import { describe, expect, it } from "vitest";
import { z } from "zod";
import { extractDocument, type Extraction } from "../src/index.js";
import type { GoldPair, Plant } from "../src/compare/exam.js";
import { grade, type MethodGrade } from "../src/compare/grade.js";
import { TallyError, classifyCandidates, hybridOutcome, judgeTableCells, pairCellsInTablesSchema, renderCellPairRequest, renderTableSide, tallyCandidates, unmaskedValues } from "../src/compare/hybrid.js";
import { pageTexts, type DocContext, type MethodOutcome } from "../src/compare/methods.js";
import { MASK } from "../src/compare/render.js";
import { comparedOnlyInBase, outsideGoldMatches, pairStability, plantedByRun, renderRuns, setStats, type Run } from "../src/compare/runs.js";
import { syntheticDocument, units } from "./helpers.js";

// 방법 G 와 반복 실행 비교를 지어낸 문서로 본다 (synthetic). 어느 회사의 실제 값도 아니다.

const HEADER = { rows: [0], cols: [0, 1], basis: "rows_before_first_numeric_row" };
const table = (id: string, page: number, rows: string[][], before: string[] = []) => ({ id, page, cells: [["구분", "단위", "2023", "2024"], ...rows], header: HEADER, before });
// 문서 a: 1쪽에 표 둘(둘째 표는 요청에 실리면 안 된다)과 글, 2쪽에 표 하나.
const documentA = () =>
  syntheticDocument(
    "a",
    [
      table("p1-t1", 1, [["Scope 1", "tCO2eq", "48,217", "39,604"], ["감축률", "%", "17.3%", "기준연도 대비 26.5% 감축"]], ["배출량 현황 (목표 91,350 tCO2eq 이내)", "1) 2023년 배출량은 48,217 tCO2eq 로 검증받았다"]),
      table("p1-t2", 1, [["용수 사용량", "톤", "7,731", "7,912"]], ["같은 쪽의 다른 표"]),
      table("p2-t1", 2, [["폐기물", "톤", "615", "642"]], ["다른 쪽의 표"]),
    ],
    [{ id: "a-body", page: 1, text: "2024년 배출량은 39,604 tCO2eq 이며 전년 대비 8,613 tCO2eq 줄었다" }, { id: "a-other", page: 2, text: "다른 쪽의 글 55,501" }],
  );
const documentB = () =>
  syntheticDocument("b", [table("p3-t1", 1, [["직접 배출", "tCO2eq", "48,217", "39,640"]], ["온실가스 (오차 4.75% 이내)", "(단위: tCO2eq, 연 6,789 미만 사업장 제외)"]), table("p3-t2", 1, [["전력 사용량", "MWh", "2,210", "2,305"]])], [{ id: "b-body", page: 1, text: "검증 대상 사업장 37개" }]);
const own = (document: ReturnType<typeof documentA>, id: string): Extraction => extractDocument(document, id, { titles: "own" });
const context = (extraction: Extraction): DocContext => ({ extraction, pages: pageTexts(extraction, new Map(), new Map()) });
const VALUES = ["48,217", "39,604", "39,640", "17.3", "26.5", "91,350", "8,613", "4.75", "7,731", "7,912", "615", "642", "55,501", "37", "6,789"];

describe("방법 G: 칸 짝 요청의 조립", () => {
  const a = { document: documentA(), extraction: own(documentA(), "a") };
  const b = { document: documentB(), extraction: own(documentB(), "b") };
  const text = renderCellPairRequest(a, b, { a: "p1-t1", b: "p3-t1" });

  it("두 표와 그 쪽의 글만 실린다. 같은 쪽의 다른 표와 다른 쪽의 글은 실리지 않는다", () => {
    expect(text).toContain("[표 p1-t1]");
    expect(text).toContain("[표 p3-t1]");
    expect(text).not.toContain("[표 p1-t2]");
    expect(text).not.toContain("[표 p2-t1]");
    expect(text).not.toContain("[표 p3-t2]");
    expect(text).not.toContain("용수 사용량");
    expect(text).not.toContain("전력 사용량");
    expect(text).not.toContain("폐기물");
    expect(text).not.toContain("다른 쪽의 글");
    // 그 쪽의 글, 제목·각주, 머리글의 연도, 행 이름은 실린다.
    expect(text).toContain("2024년 배출량은");
    expect(text).toContain("배출량 현황");
    expect(text).toContain("검증 대상 사업장");
    expect(text).toContain("c2: 2023");
    expect(text).toContain("직접 배출");
    expect(text).toContain("# 문서 a 의 표 p1-t1 (1쪽)");
  });

  it("요청 글에 값 후보 숫자가 남지 않는다: 표 칸, 제목·각주, 쪽의 글 전부", () => {
    for (const value of VALUES) expect(text).not.toContain(value);
    expect(unmaskedValues(text)).toEqual([]);
    expect(text).toContain(`r1 | c0: Scope 1 | c1: tCO2eq | c2: ${MASK} | c3: ${MASK}`);
    expect(text).toContain(`2024년 배출량은 ${MASK} tCO2eq 이며 전년 대비 ${MASK} tCO2eq 줄었다`);
    expect(text).toContain(`목표 ${MASK} tCO2eq 이내`);
    expect(text).toContain(`오차 ${MASK} 이내`);
    expect(text).toContain(`검증 대상 사업장 ${MASK}`);
    // 단위 줄 속의 값 후보도 가린다.
    expect(text).toContain(`단위 줄: (단위: tCO2eq, 연 ${MASK} 미만 사업장 제외)`);
  });

  it("가림이 빠진 글은 확인이 잡는다. 칸·표·쪽을 가리키는 글자와 연도·식별자는 잡지 않는다", () => {
    expect(unmaskedValues("r3 | c0: Scope 1 | c2: 2023 | c4: ▒\n[표 p12-t3]\n# 문서 a 의 표 p12-t3 (12쪽)\n## 12쪽의 글과 이 표 (읽는 순서)\n2025년 기준")).toEqual([]);
    expect(unmaskedValues("r3 | c0: Scope 1 | c2: 48,217")).toEqual(["48,217"]);
    expect(unmaskedValues("전년 대비 8,613 tCO2eq 줄었다")).toEqual(["8,613"]);
  });

  it("표 하나의 한쪽 글은 그 표가 없으면 만들지 않는다", () => {
    expect(() => renderTableSide(a.document, a.extraction, "p9-t9")).toThrow();
  });
});

describe("방법 G: 답의 확인", () => {
  const a = own(documentA(), "a");
  const b = own(documentB(), "b");
  const pair = { a: "p1-t1", b: "p3-t1" };

  it("답의 스키마에 수를 적을 수 있는 필드가 없다", () => {
    expect(JSON.stringify(z.toJSONSchema(pairCellsInTablesSchema))).not.toMatch(/"type":"(number|integer)"/);
    expect(pairCellsInTablesSchema.parse({ pairs: [{ a: "x", b: "y", unit_a: null, unit_b: null, value: 1 }] }).pairs[0]).not.toHaveProperty("value");
  });

  it("없는 ID 와 그 요청의 두 표 밖의 칸을 가리킨 짝은 버리고, 남은 짝은 코드가 값을 견준다", () => {
    const judged = judgeTableCells(
      {
        pairs: [
          { a: "p1-t1:r1c2", b: "p3-t1:r1c2", unit_a: "tCO2eq", unit_b: "tCO2eq" }, // 일치
          { a: "p1-t1:r1c3", b: "p3-t1:r1c3", unit_a: null, unit_b: null }, // 불일치 (39,604 대 39,640)
          { a: "p1-t1:r1c2", b: "p3-t1:r1c2", unit_a: null, unit_b: null }, // 같은 짝 두 번
          { a: "p1-t1:r9c9", b: "p3-t1:r1c2", unit_a: null, unit_b: null }, // 없는 ID
          { a: "p1-t2:r1c2", b: "p3-t1:r1c2", unit_a: null, unit_b: null }, // 문서 a 의 다른 표
          { a: "p1-t1:r1c2", b: "p3-t2:r1c2", unit_a: null, unit_b: null }, // 문서 b 의 다른 표
          { a: "p1-t1:r2c2", b: "p3-t1:r1c3", unit_a: "kWh", unit_b: null }, // 원문에 없는 단위 글자
        ],
      },
      pair,
      context(a),
      context(b),
      units,
    );
    expect(judged.verdicts.map((verdict) => `${verdict.a}|${verdict.b}:${verdict.outcome}`)).toEqual(["p1-t1:r1c2|p3-t1:r1c2:match", "p1-t1:r1c3|p3-t1:r1c3:mismatch"]);
    expect(judged.rejected).toEqual({ duplicate_pair: 1, unknown_id: 1, outside_request_tables: 2, unit_not_in_source: 1 });
    expect(judged.rejectedCells.a.sort()).toEqual(["p1-t1:r1c2", "p1-t1:r2c2", "p1-t2:r1c2"]);
    expect(judged.rejectedCells.b.sort()).toEqual(["p3-t1:r1c2", "p3-t1:r1c2", "p3-t1:r1c3", "p3-t2:r1c2"]);
  });
});

describe("방법 G: 모든 값 후보가 결과 하나에", () => {
  const a = own(documentA(), "a");
  const b = own(documentB(), "b");
  const pair = { a: "p1-t1", b: "p3-t1" };
  const judged = judgeTableCells({ pairs: [{ a: "p1-t1:r1c2", b: "p3-t1:r1c2", unit_a: null, unit_b: null }, { a: "p1-t1:r1c3", b: "p3-t1:r1c3", unit_a: null, unit_b: null }, { a: "p1-t2:r1c3", b: "p3-t1:r1c3", unit_a: null, unit_b: null }] }, pair, context(a), context(b), units);
  const result = hybridOutcome(a, b, [pair], [judged], { unknown_id: 1 });

  it("견줌, 짝지어진 표 안인데 칸 짝 없음, 표 짝 없음, 표 밖, 버려진 답의 합이 값 후보 수다", () => {
    // 문서 a: 표 숫자 칸 7 (p1-t1 셋, p1-t2 둘, p2-t1 둘) + 글 칸 속 1 + 문장 속 (제목·각주 3 + 본문 3).
    expect(result.tally.a.total).toBe(a.facts.length);
    expect(result.tally.a).toMatchObject({ compared: { match: 1, mismatch: 1, needsExplanation: 0 }, noCellPairInPairedTable: 1, tableNotPaired: 3, answerRejected: 1 });
    expect(result.tally.a.outsideTables).toEqual({ table_text_cell: 1, sentence: a.facts.filter((fact) => fact.bucket === "sentence").length });
    expect(result.tally.b).toMatchObject({ total: b.facts.length, compared: { match: 1, mismatch: 1, needsExplanation: 0 }, noCellPairInPairedTable: 0, tableNotPaired: 2, answerRejected: 0 });
    for (const side of ["a", "b"] as const) {
      const tally = result.tally[side];
      const sum = tally.compared.match + tally.compared.mismatch + tally.compared.needsExplanation + tally.noCellPairInPairedTable + tally.tableNotPaired + tally.answerRejected + Object.values(tally.outsideTables).reduce((x, y) => x + y, 0);
      expect(sum).toBe(tally.total);
    }
    // 견주지 못한 값 후보는 전부 사유와 함께 남는다.
    expect(Object.keys(result.outcome.states.a)).toHaveLength(a.facts.length - 2);
    expect(result.outcome.states.a["p2-t1:r1c2"]).toBe("unverified:table_not_paired");
    expect(result.outcome.states.a["p1-t2:r1c3"]).toBe("unverified:answer_rejected");
    expect(result.outcome.states.a["p1-t1:r2c2"]).toBe("unverified:no_cell_pair_in_paired_table");
    expect(result.outcome.rejected).toEqual({ unknown_id: 1, outside_request_tables: 1 });
  });

  it("합이 값 후보 수와 다르면 수를 내지 않고 예외를 낸다", () => {
    expect(() => tallyCandidates(3, ["compared:match", "unverified:table_not_paired"])).toThrow(TallyError);
    expect(tallyCandidates(2, ["compared:match", "unverified:table_not_paired"])).toMatchObject({ total: 2, tableNotPaired: 1 });
  });

  it("한 칸이 여러 짝에서 견주어졌으면 가장 무거운 결과가 그 칸의 결과다", () => {
    const states = classifyCandidates(b, "b", new Set(["p3-t1"]), [{ a: "x", b: "p3-t1:r1c2", outcome: "match", reason: "", valuesDiffer: false }, { a: "y", b: "p3-t1:r1c2", outcome: "mismatch", reason: "", valuesDiffer: true }], new Set(["p3-t1:r1c2"]));
    expect(states.get("p3-t1:r1c2")).toBe("compared:mismatch");
    expect(states.get("p3-t1:r1c3")).toBe("unverified:no_cell_pair_in_paired_table");
  });

  it("여러 표 짝에서 같은 칸 짝이 나오면 한 번만 센다", () => {
    expect(hybridOutcome(a, b, [pair], [judged, judged], {}).outcome.verdicts).toHaveLength(2);
  });
});

describe("반복 실행의 비교", () => {
  const gold = (id: string): GoldPair => ({ id, item: id, period: "2025", a: `ta:${id}`, b: `tb:${id}`, rawA: "1", rawB: "1", origin: "added", inSeedTables: true });
  const pairs = ["clean", "err"].map(gold);
  const plants: Plant[] = [{ id: "E1", kind: "digit_changed", isError: true, cells: [{ cell: "tb:err", original: "1", planted: "2", pair: "err" }], otherPlaceInB: [] }];
  const verdict = (a: string, b: string, outcome: "match" | "mismatch") => ({ a, b, outcome, reason: "r", valuesDiffer: outcome === "mismatch" });
  const run = (method: string, index: number, verdicts: ReturnType<typeof verdict>[], states: MethodOutcome["states"] = { a: {}, b: {} }): Run => {
    const outcome: MethodOutcome = { verdicts, intra: [], states, rejected: {} };
    const graded: MethodGrade = { method, title: method, reportsUnchecked: method === "g", reportsMatches: true, grade: grade(pairs, plants, outcome), cost: null };
    return { method, run: index, outcome, grade: graded };
  };
  const d = [run("d", 1, [verdict("ta:clean", "tb:clean", "match"), verdict("ta:err", "tb:err", "mismatch"), verdict("ta:x", "tb:x", "match")]), run("d", 2, [verdict("ta:clean", "tb:clean", "match"), verdict("ta:x", "tb:x", "match"), verdict("ta:y", "tb:y", "match")])];
  const g = [run("g", 1, [verdict("ta:clean", "tb:clean", "match"), verdict("ta:err", "tb:err", "mismatch")], { a: { "ta:x": "unverified:table_not_paired", "ta:y": "unverified:table_not_paired" }, b: { "tb:x": "unverified:no_cell_pair_in_paired_table", "tb:y": "unverified:table_not_paired" } })];

  it("교집합과 합집합, 몇 실행에 나왔는지", () => {
    expect(setStats([new Set(["p", "q"]), new Set(["q", "r"]), new Set(["q"])])).toEqual({ perRun: [2, 2, 1], intersection: 1, union: 3, inRuns: [2, 0, 1] });
    const stability = pairStability(d.map((item) => item.outcome));
    expect(stability.compared).toMatchObject({ perRun: [3, 3], intersection: 2, union: 4 });
    expect(stability.mismatch).toMatchObject({ perRun: [1, 0], intersection: 0, union: 1 });
  });

  it("심은 칸이 실행 중 몇 번 잡혔는가", () => {
    expect(plantedByRun(plants, d.map((item) => item.grade))).toEqual([{ plant: "E1", kind: "digit_changed", isError: true, cell: "tb:err", statuses: ["mismatch", "silent"], good: 1 }]);
  });

  it("정답 쌍 밖의 일치는 실행들의 합집합으로 모으고, 어느 실행이 냈는지 남긴다", () => {
    expect(outsideGoldMatches(pairs, [...d, ...g])).toEqual([{ a: "ta:x", b: "tb:x", runs: ["D#1", "D#2"] }, { a: "ta:y", b: "tb:y", runs: ["D#2"] }]);
  });

  it("기준 방법만 견준 짝과, 그 칸이 다른 방법에서 어떤 상태였는지", () => {
    expect(comparedOnlyInBase(pairs, d, g)).toEqual([
      { a: "ta:x", b: "tb:x", base: [{ run: "D#1", outcome: "match" }, { run: "D#2", outcome: "match" }], inGold: false, other: [{ run: "G#1", a: "unverified:table_not_paired", b: "unverified:no_cell_pair_in_paired_table" }] },
      { a: "ta:y", b: "tb:y", base: [{ run: "D#2", outcome: "match" }], inGold: false, other: [{ run: "G#1", a: "unverified:table_not_paired", b: "unverified:table_not_paired" }] },
    ]);
  });

  it("비교를 마크다운으로 내고, 판정 기록이 있으면 건마다 붙인다", () => {
    const md = renderRuns([...d, ...g], pairs, plants, { note: "n", status: "s", judgments: [{ a: "ta:x", b: "tb:x", verdict: "not_same", evidence: { a: { page: 1, row: "가", column: "2025" }, b: { page: 2, row: "나", column: "2025" } }, note: null }] });
    expect(md).toContain("| 항목 | D#1 | D#2 | G#1 |");
    expect(md).toContain("| E1 | digit_changed | tb:err | 1/2 | 1/1 |");
    expect(md).toContain("합집합 2쌍. 판정: 같은 항목 0/2, 같은 항목 아님(정답 밖 거짓 일치) 1/2, 값이 아닌 표시끼리 0/2, 가를 수 없음 0/2, 판정 없음 1/2.");
    expect(md).toContain("| ta:x | tb:x | D#1 D#2 | 같은 항목 아님 | 1쪽, 가, 2025 | 2쪽, 나, 2025 |");
    expect(md).toContain("2쌍 (그중 정답 쌍 0쌍)");
  });
});
