import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { AnthropicProvider, LlmClient, MemorySink, computeCostUsd, defaultConfigPaths, type MockProvider, type ProviderRequest } from "llm";
import {
  FileChunkStore,
  dryRunChunks,
  planTranslate,
  prompts,
  runTranslate,
  syntheticTranslator,
  translateRequest,
  type TranslateOutput,
} from "../src/index.js";
import { TAGS, config, estimator, fixtureDocument, glossary, harness, itemsIn, settings, tempDir } from "./helpers.js";

const document = fixtureDocument();
const base = { document, glossary, settings, estimator, route: "mock", tags: TAGS, budgetTokens: 400, concurrency: 3 };
const plan = planTranslate(base);
const synthetic = syntheticTranslator(glossary, estimator);
const seed = (mock: MockProvider, count: number): void => {
  for (let i = 0; i < count; i++) mock.enqueue(synthetic);
};
const sourceCells = new Map(document.tables.map((table) => [table.id, table.cells]));

describe("모의 공급사 종단", () => {
  it("문서 하나를 조각내 번역하고 조립한다: 누락·중복 없음, 숫자 셀 원본 유지", async () => {
    const { client, mock } = harness();
    seed(mock, plan.chunks.length);
    const outcome = await runTranslate({ ...base, client, store: new FileChunkStore<TranslateOutput>(tempDir()) });

    expect(plan.chunks.length).toBeGreaterThan(3);
    expect(outcome.run).toMatchObject({ total: plan.chunks.length, done: plan.chunks.length, failed: 0, skipped: 0 });
    expect(mock.calls).toHaveLength(plan.chunks.length);
    expect(outcome.coverage).toMatchObject({ ok: true, notTranslated: [], unexpectedItems: {} });
    expect(outcome.result.blocks.map((block) => block.id)).toEqual(document.blocks.map((block) => block.id));

    for (const table of outcome.result.tables.filter((candidate) => candidate.status === "translated")) {
      const source = sourceCells.get(table.id)!;
      table.cells!.forEach((row, rowIndex) =>
        row.forEach((cell, colIndex) => {
          const original = source[rowIndex]![colIndex]!;
          if (/[가-힣]/.test(original)) expect(cell).toMatch(/^\[mock-en\] /);
          else expect(cell).toBe(original);
        }),
      );
    }
    // 모의 응답기는 용어집 용어를 그대로 바꿔 넣으므로 검사가 걸릴 것이 없다. 검사가 돌았다는 것만 본다.
    expect(outcome.consistency.pairsChecked).toBe(plan.chunks.reduce((sum, chunk) => sum + chunk.items.length, 0));
    expect(outcome.consistency.termOccurrences).toBeGreaterThan(0);
    expect(outcome.consistency.termMisses).toEqual([]);
  });

  it("조각마다 문서 전체를 넣지 않는다: 요청에는 그 조각의 항목만 있고, 항목은 전체에서 한 번씩만 나간다", async () => {
    const { client, mock } = harness();
    seed(mock, plan.chunks.length);
    await runTranslate({ ...base, client, store: new FileChunkStore<TranslateOutput>(tempDir()) });

    const expected = new Map(plan.chunks.map((chunk) => [chunk.items.map((item) => item.id).join(","), chunk]));
    const sent: string[] = [];
    for (const request of mock.calls) {
      const items = itemsIn(request);
      const chunk = expected.get(items.map((item) => item.id).join(","));
      expect(chunk).toBeDefined();
      expect(items).toEqual(chunk!.items);
      sent.push(...items.map((item) => item.id));
      expect(request.user).toHaveLength(1);
      expect(request.user[0]!.type).toBe("text");
    }
    // 항목 배열 앞뒤에 다른 글자가 붙지 않았는지: 요청의 어림 토큰이 고정 앞부분 + 그 조각의 원문 + 위치 한 줄을 넘지 않는다.
    const wholeDocument = plan.chunks.reduce((sum, chunk) => sum + chunk.sourceTokens, 0);
    const prefix = estimator.estimate(mock.calls[0]!.system.map((segment) => segment.text).join("\n"));
    for (const request of mock.calls) {
      const user = request.user.map((part) => (part.type === "text" ? part.text : "")).join("");
      const items = itemsIn(request);
      const chunk = expected.get(items.map((item) => item.id).join(","))!;
      expect(estimator.estimate(user)).toBeLessThanOrEqual(chunk.sourceTokens + 120);
      expect(estimator.estimate(user) + prefix).toBeLessThan(wholeDocument / 2);
      expect(user.slice(0, user.indexOf("\n[\n")).split("\n")).toHaveLength(3);
    }
    expect(new Set(sent).size).toBe(sent.length);
    expect(sent).toHaveLength(plan.chunks.reduce((sum, chunk) => sum + chunk.items.length, 0));
    // 고정 앞부분(지침, 용어집)은 모든 조각에서 같다.
    expect(new Set(mock.calls.map((request) => JSON.stringify(request.system))).size).toBe(1);
    expect(mock.calls[0]!.system[0]!.cache).toBe(true);
  });

  it("이어 하기: 끝난 조각 재호출 없음 (새 프로세스를 흉내 내 클라이언트와 저장소를 새로 만든다)", async () => {
    const dir = tempDir();
    const first = harness();
    seed(first.mock, plan.chunks.length);
    const firstRun = await runTranslate({ ...base, client: first.client, store: new FileChunkStore<TranslateOutput>(dir) });

    const second = harness();
    seed(second.mock, plan.chunks.length);
    const secondRun = await runTranslate({ ...base, client: second.client, store: new FileChunkStore<TranslateOutput>(dir) });

    expect(second.mock.calls).toHaveLength(0);
    expect(secondRun.run).toMatchObject({ done: 0, failed: 0, skipped: plan.chunks.length, workCalls: 0 });
    expect(secondRun.result).toEqual(firstRun.result);
  });

  it("조각 실패 격리: 실패한 조각만 실패로 남고, 다시 실행하면 그 조각만 부른다", async () => {
    const dir = tempDir();
    const victim = plan.chunks[2]!;
    const first = harness();
    for (let i = 0; i < plan.chunks.length; i++) {
      first.mock.enqueue((request: ProviderRequest) =>
        itemsIn(request)[0]!.id === victim.items[0]!.id ? { kind: "refusal", text: "거부", category: null, usage: { inputTokens: 1, outputTokens: 1, cacheReadTokens: 0, cacheWriteTokens: 0 } } : synthetic(request),
      );
    }
    const firstRun = await runTranslate({ ...base, client: first.client, store: new FileChunkStore<TranslateOutput>(dir) });
    expect(firstRun.run).toMatchObject({ done: plan.chunks.length - 1, failed: 1 });
    expect(firstRun.run.records[2]).toMatchObject({ chunkId: victim.id, status: "failed", error: { kind: "refusal" } });
    const untranslated = firstRun.result.blocks.filter((block) => block.status === "not_translated");
    expect(untranslated.length).toBeGreaterThan(0);
    expect(untranslated.every((block) => block.status === "not_translated" && block.chunkId === victim.id && block.reason === "chunk_failed:refusal")).toBe(true);
    expect(firstRun.result.blocks.filter((block) => block.status === "translated").length).toBeGreaterThan(0);
    expect(firstRun.coverage.notTranslated.length).toBeGreaterThan(0);

    const second = harness();
    seed(second.mock, plan.chunks.length);
    const secondRun = await runTranslate({ ...base, client: second.client, store: new FileChunkStore<TranslateOutput>(dir) });
    expect(second.mock.calls).toHaveLength(1);
    expect(itemsIn(second.mock.calls[0]!)).toEqual(victim.items);
    expect(secondRun.run).toMatchObject({ done: 1, failed: 0, skipped: plan.chunks.length - 1 });
    expect(secondRun.coverage.notTranslated).toEqual([]);
  });

  it("숫자 셀 원본 유지: 모델이 표의 숫자 셀을 바꿔 돌려줘도 결과에는 원본이 남는다", async () => {
    const { client, mock } = harness();
    const tamper = (request: ProviderRequest) => {
      const items = itemsIn(request).map((item) => ({ id: item.id, text: `en:${item.text}` }));
      const tableIds = new Set(items.filter((item) => item.id.includes(":r")).map((item) => item.id.split(":")[0]!));
      for (const tableId of tableIds) {
        sourceCells.get(tableId)!.forEach((row, rowIndex) =>
          row.forEach((cell, colIndex) => {
            if (!/[가-힣]/.test(cell)) items.push({ id: `${tableId}:r${rowIndex}c${colIndex}`, text: "999,999" });
          }),
        );
      }
      const text = JSON.stringify({ items });
      return { kind: "completed" as const, text, usage: { inputTokens: 10, outputTokens: 10, cacheReadTokens: 0, cacheWriteTokens: 0 } };
    };
    for (let i = 0; i < plan.chunks.length; i++) mock.enqueue(tamper);
    const outcome = await runTranslate({ ...base, client, store: new FileChunkStore<TranslateOutput>(tempDir()) });

    expect(outcome.run.failed).toBe(0);
    const translated = outcome.result.tables.filter((table) => table.status === "translated");
    expect(translated.length).toBe(document.tables.length - 1);
    for (const table of translated) {
      expect(JSON.stringify(table.cells)).not.toContain("999,999");
      const source = sourceCells.get(table.id)!;
      table.cells!.forEach((row, rowIndex) => row.forEach((cell, colIndex) => expect(cell).toBe(/[가-힣]/.test(source[rowIndex]![colIndex]!) ? `en:${source[rowIndex]![colIndex]}` : source[rowIndex]![colIndex])));
    }
    expect(outcome.result.tables.find((table) => table.id === "p2-t1")!.cells![1]).toEqual(["en:사업장 에너지 사용량", "GWh", "2,234", "2,567.8"]);
    // 받은 것은 기록에 남고 누락·중복 검사에 드러난다.
    expect(Object.keys(outcome.coverage.unexpectedItems).length).toBeGreaterThan(0);
    expect(outcome.run.records.some((record) => record.warnings.includes("unexpected_ids"))).toBe(true);
  });

  it("재시도 가능한 공급사 오류는 조각 단위로 다시 부르고, 재시도 불가 오류는 한 번으로 끝낸다", async () => {
    const { client, mock } = harness();
    const one = { ...base, budgetTokens: 100_000, concurrency: 1 };
    mock.enqueue({ kind: "error", status: 503, retryable: true, message: "과부하" });
    mock.enqueue(synthetic);
    const retried = await runTranslate({ ...one, client, store: new FileChunkStore<TranslateOutput>(tempDir()) });
    expect(retried.run).toMatchObject({ total: 1, done: 1, failed: 0, workCalls: 2 });
    expect(retried.run.records[0]).toMatchObject({ status: "done", attempts: 2 });

    mock.enqueue({ kind: "error", status: 400, retryable: false, message: "잘못된 요청" });
    const fatal = await runTranslate({ ...one, client, store: new FileChunkStore<TranslateOutput>(tempDir()) });
    expect(fatal.run).toMatchObject({ done: 0, failed: 1, workCalls: 1 });
    expect(fatal.run.records[0]).toMatchObject({ status: "failed", error: { kind: "provider_error", retryable: false } });
  });

  it("비용 상한에 닿으면 남은 조각은 budget_exceeded 로 실패하고 끝난 조각은 남는다", async () => {
    const { client, mock } = harness({ limits: [{ scope: { module: TAGS.module }, limitUsd: 0.0000001 }] });
    seed(mock, plan.chunks.length);
    const outcome = await runTranslate({ ...base, concurrency: 1, client, store: new FileChunkStore<TranslateOutput>(tempDir()) });
    expect(outcome.run.done).toBe(1);
    expect(outcome.run.failed).toBe(plan.chunks.length - 1);
    expect(mock.calls).toHaveLength(1);
    expect(outcome.run.records.slice(1).every((record) => record.status === "failed" && record.error.kind === "budget_exceeded")).toBe(true);
  });
});

describe("dry-run", () => {
  it("공급사를 부르지 않고 조각 수, 조각별 예상 토큰, 총 예상 비용을 낸다. 단가는 data/llm 설정에서 온다", () => {
    const anthropic = new AnthropicProvider();
    const client = new LlmClient({ config, providers: { anthropic }, prompts, log: new MemorySink() });
    const route = "light";
    const lightPlan = planTranslate({ ...base, route });
    const report = dryRunChunks(client, config, lightPlan.chunks, (chunk) => translateRequest(chunk, { route, tags: TAGS, document, glossary }), route, settings.translate.expectedOutputRatio);

    // 단가의 정본은 설정 파일이다. 코드에 같은 숫자가 없으므로 파일을 직접 읽어 견준다.
    const models = JSON.parse(readFileSync(defaultConfigPaths({ synthetic: false })[0]!, "utf8")) as { routes: Record<string, string[]>; models: Record<string, { pricing: { input: number; output: number; cacheWrite: number; cacheRead: number } }> };
    const modelKey = models.routes[route]![0]!;
    const pricing = models.models[modelKey]!.pricing;
    expect(report.model).toBe(modelKey);
    expect(report.pricingUsdPerMTok).toEqual({ input: pricing.input, output: pricing.output });

    expect(report.chunks).toBe(lightPlan.chunks.length);
    expect(report.rows.map((row) => row.chunkId)).toEqual(lightPlan.chunks.map((chunk) => chunk.id));
    let total = 0;
    report.rows.forEach((row, index) => {
      const chunk = lightPlan.chunks[index]!;
      expect(row.sourceTokens).toBe(chunk.sourceTokens);
      expect(row.inputTokens).toBeGreaterThan(chunk.sourceTokens);
      expect(row.expectedOutputTokens).toBe(Math.ceil(chunk.sourceTokens * settings.translate.expectedOutputRatio));
      const expected = computeCostUsd({ inputTokens: row.inputTokens, outputTokens: row.expectedOutputTokens, cacheReadTokens: 0, cacheWriteTokens: 0 }, pricing);
      expect(row.expectedCostUsd).toBe(expected);
      expect(row.expectedCostUsd).toBeCloseTo((row.inputTokens * pricing.input + row.expectedOutputTokens * pricing.output) / 1e6, 10);
      expect(row.costUpperBoundUsd).toBeGreaterThan(row.expectedCostUsd);
      total += expected;
    });
    expect(report.totals.expectedCostUsd).toBeCloseTo(total, 6);
    expect(report.totals.sourceTokens).toBe(lightPlan.chunks.reduce((sum, chunk) => sum + chunk.sourceTokens, 0));
    // 용어집이 짧아 캐시 최소 길이에 못 미친다는 경고가 조각마다 나온다.
    expect(Object.values(report.warnings)).toEqual([lightPlan.chunks.length]);
  });

  it("경로가 다르면 조각 id 가 달라 다른 모델의 결과를 이어 받지 않는다", () => {
    const light = planTranslate({ ...base, route: "light" }).chunks.map((chunk) => chunk.id);
    expect(light).toHaveLength(plan.chunks.length);
    expect(light.some((id, index) => id === plan.chunks[index]!.id)).toBe(false);
  });
});
