import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { MockProvider, ProviderRequest } from "llm";
import {
  MemoryChunkStore,
  loadUnitTable,
  parseDocument,
  planTranslate,
  planUnits,
  runTranslate,
  translatableCells,
  type ParsedDocument,
  type TranslateOutput,
  type TranslateRules,
} from "../src/index.js";
import { main } from "../src/cli.js";
import { TAGS, estimator, glossary, harness, itemsIn, settings, tempDir } from "./helpers.js";

const units = loadUnitTable();
const NEW_RULES: TranslateRules = { tableNumbers: "flag", units };
const OLD_RULES: TranslateRules = { tableNumbers: "strict", units: null };

/** synthetic 문서. 단위 셀, 한글과 숫자가 섞인 셀, 단위 셀뿐인 표가 들어 있다. 어느 보고서의 수치도 아니다. */
function unitDocument(): ParsedDocument {
  const bbox = [0, 0, 10, 10];
  const table = (id: string, cells: string[][]) => ({ id, page: 1, bbox, n_rows: cells.length, n_cols: cells[0]!.length, cells, header: { rows: [0], cols: [0], basis: "rows_before_first_numeric_row" }, method: "lines", status: "ok", notes: [], footnote_marks: [] });
  return parseDocument({
    schema_version: "0.1",
    source: { file_name: "synthetic_units.pdf", sha256: "2".repeat(64), page_count: 1 },
    parser: { name: "docparse", version: "0.1.0", table_strategy: "auto", llm_used: false, ocr_used: false },
    pages: [{ page: 1, width: 595, height: 842, text_layer: "present", char_count: 100, image_coverage: 0, columns: 1, notes: [] }],
    sections: [{ id: "s1", title: "가상 성과", level: 1, page_start: 1, page_end: 1, basis: "page_top_largest_font" }],
    blocks: [
      { id: "b1", kind: "paragraph", page: 1, bbox, order: 1, section_id: "s1", text: "가상 회사의 1차 협력회사 점검 결과입니다." },
      { id: "b2", kind: "table", page: 1, bbox, order: 2, section_id: "s1", table_id: "t1" },
      { id: "b3", kind: "table", page: 1, bbox, order: 3, section_id: "s1", table_id: "t2" },
    ],
    tables: [
      table("t1", [
        ["구분", "단위", "2024년", "’25년 목표"],
        ["매출액", "억 원", "1,234", "1,567"],
        ["폐기물 발생량", "천 톤", "12", "13"],
        ["교육 시간", "시간", "10", "11"],
        ["1차 협력회사 점검", "개사", "71", "80"],
        ["재생 원료 3) 사용률4)", "%", "31.0", "35.0"],
      ]),
      table("t2", [
        ["억 원", "1,234"],
        ["톤 CO₂e/억 원", "0.5"],
      ]),
    ],
  });
}

const document = unitDocument();
const base = { document, glossary, settings, estimator, route: "mock", tags: TAGS, budgetTokens: 100_000, concurrency: 1 };
const usage = { inputTokens: 10, outputTokens: 10, cacheReadTokens: 0, cacheWriteTokens: 0 };

/** 가상 번역기. 정해 둔 글자를 돌려주고 나머지는 한글을 x 로 바꾼다(숫자는 그대로). 단위 셀 id 를 받으면 숫자를 품은 표기로 답한다. */
const ANSWERS: Record<string, string> = {
  b1: "Results of first-tier supplier inspections.",
  "t1:r0c3": "2025 target",
  "t1:r4c0": "First-tier supplier inspections",
  "t1:r5c0": "Recycled material usage rate4)",
  "t1:r1c1": "KRW 100 million (model)",
  "t1:r2c1": "1,000 tons (model)",
  "t2:r0c0": "KRW 100 million (model)",
  "t2:r1c0": "tons CO₂e/KRW 100 million (model)",
};
const translator = (request: ProviderRequest) => {
  const items = itemsIn(request).map((item) => ({ id: item.id, text: ANSWERS[item.id] ?? item.text.replace(/[가-힣]+/g, "x") }));
  return { kind: "completed" as const, text: JSON.stringify({ items }), usage };
};
const seed = (mock: MockProvider, count = 6): void => {
  for (let i = 0; i < count; i++) mock.enqueue(translator);
};
const sentIds = (mock: MockProvider): string[] => mock.calls.flatMap((request) => itemsIn(request).map((item) => item.id));

describe("단위 셀: 고정 번역하고 모델에 보내지 않는다", () => {
  it("고정 번역 표에 있는 단위 셀은 번역 대상에서 빠지고, 표에 없는 단위 셀과 일반 셀은 남는다", () => {
    const table = document.tables[0]!;
    const sent = translatableCells(table, units).map((item) => item.text);
    expect(sent).not.toContain("억 원");
    expect(sent).not.toContain("천 톤");
    expect(sent).not.toContain("개사");
    expect(sent).toEqual(["구분", "단위", "2024년", "’25년 목표", "매출액", "폐기물 발생량", "교육 시간", "시간", "1차 협력회사 점검", "재생 원료 3) 사용률4)"]);
    // 표를 주지 않으면 옛 동작이다: 단위 셀도 보낸다.
    expect(translatableCells(table, null).map((item) => item.text)).toEqual(expect.arrayContaining(["억 원", "천 톤", "개사", "시간"]));
    expect(translatableCells(table).length).toBe(sent.length + 3);
  });

  it("단위 셀뿐인 표는 보낼 것이 없어 fixed 로 남고, 옛 동작에서는 보낸다", () => {
    expect(planUnits(document, { ...settings, units }).dispositions.get("b3")).toEqual({ kind: "fixed", reason: "table_with_only_fixed_unit_cells" });
    expect(planUnits(document, settings).dispositions.get("b3")).toEqual({ kind: "send" });
  });

  it("설정의 기본 규칙은 새 규칙이다 (의심 표시, 단위 셀 고정 번역)", () => {
    expect(settings.translate).toMatchObject({ tableNumbers: "flag", unitCells: "fixed" });
    const plan = planTranslate(base);
    expect(plan.rules.tableNumbers).toBe("flag");
    expect(plan.rules.units?.path).toBe(units.path);
    expect(plan.chunks.flatMap((chunk) => chunk.items.map((item) => item.text))).not.toContain("억 원");
  });

  it("모델에 안 보냄: 요청에 단위 셀이 없고 결과에는 표의 글자가 들어가며 셀에 unit_fixed 표시가 남는다", async () => {
    const { client, mock } = harness();
    seed(mock);
    const outcome = await runTranslate({ ...base, rules: NEW_RULES, client, store: new MemoryChunkStore<TranslateOutput>() });
    expect(outcome.run).toMatchObject({ total: 1, done: 1, failed: 0 });
    for (const id of ["t1:r1c1", "t1:r2c1", "t1:r4c1", "t2:r0c0", "t2:r1c0"]) expect(sentIds(mock)).not.toContain(id);
    expect(mock.calls.map((request) => JSON.stringify(request.user)).join("")).not.toContain("억 원");

    const t1 = outcome.result.tables.find((table) => table.id === "t1")!;
    expect(t1.status).toBe("translated");
    expect(t1.cells![1]).toEqual(["x", "KRW 100 million", "1,234", "1,567"]);
    expect(t1.cells![2]).toEqual(["x x", "1,000 tons", "12", "13"]);
    expect(t1.cells![4]![1]).toBe("companies");
    expect(t1.cellFlags.filter((flag) => flag.kind === "unit_fixed")).toEqual([
      { kind: "unit_fixed", id: "t1:r1c1", row: 1, col: 1, source: "억 원", text: "KRW 100 million" },
      { kind: "unit_fixed", id: "t1:r2c1", row: 2, col: 1, source: "천 톤", text: "1,000 tons" },
      { kind: "unit_fixed", id: "t1:r4c1", row: 4, col: 1, source: "개사", text: "companies" },
    ]);
    // 고정 번역한 셀은 숫자를 품은 영어 표기여도 숫자 의심 표시가 붙지 않는다.
    expect(t1.cellFlags.some((flag) => flag.kind === "number_change_suspected" && flag.col === 1)).toBe(false);

    const t2 = outcome.result.tables.find((table) => table.id === "t2")!;
    expect(t2).toMatchObject({ status: "fixed", chunkId: null, reason: "table_with_only_fixed_unit_cells", cells: [["KRW 100 million", "1,234"], ["tons CO₂e/KRW 100 million", "0.5"]] });
    expect(outcome.result.tableCells).toMatchObject({ unitFixed: 5, unitWithoutFixed: 1, unitTable: { synthetic: true } });
    expect(outcome.coverage.ok).toBe(true);
  });

  it("표에 없는 단위 셀은 모델에 보내고 unit_without_fixed_translation 표시를 남긴다. 실패가 아니다", async () => {
    const { client, mock } = harness();
    seed(mock);
    const outcome = await runTranslate({ ...base, rules: NEW_RULES, client, store: new MemoryChunkStore<TranslateOutput>() });
    expect(sentIds(mock)).toContain("t1:r3c1");
    expect(outcome.run.failed).toBe(0);
    const t1 = outcome.result.tables.find((table) => table.id === "t1")!;
    expect(t1.cells![3]![1]).toBe("x");
    expect(t1.cellFlags.filter((flag) => flag.kind === "unit_without_fixed_translation")).toEqual([{ kind: "unit_without_fixed_translation", id: "t1:r3c1", row: 3, col: 1, source: "시간", unknown: ["시간"] }]);
  });
});

describe("표 셀의 숫자가 달라졌을 때", () => {
  it("새 규칙: 조각은 성공이고 다시 부르지 않는다. 번역문을 쓰고 셀에 표시와 차이, 등급을 남긴다", async () => {
    const { client, mock } = harness();
    seed(mock);
    const outcome = await runTranslate({ ...base, rules: NEW_RULES, client, store: new MemoryChunkStore<TranslateOutput>() });
    expect(outcome.run).toMatchObject({ total: 1, done: 1, failed: 0, workCalls: 1 });
    expect(mock.calls).toHaveLength(1);
    expect(outcome.run.records[0]).toMatchObject({ status: "done", attempts: 1, warnings: expect.arrayContaining(["table_number_change_suspected"]) });

    const t1 = outcome.result.tables.find((table) => table.id === "t1")!;
    expect(t1.cells![0]![3]).toBe("2025 target");
    expect(t1.cells![5]![0]).toBe("Recycled material usage rate4)");
    expect(t1.cellFlags.filter((flag) => flag.kind === "number_change_suspected")).toEqual([
      { kind: "number_change_suspected", id: "t1:r0c3", row: 0, col: 3, source: "’25년 목표", target: "2025 target", sourceOnly: ["25"], targetOnly: ["2025"], grade: "notation_like", explained: [{ pattern: "year_expansion", source: "25", target: "2025" }] },
      { kind: "number_change_suspected", id: "t1:r4c0", row: 4, col: 0, source: "1차 협력회사 점검", target: "First-tier supplier inspections", sourceOnly: ["1"], targetOnly: [], grade: "notation_like", explained: [{ pattern: "number_word", source: "1", target: null }] },
      { kind: "number_change_suspected", id: "t1:r5c0", row: 5, col: 0, source: "재생 원료 3) 사용률4)", target: "Recycled material usage rate4)", sourceOnly: ["3"], targetOnly: [], grade: "other", explained: [] },
    ]);
    // 합계와 분모: 모델을 거친 표 셀 10개를 전부 견줬고 그중 3개가 의심이다.
    expect(outcome.result.tableCells).toMatchObject({ tableNumbers: "flag", sentCells: 10, numberChecked: 10, numberNotChecked: 0, suspected: 3, suspectedByGrade: { notation_like: 2, other: 1 } });
    // 사람이 볼 목록은 그 밖(other)이 앞에 온다.
    expect(outcome.suspects.map((cell) => [cell.cellId, cell.grade, cell.chunkId])).toEqual([
      ["t1:r5c0", "other", outcome.plan.chunks[0]!.id],
      ["t1:r0c3", "notation_like", outcome.plan.chunks[0]!.id],
      ["t1:r4c0", "notation_like", outcome.plan.chunks[0]!.id],
    ]);
    expect(outcome.suspects[0]).toMatchObject({ tableId: "t1", page: 1, source: "재생 원료 3) 사용률4)", target: "Recycled material usage rate4)", sourceOnly: ["3"], targetOnly: [] });
    // 문단의 사후 숫자 보고는 그대로다.
    expect(outcome.consistency.numberDrifts.map((drift) => drift.id)).toContain("b1");
  });

  it("엄격 설정: 옛 동작이다. 조각은 table_number_changed 로 실패하고 시도 횟수만큼 다시 부른다. 단위 셀도 보낸다", async () => {
    const { client, mock } = harness();
    seed(mock, 9);
    const outcome = await runTranslate({ ...base, rules: OLD_RULES, client, store: new MemoryChunkStore<TranslateOutput>() });
    expect(outcome.run).toMatchObject({ total: 1, done: 0, failed: 1, workCalls: settings.runner.maxAttempts });
    expect(mock.calls).toHaveLength(settings.runner.maxAttempts);
    expect(outcome.run.records[0]).toMatchObject({ status: "failed", error: { kind: "table_number_changed", retryable: true } });
    const message = outcome.run.records[0]!.status === "failed" ? outcome.run.records[0]!.error.message : "";
    for (const id of ["t1:r0c3", "t1:r4c0", "t1:r5c0", "t1:r1c1", "t2:r0c0"]) expect(message).toContain(id);
    expect(sentIds(mock)).toEqual(expect.arrayContaining(["t1:r1c1", "t1:r2c1", "t2:r0c0", "t2:r1c0"]));
    // 실패한 조각의 셀은 견주지 못한 것이다. 「의심 0」 이 아니라 「검사 안 함」 으로 남는다.
    expect(outcome.result.tableCells).toMatchObject({ tableNumbers: "strict", unitTable: null, sentCells: 15, numberChecked: 0, numberNotChecked: 15, suspected: 0, unitFixed: 0 });
    expect(outcome.result.tables.every((table) => table.status === "not_translated" && table.cellFlags.length === 0)).toBe(true);
    expect(outcome.suspects).toEqual([]);
  });

  it("엄격 설정에서도 숫자가 그대로면 통과한다", async () => {
    const { client, mock } = harness();
    mock.enqueue((request: ProviderRequest) => ({ kind: "completed" as const, text: JSON.stringify({ items: itemsIn(request).map((item) => ({ id: item.id, text: item.text.replace(/[가-힣]+/g, "x") })) }), usage }));
    const outcome = await runTranslate({ ...base, rules: OLD_RULES, client, store: new MemoryChunkStore<TranslateOutput>() });
    expect(outcome.run).toMatchObject({ done: 1, failed: 0 });
    expect(outcome.result.tableCells).toMatchObject({ numberChecked: 15, numberNotChecked: 0, suspected: 0 });
  });
});

describe("CLI: 요약의 의심 수와 저장된 응답 다시 읽기", () => {
  function files(): { documentPath: string; callsPath: string } {
    const dir = tempDir();
    const documentPath = join(dir, "document.json");
    writeFileSync(documentPath, JSON.stringify(document));
    // 호출 기록의 가상 사본 (synthetic). 옛 규칙으로 돌린 실행이 남긴 모양: 단위 셀까지 보낸 요청 하나와 그 응답 세 번.
    const items = planTranslate({ ...base, rules: OLD_RULES }).chunks[0]!.items;
    const line = (responseText: string) =>
      JSON.stringify({ resultKind: "success", body: { request: { user: [{ type: "text", text: `문서: synthetic_units.pdf\n위치: 1쪽\n\n[\n${items.map((item) => JSON.stringify(item)).join(",\n")}\n]` }] }, responseText } });
    const answer = (overrides: Record<string, string>) => JSON.stringify({ items: items.map((item) => ({ id: item.id, text: overrides[item.id] ?? ANSWERS[item.id] ?? item.text.replace(/[가-힣]+/g, "x") })) });
    const callsPath = join(dir, "calls.jsonl");
    writeFileSync(callsPath, [answer({}), answer({ "t1:r5c0": "Recycled material 3) usage rate4)" }), answer({})].map(line).join("\n") + "\n");
    return { documentPath, callsPath };
  }
  async function run(argv: string[]) {
    const lines: string[] = [];
    const code = await main(argv, (text) => lines.push(text), () => undefined);
    return { code, summary: JSON.parse(lines.join("\n")) as Record<string, any> };
  }

  it("translate --provider mock: 요약에 견준 셀 수와 의심 수가 같이 나온다 (의심 0 은 검사한 결과다)", async () => {
    const { documentPath } = files();
    const out = tempDir();
    const { code, summary } = await run(["translate", documentPath, "--provider", "mock", "--out", out]);
    expect(code).toBe(0);
    expect(summary.tableCells).toMatchObject({ tableNumbers: "flag", sentCells: 10, numberChecked: 10, numberNotChecked: 0, suspected: 0, unitFixed: 5, unitWithoutFixed: 1 });
    expect(summary.tables).toEqual({ translated: 1, fixed: 1 });
    const suspects = JSON.parse(readFileSync(join(out, "suspects.json"), "utf8")) as { tableCells: { numberChecked: number }; suspects: unknown[] };
    expect(suspects).toMatchObject({ tableCells: { numberChecked: 10 }, suspects: [] });
    expect(JSON.parse(readFileSync(join(out, "report.json"), "utf8"))).toMatchObject({ tableCells: { suspected: 0 } });
  });

  it("reread: 공급사를 부르지 않고 저장된 응답을 새 규칙에 통과시켜 요약에 의심 수를 싣는다", async () => {
    const { documentPath, callsPath } = files();
    const out = tempDir();
    const { code, summary } = await run(["reread", documentPath, "--calls", callsPath, "--route", "light-openai", "--out", out]);
    expect(code).toBe(0);
    expect(summary).toMatchObject({ task: "reread", provider: "stored-responses", providerCallsThisRun: 0, chunks: { total: 1, done: 1, failed: 0, workCalls: 1 } });
    // 단위 셀을 빼서 조각 구성이 달라졌으므로 항목별 저장 번역을 다시 묶었다.
    expect(summary.reread).toMatchObject({ storedLines: 3, storedLinesUnusable: 0, storedRequests: 1, exactReplies: 0, recomposedReplies: 1, itemsWithoutStoredResponse: [] });
    expect(summary.tableCells).toMatchObject({ sentCells: 10, numberChecked: 10, suspected: 3, suspectedByGrade: { notation_like: 2, other: 1 }, unitFixed: 5 });
    expect(summary.costUsd).toBeUndefined();
    expect(existsSync(join(out, "calls.jsonl"))).toBe(false);
    const suspects = JSON.parse(readFileSync(join(out, "suspects.json"), "utf8")) as { suspects: { cellId: string; grade: string }[] };
    expect(suspects.suspects.map((cell) => [cell.cellId, cell.grade])).toEqual([["t1:r5c0", "other"], ["t1:r0c3", "notation_like"], ["t1:r4c0", "notation_like"]]);
  });

  it("reread --table-numbers strict --unit-cells model: 그때의 응답을 시도 순서대로 돌려줘 옛 규칙의 결과를 다시 만든다", async () => {
    const { documentPath, callsPath } = files();
    const { code, summary } = await run(["reread", documentPath, "--calls", callsPath, "--route", "light-openai", "--table-numbers", "strict", "--unit-cells", "model", "--out", tempDir()]);
    expect(code).toBe(3);
    expect(summary.chunks).toMatchObject({ done: 0, failed: 1, workCalls: 3 });
    expect(summary.reread).toMatchObject({ exactReplies: 3, recomposedReplies: 0 });
    expect(summary.failures[0]).toMatchObject({ kind: "table_number_changed", attempts: 3 });
    expect(summary.tableCells).toMatchObject({ tableNumbers: "strict", unitTable: null, numberChecked: 0, numberNotChecked: 15 });
  });

  it("reread 는 본문 없는 호출 기록, 빠진 인자, --provider 를 받지 않는다", async () => {
    const { documentPath, callsPath } = files();
    const bare = join(tempDir(), "calls.jsonl");
    writeFileSync(bare, `${JSON.stringify({ resultKind: "success", requestHash: "0".repeat(64) })}\n`);
    await expect(run(["reread", documentPath, "--calls", bare, "--route", "light-openai", "--out", tempDir()])).rejects.toThrow(/요청 본문이 없다/);
    const usage = async (argv: string[]) => main(argv, () => undefined, () => undefined);
    expect(await usage(["reread", documentPath, "--route", "light-openai"])).toBe(2);
    expect(await usage(["reread", documentPath, "--calls", callsPath])).toBe(2);
    expect(await usage(["reread", documentPath, "--calls", callsPath, "--route", "light-openai", "--provider", "openai"])).toBe(2);
    expect(await usage(["translate", documentPath, "--dry-run", "--table-numbers", "loose"])).toBe(2);
  });
});
