import { z } from "zod";
import type { DocModel } from "../domain/facts.js";

// 시험지. 방법 비교 실측(MARC-TICK-21)에서 「오류를 잡는가」를 재려고, 문서 모델의 사본에 오류를 심는다.
// 원본 문서 모델은 고치지 않는다. 심는 것은 숫자 칸의 글자뿐이고 머리글·단위 글자는 그대로다.
// 심는 자리는 손으로 고르지 않는다: 정답 쌍 가운데 종류별 조건에 맞는 것에서 씨앗 고정 난수로 뽑는다.

/** 같은 항목임을 원문으로 확인한 쌍 (문서 A 의 칸, 문서 B 의 칸). 칸 ID 는 `표 ID:r행c열`. */
export const pairsSchema = z.object({
  note: z.string(),
  /** 누가 달았고 검토를 받았는가. */
  status: z.string().min(1),
  docs: z.object({ a: z.object({ file: z.string(), sha256: z.string() }), b: z.object({ file: z.string(), sha256: z.string() }) }),
  pairs: z.array(
    z.object({
      id: z.string(),
      /** 사람이 읽는 이름 (채점에는 쓰지 않는다). */
      item: z.string(),
      period: z.string().nullable(),
      a: z.string(),
      b: z.string(),
      /** 원본 문서의 글자. 읽을 때 문서와 대조한다. */
      rawA: z.string(),
      rawB: z.string(),
      /** seed: 밴드 P 의 41쌍. added: 이번에 더한 쌍. */
      origin: z.enum(["seed", "added"]),
      /** 밴드 P 의 네 표(양쪽 둘씩) 안의 쌍인가. */
      inSeedTables: z.boolean(),
    }),
  ),
});
export type GoldPairs = z.infer<typeof pairsSchema>;
export type GoldPair = GoldPairs["pairs"][number];

export const PLANT_KINDS = ["digit_changed", "digits_transposed", "digit_dropped", "digit_added", "times_1000", "row_shift", "year_swap", "one_of_two_places", "rounding_control"] as const;
export type PlantKind = (typeof PLANT_KINDS)[number];

export const plantsSchema = z.object({
  note: z.string(),
  status: z.string().min(1),
  seed: z.number().int(),
  /** 뽑기에 쓴 계획 (종류와 뽑는 범위). */
  plan: z.array(z.object({ kind: z.enum(PLANT_KINDS), pool: z.enum(["any", "seed_tables", "outside"]) })),
  plants: z.array(
    z.object({
      id: z.string(),
      kind: z.enum(PLANT_KINDS),
      /** 오류인가. 반올림 허용폭 안의 차이(대조군)는 오류가 아니다. */
      isError: z.boolean(),
      cells: z.array(z.object({ cell: z.string(), original: z.string(), planted: z.string(), pair: z.string() })).min(1),
      /** 같은 항목이 문서 B 의 다른 곳에도 실려 있으면 그 칸 (손대지 않은 쪽). */
      otherPlaceInB: z.array(z.string()),
    }),
  ),
});
export type PlantSpec = z.infer<typeof plantsSchema>;
export type Plant = PlantSpec["plants"][number];

export class ExamError extends Error {
  override name = "ExamError";
}

/** 씨앗 고정 난수 (mulberry32). 값이 아니라 뽑기 순번에만 쓴다. */
export function prng(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const pick = <T>(items: T[], random: () => number): T => items[Math.floor(random() * items.length)] as T;

// ── 숫자 글자 바꾸기 (글자 조작이다. 값의 연산이 아니다) ──────────────────────

const PLAIN_NUMBER = /^\d{1,3}(,\d{3})*(\.\d+)?$|^\d+(\.\d+)?$/;

function split(raw: string): { digits: string; fraction: string; commas: boolean } {
  if (!PLAIN_NUMBER.test(raw)) throw new ExamError(`숫자 칸의 글자가 아니다: ${raw}`);
  const [whole = "", fraction] = raw.split(".");
  return { digits: whole.replace(/,/g, ""), fraction: fraction === undefined ? "" : `.${fraction}`, commas: whole.includes(",") || whole.length <= 3 };
}

function join(digits: string, fraction: string, commas: boolean): string {
  return `${commas ? digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",") : digits}${fraction}`;
}

/** 종류별로 글자를 바꾼다. 조건에 안 맞으면 null. 정수 부분만 건드린다. */
export function mutate(kind: Exclude<PlantKind, "row_shift" | "year_swap" | "one_of_two_places">, raw: string, random: () => number): string | null {
  if (!PLAIN_NUMBER.test(raw)) return null;
  const { digits, fraction, commas } = split(raw);
  const chars = [...digits];
  if (kind === "digit_changed") {
    const at = Math.floor(random() * chars.length);
    const choices = "0123456789".split("").filter((d) => d !== chars[at] && !(at === 0 && d === "0"));
    chars[at] = pick(choices, random);
    return join(chars.join(""), fraction, commas);
  }
  if (kind === "digits_transposed") {
    const spots = chars.map((_, i) => i).filter((i) => i + 1 < chars.length && chars[i] !== chars[i + 1] && !(i === 0 && chars[i + 1] === "0"));
    if (spots.length === 0) return null;
    const at = pick(spots, random);
    [chars[at], chars[at + 1]] = [chars[at + 1] as string, chars[at] as string];
    return join(chars.join(""), fraction, commas);
  }
  if (kind === "digit_dropped") {
    if (chars.length < 3) return null;
    const spots = chars.map((_, i) => i).filter((i) => !(i === 0 && chars[1] === "0"));
    chars.splice(pick(spots, random), 1);
    return join(chars.join(""), fraction, commas);
  }
  if (kind === "digit_added") {
    const at = Math.floor(random() * (chars.length + 1));
    const choices = "0123456789".split("").filter((d) => !(at === 0 && d === "0"));
    chars.splice(at, 0, pick(choices, random));
    return join(chars.join(""), fraction, commas);
  }
  if (kind === "times_1000") return fraction === "" ? join(`${digits}000`, "", commas) : null;
  // rounding_control: 소수 아래를 적지 않는다. 버린 소수가 0.5 미만일 때만 쓴다 (반올림하면 같은 수라 허용폭 안이고 오류가 아니다).
  if (fraction === "" || /^\.0*$/.test(fraction) || (fraction[1] as string) >= "5") return null;
  return join(digits, "", commas);
}

// ── 뽑기 ────────────────────────────────────────────────────────────────────

const cellParts = (cell: string): { table: string; row: number; col: number } => {
  const match = /^(.+):r(\d+)c(\d+)$/.exec(cell);
  if (!match) throw new ExamError(`칸 ID 꼴이 아니다: ${cell}`);
  return { table: match[1] as string, row: Number(match[2]), col: Number(match[3]) };
};

/**
 * 계획대로 심을 자리를 뽑는다. 쓴 칸과, 그 칸과 같은 항목이 문서 B 의 다른 곳에 실린 칸은 다음 뽑기에서 뺀다
 * (「한 곳만 틀리게」가 유지되도록).
 */
export function drawPlants(pairs: GoldPair[], plan: PlantSpec["plan"], seed: number): Plant[] {
  const random = prng(seed);
  const used = new Set<string>();
  const byB = new Map(pairs.map((pair) => [pair.b, pair]));
  // 같은 항목(문서 A 의 같은 값을 가리키는 것이 아니라, 이름과 기간이 같은 것)이 B 의 두 칸에 실린 경우.
  const sameItem = (pair: GoldPair): GoldPair[] => pairs.filter((other) => other.id !== pair.id && other.item === pair.item && other.period === pair.period && other.rawB.replace(/\.0+$/, "") === pair.rawB.replace(/\.0+$/, ""));
  const inPool = (pair: GoldPair, pool: PlantSpec["plan"][number]["pool"]): boolean => pool === "any" || (pool === "seed_tables") === pair.inSeedTables;
  const free = (pair: GoldPair): boolean => !used.has(pair.b);
  const take = (pair: GoldPair): void => {
    used.add(pair.b);
    for (const other of sameItem(pair)) used.add(other.b);
  };
  const plants: Plant[] = [];
  plan.forEach((step, index) => {
    const id = `E${String(index + 1).padStart(2, "0")}`;
    const pool = pairs.filter((pair) => inPool(pair, step.pool) && free(pair));
    if (step.kind === "row_shift" || step.kind === "year_swap") {
      // 이웃한 두 칸: 행 밀림은 같은 열의 위아래 행, 연도 열 뒤바뀜은 같은 행의 이웃 열. 둘 다 정답 쌍이고 글자가 달라야 한다.
      const couples: [GoldPair, GoldPair][] = [];
      for (const first of pool) {
        const p = cellParts(first.b);
        const other = byB.get(step.kind === "row_shift" ? `${p.table}:r${p.row + 1}c${p.col}` : `${p.table}:r${p.row}c${p.col + 1}`);
        if (other && inPool(other, step.pool) && free(other) && other.rawB !== first.rawB) couples.push([first, other]);
      }
      if (couples.length === 0) throw new ExamError(`${id} ${step.kind}: 조건에 맞는 자리가 없다`);
      const [first, second] = pick(couples, random);
      plants.push({ id, kind: step.kind, isError: true, cells: [{ cell: first.b, original: first.rawB, planted: second.rawB, pair: first.id }, { cell: second.b, original: second.rawB, planted: first.rawB, pair: second.id }], otherPlaceInB: [...sameItem(first), ...sameItem(second)].map((pair) => pair.b) });
      take(first);
      take(second);
      return;
    }
    const kind = step.kind === "one_of_two_places" ? "digit_changed" : step.kind;
    const candidates = pool.filter((pair) => (step.kind === "one_of_two_places" ? sameItem(pair).length > 0 : true) && mutate(kind, pair.rawB, prng(1)) !== null);
    if (candidates.length === 0) throw new ExamError(`${id} ${step.kind}: 조건에 맞는 자리가 없다`);
    const chosen = pick(candidates, random);
    const planted = mutate(kind, chosen.rawB, random) as string;
    plants.push({ id, kind: step.kind, isError: step.kind !== "rounding_control", cells: [{ cell: chosen.b, original: chosen.rawB, planted, pair: chosen.id }], otherPlaceInB: sameItem(chosen).map((pair) => pair.b) });
    take(chosen);
  });
  return plants;
}

// ── 사본에 심기와 확인 ──────────────────────────────────────────────────────

interface TablesOnly {
  tables: { id: string; cells: string[][] }[];
}

/** 명세대로 사본에 심는다. 원래 글자가 명세와 다르면 거부한다. 넘겨받은 문서는 고치지 않고 새 사본을 돌려준다. */
export function applyPlants<T extends TablesOnly>(document: T, spec: Pick<PlantSpec, "plants">): T {
  const copy = structuredClone(document);
  const tables = new Map(copy.tables.map((table) => [table.id, table]));
  const seen = new Set<string>();
  for (const plant of spec.plants) {
    for (const change of plant.cells) {
      if (seen.has(change.cell)) throw new ExamError(`${plant.id}: 같은 칸을 두 번 고친다: ${change.cell}`);
      seen.add(change.cell);
      const { table, row, col } = cellParts(change.cell);
      const cells = tables.get(table)?.cells[row];
      const current = cells?.[col];
      if (cells === undefined || current === undefined) throw new ExamError(`${plant.id}: 없는 칸이다: ${change.cell}`);
      // 원본의 글자와 대조한다 (사본은 이미 고쳐졌을 수 있으므로 넘겨받은 문서를 본다).
      const original = document.tables.find((item) => item.id === table)?.cells[row]?.[col];
      if (original !== change.original) throw new ExamError(`${plant.id}: ${change.cell} 의 원래 글자가 명세와 다르다: 문서 「${original}」, 명세 「${change.original}」`);
      cells[col] = change.planted;
    }
  }
  return copy;
}

/** 「명세 밖의 칸은 원본과 같다」와 「명세의 칸은 바꾼 글자다」를 확인한다. 표 밖의 것(쪽, 블록, 머리글 추정)도 전부 같아야 한다. */
export function checkExam(original: DocModel & TablesOnly, exam: DocModel & TablesOnly, spec: Pick<PlantSpec, "plants">): string[] {
  const problems: string[] = [];
  const planted = new Map(spec.plants.flatMap((plant) => plant.cells.map((change) => [change.cell, change] as const)));
  const strip = (document: object): string => JSON.stringify({ ...document, tables: (document as TablesOnly).tables.map((table) => ({ ...table, cells: null })) });
  if (strip(original) !== strip(exam)) problems.push("표의 칸 말고 다른 것이 원본과 다르다");
  if (original.tables.length !== exam.tables.length) return [...problems, "표의 수가 다르다"];
  const found = new Set<string>();
  original.tables.forEach((table, t) => {
    const other = exam.tables[t] as TablesOnly["tables"][number];
    if (other.id !== table.id || other.cells.length !== table.cells.length) return void problems.push(`${table.id}: 표의 꼴이 다르다`);
    table.cells.forEach((row, r) => {
      if ((other.cells[r] as string[]).length !== row.length) return void problems.push(`${table.id}: r${r} 의 칸 수가 다르다`);
      row.forEach((text, c) => {
        const id = `${table.id}:r${r}c${c}`;
        const change = planted.get(id);
        const now = (other.cells[r] as string[])[c];
        if (!change) {
          if (now !== text) problems.push(`${id}: 명세 밖의 칸이 원본과 다르다 (「${text}」 → 「${now}」)`);
          return;
        }
        found.add(id);
        if (text !== change.original) problems.push(`${id}: 원본의 글자가 명세와 다르다`);
        if (now !== change.planted) problems.push(`${id}: 시험지의 글자가 명세의 바꾼 글자와 다르다`);
      });
    });
  });
  for (const id of planted.keys()) if (!found.has(id)) problems.push(`${id}: 명세의 칸이 문서에 없다`);
  return problems;
}

/** 정답 쌍이 문서와 맞는지 (칸이 있고 글자가 같은지). */
export function checkPairs(gold: GoldPairs, a: TablesOnly, b: TablesOnly): string[] {
  const problems: string[] = [];
  const text = (document: TablesOnly, cell: string): string | undefined => {
    const { table, row, col } = cellParts(cell);
    return document.tables.find((item) => item.id === table)?.cells[row]?.[col];
  };
  const ids = new Set<string>();
  for (const pair of gold.pairs) {
    if (ids.has(pair.id)) problems.push(`${pair.id}: 쌍 ID 가 겹친다`);
    ids.add(pair.id);
    if (text(a, pair.a) !== pair.rawA) problems.push(`${pair.id}: 문서 A 의 ${pair.a} 가 「${text(a, pair.a)}」 이고 정답은 「${pair.rawA}」`);
    if (text(b, pair.b) !== pair.rawB) problems.push(`${pair.id}: 문서 B 의 ${pair.b} 가 「${text(b, pair.b)}」 이고 정답은 「${pair.rawB}」`);
  }
  return problems;
}
