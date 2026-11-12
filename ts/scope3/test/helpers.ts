import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { FilePromptStore, LlmClient, MemorySink, MockProvider, defaultConfigPaths, loadConfig } from "llm";
import { promptRoot } from "../src/cli.js";
import { FactorStore, InMemoryMappingMemory, type InputRow, type MappingMemory, type PipelineOptions } from "../src/index.js";

export const TAGS = { tenant: "tenant-synthetic", module: "scope3" };

interface SyntheticFactor {
  id: string;
  name: string;
  categories: number[];
  type: "activity" | "distance" | "spend";
  per: string;
  value: string | null;
  license?: "ok" | "disputed" | "unverified";
  gas?: string;
  naics?: string;
  path?: string[];
}

/** 테스트 전용 가상 계수. 값은 계산하기 쉬운 숫자로 지어냈고 실제 배출계수가 아니다. */
export const SYNTHETIC_FACTORS: SyntheticFactor[] = [
  { id: "syn:steel-kg", name: "합성 강판 (synthetic)", categories: [1], type: "activity", per: "kg", value: "2.5", path: ["합성 자재", "금속"] },
  { id: "syn:steel-t", name: "Synthetic steel sheet", categories: [1], type: "activity", per: "tonnes", value: "2400", path: ["Synthetic material", "Metal"] },
  { id: "syn:pp-kg", name: "합성 폴리프로필렌 (synthetic)", categories: [1], type: "activity", per: "kg", value: "1.25", license: "disputed", path: ["합성 자재", "플라스틱"] },
  { id: "syn:truck", name: "Synthetic truck", categories: [4, 9], type: "distance", per: "tonne.km", value: "0.1", path: ["Synthetic freight", "Road"] },
  { id: "syn:rail", name: "Synthetic rail", categories: [6, 7], type: "distance", per: "passenger.km", value: "0.04", path: ["Synthetic travel", "Rail"] },
  { id: "syn:spend-computer", name: "Synthetic Computer Manufacturing", categories: [1, 2], type: "spend", per: "2022 USD, purchaser price", value: "0.5", naics: "334111" },
  { id: "syn:spend-storage", name: "Synthetic Storage Device Manufacturing", categories: [1, 2], type: "spend", per: "2022 USD, purchaser price", value: "0.2", naics: "334112" },
  { id: "syn:spend-legal", name: "Synthetic Legal Services", categories: [1, 2], type: "spend", per: "2022 USD, purchaser price", value: "0.1", naics: "541199" },
  { id: "syn:steel-kg-ch4", name: "합성 강판 (synthetic)", categories: [1], type: "activity", per: "kg", value: "0.01", gas: "CH4" },
  { id: "syn:empty", name: "합성 빈 값 (synthetic)", categories: [1], type: "activity", per: "kg", value: null },
];

function record(factor: SyntheticFactor): Record<string, unknown> {
  return {
    schema_version: "0.1",
    id: factor.id,
    record_type: "emission_factor",
    scope: "scope3",
    scope3_categories: factor.categories,
    factor_type: factor.type,
    name: { original: factor.name, ko: null, en: null },
    category_path: factor.path ?? [],
    classification: factor.naics ? { system: "NAICS 2017", code: factor.naics } : null,
    value: factor.value === null ? null : Number(factor.value),
    value_raw: factor.value,
    value_status: factor.value === null ? "not_available_in_source" : "ok",
    unit: `kg CO2e/${factor.per}`,
    unit_numerator: "kg CO2e",
    unit_denominator: factor.per,
    gas: factor.gas ?? "CO2e",
    region: "GLOBAL",
    spend: factor.type === "spend" ? { currency: "USD", price_year: 2022, price_basis: "purchaser" } : null,
    source: { id: "synthetic" },
    license_status: factor.license ?? "ok",
    synthetic: true,
  };
}

export function writeSyntheticFactors(factors: readonly SyntheticFactor[] = SYNTHETIC_FACTORS): string {
  const path = join(mkdtempSync(join(tmpdir(), "scope3-factors-")), "factors.synthetic.jsonl");
  writeFileSync(path, `${factors.map((factor) => JSON.stringify(record(factor))).join("\n")}\n`);
  return path;
}

export function row(over: Partial<InputRow> & { itemName: string }): InputRow {
  return { rowId: "r1", quantity: "10", unit: "kg", amount: "100000", currency: "KRW", date: "2026-01-05", orgUnit: "본사", transport: null, ...over };
}

export function classification(category: number | "other", keywords: string[] = []): string {
  return JSON.stringify({ category, keywords_en: keywords, confidence: "medium", reason: "테스트 응답 (synthetic)" });
}

export function recommendation(ids: string[]): string {
  return JSON.stringify({ picks: ids.map((id) => ({ factor_id: id, reason: "테스트 응답 (synthetic)", confidence: "medium" })), none_suitable: ids.length === 0 });
}

/** 모의 공급사 하나와 가상 계수 표로 파이프라인 옵션을 만든다. 심어 둔 응답이 없으면 모의 공급사는 오류를 돌려준다. */
export function harness(options: { memory?: MappingMemory; store?: FactorStore } & Partial<Pick<PipelineOptions, "excludeLicenseStatuses" | "adjustments" | "shortlistSize">> = {}) {
  const mock = new MockProvider();
  const log = new MemorySink();
  const client = new LlmClient({
    config: loadConfig(defaultConfigPaths({ synthetic: true })),
    providers: { mock },
    prompts: new FilePromptStore(promptRoot()),
    log,
  });
  const { memory, store, ...rest } = options;
  const pipeline: PipelineOptions = {
    client,
    store: store ?? FactorStore.load(writeSyntheticFactors()),
    memory: memory ?? new InMemoryMappingMemory(),
    route: "mock",
    tags: TAGS,
    ...rest,
  };
  return { mock, log, client, pipeline };
}

/** 모의 공급사가 받은 요청의 사용자 글자. */
export function userTexts(mock: MockProvider): string[] {
  return mock.calls.map((call) => call.user.map((part) => (part.type === "text" ? part.text : "")).join("\n"));
}
