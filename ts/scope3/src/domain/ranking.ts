import type { Factor } from "./factor.js";
import type { ShortlistQuery } from "./lexicalV1.js";

// 후보 순위 함수. 입출력이 없다: 파일도 공급사도 모른다. 벡터는 값으로 받는다.

/** 임베딩 질의에 무엇을 넣는지. `item` 은 품목명(과 운송 수단)만, `item+keywords` 는 분류 단계의 영어 검색어를 덧붙인다. */
export const EMBEDDING_QUERY_MODES = ["item", "item+keywords"] as const;
export type EmbeddingQueryMode = (typeof EMBEDDING_QUERY_MODES)[number];

/** 미리 정한 기본값이다. 예제 세트를 보고 고른 값이 아니다 (BM25 의 통상 기본값, RRF 논문의 k). */
export const BM25_K1 = 1.2;
export const BM25_B = 0.75;
export const RRF_K = 60;

/** 추린 후보 하나가 왜 그 자리에 왔는지. 순위는 1부터다. 해당 검색에 걸리지 않았으면 그 항목이 없다. */
export interface CandidateEvidence {
  id: string;
  rank: number;
  lexical?: { rank: number; score: number; matchedTerms: string[] };
  embedding?: { rank: number; similarity: number };
  /** 합친 방식의 순위 합산 점수. */
  rrfScore?: number;
}

export interface RankedCandidate {
  factor: Factor;
  /** 후보 표에서의 순서. 동점을 가르는 마지막 기준이다. */
  index: number;
  score: number;
  evidence: Omit<CandidateEvidence, "rank">;
}

const HANGUL = /^[가-힣]+$/;

/** 영어 복수형만 접는다 (services → service, batteries → battery). 그 밖의 어형 변화는 건드리지 않는다. */
export function singular(word: string): string {
  if (!/^[a-z]+$/.test(word) || word.length <= 3) return word;
  if (word.endsWith("ies")) return `${word.slice(0, -3)}y`;
  if (word.endsWith("sses")) return word.slice(0, -2);
  if (word.endsWith("s") && !word.endsWith("ss") && !word.endsWith("us") && !word.endsWith("is")) return word.slice(0, -1);
  return word;
}

/**
 * 글을 낱말 단위로 쪼갠다. 글자·숫자가 아닌 것에서 끊고, 두 글자 미만은 버린다.
 * `bigrams` 를 켜면 한글 낱말은 두 글자 조각(`#` 접두)도 낱말로 더한다. 한국어 계수 이름은 띄어쓰기 없이 붙여 쓴 복합어가 많아서다.
 */
export function tokenize(text: string, options: { bigrams: boolean }): string[] {
  const tokens: string[] = [];
  for (const raw of text.normalize("NFKC").toLowerCase().split(/[^\p{L}\p{N}]+/u)) {
    if (raw.length < 2) continue;
    tokens.push(singular(raw));
    if (options.bigrams && HANGUL.test(raw)) for (let i = 0; i + 1 < raw.length; i += 1) tokens.push(`#${raw.slice(i, i + 2)}`);
  }
  return tokens;
}

/** 계수 하나를 검색·임베딩할 때 쓰는 글. 색인을 만들 때와 낱말 검색이 같은 글을 본다. */
export function factorText(factor: Factor): string {
  const path = factor.categoryPath.join(" > ");
  return path === "" ? factor.name : `${factor.name} | ${path}`;
}

function compare(a: RankedCandidate, b: RankedCandidate): number {
  return b.score - a.score || a.index - b.index;
}

export interface LexicalV2Options {
  /** 한글 두 글자 조각을 낱말로 더할지. */
  bigrams: boolean;
}

/**
 * 낱말 단위 BM25. 낱말이 통째로 같아야 맞는다 (`car` 는 `cargo` 에 맞지 않는다).
 * 드문 낱말일수록 무겁다: IDF 는 이번 호출에 넘어온 후보 집합에서 센다.
 * 점수가 0 인 후보(겹친 낱말 없음)는 돌려주지 않는다. 동점은 후보 표의 순서를 따른다.
 */
export function rankLexicalV2(candidates: readonly Factor[], query: ShortlistQuery, options: LexicalV2Options): RankedCandidate[] {
  const docs = candidates.map((factor) => {
    const counts = new Map<string, number>();
    const tokens = tokenize(factorText(factor), options);
    for (const token of tokens) counts.set(token, (counts.get(token) ?? 0) + 1);
    return { counts, length: tokens.length };
  });
  const averageLength = docs.reduce((sum, doc) => sum + doc.length, 0) / Math.max(1, docs.length);
  // 영어 검색어에서는 한글 조각이 나오지 않는다. 한글 조각은 원문 글자에서만 만든다.
  const terms = [...new Set([...query.texts.flatMap((text) => tokenize(text, options)), ...query.keywords.flatMap((text) => tokenize(text, { bigrams: false }))])];
  const idf = new Map<string, number>();
  for (const term of terms) {
    const df = docs.reduce((sum, doc) => sum + (doc.counts.has(term) ? 1 : 0), 0);
    idf.set(term, Math.log(1 + (docs.length - df + 0.5) / (df + 0.5)));
  }
  const ranked: RankedCandidate[] = [];
  candidates.forEach((factor, index) => {
    const doc = docs[index] as (typeof docs)[number];
    let score = 0;
    const matchedTerms: string[] = [];
    for (const term of terms) {
      const tf = doc.counts.get(term);
      if (tf === undefined) continue;
      matchedTerms.push(term);
      score += ((idf.get(term) as number) * tf * (BM25_K1 + 1)) / (tf + BM25_K1 * (1 - BM25_B + (BM25_B * doc.length) / averageLength));
    }
    if (matchedTerms.length > 0) ranked.push({ factor, index, score, evidence: { id: factor.id, lexical: { rank: 0, score, matchedTerms } } });
  });
  ranked.sort(compare);
  ranked.forEach((entry, position) => {
    (entry.evidence.lexical as NonNullable<CandidateEvidence["lexical"]>).rank = position + 1;
  });
  return ranked;
}

export function cosine(a: ArrayLike<number>, b: ArrayLike<number>): number {
  let dot = 0;
  let normA = 0;
  let normB = 0;
  for (let i = 0; i < a.length; i += 1) {
    const x = a[i] as number;
    const y = b[i] as number;
    dot += x * y;
    normA += x * x;
    normB += y * y;
  }
  return normA === 0 || normB === 0 ? 0 : dot / Math.sqrt(normA * normB);
}

/** 질의 벡터와의 코사인 유사도로 후보 전체를 줄 세운다. 색인에 벡터가 없는 후보가 있으면 그 id 를 돌려준다. 동점은 후보 표의 순서를 따른다. */
export function rankByEmbedding(candidates: readonly Factor[], queryVector: ArrayLike<number>, index: { vector(id: string): ArrayLike<number> | undefined }): RankedCandidate[] | { missing: string } {
  const ranked: RankedCandidate[] = [];
  for (const [position, factor] of candidates.entries()) {
    const vector = index.vector(factor.id);
    if (!vector) return { missing: factor.id };
    const similarity = cosine(queryVector, vector);
    ranked.push({ factor, index: position, score: similarity, evidence: { id: factor.id, embedding: { rank: 0, similarity } } });
  }
  ranked.sort(compare);
  ranked.forEach((entry, position) => {
    (entry.evidence.embedding as NonNullable<CandidateEvidence["embedding"]>).rank = position + 1;
  });
  return ranked;
}

/**
 * 순위 합산(RRF). 각 순위표에서 r 등인 후보에 1/(k+r) 을 주고 더한다. 한 순위표에 없는 후보는 그 표에서 0 을 받는다.
 * 동점은 후보 표의 순서를 따른다.
 */
export function fuseRrf(rankings: readonly (readonly RankedCandidate[])[], k: number = RRF_K): RankedCandidate[] {
  const fused = new Map<string, RankedCandidate>();
  for (const ranking of rankings) {
    ranking.forEach((entry, position) => {
      const current = fused.get(entry.factor.id) ?? { factor: entry.factor, index: entry.index, score: 0, evidence: { id: entry.factor.id } };
      current.score += 1 / (k + position + 1);
      current.evidence = { ...current.evidence, ...entry.evidence, rrfScore: current.score };
      fused.set(entry.factor.id, current);
    });
  }
  return [...fused.values()].sort(compare);
}

export function embeddingQueryText(query: ShortlistQuery, mode: EmbeddingQueryMode): string {
  const item = query.texts.join(" ");
  return mode === "item" || query.keywords.length === 0 ? item : `${item} | ${query.keywords.join(", ")}`;
}
