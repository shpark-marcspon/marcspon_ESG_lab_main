import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { agreement, judgePlaceAnswers, mergePlaces, placeLabelsSchema, renderLines, scoreGrouping, scoreRoles, tagsInLines, toChartTableInput, unmaskedInLines, type Line, type PlaceAnswer } from "../src/compare/chart-places.js";
import type { ChartGold, Tag } from "../src/compare/charts.js";
import { factsOfLinks, main, renderScores } from "../src/compare/cli13.js";
import type { Fact } from "../src/domain/facts.js";

// 밴드 VT1: 번호표마다의 답(도표 묶음·계열·범주)의 확인, 합치기, 채점. 지어낸 번호표와 답으로 본다 (synthetic). 모델을 부르지 않는다.

const tags: Tag[] = [
  { page: 1, n: 1, bbox: [50, 100, 70, 108], id: "v1", ids: ["v1", "v1-twin"] },
  { page: 1, n: 2, bbox: [90, 100, 110, 108], id: "v2", ids: ["v2"] },
  { page: 1, n: 3, bbox: [50, 150, 60, 158], id: "c1", ids: ["c1"] },
  { page: 1, n: 4, bbox: [20, 150, 30, 158], id: "t1", ids: ["t1"] },
];
const RAW: Record<string, string> = { v1: "1,234", "v1-twin": "1,234", v2: "42.5%", c1: "3", t1: "10" };
const rawOf = (id: string): string => RAW[id] as string;
type Item = z.infer<typeof placeLabelsSchema>["tags"][number];
const item = (tag: string, extra: Partial<Item> = {}): Item => ({ tag, role: "value", chart: "A", title: "가상 배출량", series: "계획", category: "2023", category_tag: null, unit: "톤", ...extra });
const value = (extra: Partial<PlaceAnswer> = {}): PlaceAnswer => ({ role: "value", chart: "A", title: "가상 배출량", series: "계획", category: "2023", categoryFrom: "text", unit: null, ...extra });
const other: PlaceAnswer = { role: "other", chart: null, title: null, series: null, category: null, categoryFrom: null, unit: null };

describe("쪽의 글에 값이 남지 않는다", () => {
  const lines: Line[] = [
    { x0: 100, x1: 300, y: 50, text: "가상 배출량 (단위: tCO▒eq)" },
    { x0: 120, x1: 140, y: 200, text: "〔1〕" },
    { x0: 120, x1: 140, y: 260, text: "2023" },
    { x0: 400, x1: 700, y: 400, text: "전년보다 ▒% 늘었다 〔2〕 FY2024 '2023 2025년" },
  ];
  it("번호표와 ▒ 와 연도 꼴뿐이면 걸리는 낱말이 없다", () => {
    expect(unmaskedInLines(lines)).toEqual([]);
    expect(tagsInLines(lines)).toEqual([1, 2]);
    expect(renderLines(lines).split("\n")[1]).toBe("[120-140, 200] 〔1〕");
  });
  it("가리지 못한 숫자가 하나라도 있으면 그 낱말을 낸다 (요청을 막는 확인)", () => {
    expect(unmaskedInLines([...lines, { x0: 1, x1: 2, y: 3, text: "합계 1,234 톤" }])).toEqual(["1,234"]);
    expect(unmaskedInLines([{ x0: 1, x1: 2, y: 3, text: "20231" }, { x0: 1, x1: 2, y: 3, text: "３７%" }, { x0: 1, x1: 2, y: 3, text: "Scope1 '23" }])).toEqual(["20231", "３７%", "Scope1", "'23"]); // 두 자리 줄임 연도는 연도 꼴이 아니다
  });
  it("좌표는 글과 따로다: 좌표의 숫자가 걸리지 않는다", () => {
    expect(unmaskedInLines([{ x0: 123, x1: 456, y: 789, text: "범례" }])).toEqual([]);
  });
});

describe("번호표의 답 확인", () => {
  it("답 스키마에 숫자 필드가 없다", () => {
    const json = JSON.stringify(z.toJSONSchema(placeLabelsSchema));
    expect(json).not.toMatch(/"type":"(number|integer)"/);
    expect(json).not.toContain('"value":{');
  });

  it("번호마다 답을 ID 에 건다. 자리가 같은 ID 는 같은 답을 받는다", () => {
    const judged = judgePlaceAnswers({ tags: [item("1"), item("2", { series: null, category: "2024", chart: "B" }), item("3", { role: "category" }), item("4", { role: "tick" })] }, tags, rawOf);
    expect(judged.rejected).toEqual({});
    expect(judged.unanswered).toEqual([]);
    expect(judged.answers.v1).toEqual({ role: "value", chart: "A", title: "가상 배출량", series: "계획", category: "2023", categoryFrom: "text", unit: "톤" });
    expect(judged.answers["v1-twin"]).toEqual(judged.answers.v1);
    expect(judged.answers.v2?.chart).toBe("B");
    // 값이 아닌 갈래에는 묶음 이름만 남는다.
    expect(judged.answers.c1).toEqual({ role: "category", chart: "A", title: null, series: null, category: null, categoryFrom: null, unit: null });
    expect(judged.answers.t1?.role).toBe("tick");
  });

  it("없는 번호, 겹친 번호를 버린다", () => {
    const judged = judgePlaceAnswers({ tags: [item("1"), item("1", { category: "2030" }), item("9")] }, tags, rawOf);
    expect(judged.rejected).toEqual({ duplicate_tag: 1, unknown_tag: 1 });
    expect(judged.answers.v1?.category).toBe("2023");
    expect(judged.unanswered).toEqual([2, 3, 4]);
  });

  it("답의 글에 값의 글자가 있으면 버린다", () => {
    const judged = judgePlaceAnswers({ tags: [item("1", { category: "1,234" }), item("2", { title: "1234 톤 배출" }), item("4", { role: "value", series: "10" })] }, tags, rawOf);
    expect(judged.rejected).toEqual({ value_text_in_answer: 3 });
    expect(Object.keys(judged.answers)).toEqual([]);
  });

  it("범주를 번호표로 가리키면 그 번호표의 원문을 코드가 채운다", () => {
    const judged = judgePlaceAnswers({ tags: [item("1", { category: null, category_tag: "3" }), item("2", { category: null, category_tag: "〔3〕" })] }, tags, rawOf);
    expect(judged.rejected).toEqual({});
    expect(judged.answers.v1).toMatchObject({ category: "3", categoryFrom: "tag" });
    expect(judged.answers.v2).toMatchObject({ category: "3", categoryFrom: "tag" });
  });

  it("없는 번호나 자기 자신을 범주로 가리키면 범주를 비운다", () => {
    const judged = judgePlaceAnswers({ tags: [item("1", { category: "2023", category_tag: "77" }), item("2", { category: null, category_tag: "2" })] }, tags, rawOf);
    expect(judged.badCategoryTag).toBe(2);
    expect(judged.answers.v1).toMatchObject({ role: "value", category: null, categoryFrom: null });
    expect(judged.answers.v2?.category).toBeNull();
  });
});

describe("세 실행을 합치기", () => {
  const pageOf = (): number => 1;
  it("과반이 같은 답만 쓴다. 한 실행만 낸 답은 쓰지 않는다", () => {
    const merged = mergePlaces([{ x: value(), y: value({ category: "2024" }), z: value({ series: "실적" }) }, { x: value({ title: "다른 제목" }), y: value({ category: "2025" }), w: value() }, { x: value(), y: value({ category: "2026" }) }], pageOf);
    expect(Object.keys(merged.answers)).toEqual(["x"]);
    expect(merged.answers.x?.title).toBe("가상 배출량"); // 제목은 그렇게 답한 가장 앞선 실행의 것
    expect(merged.agreement).toEqual({ all: 1, majority: 0, none: 3, ids: 4 });
  });
  it("둘이 같으면 과반이다. unanimous 는 셋이 다 같아야 한다", () => {
    const runs = [{ x: value(), y: value() }, { x: value(), y: value() }, { x: value(), y: other }];
    expect(Object.keys(mergePlaces(runs, pageOf).answers)).toEqual(["x", "y"]);
    expect(Object.keys(mergePlaces(runs, pageOf, "unanimous").answers)).toEqual(["x"]);
  });
  it("실행이 둘뿐이면 둘이 같아야 한다", () => {
    expect(Object.keys(mergePlaces([{ x: value(), y: value() }, { x: value(), y: other }], pageOf).answers)).toEqual(["x"]);
  });
  it("도표 묶음은 이름이 아니라 같이 든 실행의 수로 잇는다", () => {
    // 실행마다 묶음 이름이 다르다: 1회 A/B, 2회 X/Y, 3회는 전부 한 묶음.
    const run = (a: string, b: string) => ({ p: value({ chart: a }), q: value({ chart: a }), r: value({ chart: b }), s: value({ chart: b }) });
    const merged = mergePlaces([run("A", "B"), run("X", "Y"), run("K", "K")], pageOf);
    expect(merged.answers.p?.chart).toBe(merged.answers.q?.chart);
    expect(merged.answers.r?.chart).toBe(merged.answers.s?.chart);
    expect(merged.answers.p?.chart).not.toBe(merged.answers.r?.chart);
    expect(new Set(Object.values(merged.answers).map((answer) => answer.chart))).toEqual(new Set(["p1-m1", "p1-m2"]));
  });
  it("다른 쪽의 자리는 묶음 이름이 같아도 한 도표가 아니다", () => {
    const merged = mergePlaces([{ p: value(), q: value() }, { p: value(), q: value() }], (id) => (id === "p" ? 1 : 2));
    expect(merged.answers.p?.chart).toBe("p1-m1");
    expect(merged.answers.q?.chart).toBe("p2-m1");
  });
});

describe("채점", () => {
  const gold: ChartGold = {
    charts: [
      { id: "G1", doc: "a", page: 1, kind: "bar", title: "가상 배출량", series: ["계획", "실적"], bbox: [0, 0, 10, 10], value_texts_without_fact: [] },
      { id: "G2", doc: "a", page: 1, kind: "pie", title: "가상 비중", series: null, bbox: [0, 0, 10, 10], value_texts_without_fact: [] },
      { id: "G3", doc: "a", page: 2, kind: "pie", title: "값 없는 그림", series: null, bbox: [0, 0, 10, 10], value_texts_without_fact: [] },
    ],
    labels: [
      { doc: "a", id: "p", raw: "1", chart: "G1", role: "value", series: ["계획"], category: ["2023"] },
      { doc: "a", id: "q", raw: "2", chart: "G1", role: "value", series: ["실적"], category: ["2023"] },
      { doc: "a", id: "r", raw: "3", chart: "G2", role: "value", series: null, category: ["국내"] },
      { doc: "a", id: "s", raw: "4", chart: "G2", role: "value", series: null, category: ["해외"] },
      { doc: "a", id: "k", raw: "5", chart: "G1", role: "tick", series: null, category: null },
      { doc: "a", id: "o", raw: "6", chart: "", role: "other", series: null, category: null },
    ],
  };
  const pages = { a: new Map(["p", "q", "r", "s", "k", "o", "x"].map((id) => [id, { page: 1, bbox: [0, 0, 1, 1] }])), b: new Map() };
  const good = { a: { p: value(), q: value({ series: "실적" }), r: value({ chart: "B", title: "가상 비중", series: null, category: "국내" }), s: value({ chart: "B", title: "가상 비중", series: null, category: "해외" }), k: { ...other, role: "tick" as const }, o: other } };

  it("다 맞은 답은 다 맞게 센다", () => {
    const { json } = renderScores(gold, [{ name: "갈래", runs: [good], merged: null }], pages);
    const score = (json["갈래"] as Record<string, { labels: { category: { correct: number; attached: number }; series: { correct: number; attached: number; gold: number }; bothCorrect: number; nonValuesSeenAsValue: number }; grouping: { a: { found: string[]; exact: string[]; missed: string[]; falseCharts: string[]; chartsWithValues: number } }; titles: { same: number; found: number } }>)["갈래 1"]!;
    expect(score.labels.category).toMatchObject({ correct: 4, attached: 4 });
    expect(score.labels.series).toMatchObject({ correct: 2, attached: 2, gold: 2 });
    expect(score.labels.bothCorrect).toBe(4);
    expect(score.labels.nonValuesSeenAsValue).toBe(0);
    expect(score.grouping.a).toMatchObject({ found: ["G1", "G2"], exact: ["G1", "G2"], missed: ["G3"], falseCharts: [], chartsWithValues: 2 });
    expect(score.titles).toMatchObject({ same: 2, found: 2 });
  });

  it("계열과 범주를 서로 바꿔 적은 답은 둘 다 틀린 것이다", () => {
    const swapped = { a: { ...good.a, p: value({ series: "2023", category: "계획" }) } };
    const { json } = renderScores(gold, [{ name: "갈래", runs: [swapped], merged: null }], pages);
    const labels = (json["갈래"] as Record<string, { labels: { category: { correct: number; attached: number }; series: { correct: number; attached: number }; wrong: { what: string }[] } }>)["갈래 1"]!.labels;
    expect(labels.category).toMatchObject({ correct: 3, attached: 4 });
    expect(labels.series).toMatchObject({ correct: 1, attached: 2 });
    expect(labels.wrong.map((wrong) => wrong.what).sort()).toEqual(["category", "series"]);
  });

  it("도표 묶기: 두 도표를 한 묶음으로 내면 둘 다 꼭 맞지 않고, 정답 값이 없는 묶음은 도표가 아닌 묶음이다", () => {
    const charts = [{ id: "G1", page: 1, values: ["p", "q"] }, { id: "G2", page: 1, values: ["r", "s"] }];
    const one = scoreGrouping(charts, { p: { role: "value", group: "A" }, q: { role: "value", group: "A" }, r: { role: "value", group: "A" }, s: { role: "value", group: "A" }, x: { role: "value", group: "Z" } }, () => 1);
    expect(one.found.sort()).toEqual(["G1", "G2"]); // 절반 이상이 한 묶음에 들고 그 묶음의 절반이 제 것이다 (2/4)
    expect(one.exact).toEqual([]);
    expect(one.falseCharts).toEqual(["1|Z"]);
    const split = scoreGrouping(charts, { p: { role: "value", group: "A" }, q: { role: "other", group: null }, r: { role: "value", group: "B" }, s: { role: "value", group: null } }, () => 1);
    expect(split.found.sort()).toEqual(["G1", "G2"]);
    expect(split.exact).toEqual([]); // 값 자리를 다 담지 못했다
    const lost = scoreGrouping([{ id: "G1", page: 1, values: ["p", "q", "r"] }], { p: { role: "value", group: "A" } }, () => 1);
    expect(lost).toMatchObject({ found: [], missed: ["G1"] });
  });

  it("갈래의 가름과 실행 사이의 같음", () => {
    expect(scoreRoles([{ id: "k", role: "tick" }, { id: "o", role: "other" }, { id: "z", role: "other" }], (id) => ({ k: "tick", o: "value" })[id]).matrix).toEqual({ tick: { tick: 1 }, other: { value: 1, none: 1 } });
    expect(agreement(["p", "q", "r", "s"], [{ p: value(), q: value(), r: value() }, { p: value(), q: value(), r: other }, { p: value(), q: value({ category: "2024" }), r: { ...other, role: "tick" } }])).toEqual({ all: 1, majority: 1, none: 1, unanswered: 1 });
  });
});

describe("표로 세우는 변환의 입력", () => {
  it("답의 묶음이 도표가 되고, 값 후보가 없는 자리와 묶음 없는 자리는 빠진다", () => {
    const place = (id: string) => ({ page: 3, bbox: id === "p" ? [10, 10, 20, 20] : [40, 5, 60, 15] });
    const moved = toChartTableInput({ p: value(), q: value({ series: "실적" }), n: value({ chart: null }), w: value(), o: other }, place, (id) => (id === "w" ? null : `f-${id}`), "synthetic");
    expect(moved.input.charts).toEqual([{ id: "p3-gA", page: 3, bbox: [10, 5, 60, 20], title: "가상 배출량" }]);
    expect(moved.input.values.map((item) => [item.id, item.chart, item.series, item.category])).toEqual([["f-p", "p3-gA", "계획", "2023"], ["f-q", "p3-gA", "실적", "2023"]]);
    expect(moved.noGroup).toEqual(["n"]);
    expect(moved.noFact).toEqual(["w"]);
  });

  it("숫자 낱말을 값 후보로 잇는다: 그릇이 같고 글자 자리가 겹칠 때", () => {
    const fact = (id: string, where: Partial<Fact["where"]>): Fact => ({ id, where: { page: 1, sheet: null, gridId: null, row: null, col: null, ref: null, blockId: null, span: null, ...where } }) as Fact;
    const facts = [fact("p1-b2#n1", { blockId: "p1-b2", span: { start: 0, end: 3, occurrence: 1 } }), fact("p1-b2#n2", { blockId: "p1-b2", span: { start: 8, end: 12, occurrence: 1 } }), fact("p1-t1:r2c3", { gridId: "p1-t1", row: 2, col: 3 })];
    expect(factsOfLinks([{ container: "p1-b2", start: 8, end: 12 }], facts)).toEqual(["p1-b2#n2"]);
    expect(factsOfLinks([{ container: "p1-t1:r2c3", start: 0, end: 4 }, { container: "p1-b2", start: 4, end: 7 }], facts)).toEqual(["p1-t1:r2c3"]);
    expect(factsOfLinks([], facts)).toEqual([]);
  });
});

describe("runp 명령 (모의 공급사, synthetic)", () => {
  const bundle = (lines: Line[]): string => {
    const dir = mkdtempSync(join(tmpdir(), "vt1-"));
    mkdirSync(join(dir, "a"));
    writeFileSync(join(dir, "a", "tags.json"), JSON.stringify([{ page: 1, n: 1, bbox: [50, 100, 70, 108], id: "p1-w1" }, { page: 1, n: 2, bbox: [90, 100, 110, 108], id: "p1-w2" }]));
    writeFileSync(join(dir, "a", "raws.json"), JSON.stringify({ "p1-w1": "1,234", "p1-w2": "42.5%" }));
    writeFileSync(join(dir, "a", "page-lines.json"), JSON.stringify({ "1": { lines, visible_numbers: { year: 1, other: 0 }, tags: 2 } }));
    return dir;
  };
  const run = async (dir: string, extra: string[]): Promise<{ code: number; out: string[] }> => {
    const out: string[] = [];
    const code = await main(["runp", "--arm", "text", "--docs", `a=${join(dir, "a")}`, "--out", join(dir, "out"), ...extra], (line) => out.push(line), () => undefined);
    return { code, out };
  };
  const clean: Line[] = [{ x0: 10, x1: 20, y: 30, text: "가상 배출량 2023" }, { x0: 10, x1: 20, y: 40, text: "〔1〕 〔2〕 ▒%" }];

  it("dry-run 은 요청 글을 쓰고 어림을 낸다. 글에는 번호표와 ▒ 뿐이다", async () => {
    const dir = bundle(clean);
    const { code, out } = await run(dir, ["--dry-run"]);
    expect(code).toBe(0);
    expect(out.join("\n")).toContain("번호표 2");
    const report = JSON.parse(readFileSync(join(dir, "out", "dry-run.json"), "utf8")) as { calls: number; prompt: string; imageInputTokens: number };
    expect(report).toMatchObject({ calls: 1, prompt: "reconcile/label-chart-places-text@1", imageInputTokens: 0 });
  });

  it("글에 가리지 못한 숫자가 있으면 요청을 만들지 않는다", async () => {
    const dir = bundle([...clean, { x0: 1, x1: 2, y: 3, text: "합계 1,234" }]);
    const { code, out } = await run(dir, ["--dry-run"]);
    expect(code).toBe(1);
    expect(out.join("\n")).toContain("가리지 못한 숫자");
  });

  it("글에 요청에 없는 번호표가 있으면 요청을 만들지 않는다", async () => {
    const dir = bundle([...clean, { x0: 1, x1: 2, y: 3, text: "〔7〕" }]);
    expect((await run(dir, ["--dry-run"])).code).toBe(1);
  });

  it("모의 실행은 빈 답을 받아 답 없는 번호로 적는다 (성능과 무관)", async () => {
    const dir = bundle(clean);
    const { code } = await run(dir, ["--provider", "mock"]);
    expect(code).toBe(0);
    const outcome = JSON.parse(readFileSync(join(dir, "out", "outcome.json"), "utf8")) as { synthetic: string; pages: { unanswered: number[] }[] };
    expect(outcome.synthetic).toContain("모의");
    expect(outcome.pages[0]?.unanswered).toEqual([1, 2]);
    expect(JSON.parse(readFileSync(join(dir, "out", "tag-answers.json"), "utf8"))).toEqual({ a: {} });
  });
});
