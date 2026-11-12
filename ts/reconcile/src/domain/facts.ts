import { dec, shift10, show } from "./dec.js";
import { classifyKinds, type ValueKind } from "./kinds.js";
import { classifyCell, normalizeText, scanNumbers, type CellKind, type NotationFlag, type SignRule, type TokenKind } from "./notation.js";

// 사실 후보 추출. 문서 모델(`schemas/document.schema.json`)과 엑셀 격자에서 숫자를 전부 찾아 ID 를 붙인다.
// 여기서 정하는 것은 「어디에 어떤 글자로 있는가」와 「값 후보인가」까지다. 뜻(지표·기간·단위·범위)은 붙이지 않는다.

/** 이 패키지가 문서 모델에서 읽는 부분. 형식의 정본은 `schemas/document.schema.json`. */
export interface DocModel {
  source: { file_name: string; sha256: string; page_count: number };
  pages: { page: number; height: number; text_layer: "present" | "none" }[];
  blocks: { id: string; kind: "paragraph" | "table" | "heading"; page: number; bbox: number[]; order: number; text?: string | undefined; table_id?: string | undefined }[];
  tables: {
    id: string;
    page: number;
    cells: string[][];
    header: { rows: number[]; cols: number[]; basis: string };
    method: "lines" | "text" | "none";
    status: "ok" | "low_confidence" | "failed";
    footnote_marks: { row: number; col: number; mark: string }[];
  }[];
}

/** 엑셀 숫자 셀의 저장 값과 화면 표기. 둘이 다를 수 있어 둘 다 남긴다. */
export interface StoredNumber {
  /** 파일에 저장된 글자 그대로 (`<v>` 의 내용). */
  storedRaw: string;
  /** 저장 값의 십진 문자열 (지수 표기를 풀었다). 이진 부동소수의 꼬리(`0.30000000000000004`)도 그대로다. */
  stored: string;
  formatCode: string;
  /** 서식을 적용한 화면 표기. 지원하지 않는 서식이면 null. */
  display: string | null;
  displayStatus: "rendered" | "general_approximated" | "unsupported_format";
  isDate: boolean;
  formula: boolean;
}

/** 표 하나 또는 시트 하나의 격자. 뜻 붙이기의 단위다. */
export interface Grid {
  id: string;
  docId: string;
  kind: "pdf_table" | "sheet";
  page: number | null;
  sheet: string | null;
  cells: string[][];
  header: { rows: number[]; cols: number[]; basis: string };
  status: "ok" | "low_confidence";
  footnoteMarks: { row: number; col: number; mark: string }[];
  /** 표 바로 앞의 글 블록 (가까운 것부터 최대 3개, 숫자뿐인 블록은 뺀다). 제목일 수도 아닐 수도 있다. */
  titleCandidates: string[];
  /** `단위:` 가 든 줄. */
  unitLines: string[];
  /**
   * 본문 중간의 머리글 행. 한 격자에 표가 위아래로 붙어 있으면 아래 표의 머리글이 본문 행으로 들어온다.
   * 값 칸에 값 후보 숫자도 값 없음 표시도 없고 글자 칸이 둘 이상인 행을 그렇게 본다 (추정, 정상 표만). `innerHeaderRows` 로 구한다.
   */
  innerHeaders?: number[];
  /** 엑셀 숫자 셀. 그 밖의 셀은 null. */
  numeric?: (StoredNumber | null)[][];
  /** 엑셀 셀 주소 (`D5`). */
  refs?: string[][];
}

export interface HeaderRef {
  /** 머리글 셀의 ID (격자 안의 `r0c3`). */
  cell: string;
  text: string;
  /** 병합 셀이라 비어 있어서 위(행 머리)나 왼쪽(열 머리)의 글자를 물려받았다. 추정이다. */
  inherited: boolean;
}

export type FactBucket =
  | "table_cell" // 표의 숫자 셀
  | "table_text_cell" // 표의 글 셀 속 숫자
  | "sentence" // 문단·제목 속 숫자
  | "number_only_block"; // 숫자뿐인 블록 (도표의 숫자 등)

export interface FactWhere {
  page: number | null;
  sheet: string | null;
  gridId: string | null;
  row: number | null;
  col: number | null;
  /** 엑셀 셀 주소. */
  ref: string | null;
  blockId: string | null;
  /** 글 속 숫자일 때: 원문에서의 자리와, 같은 글자가 몇 번째 나온 것인지. */
  span: { start: number; end: number; occurrence: number } | null;
}

/** 값 후보 하나. */
export interface Fact {
  id: string;
  docId: string;
  bucket: FactBucket;
  where: FactWhere;
  /** 원문 글자. 표 셀이면 셀 전체, 글 속이면 그 숫자 덩어리. */
  raw: string;
  /** 읽은 값 (십진 문자열). 수 단위 낱말은 곱하지 않은 표기 그대로의 수. */
  value: string;
  decimals: number;
  sign: SignRule;
  percent: boolean;
  unitSuffix: string | null;
  flags: NotationFlag[];
  footnoteMarks: string[];
  /** 표에서 나온 숫자면 그 표의 추출 상태. */
  tableStatus: "ok" | "low_confidence" | null;
  headers: { row: HeaderRef[]; col: HeaderRef[] } | null;
  stored: StoredNumber | null;
  storedAsText: boolean;
}

export type NonValueClass = Exclude<CellKind, "blank" | "value" | "text" | "text_with_digits" | "multi_number"> | Exclude<TokenKind, "value"> | "header" | "page_reference" | "page_number" | "formula_without_result";

/** 숫자가 있지만 값 후보가 아닌 것. 버리지 않고 갈래와 함께 남긴다. */
export interface NonValue {
  docId: string;
  where: FactWhere;
  raw: string;
  class: NonValueClass;
}

export interface Extraction {
  docId: string;
  fileName: string;
  grids: Grid[];
  facts: Fact[];
  nonValues: NonValue[];
  /** 격자를 세우지 못한 표. 안의 숫자 수는 모른다. */
  failedTables: { id: string; page: number }[];
  /** 텍스트 계층이 없는 쪽 (스캔본 등). 숫자가 있어도 읽지 못한다. */
  pagesWithoutText: number[];
  /** 값 후보의 종류 (ID → 종류). `kinds` 옵션을 켰을 때만 있다. 값 후보 목록은 종류와 무관하게 같다. */
  kinds?: Record<string, ValueKind>;
}

/** 엑셀의 수식 셀에 저장된 결과가 없을 때 격자에 싣는 글자. 값을 지어내지 않고 「읽지 못한 숫자」로 남긴다. */
export const FORMULA_WITHOUT_RESULT = "#수식 결과 없음";

const PAGE_REFERENCE_HEADER = /페이지|쪽수|^쪽$|page|보고 ?위치|문단 ?번호|조항/iu;
const UNIT_LINE = /단위\s*[:：]/u;
const TITLE_MAX = 200;

export const cellId = (row: number, col: number): string => `r${row}c${col}`;

function isDataNumber(text: string): boolean {
  const notation = classifyCell(text);
  return notation.kind === "value" && !notation.number?.flags.includes("year_like");
}

/** 머리글 행·열 추정. `py/docparse` 의 `estimate_header` 와 같은 규칙이다 (엑셀 격자에 쓴다). */
export function estimateHeader(cells: string[][]): Grid["header"] {
  if (cells.length === 0) return { rows: [], cols: [], basis: "empty" };
  const firstData = cells.findIndex((row) => row.some(isDataNumber));
  let rows: number[];
  let basis: string;
  if (firstData === -1) {
    rows = [0];
    basis = "no_numeric_row";
  } else {
    rows = Array.from({ length: firstData }, (_, i) => i);
    basis = "rows_before_first_numeric_row";
    while (rows.length > 1 && (cells[rows[rows.length - 1] as number] as string[]).filter((c) => c !== "").length <= 1) rows.pop();
  }
  const body = cells.slice(rows.length);
  const cols: number[] = [];
  const width = (cells[0] as string[]).length;
  for (let j = 0; j < width; j++) {
    if (body.some((row) => isDataNumber(row[j] ?? "") || classifyCell(row[j] ?? "").kind === "no_value_marker")) break;
    cols.push(j);
  }
  return { rows, cols, basis };
}

/**
 * 본문 중간의 머리글 행 (추정): 첫 값 행보다 아래에 있고, 값 칸(머리글 열이 아닌 칸)에 값 후보 숫자·값 없음 표시가 없고 글자 칸이 둘 이상인 행.
 * 글 속에 값 후보 숫자가 든 칸(`10.7%`, `418억 원`)이 있으면 데이터 행이다. `Scope 1` 처럼 식별자뿐인 칸은 글자 칸으로 센다.
 * 추출 상태가 정상인 표에서만 본다 (신뢰도 낮은 표는 구조를 믿을 수 없어 손대지 않는다).
 */
export function innerHeaderRows(grid: Pick<Grid, "cells" | "header" | "status">): number[] {
  if (grid.status !== "ok") return [];
  const headerRows = new Set(grid.header.rows);
  const headerCols = new Set(grid.header.cols);
  const rows: number[] = [];
  let seenData = false;
  grid.cells.forEach((cells, row) => {
    if (headerRows.has(row)) return;
    const kinds = cells.map((cell, col) => (headerCols.has(col) ? "skip" : classifyCell(cell).kind));
    const data =
      kinds.some((kind) => kind === "value" || kind === "no_value_marker" || kind === "multi_number" || kind === "range") ||
      cells.some((cell, col) => kinds[col] === "text_with_digits" && scanNumbers(cell).some((token) => token.kind === "value"));
    if (data) seenData = true;
    else if (seenData && kinds.filter((kind) => kind === "text" || kind === "year" || kind === "text_with_digits").length >= 2) rows.push(row);
  });
  return rows;
}

/** 이 행에 적용할 머리글 행: 위쪽에 본문 중간의 머리글 행이 있으면 가장 가까운 묶음, 없으면 표 맨 위의 머리글 행. */
function headerRowsFor(grid: Grid, row: number): number[] {
  const inner = (grid.innerHeaders ?? []).filter((r) => r < row);
  if (inner.length === 0) return grid.header.rows;
  const block = [inner[inner.length - 1] as number];
  for (let i = inner.length - 2; i >= 0 && inner[i] === (block[0] as number) - 1; i--) block.unshift(inner[i] as number);
  return block;
}

function headersFor(grid: Grid, row: number, col: number): { row: HeaderRef[]; col: HeaderRef[] } {
  const headerRows = new Set([...grid.header.rows, ...(grid.innerHeaders ?? [])]);
  const rowRefs: HeaderRef[] = [];
  // 이 행에 제 글자가 있는 머리글 열이 나오면, 그 오른쪽의 빈 칸은 위에서 물려받지 않는다 (`합계` 행이 윗 묶음의 이름을 물려받지 않게).
  let ownSeen = false;
  for (const j of grid.header.cols) {
    const own = normalizeText(grid.cells[row]?.[j] ?? "");
    if (own !== "") {
      ownSeen = true;
      rowRefs.push({ cell: cellId(row, j), text: own, inherited: false });
      continue;
    }
    if (ownSeen) continue;
    let r = row - 1;
    while (r >= 0 && !headerRows.has(r) && normalizeText(grid.cells[r]?.[j] ?? "") === "") r -= 1;
    if (r < 0 || headerRows.has(r)) continue;
    rowRefs.push({ cell: cellId(r, j), text: normalizeText(grid.cells[r]?.[j] ?? ""), inherited: true });
  }
  const colRefs: HeaderRef[] = [];
  for (const i of headerRowsFor(grid, row)) {
    let c = col;
    while (c >= 0 && normalizeText(grid.cells[i]?.[c] ?? "") === "") c -= 1;
    if (c < 0) continue;
    colRefs.push({ cell: cellId(i, c), text: normalizeText(grid.cells[i]?.[c] ?? ""), inherited: c !== col });
  }
  return { row: rowRefs, col: colRefs };
}

function baseWhere(grid: Grid, row: number, col: number): FactWhere {
  return { page: grid.page, sheet: grid.sheet, gridId: grid.id, row, col, ref: grid.refs?.[row]?.[col] ?? null, blockId: null, span: null };
}

/** 격자 하나에서 값 후보와 그 밖의 숫자를 뽑는다. */
export function gridFacts(grid: Grid): { facts: Fact[]; nonValues: NonValue[] } {
  const facts: Fact[] = [];
  const nonValues: NonValue[] = [];
  grid.innerHeaders ??= innerHeaderRows(grid);
  const headerRows = new Set([...grid.header.rows, ...grid.innerHeaders]);
  const headerCols = new Set(grid.header.cols);
  grid.cells.forEach((cells, row) => {
    cells.forEach((raw, col) => {
      const where = baseWhere(grid, row, col);
      const stored = grid.numeric?.[row]?.[col] ?? null;
      const notation = classifyCell(raw);
      if (raw === FORMULA_WITHOUT_RESULT) {
        nonValues.push({ docId: grid.docId, where, raw, class: "formula_without_result" });
        return;
      }
      if (notation.kind === "blank" && !stored) return;
      if (notation.kind === "text" && !stored) return;
      const inHeader = headerRows.has(row) || headerCols.has(col);
      if (inHeader) {
        if (notation.kind !== "no_value_marker") nonValues.push({ docId: grid.docId, where, raw, class: "header" });
        return;
      }
      const headers = headersFor(grid, row, col);
      const marks = grid.footnoteMarks.filter((mark) => mark.row === row && mark.col === col).map((mark) => mark.mark);
      const common = { docId: grid.docId, footnoteMarks: marks, tableStatus: grid.status, headers } as const;
      const pageRef = headers.col.some((ref) => PAGE_REFERENCE_HEADER.test(ref.text));

      if (stored) {
        if (stored.isDate) {
          nonValues.push({ docId: grid.docId, where, raw: stored.display ?? stored.storedRaw, class: "date" });
          return;
        }
        const negative = stored.stored.startsWith("-");
        // 퍼센트 서식: 저장 값 0.123 은 12.3% 다. % 단위의 값으로 맞춘다 (자리 옮김뿐이다).
        const percent = stored.displayStatus === "rendered" && (stored.display ?? "").includes("%");
        const value = percent ? show(shift10(dec(stored.stored), 2)) : stored.stored;
        facts.push({
          ...common,
          id: `${grid.id}:${cellId(row, col)}`,
          bucket: "table_cell",
          where,
          raw: stored.display ?? stored.storedRaw,
          value,
          decimals: value.includes(".") ? (value.split(".")[1] as string).length : 0,
          sign: negative ? "minus" : "none",
          percent,
          unitSuffix: percent ? "%" : null,
          flags: percent ? ["percent_format_scaled"] : [],
          stored,
          storedAsText: false,
        });
        return;
      }
      if (notation.kind === "value" && notation.number) {
        if (pageRef) {
          nonValues.push({ docId: grid.docId, where, raw, class: "page_reference" });
          return;
        }
        facts.push({
          ...common,
          id: `${grid.id}:${cellId(row, col)}`,
          bucket: "table_cell",
          where,
          raw,
          value: notation.number.value,
          decimals: notation.number.decimals,
          sign: notation.number.sign,
          percent: notation.number.percent,
          unitSuffix: notation.number.unitSuffix,
          flags: notation.number.flags,
          stored: null,
          storedAsText: grid.kind === "sheet",
        });
        return;
      }
      if (notation.kind === "no_value_marker" || notation.kind === "year" || notation.kind === "code" || notation.kind === "date") {
        nonValues.push({ docId: grid.docId, where, raw, class: notation.kind });
        return;
      }
      // 범위, 숫자 여럿, 글 속 숫자: 셀 안의 숫자 덩어리를 하나씩 본다.
      if (pageRef) {
        nonValues.push({ docId: grid.docId, where, raw, class: "page_reference" });
        return;
      }
      scanNumbers(raw).forEach((token, index) => {
        const tokenWhere: FactWhere = { ...where, span: { start: token.start, end: token.end, occurrence: token.occurrence } };
        if (token.kind !== "value" || !token.number) {
          nonValues.push({ docId: grid.docId, where: tokenWhere, raw: token.raw, class: token.kind as NonValueClass });
          return;
        }
        if (notation.kind === "range") {
          nonValues.push({ docId: grid.docId, where: tokenWhere, raw: token.raw, class: "range_part" });
          return;
        }
        facts.push({
          ...common,
          id: `${grid.id}:${cellId(row, col)}#n${index + 1}`,
          bucket: "table_text_cell",
          where: tokenWhere,
          raw: token.raw,
          value: token.number.value,
          decimals: token.number.decimals,
          sign: token.number.sign,
          percent: token.number.percent,
          unitSuffix: token.number.unitSuffix,
          flags: token.number.flags,
          stored: null,
          storedAsText: false,
        });
      });
    });
  });
  return { facts, nonValues };
}

/** 쪽 번호로 볼 조건: 숫자뿐인 블록이고, 쉼표 없는 네 자리 이하 정수이고, 쪽의 위·아래 가장자리(높이의 8%)에 있다. */
const PAGE_MARGIN_RATIO = 0.08;

function isPageNumber(raw: string, bbox: number[], pageHeight: number | undefined): boolean {
  if (!/^\d{1,4}$/.test(normalizeText(raw)) || pageHeight === undefined) return false;
  const [, y0 = 0, , y1 = 0] = bbox;
  return y1 <= pageHeight * PAGE_MARGIN_RATIO || y0 >= pageHeight * (1 - PAGE_MARGIN_RATIO);
}

export interface ExtractOptions {
  /**
   * 표 제목 후보를 고르는 방법. 기본 `before3` 은 표 앞의 글 블록 셋이다 (옆 표의 제목·각주가 섞일 수 있다).
   * `own` 은 그 표의 제목과 그 표의 각주만 싣는다: 앞 표 뒤부터 이 표 앞까지의 글 중 각주 꼴이 아닌 것(가까운 것부터 최대 3개)과,
   * 이 표 바로 뒤에 이어지는 각주 꼴의 글. 각주 꼴은 줄 머리의 표시(`1)`, `*`, `※`, `주1)`)로 본다 (추정).
   */
  titles?: "before3" | "own";
  /** 값 후보마다 종류(값 / 식별자 / 기간 / 개수 / 연도 표시 / 눈금)를 붙인다 (`src/domain/kinds.ts`). 기본은 끔. 켜도 값 후보는 버리지 않는다. */
  kinds?: boolean;
}

const FOOTNOTE_LINE = /^\s*(?:\d{1,2}\)|\*|※|주\s*\d*\))/u;

/** 문서 모델 하나에서 격자와 사실 후보를 뽑는다. */
export function extractDocument(document: DocModel, docId: string, options: ExtractOptions = {}): Extraction {
  const grids: Grid[] = [];
  const facts: Fact[] = [];
  const nonValues: NonValue[] = [];
  const failedTables: { id: string; page: number }[] = [];
  const heights = new Map(document.pages.map((page) => [page.page, page.height]));
  const blocksByPage = new Map<number, DocModel["blocks"]>();
  for (const block of document.blocks) {
    const list = blocksByPage.get(block.page) ?? [];
    list.push(block);
    blocksByPage.set(block.page, list);
  }
  const tableBlock = new Map(document.blocks.filter((block) => block.kind === "table").map((block) => [block.table_id as string, block]));

  for (const table of document.tables) {
    if (table.status === "failed") {
      failedTables.push({ id: table.id, page: table.page });
      continue;
    }
    const block = tableBlock.get(table.id);
    const before = (blocksByPage.get(table.page) ?? [])
      .filter((other) => other.kind !== "table" && block !== undefined && other.order < block.order && ["text", "text_with_digits"].includes(classifyCell(other.text ?? "").kind))
      .sort((a, b) => b.order - a.order)
      .slice(0, 3)
      .map((other) => normalizeText(other.text ?? "").slice(0, TITLE_MAX));
    const titleCandidates = options.titles === "own" ? ownTitles(blocksByPage.get(table.page) ?? [], block) : before;
    const unitLines = [
      ...before.filter((text) => UNIT_LINE.test(text)),
      ...table.cells.flat().map(normalizeText).filter((text) => UNIT_LINE.test(text) && text.length <= 60),
    ];
    const grid: Grid = {
      id: table.id,
      docId,
      kind: "pdf_table",
      page: table.page,
      sheet: null,
      cells: table.cells,
      header: table.header,
      status: table.status,
      footnoteMarks: table.footnote_marks,
      titleCandidates,
      unitLines: [...new Set(unitLines)],
    };
    grids.push(grid);
    const extracted = gridFacts(grid);
    facts.push(...extracted.facts);
    nonValues.push(...extracted.nonValues);
  }

  for (const block of document.blocks) {
    if (block.kind === "table" || block.text === undefined) continue;
    const text = block.text;
    const whole = classifyCell(text);
    if (whole.kind === "blank" || whole.kind === "text") continue;
    const numberOnly = whole.kind !== "text_with_digits";
    const where = (span: FactWhere["span"]): FactWhere => ({ page: block.page, sheet: null, gridId: null, row: null, col: null, ref: null, blockId: block.id, span });
    if (numberOnly && isPageNumber(text, block.bbox, heights.get(block.page))) {
      nonValues.push({ docId, where: where(null), raw: text, class: "page_number" });
      continue;
    }
    scanNumbers(text).forEach((token, index) => {
      const tokenWhere = where({ start: token.start, end: token.end, occurrence: token.occurrence });
      if (token.kind !== "value" || !token.number) {
        nonValues.push({ docId, where: tokenWhere, raw: token.raw, class: token.kind as NonValueClass });
        return;
      }
      facts.push({
        id: `${block.id}#n${index + 1}`,
        docId,
        bucket: numberOnly ? "number_only_block" : "sentence",
        where: tokenWhere,
        raw: token.raw,
        value: token.number.value,
        decimals: token.number.decimals,
        sign: token.number.sign,
        percent: token.number.percent,
        unitSuffix: token.number.unitSuffix,
        flags: token.number.flags,
        footnoteMarks: [],
        tableStatus: null,
        headers: null,
        stored: null,
        storedAsText: false,
      });
    });
  }

  const extraction: Extraction = {
    docId,
    fileName: document.source.file_name,
    grids,
    facts,
    nonValues,
    failedTables,
    pagesWithoutText: document.pages.filter((page) => page.text_layer === "none").map((page) => page.page),
  };
  if (options.kinds) extraction.kinds = classifyKinds(document, extraction);
  return extraction;
}

/** 그 표의 제목(앞 표 뒤부터 이 표 앞까지, 각주 꼴은 앞 표의 것이라 뺀다)과 그 표의 각주(표 바로 뒤에 이어지는 각주 꼴의 글). */
function ownTitles(pageBlocks: DocModel["blocks"], block: DocModel["blocks"][number] | undefined): string[] {
  if (block === undefined) return [];
  const isText = (other: DocModel["blocks"][number]): boolean => other.kind !== "table" && ["text", "text_with_digits"].includes(classifyCell(other.text ?? "").kind);
  const clip = (other: DocModel["blocks"][number]): string => normalizeText(other.text ?? "").slice(0, TITLE_MAX);
  const ordered = [...pageBlocks].sort((a, b) => a.order - b.order);
  const previousTable = ordered.filter((other) => other.kind === "table" && other.order < block.order).pop();
  const titles = ordered
    .filter((other) => other.order < block.order && (previousTable === undefined || other.order > previousTable.order) && isText(other) && !FOOTNOTE_LINE.test(other.text ?? ""))
    .reverse()
    .slice(0, 3)
    .map(clip);
  const footnotes: string[] = [];
  for (const other of ordered.filter((item) => item.order > block.order)) {
    if (other.kind === "table" || !isText(other) || !FOOTNOTE_LINE.test(other.text ?? "")) break;
    footnotes.push(clip(other));
  }
  return [...titles, ...footnotes];
}

/** 엑셀 격자 묶음에서 사실 후보를 뽑는다. */
export function extractGrids(grids: Grid[], docId: string, fileName: string): Extraction {
  const facts: Fact[] = [];
  const nonValues: NonValue[] = [];
  for (const grid of grids) {
    const extracted = gridFacts(grid);
    facts.push(...extracted.facts);
    nonValues.push(...extracted.nonValues);
  }
  return { docId, fileName, grids, facts, nonValues, failedTables: [], pagesWithoutText: [] };
}
