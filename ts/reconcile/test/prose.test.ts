import { mkdirSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { extractDocument, type DocModel, type Extraction } from "../src/index.js";
import { main } from "../src/compare/cli2.js";
import { applyChanges, checkExam2, checkGold2, drawPlants2, mutate2, type Gold2 } from "../src/compare/exam2.js";
import { prng } from "../src/compare/exam.js";
import { TallyError } from "../src/compare/hybrid.js";
import { pageTexts, type DocContext } from "../src/compare/methods.js";
import { idsInText, judgeCellReview2, judgeGroups, judgePlacePairs, maskWithIds, planCellReviews2, renderDocumentWithIds, review2Outcome, tallyGroups, tallyPlaces, unmaskedInIdDocument } from "../src/compare/prose.js";
import { gradeGroups, gradeProse, type Planted } from "../src/compare/prose-report.js";
import { MASK } from "../src/compare/render.js";
import { renderCellReviewRequest, unmaskedInCellReview } from "../src/compare/review.js";
import { asDocumentJson, syntheticDocument, units } from "./helpers.js";

// 밴드 U (서술 속 숫자, 같은 문서 안)를 지어낸 문서로 본다 (synthetic). 어느 회사의 실제 값도 아니다.

const HEADER = { rows: [0], cols: [0, 1], basis: "rows_before_first_numeric_row" };
const table = (id: string, page: number, rows: string[][]) => ({ id, page, cells: [["구분", "단위", "2023", "2024"], ...rows], header: HEADER });
const documentA = (): DocModel =>
  syntheticDocument(
    "a",
    [table("p1-t1", 1, [["Scope 1", "tCO2eq", "48,217", "39,604"], ["Scope 2", "tCO2eq", "12,345", "11,871"]]), table("p2-t1", 2, [["Scope 1 (검증)", "tCO2eq", "48,217", "39,604"], ["비고", "", "목표 91,350 tCO2eq 이내", "달성"]])],
    [
      { id: "p1-b1", page: 1, text: "2024년 Scope 1 배출량은 39,604 tCO2eq 이며 전년 대비 8,613 tCO2eq 줄었다. 감축률은 17.9% 이다." },
      { id: "p2-b1", page: 2, text: "임직원은 1,251명이다." },
    ],
  );
const documentB = (): DocModel =>
  syntheticDocument(
    "b",
    [table("p3-t1", 1, [["직접 배출", "tCO2eq", "48,217", "39,640"], ["간접 배출", "tCO2eq", "12,345", "11,871"], ["임직원 수", "명", "1,204", "1,251"]])],
    [{ id: "p1-b1", page: 1, text: "2024년 직접 배출은 39,640 tCO2eq 이다." }],
  );
const VALUES = ["48,217", "39,604", "39,640", "12,345", "11,871", "8,613", "17.9", "91,350", "1,251", "1,204"];
const own = (document: DocModel, id: string): Extraction => extractDocument(document, id, { titles: "own" });
const context = (document: DocModel, extraction: Extraction): DocContext => ({ extraction, pages: pageTexts(extraction, new Map(document.blocks.filter((block) => block.text !== undefined).map((block) => [block.id, block.text as string])), new Map(document.blocks.map((block) => [block.id, block.page]))) });
const a = own(documentA(), "a");
const b = own(documentB(), "b");
const ctxA = context(documentA(), a);
const ctxB = context(documentB(), b);
const pair = (x: string, y: string, unit: string | null = null) => ({ a: x, b: y, unit_a: unit, unit_b: unit });

describe("서술 값 후보의 ID 붙이기와 가림", () => {
  it("글 속 값 후보는 ▒ 뒤에 ID 가 붙고, 값의 글자는 요청 글과 ID 어디에도 남지 않는다", () => {
    const text = renderDocumentWithIds(documentA(), a, { prefix: "a:" });
    expect(text).toContain(`배출량은 ${MASK}〔a:p1-b1#n3〕 tCO2eq 이며 전년 대비 ${MASK}〔a:p1-b1#n5〕 tCO2eq`);
    expect(text).toContain(`감축률은 ${MASK}%〔a:p1-b1#n7〕`);
    expect(text).toContain(`목표 ${MASK}〔a:p2-t1:r2c2#n1〕 tCO2eq 이내`);
    expect(text).toContain(`r1 | c0: Scope 1 | c1: tCO2eq | c2: ${MASK} | c3: ${MASK}`);
    for (const value of VALUES) expect(text).not.toContain(value);
    expect(unmaskedInIdDocument(text)).toEqual([]);
    const prose = a.facts.filter((fact) => fact.bucket !== "table_cell").map((fact) => fact.id);
    expect(idsInText(text).sort()).toEqual([...prose].sort());
    for (const id of idsInText(text)) expect(id).toMatch(/^p\d+-(b\d+|t\d+:r\d+c\d+)#n\d+$/);
  });

  it("가림이 빠지거나 ID 꼴이 아닌 것이 붙으면 확인이 잡는다", () => {
    expect(unmaskedInIdDocument("배출량은 39,604 tCO2eq")).toEqual(["39,604"]);
    expect(unmaskedInIdDocument(`r1 | c0: Scope 1 | c2: ${MASK} | c3: 39,604`)).toContain("39,604");
    expect(unmaskedInIdDocument(`배출량은 ${MASK}〔a:39604〕 tCO2eq`).length).toBeGreaterThan(0);
    expect(maskWithIds("감축률은 17.9% 이다", [], "")).toBe(`감축률은 ${MASK}% 이다`);
  });

  it("값을 바꿔 심어도 요청 글은 글자까지 같다 (값이 ID 와 글에 새지 않는다)", () => {
    const fact = a.facts.find((item) => item.id === "p1-b1#n3")!;
    const exam = applyChanges(documentA(), a, [{ doc: "a", target: fact.id, original: "39,604", planted: "39,904" }, { doc: "a", target: "p1-t1:r2c3", original: "11,871", planted: "118,710" }]);
    expect(renderDocumentWithIds(exam, own(exam, "a"), { prefix: "a:" })).toBe(renderDocumentWithIds(documentA(), a, { prefix: "a:" }));
  });
});

describe("D2 의 답 확인과 셈", () => {
  const answer = {
    pairs: [
      pair("a:p1-t1:r1c2", "b:p3-t1:r1c2"), // 표 칸끼리, 일치
      pair("a:p1-b1#n3", "b:p3-t1:r1c3", "tCO2eq"), // 서술 ↔ 표 칸, 값이 다르다
      pair("b:p1-b1#n2", "a:p1-b1#n3"), // 자리가 바뀌어 적힌 서술 ↔ 서술
      pair("a:p1-b1#n3", "a:p1-t1:r1c3"), // 같은 문서끼리
      pair("a:p9-b9#n1", "b:p3-t1:r1c2"), // 없는 ID
      pair("p1-t1:r2c2", "b:p3-t1:r2c2"), // 문서 이름이 없는 ID
      pair("a:p1-t1:r1c2", "b:p3-t1:r1c2"), // 같은 짝 두 번
    ],
  };
  const judged = judgePlacePairs([answer], ctxA, ctxB, units);

  it("없는 ID 와 같은 문서끼리의 짝은 버리고, 남은 짝은 코드가 값을 견준다", () => {
    expect(judged.rejected).toEqual({ same_document: 1, unknown_id: 2, duplicate_pair: 1 });
    expect(judged.verdicts.map((verdict) => `${verdict.a}|${verdict.b}|${verdict.outcome}`)).toEqual(["p1-t1:r1c2|p3-t1:r1c2|match", "p1-b1#n3|p3-t1:r1c3|mismatch", "p1-b1#n3|p1-b1#n2|mismatch"]);
  });

  it("서술 값 후보도 셈에 들어가고, 짝이 안 나온 서술 숫자는 갈래별로 남는다. 표 짝은 표의 숫자 칸끼리의 짝에서만 끌어낸다", () => {
    const tallied = tallyPlaces(a, b, judged);
    expect(tallied.tablePairs.map((item) => `${item.a}|${item.b}`)).toEqual(["p1-t1|p3-t1"]);
    expect(tallied.states.a.get("p1-b1#n3")).toBe("compared:mismatch");
    expect(tallied.states.a.get("p1-b1#n5")).toBe("unverified:outside_tables:sentence");
    expect(tallied.states.a.get("p2-t1:r2c2#n1")).toBe("unverified:outside_tables:table_text_cell");
    expect(tallied.states.a.get("p1-t1:r1c3")).toBe("left:in_paired_table");
    expect(tallied.states.a.get("p2-t1:r1c2")).toBe("unverified:table_not_paired");
    const sum = (tally: typeof tallied.tally.a): number => tally.compared.match + tally.compared.mismatch + tally.compared.needsExplanation + tally.leftInPairedTable + tally.answerRejected + tally.tableNotPaired + Object.values(tally.outsideTables).reduce((x, y) => x + y, 0);
    expect(sum(tallied.tally.a)).toBe(a.facts.length);
    expect(sum(tallied.tally.b)).toBe(b.facts.length);
    // 서술과만 견주어진 표 칸(b 의 r1c3)은 「견줌」이고, 그 표가 그것만으로 짝지어진 표가 되지는 않는다.
    const proseOnly = tallyPlaces(a, b, judgePlacePairs([{ pairs: [pair("a:p1-b1#n3", "b:p3-t1:r1c3", "tCO2eq")] }], ctxA, ctxB, units));
    expect(proseOnly.tablePairs).toEqual([]);
    expect(proseOnly.states.b.get("p3-t1:r1c3")).toBe("compared:mismatch");
    expect(proseOnly.states.b.get("p3-t1:r1c2")).toBe("unverified:table_not_paired");
  });
});

describe("H2 의 칸 검수 (상대는 그 요청의 남은 칸에서만)", () => {
  const base = judgePlacePairs([{ pairs: [pair("a:p1-t1:r1c2", "b:p3-t1:r1c2"), pair("a:p1-t1:r2c2", "b:p3-t1:r2c2"), pair("a:p1-b1#n3", "b:p3-t1:r1c3", "tCO2eq")] }], ctxA, ctxB, units);
  const plans = planCellReviews2(a, b, base.verdicts);
  const plan = plans[0]!;

  it("남은 칸에서 서술과 견주어진 칸은 빠지고, 요청 글에는 칸 ID 만 실린다", () => {
    expect(plans).toHaveLength(1);
    expect(plan.paired).toEqual([{ a: "p1-t1:r1c2", b: "p3-t1:r1c2" }, { a: "p1-t1:r2c2", b: "p3-t1:r2c2" }]);
    expect(plan.remaining).toEqual(["a:p1-t1:r1c3", "a:p1-t1:r2c3", "b:p3-t1:r2c3", "b:p3-t1:r3c2", "b:p3-t1:r3c3"]);
    const text = renderCellReviewRequest({ document: documentA(), extraction: a }, { document: documentB(), extraction: b }, plan);
    expect(unmaskedInCellReview(text)).toEqual([]);
    for (const value of VALUES) expect(text).not.toContain(value);
  });

  it("남은 칸이 아닌 상대(이미 짝지어진 칸)는 버리고, 남은 칸끼리의 짝만 받는다. 안 온 답은 미응답이다", () => {
    const judged = judgeCellReview2(
      {
        answers: [
          { cell: "a:p1-t1:r2c3", counterpart: "b:p3-t1:r2c3", reason: null, unit: null, counterpart_unit: null },
          { cell: "a:p1-t1:r1c3", counterpart: "b:p3-t1:r1c3", reason: null, unit: null, counterpart_unit: null }, // 상대가 남은 칸이 아니다 (서술과 이미 견주어졌다)
          { cell: "b:p3-t1:r3c2", counterpart: "a:p1-t1:r1c2", reason: null, unit: null, counterpart_unit: null }, // 이미 짝지어진 칸
          { cell: "b:p3-t1:r3c3", counterpart: null, reason: "already_paired", unit: null, counterpart_unit: null },
        ],
      },
      plan,
      ctxA,
      ctxB,
      units,
    );
    expect(judged.rejected).toEqual({ counterpart_not_remaining: 2 });
    expect(judged.newPairs.map((item) => `${item.a}|${item.b}|${item.outcome}`)).toEqual(["p1-t1:r2c3|p3-t1:r2c3|match"]);
    expect(judged.cells.get("a:p1-t1:r1c3")).toEqual({ state: "answer_rejected", why: "counterpart_not_remaining" });
    expect(judged.cells.get("b:p3-t1:r3c2")).toEqual({ state: "answer_rejected", why: "counterpart_not_remaining" });
    expect(judged.cells.get("b:p3-t1:r3c3")).toEqual({ state: "no_counterpart", reason: "already_paired" });
    expect(judged.cells.get("b:p3-t1:r2c3")).toEqual({ state: "unanswered" });
    const result = review2Outcome(a, b, base, [judged]);
    expect(result.newPairs).toHaveLength(1);
    expect(result.after.tally.a.compared.match).toBe(result.before.tally.a.compared.match + 1);
    expect(result.after.tally.a.undecided.answerRejected).toBe(1);
    expect(result.after.tally.b.noCounterpart).toEqual({ already_paired: 1 });
    expect(result.after.tally.a.leftInPairedTable + result.after.tally.b.leftInPairedTable).toBe(0);
    expect(result.after.tally.a.outsideTables).toEqual(result.before.tally.a.outsideTables);
  });
});

describe("I 의 묶음 확인과 묶음 안 비교", () => {
  const place = (id: string) => ({ id, unit: id.includes("#n") ? null : "tCO2eq" });
  const judged = judgeGroups(
    [
      {
        groups: [
          { places: [place("p1-t1:r1c3"), place("p2-t1:r1c3"), place("p1-b1#n3")] }, // 같은 값 셋
          { places: [place("p1-t1:r1c2"), place("p1-t1:r2c2")] }, // 값이 다른 둘
          { places: [place("p1-t1:r1c2"), place("p2-t1:r1c2"), place("p9-t9:r1c1")] }, // 없는 ID 하나, 한 자리가 두 묶음에
          { places: [place("p2-b1#n1"), place("p2-b1#n1")] }, // 자리 하나뿐
          { places: [place("p2-t1:r1c3"), place("p1-t1:r1c3"), place("p1-b1#n3")] }, // 같은 묶음 두 번
        ],
      },
    ],
    ctxA,
    units,
  );

  it("없는 ID 의 자리와 자리 하나뿐인 묶음은 버리고, 한 자리가 두 묶음에 들면 그 사실을 남긴다", () => {
    expect(judged.rejected).toEqual({ unknown_id: 1, duplicate_place: 1, single_place: 1, duplicate_group: 1 });
    expect(judged.groups.map((group) => group.places.length)).toEqual([3, 2, 2]);
    expect(judged.inSeveralGroups).toEqual(["p1-t1:r1c2"]);
  });

  it("묶음 안의 값을 둘씩 견준다. 값이 다르면 불일치다", () => {
    expect(judged.groups.map((group) => group.outcome)).toEqual(["match", "mismatch", "match"]);
    expect(judged.groups[0]!.verdicts).toHaveLength(3);
    const tallied = tallyGroups(a, judged);
    expect(tallied.states.get("p1-b1#n3")).toBe("grouped:match");
    expect(tallied.states.get("p1-t1:r2c2")).toBe("grouped:mismatch");
    expect(tallied.states.get("p1-t1:r1c2")).toBe("grouped:mismatch");
    expect(tallied.states.get("p1-b1#n5")).toBe("ungrouped:sentence");
    expect(tallied.tally.grouped.match + tallied.tally.grouped.mismatch + tallied.tally.grouped.needsExplanation + Object.values(tallied.tally.ungrouped).reduce((x, y) => x + y, 0)).toBe(a.facts.length);
    expect(() => tallyGroups({ ...a, facts: [...a.facts, a.facts[0]!] }, judged)).not.toThrow(TallyError);
  });

  it("채점: 심은 자리가 같은 정답 묶음의 자리와 불일치로 나오면 잡은 것이다", () => {
    const exam = applyChanges(documentA(), a, [{ doc: "a", target: "p1-b1#n3", original: "39,604", planted: "39,904" }]);
    const examEx = own(exam, "a");
    const judgedExam = judgeGroups([{ groups: [{ places: [place("p1-t1:r1c3"), place("p2-t1:r1c3"), place("p1-b1#n3")] }, { places: [place("p1-t1:r1c2"), place("p2-t1:r1c2")] }] }], context(exam, examEx), units);
    const planted: Planted[] = [{ plant: "U01", kind: "digit_changed", isError: true, doc: "a", target: "p1-b1#n3", original: "39,604", planted: "39,904" }];
    const gold = [
      { id: "G1", doc: "a" as const, item: "scope1", period: "2024", places: [{ id: "p1-t1:r1c3", raw: "39,604" }, { id: "p2-t1:r1c3", raw: "39,604" }, { id: "p1-b1#n3", raw: "39,604" }], control: null, evidence: "synthetic" },
      { id: "G2", doc: "a" as const, item: "scope1", period: "2023", places: [{ id: "p1-t1:r1c2", raw: "48,217" }, { id: "p2-t1:r1c2", raw: "48,217" }], control: null, evidence: "synthetic" },
      { id: "G3", doc: "a" as const, item: "scope2", period: "2023", places: [{ id: "p1-t1:r2c2", raw: "12,345" }, { id: "p1-b1#n5", raw: "8,613" }], control: null, evidence: "synthetic" },
    ];
    const graded = gradeGroups("a", gold, planted, judgedExam);
    expect(graded.errors).toEqual({ total: 1, caught: 1, visibleOther: 0, falseMatch: 0, silent: 0 });
    expect(graded.clean).toEqual({ total: 2, found: 1, match: 1, mismatch: 0, needsExplanation: 0 });
    expect(graded.groups).toEqual({ total: 3, full: 2, partial: 0, none: 1 });
    expect(graded.mismatches).toBe(2);
    expect(graded.otherMismatches).toEqual([]);
    // 심지 않았으면 그 묶음은 일치다.
    expect(gradeGroups("a", gold, [], judgeGroups([{ groups: [{ places: [place("p1-t1:r1c3"), place("p1-b1#n3")] }] }], ctxA, units)).rows[0]).toMatchObject({ found: "partial", outcome: "match" });
  });
});

const GOLD: Gold2 = {
  note: "synthetic",
  status: "synthetic",
  docs: { a: { file: "a", sha256: "0" }, b: { file: "b", sha256: "0" } },
  prosePairs: [
    { id: "N1", item: "scope1", period: "2024", a: "p1-b1#n3", b: "p3-t1:r1c3", rawA: "39,604", rawB: "39,640", control: null, evidence: "synthetic" },
    { id: "N2", item: "employees", period: "2024", a: "p2-b1#n1", b: "p3-t1:r3c3", rawA: "1,251", rawB: "1,251", control: null, evidence: "synthetic" },
  ],
  groups: [
    { id: "GA1", doc: "a", item: "scope1", period: "2023", places: [{ id: "p1-t1:r1c2", raw: "48,217" }, { id: "p2-t1:r1c2", raw: "48,217" }], control: null, evidence: "synthetic" },
    { id: "GA2", doc: "a", item: "target", period: null, places: [{ id: "p2-t1:r2c2#n1", raw: "91,350" }, { id: "p1-b1#n7", raw: "17.9%" }], control: null, evidence: "synthetic" },
  ],
};

describe("넓힌 시험지", () => {
  it("글자 바꾸기: 부호와 % 는 그대로 두고 숫자만 바꾼다", () => {
    expect(mutate2("times_10", "19.7", prng(1))).toBe("197");
    expect(mutate2("times_10", "13.82%", prng(1))).toBe("138.2%");
    expect(mutate2("times_10", "25,806", prng(1))).toBe("258,060");
    expect(mutate2("times_1000", "2,883", prng(1))).toBe("2,883,000");
    expect(mutate2("times_1000", "19.7", prng(1))).toBeNull();
    expect(mutate2("digits_transposed", "13.82%", prng(1))).toBe("31.82%");
    expect(mutate2("digit_changed", "약", prng(1))).toBeNull();
  });

  it("서술에 심으면 그 숫자 덩어리의 글자만 바뀐다. 명세 밖이 바뀐 사본은 확인이 잡는다", () => {
    const changes = [{ doc: "a" as const, target: "p1-b1#n5", original: "8,613", planted: "8,163" }, { doc: "a" as const, target: "p1-b1#n3", original: "39,604", planted: "39,904" }, { doc: "a" as const, target: "p2-t1:r2c2#n1", original: "91,350", planted: "913,500" }, { doc: "a" as const, target: "p1-t1:r2c2", original: "12,345", planted: "12,845" }];
    const exam = applyChanges(documentA(), a, changes);
    expect(exam.blocks.find((block) => block.id === "p1-b1")!.text).toBe("2024년 Scope 1 배출량은 39,904 tCO2eq 이며 전년 대비 8,163 tCO2eq 줄었다. 감축률은 17.9% 이다.");
    expect(exam.tables.find((item) => item.id === "p2-t1")!.cells[2]![2]).toBe("목표 913,500 tCO2eq 이내");
    expect(exam.tables.find((item) => item.id === "p1-t1")!.cells[2]![2]).toBe("12,845");
    expect(checkExam2(documentA(), exam, a, changes)).toEqual([]);
    expect(documentA().blocks.find((block) => block.id === "p1-b1")!.text).toContain("39,604");
    const tampered = structuredClone(exam);
    tampered.blocks.find((block) => block.id === "p2-b1")!.text = "임직원은 1,252명이다.";
    expect(checkExam2(documentA(), tampered, a, changes)).toEqual(["블록 p2-b1: 시험지의 글자가 「원본에 명세만 적용한 글자」와 다르다"]);
    expect(checkExam2(documentA(), exam, a, changes.slice(1)).length).toBeGreaterThan(0);
    expect(() => applyChanges(documentA(), a, [{ doc: "a", target: "p1-b1#n3", original: "39,640", planted: "1" }])).toThrow("원래 글자가 명세와 다르다");
  });

  it("정답의 자리가 문서와 맞는지 확인하고, 뽑기는 씨앗이 같으면 같고 한 쌍·한 묶음에 한 자리만 심는다", () => {
    expect(checkGold2(GOLD, a, b)).toEqual([]);
    expect(checkGold2({ ...GOLD, prosePairs: [{ ...GOLD.prosePairs[0]!, rawA: "39,640" }] }, a, b)[0]).toContain("정답은 「39,640」");
    const input = { gold: GOLD, v1Cells: new Set(["b:p3-t1:r1c3"]), plan: [{ kind: "digit_changed" as const, pool: "prose_pair" as const }, { kind: "digit_changed" as const, pool: "prose_pair" as const }, { kind: "times_10" as const, pool: "group_a" as const }, { kind: "times_10" as const, pool: "group_a" as const }, { kind: "times_10" as const, pool: "group_a" as const }], seed: 7, survives: () => true };
    const drawn = drawPlants2(input);
    expect(drawPlants2(input)).toEqual(drawn);
    // N1 은 밴드 R 의 칸과 이어져 있어 심지 않는다. 서술 쌍의 자리는 N2 의 서술 쪽 하나뿐이다.
    expect(drawn.plants.filter((plant) => plant.pool === "prose_pair").map((plant) => plant.target)).toEqual(["p2-b1#n1"]);
    expect(drawn.plants.filter((plant) => plant.pool === "group_a").map((plant) => plant.groups[0]).sort()).toEqual(["GA1", "GA2"]);
    expect(drawn.skipped.map((item) => item.step)).toEqual([2, 5]);
    expect(drawPlants2({ ...input, survives: () => false }).plants).toEqual([]);
  });

  it("채점: 서술이 낀 정답 쌍이 불일치로 나오면 잡은 것, 짝지어지지 않으면 조용히 지나간 것이다", () => {
    const planted: Planted[] = [{ plant: "U01", kind: "digit_changed", isError: true, doc: "a", target: "p1-b1#n3", original: "39,640", planted: "39,604" }];
    const prose = (doc: "a" | "b", id: string): boolean => (doc === "a" ? a : b).facts.find((fact) => fact.id === id)?.bucket !== "table_cell";
    const judged = judgePlacePairs([{ pairs: [pair("a:p1-b1#n3", "b:p3-t1:r1c3", "tCO2eq"), pair("a:p2-b1#n1", "b:p3-t1:r3c3", "명"), pair("a:p1-b1#n5", "b:p1-b1#n2")] }], ctxA, ctxB, units);
    const graded = gradeProse(GOLD, planted, judged.verdicts, prose);
    expect(graded.errors).toEqual({ total: 1, caught: 1, visibleOther: 0, falseMatch: 0, silent: 0 });
    expect(graded.clean).toEqual({ total: 1, match: 1, needsExplanation: 0, mismatch: 0, notPaired: 0 });
    expect(graded.outsideGold.mismatch.map((verdict) => `${verdict.a}|${verdict.b}`)).toEqual(["p1-b1#n5|p1-b1#n2"]);
    const none = gradeProse(GOLD, planted, [], prose);
    expect(none.errors.silent).toBe(1);
    expect(none.clean.notPaired).toBe(1);
  });
});

describe("밴드 U 의 명령 (모의 공급사, synthetic)", () => {
  const dir = mkdtempSync(join(tmpdir(), "reconcile-prose-"));
  const file = (name: string, value: unknown): string => {
    const path = join(dir, name);
    writeFileSync(path, typeof value === "string" ? value : JSON.stringify(value));
    return path;
  };
  const docA = file("a.json", asDocumentJson(documentA()));
  const docB = file("b.json", asDocumentJson(documentB()));
  const pairs = file("pairs.json", { note: "synthetic", status: "synthetic", docs: { a: { file: "a", sha256: "0" }, b: { file: "b", sha256: "0" } }, pairs: [{ id: "g1", item: "Scope 1", period: "2023", a: "p1-t1:r1c2", b: "p3-t1:r1c2", rawA: "48,217", rawB: "48,217", origin: "added", inSeedTables: true }] });
  const plants = file("plants.json", { note: "synthetic", status: "synthetic", seed: 1, plan: [], plants: [] });
  const gold2 = file("gold2.json", GOLD);
  const spec = join(dir, "plants2.json");
  const exam = join(dir, "exam");
  const common = ["--a", docA, "--b", docB, "--pairs", pairs, "--plants", plants, "--gold2", gold2];
  const withExam = [...common, "--plants2", spec, "--exam-a", join(exam, "a.document.json"), "--exam-b", join(exam, "b.document.json")];
  const run = async (args: string[]): Promise<{ code: number; text: string }> => {
    const lines: string[] = [];
    const code = await main(args, (line) => lines.push(line), () => undefined, { env: {} });
    return { code, text: lines.join("\n") };
  };

  it("뽑고, 심고, dry-run 은 부르지 않고 요청 글을 쓴다. 요청 글에 값 후보 숫자가 없다", async () => {
    expect((await run(["draw2", ...common, "--seed", "3", "--out-spec", spec])).code).toBe(0);
    expect((await run(["draw2", ...common, "--seed", "3", "--out-spec", spec])).code).toBe(2);
    const built = await run(["build2", ...common, "--plants2", spec, "--out", exam]);
    expect(built.code).toBe(0);
    for (const [method, extra] of [["d2", []], ["i", ["--doc", "a"]], ["i", ["--doc", "b"]]] as const) {
      const out = join(dir, `dry-${method}-${extra[1] ?? ""}`);
      const result = await run(["run2", "--method", method, ...withExam, ...extra, "--dry-run", "--out", out]);
      expect(result.code).toBe(0);
      const report = JSON.parse(readFileSync(join(out, "dry-run.json"), "utf8")) as { calls: number; notes: { maskCheck: { unmaskedValues: number } } };
      expect(report.calls).toBe(1);
      expect(report.notes.maskCheck.unmaskedValues).toBe(0);
      for (const name of readdirSync(join(out, "requests"))) {
        const text = readFileSync(join(out, "requests", name), "utf8");
        for (const value of VALUES) expect(text).not.toContain(value);
        expect(text).toContain(MASK);
      }
    }
  });

  it("모의 실행은 synthetic 표시를 달고, h2 는 d2 의 저장된 답 없이는 돌지 않고, 표는 모의 실행을 받지 않는다", async () => {
    const d2 = join(dir, "mock-d2");
    expect((await run(["run2", "--method", "d2", ...withExam, "--provider", "mock", "--out", d2])).code).toBe(0);
    expect(JSON.parse(readFileSync(join(d2, "outcome.json"), "utf8")).synthetic).toContain("모의 실행");
    expect((await run(["run2", "--method", "h2", ...withExam, "--provider", "mock", "--out", join(dir, "x1")])).code).toBe(1);
    const h2 = await run(["run2", "--method", "h2", ...withExam, "--d2-run", d2, "--provider", "mock", "--out", join(dir, "mock-h2")]);
    expect(h2.code).toBe(0);
    expect((await run(["run2", "--method", "i", ...withExam, "--doc", "a", "--provider", "mock", "--out", join(dir, "mock-i")])).code).toBe(0);
    const tables = await run(["table2", ...withExam, "--d2", `D2-1=${d2}`, "--out", join(dir, "table")]);
    expect(tables.code).toBe(1);
    expect(tables.text).toContain("모의 실행의 결과다");
    // 실제 공급사는 경로·상한·호출 기록·키가 없으면 부르지 않는다.
    expect((await run(["run2", "--method", "d2", ...withExam, "--provider", "openai", "--route", "light-openai", "--out", join(dir, "x2")])).code).toBe(2);
    mkdirSync(join(dir, "x3"));
    expect((await run(["run2", "--method", "i", ...withExam, "--provider", "mock", "--out", join(dir, "x3")])).code).toBe(1);
  });
});
