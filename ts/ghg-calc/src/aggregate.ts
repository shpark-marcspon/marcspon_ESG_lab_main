import { ZERO, add, formatDecimal, parseDecimal, roundDecimal, type Decimal, type RoundingMode } from "./decimal.js";
import type { DecimalString, FactorGas, RowResult } from "./types.js";

export type GroupKey = "orgUnit" | "period" | "scope" | "sourceType";

export interface Totals {
  /** 성공한 행의 정확한 합 (tCO2eq). 반올림하지 않는다. */
  co2e: DecimalString;
  co2eByGas: Partial<Record<FactorGas, DecimalString>>;
  okRowCount: number;
  /** 계산에 실패한 행. 합계에 0 으로 들어간 것이 아니라 빠져 있다. */
  failedRowIds: string[];
  /** 실패한 행이 하나라도 있으면 false. 이때 co2e 는 부분합이다. */
  complete: boolean;
}

export interface Group extends Totals {
  key: Partial<Record<GroupKey, string>>;
}

export interface Aggregation {
  by: GroupKey[];
  groups: Group[];
  total: Totals;
}

interface Accumulator {
  co2e: Decimal;
  byGas: Map<FactorGas, Decimal>;
  okRowCount: number;
  failedRowIds: string[];
}

const GAS_ORDER: readonly FactorGas[] = ["CO2", "CH4", "N2O", "CO2e"];

function must(text: string): Decimal {
  const value = parseDecimal(text);
  if (value === null) throw new Error(`row result holds a non-decimal value: ${text}`);
  return value;
}

function accumulate(acc: Accumulator, row: RowResult): void {
  if (row.status === "failed") {
    acc.failedRowIds.push(row.rowId);
    return;
  }
  acc.okRowCount += 1;
  acc.co2e = add(acc.co2e, must(row.co2e));
  for (const gas of row.gases) {
    acc.byGas.set(gas.gas, add(acc.byGas.get(gas.gas) ?? ZERO, must(gas.co2e)));
  }
}

function finish(acc: Accumulator): Totals {
  const co2eByGas: Partial<Record<FactorGas, DecimalString>> = {};
  for (const gas of GAS_ORDER) {
    const value = acc.byGas.get(gas);
    if (value !== undefined) co2eByGas[gas] = formatDecimal(value);
  }
  return {
    co2e: formatDecimal(acc.co2e),
    co2eByGas,
    okRowCount: acc.okRowCount,
    failedRowIds: acc.failedRowIds,
    complete: acc.failedRowIds.length === 0,
  };
}

const emptyAccumulator = (): Accumulator => ({ co2e: ZERO, byGas: new Map(), okRowCount: 0, failedRowIds: [] });

/**
 * 집계 규칙: 합계는 행의 **정확한 값**을 더한 것이다. 반올림한 행 값을 더하지 않는다.
 * 그래서 어떤 묶음으로 나눠도 (묶음 합의 합) = (전체 합) = (행 값의 합)이 문자열까지 같다.
 * 보고용 자릿수는 roundForReport 로 합계에 한 번만 적용한다.
 */
export function aggregate(rows: readonly RowResult[], by: readonly GroupKey[]): Aggregation {
  const total = emptyAccumulator();
  const groups = new Map<string, { key: Partial<Record<GroupKey, string>>; acc: Accumulator }>();

  for (const row of rows) {
    accumulate(total, row);
    const key: Partial<Record<GroupKey, string>> = {};
    for (const k of by) key[k] = String(row[k]);
    const id = JSON.stringify(by.map((k) => key[k]));
    let group = groups.get(id);
    if (group === undefined) {
      group = { key, acc: emptyAccumulator() };
      groups.set(id, group);
    }
    accumulate(group.acc, row);
  }

  const ordered = [...groups.entries()].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  return {
    by: [...by],
    groups: ordered.map(([, g]) => ({ key: g.key, ...finish(g.acc) })),
    total: finish(total),
  };
}

/** 보고용 반올림. 자릿수와 방식에 기본값이 없다. 호출자가 보고 기준에 맞춰 정한다. */
export function roundForReport(value: DecimalString, digits: number, mode: RoundingMode): string {
  return roundDecimal(must(value), digits, mode);
}
