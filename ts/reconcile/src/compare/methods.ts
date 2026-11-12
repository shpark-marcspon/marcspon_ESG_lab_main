import { z } from "zod";
import type { Extraction, Fact, Grid } from "../domain/facts.js";
import { UNSPECIFIED_BOUNDARY, labelAnswerSchema, validateAnswers, type Catalog, type Label, type LabelAnswer, type LabelUnit, type Validation } from "../domain/label.js";
import { compareValues, type Finding, type Keyed, type Outcome } from "../domain/match.js";
import { resolveUnit, type UnitTable } from "../domain/units.js";

// 방법 비교 실측(MARC-TICK-21)의 방법 B·C·D·E·F: 답의 스키마, 답의 확인, 짝짓기.
// B·C·D·F 의 답에는 숫자 값을 적을 필드가 없다 (ID 로 가리키기만 한다). 없는 ID 를 가리킨 답은 버린다.
// 값의 비교는 전부 `domain/match.ts` 의 `compareValues` 다 (단위 환산, 반올림 허용폭).

export const PROMPTS = {
  b: "reconcile/by-table@1",
  c: "reconcile/by-document@1",
  d: "reconcile/pair-cells@1",
  e: "reconcile/find-diffs@1",
  f1: "reconcile/pair-tables@1",
  f2: "reconcile/pair-rows@1",
} as const;

// ── 방법들이 같이 쓰는 결과의 꼴 ────────────────────────────────────────────

/** 두 칸을 견준 결과 하나. 칸 ID 는 `표 ID:r행c열`. */
export interface Verdict {
  a: string;
  b: string;
  outcome: "match" | "mismatch" | "needs_explanation";
  reason: string;
  /** 코드가 견준 값이 다른가 (반올림 허용폭 밖). 견주지 못했으면 null. */
  valuesDiffer: boolean | null;
}

export interface MethodOutcome {
  /** 문서 A 의 칸과 문서 B 의 칸을 견준 결과. */
  verdicts: Verdict[];
  /** 한 문서 안의 두 칸을 견준 결과 (방법 C). a, b 는 그 문서의 칸. */
  intra: (Verdict & { doc: "a" | "b" })[];
  /** 견준 결과에 들지 못한 칸의 상태 (`unpaired:사유`, `unverified:사유`). 방법이 숫자 단위로 낼 수 있을 때만 채운다. */
  states: { a: Record<string, string>; b: Record<string, string> };
  /** 버려진 답의 수 (사유별). */
  rejected: Record<string, number>;
}

const emptyOutcome = (): MethodOutcome => ({ verdicts: [], intra: [], states: { a: {}, b: {} }, rejected: {} });
const bump = (bag: Record<string, number>, key: string): void => void (bag[key] = (bag[key] ?? 0) + 1);

/** A 계열과 B: `reconcile` 의 결과를 방법 공통의 꼴로. */
export function outcomeFromFindings(findings: Finding[]): MethodOutcome {
  const out = emptyOutcome();
  for (const finding of findings) {
    if (finding.a && finding.b) {
      out.verdicts.push({ a: finding.a.factId, b: finding.b.factId, outcome: finding.outcome as Verdict["outcome"], reason: finding.reason, valuesDiffer: finding.comparison ? finding.comparison.rule === "none" : null });
      continue;
    }
    const side = finding.a ? "a" : "b";
    const view = finding.a ?? finding.b;
    if (view) out.states[side][view.factId] = `${finding.outcome}:${finding.reason}`;
  }
  return out;
}

/** 두 칸의 값을 `compareValues` 로 견준다. 단위 글자가 단위 표에 없으면 단위 없이 견주고 그 사실을 사유에 남긴다. */
export function compareCells(a: { fact: Fact; unit: string | null }, b: { fact: Fact; unit: string | null }, units: UnitTable): { outcome: Outcome; reason: string; valuesDiffer: boolean } {
  const keyed = (side: { fact: Fact; unit: string | null }): Keyed => {
    const text = side.unit ?? side.fact.unitSuffix;
    const label: Label = { factId: side.fact.id, kind: "indicator", indicatorKey: null, freeText: null, period: null, unit: text, boundary: UNSPECIFIED_BOUNDARY, basisNote: null, confidence: "high", evidence: [], source: "ai" };
    return { fact: side.fact, label, indicator: "", period: "", boundary: UNSPECIFIED_BOUNDARY, unit: text === null ? null : resolveUnit(text, units), notes: [] };
  };
  const result = compareValues(keyed(a), keyed(b));
  return { outcome: result.outcome, reason: result.reason, valuesDiffer: result.comparison.rule === "none" };
}

const cellFacts = (extraction: Extraction): Map<string, Fact> => new Map(extraction.facts.filter((fact) => fact.bucket === "table_cell").map((fact) => [fact.id, fact]));

/** 쪽마다의 글자 (표의 칸과 글 블록). 기간·단위 글자가 원문에 있는지 볼 때 쓴다. */
export function pageTexts(extraction: Extraction, blockText: Map<string, string>, blockPages: Map<string, number>): Map<number, string> {
  const pages = new Map<number, string[]>();
  const push = (page: number | null, text: string): void => {
    if (page !== null) pages.set(page, [...(pages.get(page) ?? []), text]);
  };
  for (const grid of extraction.grids) push(grid.page, grid.cells.flat().join(" "));
  for (const [id, text] of blockText) push(blockPages.get(id) ?? null, text);
  return new Map([...pages.entries()].map(([page, texts]) => [page, texts.join(" ").normalize("NFKC")]));
}

const yearIn = (text: string, year: string): boolean => new RegExp(`(?<![\\d,.])${year}(?![\\d,])`).test(text) || text.includes(`'${year.slice(2)}`) || text.includes(`’${year.slice(2)}`);
const squash = (text: string): string => text.normalize("NFKC").replace(/\s+/g, "");

// ── 방법 B: 지표 우선, 표 단위 ──────────────────────────────────────────────

export const byTableSchema = z.object({
  items: z.array(
    z.object({
      indicator_key: z.string(),
      /** 원문의 단위 글자 그대로. */
      unit: z.string().nullable(),
      boundary: z.string().nullable(),
      basis_note: z.string().nullable(),
      cells: z.array(z.object({ target: z.string(), period: z.string().nullable() })),
    }),
  ),
});
export type ByTableAnswer = z.infer<typeof byTableSchema>;

/** B 의 답을 A 와 같은 문(`validateAnswers`)에 넣는다. 가리키지 않은 숫자는 「이 표에 목록의 지표로 실리지 않았다」는 답이다. */
export function validateByTable(unit: LabelUnit, answer: ByTableAnswer, ctx: { catalog: Catalog; facts: Map<string, Fact> }): Validation {
  const labels: LabelAnswer["labels"] = answer.items.flatMap((item) =>
    item.cells.map((cell) => ({ target: cell.target, quote: null, occurrence: null, kind: "indicator" as const, indicator_key: item.indicator_key, free_text: null, period: cell.period, unit: item.unit, boundary: item.boundary, basis_note: item.basis_note, confidence: "high" as const, evidence: [] })),
  );
  return validateAnswers(unit, labelAnswerSchema.parse({ labels }), ctx);
}

// ── 방법 C: 지표 우선, 문서 통째 ────────────────────────────────────────────

export const byDocumentSchema = z.object({
  items: z.array(
    z.object({
      indicator_key: z.string(),
      places: z.array(z.object({ cell: z.string(), period: z.string().nullable(), unit: z.string().nullable(), boundary: z.string().nullable() })),
    }),
  ),
});
export type ByDocumentAnswer = z.infer<typeof byDocumentSchema>;

export interface Place {
  fact: Fact;
  indicator: string;
  period: string | null;
  unit: string | null;
  boundary: string;
}

export interface DocContext {
  extraction: Extraction;
  /** 쪽 → 그 쪽의 글자. */
  pages: Map<number, string>;
}

/** C 의 답 확인: 있는 숫자 칸인가, 목록의 지표·범위인가, 기간·단위 글자가 그 쪽에 있는가. 같은 칸을 두 번 가리키면 둘 다 버린다. */
export function validateByDocument(answer: ByDocumentAnswer, doc: DocContext, catalog: Catalog): { places: Place[]; rejected: Record<string, number> } {
  const facts = cellFacts(doc.extraction);
  const indicators = new Set(catalog.indicators.map((item) => item.key));
  const boundaries = new Set(catalog.boundaries.map((item) => item.key));
  const rejected: Record<string, number> = {};
  const seen = new Map<string, Place[]>();
  for (const item of answer.items) {
    for (const raw of item.places) {
      const fact = facts.get(raw.cell);
      const boundary = raw.boundary ?? UNSPECIFIED_BOUNDARY;
      const page = doc.pages.get(fact?.where.page ?? -1) ?? "";
      const reason = !fact
        ? "unknown_id"
        : !indicators.has(item.indicator_key)
          ? "unknown_indicator"
          : !boundaries.has(boundary)
            ? "unknown_boundary"
            : raw.period !== null && !/^(19|20)\d{2}$/.test(raw.period)
              ? "bad_period"
              : raw.period !== null && !yearIn(page, raw.period)
                ? "period_not_in_source"
                : raw.unit !== null && !squash(page).includes(squash(raw.unit)) && squash(fact.unitSuffix ?? "") !== squash(raw.unit)
                  ? "unit_not_in_source"
                  : null;
      if (reason !== null || !fact) {
        bump(rejected, reason ?? "unknown_id");
        continue;
      }
      seen.set(fact.id, [...(seen.get(fact.id) ?? []), { fact, indicator: item.indicator_key, period: raw.period, unit: raw.unit, boundary }]);
    }
  }
  const places: Place[] = [];
  for (const list of seen.values()) {
    if (list.length > 1) bump(rejected, "duplicate_answer");
    else places.push(list[0] as Place);
  }
  return { places, rejected };
}

/**
 * C 의 짝짓기: 두 문서의 같은 (지표, 기간) 끼리 모든 자리를 서로 견준다. 한 문서 안의 여러 자리끼리도 견준다.
 * 범위가 양쪽에 다르게 적혀 있으면 값이 같든 다르든 「설명 필요」다 (A2 의 규칙과 같다). 기간이 없는 자리는 견주지 않는다.
 */
export function matchByDocument(a: Place[], b: Place[], units: UnitTable): MethodOutcome {
  const out = emptyOutcome();
  const groups = new Map<string, { a: Place[]; b: Place[] }>();
  for (const side of ["a", "b"] as const) {
    for (const place of side === "a" ? a : b) {
      if (place.period === null) {
        out.states[side][place.fact.id] = "unverified:period_unknown";
        continue;
      }
      const key = `${place.indicator}|${place.period}`;
      const group = groups.get(key) ?? { a: [], b: [] };
      group[side].push(place);
      groups.set(key, group);
    }
  }
  const judge = (x: Place, y: Place): Verdict => {
    const result = compareCells({ fact: x.fact, unit: x.unit }, { fact: y.fact, unit: y.unit }, units);
    const boundaryDiffers = x.boundary !== UNSPECIFIED_BOUNDARY && y.boundary !== UNSPECIFIED_BOUNDARY && x.boundary !== y.boundary;
    return { a: x.fact.id, b: y.fact.id, outcome: boundaryDiffers ? "needs_explanation" : (result.outcome as Verdict["outcome"]), reason: boundaryDiffers ? "boundary_differs" : result.reason, valuesDiffer: result.valuesDiffer };
  };
  for (const group of groups.values()) {
    for (const x of group.a) for (const y of group.b) out.verdicts.push(judge(x, y));
    for (const side of ["a", "b"] as const) {
      const list = group[side];
      for (let i = 0; i < list.length; i++) for (let j = i + 1; j < list.length; j++) out.intra.push({ ...judge(list[i] as Place, list[j] as Place), doc: side });
      if (group[side === "a" ? "b" : "a"].length === 0) for (const place of list) out.states[side][place.fact.id] = "unpaired:no_counterpart";
    }
  }
  return out;
}

// ── 방법 D: 목록 없이 칸끼리 짝짓기 (숫자 가림) ─────────────────────────────

export const pairCellsSchema = z.object({
  pairs: z.array(z.object({ a: z.string(), b: z.string(), unit_a: z.string().nullable(), unit_b: z.string().nullable() })),
});
export type PairCellsAnswer = z.infer<typeof pairCellsSchema>;

/** D 의 답 확인과 비교: 양쪽 ID 가 그 문서의 숫자 칸이어야 하고, 단위 글자는 그 쪽에 있어야 한다. 같은 짝을 두 번 적으면 한 번만 센다. */
export function judgePairs(answer: PairCellsAnswer, a: DocContext, b: DocContext, units: UnitTable): MethodOutcome {
  const out = emptyOutcome();
  const factsA = cellFacts(a.extraction);
  const factsB = cellFacts(b.extraction);
  const done = new Set<string>();
  for (const pair of answer.pairs) {
    const fa = factsA.get(pair.a);
    const fb = factsB.get(pair.b);
    if (!fa || !fb) {
      bump(out.rejected, "unknown_id");
      continue;
    }
    if (done.has(`${fa.id}|${fb.id}`)) {
      bump(out.rejected, "duplicate_pair");
      continue;
    }
    const unitOk = (unit: string | null, doc: DocContext, fact: Fact): boolean => unit === null || squash(doc.pages.get(fact.where.page ?? -1) ?? "").includes(squash(unit)) || squash(fact.unitSuffix ?? "") === squash(unit);
    if (!unitOk(pair.unit_a, a, fa) || !unitOk(pair.unit_b, b, fb)) {
      bump(out.rejected, "unit_not_in_source");
      continue;
    }
    done.add(`${fa.id}|${fb.id}`);
    const result = compareCells({ fact: fa, unit: pair.unit_a }, { fact: fb, unit: pair.unit_b }, units);
    out.verdicts.push({ a: fa.id, b: fb.id, outcome: result.outcome as Verdict["outcome"], reason: result.reason, valuesDiffer: result.valuesDiffer });
  }
  return out;
}

// ── 방법 E: 숫자째 통째로 주고 어긋나는 곳을 받는다 (비교 기준) ──────────────

export const findDiffsSchema = z.object({
  discrepancies: z.array(z.object({ a: z.string(), b: z.string(), reason: z.string() })),
});
export type FindDiffsAnswer = z.infer<typeof findDiffsSchema>;

/** E 의 답 확인: 가리킨 칸 ID 가 있는지만 본다. 「어긋남」은 모델의 말이고, 코드가 견준 값의 같고 다름은 참고로만 붙인다. */
export function judgeDiffs(answer: FindDiffsAnswer, a: DocContext, b: DocContext, units: UnitTable): MethodOutcome {
  const out = emptyOutcome();
  const factsA = cellFacts(a.extraction);
  const factsB = cellFacts(b.extraction);
  for (const item of answer.discrepancies) {
    const fa = factsA.get(item.a);
    const fb = factsB.get(item.b);
    if (!fa || !fb) {
      bump(out.rejected, "unknown_id");
      continue;
    }
    out.verdicts.push({ a: fa.id, b: fb.id, outcome: "mismatch", reason: "model_said_differs", valuesDiffer: compareCells({ fact: fa, unit: null }, { fact: fb, unit: null }, units).valuesDiffer });
  }
  return out;
}

// ── 방법 F: 표끼리 먼저 짝짓고 그 안에서 행·열 맞추기 ───────────────────────

export const pairTablesSchema = z.object({ table_pairs: z.array(z.object({ a_table: z.string(), b_table: z.string() })) });
export type PairTablesAnswer = z.infer<typeof pairTablesSchema>;
export const pairRowsSchema = z.object({ row_pairs: z.array(z.object({ a_row: z.string(), b_row: z.string() })) });
export type PairRowsAnswer = z.infer<typeof pairRowsSchema>;

/** 표 짝 답의 확인: 양쪽 표 ID(`a:p46-t2` 꼴)가 그 문서의 표 목록에 있어야 한다. 같은 짝은 한 번만. */
export function validateTablePairs(answer: PairTablesAnswer, tablesA: Set<string>, tablesB: Set<string>): { pairs: { a: string; b: string }[]; rejected: Record<string, number> } {
  const rejected: Record<string, number> = {};
  const pairs = new Map<string, { a: string; b: string }>();
  for (const raw of answer.table_pairs) {
    const a = raw.a_table.replace(/^a:/, "");
    const b = raw.b_table.replace(/^b:/, "");
    if (!raw.a_table.startsWith("a:") || !raw.b_table.startsWith("b:") || !tablesA.has(a) || !tablesB.has(b)) bump(rejected, "unknown_id");
    else if (pairs.has(`${a}|${b}`)) bump(rejected, "duplicate_pair");
    else pairs.set(`${a}|${b}`, { a, b });
  }
  return { pairs: [...pairs.values()], rejected };
}

/** 머리글 글자의 정규화: NFKC, 끝의 각주 표시(`1)` 한 자리) 떼기, 공백·문장부호 지우기, 소문자. 두 자리 각주 번호는 떼지 못한다. */
export function headerKey(text: string): string {
  return text.normalize("NFKC").replace(/\d\)\s*$/u, "").replace(/[\s·,()\-–_:;/'"‘’“”>&+*]/g, "").toLowerCase();
}

interface Line {
  index: number;
  /** 정규화한 머리글 조각 (단위 글자는 뺀다). */
  parts: string[];
  texts: string[];
}

function lines(facts: Fact[], axis: "row" | "col", units: UnitTable): Line[] {
  const seen = new Map<number, Line>();
  for (const fact of facts) {
    const index = (axis === "row" ? fact.where.row : fact.where.col) as number;
    if (seen.has(index)) continue;
    const texts = (fact.headers?.[axis] ?? []).map((ref) => ref.text);
    seen.set(index, { index, texts, parts: texts.filter((text) => resolveUnit(text, units) === null).map(headerKey).filter((part) => part !== "") });
  }
  return [...seen.values()].sort((x, y) => x.index - y.index);
}

/** 글자로 맞춘다: (1) 머리글 경로 전체가 같고 양쪽에서 하나뿐 (2) 남은 것 중 마지막 조각이 같고 양쪽에서 하나뿐 (3) 머리글이 없는 것이 양쪽에 하나씩. */
function alignLines(a: Line[], b: Line[]): { pairs: [number, number][]; restA: Line[]; restB: Line[] } {
  const pairs: [number, number][] = [];
  let restA = [...a];
  let restB = [...b];
  for (const keyOf of [(line: Line) => line.parts.join(">"), (line: Line) => line.parts[line.parts.length - 1] ?? ""]) {
    const count = (list: Line[]): Map<string, Line[]> => {
      const map = new Map<string, Line[]>();
      for (const line of list) map.set(keyOf(line), [...(map.get(keyOf(line)) ?? []), line]);
      return map;
    };
    const inA = count(restA);
    const inB = count(restB);
    const matched = new Set<Line>();
    for (const [key, list] of inA) {
      const other = inB.get(key);
      if (list.length !== 1 || other?.length !== 1) continue;
      if (key === "" && !(restA.length === 1 && restB.length === 1) && !(inA.get("")?.length === 1 && inB.get("")?.length === 1)) continue;
      pairs.push([(list[0] as Line).index, (other[0] as Line).index]);
      matched.add(list[0] as Line).add(other[0] as Line);
    }
    restA = restA.filter((line) => !matched.has(line));
    restB = restB.filter((line) => !matched.has(line));
  }
  return { pairs, restA, restB };
}

/** 칸의 단위: 숫자에 붙은 접미, 행·열 머리글, 「단위:」 줄 가운데 단위 표에 있는 첫 글자. */
function unitOf(fact: Fact, grid: Grid, units: UnitTable): string | null {
  const inline = (text: string): string[] => [text, ...[...text.matchAll(/단위\s*[:：]\s*([^)\s]+)/gu)].map((match) => match[1] as string)];
  const candidates = [fact.unitSuffix ?? "", ...(fact.headers?.row ?? []).flatMap((ref) => inline(ref.text)), ...(fact.headers?.col ?? []).flatMap((ref) => inline(ref.text)), ...grid.unitLines.flatMap(inline)];
  return candidates.find((text) => text !== "" && resolveUnit(text, units) !== null) ?? null;
}

export interface TableAlignment {
  a: string;
  b: string;
  cellPairs: { a: Fact; b: Fact; unitA: string | null; unitB: string | null }[];
  /** 글자로 못 맞춘 행 (양쪽에 다 남았을 때 AI 에게 묻는다). */
  unmatchedRows: { a: { id: string; header: string }[]; b: { id: string; header: string }[] };
}

/** 짝지어진 두 표의 행과 열을 맞춰 칸의 짝을 낸다. `rowPairs` 는 AI 가 답한 행 짝(행 ID `r3` 꼴)이고, 없는 행을 가리킨 것은 버린다. */
export function alignTables(a: { grid: Grid; facts: Fact[] }, b: { grid: Grid; facts: Fact[] }, units: UnitTable, rowPairs: PairRowsAnswer["row_pairs"] = []): TableAlignment & { rejectedRowPairs: number } {
  const rows = alignLines(lines(a.facts, "row", units), lines(b.facts, "row", units));
  let rejectedRowPairs = 0;
  for (const pair of rowPairs) {
    const left = rows.restA.find((line) => `r${line.index}` === pair.a_row);
    const right = rows.restB.find((line) => `r${line.index}` === pair.b_row);
    if (!left || !right) {
      rejectedRowPairs += 1;
      continue;
    }
    rows.pairs.push([left.index, right.index]);
    rows.restA = rows.restA.filter((line) => line !== left);
    rows.restB = rows.restB.filter((line) => line !== right);
  }
  const matchedA = new Set(rows.pairs.map(([row]) => row));
  const matchedB = new Set(rows.pairs.map(([, row]) => row));
  const colsA = lines(a.facts.filter((fact) => matchedA.has(fact.where.row as number)), "col", units);
  const colsB = lines(b.facts.filter((fact) => matchedB.has(fact.where.row as number)), "col", units);
  const cols = alignLines(colsA, colsB);
  // 열 머리글이 글자로 하나도 안 맞았고 양쪽의 값 열이 하나씩이면 그 둘을 맞춘다.
  if (cols.pairs.length === 0 && colsA.length === 1 && colsB.length === 1) cols.pairs.push([(colsA[0] as Line).index, (colsB[0] as Line).index]);
  const at = (facts: Fact[]): Map<string, Fact> => new Map(facts.map((fact) => [`${fact.where.row}|${fact.where.col}`, fact]));
  const inA = at(a.facts);
  const inB = at(b.facts);
  const cellPairs: TableAlignment["cellPairs"] = [];
  for (const [rowA, rowB] of rows.pairs) {
    for (const [colA, colB] of cols.pairs) {
      const fa = inA.get(`${rowA}|${colA}`);
      const fb = inB.get(`${rowB}|${colB}`);
      if (fa && fb) cellPairs.push({ a: fa, b: fb, unitA: unitOf(fa, a.grid, units), unitB: unitOf(fb, b.grid, units) });
    }
  }
  const show = (list: Line[]): { id: string; header: string }[] => list.map((line) => ({ id: `r${line.index}`, header: line.texts.join(" > ") }));
  return { a: a.grid.id, b: b.grid.id, cellPairs, unmatchedRows: { a: show(rows.restA), b: show(rows.restB) }, rejectedRowPairs };
}

/** F 의 결과: 맞춘 칸끼리 값을 견준다. 한 칸이 여러 표 짝에서 견주어지면 전부 남긴다. 견주지 못한 숫자 칸은 「확인 못 함」이다. */
export function judgeAlignments(alignments: TableAlignment[], a: Extraction, b: Extraction, units: UnitTable): MethodOutcome {
  const out = emptyOutcome();
  const done = new Set<string>();
  const touched = { a: new Set<string>(), b: new Set<string>() };
  const inPairedTable = { a: new Set(alignments.map((item) => item.a)), b: new Set(alignments.map((item) => item.b)) };
  for (const alignment of alignments) {
    for (const pair of alignment.cellPairs) {
      touched.a.add(pair.a.id);
      touched.b.add(pair.b.id);
      if (done.has(`${pair.a.id}|${pair.b.id}`)) continue;
      done.add(`${pair.a.id}|${pair.b.id}`);
      const result = compareCells({ fact: pair.a, unit: pair.unitA }, { fact: pair.b, unit: pair.unitB }, units);
      out.verdicts.push({ a: pair.a.id, b: pair.b.id, outcome: result.outcome as Verdict["outcome"], reason: result.reason, valuesDiffer: result.valuesDiffer });
    }
  }
  for (const side of ["a", "b"] as const) {
    for (const fact of cellFacts(side === "a" ? a : b).values()) {
      if (touched[side].has(fact.id)) continue;
      out.states[side][fact.id] = inPairedTable[side].has(fact.where.gridId as string) ? "unverified:row_or_column_not_aligned" : "unverified:table_not_paired";
    }
  }
  return out;
}
