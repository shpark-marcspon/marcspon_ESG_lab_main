import { mkdirSync, writeFileSync } from "node:fs";
import { basename, join } from "node:path";
import { parseArgs } from "node:util";
import {
  AnthropicProvider,
  BudgetGuard,
  GeminiProvider,
  HeuristicTokenEstimator,
  JsonlFileSink,
  LiveSetupError,
  LlmClient,
  MemorySink,
  MockProvider,
  OpenAIProvider,
  defaultConfigPaths,
  liveProviders,
  loadConfig,
  parseRequestsPerMinute,
  requireLimitUsd,
  resolveRoute,
  type CallLogSink,
  type LiveDeps,
  type LlmConfig,
  type Provider,
} from "llm";
import type { Chunk } from "./chunk.js";
import { loadDocument, type ParsedDocument } from "./document.js";
import { dryRunChunks, type DryRunReport } from "./dryrun.js";
import { defaultGlossaryPath, loadGlossary } from "./glossary.js";
import { defaultChecklistPath, greenwashRequest, loadChecklist, type GreenwashOutput } from "./greenwash.js";
import { syntheticReviewer, syntheticTranslator } from "./mock-synthetic.js";
import { defaultOutRoot, resolveInputPath } from "./paths.js";
import { planCell } from "./select.js";
import { planGreenwash, planTranslate, prompts, runGreenwash, runTranslate, translateRulesFrom } from "./pipeline.js";
import { loadStoredCalls, storedResponder } from "./reread.js";
import type { Progress } from "./runner.js";
import { loadSettings } from "./settings.js";
import { FileChunkStore, type ChunkRecord, type ChunkStore } from "./store.js";
import { translateRequest, type TranslateOutput } from "./translate.js";

const USAGE = `사용법:
  tsx src/cli.ts translate <문서 JSON> (--provider mock | --provider <openai|anthropic|gemini> --route <경로> --limit-usd <상한> | --dry-run) [옵션]
  tsx src/cli.ts greenwash <문서 JSON> (--provider mock | --provider <openai|anthropic|gemini> --route <경로> --limit-usd <상한> | --dry-run) [옵션]
  tsx src/cli.ts reread <문서 JSON> --calls <calls.jsonl> --route <경로> [옵션]

문서 JSON 은 py/docparse 가 낸 document.json 이다 (PDF 를 직접 읽지 않는다).

  --provider mock     모의 공급사로 끝까지 돌린다. 응답은 synthetic 이고 번역·판정이 아니다.
  --provider openai|anthropic|gemini
                      실제 공급사를 부른다. 돈이 든다. --route 와 --limit-usd 가 필요하고 키는 그 공급사의 환경 변수
                      (OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY)로 받는다.
  --dry-run           공급사를 부르지 않고 조각 수, 조각별 예상 토큰, 총 예상 비용을 낸다.
  --route <경로>      data/llm 설정의 경로 이름. --dry-run 에서는 여러 번 줄 수 있다 (기본: 설정 파일의 dryRunRoutes).
  --budget-tokens <n> 조각당 원문 토큰 상한 (기본: data/docpipe/settings.json).
  --concurrency <n>   동시 실행 수.
  --limit-usd <x>     이 실행의 누적 비용 상한. 닿으면 남은 조각은 budget_exceeded 로 실패한다.
  --glossary <파일>   용어집 (기본: data/docpipe/glossary.synthetic.json).
  --checklist <파일>  그린워싱 체크리스트 (기본: data/docpipe/greenwash-checklist.synthetic.json).
  --out <디렉터리>    상태와 결과를 둘 곳 (기본: out/docpipe/<문서>-<sha>/<작업>-<경로>).
  --fresh             앞선 실행의 끝난 조각을 건너뛰지 않고 전부 다시 돌린다.
  --no-rows           --dry-run 에서 조각별 표를 빼고 합계만 출력한다.
  --max-chunks <n>    앞에서부터 n 개 조각만 돌린다 (배선 확인용). 같은 --out 으로 다시 돌리면 이어서 한다.
  --rpm <n>           실제 공급사 호출의 시작 간격을 벌려 분당 n 건을 넘지 않게 한다 (분당 한도가 있는 키용).
  --store-bodies      호출 기록(calls.jsonl)에 요청 본문과 응답 글자를 그대로 남긴다 (기본은 해시만).
  --table-numbers <flag|strict>  표 셀의 번역문 숫자가 원문과 다를 때. flag = 의심 표시만 남긴다, strict = 조각 실패(옛 규칙). 기본: settings.json.
  --unit-cells <fixed|model>     단위만 있는 표 셀(억 원, 천 톤). fixed = 모델에 보내지 않고 고정 번역, model = 모델에 보낸다(옛 동작). 기본: settings.json.
  --units <파일>      단위 셀 고정 번역 표 (기본: data/docpipe/unit-cells.synthetic.json).

reread 는 공급사를 부르지 않는다. --store-bodies 로 남긴 번역 호출 기록(--calls)의 응답을 다시 읽어 지금의 규칙에 통과시킨다.
조각 구성이 그때와 같으면 그때의 응답을 시도 순서대로 돌려주고, 달라졌으면(단위 셀을 빼서) 항목별 저장 번역을 다시 묶는다.

번역 결과의 표 셀 숫자가 원문과 다르면(기본 규칙) 조각은 완료로 치고 요약의 tableCells.suspected 와 suspects.json 에 남긴다.

종료 코드: 0 = 모든 조각 완료, 3 = 실패한 조각이 남음(끝난 조각은 저장됨), 2 = 사용법 오류, 1 = 그 밖의 오류.`;

type Out = (line: string) => void;

interface ClientOptions {
  logPath?: string;
  limitUsd?: number;
  module: string;
  storeBodies?: boolean;
  /** 실제 공급사 어댑터 (SDK 클라이언트가 주입된 것). 없으면 모의 공급사와 요청 조립용 어댑터만 등록한다. */
  live?: Record<string, Provider>;
}

/** `providerCalls()` 는 이 실행에서 공급사를 실제로 부른 횟수다 (상한에 막혀 부르지 않은 것은 뺀다). */
function buildClient(config: LlmConfig, options: ClientOptions): { client: LlmClient; mock: MockProvider; providerCalls: () => number } {
  const mock = new MockProvider();
  const inner: CallLogSink = options.logPath ? new JsonlFileSink(options.logPath) : new MemorySink();
  let providerCalls = 0;
  const log: CallLogSink = {
    write(record) {
      if (record.resultKind !== "budget_exceeded") providerCalls += 1;
      inner.write(record);
    },
  };
  const client = new LlmClient({
    config,
    // 실제 공급사 어댑터는 주입받은 것이 없으면 SDK 클라이언트 없이 등록한다. 요청 조립(dry-run)만 할 수 있다.
    providers: options.live ?? { mock, "mock-b": new MockProvider("mock-b"), anthropic: new AnthropicProvider(), openai: new OpenAIProvider(), gemini: new GeminiProvider() },
    prompts,
    log,
    storeBodies: options.storeBodies ?? false,
    ...(options.limitUsd === undefined ? {} : { budget: new BudgetGuard([{ scope: { module: options.module }, limitUsd: options.limitUsd }]) }),
  });
  return { client, mock, providerCalls: () => providerCalls };
}

function progressPrinter(log: Out): (progress: Progress) => void {
  let lastBucket = -1;
  return (progress) => {
    const finished = progress.done + progress.failed + progress.skipped;
    const bucket = Math.floor((finished * 10) / Math.max(progress.total, 1));
    if (progress.outcome === "failed") log(`  실패: ${progress.chunkId}`);
    if (bucket === lastBucket && finished !== progress.total) return;
    lastBucket = bucket;
    log(`  진행 ${finished}/${progress.total} (완료 ${progress.done}, 건너뜀 ${progress.skipped}, 실패 ${progress.failed})`);
  };
}

function freshStore<O>(store: ChunkStore<O>): ChunkStore<O> {
  return { read: () => undefined, write: (record) => store.write(record) };
}

function writeJson(path: string, value: unknown): void {
  writeFileSync(path, `${JSON.stringify(value, null, 1)}\n`, "utf8");
}

function countBy<T>(values: T[], key: (value: T) => string): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const value of values) counts[key(value)] = (counts[key(value)] ?? 0) + 1;
  return counts;
}

function failures(records: ChunkRecord<unknown>[]): { chunkId: string; kind: string; message: string; attempts: number }[] {
  return records.flatMap((record) => (record.status === "failed" ? [{ chunkId: record.chunkId, kind: record.error.kind, message: record.error.message, attempts: record.attempts }] : []));
}

function printDryRun(out: Out, reports: DryRunReport[], rows: boolean, config: LlmConfig): void {
  out("");
  out(`토큰은 실제 값이 아니라 글자 수 어림이다 (data/llm/models.json tokenEstimate: 로마자 ${config.tokenEstimate.latinCharsPerToken}자 = 1토큰, 한글·한자 ${config.tokenEstimate.cjkCharsPerToken}자 = 1토큰).`);
  out(`  한글 어림의 근거: ${config.tokenEstimate.cjkBasis}`);
  for (const report of reports) {
    out("");
    out(`[경로 ${report.route}] 모델 ${report.model} (${report.provider}) · 단가 입력 ${report.pricingUsdPerMTok.input} / 출력 ${report.pricingUsdPerMTok.output} USD/MTok (data/llm 설정)`);
    out(`  조각 수: ${report.chunks} (예산 초과 단독 조각 ${report.overBudgetChunks})`);
    out(`  조각마다 되풀이되는 고정 앞부분(캐시 대상): 약 ${report.cacheablePrefixTokens}토큰`);
    out(`  예상 입력 토큰 합: ${report.totals.inputTokens} (그중 조각 원문 ${report.totals.sourceTokens})`);
    out(`  예상 출력 토큰 합: ${report.totals.expectedOutputTokens}`);
    out(`  총 예상 비용: ${report.totals.expectedCostUsd.toFixed(4)} USD (캐시·배치 할인 없음)`);
    out(`  비용 상한(조각마다 출력 상한을 다 쓸 때): ${report.totals.costUpperBoundUsd.toFixed(4)} USD`);
    for (const [message, count] of Object.entries(report.warnings)) out(`  경고 (${count}개 조각): ${message}`);
    if (!rows) continue;
    out("  조각별 예상 토큰:");
    out("  조각 id            쪽      단위  항목  원문토큰  입력토큰  예상출력  예상비용USD");
    for (const row of report.rows) {
      out(
        `  ${row.chunkId.padEnd(18)} ${row.pages.padEnd(7)} ${String(row.units).padStart(4)}  ${String(row.items).padStart(4)}  ${String(row.sourceTokens).padStart(8)}  ${String(row.inputTokens).padStart(8)}  ${String(row.expectedOutputTokens).padStart(8)}  ${row.expectedCostUsd.toFixed(6)}${row.overBudget ? "  예산초과" : ""}`,
      );
    }
  }
}

function chunkTokenStats(chunks: Chunk[]): { min: number; median: number; max: number } | null {
  if (chunks.length === 0) return null;
  const sorted = chunks.map((chunk) => chunk.sourceTokens).sort((a, b) => a - b);
  return { min: sorted[0] as number, median: sorted[Math.floor(sorted.length / 2)] as number, max: sorted[sorted.length - 1] as number };
}

function outDirFor(document: ParsedDocument, task: string, route: string, override: string | undefined): string {
  if (override) return resolveInputPath(override);
  const stem = basename(document.source.file_name).replace(/\.[^.]+$/, "");
  return join(defaultOutRoot, `${stem}-${document.source.sha256.slice(0, 8)}`, `${task}-${route}`);
}

// 키는 여기(CLI 조립 지점)에서만 환경에서 읽어 넘긴다.
export async function main(argv: string[], out: Out = console.log, log: Out = console.error, deps: LiveDeps = { env: process.env }): Promise<number> {
  const { values, positionals } = parseArgs({
    args: argv,
    allowPositionals: true,
    options: {
      provider: { type: "string" },
      "dry-run": { type: "boolean", default: false },
      route: { type: "string", multiple: true },
      "budget-tokens": { type: "string" },
      concurrency: { type: "string" },
      "limit-usd": { type: "string" },
      glossary: { type: "string" },
      checklist: { type: "string" },
      out: { type: "string" },
      fresh: { type: "boolean", default: false },
      "no-rows": { type: "boolean", default: false },
      "max-chunks": { type: "string" },
      "store-bodies": { type: "boolean", default: false },
      "table-numbers": { type: "string" },
      "unit-cells": { type: "string" },
      units: { type: "string" },
      calls: { type: "string" },
      rpm: { type: "string" },
    },
  });
  const [task, documentArg] = positionals;
  const reread = task === "reread";
  const usageError =
    (task !== "translate" && task !== "greenwash" && !reread) ||
    !documentArg ||
    (reread ? !values.calls || !values.route?.[0] || values["dry-run"] || values.provider !== undefined : !values["dry-run"] && !values.provider) ||
    (values["table-numbers"] !== undefined && values["table-numbers"] !== "flag" && values["table-numbers"] !== "strict") ||
    (values["unit-cells"] !== undefined && values["unit-cells"] !== "fixed" && values["unit-cells"] !== "model");
  if (usageError || !documentArg) {
    out(USAGE);
    return 2;
  }
  // reread 는 저장된 응답만 읽는다. 실제 공급사 조립 경로(live)에 들어가지 않는다.
  const live = !reread && !values["dry-run"] && values.provider !== "mock";
  const positiveInt = (name: "budget-tokens" | "concurrency" | "max-chunks"): number | undefined => {
    const raw = values[name];
    if (raw === undefined) return undefined;
    const parsed = Number(raw);
    if (!Number.isInteger(parsed) || parsed <= 0) throw new Error(`--${name} 는 양의 정수여야 한다: ${raw}`);
    return parsed;
  };
  const budgetTokens = positiveInt("budget-tokens");
  const concurrency = positiveInt("concurrency");
  const maxChunks = positiveInt("max-chunks");
  const limitUsd = values["limit-usd"] === undefined ? undefined : Number(values["limit-usd"]);
  if (limitUsd !== undefined && !(limitUsd > 0)) throw new Error(`--limit-usd 는 양수여야 한다: ${values["limit-usd"]}`);

  const settings = loadSettings();
  const config = loadConfig(defaultConfigPaths({ synthetic: true }));
  const estimator = new HeuristicTokenEstimator(config.tokenEstimate);
  const document = loadDocument(resolveInputPath(documentArg));
  const glossary = loadGlossary(values.glossary ? resolveInputPath(values.glossary) : defaultGlossaryPath);
  const checklist = loadChecklist(values.checklist ? resolveInputPath(values.checklist) : defaultChecklistPath);
  const tags = { tenant: "cli", module: "docpipe" };
  const planBase = { document, settings, estimator, ...(budgetTokens === undefined ? {} : { budgetTokens }) };
  const rules = translateRulesFrom(settings, {
    ...(values["table-numbers"] === undefined ? {} : { tableNumbers: values["table-numbers"] as "flag" | "strict" }),
    ...(values["unit-cells"] === undefined ? {} : { unitCells: values["unit-cells"] as "fixed" | "model" }),
    ...(values.units === undefined ? {} : { unitTablePath: resolveInputPath(values.units) }),
  });

  if (values["dry-run"]) {
    const routes = values.route ?? (values.provider === "mock" ? ["mock"] : settings.dryRunRoutes);
    const { client } = buildClient(config, { module: tags.module });
    const reports: DryRunReport[] = [];
    let header: Record<string, unknown> = {};
    for (const route of routes) {
      if (task === "translate") {
        const plan = planTranslate({ ...planBase, glossary, route, rules });
        reports.push(dryRunChunks(client, config, plan.chunks, (chunk) => translateRequest(chunk, { route, tags, document, glossary }), route, settings.translate.expectedOutputRatio));
        const cellPlans = document.tables.flatMap((table) => table.cells.flatMap((row) => row.map((cell) => planCell(cell, rules.units))));
        header = {
          작업: "translate",
          조각당_원문_토큰_상한: plan.budgetTokens,
          표_셀_규칙: {
            숫자가_달라진_셀: rules.tableNumbers,
            단위_셀: rules.units ? "fixed" : "model",
            고정_번역_표: rules.units ? { path: rules.units.path, synthetic: rules.units.synthetic, 항목: rules.units.phrases.size } : null,
            모델에_보내는_표_셀: cellPlans.filter((plan) => plan.kind === "send").length,
            고정_번역하는_단위_셀: cellPlans.filter((plan) => plan.kind === "fixed").length,
            고정_번역_없는_단위_셀: cellPlans.filter((plan) => plan.kind === "send" && plan.unitWithoutFixed).length,
          },
          블록_처리: countBy([...plan.units.dispositions.values()], (disposition) => `${disposition.kind}:${"reason" in disposition ? disposition.reason : "llm"}`),
          되풀이_장식으로_뺀_글자: plan.units.furnitureTexts,
          조각_원문_토큰_분포: chunkTokenStats(plan.chunks),
          용어집: { path: glossary.path, synthetic: glossary.synthetic, terms: glossary.terms.length },
          출력_비율_가정: settings.translate.expectedOutputRatioBasis,
        };
      } else {
        const plan = planGreenwash({ ...planBase, checklist, route });
        reports.push(dryRunChunks(client, config, plan.chunks, (chunk) => greenwashRequest(chunk, { route, tags, document, checklist }), route, settings.greenwash.expectedOutputRatio));
        header = {
          작업: "greenwash",
          조각당_원문_토큰_상한: plan.budgetTokens,
          걸러_내기: { 본_문단: plan.selection.paragraphsConsidered, 키워드로_고름: plan.selection.byKeyword, 섹션_제목만으로_고름: plan.selection.bySectionTitleOnly },
          조각_원문_토큰_분포: chunkTokenStats(plan.chunks),
          체크리스트: { path: checklist.path, synthetic: checklist.synthetic, decisionPending: checklist.decisionPending ?? null },
          출력_비율_가정: settings.greenwash.expectedOutputRatioBasis,
        };
      }
    }
    out(JSON.stringify({ dryRun: true, 문서: { file: document.source.file_name, 쪽: document.source.page_count, 섹션: document.sections.length, 블록: document.blocks.length, 표: document.tables.length }, ...header }, null, 1));
    printDryRun(out, reports, !values["no-rows"], config);
    return 0;
  }

  const route = values.route?.[0] ?? (live ? "" : "mock");
  if (reread && !config.routes[route]) {
    out(`설정에 없는 경로다: ${route}`);
    return 2;
  }
  // 다시 읽기: 그 경로의 공급사 이름 자리에 저장된 응답을 돌려주는 모의 공급사만 등록한다. 실제 어댑터는 등록하지 않는다.
  const stored = reread ? loadStoredCalls(resolveInputPath(values.calls as string)) : undefined;
  const replay = stored ? storedResponder(stored) : undefined;
  const replayMock = new MockProvider("stored-responses");
  let liveAdapters: Record<string, Provider> | undefined = reread ? Object.fromEntries(resolveRoute(config, route).map((model) => [model.provider, replayMock])) : undefined;
  if (live) {
    // 실제 공급사. 경로와 상한을 명시하지 않거나 키가 없으면 부르지 않는다.
    try {
      if (route === "") throw new LiveSetupError("실제 공급사 호출에는 --route <경로> 가 필요하다");
      if (!config.routes[route]) throw new LiveSetupError(`설정에 없는 경로다: ${route}`);
      requireLimitUsd(values["limit-usd"]);
      liveAdapters = liveProviders(config, { provider: values.provider as string, route, ...parseRequestsPerMinute(values.rpm) }, deps);
    } catch (error) {
      if (!(error instanceof LiveSetupError)) throw error;
      out(`실제 공급사 호출을 지원하지 않는다: ${error.message}`);
      return 2;
    }
  }
  const outDir = outDirFor(document, task as string, route, values.out);
  mkdirSync(outDir, { recursive: true });
  const { client, mock, providerCalls } = buildClient(config, {
    module: tags.module,
    // 다시 읽기는 호출이 없으므로 호출 기록 파일을 남기지 않는다.
    ...(reread ? {} : { logPath: join(outDir, "calls.jsonl") }),
    storeBodies: values["store-bodies"],
    ...(limitUsd === undefined ? {} : { limitUsd }),
    ...(liveAdapters ? { live: liveAdapters } : {}),
  });
  // 다시 읽기는 저장된 응답을 시도 순서대로 돌려줘야 하므로 한 번에 한 조각씩 돌린다.
  const runBase = { ...planBase, route, client, tags, onProgress: progressPrinter(log), ...(reread ? { concurrency: 1 } : concurrency === undefined ? {} : { concurrency }), ...(maxChunks === undefined ? {} : { maxChunks }) };
  const provenance = live
    ? { synthetic: false, costUsd: (records: ChunkRecord<unknown>[]) => ({ costUsd: Math.round(records.reduce((sum, record) => sum + record.costUsd, 0) * 1e6) / 1e6 }) }
    : { synthetic: true, costUsd: (records: ChunkRecord<unknown>[]) => ({ costUsdSyntheticPricing: Math.round(records.reduce((sum, record) => sum + record.costUsd, 0) * 1e6) / 1e6 }) };

  if (task === "translate" || reread) {
    const store = new FileChunkStore<TranslateOutput>(join(outDir, "chunks"));
    const plan = planTranslate({ ...planBase, glossary, route, rules });
    // 모의 공급사는 심어 둔 응답만 돌려준다. 조각마다 재시도 몫까지 넉넉히 심는다.
    const reply = syntheticTranslator(glossary, estimator);
    if (replay) for (let i = 0; i < plan.chunks.length * settings.runner.maxAttempts; i++) replayMock.enqueue(replay.reply);
    else if (!live) for (let i = 0; i < plan.chunks.length * settings.runner.maxAttempts; i++) mock.enqueue(reply);
    log(`${reread ? "다시 읽기" : "번역"}: 조각 ${plan.chunks.length}개, 경로 ${route}, 상태 ${outDir}`);
    // 다시 읽기는 앞선 다시 읽기의 기록을 이어 받지 않는다 (규칙을 바꿔 가며 돌린다).
    const outcome = await runTranslate({ ...runBase, glossary, rules, store: values.fresh || reread ? freshStore(store) : store });
    writeJson(join(outDir, "plan.json"), { budgetTokens: plan.budgetTokens, chunks: plan.chunks.map(({ items: _, ...chunk }) => chunk) });
    writeJson(join(outDir, "result.json"), outcome.result);
    writeJson(join(outDir, "report.json"), { coverage: outcome.coverage, consistency: outcome.consistency, tableCells: outcome.result.tableCells, failures: failures(outcome.run.records) });
    // 사람이 훑어볼 의심 셀 목록. 의심이 없어도 파일을 쓴다 (분모와 함께, 「의심 0」 과 「검사 안 함」 을 가르려고).
    writeJson(join(outDir, "suspects.json"), { tableCells: outcome.result.tableCells, suspects: outcome.suspects });
    const { records: _, ...counts } = outcome.run;
    out(
      JSON.stringify(
        {
          task,
          provider: reread ? "stored-responses" : values.provider,
          synthetic: reread ? false : provenance.synthetic ? "모의 공급사의 응답은 번역이 아니다. 아래 수치는 파이프라인이 끝까지 도는지만 보여 준다" : false,
          ...(stored && replay
            ? {
                reread: {
                  note: "공급사를 부르지 않았다. 저장된 응답을 다시 읽어 지금의 규칙에 통과시킨 결과다. 조각 구성이 그때와 다른 요청(recomposedReplies)은 항목별 저장 번역을 다시 묶은 것이고, 모델이 이 조각 구성으로 낸 응답이 아니다",
                  calls: stored.path,
                  storedLines: stored.lines,
                  storedLinesUnusable: stored.unusable,
                  storedRequests: stored.byRequest.size,
                  ...replay.stats,
                },
              }
            : {}),
          route,
          outDir,
          plannedChunks: plan.chunks.length,
          chunks: counts,
          providerCallsThisRun: reread ? 0 : providerCalls(),
          ...(reread ? {} : provenance.costUsd(outcome.run.records)),
          failures: failures(outcome.run.records),
          blocks: countBy(outcome.result.blocks, (block) => block.status),
          tables: countBy(outcome.result.tables, (table) => table.status),
          coverage: { ok: outcome.coverage.ok, missingBlocks: outcome.coverage.missingBlocks.length, duplicatedBlocks: outcome.coverage.duplicatedBlocks.length, notTranslated: outcome.coverage.notTranslated.length },
          tableCells: outcome.result.tableCells,
          suspectsFile: join(outDir, "suspects.json"),
          consistency: {
            pairsChecked: outcome.consistency.pairsChecked,
            termOccurrences: outcome.consistency.termOccurrences,
            termMisses: outcome.consistency.termMisses.length,
            termDrifts: outcome.consistency.termDrifts.length,
            repeatDrifts: outcome.consistency.repeatDrifts.length,
            numberDrifts: outcome.consistency.numberDrifts.length,
          },
          glossary: { path: glossary.path, synthetic: glossary.synthetic },
        },
        null,
        1,
      ),
    );
    return outcome.run.failed > 0 ? 3 : 0;
  }

  const store = new FileChunkStore<GreenwashOutput>(join(outDir, "chunks"));
  const plan = planGreenwash({ ...planBase, checklist, route });
  const reply = syntheticReviewer(checklist, estimator);
  if (!live) for (let i = 0; i < plan.chunks.length * settings.runner.maxAttempts; i++) mock.enqueue(reply);
  log(`그린워싱 태깅: 조각 ${plan.chunks.length}개, 경로 ${route}, 상태 ${outDir}`);
  const outcome = await runGreenwash({ ...runBase, checklist, store: values.fresh ? freshStore(store) : store });
  const findings = outcome.run.records.flatMap((record) => (record.status === "done" ? record.output.findings.map((finding) => ({ chunkId: record.chunkId, ...finding })) : []));
  writeJson(join(outDir, "findings.json"), {
    checklist: { path: checklist.path, synthetic: checklist.synthetic, decisionPending: checklist.decisionPending ?? null },
    selection: { paragraphsConsidered: outcome.plan.selection.paragraphsConsidered, byKeyword: outcome.plan.selection.byKeyword, bySectionTitleOnly: outcome.plan.selection.bySectionTitleOnly },
    failures: failures(outcome.run.records),
    findings,
  });
  const { records: _, ...counts } = outcome.run;
  out(
    JSON.stringify(
      {
        task,
        provider: values.provider,
        synthetic: provenance.synthetic ? "모의 공급사의 응답은 판정이 아니다" : false,
        route,
        outDir,
        plannedChunks: plan.chunks.length,
        chunks: counts,
        providerCallsThisRun: providerCalls(),
        ...provenance.costUsd(outcome.run.records),
        failures: failures(outcome.run.records),
        selection: { paragraphsConsidered: outcome.plan.selection.paragraphsConsidered, byKeyword: outcome.plan.selection.byKeyword, bySectionTitleOnly: outcome.plan.selection.bySectionTitleOnly },
        findings: findings.length,
        checklist: { path: checklist.path, synthetic: checklist.synthetic, decisionPending: checklist.decisionPending ?? null },
      },
      null,
      1,
    ),
  );
  return outcome.run.failed > 0 ? 3 : 0;
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
