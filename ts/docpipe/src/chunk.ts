import { createHash } from "node:crypto";
import type { DocSection } from "./document.js";
import type { Item, Unit } from "./select.js";

export interface Chunk {
  /** `c<순번>-<내용 해시>`. 해시에는 작업 지문(프롬프트, 용어집)과 조각의 글자가 들어간다. */
  id: string;
  index: number;
  unitIds: string[];
  items: Item[];
  /** 조각에 실린 원문의 어림 토큰 (id 를 포함한 JSON 꼴 기준). */
  sourceTokens: number;
  pageStart: number;
  pageEnd: number;
  sectionIds: string[];
  /** 단위 하나가 예산보다 커서 혼자 조각이 된 경우. 표와 문단은 쪼개지 않는다. */
  overBudget: boolean;
}

export interface ChunkOptions {
  /** 조각 하나에 실을 원문의 토큰 상한. */
  budgetTokens: number;
  estimate: (text: string) => number;
  sections: DocSection[];
  /** 프롬프트 템플릿 해시, 용어집 해시처럼 결과를 바꾸는 것들. 바뀌면 조각 id 가 바뀌어 다시 실행된다. */
  fingerprint: string;
}

export function itemTokens(item: Item, estimate: (text: string) => number): number {
  return estimate(JSON.stringify(item));
}

interface Sized {
  unit: Unit;
  tokens: number;
}

/**
 * 단위(문단, 표)를 토큰 예산에 맞춰 조각으로 묶는다.
 *
 * - 단위는 쪼개지 않는다. 예산보다 큰 단위는 혼자 조각이 되고 `overBudget` 으로 표시된다.
 * - PDF 책갈피에서 온 섹션(`pdf_outline`)은 반드시 조각을 가른다.
 * - 추정한 섹션(`page_top_largest_font`, `no_title_found`)은 그 자리에서 가르는 쪽을 먼저 고르되,
 *   다음 섹션이 통째로 남은 예산에 들어가면 같은 조각에 싣는다. 추정 섹션은 한두 쪽으로 잘게 쪼개져 있어
 *   그대로 가르면 조각마다 붙는 고정 앞부분(지침, 용어집)이 원문보다 커진다.
 * - 조각이 제목으로 끝나지 않게, 끝에 걸린 제목은 다음 조각으로 넘긴다.
 */
export function chunkUnits(units: Unit[], options: ChunkOptions): Chunk[] {
  if (!Number.isInteger(options.budgetTokens) || options.budgetTokens <= 0) throw new Error(`조각 예산은 양의 정수여야 한다: ${options.budgetTokens}`);
  const hardSections = new Set(options.sections.filter((section) => section.basis === "pdf_outline").map((section) => section.id));

  const groups: { sectionId: string | null; members: Sized[]; tokens: number }[] = [];
  for (const unit of units) {
    const tokens = unit.items.reduce((sum, item) => sum + itemTokens(item, options.estimate), 0);
    const last = groups[groups.length - 1];
    if (last && last.sectionId === unit.sectionId) {
      last.members.push({ unit, tokens });
      last.tokens += tokens;
    } else {
      groups.push({ sectionId: unit.sectionId, members: [{ unit, tokens }], tokens });
    }
  }

  const packed: Sized[][] = [];
  let current: Sized[] = [];
  let currentTokens = 0;
  const flush = (carryTrailingHeadings: boolean): void => {
    if (current.length === 0) return;
    const carry: Sized[] = [];
    if (carryTrailingHeadings) {
      while (current.length > 1 && isHeading(current[current.length - 1] as Sized)) carry.unshift(current.pop() as Sized);
      // 제목뿐인 조각은 넘길 것이 없다. 그대로 낸다.
      if (current.every(isHeading)) {
        current.push(...carry);
        carry.length = 0;
      }
    }
    packed.push(current);
    current = carry;
    currentTokens = carry.reduce((sum, sized) => sum + sized.tokens, 0);
  };

  let previousHard = false;
  for (const group of groups) {
    const hard = group.sectionId !== null && hardSections.has(group.sectionId);
    if (hard || previousHard || currentTokens + group.tokens > options.budgetTokens) flush(false);
    if (group.tokens <= options.budgetTokens) {
      current.push(...group.members);
      currentTokens += group.tokens;
    } else {
      for (const sized of group.members) {
        if (current.length > 0 && currentTokens + sized.tokens > options.budgetTokens) flush(true);
        // 넘긴 제목과 합쳐도 넘치면 제목만으로 조각을 닫는다.
        if (current.length > 0 && currentTokens + sized.tokens > options.budgetTokens) flush(false);
        current.push(sized);
        currentTokens += sized.tokens;
      }
    }
    previousHard = hard;
  }
  flush(false);

  return packed.map((members, index) => {
    const items = members.flatMap((sized) => sized.unit.items);
    const tokens = members.reduce((sum, sized) => sum + sized.tokens, 0);
    const pages = members.map((sized) => sized.unit.page);
    const hash = createHash("sha256").update(options.fingerprint).update("\n").update(JSON.stringify(items)).digest("hex").slice(0, 12);
    return {
      id: `c${String(index + 1).padStart(4, "0")}-${hash}`,
      index,
      unitIds: members.map((sized) => sized.unit.id),
      items,
      sourceTokens: tokens,
      pageStart: Math.min(...pages),
      pageEnd: Math.max(...pages),
      sectionIds: [...new Set(members.map((sized) => sized.unit.sectionId).filter((id): id is string => id !== null))],
      overBudget: tokens > options.budgetTokens,
    };
  });
}

function isHeading(sized: Sized): boolean {
  return sized.unit.type === "text" && sized.unit.blockKind === "heading";
}
