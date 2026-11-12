/** AI 를 적용하는 Scope 3 카테고리 (PRJ11-RPT-6): 1·2 구매, 4·9 운송, 6 출장, 7 통근. */
export const SUPPORTED_CATEGORIES = [1, 2, 4, 6, 7, 9] as const;
export type SupportedCategory = (typeof SUPPORTED_CATEGORIES)[number];

export type Confidence = "high" | "medium" | "low";

/** 운송 행의 부가 정보. 거리는 입력으로 받는다. 없으면 null 이고 추정하지 않는다. */
export interface TransportInfo {
  origin: string;
  destination: string;
  mode: string;
  /** 십진 문자열 (kg). */
  weightKg: string | null;
  /** 십진 문자열 (km). */
  distanceKm: string | null;
}

/** 구매·물류·출장·통근 내역의 한 행. 수량과 금액은 십진 문자열이다. */
export interface InputRow {
  rowId: string;
  itemName: string;
  quantity: string | null;
  unit: string | null;
  amount: string | null;
  currency: string | null;
  date: string;
  orgUnit: string;
  transport: TransportInfo | null;
}

export type Basis = "activity" | "distance" | "spend";

/** 활동 기반에서 지출 기반으로 넘어간 이유. */
export type FallbackReason = "no_activity_candidates" | "unit_mismatch" | "llm_found_none_suitable";

/** 추천된 계수 하나와 그 선택 이유·신뢰도. */
export interface FactorPick {
  factorId: string;
  reason: string;
  confidence: Confidence;
}
