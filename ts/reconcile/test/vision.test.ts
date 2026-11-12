import { describe, expect, it } from "vitest";
import type { ChartGold, Tag } from "../src/compare/charts.js";
import { scoreLabels } from "../src/compare/charts.js";
import { visionPredictor } from "../src/compare/cli4.js";
import { chartLabelFor } from "../src/compare/prose.js";
import { answerKey, imageTokens, judgeTagAnswers, mergeVision, pngSize, tagLabelsSchema, visionLabels, type TagAnswer } from "../src/compare/vision.js";
import { z } from "zod";

// 그림 라벨(밴드 W 의 (다))의 답 확인과 합치기. 지어낸 번호표와 답으로 본다 (synthetic). 모델을 부르지 않는다.

const tags: Tag[] = [
  { page: 1, n: 1, bbox: [50, 100, 70, 108], id: "v1", ids: ["v1", "v1-twin"] },
  { page: 1, n: 2, bbox: [90, 100, 110, 108], id: "v2", ids: ["v2"] },
  { page: 1, n: 3, bbox: [20, 150, 30, 158], id: "t1", ids: ["t1"] },
];
const RAW: Record<string, string> = { v1: "1,234", "v1-twin": "1,234", v2: "42.5%", t1: "10" };
const rawOf = (id: string): string => RAW[id] as string;
const item = (tag: string, extra: Partial<z.infer<typeof tagLabelsSchema>["tags"][number]> = {}) => ({ tag, role: "value" as const, title: "가상 배출량", series: "계획", category: "2023", unit: "톤", ...extra });

describe("그림 라벨의 답 확인", () => {
  it("답 스키마에 숫자 필드가 없다 (번호도 글자다)", () => {
    const json = JSON.stringify(z.toJSONSchema(tagLabelsSchema));
    expect(json).not.toMatch(/"type":"(number|integer)"/);
    expect(json).not.toContain("value\":{"); // 값 필드가 없다
  });

  it("번호마다 답을 값 후보 ID 에 건다. 자리가 같은 값 후보는 같은 답을 받는다", () => {
    const judged = judgeTagAnswers({ tags: [item("1"), item("2", { series: null, category: "2024" }), item("3", { role: "tick" })] }, tags, rawOf);
    expect(judged.rejected).toEqual({});
    expect(judged.unanswered).toEqual([]);
    expect(judged.answers.v1).toEqual({ role: "value", title: "가상 배출량", series: "계획", category: "2023", unit: "톤" });
    expect(judged.answers["v1-twin"]).toEqual(judged.answers.v1);
    expect(judged.answers.v2?.series).toBeNull();
    // 값이 아니라고 한 자리는 글을 받지 않는다
    expect(judged.answers.t1).toEqual({ role: "tick", title: null, series: null, category: null, unit: null });
  });

  it("없는 번호와 같은 번호의 둘째 답은 버린다. 답이 없는 번호를 남긴다", () => {
    const judged = judgeTagAnswers({ tags: [item("1"), item("1", { category: "2024" }), item("9")] }, tags, rawOf);
    expect(judged.rejected).toEqual({ duplicate_tag: 1, unknown_tag: 1 });
    expect(judged.answers.v1?.category).toBe("2023");
    expect(judged.unanswered).toEqual([2, 3]);
    expect(judgeTagAnswers(null, tags, rawOf).unanswered).toEqual([1, 2, 3]);
  });

  it("답의 글에 값의 글자가 있으면 그 답을 버린다", () => {
    // 제 번호표가 덮은 숫자 (쉼표가 없어도, 어느 칸에 있어도)
    expect(judgeTagAnswers({ tags: [item("1", { series: "계획 1234 톤" })] }, tags, rawOf).rejected).toEqual({ value_text_in_answer: 1 });
    expect(judgeTagAnswers({ tags: [item("2", { unit: "42.5%" })] }, tags, rawOf).rejected).toEqual({ value_text_in_answer: 1 });
    // 그 쪽의 다른 번호표가 덮은 숫자 (숫자 글자 셋 이상)
    expect(judgeTagAnswers({ tags: [item("2", { title: "합계 1,234" })] }, tags, rawOf).rejected).toEqual({ value_text_in_answer: 1 });
    // 다른 번호표의 한두 자리 숫자(눈금 10)와 겹치는 이름은 버리지 않는다. 범주의 연도도 그대로다
    const kept = judgeTagAnswers({ tags: [item("1", { series: "장기(10년)", category: "2030" })] }, tags, rawOf);
    expect(kept.rejected).toEqual({});
    expect(kept.answers.v1?.series).toBe("장기(10년)");
    // 제 번호표의 숫자는 짧아도 버린다
    expect(judgeTagAnswers({ tags: [item("3", { category: "10" })] }, tags, rawOf).rejected).toEqual({ value_text_in_answer: 1 });
    // 버려진 번호는 답 없음으로 남는다
    expect(judgeTagAnswers({ tags: [item("1", { series: "1,234" })] }, tags, rawOf).unanswered).toEqual([1, 2, 3]);
  });

  it("라벨 글은 값이라고 답했고 범주가 있는 것만, 요청 글에서는 같은 가림을 거친다", () => {
    const answers: Record<string, TagAnswer> = { v1: { role: "value", title: "가상 배출량", series: "계획", category: "2023", unit: "톤" }, v2: { role: "value", title: "가상 배출량", series: null, category: null, unit: null }, t1: { role: "tick", title: null, series: null, category: null, unit: null }, v3: { role: "value", title: "합계 9,876 톤", series: null, category: "2024", unit: null } };
    const labels = visionLabels(answers);
    expect(labels).toEqual({ v1: "가상 배출량 · 계획 · 2023", v3: "합계 9,876 톤 · 2024" });
    expect(chartLabelFor(labels, "v3")).toBe("합계 ▒ 톤 · 2024");
  });
});

describe("세 실행을 합치기", () => {
  const value = (series: string | null, category: string | null, title = "제목"): TagAnswer => ({ role: "value", title, series, category, unit: null });
  const tick: TagAnswer = { role: "tick", title: null, series: null, category: null, unit: null };

  it("과반이 같은 갈래·계열·범주를 냈을 때만 쓰고, 제목은 그렇게 답한 가장 앞선 실행의 것이다", () => {
    const merged = mergeVision([
      { same: value("계획", "2023", "첫째 제목"), two: value("계획", "2023"), split: value("계획", "2023"), ticks: tick, lone: value("계획", "2023") },
      { same: value("계획", "2023", "둘째 제목"), two: value("실적", "2023", "둘째 제목"), split: value("실적", "2023"), ticks: tick },
      { same: value("계 획", "2023"), two: value("실적", "2023", "셋째 제목"), split: value(null, "2023"), ticks: value(null, "2024") },
    ]);
    expect(merged.answers.same).toEqual(value("계획", "2023", "첫째 제목"));
    expect(merged.answers.two).toEqual(value("실적", "2023", "둘째 제목"));
    expect(merged.answers.split).toBeUndefined();
    expect(merged.answers.ticks).toEqual(tick);
    expect(merged.answers.lone).toBeUndefined(); // 한 실행만 답한 것은 과반이 아니다
    expect(merged.agreement).toEqual({ all: 1, majority: 2, none: 2, ids: 5 });
  });

  it("같은 답의 열쇠는 제목과 단위를 보지 않는다", () => {
    expect(answerKey(value("계획", "2023", "가"))).toBe(answerKey({ ...value("계획", "2023", "나"), unit: "톤" }));
    expect(answerKey(value("계획", "2023"))).not.toBe(answerKey(value("계획", "2024")));
    expect(answerKey(undefined)).toBe("∅");
  });
});

describe("그림 라벨의 채점과 그림 토큰의 어림", () => {
  it("번호표가 없던 자리와 값이 아니라고 답한 자리는 못 붙임이고 분모에 든다", () => {
    const labels: ChartGold["labels"] = [
      { doc: "a", id: "v1", raw: "1,234", chart: "G1", role: "value", series: ["계획"], category: ["2023"] },
      { doc: "a", id: "v2", raw: "42.5%", chart: "G1", role: "value", series: ["실적"], category: ["2023"] },
      { doc: "a", id: "untagged", raw: "7", chart: "G1", role: "value", series: ["실적"], category: ["2024"] },
    ];
    const score = scoreLabels(labels, visionPredictor({ a: { v1: { role: "value", title: null, series: "계획", category: "2023", unit: null }, v2: { role: "tick", title: null, series: null, category: null, unit: null } }, b: {} }));
    expect([score.values, score.seenAsValue]).toEqual([3, 1]);
    expect(score.category).toEqual({ attached: 1, correct: 1, none: 2 });
    expect(score.series).toEqual({ gold: 3, attached: 1, correct: 1, correctStrict: 1, none: 2 });
  });

  it("PNG 의 크기를 읽고 32px 조각 수에 곱수를 곱한다", () => {
    const png = new Uint8Array(24);
    new DataView(png.buffer).setUint32(0, 0x89504e47);
    new DataView(png.buffer).setUint32(16, 1654);
    new DataView(png.buffer).setUint32(20, 2339);
    expect(pngSize(png)).toEqual({ width: 1654, height: 2339 });
    expect(imageTokens({ width: 1654, height: 2339 }, 1.2)).toBe(Math.ceil(52 * 74 * 1.2));
    expect(() => pngSize(new Uint8Array(24))).toThrow();
  });
});
