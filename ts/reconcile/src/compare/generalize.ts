import { z } from "zod";
import type { DocModel, Extraction, Fact, FactBucket } from "../domain/facts.js";
import { VALUE_KINDS, isNonValue, type ValueKind } from "../domain/kinds.js";
import type { UnitTable } from "../domain/units.js";
import { prng } from "./exam.js";
import { TallyError } from "./hybrid.js";
import { scoreKinds, type KindScore } from "./kind-report.js";
import { lineKey } from "./recover.js";

// 밴드 G1 (MARC-TICK-34): 호출 없는 규칙(값 후보의 종류, 도표 찾기)을 KB 가 아닌 문서 하나에서 재는 도구.
// 규칙은 여기 없다(`domain/kinds.ts`, `py/docparse/charts.py` 를 그대로 부른다). 이 파일은 문서 하나의 표본 뽑기, 정답의 형식과 대조, 셈만 한다.
// 밴드 V 의 `drawKindTargets` 는 두 문서를 합쳐 서술 값 후보를 전부 싣는다. 긴 보고서에서는 수천 자리가 되므로 여기서는 문서 하나에서 갈래마다 정한 수를 뽑는다.

export const BUCKETS = ["sentence", "number_only_block", "table_text_cell", "table_cell"] as const satisfies readonly FactBucket[];
export type Quota = Record<(typeof BUCKETS)[number], number>;

export interface DocTarget {
  id: string;
  bucket: FactBucket;
  page: number | null;
  raw: string;
}

/** 문서 하나에서 갈래마다 `quota` 개를 씨앗 고정 난수로 뽑는다 (모자라면 전부). 갈래의 차례는 `BUCKETS`, 결과는 문서의 차례. 손으로 고르지 않는다. */
export function drawDocTargets(extraction: Extraction, seed: number, quota: Quota): DocTarget[] {
  const all = extraction.facts.map((fact, at) => ({ id: fact.id, bucket: fact.bucket, page: fact.where.page, raw: fact.raw, at }));
  const random = prng(seed);
  const chosen: typeof all = [];
  for (const bucket of BUCKETS) {
    const pool = all.filter((item) => item.bucket === bucket);
    for (let n = 0; n < quota[bucket] && pool.length > 0; n++) chosen.push(...pool.splice(Math.floor(random() * pool.length), 1));
  }
  return chosen.sort((x, y) => x.at - y.at).map(({ at: _at, ...rest }) => rest);
}

/** 정답을 다는 사람에게 보여 줄 자리의 앞뒤. 문서 모델의 글자만 쓴다 (종류 규칙을 부르지 않는다). */
export interface TargetContext extends DocTarget {
  /** 글 속 숫자: 그 블록의 글에서 숫자를 〔〕로 싼 것. 표 칸: 그 칸의 글에서 숫자를 〔〕로 싼 것. */
  marked: string;
  table: null | { tableId: string; status: string; titleCandidates: string[]; unitLines: string[]; rowHeaders: string[]; colHeaders: string[]; rowCells: string[]; cellRow: number; cellCol: number };
}

const clip = (text: string, start: number, end: number, before: number, after: number): string => `${start > before ? "…" : ""}${text.slice(Math.max(0, start - before), start)}〔${text.slice(start, end)}〕${text.slice(end, end + after)}${end + after < text.length ? "…" : ""}`;

export function targetContext(model: DocModel, extraction: Extraction, target: DocTarget, before = 160, after = 100): TargetContext {
  const fact = extraction.facts.find((item) => item.id === target.id);
  if (!fact) throw new TallyError(`${target.id}: 문서에 없는 자리다`);
  const span = fact.where.span;
  if (fact.where.gridId === null) {
    const text = model.blocks.find((block) => block.id === fact.where.blockId)?.text ?? "";
    return { ...target, marked: span ? clip(text, span.start, span.end, before, after) : `〔${fact.raw}〕`, table: null };
  }
  const grid = extraction.grids.find((item) => item.id === fact.where.gridId);
  if (!grid) throw new TallyError(`${target.id}: 격자가 없다`);
  const row = fact.where.row as number;
  const col = fact.where.col as number;
  const cell = grid.cells[row]?.[col] ?? "";
  return {
    ...target,
    marked: span ? clip(cell, span.start, span.end, before, after) : `〔${cell}〕`,
    table: { tableId: grid.id, status: grid.status, titleCandidates: grid.titleCandidates, unitLines: grid.unitLines, rowHeaders: (fact.headers?.row ?? []).map((ref) => ref.text), colHeaders: (fact.headers?.col ?? []).map((ref) => ref.text), rowCells: grid.cells[row] ?? [], cellRow: row, cellCol: col },
  };
}

export const docKindGoldSchema = z.object({
  note: z.string(),
  status: z.string().min(1),
  document: z.object({ file_name: z.string(), sha256: z.string() }),
  seed: z.number().int(),
  quota: z.object({ sentence: z.number().int(), number_only_block: z.number().int(), table_text_cell: z.number().int(), table_cell: z.number().int() }),
  items: z.array(
    z.object({
      id: z.string(),
      bucket: z.enum(BUCKETS),
      page: z.number().nullable(),
      raw: z.string(),
      /** 종류. 정답을 단 손이 「모르겠다」고 한 자리는 null 이고 채점의 분모에서 빠진다 (따로 센다). */
      kind: z.enum(VALUE_KINDS).nullable(),
      ambiguousWith: z.enum(VALUE_KINDS).nullable(),
      basis: z.string().min(1),
    }),
  ),
});
export type DocKindGold = z.infer<typeof docKindGoldSchema>;

/** 정답이 문서와 맞는가: 같은 씨앗·수로 다시 뽑은 대상과 자리·원문 글자·갈래가 같고 빠짐·더함이 없다. */
export function checkDocKindGold(gold: DocKindGold, extraction: Extraction): string[] {
  const problems: string[] = [];
  const wanted = new Map(drawDocTargets(extraction, gold.seed, gold.quota).map((item) => [item.id, item]));
  const seen = new Set<string>();
  for (const item of gold.items) {
    const target = wanted.get(item.id);
    if (seen.has(item.id)) problems.push(`${item.id}: 정답에 두 번 있다`);
    seen.add(item.id);
    if (!target) problems.push(`${item.id}: 뽑은 대상에 없는 자리다`);
    else if (target.raw !== item.raw || target.bucket !== item.bucket) problems.push(`${item.id}: 원문 글자나 갈래가 문서와 다르다 (${item.raw} / ${target.raw})`);
  }
  for (const id of wanted.keys()) if (!seen.has(id)) problems.push(`${id}: 정답이 없다`);
  return problems;
}

export interface DocKindScore {
  /** 표본의 자리 수 (모르겠다 포함). */
  sampled: number;
  /** 정답이 「모르겠다」여서 분모에서 뺀 자리. */
  unsure: number;
  score: KindScore;
  /** 정답 종류 → 규칙의 종류 → 수. 합은 `score.total`. */
  confusion: Record<ValueKind, Record<ValueKind, number>>;
  /** 정답이 「값」인데 규칙이 값이 아니라고 가른 자리 (대조 대상을 잃는 쪽). 갈래 → 수 / 정답이 값인 자리 수. */
  valueFiltered: Record<string, { n: number; of: number }>;
  /** 정답이 값이 아닌데 규칙이 값으로 남긴 자리 (거짓 경보의 재료). 갈래 → 수 / 정답이 값이 아닌 자리 수. */
  nonValueKept: Record<string, { n: number; of: number }>;
}

/** 문서 하나의 종류 채점. 종류별 셈은 밴드 V 의 `scoreKinds` 를 그대로 쓴다. 「모르겠다」는 분모에서 빼고 따로 센다. */
export function scoreDocKinds(gold: DocKindGold, predictedOf: (id: string) => ValueKind | undefined): DocKindScore {
  const known = gold.items.filter((item): item is typeof item & { kind: ValueKind } => item.kind !== null);
  const score = scoreKinds(known.map((item) => ({ doc: "a" as const, id: item.id, bucket: item.bucket, raw: item.raw, kind: item.kind, ambiguousWith: item.ambiguousWith, basis: item.basis })), (_doc, id) => predictedOf(id));
  const confusion = Object.fromEntries(VALUE_KINDS.map((g) => [g, Object.fromEntries(VALUE_KINDS.map((p) => [p, 0]))])) as DocKindScore["confusion"];
  const valueFiltered: DocKindScore["valueFiltered"] = { all: { n: 0, of: 0 } };
  const nonValueKept: DocKindScore["nonValueKept"] = { all: { n: 0, of: 0 } };
  for (const item of known) {
    const predicted = predictedOf(item.id) as ValueKind;
    confusion[item.kind][predicted] += 1;
    for (const bucket of ["all", item.bucket]) {
      const filtered = (valueFiltered[bucket] ??= { n: 0, of: 0 });
      const kept = (nonValueKept[bucket] ??= { n: 0, of: 0 });
      if (!isNonValue(item.kind)) {
        filtered.of += 1;
        if (isNonValue(predicted)) filtered.n += 1;
      } else {
        kept.of += 1;
        if (!isNonValue(predicted)) kept.n += 1;
      }
    }
  }
  const sum = Object.values(confusion).reduce((total, row) => total + Object.values(row).reduce((x, y) => x + y, 0), 0);
  const all = { filtered: valueFiltered.all as { n: number; of: number }, kept: nonValueKept.all as { n: number; of: number } };
  if (sum !== score.total || score.total + (gold.items.length - known.length) !== gold.items.length || all.filtered.of + all.kept.of !== score.total) throw new TallyError("종류 채점의 합이 표본 수와 맞지 않는다");
  return { sampled: gold.items.length, unsure: gold.items.length - known.length, score, confusion, valueFiltered, nonValueKept };
}

export interface HeaderStats {
  /** 문서 모델의 표 (격자를 세우지 못한 것 포함). */
  tables: number;
  failed: number;
  byStatus: Record<string, number>;
  byMethod: Record<string, number>;
  /** 격자가 선 표 가운데 머리글 행·머리글 열이 추정된 것. */
  withHeaderRows: number;
  withHeaderCols: number;
  withBoth: number;
  /** 숫자 칸(값 후보)이 하나라도 있는 표. */
  tablesWithNumbers: number;
  /** 그 가운데 숫자 칸 전부가 열·행 머리글 열쇠를 둘 다 가진 표 / 하나라도 가진 표. */
  tablesAllCellsKeyed: number;
  tablesSomeCellsKeyed: number;
  /** 표의 숫자 칸. */
  cells: number;
  cellsWithCol: number;
  cellsWithRow: number;
  /** 열 머리글 열쇠와 행 머리글 열쇠가 둘 다 비어 있지 않은 칸 (열 밀림 검사가 판단할 수 있으려면 필요한 조건. 충분하지는 않다). */
  cellsWithBoth: number;
  cellsWithNeither: number;
  /** 정상 표(`ok`)의 숫자 칸만. */
  okCells: number;
  okCellsWithBoth: number;
}

/** 머리글이 선 표와 칸의 수. 열쇠는 열 밀림 검사(`recover.ts` 의 `lineKey`)가 읽는 것과 같다: 단위 글자와 빈 조각을 뺀 머리글 경로. */
export function headerStats(model: DocModel, extraction: Extraction, units: UnitTable): HeaderStats {
  const count = (items: string[]): Record<string, number> => items.reduce<Record<string, number>>((bag, item) => ((bag[item] = (bag[item] ?? 0) + 1), bag), {});
  const cells = extraction.facts.filter((fact: Fact) => fact.bucket === "table_cell");
  const keyed = cells.map((fact) => ({ grid: fact.where.gridId as string, ok: fact.tableStatus === "ok", col: lineKey(fact.headers?.col, units) !== "", row: lineKey(fact.headers?.row, units) !== "" }));
  const perTable = new Map<string, { all: boolean; some: boolean }>();
  for (const cell of keyed) {
    const state = perTable.get(cell.grid) ?? { all: true, some: false };
    perTable.set(cell.grid, { all: state.all && cell.col && cell.row, some: state.some || (cell.col && cell.row) });
  }
  const standing = model.tables.filter((table) => table.status !== "failed");
  return {
    tables: model.tables.length,
    failed: model.tables.length - standing.length,
    byStatus: count(model.tables.map((table) => table.status)),
    byMethod: count(model.tables.map((table) => table.method)),
    withHeaderRows: standing.filter((table) => table.header.rows.length > 0).length,
    withHeaderCols: standing.filter((table) => table.header.cols.length > 0).length,
    withBoth: standing.filter((table) => table.header.rows.length > 0 && table.header.cols.length > 0).length,
    tablesWithNumbers: perTable.size,
    tablesAllCellsKeyed: [...perTable.values()].filter((state) => state.all).length,
    tablesSomeCellsKeyed: [...perTable.values()].filter((state) => state.some).length,
    cells: keyed.length,
    cellsWithCol: keyed.filter((cell) => cell.col).length,
    cellsWithRow: keyed.filter((cell) => cell.row).length,
    cellsWithBoth: keyed.filter((cell) => cell.col && cell.row).length,
    cellsWithNeither: keyed.filter((cell) => !cell.col && !cell.row).length,
    okCells: keyed.filter((cell) => cell.ok).length,
    okCellsWithBoth: keyed.filter((cell) => cell.ok && cell.col && cell.row).length,
  };
}

/** 도표의 정답 (문서 하나). 쪽 그림을 보고 만든 목록이다. 상자는 쪽 그림에서 읽은 어림(pt). */
export const docChartGoldSchema = z.object({
  note: z.string(),
  status: z.string().min(1),
  document: z.object({ file_name: z.string(), sha256: z.string() }),
  pages: z.number().int(),
  charts: z.array(
    z.object({
      id: z.string(),
      page: z.number().int(),
      /** 갈래: 세로 막대 / 가로 막대 / 꺾은선 / 원·도넛 / 그 밖. 막대와 꺾은선이 한 그림에 있으면 `bar_v+line` 처럼 잇지 않고 주된 것을 적고 `mixed` 에 나머지를 적는다. */
      kind: z.enum(["bar_v", "bar_h", "line", "pie", "other"]),
      mixed: z.array(z.enum(["bar_v", "bar_h", "line", "pie", "other"])),
      title: z.string(),
      /** 값 숫자가 그림에 적혀 있는가. */
      valuesPrinted: z.enum(["yes", "no", "partial"]),
      /** 수량을 그린 것인지 장식인지 갈리는 것 (진행 막대, 숫자를 둘러싼 고리 등). 성적은 이것을 넣은 것과 뺀 것을 둘 다 낸다. */
      borderline: z.boolean(),
      bbox: z.array(z.number()).length(4),
      basis: z.string(),
    }),
  ),
});
export type DocChartGold = z.infer<typeof docChartGoldSchema>;
