import { describe, expect, it } from "vitest";
import { calculateRow, validateTables, type CombustionFactor, type ReferenceTables } from "ghg-calc";
import { assembleCombustionFactor, replayHandCases, replaySummary, type HandCase, type ReplaySpec, type ReplayTables } from "../src/index.js";
import { mapping, readData, records, valueOf } from "./helpers.js";

// 끝의 증거: 엔진의 검산 사례가 손으로 묶은 표본이 아니라 계수 표에서 조립한 계수로 다시 통과하는가.
// 기대값은 data/ghg-calc/handcalc-cases.json 의 것을 그대로 읽는다 (이 테스트는 기대값을 갖지 않는다).

const cases = (readData("ghg-calc/handcalc-cases.json") as { cases: HandCase[] }).cases;
const spec = readData("ghg-calc/handcalc-assembly.json") as ReplaySpec;
const sample = readData("ghg-calc/factors.kr-ets-sample.json") as { factors: Array<CombustionFactor | { kind: "purchased_energy"; id: string }> };

function loadTables(): ReplayTables {
  const validated = validateTables({
    unitConversions: (readData("ghg-calc/unit-conversions.json") as { unitConversions: unknown[] }).unitConversions,
    gwpSets: (readData("ghg-calc/gwp-sets.json") as { gwpSets: unknown[] }).gwpSets,
    factors: [],
  });
  if (!validated.ok) throw new Error(JSON.stringify(validated.problems));
  return validated.tables;
}
const tables = loadTables();
const rows = replayHandCases(cases, spec, records, mapping, tables);

describe("검산 재통과 (계수 표에서 조립 → 엔진 → 기대값과 문자열 일치)", () => {
  it("한국 지침 계수를 쓰는 사례 9건이 전부 돈다 (연소 7건, 구매 전력 2건은 별표 6 표-69 에서 조립)", () => {
    const real = cases.filter((c) => !c.synthetic);
    expect(real.map((c) => c.id)).toEqual(["H01", "H02", "H03", "H04", "H05", "H06", "H07", "H12", "H13"]);
    const byStatus = (status: string) => rows.filter((r) => r.outcome.status === status).map((r) => r.caseId);
    expect(byStatus("match")).toEqual(["H01", "H02", "H03", "H04", "H05", "H06", "H07", "H12", "H13", "N01"]);
    expect(byStatus("not_replayed")).toEqual([]);
    expect(replaySummary(rows)).toEqual({ total: 10, matched: 10, notReplayed: 0, failed: 0 });
  });

  it("구매 전력 사례(H06·H07)는 표본 파일이 아니라 계수 표의 표-69 낱줄 셋으로 조립한 계수로 계산된다", () => {
    for (const id of ["H06", "H07"]) {
      expect(rows.find((r) => r.caseId === id)?.outcome).toEqual({ status: "match", assembledFactorId: "electricity:kr-ets-byl06:표-69:2014-2016평균:per_gas", assumptions: [] });
    }
    expect(rows.find((r) => r.caseId === "H06")?.actualCo2e).toBe("567.1731662102");
    expect(rows.find((r) => r.caseId === "H07")?.actualCo2e).toBe("229.52665");
  });

  it.each(rows.filter((r) => r.outcome.status !== "not_replayed").map((r) => [r.caseId, r.title, r] as const))("%s %s", (_id, _title, row) => {
    expect(row.outcome).toMatchObject({ status: "match" });
    expect(row.actualCo2e).toBe(row.expectedCo2e);
  });

  it("도시가스 사례는 추정 대응을 탔다는 것이 결과에 남는다", () => {
    const h04 = rows.find((r) => r.caseId === "H04");
    expect(h04?.outcome).toMatchObject({ status: "match", assumptions: ["fuel_state: 도시가스(LNG) → 기체", "fuel_name: 도시가스(LNG) → 천연가스 (kr-ets-byl10)"] });
    expect(rows.find((r) => r.caseId === "H01")?.outcome).toMatchObject({ status: "match", assumptions: [] });
  });

  it("A 그룹 최소 조합(경유 제조업 1000 L)은 2.6170848 tCO2eq 이고 Tier 2 조합보다 크다", () => {
    const n01 = rows.find((r) => r.caseId === "N01");
    expect(n01?.actualCo2e).toBe("2.6170848");
    expect(rows.find((r) => r.caseId === "H01")?.actualCo2e).toBe("2.5596384");
  });

  it("조립된 계수의 근거가 엔진 결과의 근거까지 이어진다 (매개변수별 산정등급, 부문, 출처의 추정 표시)", () => {
    const assembled = valueOf(assembleCombustionFactor(spec.assemblyCases[0]!.request, records, mapping));
    const result = calculateRow(spec.assemblyCases[0]!.activity, { ...tables, factors: [assembled.factor] }, { gwpSetId: "kr-ets-decree-annex2" });
    if (result.status !== "ok") throw new Error(JSON.stringify(result.failure));
    expect(result.evidence.parameterTiers).toEqual({ calorific: 2, co2: 1, nonCo2: 1, oxidation: 1 });
    expect(result.evidence.sector).toBe("manufacturing_construction");
    expect(result.evidence.oxidationFactor).toBe("1");
    expect(result.evidence.factor.source).toEqual(assembled.factor.source);
  });

  it("조립 조건이 사례의 부문·등급과 다르면 통과로 치지 않는다", () => {
    const wrongSector: ReplaySpec = {
      ...spec,
      assemblyCases: [],
      electricityReplays: [],
      replays: spec.replays.map((r) => ({ ...r, request: { ...r.request, industry: { sector: "residential_other" } } })),
    };
    const outcome = replayHandCases(cases, wrongSector, records, mapping, tables);
    expect(replaySummary(outcome)).toMatchObject({ matched: 0, failed: 7 });
    expect(outcome[0]?.outcome).toMatchObject({ status: "calculation_failed", failure: { kind: "factor_not_found" } });
    const noSpec = replayHandCases(cases, { replays: [], notReplayed: [], assemblyCases: [] }, records, mapping, tables);
    expect(replaySummary(noSpec)).toEqual({ total: 9, matched: 0, notReplayed: 9, failed: 0 });
  });

  it("계수 값이 한 글자라도 다르면 어긋남으로 나온다", () => {
    const id = "kr-ets-byl12:ef:p3r3:CO2";
    const altered = records.map((r) => (r.id === id ? { ...r, value_raw: "73,201", synthetic: true } : r));
    const outcome = replayHandCases(cases, spec, altered, mapping, tables);
    expect(outcome.find((r) => r.caseId === "H01")?.outcome.status).toBe("mismatch");
    expect(outcome.find((r) => r.caseId === "H04")?.outcome.status).toBe("match");
  });
});

describe("표본 계수와 조립 결과의 필드 대조", () => {
  const VOLATILE = ["id", "version", "source", "fuel"] as const;

  it.each(spec.replays.map((r) => [r.sampleFactorId, r] as const))("%s", (sampleId, replay) => {
    const hand = sample.factors.find((f) => f.id === sampleId) as CombustionFactor;
    const { factor } = valueOf(assembleCombustionFactor(replay.request, records, mapping));
    const strip = (f: CombustionFactor) => Object.fromEntries(Object.entries(f).filter(([key]) => !(VOLATILE as readonly string[]).includes(key)));
    // 계산에 들어가는 필드(연소 유형, 부문, 등급, 발열량, 기체별 계수, 산화계수)는 글자까지 같다.
    expect(strip(factor)).toEqual(strip(hand));
    // 다른 것은 이름표뿐이다: id, 고시 판 표기, 출처 서술, 연료 이름(표본은 영문 별칭).
    expect(factor.id).not.toBe(hand.id);
    expect(factor.version).toBe("환경부고시 제2025-64호 (2025-04-11 시행)");
    expect(hand.version).toBe("환경부고시 제2025-64호");
    expect([hand.fuel, factor.fuel]).toEqual(sampleId.includes("diesel") ? ["diesel", "경유"] : ["city-gas-lng", "도시가스(LNG)"]);
  });

  it("조립된 계수는 엔진의 표 검사를 통과한다", () => {
    const factors = spec.replays.map((r) => valueOf(assembleCombustionFactor(r.request, records, mapping)).factor);
    const checked = validateTables({ ...tables, factors } satisfies ReferenceTables);
    expect(checked.ok).toBe(true);
  });
});
