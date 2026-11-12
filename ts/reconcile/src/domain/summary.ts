import type { Extraction } from "./facts.js";
import type { Finding, Outcome } from "./match.js";

// 분모가 있는 요약. 「숫자 N개 중 M개 대조, K개 미확인」 에서 N 은 코드가 센 값 후보 전체이고,
// 미확인은 N 에서 빠지지 않는다. 합이 N 과 다르면 요약을 내지 않고 예외를 낸다.

export interface DocSummary {
  docId: string;
  fileName: string;
  /** N: 이 문서의 값 후보 전체. */
  valueCandidates: number;
  /** M: 상대 문서의 숫자와 견준 것 (일치 + 불일치 + 설명 필요). */
  compared: number;
  byOutcome: Record<Outcome, number>;
  unverifiedByReason: Record<string, number>;
  /** 신뢰도 낮은 표에서 나온 값 후보 (미확인에 들어 있다). */
  lowConfidenceTableNumbers: number;
  /** 격자를 세우지 못한 표. 그 안의 숫자는 N 에 없다 (수를 모른다). */
  failedTables: number;
  /** 텍스트 계층이 없는 쪽. 그 쪽의 숫자는 N 에 없다 (미확인: 텍스트 없음). */
  pagesWithoutText: number[];
  /** 엑셀의 수식 셀 가운데 저장된 결과가 없어 읽지 못한 것. N 에 없다. */
  formulasWithoutResult: number;
  sentence: string;
}

export interface Summary {
  a: DocSummary;
  b: DocSummary;
  pairs: Record<"match" | "mismatch" | "needs_explanation", number>;
  needsExplanationByReason: Record<string, number>;
}

const OUTCOMES: Outcome[] = ["match", "mismatch", "needs_explanation", "unpaired", "unverified"];

function summarizeDoc(side: "a" | "b", extraction: Extraction, findings: Finding[]): DocSummary {
  const byOutcome = Object.fromEntries(OUTCOMES.map((outcome) => [outcome, 0])) as Record<Outcome, number>;
  const unverifiedByReason: Record<string, number> = {};
  const seen = new Set<string>();
  for (const finding of findings) {
    const fact = finding[side];
    if (!fact) continue;
    if (seen.has(fact.factId)) throw new Error(`요약: 사실 후보 ${fact.factId} 가 결과에 두 번 들어 있다`);
    seen.add(fact.factId);
    byOutcome[finding.outcome] += 1;
    if (finding.outcome === "unverified") unverifiedByReason[finding.reason] = (unverifiedByReason[finding.reason] ?? 0) + 1;
  }
  const valueCandidates = extraction.facts.length;
  const total = OUTCOMES.reduce((sum, outcome) => sum + byOutcome[outcome], 0);
  if (total !== valueCandidates || seen.size !== valueCandidates) {
    throw new Error(`요약: 문서 ${extraction.docId} 의 값 후보는 ${valueCandidates}개인데 결과에는 ${total}개가 있다. 빠진 숫자가 있다`);
  }
  const compared = byOutcome.match + byOutcome.mismatch + byOutcome.needs_explanation;
  const formulasWithoutResult = extraction.nonValues.filter((item) => item.class === "formula_without_result").length;
  const extras = [
    formulasWithoutResult > 0 ? `저장된 결과가 없는 수식 셀 ${formulasWithoutResult}개는 읽지 못했다` : null,
    extraction.failedTables.length > 0 ? `추출 실패 표 ${extraction.failedTables.length}개의 숫자는 세지 못했다` : null,
    extraction.pagesWithoutText.length > 0 ? `텍스트 없는 쪽 ${extraction.pagesWithoutText.length}개는 읽지 못했다` : null,
  ].filter((text): text is string => text !== null);
  const sentence =
    `숫자 ${valueCandidates}개 중 ${compared}개 대조 (일치 ${byOutcome.match}, 불일치 ${byOutcome.mismatch}, 설명 필요 ${byOutcome.needs_explanation}), ` +
    `짝 없음 ${byOutcome.unpaired}개, 미확인 ${byOutcome.unverified}개` +
    (extras.length > 0 ? `. ${extras.join(", ")}` : "");
  return {
    docId: extraction.docId,
    fileName: extraction.fileName,
    valueCandidates,
    compared,
    byOutcome,
    unverifiedByReason,
    lowConfidenceTableNumbers: extraction.facts.filter((fact) => fact.tableStatus === "low_confidence").length,
    failedTables: extraction.failedTables.length,
    pagesWithoutText: extraction.pagesWithoutText,
    formulasWithoutResult,
    sentence,
  };
}

export function summarize(a: Extraction, b: Extraction, findings: Finding[]): Summary {
  const pairs = { match: 0, mismatch: 0, needs_explanation: 0 };
  const needsExplanationByReason: Record<string, number> = {};
  for (const finding of findings) {
    if (!finding.a || !finding.b) continue;
    if (finding.outcome === "match" || finding.outcome === "mismatch" || finding.outcome === "needs_explanation") pairs[finding.outcome] += 1;
    if (finding.outcome === "needs_explanation") needsExplanationByReason[finding.reason] = (needsExplanationByReason[finding.reason] ?? 0) + 1;
  }
  return { a: summarizeDoc("a", a, findings), b: summarizeDoc("b", b, findings), pairs, needsExplanationByReason };
}
