import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { AssemblyMapping, AssemblyRequest, AssemblyResult, FactorRecord } from "../src/index.js";
import { loadGuidelineRecords, loadMapping } from "../src/index.js";

const here = dirname(fileURLToPath(import.meta.url));
export const DATA = join(here, "..", "..", "..", "data");

export const readData = (relative: string): unknown => JSON.parse(readFileSync(join(DATA, relative), "utf8"));

/** 저장소의 실제 계수 표와 대응표 (지침 레코드만). */
export const records: readonly FactorRecord[] = loadGuidelineRecords();
export const mapping: AssemblyMapping = loadMapping();

export const TIER2 = { kind: "explicit", calorific: 2, co2: 2, oxidation: 2 } as const;
export const TIER1 = { kind: "explicit", calorific: 1, co2: 1, oxidation: 1 } as const;
export const GROUP_A = { kind: "group_minimum", group: "A" } as const;

/** 경유, 제조업·건설업, 전부 Tier 2. 필요한 필드만 덮어쓴다. */
export function request(overrides: Partial<AssemblyRequest> = {}): AssemblyRequest {
  return { fuel: "경유", combustionType: "stationary", industry: { sector: "manufacturing_construction" }, tiers: TIER2, ...overrides };
}

export function valueOf(result: AssemblyResult) {
  if (!result.ok) throw new Error(`조립 실패: ${JSON.stringify(result.failure)}`);
  return result.value;
}

export function failureOf(result: AssemblyResult) {
  if (result.ok) throw new Error(`실패해야 하는데 조립됐다: ${result.value.factor.id}`);
  return result.failure;
}

/** 실제 레코드 하나를 베껴 값·조건을 바꾼 가상 레코드 (synthetic). 실제 값으로 쓰면 안 된다. */
export function synthetic(base: FactorRecord, patch: Partial<FactorRecord> & { id: string }): FactorRecord {
  return { ...base, ...patch, synthetic: true };
}

export const byId = (id: string): FactorRecord => {
  const found = records.find((record) => record.id === id);
  if (found === undefined) throw new Error(`레코드가 없다: ${id}`);
  return found;
};
