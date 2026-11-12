import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import type { EmbedResult, EmbeddingClient, Tags } from "llm";

// 단위(표·도표·쪽)의 임베딩 색인 파일. scope3 의 `embeddingIndex.ts` 와 같은 꼴이다 (머리 길이 4바이트 + 머리 JSON + Float32 벡터, 신선도 머리).
// 그쪽은 계수 표(`Factor`)에 묶여 있어 여기에 따로 둔다. 벡터 DB 가 아니라 메모리에 올린 배열이다.

/** 색인을 읽을 수 없거나 형식이 다르다. */
export class UnitIndexError extends Error {
  override name = "UnitIndexError";
}

/** 색인이 지금의 문서·모델·단위 글과 맞지 않는다. 낡은 색인으로 지목하지 않는다. */
export class StaleIndexError extends UnitIndexError {
  override name = "StaleIndexError";
}

/** 단위를 글로 바꾸는 방식의 판. `embed-nominate.ts` 의 단위 글을 바꾸면 올린다. */
export const UNIT_TEXT_VERSION = 1;
const FORMAT = "reconcile-unit-index@1";
/** 한 번의 임베딩 호출에 싣는 글자 수의 한도. 한 요청의 토큰 합 한도(300,000)를 글자당 2토큰으로 잡아도 넘지 않게 한다. */
export const MAX_CHARS_PER_CALL = 80_000;

export interface UnitIndexHeader {
  format: typeof FORMAT;
  modelKey: string;
  apiModelId: string;
  dimensions: number;
  /** 색인을 만들 때 읽은 문서 모델 파일의 sha256. */
  documentSha256: string;
  /** 단위의 갈래 (`table`, `page`, `chart-code` …). */
  unitKind: string;
  textVersion: number;
  /** 입력(조각 ID 와 글)의 sha256. 글이 한 글자라도 다르면 다른 색인이다. */
  inputsSha256: string;
  createdAt: string;
  /** 벡터의 순서와 같은 조각 ID (`단위 ID#c0`). */
  ids: string[];
  requests: number;
  inputTokens: number;
  costUsd: number;
}

export interface IndexInput {
  id: string;
  text: string;
}

export const inputsSha256 = (inputs: readonly IndexInput[]): string => createHash("sha256").update(JSON.stringify(inputs.map((input) => [input.id, input.text]))).digest("hex");
export const sha256File = (path: string): string => createHash("sha256").update(readFileSync(path)).digest("hex");

export class UnitIndex {
  private readonly offsets = new Map<string, number>();

  constructor(
    readonly header: UnitIndexHeader,
    private readonly data: Float32Array,
  ) {
    if (data.length !== header.ids.length * header.dimensions) throw new UnitIndexError(`색인의 벡터 크기가 맞지 않는다: ${data.length} != ${header.ids.length} × ${header.dimensions}`);
    header.ids.forEach((id, position) => this.offsets.set(id, position * header.dimensions));
  }

  vector(id: string): Float32Array | undefined {
    const offset = this.offsets.get(id);
    return offset === undefined ? undefined : this.data.subarray(offset, offset + this.header.dimensions);
  }

  save(path: string): void {
    const head = Buffer.from(JSON.stringify(this.header), "utf8");
    const length = Buffer.alloc(4);
    length.writeUInt32LE(head.length, 0);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, Buffer.concat([length, head, Buffer.from(this.data.buffer, this.data.byteOffset, this.data.byteLength)]));
  }
}

export interface IndexExpectation {
  documentSha256: string;
  modelKey: string;
  dimensions: number;
  unitKind: string;
  inputsSha256: string;
}

/** 색인이 지금의 문서·모델·단위 글과 맞는지 본다. 하나라도 다르면 `StaleIndexError` 다. */
export function assertFresh(header: UnitIndexHeader, expected: IndexExpectation): void {
  if (header.documentSha256 !== expected.documentSha256) throw new StaleIndexError(`색인이 낡았다: 문서의 해시가 다르다 (색인 ${header.documentSha256.slice(0, 12)}…, 지금 ${expected.documentSha256.slice(0, 12)}…)`);
  if (header.modelKey !== expected.modelKey || header.dimensions !== expected.dimensions) throw new StaleIndexError(`색인의 모델이 다르다 (색인 ${header.modelKey} ${header.dimensions}차원, 지금 ${expected.modelKey} ${expected.dimensions}차원)`);
  if (header.unitKind !== expected.unitKind) throw new StaleIndexError(`색인의 단위가 다르다 (색인 ${header.unitKind}, 지금 ${expected.unitKind})`);
  if (header.textVersion !== UNIT_TEXT_VERSION) throw new StaleIndexError(`색인을 만든 글 형식의 판이 다르다 (색인 ${header.textVersion}, 지금 ${UNIT_TEXT_VERSION})`);
  if (header.inputsSha256 !== expected.inputsSha256) throw new StaleIndexError(`색인이 낡았다: 단위의 글이 다르다 (색인 ${header.inputsSha256.slice(0, 12)}…, 지금 ${expected.inputsSha256.slice(0, 12)}…)`);
}

export function loadUnitIndex(path: string, expected: IndexExpectation): UnitIndex {
  if (!existsSync(path)) throw new UnitIndexError(`임베딩 색인이 없다: ${path}`);
  const file = readFileSync(path);
  let header: UnitIndexHeader;
  let body: Buffer;
  try {
    const length = file.readUInt32LE(0);
    header = JSON.parse(file.subarray(4, 4 + length).toString("utf8")) as UnitIndexHeader;
    body = file.subarray(4 + length);
  } catch {
    throw new UnitIndexError(`임베딩 색인을 읽을 수 없다: ${path}`);
  }
  if (header.format !== FORMAT) throw new UnitIndexError(`임베딩 색인의 형식이 다르다: ${String(header.format)}`);
  assertFresh(header, expected);
  if (body.byteLength % 4 !== 0) throw new UnitIndexError(`임베딩 색인의 벡터 길이가 4의 배수가 아니다: ${path}`);
  const data = new Float32Array(body.byteLength / 4);
  Buffer.from(data.buffer).set(body);
  return new UnitIndex(header, data);
}

/** 입력을 호출 단위로 나눈다 (글자 수 합의 한도). */
export function splitCalls(inputs: readonly IndexInput[], maxChars: number = MAX_CHARS_PER_CALL): IndexInput[][] {
  const calls: IndexInput[][] = [];
  let current: IndexInput[] = [];
  let chars = 0;
  for (const input of inputs) {
    if (current.length > 0 && chars + input.text.length > maxChars) {
      calls.push(current);
      current = [];
      chars = 0;
    }
    current.push(input);
    chars += input.text.length;
  }
  if (current.length > 0) calls.push(current);
  return calls;
}

export type BuildOutcome = { ok: true; index: UnitIndex } | { ok: false; failure: Exclude<EmbedResult, { kind: "success" }>; spentUsd: number };

/** 입력 전부를 임베딩해 색인을 만든다. 호출이 하나라도 실패하면 색인을 만들지 않고 실패를 그대로 돌려준다 (그때까지 쓴 비용과 함께). */
export async function buildUnitIndex(options: { client: EmbeddingClient; route: string; tags: Tags; documentSha256: string; unitKind: string; inputs: readonly IndexInput[]; now?: () => Date }): Promise<BuildOutcome> {
  const model = options.client.model(options.route);
  const data = new Float32Array(options.inputs.length * model.dimensions);
  let position = 0;
  let requests = 0;
  let inputTokens = 0;
  let costUsd = 0;
  for (const call of splitCalls(options.inputs)) {
    const result = await options.client.embed({ route: options.route, inputs: call.map((input) => input.text), tags: options.tags });
    requests += result.meta.requests;
    inputTokens += result.meta.inputTokens;
    costUsd += result.meta.totalCostUsd;
    if (result.kind !== "success") return { ok: false, failure: result, spentUsd: costUsd };
    for (const vector of result.vectors) {
      data.set(vector, position * model.dimensions);
      position += 1;
    }
  }
  const header: UnitIndexHeader = {
    format: FORMAT,
    modelKey: model.key,
    apiModelId: model.apiModelId,
    dimensions: model.dimensions,
    documentSha256: options.documentSha256,
    unitKind: options.unitKind,
    textVersion: UNIT_TEXT_VERSION,
    inputsSha256: inputsSha256(options.inputs),
    createdAt: (options.now?.() ?? new Date()).toISOString(),
    ids: options.inputs.map((input) => input.id),
    requests,
    inputTokens,
    costUsd: Math.round(costUsd * 1e8) / 1e8,
  };
  return { ok: true, index: new UnitIndex(header, data) };
}
