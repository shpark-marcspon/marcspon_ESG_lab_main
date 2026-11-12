import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { BudgetGuard, HeuristicTokenEstimator, LlmClient, MemorySink, MockProvider, defaultConfigPaths, loadConfig, type BudgetLimit, type ProviderRequest } from "llm";
import { loadGlossary, loadSettings, parseDocument, prompts, type ParsedDocument } from "../src/index.js";
import { itemsOf } from "../src/mock-synthetic.js";

export const TAGS = { tenant: "tenant-synthetic", module: "docpipe" };

export const config = loadConfig(defaultConfigPaths({ synthetic: true }));
export const estimator = new HeuristicTokenEstimator(config.tokenEstimate);
export const estimate = (text: string): number => estimator.estimate(text);
export const settings = loadSettings();
export const glossary = loadGlossary();

export function tempDir(): string {
  return mkdtempSync(join(tmpdir(), "docpipe-"));
}

export function harness(options: { limits?: BudgetLimit[] } = {}) {
  const mock = new MockProvider();
  const log = new MemorySink();
  const client = new LlmClient({
    config,
    providers: { mock, "mock-b": new MockProvider("mock-b") },
    prompts,
    log,
    budget: new BudgetGuard(options.limits ?? []),
  });
  return { client, mock, log };
}

export function itemsIn(request: ProviderRequest) {
  return itemsOf(request);
}

const FURNITURE = "가상 주식회사 지속가능경영보고서 (synthetic)";

export interface FixtureOptions {
  pages?: number;
  /** 섹션 근거. 기본은 밴드 A 가 실제로 내는 추정 섹션이다. */
  basis?: "pdf_outline" | "page_top_largest_font";
  paragraphsPerPage?: number;
}

/**
 * synthetic 문서. 밴드 A 의 출력 모양(쪽마다 되풀이되는 머리말, heading 으로 잡힌 쪽 번호, 숫자 표, 각주 위첨자,
 * low_confidence 표, failed 표)을 흉내 낸 가상 값이고 어느 보고서의 수치도 아니다.
 */
export function fixtureDocument(options: FixtureOptions = {}): ParsedDocument {
  const pages = options.pages ?? 6;
  const basis = options.basis ?? "page_top_largest_font";
  const paragraphsPerPage = options.paragraphsPerPage ?? 3;
  const blocks: unknown[] = [];
  const tables: unknown[] = [];
  const sections: unknown[] = [];
  const bbox = [0, 0, 10, 10];

  for (let page = 1; page <= pages; page++) {
    const sectionId = `s${Math.ceil(page / 2)}`;
    if (page % 2 === 1) sections.push({ id: sectionId, title: page === 1 ? "기후변화 대응" : `가상 섹션 ${sectionId}`, level: 1, page_start: page, page_end: Math.min(page + 1, pages), basis });
    let order = 0;
    const push = (kind: string, extra: Record<string, unknown>): void => {
      order += 1;
      blocks.push({ id: `p${page}-b${order}`, kind, page, bbox, order, section_id: sectionId, ...extra });
    };
    push("heading", { text: ["추진방향", "활동", "리스크 관리"][page % 3] });
    for (let i = 0; i < paragraphsPerPage; i++) {
      push("paragraph", { text: `${page}쪽 ${i + 1}번째 문단입니다. 가상 회사는 온실가스 배출량을 줄이고 재생에너지 사용을 늘려 탄소중립을 추진합니다. 협력회사와 함께 사업장 ${page * 10 + i}곳을 점검했습니다.` });
    }
    const tableId = `p${page}-t1`;
    tables.push({
      id: tableId,
      page,
      bbox,
      n_rows: 3,
      n_cols: 4,
      cells: [
        ["에너지 관리", "", "2024년", "2025년"],
        ["사업장 에너지 사용량", "GWh", `${page},234`, `${page},567.8`],
        ["재생에너지 전환율", "%", "31.0", "-"],
      ],
      header: { rows: [0], cols: [0, 1], basis: "rows_before_first_numeric_row" },
      method: "lines",
      status: "ok",
      notes: [],
      footnote_marks: page === 1 ? [{ row: 1, col: 3, mark: "1)" }] : [],
    });
    push("table", { table_id: tableId });
    if (page <= 2) push("paragraph", { text: "Scope 1, 2" });
    push("paragraph", { text: FURNITURE });
    push("heading", { text: String(page) });
  }

  // 목차가 표로 잡힌 경우(low_confidence)와 격자를 못 세운 도표(failed).
  tables.push({ id: "p1-t2", page: 1, bbox, n_rows: 2, n_cols: 2, cells: [["회사소개", "05"], ["환경성과", "69"]], header: { rows: [], cols: [], basis: "no_numeric_row" }, method: "text", status: "low_confidence", notes: ["side_by_side_split"], footnote_marks: [] });
  blocks.push({ id: "p1-b90", kind: "table", page: 1, bbox, order: 90, section_id: "s1", table_id: "p1-t2" });
  tables.push({ id: "p1-t3", page: 1, bbox, n_rows: 0, n_cols: 0, cells: [], header: { rows: [], cols: [], basis: "empty" }, method: "none", status: "failed", notes: [], footnote_marks: [] });
  blocks.push({ id: "p1-b91", kind: "table", page: 1, bbox, order: 91, section_id: "s1", table_id: "p1-t3" });

  return parseDocument({
    schema_version: "0.1",
    source: { file_name: "synthetic_report.pdf", sha256: "0".repeat(64), page_count: pages },
    parser: { name: "docparse", version: "0.1.0", table_strategy: "auto", llm_used: false, ocr_used: false },
    pages: Array.from({ length: pages }, (_, index) => ({ page: index + 1, width: 595, height: 842, text_layer: "present", char_count: 100, image_coverage: 0, columns: 1, notes: [] })),
    sections,
    blocks,
    tables,
  });
}

/** 섹션 제목과 블록만 정해 주는 작은 synthetic 문서. */
export function parseDocumentWith(rows: { section: string; kind: "paragraph" | "heading"; text: string }[]): ParsedDocument {
  const titles = [...new Set(rows.map((row) => row.section))];
  return parseDocument({
    schema_version: "0.1",
    source: { file_name: "synthetic_small.pdf", sha256: "1".repeat(64), page_count: titles.length },
    parser: { name: "docparse", version: "0.1.0", table_strategy: "auto", llm_used: false, ocr_used: false },
    pages: titles.map((_, index) => ({ page: index + 1, width: 595, height: 842, text_layer: "present", char_count: 100, image_coverage: 0, columns: 1, notes: [] })),
    sections: titles.map((title, index) => ({ id: `s${index + 1}`, title, level: 1, page_start: index + 1, page_end: index + 1, basis: "page_top_largest_font" })),
    blocks: rows.map((row, index) => ({ id: `b${index + 1}`, kind: row.kind, page: titles.indexOf(row.section) + 1, bbox: [0, 0, 1, 1], order: index + 1, section_id: `s${titles.indexOf(row.section) + 1}`, text: row.text })),
    tables: [],
  });
}
