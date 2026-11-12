import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { z } from "zod";
import { validateAdjustments, type CurrencyAdjustment } from "../domain/calc.js";
import type { InputRow } from "../domain/types.js";

/** 예제 세트를 읽지 못했거나 형식이 다르다. */
export class ExampleSetError extends Error {
  override name = "ExampleSetError";
}

export const ROWS_FILE = "rows.synthetic.jsonl";
export const MEMORY_FILE = "memory.synthetic.json";
export const ADJUSTMENTS_FILE = "adjustments.synthetic.json";

const decimal = z.string().regex(/^-?\d+(\.\d+)?$/);

const rowSchema = z.strictObject({
  row_id: z.string().min(1),
  /** 예제는 전부 가상 내역이다. true 가 아니면 읽지 않는다. */
  synthetic: z.literal(true),
  kind: z.enum(["purchase", "capital", "logistics", "travel", "commute", "other"]),
  item_name: z.string().min(1),
  quantity: decimal.nullable(),
  unit: z.string().nullable(),
  amount: decimal.nullable(),
  currency: z.string().nullable(),
  date: z.iso.date(),
  org_unit: z.string().min(1),
  transport: z
    .strictObject({
      origin: z.string(),
      destination: z.string(),
      mode: z.string(),
      weight_kg: decimal.nullable(),
      distance_km: decimal.nullable(),
    })
    .nullable(),
  gold: z.strictObject({
    /** Scope 3 카테고리 번호. 사람도 가르기 어려운 행은 null. */
    category: z.number().int().min(1).max(15).nullable(),
    /** 정답으로 인정하는 계수 id (계수 표에 실제로 있는 것). 달 수 있는 계수가 없으면 빈 배열. */
    factor_ids: z.array(z.string().min(1)),
    note: z.string(),
  }),
});

export interface Gold {
  category: number | null;
  factorIds: string[];
  note: string;
}

export interface ExampleRow {
  row: InputRow;
  kind: z.infer<typeof rowSchema>["kind"];
  gold: Gold;
}

export interface ExampleSet {
  dir: string;
  rows: ExampleRow[];
  /** 기억 파일 경로. 없으면 null (기억 없이 평가한다). */
  memoryPath: string | null;
}

/** 예제 폴더의 `rows.synthetic.jsonl` 을 읽는다. 한 줄이라도 형식이 다르면 전체를 거부한다. */
export function loadExampleSet(dir: string): ExampleSet {
  const path = join(dir, ROWS_FILE);
  if (!existsSync(path)) throw new ExampleSetError(`예제 파일이 없다: ${path}`);
  const rows: ExampleRow[] = [];
  const ids = new Set<string>();
  readFileSync(path, "utf8")
    .split("\n")
    .forEach((line, index) => {
      if (line.trim() === "") return;
      let raw: unknown;
      try {
        raw = JSON.parse(line);
      } catch {
        throw new ExampleSetError(`${path} ${index + 1}행이 JSON 이 아니다`);
      }
      const parsed = rowSchema.safeParse(raw);
      if (!parsed.success) {
        throw new ExampleSetError(`${path} ${index + 1}행: ${parsed.error.issues.map((issue) => `${issue.path.join(".")} ${issue.message}`).join("; ")}`);
      }
      const data = parsed.data;
      if (ids.has(data.row_id)) throw new ExampleSetError(`${path} ${index + 1}행: row_id 가 겹친다 (${data.row_id})`);
      ids.add(data.row_id);
      rows.push({
        kind: data.kind,
        row: {
          rowId: data.row_id,
          itemName: data.item_name,
          quantity: data.quantity,
          unit: data.unit,
          amount: data.amount,
          currency: data.currency,
          date: data.date,
          orgUnit: data.org_unit,
          transport: data.transport
            ? {
                origin: data.transport.origin,
                destination: data.transport.destination,
                mode: data.transport.mode,
                weightKg: data.transport.weight_kg,
                distanceKm: data.transport.distance_km,
              }
            : null,
        },
        gold: { category: data.gold.category, factorIds: data.gold.factor_ids, note: data.gold.note },
      });
    });
  if (rows.length === 0) throw new ExampleSetError(`${path} 에 행이 없다`);
  const memoryPath = join(dir, MEMORY_FILE);
  return { dir, rows, memoryPath: existsSync(memoryPath) ? memoryPath : null };
}

const adjustmentsSchema = z.strictObject({
  synthetic: z.boolean(),
  note: z.string().optional(),
  adjustments: z.array(
    z.strictObject({
      currency: z.string(),
      targetCurrency: z.string(),
      targetPriceYear: z.number().int(),
      multiplier: decimal,
      synthetic: z.boolean(),
      source: z.strictObject({ url: z.string(), checkedOn: z.string() }).nullable(),
    }),
  ),
});

/** 환율·물가 보정 계수 파일을 읽는다. 출처 없는 값은 `synthetic` 표시가 있어야 통과한다. */
export function loadAdjustments(path: string): CurrencyAdjustment[] {
  if (!existsSync(path)) throw new ExampleSetError(`보정 계수 파일이 없다: ${path}`);
  let raw: unknown;
  try {
    raw = JSON.parse(readFileSync(path, "utf8"));
  } catch {
    throw new ExampleSetError(`보정 계수 파일이 JSON 이 아니다: ${path}`);
  }
  const parsed = adjustmentsSchema.safeParse(raw);
  if (!parsed.success) throw new ExampleSetError(`보정 계수 파일 형식이 다르다: ${path}`);
  validateAdjustments(parsed.data.adjustments);
  return parsed.data.adjustments;
}
