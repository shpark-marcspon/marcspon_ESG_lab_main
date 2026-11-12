import type { EmbedResult, EmbeddingClient, Tags } from "llm";
import type { Factor } from "../domain/factor.js";
import type { ShortlistQuery } from "../domain/lexicalV1.js";
import type { QueryEmbedder, QueryEmbedding, ShortlistMethod, ShortlistOutcome, Shortlister } from "../domain/ports.js";
import type { EmbeddingQueryMode, LexicalV2Options } from "../domain/ranking.js";
import { VectorShortlister } from "../domain/shortlister.js";
import type { EmbeddingIndex } from "./embeddingIndex.js";

/** `QueryEmbedder` 포트의 `llm` 구현. 호출 계층(`EmbeddingClient`)을 지나므로 호출 기록과 상한을 그대로 탄다. */
export class LlmQueryEmbedder implements QueryEmbedder {
  readonly model: { key: string; dimensions: number };

  constructor(
    private readonly client: EmbeddingClient,
    /** 임베딩 경로 이름. 색인을 만든 모델과 같은 모델을 가리켜야 한다. */
    readonly route: string,
    private readonly tags: Tags,
  ) {
    this.model = client.model(route);
  }

  /** 호출 계층의 결과를 그대로 돌려준다. */
  embedRaw(texts: readonly string[]): Promise<EmbedResult> {
    return this.client.embed({ route: this.route, inputs: [...texts], tags: this.tags });
  }

  async embed(texts: readonly string[]): Promise<QueryEmbedding> {
    const result = await this.embedRaw(texts);
    const costUsd = result.meta.totalCostUsd;
    if (result.kind === "success") return { ok: true, vectors: result.vectors, costUsd };
    const detail = result.kind === "budget_exceeded" ? `상한 ${result.limitUsd} USD, 누적 ${result.spentUsd} USD` : result.message;
    return { ok: false, failure: { kind: result.kind, detail }, costUsd };
  }
}

export interface EmbeddingShortlisterOptions {
  client: EmbeddingClient;
  /** 임베딩 경로 이름. 색인을 만든 모델과 같은 모델을 가리켜야 한다. */
  route: string;
  tags: Tags;
  index: EmbeddingIndex;
  queryMode: EmbeddingQueryMode;
  /** 주면 합친 방식(hybrid)이다: 이 낱말 검색 순위와 임베딩 순위를 RRF 로 합친다. */
  fuseWith?: LexicalV2Options;
}

/**
 * `llm` 의 임베딩 호출과 파일 색인으로 조립한 `VectorShortlister`. 순위와 합산은 그쪽이 하고,
 * 여기서는 경로·태그를 묶고 호출 계층의 결과를 그대로 돌려주는 `warm` 을 얹는다.
 */
export class EmbeddingShortlister implements Shortlister {
  readonly method: ShortlistMethod;
  readonly description: string;
  private readonly embedder: LlmQueryEmbedder;
  private readonly inner: VectorShortlister;

  constructor(options: EmbeddingShortlisterOptions) {
    this.embedder = new LlmQueryEmbedder(options.client, options.route, options.tags);
    const model = this.embedder.model;
    if (model.key !== options.index.header.modelKey) {
      throw new Error(`임베딩 경로 ${options.route} 의 모델 ${model.key} 가 색인의 모델 ${options.index.header.modelKey} 와 다르다`);
    }
    this.inner = new VectorShortlister({ embedder: this.embedder, index: options.index, queryMode: options.queryMode, ...(options.fuseWith ? { fuseWith: options.fuseWith } : {}) });
    this.method = this.inner.method;
    this.description = this.inner.description;
  }

  /** 질의 글을 미리 한꺼번에 임베딩해 둔다 (측정용). 실패하면 그 결과를 그대로 돌려준다. */
  async warm(texts: readonly string[]): Promise<EmbedResult | null> {
    const missing = [...new Set(texts)].filter((text) => !this.inner.has(text));
    if (missing.length === 0) return null;
    const result = await this.embedder.embedRaw(missing);
    if (result.kind === "success") this.inner.seed(missing, result.vectors);
    return result;
  }

  /** 이미 임베딩한 질의 벡터를 넣어 둔다 (여러 방식이 같은 질의를 나눠 쓸 때). */
  seed(texts: readonly string[], vectors: readonly number[][]): void {
    this.inner.seed(texts, vectors);
  }

  shortlist(candidates: readonly Factor[], query: ShortlistQuery, limit: number): Promise<ShortlistOutcome> {
    return this.inner.shortlist(candidates, query, limit);
  }
}
