import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { z } from "zod";
import { ReconcileFileError, documentSchema, loadUnits, readJsonFile, resolveInputPath, resolveOutputPath } from "../adapters/files.js";
import { extractDocument, type DocModel } from "../domain/facts.js";
import { VALUE_KINDS, type ValueKind } from "../domain/kinds.js";
import { chartLabelsSchema, matchCharts } from "./charts.js";
import { ExamError } from "./exam.js";
import { BUCKETS, checkDocKindGold, docChartGoldSchema, docKindGoldSchema, drawDocTargets, headerStats, scoreDocKinds, targetContext, type DocChartGold, type DocKindGold, type Quota } from "./generalize.js";
import { TallyError } from "./hybrid.js";
import { KIND_KO, renderKindScore } from "./kind-report.js";

// 밴드 G1 (MARC-TICK-34) 의 명령: 문서 하나를 받는다. 밴드 V·W 의 명령(`cli3.ts`, `cli4.ts`)은 KB 두 문서의 시험지·정답 쌍을 인자로 요구해 다른 문서에 쓸 수 없다.
// 모델을 부르지 않는다. 규칙(`domain/kinds.ts`, `py/docparse/charts.py`)은 고치지 않고 부르기만 한다.

const USAGE = `쓰임: tsx src/compare/cli9.ts <명령> --doc <문서 모델 document.json> --out <디렉터리> [..]
  stats                         쪽·표·값 후보의 수와 머리글이 선 표·칸의 수 (stats.json, stats.md)
  draw --seed <n> --quota <문장 속,숫자뿐인 블록,표의 글 칸 속,표의 숫자 칸>
                                종류의 정답을 달 표본과 앞뒤 글 (kind-targets.json). 종류 규칙을 부르지 않는다
  kinds --kind-gold <종류의 정답>   종류 규칙을 돌려 정답과 견준다 (kinds.md, kinds.json)
  chart-match --chart-gold <도표의 정답> --labels <chart-labels.json>
                                도표 찾기의 대조 (chart-match.md, chart-match.json)
--out 은 반드시 준다. 상대 경로는 ts/ 기준이다.`;

type Out = (line: string) => void;

const sha = (path: string): string => createHash("sha256").update(readFileSync(path)).digest("hex");
const writeJson = (path: string, value: unknown): void => writeFileSync(path, `${JSON.stringify(value, null, 1)}\n`);
const table = (head: string[], rows: string[][]): string[] => [`| ${head.join(" | ")} |`, `|${head.map((_, index) => (index === 0 ? "---" : "---:")).join("|")}|`, ...rows.map((row) => `| ${row.join(" | ")} |`), ""];
const frac = (n: number, of: number): string => `${n}/${of}`;
const BUCKET_KO: Record<string, string> = { all: "전체", sentence: "문장 속", number_only_block: "숫자뿐인 블록", table_text_cell: "표의 글 칸 속", table_cell: "표의 숫자 칸" };
const CHART_KO: Record<string, string> = { bar_v: "세로 막대", bar_h: "가로 막대", line: "꺾은선", pie: "원·도넛", other: "그 밖" };

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

export function parseQuota(text: string): Quota {
  const parts = text.split(",").map((part) => Number(part));
  if (parts.length !== BUCKETS.length || parts.some((part) => !Number.isInteger(part) || part < 0)) throw new ReconcileFileError(`--quota 는 0 이상의 정수 ${BUCKETS.length}개다 (${BUCKETS.join(",")})`);
  return Object.fromEntries(BUCKETS.map((bucket, index) => [bucket, parts[index] as number])) as Quota;
}

export function renderDocKinds(gold: DocKindGold, result: ReturnType<typeof scoreDocKinds>): string {
  const lines: string[] = [`# 종류 규칙의 채점: ${gold.document.file_name} (호출 0)`, ""];
  lines.push(`표본 ${result.sampled}자리 (씨앗 ${gold.seed}). 정답이 「모르겠다」여서 분모에서 뺀 자리 ${result.unsure}. 채점한 자리 ${result.score.total}.`, "");
  lines.push(`규칙의 종류가 정답과 같은 자리: ${frac(result.score.correct, result.score.total)} (애매 표시의 다른 쪽도 맞다고 보면 ${frac(result.score.correctLenient, result.score.total)}, 애매 표시 ${result.score.ambiguous}자리).`, "");
  lines.push("## 값을 거른 것과 값이 아닌 것을 남긴 것 (갈래별)", "");
  lines.push(...table(["갈래", "정답이 값인데 규칙이 값이 아니라고 거름 / 정답이 값", "정답이 값이 아닌데 규칙이 값으로 남김 / 정답이 값이 아님"], Object.keys(result.valueFiltered).map((bucket) => [BUCKET_KO[bucket] ?? bucket, frac(result.valueFiltered[bucket]?.n ?? 0, result.valueFiltered[bucket]?.of ?? 0), frac(result.nonValueKept[bucket]?.n ?? 0, result.nonValueKept[bucket]?.of ?? 0)])));
  lines.push("## 헷갈린 짝 (행 = 정답, 열 = 규칙)", "");
  lines.push(...table(["정답 \\ 규칙", ...VALUE_KINDS.map((kind) => KIND_KO[kind]), "합"], VALUE_KINDS.map((g) => [KIND_KO[g], ...VALUE_KINDS.map((p) => String(result.confusion[g][p])), String(VALUE_KINDS.reduce((total, p) => total + result.confusion[g][p], 0))])));
  lines.push(renderKindScore(result.score));
  return lines.join("\n");
}

export function renderChartMatch(gold: DocChartGold, found: { id: string; page: number; bbox: number[] }[]): { md: string; json: Record<string, unknown> } {
  const match = matchCharts(gold.charts.map((chart) => ({ id: chart.id, doc: "a" as const, page: chart.page, kind: chart.kind, title: chart.title, series: null, bbox: chart.bbox, value_texts_without_fact: [] })), found);
  const hit = Object.keys(match.matched).length;
  if (hit + match.missed.length !== gold.charts.length || hit + match.extra.length !== found.length) throw new TallyError("도표 대조의 합이 맞지 않는다");
  const kinds = [...new Set(gold.charts.map((chart) => chart.kind))];
  const row = (title: string, charts: DocChartGold["charts"]): string[] => {
    const got = charts.filter((chart) => match.matched[chart.id] !== undefined).length;
    return [title, String(charts.length), frac(got, charts.length), frac(charts.length - got, charts.length)];
  };
  const lines = [`# 도표 찾기의 대조: ${gold.document.file_name} (호출 0)`, "", `정답 도표 ${gold.charts.length}개(${gold.pages}쪽 전부를 봄), 코드가 도표로 본 것 ${found.length}개. 찾은 것 ${frac(hit, gold.charts.length)}, 코드가 낸 것 가운데 정답과 겹친 것 ${frac(hit, found.length)}, 정답에 없는 것 ${match.extra.length}.`, "같은 쪽에서 겹친 넓이가 작은 쪽 상자의 절반 이상이면 같은 도표로 본다(밴드 W 의 `matchCharts`). 정답의 상자는 쪽 그림에서 읽은 어림이라 이 대조는 기계의 첫 대조이고, 못 찾은 것과 정답에 없는 것은 쪽 그림으로 다시 본다.", ""];
  lines.push(...table(["갈래", "정답", "찾음", "못 찾음"], [...kinds.map((kind) => row(CHART_KO[kind] ?? kind, gold.charts.filter((chart) => chart.kind === kind))), row("합", gold.charts)]));
  lines.push(...table(["값 숫자가 그림에", "정답", "찾음", "못 찾음"], ["yes", "partial", "no"].map((printed) => row(printed, gold.charts.filter((chart) => chart.valuesPrinted === printed)))));
  lines.push("## 못 찾은 정답", "", ...table(["ID", "쪽", "갈래", "제목"], gold.charts.filter((chart) => match.matched[chart.id] === undefined).map((chart) => [chart.id, String(chart.page), CHART_KO[chart.kind] ?? chart.kind, chart.title])));
  lines.push("## 정답에 없는 것", "", ...table(["코드의 ID", "쪽", "상자(pt)"], found.filter((chart) => match.extra.includes(chart.id)).map((chart) => [chart.id, String(chart.page), chart.bbox.map((n) => Math.round(n)).join(" ")])));
  return { md: lines.join("\n"), json: { gold: gold.charts.length, found: found.length, ...match } };
}

export async function main(argv: string[], out: Out = console.log, log: Out = console.error): Promise<number> {
  const [command, ...rest] = argv;
  let values;
  try {
    values = parseArgs({ args: rest, options: { doc: { type: "string" }, out: { type: "string" }, seed: { type: "string" }, quota: { type: "string" }, "kind-gold": { type: "string" }, "chart-gold": { type: "string" }, labels: { type: "string" } } }).values;
  } catch (error) {
    out(`${(error as Error).message}\n\n${USAGE}`);
    return 2;
  }
  try {
    if (!["stats", "draw", "kinds", "chart-match"].includes(command ?? "") || !values.doc || !values.out) return out(USAGE), 2;
    const docPath = resolveInputPath(values.doc);
    const model = readDoc(docPath);
    const outDir = resolveOutputPath(values.out);
    mkdirSync(outDir, { recursive: true });
    const source = { file_name: model.source.file_name, sha256: model.source.sha256 };

    if (command === "stats") {
      const extraction = extractDocument(model, "a", { titles: "own" });
      const buckets = Object.fromEntries(BUCKETS.map((bucket) => [bucket, extraction.facts.filter((fact) => fact.bucket === bucket).length]));
      const headers = headerStats(model, extraction, loadUnits());
      const stats = { document: source, documentJsonSha256: sha(docPath), pages: model.source.page_count, pagesWithoutText: extraction.pagesWithoutText.length, facts: extraction.facts.length, buckets, headers };
      writeJson(join(outDir, "stats.json"), stats);
      const md = [`# ${source.file_name}`, "", `쪽 ${stats.pages} (텍스트 계층 없는 쪽 ${stats.pagesWithoutText}), 표 ${headers.tables} (격자 못 세움 ${headers.failed}), 값 후보 ${stats.facts}: ${BUCKETS.map((bucket) => `${BUCKET_KO[bucket]} ${buckets[bucket]}`).join(", ")}.`, "", ...table(["머리글", "수 / 분모"], [["머리글 행이 선 표 / 격자가 선 표", frac(headers.withHeaderRows, headers.tables - headers.failed)], ["머리글 열이 선 표 / 격자가 선 표", frac(headers.withHeaderCols, headers.tables - headers.failed)], ["둘 다 선 표 / 격자가 선 표", frac(headers.withBoth, headers.tables - headers.failed)], ["숫자 칸 전부가 열·행 열쇠를 가진 표 / 숫자 칸이 있는 표", frac(headers.tablesAllCellsKeyed, headers.tablesWithNumbers)], ["열 열쇠가 있는 숫자 칸 / 숫자 칸", frac(headers.cellsWithCol, headers.cells)], ["행 열쇠가 있는 숫자 칸 / 숫자 칸", frac(headers.cellsWithRow, headers.cells)], ["둘 다 있는 숫자 칸 / 숫자 칸", frac(headers.cellsWithBoth, headers.cells)], ["둘 다 없는 숫자 칸 / 숫자 칸", frac(headers.cellsWithNeither, headers.cells)], ["정상 표의 숫자 칸 가운데 둘 다 있는 것", frac(headers.okCellsWithBoth, headers.okCells)]])].join("\n");
      writeFileSync(join(outDir, "stats.md"), `${md}\n`);
      out(md);
      return 0;
    }

    if (command === "draw") {
      if (!values.seed || !values.quota) return out(USAGE), 2;
      const quota = parseQuota(values.quota);
      const seed = Number(values.seed);
      // 종류 옵션을 켜지 않는다: 표본과 앞뒤 글은 규칙의 출력을 담지 않는다.
      const extraction = extractDocument(model, "a", { titles: "own" });
      const targets = drawDocTargets(extraction, seed, quota).map((target) => targetContext(model, extraction, target));
      writeJson(join(outDir, "kind-targets.json"), { document: source, seed, quota, targets });
      const count: Record<string, number> = {};
      for (const target of targets) count[target.bucket] = (count[target.bucket] ?? 0) + 1;
      out(JSON.stringify({ targets: targets.length, count }, null, 1));
      return 0;
    }

    if (command === "kinds") {
      if (!values["kind-gold"]) return out(USAGE), 2;
      const goldPath = resolveInputPath(values["kind-gold"]);
      const gold: DocKindGold = readAs(goldPath, docKindGoldSchema, "종류의 정답");
      if (gold.document.sha256 !== source.sha256) throw new ReconcileFileError("종류의 정답이 다른 문서의 것이다 (원본 PDF 의 해시가 다르다)");
      const extraction = extractDocument(model, "a", { titles: "own", kinds: true });
      const problems = checkDocKindGold(gold, extraction);
      if (problems.length > 0) throw new ReconcileFileError(`종류의 정답이 문서와 맞지 않는다\n${problems.join("\n")}`);
      const result = scoreDocKinds(gold, (id) => extraction.kinds?.[id]);
      const all: Record<string, Record<string, number>> = {};
      for (const fact of extraction.facts) (all[fact.bucket] ??= {})[extraction.kinds?.[fact.id] as string] = (all[fact.bucket]?.[extraction.kinds?.[fact.id] as string] ?? 0) + 1;
      const md = [renderDocKinds(gold, result), "## 규칙이 모든 값 후보에 붙인 종류 (정답이 없는 자리 포함, 갈래별)", "", ...table(["갈래", ...Object.values(KIND_KO)], Object.entries(all).map(([name, bag]) => [BUCKET_KO[name] ?? name, ...Object.keys(KIND_KO).map((kind) => `${bag[kind] ?? 0}`)]))].join("\n");
      writeFileSync(join(outDir, "kinds.md"), md);
      const predicted = Object.fromEntries(gold.items.map((item) => [item.id, extraction.kinds?.[item.id] as ValueKind]));
      writeJson(join(outDir, "kinds.json"), { goldSha256: sha(goldPath), ...result, all, predicted });
      out(md);
      return 0;
    }

    if (!values["chart-gold"] || !values.labels) return out(USAGE), 2;
    const chartGold: DocChartGold = readAs(resolveInputPath(values["chart-gold"]), docChartGoldSchema, "도표의 정답");
    if (chartGold.document.sha256 !== source.sha256) throw new ReconcileFileError("도표의 정답이 다른 문서의 것이다 (원본 PDF 의 해시가 다르다)");
    const labels = readAs(resolveInputPath(values.labels), chartLabelsSchema, "도표 라벨");
    const rendered = renderChartMatch(chartGold, labels.charts);
    writeFileSync(join(outDir, "chart-match.md"), `${rendered.md}\n`);
    writeJson(join(outDir, "chart-match.json"), { goldSha256: sha(resolveInputPath(values["chart-gold"])), ...rendered.json });
    out(rendered.md);
    return 0;
  } catch (error) {
    if (error instanceof ReconcileFileError || error instanceof ExamError || error instanceof TallyError) {
      log(error.message);
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
