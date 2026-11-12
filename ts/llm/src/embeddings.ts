import { randomUUID } from "node:crypto";
import { BudgetGuard } from "./budget.js";
import { ConfigError, resolveEmbeddingRoute, type EmbeddingModelConfig, type LlmConfig } from "./config.js";
import { roundUsd } from "./cost.js";
import { sha256, type CallLogSink } from "./log.js";
import type { InvokeOptions, Tags } from "./types.js";

const PER_MTOK = 1_000_000;
const DEFAULT_TIMEOUT_MS = 120_000;

/** 호출 기록의 `promptId` 자리에 적는 값. 임베딩 호출에는 프롬프트 템플릿이 없다. */
export const EMBEDDING_PROMPT_ID = "embedding";

/** 공급사에 넘기는 중립 임베딩 요청. */
export interface EmbeddingProviderRequest {
  apiModelId: string;
  inputs: string[];
}

/** `usage` 는 공급사가 보고한 입력 토큰 수다. 벡터는 입력 순서를 따른다. */
export type EmbeddingProviderResponse =
  | { kind: "completed"; vectors: number[][]; usage: { inputTokens: number }; modelId?: string }
  | { kind: "error"; status: number | null; retryable: boolean; message: string }
  | { kind: "timeout"; message: string };

export interface EmbeddingProvider {
  readonly name: string;
  /** 실패를 던지지 않고 `EmbeddingProviderResponse` 로 돌려준다. */
  embed(request: EmbeddingProviderRequest, options: InvokeOptions): Promise<EmbeddingProviderResponse>;
}

export interface EmbedRequest {
  /** 설정 파일의 임베딩 경로 이름. 호출하는 쪽은 공급사와 모델 이름을 모른다. */
  route: string;
  inputs: string[];
  tags: Tags;
  timeoutMs?: number;
}

export interface EmbedMeta {
  callId: string;
  /** 설정 파일의 모델 키. */
  model: string;
  provider: string;
  dimensions: number;
  /** 공급사를 부른 횟수 (입력이 많으면 여러 요청으로 나뉜다). */
  requests: number;
  inputTokens: number;
  totalCostUsd: number;
}

/** 임베딩 결과. 실패를 빈 벡터로 삼키지 않고 종류별로 나눈다. 일부 요청만 성공해도 전체가 실패다 (쓴 비용은 `meta` 에 남는다). */
export type EmbedResult =
  | { kind: "success"; vectors: number[][]; meta: EmbedMeta }
  | { kind: "provider_error"; status: number | null; retryable: boolean; message: string; meta: EmbedMeta }
  | { kind: "timeout"; message: string; meta: EmbedMeta }
  | { kind: "budget_exceeded"; limitUsd: number; spentUsd: number; scope: Partial<Tags>; meta: EmbedMeta };

export interface EmbeddingClientOptions {
  config: LlmConfig;
  /** 공급사 이름 → 임베딩 어댑터. */
  providers: Record<string, EmbeddingProvider>;
  log: CallLogSink;
  /** 생성 호출과 같은 상한을 타게 하려면 `LlmClient` 와 같은 `BudgetGuard` 를 넘긴다. */
  budget?: BudgetGuard;
  /** 호출 기록에 입력 글자를 그대로 남길지. 기본은 해시만 남긴다. 벡터는 남기지 않는다. */
  storeBodies?: boolean;
  now?: () => number;
}

/** 입력 토큰 수와 단가(USD / 100만 토큰)로 임베딩 비용을 계산한다. */
export function computeEmbeddingCostUsd(inputTokens: number, model: Pick<EmbeddingModelConfig, "pricing">): number {
  return roundUsd((inputTokens * model.pricing.input) / PER_MTOK);
}

/**
 * 입력을 요청 단위로 나눈다. 한 요청은 입력 수 한도와 토큰 합 한도를 넘지 않는다.
 * 토큰 수는 미리 알 수 없어 글자 수를 상한으로 쓴다 (1글자가 1토큰을 넘는 경우는 드물다).
 */
export function batchInputs(inputs: readonly string[], model: Pick<EmbeddingModelConfig, "maxInputsPerRequest" | "maxTokensPerRequest">): string[][] {
  const batches: string[][] = [];
  let current: string[] = [];
  let chars = 0;
  for (const input of inputs) {
    if (current.length > 0 && (current.length >= model.maxInputsPerRequest || chars + input.length > model.maxTokensPerRequest)) {
      batches.push(current);
      current = [];
      chars = 0;
    }
    current.push(input);
    chars += input.length;
  }
  if (current.length > 0) batches.push(current);
  return batches;
}

/** 공급사에 묶이지 않는 임베딩 호출. 호출 기록, 비용, 상한을 생성 호출과 같은 방식으로 탄다. */
export class EmbeddingClient {
  private readonly budget: BudgetGuard;
  private readonly now: () => number;

  constructor(private readonly options: EmbeddingClientOptions) {
    this.budget = options.budget ?? new BudgetGuard();
    this.now = options.now ?? Date.now;
  }

  /** 경로가 가리키는 모델의 설정. 색인을 만들거나 읽는 쪽이 모델 키와 차원을 대조할 때 쓴다. */
  model(route: string): EmbeddingModelConfig {
    return resolveEmbeddingRoute(this.options.config, route);
  }

  async embed(request: EmbedRequest): Promise<EmbedResult> {
    const model = this.model(request.route);
    const provider = this.options.providers[model.provider];
    if (!provider) throw new ConfigError(`임베딩 모델 ${model.key} 의 공급사 ${model.provider} 어댑터가 등록되지 않았다`);
    if (request.inputs.some((input) => input === "")) throw new ConfigError("빈 문자열은 임베딩할 수 없다");

    const callId = randomUUID();
    const timeoutMs = request.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    const vectors: number[][] = [];
    let requests = 0;
    let inputTokens = 0;
    let totalCostUsd = 0;
    const meta = (): EmbedMeta => ({ callId, model: model.key, provider: provider.name, dimensions: model.dimensions, requests, inputTokens, totalCostUsd: roundUsd(totalCostUsd) });

    for (const batch of batchInputs(request.inputs, model)) {
      const providerRequest: EmbeddingProviderRequest = { apiModelId: model.apiModelId, inputs: batch };
      const base = { callId, attempt: requests, model, tags: request.tags, providerRequest };

      const verdict = this.budget.check(request.tags);
      if (!verdict.allowed && verdict.limit && verdict.spentUsd !== undefined) {
        this.writeLog({ ...base, logModel: false, kind: "budget_exceeded", inputTokens: null, costUsd: null, durationMs: 0, responseModelId: null });
        return { kind: "budget_exceeded", limitUsd: verdict.limit.limitUsd, spentUsd: verdict.spentUsd, scope: verdict.limit.scope, meta: meta() };
      }

      const started = this.now();
      let response = await invokeWithTimeout(provider, providerRequest, timeoutMs);
      const durationMs = this.now() - started;
      requests += 1;

      let costUsd: number | null = null;
      if (response.kind === "completed") {
        costUsd = computeEmbeddingCostUsd(response.usage.inputTokens, model);
        this.budget.record(request.tags, costUsd);
        inputTokens += response.usage.inputTokens;
        totalCostUsd += costUsd;
        const tokens = response.usage.inputTokens;
        const bad = invalidShape(response.vectors, batch.length, model.dimensions);
        // 모양이 틀린 응답도 과금은 됐다. 비용은 남기고 결과는 오류로 돌린다.
        if (bad) response = { kind: "error", status: null, retryable: false, message: bad };
        this.writeLog({ ...base, logModel: true, kind: bad ? "provider_error" : "success", inputTokens: tokens, costUsd, durationMs, responseModelId: ("modelId" in response ? response.modelId : undefined) ?? null });
      } else {
        this.writeLog({ ...base, logModel: true, kind: response.kind === "timeout" ? "timeout" : "provider_error", inputTokens: null, costUsd: null, durationMs, responseModelId: null });
      }

      if (response.kind === "error") return { kind: "provider_error", status: response.status, retryable: response.retryable, message: response.message, meta: meta() };
      if (response.kind === "timeout") return { kind: "timeout", message: response.message, meta: meta() };
      vectors.push(...response.vectors);
    }
    return { kind: "success", vectors, meta: meta() };
  }

  private writeLog(entry: {
    callId: string;
    attempt: number;
    model: EmbeddingModelConfig;
    logModel: boolean;
    tags: Tags;
    providerRequest: EmbeddingProviderRequest;
    kind: "success" | "provider_error" | "timeout" | "budget_exceeded";
    inputTokens: number | null;
    costUsd: number | null;
    durationMs: number;
    responseModelId: string | null;
  }): void {
    this.options.log.write({
      ts: new Date(this.now()).toISOString(),
      callId: entry.callId,
      attempt: entry.attempt,
      provider: entry.logModel ? entry.model.provider : null,
      model: entry.logModel ? entry.model.key : null,
      apiModelId: entry.logModel ? entry.model.apiModelId : null,
      responseModelId: entry.responseModelId,
      promptId: EMBEDDING_PROMPT_ID,
      promptVersion: 0,
      templateHash: "",
      requestHash: sha256(entry.providerRequest),
      inputTokens: entry.inputTokens,
      outputTokens: entry.inputTokens === null ? null : 0,
      cacheReadTokens: null,
      cacheWriteTokens: null,
      costUsd: entry.costUsd,
      durationMs: entry.durationMs,
      resultKind: entry.kind,
      tenant: entry.tags.tenant,
      module: entry.tags.module,
      warnings: [],
      embeddingInputs: entry.providerRequest.inputs.length,
      ...(this.options.storeBodies ? { body: { request: entry.providerRequest, responseText: null } } : {}),
    });
  }
}

function invalidShape(vectors: number[][], count: number, dimensions: number): string | null {
  if (vectors.length !== count) return `임베딩 응답의 벡터 수가 입력 수와 다르다: ${vectors.length} != ${count}`;
  const wrong = vectors.find((vector) => vector.length !== dimensions);
  if (wrong) return `임베딩 응답의 차원이 설정과 다르다: ${wrong.length} != ${dimensions}`;
  if (vectors.some((vector) => vector.some((value) => !Number.isFinite(value)))) return "임베딩 응답에 유한하지 않은 값이 있다";
  return null;
}

async function invokeWithTimeout(provider: EmbeddingProvider, request: EmbeddingProviderRequest, timeoutMs: number): Promise<EmbeddingProviderResponse> {
  const controller = new AbortController();
  let timer: NodeJS.Timeout | undefined;
  const timeout = new Promise<EmbeddingProviderResponse>((resolve) => {
    timer = setTimeout(() => {
      controller.abort();
      resolve({ kind: "timeout", message: `${timeoutMs}ms 안에 응답이 없었다` });
    }, timeoutMs);
  });
  try {
    return await Promise.race([provider.embed(request, { signal: controller.signal, timeoutMs }), timeout]);
  } catch (error) {
    return { kind: "error", status: null, retryable: false, message: `어댑터가 예외를 던졌다: ${(error as Error).message}` };
  } finally {
    clearTimeout(timer);
  }
}
