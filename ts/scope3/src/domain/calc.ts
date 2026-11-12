import { formatDecimal, mul, parseDecimal, type Decimal } from "kernel";
import type { Factor } from "./factor.js";
import type { InputRow } from "./types.js";
import { conversionFactor, unitDim } from "./units.js";

/** 이미 CO2 환산 kg 인 분자 단위 표기. 이 밖의 분자는 계산하지 않는다. */
const KG_CO2E_NUMERATORS = new Set(["kg CO2e", "kg CO2 eq"]);

/**
 * 지출 기반 계수(USD, 기준 연도 가격)에 맞추는 환율·물가 보정 계수.
 * 금액 × multiplier = 계수의 통화·기준 연도 금액. 어떤 환율과 물가지수를 쓸지는 결정 대기라 저장소에 실제 값이 없다.
 * 출처(URL, 확인 일자)가 없으면 `synthetic: true` 여야 한다.
 */
export interface CurrencyAdjustment {
  currency: string;
  targetCurrency: string;
  targetPriceYear: number;
  /** 십진 문자열. */
  multiplier: string;
  synthetic: boolean;
  source: { url: string; checkedOn: string } | null;
}

export class AdjustmentError extends Error {
  override name = "AdjustmentError";
}

export function validateAdjustments(adjustments: readonly CurrencyAdjustment[]): void {
  for (const adjustment of adjustments) {
    if (parseDecimal(adjustment.multiplier) === null) throw new AdjustmentError(`보정 계수가 십진 문자열이 아니다: ${adjustment.multiplier}`);
    if (!adjustment.synthetic && adjustment.source === null) {
      throw new AdjustmentError(`${adjustment.currency} 보정 계수에 출처가 없다. 출처를 적거나 synthetic 으로 표시한다`);
    }
  }
}

export type CalcResult =
  | {
      status: "computed";
      /** kg CO2e, 십진 문자열. 반올림하지 않는다. */
      emissionsKgCo2e: string;
      factorId: string;
      factorValueRaw: string;
      factorUnit: string;
      /** 계수의 분모 단위로 맞춘 활동량. */
      activityAmount: string;
      formula: string;
      adjustment: CurrencyAdjustment | null;
      /** 가상 보정 계수가 들어간 결과면 true. 실제 배출량으로 쓰지 않는다. */
      synthetic: boolean;
    }
  /** 운송 행에 거리가 없다. 거리는 입력으로 받고 추정하지 않는다. */
  | { status: "needs_distance"; factorId: string }
  /** 지출 기반 계수인데 그 통화의 환율·물가 보정 계수가 없다. */
  | { status: "needs_currency_adjustment"; factorId: string; currency: string | null; targetCurrency: string; targetPriceYear: number }
  | { status: "missing_quantity"; factorId: string }
  | { status: "missing_amount"; factorId: string }
  | { status: "invalid_number"; factorId: string; field: "quantity" | "amount" | "weightKg" | "distanceKm" }
  | { status: "unit_mismatch"; factorId: string; rowUnit: string | null; factorUnit: string }
  | { status: "unsupported_factor_unit"; factorId: string; factorUnit: string }
  /** 추천이 없어 계산하지 않았다. */
  | { status: "not_calculated"; reason: string };

type Amount = { ok: true; value: Decimal; text: string } | { ok: false; result: CalcResult };

function physicalAmount(row: InputRow, factor: Factor): Amount {
  const id = factor.id;
  if (row.transport && row.transport.weightKg !== null) {
    const toFactor = conversionFactor("tonne_km", factor.denominatorDim);
    if (toFactor === null) return { ok: false, result: { status: "unit_mismatch", factorId: id, rowUnit: "tonne.km", factorUnit: factor.unit } };
    const weight = parseDecimal(row.transport.weightKg);
    if (weight === null) return { ok: false, result: { status: "invalid_number", factorId: id, field: "weightKg" } };
    if (row.transport.distanceKm === null) return { ok: false, result: { status: "needs_distance", factorId: id } };
    const distance = parseDecimal(row.transport.distanceKm);
    if (distance === null) return { ok: false, result: { status: "invalid_number", factorId: id, field: "distanceKm" } };
    const kgToTonne = parseDecimal(conversionFactor("kg", "tonne")) as Decimal;
    const value = mul(mul(weight, kgToTonne), distance);
    return { ok: true, value, text: `${row.transport.weightKg} kg × 0.001 × ${row.transport.distanceKm} km` };
  }
  const toFactor = conversionFactor(unitDim(row.unit), factor.denominatorDim);
  if (toFactor === null) return { ok: false, result: { status: "unit_mismatch", factorId: id, rowUnit: row.unit, factorUnit: factor.unit } };
  if (row.quantity === null) return { ok: false, result: { status: "missing_quantity", factorId: id } };
  const quantity = parseDecimal(row.quantity);
  if (quantity === null) return { ok: false, result: { status: "invalid_number", factorId: id, field: "quantity" } };
  const value = mul(quantity, parseDecimal(toFactor) as Decimal);
  return { ok: true, value, text: toFactor === "1" ? `${row.quantity} ${row.unit ?? ""}`.trim() : `${row.quantity} ${row.unit ?? ""} × ${toFactor}` };
}

/**
 * 한 행의 배출량 = 활동량(또는 보정한 금액) × 계수. 계산은 전부 십진 정확 연산이고 LLM 은 관여하지 않는다.
 * 계산할 수 없는 사정은 0 이나 빈 값이 아니라 상태로 돌려준다.
 */
export function calculateRow(row: InputRow, factor: Factor, adjustments: readonly CurrencyAdjustment[] = []): CalcResult {
  if (!KG_CO2E_NUMERATORS.has(factor.unitNumerator)) return { status: "unsupported_factor_unit", factorId: factor.id, factorUnit: factor.unit };
  const value = parseDecimal(factor.valueRaw) as Decimal;

  if (factor.factorType === "spend") {
    const spend = factor.spend;
    if (spend === null) return { status: "unsupported_factor_unit", factorId: factor.id, factorUnit: factor.unit };
    if (row.amount === null) return { status: "missing_amount", factorId: factor.id };
    const amount = parseDecimal(row.amount);
    if (amount === null) return { status: "invalid_number", factorId: factor.id, field: "amount" };
    const adjustment = adjustments.find(
      (candidate) => candidate.currency === row.currency && candidate.targetCurrency === spend.currency && candidate.targetPriceYear === spend.priceYear,
    );
    if (!adjustment) {
      return { status: "needs_currency_adjustment", factorId: factor.id, currency: row.currency, targetCurrency: spend.currency, targetPriceYear: spend.priceYear };
    }
    const adjusted = mul(amount, parseDecimal(adjustment.multiplier) as Decimal);
    return {
      status: "computed",
      emissionsKgCo2e: formatDecimal(mul(adjusted, value)),
      factorId: factor.id,
      factorValueRaw: factor.valueRaw,
      factorUnit: factor.unit,
      activityAmount: formatDecimal(adjusted),
      formula: `${row.amount} ${row.currency ?? ""} × ${adjustment.multiplier} × ${factor.valueRaw} ${factor.unit}`,
      adjustment,
      synthetic: adjustment.synthetic,
    };
  }

  const amount = physicalAmount(row, factor);
  if (!amount.ok) return amount.result;
  return {
    status: "computed",
    emissionsKgCo2e: formatDecimal(mul(amount.value, value)),
    factorId: factor.id,
    factorValueRaw: factor.valueRaw,
    factorUnit: factor.unit,
    activityAmount: formatDecimal(amount.value),
    formula: `${amount.text} × ${factor.valueRaw} ${factor.unit}`,
    adjustment: null,
    synthetic: false,
  };
}
