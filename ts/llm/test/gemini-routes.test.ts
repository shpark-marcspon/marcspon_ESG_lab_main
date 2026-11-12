import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ApiError, type GenerateContentParameters, type GenerateContentResponse } from "@google/genai";
import { describe, expect, it } from "vitest";
import { main } from "../src/cli.js";
import { computeCostUsd, defaultConfigPaths, loadConfig, resolveRoute, type CallRecord, type GeminiLike, type LiveDeps } from "../src/index.js";

// 실제 공급사로 가는 호출은 없다. SDK 클라이언트 자리에 가짜를 넣는다. 키, 토큰 수, 응답은 전부 가상(synthetic) 값이다.
const KEY_SYNTHETIC = "key-synthetic";
const CONFIG = loadConfig(defaultConfigPaths({ synthetic: false }));

/** 경로 이름 → 그 경로가 불러야 하는 API 모델 ID. 설정 파일(`data/llm/models.json`)과 맞아야 한다. */
const GEMINI_ROUTES = [
  { route: "light-gemini", apiModelId: "gemini-3.1-flash-lite" },
  { route: "light-gemini-35", apiModelId: "gemini-3.5-flash-lite" },
] as const;

type Reply = (params: GenerateContentParameters) => GenerateContentResponse | Error;

function fakeDeps(reply: Reply) {
  const seen: GenerateContentParameters[] = [];
  const client: GeminiLike = {
    models: {
      generateContent(params) {
        seen.push(params);
        const result = reply(params);
        return result instanceof Error ? Promise.reject(result) : Promise.resolve(result);
      },
    },
  };
  const sleeps: number[] = [];
  const deps: LiveDeps = { env: { GEMINI_API_KEY: KEY_SYNTHETIC }, geminiClient: () => client, sleep: async (ms) => void sleeps.push(ms) };
  return { seen, deps, sleeps };
}

/** 입력 1000, 답 1000 토큰에 생각 토큰을 `thoughts` 만큼 얹은 가상 응답. `thoughts` 를 주지 않으면 필드가 없다. */
const answered =
  (thoughts?: number): Reply =>
  (params) =>
    ({
      modelVersion: params.model,
      candidates: [{ finishReason: "STOP", content: { role: "model", parts: [{ text: "pong" }] } }],
      usageMetadata: { promptTokenCount: 1000, candidatesTokenCount: 1000, ...(thoughts === undefined ? {} : { thoughtsTokenCount: thoughts }), totalTokenCount: 2000 + (thoughts ?? 0) },
    }) as unknown as GenerateContentResponse;

async function call(route: string, reply: Reply) {
  const fake = fakeDeps(reply);
  const log = join(mkdtempSync(join(tmpdir(), "llm-gemini-routes-")), "calls.jsonl");
  const code = await main(["call", "--prompt", "example/hello@1", "--provider", "gemini", "--route", route, "--limit-usd", "0.1", "--log", log], () => undefined, fake.deps);
  const records = readFileSync(log, "utf8").trim().split("\n").map((line) => JSON.parse(line) as CallRecord);
  return { code, records, seen: fake.seen, sleeps: fake.sleeps };
}

describe("Gemini 경로들 (설정 읽기와 조립)", () => {
  for (const { route, apiModelId } of GEMINI_ROUTES) {
    it(`${route}: 설정이 모델 하나(${apiModelId})를 가리키고 단가·한도·출처가 있다`, () => {
      const models = resolveRoute(CONFIG, route);
      expect(models.map((model) => [model.provider, model.apiModelId])).toEqual([["gemini", apiModelId]]);
      const model = models[0];
      expect(model?.pricing.input).toBeGreaterThan(0);
      expect(model?.pricing.output).toBeGreaterThan(model?.pricing.input ?? Infinity);
      expect(model?.pricing.cacheRead).toBeLessThan(model?.pricing.input ?? 0);
      expect(model?.maxOutputTokens).toBeGreaterThan(0);
      expect(model?.structuredOutput).toBe(true);
      expect(model?.sourceRefs).toContain("gemini_pricing");
    });

    it(`${route}: CLI 가 그 모델 ID 로 부르고 호출 기록의 비용은 그 모델의 단가다`, async () => {
      const { code, records, seen } = await call(route, answered());
      expect(code).toBe(0);
      expect(seen.map((params) => params.model)).toEqual([apiModelId]);
      const model = resolveRoute(CONFIG, route)[0];
      expect(records).toHaveLength(1);
      expect(records[0]).toMatchObject({ provider: "gemini", apiModelId, responseModelId: apiModelId, resultKind: "success" });
      expect(records[0]?.costUsd).toBe(computeCostUsd({ inputTokens: 1000, outputTokens: 1000, cacheReadTokens: 0, cacheWriteTokens: 0 }, model?.pricing as never));
    });
  }

  it("경로마다 모델이 달라 같은 사용량의 비용이 다르다", () => {
    const usage = { inputTokens: 1000, outputTokens: 1000, cacheReadTokens: 0, cacheWriteTokens: 0 };
    const costs = GEMINI_ROUTES.map(({ route }) => computeCostUsd(usage, resolveRoute(CONFIG, route)[0]?.pricing as never));
    expect(new Set(costs).size).toBe(GEMINI_ROUTES.length);
  });
});

describe("호출 기록의 생각 토큰과 오류 상태 코드", () => {
  const ROUTE = "light-gemini-35";

  it("생각 토큰은 출력 토큰에 합쳐 과금되고 thinkingTokens 에 따로도 남는다", async () => {
    const { records } = await call(ROUTE, answered(40));
    const model = resolveRoute(CONFIG, ROUTE)[0];
    expect(records[0]).toMatchObject({ outputTokens: 1040, thinkingTokens: 40 });
    expect(records[0]?.costUsd).toBe(computeCostUsd({ inputTokens: 1000, outputTokens: 1040, cacheReadTokens: 0, cacheWriteTokens: 0 }, model?.pricing as never));
    expect(records[0] && "errorStatus" in records[0]).toBe(false);
  });

  it("공급사가 생각 토큰을 보고하지 않으면 필드를 남기지 않고, 0 을 보고하면 0 으로 남긴다", async () => {
    const absent = await call(ROUTE, answered());
    expect(absent.records[0] && "thinkingTokens" in absent.records[0]).toBe(false);
    const zero = await call(ROUTE, answered(0));
    expect(zero.records[0]).toMatchObject({ outputTokens: 1000, thinkingTokens: 0 });
  });

  it("공급사 오류의 상태 코드가 errorStatus 에 남는다. 상태 없이 실패하면 null 이다", async () => {
    const overloaded = await call(ROUTE, () => new ApiError({ status: 503, message: "과부하 (synthetic)" }));
    expect(overloaded.code).toBe(1);
    expect(overloaded.records[0]).toMatchObject({ resultKind: "provider_error", errorStatus: 503, inputTokens: null, costUsd: null });
    const limited = await call(ROUTE, () => new ApiError({ status: 429, message: "한도 (synthetic)" }));
    expect(limited.records[0]).toMatchObject({ resultKind: "provider_error", errorStatus: 429 });
    const offline = await call(ROUTE, () => new TypeError("fetch failed"));
    expect(offline.records[0]).toMatchObject({ resultKind: "provider_error", errorStatus: null });
    const missing = await call(ROUTE, () => new ApiError({ status: 404, message: "없는 모델 (synthetic)" }));
    expect(missing.records).toHaveLength(1);
    expect(missing.records[0]).toMatchObject({ resultKind: "provider_error", errorStatus: 404 });
  });

  it("call 명령은 일시 오류(503, 429, 연결 실패)를 같은 모델로 2회 다시 부르고 시도마다 기록을 남긴다", async () => {
    for (const [error, status] of [[new ApiError({ status: 503, message: "과부하 (synthetic)" }), 503], [new ApiError({ status: 429, message: "한도 (synthetic)" }), 429], [new TypeError("fetch failed"), null]] as const) {
      const failed = await call(ROUTE, () => error);
      expect(failed.code).toBe(1);
      expect(failed.seen).toHaveLength(3);
      expect(failed.sleeps).toEqual([1_000, 2_000]);
      expect(failed.records.map((record) => [record.attempt, record.errorStatus, record.providerRetry, record.previousErrorStatus])).toEqual([
        [0, status, undefined, undefined],
        [1, status, 1, status],
        [2, status, 2, status],
      ]);
    }
    let sent = 0;
    const recovered = await call(ROUTE, (params) => ((sent += 1) === 1 ? new ApiError({ status: 503, message: "과부하 (synthetic)" }) : answered()(params)));
    expect(recovered.code).toBe(0);
    expect(recovered.records.map((record) => [record.resultKind, record.providerRetry])).toEqual([["provider_error", undefined], ["success", 1]]);
  });

  it("probe 명령은 일시 오류를 다시 부르지 않는다 (응답을 한 번씩 그대로 본다)", async () => {
    const fake = fakeDeps(() => new ApiError({ status: 503, message: "과부하 (synthetic)" }));
    const lines: string[] = [];
    const code = await main(["probe", "--provider", "gemini", "--route", ROUTE, "--limit-usd", "0.1", "--only", "ping"], (line) => lines.push(line), fake.deps);
    expect(code).toBe(1);
    expect(fake.seen).toHaveLength(1);
    expect(fake.sleeps).toEqual([]);
    expect(lines.join("\n")).toContain("provider_error");
  });
});
