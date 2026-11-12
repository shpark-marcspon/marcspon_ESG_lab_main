import { describe, expect, it } from "vitest";
import { assembleCombustionFactor, nationalCalorificFuels } from "../src/index.js";
import type { AssemblyMapping, AssemblyRequest } from "../src/index.js";
import { GROUP_A, TIER1, TIER2, failureOf, mapping, records, request, valueOf } from "./helpers.js";

// MARC-TICK-18: 별표마다 이름이 다른 연료의 대응, 별표 12 안의 두 표 이름 대응, 연료 상태.
// 기대하는 레코드 id 와 값 표기는 지침 원문(환경부고시 제2025-64호 별표 10·11·12)에서 2026-10-03 에 대조한 것이다 (band-reports/O-factor-coverage.md).

const assemble = (overrides: Partial<AssemblyRequest> = {}, table: AssemblyMapping = mapping) => assembleCombustionFactor(request(overrides), records, table);
const param = (result: ReturnType<typeof assemble>, parameter: string) => valueOf(result).evidence.parameters.find((p) => p.parameter === parameter);

describe("별표 12 안에서 발열량 표와 배출계수 표의 이름이 다른 연료", () => {
  it("항공유: CO2 Tier 2 는 배출계수 표의 「항공유(JET-A1)」 낱줄이고 추정 대응으로 남는다", () => {
    const result = assemble({ fuel: "항공유" });
    const { factor, evidence } = valueOf(result);
    expect(param(result, "calorific")).toMatchObject({ recordId: "kr-ets-byl12:hv:p1r13:net", value: "33.9", nameMapping: null });
    expect(param(result, "CO2")).toMatchObject({
      recordId: "kr-ets-byl12:ef:p3r9:CO2",
      valueRaw: "73,000",
      nameUsed: "항공유(JET-A1)",
      nameMapping: { domestic: "항공유", ipcc: "항공유(JET-A1)", basis: "assumed", table: "kr-ets-byl12" },
    });
    expect(param(result, "CH4")?.nameMapping).toMatchObject({ ipcc: "제트용 등유", basis: "assumed" });
    expect(evidence.assumptions.map((a) => a.detail)).toEqual([
      "항공유 → 액체",
      "항공유 → 항공유(JET-A1) (kr-ets-byl12 배출계수 표)",
      "항공유 → 제트용 등유 (kr-ets-byl10)",
    ]);
    if (factor.source.kind !== "cited") throw new Error("unreachable");
    expect(factor.source.note).toContain("추정 대응 3건을 탔다");
    expect(factor.source.note).toContain("항공유 → 항공유(JET-A1)");
  });

  it("부탄(LPG3호): 원문 오자로 보이는 「부탄(LPP3호)」 는 고치지 않고 대응으로 잇는다", () => {
    const result = assemble({ fuel: "부탄(LPG3호)" });
    expect(param(result, "CO2")).toMatchObject({ recordId: "kr-ets-byl12:ef:p3r16:CO2", nameUsed: "부탄(LPP3호)", value: "66300" });
    expect(records.some((r) => r.name.original === "부탄(LPP3호)")).toBe(true);
    expect(records.some((r) => r.name.original === "부탄(LPG3호)" && r.record_type === "emission_factor")).toBe(false);
  });

  it("두 표에 같은 이름으로 있는 연료는 대응표를 타지 않는다 (경유)", () => {
    expect(param(assemble(), "CO2")).toMatchObject({ nameUsed: "경유", nameMapping: null });
  });

  it("별표 12 배출계수 표에 없는 연료는 다른 연료로 대신하지 않는다 (원유, 코크스의 Tier 2)", () => {
    expect(failureOf(assemble({ fuel: "원유" }))).toMatchObject({ kind: "record_not_found", parameter: "CO2", criteria: { name: "원유" } });
    expect(failureOf(assemble({ fuel: "코크스", powerGeneration: false }))).toMatchObject({ kind: "record_not_found", parameter: "CO2", criteria: { name: "코크스" } });
  });
});

describe("별표 10·11 과 이름이 다른 연료", () => {
  it("B-A유: 별표 10 의 B-A유 표(국내에너지원 기준 열이 없다)의 행 이름이 곧 국내 이름이라 지침의 대응이다", () => {
    const result = assemble({ fuel: "B-A유", tiers: GROUP_A });
    expect(param(result, "CO2")).toMatchObject({
      recordId: "kr-ets-byl10:p3t1r2:CO2:0",
      valueRaw: "75,100",
      nameMapping: { domestic: "B-A유", ipcc: "B-A유", basis: "guideline" },
    });
    // 추정은 연료 상태 하나뿐이다
    expect(valueOf(result).evidence.assumptions.map((a) => a.kind)).toEqual(["fuel_state"]);
    expect(valueOf(assemble({ fuel: "B-A유", tiers: GROUP_A, fuelState: "액체" })).evidence.assumptions).toEqual([]);
  });

  it("나프타: 별표 10 의 IPCC 이름이 「나프타」, 국내에너지원 기준이 「납사」 라서 추정 대응을 탄다", () => {
    const co2 = param(assemble({ fuel: "나프타", tiers: GROUP_A }), "CO2");
    expect(co2).toMatchObject({ nameUsed: "나프타", nameMapping: { ipcc: "나프타", basis: "assumed" } });
    expect(mapping.fuel_names.find((e) => e.table === "kr-ets-byl10" && e.ipcc_name === "나프타" && e.basis === "guideline")?.domestic_names).toEqual(["납사"]);
  });

  it("무연탄 세 연료와 유연탄 두 연료는 별표 10 의 국내 이름(줄바꿈 칸 판단 포함)에서 추정 대응으로 잇는다", () => {
    for (const [fuel, ipcc] of [
      ["국내무연탄", "무연탄"],
      ["연료용 수입무연탄", "무연탄"],
      ["원료용 수입무연탄", "무연탄"],
      ["연료용 유연탄(역청탄)", "기타 역청탄"],
      ["원료용 유연탄(역청탄)", "점결탄"],
    ] as const) {
      const co2 = param(assemble({ fuel, tiers: GROUP_A }), "CO2");
      expect(co2?.nameMapping).toMatchObject({ domestic: fuel, ipcc, basis: "assumed" });
    }
  });

  it("코크스: 별표 11 은 지침의 대응(「코크스」), 별표 10 은 「코크스(석탄)」 에서 이은 추정이다", () => {
    const result = assemble({ fuel: "코크스", tiers: TIER1 });
    expect(param(result, "calorific")?.nameMapping).toMatchObject({ ipcc: "코크스로 코크스", basis: "guideline" });
    expect(param(result, "CO2")?.nameMapping).toMatchObject({ ipcc: "코크스로 코크스", basis: "assumed" });
    expect(param(result, "CO2")).toMatchObject({ recordId: "kr-ets-byl10:p2t0r10:CO2:0", valueRaw: "107,000" });
  });

  it("도시가스(LPG) 는 별표 10·11 에 대응이 없어 실패한다 (LPG 로 대신하지 않는다)", () => {
    expect(failureOf(assemble({ fuel: "도시가스(LPG)" }))).toMatchObject({ kind: "fuel_name_not_mapped", table: "kr-ets-byl10", parameter: "CH4" });
  });
});

describe("부생연료 1·2호: 별표 10 주3·주4 의 CO2 규칙은 CO2 에만 쓴다", () => {
  it("CH4·N2O 는 대응이 없어 실패한다 (등유의 CH4 로 대신하지 않는다)", () => {
    for (const fuel of ["부생연료유1호", "부생연료유2호"]) {
      expect(failureOf(assemble({ fuel, tiers: GROUP_A }))).toEqual({ kind: "fuel_name_not_mapped", fuel, table: "kr-ets-byl10", parameter: "CH4" });
    }
  });

  it("CH4·N2O 대응이 있다면 CO2 Tier 1 은 기타 등유(1호)·잔여 연료유(2호)의 낱줄이다 (CH4·N2O 대응은 synthetic)", () => {
    const syntheticNonCo2 = (domestic: string, ipcc: string) => ({
      table: "kr-ets-byl10",
      ipcc_name: ipcc,
      domestic_names: [domestic],
      basis: "assumed" as const,
      note: "synthetic: 테스트용 CH4·N2O 대응. 지침에 없다",
      source_id: "kr-ets-byl10",
      locator: "synthetic",
      parameters: ["CH4", "N2O"],
    });
    const withNonCo2 = { ...mapping, fuel_names: [...mapping.fuel_names, syntheticNonCo2("부생연료유1호", "기타 등유"), syntheticNonCo2("부생연료유2호", "잔여 연료유")] };
    const first = assemble({ fuel: "부생연료유1호", tiers: GROUP_A }, withNonCo2);
    expect(param(first, "CO2")).toMatchObject({ recordId: "kr-ets-byl10:p1t0r10:CO2:0", valueRaw: "71,900", nameMapping: { ipcc: "기타 등유", basis: "assumed" } });
    expect(valueOf(first).evidence.assumptions.map((a) => a.detail)).toContain("부생연료유1호 → 기타 등유 (kr-ets-byl10, CO2 만)");
    const second = assemble({ fuel: "부생연료유2호", tiers: GROUP_A }, withNonCo2);
    expect(param(second, "CO2")).toMatchObject({ valueRaw: "77,400", nameMapping: { ipcc: "잔여 연료유" } });
    // Tier 2 의 CO2 는 별표 12 배출계수 표의 「부생연료 1호」 다
    expect(param(assemble({ fuel: "부생연료유1호" }, withNonCo2), "CO2")).toMatchObject({ nameUsed: "부생연료 1호", valueRaw: "73,500" });
  });

  it("CO2 에만 쓰는 대응은 별표 11(발열량)에도 쓰이지 않는다", () => {
    expect(failureOf(assemble({ fuel: "부생연료유1호", tiers: TIER1 }))).toMatchObject({ kind: "fuel_name_not_mapped", table: "kr-ets-byl11", parameter: "calorific" });
  });
});

describe("연료 상태", () => {
  it("석유코크스는 고체로 실려 Tier 2 산화계수에 발전 여부가 필요하다", () => {
    expect(failureOf(assemble({ fuel: "석유코크스" }))).toMatchObject({ kind: "missing_input", field: "powerGeneration" });
    const result = assemble({ fuel: "석유코크스", powerGeneration: false });
    expect(param(result, "oxidation")).toMatchObject({ recordId: "kr-ets-byl06:ox:1:t2:기타부문", value: "0.98" });
    expect(valueOf(result).evidence.fuelState).toMatchObject({ value: "고체", basis: "assumed" });
  });

  it("프로판·부탄은 별표 12 의 석유(16)가 아니라 별표 6 의 문장대로 기체다", () => {
    for (const fuel of ["프로판(LPG1호)", "부탄(LPG3호)"]) {
      expect(param(assemble({ fuel }), "oxidation")).toMatchObject({ recordId: "kr-ets-byl06:ox:2:t2", value: "0.995" });
    }
  });

  it("별표 12 의 연료(전기 제외 27개)는 전부 대응표에 상태가 있다", () => {
    const fuels = nationalCalorificFuels(records).map((f) => f.fuel).filter((f) => !f.startsWith("전기"));
    expect(fuels.length).toBe(27);
    expect(fuels.filter((fuel) => !mapping.fuel_states.some((e) => e.fuel === fuel))).toEqual([]);
  });
});

describe("추정 대응을 탄 자리는 근거에 빠짐없이 남는다", () => {
  it("모든 연료 × 조합: 이름 대응이나 연료 상태가 assumed 이면 assumptions 에 그 항목이 있고 출처 note 에 적힌다", () => {
    let checked = 0;
    for (const { fuel } of nationalCalorificFuels(records)) {
      for (const tiers of [TIER2, GROUP_A, TIER1]) {
        const result = assemble({ fuel, tiers, powerGeneration: false });
        if (!result.ok) continue;
        const { factor, evidence } = result.value;
        const assumed = evidence.parameters.filter((p) => p.nameMapping?.basis === "assumed").map((p) => `${p.nameMapping?.domestic} → ${p.nameMapping?.ipcc}`);
        for (const detail of assumed) expect(evidence.assumptions.some((a) => a.kind === "fuel_name" && a.detail.startsWith(detail))).toBe(true);
        expect(evidence.assumptions.some((a) => a.kind === "fuel_state")).toBe(evidence.fuelState.basis === "assumed");
        if (factor.source.kind !== "cited") throw new Error("unreachable");
        if (evidence.assumptions.length > 0) expect(factor.source.note).toContain(`추정 대응 ${evidence.assumptions.length}건을 탔다`);
        else expect(factor.source.note).not.toContain("추정 대응");
        checked += 1;
      }
    }
    expect(checked).toBe(22 + 24 + 24);
  });
});
