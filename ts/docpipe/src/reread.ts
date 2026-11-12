import { readFileSync } from "node:fs";
import type { ProviderRequest, ProviderResponse } from "llm";
import { itemsOf } from "./mock-synthetic.js";

/**
 * 저장된 호출 기록(`calls.jsonl`, `--store-bodies` 로 남긴 것)을 다시 읽어 지금의 규칙에 통과시키기 위한 도구.
 * 공급사를 부르지 않는다. 저장된 응답 글자를 모의 공급사의 응답기로 되돌려줄 뿐이다.
 */
export interface StoredCalls {
  path: string;
  /** 기록의 줄 수. */
  lines: number;
  /** 성공 응답이 아니거나 응답 글자를 읽지 못해 쓰지 않은 줄. */
  unusable: number;
  /** 요청의 항목 id 묶음(그때의 조각) → 그 조각의 응답들, 부른 순서대로. */
  byRequest: Map<string, string[]>;
  /** 항목 id → 그 항목의 번역들, 부른 순서대로. */
  byItem: Map<string, string[]>;
}

export class StoredCallsError extends Error {
  override name = "StoredCallsError";
}

const signature = (ids: string[]): string => ids.join("\u0000");

interface LogLine {
  resultKind?: string;
  body?: { request?: { user?: { type?: string; text?: string }[] }; responseText?: string };
}

export function loadStoredCalls(path: string): StoredCalls {
  const byRequest = new Map<string, string[]>();
  const byItem = new Map<string, string[]>();
  let lines = 0;
  let unusable = 0;
  for (const text of readFileSync(path, "utf8").split("\n")) {
    if (text.trim() === "") continue;
    lines += 1;
    const record = JSON.parse(text) as LogLine;
    const user = record.body?.request?.user;
    // 본문 없이 해시만 남긴 기록으로는 다시 읽을 수 없다. 빈 결과로 넘기지 않는다.
    if (!user) throw new StoredCallsError(`${path}: ${lines}번째 줄에 요청 본문이 없다. --store-bodies 로 남긴 호출 기록이어야 한다`);
    if (record.resultKind !== "success" || typeof record.body?.responseText !== "string") {
      unusable += 1;
      continue;
    }
    const requestText = user.map((part) => (part.type === "text" ? (part.text ?? "") : "")).join("\n");
    const at = requestText.indexOf("\n[\n");
    if (at === -1) throw new StoredCallsError(`${path}: ${lines}번째 줄의 요청에서 항목 배열을 찾지 못했다`);
    const ids = (JSON.parse(requestText.slice(at + 1)) as { id: string }[]).map((item) => item.id);
    let answered: { id: string; text: string }[];
    try {
      answered = (JSON.parse(record.body.responseText) as { items: { id: string; text: string }[] }).items;
    } catch {
      unusable += 1;
      continue;
    }
    const key = signature(ids);
    byRequest.set(key, [...(byRequest.get(key) ?? []), record.body.responseText]);
    const wanted = new Set(ids);
    for (const item of answered) if (wanted.has(item.id)) byItem.set(item.id, [...(byItem.get(item.id) ?? []), item.text]);
  }
  return { path, lines, unusable, byRequest, byItem };
}

export interface RereadStats {
  /** 저장된 조각과 항목이 똑같아 그때의 응답을 통째로 돌려준 요청 수. */
  exactReplies: number;
  /** 조각 구성이 달라져(단위 셀을 빼서) 항목별 저장 번역을 다시 묶어 돌려준 요청 수. */
  recomposedReplies: number;
  /** 저장된 번역이 없어 응답에서 빠진 항목 id. 그 조각은 `incomplete_output` 으로 실패한다. */
  itemsWithoutStoredResponse: string[];
}

const NO_USAGE = { inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0 };

/**
 * 저장된 응답을 돌려주는 응답기. 같은 조각을 n 번째로 물으면 n 번째 저장 응답을 준다(없으면 마지막 것).
 * 요청의 항목이 저장된 어느 조각과도 같지 않으면 항목마다 그 항목의 n 번째 저장 번역을 모아 응답을 만든다.
 * 이때 돌려주는 글자는 전부 실제 모델이 그 항목에 낸 번역이지만, 모델이 이 조각 구성으로 낸 응답은 아니다.
 * 사용량은 0 이다 (부르지 않았으므로 비용이 없다).
 */
export function storedResponder(stored: StoredCalls): { reply: (request: ProviderRequest) => ProviderResponse; stats: RereadStats } {
  const stats: RereadStats = { exactReplies: 0, recomposedReplies: 0, itemsWithoutStoredResponse: [] };
  const asked = new Map<string, number>();
  const reply = (request: ProviderRequest): ProviderResponse => {
    const ids = itemsOf(request).map((item) => item.id);
    const key = signature(ids);
    const nth = asked.get(key) ?? 0;
    asked.set(key, nth + 1);
    const exact = stored.byRequest.get(key);
    if (exact) {
      stats.exactReplies += 1;
      return { kind: "completed", text: exact[Math.min(nth, exact.length - 1)] as string, usage: NO_USAGE };
    }
    stats.recomposedReplies += 1;
    const items: { id: string; text: string }[] = [];
    for (const id of ids) {
      const texts = stored.byItem.get(id);
      if (!texts) {
        if (!stats.itemsWithoutStoredResponse.includes(id)) stats.itemsWithoutStoredResponse.push(id);
        continue;
      }
      items.push({ id, text: texts[Math.min(nth, texts.length - 1)] as string });
    }
    return { kind: "completed", text: JSON.stringify({ items }), usage: NO_USAGE };
  };
  return { reply, stats };
}
