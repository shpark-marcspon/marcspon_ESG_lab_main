import type { Fact } from "./facts.js";
import { UNSPECIFIED_BOUNDARY, type Catalog, type Label } from "./label.js";

// 확정 기억. 사람이 확정한 「(문서 종류, 머리글 경로) → 지표」 를 받아, 맞는 셀에는 AI 를 부르지 않고 뜻을 붙인다.
// 이번에는 파일로 받아 적용하는 데까지다. 확정하는 화면은 없다.

export interface MemoryEntry {
  /** 문서 종류. 실행할 때 문서마다 주는 이름과 글자가 같아야 한다 (예: `databook`). */
  docKind: string;
  /** 행 머리글 경로 (왼쪽 열부터). 각주 번호와 공백은 빼고 견준다. */
  rowPath: string[];
  /** 열 머리글 경로 (위 행부터). 연도 머리글은 뺀다. */
  colPath: string[];
  indicatorKey: string;
  unit: string | null;
  boundary: string;
  basisNote: string | null;
  /** 기간을 어디서 읽는가. `header_year` 는 그 셀의 머리글에 연도가 정확히 하나 있을 때 그것을 쓴다. */
  period: { from: "header_year" } | { fixed: string };
  confirmedBy: string;
  confirmedAt: string;
}

const YEAR_HEADER = /^(?:FY)?['’]?((?:19|20)\d{2})(?:년|년도)?$/;

/**
 * 머리글 글자를 견줄 꼴로: NFKC, 공백 제거, 끝의 각주 번호(`1)`) 제거.
 * 각주 번호는 한 자리이고 짝 없는 닫는 괄호일 때만 뗀다 (`배출량1)` 은 떼고 `(Scope 1)` 은 그대로 둔다).
 */
export function headerKey(text: string): string {
  let key = text.normalize("NFKC").replace(/\s+/g, "").replace(/\*+$/, "");
  while (/\d\)$/.test(key) && (key.match(/\(/g) ?? []).length < (key.match(/\)/g) ?? []).length) key = key.slice(0, -2);
  return key;
}

export function headerPaths(fact: Fact): { rowPath: string[]; colPath: string[]; years: string[] } | null {
  if (!fact.headers) return null;
  const years = new Set<string>();
  const strip = (texts: string[]): string[] => {
    const kept: string[] = [];
    for (const text of texts) {
      const key = headerKey(text);
      const year = YEAR_HEADER.exec(key)?.[1];
      if (year) years.add(year);
      else if (key !== "" && kept[kept.length - 1] !== key) kept.push(key);
    }
    return kept;
  };
  const rowPath = strip(fact.headers.row.map((ref) => ref.text));
  const colPath = strip(fact.headers.col.map((ref) => ref.text));
  return { rowPath, colPath, years: [...years] };
}

const samePath = (a: string[], b: string[]): boolean => a.length === b.length && a.every((text, index) => text === headerKey(b[index] as string));

export interface MemoryApplication {
  labels: Label[];
  /** 기억에 맞는 항목은 있었지만 기간을 정하지 못해 붙이지 못한 사실 후보. AI 로 넘어간다. */
  periodUndetermined: string[];
}

/** 기억에 맞는 사실 후보에 뜻을 붙인다. 목록에 없는 지표·범위를 가리키는 항목은 읽을 때 걸러진다 (`checkMemory`). */
export function applyMemory(facts: Fact[], docKind: string, entries: MemoryEntry[]): MemoryApplication {
  const labels: Label[] = [];
  const periodUndetermined: string[] = [];
  const mine = entries.filter((entry) => entry.docKind === docKind);
  if (mine.length === 0) return { labels, periodUndetermined };
  for (const fact of facts) {
    const paths = headerPaths(fact);
    if (!paths) continue;
    const hits = mine.filter((entry) => samePath(paths.rowPath, entry.rowPath) && samePath(paths.colPath, entry.colPath));
    // 같은 머리글 경로에 확정이 둘이면 어느 쪽인지 정할 수 없다. 붙이지 않는다.
    if (hits.length !== 1) continue;
    const entry = hits[0] as MemoryEntry;
    const period = "fixed" in entry.period ? entry.period.fixed : paths.years.length === 1 ? (paths.years[0] as string) : null;
    if (period === null) {
      periodUndetermined.push(fact.id);
      continue;
    }
    labels.push({
      factId: fact.id,
      kind: "indicator",
      indicatorKey: entry.indicatorKey,
      freeText: null,
      period,
      unit: entry.unit,
      boundary: entry.boundary,
      basisNote: entry.basisNote,
      confidence: "high",
      evidence: [...(fact.headers?.row ?? []), ...(fact.headers?.col ?? [])].map((ref) => ref.cell),
      source: "memory",
    });
  }
  return { labels, periodUndetermined };
}

/** 기억 파일의 항목이 기준 목록과 맞는지 본다. 틀린 항목은 조용히 건너뛰지 않고 오류로 낸다. */
export function checkMemory(entries: MemoryEntry[], catalog: Catalog): string[] {
  const indicators = new Set(catalog.indicators.map((indicator) => indicator.key));
  const boundaries = new Set([UNSPECIFIED_BOUNDARY, ...catalog.boundaries.map((boundary) => boundary.key)]);
  const problems: string[] = [];
  entries.forEach((entry, index) => {
    if (!indicators.has(entry.indicatorKey)) problems.push(`항목 ${index}: 목록에 없는 지표 ${entry.indicatorKey}`);
    if (!boundaries.has(entry.boundary)) problems.push(`항목 ${index}: 목록에 없는 범위 ${entry.boundary}`);
    if ("fixed" in entry.period && !/^(19|20)\d{2}$/.test(entry.period.fixed)) problems.push(`항목 ${index}: 기간은 네 자리 연도여야 한다`);
  });
  return problems;
}
