import { describe, expect, it } from "vitest";
import { countRatio, extractDocument } from "../src/index.js";
import { syntheticDocument } from "./helpers.js";

const document = syntheticDocument(
  "facts",
  [
    {
      id: "p1-t1",
      before: ["환경 성과 (synthetic)", "(단위: tCO2eq)"],
      cells: [
        ["구분", "", "단위", "2023", "2024", "페이지"],
        ["온실가스 배출량1)", "직접(Scope 1)", "tCO2eq", "1,200", "1,350", "46"],
        ["", "간접(Scope 2)", "tCO2eq", "5,000", "-", "46"],
        ["합계", "", "tCO2eq", "6,200", "기준연도 대비 42% 감축", "47~48"],
        ["목표연도", "", "", "2030", "2050", ""],
      ],
      footnotes: [{ row: 1, col: 3, mark: "2)" }],
    },
    { id: "p1-t2", status: "low_confidence", header: { rows: [0], cols: [0], basis: "rows_before_first_numeric_row" }, cells: [["막대", "값"], ["가", "116,818 15,649"], ["나", "300"]] },
    { id: "p2-t1", page: 2, status: "failed", cells: [] },
  ],
  [
    { id: "p1-b1", text: "2025년 Scope 1 배출량은 22,253 tCO2eq이다." },
    { id: "p1-b2", text: "17", bbox: [280, 770, 300, 790] },
    { id: "p1-b3", text: "116,818", bbox: [100, 300, 150, 320] },
  ],
);
document.pages[1] = { page: 2, height: 800, text_layer: "none" };

describe("사실 후보 추출", () => {
  const extraction = extractDocument(document, "a");
  const byId = new Map(extraction.facts.map((fact) => [fact.id, fact]));

  it("표의 숫자 셀마다 원문 글자, 값, 위치, 각주, 표 상태를 싣는다", () => {
    expect(byId.get("p1-t1:r1c3")).toMatchObject({
      bucket: "table_cell",
      raw: "1,200",
      value: "1200",
      decimals: 0,
      footnoteMarks: ["2)"],
      tableStatus: "ok",
      where: { page: 1, gridId: "p1-t1", row: 1, col: 3 },
    });
  });

  it("행 머리와 열 머리 후보를 싣고, 병합 셀은 위 행에서 물려받았다고 표시한다", () => {
    expect(byId.get("p1-t1:r2c3")?.headers).toEqual({
      row: [
        { cell: "r1c0", text: "온실가스 배출량1)", inherited: true },
        { cell: "r2c1", text: "간접(Scope 2)", inherited: false },
        { cell: "r2c2", text: "tCO2eq", inherited: false },
      ],
      col: [{ cell: "r0c3", text: "2023", inherited: false }],
    });
    // 제 글자가 있는 행(`합계`)은 오른쪽 빈 칸을 윗 묶음에서 물려받지 않는다.
    expect(byId.get("p1-t1:r3c3")?.headers?.row.map((ref) => ref.text)).toEqual(["합계", "tCO2eq"]);
  });

  it("표 제목과 단위 줄 후보를 격자에 싣는다", () => {
    const grid = extraction.grids.find((item) => item.id === "p1-t1");
    // 가까운 것부터 세 개. 숫자뿐인 블록(p1-b2, p1-b3)은 빠진다.
    expect(grid?.titleCandidates).toEqual(["(단위: tCO2eq)", "환경 성과 (synthetic)", "2025년 Scope 1 배출량은 22,253 tCO2eq이다."]);
    expect(grid?.unitLines).toEqual(["(단위: tCO2eq)"]);
  });

  it("머리글의 연도, 쪽 번호 열, 값 없음 표시, 쪽 번호 블록은 값 후보가 아니다", () => {
    const classes = (raw: string) => extraction.nonValues.filter((item) => item.raw === raw).map((item) => item.class);
    expect(classes("2023")).toEqual(["header"]);
    expect(classes("46")).toEqual(["page_reference", "page_reference"]);
    expect(classes("47~48")).toEqual(["page_reference"]);
    expect(classes("-")).toEqual(["no_value_marker"]);
    expect(classes("17")).toEqual(["page_number"]);
    expect(byId.has("p1-t1:r1c5")).toBe(false);
  });

  it("본문 칸의 연도 꼴 숫자는 값 후보로 두되 표시를 단다", () => {
    expect(byId.get("p1-t1:r4c3")).toMatchObject({ value: "2030", flags: ["year_like"] });
  });

  it("글 셀 속 숫자, 문장 속 숫자, 숫자뿐인 블록을 갈래를 달아 뽑는다", () => {
    expect(byId.get("p1-t1:r3c4#n1")).toMatchObject({ bucket: "table_text_cell", raw: "42%", value: "42" });
    expect(byId.get("p1-b1#n3")).toMatchObject({ bucket: "sentence", raw: "22,253", value: "22253", where: { blockId: "p1-b1", span: { occurrence: 1 } } });
    expect(byId.get("p1-b3#n1")).toMatchObject({ bucket: "number_only_block", value: "116818" });
  });

  it("신뢰도 낮은 표의 숫자는 그 표시를 달고, 실패 표와 텍스트 없는 쪽은 따로 남긴다", () => {
    expect(byId.get("p1-t2:r2c1")).toMatchObject({ tableStatus: "low_confidence", value: "300" });
    expect(byId.get("p1-t2:r1c1#n2")).toMatchObject({ bucket: "table_text_cell", tableStatus: "low_confidence", value: "15649" });
    expect(extraction.failedTables).toEqual([{ id: "p2-t1", page: 2 }]);
    expect(extraction.pagesWithoutText).toEqual([2]);
  });

  it("문단 속의 값 후보 아닌 숫자도 갈래와 함께 남긴다", () => {
    expect(extraction.nonValues.filter((item) => item.where.blockId === "p1-b1").map((item) => [item.raw, item.class])).toEqual([
      ["2025", "year"],
      ["1", "identifier"],
      ["2", "identifier"],
    ]);
  });

  it("한 격자에 표가 위아래로 붙어 있으면 아래 표의 숫자는 가까운 중간 머리글 행을 열 머리로 쓴다", () => {
    const stacked = extractDocument(
      syntheticDocument("stacked", [
        {
          id: "p1-t1",
          cells: [
            ["자산", "금액", "비중"],
            ["대출", "100", "40%"],
            ["채권", "150", "60%"],
            ["자산", "Scope 1", "Scope 2"],
            ["대출", "7", "9"],
            ["비고", "전년 대비 10.7% 증가", "투자 418억 원"],
          ],
        },
      ]),
      "a",
    );
    expect(stacked.grids[0]?.innerHeaders).toEqual([3]);
    const byCell = new Map(stacked.facts.map((fact) => [fact.id, fact.headers?.col.map((ref) => ref.text)]));
    expect(byCell.get("p1-t1:r1c1")).toEqual(["금액"]);
    expect(byCell.get("p1-t1:r4c1")).toEqual(["Scope 1"]);
    expect(byCell.get("p1-t1:r4c2")).toEqual(["Scope 2"]);
    expect(stacked.facts.map((fact) => fact.id)).not.toContain("p1-t1:r3c1");
    // 글 속에 값 후보 숫자가 든 행은 머리글이 아니라 데이터 행이다.
    expect(stacked.facts.find((fact) => fact.id === "p1-t1:r5c1#n1")).toMatchObject({ bucket: "table_text_cell", raw: "10.7%" });
  });

  it("비율 세기: 갈래의 합이 값 후보 전체와 같다", () => {
    const ratio = countRatio(extraction);
    expect(ratio.valueCandidates).toBe(extraction.facts.length);
    expect(ratio.tableCellsOk + ratio.tableCellsLowConfidence + ratio.tableTextCellsOk + ratio.tableTextCellsLowConfidence + ratio.sentences + ratio.numberOnlyBlocks).toBe(ratio.valueCandidates);
    expect(ratio).toMatchObject({ tableCellsOk: 6, tableCellsLowConfidence: 1, tableTextCellsOk: 1, tableTextCellsLowConfidence: 2, sentences: 1, numberOnlyBlocks: 1, failedTables: 1, pagesWithoutText: 1 });
  });
});
