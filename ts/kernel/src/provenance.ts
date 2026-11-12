/**
 * 수치의 출처. 출처 없는 수치는 표에 들어올 수 없다.
 * ghg-calc 의 기준정보 표가 쓰는 모양 그대로다 (`ghg-calc/src/types.ts` 의 `Provenance`).
 */
export type Provenance =
  | { kind: "cited"; title: string; url: string; checkedOn: string; locator?: string; note?: string }
  /** 정의상 정확한 값 (SI 접두어, 1 kWh = 3.6 MJ). 측정값이나 고시값이 아니다. */
  | { kind: "definition"; note: string }
  /** 테스트용 가상 값. 실제 산정에 쓰면 안 된다. */
  | { kind: "synthetic"; note: string };
