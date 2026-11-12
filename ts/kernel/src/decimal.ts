// 십진 정확 연산. 값 = coef / 10^scale (coef 는 bigint).
// 수량·금액·계수는 십진 문자열로 들어와 여기서만 연산된다. JS number 는 쓰지 않는다.
// 나눗셈이 없다: 식은 곱셈과 덧셈뿐이고 단위 환산도 곱셈 계수로 주어지므로
// 결과는 항상 유한 소수이며 중간 반올림이 없다. 반올림은 roundDecimal 한 곳에만 있다.
// ghg-calc 와 scope3 가 같이 쓴다 (각자의 src/decimal.ts 는 여기를 재수출한다).

export interface Decimal {
  readonly coef: bigint;
  readonly scale: number;
}

export type RoundingMode = "half_up" | "half_even" | "down";

const DECIMAL_PATTERN = /^-?\d+(\.\d+)?$/;


export function isDecimalString(text: unknown): text is string {
  return typeof text === "string" && DECIMAL_PATTERN.test(text);
}

function normalize(coef: bigint, scale: number): Decimal {
  if (coef === 0n) return { coef: 0n, scale: 0 };
  let c = coef;
  let s = scale;
  while (s > 0 && c % 10n === 0n) {
    c /= 10n;
    s -= 1;
  }
  return { coef: c, scale: s };
}

/** 십진 문자열만 받는다. 지수 표기, 쉼표, 공백, `+` 부호, 빈 문자열은 null. */
export function parseDecimal(text: unknown): Decimal | null {
  if (!isDecimalString(text)) return null;
  const negative = text.startsWith("-");
  const body = negative ? text.slice(1) : text;
  const [intPart = "", fracPart = ""] = body.split(".");
  const coef = BigInt(intPart + fracPart);
  return normalize(negative ? -coef : coef, fracPart.length);
}

export function formatDecimal(d: Decimal): string {
  const negative = d.coef < 0n;
  const digits = (negative ? -d.coef : d.coef).toString();
  if (d.scale === 0) return (negative ? "-" : "") + digits;
  const padded = digits.padStart(d.scale + 1, "0");
  const intPart = padded.slice(0, padded.length - d.scale);
  const fracPart = padded.slice(padded.length - d.scale);
  return `${negative ? "-" : ""}${intPart}.${fracPart}`;
}

export const ZERO: Decimal = { coef: 0n, scale: 0 };

export function mul(a: Decimal, b: Decimal): Decimal {
  return normalize(a.coef * b.coef, a.scale + b.scale);
}

export function add(a: Decimal, b: Decimal): Decimal {
  const scale = Math.max(a.scale, b.scale);
  const ac = a.coef * 10n ** BigInt(scale - a.scale);
  const bc = b.coef * 10n ** BigInt(scale - b.scale);
  return normalize(ac + bc, scale);
}

export function isNegative(d: Decimal): boolean {
  return d.coef < 0n;
}

export function equals(a: Decimal, b: Decimal): boolean {
  return a.coef === b.coef && a.scale === b.scale;
}

/**
 * 유일한 반올림. 계산과 집계는 이 함수를 부르지 않는다.
 * 표시·보고 단계에서 호출자가 자릿수와 방식을 명시해 부른다.
 * 결과는 소수 `digits` 자리로 고정된 문자열이다 (예: digits=3 → "2.560").
 */
export function roundDecimal(d: Decimal, digits: number, mode: RoundingMode): string {
  if (!Number.isInteger(digits) || digits < 0) {
    throw new RangeError(`digits must be a non-negative integer: ${digits}`);
  }
  let coef: bigint;
  if (d.scale <= digits) {
    coef = d.coef * 10n ** BigInt(digits - d.scale);
  } else {
    const divisor = 10n ** BigInt(d.scale - digits);
    const negative = d.coef < 0n;
    const abs = negative ? -d.coef : d.coef;
    let quotient = abs / divisor;
    const twiceRemainder = (abs % divisor) * 2n;
    if (mode === "half_up") {
      if (twiceRemainder >= divisor) quotient += 1n;
    } else if (mode === "half_even") {
      if (twiceRemainder > divisor || (twiceRemainder === divisor && quotient % 2n === 1n)) {
        quotient += 1n;
      }
    }
    coef = negative ? -quotient : quotient;
  }
  const negative = coef < 0n;
  const text = (negative ? -coef : coef).toString().padStart(digits + 1, "0");
  const sign = negative ? "-" : "";
  if (digits === 0) return sign + text;
  return `${sign}${text.slice(0, text.length - digits)}.${text.slice(text.length - digits)}`;
}
