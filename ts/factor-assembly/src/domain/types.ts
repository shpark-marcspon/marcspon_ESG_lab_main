// 조합기가 받는 값(계수 레코드, 대응표, 조립 조건)과 내놓는 값(조립된 계수와 근거, 또는 실패)의 형식.
// 계수 레코드의 정본은 `schemas/factor.schema.json`, 대응표의 정본은 `schemas/factor-mapping.schema.json` 이고
// 여기 타입은 조합기가 읽는 필드만 옮긴 부분 사본이다 (파일을 읽을 때 `adapters/files.ts` 의 zod 스키마로 검증한다).

import type { CombustionFactor, ParameterTiers, Tier } from "ghg-calc";

export type FuelState = "고체" | "기체" | "액체";
export type FacilityGroup = "A" | "B" | "C";
export type MappingBasis = "guideline" | "assumed";

/** 계수 표의 한 줄 (조합기가 읽는 필드). */
export interface FactorRecord {
  id: string;
  record_type: string;
  name: { original: string };
  category_path: string[];
  /** 출처 표기 그대로의 문자열. 조합기는 `value`(number)를 읽지 않는다. */
  value_raw: string | null;
  value_status: string;
  unit_numerator: string;
  unit_denominator: string;
  gas: string;
  gas_basis: string;
  heating_value_basis: "net" | "gross" | null;
  conditions: Record<string, string>;
  source: {
    id: string;
    name: string;
    url: string;
    version: string;
    retrieved: string;
    file_name: string;
    file_sha256: string;
    locator: string;
  };
  synthetic: boolean;
}

export interface SectorEntry {
  key: string;
  label: string;
  ksic_divisions: string[];
  ch4_condition: string;
  n2o_condition: string;
  source_id: string;
  locator: string;
}

export interface SectorOverlap {
  division: string;
  sectors: string[];
  exceptions: Array<{ code_prefix: string; sector: string; raw: string }>;
  source_id: string;
  locator: string;
}

/** 추정의 근거 문장 자리 (적재 때 원문의 그 쪽에 있는지 확인됐다). */
export interface MappingEvidence {
  source_id: string;
  locator: string;
  quote: string;
}

export interface FuelNameEntry {
  table: string;
  ipcc_name: string;
  domestic_names: string[];
  basis: MappingBasis;
  note: string | null;
  source_id: string;
  locator: string;
  /** 있으면 이 대응은 이 매개변수에만 쓰인다 (부생연료 1·2호의 CO2 Tier 1). 없으면 그 표의 모든 매개변수. */
  parameters?: string[] | undefined;
  evidence?: MappingEvidence[] | undefined;
}

/** 별표 12 안에서 발열량 표의 이름 → 배출계수 표의 이름 (두 표가 같은 연료를 다르게 적을 때). */
export interface NationalNameEntry {
  fuel: string;
  ef_name: string;
  basis: MappingBasis;
  note: string | null;
  source_id: string;
  locator: string;
}

export interface FuelStateEntry {
  fuel: string;
  state: FuelState;
  basis: MappingBasis;
  quote: string;
  note: string | null;
  source_id: string;
  locator: string;
}

export interface TierMinimumEntry {
  activity: string;
  fuel_state: FuelState;
  parameter: string;
  group: FacilityGroup;
  tier: number;
  source_id: string;
  locator: string;
}

/** 조립용 대응표 (`data/factors/mappings.kr-ets.json`). */
export interface AssemblyMapping {
  guideline: { name: string; version: string; url: string };
  sectors: SectorEntry[];
  sector_overlaps: SectorOverlap[];
  fuel_names: FuelNameEntry[];
  /** 없는 대응표(MARC-TICK-17 의 것)도 읽는다. 없으면 별표 12 의 두 표가 같은 이름을 쓰는 연료만 조립된다. */
  national_ef_names?: NationalNameEntry[] | undefined;
  fuel_states: FuelStateEntry[];
  tier_minimums: TierMinimumEntry[];
}

/** 사업장의 업종. 부문을 직접 주거나 한국표준산업분류 코드(중분류 두 자리 이상)를 준다. */
export type IndustryInput = { sector: string } | { ksicCode: string };

/**
 * 적용할 산정등급. 기본값이 없다.
 * - explicit: 매개변수별로 직접 준다 (CH4·N2O 는 지침에 Tier 1 뿐이라 받지 않는다).
 * - group_minimum: 시설 규모 그룹의 최소 적용기준(별표 5)을 따른다.
 */
export type TierPolicy =
  | { kind: "explicit"; calorific: Tier; co2: Tier; oxidation: Tier }
  | { kind: "group_minimum"; group: FacilityGroup };

/** 조립 조건. 전부 고객사나 사업장 설정에서 오는 입력이고, 빠지면 조립하지 않는다. */
export interface AssemblyRequest {
  /** 국내 연료명 (별표 12 의 이름). */
  fuel: string;
  combustionType: "stationary";
  industry: IndustryInput;
  tiers: TierPolicy;
  /** 대응표에 그 연료의 상태가 없을 때만 필요하다. 대응표에 있는 연료에 다른 상태를 주면 실패다 (같은 값이면 입력을 근거로 쓴다). */
  fuelState?: FuelState;
  /** 고체연료에 Tier 2 산화계수를 쓸 때만 필요하다 (발전 0.99 대 그 밖 0.98). */
  powerGeneration?: boolean;
}

/** 조립하는 값의 자리. `CO2e` 는 구매 전력의 CO2 환산 계수다 (MARC-TICK-18). */
export type ParameterName = "calorific" | "CO2" | "CH4" | "N2O" | "oxidation" | "CO2e";

export type AssemblyFailure =
  | { kind: "missing_input"; field: string; reason: string }
  | { kind: "invalid_input"; field: string; value: string; reason: string }
  | { kind: "unsupported_combustion_type"; combustionType: string }
  | { kind: "fuel_state_unknown"; fuel: string; reason: string }
  | { kind: "fuel_state_conflict"; fuel: string; given: FuelState; mapped: FuelState; basis: MappingBasis; locator: string }
  | { kind: "sector_not_defined"; ksicCode: string; division: string }
  | { kind: "sector_ambiguous"; ksicCode: string; division: string; candidates: string[]; notes: string[]; locator: string }
  | { kind: "tier_combination_invalid"; reason: string; locator: string }
  | { kind: "tier_requires_site_values"; parameter: string; tier: number; reason: string }
  | { kind: "tier_minimum_not_found"; fuelState: FuelState; group: string; parameter: string; found: number }
  | { kind: "fuel_name_not_mapped"; fuel: string; table: string; parameter: ParameterName }
  | { kind: "fuel_name_ambiguous"; fuel: string; table: string; ipccNames: string[] }
  | { kind: "record_not_found"; parameter: ParameterName; criteria: Record<string, string> }
  | { kind: "record_ambiguous"; parameter: ParameterName; criteria: Record<string, string>; recordIds: string[] }
  | { kind: "record_value_missing"; parameter: ParameterName; recordId: string; valueStatus: string }
  | { kind: "invalid_value_raw"; parameter: ParameterName; recordId: string; valueRaw: string }
  | { kind: "unit_notation_unknown"; parameter: ParameterName; recordId: string; unit: string }
  | { kind: "guideline_version_mismatch"; recordId: string; recordVersion: string; guidelineVersion: string };

/** 지침에 없는 추정 대응을 탄 자리. 하나라도 있으면 조립 결과는 그만큼 추정에 기댄다. */
export interface Assumption {
  kind: "fuel_name" | "fuel_state";
  detail: string;
  note: string;
  locator: string;
}

/** 매개변수 하나가 어느 낱줄에서, 어느 규칙으로 왔는지. */
export interface ParameterEvidence {
  parameter: ParameterName;
  tier: Tier;
  /** 그 등급에서 어느 표를 쓰는지 정한 지침의 자리. */
  rule: string;
  recordId: string;
  sourceId: string;
  sourceName: string;
  url: string;
  retrieved: string;
  fileSha256: string;
  locator: string;
  /** 낱줄을 찾은 이름. 국내 이름 그대로이거나 대응표로 바꾼 IPCC 이름(또는 별표 12 배출계수 표의 이름). */
  nameUsed: string;
  /** 대응표로 이름을 바꿨을 때. `ipcc` 는 바꾼 이름이다 (별표 12 배출계수 표의 이름이면 `table` 이 kr-ets-byl12). */
  nameMapping: { domestic: string; ipcc: string; basis: MappingBasis; locator: string; table?: string } | null;
  valueRaw: string;
  /** 쉼표를 걷어 낸 십진 문자열. 계수에 실린 값과 같다. */
  value: string;
  unit: string;
  conditions: Record<string, string>;
}

export interface AssemblyEvidence {
  guideline: AssemblyMapping["guideline"];
  fuelState: { value: FuelState; basis: MappingBasis | "input"; locator: string | null };
  sector: { key: string; label: string; basis: "input" | "ksic_division" | "ksic_exception"; ksicCode: string | null; locator: string };
  tiers: {
    values: ParameterTiers;
    basis: "explicit" | "group_minimum";
    group: FacilityGroup | null;
    /** 그룹 최소기준에서 왔을 때 별표 5 의 자리. CH4·N2O 의 Tier 1 은 별표 6 의 자리. */
    locators: string[];
  };
  parameters: ParameterEvidence[];
  assumptions: Assumption[];
}

export interface AssembledFactor {
  factor: CombustionFactor;
  evidence: AssemblyEvidence;
}

export type AssemblyResult = { ok: true; value: AssembledFactor } | { ok: false; failure: AssemblyFailure };

export type Step<T> = { ok: true; value: T } | { ok: false; failure: AssemblyFailure };
