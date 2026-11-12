import type { DocModel, Extraction, Fact, Grid } from "../domain/facts.js";
import { normalizeText, scanNumbers } from "../domain/notation.js";

// 문서를 통째로(또는 표 목록으로) 글자로 옮긴다. 방법 C·D·E·F 의 요청에 쓴다.
// 칸 ID 는 `표 ID:r행c열` 이다. `mask` 를 켜면 값 후보 숫자를 고정 기호 하나로 바꾼다 (길이가 같지 않아 자릿수가 새지 않는다).

export const MASK = "▒";

/** 글 속의 값 후보 숫자를 가린다. 연도·식별자·순번은 값 후보가 아니라 남는다. */
export function maskText(text: string): string {
  let out = text;
  for (const token of scanNumbers(text).filter((item) => item.kind === "value").sort((a, b) => b.start - a.start)) out = `${out.slice(0, token.start)}${MASK}${out.slice(token.end)}`;
  return out;
}

export function cellText(grid: Grid, row: number, col: number, raw: string, facts: Map<string, Fact>, mask: boolean): string {
  const text = normalizeText(raw);
  if (!mask) return text;
  const fact = facts.get(`${grid.id}:r${row}c${col}`);
  // 숫자 칸은 칸 전체를 기호 하나로 (단위 접미는 남긴다). 글 칸은 그 안의 값 후보만.
  if (fact && fact.bucket === "table_cell") return `${MASK}${fact.unitSuffix ?? ""}`;
  return maskText(text);
}

export interface RenderOptions {
  mask: boolean;
  /** 쪽 범위 (양 끝 포함). 없으면 전부. */
  pages?: { from: number; to: number };
}

/** 문서를 쪽 순서, 쪽 안에서는 읽는 순서대로 옮긴다. 격자를 세우지 못한 표는 싣지 못한다 (그 사실을 한 줄 남긴다). */
export function renderDocument(document: DocModel, extraction: Extraction, options: RenderOptions): string {
  const grids = new Map(extraction.grids.map((grid) => [grid.id, grid]));
  const facts = new Map(extraction.facts.map((fact) => [fact.id, fact]));
  const lines: string[] = [`# 문서 ${extraction.docId} (${extraction.fileName})`];
  const pages = [...new Set(document.blocks.map((block) => block.page))].sort((a, b) => a - b).filter((page) => !options.pages || (page >= options.pages.from && page <= options.pages.to));
  for (const page of pages) {
    lines.push(`## ${page}쪽`);
    for (const block of document.blocks.filter((item) => item.page === page).sort((a, b) => a.order - b.order)) {
      if (block.kind !== "table") {
        const text = normalizeText(block.text ?? "");
        if (text !== "") lines.push(options.mask ? maskText(text) : text);
        continue;
      }
      const grid = grids.get(block.table_id as string);
      if (!grid) {
        lines.push(`[표 ${block.table_id}: 격자를 세우지 못해 싣지 못함]`);
        continue;
      }
      lines.push(`[표 ${grid.id}]`);
      grid.cells.forEach((cells, row) => {
        const parts = cells.map((raw, col) => [col, cellText(grid, row, col, raw, facts, options.mask)] as const).filter(([, text]) => text !== "");
        if (parts.length > 0) lines.push(`r${row} | ${parts.map(([col, text]) => `c${col}: ${text}`).join(" | ")}`);
      });
      lines.push("[표 끝]");
    }
  }
  return lines.join("\n");
}

/** 값 후보 숫자 칸(표의 숫자 셀)이 있는 표. */
export function tablesWithValues(extraction: Extraction): Grid[] {
  const withValues = new Set(extraction.facts.filter((fact) => fact.bucket === "table_cell").map((fact) => fact.where.gridId));
  return extraction.grids.filter((grid) => withValues.has(grid.id));
}

const unique = (items: string[]): string[] => [...new Set(items.filter((item) => item !== ""))];

/** 방법 F 의 표 목록: 표마다 ID, 제목·각주, 열 머리글, 행 머리글. 값 후보 숫자는 가린다 (숫자 칸은 싣지 않는다). */
export function renderTableList(extraction: Extraction): string {
  const byGrid = new Map<string, Fact[]>();
  for (const fact of extraction.facts) if (fact.bucket === "table_cell" && fact.where.gridId) byGrid.set(fact.where.gridId, [...(byGrid.get(fact.where.gridId) ?? []), fact]);
  const lines: string[] = [`# 문서 ${extraction.docId} (${extraction.fileName})`];
  for (const grid of tablesWithValues(extraction)) {
    const facts = byGrid.get(grid.id) ?? [];
    const rows = new Map<number, string>();
    for (const fact of facts) if (!rows.has(fact.where.row as number)) rows.set(fact.where.row as number, (fact.headers?.row ?? []).map((ref) => maskText(ref.text)).join(" > "));
    const cols = unique(facts.map((fact) => (fact.headers?.col ?? []).map((ref) => maskText(ref.text)).join(" > ")));
    lines.push(`[표 ${extraction.docId}:${grid.id}] ${grid.page}쪽`);
    lines.push(`제목·각주: ${grid.titleCandidates.map(maskText).join(" / ") || "(없음)"}`);
    if (grid.unitLines.length > 0) lines.push(`단위 줄: ${grid.unitLines.map(maskText).join(" / ")}`);
    lines.push(`열 머리글: ${cols.join(" | ") || "(없음)"}`);
    lines.push(`행 머리글: ${unique([...rows.values()]).join(" ; ") || "(없음)"}`);
  }
  return lines.join("\n");
}

/** 방법 F 의 행 짝 요청: 한 표의 제목·각주와 아직 짝이 없는 행의 목록. 값 후보 숫자는 가린다. */
export function renderRowList(name: string, grid: Pick<Grid, "titleCandidates">, rows: { id: string; header: string }[]): string {
  return `[문서 ${name} 의 표] 제목·각주: ${grid.titleCandidates.map(maskText).join(" / ") || "(없음)"}\n${rows.map((row) => `${row.id}: ${maskText(row.header) || "(머리글 없음)"}`).join("\n")}`;
}

/**
 * 보수적인 토큰 어림: 한글·한자 1자 = 1토큰, 그 밖 1자 = 0.6토큰.
 * 밴드 Q 의 실측 맞춤(한글 0.62, 그 밖 0.54)보다 크게 잡았다. 272,000 토큰(장문 단가의 경계)을 넘는 요청을 만들지 않기 위한 값이고 과금 근거가 아니다.
 */
export function conservativeTokens(text: string): number {
  let cjk = 0;
  for (const char of text) if (/[ㄱ-ㆎ가-힣一-鿿]/.test(char)) cjk += 1;
  return Math.ceil(cjk + (text.length - cjk) * 0.6);
}
