import { z } from "zod";
import type { Extraction } from "../domain/facts.js";
import { ASK_REASONS, BRANCHES, BRANCH_NAME, signature, stability, targetKey, type AskState, type AskTally, type AskVerdict, type Branch, type Target } from "./ask.js";
import { prng, type GoldPair } from "./exam.js";
import type { Gold2, Plant2 } from "./exam2.js";
import type { PairStatus } from "./grade.js";
import { gradeProse, type CostSummary, type Planted, type ProseGrade } from "./prose-report.js";

// 밴드 X 의 채점과 표 (방법 Q). 정답(서술 쌍 21, 밴드 R 의 정답 쌍)과 심은 것으로 저장된 답을 센다. 모델을 부르지 않는다. 수는 전부 분모와 같이 낸다.
// 대상 목록과 요청 조립(`ask.ts`, `cli5.ts`)은 이 파일을 쓰지 않는다.

type Side = "a" | "b";

// ── 대상 목록과 정답의 겹침 (호출 0) ─────────────────────────────────────────

export interface CoverageSide {
  id: string;
  /** 이 자리가 대상이면 그 갈래. */
  branch: Branch | null;
  /** 대상이 아닌 까닭 (값 후보의 갈래와 종류, 라벨·이름의 유무로 본다). */
  why: string | null;
}

export interface Coverage {
  pairs: { pair: string; item: string; a: CoverageSide; b: CoverageSide; reachable: boolean }[];
  /** 한쪽이라도 대상인 쌍 / 전체. */
  reachable: number;
  total: number;
  /** 서술에 심은 오류: 심은 자리가 대상인가, 그 쌍의 어느 한쪽이 대상인가. */
  plants: { plant: string; kind: string; target: string; branch: Branch | null; pairs: string[]; reachable: boolean }[];
  /** 정답 쌍의 자리(중복 없이) 가운데 대상인 것과 아닌 것(까닭별). */
  places: { total: number; targets: number; missing: Record<string, string[]> };
}

/** 대상이 아닌 까닭. 대상 규칙을 다시 적용하지 않고, 그 값 후보의 갈래·종류·라벨·이름만 읽어 말로 옮긴다. */
function whyNot(extraction: Extraction, id: string): string {
  const fact = extraction.facts.find((item) => item.id === id);
  if (!fact) return "값 후보가 아니다";
  if (fact.bucket === "table_cell") return "표의 숫자 칸 (코드 라벨 없음. 표 칸은 이 방법의 대상이 아니다)";
  if (fact.bucket === "table_text_cell") return "표의 글 칸 속 숫자인데 코드 라벨이 없다";
  if (fact.bucket === "number_only_block") return "숫자뿐인 블록인데 이름이 묶이지 않았다";
  return `문장 속 숫자인데 종류 옵션이 「${extraction.kinds?.[id] ?? "?"}」로 보았다`;
}

export function coverage(gold: Gold2, plants: Plant2[], targets: Target[], facts: Record<Side, Extraction>): Coverage {
  const branchOf = new Map(targets.map((target) => [targetKey(target), target.branch]));
  const side = (doc: Side, id: string): CoverageSide => {
    const branch = branchOf.get(`${doc}:${id}`) ?? null;
    return { id, branch, why: branch ? null : whyNot(facts[doc], id) };
  };
  const pairs = gold.prosePairs.map((pair) => {
    const a = side("a", pair.a);
    const b = side("b", pair.b);
    return { pair: pair.id, item: pair.item, a, b, reachable: a.branch !== null || b.branch !== null };
  });
  const reachablePairs = new Set(pairs.filter((pair) => pair.reachable).map((pair) => pair.pair));
  const places = new Map<string, CoverageSide>();
  for (const pair of pairs) {
    places.set(`a:${pair.a.id}`, pair.a);
    places.set(`b:${pair.b.id}`, pair.b);
  }
  const missing: Record<string, string[]> = {};
  for (const [key, place] of places) if (place.why !== null) missing[place.why] = [...(missing[place.why] ?? []), key];
  return {
    pairs,
    reachable: reachablePairs.size,
    total: pairs.length,
    plants: plants.filter((plant) => plant.isError && plant.pairs.length > 0).map((plant) => ({ plant: plant.id, kind: plant.kind, target: `${plant.doc}:${plant.target}`, branch: branchOf.get(`${plant.doc}:${plant.target}`) ?? null, pairs: plant.pairs, reachable: plant.pairs.some((pair) => reachablePairs.has(pair)) })),
    places: { total: places.size, targets: [...places.values()].filter((place) => place.branch !== null).length, missing },
  };
}

// ── 실행 하나의 채점 ─────────────────────────────────────────────────────────

export interface AskRun {
  name: string;
  targets: Target[];
  states: Map<string, AskState>;
  verdicts: AskVerdict[];
  rejected: Record<string, number>;
  tally: AskTally;
  cost: CostSummary | null;
}

export type PlantStatus = "caught" | "visible_elsewhere" | "false_match" | "silent";
export type GoldTargetStatus = "found_all" | "found_some" | "paired_elsewhere" | "none" | "unanswered" | "rejected";

export interface AskGrade {
  prose: ProseGrade;
  /** 서술에 심은 오류마다: 불일치로 잡음 / 다른 자리와 견주어져 드러남 / 일치로 나옴 / 조용히 지나감. */
  plants: { plant: string; branch: Branch | null; pairs: string[]; status: PlantStatus }[];
  /** 정답에 상대가 있는 대상마다 그 답. `none` 이 「없음」의 놓침이다. */
  goldTargets: { target: string; branch: Branch; expected: string[]; status: GoldTargetStatus; reason: string | null }[];
  /** 답한 짝 가운데 서술 쌍도 밴드 R 의 정답 쌍도 아닌 것 (원문으로 판정할 대상). */
  outside: AskVerdict[];
  /** 답한 짝 가운데 밴드 R 의 정답 쌍(표 칸끼리)에 든 것. */
  inTableGold: AskVerdict[];
}

const pairKey = (pair: { a: string; b: string }): string => `${pair.a}|${pair.b}`;

export function gradeAsk(run: AskRun, gold: Gold2, v1Pairs: GoldPair[], planted: Planted[], plants: Plant2[], prose: (doc: Side, id: string) => boolean): AskGrade {
  const graded = gradeProse(gold, planted, run.verdicts, prose);
  const branchOf = new Map(run.targets.map((target) => [targetKey(target), target.branch]));
  const rowOf = new Map(graded.rows.map((row) => [row.pair, row.status]));
  const plantStatus = (pairs: string[]): PlantStatus => {
    const statuses = pairs.map((pair) => rowOf.get(pair) as PairStatus);
    if (statuses.includes("mismatch")) return "caught";
    if (statuses.includes("visible_elsewhere")) return "visible_elsewhere";
    if (statuses.includes("match") || statuses.includes("matched_elsewhere")) return "false_match";
    return "silent";
  };
  const expected = new Map<string, string[]>();
  for (const pair of gold.prosePairs) {
    for (const [self, other] of [[`a:${pair.a}`, `b:${pair.b}`], [`b:${pair.b}`, `a:${pair.a}`]] as const) if (branchOf.has(self)) expected.set(self, [...(expected.get(self) ?? []), other]);
  }
  const goldTargets = [...expected].map(([target, wanted]) => {
    const state = run.states.get(target) as AskState;
    let status: GoldTargetStatus;
    if (state.state === "paired") {
      const hit = wanted.filter((id) => state.counterparts.includes(id)).length;
      status = hit === wanted.length ? "found_all" : hit > 0 ? "found_some" : "paired_elsewhere";
    } else status = state.state === "none" ? "none" : state.state === "unanswered" ? "unanswered" : "rejected";
    return { target, branch: branchOf.get(target) as Branch, expected: wanted, status, reason: state.state === "none" ? state.reason : null };
  });
  const proseKeys = new Set(gold.prosePairs.map(pairKey));
  const tableKeys = new Set(v1Pairs.map(pairKey));
  return {
    prose: graded,
    plants: plants.filter((plant) => plant.isError && plant.pairs.length > 0).map((plant) => ({ plant: plant.id, branch: branchOf.get(`${plant.doc}:${plant.target}`) ?? null, pairs: plant.pairs, status: plantStatus(plant.pairs) })),
    goldTargets,
    outside: run.verdicts.filter((verdict) => !proseKeys.has(pairKey(verdict)) && !tableKeys.has(pairKey(verdict))),
    inTableGold: run.verdicts.filter((verdict) => tableKeys.has(pairKey(verdict))),
  };
}

// ── 판정할 것과 판정 기록 ────────────────────────────────────────────────────

/** 일치가 이 수를 넘으면 씨앗 고정 표본만 판정한다. 불일치와 설명 필요는 전부 판정한다. */
export const MATCH_SAMPLE_SIZE = 60;
export const MATCH_SAMPLE_SEED = 20261004;

export interface ToJudge {
  /** 정답 밖의 짝의 합집합 (실행들에 걸쳐). `judge` 가 참인 것이 판정 대상이다. */
  pairs: { a: string; b: string; outcomes: Record<string, string>; from: string[]; judge: boolean }[];
  matchOnly: { total: number; sampled: number; seed: number };
  /** 요약 숫자 대상: 실행마다의 답 (전부 판정한다). */
  summary: { target: string; name: string | null; answers: Record<string, string> }[];
}

/** 원문으로 판정할 것. 어느 실행에서든 불일치·설명 필요로 나온 짝은 전부, 일치로만 나온 짝은 60쌍을 넘으면 씨앗 고정 표본 60쌍. */
export function toJudgeAsk(runs: { run: AskRun; grade: AskGrade }[]): ToJudge {
  const bag = new Map<string, { a: string; b: string; outcomes: Record<string, string>; from: Set<string> }>();
  for (const { run, grade } of runs) {
    for (const verdict of grade.outside) {
      const entry = bag.get(pairKey(verdict)) ?? { a: verdict.a, b: verdict.b, outcomes: {}, from: new Set<string>() };
      entry.outcomes[run.name] = verdict.outcome;
      for (const target of verdict.from) entry.from.add(target);
      bag.set(pairKey(verdict), entry);
    }
  }
  const all = [...bag.values()].sort((x, y) => pairKey(x).localeCompare(pairKey(y), "en"));
  const matchOnly = all.filter((entry) => Object.values(entry.outcomes).every((outcome) => outcome === "match"));
  const picked = new Set<string>();
  if (matchOnly.length > MATCH_SAMPLE_SIZE) {
    const random = prng(MATCH_SAMPLE_SEED);
    const pool = matchOnly.map(pairKey);
    while (picked.size < MATCH_SAMPLE_SIZE) picked.add(pool.splice(Math.floor(random() * pool.length), 1)[0] as string);
  } else for (const entry of matchOnly) picked.add(pairKey(entry));
  const isMatchOnly = new Set(matchOnly.map(pairKey));
  const first = runs[0]?.run;
  return {
    pairs: all.map((entry) => ({ a: entry.a, b: entry.b, outcomes: entry.outcomes, from: [...entry.from].sort(), judge: !isMatchOnly.has(pairKey(entry)) || picked.has(pairKey(entry)) })),
    matchOnly: { total: matchOnly.length, sampled: picked.size, seed: MATCH_SAMPLE_SEED },
    summary: (first?.targets ?? []).filter((target) => target.branch === "summary").map((target) => ({ target: targetKey(target), name: target.name, answers: Object.fromEntries(runs.map(({ run }) => [run.name, signature(run.states.get(targetKey(target)) as AskState)])) })),
  };
}

/** 이 밴드의 판정 기록. 정답 밖의 짝을 원문 PDF 로 본 것과, 요약 숫자 대상마다 상대 문서에 같은 항목의 자리가 있는지 본 것. */
export const askJudgmentsSchema = z.object({
  note: z.string(),
  status: z.string().min(1),
  pairs: z.array(
    z.object({
      a: z.string(),
      b: z.string(),
      /** same_item: 같은 항목(같은 기간·범위). different_item: 다른 항목. not_a_value: 한쪽 이상이 값이 아닌 표시. */
      verdict: z.enum(["same_item", "different_item", "not_a_value"]),
      basis: z.string().min(1),
    }),
  ),
  summary: z.array(
    z.object({
      target: z.string(),
      /** 상대 문서에서 같은 항목·기간·범위의 값을 실은 자리 (`a:…`). 없으면 빈 배열. */
      counterparts: z.array(z.string()),
      basis: z.string().min(1),
    }),
  ),
});
export type AskJudgments = z.infer<typeof askJudgmentsSchema>;

// ── 표 ──────────────────────────────────────────────────────────────────────

export interface D2Column {
  name: string;
  rows: Map<string, PairStatus>;
}

export interface AskTableInput {
  runs: { run: AskRun; grade: AskGrade }[];
  d2: D2Column[];
  coverage: Coverage;
  gold: Gold2;
  judgments: AskJudgments | null;
}

const STATUS_KO: Record<PairStatus, string> = { match: "일치", mismatch: "불일치", needs_explanation: "설명 필요", visible_elsewhere: "다른 자리와 견주어져 드러남", matched_elsewhere: "다른 자리와 일치", silent: "짝 없음" };
const PLANT_KO: Record<PlantStatus, string> = { caught: "잡음", visible_elsewhere: "다른 자리와 견주어져 드러남", false_match: "거짓 일치", silent: "조용히 지나감" };
const GOLD_KO: Record<GoldTargetStatus, string> = { found_all: "정답의 상대를 전부 답함", found_some: "정답의 상대 일부를 답함", paired_elsewhere: "다른 자리를 답함", none: "없음 (놓침)", unanswered: "답 없음", rejected: "버려진 답" };
const JUDGED = { same_item: "같은 항목", different_item: "같은 항목 아님", not_a_value: "값이 아닌 표시" } as const;
const table = (head: string[], rows: string[][]): string[] => [`| ${head.join(" | ")} |`, `|${head.map((_, index) => (index === 0 ? "---" : "---:")).join("|")}|`, ...rows.map((row) => `| ${row.join(" | ")} |`), ""];
const count = <T>(list: T[], test: (item: T) => boolean): number => list.filter(test).length;

export function renderAskTables(input: AskTableInput): string {
  const { runs, coverage: cover } = input;
  const names = runs.map(({ run }) => run.name);
  const judged = new Map((input.judgments?.pairs ?? []).map((item) => [pairKey(item), item]));
  const summaryTruth = new Map((input.judgments?.summary ?? []).map((item) => [item.target, item]));
  const lines: string[] = ["# 방법 Q: 낱개 숫자를 자리별로 묻기 (실측)", "", "수치는 전부 저장된 실제 실행의 답을 다시 채점한 것이다. 모의 실행은 섞지 않았다.", ""];

  lines.push("## 1. 대상과 정답의 겹침 (호출 0)", "");
  const first = runs[0]?.run;
  if (first) lines.push(...table(["갈래", "대상 수"], [...BRANCHES.map((branch) => [BRANCH_NAME[branch], String(first.tally[branch].total)]), ["합", String(first.tally.total)]]));
  lines.push(`서술 쌍 ${cover.total}개 중 한쪽이라도 대상인 것: ${cover.reachable}/${cover.total}. 정답 쌍의 자리 ${cover.places.total}곳 중 대상인 것: ${cover.places.targets}/${cover.places.total}.`, "");
  lines.push(...table(["대상이 아닌 정답 자리의 까닭", "수", "자리"], Object.entries(cover.places.missing).map(([why, ids]) => [why, String(ids.length), ids.join(", ")])));
  lines.push(...table(["쌍", "항목", "a 쪽", "b 쪽", "물을 수 있는가"], cover.pairs.map((pair) => [pair.pair, pair.item, `${pair.a.id} (${pair.a.branch ? BRANCH_NAME[pair.a.branch] : "대상 아님"})`, `${pair.b.id} (${pair.b.branch ? BRANCH_NAME[pair.b.branch] : "대상 아님"})`, pair.reachable ? "예" : "아니오"])));
  lines.push(...table(["심은 오류", "종류", "심은 자리", "대상인가", "걸린 쌍"], cover.plants.map((plant) => [plant.plant, plant.kind, plant.target, plant.branch ? BRANCH_NAME[plant.branch] : "대상 아님", plant.pairs.join(", ")])));

  lines.push("## 2. 대상마다의 답의 분포 (갈래별, 실행별)", "");
  for (const branch of BRANCHES) {
    lines.push(`### ${BRANCH_NAME[branch]}`, "");
    lines.push(...table(["답", ...names], [
      ["상대를 답함", ...runs.map(({ run }) => `${run.tally[branch].paired}/${run.tally[branch].total}`)],
      ...ASK_REASONS.map((reason) => [`없음 (${reason})`, ...runs.map(({ run }) => `${run.tally[branch].none[reason]}/${run.tally[branch].total}`)]),
      ["답 없음", ...runs.map(({ run }) => `${run.tally[branch].unanswered}/${run.tally[branch].total}`)],
      ["버려진 답", ...runs.map(({ run }) => `${run.tally[branch].rejected}/${run.tally[branch].total}`)],
    ]));
  }
  const reasons = [...new Set(runs.flatMap(({ run }) => Object.keys(run.rejected)))].sort();
  lines.push("버려진 답의 사유 (건수. 상대 자리 하나가 버려진 것도 한 건이다)", "", ...table(["사유", ...names], reasons.length > 0 ? reasons.map((reason) => [reason, ...runs.map(({ run }) => String(run.rejected[reason] ?? 0))]) : [["(없음)", ...names.map(() => "0")]]));
  lines.push(...table(["답한 짝 (값은 코드가 견줌)", ...names], [
    ["일치", ...runs.map(({ run }) => String(count(run.verdicts, (verdict) => verdict.outcome === "match")))],
    ["불일치", ...runs.map(({ run }) => String(count(run.verdicts, (verdict) => verdict.outcome === "mismatch")))],
    ["설명 필요", ...runs.map(({ run }) => String(count(run.verdicts, (verdict) => verdict.outcome === "needs_explanation")))],
    ["합", ...runs.map(({ run }) => String(run.verdicts.length))],
  ]));

  lines.push("## 3. 서술 쌍 21개의 쌍별 결과 (밴드 U 의 D2 와 나란히)", "");
  lines.push(...table(["쌍", "항목", "갈래", ...input.d2.map((column) => column.name), ...names], input.gold.prosePairs.map((pair) => {
    const row = runs[0]?.grade.prose.rows.find((item) => item.pair === pair.id);
    const cls = row ? { clean: "손대지 않음", control: "대조군", error: `오류 (${row.plants.join(", ")})` }[row.class] : "";
    return [pair.id, pair.item, cls, ...input.d2.map((column) => STATUS_KO[column.rows.get(pair.id) as PairStatus] ?? "-"), ...runs.map(({ grade }) => STATUS_KO[grade.prose.rows.find((item) => item.pair === pair.id)?.status as PairStatus])];
  })));
  lines.push(...table(["서술 쌍의 셈", ...names], [
    ["손대지 않은 쌍 중 일치", ...runs.map(({ grade }) => `${grade.prose.clean.match}/${grade.prose.clean.total}`)],
    ["　불일치로 (거짓 경보)", ...runs.map(({ grade }) => `${grade.prose.clean.mismatch}/${grade.prose.clean.total}`)],
    ["　짝 없음", ...runs.map(({ grade }) => `${grade.prose.clean.notPaired}/${grade.prose.clean.total}`)],
    ["대조군 중 일치", ...runs.map(({ grade }) => `${grade.prose.control.match}/${grade.prose.control.total}`)],
    ["오류 쌍 중 불일치 (밴드 R 의 E07 과 짝인 2쌍 포함)", ...runs.map(({ grade }) => `${grade.prose.errors.caught}/${grade.prose.errors.total}`)],
  ]));

  lines.push("## 4. 서술에 심은 오류 8개", "");
  const plantIds = runs[0]?.grade.plants ?? [];
  lines.push(...table(["심은 오류", "갈래", "쌍", ...names], plantIds.map((plant) => [plant.plant, plant.branch ? BRANCH_NAME[plant.branch] : "대상 아님", plant.pairs.join(", "), ...runs.map(({ grade }) => PLANT_KO[grade.plants.find((item) => item.plant === plant.plant)?.status as PlantStatus])])));
  const plantCount = (grade: AskGrade, status: PlantStatus, branch?: Branch): string => `${count(grade.plants, (plant) => plant.status === status && (branch === undefined || plant.branch === branch))}/${count(grade.plants, (plant) => branch === undefined || plant.branch === branch)}`;
  lines.push(...table(["셈", ...names], [
    ["불일치로 잡음", ...runs.map(({ grade }) => plantCount(grade, "caught"))],
    ["　문장 속에 심은 것", ...runs.map(({ grade }) => plantCount(grade, "caught", "sentence"))],
    ["　도표에 심은 것", ...runs.map(({ grade }) => plantCount(grade, "caught", "chart"))],
    ["다른 자리와 견주어져 드러남", ...runs.map(({ grade }) => plantCount(grade, "visible_elsewhere"))],
    ["조용히 지나감", ...runs.map(({ grade }) => plantCount(grade, "silent"))],
    ["일치로 나옴 (거짓 일치)", ...runs.map(({ grade }) => plantCount(grade, "false_match"))],
  ]));

  lines.push("## 5. 정답에 상대가 있는 대상의 답 (「없음」의 놓침)", "");
  const goldTargets = runs[0]?.grade.goldTargets ?? [];
  lines.push(...table(["대상", "갈래", "정답의 상대", ...names], goldTargets.map((item) => [item.target, BRANCH_NAME[item.branch], item.expected.join(", "), ...runs.map(({ grade }) => {
    const mine = grade.goldTargets.find((other) => other.target === item.target);
    return mine ? `${GOLD_KO[mine.status]}${mine.reason ? ` · ${mine.reason}` : ""}` : "-";
  })])));
  lines.push(...table(["셈", ...names], (Object.keys(GOLD_KO) as GoldTargetStatus[]).map((status) => [GOLD_KO[status], ...runs.map(({ grade }) => `${count(grade.goldTargets, (item) => item.status === status)}/${grade.goldTargets.length}`)])));

  lines.push("## 6. 정답 밖의 답 (원문 판정)", "");
  const judge = toJudgeAsk(runs);
  lines.push(`정답 밖의 짝의 합집합 ${judge.pairs.length}쌍. 그중 어느 실행에서든 불일치·설명 필요로 나온 것 ${judge.pairs.length - judge.matchOnly.total}쌍(전부 판정), 일치로만 나온 것 ${judge.matchOnly.total}쌍(판정 ${judge.matchOnly.sampled}쌍${judge.matchOnly.total > judge.matchOnly.sampled ? `, 씨앗 ${judge.matchOnly.seed} 고정 표본` : ", 전부"}).`, "");
  const verdictOf = (verdict: { a: string; b: string }): string => judged.get(pairKey(verdict))?.verdict ?? "unjudged";
  const outsideRow = (label: string, outcome: string, verdict: string): string[] => [label, ...runs.map(({ grade }) => `${count(grade.outside, (item) => item.outcome === outcome && verdictOf(item) === verdict)}/${count(grade.outside, (item) => item.outcome === outcome)}`)];
  lines.push(...table(["정답 밖의 짝 (분모: 그 실행의 그 결과의 정답 밖 짝)", ...names], [
    ["전체", ...runs.map(({ run, grade }) => `${grade.outside.length}/${run.verdicts.length} (답한 짝 중)`)],
    outsideRow("불일치 · 같은 항목 아님 = 거짓 경보", "mismatch", "different_item"),
    outsideRow("불일치 · 같은 항목 (원문의 어긋남이거나 심은 것이 드러난 것)", "mismatch", "same_item"),
    outsideRow("불일치 · 값이 아닌 표시", "mismatch", "not_a_value"),
    outsideRow("불일치 · 판정 안 함", "mismatch", "unjudged"),
    outsideRow("일치 · 같은 항목", "match", "same_item"),
    outsideRow("일치 · 같은 항목 아님 = 틀린 일치", "match", "different_item"),
    outsideRow("일치 · 값이 아닌 표시", "match", "not_a_value"),
    outsideRow("일치 · 판정 안 함 (표본 밖)", "match", "unjudged"),
    outsideRow("설명 필요 · 같은 항목", "needs_explanation", "same_item"),
    outsideRow("설명 필요 · 같은 항목 아님", "needs_explanation", "different_item"),
    outsideRow("설명 필요 · 판정 안 함", "needs_explanation", "unjudged"),
  ]));
  const branchOfTarget = new Map((first?.targets ?? []).map((target) => [targetKey(target), target.branch]));
  lines.push(...table(["정답 밖의 짝: 갈래별 (그 짝을 답한 대상의 갈래)", ...names], BRANCHES.map((branch) => [BRANCH_NAME[branch], ...runs.map(({ grade }) => String(count(grade.outside, (item) => item.from.some((target) => branchOfTarget.get(target) === branch))))])));
  lines.push(...table(["a", "b", ...names, "판정", "근거"], judge.pairs.filter((pair) => pair.judge).map((pair) => [pair.a, pair.b, ...names.map((name) => (pair.outcomes[name] ? STATUS_KO[pair.outcomes[name] as PairStatus] : "-")), judged.has(pairKey(pair)) ? JUDGED[judged.get(pairKey(pair))!.verdict] : "판정 안 함", judged.get(pairKey(pair))?.basis ?? ""])));
  lines.push(...table(["밴드 R 의 정답 쌍(표 칸끼리)에 든 답", ...names], [["수", ...runs.map(({ grade }) => String(grade.inTableGold.length))]]));

  lines.push("## 7. 요약 숫자 대상의 답 (전부 판정)", "");
  const scoreSummary = (state: AskState, truth: string[] | undefined): string => {
    if (truth === undefined) return "판정 안 함";
    if (state.state === "paired") {
      const right = state.counterparts.filter((id) => truth.includes(id)).length;
      return right === state.counterparts.length ? "맞는 상대" : right > 0 ? "맞는 상대와 틀린 상대가 섞임" : "틀린 상대";
    }
    if (state.state === "none") return truth.length === 0 ? "맞는 없음" : "놓침";
    return state.state === "unanswered" ? "답 없음" : "버려진 답";
  };
  lines.push(...table(["대상", "이름", "원문으로 본 상대 자리", ...names], (first?.targets ?? []).filter((target) => target.branch === "summary").map((target) => {
    const truth = summaryTruth.get(targetKey(target))?.counterparts;
    return [targetKey(target), target.name ?? "", truth === undefined ? "판정 안 함" : truth.length === 0 ? "없음" : truth.join(", "), ...runs.map(({ run }) => {
      const state = run.states.get(targetKey(target)) as AskState;
      return `${signature(state)} → ${scoreSummary(state, truth)}`;
    })];
  })));

  lines.push("## 8. 세 실행이 서로 같은 답을 낸 대상", "");
  if (first && runs.length > 0) {
    const strict = stability(first.targets, runs.map(({ run }) => run.states));
    const loose = stability(first.targets, runs.map(({ run }) => run.states), true);
    lines.push(...table(["갈래", "전부 같음 (상대의 집합과 사유까지)", "과반만 같음", "과반 없음", "전부 같음 (답의 갈래만)"], BRANCHES.map((branch) => [BRANCH_NAME[branch], `${strict[branch].allSame}/${strict[branch].total}`, `${strict[branch].majority}/${strict[branch].total}`, `${strict[branch].split}/${strict[branch].total}`, `${loose[branch].allSame}/${loose[branch].total}`])));
  }

  lines.push("## 9. 비용", "");
  lines.push(...table(["실행", "호출", "입력 토큰 (캐시 포함)", "그중 캐시 읽기", "출력 토큰", "요청 최대 입력", "비용 USD"], runs.map(({ run }) => [run.name, String(run.cost?.calls ?? "-"), String(run.cost?.inputTokens ?? "-"), String(run.cost?.cacheReadTokens ?? "-"), String(run.cost?.outputTokens ?? "-"), String(run.cost?.maxInputTokens ?? "-"), run.cost ? run.cost.costUsd.toFixed(6) : "-"])));
  return `${lines.join("\n")}\n`;
}
