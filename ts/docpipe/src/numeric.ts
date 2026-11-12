/**
 * 글자 한 덩어리(표의 셀, 블록)를 LLM 에 보낼지 정하는 유일한 자리.
 *
 * - `blank`        비었다.
 * - `symbol`       글자도 숫자도 없다 (`-`, `※`, `%`).
 * - `numeric`      숫자가 있고 어느 문자 체계의 글자도 없다 (`36,399`, `15.4`, `31.0%`, `(1,234)`, `△12`).
 * - `passthrough`  글자는 있지만 원문 언어(한글·한자)의 글자가 없다 (`GWh`, `tCO2e`, `Scope 1`).
 * - `translatable` 한글이나 한자가 하나라도 있다 (`전력`, `2023년`, `187조 원`).
 *
 * `translatable` 만 LLM 에 보낼 수 있고 나머지 넷은 원본 글자를 그대로 옮긴다.
 * `translatable` 인 표 셀 가운데 단위만 있는 셀(`억 원`, `천 톤`)은 그 다음 단계(`units.ts` 의 `unitCell`)가
 * 갈라 고정 번역 표로 옮기고 LLM 에 보내지 않는다.
 * 숫자와 한글이 섞인 셀(`2023년`, `1,234톤`)은 LLM 을 거친다. 표 셀이면 번역문의 숫자 덩어리를 원문과 견줘
 * 다르면 그 셀에 의심 표시를 남기고(`translate.ts` 의 `number_change_suspected`), 문단은
 * `consistency.numberDrifts` 가 사후에 보고한다.
 */
export type TextClass = "blank" | "symbol" | "numeric" | "passthrough" | "translatable";

const LETTER = /\p{L}/u;
const DIGIT = /\p{Nd}/u;
const SOURCE_SCRIPT = /[\p{Script=Hangul}\p{Script=Han}]/u;

export function classifyText(text: string): TextClass {
  const normalized = text.normalize("NFKC").trim();
  if (normalized === "") return "blank";
  if (!LETTER.test(normalized)) return DIGIT.test(normalized) ? "numeric" : "symbol";
  return SOURCE_SCRIPT.test(normalized) ? "translatable" : "passthrough";
}

/** 숫자 셀인가. 숫자 셀은 LLM 에 보내지 않고, LLM 이 값을 돌려줘도 쓰지 않는다. */
export function isNumericCell(text: string): boolean {
  return classifyText(text) === "numeric";
}

/** 원본을 그대로 옮기는 셀인가 (숫자 셀 포함). */
export function isCopiedVerbatim(text: string): boolean {
  return classifyText(text) !== "translatable";
}

const NUMBER_RUN = /\d+(?:[.,]\d+)*/g;

/** 글자 속 숫자 덩어리를 천 단위 쉼표를 지운 꼴로 뽑는다. */
export function numberRuns(text: string): string[] {
  return (text.normalize("NFKC").match(NUMBER_RUN) ?? []).map((run) => run.replace(/,(?=\d{3}(?!\d))/g, ""));
}
