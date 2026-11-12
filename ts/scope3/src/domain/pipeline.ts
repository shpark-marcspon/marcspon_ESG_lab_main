import { roundUsd, type Tags } from "llm";
import { calculateRow, validateAdjustments, type CalcResult, type CurrencyAdjustment } from "./calc.js";
import type { Factor, LicenseStatus } from "./factor.js";
import {
  CLASSIFY_PROMPT,
  RECOMMEND_PROMPT,
  classifyRequest,
  describeFailure,
  emptyUsage,
  recommendRequest,
  recordUsage,
  type Classification,
  type LlmFailure,
  type LlmUsage,
} from "./llmSteps.js";
import type { MappingMemory, MemoryEntry } from "./memory.js";
import { groupRows, type RowGroup } from "./normalize.js";
import type { FactorCatalog, LlmGateway, ShortlistFailure, ShortlistMethod, Shortlister } from "./ports.js";
import type { CandidateEvidence } from "./ranking.js";
import { defaultShortlister } from "./shortlister.js";
import type { Basis, FactorPick, FallbackReason, InputRow, SupportedCategory } from "./types.js";
import { activityDim, conversionFactor } from "./units.js";

export const DEFAULT_SHORTLIST_SIZE = 25;

export interface PipelineOptions {
  client: LlmGateway;
  store: FactorCatalog;
  memory: MappingMemory;
  /** 호출 계층의 경로 이름. */
  route: string;
  tags: Tags;
  /** 이 라이선스 상태의 계수를 후보에서 뺀다. 기본은 빼지 않는다. */
  excludeLicenseStatuses?: readonly LicenseStatus[];
  /** 한 번의 추천 호출에 넣는 후보 수. */
  shortlistSize?: number;
  /** 후보를 추리는 방식. 안 주면 `DEFAULT_SHORTLIST_METHOD` (키 없이 도는 낱말 검색) 다. */
  shortlister?: Shortlister;
  /** 지출 기반 계산에 쓰는 환율·물가 보정 계수. 없으면 계산은 `needs_currency_adjustment` 로 끝난다. */
  adjustments?: readonly CurrencyAdjustment[];
}

/** 어느 기반의 계수를 썼는지와, 지출 기반으로 넘어갔다면 그 이유. */
export interface BasisTrace {
  used: Basis;
  fellBack: boolean;
  fallbackReason: FallbackReason | null;
}

/**
 * 추천 호출에 넣기 전 후보 수, 추려서 넣은 수, 검색어와 겹친 후보 수, 추린 방식.
 * `lexicalMatches` 가 0 이면 낱말 검색으로 추린 목록은 표의 앞 순서일 뿐이다. 낱말 검색을 쓰지 않는 방식은 null.
 */
export interface CandidateStats {
  total: number;
  shown: number;
  lexicalMatches: number | null;
  method: ShortlistMethod;
}

/** 묶음 하나에 대한 판단. 추천하지 못한 사정은 종류별 상태로 남긴다. */
export type GroupDecision =
  | {
      status: "recommended";
      source: "memory" | "llm";
      category: SupportedCategory;
      /** 첫째가 대표 결과(one-shot), 전체가 후보 목록이다. 기억에서 온 것은 하나다. */
      picks: FactorPick[];
      basis: BasisTrace;
      classification: Classification | null;
      memoryEntry: MemoryEntry | null;
      /** 추천 호출에 넣은 후보의 규모. 기억에서 온 판단은 null. */
      candidates: CandidateStats | null;
    }
  /** 기억의 계수 id 가 지금 표에 없다. LLM 으로 넘기지 않고 사람에게 돌린다. */
  | { status: "memory_factor_missing"; source: "memory"; memoryEntry: MemoryEntry }
  /** 기억의 카테고리가 그 계수의 Scope 3 카테고리 후보에 없다. 기억이 잘못됐거나 표가 바뀐 것이다. */
  | { status: "memory_category_mismatch"; source: "memory"; memoryEntry: MemoryEntry; factorCategories: number[] }
  /** 기억의 계수가 라이선스 설정으로 제외돼 있다. */
  | { status: "memory_factor_excluded"; source: "memory"; memoryEntry: MemoryEntry; licenseStatus: LicenseStatus }
  | { status: "classification_failed"; source: "llm"; llm: LlmFailure }
  /** AI 적용 카테고리(1·2·4·6·7·9) 밖이거나 판단 불가. */
  | { status: "unsupported_category"; source: "llm"; classification: Classification }
  /** 활동 기반도 지출 기반도 후보가 없다. */
  | { status: "no_candidates"; source: "llm"; category: SupportedCategory; classification: Classification; physical: FallbackReason }
  /** 후보는 있었지만 LLM 이 쓸 만한 것이 없다고 답했다. */
  | { status: "no_suitable_factor"; source: "llm"; category: SupportedCategory; classification: Classification; physical: FallbackReason }
  | { status: "recommendation_failed"; source: "llm"; category: SupportedCategory; classification: Classification; basis: "physical" | "spend"; llm: LlmFailure }
  /** 후보를 추리지 못했다 (임베딩 호출 실패 등). 다른 방식으로 조용히 넘어가지 않고 추천 호출도 하지 않는다. */
  | { status: "shortlist_failed"; source: "llm"; category: SupportedCategory; classification: Classification; basis: "physical" | "spend"; method: ShortlistMethod; shortlist: ShortlistFailure };

export interface RowResult {
  row: InputRow;
  groupKey: string;
  decision: GroupDecision;
  calc: CalcResult;
}

/** 추천 호출 한 번에 넣은 후보 목록. 추리기가 정답을 떨어뜨렸는지 보려고 id 와 후보별 근거(겹친 낱말, 유사도 순위)까지 남긴다. */
export interface ShortlistTrace {
  basis: "physical" | "spend";
  /** 어느 방식으로 추렸는지. */
  method: ShortlistMethod;
  /** 추리기 전 후보 수. */
  total: number;
  shownIds: string[];
  evidence: CandidateEvidence[];
  /** 추리지 못했으면 그 사정. 이때 `shownIds` 는 비어 있다. */
  failure: ShortlistFailure | null;
}

export interface PipelineResult {
  rows: RowResult[];
  /** `shortlists` 는 추천 호출마다 하나다 (호출이 실패했어도 남는다). 기억에서 온 묶음과 추천까지 가지 못한 묶음은 비어 있다. */
  groups: { key: string; rowIds: string[]; decision: GroupDecision; shortlists: ShortlistTrace[] }[];
  usage: LlmUsage;
  /** 후보 추리기 방식과 그 설정, 추리기에 쓴 임베딩 비용 (USD. `usage` 에는 들어 있지 않다). */
  shortlist: { method: ShortlistMethod; description: string; embeddingCostUsd: number };
}

type RecommendOutcome =
  | { ok: true; picks: FactorPick[]; candidates: CandidateStats }
  | { ok: false; failure: LlmFailure }
  | { ok: false; failure: null; method: ShortlistMethod; shortlist: ShortlistFailure };

const DEFAULT_SHORTLISTER = defaultShortlister();

/** 추리기에 쓴 임베딩 비용의 누계. 묶음을 차례로 처리하므로 한 실행에 하나다. */
interface ShortlistCost {
  embeddingCostUsd: number;
}

async function recommend(
  options: PipelineOptions,
  usage: LlmUsage,
  row: InputRow,
  category: SupportedCategory,
  basis: "physical" | "spend",
  candidates: readonly Factor[],
  keywords: string[],
  trace: ShortlistTrace[],
  cost: ShortlistCost,
): Promise<RecommendOutcome> {
  const texts = [row.itemName, ...(row.transport ? [row.transport.mode] : [])];
  const short = await (options.shortlister ?? DEFAULT_SHORTLISTER).shortlist(candidates, { texts, keywords }, options.shortlistSize ?? DEFAULT_SHORTLIST_SIZE);
  cost.embeddingCostUsd = roundUsd(cost.embeddingCostUsd + short.embeddingCostUsd);
  if (!short.ok) {
    trace.push({ basis, method: short.method, total: candidates.length, shownIds: [], evidence: [], failure: short.failure });
    return { ok: false, failure: null, method: short.method, shortlist: short.failure };
  }
  trace.push({ basis, method: short.method, total: short.total, shownIds: short.factors.map((factor) => factor.id), evidence: short.evidence, failure: null });
  const result = await options.client.call(recommendRequest(row, category, basis, short.factors, options.route, options.tags));
  recordUsage(usage, RECOMMEND_PROMPT, result.meta);
  if (result.kind !== "success") return { ok: false, failure: describeFailure(result) };
  return {
    ok: true,
    candidates: { total: short.total, shown: short.factors.length, lexicalMatches: short.lexicalMatches, method: short.method },
    picks: result.data.picks.map((pick) => ({ factorId: pick.factor_id, reason: pick.reason, confidence: pick.confidence })),
  };
}

function decideFromMemory(options: PipelineOptions, entry: MemoryEntry): GroupDecision {
  const factor = options.store.get(entry.factorId);
  if (!factor) return { status: "memory_factor_missing", source: "memory", memoryEntry: entry };
  if (!factor.scope3Categories.includes(entry.category)) {
    return { status: "memory_category_mismatch", source: "memory", memoryEntry: entry, factorCategories: factor.scope3Categories };
  }
  if ((options.excludeLicenseStatuses ?? []).includes(factor.licenseStatus)) {
    return { status: "memory_factor_excluded", source: "memory", memoryEntry: entry, licenseStatus: factor.licenseStatus };
  }
  return {
    status: "recommended",
    source: "memory",
    category: entry.category,
    picks: [{ factorId: entry.factorId, reason: `확정 매핑 기억 (${entry.confirmedBy}, ${entry.confirmedAt})`, confidence: "high" }],
    basis: { used: factor.factorType, fellBack: false, fallbackReason: null },
    classification: null,
    memoryEntry: entry,
    candidates: null,
  };
}

function failed(outcome: Exclude<RecommendOutcome, { ok: true }>, category: SupportedCategory, classification: Classification, basis: "physical" | "spend"): GroupDecision {
  if (outcome.failure === null) return { status: "shortlist_failed", source: "llm", category, classification, basis, method: outcome.method, shortlist: outcome.shortlist };
  return { status: "recommendation_failed", source: "llm", category, classification, basis, llm: outcome.failure };
}

/**
 * 묶음 하나를 판단한다. 순서는 고정이다: 확정 매핑 기억 → 카테고리 분류 → 후보 조회 → top-3 추천.
 * 기억에 있으면 LLM 을 부르지 않는다. 활동(물량·거리) 기반 후보를 먼저 보고, 없거나 단위가 맞지 않거나
 * LLM 이 쓸 만한 것이 없다고 하면 지출 기반으로 넘어간다.
 */
export async function decideGroup(options: PipelineOptions, group: RowGroup, usage: LlmUsage, trace: ShortlistTrace[] = [], cost: ShortlistCost = { embeddingCostUsd: 0 }): Promise<GroupDecision> {
  const remembered = options.memory.lookup(group.key);
  if (remembered) return decideFromMemory(options, remembered);

  const row = group.rows[0] as InputRow;
  const classified = await options.client.call(classifyRequest(row, options.route, options.tags));
  recordUsage(usage, CLASSIFY_PROMPT, classified.meta);
  if (classified.kind !== "success") return { status: "classification_failed", source: "llm", llm: describeFailure(classified) };
  const classification: Classification = {
    category: classified.data.category,
    keywords: classified.data.keywords_en,
    confidence: classified.data.confidence,
    reason: classified.data.reason,
  };
  if (classification.category === "other") return { status: "unsupported_category", source: "llm", classification };
  const category = classification.category;
  const exclude = options.excludeLicenseStatuses ?? [];

  const physicalAll = options.store.candidates({ category, basis: "physical", excludeLicenseStatuses: exclude });
  const dim = activityDim(row);
  const physical = physicalAll.filter((factor) => conversionFactor(dim, factor.denominatorDim) !== null);
  let fallbackReason: FallbackReason;
  if (physical.length > 0) {
    const outcome = await recommend(options, usage, row, category, "physical", physical, classification.keywords, trace, cost);
    if (!outcome.ok) return failed(outcome, category, classification, "physical");
    const first = outcome.picks[0];
    if (first) {
      const used = (options.store.get(first.factorId) as Factor).factorType;
      return { status: "recommended", source: "llm", category, picks: outcome.picks, basis: { used, fellBack: false, fallbackReason: null }, classification, memoryEntry: null, candidates: outcome.candidates };
    }
    fallbackReason = "llm_found_none_suitable";
  } else {
    fallbackReason = physicalAll.length === 0 ? "no_activity_candidates" : "unit_mismatch";
  }

  const spend = options.store.candidates({ category, basis: "spend", excludeLicenseStatuses: exclude });
  if (spend.length === 0) {
    if (fallbackReason === "llm_found_none_suitable") return { status: "no_suitable_factor", source: "llm", category, classification, physical: fallbackReason };
    return { status: "no_candidates", source: "llm", category, classification, physical: fallbackReason };
  }
  const outcome = await recommend(options, usage, row, category, "spend", spend, classification.keywords, trace, cost);
  if (!outcome.ok) return failed(outcome, category, classification, "spend");
  if (outcome.picks.length === 0) return { status: "no_suitable_factor", source: "llm", category, classification, physical: fallbackReason };
  return { status: "recommended", source: "llm", category, picks: outcome.picks, basis: { used: "spend", fellBack: true, fallbackReason }, classification, memoryEntry: null, candidates: outcome.candidates };
}

/** 묶음의 판단을 행에 펼쳐 대표 결과(첫째 추천)로 계산한다. */
export function calculateFor(options: Pick<PipelineOptions, "store" | "adjustments">, row: InputRow, decision: GroupDecision): CalcResult {
  if (decision.status !== "recommended") return { status: "not_calculated", reason: decision.status };
  const factor = options.store.get((decision.picks[0] as FactorPick).factorId) as Factor;
  return calculateRow(row, factor, options.adjustments ?? []);
}

/** 행을 묶어 묶음 단위로 판단하고 행에 펼친다. 묶음은 차례로 처리한다 (호출 순서가 결과와 기록에 그대로 남는다). */
export async function runPipeline(rows: readonly InputRow[], options: PipelineOptions): Promise<PipelineResult> {
  validateAdjustments(options.adjustments ?? []);
  const ids = new Set<string>();
  for (const row of rows) {
    if (ids.has(row.rowId)) throw new Error(`rowId 가 겹친다: ${row.rowId}`);
    ids.add(row.rowId);
  }
  const usage = emptyUsage();
  const groups = groupRows(rows);
  const decisions = new Map<string, GroupDecision>();
  const traces = new Map<string, ShortlistTrace[]>();
  const cost: ShortlistCost = { embeddingCostUsd: 0 };
  for (const group of groups) {
    const trace: ShortlistTrace[] = [];
    decisions.set(group.key, await decideGroup(options, group, usage, trace, cost));
    traces.set(group.key, trace);
  }

  const keyOfRow = new Map<string, string>();
  for (const group of groups) for (const row of group.rows) keyOfRow.set(row.rowId, group.key);
  const results = rows.map((row): RowResult => {
    const groupKey = keyOfRow.get(row.rowId) as string;
    const decision = decisions.get(groupKey) as GroupDecision;
    return { row, groupKey, decision, calc: calculateFor(options, row, decision) };
  });
  return {
    rows: results,
    groups: groups.map((group) => ({ key: group.key, rowIds: group.rows.map((row) => row.rowId), decision: decisions.get(group.key) as GroupDecision, shortlists: traces.get(group.key) as ShortlistTrace[] })),
    usage,
    shortlist: { method: (options.shortlister ?? DEFAULT_SHORTLISTER).method, description: (options.shortlister ?? DEFAULT_SHORTLISTER).description, embeddingCostUsd: cost.embeddingCostUsd },
  };
}
