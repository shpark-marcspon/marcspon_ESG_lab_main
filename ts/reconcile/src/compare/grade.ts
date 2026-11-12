import type { GoldPair, Plant } from "./exam.js";
import type { MethodOutcome, Verdict } from "./methods.js";

// 방법 비교의 채점. 정답 쌍(같은 항목임을 아는 두 칸)과 심은 오류로 방법의 결과를 센다. 모델을 부르지 않는다.
// 수는 전부 분모와 같이 낸다.

/** 정답 쌍 하나가 그 방법에서 어떻게 나왔는가. */
export type PairStatus =
  | "match" // 그 두 칸이 짝지어져 일치로 나왔다
  | "mismatch" // 그 두 칸이 짝지어져 불일치로 나왔다
  | "needs_explanation" // 그 두 칸이 짝지어져 설명 필요로 나왔다
  | "visible_elsewhere" // 그 두 칸은 짝지어지지 않았지만, 문서 B 의 그 칸이 다른 칸과 견주어져 값 차이가 드러났다 (다른 상대와의 불일치·설명 필요, 한 문서 안의 불일치)
  | "matched_elsewhere" // 그 두 칸은 짝지어지지 않았고, 문서 B 의 그 칸이 다른 칸과 일치로 나왔다
  | "silent"; // 어디에도 값 차이가 드러나지 않았다 (짝 없음, 미확인, 언급 없음)

export interface PairGrade {
  pair: string;
  a: string;
  b: string;
  status: PairStatus;
  /** 설명 필요일 때: 코드가 견준 값이 달랐는가. */
  valuesDiffer: boolean | null;
  detail: string;
}

export function gradePair(pair: GoldPair, outcome: MethodOutcome): PairGrade {
  const base = { pair: pair.id, a: pair.a, b: pair.b };
  const exact = outcome.verdicts.find((verdict) => verdict.a === pair.a && verdict.b === pair.b);
  if (exact) return { ...base, status: exact.outcome, valuesDiffer: exact.valuesDiffer, detail: exact.reason };
  const shows = (verdict: Verdict): boolean => verdict.outcome === "mismatch" || (verdict.outcome === "needs_explanation" && verdict.valuesDiffer === true);
  const others = [...outcome.verdicts.filter((verdict) => verdict.b === pair.b), ...outcome.intra.filter((verdict) => verdict.doc === "b" && (verdict.a === pair.b || verdict.b === pair.b))];
  const visible = others.find(shows);
  const partner = (verdict: Verdict): string => ("doc" in verdict ? `문서 B 안의 ${verdict.a === pair.b ? verdict.b : verdict.a}` : `문서 A 의 ${verdict.a}`);
  if (visible) return { ...base, status: "visible_elsewhere", valuesDiffer: visible.valuesDiffer, detail: `${partner(visible)} 와 ${visible.outcome} (${visible.reason})` };
  const matched = others.find((verdict) => verdict.outcome === "match");
  if (matched) return { ...base, status: "matched_elsewhere", valuesDiffer: false, detail: `${partner(matched)} 와 일치` };
  return { ...base, status: "silent", valuesDiffer: null, detail: `A: ${outcome.states.a[pair.a] ?? "결과에 없음"} / B: ${outcome.states.b[pair.b] ?? "결과에 없음"}` };
}

export interface FalseAlarm {
  a: string;
  b: string;
  /** gold_clean: 오류를 심지 않은 정답 쌍 (값이 같은 것을 아는 쌍). planted_cell_other_partner: 오류를 심은 칸이 정답의 상대가 아닌 칸과 견주어졌다. outside_gold: 정답 쌍 밖 (원문으로 판정해야 한다). */
  kind: "gold_clean" | "planted_cell_other_partner" | "outside_gold";
  reason: string;
}

export interface Grade {
  /** 오류를 심지 않은 정답 쌍 (대조군 포함하지 않음). */
  clean: { total: number; match: number; needsExplanation: number; mismatch: number; other: number; rows: PairGrade[] };
  /** 반올림 허용폭 안의 차이를 심은 쌍 (오류가 아니다). 일치로 나와야 한다. */
  control: { total: number; match: number; rows: PairGrade[] };
  /** 오류를 심은 쌍 (칸 단위). */
  errors: { total: number; caught: number; visibleOther: number; falseMatch: number; silent: number; rows: (PairGrade & { plant: string; kind: string; bucket: "caught" | "visible_other" | "false_match" | "silent" })[] };
  /** 불일치로 나온 것 가운데 심은 오류를 정답의 상대와 잡은 것이 아닌 것. */
  mismatches: { total: number; truePlanted: number; other: FalseAlarm[] };
  /** 한 문서 안에서 불일치로 나온 것 (방법 C). */
  intraMismatches: { doc: "a" | "b"; a: string; b: string; involvesPlantedCell: boolean }[];
  /** 「확인 못 함」으로 남긴 숫자 (사유별). 방법이 숫자 단위로 낼 수 없으면 비어 있다. */
  unchecked: { a: Record<string, number>; b: Record<string, number> };
  verdicts: { match: number; mismatch: number; needsExplanation: number };
  rejected: Record<string, number>;
}

export function grade(pairs: GoldPair[], plants: Plant[], outcome: MethodOutcome): Grade {
  const plantOf = new Map(plants.flatMap((plant) => plant.cells.map((cell) => [cell.pair, plant] as const)));
  const plantedCells = new Set(plants.filter((plant) => plant.isError).flatMap((plant) => plant.cells.map((cell) => cell.cell)));
  const result: Grade = {
    clean: { total: 0, match: 0, needsExplanation: 0, mismatch: 0, other: 0, rows: [] },
    control: { total: 0, match: 0, rows: [] },
    errors: { total: 0, caught: 0, visibleOther: 0, falseMatch: 0, silent: 0, rows: [] },
    mismatches: { total: 0, truePlanted: 0, other: [] },
    intraMismatches: [],
    unchecked: { a: {}, b: {} },
    verdicts: { match: 0, mismatch: 0, needsExplanation: 0 },
    rejected: outcome.rejected,
  };
  const errorPairs = new Set<string>();
  for (const pair of pairs) {
    const row = gradePair(pair, outcome);
    const plant = plantOf.get(pair.id);
    if (!plant) {
      result.clean.total += 1;
      result.clean.rows.push(row);
      if (row.status === "match") result.clean.match += 1;
      else if (row.status === "needs_explanation") result.clean.needsExplanation += 1;
      else if (row.status === "mismatch") result.clean.mismatch += 1;
      else result.clean.other += 1;
      continue;
    }
    if (!plant.isError) {
      result.control.total += 1;
      result.control.rows.push(row);
      if (row.status === "match") result.control.match += 1;
      continue;
    }
    errorPairs.add(`${pair.a}|${pair.b}`);
    result.errors.total += 1;
    // 「잡은 오류」는 그 쌍이 불일치로 나온 것만이다. 값 차이가 다른 꼴로 드러난 것과 조용히 지나간 것은 따로 센다.
    const bucket = row.status === "mismatch" ? "caught" : (row.status === "needs_explanation" && row.valuesDiffer === true) || row.status === "visible_elsewhere" ? "visible_other" : row.status === "match" || row.status === "matched_elsewhere" ? "false_match" : "silent";
    if (bucket === "caught") result.errors.caught += 1;
    else if (bucket === "visible_other") result.errors.visibleOther += 1;
    else if (bucket === "false_match") result.errors.falseMatch += 1;
    else result.errors.silent += 1;
    result.errors.rows.push({ ...row, plant: plant.id, kind: plant.kind, bucket });
  }
  const cleanPairs = new Set(result.clean.rows.concat(result.control.rows).map((row) => `${row.a}|${row.b}`));
  for (const verdict of outcome.verdicts) {
    if (verdict.outcome === "match") result.verdicts.match += 1;
    else if (verdict.outcome === "needs_explanation") result.verdicts.needsExplanation += 1;
    else {
      result.verdicts.mismatch += 1;
      result.mismatches.total += 1;
      const key = `${verdict.a}|${verdict.b}`;
      if (errorPairs.has(key)) result.mismatches.truePlanted += 1;
      else result.mismatches.other.push({ a: verdict.a, b: verdict.b, kind: cleanPairs.has(key) ? "gold_clean" : plantedCells.has(verdict.b) ? "planted_cell_other_partner" : "outside_gold", reason: verdict.reason });
    }
  }
  for (const verdict of outcome.intra) if (verdict.outcome === "mismatch") result.intraMismatches.push({ doc: verdict.doc, a: verdict.a, b: verdict.b, involvesPlantedCell: verdict.doc === "b" && (plantedCells.has(verdict.a) || plantedCells.has(verdict.b)) });
  for (const side of ["a", "b"] as const) for (const state of Object.values(outcome.states[side])) result.unchecked[side][state] = (result.unchecked[side][state] ?? 0) + 1;
  return result;
}

const STATUS_KO: Record<PairStatus, string> = { match: "일치", mismatch: "불일치", needs_explanation: "설명 필요", visible_elsewhere: "다른 칸과 견주어져 드러남", matched_elsewhere: "다른 칸과 일치", silent: "조용히 지나감" };

export interface MethodGrade {
  method: string;
  title: string;
  /** 방법이 「확인 못 함」을 숫자 단위로 낼 수 있는가. */
  reportsUnchecked: boolean;
  /** 방법이 「일치」를 낼 수 있는가 (E 는 어긋남만 낸다). */
  reportsMatches: boolean;
  grade: Grade;
  cost: { calls: number; inputTokens: number; outputTokens: number; costUsd: number; byResultKind: Record<string, number> } | null;
}

/** 방법 × 항목 비교표와 오류별 × 방법별 표. */
export function renderComparison(methods: MethodGrade[], plants: Plant[]): string {
  const sum = (bag: Record<string, number>): number => Object.values(bag).reduce((total, count) => total + count, 0);
  const lines = ["## 방법 × 항목 비교표", "", `| 항목 | ${methods.map((item) => item.method).join(" | ")} |`, `|---|${methods.map(() => "---:").join("|")}|`];
  const row = (name: string, cell: (item: MethodGrade) => string): void => void lines.push(`| ${name} | ${methods.map(cell).join(" | ")} |`);
  row("1. 참 일치 (오류를 심지 않은 정답 쌍 중 일치)", (item) => (item.reportsMatches ? `${item.grade.clean.match}/${item.grade.clean.total}` : `낼 수 없음 (0/${item.grade.clean.total})`));
  row("　그 쌍이 설명 필요로", (item) => `${item.grade.clean.needsExplanation}/${item.grade.clean.total}`);
  row("　그 쌍이 불일치로 (거짓 경보)", (item) => `${item.grade.clean.mismatch}/${item.grade.clean.total}`);
  row("　대조군(반올림 허용폭 안)이 일치로", (item) => (item.reportsMatches ? `${item.grade.control.match}/${item.grade.control.total}` : `낼 수 없음 (0/${item.grade.control.total})`));
  row("2. 잡은 오류 (심은 오류 칸 중 그 쌍이 불일치)", (item) => `${item.grade.errors.caught}/${item.grade.errors.total}`);
  row("　값 차이가 다른 꼴로 드러남", (item) => `${item.grade.errors.visibleOther}/${item.grade.errors.total}`);
  row("3. 조용히 지나간 오류", (item) => `${item.grade.errors.silent + item.grade.errors.falseMatch}/${item.grade.errors.total}`);
  row("　그중 일치로 나온 것 (거짓 일치)", (item) => `${item.grade.errors.falseMatch}/${item.grade.errors.total}`);
  row("4. 불일치로 나온 짝 (전체)", (item) => `${item.grade.mismatches.total}`);
  row("　심은 오류를 정답의 상대와 잡은 것", (item) => `${item.grade.mismatches.truePlanted}/${item.grade.mismatches.total}`);
  row("　그 밖 (건별 판정 대상)", (item) => `${item.grade.mismatches.other.length}/${item.grade.mismatches.total}`);
  row("　한 문서 안의 불일치", (item) => `${item.grade.intraMismatches.length}`);
  row("5. 「확인 못 함」을 숫자 단위로 내는가", (item) => (item.reportsUnchecked ? `낸다 (A ${sum(item.grade.unchecked.a)}개, B ${sum(item.grade.unchecked.b)}개)` : "못 낸다"));
  row("　견준 짝: 일치 / 불일치 / 설명 필요", (item) => `${item.grade.verdicts.match} / ${item.grade.verdicts.mismatch} / ${item.grade.verdicts.needsExplanation}`);
  row("　버려진 답", (item) => `${sum(item.grade.rejected)}`);
  row("6. 호출 수", (item) => (item.cost ? `${item.cost.calls}` : "0 (저장된 답)"));
  row("　입력 / 출력 토큰", (item) => (item.cost ? `${item.cost.inputTokens} / ${item.cost.outputTokens}` : "-"));
  row("　비용 USD", (item) => (item.cost ? item.cost.costUsd.toFixed(6) : "0"));
  row("　성공이 아닌 호출", (item) => (item.cost ? `${sum(item.cost.byResultKind) - (item.cost.byResultKind.success ?? 0)}/${sum(item.cost.byResultKind)}` : "-"));
  lines.push("", "## 심은 오류별 × 방법별", "", `| 오류 | 종류 | 칸 (원래 → 바꾼 글자) | ${methods.map((item) => item.method).join(" | ")} |`, `|---|---|---|${methods.map(() => "---").join("|")}|`);
  for (const plant of plants) {
    for (const cell of plant.cells) {
      const cells = methods.map((item) => {
        const found = [...item.grade.errors.rows, ...item.grade.control.rows].find((entry) => entry.pair === cell.pair);
        return found ? STATUS_KO[found.status] : "?";
      });
      lines.push(`| ${plant.id}${plant.isError ? "" : " (대조군)"} | ${plant.kind} | ${cell.cell} ${cell.original} → ${cell.planted} | ${cells.join(" | ")} |`);
    }
  }
  return `${lines.join("\n")}\n`;
}
