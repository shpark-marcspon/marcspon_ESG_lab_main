import { computeCostUsd, roundUsd, type CallRecord, type Pricing } from "llm";

// 호출 기록에서 실제 토큰과 비용을 다시 센다. 모델을 부르지 않는다.
// 여기의 수는 문서의 값이 아니라 토큰 수·비용이라 JS number 로 다룬다.

export interface UnitUsage {
  unitId: string;
  callId: string;
  targets: number;
  /** 그 요청 단위에 든 공급사 시도 수 (스키마 불일치 재시도 포함). */
  attempts: number;
  finalKind: string;
  /** 마지막 시도의 토큰. */
  inputTokens: number;
  outputTokens: number;
  thinkingTokens: number;
  costUsd: number;
  durationMs: number;
  /** 요청에 실린 글자 수 (본문을 저장했을 때만): 고정 지침과 지표 목록, 그리고 표. */
  chars: { hangul: number; other: number } | null;
  maxOutputTokens: number | null;
}

export interface UsageReport {
  lines: number;
  /** 사용량을 받은 줄 (과금된 시도). */
  billedLines: number;
  byProvider: Record<string, number>;
  byApiModel: Record<string, number>;
  byResponseModel: Record<string, number>;
  byPrompt: Record<string, number>;
  byResultKind: Record<string, number>;
  /** 첫 시도가 아닌 줄 (스키마 불일치로 같은 모델을 다시 부른 것). */
  retryLines: number;
  tokens: { input: number; uncachedInput: number; cacheRead: number; cacheWrite: number; output: number; thinking: number };
  /** 기록에 적힌 비용의 합. */
  costUsd: number;
  /** 토큰 × 설정 단가로 다시 계산한 비용. 단가를 주지 않으면 null. */
  recomputedCostUsd: number | null;
  durationMs: { median: number; max: number };
  units: UnitUsage[];
  perTarget: {
    /** 답을 받은 요청 단위의 대상 숫자 (분모). */
    targets: number;
    units: number;
    outputTokensPerTarget: number;
    answerTokensPerTarget: number;
    thinkingTokensPerTarget: number;
    /** 요청 단위별 「출력 토큰 ÷ 대상 수」의 분포. */
    perUnit: { min: number; median: number; max: number };
    /** 출력 토큰이 출력 상한에서 차지한 비율이 가장 큰 요청. */
    maxOutputShare: number | null;
  } | null;
  /** 입력 토큰 = a × 한글·한자 글자 수 + b × 그 밖 글자 수 + c 의 최소제곱. 본문을 저장한 기록이 셋 이상일 때만. */
  tokenFit: { hangulTokensPerChar: number; otherTokensPerChar: number; constant: number; meanAbsResidual: number; calls: number } | null;
}

const bump = (bag: Record<string, number>, key: string): void => {
  bag[key] = (bag[key] ?? 0) + 1;
};

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 1 ? (sorted[mid] as number) : ((sorted[mid - 1] as number) + (sorted[mid] as number)) / 2;
}

const HANGUL_OR_HAN = /[ᄀ-ᇿ㄰-㆏가-힯㐀-鿿]/gu;

function charsOf(record: CallRecord): { hangul: number; other: number } | null {
  const request = record.body?.request as { system?: { text?: string }[]; user?: { type?: string; text?: string }[]; jsonSchema?: unknown } | undefined;
  if (!request) return null;
  const text = [...(request.system ?? []).map((segment) => segment.text ?? ""), ...(request.user ?? []).map((part) => part.text ?? ""), request.jsonSchema === undefined ? "" : JSON.stringify(request.jsonSchema)].join("\n");
  const hangul = text.match(HANGUL_OR_HAN)?.length ?? 0;
  return { hangul, other: [...text].length - hangul };
}

/** 3 미지수 최소제곱 (정규방정식을 소거로 푼다). 풀 수 없으면 null. */
function fit(rows: { x1: number; x2: number; y: number }[]): [number, number, number] | null {
  if (rows.length < 3) return null;
  const m = [[0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]] as number[][];
  for (const { x1, x2, y } of rows) {
    const x = [x1, x2, 1];
    for (let i = 0; i < 3; i++) {
      for (let j = 0; j < 3; j++) (m[i] as number[])[j] = ((m[i] as number[])[j] as number) + (x[i] as number) * (x[j] as number);
      (m[i] as number[])[3] = ((m[i] as number[])[3] as number) + (x[i] as number) * y;
    }
  }
  for (let col = 0; col < 3; col++) {
    let pivot = col;
    for (let row = col + 1; row < 3; row++) if (Math.abs((m[row] as number[])[col] as number) > Math.abs((m[pivot] as number[])[col] as number)) pivot = row;
    [m[col], m[pivot]] = [m[pivot] as number[], m[col] as number[]];
    const head = (m[col] as number[])[col] as number;
    if (Math.abs(head) < 1e-9) return null;
    for (let row = 0; row < 3; row++) {
      if (row === col) continue;
      const factor = ((m[row] as number[])[col] as number) / head;
      for (let k = col; k < 4; k++) (m[row] as number[])[k] = ((m[row] as number[])[k] as number) - factor * ((m[col] as number[])[k] as number);
    }
  }
  return [0, 1, 2].map((i) => ((m[i] as number[])[3] as number) / ((m[i] as number[])[i] as number)) as [number, number, number];
}

/**
 * 호출 기록(JSONL 의 줄들)과 「호출 ID → 요청 단위」 대응으로 실제 사용량을 모은다.
 * `units` 에 없는 호출 ID 의 줄도 합계에는 들어간다.
 */
export function analyzeUsage(records: CallRecord[], units: { unitId: string; callId: string; targets: number }[], pricing?: Pricing): UsageReport {
  const report: UsageReport = { lines: records.length, billedLines: 0, byProvider: {}, byApiModel: {}, byResponseModel: {}, byPrompt: {}, byResultKind: {}, retryLines: 0, tokens: { input: 0, uncachedInput: 0, cacheRead: 0, cacheWrite: 0, output: 0, thinking: 0 }, costUsd: 0, recomputedCostUsd: pricing ? 0 : null, durationMs: { median: median(records.map((record) => record.durationMs)), max: Math.max(0, ...records.map((record) => record.durationMs)) }, units: [], perTarget: null, tokenFit: null };
  const byCall = new Map<string, CallRecord[]>();
  for (const record of records) {
    bump(report.byProvider, record.provider ?? "(없음)");
    bump(report.byApiModel, record.apiModelId ?? "(없음)");
    bump(report.byResponseModel, record.responseModelId ?? "(없음)");
    bump(report.byPrompt, record.promptId);
    bump(report.byResultKind, record.resultKind);
    if (record.attempt > 0) report.retryLines += 1;
    byCall.set(record.callId, [...(byCall.get(record.callId) ?? []), record]);
    if (record.inputTokens === null || record.outputTokens === null) continue;
    report.billedLines += 1;
    const cacheRead = record.cacheReadTokens ?? 0;
    const cacheWrite = record.cacheWriteTokens ?? 0;
    report.tokens.input += record.inputTokens + cacheRead + cacheWrite;
    report.tokens.uncachedInput += record.inputTokens;
    report.tokens.cacheRead += cacheRead;
    report.tokens.cacheWrite += cacheWrite;
    report.tokens.output += record.outputTokens;
    report.tokens.thinking += record.thinkingTokens ?? 0;
    report.costUsd += record.costUsd ?? 0;
    if (pricing) report.recomputedCostUsd = (report.recomputedCostUsd ?? 0) + computeCostUsd({ inputTokens: record.inputTokens, outputTokens: record.outputTokens, cacheReadTokens: cacheRead, cacheWriteTokens: cacheWrite }, pricing);
  }
  report.costUsd = roundUsd(report.costUsd);
  if (report.recomputedCostUsd !== null) report.recomputedCostUsd = roundUsd(report.recomputedCostUsd);

  for (const unit of units) {
    const attempts = (byCall.get(unit.callId) ?? []).sort((a, b) => a.attempt - b.attempt);
    const last = attempts.at(-1);
    if (!last || last.inputTokens === null || last.outputTokens === null) continue;
    report.units.push({
      unitId: unit.unitId,
      callId: unit.callId,
      targets: unit.targets,
      attempts: attempts.length,
      finalKind: last.resultKind,
      inputTokens: last.inputTokens + (last.cacheReadTokens ?? 0) + (last.cacheWriteTokens ?? 0),
      outputTokens: last.outputTokens,
      thinkingTokens: last.thinkingTokens ?? 0,
      costUsd: roundUsd(attempts.reduce((sum, record) => sum + (record.costUsd ?? 0), 0)),
      durationMs: last.durationMs,
      chars: charsOf(last),
      maxOutputTokens: (last.body?.request as { maxOutputTokens?: number } | undefined)?.maxOutputTokens ?? null,
    });
  }
  const done = report.units.filter((unit) => unit.finalKind === "success" && unit.targets > 0);
  if (done.length > 0) {
    const targets = done.reduce((sum, unit) => sum + unit.targets, 0);
    const output = done.reduce((sum, unit) => sum + unit.outputTokens, 0);
    const thinking = done.reduce((sum, unit) => sum + unit.thinkingTokens, 0);
    const ratios = done.map((unit) => unit.outputTokens / unit.targets);
    const shares = report.units.filter((unit) => unit.maxOutputTokens !== null).map((unit) => unit.outputTokens / (unit.maxOutputTokens as number));
    report.perTarget = { targets, units: done.length, outputTokensPerTarget: output / targets, answerTokensPerTarget: (output - thinking) / targets, thinkingTokensPerTarget: thinking / targets, perUnit: { min: Math.min(...ratios), median: median(ratios), max: Math.max(...ratios) }, maxOutputShare: shares.length > 0 ? Math.max(...shares) : null };
  }
  const withChars = report.units.filter((unit) => unit.chars !== null);
  const solved = fit(withChars.map((unit) => ({ x1: (unit.chars as { hangul: number }).hangul, x2: (unit.chars as { other: number }).other, y: unit.inputTokens })));
  if (solved) {
    const [a, b, c] = solved;
    const residuals = withChars.map((unit) => Math.abs(unit.inputTokens - (a * (unit.chars as { hangul: number }).hangul + b * (unit.chars as { other: number }).other + c)));
    report.tokenFit = { hangulTokensPerChar: a, otherTokensPerChar: b, constant: c, meanAbsResidual: residuals.reduce((sum, value) => sum + value, 0) / residuals.length, calls: withChars.length };
  }
  return report;
}

const bag = (items: Record<string, number>): string => Object.entries(items).map(([key, value]) => `${key} ${value}`).join(", ") || "없음";

export function renderUsage(title: string, report: UsageReport): string {
  const lines = [`## ${title}`, ""];
  lines.push(`- 호출 기록 ${report.lines}줄 (사용량을 받은 줄 ${report.billedLines}, 다시 부른 줄 ${report.retryLines})`);
  lines.push(`- 공급사: ${bag(report.byProvider)} · 요청 모델: ${bag(report.byApiModel)} · 응답 모델: ${bag(report.byResponseModel)}`);
  lines.push(`- 프롬프트: ${bag(report.byPrompt)} · 결과 종류: ${bag(report.byResultKind)}`);
  lines.push(`- 토큰: 입력 ${report.tokens.input} (비캐시 ${report.tokens.uncachedInput}, 캐시 읽기 ${report.tokens.cacheRead}, 캐시 쓰기 ${report.tokens.cacheWrite}), 출력 ${report.tokens.output} (그중 추론 ${report.tokens.thinking})`);
  lines.push(`- 비용: 기록 합 ${report.costUsd} USD${report.recomputedCostUsd === null ? "" : `, 토큰 × 설정 단가 재계산 ${report.recomputedCostUsd} USD`}`);
  lines.push(`- 호출 시간: 중앙값 ${Math.round(report.durationMs.median)}ms, 최대 ${report.durationMs.max}ms`);
  if (report.perTarget) {
    const p = report.perTarget;
    lines.push(`- 답 하나(대상 숫자 하나)의 출력 토큰: ${p.outputTokensPerTarget.toFixed(1)} (답 ${p.answerTokensPerTarget.toFixed(1)} + 추론 ${p.thinkingTokensPerTarget.toFixed(1)}). 분모는 답을 받은 요청 ${p.units}개의 대상 ${p.targets}개. 요청별 분포 최소 ${p.perUnit.min.toFixed(1)} / 중앙값 ${p.perUnit.median.toFixed(1)} / 최대 ${p.perUnit.max.toFixed(1)}`);
    if (p.maxOutputShare !== null) lines.push(`- 출력 상한을 가장 많이 쓴 요청: 상한의 ${(p.maxOutputShare * 100).toFixed(1)}%`);
  }
  if (report.tokenFit) {
    const f = report.tokenFit;
    lines.push(`- 입력 토큰 맞춤 (요청 ${f.calls}건, 최소제곱): 한글·한자 1자 = ${f.hangulTokensPerChar.toFixed(3)} 토큰, 그 밖 1자 = ${f.otherTokensPerChar.toFixed(3)} 토큰, 상수 ${f.constant.toFixed(0)}, 잔차 절대값 평균 ${f.meanAbsResidual.toFixed(1)} 토큰`);
  }
  return `${lines.join("\n")}\n`;
}
