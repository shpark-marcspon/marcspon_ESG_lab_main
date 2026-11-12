import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { CallMeta, CallRequest, LlmResult } from "llm";
import { describe, expect, it } from "vitest";
import {
  InMemoryMappingMemory,
  VectorShortlister,
  calculateFor,
  decideGroup,
  groupRows,
  runPipeline,
  type Factor,
  type FactorCatalog,
  type FactorVectorIndex,
  type LlmGateway,
  type LlmUsage,
  type QueryEmbedder,
} from "../src/index.js";
import { TAGS, row } from "./helpers.js";

// 포트 대역. 파일도 `llm` 의 클라이언트도 쓰지 않고, 포트 모양의 값만으로 도메인 코드가 도는지 본다.
// 계수 값은 계산하기 쉽게 지어낸 가상 값이다 (synthetic).

function factor(over: Partial<Factor> & { id: string; name: string }): Factor {
  return {
    categoryPath: [],
    factorType: "activity",
    scope3Categories: [1],
    unit: "kg CO2e/kg",
    unitNumerator: "kg CO2e",
    unitDenominator: "kg",
    denominatorDim: "kg",
    valueRaw: "2.5",
    licenseStatus: "ok",
    sourceId: "synthetic",
    region: "GLOBAL",
    classification: null,
    spend: null,
    ...over,
  };
}

const FACTORS: Factor[] = [
  factor({ id: "fake:steel", name: "가짜 강판 (synthetic)" }),
  factor({ id: "fake:paper", name: "가짜 종이 (synthetic)", valueRaw: "1.2" }),
  factor({ id: "fake:plastic", name: "가짜 플라스틱 (synthetic)", valueRaw: "3" }),
];

/** 배열 하나로 만든 가짜 카탈로그. `FactorStore` 가 아니다. */
function fakeCatalog(factors: readonly Factor[]): FactorCatalog {
  return {
    get: (id) => factors.find((entry) => entry.id === id),
    candidates: (query) =>
      factors.filter(
        (entry) =>
          entry.scope3Categories.includes(query.category) &&
          (query.basis === "spend" ? entry.factorType === "spend" : entry.factorType !== "spend") &&
          !(query.excludeLicenseStatuses ?? []).includes(entry.licenseStatus),
      ),
  };
}

const META: CallMeta = { callId: "fake-call", promptId: "fake", attempts: [], totalCostUsd: 0, warnings: [] };

/** 스키마 이름만 보고 정해 둔 답을 돌려주는 가짜 호출 포트. `LlmClient` 가 아니다. */
function fakeGateway(pick: string): { gateway: LlmGateway; seen: string[] } {
  const seen: string[] = [];
  const gateway: LlmGateway = {
    async call<T = string>(request: CallRequest<T>): Promise<LlmResult<T>> {
      seen.push(request.schemaName ?? "");
      const answer =
        request.schemaName === "scope3_classification"
          ? { category: 1, keywords_en: ["steel"], confidence: "high", reason: "대역 응답 (synthetic)" }
          : { picks: [{ factor_id: pick, reason: "대역 응답 (synthetic)", confidence: "high" }], none_suitable: false };
      if (!request.schema) throw new Error("대역: 스키마 없는 호출은 다루지 않는다");
      return { kind: "success", data: request.schema.parse(answer), text: JSON.stringify(answer), meta: META };
    },
  };
  return { gateway, seen };
}

const emptyUsage = (): LlmUsage => ({ calls: 0, attempts: 0, costUsd: 0, byPrompt: {} });

describe("포트 대역으로 도는 도메인 코드", () => {
  it("decideGroup 은 가짜 FactorCatalog 와 가짜 LlmGateway 로 추천까지 간다", async () => {
    const { gateway, seen } = fakeGateway("fake:paper");
    const store = fakeCatalog(FACTORS);
    const options = { client: gateway, store, memory: new InMemoryMappingMemory(), route: "any", tags: TAGS };
    const input = row({ itemName: "복사 용지" });
    const [group] = groupRows([input]);
    const usage = emptyUsage();
    const decision = await decideGroup(options, group!, usage);
    expect(decision).toMatchObject({ status: "recommended", source: "llm", category: 1, picks: [{ factorId: "fake:paper" }], candidates: { total: 3, shown: 3, method: "lexical-v2" } });
    expect(seen).toEqual(["scope3_classification", "scope3_recommendation"]);
    expect(usage.calls).toBe(2);
    expect(calculateFor({ store }, input, decision)).toMatchObject({ status: "computed", emissionsKgCo2e: "12", factorId: "fake:paper" });
  });

  it("기억에 있으면 호출 포트를 부르지 않는다", async () => {
    const { gateway, seen } = fakeGateway("fake:steel");
    const input = row({ itemName: "강판" });
    const [group] = groupRows([input]);
    const memory = new InMemoryMappingMemory([{ key: group!.key, category: 1, factorId: "fake:steel", confirmedBy: "tester", confirmedAt: "2026-10-02T00:00:00+09:00" }]);
    const result = await runPipeline([input], { client: gateway, store: fakeCatalog(FACTORS), memory, route: "any", tags: TAGS });
    expect(result.rows[0]).toMatchObject({ decision: { status: "recommended", source: "memory" }, calc: { status: "computed", emissionsKgCo2e: "25" } });
    expect(seen).toEqual([]);
  });

  it("VectorShortlister 는 가짜 QueryEmbedder 와 가짜 FactorVectorIndex 로 돈다", async () => {
    const vectors: Record<string, number[]> = { "fake:steel": [1, 0], "fake:paper": [0, 1], "fake:plastic": [0.6, 0.8] };
    const index: FactorVectorIndex = { header: { modelKey: "fake-model", dimensions: 2, factorsSha256: "-", textVersion: 1 }, vector: (id) => vectors[id] };
    const asked: string[][] = [];
    const embedder: QueryEmbedder = {
      model: { key: "fake-model", dimensions: 2 },
      embed: async (texts) => {
        asked.push([...texts]);
        return { ok: true, vectors: texts.map(() => [0, 1]), costUsd: 0.25 };
      },
    };
    const shortlister = new VectorShortlister({ embedder, index, queryMode: "item" });
    const query = { texts: ["복사 용지"], keywords: [] };
    const first = await shortlister.shortlist(FACTORS, query, 2);
    expect(first).toMatchObject({ ok: true, method: "embedding", total: 3, lexicalMatches: null, embeddingCostUsd: 0.25 });
    expect(first.ok && first.factors.map((entry) => entry.id)).toEqual(["fake:paper", "fake:plastic"]);
    // 같은 글은 다시 임베딩하지 않는다.
    expect(await shortlister.shortlist(FACTORS, query, 2)).toMatchObject({ ok: true, embeddingCostUsd: 0 });
    expect(asked).toEqual([["복사 용지"]]);

    const hybrid = new VectorShortlister({ embedder, index, queryMode: "item", fuseWith: { bigrams: true } });
    expect(await hybrid.shortlist(FACTORS, { texts: ["강판"], keywords: [] }, 3)).toMatchObject({ ok: true, method: "hybrid", lexicalMatches: 1 });
  });

  it("질의 임베딩이 실패하거나 색인에 벡터가 없으면 포트의 실패 종류로 남는다", async () => {
    const index: FactorVectorIndex = { header: { modelKey: "fake-model", dimensions: 2, factorsSha256: "-", textVersion: 1 }, vector: () => undefined };
    const failing: QueryEmbedder = { model: { key: "fake-model", dimensions: 2 }, embed: async () => ({ ok: false, failure: { kind: "timeout", detail: "대역 시간 초과" }, costUsd: 0 }) };
    const query = { texts: ["강판"], keywords: [] };
    expect(await new VectorShortlister({ embedder: failing, index, queryMode: "item" }).shortlist(FACTORS, query, 2)).toEqual({ ok: false, method: "embedding", failure: { kind: "timeout", detail: "대역 시간 초과" }, embeddingCostUsd: 0 });
    const working: QueryEmbedder = { model: failing.model, embed: async (texts) => ({ ok: true, vectors: texts.map(() => [1, 0]), costUsd: 0 }) };
    expect(await new VectorShortlister({ embedder: working, index, queryMode: "item" }).shortlist(FACTORS, query, 2)).toMatchObject({ ok: false, failure: { kind: "index_missing_factor" } });
    expect(() => new VectorShortlister({ embedder: { ...working, model: { key: "other-model", dimensions: 2 } }, index, queryMode: "item" })).toThrow("색인의 모델");
  });
});

describe("폴더 경계", () => {
  const src = join(dirname(fileURLToPath(import.meta.url)), "..", "src");
  const imports = (folder: string) =>
    readdirSync(join(src, folder))
      .filter((file) => file.endsWith(".ts"))
      .flatMap((file) => [...readFileSync(join(src, folder, file), "utf8").matchAll(/from "([^"]+)"/g)].map((match) => ({ file: `${folder}/${file}`, from: match[1] as string })));

  it("domain 은 파일·네트워크 모듈과 adapters·eval 을 import 하지 않는다", () => {
    const found = imports("domain");
    expect(found.length).toBeGreaterThan(10);
    expect(found.filter((entry) => entry.from.startsWith("node:") || entry.from.includes("adapters/") || entry.from.includes("eval/") || entry.from.startsWith("../"))).toEqual([]);
  });

  it("eval 은 adapters 를 import 하지 않는다 (계수 표와 기억은 포트로 받는다)", () => {
    expect(imports("eval").filter((entry) => entry.from.includes("adapters/"))).toEqual([]);
  });

  it("adapters 는 domain 에만 기댄다 (eval 과 조립 지점을 import 하지 않는다)", () => {
    expect(imports("adapters").filter((entry) => entry.from.startsWith("../") && !entry.from.startsWith("../domain/"))).toEqual([]);
  });
});
