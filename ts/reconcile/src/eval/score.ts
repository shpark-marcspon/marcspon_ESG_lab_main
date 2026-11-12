import type { Extraction, Fact } from "../domain/facts.js";
import { UNSPECIFIED_BOUNDARY, validateAnswers, type Catalog, type LabelAnswer, type LabelUnit, type RawLabel, type RejectReason } from "../domain/label.js";
import type { Finding, FactView } from "../domain/match.js";
import { normalizeUnitText } from "../domain/units.js";
import { goldFactId, type Gold, type GoldCell } from "./gold.js";

// 채점. 모델의 답(코드 확인을 지나기 전의 것)을 정답과 셀 단위로 견준다. 모델을 부르지 않는다.
// 모든 수치는 분모와 같이 낸다. 답이 없는 셀은 분모에서 빠지지 않고 「틀림」 쪽에 선다.

/** 요청 단위 하나의 결과. */
export type UnitAnswer = { status: "answered"; answer: LabelAnswer } | { status: "failed"; kind: string } | { status: "not_run" };

export interface DocAnswers {
  extraction: Extraction;
  units: LabelUnit[];
  /** 요청 단위 ID → 결과. 없는 단위는 `not_run` 으로 본다. */
  answers: Map<string, UnitAnswer>;
}

export type CellStatus = "answered" | "missing" | "duplicate" | "call_failed" | "not_run";

export type ErrorClass =
  | "indicator_wrong" // 지표인데 다른 지표를 붙였다
  | "indicator_dropped" // 지표인데 목록에 없음·데이터 아님으로 답했다
  | "forced_indicator" // 목록에 없는 숫자(또는 데이터가 아닌 숫자)에 지표를 붙였다
  | "kind_confused" // 목록에 없음 ↔ 데이터 아님 (어느 쪽이든 대조에 쓰이지 않는다)
  | "period_wrong_year" // 다른 연도를 붙였다
  | "period_missing" // 연도가 원문에 있는데 붙이지 않았다
  | "period_unfounded" // 요청에 연도가 없는데 붙였다
  | "unit_wrong"
  | "boundary_wrong";

export interface CellScore {
  doc: "a" | "b";
  tableId: string;
  cell: string;
  factId: string;
  raw: string;
  gold: GoldCell;
  status: CellStatus;
  /** 요청 단위가 실패했을 때 그 종류. */
  failureKind: string | null;
  answer: RawLabel | null;
  /** 코드 확인(`validateAnswers`)의 결과. 답이 없으면 null. */
  validation: "accepted" | RejectReason | null;
  kindOk: boolean;
  indicatorOk: boolean;
  periodOk: boolean;
  unitOk: boolean;
  boundaryOk: boolean;
  /** 지표·기간·단위·범위가 다 맞는가. 정답이 지표인 셀에서만 뜻이 있다. */
  allFour: boolean;
  errors: ErrorClass[];
}

const same = (a: string | null, b: string | null): boolean => (a ?? null) === (b ?? null);

export function indicatorMatches(answer: RawLabel, gold: GoldCell): boolean {
  if (gold.kind !== "indicator") return answer.kind === gold.kind;
  return answer.kind === "indicator" && (answer.indicator_key === gold.indicator || (gold.accept?.indicator ?? []).includes(answer.indicator_key ?? ""));
}

export function periodMatches(answer: RawLabel, gold: GoldCell): boolean {
  return same(answer.period, gold.period) || (gold.accept?.period ?? []).some((period) => same(answer.period, period));
}

export function unitMatches(answer: RawLabel, gold: GoldCell): boolean {
  return normalizeUnitText(answer.unit ?? "") === normalizeUnitText(gold.unit ?? "");
}

export function boundaryMatches(answer: RawLabel, gold: GoldCell): boolean {
  const boundary = answer.boundary ?? UNSPECIFIED_BOUNDARY;
  return boundary === gold.boundary || (gold.accept?.boundary ?? []).includes(boundary);
}

function errorsOf(answer: RawLabel, gold: GoldCell): ErrorClass[] {
  const errors: ErrorClass[] = [];
  if (gold.kind === "indicator") {
    if (answer.kind !== "indicator") errors.push("indicator_dropped");
    else if (!indicatorMatches(answer, gold)) errors.push("indicator_wrong");
  } else if (answer.kind === "indicator") errors.push("forced_indicator");
  else if (answer.kind !== gold.kind) errors.push("kind_confused");
  if (!periodMatches(answer, gold)) errors.push(gold.period === null ? "period_unfounded" : answer.period === null ? "period_missing" : "period_wrong_year");
  if (!unitMatches(answer, gold)) errors.push("unit_wrong");
  if (!boundaryMatches(answer, gold)) errors.push("boundary_wrong");
  return errors;
}

/** 정답 셀 하나를 채점한다. 답이 없으면(빠뜨림, 겹침, 호출 실패, 안 돌림) 전부 틀린 것으로 둔다. */
export function scoreCell(base: Pick<CellScore, "doc" | "tableId" | "cell" | "factId" | "raw" | "gold">, found: { status: CellStatus; failureKind: string | null; answer: RawLabel | null; validation: CellScore["validation"] }): CellScore {
  const { gold } = base;
  const { answer } = found;
  if (!answer) return { ...base, ...found, kindOk: false, indicatorOk: false, periodOk: false, unitOk: false, boundaryOk: false, allFour: false, errors: [] };
  const indicatorOk = indicatorMatches(answer, gold);
  const periodOk = periodMatches(answer, gold);
  const unitOk = unitMatches(answer, gold);
  const boundaryOk = boundaryMatches(answer, gold);
  return { ...base, ...found, kindOk: answer.kind === gold.kind, indicatorOk, periodOk, unitOk, boundaryOk, allFour: indicatorOk && periodOk && unitOk && boundaryOk, errors: errorsOf(answer, gold) };
}

interface UnitView {
  unit: LabelUnit;
  result: UnitAnswer;
  /** 사실 후보 ID → 그 셀을 가리킨 답들. */
  byFact: Map<string, RawLabel[]>;
  validation: Map<string, "accepted" | RejectReason>;
}

function viewUnits(doc: DocAnswers, catalog: Catalog): { byFact: Map<string, UnitView>; views: UnitView[] } {
  const facts = new Map(doc.extraction.facts.map((fact): [string, Fact] => [fact.id, fact]));
  const byFact = new Map<string, UnitView>();
  const views = doc.units.map((unit) => {
    const result = doc.answers.get(unit.id) ?? { status: "not_run" as const };
    const view: UnitView = { unit, result, byFact: new Map(), validation: new Map() };
    if (result.status === "answered") {
      for (const label of result.answer.labels) {
        // 표 요청만 채점한다 (v1 의 뜻 붙이기 대상). 문장 속 숫자는 가리키는 방식이 달라 여기서 다루지 않는다.
        const factId = unit.kind === "table" ? `${unit.gridId}:${label.target}` : null;
        if (factId) view.byFact.set(factId, [...(view.byFact.get(factId) ?? []), label]);
      }
      const checked = validateAnswers(unit, result.answer, { catalog, facts });
      for (const label of checked.accepted) view.validation.set(label.factId, "accepted");
      for (const rejected of checked.rejected) if (rejected.factId && !view.validation.has(rejected.factId)) view.validation.set(rejected.factId, rejected.reason);
    }
    for (const id of unit.targetFactIds) byFact.set(id, view);
    return view;
  });
  return { byFact, views };
}

export function scoreGold(gold: Gold, catalog: Catalog, docs: { a: DocAnswers; b: DocAnswers }): CellScore[] {
  const viewed = { a: viewUnits(docs.a, catalog), b: viewUnits(docs.b, catalog) };
  return gold.tables.flatMap((table) =>
    table.cells.map((cell): CellScore => {
      const factId = goldFactId(table, cell);
      const base = { doc: table.doc, tableId: table.tableId, cell: cell.cell, factId, raw: cell.raw, gold: cell };
      const view = viewed[table.doc].byFact.get(factId);
      if (!view || view.result.status === "not_run") return scoreCell(base, { status: "not_run", failureKind: null, answer: null, validation: null });
      if (view.result.status === "failed") return scoreCell(base, { status: "call_failed", failureKind: view.result.kind, answer: null, validation: null });
      const answers = view.byFact.get(factId) ?? [];
      if (answers.length === 0) return scoreCell(base, { status: "missing", failureKind: null, answer: null, validation: null });
      if (answers.length > 1) return scoreCell(base, { status: "duplicate", failureKind: null, answer: null, validation: "duplicate_answer" });
      return scoreCell(base, { status: "answered", failureKind: null, answer: answers[0] as RawLabel, validation: view.validation.get(factId) ?? null });
    }),
  );
}

// ── 모아 세기 ───────────────────────────────────────────────────────────────

export interface AccuracyRow {
  name: string;
  /** 정답이 지표인 셀 (분모). */
  indicatorCells: number;
  answered: number;
  indicatorOk: number;
  periodOk: number;
  unitOk: number;
  boundaryOk: number;
  allFour: number;
  /** 넷 다 맞고 코드 확인도 통과한 것 (짝짓기에 실제로 쓰이는 뜻이 맞는 것). */
  allFourAccepted: number;
}

export interface RestraintRow {
  name: string;
  /** 정답이 「목록에 없음」 또는 「데이터 아님」인 셀 (분모). */
  outsideCells: number;
  answered: number;
  keptOutside: number;
  /** 그중 종류까지 맞은 것. */
  kindExact: number;
  forced: number;
  /** 억지로 붙였는데 코드 확인도 통과해 짝짓기까지 간 것. */
  forcedAccepted: number;
}

const count = <T>(items: T[], test: (item: T) => boolean): number => items.filter(test).length;

export function accuracyRow(name: string, cells: CellScore[]): AccuracyRow {
  const rows = cells.filter((cell) => cell.gold.kind === "indicator");
  return {
    name,
    indicatorCells: rows.length,
    answered: count(rows, (cell) => cell.status === "answered"),
    indicatorOk: count(rows, (cell) => cell.indicatorOk),
    periodOk: count(rows, (cell) => cell.periodOk),
    unitOk: count(rows, (cell) => cell.unitOk),
    boundaryOk: count(rows, (cell) => cell.boundaryOk),
    allFour: count(rows, (cell) => cell.allFour),
    allFourAccepted: count(rows, (cell) => cell.allFour && cell.validation === "accepted"),
  };
}

export function restraintRow(name: string, cells: CellScore[]): RestraintRow {
  const rows = cells.filter((cell) => cell.gold.kind !== "indicator");
  return {
    name,
    outsideCells: rows.length,
    answered: count(rows, (cell) => cell.status === "answered"),
    keptOutside: count(rows, (cell) => cell.answer !== null && cell.answer.kind !== "indicator"),
    kindExact: count(rows, (cell) => cell.kindOk),
    forced: count(rows, (cell) => cell.answer?.kind === "indicator"),
    forcedAccepted: count(rows, (cell) => cell.answer?.kind === "indicator" && cell.validation === "accepted"),
  };
}

const tableName = (cell: CellScore): string => `${cell.doc}:${cell.tableId}`;

function byTable(cells: CellScore[]): Map<string, CellScore[]> {
  const groups = new Map<string, CellScore[]>();
  for (const cell of cells) groups.set(tableName(cell), [...(groups.get(tableName(cell)) ?? []), cell]);
  return groups;
}

export interface ConfidenceRow {
  confidence: "high" | "medium" | "low";
  answered: number;
  /** 종류가 맞고, 정답이 지표면 넷 다 맞은 것. */
  correct: number;
  indicatorCells: number;
  indicatorAllFour: number;
  outsideCells: number;
  forced: number;
}

export function confidenceRows(cells: CellScore[]): ConfidenceRow[] {
  return (["high", "medium", "low"] as const).map((confidence) => {
    const rows = cells.filter((cell) => cell.answer?.confidence === confidence);
    const indicator = rows.filter((cell) => cell.gold.kind === "indicator");
    const outside = rows.filter((cell) => cell.gold.kind !== "indicator");
    return {
      confidence,
      answered: rows.length,
      correct: count(rows, (cell) => (cell.gold.kind === "indicator" ? cell.allFour : cell.answer?.kind !== "indicator")),
      indicatorCells: indicator.length,
      indicatorAllFour: count(indicator, (cell) => cell.allFour),
      outsideCells: outside.length,
      forced: count(outside, (cell) => cell.answer?.kind === "indicator"),
    };
  });
}

// ── 짝이 맞는가 (틀린 뜻이 「일치」로 새는가) ─────────────────────────────────

export type SideVerdict = "correct" | "wrong" | "no_gold";

export interface PairCheck {
  outcome: Finding["outcome"];
  reason: string;
  key: Finding["key"];
  a: { factId: string; raw: string; page: number | null; verdict: SideVerdict; gold: GoldCell | null };
  b: { factId: string; raw: string; page: number | null; verdict: SideVerdict; gold: GoldCell | null };
  /** `both_correct`: 양쪽 뜻이 정답과 같다. `wrong_label`: 한쪽이라도 정답과 다르다. `unjudged`: 정답이 없는 쪽이 있고 틀린 쪽은 없다. */
  verdict: "both_correct" | "wrong_label" | "unjudged";
}

function sideVerdict(view: FactView, gold: GoldCell | undefined): SideVerdict {
  if (!gold) return "no_gold";
  const label = view.label;
  if (!label) return "wrong";
  const asRaw = { kind: label.kind, indicator_key: label.indicatorKey, period: label.period, boundary: label.boundary } as RawLabel;
  return indicatorMatches(asRaw, gold) && periodMatches(asRaw, gold) && boundaryMatches(asRaw, gold) ? "correct" : "wrong";
}

/** 짝으로 견준 결과(일치·불일치·설명 필요)마다, 그 짝을 만든 뜻(지표·기간·범위)이 정답과 같은지 본다. */
export function checkPairs(gold: Gold, findings: Finding[]): PairCheck[] {
  const golds = { a: new Map<string, GoldCell>(), b: new Map<string, GoldCell>() };
  for (const table of gold.tables) for (const cell of table.cells) golds[table.doc].set(goldFactId(table, cell), cell);
  const checks: PairCheck[] = [];
  for (const finding of findings) {
    if (!finding.a || !finding.b || !["match", "mismatch", "needs_explanation"].includes(finding.outcome)) continue;
    const side = (name: "a" | "b", view: FactView) => {
      const cell = golds[name].get(view.factId);
      return { factId: view.factId, raw: view.raw, page: view.where.page, verdict: sideVerdict(view, cell), gold: cell ?? null };
    };
    const a = side("a", finding.a);
    const b = side("b", finding.b);
    const verdict = a.verdict === "wrong" || b.verdict === "wrong" ? "wrong_label" : a.verdict === "correct" && b.verdict === "correct" ? "both_correct" : "unjudged";
    checks.push({ outcome: finding.outcome, reason: finding.reason, key: finding.key, a, b, verdict });
  }
  return checks;
}

// ── 전체 분포 (정답 표 밖 포함) ──────────────────────────────────────────────

export interface Distribution {
  docId: string;
  units: number;
  unitsAnswered: number;
  unitsFailed: Record<string, number>;
  unitsNotRun: number;
  /** 뜻 붙이기 대상 숫자 (분모). */
  targets: number;
  /** 답이 하나 온 숫자. */
  answered: number;
  missing: number;
  duplicate: number;
  inFailedUnits: number;
  inNotRunUnits: number;
  /** 모델이 답한 종류 (답이 하나 온 숫자 기준). */
  answeredKind: Record<string, number>;
  confidence: Record<string, number>;
  accepted: number;
  rejectedByReason: Record<string, number>;
  /** 대상이 아닌 셀·없는 ID 를 가리킨 답 (숫자에 묶이지 않는 버려진 답). */
  strayAnswers: Record<string, number>;
  indicatorKeys: Record<string, number>;
}

const bump = (bag: Record<string, number>, key: string, by = 1): void => {
  bag[key] = (bag[key] ?? 0) + by;
};

export function distribution(doc: DocAnswers, catalog: Catalog): Distribution {
  const { views } = viewUnits(doc, catalog);
  const facts = new Map(doc.extraction.facts.map((fact): [string, Fact] => [fact.id, fact]));
  const out: Distribution = { docId: doc.extraction.docId, units: views.length, unitsAnswered: 0, unitsFailed: {}, unitsNotRun: 0, targets: 0, answered: 0, missing: 0, duplicate: 0, inFailedUnits: 0, inNotRunUnits: 0, answeredKind: {}, confidence: {}, accepted: 0, rejectedByReason: {}, strayAnswers: {}, indicatorKeys: {} };
  for (const view of views) {
    const targets = view.unit.targetFactIds;
    out.targets += targets.length;
    if (view.result.status === "not_run") {
      out.unitsNotRun += 1;
      out.inNotRunUnits += targets.length;
      continue;
    }
    if (view.result.status === "failed") {
      bump(out.unitsFailed, view.result.kind);
      out.inFailedUnits += targets.length;
      continue;
    }
    out.unitsAnswered += 1;
    for (const id of targets) {
      const answers = view.byFact.get(id) ?? [];
      if (answers.length === 0) out.missing += 1;
      else if (answers.length > 1) out.duplicate += 1;
      else {
        const answer = answers[0] as RawLabel;
        out.answered += 1;
        bump(out.answeredKind, answer.kind);
        bump(out.confidence, answer.confidence);
        if (answer.kind === "indicator") bump(out.indicatorKeys, answer.indicator_key ?? "(null)");
      }
      const validation = view.validation.get(id);
      if (validation === "accepted") out.accepted += 1;
      else if (validation) bump(out.rejectedByReason, validation);
    }
    const checked = validateAnswers(view.unit, view.result.answer, { catalog, facts });
    for (const rejected of checked.rejected) if (!rejected.factId) bump(out.strayAnswers, rejected.reason);
  }
  return out;
}

// ── 보고 ────────────────────────────────────────────────────────────────────

export interface ScoreReport {
  gold: { id: string; status: string; tables: number; cells: number; indicatorCells: number; outsideCells: number; cellsWithTruthBeyondRequest: number };
  accuracy: { total: AccuracyRow; tables: AccuracyRow[] };
  restraint: { total: RestraintRow; tables: RestraintRow[] };
  statusCounts: Record<CellStatus, number>;
  validationCounts: Record<string, number>;
  errorCounts: Record<ErrorClass, number>;
  confidence: ConfidenceRow[];
  pairs: { byOutcome: Record<string, Record<PairCheck["verdict"], number>>; checks: PairCheck[] };
  distribution: { a: Distribution; b: Distribution };
  cells: CellScore[];
}

export function buildScoreReport(gold: Gold, catalog: Catalog, docs: { a: DocAnswers; b: DocAnswers }, findings: Finding[]): ScoreReport {
  const cells = scoreGold(gold, catalog, docs);
  const statusCounts = { answered: 0, missing: 0, duplicate: 0, call_failed: 0, not_run: 0 } as Record<CellStatus, number>;
  const validationCounts: Record<string, number> = {};
  const errorCounts = { indicator_wrong: 0, indicator_dropped: 0, forced_indicator: 0, kind_confused: 0, period_wrong_year: 0, period_missing: 0, period_unfounded: 0, unit_wrong: 0, boundary_wrong: 0 } as Record<ErrorClass, number>;
  for (const cell of cells) {
    statusCounts[cell.status] += 1;
    if (cell.validation) bump(validationCounts, cell.validation);
    for (const error of cell.errors) errorCounts[error] += 1;
  }
  const checks = checkPairs(gold, findings);
  const byOutcome: Record<string, Record<PairCheck["verdict"], number>> = {};
  for (const check of checks) {
    byOutcome[check.outcome] ??= { both_correct: 0, wrong_label: 0, unjudged: 0 };
    (byOutcome[check.outcome] as Record<PairCheck["verdict"], number>)[check.verdict] += 1;
  }
  const groups = [...byTable(cells).entries()];
  return {
    gold: {
      id: gold.id,
      status: gold.status,
      tables: gold.tables.length,
      cells: cells.length,
      indicatorCells: count(cells, (cell) => cell.gold.kind === "indicator"),
      outsideCells: count(cells, (cell) => cell.gold.kind !== "indicator"),
      cellsWithTruthBeyondRequest: count(cells, (cell) => cell.gold.truth !== undefined),
    },
    accuracy: { total: accuracyRow("전체", cells), tables: groups.map(([name, rows]) => accuracyRow(name, rows)).filter((row) => row.indicatorCells > 0) },
    restraint: { total: restraintRow("전체", cells), tables: groups.map(([name, rows]) => restraintRow(name, rows)).filter((row) => row.outsideCells > 0) },
    statusCounts,
    validationCounts,
    errorCounts,
    confidence: confidenceRows(cells),
    pairs: { byOutcome, checks },
    distribution: { a: distribution(docs.a, catalog), b: distribution(docs.b, catalog) },
    cells,
  };
}

const pct = (part: number, whole: number): string => (whole === 0 ? `${part}/0` : `${part}/${whole} (${((part / whole) * 100).toFixed(1)}%)`);
const bag = (items: Record<string, number>): string => {
  const entries = Object.entries(items).filter(([, value]) => value > 0);
  return entries.length === 0 ? "없음" : entries.sort((x, y) => y[1] - x[1]).map(([key, value]) => `${key} ${value}`).join(", ");
};
const labelText = (answer: RawLabel | null): string => (answer ? `${answer.kind === "indicator" ? answer.indicator_key : answer.kind} · ${answer.period ?? "기간 없음"} · ${answer.unit ?? "단위 없음"} · ${answer.boundary ?? "범위 없음"} · ${answer.confidence}` : "(답 없음)");
const goldText = (gold: GoldCell): string => `${gold.kind === "indicator" ? gold.indicator : gold.kind} · ${gold.period ?? "기간 없음"} · ${gold.unit ?? "단위 없음"} · ${gold.boundary}`;

/** 사람이 읽는 채점표. 모든 비율에 분모가 붙는다. */
export function renderScore(report: ScoreReport): string {
  const lines: string[] = ["# 뜻 붙이기 채점", ""];
  lines.push(`정답: ${report.gold.id} (${report.gold.status}). 표 ${report.gold.tables}개, 숫자 셀 ${report.gold.cells}개 = 지표 ${report.gold.indicatorCells} + 목록 밖 ${report.gold.outsideCells}.`);
  lines.push(`정답 셀의 답 상태: ${bag(report.statusCounts)}. 코드 확인: ${bag(report.validationCounts)}.`);
  lines.push(`요청에 근거가 없어 붙일 수 없는 뜻(기간이나 범위가 쪽의 본문·다른 각주에만 있다)이 있는 셀: ${report.gold.cellsWithTruthBeyondRequest}개.`, "");

  lines.push("## 1. 가리키기 정확도 (정답이 지표인 셀)", "", "분모는 정답이 지표인 셀 전부다. 답이 없는 셀은 틀린 것으로 센다.", "");
  lines.push("| 표 | 지표 셀 | 답 있음 | 지표 맞음 | 기간 맞음 | 단위 맞음 | 범위 맞음 | 넷 다 맞음 | 넷 다 맞고 코드 확인 통과 |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|");
  for (const row of [...report.accuracy.tables, report.accuracy.total]) {
    const n = row.indicatorCells;
    lines.push(`| ${row.name} | ${n} | ${row.answered} | ${pct(row.indicatorOk, n)} | ${pct(row.periodOk, n)} | ${pct(row.unitOk, n)} | ${pct(row.boundaryOk, n)} | ${pct(row.allFour, n)} | ${pct(row.allFourAccepted, n)} |`);
  }

  lines.push("", "## 2. 억지로 붙이지 않는가 (정답이 「목록에 없음」인 셀)", "", "| 표 | 목록 밖 셀 | 답 있음 | 목록 밖으로 둠 | 종류까지 맞음 | 억지로 지표를 붙임 | 그중 코드 확인 통과 |", "|---|---:|---:|---:|---:|---:|---:|");
  for (const row of [...report.restraint.tables, report.restraint.total]) {
    const n = row.outsideCells;
    lines.push(`| ${row.name} | ${n} | ${row.answered} | ${pct(row.keptOutside, n)} | ${pct(row.kindExact, n)} | ${pct(row.forced, n)} | ${row.forcedAccepted} |`);
  }

  lines.push("", "## 틀린 답의 종류 (정답 셀, 한 셀이 여러 종류에 들 수 있다)", "", `${bag(report.errorCounts)}`, "", "| 표 | 셀 | 원문 | 정답 | 모델의 답 | 종류 | 코드 확인 |", "|---|---|---|---|---|---|---|");
  for (const cell of report.cells.filter((item) => item.errors.length > 0 || item.status !== "answered")) {
    lines.push(`| ${cell.doc}:${cell.tableId} | ${cell.cell} | ${cell.raw} | ${goldText(cell.gold)} | ${labelText(cell.answer)} | ${cell.status === "answered" ? cell.errors.join(", ") : cell.status + (cell.failureKind ? `: ${cell.failureKind}` : "")} | ${cell.validation ?? "-"} |`);
  }

  lines.push("", "## 확신 등급과 정확도 (답이 하나 온 정답 셀)", "", "| 확신 | 답 수 | 맞음 (지표면 넷 다, 목록 밖이면 붙이지 않음) | 지표 셀에서 넷 다 맞음 | 목록 밖 셀에서 억지로 붙임 |", "|---|---:|---:|---:|---:|");
  for (const row of report.confidence) lines.push(`| ${row.confidence} | ${row.answered} | ${pct(row.correct, row.answered)} | ${pct(row.indicatorAllFour, row.indicatorCells)} | ${pct(row.forced, row.outsideCells)} |`);

  lines.push("", "## 4. 짝의 뜻이 정답과 같은가", "", "짝으로 견준 결과마다 양쪽 숫자에 붙은 뜻(지표·기간·범위)을 정답과 견줬다. 「뜻이 틀림」은 한쪽이라도 정답과 다른 짝, 「판정 못 함」은 정답이 없는 쪽이 있는 짝이다.", "", "| 결과 | 짝 수 | 양쪽 다 정답과 같음 | 뜻이 틀림 | 판정 못 함 (정답 밖) |", "|---|---:|---:|---:|---:|");
  for (const [outcome, counts] of Object.entries(report.pairs.byOutcome)) lines.push(`| ${outcome} | ${counts.both_correct + counts.wrong_label + counts.unjudged} | ${counts.both_correct} | ${counts.wrong_label} | ${counts.unjudged} |`);
  const wrong = report.pairs.checks.filter((check) => check.verdict === "wrong_label");
  if (wrong.length > 0) {
    lines.push("", "뜻이 틀린 짝:", "", "| 결과 | 붙은 뜻 | A (쪽, 셀, 원문) | A 정답 | B (쪽, 셀, 원문) | B 정답 |", "|---|---|---|---|---|---|");
    for (const check of wrong) {
      const side = (item: PairCheck["a"]): string => `${item.page}쪽 ${item.factId} 「${item.raw}」 (${item.verdict})`;
      lines.push(`| ${check.outcome} | ${check.key ? `${check.key.indicator} · ${check.key.period} · ${check.key.boundary}` : "-"} | ${side(check.a)} | ${check.a.gold ? goldText(check.a.gold) : "정답 없음"} | ${side(check.b)} | ${check.b.gold ? goldText(check.b.gold) : "정답 없음"} |`);
    }
  }

  lines.push("", "## 3. 전체 분포 (뜻 붙이기 대상 전부, 정답 표 밖 포함)", "", "| | A | B |", "|---|---:|---:|");
  const { a, b } = report.distribution;
  const row = (name: string, pick: (doc: Distribution) => string | number): void => void lines.push(`| ${name} | ${pick(a)} | ${pick(b)} |`);
  row("요청 단위 (답 받음 / 실패 / 안 돌림)", (doc) => `${doc.units} (${doc.unitsAnswered} / ${Object.values(doc.unitsFailed).reduce((sum, value) => sum + value, 0)} / ${doc.unitsNotRun})`);
  row("실패한 요청 단위의 종류", (doc) => bag(doc.unitsFailed));
  row("대상 숫자 (분모)", (doc) => doc.targets);
  row("답이 하나 온 숫자", (doc) => pct(doc.answered, doc.targets));
  row("답이 없는 숫자 (빠뜨림)", (doc) => pct(doc.missing, doc.targets));
  row("답이 둘 이상 온 숫자", (doc) => pct(doc.duplicate, doc.targets));
  row("실패한 요청에 든 숫자", (doc) => pct(doc.inFailedUnits, doc.targets));
  row("안 돌린 요청에 든 숫자", (doc) => pct(doc.inNotRunUnits, doc.targets));
  row("모델이 답한 종류", (doc) => bag(doc.answeredKind));
  row("확신 등급", (doc) => bag(doc.confidence));
  row("코드 확인 통과", (doc) => pct(doc.accepted, doc.targets));
  row("코드 확인에서 버려진 답 (사유별)", (doc) => `${Object.values(doc.rejectedByReason).reduce((sum, value) => sum + value, 0)}: ${bag(doc.rejectedByReason)}`);
  row("숫자에 묶이지 않는 버려진 답", (doc) => bag(doc.strayAnswers));
  row("붙은 지표 (답 기준)", (doc) => bag(doc.indicatorKeys));
  return `${lines.join("\n")}\n`;
}
