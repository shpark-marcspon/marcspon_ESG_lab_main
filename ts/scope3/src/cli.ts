import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import {
  AnthropicProvider,
  BudgetGuard,
  GeminiProvider,
  EmbeddingClient,
  FilePromptStore,
  JsonlFileSink,
  LiveSetupError,
  LlmClient,
  MemorySink,
  MockEmbeddingProvider,
  MockProvider,
  OpenAIProvider,
  defaultConfigPaths,
  liveEmbeddingProviders,
  liveProviders,
  loadConfig,
  parseRequestsPerMinute,
  requireLimitUsd,
  roundUsd,
  type CallLogSink,
  type EmbeddingProvider,
  type LiveDeps,
  type LlmConfig,
  type Provider,
  type Tags,
} from "llm";
import { EmbeddingIndexError, buildEmbeddingIndex, loadEmbeddingIndex, type EmbeddingIndex } from "./adapters/embeddingIndex.js";
import { EmbeddingShortlister } from "./adapters/embeddingShortlister.js";
import { FileMappingMemory } from "./adapters/fileMappingMemory.js";
import { loadFactorStore, type FactorStore } from "./adapters/jsonlFactorCatalog.js";
import type { CurrencyAdjustment } from "./domain/calc.js";
import type { LicenseStatus } from "./domain/factor.js";
import { InMemoryMappingMemory } from "./domain/memory.js";
import { DEFAULT_SHORTLIST_SIZE, runPipeline, type PipelineResult } from "./domain/pipeline.js";
import { DEFAULT_SHORTLIST_METHOD, SHORTLIST_METHODS, type MappingMemory, type ShortlistMethod, type Shortlister } from "./domain/ports.js";
import { EMBEDDING_QUERY_MODES, embeddingQueryText, type EmbeddingQueryMode } from "./domain/ranking.js";
import { LexicalV1Shortlister, LexicalV2Shortlister, lexicalShortlister } from "./domain/shortlister.js";
import { analyzeCoverage, evaluate, planDryRun, predictedCategory, type CoverageReport, type DryRunPlan, type EvalReport, type MatchCriterion } from "./eval/evaluate.js";
import { loadAdjustments, loadExampleSet, type ExampleRow } from "./eval/examples.js";
import { HeuristicMockProvider } from "./eval/mockHeuristic.js";
import { measureRecall, recallTargets, type MethodRecall, type PriorRow } from "./eval/recall.js";

const USAGE = `사용법:
  tsx src/cli.ts eval <예제 폴더> (--provider mock | --provider <openai|anthropic|gemini> --route <경로> --limit-usd <상한> | --dry-run)
      [--route <경로>] [--exclude-license disputed,unverified] [--criterion factor_id|sector]
      [--shortlist <후보 수>] [--adjustments <보정 계수 파일>] [--factors <factors.jsonl>] [--json]
      [--limit-rows <행 수>] [--log <jsonl 경로>] [--store-bodies] [--out <디렉터리>] [--rpm <분당 요청 수>]
      [--shortlist-method lexical-v1|lexical-v2|embedding|hybrid] [--no-bigrams]
      [--index <색인 파일>] [--embedding-route <임베딩 경로>] [--embedding-query item|item+keywords]
  tsx src/cli.ts index --index <색인 파일> (--provider mock | --provider openai --embedding-route <임베딩 경로> --limit-usd <상한>)
      [--factors <factors.jsonl>] [--log <jsonl 경로>]
  tsx src/cli.ts recall <예제 폴더> --from <앞선 실행의 rows.json> [--sizes 10,25,50] [--out <디렉터리>]
      [--index <색인 파일> (--provider mock | --provider openai --embedding-route <임베딩 경로> --limit-usd <상한>)] [--log <jsonl 경로>]

--provider mock 은 규칙 기반 모의 응답으로 전체 흐름을 돌려 지표를 낸다. 그 정확도는 성능과 무관하다.
--provider openai|anthropic|gemini 는 실제 공급사를 부른다. 돈이 든다. --route 와 --limit-usd 가 필요하고 키는 그 공급사의 환경 변수
  (OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY)로 받는다. 임베딩(index, recall --index, embedding·hybrid)은 openai 만 된다.
--dry-run 은 공급사를 부르지 않고 예상 호출 수와 비용 상한을 낸다 (기본 경로 light, 추리기는 --shortlist-method 의 낱말 검색 방식으로 어림한다).
--limit-rows 는 예제의 앞 n 행만 돌린다 (배선 확인용). --out 은 행별 판단(rows.json)과 지표(report.json)를 남긴다.
--store-bodies 는 호출 기록에 요청 본문과 응답 글자를 그대로 남긴다 (기본은 해시만).
--rpm 은 실제 공급사의 생성 호출 시작 간격을 벌려 분당 그 수를 넘지 않게 한다 (분당 한도가 있는 키용).
--shortlist-method 는 후보를 추리는 방식이다. 기본 ${DEFAULT_SHORTLIST_METHOD} 와 lexical-v1 은 키 없이 돈다. embedding·hybrid 는 --index 가 필요하고 질의를 임베딩하므로
  실제 공급사에서는 --embedding-route 가 필요하다 (비용은 --limit-usd 한 상한을 생성 호출과 같이 탄다). 임베딩이 실패하면 낱말 검색으로 넘어가지 않는다.
index 는 후보 계수 전부를 임베딩해 색인 파일을 만든다. 계수 표가 바뀌면 낡은 색인은 거부되므로 다시 만든다.
recall 은 생성 모델을 부르지 않고, 앞선 실행이 남긴 분류 결과(카테고리, 영어 검색어)로 방식별 후보 추리기 재현율만 다시 잰다.
  --index 를 주면 임베딩·합친 방식도 잰다 (질의 임베딩 비용이 든다).`;

const LICENSE_STATUSES: readonly LicenseStatus[] = ["ok", "disputed", "unverified"];
const here = dirname(fileURLToPath(import.meta.url));

export function promptRoot(): string {
  return join(here, "..", "prompts");
}

/**
 * 예제 폴더 경로를 푼다. 현재 디렉터리 기준으로 없으면 워크스페이스 루트(`ts/`) 기준으로 본다.
 * `cd ts && pnpm -F scope3 exec ...` 는 `ts/scope3` 에서 실행되므로 `ts/` 기준으로 적은 상대 경로가 어긋나기 때문이다.
 */
export function resolveFromWorkspace(path: string): string {
  const fromCwd = resolve(path);
  if (isAbsolute(path) || existsSync(fromCwd)) return fromCwd;
  const fromWorkspace = resolve(here, "..", "..", path);
  return existsSync(fromWorkspace) ? fromWorkspace : fromCwd;
}

function percent(value: number | null): string {
  return value === null ? "해당 행 없음" : `${(value * 100).toFixed(1)}%`;
}

function coverageLines(coverage: CoverageReport, unfiltered: CoverageReport): string[] {
  const { rows, rowsCategory12: r12 } = coverage;
  return [
    `후보 (라이선스 제외: ${coverage.excludeLicenseStatuses.join(",") || "없음"})`,
    `  카테고리 1 활동 기반 ${coverage.activityCandidates.category1}건 (제외 없이 ${unfiltered.activityCandidates.category1}건), 카테고리 2 활동 기반 ${coverage.activityCandidates.category2}건 (제외 없이 ${unfiltered.activityCandidates.category2}건)`,
    `  정답 카테고리가 AI 적용 범위인 ${rows.evaluated}행: 단위가 맞는 활동 기반 후보 있음 ${rows.physicalPossible}, 지출 기반으로만 가능 ${rows.spendOnly} (${percent(rows.evaluated ? rows.spendOnly / rows.evaluated : null)}), 후보 없음 ${rows.noCandidates}`,
    `  그중 카테고리 1·2 인 ${r12.evaluated}행: 활동 기반 가능 ${r12.physicalPossible}, 지출 기반으로만 가능 ${r12.spendOnly} (${percent(r12.evaluated ? r12.spendOnly / r12.evaluated : null)})`,
    `  넘어가는 이유: ${JSON.stringify(rows.byReason)}`,
  ];
}

function evalLines(report: EvalReport, provider: string): string[] {
  const line = (label: string, value: { hits: number; evaluated: number; rate: number | null }) => `  ${label}: ${value.hits}/${value.evaluated} (${percent(value.rate)})`;
  const recall = report.shortlistRecall;
  return [
    provider === "mock" ? `평가 (공급사 ${provider}) — 모의 공급사의 정확도는 성능과 무관하다. 도구가 도는지만 본다.` : `평가 (공급사 ${provider}) — 실제 호출 결과다.`,
    `행 ${report.counts.rows}, 묶음 ${report.counts.groups}, 정답 계수 있는 행 ${report.counts.withGoldFactor}, 정답을 달 수 없는 행 ${report.counts.unanswerable}`,
    `기억 적중: 행 ${report.memory.hitRows}/${report.counts.rows} (${percent(report.memory.rowRatio)}), 묶음 ${report.memory.hitGroups}/${report.counts.groups} (${percent(report.memory.groupRatio)})`,
    "분류 정확도",
    line("전체", report.classification),
    line("기억 적중 제외", report.classification.llmOnly),
    `top-3 적중률 (대표 기준: ${report.primaryCriterion})`,
    line("계수 id 일치", report.top3.factor_id),
    line(`같은 분류 부문 (NAICS ${report.sector.naicsDigits}자리, 분류 계층 ${report.sector.pathDepth}단계)`, report.top3.sector),
    line("계수 id 일치, 기억 적중 제외", report.top3LlmOnly.factor_id),
    line("같은 분류 부문, 기억 적중 제외", report.top3LlmOnly.sector),
    "top-1 적중률",
    line("계수 id 일치", report.top1.factor_id),
    line("같은 분류 부문", report.top1.sector),
    `정답을 달 수 없는 행 ${report.unanswerable.rows}: 추천 안 냄 ${report.unanswerable.withoutRecommendation}, 추천 냄 ${report.unanswerable.withRecommendation}`,
    `쓴 계수 기반: 활동 ${report.basis.activity}, 거리 ${report.basis.distance}, 지출 ${report.basis.spend} (지출로 넘어간 행 ${report.basis.fellBack}, 이유 ${JSON.stringify(report.basis.byReason)})`,
    "후보 추리기 재현율 (정답 계수가 추린 목록 안에 든 행 / 추천 호출까지 간 행, 기억 적중 제외)",
    line("전체", recall),
    line("분류가 맞은 행만", recall.correctCategory),
    `  추천 호출까지 가지 못한 행: ${recall.notReached}`,
    `  추린 방식: ${report.shortlist.description}${report.shortlist.embeddingCostUsd > 0 ? `, 임베딩 비용 ${report.shortlist.embeddingCostUsd} USD (아래 LLM 비용과 따로다)` : ""}`,
    `LLM·임베딩 단계 실패 (묶음 수): ${JSON.stringify(report.llmFailures)}`,
    `판단 상태: ${JSON.stringify(report.decisionStatus)}`,
    `계산 상태: ${JSON.stringify(report.calcStatus)}`,
    `LLM 호출 ${report.llm.calls}회 (공급사 시도 ${report.llm.attempts}회), 비용 ${report.llm.costUsd} USD${provider === "mock" ? " (모의 단가, synthetic)" : ""}`,
    `  프롬프트별: ${JSON.stringify(report.llm.byPrompt)}`,
  ];
}

function planLines(plan: DryRunPlan): string[] {
  return [
    `dry-run — 공급사를 부르지 않았다. 경로 ${plan.route}, 주 모델 ${plan.model} (${plan.provider}), 추천 호출당 후보 ${plan.shortlistSize}건 (추리기 ${plan.shortlistMethod})`,
    `묶음 ${plan.groups.total}: 기억 적중 ${plan.groups.memoryHits}, LLM 필요 ${plan.groups.needLlm} (그중 카테고리를 가정할 수 없어 분류만 센 묶음 ${plan.groups.withoutAssumedCategory})`,
    `예상 호출 수: 최소 ${plan.minimum.calls}회, 최대 ${plan.maximum.calls}회`,
    `입력 토큰 어림: 최소 ${plan.minimum.inputTokens}, 최대 ${plan.maximum.inputTokens}`,
    `비용 상한 (USD): 최소 경로 ${plan.minimum.costUpperBoundUsd}, 최대 경로 ${plan.maximum.costUpperBoundUsd}`,
    `추린 추천 프롬프트의 입력 토큰 어림: ${plan.shortlistedPromptTokens ? `${plan.shortlistedPromptTokens.min}~${plan.shortlistedPromptTokens.max}` : "추천 호출 없음"}`,
    "한 카테고리의 후보 전체를 넣었을 때:",
    ...plan.fullCandidateLists.map(
      (entry) => `  카테고리 ${entry.category} ${entry.basis === "physical" ? "활동·거리" : "지출"}: 후보 ${entry.candidates}건, 입력 토큰 어림 ${entry.inputTokens} / 문맥 한도 ${entry.contextWindowTokens} → ${entry.fits ? "들어감" : "넘침"}`,
    ),
    ...plan.warnings.map((warning) => `경고: ${warning}`),
    ...plan.assumptions.map((assumption) => `가정: ${assumption}`),
  ];
}

/** 행별 판단을 사람이 읽고 다시 집계할 수 있는 꼴로 옮긴다. */
function rowDump(examples: readonly ExampleRow[], result: PipelineResult) {
  const groupOf = new Map(result.groups.flatMap((group) => group.rowIds.map((rowId) => [rowId, group] as const)));
  const byRow = new Map(result.rows.map((entry) => [entry.row.rowId, entry]));
  return examples.map((example) => {
    const entry = byRow.get(example.row.rowId);
    const group = groupOf.get(example.row.rowId);
    if (!entry || !group) throw new Error(`결과에 행이 없다: ${example.row.rowId}`);
    const decision = entry.decision;
    return {
      rowId: example.row.rowId,
      itemName: example.row.itemName,
      unit: example.row.unit,
      gold: example.gold,
      status: decision.status,
      source: decision.source,
      predictedCategory: predictedCategory(decision),
      keywords: "classification" in decision && decision.classification ? decision.classification.keywords : null,
      picks: decision.status === "recommended" ? decision.picks.map((pick) => pick.factorId) : [],
      basis: decision.status === "recommended" ? decision.basis : null,
      failure: "llm" in decision ? decision.llm : null,
      shortlists: group.shortlists,
      calcStatus: entry.calc.status,
    };
  });
}

const TAGS: Tags = { tenant: "example-synthetic", module: "scope3" };

interface EmbeddingSetup {
  client: EmbeddingClient;
  route: string;
}

/**
 * 임베딩 호출을 조립한다. 모의 공급사는 가상 임베딩 모델(경로 mock)을 쓰고, 실제 공급사는 임베딩 경로·상한·키가 있어야 한다.
 * 상한은 생성 호출과 같은 `BudgetGuard` 를 타도록 밖에서 만든 것을 받는다.
 */
function embeddingSetup(config: LlmConfig, provider: string, embeddingRoute: string | undefined, budget: BudgetGuard | undefined, log: CallLogSink, storeBodies: boolean, deps: LiveDeps): EmbeddingSetup {
  const live = provider !== "mock";
  const route = embeddingRoute ?? (live ? "" : "mock");
  let providers: Record<string, EmbeddingProvider>;
  if (live) {
    if (route === "") throw new LiveSetupError("실제 공급사의 임베딩 호출에는 --embedding-route <임베딩 경로> 가 필요하다");
    if (!budget) throw new LiveSetupError("실제 공급사 호출에는 --limit-usd <상한 USD> 가 필요하다");
    providers = liveEmbeddingProviders(config, { provider, route }, deps);
  } else {
    const key = config.embeddingRoutes[route];
    const model = key ? config.embeddingModels[key] : undefined;
    if (!model) throw new LiveSetupError(`설정에 없는 임베딩 경로다: ${route}`);
    if (model.provider !== "mock") throw new LiveSetupError(`임베딩 경로 ${route} 의 모델은 모의 공급사가 아니다 (${model.provider}). --provider mock 은 모의 경로에서만 돈다.`);
    providers = { mock: new MockEmbeddingProvider(model.dimensions) };
  }
  return { client: new EmbeddingClient({ config, providers, log, storeBodies, ...(budget ? { budget } : {}) }), route };
}

function openIndex(path: string, store: FactorStore, setup: EmbeddingSetup): EmbeddingIndex {
  const model = setup.client.model(setup.route);
  return loadEmbeddingIndex(resolve(path), { factorsSha256: store.sha256, modelKey: model.key, dimensions: model.dimensions });
}

function recallLines(results: readonly MethodRecall[]): string[] {
  const lines = ["후보 추리기 재현율 (생성 모델 호출 없음. 분모: 정답 계수가 있고 기억에서 오지 않았고 앞선 실행에서 추천 호출까지 간 행)"];
  for (const result of results) {
    lines.push(`${result.label} — ${result.description}`);
    for (const entry of result.bySize) lines.push(`  추린 수 ${entry.size}: ${entry.hits}/${entry.evaluated} (${percent(entry.rate)})`);
  }
  return lines;
}

// 키는 여기(CLI 조립 지점)에서만 환경에서 읽어 넘긴다.
export async function main(argv: string[], out: (line: string) => void = console.log, deps: LiveDeps = { env: process.env }): Promise<number> {
  const { values, positionals } = parseArgs({
    args: argv,
    allowPositionals: true,
    options: {
      provider: { type: "string" },
      "dry-run": { type: "boolean", default: false },
      route: { type: "string" },
      "exclude-license": { type: "string" },
      criterion: { type: "string", default: "factor_id" },
      shortlist: { type: "string" },
      adjustments: { type: "string" },
      factors: { type: "string" },
      json: { type: "boolean", default: false },
      "limit-usd": { type: "string" },
      "limit-rows": { type: "string" },
      rpm: { type: "string" },
      log: { type: "string" },
      "store-bodies": { type: "boolean", default: false },
      out: { type: "string" },
      "shortlist-method": { type: "string", default: DEFAULT_SHORTLIST_METHOD },
      "no-bigrams": { type: "boolean", default: false },
      index: { type: "string" },
      "embedding-route": { type: "string" },
      "embedding-query": { type: "string", default: "item+keywords" },
      from: { type: "string" },
      sizes: { type: "string", default: "10,25,50" },
    },
  });
  const method = values["shortlist-method"];
  if (!(SHORTLIST_METHODS as readonly string[]).includes(method)) {
    out(`--shortlist-method 는 ${SHORTLIST_METHODS.join(" | ")} 중 하나다: ${method}`);
    return 2;
  }
  const queryMode = values["embedding-query"];
  if (!(EMBEDDING_QUERY_MODES as readonly string[]).includes(queryMode)) {
    out(`--embedding-query 는 ${EMBEDDING_QUERY_MODES.join(" | ")} 중 하나다: ${queryMode}`);
    return 2;
  }
  const options: SubcommandOptions = { ...values, method: method as ShortlistMethod, queryMode: queryMode as EmbeddingQueryMode };
  if (positionals[0] === "index") return indexCommand(options, out, deps);
  if (positionals[0] === "recall") return recallCommand(positionals[1], options, out, deps);
  const dir = positionals[1];
  if (positionals[0] !== "eval" || !dir || (!values["dry-run"] && !values.provider)) {
    out(USAGE);
    return 2;
  }
  const live = !values["dry-run"] && values.provider !== "mock";
  if (values.criterion !== "factor_id" && values.criterion !== "sector") {
    out(`--criterion 은 factor_id 또는 sector 다: ${values.criterion}`);
    return 2;
  }
  const criterion: MatchCriterion = values.criterion;
  const exclude = (values["exclude-license"] ?? "").split(",").filter((status) => status !== "");
  const unknown = exclude.filter((status) => !(LICENSE_STATUSES as readonly string[]).includes(status));
  if (unknown.length > 0) {
    out(`--exclude-license 에 모르는 상태가 있다: ${unknown.join(",")}`);
    return 2;
  }
  const excludeLicenseStatuses = exclude as LicenseStatus[];
  const shortlistSize = values.shortlist === undefined ? DEFAULT_SHORTLIST_SIZE : Number(values.shortlist);
  if (!Number.isInteger(shortlistSize) || shortlistSize < 1) {
    out(`--shortlist 는 1 이상의 정수다: ${values.shortlist}`);
    return 2;
  }

  const limitRows = values["limit-rows"] === undefined ? undefined : Number(values["limit-rows"]);
  if (limitRows !== undefined && (!Number.isInteger(limitRows) || limitRows < 1)) {
    out(`--limit-rows 는 1 이상의 정수다: ${values["limit-rows"]}`);
    return 2;
  }

  const loaded = loadExampleSet(resolveFromWorkspace(dir));
  const examples = limitRows === undefined ? loaded : { ...loaded, rows: loaded.rows.slice(0, limitRows) };
  const store = values.factors ? loadFactorStore(resolveFromWorkspace(values.factors)) : loadFactorStore();
  const memory: MappingMemory = examples.memoryPath ? FileMappingMemory.open(examples.memoryPath) : new InMemoryMappingMemory();
  const adjustments: CurrencyAdjustment[] = values.adjustments ? loadAdjustments(resolveFromWorkspace(values.adjustments)) : [];
  const config = loadConfig(defaultConfigPaths({ synthetic: true }));
  const tags = TAGS;
  const route = values.route ?? (values["dry-run"] ? "light" : live ? "" : "mock");
  // 실제 공급사 어댑터는 SDK 클라이언트 없이 등록한다. 요청 조립(dry-run)만 할 수 있다.
  let providers: Record<string, Provider> = { mock: new HeuristicMockProvider(), "mock-b": new MockProvider("mock-b"), anthropic: new AnthropicProvider(), openai: new OpenAIProvider(), gemini: new GeminiProvider() };
  let budget: BudgetGuard | undefined;
  if (live) {
    // 실제 공급사. 경로와 상한을 명시하지 않거나 키가 없으면 부르지 않는다.
    try {
      if (route === "") throw new LiveSetupError("실제 공급사 호출에는 --route <경로> 가 필요하다");
      if (!config.routes[route]) throw new LiveSetupError(`설정에 없는 경로다: ${route}`);
      budget = new BudgetGuard([{ scope: { module: tags.module }, limitUsd: requireLimitUsd(values["limit-usd"]) }]);
      providers = liveProviders(config, { provider: values.provider as string, route, ...parseRequestsPerMinute(values.rpm) }, deps);
    } catch (error) {
      if (!(error instanceof LiveSetupError)) throw error;
      out(`실제 공급사 호출을 지원하지 않는다: ${error.message}`);
      return 2;
    }
  }
  const log: CallLogSink = values.log ? new JsonlFileSink(resolve(values.log)) : new MemorySink();
  const client = new LlmClient({
    config,
    providers,
    prompts: new FilePromptStore(promptRoot()),
    log,
    storeBodies: values["store-bodies"],
    ...(budget ? { budget } : {}),
    ...(deps.sleep ? { sleep: deps.sleep } : {}),
  });
  // 후보 추리기 방식. 임베딩을 쓰는 방식은 색인과 임베딩 호출이 있어야 하고, 없으면 낱말 검색으로 바꾸지 않고 거부한다.
  let shortlister: Shortlister;
  const lexicalOptions = { bigrams: !values["no-bigrams"] };
  if (options.method === "lexical-v1" || options.method === "lexical-v2") shortlister = lexicalShortlister(options.method, lexicalOptions);
  else if (values["dry-run"]) {
    out(`--dry-run 은 공급사를 부르지 않으므로 추리기를 낱말 검색(lexical-v1, lexical-v2)으로만 어림한다. --shortlist-method ${options.method} 와 같이 쓸 수 없다.`);
    return 2;
  } else {
    if (!values.index) {
      out(`--shortlist-method ${options.method} 에는 --index <색인 파일> 이 필요하다 (index 명령으로 만든다)`);
      return 2;
    }
    try {
      const setup = embeddingSetup(config, values.provider as string, values["embedding-route"], budget, log, values["store-bodies"], deps);
      const index = openIndex(values.index, store, setup);
      shortlister = new EmbeddingShortlister({ client: setup.client, route: setup.route, tags, index, queryMode: options.queryMode, ...(options.method === "hybrid" ? { fuseWith: lexicalOptions } : {}) });
    } catch (error) {
      if (!(error instanceof LiveSetupError) && !(error instanceof EmbeddingIndexError)) throw error;
      out(`임베딩 추리기를 조립할 수 없다: ${error.message}`);
      return 2;
    }
  }
  const lead = config.models[config.routes[route]?.[0] as string];
  if (!lead) {
    out(`설정에 없는 경로다: ${route}`);
    return 2;
  }
  if (!values["dry-run"] && !live && lead.provider !== "mock") {
    out(`경로 ${route} 의 주 모델은 모의 공급사가 아니다 (${lead.provider}). --provider mock 은 모의 경로에서만 돈다.`);
    return 2;
  }
  const coverage = analyzeCoverage(examples.rows, store, excludeLicenseStatuses);
  const unfiltered = analyzeCoverage(examples.rows, store, []);
  const header = [
    `예제 ${examples.dir}: ${examples.rows.length}행 (전부 synthetic), 기억 ${examples.memoryPath ? `${memory.size}건` : "없음"}, 계수 표 ${store.recordCount}건 중 후보 ${store.candidateCount}건`,
    ...coverageLines(coverage, unfiltered),
  ];

  if (values["dry-run"]) {
    const plan = await planDryRun(examples.rows, { client, config, store, memory, route, tags, excludeLicenseStatuses, shortlistSize, shortlister });
    if (values.json) out(JSON.stringify({ coverage, coverageWithoutLicenseFilter: unfiltered, plan }, null, 2));
    else for (const line of [...header, ...planLines(plan)]) out(line);
    return 0;
  }

  const result = await runPipeline(
    examples.rows.map((example) => example.row),
    { client, store, memory, route, tags, excludeLicenseStatuses, shortlistSize, adjustments, shortlister },
  );
  const report = evaluate(examples.rows, result, store, { primaryCriterion: criterion });
  const provider = values.provider as string;
  const summary = { provider, syntheticProvider: !live, route, model: lead.key, shortlistSize, rowsEvaluated: examples.rows.length, rowsInSet: loaded.rows.length, coverage, coverageWithoutLicenseFilter: unfiltered, report };
  if (values.out) {
    const outDir = resolve(values.out);
    mkdirSync(outDir, { recursive: true });
    writeFileSync(join(outDir, "report.json"), `${JSON.stringify(summary, null, 1)}\n`, "utf8");
    writeFileSync(join(outDir, "rows.json"), `${JSON.stringify(rowDump(examples.rows, result), null, 1)}\n`, "utf8");
  }
  if (values.json) out(JSON.stringify(summary, null, 2));
  else for (const line of [...header, ...evalLines(report, provider)]) out(line);
  return 0;
}

type SubcommandOptions = {
  provider?: string | undefined;
  factors?: string | undefined;
  index?: string | undefined;
  "embedding-route"?: string | undefined;
  "limit-usd"?: string | undefined;
  log?: string | undefined;
  "store-bodies": boolean;
  "exclude-license"?: string | undefined;
  from?: string | undefined;
  sizes: string;
  out?: string | undefined;
  json: boolean;
  method: ShortlistMethod;
  queryMode: EmbeddingQueryMode;
};

/** 임베딩 호출 조립의 공통 부분: 설정, 상한, 호출 기록. 조립할 수 없으면 사유를 적고 null. */
function openEmbedding(options: SubcommandOptions, out: (line: string) => void, deps: LiveDeps): EmbeddingSetup | null {
  const config = loadConfig(defaultConfigPaths({ synthetic: true }));
  const log: CallLogSink = options.log ? new JsonlFileSink(resolve(options.log)) : new MemorySink();
  try {
    const live = options.provider !== "mock";
    const budget = live ? new BudgetGuard([{ scope: { module: TAGS.module }, limitUsd: requireLimitUsd(options["limit-usd"]) }]) : undefined;
    return embeddingSetup(config, options.provider as string, options["embedding-route"], budget, log, options["store-bodies"], deps);
  } catch (error) {
    if (!(error instanceof LiveSetupError)) throw error;
    out(`임베딩 호출을 조립할 수 없다: ${error.message}`);
    return null;
  }
}

/** 후보 계수 전부를 임베딩해 색인 파일을 만든다. */
async function indexCommand(options: SubcommandOptions, out: (line: string) => void, deps: LiveDeps): Promise<number> {
  if (!options.index || !options.provider) {
    out(USAGE);
    return 2;
  }
  const setup = openEmbedding(options, out, deps);
  if (!setup) return 2;
  const store = options.factors ? loadFactorStore(resolveFromWorkspace(options.factors)) : loadFactorStore();
  const built = await buildEmbeddingIndex({ client: setup.client, route: setup.route, tags: TAGS, store, factors: store.all() });
  if (!built.ok) {
    const failure = built.failure;
    out(`색인을 만들지 못했다 (${failure.kind}): ${failure.kind === "budget_exceeded" ? `상한 ${failure.limitUsd} USD, 누적 ${failure.spentUsd} USD` : failure.message}. 쓴 비용 ${failure.meta.totalCostUsd} USD`);
    return 1;
  }
  const path = resolve(options.index);
  built.index.save(path);
  const header = built.index.header;
  out(`색인 ${path}: 계수 ${built.index.size}건, 모델 ${header.modelKey} (${header.dimensions}차원), 계수 표 sha256 ${header.factorsSha256.slice(0, 12)}…, 만든 때 ${header.createdAt}`);
  out(`파일 크기 ${statSync(path).size} 바이트, 입력 토큰 ${header.inputTokens}, 비용 ${header.costUsd} USD${options.provider === "mock" ? " (모의 단가, synthetic)" : ""}`);
  return 0;
}

/** 앞선 실행의 분류 결과로 방식별 후보 추리기 재현율을 다시 잰다. 생성 모델은 부르지 않는다. */
async function recallCommand(dir: string | undefined, options: SubcommandOptions, out: (line: string) => void, deps: LiveDeps): Promise<number> {
  if (!dir || !options.from) {
    out(USAGE);
    return 2;
  }
  const sizes = options.sizes.split(",").map(Number);
  if (sizes.length === 0 || sizes.some((size) => !Number.isInteger(size) || size < 1)) {
    out(`--sizes 는 1 이상의 정수를 쉼표로 이은 것이다: ${options.sizes}`);
    return 2;
  }
  const exclude = (options["exclude-license"] ?? "").split(",").filter((status) => status !== "") as LicenseStatus[];
  const examples = loadExampleSet(resolveFromWorkspace(dir));
  const store = options.factors ? loadFactorStore(resolveFromWorkspace(options.factors)) : loadFactorStore();
  const prior = JSON.parse(readFileSync(resolve(options.from), "utf8")) as PriorRow[];
  const targets = recallTargets(examples.rows, prior, store, exclude);

  const methods: { label: string; shortlister: Shortlister }[] = [
    { label: "lexical-v1", shortlister: new LexicalV1Shortlister() },
    { label: "lexical-v2", shortlister: new LexicalV2Shortlister({ bigrams: true }) },
    { label: "lexical-v2 (한글 두 글자 조각 없음)", shortlister: new LexicalV2Shortlister({ bigrams: false }) },
  ];
  let embeddingCostUsd = 0;
  if (options.index) {
    if (!options.provider) {
      out("--index 를 주면 질의를 임베딩해야 하므로 --provider 가 필요하다");
      return 2;
    }
    const setup = openEmbedding(options, out, deps);
    if (!setup) return 2;
    let index: EmbeddingIndex;
    try {
      index = openIndex(options.index, store, setup);
    } catch (error) {
      if (!(error instanceof EmbeddingIndexError)) throw error;
      out(`임베딩 추리기를 조립할 수 없다: ${error.message}`);
      return 2;
    }
    const base = { client: setup.client, route: setup.route, tags: TAGS, index };
    const embedders: { label: string; shortlister: EmbeddingShortlister }[] = [];
    for (const queryMode of EMBEDDING_QUERY_MODES) {
      embedders.push({ label: `embedding (질의 ${queryMode})`, shortlister: new EmbeddingShortlister({ ...base, queryMode }) });
    }
    for (const bigrams of [true, false]) {
      for (const queryMode of EMBEDDING_QUERY_MODES) {
        embedders.push({ label: `hybrid (질의 ${queryMode}${bigrams ? "" : ", 한글 두 글자 조각 없음"})`, shortlister: new EmbeddingShortlister({ ...base, queryMode, fuseWith: { bigrams } }) });
      }
    }
    // 질의는 방식마다 한 번에 임베딩한다. 실패하면 임베딩 방식의 수치를 내지 않고 끝낸다.
    const queries = targets.flatMap((target) => target.calls.map((call) => call.query));
    for (const queryMode of EMBEDDING_QUERY_MODES) {
      const texts = [...new Set(queries.map((query) => embeddingQueryText(query, queryMode)))];
      const result = await setup.client.embed({ route: setup.route, inputs: texts, tags: TAGS });
      embeddingCostUsd += result.meta.totalCostUsd;
      if (result.kind !== "success") {
        out(`질의 임베딩이 실패했다 (${result.kind}): ${result.kind === "budget_exceeded" ? `상한 ${result.limitUsd} USD, 누적 ${result.spentUsd} USD` : result.message}. 임베딩·합친 방식은 재지 못했다.`);
        return 1;
      }
      for (const embedder of embedders) embedder.shortlister.seed(texts, result.vectors);
    }
    methods.push(...embedders);
  }

  const results: MethodRecall[] = [];
  for (const entry of methods) {
    const outcome = await measureRecall(entry.label, entry.shortlister, targets, sizes);
    if (!outcome.ok) {
      out(`${outcome.label}: 행 ${outcome.rowId} 에서 추리기가 실패했다 (${outcome.failure.kind}): ${outcome.failure.detail}`);
      return 1;
    }
    results.push(outcome.result);
  }
  const summary = { from: resolve(options.from), factorsSha256: store.sha256, sizes, targets: targets.length, calls: targets.reduce((sum, target) => sum + target.calls.length, 0), embeddingProvider: options.index ? options.provider : null, queryEmbeddingCostUsd: roundUsd(embeddingCostUsd), results };
  if (options.out) {
    const outDir = resolve(options.out);
    mkdirSync(outDir, { recursive: true });
    writeFileSync(join(outDir, "recall.json"), `${JSON.stringify(summary, null, 1)}\n`, "utf8");
  }
  if (options.json) out(JSON.stringify(summary, null, 2));
  else {
    for (const line of recallLines(results)) out(line);
    if (options.index) out(`질의 임베딩 비용 ${summary.queryEmbeddingCostUsd} USD${options.provider === "mock" ? " (모의 단가, synthetic. 모의 임베딩의 재현율은 성능과 무관하다)" : ""}`);
  }
  return 0;
}

if (process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href) {
  main(process.argv.slice(2)).then(
    (code) => {
      process.exitCode = code;
    },
    (error: unknown) => {
      console.error(error instanceof Error ? `${error.name}: ${error.message}` : error);
      process.exitCode = 1;
    },
  );
}
