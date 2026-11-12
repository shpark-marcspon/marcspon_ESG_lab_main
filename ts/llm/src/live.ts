import { resolveEmbeddingRoute, resolveRoute, type LlmConfig } from "./config.js";
import type { EmbeddingProvider } from "./embeddings.js";
import { AnthropicProvider, createAnthropicClient, type AnthropicLike } from "./providers/anthropic.js";
import { GeminiProvider, createGeminiClient, type GeminiLike } from "./providers/gemini.js";
import { OpenAIEmbeddingProvider, OpenAIProvider, createOpenAIClient, createOpenAIEmbeddingsClient, type OpenAIEmbeddingsLike, type OpenAILike } from "./providers/openai.js";
import { throttled } from "./throttle.js";
import type { Provider } from "./types.js";

/** 실제 호출을 지원하는 공급사와 그 키가 실리는 환경 변수 이름. */
const KEY_ENV: Record<string, string> = { openai: "OPENAI_API_KEY", anthropic: "ANTHROPIC_API_KEY", gemini: "GEMINI_API_KEY" };

/** 실제 임베딩 호출을 지원하는 공급사. 생성 호출보다 좁다. */
const EMBEDDING_KEY_ENV: Record<string, string> = { openai: "OPENAI_API_KEY" };

/** `--provider` 에 줄 수 있는 실제 공급사 이름. CLI 사용법 문구가 쓴다. */
export const LIVE_PROVIDERS: readonly string[] = Object.keys(KEY_ENV);

/** 실제 공급사 호출을 조립할 수 없다 (키 없음, 지원하지 않는 공급사, 경로 불일치, 상한 없음). 사용법 오류로 다룬다. */
export class LiveSetupError extends Error {
  override name = "LiveSetupError";
}

/**
 * CLI 조립 지점이 넘기는 것. 이 패키지는 `process.env` 를 읽지 않으므로 환경은 호출하는 쪽이 넘긴다.
 * 테스트는 `openaiClient`·`anthropicClient`·`geminiClient` 에 가짜를 넣어 네트워크 없이 배선을 본다.
 */
export interface LiveDeps {
  env: Record<string, string | undefined>;
  openaiClient?: (apiKey: string) => OpenAILike;
  anthropicClient?: (apiKey: string) => AnthropicLike;
  geminiClient?: (apiKey: string) => GeminiLike;
  openaiEmbeddingsClient?: (apiKey: string) => OpenAIEmbeddingsLike;
  /** 재시도 전의 대기. CLI 가 `LlmClient` 에 그대로 넘긴다. 테스트는 실제로 기다리지 않는 것을 넣는다. */
  sleep?: (ms: number) => Promise<void>;
}

export interface LiveSelection {
  provider: string;
  route: string;
  /** 주면 공급사 호출의 시작 간격을 벌려 분당 이 수를 넘지 않게 한다 (분당 한도가 있는 키용). */
  requestsPerMinute?: number;
}

/**
 * 실제 공급사 어댑터를 SDK 클라이언트와 함께 만든다.
 * - 경로의 모든 모델이 고른 공급사의 것이어야 한다. 다른 공급사로 넘어가는 대체 호출이 섞인 경로는 거부한다.
 * - 키가 없거나 비었으면 부르지 않고 `LiveSetupError` 다.
 */
export function liveProviders(config: LlmConfig, selection: LiveSelection, deps: LiveDeps): Record<string, Provider> {
  const keyName = KEY_ENV[selection.provider];
  if (!keyName) throw new LiveSetupError(`공급사 ${selection.provider} 의 실제 호출은 지원하지 않는다. 지원: ${Object.keys(KEY_ENV).join(", ")}`);
  const foreign = resolveRoute(config, selection.route).filter((model) => model.provider !== selection.provider);
  if (foreign.length > 0) {
    throw new LiveSetupError(`경로 ${selection.route} 에 ${selection.provider} 가 아닌 모델이 있다: ${foreign.map((model) => `${model.key} (${model.provider})`).join(", ")}`);
  }
  const apiKey = deps.env[keyName];
  if (apiKey === undefined || apiKey.trim() === "") {
    throw new LiveSetupError(`환경 변수 ${keyName} 가 없다. 실제 공급사는 키 없이 부를 수 없다 (--provider mock 또는 --dry-run 은 키가 필요 없다)`);
  }
  const provider = adapter(selection.provider, apiKey, deps);
  return { [selection.provider]: selection.requestsPerMinute === undefined ? provider : throttled(provider, selection.requestsPerMinute) };
}

function adapter(provider: string, apiKey: string, deps: LiveDeps): Provider {
  switch (provider) {
    case "openai":
      return new OpenAIProvider((deps.openaiClient ?? createOpenAIClient)(apiKey));
    case "anthropic":
      return new AnthropicProvider((deps.anthropicClient ?? createAnthropicClient)(apiKey));
    case "gemini":
      return new GeminiProvider((deps.geminiClient ?? createGeminiClient)(apiKey));
    default:
      throw new LiveSetupError(`공급사 ${provider} 의 어댑터가 없다`);
  }
}

/**
 * 실제 임베딩 어댑터를 SDK 클라이언트와 함께 만든다. 임베딩 경로의 모델이 고른 공급사의 것이어야 하고 키가 있어야 한다.
 */
export function liveEmbeddingProviders(config: LlmConfig, selection: LiveSelection, deps: LiveDeps): Record<string, EmbeddingProvider> {
  const keyName = EMBEDDING_KEY_ENV[selection.provider];
  if (!keyName) throw new LiveSetupError(`공급사 ${selection.provider} 의 실제 호출은 지원하지 않는다. 지원: ${Object.keys(EMBEDDING_KEY_ENV).join(", ")}`);
  if (!config.embeddingRoutes[selection.route]) throw new LiveSetupError(`설정에 없는 임베딩 경로다: ${selection.route}`);
  const model = resolveEmbeddingRoute(config, selection.route);
  if (model.provider !== selection.provider) {
    throw new LiveSetupError(`임베딩 경로 ${selection.route} 의 모델 ${model.key} 는 ${selection.provider} 가 아니라 ${model.provider} 의 것이다`);
  }
  const apiKey = deps.env[keyName];
  if (apiKey === undefined || apiKey.trim() === "") {
    throw new LiveSetupError(`환경 변수 ${keyName} 가 없다. 실제 공급사는 키 없이 부를 수 없다 (--provider mock 은 키가 필요 없다)`);
  }
  return { [selection.provider]: new OpenAIEmbeddingProvider((deps.openaiEmbeddingsClient ?? createOpenAIEmbeddingsClient)(apiKey)) };
}

/** `--rpm` 값. 주지 않으면 간격을 두지 않는다. */
export function parseRequestsPerMinute(raw: string | undefined): { requestsPerMinute?: number } {
  if (raw === undefined) return {};
  const value = Number(raw);
  if (!(value > 0)) throw new LiveSetupError(`--rpm 은 양수여야 한다: ${raw}`);
  return { requestsPerMinute: value };
}

/** 실제 호출의 누적 비용 상한. 실제 공급사를 부르는 실행은 상한 없이 시작하지 않는다. */
export function requireLimitUsd(raw: string | undefined): number {
  if (raw === undefined) throw new LiveSetupError("실제 공급사 호출에는 --limit-usd <상한 USD> 가 필요하다");
  const limit = Number(raw);
  if (!(limit > 0)) throw new LiveSetupError(`--limit-usd 는 양수여야 한다: ${raw}`);
  return limit;
}
