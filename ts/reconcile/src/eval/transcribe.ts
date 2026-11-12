import type { CallRequest, Tags } from "llm";
import { z } from "zod";
import type { LabelUnit } from "../domain/label.js";

// 옮겨 쓰기 비교 시험. 「AI 가 숫자째 옮겨 적게 하면 얼마나 틀리는가」를 재는 측정용 경로다.
// 여기서 나온 글자는 대조(짝짓기·비교)에 쓰이지 않는다. 대조에 쓰이는 숫자는 언제나 코드가 원문에서 읽은 것이다.
// 입력은 뜻 붙이기와 같은 표(셀 ID 가 붙은 격자)이고, 모델은 대상 셀마다 그 칸의 글자를 그대로 적어 돌려준다.

export const TRANSCRIBE_PROMPT = "reconcile/transcribe@1";

export const transcribeAnswerSchema = z.object({
  cells: z.array(
    z.object({
      /** 셀 ID (`r3c4`). */
      target: z.string(),
      /** 그 칸의 글자를 그대로. */
      text: z.string(),
    }),
  ),
});

export type TranscribeAnswer = z.infer<typeof transcribeAnswerSchema>;

export interface TranscribeSettings {
  /** 셀 하나의 출력 토큰 어림 (가정). */
  outputTokensPerCell: number;
  outputTokensOverhead: number;
  outputCapFactor: number;
}

/** 근거: 셀 하나의 JSON(`{"target":"r10c10","text":"10,688,220"}`)은 40자 안팎이다. 실측이 아니라 가정이다. */
export const DEFAULT_TRANSCRIBE_SETTINGS: TranscribeSettings = { outputTokensPerCell: 30, outputTokensOverhead: 100, outputCapFactor: 2 };

export function transcribeRequest(unit: LabelUnit, options: { route: string; tags: Tags; modelMaxOutputTokens: number; settings?: TranscribeSettings; timeoutMs?: number }): CallRequest<TranscribeAnswer> {
  const settings = options.settings ?? DEFAULT_TRANSCRIBE_SETTINGS;
  const wanted = unit.targetFactIds.length * settings.outputTokensPerCell + settings.outputTokensOverhead;
  return {
    prompt: TRANSCRIBE_PROMPT,
    vars: { unit: unit.text },
    route: options.route,
    tags: options.tags,
    schema: transcribeAnswerSchema,
    schemaName: "reconcile_transcription",
    maxOutputTokens: Math.min(wanted * settings.outputCapFactor, options.modelMaxOutputTokens),
    ...(options.timeoutMs === undefined ? {} : { timeoutMs: options.timeoutMs }),
  };
}

export type CellVerdict =
  | "same" // 글자가 같다
  | "notation_only" // 쉼표·공백만 다르다
  | "chars_changed" // 글자가 바뀌었다
  | "position_swapped" // 같은 요청의 다른 칸의 글자를 적었다
  | "missing" // 그 칸의 답이 없다
  | "duplicate"; // 그 칸에 답이 둘 이상이고 서로 다르다

export interface CellComparison {
  unitId: string;
  target: string;
  source: string;
  answer: string | null;
  verdict: CellVerdict;
  /** 자리 바뀜일 때: 적은 글자가 원래 있던 칸. */
  swappedWith: string | null;
}

export interface TranscriptionComparison {
  cells: CellComparison[];
  /** 지어냄: 요청에 없는 칸, 또는 옮겨 적으라고 하지 않은 칸을 적은 답. */
  fabricated: { unitId: string; target: string; text: string; inRequest: boolean }[];
}

/** 표기만 다른지 보는 꼴: 전각을 반각으로 맞추고 쉼표와 공백을 지운다. 그 밖의 글자(부호, 소수점, %, 단위)는 그대로 둔다. */
export function notationKey(text: string): string {
  return text.normalize("NFKC").replace(/[,\s]/gu, "");
}

/**
 * 요청 단위 하나의 옮겨 쓴 답을 원문(요청에 실린 칸의 글자, 곧 텍스트 계층에서 온 글자)과 칸마다 견준다.
 * - 같음: 앞뒤 공백을 뺀 글자가 같다.
 * - 표기만 다름: 쉼표·공백을 지우면 같다 (`1,234` 와 `1234`).
 * - 다름: 그 밖의 전부. 같은 요청의 다른 칸의 글자와 같으면 자리 바뀜, 아니면 글자 바뀜. 답이 없으면 빠짐.
 */
export function compareTranscription(unit: LabelUnit, answer: TranscribeAnswer): TranscriptionComparison {
  const targets = unit.targetFactIds.map((id) => id.slice(id.lastIndexOf(":") + 1));
  const wanted = new Set(targets);
  const byTarget = new Map<string, string[]>();
  const fabricated: TranscriptionComparison["fabricated"] = [];
  for (const cell of answer.cells) {
    if (!wanted.has(cell.target)) {
      fabricated.push({ unitId: unit.id, target: cell.target, text: cell.text, inRequest: unit.ids.has(cell.target) });
      continue;
    }
    byTarget.set(cell.target, [...(byTarget.get(cell.target) ?? []), cell.text]);
  }
  const cells = targets.map((target): CellComparison => {
    const source = (unit.ids.get(target) ?? "").trim();
    const base = { unitId: unit.id, target, source, swappedWith: null };
    const texts = [...new Set((byTarget.get(target) ?? []).map((text) => text.trim()))];
    if (texts.length === 0) return { ...base, answer: null, verdict: "missing" };
    if (texts.length > 1) return { ...base, answer: texts.join(" | "), verdict: "duplicate" };
    const text = texts[0] as string;
    if (text === source) return { ...base, answer: text, verdict: "same" };
    if (notationKey(text) === notationKey(source)) return { ...base, answer: text, verdict: "notation_only" };
    const other = [...unit.ids.entries()].find(([id, value]) => id !== target && notationKey(value) === notationKey(text));
    return other ? { ...base, answer: text, verdict: "position_swapped", swappedWith: other[0] } : { ...base, answer: text, verdict: "chars_changed" };
  });
  return { cells, fabricated };
}

export interface TranscriptionRow {
  name: string;
  /** 옮겨 적으라고 한 칸 (분모). */
  cells: number;
  same: number;
  notationOnly: number;
  /** 다름 = 글자 바뀜 + 자리 바뀜 + 빠짐 + 겹친 답. */
  different: number;
  charsChanged: number;
  positionSwapped: number;
  missing: number;
  duplicate: number;
  fabricated: number;
  /** 답을 받지 못한 요청(잘림 등)에 든 칸. 분모에 들어 있고 「빠짐」으로 센다. */
  inFailedUnits: number;
}

export function transcriptionRow(name: string, cells: CellComparison[], fabricated: number, inFailedUnits = 0): TranscriptionRow {
  const n = (verdict: CellVerdict): number => cells.filter((cell) => cell.verdict === verdict).length;
  return { name, cells: cells.length, same: n("same"), notationOnly: n("notation_only"), different: n("chars_changed") + n("position_swapped") + n("missing") + n("duplicate"), charsChanged: n("chars_changed"), positionSwapped: n("position_swapped"), missing: n("missing"), duplicate: n("duplicate"), fabricated, inFailedUnits };
}
