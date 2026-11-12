// 정책: 「어느 산정등급을 쓰는가」를 지침에서 끌어내는 부분. 낱줄을 고르는 일은 하지 않는다 (assemble.ts).

import type { ParameterTiers, Tier } from "ghg-calc";
import type { AssemblyMapping, FacilityGroup, FuelState, Step, TierPolicy } from "./types.js";

/** 규칙의 근거 자리 (지침 원문). 값이 아니라 조문 위치다. */
export const RULES = {
  nonCo2TierOne: "별표 6 고정연소 각 절 「3. 보고대상 온실가스」 표: CH4·N2O 의 산정방법론은 Tier 1 뿐이다",
  oxidationFollowsCo2: "별표 6 고정연소 각 절 「4. 배출량 산정방법론」: 산화계수는 CO2 배출계수와 동일한 산정등급을 사용하여야 한다",
  siteValues: "별표 6 고정연소 각 절 「5. 매개변수별 관리기준」 Tier 3: 사업자가 개발하거나 공급자가 분석한 값 (제16조), Tier 4: 연속측정",
} as const;

const GROUPS: readonly FacilityGroup[] = ["A", "B", "C"];
const isTier = (value: unknown): value is Tier => value === 1 || value === 2 || value === 3 || value === 4;

/** 그룹 최소기준에서 읽는 매개변수: 대응표의 이름 → 엔진의 자리. */
const MINIMUM_PARAMETERS = [
  ["순발열량", "calorific"],
  ["배출계수", "co2"],
  ["산화계수", "oxidation"],
] as const;

export interface ResolvedTiers {
  values: ParameterTiers;
  basis: "explicit" | "group_minimum";
  group: FacilityGroup | null;
  locators: string[];
}

/**
 * 정책을 매개변수별 산정등급으로 푼다.
 * 조합기는 고시의 표에서 값을 가져오므로 Tier 1·2 만 조립할 수 있다. Tier 3·4 는 사업장이 가진 값이 있어야 해서 실패로 돌려준다.
 */
export function resolveTiers(policy: TierPolicy, fuelState: FuelState, mapping: AssemblyMapping): Step<ResolvedTiers> {
  const raw: unknown = policy;
  if (typeof raw !== "object" || raw === null) {
    return { ok: false, failure: { kind: "missing_input", field: "tiers", reason: "적용할 산정등급을 정하지 않았다 (기본값 없음)" } };
  }
  const kind = (raw as { kind?: unknown }).kind;

  let chosen: { calorific: Tier; co2: Tier; oxidation: Tier };
  let basis: ResolvedTiers["basis"];
  let group: FacilityGroup | null = null;
  const locators: string[] = [];

  if (kind === "explicit") {
    const given = raw as Record<string, unknown>;
    for (const field of ["calorific", "co2", "oxidation"] as const) {
      if (given[field] === undefined) {
        return { ok: false, failure: { kind: "missing_input", field: `tiers.${field}`, reason: "매개변수별 산정등급이 빠졌다 (기본값 없음)" } };
      }
      if (!isTier(given[field])) {
        return { ok: false, failure: { kind: "invalid_input", field: `tiers.${field}`, value: String(given[field]), reason: "산정등급은 1~4 의 정수다" } };
      }
    }
    if (given.nonCo2 !== undefined && given.nonCo2 !== 1) {
      return { ok: false, failure: { kind: "tier_combination_invalid", reason: `CH4·N2O 에 Tier ${String(given.nonCo2)} 은 없다`, locator: RULES.nonCo2TierOne } };
    }
    chosen = { calorific: given.calorific as Tier, co2: given.co2 as Tier, oxidation: given.oxidation as Tier };
    basis = "explicit";
  } else if (kind === "group_minimum") {
    const given = (raw as { group?: unknown }).group;
    if (given === undefined) {
      return { ok: false, failure: { kind: "missing_input", field: "tiers.group", reason: "시설 규모 그룹(A·B·C)이 빠졌다 (기본값 없음)" } };
    }
    if (!GROUPS.includes(given as FacilityGroup)) {
      return { ok: false, failure: { kind: "invalid_input", field: "tiers.group", value: String(given), reason: "시설 규모 그룹은 A, B, C 중 하나다" } };
    }
    group = given as FacilityGroup;
    const picked: Partial<Record<"calorific" | "co2" | "oxidation", Tier>> = {};
    for (const [parameter, slot] of MINIMUM_PARAMETERS) {
      const found = mapping.tier_minimums.filter(
        (entry) => entry.activity === "고정연소" && entry.fuel_state === fuelState && entry.parameter === parameter && entry.group === group,
      );
      const only = found[0];
      if (only === undefined || found.length !== 1 || !isTier(only.tier)) {
        return { ok: false, failure: { kind: "tier_minimum_not_found", fuelState, group, parameter, found: found.length } };
      }
      picked[slot] = only.tier;
      locators.push(`별표 5 ${only.locator} → Tier ${only.tier}`);
    }
    chosen = picked as { calorific: Tier; co2: Tier; oxidation: Tier };
    basis = "group_minimum";
  } else if (kind === undefined) {
    return { ok: false, failure: { kind: "missing_input", field: "tiers.kind", reason: "산정등급을 직접 줄지(explicit) 그룹 최소기준을 따를지(group_minimum) 정하지 않았다" } };
  } else {
    return { ok: false, failure: { kind: "invalid_input", field: "tiers.kind", value: String(kind), reason: "explicit 또는 group_minimum" } };
  }

  if (chosen.co2 !== chosen.oxidation) {
    return {
      ok: false,
      failure: {
        kind: "tier_combination_invalid",
        reason: `CO2 배출계수는 Tier ${chosen.co2} 인데 산화계수는 Tier ${chosen.oxidation} 이다`,
        locator: RULES.oxidationFollowsCo2,
      },
    };
  }
  for (const [parameter, tier] of [["발열량", chosen.calorific], ["CO2 배출계수", chosen.co2], ["산화계수", chosen.oxidation]] as const) {
    if (tier >= 3) {
      return { ok: false, failure: { kind: "tier_requires_site_values", parameter, tier, reason: RULES.siteValues } };
    }
  }
  locators.push(RULES.nonCo2TierOne);
  return { ok: true, value: { values: { ...chosen, nonCo2: 1 }, basis, group, locators } };
}
