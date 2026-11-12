import { computeCostUsd, resolveRoute, type CallRequest, type LlmClient, type LlmConfig } from "llm";
import type { Chunk } from "./chunk.js";

export interface DryRunRow {
  chunkId: string;
  pages: string;
  units: number;
  items: number;
  sourceTokens: number;
  overBudget: boolean;
  /** 지침 + 용어집 + 조각 원문의 어림 토큰 (`ts/llm` 의 dryRun). */
  inputTokens: number;
  expectedOutputTokens: number;
  expectedCostUsd: number;
  /** 출력 토큰 상한을 다 썼을 때의 비용 (`ts/llm` 의 dryRun). */
  costUpperBoundUsd: number;
}

export interface DryRunReport {
  route: string;
  provider: string;
  model: string;
  /** 단가의 출처. `data/llm/` 설정에서 읽은 값이고 코드에 없다. */
  pricingUsdPerMTok: { input: number; output: number };
  chunks: number;
  overBudgetChunks: number;
  /** 조각마다 되풀이되는 캐시 대상 앞부분(지침, 용어집)의 어림 토큰. */
  cacheablePrefixTokens: number;
  totals: { sourceTokens: number; inputTokens: number; expectedOutputTokens: number; expectedCostUsd: number; costUpperBoundUsd: number };
  /** 경고 문구 → 그 경고가 나온 조각 수 */
  warnings: Record<string, number>;
  rows: DryRunRow[];
}

const round = (value: number): number => Math.round(value * 1e6) / 1e6;

/**
 * 공급사를 부르지 않고 조각마다 `LlmClient.dryRun` 으로 요청을 조립해 토큰과 비용을 어림한다.
 * 예상 비용 = 입력 어림 × 입력 단가 + (조각 원문 토큰 × `outputRatio`) × 출력 단가. 캐시와 배치 할인은 넣지 않는다.
 */
export function dryRunChunks<T>(
  client: LlmClient,
  config: LlmConfig,
  chunks: Chunk[],
  request: (chunk: Chunk) => CallRequest<T>,
  route: string,
  outputRatio: number,
): DryRunReport {
  const model = resolveRoute(config, route)[0];
  if (!model) throw new Error(`경로 ${route} 에 모델이 없다`);
  const warnings: Record<string, number> = {};
  let provider = model.provider;
  let cacheablePrefixTokens = 0;
  const rows = chunks.map((chunk): DryRunRow => {
    const report = client.dryRun(request(chunk));
    provider = report.provider;
    cacheablePrefixTokens = report.estimate.cacheableTokens;
    for (const warning of report.warnings) warnings[warning.message] = (warnings[warning.message] ?? 0) + 1;
    const expectedOutputTokens = Math.ceil(chunk.sourceTokens * outputRatio);
    return {
      chunkId: chunk.id,
      pages: chunk.pageStart === chunk.pageEnd ? `${chunk.pageStart}` : `${chunk.pageStart}-${chunk.pageEnd}`,
      units: chunk.unitIds.length,
      items: chunk.items.length,
      sourceTokens: chunk.sourceTokens,
      overBudget: chunk.overBudget,
      inputTokens: report.estimate.inputTokens,
      expectedOutputTokens,
      expectedCostUsd: computeCostUsd({ inputTokens: report.estimate.inputTokens, outputTokens: expectedOutputTokens, cacheReadTokens: 0, cacheWriteTokens: 0 }, model.pricing),
      costUpperBoundUsd: report.estimate.costUpperBoundUsd,
    };
  });
  const sum = (pick: (row: DryRunRow) => number): number => rows.reduce((total, row) => total + pick(row), 0);
  return {
    route,
    provider,
    model: model.key,
    pricingUsdPerMTok: { input: model.pricing.input, output: model.pricing.output },
    chunks: rows.length,
    overBudgetChunks: rows.filter((row) => row.overBudget).length,
    cacheablePrefixTokens,
    totals: {
      sourceTokens: sum((row) => row.sourceTokens),
      inputTokens: sum((row) => row.inputTokens),
      expectedOutputTokens: sum((row) => row.expectedOutputTokens),
      expectedCostUsd: round(sum((row) => row.expectedCostUsd)),
      costUpperBoundUsd: round(sum((row) => row.costUpperBoundUsd)),
    },
    warnings,
    rows,
  };
}
