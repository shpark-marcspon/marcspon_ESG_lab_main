import { MockProvider, type InvokeOptions, type ProviderRequest, type ProviderResponse, type Usage } from "llm";
import { MAX_PICKS } from "../domain/llmSteps.js";

// `--provider mock` 평가용 응답기. 정답을 보지 않고, 프롬프트에 든 글자만으로 규칙에 따라 답한다.
// 평가 도구가 끝까지 도는지 확인하려는 것이고 이 응답으로 나온 정확도는 성능과 무관하다.

const SYNTHETIC_USAGE: Usage = { inputTokens: 100, outputTokens: 20, cacheReadTokens: 0, cacheWriteTokens: 0 };

const RULES: { category: number; words: string[] }[] = [
  { category: 7, words: ["통근", "출퇴근", "셔틀", "재택"] },
  { category: 6, words: ["출장", "항공", "호텔", "숙박", "ktx", "택시", "렌터카"] },
  { category: 2, words: ["설비", "장비", "기계", "서버", "노트북", "차량 구매", "공사"] },
];

function field(text: string, label: string): unknown {
  const line = text.split("\n").find((candidate) => candidate.startsWith(`${label}: `));
  if (!line) throw new Error(`모의 응답기: 프롬프트에 ${label} 줄이 없다`);
  return JSON.parse(line.slice(label.length + 2));
}

function userText(request: ProviderRequest): string {
  return request.user.map((part) => (part.type === "text" ? part.text : "")).join("\n");
}

/** 규칙 기반 모의 응답 (synthetic). 분류는 낱말 규칙, 추천은 추린 후보의 앞 세 개다. */
export function heuristicReply(request: ProviderRequest): ProviderResponse {
  const text = userText(request);
  if (request.jsonSchema?.name === "scope3_classification") {
    const item = field(text, "ITEM_JSON") as { item_name: string; transport: unknown };
    const name = item.item_name.toLowerCase();
    const rule = RULES.find((candidate) => candidate.words.some((word) => name.includes(word)));
    const category = item.transport !== null ? 4 : (rule?.category ?? 1);
    return {
      kind: "completed",
      text: JSON.stringify({ category, keywords_en: [], confidence: "low", reason: "모의 응답 (synthetic): 낱말 규칙으로 분류" }),
      usage: SYNTHETIC_USAGE,
    };
  }
  if (request.jsonSchema?.name === "scope3_recommendation") {
    const candidates = field(text, "CANDIDATES_JSON") as { id: string }[];
    const picks = candidates.slice(0, MAX_PICKS).map((candidate) => ({ factor_id: candidate.id, reason: "모의 응답 (synthetic): 추린 목록의 앞 순서", confidence: "low" }));
    return { kind: "completed", text: JSON.stringify({ picks, none_suitable: picks.length === 0 }), usage: SYNTHETIC_USAGE };
  }
  return { kind: "error", status: null, retryable: false, message: "모의 응답기가 모르는 요청이다" };
}

/** 심어 둔 응답이 있으면 그것을, 없으면 규칙 기반 응답을 돌려주는 모의 공급사. 네트워크를 쓰지 않는다. */
export class HeuristicMockProvider extends MockProvider {
  override async invoke(request: ProviderRequest, options: InvokeOptions): Promise<ProviderResponse> {
    if (this.pending === 0) this.enqueue(heuristicReply);
    return super.invoke(request, options);
  }
}
