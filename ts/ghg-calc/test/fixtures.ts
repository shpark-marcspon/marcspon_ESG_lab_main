import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { mergeTables, validateTables } from "../src/index.js";
import type { ActivityRow, CombustionFactor, ParameterTiers, ReferenceTables } from "../src/index.js";

const DATA_DIR = new URL("../../../data/ghg-calc/", import.meta.url);

export function readData(name: string): unknown {
  return JSON.parse(readFileSync(fileURLToPath(new URL(name, DATA_DIR)), "utf8"));
}

export interface HandCase {
  id: string;
  title: string;
  synthetic: boolean;
  gwpSetId: string;
  activity: ActivityRow;
  factor: { id: string } & Record<string, unknown>;
  gwp: Record<string, string>;
  expected: {
    factorId: string;
    energy: { value: string; unit: string } | null;
    gases: Record<string, { mass: string; co2e: string }>;
    co2e: string;
  };
  working: string[];
}

export const handCases = (readData("handcalc-cases.json") as { cases: HandCase[] }).cases;

const synthetic = readData("examples/factors.synthetic.json") as Partial<ReferenceTables>;

/** 출처 있는 표(환산, GWP, 표본 계수)에 synthetic 계수와 synthetic GWP 세트를 얹은 표. */
export function loadTables(): ReferenceTables {
  const merged = mergeTables(
    readData("unit-conversions.json") as Partial<ReferenceTables>,
    readData("gwp-sets.json") as Partial<ReferenceTables>,
    readData("factors.kr-ets-sample.json") as Partial<ReferenceTables>,
    { factors: synthetic.factors ?? [], gwpSets: synthetic.gwpSets ?? [] },
  );
  const validated = validateTables(merged);
  if (!validated.ok) throw new Error(`fixture tables invalid: ${JSON.stringify(validated.problems)}`);
  return validated.tables;
}

export const ETS_GWP = "kr-ets-decree-annex2";
export const AR5_GWP = "ipcc-ar5-100y-kr-nid-2025";
export const SYNTHETIC_GWP = "synthetic-gwp";
export const DIESEL_ID = "kr-ets-sample:diesel:stationary:manufacturing:tier2";
/** 표본 계수의 조합: 발열량·CO2 계수·산화계수 Tier 2, CH4·N2O 는 Tier 1 (지침에 Tier 1 뿐이다). */
export const TIER2: ParameterTiers = { calorific: 2, co2: 2, nonCo2: 1, oxidation: 2 };
export const MANUFACTURING = "manufacturing_construction";
export const COMMERCIAL = "commercial_public";

export function dieselFactor(tables: ReferenceTables): CombustionFactor {
  const factor = tables.factors.find((f) => f.id === DIESEL_ID);
  if (factor === undefined || factor.kind !== "combustion") throw new Error("diesel fixture missing");
  return factor;
}

type RowFields = Omit<ActivityRow, "id" | "orgUnit" | "period"> & Partial<Pick<ActivityRow, "id" | "orgUnit" | "period">>;

/** 연료·구분이 없는 맨 행. 구매 전력·열이나 다른 연료에 쓴다. */
export function bare(fields: RowFields): ActivityRow {
  return { id: "t1", orgUnit: "synthetic-site", period: "2025", ...fields };
}

/** 경유 고정연소 1000 L (제조업·건설업, Tier 2 조합). 필요한 필드만 덮어쓴다. */
export function row(overrides: Partial<ActivityRow> = {}): ActivityRow {
  return bare({
    sourceType: "stationary_combustion",
    fuel: "diesel",
    sector: MANUFACTURING,
    parameterTiers: TIER2,
    amount: "1000",
    unit: "L",
    ...overrides,
  });
}
