import { z } from "zod";
import type { DocModel, Extraction, Fact } from "../domain/facts.js";
import { ExamError, mutate, prng, type PlantSpec } from "./exam.js";

// 넓힌 시험지 (MARC-TICK-24). 밴드 R 의 정답 쌍·명세는 그대로 두고, 그 위에 둘을 더한다.
//  - 서술이 낀 정답 쌍: 한 문서의 문장 속(또는 표의 글 칸 속, 숫자뿐인 블록) 숫자가 다른 문서의 자리와 같은 항목인 것.
//  - 문서 안 정답 묶음: 한 문서 안에서 같은 항목이 둘 이상의 자리에 실린 것.
// 심는 것은 두 문서 모델의 사본에만 한다. 서술에 심을 때는 그 숫자 덩어리의 글자만 바꾼다.
// 자리의 ID 는 값 후보의 ID 다: 표의 숫자 칸은 `표 ID:r행c열`, 글 속 숫자는 `블록 ID#n순번`, 표의 글 칸 속 숫자는 `표 ID:r행c열#n순번`.

const placeSchema = z.object({ id: z.string(), raw: z.string() });

export const gold2Schema = z.object({
  note: z.string(),
  status: z.string().min(1),
  docs: z.object({ a: z.object({ file: z.string(), sha256: z.string() }), b: z.object({ file: z.string(), sha256: z.string() }) }),
  /** 서술이 낀 두 문서 사이의 정답 쌍. */
  prosePairs: z.array(
    z.object({
      id: z.string(),
      item: z.string(),
      period: z.string().nullable(),
      a: z.string(),
      b: z.string(),
      rawA: z.string(),
      rawB: z.string(),
      /** 표기의 자릿수가 서로 다른 쌍 (원문 그대로. 오류가 아니다). 심는 대상에서 뺀다. */
      control: z.enum(["natural_rounding"]).nullable(),
      /** 원문에서 확인한 근거 (쪽, 문장이나 행·열). */
      evidence: z.string().min(1),
    }),
  ),
  /** 한 문서 안의 정답 묶음. */
  groups: z.array(
    z.object({
      id: z.string(),
      doc: z.enum(["a", "b"]),
      item: z.string(),
      period: z.string().nullable(),
      places: z.array(placeSchema).min(2),
      control: z.enum(["natural_rounding"]).nullable(),
      evidence: z.string().min(1),
    }),
  ),
});
export type Gold2 = z.infer<typeof gold2Schema>;
export type ProsePair = Gold2["prosePairs"][number];
export type GoldGroup = Gold2["groups"][number];

export const PLANT2_KINDS = ["digit_changed", "digits_transposed", "digit_dropped", "digit_added", "times_10", "times_1000", "rounding_control"] as const;
export type Plant2Kind = (typeof PLANT2_KINDS)[number];
/** prose_pair: 서술이 낀 정답 쌍의 서술 쪽. group_a·group_b: 그 문서의 문서 안 묶음(두 문서 사이의 정답 쌍과 겹치지 않는 것)의 자리. */
export const PLANT2_POOLS = ["prose_pair", "group_a", "group_b"] as const;

export const plants2Schema = z.object({
  note: z.string(),
  status: z.string().min(1),
  seed: z.number().int(),
  plan: z.array(z.object({ kind: z.enum(PLANT2_KINDS), pool: z.enum(PLANT2_POOLS) })),
  /** 계획에 있었지만 조건에 맞는 자리가 없어 심지 못한 것. */
  skipped: z.array(z.object({ step: z.number().int(), kind: z.enum(PLANT2_KINDS), pool: z.enum(PLANT2_POOLS), why: z.string() })),
  plants: z.array(
    z.object({
      id: z.string(),
      kind: z.enum(PLANT2_KINDS),
      isError: z.boolean(),
      pool: z.enum(PLANT2_POOLS),
      doc: z.enum(["a", "b"]),
      target: z.string(),
      original: z.string(),
      planted: z.string(),
      /** 이 자리가 든 정답 쌍과 정답 묶음. */
      pairs: z.array(z.string()),
      groups: z.array(z.string()),
    }),
  ),
});
export type Plant2Spec = z.infer<typeof plants2Schema>;
export type Plant2 = Plant2Spec["plants"][number];

/** 표의 숫자 칸이 아닌 자리인가 (ID 의 꼴로 본다). */
export const isProseId = (id: string): boolean => /#n\d+$/.test(id);

// ── 숫자 글자 바꾸기 (글자 조작이다. 값의 연산이 아니다) ──────────────────────

const PLAIN = /^\d{1,3}(,\d{3})*(\.\d+)?$|^\d+(\.\d+)?$/;
const withCommas = (digits: string): string => digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");

/** 숫자 덩어리의 글자를 바꾼다. 앞의 부호와 뒤의 `%`·`%p` 는 그대로 둔다. 조건에 안 맞으면 null. */
export function mutate2(kind: Plant2Kind, raw: string, random: () => number): string | null {
  const parts = /^([-−+△▲▼]?)(.*?)(%p|%)?$/u.exec(raw);
  if (!parts) return null;
  const [, lead = "", body = "", suffix = ""] = parts;
  if (!PLAIN.test(body)) return null;
  let changed: string | null;
  if (kind === "times_10") {
    // 소수점을 한 자리 옮긴다. 정수면 0 을 하나 붙인다.
    const [whole = "", fraction = ""] = body.split(".");
    const commas = whole.includes(",") || whole.length <= 3;
    const digits = `${whole.replace(/,/g, "")}${fraction === "" ? "0" : fraction.slice(0, 1)}`.replace(/^0+(?=\d)/, "");
    changed = `${commas ? withCommas(digits) : digits}${fraction.length > 1 ? `.${fraction.slice(1)}` : ""}`;
  } else changed = mutate(kind, body, random);
  return changed === null || changed === body ? null : `${lead}${changed}${suffix}`;
}

// ── 뽑기 ────────────────────────────────────────────────────────────────────

const ref = (doc: "a" | "b", id: string): string => `${doc}:${id}`;

export interface DrawInput {
  gold: Gold2;
  /** 밴드 R 의 정답 쌍의 칸과 심은 칸 (`a:칸`, `b:칸`). 이 칸이 든 쌍·묶음에는 새로 심지 않는다. */
  v1Cells: Set<string>;
  plan: Plant2Spec["plan"];
  seed: number;
  /** 바꾼 글자로도 값 후보의 ID 와 갈래가 그대로인가 (숫자 뽑기가 달리 읽으면 저장된 정답의 ID 가 어긋난다). */
  survives: (doc: "a" | "b", target: string, planted: string) => boolean;
}

/**
 * 계획대로 심을 자리를 씨앗 고정 난수로 뽑는다. 자리를 하나 쓰면 그 자리와 같은 쌍·묶음에 든 자리는 다음 뽑기에서 뺀다 (「한 곳만 틀리게」).
 * 조건에 맞는 자리가 없는 단계는 건너뛰고 `skipped` 에 적는다 (억지로 채우지 않는다).
 */
export function drawPlants2(input: DrawInput): Pick<Plant2Spec, "plants" | "skipped"> {
  const { gold, v1Cells, plan, seed } = input;
  const random = prng(seed);
  // 자리 → 그 자리가 든 쌍·묶음과 원문 글자.
  const places = new Map<string, { doc: "a" | "b"; id: string; raw: string; pairs: string[]; groups: string[] }>();
  const mates = new Map<string, Set<string>>();
  const link = (members: string[]): void => {
    for (const member of members) mates.set(member, new Set([...(mates.get(member) ?? []), ...members]));
  };
  const put = (doc: "a" | "b", id: string, raw: string): { pairs: string[]; groups: string[] } => {
    const entry = places.get(ref(doc, id)) ?? { doc, id, raw, pairs: [], groups: [] };
    places.set(ref(doc, id), entry);
    return entry;
  };
  for (const pair of gold.prosePairs) {
    put("a", pair.a, pair.rawA).pairs.push(pair.id);
    put("b", pair.b, pair.rawB).pairs.push(pair.id);
    link([ref("a", pair.a), ref("b", pair.b)]);
  }
  for (const group of gold.groups) {
    for (const place of group.places) put(group.doc, place.id, place.raw).groups.push(group.id);
    link(group.places.map((place) => ref(group.doc, place.id)));
  }
  const pairOf = new Map(gold.prosePairs.map((pair) => [pair.id, pair]));
  const groupOf = new Map(gold.groups.map((group) => [group.id, group]));
  /** 이 자리와 한 다리 건너 이어진 자리 (같은 쌍이나 같은 묶음). */
  const near = (key: string): string[] => [...(mates.get(key) ?? [])];
  const blocked = (key: string): boolean => near(key).some((other) => v1Cells.has(other));
  const inPool = (key: string, pool: (typeof PLANT2_POOLS)[number]): boolean => {
    const place = places.get(key)!;
    if (blocked(key)) return false;
    if (place.pairs.some((id) => pairOf.get(id)!.control !== null) || place.groups.some((id) => groupOf.get(id)!.control !== null)) return false;
    if (pool === "prose_pair") return place.pairs.length > 0 && isProseId(place.id);
    // 문서 안 전용: 두 문서 사이의 정답 쌍에 든 자리가 하나도 없는 묶음의 자리.
    return place.doc === (pool === "group_a" ? "a" : "b") && place.pairs.length === 0 && place.groups.length > 0 && near(key).every((other) => places.get(other)!.pairs.length === 0);
  };
  const used = new Set<string>();
  const plants: Plant2[] = [];
  const skipped: Plant2Spec["skipped"] = [];
  plan.forEach((step, index) => {
    const candidates = [...places.keys()].filter((key) => inPool(key, step.pool) && !used.has(key) && mutate2(step.kind, places.get(key)!.raw, prng(1)) !== null);
    while (candidates.length > 0) {
      const at = Math.floor(random() * candidates.length);
      const key = candidates[at] as string;
      const place = places.get(key)!;
      const planted = mutate2(step.kind, place.raw, random);
      if (planted === null || !input.survives(place.doc, place.id, planted)) {
        candidates.splice(at, 1);
        continue;
      }
      plants.push({ id: `U${String(plants.length + 1).padStart(2, "0")}`, kind: step.kind, isError: step.kind !== "rounding_control", pool: step.pool, doc: place.doc, target: place.id, original: place.raw, planted, pairs: [...place.pairs], groups: [...place.groups] });
      // 이 자리, 그리고 이 자리와 이어진 자리들과 다시 그 자리들과 이어진 자리들까지 뺀다 (한 쌍·한 묶음에 심은 자리는 하나).
      for (const other of near(key)) for (const far of [other, ...near(other)]) used.add(far);
      return;
    }
    skipped.push({ step: index + 1, kind: step.kind, pool: step.pool, why: "조건에 맞는 자리가 없다" });
  });
  return { plants, skipped };
}

// ── 사본에 심기와 확인 ──────────────────────────────────────────────────────

export interface Change {
  doc: "a" | "b";
  target: string;
  original: string;
  planted: string;
}

/** 밴드 R 의 명세(문서 B 의 숫자 칸)와 이번 명세의 바꿀 자리를 한 목록으로. */
export function allChanges(v1: Pick<PlantSpec, "plants">, v2: Pick<Plant2Spec, "plants">): Change[] {
  return [...v1.plants.flatMap((plant) => plant.cells.map((cell) => ({ doc: "b" as const, target: cell.cell, original: cell.original, planted: cell.planted }))), ...v2.plants.map((plant) => ({ doc: plant.doc, target: plant.target, original: plant.original, planted: plant.planted }))];
}

type Where = { kind: "cell"; table: string; row: number; col: number; span: { start: number; end: number } | null } | { kind: "block"; block: string; span: { start: number; end: number } };

function locate(fact: Fact): Where {
  const span = fact.where.span ? { start: fact.where.span.start, end: fact.where.span.end } : null;
  if (fact.where.gridId !== null) return { kind: "cell", table: fact.where.gridId, row: fact.where.row as number, col: fact.where.col as number, span: fact.bucket === "table_cell" ? null : span };
  if (fact.where.blockId === null || span === null) throw new ExamError(`${fact.id}: 자리를 찾을 수 없다`);
  return { kind: "block", block: fact.where.blockId, span };
}

/**
 * 바꿀 자리 목록대로 사본에 심는다. `extraction` 은 원본에서 뽑은 것이다 (자리의 위치를 원본의 값 후보에서 읽는다).
 * 원래 글자가 명세와 다르면 거부한다. 넘겨받은 문서는 고치지 않고 새 사본을 돌려준다.
 */
export function applyChanges(document: DocModel, extraction: Extraction, changes: Change[]): DocModel {
  const copy = structuredClone(document);
  const facts = new Map(extraction.facts.map((fact) => [fact.id, fact]));
  const seen = new Set<string>();
  // 한 글 안의 여러 자리는 뒤에서부터 바꾼다 (앞 자리의 위치가 밀리지 않게).
  const jobs = changes.map((change) => {
    if (seen.has(change.target)) throw new ExamError(`같은 자리를 두 번 고친다: ${change.target}`);
    seen.add(change.target);
    const fact = facts.get(change.target);
    if (!fact) throw new ExamError(`없는 자리다: ${change.target}`);
    return { change, where: locate(fact) };
  });
  jobs.sort((x, y) => (y.where.span?.start ?? 0) - (x.where.span?.start ?? 0));
  for (const { change, where } of jobs) {
    const swap = (text: string): string => {
      const current = where.span ? text.slice(where.span.start, where.span.end) : text;
      if (current !== change.original) throw new ExamError(`${change.target} 의 원래 글자가 명세와 다르다: 문서 「${current}」, 명세 「${change.original}」`);
      return where.span ? `${text.slice(0, where.span.start)}${change.planted}${text.slice(where.span.end)}` : change.planted;
    };
    if (where.kind === "cell") {
      const cells = copy.tables.find((table) => table.id === where.table)?.cells[where.row];
      if (cells === undefined || cells[where.col] === undefined) throw new ExamError(`없는 칸이다: ${change.target}`);
      cells[where.col] = swap(cells[where.col] as string);
    } else {
      const block = copy.blocks.find((item) => item.id === where.block);
      if (block?.text === undefined) throw new ExamError(`없는 블록이다: ${change.target}`);
      block.text = swap(block.text);
    }
  }
  return copy;
}

/**
 * 「명세 밖은 원본과 같다」와 「명세의 자리는 바꾼 글자다」를 확인한다. 심는 코드(`applyChanges`)와 따로 센다:
 * 두 문서 모델에서 글자가 다른 블록과 칸을 전부 찾아, 그 집합이 명세의 자리와 같고 다른 곳의 글자가 한 자도 안 바뀌었는지 본다.
 */
export function checkExam2(original: DocModel, exam: DocModel, extraction: Extraction, changes: Change[]): string[] {
  const problems: string[] = [];
  const strip = (document: DocModel): string => JSON.stringify({ ...document, blocks: document.blocks.map((block) => ({ ...block, text: block.text === undefined ? undefined : "" })), tables: document.tables.map((table) => ({ ...table, cells: table.cells.map((row) => row.map(() => "")) })) });
  if (strip(original) !== strip(exam)) return ["글자 말고 다른 것(블록·표의 수와 꼴, 쪽, 머리글 추정)이 원본과 다르다"];
  const facts = new Map(extraction.facts.map((fact) => [fact.id, fact]));
  // 자리(블록이나 칸)마다 기대하는 글자를 원본에서 따로 조립한다.
  const expected = new Map<string, { text: string; spans: { start: number; end: number; planted: string; original: string; target: string }[] }>();
  for (const change of changes) {
    const fact = facts.get(change.target);
    if (!fact) {
      problems.push(`${change.target}: 명세의 자리가 문서에 없다`);
      continue;
    }
    const where = locate(fact);
    const key = where.kind === "cell" ? `cell|${where.table}|${where.row}|${where.col}` : `block|${where.block}`;
    const text = where.kind === "cell" ? (original.tables.find((table) => table.id === where.table)?.cells[where.row]?.[where.col] ?? "") : (original.blocks.find((block) => block.id === where.block)?.text ?? "");
    const entry = expected.get(key) ?? { text, spans: [] };
    entry.spans.push({ start: where.span?.start ?? 0, end: where.span?.end ?? text.length, planted: change.planted, original: change.original, target: change.target });
    expected.set(key, entry);
  }
  const want = (key: string, text: string): string => {
    const entry = expected.get(key);
    if (!entry) return text;
    let out = text;
    for (const span of [...entry.spans].sort((x, y) => y.start - x.start)) {
      if (text.slice(span.start, span.end) !== span.original) problems.push(`${span.target}: 원본의 글자가 명세와 다르다`);
      if (span.planted === span.original) problems.push(`${span.target}: 바꾼 글자가 원래 글자와 같다`);
      out = `${out.slice(0, span.start)}${span.planted}${out.slice(span.end)}`;
    }
    return out;
  };
  original.blocks.forEach((block, index) => {
    const now = (exam.blocks[index] as DocModel["blocks"][number]).text;
    if (block.text === undefined) return;
    if (now !== want(`block|${block.id}`, block.text)) problems.push(`블록 ${block.id}: 시험지의 글자가 「원본에 명세만 적용한 글자」와 다르다`);
  });
  original.tables.forEach((table, t) => {
    table.cells.forEach((row, r) =>
      row.forEach((text, c) => {
        const now = (exam.tables[t] as DocModel["tables"][number]).cells[r]?.[c];
        if (now !== want(`cell|${table.id}|${r}|${c}`, text)) problems.push(`${table.id}:r${r}c${c}: 시험지의 글자가 「원본에 명세만 적용한 글자」와 다르다`);
      }),
    );
  });
  return problems;
}

/** 넓힌 정답이 원본 문서와 맞는지: 자리가 값 후보로 있고 글자가 같은지, ID 가 겹치지 않는지, 쌍의 한쪽 이상이 서술인지. */
export function checkGold2(gold: Gold2, a: Extraction, b: Extraction): string[] {
  const problems: string[] = [];
  const facts = { a: new Map(a.facts.map((fact) => [fact.id, fact])), b: new Map(b.facts.map((fact) => [fact.id, fact])) };
  const at = (doc: "a" | "b", id: string, raw: string, where: string): void => {
    const fact = facts[doc].get(id);
    if (!fact) problems.push(`${where}: 문서 ${doc} 에 값 후보 ${id} 가 없다`);
    else if (fact.raw !== raw) problems.push(`${where}: 문서 ${doc} 의 ${id} 가 「${fact.raw}」 이고 정답은 「${raw}」`);
  };
  const ids = new Set<string>();
  const once = (id: string): void => {
    if (ids.has(id)) problems.push(`${id}: ID 가 겹친다`);
    ids.add(id);
  };
  const seenPair = new Set<string>();
  for (const pair of gold.prosePairs) {
    once(pair.id);
    at("a", pair.a, pair.rawA, pair.id);
    at("b", pair.b, pair.rawB, pair.id);
    if (!isProseId(pair.a) && !isProseId(pair.b)) problems.push(`${pair.id}: 양쪽이 다 표의 숫자 칸이다 (서술이 낀 쌍이 아니다)`);
    if (seenPair.has(`${pair.a}|${pair.b}`)) problems.push(`${pair.id}: 같은 쌍이 두 번 있다`);
    seenPair.add(`${pair.a}|${pair.b}`);
  }
  const inGroup = new Map<string, string>();
  for (const group of gold.groups) {
    once(group.id);
    for (const place of group.places) {
      at(group.doc, place.id, place.raw, group.id);
      const key = `${group.doc}:${place.id}`;
      if (inGroup.has(key)) problems.push(`${group.id}: ${place.id} 가 묶음 ${inGroup.get(key)} 에도 있다 (한 자리는 한 묶음에만)`);
      inGroup.set(key, group.id);
    }
  }
  return problems;
}
