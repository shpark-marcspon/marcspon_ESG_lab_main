import type { Extraction } from "./facts.js";

// 표 숫자와 문장 숫자의 비율. 모델을 부르지 않고 사실 후보만 센다.

export interface RatioCount {
  docId: string;
  fileName: string;
  /** 값 후보 전체. 아래 여섯 갈래의 합이다. */
  valueCandidates: number;
  tableCellsOk: number;
  tableCellsLowConfidence: number;
  tableTextCellsOk: number;
  tableTextCellsLowConfidence: number;
  sentences: number;
  numberOnlyBlocks: number;
  /** 격자를 세우지 못한 표의 수. 그 안의 숫자 수는 모른다 (어느 갈래에도 들어 있지 않다). */
  failedTables: number;
  pagesWithoutText: number;
  tables: { ok: number; lowConfidence: number };
  /** 숫자가 있지만 값 후보가 아닌 것, 갈래별. */
  nonValues: Record<string, number>;
  nonValueTotal: number;
}

export function countRatio(extraction: Extraction): RatioCount {
  const count = (pick: (fact: Extraction["facts"][number]) => boolean): number => extraction.facts.filter(pick).length;
  const nonValues: Record<string, number> = {};
  for (const item of extraction.nonValues) nonValues[item.class] = (nonValues[item.class] ?? 0) + 1;
  return {
    docId: extraction.docId,
    fileName: extraction.fileName,
    valueCandidates: extraction.facts.length,
    tableCellsOk: count((fact) => fact.bucket === "table_cell" && fact.tableStatus === "ok"),
    tableCellsLowConfidence: count((fact) => fact.bucket === "table_cell" && fact.tableStatus === "low_confidence"),
    tableTextCellsOk: count((fact) => fact.bucket === "table_text_cell" && fact.tableStatus === "ok"),
    tableTextCellsLowConfidence: count((fact) => fact.bucket === "table_text_cell" && fact.tableStatus === "low_confidence"),
    sentences: count((fact) => fact.bucket === "sentence"),
    numberOnlyBlocks: count((fact) => fact.bucket === "number_only_block"),
    failedTables: extraction.failedTables.length,
    pagesWithoutText: extraction.pagesWithoutText.length,
    tables: {
      ok: extraction.grids.filter((grid) => grid.status === "ok").length,
      lowConfidence: extraction.grids.filter((grid) => grid.status === "low_confidence").length,
    },
    nonValues,
    nonValueTotal: extraction.nonValues.length,
  };
}

const pct = (part: number, whole: number): string => (whole === 0 ? "-" : `${((part * 1000) / whole / 10).toFixed(1)}%`);

/** 사람이 읽는 표 (마크다운). 비율의 분모는 그 문서의 값 후보 전체다. */
export function renderRatioTable(rows: RatioCount[]): string {
  const lines = [
    "| 문서 | 값 후보(분모) | 표 숫자 셀·정상 | 표 숫자 셀·신뢰도 낮음 | 표 글 셀 속·정상 | 표 글 셀 속·낮음 | 문장 속 | 숫자뿐인 블록 | 실패 표(숫자 수 모름) | 텍스트 없는 쪽 | 값 후보 아님 |",
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
  ];
  for (const row of rows) {
    const n = row.valueCandidates;
    const cell = (value: number): string => `${value} (${pct(value, n)})`;
    lines.push(
      `| ${row.fileName} | ${n} | ${cell(row.tableCellsOk)} | ${cell(row.tableCellsLowConfidence)} | ${cell(row.tableTextCellsOk)} | ${cell(row.tableTextCellsLowConfidence)} | ${cell(row.sentences)} | ${cell(row.numberOnlyBlocks)} | ${row.failedTables} | ${row.pagesWithoutText} | ${row.nonValueTotal} |`,
    );
  }
  return lines.join("\n");
}
