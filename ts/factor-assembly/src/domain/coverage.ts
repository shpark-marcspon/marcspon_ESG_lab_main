// 덮인 범위: 별표 12 발열량 표의 연료마다 산정등급 조합 셋 × 연료 상태를 주지 않은 경우·준 경우로 조립해 보고,
// 조립된 계수로 엔진이 그 연료의 별표 12 단위(L, kg, Nm3) 사용량을 계산할 수 있는지까지 센다.
// 순수 함수다. 표와 대응표는 값으로 받는다. 조립 실패와 계산 실패를 따로 센다
// (전부 Tier 1 의 발열량은 별표 11 의 TJ/Gg 라서 리터·Nm3 사용량에는 밀도가 있어야 하고(조합기의 범위 밖),
// kg 사용량에는 kg→Gg 환산이 엔진의 환산표(data/ghg-calc/unit-conversions.json)에 있어야 한다. 지금은 둘 다 없다).

import { calculateRow, type ActivityRow, type GwpSet, type UnitConversion } from "ghg-calc";
import { SOURCE_IDS, assembleCombustionFactor } from "./assemble.js";
import type { AssemblyMapping, AssemblyRequest, FactorRecord, FuelState, TierPolicy } from "./types.js";

export type CoverageMode = "all_tier2" | "group_minimum_a" | "all_tier1";

/** 조합 셋. 수치가 아니라 산정등급 정책이다. */
export const COVERAGE_MODES: ReadonlyArray<{ mode: CoverageMode; label: string; tiers: TierPolicy }> = [
  { mode: "all_tier2", label: "전부 Tier 2", tiers: { kind: "explicit", calorific: 2, co2: 2, oxidation: 2 } },
  { mode: "group_minimum_a", label: "A 그룹 최소기준", tiers: { kind: "group_minimum", group: "A" } },
  { mode: "all_tier1", label: "전부 Tier 1", tiers: { kind: "explicit", calorific: 1, co2: 1, oxidation: 1 } },
];

/** 원문의 단위 표기 → 엔진의 단위 기호 (assemble.ts 의 CALORIFIC_PER_UNIT 과 같은 대응). */
const USAGE_UNITS: Record<string, string> = { L: "L", kg: "kg", "N㎥": "Nm3", kWh: "kWh" };

export type CoverageOutcome = { ok: true; factorId: string; assumptions: number } | { ok: false; kind: string; where: string | null };

export interface CoverageRow {
  fuel: string;
  mode: CoverageMode;
  stateGiven: boolean;
  /** 준 연료 상태. 주지 않았거나 줄 상태가 없으면 null. */
  givenState: FuelState | null;
  assembly: CoverageOutcome;
  /** 조립에 성공했을 때만. 그 연료의 별표 12 단위로 사용량 1000 을 넣어 엔진이 계산했는가. */
  calculation: { ok: true; usageUnit: string } | { ok: false; usageUnit: string; kind: string; where: string | null } | null;
}

export interface CoverageInputs {
  /** 업종 (부문). 덮인 범위를 셀 때는 하나로 고정한다. */
  industry: AssemblyRequest["industry"];
  /** 고체연료 Tier 2 산화계수에 필요한 발전 여부. 덮인 범위를 셀 때는 하나로 고정한다. */
  powerGeneration: boolean;
  /** 「상태를 준 경우」에 줄 상태. 연료 → 상태. 없으면 그 연료는 상태를 주지 않은 것과 같다. */
  givenStates: Readonly<Record<string, FuelState>>;
  gwpSetId: string;
  unitConversions: UnitConversion[];
  gwpSets: GwpSet[];
}

/** 별표 12 순발열량 표의 연료 이름과 단위 (표의 순서). 분모가 여기서 나온다. */
export function nationalCalorificFuels(records: readonly FactorRecord[]): Array<{ fuel: string; unit: string; recordId: string }> {
  return records
    .filter((r) => r.source.id === SOURCE_IDS.national && r.record_type === "heating_value" && r.heating_value_basis === "net")
    .map((r) => ({ fuel: r.name.original, unit: r.unit_denominator, recordId: r.id }));
}

function whereOf(failure: Record<string, unknown>): string | null {
  for (const key of ["parameter", "field", "table"] as const) {
    const value = failure[key];
    if (typeof value === "string") return value;
  }
  return null;
}

export function coverageRows(records: readonly FactorRecord[], mapping: AssemblyMapping, inputs: CoverageInputs): CoverageRow[] {
  const rows: CoverageRow[] = [];
  for (const { fuel, unit } of nationalCalorificFuels(records)) {
    for (const { mode, tiers } of COVERAGE_MODES) {
      for (const stateGiven of [false, true]) {
        const givenState = stateGiven ? (inputs.givenStates[fuel] ?? null) : null;
        const request: AssemblyRequest = {
          fuel,
          combustionType: "stationary",
          industry: inputs.industry,
          tiers,
          powerGeneration: inputs.powerGeneration,
          ...(givenState === null ? {} : { fuelState: givenState }),
        };
        const assembled = assembleCombustionFactor(request, records, mapping);
        if (!assembled.ok) {
          rows.push({ fuel, mode, stateGiven, givenState, assembly: { ok: false, kind: assembled.failure.kind, where: whereOf(assembled.failure) }, calculation: null });
          continue;
        }
        const { factor, evidence } = assembled.value;
        const usageUnit = USAGE_UNITS[unit] ?? unit;
        const activity: ActivityRow = {
          id: `${fuel}:${mode}`,
          orgUnit: "coverage",
          period: "-",
          sourceType: "stationary_combustion",
          fuel,
          ...(factor.sector === undefined ? {} : { sector: factor.sector }),
          ...(factor.parameterTiers === undefined ? {} : { parameterTiers: factor.parameterTiers }),
          ...(factor.facilityUse === undefined ? {} : { facilityUse: factor.facilityUse }),
          amount: "1000",
          unit: usageUnit,
        };
        const result = calculateRow(activity, { unitConversions: inputs.unitConversions, gwpSets: inputs.gwpSets, factors: [factor] }, { gwpSetId: inputs.gwpSetId, requireCalorificBasis: "net" });
        rows.push({
          fuel,
          mode,
          stateGiven,
          givenState,
          assembly: { ok: true, factorId: factor.id, assumptions: evidence.assumptions.length },
          calculation:
            result.status === "ok"
              ? { ok: true, usageUnit }
              : { ok: false, usageUnit, kind: result.failure.kind, where: result.failure.kind === "unit_conversion_not_found" ? `${result.failure.from}→${result.failure.to}` : null },
        });
      }
    }
  }
  return rows;
}

export interface CoverageSummaryCell {
  mode: CoverageMode;
  stateGiven: boolean;
  fuels: number;
  assembled: number;
  /** 실패 종류(와 자리)별 수. 예: `fuel_name_not_mapped (CH4)`. */
  assemblyFailures: Record<string, number>;
  calculated: number;
  calculationFailures: Record<string, number>;
}

export function coverageSummary(rows: readonly CoverageRow[]): CoverageSummaryCell[] {
  const cells: CoverageSummaryCell[] = [];
  for (const { mode } of COVERAGE_MODES) {
    for (const stateGiven of [false, true]) {
      const own = rows.filter((row) => row.mode === mode && row.stateGiven === stateGiven);
      const cell: CoverageSummaryCell = { mode, stateGiven, fuels: own.length, assembled: 0, assemblyFailures: {}, calculated: 0, calculationFailures: {} };
      for (const row of own) {
        if (!row.assembly.ok) {
          const key = `${row.assembly.kind}${row.assembly.where === null ? "" : ` (${row.assembly.where})`}`;
          cell.assemblyFailures[key] = (cell.assemblyFailures[key] ?? 0) + 1;
          continue;
        }
        cell.assembled += 1;
        if (row.calculation?.ok === true) cell.calculated += 1;
        else if (row.calculation !== null && !row.calculation.ok) {
          const key = `${row.calculation.kind}${row.calculation.where === null ? "" : ` (${row.calculation.where})`}`;
          cell.calculationFailures[key] = (cell.calculationFailures[key] ?? 0) + 1;
        }
      }
      cells.push(cell);
    }
  }
  return cells;
}

/** 대응표에 실린 연료 상태 (「상태를 준 경우」의 기본 입력). */
export function statesFromMapping(mapping: AssemblyMapping): Record<string, FuelState> {
  return Object.fromEntries(mapping.fuel_states.map((e) => [e.fuel, e.state]));
}
