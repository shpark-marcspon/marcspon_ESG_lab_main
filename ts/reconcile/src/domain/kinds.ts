import { dec, show, sub } from "./dec.js";
import type { DocModel, Extraction, Fact } from "./facts.js";
import { classifyCell, scanNumbers } from "./notation.js";

// 값 후보의 종류 (밴드 V, MARC-TICK-25). 옵션이다: 숫자 뽑기의 기본 동작(값 후보 목록, ID, 갈래)은 바꾸지 않는다.
// 값 후보는 버리지 않는다. 종류는 「이 숫자가 검증할 값인가, 값이 아닌 표시인가」를 가르는 꼬리표일 뿐이고, 요청 조립(가림, ID)에 쓰지 않는다.
// 규칙은 꼴(앞뒤 글자, 단위, 머리글, 좌표, 수열)만 본다. 특정 문서의 글귀를 박지 않는다. 규격·기준 이름은 일반 꼴(대문자 약어 + 번호)과
// `notation.ts` 의 이름표 낱말(Scope, Category, …)로 본다. 틀릴 수 있다: 정답과의 대조는 `src/compare/kind-report.ts`.

export const VALUE_KINDS = ["value", "identifier", "duration", "count", "year_label", "scale"] as const;
/**
 * value: 검증할 값 (잰 양·금액·비율·목표값·문턱값). identifier: 가리키는 번호 (규격·조항·각주·쪽 번호, 이름의 일부).
 * duration: 기간·시점 (10년, 3개년, 6개월, 날짜의 조각). count: 구성을 센 수(3종, 4대, 5개 주제)와 정해 둔 빈도(연 1회 이상).
 * year_label: 연도 표시 (표·도표의 연도 칸). scale: 눈금·척도·기준 단위 (축 눈금, 5점 척도, 1억 원당).
 */
export type ValueKind = (typeof VALUE_KINDS)[number];

export const isNonValue = (kind: ValueKind): boolean => kind !== "value";

/** 글 속 값 후보 하나의 앞뒤 글. 표의 숫자 칸이면 `text` 는 칸 글자, `start`·`end` 는 칸 전체다. */
export interface KindContext {
  text: string;
  start: number;
  end: number;
  /** 표에서 나온 숫자면 열 머리글의 글자. */
  columnHeaders: string[];
}

const LABEL_RUN = /(Scope|Category|CAT|Cat|Tier|Level|Phase|Step|Type|Class|스코프|카테고리)\s?\d+(?:\s?[,&]\s?\d+)*\s?[,&]\s?$/u;
const ACRONYM_BEFORE = /(?:^|[^A-Za-z])[A-Z]{2,}(?:\/[A-Z]{2,})? ?$/u;
const CLAUSE_BEFORE = /(문단|조항|원칙|Article|Section|Principle|Paragraph)\s?$|제\d+조의$/u;
const STANDARD_HEADER = /GRI|SASB|ESRS|KSSB|IFRS|TCFD|ISO|코드|지표 ?번호|index/iu;
const DURATION_UNITS = new Set(["년", "개월", "일", "시간", "주"]);
const isInteger = (fact: Fact): boolean => fact.decimals === 0;
const digits = (fact: Fact): string => fact.value.replace(/^-/, "");

/** 글 속 값 후보 하나의 종류 (앞뒤 글자만 본다). 수열·좌표로 보는 눈금은 `classifyKinds` 가 따로 붙인다. */
export function kindOf(fact: Fact, context: KindContext): ValueKind {
  if (fact.bucket === "table_cell") return fact.flags.includes("year_like") ? "year_label" : "value";
  const before = context.text.slice(Math.max(0, context.start - 32), context.start);
  const after = context.text.slice(context.end, context.end + 16);
  const bare = !fact.percent && fact.unitSuffix === null;
  const small = isInteger(fact) && !fact.raw.includes(",") && digits(fact).length <= 2;

  // 가리키는 번호
  if (bare && LABEL_RUN.test(before)) return "identifier";
  if (bare && (before.endsWith("*") || (before.endsWith(")") && after.startsWith(")")))) return "identifier";
  if (bare && (CLAUSE_BEFORE.test(before) || /^\(\d+\)/.test(after))) return "identifier";
  if (bare && (/\d\/$/.test(before) || /^\/\d/.test(after))) return "identifier";
  if (bare && /^ ?(페이지|쪽(?![가-힣]))/u.test(after)) return "identifier";
  // 대문자 약어 뒤의 번호: 쉼표 없는 세 자리 이상의 정수면 규격 번호로 본다(뒤에 조사가 붙어도). 그보다 짧거나 소수면 뒤에 한글이 바로 오지 않을 때만.
  if (bare && ACRONYM_BEFORE.test(before) && ((isInteger(fact) && !fact.raw.includes(",") && digits(fact).length >= 3) || !/^ ?[가-힣%]/u.test(after))) return "identifier";
  if (context.columnHeaders.some((text) => STANDARD_HEADER.test(text)) && bare) return "identifier";
  const whole = context.text.trim();
  // 차례의 한 줄(`쪽 번호 + 제목`)과 글 끝에 홀로 붙은 쪽 번호.
  if (bare && isInteger(fact) && digits(fact).length <= 3 && /^\d{1,3} [^\d]{2,40}$/u.test(whole) && context.start === context.text.indexOf(whole)) return "identifier";
  if (bare && isInteger(fact) && digits(fact).length <= 3 && context.text.slice(context.end).trim() === "" && /[.。] ?$/.test(before)) return "identifier";

  // 기간·시점
  if (/['’]$/.test(before)) return "duration";
  if (isInteger(fact) && fact.unitSuffix !== null && DURATION_UNITS.has(fact.unitSuffix)) return "duration";
  if (isInteger(fact) && /^ ?개년/u.test(after)) return "duration";

  // 눈금·척도·기준 단위
  if (/^점 ?(척도|만점|[~∼〜]|에서|까지)/u.test(after) || /^에 가까/u.test(after)) return "scale";
  if (/^1(,?0)*$/.test(fact.raw) && /^ ?[가-힣]{0,2} ?[가-힣]{1,2} ?당(?![가-힣])/u.test(after)) return "scale";

  // 구성을 센 수, 정해 둔 빈도
  if (small && /^ ?(종|대|가지)(?=$|[^가-힣]|(?:으로|로|의|을|를|이|가|은|는|에)(?![가-힣]))/u.test(after)) return "count";
  if (small && /^ ?개(?![월년소국사])/u.test(after)) return "count";
  if (small && /^회/u.test(after) && (/(연|매년|분기|분기별|반기|매월|월|주) ?$/u.test(before) || /^회 ?이상/u.test(after))) return "count";
  return "value";
}

/** 수들이 같은 간격으로 늘어서 있는가 (셋 이상, 간격이 0 이 아님). */
function evenlySpaced(values: string[]): boolean {
  if (values.length < 3) return false;
  const step = show(sub(dec(values[1] as string), dec(values[0] as string)));
  if (/^-?0(\.0+)?$/.test(step)) return false;
  return values.every((value, index) => index === 0 || show(sub(dec(value), dec(values[index - 1] as string))) === step);
}

/** 수 둘이 눈금 꼴인가: 끝자리가 0 이고, 둘 다 그 간격의 배수다 (`600 400`, `20.0 15.0`). 값 둘(`18.2 18.1`)은 아니다. */
function roundPair(raws: string[], values: string[]): boolean {
  if (values.length !== 2 || !raws.every((raw) => /0$/.test(raw))) return false;
  // 두 수를 같은 소수 자리의 정수로 맞춰 본다 (십진 그대로, JS number 를 거치지 않는다).
  const [p, q] = values.map(dec) as [ReturnType<typeof dec>, ReturnType<typeof dec>];
  const places = Math.max(p.scale, q.scale);
  const x = p.coef * 10n ** BigInt(places - p.scale);
  const y = q.coef * 10n ** BigInt(places - q.scale);
  const step = x > y ? x - y : y - x;
  return step > 0n && x % step === 0n && y % step === 0n;
}

/**
 * 문서 하나의 모든 값 후보에 종류를 붙인다. 값 후보 목록은 건드리지 않는다 (ID → 종류의 표만 낸다).
 * 앞뒤 글자로 보는 규칙(`kindOf`) 뒤에 눈금 둘을 더 본다:
 *  - 숫자만 든 표의 글 칸(도표의 축이 한 칸으로 읽힌 것)에서 수들이 같은 간격으로 늘어서 있거나(셋 이상), 둘뿐이면 눈금 꼴일 때.
 *  - 단위 없는 숫자뿐인 블록이 한 쪽에서 같은 세로줄(왼쪽이나 오른쪽 끝이 2pt 안)에 셋 이상 같은 간격으로 쌓여 있을 때.
 */
export function classifyKinds(document: DocModel, extraction: Extraction): Record<string, ValueKind> {
  const kinds: Record<string, ValueKind> = {};
  const blocks = new Map(document.blocks.map((block) => [block.id, block]));
  const grids = new Map(extraction.grids.map((grid) => [grid.id, grid]));
  const byCell = new Map<string, Fact[]>();
  for (const fact of extraction.facts) {
    const where = fact.where;
    let context: KindContext;
    if (where.blockId !== null) context = { text: blocks.get(where.blockId)?.text ?? fact.raw, start: where.span?.start ?? 0, end: where.span?.end ?? fact.raw.length, columnHeaders: [] };
    else {
      const text = grids.get(where.gridId as string)?.cells[where.row as number]?.[where.col as number] ?? fact.raw;
      context = { text, start: where.span?.start ?? 0, end: where.span?.end ?? text.length, columnHeaders: fact.headers?.col.map((ref) => ref.text) ?? [] };
      if (fact.bucket === "table_text_cell") byCell.set(`${where.gridId}:r${where.row}c${where.col}`, [...(byCell.get(`${where.gridId}:r${where.row}c${where.col}`) ?? []), fact]);
    }
    kinds[fact.id] = kindOf(fact, context);
  }
  for (const facts of byCell.values()) {
    const first = facts[0] as Fact;
    const text = grids.get(first.where.gridId as string)?.cells[first.where.row as number]?.[first.where.col as number] ?? "";
    if (classifyCell(text).kind !== "multi_number" || scanNumbers(text).length !== facts.length) continue;
    const values = facts.map((fact) => fact.value);
    if (facts.every((fact) => !fact.percent) && (evenlySpaced(values) || roundPair(facts.map((fact) => fact.raw), values))) for (const fact of facts) kinds[fact.id] = "scale";
  }
  const columns = new Map<string, { fact: Fact; y: number }[]>();
  const perBlock = new Map<string, number>();
  for (const fact of extraction.facts) if (fact.where.blockId !== null) perBlock.set(fact.where.blockId, (perBlock.get(fact.where.blockId) ?? 0) + 1);
  for (const fact of extraction.facts) {
    if (fact.bucket !== "number_only_block" || fact.unitSuffix !== null || fact.percent || perBlock.get(fact.where.blockId as string) !== 1) continue;
    const block = blocks.get(fact.where.blockId as string);
    if (!block) continue;
    const [x0 = 0, y0 = 0, x1 = 0] = block.bbox;
    for (const key of [`${block.page}|L${Math.round(x0 / 2)}`, `${block.page}|R${Math.round(x1 / 2)}`]) columns.set(key, [...(columns.get(key) ?? []), { fact, y: y0 }]);
  }
  for (const column of columns.values()) {
    const sorted = [...column].sort((a, b) => a.y - b.y);
    if (evenlySpaced(sorted.map((item) => item.fact.value))) for (const item of sorted) kinds[item.fact.id] = "scale";
  }
  return kinds;
}
