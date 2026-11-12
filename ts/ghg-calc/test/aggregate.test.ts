import { describe, expect, it } from "vitest";
import { aggregate, calculate, roundForReport } from "../src/index.js";
import type { ActivityRow } from "../src/index.js";
import { COMMERCIAL, ETS_GWP, bare, loadTables, row } from "./fixtures.js";

const tables = loadTables();
const ELECTRICITY = "kr-ets-sample:electricity:2014-2016-avg";

const activities: ActivityRow[] = [
  row({ id: "a1", orgUnit: "site-1", period: "2025-01", amount: "1000" }),
  row({ id: "a2", orgUnit: "site-1", period: "2025-02", amount: "333.333" }),
  row({ id: "a3", orgUnit: "site-2", period: "2025-01", fuel: "city-gas-lng", sector: COMMERCIAL, amount: "12345.678", unit: "Nm3" }),
  bare({ id: "a4", orgUnit: "site-2", period: "2025-01", sourceType: "purchased_electricity", factorId: ELECTRICITY, amount: "1234.567", unit: "MWh" }),
  bare({ id: "a5", orgUnit: "site-1", period: "2025-01", sourceType: "purchased_electricity", factorId: ELECTRICITY, amount: "777", unit: "kWh" }),
];

// 기대값은 엔진과 따로 계산했다 (Python decimal, 반올림 없음).
//   a1 2.5596384 / a2 0.8532119467872 / a3 26.8724539692981 / a4 567.1731662102 / a5 0.3569620362
describe("집계", () => {
  const rows = calculate(activities, tables, { gwpSetId: ETS_GWP });

  it("Scope 별 합계", () => {
    const result = aggregate(rows, ["scope"]);
    expect(result.groups.map((g) => [g.key, g.co2e, g.okRowCount])).toEqual([
      [{ scope: "1" }, "30.2853043160853", 3],
      [{ scope: "2" }, "567.5301282464", 2],
    ]);
    expect(result.total.co2e).toBe("597.8154325624853");
    expect(result.total.complete).toBe(true);
  });

  it("조직 × 기간 × 배출원 유형으로 묶고, 묶음은 키 순으로 정렬된다", () => {
    const result = aggregate(rows, ["orgUnit", "period", "sourceType"]);
    expect(result.groups.map((g) => Object.values(g.key).join("|"))).toEqual([
      "site-1|2025-01|purchased_electricity",
      "site-1|2025-01|stationary_combustion",
      "site-1|2025-02|stationary_combustion",
      "site-2|2025-01|purchased_electricity",
      "site-2|2025-01|stationary_combustion",
    ]);
    expect(result.by).toEqual(["orgUnit", "period", "sourceType"]);
  });

  it("묶음 기준이 없으면 전체 한 묶음이다", () => {
    const result = aggregate(rows, []);
    expect(result.groups).toHaveLength(1);
    expect(result.groups[0]?.co2e).toBe(result.total.co2e);
  });

  it("기체별 환산 합계의 합이 전체 합계와 같다", () => {
    const { total } = aggregate(rows, []);
    expect(total.co2eByGas).toEqual({
      CO2: "594.3899091441157",
      CH4: "0.1467747274518",
      N2O: "3.2787486909178",
    });
  });

  it("실패한 행은 0 으로 더하지 않고 빠진 행으로 드러낸다", () => {
    const withFailure = calculate([...activities, row({ id: "bad", orgUnit: "site-1", period: "2025-01", fuel: "no-such-fuel" })], tables, { gwpSetId: ETS_GWP });
    const result = aggregate(withFailure, ["orgUnit"]);
    expect(result.total).toMatchObject({ co2e: "597.8154325624853", okRowCount: 5, failedRowIds: ["bad"], complete: false });
    const site1 = result.groups.find((g) => g.key.orgUnit === "site-1");
    const site2 = result.groups.find((g) => g.key.orgUnit === "site-2");
    expect(site1).toMatchObject({ failedRowIds: ["bad"], complete: false });
    expect(site2).toMatchObject({ failedRowIds: [], complete: true });
  });

  it("배출원 유형을 모르는 행은 어느 Scope 에도 넣지 않고 실패로 남긴다", () => {
    const withUnknown = calculate([...activities, bare({ id: "odd", sourceType: "scope3_travel" as never, amount: "1", unit: "GJ" })], tables, { gwpSetId: ETS_GWP });
    const result = aggregate(withUnknown, ["scope"]);
    expect(result.total).toMatchObject({ co2e: "597.8154325624853", failedRowIds: ["odd"], complete: false });
    expect(result.groups.find((g) => g.key.scope === "null")).toMatchObject({ co2e: "0", okRowCount: 0, failedRowIds: ["odd"] });
  });

  it("행이 없으면 합계 0, 완전", () => {
    expect(aggregate([], ["scope"])).toEqual({
      by: ["scope"],
      groups: [],
      total: { co2e: "0", co2eByGas: {}, okRowCount: 0, failedRowIds: [], complete: true },
    });
  });

  it("보고용 반올림은 정확한 합계에 한 번 적용한다 (반올림한 행을 더하지 않는다)", () => {
    const { total } = aggregate(rows, []);
    expect(roundForReport(total.co2e, 0, "half_up")).toBe("598");
    expect(roundForReport(total.co2e, 0, "down")).toBe("597");
    expect(roundForReport(total.co2e, 1, "half_up")).toBe("597.8");
    // 반올림한 행을 더하면 어긋난다: 2.6 + 0.9 + 26.9 + 567.2 + 0.4 = 598.0 ≠ 597.8
    expect(rows.map((r) => (r.status === "ok" ? roundForReport(r.co2e, 1, "half_up") : null))).toEqual(["2.6", "0.9", "26.9", "567.2", "0.4"]);
  });
});
