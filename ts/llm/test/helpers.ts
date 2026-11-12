import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  BudgetGuard,
  FilePromptStore,
  LlmClient,
  MemorySink,
  MockProvider,
  defaultConfigPaths,
  loadConfig,
  type BudgetLimit,
  type LlmClientOptions,
  type LlmConfig,
  type Usage,
} from "../src/index.js";

export const TAGS = { tenant: "tenant-synthetic", module: "scope3" };

/** 모의 모델(mock-synthetic) 단가로 정확히 0.0001 USD 가 되는 가상 사용량: 60×1 + 20×2 = 100 → 100/1e6. */
export const USAGE_SYNTHETIC: Usage = { inputTokens: 60, outputTokens: 20, cacheReadTokens: 0, cacheWriteTokens: 0 };

const LONG_GUIDE = "용어집 항목. ".repeat(400);

const PROMPTS: Record<string, string> = {
  "t/plain/1.md": "=== system ===\n짧게 답한다.\n\n=== user ===\n{{question}}\n",
  "t/plain/2.md": "=== system ===\n길게 답한다.\n\n=== user ===\n{{question}}\n",
  "t/cached-short/1.md": "=== system cache ===\n짧은 지침.\n\n=== user ===\n{{question}}\n",
  "t/cached-long/1.md": `=== system cache ===\n${LONG_GUIDE}\n\n=== user ===\n{{question}}\n`,
};

export function promptRoot(): string {
  const root = mkdtempSync(join(tmpdir(), "llm-prompts-"));
  for (const [path, body] of Object.entries(PROMPTS)) {
    mkdirSync(join(root, path, ".."), { recursive: true });
    writeFileSync(join(root, path), body);
  }
  return root;
}

/** 가상 설정의 모의 모델 하나에 항목을 덧씌운 설정. 설정 파일은 건드리지 않는다. */
export function configWith(overrides: Partial<LlmConfig["models"][string]>, key = "mock-synthetic"): LlmConfig {
  const config = loadConfig(defaultConfigPaths({ synthetic: true }));
  const model = config.models[key];
  if (!model) throw new Error(`가상 설정에 모델 ${key} 가 없다`);
  return { ...config, models: { ...config.models, [key]: { ...model, ...overrides } } };
}

export function harness(options: { limits?: BudgetLimit[]; storeBodies?: boolean; config?: LlmConfig; client?: Partial<LlmClientOptions> } = {}) {
  const mock = new MockProvider();
  const mockB = new MockProvider("mock-b");
  const log = new MemorySink();
  const budget = new BudgetGuard(options.limits ?? []);
  const client = new LlmClient({
    config: options.config ?? loadConfig(defaultConfigPaths({ synthetic: true })),
    providers: { mock, "mock-b": mockB },
    prompts: new FilePromptStore(promptRoot()),
    log,
    budget,
    ...(options.storeBodies === undefined ? {} : { storeBodies: options.storeBodies }),
    ...options.client,
  });
  return { client, mock, mockB, log, budget };
}
