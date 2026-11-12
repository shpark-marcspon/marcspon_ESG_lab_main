import { z } from "zod";
import type { Extraction, Fact, FactBucket } from "../domain/facts.js";
import { VALUE_KINDS, type ValueKind } from "../domain/kinds.js";
import { prng } from "./exam.js";

// 밴드 V (MARC-TICK-25): 값 후보의 종류의 정답. 종류 규칙(`src/domain/kinds.ts` 의 `kindOf`)과 따로 둔다:
// 정답은 사람이(러너가) 원문을 보고 달았고 규칙보다 먼저 확정했다. 이 파일은 정답의 형식, 대상 뽑기, 정답과 문서의 대조만 한다.

/** 정답을 다는 대상: 서술 값 후보(문장 속, 숫자뿐인 블록)는 전부, 표의 글 칸 속과 표의 숫자 칸은 씨앗 고정 난수로 뽑은 표본. */
export const KIND_SAMPLE_BUCKETS = ["table_text_cell", "table_cell"] as const satisfies readonly FactBucket[];
export const KIND_ALL_BUCKETS = ["sentence", "number_only_block"] as const satisfies readonly FactBucket[];

export const kindGoldSchema = z.object({
  note: z.string(),
  status: z.string().min(1),
  seed: z.number().int(),
  perBucket: z.number().int().positive(),
  items: z.array(
    z.object({
      doc: z.enum(["a", "b"]),
      id: z.string(),
      bucket: z.enum(["table_cell", "table_text_cell", "sentence", "number_only_block"]),
      /** 그 자리의 원문 글자 (원본 문서 기준. 정답이 문서와 맞는지 확인하는 데 쓴다). */
      raw: z.string(),
      kind: z.enum(VALUE_KINDS),
      /** 종류의 경계에서 애매한 자리: 달 수 있었던 다른 종류. 애매하지 않으면 null. */
      ambiguousWith: z.enum(VALUE_KINDS).nullable(),
      basis: z.string().min(1),
    }),
  ),
});
export type KindGold = z.infer<typeof kindGoldSchema>;
export type KindGoldItem = KindGold["items"][number];

export interface KindTarget {
  doc: "a" | "b";
  id: string;
  bucket: FactBucket;
  raw: string;
}

/**
 * 정답을 달 대상. 서술 값 후보는 전부, 표의 글 칸 속·표의 숫자 칸은 두 문서를 합친 목록(문서 a 먼저, 뽑힌 순서)에서 갈래마다 `perBucket` 개를
 * 씨앗 고정 난수로 뽑는다 (모자라면 전부). 손으로 고르지 않는다. 결과는 문서, 뽑힌 순서로 늘어놓는다.
 */
export function drawKindTargets(a: Extraction, b: Extraction, seed: number, perBucket: number): KindTarget[] {
  const all: (KindTarget & { at: number })[] = [...a.facts.map((fact, at) => ({ doc: "a" as const, fact, at })), ...b.facts.map((fact, at) => ({ doc: "b" as const, fact, at: a.facts.length + at }))].map(({ doc, fact, at }) => ({ doc, id: fact.id, bucket: fact.bucket, raw: fact.raw, at }));
  const random = prng(seed);
  const chosen = all.filter((item) => (KIND_ALL_BUCKETS as readonly string[]).includes(item.bucket));
  for (const bucket of KIND_SAMPLE_BUCKETS) {
    const pool = all.filter((item) => item.bucket === bucket);
    for (let n = 0; n < perBucket && pool.length > 0; n++) chosen.push(...pool.splice(Math.floor(random() * pool.length), 1));
  }
  return chosen.sort((x, y) => x.at - y.at).map(({ at: _at, ...rest }) => rest);
}

/** 정답이 문서와 맞는가: 대상이 다시 뽑은 것과 같고(빠짐·더함 없음), 자리마다 원문 글자가 같다. */
export function checkKindGold(gold: KindGold, a: Extraction, b: Extraction): string[] {
  const problems: string[] = [];
  const targets = drawKindTargets(a, b, gold.seed, gold.perBucket);
  const key = (item: { doc: string; id: string }): string => `${item.doc}:${item.id}`;
  const wanted = new Map(targets.map((item) => [key(item), item]));
  const seen = new Set<string>();
  for (const item of gold.items) {
    const target = wanted.get(key(item));
    if (seen.has(key(item))) problems.push(`${key(item)}: 정답에 두 번 있다`);
    seen.add(key(item));
    if (!target) problems.push(`${key(item)}: 뽑은 대상에 없는 자리다`);
    else if (target.raw !== item.raw || target.bucket !== item.bucket) problems.push(`${key(item)}: 원문 글자나 갈래가 문서와 다르다 (${item.raw} / ${target.raw})`);
  }
  for (const target of targets) if (!seen.has(key(target))) problems.push(`${key(target)}: 정답이 없다`);
  return problems;
}

export type { Fact, ValueKind };
