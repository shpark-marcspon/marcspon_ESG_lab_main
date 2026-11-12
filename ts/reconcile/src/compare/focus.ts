import { z } from "zod";
import type { Extraction, Fact } from "../domain/facts.js";
import { ASK_REASONS, buildAskRequest, renderSourcePages, targetKey, type AskDoc, type AskPlacesAnswer, type AskPlan, type AskRequest, type AskState, type Target } from "./ask.js";
import type { Verdict } from "./methods.js";
import type { PlaceJudgement } from "./prose.js";

// 밴드 CH1 (MARC-TICK-40) 의 방법 F: 도표의 값을 좁혀 묻기.
// 방법 Q(밴드 X)는 요청마다 상대 문서를 통째로 싣는다. 방법 F 는 코드가 「도표가 든 쪽 → 상대 문서의 볼 만한 쪽」을 먼저 지목하고,
// 요청 하나에 그 두 쪽만 싣는다. 대상은 그 쪽의 도표 값 전부다(지목의 신호가 된 값만이 아니다). 값은 여전히 가리고, 답한 짝의 값은 코드가 견준다.
// 이 파일은 정답·명세를 읽지 않는다.

type Side = "a" | "b";
const otherOf = (side: Side): Side => (side === "a" ? "b" : "a");
const squash = (text: string): string => text.normalize("NFKC").replace(/\s+/g, "");

/** 값이 같다는 것을 지목의 신호로 쓰는 값 후보의 최소 유효 숫자 수. 짧은 숫자는 우연히 같기 쉽다. */
export const MIN_SIGNAL_DIGITS = 4;
/** 이름으로 지목할 때, 머리글이 계열·제목 글을 품는 것으로 볼 최소 글자 수 (공백을 뗀 뒤). */
export const MIN_NAME_CHARS = 3;

export const NOMINATE_MODES = ["value", "names", "either"] as const;
export type NominateMode = (typeof NOMINATE_MODES)[number];

/** 값의 유효 숫자 수 (부호·소수점·앞의 0 을 뗀 자릿수). */
export const significantDigits = (value: string): number => value.replace(/[-.]/g, "").replace(/^0+/, "").length;

export interface Signal {
  /** 신호를 낸 대상 (`문서:ID`). */
  target: string;
  /** 상대 문서의 자리 (`문서:ID`). */
  other: string;
  page: number;
  by: "value" | "names";
}

export interface Nomination {
  source: Side;
  page: number;
  /** 상대 문서에서 같이 실을 쪽 (오름차순). */
  otherPages: number[];
  signals: Signal[];
}

const factsOf = (extraction: Extraction): Map<string, Fact> => new Map(extraction.facts.map((fact) => [fact.id, fact]));

/**
 * 값이 같은 것을 **지목의 신호로만** 쓴다: 대상의 값과 글자 그대로 같은 값(`Fact.value`, 백분율 여부까지)이 상대 문서의 값 후보에 있으면 그 쪽을 지목한다.
 * 짝을 정하지 않는다. 지목된 쪽에서 무엇이 같은 항목인지는 가린 글을 본 AI 가 답하고, 그 요청의 대상은 그 쪽의 도표 값 전부다.
 * 그래서 값이 틀린 자리도 같은 쪽의 다른 값이 신호를 내면 물음에 들어간다. 한 쪽의 도표 값이 전부 틀렸으면 지목되지 않는다 (이 신호의 한계).
 */
function valueSignals(targets: Target[], docs: Record<Side, AskDoc>, minDigits: number): Signal[] {
  const out: Signal[] = [];
  for (const side of ["a", "b"] as const) {
    const mine = factsOf(docs[side].extraction);
    const other = otherOf(side);
    const kinds = docs[other].extraction.kinds;
    const byValue = new Map<string, Fact[]>();
    for (const fact of docs[other].extraction.facts) {
      if (kinds && kinds[fact.id] !== undefined && kinds[fact.id] !== "value") continue;
      const key = `${fact.value}|${fact.percent}`;
      byValue.set(key, [...(byValue.get(key) ?? []), fact]);
    }
    for (const target of targets.filter((item) => item.doc === side)) {
      const fact = mine.get(target.id);
      if (!fact || significantDigits(fact.value) < minDigits) continue;
      for (const hit of byValue.get(`${fact.value}|${fact.percent}`) ?? []) if (hit.where.page !== null) out.push({ target: targetKey(target), other: `${other}:${hit.id}`, page: hit.where.page, by: "value" });
    }
  }
  return out;
}

/** 라벨 글(`제목 · 계열 · 범주`)을 이름들과 범주로 가른다. 조각이 하나뿐이면 범주가 없다. */
export function labelParts(name: string | null): { names: string[]; category: string | null } {
  const parts = (name ?? "").split(" · ").map(squash).filter((part) => part !== "");
  if (parts.length < 2) return { names: parts, category: null };
  return { names: parts.slice(0, -1), category: parts[parts.length - 1] as string };
}

/**
 * 값을 보지 않는 지목: 대상의 라벨에서 이름(제목·계열)과 범주를 떼어, 상대 문서의 표의 숫자 칸 가운데
 * 한쪽 머리글(행 또는 열)이 이름을 품고 다른 쪽 머리글이 범주를 품는 칸이 있으면 그 쪽을 지목한다.
 */
function nameSignals(targets: Target[], docs: Record<Side, AskDoc>): Signal[] {
  const out: Signal[] = [];
  const holds = (headers: string[], word: string): boolean => headers.some((header) => header === word || (word.length >= MIN_NAME_CHARS && header.includes(word)));
  for (const side of ["a", "b"] as const) {
    const other = otherOf(side);
    const cells = docs[other].extraction.facts.filter((fact) => fact.bucket === "table_cell" && fact.headers !== null && fact.where.page !== null).map((fact) => ({ fact, row: (fact.headers?.row ?? []).map((header) => squash(header.text)), col: (fact.headers?.col ?? []).map((header) => squash(header.text)) }));
    for (const target of targets.filter((item) => item.doc === side)) {
      const { names, category } = labelParts(target.name);
      if (category === null) continue;
      for (const cell of cells) {
        const hit = names.some((name) => (holds(cell.row, name) && holds(cell.col, category)) || (holds(cell.col, name) && holds(cell.row, category)));
        if (hit) out.push({ target: targetKey(target), other: `${other}:${cell.fact.id}`, page: cell.fact.where.page as number, by: "names" });
      }
    }
  }
  return out;
}

/** 도표 대상이 든 쪽마다 상대 문서의 쪽을 지목한다. 신호가 하나도 없는 쪽은 결과에 없다 (묻지 않는다). */
export function nominate(targets: Target[], docs: Record<Side, AskDoc>, mode: NominateMode, minDigits: number = MIN_SIGNAL_DIGITS): Nomination[] {
  const chart = targets.filter((target) => target.branch === "chart");
  const signals = [...(mode !== "names" ? valueSignals(chart, docs, minDigits) : []), ...(mode !== "value" ? nameSignals(chart, docs) : [])];
  const pageOf = new Map(chart.map((target) => [targetKey(target), target.page]));
  const bag = new Map<string, Nomination>();
  for (const signal of signals) {
    const source = signal.target.slice(0, 1) as Side;
    const page = pageOf.get(signal.target) as number;
    const key = `${source}:${page}`;
    const entry = bag.get(key) ?? { source, page, otherPages: [], signals: [] };
    if (!entry.otherPages.includes(signal.page)) entry.otherPages.push(signal.page);
    entry.signals.push(signal);
    bag.set(key, entry);
  }
  return [...bag.values()].map((entry) => ({ ...entry, otherPages: entry.otherPages.sort((x, y) => x - y) })).sort((x, y) => x.source.localeCompare(y.source) || x.page - y.page);
}

export interface FocusPlan extends AskPlan {
  /** 상대 문서에서 싣는 쪽. */
  otherPages: number[];
}

/**
 * 지목마다 요청 하나: 대상은 그 쪽의 도표 값 전부.
 * `splitByName` 이면 한 쪽의 대상을 라벨의 이름(제목·계열)이 같은 것끼리 나눠 요청을 여럿 만든다 (쪽의 글과 상대 쪽은 요청마다 다시 싣는다). 대상의 합은 같다.
 */
export function planFocus(targets: Target[], nominations: Nomination[], splitByName = false): FocusPlan[] {
  const plans: FocusPlan[] = [];
  for (const nomination of nominations) {
    const onPage = targets.filter((target) => target.branch === "chart" && target.doc === nomination.source && target.page === nomination.page);
    const groups = new Map<string, Target[]>();
    for (const target of onPage) {
      const key = splitByName ? labelParts(target.name).names.join(" · ") : "";
      groups.set(key, [...(groups.get(key) ?? []), target]);
    }
    [...groups.values()].forEach((group, index) =>
      plans.push({ id: `f/${nomination.source}/p${nomination.page}${splitByName ? `/g${index + 1}` : ""}`, branch: "chart" as const, source: nomination.source, other: otherOf(nomination.source), pages: [nomination.page], otherPages: nomination.otherPages, targets: group }),
    );
  }
  return plans;
}

/** 요청 하나의 글. 방법 Q 의 조립과 확인(`buildAskRequest`)을 그대로 타고, 상대 문서 자리에 지목된 쪽만 싣는다. */
export function buildFocusRequest(plan: FocusPlan, docs: Record<Side, AskDoc>, chartLabels: Record<string, string>): AskRequest {
  return buildAskRequest(plan, docs, chartLabels, renderSourcePages(docs[plan.other], plan.other, plan.otherPages, chartLabels));
}

/** 「짝을 전부 적어라」(방법 D2 의 프롬프트) 꼴로 물을 때의 문서 글: 문서 a 가 앞, 문서 b 가 뒤. */
export const pairDocuments = (request: AskRequest): string => (request.plan.source === "a" ? `${request.vars.source}\n\n${request.vars.other}` : `${request.vars.other}\n\n${request.vars.source}`);

/** 상대 자리마다 근거의 세기(`basis`)를 같이 받는 프롬프트. 「없음」과 「분명함」 사이를 따로 받는다. */
export const PROMPT_F2 = "reconcile/ask-places@2";
export const BASES = ["clear", "likely"] as const;
export type Basis = (typeof BASES)[number];

export const askPlaces2Schema = z.object({
  answers: z.array(
    z.object({
      target: z.string(),
      counterparts: z.array(z.object({ id: z.string(), unit: z.string().nullable(), basis: z.enum(BASES) })),
      reason: z.enum(ASK_REASONS).nullable(),
      unit: z.string().nullable(),
    }),
  ),
});
export type AskPlaces2Answer = z.infer<typeof askPlaces2Schema>;

/** `basis` 를 떼어 방법 Q 의 답 꼴로 돌리고, 짝(`대상|상대`)마다의 `basis` 를 따로 낸다. 한 짝에 엇갈려 적혔으면 약한 쪽(`likely`)을 남긴다. */
export function splitBasis(answer: AskPlaces2Answer): { answer: AskPlacesAnswer; basis: Map<string, Basis> } {
  const basis = new Map<string, Basis>();
  for (const item of answer.answers) for (const counterpart of item.counterparts) basis.set(`${item.target}|${counterpart.id}`, basis.get(`${item.target}|${counterpart.id}`) === "likely" ? "likely" : counterpart.basis);
  return { answer: { answers: answer.answers.map((item) => ({ ...item, counterparts: item.counterparts.map(({ id, unit }) => ({ id, unit })) })) }, basis };
}

/** D2 꼴의 답(자리의 짝)을 대상마다의 상태로 옮긴다. 받아들인 짝에 나온 대상만 「상대를 답함」이고 나머지는 「답 없음」이다. */
export function statesFromPairs(plan: FocusPlan, judgement: PlaceJudgement): { states: Map<string, AskState>; verdicts: Verdict[] } {
  const states = new Map<string, AskState>(plan.targets.map((target) => [targetKey(target), { state: "unanswered" }]));
  const verdicts: Verdict[] = [];
  for (const verdict of judgement.verdicts) {
    const [mine, theirs] = plan.source === "a" ? [`a:${verdict.a}`, `b:${verdict.b}`] : [`b:${verdict.b}`, `a:${verdict.a}`];
    const state = states.get(mine);
    if (!state) continue;
    verdicts.push(verdict);
    states.set(mine, { state: "paired", counterparts: [...(state.state === "paired" ? state.counterparts : []), theirs] });
  }
  return { states, verdicts };
}

export interface FocusTally {
  /** 도표 대상 전체. */
  targets: number;
  /** 지목되지 않은 쪽의 대상 (묻지 않았다). */
  notNominated: number;
  asked: number;
  paired: number;
  none: Record<string, number>;
  unanswered: number;
  answerRejected: number;
}

/** 모든 도표 대상이 결과 하나에 든다. 합이 대상 수와 다르면 던진다. */
export function tallyFocus(targets: Target[], plans: FocusPlan[], states: Map<string, AskState>): FocusTally {
  const chart = targets.filter((target) => target.branch === "chart");
  const asked = new Set(plans.flatMap((plan) => plan.targets.map(targetKey)));
  const tally: FocusTally = { targets: chart.length, notNominated: 0, asked: asked.size, paired: 0, none: {}, unanswered: 0, answerRejected: 0 };
  for (const target of chart) {
    const key = targetKey(target);
    if (!asked.has(key)) {
      tally.notNominated += 1;
      continue;
    }
    const state = states.get(key) ?? { state: "unanswered" as const };
    if (state.state === "paired") tally.paired += 1;
    else if (state.state === "none") tally.none[state.reason] = (tally.none[state.reason] ?? 0) + 1;
    else if (state.state === "unanswered") tally.unanswered += 1;
    else tally.answerRejected += 1;
  }
  const sum = tally.notNominated + tally.paired + Object.values(tally.none).reduce((total, count) => total + count, 0) + tally.unanswered + tally.answerRejected;
  if (sum !== tally.targets) throw new Error(`도표 대상 ${tally.targets}개와 갈래의 합 ${sum}개가 다르다`);
  return tally;
}

// ── 채점 (정답을 받는 쪽. 실행은 정답을 읽지 않는다) ─────────────────────────

export interface Expected {
  name: string;
  /** 도표의 값 자리 (`문서:ID`). */
  target: string;
  /** 같은 항목인 상대 자리 (`문서:ID`). 하나라도 답하면 옳은 짝이다. */
  right: string[];
  /** 그 자리에 심은 오류의 ID. 없으면 null. */
  plant: string | null;
}

export interface FocusGrade {
  rows: { name: string; target: string; plant: string | null; state: string; rightPaired: boolean; outcomes: string[]; wrongCounterparts: string[] }[];
  /** 옳은 상대와 짝지어진 것 / 기대한 자리. */
  rightPaired: number;
  expected: number;
  /** 손대지 않은 자리 가운데 옳은 상대와 「일치」. */
  cleanMatch: number;
  clean: number;
  /** 심은 자리 가운데 옳은 상대와 「불일치」(잡음), 「일치」(거짓 일치). */
  plantedCaught: number;
  plantedFalseMatch: number;
  planted: number;
  /** 기대한 자리 밖에서 상대를 답한 대상과 그 짝 (원문으로 판정할 것). */
  outside: { target: string; counterpart: string; outcome: string }[];
}

/** 기대한 자리마다 답을 센다. 「옳은 짝」은 받아들인 짝 가운데 `right` 에 든 상대가 있는 것이다. */
export function gradeFocus(expected: Expected[], states: Map<string, AskState>, verdicts: Verdict[]): FocusGrade {
  const outcomeOf = new Map(verdicts.map((verdict) => [`a:${verdict.a}|b:${verdict.b}`, verdict.outcome]));
  const pairKey = (target: string, counterpart: string): string => (target.startsWith("a:") ? `${target}|${counterpart}` : `${counterpart}|${target}`);
  const grade: FocusGrade = { rows: [], rightPaired: 0, expected: expected.length, cleanMatch: 0, clean: 0, plantedCaught: 0, plantedFalseMatch: 0, planted: 0, outside: [] };
  const wanted = new Map(expected.map((item) => [item.target, item]));
  for (const item of expected) {
    const state = states.get(item.target) ?? { state: "not_asked" as const };
    const counterparts = state.state === "paired" ? state.counterparts : [];
    const right = counterparts.filter((id) => item.right.includes(id));
    const outcomes = right.map((id) => outcomeOf.get(pairKey(item.target, id)) ?? "unknown");
    const row = { name: item.name, target: item.target, plant: item.plant, state: state.state === "none" ? `none:${state.reason}` : state.state, rightPaired: right.length > 0, outcomes, wrongCounterparts: counterparts.filter((id) => !item.right.includes(id)) };
    grade.rows.push(row);
    if (row.rightPaired) grade.rightPaired += 1;
    if (item.plant === null) {
      grade.clean += 1;
      if (row.rightPaired && outcomes.every((outcome) => outcome === "match")) grade.cleanMatch += 1;
    } else {
      grade.planted += 1;
      if (outcomes.includes("mismatch")) grade.plantedCaught += 1;
      else if (outcomes.includes("match")) grade.plantedFalseMatch += 1;
    }
    for (const id of row.wrongCounterparts) grade.outside.push({ target: item.target, counterpart: id, outcome: outcomeOf.get(pairKey(item.target, id)) ?? "unknown" });
  }
  for (const [target, state] of states) {
    if (wanted.has(target) || state.state !== "paired") continue;
    for (const id of state.counterparts) grade.outside.push({ target, counterpart: id, outcome: outcomeOf.get(pairKey(target, id)) ?? "unknown" });
  }
  return grade;
}
