import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { z } from "zod";
import { dataDir } from "./paths.js";

/**
 * 단위 셀: 숫자 없이 단위 낱말만으로 된 표 셀 (`억 원`, `천 톤`, `톤 CO₂e/억 원`).
 * 모델에 보내면 `KRW 100 million` 처럼 숫자를 품은 표기로 돌아와 숫자 대조에 걸린다. 그래서 모델에 보내지 않고
 * `data/docpipe/` 의 고정 번역 표로 옮긴다. 단위 셀이지만 표에 없는 낱말이 있으면 모델에 보내고 그 사실을 결과에 남긴다.
 */
const unitTableSchema = z.object({
  synthetic: z.boolean(),
  note: z.string(),
  sourceLang: z.literal("ko"),
  targetLang: z.literal("en"),
  grammar: z.object({
    note: z.string(),
    /** 수 단위 낱말 (`천`, `만`, `억`). */
    multipliers: z.array(z.string().min(1)).min(1),
    /** 계량 단위 낱말 (`원`, `톤`, `건`). */
    measures: z.array(z.string().min(1)).min(1),
  }),
  /** 고정 번역. `ko` 는 띄어쓰기를 무시하고 맞춘다 (`억 원` = `억원`). */
  phrases: z.array(z.object({ ko: z.string().min(1), en: z.string().min(1), synthetic: z.boolean().optional() })).min(1),
});

export interface UnitTable {
  path: string;
  synthetic: boolean;
  sha256: string;
  /** 띄어쓰기를 지운 국문 낱말 묶음 → 영문 */
  phrases: Map<string, string>;
  /** 수 단위 낱말만으로 된 토큰 (`억`, `십억`). */
  multiplierToken: RegExp;
  /** 계량 단위 낱말 하나로 된 토큰 (`원`, `톤`). */
  measureToken: RegExp;
}

export class UnitTableError extends Error {
  override name = "UnitTableError";
}

export const defaultUnitTablePath = join(dataDir, "unit-cells.synthetic.json");

const squeeze = (text: string): string => text.normalize("NFKC").replace(/\s+/g, "");
const alternation = (words: string[]): string => [...words].sort((a, b) => b.length - a.length).map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");

export function parseUnitTable(raw: unknown, path: string, source: string): UnitTable {
  const parsed = unitTableSchema.safeParse(raw);
  if (!parsed.success) throw new UnitTableError(`${path}: ${z.prettifyError(parsed.error)}`);
  const { grammar } = parsed.data;
  const multiplierToken = new RegExp(`^(?:${alternation(grammar.multipliers)})+$`, "u");
  const measureToken = new RegExp(`^(?:${alternation(grammar.measures)})$`, "u");
  const unitPhrase = new RegExp(`^(?:${alternation(grammar.multipliers)})*(?:${alternation(grammar.measures)})$`, "u");
  const phrases = new Map<string, string>();
  for (const phrase of parsed.data.phrases) {
    const key = squeeze(phrase.ko);
    if (phrases.has(key)) throw new UnitTableError(`${path}: 국문 단위가 겹친다: ${phrase.ko}`);
    // 가상 표는 파일과 항목 양쪽에 표시가 있어야 한다 (용어집과 같은 규칙).
    if (parsed.data.synthetic && phrase.synthetic !== true) throw new UnitTableError(`${path}: 가상 표의 항목 ${phrase.ko} 에 synthetic 표시가 없다`);
    if (!parsed.data.synthetic && phrase.synthetic) throw new UnitTableError(`${path}: 가상 항목 ${phrase.ko} 는 synthetic 파일에 둬야 한다`);
    // 표의 항목은 (수 단위 낱말)* + 계량 단위 낱말 꼴이어야 한다. 일반 낱말을 표에 넣어 고정 번역하는 길을 막는다.
    if (!unitPhrase.test(key)) throw new UnitTableError(`${path}: 항목 ${phrase.ko} 가 grammar 의 낱말로 된 단위가 아니다`);
    phrases.set(key, phrase.en);
  }
  return { path, synthetic: parsed.data.synthetic, sha256: createHash("sha256").update(source).digest("hex"), phrases, multiplierToken, measureToken };
}

export function loadUnitTable(path: string = defaultUnitTablePath): UnitTable {
  const source = readFileSync(path, "utf8");
  return parseUnitTable(JSON.parse(source), path, source);
}

export type UnitCell =
  /** 단위 셀이 아니다. */
  | { kind: "not_unit" }
  /** 단위 셀이고 표에서 옮겼다. 모델에 보내지 않는다. */
  | { kind: "fixed"; text: string }
  /** 단위 셀인데 표에 없는 낱말이 있다. 모델에 보내고 결과에 표시한다. */
  | { kind: "no_fixed_translation"; unknown: string[] };

const HANGUL_OR_HAN = /[\p{Script=Hangul}\p{Script=Han}]/u;
const LETTER = /\p{L}/u;
const DIGIT_ONLY = /^[^\p{L}]*\p{Nd}[^\p{L}]*$/u;
const WRAPPER = /^\(\s*단위\s*:\s*(.+?)\s*\)$|^단위\s*:\s*(.+)$/u;

/** 구분자(빗금, 쌍점, 괄호, 띄어쓰기)의 전각 꼴만 반각으로 바꾼다. 단위 기호의 글자(`CO₂e` 의 아래 첨자)는 건드리지 않는다. */
function foldSeparators(text: string): string {
  return text.replace(/[\u2215\uFF0F]/g, "/").replace(/\uFF1A/g, ":").replace(/\uFF08/g, "(").replace(/\uFF09/g, ")").replace(/\s+/g, " ").trim();
}

/**
 * 셀이 단위 셀인지 문법으로 가른다.
 *
 * - 셀은 `/` 로 나뉜 부분들이고, 부분은 띄어쓰기로 나뉜 토큰들이다. `(단위: …)` 로 감싼 꼴도 받는다.
 * - 토큰은 셋 중 하나여야 한다 (NFKC 정규화 뒤에 본다).
 *     수 단위 토큰   수 단위 낱말만 (`억`, `십억`)
 *     계량 단위 토큰 계량 단위 낱말 하나 (`원`, `톤`), 또는 붙여 쓴 꼴이 고정 번역 표에 있는 것 (`억원`, `천톤`)
 *     단위 기호      한글·한자가 없고 글자가 있거나 `%` 다 (`CO₂e`, `MWh`, `%`). 숫자만 있는 토큰은 안 된다
 * - 이어진 한글 토큰 묶음은 계량 단위로 끝나거나 바로 뒤에 단위 기호가 와야 한다 (`천` 만 있는 셀은 단위 셀이 아니다).
 * - `/` 로 나뉜 한 부분 안에서 단위 기호는 한글 토큰 뒤에만 온다 (`천 톤 CO₂e`). 로마자 낱말 뒤에 한글 단위가 오는 셀
 *   (`ESG 건`, `Samsung 명`)은 항목명이지 단위가 아니다.
 *
 * 붙여 쓴 꼴을 표에 있는 것으로만 받는 까닭: `조건`, `조명`, `조회`, `만일` 처럼 수 단위 낱말 + 계량 단위 낱말로
 * 읽히는 일반 낱말이 있다. 일반 낱말(`사업장`, `천연가스`, `만족도`, `건수`)은 어느 토큰 꼴에도 맞지 않는다.
 */
export function unitCell(text: string, table: UnitTable): UnitCell {
  const folded = foldSeparators(text);
  if (!HANGUL_OR_HAN.test(folded)) return { kind: "not_unit" };
  const wrapped = WRAPPER.exec(folded);
  const body = wrapped ? ((wrapped[1] ?? wrapped[2]) as string) : folded;

  const parts: string[] = [];
  const unknown: string[] = [];
  for (const part of body.split("/")) {
    const tokens = part.split(" ").filter((token) => token !== "");
    if (tokens.length === 0) return { kind: "not_unit" };
    const rendered: string[] = [];
    let run: string[] = [];
    let runEndsWithMeasure = false;
    let symbolSeen = false;
    const closeRun = (followedBySymbol: boolean): boolean => {
      if (run.length === 0) return true;
      if (!runEndsWithMeasure && !followedBySymbol) return false;
      const fixed = table.phrases.get(run.join(""));
      if (fixed === undefined) unknown.push(run.join(" "));
      else rendered.push(fixed);
      run = [];
      return true;
    };
    for (const raw of tokens) {
      const token = raw.normalize("NFKC");
      if (HANGUL_OR_HAN.test(token)) {
        if (symbolSeen) return { kind: "not_unit" };
        const measure = table.measureToken.test(token) || table.phrases.has(token);
        if (!measure && !table.multiplierToken.test(token)) return { kind: "not_unit" };
        run.push(token);
        runEndsWithMeasure = measure;
        continue;
      }
      if (DIGIT_ONLY.test(token) || !(LETTER.test(token) || token === "%")) return { kind: "not_unit" };
      if (!closeRun(true)) return { kind: "not_unit" };
      symbolSeen = true;
      rendered.push(raw);
    }
    if (!closeRun(false)) return { kind: "not_unit" };
    parts.push(rendered.join(" "));
  }
  if (unknown.length > 0) return { kind: "no_fixed_translation", unknown };
  const joined = parts.join("/");
  return { kind: "fixed", text: wrapped ? (wrapped[1] !== undefined ? `(Unit: ${joined})` : `Unit: ${joined}`) : joined };
}
