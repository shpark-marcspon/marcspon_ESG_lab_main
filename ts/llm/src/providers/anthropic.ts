import Anthropic from "@anthropic-ai/sdk";
import { redactKeys } from "./redact.js";
import type { InvokeOptions, Provider, ProviderRequest, ProviderResponse, Usage } from "../types.js";

/** 어댑터가 쓰는 SDK 표면. 실제 `Anthropic` 클라이언트가 이 모양을 만족하고, 테스트는 가짜를 넣는다. */
export interface AnthropicLike {
  messages: {
    stream(
      body: Anthropic.MessageStreamParams,
      options?: { signal?: AbortSignal; timeout?: number },
    ): { finalMessage(): Promise<Anthropic.Message> };
  };
}

/**
 * 키를 인자로 받아 SDK 클라이언트를 만든다. 환경 변수를 읽지 않는다(`authToken: null` 로 SDK 가 환경의 토큰을 집지 않게 한다).
 * SDK 의 자체 재시도(기본 2회, 429·5xx·연결 실패)는 그대로 둔다.
 */
export function createAnthropicClient(apiKey: string): AnthropicLike {
  if (apiKey.trim() === "") throw new Error("Anthropic 키가 비었다");
  return new Anthropic({ apiKey, authToken: null });
}

/**
 * 구조화 출력이 받지 않는 JSON Schema 제약(글자 길이, 수 범위, 배열 길이). 보내면 400 이라 지우고 보낸다.
 * 값의 검증은 호출 계층이 zod 로 하므로 제약 자체는 사라지지 않는다. 공식 SDK 의 zod 도우미도 같은 일을 한다.
 */
const UNSUPPORTED_SCHEMA_KEYS = new Set(["minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minItems", "maxItems"]);

/** 스키마 낱말 자리에서만 지운다. `properties` 아래의 키는 필드 이름이므로 건드리지 않는다. */
export function stripUnsupportedSchemaKeys(schema: unknown, propertyNames = false): unknown {
  if (Array.isArray(schema)) return schema.map((item) => stripUnsupportedSchemaKeys(item));
  if (schema === null || typeof schema !== "object") return schema;
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(schema)) {
    if (!propertyNames && UNSUPPORTED_SCHEMA_KEYS.has(key)) continue;
    out[key] = stripUnsupportedSchemaKeys(value, !propertyNames && (key === "properties" || key === "$defs" || key === "definitions"));
  }
  return out;
}

/**
 * Anthropic Messages API 어댑터. 클라이언트는 밖에서 만들어 넣는다(키를 여기서 찾지 않는다).
 * 프롬프트 캐싱(cache_control), PDF·이미지 블록, 구조화 출력(output_config.format)은 이 파일 안에만 있다.
 */
export class AnthropicProvider implements Provider {
  readonly name = "anthropic";

  constructor(private readonly client?: AnthropicLike) {}

  buildRequest(request: ProviderRequest): Anthropic.MessageStreamParams {
    const system: Anthropic.TextBlockParam[] = request.system.map((segment) => ({ type: "text", text: segment.text }));
    // 캐시는 앞부분 일치라 마지막 캐시 구획에만 표시하면 그 앞까지 함께 걸린다.
    const lastCached = request.system.map((segment) => segment.cache).lastIndexOf(true);
    if (lastCached >= 0) (system[lastCached] as Anthropic.TextBlockParam).cache_control = { type: "ephemeral" };

    const content: Anthropic.ContentBlockParam[] = request.user.map((part) => {
      if (part.type === "text") return { type: "text", text: part.text };
      if (part.type === "pdf") {
        return { type: "document", source: { type: "base64", media_type: "application/pdf", data: part.base64 } };
      }
      return { type: "image", source: { type: "base64", media_type: part.mediaType, data: part.base64 } };
    });

    const params: Anthropic.MessageStreamParams = {
      model: request.apiModelId,
      max_tokens: request.maxOutputTokens,
      messages: [{ role: "user", content }],
    };
    if (system.length > 0) params.system = system;
    if (request.jsonSchema) params.output_config = { format: { type: "json_schema", schema: stripUnsupportedSchemaKeys(request.jsonSchema.schema) as Record<string, unknown> } };
    return params;
  }

  async invoke(request: ProviderRequest, options: InvokeOptions): Promise<ProviderResponse> {
    if (!this.client) return { kind: "error", status: null, retryable: false, message: "Anthropic 클라이언트가 주입되지 않았다" };
    let message: Anthropic.Message;
    try {
      // 출력이 길면 비스트리밍 요청은 HTTP 시간 제한에 걸리므로 항상 스트림으로 받아 합친다.
      message = await this.client.messages.stream(this.buildRequest(request), { signal: options.signal, timeout: options.timeoutMs }).finalMessage();
    } catch (error) {
      return mapError(error);
    }

    const usage = toUsage(message.usage);
    const modelId = message.model;
    const text = message.content
      .filter((block): block is Anthropic.TextBlock => block.type === "text")
      .map((block) => block.text)
      .join("");

    switch (message.stop_reason) {
      case "end_turn":
      case "stop_sequence":
        return { kind: "completed", text, usage, modelId };
      case "refusal":
        return { kind: "refusal", text, category: message.stop_details?.category ?? null, usage, modelId };
      case "max_tokens":
      case "model_context_window_exceeded":
        return { kind: "truncated", text, usage, modelId };
      default:
        // tool_use, pause_turn, null: 도구를 주지 않았으므로 나올 수 없는 종료다.
        return { kind: "error", status: null, retryable: false, message: `예상하지 못한 stop_reason: ${String(message.stop_reason)}` };
    }
  }
}

function toUsage(usage: Anthropic.Usage): Usage {
  // input_tokens 는 캐시 읽기·쓰기를 뺀 값이다. 2026-10-02 claude-haiku-4-5 실제 호출로 확인: 같은 요청을 두 번 보내면
  // input_tokens 는 15 로 같고 첫 호출은 cache_creation_input_tokens 6832, 둘째는 cache_read_input_tokens 6832 였다.
  return {
    inputTokens: usage.input_tokens,
    outputTokens: usage.output_tokens,
    cacheReadTokens: usage.cache_read_input_tokens,
    cacheWriteTokens: usage.cache_creation_input_tokens,
  };
}

function mapError(error: unknown): ProviderResponse {
  if (error instanceof Anthropic.APIUserAbortError) return { kind: "timeout", message: "호출이 중단됐다" };
  if (error instanceof Anthropic.APIConnectionTimeoutError) return { kind: "timeout", message: redactKeys(error.message) };
  if (error instanceof Anthropic.APIConnectionError) return { kind: "error", status: null, retryable: true, message: redactKeys(error.message) };
  if (error instanceof Anthropic.RateLimitError) return { kind: "error", status: 429, retryable: true, message: redactKeys(error.message) };
  if (error instanceof Anthropic.InternalServerError) return { kind: "error", status: error.status, retryable: true, message: redactKeys(error.message) };
  if (error instanceof Anthropic.APIError) return { kind: "error", status: error.status ?? null, retryable: false, message: redactKeys(error.message) };
  return { kind: "error", status: null, retryable: false, message: redactKeys(error instanceof Error ? error.message : String(error)) };
}
