import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { defaultConfigPaths, loadConfig, type AnthropicLike, type CallRecord, type GeminiLike, type LiveDeps, type ModelConfig } from "llm";
import { describe, expect, it } from "vitest";
import { main } from "../src/cli.js";
import { fixtureDocument, tempDir } from "./helpers.js";

// 실제 공급사로 가는 호출은 없다. SDK 클라이언트 자리에 가짜를 넣어 CLI 의 배선만 본다. 키, 토큰 수, 응답은 전부 가상(synthetic) 값이다.
const KEY_SYNTHETIC = "key-synthetic";
const CONFIG = loadConfig(defaultConfigPaths({ synthetic: true }));
const leadModel = (route: string) => CONFIG.models[CONFIG.routes[route]?.[0] as string] as ModelConfig;

/** 번역 요청의 항목 목록을 원문 그대로 돌려준다 (숫자가 보존된다). */
function echo(userText: string): string {
  return JSON.stringify({ items: JSON.parse(userText.slice(userText.indexOf("[\n"))) as unknown });
}

/** 한 번에 입력 1000 + 출력 1000 토큰을 보고하는 가짜 Gemini·Anthropic 클라이언트. */
function fakeDeps(env: Record<string, string | undefined>) {
  const keys: string[] = [];
  const models: string[] = [];
  const gemini: GeminiLike = {
    models: {
      generateContent(params) {
        models.push(params.model);
        const contents = params.contents as { parts: { text: string }[] }[];
        return Promise.resolve({
          modelVersion: params.model,
          candidates: [{ finishReason: "STOP", content: { role: "model", parts: [{ text: echo(contents[0]?.parts[0]?.text ?? "") }] } }],
          usageMetadata: { promptTokenCount: 1000, candidatesTokenCount: 1000, totalTokenCount: 2000 },
        } as unknown as Awaited<ReturnType<GeminiLike["models"]["generateContent"]>>);
      },
    },
  };
  const anthropic: AnthropicLike = {
    messages: {
      stream(body) {
        models.push(body.model);
        const content = body.messages[0]?.content as { text: string }[];
        const message = {
          id: "msg_synthetic",
          type: "message",
          role: "assistant",
          model: body.model,
          content: [{ type: "text", text: echo(content[0]?.text ?? ""), citations: null }],
          stop_reason: "end_turn",
          stop_sequence: null,
          usage: { input_tokens: 1000, output_tokens: 1000, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
        };
        return { finalMessage: () => Promise.resolve(message as unknown as Awaited<ReturnType<ReturnType<AnthropicLike["messages"]["stream"]>["finalMessage"]>>) };
      },
    },
  };
  const deps: LiveDeps = { env, geminiClient: (apiKey) => (keys.push(apiKey), gemini), anthropicClient: (apiKey) => (keys.push(apiKey), anthropic) };
  return { keys, models, deps };
}

function documentFile(): string {
  const path = join(tempDir(), "document.json");
  writeFileSync(path, JSON.stringify(fixtureDocument()));
  return path;
}

async function run(argv: string[], deps: LiveDeps) {
  const lines: string[] = [];
  const code = await main(argv, (line) => lines.push(line), () => undefined, deps);
  return { code, text: lines.join("\n") };
}

interface Summary {
  provider: string;
  synthetic: false | string;
  plannedChunks: number;
  chunks: { total: number; done: number; failed: number; skipped: number };
  providerCallsThisRun: number;
  costUsd?: number;
  failures: { kind: string }[];
}

const VENDORS = [
  { provider: "gemini", route: "light-gemini", keyName: "GEMINI_API_KEY" },
  { provider: "gemini", route: "light-gemini-35", keyName: "GEMINI_API_KEY" },
  { provider: "anthropic", route: "light-anthropic", keyName: "ANTHROPIC_API_KEY" },
] as const;

describe("Anthropic·Gemini 경로 배선 (가짜 SDK 클라이언트)", () => {
  for (const { provider, route, keyName } of VENDORS) {
    const live = ["--provider", provider, "--route", route];

    it(`${provider} ${route}: 키·상한이 없거나 경로가 그 공급사의 것이 아니면 부르지 않는다`, async () => {
      const path = documentFile();
      for (const env of [{}, { [keyName]: " " }, { OPENAI_API_KEY: KEY_SYNTHETIC }]) {
        const fake = fakeDeps(env);
        const { code, text } = await run(["translate", path, ...live, "--limit-usd", "1", "--out", tempDir()], fake.deps);
        expect(code).toBe(2);
        expect(text).toContain(keyName);
        expect(fake.keys).toEqual([]);
      }
      const fake = fakeDeps({ [keyName]: KEY_SYNTHETIC });
      const noLimit = await run(["translate", path, ...live], fake.deps);
      expect(noLimit.code).toBe(2);
      expect(noLimit.text).toContain("--limit-usd");
      expect((await run(["translate", path, "--provider", provider, "--route", "light-openai", "--limit-usd", "1"], fake.deps)).code).toBe(2);
      expect((await run(["translate", path, ...live, "--limit-usd", "1", "--rpm", "0"], fake.deps)).code).toBe(2);
      expect(fake.models).toEqual([]);
    });

    it(`${provider} ${route}: translate 가 경로의 모델로 끝까지 돌고 비용은 설정 단가로 잡힌다. 상한에 닿으면 남은 조각은 budget_exceeded 다`, async () => {
      const model = leadModel(route);
      const perCall = (1000 * model.pricing.input + 1000 * model.pricing.output) / 1_000_000;
      const fake = fakeDeps({ [keyName]: KEY_SYNTHETIC });
      const out = tempDir();
      const { code, text } = await run(["translate", documentFile(), ...live, "--limit-usd", "1", "--budget-tokens", "400", "--out", out], fake.deps);
      expect(code).toBe(0);
      const summary = JSON.parse(text) as Summary;
      expect(summary).toMatchObject({ provider, synthetic: false, chunks: { failed: 0 } });
      expect(summary.plannedChunks).toBeGreaterThan(2);
      expect(summary.providerCallsThisRun).toBe(summary.plannedChunks);
      expect(summary.costUsd).toBeCloseTo(summary.plannedChunks * perCall, 6);
      expect(fake.keys).toEqual([KEY_SYNTHETIC]);
      expect(new Set(fake.models)).toEqual(new Set([model.apiModelId]));
      const records = readFileSync(join(out, "calls.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line) as CallRecord);
      expect(records).toHaveLength(summary.plannedChunks);
      expect(records.every((record) => record.provider === provider && record.responseModelId === model.apiModelId && record.body === undefined)).toBe(true);
      expect(readFileSync(join(out, "calls.jsonl"), "utf8")).not.toContain(KEY_SYNTHETIC);

      const capped = fakeDeps({ [keyName]: KEY_SYNTHETIC });
      const limited = await run(["translate", documentFile(), ...live, "--limit-usd", String(perCall / 2), "--budget-tokens", "400", "--concurrency", "1", "--out", tempDir()], capped.deps);
      expect(limited.code).toBe(3);
      expect(capped.models).toHaveLength(1);
      expect(new Set((JSON.parse(limited.text) as Summary).failures.map((failure) => failure.kind))).toEqual(new Set(["budget_exceeded"]));
    });
  }
});
