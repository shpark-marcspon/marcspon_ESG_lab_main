import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { z } from "zod";
import { ReconcileFileError, documentSchema, readJsonFile, resolveInputPath, resolveOutputPath } from "../adapters/files.js";
import { extractDocument, type DocModel, type Extraction } from "../domain/facts.js";
import { listTargets, tallyAsk, type AskState, type AskTally, type AskVerdict, type Target } from "./ask.js";
import { askJudgmentsSchema, coverage, gradeAsk, renderAskTables, toJudgeAsk, type AskJudgments, type AskRun, type D2Column } from "./ask-report.js";
import { pairsSchema, plantsSchema } from "./exam.js";
import { gold2Schema, plants2Schema } from "./exam2.js";
import type { PairStatus } from "./grade.js";
import { TallyError } from "./hybrid.js";
import { plantedPlaces, type CostSummary } from "./prose-report.js";

// 밴드 X (MARC-TICK-27) 의 채점 쪽 명령: 대상 목록과 정답의 겹침(coverq), 저장된 실행의 표(tableq). 모델을 부르지 않는다.
// 정답과 명세는 여기서만 읽는다. 대상 목록과 요청 조립은 `cli5.ts` 다.

type Out = (line: string) => void;

const USAGE = `사용법 (cd ts && pnpm -F reconcile exec tsx src/compare/cli6.ts <명령> ...)
  coverq --a <문서 A 원본> --b <문서 B 원본> --exam-a <시험지 A> --exam-b <시험지 B> --chart-labels <코드 라벨> --pairs <밴드 R 의 정답 쌍> --plants <밴드 R 의 명세> --gold2 <넓힌 정답> --plants2 <넓힌 명세> --out <디렉터리>
      대상 목록에 정답(서술 쌍, 서술에 심은 오류)의 자리가 몇 개 들어 있는지 센다 (호출 0). <out>/coverage.json
  tableq --runs <이름=방법 q 의 실제 실행 출력,..> [--d2 <이름=밴드 U 의 d2 실행 출력,..>] [--judgments <판정 기록>] (coverq 와 같은 인자) --out <디렉터리>
      저장된 실행들을 다시 채점해 나란히 놓는다. <out>/ask.md, to-judge.json, grades.json
--out 은 반드시 준다. 상대 경로는 ts/ 기준이다.`;

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

export async function main(argv: string[], out: Out = console.log, log: Out = console.error): Promise<number> {
  const [command, ...rest] = argv;
  let values;
  try {
    values = parseArgs({ args: rest, options: { a: { type: "string" }, b: { type: "string" }, "exam-a": { type: "string" }, "exam-b": { type: "string" }, "chart-labels": { type: "string" }, pairs: { type: "string" }, plants: { type: "string" }, gold2: { type: "string" }, plants2: { type: "string" }, runs: { type: "string" }, d2: { type: "string" }, judgments: { type: "string" }, out: { type: "string" } } }).values;
  } catch (error) {
    out(`${(error as Error).message}\n\n${USAGE}`);
    return 2;
  }
  try {
    if ((command !== "coverq" && command !== "tableq") || !values.a || !values.b || !values["exam-a"] || !values["exam-b"] || !values["chart-labels"] || !values.pairs || !values.plants || !values.gold2 || !values.plants2 || !values.out) return out(USAGE), 2;
    const paths = { examA: resolveInputPath(values["exam-a"]), examB: resolveInputPath(values["exam-b"]), chartLabels: resolveInputPath(values["chart-labels"]) };
    const extract = (path: string, docId: "a" | "b"): { model: DocModel; extraction: Extraction } => {
      const model = readDoc(path);
      return { model, extraction: extractDocument(model, docId, { titles: "own", kinds: true }) };
    };
    const exam = { a: extract(paths.examA, "a"), b: extract(paths.examB, "b") };
    const loaded = readAs(paths.chartLabels, z.record(z.enum(["a", "b"]), z.record(z.string(), z.string())), "도표 라벨");
    const chartLabels: Record<string, string> = {};
    for (const [docId, labels] of Object.entries(loaded)) for (const [id, text] of Object.entries(labels ?? {})) chartLabels[`${docId}:${id}`] = text;
    const targets: Target[] = [...listTargets(exam.a, "a", chartLabels), ...listTargets(exam.b, "b", chartLabels)];
    const v1Pairs = readAs(resolveInputPath(values.pairs), pairsSchema, "정답 쌍");
    const v1Plants = readAs(resolveInputPath(values.plants), plantsSchema, "심은 오류 명세");
    const gold = readAs(resolveInputPath(values.gold2), gold2Schema, "넓힌 정답");
    const v2Plants = readAs(resolveInputPath(values.plants2), plants2Schema, "넓힌 명세");
    const facts = { a: exam.a.extraction, b: exam.b.extraction };
    const cover = coverage(gold, v2Plants.plants, targets, facts);
    const outDir = resolveOutputPath(values.out);
    mkdirSync(outDir, { recursive: true });

    if (command === "coverq") {
      writeJson(join(outDir, "coverage.json"), cover);
      out(JSON.stringify({ targets: targets.length, prosePairsReachable: `${cover.reachable}/${cover.total}`, goldPlacesThatAreTargets: `${cover.places.targets}/${cover.places.total}`, missing: Object.fromEntries(Object.entries(cover.places.missing).map(([why, ids]) => [why, ids.length])), plants: cover.plants.map((plant) => `${plant.plant}:${plant.branch ?? "대상 아님"}`) }, null, 1));
      return 0;
    }

    const list = (text: string | undefined): { name: string; dir: string }[] =>
      (text ?? "").split(",").filter((item) => item !== "").map((item) => {
        const [name, dir] = item.split("=") as [string, string];
        return { name, dir: resolveOutputPath(dir) };
      });
    const inputs = { examA: sha(paths.examA), examB: sha(paths.examB), chartLabels: sha(paths.chartLabels) };
    const planted = plantedPlaces(v1Plants.plants, v2Plants.plants);
    const proseSets = { a: new Set(facts.a.facts.filter((fact) => fact.bucket !== "table_cell").map((fact) => fact.id)), b: new Set(facts.b.facts.filter((fact) => fact.bucket !== "table_cell").map((fact) => fact.id)) };
    const runs = list(values.runs).map(({ name, dir }) => {
      const file = readJsonFile(join(dir, "outcome.json"), "실행 결과") as { synthetic?: string; method?: string; inputs?: Record<string, string>; targets: Target[]; states: Record<string, AskState>; verdicts: AskVerdict[]; rejected: Record<string, number>; tally: AskTally; cost: CostSummary | null };
      if (file.synthetic) throw new ReconcileFileError(`${dir}: 모의 실행의 결과다. 실측과 섞지 않는다`);
      if (file.method !== "q") throw new ReconcileFileError(`${dir}: 방법 q 의 실행 결과가 아니다`);
      if (JSON.stringify(file.inputs) !== JSON.stringify(inputs)) throw new ReconcileFileError(`${dir}: 다른 시험지나 다른 라벨로 돌린 실행이다`);
      if (JSON.stringify(file.targets) !== JSON.stringify(targets)) throw new ReconcileFileError(`${dir}: 그 실행의 대상 목록이 지금의 대상 목록과 다르다`);
      const states = new Map(Object.entries(file.states));
      const tally = tallyAsk(targets, states);
      if (JSON.stringify(tally) !== JSON.stringify(file.tally)) throw new ReconcileFileError(`${dir}: 저장된 셈이 답에서 다시 낸 셈과 다르다`);
      const run: AskRun = { name, targets, states, verdicts: file.verdicts, rejected: file.rejected, tally, cost: file.cost };
      return { run, grade: gradeAsk(run, gold, v1Pairs.pairs, planted, v2Plants.plants, (side, id) => proseSets[side].has(id)) };
    });
    if (runs.length === 0) return out(USAGE), 2;
    const d2: D2Column[] = list(values.d2).map(({ name, dir }) => {
      const file = readJsonFile(join(dir, "grade.json"), "채점 결과") as { synthetic?: string; prose: { rows: { pair: string; status: PairStatus }[] } };
      if (file.synthetic) throw new ReconcileFileError(`${dir}: 모의 실행의 결과다. 실측과 섞지 않는다`);
      return { name, rows: new Map(file.prose.rows.map((row) => [row.pair, row.status])) };
    });
    const judgments: AskJudgments | null = values.judgments ? readAs(resolveInputPath(values.judgments), askJudgmentsSchema, "판정 기록") : null;
    const md = renderAskTables({ runs, d2, coverage: cover, gold, judgments });
    writeFileSync(join(outDir, "ask.md"), md);
    writeJson(join(outDir, "to-judge.json"), toJudgeAsk(runs));
    writeJson(join(outDir, "grades.json"), runs.map(({ run, grade }) => ({ name: run.name, tally: run.tally, plants: grade.plants, goldTargets: grade.goldTargets, prose: { clean: grade.prose.clean, control: grade.prose.control, errors: grade.prose.errors, rows: grade.prose.rows }, outside: grade.outside, inTableGold: grade.inTableGold })));
    out(md);
    return 0;
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
