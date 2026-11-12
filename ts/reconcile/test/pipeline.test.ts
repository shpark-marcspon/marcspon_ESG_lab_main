import { describe, expect, it } from "vitest";
import { applyMemory, checkMemory, headerPaths, runReconcile, syntheticLabeler, type MemoryEntry } from "../src/index.js";
import { catalog, completed, estimator, extract, harness, oracle, payloadOf, rawLabel, runOptions, syntheticDocument, units } from "./helpers.js";

const table = (rows: string[][]) => [{ id: "p1-t1", cells: [["구분", "단위", "2023", "2024"], ...rows] }];
const a = extract(syntheticDocument("source", table([["직접 온실가스(Scope 1)", "tCO2eq", "1,200", "1,350"], ["간접 온실가스(Scope 2) - 지역기반", "tCO2eq", "5,000", "5,400"]])), "a");
const b = extract(syntheticDocument("report", table([["직접 온실가스(Scope 1)", "tCO2eq", "1,200", "1,530"], ["간접 온실가스(Scope 2) - 지역기반", "tCO2eq", "5,000", "5,400"]])), "b");
const meanings = { "직접온실가스(Scope1)": { key: "ghg.scope1" }, "간접온실가스(Scope2)-지역기반": { key: "ghg.scope2.location" } };

const entry = (rowPath: string[], indicatorKey: string, patch: Partial<MemoryEntry> = {}): MemoryEntry => ({
  docKind: "source",
  rowPath,
  colPath: [],
  indicatorKey,
  unit: "tCO2eq",
  boundary: "unspecified",
  basisNote: null,
  period: { from: "header_year" },
  confirmedBy: "synthetic",
  confirmedAt: "2026-10-04",
  ...patch,
});

describe("끝까지 잇기 (모의 공급사)", () => {
  it("사실 후보 → 뜻 붙이기 → 짝 → 비교가 이어지고, 결과마다 양쪽 원문 위치와 글자, 적용한 규칙이 실린다", async () => {
    const { client, mock } = harness();
    mock.enqueue(oracle([a, b], { a: meanings, b: meanings })).enqueue(oracle([a, b], { a: meanings, b: meanings }));
    const result = await runReconcile(runOptions(a, b, client));
    expect(mock.calls).toHaveLength(2);
    expect(result.summary.pairs).toEqual({ match: 3, mismatch: 1, needs_explanation: 0 });
    const mismatch = result.findings.find((finding) => finding.outcome === "mismatch");
    expect(mismatch).toMatchObject({
      key: { indicator: "ghg.scope1", period: "2024", boundary: "unspecified" },
      a: { raw: "1,350", where: { page: 1, gridId: "p1-t1", row: 1, col: 3 }, label: { source: "ai", unit: "tCO2eq" } },
      b: { raw: "1,530", where: { page: 1, gridId: "p1-t1", row: 1, col: 3 } },
      comparison: { expA: 0, expB: 0, quantumExp: 0, baseA: "1350", baseB: "1530", rule: "none" },
    });
  });

  it("AI 가 없는 ID 를 가리키거나 답을 빠뜨리면 그 숫자는 일치가 아니라 미확인으로 남는다", async () => {
    const { client, mock } = harness();
    // 문서 A: 한 숫자에는 없는 ID 로 답하고 한 숫자는 빠뜨린다.
    mock.enqueue((request) => {
      const targets = payloadOf(request).targets;
      return completed([
        rawLabel(targets[0] as string, { period: "2023", unit: "tCO2eq" }),
        rawLabel("r8c8", { period: "2024", unit: "tCO2eq" }),
        rawLabel(targets[2] as string, { indicator_key: "ghg.scope2.location", period: "2023", unit: "tCO2eq", evidence: ["r9c0"] }),
      ]);
    });
    mock.enqueue(oracle([a, b], { b: meanings }));
    const result = await runReconcile(runOptions(a, b, client));
    expect(result.labeling.a).toMatchObject({ accepted: 1, unanswered: 3, rejectedByReason: { unknown_id: 1, unknown_evidence_id: 1 } });
    expect(result.summary.a.unverifiedByReason).toEqual({ not_answered: 2, answer_rejected: 1 });
    expect(result.summary.pairs).toEqual({ match: 1, mismatch: 0, needs_explanation: 0 });
    expect(result.summary.a.sentence).toBe("숫자 4개 중 1개 대조 (일치 1, 불일치 0, 설명 필요 0), 짝 없음 0개, 미확인 3개");
    expect(result.summary.b.byOutcome).toMatchObject({ match: 1, unpaired: 3 });
  });

  it("호출이 실패하면 그 표의 숫자는 전부 미확인이다 (빈 결과로 접지 않는다)", async () => {
    const { client, mock } = harness();
    mock.enqueue({ kind: "refusal", text: "거부", category: null, usage: { inputTokens: 1, outputTokens: 1, cacheReadTokens: 0, cacheWriteTokens: 0 } });
    mock.enqueue(oracle([a, b], { b: meanings }));
    const result = await runReconcile(runOptions(a, b, client));
    expect(result.labeling.a.llmFailures).toEqual([{ unitId: "p1-t1", kind: "refusal" }]);
    expect(result.summary.a.unverifiedByReason).toEqual({ llm_call_failed: 4 });
  });

  it("범위 밖의 숫자(문장, 신뢰도 낮은 표)도 분모에 들어가고 미확인으로 센다", async () => {
    const low = extract(
      syntheticDocument(
        "low",
        [
          { id: "p1-t1", cells: [["구분", "단위", "2023"], ["직접 온실가스(Scope 1)", "tCO2eq", "1,200"]] },
          { id: "p1-t2", status: "low_confidence", cells: [["막대", "값"], ["가", "300"]] },
        ],
        [{ id: "p1-b1", text: "임직원은 3,200명이다." }],
      ),
      "a",
    );
    const { client, mock } = harness();
    mock.enqueue(oracle([low, b], { a: meanings })).enqueue(oracle([low, b], { b: meanings }));
    const result = await runReconcile(runOptions(low, b, client));
    expect(mock.calls).toHaveLength(2);
    expect(result.summary.a).toMatchObject({ valueCandidates: 3, lowConfidenceTableNumbers: 1, unverifiedByReason: { table_low_confidence: 1, out_of_scope_sentence: 1 } });
  });

  it("모의 응답기(synthetic)는 머리글 낱말로 지표를 정한다", async () => {
    const { client, mock } = harness();
    const reply = syntheticLabeler(catalog, units, [a, b], estimator);
    mock.enqueue(reply).enqueue(reply);
    const result = await runReconcile(runOptions(a, b, client));
    expect(result.summary.pairs).toEqual({ match: 3, mismatch: 1, needs_explanation: 0 });
  });
});

describe("확정 기억", () => {
  const memory = [entry(["직접 온실가스(Scope 1)", "tCO2eq"], "ghg.scope1"), entry(["간접 온실가스(Scope 2) - 지역기반", "tCO2eq"], "ghg.scope2.location", { basisNote: "지역기반" })];

  it("머리글 경로가 맞는 셀에 뜻을 붙이고 기간은 연도 머리글에서 읽는다", () => {
    const applied = applyMemory(a.facts, "source", memory);
    expect(applied.labels).toHaveLength(4);
    expect(applied.labels[1]).toMatchObject({ factId: "p1-t1:r1c3", indicatorKey: "ghg.scope1", period: "2024", unit: "tCO2eq", source: "memory" });
    expect(applyMemory(a.facts, "report", memory).labels).toEqual([]);
    expect(headerPaths(a.facts[0] as (typeof a.facts)[number])).toEqual({ rowPath: ["직접온실가스(Scope1)", "tCO2eq"], colPath: [], years: ["2023"] });
  });

  it("열 머리글 경로가 다르면 붙이지 않는다", () => {
    const wide = extract(syntheticDocument("wide", [{ id: "p1-t1", cells: [["구분", "배출량", "집약도"], ["", "2023", "2023"], ["직접 온실가스(Scope 1)", "1,200", "2.5"]], header: { rows: [0, 1], cols: [0], basis: "rows_before_first_numeric_row" } }]), "a");
    const applied = applyMemory(wide.facts, "source", [entry(["직접 온실가스(Scope 1)"], "ghg.scope1", { colPath: ["배출량"], unit: null })]);
    expect(applied.labels.map((label) => label.factId)).toEqual(["p1-t1:r2c1"]);
  });

  it("기억으로 다 붙은 표는 AI 를 부르지 않는다", async () => {
    const { client, mock } = harness();
    mock.enqueue(oracle([a, b], { b: meanings }));
    const result = await runReconcile(runOptions(a, b, client, { memory }));
    // 문서 A 는 호출 0회, 문서 B 만 1회.
    expect(mock.calls).toHaveLength(1);
    expect(payloadOf(mock.calls[0] as (typeof mock.calls)[number]).doc).toBe("b");
    expect(result.labeling.a).toMatchObject({ fromMemory: 4, units: 0, llmCalls: 0 });
    expect(result.summary.pairs).toEqual({ match: 3, mismatch: 1, needs_explanation: 0 });
    expect(result.findings.find((finding) => finding.outcome === "mismatch")?.a?.label?.source).toBe("memory");
  });

  it("기억이 일부만 덮으면 남은 셀만 AI 에 묻는다", async () => {
    const { client, mock } = harness();
    mock.enqueue(oracle([a, b], { a: meanings })).enqueue(oracle([a, b], { b: meanings }));
    await runReconcile(runOptions(a, b, client, { memory: memory.slice(0, 1) }));
    expect(payloadOf(mock.calls[0] as (typeof mock.calls)[number]).targets).toEqual(["r2c2", "r2c3"]);
  });

  it("같은 머리글 경로에 확정이 둘이거나 목록에 없는 지표를 가리키면 쓰지 않는다", () => {
    const twice = [...memory, entry(["직접 온실가스(Scope 1)", "tCO2eq"], "ghg.scope2.market")];
    expect(applyMemory(a.facts, "source", twice).labels.map((label) => label.indicatorKey)).toEqual(["ghg.scope2.location", "ghg.scope2.location"]);
    expect(checkMemory([entry(["가"], "ghg.scope9"), entry(["나"], "ghg.scope1", { boundary: "galaxy" })], catalog)).toHaveLength(2);
  });
});
