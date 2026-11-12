import { roundUsd, type DryRunReport, type LlmClient, type LlmConfig, type Tags } from "llm";
import type { ExampleRow } from "./examples.js";
import type { Factor, LicenseStatus } from "../domain/factor.js";
import { classifyRequest, recommendRequest, type LlmUsage } from "../domain/llmSteps.js";
import { groupRows } from "../domain/normalize.js";
import { DEFAULT_SHORTLIST_SIZE, type GroupDecision, type PipelineResult } from "../domain/pipeline.js";
import type { FactorCatalog, MappingMemory, ShortlistMethod, Shortlister } from "../domain/ports.js";
import { defaultShortlister } from "../domain/shortlister.js";
import { SUPPORTED_CATEGORIES, type FallbackReason, type InputRow, type SupportedCategory } from "../domain/types.js";
import { activityDim, conversionFactor } from "../domain/units.js";

/**
 * 「정답」 판정 기준. 무엇을 정답으로 볼지는 결정 대기(PRJ11-ISSUE-6)라 둘 다 계산하고 대표 기준만 설정으로 고른다.
 * - factor_id: 추천한 계수 id 가 정답 id 중 하나와 같다.
 * - sector: 추천한 계수가 정답 계수와 같은 분류 부문이다 (`sectorKey`).
 */
export type MatchCriterion = "factor_id" | "sector";

export interface SectorOptions {
  /** NAICS 코드의 앞 몇 자리가 같으면 같은 부문으로 보는지. */
  naicsDigits: number;
  /** 출처의 분류 계층을 위에서 몇 단계까지 보는지. */
  pathDepth: number;
}

export const DEFAULT_SECTOR: SectorOptions = { naicsDigits: 4, pathDepth: 2 };

/** 계수의 분류 부문. 출처가 다르면 분류 체계가 달라 같은 부문이 되지 않는다. */
export function sectorKey(factor: Factor, options: SectorOptions = DEFAULT_SECTOR): string {
  if (factor.classification?.system.startsWith("NAICS")) return `naics:${factor.classification.code.slice(0, options.naicsDigits)}`;
  return `${factor.sourceId}:${factor.categoryPath.slice(0, options.pathDepth).join("/")}`;
}

function isSupported(category: number): category is SupportedCategory {
  return (SUPPORTED_CATEGORIES as readonly number[]).includes(category);
}

/** 판단이 가리키는 카테고리. 분류에 실패했으면 null, AI 적용 범위 밖이면 "other". */
export function predictedCategory(decision: GroupDecision): SupportedCategory | "other" | null {
  switch (decision.status) {
    case "recommended":
    case "no_candidates":
    case "no_suitable_factor":
    case "recommendation_failed":
    case "shortlist_failed":
      return decision.category;
    case "unsupported_category":
      return "other";
    case "memory_factor_missing":
    case "memory_category_mismatch":
    case "memory_factor_excluded":
      return decision.memoryEntry.category;
    case "classification_failed":
      return null;
  }
}

interface Rate {
  hits: number;
  evaluated: number;
  /** evaluated 가 0 이면 null (0 이 아니다). */
  rate: number | null;
}

function rate(hits: number, evaluated: number): Rate {
  return { hits, evaluated, rate: evaluated === 0 ? null : hits / evaluated };
}

export interface EvalReport {
  primaryCriterion: MatchCriterion;
  sector: SectorOptions;
  counts: { rows: number; groups: number; withGoldCategory: number; withGoldFactor: number; unanswerable: number };
  /** 기억에서 판단이 나온 비율. */
  memory: { hitRows: number; hitGroups: number; rowRatio: number; groupRatio: number };
  /** 정답 카테고리가 있는 행 중 맞힌 비율. 정답이 AI 적용 범위 밖이면 "other" 로 답해야 맞다. */
  classification: Rate & { llmOnly: Rate };
  /** 정답 계수가 있는 행 기준. */
  top1: Record<MatchCriterion, Rate>;
  top3: Record<MatchCriterion, Rate>;
  /** 기억 적중 행을 뺀 top-3. */
  top3LlmOnly: Record<MatchCriterion, Rate>;
  /** 정답을 달 수 없는 행(계수 없음)에서 추천을 내지 않은 수와 낸 수. */
  unanswerable: { rows: number; withoutRecommendation: number; withRecommendation: number };
  basis: { activity: number; distance: number; spend: number; fellBack: number; byReason: Partial<Record<FallbackReason, number>> };
  decisionStatus: Record<string, number>;
  calcStatus: Record<string, number>;
  /**
   * 후보 추리기의 재현율 (추천 이전 단계). 정답 계수가 있고 기억에서 오지 않은 행 중, 추천 호출까지 간 행이 분모다.
   * 정답 계수 id 중 하나라도 그 묶음의 추린 목록(활동·지출 추천 호출의 합집합)에 있으면 적중이다.
   * `correctCategory` 는 분류가 정답 카테고리를 낸 행만 본 것이다 (분류가 틀리면 정답 계수는 후보에 있을 수 없다).
   * `notReached` 는 분류 실패·범위 밖 판정·후보 없음으로 추천 호출이 없었던 행이다. 분모에 넣지 않는다.
   */
  shortlistRecall: Rate & { correctCategory: Rate; notReached: number };
  /** LLM·임베딩 단계가 실패로 끝난 묶음 수. 키는 `단계:실패 종류` (예: `classify:schema_mismatch`, `shortlist:provider_error`). */
  llmFailures: Record<string, number>;
  llm: LlmUsage;
  /** 후보를 추린 방식과 그 설정, 추리기에 쓴 임베딩 비용 (`llm.costUsd` 에는 들어 있지 않다). */
  shortlist: PipelineResult["shortlist"];
}

function bump(target: Record<string, number>, key: string): void {
  target[key] = (target[key] ?? 0) + 1;
}

/** 파이프라인 결과를 정답과 대조해 지표를 낸다. 두 판정 기준을 모두 계산한다. */
export function evaluate(
  examples: readonly ExampleRow[],
  result: PipelineResult,
  store: FactorCatalog,
  options: { primaryCriterion?: MatchCriterion; sector?: SectorOptions } = {},
): EvalReport {
  const sector = options.sector ?? DEFAULT_SECTOR;
  const byRow = new Map(result.rows.map((entry) => [entry.row.rowId, entry]));
  const shownByRow = new Map<string, Set<string> | null>();
  const llmFailures: Record<string, number> = {};
  for (const group of result.groups) {
    // 추리기가 실패한 호출은 추린 목록이 없다. 모든 호출이 실패한 묶음은 「추천 호출까지 가지 못한」 쪽이다.
    const reached = group.shortlists.filter((entry) => entry.failure === null);
    const shown = reached.length === 0 ? null : new Set(reached.flatMap((entry) => entry.shownIds));
    for (const rowId of group.rowIds) shownByRow.set(rowId, shown);
    if (group.decision.status === "classification_failed") bump(llmFailures, `classify:${group.decision.llm.kind}`);
    if (group.decision.status === "recommendation_failed") bump(llmFailures, `recommend:${group.decision.llm.kind}`);
    if (group.decision.status === "shortlist_failed") bump(llmFailures, `shortlist:${group.decision.shortlist.kind}`);
  }
  const recall = { hits: 0, evaluated: 0, hitsCorrect: 0, evaluatedCorrect: 0, notReached: 0 };
  const tally = {
    category: { hits: 0, evaluated: 0 },
    categoryLlm: { hits: 0, evaluated: 0 },
    top1: { factor_id: 0, sector: 0 },
    top3: { factor_id: 0, sector: 0 },
    top3Llm: { factor_id: 0, sector: 0 },
    goldFactor: 0,
    goldFactorLlm: 0,
  };
  const report: Pick<EvalReport, "unanswerable" | "basis" | "decisionStatus" | "calcStatus"> = {
    unanswerable: { rows: 0, withoutRecommendation: 0, withRecommendation: 0 },
    basis: { activity: 0, distance: 0, spend: 0, fellBack: 0, byReason: {} },
    decisionStatus: {},
    calcStatus: {},
  };
  let memoryRows = 0;

  for (const example of examples) {
    const entry = byRow.get(example.row.rowId);
    if (!entry) throw new Error(`평가할 결과에 행이 없다: ${example.row.rowId}`);
    const decision = entry.decision;
    const fromMemory = decision.source === "memory";
    if (fromMemory) memoryRows += 1;
    bump(report.decisionStatus, decision.status);
    bump(report.calcStatus, entry.calc.status);
    const picks = decision.status === "recommended" ? decision.picks : [];
    if (decision.status === "recommended") {
      report.basis[decision.basis.used] += 1;
      if (decision.basis.fellBack) {
        report.basis.fellBack += 1;
        const reason = decision.basis.fallbackReason as FallbackReason;
        report.basis.byReason[reason] = (report.basis.byReason[reason] ?? 0) + 1;
      }
    }

    if (example.gold.category !== null) {
      const expected = isSupported(example.gold.category) ? example.gold.category : "other";
      const correct = predictedCategory(decision) === expected ? 1 : 0;
      tally.category.hits += correct;
      tally.category.evaluated += 1;
      if (!fromMemory) {
        tally.categoryLlm.hits += correct;
        tally.categoryLlm.evaluated += 1;
      }
    }

    if (example.gold.factorIds.length === 0) {
      report.unanswerable.rows += 1;
      if (picks.length === 0) report.unanswerable.withoutRecommendation += 1;
      else report.unanswerable.withRecommendation += 1;
      continue;
    }
    tally.goldFactor += 1;
    if (!fromMemory) tally.goldFactorLlm += 1;
    const goldIds = new Set(example.gold.factorIds);
    if (!fromMemory) {
      const shown = shownByRow.get(example.row.rowId) ?? null;
      if (shown === null) recall.notReached += 1;
      else {
        const hit = example.gold.factorIds.some((id) => shown.has(id)) ? 1 : 0;
        recall.hits += hit;
        recall.evaluated += 1;
        if (predictedCategory(decision) === example.gold.category) {
          recall.hitsCorrect += hit;
          recall.evaluatedCorrect += 1;
        }
      }
    }
    const goldSectors = new Set(
      example.gold.factorIds.map((id) => {
        const factor = store.get(id);
        if (!factor) throw new Error(`정답 계수 id 가 후보 계수 표에 없다: ${id} (행 ${example.row.rowId})`);
        return sectorKey(factor, sector);
      }),
    );
    const matches: Record<MatchCriterion, (id: string) => boolean> = {
      factor_id: (id) => goldIds.has(id),
      sector: (id) => goldSectors.has(sectorKey(store.get(id) as Factor, sector)),
    };
    for (const criterion of ["factor_id", "sector"] as const) {
      const first = picks[0];
      if (first && matches[criterion](first.factorId)) tally.top1[criterion] += 1;
      if (picks.some((pick) => matches[criterion](pick.factorId))) {
        tally.top3[criterion] += 1;
        if (!fromMemory) tally.top3Llm[criterion] += 1;
      }
    }
  }

  const memoryGroups = result.groups.filter((group) => group.decision.source === "memory").length;
  return {
    primaryCriterion: options.primaryCriterion ?? "factor_id",
    sector,
    counts: {
      rows: examples.length,
      groups: result.groups.length,
      withGoldCategory: tally.category.evaluated,
      withGoldFactor: tally.goldFactor,
      unanswerable: report.unanswerable.rows,
    },
    memory: {
      hitRows: memoryRows,
      hitGroups: memoryGroups,
      rowRatio: memoryRows / examples.length,
      groupRatio: memoryGroups / result.groups.length,
    },
    classification: { ...rate(tally.category.hits, tally.category.evaluated), llmOnly: rate(tally.categoryLlm.hits, tally.categoryLlm.evaluated) },
    top1: { factor_id: rate(tally.top1.factor_id, tally.goldFactor), sector: rate(tally.top1.sector, tally.goldFactor) },
    top3: { factor_id: rate(tally.top3.factor_id, tally.goldFactor), sector: rate(tally.top3.sector, tally.goldFactor) },
    top3LlmOnly: { factor_id: rate(tally.top3Llm.factor_id, tally.goldFactorLlm), sector: rate(tally.top3Llm.sector, tally.goldFactorLlm) },
    ...report,
    shortlistRecall: { ...rate(recall.hits, recall.evaluated), correctCategory: rate(recall.hitsCorrect, recall.evaluatedCorrect), notReached: recall.notReached },
    llmFailures,
    llm: result.usage,
    shortlist: result.shortlist,
  };
}

export interface CoverageReport {
  excludeLicenseStatuses: LicenseStatus[];
  /** 카테고리 1·2 활동(물량) 기반 후보 수. */
  activityCandidates: { category1: number; category2: number };
  /** 정답 카테고리가 AI 적용 범위인 행 기준. LLM 과 무관하게 단위와 후보 유무만 본 것이다. */
  rows: { evaluated: number; physicalPossible: number; spendOnly: number; noCandidates: number; byReason: Partial<Record<FallbackReason, number>> };
  /** 그중 카테고리 1·2 행. */
  rowsCategory12: { evaluated: number; physicalPossible: number; spendOnly: number };
}

/**
 * 정답 카테고리를 알 때, 단위가 맞는 활동 기반 후보가 있는 행과 지출 기반으로 넘어갈 수밖에 없는 행을 센다.
 * LLM 을 부르지 않는다. 실제 운행에서는 여기에 「후보는 있지만 맞는 것이 없다」가 더해져 지출 기반 비율이 이보다 높아진다.
 */
export function analyzeCoverage(examples: readonly ExampleRow[], store: FactorCatalog, excludeLicenseStatuses: readonly LicenseStatus[] = []): CoverageReport {
  const count = (category: number) =>
    store.candidates({ category, basis: "physical", excludeLicenseStatuses }).filter((factor) => factor.factorType === "activity").length;
  const rows: CoverageReport["rows"] = { evaluated: 0, physicalPossible: 0, spendOnly: 0, noCandidates: 0, byReason: {} };
  const rows12 = { evaluated: 0, physicalPossible: 0, spendOnly: 0 };
  for (const example of examples) {
    const category = example.gold.category;
    if (category === null || !isSupported(category)) continue;
    const is12 = category === 1 || category === 2;
    rows.evaluated += 1;
    if (is12) rows12.evaluated += 1;
    const physicalAll = store.candidates({ category, basis: "physical", excludeLicenseStatuses });
    const dim = activityDim(example.row);
    if (physicalAll.some((factor) => conversionFactor(dim, factor.denominatorDim) !== null)) {
      rows.physicalPossible += 1;
      if (is12) rows12.physicalPossible += 1;
      continue;
    }
    const reason: FallbackReason = physicalAll.length === 0 ? "no_activity_candidates" : "unit_mismatch";
    rows.byReason[reason] = (rows.byReason[reason] ?? 0) + 1;
    if (store.candidates({ category, basis: "spend", excludeLicenseStatuses }).length > 0) {
      rows.spendOnly += 1;
      if (is12) rows12.spendOnly += 1;
    } else rows.noCandidates += 1;
  }
  return {
    excludeLicenseStatuses: [...excludeLicenseStatuses],
    activityCandidates: { category1: count(1), category2: count(2) },
    rows,
    rowsCategory12: rows12,
  };
}

export interface PlanOptions {
  client: LlmClient;
  config: LlmConfig;
  store: FactorCatalog;
  memory: MappingMemory;
  route: string;
  tags: Tags;
  excludeLicenseStatuses?: readonly LicenseStatus[];
  shortlistSize?: number;
  /** 추천 프롬프트의 크기를 어림할 때 쓰는 추리기. 안 주면 `DEFAULT_SHORTLIST_METHOD` 다. 공급사를 부르는 방식(임베딩)은 넣지 않는다. */
  shortlister?: Shortlister;
}

interface Estimate {
  calls: number;
  inputTokens: number;
  /** 입력 어림 + 출력 상한을 다 썼을 때의 비용 상한 합 (USD). */
  costUpperBoundUsd: number;
}

export interface DryRunPlan {
  dryRun: true;
  route: string;
  model: string;
  provider: string;
  shortlistSize: number;
  /** 추천 프롬프트를 어림할 때 쓴 추리기 방식. */
  shortlistMethod: ShortlistMethod;
  groups: { total: number; memoryHits: number; needLlm: number; withoutAssumedCategory: number };
  /** 묶음마다 분류 1회 + 추천 1회. */
  minimum: Estimate;
  /** 활동 기반 추천에서 맞는 것이 없어 지출 기반 추천을 한 번 더 부르는 경우까지. 스키마 재시도와 대체 호출은 넣지 않았다. */
  maximum: Estimate;
  warnings: string[];
  assumptions: string[];
  /** 한 카테고리의 후보 전체를 추천 프롬프트에 넣었을 때의 어림 토큰. 추리는 단계가 필요한지의 근거다. */
  fullCandidateLists: { category: number; basis: "physical" | "spend"; candidates: number; inputTokens: number; contextWindowTokens: number; fits: boolean }[];
  shortlistedPromptTokens: { min: number; max: number } | null;
}

const PROBE_ROW: InputRow = { rowId: "probe", itemName: "측정용 품목", quantity: null, unit: "kg", amount: null, currency: "KRW", date: "2026-01-01", orgUnit: "측정", transport: null };

function add(target: Estimate, report: DryRunReport): void {
  target.calls += 1;
  target.inputTokens += report.estimate.inputTokens;
  target.costUpperBoundUsd = roundUsd(target.costUpperBoundUsd + report.estimate.costUpperBoundUsd);
}

/**
 * 공급사를 부르지 않고 예상 호출 수와 비용 상한을 낸다.
 * 추천 호출의 크기는 분류 결과에 달려 있으므로, 분류가 정답 카테고리를 낸다고 가정하고 프롬프트를 조립한다.
 * 검색어(영문)는 분류 단계가 내는 값이라 여기서는 품목명만으로 후보를 추린다. 추린 수는 같아 토큰 어림은 거의 같다.
 * 추리기는 실제 실행과 같은 방식을 쓴다 (`options.shortlister`, 안 주면 기본 방식).
 */
export async function planDryRun(examples: readonly ExampleRow[], options: PlanOptions): Promise<DryRunPlan> {
  const limit = options.shortlistSize ?? DEFAULT_SHORTLIST_SIZE;
  const exclude = options.excludeLicenseStatuses ?? [];
  const goldCategory = new Map(examples.map((example) => [example.row.rowId, example.gold.category]));
  const groups = groupRows(examples.map((example) => example.row));
  const minimum: Estimate = { calls: 0, inputTokens: 0, costUpperBoundUsd: 0 };
  const maximum: Estimate = { calls: 0, inputTokens: 0, costUpperBoundUsd: 0 };
  const warnings = new Set<string>();
  const recommendTokens: number[] = [];
  let memoryHits = 0;
  let withoutAssumedCategory = 0;
  let head: DryRunReport | null = null;

  const shortlister = options.shortlister ?? defaultShortlister();
  const plan = async (row: InputRow, category: SupportedCategory, basis: "physical" | "spend", candidates: readonly Factor[]): Promise<DryRunReport> => {
    const short = await shortlister.shortlist(candidates, { texts: [row.itemName, ...(row.transport ? [row.transport.mode] : [])], keywords: [] }, limit);
    // dry-run 은 돈을 쓰지 않는다. 추리기가 실패했거나 비용을 냈다면 공급사를 부르는 방식이 들어온 것이다.
    if (!short.ok || short.embeddingCostUsd !== 0) throw new Error(`dry-run 의 추리기는 공급사를 부르지 않는 방식이어야 한다: ${shortlister.method}`);
    const report = options.client.dryRun(recommendRequest(row, category, basis, short.factors, options.route, options.tags));
    recommendTokens.push(report.estimate.inputTokens);
    for (const warning of report.warnings) warnings.add(`${warning.code}: ${warning.message}`);
    return report;
  };

  for (const group of groups) {
    if (options.memory.lookup(group.key)) {
      memoryHits += 1;
      continue;
    }
    const row = group.rows[0] as InputRow;
    const classify = options.client.dryRun(classifyRequest(row, options.route, options.tags));
    head ??= classify;
    for (const warning of classify.warnings) warnings.add(`${warning.code}: ${warning.message}`);
    add(minimum, classify);
    add(maximum, classify);

    const category = goldCategory.get(row.rowId) ?? null;
    if (category === null || !isSupported(category)) {
      withoutAssumedCategory += 1;
      continue;
    }
    const physical = options.store
      .candidates({ category, basis: "physical", excludeLicenseStatuses: exclude })
      .filter((factor) => conversionFactor(activityDim(row), factor.denominatorDim) !== null);
    const spend = options.store.candidates({ category, basis: "spend", excludeLicenseStatuses: exclude });
    if (physical.length > 0) {
      const first = await plan(row, category, "physical", physical);
      add(minimum, first);
      add(maximum, first);
      if (spend.length > 0) add(maximum, await plan(row, category, "spend", spend));
    } else if (spend.length > 0) {
      const only = await plan(row, category, "spend", spend);
      add(minimum, only);
      add(maximum, only);
    }
  }

  const probe = head ?? options.client.dryRun(classifyRequest(PROBE_ROW, options.route, options.tags));
  const contextWindowTokens = (options.config.models[probe.model] as LlmConfig["models"][string]).contextWindowTokens;
  const fullCandidateLists: DryRunPlan["fullCandidateLists"] = [];
  for (const category of SUPPORTED_CATEGORIES) {
    for (const basis of ["physical", "spend"] as const) {
      const candidates = options.store.candidates({ category, basis, excludeLicenseStatuses: exclude });
      if (candidates.length === 0) continue;
      const report = options.client.dryRun(recommendRequest(PROBE_ROW, category, basis, candidates, options.route, options.tags));
      const inputTokens = report.estimate.inputTokens;
      fullCandidateLists.push({ category, basis, candidates: candidates.length, inputTokens, contextWindowTokens, fits: inputTokens + report.estimate.maxOutputTokens <= contextWindowTokens });
    }
  }

  return {
    dryRun: true,
    route: options.route,
    model: probe.model,
    provider: probe.provider,
    shortlistSize: limit,
    shortlistMethod: shortlister.method,
    groups: { total: groups.length, memoryHits, needLlm: groups.length - memoryHits, withoutAssumedCategory },
    minimum,
    maximum,
    warnings: [...warnings],
    assumptions: [
      "분류가 예제의 정답 카테고리를 낸다고 가정하고 추천 프롬프트를 조립했다. 정답 카테고리가 없거나 AI 적용 범위 밖인 묶음은 분류 1회만 셌다.",
      "토큰은 글자 수 어림이고 비용은 입력 어림 + 출력 상한을 다 쓴 상한이다. 캐시 할인, 스키마 불일치 재시도, 대체 호출은 넣지 않았다.",
    ],
    fullCandidateLists,
    shortlistedPromptTokens: recommendTokens.length === 0 ? null : { min: Math.min(...recommendTokens), max: Math.max(...recommendTokens) },
  };
}
