import { describe, expect, it } from "vitest";
import { assembleTranslation, checkConsistency, checkCoverage, chunkUnits, pairsOf, planUnits, type ChunkRecord, type Pair, type TranslateOutput } from "../src/index.js";
import { estimate, fixtureDocument, glossary, settings } from "./helpers.js";

const pair = (chunkId: string, id: string, source: string, target: string): Pair => ({ chunkId, id, source, target });

describe("용어 일관성 검사 (LLM 없음)", () => {
  it("원문에 용어집의 국문 용어가 있으면 번역문에 짝지어진 영문 용어가 있어야 한다", () => {
    const report = checkConsistency(glossary, [
      pair("c1", "b1", "재생에너지 사용을 늘렸습니다.", "We increased our use of Renewable Energy."),
      pair("c1", "b2", "탄소중립을 추진합니다.", "We are pursuing net zero."),
      pair("c2", "b3", "공급망 관리", "Value chain management"),
      pair("c2", "b4", "용어가 없는 문장입니다.", "A sentence without terms."),
    ]);
    expect(report.pairsChecked).toBe(4);
    expect(report.termOccurrences).toBe(3);
    expect(report.termMisses).toEqual([
      { chunkId: "c1", id: "b2", ko: "탄소중립", expected: "carbon neutrality", rendering: "variant:net zero" },
      { chunkId: "c2", id: "b3", ko: "공급망", expected: "supply chain", rendering: "unknown" },
    ]);
  });

  it("긴 용어를 먼저 맞춰, 그 안에 든 짧은 용어를 따로 요구하지 않는다", () => {
    const report = checkConsistency(glossary, [pair("c1", "b1", "온실가스 배출량", "GHG emissions")]);
    expect(report.termOccurrences).toBe(1);
    expect(report.termMisses).toEqual([]);
    const both = checkConsistency(glossary, [pair("c1", "b1", "온실가스 배출량과 온실가스 감축", "GHG emissions and GHG reduction")]);
    expect(both.termMisses).toEqual([{ chunkId: "c1", id: "b1", ko: "온실가스", expected: "greenhouse gas", rendering: "variant:GHG" }]);
  });

  it("조각 사이에 같은 용어가 다르게 번역된 곳을 보고한다", () => {
    const report = checkConsistency(glossary, [
      pair("c1", "b1", "협력회사 지원", "Support for suppliers"),
      pair("c2", "b2", "협력회사 평가", "Evaluation of partner companies"),
      pair("c3", "b3", "협력회사 교육", "Training for suppliers"),
      pair("c3", "b4", "임직원 교육", "Training for employees"),
    ]);
    expect(report.termDrifts).toEqual([{ ko: "협력회사", expected: "suppliers", renderings: { preferred: ["c1", "c3"], "variant:partner companies": ["c2"] } }]);
  });

  it("글자가 같은 원문이 다르게 번역된 곳을 보고한다 (용어집에 없어도)", () => {
    const report = checkConsistency(glossary, [
      pair("c1", "p1-b1", "활동", "Activities"),
      pair("c2", "p3-b1", "활동", "activities"),
      pair("c5", "p9-b1", "활동", "Initiatives"),
      pair("c5", "p9-b2", "추진방향", "Direction"),
    ]);
    expect(report.repeatDrifts).toEqual([
      {
        source: "활동",
        targets: [
          { text: "Activities", chunkIds: ["c1", "c2"], ids: ["p1-b1", "p3-b1"] },
          { text: "Initiatives", chunkIds: ["c5"], ids: ["p9-b1"] },
        ],
      },
    ]);
  });

  it("원문의 숫자가 번역문에서 사라지거나 바뀐 곳을 보고한다", () => {
    const report = checkConsistency(glossary, [
      pair("c1", "b1", "2025년 36,399 GWh 를 사용했습니다.", "We used 36,399 GWh in 2025."),
      pair("c1", "b2", "2025년 36,399 GWh 를 사용했습니다.", "We used 36,939 GWh in 2025."),
      pair("c1", "b3", "사업장 2곳과 2개 법인", "2 sites"),
    ]);
    expect(report.numberDrifts).toEqual([
      { chunkId: "c1", id: "b2", missingNumbers: ["36399"] },
      { chunkId: "c1", id: "b3", missingNumbers: ["2"] },
    ]);
  });
});

describe("누락·중복 검사", () => {
  const document = fixtureDocument();
  const plan = planUnits(document, settings);
  const chunks = chunkUnits(plan.units, { budgetTokens: 400, estimate, sections: document.sections, fingerprint: "fp" });
  const done = (index: number, drop: string[] = []): ChunkRecord<TranslateOutput> => ({
    chunkId: chunks[index]!.id,
    status: "done",
    output: { translations: Object.fromEntries(chunks[index]!.items.filter((item) => !drop.includes(item.id)).map((item) => [item.id, `en:${item.text}`])), unexpectedIds: [] },
    attempts: 1,
    costUsd: 0,
    warnings: [],
    finishedAt: "2026-10-01T00:00:00.000Z",
  });
  const allDone = chunks.map((_, index) => done(index));

  it("전부 끝났으면 원문 블록과 결과 블록이 하나씩 대응한다", () => {
    const result = assembleTranslation(document, plan, chunks, allDone);
    const report = checkCoverage(document, result, chunks, allDone);
    expect(report).toMatchObject({ ok: true, sourceBlocks: document.blocks.length, resultBlocks: document.blocks.length, notTranslated: [] });
    expect(pairsOf(chunks, allDone)).toHaveLength(chunks.reduce((sum, chunk) => sum + chunk.items.length, 0));
  });

  it("결과에서 빠진 블록, 두 번 나온 블록, 원문에 없는 블록을 잡는다", () => {
    const result = assembleTranslation(document, plan, chunks, allDone);
    const tampered = { ...result, blocks: [...result.blocks.slice(1), result.blocks[5]!, { ...result.blocks[2]!, id: "p99-b1" }], tables: result.tables.slice(1) };
    const report = checkCoverage(document, tampered, chunks, allDone);
    expect(report.ok).toBe(false);
    expect(report.missingBlocks).toEqual([document.blocks[0]!.id]);
    expect(report.duplicatedBlocks).toEqual([result.blocks[5]!.id]);
    expect(report.extraBlocks).toEqual(["p99-b1"]);
    expect(report.missingTables).toEqual([document.tables[0]!.id]);
  });

  it("끝났다고 기록된 조각에서 번역이 빠진 항목과 두 조각에 실린 항목을 잡는다", () => {
    const result = assembleTranslation(document, plan, chunks, allDone);
    const dropped = chunks[1]!.items[0]!.id;
    const records = allDone.map((record, index) => (index === 1 ? done(1, [dropped]) : record));
    expect(checkCoverage(document, result, chunks, records)).toMatchObject({ ok: false, itemsMissingInDoneChunks: [dropped] });

    const doubled = [...chunks, { ...chunks[0]!, id: "c9999-dup" }];
    const report = checkCoverage(document, result, doubled, [...allDone, undefined]);
    expect(report.ok).toBe(false);
    expect(report.itemsInSeveralChunks).toEqual(chunks[0]!.items.map((item) => item.id));
  });

  it("실패한 조각의 블록은 원문으로 메우지 않고 not_translated 로 남는다", () => {
    const records: (ChunkRecord<TranslateOutput> | undefined)[] = allDone.map((record, index) =>
      index === 0 ? { chunkId: record.chunkId, status: "failed", error: { kind: "refusal", message: "거부", retryable: false }, attempts: 1, costUsd: 0, warnings: [], finishedAt: record.finishedAt } : index === 1 ? undefined : record,
    );
    const result = assembleTranslation(document, plan, chunks, records);
    const failed = result.blocks.filter((block) => block.status === "not_translated");
    expect(failed.length).toBeGreaterThan(0);
    expect(new Set(failed.map((block) => block.status === "not_translated" && block.reason))).toEqual(new Set(["chunk_failed:refusal", "chunk_not_run"]));
    for (const block of failed) expect(block.status === "not_translated" && block.text).toBeNull();
    const report = checkCoverage(document, result, chunks, records);
    // 누락·중복은 없다. 번역되지 않은 것은 따로 센다.
    expect(report.ok).toBe(true);
    expect(report.notTranslated.length).toBeGreaterThanOrEqual(failed.length);
    expect(result.blocks.filter((block) => block.status === "translated").length).toBeGreaterThan(0);
  });

  it("조립 결과: 표의 각주 위첨자와 추출 상태를 잃지 않고, failed 표는 빈 표가 아니라 source_failed 다", () => {
    const result = assembleTranslation(document, plan, chunks, allDone);
    const byId = new Map(result.tables.map((table) => [table.id, table]));
    expect(byId.get("p1-t1")).toMatchObject({ status: "translated", sourceStatus: "ok", footnote_marks: [{ row: 1, col: 3, mark: "1)" }] });
    expect(byId.get("p1-t1")!.cells![1]).toEqual(["en:사업장 에너지 사용량", "GWh", "1,234", "1,567.8"]);
    expect(byId.get("p1-t2")).toMatchObject({ status: "translated", sourceStatus: "low_confidence", notes: ["side_by_side_split"] });
    expect(byId.get("p1-t2")!.cells).toEqual([["en:회사소개", "05"], ["en:환경성과", "69"]]);
    expect(byId.get("p1-t3")).toMatchObject({ status: "source_failed", sourceStatus: "failed", cells: null });
    expect(result.blocks.find((block) => block.id === "p1-b7")).toMatchObject({ status: "excluded", reason: "repeated_furniture", text: null });
    expect(result.blocks.find((block) => block.id === "p1-b6")).toMatchObject({ status: "copied", reason: "passthrough", text: "Scope 1, 2" });
  });
});
