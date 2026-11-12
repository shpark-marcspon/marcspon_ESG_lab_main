import Anthropic from "@anthropic-ai/sdk";
import OpenAI from "openai";
import { describe, expect, it } from "vitest";
import { AnthropicProvider, OpenAIProvider, type AnthropicLike, type OpenAILike, type ProviderRequest } from "../src/index.js";

// 실제 공급사로 가는 호출은 없다. SDK 클라이언트 자리에 가짜를 넣고 요청 조립과 응답 해석만 본다.
// 아래 토큰 수와 응답 본문은 전부 가상(synthetic) 값이다.

const request: ProviderRequest = {
  apiModelId: "model-synthetic",
  system: [
    { text: "용어집", cache: true },
    { text: "지침", cache: true },
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

function anthropicMessage(overrides: Partial<Anthropic.Message>): Anthropic.Message {
  return {
    id: "msg_synthetic",
    type: "message",
    role: "assistant",
    model: "model-synthetic",
    content: [{ type: "text", text: '{"ok":true}', citations: null }],
    stop_reason: "end_turn",
    stop_sequence: null,
    usage: { input_tokens: 11, output_tokens: 7, cache_read_input_tokens: 300, cache_creation_input_tokens: 40 },
    ...overrides,
  } as Anthropic.Message;
}

function fakeAnthropic(reply: Anthropic.Message | Error) {
  const seen: { body: Anthropic.MessageStreamParams; options: unknown }[] = [];
  const client: AnthropicLike = {
    messages: {
      stream(body, opts) {
        seen.push({ body, options: opts });
        return { finalMessage: () => (reply instanceof Error ? Promise.reject(reply) : Promise.resolve(reply)) };
      },
    },
  };
  return { client, seen };
}

describe("Anthropic 어댑터", () => {
  it("요청 조립: 캐시 표시는 마지막 캐시 구획에만, PDF 는 글 앞에, 스키마는 output_config.format 에", () => {
    const body = new AnthropicProvider().buildRequest(request);
    expect(body).toEqual({
      model: "model-synthetic",
      max_tokens: 2000,
      system: [
        { type: "text", text: "용어집" },
        { type: "text", text: "지침", cache_control: { type: "ephemeral" } },
        { type: "text", text: "오늘의 메모" },
      ],
      messages: [
        {
          role: "user",
          content: [
            { type: "document", source: { type: "base64", media_type: "application/pdf", data: "UERG" } },
            { type: "text", text: "표를 뽑아 줘" },
          ],
        },
      ],
      output_config: { format: { type: "json_schema", schema: request.jsonSchema?.schema } },
    });
  });

  it("캐시 표시와 스키마가 없으면 해당 필드를 넣지 않는다", () => {
    const body = new AnthropicProvider().buildRequest({ apiModelId: "m", system: [], user: [{ type: "text", text: "q" }], maxOutputTokens: 10 });
    expect(body).toEqual({ model: "m", max_tokens: 10, messages: [{ role: "user", content: [{ type: "text", text: "q" }] }] });
  });

  it("완료 응답과 사용량을 옮긴다", async () => {
    const { client, seen } = fakeAnthropic(anthropicMessage({}));
    const response = await new AnthropicProvider(client).invoke(request, options);
    expect(response).toEqual({
      kind: "completed",
      text: '{"ok":true}',
      usage: { inputTokens: 11, outputTokens: 7, cacheReadTokens: 300, cacheWriteTokens: 40 },
      modelId: "model-synthetic",
    });
    expect(seen[0]?.options).toEqual({ signal: options.signal, timeout: 1000 });
  });

  it("stop_reason 을 결과 종류로 옮긴다", async () => {
    const run = async (overrides: Partial<Anthropic.Message>) => (await new AnthropicProvider(fakeAnthropic(anthropicMessage(overrides)).client).invoke(request, options)).kind;
    expect(await run({ stop_reason: "max_tokens" })).toBe("truncated");
    expect(await run({ stop_reason: "model_context_window_exceeded" })).toBe("truncated");
    expect(await run({ stop_reason: "refusal" })).toBe("refusal");
    expect(await run({ stop_reason: "tool_use" })).toBe("error");
    expect(await run({ stop_reason: null })).toBe("error");
  });

  it("보고되지 않은 캐시 토큰은 null 로 남긴다", async () => {
    const message = anthropicMessage({});
    message.usage.cache_read_input_tokens = null;
    message.usage.cache_creation_input_tokens = null;
    const response = await new AnthropicProvider(fakeAnthropic(message).client).invoke(request, options);
    expect(response).toMatchObject({ usage: { cacheReadTokens: null, cacheWriteTokens: null } });
  });

  it("SDK 예외를 오류·시간 초과로 옮긴다", async () => {
    const run = (error: Error) => new AnthropicProvider(fakeAnthropic(error).client).invoke(request, options);
    const headers = new Headers();
    expect(await run(new Anthropic.APIConnectionTimeoutError())).toMatchObject({ kind: "timeout" });
    expect(await run(new Anthropic.APIUserAbortError())).toMatchObject({ kind: "timeout" });
    expect(await run(new Anthropic.APIConnectionError({ message: "끊김" }))).toMatchObject({ kind: "error", status: null, retryable: true });
    expect(await run(Anthropic.APIError.generate(429, undefined, "한도", headers))).toMatchObject({ kind: "error", status: 429, retryable: true });
    expect(await run(Anthropic.APIError.generate(529, undefined, "과부하", headers))).toMatchObject({ kind: "error", status: 529, retryable: true });
    expect(await run(Anthropic.APIError.generate(400, undefined, "잘못된 요청", headers))).toMatchObject({ kind: "error", status: 400, retryable: false });
    expect(await run(new Error("알 수 없음"))).toMatchObject({ kind: "error", status: null, retryable: false, message: "알 수 없음" });
  });

  it("클라이언트가 없으면 부르지 않고 오류를 돌려준다", async () => {
    expect(await new AnthropicProvider().invoke(request, options)).toMatchObject({ kind: "error", retryable: false });
  });
});

type OpenAIResponse = OpenAI.Responses.Response;

function openaiResponse(overrides: Record<string, unknown>): OpenAIResponse {
  return {
    id: "resp_synthetic",
    status: "completed",
    incomplete_details: null,
    output: [{ type: "message", id: "msg", role: "assistant", status: "completed", content: [{ type: "output_text", text: '{"ok":true}', annotations: [] }] }],
    usage: {
      input_tokens: 500,
      input_tokens_details: { cached_tokens: 300, cache_write_tokens: 40 },
      output_tokens: 7,
      output_tokens_details: { reasoning_tokens: 0 },
      total_tokens: 507,
    },
    ...overrides,
  } as unknown as OpenAIResponse;
}

function fakeOpenAI(reply: OpenAIResponse | Error) {
  const seen: unknown[] = [];
  const client: OpenAILike = {
    responses: {
      create(body) {
        seen.push(body);
        return reply instanceof Error ? Promise.reject(reply) : Promise.resolve(reply);
      },
    },
  };
  return { client, seen };
}

describe("OpenAI 어댑터", () => {
  it("요청 조립: 지침은 instructions 로 합치고 PDF 는 input_file, 스키마는 text.format 에", () => {
    expect(new OpenAIProvider().buildRequest(request)).toEqual({
      model: "model-synthetic",
      max_output_tokens: 2000,
      store: false,
      instructions: "용어집\n\n지침\n\n오늘의 메모",
      input: [
        {
          role: "user",
          content: [
            { type: "input_file", filename: "report.pdf", file_data: "data:application/pdf;base64,UERG" },
            { type: "input_text", text: "표를 뽑아 줘" },
          ],
        },
      ],
      text: { format: { type: "json_schema", name: "table", schema: request.jsonSchema?.schema, strict: true } },
    });
  });

  it("완료 응답: 입력 토큰에서 캐시 읽기·쓰기를 빼 비캐시 입력으로 센다", async () => {
    const response = await new OpenAIProvider(fakeOpenAI(openaiResponse({})).client).invoke(request, options);
    expect(response).toEqual({
      kind: "completed",
      text: '{"ok":true}',
      usage: { inputTokens: 160, outputTokens: 7, cacheReadTokens: 300, cacheWriteTokens: 40, thinkingTokens: 0 },
    });
  });

  it("추론 토큰은 출력 토큰에 든 채로 두고 thinkingTokens 에 따로도 남긴다. 공급사가 안 보내면 남기지 않는다", async () => {
    const usage = { input_tokens: 500, input_tokens_details: { cached_tokens: 0, cache_write_tokens: 0 }, output_tokens: 24, total_tokens: 524 };
    const reasoned = await new OpenAIProvider(fakeOpenAI(openaiResponse({ usage: { ...usage, output_tokens_details: { reasoning_tokens: 17 } } })).client).invoke(request, options);
    expect(reasoned).toMatchObject({ kind: "completed", usage: { outputTokens: 24, thinkingTokens: 17 } });
    const silent = await new OpenAIProvider(fakeOpenAI(openaiResponse({ usage })).client).invoke(request, options);
    expect(silent).toMatchObject({ kind: "completed", usage: { outputTokens: 24 } });
    expect(silent.kind === "completed" && "thinkingTokens" in silent.usage).toBe(false);
  });

  it("거부, 출력 잘림, 미완료, usage 없는 응답을 구분한다", async () => {
    const run = (overrides: Record<string, unknown>) => new OpenAIProvider(fakeOpenAI(openaiResponse(overrides)).client).invoke(request, options);
    const refusal = [{ type: "message", id: "m", role: "assistant", status: "completed", content: [{ type: "refusal", refusal: "거절" }] }];
    expect(await run({ output: refusal })).toMatchObject({ kind: "refusal", text: "거절" });
    expect(await run({ status: "incomplete", incomplete_details: { reason: "max_output_tokens" } })).toMatchObject({ kind: "truncated" });
    expect(await run({ status: "incomplete", incomplete_details: { reason: "content_filter" } })).toMatchObject({ kind: "refusal", category: "content_filter" });
    expect(await run({ status: "failed" })).toMatchObject({ kind: "error" });
    expect(await run({ usage: undefined })).toMatchObject({ kind: "error" });
  });

  it("SDK 예외를 오류·시간 초과로 옮긴다", async () => {
    const run = (error: Error) => new OpenAIProvider(fakeOpenAI(error).client).invoke(request, options);
    const headers = new Headers();
    expect(await run(new OpenAI.APIConnectionTimeoutError())).toMatchObject({ kind: "timeout" });
    expect(await run(new OpenAI.APIConnectionError({ message: "끊김" }))).toMatchObject({ kind: "error", retryable: true });
    expect(await run(OpenAI.APIError.generate(500, undefined, "서버", headers))).toMatchObject({ kind: "error", status: 500, retryable: true });
    expect(await run(OpenAI.APIError.generate(401, undefined, "인증", headers))).toMatchObject({ kind: "error", status: 401, retryable: false });
  });

  it("클라이언트가 없으면 부르지 않고 오류를 돌려준다", async () => {
    expect(await new OpenAIProvider().invoke(request, options)).toMatchObject({ kind: "error", retryable: false });
  });
});
