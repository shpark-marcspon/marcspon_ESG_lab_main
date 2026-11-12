import { roundUsd, type CallMeta, type CallRequest, type LlmResult, type LlmResultKind, type Tags } from "llm";
import { z } from "zod";
import type { Factor } from "./factor.js";
import type { Confidence, FactorPick, InputRow, SupportedCategory } from "./types.js";
import { SUPPORTED_CATEGORIES } from "./types.js";

export const CLASSIFY_PROMPT = "scope3/classify@1";
export const RECOMMEND_PROMPT = "scope3/recommend@1";
export const MAX_PICKS = 3;

/**
 * 답의 출력 토큰 상한. 추론 토큰이 출력 상한을 같이 쓰는 것은 모델의 성질이라 단계 상수에는 넣지 않는다:
 * 호출 계층이 모델 설정의 여유분(`reasoningOutputReserveTokens`)을 더해 보낸다.
 * 추론이 없는 모델의 실측 최대는 분류 143, 추천 321 토큰이다 (2026-10-02, MARC-TICK-14·15 의 호출 기록).
 */
export const CLASSIFY_MAX_OUTPUT_TOKENS = 400;
export const RECOMMEND_MAX_OUTPUT_TOKENS = 800;

const confidenceSchema = z.enum(["high", "medium", "low"]);

export const classificationSchema = z.strictObject({
  category: z.union([...SUPPORTED_CATEGORIES.map((category) => z.literal(category)), z.literal("other")]),
  keywords_en: z.array(z.string().min(1).max(60)).max(8),
  confidence: confidenceSchema,
  reason: z.string().min(1),
});

export interface Classification {
  category: SupportedCategory | "other";
  keywords: string[];
  confidence: Confidence;
  reason: string;
}

export interface Recommendation {
  picks: FactorPick[];
  noneSuitable: boolean;
}

/**
 * 추천 응답의 스키마. `factor_id` 는 이번 호출에 넘긴 후보 id 만 허용한다.
 * 후보 밖 id 는 여기서 스키마 불일치가 되고, 호출 계층이 다시 부른 뒤에도 같으면 `schema_mismatch` 로 끝난다.
 */
export function recommendationSchema(candidateIds: readonly string[]) {
  if (candidateIds.length === 0) throw new Error("후보가 없는 추천 스키마는 만들 수 없다");
  return z
    .strictObject({
      picks: z
        .array(
          z.strictObject({
            factor_id: z.enum(candidateIds as [string, ...string[]]),
            reason: z.string().min(1),
            confidence: confidenceSchema,
          }),
        )
        .max(MAX_PICKS),
      none_suitable: z.boolean(),
    })
    .refine((value) => new Set(value.picks.map((pick) => pick.factor_id)).size === value.picks.length, {
      message: "같은 factor_id 를 두 번 골랐다",
      path: ["picks"],
    })
    .refine((value) => (value.picks.length === 0) === value.none_suitable, {
      message: "picks 가 비면 none_suitable 은 true, 아니면 false 여야 한다",
      path: ["none_suitable"],
    });
}

/**
 * LLM 에 보여 주는 품목 정보. 수량, 금액, 무게, 거리는 넣지 않는다 (숫자 판단은 코드의 몫이다).
 * 품목명에 들어 있는 규격 숫자("80g")는 이름의 일부라 그대로 간다.
 */
export function itemForPrompt(row: InputRow): Record<string, unknown> {
  return {
    item_name: row.itemName,
    unit: row.unit,
    currency: row.currency,
    transport: row.transport ? { origin: row.transport.origin, destination: row.transport.destination, mode: row.transport.mode } : null,
  };
}

/** LLM 에 보여 주는 후보 정보. 계수 값은 넣지 않는다. */
export function candidateForPrompt(factor: Factor): Record<string, unknown> {
  const path = factor.categoryPath.join(" > ");
  return {
    id: factor.id,
    name: factor.name,
    ...(path !== "" && !factor.name.startsWith(factor.categoryPath[0] as string) ? { path } : {}),
    per: factor.unitDenominator,
    source: factor.sourceId,
    region: factor.region,
  };
}

export function classifyRequest(row: InputRow, route: string, tags: Tags): CallRequest<z.infer<typeof classificationSchema>> {
  return {
    prompt: CLASSIFY_PROMPT,
    vars: { item_json: JSON.stringify(itemForPrompt(row)) },
    route,
    tags,
    schema: classificationSchema,
    schemaName: "scope3_classification",
    maxOutputTokens: CLASSIFY_MAX_OUTPUT_TOKENS,
  };
}

export function recommendRequest(
  row: InputRow,
  category: SupportedCategory,
  basis: "physical" | "spend",
  candidates: readonly Factor[],
  route: string,
  tags: Tags,
) {
  return {
    prompt: RECOMMEND_PROMPT,
    vars: {
      category: String(category),
      basis: basis === "physical" ? "활동(물량·거리) 기반" : "지출 기반",
      item_json: JSON.stringify(itemForPrompt(row)),
      candidates_json: JSON.stringify(candidates.map(candidateForPrompt)),
    },
    route,
    tags,
    schema: recommendationSchema(candidates.map((factor) => factor.id)),
    schemaName: "scope3_recommendation",
    maxOutputTokens: RECOMMEND_MAX_OUTPUT_TOKENS,
  } satisfies CallRequest<unknown>;
}

/** 성공이 아닌 LLM 결과를 종류와 함께 보존한다. 빈 결과로 바꾸지 않는다. */
export interface LlmFailure {
  kind: Exclude<LlmResultKind, "success">;
  detail: string;
}

export function describeFailure(result: Exclude<LlmResult<unknown>, { kind: "success" }>): LlmFailure {
  switch (result.kind) {
    case "schema_mismatch":
      return { kind: result.kind, detail: result.issues.map((issue) => `${issue.path}: ${issue.message}`).join("; ") };
    case "refusal":
      return { kind: result.kind, detail: result.text };
    case "truncated":
      return { kind: result.kind, detail: "출력 토큰 상한에서 잘렸다" };
    case "provider_error":
      return { kind: result.kind, detail: `status=${String(result.status)} retryable=${String(result.retryable)} ${result.message}` };
    case "timeout":
      return { kind: result.kind, detail: result.message };
    case "budget_exceeded":
      return { kind: result.kind, detail: `상한 ${result.limitUsd} USD, 누적 ${result.spentUsd} USD` };
  }
}

/** LLM 호출 집계. `calls` 는 호출 계층을 부른 수, `attempts` 는 공급사를 실제로 부른 수(재시도·대체 포함). */
export interface LlmUsage {
  calls: number;
  attempts: number;
  costUsd: number;
  byPrompt: Record<string, { calls: number; attempts: number; costUsd: number }>;
}

export function emptyUsage(): LlmUsage {
  return { calls: 0, attempts: 0, costUsd: 0, byPrompt: {} };
}

export function recordUsage(usage: LlmUsage, prompt: string, meta: CallMeta): void {
  const slot = (usage.byPrompt[prompt] ??= { calls: 0, attempts: 0, costUsd: 0 });
  for (const target of [usage, slot]) {
    target.calls += 1;
    target.attempts += meta.attempts.length;
    target.costUsd = roundUsd(target.costUsd + meta.totalCostUsd);
  }
}
