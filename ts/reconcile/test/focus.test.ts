import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { extractDocument, type DocModel } from "../src/index.js";
import { judgeAsk, listTargets, targetKey, type AskDoc, type AskState, type Target } from "../src/compare/ask.js";
import { main as cli12 } from "../src/compare/cli12.js";
import { buildFocusRequest, gradeFocus, labelParts, nominate, pairDocuments, planFocus, significantDigits, splitBasis, statesFromPairs, tallyFocus, type Expected } from "../src/compare/focus.js";
import { pageTexts, type DocContext } from "../src/compare/methods.js";
import { judgePlacePairs, unmaskedInIdDocument } from "../src/compare/prose.js";
import { asDocumentJson, syntheticDocument, tempDir, units } from "./helpers.js";

// 밴드 CH1 (방법 F: 도표의 값을 좁혀 묻기)를 지어낸 문서로 본다 (synthetic). 어느 회사의 실제 값도 아니다.

const HEADER = { rows: [0], cols: [0], basis: "rows_before_first_numeric_row" };
const NO_HEADER = { rows: [], cols: [], basis: "empty" };

/** 문서 a: 1쪽 보통 표, 2쪽 도표가 표로 잘못 실린 칸(값 둘이 한 칸에), 3쪽 상대가 없는 도표. */
const docA = (first = "1,234", second = "5,678"): DocModel =>
  syntheticDocument(
    "a",
    [
      { id: "p1-t1", page: 1, cells: [["구분", "2023", "2024"], ["가상 배출량", "1,180", "1,204"]], header: HEADER },
      { id: "p2-t1", page: 2, cells: [["가상 판매량", ""], [`${first} ${second}`, "42.5%"], ["2023 2024", ""]], header: NO_HEADER },
      { id: "p3-t1", page: 3, cells: [["가상 생산량", ""], ["7,777 8,888", ""], ["2023 2024", ""]], header: NO_HEADER },
    ],
    [{ id: "p2-b1", page: 2, text: "가상 판매량의 추이를 도표로 보인다.", bbox: [40, 100, 400, 112] }],
    3,
  );
/** 문서 b: 1쪽 글, 2쪽 판매량 표(도표의 상대), 3쪽 무관한 표. */
const docB = (): DocModel =>
  syntheticDocument(
    "b",
    [
      { id: "p2-t1", page: 2, cells: [["구분", "2023", "2024"], ["가상 판매량", "1,234", "5,678"], ["가상 배출량", "1,180", "1,204"]], header: HEADER },
      { id: "p3-t1", page: 3, cells: [["구분", "2023", "2024"], ["가상 직원 수", "311", "322"]], header: HEADER },
    ],
    [{ id: "p1-b1", page: 1, text: "가상 조직의 자료집이다. 비밀 낱말 없음.", bbox: [40, 100, 400, 112] }],
    3,
  );
const LABELS = { "a:p2-t1:r1c0#n1": "가상 판매량 · 2023", "a:p2-t1:r1c0#n2": "가상 판매량 · 2024", "a:p2-t1:r1c1": "가상 판매량 · 비중", "a:p3-t1:r1c0#n1": "가상 생산량 · 2023", "a:p3-t1:r1c0#n2": "가상 생산량 · 2024" };

const ask = (model: DocModel, side: "a" | "b"): AskDoc & { context: DocContext } => {
  const extraction = extractDocument(model, side, { titles: "own", kinds: true });
  const blockText = new Map(model.blocks.filter((block) => block.text !== undefined).map((block) => [block.id, block.text as string]));
  return { model, extraction, context: { extraction, pages: pageTexts(extraction, blockText, new Map(model.blocks.map((block) => [block.id, block.page]))) } };
};
const docs = (a = docA(), b = docB()) => ({ a: ask(a, "a"), b: ask(b, "b") });
const targetsOf = (both: ReturnType<typeof docs>): Target[] => [...listTargets(both.a, "a", LABELS), ...listTargets(both.b, "b", LABELS)].filter((target) => target.branch === "chart");

describe("지목", () => {
  it("유효 숫자 수와 라벨의 가름", () => {
    expect(significantDigits("13.82")).toBe(4);
    expect(significantDigits("-0.050")).toBe(2);
    expect(labelParts("가상 판매량 · 계열 · 2023")).toEqual({ names: ["가상판매량", "계열"], category: "2023" });
    expect(labelParts("가상 판매량")).toEqual({ names: ["가상판매량"], category: null });
  });

  it("value: 값이 같은 긴 숫자가 상대 문서에 있는 쪽만 지목하고, 신호가 없는 쪽은 묻지 않는다", () => {
    const both = docs();
    const found = nominate(targetsOf(both), both, "value");
    expect(found.map((item) => [item.source, item.page, item.otherPages])).toEqual([["a", 2, [2]]]);
    expect(found[0]?.signals.map((signal) => [signal.target, signal.other])).toEqual([["a:p2-t1:r1c0#n1", "b:p2-t1:r1c1"], ["a:p2-t1:r1c0#n2", "b:p2-t1:r1c2"]]);
  });

  it("value: 짧은 숫자는 신호가 아니다 (42.5% 는 세 자리)", () => {
    const both = docs(docA(), syntheticDocument("b", [{ id: "p1-t1", page: 1, cells: [["구분", "비중"], ["가상 판매량", "42.5%"]], header: HEADER }]));
    expect(nominate(targetsOf(both), both, "value")).toEqual([]);
    expect(nominate(targetsOf(both), both, "value", 3).map((item) => item.page)).toEqual([2]);
  });

  it("value: 값 하나가 틀려도 같은 쪽의 다른 값이 신호를 내면 지목이 남고, 전부 틀리면 지목되지 않는다", () => {
    const one = docs(docA("1,243"));
    expect(nominate(targetsOf(one), one, "value").map((item) => [item.page, item.signals.length])).toEqual([[2, 1]]);
    const all = docs(docA("1,243", "5,687"));
    expect(nominate(targetsOf(all), all, "value")).toEqual([]);
  });

  it("value: 백분율 여부가 다르면 같은 값이 아니다", () => {
    const both = docs(docA(), syntheticDocument("b", [{ id: "p1-t1", page: 1, cells: [["구분", "비율"], ["가상 판매 비율", "1,234%"]], header: HEADER }]));
    expect(nominate(targetsOf(both), both, "value")).toEqual([]);
  });

  it("names: 이름만 맞고 범주(연도)가 상대 표의 머리글에 없으면 지목하지 않는다", () => {
    const both = docs(docA(), syntheticDocument("b", [{ id: "p1-t1", page: 1, cells: [["구분", "2019", "2020"], ["가상 판매량", "9,111", "9,222"]], header: HEADER }]));
    expect(nominate(targetsOf(both), both, "names")).toEqual([]);
  });

  it("names: 값을 보지 않고 라벨의 이름과 범주가 상대 표의 머리글에 있으면 지목한다 (값이 전부 틀려도 남는다)", () => {
    const all = docs(docA("1,243", "5,687"));
    const found = nominate(targetsOf(all), all, "names");
    expect(found.map((item) => [item.source, item.page, item.otherPages])).toEqual([["a", 2, [2]]]);
    expect(nominate(targetsOf(all), all, "either").map((item) => item.page)).toEqual([2]);
  });
});

describe("요청", () => {
  const both = docs();
  const targets = targetsOf(both);
  const plans = planFocus(targets, nominate(targets, both, "value"));

  it("대상은 지목된 쪽의 도표 값 전부다 (신호를 낸 값만이 아니다)", () => {
    expect(plans.map((plan) => [plan.id, plan.pages, plan.otherPages, plan.targets.map(targetKey)])).toEqual([["f/a/p2", [2], [2], ["a:p2-t1:r1c0#n1", "a:p2-t1:r1c0#n2", "a:p2-t1:r1c1"]]]);
  });

  it("이름으로 나누면 라벨의 이름이 같은 대상끼리 요청이 갈리고 대상의 합은 같다", () => {
    const mixed = { ...LABELS, "a:p2-t1:r1c1": "가상 비중 · 2024" };
    const all = [...listTargets(both.a, "a", mixed), ...listTargets(both.b, "b", mixed)].filter((target) => target.branch === "chart");
    const split = planFocus(all, nominate(all, both, "value"), true);
    expect(split.map((plan) => [plan.id, plan.targets.map(targetKey)])).toEqual([["f/a/p2/g1", ["a:p2-t1:r1c0#n1", "a:p2-t1:r1c0#n2"]], ["f/a/p2/g2", ["a:p2-t1:r1c1"]]]);
    expect(split.every((plan) => buildFocusRequest(plan, both, mixed).vars.source === buildFocusRequest(split[0]!, both, mixed).vars.source)).toBe(true);
  });

  it("상대 문서 자리에는 지목된 쪽만 실리고, 값 후보 숫자가 남지 않는다", () => {
    const request = buildFocusRequest(plans[0]!, both, LABELS);
    expect(request.unmasked).toEqual([]);
    expect(request.missingInSource).toEqual([]);
    expect(request.vars.other).toContain("## 2쪽");
    expect(request.vars.other).not.toContain("## 1쪽");
    expect(request.vars.other).not.toContain("## 3쪽");
    expect(request.vars.other).not.toContain("비밀 낱말");
    expect(request.vars.source).toContain("[가상 판매량 · 2023: ▒〔a:p2-t1:r1c0#n1〕]");
    for (const text of [request.vars.other, request.vars.source, request.vars.targets, pairDocuments(request)]) for (const value of ["1,234", "5,678", "1,180", "42.5"]) expect(text).not.toContain(value);
    expect(unmaskedInIdDocument(pairDocuments(request))).toEqual([]);
  });

  it("값이 다른 시험지로 조립해도 같은 계획의 요청 글이 글자까지 같다", () => {
    const exam = docs(docA("1,243"));
    const examTargets = targetsOf(exam);
    const examPlans = planFocus(examTargets, nominate(examTargets, exam, "value"));
    expect(buildFocusRequest(examPlans[0]!, exam, LABELS).vars).toEqual(buildFocusRequest(examPlans[0]!, both, LABELS).vars);
  });

  it("pair 꼴의 문서 글은 문서 a 가 앞이다", () => {
    const text = pairDocuments(buildFocusRequest(plans[0]!, both, LABELS));
    expect(text.indexOf("# 문서 a")).toBeLessThan(text.indexOf("# 문서 b"));
  });
});

describe("답", () => {
  const exam = docs(docA("1,243"));
  const targets = targetsOf(exam);
  const plans = planFocus(targets, nominate(targets, exam, "value"));
  const plan = plans[0]!;

  it("ask: 상대를 답한 대상의 값은 코드가 견준다 (틀린 값은 불일치)", () => {
    const judgement = judgeAsk({ answers: [{ target: "a:p2-t1:r1c0#n1", counterparts: [{ id: "b:p2-t1:r1c1", unit: null }], reason: null, unit: null }, { target: "a:p2-t1:r1c0#n2", counterparts: [{ id: "b:p2-t1:r1c2", unit: null }], reason: null, unit: null }, { target: "a:p2-t1:r1c1", counterparts: [], reason: "not_in_other_document", unit: null }] }, plan, exam.a.context, exam.b.context, units);
    expect(judgement.verdicts.map((verdict) => [verdict.a, verdict.b, verdict.outcome])).toEqual([["p2-t1:r1c0#n1", "p2-t1:r1c1", "mismatch"], ["p2-t1:r1c0#n2", "p2-t1:r1c2", "match"]]);
    const tally = tallyFocus(targets, plans, judgement.states);
    expect(tally).toMatchObject({ targets: 5, notNominated: 2, asked: 3, paired: 2, none: { not_in_other_document: 1 }, unanswered: 0, answerRejected: 0 });
  });

  it("ask2: basis 를 떼어 같은 확인을 타고, 엇갈리면 약한 쪽을 남긴다", () => {
    const split = splitBasis({ answers: [{ target: "a:p2-t1:r1c0#n2", counterparts: [{ id: "b:p2-t1:r1c2", unit: null, basis: "clear" }, { id: "b:p2-t1:r1c2", unit: null, basis: "likely" }], reason: null, unit: null }] });
    expect(split.basis.get("a:p2-t1:r1c0#n2|b:p2-t1:r1c2")).toBe("likely");
    expect(split.answer.answers[0]?.counterparts[0]).toEqual({ id: "b:p2-t1:r1c2", unit: null });
  });

  it("pair: 받아들인 짝에 대상이 나온 것만 「상대를 답함」이다 (없는 ID 의 짝, 대상이 아닌 칸의 짝은 세지 않는다)", () => {
    const judgement = judgePlacePairs([{ pairs: [{ a: "a:p2-t1:r1c0#n2", b: "b:p2-t1:r1c2", unit_a: null, unit_b: null }, { a: "a:p2-t1:r1c0#n1", b: "b:p9-t9:r1c1", unit_a: null, unit_b: null }, { a: "a:p1-t1:r1c1", b: "b:p2-t1:r2c1", unit_a: null, unit_b: null }] }], exam.a.context, exam.b.context, units);
    const moved = statesFromPairs(plan, judgement);
    expect([...moved.states].filter(([, state]) => state.state === "paired").map(([key]) => key)).toEqual(["a:p2-t1:r1c0#n2"]);
    expect(moved.states.get("a:p2-t1:r1c0#n1")).toEqual({ state: "unanswered" });
    expect(judgement.rejected).toEqual({ unknown_id: 1 });
  });

  it("pair: 대상이 문서 b 쪽이면 b 의 자리를 대상으로, a 의 자리를 상대로 읽는다", () => {
    const planB = { ...plan, id: "f/b/p2", source: "b" as const, other: "a" as const, targets: [{ doc: "b" as const, id: "p2-t1:r1c2", branch: "chart" as const, page: 2, name: "가상 판매량 · 2024" }] };
    const judgement = judgePlacePairs([{ pairs: [{ a: "a:p2-t1:r1c0#n2", b: "b:p2-t1:r1c2", unit_a: null, unit_b: null }] }], exam.a.context, exam.b.context, units);
    expect(statesFromPairs(planB, judgement).states.get("b:p2-t1:r1c2")).toEqual({ state: "paired", counterparts: ["a:p2-t1:r1c0#n2"] });
  });

  it("셈: 버려진 답은 「상대를 답함」이 아니라 「버려진 답」으로 센다", () => {
    const states = new Map<string, AskState>([["a:p2-t1:r1c0#n1", { state: "answer_rejected", why: "unknown_id" }], ["a:p2-t1:r1c0#n2", { state: "paired", counterparts: ["b:p2-t1:r1c2"] }]]);
    expect(tallyFocus(targets, plans, states)).toMatchObject({ paired: 1, answerRejected: 1, unanswered: 1 });
  });

  it("셈: 답이 없는 대상은 「답 없음」, 지목되지 않은 쪽의 대상은 「지목되지 않음」이고 합은 도표 대상 수다", () => {
    const tally = tallyFocus(targets, plans, new Map<string, AskState>());
    expect(tally).toMatchObject({ targets: 5, notNominated: 2, asked: 3, paired: 0, unanswered: 3 });
    expect(() => tallyFocus(targets, [{ ...plan, targets: [...plan.targets, { ...plan.targets[0]!, id: "p9-t9:r0c0" }] }], new Map<string, AskState>())).not.toThrow();
  });
});

describe("채점", () => {
  const expected: Expected[] = [
    { name: "손대지 않음", target: "a:p2-t1:r1c0#n2", right: ["b:p2-t1:r1c2"], plant: null },
    { name: "심은 자리", target: "a:p2-t1:r1c0#n1", right: ["b:p2-t1:r1c1"], plant: "X1" },
    { name: "안 물은 자리", target: "a:p3-t1:r1c0#n1", right: ["b:p3-t1:r1c1"], plant: null },
  ];
  const states = new Map<string, AskState>([["a:p2-t1:r1c0#n2", { state: "paired", counterparts: ["b:p2-t1:r1c2"] }], ["a:p2-t1:r1c0#n1", { state: "paired", counterparts: ["b:p2-t1:r1c1", "b:p2-t1:r2c1"] }], ["a:p2-t1:r1c1", { state: "paired", counterparts: ["b:p3-t1:r1c1"] }]]);
  const verdict = (a: string, b: string, outcome: "match" | "mismatch") => ({ a, b, outcome, reason: "", valuesDiffer: outcome === "mismatch" });

  it("옳은 상대와 짝지어진 것만 세고, 심은 자리는 불일치여야 잡은 것이다", () => {
    const grade = gradeFocus(expected, states, [verdict("p2-t1:r1c0#n2", "p2-t1:r1c2", "match"), verdict("p2-t1:r1c0#n1", "p2-t1:r1c1", "mismatch"), verdict("p2-t1:r1c0#n1", "p2-t1:r2c1", "mismatch"), verdict("p2-t1:r1c1", "p3-t1:r1c1", "mismatch")]);
    expect(grade).toMatchObject({ rightPaired: 2, expected: 3, cleanMatch: 1, clean: 2, plantedCaught: 1, plantedFalseMatch: 0, planted: 1 });
    expect(grade.rows[2]).toMatchObject({ state: "not_asked", rightPaired: false });
    expect(grade.outside.map((item) => [item.target, item.counterpart])).toEqual([["a:p2-t1:r1c0#n1", "b:p2-t1:r2c1"], ["a:p2-t1:r1c1", "b:p3-t1:r1c1"]]);
  });

  it("심은 자리가 옳은 상대와 일치로 나오면 거짓 일치다. 틀린 상대와의 짝은 옳은 짝이 아니다", () => {
    const grade = gradeFocus(expected, new Map<string, AskState>([["a:p2-t1:r1c0#n1", { state: "paired", counterparts: ["b:p2-t1:r1c1"] }], ["a:p2-t1:r1c0#n2", { state: "paired", counterparts: ["b:p2-t1:r2c2"] }]]), [verdict("p2-t1:r1c0#n1", "p2-t1:r1c1", "match"), verdict("p2-t1:r1c0#n2", "p2-t1:r2c2", "match")]);
    expect(grade).toMatchObject({ rightPaired: 1, cleanMatch: 0, plantedCaught: 0, plantedFalseMatch: 1 });
  });
});

describe("채점의 가장자리", () => {
  const verdict = (a: string, b: string, outcome: "match" | "mismatch" | "needs_explanation") => ({ a, b, outcome, reason: "", valuesDiffer: outcome === "mismatch" });
  const planted: Expected[] = [{ name: "심은 자리", target: "a:p2-t1:r1c0#n1", right: ["b:p2-t1:r1c1"], plant: "X1" }];
  const clean: Expected[] = [{ name: "손대지 않음", target: "a:p2-t1:r1c0#n2", right: ["b:p2-t1:r1c2", "b:p2-t1:r2c2"], plant: null }];

  it("심은 자리가 틀린 상대와만 불일치면 잡은 것이 아니다", () => {
    const grade = gradeFocus(planted, new Map<string, AskState>([["a:p2-t1:r1c0#n1", { state: "paired", counterparts: ["b:p2-t1:r2c1"] }]]), [verdict("p2-t1:r1c0#n1", "p2-t1:r2c1", "mismatch")]);
    expect(grade).toMatchObject({ rightPaired: 0, plantedCaught: 0, plantedFalseMatch: 0 });
  });

  it("심은 자리가 옳은 상대와 짝지어졌어도 견준 결과가 없거나 설명 필요면 잡은 것이 아니다", () => {
    const states = new Map<string, AskState>([["a:p2-t1:r1c0#n1", { state: "paired", counterparts: ["b:p2-t1:r1c1"] }]]);
    expect(gradeFocus(planted, states, [])).toMatchObject({ rightPaired: 1, plantedCaught: 0 });
    expect(gradeFocus(planted, states, [verdict("p2-t1:r1c0#n1", "p2-t1:r1c1", "needs_explanation")])).toMatchObject({ rightPaired: 1, plantedCaught: 0, plantedFalseMatch: 0 });
  });

  it("손대지 않은 자리는 옳은 상대 전부와 일치여야 일치로 센다", () => {
    const states = new Map<string, AskState>([["a:p2-t1:r1c0#n2", { state: "paired", counterparts: ["b:p2-t1:r1c2", "b:p2-t1:r2c2"] }]]);
    expect(gradeFocus(clean, states, [verdict("p2-t1:r1c0#n2", "p2-t1:r1c2", "match"), verdict("p2-t1:r1c0#n2", "p2-t1:r2c2", "mismatch")])).toMatchObject({ rightPaired: 1, cleanMatch: 0 });
  });
});

describe("명령 (cli12)", () => {
  const write = (dir: string, name: string, value: unknown): string => {
    const path = join(dir, name);
    writeFileSync(path, JSON.stringify(value));
    return path;
  };
  const setup = (examA: DocModel = docA("1,243")) => {
    const dir = tempDir();
    const args = ["--a", write(dir, "a.json", asDocumentJson(docA())), "--b", write(dir, "b.json", asDocumentJson(docB())), "--exam-a", write(dir, "exam-a.json", asDocumentJson(examA)), "--exam-b", write(dir, "exam-b.json", asDocumentJson(docB())), "--chart-labels", write(dir, "labels.json", { a: Object.fromEntries(Object.entries(LABELS).map(([key, text]) => [key.slice(2), text])), b: {} })];
    return { dir, args };
  };
  const run = async (args: string[]): Promise<{ code: number; lines: string[] }> => {
    const lines: string[] = [];
    const code = await cli12(args, (line) => lines.push(line), () => undefined, { env: {} });
    return { code, lines };
  };

  it("nominate: 시험지와 원본의 지목을 같이 낸다 (심은 값은 신호만 줄인다)", async () => {
    const { dir, args } = setup();
    expect((await run(["nominate", ...args, "--out", join(dir, "n")])).code).toBe(0);
    const report = JSON.parse(readFileSync(join(dir, "n", "nominations.json"), "utf8")) as { chartTargets: number; value: { exam: { page: number; signals: unknown[] }[]; original: { page: number; signals: unknown[] }[] } };
    expect(report.chartTargets).toBe(5);
    expect(report.value.exam.map((item) => [item.page, item.signals.length])).toEqual([[2, 1]]);
    expect(report.value.original.map((item) => [item.page, item.signals.length])).toEqual([[2, 2]]);
  });

  it.each(["ask", "ask2", "pair"])("dry-run (%s): 요청 글에 값 후보 숫자가 없고 지목된 쪽만 실린다", async (arm) => {
    const { dir, args } = setup();
    expect((await run(["runf", ...args, "--nominate", "value", "--arm", arm, "--dry-run", "--out", join(dir, "dry")])).code).toBe(0);
    const files = readdirSync(join(dir, "dry", "requests"));
    expect(files).toEqual(["f__a__p2.txt"]);
    const text = readFileSync(join(dir, "dry", "requests", files[0]!), "utf8");
    for (const value of ["1,234", "1,243", "5,678", "1,180", "1,204", "42.5", "7,777", "311"]) expect(text).not.toContain(value);
    expect(text).not.toContain("비밀 낱말");
    expect(text).toContain("▒");
  });

  it("시험지의 글이 원본과 다르면(값이 아닌 글자) 요청을 만들지 않는다", async () => {
    const changed = docA("1,243");
    changed.blocks = changed.blocks.map((block) => (block.id === "p2-b1" ? { ...block, text: "가상 판매량의 추이를 도표로 보인다. 덧붙인 글." } : block));
    const { dir, args } = setup(changed);
    const { code, lines } = await run(["runf", ...args, "--nominate", "value", "--arm", "ask", "--dry-run", "--out", join(dir, "dry")]);
    expect(code).toBe(1);
    expect(lines.join("\n")).toContain("원본으로 조립한 요청 글과 시험지로 조립한 요청 글이 다르다");
  });

  it("tablef: 기대 자리 파일과 심은 자리를 읽어 센다 (저장된 실행의 출력에서)", async () => {
    const { dir, args } = setup();
    const outcome = { arm: "ask", nominate: "value", prompt: "reconcile/ask-places@1", tally: {}, cost: { calls: 1, costUsd: 0.001 }, states: { "a:p2-t1:r1c0#n1": { state: "paired", counterparts: ["b:p2-t1:r1c1"] }, "a:p2-t1:r1c0#n2": { state: "none", reason: "not_in_other_document" } }, verdicts: [{ a: "p2-t1:r1c0#n1", b: "p2-t1:r1c1", outcome: "mismatch", reason: "values_differ", valuesDiffer: true }] };
    writeFileSync(join(dir, "outcome.json"), JSON.stringify(outcome));
    const gold = write(dir, "gold.json", { note: "synthetic", status: "synthetic", docs: { a: { file: "a", sha256: "0" }, b: { file: "b", sha256: "0" } }, prosePairs: [], groups: [] });
    const plants = write(dir, "plants.json", { note: "synthetic", status: "synthetic", seed: 1, plan: [], skipped: [], plants: [{ id: "X1", kind: "digits_transposed", isError: true, pool: "prose_pair", doc: "a", target: "p2-t1:r1c0#n1", original: "1,234", planted: "1,243", pairs: [], groups: [] }, { id: "X2", kind: "rounding_control", isError: false, pool: "prose_pair", doc: "a", target: "p2-t1:r1c0#n2", original: "5,678", planted: "5,678", pairs: [], groups: [] }] });
    const aims = write(dir, "aims.json", { aim1: [{ name: "가상 2023", doc: "a", id: "p2-t1:r1c0#n1", right: ["p2-t1:r1c1"] }, { name: "가상 2024", doc: "a", id: "p2-t1:r1c0#n2", right: ["p2-t1:r1c2"] }] });
    const table = await run(["tablef", ...args, "--gold2", gold, "--plants2", plants, "--aims", aims, "--runs", `r=${dir}`, "--out", join(dir, "table")]);
    expect(table.code).toBe(0);
    const report = JSON.parse(readFileSync(join(dir, "table", "focus.json"), "utf8")) as { runs: { grade: { expected: number; rightPaired: number; planted: number; plantedCaught: number; clean: number; cleanMatch: number } }[] };
    expect(report.runs[0]?.grade).toMatchObject({ expected: 2, rightPaired: 1, planted: 1, plantedCaught: 1, clean: 1, cleanMatch: 0 });
  });

  it("실제 공급사는 키·경로·상한이 없으면 부르지 않는다", async () => {
    const { dir, args } = setup();
    expect((await run(["runf", ...args, "--nominate", "value", "--arm", "ask", "--provider", "openai", "--route", "light-openai", "--out", join(dir, "live")])).code).toBe(2);
  });

  it.each(["ask", "ask2", "pair"])("저장된 답을 다시 채점한다 (%s): 모델을 부르지 않고 같은 결과를 낸다", async (arm) => {
    const { dir, args } = setup();
    expect((await run(["runf", ...args, "--nominate", "value", "--arm", arm, "--provider", "mock", "--out", join(dir, "mock")])).code).toBe(0);
    expect((await run(["runf", ...args, "--nominate", "value", "--arm", arm, "--replay", join(dir, "mock"), "--route", "mock", "--log", join(dir, "mock", "calls.jsonl"), "--out", join(dir, "again")])).code).toBe(0);
    const pick = (name: string) => { const outcome = JSON.parse(readFileSync(join(dir, name, "outcome.json"), "utf8")) as Record<string, unknown>; return [outcome.tally, outcome.states, outcome.verdicts]; };
    expect(pick("again")).toEqual(pick("mock"));
  });

  it("모의 실행은 돌지만 채점 표(tablef)는 모의 실행의 출력을 받지 않는다", async () => {
    const { dir, args } = setup();
    expect((await run(["runf", ...args, "--nominate", "value", "--arm", "ask", "--provider", "mock", "--out", join(dir, "mock")])).code).toBe(0);
    const outcome = JSON.parse(readFileSync(join(dir, "mock", "outcome.json"), "utf8")) as { synthetic?: string; tally: { asked: number; notNominated: number; unanswered: number } };
    expect(outcome.synthetic).toBeDefined();
    expect(outcome.tally).toMatchObject({ asked: 3, notNominated: 2, unanswered: 3 });
    const gold = write(dir, "gold.json", { note: "synthetic", status: "synthetic", docs: { a: { file: "a", sha256: "0" }, b: { file: "b", sha256: "0" } }, prosePairs: [], groups: [] });
    const plants = write(dir, "plants.json", { note: "synthetic", status: "synthetic", seed: 1, plan: [], skipped: [], plants: [] });
    const aims = write(dir, "aims.json", { aim1: [{ name: "가상", doc: "a", id: "p2-t1:r1c0#n2", right: ["p2-t1:r1c2"] }] });
    const table = await run(["tablef", ...args, "--gold2", gold, "--plants2", plants, "--aims", aims, "--runs", `m=${join(dir, "mock")}`, "--out", join(dir, "table")]);
    expect(table.code).toBe(1);
    expect(table.lines.join("\n")).toContain("모의 실행");
  });
});
