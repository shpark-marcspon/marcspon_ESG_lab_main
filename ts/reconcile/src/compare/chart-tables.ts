import { extractDocument, type DocModel, type Extraction, type Fact } from "../domain/facts.js";
import type { ChartLabels, FactChart } from "./charts.js";

// 이름이 붙은 도표를 표로 세운다 (밴드 CT1, MARC-TICK-36). 모델을 부르지 않는다. 값을 읽지 않는다: 값 후보의 원문 글자를 새 칸으로 옮길 뿐이다.
//
// 입력: 문서 모델과 「값 후보 ID → 도표·계열·범주」 라벨. 출력: 도표마다 표 하나(행 = 계열, 열 = 범주)를 더한 **파생 문서**와 자리 ID 의 대응표.
// 파생 문서는 원래 문서 모델과 같은 스키마(`schemas/document.schema.json`)를 통과하고, 읽는 쪽(숫자 뽑기 `extractDocument`, 요청 조립)이 그대로 읽는다.
//
// 정한 것:
// - 옮긴 값은 원래 자리에서 **뺀다** (같은 값이 두 번 실리지 않게). 눈금과 범주 축의 글자는 원래 자리에 그대로 둔다.
// - 애매하면 세우지 않는다: 한 칸에 값이 둘 이상 놓이게 되는 도표, 계열 이름이 있는 값과 없는 값이 섞인 도표는 세우지 않고 까닭을 남긴다.
// - 범주 라벨이 없는 값은 옮기지 않고 원래 자리에 둔다 (그 도표는 「일부만 섬」).
// - 세운 뒤 파생 문서에서 값 후보를 다시 뽑아, 원래의 값 후보 하나하나가 대응표의 새 ID 에 같은 값으로 있는지 본다. 어긋나면 그 도표를 빼고 다시 세운다.
// - 새 표의 `method` 는 `text`(괘선이 아니라 글자의 자리로 세운 격자), `status` 는 `ok`, `notes` 에 `chart_table:<도표 ID>` 와 라벨의 출처를 남긴다.
//   새 표의 ID 는 그 쪽의 표 번호를 이어 `p<쪽>-t<번호>` 로 준다 (읽는 쪽의 ID 꼴이 그것뿐이다). 새 표의 블록은 값이 있던 블록의 바로 뒤에 같은 `order` 로 놓는다.

/** 값 후보 하나의 도표 라벨 (만든 길과 무관한 꼴). */
export interface ChartValueLabel {
  /** 값 후보의 ID (원래 문서의 것). */
  id: string;
  chart: string;
  series: string | null;
  category: string | null;
  /** 값 글자의 상자 (pt). 열의 차례를 정하는 데만 쓴다. */
  bbox: number[];
}

export interface ChartTableInput {
  /** 라벨을 만든 길 (예: `code`, `vision-merged`). 새 표의 notes 에 남는다. */
  source: string;
  /** categories = 범주 축의 글자를 축의 차례대로 (도표 찾기가 낸 것. 없어도 된다). 열의 차례에만 쓴다. */
  charts: { id: string; page: number; bbox: number[]; title: string | null; categories?: string[] | undefined }[];
  values: ChartValueLabel[];
}

export type NotBuiltReason =
  | "no_values" // 그 도표에 걸린 값 후보가 없다
  | "no_category" // 값은 있으나 범주 라벨이 하나도 없다
  | "mixed_series" // 계열 이름이 있는 값과 없는 값이 섞였다
  | "duplicate_cell" // 같은 계열·범주에 값이 둘 이상이다
  | "duplicate_cell_no_series" // 계열 이름이 하나도 없는데 한 범주에 값이 둘 이상이다 (계열을 모르는 여러 계열 도표)
  | "reads_differently"; // 세워 보니 값 후보가 원래와 다르게 읽혔다 (옮긴 값이나, 값을 뺀 원래 그릇의 남은 값)

export interface ChartTableResult {
  chart: string;
  page: number;
  title: string | null;
  status: "full" | "partial" | "not_built";
  reason: NotBuiltReason | null;
  tableId: string | null;
  rows: number;
  cols: number;
  /** 열의 차례를 무엇으로 정했나: axis = 범주 축의 글자의 차례, position = 값 글자의 자리. */
  columnOrder: "axis" | "position" | null;
  /** 계열 이름 없이 한 행으로 선 표인가. */
  singleUnnamedRow: boolean;
  values: number;
  moved: number;
  /** 범주 라벨이 없어 원래 자리에 남은 값. */
  left: string[];
  /** 같은 글자를 겹친 그릇이 두 번 실은 자리의 둘째부터 (문서 모델의 흠). 옮기지 않고 원래 자리에 둔다. `values` 에 들지 않는다. */
  twins: string[];
}

export interface MapEntry {
  /** 파생 문서에서의 ID. */
  id: string;
  /** 원래 문서에서의 ID. */
  from: string;
  /** moved = 새 표의 칸으로 옮김, renumbered = 값을 뺀 그릇에 남아 순번(`#n`)이 바뀜. 그대로인 값 후보는 싣지 않는다. */
  kind: "moved" | "renumbered";
  chart?: string;
  series?: string | null;
  category?: string;
}

export interface ChartTablesOutput {
  document: DocModel;
  charts: ChartTableResult[];
  /** ID 가 바뀐 값 후보만. 여기에 없는 값 후보는 원래 ID 그대로다. */
  map: MapEntry[];
  facts: { before: number; after: number; moved: number; renumbered: number };
}

type RawDoc = DocModel & Record<string, unknown>;
type RawTable = RawDoc["tables"][number] & { bbox: number[]; n_rows: number; n_cols: number; notes: string[] };

const containerOf = (fact: Fact): string => (fact.where.gridId !== null ? `${fact.where.gridId}:r${fact.where.row}c${fact.where.col}` : (fact.where.blockId as string));
const signature = (fact: Fact): string => JSON.stringify([fact.value, fact.decimals, fact.sign, fact.percent, fact.unitSuffix]);
const center = (bbox: number[], axis: 0 | 1): number => ((bbox[axis] as number) + (bbox[axis + 2] as number)) / 2;
const mean = (values: number[]): number => values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);
const spread = (values: number[]): number => (values.length === 0 ? 0 : Math.max(...values) - Math.min(...values));

/** 도표 라벨 파일의 `charts[].categories` (범주 축의 글자, 축의 차례). 읽는 쪽의 형식(`chartLabelsSchema`)에는 없는 필드라 있으면 쓴다. */
const categoriesOf = (chart: object): string[] | undefined => {
  const value = (chart as { categories?: unknown }).categories;
  return Array.isArray(value) && value.every((item) => typeof item === "string") ? (value as string[]) : undefined;
};

/** 코드 라벨(`py/docparse charts` 의 합친 것)에서. 도표 낱말이 값 후보에 걸린 것 가운데 갈래가 값인 것만 쓴다. */
export function inputFromCodeLabels(labels: ChartLabels, linked: FactChart[]): ChartTableInput {
  const values = linked
    .filter((item) => item.word.role === "value")
    .map((item): ChartValueLabel => ({ id: item.id, chart: item.chart, series: item.word.label?.series ?? null, category: item.word.label?.category ?? null, bbox: item.bbox }));
  return { source: "code", charts: labels.charts.map((chart) => ({ id: chart.id, page: chart.page, bbox: chart.bbox, title: chart.title, categories: categoriesOf(chart) })), values };
}

export interface VisionAnswer {
  role: string;
  title: string | null;
  series: string | null;
  category: string | null;
}

/**
 * 그림 라벨(밴드 W 의 저장된 답. 값 후보 ID → 갈래·제목·계열·범주)에서. 어느 도표의 값인지는 코드가 찾은 도표(값 후보에 걸린 도표 낱말)를 따른다.
 * 그림 라벨이 값이라고 답한 자리만 쓴다. 도표의 제목은 그 도표의 값들에 달린 제목 가운데 가장 많은 것(같으면 먼저 나온 것), 없으면 코드가 붙인 제목.
 */
export function inputFromVisionAnswers(labels: ChartLabels, linked: FactChart[], answers: Record<string, VisionAnswer>, source = "vision"): ChartTableInput {
  const values: ChartValueLabel[] = [];
  const titles = new Map<string, Map<string, number>>();
  for (const item of linked) {
    const answer = answers[item.id];
    if (!answer || answer.role !== "value") continue;
    values.push({ id: item.id, chart: item.chart, series: answer.series, category: answer.category, bbox: item.bbox });
    if (answer.title !== null && answer.title.trim() !== "") {
      const bag = titles.get(item.chart) ?? new Map<string, number>();
      bag.set(answer.title, (bag.get(answer.title) ?? 0) + 1);
      titles.set(item.chart, bag);
    }
  }
  const titleOf = (chart: string, fallback: string | null): string | null => {
    const bag = [...(titles.get(chart) ?? [])];
    if (bag.length === 0) return fallback;
    return (bag.reduce((best, item) => (item[1] > best[1] ? item : best)) as [string, number])[0];
  };
  return { source, charts: labels.charts.map((chart) => ({ id: chart.id, page: chart.page, bbox: chart.bbox, title: titleOf(chart.id, chart.title), categories: categoriesOf(chart) })), values };
}

interface Plan {
  chart: ChartTableInput["charts"][number];
  result: ChartTableResult;
  series: (string | null)[];
  categories: string[];
  /** 옮길 값: 행·열(머리글을 뺀 0 부터)과 값 후보. */
  cells: { row: number; col: number; fact: Fact; label: ChartValueLabel }[];
}

export interface ChartTableOptions {
  /**
   * 계열 이름이 있는 값과 없는 값이 섞인 도표를 어떻게 하는가. 기본 `reject` 는 세우지 않는다 (이름을 못 붙인 것인지 원래 이름이 없는 것인지 가를 수 없다).
   * `unnamed_row` 는 이름 없는 값을 이름 없는 한 행에 모아 세운다 (범례가 한 막대에만 있는 도표가 서지만, 계열을 못 붙인 값이 이름 없는 행에 놓인다).
   */
  mixedSeries?: "reject" | "unnamed_row";
}

function plan(chart: ChartTableInput["charts"][number], labels: ChartValueLabel[], facts: Map<string, Fact>, order: Map<string, number>, options: ChartTableOptions): Plan {
  const linked = labels.filter((label) => facts.has(label.id)).sort((x, y) => (order.get(x.id) as number) - (order.get(y.id) as number));
  // 겹친 표가 같은 글자를 두 번 실은 자리(상자와 원문 글자가 같다)는 먼저 나온 값 후보만 옮긴다.
  const seen = new Set<string>();
  const known: ChartValueLabel[] = [];
  const twins: string[] = [];
  for (const label of linked) {
    const place = `${label.bbox.map((v) => v.toFixed(1)).join(",")}|${(facts.get(label.id) as Fact).raw}`;
    if (seen.has(place)) twins.push(label.id);
    else {
      seen.add(place);
      known.push(label);
    }
  }
  const result: ChartTableResult = { chart: chart.id, page: chart.page, title: chart.title, status: "not_built", reason: null, tableId: null, rows: 0, cols: 0, columnOrder: null, singleUnnamedRow: false, values: known.length, moved: 0, left: [], twins };
  const empty: Plan = { chart, result, series: [], categories: [], cells: [] };
  const fail = (reason: NotBuiltReason): Plan => ({ ...empty, result: { ...result, reason } });
  if (known.length === 0) return fail("no_values");
  const placed = known.filter((label) => label.category !== null && label.category.trim() !== "");
  if (placed.length === 0) return fail("no_category");
  const named = placed.filter((label) => label.series !== null && label.series.trim() !== "");
  const mixed = named.length > 0 && named.length < placed.length;
  if (mixed && options.mixedSeries !== "unnamed_row") return fail("mixed_series");
  const seriesOf = (label: ChartValueLabel): string | null => (label.series !== null && label.series.trim() !== "" ? label.series : null);
  const keys = new Set<string>();
  for (const label of placed) {
    const key = JSON.stringify([seriesOf(label), label.category]);
    if (keys.has(key)) return fail(named.length > 0 ? "duplicate_cell" : "duplicate_cell_no_series");
    keys.add(key);
  }
  // 열의 차례: 범주가 전부 범주 축의 글자에 있으면 축의 차례. 아니면 범주마다 값 글자의 가운데를 모아, 더 넓게 퍼진 쪽(가로 또는 세로)의 차례로
  // (값 글자는 점의 옆에 비껴 놓이기도 해서 축의 차례가 더 믿을 만하다).
  const categories = [...new Set(placed.map((label) => label.category as string))];
  const squash = (text: string): string => text.normalize("NFKC").replace(/\s+/g, "");
  const axis = (chart.categories ?? []).map(squash);
  const onAxis = axis.length > 0 && new Set(axis).size === axis.length && categories.every((category) => axis.includes(squash(category)));
  const at = (axis: 0 | 1): Map<string, number> => new Map(categories.map((category) => [category, mean(placed.filter((label) => label.category === category).map((label) => center(label.bbox, axis)))]));
  const [xs, ys] = [at(0), at(1)];
  const along = spread([...ys.values()]) > spread([...xs.values()]) ? ys : xs;
  if (onAxis) categories.sort((p, q) => axis.indexOf(squash(p)) - axis.indexOf(squash(q)));
  else categories.sort((p, q) => (along.get(p) as number) - (along.get(q) as number));
  // 행의 차례: 계열이 문서에 처음 나온 차례.
  const series: (string | null)[] = [...new Set(placed.map(seriesOf))];
  const cells = placed.map((label) => ({ row: series.indexOf(seriesOf(label)), col: categories.indexOf(label.category as string), fact: facts.get(label.id) as Fact, label }));
  const left = known.filter((label) => !placed.includes(label)).map((label) => label.id);
  return { chart, series, categories, cells, result: { ...result, status: left.length === 0 ? "full" : "partial", rows: series.length, cols: categories.length, columnOrder: onAxis ? "axis" : "position", singleUnnamedRow: named.length === 0, moved: cells.length, left } };
}

/** 그릇의 글에서 값 후보의 글자를 뺀다 (뒤에서부터). 빈칸 뭉치는 하나로 줄인다. 글자가 그 자리에 없으면 null. */
function removeSpans(text: string, facts: Fact[]): string | null {
  let out = text;
  for (const fact of [...facts].sort((x, y) => (y.where.span?.start ?? 0) - (x.where.span?.start ?? 0))) {
    const span = fact.where.span;
    if (!span || out.slice(span.start, span.end) !== fact.raw) return null;
    out = `${out.slice(0, span.start)} ${out.slice(span.end)}`;
  }
  return out.replace(/\s+/g, " ").trim();
}

const numberOfId = (id: string, pattern: RegExp): number => Number(pattern.exec(id)?.[1] ?? 0);

function build(source: RawDoc, docId: string, input: ChartTableInput, plans: Plan[]): { document: RawDoc; expected: Map<string, MapEntry | null>; touchedBy: Map<string, Set<string>> } {
  const document = structuredClone(source);
  const before = extractDocument(source, docId);
  const tables = new Map(document.tables.map((table) => [table.id, table as RawTable]));
  const blocks = new Map(document.blocks.map((block) => [block.id, block]));
  const tableBlock = new Map(document.blocks.filter((block) => block.kind === "table").map((block) => [block.table_id as string, block]));
  const movedTo = new Map<string, MapEntry>();
  /** 그릇 → 그 그릇을 건드린 도표. */
  const touchedBy = new Map<string, Set<string>>();
  const touch = (container: string, chart: string): void => void touchedBy.set(container, (touchedBy.get(container) ?? new Set()).add(chart));
  const nextTable = new Map<number, number>();
  const nextBlock = new Map<number, number>();
  for (const table of source.tables) nextTable.set(table.page, Math.max(nextTable.get(table.page) ?? 0, numberOfId(table.id, /-t(\d+)$/)));
  for (const block of source.blocks) nextBlock.set(block.page, Math.max(nextBlock.get(block.page) ?? 0, numberOfId(block.id, /-b(\d+)$/)));
  const spansToRemove = new Map<string, Fact[]>();
  const lastInserted = new Map<string, string>();

  for (const item of plans) {
    if (item.result.status === "not_built") continue;
    const page = item.chart.page;
    const tableNo = (nextTable.get(page) ?? 0) + 1;
    nextTable.set(page, tableNo);
    const blockNo = (nextBlock.get(page) ?? 0) + 1;
    nextBlock.set(page, blockNo);
    const tableId = `p${page}-t${tableNo}`;
    item.result.tableId = tableId;
    const cells: string[][] = [[item.chart.title ?? "", ...item.categories], ...item.series.map((name) => [name ?? "", ...item.categories.map(() => "")])];
    const footnotes: { row: number; col: number; mark: string }[] = [];
    for (const cell of item.cells) {
      const [row, col] = [cell.row + 1, cell.col + 1];
      (cells[row] as string[])[col] = cell.fact.raw;
      movedTo.set(cell.fact.id, { id: `${tableId}:r${row}c${col}`, from: cell.fact.id, kind: "moved", chart: item.chart.id, series: item.series[cell.row] ?? null, category: item.categories[cell.col] as string });
      const container = containerOf(cell.fact);
      touch(container, item.chart.id);
      if (cell.fact.where.gridId !== null) touch(cell.fact.where.gridId, item.chart.id);
      if (cell.fact.bucket === "table_cell") {
        // 칸 전체가 값 후보다: 칸을 비우고 각주 위첨자를 새 칸으로 옮긴다.
        const table = tables.get(cell.fact.where.gridId as string) as RawTable;
        ((table.cells[cell.fact.where.row as number] as string[])[cell.fact.where.col as number] as string) = "";
        const marks = table.footnote_marks.filter((mark) => mark.row === cell.fact.where.row && mark.col === cell.fact.where.col);
        table.footnote_marks = table.footnote_marks.filter((mark) => !marks.includes(mark));
        footnotes.push(...marks.map((mark) => ({ row, col, mark: mark.mark })));
      } else spansToRemove.set(container, [...(spansToRemove.get(container) ?? []), cell.fact]);
    }
    const boxes = item.cells.map((cell) => cell.label.bbox);
    const bbox = [Math.min(...boxes.map((b) => b[0] as number)), Math.min(...boxes.map((b) => b[1] as number)), Math.max(...boxes.map((b) => b[2] as number)), Math.max(...boxes.map((b) => b[3] as number))];
    const table = { id: tableId, page, bbox, n_rows: cells.length, n_cols: (cells[0] as string[]).length, cells, header: { rows: [0], cols: [0], basis: "rows_before_first_numeric_row" }, method: "text" as const, status: "ok" as const, notes: [`chart_table:${item.chart.id}`, `chart_labels:${input.source}`], footnote_marks: footnotes };
    document.tables.push(table);
    // 새 표의 블록: 첫 값이 있던 블록의 바로 뒤, 같은 order.
    const first = item.cells[0] as Plan["cells"][number];
    const home = first.fact.where.gridId !== null ? tableBlock.get(first.fact.where.gridId) : blocks.get(first.fact.where.blockId as string);
    const block = { ...(home ? { section_id: (home as Record<string, unknown>).section_id ?? null } : { section_id: null }), id: `p${page}-b${blockNo}`, kind: "table" as const, page, bbox, order: home?.order ?? 1, table_id: tableId };
    // 같은 블록에서 나온 도표가 여럿이면 도표의 차례대로 잇는다.
    const anchor = lastInserted.get(home?.id ?? "") ?? home?.id;
    const after = anchor !== undefined ? document.blocks.findIndex((other) => other.id === anchor) : document.blocks.length - 1;
    lastInserted.set(home?.id ?? "", block.id);
    document.blocks.splice(after + 1, 0, block as RawDoc["blocks"][number]);
  }

  // 글 속의 값(표의 글 칸, 글 블록)을 그릇에서 뺀다.
  const unreadable = new Set<string>();
  for (const [container, facts] of spansToRemove) {
    const fact = facts[0] as Fact;
    if (fact.where.gridId !== null) {
      const table = tables.get(fact.where.gridId) as RawTable;
      const row = table.cells[fact.where.row as number] as string[];
      const text = removeSpans(row[fact.where.col as number] as string, facts);
      if (text === null) unreadable.add(container);
      else row[fact.where.col as number] = text;
    } else {
      const block = blocks.get(fact.where.blockId as string) as RawDoc["blocks"][number];
      const text = removeSpans(block.text ?? "", facts);
      if (text === null) unreadable.add(container);
      else block.text = text;
    }
  }

  // 원래의 값 후보마다 파생 문서에서 있어야 할 ID. 값을 뺀 그릇에 남은 값은 순번이 당겨진다 (그릇 안의 차례는 그대로).
  const expected = new Map<string, MapEntry | null>();
  const remaining = new Map<string, Fact[]>();
  for (const fact of before.facts) {
    const moved = movedTo.get(fact.id);
    if (moved) expected.set(fact.id, moved);
    else if (fact.where.span && spansToRemove.has(containerOf(fact))) remaining.set(containerOf(fact), [...(remaining.get(containerOf(fact)) ?? []), fact]);
    else expected.set(fact.id, null);
  }
  const after = extractDocument(document, docId);
  const afterIn = new Map<string, Fact[]>();
  // 값을 빼고 숫자 하나만 남은 칸은 칸 전체가 값 후보가 된다 (`#n` 없는 ID). 그것도 그 그릇에 남은 값이다.
  for (const fact of after.facts) afterIn.set(containerOf(fact), [...(afterIn.get(containerOf(fact)) ?? []), fact]);
  for (const [container, facts] of remaining) {
    const now = afterIn.get(container) ?? [];
    facts.forEach((fact, index) => expected.set(fact.id, now[index] && !unreadable.has(container) ? (now[index].id === fact.id ? null : { id: now[index].id, from: fact.id, kind: "renumbered" }) : { id: "(없음)", from: fact.id, kind: "renumbered" }));
  }
  return { document, expected, touchedBy };
}

/**
 * 도표를 표로 세운 파생 문서와 대응표를 낸다. 원래 문서는 고치지 않는다.
 * `source` 는 문서 모델의 JSON 전체다 (새 표와 블록에 스키마가 요구하는 필드를 채워야 해서, 읽는 쪽이 쓰는 부분만 담은 꼴이 아니라 전체를 받는다).
 */
export function buildChartTables(source: DocModel, docId: string, input: ChartTableInput, options: ChartTableOptions = {}): ChartTablesOutput {
  const raw = source as RawDoc;
  const before = extractDocument(raw, docId);
  const facts = new Map(before.facts.map((fact) => [fact.id, fact]));
  const order = new Map(before.facts.map((fact, index) => [fact.id, index]));
  const byChart = new Map<string, ChartValueLabel[]>();
  for (const label of input.values) byChart.set(label.chart, [...(byChart.get(label.chart) ?? []), label]);
  const plans = input.charts.map((chart) => plan(chart, byChart.get(chart.id) ?? [], facts, order, options));
  const beforeSignature = new Map(before.facts.map((fact) => [fact.id, signature(fact)]));

  for (let round = 0; ; round += 1) {
    const { document, expected, touchedBy } = build(raw, docId, input, plans);
    const after = extractDocument(document, docId);
    const afterById = new Map(after.facts.map((fact) => [fact.id, fact]));
    // 원래의 값 후보 하나하나가 있어야 할 ID 에 같은 값으로 있는가. 어긋난 것을 낸 도표를 찾는다.
    const blamed = new Set<string>();
    const used = new Set<string>();
    for (const fact of before.facts) {
      const entry = expected.get(fact.id);
      const id = entry ? entry.id : fact.id;
      const now = afterById.get(id);
      used.add(id);
      if (now && signature(now) === beforeSignature.get(fact.id)) continue;
      const charts = new Set<string>([...(entry?.chart ? [entry.chart] : []), ...(touchedBy.get(containerOf(fact)) ?? []), ...(fact.where.gridId !== null ? (touchedBy.get(fact.where.gridId) ?? []) : [])]);
      if (charts.size === 0) throw new Error(`값 후보 ${fact.id} 가 파생 문서에서 달라졌는데 건드린 도표를 찾지 못했다`);
      for (const chart of charts) blamed.add(chart);
    }
    // 새로 생긴 값 후보(원래에 없던 것)가 있으면 그 표를 낸 도표를 뺀다.
    const tableChart = new Map(plans.filter((item) => item.result.tableId !== null).map((item) => [item.result.tableId as string, item.chart.id]));
    for (const fact of after.facts) {
      if (used.has(fact.id)) continue;
      const chart = fact.where.gridId !== null ? (tableChart.get(fact.where.gridId) ?? [...(touchedBy.get(fact.where.gridId) ?? [])][0]) : [...(touchedBy.get(containerOf(fact)) ?? [])][0];
      if (chart === undefined) throw new Error(`파생 문서에 원래 없던 값 후보 ${fact.id} 가 생겼는데 건드린 도표를 찾지 못했다`);
      blamed.add(chart);
    }
    if (blamed.size === 0) {
      const map = [...expected.values()].filter((entry): entry is MapEntry => entry !== null);
      return { document, charts: plans.map((item) => item.result), map, facts: { before: before.facts.length, after: after.facts.length, moved: map.filter((entry) => entry.kind === "moved").length, renumbered: map.filter((entry) => entry.kind === "renumbered").length } };
    }
    if (round > plans.length) throw new Error("도표를 빼도 파생 문서의 값 후보가 원래와 맞지 않는다");
    for (const item of plans) {
      if (!blamed.has(item.chart.id) || item.result.status === "not_built") continue;
      item.result = { ...item.result, status: "not_built", reason: "reads_differently", tableId: null, rows: 0, cols: 0, columnOrder: null, singleUnnamedRow: false, moved: 0, left: [] };
      item.cells = [];
    }
  }
}

/** 대응표로 원래의 ID 를 파생 문서의 ID 로 옮긴다 (대응표에 없으면 그대로). */
export function toDerivedId(map: MapEntry[], id: string): string {
  return map.find((entry) => entry.from === id)?.id ?? id;
}

/** 파생 문서의 ID 를 원래의 ID 로 되돌린다. 앞 밴드의 정답 쌍·심은 자리·좌표(원래 ID 로 적혀 있다)를 따라갈 때 쓴다. */
export function toOriginalId(map: MapEntry[], id: string): string {
  return map.find((entry) => entry.id === id)?.from ?? id;
}

/** 새 표의 칸마다 머리글을 문서에서 다시 읽는다 (채점용: 라벨이 아니라 표에 실제로 선 글자). */
export function cellHeaders(document: DocModel, extraction: Extraction, id: string): { series: string; category: string } | null {
  const fact = extraction.facts.find((item) => item.id === id);
  if (!fact || fact.where.gridId === null) return null;
  const table = document.tables.find((item) => item.id === fact.where.gridId);
  if (!table) return null;
  return { series: (table.cells[fact.where.row as number] as string[])[0] ?? "", category: (table.cells[0] as string[])[fact.where.col as number] ?? "" };
}
