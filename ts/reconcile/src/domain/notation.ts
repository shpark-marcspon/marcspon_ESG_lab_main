// 숫자 표기 규칙. 「이 글자가 값 후보인가, 값이면 얼마인가」를 정하는 유일한 자리다.
// 값은 십진 문자열로만 낸다 (JS number 를 거치지 않는다). 글자를 고치거나 추측하지 않는다.
//
// 표 셀은 셀 전체를 한 덩어리로 읽는다 (`classifyCell`).
// 문장과 글 셀은 그 안의 숫자 덩어리를 하나씩 읽는다 (`scanNumbers`).

/** 표 셀 하나의 갈래. `value` 만 값 후보다. */
export type CellKind =
  | "blank"
  | "value" // 숫자 하나 (부호·괄호·쉼표·%·단위 접미 허용)
  | "no_value_marker" // `-`, `N/A`, `해당없음`. 0 이 아니라 「값 없음」이다
  | "year" // `2025`, `FY2025`, `2025년`
  | "code" // 앞자리 0 (`04`), 조항 번호 (`6(1)`), 버전 (`1.2.3`)
  | "date" // `2025.7.1`, `2025-07-01`
  | "range" // `10~20`, `65-66`, `3~17%`
  | "multi_number" // 숫자가 둘 이상 (`116,818 15,649`, `64, 216`)
  | "text_with_digits" // 글 속에 숫자가 섞임 (`기준연도 대비 42% 감축`)
  | "text";

export type SignRule = "none" | "minus" | "plus" | "paren_negative" | "triangle_negative" | "triangle_ambiguous";

export interface ParsedNumber {
  /** 표기 그대로의 수. 수 단위 낱말(천, 억)은 곱하지 않았다. 십진 문자열. */
  value: string;
  /** 표시된 소수 자릿수. 반올림 허용폭의 근거다. */
  decimals: number;
  sign: SignRule;
  percent: boolean;
  /** 숫자 뒤에 붙어 있던 단위 글자 (`%`, `명`, `만 톤`). 없으면 null. */
  unitSuffix: string | null;
  flags: NotationFlag[];
}

export type NotationFlag =
  | "paren_negative_assumed" // (123) 을 음수로 읽었다. 괄호 속 부분 수일 수도 있다
  | "triangle_negative" // △ 를 음수로 읽었다 (재무제표 관례)
  | "sign_ambiguous" // ▲ ▼ 는 증감 표시라 부호를 정할 수 없다
  | "year_like" // 1900~2100 의 쉼표 없는 네 자리 정수. 연도일 수 있다
  | "percent_format_scaled"; // 엑셀의 퍼센트 서식 셀: 저장 값(0.123)에 100 을 곱해 % 단위의 값(12.3)으로 읽었다

export interface CellNotation {
  kind: CellKind;
  number: ParsedNumber | null;
}

const DASHES = /^[-–—−‐‑ㅡ]+$/;
const NO_VALUE_WORDS = new Set(["n/a", "na", "해당없음", "해당사항없음", "미해당", "미집계", "미산정"]);
const YEAR = /^(FY)?['’]?(19|20)\d{2}(년|년도)?$/;
const NUMBER_BODY = String.raw`\d{1,3}(?:,\d{3})+(?!\d)(?:\.\d+)?|\d+(?:\.\d+)?`;
const SINGLE = new RegExp(String.raw`^(?<lead>[-−–+△▲▼]?)\s?(?<open>\(?)(?<num>${NUMBER_BODY})(?<close>\)?)\s?(?<suffix>.*)$`, "u");
const DATE = /^(19|20)\d{2}[.\-/]\s?\d{1,2}([.\-/]\s?\d{1,2})?\.?$/;
const RANGE = new RegExp(String.raw`^[-−+]?(?:${NUMBER_BODY})\s?%?\s?[~∼〜\-–]\s?[-−+]?(?:${NUMBER_BODY})\s?(%|%p)?$`, "u");
const DIGIT = /\d/;
const LETTER = /\p{L}/u;

/** 숫자 뒤에 와도 「숫자 하나짜리 셀」로 보는 단위 접미. 낱말 목록이 아니라 꼴이다: 짧고, 숫자가 없거나 단위 기호 안의 숫자뿐이다. */
const UNIT_SUFFIX = /^(%p|%|배|[가-힣]{1,3}(?: [가-힣]{1,2})?|[A-Za-zµ㎡㎥℃°][A-Za-z0-9₂²³/·]{0,9}|[가-힣]{1,2} ?[A-Za-z][A-Za-z0-9₂/]{0,9})$/u;

export function normalizeText(text: string): string {
  return text.normalize("NFKC").replace(/[\u0000-\u001f]/g, " ").replace(/\s+/g, " ").trim();
}

function digitsToDecimal(num: string): { value: string; decimals: number } {
  const plain = num.replace(/,/g, "");
  const [intPart = "0", frac = ""] = plain.split(".");
  // 앞자리 0 은 값에서 지운다 (`0.5` 는 그대로, `007` 은 여기 오지 않는다).
  const int = intPart.replace(/^0+(?=\d)/, "");
  return { value: frac === "" ? int : `${int}.${frac}`, decimals: frac.length };
}

function applySign(value: string, negative: boolean): string {
  if (!negative || /^0(\.0+)?$/.test(value)) return value;
  return `-${value}`;
}

function isYearInteger(num: string): boolean {
  return /^(19|20)\d{2}$/.test(num) || num === "2100";
}

/** 표 셀 하나를 읽는다. */
export function classifyCell(raw: string): CellNotation {
  const text = normalizeText(raw);
  if (text === "") return { kind: "blank", number: null };
  if (DASHES.test(text) || NO_VALUE_WORDS.has(text.replace(/\s/g, "").toLowerCase())) return { kind: "no_value_marker", number: null };
  if (!DIGIT.test(text)) return { kind: "text", number: null };
  const compact = text.replace(/\s/g, "");
  if (YEAR.test(compact) && !/^\d{4}$/.test(compact)) return { kind: "year", number: null };
  if (DATE.test(text)) return { kind: "date", number: null };
  if (/^\d+(\.\d+){2,}$/.test(text)) return { kind: "code", number: null };

  const single = SINGLE.exec(text);
  if (single?.groups) {
    const { lead = "", open = "", num = "", close = "", suffix = "" } = single.groups;
    const parenOk = (open === "(") === (close === ")");
    const trimmedSuffix = suffix.trim();
    const suffixOk = trimmedSuffix === "" || (UNIT_SUFFIX.test(trimmedSuffix) && !/^(년|년도|월|일|분기|차|호|조|항|기|회차|위|등급|단계|번|쪽|페이지)$/.test(trimmedSuffix));
    if (parenOk && suffixOk && !(open === "(" && lead !== "")) {
      // 앞자리 0 인 정수 (`04`, `007`) 는 번호다.
      if (/^0\d/.test(num)) return { kind: "code", number: null };
      const flags: NotationFlag[] = [];
      let sign: SignRule = "none";
      let negative = false;
      if (open === "(") {
        sign = "paren_negative";
        negative = true;
        flags.push("paren_negative_assumed");
      } else if (lead === "-" || lead === "−" || lead === "–") {
        sign = "minus";
        negative = true;
      } else if (lead === "+") {
        sign = "plus";
      } else if (lead === "△") {
        sign = "triangle_negative";
        negative = true;
        flags.push("triangle_negative");
      } else if (lead === "▲" || lead === "▼") {
        sign = "triangle_ambiguous";
        flags.push("sign_ambiguous");
      }
      const { value, decimals } = digitsToDecimal(num);
      const percent = trimmedSuffix === "%" || trimmedSuffix === "%p";
      if (trimmedSuffix === "" && sign === "none" && isYearInteger(num)) flags.push("year_like");
      return {
        kind: "value",
        number: { value: applySign(value, negative), decimals, sign, percent, unitSuffix: trimmedSuffix === "" ? null : trimmedSuffix, flags },
      };
    }
  }
  if (YEAR.test(compact)) return { kind: "year", number: null };
  if (RANGE.test(text)) return { kind: "range", number: null };
  if (/^\d+(\(\d+\))+(\([가-힣]\))?\d{0,2}$/u.test(compact)) return { kind: "code", number: null };
  if (!LETTER.test(text.replace(/%p/g, ""))) return { kind: "multi_number", number: null };
  return { kind: "text_with_digits", number: null };
}

// ── 문장 속 숫자 ────────────────────────────────────────────────────────────

/** 글 속 숫자 덩어리의 갈래. `value` 만 값 후보다. */
export type TokenKind =
  | "value"
  | "year" // 2025년, 2050, ’25
  | "date_part" // 3월, 15일, 2025.7.1 의 조각
  | "identifier" // Scope 1, GRI 305-1, CO2, 02-2073-7114, URL 속 숫자
  | "ordinal" // 제3조, 1차, 2단계, 3위
  | "marker" // 각주·목록 번호 `1)`, `(2)`
  | "range_part"; // 10~20 의 양 끝

export interface NumberToken {
  /** 원문 글자 그대로 (부호와 % 포함). */
  raw: string;
  /** 원문에서의 시작·끝 (UTF-16 자리). */
  start: number;
  end: number;
  /** 같은 원문 글자가 이 글에서 몇 번째로 나온 것인지 (1부터). */
  occurrence: number;
  kind: TokenKind;
  number: ParsedNumber | null;
}

// 숫자 바로 뒤가 아니면 어디서든 덩어리를 시작한다 (`10,20,30%` 의 20 과 30 도 남긴다).
const TOKEN = new RegExp(String.raw`(?<!\d)(${NUMBER_BODY})(%p|%)?`, "gu");
/** 숫자에 붙은 로마자 단위 꼴 (`28MW`, `34.4ton`, `940ppm`). 한 글자(`3R`)나 글자 뒤에 숫자가 오는 꼴(`24X365`)은 식별자다. */
const LATIN_UNIT_AFTER = /^([A-Za-zµ][A-Za-z₂]{1,9}(?:\/[A-Za-z]{1,6})?)(?![A-Za-z0-9])/u;
const LABEL_BEFORE = /(Scope|Category|CAT|Cat|GRI|ISO|IFRS|SASB|TCFD|KSSB|ESRS|SDGs?|Tier|Level|Phase|No\.?|Step|Type|Class|Annex|스코프|카테고리)[\s.\-]?$/u;
const ORDINAL_AFTER = /^(차|단계|위\b|등급|호|번|조|항|기\b|회차|순위|세대|종\b)/u;
const UNIT_AFTER = new RegExp(
  String.raw`^ ?((?:십|백|천|만|억|조){0,3} ?(?:원|톤|명|건|개월|개사|개소|개|시간|배|회|년|일|주|kWh|MWh|GWh|TJ|GJ|MJ|tCO2eq|tCO2e|tCO₂eq|tCO₂e|kg|㎡|㎥|m3|m2|km|L|ℓ)(?=$|[^A-Za-z0-9가-힣]|(?:이다|이며|이고|입니다|으로|에서|까지|부터|이|가|은|는|을|를|의|과|와|로|에|씩|도)(?![가-힣])|(?:이다|이며|이고|입니다)))`,
  "u",
);

function countOccurrences(text: string, needle: string, before: number): number {
  let count = 0;
  let at = text.indexOf(needle);
  while (at !== -1 && at <= before) {
    count += 1;
    at = text.indexOf(needle, at + 1);
  }
  return count;
}

/** 글 한 덩어리에서 숫자 덩어리를 전부 찾아 갈래를 붙인다. 숫자 글자는 어느 덩어리엔가 들어간다 (버리지 않는다). */
export function scanNumbers(text: string): NumberToken[] {
  const tokens: NumberToken[] = [];
  const urlSpans = [...text.matchAll(/\S*(?:https?:|www\.|@)\S*/g)].map((m) => [m.index, m.index + m[0].length] as const);
  const dateSpans = [...text.matchAll(/(?<!\d)(?:19|20)\d{2}[.\-/] ?\d{1,2}(?:[.\-/] ?\d{1,2})?(?![\d,])/g)].map((m) => [m.index, m.index + m[0].length] as const);
  for (const match of text.matchAll(TOKEN)) {
    const num = match[1] as string;
    const pct = match[2] ?? "";
    let start = match.index;
    const end = start + match[0].length;
    const before = text.slice(Math.max(0, start - 12), start);
    const after = text.slice(end, end + 14);
    const prev = before.slice(-1);
    const next = after.slice(0, 1);

    let kind: TokenKind = "value";
    let lead = "";
    if (urlSpans.some(([a, b]) => start >= a && end <= b)) kind = "identifier";
    else if (dateSpans.some(([a, b]) => start >= a && start < b)) kind = "date_part";
    else if (/[A-Za-z₂]/.test(prev) || LABEL_BEFORE.test(before)) kind = "identifier";
    else if ((prev === "-" && /\d-$/.test(before)) || (next === "-" && /^-\d/.test(after))) kind = "identifier";
    else if (prev === "." && /\d\.$/.test(before)) kind = /(19|20)\d{2}\.(\d{1,2}\.)?$/.test(before) ? "date_part" : "identifier";
    else if (prev === "~" || prev === "∼" || /^ ?[~∼〜]/.test(after)) kind = "range_part";
    else if (pct === "" && next === ")" && (/[가-힣A-Za-z]/.test(prev) || prev === "" || /[(\s·,]/.test(prev)) && num.length <= 2 && !num.includes(".")) kind = "marker";
    else if (pct === "" && /^\d{1,2}$/.test(num) && /^\.\s+\D/.test(after) && /(^|\n)[\s·•\-]*$/.test(text.slice(0, start))) kind = "marker";
    else if (pct === "" && /제$/.test(before)) kind = "ordinal";
    // `333.6조 원`, `187조 9,673억 원` 의 조는 순번(제3조)이 아니라 수 단위다.
    else if (pct === "" && ORDINAL_AFTER.test(after) && !UNIT_AFTER.test(after) && !/^조 ?\d/.test(after) && !num.includes(",")) kind = "ordinal";
    else if (pct === "" && /^(년|년도)(?!간|차| ?이상| ?이하| ?동안| ?연속| ?만에)/u.test(after) && isYearInteger(num)) kind = "year";
    else if (pct === "" && /['’]$/.test(before) && /^\d{2}$/.test(num)) kind = "year";
    else if (pct === "" && /^(월|일)(?!간| ?평균)/u.test(after) && /^\d{1,2}$/.test(num) && (after.startsWith("월") || /월 ?$/.test(before))) kind = "date_part";
    else if (pct === "" && isYearInteger(num) && !UNIT_AFTER.test(after) && !/[A-Za-z]/.test(next)) kind = "year";
    else if (pct === "" && /^[A-Za-z]/.test(after) && !UNIT_AFTER.test(after) && !LATIN_UNIT_AFTER.test(after)) kind = "identifier";
    else if (/^0\d/.test(num)) kind = "identifier";

    let number: ParsedNumber | null = null;
    if (kind === "value") {
      const flags: NotationFlag[] = [];
      let sign: SignRule = "none";
      let negative = false;
      // 부호는 숫자 바로 앞에 붙어 있고 그 앞이 글자·숫자가 아닐 때만 숫자의 것으로 본다.
      const signChar = /(^|[\s(])([-−△▲▼+])$/u.exec(before)?.[2];
      if (signChar) {
        lead = signChar;
        if (signChar === "-" || signChar === "−") {
          sign = "minus";
          negative = true;
        } else if (signChar === "+") sign = "plus";
        else if (signChar === "△") {
          sign = "triangle_negative";
          negative = true;
          flags.push("triangle_negative");
        } else {
          sign = "triangle_ambiguous";
          flags.push("sign_ambiguous");
        }
      }
      const { value, decimals } = digitsToDecimal(num);
      const unit = pct !== "" ? pct : (UNIT_AFTER.exec(after)?.[1]?.trim() ?? LATIN_UNIT_AFTER.exec(after)?.[1] ?? null);
      number = { value: applySign(value, negative), decimals, sign, percent: pct !== "", unitSuffix: unit, flags };
    }
    start -= lead.length;
    const raw = text.slice(start, end);
    tokens.push({ raw, start, end, occurrence: countOccurrences(text, raw, start), kind, number });
  }
  return tokens;
}

/** 원문 글자 `quote` 가 `text` 에서 `occurrence` 번째로 나오는 자리. 없으면 null. */
export function locateQuote(text: string, quote: string, occurrence: number): { start: number; end: number; total: number } | null {
  if (quote === "" || !Number.isInteger(occurrence) || occurrence < 1) return null;
  let total = 0;
  let found: number | null = null;
  let at = text.indexOf(quote);
  while (at !== -1) {
    total += 1;
    if (total === occurrence) found = at;
    at = text.indexOf(quote, at + 1);
  }
  return found === null ? null : { start: found, end: found + quote.length, total };
}
