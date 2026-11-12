import { describe, expect, it } from "vitest";
import { classifyText, isCopiedVerbatim, isNumericCell, numberRuns } from "../src/index.js";

describe("숫자 셀 판정", () => {
  it.each([
    ["36,399", "numeric"],
    ["15.4", "numeric"],
    ["31.0%", "numeric"],
    ["(1,234)", "numeric"],
    ["△12", "numeric"],
    ["２０２５", "numeric"],
    ["-", "symbol"],
    ["", "blank"],
    ["  ", "blank"],
    ["GWh", "passthrough"],
    ["tCO2e", "passthrough"],
    ["Scope 1, 2", "passthrough"],
    ["전력", "translatable"],
    ["2023년", "translatable"],
    ["MWh/억 원", "translatable"],
    ["187조 9,673억 원", "translatable"],
  ] as const)("%s → %s", (text, expected) => {
    expect(classifyText(text)).toBe(expected);
  });

  it("숫자 셀과 글자 없는 셀은 원본을 그대로 옮기고 한글이 든 셀만 번역 대상이다", () => {
    expect(isNumericCell("1,234.5")).toBe(true);
    expect(isNumericCell("2023년")).toBe(false);
    expect(isCopiedVerbatim("GWh")).toBe(true);
    expect(isCopiedVerbatim("전력")).toBe(false);
  });

  it("숫자 덩어리는 천 단위 쉼표를 지워 비교한다", () => {
    expect(numberRuns("2025년 36,399 GWh, 12.7%")).toEqual(["2025", "36399", "12.7"]);
    expect(numberRuns("Scope 1, 2")).toEqual(["1", "2"]);
  });
});
