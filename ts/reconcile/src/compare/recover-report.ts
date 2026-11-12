import type { Extraction } from "../domain/facts.js";
import type { UnitTable } from "../domain/units.js";
import type { GoldPair, Plant } from "./exam.js";
import { grade } from "./grade.js";
import type { MethodOutcome, Verdict } from "./methods.js";
import type { ProseJudgments } from "./prose-report.js";
import { checkShifts, type Nomination, type ShiftCheck, type ShiftRule } from "./recover.js";

// 밴드 Y (MARC-TICK-28) 의 채점과 표. 정답 쌍·심은 오류 명세·판정 기록을 읽는 것은 이 파일과 `cli8.ts` 뿐이다 (지목과 요청 조립은 `recover.ts`·`cli7.ts`).
// 수는 전부 분모와 같이 낸다. 모델을 부르지 않는다.

const key = (pair: { a: string; b: string }): string => `${pair.a}|${pair.b}`;
const gridOf = (cell: string): string => cell.slice(0, cell.lastIndexOf(":"));
const tableKey = (pair: { a: string; b: string }): string => `${gridOf(pair.a)}|${gridOf(pair.b)}`;
const byId = (x: string, y: string): number => x.localeCompare(y, "en", { numeric: true });

export interface Sheet {
  pairs: GoldPair[];
  plants: Plant[];
  /** 문서 A 에 심은 자리(넓힌 명세)와 문서 B 에 심은 칸. 「심은 칸이 낀 짝」을 가를 때 쓴다. */
  planted: { a: Set<string>; b: Set<string> };
  /** 표의 숫자 칸의 ID. */
  cells: { a: Set<string>; b: Set<string> };
  /** 판정 기록 (두 문서 사이의 짝). */
  judged: Map<string, { verdict: ProseJudgments["items"][number]["verdict"]; basis: string }>;
}

export function sheetOf(pairs: GoldPair[], plants: Plant[], plantedA: string[], a: Extraction, b: Extraction, judgments: ProseJudgments | null): Sheet {
  const cells = (extraction: Extraction): Set<string> => new Set(extraction.facts.filter((fact) => fact.bucket === "table_cell").map((fact) => fact.id));
  return {
    pairs,
    plants,
    planted: { a: new Set(plantedA), b: new Set(plants.filter((plant) => plant.isError).flatMap((plant) => plant.cells.map((cell) => cell.cell))) },
    cells: { a: cells(a), b: cells(b) },
    judged: new Map((judgments?.items ?? []).filter((item) => item.scope === "cross").map((item) => [`${item.a}|${item.b}`, { verdict: item.verdict, basis: item.basis }])),
  };
}

const asOutcome = (verdicts: Verdict[]): MethodOutcome => ({ verdicts, intra: [], states: { a: {}, b: {} }, rejected: {} });

export interface StageScore {
  /** 오류를 심지 않은 정답 쌍 가운데 일치 (분모 42). */
  cleanMatch: number;
  cleanTotal: number;
  /** 심은 오류 칸 가운데 그 쌍이 불일치 (분모 15). */
  caught: number;
  errorTotal: number;
  falseMatch: number;
  /** 정답 쌍 가운데 짝지어진 것 (분모 58). */
  goldPaired: number;
  /** 표 칸끼리의 불일치 가운데 정답 쌍이 아닌 것: 판정별. */
  outside: { differentItem: string[]; sameItem: string[]; notAValue: string[]; unjudged: string[] };
  /** 견준 짝 전체 / 표 칸끼리. */
  compared: number;
  comparedCells: number;
}

/** 한 단계(원래 답 / 검사만 / 다시 물은 뒤)의 성적. 표 칸 거짓 경보 = 표 칸끼리의 불일치 가운데 정답 쌍이 아니고 판정이 「같은 항목 아님」·「값이 아닌 표시」인 것. */
export function scoreStage(sheet: Sheet, verdicts: Verdict[]): StageScore {
  const graded = grade(sheet.pairs, sheet.plants, asOutcome(verdicts));
  const gold = new Set(sheet.pairs.map(key));
  const have = new Set(verdicts.map(key));
  const outside: StageScore["outside"] = { differentItem: [], sameItem: [], notAValue: [], unjudged: [] };
  const cellPairs = verdicts.filter((verdict) => sheet.cells.a.has(verdict.a) && sheet.cells.b.has(verdict.b));
  for (const verdict of cellPairs) {
    if (verdict.outcome !== "mismatch" || gold.has(key(verdict))) continue;
    const judged = sheet.judged.get(key(verdict))?.verdict;
    (judged === "different_item" ? outside.differentItem : judged === "same_item" ? outside.sameItem : judged === "not_a_value" ? outside.notAValue : outside.unjudged).push(key(verdict));
  }
  return { cleanMatch: graded.clean.match, cleanTotal: graded.clean.total, caught: graded.errors.caught, errorTotal: graded.errors.total, falseMatch: graded.errors.falseMatch, goldPaired: sheet.pairs.filter((pair) => have.has(key(pair))).length, outside, compared: verdicts.length, comparedCells: cellPairs.length };
}

export interface ShiftScore {
  rule: ShiftRule;
  cellPairs: number;
  ok: number;
  undecided: number;
  /** 판단 못 함 가운데 머리글이 하나라도 서지 않은 것. 나머지는 두 표가 머리글을 달리 적은 것이다. */
  undecidedNoHeader: number;
  suspect: number;
  /** 가린 것 가운데: 정답 쌍(잘못 가림), 판정이 「같은 항목」(잘못 가림), 「같은 항목 아님」(맞게 가림), 판정 없음. */
  suspectGold: string[];
  suspectSame: string[];
  suspectDifferent: string[];
  suspectUnjudged: string[];
  /** 그 실행의 표 칸 짝 가운데 판정이 「같은 항목 아님」인 것(틀린 짝)과 그중 가린 것. */
  wrongPairs: string[];
  wrongFlagged: string[];
  /** 머리글이 한쪽이라도 서지 않아 판단하지 못한 짝 (열·행 가운데 하나라도 머리글 없음은 여기서 가르지 않는다. 판단 못 함 전체가 `undecided` 다). */
  checks: ShiftCheck[];
}

/** 열 밀림 검사의 채점: 그 실행의 받아들여진 표 칸 짝 전부에 검사를 붙이고, 가린 것을 정답 쌍·판정 기록과 견준다. */
export function scoreShift(sheet: Sheet, verdicts: Verdict[], a: Extraction, b: Extraction, units: UnitTable, rule: ShiftRule): ShiftScore {
  const checks = checkShifts(verdicts, a, b, units, rule);
  const gold = new Set(sheet.pairs.map(key));
  const out: ShiftScore = { rule, cellPairs: checks.length, ok: 0, undecided: 0, undecidedNoHeader: 0, suspect: 0, suspectGold: [], suspectSame: [], suspectDifferent: [], suspectUnjudged: [], wrongPairs: [], wrongFlagged: [], checks };
  for (const check of checks) {
    const judged = gold.has(key(check)) ? "gold" : (sheet.judged.get(key(check))?.verdict ?? null);
    if (judged === "different_item") out.wrongPairs.push(key(check));
    if (check.verdict === "ok") out.ok += 1;
    else if (check.verdict === "undecided") {
      out.undecided += 1;
      if (check.noHeader) out.undecidedNoHeader += 1;
    }
    else {
      out.suspect += 1;
      (judged === "gold" ? out.suspectGold : judged === "same_item" ? out.suspectSame : judged === "different_item" ? out.suspectDifferent : out.suspectUnjudged).push(key(check));
      if (judged === "different_item") out.wrongFlagged.push(key(check));
    }
  }
  return out;
}

/** 빠진 정답 쌍을 표 짝으로 묶는다. */
export function missingByTable(sheet: Sheet, verdicts: Verdict[]): Map<string, string[]> {
  const have = new Set(verdicts.map(key));
  const out = new Map<string, string[]>();
  for (const pair of sheet.pairs) if (!have.has(key(pair))) out.set(tableKey(pair), [...(out.get(tableKey(pair)) ?? []), key(pair)]);
  return out;
}

/** `cli7 runr` 가 쓴 answers.json 에서 채점이 읽는 부분. */
export interface RecoverAnswers {
  run: string;
  shiftRule: ShiftRule;
  nominations: Nomination[];
  candidates: { a: string; b: string; sharedRows: number; sharedCols: number }[];
  skipped: { pair: { a: string; b: string }; reasons: string[]; why: string }[];
  tableReview: { answered: { a_table: string; b_table: string }[] | null; accepted: { a: string; b: string }[]; rejected: Record<string, number> } | null;
  requests: { id: string; pair: { a: string; b: string }; reasons: string[]; kind: string; remaining: { a: number; b: number }; asked: number | null; alreadyPaired: number; answered: Record<string, number> | null; pairs: number; rejected: Record<string, number> }[];
  rejected: Record<string, number>;
  before: { verdicts: Verdict[] };
  checked: { verdicts: Verdict[]; dropped: Verdict[] };
  after: { verdicts: Verdict[]; tally: { a: { total: number; states: Record<string, number> }; b: { total: number; states: Record<string, number> } } };
  added: (Verdict & { request: string; tables: { a: string; b: string } })[];
  dropped: (Verdict & { from: "original" | "recovered" })[];
  repeated: number;
  conflicts: { cell: string; pairs: string[] }[];
  stillExpected: { pair: { a: string; b: string }; count: number }[];
}

export interface RunReport {
  name: string;
  /** 원답의 버려진 답 (사유별). */
  rejectedBefore: Record<string, number>;
  answers: RecoverAnswers;
  cost: { calls: number; inputTokens: number; cacheReadTokens: number; outputTokens: number; costUsd: number; maxInputTokens: number } | null;
  before: StageScore;
  checked: StageScore;
  after: StageScore;
  shift: { either: ShiftScore; both: ShiftScore };
  /** 원래 답에서 빠진 정답 쌍 (표 짝별). */
  missing: Map<string, string[]>;
  /** 빠진 정답 쌍이 든 표 짝 가운데 다시 물은 것 (요청이 만들어진 것) / 지목은 됐지만 요청이 없던 것 / 지목되지 않은 것. */
  recall: { asked: string[]; nominatedNotAsked: string[]; notNominated: string[] };
  /** 빠졌던 정답 쌍 가운데 다시 물은 뒤 짝지어진 수. */
  recoveredGold: number;
  /** 원래 답에 있던 정답 쌍 가운데 뒤에 없는 것 (나빠진 것). */
  lostGold: string[];
  /** 다시 받은 새 짝의 갈래: 정답 쌍 / 판정 같은 항목 / 판정 같은 항목 아님 / 값이 아닌 표시 / 판정 없음. */
  added: { gold: string[]; sameItem: string[]; differentItem: string[]; notAValue: string[]; unjudged: string[] };
}

export function reportRun(input: { name: string; sheet: Sheet; answers: RecoverAnswers; rejectedBefore: Record<string, number>; cost: RunReport["cost"]; a: Extraction; b: Extraction; units: UnitTable }): RunReport {
  const { sheet, answers } = input;
  const gold = new Set(sheet.pairs.map(key));
  const missing = missingByTable(sheet, answers.before.verdicts);
  const asked = new Set(answers.requests.map((request) => key(request.pair)));
  const nominated = new Set([...answers.nominations.map((item) => key(item.pair)), ...(answers.tableReview?.accepted ?? []).map(key)]);
  const recall: RunReport["recall"] = { asked: [], nominatedNotAsked: [], notNominated: [] };
  for (const table of [...missing.keys()].sort(byId)) (asked.has(table) ? recall.asked : nominated.has(table) ? recall.nominatedNotAsked : recall.notNominated).push(table);
  const afterKeys = new Set(answers.after.verdicts.map(key));
  const beforeKeys = new Set(answers.before.verdicts.map(key));
  const added: RunReport["added"] = { gold: [], sameItem: [], differentItem: [], notAValue: [], unjudged: [] };
  for (const pair of answers.added) {
    const judged = gold.has(key(pair)) ? "gold" : (sheet.judged.get(key(pair))?.verdict ?? null);
    (judged === "gold" ? added.gold : judged === "same_item" ? added.sameItem : judged === "different_item" ? added.differentItem : judged === "not_a_value" ? added.notAValue : added.unjudged).push(key(pair));
  }
  return {
    name: input.name,
    rejectedBefore: input.rejectedBefore,
    answers,
    cost: input.cost,
    before: scoreStage(sheet, answers.before.verdicts),
    checked: scoreStage(sheet, answers.checked.verdicts),
    after: scoreStage(sheet, answers.after.verdicts),
    shift: { either: scoreShift(sheet, answers.before.verdicts, input.a, input.b, input.units, "either"), both: scoreShift(sheet, answers.before.verdicts, input.a, input.b, input.units, "both") },
    missing,
    recall,
    recoveredGold: [...missing.values()].flat().filter((pair) => afterKeys.has(pair)).length,
    lostGold: sheet.pairs.map(key).filter((pair) => beforeKeys.has(pair) && !afterKeys.has(pair)),
    added,
  };
}

/** 같은 방법의 실행들을 합친다 (0원 대조군): 짝의 합집합. 같은 짝은 먼저 나온 실행의 결과를 쓴다 (같은 칸 짝의 값 비교는 실행과 무관하게 같다). */
export function unionVerdicts(runs: Verdict[][]): Verdict[] {
  const seen = new Map<string, Verdict>();
  for (const verdicts of runs) for (const verdict of verdicts) if (!seen.has(key(verdict))) seen.set(key(verdict), verdict);
  return [...seen.values()];
}

/** 판정할 짝의 목록: 표 칸끼리의 정답 밖 불일치(원래 답, 다시 물은 뒤), 검사가 가린 정답 밖의 짝(두 기준), 다시 받은 정답 밖의 새 짝. */
export function toJudge(sheet: Sheet, runs: RunReport[]): { a: string; b: string; why: string[]; runs: string[] }[] {
  const gold = new Set(sheet.pairs.map(key));
  const found = new Map<string, { why: Set<string>; runs: Set<string> }>();
  const put = (pair: string, why: string, run: string): void => {
    if (gold.has(pair)) return;
    const entry = found.get(pair) ?? { why: new Set(), runs: new Set() };
    entry.why.add(why);
    entry.runs.add(run);
    found.set(pair, entry);
  };
  const outside = (score: StageScore): string[] => [...score.outside.differentItem, ...score.outside.sameItem, ...score.outside.notAValue, ...score.outside.unjudged];
  for (const run of runs) {
    for (const pair of outside(run.before)) put(pair, "원래 답의 표 칸 불일치", run.name);
    for (const pair of outside(run.after)) put(pair, "다시 물은 뒤의 표 칸 불일치", run.name);
    for (const rule of ["either", "both"] as const) for (const check of run.shift[rule].checks) if (check.verdict === "suspect") put(key(check), `검사가 가림 (${rule})`, run.name);
    for (const pair of run.answers.added) put(key(pair), "다시 받은 새 짝", run.name);
    for (const pair of run.answers.dropped) if (pair.from === "recovered") put(key(pair), "다시 받은 짝이 의심으로 빠짐", run.name);
  }
  return [...found.entries()].sort(([x], [y]) => byId(x, y)).map(([pair, entry]) => ({ a: pair.split("|")[0] as string, b: pair.split("|")[1] as string, why: [...entry.why].sort(), runs: [...entry.runs] }));
}

// ── 표 ──────────────────────────────────────────────────────────────────────

const JUDGED: Record<string, string> = { same_item: "같은 항목", different_item: "같은 항목 아님", not_a_value: "값이 아닌 표시" };
const row = (cells: (string | number)[]): string => `| ${cells.join(" | ")} |`;
const table = (head: string[], rows: (string | number)[][]): string[] => [row(head), row(head.map(() => "---")), ...rows.map(row)];
const bag = (counts: Record<string, number>): string => Object.entries(counts).sort(([x], [y]) => x.localeCompare(y)).map(([name, count]) => `${name} ${count}`).join(", ") || "-";
const usd = (value: number): string => value.toFixed(6);
const REASON_KO: Record<string, string> = { rejected_answer: "(i) 버려진 답", shift_suspect: "(ii) 열 밀림", expected_by_header: "(iii) 머리글로 보이는 남은 칸", new_table_pair: "(iv) 표 검수의 새 표 짝" };

export function renderRecover(sheet: Sheet, runs: RunReport[], unions: { name: string; before: StageScore; after: StageScore }[], only: "inputs" | "all" = "all"): string {
  const lines: string[] = ["# 밴드 Y: 열 밀림 검사와 빠진 표 되찾기 (방법 R)", "", "저장된 D 계열 실행의 답 위에서 낸 표다. 「전」은 그 실행의 원래 답, 「검사만」은 열 밀림 검사로 의심 짝을 뺀 것(호출 0), 「뒤」는 지목된 표 짝을 다시 물어 합친 것이다.", ""];
  const false1 = (score: StageScore): number => score.outside.differentItem.length + score.outside.notAValue.length;

  lines.push("## 1. 실행의 입력 (호출 0)", "");
  lines.push(
    ...table(
      ["실행", "받아들여진 짝 (전체 / 표 칸끼리)", "버려진 답", "짝지어진 정답 쌍 (58쌍 중)", "빠진 정답 쌍이 든 표 짝 (빠진 수)"],
      runs.map((run) => [run.name, `${run.before.compared} / ${run.before.comparedCells}`, bag(run.rejectedBefore), `${run.before.goldPaired}/${sheet.pairs.length}`, [...run.missing.entries()].map(([name, list]) => `${name} (${list.length})`).join(", ") || "-"]),
    ),
    "",
  );

  for (const rule of ["either", "both"] as const) {
    lines.push(`## 2. 열 밀림 검사: 기준 「${rule}」 ${rule === "either" ? "(첫 채점 때의 기준)" : "(한 번 고친 기준. 방법 R 이 쓴 것)"}`, "");
    lines.push(
      ...table(
        ["실행", "표 칸 짝", "정상", "판단 못 함 (그중 머리글 없음)", "의심 (가림)", "가린 것 중 정답 쌍 (잘못 가림)", "가린 것 중 판정 「같은 항목」 (잘못 가림)", "가린 것 중 판정 「같은 항목 아님」", "가린 것 중 판정 없음", "틀린 짝(판정 「같은 항목 아님」) 중 가린 것"],
        runs.map((run) => {
          const score = run.shift[rule];
          return [run.name, score.cellPairs, `${score.ok}/${score.cellPairs}`, `${score.undecided}/${score.cellPairs} (${score.undecidedNoHeader})`, `${score.suspect}/${score.cellPairs}`, `${score.suspectGold.length}/${score.suspect}`, `${score.suspectSame.length}/${score.suspect}`, `${score.suspectDifferent.length}/${score.suspect}`, `${score.suspectUnjudged.length}/${score.suspect}`, `${score.wrongFlagged.length}/${score.wrongPairs.length}`];
        }),
      ),
      "",
    );
    const sum = (pick: (score: ShiftScore) => number): number => runs.reduce((total, run) => total + pick(run.shift[rule]), 0);
    lines.push(`합: 표 칸 짝 ${sum((s) => s.cellPairs)}개 중 정상 ${sum((s) => s.ok)}, 판단 못 함 ${sum((s) => s.undecided)}(그중 머리글 없음 ${sum((s) => s.undecidedNoHeader)}), 의심 ${sum((s) => s.suspect)}. 의심 ${sum((s) => s.suspect)}개 중 정답 쌍 ${sum((s) => s.suspectGold.length)}, 판정 「같은 항목」 ${sum((s) => s.suspectSame.length)}, 「같은 항목 아님」 ${sum((s) => s.suspectDifferent.length)}, 판정 없음 ${sum((s) => s.suspectUnjudged.length)}. 틀린 짝 ${sum((s) => s.wrongPairs.length)}개(실행별로 센 합) 중 가린 것 ${sum((s) => s.wrongFlagged.length)}.`, "");
  }

  if (only === "inputs") {
    lines.push("## 8. 표 칸끼리의 정답 밖 불일치와 가린 짝 (판정)", "");
    lines.push(...table(["보고서 칸", "데이터북 칸", "왜 목록에 있는가", "나온 실행", "판정", "근거"], toJudge(sheet, runs).map((item) => [`\`${item.a}\``, `\`${item.b}\``, item.why.join(", "), item.runs.join(" "), JUDGED[sheet.judged.get(key(item))?.verdict ?? ""] ?? "판정 없음", (sheet.judged.get(key(item))?.basis ?? "").replace(/\|/g, "/")])), "");
    return `${lines.join("\n")}\n`;
  }

  lines.push("## 3. 방법 R: 실행별 전 → 검사만 → 뒤", "");
  lines.push(
    ...table(
      ["실행", "참 일치 (42쌍 중)", "잡은 오류 (15칸 중)", "거짓 일치 (15칸 중)", "짝지어진 정답 쌍 (58쌍 중)", "표 칸 거짓 경보 (개수)", "표 칸 불일치 중 판정 없음", "견준 짝", "비용 USD"],
      runs.map((run) => {
        const three = (pick: (score: StageScore) => number): string => `${pick(run.before)} → ${pick(run.checked)} → ${pick(run.after)}`;
        return [run.name, three((s) => s.cleanMatch), three((s) => s.caught), three((s) => s.falseMatch), three((s) => s.goldPaired), three(false1), three((s) => s.outside.unjudged.length), three((s) => s.compared), run.cost ? usd(run.cost.costUsd) : "0 (요청 없음)"];
      }),
    ),
    "",
  );

  lines.push("## 4. 지목과 다시 묻기", "");
  lines.push(
    ...table(
      ["실행", "지목 (i)~(iii) 의 표 짝 (갈래)", "건너뜀 (물을 칸 없음)", "(iv) 후보 표 짝 → 표 검수의 답 → 받아들임", "요청 (종류: 물은 칸 → 답)", "빠진 정답 쌍이 든 표 짝: 다시 물음 / 지목만 / 지목 안 됨", "되찾은 정답 쌍 / 빠졌던 정답 쌍", "다시 묻고도 머리글로 상대가 보이는 남은 칸"],
      runs.map((run) => {
        const answers = run.answers;
        const missed = [...run.missing.values()].flat().length;
        const tableReview = answers.tableReview ? `${answers.candidates.map(key).join(", ")} → ${answers.tableReview.answered ? answers.tableReview.answered.map((pair) => `${pair.a_table}|${pair.b_table}`).join(", ") || "(빈 답)" : "(답 없음)"} → ${answers.tableReview.accepted.map(key).join(", ") || "0"}${Object.keys(answers.tableReview.rejected).length > 0 ? ` (버림: ${bag(answers.tableReview.rejected)})` : ""}` : "후보 없음";
        const requests = answers.requests.map((request) => `${key(request.pair)} [${request.reasons.map((reason) => REASON_KO[reason] ?? reason).join(" + ")}] ${request.kind === "review" ? `칸 검수: ${request.asked}칸 → ${bag(request.answered ?? {})}, 새 짝 ${request.pairs}` : `칸 짝: 남은 칸 ${request.remaining.a}/${request.remaining.b} → 짝 ${request.pairs}`}${Object.keys(request.rejected).length > 0 ? ` (버려진 답: ${bag(request.rejected)})` : ""}`);
        return [run.name, answers.nominations.map((item) => `${key(item.pair)} [${item.reasons.map((reason) => REASON_KO[reason] ?? reason).join(" + ")}]`).join("; ") || "-", answers.skipped.map((item) => key(item.pair)).join(", ") || "-", tableReview, requests.join("; ") || "-", `${run.recall.asked.length} / ${run.recall.nominatedNotAsked.length} / ${run.recall.notNominated.length} (${run.missing.size}개 중)`, `${run.recoveredGold}/${missed}`, answers.stillExpected.map((item) => `${key(item.pair)}: ${item.count}`).join(", ") || "-"];
      }),
    ),
    "",
  );

  lines.push("## 5. 다시 받은 짝과 합치기", "");
  lines.push(
    ...table(
      ["실행", "새 짝 (원래 답에 없던 것)", "그중 정답 쌍", "정답 밖: 같은 항목", "정답 밖: 같은 항목 아님 (틀린 새 짝)", "정답 밖: 값이 아닌 표시", "정답 밖: 판정 없음", "원래 답과 같은 짝 (되풀이)", "의심으로 뺀 짝 (원래 것 / 다시 받은 것)", "한 칸이 두 상대와 (엇갈림)", "원래 있던 정답 쌍 중 사라진 것"],
      runs.map((run) => [run.name, run.answers.added.length, `${run.added.gold.length}/${run.answers.added.length}`, `${run.added.sameItem.length}/${run.answers.added.length}`, `${run.added.differentItem.length}/${run.answers.added.length}`, `${run.added.notAValue.length}/${run.answers.added.length}`, `${run.added.unjudged.length}/${run.answers.added.length}`, run.answers.repeated, `${run.answers.dropped.filter((pair) => pair.from === "original").length} / ${run.answers.dropped.filter((pair) => pair.from === "recovered").length}`, run.answers.conflicts.length, `${run.lostGold.length}/${run.before.goldPaired}`]),
    ),
    "",
  );

  lines.push("## 6. 0원 대조군: 같은 방법의 세 실행을 합친 것", "");
  lines.push(
    ...table(
      ["합친 것", "참 일치 (42쌍 중): 원래 답 / 방법 R 뒤", "잡은 오류 (15칸 중)", "거짓 일치 (15칸 중)", "짝지어진 정답 쌍 (58쌍 중)", "표 칸 거짓 경보 (개수)", "견준 짝"],
      unions.map((union) => {
        const two = (pick: (score: StageScore) => number): string => `${pick(union.before)} / ${pick(union.after)}`;
        return [union.name, two((s) => s.cleanMatch), two((s) => s.caught), two((s) => s.falseMatch), two((s) => s.goldPaired), two(false1), two((s) => s.compared)];
      }),
    ),
    "",
  );

  lines.push("## 7. 셈: 다시 물은 뒤 모든 값 후보의 결과 (합이 값 후보 수와 같다)", "");
  const states = [...new Set(runs.flatMap((run) => [...Object.keys(run.answers.after.tally.a.states), ...Object.keys(run.answers.after.tally.b.states)]))].sort();
  lines.push(...table(["결과", ...runs.flatMap((run) => [`${run.name} A`, `${run.name} B`])], [...states.map((state) => [state, ...runs.flatMap((run) => [run.answers.after.tally.a.states[state] ?? 0, run.answers.after.tally.b.states[state] ?? 0])]), ["합", ...runs.flatMap((run) => [run.answers.after.tally.a.total, run.answers.after.tally.b.total])]]), "");

  lines.push("## 8. 표 칸끼리의 정답 밖 불일치와 가린 짝, 새 짝 (판정)", "");
  const listed = toJudge(sheet, runs);
  lines.push(...table(["보고서 칸", "데이터북 칸", "왜 목록에 있는가", "나온 실행", "판정", "근거"], listed.map((item) => [`\`${item.a}\``, `\`${item.b}\``, item.why.join(", "), item.runs.join(" "), JUDGED[sheet.judged.get(key(item))?.verdict ?? ""] ?? "판정 없음", (sheet.judged.get(key(item))?.basis ?? "").replace(/\|/g, "/")])), "");
  return `${lines.join("\n")}\n`;
}
