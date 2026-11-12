import { describe, expect, it } from "vitest";
import { UnitTableError, loadUnitTable, parseUnitTable, unitCell } from "../src/index.js";

const table = loadUnitTable();

describe("단위 셀 분류", () => {
  it.each([
    ["억 원", "KRW 100 million"],
    ["억원", "KRW 100 million"],
    ["천 톤", "1,000 tons"],
    ["만 건", "10,000 cases"],
    ["천 원", "KRW 1,000"],
    ["톤", "tons"],
    ["명", "persons"],
    // 단위 기호(한글 없는 토큰)는 글자 그대로 옮긴다. 아래 첨자도 그대로다.
    ["톤 CO₂e/억 원", "tons CO₂e/KRW 100 million"],
    ["MWh/억 원", "MWh/KRW 100 million"],
    ["천 톤 CO₂e", "1,000 tons CO₂e"],
    ["(단위: 억 원)", "(Unit: KRW 100 million)"],
    ["(단위: %)", "(Unit: %)"],
    ["  억  원 ", "KRW 100 million"],
  ])("단위 셀 %s → 고정 번역 %s", (text, expected) => {
    expect(unitCell(text, table)).toEqual({ kind: "fixed", text: expected });
  });

  it.each([
    // 일반 낱말. 수 단위 낱말이나 계량 단위 낱말로 시작하거나 끝나도 단위 셀이 아니다.
    "사업장",
    "천연가스",
    "만족도",
    "억제",
    "원재료",
    "건수",
    "톤수",
    "명칭",
    "개발",
    "매출액",
    // 수 단위 낱말 + 계량 단위 낱말로 읽히는 일반 낱말.
    "조건",
    "조명",
    "조회",
    "만일",
    // 수 단위 낱말만 있는 셀, 숫자가 섞인 셀, 단위가 글 속에 든 셀.
    "천",
    "백만",
    "2023년",
    "59,498명",
    "24만 톤",
    "억 원 2",
    "월 1회",
    "환원량(천 톤)",
    "폐기물 발생량 (천 톤)",
    "원 재료",
    // 로마자 낱말 뒤에 한글 단위가 오는 셀은 항목명이다.
    "ESG 건",
    "Samsung 명",
    "1Q 건",
    // 한글이 없는 셀은 애초에 번역 대상이 아니다.
    "GWh",
    "%",
    "1,234",
    "",
  ])("단위 셀이 아니다: %s", (text) => {
    expect(unitCell(text, table)).toEqual({ kind: "not_unit" });
  });

  it("단위 셀이지만 고정 번역 표에 없는 낱말이 있으면 그 낱말을 남긴다 (모델에 보낸다)", () => {
    expect(unitCell("시간", table)).toEqual({ kind: "no_fixed_translation", unknown: ["시간"] });
    expect(unitCell("개", table)).toEqual({ kind: "no_fixed_translation", unknown: ["개"] });
    expect(unitCell("천 kWh", table)).toEqual({ kind: "no_fixed_translation", unknown: ["천"] });
    expect(unitCell("톤 CO₂e/백억 원", table)).toEqual({ kind: "no_fixed_translation", unknown: ["백억 원"] });
  });
});

describe("고정 번역 표 읽기", () => {
  const base = {
    synthetic: true,
    note: "synthetic 시험용 표",
    sourceLang: "ko",
    targetLang: "en",
    grammar: { note: "시험", multipliers: ["천", "억"], measures: ["원", "톤"] },
    phrases: [{ ko: "억 원", en: "KRW 100 million", synthetic: true }],
  };
  const parse = (raw: unknown) => parseUnitTable(raw, "시험.json", JSON.stringify(raw));

  it("기본 표는 synthetic 이고 항목마다 자기 국문 단위를 그 영문으로 옮긴다", () => {
    expect(table.synthetic).toBe(true);
    expect(table.path).toMatch(/unit-cells\.synthetic\.json$/);
    expect(table.phrases.size).toBeGreaterThanOrEqual(10);
    for (const [ko, en] of table.phrases) expect(unitCell(ko, table)).toEqual({ kind: "fixed", text: en });
  });

  it("가상 표의 항목에 synthetic 표시가 없으면 읽지 않는다", () => {
    expect(() => parse({ ...base, phrases: [{ ko: "억 원", en: "KRW 100 million" }] })).toThrow(UnitTableError);
    expect(() => parse({ ...base, synthetic: false })).toThrow(/synthetic 파일에 둬야 한다/);
  });

  it("일반 낱말을 표에 넣어 고정 번역하게 만들 수 없다", () => {
    expect(() => parse({ ...base, phrases: [{ ko: "사업장", en: "worksites", synthetic: true }] })).toThrow(/단위가 아니다/);
    expect(() => parse({ ...base, phrases: [...base.phrases, { ko: "억원", en: "KRW 100M", synthetic: true }] })).toThrow(/겹친다/);
    expect(parse(base).phrases.get("억원")).toBe("KRW 100 million");
  });
});
