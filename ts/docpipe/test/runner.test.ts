import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { FileChunkStore, MemoryChunkStore, StoreError, runChunks, type Progress, type WorkResult } from "../src/index.js";
import { tempDir } from "./helpers.js";

const chunks = (count: number) => Array.from({ length: count }, (_, index) => ({ id: `c${index + 1}` }));
const ok = (output: string): WorkResult<string> => ({ ok: true, output, costUsd: 0.001, warnings: [] });
const fail = (kind: string, retryable: boolean): WorkResult<string> => ({ ok: false, error: { kind, message: kind, retryable }, costUsd: 0, warnings: [] });

describe("조각 실행기", () => {
  it("동시 실행 수가 상한을 넘지 않는다", async () => {
    let running = 0;
    let peak = 0;
    const summary = await runChunks({
      chunks: chunks(12),
      store: new MemoryChunkStore<string>(),
      concurrency: 3,
      maxAttempts: 1,
      work: async (chunk) => {
        running += 1;
        peak = Math.max(peak, running);
        await new Promise((resolve) => setTimeout(resolve, 5));
        running -= 1;
        return ok(chunk.id);
      },
    });
    expect(peak).toBe(3);
    expect(summary).toMatchObject({ total: 12, done: 12, failed: 0, skipped: 0, workCalls: 12 });
    expect(summary.records.map((record) => record.chunkId)).toEqual(chunks(12).map((chunk) => chunk.id));
  });

  it("이어 하기: 끝난 조각 재호출 없음", async () => {
    const store = new FileChunkStore<string>(tempDir());
    const called: string[] = [];
    let c2Failures = 1;
    const work = async (chunk: { id: string }) => {
      called.push(chunk.id);
      return chunk.id === "c2" && c2Failures-- > 0 ? fail("refusal", false) : ok(`out-${chunk.id}`);
    };
    const first = await runChunks({ chunks: chunks(4), store, concurrency: 2, maxAttempts: 1, work });
    expect(first).toMatchObject({ done: 3, failed: 1, skipped: 0, workCalls: 4 });

    called.length = 0;
    const second = await runChunks({ chunks: chunks(4), store, concurrency: 2, maxAttempts: 1, work });
    // 끝난 세 조각은 다시 부르지 않고, 실패했던 조각만 다시 돈다.
    expect(called).toEqual(["c2"]);
    expect(second).toMatchObject({ done: 1, failed: 0, skipped: 3, workCalls: 1 });
    expect(second.records.map((record) => record.status)).toEqual(["done", "done", "done", "done"]);

    called.length = 0;
    const third = await runChunks({ chunks: chunks(4), store, concurrency: 2, maxAttempts: 1, work });
    expect(called).toEqual([]);
    expect(third).toMatchObject({ done: 0, failed: 0, skipped: 4, workCalls: 0 });
  });

  it("조각 실패 격리: 한 조각이 실패해도 나머지는 끝나고, 실패는 실패로 저장된다", async () => {
    const store = new MemoryChunkStore<string>();
    const summary = await runChunks({
      chunks: chunks(5),
      store,
      concurrency: 2,
      maxAttempts: 2,
      work: async (chunk) => {
        if (chunk.id === "c3") return fail("refusal", false);
        if (chunk.id === "c4") throw new Error("터짐");
        return ok(chunk.id);
      },
    });
    expect(summary).toMatchObject({ total: 5, done: 3, failed: 2 });
    expect(store.read("c3")).toMatchObject({ status: "failed", error: { kind: "refusal" }, attempts: 1 });
    expect(store.read("c4")).toMatchObject({ status: "failed", error: { kind: "exception", message: "Error: 터짐" }, attempts: 1 });
    expect(summary.records.filter((record) => record.status === "done").map((record) => record.chunkId)).toEqual(["c1", "c2", "c5"]);
  });

  it("재시도 가능한 실패만 다시 부르고 시도 횟수 상한에서 멈춘다", async () => {
    const calls: Record<string, number> = {};
    const summary = await runChunks({
      chunks: chunks(3),
      store: new MemoryChunkStore<string>(),
      concurrency: 1,
      maxAttempts: 3,
      work: async (chunk, attempt) => {
        calls[chunk.id] = attempt;
        if (chunk.id === "c1") return attempt < 3 ? fail("timeout", true) : ok("c1");
        if (chunk.id === "c2") return fail("timeout", true);
        return fail("schema_mismatch", false);
      },
    });
    expect(calls).toEqual({ c1: 3, c2: 3, c3: 1 });
    expect(summary).toMatchObject({ done: 1, failed: 2, workCalls: 7 });
    expect(summary.records[0]).toMatchObject({ status: "done", attempts: 3 });
    expect(summary.records[1]).toMatchObject({ status: "failed", attempts: 3, error: { kind: "timeout" } });
  });

  it("진행률을 조각마다 알린다", async () => {
    const events: Progress[] = [];
    await runChunks({
      chunks: chunks(3),
      store: new MemoryChunkStore<string>(),
      concurrency: 1,
      maxAttempts: 1,
      work: async (chunk) => (chunk.id === "c2" ? fail("refusal", false) : ok(chunk.id)),
      onProgress: (progress) => events.push(progress),
    });
    expect(events.map((event) => [event.chunkId, event.outcome, event.done, event.failed])).toEqual([
      ["c1", "done", 1, 0],
      ["c2", "failed", 1, 1],
      ["c3", "done", 2, 1],
    ]);
  });

  it("파일 저장소: 조각 id 로 저장하고 다시 읽는다. 읽을 수 없는 기록은 없는 것으로 치지 않는다", () => {
    const dir = tempDir();
    const store = new FileChunkStore<string>(dir);
    expect(store.read("c1")).toBeUndefined();
    store.write({ chunkId: "c1", status: "done", output: "결과", attempts: 1, costUsd: 0, warnings: [], finishedAt: "2026-10-01T00:00:00.000Z" });
    expect(readdirSync(dir)).toEqual(["c1.json"]);
    expect(JSON.parse(readFileSync(join(dir, "c1.json"), "utf8"))).toMatchObject({ chunkId: "c1", status: "done", output: "결과" });
    expect(new FileChunkStore<string>(dir).read("c1")).toMatchObject({ status: "done", output: "결과" });

    writeFileSync(join(dir, "c2.json"), "{ 깨진 파일");
    expect(() => store.read("c2")).toThrow(StoreError);
    expect(() => store.read("../밖")).toThrow(StoreError);
    writeFileSync(join(dir, "c3.json"), JSON.stringify({ chunkId: "c3", status: "done" }));
    expect(() => store.read("c3")).toThrow(/모양이 맞지 않는다/);
  });

  it("겹치는 조각 id 와 잘못된 동시 실행 수는 거부한다", async () => {
    const base = { store: new MemoryChunkStore<string>(), maxAttempts: 1, work: async () => ok("x") };
    await expect(runChunks({ ...base, chunks: [{ id: "a" }, { id: "a" }], concurrency: 1 })).rejects.toThrow(/겹친다/);
    await expect(runChunks({ ...base, chunks: chunks(1), concurrency: 0 })).rejects.toThrow(/동시 실행 수/);
  });
});
