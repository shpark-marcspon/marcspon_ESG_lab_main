import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { join } from "node:path";

export interface ChunkFailure {
  /** LLM 결과 종류(`timeout`, `refusal`, …) 또는 이 파이프라인의 검증 실패(`incomplete_output`, `duplicate_ids`, `exception`). */
  kind: string;
  message: string;
  retryable: boolean;
}

export type ChunkRecord<O> =
  | { chunkId: string; status: "done"; output: O; attempts: number; costUsd: number; warnings: string[]; finishedAt: string }
  | { chunkId: string; status: "failed"; error: ChunkFailure; attempts: number; costUsd: number; warnings: string[]; finishedAt: string };

export interface ChunkStore<O> {
  /** 기록이 없으면 undefined. 읽을 수 없는 기록은 없는 것으로 치지 않고 오류다. */
  read(chunkId: string): ChunkRecord<O> | undefined;
  write(record: ChunkRecord<O>): void;
}

export class StoreError extends Error {
  override name = "StoreError";
}

/** 조각 id 하나에 파일 하나. 쓰다 만 파일이 남지 않게 임시 파일에 쓰고 이름을 바꾼다. */
export class FileChunkStore<O> implements ChunkStore<O> {
  constructor(private readonly dir: string) {
    mkdirSync(dir, { recursive: true });
  }

  private path(chunkId: string): string {
    if (!/^[A-Za-z0-9_-]+$/.test(chunkId)) throw new StoreError(`조각 id 를 파일 이름으로 쓸 수 없다: ${chunkId}`);
    return join(this.dir, `${chunkId}.json`);
  }

  read(chunkId: string): ChunkRecord<O> | undefined {
    const path = this.path(chunkId);
    if (!existsSync(path)) return undefined;
    let record: ChunkRecord<O>;
    try {
      record = JSON.parse(readFileSync(path, "utf8")) as ChunkRecord<O>;
    } catch (error) {
      throw new StoreError(`${path}: 조각 기록을 읽을 수 없다: ${(error as Error).message}`);
    }
    const shaped = record.status === "done" ? record.output !== undefined && record.output !== null : record.status === "failed" && typeof record.error?.kind === "string";
    if (record.chunkId !== chunkId || !shaped) {
      throw new StoreError(`${path}: 조각 기록의 모양이 맞지 않는다`);
    }
    return record;
  }

  write(record: ChunkRecord<O>): void {
    const path = this.path(record.chunkId);
    const temp = `${path}.${process.pid}.tmp`;
    writeFileSync(temp, `${JSON.stringify(record, null, 1)}\n`, "utf8");
    renameSync(temp, path);
  }
}

export class MemoryChunkStore<O> implements ChunkStore<O> {
  readonly records = new Map<string, ChunkRecord<O>>();

  read(chunkId: string): ChunkRecord<O> | undefined {
    return this.records.get(chunkId);
  }

  write(record: ChunkRecord<O>): void {
    this.records.set(record.chunkId, record);
  }
}
