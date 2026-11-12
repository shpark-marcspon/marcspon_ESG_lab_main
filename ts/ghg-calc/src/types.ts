// 엔진이 받는 기준정보 표와 활동 데이터, 내놓는 결과의 형식.
// 수치는 전부 십진 문자열이다 (JSON number 를 거치면 부동소수점이 끼어든다).

import type { Provenance } from "kernel";

export type DecimalString = string;

/** 수치의 출처. 출처 없는 수치는 표에 들어올 수 없다 (validateTables 가 막는다). 정의는 `kernel` 에 있다. */
export type { Provenance };

export type Gas = "CO2" | "CH4" | "N2O";
/** `CO2e` 는 이미 환산돼 공표된 계수다. GWP 를 다시 곱하지 않는다. */
export type FactorGas = Gas | "CO2e";

export type CombustionType = "stationary" | "mobile";
export type CalorificBasis = "net" | "gross";

/** 산정등급. 숫자가 클수록 사업장에 가까운 자료를 쓴다 (1 IPCC 기본값, 2 국가 고유값, 3 사업장 고유값, 4 연속측정). */
export type Tier = 1 | 2 | 3 | 4;

/**
 * 매개변수별 산정등급. 한 계수 안에서도 매개변수마다 등급이 다를 수 있다
 * (예: 발열량은 Tier 2, CO2 배출계수와 산화계수는 Tier 1).
 */
export interface ParameterTiers {
  /** 발열량 */
  calorific: Tier;
  /** CO2 배출계수 */
  co2: Tier;
  /** CH4·N2O 배출계수 */
  nonCo2: Tier;
  /** 산화계수. 식에 산화계수 항이 없으면 null */
  oxidation: Tier | null;
}

/** 시설의 쓰임. 고체연료의 산화계수가 발전과 그 밖으로 갈린다. */
export type FacilityUse = "power_generation" | "other";

/**
 * 같은 연료·연소 유형의 연소 계수를 가르는 구분. 계수와 활동 데이터 행이 같은 필드를 갖고, 이름으로 계수를 고를 때 전부 정확히 같아야 한다.
 * 예전에는 `variant` 문자열 하나에 섞여 있었다.
 */
export interface CombustionDistinction {
  /** 부문 (CH4·N2O 기본 배출계수가 부문마다 다르다). 엔진은 뜻을 풀지 않고 같은지만 본다. */
  sector?: string;
  parameterTiers?: ParameterTiers;
  facilityUse?: FacilityUse;
  /** 위 셋에 들지 않는 그 밖의 구분 (차종 등). */
  variant?: string;
}

export interface UnitConversion {
  from: string;
  to: string;
  /** to 단위 값 = from 단위 값 × factor */
  factor: DecimalString;
  source: Provenance;
}

export interface CombustionFactor extends CombustionDistinction {
  kind: "combustion";
  id: string;
  version: string;
  source: Provenance;
  fuel: string;
  combustionType: CombustionType;
  year?: number;
  calorific: {
    value: DecimalString;
    energyUnit: string;
    perUnit: string;
    basis: CalorificBasis;
  };
  /** CO2, CH4, N2O 가 모두 있어야 한다. 빠진 기체를 0 으로 치지 않는다. */
  emissionFactors: Array<{
    gas: Gas;
    value: DecimalString;
    massUnit: string;
    perEnergyUnit: string;
  }>;
  /** CO2 에만 곱한다. 없으면 식에 이 항이 없다. */
  oxidationFactor?: DecimalString;
}

export interface PurchasedEnergyFactor {
  kind: "purchased_energy";
  id: string;
  version: string;
  source: Provenance;
  energyType: "electricity" | "heat";
  /** 같은 에너지 종류 안의 구분 (발전단·소비단, 공급자 등). 부문·산정등급 필드는 연소 계수에만 있다. */
  variant?: string;
  year?: number;
  perUnit: string;
  /** `CO2e` 하나이거나, CO2·CH4·N2O 셋 전부. */
  emissionFactors: Array<{
    gas: FactorGas;
    value: DecimalString;
    massUnit: string;
  }>;
}

export type Factor = CombustionFactor | PurchasedEnergyFactor;

export interface GwpSet {
  id: string;
  label: string;
  source: Provenance;
  values: Partial<Record<Gas, DecimalString>>;
}

export interface ReferenceTables {
  unitConversions: UnitConversion[];
  factors: Factor[];
  gwpSets: GwpSet[];
}

export type SourceType =
  | "stationary_combustion"
  | "mobile_combustion"
  | "purchased_electricity"
  | "purchased_heat";

/** `sector`, `parameterTiers`, `facilityUse` 는 연소 행에만 쓴다. 구매 전력·열 행에 적으면 맞는 계수가 없다. */
export interface ActivityRow extends CombustionDistinction {
  id: string;
  orgUnit: string;
  period: string;
  sourceType: SourceType;
  /** 연소: 연료 이름 (계수의 fuel 과 같아야 한다). 구매 전력·열: 쓰지 않는다. */
  fuel?: string;
  factorYear?: number;
  /** 지정하면 이름·구분·연도 대신 이 id 로 계수를 고른다. */
  factorId?: string;
  amount: DecimalString;
  unit: string;
}

export interface CalcOptions {
  /** 기본값이 없다. 한국의 배출권거래제와 국가 인벤토리가 서로 다른 세트를 쓴다. */
  gwpSetId: string;
  /** 지정하면 이 종류가 아닌 발열량을 쓴 계수는 실패 처리한다. */
  requireCalorificBasis?: CalorificBasis;
}

export type Failure =
  | { kind: "invalid_activity"; field: string; value: string; reason: string }
  | { kind: "factor_not_found"; criteria: Record<string, string> }
  | { kind: "factor_ambiguous"; criteria: Record<string, string>; candidateIds: string[] }
  | { kind: "factor_kind_mismatch"; factorId: string; expected: Factor["kind"]; actual: Factor["kind"] }
  | { kind: "factor_incomplete"; factorId: string; reason: string }
  | { kind: "invalid_reference_value"; where: string; value: string }
  | { kind: "calorific_basis_mismatch"; factorId: string; required: CalorificBasis; actual: CalorificBasis }
  | { kind: "unit_conversion_not_found"; from: string; to: string; context: string }
  | { kind: "unit_conversion_ambiguous"; from: string; to: string; factors: string[] }
  | { kind: "gwp_set_not_found"; gwpSetId: string }
  | { kind: "gwp_set_ambiguous"; gwpSetId: string }
  | { kind: "gwp_value_missing"; gwpSetId: string; gas: Gas };

/** 한 연산 단계. 제3자가 손으로 따라갈 수 있게 식과 결과를 문자열로 남긴다. */
export interface Step {
  label: string;
  formula: string;
  result: DecimalString;
  unit: string;
  conversion?: UnitConversion;
}

export interface GasResult {
  gas: FactorGas;
  /** 기체 질량 (t). `CO2e` 계수이면 이미 tCO2eq 이다. */
  mass: DecimalString;
  massUnit: "t";
  /** 적용한 GWP. `CO2e` 계수이면 null (곱하지 않았다). */
  gwp: DecimalString | null;
  co2e: DecimalString;
}

export interface RowIdentity {
  rowId: string;
  orgUnit: string;
  period: string;
  scope: 1 | 2;
  sourceType: SourceType;
}

export interface RowOk extends RowIdentity {
  status: "ok";
  /** 반올림하지 않은 정확한 값. */
  co2e: DecimalString;
  unit: "tCO2eq";
  /** 연소일 때만. 계수의 에너지 단위 기준 (예: TJ). */
  energy: { value: DecimalString; unit: string } | null;
  gases: GasResult[];
  evidence: {
    factor: { id: string; version: string; source: Provenance };
    gwpSet: { id: string; label: string; source: Provenance };
    calorificBasis: CalorificBasis | null;
    oxidationFactor: DecimalString | null;
    /** 쓴 계수의 부문과 매개변수별 산정등급. 계수에 적혀 있지 않으면 null (구매 전력·열은 늘 null). */
    sector: string | null;
    parameterTiers: ParameterTiers | null;
    facilityUse: FacilityUse | null;
    steps: Step[];
  };
}

/** 배출원 유형을 알 수 없는 행은 scope 와 sourceType 이 null 이다 (추측하지 않는다). */
export interface RowFailed extends Omit<RowIdentity, "scope" | "sourceType"> {
  scope: 1 | 2 | null;
  sourceType: SourceType | null;
  status: "failed";
  failure: Failure;
}

export type RowResult = RowOk | RowFailed;

export type Result<T> = { ok: true; value: T } | { ok: false; failure: Failure };
