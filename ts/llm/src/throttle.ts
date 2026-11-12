import type { InvokeOptions, Provider, ProviderRequest, ProviderResponse } from "./types.js";

export interface ThrottleClock {
  now(): number;
  sleep(ms: number): Promise<void>;
}

const REAL_CLOCK: ThrottleClock = { now: Date.now, sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)) };

/**
 * 공급사 호출의 시작 간격을 벌린다 (분당 요청 수 한도가 있는 키용). 요청 내용은 건드리지 않는다.
 * 기다리는 시간도 호출 계층의 시간 제한(`timeoutMs`) 안에 든다. 기다리는 동안 중단되면 공급사를 부르지 않는다.
 */
export function throttled(provider: Provider, requestsPerMinute: number, clock: ThrottleClock = REAL_CLOCK): Provider {
  if (!(requestsPerMinute > 0)) throw new Error(`분당 요청 수는 양수여야 한다: ${requestsPerMinute}`);
  const intervalMs = 60_000 / requestsPerMinute;
  let nextAt = 0;
  return {
    name: provider.name,
    buildRequest: (request: ProviderRequest) => provider.buildRequest(request),
    async invoke(request: ProviderRequest, options: InvokeOptions): Promise<ProviderResponse> {
      const now = clock.now();
      const startAt = Math.max(now, nextAt);
      nextAt = startAt + intervalMs;
      if (startAt > now) await clock.sleep(startAt - now);
      if (options.signal.aborted) return { kind: "timeout", message: "호출 간격을 기다리는 동안 중단됐다" };
      return provider.invoke(request, options);
    },
  };
}
