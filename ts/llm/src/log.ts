import { createHash } from "node:crypto";
import { appendFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";
import type { LlmResultKind, WarningCode } from "./types.js";

/** 호출 기록 한 줄. 공급사를 한 번 부를 때마다(재시도, 대체 호출 포함) 하나씩 남는다. */
export interface CallRecord {
  ts: string;
  callId: string;
  attempt: number;
  provider: string | null;
  model: string | null;
  apiModelId: string | null;
  /** 공급사가 응답에 적은 모델 ID. 응답을 못 받았거나 공급사가 적지 않았으면 null. */
  responseModelId: string | null;
  promptId: string;
  promptVersion: number;
  templateHash: string;
  /** 조립된 요청 본문의 sha256. 본문을 저장하지 않아도 같은 입력인지 견줄 수 있다. */
  requestHash: string;
  inputTokens: number | null;
  outputTokens: number | null;
  cacheReadTokens: number | null;
  cacheWriteTokens: number | null;
  /** 공급사가 생각(thinking)·추론(reasoning) 토큰 수를 보고했을 때만 들어간다. `outputTokens` 에 이미 든 몫이다. */
  thinkingTokens?: number;
  /** 공급사 오류(`provider_error`)일 때만 들어간다: HTTP 상태 코드. 상태 없이 실패했으면(연결 실패 등) null. */
  errorStatus?: number | null;
  /** 공급사 오류에 공급사가 「이만큼 뒤에 다시」를 알려 줬을 때만 들어간다 (ms). */
  retryAfterMs?: number;
  /**
   * 공급사 오류 뒤에 같은 모델을 다시 부른 시도일 때만 들어간다(재시도 도중 상한에 막힌 줄 포함).
   * `providerRetry` 는 몇 번째 재시도인지(1부터), `previousErrorStatus` 는 바로 앞 시도의 오류 상태 코드(상태 없이 실패했으면 null),
   * `retryWaitMs` 는 이 시도 전에 기다린 시간이다. 기다린 시간은 `durationMs` 에 들어 있지 않다.
   */
  providerRetry?: number;
  previousErrorStatus?: number | null;
  retryWaitMs?: number;
  costUsd: number | null;
  durationMs: number;
  resultKind: LlmResultKind;
  tenant: string;
  module: string;
  warnings: WarningCode[];
  /** 임베딩 호출일 때만 들어간다: 이 요청에 실은 입력 수. 임베딩 기록의 `promptId` 는 `embedding`, 출력 토큰은 0 이다. */
  embeddingInputs?: number;
  /** `storeBodies` 를 켰을 때만 들어간다. 기본은 저장하지 않는다(공시 전 고객 데이터). */
  body?: { request: unknown; responseText: string | null };
}

export interface CallLogSink {
  write(record: CallRecord): void;
}

export class JsonlFileSink implements CallLogSink {
  constructor(private readonly path: string) {
    mkdirSync(dirname(path), { recursive: true });
  }

  write(record: CallRecord): void {
    appendFileSync(this.path, `${JSON.stringify(record)}\n`, "utf8");
  }
}

export class MemorySink implements CallLogSink {
  readonly records: CallRecord[] = [];

  write(record: CallRecord): void {
    this.records.push(record);
  }
}

export function sha256(value: unknown): string {
  return createHash("sha256").update(JSON.stringify(value)).digest("hex");
}

