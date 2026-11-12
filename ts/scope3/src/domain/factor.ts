import type { Basis } from "./types.js";
import type { UnitDim } from "./units.js";

export type LicenseStatus = "ok" | "disputed" | "unverified";

/** 추천 후보가 될 수 있는 계수 한 건. 값은 원자료 표기(`valueRaw`)만 들고 계산 때만 읽는다. */
export interface Factor {
  id: string;
  name: string;
  categoryPath: string[];
  factorType: Basis;
  scope3Categories: number[];
  unit: string;
  unitNumerator: string;
  unitDenominator: string;
  denominatorDim: UnitDim;
  valueRaw: string;
  licenseStatus: LicenseStatus;
  sourceId: string;
  region: string;
  classification: { system: string; code: string } | null;
  spend: { currency: string; priceYear: number; priceBasis: string } | null;
}

export interface CandidateQuery {
  category: number;
  /** "physical" 은 활동(물량)·거리 기반, "spend" 는 지출 기반. */
  basis: "physical" | "spend";
  /** 이 라이선스 상태의 계수를 후보에서 뺀다. */
  excludeLicenseStatuses?: readonly LicenseStatus[];
}
