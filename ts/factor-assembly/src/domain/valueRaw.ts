import { parseDecimal } from "kernel";

/** 세 자리마다 쉼표를 찍은 표기 (73,200 / 1,234,567.8). 쉼표 자리가 틀린 것(1,2 / 73,20)은 받지 않는다. */
const GROUPED = /^\d{1,3}(,\d{3})+(\.\d+)?$/;

/**
 * 계수 레코드의 `value_raw` 를 엔진이 받는 십진 문자열로 바꾼다. JS number 를 거치지 않는다.
 * 규칙: (1) 이미 십진 표기면 그대로 (2) 세 자리 쉼표 표기면 쉼표만 걷어 낸다 (3) 그 밖은 전부 null
 * — 지수 표기, 공백, 부호 `+`, 단위가 붙은 값, 자리가 틀린 쉼표, 빈 문자열, `-`.
 * 음수는 계수·발열량·산화계수에 있을 수 없어 받지 않는다.
 */
export function readValueRaw(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  if (parseDecimal(raw) !== null) return raw.startsWith("-") ? null : raw;
  if (GROUPED.test(raw)) return raw.replaceAll(",", "");
  return null;
}
