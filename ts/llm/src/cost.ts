import type { Pricing, TokenEstimateConfig } from "./config.js";
import type { Usage } from "./types.js";

const PER_MTOK = 1_000_000;

/** 1e-8 달러에서 반올림해 부동소수 오차를 지운다. */
export function roundUsd(value: number): number {
  return Math.round(value * 1e8) / 1e8;
}

/**
 * 사용량과 단가로 비용(USD)을 계산한다. 단가는 100만 토큰당 값이다.
 * 장문 구간이 있는 모델은 입력 합계(캐시 포함)가 기준을 넘으면 요청 전체에 장문 단가를 적용한다.
 * 공급사가 보고하지 않은(null) 캐시 토큰은 0 으로 계산한다. 호출 쪽이 경고로 따로 알린다.
 */
export function computeCostUsd(usage: Usage, pricing: Pricing): number {
  const cacheRead = usage.cacheReadTokens ?? 0;
  const cacheWrite = usage.cacheWriteTokens ?? 0;
  const totalInput = usage.inputTokens + cacheRead + cacheWrite;
  const long = pricing.longContext;
  const rates = long && totalInput > long.aboveInputTokens ? long : pricing;
  const micro =
    usage.inputTokens * rates.input +
    usage.outputTokens * rates.output +
    cacheRead * rates.cacheRead +
    cacheWrite * rates.cacheWrite;
  return roundUsd(micro / PER_MTOK);
}

export interface TokenEstimator {
  estimate(text: string): number;
}

const CJK = /[ᄀ-ᇿ぀-ヿ㄰-㆏㐀-䶿一-鿿가-힯]/g;

/** 글자 수로 토큰 수를 어림한다. 과금 근거가 아니라 사전 경고와 dry-run 용이다. */
export class HeuristicTokenEstimator implements TokenEstimator {
  constructor(private readonly config: TokenEstimateConfig) {}

  estimate(text: string): number {
    const cjk = text.match(CJK)?.length ?? 0;
    const other = text.length - cjk;
    return Math.ceil(cjk / this.config.cjkCharsPerToken + other / this.config.latinCharsPerToken);
  }
}
