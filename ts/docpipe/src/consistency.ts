import type { Chunk } from "./chunk.js";
import type { Glossary } from "./glossary.js";
import { numberRuns } from "./numeric.js";
import type { ChunkRecord } from "./store.js";
import type { TranslateOutput } from "./translate.js";

/** 원문과 번역문의 짝. 끝난 조각의 것만 있다. */
export interface Pair {
  chunkId: string;
  id: string;
  source: string;
  target: string;
}

export function pairsOf(chunks: Chunk[], records: (ChunkRecord<TranslateOutput> | undefined)[]): Pair[] {
  const pairs: Pair[] = [];
  chunks.forEach((chunk, index) => {
    const record = records[index];
    if (record?.status !== "done") return;
    for (const item of chunk.items) {
      const target = record.output.translations[item.id];
      if (target !== undefined) pairs.push({ chunkId: chunk.id, id: item.id, source: item.text, target });
    }
  });
  return pairs;
}

/** `preferred` = 용어집의 영문 용어, `variant:<글자>` = 용어집에 적힌 다른 번역, `unknown` = 둘 다 없음. */
export type Rendering = "preferred" | `variant:${string}` | "unknown";

export interface TermMiss {
  chunkId: string;
  id: string;
  ko: string;
  expected: string;
  rendering: Exclude<Rendering, "preferred">;
}

export interface TermDrift {
  ko: string;
  expected: string;
  /** 번역 꼴 → 그 꼴이 나온 조각들. 둘 이상의 꼴이 나온 용어만 보고한다. */
  renderings: Record<string, string[]>;
}

export interface RepeatDrift {
  source: string;
  targets: { text: string; chunkIds: string[]; ids: string[] }[];
}

export interface NumberDrift {
  chunkId: string;
  id: string;
  /** 원문에 있는데 번역문에 없는 숫자. `187조` → `187 trillion` 은 같고 `1,879,673억` → `187.9 trillion` 은 다르다고 나온다. */
  missingNumbers: string[];
}

export interface ConsistencyReport {
  /** 끝난 조각의 짝만 본다. 실패하거나 안 돈 조각은 분모에 없다. */
  pairsChecked: number;
  termOccurrences: number;
  termMisses: TermMiss[];
  termDrifts: TermDrift[];
  repeatDrifts: RepeatDrift[];
  numberDrifts: NumberDrift[];
}

function renderingOf(target: string, term: { en: string; variants: string[] }): Rendering {
  const lower = target.toLowerCase();
  if (lower.includes(term.en.toLowerCase())) return "preferred";
  const variant = term.variants.find((candidate) => lower.includes(candidate.toLowerCase()));
  return variant ? `variant:${variant}` : "unknown";
}

const fold = (text: string): string => text.normalize("NFKC").replace(/\s+/g, " ").trim();

/**
 * LLM 없이 문자열만으로 보는 검사.
 * 1. 원문에 용어집의 국문 용어가 있으면 번역문에 짝지어진 영문 용어가 있는가 (대소문자 무시).
 *    긴 용어를 먼저 맞춰 가린 뒤 짧은 용어를 본다(`온실가스 배출량` 안의 `온실가스` 를 두 번 세지 않는다).
 * 2. 같은 용어가 조각마다 다른 꼴로 번역됐는가.
 * 3. 글자가 똑같은 원문(되풀이되는 소제목, 표 항목명)이 다르게 번역됐는가.
 * 4. 원문의 숫자가 번역문에 그대로 있는가.
 * 문자열 검사라 활용형(복수형, 하이픈 유무)이 다르면 빠진 것으로 나온다.
 */
export function checkConsistency(glossary: Glossary, pairs: Pair[]): ConsistencyReport {
  const terms = [...glossary.terms].sort((a, b) => b.ko.length - a.ko.length);
  const termMisses: TermMiss[] = [];
  const byTerm = new Map<string, Map<Rendering, Set<string>>>();
  let termOccurrences = 0;

  for (const pair of pairs) {
    let remaining = pair.source;
    for (const term of terms) {
      if (!remaining.includes(term.ko)) continue;
      remaining = remaining.split(term.ko).join("\u0000");
      termOccurrences += 1;
      const rendering = renderingOf(pair.target, term);
      let renderings = byTerm.get(term.ko);
      if (!renderings) byTerm.set(term.ko, (renderings = new Map()));
      let chunkIds = renderings.get(rendering);
      if (!chunkIds) renderings.set(rendering, (chunkIds = new Set()));
      chunkIds.add(pair.chunkId);
      if (rendering !== "preferred") termMisses.push({ chunkId: pair.chunkId, id: pair.id, ko: term.ko, expected: term.en, rendering });
    }
  }

  const termDrifts: TermDrift[] = [];
  for (const term of glossary.terms) {
    const renderings = byTerm.get(term.ko);
    if (!renderings || renderings.size < 2) continue;
    termDrifts.push({ ko: term.ko, expected: term.en, renderings: Object.fromEntries([...renderings].map(([rendering, chunkIds]) => [rendering, [...chunkIds]])) });
  }

  const bySource = new Map<string, Map<string, { text: string; chunkIds: Set<string>; ids: string[] }>>();
  for (const pair of pairs) {
    const key = fold(pair.source);
    let targets = bySource.get(key);
    if (!targets) bySource.set(key, (targets = new Map()));
    const targetKey = fold(pair.target).toLowerCase();
    let entry = targets.get(targetKey);
    if (!entry) targets.set(targetKey, (entry = { text: pair.target, chunkIds: new Set(), ids: [] }));
    entry.chunkIds.add(pair.chunkId);
    entry.ids.push(pair.id);
  }
  const repeatDrifts: RepeatDrift[] = [];
  for (const [source, targets] of bySource) {
    if (targets.size < 2) continue;
    repeatDrifts.push({ source, targets: [...targets.values()].map((entry) => ({ text: entry.text, chunkIds: [...entry.chunkIds], ids: entry.ids })) });
  }

  const numberDrifts: NumberDrift[] = [];
  for (const pair of pairs) {
    const available = numberRuns(pair.target);
    const missingNumbers: string[] = [];
    for (const run of numberRuns(pair.source)) {
      const at = available.indexOf(run);
      if (at === -1) missingNumbers.push(run);
      else available.splice(at, 1);
    }
    if (missingNumbers.length > 0) numberDrifts.push({ chunkId: pair.chunkId, id: pair.id, missingNumbers });
  }

  return { pairsChecked: pairs.length, termOccurrences, termMisses, termDrifts, repeatDrifts, numberDrifts };
}
