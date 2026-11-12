import { z } from "zod";
import type { DocModel, Extraction, Fact } from "../domain/facts.js";
import { scanNumbers } from "../domain/notation.js";
import type { UnitTable } from "../domain/units.js";
import { TallyError, renderCellPairRequest, unmaskedValues, type CellJudgement, type TablePair } from "./hybrid.js";
import { compareCells, judgePairs, validateTablePairs, type DocContext, type MethodOutcome, type PairCellsAnswer, type PairTablesAnswer, type Verdict } from "./methods.js";
import { renderTableList } from "./render.js";

// 방법 H (MARC-TICK-23): 방법 D 의 답(칸 짝) 뒤에 코드의 셈과 좁은 검수를 붙인다.
//  1. 코드의 셈 (AI 없음): D 의 칸 짝에서 「표 짝」을 끌어내고, 두 문서의 모든 값 후보를 결과 하나에 넣는다.
//  2. 칸 검수 (AI): 남은 칸이 있는 표 짝마다 요청 하나. 남은 칸마다 상대 칸의 ID 나 「상대 없음」의 사유를 받는다.
//  3. 표 검수 (AI): 짝이 없는 표 가운데 같은 내용을 실은 표가 더 있는지 표 ID 의 짝으로 받는다. 새 표 짝의 칸 짝은 방법 G 의 요청 그대로.
// 요청은 D 의 답과 문서에서만 조립한다 (이 파일은 정답 쌍·심은 오류 명세를 읽지 않는다). 값 비교는 코드(`compareValues`)다. 답 스키마에 값 필드가 없다.

export const PROMPT_REVIEW_CELLS = "reconcile/review-cells@1";
export const PROMPT_REVIEW_TABLES = "reconcile/review-tables@1";

/** 두 표 사이에 D 의 칸 짝이 이만큼 있으면 「짝지어진 표」다. */
export const TABLE_PAIR_MIN_CELL_PAIRS = 1;
/** 칸 검수 요청 하나에 싣는 남은 칸의 최대 수. 넘으면 남은 칸의 목록을 칸 ID 순서로 나눠 요청을 여럿 만든다 (표는 같이 싣는다). */
export const MAX_REMAINING_PER_REQUEST = 150;
/** 남은 칸 하나의 답의 출력 어림(가정). */
export const REVIEW_ANSWER_TOKENS = 60;

const key = (verdict: { a: string; b: string }): string => `${verdict.a}|${verdict.b}`;
const gridOf = (cell: string): string => cell.slice(0, cell.lastIndexOf(":"));
const cellFacts = (extraction: Extraction): Map<string, Fact> => new Map(extraction.facts.filter((fact) => fact.bucket === "table_cell").map((fact) => [fact.id, fact]));
const squash = (text: string): string => text.normalize("NFKC").replace(/\s+/g, "");
const bump = (bag: Record<string, number>, name: string, by = 1): void => void (bag[name] = (bag[name] ?? 0) + by);
const byCell = (x: Fact, y: Fact): number => (x.where.row as number) - (y.where.row as number) || (x.where.col as number) - (y.where.col as number);

// ── 1. D 의 답과 코드의 셈 ───────────────────────────────────────────────────

export interface BaseResult {
  outcome: MethodOutcome;
  /** D 의 버려진 답이 가리킨 칸 가운데 그 문서에 실제로 있는 숫자 칸. */
  rejectedCells: { a: Set<string>; b: Set<string> };
}

/** 저장된 D 의 답(요청 조각마다 하나)을 D 와 같은 확인(`judgePairs`)에 넣고 조각을 합친다. 같은 칸 짝은 한 번만. */
export function baseFromPairAnswers(answers: PairCellsAnswer[], a: DocContext, b: DocContext, units: UnitTable): BaseResult {
  const outcome: MethodOutcome = { verdicts: [], intra: [], states: { a: {}, b: {} }, rejected: {} };
  const rejectedCells = { a: new Set<string>(), b: new Set<string>() };
  const factsA = cellFacts(a.extraction);
  const factsB = cellFacts(b.extraction);
  const seen = new Set<string>();
  for (const answer of answers) {
    const part = judgePairs(answer, a, b, units);
    const accepted = new Set(part.verdicts.map(key));
    for (const verdict of part.verdicts) {
      if (seen.has(key(verdict))) continue;
      seen.add(key(verdict));
      outcome.verdicts.push(verdict);
    }
    for (const [reason, count] of Object.entries(part.rejected)) bump(outcome.rejected, reason, count);
    for (const raw of answer.pairs) {
      if (accepted.has(key(raw))) continue;
      if (factsA.has(raw.a)) rejectedCells.a.add(raw.a);
      if (factsB.has(raw.b)) rejectedCells.b.add(raw.b);
    }
  }
  return { outcome, rejectedCells };
}

export interface DerivedTablePair extends TablePair {
  /** 이 두 표 사이의 칸 짝 수. */
  cellPairs: number;
}

/** 칸 짝에서 표 짝을 끌어낸다: 두 표 사이의 칸 짝이 문턱 이상이면 짝지어진 표다. */
export function deriveTablePairs(verdicts: { a: string; b: string }[], minCellPairs: number = TABLE_PAIR_MIN_CELL_PAIRS): DerivedTablePair[] {
  const count = new Map<string, DerivedTablePair>();
  for (const verdict of verdicts) {
    const pair = { a: gridOf(verdict.a), b: gridOf(verdict.b) };
    const entry = count.get(key(pair)) ?? { ...pair, cellPairs: 0 };
    entry.cellPairs += 1;
    count.set(key(pair), entry);
  }
  return [...count.values()].filter((pair) => pair.cellPairs >= minCellPairs).sort((x, y) => key(x).localeCompare(key(y), "en", { numeric: true }));
}

/** 남은 칸에 대한 「상대 없음」의 사유. */
export const NO_COUNTERPART_REASONS = ["no_such_row_or_column", "different_item"] as const;
export type NoCounterpartReason = (typeof NO_COUNTERPART_REASONS)[number];

/** 값 후보 하나의 결과. 검수 전에는 `left:`, 검수 뒤에는 `reviewed:` 갈래가 쓰인다. */
export type ReviewState =
  | "compared:match"
  | "compared:mismatch"
  | "compared:needs_explanation"
  | "left:in_paired_table" // 검수 전: 짝지어진 표 안의 남은 칸
  | `reviewed:no_counterpart:${string}` // 검수가 「상대 없음」이라 했다 (사유. 요청마다 사유가 다르면 `+` 로 잇는다)
  | "reviewed:undecidable" // 검수가 가를 수 없다고 했다
  | "reviewed:unanswered" // 검수의 답이 오지 않았다 (요청 실패 포함)
  | "reviewed:answer_rejected" // 검수의 답이 버려졌다 (없는 ID, 두 표 밖의 칸, 원문에 없는 단위 글자, 엇갈린 답)
  | "left:in_newly_paired_table" // 표 검수가 새로 짝지은 표 안인데 칸 짝이 나오지 않았다
  | "unverified:answer_rejected" // 짝이 없는 표의 칸인데 이 칸을 가리킨 답이 버려졌다
  | "unverified:table_not_paired" // 짝이 없는 표의 칸
  | `unverified:outside_tables:${string}`; // 표의 숫자 칸이 아니다 (문장 속, 숫자뿐인 블록, 표의 글 칸 속)

const SEVERITY: Record<Verdict["outcome"], number> = { mismatch: 3, needs_explanation: 2, match: 1 };

function worstByCell(verdicts: Verdict[], side: "a" | "b"): Map<string, Verdict["outcome"]> {
  const worst = new Map<string, Verdict["outcome"]>();
  for (const verdict of verdicts) {
    const before = worst.get(verdict[side]);
    if (!before || SEVERITY[verdict.outcome] > SEVERITY[before]) worst.set(verdict[side], verdict.outcome);
  }
  return worst;
}

/**
 * 검수 전의 셈: 한 문서의 모든 값 후보에 결과를 하나씩 붙인다.
 * 우선순위: 견줌 > 표의 숫자 칸이 아님 > 짝지어진 표 안의 남은 칸 > 버려진 답이 가리킨 칸 > 짝이 없는 표의 칸.
 * 한 칸이 여러 짝에서 견주어졌으면 가장 무거운 결과(불일치 > 설명 필요 > 일치)가 그 칸의 결과다.
 */
export function classifyBefore(extraction: Extraction, side: "a" | "b", pairedTables: Set<string>, verdicts: Verdict[], rejectedCells: Set<string>): Map<string, ReviewState> {
  const worst = worstByCell(verdicts, side);
  const states = new Map<string, ReviewState>();
  for (const fact of extraction.facts) {
    const compared = worst.get(fact.id);
    if (compared) states.set(fact.id, `compared:${compared}`);
    else if (fact.bucket !== "table_cell") states.set(fact.id, `unverified:outside_tables:${fact.bucket}`);
    else if (pairedTables.has(fact.where.gridId as string)) states.set(fact.id, "left:in_paired_table");
    else if (rejectedCells.has(fact.id)) states.set(fact.id, "unverified:answer_rejected");
    else states.set(fact.id, "unverified:table_not_paired");
  }
  return states;
}

export interface ReviewTally {
  /** 그 문서의 값 후보 수 (분모). */
  total: number;
  compared: { match: number; mismatch: number; needsExplanation: number };
  /** 검수 전: 짝지어진 표 안의 남은 칸. */
  leftInPairedTable: number;
  /** 검수가 「상대 없음」이라 한 칸 (사유별). */
  noCounterpart: Record<string, number>;
  /** 검수가 가르지 못한 칸. */
  undecided: { undecidable: number; unanswered: number; answerRejected: number };
  leftInNewlyPairedTable: number;
  answerRejected: number;
  tableNotPaired: number;
  outsideTables: Record<string, number>;
}

/** 결과별 수. 합이 값 후보 수와 다르면 수를 내지 않고 예외를 낸다. */
export function tallyReview(total: number, states: Iterable<ReviewState>): ReviewTally {
  const tally: ReviewTally = { total, compared: { match: 0, mismatch: 0, needsExplanation: 0 }, leftInPairedTable: 0, noCounterpart: {}, undecided: { undecidable: 0, unanswered: 0, answerRejected: 0 }, leftInNewlyPairedTable: 0, answerRejected: 0, tableNotPaired: 0, outsideTables: {} };
  for (const state of states) {
    if (state === "compared:match") tally.compared.match += 1;
    else if (state === "compared:mismatch") tally.compared.mismatch += 1;
    else if (state === "compared:needs_explanation") tally.compared.needsExplanation += 1;
    else if (state === "left:in_paired_table") tally.leftInPairedTable += 1;
    else if (state === "reviewed:undecidable") tally.undecided.undecidable += 1;
    else if (state === "reviewed:unanswered") tally.undecided.unanswered += 1;
    else if (state === "reviewed:answer_rejected") tally.undecided.answerRejected += 1;
    else if (state === "left:in_newly_paired_table") tally.leftInNewlyPairedTable += 1;
    else if (state === "unverified:answer_rejected") tally.answerRejected += 1;
    else if (state === "unverified:table_not_paired") tally.tableNotPaired += 1;
    else if (state.startsWith("reviewed:no_counterpart:")) bump(tally.noCounterpart, state.slice("reviewed:no_counterpart:".length));
    else if (state.startsWith("unverified:outside_tables:")) bump(tally.outsideTables, state.slice("unverified:outside_tables:".length));
    else throw new TallyError(`모르는 결과 갈래: ${state}`);
  }
  const bag = (counts: Record<string, number>): number => Object.values(counts).reduce((x, y) => x + y, 0);
  const sum = tally.compared.match + tally.compared.mismatch + tally.compared.needsExplanation + tally.leftInPairedTable + bag(tally.noCounterpart) + tally.undecided.undecidable + tally.undecided.unanswered + tally.undecided.answerRejected + tally.leftInNewlyPairedTable + tally.answerRejected + tally.tableNotPaired + bag(tally.outsideTables);
  if (sum !== total) throw new TallyError(`값 후보 ${total}개 중 결과가 붙은 것이 ${sum}개다. 합이 맞지 않아 수를 내지 않는다`);
  return tally;
}

// ── 2. 칸 검수 ───────────────────────────────────────────────────────────────

export const reviewCellsSchema = z.object({
  answers: z.array(
    z.object({
      /** 남은 칸의 ID (`a:표ID:r행c열` 꼴). */
      cell: z.string(),
      /** 상대 표의 칸 ID. 상대가 없으면 null. */
      counterpart: z.string().nullable(),
      reason: z.enum([...NO_COUNTERPART_REASONS, "undecidable"]).nullable(),
      /** 원문의 단위 글자 그대로. */
      unit: z.string().nullable(),
      counterpart_unit: z.string().nullable(),
    }),
  ),
});
export type ReviewCellsAnswer = z.infer<typeof reviewCellsSchema>;

export interface CellReviewPlan {
  /** 요청 단위의 ID. */
  id: string;
  pair: TablePair;
  /** 이 두 표 사이에서 이미 짝지어진 칸 짝. */
  paired: { a: string; b: string }[];
  /** 이 요청이 묻는 남은 칸 (`a:` · `b:` 를 붙인 ID). */
  remaining: string[];
}

/**
 * 칸 검수의 요청 계획: 남은 칸(짝지어진 표의 숫자 칸 가운데 어느 짝에서도 견주어지지 않은 칸)이 있는 표 짝마다 요청 하나.
 * 남은 칸이 `maxRemaining` 을 넘으면 목록을 문서 a 의 칸, 문서 b 의 칸 순서(행, 열 순)로 늘어놓고 앞에서부터 잘라 요청을 여럿 만든다.
 */
export function planCellReviews(a: Extraction, b: Extraction, tablePairs: TablePair[], verdicts: { a: string; b: string }[], maxRemaining: number = MAX_REMAINING_PER_REQUEST): CellReviewPlan[] {
  const compared = { a: new Set(verdicts.map((verdict) => verdict.a)), b: new Set(verdicts.map((verdict) => verdict.b)) };
  const left = (extraction: Extraction, side: "a" | "b", gridId: string): string[] =>
    [...cellFacts(extraction).values()]
      .filter((fact) => fact.where.gridId === gridId && !compared[side].has(fact.id))
      .sort(byCell)
      .map((fact) => `${side}:${fact.id}`);
  const plans: CellReviewPlan[] = [];
  for (const pair of tablePairs) {
    const remaining = [...left(a, "a", pair.a), ...left(b, "b", pair.b)];
    if (remaining.length === 0) continue;
    const paired = verdicts.filter((verdict) => gridOf(verdict.a) === pair.a && gridOf(verdict.b) === pair.b).map((verdict) => ({ a: verdict.a, b: verdict.b }));
    const parts = Math.ceil(remaining.length / maxRemaining);
    for (let index = 0; index < parts; index++) plans.push({ id: `h/cells/${pair.a}|${pair.b}${parts > 1 ? `#${index + 1}` : ""}`, pair: { a: pair.a, b: pair.b }, paired, remaining: remaining.slice(index * maxRemaining, (index + 1) * maxRemaining) });
  }
  return plans;
}

const LISTS_MARK = "\n\n# 이미 짝지어진 칸 (앞 단계의 답, 참고)\n";

/** 칸 검수 요청의 글: 방법 G 의 칸 짝 요청과 같은 두 표(값 후보는 가림) 뒤에, 이미 짝지어진 칸 짝과 남은 칸의 ID 목록. */
export function renderCellReviewRequest(a: { document: DocModel; extraction: Extraction }, b: { document: DocModel; extraction: Extraction }, plan: CellReviewPlan): string {
  const paired = plan.paired.map((pair) => `a:${pair.a} = b:${pair.b}`);
  return `${renderCellPairRequest(a, b, plan.pair)}${LISTS_MARK}${paired.join("\n") || "(없음)"}\n\n# 남은 칸 (칸마다 답 하나)\n${plan.remaining.join("\n")}`;
}

/**
 * 칸 검수 요청의 글에 남은 값 후보 숫자. 두 표의 글은 방법 G 의 확인(`unmaskedValues`)을 그대로 쓴다.
 * 그 뒤의 두 목록은 칸 ID 만 실려야 한다 (문서의 글자가 섞이면 그 줄을 그대로 낸다).
 */
export function unmaskedInCellReview(requestText: string): string[] {
  const at = requestText.indexOf(LISTS_MARK);
  if (at < 0) return ["(목록 구획이 없다)"];
  const cell = String.raw`[ab]:\S+:r\d+c\d+`;
  const line = new RegExp(`^(# 남은 칸 \\(칸마다 답 하나\\)|\\(없음\\)|${cell}( = ${cell})?|)$`);
  return [...unmaskedValues(requestText.slice(0, at)), ...requestText.slice(at + LISTS_MARK.length).split("\n").filter((text) => !line.test(text))];
}

/** 남은 칸 하나에 대한 검수의 답 (요청 하나 안에서). */
export type CellReviewState = { state: "paired"; counterpart: string } | { state: "no_counterpart"; reason: NoCounterpartReason } | { state: "undecidable" } | { state: "unanswered" } | { state: "answer_rejected"; why: string };

export interface CellReviewJudgement {
  plan: CellReviewPlan;
  /** 검수가 새로 낸 짝을 코드가 견준 결과. `from` 은 그 짝을 답한 남은 칸. */
  newPairs: (Verdict & { from: string[] })[];
  /** 이 요청의 남은 칸마다 하나. */
  cells: Map<string, CellReviewState>;
  /** 버려진 답의 수 (사유별). */
  rejected: Record<string, number>;
}

/**
 * 칸 검수의 답 확인. 남은 칸 목록에 없는 칸에 대한 답은 버린다 (없는 ID 는 `unknown_id`, 있는 칸이지만 목록 밖은 `not_in_remaining_list`).
 * 상대 칸은 그 문서에 있는 숫자 칸이어야 하고(`unknown_id`), 그 요청의 상대 표의 칸이어야 하고(`outside_request_tables`), 단위 글자는 그 쪽에 있어야 한다(`unit_not_in_source`).
 * 한 칸에 답이 둘 이상이면 글자까지 같을 때만 하나로 치고(`duplicate_answer`), 다르면 전부 버린다(`conflicting_answers`).
 * 답이 안 온 남은 칸은 「미응답」이다. 「상대 없음」으로 세지 않는다.
 */
export function judgeCellReview(answer: ReviewCellsAnswer | null, plan: CellReviewPlan, a: DocContext, b: DocContext, units: UnitTable): CellReviewJudgement {
  const out: CellReviewJudgement = { plan, newPairs: [], cells: new Map(plan.remaining.map((cell) => [cell, { state: "unanswered" }])), rejected: {} };
  if (!answer) return out;
  const facts = { a: cellFacts(a.extraction), b: cellFacts(b.extraction) };
  const docs = { a, b };
  const parse = (id: string): { side: "a" | "b"; fact: Fact } | null => {
    const side = id.startsWith("a:") ? "a" : id.startsWith("b:") ? "b" : null;
    const fact = side ? facts[side].get(id.slice(2)) : undefined;
    return side && fact ? { side, fact } : null;
  };
  const unitOk = (unit: string | null, side: "a" | "b", fact: Fact): boolean => unit === null || squash(docs[side].pages.get(fact.where.page ?? -1) ?? "").includes(squash(unit)) || squash(fact.unitSuffix ?? "") === squash(unit);
  const remaining = new Set(plan.remaining);
  const answers = new Map<string, ReviewCellsAnswer["answers"]>();
  for (const raw of answer.answers) {
    if (!remaining.has(raw.cell)) bump(out.rejected, parse(raw.cell) ? "not_in_remaining_list" : "unknown_id");
    else answers.set(raw.cell, [...(answers.get(raw.cell) ?? []), raw]);
  }
  const found = new Map<string, Verdict & { from: string[] }>();
  for (const [cell, list] of answers) {
    const reject = (why: string, count = 1): void => {
      bump(out.rejected, why, count);
      out.cells.set(cell, { state: "answer_rejected", why });
    };
    if (new Set(list.map((item) => JSON.stringify(item))).size > 1) {
      reject("conflicting_answers", list.length);
      continue;
    }
    if (list.length > 1) bump(out.rejected, "duplicate_answer", list.length - 1);
    const raw = list[0] as ReviewCellsAnswer["answers"][number];
    const self = parse(cell) as { side: "a" | "b"; fact: Fact };
    if (raw.counterpart === null) {
      if (raw.reason === null) reject("neither_counterpart_nor_reason");
      else out.cells.set(cell, raw.reason === "undecidable" ? { state: "undecidable" } : { state: "no_counterpart", reason: raw.reason });
      continue;
    }
    const other = parse(raw.counterpart);
    if (!other) reject("unknown_id");
    else if (other.side === self.side || other.fact.where.gridId !== plan.pair[other.side]) reject("outside_request_tables");
    else if (!unitOk(raw.unit, self.side, self.fact) || !unitOk(raw.counterpart_unit, other.side, other.fact)) reject("unit_not_in_source");
    else {
      const [fa, ua, fb, ub] = self.side === "a" ? [self.fact, raw.unit, other.fact, raw.counterpart_unit] : [other.fact, raw.counterpart_unit, self.fact, raw.unit];
      const entry = found.get(`${fa.id}|${fb.id}`);
      if (entry) entry.from.push(cell);
      else {
        const result = compareCells({ fact: fa, unit: ua }, { fact: fb, unit: ub }, units);
        found.set(`${fa.id}|${fb.id}`, { a: fa.id, b: fb.id, outcome: result.outcome as Verdict["outcome"], reason: result.reason, valuesDiffer: result.valuesDiffer, from: [cell] });
      }
      out.cells.set(cell, { state: "paired", counterpart: raw.counterpart });
    }
  }
  out.newPairs = [...found.values()];
  return out;
}

// ── 3. 표 검수 ───────────────────────────────────────────────────────────────

const TABLE_LINE = /^\[표 ([ab]:\S+)\] \d+쪽$/;
const PAIRED_NOTE = " · 이미 짝지어짐: ";

/** 표 검수 요청의 글: 방법 F 의 표 목록(숫자 칸은 싣지 않고 글 속의 값 후보는 가림)에, 이미 짝지어진 표와 그 상대를 표시한다. */
export function renderTableReviewRequest(a: Extraction, b: Extraction, tablePairs: TablePair[]): string {
  const partners = new Map<string, string[]>();
  for (const pair of tablePairs) {
    partners.set(`a:${pair.a}`, [...(partners.get(`a:${pair.a}`) ?? []), `b:${pair.b}`]);
    partners.set(`b:${pair.b}`, [...(partners.get(`b:${pair.b}`) ?? []), `a:${pair.a}`]);
  }
  const mark = (line: string): string => {
    const id = TABLE_LINE.exec(line)?.[1];
    const list = id ? partners.get(id) : undefined;
    return list ? `${line}${PAIRED_NOTE}${list.join(", ")}` : line;
  };
  return [a, b].map((extraction) => renderTableList(extraction).split("\n").map(mark).join("\n")).join("\n\n");
}

/** 표 검수 요청의 글에 남은 값 후보 숫자. 표 ID·쪽·파일 이름을 적은 줄은 요청 조립이 넣은 것이라 떼고, 나머지는 머리글 조각마다 본다. */
export function unmaskedInTableReview(requestText: string): string[] {
  const found: string[] = [];
  for (const line of requestText.split("\n")) {
    if (/^# 문서 [ab] \(.*\)$/.test(line) || /^\[표 [ab]:\S+\] \d+쪽( · 이미 짝지어짐: [ab]:\S+(, [ab]:\S+)*)?$/.test(line)) continue;
    const label = /^(제목·각주|단위 줄|열 머리글|행 머리글): /.exec(line);
    const body = label ? line.slice(label[0].length) : line;
    for (const piece of body.split(/ \/ | \| | ; | > /)) for (const token of scanNumbers(piece)) if (token.kind === "value") found.push(piece.slice(token.start, token.end));
  }
  return found;
}

/**
 * 표 검수의 답 확인. 양쪽 표 ID 가 표 목록에 있어야 하고(`unknown_id`), 이미 끌어낸 표 짝이 아니어야 하고(`already_paired`),
 * 적어도 한쪽은 짝이 없는 표여야 한다(`both_tables_already_paired`: 묻는 것은 짝이 없는 표의 상대다).
 */
export function validateNewTablePairs(answer: PairTablesAnswer, tablesA: Set<string>, tablesB: Set<string>, existing: TablePair[]): { pairs: TablePair[]; rejected: Record<string, number> } {
  const checked = validateTablePairs(answer, tablesA, tablesB);
  const known = new Set(existing.map(key));
  const paired = { a: new Set(existing.map((pair) => pair.a)), b: new Set(existing.map((pair) => pair.b)) };
  const pairs: TablePair[] = [];
  for (const pair of checked.pairs) {
    if (known.has(key(pair))) bump(checked.rejected, "already_paired");
    else if (paired.a.has(pair.a) && paired.b.has(pair.b)) bump(checked.rejected, "both_tables_already_paired");
    else pairs.push(pair);
  }
  return { pairs, rejected: checked.rejected };
}

// ── 4. 검수 뒤의 셈 ──────────────────────────────────────────────────────────

/** 검수가 새로 낸 짝 하나. */
export interface NewPair extends Verdict {
  /** cell_review: 칸 검수가 남은 칸의 상대로 답했다. table_review: 표 검수가 새로 짝지은 표의 칸 짝 요청(방법 G 의 것)이 냈다. */
  source: "cell_review" | "table_review";
  /** 이 짝이 나온 표 짝. */
  tables: TablePair;
  /** 그 상대 칸이 D 의 답에서 이미 다른 칸과 짝지어져 있었는가 (칸 검수만). */
  counterpartAlreadyPaired: boolean;
}

/** 남은 칸 하나가 요청 하나에서 받은 답. */
export interface RemainingAnswer {
  /** `a:` · `b:` 를 붙인 칸 ID. */
  cell: string;
  /** 그 요청의 상대 표 (`a:` · `b:` 를 붙인 표 ID). */
  otherTable: string;
  request: string;
  answer: CellReviewState;
}

export interface ReviewResult {
  tablePairs: DerivedTablePair[];
  before: { outcome: MethodOutcome; tally: { a: ReviewTally; b: ReviewTally } };
  after: { outcome: MethodOutcome; tally: { a: ReviewTally; b: ReviewTally } };
  newPairs: NewPair[];
  remaining: RemainingAnswer[];
  newTablePairs: TablePair[];
  /** 검수의 버려진 답 (단계별, 사유별). */
  rejected: { cellReview: Record<string, number>; tableReview: Record<string, number>; newTableCells: Record<string, number> };
}

/** 한 칸이 여러 요청에서 받은 답을 하나로: 가르지 못한 답이 하나라도 있으면 가르지 못한 것이고, 전부 「상대 없음」일 때만 「상대 없음」이다. */
function combine(answers: CellReviewState[]): ReviewState {
  if (answers.some((item) => item.state === "unanswered")) return "reviewed:unanswered";
  if (answers.some((item) => item.state === "answer_rejected")) return "reviewed:answer_rejected";
  if (answers.some((item) => item.state === "undecidable")) return "reviewed:undecidable";
  const reasons = [...new Set(answers.flatMap((item) => (item.state === "no_counterpart" ? [item.reason] : [])))].sort();
  // 「짝지었다」는 답뿐인데 견준 결과에 없는 칸은 없다 (새 짝은 전부 견준다). 그래도 남으면 미응답으로 둔다.
  return reasons.length > 0 ? `reviewed:no_counterpart:${reasons.join("+")}` : "reviewed:unanswered";
}

/**
 * 방법 H 의 결과: D 의 답에 검수가 새로 낸 짝을 더하고(같은 칸 짝은 한 번만), 두 문서의 모든 값 후보에 검수 전과 검수 뒤의 결과를 하나씩 붙여 센다.
 * 검수 뒤의 우선순위: 견줌 > 표의 숫자 칸이 아님 > 검수가 답한 남은 칸 > 새로 짝지어진 표 안의 남은 칸 > 버려진 답이 가리킨 칸 > 짝이 없는 표의 칸.
 */
export function reviewOutcome(input: { a: Extraction; b: Extraction; base: BaseResult; cellReviews: CellReviewJudgement[]; newTablePairs: TablePair[]; newTableJudgements: CellJudgement[]; tableRejected: Record<string, number> }): ReviewResult {
  const { a, b, base } = input;
  const tablePairs = deriveTablePairs(base.outcome.verdicts);
  const extractionOf = { a, b };
  const pairedBefore = { a: new Set(tablePairs.map((pair) => pair.a)), b: new Set(tablePairs.map((pair) => pair.b)) };
  const tallyOf = (states: { a: Map<string, ReviewState>; b: Map<string, ReviewState> }): { a: ReviewTally; b: ReviewTally } => ({ a: tallyReview(a.facts.length, states.a.values()), b: tallyReview(b.facts.length, states.b.values()) });
  const withStates = (verdicts: Verdict[], rejected: Record<string, number>, states: { a: Map<string, ReviewState>; b: Map<string, ReviewState> }): MethodOutcome => {
    const outcome: MethodOutcome = { verdicts, intra: [], states: { a: {}, b: {} }, rejected };
    for (const side of ["a", "b"] as const) for (const [id, state] of states[side]) if (!state.startsWith("compared:")) outcome.states[side][id] = state;
    return outcome;
  };

  const beforeStates = { a: classifyBefore(a, "a", pairedBefore.a, base.outcome.verdicts, base.rejectedCells.a), b: classifyBefore(b, "b", pairedBefore.b, base.outcome.verdicts, base.rejectedCells.b) };
  const before = { outcome: withStates(base.outcome.verdicts, base.outcome.rejected, beforeStates), tally: tallyOf(beforeStates) };

  // 새 짝: 칸 검수의 것, 그다음 새 표 짝의 것. 같은 칸 짝은 한 번만.
  const verdicts = [...base.outcome.verdicts];
  const seen = new Set(verdicts.map(key));
  const comparedBefore = { a: new Set(base.outcome.verdicts.map((verdict) => verdict.a)), b: new Set(base.outcome.verdicts.map((verdict) => verdict.b)) };
  const newPairs: NewPair[] = [];
  const add = (verdict: Verdict, source: NewPair["source"], tables: TablePair, counterpartAlreadyPaired: boolean): void => {
    if (seen.has(key(verdict))) return;
    seen.add(key(verdict));
    const plain: Verdict = { a: verdict.a, b: verdict.b, outcome: verdict.outcome, reason: verdict.reason, valuesDiffer: verdict.valuesDiffer };
    verdicts.push(plain);
    newPairs.push({ ...plain, source, tables, counterpartAlreadyPaired });
  };
  const rejected = { cellReview: {} as Record<string, number>, tableReview: { ...input.tableRejected }, newTableCells: {} as Record<string, number> };
  const answersByCell = { a: new Map<string, CellReviewState[]>(), b: new Map<string, CellReviewState[]>() };
  const remaining: RemainingAnswer[] = [];
  for (const review of input.cellReviews) {
    for (const pair of review.newPairs) add(pair, "cell_review", review.plan.pair, comparedBefore.a.has(pair.a) || comparedBefore.b.has(pair.b));
    for (const [reason, count] of Object.entries(review.rejected)) bump(rejected.cellReview, reason, count);
    for (const [cell, answer] of review.cells) {
      const side = cell.slice(0, 1) as "a" | "b";
      const other = side === "a" ? "b" : "a";
      answersByCell[side].set(cell.slice(2), [...(answersByCell[side].get(cell.slice(2)) ?? []), answer]);
      remaining.push({ cell, otherTable: `${other}:${review.plan.pair[other]}`, request: review.plan.id, answer });
    }
  }
  const rejectedCells = { a: new Set(base.rejectedCells.a), b: new Set(base.rejectedCells.b) };
  input.newTableJudgements.forEach((judgement, index) => {
    for (const verdict of judgement.verdicts) add(verdict, "table_review", input.newTablePairs[index] as TablePair, false);
    for (const [reason, count] of Object.entries(judgement.rejected)) bump(rejected.newTableCells, reason, count);
    for (const side of ["a", "b"] as const) for (const id of judgement.rejectedCells[side]) rejectedCells[side].add(id);
  });

  const newlyPaired = { a: new Set(input.newTablePairs.map((pair) => pair.a)), b: new Set(input.newTablePairs.map((pair) => pair.b)) };
  const afterStates = { a: new Map<string, ReviewState>(), b: new Map<string, ReviewState>() };
  for (const side of ["a", "b"] as const) {
    const worst = worstByCell(verdicts, side);
    for (const fact of extractionOf[side].facts) {
      const compared = worst.get(fact.id);
      const answers = answersByCell[side].get(fact.id);
      const gridId = fact.where.gridId as string;
      let state: ReviewState;
      if (compared) state = `compared:${compared}`;
      else if (fact.bucket !== "table_cell") state = `unverified:outside_tables:${fact.bucket}`;
      else if (answers) state = combine(answers);
      // 짝지어진 표의 남은 칸인데 검수 요청에 실리지 않은 칸은 없어야 한다. 있으면 미응답으로 드러낸다.
      else if (pairedBefore[side].has(gridId)) state = "reviewed:unanswered";
      else if (newlyPaired[side].has(gridId)) state = "left:in_newly_paired_table";
      else if (rejectedCells[side].has(fact.id)) state = "unverified:answer_rejected";
      else state = "unverified:table_not_paired";
      afterStates[side].set(fact.id, state);
    }
  }
  const allRejected: Record<string, number> = { ...base.outcome.rejected };
  for (const [stage, bag] of Object.entries(rejected)) for (const [reason, count] of Object.entries(bag)) bump(allRejected, `${stage}:${reason}`, count);
  const after = { outcome: withStates(verdicts, allRejected, afterStates), tally: tallyOf(afterStates) };
  return { tablePairs, before, after, newPairs, remaining, newTablePairs: input.newTablePairs, rejected };
}
