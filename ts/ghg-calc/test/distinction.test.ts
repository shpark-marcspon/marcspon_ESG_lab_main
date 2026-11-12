import { describe, expect, it } from "vitest";
import { calculateRow, validateTables } from "../src/index.js";
import type { CombustionFactor, ReferenceTables } from "../src/index.js";
import { ETS_GWP, MANUFACTURING, TIER2, bare, dieselFactor, loadTables, row } from "./fixtures.js";

// 계수의 구분: 부문, 매개변수별 산정등급, 시설의 쓰임, 그 밖의 구분. 예전에는 `variant` 문자열 하나였다.
const tables = loadTables();
const opts = { gwpSetId: ETS_GWP };
const ok = (result: ReturnType<typeof calculateRow>) => {
  if (result.status !== "ok") throw new Error(`계산 실패: ${JSON.stringify(result.failure)}`);
  return result;
};
const failureOf = (result: ReturnType<typeof calculateRow>) => {
  if (result.status !== "failed") throw new Error(`실패해야 하는데 성공: ${result.co2e}`);
  return result.failure;
};

const diesel = dieselFactor(tables);
/** synthetic: 같은 연료·부문에 산정등급 조합만 다른 계수 (CO2 계수와 산화계수 값은 가상). */
const lowerTier: CombustionFactor = {
  ...diesel,
  id: "synthetic:diesel:lower-tier",
  source: { kind: "synthetic", note: "산정등급 조합만 다른 가상 계수" },
  parameterTiers: { calorific: 2, co2: 1, nonCo2: 1, oxidation: 1 },
  emissionFactors: diesel.emissionFactors.map((e) => (e.gas === "CO2" ? { ...e, value: "70000" } : e)),
  oxidationFactor: "1",
};
const both: ReferenceTables = { ...tables, factors: [...tables.factors, lowerTier] };
const { parameterTiers: _tiers, ...noTiers } = row();

describe("계수의 구분으로 고르기", () => {
  it("같은 연료·부문이라도 매개변수별 산정등급이 다르면 다른 계수다", () => {
    const tier2 = ok(calculateRow(row(), both, opts));
    const lower = ok(calculateRow(row({ parameterTiers: { calorific: 2, co2: 1, nonCo2: 1, oxidation: 1 } }), both, opts));
    expect(tier2.evidence.factor.id).toBe(diesel.id);
    expect(lower.evidence.factor.id).toBe("synthetic:diesel:lower-tier");
    expect(tier2.evidence.parameterTiers).toEqual(TIER2);
    expect(lower.evidence.parameterTiers).toEqual({ calorific: 2, co2: 1, nonCo2: 1, oxidation: 1 });
    expect(lower.co2e).not.toBe(tier2.co2e);
  });

  it("산정등급을 적지 않은 행은 산정등급을 적지 않은 계수에만 맞는다 (둘 중 하나를 고르지 않는다)", () => {
    expect(failureOf(calculateRow(noTiers, both, opts)).kind).toBe("factor_not_found");
  });

  it("구분까지 같은 계수가 둘이면 factor_ambiguous 다", () => {
    const twice: ReferenceTables = { ...tables, factors: [...tables.factors, { ...diesel, id: "synthetic:diesel:copy" }] };
    expect(failureOf(calculateRow(row(), twice, opts))).toMatchObject({
      kind: "factor_ambiguous",
      candidateIds: [diesel.id, "synthetic:diesel:copy"],
    });
  });

  it("시설의 쓰임과 그 밖의 구분도 정확히 같아야 한다", () => {
    const power: ReferenceTables = { ...tables, factors: [{ ...diesel, id: "synthetic:diesel:power", facilityUse: "power_generation", variant: "synthetic-구분" }] };
    expect(failureOf(calculateRow(row(), power, opts)).kind).toBe("factor_not_found");
    expect(failureOf(calculateRow(row({ facilityUse: "other", variant: "synthetic-구분" }), power, opts)).kind).toBe("factor_not_found");
    expect(failureOf(calculateRow(row({ facilityUse: "power_generation" }), power, opts)).kind).toBe("factor_not_found");
    const found = ok(calculateRow(row({ facilityUse: "power_generation", variant: "synthetic-구분" }), power, opts));
    expect(found.evidence.facilityUse).toBe("power_generation");
  });

  it("factorId 로 고르면 구분을 보지 않고, 근거에는 계수의 구분이 실린다", () => {
    const result = ok(calculateRow(bare({ sourceType: "stationary_combustion", factorId: "synthetic:diesel:lower-tier", amount: "1000", unit: "L" }), both, opts));
    expect(result.evidence.sector).toBe(MANUFACTURING);
    expect(result.evidence.parameterTiers).toEqual({ calorific: 2, co2: 1, nonCo2: 1, oxidation: 1 });
  });

  it("구분을 적지 않은 계수의 근거는 null 이다 (지어내지 않는다)", () => {
    const result = ok(calculateRow(bare({ sourceType: "stationary_combustion", fuel: "synthetic-fuel-b", amount: "1", unit: "t" }), tables, opts));
    expect(result.evidence.sector).toBeNull();
    expect(result.evidence.parameterTiers).toBeNull();
    expect(result.evidence.facilityUse).toBeNull();
  });

  it("구매 전력·열은 구분(variant)과 연도로만 고른다. 부문이나 산정등급을 적은 행에는 맞는 계수가 없다", () => {
    const electricity = { sourceType: "purchased_heat", amount: "1", unit: "GJ" } as const;
    const found = ok(calculateRow(bare(electricity), tables, opts));
    expect(found.evidence.factor.id).toBe("synthetic:heat");
    expect(found.evidence.sector).toBeNull();
    expect(found.evidence.parameterTiers).toBeNull();
    expect(failureOf(calculateRow(bare({ ...electricity, sector: MANUFACTURING }), tables, opts)).kind).toBe("factor_not_found");
    expect(failureOf(calculateRow(bare({ ...electricity, parameterTiers: TIER2 }), tables, opts)).kind).toBe("factor_not_found");
    expect(failureOf(calculateRow(bare({ ...electricity, variant: "synthetic-구분" }), tables, opts)).kind).toBe("factor_not_found");
  });
});

describe("기준정보 표 검사: 구분 필드", () => {
  const withDiesel = (patch: Record<string, unknown>) => ({ ...tables, factors: [{ ...diesel, ...patch }] });
  const problems = (input: unknown) => {
    const result = validateTables(input);
    return result.ok ? [] : result.problems.map((p) => `${p.where}: ${p.problem}`);
  };

  it("맞는 구분은 통과한다", () => {
    expect(problems(withDiesel({}))).toEqual([]);
    expect(problems(withDiesel({ facilityUse: "other" }))).toEqual([]);
  });

  it("산정등급이 1~4 가 아니거나 빠지면 거부한다", () => {
    expect(problems(withDiesel({ parameterTiers: { calorific: 2, co2: "2", nonCo2: 1, oxidation: 2 } }))).toEqual([
      'factors[0].parameterTiers.co2: 산정등급(1~4)이 아니다: "2"',
    ]);
    expect(problems(withDiesel({ parameterTiers: { calorific: 5, co2: 2, oxidation: 2 } }))).toEqual([
      "factors[0].parameterTiers.calorific: 산정등급(1~4)이 아니다: 5",
      "factors[0].parameterTiers.nonCo2: 산정등급(1~4)이 아니다: undefined",
    ]);
    expect(problems(withDiesel({ parameterTiers: "tier2" }))).toEqual(["factors[0].parameterTiers: 객체가 아니다"]);
  });

  it("산화계수와 그 산정등급은 같이 있거나 같이 없다", () => {
    expect(problems(withDiesel({ parameterTiers: { ...TIER2, oxidation: null } }))).toEqual([
      "factors[0].parameterTiers.oxidation: 산화계수가 있는데 그 산정등급(1~4)이 없다: null",
    ]);
    const { oxidationFactor: _dropped, ...noOxidation } = diesel;
    expect(problems({ ...tables, factors: [noOxidation] })).toEqual([
      "factors[0].parameterTiers.oxidation: 산화계수가 없으면 그 산정등급은 null 이어야 한다",
    ]);
    expect(problems({ ...tables, factors: [{ ...noOxidation, parameterTiers: { ...TIER2, oxidation: null } }] })).toEqual([]);
  });

  it("부문과 시설의 쓰임의 형식", () => {
    expect(problems(withDiesel({ sector: "" }))).toEqual(["factors[0]: sector 가 빈 문자열이거나 문자열이 아니다"]);
    expect(problems(withDiesel({ facilityUse: "발전" }))).toEqual(["factors[0]: facilityUse 가 power_generation/other 가 아니다"]);
  });
});
