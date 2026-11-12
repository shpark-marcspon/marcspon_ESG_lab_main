// 파일 어댑터: 계수 표(jsonl)와 대응표(json)를 읽어 조립 함수가 받는 값으로 만든다. 파일을 읽는 자리는 여기와 cli.ts 뿐이다.

import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { z } from "zod";
import type { AssemblyMapping, FactorRecord } from "../domain/types.js";

/** 파일을 읽지 못했거나 형식이 다르다. 「낱줄 없음」과 구분하려고 예외로 둔다. */
export class AssemblyFileError extends Error {
  override name = "AssemblyFileError";
}

const here = dirname(fileURLToPath(import.meta.url));
const dataDir = join(here, "..", "..", "..", "..", "data");

export const defaultFactorsPath = (): string => join(dataDir, "factors", "factors.jsonl");
export const defaultMappingPath = (): string => join(dataDir, "factors", "mappings.kr-ets.json");

/** 계수 레코드에서 조합기가 읽는 필드 (`schemas/factor.schema.json` 의 부분 사본). `value`(number)는 일부러 읽지 않는다. */
export const factorRecordSchema = z.object({
  id: z.string().min(1),
  record_type: z.string().min(1),
  name: z.object({ original: z.string().min(1) }),
  category_path: z.array(z.string()),
  value_raw: z.string().nullable(),
  value_status: z.string().min(1),
  unit_numerator: z.string().min(1),
  unit_denominator: z.string().min(1),
  gas: z.string().min(1),
  gas_basis: z.string().min(1),
  heating_value_basis: z.enum(["net", "gross"]).nullable(),
  conditions: z.record(z.string(), z.string()),
  source: z.object({
    id: z.string().min(1),
    name: z.string().min(1),
    url: z.string().regex(/^https?:\/\//),
    version: z.string().min(1),
    retrieved: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
    file_name: z.string().min(1),
    file_sha256: z.string().regex(/^[0-9a-f]{64}$/),
    locator: z.string().min(1),
  }),
  synthetic: z.boolean(),
});

const basis = z.enum(["guideline", "assumed"]);
const state = z.enum(["고체", "기체", "액체"]);
const cited = { source_id: z.string().min(1), locator: z.string().min(1) };

/** 대응표에서 조합기가 읽는 필드 (`schemas/factor-mapping.schema.json` 의 부분 사본). */
export const mappingSchema = z.object({
  guideline: z.object({ name: z.string().min(1), version: z.string().min(1), url: z.string().regex(/^https?:\/\//) }),
  sectors: z.array(
    z.object({
      key: z.string().min(1),
      label: z.string().min(1),
      ksic_divisions: z.array(z.string().regex(/^\d{2}$/)),
      ch4_condition: z.string().min(1),
      n2o_condition: z.string().min(1),
      ...cited,
    }),
  ),
  sector_overlaps: z.array(
    z.object({
      division: z.string().regex(/^\d{2}$/),
      sectors: z.array(z.string()),
      exceptions: z.array(z.object({ code_prefix: z.string().min(3), sector: z.string().min(1), raw: z.string().min(1) })),
      ...cited,
    }),
  ),
  fuel_names: z.array(
    z.object({
      table: z.string().min(1),
      ipcc_name: z.string().min(1),
      domestic_names: z.array(z.string().min(1)),
      basis,
      note: z.string().nullable(),
      ...cited,
      parameters: z.array(z.enum(["CO2", "CH4", "N2O", "calorific"])).min(1).optional(),
      evidence: z.array(z.object({ source_id: z.string().min(1), locator: z.string().min(1), quote: z.string().min(1) })).optional(),
    }),
  ),
  national_ef_names: z
    .array(z.object({ fuel: z.string().min(1), ef_name: z.string().min(1), basis, note: z.string().nullable(), ...cited }))
    .optional(),
  fuel_states: z.array(z.object({ fuel: z.string().min(1), state, basis, quote: z.string().min(1), note: z.string().nullable(), ...cited })),
  tier_minimums: z.array(
    z.object({ activity: z.string().min(1), fuel_state: state, parameter: z.string().min(1), group: z.enum(["A", "B", "C"]), tier: z.number().int(), ...cited }),
  ),
});

const issues = (error: z.ZodError): string => error.issues.map((issue) => `${issue.path.join(".")} ${issue.message}`).join("; ");

/**
 * 계수 표에서 지침(배출권거래제) 출처의 레코드만 읽는다. 다른 출처의 줄은 JSON 으로만 풀고 버린다.
 * 지침 레코드 한 줄이라도 형식이 다르면 실패한다 (그 줄만 빼고 가지 않는다).
 */
export function loadGuidelineRecords(path: string = defaultFactorsPath(), sourcePrefix = "kr-ets-"): FactorRecord[] {
  return loadRecordsBySourcePrefix(path, [sourcePrefix]);
}

/** 구매 전력 조립이 읽는 레코드: 지침(kr-ets-)과 온실가스종합정보센터 공표(gir-) 출처. */
export function loadElectricityRecords(path: string = defaultFactorsPath()): FactorRecord[] {
  return loadRecordsBySourcePrefix(path, ["kr-ets-", "gir-"]);
}

/** 계수 표에서 출처 id 가 접두 가운데 하나로 시작하는 레코드만 읽는다. 그 레코드 한 줄이라도 형식이 다르면 실패한다. */
export function loadRecordsBySourcePrefix(path: string, sourcePrefixes: readonly string[]): FactorRecord[] {
  const sourcePrefix = sourcePrefixes.join(", ");
  if (!existsSync(path)) throw new AssemblyFileError(`계수 표가 없다: ${path}`);
  const records: FactorRecord[] = [];
  readFileSync(path, "utf8")
    .split("\n")
    .forEach((line, index) => {
      if (line.trim() === "") return;
      let parsed: unknown;
      try {
        parsed = JSON.parse(line);
      } catch {
        throw new AssemblyFileError(`${path} ${index + 1}행이 JSON 이 아니다`);
      }
      const sourceId = (parsed as { source?: { id?: unknown } } | null)?.source?.id;
      if (typeof sourceId !== "string") throw new AssemblyFileError(`${path} ${index + 1}행에 source.id 가 없다`);
      if (!sourcePrefixes.some((prefix) => sourceId.startsWith(prefix))) return;
      const record = factorRecordSchema.safeParse(parsed);
      if (!record.success) throw new AssemblyFileError(`${path} ${index + 1}행: ${issues(record.error)}`);
      records.push(record.data);
    });
  if (records.length === 0) throw new AssemblyFileError(`${path} 에 ${sourcePrefix} 출처의 레코드가 한 건도 없다`);
  return records;
}

export function loadMapping(path: string = defaultMappingPath()): AssemblyMapping {
  if (!existsSync(path)) throw new AssemblyFileError(`대응표가 없다: ${path}`);
  let parsed: unknown;
  try {
    parsed = JSON.parse(readFileSync(path, "utf8"));
  } catch {
    throw new AssemblyFileError(`${path} 가 JSON 이 아니다`);
  }
  const mapping = mappingSchema.safeParse(parsed);
  if (!mapping.success) throw new AssemblyFileError(`${path}: ${issues(mapping.error)}`);
  return mapping.data;
}
