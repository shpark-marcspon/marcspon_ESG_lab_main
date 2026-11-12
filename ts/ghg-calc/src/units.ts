import { formatDecimal, isNegative, mul, parseDecimal, type Decimal } from "./decimal.js";
import type { Result, Step, UnitConversion } from "./types.js";

/**
 * 환산표에서 (from, to) 쌍을 그대로 찾는다. 역방향을 유도하거나 여러 단계를 잇지 않는다
 * (역수는 유한 소수가 아닐 수 있고, 경로가 여럿이면 근거가 흐려진다).
 * 표에 없는 조합은 실패다. 예: m3 → Nm3 는 온도·압력 없이는 환산할 수 없으므로 표에 두지 않는다.
 */
export function convertUnit(
  value: Decimal,
  from: string,
  to: string,
  table: readonly UnitConversion[],
  context: string,
): Result<{ value: Decimal; step: Step | null }> {
  if (from === to) return { ok: true, value: { value, step: null } };
  const matches = table.filter((c) => c.from === from && c.to === to);
  const first = matches[0];
  if (first === undefined) {
    return { ok: false, failure: { kind: "unit_conversion_not_found", from, to, context } };
  }
  if (matches.some((c) => c.factor !== first.factor)) {
    return {
      ok: false,
      failure: { kind: "unit_conversion_ambiguous", from, to, factors: matches.map((c) => c.factor) },
    };
  }
  const factor = parseDecimal(first.factor);
  if (factor === null || isNegative(factor)) {
    return {
      ok: false,
      failure: { kind: "invalid_reference_value", where: `unitConversions[${from}->${to}].factor`, value: String(first.factor) },
    };
  }
  const converted = mul(value, factor);
  return {
    ok: true,
    value: {
      value: converted,
      step: {
        label: `단위 환산 (${context})`,
        formula: `${formatDecimal(value)} ${from} × ${formatDecimal(factor)} ${to}/${from}`,
        result: formatDecimal(converted),
        unit: to,
        conversion: first,
      },
    },
  };
}
