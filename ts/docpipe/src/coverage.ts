import type { Chunk } from "./chunk.js";
import type { ParsedDocument } from "./document.js";
import type { ChunkRecord } from "./store.js";
import type { TranslateOutput, TranslationResult } from "./translate.js";

export interface CoverageReport {
  /** 누락도 중복도 남는 것도 없다. `notTranslated` (실패하거나 안 돈 조각)는 따로 센다. */
  ok: boolean;
  sourceBlocks: number;
  resultBlocks: number;
  /** 원문에 있는데 결과에 없는 블록 */
  missingBlocks: string[];
  /** 결과에 두 번 이상 나오는 블록 */
  duplicatedBlocks: string[];
  /** 결과에 있는데 원문에 없는 블록 */
  extraBlocks: string[];
  missingTables: string[];
  duplicatedTables: string[];
  extraTables: string[];
  /** 둘 이상의 조각에 실린 항목 id */
  itemsInSeveralChunks: string[];
  /** 끝났다고 기록된 조각에서 번역이 빠진 항목 id */
  itemsMissingInDoneChunks: string[];
  /** 조각에 없던 id 로 돌아온 것 (조각 id → id 목록) */
  unexpectedItems: Record<string, string[]>;
  /** 조각이 실패했거나 아직 안 돌아 번역되지 않은 블록·표 */
  notTranslated: string[];
}

function diff(expected: string[], actual: string[]): { missing: string[]; duplicated: string[]; extra: string[] } {
  const expectedSet = new Set(expected);
  const seen = new Set<string>();
  const duplicated = new Set<string>();
  for (const id of actual) {
    if (seen.has(id)) duplicated.add(id);
    seen.add(id);
  }
  return {
    missing: expected.filter((id) => !seen.has(id)),
    duplicated: [...duplicated],
    extra: [...seen].filter((id) => !expectedSet.has(id)),
  };
}

/**
 * 원문 블록 id 와 번역 결과의 블록 id 가 하나씩 대응하는지 본다. 조립 코드를 믿지 않고
 * 원문과 결과, 조각과 기록을 따로 대조한다.
 */
export function checkCoverage(
  document: ParsedDocument,
  result: TranslationResult,
  chunks: Chunk[],
  records: (ChunkRecord<TranslateOutput> | undefined)[],
): CoverageReport {
  const blocks = diff(document.blocks.map((block) => block.id), result.blocks.map((block) => block.id));
  const tables = diff(document.tables.map((table) => table.id), result.tables.map((table) => table.id));

  const chunkCount = new Map<string, number>();
  const itemsMissingInDoneChunks: string[] = [];
  const unexpectedItems: Record<string, string[]> = {};
  chunks.forEach((chunk, index) => {
    const record = records[index];
    for (const item of chunk.items) {
      chunkCount.set(item.id, (chunkCount.get(item.id) ?? 0) + 1);
      if (record?.status === "done" && (record.output.translations[item.id] ?? "").trim() === "") itemsMissingInDoneChunks.push(item.id);
    }
    if (record?.status === "done") {
      const expected = new Set(chunk.items.map((item) => item.id));
      const unexpected = Object.keys(record.output.translations).filter((id) => !expected.has(id));
      if (unexpected.length > 0) unexpectedItems[chunk.id] = unexpected;
    }
  });
  const itemsInSeveralChunks = [...chunkCount].filter(([, count]) => count > 1).map(([id]) => id);

  const notTranslated = [
    ...result.blocks.filter((block) => block.status === "not_translated").map((block) => block.id),
    ...result.tables.filter((table) => table.status === "not_translated").map((table) => table.id),
  ];

  const ok =
    blocks.missing.length + blocks.duplicated.length + blocks.extra.length + tables.missing.length + tables.duplicated.length + tables.extra.length === 0 &&
    itemsInSeveralChunks.length === 0 &&
    itemsMissingInDoneChunks.length === 0;

  return {
    ok,
    sourceBlocks: document.blocks.length,
    resultBlocks: result.blocks.length,
    missingBlocks: blocks.missing,
    duplicatedBlocks: blocks.duplicated,
    extraBlocks: blocks.extra,
    missingTables: tables.missing,
    duplicatedTables: tables.duplicated,
    extraTables: tables.extra,
    itemsInSeveralChunks,
    itemsMissingInDoneChunks,
    unexpectedItems,
    notTranslated,
  };
}
