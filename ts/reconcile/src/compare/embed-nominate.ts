import type { DocModel, Extraction, Fact, Grid } from "../domain/facts.js";
import { normalizeText, scanNumbers } from "../domain/notation.js";
import { MASK, cellText, maskText } from "./render.js";

// 밴드 EB1 (MARC-TICK-45): 좁혀 묻기(방법 F)의 지목을 값 없이 한다. 단위(표·도표·쪽)마다 가린 글을 만들고,
// 낱말 검색(BM25)·임베딩(코사인)·둘의 순위 합산(RRF)으로 상대 문서의 단위를 줄 세운다.
// 순위 식은 scope3 의 `domain/ranking.ts` 와 같다(BM25 k1·b, 한글 두 글자 조각, RRF k). 그쪽 함수는 `Factor`(계수 한 건)에 묶여 있어
// 여기서는 `{ id, text }` 를 받는 꼴로 얇게 다시 썼다. 두 쪽이 같은 순위를 내는지는 테스트가 본다.
// 이 파일은 입출력이 없고 값을 읽지 않는다 (`Fact.value`·`Fact.raw` 를 쓰지 않는다). 정답은 채점 함수만 받는다.

export type Side = "a" | "b";
export const otherOf = (side: Side): Side => (side === "a" ? "b" : "a");

/** 임베딩 입력 하나의 글자 수 한도. 모델의 입력 한도는 8,192 토큰이고 한국어는 글자당 2토큰 가까이 나온 적이 있다 (밴드 H: 어림의 두 배). 넘는 글은 줄 경계에서 조각으로 나눈다. */
export const CHUNK_CHARS = 3000;
/** 머리글이 선 표로 보는 기준: 숫자 칸 가운데 행 머리글과 열 머리글이 다 있는 칸의 비율. */
export const HEADER_STANDING_SHARE = 0.5;

export interface Unit {
  /** `a:table:p46-t2`, `a:chart-code:p62#2`, `a:page:46`. */
  id: string;
  doc: Side;
  /** `table` · `page` · `chart-<라벨 이름>`. */
  kind: string;
  page: number;
  /** 표 단위면 그 표의 ID. */
  table: string | null;
  /** 이 단위에 든 값 후보의 ID (문서 이름 없이). 정답과 잇는 데만 쓴다. */
  places: string[];
  /** 가린 글. 낱말 검색은 이 글을, 임베딩은 `chunks` 를 본다. */
  text: string;
  chunks: string[];
  /** 표 단위: 숫자 칸 가운데 행·열 머리글이 다 있는 칸의 비율. 숫자 칸이 없으면 null. */
  headerShare: number | null;
}

/** 글에 남은 값 후보 숫자 (`scanNumbers` 가 값이라고 읽는 것). 연도·식별자·순번 꼴은 값 후보가 아니다. */
export const valuesLeft = (text: string): string[] => text.split("\n").flatMap((line) => scanNumbers(line).filter((token) => token.kind === "value").map((token) => token.raw));

/** 글 한 줄을 가린다. 한 번 가린 뒤 새로 값 후보 꼴로 읽히는 숫자가 없을 때까지 되풀이한다 (`ask.ts` 의 `maskName` 과 같은 방식). */
export function maskLine(raw: string, enabled = true): string {
  let text = normalizeText(raw);
  if (!enabled) return text;
  for (let round = 0; round < 4; round++) {
    const next = maskText(text);
    if (next === text) break;
    text = next;
  }
  return text;
}

/** 글을 한도 안의 조각으로 나눈다. 줄 경계에서 끊고, 한 줄이 한도보다 길면 그 줄을 글자 수로 자른다. */
export function chunkText(text: string, limit: number = CHUNK_CHARS): string[] {
  const chunks: string[] = [];
  let current = "";
  const push = (): void => {
    if (current !== "") chunks.push(current);
    current = "";
  };
  for (const line of text.split("\n")) {
    for (let at = 0; at < Math.max(1, line.length); at += limit) {
      const piece = line.slice(at, at + limit);
      if (current !== "" && current.length + 1 + piece.length > limit) push();
      current = current === "" ? piece : `${current}\n${piece}`;
    }
  }
  push();
  return chunks;
}

const unique = (items: string[]): string[] => [...new Set(items.filter((item) => item !== "" && item !== MASK))];

/**
 * 조각들을 ` | ` 로 이어 `머리말 + 조각들` 한 줄로 만든다. 따로는 값 후보가 아니던 조각(머리글 칸의 `1`, `2`)이 나란히 놓이거나 머리말 뒤에 놓이면
 * 값 후보 꼴로 읽힐 수 있어, 이은 줄을 다시 가리고 기호만 남은 조각은 뺀다. 남는 조각이 없으면 빈 글이다.
 */
export function joinPieces(prefix: string, pieces: string[], mask = true): string {
  let parts = unique(pieces);
  for (let round = 0; round < 4 && mask; round++) {
    const next = unique(maskLine(`${prefix}${parts.join(" | ")}`).slice(prefix.length).split(" | ").map((part) => part.trim()));
    if (next.join(" | ") === parts.join(" | ")) break;
    parts = next;
  }
  return parts.length === 0 ? "" : `${prefix}${parts.join(" | ")}`;
}

const finish = (unit: Omit<Unit, "chunks">): Unit => ({ ...unit, chunks: unit.text === "" ? [] : chunkText(unit.text) });

/**
 * 표 하나의 글: 표 바로 앞의 글(제목 후보, 읽는 차례로) + 단위 줄 + 열 머리글 + 행 머리글. 숫자 칸은 싣지 않는다.
 * 머리글은 격자의 머리글 행·열(`grid.header`)의 글 그대로다. 머리글이 서지 않은 표는 그 줄이 비고 앞의 글만 남는다.
 */
export function tableUnits(extraction: Extraction, side: Side, mask = true): Unit[] {
  const cells = new Map<string, Fact[]>();
  const all = new Map<string, Fact[]>();
  for (const fact of extraction.facts) {
    if (fact.where.gridId === null) continue;
    all.set(fact.where.gridId, [...(all.get(fact.where.gridId) ?? []), fact]);
    if (fact.bucket === "table_cell") cells.set(fact.where.gridId, [...(cells.get(fact.where.gridId) ?? []), fact]);
  }
  return extraction.grids
    .filter((grid) => grid.kind === "pdf_table" && grid.page !== null)
    .map((grid) => {
      const m = (raw: string): string => maskLine(raw, mask);
      const headerRows = new Set(grid.header.rows);
      const headerCols = new Set(grid.header.cols);
      const cols = joinPieces("열: ", grid.header.rows.flatMap((row) => (grid.cells[row] ?? []).map(m)), mask);
      const rows = joinPieces("행: ", grid.cells.flatMap((row, r) => (headerRows.has(r) ? [] : row.filter((_, c) => headerCols.has(c)).map(m))), mask);
      const lines = [...[...grid.titleCandidates].reverse().map(m), ...grid.unitLines.map(m), cols, rows].filter((line) => line !== "");
      const numberCells = cells.get(grid.id) ?? [];
      const keyed = numberCells.filter((fact) => (fact.headers?.row ?? []).some((ref) => ref.text.trim() !== "") && (fact.headers?.col ?? []).some((ref) => ref.text.trim() !== "")).length;
      return finish({ id: `${side}:table:${grid.id}`, doc: side, kind: "table", page: grid.page as number, table: grid.id, places: (all.get(grid.id) ?? []).map((fact) => fact.id), text: lines.join("\n"), headerShare: numberCells.length === 0 ? null : keyed / numberCells.length });
    });
}

/**
 * 쪽 하나의 글: 읽는 차례의 글 블록과 표. 값 후보는 가린다. 표의 숫자 칸(가리면 기호만 남는 칸)은 싣지 않는다.
 * 격자를 세우지 못한 표는 싣지 못한다. 글자 층이 없는 쪽은 글이 비어 단위가 되지 않는다.
 */
export function pageUnits(document: DocModel, extraction: Extraction, side: Side, mask = true): Unit[] {
  const grids = new Map(extraction.grids.map((grid) => [grid.id, grid]));
  const facts = new Map(extraction.facts.map((fact) => [fact.id, fact]));
  const pages = [...new Set(document.blocks.map((block) => block.page))].sort((x, y) => x - y);
  const out: Unit[] = [];
  for (const page of pages) {
    const lines: string[] = [];
    for (const block of document.blocks.filter((item) => item.page === page).sort((x, y) => x.order - y.order)) {
      if (block.kind !== "table") {
        lines.push(maskLine(block.text ?? "", mask));
        continue;
      }
      const grid = grids.get(block.table_id as string) as Grid | undefined;
      if (!grid) continue;
      grid.cells.forEach((cellsOfRow, row) => {
        const parts = cellsOfRow.map((raw, col) => (facts.get(`${grid.id}:r${row}c${col}`)?.bucket === "table_cell" && mask ? "" : maskLine(cellText(grid, row, col, raw, facts, mask), mask))).filter((text) => text !== "" && text !== MASK);
        if (parts.length > 0) lines.push(mask ? maskLine(parts.join(" | ")) : parts.join(" | "));
      });
    }
    const text = lines.filter((line) => line !== "").join("\n");
    if (text === "") continue;
    out.push(finish({ id: `${side}:page:${page}`, doc: side, kind: "page", page, table: null, places: extraction.facts.filter((fact) => fact.where.page === page).map((fact) => fact.id), text, headerShare: null }));
  }
  return out;
}

/**
 * 도표 하나의 글: 제목 + 계열들 + 범주들. 도표는 라벨(`값 후보 ID → 제목 · 계열 · 범주`)에서 같은 쪽·같은 제목(첫 조각)인 것끼리 묶어 세운다.
 * 조각이 하나뿐인 라벨은 제목만 있다. 마지막 조각이 범주, 그 사이가 계열이다 (`focus.ts` 의 `labelParts` 와 같은 가름).
 */
export function chartUnits(extraction: Extraction, side: Side, labels: Record<string, string>, labelName: string, mask = true): Unit[] {
  const groups = new Map<string, { page: number; title: string; series: string[]; categories: string[]; places: string[] }>();
  for (const fact of extraction.facts) {
    const label = labels[fact.id];
    if (label === undefined || fact.where.page === null) continue;
    const parts = label.split(" · ").map((part) => part.trim()).filter((part) => part !== "");
    if (parts.length === 0) continue;
    const title = parts[0] as string;
    const key = `${fact.where.page}|${title}`;
    const group = groups.get(key) ?? { page: fact.where.page, title, series: [], categories: [], places: [] };
    if (parts.length >= 2) group.categories.push(parts[parts.length - 1] as string);
    group.series.push(...parts.slice(1, -1));
    group.places.push(fact.id);
    groups.set(key, group);
  }
  const perPage = new Map<number, number>();
  return [...groups.values()].sort((x, y) => x.page - y.page).map((group) => {
    const n = (perPage.get(group.page) ?? 0) + 1;
    perPage.set(group.page, n);
    const m = (raw: string): string => maskLine(raw.replace(/[〔〕[\]|]/g, " "), mask);
    const series = joinPieces("계열: ", group.series.map(m), mask);
    const categories = joinPieces("범주: ", group.categories.map(m), mask);
    const lines = [m(group.title), series, categories].filter((line) => line !== "");
    return finish({ id: `${side}:chart-${labelName}:p${group.page}#${n}`, doc: side, kind: `chart-${labelName}`, page: group.page, table: null, places: group.places, text: lines.join("\n"), headerShare: null });
  });
}

// ── 순위 ─────────────────────────────────────────────────────────────────────

export const BM25_K1 = 1.2;
export const BM25_B = 0.75;
export const RRF_K = 60;

export interface Ranked {
  id: string;
  score: number;
}

const HANGUL = /^[가-힣]+$/;
const singular = (word: string): string => {
  if (!/^[a-z]+$/.test(word) || word.length <= 3) return word;
  if (word.endsWith("ies")) return `${word.slice(0, -3)}y`;
  if (word.endsWith("sses")) return word.slice(0, -2);
  if (word.endsWith("s") && !word.endsWith("ss") && !word.endsWith("us") && !word.endsWith("is")) return word.slice(0, -1);
  return word;
};

/** 글을 낱말로 쪼갠다 (scope3 의 `tokenize` 와 같다: 글자·숫자가 아닌 데서 끊고, 두 글자 미만은 버리고, 한글 낱말은 두 글자 조각을 더한다). */
export function tokenize(text: string): string[] {
  const tokens: string[] = [];
  for (const raw of text.normalize("NFKC").toLowerCase().split(/[^\p{L}\p{N}]+/u)) {
    if (raw.length < 2) continue;
    tokens.push(singular(raw));
    if (HANGUL.test(raw)) for (let i = 0; i + 1 < raw.length; i += 1) tokens.push(`#${raw.slice(i, i + 2)}`);
  }
  return tokens;
}

type Doc = { id: string; text: string };
const byScore = (order: Map<string, number>) => (x: Ranked, y: Ranked): number => y.score - x.score || (order.get(x.id) as number) - (order.get(y.id) as number);

/** 낱말 단위 BM25. 질의는 단위의 글 전체다. 겹친 낱말이 없는 후보는 돌려주지 않는다. 동점은 후보의 차례. */
export function rankLexical(query: string, targets: readonly Doc[]): Ranked[] {
  const docs = targets.map((target) => {
    const counts = new Map<string, number>();
    const tokens = tokenize(target.text);
    for (const token of tokens) counts.set(token, (counts.get(token) ?? 0) + 1);
    return { counts, length: tokens.length };
  });
  const averageLength = docs.reduce((sum, doc) => sum + doc.length, 0) / Math.max(1, docs.length);
  const terms = [...new Set(tokenize(query))];
  const idf = new Map(terms.map((term) => {
    const df = docs.reduce((sum, doc) => sum + (doc.counts.has(term) ? 1 : 0), 0);
    return [term, Math.log(1 + (docs.length - df + 0.5) / (df + 0.5))] as const;
  }));
  const ranked: Ranked[] = [];
  targets.forEach((target, index) => {
    const doc = docs[index] as (typeof docs)[number];
    let score = 0;
    let matched = 0;
    for (const term of terms) {
      const tf = doc.counts.get(term);
      if (tf === undefined) continue;
      matched += 1;
      score += ((idf.get(term) as number) * tf * (BM25_K1 + 1)) / (tf + BM25_K1 * (1 - BM25_B + (BM25_B * doc.length) / averageLength));
    }
    if (matched > 0) ranked.push({ id: target.id, score });
  });
  return ranked.sort(byScore(new Map(targets.map((target, index) => [target.id, index]))));
}

export function cosine(a: ArrayLike<number>, b: ArrayLike<number>): number {
  let dot = 0;
  let normA = 0;
  let normB = 0;
  for (let i = 0; i < a.length; i += 1) {
    const x = a[i] as number;
    const y = b[i] as number;
    dot += x * y;
    normA += x * x;
    normB += y * y;
  }
  return normA === 0 || normB === 0 ? 0 : dot / Math.sqrt(normA * normB);
}

/** 코사인 유사도로 후보 전체를 줄 세운다. 조각이 여럿인 단위끼리는 조각 쌍의 가장 큰 값. 벡터가 없는 후보가 있으면 그 ID 를 돌려준다. */
export function rankEmbedding(query: readonly ArrayLike<number>[], targets: readonly { id: string }[], vectors: (id: string) => readonly ArrayLike<number>[] | undefined): Ranked[] | { missing: string } {
  const ranked: Ranked[] = [];
  for (const target of targets) {
    const mine = vectors(target.id);
    if (!mine || mine.length === 0) return { missing: target.id };
    let best = -Infinity;
    for (const q of query) for (const v of mine) best = Math.max(best, cosine(q, v));
    ranked.push({ id: target.id, score: best });
  }
  return ranked.sort(byScore(new Map(targets.map((target, index) => [target.id, index]))));
}

/** 순위 합산(RRF): 각 순위표에서 r 등에 1/(k+r) 을 주고 더한다. 한 순위표에 없는 후보는 그 표에서 0 이다. 동점은 후보의 차례. */
export function fuseRrf(rankings: readonly (readonly Ranked[])[], order: readonly string[], k: number = RRF_K): Ranked[] {
  const fused = new Map<string, number>();
  for (const ranking of rankings) ranking.forEach((entry, position) => fused.set(entry.id, (fused.get(entry.id) ?? 0) + 1 / (k + position + 1)));
  return [...fused].map(([id, score]) => ({ id, score })).sort(byScore(new Map(order.map((id, index) => [id, index]))));
}

/** 단위의 순위를 쪽의 순위로 합친다: 쪽은 그 쪽의 단위가 처음 나온 자리에 선다 (점수도 그 단위의 것). */
export function collapseToPages(ranked: readonly Ranked[], pageOf: (id: string) => number): { page: number; score: number }[] {
  const seen = new Set<number>();
  const out: { page: number; score: number }[] = [];
  for (const entry of ranked) {
    const page = pageOf(entry.id);
    if (seen.has(page)) continue;
    seen.add(page);
    out.push({ page, score: entry.score });
  }
  return out;
}

// ── 정답 (있는 판정 기록에서 규칙으로 끌어낸다) ──────────────────────────────

export interface Container {
  doc: Side;
  page: number;
  /** 자리가 표 안이면 그 표의 ID. */
  table: string | null;
}

export interface GoldLink {
  a: Container;
  b: Container;
  /** `cells`: 양쪽이 표의 숫자 칸. `prose`: 양쪽이 표 안이고 한쪽 이상이 글 칸 속 숫자. `chart`: 보고서 쪽이 도표의 값. */
  kind: "cells" | "prose" | "chart";
  /** 근거가 된 기록의 수 (파일 갈래 → 건수). */
  support: Record<string, number>;
  /** 그 짝의 자리들 (문서 이름 없는 값 후보 ID). 도표 단위를 잇는 데 쓴다. */
  placesA: string[];
  placesB: string[];
}

export interface GoldNone {
  doc: Side;
  page: number;
  table: string | null;
  /** `table`: 「같은 항목 아님」 판정만 있는 표. `chart-page`: 상대가 없다고 센 도표 쪽 (밴드 CH1). */
  kind: "table" | "chart-page";
  basis: string;
}

export interface NominateGold {
  links: GoldLink[];
  none: GoldNone[];
  /** 규칙이 쓰지 않은 기록의 수 (까닭 → 건수). */
  unused: Record<string, number>;
}

/** 판정 기록에서 읽은 한 건. `verdict` 는 `same`(같은 항목) · `different`(같은 항목 아님) · `other`(값이 아님 등, 쓰지 않는다). */
export interface JudgedPair {
  source: string;
  a: string;
  b: string;
  verdict: "same" | "different" | "other";
  /** 보고서 쪽이 도표의 값이라고 기록이 말하는 짝. */
  chart?: boolean;
}

const PLACE = /^p(\d+)-(?:(t\d+):r\d+c\d+|b\d+)(#n\d+)?$/;

/** 값 후보 ID 에서 쪽·표·숫자 칸 여부를 읽는다. 꼴이 아니면 null. */
export function parsePlace(id: string): { page: number; table: string | null; numberCell: boolean } | null {
  const match = PLACE.exec(id.replace(/^[ab]:/, ""));
  if (!match) return null;
  const table = match[2] === undefined ? null : `p${match[1]}-${match[2]}`;
  return { page: Number(match[1]), table, numberCell: table !== null && match[3] === undefined };
}

/**
 * 정답을 끌어내는 규칙.
 *  1. 「같은 항목」으로 판정된 짝 가운데 양쪽 자리가 다 표 안인 것 → 표 짝 (`cells` 또는 `prose`). 도표 표시가 있으면 `chart`.
 *  2. 도표 표시가 있는 「같은 항목」 짝은 상대 자리가 표 밖이어도 싣는다 (`chart`, 상대는 쪽으로만).
 *  3. 그 밖의 「같은 항목」 짝(문장 ↔ 문장, 문장 ↔ 표)은 쓰지 않는다 (표 짝이 아니다).
 *  4. 「같은 항목 아님」 판정에 나온 표 가운데 규칙 1·2 의 짝에 한 번도 들지 않은 표 → 상대 없음.
 *  5. 상대 없는 도표 쪽은 `chartPagesWithout` 로 받는다 (밴드 CH1 이 센 것).
 * 판정이 없는 표·도표는 정답에 없다 (채점의 분모에서 빠진다).
 */
export function deriveGold(pairs: readonly JudgedPair[], chartPagesWithout: readonly { doc: Side; page: number; basis: string }[]): NominateGold {
  const links = new Map<string, GoldLink>();
  const unused: Record<string, number> = {};
  const bump = (name: string): void => void (unused[name] = (unused[name] ?? 0) + 1);
  const negatives: { doc: Side; page: number; table: string; source: string }[] = [];
  for (const pair of pairs) {
    const a = parsePlace(pair.a);
    const b = parsePlace(pair.b);
    if (!a || !b) {
      bump("자리 ID 꼴이 아님");
      continue;
    }
    if (pair.verdict === "other") {
      bump("값이 아님 등의 판정");
      continue;
    }
    if (pair.verdict === "different") {
      if (a.table !== null) negatives.push({ doc: "a", page: a.page, table: a.table, source: pair.source });
      if (b.table !== null) negatives.push({ doc: "b", page: b.page, table: b.table, source: pair.source });
      continue;
    }
    const chart = pair.chart === true;
    if (!chart && (a.table === null || b.table === null)) {
      bump("같은 항목이지만 표 짝이 아님 (문장이 낌)");
      continue;
    }
    const kind = chart ? "chart" : a.numberCell && b.numberCell ? "cells" : "prose";
    const key = `${kind}|${a.page}|${a.table}|${b.page}|${b.table}`;
    const link = links.get(key) ?? { a: { doc: "a" as const, page: a.page, table: a.table }, b: { doc: "b" as const, page: b.page, table: b.table }, kind, support: {}, placesA: [], placesB: [] };
    link.support[pair.source] = (link.support[pair.source] ?? 0) + 1;
    const [idA, idB] = [pair.a.replace(/^a:/, ""), pair.b.replace(/^b:/, "")];
    if (!link.placesA.includes(idA)) link.placesA.push(idA);
    if (!link.placesB.includes(idB)) link.placesB.push(idB);
    links.set(key, link);
  }
  const list = [...links.values()].sort((x, y) => x.a.page - y.a.page || (x.a.table ?? "").localeCompare(y.a.table ?? "") || x.b.page - y.b.page || x.kind.localeCompare(y.kind));
  const linked = new Set(list.flatMap((link) => [`a|${link.a.table}`, `b|${link.b.table}`]));
  const none = new Map<string, GoldNone & { sources: Record<string, number> }>();
  for (const negative of negatives) {
    if (linked.has(`${negative.doc}|${negative.table}`)) continue;
    const key = `${negative.doc}|${negative.table}`;
    const entry = none.get(key) ?? { doc: negative.doc, page: negative.page, table: negative.table, kind: "table" as const, basis: "", sources: {} };
    entry.sources[negative.source] = (entry.sources[negative.source] ?? 0) + 1;
    none.set(key, entry);
  }
  const tables: GoldNone[] = [...none.values()].map(({ sources, ...entry }) => ({ ...entry, basis: `「같은 항목 아님」 판정만 있다 (${Object.entries(sources).map(([name, count]) => `${name} ${count}`).join(", ")}), 「같은 항목」 0` }));
  return { links: list, none: [...tables, ...chartPagesWithout.map((item) => ({ doc: item.doc, page: item.page, table: null, kind: "chart-page" as const, basis: item.basis }))], unused };
}

// ── 채점 ─────────────────────────────────────────────────────────────────────

export interface Query {
  unit: string;
  /** `right`: 옳은 상대가 있다. `none`: 상대 없음. */
  state: "right" | "none";
  /** 옳은 상대의 쪽 (상대 문서). */
  rightPages: number[];
  /** 옳은 상대 단위 (후보 단위 가운데). 후보가 쪽이면 쪽 단위의 ID. */
  rightUnits: string[];
}

/** 정답의 짝이 그 단위에 걸리는가. 도표 짝은 도표 쪽에서 도표 단위(자리로)와 쪽 단위에만 걸리고, 표 단위에는 걸리지 않는다. */
function linkTouches(link: GoldLink, unit: Unit): boolean {
  const mine = unit.doc === "a" ? link.a : link.b;
  const places = unit.doc === "a" ? link.placesA : link.placesB;
  if (mine.page !== unit.page) return false;
  if (unit.kind === "page") return true;
  if (unit.kind === "table") return !(link.kind === "chart" && unit.doc === "a") && mine.table !== null && mine.table === unit.table;
  return link.kind === "chart" && unit.doc === "a" && places.some((id) => unit.places.includes(id));
}

/**
 * 정답에서 한 방향(`source` → 상대 문서)의 질의 목록을 낸다. 질의는 `source` 문서의 단위 가운데 정답에 걸리는 것이다.
 * 「상대 없음」은 그 단위에 걸리는 옳은 짝이 하나도 없고 상대 없음 항목이 걸릴 때다 (쪽 단위는 그 쪽의 표·도표 가운데 하나라도 상대가 있으면 「옳은 상대가 있다」).
 * 방향을 바꾸면 질의도 옳은 상대도 바뀐다.
 */
export function queriesFor(gold: NominateGold, source: Side, units: readonly Unit[], targets: readonly Unit[]): Query[] {
  const out: Query[] = [];
  for (const unit of units.filter((item) => item.doc === source)) {
    const links = gold.links.filter((link) => linkTouches(link, unit));
    if (links.length > 0) {
      const rightUnits = targets.filter((target) => target.doc !== source && links.some((link) => linkTouches(link, target))).map((target) => target.id);
      const rightPages = [...new Set(links.map((link) => (source === "a" ? link.b.page : link.a.page)))].sort((x, y) => x - y);
      out.push({ unit: unit.id, state: "right", rightPages, rightUnits });
      continue;
    }
    const none = gold.none.some((item) => item.doc === unit.doc && item.page === unit.page && (unit.kind === "page" || (item.kind === "table" ? unit.kind === "table" && unit.table === item.table : unit.kind.startsWith("chart-"))));
    if (none) out.push({ unit: unit.id, state: "none", rightPages: [], rightUnits: [] });
  }
  return out;
}

export const KS = [1, 3, 5] as const;

export interface QueryScore {
  unit: string;
  state: "right" | "none";
  /** 쪽으로 합친 순위에서 옳은 쪽 가운데 가장 앞선 것의 순위 (1부터). 순위표에 없으면 null. */
  pageRank: number | null;
  /** 옳은 쪽 전부가 드는 가장 작은 k. 하나라도 순위표에 없으면 null. */
  pageRankAll: number | null;
  /** 단위 순위에서 옳은 단위 가운데 가장 앞선 것의 순위. 옳은 단위가 후보에 없으면 undefined (그 분모에서 뺀다). */
  unitRank?: number | null;
  /** 순위표 맨 위의 점수 (순위표가 비면 null). */
  topScore: number | null;
  /** 옳은 쪽 가운데 가장 앞선 것의 점수. */
  rightScore: number | null;
  candidates: number;
}

/** 질의 하나의 순위표를 채점한다. */
export function scoreQuery(query: Query, ranked: readonly Ranked[], pageOf: (id: string) => number): QueryScore {
  const pages = collapseToPages(ranked, pageOf);
  const ranks = query.rightPages.map((page) => pages.findIndex((entry) => entry.page === page)).map((index) => (index === -1 ? null : index + 1));
  const found = ranks.filter((rank): rank is number => rank !== null);
  const pageRank = found.length === 0 ? null : Math.min(...found);
  const unitRanks = query.rightUnits.map((id) => ranked.findIndex((entry) => entry.id === id)).filter((index) => index !== -1).map((index) => index + 1);
  return {
    unit: query.unit,
    state: query.state,
    pageRank,
    pageRankAll: query.state === "none" || ranks.some((rank) => rank === null) ? null : Math.max(...found),
    ...(query.rightUnits.length > 0 ? { unitRank: unitRanks.length === 0 ? null : Math.min(...unitRanks) } : {}),
    topScore: ranked[0]?.score ?? null,
    rightScore: pageRank === null ? null : ((pages[pageRank - 1] as { score: number }).score),
    candidates: ranked.length,
  };
}

export interface Recall {
  /** 옳은 상대가 있는 질의의 수 (분모). */
  queries: number;
  /** k 마다 옳은 쪽이 하나라도 k 등 안에 든 질의의 수. */
  atK: Record<number, number>;
  /** k 마다 옳은 쪽 전부가 k 등 안에 든 질의의 수. */
  allAtK: Record<number, number>;
  /** 옳은 쪽이 순위표에 아예 없는 질의의 수. */
  unranked: number;
  /** 옳은 쪽의 순위 (질의마다, 오름차순. 없는 것은 뺀다). */
  ranks: number[];
}

/** 재현율@k 와 순위 분포. `level` 이 `unit` 이면 옳은 단위가 후보에 있는 질의만 센다. */
export function recall(scores: readonly QueryScore[], level: "page" | "unit" = "page"): Recall {
  const mine = scores.filter((score) => score.state === "right" && (level === "page" || score.unitRank !== undefined));
  const rankOf = (score: QueryScore): number | null => (level === "page" ? score.pageRank : (score.unitRank as number | null));
  const out: Recall = { queries: mine.length, atK: {}, allAtK: {}, unranked: mine.filter((score) => rankOf(score) === null).length, ranks: mine.map(rankOf).filter((rank): rank is number => rank !== null).sort((x, y) => x - y) };
  for (const k of KS) {
    out.atK[k] = mine.filter((score) => (rankOf(score) ?? Infinity) <= k).length;
    out.allAtK[k] = mine.filter((score) => ((level === "page" ? score.pageRankAll : rankOf(score)) ?? Infinity) <= k).length;
  }
  return out;
}

/** 문턱이 서는지 보는 두 분포: 상대 없는 질의의 맨 위 점수와, 옳은 상대가 있는 질의의 옳은 상대 점수. 오름차순. */
export function scoreSpread(scores: readonly QueryScore[]): { none: number[]; right: number[]; rightTop: number[]; noneUnranked: number; rightUnranked: number } {
  const sorted = (items: (number | null)[]): number[] => items.filter((item): item is number => item !== null).sort((x, y) => x - y);
  const none = scores.filter((score) => score.state === "none");
  const right = scores.filter((score) => score.state === "right");
  return { none: sorted(none.map((score) => score.topScore)), right: sorted(right.map((score) => score.rightScore)), rightTop: sorted(right.map((score) => score.topScore)), noneUnranked: none.filter((score) => score.topScore === null).length, rightUnranked: right.filter((score) => score.rightScore === null).length };
}
