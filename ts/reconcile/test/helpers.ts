import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { strToU8, zipSync } from "fflate";
import { FilePromptStore, HeuristicTokenEstimator, LlmClient, MemorySink, MockProvider, defaultConfigPaths, loadConfig, type ProviderRequest, type ProviderResponse } from "llm";
import {
  estimateHeader,
  extractDocument,
  headerKey,
  loadCatalog,
  loadSettings,
  loadUnits,
  promptRoot,
  type DocModel,
  type Extraction,
  type Fact,
  type RawLabel,
  type RunOptions,
} from "../src/index.js";

// 테스트의 문서와 숫자는 전부 지어낸 것이다 (synthetic). 어느 회사의 실제 값도 아니다.

export const config = loadConfig(defaultConfigPaths({ synthetic: true }));
export const estimator = new HeuristicTokenEstimator(config.tokenEstimate);
export const catalog = loadCatalog();
export const units = loadUnits();
export const settings = loadSettings();
export const TAGS = { tenant: "tenant-synthetic", module: "reconcile" };

export function tempDir(): string {
  return mkdtempSync(join(tmpdir(), "reconcile-"));
}

export interface TableSpec {
  id: string;
  page?: number;
  cells: string[][];
  status?: "ok" | "low_confidence" | "failed";
  header?: { rows: number[]; cols: number[]; basis: string };
  footnotes?: { row: number; col: number; mark: string }[];
  /** 표 앞에 오는 글 블록 (제목, 단위 줄). */
  before?: string[];
}

export interface BlockSpec {
  id: string;
  page?: number;
  text: string;
  bbox?: number[];
  kind?: "paragraph" | "heading";
}

/** 가상(synthetic) 문서 모델. */
export function syntheticDocument(name: string, tables: TableSpec[], blocks: BlockSpec[] = [], pages = 2): DocModel {
  const all: DocModel["blocks"] = [];
  let order = 1;
  for (const block of blocks) all.push({ id: block.id, kind: block.kind ?? "paragraph", page: block.page ?? 1, bbox: block.bbox ?? [50, 300, 500, 320], order: order++, text: block.text });
  for (const table of tables) {
    (table.before ?? []).forEach((text, index) => all.push({ id: `${table.id}-before${index}`, kind: "paragraph", page: table.page ?? 1, bbox: [50, 200, 500, 220], order: order++, text }));
    all.push({ id: `${table.id}-block`, kind: "table", page: table.page ?? 1, bbox: [50, 400, 500, 600], order: order++, table_id: table.id });
  }
  return {
    source: { file_name: `${name}.synthetic.pdf`, sha256: "0".repeat(64), page_count: pages },
    pages: Array.from({ length: pages }, (_, index) => ({ page: index + 1, height: 800, text_layer: "present" as const })),
    blocks: all,
    tables: tables.map((table) => ({
      id: table.id,
      page: table.page ?? 1,
      cells: table.status === "failed" ? [] : table.cells,
      header: table.header ?? estimateHeader(table.cells),
      method: table.status === "failed" ? ("none" as const) : ("lines" as const),
      status: table.status ?? "ok",
      footnote_marks: table.footnotes ?? [],
    })),
  };
}

/** 정본 스키마가 요구하는 나머지 필드를 채워 파일로 쓸 수 있는 꼴로 만든다 (CLI 테스트용). */
export function asDocumentJson(document: DocModel): unknown {
  return {
    schema_version: "0.1",
    source: document.source,
    parser: { name: "docparse", version: "synthetic", table_strategy: "auto", llm_used: false, ocr_used: false },
    pages: document.pages.map((page) => ({ ...page, width: 600, char_count: 0, image_coverage: 0, columns: 1, notes: [] })),
    sections: [],
    blocks: document.blocks.map((block) => ({ ...block, section_id: null })),
    tables: document.tables.map((table) => ({ ...table, bbox: [0, 0, 1, 1], n_rows: table.cells.length, n_cols: table.cells[0]?.length ?? 0, notes: [] })),
  };
}

export const extract = (document: DocModel, docId: string): Extraction => extractDocument(document, docId);

export function harness() {
  const mock = new MockProvider();
  const log = new MemorySink();
  const client = new LlmClient({ config, providers: { mock }, prompts: new FilePromptStore(promptRoot), log });
  return { client, mock, log };
}

export function runOptions(a: Extraction, b: Extraction, client: LlmClient, overrides: Partial<RunOptions> = {}): RunOptions {
  return {
    a: { extraction: a, docKind: "source", defaultPeriod: null },
    b: { extraction: b, docKind: "report", defaultPeriod: null },
    catalog,
    units,
    memory: [],
    settings: settings.label,
    includeSentences: false,
    gateway: client,
    route: "mock",
    tags: TAGS,
    modelMaxOutputTokens: 4000,
    ...overrides,
  };
}

interface Payload {
  kind: "table" | "blocks";
  doc: string;
  table_id: string;
  targets: string[];
}

export function payloadOf(request: ProviderRequest): Payload {
  const text = request.user.map((part) => (part.type === "text" ? part.text : "")).join("\n");
  return JSON.parse(text.slice(text.indexOf("\n{") + 1)) as Payload;
}

export const completed = (labels: unknown[]): ProviderResponse => ({ kind: "completed", text: JSON.stringify({ labels }), usage: { inputTokens: 10, outputTokens: 10, cacheReadTokens: 0, cacheWriteTokens: 0 } });

export function rawLabel(target: string, patch: Partial<RawLabel> = {}): RawLabel {
  return { target, quote: null, occurrence: null, kind: "indicator", indicator_key: "ghg.scope1", free_text: null, period: null, unit: null, boundary: "unspecified", basis_note: null, confidence: "high", evidence: [], ...patch };
}

export interface Meaning {
  key: string;
  boundary?: string;
  basis?: string;
}

/**
 * 뜻을 「정해 준 대로」 붙이는 응답기 (테스트용). 행의 첫 머리글 글자 → 뜻의 표를 받는다.
 * 표가 옳으면 뜻을 정확히 붙이는 응답기이고, 표를 일부러 틀리게 주면 뜻을 틀리게 붙이는 응답기다.
 * 기간은 연도 머리글에서, 단위는 `단위` 열에서 읽는다. 표에 없는 행은 「목록에 없음」이라고 답한다.
 */
export function oracle(extractions: Extraction[], meanings: Record<string, Record<string, Meaning>>): (request: ProviderRequest) => ProviderResponse {
  const facts = new Map<string, Fact>();
  for (const extraction of extractions) for (const fact of extraction.facts) facts.set(`${extraction.docId}/${fact.id}`, fact);
  return (request) => {
    const payload = payloadOf(request);
    const labels = payload.targets.map((target) => {
      const fact = facts.get(`${payload.doc}/${payload.table_id}:${target}`) as Fact;
      const rowLabel = headerKey(fact.headers?.row[0]?.text ?? "");
      const meaning = meanings[payload.doc]?.[rowLabel];
      const evidence = [...(fact.headers?.row ?? []), ...(fact.headers?.col ?? [])].map((ref) => ref.cell);
      if (!meaning) return rawLabel(target, { kind: "not_in_catalog", indicator_key: null, free_text: rowLabel, confidence: "high", evidence });
      const year = fact.headers?.col.map((ref) => ref.text).find((text) => /^(19|20)\d{2}$/.test(text)) ?? null;
      const unit = fact.headers?.row[1]?.text ?? null;
      return rawLabel(target, { indicator_key: meaning.key, period: year, unit, boundary: meaning.boundary ?? "unspecified", basis_note: meaning.basis ?? null, evidence });
    });
    return completed(labels);
  };
}

// ── 가상 엑셀 ───────────────────────────────────────────────────────────────

export interface XlsxCell {
  ref: string;
  /** 저장 값의 글자 (`<v>`). */
  v?: string;
  /** 인라인 글자. */
  text?: string;
  /** 공유 문자열로 쓸 글자. */
  shared?: string;
  /** styles 의 cellXfs 번호. */
  s?: number;
  formula?: string;
}

const xmlEscape = (text: string): string => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/**
 * 가상(synthetic) xlsx 를 XML 글자로 직접 짓는다. 엑셀이 저장한 파일이 아니다.
 * `formats` 는 cellXfs 순서대로의 서식: 숫자면 내장 서식 번호, 글자면 사용자 서식 코드.
 */
export function buildXlsx(sheets: { name: string; cells: XlsxCell[] }[], formats: (number | string)[] = [0]): Uint8Array {
  const sharedStrings: string[] = [];
  const custom = formats.filter((format): format is string => typeof format === "string");
  const formatId = (format: number | string): number => (typeof format === "number" ? format : 164 + custom.indexOf(format));
  const files: Record<string, Uint8Array> = {};
  const head = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>';
  sheets.forEach((sheet, index) => {
    const rows = new Map<number, string[]>();
    for (const cell of sheet.cells) {
      const row = Number(/\d+$/.exec(cell.ref)?.[0]);
      const style = cell.s === undefined ? "" : ` s="${cell.s}"`;
      let xml: string;
      if (cell.shared !== undefined) {
        sharedStrings.push(cell.shared);
        xml = `<c r="${cell.ref}" t="s"${style}><v>${sharedStrings.length - 1}</v></c>`;
      } else if (cell.text !== undefined) xml = `<c r="${cell.ref}" t="inlineStr"${style}><is><t>${xmlEscape(cell.text)}</t></is></c>`;
      else xml = `<c r="${cell.ref}"${style}>${cell.formula === undefined ? "" : `<f>${xmlEscape(cell.formula)}</f>`}${cell.v === undefined ? "" : `<v>${cell.v}</v>`}</c>`;
      rows.set(row, [...(rows.get(row) ?? []), xml]);
    }
    const body = [...rows.entries()].sort((x, y) => x[0] - y[0]).map(([row, cells]) => `<row r="${row}">${cells.join("")}</row>`).join("");
    files[`xl/worksheets/sheet${index + 1}.xml`] = strToU8(`${head}<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>${body}</sheetData></worksheet>`);
  });
  files["xl/workbook.xml"] = strToU8(
    `${head}<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>${sheets.map((sheet, index) => `<sheet name="${xmlEscape(sheet.name)}" sheetId="${index + 1}" r:id="rId${index + 1}"/>`).join("")}</sheets></workbook>`,
  );
  files["xl/_rels/workbook.xml.rels"] = strToU8(
    `${head}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${sheets.map((_, index) => `<Relationship Id="rId${index + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${index + 1}.xml"/>`).join("")}</Relationships>`,
  );
  files["xl/styles.xml"] = strToU8(
    `${head}<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="${custom.length}">${custom.map((code, index) => `<numFmt numFmtId="${164 + index}" formatCode="${xmlEscape(code).replace(/"/g, "&quot;")}"/>`).join("")}</numFmts><cellXfs count="${formats.length}">${formats.map((format) => `<xf numFmtId="${formatId(format)}"/>`).join("")}</cellXfs></styleSheet>`,
  );
  files["xl/sharedStrings.xml"] = strToU8(`${head}<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">${sharedStrings.map((text) => `<si><t>${xmlEscape(text)}</t></si>`).join("")}</sst>`);
  files["[Content_Types].xml"] = strToU8(`${head}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>`);
  return zipSync(files);
}
