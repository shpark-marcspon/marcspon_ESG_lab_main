import { z } from "zod";
import { cellId, type Extraction, type Fact, type Grid } from "./facts.js";
import { locateQuote, normalizeText } from "./notation.js";
import { normalizeUnitText } from "./units.js";

// 뜻 붙이기. AI 는 셀·블록 ID 를 가리켜 「이 숫자는 무슨 지표의 어느 기간 값이다」만 답한다.
// 숫자 값은 주고받지 않는다: 요청에는 원문 글자가 보이지만 답의 스키마에는 값을 적을 자리가 없다.
// AI 의 답은 `validateAnswers` 를 통과한 것만 쓰이고, 통과하지 못한 것과 답이 없는 것은 미확인으로 센다.

// ── 기준 목록 (갈아 끼우는 자리) ─────────────────────────────────────────────

export interface Indicator {
  key: string;
  label: string;
  /** 이 지표의 단위 계열 (`units.json` 의 family). 다른 계열의 단위가 붙으면 미확인이다. */
  unitFamily: string;
  /** 머리글에 쓰이는 다른 이름. 프롬프트에 싣고, 모의 응답기가 낱말 대조에 쓴다. */
  aliases: string[];
}

export interface Boundary {
  key: string;
  label: string;
  aliases: string[];
}

export interface Catalog {
  id: string;
  synthetic: boolean;
  indicators: Indicator[];
  /** 범위(조직 경계)의 닫힌 목록. `unspecified` 는 반드시 있다. */
  boundaries: Boundary[];
}

export const UNSPECIFIED_BOUNDARY = "unspecified";

// ── AI 의 답 ────────────────────────────────────────────────────────────────

/**
 * 답 하나. 숫자 값을 적는 필드가 없다.
 * `occurrence` 는 값이 아니라 「그 글자가 블록에서 몇 번째로 나온 것인가」의 순번이다 (문장 속 숫자를 가리킬 때만 쓴다).
 */
export const labelAnswerSchema = z.object({
  labels: z.array(
    z.object({
      /** 표: 셀 ID (`r3c4`). 문장: 블록 ID. */
      target: z.string(),
      /** 문장 속 숫자를 가리킬 때: 그 숫자의 원문 글자 그대로. 표 셀이면 null. */
      quote: z.string().nullable(),
      occurrence: z.number().int().nullable(),
      kind: z.enum(["indicator", "not_data", "not_in_catalog"]),
      indicator_key: z.string().nullable(),
      /** 목록에 없을 때의 자유 기술. 짝짓기에는 쓰지 않는다. */
      free_text: z.string().nullable(),
      /** 네 자리 연도. 원문에 그 글자가 있어야 한다. */
      period: z.string().nullable(),
      /** 단위 글자. 원문에 있는 글자 그대로. */
      unit: z.string().nullable(),
      boundary: z.string().nullable(),
      basis_note: z.string().nullable(),
      confidence: z.enum(["high", "medium", "low"]),
      /** 근거로 본 머리글의 셀 ID (문장이면 블록 ID). */
      evidence: z.array(z.string()),
    }),
  ),
});

export type LabelAnswer = z.infer<typeof labelAnswerSchema>;
export type RawLabel = LabelAnswer["labels"][number];

/** 검증을 통과해 사실 후보에 붙은 뜻. */
export interface Label {
  factId: string;
  kind: "indicator" | "not_data" | "not_in_catalog";
  indicatorKey: string | null;
  freeText: string | null;
  period: string | null;
  unit: string | null;
  boundary: string;
  basisNote: string | null;
  confidence: "high" | "medium" | "low";
  evidence: string[];
  source: "ai" | "memory";
}

// ── 요청 단위 ───────────────────────────────────────────────────────────────

/** 모델 호출 한 번의 몫. 표 하나(큰 표는 행 묶음) 또는 한 쪽의 글 블록 묶음. */
export interface LabelUnit {
  id: string;
  docId: string;
  kind: "table" | "blocks";
  gridId: string | null;
  /** 답해야 할 사실 후보의 ID. */
  targetFactIds: string[];
  /** 요청에 싣는 글 (JSON). */
  text: string;
  /** 이 요청 안에서 가리킬 수 있는 ID (셀 또는 블록) → 그 글자. */
  ids: Map<string, string>;
}

export interface PlanOptions {
  maxTargetsPerCall: number;
  includeSentences: boolean;
}

function renderRow(grid: Grid, row: number, cols?: Set<number>): { r: number; cells: Record<string, string> } {
  const cells: Record<string, string> = {};
  (grid.cells[row] ?? []).forEach((raw, col) => {
    const text = normalizeText(raw);
    if (text !== "" && (!cols || cols.has(col))) cells[`c${col}`] = text;
  });
  return { r: row, cells };
}

function tableUnits(grid: Grid, targets: Fact[], maxTargets: number): LabelUnit[] {
  const byRow = new Map<number, Fact[]>();
  for (const fact of targets) {
    const row = fact.where.row as number;
    byRow.set(row, [...(byRow.get(row) ?? []), fact]);
  }
  const chunks: Fact[][] = [];
  let current: Fact[] = [];
  for (const row of [...byRow.keys()].sort((a, b) => a - b)) {
    const facts = byRow.get(row) as Fact[];
    if (current.length > 0 && current.length + facts.length > maxTargets) {
      chunks.push(current);
      current = [];
    }
    current.push(...facts);
  }
  if (current.length > 0) chunks.push(current);

  const headerCols = new Set(grid.header.cols);
  return chunks.map((facts, index) => {
    const rows = new Set(facts.map((fact) => fact.where.row as number));
    // 병합 셀이라 위 행에서 물려받은 행 머리글은 그 행의 머리글 열만 같이 싣는다.
    const inheritedRows = new Set<number>();
    for (const fact of facts) for (const ref of fact.headers?.row ?? []) if (ref.inherited) inheritedRows.add(Number(/^r(\d+)c/.exec(ref.cell)?.[1]));
    // 본문 중간의 머리글 행(아래에 붙은 표의 머리글)은 답할 행보다 위에 있는 것을 전부 싣는다.
    const last = Math.max(...rows);
    const inner = (grid.innerHeaders ?? []).filter((row) => row < last);
    const rendered = [
      ...grid.header.rows.map((row) => renderRow(grid, row)),
      ...inner.map((row) => renderRow(grid, row)),
      ...[...inheritedRows].filter((row) => !rows.has(row) && !grid.header.rows.includes(row)).sort((a, b) => a - b).map((row) => renderRow(grid, row, headerCols)),
      ...[...rows].sort((a, b) => a - b).map((row) => renderRow(grid, row)),
    ].sort((a, b) => a.r - b.r);
    const ids = new Map<string, string>();
    for (const row of rendered) for (const [col, text] of Object.entries(row.cells)) ids.set(`r${row.r}${col}`, text);
    const payload = {
      kind: "table",
      doc: grid.docId,
      table_id: grid.id,
      ...(grid.page === null ? {} : { page: grid.page }),
      ...(grid.sheet === null ? {} : { sheet: grid.sheet }),
      title_candidates: grid.titleCandidates,
      unit_lines: grid.unitLines,
      header_rows: grid.header.rows,
      header_cols: grid.header.cols,
      header_basis: grid.header.basis,
      inner_header_rows: inner,
      footnote_marks: grid.footnoteMarks.filter((mark) => rows.has(mark.row)).map((mark) => ({ cell: cellId(mark.row, mark.col), mark: mark.mark })),
      rows: rendered,
      targets: facts.map((fact) => cellId(fact.where.row as number, fact.where.col as number)),
    };
    return {
      id: chunks.length === 1 ? grid.id : `${grid.id}/${index + 1}`,
      docId: grid.docId,
      kind: "table" as const,
      gridId: grid.id,
      targetFactIds: facts.map((fact) => fact.id),
      text: JSON.stringify(payload, null, 1),
      ids,
    };
  });
}

function blockUnits(extraction: Extraction, targets: Fact[], blockText: Map<string, string>, maxTargets: number): LabelUnit[] {
  const byPage = new Map<number, Fact[]>();
  for (const fact of targets) byPage.set(fact.where.page as number, [...(byPage.get(fact.where.page as number) ?? []), fact]);
  const units: LabelUnit[] = [];
  for (const [page, facts] of [...byPage.entries()].sort((a, b) => a[0] - b[0])) {
    for (let at = 0, part = 1; at < facts.length; at += maxTargets, part += 1) {
      const slice = facts.slice(at, at + maxTargets);
      const blockIds = [...new Set(slice.map((fact) => fact.where.blockId as string))];
      const ids = new Map(blockIds.map((id) => [id, blockText.get(id) ?? ""]));
      const payload = {
        kind: "blocks",
        doc: extraction.docId,
        page,
        blocks: blockIds.map((id) => ({ id, text: ids.get(id) })),
        targets: slice.map((fact) => ({ block: fact.where.blockId, quote: fact.raw, occurrence: fact.where.span?.occurrence })),
      };
      units.push({ id: `${extraction.docId}:p${page}-blocks/${part}`, docId: extraction.docId, kind: "blocks", gridId: null, targetFactIds: slice.map((fact) => fact.id), text: JSON.stringify(payload, null, 1), ids });
    }
  }
  return units;
}

/** 뜻 붙이기의 대상인가. v1 은 추출 상태가 정상인 표의 숫자 셀이고, 옵션을 켜면 문장 속 숫자도 든다. */
export function inLabelScope(fact: Fact, options: Pick<PlanOptions, "includeSentences">): boolean {
  if (fact.bucket === "table_cell") return fact.tableStatus === "ok";
  if (fact.bucket === "sentence") return options.includeSentences;
  return false;
}

/** 아직 뜻이 안 붙은 사실 후보를 호출 단위로 묶는다. `blockText` 는 문장 속 숫자를 다룰 때만 필요하다. */
export function planUnits(extraction: Extraction, pending: Fact[], options: PlanOptions, blockText: Map<string, string> = new Map()): LabelUnit[] {
  const units: LabelUnit[] = [];
  const byGrid = new Map<string, Fact[]>();
  for (const fact of pending) if (fact.bucket === "table_cell" && fact.where.gridId) byGrid.set(fact.where.gridId, [...(byGrid.get(fact.where.gridId) ?? []), fact]);
  for (const grid of extraction.grids) {
    const targets = byGrid.get(grid.id);
    if (targets && targets.length > 0) units.push(...tableUnits(grid, targets, options.maxTargetsPerCall));
  }
  const sentences = pending.filter((fact) => fact.bucket === "sentence");
  if (sentences.length > 0) units.push(...blockUnits(extraction, sentences, blockText, options.maxTargetsPerCall));
  return units;
}

// ── 답의 검증 ───────────────────────────────────────────────────────────────

export type RejectReason =
  | "unknown_id" // 요청에 없는 셀·블록을 가리켰다
  | "not_a_target" // 있는 셀이지만 답해 달라고 한 숫자가 아니다
  | "quote_missing" // 문장 속 숫자인데 원문 글자를 주지 않았다
  | "quote_not_found" // 그 글자가 그 블록에 없다 (또는 그 순번만큼 나오지 않는다)
  | "quote_ambiguous" // 같은 글자가 여러 번 나오는데 몇 번째인지 주지 않았다
  | "quote_not_a_number" // 글자는 있지만 코드가 뽑은 숫자 후보의 자리와 다르다
  | "duplicate_answer" // 같은 숫자에 답이 둘 이상이다
  | "unknown_indicator"
  | "unknown_boundary"
  | "bad_period"
  | "period_not_in_source" // 그 연도가 요청의 원문 어디에도 없다
  | "unit_not_in_source" // 그 단위 글자가 요청의 원문 어디에도 없다
  | "unknown_evidence_id";

export interface Validation {
  accepted: Label[];
  rejected: { factId: string | null; target: string; reason: RejectReason }[];
  /** 답이 없거나 버려져서 뜻이 붙지 않은 대상. */
  unanswered: string[];
}

export interface ValidateContext {
  catalog: Catalog;
  facts: Map<string, Fact>;
}

function resolveTarget(unit: LabelUnit, raw: RawLabel, ctx: ValidateContext): { factId: string } | { reason: RejectReason } {
  const targets = new Set(unit.targetFactIds);
  if (unit.kind === "table") {
    if (!unit.ids.has(raw.target)) return { reason: "unknown_id" };
    const factId = `${unit.gridId}:${raw.target}`;
    return targets.has(factId) ? { factId } : { reason: "not_a_target" };
  }
  const text = unit.ids.get(raw.target);
  if (text === undefined) return { reason: "unknown_id" };
  if (raw.quote === null || raw.quote === "") return { reason: "quote_missing" };
  const first = locateQuote(text, raw.quote, 1);
  if (!first) return { reason: "quote_not_found" };
  if (raw.occurrence === null && first.total > 1) return { reason: "quote_ambiguous" };
  const at = locateQuote(text, raw.quote, raw.occurrence ?? 1);
  if (!at) return { reason: "quote_not_found" };
  for (const factId of unit.targetFactIds) {
    const fact = ctx.facts.get(factId);
    if (fact?.where.blockId === raw.target && fact.where.span?.start === at.start && fact.where.span.end === at.end) return { factId };
  }
  return { reason: "quote_not_a_number" };
}

/** AI 의 답을 하나씩 확인한다. 확인을 통과하지 못한 답은 버리고, 그 숫자는 미확인으로 남는다. */
export function validateAnswers(unit: LabelUnit, answer: LabelAnswer, ctx: ValidateContext): Validation {
  const rejected: Validation["rejected"] = [];
  const candidates = new Map<string, Label[]>();
  const indicatorKeys = new Set(ctx.catalog.indicators.map((indicator) => indicator.key));
  const boundaryKeys = new Set(ctx.catalog.boundaries.map((boundary) => boundary.key));
  const sourceText = unit.text.normalize("NFKC");
  const sourceUnits = normalizeUnitText(unit.text);

  for (const raw of answer.labels) {
    const resolved = resolveTarget(unit, raw, ctx);
    if ("reason" in resolved) {
      rejected.push({ factId: null, target: raw.target, reason: resolved.reason });
      continue;
    }
    const { factId } = resolved;
    const fact = ctx.facts.get(factId) as Fact;
    const reject = (reason: RejectReason): void => {
      rejected.push({ factId, target: raw.target, reason });
      // 버린 답도 「그 숫자에 답이 있었다」는 사실은 남겨 중복 판정에 쓴다.
      candidates.set(factId, [...(candidates.get(factId) ?? []), null as unknown as Label]);
    };
    if (raw.evidence.some((id) => !unit.ids.has(id))) {
      reject("unknown_evidence_id");
      continue;
    }
    const boundary = raw.boundary ?? UNSPECIFIED_BOUNDARY;
    if (raw.kind === "indicator") {
      if (raw.indicator_key === null || !indicatorKeys.has(raw.indicator_key)) {
        reject("unknown_indicator");
        continue;
      }
      if (!boundaryKeys.has(boundary)) {
        reject("unknown_boundary");
        continue;
      }
      if (raw.period !== null && !/^(19|20)\d{2}$/.test(raw.period)) {
        reject("bad_period");
        continue;
      }
      // 연도 글자가 홀로 선 꼴로 있어야 한다 (`12,025` 안의 2025 는 아니다).
      if (raw.period !== null && !new RegExp(`(?<![\\d,.])${raw.period}(?![\\d,])`).test(sourceText) && !sourceText.includes(`'${raw.period.slice(2)}`) && !sourceText.includes(`’${raw.period.slice(2)}`)) {
        reject("period_not_in_source");
        continue;
      }
      if (raw.unit !== null && !sourceUnits.includes(normalizeUnitText(raw.unit)) && normalizeUnitText(fact.unitSuffix ?? "") !== normalizeUnitText(raw.unit)) {
        reject("unit_not_in_source");
        continue;
      }
    }
    const label: Label = {
      factId,
      kind: raw.kind,
      indicatorKey: raw.kind === "indicator" ? raw.indicator_key : null,
      freeText: raw.free_text,
      period: raw.kind === "indicator" ? raw.period : null,
      unit: raw.kind === "indicator" ? raw.unit : null,
      boundary: raw.kind === "indicator" ? boundary : UNSPECIFIED_BOUNDARY,
      basisNote: raw.basis_note,
      confidence: raw.confidence,
      evidence: raw.evidence,
      source: "ai",
    };
    candidates.set(factId, [...(candidates.get(factId) ?? []), label]);
  }

  const accepted: Label[] = [];
  for (const [factId, labels] of candidates) {
    if (labels.length > 1) {
      rejected.push({ factId, target: factId, reason: "duplicate_answer" });
      continue;
    }
    const only = labels[0];
    if (only) accepted.push(only);
  }
  const done = new Set(accepted.map((label) => label.factId));
  return { accepted, rejected, unanswered: unit.targetFactIds.filter((id) => !done.has(id)) };
}

/** 프롬프트에 싣는 기준 목록. */
export function renderCatalog(catalog: Catalog): string {
  const indicators = catalog.indicators.map((indicator) => `- ${indicator.key} | ${indicator.label} | 단위 계열 ${indicator.unitFamily}${indicator.aliases.length > 0 ? ` | 다른 이름: ${indicator.aliases.join(", ")}` : ""}`);
  const boundaries = catalog.boundaries.map((boundary) => `- ${boundary.key} | ${boundary.label}`);
  return `지표 목록 (indicator_key):\n${indicators.join("\n")}\n\n범위 목록 (boundary):\n${boundaries.join("\n")}`;
}
