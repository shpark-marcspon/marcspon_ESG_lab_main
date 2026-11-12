// 구매 전력: 계수 표의 전력배출계수 낱줄을 조립 조건대로 골라 엔진의 구매 에너지 계수(PurchasedEnergyFactor) 한 건으로 묶는다.
// 순수 함수다. 수치를 만들지 않는다 (값은 전부 낱줄의 value_raw 에서 온다).
//
// 어느 계수를 쓸지(지침 별표 6 <표-69> 인지 온실가스종합정보센터(GIR) 공표 값인지, 어느 판·기간·발전단/소비단·산정기준인지,
// 기체별 값인지 CO2 환산 값인지)는 전부 조립 조건이다. 기본값이 없고, 빠지면 조립하지 않는다.
// 지침 Tier 2 문장의 단서(「센터에서 확인하여 지침에 수록된 경우 그 값을 적용」)로 GIR 값이 표-69 를 대신하는지는 코드가 판단하지 않는다.

import { calculateRow, type ActivityRow, type GwpSet, type PurchasedEnergyFactor, type Provenance, type UnitConversion } from "ghg-calc";
import { SOURCE_IDS } from "./assemble.js";
import type { AssemblyFailure, AssemblyMapping, FactorRecord } from "./types.js";
import { readValueRaw } from "./valueRaw.js";

export type ElectricitySource = "guideline_table69" | "gir_published";
export type ElectricityGasForm = "per_gas" | "co2e";
export type ElectricityBoundary = "generation" | "consumption";
export type ElectricityMethodology = "ipcc_2006" | "ipcc_1996_legacy";
export type ElectricityPeriod =
  | { kind: "single_year"; year: number }
  | { kind: "three_year_average"; from: number; to: number }
  /** 공표문의 표에 기간이 적혀 있지 않은 판 (GIR 2024 공표). 그 판을 고를 때만 쓴다. */
  | { kind: "not_stated" };

export interface GirSelection {
  /** 공표 판. 2024 → 계수 표의 출처 gir-2024, 2025 → gir-2025. */
  edition: number;
  period: ElectricityPeriod;
  /** 발전단 / 소비단 */
  boundary: ElectricityBoundary;
  /** 산정기준: ’06 IPCC 지침(본표) / ’96 IPCC 지침 및 기존 에너지수급통계(기존 공표 산정기준, 2025 공표의 주석 표) */
  methodology: ElectricityMethodology;
}

/** 구매 전력 조립 조건. 전부 고객사나 사업장 설정에서 오는 입력이다 (MARC-ISSUE-20 15·17번 답 대기). */
export interface ElectricityRequest {
  energyType: "electricity";
  source: ElectricitySource;
  gasForm: ElectricityGasForm;
  /** source 가 gir_published 일 때만, 그때는 전부 필요하다. 표-69 에는 판·기간 선택·발전단/소비단·산정기준 구분이 없어 주면 실패다. */
  gir?: GirSelection;
}

export type ElectricityGas = "CO2" | "CH4" | "N2O" | "CO2e";

export interface ElectricityParameterEvidence {
  gas: ElectricityGas;
  recordId: string;
  sourceId: string;
  sourceName: string;
  sourceVersion: string;
  url: string;
  retrieved: string;
  fileSha256: string;
  locator: string;
  valueRaw: string;
  value: string;
  unit: string;
  conditions: Record<string, string>;
}

export interface ElectricityEvidence {
  /** 조립 조건을 그대로 되적는다. 출처 선택은 입력이다. */
  request: ElectricityRequest;
  /**
   * co2e 계수에 이미 들어 있는 GWP (공표문이 적은 것). 엔진은 CO2e 계수에 GWP 를 곱하지 않으므로, 계산의 GWP 세트와 다를 수 있다.
   * per_gas 이면 null (GWP 는 계산할 때 고른 세트로 곱한다).
   */
  co2eGwp: string | null;
  sourceChoice: { value: ElectricitySource; basis: "input"; note: string };
  /** 그 출처를 고른 낱줄이 어디서 왔는지 (지침의 자리 또는 공표문). */
  rule: string;
  parameters: ElectricityParameterEvidence[];
}

export type ElectricityFailure =
  | AssemblyFailure
  | { kind: "record_condition_unknown"; recordId: string; field: string; value: string };

export type ElectricityResult =
  | { ok: true; value: { factor: PurchasedEnergyFactor; evidence: ElectricityEvidence } }
  | { ok: false; failure: ElectricityFailure };

/** GIR 공표 판 → 계수 표의 출처 id. */
export const GIR_EDITIONS: Readonly<Record<number, string>> = { 2024: "gir-2024", 2025: "gir-2025" };

/** 산정기준 입력 → 계수 레코드의 conditions.산정기준 (py/factors/loaders/kr_gir.py 의 BASIS_2006, BASIS_1996 과 같은 글자). */
const METHODOLOGY: Record<ElectricityMethodology, string> = {
  ipcc_2006: "’06 IPCC 지침",
  ipcc_1996_legacy: "’96 IPCC 지침 및 기존 에너지수급통계 (기존 공표 산정기준)",
};
const BOUNDARY: Record<ElectricityBoundary, string> = { generation: "발전단", consumption: "소비단" };

/**
 * GIR 공표의 CO2 환산 계수에 들어 있는 GWP (공표문 주석의 글. 값이 아니라 어느 평가보고서인가).
 * 2025 공고 주석 1)의 *: 「‘06 IPCC 지침 기준 계수는 IPCC 5차 평가보고서(AR5), ’96 IPCC 지침 기준 계수는 IPCC 2차 평가보고서(AR2)의 GWP 적용」.
 * 2024 공고 주석 1)은 「GWP 를 적용하여 합산한 계수」 라고만 쓴다.
 */
const CO2E_GWP: Record<string, Record<ElectricityMethodology, string>> = {
  "gir-2025": {
    ipcc_2006: "IPCC 5차 평가보고서(AR5) GWP (GIR 2025 공고 전력배출계수 표 주석 1)의 *)",
    ipcc_1996_legacy: "IPCC 2차 평가보고서(AR2) GWP (GIR 2025 공고 전력배출계수 표 주석 1)의 *)",
  },
  "gir-2024": {
    ipcc_2006: "공표문에 적혀 있지 않다 (GIR 2024 공고 주석 1)은 「지구온난화지수(GWP)를 적용하여 합산한 계수」 라고만 쓴다)",
    ipcc_1996_legacy: "공표문에 적혀 있지 않다",
  },
};

/** 표-69 의 자리와 규칙 (지침 원문 위치. 값이 아니다). */
export const TABLE69 = {
  sourceId: SOURCE_IDS.oxidation, // 별표 6 (kr-ets-byl06)
  category: "외부에서 공급된 전기 사용",
  rule: "별표 6 「39. 외부에서 공급된 전기 사용」 5. 매개변수별 관리기준 ② 배출계수 Tier 2: 전력배출계수는 <표-69> 의 3개년도('14∼'16년) 평균값을 적용한다. 향후 한국전력거래소에서 제공하는 전력배출계수를 온실가스종합정보센터에서 확인하여 지침에 수록된 경우 그 값을 적용한다.",
} as const;

/** 계수 표의 단위 분자 → (질량 단위, 기체). 표에 없는 표기는 실패다. */
const MASS_UNITS: Record<string, { massUnit: string; gas: ElectricityGas }> = {
  tCO2: { massUnit: "t", gas: "CO2" },
  kgCH4: { massUnit: "kg", gas: "CH4" },
  kgN2O: { massUnit: "kg", gas: "N2O" },
  "t CO2": { massUnit: "t", gas: "CO2" },
  "kg CH4": { massUnit: "kg", gas: "CH4" },
  "kg N2O": { massUnit: "kg", gas: "N2O" },
  "t CO2-eq": { massUnit: "t", gas: "CO2e" },
  "t CO2eq": { massUnit: "t", gas: "CO2e" },
};

const SINGLE_YEAR = /^[‘’'](\d{2})년$/;
const AVERAGE = /^[‘’'](\d{2})-[‘’'](\d{2})년 평균$/;

/** 공표문이 적는 두 자리 연도 (‘23년 → "23"). 숫자로 바꾸지 않고 글자로 견준다. */
type RecordPeriod = { kind: "single_year"; yy: string } | { kind: "three_year_average"; from: string; to: string } | { kind: "not_stated" };

/** 레코드의 conditions.기간 → 기간. 기간 조건이 없으면 not_stated. 읽을 수 없으면 null. */
function periodOf(record: FactorRecord): RecordPeriod | null {
  const raw = record.conditions["기간"];
  if (raw === undefined) return { kind: "not_stated" };
  const single = SINGLE_YEAR.exec(raw);
  if (single) return { kind: "single_year", yy: single[1] as string };
  const average = AVERAGE.exec(raw);
  if (average) return { kind: "three_year_average", from: average[1] as string, to: average[2] as string };
  return null;
}

/** 입력 연도(2000~2099, 검사됨) → 두 자리 글자. */
const yy = (year: number): string => String(year).slice(2);

function samePeriod(record: RecordPeriod, wanted: ElectricityPeriod): boolean {
  if (record.kind === "single_year") return wanted.kind === "single_year" && record.yy === yy(wanted.year);
  if (record.kind === "three_year_average") return wanted.kind === "three_year_average" && record.from === yy(wanted.from) && record.to === yy(wanted.to);
  return wanted.kind === "not_stated";
}

const periodText = (p: ElectricityPeriod): string =>
  p.kind === "single_year" ? `${p.year}` : p.kind === "three_year_average" ? `${p.from}-${p.to}평균` : "기간표시없음";

const fail = (failure: ElectricityFailure): { ok: false; failure: ElectricityFailure } => ({ ok: false, failure });
const isYear = (value: unknown): value is number => typeof value === "number" && Number.isInteger(value) && value >= 2000 && value <= 2099;

type Checked = { request: ElectricityRequest; gir: GirSelection | null };

function checkRequest(raw: unknown): { ok: true; value: Checked } | { ok: false; failure: ElectricityFailure } {
  if (typeof raw !== "object" || raw === null) return fail({ kind: "missing_input", field: "request", reason: "조립 조건이 없다" });
  const r = raw as Record<string, unknown>;
  if (r.energyType === undefined) return fail({ kind: "missing_input", field: "energyType", reason: "에너지 종류가 없다 (기본값 없음)" });
  if (r.energyType !== "electricity") return fail({ kind: "invalid_input", field: "energyType", value: String(r.energyType), reason: "구매 전력(electricity)만 조립한다. 열(스팀)은 범위 밖이다" });
  if (r.source === undefined) {
    return fail({ kind: "missing_input", field: "source", reason: "전력배출계수의 출처(지침 표-69 / GIR 공표)를 정하지 않았다. 기본값이 없다 (MARC-ISSUE-20 15·17번)" });
  }
  if (r.source !== "guideline_table69" && r.source !== "gir_published") {
    return fail({ kind: "invalid_input", field: "source", value: String(r.source), reason: "guideline_table69 또는 gir_published" });
  }
  if (r.gasForm === undefined) return fail({ kind: "missing_input", field: "gasForm", reason: "기체별 값(per_gas)인지 CO2 환산 값(co2e)인지 정하지 않았다 (기본값 없음)" });
  if (r.gasForm !== "per_gas" && r.gasForm !== "co2e") {
    return fail({
      kind: "invalid_input",
      field: "gasForm",
      value: JSON.stringify(r.gasForm),
      reason: "per_gas(CO2·CH4·N2O 셋, GWP 는 계산할 때 곱한다) 또는 co2e(공표된 CO2 환산 값 하나, GWP 를 다시 곱하지 않는다) 중 하나다. 둘을 섞지 않는다",
    });
  }
  if (r.source === "guideline_table69") {
    if (r.gir !== undefined) {
      return fail({ kind: "invalid_input", field: "gir", value: JSON.stringify(r.gir), reason: "표-69 에는 공표 판·기간 선택·발전단/소비단·산정기준 구분이 없다. gir 은 GIR 공표 값을 고를 때만 준다" });
    }
    return { ok: true, value: { request: { energyType: "electricity", source: r.source, gasForm: r.gasForm }, gir: null } };
  }
  if (typeof r.gir !== "object" || r.gir === null) return fail({ kind: "missing_input", field: "gir", reason: "GIR 공표 값의 판·기간·발전단/소비단·산정기준이 없다 (기본값 없음)" });
  const g = r.gir as Record<string, unknown>;
  for (const field of ["edition", "period", "boundary", "methodology"] as const) {
    if (g[field] === undefined) return fail({ kind: "missing_input", field: `gir.${field}`, reason: "GIR 공표 값을 고르는 조건이 빠졌다 (기본값 없음)" });
  }
  if (!isYear(g.edition) || GIR_EDITIONS[g.edition] === undefined) {
    return fail({ kind: "invalid_input", field: "gir.edition", value: String(g.edition), reason: `계수 표에 있는 공표 판은 ${Object.keys(GIR_EDITIONS).join(", ")} 이다` });
  }
  if (g.boundary !== "generation" && g.boundary !== "consumption") {
    return fail({ kind: "invalid_input", field: "gir.boundary", value: String(g.boundary), reason: "generation(발전단) 또는 consumption(소비단)" });
  }
  if (g.methodology !== "ipcc_2006" && g.methodology !== "ipcc_1996_legacy") {
    return fail({ kind: "invalid_input", field: "gir.methodology", value: String(g.methodology), reason: "ipcc_2006 또는 ipcc_1996_legacy" });
  }
  const p = g.period as Record<string, unknown> | null;
  let period: ElectricityPeriod;
  if (typeof p !== "object" || p === null) return fail({ kind: "invalid_input", field: "gir.period", value: String(p), reason: "기간은 객체다" });
  if (p.kind === "single_year" && isYear(p.year)) period = { kind: "single_year", year: p.year };
  else if (p.kind === "three_year_average" && isYear(p.from) && isYear(p.to) && p.to - p.from === 2) period = { kind: "three_year_average", from: p.from, to: p.to };
  else if (p.kind === "not_stated") period = { kind: "not_stated" };
  else {
    return fail({
      kind: "invalid_input",
      field: "gir.period",
      value: JSON.stringify(p),
      reason: "{kind: single_year, year} / {kind: three_year_average, from, to (3개년)} / {kind: not_stated} 중 하나다 (연도는 2000~2099 의 네 자리 정수)",
    });
  }
  const gir: GirSelection = { edition: g.edition, period, boundary: g.boundary, methodology: g.methodology };
  return { ok: true, value: { request: { energyType: "electricity", source: r.source, gasForm: r.gasForm, gir }, gir } };
}

/**
 * 구매 전력 계수 한 건을 조립한다. 낱줄은 기체마다 정확히 하나여야 한다 (없거나 둘이면 실패).
 * per_gas 는 CO2·CH4·N2O 의 질량 계수 셋, co2e 는 공표된 CO2 환산 계수 하나다. 엔진은 CO2e 계수에 GWP 를 곱하지 않는다.
 */
export function assemblePurchasedElectricityFactor(raw: ElectricityRequest, records: readonly FactorRecord[], mapping: AssemblyMapping): ElectricityResult {
  const checked = checkRequest(raw);
  if (!checked.ok) return checked;
  const { request, gir } = checked.value;
  const gases: ElectricityGas[] = request.gasForm === "co2e" ? ["CO2e"] : ["CO2", "CH4", "N2O"];
  const gasBasis = request.gasForm === "co2e" ? "co2e" : "gas_mass";
  const sourceId = gir === null ? TABLE69.sourceId : (GIR_EDITIONS[gir.edition] as string);

  // 후보: 그 출처의 전력배출계수 낱줄. GIR 은 기간을 읽을 수 없는 낱줄이 있으면 고르기 전에 실패한다 (추측해 읽지 않는다).
  // 표-69 는 한 행(3개년 평균 '14∼'16)뿐이라 기간을 고르지 않는다.
  const pool = records.filter(
    (r) =>
      r.source.id === sourceId &&
      r.record_type === "emission_factor" &&
      (gir === null ? r.category_path[0] === TABLE69.category : r.category_path.includes("전력배출계수")),
  );
  if (gir !== null) {
    for (const r of pool) {
      if (periodOf(r) === null) return fail({ kind: "record_condition_unknown", recordId: r.id, field: "기간", value: r.conditions["기간"] ?? "" });
    }
  }

  const parameters: ElectricityParameterEvidence[] = [];
  const emissionFactors: PurchasedEnergyFactor["emissionFactors"] = [];
  for (const gas of gases) {
    const criteria: Record<string, string> = { source: sourceId, record_type: "emission_factor", gas, gas_basis: gasBasis };
    if (gir !== null) {
      criteria["기준"] = BOUNDARY[gir.boundary];
      criteria["산정기준"] = METHODOLOGY[gir.methodology];
      criteria["기간"] = periodText(gir.period);
    }
    const found = pool.filter(
      (r) =>
        r.gas === gas &&
        r.gas_basis === gasBasis &&
        (gir === null ||
          (r.conditions["기준"] === BOUNDARY[gir.boundary] && r.conditions["산정기준"] === METHODOLOGY[gir.methodology] && samePeriod(periodOf(r) as RecordPeriod, gir.period))),
    );
    const record = found[0];
    if (record === undefined) return fail({ kind: "record_not_found", parameter: gas, criteria });
    if (found.length > 1) return fail({ kind: "record_ambiguous", parameter: gas, criteria, recordIds: found.map((r) => r.id) });
    if (gir === null && record.source.version !== mapping.guideline.version) {
      return fail({ kind: "guideline_version_mismatch", recordId: record.id, recordVersion: record.source.version, guidelineVersion: mapping.guideline.version });
    }
    if (record.value_status !== "ok" || record.value_raw === null) return fail({ kind: "record_value_missing", parameter: gas, recordId: record.id, valueStatus: record.value_status });
    const value = readValueRaw(record.value_raw);
    if (value === null) return fail({ kind: "invalid_value_raw", parameter: gas, recordId: record.id, valueRaw: record.value_raw });
    const unit = MASS_UNITS[record.unit_numerator];
    if (unit === undefined || unit.gas !== gas || record.unit_denominator !== "MWh") {
      return fail({ kind: "unit_notation_unknown", parameter: gas, recordId: record.id, unit: `${record.unit_numerator}/${record.unit_denominator}` });
    }
    emissionFactors.push({ gas, value, massUnit: unit.massUnit });
    parameters.push({
      gas,
      recordId: record.id,
      sourceId: record.source.id,
      sourceName: record.source.name,
      sourceVersion: record.source.version,
      url: record.source.url,
      retrieved: record.source.retrieved,
      fileSha256: record.source.file_sha256,
      locator: record.source.locator,
      valueRaw: record.value_raw,
      value,
      unit: `${record.unit_numerator}/${record.unit_denominator}`,
      conditions: record.conditions,
    });
  }
  // 섞이지 않았는지 한 번 더 (엔진도 CO2e 하나 또는 CO2·CH4·N2O 셋만 받는다)
  const listed = emissionFactors.map((e) => e.gas).join(",");
  if (listed !== (request.gasForm === "co2e" ? "CO2e" : "CO2,CH4,N2O")) throw new Error(`unreachable: 기체 구성이 ${listed}`);

  const first = parameters[0] as ElectricityParameterEvidence;
  const variant =
    gir === null
      ? `${TABLE69.sourceId}:표-69:2014-2016평균:${request.gasForm}`
      : `${sourceId}:${BOUNDARY[gir.boundary]}:${periodText(gir.period)}:${gir.methodology}:${request.gasForm}`;
  const co2eGwp = request.gasForm === "co2e" && gir !== null ? (CO2E_GWP[sourceId]?.[gir.methodology] ?? "공표문에서 확인하지 않았다") : null;
  const gwpNote = co2eGwp === null ? "" : ` 이 CO2 환산 계수에는 GWP 가 이미 들어 있다: ${co2eGwp}. 엔진은 GWP 를 다시 곱하지 않으므로 계산에 고른 GWP 세트와 다를 수 있다.`;
  const sourceNote =
    gir === null
      ? "지침 별표 6 <표-69> 를 쓴 것은 조립 조건(입력)이다. 같은 문장의 단서(센터가 확인해 지침에 수록한 값)로 GIR 공표 값이 이를 대신하는지는 판단하지 않았다."
      : "온실가스종합정보센터(GIR) 공표 값을 쓴 것은 조립 조건(입력)이다. 이 값이 지침 별표 6 Tier 2 문장의 「센터에서 확인하여 지침에 수록된 경우」 에 해당하는지는 판단하지 않았다.";
  const source: Provenance = {
    kind: "cited",
    title: `${first.sourceName} (${first.sourceVersion}): ${parameters.map((p) => `${p.gas} ${p.valueRaw} ${p.unit} ← ${p.recordId}`).join(", ")}`,
    url: gir === null ? mapping.guideline.url : first.url,
    checkedOn: parameters.map((p) => p.retrieved).sort().at(-1) ?? "",
    locator: parameters.map((p) => `${p.sourceId} ${p.locator}`).join(" | "),
    note: `계수 조합기가 계수 표의 낱줄에서 조립했다. ${sourceNote}${gwpNote}`,
  };
  const factor: PurchasedEnergyFactor = {
    kind: "purchased_energy",
    id: `electricity:${variant}`,
    version: first.sourceVersion,
    source,
    energyType: "electricity",
    variant,
    ...(gir !== null && gir.period.kind === "single_year" ? { year: gir.period.year } : {}),
    perUnit: "MWh",
    emissionFactors,
  };
  return {
    ok: true,
    value: {
      factor,
      evidence: {
        request,
        co2eGwp,
        sourceChoice: { value: request.source, basis: "input", note: sourceNote },
        rule: gir === null ? TABLE69.rule : `${first.sourceName} (${first.sourceVersion}) 공표 표에서 입력한 판·기간·${BOUNDARY[gir.boundary]}·산정기준의 낱줄`,
        parameters,
      },
    },
  };
}

// ---------------------------------------------------------------- 비교 (고객 질문의 근거. 기대값이 아니다)

/** 비교할 선택지: 표-69, GIR 2025 공표(단년 2023·3년 평균 2021~2023 × 발전단·소비단 × 산정기준 둘), GIR 2024 공표(기간 표시 없음 × 발전단·소비단). 각각 기체별·CO2e. */
export function electricityOptions(): ElectricityRequest[] {
  const options: ElectricityRequest[] = [{ energyType: "electricity", source: "guideline_table69", gasForm: "per_gas" }];
  const gir = (edition: number, period: ElectricityPeriod, boundary: ElectricityBoundary, methodology: ElectricityMethodology, gasForm: ElectricityGasForm): ElectricityRequest => ({
    energyType: "electricity",
    source: "gir_published",
    gasForm,
    gir: { edition, period, boundary, methodology },
  });
  for (const methodology of ["ipcc_2006", "ipcc_1996_legacy"] as const) {
    for (const period of [{ kind: "single_year", year: 2023 }, { kind: "three_year_average", from: 2021, to: 2023 }] as const) {
      for (const boundary of ["generation", "consumption"] as const) {
        for (const gasForm of ["per_gas", "co2e"] as const) options.push(gir(2025, period, boundary, methodology, gasForm));
      }
    }
  }
  for (const boundary of ["generation", "consumption"] as const) {
    for (const gasForm of ["per_gas", "co2e"] as const) options.push(gir(2024, { kind: "not_stated" }, boundary, "ipcc_2006", gasForm));
  }
  return options;
}

export interface ElectricityComparisonRow {
  request: ElectricityRequest;
  /** CO2e 계수에 이미 들어 있는 GWP (공표문의 글). 기체별이면 null. */
  co2eGwp: string | null;
  factorId: string | null;
  recordIds: string[];
  factorValues: string[];
  /** 엔진 결과 (tCO2eq, 반올림 없음). 조립이나 계산에 실패하면 null 과 실패. */
  co2e: string | null;
  gases: Array<{ gas: string; mass: string; gwp: string | null; co2e: string }>;
  failure: unknown;
}

/** 같은 사용량을 선택지마다 조립·계산한다. 순수 함수다. */
export function electricityComparison(
  amount: string,
  unit: string,
  gwpSetId: string,
  records: readonly FactorRecord[],
  mapping: AssemblyMapping,
  tables: { unitConversions: UnitConversion[]; gwpSets: GwpSet[] },
  options: readonly ElectricityRequest[] = electricityOptions(),
): ElectricityComparisonRow[] {
  return options.map((request) => {
    const assembled = assemblePurchasedElectricityFactor(request, records, mapping);
    if (!assembled.ok) return { request, co2eGwp: null, factorId: null, recordIds: [], factorValues: [], co2e: null, gases: [], failure: assembled.failure };
    const { factor, evidence } = assembled.value;
    const row: ActivityRow = { id: factor.id, orgUnit: "comparison", period: "-", sourceType: "purchased_electricity", factorId: factor.id, amount, unit };
    const result = calculateRow(row, { ...tables, factors: [factor] }, { gwpSetId });
    const base = { request, co2eGwp: evidence.co2eGwp, factorId: factor.id, recordIds: evidence.parameters.map((p) => p.recordId), factorValues: evidence.parameters.map((p) => `${p.gas} ${p.valueRaw} ${p.unit}`) };
    if (result.status !== "ok") return { ...base, co2e: null, gases: [], failure: result.failure };
    return { ...base, co2e: result.co2e, gases: result.gases.map((g) => ({ gas: g.gas, mass: g.mass, gwp: g.gwp, co2e: g.co2e })), failure: null };
  });
}
