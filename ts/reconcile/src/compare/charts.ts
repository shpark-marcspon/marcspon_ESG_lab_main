import { z } from "zod";
import type { Extraction, Fact } from "../domain/facts.js";

// 도표 라벨 (밴드 W). `py/docparse charts` 가 낸 낱말 단위의 라벨을 값 후보의 ID 에 걸고, 정답과 견준다.
// 여기서는 값을 읽지 않는다. 라벨은 글(도표 제목, 계열, 범주)이고 값 후보의 목록·ID·값은 그대로다.

const methodLabel = z.object({ category: z.string().nullable(), status: z.string() });
export const chartLabelsSchema = z.object({
  charts: z.array(z.object({ id: z.string(), page: z.number(), bbox: z.array(z.number()), form: z.string(), orientation: z.string(), title: z.string().nullable(), multi_series: z.boolean() })),
  words: z.array(
    z.object({
      container: z.string(),
      start: z.number(),
      end: z.number(),
      text: z.string(),
      bbox: z.array(z.number()),
      chart: z.string(),
      role: z.enum(["value", "tick", "category"]),
      coord: methodLabel.optional(),
      elem: methodLabel.extend({ series: z.string().nullable(), series_status: z.string() }).optional(),
      label: methodLabel.extend({ title: z.string().nullable(), series: z.string().nullable(), series_status: z.string() }).optional(),
    }),
  ),
});
export type ChartLabels = z.infer<typeof chartLabelsSchema>;
export type ChartWord = ChartLabels["words"][number];

export const chartGoldSchema = z.object({
  charts: z.array(z.object({ id: z.string(), doc: z.enum(["a", "b"]), page: z.number(), kind: z.string(), title: z.string(), series: z.array(z.string()).nullable(), bbox: z.array(z.number()), value_texts_without_fact: z.array(z.string()) })),
  labels: z.array(z.object({ doc: z.enum(["a", "b"]), id: z.string(), raw: z.string(), chart: z.string(), role: z.enum(["value", "tick", "category", "other"]), series: z.array(z.string().nullable()).nullable(), category: z.array(z.string()).nullable() })),
});
export type ChartGold = z.infer<typeof chartGoldSchema>;

/** 값 후보 하나에 걸린 도표 낱말. */
export interface FactChart {
  id: string;
  page: number;
  chart: string;
  bbox: number[];
  word: ChartWord;
}

const containerOf = (fact: Fact): string => (fact.where.gridId !== null ? `${fact.where.gridId}:r${fact.where.row}c${fact.where.col}` : (fact.where.blockId as string));

/**
 * 도표 낱말을 값 후보에 건다. 값 후보의 그릇(블록 ID 또는 `표ID:r행c열`)과 글자 자리가 낱말과 겹치면 그 낱말이다.
 * 표의 숫자 칸(칸 전체가 값 후보)은 그 칸의 낱말이 하나일 때만 건다. 한 값 후보에 도표 낱말이 둘 이상 걸리면 걸지 않는다 (어느 것인지 가를 수 없다).
 */
export function linkFactsToCharts(extraction: Extraction, labels: ChartLabels): { linked: FactChart[]; ambiguous: string[] } {
  const byContainer = new Map<string, ChartWord[]>();
  for (const word of labels.words) byContainer.set(word.container, [...(byContainer.get(word.container) ?? []), word]);
  const linked: FactChart[] = [];
  const ambiguous: string[] = [];
  for (const fact of extraction.facts) {
    const words = byContainer.get(containerOf(fact)) ?? [];
    const span = fact.where.span;
    const hit = span ? words.filter((word) => word.start < span.end && word.end > span.start) : words;
    if (hit.length === 0) continue;
    if (hit.length > 1) {
      ambiguous.push(fact.id);
      continue;
    }
    const word = hit[0] as ChartWord;
    linked.push({ id: fact.id, page: fact.where.page as number, chart: word.chart, bbox: word.bbox, word });
  }
  return { linked, ambiguous };
}

export interface Tag {
  page: number;
  n: number;
  bbox: number[];
  id: string;
  /** 이 번호를 같이 쓰는 값 후보 전부 (자리가 같은 것). 첫째가 `id` 다. */
  ids?: string[] | undefined;
}

/**
 * 번호표: 도표 낱말이 걸린 값 후보에 쪽마다 1 부터 번호를 준다 (값 후보의 문서 순서). 번호는 순번일 뿐이고 값과 무관하다.
 * 자리가 같은 값 후보(겹친 표가 같은 글자를 두 번 실은 것)는 번호 하나를 같이 쓴다.
 * 범주 축의 글자(연도 등)에는 번호표를 붙이지 않는다: 덮으면 쪽 그림에서 범주를 읽을 수 없다. 값과 눈금만 덮는다.
 */
export function assignTags(linked: FactChart[]): { tags: Tag[]; numberOf: Map<string, { page: number; n: number }> } {
  const tags: Tag[] = [];
  const numberOf = new Map<string, { page: number; n: number }>();
  const next = new Map<number, number>();
  const atPlace = new Map<string, number>();
  for (const item of linked) {
    if (item.word.role === "category") continue;
    const place = `${item.page}|${item.bbox.join(",")}`;
    let n = atPlace.get(place);
    if (n === undefined) {
      n = (next.get(item.page) ?? 0) + 1;
      next.set(item.page, n);
      atPlace.set(place, n);
      tags.push({ page: item.page, n, bbox: item.bbox, id: item.id, ids: [item.id] });
    } else (tags.find((tag) => tag.page === item.page && tag.n === n) as Tag).ids?.push(item.id);
    numberOf.set(item.id, { page: item.page, n });
  }
  return { tags, numberOf };
}

const LABEL_SEPARATOR = " · ";

/** 요청 글에 싣는 라벨 글: `도표 제목 · 계열 · 범주`. 범주가 없으면 라벨을 싣지 않는다 (제목만으로는 어느 값인지 가리키지 못한다). */
export function labelText(parts: { title: string | null; series: string | null; category: string | null }): string | null {
  if (parts.category === null) return null;
  return [parts.title, parts.series, parts.category].filter((part): part is string => part !== null && part.trim() !== "").join(LABEL_SEPARATOR);
}

/** 코드 라벨(합친 것)에서 값 후보 ID → 라벨 글. */
export function codeLabels(linked: FactChart[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const item of linked) {
    const label = item.word.role === "value" ? item.word.label : undefined;
    if (!label) continue;
    const text = labelText(label);
    if (text !== null) out[item.id] = text;
  }
  return out;
}

// ── 채점 ────────────────────────────────────────────────────────────────────

const squash = (text: string): string => text.normalize("NFKC").replace(/\s+/g, "");
const area = (b: number[]): number => Math.max(0, (b[2] as number) - (b[0] as number)) * Math.max(0, (b[3] as number) - (b[1] as number));
const overlap = (a: number[], b: number[]): number => area([Math.max(a[0] as number, b[0] as number), Math.max(a[1] as number, b[1] as number), Math.min(a[2] as number, b[2] as number), Math.min(a[3] as number, b[3] as number)]);

export interface ChartMatch {
  /** 정답 도표 ID → 코드가 찾은 도표 ID. */
  matched: Record<string, string>;
  missed: string[];
  /** 코드가 도표로 봤지만 정답에 없는 것. */
  extra: string[];
}

/** 도표 찾기의 대조: 같은 쪽에서 겹친 넓이가 작은 쪽 상자의 절반 이상이면 같은 도표다. 많이 겹친 것부터 하나씩 짝짓는다. */
export function matchCharts(gold: ChartGold["charts"], found: { id: string; page: number; bbox: number[] }[]): ChartMatch {
  const pairs: { g: string; f: string; share: number }[] = [];
  for (const g of gold) for (const f of found) if (g.page === f.page) pairs.push({ g: g.id, f: f.id, share: overlap(g.bbox, f.bbox) / Math.max(1e-9, Math.min(area(g.bbox), area(f.bbox))) });
  const matched: Record<string, string> = {};
  const usedF = new Set<string>();
  for (const pair of pairs.filter((item) => item.share >= 0.5).sort((x, y) => y.share - x.share)) {
    if (matched[pair.g] !== undefined || usedF.has(pair.f)) continue;
    matched[pair.g] = pair.f;
    usedF.add(pair.f);
  }
  return { matched, missed: gold.filter((g) => matched[g.id] === undefined).map((g) => g.id), extra: found.filter((f) => !usedF.has(f.id)).map((f) => f.id) };
}

/** 라벨 하나의 예측 (방법과 무관한 꼴). 값이 아니라고 봤거나 도표로 보지 못했으면 role 이 value 가 아니다. */
export interface Predicted {
  role: "value" | "not_value";
  category: string | null;
  /** undefined: 이 방법은 계열을 붙이지 않는다 (좌표). */
  series?: string | null;
}

export interface LabelScore {
  /** 정답이 「값」인 자리. */
  values: number;
  seenAsValue: number;
  category: { attached: number; correct: number; none: number };
  /** 계열 이름이 있는 정답(여러 계열 도표의 값)에서. */
  series: { gold: number; attached: number; correct: number; correctStrict: number; none: number };
  /** 계열 이름이 없는 정답(계열 하나 도표, 기준 막대)에 계열을 붙인 수. */
  seriesOnUnnamed: { gold: number; attached: number };
  /** 범주와 계열(있는 자리만)이 둘 다 맞은 수. */
  bothCorrect: number;
  /** 정답이 값이 아닌 자리(눈금, 범주 축의 글자, 그 밖). */
  nonValues: number;
  nonValuesSeenAsValue: number;
  wrong: { doc: string; id: string; raw: string; chart: string; what: string; gold: string; got: string }[];
}

export function scoreLabels(gold: ChartGold["labels"], predict: (doc: "a" | "b", id: string) => Predicted): LabelScore {
  const score: LabelScore = { values: 0, seenAsValue: 0, category: { attached: 0, correct: 0, none: 0 }, series: { gold: 0, attached: 0, correct: 0, correctStrict: 0, none: 0 }, seriesOnUnnamed: { gold: 0, attached: 0 }, bothCorrect: 0, nonValues: 0, nonValuesSeenAsValue: 0, wrong: [] };
  for (const item of gold) {
    const got = predict(item.doc, item.id);
    if (item.role !== "value") {
      score.nonValues += 1;
      if (got.role === "value") score.nonValuesSeenAsValue += 1;
      continue;
    }
    score.values += 1;
    if (got.role === "value") score.seenAsValue += 1;
    const category = got.role === "value" ? got.category : null;
    const categories = (item.category ?? []).map(squash);
    let categoryOk = false;
    if (category === null) score.category.none += 1;
    else {
      score.category.attached += 1;
      categoryOk = categories.includes(squash(category));
      if (categoryOk) score.category.correct += 1;
      else score.wrong.push({ doc: item.doc, id: item.id, raw: item.raw, chart: item.chart, what: "category", gold: (item.category ?? []).join(" / "), got: category });
    }
    const named = (item.series ?? []).filter((name): name is string => name !== null);
    let seriesOk = true;
    if (got.series !== undefined) {
      const series = got.role === "value" ? got.series : null;
      if (named.length === 0) {
        score.seriesOnUnnamed.gold += 1;
        if (series !== null) {
          score.seriesOnUnnamed.attached += 1;
          seriesOk = false;
          score.wrong.push({ doc: item.doc, id: item.id, raw: item.raw, chart: item.chart, what: "series_on_unnamed", gold: "(계열 이름 없음)", got: series });
        }
      } else {
        score.series.gold += 1;
        if (series === null) {
          score.series.none += 1;
          seriesOk = false;
        } else {
          score.series.attached += 1;
          seriesOk = named.map(squash).includes(squash(series));
          if (seriesOk) score.series.correct += 1;
          else score.wrong.push({ doc: item.doc, id: item.id, raw: item.raw, chart: item.chart, what: "series", gold: named.join(" / "), got: series });
          if (squash(named[0] as string) === squash(series)) score.series.correctStrict += 1;
        }
      }
    }
    if (categoryOk && seriesOk) score.bothCorrect += 1;
  }
  return score;
}
