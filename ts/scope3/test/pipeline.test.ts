import { describe, expect, it } from "vitest";
import { InMemoryMappingMemory, groupKey, runPipeline, type MemoryEntry } from "../src/index.js";
import { classification, harness, recommendation, row, userTexts } from "./helpers.js";

function remembered(itemName: string, factorId: string, category: MemoryEntry["category"] = 1): MemoryEntry {
  return { key: groupKey(row({ itemName })), category, factorId, confirmedBy: "tester-synthetic", confirmedAt: "2026-09-30T09:00:00+09:00" };
}

describe("확정 매핑 기억이 먼저다", () => {
  it("기억에 있는 행은 LLM 을 부르지 않는다", async () => {
    const memory = new InMemoryMappingMemory([remembered("합성 강판", "syn:steel-kg")]);
    const { mock, pipeline } = harness({ memory });
    const result = await runPipeline([row({ rowId: "a", itemName: "합성 강판" }), row({ rowId: "b", itemName: "합성강판", quantity: "4" })], pipeline);

    expect(mock.calls).toHaveLength(0);
    expect(result.usage.calls).toBe(0);
    for (const entry of result.rows) {
      expect(entry.decision).toMatchObject({ status: "recommended", source: "memory", category: 1, picks: [{ factorId: "syn:steel-kg" }] });
    }
    expect(result.rows.map((entry) => entry.calc)).toMatchObject([
      { status: "computed", emissionsKgCo2e: "25" },
      { status: "computed", emissionsKgCo2e: "10" },
    ]);
  });

  it("기억에 없는 묶음만 LLM 으로 간다 (묶음당 분류 1회 + 추천 1회)", async () => {
    const memory = new InMemoryMappingMemory([remembered("합성 강판", "syn:steel-kg")]);
    const { mock, pipeline } = harness({ memory });
    mock.enqueueText(classification(1)).enqueueText(recommendation(["syn:pp-kg", "syn:steel-kg"]));
    const result = await runPipeline(
      [row({ rowId: "a", itemName: "합성 강판" }), row({ rowId: "b", itemName: "폴리프로필렌 수지" }), row({ rowId: "c", itemName: "폴리프로필렌  수지" })],
      pipeline,
    );
    expect(mock.calls).toHaveLength(2);
    expect(result.usage).toMatchObject({ calls: 2, attempts: 2 });
    expect(result.rows.map((entry) => entry.decision.source)).toEqual(["memory", "llm", "llm"]);
    expect(result.rows[2]?.decision).toMatchObject({ status: "recommended", picks: [{ factorId: "syn:pp-kg" }, { factorId: "syn:steel-kg" }] });
  });

  it("기억의 계수가 표에 없으면 LLM 으로 넘기지 않고 상태로 알린다", async () => {
    const { mock, pipeline } = harness({ memory: new InMemoryMappingMemory([remembered("합성 강판", "syn:gone")]) });
    const result = await runPipeline([row({ itemName: "합성 강판" })], pipeline);
    expect(mock.calls).toHaveLength(0);
    expect(result.rows[0]?.decision.status).toBe("memory_factor_missing");
    expect(result.rows[0]?.calc).toEqual({ status: "not_calculated", reason: "memory_factor_missing" });
  });

  it("기억의 카테고리가 그 계수의 카테고리 후보에 없으면 상태로 알린다", async () => {
    const { mock, pipeline } = harness({ memory: new InMemoryMappingMemory([remembered("합성 강판", "syn:steel-kg", 6)]) });
    const result = await runPipeline([row({ itemName: "합성 강판" })], pipeline);
    expect(mock.calls).toHaveLength(0);
    expect(result.rows[0]?.decision).toMatchObject({ status: "memory_category_mismatch", factorCategories: [1] });
  });

  it("기억의 계수가 라이선스 설정으로 제외돼 있으면 상태로 알린다", async () => {
    const { mock, pipeline } = harness({ memory: new InMemoryMappingMemory([remembered("폴리프로필렌", "syn:pp-kg")]), excludeLicenseStatuses: ["disputed"] });
    const result = await runPipeline([row({ itemName: "폴리프로필렌" })], pipeline);
    expect(mock.calls).toHaveLength(0);
    expect(result.rows[0]?.decision).toMatchObject({ status: "memory_factor_excluded", licenseStatus: "disputed" });
  });
});

describe("LLM 은 후보 목록 안의 id 만 고를 수 있다", () => {
  it("표에는 있지만 이번 후보에 없는 id 는 스키마 불일치로 거부한다", async () => {
    const { mock, pipeline } = harness();
    mock.enqueueText(classification(1));
    // syn:rail 은 표에 있는 계수지만 카테고리 6·7 의 것이라 이 품목의 후보 목록에 없다.
    for (let i = 0; i < 3; i += 1) mock.enqueueText(recommendation(["syn:rail"]));
    const result = await runPipeline([row({ itemName: "합성 강판" })], pipeline);

    expect(result.rows[0]?.decision).toMatchObject({ status: "recommendation_failed", basis: "physical", llm: { kind: "schema_mismatch" } });
    expect(result.rows[0]?.calc).toEqual({ status: "not_calculated", reason: "recommendation_failed" });
    // 분류 1회 + 추천 3회(처음 + 스키마 재시도 2회). 거부된 뒤 다른 후보로 넘어가지 않는다.
    expect(mock.calls).toHaveLength(4);
    expect(result.usage).toMatchObject({ calls: 2, attempts: 4 });
  });

  it("표에 없는 id 도 거부한다", async () => {
    const { mock, pipeline } = harness();
    mock.enqueueText(classification(1));
    for (let i = 0; i < 3; i += 1) mock.enqueueText(recommendation(["syn:steel-kg", "syn:invented"]));
    const result = await runPipeline([row({ itemName: "합성 강판" })], pipeline);
    expect(result.rows[0]?.decision).toMatchObject({ status: "recommendation_failed", llm: { kind: "schema_mismatch" } });
  });

  it("라이선스 설정으로 뺀 계수는 후보가 아니므로 고르면 거부한다", async () => {
    const { mock, pipeline } = harness({ excludeLicenseStatuses: ["disputed"] });
    mock.enqueueText(classification(1));
    for (let i = 0; i < 3; i += 1) mock.enqueueText(recommendation(["syn:pp-kg"]));
    const result = await runPipeline([row({ itemName: "폴리프로필렌" })], pipeline);
    expect(result.rows[0]?.decision).toMatchObject({ status: "recommendation_failed", llm: { kind: "schema_mismatch" } });
    expect(userTexts(mock)[1]).not.toContain("syn:pp-kg");
  });

  it("다시 불렀을 때 후보 안의 id 로 답하면 받아들인다", async () => {
    const { mock, pipeline } = harness();
    mock.enqueueText(classification(1)).enqueueText(recommendation(["syn:rail"])).enqueueText(recommendation(["syn:steel-kg"]));
    const result = await runPipeline([row({ itemName: "합성 강판" })], pipeline);
    expect(result.rows[0]?.decision).toMatchObject({ status: "recommended", picks: [{ factorId: "syn:steel-kg" }] });
  });

  it("같은 id 를 두 번 고르거나 네 개를 고르면 거부한다", async () => {
    const { mock, pipeline } = harness();
    mock.enqueueText(classification(1));
    mock.enqueueText(recommendation(["syn:steel-kg", "syn:steel-kg"]));
    mock.enqueueText(recommendation(["syn:steel-kg", "syn:steel-t", "syn:pp-kg", "syn:steel-kg"]));
    mock.enqueueText(JSON.stringify({ picks: [], none_suitable: false }));
    const result = await runPipeline([row({ itemName: "합성 강판" })], pipeline);
    expect(result.rows[0]?.decision).toMatchObject({ status: "recommendation_failed", llm: { kind: "schema_mismatch" } });
  });
});

describe("활동 기반 → 지출 기반", () => {
  it("단위가 맞는 활동 기반 후보가 있으면 그쪽을 쓴다", async () => {
    const { mock, pipeline } = harness();
    mock.enqueueText(classification(1)).enqueueText(recommendation(["syn:steel-t"]));
    const result = await runPipeline([row({ itemName: "합성 강판", quantity: "3", unit: "kg" })], pipeline);
    expect(result.rows[0]?.decision).toMatchObject({ status: "recommended", basis: { used: "activity", fellBack: false, fallbackReason: null } });
    // kg 수량을 톤 계수에 맞춘다: 3 × 0.001 × 2400.
    expect(result.rows[0]?.calc).toMatchObject({ status: "computed", emissionsKgCo2e: "7.2", activityAmount: "0.003" });
  });

  it("단위가 맞지 않으면 지출 기반으로 넘어가고 이유를 싣는다", async () => {
    const { mock, pipeline } = harness();
    mock.enqueueText(classification(1)).enqueueText(recommendation(["syn:spend-computer"]));
    const result = await runPipeline([row({ itemName: "노트북", quantity: "2", unit: "대" })], pipeline);
    expect(result.rows[0]?.decision).toMatchObject({
      status: "recommended",
      basis: { used: "spend", fellBack: true, fallbackReason: "unit_mismatch" },
      candidates: { total: 3, shown: 3, lexicalMatches: 0 },
    });
    expect(mock.calls).toHaveLength(2);
    expect(userTexts(mock)[1]).toContain("syn:spend-computer");
    expect(userTexts(mock)[1]).not.toContain("syn:steel-kg");
  });

  it("그 카테고리에 활동 기반 후보가 없으면 지출 기반으로 넘어간다", async () => {
    const { mock, pipeline } = harness();
    mock.enqueueText(classification(2)).enqueueText(recommendation(["syn:spend-storage"]));
    const result = await runPipeline([row({ itemName: "스토리지 장비", unit: "kg" })], pipeline);
    expect(result.rows[0]?.decision).toMatchObject({ status: "recommended", category: 2, basis: { used: "spend", fellBack: true, fallbackReason: "no_activity_candidates" } });
  });

  it("LLM 이 활동 기반 후보 중 맞는 것이 없다고 하면 지출 기반으로 한 번 더 묻는다", async () => {
    const { mock, pipeline } = harness();
    mock.enqueueText(classification(1)).enqueueText(recommendation([])).enqueueText(recommendation(["syn:spend-legal"]));
    const result = await runPipeline([row({ itemName: "특수 합금", unit: "kg" })], pipeline);
    expect(mock.calls).toHaveLength(3);
    expect(result.rows[0]?.decision).toMatchObject({ status: "recommended", basis: { used: "spend", fellBack: true, fallbackReason: "llm_found_none_suitable" } });
  });

  it("지출 기반 후보도 없으면 no_candidates 다 (빈 추천이 아니다)", async () => {
    const { mock, pipeline } = harness();
    mock.enqueueText(classification(6));
    const result = await runPipeline([row({ itemName: "출장 일비", unit: "일" })], pipeline);
    expect(mock.calls).toHaveLength(1);
    expect(result.rows[0]?.decision).toMatchObject({ status: "no_candidates", category: 6, physical: "unit_mismatch" });
  });

  it("지출 기반에서도 맞는 것이 없다고 하면 no_suitable_factor 다", async () => {
    const { mock, pipeline } = harness();
    mock.enqueueText(classification(1)).enqueueText(recommendation([]));
    const result = await runPipeline([row({ itemName: "상품권", unit: "매" })], pipeline);
    expect(result.rows[0]?.decision).toMatchObject({ status: "no_suitable_factor", physical: "unit_mismatch" });
  });

  it("운송 행은 톤·km 계수를 후보로 받는다", async () => {
    const { mock, pipeline } = harness();
    mock.enqueueText(classification(4)).enqueueText(recommendation(["syn:truck"]));
    const transport = { origin: "부산", destination: "평택", mode: "트럭", weightKg: "12000", distanceKm: "350" };
    const result = await runPipeline([row({ itemName: "원자재 운송", quantity: null, unit: null, transport })], pipeline);
    expect(result.rows[0]?.decision).toMatchObject({ status: "recommended", basis: { used: "distance", fellBack: false } });
    expect(result.rows[0]?.calc).toMatchObject({ status: "computed", emissionsKgCo2e: "420", activityAmount: "4200" });
  });
});

describe("실패는 빈 결과가 아니라 상태다", () => {
  it("분류가 AI 적용 범위 밖이면 unsupported_category 이고 추천을 부르지 않는다", async () => {
    const { mock, pipeline } = harness();
    mock.enqueueText(classification("other"));
    const result = await runPipeline([row({ itemName: "폐기물 처리" })], pipeline);
    expect(mock.calls).toHaveLength(1);
    expect(result.rows[0]?.decision.status).toBe("unsupported_category");
  });

  it("공급사 오류는 classification_failed 로 남는다", async () => {
    const { mock, pipeline } = harness();
    const result = await runPipeline([row({ itemName: "합성 강판" })], pipeline);
    expect(mock.calls).toHaveLength(1);
    expect(result.rows[0]?.decision).toMatchObject({ status: "classification_failed", llm: { kind: "provider_error" } });
  });

  it("추천 단계의 거부·오류는 recommendation_failed 로 남는다", async () => {
    const { mock, pipeline } = harness();
    mock.enqueueText(classification(1)).enqueue({ kind: "refusal", text: "거부 (synthetic)", category: null, usage: { inputTokens: 10, outputTokens: 1, cacheReadTokens: 0, cacheWriteTokens: 0 } });
    const result = await runPipeline([row({ itemName: "합성 강판" })], pipeline);
    expect(result.rows[0]?.decision).toMatchObject({ status: "recommendation_failed", llm: { kind: "refusal" } });
  });

  it("분류 응답이 스키마와 다르면 classification_failed 다", async () => {
    const { mock, pipeline } = harness();
    for (let i = 0; i < 3; i += 1) mock.enqueueText(classification(5));
    const result = await runPipeline([row({ itemName: "합성 강판" })], pipeline);
    expect(result.rows[0]?.decision).toMatchObject({ status: "classification_failed", llm: { kind: "schema_mismatch" } });
  });

  it("rowId 가 겹치면 예외다", async () => {
    const { pipeline } = harness();
    await expect(runPipeline([row({ itemName: "a" }), row({ itemName: "b" })], pipeline)).rejects.toThrow(/rowId/);
  });
});

describe("숫자는 LLM 에 넘기지 않는다", () => {
  it("프롬프트에 수량, 금액, 무게, 거리, 계수 값이 들어가지 않는다", async () => {
    const { mock, pipeline } = harness();
    mock.enqueueText(classification(4)).enqueueText(recommendation(["syn:truck"]));
    const transport = { origin: "부산", destination: "평택", mode: "트럭", weightKg: "98765", distanceKm: "43210" };
    await runPipeline([row({ itemName: "원자재 운송", quantity: "13579", amount: "24680000", unit: null, transport })], pipeline);
    const sent = mock.calls.map((call) => JSON.stringify(call)).join("\n");
    for (const number of ["98765", "43210", "13579", "24680000", "0.1"]) expect(sent).not.toContain(number);
    expect(userTexts(mock)[0]).toContain("원자재 운송");
    expect(userTexts(mock)[1]).toContain('"id":"syn:truck"');
  });

  it("추린 후보 수만큼만 프롬프트에 넣는다", async () => {
    const { mock, pipeline } = harness({ shortlistSize: 1 });
    mock.enqueueText(classification(1, ["legal"])).enqueueText(recommendation(["syn:spend-legal"]));
    await runPipeline([row({ itemName: "법률 자문", unit: "식" })], pipeline);
    const candidates = JSON.parse((userTexts(mock)[1] as string).split("CANDIDATES_JSON: ")[1] as string) as { id: string }[];
    expect(candidates.map((candidate) => candidate.id)).toEqual(["syn:spend-legal"]);
  });
});
