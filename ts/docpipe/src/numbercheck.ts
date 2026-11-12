import { numberRuns } from "./numeric.js";

/** 원문과 번역문의 숫자 덩어리를 개수까지 견준 차이 (순서는 보지 않는다). 둘 다 비면 숫자가 같다. */
export interface NumberDiff {
  /** 원문에만 있는 숫자 */
  sourceOnly: string[];
  /** 번역문에만 있는 숫자 */
  targetOnly: string[];
}

export function numberDiff(source: string, target: string): NumberDiff {
  const targetOnly = numberRuns(target);
  const sourceOnly: string[] = [];
  for (const run of numberRuns(source)) {
    const at = targetOnly.indexOf(run);
    if (at === -1) sourceOnly.push(run);
    else targetOnly.splice(at, 1);
  }
  return { sourceOnly, targetOnly };
}

export function hasNumberDiff(diff: NumberDiff): boolean {
  return diff.sourceOnly.length + diff.targetOnly.length > 0;
}

/**
 * 숫자가 달라진 셀의 등급.
 * - `notation_like` 달라진 숫자가 전부 아래의 「값이 그대로인 표기 변환」 꼴로 설명된다. 흔한 표기 변환으로 보인다.
 * - `other`         설명되지 않는 숫자가 하나라도 남는다. 사람이 먼저 봐야 한다.
 * 등급은 검토 순서를 정하는 데만 쓴다. `notation_like` 도 의심 표시이고 검토 대상에서 빠지지 않는다.
 */
export type SuspectGrade = "notation_like" | "other";

export type NotationPattern =
  /** 수 단위 낱말을 숫자로 풀어 씀: `만 톤` → `10,000 tons`, `24만 톤` → `240,000 tons`, `2.5억` → `250 million` */
  | "unit_expansion"
  /** 작은 수(1~12)를 같은 값의 영어 낱말로: `1차` → `first-tier`, `연 1회` → `once a year`, `3대 원칙` → `three principles` */
  | "number_word"
  /** 두 자리 연도를 네 자리로: `’30년` → `2030` */
  | "year_expansion"
  /** `N월` 을 달 이름으로: `1월 1일` → `January 1` */
  | "month_name";

export interface NumberChangeGrade {
  grade: SuspectGrade;
  /** 설명된 숫자. 원문 쪽이나 번역문 쪽 하나만 있을 수 있다. */
  explained: { pattern: NotationPattern; source: string | null; target: string | null }[];
  /** 어느 꼴로도 설명되지 않고 남은 숫자. */
  unexplained: NumberDiff;
}

const MULTIPLIERS: [string, number][] = [
  ["십만", 1e5],
  ["백만", 1e6],
  ["천만", 1e7],
  ["십억", 1e9],
  ["백억", 1e10],
  ["천억", 1e11],
  ["십", 1e1],
  ["백", 1e2],
  ["천", 1e3],
  ["만", 1e4],
  ["억", 1e8],
  ["조", 1e12],
];
const MULTIPLIER_VALUE = new Map(MULTIPLIERS);
const MULTIPLIER_WORDS = MULTIPLIERS.map(([word]) => word).join("|");
const SCALE_VALUE = new Map<string, number>([
  ["hundred", 1e2],
  ["thousand", 1e3],
  ["million", 1e6],
  ["billion", 1e9],
  ["trillion", 1e12],
]);
const NUMBER_WORDS: Record<number, string> = {
  1: "one|first|once|single|primary",
  2: "two|second|twice|secondary|double",
  3: "three|third|thrice|triple|tertiary",
  4: "four|fourth",
  5: "five|fifth",
  6: "six|sixth",
  7: "seven|seventh",
  8: "eight|eighth",
  9: "nine|ninth",
  10: "ten|tenth",
  11: "eleven|eleventh",
  12: "twelve|twelfth",
};
const MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"];

const NUMBER_RUN = /\d+(?:[.,]\d+)*/g;
const normalizeRun = (run: string): string => run.replace(/,(?=\d{3}(?!\d))/g, "");
const close = (a: number, b: number): boolean => Math.abs(a - b) <= 1e-9 * Math.max(Math.abs(a), Math.abs(b));

/** 글자 속 숫자 덩어리마다, 그 바로 뒤에 붙은 글자(뒤따르는 수 단위 낱말·배율 낱말을 읽는 데 쓴다)를 같이 낸다. */
function runsWithTail(text: string): { run: string; before: string; tail: string }[] {
  return [...text.matchAll(NUMBER_RUN)].map((match) => ({
    run: normalizeRun(match[0]),
    before: text.slice(Math.max(0, match.index - 2), match.index),
    tail: text.slice(match.index + match[0].length, match.index + match[0].length + 16),
  }));
}

/** 숫자 바로 뒤의 수 단위 낱말: `24만 톤` → 1e4. 없으면 1. */
function sourceScale(tail: string): number {
  const match = new RegExp(`^\\s?(${MULTIPLIER_WORDS})`, "u").exec(tail);
  return match ? (MULTIPLIER_VALUE.get(match[1] as string) as number) : 1;
}

/** 숫자 바로 뒤의 영어 배율 낱말: `250 million` → 1e6, `KRW 100 million` 의 100 → 1e6. 없으면 1. */
function targetScale(tail: string): number {
  const match = /^[\s-]?(hundred|thousand|million|billion|trillion)\b/i.exec(tail);
  return match ? (SCALE_VALUE.get((match[1] as string).toLowerCase()) as number) : 1;
}

/** 숫자에 붙지 않고 홀로 선 수 단위 낱말의 값들: `만 톤`, `(단위: 억 원)`, `천원`. `천연가스` 의 `천` 은 아니다. */
function standaloneMultipliers(source: string): number[] {
  const pattern = new RegExp(`(?<![\\p{L}\\p{Nd}])(?<!\\p{Nd}\\s)(${MULTIPLIER_WORDS})(?=\\s|$|[^\\p{L}]|[원톤건명])`, "gu");
  return [...source.matchAll(pattern)].map((match) => MULTIPLIER_VALUE.get(match[1] as string) as number);
}

function countWord(target: string, alternatives: string): number {
  return [...target.matchAll(new RegExp(`\\b(?:${alternatives})\\b`, "gi"))].length;
}

function hasMonthName(target: string, month: number): boolean {
  const name = MONTHS[month - 1];
  if (!name) return false;
  return new RegExp(`\\b(?:${name}|${name.slice(0, 3)})\\b`, "i").test(target);
}

/**
 * 숫자가 달라진 셀이 「흔한 표기 변환으로 보임」인지 「그 밖」인지 가른다.
 *
 * 기준은 하나다: **달라진 숫자마다 값이 그대로라는 증거가 반대쪽 글에 있는가.** 증거가 되는 꼴은 넷이고
 * (`NotationPattern`), 원문에만 있는 숫자와 번역문에만 있는 숫자가 하나도 남지 않을 때만 `notation_like` 다.
 * 숫자가 통째로 사라지거나(각주 번호, 다른 셀로 옮겨 간 숫자) 근거 없이 새로 생기면 설명되지 않고 `other` 로 남는다.
 *
 * 낱말 증거는 문자열로만 본다. `1` 이 빠지고 번역문 어딘가에 `one` 이 있으면 설명된 것으로 치므로,
 * 각주 번호 `1)` 이 빠졌는데 같은 셀에 `first` 가 있는 경우는 가려내지 못한다.
 */
export function gradeNumberChange(source: string, target: string, diff: NumberDiff = numberDiff(source, target)): NumberChangeGrade {
  const sourceText = source.normalize("NFKC");
  const targetText = target.normalize("NFKC");
  const sourceRuns = runsWithTail(sourceText);
  const targetRuns = runsWithTail(targetText);
  const sourceOnly = [...diff.sourceOnly];
  const targetOnly = [...diff.targetOnly];
  const explained: NumberChangeGrade["explained"] = [];
  const take = (list: string[], run: string): boolean => {
    const at = list.indexOf(run);
    if (at === -1) return false;
    list.splice(at, 1);
    return true;
  };

  // 1. 두 자리 연도 → 네 자리. 원문에서 연도로 쓰였다는 표시(앞의 아포스트로피나 뒤의 `년`)가 있어야 한다.
  //    `’19.1월` 처럼 연도와 달이 한 덩어리면 달 이름도 번역문에 있어야 한다.
  for (const run of [...sourceOnly]) {
    const shape = /^(\d{2})(?:\.(\d{1,2}))?$/.exec(run);
    if (!shape) continue;
    const marked = sourceRuns.some((entry) => entry.run === run && (/['’‘`]\s?$/.test(entry.before) || (shape[2] === undefined ? /^년/.test(entry.tail) : /^월/.test(entry.tail))));
    if (!marked) continue;
    const full = [`20${shape[1]}`, `19${shape[1]}`].find((candidate) => targetOnly.includes(candidate));
    if (!full) continue;
    if (shape[2] !== undefined && !hasMonthName(targetText, Number(shape[2]))) continue;
    take(sourceOnly, run);
    take(targetOnly, full);
    explained.push({ pattern: "year_expansion", source: run, target: full });
  }

  // 2. 수 단위 낱말을 숫자로 풀어 씀. 값이 같아야 한다: 원문 수 × 수 단위 = 번역문 수 × 영어 배율.
  for (const run of [...sourceOnly]) {
    const scales = sourceRuns.filter((entry) => entry.run === run).map((entry) => sourceScale(entry.tail)).filter((scale) => scale > 1);
    const value = Number(run);
    if (scales.length === 0 || Number.isNaN(value)) continue;
    const partner = targetRuns.find((entry) => targetOnly.includes(entry.run) && scales.some((scale) => close(value * scale, Number(entry.run) * targetScale(entry.tail))));
    if (!partner) continue;
    take(sourceOnly, run);
    take(targetOnly, partner.run);
    explained.push({ pattern: "unit_expansion", source: run, target: partner.run });
  }
  const standalone = standaloneMultipliers(sourceText);
  for (const entry of targetRuns) {
    if (!targetOnly.includes(entry.run)) continue;
    const at = standalone.findIndex((multiplier) => close(multiplier, Number(entry.run) * targetScale(entry.tail)));
    if (at === -1) continue;
    standalone.splice(at, 1);
    take(targetOnly, entry.run);
    explained.push({ pattern: "unit_expansion", source: null, target: entry.run });
  }

  // 3. `N월` → 달 이름.
  for (const run of [...sourceOnly]) {
    if (!/^\d{1,2}$/.test(run)) continue;
    if (!sourceRuns.some((entry) => entry.run === run && /^월/.test(entry.tail))) continue;
    if (!hasMonthName(targetText, Number(run))) continue;
    take(sourceOnly, run);
    explained.push({ pattern: "month_name", source: run, target: null });
  }

  // 4. 작은 수 → 같은 값의 영어 낱말. 낱말이 나온 횟수만큼만 설명된다.
  const wordBudget = new Map<string, number>();
  for (const run of [...sourceOnly]) {
    const words = /^\d{1,2}$/.test(run) ? NUMBER_WORDS[Number(run)] : undefined;
    if (!words) continue;
    const left = wordBudget.get(run) ?? countWord(targetText, words);
    if (left <= 0) continue;
    wordBudget.set(run, left - 1);
    take(sourceOnly, run);
    explained.push({ pattern: "number_word", source: run, target: null });
  }

  return { grade: sourceOnly.length + targetOnly.length === 0 ? "notation_like" : "other", explained, unexplained: { sourceOnly, targetOnly } };
}

/** 등급 규칙을 읽는 사람을 위한 한 줄 설명. 결과 파일에 같이 싣는다. */
export const GRADE_BASIS =
  "notation_like = 달라진 숫자가 전부 값이 그대로인 표기 변환(수 단위 풀어 쓰기, 작은 수를 영어 낱말로, 두 자리 연도를 네 자리로, N월을 달 이름으로)으로 설명된다. other = 설명되지 않는 숫자가 남는다. 등급은 검토 순서용이고 notation_like 도 검토 대상이다.";
