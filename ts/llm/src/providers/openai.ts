import OpenAI from "openai";
import type { EmbeddingProvider, EmbeddingProviderRequest, EmbeddingProviderResponse } from "../embeddings.js";
import { redactKeys } from "./redact.js";
import type { InvokeOptions, Provider, ProviderRequest, ProviderResponse, Usage } from "../types.js";

type ResponseCreateParams = OpenAI.Responses.ResponseCreateParamsNonStreaming;
type Response = OpenAI.Responses.Response;

/** 어댑터가 쓰는 SDK 표면. 실제 `OpenAI` 클라이언트가 이 모양을 만족하고, 테스트는 가짜를 넣는다. */
export interface OpenAILike {
  responses: {
    create(body: ResponseCreateParams, options?: { signal?: AbortSignal; timeout?: number }): PromiseLike<Response>;
  };
}

/**
 * 키를 인자로 받아 SDK 클라이언트를 만든다. 환경 변수를 읽지 않는다. 키를 어디서 가져올지는 CLI 조립 지점이 정한다.
 * SDK 의 자체 재시도(기본 2회, 429·5xx·연결 실패)는 그대로 둔다.
 */
export function createOpenAIClient(apiKey: string): OpenAILike {
  if (apiKey.trim() === "") throw new Error("OpenAI 키가 비었다");
  return new OpenAI({ apiKey });
}

/** 임베딩 어댑터가 쓰는 SDK 표면. */
export interface OpenAIEmbeddingsLike {
  embeddings: {
    create(body: OpenAI.EmbeddingCreateParams, options?: { signal?: AbortSignal; timeout?: number }): PromiseLike<OpenAI.CreateEmbeddingResponse>;
  };
}

/** 키를 인자로 받아 임베딩용 SDK 클라이언트를 만든다. 환경 변수를 읽지 않는다. */
export function createOpenAIEmbeddingsClient(apiKey: string): OpenAIEmbeddingsLike {
  if (apiKey.trim() === "") throw new Error("OpenAI 키가 비었다");
  return new OpenAI({ apiKey });
}

/** OpenAI Embeddings API 어댑터. 클라이언트는 밖에서 만들어 넣는다. */
export class OpenAIEmbeddingProvider implements EmbeddingProvider {
  readonly name = "openai";

  constructor(private readonly client?: OpenAIEmbeddingsLike) {}

  buildRequest(request: EmbeddingProviderRequest): OpenAI.EmbeddingCreateParams {
    return { model: request.apiModelId, input: request.inputs, encoding_format: "float" };
  }

  async embed(request: EmbeddingProviderRequest, options: InvokeOptions): Promise<EmbeddingProviderResponse> {
    if (!this.client) return { kind: "error", status: null, retryable: false, message: "OpenAI 클라이언트가 주입되지 않았다" };
    let response: OpenAI.CreateEmbeddingResponse;
    try {
      response = await this.client.embeddings.create(this.buildRequest(request), { signal: options.signal, timeout: options.timeoutMs });
    } catch (error) {
      const mapped = mapError(error);
      return mapped.kind === "timeout" || mapped.kind === "error" ? mapped : { kind: "error", status: null, retryable: false, message: "예상하지 못한 오류 종류" };
    }
    if (!response.usage) return { kind: "error", status: null, retryable: false, message: "임베딩 응답에 usage 가 없다" };
    // 응답의 index 로 입력 순서를 되찾는다.
    const vectors: number[][] = [];
    for (const item of response.data) vectors[item.index] = item.embedding;
    return { kind: "completed", vectors, usage: { inputTokens: response.usage.prompt_tokens }, modelId: response.model };
  }
}

/**
 * OpenAI Responses API 어댑터. 클라이언트는 밖에서 만들어 넣는다(키를 여기서 찾지 않는다).
 * 캐싱은 공급사가 앞부분 일치로 자동 적용하므로 표시할 것이 없고, 고정 지침을 앞에 두는 것으로 충분하다.
 */
export class OpenAIProvider implements Provider {
  readonly name = "openai";

  constructor(private readonly client?: OpenAILike) {}

  buildRequest(request: ProviderRequest): ResponseCreateParams {
    const content: OpenAI.Responses.ResponseInputContent[] = request.user.map((part) => {
      if (part.type === "text") return { type: "input_text", text: part.text };
      if (part.type === "pdf") {
        return { type: "input_file", filename: part.filename, file_data: `data:application/pdf;base64,${part.base64}` };
      }
      return { type: "input_image", detail: "auto", image_url: `data:${part.mediaType};base64,${part.base64}` };
    });

    const params: ResponseCreateParams = {
      model: request.apiModelId,
      max_output_tokens: request.maxOutputTokens,
      input: [{ role: "user", content }],
      store: false,
    };
    if (request.system.length > 0) params.instructions = request.system.map((segment) => segment.text).join("\n\n");
    if (request.jsonSchema) {
      params.text = { format: { type: "json_schema", name: request.jsonSchema.name, schema: request.jsonSchema.schema, strict: true } };
    }
    return params;
  }

  async invoke(request: ProviderRequest, options: InvokeOptions): Promise<ProviderResponse> {
    if (!this.client) return { kind: "error", status: null, retryable: false, message: "OpenAI 클라이언트가 주입되지 않았다" };
    let response: Response;
    try {
      response = await this.client.responses.create(this.buildRequest(request), { signal: options.signal, timeout: options.timeoutMs });
    } catch (error) {
      return mapError(error);
    }

    if (!response.usage) {
      return { kind: "error", status: null, retryable: false, message: `응답에 usage 가 없다 (status: ${String(response.status)})` };
    }
    const usage = toUsage(response.usage);
    const modelId = response.model;

    let text = "";
    let refusal: string | undefined;
    for (const item of response.output) {
      if (item.type !== "message") continue;
      for (const part of item.content) {
        if (part.type === "output_text") text += part.text;
        else if (part.type === "refusal") refusal = part.refusal;
      }
    }

    if (refusal !== undefined) return { kind: "refusal", text: refusal, category: null, usage, modelId };
    if (response.status === "incomplete") {
      const reason = response.incomplete_details?.reason;
      if (reason === "max_output_tokens") return { kind: "truncated", text, usage, modelId };
      if (reason === "content_filter") return { kind: "refusal", text, category: "content_filter", usage, modelId };
      return { kind: "error", status: null, retryable: false, message: `응답이 완료되지 않았다: ${String(reason)}` };
    }
    if (response.status === "completed") return { kind: "completed", text, usage, modelId };
    return { kind: "error", status: null, retryable: false, message: `예상하지 못한 응답 상태: ${String(response.status)}` };
  }
}

function toUsage(usage: OpenAI.Responses.ResponseUsage): Usage {
  // input_tokens 는 캐시 읽기·쓰기 토큰을 포함한 합계다. 나머지를 비캐시 입력으로 센다.
  // 2026-10-01 gpt-6-luna 실제 호출로 확인: 같은 요청을 두 번 보내면 input_tokens 는 6045 로 같고
  // 첫 호출은 cache_write_tokens 6042, 둘째는 cached_tokens 6042 였다.
  // output_tokens 는 추론 토큰(output_tokens_details.reasoning_tokens)을 포함하고, 추론 토큰도 max_output_tokens 를 쓴다.
  const cacheReadTokens = usage.input_tokens_details.cached_tokens;
  const cacheWriteTokens = usage.input_tokens_details.cache_write_tokens;
  // 추론 토큰 수는 호출 기록에 따로도 남긴다(`thinkingTokens`). 출력 상한의 여유분을 정할 근거다. 공급사가 필드를 보내지 않으면 남기지 않는다.
  const reasoningTokens: number | undefined = usage.output_tokens_details?.reasoning_tokens;
  return {
    inputTokens: Math.max(0, usage.input_tokens - cacheReadTokens - cacheWriteTokens),
    outputTokens: usage.output_tokens,
    cacheReadTokens,
    cacheWriteTokens,
    ...(reasoningTokens === undefined ? {} : { thinkingTokens: reasoningTokens }),
  };
}

function mapError(error: unknown): ProviderResponse {
  if (error instanceof OpenAI.APIUserAbortError) return { kind: "timeout", message: "호출이 중단됐다" };
  if (error instanceof OpenAI.APIConnectionTimeoutError) return { kind: "timeout", message: redactKeys(error.message) };
  if (error instanceof OpenAI.APIConnectionError) return { kind: "error", status: null, retryable: true, message: redactKeys(error.message) };
  if (error instanceof OpenAI.RateLimitError) return { kind: "error", status: 429, retryable: true, message: redactKeys(error.message) };
  if (error instanceof OpenAI.InternalServerError) return { kind: "error", status: error.status, retryable: true, message: redactKeys(error.message) };
  if (error instanceof OpenAI.APIError) return { kind: "error", status: error.status ?? null, retryable: false, message: redactKeys(error.message) };
  return { kind: "error", status: null, retryable: false, message: redactKeys(error instanceof Error ? error.message : String(error)) };
}
