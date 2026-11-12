import { describe, expect, it } from "vitest";
import { ConfigError, HeuristicTokenEstimator, computeCostUsd, defaultConfigPaths, loadConfig, type Usage } from "../src/index.js";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const config = loadConfig(defaultConfigPaths({ synthetic: false }));
const pricing = (key: string) => {
  const model = config.models[key];
  if (!model) throw new Error(`설정에 ${key} 가 없다`);
  return model.pricing;
};
const usage = (inputTokens: number, outputTokens: number, cacheReadTokens = 0, cacheWriteTokens = 0): Usage => ({
  inputTokens,
  outputTokens,
  cacheReadTokens,
  cacheWriteTokens,
});

// 손계산은 data/llm/models.json 의 단가(USD/MTok, 2026-10-01 확인)로 했다.
describe("비용 계산 — 설정 파일 단가로 손계산한 사례", () => {
  it("Haiku 4.5: 입력 10,000 + 출력 2,000 + 캐시 읽기 50,000 + 캐시 쓰기 8,000", () => {
    // 10,000×1 + 2,000×5 + 50,000×0.1 + 8,000×1.25 = 10,000 + 10,000 + 5,000 + 10,000 = 35,000 → $0.035
    expect(computeCostUsd(usage(10_000, 2_000, 50_000, 8_000), pricing("claude-haiku-4-5"))).toBe(0.035);
  });

  it("Sonnet 4.6: 입력 120,000 + 출력 4,000", () => {
    // 120,000×3 + 4,000×15 = 360,000 + 60,000 = 420,000 → $0.42
    expect(computeCostUsd(usage(120_000, 4_000), pricing("claude-sonnet-4-6"))).toBe(0.42);
  });

  it("Sonnet 4.6: 캐시 읽기 2,000,000 (1M 을 넘겨도 장문 가산이 없다)", () => {
    // 2,000,000×0.3 = 600,000 → $0.6
    expect(computeCostUsd(usage(0, 0, 2_000_000), pricing("claude-sonnet-4-6"))).toBe(0.6);
  });

  it("GPT-6 Luna 단문 구간: 입력 200,000 + 출력 1,000", () => {
    // 200,000×0.1 + 1,000×0.5 = 20,000 + 500 = 20,500 → $0.0205
    expect(computeCostUsd(usage(200_000, 1_000), pricing("gpt-6-luna"))).toBe(0.0205);
  });

  it("GPT-6 Luna 장문 구간(입력 272K 초과): 입력 300,000 + 출력 1,000", () => {
    // 300,000×0.2 + 1,000×0.75 = 60,000 + 750 = 60,750 → $0.06075
    expect(computeCostUsd(usage(300_000, 1_000), pricing("gpt-6-luna"))).toBe(0.06075);
  });

  it("GPT-6 Luna: 경계 272,000 은 단문, 캐시 토큰을 합쳐 넘으면 장문", () => {
    // 272,000×0.1 = 27,200 → $0.0272
    expect(computeCostUsd(usage(272_000, 0), pricing("gpt-6-luna"))).toBe(0.0272);
    // 합계 272,001 > 272,000 → 장문: 200,000×0.2 + 72,001×0.02 = 40,000 + 1,440.02 = 41,440.02 → $0.04144002
    expect(computeCostUsd(usage(200_000, 0, 72_001), pricing("gpt-6-luna"))).toBe(0.04144002);
  });

  it("GPT-6.1 Sol: 입력 1,000 + 출력 500 + 캐시 읽기 2,000", () => {
    // 1,000×2 + 500×10 + 2,000×0.1 = 2,000 + 5,000 + 200 = 7,200 → $0.0072
    expect(computeCostUsd(usage(1_000, 500, 2_000), pricing("gpt-6.1-sol"))).toBe(0.0072);
  });

  it("보고되지 않은(null) 캐시 토큰은 0 으로 계산한다", () => {
    expect(computeCostUsd({ inputTokens: 1_000_000, outputTokens: 0, cacheReadTokens: null, cacheWriteTokens: null }, pricing("claude-haiku-4-5"))).toBe(1);
  });
});

describe("토큰 어림", () => {
  const estimator = new HeuristicTokenEstimator(config.tokenEstimate);
  it("라틴 4자 = 1토큰, 한글 1자 = 1토큰으로 어림한다", () => {
    expect(estimator.estimate("abcdefgh")).toBe(2);
    expect(estimator.estimate("가나다")).toBe(3);
    expect(estimator.estimate("가나 ab")).toBe(3); // 2 + 3/4 → 올림 3
    expect(estimator.estimate("")).toBe(0);
  });
});

describe("설정 파일", () => {
  it("실제 모델은 모두 출처를 갖고, 가상 모델은 기본 설정에 섞이지 않는다", () => {
    expect(Object.keys(config.models).sort()).toEqual(["claude-haiku-4-5", "claude-sonnet-4-6", "gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gpt-6-luna", "gpt-6.1-sol"]);
    for (const model of Object.values(config.models)) {
      expect(model.sourceRefs.length).toBeGreaterThan(0);
      expect(model.synthetic).toBeUndefined();
    }
    const withSynthetic = loadConfig(defaultConfigPaths({ synthetic: true }));
    expect(withSynthetic.models["mock-synthetic"]?.synthetic).toBe(true);
  });

  it("출처 없는 모델, 가상 표시 없는 가상 모델, 없는 모델을 가리키는 경로를 거부한다", () => {
    const dir = mkdtempSync(join(tmpdir(), "llm-config-"));
    const model = {
      provider: "p",
      apiModelId: "m",
      label: "m",
      pricing: { input: 1, output: 1, cacheWrite: 1, cacheRead: 1 },
      contextWindowTokens: 10,
      maxOutputTokens: 10,
      minCacheTokens: 10,
      pdf: { maxPagesPerRequest: null, maxRequestMb: 1 },
      structuredOutput: true,
      lifecycle: { status: "s", retirement: null, note: "" },
      sourceRefs: [] as string[],
      notes: [],
    };
    const tokenEstimate = { note: "", latinCharsPerToken: 4, latinBasis: "", cjkCharsPerToken: 1, cjkBasis: "" };
    const write = (name: string, body: unknown) => {
      const path = join(dir, name);
      writeFileSync(path, JSON.stringify(body));
      return path;
    };
    const file = { checkedOn: "2026-10-01", note: "", sources: { a: "https://example.com/a" }, tokenEstimate, routes: {} };

    expect(() => loadConfig([write("no-source.json", { ...file, models: { m: model } })])).toThrow(ConfigError);
    expect(() => loadConfig([write("bad-ref.json", { ...file, models: { m: { ...model, sourceRefs: ["zzz"] } } })])).toThrow("sources 에 없다");
    expect(() => loadConfig([write("unmarked.synthetic.json", { ...file, synthetic: true, models: { m: model } })])).toThrow("synthetic 표시가 없다");
    expect(() =>
      loadConfig([write("bad-route.json", { ...file, models: { m: { ...model, sourceRefs: ["a"] } }, routes: { r: ["nope"] } })]),
    ).toThrow("없는 모델 nope");
    expect(() => loadConfig([write("extra-key.json", { ...file, models: { m: { ...model, sourceRefs: ["a"], price: 1 } } })])).toThrow(ConfigError);
  });
});
