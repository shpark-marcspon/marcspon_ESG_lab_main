import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { FilePromptStore, LlmClient, MemorySink, defaultConfigPaths, loadConfig } from "llm";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { main, promptRoot, resolveFromWorkspace } from "../src/cli.js";
import {
  FileMappingMemory,
  HeuristicMockProvider,
  accept,
  finalize,
  groupKey,
  loadAdjustments,
  loadExampleSet,
  loadFactorStore,
  evaluate,
  propose,
  runPipeline,
  toMemoryEntry,
} from "../src/index.js";
import { ADJUSTMENTS_FILE } from "../src/examples.js";
import { TAGS } from "./helpers.js";

const DIR = resolveFromWorkspace("../data/scope3-examples");

async function cli(args: string[]): Promise<{ code: number; lines: string[] }> {
  const lines: string[] = [];
  const code = await main(args, (line) => lines.push(line));
  return { code, lines };
}

// 종단 테스트는 네트워크를 쓰지 않는다. 쓰면 바로 실패하게 fetch 를 막아 둔다.
beforeEach(() => {
  vi.stubGlobal("fetch", () => {
    throw new Error("테스트에서 네트워크 호출이 일어났다");
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("모의 공급사 종단", () => {
  it("예제 세트 전체를 기억 → 분류 → 후보 → 추천 → 계산으로 돌리고 지표를 낸다", async () => {
    const examples = loadExampleSet(DIR);
    const store = loadFactorStore();
    const mock = new HeuristicMockProvider();
    const log = new MemorySink();
    const client = new LlmClient({ config: loadConfig(defaultConfigPaths({ synthetic: true })), providers: { mock }, prompts: new FilePromptStore(promptRoot()), log });
    const memory = FileMappingMemory.open(examples.memoryPath as string);
    const result = await runPipeline(
      examples.rows.map((example) => example.row),
      { client, store, memory, route: "mock", tags: TAGS, adjustments: loadAdjustments(join(DIR, ADJUSTMENTS_FILE)) },
    );
    const report = evaluate(examples.rows, result, store);

    expect(result.rows).toHaveLength(examples.rows.length);
    // 모든 행이 판단 상태와 계산 상태를 갖는다. 빈 결과는 없다.
    expect(Object.values(report.decisionStatus).reduce((sum, count) => sum + count, 0)).toBe(examples.rows.length);
    expect(Object.values(report.calcStatus).reduce((sum, count) => sum + count, 0)).toBe(examples.rows.length);
    // 기억 적중 묶음은 LLM 을 부르지 않았다: 호출 수 ≤ (기억에 없는 묶음 수) × 3.
    expect(report.memory.hitGroups).toBe(memory.size);
    expect(mock.calls.length).toBe(report.llm.attempts);
    expect(report.llm.calls).toBeLessThanOrEqual((report.counts.groups - report.memory.hitGroups) * 3);
    expect(report.llm.calls).toBeGreaterThanOrEqual(report.counts.groups - report.memory.hitGroups);
    expect(log.records.length).toBe(report.llm.attempts);
    // 기억 적중 행은 정답 id 를 그대로 낸다.
    for (const entry of result.rows.filter((row) => row.decision.source === "memory")) {
      if (entry.decision.status !== "recommended") throw new Error(`기억 적중 행이 추천이 아니다: ${entry.decision.status}`);
      expect(entry.decision.picks.map((pick) => pick.factorId)).toEqual([memory.lookup(entry.groupKey)?.factorId]);
      expect(examples.rows.find((example) => example.row.rowId === entry.row.rowId)?.gold.factorIds).toContain(entry.decision.picks[0]?.factorId);
    }
    // 추천된 id 는 전부 표의 후보 계수다.
    for (const entry of result.rows) {
      if (entry.decision.status === "recommended") for (const pick of entry.decision.picks) expect(store.get(pick.factorId)).toBeDefined();
    }
    // 거리 없는 운송 행과 가상 보정 계수로 계산한 행이 상태로 구분된다.
    expect(report.calcStatus.needs_distance).toBeGreaterThan(0);
    const spendComputed = result.rows.filter((row) => row.calc.status === "computed" && row.calc.adjustment !== null);
    expect(spendComputed.length).toBeGreaterThan(0);
    for (const entry of spendComputed) expect(entry.calc).toMatchObject({ synthetic: true });
  });

  it("검토에서 확정한 매핑을 기억에 넣으면 다음 실행에서 그 묶음은 LLM 을 부르지 않는다", async () => {
    const store = loadFactorStore();
    const mock = new HeuristicMockProvider();
    const client = new LlmClient({ config: loadConfig(defaultConfigPaths({ synthetic: true })), providers: { mock }, prompts: new FilePromptStore(promptRoot()), log: new MemorySink() });
    const memory = FileMappingMemory.open(join(mkdtempSync(join(tmpdir(), "scope3-e2e-")), "memory.synthetic.json"), { createIfMissing: true, synthetic: true });
    const rows = [loadExampleSet(DIR).rows.find((example) => example.row.itemName === "에폭시수지")!.row];
    const options = { client, store, memory, route: "mock", tags: TAGS };

    const first = await runPipeline(rows, options);
    const decision = first.rows[0]!.decision;
    if (decision.status !== "recommended") throw new Error(`추천이 나와야 한다: ${decision.status}`);
    expect(decision.source).toBe("llm");
    const callsAfterFirst = mock.calls.length;
    expect(callsAfterFirst).toBe(2);

    const who = { by: "reviewer-synthetic", at: "2026-10-01T11:00:00+09:00" };
    const item = finalize(accept(propose(rows[0]!.rowId, groupKey(rows[0]!), { category: decision.category, picks: decision.picks, proposedAt: who.at }), who), who);
    memory.confirm(toMemoryEntry(item, rows[0]!.itemName)!);

    const second = await runPipeline(rows, options);
    expect(mock.calls.length).toBe(callsAfterFirst);
    expect(second.rows[0]!.decision).toMatchObject({ status: "recommended", source: "memory", picks: [{ factorId: decision.picks[0]!.factorId }] });
  });
});

describe("CLI", () => {
  it("eval --provider mock 은 지표를 내고 0 으로 끝난다", async () => {
    const { code, lines } = await cli(["eval", "../data/scope3-examples", "--provider", "mock", "--json"]);
    expect(code).toBe(0);
    const output = JSON.parse(lines.join("\n")) as { syntheticProvider: boolean; report: { counts: { rows: number }; top3: Record<string, unknown>; llm: { calls: number } } };
    expect(output.syntheticProvider).toBe(true);
    expect(output.report.counts.rows).toBeGreaterThanOrEqual(100);
    expect(Object.keys(output.report.top3)).toEqual(["factor_id", "sector"]);
    expect(output.report.llm.calls).toBeGreaterThan(0);
  });

  it("eval --dry-run 은 공급사를 부르지 않고 예상 호출 수와 비용 상한을 낸다", async () => {
    const { code, lines } = await cli(["eval", "../data/scope3-examples", "--dry-run", "--json"]);
    expect(code).toBe(0);
    const { plan } = JSON.parse(lines.join("\n")) as { plan: { dryRun: boolean; provider: string; minimum: { calls: number; costUpperBoundUsd: number }; maximum: { calls: number } } };
    expect(plan.dryRun).toBe(true);
    expect(plan.provider).toBe("anthropic");
    expect(plan.minimum.calls).toBeGreaterThan(0);
    expect(plan.maximum.calls).toBeGreaterThanOrEqual(plan.minimum.calls);
    expect(plan.minimum.costUpperBoundUsd).toBeGreaterThan(0);
  });

  it("라이선스가 확정되지 않은 계수를 빼면 카테고리 1 활동 기반 후보가 271 → 73 으로 줄어든다", async () => {
    const { code, lines } = await cli(["eval", "../data/scope3-examples", "--dry-run", "--json", "--exclude-license", "disputed,unverified"]);
    expect(code).toBe(0);
    const output = JSON.parse(lines.join("\n")) as { coverage: { activityCandidates: { category1: number } }; coverageWithoutLicenseFilter: { activityCandidates: { category1: number } } };
    expect(output.coverageWithoutLicenseFilter.activityCandidates.category1).toBe(271);
    expect(output.coverage.activityCandidates.category1).toBe(73);
  });

  it("실제 공급사 호출은 거부한다", async () => {
    expect((await cli(["eval", "../data/scope3-examples", "--provider", "anthropic"])).code).toBe(2);
    expect((await cli(["eval", "../data/scope3-examples"])).code).toBe(2);
    expect((await cli(["eval", "../data/scope3-examples", "--provider", "mock", "--route", "light"])).code).toBe(2);
    expect((await cli(["eval", "../data/scope3-examples", "--dry-run", "--route", "nowhere"])).code).toBe(2);
    expect((await cli(["eval", "../data/scope3-examples", "--provider", "mock", "--criterion", "exact"])).code).toBe(2);
    expect((await cli(["eval", "../data/scope3-examples", "--dry-run", "--exclude-license", "none"])).code).toBe(2);
  });

  it("예제 폴더가 없거나 깨졌으면 빈 평가가 아니라 예외다", async () => {
    await expect(cli(["eval", "/nonexistent-synthetic", "--provider", "mock"])).rejects.toThrow(/예제 파일이 없다/);
    const dir = mkdtempSync(join(tmpdir(), "scope3-bad-"));
    writeFileSync(join(dir, "rows.synthetic.jsonl"), '{"row_id":"x","synthetic":false}\n');
    await expect(cli(["eval", dir, "--provider", "mock"])).rejects.toThrow(/1행/);
  });
});
