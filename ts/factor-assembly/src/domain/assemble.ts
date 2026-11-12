// 조립: 조립 조건에 맞는 낱줄을 계수 표에서 하나씩 골라 엔진의 연소 계수 한 건으로 묶는다.
// 순수 함수다. 파일을 읽지 않고(읽는 것은 adapters/files.ts), 수치를 만들지 않는다 (값은 전부 낱줄의 value_raw 에서 온다).
// 낱줄이 없거나 둘 이상이면 실패다. 비슷한 연료나 다른 부문·등급의 낱줄로 대신하지 않는다.

import type { CombustionFactor, Gas, ParameterTiers, Provenance, Tier } from "ghg-calc";
import { RULES, resolveTiers } from "./policy.js";
import { resolveSector } from "./sector.js";
import type {
  AssemblyFailure,
  AssemblyMapping,
  AssemblyRequest,
  AssemblyResult,
  Assumption,
  FactorRecord,
  FuelState,
  ParameterEvidence,
  ParameterName,
  Step,
} from "./types.js";
import { readValueRaw } from "./valueRaw.js";

/** 지침의 별표가 계수 표에서 갖는 출처 id. */
export const SOURCE_IDS = {
  oxidation: "kr-ets-byl06",
  ipccFactors: "kr-ets-byl10",
  ipccCalorific: "kr-ets-byl11",
  national: "kr-ets-byl12",
} as const;

/** 매개변수와 등급마다 어느 별표에서 값을 가져오는지 (별표 6 고정연소 각 절 「5. 매개변수별 관리기준」, 본문 제15조). */
const TABLE_RULES = {
  calorific: {
    1: "② 열량계수 Tier 1: 별표 11 의 IPCC 기본 발열량 (순발열량)",
    2: "② 열량계수 Tier 2: 별표 12 의 국가 고유 발열량 (순발열량)",
  },
  co2: {
    1: "③ 배출계수 Tier 1: 별표 10 의 IPCC 기본 배출계수",
    2: "③ 배출계수 Tier 2: 제15조제2항의 국가 고유 배출계수 (별표 12)",
  },
  nonCo2: "③ 배출계수 Tier 1: 별표 10 의 IPCC 기본 배출계수 (부문별). CH4·N2O 는 Tier 1 뿐이다",
  oxidation: "④ 산화계수: 별표 6 의 그 연료 상태 절에 적힌 값",
} as const;

const STATES: readonly FuelState[] = ["고체", "기체", "액체"];

/** 원문의 단위 표기 → 엔진의 단위 기호. 표에 없는 표기는 실패다 (추측해 읽지 않는다). */
const CALORIFIC_PER_UNIT: Record<string, string> = { L: "L", kg: "kg", "N㎥": "Nm3", Gg: "Gg" };
const ENERGY_UNITS: readonly string[] = ["MJ", "TJ"];

const fail = (failure: AssemblyFailure): { ok: false; failure: AssemblyFailure } => ({ ok: false, failure });
const isText = (value: unknown): value is string => typeof value === "string" && value.length > 0;

interface Picked {
  record: FactorRecord;
  value: string;
  evidence: ParameterEvidence;
}

interface NameUse {
  name: string;
  mapping: ParameterEvidence["nameMapping"];
}

/** 조건에 맞는 낱줄을 정확히 하나 고르고 값을 십진 문자열로 읽는다. */
function pickOne(
  parameter: ParameterName,
  tier: Tier,
  rule: string,
  records: readonly FactorRecord[],
  criteria: Record<string, string>,
  matches: (record: FactorRecord) => boolean,
  name: NameUse,
  guidelineVersion: string,
): Step<Picked> {
  const found = records.filter(matches);
  const record = found[0];
  if (record === undefined) return fail({ kind: "record_not_found", parameter, criteria });
  if (found.length > 1) return fail({ kind: "record_ambiguous", parameter, criteria, recordIds: found.map((r) => r.id) });
  if (record.source.version !== guidelineVersion) {
    return fail({ kind: "guideline_version_mismatch", recordId: record.id, recordVersion: record.source.version, guidelineVersion });
  }
  if (record.value_status !== "ok" || record.value_raw === null) {
    return fail({ kind: "record_value_missing", parameter, recordId: record.id, valueStatus: record.value_status });
  }
  const value = readValueRaw(record.value_raw);
  if (value === null) return fail({ kind: "invalid_value_raw", parameter, recordId: record.id, valueRaw: record.value_raw });
  return {
    ok: true,
    value: {
      record,
      value,
      evidence: {
        parameter,
        tier,
        rule,
        recordId: record.id,
        sourceId: record.source.id,
        sourceName: record.source.name,
        url: record.source.url,
        retrieved: record.source.retrieved,
        fileSha256: record.source.file_sha256,
        locator: record.source.locator,
        nameUsed: name.name,
        nameMapping: name.mapping,
        valueRaw: record.value_raw,
        value,
        unit: `${record.unit_numerator}/${record.unit_denominator}`,
        conditions: record.conditions,
      },
    },
  };
}

function noteAssumption(assumptions: Assumption[], detail: string, note: string | null, locator: string): void {
  if (!assumptions.some((a) => a.kind === "fuel_name" && a.detail === detail)) {
    assumptions.push({ kind: "fuel_name", detail, note: note ?? "", locator });
  }
}

/**
 * 국내 연료명을 별표 10·11 의 IPCC 연료명으로 바꾼다. 대응이 없거나 둘이면 실패다.
 * 대응표 항목에 `parameters` 가 있으면 그 매개변수에만 쓴다 (부생연료 1·2호는 CO2 만 등유·B-C유 계수를 쓴다. CH4·N2O 는 대응이 없어 실패한다).
 */
function ipccName(fuel: string, table: string, parameter: ParameterName, mapping: AssemblyMapping, assumptions: Assumption[]): Step<NameUse> {
  const found = mapping.fuel_names.filter(
    (entry) => entry.table === table && entry.domestic_names.includes(fuel) && (entry.parameters === undefined || entry.parameters.includes(parameter)),
  );
  const entry = found[0];
  if (entry === undefined) return fail({ kind: "fuel_name_not_mapped", fuel, table, parameter });
  if (found.length > 1) return fail({ kind: "fuel_name_ambiguous", fuel, table, ipccNames: found.map((e) => e.ipcc_name) });
  if (entry.basis === "assumed") {
    const only = entry.parameters === undefined ? "" : `, ${entry.parameters.join("·")} 만`;
    noteAssumption(assumptions, `${fuel} → ${entry.ipcc_name} (${table}${only})`, entry.note, entry.locator);
  }
  return { ok: true, value: { name: entry.ipcc_name, mapping: { domestic: fuel, ipcc: entry.ipcc_name, basis: entry.basis, locator: entry.locator } } };
}

/** 별표 12 배출계수 표에서 찾을 이름. 발열량 표와 이름이 다른 연료만 대응표(national_ef_names)에 있고, 없으면 그 이름 그대로다. */
function nationalEfName(fuel: string, mapping: AssemblyMapping, assumptions: Assumption[]): Step<NameUse> {
  const found = (mapping.national_ef_names ?? []).filter((entry) => entry.fuel === fuel);
  const entry = found[0];
  if (entry === undefined) return { ok: true, value: { name: fuel, mapping: null } };
  if (found.length > 1) return fail({ kind: "fuel_name_ambiguous", fuel, table: SOURCE_IDS.national, ipccNames: found.map((e) => e.ef_name) });
  if (entry.basis === "assumed") noteAssumption(assumptions, `${fuel} → ${entry.ef_name} (${SOURCE_IDS.national} 배출계수 표)`, entry.note, entry.locator);
  return {
    ok: true,
    value: { name: entry.ef_name, mapping: { domestic: fuel, ipcc: entry.ef_name, basis: entry.basis, locator: entry.locator, table: SOURCE_IDS.national } },
  };
}

function fuelStateOf(request: AssemblyRequest, mapping: AssemblyMapping, assumptions: Assumption[]): Step<{ value: FuelState; basis: "guideline" | "assumed" | "input"; locator: string | null }> {
  if (request.fuelState !== undefined) {
    if (!STATES.includes(request.fuelState)) {
      return fail({ kind: "invalid_input", field: "fuelState", value: String(request.fuelState), reason: "고체, 기체, 액체 중 하나다" });
    }
  }
  const found = mapping.fuel_states.filter((entry) => entry.fuel === request.fuel);
  const entry = found[0];
  if (request.fuelState !== undefined) {
    // 입력이 대응표와 어긋나면 어느 쪽도 고르지 않는다 (경유를 기체로 넣어 산화계수가 바뀌는 일을 막는다).
    const clash = found.find((item) => item.state !== request.fuelState);
    if (clash !== undefined) {
      return fail({ kind: "fuel_state_conflict", fuel: request.fuel, given: request.fuelState, mapped: clash.state, basis: clash.basis, locator: clash.locator });
    }
    return { ok: true, value: { value: request.fuelState, basis: "input", locator: null } };
  }
  if (entry === undefined || found.length > 1) {
    return fail({
      kind: "fuel_state_unknown",
      fuel: request.fuel,
      reason:
        found.length > 1
          ? "대응표에 그 연료의 상태가 둘 이상 적혀 있다"
          : "대응표에 그 연료의 상태(고체·기체·액체)가 없다. 조립 조건의 fuelState 로 직접 준다",
    });
  }
  if (entry.basis === "assumed") {
    assumptions.push({ kind: "fuel_state", detail: `${entry.fuel} → ${entry.state}`, note: entry.note ?? "", locator: entry.locator });
  }
  return { ok: true, value: { value: entry.state, basis: entry.basis, locator: entry.locator } };
}

function massUnitOf(parameter: ParameterName, gas: Gas, record: FactorRecord): Step<{ massUnit: string; perEnergyUnit: string }> {
  // 별표 10·12 의 배출계수 단위는 kg<기체>/TJ 다.
  if (record.unit_numerator !== `kg${gas}` || record.unit_denominator !== "TJ") {
    return fail({ kind: "unit_notation_unknown", parameter, recordId: record.id, unit: `${record.unit_numerator}/${record.unit_denominator}` });
  }
  return { ok: true, value: { massUnit: "kg", perEnergyUnit: "TJ" } };
}

function tierLabel(tiers: ParameterTiers): string {
  return `발열량 Tier ${tiers.calorific}, CO2 계수 Tier ${tiers.co2}, CH4·N2O 계수 Tier ${tiers.nonCo2}, 산화계수 Tier ${String(tiers.oxidation)}`;
}

/**
 * 계수 레코드 묶음과 대응표, 조립 조건으로 고정연소 계수 한 건을 조립한다.
 * 입력이 빠졌거나 낱줄을 하나로 정할 수 없으면 계수를 내놓지 않고 무엇이 문제인지를 돌려준다.
 */
export function assembleCombustionFactor(request: AssemblyRequest, records: readonly FactorRecord[], mapping: AssemblyMapping): AssemblyResult {
  const raw: unknown = request;
  if (typeof raw !== "object" || raw === null) return fail({ kind: "missing_input", field: "request", reason: "조립 조건이 없다" });
  if (!isText(request.fuel)) return fail({ kind: "missing_input", field: "fuel", reason: "국내 연료명이 없다" });
  const combustionType: unknown = request.combustionType;
  if (combustionType === undefined) return fail({ kind: "missing_input", field: "combustionType", reason: "연소 유형이 없다 (기본값 없음)" });
  if (combustionType !== "stationary") return fail({ kind: "unsupported_combustion_type", combustionType: String(combustionType) });
  if (request.industry === undefined) return fail({ kind: "missing_input", field: "industry", reason: "사업장의 부문도 업종 코드도 없다 (기본값 없음)" });
  if (request.tiers === undefined) return fail({ kind: "missing_input", field: "tiers", reason: "적용할 산정등급을 정하지 않았다 (기본값 없음)" });
  const powerGeneration: unknown = request.powerGeneration;
  if (powerGeneration !== undefined && typeof powerGeneration !== "boolean") {
    return fail({ kind: "invalid_input", field: "powerGeneration", value: String(powerGeneration), reason: "true 또는 false" });
  }

  const fuel = request.fuel;
  const version = mapping.guideline.version;
  const assumptions: Assumption[] = [];

  const state = fuelStateOf(request, mapping, assumptions);
  if (!state.ok) return state;
  const tiers = resolveTiers(request.tiers, state.value.value, mapping);
  if (!tiers.ok) return tiers;
  const sector = resolveSector(request.industry, mapping);
  if (!sector.ok) return sector;
  const t = tiers.value.values;
  const oxidationTier = t.oxidation;
  if (oxidationTier === null) throw new Error("unreachable: 고정연소의 산화계수 등급은 null 이 아니다");

  // 고체연료의 Tier 2 산화계수만 발전과 그 밖으로 갈린다. 그때 발전 여부가 없으면 어느 쪽인지 고르지 않는다.
  const needsFacilityUse = state.value.value === "고체" && oxidationTier === 2;
  if (needsFacilityUse && request.powerGeneration === undefined) {
    return fail({ kind: "missing_input", field: "powerGeneration", reason: "고체연료의 Tier 2 산화계수는 발전부문과 기타부문이 다르다. 발전 여부가 필요하다 (기본값 없음)" });
  }
  const oxidationSector = needsFacilityUse ? (request.powerGeneration === true ? "발전부문" : "기타부문") : null;

  const direct: NameUse = { name: fuel, mapping: null };

  // 발열량 (배출량 산정에는 순발열량: 본문 제2조 24호, 별표 12 비고 2)
  let calorificName = direct;
  if (t.calorific === 1) {
    const mapped = ipccName(fuel, SOURCE_IDS.ipccCalorific, "calorific", mapping, assumptions);
    if (!mapped.ok) return mapped;
    calorificName = mapped.value;
  }
  const calorificSource = t.calorific === 1 ? SOURCE_IDS.ipccCalorific : SOURCE_IDS.national;
  const calorific = pickOne(
    "calorific",
    t.calorific,
    TABLE_RULES.calorific[t.calorific as 1 | 2],
    records,
    { source: calorificSource, record_type: "heating_value", heating_value_basis: "net", name: calorificName.name },
    (r) => r.source.id === calorificSource && r.record_type === "heating_value" && r.heating_value_basis === "net" && r.name.original === calorificName.name,
    calorificName,
    version,
  );
  if (!calorific.ok) return calorific;
  const energyUnit = calorific.value.record.unit_numerator;
  const perUnit = CALORIFIC_PER_UNIT[calorific.value.record.unit_denominator];
  if (!ENERGY_UNITS.includes(energyUnit) || perUnit === undefined) {
    return fail({
      kind: "unit_notation_unknown",
      parameter: "calorific",
      recordId: calorific.value.record.id,
      unit: `${calorific.value.record.unit_numerator}/${calorific.value.record.unit_denominator}`,
    });
  }

  // 별표 10 의 이름 (CO2 Tier 1 과 CH4·N2O 가 쓴다). 매개변수마다 따로 찾는다 (대응이 일부 매개변수에만 있을 수 있다).
  const needIpccFactorName = (parameter: ParameterName): Step<NameUse> => ipccName(fuel, SOURCE_IDS.ipccFactors, parameter, mapping, assumptions);

  // CO2 배출계수 (Tier 2 는 별표 12 배출계수 표의 이름, Tier 1 은 별표 10 의 IPCC 이름)
  let co2Name = direct;
  {
    const mapped = t.co2 === 1 ? needIpccFactorName("CO2") : nationalEfName(fuel, mapping, assumptions);
    if (!mapped.ok) return mapped;
    co2Name = mapped.value;
  }
  const co2Source = t.co2 === 1 ? SOURCE_IDS.ipccFactors : SOURCE_IDS.national;
  const co2 = pickOne(
    "CO2",
    t.co2,
    TABLE_RULES.co2[t.co2 as 1 | 2],
    records,
    { source: co2Source, record_type: "emission_factor", gas: "CO2", gas_basis: "gas_mass", name: co2Name.name },
    (r) => r.source.id === co2Source && r.record_type === "emission_factor" && r.gas === "CO2" && r.gas_basis === "gas_mass" && r.name.original === co2Name.name,
    co2Name,
    version,
  );
  if (!co2.ok) return co2;

  // CH4·N2O 배출계수: 항상 별표 10 의 부문별 값
  const nonCo2: Picked[] = [];
  for (const [gas, condition] of [["CH4", sector.value.entry.ch4_condition], ["N2O", sector.value.entry.n2o_condition]] as const) {
    const mapped = needIpccFactorName(gas);
    if (!mapped.ok) return mapped;
    const name = mapped.value;
    const picked = pickOne(
      gas,
      1,
      TABLE_RULES.nonCo2,
      records,
      { source: SOURCE_IDS.ipccFactors, record_type: "emission_factor", gas, gas_basis: "gas_mass", name: name.name, 부문: condition },
      (r) =>
        r.source.id === SOURCE_IDS.ipccFactors &&
        r.record_type === "emission_factor" &&
        r.gas === gas &&
        r.gas_basis === "gas_mass" &&
        r.name.original === name.name &&
        r.conditions["부문"] === condition,
      name,
      version,
    );
    if (!picked.ok) return picked;
    nonCo2.push(picked.value);
  }

  // 산화계수
  const oxidationCriteria: Record<string, string> = {
    source: SOURCE_IDS.oxidation,
    record_type: "oxidation_factor",
    "연료 상태": state.value.value,
    산정등급: `Tier ${oxidationTier}`,
  };
  if (oxidationSector !== null) oxidationCriteria["산화계수 부문"] = oxidationSector;
  const oxidation = pickOne(
    "oxidation",
    oxidationTier,
    TABLE_RULES.oxidation,
    records,
    oxidationCriteria,
    (r) =>
      r.source.id === SOURCE_IDS.oxidation &&
      r.record_type === "oxidation_factor" &&
      r.conditions["연료 상태"] === state.value.value &&
      r.conditions["산정등급"] === `Tier ${oxidationTier}` &&
      (oxidationSector === null ? r.conditions["산화계수 부문"] === undefined : r.conditions["산화계수 부문"] === oxidationSector),
    { name: `고정연소 (${state.value.value}연료)`, mapping: null },
    version,
  );
  if (!oxidation.ok) return oxidation;
  if (oxidation.value.record.unit_numerator !== "무차원") {
    return fail({ kind: "unit_notation_unknown", parameter: "oxidation", recordId: oxidation.value.record.id, unit: oxidation.value.record.unit_numerator });
  }

  const emissionFactors: CombustionFactor["emissionFactors"] = [];
  for (const picked of [co2.value, ...nonCo2]) {
    const gas = picked.record.gas as Gas;
    const unit = massUnitOf(picked.evidence.parameter, gas, picked.record);
    if (!unit.ok) return unit;
    emissionFactors.push({ gas, value: picked.value, ...unit.value });
  }

  const parameters = [calorific.value, co2.value, ...nonCo2, oxidation.value].map((picked) => picked.evidence);
  const retrieved = parameters.map((p) => p.retrieved).sort().at(-1) ?? "";
  const assumedNote =
    assumptions.length === 0
      ? ""
      : ` 추정 대응 ${assumptions.length}건을 탔다 (지침에 없는 대응): ${assumptions.map((a) => a.detail).join("; ")}.`;
  const source: Provenance = {
    kind: "cited",
    title: `${mapping.guideline.name} (${version}): ${parameters.map((p) => `${p.parameter} ${p.valueRaw} ${p.unit} ← ${p.recordId}`).join(", ")}`,
    url: mapping.guideline.url,
    checkedOn: retrieved,
    locator: parameters.map((p) => `${p.sourceId} ${p.locator}`).join(" | "),
    note: `계수 조합기가 계수 표의 낱줄에서 조립했다. ${tierLabel(t)} (${tiers.value.basis === "group_minimum" ? `${String(tiers.value.group)} 그룹 최소기준` : "직접 지정"}), 부문 ${sector.value.entry.label}.${assumedNote}`,
  };

  const factor: CombustionFactor = {
    kind: "combustion",
    id: [
      "kr-ets",
      "stationary",
      fuel,
      sector.value.entry.key,
      `cv${t.calorific}_co2ef${t.co2}_ch4n2o${t.nonCo2}_ox${oxidationTier}`,
      ...(oxidationSector === null ? [] : [request.powerGeneration === true ? "power" : "other"]),
    ].join(":"),
    version,
    source,
    fuel,
    combustionType: "stationary",
    sector: sector.value.entry.key,
    parameterTiers: t,
    ...(oxidationSector === null ? {} : { facilityUse: request.powerGeneration === true ? ("power_generation" as const) : ("other" as const) }),
    calorific: { value: calorific.value.value, energyUnit, perUnit, basis: "net" },
    emissionFactors,
    oxidationFactor: oxidation.value.value,
  };

  return {
    ok: true,
    value: {
      factor,
      evidence: {
        guideline: mapping.guideline,
        fuelState: state.value,
        sector: sector.value.evidence,
        tiers: {
          values: t,
          basis: tiers.value.basis,
          group: tiers.value.group,
          locators: [...tiers.value.locators, RULES.oxidationFollowsCo2],
        },
        parameters,
        assumptions,
      },
    },
  };
}
