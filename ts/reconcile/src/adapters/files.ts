import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { CallRecord } from "llm";
import { z } from "zod";
import { goldSchema, type Gold } from "../eval/gold.js";
import { extractDocument, extractGrids, type DocModel, type Extraction, type ExtractOptions } from "../domain/facts.js";
import { UNSPECIFIED_BOUNDARY, type Catalog } from "../domain/label.js";
import { checkMemory, type MemoryEntry } from "../domain/memory.js";
import type { LabelSettings } from "../domain/pipeline.js";
import type { UnitTable } from "../domain/units.js";
import { readXlsx } from "./xlsx.js";

// 파일 읽기. 형식이 틀리면 빈 값으로 넘기지 않고 오류로 낸다.

const here = dirname(fileURLToPath(import.meta.url));
export const repoRoot = join(here, "..", "..", "..", "..");
export const promptRoot = join(here, "..", "..", "prompts");
export const dataDir = join(repoRoot, "data", "reconcile");

export class ReconcileFileError extends Error {
  override name = "ReconcileFileError";
}

/**
 * 명령줄에서 받은 경로를 찾는다. `pnpm -F reconcile exec` 는 패키지 디렉터리에서 실행되므로
 * 상대 경로는 현재 디렉터리, 워크스페이스(`ts/`), 저장소 루트 순서로 찾는다 (docpipe 와 같은 규칙).
 */
export function resolveInputPath(path: string): string {
  if (isAbsolute(path)) return path;
  for (const base of [process.cwd(), join(repoRoot, "ts"), repoRoot]) {
    const candidate = resolve(base, path);
    if (existsSync(candidate)) return candidate;
  }
  return resolve(process.cwd(), path);
}

/** 출력 경로. 상대 경로는 워크스페이스(`ts/`) 기준이다 (`../out/band-P/...` 가 저장소의 `out/` 을 가리킨다). */
export function resolveOutputPath(path: string): string {
  return isAbsolute(path) ? path : resolve(join(repoRoot, "ts"), path);
}

function readJson(path: string, label: string): unknown {
  if (!existsSync(path)) throw new ReconcileFileError(`${label} 파일이 없다: ${path}`);
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch (error) {
    throw new ReconcileFileError(`${path}: JSON 으로 읽을 수 없다: ${(error as Error).message}`);
  }
}

function parse<T>(schema: z.ZodType<T>, raw: unknown, path: string, label: string): T {
  const parsed = schema.safeParse(raw);
  if (!parsed.success) throw new ReconcileFileError(`${path}: ${label} 형식이 맞지 않는다\n${z.prettifyError(parsed.error)}`);
  return parsed.data;
}

// ── 문서 모델 (읽는 쪽 사본. 정본은 schemas/document.schema.json) ────────────

export const documentSchema = z.object({
  schema_version: z.literal("0.1"),
  source: z.object({ file_name: z.string(), sha256: z.string(), page_count: z.number().int() }),
  pages: z.array(z.object({ page: z.number().int(), height: z.number(), text_layer: z.enum(["present", "none"]) })),
  blocks: z.array(
    z.object({
      id: z.string(),
      kind: z.enum(["paragraph", "table", "heading"]),
      page: z.number().int(),
      bbox: z.array(z.number()).length(4),
      order: z.number().int(),
      text: z.string().optional(),
      table_id: z.string().optional(),
    }),
  ),
  tables: z.array(
    z.object({
      id: z.string(),
      page: z.number().int(),
      cells: z.array(z.array(z.string())),
      header: z.object({ rows: z.array(z.number().int()), cols: z.array(z.number().int()), basis: z.string() }),
      method: z.enum(["lines", "text", "none"]),
      status: z.enum(["ok", "low_confidence", "failed"]),
      footnote_marks: z.array(z.object({ row: z.number().int(), col: z.number().int(), mark: z.string() })),
    }),
  ),
});

export interface LoadedInput {
  extraction: Extraction;
  /** 문장 속 숫자를 다룰 때 쓰는 블록 글자. 엑셀이면 비어 있다. */
  blockText: Map<string, string>;
  format: "document_json" | "xlsx";
  path: string;
}

/** 입력 하나를 사실 후보로 읽는다. `.xlsx` 는 엑셀, 그 밖은 문서 모델 JSON. */
export function loadInput(path: string, docId: string, options: ExtractOptions = {}): LoadedInput {
  if (!existsSync(path)) throw new ReconcileFileError(`입력 파일이 없다: ${path}`);
  if (path.toLowerCase().endsWith(".xlsx")) {
    const sheet = readXlsx(path, docId);
    return { extraction: extractGrids(sheet.grids, docId, sheet.fileName), blockText: new Map(), format: "xlsx", path };
  }
  const document: DocModel = parse(documentSchema, readJson(path, "문서 JSON"), path, "문서 JSON");
  const blockText = new Map(document.blocks.filter((block) => block.text !== undefined).map((block) => [block.id, block.text as string]));
  return { extraction: extractDocument(document, docId, options), blockText, format: "document_json", path };
}

// ── 기준 목록 ───────────────────────────────────────────────────────────────

const catalogSchema = z.object({
  id: z.string().min(1),
  synthetic: z.boolean(),
  note: z.string(),
  /** synthetic 이 아니면 출처가 있어야 한다. */
  source: z.object({ name: z.string().min(1), url: z.string().url(), checkedOn: z.string().regex(/^\d{4}-\d{2}-\d{2}$/), terms: z.string().min(1) }).nullable(),
  boundaries: z.array(z.object({ key: z.string().min(1), label: z.string(), aliases: z.array(z.string()) })),
  indicators: z.array(z.object({ key: z.string().min(1), label: z.string().min(1), unitFamily: z.string().min(1), aliases: z.array(z.string()) })).min(1),
});

export const defaultCatalogPath = join(dataDir, "indicators.synthetic.json");

/** 기준 목록을 읽는다. 출처도 synthetic 표시도 없는 목록은 거부한다. */
export function loadCatalog(path = defaultCatalogPath): Catalog & { sha256: string; path: string } {
  const text = existsSync(path) ? readFileSync(path, "utf8") : null;
  const data = parse(catalogSchema, readJson(path, "지표 목록"), path, "지표 목록");
  if (!data.synthetic && data.source === null) throw new ReconcileFileError(`${path}: 출처 없는 지표 목록이다. source 를 적거나 synthetic 으로 표시한다`);
  if (data.synthetic && !/synthetic/.test(path)) throw new ReconcileFileError(`${path}: synthetic 목록은 파일 이름에 synthetic 이 있어야 한다`);
  if (!data.boundaries.some((boundary) => boundary.key === UNSPECIFIED_BOUNDARY)) throw new ReconcileFileError(`${path}: 범위 목록에 ${UNSPECIFIED_BOUNDARY} 가 없다`);
  const keys = data.indicators.map((indicator) => indicator.key);
  if (new Set(keys).size !== keys.length) throw new ReconcileFileError(`${path}: 지표 키가 겹친다`);
  return { id: data.id, synthetic: data.synthetic, indicators: data.indicators, boundaries: data.boundaries, sha256: createHash("sha256").update(text ?? "").digest("hex"), path };
}

// ── 단위 표 ─────────────────────────────────────────────────────────────────

const sourced = { sourceUrl: z.string().url().nullable(), checkedOn: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).nullable(), sourceNote: z.string() };

const unitsSchema = z.object({
  note: z.string(),
  scaleWords: z.array(z.object({ word: z.string().min(1), exp: z.number().int(), ...sourced })),
  units: z.array(z.object({ symbol: z.string().min(1), family: z.string().min(1), exp: z.number().int(), aliases: z.array(z.string()), compactScale: z.boolean(), ...sourced })),
});

export const defaultUnitsPath = join(dataDir, "units.json");

export function loadUnits(path = defaultUnitsPath): UnitTable {
  const data = parse(unitsSchema, readJson(path, "단위 표"), path, "단위 표");
  // 환산이 있는 줄(지수가 0 이 아닌 줄)은 출처 없이 싣지 않는다.
  for (const item of [...data.scaleWords.map((word) => ({ name: word.word, ...word })), ...data.units.map((unit) => ({ name: unit.symbol, ...unit }))]) {
    if (item.exp !== 0 && item.sourceUrl === null) throw new ReconcileFileError(`${path}: ${item.name} 의 환산에 출처가 없다`);
  }
  return { scaleWords: Object.fromEntries(data.scaleWords.map((item) => [item.word, item.exp])), units: data.units.map(({ symbol, family, exp, aliases, compactScale }) => ({ symbol, family, exp, aliases, compactScale })) };
}

// ── 설정 ────────────────────────────────────────────────────────────────────

const settingsSchema = z.object({
  note: z.string(),
  label: z.object({
    maxTargetsPerCall: z.number().int().positive(),
    outputTokensPerLabel: z.number().int().positive(),
    outputTokensOverhead: z.number().int().nonnegative(),
    outputCapFactor: z.number().int().positive(),
    minConfidence: z.enum(["low", "medium", "high"]),
    concurrency: z.number().int().positive(),
    basis: z.string(),
  }),
  dryRunRoutes: z.array(z.string()).min(1),
});

export const defaultSettingsPath = join(dataDir, "settings.json");

export function loadSettings(path = defaultSettingsPath): { label: LabelSettings; dryRunRoutes: string[] } {
  const data = parse(settingsSchema, readJson(path, "설정"), path, "설정");
  const { basis: _basis, ...label } = data.label;
  return { label, dryRunRoutes: data.dryRunRoutes };
}

// ── 확정 기억 ───────────────────────────────────────────────────────────────

const memorySchema = z.object({
  synthetic: z.boolean(),
  note: z.string(),
  entries: z.array(
    z.object({
      docKind: z.string().min(1),
      rowPath: z.array(z.string()),
      colPath: z.array(z.string()),
      indicatorKey: z.string().min(1),
      unit: z.string().nullable(),
      boundary: z.string().min(1),
      basisNote: z.string().nullable(),
      period: z.union([z.object({ from: z.literal("header_year") }), z.object({ fixed: z.string() })]),
      confirmedBy: z.string().min(1),
      confirmedAt: z.string().min(1),
    }),
  ),
});

/** 확정 기억 파일을 읽는다. 기준 목록에 없는 지표·범위를 가리키면 오류다. */
export function loadMemory(path: string, catalog: Catalog): MemoryEntry[] {
  const data = parse(memorySchema, readJson(path, "확정 기억"), path, "확정 기억");
  if (data.synthetic && !/synthetic/.test(path)) throw new ReconcileFileError(`${path}: synthetic 기억은 파일 이름에 synthetic 이 있어야 한다`);
  const problems = checkMemory(data.entries, catalog);
  if (problems.length > 0) throw new ReconcileFileError(`${path}: 확정 기억이 지표 목록과 맞지 않는다\n${problems.join("\n")}`);
  return data.entries;
}

// ── 정답 파일, 호출 기록 ────────────────────────────────────────────────────

/** 정답 파일을 읽는다. synthetic 이 아니라 사람이 실제 문서에 단 것이라, 누가 달았고 검토를 받았는지(`status`)가 있어야 한다. */
export function loadGold(path: string): Gold & { sha256: string; path: string } {
  const text = existsSync(path) ? readFileSync(path, "utf8") : "";
  const data = parse(goldSchema, readJson(path, "정답"), path, "정답");
  return { ...data, sha256: createHash("sha256").update(text).digest("hex"), path };
}

/** 호출 기록(JSONL)을 읽는다. 없으면 빈 목록. */
export function readCallLog(path: string): CallRecord[] {
  if (!existsSync(path)) return [];
  return readFileSync(path, "utf8")
    .split("\n")
    .filter((line) => line.trim() !== "")
    .map((line, index) => {
      try {
        return JSON.parse(line) as CallRecord;
      } catch {
        throw new ReconcileFileError(`${path}:${index + 1}: 호출 기록을 JSON 으로 읽을 수 없다`);
      }
    });
}

export { readJson as readJsonFile };
