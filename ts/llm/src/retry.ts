import { ConfigError } from "./config.js";

/** 같은 모델을 다시 부르는 재시도의 설정. 재시도 가능한 공급사 오류(`provider_error`, `retryable`)에만 쓴다. */
export interface RetryPolicy {
  /** 첫 호출 뒤에 다시 부르는 최대 횟수. 0 이면 다시 부르지 않는다. */
  maxRetries: number;
  /** 첫 재시도 전의 대기 (ms). 그 뒤로 두 배씩 늘어난다. */
  baseDelayMs: number;
  /** 대기의 상한 (ms). 공급사가 이보다 긴 대기를 요구하면 다시 부르지 않는다. */
  maxDelayMs: number;
}

/**
 * 공급사 이름 → 호출 계층의 재시도 설정. 여기에 없는 공급사는 호출 계층에서 다시 부르지 않는다.
 * - `gemini`: SDK(`@google/genai`)의 자체 재시도는 꺼 둔 채로 호출 계층이 다시 부른다. 2회는 OpenAI·Anthropic SDK 의 기본값과 같은 횟수이고,
 *   대기 상한 60초는 무료 등급의 분당 한도 429 가 요구한 대기(2026-10-02 실제 응답의 `retryDelay: "55s"`)를 따를 수 있는 값이다.
 * - `openai`, `anthropic`: SDK 가 이미 2회 다시 부른다. 여기에 넣으면 시도 수가 곱해지므로 넣지 않는다.
 */
export const DEFAULT_PROVIDER_RETRY: Readonly<Record<string, RetryPolicy>> = {
  gemini: { maxRetries: 2, baseDelayMs: 1_000, maxDelayMs: 60_000 },
};

export const NO_RETRY: RetryPolicy = { maxRetries: 0, baseDelayMs: 0, maxDelayMs: 0 };

export function validateRetryPolicies(policies: Readonly<Record<string, RetryPolicy>>): void {
  for (const [provider, policy] of Object.entries(policies)) {
    if (!Number.isInteger(policy.maxRetries) || policy.maxRetries < 0) throw new ConfigError(`공급사 ${provider} 의 maxRetries 는 0 이상의 정수여야 한다: ${policy.maxRetries}`);
    if (!(policy.baseDelayMs >= 0) || !(policy.maxDelayMs >= policy.baseDelayMs)) {
      throw new ConfigError(`공급사 ${provider} 의 재시도 대기는 0 ≤ baseDelayMs ≤ maxDelayMs 여야 한다: ${policy.baseDelayMs}, ${policy.maxDelayMs}`);
    }
  }
}

/**
 * `retryIndex` 번째(0부터) 재시도 전에 기다릴 시간 (ms). 다시 부르지 않아야 하면 null.
 * 공급사가 대기 시간을 알려 줬으면(`retryAfterMs`) 그 값을 따르고, 그 값이 상한을 넘으면 다시 부르지 않는다.
 * 알려 주지 않았으면 `baseDelayMs × 2^retryIndex` 를 상한에서 자른다. 흩뜨리기(jitter)는 없다.
 */
export function retryDelayMs(policy: RetryPolicy, retryIndex: number, retryAfterMs: number | undefined): number | null {
  if (retryIndex >= policy.maxRetries) return null;
  if (retryAfterMs !== undefined) return retryAfterMs > policy.maxDelayMs ? null : retryAfterMs;
  return Math.min(policy.baseDelayMs * 2 ** retryIndex, policy.maxDelayMs);
}
