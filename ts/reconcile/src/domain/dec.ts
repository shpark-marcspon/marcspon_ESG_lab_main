import { add, formatDecimal, parseDecimal, roundDecimal, type Decimal, type RoundingMode } from "kernel";

// kernel 의 십진 연산 위에 얹은 작은 도우미. 값은 끝까지 십진 문자열과 bigint 로만 다룬다 (JS number 를 거치지 않는다).
// 나눗셈이 없다. 단위 환산은 10 의 거듭제곱 자리 옮김뿐이다 (`shift10`).

/** 십진 문자열을 읽는다. 못 읽으면 예외다 (호출하는 쪽이 이미 표기 규칙으로 거른 뒤에만 부른다). */
export function dec(text: string): Decimal {
  const parsed = parseDecimal(text);
  if (!parsed) throw new Error(`십진 문자열이 아니다: ${text}`);
  return parsed;
}

/** kernel 의 정규형(끝자리 0 없음)으로 되돌린다. */
function renormalize(coef: bigint, scale: number): Decimal {
  return dec(formatDecimal({ coef, scale }));
}

export function neg(value: Decimal): Decimal {
  return { coef: -value.coef, scale: value.scale };
}

export function sub(a: Decimal, b: Decimal): Decimal {
  return add(a, neg(b));
}

/** a - b 의 부호: -1, 0, 1. */
export function compare(a: Decimal, b: Decimal): -1 | 0 | 1 {
  const diff = sub(a, b).coef;
  return diff === 0n ? 0 : diff < 0n ? -1 : 1;
}

/** 값 × 10^exp. exp 는 음수일 수 있다. */
export function shift10(value: Decimal, exp: number): Decimal {
  if (!Number.isInteger(exp)) throw new RangeError(`자리 옮김은 정수여야 한다: ${exp}`);
  if (exp >= 0) return renormalize(value.coef * 10n ** BigInt(exp), value.scale);
  return renormalize(value.coef, value.scale - exp);
}

/** 10^exp 의 배수로 반올림한다 (exp 가 3 이면 천 단위, -1 이면 소수 첫째 자리). */
export function roundToPow10(value: Decimal, exp: number, mode: RoundingMode): Decimal {
  if (exp <= 0) return dec(roundDecimal(value, -exp, mode));
  const scaled = shift10(value, -exp);
  return shift10(dec(roundDecimal(scaled, 0, mode)), exp);
}

export function show(value: Decimal): string {
  return formatDecimal(value);
}
