import { describe, expect, it } from "vitest";
import { extractDocument, type DocModel } from "../src/index.js";
import { assignTags, codeLabels, labelText, linkFactsToCharts, matchCharts, scoreLabels, type ChartGold, type ChartLabels } from "../src/compare/charts.js";
import { chartLabelFor, idsInText, renderDocumentWithIds, unmaskedInIdDocument } from "../src/compare/prose.js";
import { predictor } from "../src/compare/cli4.js";
import { syntheticDocument } from "./helpers.js";

// 밴드 W (도표의 값에 계열·범주 붙이기)를 지어낸 문서로 본다 (synthetic). 어느 회사의 실제 값도 아니다.

const HEADER = { rows: [], cols: [], basis: "empty" };
/** 도표가 표로 잘못 실린 꼴: 한 칸에 값 둘이 이름 없이 섞여 있고, 옆 칸은 값 하나짜리 숫자 칸이다. */
const chartDoc = (first = "1,234", second = "5,678"): DocModel =>
  syntheticDocument("s", [{ id: "p1-t1", page: 1, cells: [["가상 배출량", ""], [`${first} ${second}`, "42.5%"], ["2023 2024", ""]], header: HEADER }], [{ id: "p1-b1", page: 1, text: "막대 위의 값은 910 톤이다.", bbox: [40, 300, 200, 310] }]);

const word = (container: string, start: number, end: number, text: string, x: number, extra: object = {}) => ({ container, start, end, text, bbox: [x, 100, x + 20, 108], chart: "p1-c1", role: "value" as const, ...extra });
const labelled = (title: string | null, series: string | null, category: string | null) => ({ coord: { category, status: "ok" }, elem: { category, status: "ok", series, series_status: "ok" }, label: { title, category, status: category === null ? "between_categories" : "ok", series, series_status: "ok" } });
const chartLabels = (): ChartLabels => ({
  charts: [{ id: "p1-c1", page: 1, bbox: [40, 80, 200, 160], form: "bar", orientation: "vertical", title: "가상 배출량", multi_series: true }],
  words: [
    word("p1-t1:r1c0", 0, 5, "1,234", 50, labelled("가상 배출량", "계획", "2023")),
    word("p1-t1:r1c0", 6, 11, "5,678", 90, labelled("가상 배출량", "실적", "단기(1년)")),
    word("p1-t1:r1c1", 0, 5, "42.5%", 130, labelled("가상 배출량", null, null)),
    { ...word("p1-t1:r2c0", 0, 4, "2023", 50), role: "category" as const },
  ],
});

describe("도표 낱말을 값 후보에 걸기와 번호표", () => {
  it("그릇과 글자 자리가 겹치는 낱말을 값 후보에 건다", () => {
    const extraction = extractDocument(chartDoc(), "a");
    const { linked, ambiguous } = linkFactsToCharts(extraction, chartLabels());
    expect(ambiguous).toEqual([]);
    expect(linked.map((item) => [item.id, item.word.text])).toEqual([["p1-t1:r1c0#n1", "1,234"], ["p1-t1:r1c0#n2", "5,678"], ["p1-t1:r1c1", "42.5%"]]);
  });

  it("번호표는 값 후보의 차례로 1 부터이고, 번호와 ID 의 잇기가 밀리지 않는다", () => {
    const { linked } = linkFactsToCharts(extractDocument(chartDoc(), "a"), chartLabels());
    const { tags, numberOf } = assignTags(linked);
    expect(tags.map((tag) => [tag.n, tag.id, tag.bbox[0]])).toEqual([[1, "p1-t1:r1c0#n1", 50], [2, "p1-t1:r1c0#n2", 90], [3, "p1-t1:r1c1", 130]]);
    expect(numberOf.get("p1-t1:r1c0#n2")).toEqual({ page: 1, n: 2 });
    // 값이 달라도(시험지 사본) 번호표의 수와 자리와 ID 는 같다
    const other = assignTags(linkFactsToCharts(extractDocument(chartDoc("9,999", "8,888"), "a"), chartLabels()).linked).tags;
    expect(other).toEqual(tags);
  });

  it("범주 축의 글자에는 번호표를 붙이지 않고, 값과 눈금에만 붙인다", () => {
    const { linked } = linkFactsToCharts(extractDocument(chartDoc(), "a"), chartLabels());
    const at = (id: string, x: number, role: "value" | "tick" | "category"): (typeof linked)[number] => ({ id, page: 1, chart: "p1-c1", bbox: [x, 200, x + 20, 208], word: { ...word(id, 0, 4, "0000", x), role } });
    const { tags, numberOf } = assignTags([at("axis-year", 10, "category"), ...linked, at("axis-tick", 300, "tick"), at("axis-year-2", 60, "category")]);
    expect(tags.map((tag) => [tag.n, tag.id])).toEqual([[1, "p1-t1:r1c0#n1"], [2, "p1-t1:r1c0#n2"], [3, "p1-t1:r1c1"], [4, "axis-tick"]]);
    expect(numberOf.has("axis-year")).toBe(false);
    expect(numberOf.has("axis-year-2")).toBe(false);
  });

  it("자리가 같은 값 후보는 번호 하나를 같이 쓴다", () => {
    const { linked } = linkFactsToCharts(extractDocument(chartDoc(), "a"), chartLabels());
    const twin = { ...(linked[0] as (typeof linked)[number]), id: "p1-t9:r0c0" };
    const { tags, numberOf } = assignTags([...linked, twin]);
    expect(tags).toHaveLength(3);
    expect(numberOf.get("p1-t9:r0c0")).toEqual({ page: 1, n: 1 });
    expect(tags[0]?.ids).toEqual(["p1-t1:r1c0#n1", "p1-t9:r0c0"]);
  });
});

describe("라벨을 실은 줄", () => {
  const labels = (): Record<string, string> => {
    const extraction = extractDocument(chartDoc(), "a");
    return Object.fromEntries(Object.entries(codeLabels(linkFactsToCharts(extraction, chartLabels()).linked)).map(([id, text]) => [`a:${id}`, text]));
  };

  it("범주를 못 붙인 값 후보에는 라벨을 싣지 않는다", () => {
    expect(Object.keys(labels())).toEqual(["a:p1-t1:r1c0#n1", "a:p1-t1:r1c0#n2"]);
    expect(labelText({ title: "제목", series: null, category: null })).toBeNull();
    expect(labelText({ title: null, series: "계획", category: "2023" })).toBe("계획 · 2023");
  });

  it("라벨이 있는 값 후보는 [라벨: ▒〔ID〕] 꼴이고 값의 글자는 어디에도 없다", () => {
    const model = chartDoc();
    const text = renderDocumentWithIds(model, extractDocument(model, "a"), { prefix: "a:", chartLabels: labels() });
    expect(text).toContain("[가상 배출량 · 계획 · 2023: ▒〔a:p1-t1:r1c0#n1〕]");
    expect(text).toContain("[가상 배출량 · 실적 · 단기(▒년): ▒〔a:p1-t1:r1c0#n2〕]");
    for (const raw of ["1,234", "5,678", "42.5", "910"]) expect(text).not.toContain(raw);
    expect(unmaskedInIdDocument(text)).toEqual([]);
    expect(idsInText(text).sort()).toEqual(["p1-b1#n1", "p1-t1:r1c0#n1", "p1-t1:r1c0#n2"]);
  });

  it("표의 숫자 칸에 라벨이 붙어도 그 칸은 가려진 채로 실린다", () => {
    const model = chartDoc();
    const text = renderDocumentWithIds(model, extractDocument(model, "a"), { prefix: "a:", chartLabels: { "a:p1-t1:r1c1": "가상 배출량 · 기준" } });
    expect(text.split("\n")).toContain("r1 | c0: ▒〔a:p1-t1:r1c0#n1〕 ▒〔a:p1-t1:r1c0#n2〕 | c1: [가상 배출량 · 기준: ▒%]");
    expect(text).not.toContain("42.5");
    expect(unmaskedInIdDocument(text)).toEqual([]);
  });

  it("글 블록 속 값 후보: 라벨이 있으면 대괄호로 싸고, 옵션이 없으면 그 줄이 밴드 U 의 꼴 그대로다", () => {
    const model = chartDoc();
    const extraction = extractDocument(model, "a");
    const line = "막대 위의 값은 ▒〔a:p1-b1#n1〕 톤이다.";
    expect(renderDocumentWithIds(model, extraction, { prefix: "a:" }).split("\n")).toContain(line);
    expect(renderDocumentWithIds(model, extraction, { prefix: "a:", chartLabels: {} }).split("\n")).toContain(line);
    expect(renderDocumentWithIds(model, extraction, { prefix: "a:", chartLabels: { "b:p1-b1#n1": "남의 라벨" } }).split("\n")).toContain(line);
    const labelled = renderDocumentWithIds(model, extraction, { prefix: "a:", chartLabels: { "a:p1-b1#n1": "가상 배출량 · 2024" } });
    expect(labelled.split("\n")).toContain("막대 위의 값은 [가상 배출량 · 2024: ▒〔a:p1-b1#n1〕] 톤이다.");
    expect(labelled).not.toContain("910");
  });

  it("라벨 글에 값 후보 꼴의 숫자가 있으면 가린다", () => {
    expect(chartLabelFor({ x: "묶음 [A] · 1,250 톤 · 2024" }, "x")).toBe("묶음 A · ▒ 톤 · 2024");
    expect(chartLabelFor({ x: "제목" }, "y")).toBeNull();
  });

  it("라벨 옵션을 끄면 요청 글이 옵션이 없을 때와 글자까지 같다", () => {
    const model = chartDoc();
    const extraction = extractDocument(model, "a");
    const plain = renderDocumentWithIds(model, extraction, { prefix: "a:" });
    expect(renderDocumentWithIds(model, extraction, { prefix: "a:", chartLabels: {} })).toBe(plain);
    expect(plain).not.toContain("[가상 배출량");
    // 옵션이 없을 때의 줄은 밴드 U 의 꼴 그대로다 (글자까지)
    expect(plain.split("\n")).toContain("r1 | c0: ▒〔a:p1-t1:r1c0#n1〕 ▒〔a:p1-t1:r1c0#n2〕 | c1: ▒%");
    // 다른 문서의 라벨은 실리지 않는다
    expect(renderDocumentWithIds(model, extraction, { prefix: "a:", chartLabels: { "b:p1-t1:r1c0#n1": "남의 라벨" } })).toBe(plain);
  });

  it("값이 다른 사본으로 조립해도 라벨을 실은 요청 글이 같다", () => {
    const original = chartDoc();
    const copy = chartDoc("9,999", "8,888");
    const render = (model: DocModel): string => renderDocumentWithIds(model, extractDocument(model, "a"), { prefix: "a:", chartLabels: labels() });
    expect(render(copy)).toBe(render(original));
  });
});

describe("계열의 분모", () => {
  it("값으로 보지 못한 자리도 계열의 분모에 든다 (계열을 붙이는 방법에서만)", () => {
    const labels: ChartGold["labels"] = [
      { doc: "a", id: "p1-t1:r1c0#n1", raw: "1,234", chart: "G1", role: "value", series: ["계획"], category: ["2023"] },
      { doc: "a", id: "missing", raw: "7", chart: "G1", role: "value", series: ["실적"], category: ["2024"] },
      { doc: "a", id: "missing-2", raw: "8", chart: "G1", role: "value", series: [null], category: ["2024"] },
    ];
    const { linked } = linkFactsToCharts(extractDocument(chartDoc(), "a"), chartLabels());
    const facts = { a: new Map(linked.map((item) => [item.id, item])), b: new Map() };
    const merged = scoreLabels(labels, predictor(facts, "label"));
    expect(merged.series).toEqual({ gold: 2, attached: 1, correct: 1, correctStrict: 1, none: 1 });
    expect(merged.seriesOnUnnamed).toEqual({ gold: 1, attached: 0 });
    const coord = scoreLabels(labels, predictor(facts, "coord"));
    expect(coord.series.gold).toBe(0); // 좌표는 계열을 붙이지 않는다: 계열을 채점하지 않는다
  });
});

describe("도표 찾기와 라벨의 채점", () => {
  const gold: ChartGold = {
    charts: [{ id: "G1", doc: "a", page: 1, kind: "bar_grouped", title: "가상 배출량", series: ["계획", "실적"], bbox: [40, 80, 200, 160], value_texts_without_fact: [] }, { id: "G2", doc: "a", page: 2, kind: "line_single", title: "가상 경로", series: null, bbox: [40, 80, 200, 160], value_texts_without_fact: [] }],
    labels: [
      { doc: "a", id: "v1", raw: "1,234", chart: "G1", role: "value", series: ["계획"], category: ["2023"] },
      { doc: "a", id: "v2", raw: "5,678", chart: "G1", role: "value", series: ["실적", "계획"], category: ["2024"] },
      { doc: "a", id: "v3", raw: "42.5%", chart: "G1", role: "value", series: [null], category: ["기준"] },
      { doc: "a", id: "t1", raw: "10.0", chart: "G1", role: "tick", series: null, category: null },
    ],
  };

  it("겹친 넓이가 작은 상자의 절반 이상이면 같은 도표다", () => {
    const match = matchCharts(gold.charts, [{ id: "f1", page: 1, bbox: [50, 90, 190, 150] }, { id: "f2", page: 1, bbox: [300, 90, 390, 150] }, { id: "f3", page: 3, bbox: [40, 80, 200, 160] }]);
    expect(match).toEqual({ matched: { G1: "f1" }, missed: ["G2"], extra: ["f2", "f3"] });
  });

  it("붙인 것·맞은 것·못 붙인 것을 따로 세고, 틀린 것과 없는 것을 구분한다", () => {
    const got: Record<string, { role: "value" | "not_value"; category: string | null; series?: string | null }> = {
      v1: { role: "value", category: "2023", series: "계획" },
      v2: { role: "value", category: "2023", series: "계획" }, // 범주 틀림, 계열은 둘째 답과 같아 느슨하게 맞음
      v3: { role: "value", category: null, series: "계획" }, // 못 붙임, 이름 없는 자리에 계열을 붙임
      t1: { role: "value", category: null },
    };
    const score = scoreLabels(gold.labels, (_doc, id) => got[id] ?? { role: "not_value", category: null });
    expect(score.values).toBe(3);
    expect(score.category).toEqual({ attached: 2, correct: 1, none: 1 });
    expect(score.series).toEqual({ gold: 2, attached: 2, correct: 2, correctStrict: 1, none: 0 });
    expect(score.seriesOnUnnamed).toEqual({ gold: 1, attached: 1 });
    expect(score.bothCorrect).toBe(1);
    expect([score.nonValues, score.nonValuesSeenAsValue]).toEqual([1, 1]);
    expect(score.wrong.map((item) => [item.id, item.what])).toEqual([["v2", "category"], ["v3", "series_on_unnamed"]]);
  });
});
