import { describe, expect, it } from "vitest";
import { add, formatDecimal, mul, parseDecimal, roundDecimal } from "../src/decimal.js";

const d = (text: string) => {
  const value = parseDecimal(text);
  if (value === null) throw new Error(`bad literal ${text}`);
  return value;
};

describe("십진 연산", () => {
  it("부동소수점이면 어긋나는 합과 곱이 정확하다", () => {
    expect(formatDecimal(add(d("0.1"), d("0.2")))).toBe("0.3");
    expect(formatDecimal(mul(d("1.1"), d("1.1")))).toBe("1.21");
    expect(formatDecimal(mul(d("0.0352"), d("73200")))).toBe("2576.64");
  });

  it("표기를 정규화한다", () => {
    expect(formatDecimal(d("1.500"))).toBe("1.5");
    expect(formatDecimal(d("0.000"))).toBe("0");
    expect(formatDecimal(d("-0.0"))).toBe("0");
    expect(formatDecimal(d("007"))).toBe("7");
    expect(formatDecimal(d("-0.050"))).toBe("-0.05");
  });

  it.each(["", " 1", "1e3", "1,000", "+1", ".5", "5.", "abc", "NaN", "Infinity"])("십진 문자열이 아닌 %j 는 거부한다", (text) => {
    expect(parseDecimal(text)).toBeNull();
  });

  it("문자열이 아닌 값은 거부한다 (JS number 를 받지 않는다)", () => {
    expect(parseDecimal(1.5)).toBeNull();
    expect(parseDecimal(null)).toBeNull();
    expect(parseDecimal(undefined)).toBeNull();
  });

  it("아주 큰 수와 아주 작은 수의 곱도 자릿수를 잃지 않는다", () => {
    expect(formatDecimal(mul(d("123456789012345678901234567890"), d("0.000000000000000000000000000001")))).toBe(
      "0.12345678901234567890123456789",
    );
  });
});

describe("반올림 (엔진의 유일한 반올림 자리)", () => {
  it.each([
    ["2.5", 0, "half_up", "3"],
    ["2.5", 0, "half_even", "2"],
    ["3.5", 0, "half_even", "4"],
    ["2.5", 0, "down", "2"],
    ["2.4999", 0, "half_up", "2"],
    ["2.5596384", 3, "half_up", "2.560"],
    ["2.5596384", 3, "down", "2.559"],
    ["-2.5", 0, "half_up", "-3"],
    ["-2.5", 0, "down", "-2"],
    ["0.0004", 3, "half_up", "0.000"],
    ["7", 2, "half_up", "7.00"],
    ["0.125", 2, "half_even", "0.12"],
    ["0.135", 2, "half_even", "0.14"],
  ] as const)("%s 를 소수 %i자리 %s → %s", (value, digits, mode, expected) => {
    expect(roundDecimal(d(value), digits, mode)).toBe(expected);
  });

  it("자릿수가 음수이거나 정수가 아니면 던진다", () => {
    expect(() => roundDecimal(d("1"), -1, "half_up")).toThrow(RangeError);
    expect(() => roundDecimal(d("1"), 1.5, "half_up")).toThrow(RangeError);
  });
});
