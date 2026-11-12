import { describe, expect, it } from "vitest";
import { sameNumbers } from "../src/translate.js";
import { assembleTable, chunkUnits, planUnits, translateChunk, translateRequest, type Chunk } from "../src/index.js";
import { TAGS, estimate, fixtureDocument, glossary, harness, settings } from "./helpers.js";

const document = fixtureDocument();
const table = document.tables.find((candidate) => candidate.id === "p1-t1")!;

function oneChunk(): Chunk {
  const plan = planUnits(document, settings);
  return chunkUnits(plan.units, { budgetTokens: 100_000, estimate, sections: document.sections, fingerprint: "fp" })[0]!;
}

const reply = (items: { id: string; text: string }[]): string => JSON.stringify({ items });

describe("번역 작업", () => {
  it("숫자 셀 원본 유지: LLM 이 숫자 셀의 값을 돌려줘도 원본을 쓴다", () => {
    const translations: Record<string, string> = {
      "p1-t1:r0c0": "Energy management",
      "p1-t1:r0c2": "2024",
      "p1-t1:r0c3": "2025",
      "p1-t1:r1c0": "Worksite energy use",
      "p1-t1:r2c0": "Renewable energy conversion rate",
      // 아래는 조각에 없던 id 다. 숫자 셀, 단위 셀, 기호 셀, 빈 셀을 바꾸려는 출력.
      "p1-t1:r1c2": "9,999",
      "p1-t1:r1c3": "1567.8",
      "p1-t1:r2c2": "31",
      "p1-t1:r1c1": "gigawatt-hours",
      "p1-t1:r2c3": "n/a",
      "p1-t1:r0c1": "Unit",
    };
    expect(assembleTable(table, translations)).toEqual([
      ["Energy management", "", "2024", "2025"],
      ["Worksite energy use", "GWh", "1,234", "1,567.8"],
      ["Renewable energy conversion rate", "%", "31.0", "-"],
    ]);
  });

  it("번역 대상 셀의 번역이 없으면 원문으로 메우지 않고 오류로 낸다", () => {
    expect(() => assembleTable(table, { "p1-t1:r0c0": "Energy management" })).toThrow(/번역이 없다/);
  });

  it("요청에는 조각의 글자와 용어집만 들어가고 숫자 셀은 들어가지 않는다", () => {
    const chunk = oneChunk();
    const request = translateRequest(chunk, { route: "mock", tags: TAGS, document, glossary });
    expect(request.prompt).toBe("docpipe/translate@1");
    expect(request.vars!.glossary).toContain("온실가스 배출량 => GHG emissions");
    const items = JSON.parse(request.vars!.items!) as { id: string; text: string }[];
    expect(items).toEqual(chunk.items);
    expect(request.vars!.items).not.toContain("1,234");
    expect(request.vars!.location).toContain("추정 제목");
  });

  it("빠진 id 가 있으면 성공으로 치지 않고 incomplete_output 으로 돌려준다", async () => {
    const { client, mock } = harness();
    const chunk = oneChunk();
    mock.enqueueText(reply(chunk.items.slice(1).map((item) => ({ id: item.id, text: "x" }))));
    const result = await translateChunk(chunk, { client, route: "mock", tags: TAGS, document, glossary });
    expect(result).toMatchObject({ ok: false, error: { kind: "incomplete_output", retryable: true } });
    expect(!result.ok && result.error.message).toContain(chunk.items[0]!.id);
  });

  it("빈 번역과 겹친 id 도 실패다", async () => {
    const { client, mock } = harness();
    const chunk = oneChunk();
    const context = { client, route: "mock", tags: TAGS, document, glossary };
    mock.enqueueText(reply(chunk.items.map((item, index) => ({ id: item.id, text: index === 0 ? "  " : item.text }))));
    expect(await translateChunk(chunk, context)).toMatchObject({ ok: false, error: { kind: "incomplete_output" } });
    mock.enqueueText(reply([...chunk.items, chunk.items[0]!].map((item) => ({ id: item.id, text: "x" }))));
    expect(await translateChunk(chunk, context)).toMatchObject({ ok: false, error: { kind: "duplicate_ids", retryable: true } });
  });

  it("엄격 설정(옛 규칙): 한글과 숫자가 섞인 표 셀의 숫자가 바뀌면 조각은 실패다. 숫자가 그대로면 통과한다", async () => {
    const { client, mock } = harness();
    const chunk = oneChunk();
    const context = { client, route: "mock", tags: TAGS, document, glossary, tableNumbers: "strict" as const };
    const answer = (yearCell: string) => reply(chunk.items.map((item) => ({ id: item.id, text: item.id === "p1-t1:r0c2" ? yearCell : item.text.replace(/[가-힣]+/g, "x") })));
    mock.enqueueText(answer("FY2023"));
    const changed = await translateChunk(chunk, context);
    expect(changed).toMatchObject({ ok: false, error: { kind: "table_number_changed", retryable: true } });
    expect(!changed.ok && changed.error.message).toContain("p1-t1:r0c2");
    mock.enqueueText(answer("FY2024"));
    expect(await translateChunk(chunk, context)).toMatchObject({ ok: true });
    expect(sameNumbers("용수 재이용1) 107,967", "Water reuse1) 107967")).toBe(true);
    expect(sameNumbers("59,498명", "59,489 employees")).toBe(false);
    expect(sameNumbers("2곳", "2 sites in 2025")).toBe(false);
  });

  it("기본 규칙: 표 셀의 숫자가 바뀌어도 조각은 성공이고 경고만 남긴다. 숫자가 그대로면 경고가 없다", async () => {
    const { client, mock } = harness();
    const chunk = oneChunk();
    const context = { client, route: "mock", tags: TAGS, document, glossary };
    const answer = (yearCell: string) => reply(chunk.items.map((item) => ({ id: item.id, text: item.id === "p1-t1:r0c2" ? yearCell : item.text.replace(/[가-힣]+/g, "x") })));
    mock.enqueueText(answer("FY2023"));
    const changed = await translateChunk(chunk, context);
    expect(changed).toMatchObject({ ok: true, output: { translations: { "p1-t1:r0c2": "FY2023" } } });
    expect(changed.warnings).toContain("table_number_change_suspected");
    mock.enqueueText(answer("FY2024"));
    expect((await translateChunk(chunk, context)).warnings).not.toContain("table_number_change_suspected");
  });

  it("조각에 없던 id 는 경고와 함께 기록만 하고 조각은 성공이다", async () => {
    const { client, mock } = harness();
    const chunk = oneChunk();
    mock.enqueueText(reply([...chunk.items.map((item) => ({ id: item.id, text: item.text })), { id: "p1-t1:r1c2", text: "9,999" }]));
    const result = await translateChunk(chunk, { client, route: "mock", tags: TAGS, document, glossary });
    expect(result).toMatchObject({ ok: true, output: { unexpectedIds: ["p1-t1:r1c2"] } });
    expect(result.warnings).toContain("unexpected_ids");
  });

  it.each([
    [{ kind: "refusal", text: "거부", category: null, usage: { inputTokens: 1, outputTokens: 1, cacheReadTokens: 0, cacheWriteTokens: 0 } }, "refusal", false],
    [{ kind: "truncated", text: "{", usage: { inputTokens: 1, outputTokens: 1, cacheReadTokens: 0, cacheWriteTokens: 0 } }, "truncated", false],
    [{ kind: "error", status: 503, retryable: true, message: "과부하" }, "provider_error", true],
    [{ kind: "error", status: 400, retryable: false, message: "잘못된 요청" }, "provider_error", false],
  ] as const)("LLM 결과 %#: 실패 종류와 재시도 가능 여부를 그대로 옮긴다", async (scripted, kind, retryable) => {
    const { client, mock } = harness();
    mock.enqueue(scripted);
    const result = await translateChunk(oneChunk(), { client, route: "mock", tags: TAGS, document, glossary });
    expect(result).toMatchObject({ ok: false, error: { kind, retryable } });
  });

  it("JSON 이 아닌 응답은 호출 계층의 재시도 뒤 schema_mismatch 로 남는다", async () => {
    const { client, mock } = harness();
    for (let i = 0; i < 3; i++) mock.enqueueText("번역했습니다");
    const result = await translateChunk(oneChunk(), { client, route: "mock", tags: TAGS, document, glossary });
    expect(result).toMatchObject({ ok: false, error: { kind: "schema_mismatch", retryable: false } });
    expect(mock.calls).toHaveLength(3);
  });
});
