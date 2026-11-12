import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { isDecimalString } from "kernel";
import { z } from "zod";
import type { CandidateQuery, Factor, LicenseStatus } from "../domain/factor.js";
import type { FactorCatalog } from "../domain/ports.js";
import { unitDim } from "../domain/units.js";

/** 계수 표를 읽지 못했거나 형식이 다르다. 빈 후보와 구분하려고 예외로 둔다. */
export class FactorStoreError extends Error {
  override name = "FactorStoreError";
}

const here = dirname(fileURLToPath(import.meta.url));

export function defaultFactorsPath(): string {
  return join(here, "..", "..", "..", "..", "data", "factors", "factors.jsonl");
}

/**
 * 계수 레코드(`schemas/factor.schema.json` 의 한 줄)에서 이 어댑터가 읽는 필드. 형식의 정본은 그 스키마이고 이것은 손으로 베낀 부분 사본이다.
 * 읽을 때 이 스키마로 검증하지는 않는다 (`load` 는 아래 검사만 한다). 계약 테스트(`test/factor-contract.test.ts`)가 정본과 대조한다.
 */
export const factorRecordSchema = z.object({
  id: z.string(),
  record_type: z.string(),
  scope3_categories: z.array(z.number()),
  factor_type: z.enum(["activity", "distance", "spend"]),
  name: z.object({ original: z.string() }),
  category_path: z.array(z.string()),
  classification: z.object({ system: z.string(), code: z.string() }).nullable(),
  value: z.number().nullable(),
  value_raw: z.string().nullable(),
  unit: z.string(),
  unit_numerator: z.string(),
  unit_denominator: z.string(),
  gas: z.string(),
  region: z.string(),
  spend: z.object({ currency: z.string(), price_year: z.number(), price_basis: z.string() }).nullable(),
  source: z.object({ id: z.string() }),
  license_status: z.enum(["ok", "disputed", "unverified"]),
  synthetic: z.boolean(),
});

type RawRecord = z.infer<typeof factorRecordSchema>;

/**
 * 밴드 C 의 `data/factors/factors.jsonl` 을 직접 읽는다.
 * 후보는 이미 CO2 환산된 배출계수뿐이다: `record_type == emission_factor`, `gas == CO2e`, 값이 있고, Scope 3 카테고리 후보가 붙은 것.
 * 기체별 행, 값 없는 행, 발열량·에너지 환산 레코드는 id 로는 알지만 후보로 내놓지 않는다.
 * `FactorCatalog` 포트의 jsonl 구현이다.
 */
export class FactorStore implements FactorCatalog {
  private readonly candidatesById = new Map<string, Factor>();
  private readonly allIds = new Set<string>();
  private readonly ordered: Factor[] = [];

  private constructor(
    readonly path: string,
    /** 읽은 계수 표 파일의 sha256. 임베딩 색인이 이 표로 만든 것인지 대조한다. */
    readonly sha256: string,
  ) {}

  static load(path: string = defaultFactorsPath()): FactorStore {
    if (!existsSync(path)) throw new FactorStoreError(`계수 표가 없다: ${path}`);
    const file = readFileSync(path);
    const store = new FactorStore(path, createHash("sha256").update(file).digest("hex"));
    const lines = file.toString("utf8").split("\n");
    lines.forEach((line, index) => {
      if (line.trim() === "") return;
      let record: RawRecord;
      try {
        record = JSON.parse(line) as RawRecord;
      } catch {
        throw new FactorStoreError(`${path} ${index + 1}행이 JSON 이 아니다`);
      }
      if (typeof record.id !== "string" || !Array.isArray(record.scope3_categories)) {
        throw new FactorStoreError(`${path} ${index + 1}행에 id 또는 scope3_categories 가 없다`);
      }
      if (store.allIds.has(record.id)) throw new FactorStoreError(`${path} ${index + 1}행: id 가 겹친다 (${record.id})`);
      store.allIds.add(record.id);
      if (record.record_type !== "emission_factor" || record.gas !== "CO2e") return;
      if (record.value === null || record.scope3_categories.length === 0) return;
      if (!isDecimalString(record.value_raw)) {
        throw new FactorStoreError(`${path} ${index + 1}행: value_raw 가 십진 표기가 아니다 (${String(record.value_raw)})`);
      }
      const factor: Factor = {
        id: record.id,
        name: record.name.original,
        categoryPath: record.category_path,
        factorType: record.factor_type,
        scope3Categories: record.scope3_categories,
        unit: record.unit,
        unitNumerator: record.unit_numerator,
        unitDenominator: record.unit_denominator,
        denominatorDim: unitDim(record.unit_denominator),
        valueRaw: record.value_raw,
        licenseStatus: record.license_status,
        sourceId: record.source.id,
        region: record.region,
        classification: record.classification,
        spend: record.spend
          ? { currency: record.spend.currency, priceYear: record.spend.price_year, priceBasis: record.spend.price_basis }
          : null,
      };
      store.candidatesById.set(factor.id, factor);
      store.ordered.push(factor);
    });
    if (store.ordered.length === 0) throw new FactorStoreError(`${path} 에 후보가 될 계수가 한 건도 없다`);
    return store;
  }

  /** 표의 전체 레코드 수 (후보가 아닌 것 포함). */
  get recordCount(): number {
    return this.allIds.size;
  }

  get candidateCount(): number {
    return this.ordered.length;
  }

  /** 후보가 될 수 있는 계수 전부 (표의 순서). 임베딩 색인을 만들 때 쓴다. */
  all(): readonly Factor[] {
    return this.ordered;
  }

  /** 표에 그 id 의 레코드가 있는지 (후보가 아닌 레코드 포함). */
  has(id: string): boolean {
    return this.allIds.has(id);
  }

  /** 후보가 될 수 있는 계수만 돌려준다. 기체별 행 같은 비후보 id 와 없는 id 는 undefined. */
  get(id: string): Factor | undefined {
    return this.candidatesById.get(id);
  }

  /** 조건에 맞는 후보. 빈 배열은 「조건에 맞는 계수가 없다」는 뜻이고 읽기 실패는 `load` 가 예외로 알린다. */
  candidates(query: CandidateQuery): Factor[] {
    const excluded = new Set<LicenseStatus>(query.excludeLicenseStatuses ?? []);
    return this.ordered.filter(
      (factor) =>
        factor.scope3Categories.includes(query.category) &&
        (query.basis === "spend" ? factor.factorType === "spend" : factor.factorType !== "spend") &&
        !excluded.has(factor.licenseStatus),
    );
  }
}

const cache = new Map<string, FactorStore>();

/** 같은 경로는 한 번만 읽는다 (17.6 MB). */
export function loadFactorStore(path: string = defaultFactorsPath()): FactorStore {
  let store = cache.get(path);
  if (!store) {
    store = FactorStore.load(path);
    cache.set(path, store);
  }
  return store;
}
