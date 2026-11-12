import { describe, expect, it } from "vitest";
import {
  FactorStore,
  analyzeCoverage,
  evaluate,
  groupKey,
  planDryRun,
  runPipeline,
  sectorKey,
  InMemoryMappingMemory,
  type ExampleRow,
  type Factor,
} from "../src/index.js";
import { loadConfig, defaultConfigPaths } from "llm";
import { classification, harness, recommendation, row, writeSyntheticFactors } from "./helpers.js";

function example(rowId: string, itemName: string, unit: string, category: number | null, factorIds: string[]): ExampleRow {
  return { kind: "purchase", row: row({ rowId, itemName, unit }), gold: { category, factorIds, note: "테스트 (synthetic)" } };
}

const EXAMPLES: ExampleRow[] = [
  example("m1", "합성 강판", "kg", 1, ["syn:steel-kg"]), // 기억 적중, id 일치
  example("a1", "폴리프로필렌", "kg", 1, ["syn:pp-kg"]), // LLM, top-1 은 틀리고 top-3 안에 정답
  example("s1", "노트북", "대", 2, ["syn:spend-computer"]), // LLM 이 1 로 잘못 분류, 같은 부문(NAICS 3341)의 다른 계수 추천
  example("s2", "법률 자문", "식", 1, ["syn:spend-legal"]), // LLM, 다른 부문 추천
  example("u1", "출장 일비", "일", 6, []), // 정답 없음, 추천 안 냄
  example("u2", "폐기물 처리", "kg", 5, []), // AI 적용 범위 밖, other 로 답해야 맞다
  example("u3", "기타 잡비", "식", null, []), // 카테고리 정답 없음, 추천을 내 버림
];

async function run() {
  const memory = new InMemoryMappingMemory([
    { key: groupKey(EXAMPLES[0]!.row), category: 1, factorId: "syn:steel-kg", confirmedBy: "tester-synthetic", confirmedAt: "2026-09-30T09:00:00+09:00" },
  ]);
  const { mock, pipeline, client } = harness({ memory });
  mock
    .enqueueText(classification(1))
    .enqueueText(recommendation(["syn:steel-kg", "syn:pp-kg"]))
    .enqueueText(classification(1))
    .enqueueText(recommendation(["syn:spend-storage"]))
    .enqueueText(classification(1))
    .enqueueText(recommendation(["syn:spend-computer", "syn:spend-storage"]))
    .enqueueText(classification(6))
    .enqueueText(classification("other"))
    .enqueueText(classification(1))
    .enqueueText(recommendation(["syn:spend-legal"]));
  const result = await runPipeline(EXAMPLES.map((entry) => entry.row), pipeline);
  return { result, pipeline, client, memory, mock };
}

describe("평가 지표", () => {
  it("분류 정확도, top-3 적중률(두 기준), 기억 적중 비율, 호출 수, 비용을 낸다", async () => {
    const { result, pipeline } = await run();
    const report = evaluate(EXAMPLES, result, pipeline.store);

    expect(report.counts).toEqual({ rows: 7, groups: 7, withGoldCategory: 6, withGoldFactor: 4, unanswerable: 3 });
    expect(report.memory).toEqual({ hitRows: 1, hitGroups: 1, rowRatio: 1 / 7, groupRatio: 1 / 7 });
    // 맞힌 분류: m1, a1, s2, u1, u2. 틀린 분류: s1 (2 → 1). u3 은 정답이 없어 분모에서 빠진다.
    expect(report.classification).toMatchObject({ hits: 5, evaluated: 6, rate: 5 / 6, llmOnly: { hits: 4, evaluated: 5 } });
    expect(report.top1.factor_id).toEqual({ hits: 1, evaluated: 4, rate: 0.25 });
    expect(report.top3.factor_id).toEqual({ hits: 2, evaluated: 4, rate: 0.5 });
    // 같은 부문 기준에서는 s1 (334112 ↔ 334111, NAICS 4자리 3341) 도 적중이다.
    expect(report.top3.sector).toEqual({ hits: 3, evaluated: 4, rate: 0.75 });
    expect(report.top3LlmOnly.factor_id).toEqual({ hits: 1, evaluated: 3, rate: 1 / 3 });
    expect(report.unanswerable).toEqual({ rows: 3, withoutRecommendation: 2, withRecommendation: 1 });
    expect(report.basis).toEqual({ activity: 2, distance: 0, spend: 3, fellBack: 3, byReason: { unit_mismatch: 3 } });
    expect(report.decisionStatus).toEqual({ recommended: 5, no_candidates: 1, unsupported_category: 1 });
    expect(report.calcStatus).toEqual({ computed: 2, needs_currency_adjustment: 3, not_calculated: 2 });
    // 분류 6회 + 추천 4회. 모의 사용량(입력 100, 출력 20)과 모의 단가(1, 2 USD/100만 토큰)로 호출당 0.00014 USD (synthetic).
    expect(report.llm).toMatchObject({ calls: 10, attempts: 10, costUsd: 0.0014 });
    expect(report.llm.byPrompt["scope3/classify@1"]).toEqual({ calls: 6, attempts: 6, costUsd: 0.00084 });
  });

  it("판정 기준은 설정으로 바꾼다: 대표 기준과 부문의 넓이", async () => {
    const { result, pipeline } = await run();
    expect(evaluate(EXAMPLES, result, pipeline.store).primaryCriterion).toBe("factor_id");
    const narrow = evaluate(EXAMPLES, result, pipeline.store, { primaryCriterion: "sector", sector: { naicsDigits: 6, pathDepth: 2 } });
    expect(narrow.primaryCriterion).toBe("sector");
    expect(narrow.top3.sector.hits).toBe(2);
    const wide = evaluate(EXAMPLES, result, pipeline.store, { sector: { naicsDigits: 2, pathDepth: 1 } });
    expect(wide.top3.sector.hits).toBe(3);
  });

  it("분류 부문: NAICS 는 앞자리, 그 밖은 출처와 분류 계층", () => {
    const store = FactorStore.load(writeSyntheticFactors());
    expect(sectorKey(store.get("syn:spend-computer") as Factor)).toBe("naics:3341");
    expect(sectorKey(store.get("syn:steel-kg") as Factor)).toBe("synthetic:합성 자재/금속");
    expect(sectorKey(store.get("syn:steel-kg") as Factor)).not.toBe(sectorKey(store.get("syn:pp-kg") as Factor));
    expect(sectorKey(store.get("syn:steel-kg") as Factor, { naicsDigits: 4, pathDepth: 1 })).toBe(sectorKey(store.get("syn:pp-kg") as Factor, { naicsDigits: 4, pathDepth: 1 }));
  });

  it("분모가 0 이면 비율은 0 이 아니라 null 이다", async () => {
    const only = [EXAMPLES[4]!];
    const { mock, pipeline } = harness();
    mock.enqueueText(classification(6));
    const report = evaluate(only, await runPipeline(only.map((entry) => entry.row), pipeline), pipeline.store);
    expect(report.top3.factor_id).toEqual({ hits: 0, evaluated: 0, rate: null });
  });

  it("정답 id 가 후보 계수 표에 없으면 평가를 거부한다", async () => {
    const broken = [example("x", "합성 강판", "kg", 1, ["syn:steel-kg-ch4"])];
    const { mock, pipeline } = harness();
    mock.enqueueText(classification(1)).enqueueText(recommendation(["syn:steel-kg"]));
    const result = await runPipeline(broken.map((entry) => entry.row), pipeline);
    expect(() => evaluate(broken, result, pipeline.store)).toThrow(/정답 계수 id/);
  });
});

describe("LLM 없이 세는 것", () => {
  it("단위가 맞는 활동 기반 후보가 있는 행과 지출 기반으로만 가능한 행을 센다", () => {
    const store = FactorStore.load(writeSyntheticFactors());
    const coverage = analyzeCoverage(EXAMPLES, store);
    expect(coverage.activityCandidates).toEqual({ category1: 3, category2: 0 });
    expect(coverage.rows).toEqual({ evaluated: 5, physicalPossible: 2, spendOnly: 2, noCandidates: 1, byReason: { no_activity_candidates: 1, unit_mismatch: 2 } });
    expect(coverage.rowsCategory12).toEqual({ evaluated: 4, physicalPossible: 2, spendOnly: 2 });
    expect(analyzeCoverage(EXAMPLES, store, ["disputed"]).activityCandidates.category1).toBe(2);
  });

  it("dry-run 은 공급사를 부르지 않고 호출 수와 비용 상한을 낸다", async () => {
    const { mock, pipeline, client, memory } = await run();
    const before = mock.calls.length;
    const plan = await planDryRun(EXAMPLES, { client, config: loadConfig(defaultConfigPaths({ synthetic: true })), store: pipeline.store, memory, route: "mock", tags: pipeline.tags });
    expect(mock.calls.length).toBe(before);
    expect(plan.groups).toEqual({ total: 7, memoryHits: 1, needLlm: 6, withoutAssumedCategory: 2 });
    // 분류 6회 + 추천 4회(a1, s1, s2, u1 중 u1 은 후보가 없어 0) = a1 활동, s1·s2 지출 → 최소 9. a1 은 지출 기반 재추천이 있을 수 있어 최대 10.
    expect(plan.minimum.calls).toBe(9);
    expect(plan.maximum.calls).toBe(10);
    expect(plan.maximum.costUpperBoundUsd).toBeGreaterThan(plan.minimum.costUpperBoundUsd);
    expect(plan.fullCandidateLists.find((entry) => entry.category === 1 && entry.basis === "spend")).toMatchObject({ candidates: 3, fits: true });
  });
});
