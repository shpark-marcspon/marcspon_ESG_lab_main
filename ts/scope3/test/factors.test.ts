import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { FactorStore, FactorStoreError, loadFactorStore, shortlist } from "../src/index.js";
import { writeSyntheticFactors } from "./helpers.js";

describe("계수 표 읽기", () => {
  it("CO2e 이고 값이 있는 배출계수만 후보다. 기체별 행과 빈 값은 id 로만 안다", () => {
    const store = FactorStore.load(writeSyntheticFactors());
    expect(store.recordCount).toBe(10);
    expect(store.candidateCount).toBe(8);
    expect(store.has("syn:steel-kg-ch4")).toBe(true);
    expect(store.get("syn:steel-kg-ch4")).toBeUndefined();
    expect(store.get("syn:empty")).toBeUndefined();
    expect(store.get("syn:steel-kg")?.denominatorDim).toBe("kg");
  });

  it("활동·거리 기반과 지출 기반을 가르고 라이선스 상태로 걸러 낸다", () => {
    const store = FactorStore.load(writeSyntheticFactors());
    expect(store.candidates({ category: 1, basis: "physical" }).map((factor) => factor.id)).toEqual(["syn:steel-kg", "syn:steel-t", "syn:pp-kg"]);
    expect(store.candidates({ category: 1, basis: "physical", excludeLicenseStatuses: ["disputed", "unverified"] }).map((factor) => factor.id)).toEqual(["syn:steel-kg", "syn:steel-t"]);
    expect(store.candidates({ category: 2, basis: "physical" })).toEqual([]);
    expect(store.candidates({ category: 2, basis: "spend" })).toHaveLength(3);
  });

  it("파일이 없거나 깨졌으면 빈 후보가 아니라 예외다", () => {
    const dir = mkdtempSync(join(tmpdir(), "scope3-broken-"));
    expect(() => FactorStore.load(join(dir, "none.jsonl"))).toThrow(FactorStoreError);
    writeFileSync(join(dir, "broken.jsonl"), '{"id": "x"\n');
    expect(() => FactorStore.load(join(dir, "broken.jsonl"))).toThrow(/JSON 이 아니다/);
    writeFileSync(join(dir, "empty.jsonl"), "\n");
    expect(() => FactorStore.load(join(dir, "empty.jsonl"))).toThrow(/한 건도 없다/);
  });

  it("밴드 C 의 실제 표: 카테고리 1 활동 기반 271건 중 disputed 를 빼면 73건", () => {
    const store = loadFactorStore();
    const activity = (category: number, exclude: ("disputed" | "unverified")[]) =>
      store.candidates({ category, basis: "physical", excludeLicenseStatuses: exclude }).filter((factor) => factor.factorType === "activity").length;
    expect(store.recordCount).toBe(10754);
    expect(activity(1, [])).toBe(271);
    expect(activity(1, ["disputed", "unverified"])).toBe(73);
    expect(activity(2, [])).toBe(269);
    expect(activity(2, ["disputed", "unverified"])).toBe(71);
    expect(store.candidates({ category: 1, basis: "spend" })).toHaveLength(1016);
    expect(store.candidates({ category: 6, basis: "spend" })).toHaveLength(0);
  });
});

describe("후보 추리기", () => {
  it("품목명과 검색어에 겹치는 후보를 앞에 두고 수를 제한한다", () => {
    const store = FactorStore.load(writeSyntheticFactors());
    const spend = store.candidates({ category: 1, basis: "spend" });
    const byKeyword = shortlist(spend, { texts: ["법률 자문"], keywords: ["legal services"] }, 2);
    expect(byKeyword.factors.map((factor) => factor.id)).toEqual(["syn:spend-legal", "syn:spend-computer"]);
    expect(byKeyword.total).toBe(3);
    const korean = shortlist(store.candidates({ category: 1, basis: "physical" }), { texts: ["폴리프로필렌 수지"], keywords: [] }, 1);
    expect(korean.factors.map((factor) => factor.id)).toEqual(["syn:pp-kg"]);
  });

  it("겹치는 것이 없으면 그 사실을 알린다 (lexicalMatches 0)", () => {
    const store = FactorStore.load(writeSyntheticFactors());
    const result = shortlist(store.candidates({ category: 1, basis: "spend" }), { texts: ["zzz"], keywords: [] }, 2);
    expect(result.lexicalMatches).toBe(0);
    expect(result.factors).toHaveLength(2);
  });
});
