import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import {
  AnthropicProvider,
  BudgetGuard,
  FilePromptStore,
  GeminiProvider,
  HeuristicTokenEstimator,
  JsonlFileSink,
  LiveSetupError,
  LlmClient,
  MockProvider,
  OpenAIProvider,
  computeCostUsd,
  defaultConfigPaths,
  liveProviders,
  loadConfig,
  parseRequestsPerMinute,
  requireLimitUsd,
  resolveRoute,
  roundUsd,
  type CallLogSink,
  type CallRequest,
  type LiveDeps,
  type LlmConfig,
  type LlmResult,
  type ModelConfig,
  type Provider,
} from "llm";
import { ReconcileFileError, loadCatalog, loadGold, loadInput, loadMemory, loadSettings, loadUnits, promptRoot, readCallLog, readJsonFile, resolveInputPath, resolveOutputPath, type LoadedInput } from "./adapters/files.js";
import { syntheticLabeler } from "./adapters/mockLabeler.synthetic.js";
import { syntheticTranscriber } from "./adapters/mockTranscriber.synthetic.js";
import { JsonlUnitStore, seedBudgetFromLog } from "./adapters/unitStore.js";
import { XlsxError } from "./adapters/xlsx.js";
import { countRatio, renderRatioTable } from "./domain/count.js";
import type { Fact } from "./domain/facts.js";
import { inLabelScope, labelAnswerSchema, planUnits, type Catalog, type LabelAnswer, type LabelUnit } from "./domain/label.js";
import type { FactView, Finding } from "./domain/match.js";
import { applyMemory, type MemoryEntry } from "./domain/memory.js";
import { labelRequest, runReconcile, type LabelSettings, type LlmGateway, type RunResult } from "./domain/pipeline.js";
import { checkGold } from "./eval/gold.js";
import { buildScoreReport, renderScore, type DocAnswers, type UnitAnswer } from "./eval/score.js";
import { compareTranscription, transcribeAnswerSchema, transcribeRequest, transcriptionRow, type CellComparison, type TranscribeAnswer, type TranscriptionComparison, type TranscriptionRow } from "./eval/transcribe.js";
import { analyzeUsage, renderUsage, type UsageReport } from "./eval/usage.js";

type Out = (line: string) => void;

const USAGE = `사용법 (cd ts && pnpm -F reconcile exec tsx src/cli.ts <명령> ...)
  facts <입력> --out <디렉터리>
      입력 하나에서 사실 후보(숫자와 그 위치)를 뽑아 facts.json 으로 쓴다. 모델을 부르지 않는다.
  count <입력> [<입력> ...] --out <디렉터리>
      표 숫자와 문장 숫자의 비율을 센다. 모델을 부르지 않는다.
  run <입력 A> <입력 B> (--provider mock | --provider <openai|anthropic|gemini> --route <경로> --limit-usd <상한> | --dry-run) --out <디렉터리> [옵션]
      두 문서를 끝까지 잇는다: 사실 후보 → 뜻 붙이기 → 짝짓기 → 비교 → result.json 과 summary.md.
  score <입력 A> <입력 B> --gold <정답 파일> --run <run 의 출력 디렉터리> --out <디렉터리> [--transcribe <transcribe 의 출력 디렉터리>]
      저장된 실행 결과를 정답과 견줘 채점표(score.json, score.md)를 낸다. 모델을 부르지 않는다.
  transcribe <입력 A> <입력 B> --tables <표 목록> (--provider mock | --provider <공급사> --route <경로> --limit-usd <상한> | --dry-run) --out <디렉터리>
      측정용 시험: 표의 숫자를 모델에게 글자째 옮겨 적게 하고 원문과 칸마다 견준다. 이 결과는 대조에 쓰이지 않는다.

입력은 문서 파서(py/docparse)의 document.json 또는 엑셀(.xlsx) 이다.
--out 은 반드시 준다 (기본 출력 경로가 없다). 상대 경로는 ts/ 기준이다.

run 의 옵션
  --provider mock     모의 공급사로 끝까지 돌린다. 응답기는 synthetic 이고 모델이 아니다. 결과 분포는 성능과 무관하다.
  --provider <openai|anthropic|gemini>
                      실제 공급사를 부른다. 돈이 든다. --route 와 --limit-usd 가 필요하고 키는 그 공급사의 환경 변수
                      (OPENAI_API_KEY 등)로 명령 한 줄에만 실어 넘긴다. 경로에 다른 공급사의 모델이 섞여 있으면 부르지 않는다.
  --dry-run           공급사를 부르지 않고 호출 수·입력 토큰·비용 상한을 어림한다.
  --route <경로>      실제 호출에서는 하나. --dry-run 에서는 여러 번 줄 수 있다 (기본: data/reconcile/settings.json 의 dryRunRoutes).
  --limit-usd <x>     누적 비용 상한. 닿으면 남은 요청은 부르지 않는다. 같은 --out 으로 이어 하면 앞선 실행이 쓴 비용도 같이 센다.
  --store-bodies      호출 기록에 요청 본문과 응답 글자를 그대로 남긴다 (기본은 해시만).
  --log <파일>        호출 기록 파일 (기본: <out>/calls.jsonl).
  --tables <목록>     이 표만 모델에 보낸다 (작은 실행). 꼴: a:p46-t2,b:p21-t1 (문서:표 ID). 나머지 숫자는 미확인(not_requested)이다.
  --timeout-ms <n>    호출 하나의 시간 제한.
  --rpm <n>           실제 공급사 호출의 시작 간격을 벌려 분당 n 건을 넘지 않게 한다.
  --kind-a, --kind-b  문서 종류 이름. 확정 기억을 찾을 때 쓴다 (기본 a, b).
  --period-a, --period-b  표에 기간이 안 적혀 있을 때 쓸 그 문서의 기본 연도.
  --memory <파일>     확정 기억 파일. 맞는 셀은 AI 를 부르지 않고 뜻을 붙인다.
  --catalog <파일>    지표 목록 (기본: data/reconcile/indicators.synthetic.json, synthetic).
  --sentences         문장 속 숫자도 뜻 붙이기에 넣는다 (기본은 표의 숫자 셀만).
실제 공급사 실행은 요청 단위(표)마다 결과를 <out>/units.jsonl 에 남긴다. 같은 --out 으로 다시 돌리면 이미 답을 받은 표는
다시 부르지 않고 이어 한다 (공급사 오류·시간 초과·상한에 막힌 표만 다시 부른다).`;

const prompts = new FilePromptStore(promptRoot);
const TAGS = { tenant: "tenant-synthetic", module: "reconcile" };
/** 실제 공급사 실행의 꼬리표. 비용 상한은 모듈 단위로 센다. */
const LIVE_TAGS = { tenant: "measure", module: "reconcile" };
const TRANSCRIBE_TAGS = { tenant: "measure", module: "reconcile-transcribe" };

function writeJson(path: string, value: unknown): void {
  writeFileSync(path, `${JSON.stringify(value, null, 1)}\n`);
}

function whereText(view: FactView): string {
  const { where } = view;
  if (where.sheet !== null) return `시트 ${where.sheet} ${where.ref ?? ""}`.trim();
  if (where.gridId !== null) return `${where.page}쪽 표 ${where.gridId} r${where.row}c${where.col}`;
  return `${where.page}쪽 블록 ${where.blockId}${where.span ? ` (${where.span.occurrence}번째 「${view.raw}」)` : ""}`;
}

const escape = (text: string): string => text.replace(/\|/g, "\\|").replace(/\s+/g, " ");

function findingRow(finding: Finding): string {
  const side = (view: FactView | null): string => (view ? `${escape(view.raw)}${view.label?.unit ? ` ${escape(view.label.unit)}` : ""} · ${whereText(view)}` : "-");
  const key = finding.key ? `${finding.key.indicator} · ${finding.key.period} · ${finding.key.boundary}` : "-";
  const rule = finding.comparison ? `${finding.comparison.baseA} 대 ${finding.comparison.baseB} (10^${finding.comparison.quantumExp} 자리, ${finding.comparison.rule})` : "";
  return `| ${key} | ${side(finding.a)} | ${side(finding.b)} | ${finding.reason}${finding.detail ? `: ${escape(finding.detail)}` : ""} | ${rule} |`;
}

function renderSummary(result: RunResult, meta: { a: LoadedInput; b: LoadedInput; catalog: Catalog; mock: boolean }): string {
  const lines: string[] = ["# 데이터 검증 결과 요약", ""];
  if (meta.mock) lines.push("**모의 실행이다.** 뜻을 붙인 것은 모델이 아니라 머리글 낱말을 글자로 대조하는 synthetic 응답기다. 아래 분포는 모델의 성능과 무관하다.", "");
  if (meta.catalog.synthetic) lines.push(`**지표 목록이 synthetic 이다** (${meta.catalog.id}). 실제 기준 목록이 아니다.`, "");
  for (const [name, doc, input] of [["A", result.summary.a, meta.a], ["B", result.summary.b, meta.b]] as const) {
    lines.push(`## 문서 ${name}: ${doc.fileName}`, "", `- ${doc.sentence}`, `- 입력: ${input.path} (${input.format})`);
    lines.push(`- 신뢰도 낮은 표에서 나온 숫자 ${doc.lowConfidenceTableNumbers}개 (미확인에 들어 있다), 추출 실패 표 ${doc.failedTables}개, 텍스트 없는 쪽 ${doc.pagesWithoutText.length}개`);
    const reasons = Object.entries(doc.unverifiedByReason).sort((x, y) => y[1] - x[1]);
    lines.push("", "| 미확인 사유 | 수 |", "|---|---:|", ...reasons.map(([reason, count]) => `| ${reason} | ${count} |`), "");
  }
  lines.push("## 짝", "", `일치 ${result.summary.pairs.match}쌍, 불일치 ${result.summary.pairs.mismatch}쌍, 설명 필요 ${result.summary.pairs.needs_explanation}쌍`, "");
  const header = ["| 뜻 (지표 · 기간 · 범위) | A 의 원문과 위치 | B 의 원문과 위치 | 사유 | 견준 값 |", "|---|---|---|---|---|"];
  for (const [title, outcome] of [["불일치", "mismatch"], ["설명 필요", "needs_explanation"], ["일치", "match"]] as const) {
    const rows = result.findings.filter((finding) => finding.outcome === outcome && finding.a && finding.b);
    lines.push(`### ${title} (${rows.length}쌍)`, "");
    if (rows.length > 0) lines.push(...header, ...rows.slice(0, 200).map(findingRow), rows.length > 200 ? `\n(${rows.length - 200}쌍 더 있다. 전체는 result.json)` : "", "");
  }
  const unpaired = result.findings.filter((finding) => finding.outcome === "unpaired");
  lines.push(`### 짝 없음 (${unpaired.length}개)`, "");
  if (unpaired.length > 0) lines.push(...header, ...unpaired.slice(0, 100).map(findingRow), unpaired.length > 100 ? `\n(${unpaired.length - 100}개 더 있다. 전체는 result.json)` : "", "");
  return `${lines.join("\n")}\n`;
}

interface DryRunRow {
  route: string;
  provider: string;
  model: string;
  pricingUsdPerMTok: { input: number; output: number };
  calls: number;
  targets: number;
  inputTokens: number;
  expectedOutputTokens: number;
  expectedCostUsd: number;
  /** 요청마다 출력 상한을 다 썼을 때. */
  costUpperBoundUsd: number;
  cacheablePrefixTokens: number;
  warnings: Record<string, number>;
}

function dryRun(config: LlmConfig, routes: string[], inputs: { input: LoadedInput; kind: string }[], catalog: Catalog, memory: MemoryEntry[], settings: LabelSettings, includeSentences: boolean, unitFilter: (unit: LabelUnit) => boolean = () => true): DryRunRow[] {
  const client = new LlmClient({
    config,
    // SDK 클라이언트 없이 등록한다. 요청 조립만 한다.
    providers: { mock: new MockProvider(), anthropic: new AnthropicProvider(), openai: new OpenAIProvider(), gemini: new GeminiProvider() },
    prompts,
    log: { write: () => undefined },
  });
  return routes.map((route) => {
    const model = resolveRoute(config, route)[0];
    if (!model) throw new ReconcileFileError(`경로 ${route} 에 모델이 없다`);
    const row: DryRunRow = { route, provider: model.provider, model: model.key, pricingUsdPerMTok: { input: model.pricing.input, output: model.pricing.output }, calls: 0, targets: 0, inputTokens: 0, expectedOutputTokens: 0, expectedCostUsd: 0, costUpperBoundUsd: 0, cacheablePrefixTokens: 0, warnings: {} };
    for (const { input, kind } of inputs) {
      const scope = input.extraction.facts.filter((fact) => inLabelScope(fact, { includeSentences }));
      const remembered = new Set(applyMemory(scope.filter((fact) => fact.bucket === "table_cell"), kind, memory).labels.map((label) => label.factId));
      const pending: Fact[] = scope.filter((fact) => !remembered.has(fact.id));
      for (const unit of planUnits(input.extraction, pending, { maxTargetsPerCall: settings.maxTargetsPerCall, includeSentences }, input.blockText).filter(unitFilter)) {
        const report = client.dryRun(labelRequest(unit, catalog, { route, tags: TAGS, settings, modelMaxOutputTokens: model.maxOutputTokens }));
        const output = unit.targetFactIds.length * settings.outputTokensPerLabel + settings.outputTokensOverhead;
        row.calls += 1;
        row.targets += unit.targetFactIds.length;
        row.inputTokens += report.estimate.inputTokens;
        row.expectedOutputTokens += output;
        row.expectedCostUsd += computeCostUsd({ inputTokens: report.estimate.inputTokens, outputTokens: output, cacheReadTokens: 0, cacheWriteTokens: 0 }, model.pricing);
        row.costUpperBoundUsd += report.estimate.costUpperBoundUsd;
        row.cacheablePrefixTokens = report.estimate.cacheableTokens;
        for (const warning of report.warnings) row.warnings[warning.code] = (row.warnings[warning.code] ?? 0) + 1;
      }
    }
    row.expectedCostUsd = roundUsd(row.expectedCostUsd);
    row.costUpperBoundUsd = roundUsd(row.costUpperBoundUsd);
    return row;
  });
}

/** `--tables a:p46-t2,b:p21-t1` 을 요청 단위의 거름으로 바꾼다. 계획에 없는 표를 대면 오류다. */
function tableFilter(raw: string | undefined, planned: LabelUnit[]): ((unit: LabelUnit) => boolean) | undefined {
  if (raw === undefined) return undefined;
  const names = raw.split(",").map((name) => name.trim()).filter((name) => name !== "");
  const known = new Set(planned.map((unit) => `${unit.docId}:${unit.gridId}`));
  const unknown = names.filter((name) => !known.has(name));
  if (names.length === 0 || unknown.length > 0) throw new ReconcileFileError(`--tables: 뜻 붙이기 대상에 없는 표다: ${unknown.join(", ") || "(비어 있음)"}. 꼴은 a:p46-t2,b:p21-t1 (문서:표 ID)`);
  const wanted = new Set(names);
  return (unit) => wanted.has(`${unit.docId}:${unit.gridId}`);
}

function planAll(inputs: LoadedInput[], settings: LabelSettings, includeSentences: boolean): LabelUnit[] {
  return inputs.flatMap((input) => planUnits(input.extraction, input.extraction.facts.filter((fact) => inLabelScope(fact, { includeSentences })), { maxTargetsPerCall: settings.maxTargetsPerCall, includeSentences }, input.blockText));
}

interface LiveSetup {
  providers: Record<string, Provider>;
  budget: BudgetGuard;
  limitUsd: number;
  route: string;
  model: ModelConfig;
}

/** 실제 공급사 호출의 조립. 경로·상한·키 중 하나라도 없으면 부르지 않는다 (`LiveSetupError`). */
function liveSetup(config: LlmConfig, values: { provider?: string | undefined; route?: string[] | undefined; "limit-usd"?: string | undefined; rpm?: string | undefined }, module: string, deps: LiveDeps): LiveSetup {
  const routes = values.route ?? [];
  if (routes.length !== 1) throw new LiveSetupError("실제 공급사 호출에는 --route <경로> 가 하나 필요하다");
  const route = routes[0] as string;
  if (!config.routes[route]) throw new LiveSetupError(`설정에 없는 경로다: ${route}`);
  const limitUsd = requireLimitUsd(values["limit-usd"]);
  const providers = liveProviders(config, { provider: values.provider as string, route, ...parseRequestsPerMinute(values.rpm) }, deps);
  const model = resolveRoute(config, route)[0];
  if (!model) throw new LiveSetupError(`경로 ${route} 에 모델이 없다`);
  return { providers, budget: new BudgetGuard([{ scope: { module }, limitUsd }]), limitUsd, route, model };
}

/** 이 실행에서 공급사를 실제로 부른 횟수를 세는 기록 자리 (상한에 막혀 부르지 않은 줄은 뺀다). */
function countingSink(path: string): { sink: CallLogSink; providerCalls: () => number } {
  const inner = new JsonlFileSink(path);
  let calls = 0;
  return {
    sink: {
      write(record) {
        if (record.resultKind !== "budget_exceeded") calls += 1;
        inner.write(record);
      },
    },
    providerCalls: () => calls,
  };
}

interface RunMeta {
  provider: string;
  route: string;
  model: string;
  modelMaxOutputTokens: number;
  includeSentences: boolean;
  log: string;
  units: string | null;
}

interface TranscribeMeta extends RunMeta {
  tables: string[];
}

interface TranscribeReport {
  rows: TranscriptionRow[];
  total: TranscriptionRow;
  /** 원문과 글자가 같지 않은 칸 전부 (표기만 다른 것 포함). */
  notSame: (CellComparison & { table: string })[];
  fabricated: (TranscriptionComparison["fabricated"][number] & { table: string })[];
  failedUnits: { unitId: string; kind: string; targets: number }[];
}

/** 옮겨 쓴 답을 표마다 모아 센다. 답을 받지 못한 요청의 칸은 분모에 남고 「빠짐」으로 센다. */
function transcribeReport(units: LabelUnit[], answers: Map<string, UnitAnswerOf<TranscribeAnswer>>): TranscribeReport {
  const tables = new Map<string, { cells: CellComparison[]; fabricated: number; inFailed: number }>();
  const report: TranscribeReport = { rows: [], total: transcriptionRow("전체", [], 0), notSame: [], fabricated: [], failedUnits: [] };
  for (const unit of units) {
    const table = `${unit.docId}:${unit.gridId}`;
    const entry = tables.get(table) ?? { cells: [], fabricated: 0, inFailed: 0 };
    tables.set(table, entry);
    const result = answers.get(unitKey(unit)) ?? { status: "not_run" as const };
    const answered = result.status === "answered";
    const compared = compareTranscription(unit, answered ? result.answer : { cells: [] });
    entry.cells.push(...compared.cells);
    entry.fabricated += compared.fabricated.length;
    if (!answered) {
      entry.inFailed += compared.cells.length;
      report.failedUnits.push({ unitId: unitKey(unit), kind: result.status === "failed" ? result.kind : "not_run", targets: compared.cells.length });
    }
    report.notSame.push(...compared.cells.filter((cell) => cell.verdict !== "same").map((cell) => ({ ...cell, table })));
    report.fabricated.push(...compared.fabricated.map((item) => ({ ...item, table })));
  }
  report.rows = [...tables.entries()].map(([name, entry]) => transcriptionRow(name, entry.cells, entry.fabricated, entry.inFailed));
  report.total = transcriptionRow("전체", [...tables.values()].flatMap((entry) => entry.cells), report.fabricated.length, [...tables.values()].reduce((sum, entry) => sum + entry.inFailed, 0));
  return report;
}

/** 요청 단위의 ID 는 문서 안에서만 유일하다 (같은 표 ID 가 두 문서에 다 있을 수 있다). 두 문서를 한데 모을 때는 문서를 붙여 가린다. */
const unitKey = (unit: LabelUnit): string => `${unit.docId}:${unit.id}`;

type UnitAnswerOf<T> = { status: "answered"; answer: T } | { status: "failed"; kind: string } | { status: "not_run" };

function renderTranscribe(report: TranscribeReport, note: string): string {
  const pct = (part: number, whole: number): string => (whole === 0 ? `${part}/0` : `${part}/${whole} (${((part / whole) * 100).toFixed(1)}%)`);
  const lines = ["## 6. 옮겨 쓰기 비교", "", note, "", "| 표 | 옮겨 적으라고 한 칸 | 같음 | 표기만 다름 (쉼표·공백) | 다름 | 글자 바뀜 | 자리 바뀜 | 빠짐 | 겹친 답 | 지어냄 (대상 밖의 칸) |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"];
  for (const row of [...report.rows, report.total]) lines.push(`| ${row.name} | ${row.cells} | ${pct(row.same, row.cells)} | ${row.notationOnly} | ${pct(row.different, row.cells)} | ${row.charsChanged} | ${row.positionSwapped} | ${row.missing} | ${row.duplicate} | ${row.fabricated} |`);
  if (report.failedUnits.length > 0) lines.push("", `답을 받지 못한 요청: ${report.failedUnits.map((unit) => `${unit.unitId} (${unit.kind}, 칸 ${unit.targets}개)`).join(", ")}. 그 칸은 「빠짐」에 들어 있다.`);
  if (report.notSame.length > 0) {
    lines.push("", "원문과 글자가 같지 않은 칸:", "", "| 표 | 칸 | 원문 | 모델이 적은 글자 | 판정 |", "|---|---|---|---|---|");
    for (const cell of report.notSame) lines.push(`| ${cell.table} | ${cell.target} | ${cell.source} | ${cell.answer ?? "(없음)"} | ${cell.verdict}${cell.swappedWith ? ` (${cell.swappedWith} 의 글자)` : ""} |`);
  }
  if (report.fabricated.length > 0) lines.push("", `지어냄: ${report.fabricated.map((item) => `${item.table} ${item.target} 「${item.text}」${item.inRequest ? " (요청에 있는 칸이지만 대상이 아니다)" : " (요청에 없는 칸)"}`).join(", ")}`);
  return `${lines.join("\n")}\n`;
}

function answersFrom<T>(store: JsonlUnitStore<T>, units: LabelUnit[], request: (unit: LabelUnit) => Parameters<JsonlUnitStore<T>["find"]>[1]): { answers: Map<string, UnitAnswerOf<T>>; calls: { unitId: string; callId: string; targets: number }[] } {
  const answers = new Map<string, UnitAnswerOf<T>>();
  const calls: { unitId: string; callId: string; targets: number }[] = [];
  for (const unit of units) {
    const stored = store.find(unit.id, request(unit));
    if (!stored) continue;
    answers.set(unitKey(unit), stored.result.kind === "success" ? { status: "answered", answer: stored.result.data } : { status: "failed", kind: stored.result.kind });
    calls.push({ unitId: unit.id, callId: stored.callId, targets: unit.targetFactIds.length });
  }
  return { answers, calls };
}

export async function main(argv: string[], out: Out = console.log, log: Out = console.error, deps: LiveDeps = { env: process.env }): Promise<number> {
  const [command, ...rest] = argv;
  let parsed;
  try {
    parsed = parseArgs({
      args: rest,
      allowPositionals: true,
      options: {
        out: { type: "string" },
        provider: { type: "string" },
        "dry-run": { type: "boolean" },
        route: { type: "string", multiple: true },
        "limit-usd": { type: "string" },
        "store-bodies": { type: "boolean", default: false },
        log: { type: "string" },
        tables: { type: "string" },
        "timeout-ms": { type: "string" },
        rpm: { type: "string" },
        gold: { type: "string" },
        run: { type: "string" },
        transcribe: { type: "string" },
        "kind-a": { type: "string" },
        "kind-b": { type: "string" },
        "period-a": { type: "string" },
        "period-b": { type: "string" },
        memory: { type: "string" },
        catalog: { type: "string" },
        sentences: { type: "boolean" },
      },
    });
  } catch (error) {
    out(`${(error as Error).message}\n\n${USAGE}`);
    return 2;
  }
  const { values, positionals } = parsed;
  if (!command || !["facts", "count", "run", "score", "transcribe"].includes(command) || !values.out) {
    out(USAGE);
    return 2;
  }
  try {
    const outDir = resolveOutputPath(values.out);
    if (command === "facts") {
      if (positionals.length !== 1) return out(USAGE), 2;
      const input = loadInput(resolveInputPath(positionals[0] as string), "a");
      mkdirSync(outDir, { recursive: true });
      writeJson(join(outDir, "facts.json"), { input: input.path, format: input.format, ...input.extraction, grids: input.extraction.grids.map(({ cells: _cells, numeric: _numeric, refs: _refs, ...grid }) => grid) });
      out(JSON.stringify({ out: join(outDir, "facts.json"), ...countRatio(input.extraction) }, null, 1));
      return 0;
    }
    if (command === "count") {
      if (positionals.length === 0) return out(USAGE), 2;
      const rows = positionals.map((path, index) => countRatio(loadInput(resolveInputPath(path), `doc${index + 1}`).extraction));
      mkdirSync(outDir, { recursive: true });
      writeJson(join(outDir, "ratio.json"), rows);
      const table = renderRatioTable(rows);
      writeFileSync(join(outDir, "ratio.md"), `${table}\n`);
      out(table);
      out(`\n값 후보가 아닌 숫자의 갈래:\n${rows.map((row) => `${row.fileName}: ${JSON.stringify(row.nonValues)}`).join("\n")}`);
      return 0;
    }

    if (positionals.length !== 2) return out(USAGE), 2;
    const timeoutMs = values["timeout-ms"] === undefined ? undefined : Number(values["timeout-ms"]);
    if (timeoutMs !== undefined && !(Number.isInteger(timeoutMs) && timeoutMs > 0)) return out(`--timeout-ms 는 양의 정수다: ${values["timeout-ms"]}`), 2;
    const a = loadInput(resolveInputPath(positionals[0] as string), "a");
    const b = loadInput(resolveInputPath(positionals[1] as string), "b");
    const catalog = loadCatalog(values.catalog ? resolveInputPath(values.catalog) : undefined);
    const settings = loadSettings();
    const config = loadConfig(defaultConfigPaths({ synthetic: true }));
    const includeSentences = values.sentences === true;

    if (command === "score") return score({ a, b, catalog, settings: settings.label, config, values, outDir, out });

    if ((values.provider === undefined) === (values["dry-run"] !== true)) return out(USAGE), 2;
    const live = values.provider !== undefined && values.provider !== "mock";

    if (command === "transcribe") {
      const planned = planAll([a, b], settings.label, false);
      if (values.tables === undefined) return out("transcribe 에는 --tables <표 목록> 이 필요하다 (예: a:p47-t2,b:p22-t1)"), 2;
      const filter = tableFilter(values.tables, planned) as (unit: LabelUnit) => boolean;
      const units = planned.filter(filter);
      const tables = [...new Set(units.map((unit) => `${unit.docId}:${unit.gridId}`))];
      if (values["dry-run"]) {
        const client = new LlmClient({ config, providers: { mock: new MockProvider(), anthropic: new AnthropicProvider(), openai: new OpenAIProvider(), gemini: new GeminiProvider() }, prompts, log: { write: () => undefined } });
        const rows = (values.route ?? settings.dryRunRoutes).map((route) => {
          const model = resolveRoute(config, route)[0];
          if (!model) throw new ReconcileFileError(`경로 ${route} 에 모델이 없다`);
          const reports = units.map((unit) => client.dryRun(transcribeRequest(unit, { route, tags: TRANSCRIBE_TAGS, modelMaxOutputTokens: model.maxOutputTokens })));
          return { route, model: model.key, calls: units.length, cells: units.reduce((sum, unit) => sum + unit.targetFactIds.length, 0), inputTokens: reports.reduce((sum, report) => sum + report.estimate.inputTokens, 0), costUpperBoundUsd: roundUsd(reports.reduce((sum, report) => sum + report.estimate.costUpperBoundUsd, 0)) };
        });
        mkdirSync(outDir, { recursive: true });
        writeJson(join(outDir, "dry-run.json"), { dryRun: true, note: "공급사를 부르지 않았다. 토큰은 글자 수 어림이다. 비용 상한은 호출마다 출력 상한을 다 쓸 때의 값이고 재시도는 들어 있지 않다.", tables, routes: rows });
        for (const row of rows) out(`[경로 ${row.route}] 모델 ${row.model} · 호출 수 ${row.calls} · 옮겨 적을 칸 ${row.cells} · 입력 토큰 어림 ${row.inputTokens} · 비용 상한 ${row.costUpperBoundUsd} USD`);
        return 0;
      }
      let setup: LiveSetup | undefined;
      if (live) {
        try {
          setup = liveSetup(config, values, TRANSCRIBE_TAGS.module, deps);
        } catch (error) {
          if (!(error instanceof LiveSetupError)) throw error;
          out(`실제 공급사 호출을 지원하지 않는다: ${error.message}`);
          return 2;
        }
      }
      mkdirSync(outDir, { recursive: true });
      const logPath = values.log ? resolveOutputPath(values.log) : join(outDir, "calls.jsonl");
      const unitsPath = join(outDir, "units.jsonl");
      const route = setup?.route ?? "mock";
      const model = setup?.model ?? resolveRoute(config, route)[0];
      if (!model) throw new ReconcileFileError("모의 경로가 설정에 없다");
      const mock = new MockProvider();
      if (!setup) {
        // 모의 실행은 같은 --out 으로 다시 돌릴 때 앞 실행의 줄이 섞이지 않게 비운다.
        writeFileSync(logPath, "");
        writeFileSync(unitsPath, "");
        const reply = syntheticTranscriber(new HeuristicTokenEstimator(config.tokenEstimate));
        for (let i = 0; i < units.length * 3; i++) mock.enqueue(reply);
      }
      const seeded = setup ? seedBudgetFromLog(logPath, setup.budget, TRANSCRIBE_TAGS) : { lines: 0, spentUsd: 0 };
      const counting = countingSink(logPath);
      const client = new LlmClient({ config, providers: setup?.providers ?? { mock }, prompts, log: counting.sink, storeBodies: values["store-bodies"], ...(setup ? { budget: setup.budget } : {}), ...(deps.sleep ? { sleep: deps.sleep } : {}) });
      const store = new JsonlUnitStore<TranscribeAnswer>(unitsPath, transcribeAnswerSchema);
      const answers = new Map<string, UnitAnswerOf<TranscribeAnswer>>();
      let costUsd = 0;
      let resumed = 0;
      for (const unit of units) {
        const request = transcribeRequest(unit, { route, tags: TRANSCRIBE_TAGS, modelMaxOutputTokens: model.maxOutputTokens, ...(timeoutMs === undefined ? {} : { timeoutMs }) });
        const stored = store.get(unit.id, request);
        const result = stored ?? (await client.call(request));
        if (stored) resumed += 1;
        else {
          costUsd += result.meta.totalCostUsd;
          store.put(unit.id, request, result);
          log(`[옮겨 쓰기] ${unitKey(unit)}: ${result.kind} (${result.meta.totalCostUsd} USD)`);
        }
        answers.set(unitKey(unit), result.kind === "success" ? { status: "answered", answer: result.data } : { status: "failed", kind: result.kind });
      }
      const report = transcribeReport(units, answers);
      const meta: TranscribeMeta = { provider: values.provider as string, route, model: model.key, modelMaxOutputTokens: model.maxOutputTokens, includeSentences: false, log: logPath, units: unitsPath, tables };
      const synthetic = setup ? null : "모의 실행 (synthetic 응답기가 요청의 글자를 그대로 돌려준다). 결과는 모델의 성능과 무관하다.";
      writeJson(join(outDir, "transcribe.json"), { ...(synthetic ? { synthetic } : {}), run: meta, note: "측정용 시험이다. 이 글자는 대조에 쓰이지 않는다.", ...report });
      out(JSON.stringify({ ...(synthetic ? { synthetic } : {}), provider: meta.provider, route, model: model.key, providerCalls: counting.providerCalls(), resumedUnits: resumed, costUsd: roundUsd(costUsd), ...(setup ? { limitUsd: setup.limitUsd, spentBeforeUsd: roundUsd(seeded.spentUsd) } : {}), total: report.total, failedUnits: report.failedUnits, files: { report: join(outDir, "transcribe.json"), units: unitsPath, calls: logPath } }, null, 1));
      return 0;
    }

    // run
    for (const period of [values["period-a"], values["period-b"]]) {
      if (period !== undefined && !/^(19|20)\d{2}$/.test(period)) return out("기본 기간은 네 자리 연도여야 한다"), 2;
    }
    const units = loadUnits();
    const memory = values.memory ? loadMemory(resolveInputPath(values.memory), catalog) : [];
    const kinds = { a: values["kind-a"] ?? "a", b: values["kind-b"] ?? "b" };
    const planned = planAll([a, b], settings.label, includeSentences);
    const unitFilter = tableFilter(values.tables, planned);
    const inputsMeta = { a: { path: a.path, format: a.format, kind: kinds.a }, b: { path: b.path, format: b.format, kind: kinds.b }, catalog: { id: catalog.id, synthetic: catalog.synthetic, path: catalog.path, sha256: catalog.sha256 }, memoryEntries: memory.length, includeSentences, ...(values.tables ? { tables: values.tables } : {}) };

    if (values["dry-run"]) {
      mkdirSync(outDir, { recursive: true });
      const rows = dryRun(config, values.route ?? settings.dryRunRoutes, [{ input: a, kind: kinds.a }, { input: b, kind: kinds.b }], catalog, memory, settings.label, includeSentences, unitFilter);
      const report = { dryRun: true, note: "공급사를 부르지 않았다. 토큰은 글자 수 어림이고 실제 토큰 수가 아니다. 예상 출력은 답 하나를 outputTokensPerLabel 로 어림한 가정이다. 캐시·배치 할인은 넣지 않았다. 비용 상한은 호출마다 한 번에 성공할 때의 값이고 스키마 불일치 재시도(최대 2회)와 공급사 오류 재시도는 들어 있지 않다.", inputs: inputsMeta, settings: settings.label, routes: rows };
      writeJson(join(outDir, "dry-run.json"), report);
      for (const row of rows) {
        out(`[경로 ${row.route}] 모델 ${row.model} (${row.provider}) · 단가 입력 ${row.pricingUsdPerMTok.input} / 출력 ${row.pricingUsdPerMTok.output} USD/MTok (data/llm 설정)`);
        out(`  호출 수 ${row.calls} · 답할 숫자 ${row.targets} · 입력 토큰 어림 ${row.inputTokens} · 예상 출력 토큰 ${row.expectedOutputTokens}`);
        out(`  예상 비용 ${row.expectedCostUsd} USD · 비용 상한(출력 상한을 다 쓸 때) ${row.costUpperBoundUsd} USD · 캐시 대상 앞부분 ${row.cacheablePrefixTokens} 토큰`);
        for (const [code, count] of Object.entries(row.warnings)) out(`  경고 ${code}: ${count}개 호출`);
      }
      out(`보고: ${join(outDir, "dry-run.json")}`);
      return 0;
    }

    let setup: LiveSetup | undefined;
    if (live) {
      // 실제 공급사. 경로와 상한을 명시하지 않거나 키가 없으면 부르지 않는다.
      try {
        setup = liveSetup(config, values, LIVE_TAGS.module, deps);
      } catch (error) {
        if (!(error instanceof LiveSetupError)) throw error;
        out(`실제 공급사 호출을 지원하지 않는다: ${error.message}`);
        return 2;
      }
    }
    mkdirSync(outDir, { recursive: true });
    const logPath = values.log ? resolveOutputPath(values.log) : join(outDir, "calls.jsonl");
    const route = setup?.route ?? "mock";
    const model = setup?.model ?? resolveRoute(config, route)[0];
    if (!model) throw new ReconcileFileError("모의 경로가 설정에 없다");
    const tags = setup ? LIVE_TAGS : TAGS;
    const mock = new MockProvider();
    let store: JsonlUnitStore<LabelAnswer> | undefined;
    let seeded = { lines: 0, spentUsd: 0 };
    if (setup) {
      // 이어 하기: 앞선 실행의 결과와 비용을 읽어 온다. 호출 기록은 지우지 않고 이어 쓴다.
      store = new JsonlUnitStore<LabelAnswer>(join(outDir, "units.jsonl"), labelAnswerSchema);
      seeded = seedBudgetFromLog(logPath, setup.budget, tags);
      if (seeded.lines > 0 || store.size > 0) log(`이어 하기: 저장된 요청 결과 ${store.size}개, 앞선 호출 기록 ${seeded.lines}줄 (${roundUsd(seeded.spentUsd)} USD 를 상한에 넣는다)`);
    } else {
      // 모의 실행의 호출 기록은 이어 쓰는 파일이다. 같은 --out 으로 다시 돌릴 때 앞 실행의 줄이 섞이지 않게 비운다 (result.json 도 덮어쓴다).
      writeFileSync(logPath, "");
      const reply = syntheticLabeler(catalog, units, [a.extraction, b.extraction], new HeuristicTokenEstimator(config.tokenEstimate));
      // 심어 둔 응답이 떨어지면 모의 공급사는 오류를 낸다. 호출 수의 상한만큼 심는다 (스키마 재시도 2회 포함).
      for (let i = 0; i < planned.length * 3; i++) mock.enqueue(reply);
    }
    const counting = countingSink(logPath);
    const client = new LlmClient({ config, providers: setup?.providers ?? { mock }, prompts, log: counting.sink, storeBodies: values["store-bodies"], ...(setup ? { budget: setup.budget } : {}), ...(deps.sleep ? { sleep: deps.sleep } : {}) });
    let finished = 0;
    const gateway: LlmGateway = {
      async call<T = string>(request: CallRequest<T>): Promise<LlmResult<T>> {
        const result = await client.call(request);
        finished += 1;
        if (setup) log(`[뜻 붙이기] ${finished}번째 호출: ${result.kind} (${result.meta.totalCostUsd} USD)`);
        return result;
      },
    };

    const result = await runReconcile({
      a: { extraction: a.extraction, docKind: kinds.a, defaultPeriod: values["period-a"] ?? null, blockText: a.blockText },
      b: { extraction: b.extraction, docKind: kinds.b, defaultPeriod: values["period-b"] ?? null, blockText: b.blockText },
      catalog,
      units,
      memory,
      settings: settings.label,
      includeSentences,
      gateway,
      route,
      tags,
      modelMaxOutputTokens: model.maxOutputTokens,
      ...(timeoutMs === undefined ? {} : { timeoutMs }),
      ...(unitFilter ? { unitFilter } : {}),
      ...(store ? { store } : {}),
    });
    const synthetic = setup ? null : "모의 실행 (synthetic 응답기). 결과 분포는 모델의 성능과 무관하다.";
    const run: RunMeta = { provider: values.provider as string, route, model: model.key, modelMaxOutputTokens: model.maxOutputTokens, includeSentences, log: logPath, units: store ? join(outDir, "units.jsonl") : null };
    const spend = setup ? { limitUsd: setup.limitUsd, spentBeforeUsd: roundUsd(seeded.spentUsd), spentUsd: setup.budget.spent({ module: tags.module }) } : null;
    writeJson(join(outDir, "result.json"), { ...(synthetic ? { synthetic } : {}), provider: values.provider, run, ...(spend ? { spend } : {}), inputs: inputsMeta, ...(timeoutMs === undefined ? {} : { timeoutMs }), summary: result.summary, labeling: result.labeling, findings: result.findings });
    writeFileSync(join(outDir, "summary.md"), renderSummary(result, { a, b, catalog, mock: !setup }));
    out(JSON.stringify({ ...(synthetic ? { synthetic } : {}), provider: values.provider, route, model: model.key, catalogSynthetic: catalog.synthetic, providerCalls: setup ? counting.providerCalls() : mock.calls.length, ...(spend ? { spend } : {}), labeling: result.labeling, pairs: result.summary.pairs, needsExplanationByReason: result.summary.needsExplanationByReason, a: result.summary.a, b: result.summary.b, files: { result: join(outDir, "result.json"), summary: join(outDir, "summary.md"), calls: logPath, ...(store ? { units: join(outDir, "units.jsonl") } : {}) } }, null, 1));
    return 0;
  } catch (error) {
    if (error instanceof ReconcileFileError || error instanceof XlsxError) {
      log(error.message);
      out(error.message);
      return 1;
    }
    throw error;
  }
}

interface ScoreContext {
  a: LoadedInput;
  b: LoadedInput;
  catalog: ReturnType<typeof loadCatalog>;
  settings: LabelSettings;
  config: LlmConfig;
  values: { gold?: string | undefined; run?: string | undefined; transcribe?: string | undefined };
  outDir: string;
  out: Out;
}

/** 저장된 실행 결과를 정답과 견줘 채점표를 낸다. 모델을 부르지 않는다. */
function score(ctx: ScoreContext): number {
  const { a, b, catalog, settings, config, values, outDir, out } = ctx;
  if (!values.gold || !values.run) return out("score 에는 --gold <정답 파일> 과 --run <run 의 출력 디렉터리> 가 필요하다"), 2;
  const gold = loadGold(resolveInputPath(values.gold));
  const problems = checkGold(gold, catalog, { a: a.extraction, b: b.extraction });
  if (problems.length > 0) throw new ReconcileFileError(`정답이 문서·지표 목록과 맞지 않는다\n${problems.join("\n")}`);
  const runDir = resolveOutputPath(values.run);
  const saved = readJsonFile(join(runDir, "result.json"), "실행 결과") as { synthetic?: string; run?: RunMeta; findings: Finding[] };
  if (!saved.run?.units) throw new ReconcileFileError(`${join(runDir, "result.json")}: 요청 단위의 결과를 남긴 실행이 아니다 (실제 공급사 실행만 채점한다)`);
  const { run } = saved;
  const model = resolveRoute(config, run.route)[0];
  if (!model) throw new ReconcileFileError(`경로 ${run.route} 에 모델이 없다`);
  const store = new JsonlUnitStore<LabelAnswer>(run.units as string, labelAnswerSchema);
  const pricing = model.pricing;
  const dry = new LlmClient({ config, providers: { mock: new MockProvider(), anthropic: new AnthropicProvider(), openai: new OpenAIProvider(), gemini: new GeminiProvider() }, prompts, log: { write: () => undefined } });
  const estimate = { units: 0, targets: 0, inputTokens: 0, outputTokens: 0, costUsd: 0 };
  const calls: { unitId: string; callId: string; targets: number }[] = [];
  const docOf = (input: LoadedInput): DocAnswers => {
    const units = planAll([input], settings, run.includeSentences);
    const request = (unit: LabelUnit) => labelRequest(unit, catalog, { route: run.route, tags: LIVE_TAGS, settings, modelMaxOutputTokens: run.modelMaxOutputTokens });
    const found = answersFrom(store, units, request);
    calls.push(...found.calls);
    for (const unit of units) {
      if (!found.answers.has(unitKey(unit))) continue;
      const output = unit.targetFactIds.length * settings.outputTokensPerLabel + settings.outputTokensOverhead;
      const input = dry.dryRun(request(unit)).estimate.inputTokens;
      estimate.units += 1;
      estimate.targets += unit.targetFactIds.length;
      estimate.inputTokens += input;
      estimate.outputTokens += output;
      estimate.costUsd += computeCostUsd({ inputTokens: input, outputTokens: output, cacheReadTokens: 0, cacheWriteTokens: 0 }, pricing);
    }
    // 채점은 문서마다 따로 하므로 요청 단위 ID 로 다시 묶는다.
    return { extraction: input.extraction, units, answers: new Map(units.flatMap((unit): [string, UnitAnswer][] => { const answer = found.answers.get(unitKey(unit)); return answer ? [[unit.id, answer as UnitAnswer]] : []; })) };
  };
  const report = buildScoreReport(gold, catalog, { a: docOf(a), b: docOf(b) }, saved.findings);
  const usage = analyzeUsage(readCallLog(run.log), calls, pricing);
  const actual = usage.units.reduce((sum, unit) => ({ inputTokens: sum.inputTokens + unit.inputTokens, outputTokens: sum.outputTokens + unit.outputTokens, costUsd: sum.costUsd + unit.costUsd }), { inputTokens: 0, outputTokens: 0, costUsd: 0 });
  const versus = { note: "답을 받은 요청 단위만 견준다. 어림은 dry-run 과 같은 방식(글자 수 어림, 답 하나 outputTokensPerLabel 토큰, 캐시 할인 없음)이고, 실제는 마지막 시도의 토큰과 모든 시도의 비용이다.", estimate: { ...estimate, costUsd: roundUsd(estimate.costUsd) }, actual: { ...actual, costUsd: roundUsd(actual.costUsd) } };

  let transcribe: { run: TranscribeMeta; report: TranscribeReport; usage: UsageReport } | null = null;
  if (values.transcribe) {
    const dir = resolveOutputPath(values.transcribe);
    const meta = (readJsonFile(join(dir, "transcribe.json"), "옮겨 쓰기 결과") as { run: TranscribeMeta }).run;
    const tStore = new JsonlUnitStore<TranscribeAnswer>(meta.units as string, transcribeAnswerSchema);
    const wanted = new Set(meta.tables);
    const units = planAll([a, b], settings, false).filter((unit) => wanted.has(`${unit.docId}:${unit.gridId}`));
    const found = answersFrom(tStore, units, (unit) => transcribeRequest(unit, { route: meta.route, tags: TRANSCRIBE_TAGS, modelMaxOutputTokens: meta.modelMaxOutputTokens }));
    const tModel = resolveRoute(config, meta.route)[0];
    transcribe = { run: meta, report: transcribeReport(units, found.answers), usage: analyzeUsage(readCallLog(meta.log), found.calls, tModel?.pricing) };
  }

  mkdirSync(outDir, { recursive: true });
  const header = { measured: saved.synthetic ? "모의 실행" : `실제 공급사 실행 (${run.provider}, 경로 ${run.route}, 모델 ${run.model})`, gold: { path: gold.path, sha256: gold.sha256, status: gold.status }, catalog: { id: catalog.id, synthetic: catalog.synthetic }, run: runDir };
  writeJson(join(outDir, "score.json"), { ...header, score: report, usage, dryRunVersusActual: versus, ...(transcribe ? { transcribe } : {}) });
  const md = [
    renderScore(report),
    `측정: ${header.measured}. 지표 목록 ${catalog.id}${catalog.synthetic ? " (synthetic)" : ""}. 정답 파일 sha256 ${gold.sha256}.`,
    "",
    renderUsage("5. 실제 토큰과 비용 (뜻 붙이기)", usage),
    `- dry-run 방식의 어림과 실제 (답을 받은 요청 ${estimate.units}개, 대상 ${estimate.targets}개): 입력 토큰 어림 ${estimate.inputTokens} 대 실제 ${actual.inputTokens}, 출력 토큰 어림 ${estimate.outputTokens} 대 실제 ${actual.outputTokens}, 비용 어림 ${roundUsd(estimate.costUsd)} 대 실제 ${roundUsd(actual.costUsd)} USD`,
    "",
    ...(transcribe ? [renderTranscribe(transcribe.report, `측정용 시험이다 (${transcribe.run.provider}, 모델 ${transcribe.run.model}). 입력은 뜻 붙이기와 같은 표(셀 ID 가 붙은 격자)이고, 원문은 요청에 실린 칸의 글자(텍스트 계층에서 코드가 읽은 것)다.`), renderUsage("옮겨 쓰기 시험의 토큰과 비용", transcribe.usage)] : []),
  ].join("\n");
  writeFileSync(join(outDir, "score.md"), md);
  out(md);
  if (!existsSync(run.log)) out(`호출 기록이 없다: ${run.log}`);
  return 0;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main(process.argv.slice(2)).then(
    (code) => {
      process.exitCode = code;
    },
    (error: unknown) => {
      console.error(error);
      process.exitCode = 1;
    },
  );
}
