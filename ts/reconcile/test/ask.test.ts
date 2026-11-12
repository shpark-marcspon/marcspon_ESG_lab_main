import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { extractDocument, type DocModel } from "../src/index.js";
import { BRANCHES, buildAskRequest, combineAsk, judgeAsk, listTargets, maskName, planAsks, renderOther, renderSourcePages, renderTargetList, signature, stability, tallyAsk, targetKey, unmaskedInTargetList, type AskDoc, type AskPlacesAnswer, type AskState, type Target } from "../src/compare/ask.js";
import { coverage, gradeAsk, toJudgeAsk, type AskRun } from "../src/compare/ask-report.js";
import { main as cli5 } from "../src/compare/cli5.js";
import type { Gold2, Plant2 } from "../src/compare/exam2.js";
import { TallyError } from "../src/compare/hybrid.js";
import { pageTexts, type DocContext } from "../src/compare/methods.js";
import { unmaskedInIdDocument } from "../src/compare/prose.js";
import { asDocumentJson, syntheticDocument, tempDir, units } from "./helpers.js";

// 밴드 X (방법 Q: 낱개 숫자를 자리별로 묻기)를 지어낸 문서로 본다 (synthetic). 어느 회사의 실제 값도 아니다.

const HEADER = { rows: [0], cols: [0], basis: "rows_before_first_numeric_row" };
const NO_HEADER = { rows: [], cols: [], basis: "empty" };

/** 문서 a: 문장 속 숫자(값 하나, 세어 본 수 하나), 보통 표, 도표가 표로 잘못 실린 칸(값 둘이 한 칸에, 옆 칸은 숫자 칸). */
const docA = (ratio = "13.1%", bar = "1,234"): DocModel =>
  syntheticDocument(
    "a",
    [
      { id: "p1-t1", page: 1, cells: [["구분", "2023", "2024"], ["가상 배출량", "1,180", "1,204"]], header: HEADER },
      { id: "p2-t1", page: 2, cells: [["가상 판매량", ""], [`${bar} 5,678`, "42.5%"], ["2023 2024", ""]], header: NO_HEADER },
    ],
    [
      { id: "p1-b1", page: 1, text: `2024년 말 자본비율은 ${ratio}이며 3종의 보고서에 실었다.`, bbox: [40, 100, 400, 112] },
      { id: "p2-b1", page: 2, text: "가상 판매량은 전년보다 7.5% 늘었다.", bbox: [40, 100, 400, 112] },
    ],
  );
/** 문서 b: 요약 쪽(이름 바로 아래 큰 숫자), 이름 없는 숫자 블록, 문장, 표. */
const docB = (ratio = "13.1%"): DocModel =>
  syntheticDocument(
    "b",
    [{ id: "p1-t1", page: 1, cells: [["구분", "2023", "2024"], ["자본비율", "12.5%", ratio], ["가상 배출량", "1,180", "1,204"]], header: HEADER }],
    [
      { id: "p1-b1", page: 1, text: "주요 성과", bbox: [40, 90, 120, 104] },
      { id: "p1-b2", page: 1, text: "자본비율", bbox: [60, 120, 110, 130] },
      { id: "p1-b3", page: 1, text: ratio, bbox: [50, 131, 120, 156], kind: "heading" },
      { id: "p1-b4", page: 1, text: "58.4%", bbox: [300, 400, 360, 420] },
      { id: "p2-b1", page: 2, text: "가상 배출량은 1,204 tCO2eq 이다.", bbox: [40, 100, 400, 112] },
    ],
  );
const LABELS = { "a:p2-t1:r1c0#n1": "가상 판매량 · 계획 · 2023", "a:p2-t1:r1c0#n2": "가상 판매량 · 단기(1년)", "a:p2-t1:r1c1": "가상 판매량 · 2024" };

const ask = (model: DocModel, side: "a" | "b"): AskDoc & { context: DocContext } => {
  const extraction = extractDocument(model, side, { titles: "own", kinds: true });
  const blockText = new Map(model.blocks.filter((block) => block.text !== undefined).map((block) => [block.id, block.text as string]));
  return { model, extraction, context: { extraction, pages: pageTexts(extraction, blockText, new Map(model.blocks.map((block) => [block.id, block.page]))) } };
};
const docs = (a = docA(), b = docB()) => ({ a: ask(a, "a"), b: ask(b, "b") });
const targetsOf = (both: ReturnType<typeof docs>): Target[] => [...listTargets(both.a, "a", LABELS), ...listTargets(both.b, "b", LABELS)];
const keys = (targets: Target[], branch: string): string[] => targets.filter((target) => target.branch === branch).map(targetKey);

describe("대상 목록", () => {
  const both = docs();
  const targets = targetsOf(both);

  it("갈래별로: 이름이 묶인 숫자뿐인 블록, 코드 라벨이 실리는 값 후보, 종류가 값인 문장 속 숫자", () => {
    expect(keys(targets, "summary")).toEqual(["b:p1-b3#n1"]);
    expect(keys(targets, "chart")).toEqual(["a:p2-t1:r1c0#n1", "a:p2-t1:r1c0#n2", "a:p2-t1:r1c1"]);
    expect(keys(targets, "sentence")).toEqual(["a:p1-b1#n2", "a:p2-b1#n1", "b:p2-b1#n1"]);
  });

  it("대상이 아닌 것: 이름이 묶이지 않은 숫자 블록, 세어 본 수, 라벨 없는 표 칸", () => {
    const all = targets.map(targetKey);
    expect(all).not.toContain("b:p1-b4#n1");
    expect(both.a.extraction.kinds?.["p1-b1#n3"]).toBe("count");
    expect(all).not.toContain("a:p1-b1#n3");
    expect(all.some((key) => key.startsWith("a:p1-t1:") || key.startsWith("b:p1-t1:"))).toBe(false);
  });

  it("한 값 후보는 한 갈래에만 들고, 이름·라벨은 가린 글이다", () => {
    expect(new Set(targets.map(targetKey)).size).toBe(targets.length);
    expect(targets.find((target) => target.id === "p1-b3#n1")?.name).toBe("자본비율");
    expect(targets.find((target) => target.id === "p2-t1:r1c0#n2")?.name).toBe("가상 판매량 · 단기(▒년)");
    expect(maskName("목표 1,500 톤")).toBe("목표 ▒ 톤");
  });

  it("값이 다른 사본에서도 목록이 같다 (값이 목록을 바꾸지 않는다)", () => {
    expect(JSON.stringify(targetsOf(docs(docA("31.1%", "9,999"), docB("14.0%"))))).toBe(JSON.stringify(targets));
  });

  it("종류 옵션 없이 뽑은 것으로는 목록을 내지 않는다", () => {
    const model = docA();
    expect(() => listTargets({ model, extraction: extractDocument(model, "a", { titles: "own" }) }, "a", LABELS)).toThrow(TallyError);
  });

  it("대상 목록과 요청 조립의 코드는 정답·명세·판정 기록의 모듈을 읽지 않는다", () => {
    for (const file of ["ask.ts", "cli5.ts"]) {
      const source = readFileSync(join(__dirname, "../src/compare", file), "utf8");
      const imports = [...source.matchAll(/from "([^"]+)"/g)].map((match) => match[1] as string);
      expect(imports.filter((path) => /exam|grade|report|kind-|judg|gold/.test(path))).toEqual([]);
      expect(/gold|plants|judgments|prosePairs/i.test(source.replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "").replace(/`[^`]*`/g, ""))).toBe(false);
    }
  });
});

describe("묶기와 요청 조립", () => {
  const both = docs();
  const targets = targetsOf(both);

  it("한 요청에는 한 갈래·한 문서의 대상만, 상대 문서가 같은 요청끼리 이어진다", () => {
    const plans = planAsks(targets);
    expect(plans.map((plan) => plan.id)).toEqual(["q/summary-b/1", "q/sentence-b/1", "q/chart-a/1", "q/sentence-a/1"]);
    expect(plans.map((plan) => plan.other)).toEqual(["a", "a", "b", "b"]);
    expect(plans.flatMap((plan) => plan.targets.map(targetKey)).sort()).toEqual(targets.map(targetKey).sort());
    expect(plans.find((plan) => plan.id === "q/sentence-a/1")?.pages).toEqual([1, 2]);
  });

  it("한도를 넘으면 쪽 단위로 넘기고, 한 쪽이 한도보다 크면 그 쪽만 고르게 나눈다", () => {
    const many: Target[] = [...Array.from({ length: 3 }, (_, n) => ({ doc: "a" as const, id: `p1-b${n}#n1`, branch: "sentence" as const, page: 1, name: null })), ...Array.from({ length: 5 }, (_, n) => ({ doc: "a" as const, id: `p2-b${n}#n1`, branch: "sentence" as const, page: 2, name: null })), { doc: "a" as const, id: "p3-b1#n1", branch: "sentence" as const, page: 3, name: null }];
    const plans = planAsks(many, 4);
    expect(plans.map((plan) => [plan.pages, plan.targets.length])).toEqual([[[1], 3], [[2], 3], [[2], 2], [[3], 1]]);
    expect(planAsks(many, 9).map((plan) => plan.targets.length)).toEqual([9]);
    expect(planAsks(many, 8).map((plan) => [plan.pages, plan.targets.length])).toEqual([[[1, 2], 8], [[3], 1]]);
  });

  it("요청의 앞부분은 상대 문서 전체이고, 상대 문서가 같은 요청끼리 글자까지 같다", () => {
    const requests = planAsks(targets).map((plan) => buildAskRequest(plan, both, LABELS));
    const [summary, sentenceB, chart, sentenceA] = requests as [typeof requests[number], typeof requests[number], typeof requests[number], typeof requests[number]];
    expect(summary.vars.other).toBe(renderOther(both.a, "a", LABELS));
    expect(sentenceB.vars.other).toBe(summary.vars.other);
    expect(chart.vars.other).toBe(renderOther(both.b, "b", LABELS));
    expect(sentenceA.vars.other).toBe(chart.vars.other);
    expect(summary.vars.other.startsWith("# 문서 a (")).toBe(true);
    // 상대 문서에도 묶은 이름과 코드 라벨이 실린다.
    expect(chart.vars.other.split("\n")).toContain("자본비율: ▒%〔b:p1-b3#n1〕");
    expect(summary.vars.other).toContain("[가상 판매량 · 계획 · 2023: ▒〔a:p2-t1:r1c0#n1〕]");
    const prompt = readFileSync(join(__dirname, "../prompts/reconcile/ask-places/1.md"), "utf8");
    expect(prompt.indexOf("{{other}}")).toBeGreaterThan(0);
    expect(prompt.indexOf("{{other}}")).toBeLessThan(prompt.indexOf("{{source}}"));
    expect(prompt.indexOf("{{source}}")).toBeLessThan(prompt.indexOf("{{targets}}"));
  });

  it("대상이 든 쪽만 문맥으로 싣고, 대상마다 그 자리가 문맥 글에 있다", () => {
    const requests = planAsks(targets).map((plan) => buildAskRequest(plan, both, LABELS));
    const chart = requests.find((request) => request.plan.id === "q/chart-a/1")!;
    expect(chart.vars.source.split("\n").filter((line) => line.startsWith("## "))).toEqual(["## 2쪽"]);
    expect(chart.vars.targets.split("\n")).toEqual(["- a:p2-t1:r1c0#n1 | 도표의 값 | 가상 판매량 · 계획 · 2023", "- a:p2-t1:r1c0#n2 | 도표의 값 | 가상 판매량 · 단기(▒년)", "- a:p2-t1:r1c1 | 도표의 값 | 가상 판매량 · 2024"]);
    for (const request of requests) expect(request.missingInSource).toEqual([]);
    const summary = requests.find((request) => request.plan.id === "q/summary-b/1")!;
    expect(summary.vars.targets).toBe("- b:p1-b3#n1 | 요약 숫자 | 자본비율");
    const moved = buildAskRequest({ ...chart.plan, pages: [1] }, both, LABELS);
    expect(moved.missingInSource).toEqual(["a:p2-t1:r1c0#n1", "a:p2-t1:r1c0#n2", "a:p2-t1:r1c1"]);
  });
});

describe("대상 문맥의 가림", () => {
  const both = docs();
  const requests = planAsks(targetsOf(both)).map((plan) => buildAskRequest(plan, both, LABELS));
  const RAW = ["13.1", "1,234", "5,678", "42.5", "1,204", "1,180", "12.5", "58.4", "7.5"];

  it("상대 문서, 대상이 든 쪽, 대상 목록 어디에도 값의 글자가 없다", () => {
    for (const request of requests) {
      expect(request.unmasked).toEqual([]);
      for (const text of [request.vars.other, request.vars.source, request.vars.targets]) for (const raw of RAW) expect(text).not.toContain(raw);
    }
    const page = renderSourcePages(both.a, "a", [1, 2], LABELS);
    expect(page.split("\n")).toContain("2024년 말 자본비율은 ▒%〔a:p1-b1#n2〕이며 ▒〔a:p1-b1#n3〕종의 보고서에 실었다.");
    expect(unmaskedInIdDocument(page)).toEqual([]);
  });

  it("값이 다른 사본으로 조립해도 요청 글이 글자까지 같다", () => {
    const other = docs(docA("31.1%", "9,999"), docB("14.0%"));
    const again = planAsks(targetsOf(other)).map((plan) => buildAskRequest(plan, other, LABELS));
    expect(JSON.stringify(again.map((request) => request.vars))).toBe(JSON.stringify(requests.map((request) => request.vars)));
  });

  it("대상 목록의 이름에 값 꼴 숫자가 남으면 드러난다", () => {
    expect(unmaskedInTargetList("- a:p2-t1:r1c1 | 도표의 값 | 가상 판매량 · 2024")).toEqual([]);
    expect(unmaskedInTargetList("- a:p2-t1:r1c1 | 도표의 값 | 가상 판매량 1,234 톤").length).toBeGreaterThan(0);
    expect(unmaskedInTargetList("- 꼴이 다른 줄")).toEqual(["- 꼴이 다른 줄"]);
    expect(unmaskedInTargetList(renderTargetList([{ doc: "a", id: "p1-b1#n2", branch: "sentence", page: 1, name: null }]))).toEqual([]);
  });
});

describe("답 확인", () => {
  const both = docs(docA("13.1%"), docB("31.1%"));
  const targets = targetsOf(both);
  const plans = planAsks(targets);
  const plan = (id: string) => plans.find((item) => item.id === id)!;
  const judge = (id: string, answers: AskPlacesAnswer["answers"]) => judgeAsk({ answers }, plan(id), both.a.context, both.b.context, units);
  const NONE = { counterparts: [], unit: null };

  it("상대를 답하면 코드가 값을 견준다. 한 대상에 상대가 여럿이면 전부", () => {
    const out = judge("q/summary-b/1", [{ target: "b:p1-b3#n1", counterparts: [{ id: "a:p1-b1#n2", unit: "%" }, { id: "a:p2-t1:r1c1", unit: "%" }], reason: null, unit: "%" }]);
    expect(out.states.get("b:p1-b3#n1")).toEqual({ state: "paired", counterparts: ["a:p1-b1#n2", "a:p2-t1:r1c1"] });
    expect(out.verdicts.map((verdict) => [verdict.a, verdict.b, verdict.outcome])).toEqual([["p1-b1#n2", "p1-b3#n1", "mismatch"], ["p2-t1:r1c1", "p1-b3#n1", "mismatch"]]);
    const same = judge("q/sentence-a/1", [{ target: "a:p1-b1#n2", counterparts: [{ id: "b:p1-t1:r1c1", unit: "%" }], reason: null, unit: "%" }, { target: "a:p2-b1#n1", ...NONE, reason: "not_in_other_document" }]);
    expect(same.verdicts).toHaveLength(1);
    expect(same.verdicts[0]).toMatchObject({ a: "p1-b1#n2", b: "p1-t1:r1c1", from: ["a:p1-b1#n2"] });
  });

  it("「없음」은 사유와 함께 남고 짝을 내지 않는다. 답이 안 온 대상은 「답 없음」", () => {
    const out = judge("q/sentence-a/1", [{ target: "a:p2-b1#n1", ...NONE, reason: "undecidable" }]);
    expect(out.states.get("a:p2-b1#n1")).toEqual({ state: "none", reason: "undecidable" });
    expect(out.states.get("a:p1-b1#n2")).toEqual({ state: "unanswered" });
    expect(out.verdicts).toEqual([]);
    expect(judgeAsk(null, plan("q/sentence-a/1"), both.a.context, both.b.context, units).states.get("a:p2-b1#n1")).toEqual({ state: "unanswered" });
  });

  it("대상이 아닌 자리에 대한 답은 버린다 (문서에 있는 자리여도)", () => {
    const out = judge("q/sentence-a/1", [{ target: "a:p1-t1:r1c2", counterparts: [{ id: "b:p1-t1:r2c2", unit: null }], reason: null, unit: null }, { target: "b:p1-b3#n1", counterparts: [{ id: "a:p1-b1#n2", unit: null }], reason: null, unit: null }, { target: "a:p9-b9#n9", ...NONE, reason: "undecidable" }]);
    expect(out.rejected).toEqual({ not_a_target: 2, unknown_id: 1 });
    expect(out.verdicts).toEqual([]);
    expect([...out.states.keys()].sort()).toEqual(["a:p1-b1#n2", "a:p2-b1#n1"]);
    expect([...out.states.values()].every((state) => state.state === "unanswered")).toBe(true);
  });

  it("상대 자리: 없는 ID, 같은 문서의 자리, 원문에 없는 단위, 같은 상대 두 번은 그 자리만 버린다", () => {
    const out = judge("q/sentence-a/1", [{ target: "a:p1-b1#n2", counterparts: [{ id: "b:p7-t1:r1c1", unit: null }, { id: "a:p2-t1:r1c1", unit: null }, { id: "b:p1-t1:r1c2", unit: "억 원" }, { id: "b:p1-t1:r1c1", unit: null }, { id: "b:p1-t1:r1c1", unit: null }], reason: null, unit: null }]);
    expect(out.rejected).toEqual({ unknown_id: 1, same_document: 1, unit_not_in_source: 1, duplicate_counterpart: 1 });
    expect(out.states.get("a:p1-b1#n2")).toEqual({ state: "paired", counterparts: ["b:p1-t1:r1c1"] });
    expect(out.verdicts).toHaveLength(1);
  });

  it("받아들인 상대가 하나도 없으면 그 대상의 답은 「버려진 답」이다", () => {
    const out = judge("q/sentence-a/1", [{ target: "a:p1-b1#n2", counterparts: [{ id: "a:p2-b1#n1", unit: null }], reason: null, unit: null }, { target: "a:p2-b1#n1", counterparts: [{ id: "b:p1-t1:r2c2", unit: null }], reason: null, unit: "억 원" }]);
    expect(out.states.get("a:p1-b1#n2")).toEqual({ state: "answer_rejected", why: "no_valid_counterpart" });
    expect(out.states.get("a:p2-b1#n1")).toEqual({ state: "answer_rejected", why: "unit_not_in_source" });
    expect(out.rejected).toEqual({ same_document: 1, unit_not_in_source: 1 });
    expect(out.verdicts).toEqual([]);
  });

  it("상대도 사유도 없는 답, 한 대상에 엇갈린 답은 버리고, 같은 답 두 번은 한 번만 받는다", () => {
    const twice = { target: "a:p2-b1#n1", ...NONE, reason: "not_in_other_document" as const };
    const out = judge("q/sentence-a/1", [{ target: "a:p1-b1#n2", ...NONE, reason: null }, twice, twice]);
    expect(out.states.get("a:p1-b1#n2")).toEqual({ state: "answer_rejected", why: "neither_counterpart_nor_reason" });
    expect(out.states.get("a:p2-b1#n1")).toEqual({ state: "none", reason: "not_in_other_document" });
    expect(out.rejected).toEqual({ neither_counterpart_nor_reason: 1, duplicate_answer: 1 });
    const clash = judge("q/sentence-a/1", [twice, { ...twice, reason: "undecidable" }]);
    expect(clash.states.get("a:p2-b1#n1")).toEqual({ state: "answer_rejected", why: "conflicting_answers" });
    expect(clash.rejected).toEqual({ conflicting_answers: 2 });
  });
});

describe("셈", () => {
  const both = docs();
  const targets = targetsOf(both);
  const plans = planAsks(targets);
  const answer = (id: string, answers: AskPlacesAnswer["answers"]) => judgeAsk({ answers }, plans.find((item) => item.id === id)!, both.a.context, both.b.context, units);
  const judged = [
    answer("q/summary-b/1", [{ target: "b:p1-b3#n1", counterparts: [{ id: "a:p1-b1#n2", unit: null }], reason: null, unit: null }]),
    answer("q/sentence-b/1", [{ target: "b:p2-b1#n1", counterparts: [], reason: "not_in_other_document", unit: null }]),
    answer("q/chart-a/1", [{ target: "a:p2-t1:r1c0#n1", counterparts: [], reason: "undecidable", unit: null }, { target: "a:p2-t1:r1c0#n2", counterparts: [], reason: "not_in_other_document", unit: null }]),
    answer("q/sentence-a/1", [{ target: "a:p1-b1#n2", counterparts: [{ id: "b:p1-b3#n1", unit: null }, { id: "b:p1-t1:r1c2", unit: null }], reason: null, unit: null }, { target: "a:p2-b1#n1", counterparts: [{ id: "a:p1-b1#n2", unit: null }], reason: null, unit: null }]),
  ];
  const outcome = combineAsk(judged);

  it("모든 대상이 결과 하나에 들고, 갈래마다 합이 대상 수와 같다", () => {
    const tally = tallyAsk(targets, outcome.states);
    expect(tally.total).toBe(7);
    expect(tally.summary).toEqual({ total: 1, paired: 1, none: { not_in_other_document: 0, undecidable: 0 }, unanswered: 0, rejected: 0 });
    expect(tally.chart).toEqual({ total: 3, paired: 0, none: { not_in_other_document: 1, undecidable: 1 }, unanswered: 1, rejected: 0 });
    expect(tally.sentence).toEqual({ total: 3, paired: 1, none: { not_in_other_document: 1, undecidable: 0 }, unanswered: 0, rejected: 1 });
    for (const branch of BRANCHES) expect(tally[branch].paired + tally[branch].none.not_in_other_document + tally[branch].none.undecidable + tally[branch].unanswered + tally[branch].rejected).toBe(tally[branch].total);
  });

  it("「없음」은 「상대를 답함」으로 세지 않는다", () => {
    const allNone = new Map<string, AskState>(targets.map((target) => [targetKey(target), { state: "none", reason: "not_in_other_document" }]));
    const tally = tallyAsk(targets, allNone);
    expect(BRANCHES.map((branch) => tally[branch].paired)).toEqual([0, 0, 0]);
    expect(BRANCHES.map((branch) => tally[branch].none.not_in_other_document)).toEqual([1, 3, 3]);
  });

  it("양쪽 대상이 같은 짝을 답하면 짝은 하나이고 답한 대상이 둘 남는다", () => {
    const pair = outcome.verdicts.find((verdict) => verdict.a === "p1-b1#n2" && verdict.b === "p1-b3#n1");
    expect(pair?.from.sort()).toEqual(["a:p1-b1#n2", "b:p1-b3#n1"]);
    expect(outcome.verdicts).toHaveLength(2);
    expect(pair?.outcome).toBe("match");
  });

  it("합이 맞지 않으면 수를 내지 않는다: 결과 없는 대상, 대상이 아닌 자리의 답, 두 요청에 실린 대상", () => {
    const missing = new Map(outcome.states);
    missing.delete("a:p2-b1#n1");
    expect(() => tallyAsk(targets, missing)).toThrow(TallyError);
    expect(() => tallyAsk(targets, new Map([...outcome.states, ["a:p1-t1:r1c2", { state: "unanswered" } as AskState]]))).toThrow(TallyError);
    expect(() => combineAsk([judged[0]!, judged[0]!])).toThrow(TallyError);
  });

  it("흔들림: 실행들이 대상마다 같은 답을 냈는지 갈래별로 센다", () => {
    const second = new Map(outcome.states);
    second.set("b:p1-b3#n1", { state: "paired", counterparts: ["a:p2-t1:r1c1"] });
    second.set("a:p2-t1:r1c0#n1", { state: "none", reason: "not_in_other_document" });
    const third = new Map(second);
    third.set("b:p1-b3#n1", { state: "none", reason: "undecidable" });
    const strict = stability(targets, [outcome.states, second, third]);
    expect(strict.summary).toEqual({ total: 1, allSame: 0, majority: 0, split: 1 });
    expect(strict.chart).toEqual({ total: 3, allSame: 2, majority: 1, split: 0 });
    expect(stability(targets, [outcome.states, second, third], true).chart.allSame).toBe(3);
    expect(signature({ state: "paired", counterparts: ["b:y", "b:x"] })).toBe("paired:b:x,b:y");
  });
});

describe("채점", () => {
  const both = docs(docA("13.1%"), docB("31.1%"));
  const targets = targetsOf(both);
  const gold = { note: "", status: "synthetic", docs: { a: { file: "", sha256: "" }, b: { file: "", sha256: "" } }, groups: [], prosePairs: [
    { id: "N1", item: "자본비율", period: "2024", a: "p1-b1#n2", b: "p1-b3#n1", rawA: "13.1%", rawB: "13.1%", control: null, evidence: "synthetic" },
    { id: "N2", item: "가상 배출량", period: "2024", a: "p1-t1:r1c2", b: "p2-b1#n1", rawA: "1,204", rawB: "1,204", control: null, evidence: "synthetic" },
    { id: "N3", item: "가상 배출량 (표 칸끼리는 대상이 아님)", period: "2023", a: "p1-t1:r1c1", b: "p1-t1:r2c1", rawA: "1,180", rawB: "1,180", control: null, evidence: "synthetic" },
  ] } satisfies Gold2;
  const plant: Plant2 = { id: "U1", kind: "digits_transposed", isError: true, pool: "prose_pair", doc: "b", target: "p1-b3#n1", original: "13.1%", planted: "31.1%", pairs: ["N1"], groups: [] };
  const planted = [{ plant: "U1", kind: plant.kind, isError: true, doc: "b" as const, target: "p1-b3#n1", original: "13.1%", planted: "31.1%" }];
  const plans = planAsks(targets);
  const run = (name: string, answers: Record<string, AskPlacesAnswer["answers"]>): AskRun => {
    const outcome = combineAsk(plans.map((plan) => judgeAsk({ answers: answers[plan.id] ?? [] }, plan, both.a.context, both.b.context, units)));
    return { name, targets, states: outcome.states, verdicts: outcome.verdicts, rejected: outcome.rejected, tally: tallyAsk(targets, outcome.states), cost: null };
  };
  const prose = (side: "a" | "b", id: string): boolean => both[side].extraction.facts.find((fact) => fact.id === id)?.bucket !== "table_cell";
  const grade = (item: AskRun) => gradeAsk(item, gold, [], planted, [plant], prose);

  it("대상 목록에 정답의 자리가 몇 개 들었는지 센다 (까닭과 함께)", () => {
    const cover = coverage(gold, [plant], targets, { a: both.a.extraction, b: both.b.extraction });
    expect([cover.reachable, cover.total]).toEqual([2, 3]);
    expect(cover.pairs.map((pair) => [pair.a.branch, pair.b.branch])).toEqual([["sentence", "summary"], [null, "sentence"], [null, null]]);
    expect([cover.places.targets, cover.places.total]).toEqual([3, 6]);
    expect(Object.values(cover.places.missing).flat().sort()).toEqual(["a:p1-t1:r1c1", "a:p1-t1:r1c2", "b:p1-t1:r2c1"]);
    expect(cover.plants).toEqual([{ plant: "U1", kind: "digits_transposed", target: "b:p1-b3#n1", branch: "summary", pairs: ["N1"], reachable: true }]);
  });

  it("심은 오류: 정답의 짝을 답하면 잡음, 「없음」이면 조용히 지나감이고 놓침으로 센다", () => {
    const caught = grade(run("Q-1", { "q/summary-b/1": [{ target: "b:p1-b3#n1", counterparts: [{ id: "a:p1-b1#n2", unit: null }], reason: null, unit: null }] }));
    expect(caught.plants).toEqual([{ plant: "U1", branch: "summary", pairs: ["N1"], status: "caught" }]);
    expect(caught.goldTargets.find((item) => item.target === "b:p1-b3#n1")?.status).toBe("found_all");
    expect(caught.goldTargets.find((item) => item.target === "a:p1-b1#n2")?.status).toBe("unanswered");
    const silent = grade(run("Q-2", { "q/summary-b/1": [{ target: "b:p1-b3#n1", counterparts: [], reason: "not_in_other_document", unit: null }] }));
    expect(silent.plants[0]?.status).toBe("silent");
    expect(silent.goldTargets.find((item) => item.target === "b:p1-b3#n1")).toMatchObject({ status: "none", reason: "not_in_other_document" });
    expect(silent.goldTargets.map((item) => item.target).sort()).toEqual(["a:p1-b1#n2", "b:p1-b3#n1", "b:p2-b1#n1"]);
  });

  it("정답 밖의 짝을 가르고, 불일치는 전부·일치는 표본으로 판정 대상을 낸다", () => {
    const one = run("Q-1", { "q/summary-b/1": [{ target: "b:p1-b3#n1", counterparts: [{ id: "a:p2-t1:r1c1", unit: null }], reason: null, unit: null }], "q/sentence-b/1": [{ target: "b:p2-b1#n1", counterparts: [{ id: "a:p1-t1:r1c2", unit: null }, { id: "a:p1-t1:r1c1", unit: null }], reason: null, unit: null }] });
    const graded = grade(one);
    expect(graded.outside.map((verdict) => [verdict.a, verdict.b, verdict.outcome])).toEqual([["p2-t1:r1c1", "p1-b3#n1", "mismatch"], ["p1-t1:r1c1", "p2-b1#n1", "needs_explanation"]]);
    expect(graded.goldTargets.find((item) => item.target === "b:p1-b3#n1")?.status).toBe("paired_elsewhere");
    const judge = toJudgeAsk([{ run: one, grade: graded }]);
    expect(judge.pairs.every((pair) => pair.judge)).toBe(true);
    expect(judge.matchOnly).toMatchObject({ total: 0, sampled: 0 });
    expect(judge.summary).toEqual([{ target: "b:p1-b3#n1", name: "자본비율", answers: { "Q-1": "paired:a:p2-t1:r1c1" } }]);
  });
});

describe("명령 (cli5)", () => {
  const write = (dir: string, name: string, value: unknown): string => {
    const path = join(dir, name);
    writeFileSync(path, JSON.stringify(value));
    return path;
  };
  const setup = (examA = docA(), examB = docB()) => {
    const dir = tempDir();
    const args = ["--a", write(dir, "a.json", asDocumentJson(docA())), "--b", write(dir, "b.json", asDocumentJson(docB())), "--exam-a", write(dir, "exam-a.json", asDocumentJson(examA)), "--exam-b", write(dir, "exam-b.json", asDocumentJson(examB)), "--chart-labels", write(dir, "labels.json", { a: Object.fromEntries(Object.entries(LABELS).map(([key, text]) => [key.slice(2), text])), b: {} })];
    return { dir, args };
  };
  const run = async (args: string[]): Promise<{ code: number; lines: string[] }> => {
    const lines: string[] = [];
    const code = await cli5(args, (line) => lines.push(line), () => undefined, { env: {} });
    return { code, lines };
  };

  it("dry-run: 요청 글을 쓰고, 값이 다른 시험지로도 통과하며 요청 글에 값이 없다", async () => {
    const { dir, args } = setup(docA("31.1%", "9,999"), docB("14.0%"));
    const { code } = await run(["runq", ...args, "--dry-run", "--out", join(dir, "dry")]);
    expect(code).toBe(0);
    const report = JSON.parse(readFileSync(join(dir, "dry", "dry-run.json"), "utf8")) as { calls: number; maskCheck: { unmaskedValues: number; sameAsOriginalDocuments: boolean; targets: number } };
    expect(report.calls).toBe(4);
    expect(report.maskCheck).toMatchObject({ unmaskedValues: 0, sameAsOriginalDocuments: true, targets: 7 });
    const text = readFileSync(join(dir, "dry", "requests", "q__chart-a__1.txt"), "utf8");
    for (const raw of ["9,999", "31.1", "14.0", "1,234", "13.1"]) expect(text).not.toContain(raw);
  });

  it("값 후보의 목록이 원본과 다른 시험지는 받지 않는다", async () => {
    const broken = syntheticDocument("a", [], [{ id: "p1-b1", page: 1, text: "자본비율은 13.1%이다.", bbox: [40, 100, 400, 112] }]);
    const { dir, args } = setup(broken);
    const { code, lines } = await run(["runq", ...args, "--dry-run", "--out", join(dir, "dry")]);
    expect(code).toBe(1);
    expect(lines.join("\n")).toContain("대상 목록이 다르다");
  });

  it("모의 실행: 빈 답이면 모든 대상이 「답 없음」이고 결과에 모의 표시가 붙는다", async () => {
    const { dir, args } = setup();
    const { code } = await run(["runq", ...args, "--provider", "mock", "--out", join(dir, "mock")]);
    expect(code).toBe(0);
    const outcome = JSON.parse(readFileSync(join(dir, "mock", "outcome.json"), "utf8")) as { synthetic?: string; tally: { total: number; chart: { unanswered: number } }; verdicts: unknown[] };
    expect(outcome.synthetic).toContain("모의");
    expect(outcome.tally.total).toBe(7);
    expect(outcome.tally.chart.unanswered).toBe(3);
    expect(outcome.verdicts).toEqual([]);
  });

  it("실제 공급사 호출은 상한과 밴드 공용 호출 기록 없이는 시작하지 않는다", async () => {
    const { dir, args } = setup();
    expect((await run(["runq", ...args, "--provider", "openai", "--route", "light-openai", "--out", join(dir, "live")])).code).toBe(2);
    expect((await run(["runq", ...args, "--provider", "openai", "--route", "light-openai", "--limit-usd", "0.2", "--out", join(dir, "live")])).code).toBe(2);
  });

  it("targets: 정답 없이 대상 목록과 요청 계획을 낸다", async () => {
    const { dir, args } = setup();
    expect((await run(["targets", ...args, "--out", join(dir, "t")])).code).toBe(0);
    const file = JSON.parse(readFileSync(join(dir, "t", "targets.json"), "utf8")) as { counts: { total: number }; targets: Target[] };
    expect(file.counts.total).toBe(7);
    expect((await run(["targets", ...args, "--gold2", "x", "--out", join(dir, "t")])).code).toBe(2);
  });
});
