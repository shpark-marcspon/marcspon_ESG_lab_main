import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { extractDocument, type Extraction } from "../src/index.js";
import { main } from "../src/compare/cli.js";
import type { GoldPair } from "../src/compare/exam.js";
import { grade, type MethodGrade } from "../src/compare/grade.js";
import { TallyError, type TablePair } from "../src/compare/hybrid.js";
import { pageTexts, type DocContext } from "../src/compare/methods.js";
import { MASK } from "../src/compare/render.js";
import { noCounterpartUnion, newPairUnion, recoveries, renderReview, type ReviewRun } from "../src/compare/review-report.js";
import { baseFromPairAnswers, classifyBefore, deriveTablePairs, judgeCellReview, planCellReviews, renderCellReviewRequest, renderTableReviewRequest, reviewCellsSchema, reviewOutcome, tallyReview, unmaskedInCellReview, unmaskedInTableReview, validateNewTablePairs } from "../src/compare/review.js";
import { asDocumentJson, syntheticDocument, units } from "./helpers.js";

// 방법 H (D 의 답 + 코드의 셈 + 좁은 검수)를 지어낸 문서로 본다 (synthetic). 어느 회사의 실제 값도 아니다.

const HEADER = { rows: [0], cols: [0, 1], basis: "rows_before_first_numeric_row" };
const table = (id: string, page: number, rows: string[][], before: string[] = []) => ({ id, page, cells: [["구분", "단위", "2023", "2024"], ...rows], header: HEADER, before });
// 문서 a: 1쪽에 표 둘과 글, 2쪽에 표 하나. 문서 b: 1쪽에 표 둘과 글.
const documentA = () =>
  syntheticDocument(
    "a",
    [
      table("p1-t1", 1, [["Scope 1", "tCO2eq", "48,217", "39,604"], ["Scope 2", "tCO2eq", "12,345", "11,871"], ["감축률", "%", "17.3%", "21.4%"]], ["배출량 현황 (목표 91,350 tCO2eq 이내)"]),
      table("p1-t2", 1, [["용수 사용량", "톤", "7,731", "7,912"]], ["같은 쪽의 다른 표"]),
      table("p2-t1", 2, [["폐기물", "톤", "615", "642"]], ["다른 쪽의 표"]),
    ],
    [{ id: "a-body", page: 1, text: "2024년 배출량은 39,604 tCO2eq 이며 전년 대비 8,613 tCO2eq 줄었다" }],
  );
const documentB = () =>
  syntheticDocument(
    "b",
    [table("p3-t1", 1, [["직접 배출", "tCO2eq", "48,217", "39,640"], ["간접 배출", "tCO2eq", "12,345", "11,871"], ["임직원 수", "명", "1,204", "1,251"]], ["온실가스 (오차 4.75% 이내)"]), table("p3-t2", 1, [["전력 사용량", "MWh", "2,210", "2,305"]])],
    [{ id: "b-body", page: 1, text: "검증 대상 사업장 37개" }],
  );
const own = (document: ReturnType<typeof documentA>, id: string): Extraction => extractDocument(document, id, { titles: "own" });
const context = (extraction: Extraction): DocContext => ({ extraction, pages: pageTexts(extraction, new Map(), new Map()) });
const VALUES = ["48,217", "39,604", "39,640", "12,345", "11,871", "17.3", "21.4", "91,350", "8,613", "4.75", "1,204", "1,251", "7,731", "7,912", "615", "642", "37", "2,210", "2,305"];

const a = own(documentA(), "a");
const b = own(documentB(), "b");
const sideA = { document: documentA(), extraction: a };
const sideB = { document: documentB(), extraction: b };
// D 의 답: 두 표 사이의 칸 짝 셋(하나는 값이 다르다)과, 없는 칸을 가리킨 답 하나.
const D_ANSWER = {
  pairs: [
    { a: "p1-t1:r1c2", b: "p3-t1:r1c2", unit_a: "tCO2eq", unit_b: "tCO2eq" },
    { a: "p1-t1:r1c3", b: "p3-t1:r1c3", unit_a: null, unit_b: null },
    { a: "p1-t1:r2c2", b: "p3-t1:r2c2", unit_a: null, unit_b: null },
    { a: "p1-t1:r9c9", b: "p3-t2:r1c2", unit_a: null, unit_b: null },
  ],
};
const base = () => baseFromPairAnswers([D_ANSWER], context(a), context(b), units);
const PAIR: TablePair = { a: "p1-t1", b: "p3-t1" };
const sumOf = (tally: ReturnType<typeof tallyReview>): number => {
  const bag = (counts: Record<string, number>): number => Object.values(counts).reduce((x, y) => x + y, 0);
  return tally.compared.match + tally.compared.mismatch + tally.compared.needsExplanation + tally.leftInPairedTable + bag(tally.noCounterpart) + tally.undecided.undecidable + tally.undecided.unanswered + tally.undecided.answerRejected + tally.leftInNewlyPairedTable + tally.answerRejected + tally.tableNotPaired + bag(tally.outsideTables);
};

describe("방법 H: D 의 답에서 표 짝 끌어내기", () => {
  it("저장된 답을 D 와 같은 확인에 넣고, 버려진 답이 가리킨 있는 칸을 남긴다", () => {
    const result = base();
    expect(result.outcome.verdicts.map((verdict) => `${verdict.a}|${verdict.b}:${verdict.outcome}`)).toEqual(["p1-t1:r1c2|p3-t1:r1c2:match", "p1-t1:r1c3|p3-t1:r1c3:mismatch", "p1-t1:r2c2|p3-t1:r2c2:match"]);
    expect(result.outcome.rejected).toEqual({ unknown_id: 1 });
    expect([...result.rejectedCells.a]).toEqual([]);
    expect([...result.rejectedCells.b]).toEqual(["p3-t2:r1c2"]);
    // 조각이 여럿이면 같은 칸 짝은 한 번만.
    expect(baseFromPairAnswers([D_ANSWER, D_ANSWER], context(a), context(b), units).outcome.verdicts).toHaveLength(3);
  });

  it("두 표 사이의 칸 짝이 문턱 이상이면 짝지어진 표다", () => {
    const verdicts = [...base().outcome.verdicts, { a: "p1-t2:r1c2", b: "p3-t2:r1c2" }];
    expect(deriveTablePairs(verdicts)).toEqual([{ a: "p1-t1", b: "p3-t1", cellPairs: 3 }, { a: "p1-t2", b: "p3-t2", cellPairs: 1 }]);
    expect(deriveTablePairs(verdicts, 2)).toEqual([{ a: "p1-t1", b: "p3-t1", cellPairs: 3 }]);
    expect(deriveTablePairs(verdicts, 4)).toEqual([]);
    expect(deriveTablePairs([])).toEqual([]);
  });
});

describe("방법 H: 검수 전의 셈", () => {
  const result = reviewOutcome({ a, b, base: base(), cellReviews: [], newTablePairs: [], newTableJudgements: [], tableRejected: {} });

  it("모든 값 후보가 결과 하나에: 견줌, 짝지어진 표 안의 남은 칸, 짝이 없는 표의 칸, 버려진 답, 표의 숫자 칸이 아님", () => {
    const before = result.before.tally;
    // 문서 a: p1-t1 의 숫자 칸 6 (견줌 3 + 남은 칸 3), p1-t2 둘, p2-t1 둘, 문장 속 (제목 1 + 본문 2).
    expect(before.a).toMatchObject({ total: a.facts.length, compared: { match: 2, mismatch: 1, needsExplanation: 0 }, leftInPairedTable: 3, tableNotPaired: 4, answerRejected: 0 });
    expect(before.a.outsideTables).toEqual({ sentence: a.facts.filter((fact) => fact.bucket === "sentence").length });
    // 문서 b: p3-t1 의 숫자 칸 6, p3-t2 둘 (하나는 버려진 답이 가리켰다).
    expect(before.b).toMatchObject({ total: b.facts.length, compared: { match: 2, mismatch: 1, needsExplanation: 0 }, leftInPairedTable: 3, tableNotPaired: 1, answerRejected: 1 });
    for (const side of ["a", "b"] as const) expect(sumOf(before[side])).toBe(before[side].total);
    expect(result.before.outcome.states.a["p1-t1:r3c2"]).toBe("left:in_paired_table");
    expect(result.before.outcome.states.a["p2-t1:r1c2"]).toBe("unverified:table_not_paired");
    expect(result.before.outcome.states.b["p3-t2:r1c2"]).toBe("unverified:answer_rejected");
    expect(result.before.outcome.states.b["p3-t2:r1c3"]).toBe("unverified:table_not_paired");
  });

  it("검수가 없으면 남은 칸은 검수 뒤에 「미응답」이다. 「상대 없음」이 아니다", () => {
    expect(result.after.tally.a).toMatchObject({ leftInPairedTable: 0, noCounterpart: {}, undecided: { undecidable: 0, unanswered: 3, answerRejected: 0 } });
    expect(result.newPairs).toEqual([]);
  });

  it("한 칸이 여러 짝에서 견주어졌으면 가장 무거운 결과가 그 칸의 결과다", () => {
    const states = classifyBefore(b, "b", new Set(["p3-t1"]), [{ a: "x", b: "p3-t1:r1c2", outcome: "match", reason: "", valuesDiffer: false }, { a: "y", b: "p3-t1:r1c2", outcome: "mismatch", reason: "", valuesDiffer: true }], new Set());
    expect(states.get("p3-t1:r1c2")).toBe("compared:mismatch");
    expect(states.get("p3-t1:r1c3")).toBe("left:in_paired_table");
  });

  it("합이 값 후보 수와 다르면 수를 내지 않고 예외를 낸다", () => {
    expect(() => tallyReview(3, ["compared:match", "left:in_paired_table"])).toThrow(TallyError);
    expect(tallyReview(3, ["compared:match", "left:in_paired_table", "reviewed:no_counterpart:different_item"])).toMatchObject({ total: 3, leftInPairedTable: 1, noCounterpart: { different_item: 1 } });
    expect(() => tallyReview(1, ["모르는:갈래" as never])).toThrow(TallyError);
  });
});

describe("방법 H: 칸 검수 요청의 조립", () => {
  const verdicts = base().outcome.verdicts;
  const plans = planCellReviews(a, b, deriveTablePairs(verdicts), verdicts);
  const plan = plans[0]!;
  const text = renderCellReviewRequest(sideA, sideB, plan);

  it("남은 칸이 있는 표 짝마다 요청 하나. 남은 칸은 어느 짝에서도 견주어지지 않은 그 두 표의 숫자 칸이다", () => {
    expect(plans).toHaveLength(1);
    expect(plan).toMatchObject({ id: "h/cells/p1-t1|p3-t1", pair: PAIR });
    expect(plan.paired).toEqual([{ a: "p1-t1:r1c2", b: "p3-t1:r1c2" }, { a: "p1-t1:r1c3", b: "p3-t1:r1c3" }, { a: "p1-t1:r2c2", b: "p3-t1:r2c2" }]);
    expect(plan.remaining).toEqual(["a:p1-t1:r2c3", "a:p1-t1:r3c2", "a:p1-t1:r3c3", "b:p3-t1:r2c3", "b:p3-t1:r3c2", "b:p3-t1:r3c3"]);
    // 남은 칸이 없는 표 짝은 묻지 않는다.
    const all = [...verdicts, ...["r2c3", "r3c2", "r3c3"].map((cell) => ({ a: `p1-t1:${cell}`, b: `p3-t1:${cell}` }))];
    expect(planCellReviews(a, b, deriveTablePairs(all), all)).toEqual([]);
  });

  it("남은 칸이 많으면 목록을 나눠 요청을 여럿 만든다", () => {
    const parts = planCellReviews(a, b, deriveTablePairs(verdicts), verdicts, 4);
    expect(parts.map((part) => part.id)).toEqual(["h/cells/p1-t1|p3-t1#1", "h/cells/p1-t1|p3-t1#2"]);
    expect(parts.map((part) => part.remaining.length)).toEqual([4, 2]);
    expect(parts.flatMap((part) => part.remaining)).toEqual(plan.remaining);
  });

  it("두 표와 그 쪽의 글, 이미 짝지어진 칸, 남은 칸만 실린다", () => {
    expect(text).toContain("[표 p1-t1]");
    expect(text).toContain("[표 p3-t1]");
    expect(text).not.toContain("[표 p1-t2]");
    expect(text).not.toContain("[표 p2-t1]");
    expect(text).not.toContain("[표 p3-t2]");
    expect(text).not.toContain("용수 사용량");
    expect(text).not.toContain("폐기물");
    expect(text).not.toContain("전력 사용량");
    expect(text).toContain("a:p1-t1:r1c3 = b:p3-t1:r1c3");
    const lists = text.slice(text.indexOf("# 이미 짝지어진 칸"));
    expect(lists.split("\n").filter((line) => / = /.test(line))).toHaveLength(3);
    expect(lists.slice(lists.indexOf("# 남은 칸")).split("\n").slice(1)).toEqual(plan.remaining);
    // 다른 표의 칸은 목록에도 없다.
    expect(lists).not.toContain("p1-t2");
    expect(lists).not.toContain("p3-t2");
  });

  it("요청 글에 값 후보 숫자가 남지 않는다: 표 칸, 제목·각주, 쪽의 글 전부", () => {
    for (const value of VALUES) expect(text).not.toContain(value);
    expect(unmaskedInCellReview(text)).toEqual([]);
    expect(text).toContain(`r2 | c0: Scope 2 | c1: tCO2eq | c2: ${MASK} | c3: ${MASK}`);
    expect(text).toContain(`전년 대비 ${MASK} tCO2eq 줄었다`);
    expect(text).toContain(`검증 대상 사업장 ${MASK}`);
  });

  it("가림이 빠진 글과, 목록에 섞인 문서의 글자는 확인이 잡는다", () => {
    expect(unmaskedInCellReview(text.replace(`전년 대비 ${MASK}`, "전년 대비 8,613"))).toEqual(["8,613"]);
    expect(unmaskedInCellReview(`${text}\nScope 2 는 11,871`)).toEqual(["Scope 2 는 11,871"]);
    expect(unmaskedInCellReview("목록 구획이 없는 글")).toHaveLength(1);
  });
});

describe("방법 H: 칸 검수의 답 확인", () => {
  const verdicts = base().outcome.verdicts;
  const plan = planCellReviews(a, b, deriveTablePairs(verdicts), verdicts)[0]!;
  const answer = (cell: string, counterpart: string | null, reason: "no_such_row_or_column" | "different_item" | "undecidable" | null = null) => ({ cell, counterpart, reason, unit: null, counterpart_unit: null });
  const judged = judgeCellReview(
    {
      answers: [
        answer("a:p1-t1:r2c3", "b:p3-t1:r2c3"), // 새 짝
        answer("b:p3-t1:r2c3", "a:p1-t1:r2c3"), // 같은 짝을 상대 쪽에서 한 번 더
        answer("a:p1-t1:r3c2", null, "no_such_row_or_column"),
        // a:p1-t1:r3c3 은 답이 없다
        answer("b:p3-t1:r3c2", "a:p1-t2:r1c2"), // 그 요청의 두 표 밖의 칸
        answer("b:p3-t1:r3c3", null, "undecidable"),
        answer("a:p1-t1:r1c2", null, "different_item"), // 남은 칸 목록에 없는 칸
        answer("a:p1-t1:r9c9", null, "different_item"), // 없는 ID
      ],
    },
    plan,
    context(a),
    context(b),
    units,
  );

  it("답의 스키마에 수를 적을 수 있는 필드가 없다", () => {
    expect(JSON.stringify(z.toJSONSchema(reviewCellsSchema))).not.toMatch(/"type":"(number|integer)"/);
    expect(reviewCellsSchema.parse({ answers: [{ cell: "x", counterpart: null, reason: null, unit: null, counterpart_unit: null, value: 1 }] }).answers[0]).not.toHaveProperty("value");
  });

  it("목록 밖의 칸, 없는 ID, 두 표 밖의 칸을 가리킨 답은 버리고, 새 짝은 코드가 값을 견준다", () => {
    expect(judged.newPairs).toEqual([{ a: "p1-t1:r2c3", b: "p3-t1:r2c3", outcome: "match", reason: expect.any(String), valuesDiffer: false, from: ["a:p1-t1:r2c3", "b:p3-t1:r2c3"] }]);
    expect(judged.rejected).toEqual({ not_in_remaining_list: 1, unknown_id: 1, outside_request_tables: 1 });
    expect(Object.fromEntries(judged.cells)).toEqual({
      "a:p1-t1:r2c3": { state: "paired", counterpart: "b:p3-t1:r2c3" },
      "a:p1-t1:r3c2": { state: "no_counterpart", reason: "no_such_row_or_column" },
      "a:p1-t1:r3c3": { state: "unanswered" },
      "b:p3-t1:r2c3": { state: "paired", counterpart: "a:p1-t1:r2c3" },
      "b:p3-t1:r3c2": { state: "answer_rejected", why: "outside_request_tables" },
      "b:p3-t1:r3c3": { state: "undecidable" },
    });
  });

  it("안 온 답은 미응답이다. 요청이 실패해도 남은 칸은 전부 미응답이다", () => {
    expect(judged.cells.get("a:p1-t1:r3c3")).toEqual({ state: "unanswered" });
    const failed = judgeCellReview(null, plan, context(a), context(b), units);
    expect([...failed.cells.values()].every((item) => item.state === "unanswered")).toBe(true);
    expect(failed.cells.size).toBe(plan.remaining.length);
  });

  it("같은 문서의 칸, 없는 상대 칸, 원문에 없는 단위 글자, 상대도 사유도 없는 답, 엇갈린 답은 버린다", () => {
    const more = judgeCellReview(
      {
        answers: [
          answer("a:p1-t1:r2c3", "a:p1-t1:r3c3"), // 같은 문서의 칸
          answer("a:p1-t1:r3c2", "b:p3-t1:r8c8"), // 없는 상대 칸
          { cell: "a:p1-t1:r3c3", counterpart: "b:p3-t1:r3c3", reason: null, unit: "kWh", counterpart_unit: null }, // 원문에 없는 단위 글자
          answer("b:p3-t1:r2c3", null), // 상대도 사유도 없다
          answer("b:p3-t1:r3c2", null, "different_item"),
          answer("b:p3-t1:r3c2", null, "no_such_row_or_column"), // 한 칸에 엇갈린 답 둘
          answer("b:p3-t1:r3c3", null, "different_item"),
          answer("b:p3-t1:r3c3", null, "different_item"), // 글자까지 같은 답 둘은 하나로
        ],
      },
      plan,
      context(a),
      context(b),
      units,
    );
    expect(more.newPairs).toEqual([]);
    expect(more.rejected).toEqual({ outside_request_tables: 1, unknown_id: 1, unit_not_in_source: 1, neither_counterpart_nor_reason: 1, conflicting_answers: 2, duplicate_answer: 1 });
    expect([...more.cells.values()].filter((item) => item.state === "answer_rejected")).toHaveLength(5);
    expect(more.cells.get("b:p3-t1:r3c3")).toEqual({ state: "no_counterpart", reason: "different_item" });
  });

  describe("검수 뒤의 셈", () => {
    const result = reviewOutcome({ a, b, base: base(), cellReviews: [judged], newTablePairs: [{ a: "p1-t2", b: "p3-t2" }], newTableJudgements: [{ verdicts: [{ a: "p1-t2:r1c2", b: "p3-t2:r1c2", outcome: "mismatch", reason: "r", valuesDiffer: true }], rejected: { unknown_id: 1 }, rejectedCells: { a: [], b: [] } }], tableRejected: { already_paired: 1 } });

    it("새 짝이 견줌에 더해지고, 남은 칸은 답대로 갈리고, 합이 값 후보 수다", () => {
      const after = result.after.tally;
      expect(after.a).toMatchObject({ total: a.facts.length, compared: { match: 3, mismatch: 2, needsExplanation: 0 }, leftInPairedTable: 0, noCounterpart: { no_such_row_or_column: 1 }, undecided: { undecidable: 0, unanswered: 1, answerRejected: 0 }, leftInNewlyPairedTable: 1, tableNotPaired: 2 });
      expect(after.b).toMatchObject({ total: b.facts.length, compared: { match: 3, mismatch: 2, needsExplanation: 0 }, leftInPairedTable: 0, noCounterpart: {}, undecided: { undecidable: 1, unanswered: 0, answerRejected: 1 }, leftInNewlyPairedTable: 1, tableNotPaired: 0, answerRejected: 0 });
      for (const side of ["a", "b"] as const) expect(sumOf(after[side])).toBe(after[side].total);
      expect(result.after.outcome.states.a["p1-t1:r3c3"]).toBe("reviewed:unanswered");
      expect(result.after.outcome.states.a["p1-t1:r3c2"]).toBe("reviewed:no_counterpart:no_such_row_or_column");
      expect(result.after.outcome.states.a["p1-t2:r1c3"]).toBe("left:in_newly_paired_table");
      expect(result.after.outcome.states.b["p3-t1:r3c2"]).toBe("reviewed:answer_rejected");
      expect(result.after.outcome.states.b["p3-t1:r3c3"]).toBe("reviewed:undecidable");
      // 검수 전의 셈은 그대로다.
      expect(result.before.tally.a).toMatchObject({ compared: { match: 2, mismatch: 1 }, leftInPairedTable: 3, tableNotPaired: 4 });
    });

    it("새 짝은 출처와 함께 남고, 버려진 답은 단계별로 남는다", () => {
      expect(result.newPairs.map((pair) => `${pair.a}|${pair.b}:${pair.outcome}:${pair.source}`)).toEqual(["p1-t1:r2c3|p3-t1:r2c3:match:cell_review", "p1-t2:r1c2|p3-t2:r1c2:mismatch:table_review"]);
      expect(result.after.outcome.verdicts).toHaveLength(5);
      expect(result.rejected).toEqual({ cellReview: { not_in_remaining_list: 1, unknown_id: 1, outside_request_tables: 1 }, tableReview: { already_paired: 1 }, newTableCells: { unknown_id: 1 } });
      expect(result.after.outcome.rejected).toEqual({ unknown_id: 1, "cellReview:not_in_remaining_list": 1, "cellReview:unknown_id": 1, "cellReview:outside_request_tables": 1, "tableReview:already_paired": 1, "newTableCells:unknown_id": 1 });
      expect(result.remaining).toHaveLength(6);
      expect(result.remaining[0]).toEqual({ cell: "a:p1-t1:r2c3", otherTable: "b:p3-t1", request: "h/cells/p1-t1|p3-t1", answer: { state: "paired", counterpart: "b:p3-t1:r2c3" } });
    });

    it("한 칸이 여러 요청에서 물어졌으면, 전부 「상대 없음」일 때만 「상대 없음」이다", () => {
      const cellsOf = (state: Parameters<typeof judged.cells.set>[1]) => ({ plan: { ...plan, remaining: ["a:p1-t1:r3c2"] }, newPairs: [], cells: new Map([["a:p1-t1:r3c2", state]]), rejected: {} });
      const twice = (x: Parameters<typeof judged.cells.set>[1], y: Parameters<typeof judged.cells.set>[1]) => reviewOutcome({ a, b, base: base(), cellReviews: [cellsOf(x), cellsOf(y)], newTablePairs: [], newTableJudgements: [], tableRejected: {} }).after.outcome.states.a["p1-t1:r3c2"];
      expect(twice({ state: "no_counterpart", reason: "different_item" }, { state: "no_counterpart", reason: "no_such_row_or_column" })).toBe("reviewed:no_counterpart:different_item+no_such_row_or_column");
      expect(twice({ state: "no_counterpart", reason: "different_item" }, { state: "unanswered" })).toBe("reviewed:unanswered");
      expect(twice({ state: "no_counterpart", reason: "different_item" }, { state: "undecidable" })).toBe("reviewed:undecidable");
      expect(twice({ state: "answer_rejected", why: "unknown_id" }, { state: "undecidable" })).toBe("reviewed:answer_rejected");
    });

    it("실행들을 나란히 놓은 표: 검수 전·후, 되찾기, 셈, 새 짝과 「상대 없음」의 판정", () => {
      const gold = (id: string, cell: string): GoldPair => ({ id, item: id, period: "2024", a: `p1-t1:${cell}`, b: `p3-t1:${cell}`, rawA: "1", rawB: "1", origin: "added", inSeedTables: true });
      const pairs = [gold("g1", "r1c2"), gold("g2", "r2c3")];
      const graded: MethodGrade = { method: "h", title: "H", reportsUnchecked: true, reportsMatches: true, grade: grade(pairs, [], result.after.outcome), cost: null };
      const run: ReviewRun = { name: "D1", outcome: result.after.outcome, grade: graded, review: { tablePairs: result.tablePairs, newTablePairs: result.newTablePairs, newPairs: result.newPairs, remaining: result.remaining, rejected: result.rejected, tally: { before: result.before.tally, after: result.after.tally } }, before: { outcome: result.before.outcome, grade: grade(pairs, [], result.before.outcome) } };
      expect(recoveries(pairs, [], run)).toEqual([{ pair: "g2", a: "p1-t1:r2c3", b: "p3-t1:r2c3", plant: null, before: "silent", after: "match" }]);
      expect(newPairUnion(pairs, [run, run]).map((row) => `${row.a}:${row.inGold}:${row.runs.length}`)).toEqual(["p1-t1:r2c3:true:2", "p1-t2:r1c2:false:2"]);
      expect(noCounterpartUnion([run])).toEqual([{ cell: "a:p1-t1:r3c2", otherTable: "b:p3-t1", answers: ["상대 없음 (상대 표에 없는 행·열)"] }]);
      const md = renderReview(
        [run],
        pairs,
        [],
        { note: "n", status: "s", judgments: [{ a: "p1-t2:r1c2", b: "p3-t2:r1c2", verdict: "not_same", evidence: { a: { page: 1, row: "용수", column: "2023" }, b: { page: 1, row: "전력", column: "2023" } }, note: null }] },
        { note: "n", status: "s", judgments: [{ cell: "a:p1-t1:r3c2", other_table: "b:p3-t1", verdict: "no_counterpart", counterpart: null, evidence: { page: 1, row: "감축률", column: "2023" }, note: null }] },
      );
      expect(md).toContain("| 항목 | D1 전 | D1 후 |");
      expect(md).toContain("| 1. 참 일치 (오류를 심지 않은 정답 쌍 중 일치) | 1/2 | 2/2 |");
      expect(md).toContain("| D1 | 1/2 | 1/1 | p1-t1:r2c3 ↔ p3-t1:r2c3: 조용히 지나감 → 일치 |");
      expect(md).toContain(`| 합 (= 값 후보 수) | ${a.facts.length}/${a.facts.length} | ${b.facts.length}/${b.facts.length} | ${a.facts.length}/${a.facts.length} | ${b.facts.length}/${b.facts.length} |`);
      expect(md).toContain("| 짝지어진 표 안의 남은 칸 (검수 전) | 3 | 3 | 0 | 0 |");
      expect(md).toContain("| D1 | 1 | 6 | 2/6 | 1/6 | 1/6 | 1/6 | 1/6 |");
      expect(md).toContain("| p1-t1 ↔ p3-t1 | 끌어냄 (3) |");
      expect(md).toContain("| p1-t2 ↔ p3-t2 | 표 검수가 새로 냄 |");
      expect(md).toContain("| D1 | 2 | 1/2 | 0/2 | 1/2 | 0/2 | 0/2 | 0/2 | 0/2 / 1/2 |");
      expect(md).toContain("합집합 1건. 판정: 정말 상대 없음 1/1, 상대 있음(틀린 「상대 없음」) 0/1");
    });
  });
});

describe("방법 H: 표 검수", () => {
  const text = renderTableReviewRequest(a, b, [PAIR]);

  it("표 목록에 이미 짝지어진 표와 그 상대를 표시한다. 숫자 칸은 싣지 않고 글 속의 값 후보는 가린다", () => {
    expect(text).toContain("[표 a:p1-t1] 1쪽 · 이미 짝지어짐: b:p3-t1");
    expect(text).toContain("[표 b:p3-t1] 1쪽 · 이미 짝지어짐: a:p1-t1");
    expect(text.split("\n").filter((line) => line.includes("이미 짝지어짐"))).toHaveLength(2);
    expect(text.split("\n")).toContain("[표 a:p1-t2] 1쪽");
    expect(text.split("\n")).toContain("[표 b:p3-t2] 1쪽");
    for (const value of VALUES) expect(text).not.toContain(value);
    expect(text).toContain(`목표 ${MASK} tCO2eq 이내`);
    expect(unmaskedInTableReview(text)).toEqual([]);
    expect(unmaskedInTableReview(`${text}\n제목·각주: 목표 91,350 tCO2eq 이내 / 다른 글`)).toEqual(["91,350"]);
    // 한 표가 여러 표와 짝이면 상대를 다 적는다.
    expect(renderTableReviewRequest(a, b, [PAIR, { a: "p2-t1", b: "p3-t1" }])).toContain("[표 b:p3-t1] 1쪽 · 이미 짝지어짐: a:p1-t1, a:p2-t1");
  });

  it("새 표 짝: 없는 표, 이미 끌어낸 짝, 양쪽이 다 이미 짝지어진 표인 짝은 버린다", () => {
    const tablesA = new Set(["p1-t1", "p1-t2", "p2-t1"]);
    const tablesB = new Set(["p3-t1", "p3-t2"]);
    const existing = [PAIR, { a: "p2-t1", b: "p3-t2" }];
    const checked = validateNewTablePairs({ table_pairs: [{ a_table: "a:p1-t2", b_table: "b:p3-t2" }, { a_table: "a:p1-t1", b_table: "b:p3-t1" }, { a_table: "a:p1-t1", b_table: "b:p3-t2" }, { a_table: "a:p9-t9", b_table: "b:p3-t2" }, { a_table: "a:p1-t2", b_table: "b:p3-t2" }] }, tablesA, tablesB, existing);
    expect(checked.pairs).toEqual([{ a: "p1-t2", b: "p3-t2" }]);
    expect(checked.rejected).toEqual({ unknown_id: 1, duplicate_pair: 1, already_paired: 1, both_tables_already_paired: 1 });
  });
});

describe("방법 H 의 명령 (모의 공급사, synthetic)", () => {
  const dir = mkdtempSync(join(tmpdir(), "reconcile-review-"));
  const file = (name: string, value: unknown): string => {
    const path = join(dir, name);
    writeFileSync(path, typeof value === "string" ? value : JSON.stringify(value));
    return path;
  };
  const docA = file("a.json", asDocumentJson(documentA()));
  const docB = file("b.json", asDocumentJson(documentB()));
  const pairs = file("pairs.json", { note: "synthetic", status: "synthetic", docs: { a: { file: "a", sha256: "0" }, b: { file: "b", sha256: "0" } }, pairs: [{ id: "g1", item: "Scope 1", period: "2023", a: "p1-t1:r1c2", b: "p3-t1:r1c2", rawA: "48,217", rawB: "48,217", origin: "added", inSeedTables: true }] });
  const plants = file("plants.json", { note: "synthetic", status: "synthetic", seed: 1, plan: [], plants: [] });
  const dRun = join(dir, "d");
  mkdirSync(dRun);
  writeFileSync(join(dRun, "units.jsonl"), `${JSON.stringify({ unitId: "d/1", result: { kind: "success", data: D_ANSWER } })}\n`);
  writeFileSync(join(dRun, "outcome.json"), JSON.stringify({ method: "d", outcome: base().outcome }));
  const common = ["--method", "h", "--a", docA, "--b", docB, "--exam", docB, "--pairs", pairs, "--plants", plants];
  const run = async (args: string[]): Promise<{ code: number; text: string }> => {
    const lines: string[] = [];
    const code = await main(args, (line) => lines.push(line), () => undefined, { env: {} });
    return { code, text: lines.join("\n") };
  };

  it("dry-run 은 부르지 않고 요청 글을 쓴다. 요청 글에 값 후보 숫자가 없다", async () => {
    const out = join(dir, "dry");
    const result = await run(["run", ...common, "--d-run", dRun, "--dry-run", "--out", out]);
    expect(result.code).toBe(0);
    const names = readdirSync(join(out, "requests")).sort();
    expect(names).toEqual(["cells__p1-t1__p3-t1.txt", "tables.txt"]);
    for (const name of names) for (const value of VALUES) expect(readFileSync(join(out, "requests", name), "utf8")).not.toContain(value);
    const report = JSON.parse(readFileSync(join(out, "dry-run.json"), "utf8")) as { calls: number; notes: { maskCheck: { unmaskedValues: number }; cellRequests: unknown[] } };
    expect(report.notes.cellRequests).toEqual([{ id: "h/cells/p1-t1|p3-t1", remaining: 6, paired: 3 }]);
    expect(report.notes.maskCheck.unmaskedValues).toBe(0);
    expect(existsSync(join(out, "outcome.json"))).toBe(false);
    // d 의 저장된 답은 읽기만 한다.
    expect(readdirSync(dRun).sort()).toEqual(["outcome.json", "units.jsonl"]);
  });

  it("모의 실행: 빈 답이면 남은 칸은 전부 미응답이고, 결과에 synthetic 표시가 붙는다. 표는 모의 결과를 받지 않는다", async () => {
    const out = join(dir, "mock");
    const result = await run(["run", ...common, "--d-run", dRun, "--provider", "mock", "--out", out]);
    expect(result.code).toBe(0);
    const saved = JSON.parse(readFileSync(join(out, "outcome.json"), "utf8")) as { synthetic?: string; method: string; notes: { review: { tally: { before: { a: { leftInPairedTable: number } }; after: { a: { undecided: { unanswered: number }; noCounterpart: Record<string, number> } } }; newPairs: unknown[] } } };
    expect(saved.synthetic).toBeTruthy();
    expect(saved.notes.review.tally.before.a.leftInPairedTable).toBe(3);
    expect(saved.notes.review.tally.after.a.undecided.unanswered).toBe(3);
    expect(saved.notes.review.tally.after.a.noCounterpart).toEqual({});
    expect(saved.notes.review.newPairs).toEqual([]);
    expect((await run(["review-table", "--review", `M=${out}`, "--pairs", pairs, "--plants", plants, "--out", join(dir, "table")])).code).toBe(1);
  });

  it("d 의 실행을 주지 않거나, 저장된 답이 그 실행의 결과와 다르면 멈춘다", async () => {
    expect((await run(["run", ...common, "--dry-run", "--out", join(dir, "x1")])).code).toBe(1);
    const other = join(dir, "d-other");
    mkdirSync(other);
    writeFileSync(join(other, "units.jsonl"), `${JSON.stringify({ unitId: "d/1", result: { kind: "success", data: { pairs: D_ANSWER.pairs.slice(1) } } })}\n`);
    writeFileSync(join(other, "outcome.json"), JSON.stringify({ method: "d", outcome: base().outcome }));
    const result = await run(["run", ...common, "--d-run", other, "--dry-run", "--out", join(dir, "x2")]);
    expect(result.code).toBe(1);
    expect(result.text).toContain("outcome.json 과 다르다");
  });
});
