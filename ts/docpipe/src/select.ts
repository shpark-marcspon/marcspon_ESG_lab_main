import type { DocBlock, DocTable, ParsedDocument } from "./document.js";
import { classifyText, type TextClass } from "./numeric.js";
import { unitCell, type UnitTable } from "./units.js";

/** LLM 에 보내는 글자 한 조각. 블록이면 id 는 블록 id, 표의 셀이면 `<표 id>:r<행>c<열>` 이다. */
export interface Item {
  id: string;
  text: string;
}

export interface TextUnit {
  type: "text";
  /** 블록 id */
  id: string;
  blockKind: "paragraph" | "heading";
  page: number;
  sectionId: string | null;
  items: [Item];
}

export interface TableUnit {
  type: "table";
  /** 블록 id */
  id: string;
  tableId: string;
  page: number;
  sectionId: string | null;
  /** 번역 대상인 셀(머리글, 항목명)만. 숫자 셀과 고정 번역하는 단위 셀은 여기에 없다. */
  items: Item[];
}

export type Unit = TextUnit | TableUnit;

export type CopyReason = Exclude<TextClass, "translatable">;

/** 블록 하나가 이 파이프라인에서 어떻게 처리되는지. 모든 블록이 정확히 하나를 받는다. */
export type Disposition =
  | { kind: "send" }
  | { kind: "copy"; reason: CopyReason | "table_without_translatable_cells" }
  /** 한글 셀이 전부 고정 번역하는 단위 셀이라 모델에 보낼 것이 없는 표. 조립 단계가 표에서 옮긴다. */
  | { kind: "fixed"; reason: "table_with_only_fixed_unit_cells" }
  | { kind: "excluded"; reason: "repeated_furniture" }
  | { kind: "source_failed"; reason: "table_extraction_failed" };

export interface UnitPlan {
  /** 읽는 순서(쪽, 쪽 안 순서)대로. */
  units: Unit[];
  dispositions: Map<string, Disposition>;
  furnitureTexts: string[];
}

export interface SelectOptions {
  /** 같은 글자의 블록이 텍스트 있는 쪽의 이 비율 이상에 되풀이되면 머리말·꼬리말·내비게이션으로 본다. */
  furnitureMinPageShare: number;
  furnitureMinPages: number;
  /** 단위 셀의 고정 번역 표. 주면 표에 있는 단위 셀을 모델에 보내지 않는다. 없으면 단위 셀도 보낸다 (옛 동작). */
  units?: UnitTable | null;
}

export function cellId(tableId: string, row: number, col: number): string {
  return `${tableId}:r${row}c${col}`;
}

/** 제어 문자를 지운다. 삼성전자 국문판의 글머리표 뒤에 BEL(0x07)이 섞여 있다. */
export function cleanText(text: string): string {
  return text.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "").trim();
}

function normalizeForRepeat(text: string): string {
  return text.normalize("NFKC").replace(/\s+/g, " ").trim();
}

/**
 * 쪽마다 되풀이되는 글자(머리말, 꼬리말, 내비게이션)를 찾는다. 밴드 A 는 이것들을 paragraph 로 내므로
 * 걸러 내지 않으면 조각마다 같은 글자를 번역시키게 된다.
 */
export function findFurniture(document: ParsedDocument, options: SelectOptions): Set<string> {
  const pagesWithText = document.pages.filter((page) => page.text_layer === "present").length;
  const pagesByText = new Map<string, Set<number>>();
  for (const block of document.blocks) {
    if (block.kind === "table") continue;
    const key = normalizeForRepeat(block.text ?? "");
    if (key === "") continue;
    let pages = pagesByText.get(key);
    if (!pages) pagesByText.set(key, (pages = new Set()));
    pages.add(block.page);
  }
  const furniture = new Set<string>();
  for (const [text, pages] of pagesByText) {
    if (pages.size >= options.furnitureMinPages && pages.size / Math.max(pagesWithText, 1) >= options.furnitureMinPageShare) furniture.add(text);
  }
  return furniture;
}

/**
 * 표 셀 하나를 어떻게 다루는지. 고르기(`translatableCells`)와 조립(`translate.ts`)이 같은 판정을 쓰도록 여기 한 곳에 둔다.
 * - `copy`  한글·한자가 없다. 원본 글자를 그대로 옮긴다.
 * - `fixed` 단위 셀이고 고정 번역 표에 있다. 모델에 보내지 않고 표의 글자를 쓴다.
 * - `send`  모델에 보낸다. `unitWithoutFixed` 가 있으면 단위 셀인데 표에 없는 낱말이 있어 보낸 것이다.
 */
export type CellPlan = { kind: "copy" } | { kind: "fixed"; source: string; text: string } | { kind: "send"; source: string; unitWithoutFixed?: string[] };

export function planCell(cell: string, units?: UnitTable | null): CellPlan {
  if (classifyText(cell) !== "translatable") return { kind: "copy" };
  const source = cleanText(cell);
  if (!units) return { kind: "send", source };
  const unit = unitCell(source, units);
  if (unit.kind === "fixed") return { kind: "fixed", source, text: unit.text };
  return unit.kind === "no_fixed_translation" ? { kind: "send", source, unitWithoutFixed: unit.unknown } : { kind: "send", source };
}

/** 표에서 모델에 보낼 셀만 뽑는다. `failed` 인 표는 셀이 없으므로 빈 배열이 아니라 호출 쪽에서 따로 다룬다. */
export function translatableCells(table: DocTable, units?: UnitTable | null): Item[] {
  const items: Item[] = [];
  table.cells.forEach((row, rowIndex) => {
    row.forEach((cell, colIndex) => {
      const plan = planCell(cell, units);
      if (plan.kind === "send") items.push({ id: cellId(table.id, rowIndex, colIndex), text: plan.source });
    });
  });
  return items;
}

/**
 * 문서의 모든 블록에 처리 방식을 하나씩 매기고, LLM 에 보낼 단위를 읽는 순서대로 낸다.
 * `low_confidence` 표는 목차·도표 숫자의 오탐이 많지만 셀의 글자는 텍스트 계층 그대로이므로
 * 글자 셀은 번역하고 표의 상태를 결과에 그대로 싣는다. `failed` 표는 셀이 없어 번역할 것이 없고
 * `source_failed` 로 남긴다.
 */
export function planUnits(document: ParsedDocument, options: SelectOptions): UnitPlan {
  const furniture = findFurniture(document, options);
  const tables = new Map(document.tables.map((table) => [table.id, table]));
  const units: Unit[] = [];
  const dispositions = new Map<string, Disposition>();

  const ordered = [...document.blocks].sort((a, b) => a.page - b.page || a.order - b.order);
  for (const block of ordered) {
    if (block.kind === "table") {
      const table = tables.get(block.table_id as string) as DocTable;
      if (table.status === "failed") {
        dispositions.set(block.id, { kind: "source_failed", reason: "table_extraction_failed" });
        continue;
      }
      const items = translatableCells(table, options.units);
      if (items.length === 0) {
        const fixedOnly = table.cells.some((row) => row.some((cell) => planCell(cell, options.units).kind === "fixed"));
        dispositions.set(block.id, fixedOnly ? { kind: "fixed", reason: "table_with_only_fixed_unit_cells" } : { kind: "copy", reason: "table_without_translatable_cells" });
        continue;
      }
      units.push({ type: "table", id: block.id, tableId: table.id, page: block.page, sectionId: block.section_id, items });
      dispositions.set(block.id, { kind: "send" });
      continue;
    }
    dispositions.set(block.id, textDisposition(block, furniture));
    if (dispositions.get(block.id)?.kind !== "send") continue;
    units.push({
      type: "text",
      id: block.id,
      blockKind: block.kind,
      page: block.page,
      sectionId: block.section_id,
      items: [{ id: block.id, text: cleanText(block.text as string) }],
    });
  }
  return { units, dispositions, furnitureTexts: [...furniture] };
}

function textDisposition(block: DocBlock, furniture: Set<string>): Disposition {
  const text = block.text as string;
  if (furniture.has(normalizeForRepeat(text))) return { kind: "excluded", reason: "repeated_furniture" };
  const textClass = classifyText(cleanText(text));
  return textClass === "translatable" ? { kind: "send" } : { kind: "copy", reason: textClass };
}
