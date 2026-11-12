import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { z } from "zod";
import { ReconcileFileError, documentSchema, loadUnits, readJsonFile, resolveInputPath, resolveOutputPath } from "../adapters/files.js";
import { extractDocument, type DocModel } from "../domain/facts.js";
import { pairsSchema, plantsSchema } from "./exam.js";
import { plants2Schema } from "./exam2.js";
import type { Verdict } from "./methods.js";
import { proseJudgmentsSchema, type ProseJudgments } from "./prose-report.js";
import { SHIFT_RULE, checkShifts, suspectKeys } from "./recover.js";
import { renderRecover, reportRun, scoreStage, sheetOf, toJudge, unionVerdicts, type RecoverAnswers, type RunReport } from "./recover-report.js";

// 밴드 Y (MARC-TICK-28) 의 채점 명령: 열 밀림 검사의 채점(shiftr)과 방법 R 의 표(tabler). 모델을 부르지 않는다.
// 정답 쌍·명세·판정 기록을 읽는 것은 여기와 `recover-report.ts` 뿐이다. 저장된 실행은 읽기만 한다.

type Out = (line: string) => void;

const USAGE = `사용법 (cd ts && pnpm -F reconcile exec tsx src/compare/cli8.ts <명령> ...)
  shiftr --a <문서 A 원본> --b <문서 B 원본> --exam-a <시험지 A> --exam-b <시험지 B> --pairs <정답 쌍> --plants <명세> --plants2 <넓힌 명세> [--judgments <판정 기록>] --runs <이름=저장된 D 계열 실행,..> --out <디렉터리>
      저장된 실행마다 입력(받아들여진 짝, 버려진 답, 빠진 정답 쌍)과 열 밀림 검사의 성적(두 기준)을 낸다. <out>/shift.md, to-judge.json.
  tabler 같은 인자, --runs <이름=저장된 실행=방법 R 의 출력,..>
      방법 R 의 전·검사만·뒤, 지목의 재현율, 새 짝, 세 실행을 합친 대조군, 셈, 판정 목록을 낸다. <out>/recover.md, to-judge.json, grades.json.
이름은 「방법-번호」 꼴이다 (예: D3a-2). 같은 방법의 실행을 합쳐 대조군을 낸다. --out 은 반드시 준다. 상대 경로는 ts/ 기준이다.`;

function readAs<T>(path: string, schema: z.ZodType<T>, what: string): T {
  const parsed = schema.safeParse(readJsonFile(path, what));
  if (!parsed.success) throw new ReconcileFileError(`${path}: ${what} 형식이 맞지 않는다\n${z.prettifyError(parsed.error)}`);
  return parsed.data;
}

function readDoc(path: string): DocModel {
  const parsed = documentSchema.safeParse(readJsonFile(path, "문서 JSON"));
  if (!parsed.success) throw new ReconcileFileError(`${path}: 문서 JSON 형식이 맞지 않는다`);
  return parsed.data as DocModel;
}

const verdictSchema = z.object({ a: z.string(), b: z.string(), outcome: z.enum(["match", "mismatch", "needs_explanation"]), reason: z.string(), valuesDiffer: z.boolean().nullable() });
const storedSchema = z.object({ outcome: z.object({ verdicts: z.array(verdictSchema), rejected: z.record(z.string(), z.number()) }).loose() }).loose();
const costSchema = z.object({ cost: z.object({ calls: z.number(), inputTokens: z.number(), cacheReadTokens: z.number(), outputTokens: z.number(), costUsd: z.number(), maxInputTokens: z.number() }).loose(), synthetic: z.string().optional() }).loose();

export async function main(argv: string[], out: Out = console.log, log: Out = console.error): Promise<number> {
  const [command, ...rest] = argv;
  let values;
  try {
    values = parseArgs({ args: rest, options: { a: { type: "string" }, b: { type: "string" }, "exam-a": { type: "string" }, "exam-b": { type: "string" }, pairs: { type: "string" }, plants: { type: "string" }, gold2: { type: "string" }, plants2: { type: "string" }, judgments: { type: "string" }, runs: { type: "string" }, out: { type: "string" } } }).values;
  } catch (error) {
    out(`${(error as Error).message}\n\n${USAGE}`);
    return 2;
  }
  try {
    if ((command !== "shiftr" && command !== "tabler") || !values["exam-a"] || !values["exam-b"] || !values.pairs || !values.plants || !values.plants2 || !values.runs || !values.out) return out(USAGE), 2;
    const a = extractDocument(readDoc(resolveInputPath(values["exam-a"])), "a", { titles: "own" });
    const b = extractDocument(readDoc(resolveInputPath(values["exam-b"])), "b", { titles: "own" });
    const units = loadUnits();
    const pairs = readAs(resolveInputPath(values.pairs), pairsSchema, "정답 쌍");
    const plants = readAs(resolveInputPath(values.plants), plantsSchema, "심은 오류 명세");
    const plants2 = readAs(resolveInputPath(values.plants2), plants2Schema, "넓힌 명세");
    const judgments: ProseJudgments | null = values.judgments ? readAs(resolveInputPath(values.judgments), proseJudgmentsSchema, "판정 기록") : null;
    const sheet = sheetOf(pairs.pairs, plants.plants, plants2.plants.filter((plant) => plant.isError && plant.doc === "a").map((plant) => plant.target), a, b, judgments);
    const outDir = resolveOutputPath(values.out);
    mkdirSync(outDir, { recursive: true });

    const runs: RunReport[] = [];
    for (const spec of values.runs.split(",")) {
      const [name, storedDir, recoverDir] = spec.split("=");
      if (!name || !storedDir || (command === "tabler" && !recoverDir)) return out(USAGE), 2;
      const stored = readAs(join(resolveInputPath(storedDir), "outcome.json"), storedSchema, "저장된 실행의 결과");
      let answers: RecoverAnswers;
      let cost: RunReport["cost"] = null;
      if (command === "tabler") {
        const dir = resolveInputPath(recoverDir as string);
        answers = readJsonFile(join(dir, "answers.json"), "방법 R 의 답") as RecoverAnswers;
        if ("synthetic" in (answers as object)) throw new ReconcileFileError(`${dir}: 모의 실행의 출력이다. 실측의 표에 섞지 않는다`);
        if (JSON.stringify(answers.before.verdicts) !== JSON.stringify(stored.outcome.verdicts)) throw new ReconcileFileError(`${dir}: 방법 R 의 「전」이 저장된 실행의 짝과 다르다`);
        const outcomePath = join(dir, "outcome.json");
        if (existsSync(outcomePath)) {
          const parsed = readAs(outcomePath, costSchema, "방법 R 의 결과");
          cost = parsed.cost.calls > 0 ? { calls: parsed.cost.calls, inputTokens: parsed.cost.inputTokens, cacheReadTokens: parsed.cost.cacheReadTokens, outputTokens: parsed.cost.outputTokens, costUsd: parsed.cost.costUsd, maxInputTokens: parsed.cost.maxInputTokens } : null;
        }
      } else {
        // 검사만: 저장된 실행의 짝에 검사를 붙인 것 (호출 0). 다시 묻기의 칸은 비워 둔다 (표에 싣지 않는다).
        const verdicts: Verdict[] = stored.outcome.verdicts;
        const suspects = suspectKeys(checkShifts(verdicts, a, b, units, SHIFT_RULE));
        const kept = verdicts.filter((verdict) => !suspects.has(`${verdict.a}|${verdict.b}`));
        const empty = { total: 0, states: {} };
        answers = { run: storedDir, shiftRule: SHIFT_RULE, nominations: [], candidates: [], skipped: [], tableReview: null, requests: [], rejected: {}, before: { verdicts }, checked: { verdicts: kept, dropped: verdicts.filter((verdict) => suspects.has(`${verdict.a}|${verdict.b}`)) }, after: { verdicts: kept, tally: { a: empty, b: empty } }, added: [], dropped: [], repeated: 0, conflicts: [], stillExpected: [] };
      }
      runs.push(reportRun({ name, sheet, answers, rejectedBefore: stored.outcome.rejected, cost, a, b, units }));
    }
    const methods = [...new Set(runs.map((run) => run.name.split("-")[0] as string))];
    const unions = methods.map((method) => {
      const mine = runs.filter((run) => run.name.split("-")[0] === method);
      return { name: `${method} ${mine.map((run) => run.name.split("-")[1]).join("+")}`, before: scoreStage(sheet, unionVerdicts(mine.map((run) => run.answers.before.verdicts))), after: scoreStage(sheet, unionVerdicts(mine.map((run) => run.answers.after.verdicts))) };
    });
    const fileName = command === "shiftr" ? "shift.md" : "recover.md";
    writeFileSync(join(outDir, fileName), renderRecover(sheet, runs, unions, command === "shiftr" ? "inputs" : "all"));
    writeFileSync(join(outDir, "to-judge.json"), `${JSON.stringify(toJudge(sheet, runs), null, 1)}\n`);
    const plain = (run: RunReport): Record<string, unknown> => ({ name: run.name, before: run.before, checked: run.checked, after: run.after, shift: { either: { ...run.shift.either, checks: undefined }, both: { ...run.shift.both, checks: undefined } }, missing: Object.fromEntries(run.missing), recall: run.recall, recoveredGold: run.recoveredGold, lostGold: run.lostGold, added: run.added, cost: run.cost });
    writeFileSync(join(outDir, "grades.json"), `${JSON.stringify({ runs: runs.map(plain), unions }, null, 1)}\n`);
    out(JSON.stringify({ files: [join(outDir, fileName), join(outDir, "to-judge.json"), join(outDir, "grades.json")], runs: runs.length, toJudge: toJudge(sheet, runs).length, unjudged: toJudge(sheet, runs).filter((item) => !sheet.judged.has(`${item.a}|${item.b}`)).length }, null, 1));
    return 0;
  } catch (error) {
    if (error instanceof ReconcileFileError) {
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
