import { describe, expect, it } from "vitest";
import {
  BudgetGuard,
  EMBEDDING_PROMPT_ID,
  EmbeddingClient,
  LiveSetupError,
  MemorySink,
  MockEmbeddingProvider,
  OpenAIEmbeddingProvider,
  batchInputs,
  computeEmbeddingCostUsd,
  defaultConfigPaths,
  liveEmbeddingProviders,
  loadConfig,
  syntheticEmbedding,
  type BudgetLimit,
  type OpenAIEmbeddingsLike,
} from "../src/index.js";
import { TAGS } from "./helpers.js";

// 실제 공급사로 가는 호출은 없다. 임베딩은 가상(synthetic) 구현이고 키와 토큰 수도 가상 값이다.
const config = loadConfig(defaultConfigPaths({ synthetic: true }));
const DIMENSIONS = 64;

function harness(limits: BudgetLimit[] = []) {
  const mock = new MockEmbeddingProvider(DIMENSIONS);
  const log = new MemorySink();
  const budget = new BudgetGuard(limits);
  return { mock, log, budget, client: new EmbeddingClient({ config, providers: { mock }, log, budget }) };
}

describe("임베딩 호출 계층", () => {
  it("경로 이름으로 부르고 벡터, 토큰 수, 비용, 호출 기록을 남긴다", async () => {
    const { client, mock, log, budget } = harness();
    const result = await client.embed({ route: "mock", inputs: ["합성 강판", "synthetic steel sheet"], tags: TAGS });
    expect(result.kind).toBe("success");
    if (result.kind !== "success") return;
    expect(result.vectors).toHaveLength(2);
    expect(result.vectors[0]).toHaveLength(DIMENSIONS);
    // 가상 토큰 수는 낱말 수(2 + 3), 가상 단가는 1 USD/MTok.
    expect(result.meta).toMatchObject({ model: "mock-embedding-synthetic", provider: "mock", dimensions: DIMENSIONS, requests: 1, inputTokens: 5, totalCostUsd: 0.000005 });
    expect(mock.calls).toEqual([{ apiModelId: "mock-embedding-synthetic", inputs: ["합성 강판", "synthetic steel sheet"] }]);
    expect(budget.spent({ module: TAGS.module })).toBe(0.000005);
    expect(log.records).toHaveLength(1);
    expect(log.records[0]).toMatchObject({ promptId: EMBEDDING_PROMPT_ID, provider: "mock", model: "mock-embedding-synthetic", inputTokens: 5, outputTokens: 0, costUsd: 0.000005, resultKind: "success", embeddingInputs: 2, module: TAGS.module });
    expect(log.records[0]?.body).toBeUndefined();
  });

  it("입력이 요청 한도를 넘으면 여러 요청으로 나눈다", async () => {
    expect(batchInputs(["a", "b", "c"], { maxInputsPerRequest: 2, maxTokensPerRequest: 100 })).toEqual([["a", "b"], ["c"]]);
    expect(batchInputs(["aaaa", "bbbb", "cc"], { maxInputsPerRequest: 10, maxTokensPerRequest: 6 })).toEqual([["aaaa"], ["bbbb", "cc"]]);
    const { client, mock, log } = harness();
    // 가상 모델의 요청당 입력 한도는 100개다.
    const inputs = Array.from({ length: 230 }, (_, i) => `품목 ${i}`);
    const result = await client.embed({ route: "mock", inputs, tags: TAGS });
    expect(result.kind).toBe("success");
    expect(mock.calls.map((call) => call.inputs.length)).toEqual([100, 100, 30]);
    expect(result.kind === "success" && result.vectors).toHaveLength(230);
    expect(result.meta.requests).toBe(3);
    expect(log.records).toHaveLength(3);
  });

  it("누적 비용이 상한에 닿았으면 공급사를 부르지 않고 budget_exceeded 로 끝난다", async () => {
    const { client, mock, log, budget } = harness([{ scope: { module: TAGS.module }, limitUsd: 0.000004 }]);
    const first = await client.embed({ route: "mock", inputs: ["합성 강판 다섯 낱말 입력"], tags: TAGS });
    expect(first.kind).toBe("success");
    const second = await client.embed({ route: "mock", inputs: ["다음 입력"], tags: TAGS });
    expect(second).toMatchObject({ kind: "budget_exceeded", limitUsd: 0.000004, spentUsd: 0.000005 });
    expect(mock.calls).toHaveLength(1);
    expect(log.records.map((record) => record.resultKind)).toEqual(["success", "budget_exceeded"]);
    // 생성 호출과 같은 상한을 탄다: 같은 BudgetGuard 에 쌓인다.
    expect(budget.spent({})).toBe(0.000005);
  });

  it("공급사 오류와 시간 초과를 빈 벡터로 삼키지 않는다", async () => {
    const { client, mock, log } = harness();
    mock.enqueue({ kind: "error", status: 500, retryable: true, message: "가상 오류 (synthetic)" });
    expect(await client.embed({ route: "mock", inputs: ["합성 강판"], tags: TAGS })).toMatchObject({ kind: "provider_error", status: 500, message: "가상 오류 (synthetic)" });
    mock.enqueue({ kind: "timeout", message: "가상 시간 초과 (synthetic)" });
    expect((await client.embed({ route: "mock", inputs: ["합성 강판"], tags: TAGS })).kind).toBe("timeout");
    expect(log.records.map((record) => [record.resultKind, record.costUsd])).toEqual([["provider_error", null], ["timeout", null]]);
  });

  it("벡터 수나 차원이 설정과 다른 응답은 오류다 (비용은 남는다)", async () => {
    const { client, mock, budget } = harness();
    mock.enqueue({ kind: "completed", vectors: [[1, 0, 0]], usage: { inputTokens: 10 } });
    const result = await client.embed({ route: "mock", inputs: ["합성 강판"], tags: TAGS });
    expect(result).toMatchObject({ kind: "provider_error", meta: { inputTokens: 10 } });
    expect(result.kind === "provider_error" && result.message).toContain("차원");
    expect(budget.spent({})).toBe(0.00001);
  });

  it("설정에 없는 임베딩 경로는 거부한다", async () => {
    const { client } = harness();
    await expect(client.embed({ route: "nope", inputs: ["a"], tags: TAGS })).rejects.toThrow("임베딩 경로 nope");
  });

  it("가상 임베딩은 같은 낱말을 나눠 가진 글끼리 가깝다", () => {
    const dot = (a: number[], b: number[]) => a.reduce((sum, value, i) => sum + value * (b[i] as number), 0);
    const steel = syntheticEmbedding("synthetic steel sheet", DIMENSIONS);
    expect(dot(steel, syntheticEmbedding("steel sheet", DIMENSIONS))).toBeGreaterThan(dot(steel, syntheticEmbedding("legal services", DIMENSIONS)));
  });
});

describe("임베딩 모델 설정", () => {
  it("실제 임베딩 모델에는 출처가 있고, 비용은 입력 토큰 × 단가다", () => {
    const real = loadConfig(defaultConfigPaths({ synthetic: false }));
    const model = real.embeddingModels[real.embeddingRoutes["light-openai"] as string];
    expect(model).toMatchObject({ provider: "openai" });
    expect(model?.sourceRefs.length).toBeGreaterThan(0);
    expect(real.embeddingRoutes.mock).toBeUndefined();
    expect(computeEmbeddingCostUsd(1_000_000, { pricing: { input: 0.5 } })).toBe(0.5);
  });
});

describe("OpenAI 임베딩 어댑터 (가짜 SDK 클라이언트)", () => {
  const KEY_SYNTHETIC = "key-synthetic";

  it("응답의 index 로 입력 순서를 되찾고 prompt_tokens 를 입력 토큰으로 돌려준다", async () => {
    const bodies: unknown[] = [];
    const fake: OpenAIEmbeddingsLike = {
      embeddings: {
        create(body) {
          bodies.push(body);
          return Promise.resolve({ object: "list", model: "embedding-synthetic", data: [{ object: "embedding", index: 1, embedding: [0, 1] }, { object: "embedding", index: 0, embedding: [1, 0] }], usage: { prompt_tokens: 7, total_tokens: 7 } });
        },
      },
    };
    const response = await new OpenAIEmbeddingProvider(fake).embed({ apiModelId: "embedding-synthetic", inputs: ["a", "b"] }, { signal: new AbortController().signal, timeoutMs: 1000 });
    expect(response).toEqual({ kind: "completed", vectors: [[1, 0], [0, 1]], usage: { inputTokens: 7 }, modelId: "embedding-synthetic" });
    expect(bodies).toEqual([{ model: "embedding-synthetic", input: ["a", "b"], encoding_format: "float" }]);
  });

  it("클라이언트가 없거나 SDK 가 던지면 오류로 돌려준다", async () => {
    const options = { signal: new AbortController().signal, timeoutMs: 1000 };
    expect(await new OpenAIEmbeddingProvider().embed({ apiModelId: "m", inputs: ["a"] }, options)).toMatchObject({ kind: "error", retryable: false });
    const throwing: OpenAIEmbeddingsLike = { embeddings: { create: () => Promise.reject(new Error("가상 오류 sk-abcd1234 (synthetic)")) } };
    const response = await new OpenAIEmbeddingProvider(throwing).embed({ apiModelId: "m", inputs: ["a"] }, options);
    expect(response).toMatchObject({ kind: "error" });
    expect(JSON.stringify(response)).not.toContain("sk-abcd1234");
  });

  it("키가 없거나 경로가 다른 공급사의 것이면 SDK 클라이언트를 만들지 않는다", () => {
    const real = loadConfig(defaultConfigPaths({ synthetic: true }));
    const made: string[] = [];
    const deps = (env: Record<string, string | undefined>) => ({ env, openaiEmbeddingsClient: (apiKey: string) => (made.push(apiKey), { embeddings: { create: () => Promise.reject(new Error("부르지 않는다")) } }) });
    expect(() => liveEmbeddingProviders(real, { provider: "openai", route: "light-openai" }, deps({}))).toThrow(LiveSetupError);
    expect(() => liveEmbeddingProviders(real, { provider: "openai", route: "light-openai" }, deps({ OPENAI_API_KEY: " " }))).toThrow("OPENAI_API_KEY");
    expect(() => liveEmbeddingProviders(real, { provider: "openai", route: "mock" }, deps({ OPENAI_API_KEY: KEY_SYNTHETIC }))).toThrow(LiveSetupError);
    expect(() => liveEmbeddingProviders(real, { provider: "openai", route: "nope" }, deps({ OPENAI_API_KEY: KEY_SYNTHETIC }))).toThrow(LiveSetupError);
    expect(made).toEqual([]);
    expect(Object.keys(liveEmbeddingProviders(real, { provider: "openai", route: "light-openai" }, deps({ OPENAI_API_KEY: KEY_SYNTHETIC })))).toEqual(["openai"]);
    expect(made).toEqual([KEY_SYNTHETIC]);
  });
});
