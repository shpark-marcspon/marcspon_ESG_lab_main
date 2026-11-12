import { z } from "zod";
import type { Extraction } from "../domain/facts.js";
import { inLabelScope, type Catalog } from "../domain/label.js";
import { normalizeText } from "../domain/notation.js";

// 정답 파일. 표의 숫자 셀마다 「이 숫자의 뜻」을 사람이 달아 둔 것이고, 모델의 답을 여기에 견준다.
// 정답은 지표 목록에 묶여 있다 (지표 키가 목록의 것이다). 목록이 바뀌면 정답도 다시 단다.

const kind = z.enum(["indicator", "not_in_catalog", "not_data"]);

const cellSchema = z.object({
  /** 표 안의 셀 ID (`r1c3`). */
  cell: z.string().regex(/^r\d+c\d+$/),
  /** 그 셀의 원문 글자. 코드가 뽑은 셀과 같은 셀인지 대조한다. */
  raw: z.string().min(1),
  kind,
  indicator: z.string().nullable(),
  period: z.string().regex(/^(19|20)\d{2}$/).nullable(),
  unit: z.string().nullable(),
  boundary: z.string().min(1),
  /** 판단이 갈리는 자리에서 같이 맞다고 볼 값. */
  accept: z.object({ indicator: z.array(z.string()).optional(), period: z.array(z.string().nullable()).optional(), boundary: z.array(z.string()).optional() }).optional(),
  /** 요청에 실리지 않은 글자(쪽의 본문, 다른 각주)까지 보면 알 수 있는 값. 채점에는 쓰지 않는다. */
  truth: z.object({ period: z.string().optional(), boundary: z.string().optional() }).optional(),
  note: z.string().optional(),
});

export const goldSchema = z.object({
  id: z.string().min(1),
  /** 누가 달았고 검토를 받았는지. synthetic 이 아니라 사람이 실제 문서에 단 것이다. */
  status: z.string().min(1),
  author: z.string().min(1),
  createdOn: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  note: z.string(),
  conventions: z.array(z.string()),
  catalog: z.object({ id: z.string(), sha256: z.string() }),
  documents: z.record(z.enum(["a", "b"]), z.object({ fileName: z.string(), pdfSha256: z.string() })),
  tables: z.array(z.object({ doc: z.enum(["a", "b"]), tableId: z.string().min(1), page: z.number().int(), why: z.string(), cells: z.array(cellSchema).min(1) })).min(1),
});

export type Gold = z.infer<typeof goldSchema>;
export type GoldCell = z.infer<typeof cellSchema>;
export type GoldTable = Gold["tables"][number];

/** 정답 셀의 사실 후보 ID. 문서 안에서만 유일하다. */
export const goldFactId = (table: GoldTable, cell: GoldCell): string => `${table.tableId}:${cell.cell}`;

/**
 * 정답이 지금의 문서·지표 목록과 맞는지 본다. 문제를 글로 돌려준다 (비어 있으면 맞는다).
 * - 정답의 표가 문서에 있고, 그 표의 뜻 붙이기 대상 셀과 정답 셀이 하나도 남거나 모자라지 않는다.
 * - 셀의 원문 글자가 같다.
 * - 지표 키와 범위 키가 목록에 있다. 지표인 셀에만 지표 키가 있다.
 */
export function checkGold(gold: Gold, catalog: Catalog & { sha256?: string }, extractions: { a: Extraction; b: Extraction }): string[] {
  const problems: string[] = [];
  if (catalog.sha256 !== undefined && catalog.sha256 !== gold.catalog.sha256) problems.push(`지표 목록이 정답을 달 때의 것과 다르다 (정답 ${gold.catalog.id} ${gold.catalog.sha256.slice(0, 12)}, 지금 ${catalog.id} ${catalog.sha256.slice(0, 12)})`);
  const indicators = new Set(catalog.indicators.map((indicator) => indicator.key));
  const boundaries = new Set(catalog.boundaries.map((boundary) => boundary.key));
  const seen = new Set<string>();
  for (const table of gold.tables) {
    const name = `${table.doc}:${table.tableId}`;
    if (seen.has(name)) problems.push(`${name}: 정답에 같은 표가 두 번 있다`);
    seen.add(name);
    const extraction = extractions[table.doc];
    if (gold.documents[table.doc] && gold.documents[table.doc]?.fileName !== extraction.fileName) problems.push(`${name}: 정답의 문서(${gold.documents[table.doc]?.fileName})와 입력(${extraction.fileName})이 다르다`);
    const targets = new Map(extraction.facts.filter((fact) => fact.where.gridId === table.tableId && inLabelScope(fact, { includeSentences: false })).map((fact) => [fact.id, fact]));
    if (targets.size === 0) problems.push(`${name}: 문서에 그 표가 없거나 뜻 붙이기 대상 셀이 없다`);
    const cells = new Set<string>();
    for (const cell of table.cells) {
      const id = goldFactId(table, cell);
      if (cells.has(id)) problems.push(`${name} ${cell.cell}: 정답에 같은 셀이 두 번 있다`);
      cells.add(id);
      const fact = targets.get(id);
      if (!fact) problems.push(`${name} ${cell.cell}: 뜻 붙이기 대상이 아닌 셀에 정답이 있다`);
      else if (normalizeText(fact.raw) !== cell.raw) problems.push(`${name} ${cell.cell}: 원문 글자가 다르다 (정답 「${cell.raw}」, 문서 「${normalizeText(fact.raw)}」)`);
      if ((cell.kind === "indicator") !== (cell.indicator !== null)) problems.push(`${name} ${cell.cell}: 지표 키는 kind 가 indicator 일 때만, 그리고 반드시 있어야 한다`);
      for (const key of [cell.indicator, ...(cell.accept?.indicator ?? [])]) if (key !== null && !indicators.has(key)) problems.push(`${name} ${cell.cell}: 목록에 없는 지표 키 ${key}`);
      for (const key of [cell.boundary, ...(cell.accept?.boundary ?? []), ...(cell.truth?.boundary ? [cell.truth.boundary] : [])]) if (!boundaries.has(key)) problems.push(`${name} ${cell.cell}: 목록에 없는 범위 키 ${key}`);
    }
    for (const id of targets.keys()) if (!cells.has(id)) problems.push(`${name}: 뜻 붙이기 대상 셀 ${id} 에 정답이 없다`);
  }
  return problems;
}
