// 검산 재통과: 엔진의 검산 사례를 손으로 묶은 표본 계수 대신 조합기가 조립한 계수로 다시 계산해 기대값과 문자열까지 견준다.
// 순수 함수다. 사례·조립 조건·표는 값으로 받는다.

import { calculateRow, type ActivityRow, type Factor, type GwpSet, type UnitConversion } from "ghg-calc";
import { assembleCombustionFactor } from "./assemble.js";
import { assemblePurchasedElectricityFactor, type ElectricityFailure, type ElectricityRequest } from "./electricity.js";
import type { AssembledFactor, AssemblyMapping, AssemblyRequest, FactorRecord } from "./types.js";

export interface ExpectedResult {
  energy: { value: string; unit: string } | null;
  gases: Record<string, { mass: string; co2e: string }>;
  co2e: string;
}

/** 엔진의 검산 사례 (`data/ghg-calc/handcalc-cases.json`) 에서 읽는 필드. */
export interface HandCase {
  id: string;
  title: string;
  synthetic: boolean;
  gwpSetId: string;
  activity: ActivityRow;
  factor: { id: string } & Record<string, unknown>;
  expected: ExpectedResult;
}

/** 표본 계수 하나를 계수 표에서 다시 조립하는 조건 (`data/ghg-calc/handcalc-assembly.json`). */
export interface ReplaySpec {
  replays: Array<{ sampleFactorId: string; request: AssemblyRequest }>;
  /** 구매 전력 표본 계수를 다시 조립하는 조건 (MARC-TICK-18). */
  electricityReplays?: Array<{ sampleFactorId: string; request: ElectricityRequest }>;
  notReplayed: Array<{ sampleFactorId: string; reason: string }>;
  /** 표본에 없는 조합을 조립해 보는 사례. 기대값은 손으로 따로 계산해 적는다. */
  assemblyCases: Array<{ id: string; title: string; request: AssemblyRequest; gwpSetId: string; activity: ActivityRow; expected: ExpectedResult; working: string[] }>;
}

export type ReplayOutcome =
  | { status: "match"; assembledFactorId: string; assumptions: string[] }
  | { status: "mismatch"; assembledFactorId: string; differences: string[] }
  | { status: "assembly_failed"; failure: ElectricityFailure }
  | { status: "calculation_failed"; assembledFactorId: string; failure: unknown }
  | { status: "not_replayed"; reason: string };

export interface ReplayRow {
  caseId: string;
  title: string;
  sampleFactorId: string | null;
  expectedCo2e: string;
  actualCo2e: string | null;
  outcome: ReplayOutcome;
}

export interface ReplayTables {
  unitConversions: UnitConversion[];
  gwpSets: GwpSet[];
}

function compare(
  activity: ActivityRow,
  gwpSetId: string,
  expected: ExpectedResult,
  assembled: { factor: Factor; assumptions: string[] },
  tables: ReplayTables,
): { actualCo2e: string | null; outcome: ReplayOutcome } {
  const factors: Factor[] = [assembled.factor];
  // 표에는 조립한 계수 하나만 넣는다. 표본 계수가 섞여 들어가 그쪽으로 계산되는 일이 없다.
  const result = calculateRow(activity, { ...tables, factors }, { gwpSetId, requireCalorificBasis: "net" });
  const id = assembled.factor.id;
  if (result.status !== "ok") return { actualCo2e: null, outcome: { status: "calculation_failed", assembledFactorId: id, failure: result.failure } };
  const differences: string[] = [];
  if (result.co2e !== expected.co2e) differences.push(`co2e: 기대 ${expected.co2e}, 결과 ${result.co2e}`);
  if (JSON.stringify(result.energy) !== JSON.stringify(expected.energy)) {
    differences.push(`energy: 기대 ${JSON.stringify(expected.energy)}, 결과 ${JSON.stringify(result.energy)}`);
  }
  const gases = Object.fromEntries(result.gases.map((g) => [g.gas, { mass: g.mass, co2e: g.co2e }]));
  for (const gas of new Set([...Object.keys(expected.gases), ...Object.keys(gases)])) {
    const want = expected.gases[gas];
    const got = gases[gas];
    if (want === undefined || got === undefined || want.mass !== got.mass || want.co2e !== got.co2e) {
      differences.push(`${gas}: 기대 ${JSON.stringify(want)}, 결과 ${JSON.stringify(got)}`);
    }
  }
  if (result.evidence.factor.id !== id) differences.push(`계수: ${result.evidence.factor.id} 로 계산됐다`);
  return {
    actualCo2e: result.co2e,
    outcome:
      differences.length === 0
        ? { status: "match", assembledFactorId: id, assumptions: assembled.assumptions }
        : { status: "mismatch", assembledFactorId: id, differences },
  };
}

const withAssumptions = (assembled: AssembledFactor) => ({ factor: assembled.factor, assumptions: assembled.evidence.assumptions.map((a) => `${a.kind}: ${a.detail}`) });

/**
 * synthetic 이 아닌 검산 사례를 조립한 계수로 다시 돌린다.
 * 구매 전력 사례는 `electricityReplays` 의 조건으로 조립하고, 활동 데이터의 `factorId` 만 조립한 계수의 id 로 바꾼다.
 * 활동 데이터의 연료 이름만 조립 조건의 국내 연료명으로 바꾸고(표본은 `diesel` 같은 별칭을 쓴다) 부문·산정등급·사용량·단위는 사례 그대로 둔다.
 * 그래서 이름으로 계수를 고르는 길을 탄다: 조립된 계수의 부문과 매개변수별 산정등급이 사례의 것과 다르면 계수를 찾지 못해 실패한다.
 */
export function replayHandCases(
  cases: readonly HandCase[],
  spec: ReplaySpec,
  records: readonly FactorRecord[],
  mapping: AssemblyMapping,
  tables: ReplayTables,
): ReplayRow[] {
  const rows: ReplayRow[] = [];
  for (const c of cases) {
    if (c.synthetic) continue;
    const base = { caseId: c.id, title: c.title, sampleFactorId: c.factor.id, expectedCo2e: c.expected.co2e };
    const electricity = (spec.electricityReplays ?? []).filter((entry) => entry.sampleFactorId === c.factor.id);
    if (electricity.length > 0) {
      const onlyElectricity = electricity[0];
      if (electricity.length > 1 || onlyElectricity === undefined) {
        rows.push({ ...base, actualCo2e: null, outcome: { status: "not_replayed", reason: "구매 전력 조립 조건이 둘 이상 적혀 있다" } });
        continue;
      }
      const assembled = assemblePurchasedElectricityFactor(onlyElectricity.request, records, mapping);
      if (!assembled.ok) {
        rows.push({ ...base, actualCo2e: null, outcome: { status: "assembly_failed", failure: assembled.failure } });
        continue;
      }
      // 사례는 표본 계수를 id 로 고른다. 그 id 만 조립한 계수의 id 로 바꾼다 (사용량·단위·GWP 는 사례 그대로).
      const activity = { ...c.activity, factorId: assembled.value.factor.id };
      rows.push({ ...base, ...compare(activity, c.gwpSetId, c.expected, { factor: assembled.value.factor, assumptions: [] }, tables) });
      continue;
    }
    const replay = spec.replays.filter((entry) => entry.sampleFactorId === c.factor.id);
    const skipped = spec.notReplayed.find((entry) => entry.sampleFactorId === c.factor.id);
    const only = replay[0];
    if (only === undefined || replay.length > 1) {
      rows.push({
        ...base,
        actualCo2e: null,
        outcome: { status: "not_replayed", reason: skipped?.reason ?? (replay.length > 1 ? "조립 조건이 둘 이상 적혀 있다" : "조립 조건이 적혀 있지 않다") },
      });
      continue;
    }
    const assembled = assembleCombustionFactor(only.request, records, mapping);
    if (!assembled.ok) {
      rows.push({ ...base, actualCo2e: null, outcome: { status: "assembly_failed", failure: assembled.failure } });
      continue;
    }
    rows.push({ ...base, ...compare({ ...c.activity, fuel: only.request.fuel }, c.gwpSetId, c.expected, withAssumptions(assembled.value), tables) });
  }
  for (const extra of spec.assemblyCases) {
    const base = { caseId: extra.id, title: extra.title, sampleFactorId: null, expectedCo2e: extra.expected.co2e };
    const assembled = assembleCombustionFactor(extra.request, records, mapping);
    if (!assembled.ok) {
      rows.push({ ...base, actualCo2e: null, outcome: { status: "assembly_failed", failure: assembled.failure } });
      continue;
    }
    rows.push({ ...base, ...compare(extra.activity, extra.gwpSetId, extra.expected, withAssumptions(assembled.value), tables) });
  }
  return rows;
}

/** 전부 통과인가. 범위 밖이라 돌리지 않은 사례(`not_replayed`)는 통과로 치지 않고 따로 센다. */
export function replaySummary(rows: readonly ReplayRow[]): { total: number; matched: number; notReplayed: number; failed: number } {
  const matched = rows.filter((row) => row.outcome.status === "match").length;
  const notReplayed = rows.filter((row) => row.outcome.status === "not_replayed").length;
  return { total: rows.length, matched, notReplayed, failed: rows.length - matched - notReplayed };
}
