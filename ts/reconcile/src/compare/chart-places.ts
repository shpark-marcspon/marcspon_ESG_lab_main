import { z } from "zod";
import type { ChartTableInput } from "./chart-tables.js";
import type { ChartGold, Tag } from "./charts.js";

// 밴드 VT1 (MARC-TICK-42): 도표가 든 쪽의 그림과 글을 같이(또는 글만) 주고, 번호표마다 「어느 도표의 어느 계열·어느 범주인가」를 받는다.
// 밴드 W 의 그림 라벨(`vision.ts`)과 다른 점: 쪽의 글을 좌표와 함께 싣는다, 답에 도표 묶음(`chart`)이 있다,
// 범주의 글자가 번호표에 덮였으면 그 번호(`category_tag`)로 답하고 글자는 코드가 채운다.
// 값은 받지 않는다: 답 스키마에 숫자 필드가 없고, 요청 글에는 숫자가 번호표(`〔n〕`)나 `▒` 로만 실린다 (연도 꼴은 이름이라 그대로).

export const PROMPT_IMAGE_TEXT = "reconcile/label-chart-places@1";
export const PROMPT_TEXT_ONLY = "reconcile/label-chart-places-text@1";
export const ARMS = { "image-text": { prompt: PROMPT_IMAGE_TEXT, image: true }, text: { prompt: PROMPT_TEXT_ONLY, image: false } } as const;
export type Arm = keyof typeof ARMS;

// ── 요청 글 ─────────────────────────────────────────────────────────────────

export const lineSchema = z.object({ x0: z.number(), x1: z.number(), y: z.number(), text: z.string() });
export type Line = z.infer<typeof lineSchema>;
export const pageLinesSchema = z.record(z.string(), z.object({ lines: z.array(lineSchema), visible_numbers: z.object({ year: z.number(), other: z.number() }), tags: z.number() }));

const TAG_MARK = /〔(\d+)〕/g;
/** 연도 꼴: 범주의 이름으로 쓰이는 숫자라 가리지 않는다 (`py/docparse/layout.py` 의 `_YEAR_RE` 와 같은 꼴). */
const YEAR = /^(FY)?'?(19|20)\d{2}(년|년도)?$/;

/** 쪽의 글: 줄 토막마다 `[왼쪽-오른쪽, 세로] 글`. 좌표는 쪽의 왼쪽 위가 (0, 0), 오른쪽 아래가 (1000, 1000). */
export const renderLines = (lines: Line[]): string => lines.map((line) => `[${line.x0}-${line.x1}, ${line.y}] ${line.text}`).join("\n");

/**
 * 줄 토막의 글에 가리지 못한 숫자가 있는가. 번호표 표시(`〔n〕`)를 뺀 뒤, 숫자 글자가 든 낱말은 연도 꼴이어야 한다.
 * 돌려주는 것은 걸린 낱말이다 (비어 있어야 요청을 만든다). 좌표는 줄의 글과 따로 받으므로 여기서 보지 않는다.
 */
export function unmaskedInLines(lines: Line[]): string[] {
  const left: string[] = [];
  for (const line of lines) for (const word of line.text.replace(TAG_MARK, " ").split(/\s+/)) if (/\p{Nd}/u.test(word) && !YEAR.test(word.normalize("NFKC"))) left.push(word);
  return left;
}

/** 글에 실린 번호표의 번호 (요청의 번호 목록과 맞는지 볼 때 쓴다). */
export const tagsInLines = (lines: Line[]): number[] => lines.flatMap((line) => [...line.text.matchAll(TAG_MARK)].map((match) => Number(match[1])));

// ── 답 ──────────────────────────────────────────────────────────────────────

export const ROLES = ["value", "tick", "category", "other"] as const;
export type Role = (typeof ROLES)[number];

export const placeLabelsSchema = z.object({
  tags: z.array(z.object({ tag: z.string(), role: z.enum(ROLES), chart: z.string().nullable(), title: z.string().nullable(), series: z.string().nullable(), category: z.string().nullable(), category_tag: z.string().nullable(), unit: z.string().nullable() })),
});
export type PlaceLabelsAnswer = z.infer<typeof placeLabelsSchema>;

/** 번호표 하나에 대한 받아들인 답. `chart` 는 그 쪽 안에서만 뜻이 있는 묶음 이름이다. */
export interface PlaceAnswer {
  role: Role;
  chart: string | null;
  title: string | null;
  series: string | null;
  category: string | null;
  /** 범주가 어디서 왔는가: 모델이 글자로 적었다(`text`) / 모델이 가리킨 번호표의 원문을 코드가 채웠다(`tag`). */
  categoryFrom: "text" | "tag" | null;
  unit: string | null;
}

export interface PlaceJudgement {
  /** 번호표의 ID(값 후보 ID 또는 숫자 낱말의 열쇠) → 받아들인 답. 번호 하나를 같이 쓰는 ID 는 같은 답을 받는다. */
  answers: Record<string, PlaceAnswer>;
  rejected: Record<string, number>;
  unanswered: number[];
  /** 범주를 번호표로 가리킨 답 가운데, 가리킨 번호가 그 쪽에 없어 범주를 비운 수. */
  badCategoryTag: number;
}

const NUMBER = /\d[\d,]*(?:\.\d+)?/g;
const core = (text: string): string[] => [...text.matchAll(NUMBER)].map((match) => match[0].replace(/,/g, "").replace(/\.$/, ""));
const digits = (token: string): number => token.replace(/\D/g, "").length;
const clean = (text: string | null): string | null => (text === null || text.trim() === "" ? null : text.trim());
/** 버리는 기준의 자릿수 (밴드 W 와 같다): 다른 번호표가 덮은 숫자는 숫자 글자가 이 수 이상일 때만 견준다. */
export const LEAK_MIN_DIGITS = 3;

/**
 * 한 쪽의 답을 확인한다. 버리는 것: 요청에 없는 번호(`unknown_tag`), 같은 번호의 둘째 답(`duplicate_tag`),
 * 모델이 적은 글에 값의 글자가 든 것(`value_text_in_answer`. 기준은 밴드 W 의 `judgeTagAnswers` 와 같다).
 * 범주를 번호표로 가리켰으면(`category_tag`) 그 번호표의 원문을 범주로 채운다. 그 글자는 코드가 문서에서 옮긴 것이라 새는 것이 아니다.
 * 자기 자신을 가리키거나 그 쪽에 없는 번호를 가리키면 범주를 비운다. `rawOf` 는 ID → 원문 글자.
 */
export function judgePlaceAnswers(answer: PlaceLabelsAnswer | null, pageTags: Tag[], rawOf: (id: string) => string): PlaceJudgement {
  const out: PlaceJudgement = { answers: {}, rejected: {}, unanswered: [], badCategoryTag: 0 };
  const bump = (name: string): void => void (out.rejected[name] = (out.rejected[name] ?? 0) + 1);
  const byNumber = new Map(pageTags.map((tag) => [String(tag.n), tag]));
  const covered = new Map(pageTags.map((tag) => [tag.n, new Set((tag.ids ?? [tag.id]).flatMap((id) => core(rawOf(id))))]));
  const longCovered = new Set([...covered.values()].flatMap((set) => [...set]).filter((token) => digits(token) >= LEAK_MIN_DIGITS));
  const done = new Set<number>();
  for (const item of answer?.tags ?? []) {
    const tag = byNumber.get(item.tag.trim());
    if (!tag) {
      bump("unknown_tag");
      continue;
    }
    if (done.has(tag.n)) {
      bump("duplicate_tag");
      continue;
    }
    const own = covered.get(tag.n) as Set<string>;
    const written = [item.chart, item.title, item.series, item.category, item.unit].flatMap((text) => (text === null ? [] : core(text)));
    if (written.some((token) => own.has(token) || longCovered.has(token))) {
      bump("value_text_in_answer");
      continue;
    }
    done.add(tag.n);
    let accepted: PlaceAnswer = { role: item.role, chart: null, title: null, series: null, category: null, categoryFrom: null, unit: null };
    if (item.role === "value") {
      let category = clean(item.category);
      let categoryFrom: PlaceAnswer["categoryFrom"] = category === null ? null : "text";
      const pointed = clean(item.category_tag);
      if (pointed !== null) {
        const target = byNumber.get(pointed.replace(/[〔〕]/g, "").trim());
        if (target && target.n !== tag.n) {
          category = rawOf(target.id).trim();
          categoryFrom = "tag";
        } else {
          out.badCategoryTag += 1;
          category = null;
          categoryFrom = null;
        }
      }
      accepted = { role: "value", chart: clean(item.chart), title: clean(item.title), series: clean(item.series), category, categoryFrom, unit: clean(item.unit) };
    } else if (item.role === "tick" || item.role === "category") accepted = { ...accepted, chart: clean(item.chart) };
    for (const id of tag.ids ?? [tag.id]) out.answers[id] = accepted;
  }
  out.unanswered = pageTags.filter((tag) => !done.has(tag.n)).map((tag) => tag.n);
  return out;
}

// ── 세 실행을 합치기 ────────────────────────────────────────────────────────

const squash = (text: string | null): string => (text ?? "").normalize("NFKC").replace(/\s+/g, "");
/** 두 답이 같은가를 가르는 열쇠: 갈래, 계열, 범주 (밴드 W 의 `answerKey` 와 같다. 제목·단위·묶음 이름은 보지 않는다). */
export const placeKey = (answer: { role: string; series: string | null; category: string | null } | undefined): string => (answer === undefined ? "∅" : answer.role !== "value" ? answer.role : `value|${squash(answer.series)}|${squash(answer.category)}`);

export type MergeRule = "majority" | "unanimous";

export interface MergedPlaces {
  /** ID → 합친 답. `chart` 는 합친 묶음의 이름(`p<쪽>-m<순번>`)으로 바뀐다. */
  answers: Record<string, PlaceAnswer>;
  agreement: { all: number; majority: number; none: number; ids: number };
}

/**
 * 여러 실행의 답을 하나로 합친다 (호출 전에 정한 규칙).
 * - 자리마다: `majority` 는 실행의 과반이, `unanimous` 는 실행 전부가 같은 (갈래, 계열, 범주)를 냈을 때만 그 답을 쓴다. 아니면 답 없음.
 *   한 실행만 낸 답은 어느 규칙에서도 쓰지 않는다. 제목과 단위는 그렇게 답한 가장 앞선 실행의 글자다.
 * - 도표 묶음: 묶음 이름은 실행마다 제멋대로라 이름으로 맞추지 않는다. 합친 답이 값인 두 자리가 같은 쪽에 있고,
 *   규칙이 요구하는 수만큼의 실행에서 같은 묶음에 들었으면 한 도표다. 그렇게 이어진 자리들이 한 묶음이다.
 * `pageOf` 는 ID → 쪽 (묶음 이름은 쪽 안에서만 뜻이 있다).
 */
export function mergePlaces(runs: Record<string, PlaceAnswer>[], pageOf: (id: string) => number, rule: MergeRule = "majority"): MergedPlaces {
  const need = rule === "unanimous" ? runs.length : Math.floor(runs.length / 2) + 1;
  const ids = [...new Set(runs.flatMap((run) => Object.keys(run)))].sort();
  const out: MergedPlaces = { answers: {}, agreement: { all: 0, majority: 0, none: 0, ids: ids.length } };
  for (const id of ids) {
    const votes = new Map<string, PlaceAnswer[]>();
    for (const run of runs) {
      const answer = run[id];
      if (answer === undefined) continue;
      votes.set(placeKey(answer), [...(votes.get(placeKey(answer)) ?? []), answer]);
    }
    const best = [...votes.values()].sort((x, y) => y.length - x.length)[0] ?? [];
    if (best.length === runs.length) out.agreement.all += 1;
    else if (best.length * 2 > runs.length) out.agreement.majority += 1;
    else out.agreement.none += 1;
    if (best.length >= need && best.length >= 2) out.answers[id] = { ...(best[0] as PlaceAnswer), chart: null };
  }
  // 도표 묶음: 합친 답이 값인 자리끼리, 같은 묶음에 든 실행의 수로 잇는다.
  const valueIds = Object.keys(out.answers).filter((id) => (out.answers[id] as PlaceAnswer).role === "value");
  const parent = new Map(valueIds.map((id) => [id, id]));
  const find = (id: string): string => {
    let at = id;
    while (parent.get(at) !== at) at = parent.get(at) as string;
    return at;
  };
  const byPage = new Map<number, string[]>();
  for (const id of valueIds) byPage.set(pageOf(id), [...(byPage.get(pageOf(id)) ?? []), id]);
  for (const members of byPage.values())
    for (let i = 0; i < members.length; i += 1)
      for (let j = i + 1; j < members.length; j += 1) {
        const [x, y] = [members[i] as string, members[j] as string];
        const together = runs.filter((run) => run[x]?.role === "value" && run[y]?.role === "value" && run[x]?.chart !== null && run[x]?.chart === run[y]?.chart).length;
        if (together >= need && together >= 2) parent.set(find(x), find(y));
      }
  const names = new Map<string, string>();
  const perPage = new Map<number, number>();
  for (const id of valueIds) {
    const root = find(id);
    if (!names.has(root)) {
      const page = pageOf(id);
      perPage.set(page, (perPage.get(page) ?? 0) + 1);
      names.set(root, `p${page}-m${perPage.get(page)}`);
    }
    (out.answers[id] as PlaceAnswer).chart = names.get(root) as string;
  }
  return out;
}

// ── 채점 ────────────────────────────────────────────────────────────────────

export interface GroupingScore {
  /** 정답 도표의 수 (값 숫자가 하나도 적히지 않은 도표까지). */
  charts: number;
  /** 정답의 값 자리가 하나라도 있는 도표. */
  chartsWithValues: number;
  /** 묶어 냄: 정답의 값 자리 절반 이상이 한 묶음에 값으로 들었고, 그 묶음의 값 자리 절반 이상이 이 도표의 것이다. */
  found: string[];
  /** 꼭 맞음: 그 묶음의 값 자리가 이 도표의 정답 값 자리와 똑같다. */
  exact: string[];
  missed: string[];
  /** 값 자리가 든 묶음 가운데 정답 도표의 값 자리가 하나도 없는 것 (도표가 아닌 것을 도표로 냄). `쪽|묶음 이름`. */
  falseCharts: string[];
  /** 값 자리가 든 묶음의 수. */
  groups: number;
}

/**
 * 도표 묶기의 채점. 정답: 도표마다 값 자리의 ID. 답: ID → (갈래, 묶음 이름). 묶음은 쪽마다 따로다.
 * `groupOf` 가 null 을 돌려주는 값 자리(묶음 이름을 적지 않은 답)는 어느 묶음에도 들지 않은 것으로 센다.
 */
export function scoreGrouping(goldCharts: { id: string; page: number; values: string[] }[], answers: Record<string, { role: string; group: string | null }>, pageOf: (id: string) => number): GroupingScore {
  const groups = new Map<string, Set<string>>();
  for (const [id, answer] of Object.entries(answers)) {
    if (answer.role !== "value" || answer.group === null) continue;
    const key = `${pageOf(id)}|${answer.group}`;
    groups.set(key, (groups.get(key) ?? new Set()).add(id));
  }
  const score: GroupingScore = { charts: goldCharts.length, chartsWithValues: 0, found: [], exact: [], missed: [], falseCharts: [], groups: groups.size };
  const goldValueIds = new Set(goldCharts.flatMap((chart) => chart.values));
  for (const chart of goldCharts) {
    if (chart.values.length === 0) {
      score.missed.push(chart.id);
      continue;
    }
    score.chartsWithValues += 1;
    const counts = new Map<string, number>();
    for (const id of chart.values) {
      const answer = answers[id];
      if (!answer || answer.role !== "value" || answer.group === null) continue;
      const key = `${chart.page}|${answer.group}`;
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    const best = [...counts.entries()].sort((x, y) => y[1] - x[1] || x[0].localeCompare(y[0]))[0];
    const members = best ? (groups.get(best[0]) as Set<string>) : new Set<string>();
    const mine = best ? best[1] : 0;
    if (best && mine * 2 >= chart.values.length && mine * 2 >= members.size) {
      score.found.push(chart.id);
      if (mine === chart.values.length && members.size === mine) score.exact.push(chart.id);
    } else score.missed.push(chart.id);
  }
  for (const [key, members] of groups) if (![...members].some((id) => goldValueIds.has(id))) score.falseCharts.push(key);
  return score;
}

export interface RoleScore {
  /** 정답의 갈래 → 답의 갈래 → 수. 답이 없으면 `none`. */
  matrix: Record<string, Record<string, number>>;
}

/** 갈래의 가름: 정답의 갈래마다 답이 무엇이었는가 (값 / 눈금 / 범주 축 / 그 밖 / 답 없음). */
export function scoreRoles(gold: { id: string; role: string }[], roleOf: (id: string) => string | undefined): RoleScore {
  const matrix: Record<string, Record<string, number>> = {};
  for (const item of gold) {
    const got = roleOf(item.id) ?? "none";
    matrix[item.role] = { ...(matrix[item.role] ?? {}), [got]: ((matrix[item.role] ?? {})[got] ?? 0) + 1 };
  }
  return { matrix };
}

/** 세 실행이 같은 답을 낸 자리의 수 (정답이 값인 자리에서. 밴드 W 의 셈과 같다). */
export function agreement(ids: string[], runs: Record<string, { role: string; series: string | null; category: string | null }>[]): { all: number; majority: number; none: number; unanswered: number } {
  const same = { all: 0, majority: 0, none: 0, unanswered: 0 };
  for (const id of ids) {
    const keys = runs.map((run) => placeKey(run[id]));
    const counts = new Map<string, number>();
    for (const key of keys) counts.set(key, (counts.get(key) ?? 0) + 1);
    const top = Math.max(...counts.values());
    if (keys.every((key) => key === "∅")) same.unanswered += 1;
    else if (top === runs.length) same.all += 1;
    else if (top * 2 > runs.length) same.majority += 1;
    else same.none += 1;
  }
  return same;
}

// ── 표로 세우는 변환의 입력 ──────────────────────────────────────────────────

/**
 * 답을 밴드 CT1 의 변환(`buildChartTables`)이 받는 꼴로 옮긴다. 도표는 답의 묶음이고(코드의 도표 찾기를 쓰지 않는다), 도표의 상자는 그 값 자리들을 감싼 상자다.
 * `factOf` 는 번호표의 ID → 값 후보의 ID (이미 값 후보 ID 면 그대로, 문서 모델에 이어지지 않은 낱말이면 null). 값 후보가 없는 자리는 옮길 수 없어 뺀다.
 * 묶음 이름이 없는 값 자리도 뺀다 (어느 도표인지 모른다).
 */
export function toChartTableInput(answers: Record<string, PlaceAnswer>, place: (id: string) => { page: number; bbox: number[] }, factOf: (id: string) => string | null, source: string): { input: ChartTableInput; noFact: string[]; noGroup: string[] } {
  const charts = new Map<string, { id: string; page: number; bbox: number[]; title: string | null }>();
  const values: ChartTableInput["values"] = [];
  const noFact: string[] = [];
  const noGroup: string[] = [];
  const seen = new Set<string>();
  for (const id of Object.keys(answers).sort()) {
    const answer = answers[id] as PlaceAnswer;
    if (answer.role !== "value") continue;
    if (answer.chart === null) {
      noGroup.push(id);
      continue;
    }
    const fact = factOf(id);
    if (fact === null) {
      noFact.push(id);
      continue;
    }
    if (seen.has(fact)) continue;
    seen.add(fact);
    const where = place(id);
    const chartId = answer.chart.startsWith(`p${where.page}-`) ? answer.chart : `p${where.page}-g${answer.chart}`;
    const chart = charts.get(chartId);
    if (!chart) charts.set(chartId, { id: chartId, page: where.page, bbox: [...where.bbox], title: answer.title });
    else chart.bbox = [Math.min(chart.bbox[0] as number, where.bbox[0] as number), Math.min(chart.bbox[1] as number, where.bbox[1] as number), Math.max(chart.bbox[2] as number, where.bbox[2] as number), Math.max(chart.bbox[3] as number, where.bbox[3] as number)];
    values.push({ id: fact, chart: chartId, series: answer.series, category: answer.category, bbox: where.bbox });
  }
  return { input: { source, charts: [...charts.values()], values }, noFact, noGroup };
}

/** 정답 파일(밴드 W 의 꼴)에서 도표마다의 값 자리. */
export const goldChartValues = (gold: ChartGold, doc: "a" | "b"): { id: string; page: number; values: string[] }[] => gold.charts.filter((chart) => chart.doc === doc).map((chart) => ({ id: chart.id, page: chart.page, values: gold.labels.filter((label) => label.doc === doc && label.chart === chart.id && label.role === "value").map((label) => label.id) }));
