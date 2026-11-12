import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { z } from "zod";
import { ReconcileFileError, documentSchema, readJsonFile, resolveInputPath, resolveOutputPath } from "../adapters/files.js";
import { extractDocument, type DocModel } from "../domain/facts.js";
import { ExamError } from "./exam.js";
import { answerKey, mergeVision, visionLabels, type TagAnswer } from "./vision.js";
import { assignTags, chartGoldSchema, chartLabelsSchema, codeLabels, linkFactsToCharts, matchCharts, scoreLabels, type ChartGold, type ChartLabels, type FactChart, type LabelScore, type Predicted } from "./charts.js";

// 밴드 W (MARC-TICK-26) 의 명령: 도표 라벨을 값 후보에 걸기(chart-facts), 도표 찾기와 코드 라벨의 채점(chart-score).
// 모델을 부르지 않는다. 정답은 chart-score 에서만 읽는다: chart-facts 는 문서 모델과 도표 라벨 산출만 본다.

type Out = (line: string) => void;

const USAGE = `사용법 (cd ts && pnpm -F reconcile exec tsx src/compare/cli4.ts <명령> ...)
  chart-facts --a <문서 A 원본> --b <문서 B 원본> --labels-a <A 의 chart-labels.json> --labels-b <B 의 chart-labels.json> --out <디렉터리>
      도표 낱말을 값 후보의 ID 에 건다. <out>/fact-charts.json(값 후보별 도표·갈래·라벨), tags-a.json·tags-b.json(번호표: 쪽, 번호, 자리, ID),
      code-labels.json(요청 글에 실을 코드 라벨: 문서별 ID → 글). 정답을 읽지 않는다.
  chart-score --chart-gold <도표와 라벨의 정답> --labels-a <..> --labels-b <..> --facts <chart-facts 의 fact-charts.json> --out <디렉터리>
      도표 찾기(정밀도·재현율)와 코드 라벨((가) 좌표, (나) 그림 요소, 합친 것)을 정답과 견줘 <out>/charts.md, charts.json 을 쓴다.
  vision-merge --runs <이름=그림 라벨 실행의 출력,..> --out <디렉터리>
      여러 실행의 tag-answers.json 을 합친다 (값 후보마다 과반이 같은 갈래·계열·범주를 냈을 때만). <out>/tag-answers.json, vision-labels.json, merge.json. 정답을 읽지 않는다.
  vision-score --chart-gold <도표와 라벨의 정답> --runs <이름=출력,..> [--merged <vision-merge 의 출력>] --out <디렉터리>
      그림 라벨을 정답과 견줘 <out>/vision.md, vision.json 을 쓴다 (실행별, 합친 것, 세 실행이 서로 같은 비율).
--out 은 반드시 준다. 상대 경로는 ts/ 기준이다.`;

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

const factChartsSchema = z.record(z.enum(["a", "b"]), z.object({ linked: z.array(z.object({ id: z.string(), page: z.number(), chart: z.string(), bbox: z.array(z.number()), word: chartLabelsSchema.shape.words.element })), ambiguous: z.array(z.string()) }));

const frac = (n: number, d: number): string => `${n}/${d}`;

/** 정답의 도표 꼴을 보고에 쓰는 묶음으로. */
const FORM_KO: Record<string, string> = { scatter: "산점도(눈금뿐)", line_multi: "여러 계열 꺾은선", line_single: "계열 하나 꺾은선", bar_grouped: "묶은 막대", bar_single: "계열 하나 막대", hbar_single: "가로 막대", bar_stacked_line: "쌓은 막대 + 꺾은선", bar_line: "막대 + 꺾은선" };

export type Method = "coord" | "elem" | "label";
const METHOD_KO: Record<Method, string> = { coord: "(가) 좌표", elem: "(나) 그림 요소", label: "합친 것" };

/** 계열을 붙이는 방법((나), 합친 것)은 값으로 보지 못한 자리에도 「계열 못 붙임」을 낸다: 계열의 분모는 정답의 자리 전부다. */
export function predictor(facts: Record<"a" | "b", Map<string, FactChart>>, method: Method): (doc: "a" | "b", id: string) => Predicted {
  return (doc, id) => {
    const word = facts[doc].get(id)?.word;
    if (!word || word.role !== "value") return method === "coord" ? { role: "not_value", category: null } : { role: "not_value", category: null, series: null };
    if (method === "coord") return { role: "value", category: word.coord?.category ?? null };
    const picked = method === "elem" ? word.elem : word.label;
    return { role: "value", category: picked?.category ?? null, series: picked?.series ?? null };
  };
}

const tagAnswerSchema = z.object({ role: z.enum(["value", "tick", "other"]), title: z.string().nullable(), series: z.string().nullable(), category: z.string().nullable(), unit: z.string().nullable() });
const tagAnswersSchema = z.object({ a: z.record(z.string(), tagAnswerSchema), b: z.record(z.string(), tagAnswerSchema) });
type TagAnswers = z.infer<typeof tagAnswersSchema>;

/** 그림 라벨의 예측. 답이 없거나 값이 아니라고 한 자리는 「못 붙임」이다 (분모는 정답의 자리 전부). */
export function visionPredictor(answers: TagAnswers): (doc: "a" | "b", id: string) => Predicted {
  return (doc, id) => {
    const answer = answers[doc][id];
    if (!answer || answer.role !== "value") return { role: "not_value", category: null, series: null };
    return { role: "value", category: answer.category, series: answer.series };
  };
}

const scopesOf = (gold: ChartGold): { title: string; pick: (label: ChartGold["labels"][number]) => boolean }[] => {
  const kindOf = new Map(gold.charts.map((chart) => [chart.id, chart.kind]));
  const pageOf = new Map(gold.charts.map((chart) => [chart.id, `${chart.doc}:${chart.page}`]));
  return [
    { title: "전체", pick: () => true },
    { title: "보고서 62쪽 (계열 하나 꺾은선 7개)", pick: (label) => pageOf.get(label.chart) === "a:62" },
    { title: "62쪽 밖", pick: (label) => pageOf.get(label.chart) !== "a:62" },
    ...[...new Set(gold.charts.map((chart) => chart.kind))].map((kind) => ({ title: `꼴: ${FORM_KO[kind] ?? kind}`, pick: (label: ChartGold["labels"][number]) => kindOf.get(label.chart) === kind })),
  ];
};

export function renderVisionScore(gold: ChartGold, runs: { name: string; answers: TagAnswers }[], merged: TagAnswers | null): { lines: string[]; json: Record<string, unknown> } {
  const lines: string[] = ["# 그림 라벨의 채점 (저장된 답에서. 호출 0)", ""];
  const head = ["실행", "값으로 본 것 / 정답 값", "범주: 맞음 / 붙임", "계열: 맞음 / 붙임", "계열 이름 없는 자리에 계열을 붙임", "범주·계열 둘 다 맞음 / 정답 값", "값이 아닌 것을 값으로 봄"];
  const all = [...runs, ...(merged ? [{ name: "합친 것 (과반)", answers: merged }] : [])];
  const scores: Record<string, Record<string, LabelScore>> = {};
  lines.push("## 라벨의 정확도 (분모는 정답의 자리 전부. 번호표가 없던 자리와 답이 버려진 자리는 「못 붙임」이다)", "");
  for (const scope of scopesOf(gold)) {
    const subset = gold.labels.filter(scope.pick);
    if (subset.length === 0) continue;
    lines.push(`### ${scope.title}`, "");
    scores[scope.title] = {};
    const rows = all.map((run) => {
      const score = scoreLabels(subset, visionPredictor(run.answers));
      (scores[scope.title] as Record<string, LabelScore>)[run.name] = score;
      return scoreRow(run.name, score, true);
    });
    lines.push(...table(head, rows));
  }
  // 세 실행이 서로 같은 비율: 정답이 값인 자리마다 실행들의 답(갈래·계열·범주)이 같은가.
  const values = gold.labels.filter((label) => label.role === "value");
  const same = { all: 0, majority: 0, none: 0, unanswered: 0 };
  for (const label of values) {
    const keys = runs.map((run) => answerKey(run.answers[label.doc][label.id] as TagAnswer | undefined));
    const counts = new Map<string, number>();
    for (const key of keys) counts.set(key, (counts.get(key) ?? 0) + 1);
    const top = Math.max(...counts.values());
    if (keys.every((key) => key === "∅")) same.unanswered += 1;
    else if (top === runs.length) same.all += 1;
    else if (top * 2 > runs.length) same.majority += 1;
    else same.none += 1;
  }
  lines.push("## 실행들이 서로 같은가 (정답이 값인 자리, 갈래·계열·범주가 글자까지 같을 때 같다)", "");
  lines.push(...table(["", "자리 수"], [["모든 실행이 같은 답", frac(same.all, values.length)], ["과반만 같은 답", frac(same.majority, values.length)], ["과반 없음", frac(same.none, values.length)], ["어느 실행에도 답 없음 (번호표가 없던 자리 포함)", frac(same.unanswered, values.length)]]));
  const ticks = gold.labels.filter((label) => label.role === "tick");
  lines.push("## 눈금 (정답이 눈금인 자리를 눈금이라고 답한 수)", "");
  lines.push(...table(["실행", "눈금이라고 답함", "값이라고 답함", "그 밖이라고 답함", "답 없음"], all.map((run) => {
    const got = ticks.map((label) => run.answers[label.doc][label.id]?.role ?? "none");
    return [run.name, frac(got.filter((role) => role === "tick").length, ticks.length), frac(got.filter((role) => role === "value").length, ticks.length), frac(got.filter((role) => role === "other").length, ticks.length), frac(got.filter((role) => role === "none").length, ticks.length)];
  })));
  const goldIds = new Set(gold.labels.map((label) => `${label.doc}:${label.id}`));
  lines.push("## 정답의 도표가 아닌 자리(코드가 잘못 찾은 도표의 번호표)에 대한 답", "");
  lines.push(...table(["실행", "값이라고 답함", "눈금", "그 밖", "그중 라벨(범주 있음)이 실리는 것"], all.map((run) => {
    const outside = (["a", "b"] as const).flatMap((doc) => Object.entries(run.answers[doc]).filter(([id]) => !goldIds.has(`${doc}:${id}`)).map(([, answer]) => answer));
    return [run.name, frac(outside.filter((answer) => answer.role === "value").length, outside.length), frac(outside.filter((answer) => answer.role === "tick").length, outside.length), frac(outside.filter((answer) => answer.role === "other").length, outside.length), String(outside.filter((answer) => answer.role === "value" && answer.category !== null).length)];
  })));
  for (const run of all) {
    const wrong = ((scores["전체"] as Record<string, LabelScore>)[run.name] as LabelScore).wrong;
    lines.push(`## 틀리게 붙인 자리: ${run.name} (${wrong.length}건)`, "");
    lines.push(...table(["문서", "자리", "원문", "정답 도표", "무엇", "정답", "붙인 것"], wrong.map((item) => [item.doc, item.id, item.raw, item.chart, item.what, item.gold, item.got])));
  }
  return { lines, json: { scores, same, ticks: ticks.length } };
}

function scoreRow(name: string, score: LabelScore, withSeries: boolean): string[] {
  return [
    name,
    frac(score.seenAsValue, score.values),
    `${frac(score.category.correct, score.category.attached)} · 못 붙임 ${score.category.none}`,
    withSeries ? `${frac(score.series.correct, score.series.attached)} (엄격 ${score.series.correctStrict}) · 못 붙임 ${score.series.none} · 계열 있는 정답 ${score.series.gold}` : "-",
    withSeries ? frac(score.seriesOnUnnamed.attached, score.seriesOnUnnamed.gold) : "-",
    withSeries ? frac(score.bothCorrect, score.values) : "-",
    frac(score.nonValuesSeenAsValue, score.nonValues),
  ];
}

export function renderChartScore(gold: ChartGold, labels: Record<"a" | "b", ChartLabels>, facts: Record<"a" | "b", Map<string, FactChart>>): { lines: string[]; json: Record<string, unknown> } {
  const lines: string[] = ["# 도표 찾기와 코드 라벨의 채점 (호출 0)", ""];
  const json: Record<string, unknown> = {};
  // 도표 찾기
  const finding = (["a", "b"] as const).map((doc) => ({ doc, gold: gold.charts.filter((chart) => chart.doc === doc), found: labels[doc].charts }));
  const matches = finding.map((item) => ({ ...item, match: matchCharts(item.gold, item.found) }));
  lines.push("## 도표 찾기", "");
  lines.push(
    ...table(
      ["문서", "정답 도표", "코드가 도표로 본 것", "맞은 것", "정밀도", "재현율", "못 찾은 정답", "정답에 없는 것"],
      matches.map((item) => {
        const hit = Object.keys(item.match.matched).length;
        return [item.doc, String(item.gold.length), String(item.found.length), String(hit), frac(hit, item.found.length), frac(hit, item.gold.length), item.match.missed.join(", ") || "-", item.match.extra.join(", ") || "-"];
      }),
    ),
  );
  json.finding = matches.map((item) => ({ doc: item.doc, gold: item.gold.length, found: item.found.length, ...item.match }));
  const head = ["방법", "값으로 본 것 / 정답 값", "범주: 맞음 / 붙임", "계열: 맞음 / 붙임", "계열 이름 없는 자리에 계열을 붙임", "범주·계열 둘 다 맞음 / 정답 값", "값이 아닌 것을 값으로 봄"];
  const methods: Method[] = ["coord", "elem", "label"];
  const scopes = scopesOf(gold);
  lines.push("## 라벨의 정확도 (정답의 자리 = 값 후보 ID 단위. 겹친 표가 같은 글자를 두 번 실은 자리는 두 번 센다)", "");
  const scores: Record<string, Record<string, LabelScore>> = {};
  for (const scope of scopes) {
    const subset = gold.labels.filter(scope.pick);
    if (subset.length === 0) continue;
    lines.push(`### ${scope.title}`, "");
    const rows: string[][] = [];
    scores[scope.title] = {};
    for (const method of methods) {
      const score = scoreLabels(subset, predictor(facts, method));
      (scores[scope.title] as Record<string, LabelScore>)[method] = score;
      rows.push(scoreRow(METHOD_KO[method], score, method !== "coord"));
    }
    lines.push(...table(head, rows));
  }
  json.scores = scores;
  const all = (scores["전체"] as Record<string, LabelScore>).label as LabelScore;
  lines.push("## 합친 것이 틀리게 붙인 자리", "");
  lines.push(...table(["문서", "자리", "원문", "정답 도표", "무엇", "정답", "붙인 것"], all.wrong.map((item) => [item.doc, item.id, item.raw, item.chart, item.what, item.gold, item.got])));
  // 못 붙인 까닭
  const reasons: Record<string, number> = {};
  for (const label of gold.labels.filter((item) => item.role === "value")) {
    const word = facts[label.doc].get(label.id)?.word;
    const reason = !word ? "도표 낱말로 걸리지 않음" : word.role !== "value" ? `값이 아니라 ${word.role} 로 봄` : word.label?.category === null ? `범주 못 붙임: ${word.label.status}` : null;
    if (reason !== null) reasons[reason] = (reasons[reason] ?? 0) + 1;
  }
  lines.push("## 합친 것이 범주를 못 붙인 까닭", "", ...table(["까닭", "자리 수"], Object.entries(reasons).sort((x, y) => y[1] - x[1]).map(([reason, count]) => [reason, String(count)])));
  json.noCategoryReasons = reasons;
  json.valueTextsWithoutFact = gold.charts.filter((chart) => chart.value_texts_without_fact.length > 0).map((chart) => ({ chart: chart.id, texts: chart.value_texts_without_fact }));
  return { lines, json };
}

export async function main(argv: string[], out: Out = console.log): Promise<number> {
  const [command, ...rest] = argv;
  try {
    const { values } = parseArgs({ args: rest, options: { a: { type: "string" }, b: { type: "string" }, "labels-a": { type: "string" }, "labels-b": { type: "string" }, "chart-gold": { type: "string" }, facts: { type: "string" }, runs: { type: "string" }, merged: { type: "string" }, out: { type: "string" } }, allowPositionals: false });
    if (!values.out) return out(USAGE), 2;
    const outDir = resolveOutputPath(values.out);
    if (command === "vision-merge" || command === "vision-score") {
      const runs = (values.runs ?? "").split(",").filter((item) => item !== "").map((item) => {
        const [name, dir] = item.split("=") as [string, string];
        const file = readJsonFile(join(resolveOutputPath(dir), "outcome.json"), "실행 결과") as { synthetic?: string; method?: string };
        if (file.synthetic) throw new ReconcileFileError(`${dir}: 모의 실행의 결과다. 실측과 섞지 않는다`);
        if (file.method !== "lv") throw new ReconcileFileError(`${dir}: 그림 라벨의 실행 결과가 아니다`);
        return { name, answers: readAs(join(resolveOutputPath(dir), "tag-answers.json"), tagAnswersSchema, "그림 라벨의 답") };
      });
      if (runs.length === 0) return out(USAGE), 2;
      mkdirSync(outDir, { recursive: true });
      if (command === "vision-merge") {
        const merged = { a: mergeVision(runs.map((run) => run.answers.a)), b: mergeVision(runs.map((run) => run.answers.b)) };
        writeJson(join(outDir, "tag-answers.json"), { a: merged.a.answers, b: merged.b.answers });
        writeJson(join(outDir, "vision-labels.json"), { a: visionLabels(merged.a.answers), b: visionLabels(merged.b.answers) });
        writeJson(join(outDir, "merge.json"), { rule: "값 후보마다 실행의 과반이 같은 (갈래, 계열, 범주)를 냈을 때만 그 답을 쓴다. 제목과 단위는 그렇게 답한 실행 가운데 가장 앞선 것의 글자", runs: runs.map((run) => run.name), agreement: { a: merged.a.agreement, b: merged.b.agreement } });
        out(`[vision-merge] 실행 ${runs.length}개 · 문서 a: 답이 있는 값 후보 ${merged.a.agreement.ids} (전부 같음 ${merged.a.agreement.all}, 과반 ${merged.a.agreement.majority}, 과반 없음 ${merged.a.agreement.none}) · 문서 b: ${merged.b.agreement.ids} (${merged.b.agreement.all}, ${merged.b.agreement.majority}, ${merged.b.agreement.none}) · 실리는 라벨 a ${Object.keys(visionLabels(merged.a.answers)).length}, b ${Object.keys(visionLabels(merged.b.answers)).length}`);
        return 0;
      }
      if (!values["chart-gold"]) return out(USAGE), 2;
      const gold = readAs(resolveInputPath(values["chart-gold"]), chartGoldSchema, "도표와 라벨의 정답");
      const merged = values.merged ? readAs(join(resolveOutputPath(values.merged), "tag-answers.json"), tagAnswersSchema, "합친 그림 라벨") : null;
      const { lines, json } = renderVisionScore(gold, runs, merged);
      writeFileSync(join(outDir, "vision.md"), `${lines.join("\n")}\n`);
      writeJson(join(outDir, "vision.json"), json);
      out(lines.slice(0, 16).join("\n"));
      return 0;
    }
    if (!values["labels-a"] || !values["labels-b"]) return out(USAGE), 2;
    const labels = { a: readAs(resolveInputPath(values["labels-a"]), chartLabelsSchema, "도표 라벨"), b: readAs(resolveInputPath(values["labels-b"]), chartLabelsSchema, "도표 라벨") };
    if (command === "chart-facts") {
      if (!values.a || !values.b) return out(USAGE), 2;
      mkdirSync(outDir, { recursive: true });
      const result: Record<string, unknown> = {};
      const code: Record<string, Record<string, string>> = {};
      for (const doc of ["a", "b"] as const) {
        const extraction = extractDocument(readDoc(resolveInputPath(values[doc] as string)), doc);
        const linked = linkFactsToCharts(extraction, labels[doc]);
        result[doc] = linked;
        const { tags } = assignTags(linked.linked);
        writeJson(join(outDir, `tags-${doc}.json`), tags);
        code[doc] = codeLabels(linked.linked);
        out(`[chart-facts] 문서 ${doc}: 도표 ${labels[doc].charts.length}개 · 도표 낱말이 걸린 값 후보 ${linked.linked.length}개 (가를 수 없어 걸지 않은 것 ${linked.ambiguous.length}) · 번호표 ${tags.length}개 (쪽 ${new Set(tags.map((tag) => tag.page)).size}) · 코드 라벨 ${Object.keys(code[doc] as object).length}개`);
      }
      writeJson(join(outDir, "fact-charts.json"), result);
      writeJson(join(outDir, "code-labels.json"), code);
      return 0;
    }
    if (command === "chart-score") {
      if (!values["chart-gold"] || !values.facts) return out(USAGE), 2;
      mkdirSync(outDir, { recursive: true });
      const gold = readAs(resolveInputPath(values["chart-gold"]), chartGoldSchema, "도표와 라벨의 정답");
      const saved = readAs(resolveInputPath(values.facts), factChartsSchema, "값 후보별 도표 낱말");
      const facts = { a: new Map((saved.a?.linked ?? []).map((item) => [item.id, item as FactChart])), b: new Map((saved.b?.linked ?? []).map((item) => [item.id, item as FactChart])) };
      const { lines, json } = renderChartScore(gold, labels, facts);
      writeFileSync(join(outDir, "charts.md"), `${lines.join("\n")}\n`);
      writeJson(join(outDir, "charts.json"), json);
      out(lines.slice(0, 40).join("\n"));
      return 0;
    }
    return out(USAGE), 2;
  } catch (error) {
    if (error instanceof ReconcileFileError || error instanceof ExamError) {
      out(`오류: ${error.message}`);
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
