import { describe, expect, it } from "vitest";
import { assembleCombustionFactor } from "../src/index.js";
import type { ParameterEvidence } from "../src/index.js";
import { GROUP_A, TIER1, TIER2, byId, failureOf, mapping, records, request, synthetic, valueOf } from "./helpers.js";

// 조립: 실제 계수 표(data/factors/factors.jsonl)와 대응표에서 낱줄을 골라 묶는다.
// 기대하는 레코드 id 와 값 표기는 지침 원문(환경부고시 제2025-64호 별표 6·10·11·12)에서 2026-10-02 에 대조한 것이다 (band-reports/N-factor-assembly.md).

const assemble = (overrides: Parameters<typeof request>[0] = {}) => assembleCombustionFactor(request(overrides), records, mapping);
const pick = (parameters: ParameterEvidence[]) => Object.fromEntries(parameters.map((p) => [p.parameter, { tier: p.tier, recordId: p.recordId, valueRaw: p.valueRaw, value: p.value }]));

describe("산정등급 조합별 조립 (경유, 제조업·건설업)", () => {
  it("전부 Tier 2: 발열량·CO2 계수는 별표 12, CH4·N2O 는 별표 10, 산화계수는 액체연료 Tier 2", () => {
    const { factor, evidence } = valueOf(assemble());
    expect(pick(evidence.parameters)).toEqual({
      calorific: { tier: 2, recordId: "kr-ets-byl12:hv:p1r5:net", valueRaw: "35.2", value: "35.2" },
      CO2: { tier: 2, recordId: "kr-ets-byl12:ef:p3r3:CO2", valueRaw: "73,200", value: "73200" },
      CH4: { tier: 1, recordId: "kr-ets-byl10:p1t0r12:CH4:2", valueRaw: "3", value: "3" },
      N2O: { tier: 1, recordId: "kr-ets-byl10:p1t0r12:N2O:5", valueRaw: "0.6", value: "0.6" },
      oxidation: { tier: 2, recordId: "kr-ets-byl06:ox:3:t2", valueRaw: "0.99", value: "0.99" },
    });
    expect(factor).toMatchObject({
      kind: "combustion",
      id: "kr-ets:stationary:경유:manufacturing_construction:cv2_co2ef2_ch4n2o1_ox2",
      fuel: "경유",
      combustionType: "stationary",
      sector: "manufacturing_construction",
      parameterTiers: { calorific: 2, co2: 2, nonCo2: 1, oxidation: 2 },
      calorific: { value: "35.2", energyUnit: "MJ", perUnit: "L", basis: "net" },
      emissionFactors: [
        { gas: "CO2", value: "73200", massUnit: "kg", perEnergyUnit: "TJ" },
        { gas: "CH4", value: "3", massUnit: "kg", perEnergyUnit: "TJ" },
        { gas: "N2O", value: "0.6", massUnit: "kg", perEnergyUnit: "TJ" },
      ],
      oxidationFactor: "0.99",
    });
    expect(factor).not.toHaveProperty("facilityUse");
    expect(evidence.assumptions).toEqual([]);
  });

  it("A 그룹 최소기준: 발열량만 별표 12 (Tier 2) 이고 CO2 계수는 별표 10 (Tier 1), 산화계수 1.0", () => {
    const { factor, evidence } = valueOf(assemble({ tiers: GROUP_A }));
    expect(evidence.tiers).toMatchObject({ values: { calorific: 2, co2: 1, nonCo2: 1, oxidation: 1 }, basis: "group_minimum", group: "A" });
    expect(evidence.tiers.locators.filter((l) => l.startsWith("별표 5")).length).toBe(3);
    expect(pick(evidence.parameters)).toMatchObject({
      calorific: { tier: 2, recordId: "kr-ets-byl12:hv:p1r5:net", value: "35.2" },
      CO2: { tier: 1, recordId: "kr-ets-byl10:p1t0r12:CO2:0", valueRaw: "74,100", value: "74100" },
      oxidation: { tier: 1, recordId: "kr-ets-byl06:ox:3:t1", value: "1.0" },
    });
    // CO2 계수가 국가 고유값(별표 12)에서 오면 안 된다.
    const co2 = evidence.parameters.find((p) => p.parameter === "CO2");
    expect(co2?.sourceId).toBe("kr-ets-byl10");
    expect(co2?.nameMapping).toMatchObject({ domestic: "경유", ipcc: "가스/디젤 오일", basis: "guideline" });
    expect(factor.emissionFactors[0]).toEqual({ gas: "CO2", value: "74100", massUnit: "kg", perEnergyUnit: "TJ" });
    expect(factor.oxidationFactor).toBe("1.0");
    expect(factor.id).toBe("kr-ets:stationary:경유:manufacturing_construction:cv2_co2ef1_ch4n2o1_ox1");
  });

  it("B 그룹 최소기준은 전부 Tier 2 조합과 같은 계수다", () => {
    const b = valueOf(assemble({ tiers: { kind: "group_minimum", group: "B" } }));
    const explicit = valueOf(assemble());
    expect(b.factor.id).toBe(explicit.factor.id);
    expect(b.factor.emissionFactors).toEqual(explicit.factor.emissionFactors);
    expect(b.evidence.tiers.basis).toBe("group_minimum");
  });

  it("전부 Tier 1: 발열량은 별표 11 (질량 기준), CO2 계수는 별표 10", () => {
    const { factor, evidence } = valueOf(assemble({ tiers: TIER1 }));
    expect(pick(evidence.parameters)).toMatchObject({
      calorific: { tier: 1, recordId: "kr-ets-byl11:p1t0r11", valueRaw: "43.0" },
      CO2: { tier: 1, recordId: "kr-ets-byl10:p1t0r12:CO2:0", value: "74100" },
      oxidation: { tier: 1, recordId: "kr-ets-byl06:ox:3:t1" },
    });
    // 별표 11 의 발열량은 TJ/Gg 다. 리터로 잰 사용량에 쓰려면 밀도가 있어야 하고, 조합기는 그것을 지어내지 않는다.
    expect(factor.calorific).toEqual({ value: "43.0", energyUnit: "TJ", perUnit: "Gg", basis: "net" });
    expect(evidence.parameters[0]?.nameMapping).toMatchObject({ domestic: "경유", ipcc: "가스/디젤 오일", basis: "guideline" });
  });

  it("조립된 계수는 낱줄마다 출처(레코드 id, 위치, 파일 해시, 확인 일자)와 규칙을 싣는다", () => {
    const { factor, evidence } = valueOf(assemble());
    expect(evidence.parameters.map((p) => p.parameter)).toEqual(["calorific", "CO2", "CH4", "N2O", "oxidation"]);
    for (const p of evidence.parameters) {
      const record = byId(p.recordId);
      expect(p.locator).toBe(record.source.locator);
      expect(p.fileSha256).toBe(record.source.file_sha256);
      expect(p.url).toMatch(/^https:\/\/www\.law\.go\.kr\//);
      expect(p.retrieved).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      expect(p.rule.length).toBeGreaterThan(0);
    }
    expect(evidence.parameters[1]?.conditions).toEqual({ "병합 셀": "원문 표에서 등유 행과 한 칸을 같이 쓴다" });
    expect(factor.source).toMatchObject({ kind: "cited", url: mapping.guideline.url, checkedOn: "2026-10-02" });
    if (factor.source.kind !== "cited") throw new Error("unreachable");
    for (const p of evidence.parameters) expect(factor.source.locator).toContain(p.locator);
    expect(factor.source.note).toContain("발열량 Tier 2, CO2 계수 Tier 2, CH4·N2O 계수 Tier 1, 산화계수 Tier 2");
    expect(factor.version).toBe(mapping.guideline.version);
    expect(evidence.fuelState).toMatchObject({ value: "액체", basis: "guideline" });
  });
});

describe("부문별 CH4·N2O 선택", () => {
  it.each([
    ["energy_industries", "kr-ets-byl10:p1t0r12:CH4:1", "3", "kr-ets-byl10:p1t0r12:N2O:5", "0.6"],
    ["manufacturing_construction", "kr-ets-byl10:p1t0r12:CH4:2", "3", "kr-ets-byl10:p1t0r12:N2O:5", "0.6"],
    ["commercial_public", "kr-ets-byl10:p1t0r12:CH4:3", "10", "kr-ets-byl10:p1t0r12:N2O:6", "0.6"],
    ["residential_other", "kr-ets-byl10:p1t0r12:CH4:4", "10", "kr-ets-byl10:p1t0r12:N2O:6", "0.6"],
  ])("경유 %s", (sector, ch4Id, ch4, n2oId, n2o) => {
    const { factor, evidence } = valueOf(assemble({ industry: { sector } }));
    expect(pick(evidence.parameters)).toMatchObject({ CH4: { recordId: ch4Id, value: ch4 }, N2O: { recordId: n2oId, value: n2o } });
    expect(factor.sector).toBe(sector);
    expect(byId(ch4Id).conditions["부문"]).toBe(mapping.sectors.find((s) => s.key === sector)?.ch4_condition);
  });

  it("도시가스(LNG): 부문에 따라 CH4 가 1 과 5 로 갈린다", () => {
    const ch4 = (sector: string) => valueOf(assemble({ fuel: "도시가스(LNG)", industry: { sector } })).factor.emissionFactors[1]?.value;
    expect([ch4("energy_industries"), ch4("manufacturing_construction"), ch4("commercial_public"), ch4("residential_other")]).toEqual(["1", "1", "5", "5"]);
  });

  it("업종 코드로 부문을 정한다", () => {
    expect(valueOf(assemble({ industry: { ksicCode: "35" } })).evidence.sector).toMatchObject({ key: "energy_industries", basis: "ksic_division", ksicCode: "35" });
    expect(valueOf(assemble({ industry: { ksicCode: "2011" } })).evidence.sector).toMatchObject({ key: "manufacturing_construction", basis: "ksic_division" });
    expect(valueOf(assemble({ industry: { ksicCode: "47" } })).evidence.sector.key).toBe("commercial_public");
    expect(valueOf(assemble({ industry: { ksicCode: "01" } })).evidence.sector.key).toBe("residential_other");
  });

  it("두 부문에 겹치는 업종 코드(38, 58, 59)는 고르지 않고 실패한다", () => {
    for (const ksicCode of ["38", "58", "59", "381", "5811", "591"]) {
      expect(failureOf(assemble({ industry: { ksicCode } }))).toMatchObject({
        kind: "sector_ambiguous",
        ksicCode,
        division: ksicCode.slice(0, 2),
        candidates: ["manufacturing_construction", "commercial_public"],
      });
    }
    const failure = failureOf(assemble({ industry: { ksicCode: "38" } }));
    if (failure.kind !== "sector_ambiguous") throw new Error("unreachable");
    expect(failure.notes).toEqual(["(383)은제조업ㆍ건설업계수를적용한다"]);
  });

  it("원문 주석이 더 자세한 코드로 정해 준 경우에만 그 부문을 쓴다", () => {
    expect(valueOf(assemble({ industry: { ksicCode: "383" } })).evidence.sector).toMatchObject({ key: "manufacturing_construction", basis: "ksic_exception" });
    expect(valueOf(assemble({ industry: { ksicCode: "38310" } })).evidence.sector.key).toBe("manufacturing_construction");
    expect(valueOf(assemble({ industry: { ksicCode: "5821" } })).evidence.sector).toMatchObject({ key: "commercial_public", basis: "ksic_exception" });
    expect(valueOf(assemble({ industry: { ksicCode: "5912" } })).evidence.sector.key).toBe("commercial_public");
  });

  it("겹치는 코드라도 부문을 직접 주면 조립한다", () => {
    expect(valueOf(assemble({ industry: { sector: "commercial_public" } })).evidence.sector).toMatchObject({ key: "commercial_public", basis: "input", ksicCode: null });
  });

  it("지침의 부문 정의에 없는 업종 코드는 실패한다", () => {
    for (const ksicCode of ["09", "34", "48", "00"]) {
      expect(failureOf(assemble({ industry: { ksicCode } }))).toEqual({ kind: "sector_not_defined", ksicCode, division: ksicCode.slice(0, 2) });
    }
  });

  it("심은 파손 대비: 부문을 무시하고 첫 CH4 낱줄을 집으면 상업·공공의 값이 틀린다", () => {
    const { evidence } = valueOf(assemble({ industry: { sector: "commercial_public" } }));
    const first = records.find((r) => r.source.id === "kr-ets-byl10" && r.gas === "CH4" && r.name.original === "가스/디젤 오일");
    expect(first?.id).toBe("kr-ets-byl10:p1t0r12:CH4:1");
    expect(evidence.parameters.find((p) => p.parameter === "CH4")?.recordId).not.toBe(first?.id);
  });
});

describe("산화계수: 연료 상태와 발전 여부", () => {
  const coal = { fuel: "아역청탄", fuelState: "고체" } as const;

  it("액체 Tier 2 는 0.99, 기체 Tier 2 는 0.995, Tier 1 은 상태와 무관하게 1.0", () => {
    expect(valueOf(assemble()).factor.oxidationFactor).toBe("0.99");
    expect(valueOf(assemble({ fuel: "도시가스(LNG)" })).factor.oxidationFactor).toBe("0.995");
    expect(valueOf(assemble({ tiers: GROUP_A })).factor.oxidationFactor).toBe("1.0");
    expect(valueOf(assemble({ fuel: "도시가스(LNG)", tiers: GROUP_A })).evidence.parameters.at(-1)).toMatchObject({ recordId: "kr-ets-byl06:ox:2:t1", value: "1.0" });
  });

  it("고체연료 Tier 2 는 발전 0.99, 그 밖 0.98 이고 계수에 시설의 쓰임이 실린다", () => {
    const power = valueOf(assemble({ ...coal, powerGeneration: true }));
    const other = valueOf(assemble({ ...coal, powerGeneration: false }));
    expect(power.evidence.parameters.at(-1)).toMatchObject({ recordId: "kr-ets-byl06:ox:1:t2:발전부문", value: "0.99" });
    expect(other.evidence.parameters.at(-1)).toMatchObject({ recordId: "kr-ets-byl06:ox:1:t2:기타부문", value: "0.98" });
    expect(power.factor.facilityUse).toBe("power_generation");
    expect(other.factor.facilityUse).toBe("other");
    expect(power.factor.id).not.toBe(other.factor.id);
    expect(power.evidence.fuelState).toEqual({ value: "고체", basis: "input", locator: null });
    expect(power.factor.calorific).toEqual({ value: "19.9", energyUnit: "MJ", perUnit: "kg", basis: "net" });
  });

  it("고체연료 Tier 2 에 발전 여부가 없으면 어느 쪽인지 고르지 않는다", () => {
    expect(failureOf(assemble(coal))).toMatchObject({ kind: "missing_input", field: "powerGeneration" });
  });

  it("고체연료라도 Tier 1 이면 발전 여부가 필요 없고 계수에 싣지 않는다", () => {
    const { factor } = valueOf(assemble({ ...coal, tiers: GROUP_A }));
    expect(factor.oxidationFactor).toBe("1.0");
    expect(factor).not.toHaveProperty("facilityUse");
    const given = valueOf(assemble({ powerGeneration: true }));
    expect(given.factor).not.toHaveProperty("facilityUse");
    expect(given.factor.oxidationFactor).toBe("0.99");
  });

  it("대응표에 상태가 없는 연료는 상태를 직접 받아야 한다 (석탄이니 고체겠지 하고 넘기지 않는다)", () => {
    // MARC-TICK-18 부터 아역청탄은 대응표에 추정 상태가 있다. 그 항목을 지운 대응표로 「없을 때」를 본다.
    const withoutState = { ...mapping, fuel_states: mapping.fuel_states.filter((e) => e.fuel !== "아역청탄") };
    const result = assembleCombustionFactor(request({ fuel: "아역청탄", powerGeneration: true }), records, withoutState);
    expect(failureOf(result)).toMatchObject({ kind: "fuel_state_unknown", fuel: "아역청탄" });
  });
});

describe("추정 대응을 탔을 때의 표시", () => {
  it("도시가스(LNG): 연료 상태와 이름 대응 두 곳이 추정이고 근거와 계수의 출처에 남는다", () => {
    const { factor, evidence } = valueOf(assemble({ fuel: "도시가스(LNG)", industry: { sector: "commercial_public" } }));
    expect(evidence.assumptions.map((a) => [a.kind, a.detail])).toEqual([
      ["fuel_state", "도시가스(LNG) → 기체"],
      ["fuel_name", "도시가스(LNG) → 천연가스 (kr-ets-byl10)"],
    ]);
    for (const a of evidence.assumptions) expect(a.note.length).toBeGreaterThan(10);
    expect(evidence.fuelState.basis).toBe("assumed");
    const ch4 = evidence.parameters.find((p) => p.parameter === "CH4");
    expect(ch4?.nameMapping).toMatchObject({ domestic: "도시가스(LNG)", ipcc: "천연가스", basis: "assumed" });
    expect(ch4).toMatchObject({ recordId: "kr-ets-byl10:p2t0r18:CH4:3", value: "5" });
    // 발열량과 CO2 계수는 별표 12 에 그 이름 그대로 있어 대응표를 타지 않는다.
    expect(evidence.parameters[0]).toMatchObject({ recordId: "kr-ets-byl12:hv:p1r20:net", nameMapping: null });
    expect(evidence.parameters[1]).toMatchObject({ recordId: "kr-ets-byl12:ef:p3r18:CO2", value: "56100", nameMapping: null });
    if (factor.source.kind !== "cited") throw new Error("unreachable");
    expect(factor.source.note).toContain("추정 대응 2건을 탔다");
    expect(factor.source.note).toContain("도시가스(LNG) → 천연가스 (kr-ets-byl10)");
    expect(factor.calorific).toEqual({ value: "38.9", energyUnit: "MJ", perUnit: "Nm3", basis: "net" });
  });

  it("전부 Tier 1 이면 발열량 표(별표 11)의 추정 대응도 따로 남는다", () => {
    const { evidence } = valueOf(assemble({ fuel: "도시가스(LNG)", tiers: TIER1 }));
    expect(evidence.assumptions.map((a) => a.detail)).toEqual([
      "도시가스(LNG) → 기체",
      "도시가스(LNG) → 천연가스 (kr-ets-byl11)",
      "도시가스(LNG) → 천연가스 (kr-ets-byl10)",
    ]);
  });

  it("지침의 대응만 탄 조립에는 추정 표시가 없다", () => {
    const { factor, evidence } = valueOf(assemble({ tiers: TIER1 }));
    expect(evidence.assumptions).toEqual([]);
    if (factor.source.kind !== "cited") throw new Error("unreachable");
    expect(factor.source.note).not.toContain("추정");
  });

  it("입력한 연료 상태가 대응표와 다르면 조립하지 않는다 (산화계수가 조용히 바뀌지 않는다)", () => {
    expect(failureOf(assemble({ fuelState: "기체" }))).toMatchObject({ kind: "fuel_state_conflict", fuel: "경유", given: "기체", mapped: "액체", basis: "guideline" });
    expect(failureOf(assemble({ fuelState: "고체", powerGeneration: false }))).toMatchObject({ kind: "fuel_state_conflict" });
    expect(failureOf(assemble({ fuel: "도시가스(LNG)", fuelState: "액체" }))).toMatchObject({ kind: "fuel_state_conflict", mapped: "기체", basis: "assumed" });
    expect(valueOf(assemble({ fuelState: "액체" })).evidence.fuelState).toEqual({ value: "액체", basis: "input", locator: null });
  });

  it("연료 상태를 직접 주면 대응표의 추정을 타지 않는다", () => {
    const { evidence } = valueOf(assemble({ fuel: "도시가스(LNG)", fuelState: "기체" }));
    expect(evidence.fuelState.basis).toBe("input");
    expect(evidence.assumptions.map((a) => a.kind)).toEqual(["fuel_name"]);
  });
});

describe("낱줄 없음과 중복", () => {
  it("대응표에 없는 연료 이름은 비슷한 연료로 대신하지 않는다", () => {
    // 별표 12 에는 「도시가스(LPG)」 가 있지만 별표 10 의 국내에너지원 기준에는 없다.
    expect(failureOf(assemble({ fuel: "도시가스(LPG)", fuelState: "기체" }))).toEqual({
      kind: "fuel_name_not_mapped",
      fuel: "도시가스(LPG)",
      table: "kr-ets-byl10",
      parameter: "CH4",
    });
    // 「LPG」 는 별표 10·11 에 대응이 있지만 별표 12 의 이름은 「프로판(LPG1호)」 다.
    expect(failureOf(assemble({ fuel: "LPG", fuelState: "기체" }))).toMatchObject({ kind: "record_not_found", parameter: "calorific", criteria: { name: "LPG", source: "kr-ets-byl12" } });
    expect(failureOf(assemble({ fuel: "synthetic-없는 연료", fuelState: "액체" }))).toMatchObject({ kind: "record_not_found", parameter: "calorific" });
  });

  it("줄바꿈 칸의 이름은 건별 판단대로 갈린다: 무연탄 칸은 두 이름, 가스 코크스 칸은 꺾인 한 이름", () => {
    const names = (table: string, ipcc: string) => mapping.fuel_names.find((e) => e.table === table && e.ipcc_name === ipcc && e.basis === "guideline")?.domestic_names;
    expect(names("kr-ets-byl10", "무연탄")).toEqual(["국내 무연탄", "수입 무연탄"]);
    expect(names("kr-ets-byl11", "무연탄")).toEqual(["국내 무연탄", "수입 무연탄"]);
    expect(names("kr-ets-byl10", "가스 코크스")).toEqual(["가스공장 코크스"]);
    // 칸의 글자 그대로(띄어쓰기 있음)는 지침의 대응이다. 별표 12 의 「국내무연탄」(띄어쓰기 없음)은 따로 추정 대응을 탄다.
    const direct = valueOf(assemble({ fuel: "국내 무연탄", fuelState: "고체", tiers: TIER1 }));
    expect(direct.evidence.parameters.find((p) => p.parameter === "CO2")?.nameMapping).toMatchObject({ ipcc: "무연탄", basis: "guideline" });
    expect(direct.evidence.assumptions).toEqual([]);
    // 「가스공장」 이나 「코크스」 만으로는 가스 코크스가 되지 않는다 (두 이름으로 가르지 않았다).
    expect(mapping.fuel_names.filter((e) => e.table === "kr-ets-byl10" && e.domestic_names.includes("가스공장"))).toEqual([]);
    expect(mapping.fuel_names.filter((e) => e.table === "kr-ets-byl10" && e.domestic_names.includes("코크스")).map((e) => e.ipcc_name)).toEqual(["코크스로 코크스"]);
  });

  it("낱줄이 빠지면 record_not_found 이고 무엇을 찾았는지가 실린다", () => {
    const without = records.filter((r) => r.id !== "kr-ets-byl10:p1t0r12:N2O:5");
    expect(failureOf(assembleCombustionFactor(request(), without, mapping))).toEqual({
      kind: "record_not_found",
      parameter: "N2O",
      criteria: { source: "kr-ets-byl10", record_type: "emission_factor", gas: "N2O", gas_basis: "gas_mass", name: "가스/디젤 오일", 부문: "에너지산업제조업건설업" },
    });
    const noOxidation = records.filter((r) => r.record_type !== "oxidation_factor");
    expect(failureOf(assembleCombustionFactor(request(), noOxidation, mapping))).toMatchObject({ kind: "record_not_found", parameter: "oxidation" });
  });

  it("같은 조건의 낱줄이 둘이면 record_ambiguous 다 (앞의 것을 집지 않는다)", () => {
    const twin = synthetic(byId("kr-ets-byl12:ef:p3r3:CO2"), { id: "synthetic:kr-ets-byl12:ef:dup", value_raw: "70,000" });
    expect(failureOf(assembleCombustionFactor(request(), [...records, twin], mapping))).toMatchObject({
      kind: "record_ambiguous",
      parameter: "CO2",
      recordIds: ["kr-ets-byl12:ef:p3r3:CO2", "synthetic:kr-ets-byl12:ef:dup"],
    });
  });

  it("탄소 기준 계수(kgC/TJ)나 총발열량을 CO2 계수·발열량으로 집지 않는다", () => {
    const { evidence } = valueOf(assemble());
    expect(byId(evidence.parameters[1]?.recordId ?? "").gas_basis).toBe("gas_mass");
    expect(byId(evidence.parameters[0]?.recordId ?? "").heating_value_basis).toBe("net");
    const noNet = records.filter((r) => r.id !== "kr-ets-byl12:hv:p1r5:net");
    expect(failureOf(assembleCombustionFactor(request(), noNet, mapping))).toMatchObject({ kind: "record_not_found", parameter: "calorific" });
    const noCo2 = records.filter((r) => r.id !== "kr-ets-byl12:ef:p3r3:CO2");
    expect(failureOf(assembleCombustionFactor(request(), noCo2, mapping))).toMatchObject({ kind: "record_not_found", parameter: "CO2" });
  });

  it("값이 비어 있는 낱줄, 다른 고시 판의 낱줄, 모르는 단위의 낱줄은 실패다", () => {
    const swap = (id: string, patch: Parameters<typeof synthetic>[1]) => [...records.filter((r) => r.id !== id), synthetic(byId(id), patch)];
    const id = "kr-ets-byl12:ef:p3r3:CO2";
    expect(failureOf(assembleCombustionFactor(request(), swap(id, { id, value_raw: "-", value_status: "not_available_in_source" }), mapping))).toEqual({
      kind: "record_value_missing",
      parameter: "CO2",
      recordId: id,
      valueStatus: "not_available_in_source",
    });
    const old = { ...byId(id).source, version: "synthetic 옛 고시" };
    expect(failureOf(assembleCombustionFactor(request(), swap(id, { id, source: old }), mapping))).toMatchObject({ kind: "guideline_version_mismatch", recordId: id });
    expect(failureOf(assembleCombustionFactor(request(), swap(id, { id, unit_numerator: "tCO2" }), mapping))).toEqual({
      kind: "unit_notation_unknown",
      parameter: "CO2",
      recordId: id,
      unit: "tCO2/TJ",
    });
    const hv = "kr-ets-byl12:hv:p1r5:net";
    expect(failureOf(assembleCombustionFactor(request(), swap(hv, { id: hv, unit_denominator: "gal" }), mapping))).toMatchObject({ kind: "unit_notation_unknown", parameter: "calorific", unit: "MJ/gal" });
  });
});

describe("입력 누락과 틀린 입력", () => {
  const raw = (patch: Record<string, unknown>) => assembleCombustionFactor({ ...request(), ...patch } as never, records, mapping);
  const without = (key: string) => {
    const copy: Record<string, unknown> = { ...request() };
    delete copy[key];
    return assembleCombustionFactor(copy as never, records, mapping);
  };

  it.each(["fuel", "combustionType", "industry", "tiers"])("%s 가 빠지면 조립하지 않고 빠진 것을 알려 준다", (field) => {
    expect(failureOf(without(field))).toMatchObject({ kind: "missing_input", field });
  });

  it("산정등급의 일부만 주면 나머지를 채우지 않는다", () => {
    expect(failureOf(raw({ tiers: { kind: "explicit", calorific: 2, co2: 2 } }))).toMatchObject({ kind: "missing_input", field: "tiers.oxidation" });
    expect(failureOf(raw({ tiers: { kind: "explicit", co2: 2, oxidation: 2 } }))).toMatchObject({ kind: "missing_input", field: "tiers.calorific" });
    expect(failureOf(raw({ tiers: { kind: "group_minimum" } }))).toMatchObject({ kind: "missing_input", field: "tiers.group" });
    expect(failureOf(raw({ tiers: {} }))).toMatchObject({ kind: "missing_input", field: "tiers.kind" });
    expect(failureOf(raw({ industry: {} }))).toMatchObject({ kind: "missing_input", field: "industry" });
    expect(failureOf(raw({ fuel: "" }))).toMatchObject({ kind: "missing_input", field: "fuel" });
  });

  it("틀린 값", () => {
    expect(failureOf(raw({ tiers: { kind: "explicit", calorific: 2, co2: 2, oxidation: "2" } }))).toMatchObject({ kind: "invalid_input", field: "tiers.oxidation" });
    expect(failureOf(raw({ tiers: { kind: "group_minimum", group: "D" } }))).toMatchObject({ kind: "invalid_input", field: "tiers.group", value: "D" });
    expect(failureOf(raw({ tiers: { kind: "tier2" } }))).toMatchObject({ kind: "invalid_input", field: "tiers.kind" });
    expect(failureOf(raw({ industry: { sector: "manufacturing" } }))).toMatchObject({ kind: "invalid_input", field: "industry.sector" });
    expect(failureOf(raw({ industry: { ksicCode: 20 } }))).toMatchObject({ kind: "invalid_input", field: "industry.ksicCode" });
    expect(failureOf(raw({ industry: { ksicCode: "C20" } }))).toMatchObject({ kind: "invalid_input", field: "industry.ksicCode" });
    expect(failureOf(raw({ industry: { sector: "commercial_public", ksicCode: "20" } }))).toMatchObject({ kind: "invalid_input", field: "industry" });
    expect(failureOf(raw({ fuelState: "liquid" }))).toMatchObject({ kind: "invalid_input", field: "fuelState" });
    expect(failureOf(raw({ powerGeneration: "yes" }))).toMatchObject({ kind: "invalid_input", field: "powerGeneration" });
    expect(failureOf(raw({ combustionType: "mobile" }))).toEqual({ kind: "unsupported_combustion_type", combustionType: "mobile" });
  });

  it("지침이 허용하지 않는 등급 조합", () => {
    // 산화계수는 CO2 배출계수와 같은 등급이어야 한다 (별표 6).
    expect(failureOf(raw({ tiers: { kind: "explicit", calorific: 2, co2: 2, oxidation: 1 } }))).toMatchObject({ kind: "tier_combination_invalid" });
    expect(failureOf(raw({ tiers: { kind: "explicit", calorific: 2, co2: 1, oxidation: 2 } }))).toMatchObject({ kind: "tier_combination_invalid" });
    expect(failureOf(raw({ tiers: { kind: "explicit", calorific: 2, co2: 2, oxidation: 2, nonCo2: 2 } }))).toMatchObject({ kind: "tier_combination_invalid" });
  });

  it("Tier 3·4 는 사업장의 값이 있어야 해서 고시의 표로는 조립하지 않는다 (C 그룹 최소기준 포함)", () => {
    expect(failureOf(raw({ tiers: { kind: "group_minimum", group: "C" } }))).toMatchObject({ kind: "tier_requires_site_values", parameter: "발열량", tier: 3 });
    expect(failureOf(raw({ tiers: { kind: "explicit", calorific: 2, co2: 3, oxidation: 3 } }))).toMatchObject({ kind: "tier_requires_site_values", parameter: "CO2 배출계수" });
    expect(failureOf(raw({ tiers: { kind: "explicit", calorific: 4, co2: 2, oxidation: 2 } }))).toMatchObject({ kind: "tier_requires_site_values", tier: 4 });
  });

  it("대응표에 그 그룹의 최소기준이 없으면 실패다", () => {
    const thin = { ...mapping, tier_minimums: mapping.tier_minimums.filter((e) => e.parameter !== "배출계수") };
    expect(failureOf(assembleCombustionFactor(request({ tiers: GROUP_A }), records, thin))).toEqual({
      kind: "tier_minimum_not_found",
      fuelState: "액체",
      group: "A",
      parameter: "배출계수",
      found: 0,
    });
  });
});

describe("십진 정확도: value_raw 에서만 읽는다", () => {
  it("계수 값은 value_raw 의 글자에서 오고 쉼표만 걷어 낸다", () => {
    const { factor, evidence } = valueOf(assemble());
    for (const p of evidence.parameters) {
      expect(p.valueRaw).toBe(byId(p.recordId).value_raw);
      expect(p.value).toBe(p.valueRaw.replaceAll(",", ""));
    }
    expect(factor.emissionFactors.map((e) => e.value)).toEqual(["73200", "3", "0.6"]);
    for (const text of [factor.calorific.value, factor.oxidationFactor, ...factor.emissionFactors.map((e) => e.value)]) expect(typeof text).toBe("string");
  });

  it("number 를 거치면 깨지는 값도 글자 그대로 실린다", () => {
    const id = "kr-ets-byl12:ef:p3r3:CO2";
    const precise = synthetic(byId(id), { id, value_raw: "73,200.10000000000000000001" });
    const { factor } = valueOf(assembleCombustionFactor(request(), [...records.filter((r) => r.id !== id), precise], mapping));
    expect(factor.emissionFactors[0]?.value).toBe("73200.10000000000000000001");
  });

  it.each(["7.32e4", "73 200", "73,20", "1,2", "+73200", "73200 kg", "", "-", "-73,200", "７３２００"])("이상 표기 %j 는 실패다", (valueRaw) => {
    const id = "kr-ets-byl12:ef:p3r3:CO2";
    const odd = synthetic(byId(id), { id, value_raw: valueRaw });
    expect(failureOf(assembleCombustionFactor(request(), [...records.filter((r) => r.id !== id), odd], mapping))).toEqual({
      kind: "invalid_value_raw",
      parameter: "CO2",
      recordId: id,
      valueRaw,
    });
  });
});
