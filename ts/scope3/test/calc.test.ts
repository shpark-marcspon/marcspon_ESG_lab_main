import { describe, expect, it } from "vitest";
import { AdjustmentError, FactorStore, calculateRow, validateAdjustments, type CurrencyAdjustment, type Factor } from "../src/index.js";
import { row, writeSyntheticFactors } from "./helpers.js";

const store = FactorStore.load(writeSyntheticFactors());
const factor = (id: string) => store.get(id) as Factor;

/** 테스트 전용 가상 환율·물가 보정 계수. 실제 환율이 아니다. */
const FX_SYNTHETIC: CurrencyAdjustment = { currency: "KRW", targetCurrency: "USD", targetPriceYear: 2022, multiplier: "0.001", synthetic: true, source: null };

describe("계산", () => {
  it("활동량 × 계수를 십진 정확 연산으로 낸다", () => {
    expect(calculateRow(row({ itemName: "x", quantity: "0.1", unit: "kg" }), factor("syn:pp-kg"))).toMatchObject({ status: "computed", emissionsKgCo2e: "0.125", synthetic: false });
    expect(calculateRow(row({ itemName: "x", quantity: "1.5", unit: "t" }), factor("syn:steel-kg"))).toMatchObject({ status: "computed", emissionsKgCo2e: "3750", activityAmount: "1500" });
    expect(calculateRow(row({ itemName: "x", quantity: "0.3", unit: "kg" }), factor("syn:truck"))).toMatchObject({ status: "unit_mismatch" });
  });

  it("단위를 환산할 수 없으면 계산하지 않는다", () => {
    expect(calculateRow(row({ itemName: "x", unit: "대" }), factor("syn:steel-kg"))).toEqual({ status: "unit_mismatch", factorId: "syn:steel-kg", rowUnit: "대", factorUnit: "kg CO2e/kg" });
  });

  it("수량이 없거나 숫자가 아니면 0 으로 치지 않는다", () => {
    expect(calculateRow(row({ itemName: "x", quantity: null }), factor("syn:steel-kg"))).toEqual({ status: "missing_quantity", factorId: "syn:steel-kg" });
    expect(calculateRow(row({ itemName: "x", quantity: "1,000" }), factor("syn:steel-kg"))).toEqual({ status: "invalid_number", factorId: "syn:steel-kg", field: "quantity" });
  });

  it("운송 행에 거리가 없으면 needs_distance 다. 거리를 추정하지 않는다", () => {
    const transport = { origin: "여수", destination: "평택", mode: "트럭", weightKg: "21000", distanceKm: null };
    expect(calculateRow(row({ itemName: "x", quantity: null, unit: null, transport }), factor("syn:truck"))).toEqual({ status: "needs_distance", factorId: "syn:truck" });
  });

  it("지출 기반은 보정 계수가 없으면 needs_currency_adjustment 로 끝난다", () => {
    expect(calculateRow(row({ itemName: "x", amount: "2000000", currency: "KRW" }), factor("syn:spend-computer"))).toEqual({
      status: "needs_currency_adjustment",
      factorId: "syn:spend-computer",
      currency: "KRW",
      targetCurrency: "USD",
      targetPriceYear: 2022,
    });
    // 통화가 다른 보정 계수는 쓰지 않는다.
    expect(calculateRow(row({ itemName: "x", amount: "2000", currency: "EUR" }), factor("syn:spend-computer"), [FX_SYNTHETIC]).status).toBe("needs_currency_adjustment");
    // 금액이 이미 USD 여도 기준 연도 물가 보정이 필요하다.
    expect(calculateRow(row({ itemName: "x", amount: "2000", currency: "USD" }), factor("syn:spend-computer")).status).toBe("needs_currency_adjustment");
  });

  it("가상 보정 계수로 계산한 결과는 synthetic 으로 표시된다", () => {
    const result = calculateRow(row({ itemName: "x", amount: "2000000", currency: "KRW" }), factor("syn:spend-computer"), [FX_SYNTHETIC]);
    expect(result).toMatchObject({ status: "computed", emissionsKgCo2e: "1000", activityAmount: "2000", synthetic: true, adjustment: { synthetic: true } });
  });

  it("지출 기반인데 금액이 없으면 missing_amount 다", () => {
    expect(calculateRow(row({ itemName: "x", amount: null, currency: null }), factor("syn:spend-computer"), [FX_SYNTHETIC])).toEqual({ status: "missing_amount", factorId: "syn:spend-computer" });
  });

  it("출처 없는 보정 계수는 synthetic 표시가 있어야 한다", () => {
    expect(() => validateAdjustments([{ ...FX_SYNTHETIC, synthetic: false }])).toThrow(AdjustmentError);
    expect(() => validateAdjustments([{ ...FX_SYNTHETIC, multiplier: "1e-3" }])).toThrow(AdjustmentError);
    expect(() => validateAdjustments([FX_SYNTHETIC])).not.toThrow();
  });
});
