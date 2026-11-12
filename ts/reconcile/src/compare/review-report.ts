import { z } from "zod";
import type { GoldPair, Plant } from "./exam.js";
import { gradePair, renderComparison, type Grade, type MethodGrade, type PairStatus } from "./grade.js";
import type { MethodOutcome } from "./methods.js";
import type { ReviewResult, ReviewTally } from "./review.js";
import type { Judgments } from "./runs.js";

// 방법 H 의 실행들을 나란히 놓는 표 (MARC-TICK-23). 모델을 부르지 않는다. 정답 쌍과 판정 기록은 여기(채점)에서만 쓴다.
// 수는 전부 분모와 같이 낸다.

/** 방법 H 의 실행 하나: 어느 D 실행의 몇째 검수인지의 이름, 검수 뒤의 결과와 채점, 검수 전의 결과와 채점. */
export interface ReviewRun {
  name: string;
  outcome: MethodOutcome;
  grade: MethodGrade;
  review: Pick<ReviewResult, "tablePairs" | "newTablePairs" | "newPairs" | "remaining" | "rejected"> & { tally: { before: { a: ReviewTally; b: ReviewTally }; after: { a: ReviewTally; b: ReviewTally } } };
  before: { outcome: MethodOutcome; grade: Grade };
}

/** 검수가 「상대 없음」이라 한 남은 칸의 판정 기록. 러너가 원문 PDF 로 본 것이다. */
export const noCounterpartJudgmentsSchema = z.object({
  note: z.string(),
  /** 누가 판정했고 검토를 받았는가. */
  status: z.string().min(1),
  judgments: z.array(
    z.object({
      /** 남은 칸 (`a:표ID:r행c열` 꼴). */
      cell: z.string(),
      /** 그 답이 가리킨 상대 표 (`b:표ID` 꼴). */
      other_table: z.string(),
      /** no_counterpart: 상대 표에 같은 항목·기간·범위의 칸이 정말 없다. has_counterpart: 있다 (틀린 「상대 없음」). not_a_value: 그 칸이 값이 아닌 표시(연도 등)다. unclear: 원문으로 가를 수 없다. */
      verdict: z.enum(["no_counterpart", "has_counterpart", "not_a_value", "unclear"]),
      /** has_counterpart 일 때 그 상대 칸. */
      counterpart: z.string().nullable(),
      /** 근거: 원문 PDF 의 쪽, 행 이름, 열(기간). */
      evidence: z.object({ page: z.number().int(), row: z.string(), column: z.string() }),
      note: z.string().nullable(),
    }),
  ),
});
export type NoCounterpartJudgments = z.infer<typeof noCounterpartJudgmentsSchema>;

const key = (pair: { a: string; b: string }): string => `${pair.a}|${pair.b}`;
const sum = (bag: Record<string, number>): number => Object.values(bag).reduce((total, count) => total + count, 0);
const numeric = (x: string, y: string): number => x.localeCompare(y, "en", { numeric: true });
const STATUS_KO: Record<PairStatus, string> = { match: "일치", mismatch: "불일치", needs_explanation: "설명 필요", visible_elsewhere: "다른 칸과 견주어져 드러남", matched_elsewhere: "다른 칸과 일치", silent: "조용히 지나감" };
const OUTCOME_KO = { match: "일치", mismatch: "불일치", needs_explanation: "설명 필요" } as const;
const REASON_KO: Record<string, string> = { no_such_row_or_column: "상대 표에 없는 행·열", different_item: "같은 항목이 아님" };
const reasonKo = (reason: string): string => reason.split("+").map((item) => REASON_KO[item] ?? item).join(" + ");

export interface Recovery {
  pair: string;
  a: string;
  b: string;
  /** 심은 오류의 칸이면 그 오류의 ID. */
  plant: string | null;
  before: PairStatus;
  after: PairStatus;
}

/** 되찾기: 검수 전에 그 두 칸이 짝지어지지 않았던 정답 쌍이 검수 뒤에 어떻게 나왔는가. */
export function recoveries(pairs: GoldPair[], plants: Plant[], run: Pick<ReviewRun, "outcome" | "before">): Recovery[] {
  const plantOf = new Map(plants.flatMap((plant) => plant.cells.map((cell) => [cell.pair, plant.id] as const)));
  const compared = new Set(run.before.outcome.verdicts.map(key));
  return pairs.filter((pair) => !compared.has(key(pair))).map((pair) => ({ pair: pair.id, a: pair.a, b: pair.b, plant: plantOf.get(pair.id) ?? null, before: gradePair(pair, run.before.outcome).status, after: gradePair(pair, run.outcome).status }));
}

export interface NewPairRow {
  a: string;
  b: string;
  outcome: "match" | "mismatch" | "needs_explanation";
  inGold: boolean;
  /** 이 짝을 낸 실행과 그 출처 (`D3-검수1:칸 검수` 꼴). */
  runs: string[];
  counterpartAlreadyPaired: boolean;
}

/** 검수가 새로 낸 짝의 합집합 (실행들에 걸쳐). */
export function newPairUnion(pairs: GoldPair[], runs: ReviewRun[]): NewPairRow[] {
  const gold = new Set(pairs.map(key));
  const found = new Map<string, NewPairRow>();
  for (const run of runs) {
    for (const pair of run.review.newPairs) {
      const entry = found.get(key(pair)) ?? { a: pair.a, b: pair.b, outcome: pair.outcome, inGold: gold.has(key(pair)), runs: [], counterpartAlreadyPaired: false };
      entry.runs.push(`${run.name}:${pair.source === "cell_review" ? "칸 검수" : "표 검수"}`);
      entry.counterpartAlreadyPaired ||= pair.counterpartAlreadyPaired;
      found.set(key(pair), entry);
    }
  }
  return [...found.values()].sort((x, y) => numeric(key(x), key(y)));
}

export interface NoCounterpartRow {
  cell: string;
  otherTable: string;
  /** 실행마다 그 칸이 그 상대 표에 대해 받은 답 (그 실행에서 묻지 않았으면 null). */
  answers: (string | null)[];
}

/** 검수가 「상대 없음」이라 답한 (남은 칸, 상대 표)의 합집합과, 실행마다의 답. */
export function noCounterpartUnion(runs: ReviewRun[]): NoCounterpartRow[] {
  const describe = (answer: ReviewRun["review"]["remaining"][number]["answer"]): string => (answer.state === "no_counterpart" ? `상대 없음 (${reasonKo(answer.reason)})` : answer.state === "paired" ? `상대 ${answer.counterpart}` : answer.state === "undecidable" ? "가를 수 없음" : answer.state === "unanswered" ? "미응답" : `버려진 답 (${answer.why})`);
  const found = new Map<string, NoCounterpartRow>();
  runs.forEach((run, index) => {
    for (const item of run.review.remaining) {
      const id = `${item.cell}|${item.otherTable}`;
      const entry = found.get(id) ?? { cell: item.cell, otherTable: item.otherTable, answers: runs.map(() => null), said: false };
      entry.answers[index] = describe(item.answer);
      if (item.answer.state === "no_counterpart") (entry as NoCounterpartRow & { said: boolean }).said = true;
      found.set(id, entry);
    }
  });
  return [...found.values()].filter((entry) => (entry as NoCounterpartRow & { said: boolean }).said).map(({ cell, otherTable, answers }) => ({ cell, otherTable, answers })).sort((x, y) => numeric(`${x.cell}|${x.otherTable}`, `${y.cell}|${y.otherTable}`));
}

/** 방법 H 의 실행들을 마크다운으로. 판정 기록이 없으면 목록만 낸다. */
export function renderReview(runs: ReviewRun[], pairs: GoldPair[], plants: Plant[], judgments: Judgments | null, noCounterpart: NoCounterpartJudgments | null): string {
  const lines: string[] = [];
  const columns = runs.flatMap((run): MethodGrade[] => [
    { ...run.grade, method: `${run.name} 전`, grade: run.before.grade, cost: null },
    { ...run.grade, method: `${run.name} 후` },
  ]);
  lines.push(renderComparison(columns, plants).replace("## 방법 × 항목 비교표", "## 검수 전·후 비교표 (「전」은 저장된 D 의 답에 코드의 셈만 붙인 것, 「후」의 비용은 검수의 것만)").replace("## 심은 오류별 × 방법별", "## 심은 칸 × 검수 전·후"));

  lines.push("## 되찾기: 검수 전에 짝지어지지 않았던 정답 쌍", "", "| 실행 | 검수 전에 짝지어지지 않은 정답 쌍 | 그중 검수 뒤에 짝지어진 것 | 건별 (정답 쌍: 전 → 후) |", "|---|---:|---:|---|");
  for (const run of runs) {
    const rows = recoveries(pairs, plants, run);
    const recovered = rows.filter((row) => row.after === "match" || row.after === "mismatch" || row.after === "needs_explanation");
    lines.push(`| ${run.name} | ${rows.length}/${pairs.length} | ${recovered.length}/${rows.length} | ${rows.map((row) => `${row.a} ↔ ${row.b}${row.plant ? ` (${row.plant})` : ""}: ${STATUS_KO[row.before]} → ${STATUS_KO[row.after]}`).join("; ") || "-"} |`);
  }

  lines.push("", "## 단계별 셈: 모든 값 후보가 결과 하나에 (분모는 그 문서의 값 후보 수)", "");
  const tallyRows: [string, (tally: ReviewTally) => number][] = [
    ["견줌: 일치", (tally) => tally.compared.match],
    ["견줌: 불일치", (tally) => tally.compared.mismatch],
    ["견줌: 설명 필요", (tally) => tally.compared.needsExplanation],
    ["짝지어진 표 안의 남은 칸 (검수 전)", (tally) => tally.leftInPairedTable],
    ...[...new Set(runs.flatMap((run) => (["a", "b"] as const).flatMap((side) => Object.keys(run.review.tally.after[side].noCounterpart))))].sort().map((reason): [string, (tally: ReviewTally) => number] => [`검수: 상대 없음 (${reasonKo(reason)})`, (tally) => tally.noCounterpart[reason] ?? 0]),
    ["검수: 가를 수 없음", (tally) => tally.undecided.undecidable],
    ["검수: 미응답", (tally) => tally.undecided.unanswered],
    ["검수: 버려진 답", (tally) => tally.undecided.answerRejected],
    ["새로 짝지어진 표 안인데 칸 짝이 안 나옴", (tally) => tally.leftInNewlyPairedTable],
    ["짝이 없는 표의 칸인데 이 칸을 가리킨 답이 버려짐", (tally) => tally.answerRejected],
    ["짝이 없는 표의 칸", (tally) => tally.tableNotPaired],
    ["표의 숫자 칸이 아님 (문장 속, 숫자뿐인 블록, 표의 글 칸 속)", (tally) => sum(tally.outsideTables)],
  ];
  const tallies = runs.flatMap((run) => [run.review.tally.before.a, run.review.tally.before.b, run.review.tally.after.a, run.review.tally.after.b]);
  lines.push(`| 결과 | ${runs.flatMap((run) => [`${run.name} 전 A`, `${run.name} 전 B`, `${run.name} 후 A`, `${run.name} 후 B`]).join(" | ")} |`, `|---|${tallies.map(() => "---:").join("|")}|`);
  for (const [name, pick] of tallyRows) lines.push(`| ${name} | ${tallies.map((tally) => `${pick(tally)}`).join(" | ")} |`);
  lines.push(`| 합 (= 값 후보 수) | ${tallies.map((tally) => `${tallyRows.reduce((total, [, pick]) => total + pick(tally), 0)}/${tally.total}`).join(" | ")} |`);

  lines.push("", "## 검수가 남은 칸에 답한 것 (요청 단위. 한 칸이 여러 표 짝에 걸리면 여러 번 센다)", "", "| 실행 | 칸 검수 요청 | 남은 칸 (물은 수) | 상대 칸을 답함 | 상대 없음 | 가를 수 없음 | 미응답 | 버려진 답 | 버려진 답의 사유 |", "|---|---:|---:|---:|---:|---:|---:|---:|---|");
  for (const run of runs) {
    const asked = run.review.remaining;
    const count = (state: string): number => asked.filter((item) => item.answer.state === state).length;
    lines.push(`| ${run.name} | ${new Set(asked.map((item) => item.request)).size} | ${asked.length} | ${count("paired")}/${asked.length} | ${count("no_counterpart")}/${asked.length} | ${count("undecidable")}/${asked.length} | ${count("unanswered")}/${asked.length} | ${count("answer_rejected")}/${asked.length} | ${Object.entries(run.review.rejected.cellReview).map(([reason, n]) => `${reason} ${n}`).join(", ") || "-"} |`);
  }

  const tableUnion = [...new Set(runs.flatMap((run) => [...run.review.tablePairs, ...run.review.newTablePairs].map(key)))].sort(numeric);
  lines.push("", "## 끌어낸 표 짝 (D 의 칸 짝에서, 칸 안의 수는 그 두 표 사이의 칸 짝 수)과 표 검수가 새로 낸 표 짝", "", `| 표 짝 (A ↔ B) | ${runs.map((run) => run.name).join(" | ")} |`, `|---|${runs.map(() => "---").join("|")}|`);
  for (const pair of tableUnion) lines.push(`| ${pair.replace("|", " ↔ ")} | ${runs.map((run) => { const derived = run.review.tablePairs.find((item) => key(item) === pair); return derived ? `끌어냄 (${derived.cellPairs})` : run.review.newTablePairs.some((item) => key(item) === pair) ? "표 검수가 새로 냄" : "없음"; }).join(" | ")} |`);
  lines.push("", `| 표 검수 | ${runs.map((run) => run.name).join(" | ")} |`, `|---|${runs.map(() => "---").join("|")}|`, `| 새 표 짝 (개수) | ${runs.map((run) => run.review.newTablePairs.length).join(" | ")} |`, `| 버려진 답 (사유별) | ${runs.map((run) => Object.entries(run.review.rejected.tableReview).map(([reason, n]) => `${reason} ${n}`).join(", ") || "0").join(" | ")} |`);

  const union = newPairUnion(pairs, runs);
  const judged = new Map((judgments?.judgments ?? []).map((item) => [key(item), item]));
  const VERDICT_KO = { same: "같은 항목", not_same: "같은 항목 아님", not_a_value: "값이 아닌 표시끼리", unclear: "가를 수 없음" } as const;
  const verdictOf = (row: NewPairRow): string => (row.inGold ? "정답 쌍" : judged.has(key(row)) ? VERDICT_KO[judged.get(key(row))!.verdict] : "판정 없음");
  lines.push("", "## 검수가 새로 낸 짝", "", "| 실행 | 새 짝 | 그중 정답 쌍 | 정답 밖: 같은 항목 | 정답 밖: 같은 항목 아님 | 정답 밖: 값이 아닌 표시끼리 | 정답 밖: 가를 수 없음 | 정답 밖: 판정 없음 | 같은 항목이 아닌 새 짝이 일치로 / 불일치로 |", "|---|---:|---:|---:|---:|---:|---:|---:|---|");
  for (const run of runs) {
    const mine = run.review.newPairs.map((pair) => union.find((row) => key(row) === key(pair)) as NewPairRow);
    const n = mine.length;
    const of = (verdict: string): number => mine.filter((row) => verdictOf(row) === verdict).length;
    const wrong = mine.filter((row) => verdictOf(row) === "같은 항목 아님");
    lines.push(`| ${run.name} | ${n} | ${of("정답 쌍")}/${n} | ${of("같은 항목")}/${n} | ${wrong.length}/${n} | ${of("값이 아닌 표시끼리")}/${n} | ${of("가를 수 없음")}/${n} | ${of("판정 없음")}/${n} | ${wrong.filter((row) => row.outcome === "match").length}/${n} / ${wrong.filter((row) => row.outcome === "mismatch").length}/${n} |`);
  }
  lines.push("", `합집합 ${union.length}쌍.`, "", "| A 칸 | B 칸 | 코드가 견준 결과 | 낸 실행 (출처) | 상대 칸이 이미 짝지어져 있었나 | 판정 | 근거 A (쪽, 행, 열) | 근거 B (쪽, 행, 열) | 비고 |", "|---|---|---|---|---|---|---|---|---|");
  for (const row of union) {
    const judgment = judged.get(key(row));
    const evidence = (side: "a" | "b"): string => (judgment ? `${judgment.evidence[side].page}쪽, ${judgment.evidence[side].row}, ${judgment.evidence[side].column}` : "-");
    lines.push(`| ${row.a} | ${row.b} | ${OUTCOME_KO[row.outcome]} | ${row.runs.join(", ")} | ${row.counterpartAlreadyPaired ? "예" : "아니오"} | ${verdictOf(row)} | ${evidence("a")} | ${evidence("b")} | ${judgment?.note ?? ""} |`);
  }

  const none = noCounterpartUnion(runs);
  const noneJudged = new Map((noCounterpart?.judgments ?? []).map((item) => [`${item.cell}|${item.other_table}`, item]));
  const NONE_KO = { no_counterpart: "정말 상대 없음", has_counterpart: "상대 있음 (틀린 「상대 없음」)", not_a_value: "값이 아닌 표시", unclear: "가를 수 없음" } as const;
  const noneCount = (verdict: string): number => none.filter((row) => noneJudged.get(`${row.cell}|${row.otherTable}`)?.verdict === verdict).length;
  lines.push("", "## 검수가 「상대 없음」이라 한 남은 칸 (실행들의 합집합, 남은 칸 × 상대 표)", "", `합집합 ${none.length}건. 판정: 정말 상대 없음 ${noneCount("no_counterpart")}/${none.length}, 상대 있음(틀린 「상대 없음」) ${noneCount("has_counterpart")}/${none.length}, 값이 아닌 표시 ${noneCount("not_a_value")}/${none.length}, 가를 수 없음 ${noneCount("unclear")}/${none.length}, 판정 없음 ${none.filter((row) => !noneJudged.has(`${row.cell}|${row.otherTable}`)).length}/${none.length}.`, "", `| 남은 칸 | 상대 표 | ${runs.map((run) => run.name).join(" | ")} | 판정 | 근거 (쪽, 행, 열) | 비고 |`, `|---|---|${runs.map(() => "---").join("|")}|---|---|---|`);
  for (const row of none) {
    const judgment = noneJudged.get(`${row.cell}|${row.otherTable}`);
    lines.push(`| ${row.cell} | ${row.otherTable} | ${row.answers.map((answer) => answer ?? "묻지 않음").join(" | ")} | ${judgment ? `${NONE_KO[judgment.verdict]}${judgment.counterpart ? ` → ${judgment.counterpart}` : ""}` : "판정 없음"} | ${judgment ? `${judgment.evidence.page}쪽, ${judgment.evidence.row}, ${judgment.evidence.column}` : "-"} | ${judgment?.note ?? ""} |`);
  }
  return `${lines.join("\n")}\n`;
}
