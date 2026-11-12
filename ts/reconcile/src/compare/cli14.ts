import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { z } from "zod";
import { BudgetGuard, EmbeddingClient, JsonlFileSink, LiveSetupError, MockEmbeddingProvider, computeEmbeddingCostUsd, defaultConfigPaths, liveEmbeddingProviders, loadConfig, requireLimitUsd, roundUsd, type EmbeddingProvider, type LiveDeps } from "llm";
import { ReconcileFileError, dataDir, documentSchema, readJsonFile, resolveInputPath, resolveOutputPath } from "../adapters/files.js";
import { StaleIndexError, UnitIndex, buildUnitIndex, inputsSha256, loadUnitIndex, sha256File, type IndexInput } from "../adapters/unitIndex.js";
import { seedBudgetFromLog } from "../adapters/unitStore.js";
import { extractDocument, type DocModel } from "../domain/facts.js";
import { listTargets, type AskDoc, type Target } from "./ask.js";
import { KS, chartUnits, deriveGold, fuseRrf, otherOf, pageUnits, queriesFor, rankEmbedding, rankLexical, recall, scoreQuery, scoreSpread, tableUnits, valuesLeft, HEADER_STANDING_SHARE, type JudgedPair, type NominateGold, type Query, type QueryScore, type Ranked, type Side, type Unit } from "./embed-nominate.js";
import { buildFocusRequest, nominate, planFocus, type Nomination } from "./focus.js";
import { conservativeTokens } from "./render.js";

// 밴드 EB1 (MARC-TICK-45) 의 명령: 정답 끌어내기(gold), 단위 글(units), 어림(dry-run), 색인(index), 지목과 채점(rank).
// gold 만 판정 기록을 읽는다. units·dry-run·index 는 정답을 읽지 않는다. rank 는 굳힌 정답 파일을 채점에만 쓴다.
// 생성 모델을 부르지 않는다. 공급사를 부르는 것은 index 뿐이고 임베딩 경로 하나만 탄다.

type Out = (line: string) => void;

const USAGE = `사용법 (cd ts && pnpm -F reconcile exec tsx src/compare/cli14.ts <명령> ...)
  gold    --out <디렉터리> [--check <정답 파일>]
      data/reconcile/exam 의 판정 기록에서 「단위 → 상대 단위」와 「상대 없음」을 규칙으로 끌어내 <out>/nominate-gold.json 에 쓴다 (호출 0). --check 를 주면 그 파일의 links·none 과 같은지 본다.
  units   --a <문서 A> --b <문서 B> --chart-labels <이름=라벨 파일,..> --out <디렉터리>
      단위(표·도표·쪽)의 가린 글을 <out>/units.json 에 쓴다 (호출 0). 글에 값 후보 숫자가 남으면 쓰지 않는다.
  dry-run --a .. --b .. --chart-labels .. --out <디렉터리> [--route <임베딩 경로>]
      임베딩 입력의 수·글자 수·토큰 어림·비용 어림을 낸다 (호출 0).
  index   --a .. --b .. --chart-labels .. --out <디렉터리> (--provider mock | --provider openai --route <임베딩 경로> --limit-usd <상한> --log <호출 기록>)
      단위 갈래 × 문서마다 색인 파일 하나를 <out>/index/ 에 만든다. 이미 있는 색인은 다시 만들지 않는다 (지금의 문서·모델·글과 다르면 멈춘다).
  rank    --a .. --b .. --chart-labels .. --gold <정답 파일> --index <색인 디렉터리> --out <디렉터리> [--route <임베딩 경로>]
      방법(낱말 검색 / 임베딩 / 하이브리드 / value / names)마다 지목을 내고 정답으로 채점한다 (호출 0). <out>/scores.json, <out>/rankings.json, <out>/report.md.
--out 은 반드시 준다. 상대 경로는 ts/ 기준이다. 실제 공급사 호출은 돈이 든다.`;

const TAGS = { tenant: "measure", module: "reconcile-embed-nominate" };
const TOP_STORED = 10;
const writeJson = (path: string, value: unknown): void => writeFileSync(path, `${JSON.stringify(value, null, 1)}\n`);

// ── 판정 기록 읽기 (gold 명령만) ─────────────────────────────────────────────

/** 넓힌 정답의 서술 쌍 가운데 보고서 쪽이 도표의 값인 것 (밴드 CH1 의 보고와 `kb-2025.focus-judgments.json` 의 설명: N13·N14·N16~N21). */
export const CHART_PROSE_PAIRS = ["N13", "N14", "N16", "N17", "N18", "N19", "N20", "N21"];
/** 상대가 없다고 센 도표 쪽 (밴드 CH1: 도표 대상이 든 아홉 쪽 가운데 같은 긴 숫자가 데이터북에 없는 다섯 쪽). 값으로 센 것이라 아래쪽 끝이다. */
export const CHART_PAGES_WITHOUT = [33, 36, 38, 46, 63].map((page) => ({ doc: "a" as const, page, basis: "밴드 CH1 (band-reports/CH1-charts-why.md 의 (가)·(3)): 이 쪽의 도표 값과 같은 긴 숫자(유효 숫자 4자리 이상)가 데이터북에 없다. 값으로 센 것이라 짧은 숫자의 상대는 보지 못한다 (46쪽의 「42% 감축」은 데이터북 8쪽에 있다고 CH1 의 검증 에이전트가 적었다)" }));

const verdictOf = (raw: string): JudgedPair["verdict"] => (raw === "same" || raw === "same_item" ? "same" : ["not_same", "different_item", "wrong_pair", "no_counterpart"].includes(raw) ? "different" : "other");
const pairShape = z.object({ a: z.string(), b: z.string(), verdict: z.string(), scope: z.string().optional() });

/** 판정 기록 파일들을 한 꼴로 읽는다. 파생 문서의 사본(`kb-2025.ct2.*`, `kb-2025.ct2-judgments.json`)은 ID 가 달라 읽지 않는다. */
export function loadJudgedPairs(dir: string): { pairs: JudgedPair[]; files: Record<string, { sha256: string; read: number; skippedScope: number }> } {
  const pairs: JudgedPair[] = [];
  const files: Record<string, { sha256: string; read: number; skippedScope: number }> = {};
  const read = <T>(name: string, schema: z.ZodType<T>): T => {
    const path = join(dir, name);
    const parsed = schema.safeParse(readJsonFile(path, "판정 기록"));
    if (!parsed.success) throw new ReconcileFileError(`${path}: 판정 기록의 꼴이 맞지 않는다`);
    files[name] = { sha256: sha256File(path), read: 0, skippedScope: 0 };
    return parsed.data;
  };
  const add = (name: string, pair: JudgedPair): void => {
    pairs.push(pair);
    (files[name] as { read: number }).read += 1;
  };
  const source = (name: string): string => name.replace(/^kb-2025\./, "").replace(/\.json$/, "");
  const cross = (name: string, key: "items" | "judgments" | "pairs"): void => {
    const data = read(name, z.object({ [key]: z.array(pairShape) }).passthrough()) as Record<string, z.infer<typeof pairShape>[]>;
    for (const item of data[key] ?? []) {
      if (item.scope !== undefined && item.scope !== "cross") {
        (files[name] as { skippedScope: number }).skippedScope += 1;
        continue;
      }
      add(name, { source: source(name), a: item.a, b: item.b, verdict: verdictOf(item.verdict) });
    }
  };
  for (const pair of read("kb-2025.pairs.json", z.object({ pairs: z.array(z.object({ a: z.string(), b: z.string() })) }).passthrough()).pairs) add("kb-2025.pairs.json", { source: "pairs", a: pair.a, b: pair.b, verdict: "same" });
  for (const pair of read("kb-2025.gold-v2.json", z.object({ prosePairs: z.array(z.object({ id: z.string(), a: z.string(), b: z.string() })) }).passthrough()).prosePairs) add("kb-2025.gold-v2.json", { source: "gold-v2", a: pair.a, b: pair.b, verdict: "same", chart: CHART_PROSE_PAIRS.includes(pair.id) });
  for (const aim of read("kb-2025.ct2-aims.json", z.object({ aim1: z.array(z.object({ name: z.string(), doc: z.literal("a"), id: z.string(), right: z.array(z.string()) })) }).passthrough()).aim1) for (const right of aim.right) add("kb-2025.ct2-aims.json", { source: "ct2-aims", a: aim.id, b: right, verdict: "same", chart: aim.name.startsWith("47쪽 도표") });
  cross("kb-2025.outside-gold.json", "judgments");
  cross("kb-2025.review-new-pairs.json", "judgments");
  cross("kb-2025.recover-judgments.json", "items");
  cross("kb-2025.prose-judgments.json", "items");
  cross("kb-2025.named-judgments.json", "items");
  cross("kb-2025.charts-judgments.json", "items");
  cross("kb-2025.focus-judgments.json", "judgments");
  cross("kb-2025.ask-judgments.json", "pairs");
  // 칸 검수의 「상대 없음」은 (칸, 상대 표)로 적혀 있다. 상대 표를 그 표의 한 자리로 옮겨 같은 꼴로 싣는다.
  for (const item of read("kb-2025.review-no-counterpart.json", z.object({ judgments: z.array(z.object({ cell: z.string(), other_table: z.string(), verdict: z.string() })) }).passthrough()).judgments) {
    const [mine, theirs] = [item.cell, `${item.other_table}:r0c0`];
    add("kb-2025.review-no-counterpart.json", { source: "review-no-counterpart", a: mine.startsWith("a:") ? mine : theirs, b: mine.startsWith("a:") ? theirs : mine, verdict: verdictOf(item.verdict) });
  }
  return { pairs, files };
}

const goldFileSchema = z.object({ links: z.array(z.unknown()), none: z.array(z.unknown()), unused: z.record(z.string(), z.number()) }).passthrough();

// ── 단위 ─────────────────────────────────────────────────────────────────────

function readDoc(path: string): DocModel {
  const parsed = documentSchema.safeParse(readJsonFile(path, "문서 JSON"));
  if (!parsed.success) throw new ReconcileFileError(`${path}: 문서 JSON 형식이 맞지 않는다`);
  return parsed.data as DocModel;
}

interface Loaded {
  docs: Record<Side, AskDoc & { path: string; sha256: string }>;
  labels: Record<string, Record<Side, Record<string, string>>>;
  units: Unit[];
}

function load(values: { a?: string | undefined; b?: string | undefined; "chart-labels"?: string | undefined }): Loaded {
  if (!values.a || !values.b || !values["chart-labels"]) throw new ReconcileFileError("--a, --b, --chart-labels 가 필요하다");
  const one = (path: string, side: Side) => {
    const resolved = resolveInputPath(path);
    const model = readDoc(resolved);
    return { model, extraction: extractDocument(model, side, { titles: "own", kinds: true }), path: resolved, sha256: sha256File(resolved) };
  };
  const docs = { a: one(values.a, "a"), b: one(values.b, "b") };
  const labels: Loaded["labels"] = {};
  for (const item of values["chart-labels"].split(",")) {
    const [name, path] = item.split("=");
    if (!name || !path || !/^[a-z0-9]+$/.test(name)) throw new ReconcileFileError(`--chart-labels 의 꼴이 맞지 않는다: ${item}`);
    const parsed = z.record(z.enum(["a", "b"]), z.record(z.string(), z.string())).safeParse(readJsonFile(resolveInputPath(path), "도표 라벨"));
    if (!parsed.success) throw new ReconcileFileError(`${path}: 도표 라벨 형식이 맞지 않는다`);
    labels[name] = { a: parsed.data.a ?? {}, b: parsed.data.b ?? {} };
  }
  const units: Unit[] = [];
  for (const side of ["a", "b"] as const) {
    units.push(...tableUnits(docs[side].extraction, side), ...pageUnits(docs[side].model, docs[side].extraction, side));
    for (const [name, both] of Object.entries(labels)) units.push(...chartUnits(docs[side].extraction, side, both[side], name));
  }
  const leaks = units.flatMap((unit) => valuesLeft(unit.text).map((raw) => `${unit.id}: ${raw}`));
  if (leaks.length > 0) throw new ReconcileFileError(`단위의 글에 가리지 못한 값 후보 숫자가 ${leaks.length}개 있다 (${leaks.slice(0, 20).join(" / ")}). 글을 쓰지 않는다`);
  return { docs, labels, units };
}

const kindsOf = (units: readonly Unit[]): string[] => [...new Set(units.map((unit) => unit.kind))].sort();
const inputsOf = (units: readonly Unit[], side: Side, kind: string): IndexInput[] => units.filter((unit) => unit.doc === side && unit.kind === kind).flatMap((unit) => unit.chunks.map((text, index) => ({ id: `${unit.id}#c${index}`, text })));
const indexFile = (dir: string, side: Side, kind: string): string => join(dir, `${side}.${kind}.index`);
/** 글이 빈 단위(제목도 머리글도 없는 표)는 임베딩도 낱말 검색도 할 수 없어 후보·질의에서 빠진다. */
const usable = (unit: Unit): boolean => unit.chunks.length > 0;

function unitStats(units: readonly Unit[]): Record<string, unknown>[] {
  const rows: Record<string, unknown>[] = [];
  for (const side of ["a", "b"] as const) {
    for (const kind of kindsOf(units)) {
      const mine = units.filter((unit) => unit.doc === side && unit.kind === kind);
      const withText = mine.filter(usable);
      const chars = withText.map((unit) => unit.text.length).sort((x, y) => x - y);
      rows.push({ doc: side, kind, units: mine.length, emptyText: mine.length - withText.length, inputs: withText.reduce((sum, unit) => sum + unit.chunks.length, 0), chunked: withText.filter((unit) => unit.chunks.length > 1).length, chars: chars.reduce((sum, value) => sum + value, 0), medianChars: chars[Math.floor(chars.length / 2)] ?? 0, maxChars: chars[chars.length - 1] ?? 0, ...(kind === "table" ? { headerStanding: mine.filter((unit) => unit.headerShare !== null && unit.headerShare >= HEADER_STANDING_SHARE).length, headerNotStanding: mine.filter((unit) => unit.headerShare !== null && unit.headerShare < HEADER_STANDING_SHARE).length, noNumberCells: mine.filter((unit) => unit.headerShare === null).length } : {}) });
    }
  }
  return rows;
}

// ── 채점 표 ──────────────────────────────────────────────────────────────────

const METHODS = ["lexical", "embedding", "hybrid"] as const;
type Method = (typeof METHODS)[number];
const frac = (n: number, d: number): string => `${n}/${d}`;
const num = (value: number, method: string): string => (method === "embedding" ? value.toFixed(3) : method === "hybrid" ? value.toFixed(4) : value.toFixed(1));

interface Combo {
  source: Side;
  queryKind: string;
  targetKind: string;
  candidates: number;
  sourceUnits: number;
  queries: Query[];
  /** 정답에 걸리지 않아 분모에서 뺀 질의 쪽 단위. */
  unjudged: number;
  scores: Record<Method, QueryScore[]>;
  top: Record<Method, Record<string, Ranked[]>>;
  /** 정답에 걸리지 않은 단위의 맨 위 점수 (견주는 용도. 상대가 있는지 없는지 모른다). */
  unjudgedTop: Record<Method, number[]>;
}

export async function main(argv: string[], out: Out = console.log, log: Out = console.error, deps: LiveDeps = { env: process.env }): Promise<number> {
  const [command, ...rest] = argv;
  let values;
  try {
    values = parseArgs({ args: rest, options: { a: { type: "string" }, b: { type: "string" }, "chart-labels": { type: "string" }, out: { type: "string" }, check: { type: "string" }, gold: { type: "string" }, index: { type: "string" }, provider: { type: "string" }, route: { type: "string" }, "limit-usd": { type: "string" }, log: { type: "string" } } }).values;
  } catch (error) {
    out(`${(error as Error).message}\n\n${USAGE}`);
    return 2;
  }
  try {
    if (!["gold", "units", "dry-run", "index", "rank"].includes(command ?? "") || !values.out) return out(USAGE), 2;
    const outDir = resolveOutputPath(values.out);

    if (command === "gold") {
      const { pairs, files } = loadJudgedPairs(join(dataDir, "exam"));
      const gold = deriveGold(pairs, CHART_PAGES_WITHOUT);
      mkdirSync(outDir, { recursive: true });
      writeJson(join(outDir, "nominate-gold.json"), { links: gold.links, none: gold.none, unused: gold.unused, records: files });
      if (values.check) {
        const stored = goldFileSchema.safeParse(readJsonFile(resolveInputPath(values.check), "정답"));
        if (!stored.success) throw new ReconcileFileError(`${values.check}: 정답 파일의 꼴이 맞지 않는다`);
        if (JSON.stringify({ links: stored.data.links, none: stored.data.none, unused: stored.data.unused }) !== JSON.stringify({ links: gold.links, none: gold.none, unused: gold.unused })) throw new ReconcileFileError(`${values.check}: 판정 기록에 규칙을 다시 적용한 것과 다르다`);
      }
      out(JSON.stringify({ records: pairs.length, links: gold.links.length, byKind: Object.fromEntries(["cells", "prose", "chart"].map((kind) => [kind, gold.links.filter((link) => link.kind === kind).length])), none: gold.none.length, unused: gold.unused, ...(values.check ? { check: "같다" } : {}) }, null, 1));
      return 0;
    }

    const loaded = load(values);
    const { units } = loaded;
    const stats = unitStats(units);

    if (command === "units") {
      mkdirSync(outDir, { recursive: true });
      writeJson(join(outDir, "units.json"), { note: "단위의 가린 글 (호출 0). 정답을 읽지 않았다. text 는 낱말 검색이 보는 글, chunks 는 임베딩 입력이다. 값 후보 숫자는 남기지 않는다 (연도·식별자 꼴은 남는다).", docs: { a: { path: loaded.docs.a.path, sha256: loaded.docs.a.sha256 }, b: { path: loaded.docs.b.path, sha256: loaded.docs.b.sha256 } }, stats, units });
      out(JSON.stringify(stats, null, 1));
      return 0;
    }

    const config = loadConfig(defaultConfigPaths({ synthetic: true }));

    if (command === "dry-run") {
      const route = values.route ?? "light-openai";
      const model = new EmbeddingClient({ config, providers: {}, log: { write: () => undefined } }).model(route);
      const rows = [];
      let total = { inputs: 0, chars: 0, tokensLow: 0, tokensHigh: 0 };
      for (const side of ["a", "b"] as const) {
        for (const kind of kindsOf(units)) {
          const inputs = inputsOf(units, side, kind);
          if (inputs.length === 0) continue;
          const chars = inputs.reduce((sum, input) => sum + input.text.length, 0);
          const row = { doc: side, kind, inputs: inputs.length, chars, maxInputChars: Math.max(...inputs.map((input) => input.text.length)), tokensLow: inputs.reduce((sum, input) => sum + conservativeTokens(input.text), 0), tokensHigh: chars * 2, inputsSha256: inputsSha256(inputs) };
          rows.push(row);
          total = { inputs: total.inputs + row.inputs, chars: total.chars + chars, tokensLow: total.tokensLow + row.tokensLow, tokensHigh: total.tokensHigh + row.tokensHigh };
        }
      }
      const report = { dryRun: true, route, model: model.key, pricePerMTok: model.pricing.input, note: "공급사를 부르지 않았다. tokensLow 는 글자 수 어림(한글 1자 1토큰, 그 밖 0.6), tokensHigh 는 글자당 2토큰(밴드 H 에서 한국어 글이 어림의 두 배로 나온 적이 있어 잡은 위쪽 끝)이다.", rows, total, costLowUsd: computeEmbeddingCostUsd(total.tokensLow, model), costHighUsd: computeEmbeddingCostUsd(total.tokensHigh, model) };
      mkdirSync(outDir, { recursive: true });
      writeJson(join(outDir, "dry-run.json"), report);
      out(`[embed-nominate] 모델 ${model.key} · 입력 ${total.inputs} · 글자 ${total.chars} · 토큰 어림 ${total.tokensLow}~${total.tokensHigh} · 비용 어림 ${report.costLowUsd}~${report.costHighUsd} USD`);
      return 0;
    }

    if (command === "index") {
      const live = values.provider !== undefined && values.provider !== "mock";
      if (values.provider === undefined) return out(USAGE), 2;
      const route = live ? values.route : "mock";
      let providers: Record<string, EmbeddingProvider>;
      let budget: BudgetGuard | undefined;
      const logPath = values.log ? resolveOutputPath(values.log) : join(outDir, "calls.jsonl");
      if (live) {
        if (!route || !config.embeddingRoutes[route]) throw new LiveSetupError("실제 공급사 호출에는 설정에 있는 --route <임베딩 경로> 가 필요하다");
        const limitUsd = requireLimitUsd(values["limit-usd"]);
        if (!values.log) throw new LiveSetupError("실제 공급사 호출에는 --log <밴드 공용 호출 기록> 이 필요하다 (앞선 실행이 쓴 비용을 상한에 같이 센다)");
        providers = liveEmbeddingProviders(config, { provider: values.provider as string, route }, deps);
        budget = new BudgetGuard([{ scope: { module: TAGS.module }, limitUsd }]);
        const seeded = seedBudgetFromLog(logPath, budget, TAGS);
        log(`[embed-nominate] 호출 기록 ${seeded.lines}줄, 이미 쓴 비용 ${roundUsd(seeded.spentUsd)} USD, 상한 ${limitUsd} USD`);
      } else {
        providers = { mock: new MockEmbeddingProvider(config.embeddingModels["mock-embedding-synthetic"]?.dimensions ?? 64) };
      }
      mkdirSync(join(outDir, "index"), { recursive: true });
      const client = new EmbeddingClient({ config, providers, log: new JsonlFileSink(logPath), storeBodies: true, ...(budget ? { budget } : {}) });
      const model = client.model(route as string);
      const built: Record<string, unknown>[] = [];
      for (const side of ["a", "b"] as const) {
        for (const kind of kindsOf(units)) {
          const inputs = inputsOf(units, side, kind);
          if (inputs.length === 0) continue;
          const path = indexFile(join(outDir, "index"), side, kind);
          const expected = { documentSha256: loaded.docs[side].sha256, modelKey: model.key, dimensions: model.dimensions, unitKind: kind, inputsSha256: inputsSha256(inputs) };
          if (existsSync(path)) {
            // 있는 색인은 다시 만들지 않는다. 지금의 문서·모델·글과 다르면 StaleIndexError 로 멈춘다 (덮어쓰지 않는다).
            const index = loadUnitIndex(path, expected);
            built.push({ doc: side, kind, inputs: inputs.length, reused: true, inputTokens: index.header.inputTokens, costUsd: index.header.costUsd });
            continue;
          }
          const outcome = await buildUnitIndex({ client, route: route as string, tags: TAGS, documentSha256: loaded.docs[side].sha256, unitKind: kind, inputs });
          if (!outcome.ok) {
            const detail = outcome.failure.kind === "budget_exceeded" ? `상한 ${outcome.failure.limitUsd} USD, 누적 ${outcome.failure.spentUsd} USD` : outcome.failure.message;
            writeJson(join(outDir, "index", "failed.json"), { doc: side, kind, failure: outcome.failure.kind, detail, spentUsd: outcome.spentUsd, built });
            out(`[embed-nominate] 색인 ${side}.${kind} 실패: ${outcome.failure.kind} (${detail}). 이 색인은 만들지 않았다. 다시 돌리지 않는다`);
            return 1;
          }
          outcome.index.save(path);
          built.push({ doc: side, kind, inputs: inputs.length, reused: false, requests: outcome.index.header.requests, inputTokens: outcome.index.header.inputTokens, costUsd: outcome.index.header.costUsd });
        }
      }
      const summary = { ...(live ? {} : { synthetic: "모의 임베딩으로 만든 색인이다. 검색 품질의 근거로 쓰지 않는다" }), model: model.key, built, inputTokens: built.reduce((sum, row) => sum + (row.inputTokens as number), 0), costUsd: roundUsd(built.reduce((sum, row) => sum + (row.costUsd as number), 0)) };
      writeJson(join(outDir, "index", "summary.json"), summary);
      out(JSON.stringify(summary, null, 1));
      return 0;
    }

    // ── rank ───────────────────────────────────────────────────────────────
    if (!values.gold || !values.index) return out(USAGE), 2;
    const goldPath = resolveInputPath(values.gold);
    const gold = readJsonFile(goldPath, "정답") as NominateGold;
    if (!goldFileSchema.safeParse(gold).success) throw new ReconcileFileError(`${goldPath}: 정답 파일의 꼴이 맞지 않는다`);
    const indexDir = resolveOutputPath(values.index);
    const routeGuess = values.route ?? (existsSync(join(indexDir, "summary.json")) && (readJsonFile(join(indexDir, "summary.json"), "색인 요약") as { synthetic?: string }).synthetic ? "mock" : "light-openai");
    const model = new EmbeddingClient({ config, providers: {}, log: { write: () => undefined } }).model(routeGuess);
    const synthetic = model.provider === "mock";
    const indexes = new Map<string, UnitIndex>();
    for (const side of ["a", "b"] as const) {
      for (const kind of kindsOf(units)) {
        const inputs = inputsOf(units, side, kind);
        if (inputs.length === 0) continue;
        indexes.set(`${side}|${kind}`, loadUnitIndex(indexFile(indexDir, side, kind), { documentSha256: loaded.docs[side].sha256, modelKey: model.key, dimensions: model.dimensions, unitKind: kind, inputsSha256: inputsSha256(inputs) }));
      }
    }
    const byId = new Map(units.map((unit) => [unit.id, unit]));
    const pageOf = (id: string): number => (byId.get(id) as Unit).page;
    const vectorsOf = (unit: Unit): Float32Array[] => unit.chunks.map((_, index) => indexes.get(`${unit.doc}|${unit.kind}`)?.vector(`${unit.id}#c${index}`)).filter((vector): vector is Float32Array => vector !== undefined);

    const combos: Combo[] = [];
    for (const source of ["a", "b"] as const) {
      for (const queryKind of kindsOf(units)) {
        const sourceUnits = units.filter((unit) => unit.doc === source && unit.kind === queryKind && usable(unit));
        if (sourceUnits.length === 0) continue;
        for (const targetKind of kindsOf(units)) {
          const targets = units.filter((unit) => unit.doc === otherOf(source) && unit.kind === targetKind && usable(unit));
          if (targets.length === 0) continue;
          const queries = queriesFor(gold, source, sourceUnits, targets);
          const queryOf = new Map(queries.map((query) => [query.unit, query]));
          const combo: Combo = { source, queryKind, targetKind, candidates: targets.length, sourceUnits: sourceUnits.length, queries, unjudged: sourceUnits.length - queries.length, scores: { lexical: [], embedding: [], hybrid: [] }, top: { lexical: {}, embedding: {}, hybrid: {} }, unjudgedTop: { lexical: [], embedding: [], hybrid: [] } };
          for (const unit of sourceUnits) {
            const lexical = rankLexical(unit.text, targets);
            const embedding = rankEmbedding(vectorsOf(unit), targets, (id) => vectorsOf(byId.get(id) as Unit));
            if (!Array.isArray(embedding)) throw new StaleIndexError(`색인에 단위 ${embedding.missing} 의 벡터가 없다`);
            const ranked: Record<Method, Ranked[]> = { lexical, embedding, hybrid: fuseRrf([lexical, embedding], targets.map((target) => target.id)) };
            const query = queryOf.get(unit.id);
            for (const method of METHODS) {
              if (!query) {
                const top = ranked[method][0]?.score;
                if (top !== undefined) combo.unjudgedTop[method].push(top);
                continue;
              }
              combo.scores[method].push(scoreQuery(query, ranked[method], pageOf));
              combo.top[method][unit.id] = ranked[method].slice(0, TOP_STORED);
            }
          }
          for (const method of METHODS) combo.unjudgedTop[method].sort((x, y) => x - y);
          combos.push(combo);
        }
      }
    }

    // value·names 는 있는 것 그대로 돌린다 (`focus.ts` 의 `nominate`). 도표 대상이 든 쪽마다 상대 문서의 쪽을 낸다 (순위가 없다).
    const codeLabels: Record<string, string> = {};
    const firstLabels = loaded.labels.code ?? Object.values(loaded.labels)[0] ?? { a: {}, b: {} };
    for (const side of ["a", "b"] as const) for (const [id, text] of Object.entries(firstLabels[side])) codeLabels[`${side}:${id}`] = text;
    const targetsAll: Target[] = [...listTargets(loaded.docs.a, "a", codeLabels), ...listTargets(loaded.docs.b, "b", codeLabels)].filter((target) => target.branch === "chart");
    const baseline = Object.fromEntries((["value", "names", "either"] as const).map((mode) => [mode, nominate(targetsAll, loaded.docs, mode)])) as Record<"value" | "names" | "either", Nomination[]>;
    const nominatedPages = (mode: "value" | "names" | "either", source: Side, page: number): number[] => baseline[mode].find((item) => item.source === source && item.page === page)?.otherPages ?? [];
    const baselineRows = combos.filter((combo) => combo.targetKind === "page" && (combo.queryKind.startsWith("chart-") || combo.queryKind === "page")).map((combo) => ({
      source: combo.source,
      queryKind: combo.queryKind,
      modes: Object.fromEntries((["value", "names", "either"] as const).map((mode) => {
        const rows = combo.queries.map((query) => ({ unit: query.unit, state: query.state, nominated: nominatedPages(mode, combo.source, pageOf(query.unit)), hit: nominatedPages(mode, combo.source, pageOf(query.unit)).some((page) => query.rightPages.includes(page)), all: query.rightPages.every((page) => nominatedPages(mode, combo.source, pageOf(query.unit)).includes(page)) }));
        const right = rows.filter((row) => row.state === "right");
        const none = rows.filter((row) => row.state === "none");
        return [mode, { right: right.length, hit: right.filter((row) => row.hit).length, all: right.filter((row) => row.all).length, none: none.length, noneNominated: none.filter((row) => row.nominated.length > 0).length, rows }];
      })),
    }));

    // 상위 k 쪽을 방법 F 에 넘기면 요청이 몇 개이고 입력이 얼마인가 (생성 호출은 하지 않는다. 요청 글을 조립해 글자 수로 어림한다).
    const chartPages = (source: Side): number[] => [...new Set(targetsAll.filter((target) => target.doc === source).map((target) => target.page))].sort((x, y) => x - y);
    const estimate = (nominations: Nomination[]): { requests: number; otherPages: number; inputTokens: number; maxInputTokens: number } => {
      const requests = planFocus(targetsAll, nominations).map((plan) => conservativeTokens(JSON.stringify(buildFocusRequest(plan, loaded.docs, codeLabels).vars)));
      return { requests: requests.length, otherPages: nominations.reduce((sum, item) => sum + item.otherPages.length, 0), inputTokens: requests.reduce((sum, value) => sum + value, 0), maxInputTokens: Math.max(0, ...requests) };
    };
    const fRows: Record<string, unknown>[] = [];
    for (const mode of ["value", "names", "either"] as const) fRows.push({ method: mode, k: null, queryKind: null, ...estimate(baseline[mode]) });
    for (const combo of combos.filter((item) => item.source === "a" && item.targetKind === "page" && (item.queryKind.startsWith("chart-") || item.queryKind === "page"))) {
      const sourceUnits = units.filter((unit) => unit.doc === "a" && unit.kind === combo.queryKind && usable(unit));
      const targets = units.filter((unit) => unit.doc === "b" && unit.kind === "page" && usable(unit));
      for (const method of METHODS) {
        for (const k of KS) {
          const nominations: Nomination[] = chartPages("a").map((page) => {
            const pages = new Set<number>();
            for (const unit of sourceUnits.filter((item) => item.page === page)) {
              const lexical = rankLexical(unit.text, targets);
              const embedding = rankEmbedding(vectorsOf(unit), targets, (id) => vectorsOf(byId.get(id) as Unit)) as Ranked[];
              const ranked = method === "lexical" ? lexical : method === "embedding" ? embedding : fuseRrf([lexical, embedding], targets.map((target) => target.id));
              for (const entry of ranked.slice(0, k)) pages.add(pageOf(entry.id));
            }
            return { source: "a" as const, page, otherPages: [...pages].sort((x, y) => x - y), signals: [] };
          }).filter((item) => item.otherPages.length > 0);
          fRows.push({ method, k, queryKind: combo.queryKind, ...estimate(nominations) });
        }
      }
    }

    // ── 표 ──
    const lines: string[] = [];
    if (synthetic) lines.push("**모의 임베딩으로 낸 표다. 실측이 아니다.**", "");
    lines.push("## 재현율@k (쪽으로 합쳐 셈. 분모 = 옳은 상대가 있는 질의)", "", "| 방향 | 질의 단위 → 후보 단위 | 후보 수 | 방법 | @1 | @3 | @5 | 전부@5 | 순위표에 없음 | 옳은 쪽의 순위 |", "|---|---|---:|---|---:|---:|---:|---:|---:|---|");
    for (const combo of combos) {
      for (const method of METHODS) {
        const r = recall(combo.scores[method]);
        if (r.queries === 0) continue;
        lines.push(`| ${combo.source}→${otherOf(combo.source)} | ${combo.queryKind} → ${combo.targetKind} | ${combo.candidates} | ${method} | ${frac(r.atK[1] ?? 0, r.queries)} | ${frac(r.atK[3] ?? 0, r.queries)} | ${frac(r.atK[5] ?? 0, r.queries)} | ${frac(r.allAtK[5] ?? 0, r.queries)} | ${r.unranked} | ${r.ranks.join(" ")} |`);
      }
    }
    lines.push("", "## 재현율@k (단위로 셈. 분모 = 옳은 상대 단위가 후보에 있는 질의)", "", "| 방향 | 질의 단위 → 후보 단위 | 방법 | @1 | @3 | @5 | 순위표에 없음 | 옳은 단위의 순위 |", "|---|---|---|---:|---:|---:|---:|---|");
    for (const combo of combos.filter((item) => item.targetKind !== "page")) {
      for (const method of METHODS) {
        const r = recall(combo.scores[method], "unit");
        if (r.queries === 0) continue;
        lines.push(`| ${combo.source}→${otherOf(combo.source)} | ${combo.queryKind} → ${combo.targetKind} | ${method} | ${frac(r.atK[1] ?? 0, r.queries)} | ${frac(r.atK[3] ?? 0, r.queries)} | ${frac(r.atK[5] ?? 0, r.queries)} | ${r.unranked} | ${r.ranks.join(" ")} |`);
      }
    }
    lines.push("", `## 머리글이 선 표와 서지 않은 표 (질의가 표일 때. 기준: 숫자 칸 가운데 행·열 머리글이 다 있는 칸이 ${HEADER_STANDING_SHARE * 100}% 이상)`, "", "| 방향 | 질의 → 후보 | 갈래 | 질의 수 | 방법 | @1 | @3 | @5 |", "|---|---|---|---:|---|---:|---:|---:|");
    const headerSplit: Record<string, unknown>[] = [];
    for (const combo of combos.filter((item) => item.queryKind === "table")) {
      const groupOf = (id: string): string => {
        const share = (byId.get(id) as Unit).headerShare;
        return share === null ? "숫자 칸 없음" : share >= HEADER_STANDING_SHARE ? "머리글 섬" : "머리글 안 섬";
      };
      for (const group of ["머리글 섬", "머리글 안 섬", "숫자 칸 없음"]) {
        for (const method of METHODS) {
          const r = recall(combo.scores[method].filter((score) => groupOf(score.unit) === group));
          headerSplit.push({ source: combo.source, targetKind: combo.targetKind, group, method, ...r });
          lines.push(`| ${combo.source}→${otherOf(combo.source)} | table → ${combo.targetKind} | ${group} | ${r.queries} | ${method} | ${frac(r.atK[1] ?? 0, r.queries)} | ${frac(r.atK[3] ?? 0, r.queries)} | ${frac(r.atK[5] ?? 0, r.queries)} |`);
        }
      }
    }
    lines.push("", "## 점수의 분포: 상대 없는 질의의 맨 위 점수 / 옳은 상대의 점수 (오름차순)", "", "| 방향 | 질의 → 후보 | 방법 | 상대 없음 (맨 위 점수) | 옳은 상대의 점수 | 옳은 상대가 있는 질의의 맨 위 점수 | 판정 없는 단위의 맨 위 점수 (수, 최소 / 중앙 / 최대) |", "|---|---|---|---|---|---|---|");
    for (const combo of combos) {
      for (const method of METHODS) {
        const spread = scoreSpread(combo.scores[method]);
        if (spread.none.length + spread.right.length === 0) continue;
        const u = combo.unjudgedTop[method];
        lines.push(`| ${combo.source}→${otherOf(combo.source)} | ${combo.queryKind} → ${combo.targetKind} | ${method} | ${spread.none.map((value) => num(value, method)).join(" ") || "(없음)"}${spread.noneUnranked > 0 ? ` (순위표 빔 ${spread.noneUnranked})` : ""} | ${spread.right.map((value) => num(value, method)).join(" ") || "(없음)"}${spread.rightUnranked > 0 ? ` (순위표에 없음 ${spread.rightUnranked})` : ""} | ${spread.rightTop.map((value) => num(value, method)).join(" ")} | ${u.length === 0 ? "(없음)" : `${u.length}, ${num(u[0] as number, method)} / ${num(u[Math.floor(u.length / 2)] as number, method)} / ${num(u[u.length - 1] as number, method)}`} |`);
      }
    }
    lines.push("", "## value · names (있는 것 그대로. 순위가 없고 쪽의 집합을 낸다)", "", "| 방향 | 질의 단위 | 방식 | 옳은 쪽을 하나라도 짚음 | 옳은 쪽을 전부 짚음 | 상대 없는 질의 가운데 지목이 나온 것 |", "|---|---|---|---:|---:|---:|");
    for (const row of baselineRows) for (const [mode, tally] of Object.entries(row.modes)) lines.push(`| ${row.source}→${otherOf(row.source)} | ${row.queryKind} | ${mode} | ${frac(tally.hit, tally.right)} | ${frac(tally.all, tally.right)} | ${frac(tally.noneNominated, tally.none)} |`);
    lines.push("", "## 하이브리드 상위 k 쪽 ∪ value·names 의 쪽", "", "| 방향 | 질의 단위 | k | 하이브리드만 | ∪ value | ∪ names | ∪ either |", "|---|---|---:|---:|---:|---:|---:|");
    const unionRows: Record<string, unknown>[] = [];
    for (const combo of combos.filter((item) => item.targetKind === "page" && (item.queryKind.startsWith("chart-") || item.queryKind === "page"))) {
      const right = combo.queries.filter((query) => query.state === "right");
      for (const k of KS) {
        const hit = (mode: "value" | "names" | "either" | null): number => right.filter((query) => {
          const score = combo.scores.hybrid.find((item) => item.unit === query.unit) as QueryScore;
          return (score.pageRank ?? Infinity) <= k || (mode !== null && nominatedPages(mode, combo.source, pageOf(query.unit)).some((page) => query.rightPages.includes(page)));
        }).length;
        const row = { source: combo.source, queryKind: combo.queryKind, k, queries: right.length, hybrid: hit(null), value: hit("value"), names: hit("names"), either: hit("either") };
        unionRows.push(row);
        lines.push(`| ${combo.source}→${otherOf(combo.source)} | ${combo.queryKind} | ${k} | ${frac(row.hybrid, right.length)} | ${frac(row.value, right.length)} | ${frac(row.names, right.length)} | ${frac(row.either, right.length)} |`);
      }
    }
    lines.push("", "## 방법 F 에 넘길 때의 요청 수와 입력 토큰 어림 (보고서 → 데이터북, 도표 대상이 든 쪽마다 요청 하나. 생성 호출은 하지 않았다)", "", "| 지목 | 질의 단위 | k | 요청 수 | 실은 상대 쪽의 합 | 입력 토큰 어림 합 | 요청 최대 |", "|---|---|---:|---:|---:|---:|---:|");
    for (const row of fRows) lines.push(`| ${row.method} | ${row.queryKind ?? "(쪽)"} | ${row.k ?? "-"} | ${row.requests} | ${row.otherPages} | ${row.inputTokens} | ${row.maxInputTokens} |`);
    const text = `${lines.join("\n")}\n`;
    mkdirSync(outDir, { recursive: true });
    writeFileSync(join(outDir, "report.md"), text);
    const meta = { ...(synthetic ? { synthetic: "모의 임베딩으로 낸 결과다. 실측의 표에 싣지 않는다" } : {}), model: model.key, gold: { path: goldPath, sha256: sha256File(goldPath) }, docs: { a: loaded.docs.a.sha256, b: loaded.docs.b.sha256 }, unitStats: stats };
    writeJson(join(outDir, "scores.json"), { ...meta, combos: combos.map((combo) => ({ source: combo.source, queryKind: combo.queryKind, targetKind: combo.targetKind, candidates: combo.candidates, sourceUnits: combo.sourceUnits, right: combo.queries.filter((query) => query.state === "right").length, none: combo.queries.filter((query) => query.state === "none").length, unjudged: combo.unjudged, methods: Object.fromEntries(METHODS.map((method) => [method, { recallPage: recall(combo.scores[method]), recallUnit: recall(combo.scores[method], "unit"), spread: scoreSpread(combo.scores[method]), unjudgedTop: combo.unjudgedTop[method] }])) })), headerSplit, baseline: baselineRows, union: unionRows, focusEstimate: fRows });
    writeJson(join(outDir, "rankings.json"), { ...meta, note: `질의마다 정답(queries)과 방법별 채점(scores), 순위표의 위 ${TOP_STORED}개(top). 정답에 걸리지 않은 단위는 싣지 않는다.`, combos: combos.map((combo) => ({ source: combo.source, queryKind: combo.queryKind, targetKind: combo.targetKind, queries: combo.queries, scores: combo.scores, top: combo.top })), nominations: baseline });
    out(text);
    return 0;
  } catch (error) {
    if (error instanceof ReconcileFileError || error instanceof LiveSetupError || error instanceof StaleIndexError) {
      out(`${error.name}: ${error.message}`);
      return 1;
    }
    throw error;
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).then(
    (code) => process.exit(code),
    (error: unknown) => {
      console.error(error);
      process.exit(1);
    },
  );
}
