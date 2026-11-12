import { compare, dec, neg, roundToPow10, shift10, show, sub } from "./dec.js";
import type { Fact, FactWhere } from "./facts.js";
import type { Catalog, Label } from "./label.js";
import { resolveUnit, type ResolvedUnit, type UnitTable } from "./units.js";

// 짝짓기와 비교. 전부 코드다.
// 짝의 열쇠는 뜻 (지표, 기간, 범위) 이다. 값은 열쇠에 들어가지 않는다: 값이 같다는 이유로 짝을 짓지 않는다.
// 짝이 정해진 다음에야 값을 견준다.

export type Outcome = "match" | "mismatch" | "unpaired" | "unverified" | "needs_explanation";

export type UnverifiedReason =
  | "out_of_scope_sentence" // v1 은 표 숫자만 본다
  | "out_of_scope_table_text_cell"
  | "out_of_scope_number_only_block"
  | "table_low_confidence" // 표 구조를 믿을 수 없어 뜻을 붙이지 않았다
  | "not_answered" // AI 가 이 숫자에 답하지 않았다
  | "answer_rejected" // AI 의 답이 확인을 통과하지 못했다
  | "llm_call_failed"
  | "not_requested" // 이 실행에서는 이 표를 모델에 보내지 않았다 (작은 실행)
  | "labeled_not_data" // AI 가 데이터 값이 아니라고 했다
  | "not_in_catalog" // 기준 목록에 없는 지표
  | "low_confidence_label"
  | "period_unknown"
  | "unit_unknown" // 단위 글자를 단위 표에서 찾지 못했다
  | "unit_family_mismatch" // 지표의 단위 계열과 다른 단위가 붙었다
  | "ambiguous_pair" // 같은 뜻의 숫자가 한쪽에 여럿이다
  | "repeated_key_in_table"; // 한 표 안에서 같은 뜻(지표, 기간, 범위)이 둘 이상의 숫자에 붙었다 (선택 규칙)

export interface FactView {
  factId: string;
  docId: string;
  raw: string;
  value: string;
  where: FactWhere;
  tableStatus: Fact["tableStatus"];
  footnoteMarks: string[];
  flags: string[];
  label: Label | null;
  /** 엑셀이면 저장 값과 화면 표기. */
  stored: Fact["stored"];
}

export interface Comparison {
  unitA: string | null;
  unitB: string | null;
  /** 기준 단위로 옮긴 값. 환산은 10 의 거듭제곱 자리 옮김뿐이다. */
  baseA: string;
  baseB: string;
  /** 적용한 환산: 「표기 값 × 10^exp = 기준 단위 값」. */
  expA: number;
  expB: number;
  /** 견준 자리: 두 값 중 표시가 거친 쪽의 끝자리 (10^quantumExp). */
  quantumExp: number;
  rule: "exact" | "rounded_half_up" | "rounded_half_even" | "none";
  difference: string;
  notes: string[];
}

export interface Finding {
  outcome: Outcome;
  reason: string;
  /** 미확인·설명 필요의 자세한 사정 (버려진 답의 까닭 등). */
  detail: string | null;
  key: { indicator: string; period: string; boundary: string } | null;
  a: FactView | null;
  b: FactView | null;
  comparison: Comparison | null;
}

/** 뜻이 붙기 전에 이미 미확인으로 정해진 사실 후보. */
export interface Pending {
  fact: Fact;
  label: Label | null;
  unverified: { reason: UnverifiedReason; detail: string | null } | null;
}

export interface MatchOptions {
  catalog: Catalog;
  units: UnitTable;
  /** 이 등급 아래의 확신으로 붙은 뜻은 짝짓기에 쓰지 않는다. */
  minConfidence: "low" | "medium" | "high";
  /** 문서에 기간이 안 적힌 표를 위한 기본 기간 (문서 A, B). */
  defaultPeriod: { a: string | null; b: string | null };
  /**
   * 선택 규칙 (기본은 끔). 범위가 한쪽만 `unspecified` 이면 짝짓고 값을 견준다. 범위가 달랐다는 것은 결과의 `notes` 에 남긴다.
   * 양쪽에 다르게 적힌 범위는 끌 때와 같이 「설명 필요」다. 값은 여전히 열쇠가 아니다.
   */
  looseUnspecifiedBoundary?: boolean;
  /**
   * 선택 규칙 (기본은 끔). 한 표 안에서 (지표, 기간, 범위)가 같은 답이 둘 이상이면 그 답들을 버린다 (미확인 `repeated_key_in_table`).
   * 한 표가 같은 뜻의 값을 두 번 싣는 일은 드물고, 그런 답은 부분합·다른 주체의 값에 지표가 억지로 붙은 것일 때가 많다. 값은 보지 않는다.
   */
  dropRepeatedKeysInTable?: boolean;
}

const CONFIDENCE_RANK = { low: 0, medium: 1, high: 2 } as const;

export interface Keyed {
  fact: Fact;
  label: Label;
  indicator: string;
  period: string;
  boundary: string;
  unit: ResolvedUnit | null;
  notes: string[];
}

export const view = (fact: Fact, label: Label | null): FactView => ({
  factId: fact.id,
  docId: fact.docId,
  raw: fact.raw,
  value: fact.value,
  where: fact.where,
  tableStatus: fact.tableStatus,
  footnoteMarks: fact.footnoteMarks,
  flags: fact.flags,
  label,
  stored: fact.stored,
});

function unverified(side: "a" | "b", fact: Fact, label: Label | null, reason: UnverifiedReason, detail: string | null = null): Finding {
  return { outcome: "unverified", reason, detail, key: null, a: side === "a" ? view(fact, label) : null, b: side === "b" ? view(fact, label) : null, comparison: null };
}

function keyOf(side: "a" | "b", pending: Pending, options: MatchOptions): Keyed | Finding {
  const { fact, label } = pending;
  if (pending.unverified) return unverified(side, fact, label, pending.unverified.reason, pending.unverified.detail);
  if (!label) return unverified(side, fact, null, "not_answered");
  if (label.kind === "not_data") return unverified(side, fact, label, "labeled_not_data");
  if (label.kind === "not_in_catalog" || label.indicatorKey === null) return unverified(side, fact, label, "not_in_catalog", label.freeText);
  if (CONFIDENCE_RANK[label.confidence] < CONFIDENCE_RANK[options.minConfidence]) return unverified(side, fact, label, "low_confidence_label");
  const notes: string[] = [];
  let period = label.period;
  if (period === null && options.defaultPeriod[side] !== null) {
    period = options.defaultPeriod[side];
    notes.push("period_from_document_default");
  }
  if (period === null) return unverified(side, fact, label, "period_unknown");
  const indicator = options.catalog.indicators.find((item) => item.key === label.indicatorKey);
  if (!indicator) return unverified(side, fact, label, "not_in_catalog", label.indicatorKey);
  const unitText = label.unit ?? fact.unitSuffix;
  let unit: ResolvedUnit | null = null;
  if (unitText !== null) {
    unit = resolveUnit(unitText, options.units);
    if (!unit) return unverified(side, fact, label, "unit_unknown", unitText);
    if (unit.family !== indicator.unitFamily) return unverified(side, fact, label, "unit_family_mismatch", `${unitText} (${unit.family}) ≠ ${indicator.unitFamily}`);
  }
  return { fact, label, indicator: indicator.key, period, boundary: label.boundary, unit, notes };
}

const isFinding = (value: Keyed | Finding): value is Finding => "outcome" in value;

/** 소수점 위치를 무시하고 유효 숫자열이 같은가. 단위·자릿수 착오의 실마리다 (판정에는 쓰지 않는다). */
function sameDigits(a: string, b: string): boolean {
  const digits = (text: string): string => text.replace(/^-/, "").replace(".", "").replace(/^0+/, "").replace(/0+$/, "");
  return digits(a) !== "" && digits(a) === digits(b);
}

const noteKey = (text: string | null): string => (text ?? "").normalize("NFKC").replace(/[\s·,()\-]/g, "").toLowerCase();

/** 짝이 정해진 두 값을 견준다. */
export function compareValues(a: Keyed, b: Keyed): { outcome: Outcome; reason: string; comparison: Comparison } {
  const notes = [...a.notes, ...b.notes];
  const expA = a.unit?.exp ?? 0;
  const expB = b.unit?.exp ?? 0;
  const baseA = shift10(dec(a.fact.value), expA);
  const baseB = shift10(dec(b.fact.value), expB);
  const quantumA = expA - a.fact.decimals;
  const quantumB = expB - b.fact.decimals;
  const quantumExp = Math.max(quantumA, quantumB);
  const difference = show(sub(baseA, baseB));
  const comparison: Comparison = {
    unitA: a.unit?.text ?? null,
    unitB: b.unit?.text ?? null,
    baseA: show(baseA),
    baseB: show(baseB),
    expA,
    expB,
    quantumExp,
    rule: "none",
    difference,
    notes,
  };
  // 한쪽에만 단위가 있으면 환산할 수 없다. 값이 같아 보여도 일치라고 하지 않는다.
  if ((a.unit === null) !== (b.unit === null)) return { outcome: "needs_explanation", reason: "unit_missing_one_side", comparison };
  if (a.unit === null && b.unit === null) notes.push("unit_not_stated");
  if (a.unit && b.unit && a.unit.family !== b.unit.family) return { outcome: "needs_explanation", reason: "unit_family_differs", comparison };

  let equal = compare(baseA, baseB) === 0;
  if (equal) comparison.rule = "exact";
  else {
    for (const mode of ["half_up", "half_even"] as const) {
      if (compare(roundToPow10(baseA, quantumExp, mode), roundToPow10(baseB, quantumExp, mode)) === 0) {
        comparison.rule = mode === "half_up" ? "rounded_half_up" : "rounded_half_even";
        equal = true;
        break;
      }
    }
  }
  const basisDiffers = noteKey(a.label.basisNote) !== "" && noteKey(b.label.basisNote) !== "" && noteKey(a.label.basisNote) !== noteKey(b.label.basisNote);
  const signUnsure = [...a.fact.flags, ...b.fact.flags].some((flag) => flag === "sign_ambiguous");
  if (equal) {
    if (basisDiffers) return { outcome: "needs_explanation", reason: "basis_note_differs", comparison };
    if (signUnsure) return { outcome: "needs_explanation", reason: "sign_ambiguous", comparison };
    return { outcome: "match", reason: comparison.rule, comparison };
  }
  if (compare(roundToPow10(baseA, quantumExp, "down"), roundToPow10(baseB, quantumExp, "down")) === 0) return { outcome: "needs_explanation", reason: "matches_if_truncated", comparison };
  if (compare(baseA, neg(baseB)) === 0) return { outcome: "needs_explanation", reason: "sign_differs", comparison };
  if (basisDiffers) return { outcome: "needs_explanation", reason: "basis_note_differs", comparison };
  if (sameDigits(show(baseA), show(baseB)) || sameDigits(a.fact.value, b.fact.value)) notes.push("same_digits_different_scale");
  return { outcome: "mismatch", reason: "values_differ", comparison };
}

const UNSPECIFIED = "unspecified";
const groupKey = (item: Keyed): string => `${item.indicator}|${item.period}|${item.boundary}`;

/** 두 문서의 사실 후보를 뜻으로 짝짓고 값을 견준다. 모든 값 후보가 결과 하나에 정확히 한 번 들어간다. */
export function reconcile(a: Pending[], b: Pending[], options: MatchOptions): Finding[] {
  const findings: Finding[] = [];
  const keyed = { a: [] as Keyed[], b: [] as Keyed[] };
  for (const side of ["a", "b"] as const) {
    for (const pending of side === "a" ? a : b) {
      const result = keyOf(side, pending, options);
      if (isFinding(result)) findings.push(result);
      else keyed[side].push(result);
    }
    if (options.dropRepeatedKeysInTable) {
      const perTable = new Map<string, Keyed[]>();
      for (const item of keyed[side]) {
        if (item.fact.where.gridId === null) continue;
        const key = `${item.fact.where.gridId}|${groupKey(item)}`;
        perTable.set(key, [...(perTable.get(key) ?? []), item]);
      }
      const dropped = new Set<Keyed>();
      for (const items of perTable.values()) {
        if (items.length < 2) continue;
        for (const item of items) {
          dropped.add(item);
          findings.push({ ...unverified(side, item.fact, item.label, "repeated_key_in_table", `이 표에서 같은 뜻이 ${items.length}개의 숫자에 붙었다`), key: { indicator: item.indicator, period: item.period, boundary: item.boundary } });
        }
      }
      keyed[side] = keyed[side].filter((item) => !dropped.has(item));
    }
  }
  const groups = new Map<string, { a: Keyed[]; b: Keyed[] }>();
  for (const side of ["a", "b"] as const) {
    for (const item of keyed[side]) {
      const group = groups.get(groupKey(item)) ?? { a: [], b: [] };
      group[side].push(item);
      groups.set(groupKey(item), group);
    }
  }
  const leftovers = { a: [] as Keyed[], b: [] as Keyed[] };
  for (const group of groups.values()) {
    if (group.a.length > 1 || group.b.length > 1) {
      // 한쪽에 여럿이면 어느 것과 견줄지 정할 수 없다. 값을 보고 고르지 않는다.
      for (const side of ["a", "b"] as const) {
        const values = new Set(group[side].map((item) => show(shift10(dec(item.fact.value), item.unit?.exp ?? 0))));
        const detail = `이 문서에 ${group[side].length}개, 상대 문서에 ${group[side === "a" ? "b" : "a"].length}개${group[side].length > 1 ? (values.size === 1 ? " (이 문서의 값은 서로 같다)" : " (이 문서의 값이 서로 다르다)") : ""}`;
        for (const item of group[side]) findings.push({ ...unverified(side, item.fact, item.label, "ambiguous_pair", detail), key: { indicator: item.indicator, period: item.period, boundary: item.boundary } });
      }
      continue;
    }
    const [left] = group.a;
    const [right] = group.b;
    if (left && right) {
      const result = compareValues(left, right);
      findings.push({ outcome: result.outcome, reason: result.reason, detail: null, key: { indicator: left.indicator, period: left.period, boundary: left.boundary }, a: view(left.fact, left.label), b: view(right.fact, right.label), comparison: result.comparison });
    } else if (left) leftovers.a.push(left);
    else if (right) leftovers.b.push(right);
  }
  // 지표와 기간은 같은데 범위만 다른 것이 양쪽에 하나씩 남았으면, 견줄 상대는 있지만 같은 것이라고 할 수 없다: 설명 필요.
  const loose = new Map<string, { a: Keyed[]; b: Keyed[] }>();
  for (const side of ["a", "b"] as const) {
    for (const item of leftovers[side]) {
      const key = `${item.indicator}|${item.period}`;
      const group = loose.get(key) ?? { a: [], b: [] };
      group[side].push(item);
      loose.set(key, group);
    }
  }
  for (const group of loose.values()) {
    const [left] = group.a;
    const [right] = group.b;
    if (group.a.length === 1 && group.b.length === 1 && left && right) {
      const result = compareValues(left, right);
      if (options.looseUnspecifiedBoundary && (left.boundary === UNSPECIFIED || right.boundary === UNSPECIFIED)) {
        // 한쪽에 범위가 안 적혀 있다: 견줄 수 있다고 보고 값을 견준다. 범위가 달랐다는 것은 표시로 남긴다.
        result.comparison.notes.push("boundary_unspecified_one_side");
        findings.push({ outcome: result.outcome, reason: result.reason, detail: `범위 ${left.boundary} 와 ${right.boundary}`, key: { indicator: left.indicator, period: left.period, boundary: `${left.boundary}~${right.boundary}` }, a: view(left.fact, left.label), b: view(right.fact, right.label), comparison: result.comparison });
        continue;
      }
      findings.push({
        outcome: "needs_explanation",
        reason: "boundary_differs",
        detail: `범위 ${left.boundary} 와 ${right.boundary}. 값만 보면 ${result.outcome === "match" ? "같다" : "다르다"}`,
        key: { indicator: left.indicator, period: left.period, boundary: `${left.boundary}≠${right.boundary}` },
        a: view(left.fact, left.label),
        b: view(right.fact, right.label),
        comparison: result.comparison,
      });
      continue;
    }
    // 범위 표시 없는 숫자와 견줄 후보가 상대 문서에 여럿이면 고르지 않는다 (선택 규칙을 켰을 때만. 끄면 아래의 「짝 없음」).
    const looseAmbiguous = options.looseUnspecifiedBoundary === true && group.a.length > 0 && group.b.length > 0 && [...group.a, ...group.b].some((item) => item.boundary === UNSPECIFIED);
    for (const side of ["a", "b"] as const) {
      for (const item of group[side]) {
        if (looseAmbiguous) {
          findings.push({ ...unverified(side, item.fact, item.label, "ambiguous_pair", `범위가 다른 후보: 이 문서에 ${group[side].length}개, 상대 문서에 ${group[side === "a" ? "b" : "a"].length}개`), key: { indicator: item.indicator, period: item.period, boundary: item.boundary } });
          continue;
        }
        findings.push({ outcome: "unpaired", reason: "no_counterpart", detail: null, key: { indicator: item.indicator, period: item.period, boundary: item.boundary }, a: side === "a" ? view(item.fact, item.label) : null, b: side === "b" ? view(item.fact, item.label) : null, comparison: null });
      }
    }
  }
  return findings;
}
