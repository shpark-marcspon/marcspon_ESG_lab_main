import { z } from "zod";
import type { GoldPair, Plant } from "./exam.js";
import { renderComparison, type MethodGrade, type PairStatus } from "./grade.js";
import type { CandidateTally, TablePair } from "./hybrid.js";
import type { MethodOutcome, Verdict } from "./methods.js";

// 반복 실행의 비교 (MARC-TICK-22). 같은 방법을 여러 번 돌린 결과를 나란히 놓는다. 모델을 부르지 않는다.
// 수는 전부 분모와 같이 낸다.

/** 실행 하나: 방법, 몇째 실행인지, 그 실행의 결과와 채점. */
export interface Run {
  method: string;
  run: number;
  outcome: MethodOutcome;
  grade: MethodGrade;
  /** 방법 G 만: 그 실행이 낸 표 짝과 값 후보의 셈. */
  tablePairs?: TablePair[];
  tally?: { a: CandidateTally; b: CandidateTally };
}

const key = (verdict: { a: string; b: string }): string => `${verdict.a}|${verdict.b}`;

export interface SetStats {
  perRun: number[];
  intersection: number;
  union: number;
  /** `inRuns[k]` = 정확히 k+1 개의 실행에 나온 것의 수. */
  inRuns: number[];
}

export function setStats(sets: Set<string>[]): SetStats {
  const count = new Map<string, number>();
  for (const set of sets) for (const item of set) count.set(item, (count.get(item) ?? 0) + 1);
  const inRuns = sets.map((_, index) => [...count.values()].filter((n) => n === index + 1).length);
  return { perRun: sets.map((set) => set.size), intersection: inRuns[sets.length - 1] ?? 0, union: count.size, inRuns };
}

/** 세 실행이 낸 짝의 교집합과 합집합: 견준 짝 전체, 그중 일치, 그중 불일치. */
export function pairStability(outcomes: MethodOutcome[]): { compared: SetStats; match: SetStats; mismatch: SetStats } {
  const pick = (want: (verdict: Verdict) => boolean): SetStats => setStats(outcomes.map((outcome) => new Set(outcome.verdicts.filter(want).map(key))));
  return { compared: pick(() => true), match: pick((verdict) => verdict.outcome === "match"), mismatch: pick((verdict) => verdict.outcome === "mismatch") };
}

export interface PlantedRow {
  plant: string;
  kind: string;
  isError: boolean;
  cell: string;
  statuses: PairStatus[];
  /** 오류 칸이면 불일치로 잡힌 실행 수, 대조군이면 일치로 나온 실행 수. */
  good: number;
}

/** 심은 칸 하나가 실행마다 어떻게 나왔는가. */
export function plantedByRun(plants: Plant[], grades: MethodGrade[]): PlantedRow[] {
  return plants.flatMap((plant) =>
    plant.cells.map((cell) => {
      const statuses = grades.map((item) => [...item.grade.errors.rows, ...item.grade.control.rows].find((row) => row.pair === cell.pair)?.status ?? "silent");
      return { plant: plant.id, kind: plant.kind, isError: plant.isError, cell: cell.cell, statuses, good: statuses.filter((status) => status === (plant.isError ? "mismatch" : "match")).length };
    }),
  );
}

export interface OutsideMatch {
  a: string;
  b: string;
  /** 이 짝을 일치로 낸 실행 (`D#1` 꼴). */
  runs: string[];
}

/** 정답 쌍 밖에서 「일치」로 나온 짝의 합집합. 원문으로 판정할 목록이다. */
export function outsideGoldMatches(pairs: GoldPair[], runs: Run[]): OutsideMatch[] {
  const gold = new Set(pairs.map(key));
  const found = new Map<string, OutsideMatch>();
  for (const run of runs) {
    for (const verdict of run.outcome.verdicts) {
      if (verdict.outcome !== "match" || gold.has(key(verdict))) continue;
      const entry = found.get(key(verdict)) ?? { a: verdict.a, b: verdict.b, runs: [] };
      entry.runs.push(`${run.method.toUpperCase()}#${run.run}`);
      found.set(key(verdict), entry);
    }
  }
  return [...found.values()].sort((x, y) => key(x).localeCompare(key(y), "en", { numeric: true }));
}

export interface OnlyInBase {
  a: string;
  b: string;
  /** 기준 방법의 실행 가운데 이 짝을 견준 것 (`D#1` 꼴)과 그 결과. */
  base: { run: string; outcome: Verdict["outcome"] }[];
  inGold: boolean;
  /** 다른 방법의 실행마다, 이 짝의 두 칸이 어떤 상태였는가 (견줌이면 `compared`). */
  other: { run: string; a: string; b: string }[];
}

/** 기준 방법(D)은 견줬는데 다른 방법(G)은 어느 실행에서도 견주지 못한 짝과, 그 칸들이 다른 방법에서 어떤 상태였는지. */
export function comparedOnlyInBase(pairs: GoldPair[], base: Run[], other: Run[]): OnlyInBase[] {
  const gold = new Set(pairs.map(key));
  const inOther = new Set(other.flatMap((run) => run.outcome.verdicts.map(key)));
  const found = new Map<string, OnlyInBase>();
  for (const run of base) {
    for (const verdict of run.outcome.verdicts) {
      if (inOther.has(key(verdict))) continue;
      const entry = found.get(key(verdict)) ?? {
        a: verdict.a,
        b: verdict.b,
        base: [],
        inGold: gold.has(key(verdict)),
        other: other.map((item) => ({ run: `${item.method.toUpperCase()}#${item.run}`, a: item.outcome.states.a[verdict.a] ?? "compared", b: item.outcome.states.b[verdict.b] ?? "compared" })),
      };
      entry.base.push({ run: `${run.method.toUpperCase()}#${run.run}`, outcome: verdict.outcome });
      found.set(key(verdict), entry);
    }
  }
  return [...found.values()].sort((x, y) => key(x).localeCompare(key(y), "en", { numeric: true }));
}

/** 정답 밖 일치의 판정 기록. 러너가 원문 PDF 로 본 것이다. */
export const judgmentsSchema = z.object({
  note: z.string(),
  /** 누가 판정했고 검토를 받았는가. */
  status: z.string().min(1),
  judgments: z.array(
    z.object({
      a: z.string(),
      b: z.string(),
      /** same: 같은 항목·같은 기간·같은 범위. not_same: 같은 항목이 아닌데 값이 같아 일치로 나왔다. not_a_value: 두 칸이 같은 자리의 표시(연도 등)이고 값이 아니다. unclear: 원문으로 가를 수 없다. */
      verdict: z.enum(["same", "not_same", "not_a_value", "unclear"]),
      /** 근거: 원문 PDF 의 쪽, 행 이름, 열(기간). */
      evidence: z.object({ a: z.object({ page: z.number().int(), row: z.string(), column: z.string() }), b: z.object({ page: z.number().int(), row: z.string(), column: z.string() }) }),
      note: z.string().nullable(),
    }),
  ),
});
export type Judgments = z.infer<typeof judgmentsSchema>;

const STATUS_KO: Record<PairStatus, string> = { match: "일치", mismatch: "불일치", needs_explanation: "설명 필요", visible_elsewhere: "다른 칸과 견주어져 드러남", matched_elsewhere: "다른 칸과 일치", silent: "조용히 지나감" };
const label = (run: Run): string => `${run.method.toUpperCase()}#${run.run}`;
const sum = (bag: Record<string, number>): number => Object.values(bag).reduce((total, count) => total + count, 0);

/** 반복 실행의 비교를 마크다운으로. `judgments` 가 없으면 정답 밖 일치는 목록만 낸다. */
export function renderRuns(runs: Run[], pairs: GoldPair[], plants: Plant[], judgments: Judgments | null): string {
  const methods = [...new Set(runs.map((run) => run.method))];
  const of = (method: string): Run[] => runs.filter((run) => run.method === method).sort((x, y) => x.run - y.run);
  const lines: string[] = [renderComparison(runs.map((run) => ({ ...run.grade, method: label(run) })), plants).replace("## 방법 × 항목 비교표", "## 방법 × 실행 비교표").replace("## 심은 오류별 × 방법별", "## 심은 칸 × 실행")];

  lines.push("## 심은 칸이 실행 중 몇 번 잡혔는가", "", `| 오류 | 종류 | 칸 | ${methods.map((method) => `${method.toUpperCase()} (불일치로 잡힌 실행 / 실행 수)`).join(" | ")} |`, `|---|---|---|${methods.map(() => "---:").join("|")}|`);
  const planted = new Map(methods.map((method) => [method, plantedByRun(plants, of(method).map((run) => run.grade))]));
  (planted.get(methods[0] as string) ?? []).forEach((row, index) => {
    lines.push(`| ${row.plant}${row.isError ? "" : " (대조군: 일치로 나온 실행)"} | ${row.kind} | ${row.cell} | ${methods.map((method) => `${planted.get(method)?.[index]?.good}/${of(method).length}`).join(" | ")} |`);
  });
  lines.push(`| **실행마다 전부 잡힌 오류 칸** | | | ${methods.map((method) => { const rows = (planted.get(method) ?? []).filter((row) => row.isError); return `${rows.filter((row) => row.good === of(method).length).length}/${rows.length}`; }).join(" | ")} |`);

  lines.push("", "## 실행이 낸 짝의 교집합과 합집합", "", "| 방법 | 갈래 | 실행별 수 | 교집합 (모든 실행에 나옴) | 합집합 | 한 실행에만 / 두 실행에만 … |", "|---|---|---|---:|---:|---|");
  for (const method of methods) {
    const stability = pairStability(of(method).map((run) => run.outcome));
    for (const [name, stats] of [["견준 짝", stability.compared], ["그중 일치", stability.match], ["그중 불일치", stability.mismatch]] as const) lines.push(`| ${method.toUpperCase()} | ${name} | ${stats.perRun.join(" / ")} | ${stats.intersection} | ${stats.union} | ${stats.inRuns.slice(0, -1).join(" / ")} |`);
  }

  const hybrid = runs.filter((run) => run.tablePairs && run.tally);
  if (hybrid.length > 0) {
    const all = [...new Set(hybrid.flatMap((run) => (run.tablePairs ?? []).map(key)))].sort((x, y) => x.localeCompare(y, "en", { numeric: true }));
    lines.push("", "## G 의 표 짝 (실행별)", "", `| 표 짝 (A | B) | ${hybrid.map(label).join(" | ")} |`, `|---|${hybrid.map(() => "---").join("|")}|`);
    for (const pair of all) lines.push(`| ${pair.replace("|", " ↔ ")} | ${hybrid.map((run) => ((run.tablePairs ?? []).some((item) => key(item) === pair) ? "있음" : "없음")).join(" | ")} |`);
    lines.push("", "## G 의 셈: 모든 값 후보가 결과 하나에", "", `| 결과 | ${hybrid.flatMap((run) => [`${label(run)} 문서 A`, `${label(run)} 문서 B`]).join(" | ")} |`, `|---|${hybrid.flatMap(() => ["---:", "---:"]).join("|")}|`);
    const cell = (pick: (tally: CandidateTally) => number): string => hybrid.flatMap((run) => (["a", "b"] as const).map((side) => { const tally = (run.tally as NonNullable<Run["tally"]>)[side]; return `${pick(tally)}/${tally.total}`; })).join(" | ");
    lines.push(`| 견줌: 일치 | ${cell((tally) => tally.compared.match)} |`, `| 견줌: 불일치 | ${cell((tally) => tally.compared.mismatch)} |`, `| 견줌: 설명 필요 | ${cell((tally) => tally.compared.needsExplanation)} |`, `| 확인 못 함: 짝지어진 표 안인데 칸 짝이 안 나옴 | ${cell((tally) => tally.noCellPairInPairedTable)} |`, `| 확인 못 함: 표 짝 없음 | ${cell((tally) => tally.tableNotPaired)} |`, `| 확인 못 함: 표의 숫자 칸이 아님 (문장 속, 숫자뿐인 블록, 표의 글 칸 속) | ${cell((tally) => sum(tally.outsideTables))} |`, `| 확인 못 함: 이 칸을 가리킨 답이 버려짐 | ${cell((tally) => tally.answerRejected)} |`);
  }

  const targets = runs.filter((run) => run.method === "d" || run.method === "g");
  const outside = outsideGoldMatches(pairs, targets);
  const judged = new Map((judgments?.judgments ?? []).map((item) => [key(item), item]));
  const count = (verdict: string): number => outside.filter((item) => judged.get(key(item))?.verdict === verdict).length;
  lines.push("", "## 정답 쌍 밖의 일치 (D 와 G 의 모든 실행의 합집합)", "", `합집합 ${outside.length}쌍. 판정: 같은 항목 ${count("same")}/${outside.length}, 같은 항목 아님(정답 밖 거짓 일치) ${count("not_same")}/${outside.length}, 값이 아닌 표시끼리 ${count("not_a_value")}/${outside.length}, 가를 수 없음 ${count("unclear")}/${outside.length}, 판정 없음 ${outside.filter((item) => !judged.has(key(item))).length}/${outside.length}.`, "", "| A 칸 | B 칸 | 일치로 낸 실행 | 판정 | 근거 A (쪽, 행, 열) | 근거 B (쪽, 행, 열) | 비고 |", "|---|---|---|---|---|---|---|");
  const VERDICT_KO = { same: "같은 항목", not_same: "같은 항목 아님", not_a_value: "값이 아닌 표시끼리", unclear: "가를 수 없음" } as const;
  for (const item of outside) {
    const judgment = judged.get(key(item));
    const evidence = (side: "a" | "b"): string => (judgment ? `${judgment.evidence[side].page}쪽, ${judgment.evidence[side].row}, ${judgment.evidence[side].column}` : "-");
    lines.push(`| ${item.a} | ${item.b} | ${item.runs.join(" ")} | ${judgment ? VERDICT_KO[judgment.verdict] : "판정 없음"} | ${evidence("a")} | ${evidence("b")} | ${judgment?.note ?? ""} |`);
  }

  if (of("d").length > 0 && of("g").length > 0) {
    const only = comparedOnlyInBase(pairs, of("d"), of("g"));
    lines.push("", "## D 는 견줬는데 G 는 어느 실행에서도 견주지 못한 짝", "", `${only.length}쌍 (그중 정답 쌍 ${only.filter((item) => item.inGold).length}쌍).`, "", "| A 칸 | B 칸 | 정답 쌍 | D 의 결과 | G 에서 A 칸의 상태 (실행별) | G 에서 B 칸의 상태 (실행별) |", "|---|---|---|---|---|---|");
    for (const item of only) lines.push(`| ${item.a} | ${item.b} | ${item.inGold ? "예" : "아니오"} | ${item.base.map((entry) => `${entry.run} ${STATUS_KO[entry.outcome]}`).join(", ")} | ${item.other.map((entry) => entry.a).join(" / ")} | ${item.other.map((entry) => entry.b).join(" / ")} |`);
  }
  return `${lines.join("\n")}\n`;
}
