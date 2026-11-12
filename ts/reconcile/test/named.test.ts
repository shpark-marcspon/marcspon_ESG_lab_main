import { describe, expect, it } from "vitest";
import { extractDocument, type DocModel, type Extraction, type Fact } from "../src/index.js";
import { classifyKinds, isNonValue, kindOf, type ValueKind } from "../src/domain/kinds.js";
import { TallyError } from "../src/compare/hybrid.js";
import { checkKindGold, drawKindTargets, type KindGold } from "../src/compare/kind-exam.js";
import { nonValuePlaces, reclassify, scoreKinds } from "../src/compare/kind-report.js";
import { idsInText, pickNameBlocks, renderDocumentWithIds, unmaskedInIdDocument } from "../src/compare/prose.js";
import { MASK } from "../src/compare/render.js";
import { syntheticDocument } from "./helpers.js";

// 밴드 V (요약 숫자의 이름 묶기, 값 후보의 종류)를 지어낸 문서로 본다 (synthetic). 어느 회사의 실제 값도 아니다.

const HEADER = { rows: [0], cols: [0], basis: "rows_before_first_numeric_row" };
/** 요약 쪽: 이름(작은 글자) 바로 아래에 숫자(큰 글자)가 따로 놓인 블록들, 그리고 도표의 축 눈금처럼 이름 없이 쌓인 숫자들. */
const summary = (): DocModel =>
  syntheticDocument(
    "s",
    [{ id: "p1-t1", page: 1, cells: [["구분", "2023", "2024"], ["자본비율", "12.5%", "13.1%"]], header: HEADER }],
    [
      { id: "p1-b1", page: 1, text: "주요 성과", bbox: [40, 90, 120, 104] },
      { id: "p1-b2", page: 1, text: "자본비율", bbox: [60, 120, 110, 130] },
      { id: "p1-b3", page: 1, text: "13.1%", bbox: [50, 131, 120, 156], kind: "heading" },
      { id: "p1-b4", page: 1, text: "총자산", bbox: [160, 120, 200, 130] },
      { id: "p1-b5", page: 1, text: "742조 원", bbox: [150, 131, 215, 156], kind: "heading" },
      // 순서로는 바로 앞이지만 가로로 겹치지 않는 글: 이름으로 고르면 안 된다.
      { id: "p1-b6", page: 1, text: "안전 점검", bbox: [400, 300, 440, 310] },
      { id: "p1-b7", page: 1, text: "30.0", bbox: [40, 305, 56, 313] },
      { id: "p1-b8", page: 1, text: "20.0", bbox: [40, 345, 56, 353] },
      { id: "p1-b9", page: 1, text: "10.0", bbox: [40, 385, 56, 393] },
      // 숫자보다 글자가 큰 제목, 값 후보가 든 문장: 이름으로 고르지 않는다.
      { id: "p2-b1", page: 2, text: "큰 제목", bbox: [40, 100, 200, 140] },
      { id: "p2-b2", page: 2, text: "58.4%", bbox: [40, 141, 100, 160] },
      { id: "p2-b3", page: 2, text: "전년에는 1,204명이었다.", bbox: [300, 100, 420, 110] },
      { id: "p2-b4", page: 2, text: "1,251명", bbox: [300, 111, 380, 136] },
    ],
  );
const own = (document: DocModel, kinds = false): Extraction => extractDocument(document, "s", { titles: "own", ...(kinds ? { kinds: true } : {}) });

describe("이름 블록 고르기", () => {
  const document = summary();
  const picked = pickNameBlocks(document, own(document));

  it("숫자 블록 바로 위에 있고 가로로 겹치는 작은 글 블록을 이름으로 고른다", () => {
    expect(picked.bound.get("p1-b3")).toBe("p1-b2");
    expect(picked.bound.get("p1-b5")).toBe("p1-b4");
  });

  it("후보가 없으면 묶지 않고 그 수를 남긴다: 이름 없이 쌓인 눈금, 순서로만 가까운 글, 더 큰 글자, 값이 든 문장", () => {
    expect(picked.unbound.sort()).toEqual(["p1-b7", "p1-b8", "p1-b9", "p2-b2", "p2-b4"]);
    expect(picked.bound.size + picked.unbound.length).toBe(7);
    // `30.0` 의 바로 앞 순서는 「안전 점검」이지만 가로로 겹치지 않는다.
    expect([...picked.bound.values()]).not.toContain("p1-b6");
  });

  it("위에 둘이 겹치면 세로로 가까운 것을 고르고, 한 이름은 한 숫자에만 묶는다", () => {
    const two = syntheticDocument("s", [], [
      { id: "p1-b1", page: 1, text: "먼 이름", bbox: [50, 100, 110, 110] },
      { id: "p1-b2", page: 1, text: "가까운 이름", bbox: [50, 121, 110, 130] },
      { id: "p1-b3", page: 1, text: "13.1%", bbox: [50, 131, 120, 156] },
      { id: "p1-b4", page: 1, text: "14.2%", bbox: [50, 133, 120, 158] },
    ]);
    const result = pickNameBlocks(two, own(two));
    expect(result.bound.get("p1-b3")).toBe("p1-b2");
    expect(result.bound.get("p1-b4")).toBeUndefined();
    expect(result.unbound).toEqual(["p1-b4"]);
  });
});

describe("묶은 줄의 가림", () => {
  const document = summary();
  const extraction = own(document);
  const bound = renderDocumentWithIds(document, extraction, { prefix: "b:", bindNames: true });
  const plain = renderDocumentWithIds(document, extraction, { prefix: "b:" });

  it("이름과 숫자가 한 줄에 실리고, 값의 글자는 남지 않고, ID 는 그대로다", () => {
    expect(bound.split("\n")).toContain(`자본비율: ${MASK}%〔b:p1-b3#n1〕`);
    expect(bound.split("\n")).toContain(`총자산: ${MASK}〔b:p1-b5#n1〕조 원`);
    for (const value of ["13.1", "742", "30.0", "20.0", "10.0", "58.4", "1,204", "1,251", "12.5"]) expect(bound).not.toContain(value);
    expect(unmaskedInIdDocument(bound)).toEqual([]);
    expect(idsInText(bound).sort()).toEqual(idsInText(plain).sort());
    expect(idsInText(bound).sort()).toEqual(extraction.facts.filter((fact) => fact.bucket !== "table_cell").map((fact) => fact.id).sort());
  });

  it("묶지 못한 숫자 블록은 제 줄에 그대로 실린다", () => {
    expect(bound.split("\n")).toContain(`${MASK}〔b:p1-b7#n1〕`);
    expect(bound.split("\n")).toContain(`${MASK}%〔b:p2-b2#n1〕`);
    expect(bound).not.toContain("안전 점검:");
    expect(bound).not.toContain("큰 제목:");
  });

  it("묶기를 끄면 요청 글이 밴드 U 의 것과 같다 (옵션이 없을 때와 false 일 때가 같고, 이름과 숫자가 다른 줄이다)", () => {
    expect(renderDocumentWithIds(document, extraction, { prefix: "b:", bindNames: false })).toBe(plain);
    expect(plain.split("\n")).toContain("자본비율");
    expect(plain.split("\n")).toContain(`${MASK}%〔b:p1-b3#n1〕`);
    expect(plain).not.toContain("자본비율:");
    // 묶은 글은 줄바꿈 하나가 `: ` 로 바뀐 것뿐이다.
    expect(bound.replace(`자본비율: ${MASK}`, `자본비율\n${MASK}`).replace(`총자산: ${MASK}`, `총자산\n${MASK}`)).toBe(plain);
  });
});

describe("값 후보의 종류", () => {
  const kindIn = (text: string, raw: string, nth = 1): ValueKind => {
    const document = syntheticDocument("s", [], [{ id: "p1-b1", page: 1, text }]);
    const extraction = own(document, true);
    const fact = extraction.facts.filter((item) => item.raw === raw)[nth - 1] as Fact | undefined;
    if (!fact) throw new Error(`값 후보가 아니다: ${raw} (${text})`);
    return (extraction.kinds as Record<string, ValueKind>)[fact.id] as ValueKind;
  };

  it("값: 잰 양·금액·비율·목표값·문턱값", () => {
    expect(kindIn("2024년 배출량은 39,604 tCO2eq 이다.", "39,604")).toBe("value");
    expect(kindIn("임직원은 1,251명이다.", "1,251")).toBe("value");
    expect(kindIn("감축률은 17.9% 이며 목표는 42% 감축이다.", "42%")).toBe("value");
    expect(kindIn("영업점은 414개소이다.", "414")).toBe("value");
    expect(kindIn("이사 9명 중 3명이 전문가다.", "3")).toBe("value");
    expect(kindIn("총자산은 742조 원이다.", "742")).toBe("value");
  });

  it("식별자: 규격 번호, 이름의 일부, 각주·조항·쪽 번호", () => {
    expect(kindIn("검증은 ABCD 4100 에 따라 수행하였다.", "4100")).toBe("identifier");
    expect(kindIn("검증은 ABCD 4100에서 정한 절차를 따랐다.", "4100")).toBe("identifier");
    expect(kindIn("내부 배출량(Scope 1&2)은 줄었다.", "2")).toBe("identifier");
    expect(kindIn("Scope 1, 2, 3 배출량을 쓴다.", "3")).toBe("identifier");
    expect(kindIn("집약도*1 를 관리한다.", "1")).toBe("identifier");
    expect(kindIn("문단 14(1)에 따라 공시한다.", "14")).toBe("identifier");
    expect(kindIn("세부 현황은 50 페이지에서 확인한다.", "50")).toBe("identifier");
    expect(kindIn("32 소비자 보호", "32")).toBe("identifier");
    // 반례: 약어 뒤라도 단위가 붙은 수는 값이다.
    expect(kindIn("ABCD 7.8조 원 증가", "7.8")).toBe("value");
    expect(kindIn("ABCD 3명", "3")).toBe("value");
  });

  it("기간: 몇 년·개월·개년, 날짜의 조각. 반례: 소수가 붙은 연수는 값으로 남긴다", () => {
    expect(kindIn("장기(10년) 시점의 영향", "10")).toBe("duration");
    expect(kindIn("최근 3개년 자료를 실었다.", "3")).toBe("duration");
    expect(kindIn("복귀 후 12개월 이상 근속", "12")).toBe("duration");
    expect(kindIn("평균 근속은 16.1년이다.", "16.1")).toBe("value");
  });

  it("개수: 구성을 센 수와 정해 둔 빈도. 반례: 큰 수, 실적의 횟수", () => {
    expect(kindIn("보고서를 3종으로 나누어 낸다.", "3")).toBe("count");
    expect(kindIn("4대 핵심요소를 중심으로", "4")).toBe("count");
    expect(kindIn("5개 주제를 선정하였다.", "5")).toBe("count");
    expect(kindIn("교육을 연 1회 이상 실시한다.", "1")).toBe("count");
    expect(kindIn("국내 영업점 1,449 개", "1,449")).toBe("value");
    expect(kindIn("위원회를 총 3회 개최하였다.", "3")).toBe("value");
  });

  it("눈금: 척도, 기준 단위, 같은 간격으로 쌓인 숫자뿐인 블록, 한 칸에 든 축 눈금", () => {
    expect(kindIn("파급효과를 5점 척도로 평가", "5")).toBe("scale");
    expect(kindIn("근로자 10,000명당 사망자 수", "10,000")).toBe("scale");
    const document = summary();
    const kinds = own(document, true).kinds as Record<string, ValueKind>;
    expect([kinds["p1-b7#n1"], kinds["p1-b8#n1"], kinds["p1-b9#n1"]]).toEqual(["scale", "scale", "scale"]);
    expect(kinds["p1-b3#n1"]).toBe("value");
    expect(kinds["p2-b2#n1"]).toBe("value");
    const chart = syntheticDocument("s", [{ id: "p1-t1", page: 1, cells: [["300.0 200.0 100.0", "157.2 149.8"], ["600 400", "18.2 18.1"]], header: { rows: [], cols: [], basis: "none" } }]);
    const chartKinds = own(chart, true).kinds as Record<string, ValueKind>;
    expect(chartKinds["p1-t1:r0c0#n2"]).toBe("scale");
    expect(chartKinds["p1-t1:r1c0#n1"]).toBe("scale");
    expect(chartKinds["p1-t1:r0c1#n1"]).toBe("value");
    expect(chartKinds["p1-t1:r1c1#n2"]).toBe("value");
  });

  it("연도 표시: 표의 숫자 칸에 든 쉼표 없는 연도 꼴. 쉼표가 있으면 값이다", () => {
    const document = syntheticDocument("s", [{ id: "p1-t1", page: 1, cells: [["구분", "기준", "실적"], ["목표 연도", "2030", "2,030"]], header: HEADER }]);
    const kinds = own(document, true).kinds as Record<string, ValueKind>;
    expect(kinds["p1-t1:r1c1"]).toBe("year_label");
    expect(kinds["p1-t1:r1c2"]).toBe("value");
    expect(isNonValue("year_label")).toBe(true);
    expect(isNonValue("value")).toBe(false);
  });

  it("종류 옵션을 끄면 값 후보 목록이 기존과 같고, 켜도 값 후보는 버려지지 않는다", () => {
    const document = summary();
    const off = extractDocument(document, "s", { titles: "own" });
    const on = extractDocument(document, "s", { titles: "own", kinds: true });
    expect(off.kinds).toBeUndefined();
    expect(JSON.stringify(off.facts)).toBe(JSON.stringify(on.facts));
    expect(JSON.stringify(off.nonValues)).toBe(JSON.stringify(on.nonValues));
    expect(JSON.stringify(extractDocument(document, "s", { titles: "own", kinds: false }))).toBe(JSON.stringify(off));
    // 「값이 아님」으로 갈린 자리도 값 후보 목록에 그대로 있다.
    expect(off.facts.map((fact) => fact.id)).toContain("p1-b7#n1");
    expect(Object.keys(on.kinds as object).sort()).toEqual(on.facts.map((fact) => fact.id).sort());
    expect(classifyKinds(document, off)).toEqual(on.kinds);
    // 요청 글은 종류와 무관하다.
    expect(renderDocumentWithIds(document, on, { prefix: "b:", bindNames: true })).toBe(renderDocumentWithIds(document, off, { prefix: "b:", bindNames: true }));
  });

  it("kindOf 는 표의 숫자 칸에서 연도 꼴만 가른다", () => {
    const base = { id: "x", docId: "s", bucket: "table_cell", where: { page: 1, sheet: null, gridId: "t", row: 1, col: 1, ref: null, blockId: null, span: null }, raw: "39,604", value: "39604", decimals: 0, sign: "none", percent: false, unitSuffix: null, flags: [], footnoteMarks: [], tableStatus: "ok", headers: null, stored: null, storedAsText: false } as Fact;
    expect(kindOf(base, { text: "39,604", start: 0, end: 6, columnHeaders: [] })).toBe("value");
    expect(kindOf({ ...base, raw: "2030", value: "2030", flags: ["year_like"] }, { text: "2030", start: 0, end: 4, columnHeaders: [] })).toBe("year_label");
  });
});

describe("종류의 정답과 채점, 재분류의 셈", () => {
  const document = summary();
  const extraction = own(document, true);
  const empty = own(syntheticDocument("e", []));

  it("정답의 대상: 서술 값 후보는 전부, 표의 칸은 씨앗 고정 표본. 씨앗이 같으면 같다", () => {
    const targets = drawKindTargets(extraction, empty, 7, 1);
    expect(targets.filter((item) => item.bucket !== "table_cell").map((item) => item.id).sort()).toEqual(extraction.facts.filter((fact) => fact.bucket !== "table_cell").map((fact) => fact.id).sort());
    expect(targets.filter((item) => item.bucket === "table_cell")).toHaveLength(1);
    expect(drawKindTargets(extraction, empty, 7, 1)).toEqual(targets);
    const gold: KindGold = { note: "synthetic", status: "synthetic", seed: 7, perBucket: 1, items: targets.map((item) => ({ ...item, doc: item.doc, kind: "value" as const, ambiguousWith: null, basis: "synthetic" })) };
    expect(checkKindGold(gold, extraction, empty)).toEqual([]);
    expect(checkKindGold({ ...gold, items: gold.items.slice(1) }, extraction, empty).join("\n")).toContain("정답이 없다");
    expect(checkKindGold({ ...gold, items: gold.items.map((item, index) => (index === 0 ? { ...item, raw: "999" } : item)) }, extraction, empty).join("\n")).toContain("원문 글자");
  });

  it("채점: 종류별 정밀도·재현율의 분자·분모와 틀린 것의 목록. 애매한 자리는 따로 센다", () => {
    const items = [
      { doc: "a" as const, id: "1", bucket: "sentence" as const, raw: "1", kind: "value" as const, ambiguousWith: null, basis: "s" },
      { doc: "a" as const, id: "2", bucket: "sentence" as const, raw: "2", kind: "count" as const, ambiguousWith: "value" as const, basis: "s" },
      { doc: "a" as const, id: "3", bucket: "sentence" as const, raw: "3", kind: "scale" as const, ambiguousWith: null, basis: "s" },
      { doc: "a" as const, id: "4", bucket: "table_cell" as const, raw: "4", kind: "value" as const, ambiguousWith: null, basis: "s" },
    ];
    const predicted: Record<string, ValueKind> = { 1: "value", 2: "value", 3: "scale", 4: "duration" };
    const score = scoreKinds(items, (_doc, id) => predicted[id]);
    expect([score.total, score.correct, score.correctLenient, score.ambiguous]).toEqual([4, 2, 3, 1]);
    expect(score.byBucket.all?.value).toEqual({ gold: 2, predicted: 2, hit: 1 });
    expect(score.byBucket.sentence?.scale).toEqual({ gold: 1, predicted: 1, hit: 1 });
    expect(score.nonValue.all).toEqual({ gold: 2, predicted: 2, hit: 1, wronglyNonValue: 1, wronglyNonValueAmbiguous: 0 });
    expect(score.wrong.map((item) => item.id)).toEqual(["2", "4"]);
    expect(() => scoreKinds(items, () => undefined)).toThrow(TallyError);
  });

  it("재분류: 한 자리라도 「값이 아님」이면 걸러지고, 합이 대상 수와 같다", () => {
    const tally = reclassify([{ x: "value", y: "value" }, { x: "value", y: "identifier" }, { x: "count", y: "count" }, { x: "duration", y: "value" }, { x: "value", y: "value" }]);
    expect(tally).toEqual({ total: 5, bothValue: 2, oneNonValue: 2, bothNonValue: 1, filtered: 3 });
    expect(tally.bothValue + tally.oneNonValue + tally.bothNonValue).toBe(tally.total);
    expect(reclassify([])).toEqual({ total: 0, bothValue: 0, oneNonValue: 0, bothNonValue: 0, filtered: 0 });
    const kinds: Record<string, ValueKind> = { p: "value", q: "year_label" };
    expect(nonValuePlaces([{ doc: "a" as const, id: "p" }, { doc: "a" as const, id: "q" }], (_doc, id) => kinds[id])).toEqual([{ doc: "a", id: "q", kind: "year_label" }]);
    expect(() => nonValuePlaces([{ doc: "a" as const, id: "z" }], () => undefined)).toThrow(TallyError);
  });
});
