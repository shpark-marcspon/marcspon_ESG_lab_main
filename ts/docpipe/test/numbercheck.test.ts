import { describe, expect, it } from "vitest";
import { gradeNumberChange, hasNumberDiff, numberDiff } from "../src/index.js";

// 아래 글은 꼴을 보이려고 지은 예다. 수치는 가상(synthetic)이다. 짧은 꼴(`억 원`, `1차 협력회사`, `제3자 검증`)은
// 실제 보고서에도 흔히 나오는 낱말이지만 어느 보고서의 측정 결과를 옮겨 적은 것이 아니다.

describe("숫자 차이", () => {
  it("원문에만 있는 숫자와 번역문에만 있는 숫자를 개수까지 센다", () => {
    expect(numberDiff("용수 재이용1) 107,967", "Water reuse1) 107967")).toEqual({ sourceOnly: [], targetOnly: [] });
    expect(numberDiff("59,498명", "59,489 employees")).toEqual({ sourceOnly: ["59498"], targetOnly: ["59489"] });
    expect(numberDiff("2곳", "2 sites in 2025")).toEqual({ sourceOnly: [], targetOnly: ["2025"] });
    expect(numberDiff("1차 점검 1회", "1 inspection")).toEqual({ sourceOnly: ["1"], targetOnly: [] });
    expect(hasNumberDiff(numberDiff("2023년", "FY2023"))).toBe(false);
    expect(hasNumberDiff(numberDiff("2023년", "FY2024"))).toBe(true);
  });
});

describe("의심 등급", () => {
  it.each([
    // 수 단위 낱말을 숫자로 풀어 씀 (값이 같다)
    ["억 원", "KRW 100 million", ["unit_expansion"]],
    ["천 원", "KRW 1,000", ["unit_expansion"]],
    ["(단위: 만 톤)", "(Unit: 10,000 tons)", ["unit_expansion"]],
    ["만 건", "ten thousand cases (10,000)", ["unit_expansion"]],
    ["복원량 36만 톤 확보", "Secured 360,000 tons of replenishment", ["unit_expansion"]],
    ["누적 3.5천만 톤 수거", "Collect a cumulative 35 million tons", ["unit_expansion"]],
    ["투자 412억 원", "Invested KRW 41.2 billion", ["unit_expansion"]],
    // 작은 수를 같은 값의 영어 낱말로
    ["1차 협력회사", "first-tier suppliers", ["number_word"]],
    ["제3자 검증", "third-party verification", ["number_word"]],
    ["연 1회 점검", "Inspected once a year", ["number_word"]],
    ["4대 원칙", "Four principles", ["number_word"]],
    ["2차 사고 방지", "Prevent secondary incidents", ["number_word"]],
    ["1차 협력회사 71개, 2차 협력회사 30개", "71 first-tier suppliers and 30 second-tier suppliers", ["number_word", "number_word"]],
    // 두 자리 연도를 네 자리로
    ["’30년 목표", "2030 target", ["year_expansion"]],
    ["’98년 설립", "Established in 1998", ["year_expansion"]],
    ["연구소 설립(’21.3월)", "Research center established (Mar. 2021)", ["year_expansion"]],
    // N월을 달 이름으로
    ["2024년 3월 1일 시행", "Effective March 1, 2024", ["month_name"]],
    ["2022년 11월 결의", "Approved in November 2022", ["month_name"]],
  ] as const)("흔한 표기 변환으로 보임: %s → %s", (source, target, patterns) => {
    const graded = gradeNumberChange(source, target);
    expect(graded.unexplained).toEqual({ sourceOnly: [], targetOnly: [] });
    expect(graded.explained.map((entry) => entry.pattern)).toEqual(patterns);
    expect(graded.grade).toBe("notation_like");
  });

  it.each([
    // 각주 번호가 빠짐
    ["재생 원료 3) 사용률4)", "Recycled material usage rate4)", ["3"], []],
    // 숫자가 옆 셀에서 넘어옴, 옆 셀로 넘어감
    ["가상 회사는 올해 국내와", "This year, the company inspected 58 suppliers in Korea and", [], ["58"]],
    ["해외에 있는 58개 협력회사를", "overseas.", ["58"], []],
    // 숫자가 통째로 사라짐
    ["대비 2024년 87%로 같은 수준입니다", "This reflects continued management.", ["2024", "87"], []],
    // 숫자가 다른 숫자로 바뀜
    ["59,498명", "59,489 employees", ["59498"], ["59489"]],
    // 수 단위를 풀었는데 값이 다르다
    ["복원량 36만 톤 확보", "Secured 36,000 tons", ["36"], ["36000"]],
    ["억 원", "KRW 1 million", [], ["1"]],
    // 연도가 다른 해로 바뀜, 연도 표시 없는 두 자리 수
    ["’30년 목표", "2031 target", ["30"], ["2031"]],
    ["30개 사업장", "2030 worksites", ["30"], ["2030"]],
    // 작은 수가 사라졌는데 같은 값의 낱말이 없다
    ["주 1회 점검", "Weekly inspection", ["1"], []],
    ["가구 1곳당 지원액", "Support amount per household", ["1"], []],
    // 낱말이 한 번 나왔으면 한 번만 설명된다
    ["1차 점검 1회", "First inspection", ["1"], []],
    // 일반 낱말 속의 수 단위 글자는 수 단위가 아니다
    ["천연가스 사용량", "Natural gas use 1,000", [], ["1000"]],
  ] as const)("그 밖: %s → %s", (source, target, sourceOnly, targetOnly) => {
    const graded = gradeNumberChange(source, target);
    expect(graded.grade).toBe("other");
    expect(graded.unexplained).toEqual({ sourceOnly, targetOnly });
  });

  it("한 셀에 설명되는 숫자와 설명되지 않는 숫자가 같이 있으면 그 밖이다", () => {
    const graded = gradeNumberChange("1차 협력회사 5) 점검률6)", "First-tier supplier inspection rate6)");
    expect(graded).toMatchObject({ grade: "other", explained: [{ pattern: "number_word", source: "1" }], unexplained: { sourceOnly: ["5"], targetOnly: [] } });
  });
});
