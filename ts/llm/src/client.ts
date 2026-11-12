import { randomUUID } from "node:crypto";
import { z } from "zod";
import { BudgetGuard } from "./budget.js";
import { ConfigError, resolveRoute, type LlmConfig, type ModelConfig } from "./config.js";
import { HeuristicTokenEstimator, computeCostUsd, roundUsd, type TokenEstimator } from "./cost.js";
import { sha256, type CallLogSink } from "./log.js";
import { parsePromptId, renderPrompt, type PromptStore, type PromptTemplate, type RenderedPrompt } from "./prompts.js";
import { DEFAULT_PROVIDER_RETRY, NO_RETRY, retryDelayMs, validateRetryPolicies, type RetryPolicy } from "./retry.js";
import type {
  Attempt,
  AttemptKind,
  CallMeta,
  CallRequest,
  DryRunReport,
  LlmResult,
  LlmResultKind,
  LlmWarning,
  Provider,
  ProviderRequest,
  ProviderResponse,
  SchemaIssue,
  Usage,
} from "./types.js";

/**
 * 다음 모델로 넘어가는 실패 종류. 공급사 쪽 장애만 대체한다.
 * 스키마 불일치, 거부, 출력 잘림은 다른 공급사로 가도 같은 입력이라 대체하지 않는다.
 * `provider_error` 는 `retryable` 일 때만(한도 초과, 5xx, 연결 실패) 대체한다. 잘못된 요청·인증 실패·
 * 클라이언트 미주입 같은 재시도 불가 오류는 설정이나 요청의 문제라 덮지 않고 그대로 돌려준다.
 * 순서: 재시도 가능한 공급사 오류는 먼저 같은 모델을 다시 부르고(`providerRetry`, 설정된 공급사만), 그래도 실패하면 다음 모델로 넘어간다.
 */
export const FALLBACK_ON: ReadonlySet<LlmResultKind> = new Set<LlmResultKind>(["provider_error", "timeout"]);

const DEFAULT_MAX_OUTPUT_TOKENS = 4096;
const DEFAULT_TIMEOUT_MS = 120_000;
const DEFAULT_SCHEMA_RETRIES = 2;

export interface LlmClientOptions {
  config: LlmConfig;
  /** 공급사 이름 → 어댑터. 설정 파일의 `provider` 값과 맞춘다. */
  providers: Record<string, Provider>;
  prompts: PromptStore;
  log: CallLogSink;
  budget?: BudgetGuard;
  estimator?: TokenEstimator;
  /** 호출 기록에 요청 본문과 응답 글자를 그대로 남길지. 기본은 남기지 않고 해시만 남긴다. */
  storeBodies?: boolean;
  now?: () => number;
  /**
   * 공급사 이름 → 재시도 가능한 공급사 오류에서 같은 모델을 다시 부르는 설정. 기본은 `DEFAULT_PROVIDER_RETRY`.
   * 주면 기본을 통째로 바꾼다 (`{}` 는 어느 공급사도 다시 부르지 않는다).
   */
  providerRetry?: Readonly<Record<string, RetryPolicy>>;
  /** 재시도 전의 대기. 테스트는 실제로 기다리지 않는 것을 넣는다. */
  sleep?: (ms: number) => Promise<void>;
}

/** 같은 모델을 다시 부른 시도에 붙는 기록. */
interface RetryTrace {
  providerRetry: number;
  previousErrorStatus: number | null;
  retryWaitMs: number;
}

interface Prepared {
  template: PromptTemplate;
  rendered: RenderedPrompt;
  models: ModelConfig[];
}

type Validated<T> = { ok: true; data: T } | { ok: false; issues: SchemaIssue[] };

export class LlmClient {
  private readonly budget: BudgetGuard;
  private readonly estimator: TokenEstimator;
  private readonly now: () => number;
  private readonly providerRetry: Readonly<Record<string, RetryPolicy>>;
  private readonly sleep: (ms: number) => Promise<void>;

  constructor(private readonly options: LlmClientOptions) {
    this.budget = options.budget ?? new BudgetGuard();
    this.estimator = options.estimator ?? new HeuristicTokenEstimator(options.config.tokenEstimate);
    this.now = options.now ?? Date.now;
    this.providerRetry = options.providerRetry ?? DEFAULT_PROVIDER_RETRY;
    validateRetryPolicies(this.providerRetry);
    this.sleep = options.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  }

  async call<T = string>(request: CallRequest<T>): Promise<LlmResult<T>> {
    const { template, rendered, models } = this.prepare(request);
    const callId = randomUUID();
    const attempts: Attempt[] = [];
    const warnings: LlmWarning[] = [];
    const meta = (): CallMeta => ({
      callId,
      promptId: template.id,
      attempts,
      totalCostUsd: roundUsd(attempts.reduce((sum, attempt) => sum + (attempt.costUsd ?? 0), 0)),
      warnings,
    });
    const schemaRetries = request.maxSchemaRetries ?? DEFAULT_SCHEMA_RETRIES;
    if (!Number.isInteger(schemaRetries) || schemaRetries < 0) {
      throw new ConfigError(`maxSchemaRetries 는 0 이상의 정수여야 한다: ${schemaRetries}`);
    }
    const timeoutMs = request.timeoutMs ?? DEFAULT_TIMEOUT_MS;

    let failure: LlmResult<T> | undefined;
    for (const model of models) {
      const provider = this.provider(model);
      const providerRequest = this.buildProviderRequest(request, rendered, model);
      const requestHash = sha256(providerRequest);
      addWarnings(warnings, this.cacheWarnings(rendered, model));

      let mismatch: { issues: SchemaIssue[]; rawText: string } | undefined;
      failure = undefined;
      const retryPolicy = this.providerRetry[model.provider] ?? NO_RETRY;
      for (let round = 0; round <= schemaRetries; round++) {
        const logBase = { callId, template, requestHash, tags: request.tags, providerRequest };

        // 공급사 호출 한 번과, 재시도 가능한 공급사 오류일 때 같은 모델을 다시 부르는 고리.
        // 다시 부를 때마다 상한을 다시 보고, 공급사 어댑터(호출 간격 조절 포함)를 그대로 지나고, 호출 기록을 한 줄 남긴다.
        let response: ProviderResponse;
        let validated: Validated<T> | undefined;
        let retry: RetryTrace | undefined;
        for (;;) {
          const verdict = this.budget.check(request.tags);
          if (!verdict.allowed && verdict.limit && verdict.spentUsd !== undefined) {
            this.writeLog({ ...logBase, attempt: attempts.length, model: null, kind: "budget_exceeded", usage: null, costUsd: null, durationMs: 0, warnings, responseText: null, responseModelId: null, ...(retry ? { retry } : {}) });
            return { kind: "budget_exceeded", limitUsd: verdict.limit.limitUsd, spentUsd: verdict.spentUsd, scope: verdict.limit.scope, meta: meta() };
          }

          const started = this.now();
          response = await invokeWithTimeout(provider, providerRequest, timeoutMs);
          const durationMs = this.now() - started;

          const usage = "usage" in response ? response.usage : null;
          const costUsd = usage ? computeCostUsd(usage, model.pricing) : null;
          if (costUsd !== null) this.budget.record(request.tags, costUsd);
          if (usage) addWarnings(warnings, usageWarnings(rendered, usage));

          let kind: AttemptKind;
          validated = undefined;
          if (response.kind === "completed") {
            validated = validate(request.schema, response.text);
            kind = validated.ok ? "success" : "schema_mismatch";
          } else {
            kind = response.kind === "error" ? "provider_error" : response.kind;
          }
          attempts.push({ provider: provider.name, model: model.key, kind, usage, costUsd, durationMs, ...(retry ? { providerRetry: retry.providerRetry } : {}) });
          this.writeLog({ ...logBase, attempt: attempts.length - 1, model, kind, usage, costUsd, durationMs, warnings, responseText: "text" in response ? response.text : null, responseModelId: ("modelId" in response ? response.modelId : undefined) ?? null, ...(response.kind === "error" ? { errorStatus: response.status, ...(response.retryAfterMs === undefined ? {} : { retryAfterMs: response.retryAfterMs }) } : {}), ...(retry ? { retry } : {}) });

          if (response.kind !== "error" || !response.retryable) break;
          const done = retry?.providerRetry ?? 0;
          const waitMs = retryDelayMs(retryPolicy, done, response.retryAfterMs);
          if (waitMs === null) break;
          await this.sleep(waitMs);
          retry = { providerRetry: done + 1, previousErrorStatus: response.status, retryWaitMs: waitMs };
        }

        if (response.kind === "completed" && validated) {
          if (validated.ok) return { kind: "success", data: validated.data, text: response.text, meta: meta() };
          mismatch = { issues: validated.issues, rawText: response.text };
          continue;
        }
        if (response.kind === "refusal") return { kind: "refusal", text: response.text, category: response.category, meta: meta() };
        if (response.kind === "truncated") return { kind: "truncated", partialText: response.text, meta: meta() };
        if (response.kind === "error") {
          failure = { kind: "provider_error", status: response.status, retryable: response.retryable, message: response.message, meta: meta() };
        } else if (response.kind === "timeout") {
          failure = { kind: "timeout", message: response.message, meta: meta() };
        }
        break;
      }

      if (!failure) {
        if (!mismatch) throw new Error("도달할 수 없는 상태: 실패도 불일치도 없이 재시도가 끝났다");
        return { kind: "schema_mismatch", issues: mismatch.issues, rawText: mismatch.rawText, meta: meta() };
      }
      if (!shouldFallback(failure)) return failure;
    }
    if (!failure) throw new ConfigError(`경로 ${request.route} 에 모델이 없다`);
    // 마지막 모델까지 실패했다. meta 는 모든 시도를 담도록 다시 만든다.
    return { ...failure, meta: meta() };
  }

  /** 공급사를 부르지 않고 조립된 요청과 어림 토큰·비용만 돌려준다. */
  dryRun<T>(request: CallRequest<T>): DryRunReport {
    const { template, rendered, models } = this.prepare(request);
    const model = models[0] as ModelConfig;
    const provider = this.provider(model);
    const providerRequest = this.buildProviderRequest(request, rendered, model);

    const cacheableTokens = this.cacheableTokens(rendered);
    const inputTokens = this.estimator.estimate([...rendered.system.map((segment) => segment.text), rendered.user].join("\n"));
    const upperBound: Usage = { inputTokens, outputTokens: providerRequest.maxOutputTokens, cacheReadTokens: 0, cacheWriteTokens: 0 };

    return {
      dryRun: true,
      promptId: template.id,
      provider: provider.name,
      model: model.key,
      request: provider.buildRequest(providerRequest),
      estimate: {
        inputTokens,
        cacheableTokens,
        maxOutputTokens: providerRequest.maxOutputTokens,
        costUpperBoundUsd: computeCostUsd(upperBound, model.pricing),
        unestimatedParts: (request.attachments ?? []).map((part) => part.type),
      },
      fallbackModels: models.slice(1).map((fallback) => fallback.key),
      warnings: this.cacheWarnings(rendered, model),
    };
  }

  private prepare<T>(request: CallRequest<T>): Prepared {
    const template = this.options.prompts.get(parsePromptId(request.prompt));
    const rendered = renderPrompt(template, request.vars);
    const models = resolveRoute(this.options.config, request.route);
    return { template, rendered, models };
  }

  private provider(model: ModelConfig): Provider {
    const provider = this.options.providers[model.provider];
    if (!provider) throw new ConfigError(`모델 ${model.key} 의 공급사 ${model.provider} 어댑터가 등록되지 않았다`);
    return provider;
  }

  private buildProviderRequest<T>(request: CallRequest<T>, rendered: RenderedPrompt, model: ModelConfig): ProviderRequest {
    const answerTokens = request.maxOutputTokens ?? Math.min(DEFAULT_MAX_OUTPUT_TOKENS, model.maxOutputTokens);
    if (answerTokens > model.maxOutputTokens) {
      throw new ConfigError(`maxOutputTokens ${answerTokens} 이 모델 ${model.key} 의 상한 ${model.maxOutputTokens} 을 넘는다`);
    }
    // 요청의 상한은 답의 상한이다. 추론 토큰이 출력 상한을 같이 쓰는 모델에는 설정의 여유분을 더해 보낸다 (모델 상한을 넘기지는 않는다).
    const maxOutputTokens = Math.min(model.maxOutputTokens, answerTokens + (model.reasoningOutputReserveTokens ?? 0));
    const providerRequest: ProviderRequest = {
      apiModelId: model.apiModelId,
      system: rendered.system,
      user: [...(request.attachments ?? []), { type: "text", text: rendered.user }],
      maxOutputTokens,
    };
    if (request.schema && model.structuredOutput) {
      const { $schema: _, ...schema } = z.toJSONSchema(request.schema) as Record<string, unknown>;
      providerRequest.jsonSchema = { name: request.schemaName ?? "output", schema };
    }
    return providerRequest;
  }

  private cacheableTokens(rendered: RenderedPrompt): number {
    const cacheable = rendered.system.filter((segment) => segment.cache).map((segment) => segment.text);
    return cacheable.length === 0 ? 0 : this.estimator.estimate(cacheable.join("\n"));
  }

  private cacheWarnings(rendered: RenderedPrompt, model: ModelConfig): LlmWarning[] {
    if (!rendered.system.some((segment) => segment.cache)) return [];
    const tokens = this.cacheableTokens(rendered);
    if (tokens >= model.minCacheTokens) return [];
    return [
      {
        code: "cache_below_minimum",
        message: `캐시 대상 앞부분이 약 ${tokens}토큰(어림)으로 모델 ${model.key} 의 최소 ${model.minCacheTokens}토큰에 못 미친다. 캐시가 걸리지 않는다.`,
      },
    ];
  }

  private writeLog(entry: {
    callId: string;
    template: PromptTemplate;
    requestHash: string;
    tags: { tenant: string; module: string };
    providerRequest: ProviderRequest;
    attempt: number;
    model: ModelConfig | null;
    kind: LlmResultKind;
    usage: Usage | null;
    costUsd: number | null;
    durationMs: number;
    warnings: LlmWarning[];
    responseText: string | null;
    responseModelId: string | null;
    errorStatus?: number | null;
    retryAfterMs?: number;
    retry?: RetryTrace;
  }): void {
    this.options.log.write({
      ts: new Date(this.now()).toISOString(),
      callId: entry.callId,
      attempt: entry.attempt,
      provider: entry.model?.provider ?? null,
      model: entry.model?.key ?? null,
      apiModelId: entry.model?.apiModelId ?? null,
      responseModelId: entry.responseModelId,
      promptId: entry.template.id,
      promptVersion: entry.template.version,
      templateHash: entry.template.hash,
      requestHash: entry.requestHash,
      inputTokens: entry.usage?.inputTokens ?? null,
      outputTokens: entry.usage?.outputTokens ?? null,
      cacheReadTokens: entry.usage?.cacheReadTokens ?? null,
      cacheWriteTokens: entry.usage?.cacheWriteTokens ?? null,
      ...(entry.usage?.thinkingTokens === undefined ? {} : { thinkingTokens: entry.usage.thinkingTokens }),
      ...(entry.errorStatus === undefined ? {} : { errorStatus: entry.errorStatus }),
      ...(entry.retryAfterMs === undefined ? {} : { retryAfterMs: entry.retryAfterMs }),
      ...(entry.retry ?? {}),
      costUsd: entry.costUsd,
      durationMs: entry.durationMs,
      resultKind: entry.kind,
      tenant: entry.tags.tenant,
      module: entry.tags.module,
      warnings: entry.warnings.map((warning) => warning.code),
      ...(this.options.storeBodies ? { body: { request: entry.providerRequest, responseText: entry.responseText } } : {}),
    });
  }
}

function shouldFallback(failure: LlmResult<unknown>): boolean {
  if (!FALLBACK_ON.has(failure.kind)) return false;
  return failure.kind !== "provider_error" || failure.retryable;
}

function addWarnings(target: LlmWarning[], incoming: LlmWarning[]): void {
  for (const warning of incoming) {
    if (!target.some((existing) => existing.code === warning.code && existing.message === warning.message)) target.push(warning);
  }
}

function usageWarnings(rendered: RenderedPrompt, usage: Usage): LlmWarning[] {
  if (!rendered.system.some((segment) => segment.cache)) return [];
  if (usage.cacheReadTokens === null || usage.cacheWriteTokens === null) {
    return [{ code: "usage_unreported", message: "공급사가 캐시 토큰 수를 보고하지 않았다. 비용은 캐시 토큰을 0 으로 두고 계산했다." }];
  }
  if (usage.cacheReadTokens + usage.cacheWriteTokens === 0) {
    return [{ code: "cache_not_applied", message: "캐시 대상으로 표시했지만 응답의 캐시 읽기·쓰기 토큰이 0 이다." }];
  }
  return [];
}

function validate<T>(schema: z.ZodType<T> | undefined, text: string): Validated<T> {
  // 스키마가 없는 호출의 T 는 기본값 string 이다.
  if (!schema) return { ok: true, data: text as T };
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    return { ok: false, issues: [{ path: "", message: `JSON 으로 읽을 수 없다: ${(error as Error).message}` }] };
  }
  const result = schema.safeParse(parsed);
  if (result.success) return { ok: true, data: result.data };
  return { ok: false, issues: result.error.issues.map((issue) => ({ path: issue.path.join("."), message: issue.message })) };
}

async function invokeWithTimeout(provider: Provider, request: ProviderRequest, timeoutMs: number): Promise<ProviderResponse> {
  const controller = new AbortController();
  let timer: NodeJS.Timeout | undefined;
  const timeout = new Promise<ProviderResponse>((resolve) => {
    timer = setTimeout(() => {
      controller.abort();
      resolve({ kind: "timeout", message: `${timeoutMs}ms 안에 응답이 없었다` });
    }, timeoutMs);
  });
  try {
    return await Promise.race([provider.invoke(request, { signal: controller.signal, timeoutMs }), timeout]);
  } catch (error) {
    // 어댑터는 던지지 않기로 했지만, 던졌다면 삼키지 않고 공급사 오류로 돌린다.
    return { kind: "error", status: null, retryable: false, message: `어댑터가 예외를 던졌다: ${(error as Error).message}` };
  } finally {
    clearTimeout(timer);
  }
}
