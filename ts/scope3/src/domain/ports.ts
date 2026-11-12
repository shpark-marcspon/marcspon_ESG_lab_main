import type { CallRequest, LlmResult } from "llm";
import type { CandidateQuery, Factor } from "./factor.js";
import type { ShortlistQuery } from "./lexicalV1.js";
import type { CandidateEvidence } from "./ranking.js";

// 도메인 코드가 바깥(파일, 공급사)에 기대는 자리. 구현은 `adapters/` 에 있고 조립은 `cli.ts` 가 한다.

/** 계수 카탈로그의 읽기 포트. 파이프라인이 실제로 부르는 것만 있다. 저장 방식(jsonl, DB)은 이 뒤에 둔다. */
export interface FactorCatalog {
  /** 후보가 될 수 있는 계수만 돌려준다. 후보가 아닌 레코드의 id 와 없는 id 는 undefined. */
  get(id: string): Factor | undefined;
  /** 조건에 맞는 후보 (표의 순서). 빈 배열은 「조건에 맞는 계수가 없다」는 뜻이고 읽기 실패는 예외다. */
  candidates(query: CandidateQuery): Factor[];
}

/** 확정 매핑 기억의 포트. 항목 타입과 같이 `memory.ts` 에 있다. */
export type { MappingMemory } from "./memory.js";

/** 생성 모델 호출 포트. 파이프라인이 쓰는 것은 `call` 하나다. `llm` 의 `LlmClient` 가 그대로 맞는다. */
export interface LlmGateway {
  call<T = string>(request: CallRequest<T>): Promise<LlmResult<T>>;
}

/** 후보 추리기 방식. `lexical-v1` 은 처음의 글자 수준 검색이고 견줄 기준으로 남아 있다. */
export const SHORTLIST_METHODS = ["lexical-v1", "lexical-v2", "embedding", "hybrid"] as const;
export type ShortlistMethod = (typeof SHORTLIST_METHODS)[number];

/** 키도 색인도 없이 도는 방식. */
export type LexicalMethod = Extract<ShortlistMethod, "lexical-v1" | "lexical-v2">;

/**
 * 방식을 고르지 않았을 때의 추리기. 기본값은 이 한 자리에만 있다: CLI 의 `--shortlist-method`, `PipelineOptions.shortlister` 를
 * 안 줬을 때, `--dry-run` 의 어림이 모두 여기를 읽는다. 기본값은 키 없이 돌아야 하므로 낱말 검색 방식이어야 한다.
 */
export const DEFAULT_SHORTLIST_METHOD: LexicalMethod = "lexical-v2";

/** 추리지 못한 사정. 종류는 이 포트가 스스로 정한다 (어느 구현의 타입에서도 파생하지 않는다). */
export interface ShortlistFailure {
  /** 앞의 셋은 질의 임베딩 호출의 실패다. `index_missing_factor` 는 색인에 그 후보의 벡터가 없다는 뜻이다. */
  kind: "provider_error" | "timeout" | "budget_exceeded" | "index_missing_factor";
  detail: string;
}

/** 추리기 결과. 실패는 다른 방식으로 조용히 넘어가지 않고 `ok: false` 로 남는다. */
export type ShortlistOutcome =
  | {
      ok: true;
      method: ShortlistMethod;
      factors: Factor[];
      evidence: CandidateEvidence[];
      /** 추리기 전 후보 수. */
      total: number;
      /** 낱말이 한 군데라도 겹친 후보 수. 낱말 검색을 쓰지 않는 방식은 null. */
      lexicalMatches: number | null;
      /** 이 추리기에 든 비용 (USD). 지금은 질의 임베딩 비용뿐이다. 이름은 결과 파일의 `shortlist.embeddingCostUsd` 와 같이 둔다. */
      embeddingCostUsd: number;
    }
  | { ok: false; method: ShortlistMethod; failure: ShortlistFailure; embeddingCostUsd: number };

/** 카테고리·단위로 거른 후보를 프롬프트에 넣을 수로 줄이는 단계. */
export interface Shortlister {
  readonly method: ShortlistMethod;
  /** 방식의 설정을 사람이 읽을 수 있게 적은 것. 결과 파일에 싣는다. */
  readonly description: string;
  shortlist(candidates: readonly Factor[], query: ShortlistQuery, limit: number): Promise<ShortlistOutcome>;
}

export type QueryEmbedding = { ok: true; vectors: number[][]; costUsd: number } | { ok: false; failure: ShortlistFailure; costUsd: number };

/** 질의 글 → 벡터와 비용. `VectorShortlister` 가 기댄다. */
export interface QueryEmbedder {
  /** 질의를 임베딩하는 모델. 색인을 만든 모델과 같아야 한다. */
  readonly model: { key: string; dimensions: number };
  /** 벡터는 입력 순서를 따른다. 실패해도 쓴 비용은 `costUsd` 에 남는다. */
  embed(texts: readonly string[]): Promise<QueryEmbedding>;
}

/** 계수 id → 벡터. 계수 카탈로그의 파생 읽기 모델이다. 저장 방식(파일, DB 벡터 컬럼)은 이 뒤에 둔다. */
export interface FactorVectorIndex {
  /** 신선도 머리: 어느 모델로, 어느 계수 표에서, 어느 글 형식 판으로 만든 색인인지. */
  readonly header: { modelKey: string; dimensions: number; factorsSha256: string; textVersion: number };
  /** 색인에 없는 id 는 undefined. */
  vector(id: string): ArrayLike<number> | undefined;
}
