import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { z } from "zod";
import { ReconcileFileError, documentSchema, loadUnits, readCallLog, readJsonFile, resolveInputPath, resolveOutputPath } from "../adapters/files.js";
import { extractDocument, type DocModel, type Extraction } from "../domain/facts.js";
import { aimPlaces, aimRows, aimSummary, checkMoved, checkPlantedCells, missingPairs, moveGold, scoreAims, type AimRow, type AimScore, type AimSpec, type Maps, type MovedGold } from "./chart-live.js";
import { toDerivedId, type MapEntry } from "./chart-tables.js";
import { chartGoldSchema } from "./charts.js";
import { pairsSchema, plantsSchema } from "./exam.js";
import { allChanges, checkExam2, checkGold2, gold2Schema, plants2Schema } from "./exam2.js";
import { grade, type Grade } from "./grade.js";
import { pageTexts, type DocContext, type MethodOutcome } from "./methods.js";
import { judgePlacePairs, pairPlacesSchema, type PairPlacesAnswer } from "./prose.js";
import { gradeProse, plantedPlaces } from "./prose-report.js";

// 밴드 CT2 (MARC-TICK-38) 의 명령: 도표를 표로 세운 파생 문서로 방법 D2 를 돌리기 위한 준비(move, aims)와 채점(score). 모델을 부르지 않는다.
// 실제 실행은 밴드 U 의 명령 그대로다: `cli2.ts run2 --method d2` 에 파생 문서·파생 시험지와 move 가 낸 옮긴 정답을 준다.

const USAGE = `쓰임: tsx src/compare/cli11.ts <명령> [..] --out <디렉터리>
  move  --orig-a <원래 a> --orig-b <원래 b> --build <cli10 build 의 출력 묶음: a/ b/ exam-a/ exam-b/> --pairs .. --plants .. --gold2 .. --plants2 ..
        정답 쌍·심은 자리의 ID 를 대응표로 옮긴 사본(pairs.json, plants.json, gold-v2.json, plants-v2.json, chart-pairs.json)과 옮긴 기록(move.json, move.md)
  aims  --orig-a .. --orig-b .. --chart-gold <도표 정답> --gold2 .. --plants2 .. --aims <겨냥 1 의 자리> --sets <이름=build 묶음,..>
        벌마다 겨냥의 자리가 표의 칸으로 옮겨졌는가 (aims.json, aims.md). 고르는 규칙을 적용한 결과도 같이 낸다
  score --space <derived|original> --a <그 실행이 쓴 문서 a> --b .. --exam-a <그 실행이 쓴 시험지 a> --exam-b .. --map-a <대응표 a> --map-b .. --pairs .. --plants .. --gold2 .. --plants2 .. (원래 ID 의 정답)
        --chart-gold .. --aims .. --runs <이름=실행 출력,..> [--moved <move 의 출력: 옮긴 사본이 이것과 같은지 본다>] [--calls <호출 기록>] [--judgments <판정 기록>]
        저장된 원답(units.jsonl)을 다시 확인해 겨냥과 표 칸의 성적을 낸다 (score.json, score.md, to-judge.json)
--out 은 반드시 준다. 상대 경로는 ts/ 기준이다.`;

type Out = (line: string) => void;
type Side = "a" | "b";
const writeJson = (path: string, value: unknown): void => writeFileSync(path, `${JSON.stringify(value, null, 1)}\n`);
const table = (head: string[], rows: string[][]): string[] => [`| ${head.join(" | ")} |`, `|${head.map((_, index) => (index === 0 ? "---" : "---:")).join("|")}|`, ...rows.map((row) => `| ${row.join(" | ")} |`), ""];

const mapSchema = z.object({ map: z.array(z.object({ id: z.string(), from: z.string(), kind: z.enum(["moved", "renumbered"]), chart: z.string().optional(), series: z.string().nullable().optional(), category: z.string().optional() })) });
const builtSchema = z.object({ charts: z.array(z.object({ chart: z.string(), status: z.string(), left: z.array(z.string()), twins: z.array(z.string()) })) });
export const aimsSchema = z.object({
  note: z.string(),
  status: z.string().min(1),
  /** 겨냥 1 의 자리(원래 문서의 ID)와 「옳은 상대」(문서 B 의 ID). 정답 파일의 것이 아니다: 원문으로 따로 판정한 것이다. */
  aim1: z.array(z.object({ name: z.string(), doc: z.enum(["a", "b"]), id: z.string(), raw: z.string(), right: z.array(z.string()), evidence: z.string().min(1) })),
});
export const judgmentsSchema = z.object({
  note: z.string(),
  status: z.string().min(1),
  judgments: z.array(z.object({ a: z.string(), b: z.string(), verdict: z.enum(["same_item", "not_same_item", "not_a_value"]), evidence: z.string().min(1) })),
});

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

/** 밴드 U 의 명령(`cli2.ts`)과 같은 읽기. */
function doc(model: DocModel, docId: Side): Doc {
  const extraction = extractDocument(model, docId, { titles: "own" });
  const blockText = new Map(model.blocks.filter((block) => block.text !== undefined).map((block) => [block.id, block.text as string]));
  return { model, extraction, context: { extraction, pages: pageTexts(extraction, blockText, new Map(model.blocks.map((block) => [block.id, block.page]))) } };
}

const readMap = (path: string): MapEntry[] => readAs(path, mapSchema, "대응표").map as MapEntry[];

/** 파생 문서에서 표로 세운 도표의 표 ID (notes 의 `chart_table:` 로 가른다. 읽는 쪽의 스키마는 notes 를 남기지 않으므로 파일에서 바로 읽는다). */
const chartTableIds = (path: string): Set<string> => new Set(((readJsonFile(path, "문서 JSON") as { tables?: { id: string; notes?: string[] }[] }).tables ?? []).filter((item) => (item.notes ?? []).some((note) => note.startsWith("chart_table:"))).map((item) => item.id));

const KIND_KO = { moved: "새 표의 칸으로 옮김", renumbered: "원래 그릇에 남아 순번만 바뀜", same: "그대로" } as const;

function renderMove(name: string, moved: MovedGold, twins: Set<string>, left: Set<string>): string {
  const lines = [`# 정답의 ID 옮기기: ${name} (호출 0)`, "", "정답 쌍·심은 자리는 원래 문서의 ID 로 적혀 있다. 대응표(`chart-table-map.json`)로 파생 문서의 ID 로 옮겼다. 글자는 그대로다. 정답 파일은 고치지 않았다.", ""];
  const names = { pairs: "밴드 R 의 정답 쌍", plantCells: "밴드 R 의 심은 칸", prosePairs: "서술 쌍 (옮긴 뒤: 서술 쌍 + 도표 쌍)", groups: "문서 안 묶음", groupPlaces: "문서 안 묶음의 자리", plants2: "넓힌 명세의 심은 자리" } as const;
  lines.push(...table(["무엇", "옮기기 전", "옮긴 뒤"], Object.entries(moved.counts).map(([key, [before, after]]) => [names[key as keyof typeof names], String(before), String(after)])));
  const count = (kind: string): number => moved.places.filter((place) => place.kind === kind).length;
  lines.push(`정답에 나오는 자리 ${moved.places.length}개: ${KIND_KO.moved} ${count("moved")}, ${KIND_KO.renumbered} ${count("renumbered")}, ${KIND_KO.same} ${count("same")}. 파생 문서에 없는 ID 0 (있으면 이 명령이 실패한다).`, "");
  lines.push(`옮긴 뒤 양쪽이 다 표의 숫자 칸이 되어 「도표 쌍」으로 따로 실은 서술 쌍: ${moved.chartPairs.length}개 (${moved.chartPairs.map((pair) => pair.id).join(", ") || "없음"}).`, "");
  const changed = moved.places.filter((place) => place.kind !== "same");
  lines.push("## ID 가 바뀐 자리", "", ...table(["문서", "원래 ID", "파생 문서의 ID", "어떻게", "쓰인 곳"], changed.map((place) => [place.doc, place.from, place.to, KIND_KO[place.kind], place.usedIn.join(", ")])));
  const stay = moved.places.filter((place) => place.kind === "same" && (twins.has(`${place.doc}:${place.from}`) || left.has(`${place.doc}:${place.from}`)));
  lines.push("## 도표의 값인데 원래 자리에 남은 정답의 자리", "", stay.length === 0 ? "없다 (겹친 그릇의 둘째, 범주 없는 값 가운데 정답에 쓰인 것 0)." : "", ...(stay.length === 0 ? [] : table(["문서", "ID", "까닭", "쓰인 곳"], stay.map((place) => [place.doc, place.from, twins.has(`${place.doc}:${place.from}`) ? "겹친 그릇의 둘째" : "범주 라벨 없음", place.usedIn.join(", ")]))));
  return `${lines.join("\n")}\n`;
}

const PLACED_KO = { right: "옳은 칸", wrong: "틀린 칸" } as const;

export interface SetAims {
  name: string;
  rows: AimRow[];
  summary: ReturnType<typeof aimSummary>;
}

/** 고르는 규칙 (out/band-CT2/pick-rule.md): 옮겨진 것이 많은 벌 → 틀린 칸이 적은 벌 → 넘겨받은 차례(앞의 것이 먼저). 전체의 틀린 칸으로 가르는 셋째 규칙은 `tieBreak` 로 받는다. */
export function pickSet(sets: SetAims[], tieBreak: Record<string, number> = {}): { picked: string; order: string[]; enough: boolean } {
  const order = sets.map((set, index) => ({ set, index })).sort((x, y) => y.set.summary.moved - x.set.summary.moved || x.set.summary.wrong - y.set.summary.wrong || (tieBreak[x.set.name] ?? 0) - (tieBreak[y.set.name] ?? 0) || x.index - y.index);
  const best = order[0]!.set;
  return { picked: best.name, order: order.map((item) => item.set.name), enough: best.summary.moved * 2 >= best.summary.total };
}

function renderAims(sets: SetAims[], pick: ReturnType<typeof pickSet>): string {
  const lines = ["# 벌마다: 겨냥의 자리가 표의 칸으로 옮겨졌는가 (호출 0)", "", "분모는 발주서가 적은 세 목록을 이은 것이다(겨냥 3 의 자리는 겨냥 2 와 겹친다). 「옮겨짐」 = 그 벌의 대응표에 moved 로 실림. 「틀린 칸」 = 새 칸의 머리글이 도표 정답과 다름.", ""];
  lines.push(...table(["벌", "옮겨진 것 / 겨냥 전체", "겨냥 1 (47쪽 6칸)", "겨냥 2 (도표가 낀 서술 쌍)", "겨냥 3 (도표에 심은 오류)", "겹침을 뺀 자리로: 옮겨진 것 / 전체", "틀린 칸에 놓인 것"], sets.map((set) => [set.name, `${set.summary.moved}/${set.summary.total}`, set.summary.byAim["1"]!, set.summary.byAim["2"]!, set.summary.byAim["3"]!, `${set.summary.movedDistinct}/${set.summary.distinct}`, String(set.summary.wrong)])));
  lines.push(`규칙을 적용한 차례: ${pick.order.join(" > ")}. **고른 벌: ${pick.picked}** (옮겨진 것이 절반 이상인가: ${pick.enough ? "예" : "아니오 → 호출하지 않는다"}).`, "");
  for (const set of sets) lines.push(`## ${set.name}`, "", ...table(["겨냥", "이름", "원래 ID", "어떻게", "파생 문서의 ID", "새 칸의 머리글 (계열 · 범주)", "도표 정답과"], set.rows.map((row) => [String(row.aim), row.name, `${row.doc}:${row.id}`, KIND_KO[row.kind], row.to, row.headers ? `${row.headers.series || "(이름 없음)"} · ${row.headers.category}` : "-", row.placed ? PLACED_KO[row.placed] : "-"])));
  return `${lines.join("\n")}\n`;
}

export interface RunScore {
  name: string;
  aims: AimScore;
  table: { cleanMatch: string; cleanMismatch: string; control: string; caught: string; falseMatch: number; silent: number; missing: string; missingIds: string[]; otherMismatches: Grade["mismatches"]["other"]; cellFalseAlarms: Grade["mismatches"]["other"] };
  prose: { clean: string; errors: string; outsideMismatch: number };
  verdicts: Grade["verdicts"];
  rejected: Record<string, number>;
  answers: number;
  cost: { calls: number; inputTokens: number; cacheReadTokens: number; cacheWriteTokens: number; outputTokens: number; thinkingTokens: number; costUsd: number; models: string[]; firstTs: string | null; lastTs: string | null } | null;
}

const OUTCOME_KO = { match: "일치", mismatch: "불일치", needs_explanation: "설명 필요" } as const;

export function renderScore(space: string, runs: RunScore[], judgments: z.infer<typeof judgmentsSchema> | null): string {
  const head = ["항목", ...runs.map((run) => run.name)];
  const row = (label: string, pick: (run: RunScore) => string): string[] => [label, ...runs.map(pick)];
  const judged = new Map((judgments?.judgments ?? []).map((item) => [`${item.a}|${item.b}`, item.verdict]));
  const lines = [`# 겨냥과 표 칸의 성적 (${space === "derived" ? "파생 문서의 실행" : "원래 문서의 실행"}, 저장된 원답을 다시 확인한 것, 호출 0)`, "", "「짝으로 나옴」은 확인을 통과해 받아들인 짝만 센다. 원답에는 있지만 버려진 답은 따로 센다.", ""];
  lines.push("## 겨냥", "", ...table(head, [
    row("겨냥 1: 47쪽의 6칸 가운데 짝으로 나온 것", (run) => `${run.aims.aim1.paired}/${run.aims.aim1.total}`),
    row("　그중 옳은 상대와 (같은 연도의 합계 행·CAT 15 행)", (run) => `${run.aims.aim1.right}/${run.aims.aim1.total}`),
    row("　그중 다른 상대와만", (run) => `${run.aims.aim1.other}/${run.aims.aim1.total}`),
    row("겨냥 2: 도표가 낀 정답 쌍 가운데 짝으로 나온 것", (run) => `${run.aims.aim2.paired}/${run.aims.aim2.total}`),
    row("　일치 / 불일치", (run) => `${run.aims.aim2.match} / ${run.aims.aim2.mismatch}`),
    row("겨냥 3: 도표에 심은 오류 가운데 불일치로 잡은 것", (run) => `${run.aims.aim3.caught}/${run.aims.aim3.total}`),
    row("　일치로 나옴(거짓 일치) / 조용히 지나감", (run) => `${run.aims.aim3.falseMatch} / ${run.aims.aim3.silent}`),
    row("표로 세운 도표의 값 자리 가운데 받아들인 짝에 나온 것", (run) => `${run.aims.chartCells.accepted}/${run.aims.chartCells.total}`),
    row("　버려진 답에만 나온 것", (run) => `${run.aims.chartCells.rejectedOnly}/${run.aims.chartCells.total}`),
    row("표로 세우지 못해 원래 자리에 남은 도표의 값 가운데 받아들인 짝에 나온 것", (run) => `${run.aims.leftBehind.accepted}/${run.aims.leftBehind.total}`),
    row("　버려진 답에만 나온 것", (run) => `${run.aims.leftBehind.rejectedOnly}/${run.aims.leftBehind.total}`),
    row("표로 세운 도표의 값 자리가 낀 정답 밖의 짝", (run) => String(run.aims.outsideGold.length)),
  ]));
  lines.push("## 표 칸의 원래 성적 (밴드 R 의 정답 쌍과 심은 칸)", "", ...table(head, [
    row("참 일치", (run) => run.table.cleanMatch),
    row("　그 쌍이 불일치로 (거짓 경보)", (run) => run.table.cleanMismatch),
    row("　대조군이 일치로", (run) => run.table.control),
    row("잡은 오류", (run) => run.table.caught),
    row("　거짓 일치 / 조용히 지나감", (run) => `${run.table.falseMatch} / ${run.table.silent}`),
    row("빠진 정답 쌍 (그 두 칸이 짝으로 안 나옴)", (run) => run.table.missing),
    row("밴드 R 의 심은 오류도 넓힌 정답의 쌍도 아닌 불일치 (전부)", (run) => String(run.table.otherMismatches.length)),
    row("　그중 양쪽이 다 표의 숫자 칸 (표 칸 거짓 경보 후보)", (run) => String(run.table.cellFalseAlarms.length)),
    row("견준 짝: 일치 / 불일치 / 설명 필요", (run) => `${run.verdicts.match} / ${run.verdicts.mismatch} / ${run.verdicts.needsExplanation}`),
    row("원답의 짝 수", (run) => String(run.answers)),
    row("버려진 답", (run) => Object.entries(run.rejected).map(([reason, n]) => `${reason} ${n}`).join(", ") || "0"),
    row("손대지 않은 서술 쌍 일치", (run) => run.prose.clean),
    row("서술 쌍의 오류 잡음", (run) => run.prose.errors),
  ]));
  if (runs.some((run) => run.cost)) {
    lines.push("## 비용 (호출 기록에서, 설정 단가 x 토큰)", "", ...table(head, [
      row("호출", (run) => String(run.cost?.calls ?? "-")),
      row("입력 토큰 (캐시 포함)", (run) => String(run.cost?.inputTokens ?? "-")),
      row("　그중 캐시 읽기 / 캐시 쓰기", (run) => (run.cost ? `${run.cost.cacheReadTokens} / ${run.cost.cacheWriteTokens}` : "-")),
      row("출력 토큰 (그중 추론)", (run) => (run.cost ? `${run.cost.outputTokens} (${run.cost.thinkingTokens})` : "-")),
      row("비용 USD", (run) => run.cost?.costUsd.toFixed(6) ?? "-"),
      row("모델", (run) => run.cost?.models.join(", ") ?? "-"),
      row("첫 호출 ~ 마지막 호출 (기록 시각)", (run) => (run.cost ? `${run.cost.firstTs} ~ ${run.cost.lastTs}` : "-")),
    ]));
  }
  for (const run of runs) {
    lines.push(`## ${run.name}: 겨냥의 줄`, "");
    lines.push(...table(["겨냥 1 의 자리", "ID", "받아들인 짝의 상대", "버려진 답에만"], run.aims.aim1.rows.map((item) => [item.name, item.a, item.partners.map((partner) => `${partner.b} (${OUTCOME_KO[partner.outcome]}, ${partner.right ? "옳은 상대" : "다른 상대"})`).join("; ") || "없음", item.inRejected ? "예" : "-"])));
    lines.push(...table(["겨냥 2 의 쌍", "a", "b", "결과", "a 의 다른 상대", "버려진 답에만"], run.aims.aim2.rows.map((item) => [item.pair, item.a, item.b, item.outcome ? OUTCOME_KO[item.outcome] : "짝 없음", item.otherPartners.join(", ") || "-", item.inRejected ? "예" : "-"])));
    lines.push(...table(["겨냥 3 의 심은 것", "쌍", "결과"], run.aims.aim3.rows.map((item) => [item.plant, item.pair, item.bucket === "caught" ? "불일치로 잡음" : item.bucket === "false_match" ? "일치로 나옴 (거짓 일치)" : "조용히 지나감 (짝 없음)"])));
    if (run.aims.chartCells.acceptedIds.length + run.aims.chartCells.rejectedOnlyIds.length > 0) lines.push(`표로 세운 도표의 값 자리: 받아들인 짝에 ${run.aims.chartCells.acceptedIds.join(", ") || "없음"} / 버려진 답에만 ${run.aims.chartCells.rejectedOnlyIds.join(", ") || "없음"}`, "");
    if (run.aims.leftBehind.acceptedIds.length + run.aims.leftBehind.rejectedOnlyIds.length > 0) lines.push(`원래 자리에 남은 도표의 값: 받아들인 짝에 ${run.aims.leftBehind.acceptedIds.join(", ") || "없음"} / 버려진 답에만 ${run.aims.leftBehind.rejectedOnlyIds.join(", ") || "없음"}`, "");
    if (run.aims.outsideGold.length > 0) lines.push(...table(["표로 세운 도표의 값 자리가 낀 정답 밖의 짝: a", "b", "결과", "판정"], run.aims.outsideGold.map((verdict) => [verdict.a, verdict.b, OUTCOME_KO[verdict.outcome], judged.get(`${verdict.a}|${verdict.b}`) ?? "판정 전"])));
    if (run.table.missingIds.length > 0) lines.push(`빠진 정답 쌍: ${run.table.missingIds.join(", ")}`, "");
    if (run.table.otherMismatches.length > 0) lines.push(...table(["심은 오류도 넓힌 정답의 쌍도 아닌 불일치: a", "b", "갈래", "양쪽이 표의 숫자 칸", "판정"], run.table.otherMismatches.map((item) => [item.a, item.b, item.kind, run.table.cellFalseAlarms.includes(item) ? "예" : "-", judged.get(`${item.a}|${item.b}`) ?? "-"])));
  }
  return `${lines.join("\n")}\n`;
}

export async function main(argv: string[], out: Out = console.log): Promise<number> {
  const [command, ...rest] = argv;
  try {
    const { values } = parseArgs({ args: rest, options: { "orig-a": { type: "string" }, "orig-b": { type: "string" }, build: { type: "string" }, pairs: { type: "string" }, plants: { type: "string" }, gold2: { type: "string" }, plants2: { type: "string" }, "chart-gold": { type: "string" }, aims: { type: "string" }, sets: { type: "string" }, "tie-break": { type: "string" }, space: { type: "string" }, a: { type: "string" }, b: { type: "string" }, "exam-a": { type: "string" }, "exam-b": { type: "string" }, "map-a": { type: "string" }, "map-b": { type: "string" }, runs: { type: "string" }, moved: { type: "string" }, calls: { type: "string" }, judgments: { type: "string" }, name: { type: "string" }, out: { type: "string" } } });
    if (!values.out || !["move", "aims", "score"].includes(command ?? "")) return out(USAGE), 2;
    const outDir = resolveOutputPath(values.out);
    const need = (key: keyof typeof values): string => {
      const value = values[key];
      if (typeof value !== "string") throw new ReconcileFileError(`--${key} 가 필요하다`);
      return resolveInputPath(value);
    };
    const wanted = (key: "sets" | "runs"): string => {
      const value = values[key];
      if (typeof value !== "string" || value === "") throw new ReconcileFileError(`--${key} 가 필요하다`);
      return value;
    };
    const list = (text: string): { name: string; dir: string }[] => text.split(",").filter((item) => item !== "").map((item) => ({ name: item.split("=")[0] as string, dir: resolveInputPath(item.split("=")[1] as string) }));
    const readGold = () => ({ pairs: readAs(need("pairs"), pairsSchema, "정답 쌍"), plants: readAs(need("plants"), plantsSchema, "심은 오류 명세"), gold: readAs(need("gold2"), gold2Schema, "넓힌 정답"), plants2: readAs(need("plants2"), plants2Schema, "넓힌 명세") });
    const builtSide = (dir: string, side: string) => ({ model: readDoc(join(dir, side, "document.json")), map: readMap(join(dir, side, "chart-table-map.json")), built: readAs(join(dir, side, "chart-tables.json"), builtSchema, "도표별 결과").charts });
    mkdirSync(outDir, { recursive: true });

    if (command === "move") {
      const original = { a: doc(readDoc(need("orig-a")), "a"), b: doc(readDoc(need("orig-b")), "b") };
      const dir = need("build");
      const built = { a: builtSide(dir, "a"), b: builtSide(dir, "b") };
      const exam = { a: readDoc(join(dir, "exam-a", "document.json")), b: readDoc(join(dir, "exam-b", "document.json")) };
      const derived = { a: doc(built.a.model, "a"), b: doc(built.b.model, "b") };
      const derivedExam = { a: doc(exam.a, "a"), b: doc(exam.b, "b") };
      const source = readGold();
      const before = [...checkGold2(source.gold, original.a.extraction, original.b.extraction)];
      if (before.length > 0) throw new ReconcileFileError(`정답이 원래 문서와 맞지 않는다\n${before.join("\n")}`);
      const moved = moveGold(source, { a: built.a.map, b: built.b.map });
      const problems = [
        ...checkMoved(moved, { a: original.a.extraction, b: original.b.extraction }, { a: derived.a.extraction, b: derived.b.extraction }),
        ...checkGold2(moved.gold, derived.a.extraction, derived.b.extraction),
        ...checkPlantedCells(moved, { a: derived.a.extraction, b: derived.b.extraction }, { a: derivedExam.a.extraction, b: derivedExam.b.extraction }),
      ];
      // 파생 시험지(시험지 사본에 같은 변환을 한 것)가 「파생 문서에 옮긴 명세만 적용한 것」과 같은가.
      const changes = allChanges(moved.plants, moved.plants2);
      for (const side of ["a", "b"] as const) problems.push(...checkExam2(built[side].model, exam[side], derived[side].extraction, changes.filter((change) => change.doc === side)).map((problem) => `파생 시험지 ${side}: ${problem}`));
      if (problems.length > 0) throw new ReconcileFileError(`옮긴 정답이 파생 문서와 맞지 않는다\n${problems.join("\n")}`);
      const tag = `[옮긴 사본: 밴드 CT2 (MARC-TICK-38) 가 대응표 ${values.name ?? dir} 로 자리 ID 만 파생 문서의 ID 로 옮겼다. 글자와 그 밖의 내용은 원래 파일 그대로다. 원래 파일을 고친 것이 아니다.] `;
      writeJson(join(outDir, "pairs.json"), { ...moved.pairs, note: tag + moved.pairs.note });
      writeJson(join(outDir, "plants.json"), { ...moved.plants, note: tag + moved.plants.note });
      writeJson(join(outDir, "gold-v2.json"), { ...moved.gold, note: tag + moved.gold.note });
      writeJson(join(outDir, "plants-v2.json"), { ...moved.plants2, note: tag + moved.plants2.note });
      writeJson(join(outDir, "chart-pairs.json"), { note: `${tag}넓힌 정답의 서술 쌍 가운데, 옮긴 뒤 양쪽이 다 표의 숫자 칸이 된 것. 「서술이 낀 쌍」 꼴에 더는 맞지 않아 여기에 따로 싣는다.`, status: source.gold.status, pairs: moved.chartPairs });
      const mark = (pick: (chart: (typeof built.a.built)[number]) => string[]): Set<string> => new Set((["a", "b"] as const).flatMap((side) => built[side].built.flatMap((chart) => pick(chart).map((id) => `${side}:${id}`))));
      writeJson(join(outDir, "move.json"), { counts: moved.counts, places: moved.places, chartPairs: moved.chartPairs.map((pair) => pair.id) });
      writeFileSync(join(outDir, "move.md"), renderMove(values.name ?? dir, moved, mark((chart) => chart.twins), mark((chart) => chart.left)));
      out(JSON.stringify({ out: outDir, counts: moved.counts, moved: moved.places.filter((place) => place.kind === "moved").length, renumbered: moved.places.filter((place) => place.kind === "renumbered").length, same: moved.places.filter((place) => place.kind === "same").length, chartPairs: moved.chartPairs.map((pair) => pair.id), checked: "옮긴 ID 가 전부 파생 문서의 값 후보이고 글자가 같다. 파생 시험지 = 파생 문서 + 옮긴 명세" }));
      return 0;
    }

    const chartGold = readAs(need("chart-gold"), chartGoldSchema, "도표 정답");
    const aims = readAs(need("aims"), aimsSchema, "겨냥");
    const gold = readAs(need("gold2"), gold2Schema, "넓힌 정답");
    const plants2 = readAs(need("plants2"), plants2Schema, "넓힌 명세");
    const places = aimPlaces(aims.aim1.map((item) => ({ name: item.name, doc: item.doc, id: item.id })), gold, plants2, chartGold);

    if (command === "aims") {
      const sets: SetAims[] = list(wanted("sets")).map(({ name, dir }) => {
        const built = { a: builtSide(dir, "a"), b: builtSide(dir, "b") };
        const rows = aimRows(places, { a: built.a.map, b: built.b.map }, { a: doc(built.a.model, "a"), b: doc(built.b.model, "b") }, chartGold);
        return { name, rows, summary: aimSummary(rows) };
      });
      const tieBreak = Object.fromEntries((values["tie-break"] ?? "").split(",").filter((item) => item !== "").map((item) => [item.split("=")[0] as string, Number(item.split("=")[1])]));
      const pick = pickSet(sets, tieBreak);
      writeJson(join(outDir, "aims.json"), { pick, tieBreak, sets });
      writeFileSync(join(outDir, "aims.md"), renderAims(sets, pick));
      out(JSON.stringify({ out: join(outDir, "aims.md"), pick, sets: Object.fromEntries(sets.map((set) => [set.name, set.summary])) }));
      return pick.enough ? 0 : 3;
    }

    // ── score ────────────────────────────────────────────────────────────────
    const space = values.space;
    if (space !== "derived" && space !== "original") return out(USAGE), 2;
    const source = { pairs: readAs(need("pairs"), pairsSchema, "정답 쌍"), plants: readAs(need("plants"), plantsSchema, "심은 오류 명세"), gold, plants2 };
    const maps: Maps = { a: readMap(need("map-a")), b: readMap(need("map-b")) };
    const used = { a: doc(readDoc(need("a")), "a"), b: doc(readDoc(need("b")), "b") };
    const exam = { a: doc(readDoc(need("exam-a")), "a"), b: doc(readDoc(need("exam-b")), "b") };
    const identity: Maps = { a: [], b: [] };
    // 정답은 그 실행이 쓴 문서의 ID 로 옮겨서 쓴다. 옮기지 않은 정답으로 파생 문서의 실행을 채점하면 아래의 확인에서 걸린다.
    const moved = moveGold(source, space === "derived" ? maps : identity);
    const goldProblems = [...checkGold2(moved.gold, used.a.extraction, used.b.extraction), ...moved.chartPairs.flatMap((pair) => (used.a.extraction.facts.some((fact) => fact.id === pair.a && fact.raw === pair.rawA) && used.b.extraction.facts.some((fact) => fact.id === pair.b && fact.raw === pair.rawB) ? [] : [`${pair.id}: 도표 쌍의 자리가 문서에 없다`]))];
    for (const pair of moved.pairs.pairs) if (!used.a.extraction.facts.some((fact) => fact.id === pair.a) || !used.b.extraction.facts.some((fact) => fact.id === pair.b)) goldProblems.push(`${pair.id}: 정답 쌍의 칸이 문서에 없다`);
    if (goldProblems.length > 0) throw new ReconcileFileError(`정답이 이 실행의 문서와 맞지 않는다 (ID 를 옮기지 않았는가)\n${goldProblems.join("\n")}`);
    if (values.moved) {
      const dir = need("moved");
      const same = (file: string, value: { note: string }, schema: z.ZodType<{ note: string }>): boolean => JSON.stringify({ ...readAs(join(dir, file), schema, file), note: "" }) === JSON.stringify({ ...schema.parse(value), note: "" });
      const diff = [same("pairs.json", moved.pairs, pairsSchema) ? null : "pairs.json", same("plants.json", moved.plants, plantsSchema) ? null : "plants.json", same("gold-v2.json", moved.gold, gold2Schema) ? null : "gold-v2.json", same("plants-v2.json", moved.plants2, plants2Schema) ? null : "plants-v2.json"].filter((item) => item !== null);
      if (diff.length > 0) throw new ReconcileFileError(`옮긴 사본이 정답과 대응표에서 다시 만든 것과 다르다: ${diff.join(", ")}`);
    }
    const here = (doc: Side, id: string): string => (space === "derived" ? toDerivedId(maps[doc], id) : id);
    const allPairs = [...moved.gold.prosePairs, ...moved.chartPairs];
    const pairOf = new Map(allPairs.map((pair) => [pair.id, pair]));
    const movedFrom = { a: new Set(maps.a.filter((entry) => entry.kind === "moved").map((entry) => entry.from)), b: new Set(maps.b.filter((entry) => entry.kind === "moved").map((entry) => entry.from)) };
    const spec: AimSpec = {
      aim1: aims.aim1.map((item) => ({ name: item.name, a: here(item.doc, item.id), right: item.right.map((id) => here("b", id)) })),
      aim2: places.filter((place) => place.aim === 2).map((place) => pairOf.get(place.name)!).map((pair) => ({ pair: pair.id, a: pair.a, b: pair.b })),
      aim3: places.filter((place) => place.aim === 3).map((place) => {
        const plant = plants2.plants.find((item) => item.id === place.name)!;
        const pair = pairOf.get(plant.pairs[0] as string)!;
        return { plant: plant.id, pair: pair.id, a: pair.a, b: pair.b };
      }),
      chartCells: { a: [...movedFrom.a].map((id) => here("a", id)), b: [...movedFrom.b].map((id) => here("b", id)) },
      leftBehind: { a: chartGold.labels.filter((label) => label.doc === "a" && label.role === "value" && !movedFrom.a.has(label.id)).map((label) => here("a", label.id)), b: chartGold.labels.filter((label) => label.doc === "b" && label.role === "value" && !movedFrom.b.has(label.id)).map((label) => here("b", label.id)) },
    };
    if (space === "derived") {
      // 표로 세운 도표의 값 자리는 전부 `chart_table:` 표의 칸이어야 한다.
      const tables = { a: chartTableIds(need("a")), b: chartTableIds(need("b")) };
      for (const side of ["a", "b"] as const) for (const id of spec.chartCells[side]) if (!tables[side].has(id.split(":")[0] as string)) throw new ReconcileFileError(`${side}:${id}: 표로 세운 도표의 칸이 아니다 (대응표가 이 문서의 것이 아닌가)`);
    }
    const goldKeys = new Set([...moved.pairs.pairs, ...allPairs].map((pair) => `${pair.a}|${pair.b}`));
    const units = loadUnits();
    const planted = plantedPlaces(moved.plants.plants, moved.plants2.plants);
    const isCell = (side: Side, id: string): boolean => exam[side].extraction.facts.find((fact) => fact.id === id)?.bucket === "table_cell";
    const calls = values.calls ? readCallLog(need("calls")) : null;
    const judgments = values.judgments ? readAs(need("judgments"), judgmentsSchema, "판정 기록") : null;
    const runs: RunScore[] = list(wanted("runs")).map(({ name, dir }) => {
      const saved = readJsonFile(join(dir, "outcome.json"), "실행 결과") as { synthetic?: string; method?: string; outcome: MethodOutcome };
      if (saved.synthetic) throw new ReconcileFileError(`${dir}: 모의 실행의 결과다. 실측과 섞지 않는다`);
      const lines = readFileSync(join(dir, "units.jsonl"), "utf8").split("\n").filter((line) => line.trim() !== "").map((line) => JSON.parse(line) as { unitId: string; callId: string; result: { kind: string; data?: unknown } });
      const answers: PairPlacesAnswer[] = lines.filter((entry) => entry.result.kind === "success").sort((x, y) => x.unitId.localeCompare(y.unitId, "en", { numeric: true })).map((entry) => pairPlacesSchema.parse(entry.result.data));
      // 저장된 원답을 밴드 U 의 확인(`judgePlacePairs`)에 다시 넣는다. 그 실행의 outcome.json 과 같아야 한다.
      const judged = judgePlacePairs(answers, exam.a.context, exam.b.context, units);
      const key = (verdict: { a: string; b: string; outcome?: string }): string => `${verdict.a}|${verdict.b}|${verdict.outcome}`;
      if (judged.verdicts.map(key).sort().join("\n") !== saved.outcome.verdicts.map(key).sort().join("\n")) throw new ReconcileFileError(`${dir}: 저장된 원답을 다시 확인한 결과가 그 실행의 outcome.json 과 다르다 (다른 문서로 돌린 실행인가)`);
      const outcome: MethodOutcome = { verdicts: judged.verdicts, intra: [], states: saved.outcome.states, rejected: judged.rejected };
      const graded = grade(moved.pairs.pairs, moved.plants.plants, outcome);
      const prose = gradeProse(moved.gold, planted, outcome.verdicts, (side, id) => !isCell(side, id));
      const missing = missingPairs(moved.pairs.pairs, outcome.verdicts);
      // 서술 쌍·도표 쌍(넓힌 정답)의 불일치는 위의 서술·겨냥의 셈에서 따로 센다. 여기에는 어느 정답 쌍도 아닌 불일치와, 손대지 않은 정답 쌍의 불일치만 남긴다.
      const widened = new Set(allPairs.map((pair) => `${pair.a}|${pair.b}`));
      const others = graded.mismatches.other.filter((item) => !widened.has(`${item.a}|${item.b}`));
      const mine = calls?.filter((record) => lines.some((entry) => entry.callId === record.callId)) ?? null;
      const sum = (pick: (record: NonNullable<typeof mine>[number]) => number | null | undefined): number => (mine ?? []).reduce((total, record) => total + (pick(record) ?? 0), 0);
      const stamps = (mine ?? []).map((record) => (record as { ts?: string }).ts ?? "").filter((ts) => ts !== "").sort();
      return {
        name,
        aims: scoreAims(spec, outcome, answers, goldKeys),
        table: { cleanMatch: `${graded.clean.match}/${graded.clean.total}`, cleanMismatch: `${graded.clean.mismatch}/${graded.clean.total}`, control: `${graded.control.match}/${graded.control.total}`, caught: `${graded.errors.caught}/${graded.errors.total}`, falseMatch: graded.errors.falseMatch, silent: graded.errors.silent, missing: `${missing.length}/${moved.pairs.pairs.length}`, missingIds: missing, otherMismatches: others, cellFalseAlarms: others.filter((item) => isCell("a", item.a) && isCell("b", item.b)) },
        prose: { clean: `${prose.clean.match}/${prose.clean.total}`, errors: `${prose.errors.caught}/${prose.errors.total}`, outsideMismatch: prose.outsideGold.mismatch.length },
        verdicts: graded.verdicts,
        rejected: judged.rejected,
        answers: answers.reduce((total, answer) => total + answer.pairs.length, 0),
        cost: mine ? { calls: mine.length, inputTokens: sum((record) => (record.inputTokens ?? 0) + (record.cacheReadTokens ?? 0) + (record.cacheWriteTokens ?? 0)), cacheReadTokens: sum((record) => record.cacheReadTokens), cacheWriteTokens: sum((record) => record.cacheWriteTokens), outputTokens: sum((record) => record.outputTokens), thinkingTokens: sum((record) => (record as { thinkingTokens?: number }).thinkingTokens), costUsd: Math.round(sum((record) => record.costUsd) * 1e8) / 1e8, models: [...new Set(mine.map((record) => `${(record as { provider?: string }).provider}/${(record as { responseModelId?: string }).responseModelId}`))], firstTs: stamps[0] ?? null, lastTs: stamps[stamps.length - 1] ?? null } : null,
      };
    });
    const toJudge = new Map<string, { a: string; b: string; outcome: string; why: string; runs: string[] }>();
    for (const run of runs) {
      for (const verdict of run.aims.outsideGold) (toJudge.get(`${verdict.a}|${verdict.b}`) ?? toJudge.set(`${verdict.a}|${verdict.b}`, { a: verdict.a, b: verdict.b, outcome: verdict.outcome, why: "표로 세운 도표의 값 자리가 낀 정답 밖의 짝", runs: [] }).get(`${verdict.a}|${verdict.b}`)!).runs.push(run.name);
      for (const row of run.aims.aim1.rows) for (const partner of row.partners) (toJudge.get(`${row.a}|${partner.b}`) ?? toJudge.set(`${row.a}|${partner.b}`, { a: row.a, b: partner.b, outcome: partner.outcome, why: "겨냥 1 의 자리의 짝", runs: [] }).get(`${row.a}|${partner.b}`)!).runs.push(run.name);
    }
    writeJson(join(outDir, "score.json"), { space, spec, runs });
    writeJson(join(outDir, "to-judge.json"), [...toJudge.values()].map((item) => ({ ...item, runs: [...new Set(item.runs)] })));
    const md = renderScore(space, runs, judgments);
    writeFileSync(join(outDir, "score.md"), md);
    out(md);
    return 0;
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
