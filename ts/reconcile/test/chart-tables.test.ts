import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { documentSchema } from "../src/adapters/files.js";
import { buildChartTables, cellHeaders, inputFromCodeLabels, inputFromVisionAnswers, toDerivedId, toOriginalId, type ChartTableInput } from "../src/compare/chart-tables.js";
import { linkFactsToCharts, type ChartGold, type ChartLabels } from "../src/compare/charts.js";
import { pageOf, scorePlacement } from "../src/compare/cli10.js";
import { renderDocumentWithIds, unmaskedInIdDocument } from "../src/compare/prose.js";
import { extractDocument, type DocModel, type Fact } from "../src/index.js";

// 밴드 CT1 (이름 붙은 도표를 표로 세우기)를 지어낸 문서와 라벨로 본다 (synthetic). 어느 회사의 실제 값도 아니다.

const fixture = (name: string): string => fileURLToPath(new URL(`./fixtures/${name}`, import.meta.url));
const source = (): DocModel => JSON.parse(readFileSync(fixture("chart-doc.synthetic.json"), "utf8")) as DocModel;
const box = (x: number, y = 100): number[] => [x, y, x + 20, y + 8];

/** 계열 둘(계획, 실적)·범주 셋(2023~2025)인 도표 c1, 계열 없는 도표 c2(글 블록의 값 둘), 값 하나짜리 숫자 칸 c3. */
const input = (): ChartTableInput => ({
  source: "synthetic",
  charts: [
    { id: "p1-c1", page: 1, bbox: [50, 80, 300, 200], title: "가상 배출량", categories: ["2023", "2024", "2025"] },
    { id: "p1-c2", page: 1, bbox: [50, 205, 300, 225], title: null },
    { id: "p1-c3", page: 1, bbox: [320, 80, 500, 200], title: "가상 비율" },
  ],
  values: [
    { id: "p1-t1:r0c0#n1", chart: "p1-c1", series: "계획", category: "2023", bbox: box(60) },
    { id: "p1-t1:r0c0#n2", chart: "p1-c1", series: "계획", category: "2024", bbox: box(100) },
    { id: "p1-t1:r0c0#n3", chart: "p1-c1", series: "계획", category: "2025", bbox: box(140) },
    { id: "p1-t1:r0c0#n4", chart: "p1-c1", series: "실적", category: "2023", bbox: box(60, 120) },
    { id: "p1-t1:r0c0#n5", chart: "p1-c1", series: "실적", category: "2024", bbox: box(100, 120) },
    { id: "p1-t1:r0c0#n6", chart: "p1-c1", series: "실적", category: "2025", bbox: box(140, 120) },
    { id: "p1-b3#n1", chart: "p1-c2", series: null, category: "상반기", bbox: box(60, 210) },
    { id: "p1-b3#n2", chart: "p1-c2", series: null, category: "하반기", bbox: box(100, 210) },
    { id: "p1-t1:r0c1", chart: "p1-c3", series: null, category: "2025", bbox: box(330) },
  ],
});

const tableOf = (document: DocModel, id: string | null) => document.tables.find((table) => table.id === id) as DocModel["tables"][number];
const valuesOf = (facts: Fact[]): string[] => facts.map((fact) => `${fact.value}|${fact.percent}`).sort();

describe("도표를 표로 세우기", () => {
  it("계열 둘·범주 셋인 도표는 머리글이 선 2x3 표가 된다 (행 = 계열, 열 = 범주)", () => {
    const out = buildChartTables(source(), "a", input());
    const chart = out.charts[0]!;
    expect([chart.status, chart.rows, chart.cols, chart.singleUnnamedRow, chart.columnOrder]).toEqual(["full", 2, 3, false, "axis"]);
    const table = tableOf(out.document, chart.tableId);
    expect(table.cells).toEqual([["가상 배출량", "2023", "2024", "2025"], ["계획", "110", "120", "130"], ["실적", "210", "220", "230"]]);
    expect(table.header).toEqual({ rows: [0], cols: [0], basis: "rows_before_first_numeric_row" });
    expect([table.method, table.status]).toEqual(["text", "ok"]);
    // 읽는 쪽이 새 칸을 머리글이 선 표의 숫자 칸으로 읽는다
    const fact = extractDocument(out.document, "a").facts.find((item) => item.id === `${chart.tableId}:r2c3`)!;
    expect([fact.bucket, fact.value, fact.tableStatus]).toEqual(["table_cell", "230", "ok"]);
    expect([fact.headers?.row.map((ref) => ref.text), fact.headers?.col.map((ref) => ref.text)]).toEqual([["실적"], ["2025"]]);
  });

  it("계열 없는 도표는 한 행으로 서고, 열의 차례는 값 글자의 자리를 따른다", () => {
    const out = buildChartTables(source(), "a", input());
    const chart = out.charts[1]!;
    expect([chart.status, chart.rows, chart.cols, chart.singleUnnamedRow, chart.columnOrder]).toEqual(["full", 1, 2, true, "position"]);
    expect(tableOf(out.document, chart.tableId).cells).toEqual([["", "상반기", "하반기"], ["", "310", "320"]]);
  });

  it("한 범주에 값이 둘이면 표로 세우지 않고 까닭을 남긴다", () => {
    const twice = input();
    twice.values[1] = { ...twice.values[1]!, category: "2023" };
    const out = buildChartTables(source(), "a", twice);
    expect([out.charts[0]!.status, out.charts[0]!.reason, out.charts[0]!.tableId]).toEqual(["not_built", "duplicate_cell", null]);
    // 서지 못한 도표의 값은 원래 자리에 그대로다
    expect(tableOf(out.document, "p1-t1").cells[0]![0]).toBe("110 120 130 210 220 230");
    const noSeries = input();
    for (const value of noSeries.values) value.series = null;
    expect(buildChartTables(source(), "a", noSeries).charts[0]!.reason).toBe("duplicate_cell_no_series");
  });

  it("계열 이름이 있는 값과 없는 값이 섞이면 기본으로는 세우지 않고, 옵션을 주면 이름 없는 행으로 세운다", () => {
    const mixed = input();
    mixed.values[5] = { ...mixed.values[5]!, series: null, category: "목표" };
    expect(buildChartTables(source(), "a", mixed).charts[0]!.reason).toBe("mixed_series");
    const out = buildChartTables(source(), "a", mixed, { mixedSeries: "unnamed_row" });
    expect(tableOf(out.document, out.charts[0]!.tableId).cells.map((row) => row[0])).toEqual(["가상 배출량", "계획", "실적", ""]);
  });

  it("범주 라벨이 없는 값은 옮기지 않고 원래 자리에 두며, 남은 값의 바뀐 순번을 대응표가 잇는다", () => {
    const partial = input();
    partial.values[0] = { ...partial.values[0]!, category: null };
    const out = buildChartTables(source(), "a", partial);
    expect([out.charts[0]!.status, out.charts[0]!.left]).toEqual(["partial", ["p1-t1:r0c0#n1"]]);
    // 여섯 가운데 하나만 남은 칸은 칸 전체가 값 후보가 된다 (`#n` 없는 ID)
    expect(tableOf(out.document, "p1-t1").cells[0]![0]).toBe("110");
    expect(out.map.find((entry) => entry.from === "p1-t1:r0c0#n1")).toEqual({ id: "p1-t1:r0c0", from: "p1-t1:r0c0#n1", kind: "renumbered" });
    // 글 블록: 값 둘을 빼면 눈금 둘이 남는다. 눈금도 값 후보이고 순번이 #n3·#n4 에서 #n1·#n2 로 당겨진다
    expect(out.document.blocks.find((block) => block.id === "p1-b3")!.text).toBe("한 줄 도표의 값 그리고 눈금 0 100");
    expect(toDerivedId(out.map, "p1-b3#n4")).toBe("p1-b3#n2");
  });

  it("대응표가 새 칸 전부를 원래 ID 로 잇고 빠짐·겹침이 없다", () => {
    const out = buildChartTables(source(), "a", input());
    const before = extractDocument(source(), "a").facts;
    const after = extractDocument(out.document, "a").facts;
    const newTables = new Set(out.charts.map((chart) => chart.tableId));
    const newCells = after.filter((fact) => newTables.has(fact.where.gridId)).map((fact) => fact.id);
    const moved = out.map.filter((entry) => entry.kind === "moved");
    expect(newCells.length).toBe(9);
    expect(moved.map((entry) => entry.id).sort()).toEqual([...newCells].sort());
    expect(new Set(out.map.map((entry) => entry.from)).size).toBe(out.map.length);
    expect(new Set(out.map.map((entry) => entry.id)).size).toBe(out.map.length);
    // 한 칸씩: 원래 ID 로 되돌리면 그 값 후보이고, 새 칸의 머리글이 그 값의 라벨이다
    const byId = new Map(before.map((fact) => [fact.id, fact]));
    for (const label of input().values) {
      const id = toDerivedId(out.map, label.id);
      expect(toOriginalId(out.map, id)).toBe(label.id);
      const now = after.find((fact) => fact.id === id)!;
      expect(now.value).toBe(byId.get(label.id)!.value);
      expect(cellHeaders(out.document, extractDocument(out.document, "a"), id)).toEqual({ series: label.series ?? "", category: label.category });
    }
    // 대응표에 없는 값 후보는 ID 가 그대로다
    expect(toDerivedId(out.map, "p1-b4#n1")).toBe("p1-b4#n1");
    expect(after.some((fact) => fact.id === "p1-b4#n1")).toBe(true);
  });

  it("값 후보의 수와 값이 변환 앞뒤로 같고, 원래의 숫자 덩어리에 옮긴 값이 남지 않는다", () => {
    const out = buildChartTables(source(), "a", input());
    const before = extractDocument(source(), "a").facts;
    const after = extractDocument(out.document, "a").facts;
    expect(out.facts).toEqual({ before: before.length, after: before.length, moved: 9, renumbered: 2 });
    expect(valuesOf(after)).toEqual(valuesOf(before));
    const original = tableOf(out.document, "p1-t1");
    expect(original.cells).toEqual([["", ""], ["2023 2024 2025", ""], ["계획 실적", ""]]);
    for (const raw of ["110", "120", "130", "210", "220", "230", "42.5%"]) expect(original.cells.flat().join(" ")).not.toContain(raw);
    for (const raw of ["310", "320"]) expect(out.document.blocks.find((block) => block.id === "p1-b3")!.text).not.toContain(raw);
    // 각주 위첨자는 값을 따라 새 칸으로 간다
    expect(original.footnote_marks).toEqual([]);
    expect(tableOf(out.document, out.charts[2]!.tableId).footnote_marks).toEqual([{ row: 1, col: 1, mark: "1)" }]);
    // 원래 문서는 고치지 않는다
    expect(buildChartTables(source(), "a", input()).document).toEqual(out.document);
  });

  it("겹친 그릇이 같은 글자를 두 번 실은 자리는 첫째만 옮긴다", () => {
    const twin = input();
    twin.values.push({ id: "p1-b4#n1", chart: "p1-c3", series: null, category: "2025", bbox: box(330) });
    const doc = source();
    doc.blocks[3]!.text = "42.5%";
    const out = buildChartTables(doc, "a", twin);
    expect([out.charts[2]!.status, out.charts[2]!.values, out.charts[2]!.twins]).toEqual(["full", 1, ["p1-b4#n1"]]);
    expect(out.document.blocks.find((block) => block.id === "p1-b4")!.text).toBe("42.5%");
    expect(out.facts.after).toBe(out.facts.before);
  });

  it("파생 문서가 읽는 쪽의 형식을 통과하고, 새 표의 블록이 값이 있던 블록의 바로 뒤에 놓인다", () => {
    const out = buildChartTables(source(), "a", input());
    expect(documentSchema.safeParse(out.document).success).toBe(true);
    expect(out.document.blocks.map((block) => `${block.id}:${block.order}`)).toEqual(["p1-b1:1", "p1-b2:2", "p1-b5:2", "p1-b7:2", "p1-b3:3", "p1-b6:3", "p1-b4:4"]);
    expect(out.document.tables.map((table) => table.id)).toEqual(["p1-t1", "p1-t2", "p1-t3", "p1-t4"]);
    // 다른 언어 쪽(JSON 스키마)의 확인에 쓰는 본보기 파일과 글자까지 같다. `UPDATE_FIXTURES=1` 로만 다시 쓴다.
    const path = fixture("chart-doc.derived.synthetic.json");
    const text = `${JSON.stringify({ document: out.document, map: out.map, charts: out.charts }, null, 1)}\n`;
    if (process.env.UPDATE_FIXTURES === "1" || !existsSync(path)) writeFileSync(path, text);
    expect(readFileSync(path, "utf8")).toBe(text);
  });

  it("요청 글에 새 표가 다른 표와 같은 꼴로 실리고 값 후보가 전부 가려진다", () => {
    const out = buildChartTables(source(), "a", input());
    const text = renderDocumentWithIds(out.document, extractDocument(out.document, "a"), { prefix: "a:" });
    expect(unmaskedInIdDocument(text)).toEqual([]);
    expect(pageOf(text, 1)).toContain(["[표 p1-t2]", "r0 | c0: 가상 배출량 | c1: 2023 | c2: 2024 | c3: 2025", "r1 | c0: 계획 | c1: ▒ | c2: ▒ | c3: ▒", "r2 | c0: 실적 | c1: ▒ | c2: ▒ | c3: ▒", "[표 끝]"].join("\n"));
    // 값만 다른 사본(시험지)에 같은 라벨을 입히면 요청 글이 글자까지 같다 (값이 요청 글에 새지 않는다)
    const copy = source();
    copy.tables[0]!.cells[0]![0] = "999 120 130 210 220 230";
    const other = buildChartTables(copy, "a", input());
    expect(renderDocumentWithIds(other.document, extractDocument(other.document, "a"), { prefix: "a:" })).toBe(text);
  });
});

describe("라벨의 두 출처와 채점", () => {
  const labels = (): ChartLabels => ({
    charts: [{ id: "p1-c1", page: 1, bbox: [50, 80, 300, 200], form: "bar", orientation: "vertical", title: "가상 배출량", multi_series: true }],
    words: ["110", "120", "130"].map((text, index) => ({ container: "p1-t1:r0c0", start: index * 4, end: index * 4 + 3, text, bbox: box(60 + 40 * index), chart: "p1-c1", role: "value" as const, label: { category: ["2023", "2024", "2025"][index]!, status: "ok", title: "가상 배출량", series: index === 2 ? null : "계획", series_status: "ok" } })),
  });

  it("코드 라벨과 그림 라벨에서 같은 꼴의 입력을 만든다", () => {
    const { linked } = linkFactsToCharts(extractDocument(source(), "a"), labels());
    const code = inputFromCodeLabels(labels(), linked);
    expect(code.values.map((value) => [value.id, value.series, value.category])).toEqual([["p1-t1:r0c0#n1", "계획", "2023"], ["p1-t1:r0c0#n2", "계획", "2024"], ["p1-t1:r0c0#n3", null, "2025"]]);
    // 갈래가 값이 아닌 낱말(눈금, 범주 축의 글자)은 쓰지 않는다
    const withTick = labels();
    withTick.words.push({ container: "p1-t1:r0c1", start: 0, end: 5, text: "42.5%", bbox: box(330), chart: "p1-c1", role: "tick" as const });
    expect(inputFromCodeLabels(withTick, linkFactsToCharts(extractDocument(source(), "a"), withTick).linked).values.map((value) => value.id)).toEqual(code.values.map((value) => value.id));
    const answers = { "p1-t1:r0c0#n1": { role: "value", title: "그림이 읽은 제목", series: "실적", category: "2023" }, "p1-t1:r0c0#n2": { role: "tick", title: null, series: null, category: null } };
    const vision = inputFromVisionAnswers(labels(), linked, answers, "vision-synthetic");
    expect(vision.values.map((value) => [value.id, value.series, value.category])).toEqual([["p1-t1:r0c0#n1", "실적", "2023"]]);
    expect([vision.source, vision.charts[0]!.title]).toEqual(["vision-synthetic", "그림이 읽은 제목"]);
  });

  it("옳은 칸의 채점은 표의 머리글을 다시 읽고, 라벨의 틀림과 변환의 틀림을 가른다", () => {
    const wrong = input();
    wrong.values[0] = { ...wrong.values[0]!, category: "2024" };
    wrong.values[1] = { ...wrong.values[1]!, category: "2023" };
    const out = buildChartTables(source(), "a", wrong);
    const empty = buildChartTables(source(), "b", { source: "synthetic", charts: [], values: [] });
    const side = (o: typeof out, results = o.charts) => ({ original: source(), derived: o.document, map: o.map, input: wrong, results });
    const gold: ChartGold = {
      charts: [{ id: "G1", doc: "a", page: 1, kind: "bar_grouped", title: "가상 배출량", series: ["계획", "실적"], bbox: [50, 80, 300, 200], value_texts_without_fact: [] }],
      labels: [
        { doc: "a", id: "p1-t1:r0c0#n1", raw: "110", chart: "G1", role: "value", series: ["계획"], category: ["2023"] },
        { doc: "a", id: "p1-t1:r0c0#n3", raw: "130", chart: "G1", role: "value", series: ["계획"], category: ["2025"] },
        { doc: "a", id: "p1-b3#n1", raw: "310", chart: "G1", role: "value", series: [null], category: ["상반기"] },
        { doc: "a", id: "p1-b4#n1", raw: "55", chart: "G1", role: "value", series: null, category: ["2030"] },
        { doc: "a", id: "p1-b3#n4", raw: "100", chart: "G1", role: "tick", series: null, category: null },
        // 계열도 본다: 이름이 없어야 할 자리에 이름이 붙었거나, 이름이 있어야 할 자리가 이름 없는 행이면 틀린 칸이다
        { doc: "a", id: "p1-t1:r0c0#n4", raw: "210", chart: "G1", role: "value", series: [null], category: ["2023"] },
        { doc: "a", id: "p1-b3#n2", raw: "320", chart: "G1", role: "value", series: ["실적"], category: ["하반기"] },
        // 정답이 값이 아니라고 한 자리가 표에 놓이면 따로 센다
        { doc: "a", id: "p1-t1:r0c0#n5", raw: "220", chart: "G1", role: "tick", series: null, category: null },
      ],
    };
    const score = scorePlacement(gold, { a: side(out), b: { ...side(empty), input: { source: "synthetic", charts: [], values: [] } } });
    expect([score.values, score.moved, score.correct, score.wrongByLabel, score.wrongByTransform, score.nonValues, score.nonValuesMoved]).toEqual([6, 5, 2, 3, 0, 2, 1]);
    expect(score.wrong.map((item) => [item.id, item.got, item.blame])).toEqual([["p1-t1:r0c0#n1", "계획 · 2024", "라벨"], ["p1-t1:r0c0#n4", "실적 · 2023", "라벨"], ["p1-b3#n2", "(계열 이름 없음) · 하반기", "라벨"]]);
    expect(score.notMoved).toEqual({ "라벨이 값으로 보지 않음 (도표로 걸리지 않음 포함)": 1 });
    // 표의 머리글이 라벨과 달라지면(행과 열이 뒤바뀌는 따위) 변환의 틀림으로 센다
    const broken = structuredClone(out);
    const table = tableOf(broken.document, broken.charts[0]!.tableId);
    table.cells[0] = ["가상 배출량", "2025", "2024", "2023"];
    const again = scorePlacement(gold, { a: side(broken), b: { ...side(empty), input: { source: "synthetic", charts: [], values: [] } } });
    expect([again.correct, again.wrongByLabel, again.wrongByTransform]).toEqual([1, 3, 1]);
  });
});
