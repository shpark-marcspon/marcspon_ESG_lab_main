import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { z } from "zod";
import { AnthropicProvider, BudgetGuard, FilePromptStore, GeminiProvider, JsonlFileSink, LiveSetupError, LlmClient, MockProvider, OpenAIProvider, computeCostUsd, defaultConfigPaths, liveProviders, loadConfig, requireLimitUsd, resolveRoute, roundUsd, type CallRequest, type LiveDeps, type ModelConfig } from "llm";
import { ReconcileFileError, documentSchema, loadUnits, promptRoot, readCallLog, readJsonFile, resolveInputPath, resolveOutputPath } from "../adapters/files.js";
import { JsonlUnitStore, seedBudgetFromLog } from "../adapters/unitStore.js";
import { extractDocument, type DocModel, type Extraction } from "../domain/facts.js";
import { PROMPT_G_CELLS, TallyError, judgeTableCells, pairCellsInTablesSchema, renderCellPairRequest, unmaskedValues, valueCellCount, type CellJudgement, type TablePair } from "./hybrid.js";
import { pageTexts, pairTablesSchema, type DocContext, type PairTablesAnswer, type Verdict } from "./methods.js";
import { PROMPT_REVIEW_CELLS_2, judgeCellReview2, judgePlacePairs, pairPlacesSchema, reviewCells2Schema, type CellReview2Judgement, type PairPlacesAnswer } from "./prose.js";
import { MAX_NEW_TABLE_PAIRS, PAIR_ANSWER_TOKENS, SHIFT_RULE, acceptNewTablePairs, checkShifts, nominate, planCellRequests, recoverOutcome, renderShortTableReview, suspectKeys, type CellRequestPlan, type NominationResult, type ShiftCheck, type ShiftRule } from "./recover.js";
import { conservativeTokens } from "./render.js";
import { PROMPT_REVIEW_TABLES, renderCellReviewRequest, unmaskedInCellReview, unmaskedInTableReview } from "./review.js";

// 밴드 Y (MARC-TICK-28) 의 명령: 열 밀림 검사와 지목(check, 호출 0), 방법 R 의 실행(runr).
// 이 파일은 정답 쌍·심은 오류 명세·판정 기록을 읽지 않는다 (그런 인자를 받지 않는다). 채점과 표는 `cli8.ts` 에 따로 둔다.
// 저장된 D 계열 실행(`--run`)은 읽기만 한다. D 를 다시 부르지 않는다.

type Out = (line: string) => void;

const USAGE = `사용법 (cd ts && pnpm -F reconcile exec tsx src/compare/cli7.ts <명령> ...)
  check --a <문서 A 원본> --b <문서 B 원본> --exam-a <시험지 A> --exam-b <시험지 B> --run <저장된 D 계열 실행의 출력> --out <디렉터리> [--shift-rule either|both]
      저장된 답의 표 칸 짝마다 열 밀림 검사를 붙이고, 다시 물을 표 짝을 지목한다 (호출 0). <out>/check.json 을 쓴다.
  runr  --a .. --b .. --exam-a .. --exam-b .. --run <저장된 실행> --out <디렉터리> [--shift-rule either|both]
        (--dry-run | --provider mock [--mock-answers <요청 ID → 답 JSON>] | --provider openai --route <경로> --limit-usd <상한> --log <밴드 공용 호출 기록> | --replay <실제 실행의 출력> --route <경로> --log <호출 기록>) [--timeout-ms <n>]
      지목된 표 짝마다 그 두 표만 가려서 주고 칸의 짝을 다시 받는다. 어느 짝에도 들지 않은 표는 머리글이 겹치는 후보만 표 목록으로 먼저 묻는다. 값은 코드가 견준다.
      요청 글에 값 후보 숫자가 남거나, 원본으로 조립한 글과 시험지로 조립한 글이 다르면 요청을 만들지 않는다.
--out 은 반드시 준다. 상대 경로는 ts/ 기준이다. 실제 공급사 호출은 돈이 든다. 정답과 명세는 받지 않는다 (채점은 cli8.ts).`;

const TAGS = { tenant: "measure", module: "reconcile-compare" };
const INPUT_GUARD_TOKENS = 250_000;
const CELL_OUTPUT_CAP = 16_000;
const TABLE_OUTPUT_CAP = 16_000;
/** 예상 출력 토큰 (가정. 추론 토큰을 넣어 넉넉히 잡았다): 남은 칸 하나의 답 80, 칸 짝 하나 PAIR_ANSWER_TOKENS + 15, 요청마다 500, 표 검수 800. 밴드 T 의 칸 검수는 남은 칸 45개에 출력 3,190 토큰이었다. */
const EXPECTED = { reviewPerCell: 80, pairPerPair: PAIR_ANSWER_TOKENS + 15, perRequest: 500, tables: 800 } as const;
const TITLE = "R: 저장된 D 계열의 답 + 열 밀림 검사 + 지목된 표 짝을 다시 묻기";

const sha = (path: string): string => createHash("sha256").update(readFileSync(path)).digest("hex");
const writeJson = (path: string, value: unknown): void => writeFileSync(path, `${JSON.stringify(value, null, 1)}\n`);
const key = (pair: { a: string; b: string }): string => `${pair.a}|${pair.b}`;

function readDoc(path: string): DocModel {
  const parsed = documentSchema.safeParse(readJsonFile(path, "문서 JSON"));
  if (!parsed.success) throw new ReconcileFileError(`${path}: 문서 JSON 형식이 맞지 않는다`);
  return parsed.data as DocModel;
}

interface Doc {
  document: DocModel;
  extraction: Extraction;
  context: DocContext;
}

function doc(model: DocModel, docId: "a" | "b"): Doc {
  // 앞 밴드(방법 d2 계열)와 같은 뽑기 옵션. 값 후보의 ID 가 저장된 답의 ID 와 같아야 한다.
  const extraction = extractDocument(model, docId, { titles: "own" });
  const blockText = new Map(model.blocks.filter((block) => block.text !== undefined).map((block) => [block.id, block.text as string]));
  return { document: model, extraction, context: { extraction, pages: pageTexts(extraction, blockText, new Map(model.blocks.map((block) => [block.id, block.page]))) } };
}

const storedLine = z.object({ unitId: z.string(), result: z.object({ kind: z.string(), data: z.unknown().optional() }).loose() }).loose();
const storedOutcome = z.object({ method: z.string(), outcome: z.object({ verdicts: z.array(z.object({ a: z.string(), b: z.string(), outcome: z.enum(["match", "mismatch", "needs_explanation"]), reason: z.string(), valuesDiffer: z.boolean().nullable() })) }).loose() }).loose();

/** 저장된 실행의 원답(요청 조각마다 하나)과 그 실행이 받아들인 짝. 읽기만 한다. */
function readStoredRun(dir: string): { answers: PairPlacesAnswer[]; verdicts: Verdict[]; method: string; hashes: { units: string; outcome: string } } {
  const unitsPath = join(dir, "units.jsonl");
  const outcomePath = join(dir, "outcome.json");
  if (!existsSync(unitsPath) || !existsSync(outcomePath)) throw new ReconcileFileError(`${dir}: 저장된 실행의 units.jsonl 과 outcome.json 이 있어야 한다`);
  const answers: PairPlacesAnswer[] = [];
  for (const [index, line] of readFileSync(unitsPath, "utf8").split("\n").entries()) {
    if (line.trim() === "") continue;
    const parsed = storedLine.safeParse(JSON.parse(line));
    if (!parsed.success) throw new ReconcileFileError(`${unitsPath}:${index + 1}: 저장된 답의 형식이 맞지 않는다`);
    if (parsed.data.result.kind !== "success") continue;
    const answer = pairPlacesSchema.safeParse(parsed.data.result.data);
    if (!answer.success) throw new ReconcileFileError(`${unitsPath}:${index + 1}: 저장된 답이 자리 짝의 스키마와 맞지 않는다`);
    answers.push(answer.data);
  }
  const outcome = storedOutcome.safeParse(readJsonFile(outcomePath, "저장된 실행의 결과"));
  if (!outcome.success) throw new ReconcileFileError(`${outcomePath}: 저장된 실행의 결과 형식이 맞지 않는다`);
  return { answers, verdicts: outcome.data.outcome.verdicts, method: outcome.data.method, hashes: { units: sha(unitsPath), outcome: sha(outcomePath) } };
}

interface Assembled {
  checks: ShiftCheck[];
  nomination: NominationResult;
  kept: Verdict[];
  /** (i)~(iii) 의 요청 계획과 건너뛴 것. */
  first: ReturnType<typeof planCellRequests>;
  /** (iv) 의 표 검수 요청의 글 (후보가 없으면 null). */
  tableText: string | null;
}

/** 답과 문서에서만 조립한다: 검사, 지목, 요청 계획, 표 검수의 글. */
function assemble(docs: { a: Doc; b: Doc }, answers: PairPlacesAnswer[], verdicts: Verdict[], rule: ShiftRule, units: ReturnType<typeof loadUnits>): Assembled {
  const checks = checkShifts(verdicts, docs.a.extraction, docs.b.extraction, units, rule);
  const nomination = nominate({ a: docs.a.extraction, b: docs.b.extraction, answers, verdicts, checks, units });
  const suspects = suspectKeys(checks);
  const kept = verdicts.filter((verdict) => !suspects.has(key(verdict)));
  const first = planCellRequests(docs.a.extraction, docs.b.extraction, nomination.nominations.map((item) => ({ pair: item.pair, reasons: item.reasons })), kept);
  const tableText = renderShortTableReview(docs.a.extraction, docs.b.extraction, nomination.candidates, [...nomination.derived, ...nomination.nominations.map((item) => item.pair)]);
  return { checks, nomination, kept, first, tableText };
}

function cellRequestText(docs: { a: Doc; b: Doc }, plan: CellRequestPlan): { text: string; unmasked: string[] } {
  if (plan.kind === "review" && plan.review) {
    const text = renderCellReviewRequest(docs.a, docs.b, plan.review);
    return { text, unmasked: unmaskedInCellReview(text) };
  }
  const text = renderCellPairRequest(docs.a, docs.b, plan.pair);
  return { text, unmasked: unmaskedValues(text) };
}

const fileName = (id: string): string => id.replace(/[/|#:]/g, "__");

export async function main(argv: string[], out: Out = console.log, log: Out = console.error, deps: LiveDeps = { env: process.env }): Promise<number> {
  const [command, ...rest] = argv;
  let values;
  try {
    values = parseArgs({ args: rest, options: { a: { type: "string" }, b: { type: "string" }, "exam-a": { type: "string" }, "exam-b": { type: "string" }, run: { type: "string" }, out: { type: "string" }, "shift-rule": { type: "string" }, "dry-run": { type: "boolean" }, provider: { type: "string" }, route: { type: "string" }, "limit-usd": { type: "string" }, log: { type: "string" }, "timeout-ms": { type: "string" }, replay: { type: "string" }, "mock-answers": { type: "string" } } }).values;
  } catch (error) {
    out(`${(error as Error).message}\n\n${USAGE}`);
    return 2;
  }
  try {
    if ((command !== "check" && command !== "runr") || !values.a || !values.b || !values["exam-a"] || !values["exam-b"] || !values.run || !values.out) return out(USAGE), 2;
    const rule = (values["shift-rule"] ?? SHIFT_RULE) as ShiftRule;
    if (rule !== "either" && rule !== "both") return out(USAGE), 2;
    const paths = { a: resolveInputPath(values.a), b: resolveInputPath(values.b), examA: resolveInputPath(values["exam-a"]), examB: resolveInputPath(values["exam-b"]), run: resolveInputPath(values.run) };
    const outDir = resolveOutputPath(values.out);
    if (outDir === paths.run || outDir.startsWith(`${paths.run}/`)) throw new ReconcileFileError("--out 이 저장된 실행의 폴더 안이다. 앞 밴드의 산출물은 읽기만 한다");
    const original = { a: doc(readDoc(paths.a), "a"), b: doc(readDoc(paths.b), "b") };
    const exam = { a: doc(readDoc(paths.examA), "a"), b: doc(readDoc(paths.examB), "b") };
    const units = loadUnits();
    const stored = readStoredRun(paths.run);

    // 저장된 원답을 그 방법과 같은 확인에 다시 넣는다. 그 실행이 받아들인 짝과 다르면 멈춘다.
    const judged = judgePlacePairs(stored.answers, exam.a.context, exam.b.context, units);
    if (JSON.stringify(judged.verdicts) !== JSON.stringify(stored.verdicts)) throw new ReconcileFileError(`${paths.run}: 저장된 원답을 다시 확인한 결과가 그 실행의 outcome.json 과 다르다. 멈춘다`);

    // 검사·지목·요청 계획: 그 실행의 답과 문서에서만. 원본에서 낸 것과 시험지에서 낸 것이 같아야 한다 (심은 값이 지목에 새지 않는다).
    const plan = assemble(exam, stored.answers, judged.verdicts, rule, units);
    const fromOriginal = assemble(original, stored.answers, judged.verdicts, rule, units);
    const sign = (item: Assembled): string => JSON.stringify([item.checks, item.nomination, item.first, item.tableText]);
    if (sign(plan) !== sign(fromOriginal)) throw new ReconcileFileError("원본에서 낸 검사·지목과 시험지에서 낸 검사·지목이 다르다 (심은 값이 지목에 샌다)");
    const count = (list: ShiftCheck[]): Record<string, number> => {
      const bag: Record<string, number> = {};
      for (const check of list) bag[`${check.verdict} (열 ${check.col} / 행 ${check.row})`] = (bag[`${check.verdict} (열 ${check.col} / 행 ${check.row})`] ?? 0) + 1;
      return bag;
    };
    const { nomination } = plan;
    const brief = (item: CellRequestPlan): Record<string, unknown> => ({ id: item.id, pair: item.pair, reasons: item.reasons, kind: item.kind, remaining: item.remaining, asked: item.review ? item.review.remaining.length : null, alreadyPaired: item.review ? item.review.paired.length : 0 });
    const checkReport = {
      run: values.run,
      runMethod: stored.method,
      shiftRule: rule,
      inputs: { units: stored.hashes.units, outcome: stored.hashes.outcome, examA: sha(paths.examA), examB: sha(paths.examB) },
      accepted: judged.verdicts.length,
      rejected: judged.rejected,
      cellPairs: plan.checks.length,
      shift: { ok: plan.checks.filter((check) => check.verdict === "ok").length, suspect: plan.checks.filter((check) => check.verdict === "suspect").length, undecided: plan.checks.filter((check) => check.verdict === "undecided").length, byAxis: count(plan.checks) },
      suspects: plan.checks.filter((check) => check.verdict === "suspect"),
      derivedTablePairs: nomination.derived,
      nominations: nomination.nominations,
      unpairedTables: { a: nomination.unpaired.a.length, b: nomination.unpaired.b.length },
      candidates: nomination.candidates,
      requests: plan.first.plans.map(brief),
      skipped: plan.first.skipped,
      tableReview: plan.tableText === null ? null : { tables: { a: [...new Set(nomination.candidates.map((item) => item.a))], b: [...new Set(nomination.candidates.map((item) => item.b))] } },
    };

    if (command === "check") {
      mkdirSync(outDir, { recursive: true });
      writeJson(join(outDir, "check.json"), { note: "열 밀림 검사와 지목 (호출 0). 그 실행의 답과 문서 모델에서만 냈다. 정답을 읽지 않았다.", ...checkReport, checks: plan.checks });
      out(JSON.stringify({ ...checkReport, suspects: checkReport.suspects.length }, null, 1));
      return 0;
    }

    // ── runr ───────────────────────────────────────────────────────────────
    const replayDir = values.replay ? resolveOutputPath(values.replay) : null;
    if ([values.provider !== undefined, values["dry-run"] === true, replayDir !== null].filter(Boolean).length !== 1) return out(USAGE), 2;
    const config = loadConfig(defaultConfigPaths({ synthetic: true }));
    const prompts = new FilePromptStore(promptRoot);
    const live = values.provider !== undefined && values.provider !== "mock";
    const route = live || values["dry-run"] || replayDir ? (values.route ?? "light-openai") : "mock";
    const storeDir = replayDir ?? outDir;
    const model = resolveRoute(config, route)[0] as ModelConfig | undefined;
    if (!model) throw new ReconcileFileError(`경로 ${route} 에 모델이 없다`);
    const timeoutMs = values["timeout-ms"] === undefined ? undefined : Number(values["timeout-ms"]);
    mkdirSync(outDir, { recursive: true });
    const cap = (wanted: number): number => Math.min(wanted, model.maxOutputTokens);
    const base = { route, tags: TAGS, ...(timeoutMs === undefined ? {} : { timeoutMs }) };

    // 요청 조립과 확인 (호출 전에 한다): 값 후보가 남지 않는다, 원본으로 조립한 글과 글자까지 같다, 입력과 예상 출력이 한도 안이다.
    const dry = new LlmClient({ config, providers: { mock: new MockProvider(), anthropic: new AnthropicProvider(), openai: new OpenAIProvider(), gemini: new GeminiProvider() }, prompts, log: { write: () => undefined } });
    const estimate = { calls: 0, inputTokens: 0, maxInputTokensPerCall: 0, expectedOutputTokens: 0, noCacheUsd: 0, costUpperBoundUsd: 0 };
    const perRequest: Record<string, unknown>[] = [];
    const account = (id: string, call: CallRequest<unknown>, expectedOutput: number, outputCap: number, assumed: boolean): void => {
      const report = dry.dryRun(call);
      const input = Math.max(conservativeTokens(JSON.stringify(call.vars)), report.estimate.inputTokens);
      if (input > INPUT_GUARD_TOKENS) throw new ReconcileFileError(`${id}: 입력 어림 ${input} 토큰이 한도 ${INPUT_GUARD_TOKENS} 을 넘는다. 요청을 만들지 않는다`);
      if (values.provider !== "mock" && expectedOutput > outputCap * 0.75) throw new ReconcileFileError(`${id}: 예상 출력 ${expectedOutput} 토큰이 출력 상한의 3/4 을 넘는다. 요청을 만들지 않는다`);
      estimate.calls += 1;
      estimate.inputTokens += input;
      estimate.maxInputTokensPerCall = Math.max(estimate.maxInputTokensPerCall, input);
      estimate.expectedOutputTokens += expectedOutput;
      estimate.noCacheUsd += computeCostUsd({ inputTokens: input, outputTokens: expectedOutput, cacheReadTokens: 0, cacheWriteTokens: 0 }, model.pricing);
      estimate.costUpperBoundUsd += computeCostUsd({ inputTokens: input, outputTokens: report.estimate.maxOutputTokens, cacheReadTokens: 0, cacheWriteTokens: 0 }, model.pricing);
      perRequest.push({ id, inputTokensEstimate: input, expectedOutputTokens: expectedOutput, ...(assumed ? { assumed: "표 검수가 이 후보를 낸다는 가정" } : {}) });
    };
    const tableCall = (text: string) => ({ ...base, prompt: PROMPT_REVIEW_TABLES, vars: { tables: text }, schema: pairTablesSchema, schemaName: "reconcile_review_tables", maxOutputTokens: cap(TABLE_OUTPUT_CAP) });
    const cellCall = (item: CellRequestPlan, text: string) =>
      item.kind === "review"
        ? { ...base, prompt: PROMPT_REVIEW_CELLS_2, vars: { tables: text }, schema: reviewCells2Schema, schemaName: "reconcile_review_cells_2", maxOutputTokens: cap(CELL_OUTPUT_CAP) }
        : { ...base, prompt: PROMPT_G_CELLS, vars: { tables: text }, schema: pairCellsInTablesSchema, schemaName: "reconcile_pair_cells_in_tables", maxOutputTokens: cap(CELL_OUTPUT_CAP) };
    const expectedOf = (item: CellRequestPlan): number => (item.kind === "review" ? (item.review?.remaining.length ?? 0) * EXPECTED.reviewPerCell : Math.min(valueCellCount(exam.a.extraction, { id: item.pair.a }), valueCellCount(exam.b.extraction, { id: item.pair.b })) * EXPECTED.pairPerPair) + EXPECTED.perRequest;
    const texts = new Map<string, string>();
    const prepare = (items: CellRequestPlan[], assumed: boolean): void => {
      const fromOriginalPlans = new Map(planCellRequests(original.a.extraction, original.b.extraction, items.map((item) => ({ pair: item.pair, reasons: item.reasons })), plan.kept).plans.map((item) => [item.id, item]));
      for (const item of items) {
        const built = cellRequestText(exam, item);
        if (built.unmasked.length > 0) throw new ReconcileFileError(`${item.id}: 요청 글에 가리지 못한 값 후보 숫자가 있다 (${built.unmasked.slice(0, 20).join(", ")}). 요청을 만들지 않는다`);
        const other = fromOriginalPlans.get(item.id);
        if (!other || cellRequestText(original, other).text !== built.text) throw new ReconcileFileError(`${item.id}: 원본으로 조립한 요청 글과 시험지로 조립한 요청 글이 다르다 (심은 값이 요청 글에 샌다)`);
        texts.set(item.id, built.text);
        account(item.id, cellCall(item, built.text), expectedOf(item), cap(CELL_OUTPUT_CAP), assumed);
      }
    };
    if (plan.tableText !== null) {
      const left = unmaskedInTableReview(plan.tableText);
      if (left.length > 0) throw new ReconcileFileError(`r/tables: 표 검수 요청 글에 가리지 못한 값 후보 숫자가 있다 (${left.slice(0, 20).join(", ")}). 요청을 만들지 않는다`);
      account("r/tables", tableCall(plan.tableText), EXPECTED.tables, cap(TABLE_OUTPUT_CAP), false);
    }
    prepare(plan.first.plans, false);
    const existing = [...nomination.derived, ...nomination.nominations.map((item) => item.pair)];
    const maskCheck = { requests: 0, unmaskedValues: 0, sameAsOriginalDocuments: true };

    if (values["dry-run"]) {
      // 표 검수의 답은 호출해야 안다. 어림에는 「후보를 머리글이 많이 겹치는 것부터 한도까지 전부 낸다」는 가정을 넣는다.
      const assumedPairs: TablePair[] = nomination.candidates.slice(0, MAX_NEW_TABLE_PAIRS).map((item) => ({ a: item.a, b: item.b }));
      const assumed = planCellRequests(exam.a.extraction, exam.b.extraction, assumedPairs.map((pair) => ({ pair, reasons: ["new_table_pair" as const] })), plan.kept);
      prepare(assumed.plans, true);
      const requestsDir = join(outDir, "requests");
      mkdirSync(requestsDir, { recursive: true });
      if (plan.tableText !== null) writeFileSync(join(requestsDir, "r__tables.txt"), `${plan.tableText}\n`);
      for (const [id, text] of texts) writeFileSync(join(requestsDir, `${fileName(id)}.txt`), `${text}\n`);
      maskCheck.requests = texts.size + (plan.tableText === null ? 0 : 1);
      const report = { dryRun: true, method: "r", route, model: model.key, note: "공급사를 부르지 않았다. 입력 토큰은 보수적인 글자 수 어림(한글 1자 1토큰, 그 밖 1자 0.6토큰)과 호출 계층의 어림 중 큰 값이다. 예상 출력은 가정이다. noCacheUsd 는 캐시 할인 없는 값이다 (캐시에 기대지 않는다). 비용 상한은 요청마다 출력 상한을 다 쓸 때의 값이고 재시도는 들어 있지 않다.", ...checkReport, suspects: checkReport.suspects.length, expectedAssumptions: EXPECTED, ...estimate, noCacheUsd: roundUsd(estimate.noCacheUsd), costUpperBoundUsd: roundUsd(estimate.costUpperBoundUsd), maskCheck, perRequest };
      writeJson(join(outDir, "dry-run.json"), report);
      out(`[r] ${values.run} · 모델 ${model.key} · 지목 ${nomination.nominations.length} · 후보 표 짝 ${nomination.candidates.length} · 호출 ${estimate.calls} · 입력 어림 ${estimate.inputTokens} (요청 최대 ${estimate.maxInputTokensPerCall}) · 예상 출력 ${estimate.expectedOutputTokens} · 캐시 없이 ${report.noCacheUsd} USD · 비용 상한 ${report.costUpperBoundUsd} USD`);
      return 0;
    }

    const mock = new MockProvider();
    let client: LlmClient | null = null;
    let budget: BudgetGuard | null = null;
    const logPath = values.log ? resolveOutputPath(values.log) : join(outDir, "calls.jsonl");
    if (live) {
      try {
        if (!values.route || !config.routes[values.route]) throw new LiveSetupError("실제 공급사 호출에는 설정에 있는 --route <경로> 가 필요하다");
        const limitUsd = requireLimitUsd(values["limit-usd"]);
        if (!values.log) throw new LiveSetupError("실제 공급사 호출에는 --log <밴드 공용 호출 기록> 이 필요하다 (앞선 실행이 쓴 비용을 상한에 같이 센다)");
        budget = new BudgetGuard([{ scope: { module: TAGS.module }, limitUsd }]);
        const seeded = seedBudgetFromLog(logPath, budget, TAGS);
        log(`상한 ${limitUsd} USD, 앞선 호출 기록 ${seeded.lines}줄 (${roundUsd(seeded.spentUsd)} USD 를 상한에 넣는다)`);
        client = new LlmClient({ config, providers: liveProviders(config, { provider: values.provider as string, route: values.route }, deps), prompts, log: new JsonlFileSink(logPath), storeBodies: true, budget, ...(deps.sleep ? { sleep: deps.sleep } : {}) });
      } catch (error) {
        if (!(error instanceof LiveSetupError)) throw error;
        out(`실제 공급사 호출을 지원하지 않는다: ${error.message}`);
        return 2;
      }
    } else if (values.provider === "mock") {
      writeFileSync(logPath, "");
      client = new LlmClient({ config, providers: { mock }, prompts, log: new JsonlFileSink(logPath) });
    }
    // 모의 실행의 답: 기본은 빈 답이고, --mock-answers 가 있으면 요청 ID 마다 그 답을 돌려준다 (배선 확인용. 결과에는 synthetic 표시가 붙는다).
    if (values["mock-answers"] !== undefined && values.provider !== "mock") return out(USAGE), 2;
    const mockAnswers = values["mock-answers"] === undefined ? {} : (readJsonFile(resolveInputPath(values["mock-answers"]), "모의 답") as Record<string, unknown>);
    const failures: { id: string; kind: string }[] = [];
    const callIds: string[] = [];
    const ask = async <T>(store: JsonlUnitStore<T>, id: string, call: CallRequest<T>, mockText: string): Promise<T | null> => {
      let result = store.get(id, call);
      if (!result) {
        if (!client) throw new ReconcileFileError(`${id}: 저장된 답에 없는 요청이다 (요청 글이 실측 때와 다르다). 다시 채점할 때는 모델을 부르지 않는다`);
        if (!live) mock.enqueueText(id in mockAnswers ? JSON.stringify(mockAnswers[id]) : mockText);
        result = await client.call(call);
        store.put(id, call, result);
        log(`[r] ${id}: ${result.kind} (${result.meta.totalCostUsd} USD)`);
      }
      callIds.push(result.meta.callId);
      if (result.kind !== "success") failures.push({ id, kind: result.kind });
      return result.kind === "success" ? result.data : null;
    };

    // (iv) 표 검수 → 새 표 짝 → 그 칸의 요청 계획.
    let tableAnswer: PairTablesAnswer | null = null;
    if (plan.tableText !== null) tableAnswer = await ask(new JsonlUnitStore(join(storeDir, "tables.jsonl"), pairTablesSchema), "r/tables", tableCall(plan.tableText), JSON.stringify({ table_pairs: [] }));
    const accepted = acceptNewTablePairs(tableAnswer, nomination.candidates, existing, nomination.unpaired);
    const second = planCellRequests(exam.a.extraction, exam.b.extraction, accepted.pairs.map((pair) => ({ pair, reasons: ["new_table_pair" as const] })), plan.kept);
    prepare(second.plans, false);
    const plans = [...plan.first.plans, ...second.plans];
    maskCheck.requests = plans.length + (plan.tableText === null ? 0 : 1);

    const reviewStore = new JsonlUnitStore(join(storeDir, "review-cells.jsonl"), reviewCells2Schema);
    const pairStore = new JsonlUnitStore(join(storeDir, "pair-cells.jsonl"), pairCellsInTablesSchema);
    const reviews = new Map<string, CellReview2Judgement>();
    const pairs = new Map<string, CellJudgement>();
    for (const item of plans) {
      const text = texts.get(item.id) as string;
      if (item.kind === "review" && item.review) {
        const answer = await ask(reviewStore, item.id, { ...base, prompt: PROMPT_REVIEW_CELLS_2, vars: { tables: text }, schema: reviewCells2Schema, schemaName: "reconcile_review_cells_2", maxOutputTokens: cap(CELL_OUTPUT_CAP) }, JSON.stringify({ answers: [] }));
        reviews.set(item.id, judgeCellReview2(answer, item.review, exam.a.context, exam.b.context, units));
      } else {
        const answer = await ask(pairStore, item.id, { ...base, prompt: PROMPT_G_CELLS, vars: { tables: text }, schema: pairCellsInTablesSchema, schemaName: "reconcile_pair_cells_in_tables", maxOutputTokens: cap(CELL_OUTPUT_CAP) }, JSON.stringify({ pairs: [] }));
        if (answer) pairs.set(item.id, judgeTableCells(answer, item.pair, exam.a.context, exam.b.context, units));
      }
    }
    const result = recoverOutcome({ a: exam.a.extraction, b: exam.b.extraction, units, original: { verdicts: judged.verdicts, rejectedPlaces: judged.rejectedPlaces }, plans, rule, reviews, pairs });

    const records = readCallLog(logPath).filter((record) => callIds.includes(record.callId));
    const byResultKind: Record<string, number> = {};
    for (const record of records) byResultKind[record.resultKind] = (byResultKind[record.resultKind] ?? 0) + 1;
    const total = (record: (typeof records)[number]): number => (record.inputTokens ?? 0) + (record.cacheReadTokens ?? 0) + (record.cacheWriteTokens ?? 0);
    const cost = { calls: records.length, inputTokens: records.reduce((sum, record) => sum + total(record), 0), cacheReadTokens: records.reduce((sum, record) => sum + (record.cacheReadTokens ?? 0), 0), outputTokens: records.reduce((sum, record) => sum + (record.outputTokens ?? 0), 0), costUsd: roundUsd(records.reduce((sum, record) => sum + (record.costUsd ?? 0), 0)), maxInputTokens: records.reduce((max, record) => Math.max(max, total(record)), 0), byResultKind };
    const synthetic = values.provider === "mock" ? "모의 실행 (정해 둔 답이나 빈 답을 돌려주는 synthetic 응답기). 결과는 모델의 성능과 무관하다." : null;
    const perPlan = plans.map((item) => {
      const review = reviews.get(item.id);
      const states: Record<string, number> = {};
      for (const state of review?.cells.values() ?? []) states[state.state === "no_counterpart" ? `no_counterpart:${state.reason}` : state.state] = (states[state.state === "no_counterpart" ? `no_counterpart:${state.reason}` : state.state] ?? 0) + 1;
      return { ...brief(item), answered: item.kind === "review" ? states : null, pairs: item.kind === "review" ? (review?.newPairs.length ?? 0) : (pairs.get(item.id)?.verdicts.length ?? 0), rejected: item.kind === "review" ? (review?.rejected ?? {}) : (pairs.get(item.id)?.rejected ?? {}) };
    });
    const answers = {
      ...(synthetic ? { synthetic } : {}),
      method: "r",
      run: values.run,
      shiftRule: rule,
      nominations: nomination.nominations,
      candidates: nomination.candidates,
      skipped: [...plan.first.skipped, ...second.skipped],
      tableReview: plan.tableText === null ? null : { answered: tableAnswer?.table_pairs ?? null, accepted: accepted.pairs, rejected: accepted.rejected },
      requests: perPlan,
      rejected: result.rejected,
      before: result.before,
      checked: result.checked,
      after: { verdicts: result.after.verdicts, tally: result.after.tally },
      added: result.merge.added,
      dropped: result.merge.dropped,
      repeated: result.merge.repeated,
      conflicts: result.merge.conflicts,
      recoveredChecks: result.recoveredChecks,
      stillExpected: result.stillExpected,
    };
    writeJson(join(outDir, "outcome.json"), { ...answers, title: TITLE, provider: values.provider ?? null, ...(replayDir ? { replayedFrom: replayDir } : {}), route, model: model.key, prompts: [PROMPT_REVIEW_TABLES, PROMPT_REVIEW_CELLS_2, PROMPT_G_CELLS], inputs: checkReport.inputs, maskCheck, failures, cost, states: { a: Object.fromEntries(result.after.states.a), b: Object.fromEntries(result.after.states.b) } });
    // 다시 채점했을 때 견줄 것: 답에서 나온 것만 (실행 방식과 비용 기록은 뺀다).
    writeJson(join(outDir, "answers.json"), answers);
    const kinds = (verdicts: Verdict[]): Record<string, number> => ({ match: verdicts.filter((verdict) => verdict.outcome === "match").length, mismatch: verdicts.filter((verdict) => verdict.outcome === "mismatch").length, needsExplanation: verdicts.filter((verdict) => verdict.outcome === "needs_explanation").length });
    out(JSON.stringify({ ...(synthetic ? { synthetic } : {}), method: "r", run: values.run, failures, cost, spentUsd: budget ? budget.spent({ module: TAGS.module }) : null, nominations: nomination.nominations.length, requests: perPlan.length, tableReview: answers.tableReview, before: kinds(result.before.verdicts), checked: kinds(result.checked.verdicts), after: kinds(result.after.verdicts), added: result.merge.added.length, dropped: result.merge.dropped.length, conflicts: result.merge.conflicts.length, stillExpected: result.stillExpected, rejected: result.rejected, files: { outcome: join(outDir, "outcome.json") } }, null, 1));
    return failures.length > 0 ? 3 : 0;
  } catch (error) {
    if (error instanceof ReconcileFileError || error instanceof TallyError) {
      log(error.message);
      out(error.message);
      return 1;
    }
    throw error;
  }
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
