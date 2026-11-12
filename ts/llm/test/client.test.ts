import { describe, expect, it } from "vitest";
import { z } from "zod";
import { TAGS, USAGE_SYNTHETIC, configWith, harness } from "./helpers.js";

const Answer = z.object({ category: z.string(), confidence: z.number() });
const base = { prompt: "t/plain@1", vars: { question: "분류해 줘" }, route: "mock", tags: TAGS };
const good = JSON.stringify({ category: "cat-1", confidence: 0.9 });
const bad = JSON.stringify({ category: "cat-1" });

describe("LlmClient.call", () => {
  it("성공: 스키마로 검증한 값과 비용을 돌려준다", async () => {
    const { client, mock, log } = harness();
    mock.enqueueText(good, USAGE_SYNTHETIC);

    const result = await client.call({ ...base, schema: Answer });

    expect(result.kind).toBe("success");
    if (result.kind !== "success") return;
    expect(result.data).toEqual({ category: "cat-1", confidence: 0.9 });
    expect(result.meta.totalCostUsd).toBe(0.0001);
    expect(result.meta.attempts.map((attempt) => attempt.kind)).toEqual(["success"]);
    expect(mock.calls[0]?.jsonSchema?.schema).toMatchObject({ type: "object", required: ["category", "confidence"] });
    expect(log.records).toHaveLength(1);
  });

  it("스키마 없는 호출은 글자를 그대로 돌려준다", async () => {
    const { client, mock } = harness();
    mock.enqueueText("안녕하세요");
    const result = await client.call(base);
    expect(result).toMatchObject({ kind: "success", data: "안녕하세요" });
    expect(mock.calls[0]?.jsonSchema).toBeUndefined();
  });

  it("스키마 불일치 뒤 재시도에서 성공한다", async () => {
    const { client, mock, log } = harness();
    mock.enqueueText(bad, USAGE_SYNTHETIC).enqueueText("JSON 아님", USAGE_SYNTHETIC).enqueueText(good, USAGE_SYNTHETIC);

    const result = await client.call({ ...base, schema: Answer });

    expect(result.kind).toBe("success");
    expect(result.meta.attempts.map((attempt) => attempt.kind)).toEqual(["schema_mismatch", "schema_mismatch", "success"]);
    // 실패한 시도의 토큰도 과금되므로 비용에 들어간다.
    expect(result.meta.totalCostUsd).toBe(0.0003);
    expect(log.records.map((record) => record.resultKind)).toEqual(["schema_mismatch", "schema_mismatch", "success"]);
  });

  it("재시도를 다 쓰면 schema_mismatch 로 끝나고 빈 성공을 만들지 않는다", async () => {
    const { client, mock } = harness();
    mock.enqueueText(bad).enqueueText(bad);

    const result = await client.call({ ...base, schema: Answer, maxSchemaRetries: 1 });

    expect(result.kind).toBe("schema_mismatch");
    if (result.kind !== "schema_mismatch") return;
    expect(result.issues[0]?.path).toBe("confidence");
    expect(result.rawText).toBe(bad);
    expect(mock.calls).toHaveLength(2);
    expect("data" in result).toBe(false);
  });

  it("스키마 불일치는 대체 공급사로 넘기지 않는다", async () => {
    const { client, mock, mockB } = harness();
    mock.enqueueText(bad);
    mockB.enqueueText(good);

    const result = await client.call({ ...base, route: "mock-with-fallback", schema: Answer, maxSchemaRetries: 0 });

    expect(result.kind).toBe("schema_mismatch");
    expect(mockB.calls).toHaveLength(0);
  });

  it("거부는 refusal 로 돌려주고 재시도하지 않는다", async () => {
    const { client, mock, mockB } = harness();
    mock.enqueue({ kind: "refusal", text: "답할 수 없다", category: "synthetic", usage: USAGE_SYNTHETIC });

    const result = await client.call({ ...base, route: "mock-with-fallback", schema: Answer });

    expect(result).toMatchObject({ kind: "refusal", text: "답할 수 없다", category: "synthetic" });
    expect(mock.calls).toHaveLength(1);
    expect(mockB.calls).toHaveLength(0);
  });

  it("출력 잘림은 truncated 로 돌려주고 잘린 글자를 스키마에 넣지 않는다", async () => {
    const { client, mock } = harness();
    mock.enqueue({ kind: "truncated", text: '{"category":"ca', usage: USAGE_SYNTHETIC });

    const result = await client.call({ ...base, schema: Answer });

    expect(result).toMatchObject({ kind: "truncated", partialText: '{"category":"ca' });
    expect(mock.calls).toHaveLength(1);
    expect(result.meta.totalCostUsd).toBe(0.0001);
  });

  it("시간 초과는 timeout 으로 돌려준다", async () => {
    const { client, mock, log } = harness();
    mock.enqueue({ kind: "completed", text: good, usage: USAGE_SYNTHETIC }, { delayMs: 500 });

    const result = await client.call({ ...base, schema: Answer, timeoutMs: 20 });

    expect(result.kind).toBe("timeout");
    expect(result.meta.attempts[0]).toMatchObject({ kind: "timeout", usage: null, costUsd: null });
    expect(log.records[0]).toMatchObject({ resultKind: "timeout", inputTokens: null, costUsd: null });
  });

  it("공급사 오류는 provider_error 로 돌려준다 (대체할 모델이 없을 때)", async () => {
    const { client, mock } = harness();
    mock.enqueue({ kind: "error", status: 503, retryable: true, message: "과부하" });

    const result = await client.call(base);

    expect(result).toMatchObject({ kind: "provider_error", status: 503, retryable: true, message: "과부하" });
  });

  it("심어 둔 응답이 없으면 성공을 지어내지 않는다", async () => {
    const { client } = harness();
    expect((await client.call(base)).kind).toBe("provider_error");
  });

  it("대체 호출: 주 공급사 오류면 다음 공급사를 부른다", async () => {
    const { client, mock, mockB, log } = harness();
    mock.enqueue({ kind: "error", status: 500, retryable: true, message: "서버 오류" });
    mockB.enqueueText(good, USAGE_SYNTHETIC);

    const result = await client.call({ ...base, route: "mock-with-fallback", schema: Answer });

    expect(result.kind).toBe("success");
    expect(result.meta.attempts.map((attempt) => [attempt.provider, attempt.kind])).toEqual([
      ["mock", "provider_error"],
      ["mock-b", "success"],
    ]);
    // 대체 모델(mock-b-synthetic)의 단가로 계산한다: 60×2 + 20×4 = 200 → 0.0002.
    expect(result.meta.totalCostUsd).toBe(0.0002);
    expect(log.records.map((record) => record.model)).toEqual(["mock-synthetic", "mock-b-synthetic"]);
  });

  it("대체 호출: 재시도 불가 오류(잘못된 요청, 인증 실패)는 대체하지 않고 그대로 돌려준다", async () => {
    const { client, mock, mockB } = harness();
    mock.enqueue({ kind: "error", status: 400, retryable: false, message: "잘못된 요청" });
    mockB.enqueueText(good, USAGE_SYNTHETIC);

    const result = await client.call({ ...base, route: "mock-with-fallback", schema: Answer });

    expect(result).toMatchObject({ kind: "provider_error", status: 400, retryable: false });
    expect(mockB.calls).toHaveLength(0);
  });

  it("대체 호출: 주 공급사 시간 초과면 다음 공급사를 부르고, 둘 다 실패하면 마지막 실패를 돌려준다", async () => {
    const { client, mock, mockB } = harness();
    mock.enqueue({ kind: "completed", text: good, usage: USAGE_SYNTHETIC }, { delayMs: 500 });
    mockB.enqueue({ kind: "error", status: 429, retryable: true, message: "한도" });

    const result = await client.call({ ...base, route: "mock-with-fallback", timeoutMs: 20 });

    expect(result).toMatchObject({ kind: "provider_error", status: 429 });
    expect(result.meta.attempts.map((attempt) => attempt.kind)).toEqual(["timeout", "provider_error"]);
  });
});

describe("비용 상한", () => {
  it("누적 비용이 상한에 닿으면 공급사를 부르기 전에 거부한다", async () => {
    const { client, mock, log, budget } = harness({ limits: [{ scope: { tenant: TAGS.tenant }, limitUsd: 0.0002 }] });
    mock.enqueueText("1", USAGE_SYNTHETIC).enqueueText("2", USAGE_SYNTHETIC).enqueueText("3", USAGE_SYNTHETIC);

    expect((await client.call(base)).kind).toBe("success");
    expect((await client.call(base)).kind).toBe("success");
    const third = await client.call(base);

    expect(third).toMatchObject({ kind: "budget_exceeded", limitUsd: 0.0002, spentUsd: 0.0002, scope: { tenant: TAGS.tenant } });
    expect(mock.calls).toHaveLength(2);
    expect(mock.pending).toBe(1);
    expect(budget.spent({ tenant: TAGS.tenant })).toBe(0.0002);
    expect(log.records.at(-1)).toMatchObject({ resultKind: "budget_exceeded", provider: null, costUsd: null });
  });

  it("다른 꼬리표의 호출은 막지 않는다", async () => {
    const { client, mock } = harness({ limits: [{ scope: { tenant: TAGS.tenant, module: "scope3" }, limitUsd: 0.0001 }] });
    mock.enqueueText("1", USAGE_SYNTHETIC).enqueueText("2", USAGE_SYNTHETIC);

    expect((await client.call(base)).kind).toBe("success");
    expect((await client.call(base)).kind).toBe("budget_exceeded");
    expect((await client.call({ ...base, tags: { tenant: TAGS.tenant, module: "translate" } })).kind).toBe("success");
  });

  it("스키마 재시도 도중에도 상한을 확인한다", async () => {
    const { client, mock } = harness({ limits: [{ scope: {}, limitUsd: 0.0001 }] });
    mock.enqueueText(bad, USAGE_SYNTHETIC).enqueueText(good, USAGE_SYNTHETIC);

    const result = await client.call({ ...base, schema: Answer });

    expect(result.kind).toBe("budget_exceeded");
    expect(mock.calls).toHaveLength(1);
  });
});

describe("출력 상한과 추론 여유분", () => {
  it("여유분이 없는 모델에는 요청의 상한이 그대로 나간다", async () => {
    const { client, mock } = harness();
    mock.enqueueText("ok");
    await client.call({ ...base, maxOutputTokens: 400 });
    expect(mock.calls[0]?.maxOutputTokens).toBe(400);
    expect(client.dryRun({ ...base, maxOutputTokens: 400 }).estimate.maxOutputTokens).toBe(400);
  });

  it("여유분이 있는 모델에는 요청의 상한에 여유분을 더해 보내고, dry-run 의 비용 상한도 그 값으로 센다", async () => {
    const { client, mock, log } = harness({ config: configWith({ reasoningOutputReserveTokens: 1500 }), storeBodies: true });
    mock.enqueueText("ok");
    await client.call({ ...base, maxOutputTokens: 400 });
    expect(mock.calls[0]?.maxOutputTokens).toBe(1900);
    expect((log.records[0]?.body?.request as { maxOutputTokens: number }).maxOutputTokens).toBe(1900);
    const plain = harness().client.dryRun({ ...base, maxOutputTokens: 400 });
    const reserved = client.dryRun({ ...base, maxOutputTokens: 400 });
    expect(reserved.estimate.maxOutputTokens).toBe(1900);
    // 모의 단가: 출력 2 USD/100만 토큰. 여유분 1500 토큰만큼 상한이 오른다.
    expect(reserved.estimate.costUpperBoundUsd - plain.estimate.costUpperBoundUsd).toBeCloseTo((1500 * 2) / 1e6, 12);
  });

  it("상한을 주지 않은 호출에도 여유분이 붙고, 합이 모델 상한을 넘으면 모델 상한에서 멈춘다", async () => {
    const { client } = harness({ config: configWith({ reasoningOutputReserveTokens: 1500 }) });
    // 모의 모델의 출력 상한은 4000 이다. 기본 답 상한 min(4096, 4000) + 1500 → 4000.
    expect(client.dryRun(base).estimate.maxOutputTokens).toBe(4000);
    expect(client.dryRun({ ...base, maxOutputTokens: 3000 }).estimate.maxOutputTokens).toBe(4000);
    // 답의 상한 자체가 모델 상한을 넘으면 여전히 설정 오류다.
    expect(() => client.dryRun({ ...base, maxOutputTokens: 4001 })).toThrow("상한");
  });
});

describe("프롬프트 캐싱 경고", () => {
  it("캐시 대상이 모델의 최소 길이에 못 미치면 호출 전에 경고한다", async () => {
    const { client, mock } = harness();
    mock.enqueueText("ok", USAGE_SYNTHETIC);

    const dry = client.dryRun({ ...base, prompt: "t/cached-short@1" });
    expect(dry.warnings.map((warning) => warning.code)).toEqual(["cache_below_minimum"]);
    expect(mock.calls).toHaveLength(0);

    const result = await client.call({ ...base, prompt: "t/cached-short@1" });
    expect(result.meta.warnings.map((warning) => warning.code)).toContain("cache_below_minimum");
    expect(mock.calls[0]?.system).toEqual([{ text: "짧은 지침.", cache: true }]);
  });

  it("최소 길이를 넘으면 경고하지 않고, 캐시 표시가 없으면 검사하지 않는다", () => {
    const { client } = harness();
    expect(client.dryRun({ ...base, prompt: "t/cached-long@1" }).warnings).toEqual([]);
    expect(client.dryRun(base).warnings).toEqual([]);
  });

  it("캐시를 표시했는데 응답의 캐시 토큰이 0 이면 알린다", async () => {
    const { client, mock } = harness();
    mock.enqueueText("ok", USAGE_SYNTHETIC);
    const result = await client.call({ ...base, prompt: "t/cached-long@1" });
    expect(result.meta.warnings.map((warning) => warning.code)).toEqual(["cache_not_applied"]);
  });

  it("공급사가 캐시 토큰을 보고하지 않으면 0 과 구분해 알린다", async () => {
    const { client, mock } = harness();
    mock.enqueueText("ok", { ...USAGE_SYNTHETIC, cacheReadTokens: null, cacheWriteTokens: null });
    const result = await client.call({ ...base, prompt: "t/cached-long@1" });
    expect(result.meta.warnings.map((warning) => warning.code)).toEqual(["usage_unreported"]);
  });
});

describe("호출 기록", () => {
  it("기본은 본문을 남기지 않고 해시만 남긴다", async () => {
    const { client, mock, log } = harness();
    mock.enqueueText("비밀 응답", { inputTokens: 60, outputTokens: 20, cacheReadTokens: 7, cacheWriteTokens: 3 });

    await client.call({ ...base, vars: { question: "비밀 질문" } });

    const record = log.records[0];
    expect(record).toMatchObject({
      provider: "mock",
      model: "mock-synthetic",
      apiModelId: "mock-synthetic",
      promptId: "t/plain@1",
      promptVersion: 1,
      inputTokens: 60,
      outputTokens: 20,
      cacheReadTokens: 7,
      cacheWriteTokens: 3,
      resultKind: "success",
      tenant: TAGS.tenant,
      module: TAGS.module,
    });
    expect(record?.requestHash).toMatch(/^[0-9a-f]{64}$/);
    expect(record?.templateHash).toMatch(/^[0-9a-f]{64}$/);
    expect(record && "body" in record).toBe(false);
    expect(JSON.stringify(record)).not.toContain("비밀");
  });

  it("storeBodies 를 켜면 본문을 남긴다", async () => {
    const { client, mock, log } = harness({ storeBodies: true });
    mock.enqueueText("비밀 응답");
    await client.call({ ...base, vars: { question: "비밀 질문" } });
    expect(log.records[0]?.body?.responseText).toBe("비밀 응답");
    expect(JSON.stringify(log.records[0]?.body?.request)).toContain("비밀 질문");
  });

  it("같은 입력은 같은 요청 해시, 다른 입력은 다른 해시다", async () => {
    const { client, mock, log } = harness();
    mock.enqueueText("a").enqueueText("b").enqueueText("c");
    await client.call(base);
    await client.call(base);
    await client.call({ ...base, vars: { question: "다른 질문" } });
    const [first, second, third] = log.records.map((record) => record.requestHash);
    expect(first).toBe(second);
    expect(third).not.toBe(first);
  });
});

describe("설정 오류는 결과가 아니라 예외다", () => {
  it("없는 경로, 등록 안 된 공급사, 모델 상한을 넘는 출력 토큰", async () => {
    const { client } = harness();
    await expect(client.call({ ...base, route: "nope" })).rejects.toThrow("경로 nope");
    await expect(client.call({ ...base, route: "light" })).rejects.toThrow("어댑터가 등록되지 않았다");
    await expect(client.call({ ...base, maxOutputTokens: 4001 })).rejects.toThrow("상한 4000");
    await expect(client.call({ ...base, maxSchemaRetries: -1 })).rejects.toThrow("0 이상의 정수");
    await expect(client.call({ ...base, maxSchemaRetries: 1.5 })).rejects.toThrow("0 이상의 정수");
  });
});
