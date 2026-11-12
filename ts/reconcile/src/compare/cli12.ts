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
import { ASK_ANSWER_TOKENS, PROMPT_Q, askPlacesSchema, judgeAsk, listTargets, targetKey, type AskDoc, type AskState, type Target } from "./ask.js";
import { gold2Schema, plants2Schema } from "./exam2.js";
import { NOMINATE_MODES, PROMPT_F2, askPlaces2Schema, buildFocusRequest, gradeFocus, nominate, pairDocuments, planFocus, splitBasis, statesFromPairs, tallyFocus, type Basis, type Expected, type FocusPlan, type NominateMode } from "./focus.js";
import { TallyError } from "./hybrid.js";
import { pageTexts, type DocContext, type Verdict } from "./methods.js";
import { PROMPT_D2, judgePlacePairs, pairPlacesSchema, unmaskedInIdDocument } from "./prose.js";
import { conservativeTokens } from "./render.js";

// 밴드 CH1 (MARC-TICK-40) 의 명령: 방법 F (도표의 값을 좁혀 묻기)의 지목(nominate), 실행(runf), 채점(tablef).
// nominate 와 runf 는 정답 쌍·심은 오류 명세·판정 기록을 읽지 않는다. 채점은 tablef 에만 있다.

type Out = (line: string) => void;

const USAGE = `사용법 (cd ts && pnpm -F reconcile exec tsx src/compare/cli12.ts <명령> ...)
  nominate --a <문서 A 원본> --b <문서 B 원본> --exam-a <시험지 A> --exam-b <시험지 B> --chart-labels <코드 라벨> --out <디렉터리>
      도표 대상이 든 쪽마다 상대 문서의 볼 만한 쪽을 지목한다 (호출 0). 세 방식(value / names / either)의 지목을 시험지와 원본에서 각각 내 <out>/nominations.json 에 쓴다.
  runf    --a .. --b .. --exam-a .. --exam-b .. --chart-labels .. --nominate <value|names|either> --arm <ask|ask2|pair> [--split-by-name] [--prompt <프롬프트 ID@버전>] --out <디렉터리>
          (--dry-run | --provider mock | --provider openai --route <경로> --limit-usd <상한> --log <밴드 공용 호출 기록> | --replay <실제 실행의 출력> --route <경로> --log <호출 기록>) [--timeout-ms <n>]
      지목된 쪽마다 요청 하나: 상대 문서의 지목된 쪽 + 도표가 든 쪽 + 그 쪽의 도표 대상 전부. ask 는 대상마다 상대 자리 또는 「없음」을 받고(방법 Q 의 프롬프트), ask2 는 상대 자리마다 근거의 세기(clear / likely)를 같이 받고(ask-places@2), pair 는 두 글의 짝을 전부 받는다(방법 D2 의 프롬프트).
      --split-by-name 이면 한 쪽의 대상을 라벨의 이름이 같은 것끼리 나눠 요청을 여럿 만든다. 지목은 시험지에서 낸다. 요청 글에 값 후보 숫자가 남거나, 같은 계획을 원본으로 조립한 글과 다르면 요청을 만들지 않는다.
  tablef  --a .. --b .. --exam-a .. --exam-b .. --chart-labels .. --gold2 <넓힌 정답> --plants2 <넓힌 명세> --aims <그 밖의 기대 자리> --runs <이름=실행 출력,..> --out <디렉터리>
      저장된 실행(outcome.json)에서 기대한 자리마다의 답을 센다 (호출 0).
--out 은 반드시 준다. 상대 경로는 ts/ 기준이다. 실제 공급사 호출은 돈이 든다.`;

const TAGS = { tenant: "measure", module: "reconcile-compare" };
const INPUT_GUARD_TOKENS = 250_000;
const F_OUTPUT_CAP = 32_000;
const TITLE = "F: 도표의 값을 좁혀 묻기 (지목된 상대 쪽 + 도표가 든 쪽 + 그 쪽의 도표 대상 전부)";

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

const aimsSchema = z.object({ aim1: z.array(z.object({ name: z.string(), doc: z.enum(["a", "b"]), id: z.string(), right: z.array(z.string()) })) });
const storedSchema = z.object({ synthetic: z.string().optional(), arm: z.enum(["ask", "ask2", "pair"]), nominate: z.enum(NOMINATE_MODES), prompt: z.string(), basis: z.record(z.string(), z.enum(["clear", "likely"])).optional(), tally: z.unknown(), states: z.record(z.string(), z.unknown()), verdicts: z.array(z.unknown()), cost: z.object({ calls: z.number(), costUsd: z.number() }).passthrough() });

export async function main(argv: string[], out: Out = console.log, log: Out = console.error, deps: LiveDeps = { env: process.env }): Promise<number> {
  const [command, ...rest] = argv;
  let values;
  try {
    values = parseArgs({ args: rest, options: { a: { type: "string" }, b: { type: "string" }, "exam-a": { type: "string" }, "exam-b": { type: "string" }, "chart-labels": { type: "string" }, out: { type: "string" }, nominate: { type: "string" }, arm: { type: "string" }, prompt: { type: "string" }, "split-by-name": { type: "boolean" }, "dry-run": { type: "boolean" }, provider: { type: "string" }, route: { type: "string" }, "limit-usd": { type: "string" }, log: { type: "string" }, "timeout-ms": { type: "string" }, replay: { type: "string" }, gold2: { type: "string" }, plants2: { type: "string" }, aims: { type: "string" }, runs: { type: "string" } } }).values;
  } catch (error) {
    out(`${(error as Error).message}\n\n${USAGE}`);
    return 2;
  }
  try {
    if (!["nominate", "runf", "tablef"].includes(command ?? "") || !values.a || !values.b || !values["exam-a"] || !values["exam-b"] || !values["chart-labels"] || !values.out) return out(USAGE), 2;
    const paths = { a: resolveInputPath(values.a), b: resolveInputPath(values.b), examA: resolveInputPath(values["exam-a"]), examB: resolveInputPath(values["exam-b"]), chartLabels: resolveInputPath(values["chart-labels"]) };
    const original = { a: doc(readDoc(paths.a), "a"), b: doc(readDoc(paths.b), "b") };
    const exam = { a: doc(readDoc(paths.examA), "a"), b: doc(readDoc(paths.examB), "b") };
    const loaded = z.record(z.enum(["a", "b"]), z.record(z.string(), z.string())).safeParse(readJsonFile(paths.chartLabels, "도표 라벨"));
    if (!loaded.success) throw new ReconcileFileError(`${paths.chartLabels}: 도표 라벨 형식이 맞지 않는다`);
    const chartLabels: Record<string, string> = {};
    for (const [docId, labels] of Object.entries(loaded.data)) for (const [id, text] of Object.entries(labels ?? {})) chartLabels[`${docId}:${id}`] = text;
    const outDir = resolveOutputPath(values.out);

    // 대상은 방법 Q 의 도표 갈래 그대로다. 원본과 시험지에서 낸 목록이 같아야 한다 (심은 값이 목록을 바꾸지 않는다).
    const targetsOf = (docs: { a: Doc; b: Doc }): Target[] => [...listTargets(docs.a, "a", chartLabels), ...listTargets(docs.b, "b", chartLabels)].filter((target) => target.branch === "chart");
    const targets = targetsOf(exam);
    if (JSON.stringify(targets) !== JSON.stringify(targetsOf(original))) throw new ReconcileFileError("원본에서 낸 대상 목록과 시험지에서 낸 대상 목록이 다르다 (심은 값이 대상 목록에 샌다)");
    const brief = (plan: FocusPlan): Record<string, unknown> => ({ id: plan.id, source: plan.source, page: plan.pages[0], otherPages: plan.otherPages, targets: plan.targets.length });
    const splitNote = values["split-by-name"] === true ? { splitByName: true } : {};

    if (command === "nominate") {
      mkdirSync(outDir, { recursive: true });
      const result = Object.fromEntries(NOMINATE_MODES.map((mode) => [mode, { exam: nominate(targets, exam, mode), original: nominate(targets, original, mode) }]));
      writeJson(join(outDir, "nominations.json"), { note: "방법 F 의 지목 (호출 0). 문서 모델과 코드 라벨에서만 냈다. 정답을 읽지 않았다. value 는 값이 같은 것을 지목의 신호로만 쓴다(짝을 정하지 않는다). exam 은 시험지(오류를 심은 사본), original 은 원본에서 낸 것이다.", chartTargets: targets.length, ...result });
      out(JSON.stringify({ chartTargets: targets.length, ...Object.fromEntries(NOMINATE_MODES.map((mode) => [mode, { exam: result[mode]?.exam.map((item) => `${item.source}:p${item.page} → ${item.otherPages.join(",")} (신호 ${item.signals.length})`), original: result[mode]?.original.map((item) => `${item.source}:p${item.page} → ${item.otherPages.join(",")} (신호 ${item.signals.length})`) }])) }, null, 1));
      return 0;
    }

    if (command === "tablef") {
      if (!values.gold2 || !values.plants2 || !values.aims || !values.runs) return out(USAGE), 2;
      const gold = gold2Schema.parse(readJsonFile(resolveInputPath(values.gold2), "넓힌 정답"));
      const plants = plants2Schema.parse(readJsonFile(resolveInputPath(values.plants2), "넓힌 명세"));
      const aims = aimsSchema.parse(readJsonFile(resolveInputPath(values.aims), "기대 자리"));
      const chartIds = new Set(targets.map(targetKey));
      const plantOf = new Map(plants.plants.filter((plant) => plant.isError).map((plant) => [`${plant.doc}:${plant.target}`, plant.id]));
      // 기대한 자리: 넓힌 정답의 서술 쌍 가운데 한쪽이 도표 대상인 것 + 기대 자리 파일의 도표 대상. 같은 대상의 상대는 모은다.
      const expected = new Map<string, Expected>();
      const add = (name: string, target: string, right: string[]): void => {
        const entry = expected.get(target) ?? { name, target, right: [], plant: plantOf.get(target) ?? null };
        for (const id of right) if (!entry.right.includes(id)) entry.right.push(id);
        expected.set(target, entry);
      };
      for (const pair of gold.prosePairs) {
        if (chartIds.has(`a:${pair.a}`)) add(pair.id, `a:${pair.a}`, [`b:${pair.b}`]);
        else if (chartIds.has(`b:${pair.b}`)) add(pair.id, `b:${pair.b}`, [`a:${pair.a}`]);
      }
      for (const aim of aims.aim1) if (chartIds.has(`${aim.doc}:${aim.id}`)) add(aim.name, `${aim.doc}:${aim.id}`, aim.right.map((id) => `${aim.doc === "a" ? "b" : "a"}:${id}`));
      const list = [...expected.values()];
      const runs = values.runs.split(",").map((item) => {
        const [name, dir] = item.split("=");
        if (!name || !dir) throw new ReconcileFileError(`--runs 의 꼴이 맞지 않는다: ${item}`);
        const stored = storedSchema.parse(readJsonFile(join(resolveOutputPath(dir), "outcome.json"), "실행 출력"));
        if (stored.synthetic) throw new ReconcileFileError(`${name}: 모의 실행의 출력이다. 실측의 표에 싣지 않는다`);
        return { name, stored, grade: gradeFocus(list, new Map(Object.entries(stored.states)) as Map<string, AskState>, stored.verdicts as Verdict[]) };
      });
      const lines = [`| 항목 | ${runs.map((run) => run.name).join(" | ")} |`, `|---|${runs.map(() => "---:").join("|")}|`];
      const row = (name: string, cell: (run: (typeof runs)[number]) => string): void => void lines.push(`| ${name} | ${runs.map(cell).join(" | ")} |`);
      row("방식 (지목 / 묻는 꼴 / 프롬프트)", (run) => `${run.stored.nominate} / ${run.stored.arm} / ${run.stored.prompt}`);
      row("기대한 자리 가운데 옳은 상대와 짝지어진 것", (run) => `${run.grade.rightPaired}/${run.grade.expected}`);
      row("　손대지 않은 자리: 옳은 상대와 일치", (run) => `${run.grade.cleanMatch}/${run.grade.clean}`);
      row("　심은 자리: 불일치로 잡음", (run) => `${run.grade.plantedCaught}/${run.grade.planted}`);
      row("　심은 자리: 일치로 나옴 (거짓 일치)", (run) => `${run.grade.plantedFalseMatch}/${run.grade.planted}`);
      row("기대한 자리 밖의 짝 (판정할 것)", (run) => `${run.grade.outside.length}`);
      row("호출 / 비용 USD", (run) => `${run.stored.cost.calls} / ${run.stored.cost.costUsd}`);
      const detail = list.map((item) => `| ${item.name} | ${item.target} | ${item.plant ?? ""} | ${runs.map((run) => { const found = run.grade.rows.find((entry) => entry.target === item.target); return found ? `${found.state}${found.rightPaired ? ` ✓ ${found.outcomes.join("/")}${run.stored.basis ? ` (${Object.entries(run.stored.basis).filter(([key]) => key.startsWith(`${item.target}|`) && item.right.includes(key.split("|")[1] as string)).map(([, value]) => value).join("/")})` : ""}` : ""}${found.wrongCounterparts.length > 0 ? ` ✗ ${found.wrongCounterparts.join(" ")}` : ""}` : ""; }).join(" | ")} |`);
      mkdirSync(outDir, { recursive: true });
      const text = `${lines.join("\n")}\n\n| 기대한 자리 | 대상 | 심은 오류 | ${runs.map((run) => run.name).join(" | ")} |\n|---|---|---|${runs.map(() => "---").join("|")}|\n${detail.join("\n")}\n`;
      writeFileSync(join(outDir, "focus.md"), text);
      writeJson(join(outDir, "focus.json"), { expected: list, runs: runs.map((run) => ({ name: run.name, arm: run.stored.arm, nominate: run.stored.nominate, prompt: run.stored.prompt, cost: run.stored.cost, grade: run.grade })) });
      out(text);
      return 0;
    }

    // ── runf ───────────────────────────────────────────────────────────────
    const mode = values.nominate as NominateMode | undefined;
    const arm = values.arm;
    if (!mode || !NOMINATE_MODES.includes(mode) || (arm !== "ask" && arm !== "ask2" && arm !== "pair")) return out(USAGE), 2;
    const replayDir = values.replay ? resolveOutputPath(values.replay) : null;
    if ([values.provider !== undefined, values["dry-run"] === true, replayDir !== null].filter(Boolean).length !== 1) return out(USAGE), 2;
    const promptId = values.prompt ?? (arm === "ask" ? PROMPT_Q : arm === "ask2" ? PROMPT_F2 : PROMPT_D2);
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

    // 지목은 시험지에서 낸다 (실제로 받는 문서가 그것이다). 같은 계획을 원본으로 조립한 글과 글자까지 같아야 한다 (심은 값이 요청 글에 새지 않는다).
    const nominations = nominate(targets, exam, mode);
    const plans = planFocus(targets, nominations, values["split-by-name"] === true);
    const requests = plans.map((plan) => buildFocusRequest(plan, exam, chartLabels));
    for (const [index, request] of requests.entries()) {
      const plan = plans[index] as FocusPlan;
      if (request.unmasked.length > 0) throw new ReconcileFileError(`${plan.id}: 요청 글에 가리지 못한 값 후보 숫자가 있다 (${request.unmasked.slice(0, 20).join(", ")}). 요청을 만들지 않는다`);
      if (request.missingInSource.length > 0) throw new ReconcileFileError(`${plan.id}: 대상이 문맥 글에 없다 (${request.missingInSource.slice(0, 20).join(", ")}). 요청을 만들지 않는다`);
      if (JSON.stringify(request.vars) !== JSON.stringify(buildFocusRequest(plan, original, chartLabels).vars)) throw new ReconcileFileError(`${plan.id}: 원본으로 조립한 요청 글과 시험지로 조립한 요청 글이 다르다 (심은 값이 요청 글에 샌다)`);
      const left = unmaskedInIdDocument(pairDocuments(request));
      if (left.length > 0) throw new ReconcileFileError(`${plan.id}: 문서 글에 가리지 못한 값 후보 숫자가 있다 (${left.slice(0, 20).join(", ")}). 요청을 만들지 않는다`);
    }

    const dry = new LlmClient({ config, providers: { mock: new MockProvider(), anthropic: new AnthropicProvider(), openai: new OpenAIProvider(), gemini: new GeminiProvider() }, prompts, log: { write: () => undefined } });
    const cap = Math.min(F_OUTPUT_CAP, model.maxOutputTokens);
    const base = { route, tags: TAGS, prompt: promptId, maxOutputTokens: cap, ...(timeoutMs === undefined ? {} : { timeoutMs }) };
    const askCall = (request: (typeof requests)[number]) => ({ ...base, vars: request.vars, schema: askPlacesSchema, schemaName: "reconcile_ask_places" });
    const ask2Call = (request: (typeof requests)[number]) => ({ ...base, vars: request.vars, schema: askPlaces2Schema, schemaName: "reconcile_ask_places_2" });
    const pairCall = (request: (typeof requests)[number]) => ({ ...base, vars: { documents: pairDocuments(request) }, schema: pairPlacesSchema, schemaName: "reconcile_pair_places" });
    // 어림은 캐시 없이 낸다 (앞부분이 같은 서로 다른 요청 사이에서 캐시 읽기가 듣지 않았다, 밴드 X).
    const estimate = { calls: 0, inputTokens: 0, maxInputTokensPerCall: 0, expectedOutputTokens: 0, noCacheUsd: 0, costUpperBoundUsd: 0 };
    const perRequest: Record<string, unknown>[] = [];
    for (const [index, request] of requests.entries()) {
      const report = arm === "ask" ? dry.dryRun(askCall(request)) : arm === "ask2" ? dry.dryRun(ask2Call(request)) : dry.dryRun(pairCall(request));
      const input = Math.max(conservativeTokens(JSON.stringify(arm === "pair" ? { documents: pairDocuments(request) } : request.vars)), report.estimate.inputTokens);
      if (input > INPUT_GUARD_TOKENS) throw new ReconcileFileError(`${request.plan.id}: 입력 어림 ${input} 토큰이 한도 ${INPUT_GUARD_TOKENS} 을 넘는다. 요청을 만들지 않는다`);
      const expectedOutput = request.plan.targets.length * ASK_ANSWER_TOKENS + 300;
      estimate.calls += 1;
      estimate.inputTokens += input;
      estimate.maxInputTokensPerCall = Math.max(estimate.maxInputTokensPerCall, input);
      estimate.expectedOutputTokens += expectedOutput;
      estimate.noCacheUsd += computeCostUsd({ inputTokens: input, outputTokens: expectedOutput, cacheReadTokens: 0, cacheWriteTokens: 0 }, model.pricing);
      estimate.costUpperBoundUsd += computeCostUsd({ inputTokens: input, outputTokens: report.estimate.maxOutputTokens, cacheReadTokens: 0, cacheWriteTokens: 0 }, model.pricing);
      perRequest.push({ ...brief(plans[index] as FocusPlan), inputTokensEstimate: input, expectedOutputTokens: expectedOutput });
    }
    const counts = { chartTargets: targets.length, nominatedPages: plans.length, asked: plans.reduce((sum, plan) => sum + plan.targets.length, 0) };

    if (values["dry-run"]) {
      const requestsDir = join(outDir, "requests");
      mkdirSync(requestsDir, { recursive: true });
      for (const request of requests) writeFileSync(join(requestsDir, `${request.plan.id.replace(/[/|#:]/g, "__")}.txt`), arm === "pair" ? `${pairDocuments(request)}\n` : `${request.vars.other}\n\n=====\n\n${request.vars.source}\n\n=====\n\n${request.vars.targets}\n`);
      const report = { dryRun: true, method: "f", arm, nominate: mode, prompt: promptId, ...splitNote, route, model: model.key, note: "공급사를 부르지 않았다. 입력 토큰은 보수적인 글자 수 어림과 호출 계층의 어림 중 큰 값이다. 예상 출력은 대상 하나에 ASK_ANSWER_TOKENS 토큰이라는 가정이다. noCacheUsd 는 캐시 할인 없는 값이고, 비용 상한은 요청마다 출력 상한을 다 쓸 때의 값이다(재시도 제외).", counts, ...estimate, noCacheUsd: roundUsd(estimate.noCacheUsd), costUpperBoundUsd: roundUsd(estimate.costUpperBoundUsd), nominations, requests: perRequest };
      writeJson(join(outDir, "dry-run.json"), report);
      out(`[f] 모델 ${model.key} · ${arm} · 지목 ${mode} · 프롬프트 ${promptId} · 도표 대상 ${targets.length} 가운데 물을 것 ${counts.asked} · 호출 ${estimate.calls} · 입력 어림 ${estimate.inputTokens} (요청 최대 ${estimate.maxInputTokensPerCall}) · 예상 출력 ${estimate.expectedOutputTokens} · 캐시 없이 ${report.noCacheUsd} USD · 비용 상한 ${report.costUpperBoundUsd} USD`);
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
    // 저장된 답의 파일은 하나이고 묻는 꼴마다 답의 스키마가 다르다. 그 꼴의 저장소만 연다 (다른 스키마로 열면 저장된 줄이 걸린다).
    const storePath = join(storeDir, "units.jsonl");
    const askStore = arm === "ask" ? new JsonlUnitStore(storePath, askPlacesSchema) : null;
    const ask2Store = arm === "ask2" ? new JsonlUnitStore(storePath, askPlaces2Schema) : null;
    const pairStore = arm === "pair" ? new JsonlUnitStore(storePath, pairPlacesSchema) : null;
    const basis: Record<string, Basis> = {};
    const failures: { id: string; kind: string }[] = [];
    const callIds: string[] = [];
    const states = new Map<string, AskState>();
    const verdicts: Verdict[] = [];
    const rejected: Record<string, number> = {};
    const perPlan: Record<string, unknown>[] = [];
    const missing = (id: string): never => {
      throw new ReconcileFileError(`${id}: 저장된 답에 없는 요청이다 (요청 글이 실측 때와 다르다). 다시 채점할 때는 모델을 부르지 않는다`);
    };
    for (const [index, request] of requests.entries()) {
      const plan = plans[index] as FocusPlan;
      let planStates: Map<string, AskState>;
      let planVerdicts: Verdict[];
      let planRejected: Record<string, number>;
      if (arm === "ask") {
        const call = askCall(request);
        let result = askStore?.get(plan.id, call);
        if (!result) {
          if (!client) missing(plan.id);
          if (!live) mock.enqueueText(JSON.stringify({ answers: [] }));
          result = await (client as LlmClient).call(call);
          askStore?.put(plan.id, call, result);
          log(`[f] ${plan.id}: ${result.kind} (${result.meta.totalCostUsd} USD)`);
        }
        callIds.push(result.meta.callId);
        if (result.kind !== "success") failures.push({ id: plan.id, kind: result.kind });
        const judgement = judgeAsk(result.kind === "success" ? result.data : null, plan, exam.a.context, exam.b.context, units);
        [planStates, planVerdicts, planRejected] = [judgement.states, judgement.verdicts, judgement.rejected];
      } else if (arm === "ask2") {
        const call = ask2Call(request);
        let result = ask2Store?.get(plan.id, call);
        if (!result) {
          if (!client) missing(plan.id);
          if (!live) mock.enqueueText(JSON.stringify({ answers: [] }));
          result = await (client as LlmClient).call(call);
          ask2Store?.put(plan.id, call, result);
          log(`[f] ${plan.id}: ${result.kind} (${result.meta.totalCostUsd} USD)`);
        }
        callIds.push(result.meta.callId);
        if (result.kind !== "success") failures.push({ id: plan.id, kind: result.kind });
        const split = result.kind === "success" ? splitBasis(result.data) : null;
        const judgement = judgeAsk(split ? split.answer : null, plan, exam.a.context, exam.b.context, units);
        // 받아들인 짝의 basis 만 남긴다.
        for (const [key, state] of judgement.states) if (state.state === "paired") for (const id of state.counterparts) basis[`${key}|${id}`] = split?.basis.get(`${key}|${id}`) ?? "likely";
        [planStates, planVerdicts, planRejected] = [judgement.states, judgement.verdicts, judgement.rejected];
      } else {
        const call = pairCall(request);
        let result = pairStore?.get(plan.id, call);
        if (!result) {
          if (!client) missing(plan.id);
          if (!live) mock.enqueueText(JSON.stringify({ pairs: [] }));
          result = await (client as LlmClient).call(call);
          pairStore?.put(plan.id, call, result);
          log(`[f] ${plan.id}: ${result.kind} (${result.meta.totalCostUsd} USD)`);
        }
        callIds.push(result.meta.callId);
        if (result.kind !== "success") failures.push({ id: plan.id, kind: result.kind });
        const judgement = judgePlacePairs(result.kind === "success" ? [result.data] : [], exam.a.context, exam.b.context, units);
        const moved = statesFromPairs(plan, judgement);
        [planStates, planVerdicts, planRejected] = [moved.states, judgement.verdicts, judgement.rejected];
      }
      for (const [key, state] of planStates) states.set(key, state);
      verdicts.push(...planVerdicts);
      for (const [why, count] of Object.entries(planRejected)) rejected[why] = (rejected[why] ?? 0) + count;
      const byState: Record<string, number> = {};
      for (const state of planStates.values()) byState[state.state === "none" ? `none:${state.reason}` : state.state] = (byState[state.state === "none" ? `none:${state.reason}` : state.state] ?? 0) + 1;
      perPlan.push({ ...brief(plan), states: byState, pairs: planVerdicts.length, rejected: planRejected });
    }
    let tally;
    try {
      tally = tallyFocus(targets, plans, states);
    } catch (error) {
      throw new TallyError((error as Error).message);
    }
    const records = readCallLog(logPath).filter((record) => callIds.includes(record.callId));
    const total = (record: (typeof records)[number]): number => (record.inputTokens ?? 0) + (record.cacheReadTokens ?? 0) + (record.cacheWriteTokens ?? 0);
    const cost = { calls: records.length, inputTokens: records.reduce((sum, record) => sum + total(record), 0), cacheReadTokens: records.reduce((sum, record) => sum + (record.cacheReadTokens ?? 0), 0), outputTokens: records.reduce((sum, record) => sum + (record.outputTokens ?? 0), 0), costUsd: roundUsd(records.reduce((sum, record) => sum + (record.costUsd ?? 0), 0)) };
    const synthetic = values.provider === "mock" ? "모의 실행 (빈 답을 돌려주는 synthetic 응답기). 결과는 모델의 성능과 무관하다." : null;
    writeJson(join(outDir, "outcome.json"), { ...(synthetic ? { synthetic } : {}), method: "f", title: TITLE, arm, nominate: mode, prompt: promptId, ...splitNote, provider: values.provider ?? null, ...(replayDir ? { replayedFrom: replayDir } : {}), route, model: model.key, inputs: { examA: sha(paths.examA), examB: sha(paths.examB), chartLabels: sha(paths.chartLabels) }, counts, nominations, requests: perPlan, failures, cost, tally, rejected, states: Object.fromEntries(states), verdicts, ...(arm === "ask2" ? { basis } : {}) });
    out(JSON.stringify({ ...(synthetic ? { synthetic } : {}), method: "f", arm, nominate: mode, prompt: promptId, failures, cost, spentUsd: budget ? budget.spent({ module: TAGS.module }) : null, tally, pairs: { match: verdicts.filter((verdict) => verdict.outcome === "match").length, mismatch: verdicts.filter((verdict) => verdict.outcome === "mismatch").length, needsExplanation: verdicts.filter((verdict) => verdict.outcome === "needs_explanation").length }, rejected }, null, 1));
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
