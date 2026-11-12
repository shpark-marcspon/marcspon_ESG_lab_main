import { z } from "zod";
import type { CallRequest, LlmClient, Tags } from "llm";
import type { Chunk } from "./chunk.js";
import type { DocTable, ParsedDocument } from "./document.js";
import { renderGlossary, type Glossary } from "./glossary.js";
import { describeLocation, failureOf, renderItems } from "./llm-work.js";
import { GRADE_BASIS, gradeNumberChange, hasNumberDiff, numberDiff, type NumberChangeGrade, type SuspectGrade } from "./numbercheck.js";
import { numberRuns } from "./numeric.js";
import type { WorkResult } from "./runner.js";
import { cellId, planCell, type Disposition, type UnitPlan } from "./select.js";
import type { ChunkFailure, ChunkRecord } from "./store.js";
import type { UnitTable } from "./units.js";

export const TRANSLATE_PROMPT = "docpipe/translate@1";

const outputSchema = z.object({ items: z.array(z.object({ id: z.string(), text: z.string() })) });
type TranslateData = z.infer<typeof outputSchema>;

/** 조각 하나의 저장 결과. 받은 것을 그대로 두고, 어느 것을 쓸지는 조립 단계가 정한다. */
export interface TranslateOutput {
  translations: Record<string, string>;
  /** 조각에 없던 id 로 돌아온 것. 쓰지 않는다. */
  unexpectedIds: string[];
}

/**
 * 표 셀의 번역문 숫자가 원문과 다를 때의 처리.
 * - `flag`   (기본) 조각을 실패시키지 않고 다시 부르지 않는다. 번역문을 쓰되 그 셀에 `number_change_suspected` 표시와 차이를 남긴다.
 * - `strict` 옛 규칙. 조각을 `table_number_changed` 로 실패시키고 다시 부른다. 비교 측정용으로 남겨 둔다.
 */
export type TableNumberRule = "flag" | "strict";

export interface TranslateContext {
  client: LlmClient;
  route: string;
  tags: Tags;
  document: ParsedDocument;
  glossary: Glossary;
  maxOutputTokens?: number;
  /** 기본 `flag`. */
  tableNumbers?: TableNumberRule;
}

export function translateRequest(chunk: Chunk, context: Omit<TranslateContext, "client">): CallRequest<TranslateData> {
  return {
    prompt: TRANSLATE_PROMPT,
    vars: {
      glossary: renderGlossary(context.glossary),
      document: context.document.source.file_name,
      location: describeLocation(chunk, context.document.sections),
      items: renderItems(chunk.items),
    },
    route: context.route,
    tags: context.tags,
    schema: outputSchema,
    schemaName: "translation",
    ...(context.maxOutputTokens === undefined ? {} : { maxOutputTokens: context.maxOutputTokens }),
  };
}

/** 조각 하나를 번역한다. 빠진 id, 겹친 id, 빈 번역이 있으면 성공으로 치지 않는다. */
export async function translateChunk(chunk: Chunk, context: TranslateContext): Promise<WorkResult<TranslateOutput>> {
  const result = await context.client.call(translateRequest(chunk, context));
  const costUsd = result.meta.totalCostUsd;
  const warnings = result.meta.warnings.map((warning) => warning.code as string);
  if (result.kind !== "success") return { ok: false, error: failureOf(result), costUsd, warnings };

  const expected = new Set(chunk.items.map((item) => item.id));
  const received = new Map<string, string>();
  const duplicated: string[] = [];
  const unexpectedIds: string[] = [];
  for (const item of result.data.items) {
    if (received.has(item.id)) duplicated.push(item.id);
    received.set(item.id, item.text);
    if (!expected.has(item.id)) unexpectedIds.push(item.id);
  }
  const translations: Record<string, string> = Object.fromEntries(received);
  const missing = chunk.items.filter((item) => (received.get(item.id) ?? "").trim() === "").map((item) => item.id);

  let error: ChunkFailure | undefined;
  if (duplicated.length > 0) error = { kind: "duplicate_ids", retryable: true, message: `같은 id 가 두 번 돌아왔다: ${duplicated.join(", ")}` };
  else if (missing.length > 0) error = { kind: "incomplete_output", retryable: true, message: `번역이 빠졌거나 비었다: ${missing.join(", ")}` };
  else {
    // 한글과 숫자가 섞인 표 셀(`2023년`, `59,498명`)은 LLM 을 거친다. 그 숫자가 원문과 다른 셀을 찾는다.
    // 기본 규칙에서는 조각을 실패시키지 않는다. 셀별 표시와 차이는 조립 단계(`assembleTranslation`)가 저장된 번역에서 다시 계산해 결과에 싣는다.
    const changed = chunk.items.filter((item) => isCellId(item.id) && hasNumberDiff(numberDiff(item.text, received.get(item.id) as string))).map((item) => item.id);
    if (changed.length > 0) {
      if ((context.tableNumbers ?? "flag") === "strict") error = { kind: "table_number_changed", retryable: true, message: `표 셀의 숫자가 원문과 다르다: ${changed.join(", ")}` };
      else warnings.push("table_number_change_suspected");
    }
  }
  if (error) return { ok: false, error, costUsd, warnings };

  if (unexpectedIds.length > 0) warnings.push("unexpected_ids");
  return { ok: true, output: { translations, unexpectedIds }, costUsd, warnings };
}

function isCellId(id: string): boolean {
  return /:r\d+c\d+$/.test(id);
}

/** 두 글자의 숫자 덩어리가 개수까지 같은가 (순서는 보지 않는다). */
export function sameNumbers(source: string, target: string): boolean {
  const a = numberRuns(source).sort();
  const b = numberRuns(target).sort();
  return a.length === b.length && a.every((run, index) => run === b[index]);
}

/** 표 셀에 붙는 표시. 표시가 없는 셀은 원본을 그대로 옮겼거나, 모델이 옮겼고 숫자가 원문과 같다. */
export type CellFlag =
  /** 단위 셀. 모델에 보내지 않고 고정 번역 표에서 옮겼다. 숫자 대조 대상이 아니다. */
  | { kind: "unit_fixed"; id: string; row: number; col: number; source: string; text: string }
  /** 단위 셀인데 고정 번역 표에 없는 낱말(`unknown`)이 있어 모델에 보냈다. 실패가 아니다. */
  | { kind: "unit_without_fixed_translation"; id: string; row: number; col: number; source: string; unknown: string[] }
  /** 모델이 옮긴 글의 숫자가 원문과 다르다. 번역문은 그대로 썼다. 사람이 봐야 한다. */
  | ({ kind: "number_change_suspected"; id: string; row: number; col: number; source: string; target: string; sourceOnly: string[]; targetOnly: string[] } & Pick<NumberChangeGrade, "grade" | "explained">);

function assembleCells(table: DocTable, translations: Record<string, string>, units?: UnitTable | null): { cells: string[][]; flags: CellFlag[] } {
  const flags: CellFlag[] = [];
  const cells = table.cells.map((row, rowIndex) =>
    row.map((cell, colIndex) => {
      const plan = planCell(cell, units);
      if (plan.kind === "copy") return cell;
      const id = cellId(table.id, rowIndex, colIndex);
      const at = { id, row: rowIndex, col: colIndex, source: plan.source };
      if (plan.kind === "fixed") {
        flags.push({ kind: "unit_fixed", ...at, text: plan.text });
        return plan.text;
      }
      const translated = translations[id];
      if (translated === undefined) throw new Error(`표 ${table.id} 의 셀 r${rowIndex}c${colIndex} 번역이 없다`);
      if (plan.unitWithoutFixed) flags.push({ kind: "unit_without_fixed_translation", ...at, unknown: plan.unitWithoutFixed });
      const diff = numberDiff(plan.source, translated);
      if (hasNumberDiff(diff)) {
        const { grade, explained } = gradeNumberChange(plan.source, translated, diff);
        flags.push({ kind: "number_change_suspected", ...at, target: translated, ...diff, grade, explained });
      }
      return translated;
    }),
  );
  return { cells, flags };
}

/**
 * 번역된 표를 조립한다. 숫자 셀을 비롯해 한글·한자가 없는 셀은 LLM 이 무엇을 돌려줬든 원본 글자를 쓴다.
 * 고정 번역 표(`units`)를 주면 표에 있는 단위 셀은 표의 글자를 쓴다. 번역 대상 셀의 번역이 없으면 원문으로 메우지 않고 오류로 낸다.
 */
export function assembleTable(table: DocTable, translations: Record<string, string>, units?: UnitTable | null): string[][] {
  return assembleCells(table, translations, units).cells;
}

export type BlockOutcome =
  | { id: string; kind: "paragraph" | "heading"; page: number; status: "translated"; chunkId: string; text: string }
  | { id: string; kind: "paragraph" | "heading"; page: number; status: "copied"; reason: string; text: string }
  | { id: string; kind: "paragraph" | "heading"; page: number; status: "excluded"; reason: string; text: null }
  | { id: string; kind: "paragraph" | "heading"; page: number; status: "not_translated"; chunkId: string; reason: string; text: null }
  | { id: string; kind: "table"; page: number; status: "table"; tableId: string };

export interface TableOutcome {
  id: string;
  page: number;
  /** 밴드 A 가 매긴 표 추출 상태. `low_confidence` 는 표가 아닌 것(목차, 도표 숫자)일 수 있다. */
  sourceStatus: DocTable["status"];
  /** `fixed` = 한글 셀이 전부 고정 번역하는 단위 셀이라 모델을 거치지 않고 옮겼다. */
  status: "translated" | "copied" | "fixed" | "not_translated" | "source_failed";
  chunkId: string | null;
  reason: string | null;
  /** `not_translated`, `source_failed` 면 null. 원문으로 메우지 않는다. */
  cells: string[][] | null;
  header: DocTable["header"];
  /** 밴드 A 가 숫자 셀에서 떼어 둔 각주 위첨자. 그대로 옮긴다. */
  footnote_marks: DocTable["footnote_marks"];
  notes: string[];
  /** 셀별 표시. `cells` 가 null 인 표(`not_translated`, `source_failed`)는 비어 있고, 그 표의 셀은 숫자 대조를 하지 못한 것이다. */
  cellFlags: CellFlag[];
}

/** 표 셀 처리의 합계. 「의심 0」 과 「검사 안 함」 을 가르려고 분모를 같이 싣는다. */
export interface TableCellReport {
  /** 숫자가 달라진 셀을 어떻게 다뤘는가. `strict` 면 달라진 조각은 실패로 남아 `numberNotChecked` 에 들어간다. */
  tableNumbers: TableNumberRule;
  /** 고정 번역 표. null 이면 단위 셀도 모델에 보냈다 (옛 동작). */
  unitTable: { path: string; synthetic: boolean } | null;
  /** 모델에 보내는 표 셀 (계획 기준). `numberChecked + numberNotChecked` 와 같다. */
  sentCells: number;
  /** 끝난 조각의 셀. 번역문의 숫자를 원문과 견줬다. */
  numberChecked: number;
  /** 조각이 실패했거나 안 돌아 견주지 못한 셀. 0 이 아니면 `suspected` 는 전체의 수가 아니다. */
  numberNotChecked: number;
  /** `numberChecked` 가운데 숫자가 달라 표시가 붙은 셀. */
  suspected: number;
  suspectedByGrade: Record<SuspectGrade, number>;
  gradeBasis: string;
  /** 모델에 보내지 않고 고정 번역 표로 옮기는 단위 셀 (계획 기준. 추출 실패 표는 셀이 없어 빠진다). */
  unitFixed: number;
  /** 단위 셀인데 표에 없는 낱말이 있어 모델에 보낸 셀 (계획 기준). */
  unitWithoutFixed: number;
}

export interface TranslationResult {
  format: "docpipe.translation/0.1";
  source: ParsedDocument["source"];
  blocks: BlockOutcome[];
  tables: TableOutcome[];
  tableCells: TableCellReport;
}

export interface AssembleOptions {
  units?: UnitTable | null;
  tableNumbers?: TableNumberRule;
}

/** 사람이 훑어볼 의심 셀 한 줄. `other` 등급이 앞에 온다. */
export interface SuspectCell {
  chunkId: string | null;
  tableId: string;
  page: number;
  cellId: string;
  row: number;
  col: number;
  grade: SuspectGrade;
  source: string;
  target: string;
  sourceOnly: string[];
  targetOnly: string[];
  explained: NumberChangeGrade["explained"];
}

export function suspectCells(result: TranslationResult): SuspectCell[] {
  const cells = result.tables.flatMap((table) =>
    table.cellFlags.flatMap((flag) =>
      flag.kind === "number_change_suspected"
        ? [{ chunkId: table.chunkId, tableId: table.id, page: table.page, cellId: flag.id, row: flag.row, col: flag.col, grade: flag.grade, source: flag.source, target: flag.target, sourceOnly: flag.sourceOnly, targetOnly: flag.targetOnly, explained: flag.explained }]
        : [],
    ),
  );
  return [...cells.filter((cell) => cell.grade === "other"), ...cells.filter((cell) => cell.grade !== "other")];
}

/** 조각 결과를 문서 순서로 합친다. 실패했거나 아직 안 돈 조각의 블록은 `not_translated` 로 남는다. */
export function assembleTranslation(
  document: ParsedDocument,
  plan: UnitPlan,
  chunks: Chunk[],
  records: (ChunkRecord<TranslateOutput> | undefined)[],
  options: AssembleOptions = {},
): TranslationResult {
  const units = options.units ?? null;
  const cellReport: TableCellReport = {
    tableNumbers: options.tableNumbers ?? "flag",
    unitTable: units ? { path: units.path, synthetic: units.synthetic } : null,
    sentCells: 0,
    numberChecked: 0,
    numberNotChecked: 0,
    suspected: 0,
    suspectedByGrade: { notation_like: 0, other: 0 },
    gradeBasis: GRADE_BASIS,
    unitFixed: 0,
    unitWithoutFixed: 0,
  };
  const chunkOfUnit = new Map<string, { chunk: Chunk; record: ChunkRecord<TranslateOutput> | undefined }>();
  chunks.forEach((chunk, index) => {
    for (const unitId of chunk.unitIds) chunkOfUnit.set(unitId, { chunk, record: records[index] });
  });
  const tablesById = new Map(document.tables.map((table) => [table.id, table]));
  const whyNot = (record: ChunkRecord<TranslateOutput> | undefined): string => (record?.status === "failed" ? `chunk_failed:${record.error.kind}` : "chunk_not_run");

  const blocks: BlockOutcome[] = [];
  const tables: TableOutcome[] = [];
  for (const block of document.blocks) {
    const disposition = plan.dispositions.get(block.id) as Disposition | undefined;
    if (!disposition) throw new Error(`블록 ${block.id} 에 처리 방식이 매겨지지 않았다`);
    const placed = chunkOfUnit.get(block.id);
    if (disposition.kind === "send" && !placed) throw new Error(`블록 ${block.id} 가 어느 조각에도 없다`);

    if (block.kind === "table") {
      const table = tablesById.get(block.table_id as string) as DocTable;
      blocks.push({ id: block.id, kind: "table", page: block.page, status: "table", tableId: table.id });
      const base = { id: table.id, page: table.page, sourceStatus: table.status, header: table.header, footnote_marks: table.footnote_marks, notes: table.notes };
      const plans = table.cells.flatMap((row) => row.map((cell) => planCell(cell, units)));
      const sent = plans.filter((plan) => plan.kind === "send");
      cellReport.sentCells += sent.length;
      cellReport.unitFixed += plans.filter((plan) => plan.kind === "fixed").length;
      cellReport.unitWithoutFixed += sent.filter((plan) => plan.kind === "send" && plan.unitWithoutFixed).length;
      if (disposition.kind === "source_failed") tables.push({ ...base, status: "source_failed", chunkId: null, reason: disposition.reason, cells: null, cellFlags: [] });
      else if (disposition.kind === "fixed") tables.push({ ...base, status: "fixed", chunkId: null, reason: disposition.reason, ...renameFlags(assembleCells(table, {}, units)) });
      else if (disposition.kind !== "send") tables.push({ ...base, status: "copied", chunkId: null, reason: disposition.reason, cells: table.cells.map((row) => [...row]), cellFlags: [] });
      else if (placed?.record?.status === "done") {
        const assembled = assembleCells(table, placed.record.output.translations, units);
        cellReport.numberChecked += sent.length;
        for (const flag of assembled.flags) {
          if (flag.kind !== "number_change_suspected") continue;
          cellReport.suspected += 1;
          cellReport.suspectedByGrade[flag.grade] += 1;
        }
        tables.push({ ...base, status: "translated", chunkId: placed.chunk.id, reason: null, ...renameFlags(assembled) });
      } else {
        cellReport.numberNotChecked += sent.length;
        tables.push({ ...base, status: "not_translated", chunkId: (placed as { chunk: Chunk }).chunk.id, reason: whyNot(placed?.record), cells: null, cellFlags: [] });
      }
      continue;
    }

    const head = { id: block.id, kind: block.kind, page: block.page };
    const text = block.text as string;
    if (disposition.kind === "copy") blocks.push({ ...head, status: "copied", reason: disposition.reason, text });
    else if (disposition.kind !== "send") blocks.push({ ...head, status: "excluded", reason: disposition.reason, text: null });
    else if (placed?.record?.status === "done") {
      const translated = placed.record.output.translations[block.id];
      if (translated === undefined) throw new Error(`블록 ${block.id} 의 번역이 끝난 조각 ${placed.chunk.id} 에 없다`);
      blocks.push({ ...head, status: "translated", chunkId: placed.chunk.id, text: translated });
    } else blocks.push({ ...head, status: "not_translated", chunkId: (placed as { chunk: Chunk }).chunk.id, reason: whyNot(placed?.record), text: null });
  }
  return { format: "docpipe.translation/0.1", source: document.source, blocks, tables, tableCells: cellReport };
}

function renameFlags(assembled: { cells: string[][]; flags: CellFlag[] }): { cells: string[][]; cellFlags: CellFlag[] } {
  return { cells: assembled.cells, cellFlags: assembled.flags };
}
