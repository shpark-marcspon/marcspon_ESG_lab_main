import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { z } from "zod";
import { AnthropicProvider, BudgetGuard, FilePromptStore, GeminiProvider, JsonlFileSink, LiveSetupError, LlmClient, MockProvider, OpenAIProvider, computeCostUsd, defaultConfigPaths, liveProviders, loadConfig, requireLimitUsd, resolveRoute, roundUsd, type CallRequest, type LiveDeps, type LlmResult, type ModelConfig } from "llm";
import { ReconcileFileError, documentSchema, promptRoot, readCallLog, readJsonFile, resolveInputPath, resolveOutputPath } from "../adapters/files.js";
import { JsonlUnitStore, seedBudgetFromLog } from "../adapters/unitStore.js";
import { extractDocument, type DocModel, type Fact } from "../domain/facts.js";
import { ARMS, agreement, goldChartValues, judgePlaceAnswers, mergePlaces, pageLinesSchema, placeLabelsSchema, renderLines, scoreGrouping, scoreRoles, tagsInLines, toChartTableInput, unmaskedInLines, type Arm, type GroupingScore, type MergeRule, type PlaceAnswer } from "./chart-places.js";
import { chartGoldSchema, scoreLabels, type ChartGold, type LabelScore, type Predicted, type Tag } from "./charts.js";
import { ExamError } from "./exam.js";
import { conservativeTokens } from "./render.js";
import { imageTokens, pngSize } from "./vision.js";

// 밴드 VT1 (MARC-TICK-42) 의 명령: 번호표가 붙은 쪽의 그림과 글(또는 글만)로 번호마다 도표·계열·범주를 받고(runp),
// 세 실행을 합치고(mergep), 정답과 견주고(scorep), 표로 세우는 변환의 입력으로 옮긴다(inputp).
// runp·mergep·inputp 는 정답을 읽지 않는다. 채점은 scorep 에만 있다.

type Out = (line: string) => void;

const USAGE = `쓰임 (cd ts && pnpm -F reconcile exec tsx src/compare/cli13.ts <명령> ...)
  raws   (--doc <document.json> --doc-id <a|b> | --number-words <number-words.json>) --tags <tags.json> --out <파일>
      번호표의 ID → 원문 글자. 답에 값이 샜는지 보는 확인과, 번호표로 가리킨 범주를 채우는 데만 쓴다. 요청에는 싣지 않는다.
  runp   --arm <image-text|text> --docs <a=묶음 디렉터리[,b=..]> [--pages a:8,b:6] --out <디렉터리>
         (--dry-run | --provider mock | --provider openai --route <경로> --limit-usd <상한> --log <밴드 공용 호출 기록> | --replay <실제 실행의 출력> --log <호출 기록>) [--timeout-ms <n>] [--image-multiplier <곱수>]
      묶음 디렉터리: tags.json(번호표), page-lines.json(쪽의 글), raws.json, images/p0000.png(번호표를 덮은 쪽 그림. text 갈래는 읽지 않는다).
      쪽마다 한 요청. <out>/tag-answers.json 을 쓴다.
  mergep --runs <이름=runp 의 출력,..> --docs <..> [--rule <majority|unanimous>] --out <디렉터리>
  scorep --chart-gold <도표와 라벨의 정답> --arms <갈래=실행1+실행2+실행3[;..]> [--merged <갈래=mergep 출력[;..]>] [--w-arms <갈래=밴드 W 의 실행1+..>] --docs <..> --out <디렉터리>
      정답과 견줘 <out>/places.md, places.json. --w-arms 는 밴드 W 의 그림 라벨(tag-answers.json)을 같은 채점에 태운다 (묶음은 제목이 같은 것끼리).
  inputp --answers <tag-answers.json> --doc-id <a|b> --doc <document.json> --bundle <묶음 디렉터리> --source <이름> [--chart-gold <정답> --gold-out <값 후보 ID 로 옮긴 정답>] --out <파일>
      답을 밴드 CT1 의 변환(cli10 build --input)이 받는 꼴로 옮긴다. 번호표의 ID 가 숫자 낱말의 열쇠면 문서 모델의 값 후보로 잇는다.
--out 은 반드시 준다. 상대 경로는 ts/ 기준이다. 실제 공급사 호출은 돈이 든다.`;

const TAGS = { tenant: "measure", module: "reconcile-compare" };
const INPUT_GUARD_TOKENS = 250_000;
const OUTPUT_CAP = 32_000;
/** 번호표 하나의 출력 토큰 어림 (요청을 막는 확인용, 넉넉한 값). 밴드 W 의 연결 확인에서 잰 값을 올림한 것 그대로다. */
const ANSWER_TOKENS_GUARD = 240;
/** 번호표 하나의 예상 출력 토큰 (비용 어림용). 밴드 W 의 그림 라벨 세 실행의 실측(번호표 360개에 출력 28,967~31,082 토큰, 추론 포함 = 번호표당 80~86)에 답의 칸이 여섯에서 여덟으로 는 몫을 곱한 값이다 (가정). */
const ANSWER_TOKENS_EXPECTED = 120;
const REQUEST_OVERHEAD_TOKENS = 300;
/** 그림 토큰 어림의 곱수. 밴드 W 의 연결 확인(200dpi 쪽 그림 1654×2339 → 입력 합 5,356 토큰)이 이 곱수의 어림과 맞았다. */
const IMAGE_TOKEN_MULTIPLIER = 1.2;

const writeJson = (path: string, value: unknown): void => writeFileSync(path, `${JSON.stringify(value, null, 1)}\n`);
const table = (head: string[], rows: string[][]): string[] => [`| ${head.join(" | ")} |`, `|${head.map((_, index) => (index === 0 ? "---" : "---:")).join("|")}|`, ...rows.map((row) => `| ${row.join(" | ")} |`), ""];
const frac = (n: number, d: number): string => `${n}/${d}`;

function readAs<T>(path: string, schema: z.ZodType<T>, what: string): T {
  const parsed = schema.safeParse(readJsonFile(path, what));
  if (!parsed.success) throw new ReconcileFileError(`${path}: ${what} 형식이 맞지 않는다\n${z.prettifyError(parsed.error)}`);
  return parsed.data;
}

function readDoc(path: string): DocModel {
  const raw = readJsonFile(path, "문서 JSON");
  if (!documentSchema.safeParse(raw).success) throw new ReconcileFileError(`${path}: 문서 JSON 형식이 맞지 않는다`);
  return raw as DocModel;
}

const linkSchema = z.object({ container: z.string(), start: z.number(), end: z.number() });
export const tagFileSchema = z.array(z.object({ page: z.number(), n: z.number(), bbox: z.array(z.number()), id: z.string(), ids: z.array(z.string()).optional(), links: z.array(linkSchema).optional() }));
type TagFile = z.infer<typeof tagFileSchema>;
const rawsSchema = z.record(z.string(), z.string());
const numberWordsSchema = z.object({ words: z.array(z.object({ key: z.string(), page: z.number(), text: z.string() })) });
const placeAnswerSchema = z.object({ role: z.enum(["value", "tick", "category", "other"]), chart: z.string().nullable(), title: z.string().nullable(), series: z.string().nullable(), category: z.string().nullable(), categoryFrom: z.enum(["text", "tag"]).nullable(), unit: z.string().nullable() });
const answersSchema = z.record(z.enum(["a", "b"]), z.record(z.string(), placeAnswerSchema));
type Answers = Partial<Record<"a" | "b", Record<string, PlaceAnswer>>>;
/** 밴드 W 의 그림 라벨의 답 (묶음 이름과 `category` 갈래가 없다). */
const wAnswersSchema = z.record(z.enum(["a", "b"]), z.record(z.string(), z.object({ role: z.enum(["value", "tick", "other"]), title: z.string().nullable(), series: z.string().nullable(), category: z.string().nullable(), unit: z.string().nullable() })));

interface Bundle {
  dir: string;
  tags: TagFile;
  lines: z.infer<typeof pageLinesSchema>;
  raws: Record<string, string>;
}

function parseDocs(spec: string | undefined): Partial<Record<"a" | "b", string>> {
  if (!spec) throw new ReconcileFileError("--docs <a=묶음 디렉터리[,b=..]> 가 필요하다");
  const out: Partial<Record<"a" | "b", string>> = {};
  for (const item of spec.split(",")) {
    const [side, dir] = item.split("=");
    if ((side !== "a" && side !== "b") || !dir) throw new ReconcileFileError(`--docs 의 꼴이 틀렸다: ${item}`);
    out[side] = resolveInputPath(dir);
  }
  return out;
}

function readBundle(dir: string, withLines = true): Bundle {
  return { dir, tags: readAs(join(dir, "tags.json"), tagFileSchema, "번호표 목록"), lines: withLines ? readAs(join(dir, "page-lines.json"), pageLinesSchema, "쪽의 글") : {}, raws: readAs(join(dir, "raws.json"), rawsSchema, "원문 글자") };
}

const squash = (text: string | null): string => (text ?? "").normalize("NFKC").replace(/\s+/g, "");

/** 번호표의 ID → 쪽 (여러 묶음에 걸쳐). 값 후보를 같이 쓰는 ID(ids)도 넣는다. */
function pageIndex(bundle: Bundle): Map<string, { page: number; bbox: number[] }> {
  const out = new Map<string, { page: number; bbox: number[] }>();
  for (const tag of bundle.tags) for (const id of tag.ids ?? [tag.id]) out.set(id, { page: tag.page, bbox: tag.bbox });
  return out;
}

/** 숫자 낱말에 이어진 그릇·글자 자리와 겹치는 값 후보. 표의 숫자 칸(칸 전체가 값 후보)은 그 칸이면 겹친 것이다. */
export function factsOfLinks(links: { container: string; start: number; end: number }[], facts: Fact[]): string[] {
  const out: string[] = [];
  for (const link of links)
    for (const fact of facts) {
      const container = fact.where.gridId !== null ? `${fact.where.gridId}:r${fact.where.row}c${fact.where.col}` : fact.where.blockId;
      if (container !== link.container) continue;
      const span = fact.where.span;
      if (span === null || (link.start < span.end && link.end > span.start)) out.push(fact.id);
    }
  return [...new Set(out)];
}

interface ArmRuns {
  name: string;
  runs: Answers[];
  merged: Answers | null;
}

function fromW(answers: z.infer<typeof wAnswersSchema>): Answers {
  const out: Answers = {};
  for (const side of ["a", "b"] as const) {
    out[side] = {};
    for (const [id, answer] of Object.entries(answers[side] ?? {})) (out[side] as Record<string, PlaceAnswer>)[id] = { role: answer.role, chart: answer.role === "value" && answer.title !== null ? `제목:${squash(answer.title)}` : null, title: answer.title, series: answer.series, category: answer.category, categoryFrom: answer.category === null ? null : "text", unit: answer.unit };
  }
  return out;
}

const predictorOf = (answers: Answers) => (doc: "a" | "b", id: string): Predicted => {
  const answer = answers[doc]?.[id];
  if (!answer || answer.role !== "value") return { role: "not_value", category: null, series: null };
  return { role: "value", category: answer.category, series: answer.series };
};

interface RunScore {
  labels: LabelScore;
  grouping: Record<string, GroupingScore>;
  titles: { found: number; same: number; contains: number };
  roles: Record<string, Record<string, number>>;
  byKind: Record<string, { labels: LabelScore; found: number; charts: number; chartsWithValues: number }>;
  categoryFromTag: { attached: number; correct: number };
}

function scoreRun(gold: ChartGold, answers: Answers, pages: Record<"a" | "b", Map<string, { page: number; bbox: number[] }>>): RunScore {
  const labels = scoreLabels(gold.labels, predictorOf(answers));
  const grouping: Record<string, GroupingScore> = {};
  const titles = { found: 0, same: 0, contains: 0 };
  const foundAll = new Set<string>();
  for (const side of ["a", "b"] as const) {
    const charts = goldChartValues(gold, side);
    if (charts.length === 0) continue;
    const mine = answers[side] ?? {};
    const score = scoreGrouping(charts, Object.fromEntries(Object.entries(mine).map(([id, answer]) => [id, { role: answer.role, group: answer.chart }])), (id) => pages[side].get(id)?.page ?? -1);
    grouping[side] = score;
    for (const id of score.found) {
      foundAll.add(id);
      const chart = gold.charts.find((item) => item.id === id) as ChartGold["charts"][number];
      const values = charts.find((item) => item.id === id)?.values ?? [];
      const said = values.map((value) => mine[value]).filter((answer): answer is PlaceAnswer => answer !== undefined && answer.role === "value" && answer.title !== null).map((answer) => squash(answer.title));
      const counts = new Map<string, number>();
      for (const title of said) counts.set(title, (counts.get(title) ?? 0) + 1);
      const top = [...counts.entries()].sort((x, y) => y[1] - x[1])[0]?.[0] ?? "";
      const want = squash(chart.title);
      titles.found += 1;
      if (top !== "" && top === want) titles.same += 1;
      if (top !== "" && (top.includes(want) || want.includes(top))) titles.contains += 1;
    }
  }
  const roles = scoreRoles(
    gold.labels.map((label) => ({ id: `${label.doc}:${label.id}`, role: label.role })),
    (key) => answers[key.slice(0, 1) as "a" | "b"]?.[key.slice(2)]?.role,
  ).matrix;
  const kindOf = new Map(gold.charts.map((chart) => [chart.id, chart.kind]));
  const byKind: RunScore["byKind"] = {};
  for (const kind of [...new Set(gold.charts.map((chart) => chart.kind))].sort()) {
    const charts = gold.charts.filter((chart) => chart.kind === kind);
    byKind[kind] = { labels: scoreLabels(gold.labels.filter((label) => kindOf.get(label.chart) === kind), predictorOf(answers)), found: charts.filter((chart) => foundAll.has(chart.id)).length, charts: charts.length, chartsWithValues: charts.filter((chart) => gold.labels.some((label) => label.chart === chart.id && label.role === "value")).length };
  }
  const categoryFromTag = { attached: 0, correct: 0 };
  for (const label of gold.labels) {
    const answer = answers[label.doc]?.[label.id];
    if (label.role !== "value" || !answer || answer.role !== "value" || answer.categoryFrom !== "tag") continue;
    categoryFromTag.attached += 1;
    if ((label.category ?? []).map(squash).includes(squash(answer.category))) categoryFromTag.correct += 1;
  }
  return { labels, grouping, titles, roles, byKind, categoryFromTag };
}

const sumGroup = (score: RunScore, pick: (group: GroupingScore) => number): number => Object.values(score.grouping).reduce((sum, group) => sum + pick(group), 0);

export function renderScores(gold: ChartGold, arms: ArmRuns[], pages: Record<"a" | "b", Map<string, { page: number; bbox: number[] }>>): { lines: string[]; json: Record<string, unknown> } {
  const lines = ["# 번호표마다의 답을 정답과 견준 표 (저장된 답에서. 호출 0)", ""];
  const json: Record<string, unknown> = {};
  const values = gold.labels.filter((label) => label.role === "value");
  const tagged = values.filter((label) => pages[label.doc].has(label.id)).length;
  lines.push(`정답: 도표 ${gold.charts.length}개, 값 자리 ${values.length}, 그 가운데 번호표가 붙은 것 ${tagged}. 값이 아닌 자리 ${gold.labels.length - values.length}.`, "", "분모는 정답의 자리 전부다 (번호표가 없던 자리, 답이 없거나 버려진 자리는 「못 붙임」).", "");
  const head = ["갈래 · 실행", "값으로 본 것 / 정답 값", "범주: 맞음 / 붙임 · 못 붙임", "계열: 맞음 / 붙임 · 못 붙임 (계열 이름 있는 자리)", "계열 이름 없는 자리에 계열", "범주·계열 둘 다 맞음", "값이 아닌 것을 값으로", "도표 묶어 냄 / 값 있는 도표 (꼭 맞음)", "도표가 아닌 묶음", "제목 같음 · 품음 / 묶어 낸 도표"];
  const rows: string[][] = [];
  for (const arm of arms) {
    const all = [...arm.runs.map((answers, index) => ({ name: `${arm.name} ${index + 1}`, answers })), ...(arm.merged ? [{ name: `${arm.name} 합친 것`, answers: arm.merged }] : [])];
    const armJson: Record<string, unknown> = {};
    for (const run of all) {
      const score = scoreRun(gold, run.answers, pages);
      armJson[run.name] = score;
      const s = score.labels;
      rows.push([run.name, frac(s.seenAsValue, s.values), `${frac(s.category.correct, s.category.attached)} · ${s.category.none}`, `${frac(s.series.correct, s.series.attached)} · ${s.series.none} (${s.series.gold})`, frac(s.seriesOnUnnamed.attached, s.seriesOnUnnamed.gold), frac(s.bothCorrect, s.values), frac(s.nonValuesSeenAsValue, s.nonValues), `${frac(sumGroup(score, (group) => group.found.length), sumGroup(score, (group) => group.chartsWithValues))} (${sumGroup(score, (group) => group.exact.length)})`, String(sumGroup(score, (group) => group.falseCharts.length)), `${score.titles.same} · ${score.titles.contains} / ${score.titles.found}`]);
    }
    const same = agreement(
      values.map((label) => `${label.doc}:${label.id}`),
      arm.runs.map((answers) => Object.fromEntries((["a", "b"] as const).flatMap((side) => Object.entries(answers[side] ?? {}).map(([id, answer]) => [`${side}:${id}`, answer])))),
    );
    armJson.agreement = same;
    json[arm.name] = armJson;
  }
  lines.push("## 라벨과 도표 묶기", "", ...table(head, rows));
  lines.push("## 실행들이 서로 같은가 (정답이 값인 자리. 갈래·계열·범주가 글자까지 같을 때 같다)", "");
  lines.push(...table(["갈래", "셋 다 같음", "과반만 같음", "과반 없음", "답 없음", "정답 값"], arms.map((arm) => {
    const same = (json[arm.name] as { agreement: ReturnType<typeof agreement> }).agreement;
    return [arm.name, String(same.all), String(same.majority), String(same.none), String(same.unanswered), String(values.length)];
  })));
  lines.push("## 갈래의 가름 (정답의 갈래 → 답의 갈래. none 은 번호표가 없거나 답이 없는 것)", "");
  for (const arm of arms)
    arm.runs.forEach((_answers, index) => {
      const matrix = ((json[arm.name] as Record<string, RunScore>)[`${arm.name} ${index + 1}`] as RunScore).roles;
      lines.push(`- ${arm.name} ${index + 1}: ${Object.entries(matrix).map(([goldRole, got]) => `${goldRole} → ${Object.entries(got).sort().map(([role, count]) => `${role} ${count}`).join(", ")}`).join(" / ")}`);
    });
  lines.push("", "## 도표의 꼴별 (실행 1 · 2 · 3, 합친 것)", "");
  const kinds = [...new Set(gold.charts.map((chart) => chart.kind))].sort();
  for (const arm of arms) {
    const names = [...arm.runs.map((_answers, index) => `${arm.name} ${index + 1}`), ...(arm.merged ? [`${arm.name} 합친 것`] : [])];
    const scores = names.map((name) => (json[arm.name] as Record<string, RunScore>)[name] as RunScore);
    lines.push(`### ${arm.name}`, "", ...table(["꼴", "도표 (값 있는 것)", "묶어 냄", "범주 맞음/붙임 (정답 값)", "계열 맞음/붙임 (계열 있는 정답)"], kinds.map((kind) => {
      const of = scores.map((score) => score.byKind[kind] as RunScore["byKind"][string]);
      const first = of[0] as RunScore["byKind"][string];
      return [kind, `${first.charts} (${first.chartsWithValues})`, of.map((item) => String(item.found)).join(" · "), `${of.map((item) => frac(item.labels.category.correct, item.labels.category.attached)).join(" · ")} (${first.labels.values})`, `${of.map((item) => frac(item.labels.series.correct, item.labels.series.attached)).join(" · ")} (${first.labels.series.gold})`];
    })));
  }
  lines.push("## 틀리게 붙인 자리", "");
  for (const arm of arms)
    for (const [name, score] of Object.entries(json[arm.name] as Record<string, RunScore>)) {
      if (name === "agreement") continue;
      const wrong = score.labels.wrong;
      lines.push(`- ${name}: ${wrong.length}건${wrong.length > 0 ? ` — ${wrong.slice(0, 40).map((item) => `${item.doc}:${item.id}(${item.what}: 정답 「${item.gold}」, 답 「${item.got}」)`).join("; ")}` : ""}`);
    }
  lines.push("");
  return { lines, json };
}

interface Planned<T> {
  id: string;
  request: CallRequest<T>;
  expectedOutput: number;
  imageTokens: number;
}

export async function main(argv: string[], out: Out = console.log, log: Out = console.error, deps: LiveDeps = { env: process.env }): Promise<number> {
  const [command, ...rest] = argv;
  let values;
  try {
    values = parseArgs({ args: rest, options: { arm: { type: "string" }, docs: { type: "string" }, pages: { type: "string" }, out: { type: "string" }, "dry-run": { type: "boolean" }, provider: { type: "string" }, route: { type: "string" }, "limit-usd": { type: "string" }, log: { type: "string" }, "timeout-ms": { type: "string" }, replay: { type: "string" }, "image-multiplier": { type: "string" }, runs: { type: "string" }, rule: { type: "string" }, "chart-gold": { type: "string" }, arms: { type: "string" }, merged: { type: "string" }, "w-arms": { type: "string" }, answers: { type: "string" }, "doc-id": { type: "string" }, doc: { type: "string" }, bundle: { type: "string" }, source: { type: "string" }, "gold-out": { type: "string" }, tags: { type: "string" }, "number-words": { type: "string" } } }).values;
  } catch {
    return out(USAGE), 2;
  }
  if (!["raws", "runp", "mergep", "scorep", "inputp"].includes(command ?? "") || !values.out) return out(USAGE), 2;
  try {
    const outPath = resolveOutputPath(values.out);

    if (command === "raws") {
      if (!values.tags || (values.doc === undefined) === (values["number-words"] === undefined)) return out(USAGE), 2;
      const tags = readAs(resolveInputPath(values.tags), tagFileSchema, "번호표 목록");
      const source = values.doc ? new Map(extractDocument(readDoc(resolveInputPath(values.doc)), values["doc-id"] ?? "a", { titles: "own" }).facts.map((fact) => [fact.id, fact.raw])) : new Map(readAs(resolveInputPath(values["number-words"] as string), numberWordsSchema, "숫자 낱말 목록").words.map((word) => [word.key, word.text]));
      const raws: Record<string, string> = {};
      for (const tag of tags)
        for (const id of tag.ids ?? [tag.id]) {
          const raw = source.get(id);
          if (raw === undefined) throw new ReconcileFileError(`번호표의 ID ${id} 의 원문이 없다`);
          raws[id] = raw;
        }
      writeJson(outPath, raws);
      out(JSON.stringify({ out: outPath, ids: Object.keys(raws).length }));
      return 0;
    }

    if (command === "mergep") {
      const rule = (values.rule ?? "majority") as MergeRule;
      if (rule !== "majority" && rule !== "unanimous") return out(USAGE), 2;
      const dirs = (values.runs ?? "").split(",").filter((item) => item !== "").map((item) => resolveOutputPath(item.includes("=") ? (item.split("=")[1] as string) : item));
      if (dirs.length < 2) throw new ReconcileFileError("--runs 에 실행이 둘 이상 있어야 한다");
      const docs = parseDocs(values.docs);
      const runs = dirs.map((dir) => readAs(join(dir, "tag-answers.json"), answersSchema, "번호표의 답"));
      mkdirSync(outPath, { recursive: true });
      const merged: Answers = {};
      const note: Record<string, unknown> = { rule, runs: dirs };
      for (const side of ["a", "b"] as const) {
        if (!docs[side]) continue;
        const index = pageIndex(readBundle(docs[side] as string, false));
        const result = mergePlaces(runs.map((run) => run[side] ?? {}), (id) => index.get(id)?.page ?? -1, rule);
        merged[side] = result.answers;
        note[side] = result.agreement;
      }
      writeJson(join(outPath, "tag-answers.json"), merged);
      writeJson(join(outPath, "merge.json"), note);
      out(JSON.stringify(note));
      return 0;
    }

    if (command === "scorep") {
      const gold = readAs(resolveInputPath(values["chart-gold"] ?? ""), chartGoldSchema, "도표와 라벨의 정답");
      const docs = parseDocs(values.docs);
      const pages = { a: docs.a ? pageIndex(readBundle(docs.a, false)) : new Map(), b: docs.b ? pageIndex(readBundle(docs.b, false)) : new Map() };
      const split = (spec: string | undefined): [string, string[]][] => (spec ?? "").split(";").filter((item) => item.trim() !== "").map((item) => [item.split("=")[0] as string, (item.split("=")[1] ?? "").split("+").map((dir) => resolveOutputPath(dir))]);
      const mergedOf = new Map(split(values.merged).map(([name, dirs]) => [name, dirs[0] as string]));
      const arms: ArmRuns[] = [
        ...split(values["w-arms"]).map(([name, dirs]) => ({ name, runs: dirs.map((dir) => fromW(readAs(join(dir, "tag-answers.json"), wAnswersSchema, "밴드 W 의 그림 라벨의 답"))), merged: mergedOf.has(name) ? fromW(readAs(join(mergedOf.get(name) as string, "tag-answers.json"), wAnswersSchema, "밴드 W 의 합친 답")) : null })),
        ...split(values.arms).map(([name, dirs]) => ({ name, runs: dirs.map((dir) => readAs(join(dir, "tag-answers.json"), answersSchema, "번호표의 답") as Answers), merged: mergedOf.has(name) ? (readAs(join(mergedOf.get(name) as string, "tag-answers.json"), answersSchema, "합친 답") as Answers) : null })),
      ];
      if (arms.length === 0) return out(USAGE), 2;
      const rendered = renderScores(gold, arms, pages);
      mkdirSync(outPath, { recursive: true });
      writeFileSync(join(outPath, "places.md"), `${rendered.lines.join("\n")}\n`);
      writeJson(join(outPath, "places.json"), rendered.json);
      out(JSON.stringify({ out: join(outPath, "places.md"), arms: arms.map((arm) => arm.name) }));
      return 0;
    }

    if (command === "inputp") {
      const side = (values["doc-id"] ?? "a") as "a" | "b";
      if (!values.answers || !values.doc || !values.bundle || !values.source) return out(USAGE), 2;
      const answers = readAs(resolveInputPath(values.answers), answersSchema, "번호표의 답")[side] ?? {};
      const bundle = readBundle(resolveInputPath(values.bundle), false);
      const facts = extractDocument(readDoc(resolveInputPath(values.doc)), side).facts;
      const factIds = new Set(facts.map((fact) => fact.id));
      const tagOf = new Map(bundle.tags.flatMap((tag) => (tag.ids ?? [tag.id]).map((id) => [id, tag] as const)));
      const factsOf = (id: string): string[] => (factIds.has(id) ? [id] : factsOfLinks(tagOf.get(id)?.links ?? [], facts));
      const index = pageIndex(bundle);
      // 값 후보가 여럿 걸린 낱말(겹친 표)은 값 후보마다 한 줄씩 넣는다: 변환이 겹친 둘째를 스스로 가른다.
      const expanded: Record<string, PlaceAnswer> = {};
      const placeOf = new Map<string, { page: number; bbox: number[] }>();
      const noFact: string[] = [];
      for (const [id, answer] of Object.entries(answers)) {
        const linked = factsOf(id);
        if (linked.length === 0 && answer.role === "value") noFact.push(id);
        for (const fact of linked) {
          expanded[fact] = answer;
          placeOf.set(fact, index.get(id) as { page: number; bbox: number[] });
        }
      }
      const moved = toChartTableInput(expanded, (id) => placeOf.get(id) as { page: number; bbox: number[] }, (id) => id, values.source);
      writeJson(outPath, moved.input);
      const note: Record<string, unknown> = { out: outPath, charts: moved.input.charts.length, values: moved.input.values.length, valueAnswersWithoutFact: noFact.length, valueAnswersWithoutGroup: moved.noGroup.length };
      if (values["chart-gold"] && values["gold-out"]) {
        // 정답의 ID 가 숫자 낱말의 열쇠면 값 후보 ID 로 옮긴다 (밴드 CT1 의 「옳은 칸」 채점이 값 후보 ID 로 읽는다). 값 후보가 없는 자리는 빠진다.
        const gold = readAs(resolveInputPath(values["chart-gold"]), chartGoldSchema, "도표와 라벨의 정답");
        const labels: ChartGold["labels"] = [];
        let dropped = 0;
        for (const label of gold.labels.filter((item) => item.doc === side)) {
          const linked = factsOf(label.id);
          if (linked.length === 0) dropped += 1;
          for (const fact of linked) labels.push({ ...label, id: fact });
        }
        const goldOut = resolveOutputPath(values["gold-out"]);
        const before = existsSync(goldOut) ? readAs(goldOut, chartGoldSchema, "옮긴 정답") : { charts: [], labels: [] };
        writeJson(goldOut, { charts: [...before.charts.filter((chart) => chart.doc !== side), ...gold.charts.filter((chart) => chart.doc === side)], labels: [...before.labels.filter((label) => label.doc !== side), ...labels] });
        note.gold = { out: goldOut, labels: labels.length, withoutFact: dropped };
      }
      out(JSON.stringify(note));
      return 0;
    }

    // ── runp ───────────────────────────────────────────────────────────────
    const arm = values.arm as Arm | undefined;
    if (!arm || !(arm in ARMS)) return out(USAGE), 2;
    const replayDir = values.replay ? resolveOutputPath(values.replay) : null;
    if ([values.provider !== undefined, values["dry-run"] === true, replayDir !== null].filter(Boolean).length !== 1) return out(USAGE), 2;
    const docs = parseDocs(values.docs);
    const only = values.pages ? new Set(values.pages.split(",").map((item) => item.trim())) : null;
    const multiplier = values["image-multiplier"] === undefined ? IMAGE_TOKEN_MULTIPLIER : Number(values["image-multiplier"]);
    if (!Number.isFinite(multiplier) || multiplier <= 0) throw new ReconcileFileError("--image-multiplier 는 0 보다 큰 수다");
    const config = loadConfig(defaultConfigPaths({ synthetic: true }));
    const prompts = new FilePromptStore(promptRoot);
    const live = values.provider !== undefined && values.provider !== "mock";
    const route = live || values["dry-run"] || replayDir ? (values.route ?? "light-openai") : "mock";
    const model = resolveRoute(config, route)[0] as ModelConfig | undefined;
    if (!model) throw new ReconcileFileError(`경로 ${route} 에 모델이 없다`);
    const timeoutMs = values["timeout-ms"] === undefined ? undefined : Number(values["timeout-ms"]);
    mkdirSync(outPath, { recursive: true });
    const dry = new LlmClient({ config, providers: { mock: new MockProvider(), anthropic: new AnthropicProvider(), openai: new OpenAIProvider(), gemini: new GeminiProvider() }, prompts, log: { write: () => undefined } });
    const estimate = { calls: 0, tags: 0, inputTokens: 0, imageInputTokens: 0, expectedOutputTokens: 0, expectedCostUsd: 0, costUpperBoundUsd: 0, maxInputTokensPerCall: 0 };
    const mock = new MockProvider();
    let client: LlmClient | null = null;
    let budget: BudgetGuard | null = null;
    const logPath = values.log ? resolveOutputPath(values.log) : join(outPath, "calls.jsonl");
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
    const store = new JsonlUnitStore(join(replayDir ?? outPath, "units.jsonl"), placeLabelsSchema);
    async function ask(planned: Planned<z.infer<typeof placeLabelsSchema>>): Promise<z.infer<typeof placeLabelsSchema> | null> {
      const tokens = conservativeTokens(JSON.stringify(planned.request.vars ?? {}));
      const report = dry.dryRun(planned.request);
      const input = Math.max(tokens, report.estimate.inputTokens) + planned.imageTokens;
      estimate.imageInputTokens += planned.imageTokens;
      if (input > INPUT_GUARD_TOKENS) throw new ReconcileFileError(`${planned.id}: 입력 어림 ${input} 토큰이 한도 ${INPUT_GUARD_TOKENS} 을 넘는다. 요청을 만들지 않는다`);
      estimate.calls += 1;
      estimate.inputTokens += input;
      estimate.maxInputTokensPerCall = Math.max(estimate.maxInputTokensPerCall, input);
      estimate.expectedOutputTokens += planned.expectedOutput;
      estimate.expectedCostUsd += computeCostUsd({ inputTokens: input, outputTokens: planned.expectedOutput, cacheReadTokens: 0, cacheWriteTokens: 0 }, (model as ModelConfig).pricing);
      estimate.costUpperBoundUsd += computeCostUsd({ inputTokens: input, outputTokens: report.estimate.maxOutputTokens, cacheReadTokens: 0, cacheWriteTokens: 0 }, (model as ModelConfig).pricing);
      if (!client && !replayDir) return null;
      let result: LlmResult<z.infer<typeof placeLabelsSchema>> | undefined = store.get(planned.id, planned.request);
      if (!result) {
        if (!client) throw new ReconcileFileError(`${planned.id}: 저장된 답에 없는 요청이다 (요청 글이 실측 때와 다르다). 다시 채점할 때는 모델을 부르지 않는다`);
        if (!live) mock.enqueueText(JSON.stringify({ tags: [] }));
        result = await client.call(planned.request);
        store.put(planned.id, planned.request, result);
        log(`[${arm}] ${planned.id}: ${result.kind} (${result.meta.totalCostUsd} USD)`);
      }
      callIds.push(result.meta.callId);
      if (result.kind !== "success") {
        failures.push({ id: planned.id, kind: result.kind });
        return null;
      }
      return result.data;
    }
    const base = { route, tags: TAGS, ...(timeoutMs === undefined ? {} : { timeoutMs }) };
    const cap = Math.min(OUTPUT_CAP, model.maxOutputTokens);
    const requestsDir = join(outPath, "requests");
    if (values["dry-run"]) mkdirSync(requestsDir, { recursive: true });
    const answers: Answers = {};
    const rejected: Record<string, number> = {};
    const pageNotes: Record<string, unknown>[] = [];
    let badCategoryTag = 0;
    for (const side of ["a", "b"] as const) {
      if (!docs[side]) continue;
      const bundle = readBundle(docs[side] as string);
      answers[side] = {};
      const rawOf = (id: string): string => {
        const raw = bundle.raws[id];
        if (raw === undefined) throw new ReconcileFileError(`번호표의 ID ${side}:${id} 의 원문이 없다`);
        return raw;
      };
      for (const page of [...new Set(bundle.tags.map((tag) => tag.page))].sort((x, y) => x - y)) {
        if (only && !only.has(`${side}:${page}`)) continue;
        const pageTags: Tag[] = bundle.tags.filter((tag) => tag.page === page).sort((x, y) => x.n - y.n).map((tag) => ({ page: tag.page, n: tag.n, bbox: tag.bbox, id: tag.id, ids: tag.ids ?? [tag.id] }));
        const pageLines = bundle.lines[String(page)];
        if (!pageLines) throw new ReconcileFileError(`${side}:${page}: 쪽의 글이 없다 (page-lines.json)`);
        // 요청을 막는 확인: 글에 가리지 못한 숫자가 없고, 글에 실린 번호표가 요청의 번호 목록 안에 있다.
        const left = unmaskedInLines(pageLines.lines);
        if (left.length > 0) throw new ReconcileFileError(`${side}:${page}: 요청 글에 가리지 못한 숫자가 있다 (${left.slice(0, 20).join(", ")}). 요청을 만들지 않는다`);
        const numbers = new Set(pageTags.map((tag) => tag.n));
        const stray = tagsInLines(pageLines.lines).filter((n) => !numbers.has(n));
        if (stray.length > 0) throw new ReconcileFileError(`${side}:${page}: 글에 요청에 없는 번호표가 있다 (${stray.slice(0, 20).join(", ")})`);
        const list = pageTags.map((tag) => String(tag.n)).join(", ");
        const text = renderLines(pageLines.lines);
        let attachments: CallRequest<unknown>["attachments"];
        let image: Record<string, unknown> | null = null;
        let tokensOfImage = 0;
        let key = createHash("sha256").update(text).digest("hex").slice(0, 12);
        if (ARMS[arm].image) {
          const bytes = readFileSync(join(bundle.dir, "images", `p${String(page).padStart(4, "0")}.png`));
          const imageSha = createHash("sha256").update(bytes).digest("hex");
          const size = pngSize(bytes);
          tokensOfImage = imageTokens(size, multiplier);
          attachments = [{ type: "image", base64: bytes.toString("base64"), mediaType: "image/png" }];
          image = { sha256: imageSha, ...size, tokensEstimate: tokensOfImage };
          key = imageSha.slice(0, 12);
        }
        const id = `lp/${arm}/${side}/p${page}@${key}`;
        if (values.provider !== "mock" && pageTags.length * ANSWER_TOKENS_GUARD + REQUEST_OVERHEAD_TOKENS > cap * 0.75) throw new ReconcileFileError(`${id}: 번호표 ${pageTags.length}개의 예상 출력이 출력 상한의 3/4 을 넘는다. 요청을 만들지 않는다`);
        if (values["dry-run"]) writeFileSync(join(requestsDir, `${id.replace(/[/|#:@]/g, "__")}.txt`), `번호표: ${list}\n\n${text}\n`);
        estimate.tags += pageTags.length;
        const answer = await ask({ id, request: { ...base, prompt: ARMS[arm].prompt, vars: { tags: list, lines: text }, schema: placeLabelsSchema, schemaName: "reconcile_label_chart_places", maxOutputTokens: cap, ...(attachments ? { attachments } : {}) }, expectedOutput: pageTags.length * ANSWER_TOKENS_EXPECTED + REQUEST_OVERHEAD_TOKENS, imageTokens: tokensOfImage });
        const judged = judgePlaceAnswers(answer, pageTags, rawOf);
        Object.assign(answers[side] as Record<string, PlaceAnswer>, judged.answers);
        badCategoryTag += judged.badCategoryTag;
        for (const [name, count] of Object.entries(judged.rejected)) rejected[name] = (rejected[name] ?? 0) + count;
        pageNotes.push({ doc: side, page, tags: pageTags.length, lines: pageLines.lines.length, visibleNumbers: pageLines.visible_numbers, image, answered: pageTags.length - judged.unanswered.length, unanswered: judged.unanswered, rejected: judged.rejected });
      }
    }
    if (values["dry-run"]) {
      const report = { dryRun: true, arm, prompt: ARMS[arm].prompt, route, model: model.key, note: "공급사를 부르지 않았다. 입력 토큰은 보수적인 글자 수 어림과 호출 계층의 어림 중 큰 값에 그림 토큰의 어림(32px 조각 × 곱수)을 더한 값이다. 예상 출력은 번호표 하나에 ANSWER_TOKENS_EXPECTED 토큰이라는 가정이다. 캐시 할인은 넣지 않았다. 비용 상한은 요청마다 출력 상한을 다 쓸 때의 값이다(재시도 제외).", ...estimate, expectedCostUsd: roundUsd(estimate.expectedCostUsd), costUpperBoundUsd: roundUsd(estimate.costUpperBoundUsd), pages: pageNotes };
      writeJson(join(outPath, "dry-run.json"), report);
      out(`[${arm}] 모델 ${model.key} · 호출 ${estimate.calls} · 번호표 ${estimate.tags} · 입력 어림 ${estimate.inputTokens} (그중 그림 ${estimate.imageInputTokens}, 요청 최대 ${estimate.maxInputTokensPerCall}) · 예상 출력 ${estimate.expectedOutputTokens} · 예상 비용 ${report.expectedCostUsd} USD · 비용 상한 ${report.costUpperBoundUsd} USD`);
      return 0;
    }
    const records = readCallLog(logPath).filter((record) => callIds.includes(record.callId));
    const total = (record: (typeof records)[number]): number => (record.inputTokens ?? 0) + (record.cacheReadTokens ?? 0) + (record.cacheWriteTokens ?? 0);
    const byResultKind: Record<string, number> = {};
    for (const record of records) byResultKind[record.resultKind] = (byResultKind[record.resultKind] ?? 0) + 1;
    const cost = { calls: records.length, inputTokens: records.reduce((sum, record) => sum + total(record), 0), cacheReadTokens: records.reduce((sum, record) => sum + (record.cacheReadTokens ?? 0), 0), outputTokens: records.reduce((sum, record) => sum + (record.outputTokens ?? 0), 0), costUsd: roundUsd(records.reduce((sum, record) => sum + (record.costUsd ?? 0), 0)), maxInputTokens: records.reduce((max, record) => Math.max(max, total(record)), 0), byResultKind };
    const synthetic = values.provider === "mock" ? "모의 실행 (빈 답을 돌려주는 synthetic 응답기). 결과는 모델의 성능과 무관하다." : null;
    writeJson(join(outPath, "tag-answers.json"), answers);
    writeJson(join(outPath, "outcome.json"), { ...(synthetic ? { synthetic } : {}), method: "lp", arm, prompt: ARMS[arm].prompt, provider: values.provider ?? null, ...(replayDir ? { replayedFrom: replayDir } : {}), route, model: model.key, failures, cost, rejected, badCategoryTag, pages: pageNotes });
    out(JSON.stringify({ ...(synthetic ? { synthetic } : {}), arm, failures, cost, spentUsd: budget ? budget.spent({ module: TAGS.module }) : null, rejected, badCategoryTag, answered: Object.values(answers).reduce((sum, side) => sum + Object.keys(side ?? {}).length, 0) }, null, 1));
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
