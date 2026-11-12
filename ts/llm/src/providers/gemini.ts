import { ApiError, GoogleGenAI, type GenerateContentParameters, type GenerateContentResponse, type GenerateContentResponseUsageMetadata, type Part } from "@google/genai";
import { redactKeys } from "./redact.js";
import type { InvokeOptions, Provider, ProviderRequest, ProviderResponse, Usage } from "../types.js";

/** 어댑터가 쓰는 SDK 표면. 실제 `GoogleGenAI` 클라이언트가 이 모양을 만족하고, 테스트는 가짜를 넣는다. */
export interface GeminiLike {
  models: {
    generateContent(params: GenerateContentParameters): Promise<GenerateContentResponse>;
  };
}

/**
 * 키를 인자로 받아 SDK 클라이언트를 만든다. 환경 변수를 읽지 않는다. 키를 어디서 가져올지는 CLI 조립 지점이 정한다.
 * SDK 의 자체 재시도는 켜지 않는다(`httpOptions.retryOptions` 를 주지 않는다). 일시 오류(429·5xx)는 호출 계층이 다시 부른다
 * (`DEFAULT_PROVIDER_RETRY`). SDK 안의 재시도는 호출 기록에 남지 않고 호출 간격 조절을 지나지 않으며 공급사가 요구한 대기를 따르지 않는다.
 */
export function createGeminiClient(apiKey: string): GeminiLike {
  if (apiKey.trim() === "") throw new Error("Gemini 키가 비었다");
  return new GoogleGenAI({ apiKey });
}

/** 출력이 정책으로 막힌 종료 사유. 거부로 옮긴다. */
const BLOCKED_FINISH = new Set(["SAFETY", "RECITATION", "LANGUAGE", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"]);

/**
 * Gemini API(`models.generateContent`) 어댑터. 클라이언트는 밖에서 만들어 넣는다(키를 여기서 찾지 않는다).
 * 캐싱은 공급사가 앞부분 일치로 자동 적용(implicit caching)하므로 표시할 것이 없고, 고정 지침을 앞에 두는 것으로 충분하다.
 * 구조화 출력은 `responseMimeType` + `responseJsonSchema` 다. 공급사가 지원하지 않는 스키마 속성은 무시된다고 문서에 적혀 있어
 * 스키마는 그대로 보내고, 값의 검증은 호출 계층이 zod 로 한다.
 */
export class GeminiProvider implements Provider {
  readonly name = "gemini";

  constructor(private readonly client?: GeminiLike) {}

  buildRequest(request: ProviderRequest): GenerateContentParameters {
    const parts: Part[] = request.user.map((part) => {
      if (part.type === "text") return { text: part.text };
      if (part.type === "pdf") return { inlineData: { mimeType: "application/pdf", data: part.base64 } };
      return { inlineData: { mimeType: part.mediaType, data: part.base64 } };
    });

    const config: NonNullable<GenerateContentParameters["config"]> = { maxOutputTokens: request.maxOutputTokens };
    if (request.system.length > 0) config.systemInstruction = request.system.map((segment) => segment.text).join("\n\n");
    if (request.jsonSchema) {
      config.responseMimeType = "application/json";
      config.responseJsonSchema = request.jsonSchema.schema;
    }
    return { model: request.apiModelId, contents: [{ role: "user", parts }], config };
  }

  async invoke(request: ProviderRequest, options: InvokeOptions): Promise<ProviderResponse> {
    if (!this.client) return { kind: "error", status: null, retryable: false, message: "Gemini 클라이언트가 주입되지 않았다" };
    const params = this.buildRequest(request);
    let response: GenerateContentResponse;
    try {
      // 시간 제한은 중단 신호로만 건다. `httpOptions.timeout` 은 서버에 마감 시간으로 전달돼 10초 미만이면 400 이다
      // (2026-10-02 실제 호출: "Manually set deadline 1s is too short. Minimum allowed deadline is 10s.").
      response = await this.client.models.generateContent({ ...params, config: { ...params.config, abortSignal: options.signal } });
    } catch (error) {
      return mapError(error, options.signal);
    }

    const candidate = response.candidates?.[0];
    const blockReason = response.promptFeedback?.blockReason;
    const zero: Usage = { inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0 };
    // 입력이 막히면 후보가 없고 promptFeedback.blockReason 만 온다. usage 가 같이 오지 않아도 거부로 돌려준다.
    if (!candidate && blockReason) {
      const usage = response.usageMetadata ? toUsage(response.usageMetadata) : zero;
      return { kind: "refusal", text: "", category: String(blockReason), usage, ...(response.modelVersion === undefined ? {} : { modelId: response.modelVersion }) };
    }
    if (!response.usageMetadata) return { kind: "error", status: null, retryable: false, message: "응답에 usageMetadata 가 없다" };
    const usage = toUsage(response.usageMetadata);
    const modelId = response.modelVersion;
    const meta = modelId === undefined ? { usage } : { usage, modelId };
    if (!candidate) return { kind: "error", status: null, retryable: false, message: "응답에 후보가 없다" };

    // 생각(thought) 부분은 답이 아니므로 뺀다.
    const text = (candidate.content?.parts ?? [])
      .filter((part) => !part.thought && typeof part.text === "string")
      .map((part) => part.text as string)
      .join("");
    const finish = String(candidate.finishReason);
    if (finish === "STOP") return { kind: "completed", text, ...meta };
    if (finish === "MAX_TOKENS") return { kind: "truncated", text, ...meta };
    if (BLOCKED_FINISH.has(finish)) return { kind: "refusal", text, category: finish, ...meta };
    return { kind: "error", status: null, retryable: false, message: `예상하지 못한 finishReason: ${finish}` };
  }
}

function toUsage(usage: GenerateContentResponseUsageMetadata): Usage {
  // promptTokenCount 는 캐시에서 읽은 토큰(cachedContentTokenCount)을 포함한 합계다. 나머지를 비캐시 입력으로 센다.
  // 2026-10-02 gemini-3.1-flash-lite 실제 호출로 확인: 같은 요청을 두 번 보내면 promptTokenCount 는 7127 로 같고
  // 첫 호출에는 cachedContentTokenCount 필드가 없고 둘째는 4076 이었다(앞부분의 일부만 적중).
  // 캐시 적중이 없으면 공급사가 필드를 아예 보내지 않는다. 0 으로 센다.
  // 자동 캐시에는 쓰기 과금이 따로 없어 캐시 쓰기 토큰은 항상 0 이다.
  // 생각 토큰(thoughtsTokenCount)은 candidatesTokenCount 에 들어 있지 않고 출력 단가로 과금되므로 출력에 더한다.
  // 생각 토큰 수는 호출 기록에 따로도 남긴다(`thinkingTokens`). 공급사가 필드를 보내지 않으면 남기지 않는다.
  const cacheReadTokens = usage.cachedContentTokenCount ?? 0;
  const thinkingTokens = usage.thoughtsTokenCount;
  return {
    inputTokens: Math.max(0, (usage.promptTokenCount ?? 0) - cacheReadTokens),
    outputTokens: (usage.candidatesTokenCount ?? 0) + (thinkingTokens ?? 0),
    cacheReadTokens,
    cacheWriteTokens: 0,
    ...(thinkingTokens === undefined ? {} : { thinkingTokens }),
  };
}

function mapError(error: unknown, signal: AbortSignal): ProviderResponse {
  const name = error instanceof Error ? error.name : "";
  const message = redactKeys(error instanceof Error ? error.message : String(error));
  if (signal.aborted || name === "AbortError") return { kind: "timeout", message: "호출이 중단됐다" };
  if (name === "TimeoutError") return { kind: "timeout", message };
  if (error instanceof ApiError) {
    const retryable = error.status === 408 || error.status === 429 || error.status >= 500;
    const retryAfterMs = retryable ? retryInfoDelayMs(error.message) : undefined;
    return { kind: "error", status: error.status, retryable, message, ...(retryAfterMs === undefined ? {} : { retryAfterMs }) };
  }
  // SDK 는 연결 실패를 따로 감싸지 않는다. fetch 가 던진 TypeError 가 그대로 온다.
  if (error instanceof TypeError) return { kind: "error", status: null, retryable: true, message };
  return { kind: "error", status: null, retryable: false, message };
}

/**
 * 오류 본문의 `google.rpc.RetryInfo.retryDelay` ("55s" 꼴)를 ms 로 읽는다. SDK 의 `ApiError` 는 응답 머리(Retry-After)를 주지 않고
 * 본문 JSON 을 `message` 에 싣는다 (`@google/genai` 2.26.0 의 `throwErrorIfNotOK`). 없거나 읽을 수 없으면 undefined.
 * 2026-10-02 무료 등급 429 의 실제 응답: `{"@type":"type.googleapis.com/google.rpc.RetryInfo","retryDelay":"55s"}`.
 */
export function retryInfoDelayMs(message: string): number | undefined {
  let body: unknown;
  try {
    body = JSON.parse(message);
  } catch {
    return undefined;
  }
  const details = (body as { error?: { details?: unknown } } | null)?.error?.details;
  if (!Array.isArray(details)) return undefined;
  for (const detail of details as { "@type"?: unknown; retryDelay?: unknown }[]) {
    if (typeof detail?.["@type"] !== "string" || !detail["@type"].endsWith("google.rpc.RetryInfo") || typeof detail.retryDelay !== "string") continue;
    const match = /^(\d+(?:\.\d+)?)s$/.exec(detail.retryDelay);
    if (match) return Math.round(Number(match[1]) * 1000);
  }
  return undefined;
}
