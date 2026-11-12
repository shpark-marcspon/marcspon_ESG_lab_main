import { existsSync, readFileSync } from "node:fs";
import { z } from "zod";

/**
 * 밴드 A(`py/docparse`)가 낸 문서 JSON 의 읽는 쪽 정의. 형식의 정본은 `schemas/document.schema.json` 이고
 * 여기서는 이 파이프라인이 쓰는 필드만 검증한다.
 */
const sectionSchema = z.object({
  id: z.string(),
  title: z.string().nullable(),
  level: z.number().int(),
  page_start: z.number().int(),
  page_end: z.number().int(),
  basis: z.enum(["pdf_outline", "page_top_largest_font", "no_title_found"]),
});

const blockSchema = z.object({
  id: z.string(),
  kind: z.enum(["paragraph", "table", "heading"]),
  page: z.number().int(),
  order: z.number().int(),
  section_id: z.string().nullable(),
  text: z.string().optional(),
  table_id: z.string().optional(),
});

const tableSchema = z.object({
  id: z.string(),
  page: z.number().int(),
  n_rows: z.number().int(),
  n_cols: z.number().int(),
  cells: z.array(z.array(z.string())),
  header: z.object({ rows: z.array(z.number().int()), cols: z.array(z.number().int()), basis: z.string() }),
  method: z.enum(["lines", "text", "none"]),
  status: z.enum(["ok", "low_confidence", "failed"]),
  notes: z.array(z.string()),
  footnote_marks: z.array(z.object({ row: z.number().int(), col: z.number().int(), mark: z.string() })),
});

/** 읽는 쪽 사본. 계약 테스트(`test/schema-contract.test.ts`)가 정본과 구조를 대조할 수 있게 공개한다. */
export const documentSchema = z.object({
  schema_version: z.literal("0.1"),
  source: z.object({ file_name: z.string(), sha256: z.string(), page_count: z.number().int() }),
  parser: z.object({ name: z.string(), version: z.string() }),
  pages: z.array(z.object({ page: z.number().int(), text_layer: z.enum(["present", "none"]) })),
  sections: z.array(sectionSchema),
  blocks: z.array(blockSchema),
  tables: z.array(tableSchema),
});

export type DocSection = z.infer<typeof sectionSchema>;
export type DocBlock = z.infer<typeof blockSchema>;
export type DocTable = z.infer<typeof tableSchema>;
export type ParsedDocument = z.infer<typeof documentSchema>;

export class DocumentError extends Error {
  override name = "DocumentError";
}

/** 문서 JSON 을 검증한다. 블록과 표의 짝이 안 맞으면 빈 결과로 넘기지 않고 오류로 낸다. */
export function parseDocument(raw: unknown, label = "document"): ParsedDocument {
  const parsed = documentSchema.safeParse(raw);
  if (!parsed.success) throw new DocumentError(`${label}: 문서 JSON 형식이 맞지 않는다\n${z.prettifyError(parsed.error)}`);
  const document = parsed.data;

  const tableIds = new Set(document.tables.map((table) => table.id));
  const blockIds = new Set<string>();
  for (const block of document.blocks) {
    if (blockIds.has(block.id)) throw new DocumentError(`${label}: 블록 id 가 겹친다: ${block.id}`);
    blockIds.add(block.id);
    if (block.kind === "table") {
      if (!block.table_id || !tableIds.has(block.table_id)) throw new DocumentError(`${label}: 블록 ${block.id} 가 없는 표를 가리킨다: ${block.table_id}`);
    } else if (block.text === undefined) {
      throw new DocumentError(`${label}: 블록 ${block.id} 에 text 가 없다`);
    }
  }
  return document;
}

export function loadDocument(path: string): ParsedDocument {
  if (!existsSync(path)) throw new DocumentError(`문서 JSON 이 없다: ${path}`);
  let raw: unknown;
  try {
    raw = JSON.parse(readFileSync(path, "utf8"));
  } catch (error) {
    throw new DocumentError(`${path}: JSON 으로 읽을 수 없다: ${(error as Error).message}`);
  }
  return parseDocument(raw, path);
}
