import type { Factor } from "./factor.js";

export interface ShortlistQuery {
  /** 품목명과 운송 수단 같은 원문 글자. */
  texts: string[];
  /** 분류 단계가 낸 영어 검색어. */
  keywords: string[];
}

export interface Shortlist {
  factors: Factor[];
  /** 추리기 전 후보 수. */
  total: number;
  /** 검색어와 한 군데라도 겹친 후보 수. 0 이면 추린 목록은 표의 앞 순서일 뿐이다. */
  lexicalMatches: number;
}

const HANGUL = /[가-힣]/;

function words(text: string): string[] {
  return text
    .normalize("NFKC")
    .toLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .filter((word) => word.length >= 2);
}

function hangulBigrams(text: string): string[] {
  const grams: string[] = [];
  for (const word of words(text)) {
    for (let i = 0; i + 1 < word.length; i += 1) {
      const gram = word.slice(i, i + 2);
      if (HANGUL.test(gram[0] as string) && HANGUL.test(gram[1] as string)) grams.push(gram);
    }
  }
  return grams;
}

function searchText(factor: Factor): string {
  return `${factor.name} ${factor.categoryPath.join(" ")}`.normalize("NFKC").toLowerCase();
}

/** 후보 전체에 처음 방식(lexical-v1)의 점수를 매겨 줄 세운다. `matched` 는 겹친 낱말과 한글 두 글자 조각이다. */
export function rankLexicalV1(candidates: readonly Factor[], query: ShortlistQuery): { factor: Factor; index: number; score: number; matched: string[] }[] {
  const queryWords = new Set([...query.texts.flatMap(words), ...query.keywords.flatMap(words)]);
  const queryGrams = new Set(query.texts.flatMap(hangulBigrams));
  const scored = candidates.map((factor, index) => {
    const text = searchText(factor);
    let score = 0;
    const matched: string[] = [];
    for (const word of queryWords) {
      if (text.includes(word)) {
        score += 3;
        matched.push(word);
      }
    }
    for (const gram of queryGrams) {
      if (text.includes(gram)) {
        score += 1;
        matched.push(`#${gram}`);
      }
    }
    return { factor, index, score, matched };
  });
  scored.sort((a, b) => b.score - a.score || a.index - b.index);
  return scored;
}

/**
 * 후보를 프롬프트에 넣을 수 있는 수로 추린다. 낱말 일치와 한글 두 글자 겹침으로 점수를 매기는 글자 수준 검색이다 (lexical-v1).
 * 한 카테고리의 후보 전체(카테고리 1 은 1,000건 이상)를 매 호출에 넣지 않으려는 단계이고, 뜻을 아는 검색이 아니다.
 * 낱말이 계수 이름에 부분 문자열로 들어 있으면 맞은 것으로 치고, 점수가 같으면 표의 순서를 따른다.
 * 다른 방식은 `ranking.ts` 에 있다.
 */
export function shortlist(candidates: readonly Factor[], query: ShortlistQuery, limit: number): Shortlist {
  const scored = rankLexicalV1(candidates, query);
  const lexicalMatches = scored.filter((entry) => entry.score > 0).length;
  return { factors: scored.slice(0, limit).map((entry) => entry.factor), total: candidates.length, lexicalMatches };
}
