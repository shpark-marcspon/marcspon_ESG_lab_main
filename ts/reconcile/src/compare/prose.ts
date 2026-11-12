import { z } from "zod";
import type { DocModel, Extraction, Fact } from "../domain/facts.js";
import { normalizeText, scanNumbers } from "../domain/notation.js";
import type { UnitTable } from "../domain/units.js";
import { TallyError } from "./hybrid.js";
import { compareCells, type DocContext, type MethodOutcome, type Verdict } from "./methods.js";
import { MASK, maskText } from "./render.js";
import { classifyBefore, deriveTablePairs, planCellReviews, tallyReview, type CellReviewPlan, type DerivedTablePair, type ReviewState, type ReviewTally } from "./review.js";

// 밴드 U (MARC-TICK-24): 방법 H 의 뼈대를 서술 속 숫자와 같은 문서 안으로 넓힌다.
//  - D2: 방법 D 와 같되 가린 자리마다 ID 를 보여 준다. 표의 숫자 칸은 칸 ID(행·열로 읽는다), 문장 속·표의 글 칸 속 값 후보는 `▒` 뒤에 `〔ID〕`.
//  - H2: D2 의 답 + 코드의 셈 + 칸 검수(새 버전: 상대는 그 요청의 남은 칸에서만). 표 검수는 없다.
//  - I: 문서 하나를 같은 방식으로 가려서 주고 「같은 항목을 말하는 자리들」의 묶음을 받는다. 묶음 안의 값은 코드가 서로 견준다.
// 요청은 문서와 앞 단계의 답에서만 조립한다 (이 파일은 정답과 심은 오류 명세를 읽지 않는다). 값 비교는 코드(`compareValues`)다. 답 스키마에 값 필드가 없다.
// 자리의 ID 는 값 후보의 ID 그대로다 (`블록 ID#n순번`, `표 ID:r행c열#n순번`). 순번은 그 글에서 숫자 덩어리가 나온 차례라 값의 글자가 들어 있지 않다.

export const PROMPT_D2 = "reconcile/pair-places@1";
/** 밴드 V 의 D3b: `@1` 에 「이름과 숫자가 따로 놓인 요약 숫자」 갈래와 「기간이 쪽의 제목·각주에 있을 수 있다」를 더한 것. */
export const PROMPT_D3B = "reconcile/pair-places@2";
export const PROMPT_REVIEW_CELLS_2 = "reconcile/review-cells@2";
export const PROMPT_I = "reconcile/group-places@1";

const OPEN = "〔";
const CLOSE = "〕";
/** 요청 글에 붙인 ID 표시의 꼴. 문서 이름(`a:`·`b:`)은 두 문서를 같이 싣는 요청(D2)에서만 붙는다. */
const ID_MARK = /〔(?:[ab]:)?p\d+-(?:b\d+|t\d+:r\d+c\d+)#n\d+〕/g;

const bump = (bag: Record<string, number>, name: string, by = 1): void => void (bag[name] = (bag[name] ?? 0) + by);
const squash = (text: string): string => text.normalize("NFKC").replace(/\s+/g, "");
const pairKey = (verdict: { a: string; b: string }): string => `${verdict.a}|${verdict.b}`;
const SEVERITY: Record<Verdict["outcome"], number> = { mismatch: 3, needs_explanation: 2, match: 1 };

// ── 가림과 ID 붙이기 ─────────────────────────────────────────────────────────

/**
 * 글 한 덩어리의 값 후보를 가린다. 값 후보(`facts`: 이 글에서 뽑힌 것)는 `▒〔ID〕` 로, 값 후보 꼴이지만 뽑히지 않은 숫자(쪽 번호, 범위의 끝 등)는 ID 없이 `▒` 로.
 * ID 속의 숫자는 앞에 글자(p, b, t, r, c, n)가 붙어 있어 값 후보 꼴로 읽히지 않는다.
 * `%`·`%p` 는 단위라 남긴다. 숫자 뽑기와 같은 글(원문 그대로)에서 자리를 읽는다.
 */
export function maskWithIds(raw: string, facts: Fact[], prefix: string): string {
  const byStart = new Map(facts.filter((fact) => fact.where.span).map((fact) => [fact.where.span!.start, fact]));
  let out = raw;
  for (const token of scanNumbers(raw).filter((item) => item.kind === "value").sort((x, y) => y.start - x.start)) {
    const fact = byStart.get(token.start);
    const percent = /%p$/.test(token.raw) ? "%p" : token.raw.endsWith("%") ? "%" : "";
    out = `${out.slice(0, token.start)}${MASK}${percent}${fact && fact.where.span!.end === token.end ? `${OPEN}${prefix}${fact.id}${CLOSE}` : ""}${out.slice(token.end)}`;
  }
  // 글을 한 줄로 고른 뒤에야 값 후보 꼴로 읽히는 숫자(줄 머리의 목록 번호 등)는 방법 D 와 같이 ID 없이 가린다.
  return maskText(normalizeText(out));
}

export interface IdRenderOptions {
  /** ID 앞에 붙이는 문서 이름 (`a:`). 문서 하나만 싣는 요청이면 빈 글자. */
  prefix: string;
  /** 쪽 범위 (양 끝 포함). 없으면 전부. */
  pages?: { from: number; to: number };
  /** 숫자뿐인 블록을 코드가 고른 이름 블록과 한 줄로 싣는다 (`이름: ▒〔ID〕`, 밴드 V). 기본은 끔: 끄면 요청 글이 밴드 U 와 글자까지 같다. */
  bindNames?: boolean;
  /**
   * 도표의 값 후보에 붙일 라벨 (`문서:값 후보 ID` → `도표 제목 · 계열 · 범주`, 밴드 W). 라벨이 있는 값 후보는 `[라벨: ▒〔ID〕]` 꼴로 싣는다 (표의 숫자 칸은 `[라벨: ▒]`).
   * 값 후보의 ID 와 가림은 그대로다. 라벨 글도 같은 가림을 거친다. 기본은 없음: 없으면 요청 글이 밴드 U 와 글자까지 같다.
   */
  chartLabels?: Record<string, string>;
}

/** 라벨 글을 가려서 낸다. */
export function chartLabelFor(labels: Record<string, string> | undefined, id: string): string | null {
  const raw = labels?.[id];
  if (raw === undefined) return null;
  // 라벨이 놓이는 자리(대괄호 안, 쌍점 앞)에서 값 후보 꼴로 읽히는 숫자를 전부 가린다. 연도·식별자 꼴(2025, Scope 1)은 남는다.
  // 요청 글의 꼴을 깨뜨리는 글자(대괄호, 세로줄, ID 표시의 괄호)는 라벨에서 뺀다.
  let text = maskText(normalizeText(raw.replace(/[〔〕[\]|]/g, " ")));
  for (let round = 0; round < 3; round++) {
    const left = scanNumbers(`[${text}: ${MASK}]`).filter((token) => token.kind === "value");
    if (left.length === 0) break;
    for (const token of left.sort((x, y) => y.start - x.start)) text = `${text.slice(0, token.start - 1)}${MASK}${token.raw.endsWith("%") ? "%" : ""}${text.slice(token.end - 1)}`;
  }
  return text === "" || /[〔〕\[\]|]/.test(text) ? null : text;
}

/** 가린 글에서 라벨이 있는 값 후보의 `▒〔ID〕` 를 `[라벨: ▒〔ID〕]` 로 바꾼다. */
function withChartLabels(masked: string, facts: Fact[], prefix: string, labels: Record<string, string> | undefined): string {
  let out = masked;
  for (const fact of facts) {
    const label = chartLabelFor(labels, `${fact.docId}:${fact.id}`);
    if (label === null) continue;
    const mark = `${OPEN}${prefix}${fact.id}${CLOSE}`;
    const at = out.indexOf(mark);
    if (at === -1) continue;
    const head = /▒(?:%p|%)?$/.exec(out.slice(0, at));
    if (!head) continue;
    out = `${out.slice(0, head.index)}[${label}: ${head[0]}${mark}]${out.slice(at + mark.length)}`;
  }
  return out;
}

export interface NameBinding {
  /** 숫자뿐인 블록의 ID → 이름 블록의 ID. */
  bound: Map<string, string>;
  /** 이름 블록을 고르지 못한 숫자뿐인 블록. 묶지 않고 그대로 싣는다. */
  unbound: string[];
}

/**
 * 숫자뿐인 블록(이름 없이 숫자만 든 글 블록)마다 이름 블록을 고른다. 문서 모델의 순서·좌표·글자 크기(블록의 높이)만 본다. 글의 뜻은 보지 않는다.
 * 이름 블록의 조건: 같은 쪽의 글 블록이고(표가 아님), 값 후보가 들어 있지 않고(숫자뿐인 블록도 문장도 아님), 숫자 블록과 가로로 겹치고(좁은 쪽 너비의 절반 이상),
 * 숫자 블록의 바로 위에 있고(이름의 아래 끝과 숫자의 위 끝 사이가 이름의 높이 이하, 겹침은 2pt 까지), 숫자 블록보다 높지 않다(글자가 더 크지 않다).
 * 여럿이면 세로로 가장 가까운 것, 같으면 문서 순서로 가까운 것. 한 이름 블록은 한 숫자 블록에만 묶는다. 조건에 맞는 것이 없으면 묶지 않는다.
 */
export function pickNameBlocks(document: DocModel, extraction: Extraction): NameBinding {
  const withFacts = new Map<string, Fact[]>();
  for (const fact of extraction.facts) if (fact.where.blockId !== null) withFacts.set(fact.where.blockId, [...(withFacts.get(fact.where.blockId) ?? []), fact]);
  const numberBlocks = document.blocks.filter((block) => (withFacts.get(block.id) ?? []).some((fact) => fact.bucket === "number_only_block")).sort((x, y) => x.page - y.page || x.order - y.order);
  const out: NameBinding = { bound: new Map(), unbound: [] };
  const taken = new Set<string>();
  for (const block of numberBlocks) {
    const [x0 = 0, y0 = 0, x1 = 0, y1 = 0] = block.bbox;
    const candidates = document.blocks
      .filter((other) => other.page === block.page && other.kind !== "table" && other.id !== block.id && !taken.has(other.id) && !withFacts.has(other.id) && normalizeText(other.text ?? "") !== "")
      .map((other) => {
        const [ox0 = 0, oy0 = 0, ox1 = 0, oy1 = 0] = other.bbox;
        return { other, overlap: Math.min(x1, ox1) - Math.max(x0, ox0), narrow: Math.min(x1 - x0, ox1 - ox0), gap: y0 - oy1, height: oy1 - oy0 };
      })
      .filter((item) => item.overlap > 0 && item.overlap >= item.narrow / 2 && item.gap >= -2 && item.gap <= item.height && item.height <= y1 - y0)
      .sort((p, q) => p.gap - q.gap || Math.abs(p.other.order - block.order) - Math.abs(q.other.order - block.order));
    const name = candidates[0]?.other;
    if (!name) out.unbound.push(block.id);
    else {
      out.bound.set(block.id, name.id);
      taken.add(name.id);
    }
  }
  return out;
}

/** 문서를 방법 D 와 같은 꼴로 옮기되(값 후보는 전부 가림), 표의 숫자 칸이 아닌 값 후보에는 ID 를 붙인다. */
export function renderDocumentWithIds(document: DocModel, extraction: Extraction, options: IdRenderOptions): string {
  const grids = new Map(extraction.grids.map((grid) => [grid.id, grid]));
  const cells = new Map(extraction.facts.filter((fact) => fact.bucket === "table_cell").map((fact) => [fact.id, fact]));
  const inBlock = new Map<string, Fact[]>();
  const inCell = new Map<string, Fact[]>();
  for (const fact of extraction.facts) {
    if (fact.bucket === "table_cell") continue;
    const [bag, key] = fact.where.gridId !== null ? [inCell, `${fact.where.gridId}:r${fact.where.row}c${fact.where.col}`] : [inBlock, fact.where.blockId as string];
    bag.set(key, [...(bag.get(key) ?? []), fact]);
  }
  const lines: string[] = [`# 문서 ${extraction.docId} (${extraction.fileName})`];
  const pages = [...new Set(document.blocks.map((block) => block.page))].sort((x, y) => x - y).filter((page) => !options.pages || (page >= options.pages.from && page <= options.pages.to));
  // 한 줄 묶기: 이름 블록의 자리에 `이름: 가린 숫자` 를 싣고, 숫자 블록의 제 줄은 싣지 않는다. 이름도 숫자도 같은 가림(`maskWithIds`)을 거친다.
  const binding = options.bindNames ? pickNameBlocks(document, extraction).bound : new Map<string, string>();
  const numberOf = new Map([...binding].map(([number, name]) => [name, number]));
  const byId = new Map(document.blocks.map((block) => [block.id, block]));
  for (const page of pages) {
    lines.push(`## ${page}쪽`);
    for (const block of document.blocks.filter((item) => item.page === page).sort((x, y) => x.order - y.order)) {
      if (block.kind !== "table") {
        if (binding.has(block.id)) continue;
        const text = withChartLabels(maskWithIds(block.text ?? "", inBlock.get(block.id) ?? [], options.prefix), inBlock.get(block.id) ?? [], options.prefix, options.chartLabels);
        const number = numberOf.get(block.id);
        if (number !== undefined) lines.push(`${text}: ${maskWithIds(byId.get(number)?.text ?? "", inBlock.get(number) ?? [], options.prefix)}`);
        else if (text !== "") lines.push(text);
        continue;
      }
      const grid = grids.get(block.table_id as string);
      if (!grid) {
        lines.push(`[표 ${block.table_id}: 격자를 세우지 못해 싣지 못함]`);
        continue;
      }
      lines.push(`[표 ${grid.id}]`);
      grid.cells.forEach((row, r) => {
        const parts = row
          .map((raw, c) => {
            const cell = cells.get(`${grid.id}:r${r}c${c}`);
            if (cell) {
              const label = chartLabelFor(options.chartLabels, `${cell.docId}:${cell.id}`);
              const shown = `${MASK}${cell.unitSuffix ?? ""}`;
              return [c, label === null ? shown : `[${label}: ${shown}]`] as const;
            }
            const inside = inCell.get(`${grid.id}:r${r}c${c}`) ?? [];
            return [c, withChartLabels(maskWithIds(raw, inside, options.prefix), inside, options.prefix, options.chartLabels)] as const;
          })
          .filter(([, text]) => text !== "");
        if (parts.length > 0) lines.push(`r${r} | ${parts.map(([c, text]) => `c${c}: ${text}`).join(" | ")}`);
      });
      lines.push("[표 끝]");
    }
  }
  return lines.join("\n");
}

/**
 * 요청 글에 남은 값 후보 숫자. ID 표시(`〔…〕`)와 요청 조립이 넣은 줄(문서 이름, 쪽, 표 ID)은 떼고, 표의 행은 칸마다 본다.
 * ID 표시의 꼴이 아닌 `〔` 가 남아 있으면 그 줄을 그대로 낸다 (ID 에 다른 글자가 섞였다는 뜻이다).
 */
export function unmaskedInIdDocument(text: string): string[] {
  const found: string[] = [];
  for (const line of text.split("\n")) {
    if (/^# 문서 [ab] \(.*\)$/.test(line) || /^## \d+쪽$/.test(line) || /^\[표 [^\]]+\]$/.test(line) || /^\[표 \S+: 격자를 세우지 못해 싣지 못함\]$/.test(line)) continue;
    const bare = line.replace(ID_MARK, "");
    if (bare.includes(`${MASK}${OPEN}`) || bare.includes(`%${OPEN}`)) found.push(line);
    const parts = /^r\d+ \| /.test(bare) ? bare.replace(/^r\d+ \| /, "").split(" | ").map((part) => part.replace(/^c\d+: /, "")) : [bare];
    for (const part of parts) for (const token of scanNumbers(part)) if (token.kind === "value") found.push(part.slice(token.start, token.end));
  }
  return found;
}

/** 요청 글에 실린 ID 표시의 수 (문서 이름을 뗀 ID 로). */
export function idsInText(text: string): string[] {
  return [...text.matchAll(ID_MARK)].map((match) => match[0].slice(1, -1).replace(/^[ab]:/, ""));
}

// ── D2: 서술을 넣은 자리 짝 ──────────────────────────────────────────────────

export const pairPlacesSchema = z.object({
  pairs: z.array(z.object({ a: z.string(), b: z.string(), unit_a: z.string().nullable(), unit_b: z.string().nullable() })),
});
export type PairPlacesAnswer = z.infer<typeof pairPlacesSchema>;

export interface PlaceJudgement {
  verdicts: Verdict[];
  rejected: Record<string, number>;
  /** 버려진 답이 가리킨 자리 가운데 그 문서에 실제로 있는 값 후보. */
  rejectedPlaces: { a: Set<string>; b: Set<string> };
}

const allFacts = (extraction: Extraction): Map<string, Fact> => new Map(extraction.facts.map((fact) => [fact.id, fact]));
const unitOk = (unit: string | null, doc: DocContext, fact: Fact): boolean => unit === null || squash(doc.pages.get(fact.where.page ?? -1) ?? "").includes(squash(unit)) || squash(fact.unitSuffix ?? "") === squash(unit);

/**
 * D2 의 답 확인과 비교. ID 는 문서 이름을 붙인 꼴(`a:…`, `b:…`)이어야 하고 그 문서의 값 후보(표의 숫자 칸이든 글 속 숫자든)여야 한다(`unknown_id`).
 * 같은 문서의 두 자리를 짝지은 답은 버린다(`same_document`). `a`·`b` 자리가 서로 바뀌어 적힌 답은 바로잡아 받는다.
 * 단위 글자는 그 쪽에 있어야 한다(`unit_not_in_source`, 방법 D 와 같다). 같은 짝은 한 번만 센다. 남은 짝은 코드가 값을 견준다.
 */
export function judgePlacePairs(answers: PairPlacesAnswer[], a: DocContext, b: DocContext, units: UnitTable): PlaceJudgement {
  const out: PlaceJudgement = { verdicts: [], rejected: {}, rejectedPlaces: { a: new Set(), b: new Set() } };
  const facts = { a: allFacts(a.extraction), b: allFacts(b.extraction) };
  const docs = { a, b };
  const parse = (id: string): { side: "a" | "b"; fact: Fact } | null => {
    const side = id.startsWith("a:") ? "a" : id.startsWith("b:") ? "b" : null;
    const fact = side ? facts[side].get(id.slice(2)) : undefined;
    return side && fact ? { side, fact } : null;
  };
  const done = new Set<string>();
  for (const answer of answers) {
    for (const raw of answer.pairs) {
      const first = parse(raw.a);
      const second = parse(raw.b);
      const reject = (reason: string): void => {
        bump(out.rejected, reason);
        for (const place of [first, second]) if (place) out.rejectedPlaces[place.side].add(place.fact.id);
      };
      if (!first || !second) {
        reject("unknown_id");
        continue;
      }
      if (first.side === second.side) {
        reject("same_document");
        continue;
      }
      const [pa, ua, pb, ub] = first.side === "a" ? [first, raw.unit_a, second, raw.unit_b] : [second, raw.unit_b, first, raw.unit_a];
      if (done.has(`${pa.fact.id}|${pb.fact.id}`)) {
        bump(out.rejected, "duplicate_pair");
        continue;
      }
      if (!unitOk(ua, docs.a, pa.fact) || !unitOk(ub, docs.b, pb.fact)) {
        reject("unit_not_in_source");
        continue;
      }
      done.add(`${pa.fact.id}|${pb.fact.id}`);
      const result = compareCells({ fact: pa.fact, unit: ua }, { fact: pb.fact, unit: ub }, units);
      out.verdicts.push({ a: pa.fact.id, b: pb.fact.id, outcome: result.outcome as Verdict["outcome"], reason: result.reason, valuesDiffer: result.valuesDiffer });
    }
  }
  // 다른 짝으로 견주어진 자리는 「버려진 답이 가리킨 자리」에서 뺀다.
  for (const verdict of out.verdicts) {
    out.rejectedPlaces.a.delete(verdict.a);
    out.rejectedPlaces.b.delete(verdict.b);
  }
  return out;
}

/** 표의 숫자 칸끼리의 짝만. 표 짝은 여기서만 끌어낸다 (서술 속 숫자는 표 짝에 속하지 않는다). */
export function cellVerdicts(verdicts: Verdict[], a: Extraction, b: Extraction): Verdict[] {
  const isCell = { a: new Set(a.facts.filter((fact) => fact.bucket === "table_cell").map((fact) => fact.id)), b: new Set(b.facts.filter((fact) => fact.bucket === "table_cell").map((fact) => fact.id)) };
  return verdicts.filter((verdict) => isCell.a.has(verdict.a) && isCell.b.has(verdict.b));
}

export interface PlaceTallies {
  tablePairs: DerivedTablePair[];
  states: { a: Map<string, ReviewState>; b: Map<string, ReviewState> };
  tally: { a: ReviewTally; b: ReviewTally };
}

/**
 * D2 의 답 뒤의 셈 (AI 없음). 방법 H 의 검수 전 셈과 같은 갈래를 쓴다: 견줌 / 표의 숫자 칸이 아님(= 짝이 안 나온 서술 숫자, 갈래별) /
 * 짝지어진 표 안의 남은 칸 / 짝이 없는 표의 칸인데 버려진 답이 가리킴 / 짝이 없는 표의 칸. 서술 속 숫자도 짝지어져 견주어졌으면 「견줌」이다.
 */
export function tallyPlaces(a: Extraction, b: Extraction, judgement: PlaceJudgement): PlaceTallies {
  const tablePairs = deriveTablePairs(cellVerdicts(judgement.verdicts, a, b));
  const states = {
    a: classifyBefore(a, "a", new Set(tablePairs.map((pair) => pair.a)), judgement.verdicts, judgement.rejectedPlaces.a),
    b: classifyBefore(b, "b", new Set(tablePairs.map((pair) => pair.b)), judgement.verdicts, judgement.rejectedPlaces.b),
  };
  return { tablePairs, states, tally: { a: tallyReview(a.facts.length, states.a.values()), b: tallyReview(b.facts.length, states.b.values()) } };
}

// ── H2: 칸 검수 (새 버전) ────────────────────────────────────────────────────

/** 남은 칸에 상대가 없다는 답의 사유. `already_paired` 는 같은 항목의 칸이 상대 표에 있지만 이미 짝지어져 있다는 뜻이다. */
export const REVIEW2_REASONS = ["no_such_row_or_column", "different_item", "already_paired", "undecidable"] as const;

export const reviewCells2Schema = z.object({
  answers: z.array(
    z.object({
      cell: z.string(),
      counterpart: z.string().nullable(),
      reason: z.enum(REVIEW2_REASONS).nullable(),
      unit: z.string().nullable(),
      counterpart_unit: z.string().nullable(),
    }),
  ),
});
export type ReviewCells2Answer = z.infer<typeof reviewCells2Schema>;

/**
 * 칸 검수의 요청 계획. 방법 H 와 같되 서술을 넣은 답에 맞춘다: 표 짝과 「이미 짝지어진 칸」은 표의 숫자 칸끼리의 짝에서만 끌어내고,
 * 서술 속 숫자와 견주어진 칸은 「견줌」이라 남은 칸에서 뺀다. 남은 칸이 없어진 요청은 만들지 않는다.
 */
export function planCellReviews2(a: Extraction, b: Extraction, verdicts: Verdict[], maxRemaining?: number): CellReviewPlan[] {
  const cells = cellVerdicts(verdicts, a, b);
  const compared = { a: new Set(verdicts.map((verdict) => verdict.a)), b: new Set(verdicts.map((verdict) => verdict.b)) };
  return planCellReviews(a, b, deriveTablePairs(cells), cells, maxRemaining)
    .map((plan) => ({ ...plan, remaining: plan.remaining.filter((cell) => !compared[cell.slice(0, 1) as "a" | "b"].has(cell.slice(2))) }))
    .filter((plan) => plan.remaining.length > 0);
}

export type CellReview2State = { state: "paired"; counterpart: string } | { state: "no_counterpart"; reason: string } | { state: "undecidable" } | { state: "unanswered" } | { state: "answer_rejected"; why: string };

export interface CellReview2Judgement {
  plan: CellReviewPlan;
  newPairs: (Verdict & { from: string[] })[];
  cells: Map<string, CellReview2State>;
  rejected: Record<string, number>;
}

/**
 * 칸 검수(새 버전)의 답 확인. 방법 H 의 확인에 하나를 더한다: **상대 칸은 그 요청의 남은 칸이어야 한다**(`counterpart_not_remaining`).
 * 이미 짝지어진 칸이나 요청 밖의 칸을 상대로 적은 답은 버린다. 그 밖은 같다: 목록에 없는 칸에 대한 답(`not_in_remaining_list`), 없는 ID(`unknown_id`),
 * 같은 문서의 칸(`same_document`), 원문에 없는 단위 글자(`unit_not_in_source`), 상대도 사유도 없는 답, 한 칸에 엇갈린 답. 답이 안 온 남은 칸은 「미응답」이다.
 */
export function judgeCellReview2(answer: ReviewCells2Answer | null, plan: CellReviewPlan, a: DocContext, b: DocContext, units: UnitTable): CellReview2Judgement {
  const out: CellReview2Judgement = { plan, newPairs: [], cells: new Map(plan.remaining.map((cell) => [cell, { state: "unanswered" }])), rejected: {} };
  if (!answer) return out;
  const facts = { a: new Map(a.extraction.facts.filter((fact) => fact.bucket === "table_cell").map((fact) => [fact.id, fact])), b: new Map(b.extraction.facts.filter((fact) => fact.bucket === "table_cell").map((fact) => [fact.id, fact])) };
  const docs = { a, b };
  const parse = (id: string): { side: "a" | "b"; fact: Fact } | null => {
    const side = id.startsWith("a:") ? "a" : id.startsWith("b:") ? "b" : null;
    const fact = side ? facts[side].get(id.slice(2)) : undefined;
    return side && fact ? { side, fact } : null;
  };
  const remaining = new Set(plan.remaining);
  const answers = new Map<string, ReviewCells2Answer["answers"]>();
  for (const raw of answer.answers) {
    if (!remaining.has(raw.cell)) bump(out.rejected, parse(raw.cell) ? "not_in_remaining_list" : "unknown_id");
    else answers.set(raw.cell, [...(answers.get(raw.cell) ?? []), raw]);
  }
  const found = new Map<string, Verdict & { from: string[] }>();
  for (const [cell, list] of answers) {
    const reject = (why: string, count = 1): void => {
      bump(out.rejected, why, count);
      out.cells.set(cell, { state: "answer_rejected", why });
    };
    if (new Set(list.map((item) => JSON.stringify(item))).size > 1) {
      reject("conflicting_answers", list.length);
      continue;
    }
    if (list.length > 1) bump(out.rejected, "duplicate_answer", list.length - 1);
    const raw = list[0] as ReviewCells2Answer["answers"][number];
    const self = parse(cell) as { side: "a" | "b"; fact: Fact };
    if (raw.counterpart === null) {
      if (raw.reason === null) reject("neither_counterpart_nor_reason");
      else out.cells.set(cell, raw.reason === "undecidable" ? { state: "undecidable" } : { state: "no_counterpart", reason: raw.reason });
      continue;
    }
    const other = parse(raw.counterpart);
    if (!other) reject("unknown_id");
    else if (other.side === self.side) reject("same_document");
    else if (!remaining.has(raw.counterpart)) reject("counterpart_not_remaining");
    else if (!unitOk(raw.unit, docs[self.side], self.fact) || !unitOk(raw.counterpart_unit, docs[other.side], other.fact)) reject("unit_not_in_source");
    else {
      const [fa, ua, fb, ub] = self.side === "a" ? [self.fact, raw.unit, other.fact, raw.counterpart_unit] : [other.fact, raw.counterpart_unit, self.fact, raw.unit];
      const entry = found.get(`${fa.id}|${fb.id}`);
      if (entry) entry.from.push(cell);
      else {
        const result = compareCells({ fact: fa, unit: ua }, { fact: fb, unit: ub }, units);
        found.set(`${fa.id}|${fb.id}`, { a: fa.id, b: fb.id, outcome: result.outcome as Verdict["outcome"], reason: result.reason, valuesDiffer: result.valuesDiffer, from: [cell] });
      }
      out.cells.set(cell, { state: "paired", counterpart: raw.counterpart });
    }
  }
  out.newPairs = [...found.values()];
  return out;
}

export interface Review2Result {
  tablePairs: DerivedTablePair[];
  before: { outcome: MethodOutcome; tally: { a: ReviewTally; b: ReviewTally } };
  after: { outcome: MethodOutcome; tally: { a: ReviewTally; b: ReviewTally } };
  /** 검수가 새로 낸 짝 (표의 숫자 칸끼리). */
  newPairs: (Verdict & { tables: { a: string; b: string } })[];
  /** 남은 칸마다, 요청마다의 답. */
  remaining: { cell: string; otherTable: string; request: string; answer: CellReview2State }[];
  rejected: Record<string, number>;
}

function combine(answers: CellReview2State[]): ReviewState {
  if (answers.some((item) => item.state === "unanswered")) return "reviewed:unanswered";
  if (answers.some((item) => item.state === "answer_rejected")) return "reviewed:answer_rejected";
  if (answers.some((item) => item.state === "undecidable")) return "reviewed:undecidable";
  const reasons = [...new Set(answers.flatMap((item) => (item.state === "no_counterpart" ? [item.reason] : [])))].sort();
  return reasons.length > 0 ? `reviewed:no_counterpart:${reasons.join("+")}` : "reviewed:unanswered";
}

/**
 * 방법 H2 의 결과: D2 의 답에 칸 검수가 새로 낸 짝을 더하고, 두 문서의 모든 값 후보에 검수 전과 검수 뒤의 결과를 하나씩 붙여 센다.
 * 검수 뒤의 우선순위: 견줌 > 표의 숫자 칸이 아님(짝이 안 나온 서술 숫자) > 검수가 답한 남은 칸 > 버려진 답이 가리킨 칸 > 짝이 없는 표의 칸.
 */
export function review2Outcome(a: Extraction, b: Extraction, base: PlaceJudgement, reviews: CellReview2Judgement[]): Review2Result {
  const before = tallyPlaces(a, b, base);
  const extractionOf = { a, b };
  const outcomeOf = (verdicts: Verdict[], rejected: Record<string, number>, states: { a: Map<string, ReviewState>; b: Map<string, ReviewState> }): MethodOutcome => {
    const outcome: MethodOutcome = { verdicts, intra: [], states: { a: {}, b: {} }, rejected };
    for (const side of ["a", "b"] as const) for (const [id, state] of states[side]) if (!state.startsWith("compared:")) outcome.states[side][id] = state;
    return outcome;
  };
  const verdicts = [...base.verdicts];
  const seen = new Set(verdicts.map(pairKey));
  const newPairs: Review2Result["newPairs"] = [];
  const rejected: Record<string, number> = {};
  const answersByCell = { a: new Map<string, CellReview2State[]>(), b: new Map<string, CellReview2State[]>() };
  const remaining: Review2Result["remaining"] = [];
  for (const review of reviews) {
    for (const pair of review.newPairs) {
      if (seen.has(pairKey(pair))) continue;
      seen.add(pairKey(pair));
      const plain: Verdict = { a: pair.a, b: pair.b, outcome: pair.outcome, reason: pair.reason, valuesDiffer: pair.valuesDiffer };
      verdicts.push(plain);
      newPairs.push({ ...plain, tables: { a: review.plan.pair.a, b: review.plan.pair.b } });
    }
    for (const [reason, count] of Object.entries(review.rejected)) bump(rejected, reason, count);
    for (const [cell, answer] of review.cells) {
      const side = cell.slice(0, 1) as "a" | "b";
      const other = side === "a" ? "b" : "a";
      answersByCell[side].set(cell.slice(2), [...(answersByCell[side].get(cell.slice(2)) ?? []), answer]);
      remaining.push({ cell, otherTable: `${other}:${review.plan.pair[other]}`, request: review.plan.id, answer });
    }
  }
  const paired = { a: new Set(before.tablePairs.map((pair) => pair.a)), b: new Set(before.tablePairs.map((pair) => pair.b)) };
  const afterStates = { a: new Map<string, ReviewState>(), b: new Map<string, ReviewState>() };
  for (const side of ["a", "b"] as const) {
    const worst = new Map<string, Verdict["outcome"]>();
    for (const verdict of verdicts) {
      const was = worst.get(verdict[side]);
      if (!was || SEVERITY[verdict.outcome] > SEVERITY[was]) worst.set(verdict[side], verdict.outcome);
    }
    for (const fact of extractionOf[side].facts) {
      const compared = worst.get(fact.id);
      const answers = answersByCell[side].get(fact.id);
      let state: ReviewState;
      if (compared) state = `compared:${compared}`;
      else if (fact.bucket !== "table_cell") state = `unverified:outside_tables:${fact.bucket}`;
      else if (answers) state = combine(answers);
      // 짝지어진 표의 남은 칸인데 검수 요청에 실리지 않은 칸은 없어야 한다. 있으면 미응답으로 드러낸다.
      else if (paired[side].has(fact.where.gridId as string)) state = "reviewed:unanswered";
      else if (base.rejectedPlaces[side].has(fact.id)) state = "unverified:answer_rejected";
      else state = "unverified:table_not_paired";
      afterStates[side].set(fact.id, state);
    }
  }
  const allRejected: Record<string, number> = { ...base.rejected };
  for (const [reason, count] of Object.entries(rejected)) bump(allRejected, `cellReview:${reason}`, count);
  return {
    tablePairs: before.tablePairs,
    before: { outcome: outcomeOf(base.verdicts, base.rejected, before.states), tally: before.tally },
    after: { outcome: outcomeOf(verdicts, allRejected, afterStates), tally: { a: tallyReview(a.facts.length, afterStates.a.values()), b: tallyReview(b.facts.length, afterStates.b.values()) } },
    newPairs,
    remaining,
    rejected,
  };
}

// ── I: 같은 문서 안의 묶음 ───────────────────────────────────────────────────

export const groupPlacesSchema = z.object({
  groups: z.array(z.object({ places: z.array(z.object({ id: z.string(), unit: z.string().nullable() })) })),
});
export type GroupPlacesAnswer = z.infer<typeof groupPlacesSchema>;

export interface JudgedGroup {
  places: string[];
  /** 묶음 안의 자리를 둘씩 견준 결과 (자리가 n 개면 n(n-1)/2 개). */
  verdicts: Verdict[];
  /** 묶음의 결과: 하나라도 불일치면 불일치, 아니고 하나라도 설명 필요면 설명 필요, 전부 일치면 일치. */
  outcome: Verdict["outcome"];
}

export interface GroupJudgement {
  groups: JudgedGroup[];
  rejected: Record<string, number>;
  /** 둘 이상의 묶음에 든 자리. 버리지 않고 묶음마다 견주되 그 사실을 남긴다. */
  inSeveralGroups: string[];
}

/**
 * I 의 답 확인과 묶음 안 비교. 없는 ID 의 자리는 그 자리만 버리고(`unknown_id`), 한 묶음에 같은 자리가 두 번이면 한 번만(`duplicate_place`),
 * 단위 글자가 그 쪽에 없으면 그 자리만 버린다(`unit_not_in_source`). 남은 자리가 하나뿐인 묶음은 버린다(`single_place`). 자리의 집합이 같은 묶음은 한 번만(`duplicate_group`).
 * 한 자리가 두 묶음에 들어 있어도 묶음마다 견준다 (어느 쪽이 옳은지 코드가 고르지 않는다).
 */
export function judgeGroups(answers: GroupPlacesAnswer[], doc: DocContext, units: UnitTable): GroupJudgement {
  const out: GroupJudgement = { groups: [], rejected: {}, inSeveralGroups: [] };
  const facts = allFacts(doc.extraction);
  const seenGroups = new Set<string>();
  const uses = new Map<string, number>();
  for (const answer of answers) {
    for (const group of answer.groups) {
      const places = new Map<string, { fact: Fact; unit: string | null }>();
      for (const raw of group.places) {
        const fact = facts.get(raw.id);
        if (!fact) bump(out.rejected, "unknown_id");
        else if (places.has(fact.id)) bump(out.rejected, "duplicate_place");
        else if (!unitOk(raw.unit, doc, fact)) bump(out.rejected, "unit_not_in_source");
        else places.set(fact.id, { fact, unit: raw.unit });
      }
      if (places.size < 2) {
        bump(out.rejected, "single_place");
        continue;
      }
      const ids = [...places.keys()];
      const signature = [...ids].sort().join("|");
      if (seenGroups.has(signature)) {
        bump(out.rejected, "duplicate_group");
        continue;
      }
      seenGroups.add(signature);
      const list = [...places.values()];
      const verdicts: Verdict[] = [];
      for (let i = 0; i < list.length; i++) {
        for (let j = i + 1; j < list.length; j++) {
          const result = compareCells(list[i] as { fact: Fact; unit: string | null }, list[j] as { fact: Fact; unit: string | null }, units);
          verdicts.push({ a: (list[i] as { fact: Fact }).fact.id, b: (list[j] as { fact: Fact }).fact.id, outcome: result.outcome as Verdict["outcome"], reason: result.reason, valuesDiffer: result.valuesDiffer });
        }
      }
      const outcome = verdicts.reduce<Verdict["outcome"]>((worst, verdict) => (SEVERITY[verdict.outcome] > SEVERITY[worst] ? verdict.outcome : worst), "match");
      out.groups.push({ places: ids, verdicts, outcome });
      for (const id of ids) uses.set(id, (uses.get(id) ?? 0) + 1);
    }
  }
  out.inSeveralGroups = [...uses.entries()].filter(([, count]) => count > 1).map(([id]) => id);
  return out;
}

export interface GroupTally {
  /** 그 문서의 값 후보 수 (분모). */
  total: number;
  /** 묶음에 든 자리: 그 자리가 낀 비교 가운데 가장 무거운 결과. */
  grouped: { match: number; mismatch: number; needsExplanation: number };
  /** 어느 묶음에도 들지 않은 자리 (갈래별: 표의 숫자 칸, 문장 속, 숫자뿐인 블록, 표의 글 칸 속). */
  ungrouped: Record<string, number>;
}

/** 문서 하나의 모든 값 후보가 결과 하나에. 합이 값 후보 수와 다르면 예외다. */
export function tallyGroups(extraction: Extraction, judgement: GroupJudgement): { states: Map<string, string>; tally: GroupTally } {
  const worst = new Map<string, Verdict["outcome"]>();
  for (const group of judgement.groups) {
    for (const verdict of group.verdicts) {
      for (const id of [verdict.a, verdict.b]) {
        const was = worst.get(id);
        if (!was || SEVERITY[verdict.outcome] > SEVERITY[was]) worst.set(id, verdict.outcome);
      }
    }
  }
  const states = new Map<string, string>();
  const tally: GroupTally = { total: extraction.facts.length, grouped: { match: 0, mismatch: 0, needsExplanation: 0 }, ungrouped: {} };
  for (const fact of extraction.facts) {
    const outcome = worst.get(fact.id);
    states.set(fact.id, outcome ? `grouped:${outcome}` : `ungrouped:${fact.bucket}`);
    if (outcome === "match") tally.grouped.match += 1;
    else if (outcome === "mismatch") tally.grouped.mismatch += 1;
    else if (outcome === "needs_explanation") tally.grouped.needsExplanation += 1;
    else bump(tally.ungrouped, fact.bucket);
  }
  const sum = tally.grouped.match + tally.grouped.mismatch + tally.grouped.needsExplanation + Object.values(tally.ungrouped).reduce((x, y) => x + y, 0);
  if (sum !== tally.total) throw new TallyError(`값 후보 ${tally.total}개 중 결과가 붙은 것이 ${sum}개다. 합이 맞지 않아 수를 내지 않는다`);
  return { states, tally };
}
