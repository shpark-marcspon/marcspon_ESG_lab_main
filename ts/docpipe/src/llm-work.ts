import type { LlmResult } from "llm";
import type { Chunk } from "./chunk.js";
import type { DocSection } from "./document.js";
import type { Item } from "./select.js";
import type { ChunkFailure } from "./store.js";

/** 조각의 글자를 프롬프트에 넣는 꼴. 한 줄에 항목 하나. */
export function renderItems(items: Item[]): string {
  return `[\n${items.map((item) => JSON.stringify(item)).join(",\n")}\n]`;
}

/** 조각이 문서의 어디인지. 문서 전체를 넣는 대신 이 한 줄만 준다. */
export function describeLocation(chunk: Chunk, sections: DocSection[]): string {
  const pages = chunk.pageStart === chunk.pageEnd ? `${chunk.pageStart}쪽` : `${chunk.pageStart}~${chunk.pageEnd}쪽`;
  const titles = chunk.sectionIds
    .map((id) => sections.find((section) => section.id === id))
    .filter((section): section is DocSection => section !== undefined && section.title !== null)
    .map((section) => `${section.title}${section.basis === "pdf_outline" ? "" : " (추정 제목)"}`);
  return titles.length === 0 ? pages : `${pages} · 섹션: ${titles.join(" / ")}`;
}

/**
 * 성공이 아닌 LLM 결과를 조각의 실패로 옮긴다. 조각 단위로 다시 부를 가치가 있는 것은
 * 시간 초과와 재시도 가능한 공급사 오류뿐이다. 스키마 불일치는 호출 계층이 이미 다시 불렀고,
 * 거부·출력 잘림은 같은 입력이면 되풀이된다(잘림은 조각 예산을 줄여야 한다).
 */
export function failureOf(result: Exclude<LlmResult<unknown>, { kind: "success" }>): ChunkFailure {
  switch (result.kind) {
    case "schema_mismatch":
      return { kind: result.kind, retryable: false, message: result.issues.map((issue) => `${issue.path}: ${issue.message}`).join("; ") };
    case "refusal":
      return { kind: result.kind, retryable: false, message: result.text };
    case "truncated":
      return { kind: result.kind, retryable: false, message: "출력 토큰 상한에서 잘렸다. 조각 예산을 줄여 다시 나눠야 한다" };
    case "provider_error":
      return { kind: result.kind, retryable: result.retryable, message: `status=${result.status ?? "없음"} ${result.message}` };
    case "timeout":
      return { kind: result.kind, retryable: true, message: result.message };
    case "budget_exceeded":
      return { kind: result.kind, retryable: false, message: `누적 ${result.spentUsd} USD 가 상한 ${result.limitUsd} USD 에 닿았다` };
  }
}
