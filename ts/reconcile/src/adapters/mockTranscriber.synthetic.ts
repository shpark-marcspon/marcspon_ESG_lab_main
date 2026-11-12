import type { ProviderRequest, ProviderResponse, TokenEstimator, Usage } from "llm";

/**
 * 옮겨 쓰기 시험을 `--provider mock` 으로 끝까지 돌려 보기 위한 synthetic 응답기다. 모델이 아니다.
 * 요청에 실린 칸의 글자를 그대로 돌려준다. 그래서 결과는 언제나 「전부 같음」이고 모델의 성능과 아무 관계가 없다.
 */
export function syntheticTranscriber(estimator: TokenEstimator): (request: ProviderRequest) => ProviderResponse {
  return (request) => {
    const text = request.user.map((part) => (part.type === "text" ? part.text : "")).join("\n");
    const at = text.indexOf("\n{");
    if (at === -1) throw new Error("모의 응답기: 요청에서 입력 JSON 을 찾지 못했다");
    const payload = JSON.parse(text.slice(at + 1)) as { rows: { r: number; cells: Record<string, string> }[]; targets: string[] };
    const cells = new Map(payload.rows.flatMap((row) => Object.entries(row.cells).map(([col, value]): [string, string] => [`r${row.r}${col}`, value])));
    const output = JSON.stringify({ cells: payload.targets.map((target) => ({ target, text: cells.get(target) ?? "" })) });
    const input = [...request.system.map((segment) => segment.text), text].join("\n");
    const usage: Usage = { inputTokens: estimator.estimate(input), outputTokens: estimator.estimate(output), cacheReadTokens: 0, cacheWriteTokens: 0 };
    return { kind: "completed", text: output, usage };
  };
}
