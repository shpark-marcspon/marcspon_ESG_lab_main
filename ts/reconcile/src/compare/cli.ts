import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { z } from "zod";
import { AnthropicProvider, BudgetGuard, FilePromptStore, GeminiProvider, JsonlFileSink, LiveSetupError, LlmClient, MockProvider, OpenAIProvider, computeCostUsd, defaultConfigPaths, liveProviders, loadConfig, requireLimitUsd, resolveRoute, roundUsd, type CallRequest, type LiveDeps, type LlmResult, type ModelConfig } from "llm";
import { ReconcileFileError, dataDir, documentSchema, loadCatalog, loadGold, loadSettings, loadUnits, promptRoot, readCallLog, readJsonFile, resolveInputPath, resolveOutputPath } from "../adapters/files.js";
import { JsonlUnitStore, seedBudgetFromLog } from "../adapters/unitStore.js";
import { extractDocument, type DocModel, type Extraction, type ExtractOptions, type Fact } from "../domain/facts.js";
import { inLabelScope, labelAnswerSchema, planUnits, renderCatalog, validateAnswers, type LabelAnswer, type LabelUnit } from "../domain/label.js";
import { reconcile, type MatchOptions, type Pending } from "../domain/match.js";
import { labelDocument, labelRequest, type LabelSettings, type RunOptions } from "../domain/pipeline.js";
import { ExamError, applyPlants, checkExam, checkPairs, drawPlants, pairsSchema, plantsSchema, type GoldPairs, type PlantSpec } from "./exam.js";
import { grade, renderComparison, type MethodGrade } from "./grade.js";
import { PROMPTS, alignTables, byDocumentSchema, byTableSchema, findDiffsSchema, judgeAlignments, judgeDiffs, judgePairs, matchByDocument, outcomeFromFindings, pageTexts, pairCellsSchema, pairRowsSchema, pairTablesSchema, validateByDocument, validateByTable, validateTablePairs, type DocContext, type MethodOutcome, type Place, type TableAlignment } from "./methods.js";
import { conservativeTokens, renderDocument, renderRowList, renderTableList, tablesWithValues } from "./render.js";
import { PROMPT_G_CELLS, hybridOutcome, judgeTableCells, pairCellsInTablesSchema, renderCellPairRequest, renderTableSide, unmaskedValues, valueCellCount, type CellJudgement, type TablePair } from "./hybrid.js";
import { judgmentsSchema, renderRuns, type Judgments, type Run } from "./runs.js";
import { MAX_REMAINING_PER_REQUEST, PROMPT_REVIEW_CELLS, PROMPT_REVIEW_TABLES, REVIEW_ANSWER_TOKENS, baseFromPairAnswers, deriveTablePairs, judgeCellReview, planCellReviews, renderCellReviewRequest, renderTableReviewRequest, reviewCellsSchema, reviewOutcome, unmaskedInCellReview, unmaskedInTableReview, validateNewTablePairs, type CellReviewJudgement } from "./review.js";
import { noCounterpartJudgmentsSchema, renderReview, type NoCounterpartJudgments, type ReviewRun } from "./review-report.js";

// 방법 비교 실측(MARC-TICK-21, 방법 G 와 반복 실행은 MARC-TICK-22, 방법 H 는 MARC-TICK-23)의 명령. 기본 명령(`src/cli.ts`)과 따로 둔다: 기본 동작을 바꾸지 않는다.

type Out = (line: string) => void;

const USAGE = `사용법 (cd ts && pnpm -F reconcile exec tsx src/compare/cli.ts <명령> ...)
  draw  --pairs <정답 쌍> --seed <n> --out-spec <심은 오류 명세>
      정답 쌍에서 심을 자리를 씨앗 고정 난수로 뽑아 명세를 쓴다. 이미 있는 명세는 덮어쓰지 않는다.
  build --a <문서 A> --b <문서 B 원본> --pairs <정답 쌍> --plants <명세> --out <디렉터리>
      문서 B 의 사본에 명세대로 심어 <out>/document.json 을 쓰고, 명세 밖의 칸이 원본과 같은지 확인하고, 해시를 낸다.
  run   --method <a|a2|a3|b|c|d|e|f|g|h> --a <문서 A> --b <문서 B 원본> --exam <시험지> --pairs .. --plants .. --out <디렉터리>
        (--dry-run | --provider mock | --provider openai --route <경로> --limit-usd <상한> --log <밴드 공용 호출 기록>
         | --replay <실제 실행의 출력 디렉터리> --route <경로> --log <그 실행의 호출 기록>)
        [--q-units <밴드 Q 의 units.jsonl 사본>] [--gold <정답 표 파일>] [--timeout-ms <n>] [--assume-table-pairs <표 짝 답 JSON>] [--d-run <방법 d 의 실제 실행 출력>]
      방법 하나를 시험지에서 돌리고 채점한다. a·a2 는 저장된 답만 쓴다 (호출 없음).
      --replay 는 실제 실행이 남긴 답(units.jsonl)만으로 다시 채점한다. 모델을 부르지 않고, 저장된 답에 없는 요청이 있으면 멈춘다.
      g 의 칸 짝 요청은 표 짝의 답이 와야 정해진다. --dry-run 에서는 --assume-table-pairs 로 준 표 짝으로 요청을 조립해 어림하고,
      조립한 요청 글을 <out>/requests/ 에 쓴다. 같은 방법을 다시 돌릴 때는 --out 을 실행마다 달리한다 (저장된 답을 다시 쓰지 않게).
      h 는 d 를 부르지 않는다. --d-run 으로 준 d 의 실제 실행이 남긴 답(units.jsonl)을 읽기만 하고, 그 뒤에 코드의 셈과 검수(칸 검수, 표 검수)를 붙인다.
  table --dir <방법별 출력이 든 디렉터리> --plants <명세> --out <디렉터리>
      방법별 grade.json 을 모아 비교표를 낸다. 모델을 부르지 않는다.
  runs  --run <방법>=<첫째 실행의 출력>,<둘째>,... [--run ...] --pairs <정답 쌍> --plants <명세> [--judgments <정답 밖 일치의 판정 기록>] --out <디렉터리>
      같은 방법의 반복 실행을 나란히 놓는다: 방법 × 실행 비교표, 심은 칸 × 실행, 짝의 교집합·합집합, 정답 밖 일치의 합집합. 모델을 부르지 않는다.
  review-table --review <이름>=<방법 h 의 실제 실행 출력> [--review ...] --pairs <정답 쌍> --plants <명세> [--judgments <새 짝의 판정 기록>] [--no-counterpart-judgments <「상대 없음」의 판정 기록>] --out <디렉터리>
      방법 h 의 실행들을 나란히 놓는다: 검수 전·후 비교표, 단계별 셈, 끌어낸 표 짝, 새 짝, 「상대 없음」. 모델을 부르지 않는다.
--out 은 반드시 준다. 상대 경로는 ts/ 기준이다. 실제 공급사 호출은 돈이 든다.`;

const TAGS = { tenant: "measure", module: "reconcile-compare" };
/** 요청 하나의 입력 어림(보수적)이 이 값을 넘으면 요청을 만들지 않고 나눈다. 장문 단가의 경계는 272,000 토큰이다. */
const INPUT_GUARD_TOKENS = 250_000;
const WHOLE_DOC_OUTPUT_CAP = 32_000;
/** A2 의 짝짓기 규칙 둘. A3·B 도 같은 규칙을 쓴다. */
const A2_RULES = { looseUnspecifiedBoundary: true, dropRepeatedKeysInTable: true } as const;
const METHOD_TITLES: Record<string, string> = {
  a: "A: 표마다 모든 숫자에 이름표 (지금 방식, 저장된 답)",
  a2: "A2: A 의 답 + 짝짓기 규칙 둘",
  a3: "A3: 제목·각주 잇기를 고쳐 다시 받음 + A2 의 규칙",
  b: "B: 지표 우선, 표 단위",
  c: "C: 지표 우선, 문서 통째",
  d: "D: 목록 없이 칸끼리 짝 (숫자 가림)",
  e: "E: 숫자째 통째로 주고 어긋나는 곳을 받음",
  f: "F: 표끼리 먼저 짝, 그 안에서 행·열 맞추기",
  g: "G: 표끼리 먼저 짝, 그 안의 칸 짝은 가린 표를 보고 AI 가",
  h: "H: D 의 답 + 코드의 셈 + 좁은 검수 (남은 칸, 짝이 없는 표)",
};
/** H 의 표 검수가 낸 새 표 짝이 이 수를 넘으면 칸 짝 요청을 만들지 않고 멈춘다. */
const NEW_TABLE_PAIR_LIMIT = 10;
/** G 의 칸 짝 요청 하나의 출력 상한과, 짝 하나의 출력 어림(가정). 예상 출력이 상한의 3/4 을 넘는 표 짝은 요청을 만들지 않는다. */
const CELL_PAIR_OUTPUT_CAP = 16_000;
const CELL_PAIR_TOKENS = 45;

const sha = (path: string): string => createHash("sha256").update(readFileSync(path)).digest("hex");
const writeJson = (path: string, value: unknown): void => writeFileSync(path, `${JSON.stringify(value, null, 1)}\n`);

function readDoc(path: string): DocModel {
  const parsed = documentSchema.safeParse(readJsonFile(path, "문서 JSON"));
  if (!parsed.success) throw new ReconcileFileError(`${path}: 문서 JSON 형식이 맞지 않는다`);
  return parsed.data as DocModel;
}

function readPairs(path: string): GoldPairs {
  const parsed = pairsSchema.safeParse(readJsonFile(path, "정답 쌍"));
  if (!parsed.success) throw new ReconcileFileError(`${path}: 정답 쌍 형식이 맞지 않는다\n${z.prettifyError(parsed.error)}`);
  return parsed.data;
}

function readPlants(path: string): PlantSpec {
  const parsed = plantsSchema.safeParse(readJsonFile(path, "심은 오류 명세"));
  if (!parsed.success) throw new ReconcileFileError(`${path}: 심은 오류 명세 형식이 맞지 않는다\n${z.prettifyError(parsed.error)}`);
  return parsed.data;
}

interface Doc {
  model: DocModel;
  extraction: Extraction;
  blockText: Map<string, string>;
  context: DocContext;
}

function doc(model: DocModel, docId: string, options: ExtractOptions = {}): Doc {
  const extraction = extractDocument(model, docId, options);
  const blockText = new Map(model.blocks.filter((block) => block.text !== undefined).map((block) => [block.id, block.text as string]));
  return { model, extraction, blockText, context: { extraction, pages: pageTexts(extraction, blockText, new Map(model.blocks.map((block) => [block.id, block.page]))) } };
}

/** 요청을 쪽 순서로 나눈다: 조각마다 입력 어림이 한도 안에 들 때까지 조각 수를 늘린다. 나누는 기준에 정답을 쓰지 않는다. */
export function pageChunks(pages: number[], fits: (from: number, to: number) => boolean): { from: number; to: number }[] {
  const sorted = [...pages].sort((a, b) => a - b);
  for (let parts = 1; parts <= sorted.length; parts++) {
    const size = Math.ceil(sorted.length / parts);
    const chunks = Array.from({ length: parts }, (_, i) => sorted.slice(i * size, (i + 1) * size)).filter((chunk) => chunk.length > 0).map((chunk) => ({ from: chunk[0] as number, to: chunk[chunk.length - 1] as number }));
    if (chunks.every((chunk) => fits(chunk.from, chunk.to))) return chunks;
  }
  throw new ReconcileFileError("쪽 하나가 입력 한도를 넘는다");
}

interface Planned<T> {
  id: string;
  request: CallRequest<T>;
  /** 예상 출력 토큰 (가정). */
  expectedOutput: number;
}

export async function main(argv: string[], out: Out = console.log, log: Out = console.error, deps: LiveDeps = { env: process.env }): Promise<number> {
  const [command, ...rest] = argv;
  let values;
  try {
    values = parseArgs({
      args: rest,
      options: { method: { type: "string" }, a: { type: "string" }, b: { type: "string" }, exam: { type: "string" }, pairs: { type: "string" }, plants: { type: "string" }, seed: { type: "string" }, "out-spec": { type: "string" }, out: { type: "string" }, "dry-run": { type: "boolean" }, provider: { type: "string" }, route: { type: "string" }, "limit-usd": { type: "string" }, log: { type: "string" }, "q-units": { type: "string" }, gold: { type: "string" }, "timeout-ms": { type: "string" }, dir: { type: "string" }, replay: { type: "string" }, "assume-table-pairs": { type: "string" }, run: { type: "string", multiple: true }, judgments: { type: "string" }, "d-run": { type: "string" }, review: { type: "string", multiple: true }, "no-counterpart-judgments": { type: "string" } },
    }).values;
  } catch (error) {
    out(`${(error as Error).message}\n\n${USAGE}`);
    return 2;
  }
  try {
    if (command === "draw") {
      if (!values.pairs || !values.seed || !values["out-spec"]) return out(USAGE), 2;
      const specPath = resolveOutputPath(values["out-spec"]);
      if (existsSync(specPath)) return out(`이미 있는 명세는 덮어쓰지 않는다: ${specPath}`), 2;
      const gold = readPairs(resolveInputPath(values.pairs));
      const plan: PlantSpec["plan"] = [
        { kind: "one_of_two_places", pool: "any" },
        { kind: "one_of_two_places", pool: "any" },
        { kind: "one_of_two_places", pool: "any" },
        { kind: "rounding_control", pool: "any" },
        { kind: "row_shift", pool: "any" },
        { kind: "year_swap", pool: "seed_tables" },
        { kind: "digit_changed", pool: "seed_tables" },
        { kind: "digit_changed", pool: "any" },
        { kind: "digits_transposed", pool: "seed_tables" },
        { kind: "digits_transposed", pool: "outside" },
        { kind: "digit_dropped", pool: "outside" },
        { kind: "digit_added", pool: "seed_tables" },
        { kind: "times_1000", pool: "seed_tables" },
        { kind: "times_1000", pool: "outside" },
      ];
      const seed = Number(values.seed);
      const spec: PlantSpec = { note: "방법 비교 실측의 시험지에 심는 오류. 문서 B(데이터북) 문서 모델의 사본에만 심는다. 심는 자리는 정답 쌍 가운데 종류별 조건에 맞는 것에서 씨앗 고정 난수로 뽑았다 (손으로 고르지 않았다). rounding_control 은 오류가 아닌 대조군이다.", status: "러너 작성, 전문가 검토 전 (밴드 R, 2026-10-04)", seed, plan, plants: drawPlants(gold.pairs, plan, seed) };
      writeJson(specPath, spec);
      out(JSON.stringify({ spec: specPath, sha256: sha(specPath), plants: spec.plants.length, errorCells: spec.plants.filter((plant) => plant.isError).reduce((sum, plant) => sum + plant.cells.length, 0) }, null, 1));
      return 0;
    }
    if (command === "table") {
      if (!values.dir || !values.plants || !values.out) return out(USAGE), 2;
      const dir = resolveOutputPath(values.dir);
      const order = Object.keys(METHOD_TITLES);
      const grades = readdirSync(dir).filter((name) => existsSync(join(dir, name, "grade.json"))).map((name) => readJsonFile(join(dir, name, "grade.json"), "채점 결과") as MethodGrade).sort((x, y) => order.indexOf(x.method) - order.indexOf(y.method));
      const md = `${grades.map((item) => `- ${item.title}`).join("\n")}\n\n${renderComparison(grades.map((item) => ({ ...item, method: item.method.toUpperCase() })), readPlants(resolveInputPath(values.plants)).plants)}`;
      const outDir = resolveOutputPath(values.out);
      mkdirSync(outDir, { recursive: true });
      writeFileSync(join(outDir, "comparison.md"), md);
      writeJson(join(outDir, "comparison.json"), grades);
      out(md);
      return 0;
    }
    if (command === "runs") {
      if (!values.run || values.run.length === 0 || !values.pairs || !values.plants || !values.out) return out(USAGE), 2;
      const runs: Run[] = [];
      for (const spec of values.run) {
        const [method, dirs] = spec.split("=");
        if (!method || !dirs) return out(USAGE), 2;
        dirs.split(",").forEach((dir, index) => {
          const base = resolveOutputPath(dir);
          const saved = readJsonFile(join(base, "outcome.json"), "실행 결과") as { synthetic?: string; outcome: Run["outcome"]; notes?: { tablePairs?: TablePair[]; tally?: Run["tally"] } };
          if (saved.synthetic) throw new ReconcileFileError(`${base}: 모의 실행의 결과다. 실측과 섞지 않는다`);
          runs.push({ method, run: index + 1, outcome: saved.outcome, grade: readJsonFile(join(base, "grade.json"), "채점 결과") as MethodGrade, ...(saved.notes?.tablePairs && saved.notes.tally ? { tablePairs: saved.notes.tablePairs, tally: saved.notes.tally } : {}) });
        });
      }
      let judgments: Judgments | null = null;
      if (values.judgments) {
        const parsed = judgmentsSchema.safeParse(readJsonFile(resolveInputPath(values.judgments), "정답 밖 일치의 판정 기록"));
        if (!parsed.success) throw new ReconcileFileError(`${values.judgments}: 판정 기록의 형식이 맞지 않는다\n${z.prettifyError(parsed.error)}`);
        judgments = parsed.data;
      }
      const md = renderRuns(runs, readPairs(resolveInputPath(values.pairs)).pairs, readPlants(resolveInputPath(values.plants)).plants, judgments);
      const outDir = resolveOutputPath(values.out);
      mkdirSync(outDir, { recursive: true });
      writeFileSync(join(outDir, "runs.md"), md);
      out(md);
      return 0;
    }
    if (command === "review-table") {
      if (!values.review || values.review.length === 0 || !values.pairs || !values.plants || !values.out) return out(USAGE), 2;
      const reviews: ReviewRun[] = [];
      for (const spec of values.review) {
        const [name, dir] = spec.split("=");
        if (!name || !dir) return out(USAGE), 2;
        const base = resolveOutputPath(dir);
        const saved = readJsonFile(join(base, "outcome.json"), "실행 결과") as { synthetic?: string; method?: string; outcome: ReviewRun["outcome"]; notes?: { review?: ReviewRun["review"]; before?: ReviewRun["before"] } };
        if (saved.synthetic) throw new ReconcileFileError(`${base}: 모의 실행의 결과다. 실측과 섞지 않는다`);
        if (saved.method !== "h" || !saved.notes?.review || !saved.notes.before) throw new ReconcileFileError(`${base}: 방법 h 의 실행 결과가 아니다`);
        reviews.push({ name, outcome: saved.outcome, grade: readJsonFile(join(base, "grade.json"), "채점 결과") as MethodGrade, review: saved.notes.review, before: saved.notes.before });
      }
      let judgments: Judgments | null = null;
      if (values.judgments) {
        const parsed = judgmentsSchema.safeParse(readJsonFile(resolveInputPath(values.judgments), "새 짝의 판정 기록"));
        if (!parsed.success) throw new ReconcileFileError(`${values.judgments}: 판정 기록의 형식이 맞지 않는다\n${z.prettifyError(parsed.error)}`);
        judgments = parsed.data;
      }
      let noCounterpart: NoCounterpartJudgments | null = null;
      if (values["no-counterpart-judgments"]) {
        const parsed = noCounterpartJudgmentsSchema.safeParse(readJsonFile(resolveInputPath(values["no-counterpart-judgments"]), "「상대 없음」의 판정 기록"));
        if (!parsed.success) throw new ReconcileFileError(`${values["no-counterpart-judgments"]}: 판정 기록의 형식이 맞지 않는다\n${z.prettifyError(parsed.error)}`);
        noCounterpart = parsed.data;
      }
      const md = renderReview(reviews, readPairs(resolveInputPath(values.pairs)).pairs, readPlants(resolveInputPath(values.plants)).plants, judgments, noCounterpart);
      const outDir = resolveOutputPath(values.out);
      mkdirSync(outDir, { recursive: true });
      writeFileSync(join(outDir, "review.md"), md);
      out(md);
      return 0;
    }
    if (!values.out || !values.a || !values.b || !values.pairs || !values.plants) return out(USAGE), 2;
    const outDir = resolveOutputPath(values.out);
    const modelA = readDoc(resolveInputPath(values.a));
    const modelB = readDoc(resolveInputPath(values.b));
    const pairsPath = resolveInputPath(values.pairs);
    const plantsPath = resolveInputPath(values.plants);
    const gold = readPairs(pairsPath);
    const spec = readPlants(plantsPath);
    const pairProblems = checkPairs(gold, modelA, modelB);
    if (pairProblems.length > 0) throw new ReconcileFileError(`정답 쌍이 문서와 맞지 않는다\n${pairProblems.join("\n")}`);

    if (command === "build") {
      const exam = applyPlants(modelB, spec);
      const problems = checkExam(modelB, exam, spec);
      if (problems.length > 0) throw new ExamError(problems.join("\n"));
      mkdirSync(outDir, { recursive: true });
      const examPath = join(outDir, "document.json");
      writeFileSync(examPath, `${JSON.stringify(exam)}\n`);
      const before = new Set(extractDocument(modelB, "b").facts.map((fact) => fact.id));
      const after = extractDocument(exam, "b").facts.map((fact) => fact.id);
      if (after.length !== before.size || after.some((id) => !before.has(id))) throw new ExamError("시험지의 값 후보 ID 가 원본과 다르다 (저장된 답을 쓸 수 없다)");
      const hashes = { pairs: sha(pairsPath), plants: sha(plantsPath), exam: sha(examPath), originalB: sha(resolveInputPath(values.b)) };
      writeJson(join(outDir, "hashes.json"), hashes);
      out(JSON.stringify({ exam: examPath, checked: "명세 밖의 칸은 원본과 같다", changedCells: spec.plants.reduce((sum, plant) => sum + plant.cells.length, 0), valueCandidates: after.length, sha256: hashes }, null, 1));
      return 0;
    }

    if (command !== "run" || !values.method || !(values.method in METHOD_TITLES) || !values.exam) return out(USAGE), 2;
    const method = values.method;
    const offline = method === "a" || method === "a2";
    const replayDir = values.replay ? resolveOutputPath(values.replay) : null;
    if (!offline && [values.provider !== undefined, values["dry-run"] === true, replayDir !== null].filter(Boolean).length !== 1) return out(USAGE), 2;
    const modelExam = readDoc(resolveInputPath(values.exam));
    const examProblems = checkExam(modelB, modelExam, spec);
    if (examProblems.length > 0) throw new ExamError(`시험지가 명세와 맞지 않는다\n${examProblems.join("\n")}`);
    const catalog = loadCatalog();
    const settings = loadSettings().label;
    const units = loadUnits();
    const config = loadConfig(defaultConfigPaths({ synthetic: true }));
    const prompts = new FilePromptStore(promptRoot);
    const live = values.provider !== undefined && values.provider !== "mock";
    const route = live || values["dry-run"] || replayDir ? (values.route ?? "light-openai") : "mock";
    /** 답을 저장하는 자리. 다시 채점할 때는 실제 실행의 것을 읽기만 한다. */
    const storeDir = replayDir ?? outDir;
    const model = resolveRoute(config, route)[0] as ModelConfig | undefined;
    if (!model) throw new ReconcileFileError(`경로 ${route} 에 모델이 없다`);
    const timeoutMs = values["timeout-ms"] === undefined ? undefined : Number(values["timeout-ms"]);
    const matchOptions = (rules: boolean): MatchOptions => ({ catalog, units, minConfidence: settings.minConfidence, defaultPeriod: { a: null, b: null }, ...(rules ? A2_RULES : {}) });
    mkdirSync(outDir, { recursive: true });

    // ── 호출 준비 ─────────────────────────────────────────────────────────
    const dry = new LlmClient({ config, providers: { mock: new MockProvider(), anthropic: new AnthropicProvider(), openai: new OpenAIProvider(), gemini: new GeminiProvider() }, prompts, log: { write: () => undefined } });
    const estimate = { calls: 0, inputTokens: 0, expectedOutputTokens: 0, expectedCostUsd: 0, costUpperBoundUsd: 0, maxInputTokensPerCall: 0 };
    const mock = new MockProvider();
    let client: LlmClient | null = null;
    let budget: BudgetGuard | null = null;
    const logPath = values.log ? resolveOutputPath(values.log) : join(outDir, "calls.jsonl");
    if (live) {
      try {
        if (!values.route || !config.routes[values.route]) throw new LiveSetupError("실제 공급사 호출에는 설정에 있는 --route <경로> 가 필요하다");
        const limitUsd = requireLimitUsd(values["limit-usd"]);
        if (!values.log) throw new LiveSetupError("실제 공급사 호출에는 --log <밴드 공용 호출 기록> 이 필요하다 (앞선 방법이 쓴 비용을 상한에 같이 센다)");
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
    const failures: { id: string; kind: string }[] = [];
    const requestChanged: string[] = [];
    const callIds: string[] = [];
    /** 요청 하나: 어림을 쌓고, dry-run 이면 부르지 않는다. 답을 받은 요청은 저장해 두고 다시 부르지 않는다. */
    async function ask<T>(store: JsonlUnitStore<T>, planned: Planned<T>, mockText: string): Promise<T | null> {
      const tokens = conservativeTokens(JSON.stringify(planned.request.vars ?? {}));
      const report = dry.dryRun(planned.request);
      const input = Math.max(tokens, report.estimate.inputTokens);
      if (input > INPUT_GUARD_TOKENS) throw new ReconcileFileError(`${planned.id}: 입력 어림 ${input} 토큰이 한도 ${INPUT_GUARD_TOKENS} 을 넘는다. 요청을 만들지 않는다`);
      estimate.calls += 1;
      estimate.inputTokens += input;
      estimate.maxInputTokensPerCall = Math.max(estimate.maxInputTokensPerCall, input);
      estimate.expectedOutputTokens += planned.expectedOutput;
      estimate.expectedCostUsd += computeCostUsd({ inputTokens: input, outputTokens: planned.expectedOutput, cacheReadTokens: 0, cacheWriteTokens: 0 }, model!.pricing);
      estimate.costUpperBoundUsd += computeCostUsd({ inputTokens: input, outputTokens: report.estimate.maxOutputTokens, cacheReadTokens: 0, cacheWriteTokens: 0 }, model!.pricing);
      if (!client && !replayDir) return null;
      let result: LlmResult<T> | undefined = store.get(planned.id, planned.request);
      if (!result && replayDir) {
        // 실측 뒤에 요청 글이 바뀐 단위(행 짝 요청의 가림을 실측 뒤에 고쳤다)는 요청 단위 ID 로 찾아 쓰고 그 사실을 남긴다.
        result = store.all().find((entry) => entry.unitId === planned.id)?.result;
        if (result) {
          requestChanged.push(planned.id);
          log(`[${method}] ${planned.id}: 요청 글이 실측 때와 다르다. 저장된 답을 요청 단위 ID 로 찾아 썼다`);
        }
      }
      if (!result) {
        if (!client) throw new ReconcileFileError(`${planned.id}: 저장된 답에 없는 요청이다. 다시 채점할 때는 모델을 부르지 않는다`);
        if (!live) mock.enqueueText(mockText);
        result = await client.call(planned.request);
        store.put(planned.id, planned.request, result);
        log(`[${method}] ${planned.id}: ${result.kind} (${result.meta.totalCostUsd} USD)`);
      }
      callIds.push(result.meta.callId);
      if (result.kind !== "success") {
        failures.push({ id: planned.id, kind: result.kind });
        return null;
      }
      return result.data;
    }
    const base = { route, tags: TAGS, ...(timeoutMs === undefined ? {} : { timeoutMs }) };
    const cap = (wanted: number): number => Math.min(wanted, model.maxOutputTokens);

    // ── 방법 ───────────────────────────────────────────────────────────────
    let outcome: MethodOutcome;
    const notes: Record<string, unknown> = {};
    /** 저장된 답(밴드 Q)으로 원본 문서에 뜻을 붙이고, 문서 B 의 값만 시험지의 것으로 바꾼다. 심은 것은 숫자 칸의 글자뿐이라 뜻은 그대로다. */
    const replay = async (): Promise<{ a: Pending[]; b: Pending[]; original: Pending[] }> => {
      if (!values["q-units"]) throw new ReconcileFileError("a·a2·a3 에는 --q-units <밴드 Q 의 units.jsonl 사본> 이 필요하다");
      const store = new JsonlUnitStore<LabelAnswer>(resolveInputPath(values["q-units"]), labelAnswerSchema);
      const gateway = { call: (): never => { throw new ReconcileFileError("저장된 답에 없는 요청이 있다. a·a2 는 모델을 부르지 않는다"); } };
      const qModel = resolveRoute(config, "light-openai")[0] as ModelConfig;
      const a = doc(modelA, "a");
      const b = doc(modelB, "b");
      const options: RunOptions = { a: { extraction: a.extraction, docKind: "report", defaultPeriod: null }, b: { extraction: b.extraction, docKind: "databook", defaultPeriod: null }, catalog, units, memory: [], settings, includeSentences: false, gateway, route: "light-openai", tags: TAGS, modelMaxOutputTokens: qModel.maxOutputTokens, store };
      const pa = (await labelDocument(options.a, options)).pending;
      const pb = (await labelDocument(options.b, options)).pending;
      const examFacts = new Map(extractDocument(modelExam, "b").facts.map((fact) => [fact.id, fact]));
      return { a: pa, original: pb, b: pb.map((pending) => ({ ...pending, fact: examFacts.get(pending.fact.id) as Fact })) };
    };
    const distribution = (findings: ReturnType<typeof reconcile>): Record<string, number> => {
      const count: Record<string, number> = { match: 0, mismatch: 0, needs_explanation: 0 };
      for (const finding of findings) if (finding.a && finding.b) count[finding.outcome] = (count[finding.outcome] ?? 0) + 1;
      return count;
    };

    if (method === "a" || method === "a2") {
      const pending = await replay();
      notes.originalDocuments = distribution(reconcile(pending.a, pending.original, matchOptions(method === "a2")));
      outcome = outcomeFromFindings(reconcile(pending.a, pending.b, matchOptions(method === "a2")));
    } else if (method === "a3" || method === "b") {
      const a = doc(modelA, "a", { titles: "own" });
      const b = doc(modelExam, "b", { titles: "own" });
      const store = method === "a3" ? new JsonlUnitStore<LabelAnswer>(join(storeDir, "units.jsonl"), labelAnswerSchema) : null;
      const storeB = method === "b" ? new JsonlUnitStore(join(storeDir, "units.jsonl"), byTableSchema) : null;
      let wanted: Set<string> | null = null;
      if (method === "a3") {
        // 다시 받는 표: 정답 쌍이 있는 표와 정답 표(밴드 Q). 오류를 심은 표는 정답 쌍이 있는 표에 들어 있다.
        const goldTables = loadGold(values.gold ? resolveInputPath(values.gold) : join(dataDir, "gold", "kb-2025.gold.json")).tables.map((table) => `${table.doc}:${table.tableId}`);
        wanted = new Set([...gold.pairs.flatMap((pair) => [`a:${pair.a.split(":")[0]}`, `b:${pair.b.split(":")[0]}`]), ...goldTables]);
        notes.tables = [...wanted].sort();
      }
      const base0 = method === "a3" ? await replay() : null;
      const labelSettings: LabelSettings = method === "b" ? { ...settings, maxTargetsPerCall: Number.MAX_SAFE_INTEGER } : settings;
      const pendingOf = async (side: Doc, prior: Pending[] | null): Promise<Pending[]> => {
        const scope = side.extraction.facts.filter((fact) => inLabelScope(fact, { includeSentences: false }));
        const planned = planUnits(side.extraction, scope, { maxTargetsPerCall: labelSettings.maxTargetsPerCall, includeSentences: false }).filter((unit) => !wanted || wanted.has(`${unit.docId}:${unit.gridId}`));
        const facts = new Map(side.extraction.facts.map((fact) => [fact.id, fact]));
        const labels = new Map<string, Pending>();
        const work = async (unit: LabelUnit): Promise<void> => {
          const id = `${unit.docId}:${unit.id}`;
          const targets = unit.targetFactIds.length;
          const mockText = JSON.stringify(method === "a3" ? { labels: [] } : { items: [] });
          const validation =
            method === "a3"
              ? await ask(store!, { id, request: labelRequest(unit, catalog, { ...base, settings, modelMaxOutputTokens: model.maxOutputTokens }), expectedOutput: targets * 105 + 200 }, mockText).then((data) => (data ? validateAnswers(unit, data, { catalog, facts }) : null))
              : await ask(storeB!, { id, request: { ...base, prompt: PROMPTS.b, vars: { catalog: renderCatalog(catalog), unit: unit.text }, schema: byTableSchema, schemaName: "reconcile_by_table", maxOutputTokens: cap((targets * 30 + 400) * 2) }, expectedOutput: targets * 12 + 700 }, mockText).then((data) => (data ? validateByTable(unit, data, { catalog, facts }) : null));
          for (const factId of unit.targetFactIds) labels.set(factId, { fact: facts.get(factId) as Fact, label: null, unverified: { reason: validation ? (method === "b" ? "not_in_catalog" : "not_answered") : "llm_call_failed", detail: null } });
          if (!validation) return;
          for (const label of validation.accepted) labels.set(label.factId, { fact: facts.get(label.factId) as Fact, label, unverified: null });
          for (const rejected of validation.rejected) {
            outcomeRejected[rejected.reason] = (outcomeRejected[rejected.reason] ?? 0) + 1;
            if (rejected.factId) labels.set(rejected.factId, { fact: facts.get(rejected.factId) as Fact, label: null, unverified: { reason: "answer_rejected", detail: rejected.reason } });
          }
        };
        let next = 0;
        await Promise.all(Array.from({ length: Math.min(settings.concurrency, Math.max(1, planned.length)) }, async () => { while (next < planned.length) await work(planned[next++] as LabelUnit); }));
        // 다시 받지 않은 숫자: A3 는 저장된 답, B 는 범위 밖(신뢰도 낮은 표 등).
        const before = new Map((prior ?? []).map((pending) => [pending.fact.id, pending]));
        return side.extraction.facts.map((fact): Pending => labels.get(fact.id) ?? (before.has(fact.id) ? { ...(before.get(fact.id) as Pending), fact } : { fact, label: null, unverified: { reason: "table_low_confidence", detail: "뜻 붙이기 대상 밖" } }));
      };
      const outcomeRejected: Record<string, number> = {};
      const pa = await pendingOf(a, base0?.a ?? null);
      const pb = await pendingOf(b, base0?.b ?? null);
      outcome = outcomeFromFindings(reconcile(pa, pb, matchOptions(true)));
      outcome.rejected = outcomeRejected;
    } else {
      const own = method === "f" || method === "g" || method === "h" ? ({ titles: "own" } as const) : {};
      const a = doc(modelA, "a", own);
      const b = doc(modelExam, "b", own);
      const mask = method === "d";
      const pagesOf = (side: Doc): number[] => [...new Set(side.model.blocks.map((block) => block.page))];
      const prefix = conservativeTokens(readFileSync(join(promptRoot, "reconcile", "by-document", "1.md"), "utf8")) + conservativeTokens(renderCatalog(catalog));
      if (method === "c") {
        const store = new JsonlUnitStore(join(storeDir, "units.jsonl"), byDocumentSchema);
        const places: Record<"a" | "b", Place[]> = { a: [], b: [] };
        outcome = { verdicts: [], intra: [], states: { a: {}, b: {} }, rejected: {} };
        const split: Record<string, unknown> = {};
        for (const side of [a, b]) {
          const id = side.extraction.docId as "a" | "b";
          const chunks = pageChunks(pagesOf(side), (from, to) => prefix + conservativeTokens(renderDocument(side.model, side.extraction, { mask: false, pages: { from, to } })) <= INPUT_GUARD_TOKENS - 2000);
          split[id] = chunks;
          for (const [index, chunk] of chunks.entries()) {
            const answer = await ask(store, { id: `c:${id}/${index + 1}`, request: { ...base, prompt: PROMPTS.c, vars: { catalog: renderCatalog(catalog), document: renderDocument(side.model, side.extraction, { mask: false, pages: chunk }) }, schema: byDocumentSchema, schemaName: "reconcile_by_document", maxOutputTokens: cap(WHOLE_DOC_OUTPUT_CAP) }, expectedOutput: 8000 }, JSON.stringify({ items: [] }));
            if (!answer) continue;
            const checked = validateByDocument(answer, side.context, catalog);
            places[id].push(...checked.places);
            for (const [reason, count] of Object.entries(checked.rejected)) outcome.rejected[reason] = (outcome.rejected[reason] ?? 0) + count;
          }
        }
        notes.split = split;
        notes.places = { a: places.a.length, b: places.b.length };
        const matched = matchByDocument(places.a, places.b, units);
        outcome = { ...matched, rejected: outcome.rejected };
      } else if (method === "d" || method === "e") {
        // 두 문서를 한 요청에. 한도를 넘으면 문서 A 를 쪽 순서로 나누고, 조각마다 문서 B 전체를 같이 싣는다.
        const wholeB = renderDocument(b.model, b.extraction, { mask });
        const chunks = pageChunks(pagesOf(a), (from, to) => conservativeTokens(renderDocument(a.model, a.extraction, { mask, pages: { from, to } })) + conservativeTokens(wholeB) + 1500 <= INPUT_GUARD_TOKENS);
        notes.split = { a: chunks, b: "전체" };
        outcome = { verdicts: [], intra: [], states: { a: {}, b: {} }, rejected: {} };
        const merge = (part: MethodOutcome): void => {
          const seen = new Set(outcome.verdicts.map((verdict) => `${verdict.a}|${verdict.b}`));
          outcome.verdicts.push(...part.verdicts.filter((verdict) => !seen.has(`${verdict.a}|${verdict.b}`)));
          for (const [reason, count] of Object.entries(part.rejected)) outcome.rejected[reason] = (outcome.rejected[reason] ?? 0) + count;
        };
        for (const [index, chunk] of chunks.entries()) {
          const documents = `${renderDocument(a.model, a.extraction, { mask, pages: chunk })}\n\n${wholeB}`;
          if (method === "d") {
            const answer = await ask(new JsonlUnitStore(join(storeDir, "units.jsonl"), pairCellsSchema), { id: `d/${index + 1}`, request: { ...base, prompt: PROMPTS.d, vars: { documents }, schema: pairCellsSchema, schemaName: "reconcile_pair_cells", maxOutputTokens: cap(WHOLE_DOC_OUTPUT_CAP) }, expectedOutput: 12000 }, JSON.stringify({ pairs: [] }));
            if (answer) merge(judgePairs(answer, a.context, b.context, units));
          } else {
            const answer = await ask(new JsonlUnitStore(join(storeDir, "units.jsonl"), findDiffsSchema), { id: `e/${index + 1}`, request: { ...base, prompt: PROMPTS.e, vars: { documents }, schema: findDiffsSchema, schemaName: "reconcile_find_diffs", maxOutputTokens: cap(WHOLE_DOC_OUTPUT_CAP) }, expectedOutput: 8000 }, JSON.stringify({ discrepancies: [] }));
            if (answer) merge(judgeDiffs(answer, a.context, b.context, units));
          }
        }
      } else if (method === "g") {
        // G: 표 목록 한 요청 → 표 짝 (F 와 같은 요청). 표 짝마다 한 요청 → 가린 두 표와 그 쪽의 글을 보고 칸 짝. 값은 코드가 견준다.
        const tables = `${renderTableList(a.extraction)}\n\n${renderTableList(b.extraction)}`;
        const gridsA = new Map(tablesWithValues(a.extraction).map((grid) => [grid.id, grid]));
        const gridsB = new Map(tablesWithValues(b.extraction).map((grid) => [grid.id, grid]));
        const answer = await ask(new JsonlUnitStore(join(storeDir, "units.jsonl"), pairTablesSchema), { id: "g/tables", request: { ...base, prompt: PROMPTS.f1, vars: { tables }, schema: pairTablesSchema, schemaName: "reconcile_pair_tables", maxOutputTokens: cap(16_000) }, expectedOutput: 4000 }, JSON.stringify({ table_pairs: [] }));
        let assumed: unknown = null;
        if (values["dry-run"]) {
          if (!values["assume-table-pairs"]) throw new ReconcileFileError("g 의 dry-run 에는 --assume-table-pairs <표 짝 답 JSON> 이 필요하다 (칸 짝 요청은 표 짝의 답이 와야 정해진다)");
          assumed = readJsonFile(resolveInputPath(values["assume-table-pairs"]), "표 짝 답");
          // 가림의 전수 확인: 표 짝이 무엇으로 오든 실릴 수 있는 모든 표의 한쪽 글에 값 후보 숫자가 남지 않아야 한다.
          const leaks = [a, b].flatMap((side) => tablesWithValues(side.extraction).flatMap((grid) => unmaskedValues(renderTableSide(side.model, side.extraction, grid.id)).map((value) => `${side.extraction.docId}:${grid.id}: ${value}`)));
          if (leaks.length > 0) throw new ReconcileFileError(`가리지 못한 값 후보 숫자가 있다\n${leaks.join("\n")}`);
          notes.maskCheck = { tablesRendered: { a: gridsA.size, b: gridsB.size }, unmaskedValues: 0 };
        }
        const checked = validateTablePairs(values["dry-run"] ? pairTablesSchema.parse(assumed) : (answer ?? { table_pairs: [] }), new Set(gridsA.keys()), new Set(gridsB.keys()));
        const cellStore = new JsonlUnitStore(join(storeDir, "cell-units.jsonl"), pairCellsInTablesSchema);
        const judgements: CellJudgement[] = [];
        const requests: { id: string; a: number; b: number }[] = [];
        if (values["dry-run"]) mkdirSync(join(outDir, "requests"), { recursive: true });
        for (const pair of checked.pairs) {
          const id = `g/cells/${pair.a}|${pair.b}`;
          const text = renderCellPairRequest({ document: a.model, extraction: a.extraction }, { document: b.model, extraction: b.extraction }, pair);
          const left = unmaskedValues(text);
          if (left.length > 0) throw new ReconcileFileError(`${id}: 요청 글에 가리지 못한 값 후보 숫자가 있다 (${left.join(", ")}). 요청을 만들지 않는다`);
          const cells = { a: valueCellCount(a.extraction, { id: pair.a }), b: valueCellCount(b.extraction, { id: pair.b }) };
          const expectedOutput = Math.min(cells.a, cells.b) * CELL_PAIR_TOKENS + 300;
          if (expectedOutput > cap(CELL_PAIR_OUTPUT_CAP) * 0.75) throw new ReconcileFileError(`${id}: 예상 출력 ${expectedOutput} 토큰이 출력 상한의 3/4 을 넘는다. 요청을 만들지 않는다`);
          requests.push({ id, ...cells });
          if (values["dry-run"]) writeFileSync(join(outDir, "requests", `${pair.a}__${pair.b}.txt`), `${text}\n`);
          const cellAnswer = await ask(cellStore, { id, request: { ...base, prompt: PROMPT_G_CELLS, vars: { tables: text }, schema: pairCellsInTablesSchema, schemaName: "reconcile_pair_cells_in_tables", maxOutputTokens: cap(CELL_PAIR_OUTPUT_CAP) }, expectedOutput }, JSON.stringify({ pairs: [] }));
          if (cellAnswer) judgements.push(judgeTableCells(cellAnswer, pair, a.context, b.context, units));
        }
        const result = hybridOutcome(a.extraction, b.extraction, checked.pairs, judgements, checked.rejected);
        outcome = result.outcome;
        notes.tablePairs = checked.pairs;
        notes.cellRequests = requests;
        notes.tally = result.tally;
        if (values["dry-run"]) notes.assumedTablePairs = "표 짝은 --assume-table-pairs 로 준 것이다 (실제 실행에서는 모델의 답으로 정해진다)";
      } else if (method === "h") {
        // H: 저장된 D 의 답(읽기만) → 코드의 셈 → 칸 검수(남은 칸이 있는 표 짝마다 한 요청) → 표 검수(한 요청) → 새 표 짝마다 G 의 칸 짝 요청.
        // 요청은 D 의 답과 문서에서만 조립한다. 정답 쌍과 명세는 아래의 채점에서만 쓴다.
        if (!values["d-run"]) throw new ReconcileFileError("h 에는 --d-run <방법 d 의 실제 실행 출력 디렉터리> 가 필요하다 (d 를 다시 부르지 않는다)");
        const dDir = resolveOutputPath(values["d-run"]);
        const dSaved = readJsonFile(join(dDir, "outcome.json"), "방법 d 의 실행 결과") as { synthetic?: string; method?: string; outcome: MethodOutcome };
        if (dSaved.synthetic || dSaved.method !== "d") throw new ReconcileFileError(`${dDir}: 방법 d 의 실제 실행 결과가 아니다`);
        const dAnswers = readFileSync(join(dDir, "units.jsonl"), "utf8").split("\n").filter((line) => line.trim() !== "").map((line) => JSON.parse(line) as { unitId: string; result: { kind: string; data?: unknown } }).filter((entry) => entry.result.kind === "success").sort((x, y) => x.unitId.localeCompare(y.unitId, "en", { numeric: true })).map((entry) => pairCellsSchema.parse(entry.result.data));
        const dBase = baseFromPairAnswers(dAnswers, a.context, b.context, units);
        const pairKey = (verdict: { a: string; b: string; outcome?: string }): string => `${verdict.a}|${verdict.b}|${verdict.outcome}`;
        if (dBase.outcome.verdicts.map(pairKey).sort().join("\n") !== dSaved.outcome.verdicts.map(pairKey).sort().join("\n")) throw new ReconcileFileError(`${dDir}: 저장된 답을 다시 확인한 결과가 그 실행의 outcome.json 과 다르다`);
        const derived = deriveTablePairs(dBase.outcome.verdicts);
        const sideA = { document: a.model, extraction: a.extraction };
        const sideB = { document: b.model, extraction: b.extraction };
        if (values["dry-run"]) mkdirSync(join(outDir, "requests"), { recursive: true });
        const fileName = (id: string): string => id.replace(/^h\//, "").replace(/[/|#]/g, "__");
        // (가) 칸 검수
        const cellStore = new JsonlUnitStore(join(storeDir, "review-cells.jsonl"), reviewCellsSchema);
        const cellReviews: CellReviewJudgement[] = [];
        const requests: { id: string; remaining: number; paired: number }[] = [];
        for (const plan of planCellReviews(a.extraction, b.extraction, derived, dBase.outcome.verdicts, MAX_REMAINING_PER_REQUEST)) {
          const text = renderCellReviewRequest(sideA, sideB, plan);
          const left = unmaskedInCellReview(text);
          if (left.length > 0) throw new ReconcileFileError(`${plan.id}: 요청 글에 가리지 못한 값 후보 숫자가 있다 (${left.join(", ")}). 요청을 만들지 않는다`);
          const expectedOutput = plan.remaining.length * REVIEW_ANSWER_TOKENS + 300;
          if (expectedOutput > cap(CELL_PAIR_OUTPUT_CAP) * 0.75) throw new ReconcileFileError(`${plan.id}: 예상 출력 ${expectedOutput} 토큰이 출력 상한의 3/4 을 넘는다. 요청을 만들지 않는다`);
          requests.push({ id: plan.id, remaining: plan.remaining.length, paired: plan.paired.length });
          if (values["dry-run"]) writeFileSync(join(outDir, "requests", `${fileName(plan.id)}.txt`), `${text}\n`);
          const reviewAnswer = await ask(cellStore, { id: plan.id, request: { ...base, prompt: PROMPT_REVIEW_CELLS, vars: { tables: text }, schema: reviewCellsSchema, schemaName: "reconcile_review_cells", maxOutputTokens: cap(CELL_PAIR_OUTPUT_CAP) }, expectedOutput }, JSON.stringify({ answers: [] }));
          cellReviews.push(judgeCellReview(reviewAnswer, plan, a.context, b.context, units));
        }
        // (나) 표 검수
        const gridsA = new Map(tablesWithValues(a.extraction).map((grid) => [grid.id, grid]));
        const gridsB = new Map(tablesWithValues(b.extraction).map((grid) => [grid.id, grid]));
        const tables = renderTableReviewRequest(a.extraction, b.extraction, derived);
        const tableLeft = unmaskedInTableReview(tables);
        if (tableLeft.length > 0) throw new ReconcileFileError(`h/tables: 요청 글에 가리지 못한 값 후보 숫자가 있다 (${tableLeft.join(", ")}). 요청을 만들지 않는다`);
        if (values["dry-run"]) writeFileSync(join(outDir, "requests", "tables.txt"), `${tables}\n`);
        const tableAnswer = await ask(new JsonlUnitStore(join(storeDir, "review-tables.jsonl"), pairTablesSchema), { id: "h/tables", request: { ...base, prompt: PROMPT_REVIEW_TABLES, vars: { tables }, schema: pairTablesSchema, schemaName: "reconcile_review_tables", maxOutputTokens: cap(16_000) }, expectedOutput: 1500 }, JSON.stringify({ table_pairs: [] }));
        const checked = validateNewTablePairs(tableAnswer ?? { table_pairs: [] }, new Set(gridsA.keys()), new Set(gridsB.keys()), derived);
        if (checked.pairs.length > NEW_TABLE_PAIR_LIMIT) throw new ReconcileFileError(`표 검수가 새 표 짝 ${checked.pairs.length}개를 냈다 (한도 ${NEW_TABLE_PAIR_LIMIT}). 칸 짝 요청을 만들지 않는다`);
        const newCellStore = new JsonlUnitStore(join(storeDir, "new-table-cells.jsonl"), pairCellsInTablesSchema);
        const newJudgements: CellJudgement[] = [];
        const answeredPairs: TablePair[] = [];
        for (const pair of checked.pairs) {
          const id = `h/new-cells/${pair.a}|${pair.b}`;
          const text = renderCellPairRequest(sideA, sideB, pair);
          const left = unmaskedValues(text);
          if (left.length > 0) throw new ReconcileFileError(`${id}: 요청 글에 가리지 못한 값 후보 숫자가 있다 (${left.join(", ")}). 요청을 만들지 않는다`);
          const expectedOutput = Math.min(valueCellCount(a.extraction, { id: pair.a }), valueCellCount(b.extraction, { id: pair.b })) * CELL_PAIR_TOKENS + 300;
          if (expectedOutput > cap(CELL_PAIR_OUTPUT_CAP) * 0.75) throw new ReconcileFileError(`${id}: 예상 출력 ${expectedOutput} 토큰이 출력 상한의 3/4 을 넘는다. 요청을 만들지 않는다`);
          const cellAnswer = await ask(newCellStore, { id, request: { ...base, prompt: PROMPT_G_CELLS, vars: { tables: text }, schema: pairCellsInTablesSchema, schemaName: "reconcile_pair_cells_in_tables", maxOutputTokens: cap(CELL_PAIR_OUTPUT_CAP) }, expectedOutput }, JSON.stringify({ pairs: [] }));
          answeredPairs.push(pair);
          newJudgements.push(cellAnswer ? judgeTableCells(cellAnswer, pair, a.context, b.context, units) : { verdicts: [], rejected: {}, rejectedCells: { a: [], b: [] } });
        }
        if (values["dry-run"]) {
          // 새 표 짝의 수는 표 검수의 답이 와야 안다. 어림에는 한도까지 다 나온다는 가정을 넣는다: 10건, 건마다 입력 3500·출력 2000 토큰.
          estimate.calls += NEW_TABLE_PAIR_LIMIT;
          estimate.inputTokens += NEW_TABLE_PAIR_LIMIT * 3500;
          estimate.expectedOutputTokens += NEW_TABLE_PAIR_LIMIT * 2000;
          estimate.expectedCostUsd += NEW_TABLE_PAIR_LIMIT * computeCostUsd({ inputTokens: 3500, outputTokens: 2000, cacheReadTokens: 0, cacheWriteTokens: 0 }, model.pricing);
          estimate.costUpperBoundUsd += NEW_TABLE_PAIR_LIMIT * computeCostUsd({ inputTokens: 3500, outputTokens: cap(CELL_PAIR_OUTPUT_CAP) + (model.reasoningOutputReserveTokens ?? 0), cacheReadTokens: 0, cacheWriteTokens: 0 }, model.pricing);
          const all = [a, b].flatMap((side) => tablesWithValues(side.extraction).flatMap((grid) => unmaskedValues(renderTableSide(side.model, side.extraction, grid.id)).map((value) => `${side.extraction.docId}:${grid.id}: ${value}`)));
          if (all.length > 0) throw new ReconcileFileError(`가리지 못한 값 후보 숫자가 있다\n${all.join("\n")}`);
          notes.maskCheck = { cellReviewRequests: requests.length, tableReviewRequest: 1, tablesRendered: { a: gridsA.size, b: gridsB.size }, unmaskedValues: 0 };
          notes.newTablePairAllowance = `새 표 짝의 칸 짝 요청은 표 검수의 답이 와야 정해진다. 어림에 ${NEW_TABLE_PAIR_LIMIT}건(한도)을 가정으로 넣었다`;
        }
        const result = reviewOutcome({ a: a.extraction, b: b.extraction, base: dBase, cellReviews, newTablePairs: answeredPairs, newTableJudgements: newJudgements, tableRejected: checked.rejected });
        outcome = result.after.outcome;
        notes.dRun = dDir;
        notes.cellRequests = requests;
        notes.review = { tablePairs: result.tablePairs, newTablePairs: result.newTablePairs, newPairs: result.newPairs, remaining: result.remaining, rejected: result.rejected, tally: { before: result.before.tally, after: result.after.tally } };
        notes.before = { outcome: result.before.outcome, grade: grade(gold.pairs, spec.plants, result.before.outcome) };
      } else {
        // F: 표 목록 한 요청 → 표 짝. 짝지어진 표마다 코드가 행·열을 맞추고, 양쪽에 못 맞춘 행이 남으면 그 표 짝만 한 번 더 묻는다.
        const tables = `${renderTableList(a.extraction)}\n\n${renderTableList(b.extraction)}`;
        const gridsA = new Map(tablesWithValues(a.extraction).map((grid) => [grid.id, grid]));
        const gridsB = new Map(tablesWithValues(b.extraction).map((grid) => [grid.id, grid]));
        const factsIn = (side: Doc, gridId: string): Fact[] => side.extraction.facts.filter((fact) => fact.bucket === "table_cell" && fact.where.gridId === gridId);
        const answer = await ask(new JsonlUnitStore(join(storeDir, "units.jsonl"), pairTablesSchema), { id: "f/tables", request: { ...base, prompt: PROMPTS.f1, vars: { tables }, schema: pairTablesSchema, schemaName: "reconcile_pair_tables", maxOutputTokens: cap(16_000) }, expectedOutput: 4000 }, JSON.stringify({ table_pairs: [] }));
        const checked = validateTablePairs(answer ?? { table_pairs: [] }, new Set(gridsA.keys()), new Set(gridsB.keys()));
        const rowStore = new JsonlUnitStore(join(storeDir, "row-units.jsonl"), pairRowsSchema);
        const alignments: TableAlignment[] = [];
        let rowCalls = 0;
        let rejectedRowPairs = 0;
        for (const pair of checked.pairs) {
          const left = { grid: gridsA.get(pair.a)!, facts: factsIn(a, pair.a) };
          const right = { grid: gridsB.get(pair.b)!, facts: factsIn(b, pair.b) };
          let aligned = alignTables(left, right, units);
          if (aligned.unmatchedRows.a.length > 0 && aligned.unmatchedRows.b.length > 0) {
            const rows = `${renderRowList("a", left.grid, aligned.unmatchedRows.a)}\n\n${renderRowList("b", right.grid, aligned.unmatchedRows.b)}`;
            rowCalls += 1;
            const rowAnswer = await ask(rowStore, { id: `f/rows/${pair.a}|${pair.b}`, request: { ...base, prompt: PROMPTS.f2, vars: { rows }, schema: pairRowsSchema, schemaName: "reconcile_pair_rows", maxOutputTokens: cap(4000) }, expectedOutput: 600 }, JSON.stringify({ row_pairs: [] }));
            if (rowAnswer) aligned = alignTables(left, right, units, rowAnswer.row_pairs);
            rejectedRowPairs += aligned.rejectedRowPairs;
          }
          alignments.push(aligned);
        }
        if (values["dry-run"]) {
          // 행 짝 요청의 수는 표 짝의 답이 와야 안다. 어림에는 가정을 넣는다: 30건, 건마다 입력 800·출력 600 토큰.
          estimate.calls += 30;
          estimate.inputTokens += 30 * 800;
          estimate.expectedOutputTokens += 30 * 600;
          estimate.expectedCostUsd += 30 * computeCostUsd({ inputTokens: 800, outputTokens: 600, cacheReadTokens: 0, cacheWriteTokens: 0 }, model.pricing);
          estimate.costUpperBoundUsd += 30 * computeCostUsd({ inputTokens: 800, outputTokens: 6000, cacheReadTokens: 0, cacheWriteTokens: 0 }, model.pricing);
        }
        outcome = judgeAlignments(alignments, a.extraction, b.extraction, units);
        outcome.rejected = { ...checked.rejected, ...(rejectedRowPairs > 0 ? { unknown_row_id: rejectedRowPairs } : {}) };
        notes.tablePairs = checked.pairs;
        notes.rowCalls = rowCalls;
        notes.alignments = alignments.map((item) => ({ a: item.a, b: item.b, cellPairs: item.cellPairs.length, unmatchedRows: { a: item.unmatchedRows.a.length, b: item.unmatchedRows.b.length } }));
      }
    }

    if (values["dry-run"]) {
      const report = { dryRun: true, method, route, model: model.key, note: "공급사를 부르지 않았다. 입력 토큰은 보수적인 글자 수 어림(한글 1자 1토큰, 그 밖 1자 0.6토큰)과 호출 계층의 어림 중 큰 값이다. 예상 출력은 방법마다 둔 가정이다. 캐시 할인은 넣지 않았다. 비용 상한은 요청마다 출력 상한을 다 쓸 때의 값이고 재시도는 들어 있지 않다.", inputGuardTokens: INPUT_GUARD_TOKENS, ...estimate, expectedCostUsd: roundUsd(estimate.expectedCostUsd), costUpperBoundUsd: roundUsd(estimate.costUpperBoundUsd), notes };
      writeJson(join(outDir, "dry-run.json"), report);
      out(`[${method}] 모델 ${model.key} · 호출 ${estimate.calls} · 입력 어림 ${estimate.inputTokens} (요청 최대 ${estimate.maxInputTokensPerCall}) · 예상 출력 ${estimate.expectedOutputTokens} · 예상 비용 ${report.expectedCostUsd} USD · 비용 상한 ${report.costUpperBoundUsd} USD`);
      return 0;
    }
    const records = offline ? [] : readCallLog(logPath).filter((record) => callIds.includes(record.callId));
    const byResultKind: Record<string, number> = {};
    for (const record of records) byResultKind[record.resultKind] = (byResultKind[record.resultKind] ?? 0) + 1;
    const cost = offline ? null : { calls: records.length, inputTokens: records.reduce((sum, record) => sum + (record.inputTokens ?? 0) + (record.cacheReadTokens ?? 0) + (record.cacheWriteTokens ?? 0), 0), outputTokens: records.reduce((sum, record) => sum + (record.outputTokens ?? 0), 0), costUsd: roundUsd(records.reduce((sum, record) => sum + (record.costUsd ?? 0), 0)), byResultKind };
    const graded: MethodGrade = { method, title: METHOD_TITLES[method] as string, reportsUnchecked: method !== "e" && method !== "d", reportsMatches: method !== "e", grade: grade(gold.pairs, spec.plants, outcome), cost };
    const synthetic = values.provider === "mock" ? "모의 실행 (빈 답을 돌려주는 synthetic 응답기). 결과는 모델의 성능과 무관하다." : null;
    writeJson(join(outDir, "outcome.json"), { ...(synthetic ? { synthetic } : {}), method, provider: values.provider ?? null, ...(replayDir ? { replayedFrom: replayDir } : {}), route: offline ? null : route, model: offline ? null : model.key, exam: { pairs: sha(pairsPath), plants: sha(plantsPath), document: sha(resolveInputPath(values.exam)) }, notes: { ...notes, ...(requestChanged.length > 0 ? { requestChangedSinceRun: requestChanged } : {}) }, failures, outcome });
    writeJson(join(outDir, "grade.json"), { ...(synthetic ? { synthetic } : {}), ...graded });
    const g = graded.grade;
    out(JSON.stringify({ ...(synthetic ? { synthetic } : {}), method, notes, failures, cost, spentUsd: budget ? budget.spent({ module: TAGS.module }) : null, clean: `${g.clean.match}/${g.clean.total} 일치, 설명 필요 ${g.clean.needsExplanation}, 불일치 ${g.clean.mismatch}`, control: `${g.control.match}/${g.control.total}`, errors: `잡음 ${g.errors.caught}/${g.errors.total}, 다른 꼴로 드러남 ${g.errors.visibleOther}, 거짓 일치 ${g.errors.falseMatch}, 조용히 ${g.errors.silent}`, mismatches: `${g.mismatches.total} (심은 오류 ${g.mismatches.truePlanted}, 그 밖 ${g.mismatches.other.length})`, verdicts: g.verdicts, rejected: g.rejected, files: { outcome: join(outDir, "outcome.json"), grade: join(outDir, "grade.json") } }, null, 1));
    return failures.length > 0 ? 3 : 0;
  } catch (error) {
    if (error instanceof ReconcileFileError || error instanceof ExamError) {
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
