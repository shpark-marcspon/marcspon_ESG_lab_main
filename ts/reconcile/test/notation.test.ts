import { describe, expect, it } from "vitest";
import { classifyCell, locateQuote, scanNumbers } from "../src/index.js";

describe("숫자 표기 규칙: 표 셀", () => {
  it.each([
    ["1,234", "1234", 0],
    ["1234", "1234", 0],
    ["12.50", "12.50", 2],
    ["0.5", "0.5", 1],
    ["-12", "-12", 0],
    ["−12", "-12", 0],
    ["(123)", "-123", 0],
    ["(2,720)", "-2720", 0],
    ["△12", "-12", 0],
    ["31.0%", "31.0", 1],
    ["1.2배", "1.2", 1],
    ["59,498명", "59498", 0],
    ["24만 톤", "24", 0],
    ["１，２３４", "1234", 0],
    ["1,557,077,275", "1557077275", 0],
  ])("값 후보: %s → %s", (raw, value, decimals) => {
    const cell = classifyCell(raw);
    expect(cell.kind).toBe("value");
    expect(cell.number?.value).toBe(value);
    expect(cell.number?.decimals).toBe(decimals);
  });

  it("부호와 접미를 표시로 남긴다", () => {
    expect(classifyCell("(123)").number).toMatchObject({ sign: "paren_negative", flags: ["paren_negative_assumed"] });
    expect(classifyCell("△12").number).toMatchObject({ sign: "triangle_negative", flags: ["triangle_negative"] });
    expect(classifyCell("▲ 3").number).toMatchObject({ sign: "triangle_ambiguous", value: "3", flags: ["sign_ambiguous"] });
    expect(classifyCell("31.0%").number).toMatchObject({ percent: true, unitSuffix: "%" });
    expect(classifyCell("24만 톤").number).toMatchObject({ unitSuffix: "만 톤" });
    expect(classifyCell("2025").number?.flags).toContain("year_like");
    expect(classifyCell("2,025").number?.flags).not.toContain("year_like");
  });

  it.each([
    ["", "blank"],
    ["-", "no_value_marker"],
    ["–", "no_value_marker"],
    ["N/A", "no_value_marker"],
    ["해당 없음", "no_value_marker"],
    ["2025년", "year"],
    ["FY2024", "year"],
    ["’25년", "text_with_digits"],
    ["04", "code"],
    ["6(1)", "code"],
    ["29(1)(가)①", "code"],
    ["1.2.3", "code"],
    ["2025.7.1", "date"],
    ["10~20", "range"],
    ["65-66", "range"],
    ["3~17%", "range"],
    ["116,818 15,649", "multi_number"],
    ["64, 216", "multi_number"],
    ["1,23", "multi_number"],
    ["21.1 10.2", "multi_number"],
    ["20.00 40.00 60.00", "multi_number"],
    ["Scope 1", "text_with_digits"],
    ["기준연도 대비 42% 감축", "text_with_digits"],
    ["3년", "text_with_digits"],
    ["305-1 직접 온실가스 배출량(Scope 1)", "text_with_digits"],
    ["전력", "text"],
  ])("값 후보 아님: %s → %s", (raw, kind) => {
    const cell = classifyCell(raw);
    expect(cell.kind).toBe(kind);
    expect(cell.number).toBeNull();
  });
});

describe("숫자 표기 규칙: 글 속 숫자", () => {
  const kinds = (text: string) => scanNumbers(text).map((token) => [token.raw, token.kind]);

  it("값 후보와 그 밖을 가른다", () => {
    expect(kinds("2025년 Scope 1 배출량은 22,253 tCO2eq로 전년 대비 1.1% 줄었다.")).toEqual([
      ["2025", "year"],
      ["1", "identifier"],
      ["22,253", "value"],
      ["2", "identifier"],
      ["1.1%", "value"],
    ]);
    expect(kinds("GRI 305-1, ISO 14001, RE100, CO2")).toEqual([
      ["305", "identifier"],
      ["1", "identifier"],
      ["14001", "identifier"],
      ["100", "identifier"],
      ["2", "identifier"],
    ]);
    expect(kinds("제3조에 따라 2차 협력사 12개사를 3월 15일 점검했다")).toEqual([
      ["3", "ordinal"],
      ["2", "ordinal"],
      ["12", "value"],
      ["3", "date_part"],
      ["15", "date_part"],
    ]);
    expect(kinds("문의 02-2073-7114, 발간 2025.7.1")).toEqual([
      ["02", "identifier"],
      ["2073", "identifier"],
      ["7114", "identifier"],
      ["2025.7", "date_part"],
      ["1", "date_part"],
    ]);
    // 숫자 바로 뒤가 아니면 어디서든 덩어리를 낸다: 쉼표·마침표 뒤의 숫자도 버리지 않는다.
    expect(kinds("비중은 각각 10,20,30%이고 12,5%")).toEqual([
      ["10", "value"],
      ["20", "value"],
      ["30%", "value"],
      ["12", "value"],
      ["5%", "value"],
    ]);
    // 「조 원」은 순번(제3조)이 아니라 금액 단위다. 숫자에 붙은 로마자 단위는 값이고, 한 글자나 글자+숫자는 식별자다.
    expect(scanNumbers("자산 333.6조 원, 발전 28MW와 5.02GWh의 전력, 3R 활동, 24X365").map((token) => [token.raw, token.kind, token.number?.unitSuffix ?? null])).toEqual([
      ["333.6", "value", "조 원"],
      ["28", "value", "MW"],
      ["5.02", "value", "GWh"],
      ["3", "identifier", null],
      ["24", "identifier", null],
      ["365", "identifier", null],
    ]);
    expect(kinds("용수 재이용1) 107,967톤, 단기(1~5년)")).toEqual([
      ["1", "marker"],
      ["107,967", "value"],
      ["1", "range_part"],
      ["5", "range_part"],
    ]);
    expect(kinds("1. 개요")).toEqual([["1", "marker"]]);
    // 단위 낱말 뒤에 다른 낱말이 이어지면 단위가 아니다 (`배출량` 의 배, `개국` 의 개, `회차`).
    expect(scanNumbers("2025 배출량, 76개국, 3회차, 3,200명이다, 187조 9,673억 원").map((token) => [token.raw, token.kind, token.number?.unitSuffix ?? null])).toEqual([
      ["2025", "year", null],
      ["76", "value", null],
      ["3", "ordinal", null],
      ["3,200", "value", "명"],
      ["187", "value", null],
      ["9,673", "value", "억 원"],
    ]);
    expect(kinds("2050 탄소중립, 근속 10년, 직원 3,200명")).toEqual([
      ["2050", "year"],
      ["10", "value"],
      ["3,200", "value"],
    ]);
  });

  it("값과 단위 접미, 부호를 읽는다", () => {
    const [amount, change] = scanNumbers("투자액 30억 원, 증감 -3.2%");
    expect(amount?.number).toMatchObject({ value: "30", unitSuffix: "억 원", decimals: 0 });
    expect(change).toMatchObject({ raw: "-3.2%" });
    expect(change?.number).toMatchObject({ value: "-3.2", percent: true, sign: "minus", decimals: 1 });
  });

  it("같은 글자가 두 번 나오면 몇 번째인지를 센다", () => {
    const tokens = scanNumbers("여성 120명, 남성 120명, 합계 240명");
    expect(tokens.map((token) => [token.raw, token.occurrence])).toEqual([
      ["120", 1],
      ["120", 2],
      ["240", 1],
    ]);
    const second = tokens[1];
    expect(locateQuote("여성 120명, 남성 120명, 합계 240명", "120", 2)).toEqual({ start: second?.start, end: second?.end, total: 2 });
    expect(locateQuote("여성 120명", "121", 1)).toBeNull();
    expect(locateQuote("여성 120명", "120", 2)).toBeNull();
  });
});
