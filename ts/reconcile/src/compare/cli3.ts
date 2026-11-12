import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { z } from "zod";
import { ReconcileFileError, documentSchema, readJsonFile, resolveInputPath, resolveOutputPath } from "../adapters/files.js";
import { extractDocument, type DocModel, type Extraction } from "../domain/facts.js";
import { classifyKinds, isNonValue, type ValueKind } from "../domain/kinds.js";
import { normalizeText } from "../domain/notation.js";
import { ExamError, pairsSchema, plantsSchema, type GoldPairs, type PlantSpec } from "./exam.js";
import { gold2Schema, plants2Schema, type Gold2, type Plant2Spec } from "./exam2.js";
import { grade, type Grade } from "./grade.js";
import { checkKindGold, drawKindTargets, kindGoldSchema, type KindGold } from "./kind-exam.js";
import { KIND_KO, nonValuePlaces, reclassify, renderKindScore, scoreKinds, type Reclass } from "./kind-report.js";
import type { MethodOutcome, Verdict } from "./methods.js";
import type { GroupJudgement } from "./prose.js";
import { gradeGroups, gradeProse, plantedPlaces, proseJudgmentsSchema, renderProseTables, toJudge, type CostSummary, type D2Run, type IRun, type ProseJudgments } from "./prose-report.js";

// 밴드 V (MARC-TICK-25) 의 명령: 종류의 정답 대상 뽑기(draw-kinds), 종류 규칙의 채점(kinds), 저장된 답을 종류로 다시 가르기(reclass), 표(table3).
// 모델을 부르지 않는다. 실행(D3a·D3b·I3)은 `cli2.ts` 의 run2 다. 종류는 여기서만 쓴다: 요청 조립은 종류를 읽지 않는다.

type Out = (line: string) => void;

const USAGE = `사용법 (cd ts && pnpm -F reconcile exec tsx src/compare/cli3.ts <명령> ...)
  공통: --a <문서 A 원본> --b <문서 B 원본> --exam-a <시험지 A> --exam-b <시험지 B> --pairs <밴드 R 의 정답 쌍> --plants <밴드 R 의 명세> --gold2 <넓힌 정답> --plants2 <넓힌 명세> --out <디렉터리>
  draw-kinds --seed <n> --per-bucket <n>
      종류의 정답을 달 대상(서술 값 후보 전부 + 표의 글 칸 속·표의 숫자 칸의 표본)을 <out>/kind-targets.json 에 쓴다.
  kinds --kind-gold <종류의 정답>
      종류 규칙을 정답과 견줘 <out>/kinds.md, kinds.json 을 쓴다.
  reclass --d2 <이름=출력,..> [--h2 <이름=출력,..>] --i <이름=출력,..> --judgments <밴드 U 의 판정 기록>
      밴드 U 의 저장된 답을 종류와 함께 다시 본다: 걸러지는 것과 잘못 걸러지는 것. <out>/reclass.md, reclass.json.
  table3 --d2 <이름=출력,..> --d3a <..> --d3b <..> --ib <밴드 U 의 I(데이터북) 이름=출력,..> --i3 <..> [--judgments <판정 기록,판정 기록..>]
      저장된 실행들을 나란히 놓는다. <out>/named.md, to-judge.json.
--out 은 반드시 준다. 상대 경로는 ts/ 기준이다.`;

const sha = (path: string): string => createHash("sha256").update(readFileSync(path)).digest("hex");
const writeJson = (path: string, value: unknown): void => writeFileSync(path, `${JSON.stringify(value, null, 1)}\n`);
const table = (head: string[], rows: string[][]): string[] => [`| ${head.join(" | ")} |`, `|${head.map((_, index) => (index === 0 ? "---" : "---:")).join("|")}|`, ...rows.map((row) => `| ${row.join(" | ")} |`), ""];

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

const list = (text: string | undefined): { name: string; dir: string }[] =>
  (text ?? "").split(",").filter((item) => item !== "").map((item) => {
    const [name, dir] = item.includes("=") ? (item.split("=") as [string, string]) : [item, item];
    return { name, dir: resolveOutputPath(dir) };
  });

interface Saved {
  synthetic?: string;
  method?: string;
  exam?: Record<string, string>;
  outcome: MethodOutcome;
  notes: Record<string, unknown>;
  cost: CostSummary | null;
}

export async function main(argv: string[], out: Out = console.log, log: Out = console.error): Promise<number> {
  const [command, ...rest] = argv;
  let values;
  try {
    values = parseArgs({
      args: rest,
      options: { a: { type: "string" }, b: { type: "string" }, "exam-a": { type: "string" }, "exam-b": { type: "string" }, pairs: { type: "string" }, plants: { type: "string" }, gold2: { type: "string" }, plants2: { type: "string" }, out: { type: "string" }, seed: { type: "string" }, "per-bucket": { type: "string" }, "kind-gold": { type: "string" }, d2: { type: "string" }, h2: { type: "string" }, i: { type: "string" }, d3a: { type: "string" }, d3b: { type: "string" }, ib: { type: "string" }, i3: { type: "string" }, judgments: { type: "string" } },
    }).values;
  } catch (error) {
    out(`${(error as Error).message}\n\n${USAGE}`);
    return 2;
  }
  try {
    if (!["draw-kinds", "kinds", "reclass", "table3"].includes(command ?? "") || !values.a || !values.b || !values["exam-a"] || !values["exam-b"] || !values.pairs || !values.plants || !values.gold2 || !values.plants2 || !values.out) return out(USAGE), 2;
    const models = { a: readDoc(resolveInputPath(values.a)), b: readDoc(resolveInputPath(values.b)) };
    const examPaths = { a: resolveInputPath(values["exam-a"]), b: resolveInputPath(values["exam-b"]) };
    const exams = { a: readDoc(examPaths.a), b: readDoc(examPaths.b) };
    const pairsPath = resolveInputPath(values.pairs);
    const plantsPath = resolveInputPath(values.plants);
    const gold2Path = resolveInputPath(values.gold2);
    const plants2Path = resolveInputPath(values.plants2);
    const v1Pairs: GoldPairs = readAs(pairsPath, pairsSchema, "정답 쌍");
    const v1Plants: PlantSpec = readAs(plantsPath, plantsSchema, "심은 오류 명세");
    const gold: Gold2 = readAs(gold2Path, gold2Schema, "넓힌 정답");
    const v2Plants: Plant2Spec = readAs(plants2Path, plants2Schema, "넓힌 명세");
    const planted = plantedPlaces(v1Plants.plants, v2Plants.plants);
    const examHashes = { pairs: sha(pairsPath), plants: sha(plantsPath), gold2: sha(gold2Path), plants2: sha(plants2Path), examA: sha(examPaths.a), examB: sha(examPaths.b) };
    const outDir = resolveOutputPath(values.out);
    mkdirSync(outDir, { recursive: true });
    // 종류는 옵션을 켠 숫자 뽑기에서 읽는다. 원본 문서와 시험지 사본 둘 다에 붙여, 심은 값 때문에 종류가 달라지는 자리를 센다.
    const original = { a: extractDocument(models.a, "a", { titles: "own", kinds: true }), b: extractDocument(models.b, "b", { titles: "own", kinds: true }) };
    const exam = { a: extractDocument(exams.a, "a", { titles: "own", kinds: true }), b: extractDocument(exams.b, "b", { titles: "own", kinds: true }) };
    const kindOf = (doc: "a" | "b", id: string): ValueKind | undefined => exam[doc].kinds?.[id];
    const factOf = { a: new Map(exam.a.facts.map((fact) => [fact.id, fact])), b: new Map(exam.b.facts.map((fact) => [fact.id, fact])) };
    const raw = (doc: "a" | "b", id: string): string => factOf[doc].get(id)?.raw ?? "?";
    const shown = (doc: "a" | "b", id: string): string => `\`${id}\` ${raw(doc, id)} (${KIND_KO[kindOf(doc, id) as ValueKind]})`;
    const saved = (dir: string, methods: string[]): Saved => {
      const file = readJsonFile(join(dir, "outcome.json"), "실행 결과") as Saved;
      if (file.synthetic) throw new ReconcileFileError(`${dir}: 모의 실행의 결과다. 실측과 섞지 않는다`);
      if (!methods.includes(file.method ?? "")) throw new ReconcileFileError(`${dir}: 방법 ${methods.join("·")} 의 실행 결과가 아니다`);
      if (JSON.stringify(file.exam) !== JSON.stringify(examHashes)) throw new ReconcileFileError(`${dir}: 다른 시험지로 돌린 실행이다`);
      return file;
    };

    if (command === "draw-kinds") {
      if (!values.seed || !values["per-bucket"]) return out(USAGE), 2;
      const targets = drawKindTargets(original.a, original.b, Number(values.seed), Number(values["per-bucket"]));
      writeJson(join(outDir, "kind-targets.json"), targets);
      const count: Record<string, number> = {};
      for (const target of targets) count[`${target.doc}:${target.bucket}`] = (count[`${target.doc}:${target.bucket}`] ?? 0) + 1;
      out(JSON.stringify({ targets: targets.length, count }, null, 1));
      return 0;
    }

    if (command === "kinds") {
      if (!values["kind-gold"]) return out(USAGE), 2;
      const kindGold: KindGold = readAs(resolveInputPath(values["kind-gold"]), kindGoldSchema, "종류의 정답");
      const problems = checkKindGold(kindGold, original.a, original.b);
      if (problems.length > 0) throw new ReconcileFileError(`종류의 정답이 문서와 맞지 않는다\n${problems.join("\n")}`);
      const score = scoreKinds(kindGold.items, (doc, id) => original[doc].kinds?.[id]);
      const differ = (["a", "b"] as const).flatMap((doc) => original[doc].facts.filter((fact) => original[doc].kinds?.[fact.id] !== exam[doc].kinds?.[fact.id]).map((fact) => `${doc}:${fact.id}`));
      const all: Record<string, Record<string, number>> = {};
      for (const doc of ["a", "b"] as const) for (const fact of original[doc].facts) ((all[`${doc}:${fact.bucket}`] ??= {})[original[doc].kinds?.[fact.id] as string] = (all[`${doc}:${fact.bucket}`]?.[original[doc].kinds?.[fact.id] as string] ?? 0) + 1);
      const md = [renderKindScore(score), "## 규칙이 모든 값 후보에 붙인 종류 (정답이 없는 자리 포함, 문서:갈래별)", "", ...table(["문서:갈래", ...Object.values(KIND_KO)], Object.entries(all).map(([name, bag]) => [name, ...Object.keys(KIND_KO).map((kind) => `${bag[kind] ?? 0}`)])), `원본 문서와 시험지 사본에서 종류가 다른 자리: ${differ.length}개${differ.length > 0 ? ` (${differ.join(", ")})` : ""}.`, ""].join("\n");
      writeFileSync(join(outDir, "kinds.md"), md);
      writeJson(join(outDir, "kinds.json"), { goldSha256: sha(resolveInputPath(values["kind-gold"])), score, all, kindsDifferOnExam: differ });
      out(md);
      return 0;
    }

    const prose = (doc: "a" | "b", id: string): boolean => factOf[doc].get(id)?.bucket !== "table_cell";
    const gradeCross = (outcome: MethodOutcome): { table: Grade; prose: ReturnType<typeof gradeProse> } => ({ table: grade(v1Pairs.pairs, v1Plants.plants, outcome), prose: gradeProse(gold, planted, outcome.verdicts, prose) });
    const crossRun = (item: { name: string; dir: string }, methods: string[]): D2Run & { notes: Record<string, unknown> } => {
      const file = saved(item.dir, methods);
      return { name: item.name, verdicts: file.outcome.verdicts, ...gradeCross(file.outcome), tally: (file.notes as { tally: D2Run["tally"] }).tally, rejected: file.outcome.rejected, cost: file.cost, notes: file.notes };
    };
    const groupRun = (item: { name: string; dir: string }, methods: string[]): IRun & { notes: Record<string, unknown> } => {
      const file = saved(item.dir, methods);
      const notes = file.notes as { doc: "a" | "b"; judgement: GroupJudgement; tally: IRun["tally"] };
      return { name: item.name, doc: notes.doc, grade: gradeGroups(notes.doc, gold.groups, planted, notes.judgement), tally: notes.tally, groups: notes.judgement.groups, cost: file.cost, notes: file.notes };
    };
    const judgments: ProseJudgments | null = values.judgments
      ? values.judgments.split(",").map((path) => readAs(resolveInputPath(path), proseJudgmentsSchema, "판정 기록")).reduce((merged, next) => ({ ...merged, items: [...merged.items, ...next.items] }))
      : null;
    const judgedKey = (scope: string, a: string, b: string): string => `${scope}|${[a, b].sort().join("|")}`;
    const judged = new Map((judgments?.items ?? []).map((item) => [judgedKey(item.scope, item.a, item.b), item]));
    const kindsOfPair = (scope: "cross" | "a" | "b", a: string, b: string): { x: ValueKind; y: ValueKind } => {
      const [docA, docB]: ["a" | "b", "a" | "b"] = scope === "cross" ? ["a", "b"] : [scope, scope];
      const x = kindOf(docA, a);
      const y = kindOf(docB, b);
      if (x === undefined || y === undefined) throw new ReconcileFileError(`${scope} ${a} ↔ ${b}: 값 후보에 없는 자리다`);
      return { x, y };
    };
    const cell = (tally: Reclass): string => `${tally.filtered}/${tally.total} (한 자리만 ${tally.oneNonValue}, 두 자리 다 ${tally.bothNonValue})`;

    if (command === "reclass") {
      if (!judgments) return out(USAGE), 2;
      const d2 = list(values.d2).map((item) => crossRun(item, ["d2"]));
      const h2 = list(values.h2).map((item) => crossRun(item, ["h2"]));
      const i = list(values.i).map((item) => groupRun(item, ["i"]));
      const lines: string[] = ["# 밴드 U 의 저장된 답을 종류와 함께 다시 본다 (호출 없음)", "", "「걸러진다」 = 짝(또는 묶음 안의 두 자리) 가운데 한 자리라도 종류 규칙이 「값이 아님」으로 가른 것. 분모는 그 줄의 대상 수다.", ""];
      // 1. 판정 기록의 갈래별: 걸러져야 할 것(값이 아닌 표시, 세어 본 수, 거짓 경보)이 걸러지는가.
      const groups = new Map<string, { scope: "cross" | "a" | "b"; a: string; b: string; basis: string }[]>();
      for (const item of judgments.items) {
        const name = item.scope === "cross" ? `두 문서 사이 · ${{ same_item: "같은 항목", different_item: "같은 항목 아님", not_a_value: "값이 아닌 표시" }[item.verdict]}` : `문서 ${item.scope.toUpperCase()} 안 · 같은 항목 아님 (거짓 경보)`;
        groups.set(name, [...(groups.get(name) ?? []), item]);
      }
      const byJudgment: Record<string, Reclass> = {};
      lines.push("## 1. 밴드 U 의 판정 기록 40건을 종류로 가르면", "");
      const rows: string[][] = [];
      const detail: string[][] = [];
      for (const [name, items] of groups) {
        const tally = reclassify(items.map((item) => kindsOfPair(item.scope, item.a, item.b)));
        byJudgment[name] = tally;
        rows.push([name, `${tally.total}`, `${tally.filtered}/${tally.total}`, `${tally.oneNonValue}/${tally.total}`, `${tally.bothNonValue}/${tally.total}`, `${tally.bothValue}/${tally.total}`]);
        for (const item of items) {
          const [docA, docB]: ["a" | "b", "a" | "b"] = item.scope === "cross" ? ["a", "b"] : [item.scope, item.scope];
          const kinds = kindsOfPair(item.scope, item.a, item.b);
          detail.push([name, shown(docA, item.a), shown(docB, item.b), isNonValue(kinds.x) || isNonValue(kinds.y) ? "걸러짐" : "남음", item.basis.slice(0, 70)]);
        }
      }
      lines.push(...table(["밴드 U 의 판정", "건수", "걸러짐", "　한 자리만 「값이 아님」", "　두 자리 다", "남음 (두 자리 다 값)"], rows));
      lines.push("### 건별", "", ...table(["밴드 U 의 판정", "자리 1 (규칙의 종류)", "자리 2 (규칙의 종류)", "결과", "밴드 U 의 근거"], detail));
      // 2. 실행별: 견준 짝 전체와 서술이 낀 짝.
      lines.push("## 2. 실행별: 견준 짝 가운데 걸러지는 것", "");
      const perRun: Record<string, unknown> = {};
      const runRows: string[][] = [];
      for (const run of [...d2, ...h2]) {
        const all = reclassify(run.verdicts.map((verdict) => kindsOfPair("cross", verdict.a, verdict.b)));
        const withProse = run.verdicts.filter((verdict) => prose("a", verdict.a) || prose("b", verdict.b));
        const proseTally = reclassify(withProse.map((verdict) => kindsOfPair("cross", verdict.a, verdict.b)));
        const outside = [...run.prose.outsideGold.match, ...run.prose.outsideGold.mismatch, ...run.prose.outsideGold.needsExplanation];
        const outsideTally = reclassify(outside.map((verdict) => kindsOfPair("cross", verdict.a, verdict.b)));
        perRun[run.name] = { all, prose: proseTally, outsideGold: outsideTally };
        runRows.push([run.name, cell(all), cell(proseTally), cell(outsideTally)]);
      }
      lines.push(...table(["실행", "견준 짝 전체 중 걸러짐", "서술이 낀 짝 중 걸러짐", "서술이 낀 정답 밖의 짝 중 걸러짐"], runRows));
      const iRows: string[][] = [];
      for (const run of i) {
        const seen = new Set<string>();
        const verdicts = run.groups.flatMap((group) => group.verdicts).filter((verdict) => !seen.has(`${verdict.a}|${verdict.b}`) && seen.add(`${verdict.a}|${verdict.b}`));
        const all = reclassify(verdicts.map((verdict) => kindsOfPair(run.doc, verdict.a, verdict.b)));
        const mismatches = reclassify(verdicts.filter((verdict) => verdict.outcome === "mismatch").map((verdict) => kindsOfPair(run.doc, verdict.a, verdict.b)));
        const other = reclassify(run.grade.otherMismatches.map((verdict) => kindsOfPair(run.doc, verdict.a, verdict.b)));
        perRun[run.name] = { all, mismatches, otherMismatches: other };
        iRows.push([run.name, cell(all), cell(mismatches), cell(other)]);
      }
      lines.push(...table(["실행 (문서 안)", "묶음 안의 비교 전체 중 걸러짐", "그중 불일치 중 걸러짐", "심은 것이 아닌 불일치(거짓 경보) 중 걸러짐"], iRows));
      // 3. 잘못 걸러지는 것: 정답의 자리 가운데 규칙이 「값이 아님」으로 가른 것.
      const goldPlaces: { set: string; ref: string; doc: "a" | "b"; id: string }[] = [
        ...v1Pairs.pairs.flatMap((pair) => [{ set: "정답 쌍 (밴드 R)", ref: pair.id, doc: "a" as const, id: pair.a }, { set: "정답 쌍 (밴드 R)", ref: pair.id, doc: "b" as const, id: pair.b }]),
        ...gold.prosePairs.flatMap((pair) => [{ set: "서술 쌍 (밴드 U)", ref: pair.id, doc: "a" as const, id: pair.a }, { set: "서술 쌍 (밴드 U)", ref: pair.id, doc: "b" as const, id: pair.b }]),
        ...gold.groups.flatMap((group) => group.places.map((place) => ({ set: "문서 안 묶음 (밴드 U)", ref: group.id, doc: group.doc, id: place.id }))),
      ];
      const wrongly = nonValuePlaces(goldPlaces, kindOf);
      lines.push("## 3. 잘못 걸러지는 것: 정답의 자리 가운데 규칙이 「값이 아님」으로 가른 것", "");
      const sets = [...new Set(goldPlaces.map((place) => place.set))];
      lines.push(...table(["정답", "쌍·묶음 수", "자리 수 (쌍·묶음에 걸쳐 센 것)", "「값이 아님」으로 갈린 자리", "그 자리가 낀 쌍·묶음"], sets.map((set) => [set, `${new Set(goldPlaces.filter((place) => place.set === set).map((place) => place.ref)).size}`, `${goldPlaces.filter((place) => place.set === set).length}`, `${wrongly.filter((place) => place.set === set).length}`, `${new Set(wrongly.filter((place) => place.set === set).map((place) => place.ref)).size}`])));
      if (wrongly.length > 0) lines.push(...table(["정답", "쌍·묶음", "문서", "자리", "원문 글자", "규칙의 종류"], wrongly.map((place) => [place.set, place.ref, place.doc, `\`${place.id}\``, raw(place.doc, place.id), KIND_KO[place.kind]])));
      const md = `${lines.join("\n")}\n`;
      writeFileSync(join(outDir, "reclass.md"), md);
      writeJson(join(outDir, "reclass.json"), { byJudgment, perRun, goldPlaces: goldPlaces.length, wronglyFiltered: wrongly });
      out(md);
      return 0;
    }

    // ── table3 ─────────────────────────────────────────────────────────────
    const d2 = list(values.d2).map((item) => crossRun(item, ["d2"]));
    const d3a = list(values.d3a).map((item) => crossRun(item, ["d3a"]));
    const d3b = list(values.d3b).map((item) => crossRun(item, ["d3b"]));
    const ib = list(values.ib).map((item) => groupRun(item, ["i"]));
    const i3 = list(values.i3).map((item) => groupRun(item, ["i3"]));
    if (ib.some((run) => run.doc !== "b") || i3.some((run) => run.doc !== "b")) throw new ReconcileFileError("--ib 와 --i3 는 문서 b(데이터북)의 실행이어야 한다");
    const cross = [...d2, ...d3a, ...d3b];
    const lines: string[] = ["# 밴드 V: 요약 숫자의 이름 묶기 (D3a·D3b·I3) — 밴드 U 의 D2·I(데이터북)와 나란히", "", "전부 실측이다 (모의 실행은 읽지 않는다). 수는 분모와 같이 적는다.", ""];
    // 숫자뿐인 블록
    const numberFacts = exam.b.facts.filter((fact) => fact.bucket === "number_only_block");
    const blockText = new Map(exams.b.blocks.map((block) => [block.id, normalizeText(block.text ?? "")]));
    const bindingOf = (run: { notes: Record<string, unknown> }): Map<string, string> => new Map(((run.notes.binding as { b?: { bound: { number: string; name: string }[] } } | undefined)?.b?.bound ?? []).map((item) => [item.number, item.name]));
    lines.push("## A. 숫자뿐인 블록 (데이터북, 값 후보 " + `${numberFacts.length}개)`, "");
    lines.push(
      ...table(
        ["항목", ...cross.map((run) => run.name), ...[...ib, ...i3].map((run) => run.name)],
        [
          ["이름이 묶인 블록", ...[...cross, ...ib, ...i3].map((run) => `${bindingOf(run).size}/${numberFacts.length}`)],
          ["견주어진 자리 (짝·묶음에 든 것)", ...cross.map((run) => `${numberFacts.filter((fact) => run.verdicts.some((verdict) => verdict.b === fact.id)).length}/${numberFacts.length}`), ...[...ib, ...i3].map((run) => `${numberFacts.filter((fact) => run.groups.some((group) => group.places.includes(fact.id))).length}/${numberFacts.length}`)],
          ["　그중 이름이 묶인 블록의 자리", ...cross.map((run) => `${numberFacts.filter((fact) => bindingOf(run).has(fact.where.blockId as string) && run.verdicts.some((verdict) => verdict.b === fact.id)).length}/${bindingOf(run).size}`), ...[...ib, ...i3].map((run) => `${numberFacts.filter((fact) => bindingOf(run).has(fact.where.blockId as string) && run.groups.some((group) => group.places.includes(fact.id))).length}/${bindingOf(run).size}`)],
        ],
      ),
    );
    const anyBinding = [...d3a, ...d3b, ...i3].map(bindingOf).find((binding) => binding.size > 0) ?? new Map<string, string>();
    lines.push("### 블록 × 실행 (짝의 상대와 코드의 결과)", "");
    const KO = { match: "일치", mismatch: "불일치", needs_explanation: "설명 필요" } as const;
    lines.push(
      ...table(
        ["자리", "원문 글자", "묶인 이름", ...cross.map((run) => run.name), ...[...ib, ...i3].map((run) => run.name)],
        numberFacts.map((fact) => [
          `\`${fact.id}\``,
          normalizeText(blockText.get(fact.where.blockId as string) ?? fact.raw),
          anyBinding.has(fact.where.blockId as string) ? (blockText.get(anyBinding.get(fact.where.blockId as string) as string) ?? "?") : "(묶지 않음)",
          ...cross.map((run) => run.verdicts.filter((verdict) => verdict.b === fact.id).map((verdict) => `${verdict.a} ${KO[verdict.outcome]}`).join("; ") || "-"),
          ...[...ib, ...i3].map((run) => run.groups.filter((group) => group.places.includes(fact.id)).map((group) => `${group.places.filter((id) => id !== fact.id).join(", ")} ${KO[group.outcome]}`).join("; ") || "-"),
        ]),
      ),
    );
    lines.push(renderProseTables({ d: [], d2: cross, h2: [], i: [...ib, ...i3], gold, v1Pairs: v1Pairs.pairs, planted, facts: { a: exam.a, b: exam.b }, judgments }));
    // 정답 밖의 짝을 종류로 가르면 (D3a·D3b·I3 의 결과에 종류를 붙여 본 것. 요청 조립과 무관하다.)
    lines.push("## 8. 이 밴드의 실행을 종류로 가르면 (호출 뒤의 셈)", "");
    lines.push(
      ...table(
        ["실행", "견준 짝 전체 중 「값이 아님」이 낀 것", "서술이 낀 정답 밖의 짝 중"],
        cross.map((run) => [run.name, cell(reclassify(run.verdicts.map((verdict) => kindsOfPair("cross", verdict.a, verdict.b)))), cell(reclassify([...run.prose.outsideGold.match, ...run.prose.outsideGold.mismatch, ...run.prose.outsideGold.needsExplanation].map((verdict) => kindsOfPair("cross", verdict.a, verdict.b))))]),
      ),
    );
    lines.push(
      ...table(
        ["실행 (문서 안)", "심은 것이 아닌 불일치 중 「값이 아님」이 낀 것"],
        [...ib, ...i3].map((run) => [run.name, cell(reclassify(run.grade.otherMismatches.map((verdict) => kindsOfPair(run.doc, verdict.a, verdict.b))))]),
      ),
    );
    const md = `${lines.join("\n")}\n`;
    const pending = toJudge({ d2: [...d3a, ...d3b], h2: [], i: i3 }).filter((entry) => !judged.has(judgedKey(entry.scope, entry.a, entry.b)));
    writeFileSync(join(outDir, "named.md"), md);
    writeJson(join(outDir, "to-judge.json"), { all: toJudge({ d2: [...d3a, ...d3b], h2: [], i: i3 }), notYetJudged: pending });
    out(md);
    log(`판정이 필요한데 판정 기록에 없는 것: ${pending.length}건`);
    return 0;
  } catch (error) {
    if (error instanceof ReconcileFileError || error instanceof ExamError) {
      log(error.message);
      out(error.message);
      return 1;
    }
    throw error;
  }
}

export type { Extraction, Verdict };

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
