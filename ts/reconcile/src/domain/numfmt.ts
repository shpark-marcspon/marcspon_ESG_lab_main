import { roundDecimal } from "kernel";
import { dec, neg, shift10, show } from "./dec.js";

// 엑셀 숫자 서식의 일부를 적용해 「화면에 보이는 표기」를 만든다. 저장 값은 건드리지 않는다.
// 다루는 것: General, 자릿수(0, 0.00), 천 단위 쉼표(#,##0), 퍼센트, 끝 쉼표(천 단위로 줄여 보이기), 음수 구획(괄호·부호), 글자 접두·접미.
// 다루지 않는 것(분수, 지수, `?` 자리 맞춤, 조건 구획, 글자 서식)은 null 을 돌려주고 지어내지 않는다.

/** 내장 서식 번호 → 서식 코드. ECMA-376 Part 1 §18.8.30 (numFmt) 의 표. */
export const BUILTIN_FORMATS: Record<number, string> = {
  0: "General",
  1: "0",
  2: "0.00",
  3: "#,##0",
  4: "#,##0.00",
  9: "0%",
  10: "0.00%",
  11: "0.00E+00",
  12: "# ?/?",
  13: "# ??/??",
  14: "mm-dd-yy",
  15: "d-mmm-yy",
  16: "d-mmm",
  17: "mmm-yy",
  18: "h:mm AM/PM",
  19: "h:mm:ss AM/PM",
  20: "h:mm",
  21: "h:mm:ss",
  22: "m/d/yy h:mm",
  37: "#,##0 ;(#,##0)",
  38: "#,##0 ;[Red](#,##0)",
  39: "#,##0.00;(#,##0.00)",
  40: "#,##0.00;[Red](#,##0.00)",
  45: "mm:ss",
  46: "[h]:mm:ss",
  47: "mmss.0",
  48: "##0.0E+0",
  49: "@",
};

function stripLiterals(code: string): string {
  return code.replace(/"[^"]*"/g, "").replace(/\\./g, "").replace(/\[[^\]]*\]/g, "");
}

/** 날짜·시간 서식인가. 날짜 셀의 저장 값(일련번호)은 값 후보가 아니다. */
export function isDateFormat(code: string): boolean {
  if (/^general$/i.test(code.trim())) return false;
  if (/\[(h+|m+|s+)\]/i.test(code)) return true;
  return /[ymdhs]/i.test(stripLiterals(code).replace(/E[+-]/gi, "").replace(/AM\/PM|A\/P/gi, "x"));
}

function splitSections(code: string): string[] {
  const sections: string[] = [];
  let current = "";
  let quoted = false;
  for (let i = 0; i < code.length; i++) {
    const ch = code[i] as string;
    if (ch === '"') quoted = !quoted;
    if (ch === "\\" && !quoted) {
      current += ch + (code[i + 1] ?? "");
      i += 1;
      continue;
    }
    if (ch === ";" && !quoted) {
      sections.push(current);
      current = "";
    } else current += ch;
  }
  sections.push(current);
  return sections;
}

function group(intDigits: string): string {
  return intDigits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

export interface Rendered {
  display: string | null;
  status: "rendered" | "general_approximated" | "unsupported_format";
}

/** 저장 값(십진 문자열)에 서식 코드를 적용한다. */
export function renderNumber(stored: string, formatCode: string): Rendered {
  const value = dec(stored);
  if (/^general$/i.test(formatCode.trim())) {
    // General 은 대략 유효 숫자 11자리까지 보인다. 정확한 규칙(칸 너비에 따른 지수 표기)은 흉내 내지 않는다.
    const digits = stored.replace(/[-.]/g, "").replace(/^0+/, "");
    if (digits.length <= 11) return { display: show(value), status: "general_approximated" };
    const intLength = stored.replace(/^-/, "").split(".")[0]?.replace(/^0+/, "").length ?? 0;
    const places = Math.max(0, 11 - Math.max(1, intLength));
    return { display: show(dec(roundDecimal(value, places, "half_up"))), status: "general_approximated" };
  }
  const sections = splitSections(formatCode);
  const negative = value.coef < 0n;
  const zero = value.coef === 0n;
  let section = sections[0] as string;
  let showMinus = negative;
  if (negative && sections.length >= 2) {
    section = sections[1] as string;
    showMinus = false;
  } else if (zero && sections.length >= 3) section = sections[2] as string;

  // 글자(따옴표, 역슬래시)는 자리표시로 바꿔 두고 숫자 자리만 본다.
  const literals: string[] = [];
  let body = section
    .replace(/\[[^\]]*\]/g, "")
    .replace(/"([^"]*)"/g, (_, text: string) => `\u0001${literals.push(text) - 1}\u0002`)
    .replace(/\\(.)/g, (_, ch: string) => `\u0001${literals.push(ch) - 1}\u0002`)
    .replace(/_./g, "")
    .replace(/\*./g, "");
  const pattern = /[#0][#0,]*(?:\.[#0]+)?,*|\.[#0]+/.exec(body);
  if (!pattern) return { display: null, status: "unsupported_format" };
  const outside = body.slice(0, pattern.index) + body.slice(pattern.index + pattern[0].length);
  if (/[#0?@E/]/i.test(outside.replace(/\u0001\d+\u0002/g, ""))) return { display: null, status: "unsupported_format" };
  if (/[a-zA-Z가-힣]/.test(outside.replace(/\u0001\d+\u0002/g, ""))) return { display: null, status: "unsupported_format" };

  const [intPattern = "", fracPattern = ""] = pattern[0].replace(/,+$/, "").split(".");
  const trailingCommas = /,+$/.exec(pattern[0])?.[0].length ?? 0;
  const percents = (outside.match(/%/g) ?? []).length;
  const decimals = fracPattern.length;
  const requiredDecimals = fracPattern.replace(/#+$/, "").length;
  const grouping = intPattern.includes(",");

  const magnitude = negative ? neg(value) : value;
  const scaled = shift10(magnitude, 2 * percents - 3 * trailingCommas);
  let text = roundDecimal(scaled, decimals, "half_up");
  let [intText = "0", fracText = ""] = text.split(".");
  while (fracText.length > requiredDecimals && fracText.endsWith("0")) fracText = fracText.slice(0, -1);
  if (grouping) intText = group(intText);
  if (!/0/.test(intPattern) && intText === "0" && fracText !== "") intText = "";
  text = fracText === "" ? intText : `${intText}.${fracText}`;
  const roundedToZero = /^[0.,]*$/.test(text);
  body = body.slice(0, pattern.index) + text + body.slice(pattern.index + pattern[0].length);
  body = body.replace(/\u0001(\d+)\u0002/g, (_, index: string) => literals[Number(index)] ?? "");
  return { display: `${showMinus && !roundedToZero ? "-" : ""}${body}`.trim(), status: "rendered" };
}

/** `<v>` 의 글자를 십진 문자열로. 지수 표기(`1.5E-05`)는 자리 옮김으로 푼다. 못 읽으면 null. */
export function storedToDecimal(raw: string): string | null {
  const match = /^(-?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/.exec(raw.trim());
  if (!match) return null;
  const [, sign = "", intPart = "0", frac = "", exponent] = match;
  const plain = `${sign}${intPart.replace(/^0+(?=\d)/, "")}${frac === "" ? "" : `.${frac}`}`;
  const value = dec(plain === "-0" ? "0" : plain);
  return show(exponent === undefined ? value : shift10(value, Number.parseInt(exponent, 10)));
}
