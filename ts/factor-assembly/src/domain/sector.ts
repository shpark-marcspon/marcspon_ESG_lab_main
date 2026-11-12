import type { AssemblyEvidence, AssemblyMapping, IndustryInput, SectorEntry, Step } from "./types.js";

export interface ResolvedSector {
  entry: SectorEntry;
  evidence: AssemblyEvidence["sector"];
}

/**
 * 사업장의 업종에서 CH4·N2O 계수의 부문을 정한다.
 * 한 중분류 코드가 두 부문에 적혀 있으면(38, 58, 59) 고르지 않는다. 원문 주석이 더 자세한 코드로 부문을 정해 준 경우에만
 * 그 부문을 쓰고, 아니면 실패로 돌려 부문을 직접 받게 한다.
 */
export function resolveSector(industry: IndustryInput, mapping: AssemblyMapping): Step<ResolvedSector> {
  const raw: unknown = industry;
  if (typeof raw !== "object" || raw === null) {
    return { ok: false, failure: { kind: "missing_input", field: "industry", reason: "사업장의 부문도 업종 코드도 없다 (기본값 없음)" } };
  }
  const { sector, ksicCode } = raw as { sector?: unknown; ksicCode?: unknown };
  const byKey = (key: string): SectorEntry | undefined => mapping.sectors.find((entry) => entry.key === key);

  if (sector !== undefined) {
    if (ksicCode !== undefined) {
      return { ok: false, failure: { kind: "invalid_input", field: "industry", value: `${String(sector)} + ${String(ksicCode)}`, reason: "부문과 업종 코드 가운데 하나만 준다" } };
    }
    const entry = typeof sector === "string" ? byKey(sector) : undefined;
    if (entry === undefined) {
      return {
        ok: false,
        failure: { kind: "invalid_input", field: "industry.sector", value: String(sector), reason: `부문 키는 ${mapping.sectors.map((s) => s.key).join(", ")} 중 하나다` },
      };
    }
    return { ok: true, value: { entry, evidence: { key: entry.key, label: entry.label, basis: "input", ksicCode: null, locator: entry.locator } } };
  }

  if (ksicCode === undefined) {
    return { ok: false, failure: { kind: "missing_input", field: "industry", reason: "사업장의 부문도 업종 코드도 없다 (기본값 없음)" } };
  }
  if (typeof ksicCode !== "string" || !/^\d{2,5}$/.test(ksicCode)) {
    return { ok: false, failure: { kind: "invalid_input", field: "industry.ksicCode", value: String(ksicCode), reason: "한국표준산업분류 코드는 숫자 2~5자리 문자열이다 (앞 두 자리가 중분류)" } };
  }
  const division = ksicCode.slice(0, 2);
  const holders = mapping.sectors.filter((entry) => entry.ksic_divisions.includes(division));
  const only = holders[0];
  if (only === undefined) return { ok: false, failure: { kind: "sector_not_defined", ksicCode, division } };
  if (holders.length === 1) {
    return { ok: true, value: { entry: only, evidence: { key: only.key, label: only.label, basis: "ksic_division", ksicCode, locator: only.locator } } };
  }

  const overlap = mapping.sector_overlaps.find((entry) => entry.division === division);
  const matched = (overlap?.exceptions ?? []).filter((exception) => ksicCode.startsWith(exception.code_prefix));
  const exception = matched[0];
  const entry = exception === undefined ? undefined : byKey(exception.sector);
  if (overlap !== undefined && exception !== undefined && matched.length === 1 && entry !== undefined) {
    return {
      ok: true,
      value: { entry, evidence: { key: entry.key, label: entry.label, basis: "ksic_exception", ksicCode, locator: `${overlap.locator}: ${exception.raw}` } },
    };
  }
  return {
    ok: false,
    failure: {
      kind: "sector_ambiguous",
      ksicCode,
      division,
      candidates: holders.map((holder) => holder.key),
      notes: (overlap?.exceptions ?? []).map((item) => item.raw),
      locator: overlap?.locator ?? holders.map((holder) => holder.locator).join(" / "),
    },
  };
}
