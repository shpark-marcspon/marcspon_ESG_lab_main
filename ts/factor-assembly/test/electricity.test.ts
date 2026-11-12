import { calculateRow, validateTables, type GwpSet, type UnitConversion } from "ghg-calc";
import { describe, expect, it } from "vitest";
import { assemblePurchasedElectricityFactor, electricityComparison, electricityOptions, loadElectricityRecords } from "../src/index.js";
import type { ElectricityRequest, ElectricityResult, FactorRecord } from "../src/index.js";
import { mapping, readData } from "./helpers.js";

// 구매 전력 조립 (MARC-TICK-18). 기대하는 레코드 id 와 값 표기는 원문에서 2026-10-03 에 대조한 것이다:
// 지침 별표 6 PDF 274쪽 <표-69>, GIR 2025 공고(HWPX) 전력배출계수 표와 주석 2) 표, GIR 2024 공고(PDF) 1쪽.

const records: readonly FactorRecord[] = loadElectricityRecords();
const tables = validateTables({
  unitConversions: (readData("ghg-calc/unit-conversions.json") as { unitConversions: UnitConversion[] }).unitConversions,
  gwpSets: (readData("ghg-calc/gwp-sets.json") as { gwpSets: GwpSet[] }).gwpSets,
  factors: [],
});
if (!tables.ok) throw new Error("환산표·GWP 표에 문제가 있다");

const TABLE69: ElectricityRequest = { energyType: "electricity", source: "guideline_table69", gasForm: "per_gas" };
const gir = (over: Partial<NonNullable<ElectricityRequest["gir"]>> = {}, gasForm: ElectricityRequest["gasForm"] = "per_gas"): ElectricityRequest => ({
  energyType: "electricity",
  source: "gir_published",
  gasForm,
  gir: { edition: 2025, period: { kind: "single_year", year: 2023 }, boundary: "consumption", methodology: "ipcc_2006", ...over },
});
const assemble = (request: unknown) => assemblePurchasedElectricityFactor(request as ElectricityRequest, records, mapping);
const ok = (result: ElectricityResult) => {
  if (!result.ok) throw new Error(`조립 실패: ${JSON.stringify(result.failure)}`);
  return result.value;
};
const failure = (result: ElectricityResult) => {
  if (result.ok) throw new Error(`실패해야 하는데 조립됐다: ${result.value.factor.id}`);
  return result.failure;
};
const ids = (result: ElectricityResult) => ok(result).evidence.parameters.map((p) => p.recordId);

describe("지침 별표 6 <표-69>", () => {
  it("CO2·CH4·N2O 세 낱줄을 엔진의 구매 전력 계수로 묶고 출처 선택이 입력이었다는 것을 남긴다", () => {
    const { factor, evidence } = ok(assemble(TABLE69));
    expect(factor).toMatchObject({
      kind: "purchased_energy",
      energyType: "electricity",
      perUnit: "MWh",
      version: "환경부고시 제2025-64호 (2025-04-11 시행)",
      emissionFactors: [
        { gas: "CO2", value: "0.4567", massUnit: "t" },
        { gas: "CH4", value: "0.0036", massUnit: "kg" },
        { gas: "N2O", value: "0.0085", massUnit: "kg" },
      ],
    });
    expect(factor).not.toHaveProperty("year");
    expect(evidence.parameters.map((p) => [p.recordId, p.valueRaw, p.unit])).toEqual([
      ["kr-ets-byl06:elec:t69:CO2", "0.4567", "tCO2/MWh"],
      ["kr-ets-byl06:elec:t69:CH4", "0.0036", "kgCH4/MWh"],
      ["kr-ets-byl06:elec:t69:N2O", "0.0085", "kgN2O/MWh"],
    ]);
    expect(evidence.sourceChoice).toMatchObject({ value: "guideline_table69", basis: "input" });
    expect(evidence.rule).toContain("<표-69>");
    if (factor.source.kind !== "cited") throw new Error("unreachable");
    expect(factor.source.note).toContain("조립 조건(입력)");
    expect(validateTables({ unitConversions: [], gwpSets: [], factors: [factor] }).ok).toBe(true);
  });

  it("표-69 에는 CO2 환산 값이 없어 co2e 를 고르면 실패한다 (기체별 값을 더해 만들지 않는다)", () => {
    expect(failure(assemble({ ...TABLE69, gasForm: "co2e" }))).toMatchObject({ kind: "record_not_found", parameter: "CO2e" });
  });

  it("표-69 에 판·기간·발전단/소비단을 주면 실패한다 (표-69 에는 그 구분이 없다)", () => {
    expect(failure(assemble({ ...TABLE69, gir: { edition: 2025 } }))).toMatchObject({ kind: "invalid_input", field: "gir" });
  });
});

describe("GIR 공표 값", () => {
  it("2025 공표, 2023년 단년, 소비단, ’06 IPCC 의 기체별 값과 CO2 환산 값", () => {
    expect(ids(assemble(gir()))).toEqual(["gir-2025:main:r7", "gir-2025:main:r8", "gir-2025:main:r9"]);
    const co2e = ok(assemble(gir({}, "co2e")));
    expect(co2e.factor.emissionFactors).toEqual([{ gas: "CO2e", value: "0.4173", massUnit: "t" }]);
    expect(co2e.factor.year).toBe(2023);
    expect(co2e.evidence.parameters[0]).toMatchObject({ recordId: "gir-2025:main:r6", valueRaw: "0.4173", unit: "t CO2-eq/MWh" });
    // CO2e 계수에 이미 든 GWP 가 근거와 출처에 남는다 (’06 기준은 AR5, ’96 기준은 AR2, 2024 공고는 적혀 있지 않다)
    expect(co2e.evidence.co2eGwp).toContain("AR5");
    if (co2e.factor.source.kind !== "cited") throw new Error("unreachable");
    expect(co2e.factor.source.note).toContain("GWP 가 이미 들어 있다");
    expect(ok(assemble(gir({ methodology: "ipcc_1996_legacy" }, "co2e"))).evidence.co2eGwp).toContain("AR2");
    expect(ok(assemble(gir({ edition: 2024, period: { kind: "not_stated" } }, "co2e"))).evidence.co2eGwp).toContain("적혀 있지 않다");
    expect(ok(assemble(gir())).evidence.co2eGwp).toBeNull();
    expect(ok(assemble(TABLE69)).evidence.co2eGwp).toBeNull();
  });

  it("발전단과 3년 평균은 다른 낱줄이다", () => {
    expect(ids(assemble(gir({ boundary: "generation" })))).toEqual(["gir-2025:main:r3", "gir-2025:main:r4", "gir-2025:main:r5"]);
    expect(ids(assemble(gir({ period: { kind: "three_year_average", from: 2021, to: 2023 } }, "co2e")))).toEqual(["gir-2025:main:r14"]);
    expect(ok(assemble(gir({ period: { kind: "three_year_average", from: 2021, to: 2023 } }))).factor).not.toHaveProperty("year");
  });

  it("2025 공표의 기존 산정기준(’96 IPCC) 값은 주석 2) 표의 낱줄이다", () => {
    expect(ids(assemble(gir({ methodology: "ipcc_1996_legacy" })))).toEqual(["gir-2025:alt:r3c3", "gir-2025:alt:r3c4", "gir-2025:alt:r3c5"]);
    // 주석 표는 3년 평균의 따옴표가 본표와 다르다 (‘21-’23 / ‘21-‘23). 둘 다 같은 기간으로 읽는다.
    expect(ids(assemble(gir({ methodology: "ipcc_1996_legacy", period: { kind: "three_year_average", from: 2021, to: 2023 } }, "co2e")))).toEqual(["gir-2025:alt:r5c2"]);
  });

  it("2024 공표는 표에 기간이 없어 not_stated 로만 고른다", () => {
    expect(ids(assemble(gir({ edition: 2024, period: { kind: "not_stated" }, boundary: "generation" }, "co2e")))).toEqual(["gir-2024:main:r2"]);
    expect(failure(assemble(gir({ edition: 2024, period: { kind: "single_year", year: 2022 } })))).toMatchObject({ kind: "record_not_found", parameter: "CO2" });
    expect(failure(assemble(gir({ period: { kind: "not_stated" } })))).toMatchObject({ kind: "record_not_found" });
    // 2024 공표의 기존 산정기준 표는 적재하지 않았다 (단위가 표 안에 없다, 밴드 C)
    expect(failure(assemble(gir({ edition: 2024, period: { kind: "not_stated" }, methodology: "ipcc_1996_legacy" })))).toMatchObject({ kind: "record_not_found" });
  });

  it("없는 해의 값은 가까운 해로 대신하지 않는다", () => {
    expect(failure(assemble(gir({ period: { kind: "single_year", year: 2024 } })))).toMatchObject({ kind: "record_not_found", parameter: "CO2", criteria: { 기간: "2024" } });
  });
});

describe("입력 누락과 틀린 입력 (기본값이 없다)", () => {
  it("출처가 빠지면 표-69 로도 GIR 로도 채우지 않는다", () => {
    expect(failure(assemble({ energyType: "electricity", gasForm: "per_gas" }))).toMatchObject({ kind: "missing_input", field: "source" });
  });

  it("기체별·CO2e 구분, GIR 의 판·기간·발전단/소비단·산정기준이 하나라도 빠지면 실패한다", () => {
    expect(failure(assemble({ energyType: "electricity", source: "guideline_table69" }))).toMatchObject({ kind: "missing_input", field: "gasForm" });
    expect(failure(assemble({ energyType: "electricity", source: "gir_published", gasForm: "co2e" }))).toMatchObject({ kind: "missing_input", field: "gir" });
    for (const field of ["edition", "period", "boundary", "methodology"] as const) {
      const request = gir();
      const partial = { ...request.gir } as Record<string, unknown>;
      delete partial[field];
      expect(failure(assemble({ ...request, gir: partial }))).toMatchObject({ kind: "missing_input", field: `gir.${field}` });
    }
    expect(failure(assemble({ source: "guideline_table69", gasForm: "per_gas" }))).toMatchObject({ kind: "missing_input", field: "energyType" });
  });

  it("틀린 값", () => {
    expect(failure(assemble({ ...TABLE69, energyType: "heat" }))).toMatchObject({ kind: "invalid_input", field: "energyType" });
    expect(failure(assemble({ ...TABLE69, source: "kepco" }))).toMatchObject({ kind: "invalid_input", field: "source" });
    expect(failure(assemble(gir({ edition: 2023 })))).toMatchObject({ kind: "invalid_input", field: "gir.edition" });
    expect(failure(assemble(gir({ period: { kind: "three_year_average", from: 2022, to: 2023 } })))).toMatchObject({ kind: "invalid_input", field: "gir.period" });
    expect(failure(assemble({ ...gir(), gir: { ...gir().gir, boundary: "both" } }))).toMatchObject({ kind: "invalid_input", field: "gir.boundary" });
  });

  it("CO2e 와 기체별 값을 섞으려는 입력은 실패한다", () => {
    for (const gasForm of [["co2e", "per_gas"], "mixed", "CO2e+CH4", ""]) {
      expect(failure(assemble({ ...gir(), gasForm }))).toMatchObject({ kind: "invalid_input", field: "gasForm" });
    }
  });

  it("계수 표에 같은 조건의 낱줄이 둘이면 앞의 것을 집지 않는다", () => {
    const base = records.find((r) => r.id === "gir-2025:main:r6") as FactorRecord;
    const doubled = [...records, { ...base, id: "gir-2025:synthetic-dup", synthetic: true }];
    expect(assemblePurchasedElectricityFactor(gir({}, "co2e"), doubled, mapping)).toMatchObject({ ok: false, failure: { kind: "record_ambiguous" } });
  });

  it("기간 표기를 읽을 수 없는 GIR 낱줄이 있으면 고르기 전에 실패한다", () => {
    const base = records.find((r) => r.id === "gir-2025:main:r6") as FactorRecord;
    const odd = [...records, { ...base, id: "gir-2025:synthetic-odd", conditions: { ...base.conditions, 기간: "2023 상반기" }, synthetic: true }];
    expect(assemblePurchasedElectricityFactor(gir({}, "co2e"), odd, mapping)).toMatchObject({ ok: false, failure: { kind: "record_condition_unknown", field: "기간" } });
  });
});

describe("엔진으로 계산", () => {
  const gwp = (id: string) => ({ gwpSetId: id });
  const row = (factorId: string) => ({ id: "e", orgUnit: "synthetic-site", period: "2025", sourceType: "purchased_electricity" as const, factorId, amount: "1000", unit: "MWh" });

  it("CO2e 계수에는 GWP 를 곱하지 않는다: GWP 세트를 바꿔도 결과가 같다", () => {
    const { factor } = ok(assemble(gir({}, "co2e")));
    const t = { ...tables.tables, factors: [factor] };
    const decree = calculateRow(row(factor.id), t, gwp("kr-ets-decree-annex2"));
    const ar5 = calculateRow(row(factor.id), t, gwp("ipcc-ar5-100y-kr-nid-2025"));
    expect(decree).toMatchObject({ status: "ok", co2e: "417.3", gases: [{ gas: "CO2e", mass: "417.3", gwp: null, co2e: "417.3" }] });
    expect(ar5).toMatchObject({ status: "ok", co2e: "417.3" });
  });

  it("기체별 계수에는 GWP 세트의 값을 곱한다", () => {
    const { factor } = ok(assemble(TABLE69));
    const result = calculateRow(row(factor.id), { ...tables.tables, factors: [factor] }, gwp("kr-ets-decree-annex2"));
    // 1000 MWh × (0.4567 tCO2 + 0.0036 kgCH4 × 21 / 1000 + 0.0085 kgN2O × 310 / 1000)
    expect(result).toMatchObject({ status: "ok", co2e: "459.4106" });
  });
});

describe("비교 표 (기대값이 아니다)", () => {
  it("선택지 21개가 다 조립·계산되고, 표-69 줄은 검산 H06 의 기대값과 같다", () => {
    const rows = electricityComparison("1234.567", "MWh", "kr-ets-decree-annex2", records, mapping, tables.tables);
    expect(electricityOptions().length).toBe(21);
    expect(rows.every((r) => r.co2e !== null && r.failure === null)).toBe(true);
    expect(rows[0]).toMatchObject({ factorId: "electricity:kr-ets-byl06:표-69:2014-2016평균:per_gas", co2e: "567.1731662102" });
    expect(new Set(rows.flatMap((r) => r.recordIds)).size).toBe(3 + 32 + 8);
  });
});
