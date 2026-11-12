import type { z } from "zod";

/** 호출 기록과 비용 집계에 쓰는 꼬리표. */
export interface Tags {
  tenant: string;
  module: string;
}

/**
 * 토큰 사용량. `inputTokens` 는 캐시에 걸리지 않은 입력만 센다.
 * 공급사가 보고하지 않은 항목은 0 이 아니라 null 이다.
 */
export interface Usage {
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number | null;
  cacheWriteTokens: number | null;
  /** 생각(thinking)·추론(reasoning) 토큰 수. `outputTokens` 에 이미 들어 있는 몫이고 따로 과금하지 않는다. 공급사가 보고했을 때만 있다. */
  thinkingTokens?: number;
}

/** 시스템 지침 한 덩어리. `cache` 는 고정 앞부분(지침, 용어집)을 캐시 대상으로 표시한다. */
export interface SystemSegment {
  text: string;
  cache: boolean;
}

export type UserPart =
  | { type: "text"; text: string }
  | { type: "pdf"; base64: string; filename: string }
  | { type: "image"; base64: string; mediaType: "image/png" | "image/jpeg" | "image/webp" | "image/gif" };

/** 공급사에 넘기는 중립 요청. 공급사 고유 형식으로 바꾸는 일은 어댑터가 한다. */
export interface ProviderRequest {
  apiModelId: string;
  system: SystemSegment[];
  user: UserPart[];
  maxOutputTokens: number;
  jsonSchema?: { name: string; schema: Record<string, unknown> };
}

/** `modelId` 는 공급사가 응답에 적은 모델 ID 다. 요청한 ID 와 다를 수 있어(날짜 붙은 스냅숏) 호출 기록에 따로 남긴다. */
export type ProviderResponse =
  | { kind: "completed"; text: string; usage: Usage; modelId?: string }
  | { kind: "refusal"; text: string; category: string | null; usage: Usage; modelId?: string }
  | { kind: "truncated"; text: string; usage: Usage; modelId?: string }
  | { kind: "error"; status: number | null; retryable: boolean; message: string; /** 공급사가 알려 준 「이만큼 뒤에 다시」 (ms). 알려 줬을 때만 있다. */ retryAfterMs?: number }
  | { kind: "timeout"; message: string };

export interface InvokeOptions {
  signal: AbortSignal;
  timeoutMs: number;
}

export interface Provider {
  readonly name: string;
  /** 네트워크 없이 공급사 고유 요청 본문만 조립한다. `--dry-run` 과 단위 테스트가 쓴다. */
  buildRequest(request: ProviderRequest): unknown;
  /** 실패를 던지지 않고 `ProviderResponse` 로 돌려준다. */
  invoke(request: ProviderRequest, options: InvokeOptions): Promise<ProviderResponse>;
}

export type WarningCode = "cache_below_minimum" | "cache_not_applied" | "usage_unreported";

export interface LlmWarning {
  code: WarningCode;
  message: string;
}

export type AttemptKind = "success" | "schema_mismatch" | "refusal" | "truncated" | "provider_error" | "timeout";

/** 공급사를 한 번 부른 기록. 재시도와 대체 호출마다 하나씩 생긴다. */
export interface Attempt {
  provider: string;
  model: string;
  kind: AttemptKind;
  usage: Usage | null;
  costUsd: number | null;
  durationMs: number;
  /** 공급사 오류 뒤에 같은 모델을 다시 부른 시도일 때만 있다: 몇 번째 재시도인지 (1부터). */
  providerRetry?: number;
}

export interface CallMeta {
  callId: string;
  promptId: string;
  attempts: Attempt[];
  /** 모든 시도의 비용 합. 사용량을 못 받은 시도(오류, 시간 초과)는 빼고 더한다. */
  totalCostUsd: number;
  warnings: LlmWarning[];
}

export interface SchemaIssue {
  path: string;
  message: string;
}

/** 호출 결과. 실패를 빈 결과로 삼키지 않고 종류별로 나눈다. */
export type LlmResult<T> =
  | { kind: "success"; data: T; text: string; meta: CallMeta }
  | { kind: "schema_mismatch"; issues: SchemaIssue[]; rawText: string; meta: CallMeta }
  | { kind: "refusal"; text: string; category: string | null; meta: CallMeta }
  | { kind: "truncated"; partialText: string; meta: CallMeta }
  | { kind: "provider_error"; status: number | null; retryable: boolean; message: string; meta: CallMeta }
  | { kind: "timeout"; message: string; meta: CallMeta }
  | { kind: "budget_exceeded"; limitUsd: number; spentUsd: number; scope: Partial<Tags>; meta: CallMeta };

export type LlmResultKind = LlmResult<unknown>["kind"];

export interface CallRequest<T> {
  /** `모듈/이름@버전`. 버전을 빼면 거부한다. */
  prompt: string;
  vars?: Record<string, string>;
  /** 설정 파일의 경로(route) 이름. 첫 모델이 주, 나머지가 대체 순서다. */
  route: string;
  tags: Tags;
  /** 주면 응답을 JSON 으로 받아 이 스키마로 검증한다. 없으면 `data` 는 응답 글자 그대로다. */
  schema?: z.ZodType<T>;
  schemaName?: string;
  attachments?: Exclude<UserPart, { type: "text" }>[];
  /**
   * 답의 출력 토큰 상한. 모델 설정에 추론 여유분(`reasoningOutputReserveTokens`)이 있으면 호출 계층이 더해 보낸다.
   * 공급사에 실제로 보낸 값은 `ProviderRequest.maxOutputTokens` 와 dry-run 의 `estimate.maxOutputTokens` 다.
   */
  maxOutputTokens?: number;
  timeoutMs?: number;
  /** 스키마 불일치 때 같은 모델로 다시 부르는 횟수. 기본 2. */
  maxSchemaRetries?: number;
}

export interface DryRunReport {
  dryRun: true;
  promptId: string;
  provider: string;
  model: string;
  request: unknown;
  /** 어림값이다. 실제 토큰 수가 아니다. PDF·이미지는 세지 않는다. */
  estimate: {
    inputTokens: number;
    cacheableTokens: number;
    maxOutputTokens: number;
    /** 입력 어림 + 출력 상한을 다 썼을 때의 비용 상한. */
    costUpperBoundUsd: number;
    unestimatedParts: string[];
  };
  fallbackModels: string[];
  warnings: LlmWarning[];
}
