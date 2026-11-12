import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { resolveFromWorkspace } from "../src/cli.js";
import { ADJUSTMENTS_FILE, MEMORY_FILE, ROWS_FILE } from "../src/examples.js";
import { FileMappingMemory, SUPPORTED_CATEGORIES, groupRows, loadAdjustments, loadExampleSet, loadFactorStore } from "../src/index.js";

const DIR = resolveFromWorkspace("../data/scope3-examples");
const examples = loadExampleSet(DIR);
const store = loadFactorStore();

describe("예제 세트", () => {
  it("100행 이상이고 전부 synthetic 표시가 있다", () => {
    expect(examples.rows.length).toBeGreaterThanOrEqual(100);
    const raw = readFileSync(join(DIR, ROWS_FILE), "utf8").split("\n").filter((line) => line !== "");
    for (const line of raw) expect((JSON.parse(line) as { synthetic: unknown }).synthetic).toBe(true);
  });

  it("정답 계수 id 는 전부 밴드 C 의 계수 표에 실제로 있는 후보 계수다", () => {
    const missing: string[] = [];
    for (const example of examples.rows) {
      for (const id of example.gold.factorIds) if (!store.has(id) || !store.get(id)) missing.push(`${example.row.rowId}:${id}`);
    }
    expect(missing).toEqual([]);
  });

  it("정답 계수는 정답 카테고리의 후보다", () => {
    const wrong: string[] = [];
    for (const example of examples.rows) {
      for (const id of example.gold.factorIds) {
        if (!store.get(id)?.scope3Categories.includes(example.gold.category as number)) wrong.push(`${example.row.rowId}:${id}`);
      }
    }
    expect(wrong).toEqual([]);
  });

  it("계수 값을 베껴 적지 않는다: 예제 행에는 정답 id 만 있고 값 필드가 없다", () => {
    for (const line of readFileSync(join(DIR, ROWS_FILE), "utf8").split("\n").filter((entry) => entry !== "")) {
      const gold = (JSON.parse(line) as { gold: Record<string, unknown> }).gold;
      expect(Object.keys(gold).sort()).toEqual(["category", "factor_ids", "note"]);
    }
  });

  it("구매·물류·출장·통근이 모두 있고 정답을 달 수 없는 행도 있다", () => {
    const kinds = new Set(examples.rows.map((example) => example.kind));
    expect([...kinds].sort()).toEqual(["capital", "commute", "logistics", "other", "purchase", "travel"]);
    const categories = new Set(examples.rows.map((example) => example.gold.category));
    for (const category of SUPPORTED_CATEGORIES) expect(categories.has(category)).toBe(true);
    const unanswerable = examples.rows.filter((example) => example.gold.factorIds.length === 0);
    expect(unanswerable.length).toBeGreaterThan(0);
    for (const example of unanswerable) expect(example.gold.note).not.toBe("");
  });

  it("표기만 다른 같은 품목이 있어 묶음 수가 행 수보다 적다", () => {
    expect(groupRows(examples.rows.map((example) => example.row)).length).toBeLessThan(examples.rows.length);
  });

  it("기억 파일은 synthetic 이고 모든 항목이 예제의 묶음 키와 실제 계수를 가리킨다", () => {
    const memory = FileMappingMemory.open(join(DIR, MEMORY_FILE));
    expect(memory.synthetic).toBe(true);
    expect(memory.size).toBeGreaterThan(0);
    const raw = JSON.parse(readFileSync(join(DIR, MEMORY_FILE), "utf8")) as { entries: { key: string; factorId: string }[] };
    const keys = new Set(groupRows(examples.rows.map((example) => example.row)).map((group) => group.key));
    for (const entry of raw.entries) {
      expect(keys.has(entry.key), entry.key).toBe(true);
      expect(store.get(entry.factorId), entry.factorId).toBeDefined();
    }
  });

  it("보정 계수 파일은 synthetic 표시가 있다", () => {
    const adjustments = loadAdjustments(join(DIR, ADJUSTMENTS_FILE));
    expect(adjustments.length).toBeGreaterThan(0);
    for (const adjustment of adjustments) expect(adjustment.synthetic).toBe(true);
  });
});
