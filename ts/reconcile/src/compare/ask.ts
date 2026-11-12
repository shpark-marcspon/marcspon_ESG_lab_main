import { z } from "zod";
import type { DocModel, Extraction, Fact } from "../domain/facts.js";
import { normalizeText, scanNumbers } from "../domain/notation.js";
import type { UnitTable } from "../domain/units.js";
import { TallyError } from "./hybrid.js";
import { compareCells, type DocContext, type Verdict } from "./methods.js";
import { chartLabelFor, pickNameBlocks, renderDocumentWithIds, unmaskedInIdDocument } from "./prose.js";
import { MASK, maskText } from "./render.js";

// 밴드 X (MARC-TICK-27): 방법 Q, 낱개 숫자를 자리별로 묻는다.
// 지금까지의 방법(D·D2·D3·D4)은 「두 문서를 통째로 주고 같은 항목인 자리를 빠짐없이 적어라」였다. Q 는 거꾸로다:
// 물을 자리(대상)의 목록을 코드가 쥐고, 대상마다 「상대 문서에 같은 항목의 자리가 있는가, 없으면 없다고」를 받는다.
//  - 대상은 세 갈래다: 요약 숫자(이름이 묶인 숫자뿐인 블록, 밴드 V 의 `pickNameBlocks`), 도표의 값(코드 라벨이 붙은 값 후보, 밴드 W),
//    문장 속 숫자(문장 속 값 후보 가운데 밴드 V 의 종류 옵션이 「값」으로 본 것).
//  - 요청의 앞부분은 상대 문서 전체(가림과 ID 표시는 D2 와 같다), 뒷부분은 대상이 든 쪽과 대상 목록이다.
//  - 답에는 값 필드가 없다. 답한 짝의 값은 코드(`compareValues`)가 견준다.
// 이 파일은 정답 쌍·심은 오류 명세·판정 기록을 읽지 않는다. 대상 목록과 요청은 문서 모델, 종류 옵션, 코드 라벨에서만 나온다.

export const PROMPT_Q = "reconcile/ask-places@1";

export const BRANCHES = ["summary", "chart", "sentence"] as const;
export type Branch = (typeof BRANCHES)[number];
/** 요청 글의 대상 목록에 적는 갈래 이름. 프롬프트가 이 세 낱말을 설명한다. */
export const BRANCH_NAME: Record<Branch, string> = { summary: "요약 숫자", chart: "도표의 값", sentence: "문장 속 숫자" };
type Side = "a" | "b";
const otherOf = (side: Side): Side => (side === "a" ? "b" : "a");

/** 한 요청에 싣는 대상의 최대 수. 칸 검수의 선례(150)보다 작게 잡았다: 대상마다 쪽 전체가 문맥으로 따라오고, 한 대상에 상대가 여럿일 수 있다. */
export const MAX_TARGETS_PER_REQUEST = 80;
/** 대상 하나에 대한 출력 토큰의 어림 (가정). 칸 검수의 실측은 칸당 58~76 토큰, 그림 라벨은 번호표당 약 240 토큰(추론 포함)이었다. */
export const ASK_ANSWER_TOKENS = 100;

const bump = (bag: Record<string, number>, name: string, by = 1): void => void (bag[name] = (bag[name] ?? 0) + by);
const squash = (text: string): string => text.normalize("NFKC").replace(/\s+/g, "");

// ── 대상 목록 ────────────────────────────────────────────────────────────────

export interface Target {
  doc: Side;
  /** 값 후보의 ID (문서 이름 없이). */
  id: string;
  branch: Branch;
  page: number;
  /** 대상 목록에 같이 적는 글: 요약 숫자는 묶인 이름, 도표의 값은 코드 라벨. 가린 글이다. 문장 속 숫자는 없다. */
  name: string | null;
}

export const targetKey = (target: { doc: string; id: string }): string => `${target.doc}:${target.id}`;

/** 이름·라벨 글을 대상 목록의 한 줄에 놓였을 때의 꼴로 가린다. 값 후보 꼴로 읽히는 숫자는 남기지 않는다. */
export function maskName(raw: string): string {
  let text = maskText(normalizeText(raw.replace(/[〔〕[\]|]/g, " ")));
  for (let round = 0; round < 3; round++) {
    const left = scanNumbers(text).filter((token) => token.kind === "value");
    if (left.length === 0) break;
    for (const token of left.sort((x, y) => y.start - x.start)) text = `${text.slice(0, token.start)}${MASK}${token.raw.endsWith("%") ? "%" : ""}${text.slice(token.end)}`;
  }
  return text;
}

export interface AskDoc {
  model: DocModel;
  /** 종류 옵션을 켠 뽑기 (`extractDocument(…, { kinds: true })`). 값 후보의 목록·ID 는 옵션과 무관하게 같다. */
  extraction: Extraction;
}

/**
 * 문서 하나의 대상 목록. 정답을 쓰지 않는다: 문서 모델(묶은 이름), 코드 라벨, 종류 옵션만 본다.
 *  - 요약 숫자: 이름 블록이 묶인 숫자뿐인 블록의 값 후보.
 *  - 도표의 값: 코드 라벨이 실리는 값 후보 (`chartLabelFor` 가 라벨을 내는 것).
 *  - 문장 속 숫자: 문장 속 값 후보 가운데 종류가 「값」인 것.
 * 한 값 후보는 한 갈래에만 든다 (위 순서로 먼저 걸리는 갈래). 차례는 쪽, 그 안에서는 값 후보의 차례.
 */
export function listTargets(doc: AskDoc, side: Side, chartLabels: Record<string, string>): Target[] {
  const kinds = doc.extraction.kinds;
  if (!kinds) throw new TallyError("대상 목록에는 종류 옵션을 켠 뽑기가 필요하다 (extractDocument 의 kinds)");
  const nameOf = pickNameBlocks(doc.model, doc.extraction).bound;
  const blockText = new Map(doc.model.blocks.map((block) => [block.id, block.text ?? ""]));
  const out: Target[] = [];
  for (const fact of doc.extraction.facts) {
    const page = fact.where.page ?? 0;
    const nameBlock = fact.bucket === "number_only_block" && fact.where.blockId !== null ? nameOf.get(fact.where.blockId) : undefined;
    const label = chartLabelFor(chartLabels, `${side}:${fact.id}`);
    if (nameBlock !== undefined) out.push({ doc: side, id: fact.id, branch: "summary", page, name: maskName(blockText.get(nameBlock) ?? "") });
    else if (label !== null) out.push({ doc: side, id: fact.id, branch: "chart", page, name: maskName(label) });
    else if (fact.bucket === "sentence" && kinds[fact.id] === "value") out.push({ doc: side, id: fact.id, branch: "sentence", page, name: null });
  }
  const order = new Map(out.map((target, index) => [target.id, index]));
  return out.sort((x, y) => x.page - y.page || (order.get(x.id) as number) - (order.get(y.id) as number));
}

// ── 묶기와 요청 조립 ─────────────────────────────────────────────────────────

export interface AskPlan {
  id: string;
  branch: Branch;
  /** 대상이 든 문서. */
  source: Side;
  /** 상대 문서 (요청의 앞부분에 통째로 싣는다). */
  other: Side;
  /** 문맥으로 싣는 쪽 (대상이 든 쪽). */
  pages: number[];
  targets: Target[];
}

/**
 * 대상을 요청으로 묶는다. 한 요청에는 한 갈래·한 문서의 대상만 싣는다. 쪽 차례로 채우다가 한도를 넘으면 새 요청으로 넘긴다 (쪽을 쪼개지 않는다).
 * 한 쪽의 대상이 한도보다 많으면 그 쪽만으로 요청을 여럿 만든다 (고르게 나눈다. 그 쪽의 문맥은 요청마다 다시 싣는다).
 * 요청의 차례는 상대 문서가 같은 것끼리 이어지게 한다 (앞부분이 같은 요청이 잇달아 나가야 캐시가 듣는다).
 */
export function planAsks(targets: Target[], max: number = MAX_TARGETS_PER_REQUEST): AskPlan[] {
  const plans: AskPlan[] = [];
  for (const source of ["b", "a"] as const) {
    for (const branch of BRANCHES) {
      const mine = targets.filter((target) => target.doc === source && target.branch === branch);
      const chunks: Target[][] = [];
      let current: Target[] = [];
      const flush = (): void => {
        if (current.length > 0) chunks.push(current);
        current = [];
      };
      for (const page of [...new Set(mine.map((target) => target.page))]) {
        const onPage = mine.filter((target) => target.page === page);
        if (onPage.length > max) {
          flush();
          const parts = Math.ceil(onPage.length / max);
          const size = Math.ceil(onPage.length / parts);
          for (let at = 0; at < onPage.length; at += size) chunks.push(onPage.slice(at, at + size));
          continue;
        }
        if (current.length + onPage.length > max) flush();
        current.push(...onPage);
      }
      flush();
      chunks.forEach((chunk, index) => plans.push({ id: `q/${branch}-${source}/${index + 1}`, branch, source, other: otherOf(source), pages: [...new Set(chunk.map((target) => target.page))], targets: chunk }));
    }
  }
  return plans;
}

/** 문서 하나를 방법 Q 의 꼴로 옮긴다: D2 와 같은 가림과 ID 표시에, 묶은 이름(밴드 V)과 코드 라벨(밴드 W)을 얹는다. */
const render = (doc: AskDoc, side: Side, chartLabels: Record<string, string>, pages?: { from: number; to: number }): string =>
  renderDocumentWithIds(doc.model, doc.extraction, { prefix: `${side}:`, bindNames: true, chartLabels, ...(pages ? { pages } : {}) });

/** 상대 문서 전체. 같은 상대 문서를 쓰는 요청은 이 글이 글자까지 같다. */
export const renderOther = (doc: AskDoc, side: Side, chartLabels: Record<string, string>): string => render(doc, side, chartLabels);

/** 대상이 든 쪽들 (가린 글). 문서 이름 줄은 한 번만 싣는다. */
export function renderSourcePages(doc: AskDoc, side: Side, pages: number[], chartLabels: Record<string, string>): string {
  const lines: string[] = [];
  for (const [index, page] of pages.entries()) lines.push(...render(doc, side, chartLabels, { from: page, to: page }).split("\n").slice(index === 0 ? 0 : 1));
  return lines.join("\n");
}

const TARGET_LINE = /^- ([ab]:p\d+-(?:b\d+|t\d+:r\d+c\d+)(?:#n\d+)?) \| (요약 숫자|도표의 값|문장 속 숫자)(?: \| (.+))?$/;

/** 대상 목록: 한 줄에 `- 대상 ID | 갈래 | 이름이나 라벨`. */
export const renderTargetList = (targets: Target[]): string => targets.map((target) => `- ${targetKey(target)} | ${BRANCH_NAME[target.branch]}${target.name === null || target.name === "" ? "" : ` | ${target.name}`}`).join("\n");

/** 대상 목록의 글에 남은 값 후보 숫자. 꼴이 맞지 않는 줄은 그 줄을 그대로 낸다. */
export function unmaskedInTargetList(text: string): string[] {
  const found: string[] = [];
  for (const line of text.split("\n")) {
    const match = TARGET_LINE.exec(line);
    if (!match) {
      found.push(line);
      continue;
    }
    const name = match[3] ?? "";
    for (const shape of [name, `[${name}: ${MASK}]`]) for (const token of scanNumbers(shape)) if (token.kind === "value") found.push(shape.slice(token.start, token.end));
  }
  return found;
}

export interface AskRequest {
  plan: AskPlan;
  /** 프롬프트의 변수: 상대 문서, 대상이 든 쪽, 대상 목록. */
  vars: { other: string; source: string; targets: string };
  /** 가리지 못한 값 후보 숫자. 하나라도 있으면 요청을 만들지 않는다. */
  unmasked: string[];
  /** 대상 가운데 문맥 글에서 그 자리를 찾을 수 없는 것 (ID 표시나 표의 그 칸이 없다). 하나라도 있으면 요청을 만들지 않는다. */
  missingInSource: string[];
}

/** 요청 하나의 글. 앞부분(상대 문서)은 같은 상대 문서의 요청끼리 글자까지 같다. */
export function buildAskRequest(plan: AskPlan, docs: Record<Side, AskDoc>, chartLabels: Record<string, string>, otherText?: string): AskRequest {
  const other = otherText ?? renderOther(docs[plan.other], plan.other, chartLabels);
  const source = renderSourcePages(docs[plan.source], plan.source, plan.pages, chartLabels);
  const targets = renderTargetList(plan.targets);
  const lines = new Set(source.split("\n"));
  const tableOpen = (grid: string): boolean => lines.has(`[표 ${grid}]`);
  const missingInSource = plan.targets
    .filter((target) => {
      const cell = /^(p\d+-t\d+):r(\d+)c(\d+)$/.exec(target.id);
      if (!cell) return !source.includes(`〔${targetKey(target)}〕`);
      return !tableOpen(cell[1] as string) || !source.split("\n").some((line) => line.startsWith(`r${cell[2]} | `) && new RegExp(`(?:\\| |^r\\d+ \\| )c${cell[3]}: `).test(line));
    })
    .map(targetKey);
  return { plan, vars: { other, source, targets }, unmasked: [...unmaskedInIdDocument(other), ...unmaskedInIdDocument(source), ...unmaskedInTargetList(targets)], missingInSource };
}

// ── 답 ───────────────────────────────────────────────────────────────────────

/** 상대가 없다는 답의 사유. not_in_other_document: 상대 문서에 그 항목의 값이 없다. undecidable: 주어진 글로는 가를 수 없다. */
export const ASK_REASONS = ["not_in_other_document", "undecidable"] as const;
export type AskReason = (typeof ASK_REASONS)[number];

export const askPlacesSchema = z.object({
  answers: z.array(
    z.object({
      target: z.string(),
      counterparts: z.array(z.object({ id: z.string(), unit: z.string().nullable() })),
      reason: z.enum(ASK_REASONS).nullable(),
      unit: z.string().nullable(),
    }),
  ),
});
export type AskPlacesAnswer = z.infer<typeof askPlacesSchema>;

export type AskState = { state: "paired"; counterparts: string[] } | { state: "none"; reason: AskReason } | { state: "unanswered" } | { state: "answer_rejected"; why: string };

/** 답한 짝 하나를 견준 결과. `from` 은 이 짝을 답한 대상 (양쪽에서 같은 짝이 나오면 둘). */
export type AskVerdict = Verdict & { from: string[] };

export interface AskJudgement {
  plan: AskPlan;
  /** 대상(`문서:ID`)마다의 답. */
  states: Map<string, AskState>;
  verdicts: AskVerdict[];
  rejected: Record<string, number>;
}

const unitOk = (unit: string | null, doc: DocContext, fact: Fact): boolean => unit === null || squash(doc.pages.get(fact.where.page ?? -1) ?? "").includes(squash(unit)) || squash(fact.unitSuffix ?? "") === squash(unit);

/**
 * 방법 Q 의 답 확인과 비교. 버리는 갈래:
 *  - 그 요청의 대상 목록에 없는 자리에 대한 답 (`not_a_target`. 문서에 없는 ID 면 `unknown_id`).
 *  - 한 대상에 엇갈린 답 (`conflicting_answers`), 같은 답이 두 번 (`duplicate_answer`: 한 번만 받는다).
 *  - 상대도 사유도 없는 답 (`neither_counterpart_nor_reason`).
 *  - 대상의 단위 글자가 그 쪽에 없다 (`unit_not_in_source`: 답 전체를 버린다).
 *  - 상대 자리마다: 문서에 없는 ID (`unknown_id`), 대상과 같은 문서의 자리 (`same_document`), 단위 글자가 그 쪽에 없다 (`unit_not_in_source`), 같은 상대가 두 번 (`duplicate_counterpart`).
 *    받아들인 상대가 하나도 남지 않으면 그 대상의 답은 「버려진 답」이다. 하나라도 남으면 남은 것으로 「상대를 답함」.
 * 상대를 적고 사유도 적은 답은 상대를 받는다 (칸 검수와 같다). 답이 안 온 대상은 「답 없음」이다. 한 대상에 상대가 여럿이면 전부 견준다.
 */
export function judgeAsk(answer: AskPlacesAnswer | null, plan: AskPlan, a: DocContext, b: DocContext, units: UnitTable): AskJudgement {
  const out: AskJudgement = { plan, states: new Map(plan.targets.map((target) => [targetKey(target), { state: "unanswered" }])), verdicts: [], rejected: {} };
  if (!answer) return out;
  const facts = { a: new Map(a.extraction.facts.map((fact) => [fact.id, fact])), b: new Map(b.extraction.facts.map((fact) => [fact.id, fact])) };
  const docs = { a, b };
  const parse = (id: string): { side: Side; fact: Fact } | null => {
    const side = id.startsWith("a:") ? "a" : id.startsWith("b:") ? "b" : null;
    const fact = side ? facts[side].get(id.slice(2)) : undefined;
    return side && fact ? { side, fact } : null;
  };
  const asked = new Set(plan.targets.map(targetKey));
  const answers = new Map<string, AskPlacesAnswer["answers"]>();
  for (const raw of answer.answers) {
    if (!asked.has(raw.target)) bump(out.rejected, parse(raw.target) ? "not_a_target" : "unknown_id");
    else answers.set(raw.target, [...(answers.get(raw.target) ?? []), raw]);
  }
  const found = new Map<string, AskVerdict>();
  for (const [target, list] of answers) {
    const reject = (why: string, count = 1): void => {
      bump(out.rejected, why, count);
      out.states.set(target, { state: "answer_rejected", why });
    };
    if (new Set(list.map((item) => JSON.stringify(item))).size > 1) {
      reject("conflicting_answers", list.length);
      continue;
    }
    if (list.length > 1) bump(out.rejected, "duplicate_answer", list.length - 1);
    const raw = list[0] as AskPlacesAnswer["answers"][number];
    const self = parse(target) as { side: Side; fact: Fact };
    if (raw.counterparts.length === 0) {
      if (raw.reason === null) reject("neither_counterpart_nor_reason");
      else out.states.set(target, { state: "none", reason: raw.reason });
      continue;
    }
    if (!unitOk(raw.unit, docs[self.side], self.fact)) {
      reject("unit_not_in_source");
      continue;
    }
    const accepted: string[] = [];
    for (const counterpart of raw.counterparts) {
      const other = parse(counterpart.id);
      if (!other) bump(out.rejected, "unknown_id");
      else if (other.side === self.side) bump(out.rejected, "same_document");
      else if (accepted.includes(counterpart.id)) bump(out.rejected, "duplicate_counterpart");
      else if (!unitOk(counterpart.unit, docs[other.side], other.fact)) bump(out.rejected, "unit_not_in_source");
      else {
        accepted.push(counterpart.id);
        const [fa, ua, fb, ub] = self.side === "a" ? [self.fact, raw.unit, other.fact, counterpart.unit] : [other.fact, counterpart.unit, self.fact, raw.unit];
        const key = `${fa.id}|${fb.id}`;
        const result = compareCells({ fact: fa, unit: ua }, { fact: fb, unit: ub }, units);
        if (!found.has(key)) found.set(key, { a: fa.id, b: fb.id, outcome: result.outcome as Verdict["outcome"], reason: result.reason, valuesDiffer: result.valuesDiffer, from: [target] });
      }
    }
    out.states.set(target, accepted.length > 0 ? { state: "paired", counterparts: accepted } : { state: "answer_rejected", why: "no_valid_counterpart" });
  }
  out.verdicts = [...found.values()];
  return out;
}

const SEVERITY: Record<Verdict["outcome"], number> = { mismatch: 3, needs_explanation: 2, match: 1 };

export interface AskOutcome {
  states: Map<string, AskState>;
  /** 답한 짝 (같은 짝은 하나로. 두 대상이 같은 짝을 답했고 단위가 달라 결과가 다르면 무거운 쪽을 남긴다). */
  verdicts: AskVerdict[];
  rejected: Record<string, number>;
}

/** 요청들의 답을 하나로 모은다. 한 대상은 한 요청에만 실려야 한다. */
export function combineAsk(judgements: AskJudgement[]): AskOutcome {
  const states = new Map<string, AskState>();
  const pairs = new Map<string, AskVerdict>();
  const rejected: Record<string, number> = {};
  for (const judgement of judgements) {
    for (const [target, state] of judgement.states) {
      if (states.has(target)) throw new TallyError(`대상 ${target} 이 두 요청에 실렸다`);
      states.set(target, state);
    }
    for (const verdict of judgement.verdicts) {
      const key = `${verdict.a}|${verdict.b}`;
      const was = pairs.get(key);
      if (!was) pairs.set(key, { ...verdict, from: [...verdict.from] });
      else pairs.set(key, { ...(SEVERITY[verdict.outcome] > SEVERITY[was.outcome] ? verdict : was), from: [...was.from, ...verdict.from] });
    }
    for (const [reason, count] of Object.entries(judgement.rejected)) bump(rejected, reason, count);
  }
  return { states, verdicts: [...pairs.values()], rejected };
}

// ── 셈 ───────────────────────────────────────────────────────────────────────

export interface BranchTally {
  total: number;
  /** 상대 자리를 하나 이상 답했고 받아들여졌다. */
  paired: number;
  /** 상대가 없다고 답했다 (사유별). */
  none: Record<AskReason, number>;
  /** 답이 오지 않았다. */
  unanswered: number;
  /** 답이 왔지만 버려졌다. */
  rejected: number;
}

export type AskTally = Record<Branch, BranchTally> & { total: number };

/** 모든 대상이 결과 하나에 든다. 갈래마다의 합이 대상 수와 다르거나, 대상이 아닌 자리의 답이 섞여 있으면 예외다. */
export function tallyAsk(targets: Target[], states: Map<string, AskState>): AskTally {
  const empty = (): BranchTally => ({ total: 0, paired: 0, none: { not_in_other_document: 0, undecidable: 0 }, unanswered: 0, rejected: 0 });
  const tally: AskTally = { total: targets.length, summary: empty(), chart: empty(), sentence: empty() };
  const keys = new Set(targets.map(targetKey));
  if (keys.size !== targets.length) throw new TallyError("대상 목록에 같은 자리가 두 번 있다");
  for (const key of states.keys()) if (!keys.has(key)) throw new TallyError(`대상이 아닌 자리 ${key} 의 답이 셈에 섞였다`);
  for (const target of targets) {
    const bag = tally[target.branch];
    const state = states.get(targetKey(target));
    if (!state) throw new TallyError(`대상 ${targetKey(target)} 에 결과가 없다`);
    bag.total += 1;
    if (state.state === "paired") bag.paired += 1;
    else if (state.state === "none") bag.none[state.reason] += 1;
    else if (state.state === "unanswered") bag.unanswered += 1;
    else bag.rejected += 1;
  }
  let sum = 0;
  for (const branch of BRANCHES) {
    const bag = tally[branch];
    const inside = bag.paired + bag.none.not_in_other_document + bag.none.undecidable + bag.unanswered + bag.rejected;
    if (inside !== bag.total) throw new TallyError(`${BRANCH_NAME[branch]}: 대상 ${bag.total}개 중 결과가 붙은 것이 ${inside}개다. 합이 맞지 않아 수를 내지 않는다`);
    sum += inside;
  }
  if (sum !== tally.total) throw new TallyError(`대상 ${tally.total}개 중 결과가 붙은 것이 ${sum}개다. 합이 맞지 않아 수를 내지 않는다`);
  return tally;
}

// ── 흔들림 ───────────────────────────────────────────────────────────────────

/** 대상 하나의 답을 견줄 글자. 엄격: 상대의 집합과 「없음」의 사유까지. 느슨: 갈래(상대를 답함 / 없음 / 답 없음 / 버려진 답)만. */
export function signature(state: AskState, loose = false): string {
  if (state.state === "paired") return loose ? "paired" : `paired:${[...state.counterparts].sort().join(",")}`;
  if (state.state === "none") return loose ? "none" : `none:${state.reason}`;
  return state.state === "unanswered" ? "unanswered" : "rejected";
}

export interface Stability {
  total: number;
  /** 모든 실행이 같은 답. */
  allSame: number;
  /** 과반이 같은 답 (전부 같은 것은 빼고). */
  majority: number;
  /** 과반이 없다. */
  split: number;
}

/** 실행들이 대상마다 같은 답을 냈는가 (갈래별). 실행이 하나면 전부 「같다」다. */
export function stability(targets: Target[], runs: Map<string, AskState>[], loose = false): Record<Branch, Stability> {
  const out: Record<Branch, Stability> = { summary: { total: 0, allSame: 0, majority: 0, split: 0 }, chart: { total: 0, allSame: 0, majority: 0, split: 0 }, sentence: { total: 0, allSame: 0, majority: 0, split: 0 } };
  for (const target of targets) {
    const bag = out[target.branch];
    const counts: Record<string, number> = {};
    for (const run of runs) {
      const state = run.get(targetKey(target));
      if (!state) throw new TallyError(`대상 ${targetKey(target)} 에 결과가 없는 실행이 있다`);
      bump(counts, signature(state, loose));
    }
    const top = Math.max(...Object.values(counts));
    bag.total += 1;
    if (top === runs.length) bag.allSame += 1;
    else if (top * 2 > runs.length) bag.majority += 1;
    else bag.split += 1;
  }
  return out;
}
