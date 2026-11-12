import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { EmbeddingClient, MemorySink, MockEmbeddingProvider } from "llm";
// scope3 의 순위 함수와 같은 순위를 내는지 견주려고 테스트에서만 곧바로 읽는다 (reconcile 은 scope3 에 기대지 않는다).
import { fuseRrf as scope3FuseRrf, rankByEmbedding as scope3RankByEmbedding, rankLexicalV2 as scope3RankLexicalV2 } from "../../scope3/src/domain/ranking.js";
import { StaleIndexError, UNIT_TEXT_VERSION, assertFresh, buildUnitIndex, inputsSha256, loadUnitIndex, splitCalls, type IndexInput } from "../src/adapters/unitIndex.js";
import { main } from "../src/compare/cli14.js";
import { CHUNK_CHARS, chartUnits, chunkText, collapseToPages, cosine, deriveGold, fuseRrf, joinPieces, maskLine, pageUnits, parsePlace, queriesFor, rankEmbedding, rankLexical, recall, scoreQuery, scoreSpread, tableUnits, tokenize, valuesLeft, type JudgedPair, type NominateGold, type Unit } from "../src/compare/embed-nominate.js";
import { extractDocument } from "../src/index.js";
import { asDocumentJson, config, syntheticDocument, tempDir } from "./helpers.js";

// 밴드 EB1 (지목을 값 없이: 단위 글, 낱말 검색·임베딩·RRF, 정답 끌어내기, 채점)을 지어낸 문서로 본다 (synthetic). 어느 회사의 실제 값도 아니다.

const HEADER = { rows: [0], cols: [0], basis: "synthetic" };
const docA = () =>
  syntheticDocument(
    "a",
    [
      { id: "p1-t1", page: 1, cells: [["구분", "2024", "2025"], ["가상 전력 사용량", "1,234", "5,678"], ["가상 용수 사용량", "910", "1,112"]], header: HEADER, before: ["가상 환경 지표 (단위: MWh) 총 9,999 건"] },
      { id: "p2-t1", page: 2, cells: [["구분", "2025"], ["가상 임직원 수", "4,321"]], header: HEADER, before: ["가상 인력 현황"] },
    ],
    [{ id: "p1-b1", page: 1, text: "가상 회사는 2025년에 전력 3,456 MWh 를 썼다." }],
  );
const docB = () =>
  syntheticDocument("b", [
    { id: "p1-t1", page: 1, cells: [["지표", "2025"], ["가상 임직원 수", "4,321"], ["가상 이직률", "7.5%"]], header: HEADER, before: ["가상 인력 현황 상세"] },
    { id: "p2-t1", page: 2, cells: [["지표", "2024", "2025"], ["가상 전력 사용량", "1,234", "5,678"]], header: HEADER, before: ["가상 환경 지표"] },
    { id: "p2-t2", page: 2, cells: [["지표", "2025"], ["가상 기부금", "88,000"]], header: HEADER, before: ["가상 사회공헌"] },
  ]);
const extraction = (model: ReturnType<typeof docA>, side: "a" | "b") => extractDocument(model, side, { titles: "own", kinds: true });
const unit = (id: string, doc: "a" | "b", kind: string, page: number, extra: Partial<Unit> = {}): Unit => ({ id, doc, kind, page, table: null, places: [], text: id, chunks: [id], headerShare: null, ...extra });

describe("단위의 글과 가림", () => {
  it("표 단위는 앞의 글·머리글만 싣고 값 후보 숫자를 남기지 않는다 (연도 꼴은 남는다)", () => {
    const units = tableUnits(extraction(docA(), "a"), "a");
    const first = units.find((item) => item.id === "a:table:p1-t1") as Unit;
    expect(first.text).toContain("가상 전력 사용량");
    expect(first.text).toContain("2024");
    expect(first.text).not.toContain("1,234");
    expect(first.text).not.toContain("9,999");
    expect(first.text).toContain("▒");
    expect(units.flatMap((item) => valuesLeft(item.text))).toEqual([]);
    expect(first.headerShare).toBe(1);
    expect(first.places).toContain("p1-t1:r1c1");
  });

  it("가림을 끄면 값 후보 숫자가 글에 남는다 (가림이 실제로 하는 일)", () => {
    const off = tableUnits(extraction(docA(), "a"), "a", false);
    expect(off.flatMap((item) => valuesLeft(item.text))).toContain("9,999");
    const pagesOff = pageUnits(docA(), extraction(docA(), "a"), "a", false);
    expect(pagesOff.flatMap((item) => valuesLeft(item.text)).length).toBeGreaterThan(0);
  });

  it("쪽 단위는 글과 표의 글 칸을 싣고 숫자 칸은 싣지 않는다", () => {
    const pages = pageUnits(docA(), extraction(docA(), "a"), "a");
    expect(pages.map((item) => item.id)).toEqual(["a:page:1", "a:page:2"]);
    const first = pages[0] as Unit;
    expect(first.text).toContain("가상 회사는 2025년에 전력 ▒ MWh 를 썼다.");
    expect(first.text).toContain("가상 전력 사용량");
    expect(first.text).not.toContain("1,234");
    expect(first.text).not.toContain("3,456");
    expect(pages.flatMap((item) => valuesLeft(item.text))).toEqual([]);
  });

  it("도표 단위는 같은 쪽·같은 제목의 라벨을 묶어 제목·계열·범주를 싣는다", () => {
    const charts = chartUnits(extraction(docA(), "a"), "a", { "p1-t1:r1c1": "가상 전력 추이 · 본사 · 2024", "p1-t1:r1c2": "가상 전력 추이 · 본사 · 2025", "p1-t1:r2c1": "가상 용수 추이 · 1,500 톤", "p2-t1:r1c1": "가상 인력" }, "code");
    expect(charts.map((item) => item.id)).toEqual(["a:chart-code:p1#1", "a:chart-code:p1#2", "a:chart-code:p2#1"]);
    expect((charts[0] as Unit).text).toBe("가상 전력 추이\n계열: 본사\n범주: 2024 | 2025");
    expect((charts[0] as Unit).places).toEqual(["p1-t1:r1c1", "p1-t1:r1c2"]);
    expect((charts[1] as Unit).text).not.toContain("1,500");
    expect((charts[2] as Unit).text).toBe("가상 인력");
    expect(charts.flatMap((item) => valuesLeft(item.text))).toEqual([]);
  });

  it("이어 붙여 값 후보 꼴이 되는 조각도 가린다", () => {
    const joined = joinPieces("열: ", ["구분", "1", "2", "3"]);
    expect(valuesLeft(joined)).toEqual([]);
    expect(joined.startsWith("열: 구분")).toBe(true);
    expect(joinPieces("열: ", [])).toBe("");
    expect(maskLine("총 1,234 건", false)).toBe("총 1,234 건");
  });

  it("긴 글은 줄 경계에서 한도 안의 조각으로 나뉘고 글자를 잃지 않는다", () => {
    const text = Array.from({ length: 40 }, (_, index) => `줄 ${index} ${"가".repeat(200)}`).join("\n");
    const chunks = chunkText(text);
    expect(chunks.length).toBeGreaterThan(1);
    expect(chunks.every((chunk) => chunk.length <= CHUNK_CHARS)).toBe(true);
    expect(chunks.join("\n")).toBe(text);
    expect(chunkText("가".repeat(CHUNK_CHARS + 10)).map((chunk) => chunk.length)).toEqual([CHUNK_CHARS, 10]);
    expect(chunkText("짧은 글")).toEqual(["짧은 글"]);
  });
});

describe("순위", () => {
  const targets = [
    { id: "t1", text: "가상 인력 현황 임직원 수" },
    { id: "t2", text: "가상 환경 지표 전력 사용량" },
    { id: "t3", text: "기부금 사회공헌" },
  ];

  it("낱말 검색은 겹친 낱말이 많은 후보를 앞에 두고 겹치지 않는 후보는 내지 않는다", () => {
    const ranked = rankLexical("전력 사용량 환경", targets);
    expect(ranked.map((entry) => entry.id)).toEqual(["t2"]);
    expect(rankLexical("가상 전력", targets).map((entry) => entry.id)).toEqual(["t2", "t1"]);
    expect(tokenize("전력 Services")).toEqual(["전력", "#전력", "service"]);
  });

  it("코사인 순위: 가까운 벡터가 앞, 조각이 여럿이면 가장 큰 값, 동점은 후보의 차례", () => {
    const vectors: Record<string, number[][]> = { t1: [[1, 0]], t2: [[0, 1]], t3: [[0, 1], [1, 0]] };
    expect(cosine([1, 0], [1, 0])).toBeCloseTo(1);
    expect(cosine([1, 0], [0, 1])).toBeCloseTo(0);
    expect(cosine([0, 0], [0, 1])).toBe(0);
    const ranked = rankEmbedding([[1, 0]], targets, (id) => vectors[id]) as { id: string; score: number }[];
    expect(ranked.map((entry) => entry.id)).toEqual(["t1", "t3", "t2"]);
    expect(ranked[1]?.score).toBeCloseTo(1);
    expect(rankEmbedding([[1, 0]], targets, (id) => (id === "t2" ? undefined : vectors[id]))).toEqual({ missing: "t2" });
  });

  it("RRF 는 두 순위표를 다 쓴다: 한쪽에서만 1등인 후보보다 양쪽에서 2등인 후보가 앞선다", () => {
    const lexical = [{ id: "x", score: 9 }, { id: "both", score: 8 }];
    const embedding = [{ id: "y", score: 0.9 }, { id: "both", score: 0.8 }];
    const fused = fuseRrf([lexical, embedding], ["x", "y", "both"]);
    expect(fused.map((entry) => entry.id)).toEqual(["both", "x", "y"]);
    expect(fused[0]?.score).toBeCloseTo(2 / 62);
    expect(fused[2]?.score).toBeCloseTo(1 / 61);
    // 한쪽만 쓰면 순서가 달라진다.
    expect(fuseRrf([lexical], ["x", "y", "both"]).map((entry) => entry.id)).toEqual(["x", "both"]);
    expect(fuseRrf([embedding], ["x", "y", "both"]).map((entry) => entry.id)).toEqual(["y", "both"]);
  });

  it("scope3 의 순위 함수와 같은 순위·같은 점수를 낸다 (낱말 검색, 코사인, RRF)", () => {
    const docs = [
      { id: "f1", text: "가상 전력 사용량 본사 services" },
      { id: "f2", text: "가상 용수 사용량 batteries 공장" },
      { id: "f3", text: "임직원 수 가상 인력 현황" },
      { id: "f4", text: "가상 전력 생산량" },
    ];
    const factors = docs.map((doc) => ({ id: doc.id, name: doc.text, categoryPath: [] }) as unknown as Parameters<typeof scope3RankLexicalV2>[0][number]);
    const query = "가상 전력 사용량 service battery";
    const mine = rankLexical(query, docs);
    const theirs = scope3RankLexicalV2(factors, { texts: [query], keywords: [] } as Parameters<typeof scope3RankLexicalV2>[1], { bigrams: true });
    expect(mine.map((entry) => entry.id)).toEqual(theirs.map((entry) => entry.factor.id));
    mine.forEach((entry, index) => expect(entry.score).toBeCloseTo((theirs[index] as { score: number }).score, 10));
    const vectors: Record<string, number[]> = { f1: [0.9, 0.1, 0], f2: [0.2, 0.8, 0.1], f3: [0, 0.3, 0.9], f4: [0.7, 0.2, 0.2] };
    const mineE = rankEmbedding([[1, 0.2, 0]], docs, (id) => [vectors[id] as number[]]) as { id: string; score: number }[];
    const theirsE = scope3RankByEmbedding(factors, [1, 0.2, 0], { vector: (id: string) => vectors[id] }) as { factor: { id: string }; score: number }[];
    expect(mineE.map((entry) => entry.id)).toEqual(theirsE.map((entry) => entry.factor.id));
    const fusedMine = fuseRrf([mine, mineE], docs.map((doc) => doc.id));
    const fusedTheirs = scope3FuseRrf([theirs, theirsE as unknown as Parameters<typeof scope3FuseRrf>[0][number]]);
    expect(fusedMine.map((entry) => entry.id)).toEqual(fusedTheirs.map((entry) => entry.factor.id));
    fusedMine.forEach((entry, index) => expect(entry.score).toBeCloseTo((fusedTheirs[index] as { score: number }).score, 12));
  });

  it("단위의 순위를 쪽으로 합치면 쪽은 처음 나온 자리에 선다", () => {
    const pageOf = (id: string): number => ({ u1: 3, u2: 5, u3: 3, u4: 7 })[id] as number;
    expect(collapseToPages([{ id: "u1", score: 4 }, { id: "u2", score: 3 }, { id: "u3", score: 2 }, { id: "u4", score: 1 }], pageOf)).toEqual([{ page: 3, score: 4 }, { page: 5, score: 3 }, { page: 7, score: 1 }]);
  });
});

describe("정답 끌어내기", () => {
  const pairs: JudgedPair[] = [
    { source: "pairs", a: "p46-t2:r1c3", b: "p21-t1:r1c3", verdict: "same" },
    { source: "pairs", a: "p46-t2:r2c3", b: "p21-t1:r2c3", verdict: "same" },
    { source: "outside", a: "p46-t2:r1c5", b: "p77-t1:r2c0", verdict: "same" },
    { source: "gold-v2", a: "p41-t3:r2c3#n2", b: "p14-t2:r4c4", verdict: "same" },
    { source: "gold-v2", a: "p17-b7#n7", b: "p21-t1:r3c5", verdict: "same" },
    { source: "gold-v2", a: "p62-t2:r8c1#n3", b: "p22-t1:r4c3", verdict: "same", chart: true },
    { source: "gold-v2", a: "p37-t3:r2c4", b: "p4-b36#n1", verdict: "same", chart: true },
    { source: "recover", a: "p59-t1:r12c1", b: "p22-t1:r8c5", verdict: "different" },
    { source: "recover", a: "p46-t2:r1c3", b: "p21-t1:r1c5", verdict: "different" },
    { source: "outside", a: "p111-t1:r12c2", b: "p78-t1:r13c2", verdict: "other" },
    { source: "x", a: "이상한 ID", b: "p1-t1:r0c0", verdict: "same" },
  ];
  const gold = deriveGold(pairs, [{ doc: "a", page: 33, basis: "synthetic" }]);

  it("자리 ID 에서 쪽·표·숫자 칸 여부를 읽는다", () => {
    expect(parsePlace("a:p46-t2:r1c3")).toEqual({ page: 46, table: "p46-t2", numberCell: true });
    expect(parsePlace("p41-t3:r2c3#n2")).toEqual({ page: 41, table: "p41-t3", numberCell: false });
    expect(parsePlace("p17-b7#n7")).toEqual({ page: 17, table: null, numberCell: false });
    expect(parsePlace("엉뚱")).toBeNull();
  });

  it("표 짝·도표 짝을 묶고, 문장이 낀 짝과 값이 아닌 판정은 쓰지 않는다", () => {
    expect(gold.links.map((link) => `${link.kind} ${link.a.table}→${link.b.page}/${link.b.table}`)).toEqual(["chart p37-t3→4/null", "prose p41-t3→14/p14-t2", "cells p46-t2→21/p21-t1", "cells p46-t2→77/p77-t1", "chart p62-t2→22/p22-t1"]);
    expect(gold.links.find((link) => link.a.table === "p46-t2" && link.b.table === "p21-t1")?.support).toEqual({ pairs: 2 });
    expect(gold.unused).toEqual({ "같은 항목이지만 표 짝이 아님 (문장이 낌)": 1, "값이 아님 등의 판정": 1, "자리 ID 꼴이 아님": 1 });
  });

  it("「같은 항목 아님」만 있는 표만 상대 없음이다 (옳은 짝이 있는 표는 아니다)", () => {
    expect(gold.none.map((item) => `${item.kind} ${item.doc} ${item.page} ${item.table}`)).toEqual(["table a 59 p59-t1", "chart-page a 33 null"]);
  });
});

describe("채점", () => {
  const gold: NominateGold = {
    links: [
      { a: { doc: "a", page: 46, table: "p46-t2" }, b: { doc: "b", page: 21, table: "p21-t1" }, kind: "cells", support: {}, placesA: ["p46-t2:r1c3"], placesB: ["p21-t1:r1c3"] },
      { a: { doc: "a", page: 46, table: "p46-t2" }, b: { doc: "b", page: 77, table: "p77-t1" }, kind: "cells", support: {}, placesA: ["p46-t2:r1c5"], placesB: ["p77-t1:r2c0"] },
      { a: { doc: "a", page: 62, table: "p62-t2" }, b: { doc: "b", page: 22, table: "p22-t1" }, kind: "chart", support: {}, placesA: ["p62-t2:r8c1#n3"], placesB: ["p22-t1:r4c3"] },
    ],
    none: [{ doc: "a", page: 59, table: "p59-t1", kind: "table", basis: "" }, { doc: "a", page: 33, table: null, kind: "chart-page", basis: "" }],
    unused: {},
  };
  const a = [
    unit("a:table:p46-t2", "a", "table", 46, { table: "p46-t2" }),
    unit("a:table:p59-t1", "a", "table", 59, { table: "p59-t1" }),
    unit("a:table:p62-t2", "a", "table", 62, { table: "p62-t2" }),
    unit("a:table:p9-t1", "a", "table", 9, { table: "p9-t1" }),
    unit("a:chart-code:p62#1", "a", "chart-code", 62, { places: ["p62-t2:r8c1#n3"] }),
    unit("a:chart-code:p62#2", "a", "chart-code", 62, { places: ["p62-t2:r1c1"] }),
    unit("a:chart-code:p33#1", "a", "chart-code", 33, { places: ["p33-t2:r0c1"] }),
    unit("a:page:46", "a", "page", 46),
    unit("a:page:33", "a", "page", 33),
    unit("a:page:62", "a", "page", 62),
  ];
  const b = [unit("b:table:p21-t1", "b", "table", 21, { table: "p21-t1" }), unit("b:table:p22-t1", "b", "table", 22, { table: "p22-t1" }), unit("b:table:p77-t1", "b", "table", 77, { table: "p77-t1" }), unit("b:table:p21-t2", "b", "table", 21, { table: "p21-t2" })];
  const pageOf = (id: string): number => [...a, ...b].find((item) => item.id === id)?.page as number;

  it("방향마다 질의와 옳은 상대가 다르다 (a→b 의 질의는 보고서의 단위, b→a 의 질의는 데이터북의 단위)", () => {
    const forward = queriesFor(gold, "a", a, b);
    expect(forward.map((query) => `${query.unit} ${query.state} ${query.rightPages.join(",")} ${query.rightUnits.join(",")}`)).toEqual([
      "a:table:p46-t2 right 21,77 b:table:p21-t1,b:table:p77-t1",
      "a:table:p59-t1 none  ",
      "a:chart-code:p62#1 right 22 b:table:p22-t1",
      "a:chart-code:p33#1 none  ",
      "a:page:46 right 21,77 b:table:p21-t1,b:table:p77-t1",
      "a:page:33 none  ",
      "a:page:62 right 22 b:table:p22-t1",
    ]);
    const backward = queriesFor(gold, "b", b, a.filter((item) => item.kind === "table"));
    expect(backward.map((query) => `${query.unit} ${query.state} ${query.rightPages.join(",")} ${query.rightUnits.join(",")}`)).toEqual(["b:table:p21-t1 right 46 a:table:p46-t2", "b:table:p22-t1 right 62 ", "b:table:p77-t1 right 46 a:table:p46-t2"]);
    // 도표 짝은 보고서 쪽에서 도표가 얹힌 격자(표 단위)에는 걸리지 않고, 판정이 없는 단위는 질의가 아니다.
    expect(forward.some((query) => query.unit === "a:table:p62-t2" || query.unit === "a:table:p9-t1" || query.unit === "a:chart-code:p62#2")).toBe(false);
  });

  it("재현율@k 와 순위 분포: 옳은 쪽 가운데 가장 앞선 순위로 세고, 전부 드는지는 따로 센다", () => {
    const query = queriesFor(gold, "a", a, b)[0] as ReturnType<typeof queriesFor>[number];
    const ranked = [{ id: "b:table:p22-t1", score: 0.9 }, { id: "b:table:p21-t2", score: 0.8 }, { id: "b:table:p21-t1", score: 0.7 }, { id: "b:table:p77-t1", score: 0.6 }];
    const score = scoreQuery(query, ranked, pageOf);
    expect(score).toMatchObject({ pageRank: 2, pageRankAll: 3, unitRank: 3, topScore: 0.9, rightScore: 0.8, candidates: 4 });
    const missing = scoreQuery(query, [{ id: "b:table:p22-t1", score: 0.9 }], pageOf);
    expect(missing).toMatchObject({ pageRank: null, pageRankAll: null, unitRank: null, rightScore: null });
    const half = scoreQuery(query, [{ id: "b:table:p77-t1", score: 0.5 }], pageOf);
    expect(half).toMatchObject({ pageRank: 1, pageRankAll: null });
    const tally = recall([score, missing, half]);
    expect(tally).toEqual({ queries: 3, atK: { 1: 1, 3: 2, 5: 2 }, allAtK: { 1: 0, 3: 1, 5: 1 }, unranked: 1, ranks: [1, 2] });
    expect(recall([score, missing, half], "unit")).toMatchObject({ queries: 3, atK: { 1: 1, 3: 2, 5: 2 }, ranks: [1, 3] });
  });

  it("상대 없는 질의는 재현율의 분모에 들지 않고 맨 위 점수만 낸다", () => {
    const queries = queriesFor(gold, "a", a, b);
    const none = scoreQuery(queries[1] as (typeof queries)[number], [{ id: "b:table:p22-t1", score: 0.42 }], pageOf);
    const right = scoreQuery(queries[0] as (typeof queries)[number], [{ id: "b:table:p22-t1", score: 0.9 }, { id: "b:table:p21-t1", score: 0.7 }], pageOf);
    const empty = scoreQuery(queries[3] as (typeof queries)[number], [], pageOf);
    expect(none).toMatchObject({ state: "none", pageRank: null, topScore: 0.42 });
    expect(recall([none, right, empty]).queries).toBe(1);
    expect(scoreSpread([none, right, empty])).toEqual({ none: [0.42], right: [0.7], rightTop: [0.9], noneUnranked: 1, rightUnranked: 0 });
  });
});

describe("색인 파일", () => {
  const inputs: IndexInput[] = [{ id: "a:table:p1-t1#c0", text: "가상 전력 사용량" }, { id: "a:table:p2-t1#c0", text: "가상 임직원 수" }];
  const client = () => new EmbeddingClient({ config, providers: { mock: new MockEmbeddingProvider(64) }, log: new MemorySink() });
  const tags = { tenant: "tenant-synthetic", module: "reconcile" };

  it("만들고 쓰고 다시 읽으면 같은 벡터가 나온다", async () => {
    const built = await buildUnitIndex({ client: client(), route: "mock", tags, documentSha256: "d1", unitKind: "table", inputs });
    if (!built.ok) throw new Error("모의 임베딩이 실패했다");
    const path = join(tempDir(), "a.table.index");
    built.index.save(path);
    const loaded = loadUnitIndex(path, { documentSha256: "d1", modelKey: "mock-embedding-synthetic", dimensions: 64, unitKind: "table", inputsSha256: inputsSha256(inputs) });
    expect(loaded.header.ids).toEqual(inputs.map((input) => input.id));
    expect(Array.from(loaded.vector("a:table:p2-t1#c0") as Float32Array)).toEqual(Array.from(built.index.vector("a:table:p2-t1#c0") as Float32Array));
    expect(loaded.vector("없는 ID")).toBeUndefined();
    expect(loaded.header.textVersion).toBe(UNIT_TEXT_VERSION);
  });

  it("문서 해시·모델·차원·단위·글이 하나라도 다르면 낡은 색인으로 거부한다", async () => {
    const built = await buildUnitIndex({ client: client(), route: "mock", tags, documentSha256: "d1", unitKind: "table", inputs });
    if (!built.ok) throw new Error("모의 임베딩이 실패했다");
    const good = { documentSha256: "d1", modelKey: "mock-embedding-synthetic", dimensions: 64, unitKind: "table", inputsSha256: inputsSha256(inputs) };
    expect(() => assertFresh(built.index.header, good)).not.toThrow();
    expect(() => assertFresh(built.index.header, { ...good, documentSha256: "d2" })).toThrow(StaleIndexError);
    expect(() => assertFresh(built.index.header, { ...good, modelKey: "text-embedding-3-small" })).toThrow(StaleIndexError);
    expect(() => assertFresh(built.index.header, { ...good, dimensions: 1536 })).toThrow(StaleIndexError);
    expect(() => assertFresh(built.index.header, { ...good, unitKind: "page" })).toThrow(StaleIndexError);
    expect(() => assertFresh(built.index.header, { ...good, inputsSha256: inputsSha256([{ id: "a:table:p1-t1#c0", text: "다른 글" }]) })).toThrow(StaleIndexError);
    expect(() => assertFresh({ ...built.index.header, textVersion: UNIT_TEXT_VERSION + 1 }, good)).toThrow(StaleIndexError);
  });

  it("호출이 실패하면 색인을 만들지 않고 실패를 돌려준다", async () => {
    const provider = new MockEmbeddingProvider(64).enqueue({ kind: "error", status: 500, retryable: false, message: "synthetic failure" });
    const failing = new EmbeddingClient({ config, providers: { mock: provider }, log: new MemorySink() });
    const built = await buildUnitIndex({ client: failing, route: "mock", tags, documentSha256: "d1", unitKind: "table", inputs });
    expect(built.ok).toBe(false);
  });

  it("입력을 글자 수 한도로 호출 단위로 나눈다", () => {
    const many = Array.from({ length: 5 }, (_, index) => ({ id: `i${index}`, text: "가".repeat(40) }));
    expect(splitCalls(many, 100).map((call) => call.length)).toEqual([2, 2, 1]);
    expect(splitCalls(many, 10).map((call) => call.length)).toEqual([1, 1, 1, 1, 1]);
  });
});

describe("명령 (모의 임베딩)", () => {
  const setup = () => {
    const dir = tempDir();
    const write = (name: string, value: unknown): string => {
      const path = join(dir, name);
      writeFileSync(path, JSON.stringify(value));
      return path;
    };
    const a = write("a.json", asDocumentJson(docA()));
    const b = write("b.json", asDocumentJson(docB()));
    const labels = write("labels.json", { a: { "p1-t1:r1c1": "가상 환경 지표 · 가상 전력 사용량 · 2024", "p1-t1:r1c2": "가상 환경 지표 · 가상 전력 사용량 · 2025" }, b: {} });
    const gold = write("gold.json", { links: [{ a: { doc: "a", page: 1, table: "p1-t1" }, b: { doc: "b", page: 2, table: "p2-t1" }, kind: "cells", support: { synthetic: 1 }, placesA: ["p1-t1:r1c1"], placesB: ["p2-t1:r1c1"] }, { a: { doc: "a", page: 2, table: "p2-t1" }, b: { doc: "b", page: 1, table: "p1-t1" }, kind: "cells", support: { synthetic: 1 }, placesA: ["p2-t1:r1c1"], placesB: ["p1-t1:r1c1"] }], none: [], unused: {} });
    const common = ["--a", a, "--b", b, "--chart-labels", `code=${labels}`];
    return { dir, a, b, common, gold, write };
  };
  const run = async (args: string[]): Promise<{ code: number; text: string }> => {
    const lines: string[] = [];
    const code = await main(args, (line) => lines.push(line), () => undefined, { env: {} });
    return { code, text: lines.join("\n") };
  };

  it("단위 → 색인 → 지목·채점이 키 없이 돌고, 모의 결과에는 표시가 남는다", async () => {
    const { dir, common, gold } = setup();
    expect((await run(["units", ...common, "--out", join(dir, "out")])).code).toBe(0);
    const stored = JSON.parse(readFileSync(join(dir, "out", "units.json"), "utf8")) as { units: Unit[] };
    expect(stored.units.flatMap((item) => [item.text, ...item.chunks]).flatMap(valuesLeft)).toEqual([]);
    expect((await run(["dry-run", ...common, "--out", join(dir, "out"), "--route", "mock"])).code).toBe(0);
    expect((await run(["index", ...common, "--provider", "mock", "--out", join(dir, "out")])).code).toBe(0);
    const calls = readFileSync(join(dir, "out", "calls.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line) as { promptId: string; model: string; body: { request: { inputs: string[] } } });
    expect(calls.every((call) => call.promptId === "embedding" && call.model === "mock-embedding-synthetic")).toBe(true);
    expect(calls.flatMap((call) => call.body.request.inputs).flatMap(valuesLeft)).toEqual([]);
    // 있는 색인은 다시 만들지 않는다: 두 번째 실행은 호출 기록을 늘리지 않는다.
    expect((await run(["index", ...common, "--provider", "mock", "--out", join(dir, "out")])).code).toBe(0);
    expect(readFileSync(join(dir, "out", "calls.jsonl"), "utf8").trim().split("\n").length).toBe(calls.length);
    const ranked = await run(["rank", ...common, "--gold", gold, "--index", join(dir, "out", "index"), "--out", join(dir, "out", "rank")]);
    expect(ranked.code).toBe(0);
    expect(ranked.text).toContain("모의 임베딩으로 낸 표다");
    const scores = JSON.parse(readFileSync(join(dir, "out", "rank", "scores.json"), "utf8")) as { synthetic?: string; combos: { source: string; queryKind: string; targetKind: string; right: number; methods: Record<string, { recallPage: { queries: number; atK: Record<string, number> } }> }[] };
    expect(scores.synthetic).toBeDefined();
    const forward = scores.combos.find((combo) => combo.source === "a" && combo.queryKind === "table" && combo.targetKind === "table");
    expect(forward?.right).toBe(2);
    // 지어낸 문서는 옳은 상대와 낱말이 겹치게 만들었다: 낱말 검색이 둘 다 1등으로 짚는다.
    expect(forward?.methods.lexical?.recallPage).toMatchObject({ queries: 2, atK: { 1: 2 } });
    const backward = scores.combos.find((combo) => combo.source === "b" && combo.queryKind === "table" && combo.targetKind === "table");
    expect(backward?.right).toBe(2);
  });

  it("문서가 바뀌면 낡은 색인으로 채점하지 않고, 색인을 덮어쓰지도 않는다", async () => {
    const { dir, common, gold, a, write } = setup();
    expect((await run(["index", ...common, "--provider", "mock", "--out", join(dir, "out")])).code).toBe(0);
    const before = readFileSync(join(dir, "out", "index", "a.table.index"));
    const changed = docA();
    (changed.tables[0] as { cells: string[][] }).cells[1] = ["가상 가스 사용량", "1,234", "5,678"];
    writeFileSync(a, JSON.stringify(asDocumentJson(changed)));
    void write;
    const ranked = await run(["rank", ...common, "--gold", gold, "--index", join(dir, "out", "index"), "--out", join(dir, "out", "rank")]);
    expect(ranked.code).toBe(1);
    expect(ranked.text).toContain("StaleIndexError");
    const again = await run(["index", ...common, "--provider", "mock", "--out", join(dir, "out")]);
    expect(again.code).toBe(1);
    expect(readFileSync(join(dir, "out", "index", "a.table.index")).equals(before)).toBe(true);
    expect(existsSync(join(dir, "out", "rank", "scores.json"))).toBe(false);
  });

  it("실제 공급사는 경로·상한·호출 기록·키 없이 시작하지 않는다", async () => {
    const { dir, common } = setup();
    expect((await run(["index", ...common, "--provider", "openai", "--out", join(dir, "out")])).code).toBe(1);
    expect((await run(["index", ...common, "--provider", "openai", "--route", "light-openai", "--out", join(dir, "out")])).code).toBe(1);
    expect((await run(["index", ...common, "--provider", "openai", "--route", "light-openai", "--limit-usd", "0.01", "--out", join(dir, "out")])).code).toBe(1);
    const noKey = await run(["index", ...common, "--provider", "openai", "--route", "light-openai", "--limit-usd", "0.01", "--log", join(dir, "calls.jsonl"), "--out", join(dir, "out")]);
    expect(noKey.code).toBe(1);
    expect(noKey.text).toContain("OPENAI_API_KEY");
    expect(existsSync(join(dir, "out", "index", "a.table.index"))).toBe(false);
  });
});
