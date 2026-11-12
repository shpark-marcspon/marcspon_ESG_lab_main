import { mkdirSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { extractDocument, type Extraction } from "../src/index.js";
import { main as main7 } from "../src/compare/cli7.js";
import { main as main8 } from "../src/compare/cli8.js";
import type { GoldPair } from "../src/compare/exam.js";
import { TallyError, judgeTableCells, renderCellPairRequest, unmaskedValues } from "../src/compare/hybrid.js";
import { pageTexts, type DocContext, type Verdict } from "../src/compare/methods.js";
import { judgeCellReview2, judgePlacePairs } from "../src/compare/prose.js";
import { acceptNewTablePairs, checkShifts, classifyRecovered, expectedByHeader, lineKey, mergeRecovered, nominate, planCellRequests, recoverOutcome, renderShortTableReview, suspectKeys, type RecoveredPair } from "../src/compare/recover.js";
import { reportRun, scoreShift, scoreStage, sheetOf, toJudge, unionVerdicts, type RecoverAnswers } from "../src/compare/recover-report.js";
import { MASK } from "../src/compare/render.js";
import { renderCellReviewRequest, unmaskedInCellReview, unmaskedInTableReview } from "../src/compare/review.js";
import { asDocumentJson, syntheticDocument, units } from "./helpers.js";

// 방법 R (열 밀림 검사, 지목, 다시 묻기, 합치기)을 지어낸 문서와 답으로 본다 (synthetic). 어느 회사의 실제 값도 아니다.

const H2 = { rows: [0], cols: [0, 1], basis: "rows_before_first_numeric_row" };
const NONE = { rows: [], cols: [], basis: "rows_before_first_numeric_row" };
// 문서 a: 1쪽에 배출량 표(세 해)와 국가별 표, 2쪽에 폐기물 표와 머리글 없는 표. 문서 b: 같은 배출량 표, 자산 표(「합계」 말고 「국채」 행이 있다), 같은 폐기물 표, 머리글 없는 표.
const documentA = () =>
  syntheticDocument(
    "a",
    [
      { id: "p1-t1", page: 1, header: H2, before: ["배출량 현황 (목표 91,350 tCO2eq 이내)"], cells: [["구분", "단위", "2023", "2024", "2025"], ["Scope 1", "tCO2eq", "48,217", "39,604", "41,010"], ["Scope 2", "tCO2eq", "12,345", "11,871", "10,202"], ["합계", "tCO2eq", "60,562", "51,475", "51,212"]] },
      { id: "p1-t2", page: 1, header: H2, before: ["국가별 현황"], cells: [["국가", "단위", "2024"], ["가나", "톤", "7,731"], ["다라", "톤", "7,912"], ["합계", "톤", "15,643"]] },
      { id: "p2-t1", page: 2, header: H2, before: ["폐기물"], cells: [["구분", "단위", "2023", "2024"], ["일반 폐기물", "톤", "615", "642"], ["지정 폐기물", "톤", "37", "41"], ["재활용", "톤", "2,210", "2,305"]] },
      { id: "p2-t2", page: 2, header: NONE, cells: [["1,204", "1,251"]] },
    ],
    [{ id: "a-body", page: 1, text: "2024년 배출량은 51,475 tCO2eq 이며 전년 대비 9,087 tCO2eq 줄었다" }],
  );
const documentB = () =>
  syntheticDocument(
    "b",
    [
      { id: "p3-t1", page: 1, header: H2, before: ["온실가스 (오차 4.75% 이내)"], cells: [["지표", "단위", "2023", "2024", "2025"], ["Scope 1", "tCO2eq", "48,217", "39,640", "41,010"], ["Scope 2", "tCO2eq", "12,345", "11,871", "10,202"], ["합계1)", "tCO2eq", "60,562", "51,475", "51,212"]] },
      { id: "p3-t2", page: 1, header: H2, cells: [["자산", "단위", "2024"], ["국채", "톤", "15,640"], ["회사채", "톤", "9,001"], ["합계", "톤", "24,641"]] },
      { id: "p4-t1", page: 2, header: H2, before: ["폐기물 처리"], cells: [["구분", "단위", "2023", "2024"], ["일반 폐기물", "톤", "615", "642"], ["지정 폐기물", "톤", "37", "41"], ["재활용", "톤", "2,210", "2,350"]] },
      { id: "p4-t2", page: 2, header: NONE, cells: [["1,204", "1,251"]] },
    ],
    [{ id: "b-body", page: 1, text: "검증 대상 사업장 37개" }],
  );
const VALUES = ["48,217", "39,604", "39,640", "41,010", "12,345", "11,871", "10,202", "60,562", "51,475", "51,212", "7,731", "7,912", "15,643", "15,640", "9,001", "24,641", "615", "642", "2,210", "2,305", "2,350", "1,204", "1,251", "91,350", "9,087", "4.75"];
const own = (document: ReturnType<typeof documentA>, id: string): Extraction => extractDocument(document, id, { titles: "own" });
const context = (extraction: Extraction): DocContext => ({ extraction, pages: pageTexts(extraction, new Map(), new Map()) });
const a = own(documentA(), "a");
const b = own(documentB(), "b");
const sideA = { document: documentA(), extraction: a };
const sideB = { document: documentB(), extraction: b };
const pair = (x: string, y: string) => ({ a: `a:${x}`, b: `b:${y}`, unit_a: null, unit_b: null });
// 한 실행의 원답: 배출량 표의 Scope 1·2 행은 열을 둘씩 밀려 적었다 (2023 ↔ 2025 는 있는 칸이라 받아들여지고, 2024·2025 ↔ 없는 열은 버려진다).
// 합계 행의 2023 은 맞게, 국가별 「합계」 ↔ 자산 표의 「국채」, 머리글 없는 표의 칸, 서술 속 숫자 ↔ 표 칸도 하나씩. 폐기물 표는 통째로 안 적었다.
const SHIFTED = {
  pairs: [
    pair("p1-t1:r1c2", "p3-t1:r1c4"),
    pair("p1-t1:r1c3", "p3-t1:r1c5"),
    pair("p1-t1:r1c4", "p3-t1:r1c6"),
    pair("p1-t1:r2c2", "p3-t1:r2c4"),
    pair("p1-t1:r2c3", "p3-t1:r2c5"),
    pair("p1-t1:r3c2", "p3-t1:r3c2"),
    pair("p1-t2:r3c2", "p3-t2:r1c2"),
    pair("p2-t2:r0c0", "p4-t2:r0c0"),
    pair("a-body#n2", "p3-t1:r3c3"),
    pair("a-body#n9", "b-body#n9"),
  ],
};
const judged = (answer = SHIFTED) => judgePlacePairs([answer], context(a), context(b), units);
const checksOf = (verdicts: Verdict[], rule: "either" | "both" = "both") => checkShifts(verdicts, a, b, units, rule);
const verdictOf = (x: string, y: string, outcome: Verdict["outcome"] = "match"): Verdict => ({ a: x, b: y, outcome, reason: "exact", valuesDiffer: outcome !== "match" });

describe("방법 R: 열 밀림 검사", () => {
  const base = judged();
  const byKey = (rule: "either" | "both") => new Map(checksOf(base.verdicts, rule).map((check) => [`${check.a}|${check.b}`, check]));

  it("머리글의 열쇠는 단위와 각주 표시를 빼고 잇는다", () => {
    const fact = (id: string) => b.facts.find((item) => item.id === id);
    expect(lineKey(fact("p3-t1:r3c2")?.headers?.row, units)).toBe("합계");
    expect(lineKey(fact("p3-t1:r3c2")?.headers?.col, units)).toBe("2023");
    expect(lineKey(fact("p4-t2:r0c0")?.headers?.col, units)).toBe("");
  });

  it("정상: 열 머리글과 행 머리글이 다 같으면 정상이다", () => {
    expect(byKey("both").get("p1-t1:r3c2|p3-t1:r3c2")).toMatchObject({ col: "same", row: "same", verdict: "ok", noHeader: false });
  });

  it("한 칸 밀림: 열 머리글이 다르고 서로의 머리글이 상대 표의 다른 열에 있으면 의심이다", () => {
    for (const rule of ["either", "both"] as const) {
      expect(byKey(rule).get("p1-t1:r1c2|p3-t1:r1c4")).toMatchObject({ col: "shifted", row: "same", verdict: "suspect" });
      expect(byKey(rule).get("p1-t1:r2c2|p3-t1:r2c4")).toMatchObject({ col: "shifted", verdict: "suspect" });
    }
  });

  it("머리글 없음: 머리글이 서지 않은 표의 짝은 판단하지 않는다 (의심도 정상도 아니다)", () => {
    expect(byKey("both").get("p2-t2:r0c0|p4-t2:r0c0")).toMatchObject({ col: "unknown", row: "unknown", verdict: "undecided", noHeader: true });
  });

  it("한 표 짝에 정상과 밀림이 섞이면 밀린 짝만 가린다", () => {
    const inPair = checksOf(base.verdicts).filter((check) => check.a.startsWith("p1-t1:") && check.b.startsWith("p3-t1:"));
    expect(inPair.map((check) => check.verdict).sort()).toEqual(["ok", "suspect", "suspect"]);
    expect([...suspectKeys(checksOf(base.verdicts))].sort()).toEqual(["p1-t1:r1c2|p3-t1:r1c4", "p1-t1:r2c2|p3-t1:r2c4"]);
  });

  it("「합계」 ↔ 이름 있는 행: 한쪽의 머리글만 상대 표의 다른 행에 있으면 either 는 가리고 both 는 판단하지 않는다", () => {
    expect(byKey("either").get("p1-t2:r3c2|p3-t2:r1c2")).toMatchObject({ row: "shifted", verdict: "suspect" });
    expect(byKey("both").get("p1-t2:r3c2|p3-t2:r1c2")).toMatchObject({ col: "same", row: "unknown", verdict: "undecided", noHeader: false });
  });

  it("서술이 낀 짝은 검사 대상이 아니다", () => {
    expect(base.verdicts.some((verdict) => verdict.a === "a-body#n2")).toBe(true);
    expect(checksOf(base.verdicts).some((check) => check.a === "a-body#n2")).toBe(false);
    expect(checksOf(base.verdicts)).toHaveLength(5);
  });
});

describe("방법 R: 지목", () => {
  const base = judged();
  const nominated = (answer = SHIFTED, rule: "either" | "both" = "both") => {
    const result = judged(answer);
    return nominate({ a, b, answers: [answer], verdicts: result.verdicts, checks: checksOf(result.verdicts, rule), units });
  };

  it("(i) 버려진 답이 가리키는 표 짝: 없는 칸의 ID 에서 표 ID 를 읽는다. 글 속 자리의 ID 는 표 짝을 가리키지 않는다", () => {
    expect(base.rejected).toEqual({ unknown_id: 4 });
    const only = nominated({ pairs: [pair("p1-t1:r3c2", "p3-t1:r3c2"), pair("p1-t1:r3c3", "p3-t1:r3c3"), pair("p1-t1:r3c4", "p3-t1:r3c4"), pair("p1-t1:r1c2", "p3-t1:r1c2"), pair("p1-t1:r1c3", "p3-t1:r1c3"), pair("p1-t1:r1c4", "p3-t1:r1c4"), pair("p1-t1:r2c2", "p3-t1:r2c2"), pair("p1-t1:r2c3", "p3-t1:r2c3"), pair("p1-t1:r2c4", "p3-t1:r2c4"), pair("p1-t2:r9c9", "p3-t2:r1c2"), pair("a-body#n9", "b-body#n9"), pair("p9-t9:r1c1", "p3-t2:r1c2")] });
    expect(only.nominations).toEqual([{ pair: { a: "p1-t2", b: "p3-t2" }, reasons: ["rejected_answer"], rejectedAnswers: 1, suspects: 0, expectedByHeader: 0 }]);
  });

  it("(ii) 의심이 나온 표 짝과 (iii) 머리글로 보면 상대가 있는 남은 칸이 있는 표 짝", () => {
    const result = nominated();
    const main = result.nominations.find((item) => item.pair.a === "p1-t1");
    // 버려진 답 3 (없는 열을 가리킨 것), 의심 2. 의심을 뺀 뒤 견주어지지 않은 칸 가운데 머리글이 같은 것: Scope 1·2 의 세 해와 합계의 2025 (2024 는 서술과 견주어졌다).
    expect(main).toEqual({ pair: { a: "p1-t1", b: "p3-t1" }, reasons: ["rejected_answer", "shift_suspect", "expected_by_header"], rejectedAnswers: 3, suspects: 2, expectedByHeader: 7 });
    expect(result.nominations.map((item) => item.pair.a)).toEqual(["p1-t1"]);
  });

  it("(iii) 은 머리글이 같은 남은 칸이 없으면 지목하지 않는다", () => {
    const kept = base.verdicts.filter((verdict) => !suspectKeys(checksOf(base.verdicts)).has(`${verdict.a}|${verdict.b}`));
    const compared = { a: new Set(kept.map((verdict) => verdict.a)), b: new Set(kept.map((verdict) => verdict.b)) };
    expect(expectedByHeader({ a: "p1-t2", b: "p3-t2" }, a, b, compared, units)).toEqual([]);
    expect(expectedByHeader({ a: "p1-t1", b: "p3-t1" }, a, b, compared, units)).toHaveLength(7);
  });

  it("(iv) 어느 짝에도 들지 않은 표: 행 머리글 셋 이상과 열 머리글 하나 이상이 같은 표 짝만 후보다", () => {
    const result = nominated();
    expect(result.unpaired).toEqual({ a: ["p2-t1"], b: ["p4-t1"] });
    expect(result.candidates).toEqual([{ a: "p2-t1", b: "p4-t1", sharedRows: 3, sharedCols: 2 }]);
  });

  it("either 기준이면 「합계」 ↔ 「국채」 짝의 표 짝도 지목된다", () => {
    expect(nominated(SHIFTED, "either").nominations.map((item) => `${item.pair.a}|${item.pair.b}:${item.reasons.join("+")}`)).toEqual(["p1-t1|p3-t1:rejected_answer+shift_suspect+expected_by_header", "p1-t2|p3-t2:shift_suspect+expected_by_header"]);
  });
});

describe("방법 R: 요청 계획과 가림", () => {
  const base = judged();
  const suspects = suspectKeys(checksOf(base.verdicts));
  const kept = base.verdicts.filter((verdict) => !suspects.has(`${verdict.a}|${verdict.b}`));

  it("남은 짝이 있는 표 짝은 칸 검수, 남은 짝이 없는 표 짝은 칸 짝 요청, 물을 칸이 없으면 건너뛴다", () => {
    const full = [...kept, verdictOf("p1-t2:r1c2", "p3-t2:r2c2"), verdictOf("p1-t2:r2c2", "p3-t2:r3c2")];
    const planned = planCellRequests(a, b, [{ pair: { a: "p1-t1", b: "p3-t1" }, reasons: ["shift_suspect"] }, { pair: { a: "p2-t1", b: "p4-t1" }, reasons: ["new_table_pair"] }, { pair: { a: "p1-t2", b: "p3-t2" }, reasons: ["rejected_answer"] }], full);
    expect(planned.plans.map((plan) => [plan.id, plan.kind, plan.remaining])).toEqual([
      ["r/cells/p1-t1|p3-t1", "review", { a: 8, b: 7 }],
      ["r/pair/p2-t1|p4-t1", "pair", { a: 6, b: 6 }],
    ]);
    expect(planned.skipped).toEqual([{ pair: { a: "p1-t2", b: "p3-t2" }, reasons: ["rejected_answer"], why: "nothing_left" }]);
    const review = planned.plans[0]?.review;
    expect(review?.paired).toEqual([{ a: "p1-t1:r3c2", b: "p3-t1:r3c2" }]);
    // 의심으로 뺀 짝의 칸은 남은 칸으로 다시 묻는다. 서술과 견주어진 칸(합계 2024)은 묻지 않는다.
    expect(review?.remaining).toContain("a:p1-t1:r1c2");
    expect(review?.remaining).toContain("b:p3-t1:r1c4");
    expect(review?.remaining).not.toContain("b:p3-t1:r3c3");
    expect(review?.remaining).toHaveLength(15);
  });

  it("남은 칸이 많으면 목록을 나눠 요청을 여럿 만든다", () => {
    const planned = planCellRequests(a, b, [{ pair: { a: "p1-t1", b: "p3-t1" }, reasons: ["shift_suspect"] }], kept, 6);
    expect(planned.plans.map((plan) => [plan.id, plan.review?.remaining.length])).toEqual([["r/cells/p1-t1|p3-t1#1", 6], ["r/cells/p1-t1|p3-t1#2", 6], ["r/cells/p1-t1|p3-t1#3", 3]]);
  });

  it("요청의 가림: 칸 검수, 칸 짝, 표 검수의 요청 글에 값 후보 숫자가 남지 않는다", () => {
    const planned = planCellRequests(a, b, [{ pair: { a: "p1-t1", b: "p3-t1" }, reasons: ["shift_suspect"] }, { pair: { a: "p2-t1", b: "p4-t1" }, reasons: ["new_table_pair"] }], kept);
    const review = renderCellReviewRequest(sideA, sideB, planned.plans[0]?.review as NonNullable<(typeof planned.plans)[number]["review"]>);
    const pairText = renderCellPairRequest(sideA, sideB, { a: "p2-t1", b: "p4-t1" });
    const tables = renderShortTableReview(a, b, [{ a: "p2-t1", b: "p4-t1", sharedRows: 3, sharedCols: 2 }], [{ a: "p1-t1", b: "p3-t1" }]) as string;
    expect(unmaskedInCellReview(review)).toEqual([]);
    expect(unmaskedValues(pairText)).toEqual([]);
    expect(unmaskedInTableReview(tables)).toEqual([]);
    for (const text of [review, pairText, tables]) for (const value of VALUES) expect(text).not.toContain(value);
    expect(review).toContain(MASK);
    expect(pairText).toContain(`c2: ${MASK}`);
  });

  it("표 검수의 글에는 후보 표만 실린다. 후보가 없으면 요청을 만들지 않는다", () => {
    const tables = renderShortTableReview(a, b, [{ a: "p2-t1", b: "p3-t1", sharedRows: 3, sharedCols: 2 }], [{ a: "p1-t1", b: "p3-t1" }]) as string;
    expect(tables.split("\n").filter((line) => line.startsWith("[표 "))).toEqual(["[표 a:p2-t1] 2쪽", "[표 b:p3-t1] 1쪽 · 이미 짝지어짐: a:p1-t1"]);
    expect(tables).toContain("행 머리글: 일반 폐기물 > 톤 ; 지정 폐기물 > 톤 ; 재활용 > 톤");
    expect(renderShortTableReview(a, b, [], [])).toBeNull();
  });

  it("표 검수의 답: 목록 밖의 표, 이미 있는 표 짝, 양쪽이 다 짝지어진 표는 버리고, 한도를 넘으면 머리글이 많이 겹치는 것부터 남긴다", () => {
    const candidates = [{ a: "p2-t1", b: "p4-t1", sharedRows: 3, sharedCols: 2 }, { a: "p2-t1", b: "p3-t1", sharedRows: 3, sharedCols: 1 }, { a: "p1-t1", b: "p3-t1", sharedRows: 3, sharedCols: 1 }];
    const answer = { table_pairs: [{ a_table: "a:p2-t1", b_table: "b:p3-t1" }, { a_table: "a:p2-t1", b_table: "b:p4-t1" }, { a_table: "a:p9-t9", b_table: "b:p4-t1" }, { a_table: "a:p1-t1", b_table: "b:p3-t1" }, { a_table: "a:p1-t1", b_table: "b:p3-t1" }] };
    const existing = [{ a: "p1-t1", b: "p3-t2" }, { a: "p1-t2", b: "p3-t1" }];
    const unpaired = { a: ["p2-t1"], b: ["p4-t1"] };
    expect(acceptNewTablePairs(answer, candidates, existing, unpaired)).toEqual({ pairs: [{ a: "p2-t1", b: "p4-t1" }, { a: "p2-t1", b: "p3-t1" }], rejected: { unknown_id: 1, duplicate_pair: 1, both_tables_already_paired: 1 } });
    expect(acceptNewTablePairs(answer, candidates, existing, unpaired, 1)).toEqual({ pairs: [{ a: "p2-t1", b: "p4-t1" }], rejected: { unknown_id: 1, duplicate_pair: 1, both_tables_already_paired: 1, over_limit: 1 } });
    expect(acceptNewTablePairs({ table_pairs: [{ a_table: "a:p1-t1", b_table: "b:p3-t2" }] }, candidates, existing, unpaired).rejected).toEqual({ unknown_id: 1 });
    expect(acceptNewTablePairs(null, candidates, existing, unpaired)).toEqual({ pairs: [], rejected: {} });
  });
});

describe("방법 R: 합치는 규칙", () => {
  const tables = { a: "p1-t1", b: "p3-t1" };
  const recovered = (x: string, y: string, outcome: Verdict["outcome"] = "match"): RecoveredPair => ({ ...verdictOf(x, y, outcome), request: "r/cells/p1-t1|p3-t1", tables });

  it("같음: 다시 받은 짝이 원래 답에 있으면 한 번만 남긴다", () => {
    const merged = mergeRecovered([verdictOf("p1-t1:r3c2", "p3-t1:r3c2")], [recovered("p1-t1:r3c2", "p3-t1:r3c2"), recovered("p1-t1:r3c4", "p3-t1:r3c4")], new Set());
    expect(merged.verdicts.map((verdict) => `${verdict.a}|${verdict.b}`)).toEqual(["p1-t1:r3c2|p3-t1:r3c2", "p1-t1:r3c4|p3-t1:r3c4"]);
    expect(merged.repeated).toBe(1);
    expect(merged.added.map((item) => item.a)).toEqual(["p1-t1:r3c4"]);
    expect(merged.conflicts).toEqual([]);
  });

  it("다름: 원래 짝과 다시 받은 짝이 한 칸을 서로 다른 상대와 짝지었으면 둘 다 남기고 엇갈림으로 센다", () => {
    const merged = mergeRecovered([verdictOf("p1-t1:r1c3", "p3-t1:r2c3", "mismatch")], [recovered("p1-t1:r1c3", "p3-t1:r1c3", "mismatch")], new Set());
    expect(merged.verdicts).toHaveLength(2);
    expect(merged.conflicts).toEqual([{ cell: "a:p1-t1:r1c3", pairs: ["p1-t1:r1c3|p3-t1:r2c3", "p1-t1:r1c3|p3-t1:r1c3"] }]);
  });

  it("원래 것이 의심: 의심으로 걸린 짝은 원래 것이든 다시 받은 것이든 견준 결과에 넣지 않는다", () => {
    const suspects = new Set(["p1-t1:r1c2|p3-t1:r1c4", "p1-t1:r2c3|p3-t1:r2c5"]);
    const merged = mergeRecovered([verdictOf("p1-t1:r1c2", "p3-t1:r1c4", "mismatch"), verdictOf("p1-t1:r3c2", "p3-t1:r3c2")], [recovered("p1-t1:r1c2", "p3-t1:r1c4", "mismatch"), recovered("p1-t1:r2c3", "p3-t1:r2c5"), recovered("p1-t1:r1c2", "p3-t1:r1c2")], suspects);
    expect(merged.verdicts.map((verdict) => `${verdict.a}|${verdict.b}`)).toEqual(["p1-t1:r3c2|p3-t1:r3c2", "p1-t1:r1c2|p3-t1:r1c2"]);
    expect(merged.dropped.map((item) => [item.a, item.b, item.from])).toEqual([["p1-t1:r1c2", "p3-t1:r1c4", "original"], ["p1-t1:r2c3", "p3-t1:r2c5", "recovered"]]);
    expect(merged.repeated).toBe(1);
  });
});

describe("방법 R: 결과와 셈", () => {
  const base = judged();
  const nomination = nominate({ a, b, answers: [SHIFTED], verdicts: base.verdicts, checks: checksOf(base.verdicts), units });
  const suspects = suspectKeys(checksOf(base.verdicts));
  const kept = base.verdicts.filter((verdict) => !suspects.has(`${verdict.a}|${verdict.b}`));
  const planned = planCellRequests(a, b, [...nomination.nominations.map((item) => ({ pair: item.pair, reasons: item.reasons })), { pair: { a: "p2-t1", b: "p4-t1" }, reasons: ["new_table_pair" as const] }], kept);
  const reviewPlan = planned.plans[0]?.review as NonNullable<(typeof planned.plans)[number]["review"]>;
  const answer = (cell: string, counterpart: string | null, reason: "no_such_row_or_column" | null = null) => ({ cell, counterpart, reason, unit: null, counterpart_unit: null });
  // 칸 검수의 답: Scope 1 의 세 해를 맞게, Scope 2 의 2024 는 밀려서(2025 열과), 합계 2025 는 「상대 없음」, 나머지는 답 없음.
  const reviewAnswer = { answers: [answer("a:p1-t1:r1c2", "b:p3-t1:r1c2"), answer("a:p1-t1:r1c3", "b:p3-t1:r1c3"), answer("a:p1-t1:r1c4", "b:p3-t1:r1c4"), answer("a:p1-t1:r2c3", "b:p3-t1:r2c4"), answer("a:p1-t1:r3c4", null, "no_such_row_or_column")] };
  const reviews = new Map([[planned.plans[0]?.id as string, judgeCellReview2(reviewAnswer, reviewPlan, context(a), context(b), units)]]);
  const pairs = new Map([["r/pair/p2-t1|p4-t1", judgeTableCells({ pairs: [{ a: "p2-t1:r1c2", b: "p4-t1:r1c2", unit_a: null, unit_b: null }, { a: "p2-t1:r3c3", b: "p4-t1:r3c3", unit_a: null, unit_b: null }] }, { a: "p2-t1", b: "p4-t1" }, context(a), context(b), units)]]);
  const result = recoverOutcome({ a, b, units, original: { verdicts: base.verdicts, rejectedPlaces: base.rejectedPlaces }, plans: planned.plans, rule: "both", reviews, pairs });
  const total = (tally: { states: Record<string, number> }): number => Object.values(tally.states).reduce((x, y) => x + y, 0);

  it("셈: 원래 답, 검사만, 다시 물은 뒤 모두 합이 값 후보 수와 같다", () => {
    for (const stage of [result.before, result.checked, result.after]) {
      expect(total(stage.tally.a)).toBe(a.facts.length);
      expect(total(stage.tally.b)).toBe(b.facts.length);
      expect(stage.tally.a.total).toBe(a.facts.length);
    }
  });

  it("검사만: 의심 짝이 견준 결과에서 빠지고 그 칸은 「의심」으로 남는다", () => {
    expect(result.before.verdicts).toHaveLength(base.verdicts.length);
    expect(result.checked.verdicts).toHaveLength(base.verdicts.length - 2);
    expect(result.checked.dropped.map((item) => `${item.a}|${item.b}`)).toEqual(["p1-t1:r1c2|p3-t1:r1c4", "p1-t1:r2c2|p3-t1:r2c4"]);
    expect(result.checked.tally.a.states["suspect:shifted"]).toBe(2);
    expect(result.before.tally.a.states["suspect:shifted"]).toBeUndefined();
  });

  it("다시 물은 뒤: 새 짝이 더해지고, 또 밀린 새 짝은 빠지고, 남은 칸은 답대로 갈린다", () => {
    expect(result.merge.added.map((item) => `${item.a}|${item.b}`)).toEqual(["p1-t1:r1c2|p3-t1:r1c2", "p1-t1:r1c3|p3-t1:r1c3", "p1-t1:r1c4|p3-t1:r1c4", "p2-t1:r1c2|p4-t1:r1c2", "p2-t1:r3c3|p4-t1:r3c3"]);
    expect(result.merge.dropped.filter((item) => item.from === "recovered").map((item) => `${item.a}|${item.b}`)).toEqual(["p1-t1:r2c3|p3-t1:r2c4"]);
    const states = result.after.states.a;
    expect(states.get("p1-t1:r1c3")).toBe("compared:mismatch");
    expect(states.get("p1-t1:r3c4")).toBe("asked:no_counterpart:no_such_row_or_column");
    expect(states.get("p1-t1:r2c2")).toBe("asked:unanswered");
    expect(states.get("p1-t1:r2c3")).toBe("suspect:shifted");
    expect(states.get("p2-t1:r2c2")).toBe("asked:not_paired");
    expect(states.get("p2-t1:r3c3")).toBe("compared:mismatch");
    expect(states.get("p1-t2:r1c2")).toBe("left:in_paired_table");
    expect([...states].filter(([id]) => id.startsWith("a-body#")).map(([, state]) => state).sort()).toEqual(["compared:needs_explanation", "unverified:outside_tables:sentence"]);
  });

  it("다시 묻고도 머리글로 보면 상대가 있는 남은 칸을 표 짝마다 센다", () => {
    expect(result.stillExpected).toEqual([{ pair: { a: "p1-t1", b: "p3-t1" }, count: 4 }, { pair: { a: "p2-t1", b: "p4-t1" }, count: 4 }]);
  });

  it("요청이 실패하면 그 요청의 남은 칸은 전부 미응답이다", () => {
    const failed = recoverOutcome({ a, b, units, original: { verdicts: base.verdicts, rejectedPlaces: base.rejectedPlaces }, plans: planned.plans, rule: "both", reviews: new Map(), pairs: new Map() });
    expect(failed.after.tally.a.states["asked:unanswered"]).toBe(8);
    expect(failed.after.tally.a.states["asked:not_paired"]).toBe(6);
    expect(failed.merge.added).toEqual([]);
  });

  it("합이 맞지 않는 셈은 수를 내지 않고 예외를 낸다", () => {
    const broken = { ...a, facts: [...a.facts, a.facts[0] as (typeof a.facts)[number]] };
    expect(() => classifyRecovered(broken, "a", { verdicts: [], dropped: [], asked: new Map(), pairRequestTables: new Set(), pairedTables: new Set(), rejectedPlaces: new Set() })).toThrow(TallyError);
  });

  describe("채점", () => {
    const gold: GoldPair[] = ["r1c2", "r1c3", "r1c4", "r2c2", "r3c2"].map((cell, index) => ({ id: `g${index + 1}`, item: "x", period: "2023", a: `p1-t1:${cell}`, b: `p3-t1:${cell}`, rawA: "0", rawB: "0", origin: "added", inSeedTables: true }));
    const plants = [{ id: "E1", kind: "digits_transposed" as const, isError: true, cells: [{ cell: "p3-t1:r1c3", original: "39,604", planted: "39,640", pair: "g2" }], otherPlaceInB: [] }];
    const judgments = { note: "synthetic", status: "synthetic", items: [{ scope: "cross" as const, a: "p1-t1:r1c2", b: "p3-t1:r1c4", verdict: "different_item" as const, basis: "2023 ↔ 2025" }, { scope: "cross" as const, a: "p1-t1:r2c2", b: "p3-t1:r2c4", verdict: "different_item" as const, basis: "2023 ↔ 2025" }, { scope: "cross" as const, a: "p1-t2:r3c2", b: "p3-t2:r1c2", verdict: "same_item" as const, basis: "합계 = 국채" }] };
    const sheet = sheetOf(gold, plants, [], a, b, judgments);
    const answers: RecoverAnswers = { run: "synthetic", shiftRule: "both", nominations: nomination.nominations, candidates: nomination.candidates, skipped: planned.skipped, tableReview: null, requests: planned.plans.map((plan) => ({ id: plan.id, pair: plan.pair, reasons: plan.reasons, kind: plan.kind, remaining: plan.remaining, asked: plan.review?.remaining.length ?? null, alreadyPaired: plan.review?.paired.length ?? 0, answered: null, pairs: 0, rejected: {} })), rejected: result.rejected, before: { verdicts: result.before.verdicts }, checked: { verdicts: result.checked.verdicts, dropped: result.checked.dropped }, after: { verdicts: result.after.verdicts, tally: result.after.tally }, added: result.merge.added, dropped: result.merge.dropped, repeated: result.merge.repeated, conflicts: result.merge.conflicts, stillExpected: result.stillExpected };
    const report = reportRun({ name: "S-1", sheet, answers, rejectedBefore: base.rejected, cost: null, a, b, units });

    it("전 → 검사만 → 뒤: 참 일치, 잡은 오류, 짝지어진 정답 쌍, 표 칸 거짓 경보", () => {
      expect([report.before.cleanMatch, report.checked.cleanMatch, report.after.cleanMatch]).toEqual([1, 1, 3]);
      expect([report.before.caught, report.after.caught, report.after.errorTotal]).toEqual([0, 1, 1]);
      expect([report.before.goldPaired, report.after.goldPaired]).toEqual([1, 4]);
      // 밀린 두 짝(값이 다르다)과 합계 ↔ 국채(같은 항목, 값이 다르다): 거짓 경보는 판정이 「같은 항목 아님」인 둘뿐이다.
      expect(report.before.outside.differentItem).toEqual(["p1-t1:r1c2|p3-t1:r1c4", "p1-t1:r2c2|p3-t1:r2c4"]);
      expect(report.before.outside.sameItem).toEqual(["p1-t2:r3c2|p3-t2:r1c2"]);
      expect(report.checked.outside.differentItem).toEqual([]);
      expect(report.after.outside.unjudged).toEqual(["p2-t1:r3c3|p4-t1:r3c3"]);
    });

    it("열 밀림 검사의 채점: 가린 것 가운데 정답 쌍·같은 항목(잘못 가림)과 같은 항목 아님을 가른다", () => {
      expect(report.shift.both).toMatchObject({ cellPairs: 5, ok: 1, undecided: 2, undecidedNoHeader: 1, suspect: 2, suspectGold: [], suspectSame: [], wrongPairs: ["p1-t1:r1c2|p3-t1:r1c4", "p1-t1:r2c2|p3-t1:r2c4"], wrongFlagged: ["p1-t1:r1c2|p3-t1:r1c4", "p1-t1:r2c2|p3-t1:r2c4"] });
      expect(report.shift.either).toMatchObject({ suspect: 3, suspectSame: ["p1-t2:r3c2|p3-t2:r1c2"] });
      const goldFlagged = scoreShift(sheetOf([{ ...(gold[0] as GoldPair), b: "p3-t1:r1c4" }], [], [], a, b, null), base.verdicts, a, b, units, "both");
      expect(goldFlagged.suspectGold).toEqual(["p1-t1:r1c2|p3-t1:r1c4"]);
    });

    it("지목의 재현율, 되찾은 정답 쌍, 사라진 정답 쌍, 새 짝의 갈래, 판정할 목록", () => {
      expect(Object.fromEntries(report.missing)).toEqual({ "p1-t1|p3-t1": ["p1-t1:r1c2|p3-t1:r1c2", "p1-t1:r1c3|p3-t1:r1c3", "p1-t1:r1c4|p3-t1:r1c4", "p1-t1:r2c2|p3-t1:r2c2"] });
      expect(report.recall).toEqual({ asked: ["p1-t1|p3-t1"], nominatedNotAsked: [], notNominated: [] });
      expect(report.recoveredGold).toBe(3);
      expect(report.lostGold).toEqual([]);
      expect(report.added.gold).toHaveLength(3);
      expect(report.added.unjudged).toEqual(["p2-t1:r1c2|p4-t1:r1c2", "p2-t1:r3c3|p4-t1:r3c3"]);
      expect(toJudge(sheet, [report]).map((item) => `${item.a}|${item.b}`)).toEqual(["p1-t1:r1c2|p3-t1:r1c4", "p1-t1:r2c2|p3-t1:r2c4", "p1-t1:r2c3|p3-t1:r2c4", "p1-t2:r3c2|p3-t2:r1c2", "p2-t1:r1c2|p4-t1:r1c2", "p2-t1:r3c3|p4-t1:r3c3"]);
      const lost = reportRun({ name: "S-2", sheet, answers: { ...answers, after: { ...answers.after, verdicts: [] } }, rejectedBefore: {}, cost: null, a, b, units });
      expect(lost.lostGold).toEqual(["p1-t1:r3c2|p3-t1:r3c2"]);
      expect(reportRun({ name: "S-3", sheet, answers: { ...answers, nominations: [], requests: [] }, rejectedBefore: {}, cost: null, a, b, units }).recall.notNominated).toEqual(["p1-t1|p3-t1"]);
    });

    it("세 실행을 합친 대조군: 짝의 합집합, 같은 짝은 한 번", () => {
      const union = unionVerdicts([result.before.verdicts, result.after.verdicts]);
      expect(union).toHaveLength(new Set([...result.before.verdicts, ...result.after.verdicts].map((verdict) => `${verdict.a}|${verdict.b}`)).size);
      expect(scoreStage(sheet, union).goldPaired).toBe(4);
    });
  });
});

describe("방법 R 의 명령 (모의 공급사, synthetic)", () => {
  const dir = mkdtempSync(join(tmpdir(), "reconcile-recover-"));
  const file = (name: string, value: unknown): string => {
    const path = join(dir, name);
    writeFileSync(path, typeof value === "string" ? value : JSON.stringify(value));
    return path;
  };
  const docA = file("a.json", asDocumentJson(documentA()));
  const docB = file("b.json", asDocumentJson(documentB()));
  const stored = join(dir, "d");
  mkdirSync(stored);
  writeFileSync(join(stored, "units.jsonl"), `${JSON.stringify({ unitId: "d2/1", result: { kind: "success", data: SHIFTED } })}\n`);
  writeFileSync(join(stored, "outcome.json"), JSON.stringify({ method: "d2", outcome: { verdicts: judged().verdicts, rejected: judged().rejected } }));
  const common = ["--a", docA, "--b", docB, "--exam-a", docA, "--exam-b", docB, "--run", stored];
  const run = async (args: string[]): Promise<{ code: number; text: string }> => {
    const lines: string[] = [];
    const code = await main7(args, (line) => lines.push(line), () => undefined, { env: {} });
    return { code, text: lines.join("\n") };
  };

  it("check: 호출 없이 검사와 지목을 쓴다. 저장된 실행은 읽기만 한다", async () => {
    const out = join(dir, "check");
    const result = await run(["check", ...common, "--out", out]);
    expect(result.code).toBe(0);
    const report = JSON.parse(readFileSync(join(out, "check.json"), "utf8")) as { shift: { suspect: number; ok: number; undecided: number }; nominations: { pair: { a: string } }[]; candidates: unknown[]; requests: { id: string }[] };
    expect(report.shift).toMatchObject({ suspect: 2, ok: 1, undecided: 2 });
    expect(report.nominations.map((item) => item.pair.a)).toEqual(["p1-t1"]);
    expect(report.candidates).toHaveLength(1);
    expect(report.requests.map((item) => item.id)).toEqual(["r/cells/p1-t1|p3-t1"]);
    expect(readdirSync(stored).sort()).toEqual(["outcome.json", "units.jsonl"]);
  });

  it("dry-run 은 부르지 않고 요청 글을 쓴다. 요청 글에 값 후보 숫자가 없다", async () => {
    const out = join(dir, "dry");
    const result = await run(["runr", ...common, "--dry-run", "--out", out]);
    expect(result.code).toBe(0);
    const names = readdirSync(join(out, "requests")).sort();
    expect(names).toEqual(["r__cells__p1-t1__p3-t1.txt", "r__pair__p2-t1__p4-t1.txt", "r__tables.txt"]);
    for (const name of names) for (const value of VALUES) expect(readFileSync(join(out, "requests", name), "utf8")).not.toContain(value);
    const report = JSON.parse(readFileSync(join(out, "dry-run.json"), "utf8")) as { calls: number; noCacheUsd: number; maskCheck: { unmaskedValues: number; requests: number } };
    expect(report).toMatchObject({ calls: 3, maskCheck: { unmaskedValues: 0, requests: 3 } });
    expect(report.noCacheUsd).toBeGreaterThan(0);
  });

  it("모의 실행: 빈 답이면 다시 물은 칸은 전부 미응답이고 결과에 synthetic 표시가 붙는다. 채점 명령은 모의 결과를 받지 않는다", async () => {
    const out = join(dir, "mock");
    const result = await run(["runr", ...common, "--provider", "mock", "--out", out]);
    expect(result.code).toBe(0);
    const answers = JSON.parse(readFileSync(join(out, "answers.json"), "utf8")) as RecoverAnswers & { synthetic?: string };
    expect(answers.synthetic).toContain("모의 실행");
    expect(answers.tableReview).toEqual({ answered: [], accepted: [], rejected: {} });
    expect(answers.requests.map((item) => [item.id, item.answered])).toEqual([["r/cells/p1-t1|p3-t1", { unanswered: 15 }]]);
    expect(answers.after.tally.a.states["asked:unanswered"]).toBe(8);
    expect(answers.checked.dropped).toHaveLength(2);
    const pairs = file("pairs.json", { note: "synthetic", status: "synthetic", docs: { a: { file: "a", sha256: "0" }, b: { file: "b", sha256: "0" } }, pairs: [] });
    const plants = file("plants.json", { note: "synthetic", status: "synthetic", seed: 1, plan: [], plants: [] });
    const plants2 = file("plants2.json", { note: "synthetic", status: "synthetic", seed: 1, plan: [], plants: [], skipped: [] });
    const lines: string[] = [];
    const grading = ["--exam-a", docA, "--exam-b", docB, "--pairs", pairs, "--plants", plants, "--plants2", plants2];
    expect(await main8(["tabler", ...grading, "--runs", `S-1=${stored}=${out}`, "--out", join(dir, "table")], (line) => lines.push(line), () => undefined)).toBe(1);
    expect(lines.join("\n")).toContain("모의 실행");
    expect(await main8(["shiftr", ...grading, "--runs", `S-1=${stored}`, "--out", join(dir, "shift")], () => undefined, () => undefined)).toBe(0);
    expect(readFileSync(join(dir, "shift", "shift.md"), "utf8")).toContain("| S-1 | 5 | 1/5 | 2/5 (1) | 2/5 |");
  });

  it("모의 실행 (정해 둔 답): 표 검수가 낸 새 표 짝의 칸을 묻고, 칸 검수의 새 짝과 합쳐 결과를 낸다. 밀린 새 짝은 빠진다", async () => {
    const out = join(dir, "mock2");
    const cell = (id: string, counterpart: string | null) => ({ cell: id, counterpart, reason: counterpart === null ? "no_such_row_or_column" : null, unit: null, counterpart_unit: null });
    const mockAnswers = file("mock-answers.json", {
      "r/tables": { table_pairs: [{ a_table: "a:p2-t1", b_table: "b:p4-t1" }, { a_table: "a:p2-t1", b_table: "b:p9-t9" }] },
      "r/cells/p1-t1|p3-t1": { answers: [cell("a:p1-t1:r1c2", "b:p3-t1:r1c2"), cell("a:p1-t1:r1c3", "b:p3-t1:r1c3"), cell("a:p1-t1:r2c3", "b:p3-t1:r2c4"), cell("a:p1-t1:r3c4", null)] },
      "r/pair/p2-t1|p4-t1": { pairs: [{ a: "p2-t1:r1c2", b: "p4-t1:r1c2", unit_a: null, unit_b: null }, { a: "p2-t1:r3c3", b: "p4-t1:r3c3", unit_a: null, unit_b: null }, { a: "p2-t1:r3c3", b: "p3-t1:r3c3", unit_a: null, unit_b: null }] },
    });
    const result = await run(["runr", ...common, "--provider", "mock", "--mock-answers", mockAnswers, "--out", out]);
    expect(result.code).toBe(0);
    const answers = JSON.parse(readFileSync(join(out, "answers.json"), "utf8")) as RecoverAnswers & { synthetic?: string };
    expect(answers.synthetic).toContain("모의 실행");
    expect(answers.tableReview).toEqual({ answered: [{ a_table: "a:p2-t1", b_table: "b:p4-t1" }, { a_table: "a:p2-t1", b_table: "b:p9-t9" }], accepted: [{ a: "p2-t1", b: "p4-t1" }], rejected: { unknown_id: 1 } });
    expect(answers.requests.map((item) => [item.id, item.kind, item.reasons, item.pairs])).toEqual([
      ["r/cells/p1-t1|p3-t1", "review", ["rejected_answer", "shift_suspect", "expected_by_header"], 3],
      ["r/pair/p2-t1|p4-t1", "pair", ["new_table_pair"], 2],
    ]);
    expect(answers.added.map((item) => `${item.a}|${item.b}`)).toEqual(["p1-t1:r1c2|p3-t1:r1c2", "p1-t1:r1c3|p3-t1:r1c3", "p2-t1:r1c2|p4-t1:r1c2", "p2-t1:r3c3|p4-t1:r3c3"]);
    expect(answers.dropped.map((item) => [`${item.a}|${item.b}`, item.from])).toEqual([["p1-t1:r1c2|p3-t1:r1c4", "original"], ["p1-t1:r2c2|p3-t1:r2c4", "original"], ["p1-t1:r2c3|p3-t1:r2c4", "recovered"]]);
    expect(answers.rejected).toEqual({ "pair:outside_request_tables": 1 });
    expect(answers.after.verdicts).toHaveLength(answers.before.verdicts.length - 2 + 4);
    expect(answers.stillExpected).toEqual([{ pair: { a: "p1-t1", b: "p3-t1" }, count: 5 }, { pair: { a: "p2-t1", b: "p4-t1" }, count: 4 }]);
    // 같은 답으로 다시 채점하면(호출 없음) 같은 결과가 나온다.
    const again = join(dir, "mock2-replay");
    expect((await run(["runr", ...common, "--replay", out, "--route", "mock", "--out", again])).code).toBe(0);
    const replayed = JSON.parse(readFileSync(join(again, "answers.json"), "utf8")) as RecoverAnswers & { synthetic?: string };
    expect(replayed.after).toEqual(answers.after);
    expect(replayed.added).toEqual(answers.added);
  });

  it("정답·명세 인자를 받지 않고, 출력이 저장된 실행의 폴더 안이면 멈추고, 저장된 답이 그 실행의 결과와 다르면 멈춘다", async () => {
    expect((await run(["runr", ...common, "--pairs", "x.json", "--dry-run", "--out", join(dir, "x")])).code).toBe(2);
    expect((await run(["runr", ...common, "--dry-run", "--out", join(stored, "sub")])).code).toBe(1);
    const other = join(dir, "d-other");
    mkdirSync(other);
    writeFileSync(join(other, "units.jsonl"), `${JSON.stringify({ unitId: "d2/1", result: { kind: "success", data: SHIFTED } })}\n`);
    writeFileSync(join(other, "outcome.json"), JSON.stringify({ method: "d2", outcome: { verdicts: judged().verdicts.slice(1), rejected: {} } }));
    const result = await run(["check", "--a", docA, "--b", docB, "--exam-a", docA, "--exam-b", docB, "--run", other, "--out", join(dir, "y")]);
    expect(result.code).toBe(1);
    expect(result.text).toContain("다르다");
  });

  it("시험지로 낸 지목과 원본으로 낸 지목이 다르면 멈춘다 (머리글이 다른 문서)", async () => {
    const changed = documentB();
    (changed.tables[0] as { cells: string[][] }).cells[0] = ["지표", "단위", "2021", "2022", "2023"];
    const result = await run(["check", "--a", docA, "--b", docB, "--exam-a", docA, "--exam-b", file("b2.json", asDocumentJson(changed)), "--run", stored, "--out", join(dir, "z")]);
    expect(result.code).toBe(1);
  });
});
