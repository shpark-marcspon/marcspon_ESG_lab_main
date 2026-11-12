import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { aggregate, calculate, calculateRow } from "../src/index.js";
import { add, formatDecimal, mul, parseDecimal, type Decimal } from "../src/decimal.js";
import type { ActivityRow, RowResult } from "../src/index.js";
import type { GroupKey } from "../src/aggregate.js";
import { COMMERCIAL, ETS_GWP, MANUFACTURING, TIER2, bare, loadTables } from "./fixtures.js";

const tables = loadTables();
const opts = { gwpSetId: ETS_GWP };
const dec = (text: string): Decimal => {
  const value = parseDecimal(text);
  if (value === null) throw new Error(`not a decimal: ${text}`);
  return value;
};
const sum = (values: string[]) => formatDecimal(values.reduce((acc, v) => add(acc, dec(v)), dec("0")));

// 소수 6자리까지의 음이 아닌 사용량
const amount = fc
  .tuple(fc.bigInt({ min: 0n, max: 10n ** 12n }), fc.integer({ min: 0, max: 6 }))
  .map(([coef, scale]) => formatDecimal({ coef, scale }));

const activity: fc.Arbitrary<ActivityRow> = fc
  .record({
    template: fc.constantFrom(
      { sourceType: "stationary_combustion", fuel: "diesel", sector: MANUFACTURING, parameterTiers: TIER2, unit: "L" },
      { sourceType: "stationary_combustion", fuel: "diesel", sector: MANUFACTURING, parameterTiers: TIER2, unit: "kL" },
      { sourceType: "stationary_combustion", fuel: "city-gas-lng", sector: COMMERCIAL, parameterTiers: TIER2, unit: "Nm3" },
      { sourceType: "mobile_combustion", fuel: "synthetic-fuel-a", unit: "L" },
      { sourceType: "stationary_combustion", fuel: "synthetic-fuel-b", unit: "t" },
      { sourceType: "purchased_electricity", factorId: "kr-ets-sample:electricity:2014-2016-avg", unit: "kWh" },
      { sourceType: "purchased_heat", unit: "GJ" },
      // 계수가 없는 행: 집계에서 0 이 아니라 실패로 남아야 한다
      { sourceType: "stationary_combustion", fuel: "no-such-fuel", unit: "L" },
    ) as fc.Arbitrary<Pick<ActivityRow, "sourceType" | "unit"> & Partial<ActivityRow>>,
    orgUnit: fc.constantFrom("site-1", "site-2", "site-3"),
    period: fc.constantFrom("2025-01", "2025-02", "2025-03"),
    amount,
  })
  .map(({ template, orgUnit, period, amount: value }) => bare({ ...template, orgUnit, period, amount: value }));

const activities = fc.array(activity, { maxLength: 40 }).map((rows) => rows.map((r, i) => ({ ...r, id: `p${i}` })));
const groupKeys = fc.subarray<GroupKey>(["orgUnit", "period", "scope", "sourceType"]);
const okCo2e = (rows: RowResult[]) => rows.flatMap((r) => (r.status === "ok" ? [r.co2e] : []));

describe("속성", () => {
  it("행 단위 합 = 집계 합 = 묶음 합의 합 (문자열까지)", () => {
    fc.assert(
      fc.property(activities, groupKeys, (input, by) => {
        const rows = calculate(input, tables, opts);
        const result = aggregate(rows, by);
        const rowSum = sum(okCo2e(rows));
        expect(result.total.co2e).toBe(rowSum);
        expect(sum(result.groups.map((g) => g.co2e))).toBe(rowSum);
        expect(sum(Object.values(result.total.co2eByGas))).toBe(rowSum);
        // 실패한 행은 어디에도 0 으로 섞이지 않고 id 로 남는다
        const failedIds = rows.filter((r) => r.status === "failed").map((r) => r.rowId);
        expect(result.total.failedRowIds).toEqual(failedIds);
        expect(result.groups.flatMap((g) => g.failedRowIds).sort()).toEqual([...failedIds].sort());
        expect(result.groups.reduce((n, g) => n + g.okRowCount, 0)).toBe(rows.length - failedIds.length);
      }),
      { numRuns: 300 },
    );
  });

  it("행의 합계 = 그 행의 기체별 환산값의 합", () => {
    fc.assert(
      fc.property(activity, (input) => {
        const result = calculateRow(input, tables, opts);
        if (result.status === "ok") expect(sum(result.gases.map((g) => g.co2e))).toBe(result.co2e);
      }),
      { numRuns: 300 },
    );
  });

  it("사용량 k 배 → 배출량 정확히 k 배 (k = 2, 3, 0.5, 1.37)", () => {
    fc.assert(
      fc.property(activity, fc.constantFrom("2", "3", "0.5", "1.37"), (input, k) => {
        const base = calculateRow(input, tables, opts);
        const scaled = calculateRow({ ...input, amount: formatDecimal(mul(dec(input.amount), dec(k))) }, tables, opts);
        expect(scaled.status).toBe(base.status);
        if (base.status === "ok" && scaled.status === "ok") {
          expect(scaled.co2e).toBe(formatDecimal(mul(dec(base.co2e), dec(k))));
          scaled.gases.forEach((g, i) => expect(g.mass).toBe(formatDecimal(mul(dec(base.gases[i]!.mass), dec(k)))));
        }
      }),
      { numRuns: 300 },
    );
  });

  it("같은 입력이면 같은 출력이다 (결정적)", () => {
    fc.assert(
      fc.property(activities, (input) => {
        expect(JSON.stringify(calculate(input, tables, opts))).toBe(JSON.stringify(calculate(input, tables, opts)));
      }),
      { numRuns: 50 },
    );
  });

  it("행 순서를 바꿔도 합계가 같다", () => {
    fc.assert(
      fc.property(activities, (input) => {
        const forward = aggregate(calculate(input, tables, opts), ["scope"]);
        const backward = aggregate(calculate([...input].reverse(), tables, opts), ["scope"]);
        expect(backward.total.co2e).toBe(forward.total.co2e);
        expect(backward.groups.map((g) => [g.key, g.co2e])).toEqual(forward.groups.map((g) => [g.key, g.co2e]));
      }),
      { numRuns: 100 },
    );
  });
});
