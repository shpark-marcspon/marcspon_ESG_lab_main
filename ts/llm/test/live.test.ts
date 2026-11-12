import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type Anthropic from "@anthropic-ai/sdk";
import type { GenerateContentParameters, GenerateContentResponse } from "@google/genai";
import type OpenAI from "openai";
import { describe, expect, it } from "vitest";
import { main } from "../src/cli.js";
import {
  LiveSetupError,
  computeCostUsd,
  defaultConfigPaths,
  liveProviders,
  loadConfig,
  redactKeys,
  requireLimitUsd,
  type AnthropicLike,
  type CallRecord,
  type GeminiLike,
  type LiveDeps,
  type ModelConfig,
  type OpenAILike,
} from "../src/index.js";

// 실제 공급사로 가는 호출은 없다. SDK 클라이언트 자리에 가짜를 넣어 배선만 본다. 키와 토큰 수는 전부 가상(synthetic) 값이다.
const KEY_SYNTHETIC = "key-synthetic";
const ROUTE = ["--provider", "openai", "--route", "light-openai"];

type Response = OpenAI.Responses.Response;

/** 입력 1000 + 출력 1000 토큰짜리 가상 응답. gpt-6-luna 단가(설정 파일)로 한 번에 0.0006 USD 다. */
function fakeResponse(body: { model?: unknown }, text = "pong"): Response {
  return {
    id: "resp_synthetic",
    model: `${String(body.model)}-snapshot-synthetic`,
    status: "completed",
    incomplete_details: null,
    output: [{ type: "message", id: "msg", role: "assistant", status: "completed", content: [{ type: "output_text", text, annotations: [] }] }],
    usage: { input_tokens: 1000, input_tokens_details: { cached_tokens: 0, cache_write_tokens: 0 }, output_tokens: 1000, output_tokens_details: { reasoning_tokens: 0 }, total_tokens: 2000 },
  } as unknown as Response;
}

function fakeDeps(env: Record<string, string | undefined> = { OPENAI_API_KEY: KEY_SYNTHETIC }) {
  const keys: string[] = [];
  const bodies: { model?: unknown }[] = [];
  const client: OpenAILike = {
    responses: {
      create(body) {
        bodies.push(body);
        return Promise.resolve(fakeResponse(body));
      },
    },
  };
  return {
    keys,
    bodies,
    deps: {
      env,
      openaiClient: (apiKey: string) => {
        keys.push(apiKey);
        return client;
      },
    },
  };
}

async function run(args: string[], deps: LiveDeps) {
  const lines: string[] = [];
  const code = await main(args, (line) => lines.push(line), deps);
  return { code, output: lines.join("\n") };
}

describe("실제 공급사 배선 (가짜 SDK 클라이언트)", () => {
  it("키가 없으면 SDK 클라이언트를 만들지 않고 명시적 오류로 끝난다", async () => {
    for (const env of [{}, { OPENAI_API_KEY: "" }, { OPENAI_API_KEY: "  " }]) {
      const fake = fakeDeps(env);
      const { code, output } = await run(["call", "--prompt", "example/hello@1", ...ROUTE, "--limit-usd", "0.1"], fake.deps);
      expect(code).toBe(2);
      expect(output).toContain("OPENAI_API_KEY");
      expect(fake.keys).toEqual([]);
      expect(fake.bodies).toEqual([]);
    }
  });

  it("키가 있으면 환경에서 받은 키로 클라이언트를 만들고 경로의 모델로 부른다. 기록에 응답 모델 ID 가 남는다", async () => {
    const fake = fakeDeps();
    const log = join(mkdtempSync(join(tmpdir(), "llm-live-")), "calls.jsonl");
    const { code, output } = await run(["call", "--prompt", "example/hello@1", ...ROUTE, "--limit-usd", "0.1", "--log", log], fake.deps);
    expect(code).toBe(0);
    expect(fake.keys).toEqual([KEY_SYNTHETIC]);
    expect(fake.bodies.map((body) => body.model)).toEqual(["gpt-6-luna"]);
    expect(output).not.toContain(KEY_SYNTHETIC);
    const records = readFileSync(log, "utf8").trim().split("\n").map((line) => JSON.parse(line) as CallRecord);
    expect(records).toHaveLength(1);
    expect(records[0]).toMatchObject({ provider: "openai", model: "gpt-6-luna", apiModelId: "gpt-6-luna", responseModelId: "gpt-6-luna-snapshot-synthetic", resultKind: "success", costUsd: 0.0006 });
    expect(records[0]?.body).toBeUndefined();
    expect(readFileSync(log, "utf8")).not.toContain(KEY_SYNTHETIC);
  });

  it("경로와 상한을 명시하지 않으면 부르지 않는다", async () => {
    const fake = fakeDeps();
    const noLimit = await run(["call", "--prompt", "example/hello@1", ...ROUTE], fake.deps);
    expect(noLimit.code).toBe(2);
    expect(noLimit.output).toContain("--limit-usd");
    const noRoute = await run(["call", "--prompt", "example/hello@1", "--provider", "openai", "--limit-usd", "0.1"], fake.deps);
    expect(noRoute.code).toBe(2);
    expect(noRoute.output).toContain("--route");
    expect(fake.bodies).toEqual([]);
  });

  it("다른 공급사의 모델이 섞인 경로와 지원하지 않는 공급사는 거부한다", async () => {
    const fake = fakeDeps();
    const mixed = await run(["call", "--prompt", "example/hello@1", "--provider", "openai", "--route", "light", "--limit-usd", "0.1"], fake.deps);
    expect(mixed.code).toBe(2);
    expect(mixed.output).toContain("claude-haiku-4-5");
    const anthropic = await run(["call", "--prompt", "example/hello@1", "--provider", "anthropic", "--route", "light", "--limit-usd", "0.1"], fake.deps);
    expect(anthropic.code).toBe(2);
    expect(fake.bodies).toEqual([]);

    const config = loadConfig(defaultConfigPaths({ synthetic: true }));
    expect(() => liveProviders(config, { provider: "openai", route: "standard" }, fake.deps)).toThrow(LiveSetupError);
    expect(() => liveProviders(config, { provider: "mock", route: "mock" }, fake.deps)).toThrow(LiveSetupError);
    expect(() => requireLimitUsd(undefined)).toThrow(LiveSetupError);
    expect(() => requireLimitUsd("0")).toThrow(LiveSetupError);
    expect(requireLimitUsd("0.2")).toBe(0.2);
  });

  it("probe: 비용 상한이 호출 계층에 전달돼 상한에 닿은 뒤로는 공급사를 부르지 않는다", async () => {
    const fake = fakeDeps();
    // 한 번에 0.0006 USD 이므로 상한 0.0005 는 첫 호출 뒤에 닿는다.
    const { code, output } = await run(["probe", ...ROUTE, "--limit-usd", "0.0005"], fake.deps);
    expect(code).toBe(1);
    expect(fake.bodies).toHaveLength(1);
    const report = JSON.parse(output) as { checks: { name: string; results: { kind: string }[] }[]; raw: unknown[] };
    expect(report.checks[0]).toMatchObject({ name: "ping", results: [{ kind: "success" }] });
    expect(report.checks.slice(1).flatMap((check) => check.results.map((result) => result.kind))).toEqual(Array.from({ length: 6 }, () => "budget_exceeded"));
    expect(report.raw).toHaveLength(1);
  });

  it("probe: 상한이 넉넉하면 여섯 확인을 모두 돌리고 원본 응답 모양을 남긴다", async () => {
    const fake = fakeDeps();
    const { output } = await run(["probe", ...ROUTE, "--limit-usd", "1", "--only", "ping,cache"], fake.deps);
    const report = JSON.parse(output) as { checks: { name: string; pass: boolean }[]; raw: { response: { model: string; status: string } }[] };
    expect(report.checks.map((check) => [check.name, check.pass])).toEqual([["ping", true], ["cache", true]]);
    expect(report.raw).toHaveLength(3);
    expect(report.raw[0]?.response).toMatchObject({ model: "gpt-6-luna-snapshot-synthetic", status: "completed" });
    expect((await run(["probe", ...ROUTE, "--limit-usd", "1", "--only", "없는확인"], fake.deps)).code).toBe(2);
    expect((await run(["probe", "--provider", "mock"], fake.deps)).code).toBe(2);
  });

  it("오류 문구에 섞여 온 키 모양 글자를 가린다", () => {
    expect(redactKeys("Incorrect API key provided: sk-proj-abc*****xyz1. You can find")).toBe("Incorrect API key provided: sk-[가림] You can find");
    expect(redactKeys("risk-free 문장은 그대로")).toBe("risk-free 문장은 그대로");
  });
});

// --- Anthropic, Gemini 경로 ---

const CONFIG = loadConfig(defaultConfigPaths({ synthetic: true }));
const leadModel = (route: string) => CONFIG.models[CONFIG.routes[route]?.[0] as string] as ModelConfig;

/** 입력 1000 + 출력 1000 토큰짜리 가상 응답을 돌려주는 가짜 Gemini·Anthropic 클라이언트. */
function fakeVendorDeps(env: Record<string, string | undefined>) {
  const keys: { provider: string; apiKey: string }[] = [];
  const geminiBodies: GenerateContentParameters[] = [];
  const anthropicBodies: Anthropic.MessageStreamParams[] = [];
  const gemini: GeminiLike = {
    models: {
      generateContent(params) {
        geminiBodies.push(params);
        return Promise.resolve({
          modelVersion: `${params.model}-snapshot-synthetic`,
          candidates: [{ finishReason: "STOP", content: { role: "model", parts: [{ text: "pong" }] } }],
          usageMetadata: { promptTokenCount: 1000, candidatesTokenCount: 1000, totalTokenCount: 2000 },
        } as unknown as GenerateContentResponse);
      },
    },
  };
  const anthropic: AnthropicLike = {
    messages: {
      stream(body) {
        anthropicBodies.push(body);
        return {
          finalMessage: () =>
            Promise.resolve({
              id: "msg_synthetic",
              type: "message",
              role: "assistant",
              model: `${body.model}-snapshot-synthetic`,
              content: [{ type: "text", text: "pong", citations: null }],
              stop_reason: "end_turn",
              stop_sequence: null,
              usage: { input_tokens: 1000, output_tokens: 1000, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
            } as unknown as Anthropic.Message),
        };
      },
    },
  };
  const deps: LiveDeps = {
    env,
    geminiClient: (apiKey) => (keys.push({ provider: "gemini", apiKey }), gemini),
    anthropicClient: (apiKey) => (keys.push({ provider: "anthropic", apiKey }), anthropic),
  };
  return { keys, geminiBodies, anthropicBodies, deps };
}

const VENDORS = [
  { provider: "gemini", route: "light-gemini", keyName: "GEMINI_API_KEY" },
  { provider: "anthropic", route: "light-anthropic", keyName: "ANTHROPIC_API_KEY" },
] as const;

describe("Anthropic·Gemini 실제 공급사 배선 (가짜 SDK 클라이언트)", () => {
  it("키가 없으면 SDK 클라이언트를 만들지 않고 명시적 오류로 끝난다. 다른 공급사의 키는 쓰지 않는다", async () => {
    for (const { provider, route, keyName } of VENDORS) {
      for (const env of [{}, { [keyName]: "" }, { OPENAI_API_KEY: KEY_SYNTHETIC }]) {
        const fake = fakeVendorDeps(env);
        const { code, output } = await run(["call", "--prompt", "example/hello@1", "--provider", provider, "--route", route, "--limit-usd", "0.1"], fake.deps);
        expect(code).toBe(2);
        expect(output).toContain(keyName);
        expect(fake.keys).toEqual([]);
        expect([...fake.geminiBodies, ...fake.anthropicBodies]).toEqual([]);
      }
    }
  });

  it("키가 있으면 그 공급사의 키로 클라이언트를 만들고 경로의 모델로 부른다. 비용은 설정 단가로 계산된다", async () => {
    for (const { provider, route, keyName } of VENDORS) {
      const fake = fakeVendorDeps({ [keyName]: KEY_SYNTHETIC, OPENAI_API_KEY: "다른-키-synthetic" });
      const log = join(mkdtempSync(join(tmpdir(), "llm-live-")), "calls.jsonl");
      const { code, output } = await run(["call", "--prompt", "example/hello@1", "--provider", provider, "--route", route, "--limit-usd", "0.1", "--log", log], fake.deps);
      expect(code).toBe(0);
      expect(fake.keys).toEqual([{ provider, apiKey: KEY_SYNTHETIC }]);
      const model = leadModel(route);
      expect(model.provider).toBe(provider);
      expect([...fake.geminiBodies, ...fake.anthropicBodies].map((body) => body.model)).toEqual([model.apiModelId]);
      expect(output).not.toContain(KEY_SYNTHETIC);
      const records = readFileSync(log, "utf8").trim().split("\n").map((line) => JSON.parse(line) as CallRecord);
      expect(records).toHaveLength(1);
      expect(records[0]).toMatchObject({
        provider,
        model: model.key,
        apiModelId: model.apiModelId,
        responseModelId: `${model.apiModelId}-snapshot-synthetic`,
        resultKind: "success",
        costUsd: computeCostUsd({ inputTokens: 1000, outputTokens: 1000, cacheReadTokens: 0, cacheWriteTokens: 0 }, model.pricing),
      });
      expect(records[0]?.costUsd).toBeGreaterThan(0);
      expect(records[0]?.body).toBeUndefined();
      expect(readFileSync(log, "utf8")).not.toContain(KEY_SYNTHETIC);
    }
  });

  it("경로와 상한을 명시하지 않거나 경로가 그 공급사의 것이 아니면 부르지 않는다", async () => {
    for (const { provider, route, keyName } of VENDORS) {
      const fake = fakeVendorDeps({ [keyName]: KEY_SYNTHETIC });
      const base = ["call", "--prompt", "example/hello@1", "--provider", provider];
      const noLimit = await run([...base, "--route", route], fake.deps);
      expect(noLimit.code).toBe(2);
      expect(noLimit.output).toContain("--limit-usd");
      const noRoute = await run([...base, "--limit-usd", "0.1"], fake.deps);
      expect(noRoute.code).toBe(2);
      expect(noRoute.output).toContain("--route");
      for (const foreign of ["light-openai", "light", provider === "gemini" ? "light-anthropic" : "light-gemini"]) {
        expect((await run([...base, "--route", foreign, "--limit-usd", "0.1"], fake.deps)).code).toBe(2);
      }
      expect((await run([...base, "--route", route, "--limit-usd", "0.1", "--rpm", "0"], fake.deps)).code).toBe(2);
      expect([...fake.geminiBodies, ...fake.anthropicBodies]).toEqual([]);
      expect(fake.keys).toEqual([]);
    }
  });

  it("probe: 비용 상한이 전달돼 상한에 닿은 뒤로는 부르지 않고, 원본 응답 모양(종료 사유, usage)을 남긴다", async () => {
    for (const { provider, route, keyName } of VENDORS) {
      const fake = fakeVendorDeps({ [keyName]: KEY_SYNTHETIC });
      const { code, output } = await run(["probe", "--provider", provider, "--route", route, "--limit-usd", "0.0005"], fake.deps);
      expect(code).toBe(1);
      expect([...fake.geminiBodies, ...fake.anthropicBodies]).toHaveLength(1);
      const report = JSON.parse(output) as { checks: { name: string; results: { kind: string }[] }[]; raw: { request: { model: string }; response: { model: string; status: string; usage: unknown } }[] };
      expect(report.checks[0]).toMatchObject({ name: "ping", results: [{ kind: "success" }] });
      expect(report.checks.slice(1).flatMap((check) => check.results.map((result) => result.kind))).toEqual(Array.from({ length: 6 }, () => "budget_exceeded"));
      expect(report.raw).toHaveLength(1);
      const model = leadModel(route);
      expect(report.raw[0]).toMatchObject({ request: { model: model.apiModelId }, response: { model: `${model.apiModelId}-snapshot-synthetic`, status: provider === "gemini" ? "STOP" : "end_turn" } });
      expect(report.raw[0]?.response.usage).toBeTruthy();
    }
  });
});
