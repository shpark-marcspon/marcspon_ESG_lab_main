import type { Factor } from "./factor.js";
import { rankLexicalV1, type ShortlistQuery } from "./lexicalV1.js";
import { DEFAULT_SHORTLIST_METHOD, type FactorVectorIndex, type LexicalMethod, type QueryEmbedder, type ShortlistMethod, type ShortlistOutcome, type Shortlister } from "./ports.js";
import { RRF_K, BM25_B, BM25_K1, embeddingQueryText, fuseRrf, rankByEmbedding, rankLexicalV2, type EmbeddingQueryMode, type LexicalV2Options, type RankedCandidate } from "./ranking.js";

// `Shortlister` 포트의 구현. 순위는 `ranking.ts`·`lexicalV1.ts` 의 순수 함수가 내고 여기서는 자르고 근거를 붙인다.

function cut(method: ShortlistMethod, ranked: readonly RankedCandidate[], total: number, lexicalMatches: number | null, limit: number, embeddingCostUsd: number): ShortlistOutcome {
  const top = ranked.slice(0, limit);
  return {
    ok: true,
    method,
    factors: top.map((entry) => entry.factor),
    evidence: top.map((entry, position) => ({ ...entry.evidence, rank: position + 1 })),
    total,
    lexicalMatches,
    embeddingCostUsd,
  };
}

/** 낱말 검색의 순위 뒤에, 겹친 낱말이 없는 후보를 표 순서로 이어 붙인다. 낱말이 적게 겹쳐도 추린 수는 채운다. */
function padWithTableOrder(ranked: readonly RankedCandidate[], candidates: readonly Factor[]): RankedCandidate[] {
  const seen = new Set(ranked.map((entry) => entry.factor.id));
  const rest = candidates.flatMap((factor, index): RankedCandidate[] => (seen.has(factor.id) ? [] : [{ factor, index, score: 0, evidence: { id: factor.id } }]));
  return [...ranked, ...rest];
}

/** 처음의 글자 수준 검색 (부분 문자열 일치 3점, 한글 두 글자 겹침 1점, 동점은 표 순서). 견줄 기준으로 남긴다. */
export class LexicalV1Shortlister implements Shortlister {
  readonly method = "lexical-v1";
  readonly description = "lexical-v1: 부분 문자열 일치 3점 + 한글 두 글자 겹침 1점, 동점은 표 순서";

  async shortlist(candidates: readonly Factor[], query: ShortlistQuery, limit: number): Promise<ShortlistOutcome> {
    const ranked = rankLexicalV1(candidates, query);
    const entries = ranked.map((entry): RankedCandidate => ({
      factor: entry.factor,
      index: entry.index,
      score: entry.score,
      evidence: { id: entry.factor.id, ...(entry.score > 0 ? { lexical: { rank: 0, score: entry.score, matchedTerms: entry.matched } } : {}) },
    }));
    entries.forEach((entry, position) => {
      if (entry.evidence.lexical) entry.evidence.lexical.rank = position + 1;
    });
    return cut(this.method, entries, candidates.length, entries.filter((entry) => entry.score > 0).length, limit, 0);
  }
}

export class LexicalV2Shortlister implements Shortlister {
  readonly method = "lexical-v2";
  readonly description: string;

  constructor(private readonly options: LexicalV2Options = { bigrams: true }) {
    this.description = `lexical-v2: 낱말 단위 BM25 (k1=${BM25_K1}, b=${BM25_B}), 한글 두 글자 조각 ${options.bigrams ? "씀" : "안 씀"}, 동점은 표 순서`;
  }

  async shortlist(candidates: readonly Factor[], query: ShortlistQuery, limit: number): Promise<ShortlistOutcome> {
    const ranked = rankLexicalV2(candidates, query, this.options);
    return cut(this.method, padWithTableOrder(ranked, candidates), candidates.length, ranked.length, limit, 0);
  }
}

/** 낱말 검색 추리기를 방식 이름으로 만든다. `options` 는 `lexical-v2` 에만 쓰인다. */
export function lexicalShortlister(method: LexicalMethod, options?: LexicalV2Options): Shortlister {
  return method === "lexical-v1" ? new LexicalV1Shortlister() : new LexicalV2Shortlister(options);
}

/** 방식을 고르지 않았을 때의 추리기 (`DEFAULT_SHORTLIST_METHOD`). */
export function defaultShortlister(): Shortlister {
  return lexicalShortlister(DEFAULT_SHORTLIST_METHOD);
}

export interface VectorShortlisterOptions {
  embedder: QueryEmbedder;
  index: FactorVectorIndex;
  queryMode: EmbeddingQueryMode;
  /** 주면 합친 방식(hybrid)이다: 이 낱말 검색 순위와 임베딩 순위를 RRF 로 합친다. */
  fuseWith?: LexicalV2Options;
}

/**
 * 임베딩 검색과, 낱말 검색을 순위 합산으로 합친 검색. 질의 벡터는 같은 글이면 한 번만 부른다.
 * 공급사와 색인 파일을 모른다: `QueryEmbedder` 와 `FactorVectorIndex` 포트에만 기댄다.
 */
export class VectorShortlister implements Shortlister {
  readonly method: ShortlistMethod;
  readonly description: string;
  private readonly queryVectors = new Map<string, number[]>();

  constructor(private readonly options: VectorShortlisterOptions) {
    const model = options.embedder.model;
    if (model.key !== options.index.header.modelKey) {
      throw new Error(`질의 임베딩 모델 ${model.key} 가 색인의 모델 ${options.index.header.modelKey} 와 다르다`);
    }
    this.method = options.fuseWith ? "hybrid" : "embedding";
    const base = `임베딩 ${model.key} (${model.dimensions}차원), 질의 ${options.queryMode}, 코사인 유사도`;
    this.description = options.fuseWith
      ? `hybrid: lexical-v2 (한글 두 글자 조각 ${options.fuseWith.bigrams ? "씀" : "안 씀"}) + ${base}, RRF k=${RRF_K}`
      : `embedding: ${base}`;
  }

  /** 그 글의 질의 벡터를 이미 갖고 있는지. */
  has(text: string): boolean {
    return this.queryVectors.has(text);
  }

  /** 이미 임베딩한 질의 벡터를 넣어 둔다 (여러 방식이 같은 질의를 나눠 쓸 때). */
  seed(texts: readonly string[], vectors: readonly number[][]): void {
    texts.forEach((text, position) => this.queryVectors.set(text, vectors[position] as number[]));
  }

  async shortlist(candidates: readonly Factor[], query: ShortlistQuery, limit: number): Promise<ShortlistOutcome> {
    const text = embeddingQueryText(query, this.options.queryMode);
    let embeddingCostUsd = 0;
    if (!this.queryVectors.has(text)) {
      const embedded = await this.options.embedder.embed([text]);
      embeddingCostUsd = embedded.costUsd;
      if (!embedded.ok) return { ok: false, method: this.method, failure: embedded.failure, embeddingCostUsd };
      this.seed([text], embedded.vectors);
    }
    const semantic = rankByEmbedding(candidates, this.queryVectors.get(text) as number[], this.options.index);
    if (!Array.isArray(semantic)) {
      return { ok: false, method: this.method, failure: { kind: "index_missing_factor", detail: `색인에 후보 ${semantic.missing} 의 벡터가 없다` }, embeddingCostUsd };
    }
    if (!this.options.fuseWith) return cut(this.method, semantic, candidates.length, null, limit, embeddingCostUsd);
    const lexical = rankLexicalV2(candidates, query, this.options.fuseWith);
    return cut(this.method, fuseRrf([lexical, semantic]), candidates.length, lexical.length, limit, embeddingCostUsd);
  }
}
