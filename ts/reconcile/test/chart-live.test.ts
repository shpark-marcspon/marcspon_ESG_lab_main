import { mkdirSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { aimPlaces, aimRows, aimSummary, checkMoved, checkPlantedCells, missingPairs, moveGold, scoreAims, type AimSpec, type GoldSet } from "../src/compare/chart-live.js";
import { buildChartTables, type ChartTableInput } from "../src/compare/chart-tables.js";
import type { ChartGold } from "../src/compare/charts.js";
import { main as cli11, pickSet } from "../src/compare/cli11.js";
import { main as cli2 } from "../src/compare/cli2.js";
import { applyChanges } from "../src/compare/exam2.js";
import { extractDocument, type DocModel } from "../src/index.js";

// 밴드 CT2 (도표를 표로 세운 문서로 짝짓기)의 준비와 채점을 지어낸 문서로 본다 (synthetic). 어느 회사의 실제 값도 아니다. 키 없이 돈다.

const fixture = (name: string): string => fileURLToPath(new URL(`./fixtures/${name}`, import.meta.url));
const sourceA = (): DocModel => JSON.parse(readFileSync(fixture("chart-doc.synthetic.json"), "utf8")) as DocModel;
const box = (x: number, y = 100): number[] => [x, y, x + 20, y + 8];

/** 문서 B: 머리글이 선 표 하나 (계획·실적 x 2023~2025) 와 글 한 줄. */
function sourceB(): DocModel {
  const a = sourceA();
  const table = { ...a.tables[0]!, id: "p1-t1", cells: [["가상 배출량", "2023", "2024", "2025"], ["계획", "110", "120", "130"], ["실적", "210", "220", "230"]], header: { rows: [0], cols: [0], basis: "rows_before_first_numeric_row" }, status: "ok" as const, footnote_marks: [] };
  Object.assign(table, { n_rows: 3, n_cols: 4 });
  return {
    ...a,
    source: { ...a.source, file_name: "chart-doc-b.synthetic.pdf" },
    blocks: [{ ...a.blocks[0]!, id: "p1-b1", text: "가상 자료집 (synthetic)" }, { ...a.blocks[1]!, id: "p1-b2", table_id: "p1-t1" }, { ...a.blocks[0]!, id: "p1-b3", order: 3, text: "글에는 55 톤이 적혀 있다." }],
    tables: [table],
  };
}

/** 계열 둘·범주 셋인 도표 c1 (글 칸 속 숫자 여섯). c1 의 넷째 값(실적 2023)은 범주 라벨이 없어 원래 자리에 남는다. */
const input = (): ChartTableInput => ({
  source: "synthetic",
  charts: [{ id: "p1-c1", page: 1, bbox: [50, 80, 300, 200], title: "가상 배출량", categories: ["2023", "2024", "2025"] }],
  values: [
    { id: "p1-t1:r0c0#n1", chart: "p1-c1", series: "계획", category: "2023", bbox: box(60) },
    { id: "p1-t1:r0c0#n2", chart: "p1-c1", series: "계획", category: "2024", bbox: box(100) },
    { id: "p1-t1:r0c0#n3", chart: "p1-c1", series: "계획", category: "2025", bbox: box(140) },
    { id: "p1-t1:r0c0#n4", chart: "p1-c1", series: "실적", category: null, bbox: box(60, 120) },
    { id: "p1-t1:r0c0#n5", chart: "p1-c1", series: "실적", category: "2024", bbox: box(100, 120) },
    { id: "p1-t1:r0c0#n6", chart: "p1-c1", series: "실적", category: "2025", bbox: box(140, 120) },
  ],
});

const docs = { a: { file: "a", sha256: "0" }, b: { file: "b", sha256: "0" } };
/** 원래 문서의 ID 로 적은 정답: 도표의 값 셋(옮겨지는 둘, 남는 하나)과 글 속 숫자 하나. */
const gold = (): GoldSet => ({
  pairs: { note: "synthetic", status: "synthetic", docs, pairs: [] },
  plants: { note: "synthetic", status: "synthetic", seed: 1, plan: [], plants: [] },
  gold: {
    note: "synthetic",
    status: "synthetic",
    docs,
    prosePairs: [
      { id: "N1", item: "계획 2024", period: "2024", a: "p1-t1:r0c0#n2", b: "p1-t1:r1c2", rawA: "120", rawB: "120", control: null, evidence: "synthetic" },
      { id: "N2", item: "실적 2025", period: "2025", a: "p1-t1:r0c0#n6", b: "p1-t1:r2c3", rawA: "230", rawB: "230", control: null, evidence: "synthetic" },
      { id: "N3", item: "실적 2023 (범주 라벨 없음: 원래 자리에 남는다)", period: "2023", a: "p1-t1:r0c0#n4", b: "p1-t1:r2c1", rawA: "210", rawB: "210", control: null, evidence: "synthetic" },
      { id: "N4", item: "글 속의 톤", period: null, a: "p1-b4#n1", b: "p1-b3#n1", rawA: "55", rawB: "55", control: null, evidence: "synthetic" },
    ],
    groups: [{ id: "G1", doc: "a", item: "계획 2023 과 글", period: "2023", places: [{ id: "p1-t1:r0c0#n1", raw: "110" }, { id: "p1-b4#n1", raw: "55" }], control: null, evidence: "synthetic (묶음의 꼴만 본다)" }],
  },
  plants2: { note: "synthetic", status: "synthetic", seed: 1, plan: [], skipped: [], plants: [{ id: "U1", kind: "digit_changed", isError: true, pool: "prose_pair", doc: "a", target: "p1-t1:r0c0#n6", original: "230", planted: "239", pairs: ["N2"], groups: [] }] },
});
const chartGold = (): ChartGold => ({ note: "synthetic", roles: {}, charts: [], labels: [["p1-t1:r0c0#n2", "계획", "2024"], ["p1-t1:r0c0#n6", "실적", "2025"], ["p1-t1:r0c0#n4", "실적", "2023"]].map(([id, series, category]) => ({ doc: "a", id, raw: "", chart: "A-C1", role: "value", series: [series], category: [category] })) }) as unknown as ChartGold;

function setup() {
  const a = sourceA();
  const b = sourceB();
  const built = buildChartTables(a, "a", input());
  const original = { a: extractDocument(a, "a", { titles: "own" }), b: extractDocument(b, "b", { titles: "own" }) };
  const derived = { a: extractDocument(built.document, "a", { titles: "own" }), b: original.b };
  const maps = { a: built.map, b: [] };
  // 시험지: 원래 문서의 사본에 심고, 같은 라벨로 같은 변환을 한다.
  const examA = applyChanges(a, original.a, [{ doc: "a", target: "p1-t1:r0c0#n6", original: "230", planted: "239" }]);
  const builtExam = buildChartTables(examA, "a", input());
  return { a, b, built, original, derived, maps, examA, builtExam };
}

describe("정답의 ID 옮기기", () => {
  it("옮겨진 자리는 새 표의 칸 ID 로, 남은 자리는 원래 그릇의 ID 로 간다. 수는 앞뒤로 같다", () => {
    const s = setup();
    const moved = moveGold(gold(), s.maps);
    const at = (id: string) => moved.places.find((place) => place.from === id)!;
    const table = s.built.charts[0]!.tableId;
    expect([at("p1-t1:r0c0#n2").kind, at("p1-t1:r0c0#n2").to]).toEqual(["moved", `${table}:r1c2`]);
    expect([at("p1-t1:r0c0#n6").kind, at("p1-t1:r0c0#n6").to]).toEqual(["moved", `${table}:r2c3`]);
    // 범주 라벨이 없어 남은 값: 원래 그릇에 혼자 남아 순번이 바뀐다
    expect(at("p1-t1:r0c0#n4").kind).toBe("renumbered");
    expect(at("p1-t1:r0c0#n4").to.startsWith("p1-t1:r0c0")).toBe(true);
    expect(at("p1-b4#n1").kind).toBe("same");
    for (const [before, after] of Object.values(moved.counts)) expect(after).toBe(before);
    // 양쪽이 다 표의 숫자 칸이 된 쌍은 도표 쌍으로 따로 실린다 (N3 은 원래 그릇에 혼자 남아 그 칸이 숫자 칸이 됐다)
    expect(moved.chartPairs.map((pair) => pair.id)).toEqual(["N1", "N2", "N3"]);
    expect(moved.gold.prosePairs.map((pair) => pair.id).sort()).toEqual(expect.arrayContaining(["N4"]));
    expect(moved.gold.prosePairs.length + moved.chartPairs.length).toBe(4);
    expect(moved.plants2.plants[0]!.target).toBe(`${table}:r2c3`);
    expect(checkMoved(moved, s.original, s.derived)).toEqual([]);
    // 넘겨받은 정답은 고치지 않는다
    expect(gold().gold.prosePairs[0]!.a).toBe("p1-t1:r0c0#n2");
  });

  it("옮기지 않은 정답을 파생 문서에 대면 걸린다", () => {
    const s = setup();
    const unmoved = moveGold(gold(), { a: [], b: [] });
    const problems = checkMoved(unmoved, s.original, s.derived);
    expect(problems.some((line) => line.includes("p1-t1:r0c0#n2") && line.includes("파생 문서에 값 후보가 없다"))).toBe(true);
  });

  it("파생 시험지의 심은 값은 대응표의 칸에 있다", () => {
    const s = setup();
    const moved = moveGold(gold(), s.maps);
    const derivedExam = { a: extractDocument(s.builtExam.document, "a", { titles: "own" }), b: s.original.b };
    expect(s.builtExam.map).toEqual(s.built.map);
    expect(checkPlantedCells(moved, s.derived, derivedExam)).toEqual([]);
    // 심지 않은 문서를 시험지라고 대면 걸린다
    expect(checkPlantedCells(moved, s.derived, s.derived).some((line) => line.includes("U1"))).toBe(true);
  });
});

describe("겨냥", () => {
  it("겨냥 2·3 은 도표 정답의 값 자리에서 고르고, 벌마다 옮겨졌는지와 옳은 칸인지를 센다", () => {
    const s = setup();
    const places = aimPlaces([{ name: "글", doc: "a", id: "p1-b4#n1" }], gold().gold, gold().plants2, chartGold());
    expect(places.map((place) => `${place.aim}:${place.name}`)).toEqual(["1:글", "2:N1", "2:N2", "2:N3", "3:U1"]);
    const rows = aimRows(places, s.maps, { a: { model: s.built.document, extraction: s.derived.a }, b: { model: s.b, extraction: s.derived.b } }, chartGold());
    expect(rows.map((row) => `${row.name}:${row.kind}:${row.placed}`)).toEqual(["글:same:null", "N1:moved:right", "N2:moved:right", "N3:renumbered:null", "U1:moved:right"]);
    expect(aimSummary(rows)).toMatchObject({ total: 5, distinct: 4, moved: 3, movedDistinct: 2, wrong: 0 });
    // 머리글이 정답과 다르면 틀린 칸이다
    const wrongGold = chartGold();
    (wrongGold.labels[0] as { category: string[] }).category = ["2025"];
    expect(aimSummary(aimRows(places, s.maps, { a: { model: s.built.document, extraction: s.derived.a }, b: { model: s.b, extraction: s.derived.b } }, wrongGold)).wrong).toBe(1);
  });

  it("고르는 규칙: 옮겨진 것이 많은 벌, 같으면 틀린 칸이 적은 벌, 같으면 전체의 틀린 칸, 같으면 넘겨받은 차례", () => {
    const set = (name: string, moved: number, wrong: number) => ({ name, rows: [], summary: { total: 10, distinct: 10, moved, movedDistinct: moved, wrong, byAim: {} } });
    expect(pickSet([set("x", 5, 0), set("y", 6, 2)]).picked).toBe("y");
    expect(pickSet([set("x", 6, 1), set("y", 6, 0)]).picked).toBe("y");
    expect(pickSet([set("x", 6, 0), set("y", 6, 0)], { x: 9, y: 3 }).picked).toBe("y");
    expect(pickSet([set("x", 6, 0), set("y", 6, 0)]).picked).toBe("x");
    expect(pickSet([set("x", 4, 0)]).enough).toBe(false);
    expect(pickSet([set("x", 5, 0)]).enough).toBe(true);
  });
});

describe("겨냥의 셈", () => {
  const spec: AimSpec = {
    aim1: [{ name: "k1", a: "t9:r1c1", right: ["tb:r1c1"] }, { name: "k2", a: "t9:r1c2", right: ["tb:r1c2"] }, { name: "k3", a: "t9:r1c3", right: ["tb:r1c3"] }],
    aim2: [{ pair: "N1", a: "t9:r1c1", b: "tb:r1c1" }, { pair: "N2", a: "t9:r1c2", b: "tb:r1c2" }],
    aim3: [{ plant: "U1", pair: "N1", a: "t9:r1c1", b: "tb:r1c1" }, { plant: "U2", pair: "N2", a: "t9:r1c2", b: "tb:r1c2" }],
    chartCells: { a: ["t9:r1c1", "t9:r1c2", "t9:r1c3"], b: [] },
    leftBehind: { a: ["t1:r0c0#n1"], b: [] },
  };
  const verdict = (a: string, b: string, outcome: "match" | "mismatch") => ({ a, b, outcome, reason: "synthetic", valuesDiffer: outcome === "mismatch" });
  const raw = (a: string, b: string) => ({ a: `a:${a}`, b: `b:${b}`, unit_a: null, unit_b: null });

  it("받아들인 짝만 짝으로 센다. 버려진 답에만 나온 자리는 따로 센다", () => {
    // 원답에는 k1·k2·k3 가 다 나왔지만 k2 의 답은 확인에서 버려졌다 (받아들인 짝에 없다). k3 은 다른 상대와 짝지어졌다.
    const answers = [{ pairs: [raw("t9:r1c1", "tb:r1c1"), raw("t9:r1c2", "tb:r9c9"), raw("t9:r1c3", "tb:r2c2"), raw("t1:r0c0#n1", "nowhere")] }];
    const outcome = { verdicts: [verdict("t9:r1c1", "tb:r1c1", "mismatch"), verdict("t9:r1c3", "tb:r2c2", "match")] };
    const score = scoreAims(spec, outcome, answers, new Set(["t9:r1c1|tb:r1c1", "t9:r1c2|tb:r1c2"]));
    expect([score.aim1.paired, score.aim1.right, score.aim1.other]).toEqual([2, 1, 1]);
    expect(score.aim1.rows.map((row) => row.inRejected)).toEqual([false, true, false]);
    expect([score.aim2.paired, score.aim2.match, score.aim2.mismatch]).toEqual([1, 0, 1]);
    expect(score.aim2.rows[1]).toMatchObject({ outcome: null, inRejected: true });
    expect([score.aim3.caught, score.aim3.falseMatch, score.aim3.silent]).toEqual([1, 0, 1]);
    expect([score.chartCells.accepted, score.chartCells.rejectedOnly, score.chartCells.total]).toEqual([2, 1, 3]);
    expect([score.leftBehind.accepted, score.leftBehind.rejectedOnly]).toEqual([0, 1]);
    // 표로 세운 칸이 낀 정답 밖의 짝은 판정 대상으로 남긴다
    expect(score.outsideGold.map((item) => `${item.a}|${item.b}`)).toEqual(["t9:r1c3|tb:r2c2"]);
  });

  it("심은 오류가 일치로 나오면 거짓 일치다. 빠진 정답 쌍은 그 두 칸의 짝이 없는 것이다", () => {
    const score = scoreAims(spec, { verdicts: [verdict("t9:r1c1", "tb:r1c1", "match")] }, [], new Set());
    expect([score.aim3.caught, score.aim3.falseMatch, score.aim3.silent]).toEqual([0, 1, 1]);
    expect(missingPairs([{ id: "g1", a: "x", b: "y" }, { id: "g2", a: "t9:r1c1", b: "tb:r1c1" }], [verdict("t9:r1c1", "tb:r1c1", "match"), verdict("x", "z", "match")])).toEqual(["g1"]);
  });
});

describe("파생 문서를 받는 길 (밴드 U 의 명령에 옮긴 정답을 준다, synthetic)", () => {
  const dir = mkdtempSync(join(tmpdir(), "reconcile-chart-live-"));
  const file = (name: string, value: unknown): string => {
    const path = join(dir, name);
    writeFileSync(path, JSON.stringify(value));
    return path;
  };
  const s = setup();
  const g = gold();
  const moved = moveGold(g, s.maps);
  const paths = {
    origA: file("orig-a.json", s.a),
    origB: file("orig-b.json", s.b),
    a: file("a.json", s.built.document),
    b: file("b.json", s.b),
    examA: file("exam-a.json", s.builtExam.document),
    examB: file("exam-b.json", s.b),
    mapA: file("map-a.json", { map: s.built.map }),
    mapB: file("map-b.json", { map: [] }),
    chartGold: file("chart-gold.json", chartGold()),
    aims: file("aims.json", { note: "synthetic", status: "synthetic", aim1: [{ name: "계획 2024", doc: "a", id: "p1-t1:r0c0#n2", raw: "120", right: ["p1-t1:r1c2"], evidence: "synthetic" }] }),
  };
  const goldArgs = (set: GoldSet, tag: string): string[] => ["--pairs", file(`${tag}-pairs.json`, set.pairs), "--plants", file(`${tag}-plants.json`, set.plants), "--gold2", file(`${tag}-gold2.json`, set.gold), "--plants2", file(`${tag}-plants2.json`, set.plants2)];
  const run2 = async (args: string[]): Promise<{ code: number; text: string }> => {
    const lines: string[] = [];
    const code = await cli2(args, (line) => lines.push(line), () => undefined, { env: {} });
    return { code, text: lines.join("\n") };
  };
  const derivedDocs = ["--a", paths.a, "--b", paths.b, "--exam-a", paths.examA, "--exam-b", paths.examB];

  it("옮긴 정답을 주면 파생 문서로 dry-run 이 돌고, 요청 글에 값 후보 숫자가 없다 (심은 값도)", async () => {
    const out = join(dir, "dry");
    const result = await run2(["run2", "--method", "d2", ...derivedDocs, ...goldArgs(moved, "moved"), "--dry-run", "--out", out]);
    expect(result.code).toBe(0);
    const report = JSON.parse(readFileSync(join(out, "dry-run.json"), "utf8")) as { calls: number; notes: { maskCheck: { unmaskedValues: number; sameAsOriginalDocuments: boolean } } };
    expect(report.notes.maskCheck).toMatchObject({ unmaskedValues: 0, sameAsOriginalDocuments: true });
    const texts = readdirSync(join(out, "requests")).map((name) => readFileSync(join(out, "requests", name), "utf8"));
    expect(texts.length).toBe(report.calls);
    for (const text of texts) {
      // 새 표가 요청 글에 표로 실리고, 값의 글자(원래 값 110~230, 심은 값 239, 글 속의 55)는 한 자도 없다
      expect(text).toContain(`[표 ${s.built.charts[0]!.tableId}]`);
      expect(text).toMatch(/c0: 계획 \| c1: ▒ \| c2: ▒ \| c3: ▒/);
      for (const value of ["110", "120", "130", "210", "220", "230", "239", "55"]) expect(text.split(/[^0-9]+/)).not.toContain(value);
    }
  });

  it("옮기지 않은 정답을 주면 거부된다", async () => {
    const result = await run2(["run2", "--method", "d2", ...derivedDocs, ...goldArgs(g, "unmoved"), "--dry-run", "--out", join(dir, "dry-unmoved")]);
    expect(result.code).toBe(1);
    expect(result.text).toContain("정답이 문서와 맞지 않는다");
  });

  it("채점: 모의 실행은 실측과 섞지 않고, 정답의 ID 는 그 실행이 쓴 문서의 것으로 옮겨서 센다", async () => {
    const mock = join(dir, "mock");
    expect((await run2(["run2", "--method", "d2", ...derivedDocs, ...goldArgs(moved, "moved"), "--provider", "mock", "--out", mock])).code).toBe(0);
    const score = async (args: string[]): Promise<{ code: number; text: string }> => {
      const lines: string[] = [];
      const code = await cli11(["score", "--chart-gold", paths.chartGold, "--aims", paths.aims, "--map-a", paths.mapA, "--map-b", paths.mapB, ...goldArgs(g, "unmoved"), ...args], (line) => lines.push(line));
      return { code, text: lines.join("\n") };
    };
    const refused = await score(["--space", "derived", ...derivedDocs, "--runs", `m=${mock}`, "--out", join(dir, "score-mock")]);
    expect(refused.text).toContain("모의 실행");
    expect(refused.code).toBe(2);
    // 지어낸 「실제 실행」: 도표 칸의 짝 둘(하나는 심은 칸)을 답으로 저장한다.
    const table = s.built.charts[0]!.tableId;
    const live = join(dir, "live");
    const answer = { pairs: [{ a: `a:${table}:r1c2`, b: "b:p1-t1:r1c2", unit_a: null, unit_b: null }, { a: `a:${table}:r2c3`, b: "b:p1-t1:r2c3", unit_a: null, unit_b: null }, { a: `a:${table}:r9c9`, b: "b:p1-t1:r1c1", unit_a: null, unit_b: null }] };
    const saved = JSON.parse(readFileSync(join(mock, "outcome.json"), "utf8")) as { outcome: { verdicts: unknown[] } };
    mkdirSync(live);
    writeFileSync(join(live, "units.jsonl"), `${JSON.stringify({ unitId: "d2/1", callId: "c1", result: { kind: "success", data: answer } })}\n`);
    writeFileSync(join(live, "outcome.json"), JSON.stringify({ method: "d2", outcome: { ...saved.outcome, verdicts: [{ a: `${table}:r1c2`, b: "p1-t1:r1c2", outcome: "match" }, { a: `${table}:r2c3`, b: "p1-t1:r2c3", outcome: "mismatch" }] } }));
    const ok = await score(["--space", "derived", ...derivedDocs, "--runs", `r1=${live}`, "--out", join(dir, "score-live")]);
    expect(ok.code).toBe(0);
    const result = JSON.parse(readFileSync(join(dir, "score-live", "score.json"), "utf8")) as { runs: { aims: { aim1: { paired: number; right: number }; aim2: { paired: number; total: number }; aim3: { caught: number; total: number }; chartCells: { accepted: number; total: number } }; rejected: Record<string, number> }[] };
    const aims = result.runs[0]!.aims;
    expect([aims.aim1.paired, aims.aim1.right]).toEqual([1, 1]);
    expect([aims.aim2.paired, aims.aim2.total]).toEqual([2, 3]);
    expect([aims.aim3.caught, aims.aim3.total]).toEqual([1, 1]);
    expect([aims.chartCells.accepted, aims.chartCells.total]).toEqual([2, 5]);
    expect(result.runs[0]!.rejected).toEqual({ unknown_id: 1 });
    // 파생 문서의 실행을 옮기지 않은 정답(원래 ID)으로 채점하려 하면 거부된다
    const wrongSpace = await score(["--space", "original", ...derivedDocs, "--runs", `r1=${live}`, "--out", join(dir, "score-wrong")]);
    expect([wrongSpace.code, wrongSpace.text.includes("ID 를 옮기지 않았는가")]).toEqual([2, true]);
  });
});
