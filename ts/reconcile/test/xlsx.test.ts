import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { XlsxError, extractGrids, isDateFormat, readXlsx, renderNumber, storedToDecimal } from "../src/index.js";
import { buildXlsx, tempDir } from "./helpers.js";

// 가상(synthetic) 엑셀. XML 을 직접 지은 것이고 엑셀이 저장한 파일이 아니다.
function sourceFile(): string {
  const formats = [0, 3, 2, 10, 14, '#,##0,"천"', "#,##0;(#,##0)", "0.0E+0"];
  const bytes = buildXlsx(
    [
      {
        name: "환경(synthetic)",
        cells: [
          { ref: "A1", shared: "환경 데이터 (단위: tCO2eq)" },
          { ref: "A3", shared: "구분" },
          { ref: "B3", shared: "단위" },
          { ref: "C3", v: "2023" },
          { ref: "D3", v: "2024" },
          { ref: "A4", shared: "직접 온실가스(Scope 1)" },
          { ref: "B4", text: "tCO2eq" },
          { ref: "C4", v: "1200.4", s: 1 },
          { ref: "D4", v: "1349.5", s: 1 },
          { ref: "A5", shared: "에너지 사용량" },
          { ref: "B5", text: "TJ" },
          { ref: "C5", v: "45.324999999999996", s: 2 },
          { ref: "D5", v: "0.30000000000000004", formula: "0.1+0.2" },
          { ref: "A6", shared: "재생에너지 비율" },
          { ref: "B6", text: "%" },
          { ref: "C6", v: "0.1234", s: 3 },
          { ref: "D6", v: "1.5E-2", s: 3 },
          { ref: "A7", shared: "기준일" },
          { ref: "C7", v: "45657", s: 4 },
          { ref: "A8", shared: "매출" },
          { ref: "B8", text: "원" },
          { ref: "C8", v: "5120000", s: 5 },
          { ref: "D8", v: "-2720", s: 6 },
          { ref: "A9", shared: "글자로 든 숫자" },
          { ref: "C9", text: "1,234" },
          { ref: "D9", v: "12345", s: 7 },
          { ref: "A10", shared: "합계" },
          { ref: "C10", formula: "C4+C5" },
        ],
      },
    ],
    formats,
  );
  const path = join(tempDir(), "source.synthetic.xlsx");
  writeFileSync(path, bytes);
  return path;
}

describe("엑셀 읽기: 저장 값과 화면 표기", () => {
  const sheet = readXlsx(sourceFile(), "a");
  const extraction = extractGrids(sheet.grids, "a", sheet.fileName);
  const fact = (ref: string) => extraction.facts.find((item) => item.where.ref === ref);

  it("빈 행으로 끊긴 덩어리를 격자로 읽고, 글자 하나뿐인 덩어리는 제목 후보로 둔다", () => {
    expect(sheet.grids).toHaveLength(1);
    expect(sheet.grids[0]).toMatchObject({ id: "환경(synthetic)!A3", kind: "sheet", sheet: "환경(synthetic)", titleCandidates: ["환경 데이터 (단위: tCO2eq)"], unitLines: ["환경 데이터 (단위: tCO2eq)"] });
    expect(sheet.grids[0]?.header).toMatchObject({ rows: [0], cols: [0, 1] });
  });

  it("값은 저장 값이고, 화면 표기는 따로 남는다 (둘이 다를 수 있다)", () => {
    expect(fact("C4")).toMatchObject({ value: "1200.4", raw: "1,200", decimals: 1, stored: { storedRaw: "1200.4", formatCode: "#,##0", display: "1,200", displayStatus: "rendered" } });
    expect(fact("D4")).toMatchObject({ value: "1349.5", raw: "1,350" });
    expect(fact("C5")).toMatchObject({ value: "45.324999999999996", raw: "45.32", stored: { formatCode: "0.00" } });
  });

  it("이진 부동소수의 꼬리와 지수 표기를 JS number 없이 그대로 읽는다", () => {
    expect(fact("D5")).toMatchObject({ value: "0.30000000000000004", raw: "0.3", stored: { formula: true, displayStatus: "general_approximated" } });
    // 퍼센트 서식: 저장 값 0.015 를 % 단위의 값 1.5 로 읽는다.
    expect(fact("D6")).toMatchObject({ value: "1.5", raw: "1.50%", percent: true, unitSuffix: "%", flags: ["percent_format_scaled"], stored: { stored: "0.015", storedRaw: "1.5E-2" } });
    expect(storedToDecimal("1.5E-2")).toBe("0.015");
    expect(storedToDecimal("1E+3")).toBe("1000");
    expect(storedToDecimal("-0")).toBe("0");
    expect(storedToDecimal("abc")).toBeNull();
  });

  it("퍼센트, 끝 쉼표(천 단위로 줄여 보이기), 음수 괄호 서식을 적용한다", () => {
    expect(fact("C6")).toMatchObject({ value: "12.34", raw: "12.34%", stored: { stored: "0.1234" } });
    expect(fact("C8")).toMatchObject({ value: "5120000", raw: "5,120천" });
    expect(fact("D8")).toMatchObject({ value: "-2720", raw: "(2,720)", sign: "minus" });
  });

  it("지원하지 않는 서식은 화면 표기를 지어내지 않는다", () => {
    expect(fact("D9")).toMatchObject({ value: "12345", raw: "12345", stored: { display: null, displayStatus: "unsupported_format" } });
  });

  it("날짜 서식 셀은 값 후보가 아니고, 글자로 든 숫자는 그 표시를 단다", () => {
    expect(fact("C7")).toBeUndefined();
    expect(extraction.nonValues.find((item) => item.where.ref === "C7")?.class).toBe("date");
    expect(fact("C9")).toMatchObject({ value: "1234", storedAsText: true, stored: null });
  });

  it("수식인데 저장된 결과가 없는 셀은 값을 지어내지 않고 「읽지 못한 숫자」로 남긴다", () => {
    expect(fact("C10")).toBeUndefined();
    expect(extraction.nonValues.find((item) => item.where.ref === "C10")).toMatchObject({ class: "formula_without_result" });
  });

  it("머리글의 연도는 값 후보가 아니고, 행 머리와 열 머리가 붙는다", () => {
    expect(fact("C3")).toBeUndefined();
    expect(fact("C4")?.headers).toEqual({
      row: [
        { cell: "r1c0", text: "직접 온실가스(Scope 1)", inherited: false },
        { cell: "r1c1", text: "tCO2eq", inherited: false },
      ],
      col: [{ cell: "r0c2", text: "2023", inherited: false }],
    });
  });

  it("xlsx 가 아닌 파일은 오류다", () => {
    const path = join(tempDir(), "broken.xlsx");
    writeFileSync(path, "xlsx 가 아니다");
    expect(() => readXlsx(path, "a")).toThrow(XlsxError);
  });
});

describe("숫자 서식", () => {
  it.each([
    ["1234.5", "0", "1235"],
    ["1234.5", "#,##0.00", "1,234.50"],
    ["-1234.5", "#,##0.0", "-1,234.5"],
    ["-1234.5", "#,##0 ;[Red](#,##0)", "(1,235)"],
    ["0.5", "0%", "50%"],
    ["1234567", "#,##0,,", "1"],
    ["0.25", "0.0##", "0.25"],
    ["12", '0"명"', "12명"],
  ])("%s 에 %s → %s", (stored, code, display) => {
    expect(renderNumber(stored, code)).toEqual({ display, status: "rendered" });
  });

  it.each(["# ?/?", "0.00E+00", "@", "?numFmtId=41"])("지원하지 않는 서식: %s", (code) => {
    expect(renderNumber("12.5", code)).toEqual({ display: null, status: "unsupported_format" });
  });

  it("날짜 서식을 알아본다", () => {
    expect(isDateFormat("yyyy-mm-dd")).toBe(true);
    expect(isDateFormat("m/d/yy h:mm")).toBe(true);
    expect(isDateFormat("#,##0")).toBe(false);
    expect(isDateFormat('0"day"')).toBe(false);
    expect(isDateFormat("General")).toBe(false);
  });
});
