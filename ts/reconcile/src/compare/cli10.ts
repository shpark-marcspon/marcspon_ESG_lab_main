import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { z } from "zod";
import { ReconcileFileError, documentSchema, readJsonFile, resolveInputPath, resolveOutputPath } from "../adapters/files.js";
import { extractDocument, type DocModel } from "../domain/facts.js";
import { buildChartTables, cellHeaders, inputFromCodeLabels, inputFromVisionAnswers, type ChartTableInput, type ChartTablesOutput, type MapEntry, type NotBuiltReason } from "./chart-tables.js";
import { chartGoldSchema, chartLabelsSchema, linkFactsToCharts, type ChartGold } from "./charts.js";
import { idsInText, renderDocumentWithIds, unmaskedInIdDocument } from "./prose.js";
import { conservativeTokens } from "./render.js";

// 밴드 CT1 (MARC-TICK-36) 의 명령: 이름 붙은 도표를 표로 세운 파생 문서. 모델을 부르지 않는다.

const USAGE = `쓰임: tsx src/compare/cli10.ts <명령> [..] --out <디렉터리>
  build --doc <document.json> --doc-id <a|b> (--labels <chart-labels.json> | --input <다른 build 의 input.json>) [--vision <tag-answers.json> --source <이름>] [--mixed <reject|unnamed-row>]
        파생 문서(document.json), 대응표(chart-table-map.json), 도표별 결과(chart-tables.json, chart-tables.md), 쓴 라벨(input.json)
  score --chart-gold <도표 정답> --orig-a <원래 a> --orig-b <원래 b> --a <build 의 출력 a> --b <build 의 출력 b>
        「옳은 칸에 놓인 값」의 표 (score.json, score.md)
  dry   --orig-a .. --orig-b .. --a <build 의 출력 a> --b <..> --exam-a <시험지 사본 a 의 build 출력> --exam-b <..> [--show <a:62,a:47>]
        방법 D 의 요청 글 조립만 (보내지 않는다): 가림 확인, 원본 대 사본 diff, 토큰 어림, 쪽의 글 (dry.json, dry.md, request-*.txt)
--out 은 반드시 준다. 상대 경로는 ts/ 기준이다.`;

type Out = (line: string) => void;
const writeJson = (path: string, value: unknown): void => writeFileSync(path, `${JSON.stringify(value, null, 1)}\n`);
const table = (head: string[], rows: string[][]): string[] => [`| ${head.join(" | ")} |`, `|${head.map((_, index) => (index === 0 ? "---" : "---:")).join("|")}|`, ...rows.map((row) => `| ${row.join(" | ")} |`), ""];
const squash = (text: string): string => text.normalize("NFKC").replace(/\s+/g, "");

export const REASON_KO: Record<NotBuiltReason, string> = {
  no_values: "값으로 걸린 자리가 없음 (라벨이 없음)",
  no_category: "범주 라벨이 하나도 없음",
  mixed_series: "계열 이름이 있는 값과 없는 값이 섞임",
  duplicate_cell: "같은 계열·범주에 값이 둘 이상",
  duplicate_cell_no_series: "계열 이름 없이 한 범주에 값이 둘 이상 (범주만 있음)",
  reads_differently: "세워 보니 값 후보가 원래와 다르게 읽힘",
};

function readDoc(path: string): DocModel {
  const raw = readJsonFile(path, "문서 JSON");
  if (!documentSchema.safeParse(raw).success) throw new ReconcileFileError(`${path}: 문서 JSON 형식이 맞지 않는다`);
  // 파생 문서에 스키마가 요구하는 필드를 그대로 실어야 해서, 읽는 쪽이 쓰는 부분만 남긴 꼴이 아니라 파일의 내용 전체를 쓴다.
  return raw as DocModel;
}

function readAs<T>(path: string, schema: z.ZodType<T>, what: string): T {
  const parsed = schema.safeParse(readJsonFile(path, what));
  if (!parsed.success) throw new ReconcileFileError(`${path}: ${what} 형식이 맞지 않는다\n${z.prettifyError(parsed.error)}`);
  return parsed.data;
}

const visionSchema = z.record(z.string(), z.record(z.string(), z.object({ role: z.string(), title: z.string().nullable(), series: z.string().nullable(), category: z.string().nullable() })));
const mapSchema = z.object({ map: z.array(z.object({ id: z.string(), from: z.string(), kind: z.enum(["moved", "renumbered"]), chart: z.string().optional(), series: z.string().nullable().optional(), category: z.string().optional() })) });
const inputSchema = z.object({ source: z.string(), charts: z.array(z.object({ id: z.string(), page: z.number(), bbox: z.array(z.number()), title: z.string().nullable(), categories: z.array(z.string()).optional() })), values: z.array(z.object({ id: z.string(), chart: z.string(), series: z.string().nullable(), category: z.string().nullable(), bbox: z.array(z.number()) })) });
const resultsSchema = z.object({ charts: z.array(z.object({ chart: z.string(), page: z.number(), status: z.enum(["full", "partial", "not_built"]), reason: z.string().nullable(), tableId: z.string().nullable(), rows: z.number(), cols: z.number(), columnOrder: z.enum(["axis", "position"]).nullable(), singleUnnamedRow: z.boolean(), values: z.number(), moved: z.number(), left: z.array(z.string()), twins: z.array(z.string()) })) });

export function renderBuild(fileName: string, input: ChartTableInput, output: ChartTablesOutput): string {
  const built = output.charts.filter((chart) => chart.status !== "not_built");
  const lines = [`# 도표를 표로 세우기: ${fileName} (라벨 ${input.source}, 호출 0)`, ""];
  lines.push(`도표 ${output.charts.length}개 가운데 표로 선 것 ${built.length}개(전부 ${output.charts.filter((chart) => chart.status === "full").length}, 일부 ${output.charts.filter((chart) => chart.status === "partial").length}), 서지 못한 것 ${output.charts.length - built.length}개.`);
  lines.push(`계열 이름 없이 한 행으로 선 표 ${built.filter((chart) => chart.singleUnnamedRow).length}개. 값 후보 ${output.facts.before} → ${output.facts.after} (옮긴 것 ${output.facts.moved}, 순번이 바뀐 것 ${output.facts.renumbered}).`, "");
  const reasons = new Map<string, number>();
  for (const chart of output.charts) if (chart.reason !== null) reasons.set(chart.reason, (reasons.get(chart.reason) ?? 0) + 1);
  if (reasons.size > 0) lines.push(...table(["서지 못한 까닭", "도표"], [...reasons].map(([reason, n]) => [REASON_KO[reason as NotBuiltReason], String(n)])));
  lines.push(...table(["도표", "쪽", "결과", "새 표", "행 x 열", "값", "옮김", "남김", "겹친 둘째", "제목"], output.charts.map((chart) => [chart.chart, String(chart.page), chart.status === "not_built" ? `서지 못함: ${REASON_KO[chart.reason as NotBuiltReason]}` : chart.status === "full" ? "섬" : "일부만 섬", chart.tableId ?? "-", chart.status === "not_built" ? "-" : `${chart.rows} x ${chart.cols}${chart.singleUnnamedRow ? " (한 행)" : ""}${chart.columnOrder === "position" ? " (열의 차례: 값의 자리)" : ""}`, String(chart.values), String(chart.moved), String(chart.left.length), String(chart.twins.length), chart.title ?? "(없음)"])));
  return `${lines.join("\n")}\n`;
}

interface Side {
  original: DocModel;
  derived: DocModel;
  map: MapEntry[];
  input: ChartTableInput;
  results: z.infer<typeof resultsSchema>["charts"];
}

function readSide(originalPath: string, dir: string): Side {
  return { original: readDoc(originalPath), derived: readDoc(join(dir, "document.json")), map: readAs(join(dir, "chart-table-map.json"), mapSchema, "대응표").map as MapEntry[], input: readAs(join(dir, "input.json"), inputSchema, "라벨 입력"), results: readAs(join(dir, "chart-tables.json"), resultsSchema, "도표별 결과").charts };
}

export interface PlacementScore {
  values: number;
  /** 새 표의 칸으로 옮긴 것. */
  moved: number;
  correct: number;
  /** 라벨이 틀려 틀린 칸에 놓인 것 (표의 머리글은 라벨 그대로다). */
  wrongByLabel: number;
  /** 라벨은 맞는데 표의 머리글이 라벨과 다른 것 (변환의 틀림). */
  wrongByTransform: number;
  notMoved: Record<string, number>;
  nonValues: number;
  nonValuesMoved: number;
  wrong: { doc: string; id: string; raw: string; chart: string; cell: string; gold: string; got: string; blame: string }[];
}

/** 「옳은 칸에 놓인 값」: 정답의 값 자리마다, 파생 문서에서 그 값이 놓인 칸의 머리글(계열, 범주)을 표에서 다시 읽어 정답과 견준다. */
export function scorePlacement(gold: ChartGold, sides: Record<"a" | "b", Side>, filter: (label: ChartGold["labels"][number]) => boolean = () => true): PlacementScore {
  const score: PlacementScore = { values: 0, moved: 0, correct: 0, wrongByLabel: 0, wrongByTransform: 0, notMoved: {}, nonValues: 0, nonValuesMoved: 0, wrong: [] };
  const extractions = { a: extractDocument(sides.a.derived, "a"), b: extractDocument(sides.b.derived, "b") };
  for (const item of gold.labels.filter(filter)) {
    const side = sides[item.doc];
    const entry = side.map.find((candidate) => candidate.from === item.id && candidate.kind === "moved");
    if (item.role !== "value") {
      score.nonValues += 1;
      if (entry) score.nonValuesMoved += 1;
      continue;
    }
    score.values += 1;
    if (!entry) {
      const label = side.input.values.find((value) => value.id === item.id);
      const chart = label ? side.results.find((result) => result.chart === label.chart) : undefined;
      const why = !label ? "라벨이 값으로 보지 않음 (도표로 걸리지 않음 포함)" : chart?.twins.includes(item.id) ? "겹친 표가 같은 글자를 두 번 실은 자리의 둘째 (첫째만 옮김)" : chart?.status === "not_built" ? `도표가 서지 못함: ${REASON_KO[chart.reason as NotBuiltReason]}` : "범주 라벨이 없어 원래 자리에 남음";
      score.notMoved[why] = (score.notMoved[why] ?? 0) + 1;
      continue;
    }
    score.moved += 1;
    const headers = cellHeaders(side.derived, extractions[item.doc], entry.id);
    const named = (item.series ?? []).filter((name): name is string => name !== null);
    const ok = (series: string, category: string): boolean => (item.category ?? []).map(squash).includes(squash(category)) && (named.length === 0 ? series === "" : named.map(squash).includes(squash(series)));
    if (headers && ok(headers.series, headers.category)) {
      score.correct += 1;
      continue;
    }
    const label = side.input.values.find((value) => value.id === item.id);
    const labelOk = label !== undefined && label.category !== null && ok(label.series ?? "", label.category);
    if (labelOk) score.wrongByTransform += 1;
    else score.wrongByLabel += 1;
    score.wrong.push({ doc: item.doc, id: item.id, raw: item.raw, chart: item.chart, cell: entry.id, gold: `${named.join(" / ") || "(계열 이름 없음)"} · ${(item.category ?? []).join(" / ")}`, got: `${headers?.series || "(계열 이름 없음)"} · ${headers?.category ?? "?"}`, blame: labelOk ? "변환" : "라벨" });
  }
  return score;
}

export function renderScore(name: string, total: PlacementScore, parts: [string, PlacementScore][]): string {
  const lines = [`# 옳은 칸에 놓인 값: ${name} (호출 0)`, "", "분모는 도표 정답의 값 자리다. 옳은 칸 = 새 표에서 그 값이 놓인 칸의 머리글(행 = 계열, 열 = 범주)이 정답과 같다. 머리글은 표에서 다시 읽는다.", ""];
  const row = (label: string, s: PlacementScore): string[] => [label, `${s.moved}/${s.values}`, `${s.correct}/${s.values}`, `${s.wrongByLabel}`, `${s.wrongByTransform}`, `${s.values - s.moved}`, `${s.nonValuesMoved}/${s.nonValues}`];
  lines.push(...table(["구간", "표의 칸으로 옮긴 값 / 정답 값", "옳은 칸에 놓인 값 / 정답 값", "틀린 칸: 라벨의 틀림", "틀린 칸: 변환의 틀림", "옮기지 못한 값", "값이 아닌 것을 표에 놓음 / 정답의 값 아닌 자리"], [row("전체", total), ...parts.map(([label, s]) => row(label, s))]));
  lines.push("## 옮기지 못한 까닭", "", ...table(["까닭", "값"], Object.entries(total.notMoved).map(([why, n]) => [why, String(n)])));
  if (total.wrong.length > 0) lines.push("## 틀린 칸에 놓인 값", "", ...table(["문서", "원래 ID", "원문", "정답 도표", "놓인 칸", "정답 (계열 · 범주)", "놓인 칸의 머리글", "어디서 틀렸나"], total.wrong.map((w) => [w.doc, w.id, w.raw, w.chart, w.cell, w.gold, w.got, w.blame])));
  return `${lines.join("\n")}\n`;
}

/** 요청 글에서 한 쪽의 글만 뗀다. */
export function pageOf(text: string, page: number): string {
  const lines = text.split("\n");
  const start = lines.indexOf(`## ${page}쪽`);
  if (start === -1) return "";
  const end = lines.findIndex((line, index) => index > start && /^## \d+쪽$/.test(line));
  return lines.slice(start, end === -1 ? undefined : end).join("\n");
}

export async function main(argv: string[], out: Out = console.log): Promise<number> {
  const [command, ...rest] = argv;
  try {
    const { values } = parseArgs({ args: rest, options: { doc: { type: "string" }, "doc-id": { type: "string" }, labels: { type: "string" }, vision: { type: "string" }, source: { type: "string" }, out: { type: "string" }, "chart-gold": { type: "string" }, "orig-a": { type: "string" }, "orig-b": { type: "string" }, a: { type: "string" }, b: { type: "string" }, "exam-a": { type: "string" }, "exam-b": { type: "string" }, show: { type: "string" }, mixed: { type: "string" }, input: { type: "string" } } });
    if (!values.out) return out(USAGE), 2;
    const outDir = resolveOutputPath(values.out);
    mkdirSync(outDir, { recursive: true });
    const need = (key: keyof typeof values): string => {
      const value = values[key];
      if (typeof value !== "string") throw new ReconcileFileError(`--${key} 가 필요하다`);
      return resolveInputPath(value);
    };

    if (command === "build") {
      const docId = values["doc-id"] ?? "a";
      const document = readDoc(need("doc"));
      // --input: 값 후보 ID 단위의 라벨을 그대로 쓴다 (시험지 사본처럼 값만 다른 문서에 원본과 같은 라벨을 입힐 때).
      const labels = values.input ? null : readAs(need("labels"), chartLabelsSchema.extend({ charts: z.array(chartLabelsSchema.shape.charts.element.extend({ categories: z.array(z.string()).optional() })) }), "도표 라벨");
      const linked = labels ? linkFactsToCharts(extractDocument(document, docId), labels).linked : [];
      const input: ChartTableInput = !labels ? readAs(need("input"), inputSchema, "라벨 입력") : values.vision ? inputFromVisionAnswers(labels, linked, readAs(need("vision"), visionSchema, "그림 라벨의 답")[docId] ?? {}, values.source ?? "vision") : { ...inputFromCodeLabels(labels, linked), ...(values.source ? { source: values.source } : {}) };
      if (values.mixed !== undefined && values.mixed !== "reject" && values.mixed !== "unnamed-row") throw new ReconcileFileError("--mixed 는 reject 또는 unnamed-row 다");
      const output = buildChartTables(document, docId, input, { mixedSeries: values.mixed === "unnamed-row" ? "unnamed_row" : "reject" });
      writeFileSync(join(outDir, "document.json"), JSON.stringify(output.document, null, 1));
      writeJson(join(outDir, "chart-table-map.json"), { note: "ID 가 바뀐 값 후보만 싣는다. 여기에 없는 값 후보는 파생 문서에서도 원래 ID 그대로다. id = 파생 문서의 ID, from = 원래 문서의 ID.", source: document.source, labels: input.source, facts: output.facts, map: output.map });
      writeJson(join(outDir, "chart-tables.json"), { source: document.source, labels: input.source, facts: output.facts, charts: output.charts });
      writeJson(join(outDir, "input.json"), input);
      writeFileSync(join(outDir, "chart-tables.md"), renderBuild(document.source.file_name, input, output));
      const built = output.charts.filter((chart) => chart.status !== "not_built");
      out(JSON.stringify({ out: join(outDir, "document.json"), charts: output.charts.length, built: built.length, full: built.filter((chart) => chart.status === "full").length, singleUnnamedRow: built.filter((chart) => chart.singleUnnamedRow).length, facts: output.facts }));
      return 0;
    }

    if (command === "score") {
      const gold = readAs(need("chart-gold"), chartGoldSchema, "도표 정답");
      const sides = { a: readSide(need("orig-a"), need("a")), b: readSide(need("orig-b"), need("b")) };
      const total = scorePlacement(gold, sides);
      const kinds = [...new Set(gold.charts.map((chart) => chart.kind))].sort();
      const chartKind = new Map(gold.charts.map((chart) => [chart.id, chart.kind]));
      const parts: [string, PlacementScore][] = kinds.map((kind) => [`꼴 ${kind}`, scorePlacement(gold, sides, (label) => chartKind.get(label.chart) === kind)]);
      writeJson(join(outDir, "score.json"), { labels: sides.a.input.source, total, byKind: Object.fromEntries(parts) });
      writeFileSync(join(outDir, "score.md"), renderScore(`라벨 ${sides.a.input.source}`, total, parts));
      out(JSON.stringify({ out: join(outDir, "score.md"), values: total.values, moved: total.moved, correct: total.correct, wrongByLabel: total.wrongByLabel, wrongByTransform: total.wrongByTransform }));
      return 0;
    }

    if (command === "dry") {
      const report: Record<string, unknown> = { note: "요청 글을 조립만 했다. 공급사를 부르지 않았다. 토큰은 보수적인 글자 수 어림(한글 1자 1토큰, 그 밖 1자 0.6토큰)이고 과금 근거가 아니다." };
      const lines = ["# 파생 문서로 조립한 방법 D 의 요청 글 (dry-run, 호출 0)", ""];
      const rows: string[][] = [];
      const shows = (values.show ?? "").split(",").filter((item) => item !== "").map((item) => ({ doc: item.split(":")[0] as string, page: Number(item.split(":")[1]) }));
      for (const doc of ["a", "b"] as const) {
        const original = readDoc(need(`orig-${doc}`));
        const derived = readDoc(join(need(doc), "document.json"));
        const exam = readDoc(join(need(`exam-${doc}`), "document.json"));
        const text = (model: DocModel): string => renderDocumentWithIds(model, extractDocument(model, doc), { prefix: `${doc}:` });
        const [base, after, copy] = [text(original), text(derived), text(exam)];
        const leaks = unmaskedInIdDocument(after);
        const extraction = extractDocument(derived, doc);
        const wanted = extraction.facts.filter((fact) => fact.bucket !== "table_cell").map((fact) => fact.id);
        const shown = idsInText(after);
        const idsOk = shown.length === wanted.length && new Set(shown).size === shown.length && wanted.every((id) => shown.includes(id));
        writeFileSync(join(outDir, `request-${doc}.txt`), `${after}\n`);
        writeFileSync(join(outDir, `request-${doc}.original.txt`), `${base}\n`);
        report[doc] = { unmaskedValues: leaks.length, unmaskedLines: leaks, sameAsExamCopy: after === copy, proseCandidates: wanted.length, idMarks: shown.length, idMarksMatch: idsOk, chars: { original: base.length, derived: after.length }, tokens: { original: conservativeTokens(base), derived: conservativeTokens(after) }, facts: { tableCells: extraction.facts.length - wanted.length, withIdMark: wanted.length } };
        rows.push([doc, String(leaks.length), after === copy ? "같다" : "다르다", `${shown.length}/${wanted.length}${idsOk ? "" : " (어긋남)"}`, `${conservativeTokens(base)} → ${conservativeTokens(after)}`, `${base.length} → ${after.length}`]);
        if (leaks.length > 0 || after !== copy || !idsOk) throw new ReconcileFileError(`문서 ${doc}: 가림 확인에 걸렸다 (가리지 못한 값 ${leaks.length}, 시험지 사본과 ${after === copy ? "같다" : "다르다"}, ID 표시 ${shown.length}/${wanted.length})`);
        for (const show of shows.filter((item) => item.doc === doc)) {
          lines.push(`## 문서 ${doc} ${show.page}쪽: 원래 문서로 조립한 글`, "", "```", pageOf(base, show.page), "```", "", `## 문서 ${doc} ${show.page}쪽: 파생 문서로 조립한 글`, "", "```", pageOf(after, show.page), "```", "");
        }
      }
      lines.splice(2, 0, ...table(["문서", "가리지 못한 값 후보", "원본 대 시험지 사본의 요청 글", "ID 표시 / 표의 숫자 칸이 아닌 값 후보", "토큰 어림 (원래 → 파생)", "글자 수 (원래 → 파생)"], rows));
      writeJson(join(outDir, "dry.json"), report);
      writeFileSync(join(outDir, "dry.md"), `${lines.join("\n")}\n`);
      out(JSON.stringify({ out: join(outDir, "dry.md"), ...Object.fromEntries(rows.map((row) => [row[0], { unmasked: Number(row[1]), sameAsExamCopy: row[2] === "같다", tokens: row[4] }])) }));
      return 0;
    }
    return out(USAGE), 2;
  } catch (error) {
    if (error instanceof ReconcileFileError) return out(`오류: ${error.message}`), 2;
    throw error;
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main(process.argv.slice(2)).then(
    (code) => process.exit(code),
    (error) => {
      console.error(error);
      process.exit(1);
    },
  );
}
