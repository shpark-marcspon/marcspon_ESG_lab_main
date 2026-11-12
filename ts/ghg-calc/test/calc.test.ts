import { describe, expect, it } from "vitest";
import { calculateRow, convertUnit, parseDecimal } from "../src/index.js";
import type { ReferenceTables } from "../src/index.js";
import { AR5_GWP, COMMERCIAL, DIESEL_ID, ETS_GWP, MANUFACTURING, SYNTHETIC_GWP, TIER2, bare, dieselFactor, loadTables, row } from "./fixtures.js";

const tables = loadTables();
const ok = (result: ReturnType<typeof calculateRow>) => {
  if (result.status !== "ok") throw new Error(`계산 실패: ${JSON.stringify(result.failure)}`);
  return result;
};
const failureOf = (result: ReturnType<typeof calculateRow>) => {
  if (result.status !== "failed") throw new Error(`실패해야 하는데 성공: ${result.co2e}`);
  return result.failure;
};

describe("단위 환산", () => {
  const one = parseDecimal("1")!;
  it.each([
    ["L", "kL", "0.001"],
    ["kL", "L", "1000"],
    ["m3", "L", "1000"],
    ["kg", "t", "0.001"],
    ["t", "kg", "1000"],
    ["kWh", "MWh", "0.001"],
    ["kWh", "MJ", "3.6"],
    ["MWh", "GJ", "3.6"],
    ["MJ", "GJ", "0.001"],
    ["GJ", "TJ", "0.001"],
    ["MJ", "TJ", "0.000001"],
    ["Nm3", "kNm3", "0.001"],
  ])("1 %s = %s 의 %s 배", (from, to, expected) => {
    const result = convertUnit(one, from, to, tables.unitConversions, "test");
    if (!result.ok) throw new Error(JSON.stringify(result.failure));
    expect(result.value.step?.result).toBe(expected);
  });

  it("같은 단위는 환산 단계 없이 그대로다", () => {
    const result = convertUnit(one, "L", "L", [], "test");
    expect(result).toEqual({ ok: true, value: { value: one, step: null } });
  });

  it.each([
    ["m3", "Nm3"],
    ["L", "kg"],
    ["kWh", "L"],
    ["L", "liter"],
  ])("환산할 수 없는 조합 %s → %s 는 명시적 실패다", (from, to) => {
    expect(convertUnit(one, from, to, tables.unitConversions, "test")).toEqual({
      ok: false,
      failure: { kind: "unit_conversion_not_found", from, to, context: "test" },
    });
  });

  it("음수 환산 계수는 invalid_reference_value 다", () => {
    const table = [{ from: "a", to: "b", factor: "-2", source: { kind: "synthetic", note: "t" } as const }];
    const result = convertUnit(one, "a", "b", table, "test");
    expect(result.ok === false && result.failure.kind).toBe("invalid_reference_value");
  });

  it("같은 쌍에 계수가 둘이면 고르지 않고 실패한다", () => {
    const source = { kind: "synthetic", note: "t" } as const;
    const table = [
      { from: "a", to: "b", factor: "2", source },
      { from: "a", to: "b", factor: "3", source },
    ];
    const result = convertUnit(one, "a", "b", table, "test");
    expect(result.ok === false && result.failure.kind).toBe("unit_conversion_ambiguous");
  });
});

describe("연료 연소", () => {
  it("에너지량, 기체별 배출량, 합계와 근거를 싣는다", () => {
    const result = ok(calculateRow(row(), tables, { gwpSetId: ETS_GWP }));
    expect(result.scope).toBe(1);
    expect(result.energy).toEqual({ value: "35200", unit: "MJ" });
    expect(result.gases).toEqual([
      { gas: "CO2", mass: "2.5508736", massUnit: "t", gwp: "1", co2e: "2.5508736" },
      { gas: "CH4", mass: "0.0001056", massUnit: "t", gwp: "21", co2e: "0.0022176" },
      { gas: "N2O", mass: "0.00002112", massUnit: "t", gwp: "310", co2e: "0.0065472" },
    ]);
    expect(result.co2e).toBe("2.5596384");
    expect(result.unit).toBe("tCO2eq");

    const { evidence } = result;
    expect(evidence.factor.id).toBe(DIESEL_ID);
    expect(evidence.factor.version).toBe("환경부고시 제2025-64호");
    expect(evidence.factor.source.kind).toBe("cited");
    expect(evidence.gwpSet.source.kind).toBe("cited");
    expect(evidence.calorificBasis).toBe("net");
    expect(evidence.oxidationFactor).toBe("0.99");
    expect(evidence.sector).toBe(MANUFACTURING);
    expect(evidence.parameterTiers).toEqual({ calorific: 2, co2: 2, nonCo2: 1, oxidation: 2 });
    expect(evidence.facilityUse).toBeNull();
    expect(evidence.steps.map((s) => s.label)).toContain("에너지량");
    expect(evidence.steps.at(-1)).toEqual({
      label: "합산",
      formula: "2.5508736 (CO2) + 0.0022176 (CH4) + 0.0065472 (N2O)",
      result: "2.5596384",
      unit: "tCO2eq",
    });
    // 환산 단계에는 쓴 환산 계수와 그 출처가 붙는다.
    const conversions = evidence.steps.filter((s) => s.conversion !== undefined);
    expect(conversions.length).toBeGreaterThan(0);
    for (const step of conversions) expect(step.conversion?.source.kind).toBe("definition");
  });

  it("산화계수는 CO2 에만 곱한다", () => {
    const withOx = ok(calculateRow(row(), tables, { gwpSetId: ETS_GWP }));
    const noOxTables: ReferenceTables = {
      ...tables,
      factors: tables.factors.map((f) => {
        if (f.id !== DIESEL_ID || f.kind !== "combustion") return f;
        const { oxidationFactor: _dropped, ...rest } = f;
        return rest;
      }),
    };
    const withoutOx = ok(calculateRow(row(), noOxTables, { gwpSetId: ETS_GWP }));
    expect(withoutOx.gases[0]?.mass).toBe("2.57664");
    expect(withOx.gases[0]?.mass).toBe("2.5508736");
    expect(withoutOx.gases[1]).toEqual(withOx.gases[1]);
    expect(withoutOx.gases[2]).toEqual(withOx.gases[2]);
    expect(withoutOx.evidence.oxidationFactor).toBeNull();
  });

  it("같은 연료라도 고정연소와 이동연소는 다른 계수를 쓴다", () => {
    const base = { fuel: "synthetic-fuel-a", amount: "800", unit: "L" } as const;
    const stationary = ok(calculateRow(bare({ ...base, sourceType: "stationary_combustion" }), tables, { gwpSetId: SYNTHETIC_GWP }));
    const mobile = ok(calculateRow(bare({ ...base, sourceType: "mobile_combustion" }), tables, { gwpSetId: SYNTHETIC_GWP }));
    expect(stationary.evidence.factor.id).toBe("synthetic:fuel-a:stationary");
    expect(mobile.evidence.factor.id).toBe("synthetic:fuel-a:mobile");
    expect(mobile.co2e).not.toBe(stationary.co2e);
    expect(stationary.scope).toBe(1);
    expect(mobile.scope).toBe(1);
  });

  it("발열량 종류를 요구하면 다른 종류의 계수는 실패한다", () => {
    // 경유 총발열량 37.8 MJ/L: 배출권거래제 배출량 보고 및 인증 지침(환경부고시 제2025-64호) 별표 12,
    // https://www.law.go.kr/LSW/flDownload.do?flSeq=151024615&bylClsCd=200201 (2026-10-01 확인). 배출량 산정에는 순발열량을 쓴다(같은 표 비고 2).
    const diesel = dieselFactor(tables);
    const grossTables: ReferenceTables = {
      ...tables,
      factors: [{ ...diesel, id: "diesel-gross", variant: "gross", calorific: { ...diesel.calorific, value: "37.8", basis: "gross" } }],
    };
    const grossRow = row({ variant: "gross" });
    expect(failureOf(calculateRow(grossRow, grossTables, { gwpSetId: ETS_GWP, requireCalorificBasis: "net" }))).toEqual({
      kind: "calorific_basis_mismatch",
      factorId: "diesel-gross",
      required: "net",
      actual: "gross",
    });
    expect(ok(calculateRow(grossRow, grossTables, { gwpSetId: ETS_GWP })).evidence.calorificBasis).toBe("gross");
    expect(ok(calculateRow(row(), tables, { gwpSetId: ETS_GWP, requireCalorificBasis: "net" })).co2e).toBe("2.5596384");
  });
});

describe("구매 전력·열", () => {
  it("전력은 사용량 × 기체별 계수이고 Scope 2 다", () => {
    const result = ok(
      calculateRow(bare({ sourceType: "purchased_electricity", amount: "1", unit: "MWh" }), {
        ...tables,
        factors: tables.factors.filter((f) => f.id.startsWith("kr-ets-sample:")),
      }, { gwpSetId: ETS_GWP }),
    );
    expect(result.scope).toBe(2);
    expect(result.energy).toBeNull();
    // 0.4567 + 0.0036×21/1000 + 0.0085×310/1000
    expect(result.co2e).toBe("0.4594106");
  });

  it("이미 환산된 CO2e 계수에는 GWP 를 곱하지 않는다", () => {
    const heat = bare({ sourceType: "purchased_heat", amount: "15.5", unit: "GJ" });
    const a = ok(calculateRow(heat, tables, { gwpSetId: SYNTHETIC_GWP }));
    const b = ok(calculateRow(heat, tables, { gwpSetId: AR5_GWP }));
    expect(a.gases).toEqual([{ gas: "CO2e", mass: "0.88023725", massUnit: "t", gwp: null, co2e: "0.88023725" }]);
    expect(b.co2e).toBe(a.co2e);
  });
});

describe("GWP 세트 전환", () => {
  it("세트를 바꾸면 CH4·N2O 환산값만 달라진다", () => {
    const ets = ok(calculateRow(row(), tables, { gwpSetId: ETS_GWP }));
    const ar5 = ok(calculateRow(row(), tables, { gwpSetId: AR5_GWP }));
    expect(ets.gases.map((g) => g.mass)).toEqual(ar5.gases.map((g) => g.mass));
    expect(ets.gases.map((g) => g.gwp)).toEqual(["1", "21", "310"]);
    expect(ar5.gases.map((g) => g.gwp)).toEqual(["1", "28", "265"]);
    expect(ets.co2e).toBe("2.5596384");
    expect(ar5.co2e).toBe("2.5594272");
    expect(ar5.evidence.gwpSet.id).toBe(AR5_GWP);
  });
});

describe("실패 갈래", () => {
  const opts = { gwpSetId: ETS_GWP };

  it("계수를 찾지 못한 행은 0 이 아니라 factor_not_found 다", () => {
    const result = calculateRow(row({ fuel: "no-such-fuel" }), tables, opts);
    expect(result.status).toBe("failed");
    expect(result).not.toHaveProperty("co2e");
    expect(failureOf(result)).toEqual({
      kind: "factor_not_found",
      criteria: {
        sourceType: "stationary_combustion",
        fuel: "no-such-fuel",
        sector: MANUFACTURING,
        parameterTiers: "calorific=2,co2=2,nonCo2=1,oxidation=2",
      },
    });
  });

  it("부문, 매개변수별 산정등급, 시설의 쓰임, 그 밖의 구분, 연도가 다르면 비슷한 계수로 대신하지 않는다", () => {
    expect(failureOf(calculateRow(row({ sector: COMMERCIAL }), tables, opts)).kind).toBe("factor_not_found");
    // A 그룹 최소 조합(발열량만 Tier 2)을 달라고 하면 Tier 2 조합의 계수로 대신하지 않는다.
    expect(failureOf(calculateRow(row({ parameterTiers: { ...TIER2, co2: 1, oxidation: 1 } }), tables, opts))).toEqual({
      kind: "factor_not_found",
      criteria: {
        sourceType: "stationary_combustion",
        fuel: "diesel",
        sector: MANUFACTURING,
        parameterTiers: "calorific=2,co2=1,nonCo2=1,oxidation=1",
      },
    });
    expect(failureOf(calculateRow(row({ parameterTiers: { ...TIER2, oxidation: null } }), tables, opts)).kind).toBe("factor_not_found");
    expect(failureOf(calculateRow(row({ facilityUse: "other" }), tables, opts)).kind).toBe("factor_not_found");
    expect(failureOf(calculateRow(row({ variant: "synthetic-구분" }), tables, opts)).kind).toBe("factor_not_found");
    expect(failureOf(calculateRow(row({ factorYear: 2025 }), tables, opts)).kind).toBe("factor_not_found");
    expect(failureOf(calculateRow(row({ sourceType: "mobile_combustion" }), tables, opts)).kind).toBe("factor_not_found");
  });

  it("factorId 가 없는 id 를 가리키면 factor_not_found 다", () => {
    expect(failureOf(calculateRow(row({ factorId: "nope" }), tables, opts))).toEqual({
      kind: "factor_not_found",
      criteria: { factorId: "nope" },
    });
  });

  it("조건에 맞는 계수가 둘이면 고르지 않는다", () => {
    const diesel = dieselFactor(tables);
    const doubled = { ...tables, factors: [...tables.factors, { ...diesel, id: "dup" }] };
    expect(failureOf(calculateRow(row(), doubled, opts))).toMatchObject({ kind: "factor_ambiguous", candidateIds: [DIESEL_ID, "dup"] });
  });

  it("연소 행이 전력 계수를 가리키면 factor_kind_mismatch 다", () => {
    expect(failureOf(calculateRow(row({ factorId: "kr-ets-sample:electricity:2014-2016-avg" }), tables, opts)).kind).toBe("factor_kind_mismatch");
  });

  it("기체 하나가 빠진 계수는 그 기체를 0 으로 치지 않고 실패한다", () => {
    const diesel = dieselFactor(tables);
    const partial = { ...tables, factors: [{ ...diesel, emissionFactors: diesel.emissionFactors.filter((e) => e.gas !== "CH4") }] };
    expect(failureOf(calculateRow(row(), partial, opts))).toEqual({
      kind: "factor_incomplete",
      factorId: DIESEL_ID,
      reason: "CH4 배출계수가 없다",
    });
  });

  it("전력 계수가 CO2 만 있으면 실패한다", () => {
    const partial: ReferenceTables = {
      ...tables,
      factors: [
        {
          kind: "purchased_energy",
          id: "co2-only",
          version: "synthetic-1",
          source: { kind: "synthetic", note: "t" },
          energyType: "electricity",
          perUnit: "MWh",
          emissionFactors: [{ gas: "CO2", value: "0.5", massUnit: "t" }],
        },
      ],
    };
    const result = calculateRow(bare({ sourceType: "purchased_electricity", amount: "1000", unit: "MWh" }), partial, opts);
    expect(failureOf(result).kind).toBe("factor_incomplete");
  });

  it("환산할 수 없는 단위는 unit_conversion_not_found 다", () => {
    expect(failureOf(calculateRow(row({ unit: "kg" }), tables, opts))).toEqual({
      kind: "unit_conversion_not_found",
      from: "kg",
      to: "L",
      context: "사용량 → 발열량 기준 단위",
    });
    expect(failureOf(calculateRow(row({ fuel: "city-gas-lng", sector: COMMERCIAL, unit: "m3" }), tables, opts))).toMatchObject({
      kind: "unit_conversion_not_found",
      from: "m3",
      to: "Nm3",
    });
  });

  it("없는 GWP 세트는 gwp_set_not_found 다 (기본 세트로 대신하지 않는다)", () => {
    expect(failureOf(calculateRow(row(), tables, { gwpSetId: "AR4" }))).toEqual({ kind: "gwp_set_not_found", gwpSetId: "AR4" });
  });

  it("세트에 기체 값이 빠져 있으면 gwp_value_missing 이다", () => {
    const partial: ReferenceTables = {
      ...tables,
      gwpSets: [{ id: "partial", label: "p", source: { kind: "synthetic", note: "t" }, values: { CO2: "1", CH4: "20" } }],
    };
    expect(failureOf(calculateRow(row(), partial, { gwpSetId: "partial" }))).toEqual({
      kind: "gwp_value_missing",
      gwpSetId: "partial",
      gas: "N2O",
    });
  });

  it.each([
    ["abc", "십진 문자열이 아니다"],
    ["1e3", "십진 문자열이 아니다"],
    ["", "십진 문자열이 아니다"],
    ["-1", "음수 사용량"],
  ])("사용량 %j 는 invalid_activity 다", (amount, reason) => {
    expect(failureOf(calculateRow(row({ amount }), tables, opts))).toMatchObject({ kind: "invalid_activity", field: "amount", reason });
  });

  it("숫자 타입 사용량도 거부한다 (문자열만 받는다)", () => {
    expect(failureOf(calculateRow(row({ amount: 1000 as unknown as string }), tables, opts)).kind).toBe("invalid_activity");
  });

  it("표의 값이 십진 문자열이 아니면 invalid_reference_value 다", () => {
    const diesel = dieselFactor(tables);
    const broken = { ...tables, factors: [{ ...diesel, calorific: { ...diesel.calorific, value: "35,2" } }] };
    expect(failureOf(calculateRow(row(), broken, opts))).toEqual({
      kind: "invalid_reference_value",
      where: `factors[${DIESEL_ID}].calorific.value`,
      value: "35,2",
    });
  });

  it.each([["scope3_travel"], [undefined], [""], ["toString"]])("알 수 없는 배출원 유형 %j 는 다른 유형으로 계산하지 않는다", (sourceType) => {
    const result = calculateRow(bare({ sourceType: sourceType as never, amount: "15.5", unit: "GJ" }), tables, { gwpSetId: SYNTHETIC_GWP });
    expect(result).toEqual({
      rowId: "t1",
      orgUnit: "synthetic-site",
      period: "2025",
      scope: null,
      sourceType: null,
      status: "failed",
      failure: { kind: "invalid_activity", field: "sourceType", value: String(sourceType ?? ""), reason: "알 수 없는 배출원 유형이거나 행이 객체가 아니다" },
    });
  });

  it("배열로 적힌 배출원 유형은 문자열로 바꿔 받아들이지 않는다", () => {
    const result = calculateRow(bare({ sourceType: ["purchased_electricity"] as never, amount: "1", unit: "TJ" }), tables, { gwpSetId: SYNTHETIC_GWP });
    expect(failureOf(result)).toMatchObject({ kind: "invalid_activity", field: "sourceType" });
  });

  it.each([[null], ["row"], [[]], [42]])("객체가 아닌 행 %j 는 던지지 않고 invalid_activity 다", (input) => {
    expect(failureOf(calculateRow(input as never, tables, opts))).toMatchObject({ kind: "invalid_activity", field: "sourceType" });
  });

  it("표의 음수 값은 invalid_reference_value 다", () => {
    const diesel = dieselFactor(tables);
    const broken = { ...tables, factors: [{ ...diesel, calorific: { ...diesel.calorific, value: "-35.2" } }] };
    expect(failureOf(calculateRow(row(), broken, opts))).toMatchObject({ kind: "invalid_reference_value", value: "-35.2" });
  });

  it("실패한 행도 조직·기간·Scope·배출원 유형을 잃지 않는다", () => {
    const result = calculateRow(row({ id: "x9", orgUnit: "site-9", period: "2025-03", fuel: "no-such-fuel" }), tables, opts);
    expect(result).toMatchObject({ rowId: "x9", orgUnit: "site-9", period: "2025-03", scope: 1, sourceType: "stationary_combustion", status: "failed" });
  });
});
