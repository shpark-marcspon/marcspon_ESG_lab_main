import { describe, expect, it } from "vitest";
import { z } from "zod";
import { labelAnswerSchema, planUnits, validateAnswers, type Extraction, type LabelUnit } from "../src/index.js";
import { catalog, extract, rawLabel, syntheticDocument } from "./helpers.js";

const document = syntheticDocument(
  "label",
  [
    {
      id: "p1-t1",
      before: ["(단위: tCO2eq)"],
      cells: [
        ["구분", "단위", "2023", "2024"],
        ["직접 온실가스(Scope 1)", "tCO2eq", "1,200", "1,350"],
        ["간접 온실가스(Scope 2)", "tCO2eq", "5,000", "5,400"],
      ],
    },
  ],
  [{ id: "p1-b1", text: "여성 임직원은 120명, 남성 임직원은 120명이고 2024년 Scope 1 배출량은 1,350 tCO2eq이다." }],
);
const extraction: Extraction = extract(document, "a");
const facts = new Map(extraction.facts.map((fact) => [fact.id, fact]));
const blockText = new Map([["p1-b1", document.blocks[0]?.text ?? ""]]);
const units = planUnits(extraction, extraction.facts, { maxTargetsPerCall: 40, includeSentences: true }, blockText);
const table = units.find((unit) => unit.kind === "table") as LabelUnit;
const blocks = units.find((unit) => unit.kind === "blocks") as LabelUnit;
const validate = (unit: LabelUnit, labels: ReturnType<typeof rawLabel>[]) => validateAnswers(unit, { labels }, { catalog, facts });

describe("AI 출력 스키마", () => {
  it("숫자 값을 적을 필드가 없다 (순번인 occurrence 만 정수다)", () => {
    const schema = z.toJSONSchema(labelAnswerSchema) as unknown as { properties: { labels: { items: { properties: Record<string, { type?: string; anyOf?: { type: string }[] }> } } } };
    const properties = schema.properties.labels.items.properties;
    const numeric = Object.entries(properties)
      .filter(([, definition]) => [definition.type, ...(definition.anyOf ?? []).map((item) => item.type)].some((type) => type === "number" || type === "integer"))
      .map(([name]) => name);
    expect(numeric).toEqual(["occurrence"]);
    expect(Object.keys(properties).filter((name) => /value|amount|number|값/i.test(name))).toEqual([]);
  });

  it("값 필드를 덧붙인 답은 그 필드가 버려진다", () => {
    const parsed = labelAnswerSchema.parse({ labels: [{ ...rawLabel("r1c2"), value: "9,999" }] });
    expect(parsed.labels[0]).not.toHaveProperty("value");
  });
});

describe("요청 단위", () => {
  it("표 하나를 셀 ID 째로, 제목·단위 줄과 함께 싣고 답할 대상을 적는다", () => {
    const payload = JSON.parse(table.text) as { targets: string[]; rows: { r: number; cells: Record<string, string> }[]; unit_lines: string[]; header_rows: number[] };
    expect(payload.targets).toEqual(["r1c2", "r1c3", "r2c2", "r2c3"]);
    expect(payload.rows[1]).toEqual({ r: 1, cells: { c0: "직접 온실가스(Scope 1)", c1: "tCO2eq", c2: "1,200", c3: "1,350" } });
    expect(payload.unit_lines).toEqual(["(단위: tCO2eq)"]);
    expect(table.targetFactIds).toEqual(["p1-t1:r1c2", "p1-t1:r1c3", "p1-t1:r2c2", "p1-t1:r2c3"]);
  });

  it("큰 표는 행 묶음으로 나누고 머리글 행은 묶음마다 싣는다", () => {
    const split = planUnits(extraction, extraction.facts.filter((fact) => fact.bucket === "table_cell"), { maxTargetsPerCall: 2, includeSentences: false });
    expect(split.map((unit) => [unit.id, unit.targetFactIds.length])).toEqual([
      ["p1-t1/1", 2],
      ["p1-t1/2", 2],
    ]);
    expect((JSON.parse(split[1]?.text ?? "{}") as { rows: { r: number }[] }).rows.map((row) => row.r)).toEqual([0, 2]);
  });
});

describe("AI 답의 검증 (가리킨 ID 와 글자가 원문에 실제로 있는가)", () => {
  it("맞는 답은 받아들인다", () => {
    const result = validate(table, [rawLabel("r1c2", { period: "2023", unit: "tCO2eq", evidence: ["r1c0", "r0c2"] })]);
    expect(result.accepted).toHaveLength(1);
    expect(result.accepted[0]).toMatchObject({ factId: "p1-t1:r1c2", indicatorKey: "ghg.scope1", period: "2023", unit: "tCO2eq", source: "ai" });
    expect(result.unanswered).toEqual(["p1-t1:r1c3", "p1-t1:r2c2", "p1-t1:r2c3"]);
  });

  it("없는 ID 를 가리킨 답은 버린다", () => {
    const result = validate(table, [rawLabel("r9c9"), rawLabel("p1-t1:r1c2")]);
    expect(result.accepted).toEqual([]);
    expect(result.rejected.map((item) => item.reason)).toEqual(["unknown_id", "unknown_id"]);
    expect(result.unanswered).toHaveLength(4);
  });

  it("있는 셀이지만 숫자 대상이 아닌 셀(머리글)을 가리킨 답은 버린다", () => {
    expect(validate(table, [rawLabel("r1c0")]).rejected).toEqual([{ factId: null, target: "r1c0", reason: "not_a_target" }]);
  });

  it("목록에 없는 지표·범위, 원문에 없는 연도·단위, 없는 근거 ID 는 버린다", () => {
    const reasons = (patch: Parameters<typeof rawLabel>[1]) => validate(table, [rawLabel("r1c2", patch)]).rejected.map((item) => item.reason);
    expect(reasons({ indicator_key: "ghg.scope9" })).toEqual(["unknown_indicator"]);
    expect(reasons({ boundary: "galaxy" })).toEqual(["unknown_boundary"]);
    expect(reasons({ period: "2022" })).toEqual(["period_not_in_source"]);
    expect(reasons({ period: "작년" })).toEqual(["bad_period"]);
    // 값 `5,000` 이나 `1,200` 안의 글자로는 연도를 채울 수 없다.
    expect(reasons({ period: "2000" })).toEqual(["period_not_in_source"]);
    expect(reasons({ unit: "천 톤" })).toEqual(["unit_not_in_source"]);
    expect(reasons({ evidence: ["r7c7"] })).toEqual(["unknown_evidence_id"]);
  });

  it("같은 숫자에 답이 둘이면 둘 다 버린다", () => {
    const result = validate(table, [rawLabel("r1c2", { period: "2023" }), rawLabel("r1c2", { indicator_key: "ghg.scope2.location", period: "2023" })]);
    expect(result.accepted).toEqual([]);
    expect(result.rejected).toEqual([{ factId: "p1-t1:r1c2", target: "p1-t1:r1c2", reason: "duplicate_answer" }]);
  });

  it("문장 속 숫자: 블록 ID 와 원문 글자, 몇 번째인지가 맞아야 받아들인다", () => {
    const ok = validate(blocks, [rawLabel("p1-b1", { quote: "1,350", occurrence: 1, period: "2024", unit: "tCO2eq", evidence: ["p1-b1"] })]);
    expect(ok.accepted.map((label) => label.factId)).toEqual(["p1-b1#n5"]);
    const second = validate(blocks, [rawLabel("p1-b1", { quote: "120", occurrence: 2, indicator_key: "employees.total", unit: "명" })]);
    expect(second.accepted.map((label) => label.factId)).toEqual(["p1-b1#n2"]);
  });

  it("문장 속 숫자: 없는 글자, 모자란 순번, 같은 글자가 두 번인데 순번이 없는 답은 버린다", () => {
    const reasons = (patch: Parameters<typeof rawLabel>[1]) => validate(blocks, [rawLabel("p1-b1", patch)]).rejected.map((item) => item.reason);
    expect(reasons({ quote: "1,530", occurrence: 1 })).toEqual(["quote_not_found"]);
    expect(reasons({ quote: "120", occurrence: 3 })).toEqual(["quote_not_found"]);
    expect(reasons({ quote: "120", occurrence: null })).toEqual(["quote_ambiguous"]);
    expect(reasons({ quote: null })).toEqual(["quote_missing"]);
    // 글자는 있지만 코드가 뽑은 숫자의 자리가 아니다 (숫자의 일부만 가리켰다).
    expect(reasons({ quote: "350", occurrence: 1 })).toEqual(["quote_not_a_number"]);
    expect(validate(blocks, [rawLabel("p9-b9", { quote: "120", occurrence: 1 })]).rejected.map((item) => item.reason)).toEqual(["unknown_id"]);
  });
});
