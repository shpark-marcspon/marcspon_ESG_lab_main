import type { ChunkFailure, ChunkRecord, ChunkStore } from "./store.js";

export type WorkResult<O> =
  | { ok: true; output: O; costUsd: number; warnings: string[] }
  | { ok: false; error: ChunkFailure; costUsd: number; warnings: string[] };

export interface Progress {
  total: number;
  /** 이번 실행에서 끝낸 조각 */
  done: number;
  failed: number;
  /** 앞선 실행에서 이미 끝나 건너뛴 조각 */
  skipped: number;
  chunkId: string;
  outcome: "done" | "failed" | "skipped";
}

export interface RunOptions<C extends { id: string }, O> {
  chunks: C[];
  work: (chunk: C, attempt: number) => Promise<WorkResult<O>>;
  store: ChunkStore<O>;
  /** 동시에 실행하는 조각 수의 상한. */
  concurrency: number;
  /** 조각 하나를 부르는 횟수의 상한 (첫 시도 포함). `retryable` 인 실패만 다시 부른다. */
  maxAttempts: number;
  retryDelayMs?: number;
  onProgress?: (progress: Progress) => void;
  now?: () => Date;
}

export interface RunSummary<O> {
  total: number;
  done: number;
  failed: number;
  skipped: number;
  /** `work` 를 부른 횟수 (재시도 포함). */
  workCalls: number;
  /** 조각 순서대로. 건너뛴 조각은 앞선 실행의 기록이다. */
  records: ChunkRecord<O>[];
}

/**
 * 조각을 동시 실행 수 제한 아래에서 돌리고 조각마다 결과를 저장한다.
 * 조각 하나의 실패는 그 조각의 기록으로만 남고 다른 조각을 멈추지 않는다.
 * 다시 실행하면 `done` 으로 저장된 조각은 건너뛰고, `failed` 와 기록 없는 조각만 돌린다.
 */
export async function runChunks<C extends { id: string }, O>(options: RunOptions<C, O>): Promise<RunSummary<O>> {
  const { chunks, store } = options;
  if (!Number.isInteger(options.concurrency) || options.concurrency < 1) throw new Error(`동시 실행 수는 1 이상의 정수여야 한다: ${options.concurrency}`);
  if (!Number.isInteger(options.maxAttempts) || options.maxAttempts < 1) throw new Error(`시도 횟수는 1 이상의 정수여야 한다: ${options.maxAttempts}`);
  const ids = new Set(chunks.map((chunk) => chunk.id));
  if (ids.size !== chunks.length) throw new Error("조각 id 가 겹친다");

  const now = options.now ?? (() => new Date());
  const records = new Array<ChunkRecord<O>>(chunks.length);
  const summary = { total: chunks.length, done: 0, failed: 0, skipped: 0, workCalls: 0 };
  const report = (chunkId: string, outcome: Progress["outcome"]): void => {
    options.onProgress?.({ total: summary.total, done: summary.done, failed: summary.failed, skipped: summary.skipped, chunkId, outcome });
  };

  const runOne = async (chunk: C, index: number): Promise<void> => {
    const prior = store.read(chunk.id);
    if (prior?.status === "done") {
      records[index] = prior;
      summary.skipped += 1;
      report(chunk.id, "skipped");
      return;
    }

    let costUsd = 0;
    const warnings: string[] = [];
    let failure: ChunkFailure | undefined;
    let attempts = 0;
    while (attempts < options.maxAttempts) {
      attempts += 1;
      summary.workCalls += 1;
      let result: WorkResult<O>;
      try {
        result = await options.work(chunk, attempts);
      } catch (error) {
        // 조각 하나의 예외가 실행 전체를 넘어뜨리지 않게 그 조각의 실패로 남긴다.
        result = { ok: false, error: { kind: "exception", message: error instanceof Error ? `${error.name}: ${error.message}` : String(error), retryable: false }, costUsd: 0, warnings: [] };
      }
      costUsd += result.costUsd;
      for (const warning of result.warnings) if (!warnings.includes(warning)) warnings.push(warning);
      if (result.ok) {
        const record: ChunkRecord<O> = { chunkId: chunk.id, status: "done", output: result.output, attempts, costUsd, warnings, finishedAt: now().toISOString() };
        store.write(record);
        records[index] = record;
        summary.done += 1;
        report(chunk.id, "done");
        return;
      }
      failure = result.error;
      if (!failure.retryable) break;
      if (attempts < options.maxAttempts && options.retryDelayMs) await new Promise((resolve) => setTimeout(resolve, options.retryDelayMs));
    }
    const record: ChunkRecord<O> = { chunkId: chunk.id, status: "failed", error: failure as ChunkFailure, attempts, costUsd, warnings, finishedAt: now().toISOString() };
    store.write(record);
    records[index] = record;
    summary.failed += 1;
    report(chunk.id, "failed");
  };

  let next = 0;
  const worker = async (): Promise<void> => {
    while (next < chunks.length) {
      const index = next++;
      await runOne(chunks[index] as C, index);
    }
  };
  await Promise.all(Array.from({ length: Math.min(options.concurrency, chunks.length) }, worker));

  return { ...summary, records };
}
