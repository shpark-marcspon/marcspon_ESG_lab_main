// 십진 정확 연산은 공유 커널(`ts/kernel`)에 있다. 이 파일은 옛 경로를 지키는 재수출이다.
export { ZERO, add, equals, formatDecimal, isNegative, mul, parseDecimal, roundDecimal } from "kernel";
export type { Decimal, RoundingMode } from "kernel";
