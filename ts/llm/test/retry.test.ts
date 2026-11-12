import { ApiError, type GenerateContentResponse } from "@google/genai";
import { describe, expect, it } from "vitest";
import {
  BudgetGuard,
  ConfigError,
  DEFAULT_PROVIDER_RETRY,
  FilePromptStore,
  GeminiProvider,
  LlmClient,
  MemorySink,
  MockProvider,
  defaultConfigPaths,
  loadConfig,
  retryDelayMs,
  throttled,
  type BudgetLimit,
  type GeminiLike,
  type LlmClientOptions,
  type RetryPolicy,
} from "../src/index.js";
import { retryInfoDelayMs } from "../src/providers/gemini.js";
import { TAGS, USAGE_SYNTHETIC, harness, promptRoot } from "./helpers.js";

// 실제 공급사로 가는 호출은 없다. SDK 클라이언트 자리에 가짜를 넣고, 대기는 주입한 가짜 sleep 이 받아 적기만 한다.
// 오류 본문과 토큰 수는 전부 가상(synthetic) 값이다. 429 본문의 모양만 2026-10-02 실제 응답을 따랐다.

const base = { prompt: "t/plain@1", vars: { question: "분류해 줘" }, tags: TAGS };
const POLICY: RetryPolicy = { maxRetries: 2, baseDelayMs: 1_000, maxDelayMs: 60_000 };

function quota429(retryDelay?: string): ApiError {
  const details: unknown[] = [{ "@type": "type.googleapis.com/google.rpc.QuotaFailure", violations: [] }];
  if (retryDelay !== undefined) details.push({ "@type": "type.googleapis.com/google.rpc.RetryInfo", retryDelay });
  return new ApiError({ status: 429, message: JSON.stringify({ error: { code: 429, message: "quota (synthetic)", status: "RESOURCE_EXHAUSTED", details } }) });
}
const apiError = (status: number) => new ApiError({ status, message: JSON.stringify({ error: { code: status, message: "synthetic" } }) });

const OK = {
  modelVersion: "gemini-3.5-flash-lite",
  candidates: [{ finishReason: "STOP", content: { role: "model", parts: [{ text: "pong" }] } }],
  usageMetadata: { promptTokenCount: 1000, candidatesTokenCount: 100, totalTokenCount: 1100 },
} as unknown as GenerateContentResponse;
// gemini-3.5-flash-lite 설정 단가(입력 0.30, 출력 2.50 USD/100만 토큰): 1000×0.30 + 100×2.50 = 550 → 0.00055.
const OK_COST = 0.00055;

/** 심은 순서대로 던지거나 돌려주는 가짜 Gemini SDK 클라이언트. */
function geminiHarness(replies: (GenerateContentResponse | Error)[], options: { limits?: BudgetLimit[]; client?: Partial<LlmClientOptions> } = {}) {
  let sent = 0;
  const sdk: GeminiLike = {
    models: {
      generateContent() {
        const reply = replies[sent];
        sent += 1;
        if (reply === undefined) return Promise.reject(new Error("가짜 클라이언트에 심은 응답이 없다"));
        return reply instanceof Error ? Promise.reject(reply) : Promise.resolve(reply);
      },
    },
  };
  const log = new MemorySink();
  const budget = new BudgetGuard(options.limits ?? []);
  const sleeps: number[] = [];
  const client = new LlmClient({
    config: loadConfig(defaultConfigPaths({ synthetic: true })),
    providers: { gemini: new GeminiProvider(sdk) },
    prompts: new FilePromptStore(promptRoot()),
    log,
    budget,
    sleep: async (ms) => {
      sleeps.push(ms);
    },
    ...options.client,
  });
  return { client, log, budget, sleeps, sent: () => sent };
}
const gemini = { ...base, route: "light-gemini-35" };

describe("Gemini 일시 오류의 같은 모델 재시도 (기본 설정)", () => {
  it("기본 설정은 gemini 만 다시 부른다: 2회, 1초부터 두 배, 상한 60초", () => {
    expect(DEFAULT_PROVIDER_RETRY).toEqual({ gemini: { maxRetries: 2, baseDelayMs: 1_000, maxDelayMs: 60_000 } });
  });

  it("429·503 뒤에 다시 불러 성공한다. 공급사가 알려 준 대기를 따르고, 시도마다 호출 기록이 남는다", async () => {
    const { client, log, budget, sleeps, sent } = geminiHarness([quota429("7s"), apiError(503), OK]);

    const result = await client.call(gemini);

    expect(result).toMatchObject({ kind: "success", data: "pong" });
    expect(sent()).toBe(3);
    // 첫 대기는 429 본문의 retryDelay, 둘째는 알려 준 값이 없어 1초 × 2^1.
    expect(sleeps).toEqual([7_000, 2_000]);
    expect(result.meta.attempts.map((attempt) => [attempt.kind, attempt.providerRetry])).toEqual([
      ["provider_error", undefined],
      ["provider_error", 1],
      ["success", 2],
    ]);
    expect(log.records.map((record) => [record.attempt, record.resultKind, record.errorStatus, record.providerRetry, record.previousErrorStatus, record.retryWaitMs])).toEqual([
      [0, "provider_error", 429, undefined, undefined, undefined],
      [1, "provider_error", 503, 1, 429, 7_000],
      [2, "success", undefined, 2, 503, 2_000],
    ]);
    expect(log.records[0]?.retryAfterMs).toBe(7_000);
    expect("retryAfterMs" in (log.records[1] as object)).toBe(false);
    expect(new Set(log.records.map((record) => record.callId)).size).toBe(1);
  });

  it("재시도된 호출의 비용은 한 번만 세어진다: 결과, 상한 누적, 호출 기록 합이 같다", async () => {
    const { client, log, budget } = geminiHarness([apiError(503), OK]);

    const result = await client.call(gemini);

    expect(result.kind).toBe("success");
    expect(result.meta.attempts.map((attempt) => attempt.costUsd)).toEqual([null, OK_COST]);
    expect(result.meta.totalCostUsd).toBe(OK_COST);
    expect(budget.spent({})).toBe(OK_COST);
    expect(log.records.map((record) => record.costUsd)).toEqual([null, OK_COST]);
  });

  it("재시도를 다 쓰면 지금처럼 provider_error 로 끝난다 (3번 부르고 더 부르지 않는다)", async () => {
    const { client, log, sleeps, sent } = geminiHarness([apiError(503), apiError(503), apiError(503), OK]);

    const result = await client.call(gemini);

    expect(result).toMatchObject({ kind: "provider_error", status: 503, retryable: true });
    expect(sent()).toBe(3);
    expect(sleeps).toEqual([1_000, 2_000]);
    expect(result.meta.attempts).toHaveLength(3);
    expect(result.meta.totalCostUsd).toBe(0);
    expect(log.records.map((record) => record.providerRetry)).toEqual([undefined, 1, 2]);
  });

  for (const status of [400, 401, 403, 404]) {
    it(`재시도 불가 오류 ${status} 는 한 번만 부른다`, async () => {
      const { client, log, sleeps, sent } = geminiHarness([apiError(status), OK]);

      const result = await client.call(gemini);

      expect(result).toMatchObject({ kind: "provider_error", status, retryable: false });
      expect(sent()).toBe(1);
      expect(sleeps).toEqual([]);
      expect(log.records).toHaveLength(1);
    });
  }

  it("공급사가 대기 상한(60초)보다 긴 대기를 요구하면 다시 부르지 않고, 요구한 대기는 기록에 남는다", async () => {
    const { client, log, sleeps, sent } = geminiHarness([quota429("3600s"), OK]);

    const result = await client.call(gemini);

    expect(result).toMatchObject({ kind: "provider_error", status: 429 });
    expect(sent()).toBe(1);
    expect(sleeps).toEqual([]);
    expect(log.records[0]).toMatchObject({ errorStatus: 429, retryAfterMs: 3_600_000 });
  });

  it("시간 초과와 거부·잘림은 다시 부르지 않는다", async () => {
    const abort = Object.assign(new Error("aborted"), { name: "AbortError" });
    const truncated = { ...OK, candidates: [{ finishReason: "MAX_TOKENS", content: { role: "model", parts: [{ text: "p" }] } }] } as unknown as GenerateContentResponse;
    for (const [reply, kind] of [[abort, "timeout"], [truncated, "truncated"]] as const) {
      const { client, sleeps, sent } = geminiHarness([reply, OK]);
      expect((await client.call(gemini)).kind).toBe(kind);
      expect(sent()).toBe(1);
      expect(sleeps).toEqual([]);
    }
  });

  it("providerRetry 를 비우면 gemini 도 다시 부르지 않는다. 잘못된 설정은 예외다", async () => {
    const { client, sent } = geminiHarness([apiError(503), OK], { client: { providerRetry: {} } });
    expect((await client.call(gemini)).kind).toBe("provider_error");
    expect(sent()).toBe(1);
    expect(() => geminiHarness([], { client: { providerRetry: { gemini: { maxRetries: -1, baseDelayMs: 0, maxDelayMs: 0 } } } })).toThrow(ConfigError);
    expect(() => geminiHarness([], { client: { providerRetry: { gemini: { maxRetries: 1, baseDelayMs: 10, maxDelayMs: 5 } } } })).toThrow(ConfigError);
  });
});

describe("재시도와 비용 상한·호출 간격·대체 호출", () => {
  const retrying = (extra: Partial<LlmClientOptions> = {}) => {
    const sleeps: number[] = [];
    const made = harness({
      limits: [{ scope: { tenant: TAGS.tenant }, limitUsd: 0.00015 }],
      client: { providerRetry: { mock: POLICY }, sleep: async (ms) => void sleeps.push(ms), ...extra },
    });
    return { ...made, sleeps };
  };

  it("기본 설정에서 gemini 가 아닌 공급사(모의, SDK 가 스스로 다시 부르는 openai·anthropic)는 호출 계층이 다시 부르지 않는다", async () => {
    const { client, mock } = harness();
    mock.enqueue({ kind: "error", status: 503, retryable: true, message: "과부하" }).enqueueText("ok");
    expect((await client.call({ ...base, route: "mock" })).kind).toBe("provider_error");
    expect(mock.calls).toHaveLength(1);

    for (const [name, route] of [["openai", "light-openai"], ["anthropic", "light-anthropic"]] as const) {
      const provider = new MockProvider(name).enqueue({ kind: "error", status: 429, retryable: true, message: "한도" }).enqueueText("ok");
      const other = new LlmClient({ config: loadConfig(defaultConfigPaths({ synthetic: true })), providers: { [name]: provider }, prompts: new FilePromptStore(promptRoot()), log: new MemorySink() });
      expect((await other.call({ ...base, route })).kind).toBe("provider_error");
      expect(provider.calls).toHaveLength(1);
    }
  });

  it("상한에 닿으면 다시 부르지 않는다: 재시도 직전에도 상한을 본다", async () => {
    const { client, mock, log, budget } = retrying();
    // 첫 시도가 실패하는 사이에 같은 꼬리표의 다른 호출이 상한을 채웠다.
    mock.enqueue(() => {
      budget.record(TAGS, 0.0002);
      return { kind: "error", status: 503, retryable: true, message: "과부하" };
    });
    mock.enqueueText("ok", USAGE_SYNTHETIC);

    const result = await client.call({ ...base, route: "mock" });

    expect(result).toMatchObject({ kind: "budget_exceeded", limitUsd: 0.00015, spentUsd: 0.0002 });
    expect(mock.calls).toHaveLength(1);
    expect(mock.pending).toBe(1);
    expect(log.records.map((record) => [record.resultKind, record.providerRetry, record.previousErrorStatus])).toEqual([
      ["provider_error", undefined, undefined],
      ["budget_exceeded", 1, 503],
    ]);
  });

  it("재시도와 스키마 재시도가 섞여도 과금된 시도만, 한 번씩 세어진다", async () => {
    const { client, mock, log, budget } = retrying({ budget: new BudgetGuard() });
    const failed = { kind: "error", status: 503, retryable: true, message: "과부하" } as const;
    mock.enqueue(failed).enqueueText("JSON 아님", USAGE_SYNTHETIC).enqueue(failed).enqueueText('{"a":1}', USAGE_SYNTHETIC);
    const { z } = await import("zod");

    const result = await client.call({ ...base, route: "mock", schema: z.object({ a: z.number() }) });

    expect(result.kind).toBe("success");
    expect(result.meta.attempts.map((attempt) => [attempt.kind, attempt.providerRetry, attempt.costUsd])).toEqual([
      ["provider_error", undefined, null],
      ["schema_mismatch", 1, 0.0001],
      ["provider_error", undefined, null],
      ["success", 1, 0.0001],
    ]);
    expect(result.meta.totalCostUsd).toBe(0.0002);
    const logged = log.records.reduce((sum, record) => sum + (record.costUsd ?? 0), 0);
    expect(logged).toBeCloseTo(0.0002, 12);
    expect(log.records.map((record) => record.attempt)).toEqual([0, 1, 2, 3]);
  });

  it("다시 부르는 호출도 호출 간격 조절을 지난다", async () => {
    const waits: number[] = [];
    const clock = { now: () => 0, sleep: async (ms: number) => void waits.push(ms) };
    const mock = new MockProvider();
    const failed = { kind: "error", status: 503, retryable: true, message: "과부하" } as const;
    mock.enqueue(failed).enqueue(failed).enqueueText("ok");
    const client = new LlmClient({
      config: loadConfig(defaultConfigPaths({ synthetic: true })),
      providers: { mock: throttled(mock, 60, clock) },
      prompts: new FilePromptStore(promptRoot()),
      log: new MemorySink(),
      providerRetry: { mock: { maxRetries: 2, baseDelayMs: 0, maxDelayMs: 0 } },
      sleep: async () => {},
    });

    expect((await client.call({ ...base, route: "mock" })).kind).toBe("success");
    expect(mock.calls).toHaveLength(3);
    // 분당 60회 → 시작 간격 1초. 가짜 시계가 멈춰 있어 둘째·셋째 호출은 1초, 2초를 기다린다.
    expect(waits).toEqual([1_000, 2_000]);
  });

  it("순서: 같은 모델을 다시 부른 뒤에도 실패하면 경로의 다음 모델로 넘어간다", async () => {
    const { client, mock, mockB, sleeps } = retrying({ budget: new BudgetGuard(), providerRetry: { mock: { ...POLICY, maxRetries: 1 } } });
    const failed = { kind: "error", status: 503, retryable: true, message: "과부하" } as const;
    mock.enqueue(failed).enqueue(failed);
    mockB.enqueueText("ok", USAGE_SYNTHETIC);

    const result = await client.call({ ...base, route: "mock-with-fallback" });

    expect(result.kind).toBe("success");
    expect(result.meta.attempts.map((attempt) => [attempt.provider, attempt.kind, attempt.providerRetry])).toEqual([
      ["mock", "provider_error", undefined],
      ["mock", "provider_error", 1],
      ["mock-b", "success", undefined],
    ]);
    expect(sleeps).toEqual([1_000]);
  });
});

describe("대기 시간 계산과 RetryInfo 읽기", () => {
  it("알려 준 대기가 없으면 두 배씩 늘리고 상한에서 자른다. 횟수를 다 쓰면 null", () => {
    const policy: RetryPolicy = { maxRetries: 4, baseDelayMs: 1_000, maxDelayMs: 3_000 };
    expect([0, 1, 2, 3, 4].map((index) => retryDelayMs(policy, index, undefined))).toEqual([1_000, 2_000, 3_000, 3_000, null]);
    expect(retryDelayMs(policy, 0, 2_500)).toBe(2_500);
    expect(retryDelayMs(policy, 0, 0)).toBe(0);
    expect(retryDelayMs(policy, 0, 3_001)).toBeNull();
    expect(retryDelayMs({ maxRetries: 0, baseDelayMs: 0, maxDelayMs: 0 }, 0, undefined)).toBeNull();
  });

  it("오류 본문의 google.rpc.RetryInfo.retryDelay 를 ms 로 읽고, 없거나 읽을 수 없으면 undefined 다", () => {
    expect(retryInfoDelayMs(quota429("55s").message)).toBe(55_000);
    expect(retryInfoDelayMs(quota429("0.5s").message)).toBe(500);
    expect(retryInfoDelayMs(quota429().message)).toBeUndefined();
    expect(retryInfoDelayMs(quota429("soon").message)).toBeUndefined();
    expect(retryInfoDelayMs("본문이 JSON 이 아니다")).toBeUndefined();
    expect(retryInfoDelayMs('{"error":{"details":"x"}}')).toBeUndefined();
    expect(retryInfoDelayMs("null")).toBeUndefined();
  });
});
