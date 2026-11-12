import type { ProviderRequest, ProviderResponse, TokenEstimator, Usage } from "llm";

type Responder = (request: ProviderRequest) => ProviderResponse;
import type { Glossary } from "./glossary.js";
import type { Checklist } from "./greenwash.js";
import type { Item } from "./select.js";

/**
 * `--provider mock` 으로 파이프라인을 끝까지 돌려 보기 위한 synthetic 응답기다. 번역도 판정도 하지 않는다.
 * 여기서 나온 글자와 통과율은 모델의 성능과 아무 관계가 없다.
 */

function requestText(request: ProviderRequest): string {
  return request.user.map((part) => (part.type === "text" ? part.text : "")).join("\n");
}

/** 프롬프트의 user 구획 끝에 실린 항목 배열을 다시 읽는다. */
export function itemsOf(request: ProviderRequest): Item[] {
  const text = requestText(request);
  const at = text.indexOf("\n[\n");
  if (at === -1) throw new Error("모의 응답기: 요청에서 항목 배열을 찾지 못했다");
  return JSON.parse(text.slice(at + 1)) as Item[];
}

function usageOf(request: ProviderRequest, output: string, estimator: TokenEstimator): Usage {
  const input = [...request.system.map((segment) => segment.text), requestText(request)].join("\n");
  return { inputTokens: estimator.estimate(input), outputTokens: estimator.estimate(output), cacheReadTokens: 0, cacheWriteTokens: 0 };
}

/** 원문 앞에 `[mock-en]` 을 붙이고 용어집의 국문 용어만 영문으로 바꿔 돌려준다. */
export function syntheticTranslator(glossary: Glossary, estimator: TokenEstimator): Responder {
  const terms = [...glossary.terms].sort((a, b) => b.ko.length - a.ko.length);
  return (request) => {
    const items = itemsOf(request).map((item) => {
      let text = item.text;
      for (const term of terms) text = text.split(term.ko).join(term.en);
      return { id: item.id, text: `[mock-en] ${text}` };
    });
    const output = JSON.stringify({ items });
    return { kind: "completed", text: output, usage: usageOf(request, output, estimator) };
  };
}

/** 조각의 첫 블록 첫 문장을 체크리스트의 첫 유형으로 돌려준다. 판정이 아니라 출력 모양의 예시다. */
export function syntheticReviewer(checklist: Checklist, estimator: TokenEstimator): Responder {
  return (request) => {
    const first = itemsOf(request)[0];
    const findings = first
      ? [
          {
            block_id: first.id,
            sentence: first.text.split(/(?<=다\.)\s/)[0] ?? first.text,
            type: (checklist.types[0] as { id: string }).id,
            reason: "[mock synthetic] 판정이 아니다",
            suggestion: "[mock synthetic] 대체 표현 자리",
          },
        ]
      : [];
    const output = JSON.stringify({ findings });
    return { kind: "completed", text: output, usage: usageOf(request, output, estimator) };
  };
}
