import type { Extraction, Fact, HeaderRef } from "../domain/facts.js";
import { resolveUnit, type UnitTable } from "../domain/units.js";
import { TallyError, type CellJudgement, type TablePair } from "./hybrid.js";
import { headerKey, validateTablePairs, type PairTablesAnswer, type Verdict } from "./methods.js";
import type { CellReview2Judgement, CellReview2State, PairPlacesAnswer } from "./prose.js";
import { renderTableList, tablesWithValues } from "./render.js";
import { MAX_REMAINING_PER_REQUEST, deriveTablePairs, type CellReviewPlan } from "./review.js";

// 방법 R (MARC-TICK-28): 저장된 D 계열 한 실행의 답 뒤에 「열 밀림 검사」와 「빠진 표 되찾기」를 붙인다. D 를 다시 부르지 않는다.
//  1. 열 밀림 검사 (AI 없음): 표의 숫자 칸끼리의 짝마다 두 칸의 열 머리글·행 머리글을 견준다. 머리글이 상대 표의 다른 열(행)을 가리키면 「의심」이다.
//  2. 지목 (AI 없음): 다시 물을 표 짝을 코드가 고른다. (i) 버려진 답이 가리키는 표 짝 (ii) 의심이 나온 표 짝 (iii) 머리글로 보면 상대가 있는 남은 칸이 있는 표 짝
//     (iv) 어느 짝에도 들지 않은 표: 머리글이 겹치는 후보만 추려 표 목록으로 묻는다 (`reconcile/review-tables@1`).
//  3. 다시 묻기 (AI): 지목된 표 짝마다 요청 하나. 남은 짝이 있는 표 짝은 칸 검수(`reconcile/review-cells@2`: 남은 칸마다 답 하나), 남은 짝이 없는 표 짝은 칸 짝 요청(`reconcile/pair-cells-in-tables@1`).
//  4. 합치기 (AI 없음): 원래 짝과 다시 받은 짝을 합치고(같은 짝은 한 번), 의심으로 걸린 짝은 어느 쪽에서 왔든 견준 결과에서 뺀다 (따로 센다).
// 이 파일은 정답 쌍·심은 오류 명세·판정 기록을 읽지 않는다. 규칙은 문서 모델의 머리글, 버려진 답의 ID, 남은 칸만 쓴다. 값 비교는 코드(`compareValues`)다.

/** (iii) 머리글로 보면 상대가 있는 남은 칸이 이만큼 있으면 그 표 짝을 다시 묻는다. */
export const MIN_EXPECTED_BY_HEADER = 1;
/** (iv) 후보 표 짝: 행 머리글이 이만큼 같고 열 머리글이 하나 이상 같다. */
export const MIN_SHARED_ROW_HEADERS = 3;
export const MIN_SHARED_COL_HEADERS = 1;
/** (iv) 표 검수가 낸 새 표 짝 가운데 칸을 다시 묻는 최대 수 (머리글이 많이 겹치는 것부터). */
export const MAX_NEW_TABLE_PAIRS = 4;
/** 칸 짝 요청(`pair-cells-in-tables@1`)의 짝 하나의 출력 어림 (가정). */
export const PAIR_ANSWER_TOKENS = 45;

const key = (pair: { a: string; b: string }): string => `${pair.a}|${pair.b}`;
const gridOf = (cell: string): string => cell.slice(0, cell.lastIndexOf(":"));
const byId = (x: string, y: string): number => x.localeCompare(y, "en", { numeric: true });
const cellFacts = (extraction: Extraction): Map<string, Fact> => new Map(extraction.facts.filter((fact) => fact.bucket === "table_cell").map((fact) => [fact.id, fact]));

// ── 1. 열 밀림 검사 ──────────────────────────────────────────────────────────

/** 머리글 경로의 열쇠: 단위 글자와 빈 조각을 빼고, 조각마다 `headerKey` 로 정규화해 잇는다. 머리글이 없으면 빈 글자다. */
export function lineKey(refs: HeaderRef[] | undefined, units: UnitTable): string {
  return (refs ?? [])
    .filter((ref) => resolveUnit(ref.text, units) === null)
    .map((ref) => headerKey(ref.text))
    .filter((part) => part !== "")
    .join(">");
}

export interface TableLines {
  /** 열 번호 → 그 열의 머리글 열쇠 (그 열의 숫자 칸마다 다르면 여럿). */
  cols: Map<number, Set<string>>;
  rows: Map<number, Set<string>>;
}

/** 표마다 숫자 칸의 열·행 머리글 열쇠. */
export function tableLines(extraction: Extraction, units: UnitTable): Map<string, TableLines> {
  const out = new Map<string, TableLines>();
  for (const fact of cellFacts(extraction).values()) {
    const gridId = fact.where.gridId as string;
    const lines = out.get(gridId) ?? { cols: new Map(), rows: new Map() };
    const put = (bag: Map<number, Set<string>>, index: number, value: string): void => void bag.set(index, (bag.get(index) ?? new Set()).add(value));
    put(lines.cols, fact.where.col as number, lineKey(fact.headers?.col, units));
    put(lines.rows, fact.where.row as number, lineKey(fact.headers?.row, units));
    out.set(gridId, lines);
  }
  return out;
}

/** same: 두 칸의 머리글이 같다. shifted: 머리글이 다르고, 한쪽의 머리글이 상대 표의 다른 열(행)에 그대로 있다. unknown: 머리글이 없거나, 달리 적혀 있어 가를 수 없다. */
export type AxisCheck = "same" | "shifted" | "unknown";

export interface ShiftCheck {
  a: string;
  b: string;
  col: AxisCheck;
  row: AxisCheck;
  /** ok: 열과 행이 다 같다. suspect: 열이나 행이 밀렸다. undecided: 판단 못 함. */
  verdict: "ok" | "suspect" | "undecided";
  /** 두 칸의 열·행 머리글 넷 가운데 하나라도 서지 않았다 (머리글이 없는 표). */
  noHeader: boolean;
}

/**
 * 밀림으로 보는 기준. either: 한쪽의 머리글이 상대 표의 다른 열(행)에 있으면. both: 양쪽 다 그럴 때만 (두 칸이 저마다 「제 짝이 될 줄」을 상대 표에 두고도 엇갈려 짝지어졌다).
 * either 는 첫 채점 때의 기준이고 both 는 그 뒤에 한 번 고친 기준이다 (보고서에 두 성적을 같이 싣는다).
 */
export type ShiftRule = "either" | "both";
export const SHIFT_RULE: ShiftRule = "both";

function axisCheck(mine: string, theirs: string, myIndex: number, theirIndex: number, myLines: Map<number, Set<string>>, theirLines: Map<number, Set<string>>, rule: ShiftRule): AxisCheck {
  if (mine === "" || theirs === "") return "unknown";
  if (mine === theirs) return "same";
  const elsewhere = (lines: Map<number, Set<string>>, at: number, wanted: string): boolean => [...lines].some(([index, keys]) => index !== at && keys.has(wanted));
  const forward = elsewhere(theirLines, theirIndex, mine);
  const backward = elsewhere(myLines, myIndex, theirs);
  return (rule === "both" ? forward && backward : forward || backward) ? "shifted" : "unknown";
}

/**
 * 표의 숫자 칸끼리의 짝 하나의 열 밀림 검사. 두 칸의 열 머리글이 다른데 a 칸의 머리글이 b 의 표의 다른 열에 그대로 있으면(또는 그 반대)
 * 「머리글은 다른 열을 가리킨다」는 뜻이라 의심으로 가린다. 행도 같은 방식으로 본다. 머리글이 서지 않았거나 두 표가 머리글을 달리 적었으면 판단하지 않는다.
 */
export function checkShift(a: Fact, b: Fact, linesA: Map<string, TableLines>, linesB: Map<string, TableLines>, units: UnitTable, rule: ShiftRule = SHIFT_RULE): ShiftCheck {
  const la = linesA.get(a.where.gridId as string);
  const lb = linesB.get(b.where.gridId as string);
  if (!la || !lb) return { a: a.id, b: b.id, col: "unknown", row: "unknown", verdict: "undecided", noHeader: true };
  const keys = { colA: lineKey(a.headers?.col, units), colB: lineKey(b.headers?.col, units), rowA: lineKey(a.headers?.row, units), rowB: lineKey(b.headers?.row, units) };
  const col = axisCheck(keys.colA, keys.colB, a.where.col as number, b.where.col as number, la.cols, lb.cols, rule);
  const row = axisCheck(keys.rowA, keys.rowB, a.where.row as number, b.where.row as number, la.rows, lb.rows, rule);
  return { a: a.id, b: b.id, col, row, verdict: col === "shifted" || row === "shifted" ? "suspect" : col === "same" && row === "same" ? "ok" : "undecided", noHeader: Object.values(keys).some((text) => text === "") };
}

/** 짝들 가운데 표의 숫자 칸끼리의 짝 전부에 검사를 붙인다 (서술이 낀 짝은 대상이 아니다). */
export function checkShifts(verdicts: { a: string; b: string }[], a: Extraction, b: Extraction, units: UnitTable, rule: ShiftRule = SHIFT_RULE): ShiftCheck[] {
  const facts = { a: cellFacts(a), b: cellFacts(b) };
  const lines = { a: tableLines(a, units), b: tableLines(b, units) };
  const out: ShiftCheck[] = [];
  for (const verdict of verdicts) {
    const fa = facts.a.get(verdict.a);
    const fb = facts.b.get(verdict.b);
    if (fa && fb) out.push(checkShift(fa, fb, lines.a, lines.b, units, rule));
  }
  return out;
}

export const suspectKeys = (checks: ShiftCheck[]): Set<string> => new Set(checks.filter((check) => check.verdict === "suspect").map(key));

// ── 2. 지목 ──────────────────────────────────────────────────────────────────

export type NominationReason = "rejected_answer" | "shift_suspect" | "expected_by_header";

export interface Nomination {
  pair: TablePair;
  reasons: NominationReason[];
  /** (i) 이 표 짝을 가리킨 버려진 답의 수. */
  rejectedAnswers: number;
  /** (ii) 이 표 짝의 의심 짝의 수. */
  suspects: number;
  /** (iii) 머리글로 보면 상대가 있는 남은 칸의 수 (문서 a 의 칸으로 센다). */
  expectedByHeader: number;
}

/** 원답의 ID 에서 표 ID 를 읽는다 (`a:표ID:r행c열` 꼴만. 글 속 자리의 ID 는 표 짝을 가리키지 않는다). */
function cellRef(id: string): { side: "a" | "b"; gridId: string; cell: string } | null {
  const match = /^([ab]):(.+):r\d+c\d+$/.exec(id);
  return match ? { side: match[1] as "a" | "b", gridId: match[2] as string, cell: id.slice(2) } : null;
}

/** 한 표 짝 안에서, 견주어지지 않은 a 칸 가운데 「같은 행 머리글·같은 열 머리글」의 견주어지지 않은 b 칸이 꼭 하나 있는 것. */
export function expectedByHeader(pair: TablePair, a: Extraction, b: Extraction, compared: { a: Set<string>; b: Set<string> }, units: UnitTable): { a: string; b: string }[] {
  const sign = (fact: Fact): string | null => {
    const row = lineKey(fact.headers?.row, units);
    const col = lineKey(fact.headers?.col, units);
    return row === "" || col === "" ? null : `${row}\n${col}`;
  };
  const left = (extraction: Extraction, side: "a" | "b"): Fact[] => [...cellFacts(extraction).values()].filter((fact) => fact.where.gridId === pair[side] && !compared[side].has(fact.id));
  const inB = new Map<string, Fact[]>();
  for (const fact of left(b, "b")) {
    const s = sign(fact);
    if (s !== null) inB.set(s, [...(inB.get(s) ?? []), fact]);
  }
  const inA = new Map<string, Fact[]>();
  for (const fact of left(a, "a")) {
    const s = sign(fact);
    if (s !== null) inA.set(s, [...(inA.get(s) ?? []), fact]);
  }
  const out: { a: string; b: string }[] = [];
  for (const [s, list] of inA) {
    const other = inB.get(s);
    if (list.length === 1 && other?.length === 1) out.push({ a: (list[0] as Fact).id, b: (other[0] as Fact).id });
  }
  return out;
}

export interface TableCandidate extends TablePair {
  sharedRows: number;
  sharedCols: number;
}

export interface NominationResult {
  /** (i)~(iii) 로 지목된 표 짝. */
  nominations: Nomination[];
  /** 원래 답에서 끌어낸 표 짝 (의심 짝도 넣어 센 것). */
  derived: TablePair[];
  /** (iv) 어느 짝에도 들지 않은, 숫자 칸이 있는 표. */
  unpaired: { a: string[]; b: string[] };
  /** (iv) 표 목록으로 물을 후보 표 짝 (적어도 한쪽이 짝 없는 표이고 머리글이 겹친다). */
  candidates: TableCandidate[];
}

/**
 * 다시 물을 표 짝을 고른다. 입력은 그 실행의 원답(버려진 답을 읽으려고), 받아들여진 짝, 열 밀림 검사의 결과, 두 문서뿐이다.
 */
export function nominate(input: { a: Extraction; b: Extraction; answers: PairPlacesAnswer[]; verdicts: Verdict[]; checks: ShiftCheck[]; units: UnitTable }): NominationResult {
  const { a, b, units } = input;
  const facts = { a: cellFacts(a), b: cellFacts(b) };
  const withValues = { a: new Set(tablesWithValues(a).map((grid) => grid.id)), b: new Set(tablesWithValues(b).map((grid) => grid.id)) };
  const accepted = new Set(input.verdicts.map(key));
  const suspects = suspectKeys(input.checks);
  const cells = input.verdicts.filter((verdict) => facts.a.has(verdict.a) && facts.b.has(verdict.b));
  const kept = input.verdicts.filter((verdict) => !suspects.has(key(verdict)));
  const compared = { a: new Set(kept.map((verdict) => verdict.a)), b: new Set(kept.map((verdict) => verdict.b)) };
  const derived = deriveTablePairs(cells).map((pair) => ({ a: pair.a, b: pair.b }));

  const found = new Map<string, Nomination>();
  const entry = (pair: TablePair): Nomination => {
    const existing = found.get(key(pair));
    if (existing) return existing;
    const created: Nomination = { pair, reasons: [], rejectedAnswers: 0, suspects: 0, expectedByHeader: 0 };
    found.set(key(pair), created);
    return created;
  };
  const mark = (nomination: Nomination, reason: NominationReason): void => void (nomination.reasons.includes(reason) || nomination.reasons.push(reason));

  // (i) 버려진 답: 받아들여진 짝에 없는 원답 가운데 양쪽이 칸 꼴의 ID 이고 두 표가 다 그 문서에 있는 것.
  for (const answer of input.answers) {
    for (const raw of answer.pairs) {
      const first = cellRef(raw.a);
      const second = cellRef(raw.b);
      if (!first || !second || first.side === second.side) continue;
      const [ra, rb] = first.side === "a" ? [first, second] : [second, first];
      if (accepted.has(`${ra.cell}|${rb.cell}`) || !withValues.a.has(ra.gridId) || !withValues.b.has(rb.gridId)) continue;
      const nomination = entry({ a: ra.gridId, b: rb.gridId });
      nomination.rejectedAnswers += 1;
      mark(nomination, "rejected_answer");
    }
  }
  // (ii) 의심이 나온 표 짝.
  for (const check of input.checks) {
    if (check.verdict !== "suspect") continue;
    const nomination = entry({ a: gridOf(check.a), b: gridOf(check.b) });
    nomination.suspects += 1;
    mark(nomination, "shift_suspect");
  }
  // (iii) 짝지어진 표 짝 가운데, 머리글로 보면 상대가 있는 남은 칸이 있는 것.
  for (const pair of derived) {
    const expected = expectedByHeader(pair, a, b, compared, units).length;
    if (expected < MIN_EXPECTED_BY_HEADER) continue;
    const nomination = entry(pair);
    nomination.expectedByHeader = expected;
    mark(nomination, "expected_by_header");
  }
  const nominations = [...found.values()].sort((x, y) => byId(key(x.pair), key(y.pair)));

  // (iv) 어느 짝에도 들지 않은 표: 끌어낸 표 짝에도, (i)~(iii) 의 지목에도 없는 표.
  const taken = { a: new Set([...derived, ...nominations.map((item) => item.pair)].map((pair) => pair.a)), b: new Set([...derived, ...nominations.map((item) => item.pair)].map((pair) => pair.b)) };
  const unpaired = { a: [...withValues.a].filter((id) => !taken.a.has(id)).sort(byId), b: [...withValues.b].filter((id) => !taken.b.has(id)).sort(byId) };
  const lines = { a: tableLines(a, units), b: tableLines(b, units) };
  const keysOf = (bag: Map<number, Set<string>>): Set<string> => new Set([...bag.values()].flatMap((keys) => [...keys]).filter((text) => text !== ""));
  const shared = (x: Set<string>, y: Set<string>): number => [...x].filter((text) => y.has(text)).length;
  const known = new Set([...derived, ...nominations.map((item) => item.pair)].map(key));
  const candidates: TableCandidate[] = [];
  for (const ga of withValues.a) {
    for (const gb of withValues.b) {
      if (known.has(`${ga}|${gb}`) || (taken.a.has(ga) && taken.b.has(gb))) continue;
      const la = lines.a.get(ga) as TableLines;
      const lb = lines.b.get(gb) as TableLines;
      const sharedRows = shared(keysOf(la.rows), keysOf(lb.rows));
      const sharedCols = shared(keysOf(la.cols), keysOf(lb.cols));
      if (sharedRows >= MIN_SHARED_ROW_HEADERS && sharedCols >= MIN_SHARED_COL_HEADERS) candidates.push({ a: ga, b: gb, sharedRows, sharedCols });
    }
  }
  candidates.sort((x, y) => y.sharedRows - x.sharedRows || y.sharedCols - x.sharedCols || byId(key(x), key(y)));
  return { nominations, derived, unpaired, candidates };
}

// ── 3. 다시 묻기: 요청 계획 ──────────────────────────────────────────────────

const TABLE_LINE = /^\[표 ([ab]:\S+)\] \d+쪽$/;

/**
 * (iv) 의 표 검수 요청의 글: 후보 표 짝에 나온 표만 실은 표 목록(방법 H 의 표 검수와 같은 꼴). 이미 짝지어진 표에는 그 상대를 표시한다.
 * 후보가 없으면 null (요청을 만들지 않는다).
 */
export function renderShortTableReview(a: Extraction, b: Extraction, candidates: TableCandidate[], paired: TablePair[]): string | null {
  if (candidates.length === 0) return null;
  const listed = { a: new Set(candidates.map((item) => item.a)), b: new Set(candidates.map((item) => item.b)) };
  const partners = new Map<string, string[]>();
  for (const pair of paired) {
    partners.set(`a:${pair.a}`, [...(partners.get(`a:${pair.a}`) ?? []), `b:${pair.b}`]);
    partners.set(`b:${pair.b}`, [...(partners.get(`b:${pair.b}`) ?? []), `a:${pair.a}`]);
  }
  const mark = (line: string): string => {
    const id = TABLE_LINE.exec(line)?.[1];
    const list = id ? partners.get(id) : undefined;
    return list ? `${line} · 이미 짝지어짐: ${list.join(", ")}` : line;
  };
  const only = (extraction: Extraction, ids: Set<string>): Extraction => ({ ...extraction, grids: extraction.grids.filter((grid) => ids.has(grid.id)) });
  return [only(a, listed.a), only(b, listed.b)].map((extraction) => renderTableList(extraction).split("\n").map(mark).join("\n")).join("\n\n");
}

/**
 * 표 검수의 답 확인. 표 목록에 실은 표여야 하고(`unknown_id`), 이미 있는 표 짝(끌어낸 것, 지목된 것)이 아니어야 하고(`already_paired`),
 * 적어도 한쪽은 짝 없는 표여야 한다(`both_tables_already_paired`). 남은 것 가운데 머리글이 많이 겹치는 것부터 `max` 개만 칸을 다시 묻는다(`over_limit`).
 */
export function acceptNewTablePairs(answer: PairTablesAnswer | null, candidates: TableCandidate[], existing: TablePair[], unpaired: { a: string[]; b: string[] }, max: number = MAX_NEW_TABLE_PAIRS): { pairs: TablePair[]; rejected: Record<string, number> } {
  if (!answer) return { pairs: [], rejected: {} };
  const listed = { a: new Set(candidates.map((item) => item.a)), b: new Set(candidates.map((item) => item.b)) };
  const checked = validateTablePairs(answer, listed.a, listed.b);
  const known = new Set(existing.map(key));
  const free = { a: new Set(unpaired.a), b: new Set(unpaired.b) };
  const rank = new Map(candidates.map((item, index) => [key(item), index]));
  const bump = (name: string): void => void (checked.rejected[name] = (checked.rejected[name] ?? 0) + 1);
  const ok: TablePair[] = [];
  for (const pair of checked.pairs) {
    if (known.has(key(pair))) bump("already_paired");
    else if (!free.a.has(pair.a) && !free.b.has(pair.b)) bump("both_tables_already_paired");
    else ok.push(pair);
  }
  ok.sort((x, y) => (rank.get(key(x)) ?? candidates.length) - (rank.get(key(y)) ?? candidates.length) || byId(key(x), key(y)));
  for (let index = max; index < ok.length; index++) bump("over_limit");
  return { pairs: ok.slice(0, max), rejected: checked.rejected };
}

export interface CellRequestPlan {
  id: string;
  pair: TablePair;
  /** 왜 이 표 짝을 묻는가. `new_table_pair` 는 (iv) 의 표 검수가 낸 표 짝이다. */
  reasons: (NominationReason | "new_table_pair")[];
  /** review: 남은 칸마다 답 하나 (`review-cells@2`). pair: 두 표의 칸 짝 전부 (`pair-cells-in-tables@1`). 이 두 표 사이에 남은 짝이 하나도 없으면 pair 다. */
  kind: "review" | "pair";
  /** review 일 때의 계획 (이미 짝지어진 칸 짝, 남은 칸). */
  review: CellReviewPlan | null;
  /** 이 두 표의 견주어지지 않은 숫자 칸 수. */
  remaining: { a: number; b: number };
}

/**
 * 지목된 표 짝마다 요청 하나를 계획한다. `kept` 는 의심 짝을 뺀 원래 짝이다. 어느 한쪽 표에 견주어지지 않은 숫자 칸이 없으면 물을 것이 없어 건너뛴다(`skipped`).
 * 남은 칸이 `maxRemaining` 을 넘는 표 짝은 칸 ID 순서로 나눠 요청을 여럿 만든다.
 */
export function planCellRequests(a: Extraction, b: Extraction, targets: { pair: TablePair; reasons: CellRequestPlan["reasons"] }[], kept: Verdict[], maxRemaining: number = MAX_REMAINING_PER_REQUEST): { plans: CellRequestPlan[]; skipped: { pair: TablePair; reasons: CellRequestPlan["reasons"]; why: "nothing_left" }[] } {
  const facts = { a: cellFacts(a), b: cellFacts(b) };
  const compared = { a: new Set(kept.map((verdict) => verdict.a)), b: new Set(kept.map((verdict) => verdict.b)) };
  const order = (x: Fact, y: Fact): number => (x.where.row as number) - (y.where.row as number) || (x.where.col as number) - (y.where.col as number);
  const left = (side: "a" | "b", gridId: string): Fact[] => [...facts[side].values()].filter((fact) => fact.where.gridId === gridId && !compared[side].has(fact.id)).sort(order);
  const plans: CellRequestPlan[] = [];
  const skipped: { pair: TablePair; reasons: CellRequestPlan["reasons"]; why: "nothing_left" }[] = [];
  for (const target of targets) {
    const leftA = left("a", target.pair.a);
    const leftB = left("b", target.pair.b);
    if (leftA.length === 0 || leftB.length === 0) {
      skipped.push({ pair: target.pair, reasons: target.reasons, why: "nothing_left" });
      continue;
    }
    const paired = kept.filter((verdict) => facts.a.has(verdict.a) && facts.b.has(verdict.b) && gridOf(verdict.a) === target.pair.a && gridOf(verdict.b) === target.pair.b).map((verdict) => ({ a: verdict.a, b: verdict.b }));
    const remaining = { a: leftA.length, b: leftB.length };
    const name = `${target.pair.a}|${target.pair.b}`;
    if (paired.length === 0) {
      plans.push({ id: `r/pair/${name}`, pair: target.pair, reasons: target.reasons, kind: "pair", review: null, remaining });
      continue;
    }
    const list = [...leftA.map((fact) => `a:${fact.id}`), ...leftB.map((fact) => `b:${fact.id}`)];
    const parts = Math.ceil(list.length / maxRemaining);
    for (let index = 0; index < parts; index++) {
      const id = `r/cells/${name}${parts > 1 ? `#${index + 1}` : ""}`;
      plans.push({ id, pair: target.pair, reasons: target.reasons, kind: "review", review: { id, pair: target.pair, paired, remaining: list.slice(index * maxRemaining, (index + 1) * maxRemaining) }, remaining });
    }
  }
  return { plans, skipped };
}

// ── 4. 합치기와 셈 ──────────────────────────────────────────────────────────

export interface RecoveredPair extends Verdict {
  /** 이 짝이 나온 요청. */
  request: string;
  tables: TablePair;
}

export interface MergeResult {
  /** 마지막 결과: 의심을 뺀 원래 짝 + 의심을 뺀 다시 받은 짝 (같은 짝은 한 번). */
  verdicts: Verdict[];
  /** 다시 받은 짝 가운데 원래 답에 없던 것 (의심을 뺀 것). */
  added: RecoveredPair[];
  /** 의심으로 걸려 빠진 짝. `from` 은 어디서 온 짝인가. */
  dropped: (Verdict & { from: "original" | "recovered" })[];
  /** 다시 받은 짝 가운데 원래 답에 이미 있던 것의 수. */
  repeated: number;
  /** 한 칸이 같은 표 짝 안에서 서로 다른 상대와 짝지어진 것 (원래 짝과 새 짝이 엇갈린 것). 둘 다 남긴다. */
  conflicts: { cell: string; pairs: string[] }[];
}

/**
 * 합치는 규칙. (1) 의심으로 걸린 짝은 원래 것이든 다시 받은 것이든 견준 결과에 넣지 않는다. (2) 같은 짝은 한 번만. (3) 원래 짝과 다시 받은 짝이 한 칸을 서로 다른 상대와 짝지었으면 둘 다 남기고 센다
 * (그 칸의 결과는 가장 무거운 것이 된다. 어느 쪽이 맞는지 코드는 모른다).
 */
export function mergeRecovered(original: Verdict[], recovered: RecoveredPair[], suspects: Set<string>): MergeResult {
  const out: MergeResult = { verdicts: [], added: [], dropped: [], repeated: 0, conflicts: [] };
  const seen = new Set<string>();
  for (const verdict of original) {
    if (seen.has(key(verdict))) continue;
    seen.add(key(verdict));
    if (suspects.has(key(verdict))) out.dropped.push({ ...verdict, from: "original" });
    else out.verdicts.push(verdict);
  }
  for (const pair of recovered) {
    if (seen.has(key(pair))) {
      out.repeated += 1;
      continue;
    }
    seen.add(key(pair));
    const plain: Verdict = { a: pair.a, b: pair.b, outcome: pair.outcome, reason: pair.reason, valuesDiffer: pair.valuesDiffer };
    if (suspects.has(key(pair))) out.dropped.push({ ...plain, from: "recovered" });
    else {
      out.verdicts.push(plain);
      out.added.push(pair);
    }
  }
  const tables = new Set(out.added.map((pair) => key(pair.tables)));
  const partners = { a: new Map<string, string[]>(), b: new Map<string, string[]>() };
  for (const verdict of out.verdicts) {
    if (!tables.has(`${gridOf(verdict.a)}|${gridOf(verdict.b)}`)) continue;
    partners.a.set(`${verdict.a}→${gridOf(verdict.b)}`, [...(partners.a.get(`${verdict.a}→${gridOf(verdict.b)}`) ?? []), key(verdict)]);
    partners.b.set(`${verdict.b}→${gridOf(verdict.a)}`, [...(partners.b.get(`${verdict.b}→${gridOf(verdict.a)}`) ?? []), key(verdict)]);
  }
  for (const side of ["a", "b"] as const) for (const [cell, pairs] of partners[side]) if (pairs.length > 1) out.conflicts.push({ cell: `${side}:${cell.split("→")[0]}`, pairs });
  return out;
}

/** 값 후보 하나의 마지막 결과. */
export type RecoverState =
  | "compared:match"
  | "compared:mismatch"
  | "compared:needs_explanation"
  | "suspect:shifted" // 의심으로 걸린 짝에만 든 칸 (견준 결과에 넣지 않았다)
  | `asked:no_counterpart:${string}` // 다시 물었고 「상대 없음」이라 답했다
  | "asked:undecidable"
  | "asked:unanswered" // 다시 물었는데 답이 오지 않았다
  | "asked:answer_rejected"
  | "asked:not_paired" // 칸 짝 요청에 실린 표의 칸인데 짝이 나오지 않았다
  | "left:in_paired_table" // 짝지어진 표 안의 남은 칸 (다시 묻지 않았다)
  | "unverified:answer_rejected"
  | "unverified:table_not_paired"
  | `unverified:outside_tables:${string}`;

const SEVERITY: Record<Verdict["outcome"], number> = { mismatch: 3, needs_explanation: 2, match: 1 };

function combineAsked(answers: CellReview2State[]): RecoverState {
  if (answers.some((item) => item.state === "unanswered")) return "asked:unanswered";
  if (answers.some((item) => item.state === "answer_rejected")) return "asked:answer_rejected";
  if (answers.some((item) => item.state === "undecidable")) return "asked:undecidable";
  const reasons = [...new Set(answers.flatMap((item) => (item.state === "no_counterpart" ? [item.reason] : [])))].sort();
  // 「짝지었다」는 답인데 견준 결과에 없는 칸은 그 짝이 의심으로 빠진 것이다.
  return reasons.length > 0 ? `asked:no_counterpart:${reasons.join("+")}` : "suspect:shifted";
}

export interface RecoverTally {
  total: number;
  states: Record<string, number>;
}

/**
 * 한 문서의 모든 값 후보에 결과를 하나씩 붙이고 센다. 합이 값 후보 수와 다르면 예외다.
 * 우선순위: 견줌 > 표의 숫자 칸이 아님 > 다시 물은 칸의 답 > 칸 짝 요청에 실렸지만 짝 없음 > 의심으로 빠진 짝의 칸 > 짝지어진 표 안의 남은 칸 > 버려진 답 > 짝이 없는 표의 칸.
 */
export function classifyRecovered(extraction: Extraction, side: "a" | "b", input: { verdicts: Verdict[]; dropped: { a: string; b: string }[]; asked: Map<string, CellReview2State[]>; pairRequestTables: Set<string>; pairedTables: Set<string>; rejectedPlaces: Set<string> }): { states: Map<string, RecoverState>; tally: RecoverTally } {
  const worst = new Map<string, Verdict["outcome"]>();
  for (const verdict of input.verdicts) {
    const before = worst.get(verdict[side]);
    if (!before || SEVERITY[verdict.outcome] > SEVERITY[before]) worst.set(verdict[side], verdict.outcome);
  }
  const dropped = new Set(input.dropped.map((pair) => pair[side]));
  const states = new Map<string, RecoverState>();
  for (const fact of extraction.facts) {
    const compared = worst.get(fact.id);
    const answers = input.asked.get(fact.id);
    const gridId = fact.where.gridId as string;
    let state: RecoverState;
    if (compared) state = `compared:${compared}`;
    else if (fact.bucket !== "table_cell") state = `unverified:outside_tables:${fact.bucket}`;
    else if (answers) state = combineAsked(answers);
    else if (input.pairRequestTables.has(gridId)) state = "asked:not_paired";
    else if (dropped.has(fact.id)) state = "suspect:shifted";
    else if (input.pairedTables.has(gridId)) state = "left:in_paired_table";
    else if (input.rejectedPlaces.has(fact.id)) state = "unverified:answer_rejected";
    else state = "unverified:table_not_paired";
    states.set(fact.id, state);
  }
  const tally: RecoverTally = { total: extraction.facts.length, states: {} };
  for (const state of states.values()) tally.states[state] = (tally.states[state] ?? 0) + 1;
  const sum = Object.values(tally.states).reduce((x, y) => x + y, 0);
  if (sum !== tally.total) throw new TallyError(`값 후보 ${tally.total}개 중 결과가 붙은 것이 ${sum}개다. 합이 맞지 않아 수를 내지 않는다`);
  return { states, tally };
}

export interface RecoverInput {
  a: Extraction;
  b: Extraction;
  units: UnitTable;
  /** 원래 답의 받아들여진 짝과 버려진 답이 가리킨 자리. */
  original: { verdicts: Verdict[]; rejectedPlaces: { a: Set<string>; b: Set<string> } };
  plans: CellRequestPlan[];
  rule?: ShiftRule;
  /** 요청마다의 답의 판정 (요청 ID → 칸 검수의 판정 또는 칸 짝의 판정). 요청이 실패했으면 없다. */
  reviews: Map<string, CellReview2Judgement>;
  pairs: Map<string, CellJudgement>;
}

export interface RecoverResult {
  /** 원래 답 그대로. */
  before: { verdicts: Verdict[]; tally: { a: RecoverTally; b: RecoverTally } };
  /** 열 밀림 검사만 한 것 (호출 0): 의심 짝을 뺀 원래 답. */
  checked: { verdicts: Verdict[]; dropped: Verdict[]; tally: { a: RecoverTally; b: RecoverTally } };
  /** 다시 물은 뒤. */
  after: { verdicts: Verdict[]; tally: { a: RecoverTally; b: RecoverTally }; states: { a: Map<string, RecoverState>; b: Map<string, RecoverState> } };
  merge: MergeResult;
  /** 다시 받은 짝의 열 밀림 검사. */
  recoveredChecks: ShiftCheck[];
  /** 다시 물은 뒤에도 머리글로 보면 상대가 있는 남은 칸 (다시 물은 표 짝마다). 완결 기준을 코드가 쥔다는 것의 셈이다. */
  stillExpected: { pair: TablePair; count: number }[];
  rejected: Record<string, number>;
}

/** 방법 R 의 결과: 원래 답, 검사만 한 것, 다시 물은 뒤의 셋을 같은 셈으로 낸다. */
export function recoverOutcome(input: RecoverInput): RecoverResult {
  const { a, b, units, original, plans } = input;
  const facts = { a: cellFacts(a), b: cellFacts(b) };
  const cellsOnly = (verdicts: Verdict[]): Verdict[] => verdicts.filter((verdict) => facts.a.has(verdict.a) && facts.b.has(verdict.b));
  const tablesOf = (verdicts: Verdict[]): { a: Set<string>; b: Set<string> } => {
    const pairs = deriveTablePairs(cellsOnly(verdicts));
    return { a: new Set(pairs.map((pair) => pair.a)), b: new Set(pairs.map((pair) => pair.b)) };
  };
  const none = { asked: { a: new Map<string, CellReview2State[]>(), b: new Map<string, CellReview2State[]>() }, tables: { a: new Set<string>(), b: new Set<string>() } };
  const classify = (verdicts: Verdict[], dropped: { a: string; b: string }[], extra: typeof none): { a: ReturnType<typeof classifyRecovered>; b: ReturnType<typeof classifyRecovered> } => {
    const paired = tablesOf(verdicts);
    const one = (side: "a" | "b"): ReturnType<typeof classifyRecovered> => classifyRecovered(side === "a" ? a : b, side, { verdicts, dropped, asked: extra.asked[side], pairRequestTables: extra.tables[side], pairedTables: paired[side], rejectedPlaces: original.rejectedPlaces[side] });
    return { a: one("a"), b: one("b") };
  };

  const originalChecks = checkShifts(original.verdicts, a, b, units, input.rule);
  const before = classify(original.verdicts, [], none);

  const recovered: RecoveredPair[] = [];
  const rejected: Record<string, number> = {};
  const bump = (name: string, by: number): void => void (rejected[name] = (rejected[name] ?? 0) + by);
  const extra = { asked: { a: new Map<string, CellReview2State[]>(), b: new Map<string, CellReview2State[]>() }, tables: { a: new Set<string>(), b: new Set<string>() } };
  for (const plan of plans) {
    if (plan.kind === "review") {
      const judgement = input.reviews.get(plan.id);
      for (const cell of plan.review?.remaining ?? []) {
        const side = cell.slice(0, 1) as "a" | "b";
        const answer: CellReview2State = judgement?.cells.get(cell) ?? { state: "unanswered" };
        extra.asked[side].set(cell.slice(2), [...(extra.asked[side].get(cell.slice(2)) ?? []), answer]);
      }
      for (const pair of judgement?.newPairs ?? []) recovered.push({ a: pair.a, b: pair.b, outcome: pair.outcome, reason: pair.reason, valuesDiffer: pair.valuesDiffer, request: plan.id, tables: plan.pair });
      for (const [reason, count] of Object.entries(judgement?.rejected ?? {})) bump(`review:${reason}`, count);
    } else {
      const judgement = input.pairs.get(plan.id);
      extra.tables.a.add(plan.pair.a);
      extra.tables.b.add(plan.pair.b);
      for (const verdict of judgement?.verdicts ?? []) recovered.push({ ...verdict, request: plan.id, tables: plan.pair });
      for (const [reason, count] of Object.entries(judgement?.rejected ?? {})) bump(`pair:${reason}`, count);
    }
  }
  const recoveredChecks = checkShifts(recovered, a, b, units, input.rule);
  const suspects = new Set([...suspectKeys(originalChecks), ...suspectKeys(recoveredChecks)]);
  const checkedMerge = mergeRecovered(original.verdicts, [], suspects);
  const checked = classify(checkedMerge.verdicts, checkedMerge.dropped, none);
  const merge = mergeRecovered(original.verdicts, recovered, suspects);
  const after = classify(merge.verdicts, merge.dropped, extra);
  const compared = { a: new Set(merge.verdicts.map((verdict) => verdict.a)), b: new Set(merge.verdicts.map((verdict) => verdict.b)) };
  const asked = new Map(plans.map((plan) => [key(plan.pair), plan.pair]));
  const stillExpected = [...asked.values()].map((pair) => ({ pair, count: expectedByHeader(pair, a, b, compared, units).length }));
  return {
    before: { verdicts: original.verdicts, tally: { a: before.a.tally, b: before.b.tally } },
    checked: { verdicts: checkedMerge.verdicts, dropped: checkedMerge.dropped, tally: { a: checked.a.tally, b: checked.b.tally } },
    after: { verdicts: merge.verdicts, tally: { a: after.a.tally, b: after.b.tally }, states: { a: after.a.states, b: after.b.states } },
    merge,
    recoveredChecks,
    stillExpected,
    rejected,
  };
}
