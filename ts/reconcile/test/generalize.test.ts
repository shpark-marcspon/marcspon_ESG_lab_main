import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { extractDocument, type DocModel } from "../src/index.js";
import { main, parseQuota, renderChartMatch } from "../src/compare/cli9.js";
import { checkDocKindGold, drawDocTargets, headerStats, scoreDocKinds, targetContext, type DocChartGold, type DocKindGold, type Quota } from "../src/compare/generalize.js";
import type { ValueKind } from "../src/domain/kinds.js";
import { asDocumentJson, syntheticDocument, tempDir, units } from "./helpers.js";

// 밴드 G1 (MARC-TICK-34). 문서와 숫자는 전부 지어낸 것이다 (synthetic).

function made(): DocModel {
  const rows = Array.from({ length: 12 }, (_, index) => [`가상 항목 ${index + 1}`, `${1000 + index * 37}`, `${2000 + index * 41}`]);
  return syntheticDocument(
    "g1",
    [
      { id: "p1-t1", cells: [["구분", "가상 지표 A", "가상 지표 B"], ...rows], before: ["가상 표 제목"] },
      { id: "p2-t1", page: 2, cells: [["11", "22"], ["33", "44"]], header: { rows: [], cols: [], basis: "synthetic" } },
    ],
    Array.from({ length: 9 }, (_, index) => ({ id: `p1-b${index + 1}`, text: `가상 사업장의 지어낸 수량은 ${300 + index * 13} 가상단위이고 지어낸 비율은 ${10 + index}.5% 이다.` })),
  );
}

const QUOTA: Quota = { sentence: 5, number_only_block: 5, table_text_cell: 5, table_cell: 6 };

describe("문서 하나의 표본 뽑기 (밴드 G1)", () => {
  const document = made();
  const extraction = extractDocument(document, "a", { titles: "own" });

  it("씨앗이 같으면 표본이 같고, 씨앗이 다르면 다르다. 갈래마다 정한 수만 뽑고 모자라면 전부다", () => {
    const first = drawDocTargets(extraction, 11, QUOTA);
    expect(drawDocTargets(extraction, 11, QUOTA)).toEqual(first);
    expect(drawDocTargets(extraction, 12, QUOTA).map((item) => item.id)).not.toEqual(first.map((item) => item.id));
    expect(first.filter((item) => item.bucket === "sentence")).toHaveLength(5);
    expect(first.filter((item) => item.bucket === "table_cell")).toHaveLength(6);
    expect(first.filter((item) => item.bucket === "number_only_block")).toHaveLength(extraction.facts.filter((fact) => fact.bucket === "number_only_block").length);
    expect(new Set(first.map((item) => item.id)).size).toBe(first.length);
  });

  it("앞뒤 글: 글 속 숫자는 〔〕로 싸고, 표 칸은 머리글과 같은 행을 싣는다", () => {
    const targets = drawDocTargets(extraction, 11, QUOTA);
    const sentence = targetContext(document, extraction, targets.find((item) => item.bucket === "sentence") as (typeof targets)[number]);
    expect(sentence.marked).toContain(`〔${sentence.raw}〕`);
    expect(sentence.table).toBeNull();
    const cell = targetContext(document, extraction, targets.find((item) => item.id.startsWith("p1-t1")) as (typeof targets)[number]);
    expect(cell.table?.colHeaders.join(" ")).toContain("가상 지표");
    expect(cell.table?.rowCells).toContain(cell.raw);
  });

  it("명령은 문서 경로를 받는다: draw 가 그 문서의 표본을 쓴다", async () => {
    const dir = tempDir();
    const path = join(dir, "document.json");
    writeFileSync(path, JSON.stringify(asDocumentJson(document)));
    const lines: string[] = [];
    expect(await main(["draw", "--doc", path, "--seed", "11", "--quota", "5,5,5,6", "--out", dir], (line) => lines.push(line))).toBe(0);
    const written = JSON.parse(readFileSync(join(dir, "kind-targets.json"), "utf8")) as { document: { file_name: string }; targets: { id: string }[] };
    expect(written.document.file_name).toBe("g1.synthetic.pdf");
    expect(written.targets.map((item) => item.id)).toEqual(drawDocTargets(extraction, 11, QUOTA).map((item) => item.id));
    expect(await main(["draw", "--seed", "11", "--quota", "5,5,5,6", "--out", dir], () => undefined)).toBe(2);
    expect(() => parseQuota("1,2,3")).toThrow();
  });
});

describe("문서 하나의 종류 채점 (밴드 G1)", () => {
  const document = made();
  const extraction = extractDocument(document, "a", { titles: "own" });
  const targets = drawDocTargets(extraction, 11, QUOTA);
  const kinds: (ValueKind | null)[] = ["value", "value", "value", "identifier", "count", null, "scale", "value", "duration", "value", "year_label"];
  const gold: DocKindGold = { note: "synthetic", status: "synthetic", document: { file_name: "g1.synthetic.pdf", sha256: "0".repeat(64) }, seed: 11, quota: QUOTA, items: targets.map((item, index) => ({ ...item, kind: kinds[index] as ValueKind | null, ambiguousWith: null, basis: "synthetic" })) };

  it("정답이 문서와 맞는지 본다", () => {
    expect(checkDocKindGold(gold, extraction)).toEqual([]);
    expect(checkDocKindGold({ ...gold, items: gold.items.slice(1) }, extraction).join("\n")).toContain("정답이 없다");
    expect(checkDocKindGold({ ...gold, seed: 12 }, extraction).length).toBeGreaterThan(0);
  });

  it("셈: 종류별 합이 표본 수와 같고, 「값을 거름」과 「값이 아닌 것을 남김」의 방향이 맞다", () => {
    // 규칙의 답(지어낸 것): 0번 값을 식별자로 거르고(값을 거름 1), 3번 식별자와 6번 눈금을 값으로 남긴다(남김 2). 나머지는 정답과 같다.
    const answer = gold.items.map((item, index) => (index === 0 ? "identifier" : index === 3 || index === 6 ? "value" : (item.kind ?? "value"))) as ValueKind[];
    const at = new Map(gold.items.map((item, index) => [item.id, answer[index] as ValueKind]));
    const result = scoreDocKinds(gold, (id) => at.get(id));
    expect(result.sampled).toBe(11);
    expect(result.unsure).toBe(1);
    expect(result.score.total).toBe(10);
    expect(result.score.correct).toBe(7);
    expect(result.valueFiltered.all).toEqual({ n: 1, of: 5 });
    expect(result.nonValueKept.all).toEqual({ n: 2, of: 5 });
    const sum = Object.values(result.confusion).reduce((total, row) => total + Object.values(row).reduce((x, y) => x + y, 0), 0);
    expect(sum).toBe(result.score.total);
    expect(Object.values(result.score.byBucket.all ?? {}).reduce((total, cell) => total + cell.gold, 0)).toBe(result.score.total);
    expect(result.confusion.value.identifier).toBe(1);
    expect(result.confusion.identifier.value + result.confusion.scale.value).toBe(2);
  });
});

describe("머리글 통계와 도표 대조 (밴드 G1)", () => {
  it("머리글이 선 표와, 열·행 머리글 열쇠가 둘 다 있는 숫자 칸을 센다", () => {
    const document = made();
    const stats = headerStats(document, extractDocument(document, "a", { titles: "own" }), units);
    expect(stats.tables).toBe(2);
    expect(stats.withBoth).toBe(1);
    expect(stats.cells).toBe(28);
    expect(stats.cellsWithBoth).toBe(24);
    expect(stats.cellsWithNeither).toBe(4);
    expect(stats.tablesWithNumbers).toBe(2);
    expect(stats.tablesAllCellsKeyed).toBe(1);
  });

  it("도표 대조: 찾은 것 + 못 찾은 것 = 정답, 찾은 것 + 정답에 없는 것 = 코드가 낸 것", () => {
    const chart = (id: string, page: number, bbox: number[], kind: DocChartGold["charts"][number]["kind"]): DocChartGold["charts"][number] => ({ id, page, kind, mixed: [], title: `가상 도표 ${id}`, valuesPrinted: "yes", borderline: false, bbox, basis: "synthetic" });
    const gold: DocChartGold = { note: "synthetic", status: "synthetic", document: { file_name: "g1.synthetic.pdf", sha256: "0".repeat(64) }, pages: 2, charts: [chart("G1", 1, [0, 0, 100, 100], "bar_v"), chart("G2", 1, [200, 0, 300, 100], "pie"), chart("G3", 2, [0, 0, 100, 100], "line")] };
    const rendered = renderChartMatch(gold, [{ id: "c1", page: 1, bbox: [10, 10, 90, 90] }, { id: "c2", page: 2, bbox: [300, 300, 400, 400] }]);
    expect(rendered.json).toMatchObject({ gold: 3, found: 2, matched: { G1: "c1" }, missed: ["G2", "G3"], extra: ["c2"] });
    expect(rendered.md).toContain("| 원·도넛 | 1 | 0/1 | 1/1 |");
  });
});
