import type { CallRequest, LlmResult, Tags } from "llm";
import type { Extraction, Fact } from "./facts.js";
import { inLabelScope, labelAnswerSchema, planUnits, renderCatalog, validateAnswers, type Catalog, type Label, type LabelAnswer, type LabelUnit, type RejectReason } from "./label.js";
import { reconcile, type Finding, type Pending, type UnverifiedReason } from "./match.js";
import { applyMemory, type MemoryEntry } from "./memory.js";
import { summarize, type Summary } from "./summary.js";
import type { UnitTable } from "./units.js";

// 끝까지 잇기: 사실 후보 → 확정 기억 → 뜻 붙이기(AI) → 답 확인 → 짝짓기·비교 → 요약.

/** 생성 모델 호출 포트. `llm` 의 `LlmClient` 가 그대로 맞는다. */
export interface LlmGateway {
  call<T = string>(request: CallRequest<T>): Promise<LlmResult<T>>;
}

export const LABEL_PROMPT = "reconcile/label@1";

export interface LabelSettings {
  maxTargetsPerCall: number;
  /** 답 하나의 출력 토큰 어림. 출력 상한과 dry-run 비용에 쓴다. 실측이 아니라 가정이다. */
  outputTokensPerLabel: number;
  outputTokensOverhead: number;
  /** 요청의 출력 상한 = 어림한 출력 × 이 배수 (모델 상한에서 자른다). 어림이 틀려도 답이 잘리지 않게 두는 여유다. */
  outputCapFactor: number;
  minConfidence: "low" | "medium" | "high";
  concurrency: number;
}

export interface DocInput {
  extraction: Extraction;
  /** 확정 기억을 찾을 때 쓰는 문서 종류. */
  docKind: string;
  defaultPeriod: string | null;
  /** 문장 속 숫자를 다룰 때 필요한 블록 글자. */
  blockText?: Map<string, string>;
}

export interface RunOptions {
  a: DocInput;
  b: DocInput;
  catalog: Catalog;
  units: UnitTable;
  memory: MemoryEntry[];
  settings: LabelSettings;
  includeSentences: boolean;
  gateway: LlmGateway;
  route: string;
  tags: Tags;
  /** 경로의 첫 모델이 받는 출력 토큰 상한. 요청 상한을 여기서 자른다. */
  modelMaxOutputTokens: number;
  /** 호출 하나의 시간 제한. 없으면 호출 계층의 기본값. */
  timeoutMs?: number;
  /** 주면 이 조건에 맞는 요청 단위만 모델에 보낸다 (작은 실행). 나머지 숫자는 미확인(`not_requested`)으로 남는다. */
  unitFilter?: (unit: LabelUnit) => boolean;
  /** 주면 요청 단위의 결과를 여기에 남기고, 이미 있는 결과는 모델을 부르지 않고 쓴다 (끊긴 실행을 이어 하기). */
  store?: UnitResultStore;
}

/**
 * 요청 단위의 결과를 남겨 두는 자리. 같은 요청(프롬프트, 표, 지표 목록, 경로, 출력 상한이 같은 것)의 결과가 있으면 돌려준다.
 * 남기는 것은 모델이 답한 결과(성공, 스키마 불일치, 잘림, 거부)다. 공급사 오류·시간 초과·상한 초과는 남기지 않아 이어 할 때 다시 부른다.
 */
export interface UnitResultStore<T = LabelAnswer> {
  get(unitId: string, request: CallRequest<T>): LlmResult<T> | undefined;
  put(unitId: string, request: CallRequest<T>, result: LlmResult<T>): void;
}

export interface LabelingReport {
  docId: string;
  inScope: number;
  fromMemory: number;
  units: number;
  /** 이 실행에서 모델을 부른 요청 단위 수. 저장된 결과를 쓴 것(`resumedUnits`)과 보내지 않은 것(`skippedUnits`)은 들어 있지 않다. */
  llmCalls: number;
  resumedUnits: number;
  skippedUnits: number;
  llmFailures: { unitId: string; kind: string }[];
  accepted: number;
  rejectedByReason: Partial<Record<RejectReason, number>>;
  unanswered: number;
  /** 이 실행에서 새로 쓴 비용. */
  costUsd: number;
  /** 저장된 결과를 쓴 요청 단위가 앞선 실행에서 쓴 비용. */
  resumedCostUsd: number;
}

export interface RunResult {
  findings: Finding[];
  summary: Summary;
  labeling: { a: LabelingReport; b: LabelingReport };
}

export function labelRequest(unit: LabelUnit, catalog: Catalog, options: Pick<RunOptions, "route" | "tags" | "settings" | "modelMaxOutputTokens" | "timeoutMs">): CallRequest<LabelAnswer> {
  const wanted = unit.targetFactIds.length * options.settings.outputTokensPerLabel + options.settings.outputTokensOverhead;
  return {
    prompt: LABEL_PROMPT,
    vars: { catalog: renderCatalog(catalog), unit: unit.text },
    route: options.route,
    tags: options.tags,
    schema: labelAnswerSchema,
    schemaName: "reconcile_labels",
    maxOutputTokens: Math.min(wanted * options.settings.outputCapFactor, options.modelMaxOutputTokens),
    ...(options.timeoutMs === undefined ? {} : { timeoutMs: options.timeoutMs }),
  };
}

function outOfScopeReason(fact: Fact): UnverifiedReason {
  if (fact.bucket === "table_cell") return "table_low_confidence";
  if (fact.bucket === "table_text_cell") return fact.tableStatus === "low_confidence" ? "table_low_confidence" : "out_of_scope_table_text_cell";
  if (fact.bucket === "sentence") return "out_of_scope_sentence";
  return "out_of_scope_number_only_block";
}

async function pool<T>(items: T[], size: number, work: (item: T) => Promise<void>): Promise<void> {
  let next = 0;
  const runners = Array.from({ length: Math.max(1, Math.min(size, items.length)) }, async () => {
    while (next < items.length) {
      const item = items[next] as T;
      next += 1;
      await work(item);
    }
  });
  await Promise.all(runners);
}

/** 문서 하나의 사실 후보에 뜻을 붙인다. 모든 값 후보가 `Pending` 하나로 나온다. */
export async function labelDocument(input: DocInput, options: RunOptions): Promise<{ pending: Pending[]; report: LabelingReport }> {
  const { extraction } = input;
  const scope = extraction.facts.filter((fact) => inLabelScope(fact, options));
  const labels = new Map<string, Label>();
  const failures = new Map<string, { reason: UnverifiedReason; detail: string | null }>();

  const remembered = applyMemory(scope.filter((fact) => fact.bucket === "table_cell"), input.docKind, options.memory);
  for (const label of remembered.labels) labels.set(label.factId, label);

  const rest = scope.filter((fact) => !labels.has(fact.id));
  const units = planUnits(extraction, rest, { maxTargetsPerCall: options.settings.maxTargetsPerCall, includeSentences: options.includeSentences }, input.blockText);
  const facts = new Map(extraction.facts.map((fact) => [fact.id, fact]));
  const report: LabelingReport = { docId: extraction.docId, inScope: scope.length, fromMemory: remembered.labels.length, units: units.length, llmCalls: 0, resumedUnits: 0, skippedUnits: 0, llmFailures: [], accepted: 0, rejectedByReason: {}, unanswered: 0, costUsd: 0, resumedCostUsd: 0 };

  await pool(units, options.settings.concurrency, async (unit) => {
    if (options.unitFilter && !options.unitFilter(unit)) {
      report.skippedUnits += 1;
      for (const id of unit.targetFactIds) failures.set(id, { reason: "not_requested", detail: null });
      return;
    }
    const request = labelRequest(unit, options.catalog, options);
    const stored = options.store?.get(unit.id, request);
    const result = stored ?? (await options.gateway.call(request));
    if (stored) {
      report.resumedUnits += 1;
      report.resumedCostUsd += stored.meta.totalCostUsd;
    } else {
      report.llmCalls += 1;
      report.costUsd += result.meta.totalCostUsd;
      options.store?.put(unit.id, request, result);
    }
    if (result.kind !== "success") {
      report.llmFailures.push({ unitId: unit.id, kind: result.kind });
      for (const id of unit.targetFactIds) failures.set(id, { reason: "llm_call_failed", detail: result.kind });
      return;
    }
    // AI 의 답은 여기를 지나야만 쓰인다.
    const validation = validateAnswers(unit, result.data, { catalog: options.catalog, facts });
    for (const label of validation.accepted) labels.set(label.factId, label);
    report.accepted += validation.accepted.length;
    report.unanswered += validation.unanswered.length;
    const reasons = new Map<string, RejectReason>();
    for (const rejected of validation.rejected) {
      report.rejectedByReason[rejected.reason] = (report.rejectedByReason[rejected.reason] ?? 0) + 1;
      if (rejected.factId) reasons.set(rejected.factId, rejected.reason);
    }
    for (const id of validation.unanswered) {
      const reason = reasons.get(id);
      failures.set(id, reason ? { reason: "answer_rejected", detail: reason } : { reason: "not_answered", detail: null });
    }
  });

  const inScope = new Set(scope.map((fact) => fact.id));
  const pending = extraction.facts.map((fact): Pending => {
    if (!inScope.has(fact.id)) return { fact, label: null, unverified: { reason: outOfScopeReason(fact), detail: null } };
    const label = labels.get(fact.id) ?? null;
    if (label) return { fact, label, unverified: null };
    return { fact, label: null, unverified: failures.get(fact.id) ?? { reason: "not_answered", detail: null } };
  });
  return { pending, report };
}

export async function runReconcile(options: RunOptions): Promise<RunResult> {
  const a = await labelDocument(options.a, options);
  const b = await labelDocument(options.b, options);
  const findings = reconcile(a.pending, b.pending, {
    catalog: options.catalog,
    units: options.units,
    minConfidence: options.settings.minConfidence,
    defaultPeriod: { a: options.a.defaultPeriod, b: options.b.defaultPeriod },
  });
  return { findings, summary: summarize(options.a.extraction, options.b.extraction, findings), labeling: { a: a.report, b: b.report } };
}
