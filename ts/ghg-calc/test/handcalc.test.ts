import { describe, expect, it } from "vitest";
import { calculateRow } from "../src/index.js";
import { handCases, loadTables } from "./fixtures.js";

// 검산 사례: 엔진과 따로 계산한 기대값(data/ghg-calc/handcalc-cases.json)과 문자열까지 같아야 한다.
describe("검산 사례 (오차 0)", () => {
  const tables = loadTables();

  it("사례가 10건 이상이다", () => {
    expect(handCases.length).toBeGreaterThanOrEqual(10);
  });

  it("사례에 옮겨 적은 계수와 GWP 가 기준정보 표의 값과 같다", () => {
    for (const c of handCases) {
      const factor = tables.factors.find((f) => f.id === c.factor.id);
      expect(factor, c.id).toMatchObject(c.factor);
      expect(tables.gwpSets.find((s) => s.id === c.gwpSetId)?.values, c.id).toEqual(c.gwp);
    }
  });

  it.each(handCases.map((c) => [c.id, c.title, c] as const))("%s %s", (_id, _title, c) => {
    const result = calculateRow(c.activity, tables, { gwpSetId: c.gwpSetId });
    if (result.status !== "ok") throw new Error(`계산 실패: ${JSON.stringify(result.failure)}`);

    expect(result.co2e).toBe(c.expected.co2e);
    expect(result.energy).toEqual(c.expected.energy);
    expect(result.evidence.factor.id).toBe(c.expected.factorId);
    expect(result.evidence.gwpSet.id).toBe(c.gwpSetId);
    expect(Object.fromEntries(result.gases.map((g) => [g.gas, { mass: g.mass, co2e: g.co2e }]))).toEqual(c.expected.gases);
  });
});
