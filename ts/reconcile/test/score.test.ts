import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { BudgetGuard, type CallRecord, type LlmResult } from "llm";
import { describe, expect, it } from "vitest";
import {
  JsonlUnitStore,
  ReconcileFileError,
  accuracyRow,
  analyzeUsage,
  buildScoreReport,
  checkGold,
  checkPairs,
  confidenceRows,
  goldSchema,
  inLabelScope,
  labelAnswerSchema,
  labelRequest,
  loadGold,
  planUnits,
  renderScore,
  restraintRow,
  scoreGold,
  seedBudgetFromLog,
  type DocAnswers,
  type Extraction,
  type Finding,
  type Gold,
  type GoldCell,
  type LabelAnswer,
  type RawLabel,
  type UnitAnswer,
} from "../src/index.js";
import { TAGS, catalog, extract, rawLabel, settings, syntheticDocument, tempDir } from "./helpers.js";

// 문서, 숫자, 정답, 모델의 답은 전부 지어낸 것이다 (synthetic).

const doc = (name: string, id: string): Extraction =>
  extract(
    syntheticDocument(name, [
      {
        id: "p1-t1",
        cells: [
          ["구분", "단위", "2023", "2024"],
          ["직접 온실가스(Scope 1)", "tCO2eq", "1,200", "1,350"],
          ["총 에너지 사용량", "TJ", "45.3", "47.1"],
          ["사업장 수", "개소", "12", "14"],
        ],
      },
    ]),
    id,
  );

const gcell = (cell: string, raw: string, patch: Partial<GoldCell> = {}): GoldCell => ({ cell, raw, kind: "indicator", indicator: "ghg.scope1", period: "2023", unit: "tCO2eq", boundary: "unspecified", ...patch });

function goldFor(tables: Gold["tables"]): Gold {
  return goldSchema.parse({ id: "gold-synthetic", status: "테스트용 가상 정답 (synthetic)", author: "test", createdOn: "2026-10-04", note: "", conventions: [], catalog: { id: catalog.id, sha256: catalog.sha256 }, documents: { a: { fileName: "source.synthetic.pdf", pdfSha256: "0" }, b: { fileName: "report.synthetic.pdf", pdfSha256: "0" } }, tables });
}

const GOLD_CELLS: GoldCell[] = [
  gcell("r1c2", "1,200"),
  gcell("r1c3", "1,350", { period: "2024" }),
  gcell("r2c2", "45.3", { indicator: "energy.total", unit: "TJ" }),
  gcell("r2c3", "47.1", { indicator: "energy.total", unit: "TJ", period: "2024" }),
  gcell("r3c2", "12", { kind: "not_in_catalog", indicator: null, unit: "개소" }),
  gcell("r3c3", "14", { kind: "not_in_catalog", indicator: null, unit: "개소", period: "2024" }),
];

/** 정답대로 답하는 답안. `patch` 로 셀마다 일부러 틀리게 바꾼다. */
function answerSheet(patch: Record<string, Partial<RawLabel> | null> = {}): LabelAnswer {
  const labels = GOLD_CELLS.flatMap((cell) => {
    const change = patch[cell.cell];
    if (change === null) return [];
    return [rawLabel(cell.cell, { kind: cell.kind, indicator_key: cell.indicator, period: cell.period, unit: cell.unit, boundary: cell.boundary, ...change })];
  });
  return { labels };
}

function answersOf(extraction: Extraction, result: UnitAnswer | null): DocAnswers {
  const units = planUnits(extraction, extraction.facts.filter((fact) => inLabelScope(fact, { includeSentences: false })), { maxTargetsPerCall: 40, includeSentences: false });
  return { extraction, units, answers: new Map(result ? units.map((unit) => [unit.id, result]) : []) };
}

function scored(patch: Record<string, Partial<RawLabel> | null> = {}, result?: UnitAnswer | null) {
  const a = doc("source", "a");
  const b = doc("report", "b");
  const gold = goldFor([{ doc: "a", tableId: "p1-t1", page: 1, why: "", cells: GOLD_CELLS }]);
  const docs = { a: answersOf(a, result === undefined ? { status: "answered", answer: answerSheet(patch) } : result), b: answersOf(b, null) };
  return { gold, docs, cells: scoreGold(gold, catalog, docs) };
}

describe("채점", () => {
  it("정답대로 답하면 전부 맞고, 분모는 정답 셀 수다", () => {
    const { cells } = scored();
    expect(accuracyRow("전체", cells)).toEqual({ name: "전체", indicatorCells: 4, answered: 4, indicatorOk: 4, periodOk: 4, unitOk: 4, boundaryOk: 4, allFour: 4, allFourAccepted: 4 });
    expect(restraintRow("전체", cells)).toEqual({ name: "전체", outsideCells: 2, answered: 2, keptOutside: 2, kindExact: 2, forced: 0, forcedAccepted: 0 });
    expect(cells.flatMap((cell) => cell.errors)).toEqual([]);
  });

  it("기간이 다르면 지표가 맞아도 「넷 다 맞음」이 아니다 (연도 열을 틀리게 가리킨 답)", () => {
    const { cells } = scored({ r1c2: { period: "2024" } });
    const cell = cells.find((item) => item.cell === "r1c2");
    expect(cell).toMatchObject({ indicatorOk: true, periodOk: false, unitOk: true, boundaryOk: true, allFour: false, errors: ["period_wrong_year"] });
    expect(accuracyRow("전체", cells)).toMatchObject({ indicatorOk: 4, periodOk: 3, allFour: 3 });
  });

  it("기간을 빠뜨린 답과 요청에 없는 기간을 붙인 답을 가른다", () => {
    expect(scored({ r1c2: { period: null } }).cells.find((item) => item.cell === "r1c2")?.errors).toEqual(["period_missing"]);
    const a = doc("source", "a");
    const gold = goldFor([{ doc: "a", tableId: "p1-t1", page: 1, why: "", cells: GOLD_CELLS.map((cell) => (cell.cell === "r1c2" ? { ...cell, period: null, truth: { period: "2023" } } : cell)) }]);
    const docs = { a: answersOf(a, { status: "answered", answer: answerSheet() }), b: answersOf(doc("report", "b"), null) };
    const cell = scoreGold(gold, catalog, docs).find((item) => item.cell === "r1c2");
    expect(cell).toMatchObject({ periodOk: false, errors: ["period_unfounded"] });
    expect(buildScoreReport(gold, catalog, docs, []).gold.cellsWithTruthBeyondRequest).toBe(1);
  });

  it("지표·단위·범위가 틀린 답을 종류별로 가른다. 단위는 표기를 맞춘 뒤 견준다", () => {
    const { cells } = scored({ r1c2: { indicator_key: "ghg.scope2.location" }, r1c3: { unit: "tCO₂eq" }, r2c2: { unit: "GJ" }, r2c3: { boundary: "domestic" } });
    const errors = Object.fromEntries(cells.map((cell) => [cell.cell, cell.errors]));
    expect(errors).toMatchObject({ r1c2: ["indicator_wrong"], r1c3: [], r2c2: ["unit_wrong"], r2c3: ["boundary_wrong"] });
    expect(accuracyRow("전체", cells)).toMatchObject({ indicatorOk: 3, unitOk: 3, boundaryOk: 3, allFour: 1 });
  });

  it("목록에 없는 숫자에 지표를 붙이면 「억지로 붙임」이고, 지표를 목록 밖으로 답하면 「놓침」이다", () => {
    const { cells } = scored({ r3c2: { kind: "indicator", indicator_key: "employees.total" }, r3c3: { kind: "not_data" }, r2c2: { kind: "not_in_catalog", indicator_key: null } });
    expect(restraintRow("전체", cells)).toEqual({ name: "전체", outsideCells: 2, answered: 2, keptOutside: 1, kindExact: 0, forced: 1, forcedAccepted: 1 });
    expect(cells.find((cell) => cell.cell === "r3c2")?.errors).toEqual(["forced_indicator"]);
    expect(cells.find((cell) => cell.cell === "r3c3")?.errors).toEqual(["kind_confused"]);
    expect(cells.find((cell) => cell.cell === "r2c2")).toMatchObject({ indicatorOk: false, allFour: false, errors: ["indicator_dropped"] });
  });

  it("판단이 갈리는 자리는 정답에 적어 둔 다른 값도 맞다고 본다", () => {
    const a = doc("source", "a");
    const gold = goldFor([{ doc: "a", tableId: "p1-t1", page: 1, why: "", cells: GOLD_CELLS.map((cell) => (cell.cell === "r1c2" ? { ...cell, boundary: "domestic_overseas", accept: { boundary: ["consolidated"] } } : cell)) }]);
    const cellOf = (boundary: string) => scoreGold(gold, catalog, { a: answersOf(a, { status: "answered", answer: answerSheet({ r1c2: { boundary } }) }), b: answersOf(doc("report", "b"), null) }).find((item) => item.cell === "r1c2");
    expect(cellOf("domestic_overseas")?.boundaryOk).toBe(true);
    expect(cellOf("consolidated")?.boundaryOk).toBe(true);
    expect(cellOf("unspecified")).toMatchObject({ boundaryOk: false, allFour: false });
  });

  it("답이 없는 셀은 분모에 남고 틀린 것으로 센다: 빠뜨림, 겹친 답, 호출 실패, 안 돌림", () => {
    const missing = scored({ r1c2: null });
    expect(missing.cells.find((cell) => cell.cell === "r1c2")).toMatchObject({ status: "missing", allFour: false });
    expect(accuracyRow("전체", missing.cells)).toMatchObject({ indicatorCells: 4, answered: 3, allFour: 3 });

    const a = doc("source", "a");
    const twice = answerSheet();
    twice.labels.push(rawLabel("r1c2", { period: "2023", unit: "tCO2eq" }));
    const gold = goldFor([{ doc: "a", tableId: "p1-t1", page: 1, why: "", cells: GOLD_CELLS }]);
    const duplicate = scoreGold(gold, catalog, { a: answersOf(a, { status: "answered", answer: twice }), b: answersOf(doc("report", "b"), null) });
    expect(duplicate.find((cell) => cell.cell === "r1c2")).toMatchObject({ status: "duplicate", validation: "duplicate_answer", allFour: false });

    expect(accuracyRow("전체", scored({}, { status: "failed", kind: "truncated" }).cells)).toMatchObject({ indicatorCells: 4, answered: 0, allFour: 0 });
    expect(scored({}, { status: "failed", kind: "truncated" }).cells[0]).toMatchObject({ status: "call_failed", failureKind: "truncated" });
    expect(scored({}, null).cells.every((cell) => cell.status === "not_run")).toBe(true);
  });

  it("코드 확인에서 버려진 답은 답으로는 채점하되, 「넷 다 맞고 확인 통과」에는 들지 않는다", () => {
    // 근거로 없는 셀 ID 를 댄 답: 뜻은 맞지만 코드 확인이 버린다.
    const { cells } = scored({ r1c2: { evidence: ["r9c9"] } });
    expect(cells.find((cell) => cell.cell === "r1c2")).toMatchObject({ allFour: true, validation: "unknown_evidence_id" });
    expect(accuracyRow("전체", cells)).toMatchObject({ allFour: 4, allFourAccepted: 3 });
  });

  it("확신 등급별로 맞은 수를 낸다", () => {
    const { cells } = scored({ r1c2: { confidence: "low", period: "2024" }, r3c2: { confidence: "medium", kind: "indicator", indicator_key: "employees.total" } });
    const rows = Object.fromEntries(confidenceRows(cells).map((row) => [row.confidence, row]));
    expect(rows.high).toMatchObject({ answered: 4, correct: 4, indicatorCells: 3, indicatorAllFour: 3, outsideCells: 1, forced: 0 });
    expect(rows.medium).toMatchObject({ answered: 1, correct: 0, outsideCells: 1, forced: 1 });
    expect(rows.low).toMatchObject({ answered: 1, correct: 0, indicatorCells: 1, indicatorAllFour: 0 });
  });

  it("짝의 뜻을 정답과 견준다: 틀린 뜻으로 지어진 「일치」를 가려낸다", () => {
    const gold = goldFor([
      { doc: "a", tableId: "p1-t1", page: 1, why: "", cells: GOLD_CELLS },
      { doc: "b", tableId: "p1-t1", page: 1, why: "", cells: GOLD_CELLS.slice(0, 4) },
    ]);
    const side = (factId: string, raw: string, indicatorKey: string, period: string) => ({ factId, docId: "x", raw, value: raw, where: { page: 1, sheet: null, gridId: "p1-t1", row: 1, col: 2, ref: null, blockId: null, span: null }, tableStatus: "ok" as const, footnoteMarks: [], flags: [], stored: null, label: { factId, kind: "indicator" as const, indicatorKey, freeText: null, period, unit: null, boundary: "unspecified", basisNote: null, confidence: "high" as const, evidence: [], source: "ai" as const } });
    const finding = (outcome: Finding["outcome"], a: ReturnType<typeof side>, b: ReturnType<typeof side>): Finding => ({ outcome, reason: outcome, detail: null, key: { indicator: a.label.indicatorKey, period: a.label.period, boundary: "unspecified" }, a, b, comparison: null });
    const checks = checkPairs(gold, [
      finding("match", side("p1-t1:r1c2", "1,200", "ghg.scope1", "2023"), side("p1-t1:r1c2", "1,200", "ghg.scope1", "2023")),
      // 정답은 2024 인데 양쪽 다 2023 으로 붙었다 (연도 열 틀림). 값이 같아 일치로 나온 것.
      finding("match", side("p1-t1:r1c3", "1,350", "ghg.scope1", "2023"), side("p1-t1:r1c3", "1,350", "ghg.scope1", "2023")),
      // 정답 표 밖의 숫자와 지어진 짝.
      finding("match", side("p1-t1:r2c2", "45.3", "energy.total", "2023"), side("p9-t9:r1c1", "45.3", "energy.total", "2023")),
      finding("mismatch", side("p1-t1:r2c3", "47.1", "energy.total", "2024"), side("p1-t1:r2c3", "47.9", "energy.total", "2024")),
      // 목록에 없는 숫자(정답)에 지표가 붙어 지어진 짝.
      finding("match", side("p1-t1:r3c2", "12", "employees.total", "2023"), side("p9-t9:r1c2", "12", "employees.total", "2023")),
    ]);
    expect(checks.map((check) => [check.outcome, check.verdict])).toEqual([["match", "both_correct"], ["match", "wrong_label"], ["match", "unjudged"], ["mismatch", "both_correct"], ["match", "wrong_label"]]);
    expect(checks[1]).toMatchObject({ a: { verdict: "wrong" }, b: { verdict: "wrong" } });
    expect(checks[4]).toMatchObject({ a: { verdict: "wrong" }, b: { verdict: "no_gold" } });
  });

  it("전체 분포와 사람이 읽는 채점표: 분모가 붙고, 실패한 요청의 숫자가 빠지지 않는다", () => {
    const { gold, docs } = scored({ r1c2: null, r3c2: { kind: "indicator", indicator_key: "employees.total" } });
    const report = buildScoreReport(gold, catalog, docs, []);
    expect(report.distribution.a).toMatchObject({ units: 1, unitsAnswered: 1, targets: 6, answered: 5, missing: 1, answeredKind: { indicator: 4, not_in_catalog: 1 }, accepted: 5 });
    expect(report.distribution.b).toMatchObject({ units: 1, unitsNotRun: 1, targets: 6, inNotRunUnits: 6, answered: 0 });
    expect(report.statusCounts).toMatchObject({ answered: 5, missing: 1 });
    const md = renderScore(report);
    expect(md).toContain("| 전체 | 4 | 3 | 3/4 (75.0%) | 3/4 (75.0%) | 3/4 (75.0%) | 3/4 (75.0%) | 3/4 (75.0%) | 3/4 (75.0%) |");
    expect(md).toContain("| 전체 | 2 | 2 | 1/2 (50.0%) | 1/2 (50.0%) | 1/2 (50.0%) | 1 |");
    expect(md).toContain("forced_indicator");
    const failed = buildScoreReport(gold, catalog, { a: { ...docs.a, answers: new Map(docs.a.units.map((unit) => [unit.id, { status: "failed" as const, kind: "truncated" }])) }, b: docs.b }, []);
    expect(failed.distribution.a).toMatchObject({ unitsFailed: { truncated: 1 }, inFailedUnits: 6, answered: 0 });
  });
});

describe("정답 파일", () => {
  it("정답이 문서와 맞지 않으면 문제를 낸다: 남는 셀, 모자란 셀, 다른 글자, 목록에 없는 키", () => {
    const extractions = { a: doc("source", "a"), b: doc("report", "b") };
    const table = (cells: GoldCell[]) => goldFor([{ doc: "a", tableId: "p1-t1", page: 1, why: "", cells }]);
    expect(checkGold(table(GOLD_CELLS), catalog, extractions)).toEqual([]);
    expect(checkGold(table(GOLD_CELLS.slice(1)), catalog, extractions).join("\n")).toContain("p1-t1:r1c2 에 정답이 없다");
    expect(checkGold(table([...GOLD_CELLS, gcell("r1c1", "tCO2eq")]), catalog, extractions).join("\n")).toContain("뜻 붙이기 대상이 아닌 셀");
    expect(checkGold(table(GOLD_CELLS.map((cell) => (cell.cell === "r1c2" ? { ...cell, raw: "1,201" } : cell))), catalog, extractions).join("\n")).toContain("원문 글자가 다르다");
    expect(checkGold(table(GOLD_CELLS.map((cell) => (cell.cell === "r1c2" ? { ...cell, indicator: "ghg.none" } : cell))), catalog, extractions).join("\n")).toContain("목록에 없는 지표 키");
    expect(checkGold(table(GOLD_CELLS.map((cell) => (cell.cell === "r3c2" ? { ...cell, indicator: "ghg.scope1" } : cell))), catalog, extractions).join("\n")).toContain("kind 가 indicator 일 때만");
    expect(checkGold({ ...table(GOLD_CELLS), catalog: { id: "other", sha256: "f".repeat(64) } }, catalog, extractions).join("\n")).toContain("지표 목록이 정답을 달 때의 것과 다르다");
    expect(checkGold(goldFor([{ doc: "a", tableId: "p9-t9", page: 1, why: "", cells: [gcell("r1c2", "1")] }]), catalog, extractions).join("\n")).toContain("문서에 그 표가 없거나");
  });

  it("저장소의 KB 정답 파일은 형식이 맞고, 지표·범위 키가 지금의 지표 목록에 있고, 검토 전이라는 표시가 있다", () => {
    const gold = loadGold(join(import.meta.dirname, "..", "..", "..", "data", "reconcile", "gold", "kb-2025.gold.json"));
    expect(gold.status).toContain("전문가 검토 전");
    expect(gold.catalog).toEqual({ id: catalog.id, sha256: catalog.sha256 });
    const cells = gold.tables.flatMap((table) => table.cells);
    expect(gold.tables).toHaveLength(10);
    expect(cells).toHaveLength(312);
    const indicators = new Set(catalog.indicators.map((indicator) => indicator.key));
    const boundaries = new Set(catalog.boundaries.map((boundary) => boundary.key));
    for (const cell of cells) {
      expect(cell.kind === "indicator" ? indicators.has(cell.indicator ?? "") : cell.indicator === null, cell.cell).toBe(true);
      expect(boundaries.has(cell.boundary), cell.cell).toBe(true);
    }
    expect(() => loadGold(join(tempDir(), "none.json"))).toThrow(ReconcileFileError);
  });
});

describe("이어 하기와 호출 기록", () => {
  const usage = { inputTokens: 10, outputTokens: 10, cacheReadTokens: 0, cacheWriteTokens: 0 };
  const meta = (callId: string, cost: number) => ({ callId, promptId: "reconcile/label@1", attempts: [{ provider: "mock", model: "mock", kind: "success" as const, usage, costUsd: cost, durationMs: 1 }], totalCostUsd: cost, warnings: [] });

  it("모델이 답한 결과만 남기고, 같은 요청에만 돌려주고, 파일에서 다시 읽는다", () => {
    const a = doc("source", "a");
    const unit = planUnits(a, a.facts, { maxTargetsPerCall: 40, includeSentences: false })[0];
    if (!unit) throw new Error("요청 단위가 없다");
    const request = labelRequest(unit, catalog, { route: "mock", tags: TAGS, settings: settings.label, modelMaxOutputTokens: 4000 });
    const path = join(tempDir(), "sub", "units.jsonl");
    const store = new JsonlUnitStore<LabelAnswer>(path, labelAnswerSchema);
    store.put(unit.id, request, { kind: "timeout", message: "", meta: meta("c0", 0) });
    store.put(unit.id, request, { kind: "provider_error", status: 503, retryable: true, message: "", meta: meta("c0", 0) });
    store.put(unit.id, request, { kind: "budget_exceeded", limitUsd: 1, spentUsd: 1, scope: {}, meta: meta("c0", 0) });
    expect(store.size).toBe(0);
    const success: LlmResult<LabelAnswer> = { kind: "success", data: answerSheet(), text: "", meta: meta("c1", 0.001) };
    store.put(unit.id, request, success);
    expect(store.get(unit.id, request)).toEqual(success);
    // 지표 목록이나 경로가 달라지면 다른 요청이다.
    expect(store.get(unit.id, { ...request, route: "light-openai" })).toBeUndefined();
    expect(store.get(unit.id, { ...request, vars: { ...request.vars, catalog: "다른 목록" } })).toBeUndefined();
    expect(store.get("p9-t9", request)).toBeUndefined();
    const reopened = new JsonlUnitStore<LabelAnswer>(path, labelAnswerSchema);
    expect(reopened.get(unit.id, request)).toEqual(success);
    expect(reopened.all().map((entry) => [entry.unitId, entry.kind, entry.callId, entry.costUsd])).toEqual([[unit.id, "success", "c1", 0.001]]);
    const truncated: LlmResult<LabelAnswer> = { kind: "truncated", partialText: "", meta: meta("c2", 0.002) };
    reopened.put("p1-t2", request, truncated);
    expect(new JsonlUnitStore<LabelAnswer>(path, labelAnswerSchema).get("p1-t2", request)).toEqual(truncated);
  });

  it("저장된 답이 답의 스키마와 맞지 않으면 읽지 않고 오류를 낸다", () => {
    const dir = tempDir();
    const path = join(dir, "units.jsonl");
    writeFileSync(path, `${JSON.stringify({ unitId: "p1-t1", requestKey: "k", kind: "success", callId: "c", costUsd: 0, result: { kind: "success", data: { labels: [{ target: "r1c2", value: "1200" }] }, text: "", meta: meta("c", 0) } })}\n`);
    expect(() => new JsonlUnitStore<LabelAnswer>(path, labelAnswerSchema)).toThrow(/답의 스키마와 맞지 않는다/);
    writeFileSync(path, "{깨진 줄\n");
    expect(() => new JsonlUnitStore<LabelAnswer>(path, labelAnswerSchema)).toThrow(ReconcileFileError);
  });

  it("앞선 호출 기록의 비용을 상한 계산에 채운다. 읽을 수 없는 기록이면 이어 하지 않는다", () => {
    const dir = tempDir();
    mkdirSync(dir, { recursive: true });
    const path = join(dir, "calls.jsonl");
    writeFileSync(path, [{ costUsd: 0.3 }, { costUsd: null }, { costUsd: 0.25 }].map((line) => JSON.stringify(line)).join("\n"));
    const budget = new BudgetGuard([{ scope: { module: "reconcile" }, limitUsd: 0.5 }]);
    expect(seedBudgetFromLog(path, budget, TAGS)).toEqual({ lines: 3, spentUsd: 0.55 });
    expect(budget.check(TAGS)).toMatchObject({ allowed: false, spentUsd: 0.55 });
    expect(seedBudgetFromLog(join(dir, "none.jsonl"), budget, TAGS)).toEqual({ lines: 0, spentUsd: 0 });
    writeFileSync(path, "{깨진 줄\n");
    expect(() => seedBudgetFromLog(path, budget, TAGS)).toThrow(/비용을 모르는 채 이어 하지 않는다/);
  });

  it("호출 기록에서 토큰·비용·모델을 다시 센다 (가상 기록)", () => {
    const record = (callId: string, attempt: number, patch: Partial<CallRecord> = {}): CallRecord => ({ ts: "", callId, attempt, provider: "openai", model: "gpt-6-luna", apiModelId: "gpt-6-luna", responseModelId: "gpt-6-luna", promptId: "reconcile/label@1", promptVersion: 1, templateHash: "", requestHash: "", inputTokens: 100, outputTokens: 2000, cacheReadTokens: 0, cacheWriteTokens: 900, thinkingTokens: 500, costUsd: 0.001, durationMs: 1000, resultKind: "success", tenant: "measure", module: "reconcile", warnings: [], ...patch });
    const pricing = { input: 0.1, output: 0.5, cacheWrite: 0.125, cacheRead: 0.01 };
    const report = analyzeUsage(
      [record("c1", 0), record("c2", 0, { resultKind: "schema_mismatch" }), record("c2", 1, { outputTokens: 1000, thinkingTokens: 200, durationMs: 3000 }), record("c3", 0, { inputTokens: null, outputTokens: null, costUsd: null, resultKind: "timeout", responseModelId: null }), record("c4", 0, { resultKind: "truncated", outputTokens: 4000, thinkingTokens: 4000 })],
      [{ unitId: "u1", callId: "c1", targets: 20 }, { unitId: "u2", callId: "c2", targets: 10 }, { unitId: "u4", callId: "c4", targets: 40 }],
      pricing,
    );
    expect(report).toMatchObject({ lines: 5, billedLines: 4, retryLines: 1, byApiModel: { "gpt-6-luna": 5 }, byResponseModel: { "gpt-6-luna": 4, "(없음)": 1 }, byResultKind: { success: 2, schema_mismatch: 1, timeout: 1, truncated: 1 }, costUsd: 0.004 });
    expect(report.tokens).toEqual({ input: 4000, uncachedInput: 400, cacheRead: 0, cacheWrite: 3600, output: 9000, thinking: 5200 });
    // 100 × 0.10 + 900 × 0.125 + 출력 × 0.50 (USD/MTok)
    expect(report.recomputedCostUsd).toBeCloseTo((400 * 0.1 + 3600 * 0.125 + 9000 * 0.5) / 1e6, 8);
    // 답을 받은 요청 둘(u1, u2)만: 출력 3000 / 대상 30, 추론 700 / 30. 잘린 요청은 분모에 없다.
    expect(report.perTarget).toMatchObject({ units: 2, targets: 30, outputTokensPerTarget: 100, perUnit: { min: 100, max: 100 } });
    expect(report.perTarget?.thinkingTokensPerTarget).toBeCloseTo(700 / 30, 8);
    expect(report.units.find((unit) => unit.unitId === "u2")).toMatchObject({ attempts: 2, costUsd: 0.002, finalKind: "success" });
  });
});
