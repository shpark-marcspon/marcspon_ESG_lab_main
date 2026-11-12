import type { DocModel, Extraction } from "../domain/facts.js";
import { cellHeaders, toDerivedId, type MapEntry } from "./chart-tables.js";
import type { ChartGold } from "./charts.js";
import type { GoldPairs, PlantSpec } from "./exam.js";
import { isProseId, type Gold2, type Plant2Spec, type ProsePair } from "./exam2.js";
import type { MethodOutcome, Verdict } from "./methods.js";
import type { PairPlacesAnswer } from "./prose.js";

// 밴드 CT2 (MARC-TICK-38): 도표를 표로 세운 파생 문서로 방법 D2 를 돌릴 때의 준비와 채점. 모델을 부르지 않는다.
//
// - 정답 쌍·심은 자리(원래 문서의 ID 로 적혀 있다)를 CT1 의 대응표로 파생 문서의 ID 로 옮긴다. 정답 파일은 고치지 않고 옮긴 사본을 낸다.
// - 겨냥(47쪽의 6칸, 도표가 낀 서술 쌍, 도표에 심은 오류)이 저장된 답에서 어떻게 나왔는지 센다. 받아들인 짝과 버려진 답을 따로 센다.
// 요청의 조립과 답의 확인은 밴드 U 의 것(`cli2.ts run2 --method d2`)을 그대로 쓴다. 여기에는 그 길이 없다.

type Side = "a" | "b";
const squash = (text: string): string => text.normalize("NFKC").replace(/\s+/g, "");

// ── 정답의 ID 옮기기 ─────────────────────────────────────────────────────────

export interface Maps {
  a: MapEntry[];
  b: MapEntry[];
}

export interface GoldSet {
  pairs: GoldPairs;
  plants: PlantSpec;
  gold: Gold2;
  plants2: Plant2Spec;
}

/** 정답에 나오는 자리 하나가 어떻게 옮겨졌는가. */
export interface MovedPlace {
  doc: Side;
  from: string;
  to: string;
  /** moved = 새 표의 칸으로, renumbered = 원래 그릇에 남아 순번만 바뀜, same = 그대로. */
  kind: "moved" | "renumbered" | "same";
  /** 어디에 쓰인 자리인가 (정답 쌍·묶음·심은 것의 ID). */
  usedIn: string[];
}

export interface MovedGold extends GoldSet {
  /** 옮긴 뒤 양쪽이 다 표의 숫자 칸이 된 서술 쌍. 넓힌 정답의 「서술이 낀 쌍」 꼴(`checkGold2`)에 더는 맞지 않아 따로 싣는다. */
  chartPairs: ProsePair[];
  places: MovedPlace[];
  counts: { pairs: [number, number]; plantCells: [number, number]; prosePairs: [number, number]; groups: [number, number]; groupPlaces: [number, number]; plants2: [number, number] };
}

/**
 * 정답 쌍·심은 자리의 ID 를 대응표로 옮긴다. 글자(raw, original, planted)는 그대로다. 넘겨받은 것은 고치지 않는다.
 * 대응표에 없는 ID 는 그대로다 (도표의 값이 아니거나, 서지 못한 도표의 값이거나, 겹친 그릇의 둘째라 원래 자리에 남은 것).
 */
export function moveGold(source: GoldSet, maps: Maps): MovedGold {
  const used = new Map<string, MovedPlace>();
  const to = (doc: Side, id: string, where: string): string => {
    const entry = maps[doc].find((item) => item.from === id);
    const key = `${doc}:${id}`;
    const place = used.get(key) ?? { doc, from: id, to: entry?.id ?? id, kind: entry?.kind ?? "same", usedIn: [] };
    place.usedIn.push(where);
    used.set(key, place);
    return place.to;
  };
  const pairs: GoldPairs = { ...source.pairs, pairs: source.pairs.pairs.map((pair) => ({ ...pair, a: to("a", pair.a, pair.id), b: to("b", pair.b, pair.id) })) };
  const plants: PlantSpec = { ...source.plants, plants: source.plants.plants.map((plant) => ({ ...plant, cells: plant.cells.map((cell) => ({ ...cell, cell: to("b", cell.cell, plant.id) })), otherPlaceInB: plant.otherPlaceInB.map((id) => to("b", id, plant.id)) })) };
  const prose = source.gold.prosePairs.map((pair) => ({ ...pair, a: to("a", pair.a, pair.id), b: to("b", pair.b, pair.id) }));
  const groups = source.gold.groups.map((group) => ({ ...group, places: group.places.map((place) => ({ ...place, id: to(group.doc, place.id, group.id) })) }));
  const stillProse = prose.filter((pair) => isProseId(pair.a) || isProseId(pair.b));
  const chartPairs = prose.filter((pair) => !isProseId(pair.a) && !isProseId(pair.b));
  const plants2: Plant2Spec = { ...source.plants2, plants: source.plants2.plants.map((plant) => ({ ...plant, target: to(plant.doc, plant.target, plant.id) })) };
  const count = (list: { places: unknown[] }[]): number => list.reduce((sum, item) => sum + item.places.length, 0);
  return {
    pairs,
    plants,
    gold: { ...source.gold, prosePairs: stillProse, groups },
    plants2,
    chartPairs,
    places: [...used.values()],
    counts: {
      pairs: [source.pairs.pairs.length, pairs.pairs.length],
      plantCells: [source.plants.plants.reduce((sum, plant) => sum + plant.cells.length, 0), plants.plants.reduce((sum, plant) => sum + plant.cells.length, 0)],
      prosePairs: [source.gold.prosePairs.length, stillProse.length + chartPairs.length],
      groups: [source.gold.groups.length, groups.length],
      groupPlaces: [count(source.gold.groups), count(groups)],
      plants2: [source.plants2.plants.length, plants2.plants.length],
    },
  };
}

/**
 * 옮긴 정답이 파생 문서와 맞는가: 옮긴 ID 마다 파생 문서에 값 후보가 있고 글자가 원래 문서의 그 자리와 같은가,
 * 서로 다른 두 자리가 한 ID 로 겹치지 않았는가. 문제가 없으면 빈 목록.
 */
export function checkMoved(moved: MovedGold, original: Record<Side, Extraction>, derived: Record<Side, Extraction>): string[] {
  const problems: string[] = [];
  const seen = new Map<string, string>();
  for (const place of moved.places) {
    const before = original[place.doc].facts.find((fact) => fact.id === place.from);
    const after = derived[place.doc].facts.find((fact) => fact.id === place.to);
    if (!before) problems.push(`${place.doc}:${place.from}: 원래 문서에 없는 자리다`);
    else if (!after) problems.push(`${place.doc}:${place.from} → ${place.to}: 파생 문서에 값 후보가 없다 (옮겨지지 않았다)`);
    else if (after.raw !== before.raw) problems.push(`${place.doc}:${place.from} → ${place.to}: 글자가 다르다 (「${before.raw}」 → 「${after.raw}」)`);
    const key = `${place.doc}:${place.to}`;
    if (seen.has(key)) problems.push(`${place.doc}:${place.to}: 두 자리(${seen.get(key)}, ${place.from})가 한 ID 로 겹쳤다`);
    seen.set(key, place.from);
  }
  for (const [name, [before, after]] of Object.entries(moved.counts)) if (before !== after) problems.push(`${name}: 옮기기 앞뒤의 수가 다르다 (${before} → ${after})`);
  return problems;
}

/** 파생 시험지의 심은 자리마다, 옮긴 ID 의 값 후보가 심은 글자를 싣고 있는가 (원본의 파생 문서에서는 원래 글자를). */
export function checkPlantedCells(moved: MovedGold, derived: Record<Side, Extraction>, derivedExam: Record<Side, Extraction>): string[] {
  const problems: string[] = [];
  const targets = [...moved.plants.plants.flatMap((plant) => plant.cells.map((cell) => ({ doc: "b" as Side, id: cell.cell, original: cell.original, planted: cell.planted, plant: plant.id }))), ...moved.plants2.plants.map((plant) => ({ doc: plant.doc as Side, id: plant.target, original: plant.original, planted: plant.planted, plant: plant.id }))];
  for (const target of targets) {
    const clean = derived[target.doc].facts.find((fact) => fact.id === target.id)?.raw;
    const exam = derivedExam[target.doc].facts.find((fact) => fact.id === target.id)?.raw;
    if (clean !== target.original) problems.push(`${target.plant}: 파생 문서의 ${target.doc}:${target.id} 가 「${clean}」 이고 명세의 원래 글자는 「${target.original}」`);
    if (exam !== target.planted) problems.push(`${target.plant}: 파생 시험지의 ${target.doc}:${target.id} 가 「${exam}」 이고 심은 글자는 「${target.planted}」`);
  }
  return problems;
}

// ── 겨냥 ─────────────────────────────────────────────────────────────────────

/** 겨냥의 자리 하나 (원래 문서의 ID). */
export interface AimPlace {
  aim: 1 | 2 | 3;
  /** 겨냥 2 는 서술 쌍의 ID, 겨냥 3 은 심은 것의 ID, 겨냥 1 은 그 자리의 이름. */
  name: string;
  doc: Side;
  id: string;
}

/**
 * 겨냥의 자리를 정답에서 기계로 고른다 (원래 문서의 ID).
 * 겨냥 1 은 넘겨받은 자리 그대로. 겨냥 2 는 서술 쌍 가운데 한쪽이 도표 정답의 값 자리인 것. 겨냥 3 은 서술 쌍에 심은 오류 가운데 도표 정답의 값 자리인 것.
 */
export function aimPlaces(aim1: { name: string; doc: Side; id: string }[], gold: Gold2, plants2: Plant2Spec, chartGold: ChartGold): AimPlace[] {
  const isChartValue = (doc: Side, id: string): boolean => chartGold.labels.some((label) => label.doc === doc && label.id === id && label.role === "value");
  const out: AimPlace[] = aim1.map((place) => ({ aim: 1, ...place }));
  for (const pair of gold.prosePairs) {
    if (isChartValue("a", pair.a)) out.push({ aim: 2, name: pair.id, doc: "a", id: pair.a });
    else if (isChartValue("b", pair.b)) out.push({ aim: 2, name: pair.id, doc: "b", id: pair.b });
  }
  for (const plant of plants2.plants) if (plant.pool === "prose_pair" && plant.isError && isChartValue(plant.doc, plant.target)) out.push({ aim: 3, name: plant.id, doc: plant.doc, id: plant.target });
  return out;
}

export interface AimRow extends AimPlace {
  kind: "moved" | "renumbered" | "same";
  to: string;
  /** 새 표의 칸의 머리글 (옮겨졌을 때). */
  headers: { series: string; category: string } | null;
  /** 도표 정답과 견준 결과. 옮겨지지 않았거나 도표 정답에 없는 자리면 null. */
  placed: "right" | "wrong" | null;
}

/** 한 벌의 대응표에서 겨냥의 자리가 어디로 갔는가. 옳은 칸의 기준은 CT1 의 `scorePlacement` 와 같다. */
export function aimRows(places: AimPlace[], maps: Maps, derived: Record<Side, { model: DocModel; extraction: Extraction }>, chartGold: ChartGold): AimRow[] {
  return places.map((place) => {
    const entry = maps[place.doc].find((item) => item.from === place.id);
    const kind = entry?.kind ?? "same";
    const to = entry?.id ?? place.id;
    if (kind !== "moved") return { ...place, kind, to, headers: null, placed: null };
    const headers = cellHeaders(derived[place.doc].model, derived[place.doc].extraction, to);
    const label = chartGold.labels.find((item) => item.doc === place.doc && item.id === place.id && item.role === "value");
    if (!label || !headers) return { ...place, kind, to, headers, placed: null };
    const named = (label.series ?? []).filter((name): name is string => name !== null);
    const right = (label.category ?? []).map(squash).includes(squash(headers.category)) && (named.length === 0 ? headers.series === "" : named.map(squash).includes(squash(headers.series)));
    return { ...place, kind, to, headers, placed: right ? "right" : "wrong" };
  });
}

export const aimSummary = (rows: AimRow[]): { total: number; distinct: number; moved: number; movedDistinct: number; wrong: number; byAim: Record<string, string> } => {
  const key = (row: AimRow): string => `${row.doc}:${row.id}`;
  const moved = rows.filter((row) => row.kind === "moved");
  const byAim: Record<string, string> = {};
  for (const aim of [1, 2, 3] as const) byAim[aim] = `${moved.filter((row) => row.aim === aim).length}/${rows.filter((row) => row.aim === aim).length}`;
  return { total: rows.length, distinct: new Set(rows.map(key)).size, moved: moved.length, movedDistinct: new Set(moved.map(key)).size, wrong: new Set(moved.filter((row) => row.placed === "wrong").map(key)).size, byAim };
};

// ── 채점 ─────────────────────────────────────────────────────────────────────

/** 채점에 쓰는 겨냥 (그 실행이 쓴 문서의 ID 로). */
export interface AimSpec {
  /** 겨냥 1: 자리마다 「옳은 상대」(문서 B 의 ID). */
  aim1: { name: string; a: string; right: string[] }[];
  /** 겨냥 2: 도표가 낀 정답 쌍. */
  aim2: { pair: string; a: string; b: string }[];
  /** 겨냥 3: 도표에 심은 오류와 그 정답 쌍. */
  aim3: { plant: string; pair: string; a: string; b: string }[];
  /** 표로 세운 도표의 값 자리 (그 실행의 ID 로. 원래 문서의 실행에서는 「같은 값이 원래 놓여 있던 자리」). */
  chartCells: Record<Side, string[]>;
  /** 도표 정답의 값인데 표로 옮겨지지 않은 자리 (서지 못한 도표, 범주 없는 값, 겹친 둘째). */
  leftBehind: Record<Side, string[]>;
}

export interface AimScore {
  aim1: { total: number; paired: number; right: number; other: number; rows: { name: string; a: string; partners: { b: string; outcome: Verdict["outcome"]; right: boolean }[]; inRejected: boolean }[] };
  aim2: { total: number; paired: number; match: number; mismatch: number; rows: { pair: string; a: string; b: string; outcome: Verdict["outcome"] | null; otherPartners: string[]; inRejected: boolean }[] };
  aim3: { total: number; caught: number; falseMatch: number; silent: number; rows: { plant: string; pair: string; outcome: Verdict["outcome"] | null; bucket: "caught" | "false_match" | "silent" }[] };
  /** 표로 세운 도표의 값 자리 가운데 답에 한 번이라도 나온 것. accepted = 받아들인 짝에, rejectedOnly = 버려진 답에만. */
  chartCells: { total: number; accepted: number; rejectedOnly: number; acceptedIds: string[]; rejectedOnlyIds: string[] };
  leftBehind: { total: number; accepted: number; rejectedOnly: number; acceptedIds: string[]; rejectedOnlyIds: string[] };
  /** 표로 세운 도표의 값 자리가 낀 받아들인 짝 가운데 정답(표 칸 쌍, 서술 쌍, 도표 쌍) 밖의 것. 원문으로 판정할 대상이다. */
  outsideGold: Verdict[];
}

/** 모델의 원답에 나온 ID (문서별, 접두 `a:`·`b:` 를 뗀 것). 버려진 답까지 전부. */
export function idsInAnswers(answers: PairPlacesAnswer[]): Record<Side, Set<string>> {
  const out = { a: new Set<string>(), b: new Set<string>() };
  for (const answer of answers) {
    for (const pair of answer.pairs) {
      for (const id of [pair.a, pair.b]) {
        if (id.startsWith("a:")) out.a.add(id.slice(2));
        else if (id.startsWith("b:")) out.b.add(id.slice(2));
      }
    }
  }
  return out;
}

/**
 * 겨냥의 셈. 「짝으로 나왔다」는 받아들인 짝(`outcome.verdicts`)에 있는 것만이다.
 * 원답에는 있지만 확인에서 버려진 답은 짝으로 세지 않고 `inRejected`·`rejectedOnly` 로 따로 센다.
 */
export function scoreAims(spec: AimSpec, outcome: Pick<MethodOutcome, "verdicts">, answers: PairPlacesAnswer[], goldKeys: Set<string>): AimScore {
  const raw = idsInAnswers(answers);
  const accepted = { a: new Set(outcome.verdicts.map((verdict) => verdict.a)), b: new Set(outcome.verdicts.map((verdict) => verdict.b)) };
  const rejectedOnly = (doc: Side, id: string): boolean => raw[doc].has(id) && !accepted[doc].has(id);
  const exact = new Map(outcome.verdicts.map((verdict) => [`${verdict.a}|${verdict.b}`, verdict]));

  const aim1Rows = spec.aim1.map((place) => ({ name: place.name, a: place.a, partners: outcome.verdicts.filter((verdict) => verdict.a === place.a).map((verdict) => ({ b: verdict.b, outcome: verdict.outcome, right: place.right.includes(verdict.b) })), inRejected: rejectedOnly("a", place.a) }));
  const aim2Rows = spec.aim2.map((pair) => ({ pair: pair.pair, a: pair.a, b: pair.b, outcome: exact.get(`${pair.a}|${pair.b}`)?.outcome ?? null, otherPartners: outcome.verdicts.filter((verdict) => verdict.a === pair.a && verdict.b !== pair.b).map((verdict) => verdict.b), inRejected: rejectedOnly("a", pair.a) }));
  const aim3Rows = spec.aim3.map((plant) => {
    const found = exact.get(`${plant.a}|${plant.b}`)?.outcome ?? null;
    const bucket = found === "mismatch" ? ("caught" as const) : found === "match" ? ("false_match" as const) : ("silent" as const);
    return { plant: plant.plant, pair: plant.pair, outcome: found, bucket };
  });
  const appear = (ids: Record<Side, string[]>): AimScore["chartCells"] => {
    const all = (["a", "b"] as const).flatMap((doc) => ids[doc].map((id) => ({ doc, id })));
    const acceptedIds = all.filter((item) => accepted[item.doc].has(item.id)).map((item) => `${item.doc}:${item.id}`);
    const rejectedOnlyIds = all.filter((item) => rejectedOnly(item.doc, item.id)).map((item) => `${item.doc}:${item.id}`);
    return { total: all.length, accepted: acceptedIds.length, rejectedOnly: rejectedOnlyIds.length, acceptedIds, rejectedOnlyIds };
  };
  const cells = { a: new Set(spec.chartCells.a), b: new Set(spec.chartCells.b) };
  return {
    aim1: { total: aim1Rows.length, paired: aim1Rows.filter((row) => row.partners.length > 0).length, right: aim1Rows.filter((row) => row.partners.some((partner) => partner.right)).length, other: aim1Rows.filter((row) => row.partners.length > 0 && !row.partners.some((partner) => partner.right)).length, rows: aim1Rows },
    aim2: { total: aim2Rows.length, paired: aim2Rows.filter((row) => row.outcome !== null).length, match: aim2Rows.filter((row) => row.outcome === "match").length, mismatch: aim2Rows.filter((row) => row.outcome === "mismatch").length, rows: aim2Rows },
    aim3: { total: aim3Rows.length, caught: aim3Rows.filter((row) => row.bucket === "caught").length, falseMatch: aim3Rows.filter((row) => row.bucket === "false_match").length, silent: aim3Rows.filter((row) => row.bucket === "silent").length, rows: aim3Rows },
    chartCells: appear(spec.chartCells),
    leftBehind: appear(spec.leftBehind),
    outsideGold: outcome.verdicts.filter((verdict) => (cells.a.has(verdict.a) || cells.b.has(verdict.b)) && !goldKeys.has(`${verdict.a}|${verdict.b}`)),
  };
}

/** 표 칸의 원래 성적 가운데 `grade()` 가 따로 세지 않는 것: 정답 쌍 가운데 그 두 칸이 짝으로 나오지 않은 것. */
export function missingPairs(pairs: { id: string; a: string; b: string }[], verdicts: Verdict[]): string[] {
  const keys = new Set(verdicts.map((verdict) => `${verdict.a}|${verdict.b}`));
  return pairs.filter((pair) => !keys.has(`${pair.a}|${pair.b}`)).map((pair) => pair.id);
}
