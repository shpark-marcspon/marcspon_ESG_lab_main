import type { ExampleRow } from "./examples.js";
import type { Factor, LicenseStatus } from "../domain/factor.js";
import type { ShortlistQuery } from "../domain/lexicalV1.js";
import type { FactorCatalog, ShortlistFailure, Shortlister } from "../domain/ports.js";
import { SUPPORTED_CATEGORIES, type SupportedCategory } from "../domain/types.js";
import { activityDim, conversionFactor } from "../domain/units.js";

/** 앞선 평가 실행이 남긴 행별 판단(`rows.json`) 중 재현율을 다시 재는 데 쓰는 부분. */
export interface PriorRow {
  rowId: string;
  source: "memory" | "llm";
  predictedCategory: number | "other" | null;
  keywords: string[] | null;
  shortlists: { basis: "physical" | "spend"; total: number }[];
}

/** 추천 호출 하나를 다시 추릴 때의 입력. 카테고리, 영어 검색어, 어느 기반을 불렀는지는 앞선 실행의 것을 그대로 쓴다. */
export interface RecallCall {
  rowId: string;
  basis: "physical" | "spend";
  candidates: Factor[];
  query: ShortlistQuery;
}

export interface RecallTarget {
  rowId: string;
  itemName: string;
  goldIds: string[];
  calls: RecallCall[];
}

/** 방식 하나로 다시 추렸을 때, 호출 하나에서 정답이 몇 등이었는지. 정답이 그 호출의 후보에 없으면 `rank` 는 null. */
export interface GoldRank {
  basis: "physical" | "spend";
  candidates: number;
  rank: number | null;
  goldId: string | null;
  /** 낱말 검색 점수가 있는 방식만: 정답의 점수, 점수가 더 높은 후보 수, 정답과 점수가 같은 다른 후보 수. */
  lexical: { score: number; higher: number; tied: number } | null;
}

export interface RecallRow {
  rowId: string;
  itemName: string;
  /** 호출들 가운데 가장 좋은 순위. 어느 호출의 후보에도 정답이 없으면 null. */
  bestRank: number | null;
  calls: GoldRank[];
}

export interface MethodRecall {
  label: string;
  method: string;
  description: string;
  /** 추린 수별 재현율. 분모는 정답 계수가 있고 기억에서 오지 않았고 앞선 실행에서 추천 호출까지 간 행이다. */
  bySize: { size: number; hits: number; evaluated: number; rate: number | null; missed: { rowId: string; itemName: string; bestRank: number | null }[] }[];
  rows: RecallRow[];
}

export type RecallOutcome = { ok: true; result: MethodRecall } | { ok: false; label: string; rowId: string; failure: ShortlistFailure };

function isSupported(category: unknown): category is SupportedCategory {
  return (SUPPORTED_CATEGORIES as readonly unknown[]).includes(category);
}

/**
 * 앞선 실행의 행별 판단에서 다시 추릴 호출을 만든다. 생성 모델은 부르지 않는다.
 * 후보 집합은 파이프라인과 같은 규칙으로 다시 만들고, 그 수가 앞선 실행이 남긴 수와 다르면 계수 표나 설정이 달라진 것이므로 예외다.
 */
export function recallTargets(examples: readonly ExampleRow[], prior: readonly PriorRow[], store: FactorCatalog, excludeLicenseStatuses: readonly LicenseStatus[] = []): RecallTarget[] {
  const priorById = new Map(prior.map((entry) => [entry.rowId, entry]));
  const targets: RecallTarget[] = [];
  for (const example of examples) {
    const entry = priorById.get(example.row.rowId);
    if (!entry) throw new Error(`앞선 실행의 결과에 행이 없다: ${example.row.rowId}`);
    if (example.gold.factorIds.length === 0 || entry.source === "memory" || entry.shortlists.length === 0) continue;
    const category = entry.predictedCategory;
    if (!isSupported(category)) throw new Error(`추천 호출까지 간 행인데 카테고리가 없다: ${example.row.rowId}`);
    const row = example.row;
    const query: ShortlistQuery = { texts: [row.itemName, ...(row.transport ? [row.transport.mode] : [])], keywords: entry.keywords ?? [] };
    const calls = entry.shortlists.map((call): RecallCall => {
      const all = store.candidates({ category, basis: call.basis, excludeLicenseStatuses });
      const candidates = call.basis === "physical" ? all.filter((factor) => conversionFactor(activityDim(row), factor.denominatorDim) !== null) : all;
      if (candidates.length !== call.total) {
        throw new Error(`행 ${row.rowId} 의 ${call.basis} 후보 수가 앞선 실행과 다르다: ${candidates.length} != ${call.total}`);
      }
      return { rowId: row.rowId, basis: call.basis, candidates, query };
    });
    targets.push({ rowId: row.rowId, itemName: row.itemName, goldIds: example.gold.factorIds, calls });
  }
  return targets;
}

/** 한 방식으로 모든 호출을 다시 추려 추린 수별 재현율과 정답의 순위를 낸다. 추리기가 실패하면 수치를 내지 않고 실패를 돌려준다. */
export async function measureRecall(label: string, shortlister: Shortlister, targets: readonly RecallTarget[], sizes: readonly number[]): Promise<RecallOutcome> {
  const rows: RecallRow[] = [];
  for (const target of targets) {
    const gold = new Set(target.goldIds);
    const calls: GoldRank[] = [];
    for (const call of target.calls) {
      const outcome = await shortlister.shortlist(call.candidates, call.query, call.candidates.length);
      if (!outcome.ok) return { ok: false, label, rowId: target.rowId, failure: outcome.failure };
      const position = outcome.factors.findIndex((factor) => gold.has(factor.id));
      const scores = outcome.evidence.map((entry) => entry.lexical?.score ?? 0);
      const scored = outcome.lexicalMatches !== null && position >= 0;
      const own = scored ? (scores[position] as number) : 0;
      calls.push({
        basis: call.basis,
        candidates: call.candidates.length,
        rank: position < 0 ? null : position + 1,
        goldId: position < 0 ? null : (outcome.factors[position] as Factor).id,
        lexical: scored ? { score: own, higher: scores.filter((score) => score > own).length, tied: scores.filter((score) => score === own).length - 1 } : null,
      });
    }
    const ranks = calls.flatMap((call) => (call.rank === null ? [] : [call.rank]));
    rows.push({ rowId: target.rowId, itemName: target.itemName, bestRank: ranks.length === 0 ? null : Math.min(...ranks), calls });
  }
  const bySize = sizes.map((size) => {
    // 호출마다 앞의 size 건이 추린 목록이다. 한 호출에서라도 정답이 그 안에 들면 적중이다.
    const hit = (row: RecallRow) => row.calls.some((call) => call.rank !== null && call.rank <= size);
    const missed = rows.filter((row) => !hit(row)).map((row) => ({ rowId: row.rowId, itemName: row.itemName, bestRank: row.bestRank }));
    const hits = rows.length - missed.length;
    return { size, hits, evaluated: rows.length, rate: rows.length === 0 ? null : hits / rows.length, missed };
  });
  return { ok: true, result: { label, method: shortlister.method, description: shortlister.description, bySize, rows } };
}
