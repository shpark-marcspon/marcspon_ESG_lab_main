import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { defaultConfigPaths, loadConfig, type AnthropicLike, type CallRecord, type GeminiLike, type LiveDeps, type ModelConfig } from "llm";
import { describe, expect, it } from "vitest";
import { main } from "../src/cli.js";
import type { EvalReport } from "../src/evaluate.js";

// 실제 공급사로 가는 호출은 없다. SDK 클라이언트 자리에 가짜를 넣어 CLI 의 배선만 본다. 키, 토큰 수, 응답은 전부 가상(synthetic) 값이다.
const KEY_SYNTHETIC = "key-synthetic";
const EXAMPLES = "../data/scope3-examples";
const CONFIG = loadConfig(defaultConfigPaths({ synthetic: true }));
const leadModel = (route: string) => CONFIG.models[CONFIG.routes[route]?.[0] as string] as ModelConfig;

type Schema = { properties?: { picks?: { items?: { properties?: { factor_id?: { enum?: string[] } } } } } } | undefined;

/** 분류에는 카테고리 1, 추천에는 스키마가 허용한 첫 후보 id 를 답한다. */
function reply(schema: Schema): string {
  const first = schema?.properties?.picks?.items?.properties?.factor_id?.enum?.[0];
  if (first === undefined) return JSON.stringify({ category: 1, keywords_en: ["steel"], confidence: "high", reason: "가짜 응답 (synthetic)" });
  return JSON.stringify({ picks: [{ factor_id: first, reason: "가짜 응답 (synthetic)", confidence: "low" }], none_suitable: false });
}

/** 한 번에 입력 1000 + 출력 1000 토큰을 보고하는 가짜 Gemini·Anthropic 클라이언트. */
function fakeDeps(env: Record<string, string | undefined>, geminiFailure: (nth: number) => Error | undefined = () => undefined) {
  const keys: string[] = [];
  const models: string[] = [];
  const sleeps: number[] = [];
  const gemini: GeminiLike = {
    models: {
      generateContent(params) {
        models.push(params.model);
        const failure = geminiFailure(models.length);
        if (failure) return Promise.reject(failure);
        return Promise.resolve({
          modelVersion: params.model,
          candidates: [{ finishReason: "STOP", content: { role: "model", parts: [{ text: reply(params.config?.responseJsonSchema as Schema) }] } }],
          usageMetadata: { promptTokenCount: 1000, candidatesTokenCount: 1000, totalTokenCount: 2000 },
        } as unknown as Awaited<ReturnType<GeminiLike["models"]["generateContent"]>>);
      },
    },
  };
  const anthropic: AnthropicLike = {
    messages: {
      stream(body) {
        models.push(body.model);
        const message = {
          id: "msg_synthetic",
          type: "message",
          role: "assistant",
          model: body.model,
          content: [{ type: "text", text: reply(body.output_config?.format?.schema as Schema), citations: null }],
          stop_reason: "end_turn",
          stop_sequence: null,
          usage: { input_tokens: 1000, output_tokens: 1000, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
        };
        return { finalMessage: () => Promise.resolve(message as unknown as Awaited<ReturnType<ReturnType<AnthropicLike["messages"]["stream"]>["finalMessage"]>>) };
      },
    },
  };
  const deps: LiveDeps = { env, geminiClient: (apiKey) => (keys.push(apiKey), gemini), anthropicClient: (apiKey) => (keys.push(apiKey), anthropic), sleep: async (ms) => void sleeps.push(ms) };
  return { keys, models, sleeps, deps };
}

async function run(args: string[], deps: LiveDeps) {
  const lines: string[] = [];
  const code = await main(args, (line) => lines.push(line), deps);
  return { code, output: lines.join("\n") };
}

const VENDORS = [
  { provider: "gemini", route: "light-gemini", keyName: "GEMINI_API_KEY" },
  { provider: "gemini", route: "light-gemini-35", keyName: "GEMINI_API_KEY" },
  { provider: "anthropic", route: "light-anthropic", keyName: "ANTHROPIC_API_KEY" },
] as const;

describe("Anthropic·Gemini 경로 배선 (가짜 SDK 클라이언트)", () => {
  for (const { provider, route, keyName } of VENDORS) {
    const live = ["eval", EXAMPLES, "--provider", provider, "--route", route];

    it(`${provider} ${route}: 키·상한이 없거나 경로가 그 공급사의 것이 아니면 부르지 않는다`, async () => {
      for (const env of [{}, { [keyName]: "" }, { OPENAI_API_KEY: KEY_SYNTHETIC }]) {
        const fake = fakeDeps(env);
        const { code, output } = await run([...live, "--limit-usd", "0.6"], fake.deps);
        expect(code).toBe(2);
        expect(output).toContain(keyName);
        expect(fake.keys).toEqual([]);
      }
      const fake = fakeDeps({ [keyName]: KEY_SYNTHETIC });
      expect((await run(live, fake.deps)).output).toContain("--limit-usd");
      expect((await run(["eval", EXAMPLES, "--provider", provider, "--route", "light-openai", "--limit-usd", "0.6"], fake.deps)).code).toBe(2);
      expect((await run([...live, "--limit-usd", "0.6", "--rpm", "0"], fake.deps)).code).toBe(2);
      expect(fake.models).toEqual([]);
    });

    it(`${provider} ${route}: 키가 있으면 경로의 모델로 끝까지 돌고 비용은 설정 단가로 잡힌다. 상한에 닿으면 멈춘다`, async () => {
      const model = leadModel(route);
      const perCall = (1000 * model.pricing.input + 1000 * model.pricing.output) / 1_000_000;
      const fake = fakeDeps({ [keyName]: KEY_SYNTHETIC });
      const log = join(mkdtempSync(join(tmpdir(), "scope3-vendors-")), "calls.jsonl");
      const { code, output } = await run([...live, "--limit-usd", "0.6", "--limit-rows", "12", "--log", log, "--json"], fake.deps);
      expect(code).toBe(0);
      expect(fake.keys).toEqual([KEY_SYNTHETIC]);
      expect(fake.models.length).toBeGreaterThan(0);
      expect(new Set(fake.models)).toEqual(new Set([model.apiModelId]));
      const summary = JSON.parse(output) as { provider: string; syntheticProvider: boolean; route: string; model: string; report: EvalReport };
      expect(summary).toMatchObject({ provider, syntheticProvider: false, route, model: model.key });
      expect(summary.report.llm.attempts).toBe(fake.models.length);
      expect(summary.report.llm.costUsd).toBeCloseTo(fake.models.length * perCall, 8);
      const records = readFileSync(log, "utf8").trim().split("\n").map((line) => JSON.parse(line) as CallRecord);
      expect(records.every((record) => record.provider === provider && record.responseModelId === model.apiModelId && record.body === undefined)).toBe(true);
      expect(readFileSync(log, "utf8")).not.toContain(KEY_SYNTHETIC);

      const capped = fakeDeps({ [keyName]: KEY_SYNTHETIC });
      const limited = await run([...live, "--limit-usd", String(perCall / 2), "--limit-rows", "12", "--json"], capped.deps);
      expect(limited.code).toBe(0);
      expect(capped.models).toHaveLength(1);
      const { report } = JSON.parse(limited.output) as { report: EvalReport };
      expect(Object.keys(report.llmFailures).some((key) => key.endsWith(":budget_exceeded"))).toBe(true);
    });
  }
});

describe("Gemini 일시 오류 (가짜 SDK 클라이언트, 대기는 가짜)", () => {
  const live = ["eval", EXAMPLES, "--provider", "gemini", "--route", "light-gemini-35", "--limit-usd", "0.6", "--limit-rows", "3", "--json"];
  // scope3 는 Gemini SDK 에 기대지 않으므로 SDK 의 오류 타입 없이 만들 수 있는 일시 오류(연결 실패)를 쓴다.
  // 어댑터는 fetch 의 TypeError 를 상태 코드 없는 재시도 가능 오류로 옮긴다. 429·503 은 `ts/llm/test/retry.test.ts` 가 본다.
  const overloaded = () => new TypeError("fetch failed (synthetic)");
  const model = leadModel("light-gemini-35");
  const perCall = (1000 * model.pricing.input + 1000 * model.pricing.output) / 1_000_000;

  it("일시 오류가 한 번 섞여도 행이 실패하지 않는다. 다시 부른 시도는 호출 기록에 남고 비용은 성공한 시도만 세어진다", async () => {
    const clean = fakeDeps({ GEMINI_API_KEY: KEY_SYNTHETIC });
    const baseline = JSON.parse((await run(live, clean.deps)).output) as { report: EvalReport };
    const fake = fakeDeps({ GEMINI_API_KEY: KEY_SYNTHETIC }, (nth) => (nth === 2 ? overloaded() : undefined));
    const log = join(mkdtempSync(join(tmpdir(), "scope3-retry-")), "calls.jsonl");
    const { code, output } = await run([...live, "--log", log], fake.deps);
    expect(code).toBe(0);
    const { report } = JSON.parse(output) as { report: EvalReport };
    expect(report.llmFailures).toEqual({});
    expect(report.decisionStatus).toEqual(baseline.report.decisionStatus);
    expect(report.llm.calls).toBe(baseline.report.llm.calls);
    expect(report.llm.attempts).toBe(report.llm.calls + 1);
    expect(fake.models).toHaveLength(report.llm.attempts);
    expect(report.llm.costUsd).toBeCloseTo(report.llm.calls * perCall, 8);
    expect(report.llm.costUsd).toBe(baseline.report.llm.costUsd);
    expect(fake.sleeps).toEqual([1_000]);
    const records = readFileSync(log, "utf8").trim().split("\n").map((line) => JSON.parse(line) as CallRecord);
    expect(records.filter((record) => record.resultKind === "provider_error").map((record) => record.errorStatus)).toEqual([null]);
    expect(records.filter((record) => record.providerRetry !== undefined).map((record) => [record.resultKind, record.providerRetry, record.previousErrorStatus, record.retryWaitMs])).toEqual([["success", 1, null, 1_000]]);
  });

  it("일시 오류가 계속되면 묶음마다 3번 부른 뒤 지금처럼 실패로 남는다 (빈 결과나 성공으로 바뀌지 않는다)", async () => {
    const fake = fakeDeps({ GEMINI_API_KEY: KEY_SYNTHETIC }, () => overloaded());
    const { code, output } = await run(live, fake.deps);
    expect(code).toBe(0);
    const { report } = JSON.parse(output) as { report: EvalReport };
    const failedGroups = report.llmFailures["classify:provider_error"] ?? 0;
    expect(failedGroups).toBeGreaterThan(0);
    expect(Object.keys(report.llmFailures)).toEqual(["classify:provider_error"]);
    expect(report.llm.calls).toBe(failedGroups);
    expect(report.llm.attempts).toBe(failedGroups * 3);
    expect(fake.models).toHaveLength(failedGroups * 3);
    expect(report.llm.costUsd).toBe(0);
  });
});
