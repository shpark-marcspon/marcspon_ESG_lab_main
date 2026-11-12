import { ApiError, type GenerateContentParameters, type GenerateContentResponse } from "@google/genai";
import { describe, expect, it } from "vitest";
import { GeminiProvider, redactKeys, stripUnsupportedSchemaKeys, throttled, type GeminiLike, type Provider, type ProviderRequest } from "../src/index.js";

// 실제 공급사로 가는 호출은 없다. SDK 클라이언트 자리에 가짜를 넣고 요청 조립과 응답 해석만 본다.
// 아래 토큰 수와 응답 본문은 전부 가상(synthetic) 값이다.

const request: ProviderRequest = {
  apiModelId: "model-synthetic",
  system: [
    { text: "용어집", cache: true },
    { text: "오늘의 메모", cache: false },
  ],
  user: [
    { type: "pdf", base64: "UERG", filename: "report.pdf" },
    { type: "text", text: "표를 뽑아 줘" },
  ],
  maxOutputTokens: 2000,
  jsonSchema: { name: "table", schema: { type: "object", properties: {}, additionalProperties: false } },
};
const options = { signal: new AbortController().signal, timeoutMs: 1000 };

function geminiResponse(overrides: Record<string, unknown>): GenerateContentResponse {
  return {
    modelVersion: "model-synthetic-001",
    candidates: [{ finishReason: "STOP", content: { role: "model", parts: [{ text: "생각 (synthetic)", thought: true }, { text: '{"ok":' }, { text: "true}" }] } }],
    usageMetadata: { promptTokenCount: 500, cachedContentTokenCount: 300, candidatesTokenCount: 7, thoughtsTokenCount: 5, totalTokenCount: 512 },
    ...overrides,
  } as unknown as GenerateContentResponse;
}

function fakeGemini(reply: GenerateContentResponse | Error) {
  const seen: GenerateContentParameters[] = [];
  const client: GeminiLike = {
    models: {
      generateContent(params) {
        seen.push(params);
        return reply instanceof Error ? Promise.reject(reply) : Promise.resolve(reply);
      },
    },
  };
  return { client, seen };
}

const run = (reply: GenerateContentResponse | Error, signal = options.signal) => new GeminiProvider(fakeGemini(reply).client).invoke(request, { ...options, signal });
const finished = (finishReason: string) => geminiResponse({ candidates: [{ finishReason, content: { role: "model", parts: [{ text: "1, 2, 3" }] } }] });

describe("Gemini 어댑터", () => {
  it("요청 조립: 지침은 systemInstruction 으로 합치고 PDF 는 inlineData, 스키마는 responseJsonSchema 에", () => {
    expect(new GeminiProvider().buildRequest(request)).toEqual({
      model: "model-synthetic",
      contents: [{ role: "user", parts: [{ inlineData: { mimeType: "application/pdf", data: "UERG" } }, { text: "표를 뽑아 줘" }] }],
      config: { maxOutputTokens: 2000, systemInstruction: "용어집\n\n오늘의 메모", responseMimeType: "application/json", responseJsonSchema: request.jsonSchema?.schema },
    });
  });

  it("지침과 스키마가 없으면 해당 필드를 넣지 않는다", () => {
    expect(new GeminiProvider().buildRequest({ apiModelId: "m", system: [], user: [{ type: "text", text: "q" }], maxOutputTokens: 10 })).toEqual({
      model: "m",
      contents: [{ role: "user", parts: [{ text: "q" }] }],
      config: { maxOutputTokens: 10 },
    });
  });

  it("완료 응답: 생각 부분을 뺀 글자, 캐시 읽기를 뺀 비캐시 입력, 생각 토큰을 더한 출력, 응답 모델 ID", async () => {
    const { client, seen } = fakeGemini(geminiResponse({}));
    expect(await new GeminiProvider(client).invoke(request, options)).toEqual({
      kind: "completed",
      text: '{"ok":true}',
      usage: { inputTokens: 200, outputTokens: 12, cacheReadTokens: 300, cacheWriteTokens: 0, thinkingTokens: 5 },
      modelId: "model-synthetic-001",
    });
    // 시간 제한은 중단 신호로만 건다. 요청에 마감 시간을 싣지 않는다.
    expect(seen[0]?.config?.abortSignal).toBe(options.signal);
    expect(seen[0]?.config?.httpOptions).toBeUndefined();
  });

  it("캐시 적중이 없어 필드가 빠진 응답은 캐시 읽기 0 으로 센다", async () => {
    const response = geminiResponse({ usageMetadata: { promptTokenCount: 40, candidatesTokenCount: 3, totalTokenCount: 43 } });
    const result = await run(response);
    expect(result).toMatchObject({ usage: { inputTokens: 40, outputTokens: 3, cacheReadTokens: 0, cacheWriteTokens: 0 } });
    // 공급사가 생각 토큰 필드를 보내지 않으면 0 으로 지어내지 않고 필드를 남기지 않는다.
    expect(result.kind === "completed" && "thinkingTokens" in result.usage).toBe(false);
  });

  it("출력 토큰 상한에서 끊긴 응답은 truncated 다", async () => {
    expect(await run(finished("MAX_TOKENS"))).toMatchObject({ kind: "truncated", text: "1, 2, 3", usage: { outputTokens: 12 } });
  });

  it("정책으로 막힌 출력과 입력은 refusal, 그 밖의 종료와 usage 없는 응답은 error 다", async () => {
    expect(await run(finished("SAFETY"))).toMatchObject({ kind: "refusal", category: "SAFETY" });
    expect(await run(finished("RECITATION"))).toMatchObject({ kind: "refusal", category: "RECITATION" });
    expect(await run(geminiResponse({ candidates: undefined, promptFeedback: { blockReason: "PROHIBITED_CONTENT" } }))).toMatchObject({ kind: "refusal", category: "PROHIBITED_CONTENT", text: "" });
    // 입력이 막힌 응답에 usage 가 없어도 오류가 아니라 거부다.
    expect(await run(geminiResponse({ candidates: undefined, usageMetadata: undefined, promptFeedback: { blockReason: "SAFETY" } }))).toMatchObject({ kind: "refusal", category: "SAFETY", usage: { inputTokens: 0, outputTokens: 0 } });
    expect(await run(geminiResponse({ candidates: [] }))).toMatchObject({ kind: "error", retryable: false });
    expect(await run(finished("MALFORMED_FUNCTION_CALL"))).toMatchObject({ kind: "error", retryable: false });
    expect(await run(geminiResponse({ usageMetadata: undefined }))).toMatchObject({ kind: "error", retryable: false });
  });

  it("SDK 예외를 오류·시간 초과로 옮기고 키 모양 글자를 가린다", async () => {
    expect(await run(new ApiError({ status: 429, message: "한도" }))).toMatchObject({ kind: "error", status: 429, retryable: true });
    expect(await run(new ApiError({ status: 503, message: "과부하" }))).toMatchObject({ kind: "error", status: 503, retryable: true });
    expect(await run(new ApiError({ status: 400, message: "API key not valid: AIzaSyntheticSyntheticSynthetic0000" }))).toEqual({ kind: "error", status: 400, retryable: false, message: "API key not valid: AIza[가림]" });
    expect(await run(new ApiError({ status: 404, message: "없는 모델" }))).toMatchObject({ kind: "error", status: 404, retryable: false });
    expect(await run(new TypeError("fetch failed"))).toMatchObject({ kind: "error", status: null, retryable: true });
    expect(await run(new Error("알 수 없음"))).toMatchObject({ kind: "error", status: null, retryable: false, message: "알 수 없음" });
    const aborted = new AbortController();
    aborted.abort();
    expect(await run(new Error("This operation was aborted"), aborted.signal)).toMatchObject({ kind: "timeout" });
    expect(await run(Object.assign(new Error("중단"), { name: "AbortError" }))).toMatchObject({ kind: "timeout" });
  });

  it("클라이언트가 없으면 부르지 않고 오류를 돌려준다", async () => {
    expect(await new GeminiProvider().invoke(request, options)).toMatchObject({ kind: "error", retryable: false });
  });
});

describe("키 가리기와 스키마 낱말 지우기", () => {
  it("sk- 꼴과 AIza 꼴을 가리고 보통 낱말은 그대로 둔다", () => {
    expect(redactKeys("invalid x-api-key sk-ant-api03-synthetic_synthetic")).toBe("invalid x-api-key sk-[가림]");
    expect(redactKeys("key=AIzaSyntheticSynthetic_0000-xyz 끝")).toBe("key=AIza[가림] 끝");
    expect(redactKeys("AIza 와 risk-free 는 그대로")).toBe("AIza 와 risk-free 는 그대로");
  });

  it("Anthropic 구조화 출력이 받지 않는 제약만 지우고 같은 이름의 필드는 남긴다", () => {
    const schema = {
      type: "object",
      properties: {
        keywords: { type: "array", maxItems: 8, items: { type: "string", minLength: 1, maxLength: 60 } },
        maximum: { type: "number", minimum: 0, maximum: 10 },
        kind: { anyOf: [{ const: 1 }, { const: "other" }] },
      },
      required: ["keywords", "maximum", "kind"],
      additionalProperties: false,
    };
    expect(stripUnsupportedSchemaKeys(schema)).toEqual({
      type: "object",
      properties: { keywords: { type: "array", items: { type: "string" } }, maximum: { type: "number" }, kind: { anyOf: [{ const: 1 }, { const: "other" }] } },
      required: ["keywords", "maximum", "kind"],
      additionalProperties: false,
    });
    // 원본은 바꾸지 않는다.
    expect(schema.properties.keywords.maxItems).toBe(8);
  });
});

describe("호출 간격 조절", () => {
  it("분당 요청 수에 맞춰 시작 간격을 벌리고, 기다리는 동안 중단되면 공급사를 부르지 않는다", async () => {
    let now = 1_000;
    const sleeps: number[] = [];
    const clock = { now: () => now, sleep: (ms: number) => (sleeps.push(ms), (now += ms), Promise.resolve()) };
    const starts: number[] = [];
    const inner: Provider = {
      name: "gemini",
      buildRequest: () => "조립된 요청",
      invoke: () => (starts.push(now), Promise.resolve({ kind: "completed", text: "ok", usage: { inputTokens: 1, outputTokens: 1, cacheReadTokens: 0, cacheWriteTokens: 0 } })),
    };
    const provider = throttled(inner, 10, clock);
    expect(provider.name).toBe("gemini");
    expect(provider.buildRequest(request)).toBe("조립된 요청");
    await provider.invoke(request, options);
    await provider.invoke(request, options);
    now += 20_000; // 간격보다 오래 쉬었으면 기다리지 않는다
    await provider.invoke(request, options);
    expect(starts).toEqual([1_000, 7_000, 27_000]);
    expect(sleeps).toEqual([6_000]);

    const aborted = new AbortController();
    aborted.abort();
    expect(await provider.invoke(request, { ...options, signal: aborted.signal })).toMatchObject({ kind: "timeout" });
    expect(starts).toHaveLength(3);
    expect(() => throttled(inner, 0)).toThrow();
  });
});
