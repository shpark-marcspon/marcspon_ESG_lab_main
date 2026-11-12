import { describe, expect, it } from "vitest";
import { chunkUnits, itemTokens, planUnits, type Unit } from "../src/index.js";
import { estimate, fixtureDocument, settings } from "./helpers.js";

function chunksFor(budgetTokens: number, options: Parameters<typeof fixtureDocument>[0] = {}, fingerprint = "fp") {
  const document = fixtureDocument(options);
  const plan = planUnits(document, settings);
  return { document, plan, chunks: chunkUnits(plan.units, { budgetTokens, estimate, sections: document.sections, fingerprint }) };
}

const unitTokens = (unit: Unit): number => unit.items.reduce((sum, item) => sum + itemTokens(item, estimate), 0);

describe("조각 분할", () => {
  it.each([150, 300, 600, 1500])("예산 %i: 표와 문단을 쪼개지 않는다 (단위마다 조각 하나, 항목이 통째로 실림)", (budget) => {
    const { plan, chunks } = chunksFor(budget);
    const seen = new Map<string, number>();
    for (const chunk of chunks) for (const unitId of chunk.unitIds) seen.set(unitId, (seen.get(unitId) ?? 0) + 1);
    expect([...seen.keys()]).toEqual(plan.units.map((unit) => unit.id));
    expect([...seen.values()].every((count) => count === 1)).toBe(true);
    // 조각의 항목은 그 조각에 실린 단위들의 항목을 순서대로 이어 붙인 것과 같다.
    const unitsById = new Map(plan.units.map((unit) => [unit.id, unit]));
    for (const chunk of chunks) {
      expect(chunk.items).toEqual(chunk.unitIds.flatMap((unitId) => unitsById.get(unitId)!.items));
    }
  });

  it.each([150, 300, 600, 1500])("예산 %i: 조각의 원문 토큰이 예산을 넘지 않는다", (budget) => {
    const { chunks } = chunksFor(budget);
    for (const chunk of chunks) {
      expect(chunk.sourceTokens).toBe(chunk.items.reduce((sum, item) => sum + itemTokens(item, estimate), 0));
      expect(chunk.overBudget).toBe(false);
      expect(chunk.sourceTokens).toBeLessThanOrEqual(budget);
    }
  });

  it("예산보다 큰 단위는 쪼개지 않고 혼자 조각이 되며 overBudget 으로 표시된다", () => {
    const { plan, chunks } = chunksFor(60);
    const largest = Math.max(...plan.units.map(unitTokens));
    expect(largest).toBeGreaterThan(60);
    const over = chunks.filter((chunk) => chunk.overBudget);
    expect(over.length).toBeGreaterThan(0);
    for (const chunk of over) expect(chunk.unitIds).toHaveLength(1);
    for (const chunk of chunks.filter((candidate) => !candidate.overBudget)) expect(chunk.sourceTokens).toBeLessThanOrEqual(60);
  });

  it("책갈피에서 온 섹션은 예산이 남아도 반드시 가른다", () => {
    const { chunks } = chunksFor(100_000, { basis: "pdf_outline" });
    expect(chunks).toHaveLength(3);
    for (const chunk of chunks) expect(chunk.sectionIds).toHaveLength(1);
  });

  it("추정한 섹션은 예산에 들어가면 한 조각에 싣는다", () => {
    const { chunks } = chunksFor(100_000);
    expect(chunks).toHaveLength(1);
    expect(chunks[0]!.sectionIds).toEqual(["s1", "s2", "s3"]);
  });

  it("추정한 섹션이라도 다음 섹션이 통째로 안 들어가면 섹션 경계에서 가른다", () => {
    const document = fixtureDocument();
    const plan = planUnits(document, settings);
    const sectionTokens = new Map<string, number>();
    for (const unit of plan.units) sectionTokens.set(unit.sectionId!, (sectionTokens.get(unit.sectionId!) ?? 0) + unitTokens(unit));
    // 섹션 하나는 예산에 들어가고 둘은 안 들어가는 예산.
    const budgetTokens = Math.max(...sectionTokens.values());
    expect(sectionTokens.get("s1")! + sectionTokens.get("s2")!).toBeGreaterThan(budgetTokens);
    const chunks = chunkUnits(plan.units, { budgetTokens, estimate, sections: document.sections, fingerprint: "fp" });
    expect(chunks.map((chunk) => chunk.sectionIds)).toEqual([["s1"], ["s2"], ["s3"]]);
    for (const chunk of chunks) expect(chunk.sourceTokens).toBeLessThanOrEqual(budgetTokens);
  });

  it("조각이 제목으로 끝나지 않게 끝에 걸린 제목을 다음 조각으로 넘긴다", () => {
    const { plan, chunks } = chunksFor(300);
    const kinds = new Map(plan.units.map((unit) => [unit.id, unit.type === "text" ? unit.blockKind : "table"]));
    const splitInsideSection = chunks.filter((chunk, index) => chunks[index + 1]?.sectionIds[0] === chunk.sectionIds[chunk.sectionIds.length - 1]);
    expect(splitInsideSection.length).toBeGreaterThan(0);
    for (const chunk of splitInsideSection) {
      if (chunk.unitIds.length > 1) expect(kinds.get(chunk.unitIds[chunk.unitIds.length - 1]!)).not.toBe("heading");
    }
  });

  it("조각 id 는 같은 입력이면 같고, 작업 지문이나 글자가 바뀌면 달라진다", () => {
    const first = chunksFor(300).chunks.map((chunk) => chunk.id);
    expect(chunksFor(300).chunks.map((chunk) => chunk.id)).toEqual(first);
    const other = chunksFor(300, {}, "다른 용어집").chunks.map((chunk) => chunk.id);
    expect(other).toHaveLength(first.length);
    expect(other.some((id, index) => id === first[index])).toBe(false);
    expect(new Set(first).size).toBe(first.length);
  });

  it("예산이 양의 정수가 아니면 거부한다", () => {
    expect(() => chunksFor(0)).toThrow(/조각 예산/);
  });
});
