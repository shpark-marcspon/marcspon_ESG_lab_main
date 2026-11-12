import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import type { EmbedResult, EmbeddingClient, Tags } from "llm";
import type { Factor } from "../domain/factor.js";
import type { FactorVectorIndex } from "../domain/ports.js";
import { factorText } from "../domain/ranking.js";

/** 색인을 읽을 수 없거나 형식이 다르다. */
export class EmbeddingIndexError extends Error {
  override name = "EmbeddingIndexError";
}

/** 색인이 지금의 계수 표나 모델과 맞지 않는다. 낡은 색인으로 검색하지 않는다. */
export class StaleIndexError extends EmbeddingIndexError {
  override name = "StaleIndexError";
}

/** 계수를 글로 바꾸는 방식의 판. `factorText` 를 바꾸면 올린다. */
export const INDEX_TEXT_VERSION = 1;
const FORMAT = "scope3-embedding-index@1";

export interface EmbeddingIndexHeader {
  format: typeof FORMAT;
  /** 설정 파일의 임베딩 모델 키와 공급사의 모델 ID. */
  modelKey: string;
  apiModelId: string;
  dimensions: number;
  /** 색인을 만들 때 읽은 계수 표 파일의 sha256. */
  factorsSha256: string;
  textVersion: number;
  /** 만든 날짜 (ISO 8601). */
  createdAt: string;
  /** 벡터의 순서와 같은 후보 계수 id. */
  ids: string[];
  /** 만들 때 공급사가 보고한 입력 토큰 수와 비용. */
  inputTokens: number;
  costUsd: number;
}

export function sha256File(path: string): string {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

/** 후보 계수의 임베딩 색인. 벡터 DB 가 아니라 메모리에 올린 배열이다. `FactorVectorIndex` 포트의 파일 구현이다. */
export class EmbeddingIndex implements FactorVectorIndex {
  private readonly offsets = new Map<string, number>();

  constructor(
    readonly header: EmbeddingIndexHeader,
    private readonly data: Float32Array,
  ) {
    if (data.length !== header.ids.length * header.dimensions) {
      throw new EmbeddingIndexError(`색인의 벡터 크기가 맞지 않는다: ${data.length} != ${header.ids.length} × ${header.dimensions}`);
    }
    header.ids.forEach((id, position) => this.offsets.set(id, position * header.dimensions));
  }

  get size(): number {
    return this.header.ids.length;
  }

  vector(id: string): Float32Array | undefined {
    const offset = this.offsets.get(id);
    return offset === undefined ? undefined : this.data.subarray(offset, offset + this.header.dimensions);
  }

  /** 파일 하나로 쓴다: 머리 길이(4바이트 LE) + 머리 JSON + Float32 벡터. */
  save(path: string): void {
    const head = Buffer.from(JSON.stringify(this.header), "utf8");
    const length = Buffer.alloc(4);
    length.writeUInt32LE(head.length, 0);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, Buffer.concat([length, head, Buffer.from(this.data.buffer, this.data.byteOffset, this.data.byteLength)]));
  }
}

export interface IndexExpectation {
  /** 지금 계수 표 파일의 sha256. */
  factorsSha256: string;
  /** 지금 쓰려는 임베딩 모델의 키와 차원. */
  modelKey: string;
  dimensions: number;
}

/** 색인이 지금의 계수 표·모델과 맞는지 본다. 하나라도 다르면 `StaleIndexError` 다. */
export function assertFresh(header: EmbeddingIndexHeader, expected: IndexExpectation): void {
  if (header.factorsSha256 !== expected.factorsSha256) {
    throw new StaleIndexError(`색인이 낡았다: 계수 표의 해시가 다르다 (색인 ${header.factorsSha256.slice(0, 12)}…, 지금 ${expected.factorsSha256.slice(0, 12)}…). 색인을 다시 만든다.`);
  }
  if (header.modelKey !== expected.modelKey || header.dimensions !== expected.dimensions) {
    throw new StaleIndexError(`색인의 모델이 다르다 (색인 ${header.modelKey} ${header.dimensions}차원, 지금 ${expected.modelKey} ${expected.dimensions}차원)`);
  }
  if (header.textVersion !== INDEX_TEXT_VERSION) {
    throw new StaleIndexError(`색인을 만든 글 형식의 판이 다르다 (색인 ${header.textVersion}, 지금 ${INDEX_TEXT_VERSION})`);
  }
}

export function loadEmbeddingIndex(path: string, expected: IndexExpectation): EmbeddingIndex {
  if (!existsSync(path)) throw new EmbeddingIndexError(`임베딩 색인이 없다: ${path}`);
  const file = readFileSync(path);
  let header: EmbeddingIndexHeader;
  let body: Buffer;
  try {
    const length = file.readUInt32LE(0);
    header = JSON.parse(file.subarray(4, 4 + length).toString("utf8")) as EmbeddingIndexHeader;
    body = file.subarray(4 + length);
  } catch {
    throw new EmbeddingIndexError(`임베딩 색인을 읽을 수 없다: ${path}`);
  }
  if (header.format !== FORMAT) throw new EmbeddingIndexError(`임베딩 색인의 형식이 다르다: ${String(header.format)}`);
  assertFresh(header, expected);
  if (body.byteLength % 4 !== 0) throw new EmbeddingIndexError(`임베딩 색인의 벡터 길이가 4의 배수가 아니다: ${path}`);
  // 파일 버퍼의 오프셋이 4의 배수가 아닐 수 있어 복사한다.
  const data = new Float32Array(body.byteLength / 4);
  Buffer.from(data.buffer).set(body);
  return new EmbeddingIndex(header, data);
}

export type BuildIndexOutcome = { ok: true; index: EmbeddingIndex } | { ok: false; failure: Exclude<EmbedResult, { kind: "success" }> };

/** 후보 계수 전부를 임베딩해 색인을 만든다. 호출이 실패하면 색인을 만들지 않고 실패를 그대로 돌려준다. */
export async function buildEmbeddingIndex(options: { client: EmbeddingClient; route: string; tags: Tags; store: { readonly sha256: string }; factors: readonly Factor[]; now?: () => Date }): Promise<BuildIndexOutcome> {
  const model = options.client.model(options.route);
  const result = await options.client.embed({ route: options.route, inputs: options.factors.map(factorText), tags: options.tags });
  if (result.kind !== "success") return { ok: false, failure: result };
  const data = new Float32Array(options.factors.length * model.dimensions);
  result.vectors.forEach((vector, position) => data.set(vector, position * model.dimensions));
  const header: EmbeddingIndexHeader = {
    format: FORMAT,
    modelKey: model.key,
    apiModelId: model.apiModelId,
    dimensions: model.dimensions,
    factorsSha256: options.store.sha256,
    textVersion: INDEX_TEXT_VERSION,
    createdAt: (options.now?.() ?? new Date()).toISOString(),
    ids: options.factors.map((factor) => factor.id),
    inputTokens: result.meta.inputTokens,
    costUsd: result.meta.totalCostUsd,
  };
  return { ok: true, index: new EmbeddingIndex(header, data) };
}
