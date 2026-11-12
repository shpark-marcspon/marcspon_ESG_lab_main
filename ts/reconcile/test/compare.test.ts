import { describe, expect, it } from "vitest";
import { z } from "zod";
import { extractDocument, planUnits, reconcile, type Extraction, type Fact, type Label, type MatchOptions, type Pending } from "../src/index.js";
import { pageChunks } from "../src/compare/cli.js";
import { ExamError, applyPlants, checkExam, drawPlants, mutate, prng, type GoldPair, type Plant } from "../src/compare/exam.js";
import { grade } from "../src/compare/grade.js";
import { alignTables, byDocumentSchema, byTableSchema, judgeAlignments, judgeDiffs, judgePairs, matchByDocument, outcomeFromFindings, pageTexts, pairCellsSchema, pairRowsSchema, pairTablesSchema, validateByDocument, validateByTable, validateTablePairs, type DocContext, type MethodOutcome } from "../src/compare/methods.js";
import { MASK, renderDocument, renderRowList, renderTableList } from "../src/compare/render.js";
import { catalog, extract, syntheticDocument, units } from "./helpers.js";

// 방법 비교 실측의 도구를 지어낸 문서로 본다 (synthetic). 어느 회사의 실제 값도 아니다.

const HEADER = { rows: [0], cols: [0, 1], basis: "rows_before_first_numeric_row" };
const table = (id: string, rows: string[][], before: string[] = []) => ({ id, cells: [["구분", "단위", "2023", "2024"], ...rows], header: HEADER, before });
const docA = () => syntheticDocument("a", [table("p1-t1", [["Scope 1", "tCO2eq", "1,200", "1,350"], ["매출액", "억 원", "5,120", "5,480"]])]);
const docB = () => syntheticDocument("b", [table("p1-t1", [["직접 배출", "tCO2eq", "1,200", "1,350"], ["매출액", "억 원", "5,120", "5,480"]]), table("p1-t2", [["Scope 1", "tCO2eq", "1,200", "1,350"]])]);
const context = (extraction: Extraction): DocContext => ({ extraction, pages: pageTexts(extraction, new Map(), new Map()) });

describe("오류 심기", () => {
  const spec = { plants: [{ id: "E01", kind: "digit_changed", isError: true, cells: [{ cell: "p1-t1:r1c3", original: "1,350", planted: "1,530", pair: "P1" }], otherPlaceInB: [] }] satisfies Plant[] };

  it("명세의 칸만 바뀌고 명세 밖의 칸은 원본과 같다. 넘겨받은 문서는 고치지 않는다", () => {
    const original = docB();
    const exam = applyPlants(original, spec);
    expect(exam.tables[0]?.cells[1]?.[3]).toBe("1,530");
    expect(original.tables[0]?.cells[1]?.[3]).toBe("1,350");
    expect(checkExam(original, exam, spec)).toEqual([]);
    (exam.tables[1]?.cells[1] as string[])[2] = "9,999";
    expect(checkExam(original, exam, spec).join("\n")).toContain("명세 밖의 칸이 원본과 다르다");
  });

  it("원래 글자가 명세와 다르면 거부한다", () => {
    expect(() => applyPlants(docB(), { plants: [{ ...spec.plants[0]!, cells: [{ cell: "p1-t1:r1c3", original: "9,999", planted: "1", pair: "P1" }] }] })).toThrow(ExamError);
    expect(() => applyPlants(docB(), { plants: [{ ...spec.plants[0]!, cells: [{ cell: "p9-t9:r1c3", original: "1", planted: "2", pair: "P1" }] }] })).toThrow(ExamError);
  });

  it("글자 바꾸기는 종류마다 조건을 지키고 쉼표를 다시 놓는다", () => {
    expect(mutate("times_1000", "7,284", prng(1))).toBe("7,284,000");
    expect(mutate("times_1000", "12.5", prng(1))).toBeNull();
    expect(mutate("rounding_control", "2,689.020", prng(1))).toBe("2,689");
    expect(mutate("rounding_control", "2,689.7", prng(1))).toBeNull();
    expect(mutate("digits_transposed", "111", prng(1))).toBeNull();
    for (let seed = 1; seed <= 30; seed++) {
      const dropped = mutate("digit_dropped", "20,639", prng(seed)) as string;
      expect(dropped.replace(/,/g, "")).toHaveLength(4);
      expect(dropped).toMatch(/^[1-9]\d{0,2}(,\d{3})*$/);
      expect(mutate("digit_changed", "184", prng(seed))).not.toBe("184");
      expect((mutate("digit_added", "195", prng(seed)) as string).replace(/,/g, "")).toHaveLength(4);
    }
  });

  it("같은 씨앗이면 같은 자리를 뽑고, 쓴 칸과 같은 항목의 다른 자리는 다시 뽑지 않는다", () => {
    const pair = (id: string, item: string, b: string, rawB: string, inSeedTables = true): GoldPair => ({ id, item, period: "2025", a: `a-${id}:r1c1`, b, rawA: rawB, rawB, origin: "added", inSeedTables });
    const pairs = [pair("P1", "x", "t1:r1c3", "1,200"), pair("P2", "y", "t1:r2c3", "3,400"), pair("P3", "x", "t2:r5c1", "1,200", false), pair("P4", "z", "t2:r6c1", "777", false), pair("P5", "w", "t2:r7c1", "8,888", false)];
    const plan = [{ kind: "one_of_two_places", pool: "any" }, { kind: "digit_changed", pool: "any" }, { kind: "digit_dropped", pool: "outside" }] as const;
    const first = drawPlants(pairs, [...plan], 7);
    expect(drawPlants(pairs, [...plan], 7)).toEqual(first);
    expect(["P1", "P3"]).toContain(first[0]?.cells[0]?.pair);
    expect(first[0]?.otherPlaceInB).toHaveLength(1);
    // 둘째는 P1·P3 을 다시 쓰지 못한다. 셋째는 네 표 밖에서만.
    expect(["P2", "P4", "P5"]).toContain(first[1]?.cells[0]?.pair);
    expect(["P4", "P5"]).toContain(first[2]?.cells[0]?.pair);
    expect(new Set(first.flatMap((plant) => plant.cells.map((cell) => cell.cell))).size).toBe(3);
  });
});

describe("A2 의 짝짓기 규칙 (선택)", () => {
  const options: MatchOptions = { catalog, units, minConfidence: "medium", defaultPeriod: { a: null, b: null } };
  const pending = (extraction: Extraction, labels: Record<string, Partial<Label>>): Pending[] =>
    extraction.facts.map((fact: Fact): Pending => {
      const patch = labels[fact.id];
      return patch ? { fact, unverified: null, label: { factId: fact.id, kind: "indicator", indicatorKey: "ghg.scope1", freeText: null, period: "2023", unit: "tCO2eq", boundary: "unspecified", basisNote: null, confidence: "high", evidence: [], source: "ai", ...patch } } : { fact, label: null, unverified: { reason: "not_in_catalog", detail: null } };
    });
  const a = extract(docA(), "a");
  const b = extract(docB(), "b");

  it("(가) 범위가 한쪽만 unspecified 이면 값을 견주고 표시를 남긴다. 끄면 설명 필요다", () => {
    const pa = pending(a, { "p1-t1:r1c2": { boundary: "domestic_overseas" } });
    const pb = pending(b, { "p1-t1:r1c2": {} });
    expect(reconcile(pa, pb, options).find((finding) => finding.a && finding.b)).toMatchObject({ outcome: "needs_explanation", reason: "boundary_differs" });
    const loose = reconcile(pa, pb, { ...options, looseUnspecifiedBoundary: true }).find((finding) => finding.a && finding.b);
    expect(loose).toMatchObject({ outcome: "match" });
    expect(loose?.comparison?.notes).toContain("boundary_unspecified_one_side");
  });

  it("(가) 양쪽에 다르게 적힌 범위는 켜도 설명 필요다", () => {
    const findings = reconcile(pending(a, { "p1-t1:r1c2": { boundary: "domestic" } }), pending(b, { "p1-t1:r1c2": { boundary: "domestic_overseas" } }), { ...options, looseUnspecifiedBoundary: true });
    expect(findings.find((finding) => finding.a && finding.b)).toMatchObject({ outcome: "needs_explanation", reason: "boundary_differs" });
  });

  it("(나) 한 표 안에서 뜻이 같은 답이 둘 이상이면 그 답들을 버린다. 끄면 짝 모호로 진짜 숫자까지 끌려간다", () => {
    // 문서 B 의 첫 표에서 두 행(직접 배출, 매출액)이 같은 뜻으로 붙었다 (억지로 붙은 지표). 둘째 표의 Scope 1 이 진짜다.
    const pa = pending(a, { "p1-t1:r1c2": {} });
    const pb = pending(b, { "p1-t1:r1c2": {}, "p1-t1:r2c2": {}, "p1-t2:r1c2": {} });
    expect(reconcile(pa, pb, options).filter((finding) => finding.reason === "ambiguous_pair")).toHaveLength(4);
    const findings = reconcile(pa, pb, { ...options, dropRepeatedKeysInTable: true });
    expect(findings.filter((finding) => finding.reason === "repeated_key_in_table").map((finding) => finding.b?.factId).sort()).toEqual(["p1-t1:r1c2", "p1-t1:r2c2"]);
    expect(findings.find((finding) => finding.a && finding.b)).toMatchObject({ outcome: "match", b: { factId: "p1-t2:r1c2" } });
  });
});

describe("제목·각주 잇기 (선택)", () => {
  const document = syntheticDocument("a", [
    { ...table("p1-t1", [["Scope 1", "tCO2eq", "900", "950"]]), before: ["- 위 표의 제목 (국내 사업장 기준)"] },
    { ...table("p1-t2", [["Scope 1", "tCO2eq", "1,200", "1,350"]]), before: ["1) 위 표의 각주", "[참고] 아래 표의 제목 (국내외 사업장)"] },
  ]);
  document.blocks.push({ id: "after", kind: "paragraph", page: 1, bbox: [0, 0, 1, 1], order: 99, text: "* 아래 표의 각주" }, { id: "body", kind: "paragraph", page: 1, bbox: [0, 0, 1, 1], order: 100, text: "본문 글" });

  it("기본은 표 앞의 글 셋이라 옆 표의 제목과 각주가 섞인다", () => {
    expect(extractDocument(document, "a").grids[1]?.titleCandidates).toEqual(["[참고] 아래 표의 제목 (국내외 사업장)", "1) 위 표의 각주", "- 위 표의 제목 (국내 사업장 기준)"]);
  });

  it("own 은 그 표의 제목과 그 표의 각주만 싣는다", () => {
    const grids = extractDocument(document, "a", { titles: "own" }).grids;
    expect(grids[0]?.titleCandidates).toEqual(["- 위 표의 제목 (국내 사업장 기준)", "1) 위 표의 각주"]);
    expect(grids[1]?.titleCandidates).toEqual(["[참고] 아래 표의 제목 (국내외 사업장)", "* 아래 표의 각주"]);
  });
});

describe("답의 스키마에 값 필드가 없다 (B·C·D·F)", () => {
  it("수를 적을 수 있는 필드가 하나도 없다", () => {
    for (const schema of [byTableSchema, byDocumentSchema, pairCellsSchema, pairTablesSchema, pairRowsSchema]) {
      expect(JSON.stringify(z.toJSONSchema(schema))).not.toMatch(/"type":"(number|integer)"/);
    }
    // 덧붙여 온 값은 스키마가 버린다.
    expect(pairCellsSchema.parse({ pairs: [{ a: "x", b: "y", unit_a: null, unit_b: null, value: 123 }] }).pairs[0]).not.toHaveProperty("value");
  });
});

describe("방법 B·C·D·E 의 답 확인", () => {
  const a = extract(docA(), "a");
  const b = extract(docB(), "b");

  it("B: 없는 칸과 목록에 없는 지표를 가리킨 답은 버리고, 가리키지 않은 숫자는 뜻이 붙지 않는다", () => {
    const unit = planUnits(a, a.facts, { maxTargetsPerCall: 100, includeSentences: false })[0]!;
    const checked = validateByTable(unit, { items: [{ indicator_key: "ghg.scope1", unit: "tCO2eq", boundary: null, basis_note: null, cells: [{ target: "r1c2", period: "2023" }, { target: "r9c9", period: "2023" }, { target: "r1c3", period: "1999" }] }, { indicator_key: "no.such", unit: null, boundary: null, basis_note: null, cells: [{ target: "r2c2", period: "2023" }] }] }, { catalog, facts: new Map(a.facts.map((fact) => [fact.id, fact])) });
    expect(checked.accepted.map((label) => label.factId)).toEqual(["p1-t1:r1c2"]);
    expect(checked.rejected.map((item) => item.reason).sort()).toEqual(["period_not_in_source", "unknown_id", "unknown_indicator"]);
    expect(checked.unanswered).toHaveLength(3);
  });

  it("C: 없는 칸·머리글 칸·원문에 없는 연도는 버리고, 같은 (지표, 기간)의 자리를 문서 사이와 문서 안에서 다 견준다", () => {
    const place = (cell: string, period: string | null = "2023") => ({ cell, period, unit: "tCO2eq", boundary: null });
    const inA = validateByDocument({ items: [{ indicator_key: "ghg.scope1", places: [place("p1-t1:r1c2"), place("p1-t1:r0c2"), place("p9-t9:r1c1"), place("p1-t1:r1c3", "1999")] }] }, context(a), catalog);
    expect(inA.places.map((item) => item.fact.id)).toEqual(["p1-t1:r1c2"]);
    expect(inA.rejected).toEqual({ unknown_id: 2, period_not_in_source: 1 });
    const exam = extract(applyPlants(docB(), { plants: [{ id: "E", kind: "digit_changed", isError: true, cells: [{ cell: "p1-t2:r1c2", original: "1,200", planted: "1,900", pair: "P" }], otherPlaceInB: [] }] }), "b");
    const inB = validateByDocument({ items: [{ indicator_key: "ghg.scope1", places: [place("p1-t1:r1c2"), place("p1-t2:r1c2")] }] }, context(exam), catalog);
    const outcome = matchByDocument(inA.places, inB.places, units);
    expect(outcome.verdicts.map((verdict) => `${verdict.b}:${verdict.outcome}`).sort()).toEqual(["p1-t1:r1c2:match", "p1-t2:r1c2:mismatch"]);
    expect(outcome.intra).toMatchObject([{ doc: "b", outcome: "mismatch" }]);
  });

  it("D: 없는 칸을 가리킨 짝은 버리고, 남은 짝은 코드가 값을 견준다", () => {
    const outcome = judgePairs({ pairs: [{ a: "p1-t1:r1c2", b: "p1-t1:r1c2", unit_a: "tCO2eq", unit_b: "tCO2eq" }, { a: "p1-t1:r1c2", b: "p1-t1:r2c2", unit_a: null, unit_b: null }, { a: "p1-t1:r7c7", b: "p1-t1:r1c2", unit_a: null, unit_b: null }, { a: "p1-t1:r1c3", b: "p1-t1:r1c3", unit_a: "kWh", unit_b: null }] }, context(a), context(b), units);
    expect(outcome.verdicts.map((verdict) => verdict.outcome)).toEqual(["match", "mismatch"]);
    expect(outcome.rejected).toEqual({ unknown_id: 1, unit_not_in_source: 1 });
  });

  it("E: 가리킨 칸이 있는지만 본다", () => {
    const outcome = judgeDiffs({ discrepancies: [{ a: "p1-t1:r1c2", b: "p1-t1:r2c2", reason: "…" }, { a: "nope", b: "p1-t1:r1c2", reason: "…" }] }, context(a), context(b), units);
    expect(outcome.verdicts).toMatchObject([{ outcome: "mismatch", reason: "model_said_differs", valuesDiffer: true }]);
    expect(outcome.rejected).toEqual({ unknown_id: 1 });
  });
});

describe("숫자 가림 (D·F)", () => {
  const document = syntheticDocument("a", [{ ...table("p1-t1", [["Scope 1", "tCO2eq", "48,217", "39,604"], ["감축률", "%", "17.3%", "기준연도 대비 26.5% 감축"]]), before: ["2023년 배출량은 48,217 tCO2eq 이다"] }]);
  const extraction = extractDocument(document, "a", { titles: "own" });
  const values = ["48,217", "39,604", "17.3", "26.5", "48217", "39604"];

  it("요청 글에 값 후보 숫자가 남지 않는다. 머리글의 연도와 행 이름은 남는다", () => {
    const masked = renderDocument(document, extraction, { mask: true });
    for (const value of values) expect(masked).not.toContain(value);
    expect(masked).toContain(`c2: ${MASK}`);
    expect(masked).toContain(`${MASK}%`);
    expect(masked).toContain("c2: 2023");
    expect(masked).toContain("Scope 1");
    expect(masked).toContain("2023년 배출량은");
    // 가리지 않으면 값이 그대로 실린다.
    expect(renderDocument(document, extraction, { mask: false })).toContain("48,217");
  });

  it("가린 기호는 자릿수와 무관하게 하나다", () => {
    expect(renderDocument(document, extraction, { mask: true }).split(MASK).length - 1).toBe(5);
  });

  it("F 의 표 목록에도 값 후보 숫자가 남지 않는다", () => {
    const list = renderTableList(extraction);
    for (const value of values) expect(list).not.toContain(value);
    expect(list).toContain("[표 a:p1-t1]");
    expect(list).toContain("2023 | 2024");
  });

  it("F 의 표 목록과 행 짝 요청은 머리글과 제목·각주 속의 값 후보 숫자도 가린다", () => {
    const withValueInHeader = syntheticDocument("a", [{ ...table("p1-t1", [["허용 오차 4.75% 이내 배출량", "tCO2eq", "48,217", "39,604"]]), before: ["중요성 기준 6.25% 미만"] }]);
    const own = extractDocument(withValueInHeader, "a", { titles: "own" });
    const list = renderTableList(own);
    const rows = renderRowList("a", own.grids[0]!, [{ id: "r1", header: "허용 오차 4.75% 이내 배출량 > tCO2eq" }]);
    for (const text of [list, rows]) {
      expect(text).not.toContain("4.75");
      expect(text).not.toContain("6.25");
      expect(text).toContain(`허용 오차 ${MASK} 이내 배출량`);
      expect(text).toContain(`중요성 기준 ${MASK} 미만`);
    }
    expect(rows).toContain("r1: ");
  });
});

describe("방법 F: 표 짝과 행·열 맞추기", () => {
  const a = extract(syntheticDocument("a", [table("p1-t1", [["Scope 11)", "tCO2eq", "1,200", "1,350"], ["임직원 통근", "tCO2eq", "310", "330"], ["사업장 폐기물", "tCO2eq", "40", "45"]])]), "a");
  const b = extract(syntheticDocument("b", [{ id: "p2-t1", cells: [["지표", "단위", "2024", "2023"], ["Scope 1", "tCO2eq", "1,350", "1,200"], ["통근", "tCO2eq", "330", "310"], ["폐기물", "tCO2eq", "45", "40"]], header: { rows: [0], cols: [0, 1], basis: "rows_before_first_numeric_row" } }]), "b");
  const side = (extraction: Extraction) => ({ grid: extraction.grids[0]!, facts: extraction.facts });

  it("표 짝 답: 목록에 없는 표 ID 는 버린다", () => {
    const checked = validateTablePairs({ table_pairs: [{ a_table: "a:p1-t1", b_table: "b:p2-t1" }, { a_table: "a:p1-t1", b_table: "b:p2-t1" }, { a_table: "a:p9-t9", b_table: "b:p2-t1" }, { a_table: "p1-t1", b_table: "b:p2-t1" }] }, new Set(["p1-t1"]), new Set(["p2-t1"]));
    expect(checked.pairs).toEqual([{ a: "p1-t1", b: "p2-t1" }]);
    expect(checked.rejected).toEqual({ duplicate_pair: 1, unknown_id: 2 });
  });

  it("행은 정규화한 머리글로, 열은 연도로 맞춘다 (열 순서가 달라도). 글자가 다른 행은 남긴다", () => {
    const aligned = alignTables(side(a), side(b), units);
    expect(aligned.cellPairs.map((pair) => `${pair.a.id}=${pair.b.id}`)).toEqual(["p1-t1:r1c2=p2-t1:r1c3", "p1-t1:r1c3=p2-t1:r1c2"]);
    expect(aligned.unmatchedRows).toEqual({ a: [{ id: "r2", header: "임직원 통근 > tCO2eq" }, { id: "r3", header: "사업장 폐기물 > tCO2eq" }], b: [{ id: "r2", header: "통근 > tCO2eq" }, { id: "r3", header: "폐기물 > tCO2eq" }] });
  });

  it("AI 의 행 짝을 더하면 그 행도 견주고, 없는 행 ID 는 버린다. 견주지 못한 숫자는 확인 못 함으로 남는다", () => {
    const aligned = alignTables(side(a), side(b), units, [{ a_row: "r2", b_row: "r2" }, { a_row: "r8", b_row: "r3" }]);
    expect(aligned.rejectedRowPairs).toBe(1);
    const outcome = judgeAlignments([aligned], a, b, units);
    expect(outcome.verdicts).toHaveLength(4);
    expect(outcome.verdicts.every((verdict) => verdict.outcome === "match")).toBe(true);
    expect(Object.values(outcome.states.a)).toEqual(["unverified:row_or_column_not_aligned", "unverified:row_or_column_not_aligned"]);
    expect(Object.keys(judgeAlignments([], a, b, units).states.b)).toHaveLength(6);
  });
});

describe("요청 나누기", () => {
  it("쪽 순서로, 조각이 다 한도에 들 때까지 조각 수를 늘린다", () => {
    expect(pageChunks([1, 2, 3, 4, 5, 6], () => true)).toEqual([{ from: 1, to: 6 }]);
    expect(pageChunks([1, 2, 3, 4, 5, 6], (from, to) => to - from < 3)).toEqual([{ from: 1, to: 3 }, { from: 4, to: 6 }]);
  });
});

describe("방법별 채점", () => {
  const pair = (id: string): GoldPair => ({ id, item: id, period: "2025", a: `ta:${id}`, b: `tb:${id}`, rawA: "1", rawB: "1", origin: "added", inSeedTables: true });
  const pairs = ["clean1", "clean2", "control", "caught", "explained", "elsewhere", "leaked", "silent"].map(pair);
  const plant = (id: string, isError = true): Plant => ({ id, kind: isError ? "digit_changed" : "rounding_control", isError, cells: [{ cell: `tb:${id}`, original: "1", planted: "2", pair: id }], otherPlaceInB: [] });
  const plants = [plant("control", false), plant("caught"), plant("explained"), plant("elsewhere"), plant("leaked"), plant("silent")];
  const verdict = (id: string, outcome: "match" | "mismatch" | "needs_explanation", valuesDiffer: boolean, a = `ta:${id}`) => ({ a, b: `tb:${id}`, outcome, reason: "r", valuesDiffer });
  const outcome: MethodOutcome = {
    verdicts: [verdict("clean1", "match", false), verdict("control", "match", false), verdict("caught", "mismatch", true), verdict("explained", "needs_explanation", true), verdict("elsewhere", "mismatch", true, "ta:other"), verdict("leaked", "match", false), { a: "ta:x", b: "tb:y", outcome: "mismatch", reason: "r", valuesDiffer: true }],
    intra: [],
    states: { a: { "ta:silent": "unverified:ambiguous_pair", "ta:clean2": "unpaired:no_counterpart" }, b: { "tb:silent": "unpaired:no_counterpart" } },
    rejected: {},
  };
  const result = grade(pairs, plants, outcome);

  it("참 일치와 대조군은 심은 오류와 따로 센다", () => {
    expect(result.clean).toMatchObject({ total: 2, match: 1, other: 1 });
    expect(result.control).toMatchObject({ total: 1, match: 1 });
  });

  it("잡은 오류는 그 쌍이 불일치로 나온 것만이다. 조용히 지나간 오류와 거짓 일치는 잡은 것으로 세지 않는다", () => {
    expect(result.errors).toMatchObject({ total: 5, caught: 1, visibleOther: 2, falseMatch: 1, silent: 1 });
    expect(result.errors.rows.find((row) => row.pair === "silent")).toMatchObject({ status: "silent", bucket: "silent" });
    expect(result.errors.rows.find((row) => row.pair === "leaked")).toMatchObject({ status: "match", bucket: "false_match" });
    expect(result.errors.rows.find((row) => row.pair === "elsewhere")).toMatchObject({ status: "visible_elsewhere", bucket: "visible_other" });
  });

  it("불일치 가운데 심은 오류를 정답의 상대와 잡은 것이 아닌 것은 갈래와 함께 남긴다", () => {
    expect(result.mismatches).toMatchObject({ total: 3, truePlanted: 1 });
    expect(result.mismatches.other.map((item) => item.kind).sort()).toEqual(["outside_gold", "planted_cell_other_partner"]);
    expect(result.unchecked).toEqual({ a: { "unverified:ambiguous_pair": 1, "unpaired:no_counterpart": 1 }, b: { "unpaired:no_counterpart": 1 } });
  });

  it("reconcile 의 결과를 공통의 꼴로 옮긴다", () => {
    const converted = outcomeFromFindings([{ outcome: "unpaired", reason: "no_counterpart", detail: null, key: null, a: { factId: "t:r1c1" } as never, b: null, comparison: null }]);
    expect(converted.states.a).toEqual({ "t:r1c1": "unpaired:no_counterpart" });
  });
});
