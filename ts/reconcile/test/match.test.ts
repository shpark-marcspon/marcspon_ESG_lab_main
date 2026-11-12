import { describe, expect, it } from "vitest";
import { reconcile, resolveUnit, roundToPow10, dec, show, shift10, summarize, type Extraction, type Fact, type Label, type MatchOptions, type Pending } from "../src/index.js";
import { catalog, extract, syntheticDocument, units } from "./helpers.js";

// 값과 뜻을 손으로 붙인 가상의 사실 후보로 짝짓기와 비교만 본다.
function side(docId: string, rows: [string, string, string][]): Extraction {
  // 행마다 [행 이름, 단위 글자, 원문 숫자]. 표는 한 열짜리다.
  return extract(syntheticDocument(docId, [{ id: "p1-t1", cells: [["구분", "단위", "값"], ...rows], header: { rows: [0], cols: [0, 1], basis: "rows_before_first_numeric_row" } }]), docId);
}

function labelled(extraction: Extraction, labels: (Partial<Label> | null)[]): Pending[] {
  return extraction.facts.map((fact: Fact, index): Pending => {
    const patch = labels[index];
    if (patch === null || patch === undefined) return { fact, label: null, unverified: null };
    return {
      fact,
      unverified: null,
      label: { factId: fact.id, kind: "indicator", indicatorKey: "ghg.scope1", freeText: null, period: "2024", unit: fact.headers?.row[1]?.text || null, boundary: "unspecified", basisNote: null, confidence: "high", evidence: [], source: "ai", ...patch },
    };
  });
}

const options: MatchOptions = { catalog, units, minConfidence: "medium", defaultPeriod: { a: null, b: null } };

function run(a: [string, string, string][], aLabels: (Partial<Label> | null)[], b: [string, string, string][], bLabels: (Partial<Label> | null)[], overrides: Partial<MatchOptions> = {}) {
  const ea = side("a", a);
  const eb = side("b", b);
  const findings = reconcile(labelled(ea, aLabels), labelled(eb, bLabels), { ...options, ...overrides });
  return { findings, summary: summarize(ea, eb, findings) };
}

describe("짝짓기의 열쇠는 뜻이다", () => {
  it("뜻(지표, 기간, 범위)이 같은 것끼리 짝짓고 그다음 값을 견준다", () => {
    const { findings } = run([["Scope 1", "tCO2eq", "1,200"]], [{}], [["직접 배출", "tCO2eq", "1,200"]], [{}]);
    expect(findings).toHaveLength(1);
    expect(findings[0]).toMatchObject({ outcome: "match", reason: "exact", key: { indicator: "ghg.scope1", period: "2024", boundary: "unspecified" }, a: { raw: "1,200" }, b: { raw: "1,200" } });
  });

  it("값이 같고 뜻이 다르면 일치가 아니다", () => {
    const { findings, summary } = run([["Scope 1", "tCO2eq", "1,200"]], [{ indicatorKey: "ghg.scope1" }], [["출장", "tCO2eq", "1,200"]], [{ indicatorKey: "ghg.scope3.cat6" }]);
    expect(findings.map((finding) => finding.outcome)).toEqual(["unpaired", "unpaired"]);
    expect(findings.some((finding) => finding.outcome === "match")).toBe(false);
    expect(summary.pairs.match).toBe(0);
  });

  it("값이 같아도 기간이 다르면 짝이 아니다", () => {
    const { findings } = run([["Scope 1", "tCO2eq", "1,200"]], [{ period: "2023" }], [["Scope 1", "tCO2eq", "1,200"]], [{ period: "2024" }]);
    expect(findings.map((finding) => finding.outcome)).toEqual(["unpaired", "unpaired"]);
  });

  it("뜻이 같고 값이 다르면 불일치다 (값이 다르다는 이유로 짝을 풀지 않는다)", () => {
    const { findings } = run([["Scope 1", "tCO2eq", "1,530"]], [{}], [["Scope 1", "tCO2eq", "1,350"]], [{}]);
    expect(findings[0]).toMatchObject({ outcome: "mismatch", reason: "values_differ", comparison: { baseA: "1530", baseB: "1350", difference: "180" } });
  });

  it("같은 뜻의 숫자가 한쪽에 여럿이면 값을 보고 고르지 않고 전부 미확인이다", () => {
    const { findings } = run(
      [
        ["Scope 1", "tCO2eq", "1,200"],
        ["Scope 1 (다른 표)", "tCO2eq", "9,999"],
      ],
      [{}, {}],
      [["Scope 1", "tCO2eq", "1,200"]],
      [{}],
    );
    expect(findings.map((finding) => [finding.outcome, finding.reason])).toEqual([
      ["unverified", "ambiguous_pair"],
      ["unverified", "ambiguous_pair"],
      ["unverified", "ambiguous_pair"],
    ]);
  });

  it("지표와 기간은 같고 범위만 다르면 설명 필요다", () => {
    const { findings } = run([["에너지", "TJ", "40.1"]], [{ indicatorKey: "energy.total", boundary: "domestic" }], [["에너지", "TJ", "44.0"]], [{ indicatorKey: "energy.total", boundary: "domestic_overseas" }]);
    expect(findings).toHaveLength(1);
    expect(findings[0]).toMatchObject({ outcome: "needs_explanation", reason: "boundary_differs", a: { raw: "40.1" }, b: { raw: "44.0" } });
  });

  it("기준 메모가 서로 다르면 값이 같아도 설명 필요다", () => {
    const { findings } = run([["Scope 2", "tCO2eq", "5,000"]], [{ basisNote: "지역기반" }], [["Scope 2", "tCO2eq", "5,000"]], [{ basisNote: "시장 기반" }]);
    expect(findings[0]).toMatchObject({ outcome: "needs_explanation", reason: "basis_note_differs" });
  });
});

describe("단위 환산 (10 의 거듭제곱 자리 옮김만)", () => {
  it("단위 글자를 계열과 지수로 읽는다", () => {
    expect(resolveUnit("천 톤", units)).toMatchObject({ family: "mass", exp: 3, scaleWord: "천" });
    expect(resolveUnit("(단위: tCO₂eq)", units)).toMatchObject({ family: "mass_co2e", exp: 0 });
    expect(resolveUnit("kg", units)).toMatchObject({ family: "mass", exp: -3 });
    expect(resolveUnit("십억 원", units)).toMatchObject({ family: "currency_krw", exp: 9 });
    expect(resolveUnit("GWh", units)).toMatchObject({ family: "energy_wh", exp: 3 });
    expect(resolveUnit("tCO2eq/억 원", units)).toBeNull();
    expect(resolveUnit("억원", units)).toMatchObject({ family: "currency_krw", exp: 8 });
    expect(resolveUnit("천 명", units)).toMatchObject({ family: "count_person", exp: 3 });
    // 수 단위 낱말 + 단위로 읽히는 보통 낱말은 단위가 아니다.
    expect(resolveUnit("조건", units)).toBeNull();
    expect(resolveUnit("조명", units)).toBeNull();
    expect(resolveUnit("만개", units)).toBeNull();
  });

  it("천 톤과 톤, kg 과 톤을 환산해 견준다", () => {
    const water = run([["용수", "천 톤", "12"]], [{ indicatorKey: "water.use" }], [["용수", "톤", "12,000"]], [{ indicatorKey: "water.use" }]);
    expect(water.findings[0]).toMatchObject({ outcome: "match", comparison: { expA: 3, expB: 0, baseA: "12000", baseB: "12000" } });
    const waste = run([["폐기물", "kg", "310,000"]], [{ indicatorKey: "waste.generated" }], [["폐기물", "톤", "310"]], [{ indicatorKey: "waste.generated" }]);
    expect(waste.findings[0]).toMatchObject({ outcome: "match", comparison: { expA: -3, baseA: "310", baseB: "310" } });
  });

  it("단위 착오: 숫자열이 같아도 환산한 값이 다르면 불일치이고 실마리를 남긴다", () => {
    const { findings } = run([["매출", "백만 원", "5,120"]], [{ indicatorKey: "revenue" }], [["매출", "억 원", "5,120"]], [{ indicatorKey: "revenue" }]);
    expect(findings[0]).toMatchObject({ outcome: "mismatch", comparison: { baseA: "5120000000", baseB: "512000000000" } });
    expect(findings[0]?.comparison?.notes).toContain("same_digits_different_scale");
  });

  it("단위 표에 없는 단위, 지표의 계열과 다른 단위는 미확인이다", () => {
    const unknown = run([["Scope 1", "말", "1,200"]], [{}], [["Scope 1", "tCO2eq", "1,200"]], [{}]);
    expect(unknown.findings.map((finding) => [finding.outcome, finding.reason])).toEqual([
      ["unverified", "unit_unknown"],
      ["unpaired", "no_counterpart"],
    ]);
    const family = run([["Scope 1", "TJ", "1,200"]], [{}], [["Scope 1", "tCO2eq", "1,200"]], [{}]);
    expect(family.findings[0]).toMatchObject({ outcome: "unverified", reason: "unit_family_mismatch" });
  });

  it("한쪽에만 단위가 있으면 값이 같아 보여도 일치가 아니라 설명 필요다", () => {
    const { findings } = run([["Scope 1", "", "1,200"]], [{ unit: null }], [["Scope 1", "tCO2eq", "1,200"]], [{}]);
    expect(findings[0]).toMatchObject({ outcome: "needs_explanation", reason: "unit_missing_one_side" });
  });
});

describe("반올림 허용폭 (표시가 거친 쪽의 자리에 맞춰 반올림한 뒤 비교)", () => {
  const energy = (a: string, b: string, unitA = "TJ", unitB = "TJ") => run([["에너지", unitA, a]], [{ indicatorKey: "energy.total" }], [["에너지", unitB, b]], [{ indicatorKey: "energy.total" }]).findings[0];

  it("자릿수가 다른 두 값: 거친 쪽 자리로 반올림해 같으면 일치", () => {
    expect(energy("45.32", "45.3")).toMatchObject({ outcome: "match", reason: "rounded_half_up", comparison: { quantumExp: -1 } });
    expect(energy("2,689.020", "2,689")).toMatchObject({ outcome: "match", reason: "rounded_half_up", comparison: { quantumExp: 0 } });
    expect(energy("2,689.0", "2,689")).toMatchObject({ outcome: "match", reason: "exact" });
    expect(energy("47.08", "47.1")).toMatchObject({ outcome: "match", reason: "rounded_half_up" });
  });

  it("반올림해도 다르면 불일치", () => {
    expect(energy("45.48", "45.3")).toMatchObject({ outcome: "mismatch" });
    expect(energy("45.3", "45.4")).toMatchObject({ outcome: "mismatch" });
    expect(energy("45.24", "45.3")).toMatchObject({ outcome: "mismatch" });
  });

  it("정확히 가운데 값은 올림과 짝수 반올림 어느 쪽이든 받아들이고 적용한 규칙을 남긴다", () => {
    expect(energy("45.25", "45.3")).toMatchObject({ outcome: "match", reason: "rounded_half_up" });
    expect(energy("45.25", "45.2")).toMatchObject({ outcome: "match", reason: "rounded_half_even" });
  });

  it("버림으로만 맞으면 일치가 아니라 설명 필요다", () => {
    expect(energy("45.38", "45.3")).toMatchObject({ outcome: "needs_explanation", reason: "matches_if_truncated" });
    expect(energy("2,689.6", "2,689")).toMatchObject({ outcome: "needs_explanation", reason: "matches_if_truncated" });
  });

  it("단위가 다르면 환산한 뒤의 자리로 견준다 (GJ 12,345 와 TJ 12.3)", () => {
    expect(energy("12,345", "12.3", "GJ", "TJ")).toMatchObject({ outcome: "match", reason: "rounded_half_up", comparison: { quantumExp: -1, baseA: "12.345", baseB: "12.3" } });
    expect(energy("12,455", "12.3", "GJ", "TJ")).toMatchObject({ outcome: "mismatch" });
  });

  it("증감 세모(▲▼)는 부호를 정할 수 없어 값이 같아도 일치가 아니라 설명 필요다", () => {
    expect(energy("▲ 3", "3")).toMatchObject({ outcome: "needs_explanation", reason: "sign_ambiguous" });
  });

  it("부호만 다르면 설명 필요다", () => {
    expect(energy("(120)", "120")).toMatchObject({ outcome: "needs_explanation", reason: "sign_differs" });
  });

  it("십진 도우미: 10 의 거듭제곱 자리 반올림과 자리 옮김", () => {
    expect(show(roundToPow10(dec("12345"), 3, "half_up"))).toBe("12000");
    expect(show(roundToPow10(dec("12500"), 3, "half_even"))).toBe("12000");
    expect(show(roundToPow10(dec("-12500"), 3, "half_up"))).toBe("-13000");
    expect(show(roundToPow10(dec("0.125"), -2, "half_up"))).toBe("0.13");
    expect(show(shift10(dec("12.5"), 3))).toBe("12500");
    expect(show(shift10(dec("12000"), -3))).toBe("12");
    expect(show(shift10(dec("0.1"), -20))).toBe("0.000000000000000000001");
  });
});

describe("미확인으로 가는 뜻", () => {
  it("답 없음, 데이터 값 아님, 목록에 없음, 낮은 확신, 기간 모름", () => {
    const { findings } = run(
      [
        ["가", "tCO2eq", "1"],
        ["나", "tCO2eq", "2"],
        ["다", "tCO2eq", "3"],
        ["라", "tCO2eq", "4"],
        ["마", "tCO2eq", "5"],
      ],
      [null, { kind: "not_data", indicatorKey: null }, { kind: "not_in_catalog", indicatorKey: null, freeText: "모름" }, { confidence: "low" }, { period: null }],
      [],
      [],
    );
    expect(findings.map((finding) => [finding.outcome, finding.reason])).toEqual([
      ["unverified", "not_answered"],
      ["unverified", "labeled_not_data"],
      ["unverified", "not_in_catalog"],
      ["unverified", "low_confidence_label"],
      ["unverified", "period_unknown"],
    ]);
  });

  it("표에 기간이 없으면 문서의 기본 기간을 쓰고 그 사실을 남긴다", () => {
    const { findings } = run([["Scope 1", "tCO2eq", "1,200"]], [{ period: null }], [["Scope 1", "tCO2eq", "1,200"]], [{}], { defaultPeriod: { a: "2024", b: null } });
    expect(findings[0]).toMatchObject({ outcome: "match" });
    expect(findings[0]?.comparison?.notes).toContain("period_from_document_default");
  });
});

describe("요약의 분모", () => {
  it("다섯 결과 종류가 나오고, 숫자 N개 중 M개 대조·K개 미확인이 N 을 빠짐없이 덮는다", () => {
    const { findings, summary } = run(
      [
        ["Scope 1", "tCO2eq", "1,200"],
        ["Scope 2", "tCO2eq", "5,000"],
        ["에너지", "TJ", "40.1"],
        ["출장", "tCO2eq", "80"],
        ["기타", "tCO2eq", "7"],
      ],
      [{}, { indicatorKey: "ghg.scope2.location" }, { indicatorKey: "energy.total", boundary: "domestic" }, { indicatorKey: "ghg.scope3.cat6" }, null],
      [
        ["Scope 1", "tCO2eq", "1,200"],
        ["Scope 2", "tCO2eq", "5,400"],
        ["에너지", "TJ", "44.0"],
      ],
      [{}, { indicatorKey: "ghg.scope2.location" }, { indicatorKey: "energy.total", boundary: "domestic_overseas" }],
    );
    expect(new Set(findings.map((finding) => finding.outcome))).toEqual(new Set(["match", "mismatch", "needs_explanation", "unpaired", "unverified"]));
    expect(summary.a).toMatchObject({ valueCandidates: 5, compared: 3, byOutcome: { match: 1, mismatch: 1, needs_explanation: 1, unpaired: 1, unverified: 1 }, unverifiedByReason: { not_answered: 1 } });
    expect(summary.a.sentence).toBe("숫자 5개 중 3개 대조 (일치 1, 불일치 1, 설명 필요 1), 짝 없음 1개, 미확인 1개");
    expect(summary.b).toMatchObject({ valueCandidates: 3, compared: 3, byOutcome: { unverified: 0 } });
    expect(summary.pairs).toEqual({ match: 1, mismatch: 1, needs_explanation: 1 });
  });

  it("미확인은 분모에서 빠지지 않는다: 아무 뜻도 못 붙여도 N 은 값 후보 전체다", () => {
    const { summary } = run(
      [
        ["가", "tCO2eq", "1"],
        ["나", "tCO2eq", "2"],
      ],
      [null, null],
      [["가", "tCO2eq", "1"]],
      [null],
    );
    expect(summary.a.valueCandidates).toBe(2);
    expect(summary.a.byOutcome.unverified).toBe(2);
    expect(summary.a.compared).toBe(0);
    expect(summary.a.sentence).toBe("숫자 2개 중 0개 대조 (일치 0, 불일치 0, 설명 필요 0), 짝 없음 0개, 미확인 2개");
  });

  it("결과에서 숫자 하나가 빠지면 요약을 내지 않고 예외를 낸다", () => {
    const ea = side("a", [["Scope 1", "tCO2eq", "1,200"]]);
    const eb = side("b", []);
    expect(() => summarize(ea, eb, [])).toThrow(/빠진 숫자/);
  });
});
