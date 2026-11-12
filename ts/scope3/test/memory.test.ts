import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { FileMappingMemory, InMemoryMappingMemory, MemoryError, type MemoryEntry } from "../src/index.js";

const ENTRY: MemoryEntry = { key: "a4용지80g|kg|", category: 1, factorId: "syn:steel-kg", confirmedBy: "tester-synthetic", confirmedAt: "2026-09-30T09:00:00+09:00" };

function tempPath(): string {
  return join(mkdtempSync(join(tmpdir(), "scope3-memory-")), "memory.synthetic.json");
}

describe("확정 매핑 기억", () => {
  it("없는 키는 undefined 다", () => {
    expect(new InMemoryMappingMemory([ENTRY]).lookup("없는키")).toBeUndefined();
  });

  it("파일에 확정을 쓰고 다시 열면 그대로 읽힌다", () => {
    const path = tempPath();
    const memory = FileMappingMemory.open(path, { createIfMissing: true, synthetic: true });
    memory.confirm(ENTRY);
    memory.confirm({ ...ENTRY, factorId: "syn:steel-t" });
    const reopened = FileMappingMemory.open(path);
    expect(reopened.size).toBe(1);
    expect(reopened.lookup(ENTRY.key)).toEqual({ ...ENTRY, factorId: "syn:steel-t" });
    expect(JSON.parse(readFileSync(path, "utf8")).synthetic).toBe(true);
  });

  it("파일이 없으면 빈 기억이 아니라 예외다", () => {
    expect(() => FileMappingMemory.open(tempPath())).toThrow(MemoryError);
  });

  it("깨진 파일과 형식이 다른 파일은 예외다", () => {
    const broken = tempPath();
    writeFileSync(broken, "{");
    expect(() => FileMappingMemory.open(broken)).toThrow(/JSON 이 아니다/);
    const wrong = tempPath();
    writeFileSync(wrong, JSON.stringify({ version: 1, synthetic: true, entries: [{ ...ENTRY, category: 5 }] }));
    expect(() => FileMappingMemory.open(wrong)).toThrow(/형식이 다르다/);
    const duplicated = tempPath();
    writeFileSync(duplicated, JSON.stringify({ version: 1, synthetic: true, entries: [ENTRY, ENTRY] }));
    expect(() => FileMappingMemory.open(duplicated)).toThrow(/같은 키/);
  });
});
