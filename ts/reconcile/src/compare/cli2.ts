import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { z } from "zod";
import { AnthropicProvider, BudgetGuard, FilePromptStore, GeminiProvider, JsonlFileSink, LiveSetupError, LlmClient, MockProvider, OpenAIProvider, computeCostUsd, defaultConfigPaths, liveProviders, loadConfig, requireLimitUsd, resolveRoute, roundUsd, type CallRequest, type LiveDeps, type LlmResult, type ModelConfig } from "llm";
import { ReconcileFileError, documentSchema, loadUnits, promptRoot, readCallLog, readJsonFile, resolveInputPath, resolveOutputPath } from "../adapters/files.js";
import { JsonlUnitStore, seedBudgetFromLog } from "../adapters/unitStore.js";
import { extractDocument, type DocModel, type Extraction } from "../domain/facts.js";
import { pageChunks } from "./cli.js";
import { ExamError, checkPairs, pairsSchema, plantsSchema, type GoldPairs, type PlantSpec } from "./exam.js";
import { allChanges, applyChanges, checkExam2, checkGold2, drawPlants2, gold2Schema, plants2Schema, type Gold2, type Plant2Spec } from "./exam2.js";
import { grade, type Grade } from "./grade.js";
import { renderTableSide, unmaskedValues } from "./hybrid.js";
import { pageTexts, type DocContext, type MethodOutcome } from "./methods.js";
import { PROMPT_D2, PROMPT_D3B, PROMPT_I, PROMPT_REVIEW_CELLS_2, groupPlacesSchema, idsInText, judgeCellReview2, judgeGroups, judgePlacePairs, pairPlacesSchema, pickNameBlocks, planCellReviews2, renderDocumentWithIds, review2Outcome, reviewCells2Schema, tallyGroups, tallyPlaces, unmaskedInIdDocument, type CellReview2Judgement, type PlaceJudgement } from "./prose.js";
import { gradeGroups, gradeProse, plantedPlaces, proseJudgmentsSchema, renderProseTables, toJudge, type CostSummary, type D2Run, type IRun, type ProseJudgments } from "./prose-report.js";
import { conservativeTokens, tablesWithValues } from "./render.js";
import { MAX_REMAINING_PER_REQUEST, REVIEW_ANSWER_TOKENS, renderCellReviewRequest, unmaskedInCellReview } from "./review.js";
import { PROMPT_LV, imageTokens, judgeTagAnswers, pngSize, tagLabelsSchema, visionLabels, type TagAnswer } from "./vision.js";

// 밴드 U (MARC-TICK-24) 의 명령: 넓힌 시험지(draw2, build2), 방법 D2·H2·I (run2), 표 (table2).
// 앞 밴드의 명령(`src/compare/cli.ts`)과 따로 둔다: 방법 a~h 와 기본 동작을 바꾸지 않는다.

type Out = (line: string) => void;

const USAGE = `사용법 (cd ts && pnpm -F reconcile exec tsx src/compare/cli2.ts <명령> ...)
  draw2  --a <문서 A 원본> --b <문서 B 원본> --pairs <밴드 R 의 정답 쌍> --plants <밴드 R 의 명세> --gold2 <넓힌 정답> --seed <n> --out-spec <넓힌 명세>
      넓힌 정답에서 심을 자리를 씨앗 고정 난수로 뽑아 명세를 쓴다. 이미 있는 명세는 덮어쓰지 않는다.
  build2 --a .. --b .. --pairs .. --plants .. --gold2 .. --plants2 <넓힌 명세> --out <디렉터리>
      두 문서 모델의 사본에 심어 <out>/a.document.json, <out>/b.document.json 을 쓰고(문서 B 에는 밴드 R 의 명세도 같이), 명세 밖이 원본과 같은지 확인하고, 해시를 낸다.
  run2   --method <d2|h2|i|d3a|d3b|i3|d4c|d4v|i4|lv> --a .. --b .. --exam-a <시험지 A> --exam-b <시험지 B> --pairs .. --plants .. --gold2 .. --plants2 .. --out <디렉터리>
         (--dry-run | --provider mock | --provider openai --route <경로> --limit-usd <상한> --log <밴드 공용 호출 기록> | --replay <실제 실행의 출력> --route <경로> --log <호출 기록>)
         [--d2-run <방법 d2 의 실제 실행 출력>] [--doc <a|b>] [--timeout-ms <n>]
      d4c·d4v·i4 (밴드 W): d2·i 와 같되 --chart-labels <파일> 의 라벨이 있는 값 후보를 \`[도표 제목 · 계열 · 범주: ▒〔ID〕]\` 꼴로 싣는다. 프롬프트는 d2·i 와 같다.
      lv (밴드 W): --tags-a·--tags-b <번호표 목록> 와 --tag-images-a·--tag-images-b <번호표를 덮은 쪽 그림의 디렉터리> 로, 쪽마다 그림 한 장과 번호 목록을 주고 번호마다 갈래·제목·계열·범주·단위를 받는다.
         [--pages a:39,b:6 (그 쪽만)] [--image-multiplier <그림 토큰 어림의 곱수, 기본 1.2>]. <out>/tag-answers.json, vision-labels.json 을 쓴다. 정답·시험지 사본을 조립에 쓰지 않는다.
      d3a·d3b·i3 (밴드 V): d2·i 와 같되 숫자뿐인 블록을 코드가 고른 이름 블록과 한 줄로 싣는다. d3b 는 프롬프트만 pair-places@2 다.
      d2: 가린 두 문서(표의 숫자 칸은 칸 ID, 글 속 값 후보는 ▒ 뒤에 ID)에서 자리의 짝을 받는다.
      h2: d2 를 부르지 않는다. --d2-run 의 저장된 답 뒤에 코드의 셈과 칸 검수(상대는 그 요청의 남은 칸에서만)를 붙인다.
      i:  --doc 의 문서 하나에서 같은 항목을 말하는 자리의 묶음을 받고, 묶음 안의 값을 코드가 견준다.
  table2 [--d <밴드 R·S 의 d 실행 출력,..>] [--d2 <이름=출력,..>] [--d2x <d3a·d3b·d4c·d4v 의 이름=출력,..>] [--ix <i3·i4 의 이름=출력,..>] [--h2 <이름=출력,..>] [--i <이름=출력,..>] --a .. --b .. --exam-a .. --exam-b .. --pairs .. --plants .. --gold2 .. --plants2 .. [--judgments <판정 기록>] --out <디렉터리>
      저장된 실행들을 나란히 놓는다. 모델을 부르지 않는다.
--out 은 반드시 준다. 상대 경로는 ts/ 기준이다. 실제 공급사 호출은 돈이 든다.`;

const TAGS = { tenant: "measure", module: "reconcile-compare" };
/** 요청 하나의 입력 어림(보수적)이 이 값을 넘으면 요청을 만들지 않는다. 장문 단가의 경계는 272,000 토큰이다. */
const INPUT_GUARD_TOKENS = 250_000;
const D2_OUTPUT_CAP = 32_000;
const I_OUTPUT_CAP = 48_000;
const REVIEW_OUTPUT_CAP = 16_000;
const LV_OUTPUT_CAP = 32_000;
/** 그림 라벨의 번호표 하나에 대한 출력 토큰의 어림. 연결 확인 1회(번호표 8개, 출력 1,872 토큰, 그중 추론 1,530)에서 잰 값을 올림한 것이다. 추론 토큰이 번호표 수에 비례하는지는 모른다. */
const LV_ANSWER_TOKENS = 240;
/** 그림 토큰 어림의 곱수 기본값 (32px 조각 하나당 토큰). 공급사 문서(https://developers.openai.com/api/docs/guides/images-vision, 확인 2026-10-04)의 표에 이 모델(gpt-6-luna)은 없고 같은 계열 모델들이 1.2 다 (추정). 연결 확인 1회의 사용량(200dpi 쪽 그림 1654×2339, 입력 합 5,356 토큰)이 이 곱수의 어림(그림 4,618 + 글)과 맞았다. */
const IMAGE_TOKEN_MULTIPLIER = 1.2;
/** 예상 출력 토큰 (가정). d2 는 방법 D 의 실측(요청당 약 7,000)에 서술 몫을 얹은 값, i 는 문서의 크기로 잡은 값이다. */
const EXPECTED_OUTPUT = { d2: 13_000, i: { a: 16_000, b: 10_000 } } as const;
const TITLES = { d2: "D2: 서술을 넣은 자리 짝 (숫자 가림, 글 속 값 후보에 ID)", h2: "H2: D2 의 답 + 코드의 셈 + 칸 검수(상대는 남은 칸에서만)", i: "I: 같은 문서 안의 묶음", d3a: "D3a: D2 + 숫자뿐인 블록을 이름과 한 줄로 (프롬프트는 D2 와 같다)", d3b: "D3b: D3a 의 요청 글 + 요약 숫자 갈래를 설명한 프롬프트", i3: "I3: I + 숫자뿐인 블록을 이름과 한 줄로", d4c: "D4c: D2 + 도표의 값에 코드 라벨(좌표·그림 요소)", d4v: "D4v: D2 + 도표의 값에 그림 라벨(쪽 그림을 본 AI)", i4: "I4: I + 도표의 값에 코드 라벨", lv: "그림 라벨: 번호표를 덮은 쪽 그림에서 번호마다 계열·범주 (값은 받지 않는다)" } as const;
/** 밴드 V 의 방법: 밴드 U 의 방법에 한 줄 묶기(와 D3b 는 새 프롬프트)를 얹은 것. 답의 확인·셈·채점은 밑의 방법과 같다. */
const VARIANTS = { d3a: { base: "d2", prompt: PROMPT_D2 }, d3b: { base: "d2", prompt: PROMPT_D3B }, i3: { base: "i", prompt: PROMPT_I }, d4c: { base: "d2", prompt: PROMPT_D2 }, d4v: { base: "d2", prompt: PROMPT_D2 }, i4: { base: "i", prompt: PROMPT_I } } as const;
/** 도표 라벨을 싣는 갈래 (밴드 W). 한 줄 묶기는 하지 않는다. 라벨은 `--chart-labels` 파일(문서별 값 후보 ID → 라벨 글)에서만 온다. */
const CHART_VARIANTS = new Set(["d4c", "d4v", "i4"]);
/** 넓힌 명세의 계획: 서술에 오류 8개와 대조군 1개, 문서 안 어긋남을 문서마다 3개. */
const PLAN2: Plant2Spec["plan"] = [
  { kind: "digit_changed", pool: "prose_pair" },
  { kind: "digit_changed", pool: "prose_pair" },
  { kind: "digits_transposed", pool: "prose_pair" },
  { kind: "digits_transposed", pool: "prose_pair" },
  { kind: "digit_dropped", pool: "prose_pair" },
  { kind: "digit_added", pool: "prose_pair" },
  { kind: "times_10", pool: "prose_pair" },
  { kind: "times_1000", pool: "prose_pair" },
  { kind: "rounding_control", pool: "prose_pair" },
  { kind: "digit_changed", pool: "group_a" },
  { kind: "digits_transposed", pool: "group_a" },
  { kind: "times_10", pool: "group_a" },
  { kind: "digit_changed", pool: "group_b" },
  { kind: "digit_dropped", pool: "group_b" },
  { kind: "times_10", pool: "group_b" },
];

const sha = (path: string): string => createHash("sha256").update(readFileSync(path)).digest("hex");
const writeJson = (path: string, value: unknown): void => writeFileSync(path, `${JSON.stringify(value, null, 1)}\n`);

function readDoc(path: string): DocModel {
  const parsed = documentSchema.safeParse(readJsonFile(path, "문서 JSON"));
  if (!parsed.success) throw new ReconcileFileError(`${path}: 문서 JSON 형식이 맞지 않는다`);
  return parsed.data as DocModel;
}

function readAs<T>(path: string, schema: z.ZodType<T>, what: string): T {
  const parsed = schema.safeParse(readJsonFile(path, what));
  if (!parsed.success) throw new ReconcileFileError(`${path}: ${what} 형식이 맞지 않는다\n${z.prettifyError(parsed.error)}`);
  return parsed.data;
}

interface Doc {
  model: DocModel;
  extraction: Extraction;
  context: DocContext;
}

function doc(model: DocModel, docId: "a" | "b"): Doc {
  // 표의 제목·각주는 방법 f·g·h 와 같은 잇기(`own`)를 쓴다 (칸 검수의 요청이 방법 h 와 같은 꼴이 되게).
  const extraction = extractDocument(model, docId, { titles: "own" });
  const blockText = new Map(model.blocks.filter((block) => block.text !== undefined).map((block) => [block.id, block.text as string]));
  return { model, extraction, context: { extraction, pages: pageTexts(extraction, blockText, new Map(model.blocks.map((block) => [block.id, block.page]))) } };
}

/** 값 후보의 ID·갈래가 같은가 (심은 뒤에도 숫자 뽑기가 같은 자리를 같은 갈래로 읽는가). */
function sameCandidates(before: Extraction, after: Extraction): boolean {
  const sign = (extraction: Extraction): string => extraction.facts.map((fact) => `${fact.id}|${fact.bucket}`).join("\n");
  return sign(before) === sign(after);
}

interface Planned<T> {
  id: string;
  request: CallRequest<T>;
  expectedOutput: number;
  /** 첨부한 그림의 입력 토큰 어림 (호출 계층의 어림은 그림을 세지 않는다). */
  imageTokens?: number;
}

export async function main(argv: string[], out: Out = console.log, log: Out = console.error, deps: LiveDeps = { env: process.env }): Promise<number> {
  const [command, ...rest] = argv;
  let values;
  try {
    values = parseArgs({
      args: rest,
      options: { method: { type: "string" }, a: { type: "string" }, b: { type: "string" }, "exam-a": { type: "string" }, "exam-b": { type: "string" }, pairs: { type: "string" }, plants: { type: "string" }, gold2: { type: "string" }, plants2: { type: "string" }, seed: { type: "string" }, "out-spec": { type: "string" }, out: { type: "string" }, "dry-run": { type: "boolean" }, provider: { type: "string" }, route: { type: "string" }, "limit-usd": { type: "string" }, log: { type: "string" }, "timeout-ms": { type: "string" }, replay: { type: "string" }, "d2-run": { type: "string" }, "chart-labels": { type: "string" }, "tags-a": { type: "string" }, "tags-b": { type: "string" }, "tag-images-a": { type: "string" }, "tag-images-b": { type: "string" }, pages: { type: "string" }, "image-multiplier": { type: "string" }, doc: { type: "string" }, d: { type: "string" }, d2: { type: "string" }, d2x: { type: "string" }, ix: { type: "string" }, h2: { type: "string" }, i: { type: "string" }, judgments: { type: "string" } },
    }).values;
  } catch (error) {
    out(`${(error as Error).message}\n\n${USAGE}`);
    return 2;
  }
  try {
    if (!["draw2", "build2", "run2", "table2"].includes(command ?? "") || !values.a || !values.b || !values.pairs || !values.plants || !values.gold2) return out(USAGE), 2;
    const modelA = readDoc(resolveInputPath(values.a));
    const modelB = readDoc(resolveInputPath(values.b));
    const pairsPath = resolveInputPath(values.pairs);
    const plantsPath = resolveInputPath(values.plants);
    const gold2Path = resolveInputPath(values.gold2);
    const v1Pairs: GoldPairs = readAs(pairsPath, pairsSchema, "정답 쌍");
    const v1Plants: PlantSpec = readAs(plantsPath, plantsSchema, "심은 오류 명세");
    const gold: Gold2 = readAs(gold2Path, gold2Schema, "넓힌 정답");
    const original = { a: doc(modelA, "a"), b: doc(modelB, "b") };
    const problems = [...checkPairs(v1Pairs, modelA, modelB), ...checkGold2(gold, original.a.extraction, original.b.extraction)];
    if (problems.length > 0) throw new ReconcileFileError(`정답이 문서와 맞지 않는다\n${problems.join("\n")}`);

    if (command === "draw2") {
      if (!values.seed || !values["out-spec"]) return out(USAGE), 2;
      const specPath = resolveOutputPath(values["out-spec"]);
      if (existsSync(specPath)) return out(`이미 있는 명세는 덮어쓰지 않는다: ${specPath}`), 2;
      const seed = Number(values.seed);
      const v1Cells = new Set([...v1Pairs.pairs.flatMap((pair) => [`a:${pair.a}`, `b:${pair.b}`]), ...v1Plants.plants.flatMap((plant) => plant.cells.map((cell) => `b:${cell.cell}`))]);
      const models = { a: modelA, b: modelB };
      const survives = (side: "a" | "b", target: string, planted: string): boolean => {
        const fact = original[side].extraction.facts.find((item) => item.id === target);
        if (!fact) return false;
        const after = extractDocument(applyChanges(models[side], original[side].extraction, [{ doc: side, target, original: fact.raw, planted }]), side, { titles: "own" });
        return sameCandidates(original[side].extraction, after) && after.facts.find((item) => item.id === target)?.raw === planted;
      };
      const drawn = drawPlants2({ gold, v1Cells, plan: PLAN2, seed, survives });
      const spec: Plant2Spec = { note: "넓힌 시험지에 심는 것 (밴드 U). 밴드 R 의 명세(kb-2025.plants.json, 문서 B 의 숫자 칸 16칸)는 그대로 같이 심고, 여기에는 새로 심는 자리만 적는다. 두 문서 모델의 사본에만 심는다. 자리는 넓힌 정답의 자리 가운데 종류별 조건에 맞는 것에서 씨앗 고정 난수로 뽑았다 (손으로 고르지 않았다). 서술에 심을 때는 그 숫자 덩어리의 글자만 바꾼다. 한 쌍·한 묶음에는 한 자리만 심는다. 밴드 R 의 정답 쌍·심은 칸과 이어진 자리, 원문에서 표기 자릿수가 다른 쌍·묶음에는 심지 않는다. rounding_control 은 오류가 아닌 대조군이다.", status: "러너 작성, 전문가 검토 전 (밴드 U, 2026-10-04)", seed, plan: PLAN2, ...drawn };
      writeJson(specPath, spec);
      out(JSON.stringify({ spec: specPath, sha256: sha(specPath), plants: spec.plants.length, errors: spec.plants.filter((plant) => plant.isError).length, skipped: spec.skipped }, null, 1));
      return 0;
    }

    if (!values.plants2 || !values.out) return out(USAGE), 2;
    const plants2Path = resolveInputPath(values.plants2);
    const v2Plants: Plant2Spec = readAs(plants2Path, plants2Schema, "넓힌 명세");
    const changes = allChanges(v1Plants, v2Plants);
    const outDir = resolveOutputPath(values.out);
    const planted = plantedPlaces(v1Plants.plants, v2Plants.plants);

    if (command === "build2") {
      mkdirSync(outDir, { recursive: true });
      const hashes: Record<string, string> = { pairs: sha(pairsPath), plants: sha(plantsPath), gold2: sha(gold2Path), plants2: sha(plants2Path), originalA: sha(resolveInputPath(values.a)), originalB: sha(resolveInputPath(values.b)) };
      const counts: Record<string, number> = {};
      for (const side of ["a", "b"] as const) {
        const mine = changes.filter((change) => change.doc === side);
        const model = side === "a" ? modelA : modelB;
        const exam = applyChanges(model, original[side].extraction, mine);
        const bad = checkExam2(model, exam, original[side].extraction, mine);
        if (bad.length > 0) throw new ExamError(bad.join("\n"));
        if (!sameCandidates(original[side].extraction, extractDocument(exam, side, { titles: "own" }))) throw new ExamError(`시험지 ${side} 의 값 후보 ID·갈래가 원본과 다르다`);
        const path = join(outDir, `${side}.document.json`);
        writeFileSync(path, `${JSON.stringify(exam)}\n`);
        hashes[`exam${side.toUpperCase()}`] = sha(path);
        counts[side] = mine.length;
      }
      writeJson(join(outDir, "hashes.json"), hashes);
      out(JSON.stringify({ out: outDir, checked: "명세 밖은 원본과 같다 (두 문서)", changedPlaces: counts, valueCandidates: { a: original.a.extraction.facts.length, b: original.b.extraction.facts.length }, sha256: hashes }, null, 1));
      return 0;
    }

    if (!values["exam-a"] || !values["exam-b"]) return out(USAGE), 2;
    const examPaths = { a: resolveInputPath(values["exam-a"]), b: resolveInputPath(values["exam-b"]) };
    const examModels = { a: readDoc(examPaths.a), b: readDoc(examPaths.b) };
    for (const side of ["a", "b"] as const) {
      const bad = checkExam2(side === "a" ? modelA : modelB, examModels[side], original[side].extraction, changes.filter((change) => change.doc === side));
      if (bad.length > 0) throw new ExamError(`시험지 ${side} 가 명세와 맞지 않는다\n${bad.join("\n")}`);
    }
    const a = doc(examModels.a, "a");
    const b = doc(examModels.b, "b");
    if (!sameCandidates(original.a.extraction, a.extraction) || !sameCandidates(original.b.extraction, b.extraction)) throw new ExamError("시험지의 값 후보 ID·갈래가 원본과 다르다");
    const units = loadUnits();
    const examHashes = { pairs: sha(pairsPath), plants: sha(plantsPath), gold2: sha(gold2Path), plants2: sha(plants2Path), examA: sha(examPaths.a), examB: sha(examPaths.b) };
    const isProse = (side: "a" | "b", id: string): boolean => (side === "a" ? a : b).extraction.facts.find((fact) => fact.id === id)?.bucket !== "table_cell";
    const proseSets = { a: new Set(a.extraction.facts.filter((fact) => fact.bucket !== "table_cell").map((fact) => fact.id)), b: new Set(b.extraction.facts.filter((fact) => fact.bucket !== "table_cell").map((fact) => fact.id)) };
    const prose = (side: "a" | "b", id: string): boolean => proseSets[side].has(id);
    void isProse;
    const gradeCross = (outcome: MethodOutcome): { table: Grade; prose: ReturnType<typeof gradeProse> } => ({ table: grade(v1Pairs.pairs, v1Plants.plants, outcome), prose: gradeProse(gold, planted, outcome.verdicts, prose) });

    if (command === "table2") {
      const list = (text: string | undefined): { name: string; dir: string }[] =>
        (text ?? "").split(",").filter((item) => item !== "").map((item) => {
          const [name, dir] = item.includes("=") ? (item.split("=") as [string, string]) : [item, item];
          return { name, dir: resolveOutputPath(dir) };
        });
      const saved = (dir: string, method: string | string[]): { outcome: MethodOutcome; notes: Record<string, unknown>; cost: CostSummary | null } => {
        const file = readJsonFile(join(dir, "outcome.json"), "실행 결과") as { synthetic?: string; method?: string; exam?: Record<string, string>; outcome: MethodOutcome; notes: Record<string, unknown>; cost: CostSummary | null };
        if (file.synthetic) throw new ReconcileFileError(`${dir}: 모의 실행의 결과다. 실측과 섞지 않는다`);
        if (!(Array.isArray(method) ? method : [method]).includes(file.method ?? "")) throw new ReconcileFileError(`${dir}: 방법 ${String(method)} 의 실행 결과가 아니다`);
        if (JSON.stringify(file.exam) !== JSON.stringify(examHashes)) throw new ReconcileFileError(`${dir}: 다른 시험지로 돌린 실행이다`);
        return file;
      };
      const d = list(values.d).map(({ name, dir }) => ({ name, grade: (readJsonFile(join(dir, "grade.json"), "채점 결과") as { grade: Grade }).grade }));
      const cross = (method: "d2" | "h2", item: { name: string; dir: string }, accept: string[] = [method]): D2Run => {
        const file = saved(item.dir, accept);
        const graded = gradeCross(file.outcome);
        const notes = file.notes as { tally: D2Run["tally"]; review?: { newPairs: NonNullable<D2Run["review"]>["newPairs"]; remaining: { answer: { state: string; reason?: string } }[]; rejected: Record<string, number>; before: { outcome: MethodOutcome; tally: D2Run["tally"] } } };
        const run: D2Run = { name: item.name, verdicts: file.outcome.verdicts, ...graded, tally: notes.tally, rejected: file.outcome.rejected, cost: file.cost };
        if (method === "h2" && notes.review) {
          const states: Record<string, number> = {};
          for (const entry of notes.review.remaining) {
            const name = entry.answer.state === "no_counterpart" ? `상대 없음 (${entry.answer.reason})` : { paired: "상대 칸을 답함", undecidable: "가를 수 없음", unanswered: "미응답", answer_rejected: "버려진 답" }[entry.answer.state as "paired"];
            states[name] = (states[name] ?? 0) + 1;
          }
          run.review = { newPairs: notes.review.newPairs, asked: notes.review.remaining.length, states, rejected: notes.review.rejected, before: { ...gradeCross(notes.review.before.outcome), tally: notes.review.before.tally } };
        }
        return run;
      };
      // --d2x·--ix: d2·i 와 답의 꼴·확인·채점이 같은 갈래(d3a·d3b·d4c·d4v, i3·i4)의 실행을 d2·i 의 열 옆에 나란히 놓는다.
      const iRuns: IRun[] = [...list(values.i), ...list(values.ix)].map(({ name, dir }) => {
        const file = saved(dir, ["i", "i3", "i4"]);
        const notes = file.notes as { doc: "a" | "b"; judgement: ReturnType<typeof judgeGroups>; tally: IRun["tally"] };
        return { name, doc: notes.doc, grade: gradeGroups(notes.doc, gold.groups, planted, notes.judgement), tally: notes.tally, groups: notes.judgement.groups, cost: file.cost };
      });
      const judgments: ProseJudgments | null = values.judgments ? readAs(resolveInputPath(values.judgments), proseJudgmentsSchema, "판정 기록") : null;
      const input = { d, d2: [...list(values.d2).map((item) => cross("d2", item)), ...list(values.d2x).map((item) => cross("d2", item, ["d3a", "d3b", "d4c", "d4v"]))], h2: list(values.h2).map((item) => cross("h2", item)), i: iRuns, gold, v1Pairs: v1Pairs.pairs, planted, facts: { a: a.extraction, b: b.extraction }, judgments };
      const md = renderProseTables(input);
      mkdirSync(outDir, { recursive: true });
      writeFileSync(join(outDir, "prose.md"), md);
      writeJson(join(outDir, "to-judge.json"), toJudge(input));
      out(md);
      return 0;
    }

    // ── run2 ───────────────────────────────────────────────────────────────
    const named = values.method;
    if (named !== "d2" && named !== "h2" && named !== "i" && named !== "d3a" && named !== "d3b" && named !== "i3" && named !== "d4c" && named !== "d4v" && named !== "i4" && named !== "lv") return out(USAGE), 2;
    const variant = named === "d3a" || named === "d3b" || named === "i3" || named === "d4c" || named === "d4v" || named === "i4" ? VARIANTS[named] : null;
    const method: "d2" | "h2" | "i" | "lv" = variant ? variant.base : (named as "d2" | "h2" | "i" | "lv");
    const chartVariant = CHART_VARIANTS.has(named);
    const bindNames = variant !== null && !chartVariant;
    if (chartVariant !== (values["chart-labels"] !== undefined)) throw new ReconcileFileError("--chart-labels <문서별 값 후보 ID → 라벨 글> 은 d4c·d4v·i4 에만, 그리고 반드시 준다");
    const chartLabels: Record<string, string> = {};
    if (chartVariant) {
      const loaded = readAs(resolveInputPath(values["chart-labels"] as string), z.record(z.enum(["a", "b"]), z.record(z.string(), z.string())), "도표 라벨");
      for (const [docId, labels] of Object.entries(loaded)) for (const [id, text] of Object.entries(labels ?? {})) chartLabels[`${docId}:${id}`] = text;
    }
    const bind = chartVariant ? { chartLabels } : bindNames ? { bindNames: true } : {};
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
    const dry = new LlmClient({ config, providers: { mock: new MockProvider(), anthropic: new AnthropicProvider(), openai: new OpenAIProvider(), gemini: new GeminiProvider() }, prompts, log: { write: () => undefined } });
    const estimate = { calls: 0, inputTokens: 0, imageInputTokens: 0, expectedOutputTokens: 0, expectedCostUsd: 0, costUpperBoundUsd: 0, maxInputTokensPerCall: 0 };
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
    const failures: { id: string; kind: string }[] = [];
    const callIds: string[] = [];
    async function ask<T>(store: JsonlUnitStore<T>, planned: Planned<T>, mockText: string): Promise<T | null> {
      const tokens = conservativeTokens(JSON.stringify(planned.request.vars ?? {}));
      const report = dry.dryRun(planned.request);
      const input = Math.max(tokens, report.estimate.inputTokens) + (planned.imageTokens ?? 0);
      estimate.imageInputTokens += planned.imageTokens ?? 0;
      if (input > INPUT_GUARD_TOKENS) throw new ReconcileFileError(`${planned.id}: 입력 어림 ${input} 토큰이 한도 ${INPUT_GUARD_TOKENS} 을 넘는다. 요청을 만들지 않는다`);
      estimate.calls += 1;
      estimate.inputTokens += input;
      estimate.maxInputTokensPerCall = Math.max(estimate.maxInputTokensPerCall, input);
      estimate.expectedOutputTokens += planned.expectedOutput;
      estimate.expectedCostUsd += computeCostUsd({ inputTokens: input, outputTokens: planned.expectedOutput, cacheReadTokens: 0, cacheWriteTokens: 0 }, model!.pricing);
      estimate.costUpperBoundUsd += computeCostUsd({ inputTokens: input, outputTokens: report.estimate.maxOutputTokens, cacheReadTokens: 0, cacheWriteTokens: 0 }, model!.pricing);
      if (!client && !replayDir) return null;
      let result: LlmResult<T> | undefined = store.get(planned.id, planned.request);
      if (!result) {
        if (!client) throw new ReconcileFileError(`${planned.id}: 저장된 답에 없는 요청이다 (요청 글이 실측 때와 다르다). 다시 채점할 때는 모델을 부르지 않는다`);
        if (!live) mock.enqueueText(mockText);
        result = await client.call(planned.request);
        store.put(planned.id, planned.request, result);
        log(`[${named}] ${planned.id}: ${result.kind} (${result.meta.totalCostUsd} USD)`);
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
    const requestsDir = join(outDir, "requests");
    if (values["dry-run"]) mkdirSync(requestsDir, { recursive: true });
    const guard = (id: string, text: string, left: string[]): void => {
      if (left.length > 0) throw new ReconcileFileError(`${id}: 요청 글에 가리지 못한 값 후보 숫자가 있다 (${left.slice(0, 20).join(", ")}). 요청을 만들지 않는다`);
      if (values["dry-run"]) writeFileSync(join(requestsDir, `${id.replace(/[/|#:]/g, "__")}.txt`), `${text}\n`);
    };
    const notes: Record<string, unknown> = {};
    if (bindNames) {
      // 한 줄 묶기의 기록: 숫자뿐인 블록마다 고른 이름 블록(없으면 묶지 않음). 문서 모델에서만 고른다.
      const bindingOf = (side: Doc): { numberOnlyBlocks: number; bound: { number: string; name: string }[]; unbound: string[] } => {
        const picked = pickNameBlocks(side.model, side.extraction);
        return { numberOnlyBlocks: picked.bound.size + picked.unbound.length, bound: [...picked.bound].map(([number, name]) => ({ number, name })), unbound: picked.unbound };
      };
      notes.binding = { a: bindingOf(a), b: bindingOf(b) };
    }
    let outcome: MethodOutcome = { verdicts: [], intra: [], states: { a: {}, b: {} }, rejected: {} };
    let gradeFile: Record<string, unknown> = {};
    const withStates = (states: { a: Map<string, string>; b: Map<string, string> }): MethodOutcome["states"] => ({ a: Object.fromEntries([...states.a].filter(([, state]) => !state.startsWith("compared:"))), b: Object.fromEntries([...states.b].filter(([, state]) => !state.startsWith("compared:"))) });

    /** D2 의 요청: 문서 A 를 쪽 순서로 나누고(한도 안에 들 때까지) 조각마다 문서 B 전체를 싣는다. 방법 D 와 같은 나눔이다. */
    const d2Requests = (): { id: string; text: string }[] => {
      const wholeB = renderDocumentWithIds(b.model, b.extraction, { prefix: "b:", ...bind });
      const pagesA = [...new Set(a.model.blocks.map((block) => block.page))];
      const chunks = pageChunks(pagesA, (from, to) => conservativeTokens(renderDocumentWithIds(a.model, a.extraction, { prefix: "a:", pages: { from, to }, ...bind })) + conservativeTokens(wholeB) + 1500 <= INPUT_GUARD_TOKENS);
      notes.split = { a: chunks, b: "전체" };
      return chunks.map((chunk, index) => ({ id: `${named}/${index + 1}`, text: `${renderDocumentWithIds(a.model, a.extraction, { prefix: "a:", pages: chunk, ...bind })}\n\n${wholeB}` }));
    };
    /** 저장된 D2 의 답을 D2 와 같은 확인에 다시 넣는다. */
    const judgeD2 = (answers: z.infer<typeof pairPlacesSchema>[]): PlaceJudgement => judgePlacePairs(answers, a.context, b.context, units);

    if (method === "d2") {
      const store = new JsonlUnitStore(join(storeDir, "units.jsonl"), pairPlacesSchema);
      const requests = d2Requests();
      if (values["dry-run"]) {
        // 가림의 확인: (1) 요청 글에 값 후보가 남지 않는다 (2) 심기 전의 원본으로 조립한 요청 글과 글자까지 같다 (심은 값이 요청과 ID 에 새지 않는다)
        // (3) 서술 값 후보마다 ID 표시가 하나씩 실린다 (4) 칸 검수에 실릴 수 있는 모든 표의 한쪽 글에도 값 후보가 남지 않는다.
        const before = { a: renderDocumentWithIds(modelA, original.a.extraction, { prefix: "a:", ...bind }), b: renderDocumentWithIds(modelB, original.b.extraction, { prefix: "b:", ...bind }) };
        const after = { a: renderDocumentWithIds(a.model, a.extraction, { prefix: "a:", ...bind }), b: renderDocumentWithIds(b.model, b.extraction, { prefix: "b:", ...bind }) };
        if (before.a !== after.a || before.b !== after.b) throw new ReconcileFileError("원본으로 조립한 요청 글과 시험지로 조립한 요청 글이 다르다 (심은 값이 요청 글에 샌다)");
        const marks: Record<string, unknown> = {};
        for (const side of ["a", "b"] as const) {
          const shown = idsInText(after[side]);
          const wanted = (side === "a" ? a : b).extraction.facts.filter((fact) => fact.bucket !== "table_cell").map((fact) => fact.id);
          if (shown.length !== wanted.length || new Set(shown).size !== shown.length || wanted.some((id) => !shown.includes(id))) throw new ReconcileFileError(`문서 ${side}: 서술 값 후보 ${wanted.length}개와 요청 글의 ID 표시 ${shown.length}개가 맞지 않는다`);
          marks[side] = { proseCandidates: wanted.length, idMarks: shown.length };
        }
        const tableLeaks = [a, b].flatMap((side) => tablesWithValues(side.extraction).flatMap((grid) => unmaskedValues(renderTableSide(side.model, side.extraction, grid.id)).map((value) => `${side.extraction.docId}:${grid.id}: ${value}`)));
        if (tableLeaks.length > 0) throw new ReconcileFileError(`칸 검수에 실릴 표의 글에 가리지 못한 값 후보 숫자가 있다\n${tableLeaks.join("\n")}`);
        notes.maskCheck = { requests: requests.length, unmaskedValues: 0, sameAsOriginalDocuments: true, idMarks: marks, tablesRenderedForCellReview: { a: tablesWithValues(a.extraction).length, b: tablesWithValues(b.extraction).length } };
      }
      const answers: z.infer<typeof pairPlacesSchema>[] = [];
      for (const request of requests) {
        guard(request.id, request.text, unmaskedInIdDocument(request.text));
        const answer = await ask(store, { id: request.id, request: { ...base, prompt: variant ? variant.prompt : PROMPT_D2, vars: { documents: request.text }, schema: pairPlacesSchema, schemaName: "reconcile_pair_places", maxOutputTokens: cap(D2_OUTPUT_CAP) }, expectedOutput: EXPECTED_OUTPUT.d2 }, JSON.stringify({ pairs: [] }));
        if (answer) answers.push(answer);
      }
      const judgement = judgeD2(answers);
      const tallied = tallyPlaces(a.extraction, b.extraction, judgement);
      outcome = { verdicts: judgement.verdicts, intra: [], states: withStates(tallied.states), rejected: judgement.rejected };
      notes.tablePairs = tallied.tablePairs;
      notes.tally = tallied.tally;
      gradeFile = gradeCross(outcome);
    } else if (method === "lv") {
      // 그림 라벨: 쪽마다 번호표를 덮은 그림 한 장과 번호 목록. 그림과 번호표는 원본 PDF 와 원본 문서 모델에서 만든 것이다 (시험지 사본·정답·명세와 무관).
      const tagSchema = z.array(z.object({ page: z.number(), n: z.number(), bbox: z.array(z.number()), id: z.string(), ids: z.array(z.string()).optional() }));
      const only = values.pages ? new Set(values.pages.split(",").map((item) => item.trim())) : null;
      const multiplier = values["image-multiplier"] === undefined ? IMAGE_TOKEN_MULTIPLIER : Number(values["image-multiplier"]);
      if (!Number.isFinite(multiplier) || multiplier <= 0) throw new ReconcileFileError("--image-multiplier 는 0 보다 큰 수다");
      const store = new JsonlUnitStore(join(storeDir, "units.jsonl"), tagLabelsSchema);
      const answers: Record<"a" | "b", Record<string, TagAnswer>> = { a: {}, b: {} };
      const rejected: Record<string, number> = {};
      const pageNotes: Record<string, unknown>[] = [];
      for (const side of ["a", "b"] as const) {
        const tagsPath = values[`tags-${side}`];
        const imageDir = values[`tag-images-${side}`];
        if (!tagsPath || !imageDir) throw new ReconcileFileError("lv 에는 --tags-a, --tags-b, --tag-images-a, --tag-images-b 가 필요하다");
        const tags = readAs(resolveInputPath(tagsPath), tagSchema, "번호표 목록");
        const raws = new Map(original[side].extraction.facts.map((fact) => [fact.id, fact.raw]));
        const rawOf = (id: string): string => {
          const raw = raws.get(id);
          if (raw === undefined) throw new ReconcileFileError(`번호표의 ID ${side}:${id} 가 문서의 값 후보에 없다`);
          return raw;
        };
        for (const page of [...new Set(tags.map((tag) => tag.page))].sort((x, y) => x - y)) {
          if (only && !only.has(`${side}:${page}`)) continue;
          const pageTags = tags.filter((tag) => tag.page === page).sort((x, y) => x.n - y.n);
          const bytes = readFileSync(join(resolveInputPath(imageDir), `p${String(page).padStart(4, "0")}.png`));
          const imageSha = createHash("sha256").update(bytes).digest("hex");
          const id = `lv/${side}/p${page}@${imageSha.slice(0, 12)}`;
          const list = pageTags.map((tag) => String(tag.n)).join(", ");
          const size = pngSize(bytes);
          const expectedOutput = pageTags.length * LV_ANSWER_TOKENS + 300;
          // 모의 모델은 출력 상한이 작다. 이 확인은 실제 경로의 어림에만 뜻이 있다.
          if (values.provider !== "mock" && expectedOutput > cap(LV_OUTPUT_CAP) * 0.75) throw new ReconcileFileError(`${id}: 예상 출력 ${expectedOutput} 토큰이 출력 상한의 3/4 을 넘는다. 요청을 만들지 않는다`);
          guard(id, list, []);
          const answer = await ask(store, { id, request: { ...base, prompt: PROMPT_LV, vars: { tags: list }, schema: tagLabelsSchema, schemaName: "reconcile_label_chart_tags", maxOutputTokens: cap(LV_OUTPUT_CAP), attachments: [{ type: "image", base64: bytes.toString("base64"), mediaType: "image/png" }] }, expectedOutput, imageTokens: imageTokens(size, multiplier) }, JSON.stringify({ tags: [] }));
          const judged = judgeTagAnswers(answer, pageTags, rawOf);
          Object.assign(answers[side], judged.answers);
          for (const [name, count] of Object.entries(judged.rejected)) rejected[name] = (rejected[name] ?? 0) + count;
          pageNotes.push({ doc: side, page, tags: pageTags.length, image: { sha256: imageSha, ...size, tokensEstimate: imageTokens(size, multiplier) }, answered: pageTags.length - judged.unanswered.length, unanswered: judged.unanswered, rejected: judged.rejected });
        }
      }
      notes.vision = { imageTokenMultiplier: multiplier, pages: pageNotes };
      outcome = { verdicts: [], intra: [], states: { a: {}, b: {} }, rejected };
      if (!values["dry-run"]) {
        writeJson(join(outDir, "tag-answers.json"), answers);
        writeJson(join(outDir, "vision-labels.json"), { a: visionLabels(answers.a), b: visionLabels(answers.b) });
      }
    } else if (method === "h2") {
      // H2: 저장된 D2 의 답(읽기만) → 코드의 셈 → 칸 검수. 요청은 D2 의 답과 문서에서만 조립한다. 정답과 명세는 채점에서만 쓴다.
      if (!values["d2-run"]) throw new ReconcileFileError("h2 에는 --d2-run <방법 d2 의 실제 실행 출력 디렉터리> 가 필요하다 (d2 를 다시 부르지 않는다)");
      const d2Dir = resolveOutputPath(values["d2-run"]);
      const d2Saved = readJsonFile(join(d2Dir, "outcome.json"), "방법 d2 의 실행 결과") as { synthetic?: string; method?: string; exam?: Record<string, string>; outcome: MethodOutcome };
      if (d2Saved.method !== "d2" || (d2Saved.synthetic !== undefined) !== (values.provider === "mock")) throw new ReconcileFileError(`${d2Dir}: 방법 d2 의 ${values.provider === "mock" ? "모의" : "실제"} 실행 결과가 아니다`);
      if (JSON.stringify(d2Saved.exam) !== JSON.stringify(examHashes)) throw new ReconcileFileError(`${d2Dir}: 다른 시험지로 돌린 실행이다`);
      const d2Answers = readFileSync(join(d2Dir, "units.jsonl"), "utf8").split("\n").filter((line) => line.trim() !== "").map((line) => JSON.parse(line) as { unitId: string; result: { kind: string; data?: unknown } }).filter((entry) => entry.result.kind === "success").sort((x, y) => x.unitId.localeCompare(y.unitId, "en", { numeric: true })).map((entry) => pairPlacesSchema.parse(entry.result.data));
      const d2Base = judgeD2(d2Answers);
      const key = (verdict: { a: string; b: string; outcome?: string }): string => `${verdict.a}|${verdict.b}|${verdict.outcome}`;
      if (d2Base.verdicts.map(key).sort().join("\n") !== d2Saved.outcome.verdicts.map(key).sort().join("\n")) throw new ReconcileFileError(`${d2Dir}: 저장된 답을 다시 확인한 결과가 그 실행의 outcome.json 과 다르다`);
      const store = new JsonlUnitStore(join(storeDir, "review-cells.jsonl"), reviewCells2Schema);
      const reviews: CellReview2Judgement[] = [];
      const requests: { id: string; remaining: number; paired: number }[] = [];
      const sideA = { document: a.model, extraction: a.extraction };
      const sideB = { document: b.model, extraction: b.extraction };
      for (const plan of planCellReviews2(a.extraction, b.extraction, d2Base.verdicts, MAX_REMAINING_PER_REQUEST)) {
        const text = renderCellReviewRequest(sideA, sideB, plan);
        guard(plan.id, text, unmaskedInCellReview(text));
        const expectedOutput = plan.remaining.length * REVIEW_ANSWER_TOKENS + 300;
        if (expectedOutput > cap(REVIEW_OUTPUT_CAP) * 0.75) throw new ReconcileFileError(`${plan.id}: 예상 출력 ${expectedOutput} 토큰이 출력 상한의 3/4 을 넘는다. 요청을 만들지 않는다`);
        requests.push({ id: plan.id, remaining: plan.remaining.length, paired: plan.paired.length });
        const answer = await ask(store, { id: plan.id, request: { ...base, prompt: PROMPT_REVIEW_CELLS_2, vars: { tables: text }, schema: reviewCells2Schema, schemaName: "reconcile_review_cells_2", maxOutputTokens: cap(REVIEW_OUTPUT_CAP) }, expectedOutput }, JSON.stringify({ answers: [] }));
        reviews.push(judgeCellReview2(answer, plan, a.context, b.context, units));
      }
      const result = review2Outcome(a.extraction, b.extraction, d2Base, reviews);
      outcome = result.after.outcome;
      notes.d2Run = d2Dir;
      notes.cellRequests = requests;
      notes.tablePairs = result.tablePairs;
      notes.tally = result.after.tally;
      notes.review = { newPairs: result.newPairs, remaining: result.remaining, rejected: result.rejected, before: { outcome: result.before.outcome, tally: result.before.tally } };
      gradeFile = { ...gradeCross(outcome), before: gradeCross(result.before.outcome) };
    } else {
      // I: 문서 하나를 가려서 통째로. 한도를 넘으면 쪽 순서로 나눈다 (그러면 조각을 넘는 묶음은 찾지 못한다).
      const side = values.doc === "a" ? a : values.doc === "b" ? b : null;
      if (!side) throw new ReconcileFileError("i 에는 --doc <a|b> 가 필요하다");
      const docId = side.extraction.docId as "a" | "b";
      const pages = [...new Set(side.model.blocks.map((block) => block.page))];
      const chunks = pageChunks(pages, (from, to) => conservativeTokens(renderDocumentWithIds(side.model, side.extraction, { prefix: "", pages: { from, to }, ...bind })) + 1500 <= INPUT_GUARD_TOKENS);
      notes.doc = docId;
      notes.split = chunks;
      if (values["dry-run"]) {
        const source = docId === "a" ? { model: modelA, extraction: original.a.extraction } : { model: modelB, extraction: original.b.extraction };
        if (renderDocumentWithIds(source.model, source.extraction, { prefix: "", ...bind }) !== renderDocumentWithIds(side.model, side.extraction, { prefix: "", ...bind })) throw new ReconcileFileError("원본으로 조립한 요청 글과 시험지로 조립한 요청 글이 다르다 (심은 값이 요청 글에 샌다)");
        notes.maskCheck = { requests: chunks.length, unmaskedValues: 0, sameAsOriginalDocument: true };
      }
      const store = new JsonlUnitStore(join(storeDir, "units.jsonl"), groupPlacesSchema);
      const answers: z.infer<typeof groupPlacesSchema>[] = [];
      for (const [index, chunk] of chunks.entries()) {
        const id = `${named}/${docId}/${index + 1}`;
        const text = renderDocumentWithIds(side.model, side.extraction, { prefix: "", ...(chunks.length > 1 ? { pages: chunk } : {}), ...bind });
        guard(id, text, unmaskedInIdDocument(text));
        const answer = await ask(store, { id, request: { ...base, prompt: PROMPT_I, vars: { document: text }, schema: groupPlacesSchema, schemaName: "reconcile_group_places", maxOutputTokens: cap(I_OUTPUT_CAP) }, expectedOutput: Math.ceil(EXPECTED_OUTPUT.i[docId] / chunks.length) }, JSON.stringify({ groups: [] }));
        if (answer) answers.push(answer);
      }
      const judgement = judgeGroups(answers, side.context, units);
      const tallied = tallyGroups(side.extraction, judgement);
      outcome = { verdicts: [], intra: judgement.groups.flatMap((group) => group.verdicts.map((verdict) => ({ ...verdict, doc: docId }))), states: { a: {}, b: {} }, rejected: judgement.rejected };
      notes.judgement = judgement;
      notes.tally = tallied.tally;
      gradeFile = { groups: gradeGroups(docId, gold.groups, planted, judgement) };
    }

    if (values["dry-run"]) {
      const report = { dryRun: true, method: named, route, model: model.key, note: "공급사를 부르지 않았다. 입력 토큰은 보수적인 글자 수 어림(한글 1자 1토큰, 그 밖 1자 0.6토큰)과 호출 계층의 어림 중 큰 값이다 (그림이 있으면 그림 토큰의 어림 imageInputTokens 를 더한 값). 예상 출력은 방법마다 둔 가정이다. 캐시 할인은 넣지 않았다. 비용 상한은 요청마다 출력 상한을 다 쓸 때의 값이고 재시도는 들어 있지 않다.", inputGuardTokens: INPUT_GUARD_TOKENS, ...estimate, expectedCostUsd: roundUsd(estimate.expectedCostUsd), costUpperBoundUsd: roundUsd(estimate.costUpperBoundUsd), notes };
      writeJson(join(outDir, "dry-run.json"), report);
      out(`[${named}] 모델 ${model.key} · 호출 ${estimate.calls} · 입력 어림 ${estimate.inputTokens}${estimate.imageInputTokens > 0 ? ` (그중 그림 ${estimate.imageInputTokens})` : ""} (요청 최대 ${estimate.maxInputTokensPerCall}) · 예상 출력 ${estimate.expectedOutputTokens} · 예상 비용 ${report.expectedCostUsd} USD · 비용 상한 ${report.costUpperBoundUsd} USD`);
      return 0;
    }
    const records = readCallLog(logPath).filter((record) => callIds.includes(record.callId));
    const byResultKind: Record<string, number> = {};
    for (const record of records) byResultKind[record.resultKind] = (byResultKind[record.resultKind] ?? 0) + 1;
    const total = (record: (typeof records)[number]): number => (record.inputTokens ?? 0) + (record.cacheReadTokens ?? 0) + (record.cacheWriteTokens ?? 0);
    const cost: CostSummary = { calls: records.length, inputTokens: records.reduce((sum, record) => sum + total(record), 0), cacheReadTokens: records.reduce((sum, record) => sum + (record.cacheReadTokens ?? 0), 0), outputTokens: records.reduce((sum, record) => sum + (record.outputTokens ?? 0), 0), costUsd: roundUsd(records.reduce((sum, record) => sum + (record.costUsd ?? 0), 0)), maxInputTokens: records.reduce((max, record) => Math.max(max, total(record)), 0), byResultKind };
    const synthetic = values.provider === "mock" ? "모의 실행 (빈 답을 돌려주는 synthetic 응답기). 결과는 모델의 성능과 무관하다." : null;
    writeJson(join(outDir, "outcome.json"), { ...(synthetic ? { synthetic } : {}), method: named, title: TITLES[named], provider: values.provider ?? null, ...(replayDir ? { replayedFrom: replayDir } : {}), route, model: model.key, exam: examHashes, notes, failures, cost, outcome });
    writeJson(join(outDir, "grade.json"), { ...(synthetic ? { synthetic } : {}), method: named, ...gradeFile });
    const brief = (graded: Record<string, unknown>): Record<string, unknown> => {
      const cross = graded as { table?: Grade; prose?: ReturnType<typeof gradeProse>; groups?: ReturnType<typeof gradeGroups> };
      return { ...(cross.table ? { table: `참 일치 ${cross.table.clean.match}/${cross.table.clean.total}, 잡음 ${cross.table.errors.caught}/${cross.table.errors.total}, 거짓 일치 ${cross.table.errors.falseMatch}, 조용히 ${cross.table.errors.silent}` } : {}), ...(cross.prose ? { prose: `손대지 않은 쌍 일치 ${cross.prose.clean.match}/${cross.prose.clean.total}, 오류 잡음 ${cross.prose.errors.caught}/${cross.prose.errors.total}, 거짓 일치 ${cross.prose.errors.falseMatch}, 조용히 ${cross.prose.errors.silent}, 정답 밖 불일치 ${cross.prose.outsideGold.mismatch.length}` } : {}), ...(cross.groups ? { groups: `찾음 ${cross.groups.groups.full}/${cross.groups.groups.total} (일부 ${cross.groups.groups.partial}), 어긋남 잡음 ${cross.groups.errors.caught}/${cross.groups.errors.total}, 그 밖의 불일치 ${cross.groups.otherMismatches.length}` } : {}) };
    };
    out(JSON.stringify({ ...(synthetic ? { synthetic } : {}), method: named, failures, cost, spentUsd: budget ? budget.spent({ module: TAGS.module }) : null, ...brief(gradeFile), rejected: outcome.rejected, files: { outcome: join(outDir, "outcome.json"), grade: join(outDir, "grade.json") } }, null, 1));
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
