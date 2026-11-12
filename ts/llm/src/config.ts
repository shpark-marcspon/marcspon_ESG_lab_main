import { readFileSync } from "node:fs";
import { z } from "zod";

const rate = z.number().nonnegative();

const pricingSchema = z.strictObject({
  input: rate,
  output: rate,
  cacheWrite: rate,
  cacheWrite1h: rate.optional(),
  cacheRead: rate,
  longContext: z
    .strictObject({
      aboveInputTokens: z.number().int().positive(),
      input: rate,
      output: rate,
      cacheWrite: rate,
      cacheRead: rate,
    })
    .optional(),
});

const modelSchema = z.strictObject({
  provider: z.string().min(1),
  apiModelId: z.string().min(1),
  label: z.string(),
  synthetic: z.boolean().optional(),
  pricing: pricingSchema,
  contextWindowTokens: z.number().int().positive(),
  maxInputTokens: z.number().int().positive().optional(),
  maxOutputTokens: z.number().int().positive(),
  /**
   * 추론(reasoning) 토큰이 출력 상한을 같이 쓰는 모델의 여유분. 호출 계층이 요청의 `maxOutputTokens`(답의 상한)에 이만큼 더해 보낸다.
   * 추론 토큰이 없거나 상한을 같이 쓰지 않는 모델에는 두지 않는다.
   */
  reasoningOutputReserveTokens: z.number().int().positive().optional(),
  minCacheTokens: z.number().int().positive(),
  pdf: z.strictObject({
    maxPagesPerRequest: z.number().int().positive().nullable(),
    maxRequestMb: z.number().positive(),
  }),
  structuredOutput: z.boolean(),
  lifecycle: z.strictObject({ status: z.string(), retirement: z.string().nullable(), note: z.string() }),
  sourceRefs: z.array(z.string()),
  notes: z.array(z.string()),
});

/** 임베딩 모델. 생성 모델과 과금·한도 항목이 달라 따로 둔다. `multilingual` 은 공식 문서에 명시가 없으면 null(못 확인)이다. */
const embeddingModelSchema = z.strictObject({
  provider: z.string().min(1),
  apiModelId: z.string().min(1),
  label: z.string(),
  synthetic: z.boolean().optional(),
  pricing: z.strictObject({ input: rate }),
  dimensions: z.number().int().positive(),
  maxInputTokens: z.number().int().positive(),
  maxInputsPerRequest: z.number().int().positive(),
  maxTokensPerRequest: z.number().int().positive(),
  multilingual: z.boolean().nullable(),
  sourceRefs: z.array(z.string()),
  notes: z.array(z.string()),
});

const tokenEstimateSchema = z.strictObject({
  note: z.string(),
  latinCharsPerToken: z.number().positive(),
  latinBasis: z.string(),
  cjkCharsPerToken: z.number().positive(),
  cjkBasis: z.string(),
});

const fileSchema = z.strictObject({
  checkedOn: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  synthetic: z.boolean().optional(),
  note: z.string(),
  sources: z.record(z.string(), z.url()),
  tokenEstimate: tokenEstimateSchema.optional(),
  models: z.record(z.string(), modelSchema),
  routes: z.record(z.string(), z.array(z.string()).min(1)),
  embeddingModels: z.record(z.string(), embeddingModelSchema).optional(),
  /** 임베딩 경로 이름 → 모델 키 하나. 다른 모델의 벡터는 서로 견줄 수 없어 대체 순서를 두지 않는다. */
  embeddingRoutes: z.record(z.string(), z.string()).optional(),
});

export type Pricing = z.infer<typeof pricingSchema>;
export type ModelConfig = z.infer<typeof modelSchema> & { key: string };
export type EmbeddingModelConfig = z.infer<typeof embeddingModelSchema> & { key: string };
export type TokenEstimateConfig = z.infer<typeof tokenEstimateSchema>;

export interface LlmConfig {
  models: Record<string, ModelConfig>;
  routes: Record<string, string[]>;
  tokenEstimate: TokenEstimateConfig;
  embeddingModels: Record<string, EmbeddingModelConfig>;
  embeddingRoutes: Record<string, string>;
}

export class ConfigError extends Error {
  override name = "ConfigError";
}

/**
 * 설정 파일들을 읽어 하나로 합친다. 모델 ID, 단가, 한도는 이 파일들에만 있다.
 * 출처 없는 실제 모델, 가상 표시 없는 가상 모델은 거부한다.
 */
export function loadConfig(paths: string[]): LlmConfig {
  const models: Record<string, ModelConfig> = {};
  const routes: Record<string, string[]> = {};
  let tokenEstimate: TokenEstimateConfig | undefined;
  const embeddingModels: Record<string, EmbeddingModelConfig> = {};
  const embeddingRoutes: Record<string, string> = {};

  for (const path of paths) {
    const parsed = fileSchema.safeParse(JSON.parse(readFileSync(path, "utf8")));
    if (!parsed.success) throw new ConfigError(`${path}: ${z.prettifyError(parsed.error)}`);
    const file = parsed.data;

    for (const [key, model] of Object.entries(file.models)) {
      if (models[key]) throw new ConfigError(`${path}: 모델 ${key} 가 중복 정의됐다`);
      if (file.synthetic) {
        if (!model.synthetic) throw new ConfigError(`${path}: 가상 파일의 모델 ${key} 에 synthetic 표시가 없다`);
      } else {
        if (model.synthetic) throw new ConfigError(`${path}: 가상 모델 ${key} 는 synthetic 파일에 둬야 한다`);
        if (model.sourceRefs.length === 0) throw new ConfigError(`${path}: 모델 ${key} 에 출처가 없다`);
        for (const ref of model.sourceRefs) {
          if (!file.sources[ref]) throw new ConfigError(`${path}: 모델 ${key} 의 출처 ${ref} 가 sources 에 없다`);
        }
      }
      models[key] = { ...model, key };
    }
    for (const [name, keys] of Object.entries(file.routes)) {
      if (routes[name]) throw new ConfigError(`${path}: 경로 ${name} 이 중복 정의됐다`);
      routes[name] = keys;
    }
    for (const [key, model] of Object.entries(file.embeddingModels ?? {})) {
      if (embeddingModels[key]) throw new ConfigError(`${path}: 임베딩 모델 ${key} 가 중복 정의됐다`);
      if (file.synthetic) {
        if (!model.synthetic) throw new ConfigError(`${path}: 가상 파일의 임베딩 모델 ${key} 에 synthetic 표시가 없다`);
      } else {
        if (model.synthetic) throw new ConfigError(`${path}: 가상 임베딩 모델 ${key} 는 synthetic 파일에 둬야 한다`);
        if (model.sourceRefs.length === 0) throw new ConfigError(`${path}: 임베딩 모델 ${key} 에 출처가 없다`);
        for (const ref of model.sourceRefs) {
          if (!file.sources[ref]) throw new ConfigError(`${path}: 임베딩 모델 ${key} 의 출처 ${ref} 가 sources 에 없다`);
        }
      }
      embeddingModels[key] = { ...model, key };
    }
    for (const [name, key] of Object.entries(file.embeddingRoutes ?? {})) {
      if (embeddingRoutes[name]) throw new ConfigError(`${path}: 임베딩 경로 ${name} 이 중복 정의됐다`);
      embeddingRoutes[name] = key;
    }
    if (file.tokenEstimate) tokenEstimate = file.tokenEstimate;
  }
  for (const [name, key] of Object.entries(embeddingRoutes)) {
    if (!embeddingModels[key]) throw new ConfigError(`임베딩 경로 ${name} 이 없는 모델 ${key} 를 가리킨다`);
  }

  for (const [name, keys] of Object.entries(routes)) {
    for (const key of keys) {
      if (!models[key]) throw new ConfigError(`경로 ${name} 이 없는 모델 ${key} 를 가리킨다`);
    }
  }
  if (!tokenEstimate) throw new ConfigError("tokenEstimate 설정이 어느 파일에도 없다");
  return { models, routes, tokenEstimate, embeddingModels, embeddingRoutes };
}

export function resolveRoute(config: LlmConfig, route: string): ModelConfig[] {
  const keys = config.routes[route];
  if (!keys) throw new ConfigError(`경로 ${route} 가 설정에 없다`);
  return keys.map((key) => config.models[key] as ModelConfig);
}

export function resolveEmbeddingRoute(config: LlmConfig, route: string): EmbeddingModelConfig {
  const key = config.embeddingRoutes[route];
  if (!key) throw new ConfigError(`임베딩 경로 ${route} 가 설정에 없다`);
  return config.embeddingModels[key] as EmbeddingModelConfig;
}
