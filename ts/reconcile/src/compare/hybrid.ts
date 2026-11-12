import { z } from "zod";
import type { DocModel, Extraction, Fact, Grid } from "../domain/facts.js";
import { normalizeText, scanNumbers } from "../domain/notation.js";
import type { UnitTable } from "../domain/units.js";
import { compareCells, type DocContext, type MethodOutcome, type Verdict } from "./methods.js";
import { cellText, maskText } from "./render.js";

// 방법 G (MARC-TICK-22): 표 짝은 방법 F 처럼 먼저 받고(`reconcile/pair-tables@1`), 짝지어진 표 안의 칸 짝은 방법 D 처럼 AI 가 가린 표를 보고 낸다.
// 값 비교는 코드(`compareValues`)다. 답 스키마에 값 필드가 없다. 두 문서의 모든 값 후보가 결과 하나에 들어간다.

export const PROMPT_G_CELLS = "reconcile/pair-cells-in-tables@1";

export const pairCellsInTablesSchema = z.object({
  pairs: z.array(z.object({ a: z.string(), b: z.string(), unit_a: z.string().nullable(), unit_b: z.string().nullable() })),
});
export type PairCellsInTablesAnswer = z.infer<typeof pairCellsInTablesSchema>;

export interface TablePair {
  a: string;
  b: string;
}

/**
 * 칸 짝 요청의 한쪽: 표 하나와 그 표의 제목·각주, 그리고 그 쪽의 글 블록을 읽는 순서대로. 그 쪽의 다른 표는 싣지 않는다.
 * 값 후보 숫자는 방법 D 와 같은 방식으로 가린다: 숫자 칸은 칸 전체를 기호 하나로, 글(제목·각주·쪽의 글·글 칸) 속의 값 후보도 기호 하나로.
 */
export function renderTableSide(document: DocModel, extraction: Extraction, gridId: string): string {
  const grid = extraction.grids.find((item) => item.id === gridId);
  if (!grid) throw new Error(`표 ${gridId} 가 문서 ${extraction.docId} 에 없다`);
  const facts = new Map(extraction.facts.map((fact) => [fact.id, fact]));
  const lines: string[] = [`# 문서 ${extraction.docId} 의 표 ${grid.id} (${grid.page}쪽)`];
  lines.push(`제목·각주: ${grid.titleCandidates.map(maskText).join(" / ") || "(없음)"}`);
  if (grid.unitLines.length > 0) lines.push(`단위 줄: ${grid.unitLines.map(maskText).join(" / ")}`);
  lines.push(`## ${grid.page}쪽의 글과 이 표 (읽는 순서)`);
  for (const block of document.blocks.filter((item) => item.page === grid.page).sort((x, y) => x.order - y.order)) {
    if (block.kind !== "table") {
      const text = normalizeText(block.text ?? "");
      if (text !== "") lines.push(maskText(text));
      continue;
    }
    if (block.table_id !== grid.id) continue;
    lines.push(`[표 ${grid.id}]`);
    grid.cells.forEach((cells, row) => {
      const parts = cells.map((raw, col) => [col, cellText(grid, row, col, raw, facts, true)] as const).filter(([, text]) => text !== "");
      if (parts.length > 0) lines.push(`r${row} | ${parts.map(([col, text]) => `c${col}: ${text}`).join(" | ")}`);
    });
    lines.push("[표 끝]");
  }
  return lines.join("\n");
}

/** 칸 짝 요청의 글: 두 표의 한쪽씩. */
export function renderCellPairRequest(a: { document: DocModel; extraction: Extraction }, b: { document: DocModel; extraction: Extraction }, pair: TablePair): string {
  return `${renderTableSide(a.document, a.extraction, pair.a)}\n\n${renderTableSide(b.document, b.extraction, pair.b)}`;
}

/**
 * 요청 글에 남은 값 후보 숫자. 가림이 빠진 곳을 호출 전에 잡으려는 것이다 (밴드 R 에서 F 의 행 짝 요청이 가림 없이 나갔다).
 * 칸·표·쪽을 가리키는 글자(`r3 |`, `c4:`, `[표 p1-t1]`, `(12쪽)`)는 요청 조립이 넣은 것이라 떼고 본다.
 */
export function unmaskedValues(requestText: string): string[] {
  const found: string[] = [];
  for (const line of requestText.split("\n")) {
    if (/^# 문서 \S+ 의 표 \S+ \(\d+쪽\)$/.test(line) || /^## \d+쪽의 글과 이 표 \(읽는 순서\)$/.test(line) || /^\[표 [^\]]+\]$/.test(line)) continue;
    const cells = /^r\d+ \| /.test(line) ? line.replace(/^r\d+ \| /, "").split(" | ").map((part) => part.replace(/^c\d+: /, "")) : [line];
    for (const text of cells) for (const token of scanNumbers(text)) if (token.kind === "value") found.push(text.slice(token.start, token.end));
  }
  return found;
}

export interface CellJudgement {
  verdicts: Verdict[];
  rejected: Record<string, number>;
  /** 버려진 답이 가리킨 칸 가운데 그 문서에 실제로 있는 숫자 칸. */
  rejectedCells: { a: string[]; b: string[] };
}

const squash = (text: string): string => text.normalize("NFKC").replace(/\s+/g, "");
const cellFacts = (extraction: Extraction): Map<string, Fact> => new Map(extraction.facts.filter((fact) => fact.bucket === "table_cell").map((fact) => [fact.id, fact]));

/**
 * 칸 짝 답의 확인과 비교. 양쪽 ID 가 그 문서의 숫자 칸이어야 하고(`unknown_id`), **그 요청에 실은 두 표의 칸**이어야 하고(`outside_request_tables`),
 * 단위 글자는 그 쪽에 있어야 한다(`unit_not_in_source`, 방법 D 와 같다). 같은 짝을 두 번 적으면 한 번만 센다. 남은 짝은 코드가 값을 견준다.
 */
export function judgeTableCells(answer: PairCellsInTablesAnswer, pair: TablePair, a: DocContext, b: DocContext, units: UnitTable): CellJudgement {
  const out: CellJudgement = { verdicts: [], rejected: {}, rejectedCells: { a: [], b: [] } };
  const factsA = cellFacts(a.extraction);
  const factsB = cellFacts(b.extraction);
  const done = new Set<string>();
  const reject = (reason: string, fa: Fact | undefined, fb: Fact | undefined): void => {
    out.rejected[reason] = (out.rejected[reason] ?? 0) + 1;
    if (fa) out.rejectedCells.a.push(fa.id);
    if (fb) out.rejectedCells.b.push(fb.id);
  };
  const unitOk = (unit: string | null, doc: DocContext, fact: Fact): boolean => unit === null || squash(doc.pages.get(fact.where.page ?? -1) ?? "").includes(squash(unit)) || squash(fact.unitSuffix ?? "") === squash(unit);
  for (const raw of answer.pairs) {
    const fa = factsA.get(raw.a);
    const fb = factsB.get(raw.b);
    if (!fa || !fb) reject("unknown_id", fa, fb);
    else if (fa.where.gridId !== pair.a || fb.where.gridId !== pair.b) reject("outside_request_tables", fa, fb);
    else if (done.has(`${fa.id}|${fb.id}`)) out.rejected.duplicate_pair = (out.rejected.duplicate_pair ?? 0) + 1;
    else if (!unitOk(raw.unit_a, a, fa) || !unitOk(raw.unit_b, b, fb)) reject("unit_not_in_source", fa, fb);
    else {
      done.add(`${fa.id}|${fb.id}`);
      const result = compareCells({ fact: fa, unit: raw.unit_a }, { fact: fb, unit: raw.unit_b }, units);
      out.verdicts.push({ a: fa.id, b: fb.id, outcome: result.outcome as Verdict["outcome"], reason: result.reason, valuesDiffer: result.valuesDiffer });
    }
  }
  return out;
}

/** 값 후보 하나의 결과. 견준 것은 `compared:<결과>`, 그 밖은 「확인 못 함」의 사유. */
export type CandidateState =
  | "compared:match"
  | "compared:mismatch"
  | "compared:needs_explanation"
  | "unverified:answer_rejected" // 이 칸을 가리킨 답이 버려졌고 다른 짝으로도 견주어지지 않았다
  | "unverified:no_cell_pair_in_paired_table" // 짝지어진 표 안인데 칸 짝이 나오지 않았다
  | "unverified:table_not_paired" // 표 짝이 없다
  | `unverified:outside_tables:${string}`; // 표의 숫자 칸이 아니다 (문장 속, 숫자뿐인 블록, 표의 글 칸 속)

const SEVERITY: Record<Verdict["outcome"], number> = { mismatch: 3, needs_explanation: 2, match: 1 };

/**
 * 한 문서의 모든 값 후보에 결과를 하나씩 붙인다. 한 칸이 여러 짝에서 견주어졌으면 가장 무거운 결과(불일치 > 설명 필요 > 일치)를 그 칸의 결과로 한다.
 * 우선순위: 견줌 > 버려진 답 > 표 밖 > 짝지어진 표 안 > 표 짝 없음.
 */
export function classifyCandidates(extraction: Extraction, side: "a" | "b", pairedTables: Set<string>, verdicts: Verdict[], rejectedCells: Set<string>): Map<string, CandidateState> {
  const worst = new Map<string, Verdict["outcome"]>();
  for (const verdict of verdicts) {
    const id = verdict[side];
    const before = worst.get(id);
    if (!before || SEVERITY[verdict.outcome] > SEVERITY[before]) worst.set(id, verdict.outcome);
  }
  const states = new Map<string, CandidateState>();
  for (const fact of extraction.facts) {
    const compared = worst.get(fact.id);
    if (compared) states.set(fact.id, `compared:${compared}`);
    else if (rejectedCells.has(fact.id)) states.set(fact.id, "unverified:answer_rejected");
    else if (fact.bucket !== "table_cell") states.set(fact.id, `unverified:outside_tables:${fact.bucket}`);
    else if (pairedTables.has(fact.where.gridId as string)) states.set(fact.id, "unverified:no_cell_pair_in_paired_table");
    else states.set(fact.id, "unverified:table_not_paired");
  }
  return states;
}

export interface CandidateTally {
  /** 그 문서의 값 후보 수 (분모). */
  total: number;
  compared: { match: number; mismatch: number; needsExplanation: number };
  noCellPairInPairedTable: number;
  tableNotPaired: number;
  outsideTables: Record<string, number>;
  answerRejected: number;
}

export class TallyError extends Error {
  override name = "TallyError";
}

/** 결과별 수. 합이 값 후보 수와 다르면 수를 내지 않고 예외를 낸다. */
export function tallyCandidates(total: number, states: Iterable<CandidateState>): CandidateTally {
  const tally: CandidateTally = { total, compared: { match: 0, mismatch: 0, needsExplanation: 0 }, noCellPairInPairedTable: 0, tableNotPaired: 0, outsideTables: {}, answerRejected: 0 };
  for (const state of states) {
    if (state === "compared:match") tally.compared.match += 1;
    else if (state === "compared:mismatch") tally.compared.mismatch += 1;
    else if (state === "compared:needs_explanation") tally.compared.needsExplanation += 1;
    else if (state === "unverified:answer_rejected") tally.answerRejected += 1;
    else if (state === "unverified:no_cell_pair_in_paired_table") tally.noCellPairInPairedTable += 1;
    else if (state === "unverified:table_not_paired") tally.tableNotPaired += 1;
    else {
      const bucket = state.slice("unverified:outside_tables:".length);
      tally.outsideTables[bucket] = (tally.outsideTables[bucket] ?? 0) + 1;
    }
  }
  const sum = tally.compared.match + tally.compared.mismatch + tally.compared.needsExplanation + tally.answerRejected + tally.noCellPairInPairedTable + tally.tableNotPaired + Object.values(tally.outsideTables).reduce((x, y) => x + y, 0);
  if (sum !== total) throw new TallyError(`값 후보 ${total}개 중 결과가 붙은 것이 ${sum}개다. 합이 맞지 않아 수를 내지 않는다`);
  return tally;
}

export interface HybridResult {
  outcome: MethodOutcome;
  tally: { a: CandidateTally; b: CandidateTally };
}

/** G 의 결과: 표 짝마다의 판정을 모으고(같은 칸 짝은 한 번만), 두 문서의 모든 값 후보에 결과를 붙여 센다. */
export function hybridOutcome(a: Extraction, b: Extraction, tablePairs: TablePair[], judgements: CellJudgement[], tableRejected: Record<string, number>): HybridResult {
  const outcome: MethodOutcome = { verdicts: [], intra: [], states: { a: {}, b: {} }, rejected: { ...tableRejected } };
  const seen = new Set<string>();
  const rejectedCells = { a: new Set<string>(), b: new Set<string>() };
  for (const judgement of judgements) {
    for (const verdict of judgement.verdicts) {
      if (seen.has(`${verdict.a}|${verdict.b}`)) continue;
      seen.add(`${verdict.a}|${verdict.b}`);
      outcome.verdicts.push(verdict);
    }
    for (const [reason, count] of Object.entries(judgement.rejected)) outcome.rejected[reason] = (outcome.rejected[reason] ?? 0) + count;
    for (const side of ["a", "b"] as const) for (const id of judgement.rejectedCells[side]) rejectedCells[side].add(id);
  }
  const tally = {} as HybridResult["tally"];
  for (const side of ["a", "b"] as const) {
    const extraction = side === "a" ? a : b;
    const states = classifyCandidates(extraction, side, new Set(tablePairs.map((pair) => pair[side])), outcome.verdicts, rejectedCells[side]);
    tally[side] = tallyCandidates(extraction.facts.length, states.values());
    for (const [id, state] of states) if (!state.startsWith("compared:")) outcome.states[side][id] = state;
  }
  return { outcome, tally };
}

/** 표의 숫자 칸 수 (요청 하나의 예상 출력을 어림할 때 쓴다). */
export function valueCellCount(extraction: Extraction, grid: Pick<Grid, "id">): number {
  return extraction.facts.filter((fact) => fact.bucket === "table_cell" && fact.where.gridId === grid.id).length;
}
