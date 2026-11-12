import { describe, expect, it } from "vitest";
import { headerKey, runReconcile, type Finding } from "../src/index.js";
import { extract, harness, oracle, runOptions, syntheticDocument, type Meaning } from "./helpers.js";

// 심은 오류 시험. 가상(synthetic) 문서 한 쌍: A 는 원천자료, B 는 보고서. 숫자는 전부 지어낸 것이다.
// 원천 쪽 숫자 몇 개를 일부러 바꿨다. 행마다 무엇을 심었는지 적는다.
const HEADER = ["구분", "단위", "2023", "2024"];
const report = extract(
  syntheticDocument("report", [
    {
      id: "p1-t1",
      cells: [
        HEADER,
        ["Scope 1", "tCO2eq", "1,200", "1,350"],
        ["Scope 2", "tCO2eq", "5,000", "5,400"],
        ["총 에너지", "TJ", "45.3", "47.1"],
        ["국내외 비재생에너지", "TJ", "44.0", "44.9"],
        ["용수", "톤", "12,000", "12,500"],
        ["폐기물", "톤", "310", "290"],
        ["매출", "억 원", "5,120", "5,300"],
        ["임직원", "명", "1,024", "1,100"],
        ["구매", "tCO2eq", "96", "120"],
        ["재생에너지", "TJ", "3.2", "3.4"],
      ],
    },
  ]),
  "b",
);
const source = extract(
  syntheticDocument("source", [
    {
      id: "p1-t1",
      cells: [
        HEADER,
        ["Scope 1", "tCO2eq", "1,200", "1,530"], // 2024: 자릿수 오타 (1,350 → 1,530)
        ["Scope 2", "tCO2eq", "5,000", "5,400"], // 오류 없음
        ["총 에너지", "TJ", "45.32", "47.08"], // 오류 없음 (원천이 한 자리 더 자세하다)
        ["국내 비재생에너지", "TJ", "40.1", "40.6"], // 범위가 다른 짝 (국내 대 국내+해외)
        ["용수", "천 톤", "12", "12.5"], // 오류 없음 (단위만 다르다)
        ["폐기물", "kg", "310,000", "290,000"], // 오류 없음 (단위만 다르다)
        ["매출", "백만 원", "5,120", "5,300"], // 단위 착오 (억 원이어야 한다)
        ["임직원", "명", "96", "120"], // 행 밀림: 아래 행과 값이 바뀌었다
        ["구매", "tCO2eq", "1,024", "1,100"], // 행 밀림
        ["출장", "tCO2eq", "1,200", "80"], // 원천에만 있다. 2023 값은 우연히 보고서의 Scope 1 과 같다
      ],
    },
  ]),
  "a",
);

const TRUE_MEANINGS: Record<string, Meaning> = {
  "Scope 1": { key: "ghg.scope1" },
  "Scope 2": { key: "ghg.scope2.location" },
  "총 에너지": { key: "energy.total" },
  "국내 비재생에너지": { key: "energy.nonrenewable", boundary: "domestic" },
  "국내외 비재생에너지": { key: "energy.nonrenewable", boundary: "domestic_overseas" },
  용수: { key: "water.use" },
  폐기물: { key: "waste.generated" },
  매출: { key: "revenue" },
  임직원: { key: "employees.total" },
  구매: { key: "ghg.scope3.cat1" },
  재생에너지: { key: "energy.renewable" },
  출장: { key: "ghg.scope3.cat6" },
};
const keyed = (meanings: Record<string, Meaning>) => Object.fromEntries(Object.entries(meanings).map(([label, meaning]) => [headerKey(label), meaning]));

async function run(sourceMeanings: Record<string, Meaning>) {
  const { client, mock } = harness();
  const reply = oracle([source, report], { a: keyed(sourceMeanings), b: keyed(TRUE_MEANINGS) });
  mock.enqueue(reply).enqueue(reply);
  return runReconcile(runOptions(source, report, client));
}

const row = (finding: Finding): string => `${finding.key?.indicator} ${finding.key?.period}: ${finding.outcome}/${finding.reason} (${finding.a?.raw ?? "-"} 대 ${finding.b?.raw ?? "-"})`;

describe("심은 오류 시험: 뜻을 정확히 붙이는 응답기", () => {
  it("심은 오류는 전부 불일치·짝 없음·설명 필요로 잡히고, 오류 없는 짝은 일치다", async () => {
    const result = await run(TRUE_MEANINGS);
    expect(result.findings.map(row).sort()).toEqual(
      [
        "ghg.scope1 2023: match/exact (1,200 대 1,200)",
        "ghg.scope1 2024: mismatch/values_differ (1,530 대 1,350)", // 자릿수 오타
        "ghg.scope2.location 2023: match/exact (5,000 대 5,000)",
        "ghg.scope2.location 2024: match/exact (5,400 대 5,400)",
        "energy.total 2023: match/rounded_half_up (45.32 대 45.3)",
        "energy.total 2024: match/rounded_half_up (47.08 대 47.1)",
        "energy.nonrenewable 2023: needs_explanation/boundary_differs (40.1 대 44.0)", // 범위가 다른 짝
        "energy.nonrenewable 2024: needs_explanation/boundary_differs (40.6 대 44.9)",
        "water.use 2023: match/exact (12 대 12,000)",
        "water.use 2024: match/exact (12.5 대 12,500)",
        "waste.generated 2023: match/exact (310,000 대 310)",
        "waste.generated 2024: match/exact (290,000 대 290)",
        "revenue 2023: mismatch/values_differ (5,120 대 5,120)", // 단위 착오
        "revenue 2024: mismatch/values_differ (5,300 대 5,300)",
        "employees.total 2023: mismatch/values_differ (96 대 1,024)", // 행 밀림
        "employees.total 2024: mismatch/values_differ (120 대 1,100)",
        "ghg.scope3.cat1 2023: mismatch/values_differ (1,024 대 96)",
        "ghg.scope3.cat1 2024: mismatch/values_differ (1,100 대 120)",
        "ghg.scope3.cat6 2023: unpaired/no_counterpart (1,200 대 -)", // 원천에만 있다
        "ghg.scope3.cat6 2024: unpaired/no_counterpart (80 대 -)",
        "energy.renewable 2023: unpaired/no_counterpart (- 대 3.2)", // 보고서에만 있다
        "energy.renewable 2024: unpaired/no_counterpart (- 대 3.4)",
      ].sort(),
    );
    expect(result.summary.a.sentence).toBe("숫자 20개 중 18개 대조 (일치 9, 불일치 7, 설명 필요 2), 짝 없음 2개, 미확인 0개");
    expect(result.summary.b.sentence).toBe("숫자 20개 중 18개 대조 (일치 9, 불일치 7, 설명 필요 2), 짝 없음 2개, 미확인 0개");
  });

  it("값이 우연히 같아도 뜻이 다르면 짝이 되지 않는다 (원천의 출장 1,200 과 보고서의 Scope 1 1,200)", async () => {
    const result = await run(TRUE_MEANINGS);
    const travel = result.findings.find((finding) => finding.a?.raw === "1,200" && finding.key?.indicator === "ghg.scope3.cat6");
    expect(travel).toMatchObject({ outcome: "unpaired", b: null });
  });

  it("단위 착오에는 「숫자열은 같고 자리가 다르다」는 실마리가 붙는다", async () => {
    const result = await run(TRUE_MEANINGS);
    const revenue = result.findings.filter((finding) => finding.key?.indicator === "revenue");
    expect(revenue.every((finding) => finding.comparison?.notes.includes("same_digits_different_scale"))).toBe(true);
  });
});

describe("심은 오류 시험: 뜻을 일부러 틀리게 붙이는 응답기", () => {
  // 원천 쪽에서 Scope 1 행과 출장 행의 뜻을 서로 바꾸고, 총 에너지 행을 재생에너지라고 붙인다.
  const WRONG = { ...TRUE_MEANINGS, "Scope 1": TRUE_MEANINGS["출장"] as Meaning, 출장: TRUE_MEANINGS["Scope 1"] as Meaning, "총 에너지": TRUE_MEANINGS["재생에너지"] as Meaning };

  it("틀린 짝은 값이 다르면 불일치로 나오고 일치로 새지 않는다. 우연히 값이 같은 하나만 일치로 나온다", async () => {
    const result = await run(WRONG);
    const wrongPairs = result.findings.filter((finding) => finding.a && finding.b && ["ghg.scope1", "energy.renewable"].includes(finding.key?.indicator ?? ""));
    expect(wrongPairs.map(row).sort()).toEqual([
      "energy.renewable 2023: mismatch/values_differ (45.32 대 3.2)",
      "energy.renewable 2024: mismatch/values_differ (47.08 대 3.4)",
      // 심어 둔 우연: 원천의 출장 2023 값(1,200)이 보고서의 Scope 1 2023 값과 같다. 뜻을 틀리게 붙이면 이 한 쌍은 일치로 나온다.
      "ghg.scope1 2023: match/exact (1,200 대 1,200)",
      "ghg.scope1 2024: mismatch/values_differ (80 대 1,350)",
    ]);
    // 뜻이 틀리게 붙은 숫자 6개(원천의 Scope 1, 출장, 총 에너지 행 × 2년) 가운데 일치로 나온 것은 심어 둔 우연 하나뿐이다.
    const wrongRows = new Set(["p1-t1:r1c2", "p1-t1:r1c3", "p1-t1:r3c2", "p1-t1:r3c3", "p1-t1:r10c2", "p1-t1:r10c3"]);
    const leaked = result.findings.filter((finding) => finding.outcome === "match" && finding.a && wrongRows.has(finding.a.factId));
    expect(leaked.map((finding) => finding.a?.factId)).toEqual(["p1-t1:r10c2"]);
    // 원천의 Scope 1 행은 출장이라고 붙었고 보고서에는 출장이 없다: 짝 없음. 보고서의 총 에너지도 짝을 잃는다.
    expect(result.findings.filter((finding) => finding.outcome === "unpaired").map(row).sort()).toEqual([
      "energy.total 2023: unpaired/no_counterpart (- 대 45.3)",
      "energy.total 2024: unpaired/no_counterpart (- 대 47.1)",
      "ghg.scope3.cat6 2023: unpaired/no_counterpart (1,200 대 -)",
      "ghg.scope3.cat6 2024: unpaired/no_counterpart (1,530 대 -)",
    ]);
  });
});
