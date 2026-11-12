import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { z } from "zod";
import { AnthropicProvider, BudgetGuard, FilePromptStore, GeminiProvider, JsonlFileSink, LiveSetupError, LlmClient, MockProvider, OpenAIProvider, computeCostUsd, defaultConfigPaths, liveProviders, loadConfig, requireLimitUsd, resolveRoute, roundUsd, type LiveDeps, type ModelConfig } from "llm";
import { ReconcileFileError, documentSchema, loadUnits, promptRoot, readCallLog, readJsonFile, resolveInputPath, resolveOutputPath } from "../adapters/files.js";
import { JsonlUnitStore, seedBudgetFromLog } from "../adapters/unitStore.js";
import { extractDocument, type DocModel } from "../domain/facts.js";
import { ASK_ANSWER_TOKENS, MAX_TARGETS_PER_REQUEST, PROMPT_Q, askPlacesSchema, buildAskRequest, combineAsk, judgeAsk, listTargets, planAsks, renderOther, tallyAsk, targetKey, type AskDoc, type AskJudgement, type AskPlan, type Target } from "./ask.js";
import { TallyError } from "./hybrid.js";
import { pageTexts, type DocContext } from "./methods.js";
import { conservativeTokens } from "./render.js";

// 밴드 X (MARC-TICK-27) 의 명령: 대상 목록(targets)과 방법 Q 의 실행(runq).
// 이 파일은 정답 쌍·심은 오류 명세·판정 기록을 읽지 않는다 (그런 인자를 받지 않는다). 채점과 표는 `cli6.ts` 에 따로 둔다.

type Out = (line: string) => void;

const USAGE = `사용법 (cd ts && pnpm -F reconcile exec tsx src/compare/cli5.ts <명령> ...)
  targets --a <문서 A 원본> --b <문서 B 원본> --exam-a <시험지 A> --exam-b <시험지 B> --chart-labels <코드 라벨> --out <디렉터리>
      방법 Q 의 대상 목록을 낸다 (호출 0). 요약 숫자(이름이 묶인 숫자뿐인 블록), 도표의 값(코드 라벨이 실리는 값 후보), 문장 속 숫자(종류 옵션이 「값」으로 본 것).
      <out>/targets.json 과 요청 계획 <out>/plans.json 을 쓴다. 원본과 시험지에서 낸 목록이 다르면 멈춘다.
  runq    --a .. --b .. --exam-a .. --exam-b .. --chart-labels .. --out <디렉터리>
          (--dry-run | --provider mock | --provider openai --route <경로> --limit-usd <상한> --log <밴드 공용 호출 기록> | --replay <실제 실행의 출력> --route <경로> --log <호출 기록>) [--timeout-ms <n>]
      대상을 요청으로 묶어, 요청마다 상대 문서 전체(앞) + 대상이 든 쪽 + 대상 목록(뒤)을 주고 대상마다 상대 자리의 ID 또는 「없음」과 사유를 받는다. 값은 코드가 견준다.
      요청 글에 값 후보 숫자가 남거나, 원본으로 조립한 글과 시험지로 조립한 글이 다르면 요청을 만들지 않는다.
--out 은 반드시 준다. 상대 경로는 ts/ 기준이다. 실제 공급사 호출은 돈이 든다. 정답과 명세는 받지 않는다 (채점은 cli6.ts).`;

const TAGS = { tenant: "measure", module: "reconcile-compare" };
/** 요청 하나의 입력 어림(보수적)이 이 값을 넘으면 요청을 만들지 않는다. 장문 단가의 경계는 272,000 토큰이다. */
const INPUT_GUARD_TOKENS = 250_000;
const Q_OUTPUT_CAP = 32_000;
const TITLE = "Q: 낱개 숫자를 자리별로 묻기 (요약 숫자·도표의 값·문장 속 숫자, 상대 문서 전체 + 대상이 든 쪽 + 대상 목록)";

const sha = (path: string): string => createHash("sha256").update(readFileSync(path)).digest("hex");
const writeJson = (path: string, value: unknown): void => writeFileSync(path, `${JSON.stringify(value, null, 1)}\n`);

function readDoc(path: string): DocModel {
  const parsed = documentSchema.safeParse(readJsonFile(path, "문서 JSON"));
  if (!parsed.success) throw new ReconcileFileError(`${path}: 문서 JSON 형식이 맞지 않는다`);
  return parsed.data as DocModel;
}

interface Doc extends AskDoc {
  context: DocContext;
}

function doc(model: DocModel, docId: "a" | "b"): Doc {
  const extraction = extractDocument(model, docId, { titles: "own", kinds: true });
  const blockText = new Map(model.blocks.filter((block) => block.text !== undefined).map((block) => [block.id, block.text as string]));
  return { model, extraction, context: { extraction, pages: pageTexts(extraction, blockText, new Map(model.blocks.map((block) => [block.id, block.page]))) } };
}

export async function main(argv: string[], out: Out = console.log, log: Out = console.error, deps: LiveDeps = { env: process.env }): Promise<number> {
  const [command, ...rest] = argv;
  let values;
  try {
    values = parseArgs({ args: rest, options: { a: { type: "string" }, b: { type: "string" }, "exam-a": { type: "string" }, "exam-b": { type: "string" }, "chart-labels": { type: "string" }, out: { type: "string" }, "dry-run": { type: "boolean" }, provider: { type: "string" }, route: { type: "string" }, "limit-usd": { type: "string" }, log: { type: "string" }, "timeout-ms": { type: "string" }, replay: { type: "string" } } }).values;
  } catch (error) {
    out(`${(error as Error).message}\n\n${USAGE}`);
    return 2;
  }
  try {
    if ((command !== "targets" && command !== "runq") || !values.a || !values.b || !values["exam-a"] || !values["exam-b"] || !values["chart-labels"] || !values.out) return out(USAGE), 2;
    const paths = { a: resolveInputPath(values.a), b: resolveInputPath(values.b), examA: resolveInputPath(values["exam-a"]), examB: resolveInputPath(values["exam-b"]), chartLabels: resolveInputPath(values["chart-labels"]) };
    const original = { a: doc(readDoc(paths.a), "a"), b: doc(readDoc(paths.b), "b") };
    const exam = { a: doc(readDoc(paths.examA), "a"), b: doc(readDoc(paths.examB), "b") };
    const loaded = z.record(z.enum(["a", "b"]), z.record(z.string(), z.string())).safeParse(readJsonFile(paths.chartLabels, "도표 라벨"));
    if (!loaded.success) throw new ReconcileFileError(`${paths.chartLabels}: 도표 라벨 형식이 맞지 않는다`);
    const chartLabels: Record<string, string> = {};
    for (const [docId, labels] of Object.entries(loaded.data)) for (const [id, text] of Object.entries(labels ?? {})) chartLabels[`${docId}:${id}`] = text;
    const outDir = resolveOutputPath(values.out);

    // 대상 목록과 요청 계획: 문서 모델, 종류 옵션, 코드 라벨에서만 낸다. 원본과 시험지에서 낸 것이 같아야 한다 (심은 값이 목록을 바꾸지 않는다).
    const targetsOf = (docs: { a: Doc; b: Doc }): Target[] => [...listTargets(docs.a, "a", chartLabels), ...listTargets(docs.b, "b", chartLabels)];
    const targets = targetsOf(exam);
    if (JSON.stringify(targets) !== JSON.stringify(targetsOf(original))) throw new ReconcileFileError("원본에서 낸 대상 목록과 시험지에서 낸 대상 목록이 다르다 (심은 값이 대상 목록에 샌다)");
    const plans = planAsks(targets);
    const counts = { total: targets.length, byBranch: Object.fromEntries(["summary", "chart", "sentence"].map((branch) => [branch, { a: targets.filter((target) => target.branch === branch && target.doc === "a").length, b: targets.filter((target) => target.branch === branch && target.doc === "b").length }])) };
    const planBrief = (plan: AskPlan): Record<string, unknown> => ({ id: plan.id, branch: plan.branch, source: plan.source, other: plan.other, pages: plan.pages, targets: plan.targets.length });

    if (command === "targets") {
      mkdirSync(outDir, { recursive: true });
      writeJson(join(outDir, "targets.json"), { note: "방법 Q 의 대상 목록 (호출 0). 문서 모델, 종류 옵션, 코드 라벨에서만 냈다. 정답을 읽지 않았다.", maxTargetsPerRequest: MAX_TARGETS_PER_REQUEST, counts, targets });
      writeJson(join(outDir, "plans.json"), plans.map((plan) => ({ ...planBrief(plan), targetIds: plan.targets.map(targetKey) })));
      out(JSON.stringify({ counts, requests: plans.map(planBrief) }, null, 1));
      return 0;
    }

    // ── runq ───────────────────────────────────────────────────────────────
    const replayDir = values.replay ? resolveOutputPath(values.replay) : null;
    if ([values.provider !== undefined, values["dry-run"] === true, replayDir !== null].filter(Boolean).length !== 1) return out(USAGE), 2;
    const units = loadUnits();
    const config = loadConfig(defaultConfigPaths({ synthetic: true }));
    const prompts = new FilePromptStore(promptRoot);
    const live = values.provider !== undefined && values.provider !== "mock";
    const route = live || values["dry-run"] || replayDir ? (values.route ?? "light-openai") : "mock";
    const storeDir = replayDir ?? outDir;
    const model = resolveRoute(config, route)[0] as ModelConfig | undefined;
    if (!model) throw new ReconcileFileError(`경로 ${route} 에 모델이 없다`);
    const timeoutMs = values["timeout-ms"] === undefined ? undefined : Number(values["timeout-ms"]);
    mkdirSync(outDir, { recursive: true });

    // 요청 조립과 확인 (호출 전에 전부 한다): 값 후보가 남지 않는다, 대상이 문맥 글에 있다, 원본으로 조립한 글과 글자까지 같다, 입력이 한도 안이다.
    const otherText = { a: renderOther(exam.a, "a", chartLabels), b: renderOther(exam.b, "b", chartLabels) };
    const otherOriginal = { a: renderOther(original.a, "a", chartLabels), b: renderOther(original.b, "b", chartLabels) };
    const requests = plans.map((plan) => buildAskRequest(plan, exam, chartLabels, otherText[plan.other]));
    const fromOriginal = planAsks(targetsOf(original)).map((plan) => buildAskRequest(plan, original, chartLabels, otherOriginal[plan.other]));
    for (const [index, request] of requests.entries()) {
      if (request.unmasked.length > 0) throw new ReconcileFileError(`${request.plan.id}: 요청 글에 가리지 못한 값 후보 숫자가 있다 (${request.unmasked.slice(0, 20).join(", ")}). 요청을 만들지 않는다`);
      if (request.missingInSource.length > 0) throw new ReconcileFileError(`${request.plan.id}: 대상이 문맥 글에 없다 (${request.missingInSource.slice(0, 20).join(", ")}). 요청을 만들지 않는다`);
      if (JSON.stringify(request.vars) !== JSON.stringify(fromOriginal[index]?.vars)) throw new ReconcileFileError(`${request.plan.id}: 원본으로 조립한 요청 글과 시험지로 조립한 요청 글이 다르다 (심은 값이 요청 글에 샌다)`);
    }
    const asked = requests.flatMap((request) => request.plan.targets.map(targetKey));
    if (asked.length !== targets.length || new Set(asked).size !== asked.length) throw new TallyError(`대상 ${targets.length}개와 요청에 실린 대상 ${asked.length}개가 맞지 않는다`);

    const dry = new LlmClient({ config, providers: { mock: new MockProvider(), anthropic: new AnthropicProvider(), openai: new OpenAIProvider(), gemini: new GeminiProvider() }, prompts, log: { write: () => undefined } });
    const cap = Math.min(Q_OUTPUT_CAP, model.maxOutputTokens);
    const base = { route, tags: TAGS, ...(timeoutMs === undefined ? {} : { timeoutMs }) };
    const callOf = (request: (typeof requests)[number]) => ({ ...base, prompt: PROMPT_Q, vars: request.vars, schema: askPlacesSchema, schemaName: "reconcile_ask_places", maxOutputTokens: cap });
    // 어림 셋: 캐시 없이 / 캐시가 들을 때의 첫 실행(상대 문서가 같은 요청의 둘째부터 앞부분을 캐시로 읽고, 나머지 입력은 캐시 쓰기 단가) / 같은 요청을 되풀이할 때(전부 캐시 읽기). 뒤의 둘은 가정이다.
    const estimate = { calls: 0, inputTokens: 0, maxInputTokensPerCall: 0, expectedOutputTokens: 0, noCacheUsd: 0, firstRunWithCacheUsd: 0, repeatRunWithCacheUsd: 0, costUpperBoundUsd: 0 };
    const perRequest: Record<string, unknown>[] = [];
    let previousOther: string | null = null;
    for (const request of requests) {
      const report = dry.dryRun(callOf(request));
      const input = Math.max(conservativeTokens(JSON.stringify(request.vars)), report.estimate.inputTokens);
      if (input > INPUT_GUARD_TOKENS) throw new ReconcileFileError(`${request.plan.id}: 입력 어림 ${input} 토큰이 한도 ${INPUT_GUARD_TOKENS} 을 넘는다. 요청을 만들지 않는다`);
      const expectedOutput = request.plan.targets.length * ASK_ANSWER_TOKENS + 300;
      if (values.provider !== "mock" && expectedOutput > cap * 0.75) throw new ReconcileFileError(`${request.plan.id}: 예상 출력 ${expectedOutput} 토큰이 출력 상한의 3/4 을 넘는다. 요청을 만들지 않는다`);
      const prefix = Math.min(input, conservativeTokens(request.vars.other));
      const cached = previousOther === request.plan.other ? prefix : 0;
      previousOther = request.plan.other;
      estimate.calls += 1;
      estimate.inputTokens += input;
      estimate.maxInputTokensPerCall = Math.max(estimate.maxInputTokensPerCall, input);
      estimate.expectedOutputTokens += expectedOutput;
      estimate.noCacheUsd += computeCostUsd({ inputTokens: input, outputTokens: expectedOutput, cacheReadTokens: 0, cacheWriteTokens: 0 }, model.pricing);
      estimate.firstRunWithCacheUsd += computeCostUsd({ inputTokens: 0, outputTokens: expectedOutput, cacheReadTokens: cached, cacheWriteTokens: input - cached }, model.pricing);
      estimate.repeatRunWithCacheUsd += computeCostUsd({ inputTokens: 0, outputTokens: expectedOutput, cacheReadTokens: input, cacheWriteTokens: 0 }, model.pricing);
      estimate.costUpperBoundUsd += computeCostUsd({ inputTokens: input, outputTokens: report.estimate.maxOutputTokens, cacheReadTokens: 0, cacheWriteTokens: 0 }, model.pricing);
      perRequest.push({ ...planBrief(request.plan), inputTokensEstimate: input, otherDocumentTokensEstimate: prefix, expectedOutputTokens: expectedOutput });
    }
    const maskCheck = { requests: requests.length, unmaskedValues: 0, targetsMissingInSource: 0, sameAsOriginalDocuments: true, targets: targets.length };

    if (values["dry-run"]) {
      const requestsDir = join(outDir, "requests");
      mkdirSync(requestsDir, { recursive: true });
      for (const request of requests) writeFileSync(join(requestsDir, `${request.plan.id.replace(/[/|#:]/g, "__")}.txt`), `${request.vars.other}\n\n=====\n\n${request.vars.source}\n\n=====\n\n${request.vars.targets}\n`);
      const report = { dryRun: true, method: "q", route, model: model.key, note: "공급사를 부르지 않았다. 입력 토큰은 보수적인 글자 수 어림(한글 1자 1토큰, 그 밖 1자 0.6토큰)과 호출 계층의 어림 중 큰 값이다. 예상 출력은 대상 하나에 ASK_ANSWER_TOKENS 토큰이라는 가정이다. noCacheUsd 는 캐시 할인 없는 값, firstRunWithCacheUsd 와 repeatRunWithCacheUsd 는 앞부분 캐시가 듣는다는 가정의 값이다. 비용 상한은 요청마다 출력 상한을 다 쓸 때의 값이고 재시도는 들어 있지 않다.", inputGuardTokens: INPUT_GUARD_TOKENS, maxTargetsPerRequest: MAX_TARGETS_PER_REQUEST, answerTokensAssumed: ASK_ANSWER_TOKENS, counts, ...estimate, noCacheUsd: roundUsd(estimate.noCacheUsd), firstRunWithCacheUsd: roundUsd(estimate.firstRunWithCacheUsd), repeatRunWithCacheUsd: roundUsd(estimate.repeatRunWithCacheUsd), costUpperBoundUsd: roundUsd(estimate.costUpperBoundUsd), maskCheck, requests: perRequest };
      writeJson(join(outDir, "dry-run.json"), report);
      out(`[q] 모델 ${model.key} · 대상 ${targets.length} · 호출 ${estimate.calls} · 입력 어림 ${estimate.inputTokens} (요청 최대 ${estimate.maxInputTokensPerCall}) · 예상 출력 ${estimate.expectedOutputTokens} · 캐시 없이 ${report.noCacheUsd} USD · 캐시가 들으면 첫 실행 ${report.firstRunWithCacheUsd} USD, 되풀이 ${report.repeatRunWithCacheUsd} USD · 비용 상한 ${report.costUpperBoundUsd} USD`);
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
    const store = new JsonlUnitStore(join(storeDir, "units.jsonl"), askPlacesSchema);
    const failures: { id: string; kind: string }[] = [];
    const callIds: string[] = [];
    const judgements: AskJudgement[] = [];
    for (const request of requests) {
      const call = callOf(request);
      let result = store.get(request.plan.id, call);
      if (!result) {
        if (!client) throw new ReconcileFileError(`${request.plan.id}: 저장된 답에 없는 요청이다 (요청 글이 실측 때와 다르다). 다시 채점할 때는 모델을 부르지 않는다`);
        if (!live) mock.enqueueText(JSON.stringify({ answers: [] }));
        result = await client.call(call);
        store.put(request.plan.id, call, result);
        log(`[q] ${request.plan.id}: ${result.kind} (${result.meta.totalCostUsd} USD)`);
      }
      callIds.push(result.meta.callId);
      if (result.kind !== "success") failures.push({ id: request.plan.id, kind: result.kind });
      judgements.push(judgeAsk(result.kind === "success" ? result.data : null, request.plan, exam.a.context, exam.b.context, units));
    }
    const outcome = combineAsk(judgements);
    const tally = tallyAsk(targets, outcome.states);
    const records = readCallLog(logPath).filter((record) => callIds.includes(record.callId));
    const byResultKind: Record<string, number> = {};
    for (const record of records) byResultKind[record.resultKind] = (byResultKind[record.resultKind] ?? 0) + 1;
    const total = (record: (typeof records)[number]): number => (record.inputTokens ?? 0) + (record.cacheReadTokens ?? 0) + (record.cacheWriteTokens ?? 0);
    const cost = { calls: records.length, inputTokens: records.reduce((sum, record) => sum + total(record), 0), cacheReadTokens: records.reduce((sum, record) => sum + (record.cacheReadTokens ?? 0), 0), outputTokens: records.reduce((sum, record) => sum + (record.outputTokens ?? 0), 0), costUsd: roundUsd(records.reduce((sum, record) => sum + (record.costUsd ?? 0), 0)), maxInputTokens: records.reduce((max, record) => Math.max(max, total(record)), 0), byResultKind };
    const synthetic = values.provider === "mock" ? "모의 실행 (빈 답을 돌려주는 synthetic 응답기). 결과는 모델의 성능과 무관하다." : null;
    const perPlan = judgements.map((judgement) => {
      const states: Record<string, number> = {};
      for (const state of judgement.states.values()) states[state.state === "none" ? `none:${state.reason}` : state.state] = (states[state.state === "none" ? `none:${state.reason}` : state.state] ?? 0) + 1;
      return { ...planBrief(judgement.plan), states, pairs: judgement.verdicts.length, rejected: judgement.rejected };
    });
    writeJson(join(outDir, "outcome.json"), { ...(synthetic ? { synthetic } : {}), method: "q", title: TITLE, provider: values.provider ?? null, ...(replayDir ? { replayedFrom: replayDir } : {}), route, model: model.key, prompt: PROMPT_Q, inputs: { examA: sha(paths.examA), examB: sha(paths.examB), chartLabels: sha(paths.chartLabels) }, counts, maskCheck, requests: perPlan, failures, cost, tally, rejected: outcome.rejected, targets, states: Object.fromEntries(outcome.states), verdicts: outcome.verdicts });
    // 다시 채점했을 때 견줄 것: 답에서 나온 것만 (실행 방식과 비용 기록은 뺀다).
    writeJson(join(outDir, "answers.json"), { ...(synthetic ? { synthetic } : {}), method: "q", tally, rejected: outcome.rejected, states: Object.fromEntries(outcome.states), verdicts: outcome.verdicts });
    out(JSON.stringify({ ...(synthetic ? { synthetic } : {}), method: "q", failures, cost, spentUsd: budget ? budget.spent({ module: TAGS.module }) : null, tally, pairs: { match: outcome.verdicts.filter((verdict) => verdict.outcome === "match").length, mismatch: outcome.verdicts.filter((verdict) => verdict.outcome === "mismatch").length, needsExplanation: outcome.verdicts.filter((verdict) => verdict.outcome === "needs_explanation").length }, rejected: outcome.rejected, files: { outcome: join(outDir, "outcome.json") } }, null, 1));
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
