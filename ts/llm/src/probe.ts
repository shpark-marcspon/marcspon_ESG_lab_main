import { z } from "zod";
import type { LlmClient } from "./client.js";
import type { AnthropicLike } from "./providers/anthropic.js";
import type { GeminiLike } from "./providers/gemini.js";
import type { OpenAILike } from "./providers/openai.js";
import { redactKeys } from "./providers/redact.js";
import type { LlmResult, LlmResultKind, Tags } from "./types.js";

/**
 * 공급사가 돌려준 원본 응답에서 확인에 필요한 것만 옮긴 기록. 키와 요청 헤더는 여기에 오지 않는다.
 * `status` 는 공급사가 종료를 적는 자리다: OpenAI `status`, Anthropic `stop_reason`, Gemini `finishReason`.
 * `incompleteReason` 은 OpenAI `incomplete_details.reason`, Gemini `promptFeedback.blockReason` 이고 Anthropic 에는 없다.
 */
export interface RawExchange {
  request: { model: string | undefined; maxOutputTokens: number | null | undefined; formatType: string | null; timeoutMs: number | null };
  response: { model: string; status: string | null; incompleteReason: string | null; outputTypes: string[]; usage: unknown } | null;
  error: { name: string; message: string; status: number | null; signalAborted: boolean } | null;
  elapsedMs: number;
}

/** SDK 클라이언트를 감싸 요청·응답의 원본 모양을 `sink` 에 남긴다. 어댑터가 접어 버리는 필드(status, usage 원본)를 보려는 것이다. */
export function recordingOpenAI(inner: OpenAILike, sink: RawExchange[]): OpenAILike {
  return {
    responses: {
      async create(body, options) {
        const started = Date.now();
        const request = {
          model: body.model as string | undefined,
          maxOutputTokens: body.max_output_tokens,
          formatType: body.text?.format?.type ?? null,
          timeoutMs: options?.timeout ?? null,
        };
        try {
          const response = await inner.responses.create(body, options);
          sink.push({
            request,
            response: {
              model: response.model,
              status: response.status ?? null,
              incompleteReason: response.incomplete_details?.reason ?? null,
              outputTypes: (response.output ?? []).map((item) => item.type),
              usage: response.usage ?? null,
            },
            error: null,
            elapsedMs: Date.now() - started,
          });
          return response;
        } catch (error) {
          const status = (error as { status?: unknown }).status;
          sink.push({
            request,
            response: null,
            error: {
              name: error instanceof Error ? error.constructor.name : typeof error,
              message: redactKeys(error instanceof Error ? error.message : String(error)),
              status: typeof status === "number" ? status : null,
              signalAborted: options?.signal?.aborted ?? false,
            },
            elapsedMs: Date.now() - started,
          });
          throw error;
        }
      },
    },
  };
}

function failure(error: unknown, signal: AbortSignal | undefined): NonNullable<RawExchange["error"]> {
  const status = (error as { status?: unknown }).status;
  return {
    name: error instanceof Error ? error.constructor.name : typeof error,
    message: redactKeys(error instanceof Error ? error.message : String(error)),
    status: typeof status === "number" ? status : null,
    signalAborted: signal?.aborted ?? false,
  };
}

/** Anthropic SDK 클라이언트를 감싸 요청·응답의 원본 모양을 `sink` 에 남긴다. */
export function recordingAnthropic(inner: AnthropicLike, sink: RawExchange[]): AnthropicLike {
  return {
    messages: {
      stream(body, options) {
        const started = Date.now();
        const request = { model: body.model as string | undefined, maxOutputTokens: body.max_tokens, formatType: body.output_config?.format?.type ?? null, timeoutMs: options?.timeout ?? null };
        const stream = inner.messages.stream(body, options);
        return {
          async finalMessage() {
            try {
              const message = await stream.finalMessage();
              sink.push({
                request,
                response: { model: message.model, status: message.stop_reason ?? null, incompleteReason: null, outputTypes: message.content.map((block) => block.type), usage: message.usage ?? null },
                error: null,
                elapsedMs: Date.now() - started,
              });
              return message;
            } catch (error) {
              sink.push({ request, response: null, error: failure(error, options?.signal), elapsedMs: Date.now() - started });
              throw error;
            }
          },
        };
      },
    },
  };
}

/** Gemini SDK 클라이언트를 감싸 요청·응답의 원본 모양을 `sink` 에 남긴다. */
export function recordingGemini(inner: GeminiLike, sink: RawExchange[]): GeminiLike {
  return {
    models: {
      async generateContent(params) {
        const started = Date.now();
        const request = {
          model: params.model as string | undefined,
          maxOutputTokens: params.config?.maxOutputTokens,
          formatType: params.config?.responseMimeType ?? null,
          // 시간 제한은 중단 신호로만 걸어 요청에 실리지 않는다.
          timeoutMs: null,
        };
        try {
          const response = await inner.models.generateContent(params);
          const candidate = response.candidates?.[0];
          sink.push({
            request,
            response: {
              model: response.modelVersion ?? "",
              status: candidate?.finishReason === undefined ? null : String(candidate.finishReason),
              incompleteReason: response.promptFeedback?.blockReason === undefined ? null : String(response.promptFeedback.blockReason),
              outputTypes: (candidate?.content?.parts ?? []).map((part) => (part.thought ? "thought" : typeof part.text === "string" ? "text" : "other")),
              usage: response.usageMetadata ?? null,
            },
            error: null,
            elapsedMs: Date.now() - started,
          });
          return response;
        } catch (error) {
          sink.push({ request, response: null, error: failure(error, params.config?.abortSignal), elapsedMs: Date.now() - started });
          throw error;
        }
      },
    },
  };
}

/** Scope 3 분류·추천, 번역, 표 추출이 쓰는 스키마 꼴(리터럴 유니온, 열거형, nullable, 길이 제한, strict 객체)을 한데 모은 것. */
export const strictProbeSchema = z.strictObject({
  category: z.union([z.literal(1), z.literal(2), z.literal("other")]),
  confidence: z.enum(["high", "medium", "low"]),
  note: z.string().nullable(),
  keywords_en: z.array(z.string().min(1).max(60)).max(8),
  cells: z.array(z.strictObject({ id: z.string(), value: z.number().nullable() })).max(3),
});

/** strict 가 아닌 `z.object` (docpipe 의 번역·그린워싱 스키마 꼴). */
export const looseProbeSchema = z.object({ items: z.array(z.object({ id: z.string(), text: z.string() })) });

export const PROBE_CHECKS = ["ping", "structured_strict", "structured_loose", "truncated", "timeout", "cache"] as const;
export type ProbeCheckName = (typeof PROBE_CHECKS)[number];

export interface ProbeCheck {
  name: ProbeCheckName;
  /** 이 확인이 기대한 결과 종류. */
  expected: LlmResultKind;
  results: { kind: LlmResultKind; detail: string; attempts: number; costUsd: number; usage: unknown }[];
  pass: boolean;
}

export interface ProbeReport {
  route: string;
  checks: ProbeCheck[];
  raw: RawExchange[];
}

function summarize(result: LlmResult<unknown>): ProbeCheck["results"][number] {
  let detail: string;
  switch (result.kind) {
    case "success":
      detail = result.text;
      break;
    case "schema_mismatch":
      detail = `${result.issues.map((issue) => `${issue.path}: ${issue.message}`).join("; ")} | ${result.rawText}`;
      break;
    case "refusal":
      detail = result.text;
      break;
    case "truncated":
      detail = result.partialText;
      break;
    case "provider_error":
      detail = `status=${String(result.status)} retryable=${String(result.retryable)} ${result.message}`;
      break;
    case "timeout":
      detail = result.message;
      break;
    case "budget_exceeded":
      detail = `상한 ${result.limitUsd} USD, 누적 ${result.spentUsd} USD`;
      break;
  }
  return { kind: result.kind, detail, attempts: result.meta.attempts.length, costUsd: result.meta.totalCostUsd, usage: result.meta.attempts.map((attempt) => attempt.usage) };
}

const LONG_QUESTION = "1부터 400까지의 정수를 쉼표로 구분해 전부 나열해 줘.";

/** 캐시 최소 길이를 넘는 고정 앞부분. `nonce` 가 다르면 앞부분이 달라져 첫 호출이 캐시에 걸리지 않는다. */
export function cachePadding(nonce: string): string {
  const lines = [`nonce ${nonce}`];
  for (let i = 1; i <= 260; i++) lines.push(`${i}. This is filler line number ${i} of the fixed prefix used only to exceed the minimum cacheable length.`);
  return lines.join("\n");
}

export interface ProbeOptions {
  client: LlmClient;
  route: string;
  tags: Tags;
  /** `recordingOpenAI`·`recordingAnthropic`·`recordingGemini` 가 채우는 목록. 보고에 그대로 싣는다. */
  raw: RawExchange[];
  only?: readonly ProbeCheckName[];
  nonce: string;
  timeoutProbeMs?: number;
}

/**
 * 실제 공급사에 작은 호출 몇 개를 보내 어댑터의 가정을 확인한다. 돈이 드는 명령이다.
 * 각 확인은 기대한 결과 종류가 나왔는지만 판정하고, 원본 응답의 모양은 `raw` 로 남겨 사람이 읽는다.
 */
export async function runProbe(options: ProbeOptions): Promise<ProbeReport> {
  const { client, route, tags } = options;
  const wanted = options.only ?? PROBE_CHECKS;
  const checks: ProbeCheck[] = [];
  const run = async (name: ProbeCheckName, expected: LlmResultKind, calls: (() => Promise<LlmResult<unknown>>)[]): Promise<void> => {
    if (!wanted.includes(name)) return;
    const results: ProbeCheck["results"] = [];
    for (const call of calls) results.push(summarize(await call()));
    checks.push({ name, expected, results, pass: results.every((result) => result.kind === expected) });
  };

  await run("ping", "success", [() => client.call({ prompt: "probe/ping@1", vars: { question: "pong 이라고만 답해." }, route, tags, maxOutputTokens: 200 })]);
  const item = "품목: 스테인리스 강판 2T. 카테고리는 1, 셀은 두 개(id a 는 값 1.5, id b 는 값 모름)로 답해.";
  await run("structured_strict", "success", [
    () => client.call({ prompt: "probe/structured@1", vars: { question: item }, route, tags, schema: strictProbeSchema, schemaName: "probe_strict", maxOutputTokens: 600, maxSchemaRetries: 0 }),
  ]);
  await run("structured_loose", "success", [
    () =>
      client.call({
        prompt: "probe/structured@1",
        vars: { question: '다음 두 항목을 영어로 옮겨 {"items":[{"id","text"}]} 로 답해: [{"id":"a","text":"전력 사용량"},{"id":"b","text":"2023년"}]' },
        route,
        tags,
        schema: looseProbeSchema,
        schemaName: "probe_loose",
        maxOutputTokens: 600,
        maxSchemaRetries: 0,
      }),
  ]);
  await run("truncated", "truncated", [() => client.call({ prompt: "probe/ping@1", vars: { question: LONG_QUESTION }, route, tags, maxOutputTokens: 16 })]);
  await run("timeout", "timeout", [
    () => client.call({ prompt: "probe/ping@1", vars: { question: LONG_QUESTION }, route, tags, maxOutputTokens: 4000, timeoutMs: options.timeoutProbeMs ?? 400 }),
  ]);
  const cached = () => client.call({ prompt: "probe/cache@1", vars: { padding: cachePadding(options.nonce), question: "ok 라고만 답해." }, route, tags, maxOutputTokens: 200 });
  await run("cache", "success", [cached, cached]);

  return { route, checks, raw: options.raw };
}
