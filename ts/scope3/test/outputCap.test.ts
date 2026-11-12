import { AnthropicProvider, FilePromptStore, GeminiProvider, LlmClient, MemorySink, MockProvider, OpenAIProvider, defaultConfigPaths, loadConfig } from "llm";
import { describe, expect, it } from "vitest";
import { promptRoot } from "../src/cli.js";
import { CLASSIFY_MAX_OUTPUT_TOKENS, FactorStore, RECOMMEND_MAX_OUTPUT_TOKENS, runPipeline } from "../src/index.js";
import { classifyRequest, recommendRequest } from "../src/domain/llmSteps.js";
import { TAGS, classification, harness, recommendation, row, writeSyntheticFactors } from "./helpers.js";

// 공급사를 부르지 않는다. 실제 모델 설정(data/llm/models.json)으로 요청만 조립한다(dry-run).
const config = loadConfig(defaultConfigPaths({ synthetic: true }));
const client = new LlmClient({
  config,
  providers: { openai: new OpenAIProvider(), anthropic: new AnthropicProvider(), gemini: new GeminiProvider(), mock: new MockProvider() },
  prompts: new FilePromptStore(promptRoot()),
  log: new MemorySink(),
});
const candidates = FactorStore.load(writeSyntheticFactors()).candidates({ category: 1, basis: "physical" });
const input = row({ itemName: "합성 강판" });

function caps(route: string) {
  return {
    classify: client.dryRun(classifyRequest(input, route, TAGS)).estimate.maxOutputTokens,
    recommend: client.dryRun(recommendRequest(input, 1, "physical", candidates, route, TAGS)).estimate.maxOutputTokens,
  };
}

describe("분류·추천 호출의 출력 상한", () => {
  it("단계의 상한은 답의 상한이다: 분류 400, 추천 800", () => {
    expect(CLASSIFY_MAX_OUTPUT_TOKENS).toBe(400);
    expect(RECOMMEND_MAX_OUTPUT_TOKENS).toBe(800);
    expect(classifyRequest(input, "mock", TAGS).maxOutputTokens).toBe(400);
    expect(recommendRequest(input, 1, "physical", candidates, "mock", TAGS).maxOutputTokens).toBe(800);
  });

  it("추론 토큰이 상한을 같이 쓰는 gpt-6-luna 에는 모델 설정의 여유분이 더해져 나간다", () => {
    const reserve = config.models["gpt-6-luna"]?.reasoningOutputReserveTokens;
    expect(reserve).toBe(2000);
    expect(caps("light-openai")).toEqual({ classify: 2400, recommend: 2800 });
    // 공급사 고유 요청 본문에도 그 값이 실린다.
    expect(client.dryRun(classifyRequest(input, "light-openai", TAGS)).request).toMatchObject({ model: "gpt-6-luna", max_output_tokens: 2400 });
  });

  it("추론 여유분이 없는 모델(Haiku, Gemini Flash-Lite, 모의)의 상한은 커지지 않는다", () => {
    for (const route of ["light", "light-anthropic", "light-gemini", "light-gemini-35", "mock"]) {
      expect(caps(route), route).toEqual({ classify: 400, recommend: 800 });
    }
    for (const key of ["claude-haiku-4-5", "gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "mock-synthetic"]) {
      expect(config.models[key]?.reasoningOutputReserveTokens, key).toBeUndefined();
    }
  });

  it("파이프라인이 공급사에 보내는 요청에 그 상한이 실린다", async () => {
    const { mock, pipeline } = harness();
    mock.enqueueText(classification(1, ["steel"])).enqueueText(recommendation(["syn:steel-t"]));
    await runPipeline([input], pipeline);
    expect(mock.calls.map((call) => call.maxOutputTokens)).toEqual([400, 800]);
  });
});
