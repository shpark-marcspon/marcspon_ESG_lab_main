import { createHash } from "node:crypto";
import type { EmbeddingProvider, EmbeddingProviderRequest, EmbeddingProviderResponse } from "../embeddings.js";
import type { InvokeOptions } from "../types.js";

export type MockEmbeddingReply = EmbeddingProviderResponse | ((request: EmbeddingProviderRequest) => EmbeddingProviderResponse);

/**
 * 가상(synthetic) 임베딩. 낱말마다 해시로 정한 자리에 1 을 더한 뒤 길이 1 로 맞춘다.
 * 같은 낱말을 나눠 가진 글끼리 코사인 유사도가 높아질 뿐이고 뜻을 알지 못한다. 검색 품질의 근거로 쓰지 않는다.
 */
export function syntheticEmbedding(text: string, dimensions: number): number[] {
  const vector = new Array<number>(dimensions).fill(0);
  const words = text.normalize("NFKC").toLowerCase().split(/[^\p{L}\p{N}]+/u).filter((word) => word !== "");
  for (const word of words) {
    const slot = createHash("sha256").update(word).digest().readUInt32BE(0) % dimensions;
    vector[slot] = (vector[slot] as number) + 1;
  }
  const norm = Math.sqrt(vector.reduce((sum, value) => sum + value * value, 0));
  return norm === 0 ? vector : vector.map((value) => value / norm);
}

/** 키 없이 쓰는 모의 임베딩 공급사. 심어 둔 응답이 있으면 그것을, 없으면 가상 임베딩을 돌려준다. 토큰 수는 낱말 수로 지어낸 값이다. */
export class MockEmbeddingProvider implements EmbeddingProvider {
  readonly calls: EmbeddingProviderRequest[] = [];
  private readonly queue: MockEmbeddingReply[] = [];

  constructor(
    private readonly dimensions: number,
    readonly name = "mock",
  ) {}

  /** 다음 호출이 받을 응답을 심는다 (실패 시험용). */
  enqueue(reply: MockEmbeddingReply): this {
    this.queue.push(reply);
    return this;
  }

  async embed(request: EmbeddingProviderRequest, _options: InvokeOptions): Promise<EmbeddingProviderResponse> {
    this.calls.push(request);
    const scripted = this.queue.shift();
    if (scripted) return typeof scripted === "function" ? scripted(request) : scripted;
    return {
      kind: "completed",
      vectors: request.inputs.map((input) => syntheticEmbedding(input, this.dimensions)),
      usage: { inputTokens: request.inputs.reduce((sum, input) => sum + input.split(/\s+/).filter((word) => word !== "").length, 0) },
      modelId: request.apiModelId,
    };
  }
}
