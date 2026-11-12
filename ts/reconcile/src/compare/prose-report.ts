import { z } from "zod";
import type { Extraction } from "../domain/facts.js";
import type { GoldPair, Plant } from "./exam.js";
import { isProseId, type Change, type Gold2, type GoldGroup, type Plant2 } from "./exam2.js";
import type { Grade, PairStatus } from "./grade.js";
import type { Verdict } from "./methods.js";
import type { GroupJudgement, GroupTally } from "./prose.js";
import type { ReviewTally } from "./review.js";

// 밴드 U 의 채점과 표. 정답(밴드 R 의 정답 쌍, 넓힌 정답)과 심은 것으로 방법의 결과를 센다. 모델을 부르지 않는다. 수는 전부 분모와 같이 낸다.

/** 심은 자리 하나 (밴드 R 의 칸과 이번에 심은 자리를 한 꼴로). */
export interface Planted {
  plant: string;
  kind: string;
  isError: boolean;
  doc: "a" | "b";
  target: string;
  original: string;
  planted: string;
}

export function plantedPlaces(v1: Plant[], v2: Plant2[]): Planted[] {
  return [...v1.flatMap((plant) => plant.cells.map((cell) => ({ plant: plant.id, kind: plant.kind, isError: plant.isError, doc: "b" as const, target: cell.cell, original: cell.original, planted: cell.planted }))), ...v2.map((plant) => ({ plant: plant.id, kind: plant.kind, isError: plant.isError, doc: plant.doc, target: plant.target, original: plant.original, planted: plant.planted }))];
}

export const changesOf = (planted: Planted[]): Change[] => planted.map((item) => ({ doc: item.doc, target: item.target, original: item.original, planted: item.planted }));

// ── 서술이 낀 정답 쌍 (두 문서 사이) ─────────────────────────────────────────

export type RowClass = "clean" | "control" | "error";

export interface ProseRow {
  pair: string;
  item: string;
  a: string;
  b: string;
  /** clean: 손대지 않은 쌍. control: 표기 자릿수가 다른 쌍(원문 그대로이거나 심은 대조군). error: 한쪽에 오류를 심은 쌍. */
  class: RowClass;
  plants: string[];
  status: PairStatus;
  detail: string;
}

export interface ProseGrade {
  clean: { total: number; match: number; needsExplanation: number; mismatch: number; notPaired: number };
  control: { total: number; match: number; needsExplanation: number; mismatch: number; notPaired: number };
  errors: { total: number; caught: number; visibleOther: number; falseMatch: number; silent: number };
  rows: ProseRow[];
  /** 서술 값 후보가 낀 짝 가운데 정답 쌍이 아닌 것 (원문으로 판정할 대상). */
  outsideGold: { match: Verdict[]; mismatch: Verdict[]; needsExplanation: Verdict[] };
  /** 서술 값 후보가 낀 짝의 수. */
  proseVerdicts: { match: number; mismatch: number; needsExplanation: number };
}

const shows = (verdict: Verdict): boolean => verdict.outcome === "mismatch" || (verdict.outcome === "needs_explanation" && verdict.valuesDiffer === true);

/** 서술이 낀 정답 쌍이 그 방법에서 어떻게 나왔는가. `prose` 는 「서술 값 후보인가」(문서, ID). */
export function gradeProse(gold: Gold2, planted: Planted[], verdicts: Verdict[], prose: (doc: "a" | "b", id: string) => boolean): ProseGrade {
  const at = new Map(planted.map((item) => [`${item.doc}:${item.target}`, item]));
  const exact = new Map(verdicts.map((verdict) => [`${verdict.a}|${verdict.b}`, verdict]));
  const out: ProseGrade = { clean: { total: 0, match: 0, needsExplanation: 0, mismatch: 0, notPaired: 0 }, control: { total: 0, match: 0, needsExplanation: 0, mismatch: 0, notPaired: 0 }, errors: { total: 0, caught: 0, visibleOther: 0, falseMatch: 0, silent: 0 }, rows: [], outsideGold: { match: [], mismatch: [], needsExplanation: [] }, proseVerdicts: { match: 0, mismatch: 0, needsExplanation: 0 } };
  const goldKeys = new Set<string>();
  for (const pair of gold.prosePairs) {
    goldKeys.add(`${pair.a}|${pair.b}`);
    const sides = [at.get(`a:${pair.a}`), at.get(`b:${pair.b}`)].filter((item): item is Planted => item !== undefined);
    const cls: RowClass = sides.some((item) => item.isError) ? "error" : pair.control !== null || sides.length > 0 ? "control" : "clean";
    const found = exact.get(`${pair.a}|${pair.b}`);
    let status: PairStatus;
    let detail: string;
    if (found) {
      status = found.outcome;
      detail = found.reason;
    } else {
      // 그 두 자리는 짝지어지지 않았다. 심은 자리가 다른 자리와 견주어져 값 차이가 드러났는지 본다.
      const others = verdicts.filter((verdict) => sides.some((item) => (item.doc === "a" ? verdict.a : verdict.b) === item.target));
      const visible = others.find(shows);
      const matched = others.find((verdict) => verdict.outcome === "match");
      status = visible ? "visible_elsewhere" : matched ? "matched_elsewhere" : "silent";
      detail = visible ? `${visible.a} ↔ ${visible.b} 가 ${visible.outcome}` : matched ? `${matched.a} ↔ ${matched.b} 가 일치` : "짝지어지지 않음";
    }
    out.rows.push({ pair: pair.id, item: pair.item, a: pair.a, b: pair.b, class: cls, plants: sides.map((item) => item.plant), status, detail });
    if (cls === "error") {
      out.errors.total += 1;
      if (status === "mismatch") out.errors.caught += 1;
      else if (status === "visible_elsewhere" || (status === "needs_explanation" && found?.valuesDiffer === true)) out.errors.visibleOther += 1;
      else if (status === "match" || status === "matched_elsewhere") out.errors.falseMatch += 1;
      else out.errors.silent += 1;
    } else {
      const bag = out[cls];
      bag.total += 1;
      if (status === "match") bag.match += 1;
      else if (status === "needs_explanation") bag.needsExplanation += 1;
      else if (status === "mismatch") bag.mismatch += 1;
      else bag.notPaired += 1;
    }
  }
  for (const verdict of verdicts) {
    if (!prose("a", verdict.a) && !prose("b", verdict.b)) continue;
    const name = verdict.outcome === "match" ? "match" : verdict.outcome === "mismatch" ? "mismatch" : "needsExplanation";
    out.proseVerdicts[name] += 1;
    if (!goldKeys.has(`${verdict.a}|${verdict.b}`)) out.outsideGold[name].push(verdict);
  }
  return out;
}

// ── 문서 안 정답 묶음 ────────────────────────────────────────────────────────

export interface GroupRow {
  group: string;
  item: string;
  places: number;
  class: RowClass;
  plants: string[];
  /** 찾음: 답의 한 묶음에 정답 묶음의 자리가 전부 같이 있다. 일부: 둘 이상 같이 있다. 못 찾음: 같이 있는 것이 없다. */
  found: "full" | "partial" | "none";
  /** 같이 묶인 정답 자리들 사이의 비교 가운데 가장 무거운 결과. 같이 묶인 것이 없으면 null. */
  outcome: Verdict["outcome"] | null;
  /** 오류를 심은 묶음: 심은 자리와 다른 정답 자리의 비교가 어떻게 나왔는가. */
  planted: "caught" | "visible_other" | "false_match" | "silent" | null;
}

export interface GroupGrade {
  doc: "a" | "b";
  groups: { total: number; full: number; partial: number; none: number };
  clean: { total: number; found: number; match: number; mismatch: number; needsExplanation: number };
  control: { total: number; found: number; match: number; mismatch: number; needsExplanation: number };
  errors: { total: number; caught: number; visibleOther: number; falseMatch: number; silent: number };
  rows: GroupRow[];
  /** 묶음 안의 불일치 가운데 「심은 자리와 그 정답 묶음의 다른 자리」가 아닌 것 (원문으로 판정할 대상). */
  otherMismatches: Verdict[];
  mismatches: number;
  answered: { groups: number; places: number; rejected: Record<string, number>; inSeveralGroups: number };
}

const SEVERITY: Record<Verdict["outcome"], number> = { mismatch: 3, needs_explanation: 2, match: 1 };

export function gradeGroups(doc: "a" | "b", goldGroups: GoldGroup[], planted: Planted[], judgement: GroupJudgement): GroupGrade {
  const at = new Map(planted.filter((item) => item.doc === doc).map((item) => [item.target, item]));
  const out: GroupGrade = { doc, groups: { total: 0, full: 0, partial: 0, none: 0 }, clean: { total: 0, found: 0, match: 0, mismatch: 0, needsExplanation: 0 }, control: { total: 0, found: 0, match: 0, mismatch: 0, needsExplanation: 0 }, errors: { total: 0, caught: 0, visibleOther: 0, falseMatch: 0, silent: 0 }, rows: [], otherMismatches: [], mismatches: 0, answered: { groups: judgement.groups.length, places: new Set(judgement.groups.flatMap((group) => group.places)).size, rejected: judgement.rejected, inSeveralGroups: judgement.inSeveralGroups.length } };
  const expected = new Set<string>();
  for (const gold of goldGroups.filter((group) => group.doc === doc)) {
    const ids = new Set(gold.places.map((place) => place.id));
    const sides = gold.places.map((place) => at.get(place.id)).filter((item): item is Planted => item !== undefined);
    const cls: RowClass = sides.some((item) => item.isError) ? "error" : gold.control !== null || sides.length > 0 ? "control" : "clean";
    let best = 0;
    const inside: Verdict[] = [];
    for (const group of judgement.groups) {
      const shared = group.places.filter((id) => ids.has(id)).length;
      best = Math.max(best, shared);
      inside.push(...group.verdicts.filter((verdict) => ids.has(verdict.a) && ids.has(verdict.b)));
    }
    const found = best === ids.size ? "full" : best >= 2 ? "partial" : "none";
    const outcome = inside.length === 0 ? null : inside.reduce<Verdict["outcome"]>((worst, verdict) => (SEVERITY[verdict.outcome] > SEVERITY[worst] ? verdict.outcome : worst), "match");
    let plantedResult: GroupRow["planted"] = null;
    if (cls === "error") {
      const targets = new Set(sides.filter((item) => item.isError).map((item) => item.target));
      const touching = inside.filter((verdict) => targets.has(verdict.a) || targets.has(verdict.b));
      for (const verdict of touching) expected.add(`${verdict.a}|${verdict.b}`);
      plantedResult = touching.some((verdict) => verdict.outcome === "mismatch") ? "caught" : touching.some(shows) ? "visible_other" : touching.some((verdict) => verdict.outcome === "match") ? "false_match" : "silent";
      out.errors.total += 1;
      out.errors[plantedResult === "caught" ? "caught" : plantedResult === "visible_other" ? "visibleOther" : plantedResult === "false_match" ? "falseMatch" : "silent"] += 1;
    } else {
      const bag = out[cls];
      bag.total += 1;
      if (found !== "none") bag.found += 1;
      if (outcome === "match") bag.match += 1;
      else if (outcome === "mismatch") bag.mismatch += 1;
      else if (outcome === "needs_explanation") bag.needsExplanation += 1;
    }
    out.groups.total += 1;
    out.groups[found] += 1;
    out.rows.push({ group: gold.id, item: gold.item, places: ids.size, class: cls, plants: sides.map((item) => item.plant), found, outcome, planted: plantedResult });
  }
  const seen = new Set<string>();
  for (const group of judgement.groups) {
    for (const verdict of group.verdicts) {
      if (verdict.outcome !== "mismatch" || seen.has(`${verdict.a}|${verdict.b}`)) continue;
      seen.add(`${verdict.a}|${verdict.b}`);
      out.mismatches += 1;
      if (!expected.has(`${verdict.a}|${verdict.b}`)) out.otherMismatches.push(verdict);
    }
  }
  return out;
}

// ── 판정 기록 ────────────────────────────────────────────────────────────────

/** 정답 밖의 짝·묶음 안 비교를 원문 PDF 로 본 기록. cross: 두 문서 사이의 짝(a 는 문서 A, b 는 문서 B). a·b: 그 문서 안의 두 자리. */
export const proseJudgmentsSchema = z.object({
  note: z.string(),
  status: z.string().min(1),
  items: z.array(
    z.object({
      scope: z.enum(["cross", "a", "b"]),
      a: z.string(),
      b: z.string(),
      /** same_item: 같은 항목(같은 기간·범위). different_item: 다른 항목. not_a_value: 한쪽 이상이 값이 아닌 표시(규격 번호, 연도, 눈금). */
      verdict: z.enum(["same_item", "different_item", "not_a_value"]),
      basis: z.string().min(1),
    }),
  ),
});
export type ProseJudgments = z.infer<typeof proseJudgmentsSchema>;

const judgmentKey = (scope: string, a: string, b: string): string => `${scope}|${[a, b].sort().join("|")}`;
const JUDGED: Record<ProseJudgments["items"][number]["verdict"], string> = { same_item: "같은 항목", different_item: "같은 항목 아님", not_a_value: "값이 아닌 표시" };

// ── 표 ──────────────────────────────────────────────────────────────────────

export interface CostSummary {
  calls: number;
  inputTokens: number;
  cacheReadTokens: number;
  outputTokens: number;
  costUsd: number;
  maxInputTokens: number;
  byResultKind: Record<string, number>;
}

export interface D2Run {
  name: string;
  verdicts: Verdict[];
  table: Grade;
  prose: ProseGrade;
  tally: { a: ReviewTally; b: ReviewTally };
  rejected: Record<string, number>;
  cost: CostSummary | null;
  /** H2 의 실행이면: 검수가 새로 낸 짝, 남은 칸의 답, 검수 전의 채점. */
  review?: { newPairs: (Verdict & { tables: { a: string; b: string } })[]; asked: number; states: Record<string, number>; rejected: Record<string, number>; before: { table: Grade; prose: ProseGrade; tally: { a: ReviewTally; b: ReviewTally } } };
}

export interface IRun {
  name: string;
  doc: "a" | "b";
  grade: GroupGrade;
  tally: GroupTally;
  groups: GroupJudgement["groups"];
  cost: CostSummary | null;
}

export interface TableInput {
  /** 밴드 R·S 의 방법 D 의 세 실행 (비교 기준). */
  d: { name: string; grade: Grade }[];
  d2: D2Run[];
  h2: D2Run[];
  i: IRun[];
  gold: Gold2;
  v1Pairs: GoldPair[];
  planted: Planted[];
  facts: { a: Extraction; b: Extraction };
  judgments: ProseJudgments | null;
}

const STATUS_KO: Record<PairStatus, string> = { match: "일치", mismatch: "불일치", needs_explanation: "설명 필요", visible_elsewhere: "다른 자리와 견주어져 드러남", matched_elsewhere: "다른 자리와 일치", silent: "조용히 지나감" };
const sum = (bag: Record<string, number>): number => Object.values(bag).reduce((x, y) => x + y, 0);
const table = (head: string[], rows: string[][]): string[] => [`| ${head.join(" | ")} |`, `|${head.map((_, index) => (index === 0 ? "---" : "---:")).join("|")}|`, ...rows.map((row) => `| ${row.join(" | ")} |`), ""];
const usd = (cost: CostSummary | null): string => (cost ? cost.costUsd.toFixed(6) : "-");

/** 판정이 필요한 것의 합집합 (실행들에 걸쳐). */
export function toJudge(input: Pick<TableInput, "d2" | "h2" | "i">): { scope: "cross" | "a" | "b"; a: string; b: string; outcome: string; runs: string[] }[] {
  const bag = new Map<string, { scope: "cross" | "a" | "b"; a: string; b: string; outcome: string; runs: string[] }>();
  const add = (scope: "cross" | "a" | "b", verdict: Verdict, run: string): void => {
    const key = `${judgmentKey(scope, verdict.a, verdict.b)}|${verdict.outcome}`;
    const entry = bag.get(key) ?? { scope, a: verdict.a, b: verdict.b, outcome: verdict.outcome, runs: [] };
    entry.runs.push(run);
    bag.set(key, entry);
  };
  for (const run of [...input.d2, ...input.h2]) for (const verdict of [...run.prose.outsideGold.mismatch, ...run.prose.outsideGold.match, ...run.prose.outsideGold.needsExplanation]) add("cross", verdict, run.name);
  for (const run of input.i) for (const verdict of run.grade.otherMismatches) add(run.doc, verdict, run.name);
  return [...bag.values()];
}

export function renderProseTables(input: TableInput): string {
  const judged = new Map((input.judgments?.items ?? []).map((item) => [judgmentKey(item.scope, item.a, item.b), item]));
  const lines: string[] = [];
  const cross = [...input.d2, ...input.h2];
  const raw = (doc: "a" | "b", id: string): string => input.facts[doc].facts.find((fact) => fact.id === id)?.raw ?? "?";
  const count = (scope: "cross" | "a" | "b", verdicts: Verdict[]): string => {
    if (verdicts.length === 0) return "0";
    const bag: Record<string, number> = {};
    for (const verdict of verdicts) {
      const item = judged.get(judgmentKey(scope, verdict.a, verdict.b));
      bag[item ? JUDGED[item.verdict] : "판정 전"] = (bag[item ? JUDGED[item.verdict] : "판정 전"] ?? 0) + 1;
    }
    return `${verdicts.length} (${Object.entries(bag).map(([name, n]) => `${name} ${n}`).join(", ")})`;
  };

  lines.push("## 1. 표 칸의 성적 (밴드 R 의 정답 쌍 58개, 심은 15칸)", "");
  const tableRuns: { name: string; grade: Grade }[] = [...input.d.map((run) => ({ name: run.name, grade: run.grade })), ...cross.map((run) => ({ name: run.name, grade: run.table }))];
  lines.push(
    ...table(
      ["항목", ...tableRuns.map((run) => run.name)],
      [
        ["참 일치 (오류를 심지 않은 정답 쌍 중 일치)", ...tableRuns.map((run) => `${run.grade.clean.match}/${run.grade.clean.total}`)],
        ["　그 쌍이 불일치로 (거짓 경보)", ...tableRuns.map((run) => `${run.grade.clean.mismatch}/${run.grade.clean.total}`)],
        ["　그 쌍이 설명 필요로", ...tableRuns.map((run) => `${run.grade.clean.needsExplanation}/${run.grade.clean.total}`)],
        ["　대조군(반올림 허용폭 안)이 일치로", ...tableRuns.map((run) => `${run.grade.control.match}/${run.grade.control.total}`)],
        ["잡은 오류 (심은 칸 중 그 쌍이 불일치)", ...tableRuns.map((run) => `${run.grade.errors.caught}/${run.grade.errors.total}`)],
        ["　값 차이가 다른 꼴로 드러남", ...tableRuns.map((run) => `${run.grade.errors.visibleOther}/${run.grade.errors.total}`)],
        ["조용히 지나간 오류", ...tableRuns.map((run) => `${run.grade.errors.silent + run.grade.errors.falseMatch}/${run.grade.errors.total}`)],
        ["　그중 일치로 나온 것 (거짓 일치)", ...tableRuns.map((run) => `${run.grade.errors.falseMatch}/${run.grade.errors.total}`)],
        ["견준 짝: 일치 / 불일치 / 설명 필요", ...tableRuns.map((run) => `${run.grade.verdicts.match} / ${run.grade.verdicts.mismatch} / ${run.grade.verdicts.needsExplanation}`)],
        ["버려진 답", ...tableRuns.map((run) => `${sum(run.grade.rejected)}`)],
      ],
    ),
  );

  lines.push("## 2. 서술 (서술이 낀 정답 쌍, 두 문서 사이)", "");
  lines.push(
    ...table(
      ["항목", ...cross.map((run) => run.name)],
      [
        ["손대지 않은 쌍 중 일치", ...cross.map((run) => `${run.prose.clean.match}/${run.prose.clean.total}`)],
        ["　그 쌍이 불일치로 (거짓 경보)", ...cross.map((run) => `${run.prose.clean.mismatch}/${run.prose.clean.total}`)],
        ["　그 쌍이 설명 필요로", ...cross.map((run) => `${run.prose.clean.needsExplanation}/${run.prose.clean.total}`)],
        ["　짝지어지지 않음", ...cross.map((run) => `${run.prose.clean.notPaired}/${run.prose.clean.total}`)],
        ["오류를 심은 쌍 중 불일치 (잡음)", ...cross.map((run) => `${run.prose.errors.caught}/${run.prose.errors.total}`)],
        ["　값 차이가 다른 꼴로 드러남", ...cross.map((run) => `${run.prose.errors.visibleOther}/${run.prose.errors.total}`)],
        ["　조용히 지나감", ...cross.map((run) => `${run.prose.errors.silent}/${run.prose.errors.total}`)],
        ["　일치로 나옴 (거짓 일치)", ...cross.map((run) => `${run.prose.errors.falseMatch}/${run.prose.errors.total}`)],
        ["표기 자릿수가 다른 쌍(대조군): 일치 / 설명 필요 / 불일치 / 짝 없음", ...cross.map((run) => `${run.prose.control.match} / ${run.prose.control.needsExplanation} / ${run.prose.control.mismatch} / ${run.prose.control.notPaired} (${run.prose.control.total}쌍 중)`)],
        ["서술이 낀 짝: 일치 / 불일치 / 설명 필요", ...cross.map((run) => `${run.prose.proseVerdicts.match} / ${run.prose.proseVerdicts.mismatch} / ${run.prose.proseVerdicts.needsExplanation}`)],
        ["　정답 밖의 불일치 (판정)", ...cross.map((run) => count("cross", run.prose.outsideGold.mismatch))],
        ["　정답 밖의 일치 (판정)", ...cross.map((run) => count("cross", run.prose.outsideGold.match))],
        ["　정답 밖의 설명 필요 (판정)", ...cross.map((run) => count("cross", run.prose.outsideGold.needsExplanation))],
      ],
    ),
  );
  lines.push("### 서술이 낀 정답 쌍 × 실행", "");
  const plantOf = new Map(input.planted.map((item) => [item.plant, item]));
  lines.push(
    ...table(
      ["쌍", "항목", "문서 A 의 자리", "문서 B 의 자리", "갈래", ...cross.map((run) => run.name)],
      input.gold.prosePairs.map((pair) => {
        const first = cross[0]?.prose.rows.find((row) => row.pair === pair.id);
        const note = (first?.plants ?? []).map((id) => `${id} ${plantOf.get(id)?.kind} ${plantOf.get(id)?.original} → ${plantOf.get(id)?.planted}`).join("; ");
        return [pair.id, pair.item + (pair.period ? ` ${pair.period}` : ""), `\`${pair.a}\` ${pair.rawA}`, `\`${pair.b}\` ${pair.rawB}`, first?.class === "error" ? `오류 (${note})` : first?.class === "control" ? `대조군${note ? ` (${note})` : " (원문의 자릿수 차이)"}` : "손대지 않음", ...cross.map((run) => STATUS_KO[run.prose.rows.find((row) => row.pair === pair.id)?.status ?? "silent"])];
      }),
    ),
  );

  lines.push("## 3. 문서 안 (정답 묶음)", "");
  if (input.i.length > 0) {
    lines.push(
      ...table(
        ["항목", ...input.i.map((run) => run.name)],
        [
          ["정답 묶음: 찾음 / 일부 / 못 찾음", ...input.i.map((run) => `${run.grade.groups.full} / ${run.grade.groups.partial} / ${run.grade.groups.none} (${run.grade.groups.total}묶음 중)`)],
          ["손대지 않은 묶음 중 찾아서 일치", ...input.i.map((run) => `${run.grade.clean.match}/${run.grade.clean.total}`)],
          ["　찾았는데 불일치 (거짓 경보)", ...input.i.map((run) => `${run.grade.clean.mismatch}/${run.grade.clean.total}`)],
          ["　찾았는데 설명 필요", ...input.i.map((run) => `${run.grade.clean.needsExplanation}/${run.grade.clean.total}`)],
          ["어긋남을 심은 묶음 중 불일치 (잡음)", ...input.i.map((run) => `${run.grade.errors.caught}/${run.grade.errors.total}`)],
          ["　다른 꼴로 드러남", ...input.i.map((run) => `${run.grade.errors.visibleOther}/${run.grade.errors.total}`)],
          ["　조용히 지나감", ...input.i.map((run) => `${run.grade.errors.silent}/${run.grade.errors.total}`)],
          ["　일치로 나옴 (거짓 일치)", ...input.i.map((run) => `${run.grade.errors.falseMatch}/${run.grade.errors.total}`)],
          ["표기 자릿수가 다른 묶음(대조군): 일치 / 설명 필요 / 불일치", ...input.i.map((run) => `${run.grade.control.match} / ${run.grade.control.needsExplanation} / ${run.grade.control.mismatch} (${run.grade.control.total}묶음 중)`)],
          ["답의 묶음 수 / 묶인 자리 수", ...input.i.map((run) => `${run.grade.answered.groups} / ${run.grade.answered.places}`)],
          ["묶음 안의 불일치 (자리 쌍, 전체)", ...input.i.map((run) => `${run.grade.mismatches}`)],
          ["　그중 심은 자리와 그 정답 묶음의 자리가 아닌 것 (판정)", ...input.i.map((run) => count(run.doc, run.grade.otherMismatches))],
          ["버려진 답 / 두 묶음에 든 자리", ...input.i.map((run) => `${sum(run.grade.answered.rejected)} / ${run.grade.answered.inSeveralGroups}`)],
          ["값 후보: 묶여서 일치 / 불일치 / 설명 필요", ...input.i.map((run) => `${run.tally.grouped.match} / ${run.tally.grouped.mismatch} / ${run.tally.grouped.needsExplanation}`)],
          ["　묶이지 않음 (합)", ...input.i.map((run) => `${sum(run.tally.ungrouped)} (${run.tally.total}개 중)`)],
          ["호출 / 비용 USD", ...input.i.map((run) => `${run.cost?.calls ?? 0} / ${usd(run.cost)}`)],
          ["성공이 아닌 호출", ...input.i.map((run) => (run.cost ? `${sum(run.cost.byResultKind) - (run.cost.byResultKind.success ?? 0)}/${sum(run.cost.byResultKind)}` : "-"))],
        ],
      ),
    );
    for (const doc of ["a", "b"] as const) {
      const runs = input.i.filter((run) => run.doc === doc);
      if (runs.length === 0) continue;
      lines.push(`### 문서 ${doc.toUpperCase()} 의 정답 묶음 × 실행`, "");
      lines.push(
        ...table(
          ["묶음", "항목", "자리 수", "갈래", ...runs.map((run) => run.name)],
          (runs[0] as IRun).grade.rows.map((row) => [
            row.group,
            row.item,
            `${row.places}`,
            row.class === "error" ? `어긋남 (${row.plants.join(", ")})` : row.class === "control" ? "대조군" : "손대지 않음",
            ...runs.map((run) => {
              const mine = run.grade.rows.find((item) => item.group === row.group) as GroupRow;
              const where = mine.found === "full" ? "찾음" : mine.found === "partial" ? "일부" : "못 찾음";
              const result = mine.class === "error" ? { caught: "불일치", visible_other: "다른 꼴", false_match: "일치(거짓)", silent: "조용히" }[mine.planted as "caught"] : mine.outcome === null ? "-" : { match: "일치", mismatch: "불일치", needs_explanation: "설명 필요" }[mine.outcome];
              return `${where} · ${result}`;
            }),
          ]),
        ),
      );
    }
  }

  lines.push("## 4. 셈 (모든 값 후보가 결과 하나에)", "");
  const tallyRow = (name: string, pick: (tally: ReviewTally) => number): string[] => [name, ...cross.flatMap((run) => [`${pick(run.tally.a)}`, `${pick(run.tally.b)}`])];
  const buckets = [...new Set(cross.flatMap((run) => [...Object.keys(run.tally.a.outsideTables), ...Object.keys(run.tally.b.outsideTables)]))].sort();
  const reasons = [...new Set(cross.flatMap((run) => [...Object.keys(run.tally.a.noCounterpart), ...Object.keys(run.tally.b.noCounterpart)]))].sort();
  lines.push(
    ...table(
      ["결과", ...cross.flatMap((run) => [`${run.name} A`, `${run.name} B`])],
      [
        tallyRow("견줌: 일치", (tally) => tally.compared.match),
        tallyRow("견줌: 불일치", (tally) => tally.compared.mismatch),
        tallyRow("견줌: 설명 필요", (tally) => tally.compared.needsExplanation),
        tallyRow("짝지어진 표 안의 남은 칸 (검수 전)", (tally) => tally.leftInPairedTable),
        ...reasons.map((reason) => tallyRow(`검수: 상대 없음 (${reason})`, (tally) => tally.noCounterpart[reason] ?? 0)),
        tallyRow("검수: 가를 수 없음", (tally) => tally.undecided.undecidable),
        tallyRow("검수: 미응답", (tally) => tally.undecided.unanswered),
        tallyRow("검수: 버려진 답", (tally) => tally.undecided.answerRejected),
        tallyRow("짝이 없는 표의 칸인데 답이 버려짐", (tally) => tally.answerRejected),
        tallyRow("짝이 없는 표의 칸", (tally) => tally.tableNotPaired),
        ...buckets.map((bucket) => tallyRow(`짝 없음(서술): ${bucket}`, (tally) => tally.outsideTables[bucket] ?? 0)),
        tallyRow("합 (값 후보 수)", (tally) => tally.total),
      ],
    ),
  );
  // 서술 값 후보의 갈래별: 견준 수 / 전체.
  const proseOf = (doc: "a" | "b"): Map<string, string> => new Map(input.facts[doc].facts.filter((fact) => fact.bucket !== "table_cell").map((fact) => [fact.id, fact.bucket]));
  const proseBuckets = [...new Set([...proseOf("a").values(), ...proseOf("b").values()])].sort();
  lines.push("### 서술 값 후보 가운데 견주어진 것 (갈래별)", "");
  lines.push(
    ...table(
      ["갈래", ...cross.flatMap((run) => [`${run.name} A`, `${run.name} B`])],
      proseBuckets.map((bucket) => [
        bucket,
        ...cross.flatMap((run) =>
          (["a", "b"] as const).map((doc) => {
            const ids = [...proseOf(doc).entries()].filter(([, kind]) => kind === bucket).map(([id]) => id);
            const compared = new Set(run.verdicts.map((verdict) => verdict[doc]));
            return `${ids.filter((id) => compared.has(id)).length}/${ids.length}`;
          }),
        ),
      ]),
    ),
  );

  if (input.h2.length > 0) {
    lines.push("## 5. H2 의 칸 검수 (상대는 그 요청의 남은 칸에서만, 표 검수 없음)", "");
    lines.push(
      ...table(
        ["항목", ...input.h2.map((run) => run.name)],
        [
          ["물은 남은 칸 (요청 단위)", ...input.h2.map((run) => `${run.review?.asked ?? 0}`)],
          ...[...new Set(input.h2.flatMap((run) => Object.keys(run.review?.states ?? {})))].sort().map((state) => [`　답: ${state}`, ...input.h2.map((run) => `${run.review?.states[state] ?? 0}/${run.review?.asked ?? 0}`)]),
          ["검수가 새로 낸 짝", ...input.h2.map((run) => `${run.review?.newPairs.length ?? 0}`)],
          ["　그중 밴드 R 의 정답 쌍", ...input.h2.map((run) => `${(run.review?.newPairs ?? []).filter((pair) => input.v1Pairs.some((gold) => gold.a === pair.a && gold.b === pair.b)).length}/${run.review?.newPairs.length ?? 0}`)],
          ["　새 짝: 일치 / 불일치 / 설명 필요", ...input.h2.map((run) => ["match", "mismatch", "needs_explanation"].map((name) => (run.review?.newPairs ?? []).filter((pair) => pair.outcome === name).length).join(" / "))],
          ["검수의 버려진 답", ...input.h2.map((run) => `${sum(run.review?.rejected ?? {})}${sum(run.review?.rejected ?? {}) > 0 ? ` (${Object.entries(run.review?.rejected ?? {}).map(([name, n]) => `${name} ${n}`).join(", ")})` : ""}`)],
          ["검수 전 → 후: 참 일치", ...input.h2.map((run) => `${run.review?.before.table.clean.match}/${run.review?.before.table.clean.total} → ${run.table.clean.match}/${run.table.clean.total}`)],
          ["검수 전 → 후: 잡은 오류", ...input.h2.map((run) => `${run.review?.before.table.errors.caught}/${run.review?.before.table.errors.total} → ${run.table.errors.caught}/${run.table.errors.total}`)],
          ["검수 전 → 후: 불일치로 나온 짝", ...input.h2.map((run) => `${run.review?.before.table.verdicts.mismatch} → ${run.table.verdicts.mismatch}`)],
        ],
      ),
    );
    const newPairs = new Map<string, { pair: Verdict; runs: string[] }>();
    for (const run of input.h2) for (const pair of run.review?.newPairs ?? []) newPairs.set(`${pair.a}|${pair.b}`, { pair, runs: [...(newPairs.get(`${pair.a}|${pair.b}`)?.runs ?? []), run.name] });
    if (newPairs.size > 0) {
      lines.push("### 검수가 새로 낸 짝 (합집합)", "");
      lines.push(
        ...table(
          ["문서 A 의 칸", "문서 B 의 칸", "코드의 결과", "낸 실행", "판정"],
          [...newPairs.values()].map(({ pair, runs }) => {
            const gold = input.v1Pairs.find((item) => item.a === pair.a && item.b === pair.b);
            const item = judged.get(judgmentKey("cross", pair.a, pair.b));
            return [`\`${pair.a}\` ${raw("a", pair.a)}`, `\`${pair.b}\` ${raw("b", pair.b)}`, pair.outcome, runs.join(", "), gold ? `정답 쌍 ${gold.id}` : item ? `${JUDGED[item.verdict]}: ${item.basis}` : "판정 전"];
          }),
        ),
      );
    }
  }

  lines.push("## 6. 비용과 호출", "");
  const costRuns: { name: string; cost: CostSummary | null }[] = [...input.d2, ...input.h2, ...input.i].map((run) => ({ name: run.name, cost: run.cost }));
  lines.push(
    ...table(
      ["실행", "호출", "입력 토큰 (캐시 포함)", "그중 캐시 읽기", "출력 토큰", "요청 최대 입력", "비용 USD", "성공이 아닌 호출"],
      costRuns.map((run) => (run.cost ? [run.name, `${run.cost.calls}`, `${run.cost.inputTokens}`, `${run.cost.cacheReadTokens}`, `${run.cost.outputTokens}`, `${run.cost.maxInputTokens}`, run.cost.costUsd.toFixed(6), `${sum(run.cost.byResultKind) - (run.cost.byResultKind.success ?? 0)}/${sum(run.cost.byResultKind)}`] : [run.name, "0", "-", "-", "-", "-", "0", "-"])),
    ),
  );

  const list = toJudge(input);
  if (list.length > 0) {
    lines.push("## 7. 정답 밖의 짝과 묶음 안 불일치의 판정 (합집합)", "");
    lines.push(
      ...table(
        ["범위", "자리 1", "자리 2", "코드의 결과", "낸 실행", "판정", "근거"],
        list.map((entry) => {
          const item = judged.get(judgmentKey(entry.scope, entry.a, entry.b));
          const docs: ["a" | "b", "a" | "b"] = entry.scope === "cross" ? ["a", "b"] : [entry.scope, entry.scope];
          return [entry.scope === "cross" ? "두 문서 사이" : `문서 ${entry.scope.toUpperCase()} 안`, `\`${entry.a}\` ${raw(docs[0], entry.a)}`, `\`${entry.b}\` ${raw(docs[1], entry.b)}`, entry.outcome, entry.runs.join(", "), item ? JUDGED[item.verdict] : "판정 전", item?.basis ?? ""];
        }),
      ),
    );
  }
  return `${lines.join("\n")}\n`;
}

export { isProseId };
