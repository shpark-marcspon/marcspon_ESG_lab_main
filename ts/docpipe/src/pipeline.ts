import { FilePromptStore, parsePromptId, type LlmClient, type Tags, type TokenEstimator } from "llm";
import { chunkUnits, type Chunk } from "./chunk.js";
import { checkConsistency, pairsOf, type ConsistencyReport } from "./consistency.js";
import { checkCoverage, type CoverageReport } from "./coverage.js";
import type { ParsedDocument } from "./document.js";
import type { Glossary } from "./glossary.js";
import { GREENWASH_PROMPT, reviewChunk, selectClaimUnits, type Checklist, type ClaimSelection, type GreenwashOutput } from "./greenwash.js";
import { promptRoot } from "./paths.js";
import { runChunks, type Progress, type RunSummary } from "./runner.js";
import { planUnits, type UnitPlan } from "./select.js";
import type { Settings } from "./settings.js";
import type { ChunkRecord, ChunkStore } from "./store.js";
import { TRANSLATE_PROMPT, assembleTranslation, suspectCells, translateChunk, type SuspectCell, type TableNumberRule, type TranslateOutput, type TranslationResult } from "./translate.js";
import { loadUnitTable, type UnitTable } from "./units.js";

export const prompts = new FilePromptStore(promptRoot);

function templateHash(promptId: string): string {
  return prompts.get(parsePromptId(promptId)).hash;
}

export interface PlanInput {
  document: ParsedDocument;
  settings: Settings;
  estimator: TokenEstimator;
  route: string;
  /** 설정 파일의 조각 예산을 덮어쓴다. */
  budgetTokens?: number;
}

/** 표 셀 규칙. 주지 않으면 `data/docpipe/settings.json` 의 값(기본: 의심 표시 + 단위 셀 고정 번역)을 쓴다. */
export interface TranslateRules {
  tableNumbers: TableNumberRule;
  /** 단위 셀의 고정 번역 표. null 이면 단위 셀도 모델에 보낸다 (옛 동작). */
  units: UnitTable | null;
}

export function translateRulesFrom(settings: Settings, overrides: { tableNumbers?: TableNumberRule; unitCells?: "fixed" | "model"; unitTablePath?: string } = {}): TranslateRules {
  const unitCells = overrides.unitCells ?? settings.translate.unitCells;
  return { tableNumbers: overrides.tableNumbers ?? settings.translate.tableNumbers, units: unitCells === "fixed" ? loadUnitTable(overrides.unitTablePath) : null };
}

export interface TranslatePlan {
  units: UnitPlan;
  chunks: Chunk[];
  budgetTokens: number;
  rules: TranslateRules;
}

export function planTranslate(input: PlanInput & { glossary: Glossary; rules?: TranslateRules }): TranslatePlan {
  const rules = input.rules ?? translateRulesFrom(input.settings);
  const units = planUnits(input.document, { ...input.settings, units: rules.units });
  const budgetTokens = input.budgetTokens ?? input.settings.translate.chunkSourceTokenBudget;
  const chunks = chunkUnits(units.units, {
    budgetTokens,
    estimate: (text) => input.estimator.estimate(text),
    sections: input.document.sections,
    fingerprint: [TRANSLATE_PROMPT, templateHash(TRANSLATE_PROMPT), input.glossary.sha256, input.route].join("|"),
  });
  return { units, chunks, budgetTokens, rules };
}

export interface RunInput {
  client: LlmClient;
  tags: Tags;
  concurrency?: number;
  maxAttempts?: number;
  /** 앞에서부터 이 수만큼의 조각만 돌린다 (배선 확인용). 나머지는 저장된 기록이 있으면 그것을 쓰고 없으면 안 돈 조각으로 남는다. */
  maxChunks?: number;
  onProgress?: (progress: Progress) => void;
}

/** 이번에 돌린 조각의 기록에, 돌리지 않은 조각의 저장된 기록(없으면 undefined)을 이어 붙여 조각 순서대로 돌려준다. */
function recordsFor<O>(chunks: Chunk[], run: RunSummary<O>, store: ChunkStore<O>): (ChunkRecord<O> | undefined)[] {
  return chunks.map((chunk, index) => (index < run.records.length ? run.records[index] : store.read(chunk.id)));
}

export interface TranslateRun {
  plan: TranslatePlan;
  run: RunSummary<TranslateOutput>;
  result: TranslationResult;
  coverage: CoverageReport;
  consistency: ConsistencyReport;
  /** 숫자가 달라져 의심 표시가 붙은 표 셀. 사람이 훑어볼 목록이다 (`other` 등급이 앞). 합계와 분모는 `result.tableCells`. */
  suspects: SuspectCell[];
}

/** 번역 한 건: 분할 → 조각 실행(이어 하기) → 조립 → 누락·중복 검사 → 용어 일관성 검사. */
export async function runTranslate(input: PlanInput & RunInput & { glossary: Glossary; rules?: TranslateRules; store: ChunkStore<TranslateOutput> }): Promise<TranslateRun> {
  const plan = planTranslate(input);
  const { rules } = plan;
  const { document, glossary, client, route, tags } = input;
  const run = await runChunks({
    chunks: input.maxChunks === undefined ? plan.chunks : plan.chunks.slice(0, input.maxChunks),
    store: input.store,
    concurrency: input.concurrency ?? input.settings.runner.concurrency,
    maxAttempts: input.maxAttempts ?? input.settings.runner.maxAttempts,
    work: (chunk) => translateChunk(chunk, { client, route, tags, document, glossary, tableNumbers: rules.tableNumbers }),
    ...(input.onProgress ? { onProgress: input.onProgress } : {}),
  });
  const records = recordsFor(plan.chunks, run, input.store);
  const result = assembleTranslation(document, plan.units, plan.chunks, records, rules);
  return {
    plan,
    run,
    result,
    coverage: checkCoverage(document, result, plan.chunks, records),
    consistency: checkConsistency(glossary, pairsOf(plan.chunks, records)),
    suspects: suspectCells(result),
  };
}

export interface GreenwashPlan {
  selection: ClaimSelection;
  chunks: Chunk[];
  budgetTokens: number;
}

export function planGreenwash(input: PlanInput & { checklist: Checklist }): GreenwashPlan {
  const selection = selectClaimUnits(input.document, input.checklist, input.settings);
  const budgetTokens = input.budgetTokens ?? input.settings.greenwash.chunkSourceTokenBudget;
  const chunks = chunkUnits(selection.units, {
    budgetTokens,
    estimate: (text) => input.estimator.estimate(text),
    sections: input.document.sections,
    fingerprint: [GREENWASH_PROMPT, templateHash(GREENWASH_PROMPT), input.checklist.sha256, input.route].join("|"),
  });
  return { selection, chunks, budgetTokens };
}

export interface GreenwashRun {
  plan: GreenwashPlan;
  run: RunSummary<GreenwashOutput>;
}

export async function runGreenwash(input: PlanInput & RunInput & { checklist: Checklist; store: ChunkStore<GreenwashOutput> }): Promise<GreenwashRun> {
  const plan = planGreenwash(input);
  const { document, checklist, client, route, tags } = input;
  const run = await runChunks({
    chunks: input.maxChunks === undefined ? plan.chunks : plan.chunks.slice(0, input.maxChunks),
    store: input.store,
    concurrency: input.concurrency ?? input.settings.runner.concurrency,
    maxAttempts: input.maxAttempts ?? input.settings.runner.maxAttempts,
    work: (chunk) => reviewChunk(chunk, { client, route, tags, document, checklist }),
    ...(input.onProgress ? { onProgress: input.onProgress } : {}),
  });
  return { plan, run };
}
