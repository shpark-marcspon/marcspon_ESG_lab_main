import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { BudgetGuard, EmbeddingClient, MemorySink, MockEmbeddingProvider, defaultConfigPaths, loadConfig, type BudgetLimit } from "llm";
import { describe, expect, it } from "vitest";
import { main } from "../src/cli.js";
import type { DryRunPlan, EvalReport } from "../src/evaluate.js";
import {
  DEFAULT_SHORTLIST_METHOD,
  EmbeddingShortlister,
  FactorStore,
  LexicalV1Shortlister,
  LexicalV2Shortlister,
  RRF_K,
  StaleIndexError,
  buildEmbeddingIndex,
  defaultShortlister,
  evaluate,
  fuseRrf,
  lexicalShortlister,
  loadEmbeddingIndex,
  measureRecall,
  rankLexicalV2,
  recallTargets,
  runPipeline,
  singular,
  tokenize,
  type ExampleRow,
  type Factor,
  type RankedCandidate,
  type Shortlister,
} from "../src/index.js";
import { SYNTHETIC_FACTORS, TAGS, classification, harness, recommendation, row, writeSyntheticFactors } from "./helpers.js";

// 전부 가상(synthetic) 계수와 가상 임베딩이다. 실제 공급사를 부르지 않는다.
const config = loadConfig(defaultConfigPaths({ synthetic: true }));

function spendFactors(names: string[]): Factor[] {
  const path = writeSyntheticFactors(names.map((name, i) => ({ id: `syn:s${i}`, name, categories: [1], type: "spend" as const, per: "2022 USD, purchaser price", value: "0.1" })));
  return FactorStore.load(path).candidates({ category: 1, basis: "spend" });
}

function ids(ranked: readonly RankedCandidate[]): string[] {
  return ranked.map((entry) => entry.factor.id);
}

async function embeddingHarness(options: { limits?: BudgetLimit[]; factorsPath?: string } = {}) {
  const factorsPath = options.factorsPath ?? writeSyntheticFactors();
  const store = FactorStore.load(factorsPath);
  const provider = new MockEmbeddingProvider(64);
  const log = new MemorySink();
  const client = new EmbeddingClient({ config, providers: { mock: provider }, log, budget: new BudgetGuard(options.limits ?? []) });
  const built = await buildEmbeddingIndex({ client, route: "mock", tags: TAGS, store, factors: store.all(), now: () => new Date("2026-10-02T00:00:00Z") });
  if (!built.ok) throw new Error("가상 색인을 만들지 못했다");
  return { store, provider, log, client, index: built.index, factorsPath };
}

describe("낱말 단위 검색 (lexical-v2)", () => {
  it("낱말이 통째로 같아야 맞는다: car 는 cargo 에 맞지 않는다", async () => {
    const factors = spendFactors(["Synthetic cargo ship", "Synthetic car rental", "Synthetic scar tissue"]);
    const ranked = rankLexicalV2(factors, { texts: [], keywords: ["car"] }, { bigrams: true });
    expect(ids(ranked)).toEqual(["syn:s1"]);
    expect(ranked[0]?.evidence.lexical?.matchedTerms).toEqual(["car"]);
    // 처음 방식은 부분 문자열로 맞춰 셋 다 걸린다.
    const v1 = await new LexicalV1Shortlister().shortlist(factors, { texts: [], keywords: ["car"] }, 3);
    expect(v1.ok && v1.lexicalMatches).toBe(3);
  });

  it("영어 복수형만 접고 두 글자 미만은 버린다", () => {
    expect(["services", "batteries", "glasses", "gas", "bus", "analysis", "cars"].map(singular)).toEqual(["service", "battery", "glass", "gas", "bus", "analysis", "car"]);
    expect(tokenize("Computer & Peripheral-Equipment Manufacturers, A 4", { bigrams: true })).toEqual(["computer", "peripheral", "equipment", "manufacturer"]);
    expect(tokenize("합성 강판", { bigrams: true })).toEqual(["합성", "#합성", "강판", "#강판"]);
    expect(tokenize("폴리프로필렌", { bigrams: false })).toEqual(["폴리프로필렌"]);
  });

  it("드문 낱말이 흔한 낱말보다 무겁다", () => {
    const factors = spendFactors(["Synthetic widget manufacturing", "Synthetic gadget manufacturing", "Synthetic gizmo manufacturing", "Synthetic elevator repair"]);
    const ranked = rankLexicalV2(factors, { texts: [], keywords: ["elevator manufacturing"] }, { bigrams: true });
    // elevator 는 한 건에만, manufacturing 은 세 건에 있다. 낱말 하나씩 겹쳤어도 드문 쪽이 위다.
    expect(ids(ranked)).toEqual(["syn:s3", "syn:s0", "syn:s1", "syn:s2"]);
    expect(ranked[0]?.score).toBeGreaterThan((ranked[1]?.score as number) * 2);
  });

  it("동점은 후보 표의 순서를 따르고, 겹친 낱말이 없는 후보는 그 뒤에 표 순서로 붙는다", async () => {
    const factors = spendFactors(["Synthetic legal alpha", "Synthetic pump beta", "Synthetic pump gamma", "Synthetic legal delta"]);
    const ranked = rankLexicalV2(factors, { texts: [], keywords: ["pump"] }, { bigrams: true });
    expect(ids(ranked)).toEqual(["syn:s1", "syn:s2"]);
    expect(ranked[0]?.score).toBe(ranked[1]?.score);
    const outcome = await new LexicalV2Shortlister().shortlist(factors, { texts: [], keywords: ["pump"] }, 3);
    expect(outcome.ok && outcome.factors.map((factor) => factor.id)).toEqual(["syn:s1", "syn:s2", "syn:s0"]);
    expect(outcome.ok && outcome.lexicalMatches).toBe(2);
    expect(outcome.ok && outcome.evidence.map((entry) => entry.lexical?.rank)).toEqual([1, 2, undefined]);
  });

  it("한글 두 글자 조각은 설정으로 끈다", () => {
    const factors = spendFactors(["합성 폴리프로필렌수지", "합성 강판"]);
    const query = { texts: ["프로필렌 원료"], keywords: [] };
    expect(ids(rankLexicalV2(factors, query, { bigrams: true }))).toEqual(["syn:s0"]);
    expect(ids(rankLexicalV2(factors, query, { bigrams: false }))).toEqual([]);
  });
});

describe("순위 합산 (RRF)", () => {
  it("각 순위표의 1/(k+순위) 를 더하고, 한 표에만 있는 후보는 그 표의 몫만 받는다", () => {
    const [a, b, c] = spendFactors(["A synthetic", "B synthetic", "C synthetic"]) as [Factor, Factor, Factor];
    const entry = (factor: Factor, index: number): RankedCandidate => ({ factor, index, score: 0, evidence: { id: factor.id } });
    const fused = fuseRrf([[entry(a, 0), entry(b, 1)], [entry(b, 1), entry(c, 2), entry(a, 0)]]);
    expect(ids(fused)).toEqual(["syn:s1", "syn:s0", "syn:s2"]);
    expect(fused[0]?.score).toBeCloseTo(1 / (RRF_K + 2) + 1 / (RRF_K + 1), 12);
    expect(fused[1]?.score).toBeCloseTo(1 / (RRF_K + 1) + 1 / (RRF_K + 3), 12);
    expect(fused[2]?.score).toBeCloseTo(1 / (RRF_K + 2), 12);
    expect(RRF_K).toBe(60);
  });

  it("합산 점수가 같으면 후보 표의 순서를 따른다", () => {
    const [a, b] = spendFactors(["A synthetic", "B synthetic"]) as [Factor, Factor];
    const entry = (factor: Factor, index: number): RankedCandidate => ({ factor, index, score: 0, evidence: { id: factor.id } });
    expect(ids(fuseRrf([[entry(b, 1), entry(a, 0)], [entry(a, 0), entry(b, 1)]]))).toEqual(["syn:s0", "syn:s1"]);
  });
});

describe("임베딩 색인", () => {
  it("모델 ID, 계수 표의 해시, 만든 날짜를 싣고 파일로 저장했다 다시 읽는다", async () => {
    const { index, store } = await embeddingHarness();
    expect(index.header).toMatchObject({ modelKey: "mock-embedding-synthetic", apiModelId: "mock-embedding-synthetic", dimensions: 64, factorsSha256: store.sha256, createdAt: "2026-10-02T00:00:00.000Z" });
    expect(index.header.ids).toEqual(store.all().map((factor) => factor.id));
    const path = join(mkdtempSync(join(tmpdir(), "scope3-index-")), "index.bin");
    index.save(path);
    const loaded = loadEmbeddingIndex(path, { factorsSha256: store.sha256, modelKey: "mock-embedding-synthetic", dimensions: 64 });
    expect(loaded.size).toBe(store.candidateCount);
    expect(Array.from(loaded.vector("syn:truck") as Float32Array)).toEqual(Array.from(index.vector("syn:truck") as Float32Array));
    expect(loaded.vector("syn:gone")).toBeUndefined();
  });

  it("계수 표가 바뀌면 낡은 색인을 거부한다", async () => {
    const { index } = await embeddingHarness();
    const path = join(mkdtempSync(join(tmpdir(), "scope3-index-")), "index.bin");
    index.save(path);
    const changed = FactorStore.load(writeSyntheticFactors([...SYNTHETIC_FACTORS, { id: "syn:new", name: "Synthetic new factor", categories: [1], type: "activity", per: "kg", value: "1" }]));
    expect(changed.sha256).not.toBe(index.header.factorsSha256);
    expect(() => loadEmbeddingIndex(path, { factorsSha256: changed.sha256, modelKey: "mock-embedding-synthetic", dimensions: 64 })).toThrow(StaleIndexError);
  });

  it("다른 모델이나 다른 차원으로 만든 색인을 거부한다", async () => {
    const { index, store } = await embeddingHarness();
    const path = join(mkdtempSync(join(tmpdir(), "scope3-index-")), "index.bin");
    index.save(path);
    expect(() => loadEmbeddingIndex(path, { factorsSha256: store.sha256, modelKey: "other-model", dimensions: 64 })).toThrow(StaleIndexError);
    expect(() => loadEmbeddingIndex(path, { factorsSha256: store.sha256, modelKey: "mock-embedding-synthetic", dimensions: 32 })).toThrow(StaleIndexError);
  });

  it("임베딩 호출이 실패하면 색인을 만들지 않는다", async () => {
    const store = FactorStore.load(writeSyntheticFactors());
    const provider = new MockEmbeddingProvider(64).enqueue({ kind: "error", status: 503, retryable: true, message: "가상 오류 (synthetic)" });
    const client = new EmbeddingClient({ config, providers: { mock: provider }, log: new MemorySink() });
    expect(await buildEmbeddingIndex({ client, route: "mock", tags: TAGS, store, factors: store.all() })).toMatchObject({ ok: false, failure: { kind: "provider_error" } });
  });
});

describe("임베딩·합친 방식", () => {
  it("유사도 순으로 추리고 후보별 근거(유사도 순위, 겹친 낱말, 합산 점수)를 싣는다", async () => {
    const { store, client, index, provider } = await embeddingHarness();
    const candidates = store.candidates({ category: 1, basis: "spend" });
    const query = { texts: ["합성 서버"], keywords: ["synthetic computer manufacturing"] };
    const embedding = await new EmbeddingShortlister({ client, route: "mock", tags: TAGS, index, queryMode: "item+keywords" }).shortlist(candidates, query, 2);
    expect(embedding).toMatchObject({ ok: true, method: "embedding", total: 3, lexicalMatches: null });
    expect(embedding.ok && embedding.factors[0]?.id).toBe("syn:spend-computer");
    expect(embedding.ok && embedding.evidence[0]).toMatchObject({ id: "syn:spend-computer", rank: 1, embedding: { rank: 1 } });
    expect(embedding.embeddingCostUsd).toBeGreaterThan(0);

    const hybrid = new EmbeddingShortlister({ client, route: "mock", tags: TAGS, index, queryMode: "item+keywords", fuseWith: { bigrams: true } });
    const before = provider.calls.length;
    const first = await hybrid.shortlist(candidates, query, 3);
    expect(first).toMatchObject({ ok: true, method: "hybrid" });
    const top = first.ok ? first.evidence[0] : undefined;
    expect(top).toMatchObject({ id: "syn:spend-computer", rank: 1, lexical: { rank: 1 }, embedding: { rank: 1 } });
    expect(top?.lexical?.matchedTerms).toEqual(expect.arrayContaining(["computer", "manufacturing"]));
    expect(top?.rrfScore).toBeCloseTo(2 / (RRF_K + 1), 12);
    // 같은 질의는 다시 임베딩하지 않는다.
    await hybrid.shortlist(candidates, query, 3);
    expect(provider.calls.length).toBe(before + 1);
  });

  it("질의를 품목명만으로 할지 영어 검색어를 붙일지 설정으로 고른다", async () => {
    const { store, client, index, provider } = await embeddingHarness();
    const candidates = store.candidates({ category: 1, basis: "spend" });
    const query = { texts: ["법률 자문"], keywords: ["legal services"] };
    await new EmbeddingShortlister({ client, route: "mock", tags: TAGS, index, queryMode: "item" }).shortlist(candidates, query, 2);
    await new EmbeddingShortlister({ client, route: "mock", tags: TAGS, index, queryMode: "item+keywords" }).shortlist(candidates, query, 2);
    expect(provider.calls.slice(-2).map((call) => call.inputs)).toEqual([["법률 자문"], ["법률 자문 | legal services"]]);
  });

  it("임베딩이 실패하면 낱말 검색으로 넘어가지 않고 명시적 상태로 남긴다", async () => {
    const { store, client, index, provider } = await embeddingHarness();
    const shortlister = new EmbeddingShortlister({ client, route: "mock", tags: TAGS, index, queryMode: "item+keywords", fuseWith: { bigrams: true } });
    const { mock, pipeline } = harness({ store });
    mock.enqueueText(classification(1, ["steel sheet"]));
    provider.enqueue({ kind: "error", status: 500, retryable: true, message: "가상 오류 (synthetic)" });
    const example: ExampleRow = { row: row({ itemName: "합성 강판" }), gold: { category: 1, factorIds: ["syn:steel-kg"], note: "synthetic" } } as ExampleRow;
    const result = await runPipeline([example.row], { ...pipeline, shortlister });

    // 분류 1회뿐이다. 추천 호출은 하지 않았다.
    expect(mock.calls).toHaveLength(1);
    expect(result.rows[0]?.decision).toMatchObject({ status: "shortlist_failed", method: "hybrid", basis: "physical", shortlist: { kind: "provider_error", detail: "가상 오류 (synthetic)" } });
    expect(result.rows[0]?.calc).toEqual({ status: "not_calculated", reason: "shortlist_failed" });
    expect(result.groups[0]?.shortlists).toMatchObject([{ method: "hybrid", shownIds: [], failure: { kind: "provider_error" } }]);
    const report = evaluate([example], result, store);
    expect(report.llmFailures).toEqual({ "shortlist:provider_error": 1 });
    expect(report.shortlistRecall).toMatchObject({ evaluated: 0, notReached: 1 });
    expect(report.shortlist.method).toBe("hybrid");
  });

  it("임베딩 호출도 비용 상한을 탄다: 상한에 닿으면 budget_exceeded 상태다", async () => {
    // 색인을 만드는 비용(가상)이 이미 상한을 넘는다.
    const { store, client, index, provider } = await embeddingHarness({ limits: [{ scope: { module: TAGS.module }, limitUsd: 0.000001 }] });
    const calls = provider.calls.length;
    const outcome = await new EmbeddingShortlister({ client, route: "mock", tags: TAGS, index, queryMode: "item" }).shortlist(store.candidates({ category: 1, basis: "spend" }), { texts: ["합성 서버"], keywords: [] }, 2);
    expect(outcome).toMatchObject({ ok: false, method: "embedding", failure: { kind: "budget_exceeded" } });
    expect(provider.calls.length).toBe(calls);
  });

  it("색인에 벡터가 없는 후보가 있으면 실패로 남긴다", async () => {
    const { client, index } = await embeddingHarness();
    const wider = FactorStore.load(writeSyntheticFactors([...SYNTHETIC_FACTORS, { id: "syn:spend-new", name: "Synthetic New Manufacturing", categories: [1], type: "spend", per: "2022 USD, purchaser price", value: "0.3" }]));
    const outcome = await new EmbeddingShortlister({ client, route: "mock", tags: TAGS, index, queryMode: "item" }).shortlist(wider.candidates({ category: 1, basis: "spend" }), { texts: ["합성 서버"], keywords: [] }, 2);
    expect(outcome).toMatchObject({ ok: false, failure: { kind: "index_missing_factor" } });
  });

  it("색인을 만든 모델과 다른 모델의 경로로는 조립되지 않는다", async () => {
    const { client, index } = await embeddingHarness();
    const other = Object.assign(Object.create(Object.getPrototypeOf(index)), index, { header: { ...index.header, modelKey: "other-model" } });
    expect(() => new EmbeddingShortlister({ client, route: "mock", tags: TAGS, index: other, queryMode: "item" })).toThrow("색인의 모델");
  });
});

describe("추리기 방식 설정", () => {
  it("파이프라인의 기본은 키 없이 도는 lexical-v2 이고 결과에 방식과 근거가 실린다", async () => {
    const { mock, pipeline } = harness();
    expect(pipeline.shortlister).toBeUndefined();
    mock.enqueueText(classification(1, ["steel"])).enqueueText(recommendation(["syn:steel-t"]));
    const result = await runPipeline([row({ itemName: "합성 강판" })], pipeline);
    expect(result.shortlist).toMatchObject({ method: "lexical-v2", embeddingCostUsd: 0 });
    expect(result.shortlist.description).toBe(new LexicalV2Shortlister().description);
    expect(result.groups[0]?.shortlists[0]).toMatchObject({ method: "lexical-v2", failure: null });
    expect(result.groups[0]?.shortlists[0]?.evidence[0]?.lexical?.matchedTerms.length).toBeGreaterThan(0);
    expect(result.rows[0]?.decision).toMatchObject({ status: "recommended", candidates: { method: "lexical-v2" } });
  });

  it("기본값은 한 자리에서 나온다: 상수, 기본 추리기, 방식 이름으로 만든 추리기", () => {
    expect(DEFAULT_SHORTLIST_METHOD).toBe("lexical-v2");
    expect(defaultShortlister().method).toBe(DEFAULT_SHORTLIST_METHOD);
    expect(defaultShortlister()).toBeInstanceOf(LexicalV2Shortlister);
    expect(lexicalShortlister("lexical-v1")).toBeInstanceOf(LexicalV1Shortlister);
    expect(lexicalShortlister("lexical-v2", { bigrams: false }).description).toContain("안 씀");
  });

  it("lexical-v1 을 고르면 파이프라인이 처음 방식으로 추린다 (기본 방식과 다른 목록)", async () => {
    // car 는 v1 에서 cargo·scar 에 부분 문자열로 맞고 v2 에서는 낱말이 통째로 같은 car rental 만 맞는다.
    const names = ["Synthetic cargo ship", "Synthetic scar tissue", "Synthetic car rental", "Synthetic legal"];
    const factorsPath = writeSyntheticFactors(names.map((name, i) => ({ id: `syn:s${i}`, name, categories: [1], type: "spend" as const, per: "2022 USD, purchaser price", value: "0.1" })));
    const shown = async (shortlister?: Shortlister) => {
      const { mock, pipeline } = harness({ store: FactorStore.load(factorsPath), shortlistSize: 2 });
      mock.enqueueText(classification(1, ["car"])).enqueueText(recommendation(["syn:s2"]));
      const result = await runPipeline([row({ itemName: "렌터카" })], { ...pipeline, ...(shortlister ? { shortlister } : {}) });
      return { method: result.shortlist.method, description: result.shortlist.description, ids: result.groups[0]?.shortlists[0]?.shownIds };
    };
    const v1 = await shown(new LexicalV1Shortlister());
    expect(v1).toEqual({ method: "lexical-v1", description: new LexicalV1Shortlister().description, ids: ["syn:s0", "syn:s1"] });
    const base = await shown();
    expect(base.method).toBe("lexical-v2");
    expect(base.ids?.[0]).toBe("syn:s2");
  });

  it("lexical-v1 추리기는 처음의 shortlist 함수와 같은 목록을 낸다", async () => {
    const factors = spendFactors(["Synthetic cargo ship", "Synthetic car rental", "Synthetic scar tissue", "Synthetic legal"]);
    const outcome = await new LexicalV1Shortlister().shortlist(factors, { texts: [], keywords: ["car"] }, 2);
    expect(outcome.ok && outcome.factors.map((factor) => factor.id)).toEqual(["syn:s0", "syn:s1"]);
    expect(outcome.ok && outcome.lexicalMatches).toBe(3);
  });
});

async function run(args: string[]) {
  const lines: string[] = [];
  const code = await main(args, (line) => lines.push(line), { env: {} });
  return { code, output: lines.join("\n") };
}

describe("CLI 의 추리기 설정 (모의 공급사, 가상 임베딩)", () => {
  const EXAMPLES = "../data/scope3-examples";

  it("모르는 방식, 색인 없는 임베딩 방식, dry-run 과 임베딩 방식의 조합을 거부한다", async () => {
    expect((await run(["eval", EXAMPLES, "--provider", "mock", "--shortlist-method", "bm42"])).code).toBe(2);
    const noIndex = await run(["eval", EXAMPLES, "--provider", "mock", "--shortlist-method", "embedding"]);
    expect(noIndex.code).toBe(2);
    expect(noIndex.output).toContain("--index");
    expect((await run(["eval", EXAMPLES, "--dry-run", "--shortlist-method", "hybrid", "--index", "x"])).code).toBe(2);
    expect((await run(["eval", EXAMPLES, "--provider", "mock", "--embedding-query", "nope"])).code).toBe(2);
  });

  it("기본은 lexical-v2, --shortlist-method 로 lexical-v1 과 lexical-v2 의 설정을 고른다", async () => {
    const base = JSON.parse((await run(["eval", EXAMPLES, "--provider", "mock", "--json", "--limit-rows", "20"])).output) as { report: EvalReport };
    expect(base.report.shortlist.method).toBe("lexical-v2");
    expect(base.report.shortlist.description).toContain("조각 씀");
    const explicit = JSON.parse((await run(["eval", EXAMPLES, "--provider", "mock", "--json", "--limit-rows", "20", "--shortlist-method", "lexical-v2"])).output) as { report: EvalReport };
    expect(explicit).toEqual(base);
    const v1 = JSON.parse((await run(["eval", EXAMPLES, "--provider", "mock", "--json", "--limit-rows", "20", "--shortlist-method", "lexical-v1"])).output) as { report: EvalReport };
    expect(v1.report.shortlist).toMatchObject({ method: "lexical-v1", description: new LexicalV1Shortlister().description, embeddingCostUsd: 0 });
    const v2 = JSON.parse((await run(["eval", EXAMPLES, "--provider", "mock", "--json", "--limit-rows", "20", "--shortlist-method", "lexical-v2", "--no-bigrams"])).output) as { report: EvalReport };
    expect(v2.report.shortlist).toMatchObject({ method: "lexical-v2", embeddingCostUsd: 0 });
    expect(v2.report.shortlist.description).toContain("안 씀");
  });

  it("--dry-run 은 키 없이 기본 lexical-v2 로 어림하고, lexical-v1 을 고르면 처음 방식으로 어림한다", async () => {
    type Plan = { plan: DryRunPlan };
    const base = JSON.parse((await run(["eval", EXAMPLES, "--dry-run", "--json"])).output) as Plan;
    expect(base.plan.shortlistMethod).toBe("lexical-v2");
    const explicit = JSON.parse((await run(["eval", EXAMPLES, "--dry-run", "--json", "--shortlist-method", "lexical-v2"])).output) as Plan;
    expect(explicit).toEqual(base);
    const v1 = JSON.parse((await run(["eval", EXAMPLES, "--dry-run", "--json", "--shortlist-method", "lexical-v1"])).output) as Plan;
    expect(v1.plan.shortlistMethod).toBe("lexical-v1");
    // 호출 수는 추리기와 무관하고, 추천 프롬프트에 들어가는 후보가 달라 입력 토큰 어림이 달라진다.
    expect(v1.plan.minimum.calls).toBe(base.plan.minimum.calls);
    expect(v1.plan.maximum.calls).toBe(base.plan.maximum.calls);
    expect(v1.plan.minimum.inputTokens).not.toBe(base.plan.minimum.inputTokens);
    const text = (await run(["eval", EXAMPLES, "--dry-run"])).output;
    expect(text).toContain("추리기 lexical-v2");
  });

  it("실제 공급사의 임베딩은 키·임베딩 경로·상한이 없으면 조립되지 않는다", async () => {
    const dir = mkdtempSync(join(tmpdir(), "scope3-cli-"));
    expect((await run(["index", "--index", join(dir, "i.bin"), "--provider", "openai", "--embedding-route", "light-openai"])).output).toContain("--limit-usd");
    expect((await run(["index", "--index", join(dir, "i.bin"), "--provider", "openai", "--limit-usd", "0.1"])).output).toContain("--embedding-route");
    const noKey = await run(["index", "--index", join(dir, "i.bin"), "--provider", "openai", "--embedding-route", "light-openai", "--limit-usd", "0.1"]);
    expect(noKey.code).toBe(2);
    expect(noKey.output).toContain("OPENAI_API_KEY");
    expect(existsSync(join(dir, "i.bin"))).toBe(false);
  });

  it("index 로 색인을 만들고 hybrid 로 평가하고 recall 로 방식별 재현율을 다시 잰다", async () => {
    const dir = mkdtempSync(join(tmpdir(), "scope3-cli-"));
    const indexPath = join(dir, "index.bin");
    const log = join(dir, "calls.jsonl");
    const built = await run(["index", "--index", indexPath, "--provider", "mock", "--log", log]);
    expect(built.code).toBe(0);
    expect(built.output).toContain("계수 2964건");
    expect(readFileSync(log, "utf8").trim().split("\n").every((line) => (JSON.parse(line) as { promptId: string }).promptId === "embedding")).toBe(true);

    const evaluated = await run(["eval", EXAMPLES, "--provider", "mock", "--json", "--shortlist-method", "hybrid", "--index", indexPath, "--out", dir]);
    expect(evaluated.code).toBe(0);
    const { report } = JSON.parse(evaluated.output) as { report: EvalReport };
    expect(report.shortlist.method).toBe("hybrid");
    expect(report.shortlist.embeddingCostUsd).toBeGreaterThan(0);
    const rows = JSON.parse(readFileSync(join(dir, "rows.json"), "utf8")) as { shortlists: { method: string; evidence: { embedding?: { rank: number } }[] }[] }[];
    const traced = rows.flatMap((entry) => entry.shortlists);
    expect(traced.length).toBeGreaterThan(0);
    expect(traced.every((entry) => entry.method === "hybrid" && entry.evidence.some((item) => item.embedding))).toBe(true);

    const recall = await run(["recall", EXAMPLES, "--from", join(dir, "rows.json"), "--index", indexPath, "--provider", "mock", "--sizes", "5,25", "--json"]);
    expect(recall.code).toBe(0);
    const summary = JSON.parse(recall.output) as { targets: number; results: { label: string; bySize: { size: number; hits: number; evaluated: number; missed: unknown[] }[] }[] };
    expect(summary.results.map((result) => result.label)).toEqual(["lexical-v1", "lexical-v2", "lexical-v2 (한글 두 글자 조각 없음)", "embedding (질의 item)", "embedding (질의 item+keywords)", "hybrid (질의 item)", "hybrid (질의 item+keywords)", "hybrid (질의 item, 한글 두 글자 조각 없음)", "hybrid (질의 item+keywords, 한글 두 글자 조각 없음)"]);
    for (const result of summary.results) {
      for (const entry of result.bySize) {
        expect(entry.evaluated).toBe(summary.targets);
        expect(entry.hits + entry.missed.length).toBe(entry.evaluated);
      }
      // 추린 수가 늘면 재현율은 줄지 않는다.
      expect(result.bySize[1]?.hits).toBeGreaterThanOrEqual(result.bySize[0]?.hits as number);
    }
  });

  it("계수 표가 바뀐 뒤의 낡은 색인으로는 평가를 시작하지 않는다", async () => {
    const dir = mkdtempSync(join(tmpdir(), "scope3-cli-"));
    const indexPath = join(dir, "index.bin");
    const factorsA = writeSyntheticFactors();
    expect((await run(["index", "--index", indexPath, "--provider", "mock", "--factors", factorsA])).code).toBe(0);
    const factorsB = join(dir, "factors.synthetic.jsonl");
    writeFileSync(factorsB, `${readFileSync(factorsA, "utf8")}\n`);
    const stale = await run(["eval", EXAMPLES, "--provider", "mock", "--limit-rows", "5", "--shortlist-method", "embedding", "--index", indexPath, "--factors", factorsB]);
    expect(stale.code).toBe(2);
    expect(stale.output).toContain("색인이 낡았다");
  });
});

describe("재현율 다시 재기", () => {
  it("기억에서 온 행, 정답 없는 행, 추천 호출까지 가지 못한 행은 분모에서 뺀다", async () => {
    const store = FactorStore.load(writeSyntheticFactors());
    const example = (rowId: string, itemName: string, factorIds: string[]): ExampleRow => ({ row: row({ rowId, itemName, unit: "식", quantity: null }), gold: { category: 1, factorIds, note: "synthetic" } }) as ExampleRow;
    const examples = [example("a", "합성 서버", ["syn:spend-computer"]), example("b", "합성 기억", ["syn:spend-legal"]), example("c", "합성 미상", []), example("d", "합성 실패", ["syn:spend-legal"])];
    const call = { basis: "spend" as const, total: 3 };
    const targets = recallTargets(
      examples,
      [
        { rowId: "a", source: "llm", predictedCategory: 1, keywords: ["computer"], shortlists: [call] },
        { rowId: "b", source: "memory", predictedCategory: 1, keywords: null, shortlists: [] },
        { rowId: "c", source: "llm", predictedCategory: 1, keywords: [], shortlists: [call] },
        { rowId: "d", source: "llm", predictedCategory: null, keywords: null, shortlists: [] },
      ],
      store,
    );
    expect(targets.map((target) => target.rowId)).toEqual(["a"]);
    const outcome = await measureRecall("v2", new LexicalV2Shortlister(), targets, [1, 3]);
    expect(outcome).toMatchObject({ ok: true, result: { bySize: [{ size: 1, hits: 1, evaluated: 1, missed: [] }, { size: 3, hits: 1, evaluated: 1 }], rows: [{ rowId: "a", bestRank: 1 }] } });
    // 후보 수가 앞선 실행과 다르면 표나 설정이 달라진 것이다.
    expect(() => recallTargets(examples, [{ rowId: "a", source: "llm", predictedCategory: 1, keywords: [], shortlists: [{ basis: "spend", total: 99 }] }, ...["b", "c", "d"].map((rowId) => ({ rowId, source: "memory" as const, predictedCategory: 1, keywords: null, shortlists: [] }))], store)).toThrow("후보 수가 앞선 실행과 다르다");
  });
});
