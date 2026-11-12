import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { extractDocument, extractGrids, indexPlaces, locatePlace, placeOfWhere, sameSource, scanNumbers, spanBoxes, type CharPlaces, type DocModel, type Grid, type PlaceContainer } from "../src/index.js";
import { charPlacesSchema, factPlaces } from "../src/compare/places-cli.js";
import { syntheticDocument } from "./helpers.js";

// 문서와 숫자, 좌표는 전부 지어낸 것이다 (synthetic).
// 가짜 글자 자리: 그릇마다 한 줄, 글자마다 폭 5pt. 빈칸에서 토막을 끊는다 (분해가 조각 사이에 끼운 빈칸처럼 좌표가 없다).
const W = 5;

function fakeContainer(id: string, kind: "block" | "cell", text: string, line: number, breakAt?: number): PlaceContainer {
  const runs: PlaceContainer["runs"] = [];
  let run: PlaceContainer["runs"][number] | null = null;
  for (let i = 0; i < text.length; i++) {
    if (text[i] === " ") {
      run = null;
      continue;
    }
    const second = breakAt !== undefined && i >= breakAt;
    const y0 = 100 + 20 * line + (second ? 12 : 0);
    const x = second ? 10 + W * (i - breakAt) : 10 + W * i;
    if (run === null || run.y0 !== y0) {
      run = { start: i, y0, y1: y0 + 10, x0: [], x1: [] };
      runs.push(run);
    }
    run.x0.push(x);
    run.x1.push(x + W);
  }
  const xs = runs.flatMap((r) => [...r.x0, ...r.x1]);
  return { id, kind, page: 1, bbox: [Math.min(...xs), Math.min(...runs.map((r) => r.y0)), Math.max(...xs), Math.max(...runs.map((r) => r.y1))], runs };
}

function fakePlaces(document: DocModel, options: { skip?: string[]; breaks?: Record<string, number> } = {}): CharPlaces {
  const containers: PlaceContainer[] = [];
  const unlinked: CharPlaces["unlinked"] = [];
  let line = 0;
  const add = (id: string, kind: "block" | "cell", text: string): void => {
    if (text === "") return;
    if (options.skip?.includes(id)) unlinked.push({ id, page: 1, reason: "text_differs_from_chunks" });
    else containers.push(fakeContainer(id, kind, text, line++, options.breaks?.[id]));
  };
  for (const block of document.blocks) if (block.kind !== "table") add(block.id, "block", block.text ?? "");
  for (const table of document.tables) table.cells.forEach((row, i) => row.forEach((text, j) => add(`${table.id}:r${i}c${j}`, "cell", text)));
  return { source: document.source, unit: "utf16", containers, unlinked };
}

const document = syntheticDocument(
  "places",
  [
    {
      id: "p1-t1",
      cells: [
        ["구분", "단위", "2023", "2024"],
        ["직접 배출", "tCO2eq", "1,200", "1,350"],
        ["간접 배출", "tCO2eq", "5,000", "기준연도 대비 42% 감축, 전년 대비 42% 증가"],
      ],
    },
  ],
  [
    { id: "p1-b1", text: "임직원 59,498명이 참여했고 협력사 500곳과 공급사 500곳이 함께했다." },
    { id: "p1-b2", text: "123,456", bbox: [100, 300, 150, 320] },
    { id: "p1-b3", text: "폐기물은 모두 1, 234 톤이었다." },
  ],
);
const extraction = extractDocument(document, "doc");
const places = fakePlaces(document, { breaks: { "p1-b3": "폐기물은 모두 1, ".length } });
const container = (id: string): PlaceContainer => places.containers.find((item) => item.id === id) as PlaceContainer;
const xOf = (id: string, text: string, needle: string, occurrence = 1): [number, number] => {
  let at = -1;
  for (let n = 0; n < occurrence; n++) at = text.indexOf(needle, at + 1);
  return [10 + W * at, 10 + W * (at + needle.length)];
};

describe("자리 ID → 쪽과 상자", () => {
  it("값 후보 ID 하나하나가 상자를 얻는다 (네 갈래)", () => {
    expect(new Set(extraction.facts.map((fact) => fact.bucket))).toEqual(new Set(["table_cell", "table_text_cell", "sentence", "number_only_block"]));
    for (const fact of extraction.facts) {
      const place = locatePlace(fact.id, extraction, places);
      expect(place.found, fact.id).toBe(true);
    }
    const rows = factPlaces(extraction, places);
    expect(rows.facts.filter((row) => row.found).length).toBe(extraction.facts.length);
  });

  it("표의 숫자 칸은 칸의 글 전체, 그릇의 상자와 같다", () => {
    const place = locatePlace("p1-t1:r1c2", extraction, places);
    expect(place).toMatchObject({ found: true, page: 1, boxes: [container("p1-t1:r1c2").bbox], covered: 5, length: 5 });
  });

  it("한글과 붙은 숫자는 숫자만 덮는다 (낱말 전체가 아니다)", () => {
    const text = "임직원 59,498명이 참여했고 협력사 500곳과 공급사 500곳이 함께했다.";
    const fact = extraction.facts.find((item) => item.raw === "59,498");
    const place = locatePlace(fact?.id ?? "", extraction, places);
    const [x0, x1] = xOf("p1-b1", text, "59,498");
    expect(place).toMatchObject({ found: true, boxes: [[x0, 100, x1, 110]] });
    const [, wordEnd] = xOf("p1-b1", text, "59,498명이");
    expect(x1).toBeLessThan(wordEnd);
    expect(place.found && (place.boxes[0] as number[])[2]).toBe(x1);
  });

  it("같은 숫자가 한 문단에 두 번이면 자리가 섞이지 않는다", () => {
    const text = "임직원 59,498명이 참여했고 협력사 500곳과 공급사 500곳이 함께했다.";
    const twins = extraction.facts.filter((item) => item.where.blockId === "p1-b1" && item.raw === "500");
    expect(twins.map((item) => item.where.span?.occurrence)).toEqual([1, 2]);
    const first = locatePlace(twins[0]?.id ?? "", extraction, places);
    const second = locatePlace(twins[1]?.id ?? "", extraction, places);
    expect(first.found && first.boxes[0]?.slice(0, 3)).toEqual([xOf("p1-b1", text, "500", 1)[0], 100, xOf("p1-b1", text, "500", 1)[1]]);
    expect(second.found && second.boxes[0]?.slice(0, 3)).toEqual([xOf("p1-b1", text, "500", 2)[0], 100, xOf("p1-b1", text, "500", 2)[1]]);
    expect(first).not.toEqual(second);
  });

  it("표의 글 칸 속 같은 숫자 둘도 따로다", () => {
    const text = "기준연도 대비 42% 감축, 전년 대비 42% 증가";
    const ids = ["p1-t1:r2c3#n1", "p1-t1:r2c3#n2"];
    const found = ids.map((id) => locatePlace(id, extraction, places));
    expect(found.map((place) => place.found && place.boxes[0]?.[0])).toEqual([xOf("", text, "42%", 1)[0], xOf("", text, "42%", 2)[0]]);
    expect(found.map((place) => place.found && place.boxes[0]?.[2])).toEqual([xOf("", text, "42%", 1)[1], xOf("", text, "42%", 2)[1]]);
  });

  it("범위가 두 줄에 걸치면 상자가 둘이다", () => {
    const target = container("p1-b3");
    const start = "폐기물은 모두 ".length;
    const { boxes, covered } = spanBoxes(target, start, start + "1, 234".length);
    expect(boxes.length).toBe(2);
    expect((boxes[1] as number[])[1]).toBeGreaterThan((boxes[0] as number[])[3] as number);
    expect(covered).toBe("1, 234".length - 1); // 끼운 빈칸에는 좌표가 없다
    // 분해가 줄 사이에 빈칸을 끼우므로 숫자 뽑기는 두 덩어리로 본다. 덩어리마다 상자가 하나다.
    const parts = extraction.facts.filter((item) => item.where.blockId === "p1-b3").map((item) => locatePlace(item.id, extraction, places));
    expect(parts.map((place) => place.found && place.boxes.length)).toEqual([1, 1]);
    // 앞 덩어리 `1` 은 쉼표를 빼고 덮는다 (쉼표는 숫자 덩어리 밖이다). 뒤 덩어리 `234` 는 둘째 줄의 상자와 같다.
    const [first, second] = boxes as unknown as [number[], number[]];
    expect(parts.map((place) => place.found && place.boxes[0])).toEqual([[first[0], first[1], (first[2] as number) - W, first[3]], second]);
  });

  it("같은 줄의 토막(글자 크기가 다른 조각)은 한 상자로 묶는다", () => {
    const mixed: PlaceContainer = { id: "x", kind: "block", page: 1, bbox: [10, 100, 30, 110], runs: [{ start: 0, y0: 100, y1: 110, x0: [10, 15], x1: [15, 20] }, { start: 2, y0: 102, y1: 109, x0: [20], x1: [24] }] };
    expect(spanBoxes(mixed, 0, 3)).toEqual({ boxes: [[10, 100, 24, 110]], covered: 3 });
    expect(spanBoxes(mixed)).toEqual({ boxes: [[10, 100, 24, 110]], covered: 3 });
    expect(spanBoxes(mixed, 3, 9)).toEqual({ boxes: [], covered: 0 });
  });

  it("없는 ID, 좌표 없는 문서, 잇지 못한 그릇, 엑셀의 자리는 까닭과 함께 못 찾음이다", () => {
    expect(locatePlace("p9-b9#n1", extraction, places)).toEqual({ found: false, reason: "unknown_id" });
    expect(locatePlace("p1-t1:r1c2", extraction, null)).toEqual({ found: false, reason: "no_places" });
    expect(locatePlace("p1-t1:r1c2", extraction, undefined)).toEqual({ found: false, reason: "no_places" });
    const partial = fakePlaces(document, { skip: ["p1-t1:r1c2"] });
    expect(locatePlace("p1-t1:r1c2", extraction, partial)).toEqual({ found: false, reason: "container_unlinked" });
    expect(locatePlace("p1-t1:r1c3", extraction, partial).found).toBe(true);
    expect(locatePlace("p1-t1:r1c2", extraction, { ...places, containers: [] })).toEqual({ found: false, reason: "container_missing" });
    const sheet: Grid = { id: "s1", docId: "x", kind: "sheet", page: null, sheet: "Sheet1", cells: [["구분", "값"], ["가", "12"]], header: { rows: [0], cols: [0], basis: "rows_before_first_numeric_row" }, status: "ok", footnoteMarks: [], titleCandidates: [], unitLines: [] };
    const excel = extractGrids([sheet], "x", "x.xlsx");
    expect(excel.facts.length).toBe(1);
    expect(locatePlace(excel.facts[0]?.id ?? "", excel, places)).toEqual({ found: false, reason: "not_in_document" });
    expect(placeOfWhere({ page: 1, sheet: null, gridId: null, row: null, col: null, ref: null, blockId: "p1-b1", span: { start: 900, end: 903, occurrence: 1 } }, indexPlaces(places))).toEqual({ found: false, reason: "no_glyphs_in_span" });
  });

  it("조각이 둘인 숫자 칸: 길이는 칸의 글 전체, 그릇의 상자는 모든 토막을 감싼다", () => {
    const doc = syntheticDocument("places-two", [{ id: "p1-t1", cells: [["구분", "값"], ["가", "△ 1,200"], ["나", "300"]] }]);
    const made = fakePlaces(doc, { breaks: { "p1-t1:r1c1": 2 } });
    const place = locatePlace("p1-t1:r1c1", extractDocument(doc, "doc"), made);
    expect(place).toMatchObject({ found: true, length: "△ 1,200".length, covered: "△ 1,200".length - 1 });
    expect(place.found && place.boxes.length).toBe(2);
    expect(place.found && place.containerBox).toEqual([10, (place.found && place.boxes[0]?.[1]) as number, 10 + W * 5, (place.found && place.boxes[1]?.[3]) as number]);
  });

  it("긴 토막에서도 셈한다", () => {
    const n = 300_000;
    const long: PlaceContainer = { id: "x", kind: "block", page: 1, bbox: [0, 0, n, 10], runs: [{ start: 0, y0: 0, y1: 10, x0: Array.from({ length: n }, (_, i) => i), x1: Array.from({ length: n }, (_, i) => i + 1) }] };
    expect(spanBoxes(long).boxes).toEqual([[0, 0, n, 10]]);
  });

  it("문서 모델과 글자 자리가 같은 PDF 의 것인지 본다", () => {
    expect(sameSource(document, places)).toBe(true);
    expect(sameSource(document, { ...places, source: { ...places.source, sha256: "f".repeat(64) } })).toBe(false);
  });
});

describe("자리의 단위 (UTF-16): py/docparse 와 같은 본보기를 읽는다", () => {
  const fixture = JSON.parse(readFileSync(join(import.meta.dirname, "..", "..", "..", "py", "docparse", "tests", "fixtures", "char-places-utf16.json"), "utf8")) as {
    text: string;
    container: PlaceContainer;
    expect: { raw: string; start: number; end: number; boxes: number[][] }[];
  };

  it("본보기의 글에는 한글과 보충 평면 글자가 있고, 글자 자리의 길이는 문자열의 길이와 같다", () => {
    expect(/[가-힣]/.test(fixture.text)).toBe(true);
    expect([...fixture.text].length).toBeLessThan(fixture.text.length);
    const last = fixture.container.runs[fixture.container.runs.length - 1] as PlaceContainer["runs"][number];
    expect(last.start + last.x0.length).toBe(fixture.text.length);
    expect(charPlacesSchema.shape.containers.element.safeParse(fixture.container).success).toBe(true);
  });

  it("숫자 뽑기가 낸 start·end 로 Python 이 기대한 상자를 그대로 얻는다", () => {
    const tokens = scanNumbers(fixture.text).filter((token) => token.kind === "value");
    expect(tokens.map((token) => ({ raw: token.raw, start: token.start, end: token.end }))).toEqual(fixture.expect.map(({ raw, start, end }) => ({ raw, start, end })));
    for (const want of fixture.expect) {
      const token = tokens.find((item) => item.raw === want.raw);
      expect(spanBoxes(fixture.container, token?.start, token?.end).boxes).toEqual(want.boxes);
      expect(fixture.text.slice(want.start, want.end)).toBe(want.raw);
    }
  });
});
