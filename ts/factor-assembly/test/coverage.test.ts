import { readFileSync } from "node:fs";
import { join } from "node:path";
import { validateTables, type GwpSet, type UnitConversion } from "ghg-calc";
import { describe, expect, it } from "vitest";
import { coverageRows, coverageSummary, nationalCalorificFuels, statesFromMapping, type CoverageRow } from "../src/index.js";
import { DATA, mapping, readData, records } from "./helpers.js";

// 덮인 범위: 별표 12 발열량 표의 연료 29개 × 조합 셋 × 상태 지정 여부. 조립 결과와 계산 결과를 고정해 다음 변경에서 덮인 범위가 조용히 줄지 않게 한다.
// 기대값은 test/coverage.expected.json (`pnpm tsx src/cli.ts coverage --json` 의 rows).

const tables = validateTables({
  unitConversions: (readData("ghg-calc/unit-conversions.json") as { unitConversions: UnitConversion[] }).unitConversions,
  gwpSets: (readData("ghg-calc/gwp-sets.json") as { gwpSets: GwpSet[] }).gwpSets,
  factors: [],
});
if (!tables.ok) throw new Error("환산표·GWP 표에 문제가 있다");
const inputs = {
  industry: { sector: "manufacturing_construction" },
  powerGeneration: false,
  givenStates: statesFromMapping(mapping),
  gwpSetId: "kr-ets-decree-annex2",
  unitConversions: tables.tables.unitConversions,
  gwpSets: tables.tables.gwpSets,
};
const expected = (JSON.parse(readFileSync(join(DATA, "..", "ts", "factor-assembly", "test", "coverage.expected.json"), "utf8")) as { rows: CoverageRow[] }).rows;
const rows = coverageRows(records, mapping, inputs);

describe("덮인 범위 (별표 12 연료 × 조합 × 상태 지정 여부)", () => {
  it("분모는 별표 12 순발열량 표의 연료 29개다 (전기 2개 포함)", () => {
    const fuels = nationalCalorificFuels(records).map((f) => f.fuel);
    expect(fuels.length).toBe(29);
    expect(fuels.filter((f) => f.startsWith("전기"))).toEqual(["전기(발전기준)", "전기(소비기준)"]);
    expect(rows.length).toBe(29 * 3 * 2);
  });

  it("연료마다 조립·계산 결과가 고정된 표와 같다", () => {
    expect(rows).toEqual(expected);
  });

  it("조합별 수: 전부 Tier 2 22, A 그룹 최소 24, 전부 Tier 1 24 (계산은 0, 단위 환산이 없다)", () => {
    const summary = coverageSummary(rows);
    const pick = (mode: string, stateGiven: boolean) => summary.find((c) => c.mode === mode && c.stateGiven === stateGiven);
    for (const stateGiven of [false, true]) {
      expect(pick("all_tier2", stateGiven)).toMatchObject({ assembled: 22, calculated: 22 });
      expect(pick("group_minimum_a", stateGiven)).toMatchObject({ assembled: 24, calculated: 24 });
      expect(pick("all_tier1", stateGiven)).toMatchObject({ assembled: 24, calculated: 0 });
    }
    expect(pick("all_tier2", false)?.assemblyFailures).toEqual({ "record_not_found (CO2)": 2, "fuel_name_not_mapped (CH4)": 3, fuel_state_unknown: 2 });
    expect(pick("all_tier1", true)?.calculationFailures).toEqual({
      "unit_conversion_not_found (kg→Gg)": 13,
      "unit_conversion_not_found (L→Gg)": 10,
      "unit_conversion_not_found (Nm3→Gg)": 1,
    });
  });

  it("전기 두 줄은 연소 연료가 아니라 상태가 없어 조립되지 않는다 (구매 전력은 따로 조립한다)", () => {
    expect(rows.filter((r) => r.fuel.startsWith("전기")).every((r) => !r.assembly.ok && r.assembly.kind === "fuel_state_unknown")).toBe(true);
  });

  it("대응표에서 연료 하나의 이름 대응을 지우면 표가 달라진다", () => {
    const removed = { ...mapping, fuel_names: mapping.fuel_names.filter((e) => !e.domestic_names.includes("항공유")) };
    expect(coverageRows(records, removed, inputs)).not.toEqual(expected);
  });
});
