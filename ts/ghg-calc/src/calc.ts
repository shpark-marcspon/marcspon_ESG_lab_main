import { ZERO, add, formatDecimal, isNegative, mul, parseDecimal, type Decimal } from "./decimal.js";
import { convertUnit } from "./units.js";
import type {
  ActivityRow,
  CalcOptions,
  CombustionFactor,
  Factor,
  Failure,
  Gas,
  GasResult,
  GwpSet,
  ParameterTiers,
  PurchasedEnergyFactor,
  ReferenceTables,
  Result,
  RowFailed,
  RowIdentity,
  RowOk,
  RowResult,
  SourceType,
  Step,
} from "./types.js";

/** 결과의 질량 단위. 기체별 배출량은 모두 이 단위로 환산한 뒤 GWP 를 곱한다. */
const RESULT_MASS_UNIT = "t";
const GASES: readonly Gas[] = ["CO2", "CH4", "N2O"];

const SCOPE_OF: Record<SourceType, 1 | 2> = {
  stationary_combustion: 1,
  mobile_combustion: 1,
  purchased_electricity: 2,
  purchased_heat: 2,
};

const fail = (failure: Failure): { ok: false; failure: Failure } => ({ ok: false, failure });

function refValue(text: string, where: string): Result<Decimal> {
  const value = parseDecimal(text);
  // 계수, 발열량, GWP, 산화계수는 음수일 수 없다.
  if (value === null || isNegative(value)) return fail({ kind: "invalid_reference_value", where, value: String(text) });
  return { ok: true, value };
}

const TIER_KEYS = ["calorific", "co2", "nonCo2", "oxidation"] as const;

function sameTiers(a: ParameterTiers | undefined, b: ParameterTiers | undefined): boolean {
  if (a === undefined || b === undefined) return a === b;
  return TIER_KEYS.every((key) => a[key] === b[key]);
}

const tiersText = (tiers: ParameterTiers): string => TIER_KEYS.map((key) => `${key}=${String(tiers[key])}`).join(",");

function selectFactor(row: ActivityRow, factors: readonly Factor[]): Result<Factor> {
  const expectedKind: Factor["kind"] =
    row.sourceType === "stationary_combustion" || row.sourceType === "mobile_combustion"
      ? "combustion"
      : "purchased_energy";

  if (row.factorId !== undefined) {
    const criteria = { factorId: row.factorId };
    const byId = factors.filter((f) => f.id === row.factorId);
    const found = byId[0];
    if (found === undefined) return fail({ kind: "factor_not_found", criteria });
    if (byId.length > 1) return fail({ kind: "factor_ambiguous", criteria, candidateIds: byId.map((f) => f.id) });
    if (found.kind !== expectedKind) {
      return fail({ kind: "factor_kind_mismatch", factorId: found.id, expected: expectedKind, actual: found.kind });
    }
    return { ok: true, value: found };
  }

  // 이름으로 고를 때는 구분(부문, 매개변수별 산정등급, 시설의 쓰임, 그 밖의 구분)과 연도가 정확히 같아야 한다.
  // 비워 둔 쪽은 비워 둔 계수에만 맞는다.
  const criteria: Record<string, string> = { sourceType: row.sourceType };
  if (row.fuel !== undefined) criteria.fuel = row.fuel;
  if (row.sector !== undefined) criteria.sector = row.sector;
  if (row.parameterTiers !== undefined) criteria.parameterTiers = tiersText(row.parameterTiers);
  if (row.facilityUse !== undefined) criteria.facilityUse = row.facilityUse;
  if (row.variant !== undefined) criteria.variant = row.variant;
  if (row.factorYear !== undefined) criteria.factorYear = String(row.factorYear);

  const candidates = factors.filter((f) => {
    if (f.variant !== row.variant || f.year !== row.factorYear) return false;
    if (f.kind === "combustion") {
      return (
        expectedKind === "combustion" &&
        f.fuel === row.fuel &&
        f.combustionType === (row.sourceType === "stationary_combustion" ? "stationary" : "mobile") &&
        f.sector === row.sector &&
        sameTiers(f.parameterTiers, row.parameterTiers) &&
        f.facilityUse === row.facilityUse
      );
    }
    // 구매 전력·열 계수에는 부문·산정등급이 없다. 행에 적혀 있으면 맞는 계수가 없는 것이다.
    return (
      expectedKind === "purchased_energy" &&
      f.energyType === (row.sourceType === "purchased_electricity" ? "electricity" : "heat") &&
      row.sector === undefined &&
      row.parameterTiers === undefined &&
      row.facilityUse === undefined
    );
  });
  const found = candidates[0];
  if (found === undefined) return fail({ kind: "factor_not_found", criteria });
  if (candidates.length > 1) {
    return fail({ kind: "factor_ambiguous", criteria, candidateIds: candidates.map((f) => f.id) });
  }
  return { ok: true, value: found };
}

function selectGwpSet(gwpSetId: string, sets: readonly GwpSet[]): Result<GwpSet> {
  const matches = sets.filter((s) => s.id === gwpSetId);
  const found = matches[0];
  if (found === undefined) return fail({ kind: "gwp_set_not_found", gwpSetId });
  if (matches.length > 1) return fail({ kind: "gwp_set_ambiguous", gwpSetId });
  return { ok: true, value: found };
}

function gwpOf(set: GwpSet, gas: Gas): Result<Decimal> {
  const text = set.values[gas];
  if (text === undefined) return fail({ kind: "gwp_value_missing", gwpSetId: set.id, gas });
  return refValue(text, `gwpSets[${set.id}].values.${gas}`);
}

interface Computed {
  energy: RowOk["energy"];
  gases: GasResult[];
  steps: Step[];
  calorificBasis: RowOk["evidence"]["calorificBasis"];
  oxidationFactor: string | null;
}

function toCo2e(
  gas: Gas,
  massTonnes: Decimal,
  gwpSet: GwpSet,
  steps: Step[],
): Result<GasResult> {
  const gwp = gwpOf(gwpSet, gas);
  if (!gwp.ok) return gwp;
  const co2e = mul(massTonnes, gwp.value);
  steps.push({
    label: `${gas} 환산`,
    formula: `${formatDecimal(massTonnes)} t${gas} × GWP ${formatDecimal(gwp.value)} (${gwpSet.id})`,
    result: formatDecimal(co2e),
    unit: "tCO2eq",
  });
  return {
    ok: true,
    value: {
      gas,
      mass: formatDecimal(massTonnes),
      massUnit: RESULT_MASS_UNIT,
      gwp: formatDecimal(gwp.value),
      co2e: formatDecimal(co2e),
    },
  };
}

function computeCombustion(
  amount: Decimal,
  row: ActivityRow,
  factor: CombustionFactor,
  gwpSet: GwpSet,
  tables: ReferenceTables,
  options: CalcOptions,
): Result<Computed> {
  const where = `factors[${factor.id}]`;
  const basis = factor.calorific.basis;
  if (options.requireCalorificBasis !== undefined && basis !== options.requireCalorificBasis) {
    return fail({
      kind: "calorific_basis_mismatch",
      factorId: factor.id,
      required: options.requireCalorificBasis,
      actual: basis,
    });
  }
  for (const gas of GASES) {
    const count = factor.emissionFactors.filter((e) => e.gas === gas).length;
    if (count !== 1) {
      return fail({
        kind: "factor_incomplete",
        factorId: factor.id,
        reason: count === 0 ? `${gas} 배출계수가 없다` : `${gas} 배출계수가 ${count}개다`,
      });
    }
  }

  const steps: Step[] = [];
  const energyUnit = factor.calorific.energyUnit;
  let energy: Decimal;

  // 사용량 단위를 발열량의 기준 단위로 맞춘다. 그 환산이 없고 사용량이 이미 에너지 단위이면
  // (예: 열량으로 계량한 연료) 발열량을 거치지 않고 에너지량으로 쓴다.
  const asQuantity = convertUnit(amount, row.unit, factor.calorific.perUnit, tables.unitConversions, "사용량 → 발열량 기준 단위");
  if (asQuantity.ok) {
    if (asQuantity.value.step) steps.push(asQuantity.value.step);
    const calorific = refValue(factor.calorific.value, `${where}.calorific.value`);
    if (!calorific.ok) return calorific;
    energy = mul(asQuantity.value.value, calorific.value);
    steps.push({
      label: "에너지량",
      formula: `${formatDecimal(asQuantity.value.value)} ${factor.calorific.perUnit} × ${formatDecimal(calorific.value)} ${energyUnit}/${factor.calorific.perUnit} (${basis === "net" ? "순발열량" : "총발열량"})`,
      result: formatDecimal(energy),
      unit: energyUnit,
    });
  } else {
    if (asQuantity.failure.kind !== "unit_conversion_not_found") return asQuantity;
    const asEnergy = convertUnit(amount, row.unit, energyUnit, tables.unitConversions, "사용량(에너지) → 발열량 에너지 단위");
    if (!asEnergy.ok) {
      // 두 길이 모두 없으면 먼저 시도한 쪽(사용량 단위 환산)의 실패를 돌려준다.
      return asEnergy.failure.kind === "unit_conversion_not_found" ? asQuantity : asEnergy;
    }
    if (asEnergy.value.step) steps.push(asEnergy.value.step);
    energy = asEnergy.value.value;
    steps.push({
      label: "에너지량",
      formula: `사용량이 에너지 단위(${row.unit})로 주어져 발열량을 곱하지 않았다`,
      result: formatDecimal(energy),
      unit: energyUnit,
    });
  }

  let oxidation: Decimal | null = null;
  if (factor.oxidationFactor !== undefined) {
    const parsed = refValue(factor.oxidationFactor, `${where}.oxidationFactor`);
    if (!parsed.ok) return parsed;
    oxidation = parsed.value;
  }

  const gases: GasResult[] = [];
  for (const gas of GASES) {
    const ef = factor.emissionFactors.find((e) => e.gas === gas);
    if (ef === undefined) return fail({ kind: "factor_incomplete", factorId: factor.id, reason: `${gas} 배출계수가 없다` });
    const efValue = refValue(ef.value, `${where}.emissionFactors.${gas}`);
    if (!efValue.ok) return efValue;

    const energyForEf = convertUnit(energy, energyUnit, ef.perEnergyUnit, tables.unitConversions, `${gas} 배출계수의 에너지 단위`);
    if (!energyForEf.ok) return energyForEf;
    if (energyForEf.value.step) steps.push(energyForEf.value.step);

    let mass = mul(energyForEf.value.value, efValue.value);
    let formula = `${formatDecimal(energyForEf.value.value)} ${ef.perEnergyUnit} × ${formatDecimal(efValue.value)} ${ef.massUnit}${gas}/${ef.perEnergyUnit}`;
    if (gas === "CO2" && oxidation !== null) {
      mass = mul(mass, oxidation);
      formula += ` × 산화계수 ${formatDecimal(oxidation)}`;
    }
    steps.push({ label: `${gas} 배출량`, formula, result: formatDecimal(mass), unit: `${ef.massUnit}${gas}` });

    const tonnes = convertUnit(mass, ef.massUnit, RESULT_MASS_UNIT, tables.unitConversions, `${gas} 질량`);
    if (!tonnes.ok) return tonnes;
    if (tonnes.value.step) steps.push(tonnes.value.step);

    const gasResult = toCo2e(gas, tonnes.value.value, gwpSet, steps);
    if (!gasResult.ok) return gasResult;
    gases.push(gasResult.value);
  }

  return {
    ok: true,
    value: {
      energy: { value: formatDecimal(energy), unit: energyUnit },
      gases,
      steps,
      calorificBasis: basis,
      oxidationFactor: oxidation === null ? null : formatDecimal(oxidation),
    },
  };
}

function computePurchased(
  amount: Decimal,
  row: ActivityRow,
  factor: PurchasedEnergyFactor,
  gwpSet: GwpSet,
  tables: ReferenceTables,
): Result<Computed> {
  const where = `factors[${factor.id}]`;
  const listed = factor.emissionFactors.map((e) => e.gas);
  const preAggregated = listed.length === 1 && listed[0] === "CO2e";
  const perGas = listed.length === GASES.length && GASES.every((g) => listed.filter((x) => x === g).length === 1);
  if (!preAggregated && !perGas) {
    return fail({
      kind: "factor_incomplete",
      factorId: factor.id,
      reason: `배출계수는 CO2e 하나이거나 CO2·CH4·N2O 셋이어야 한다 (받은 것: ${listed.join(", ") || "없음"})`,
    });
  }

  const steps: Step[] = [];
  const quantity = convertUnit(amount, row.unit, factor.perUnit, tables.unitConversions, "사용량 → 배출계수 기준 단위");
  if (!quantity.ok) return quantity;
  if (quantity.value.step) steps.push(quantity.value.step);

  const gases: GasResult[] = [];
  for (const ef of factor.emissionFactors) {
    const efValue = refValue(ef.value, `${where}.emissionFactors.${ef.gas}`);
    if (!efValue.ok) return efValue;
    const mass = mul(quantity.value.value, efValue.value);
    steps.push({
      label: `${ef.gas} 배출량`,
      formula: `${formatDecimal(quantity.value.value)} ${factor.perUnit} × ${formatDecimal(efValue.value)} ${ef.massUnit}${ef.gas}/${factor.perUnit}`,
      result: formatDecimal(mass),
      unit: `${ef.massUnit}${ef.gas}`,
    });
    const tonnes = convertUnit(mass, ef.massUnit, RESULT_MASS_UNIT, tables.unitConversions, `${ef.gas} 질량`);
    if (!tonnes.ok) return tonnes;
    if (tonnes.value.step) steps.push(tonnes.value.step);

    if (ef.gas === "CO2e") {
      const text = formatDecimal(tonnes.value.value);
      gases.push({ gas: "CO2e", mass: text, massUnit: RESULT_MASS_UNIT, gwp: null, co2e: text });
      continue;
    }
    const gasResult = toCo2e(ef.gas, tonnes.value.value, gwpSet, steps);
    if (!gasResult.ok) return gasResult;
    gases.push(gasResult.value);
  }

  return { ok: true, value: { energy: null, gases, steps, calorificBasis: null, oxidationFactor: null } };
}

function identityOf(row: ActivityRow): RowIdentity {
  return {
    rowId: row.id,
    orgUnit: row.orgUnit,
    period: row.period,
    scope: SCOPE_OF[row.sourceType],
    sourceType: row.sourceType,
  };
}

/** 활동 데이터 한 행을 계산한다. 실패는 예외나 0 이 아니라 `status: "failed"` 로 돌려준다. */
export function calculateRow(row: ActivityRow, tables: ReferenceTables, options: CalcOptions): RowResult {
  // 외부 JSON 에서 온 행은 타입을 믿을 수 없다. 배출원 유형을 모르면 어떤 계수로도 계산하지 않는다.
  const raw: unknown = row;
  const sourceType = typeof raw === "object" && raw !== null ? (raw as { sourceType?: unknown }).sourceType : undefined;
  if (typeof raw !== "object" || raw === null || Array.isArray(raw) || typeof sourceType !== "string" || !Object.hasOwn(SCOPE_OF, sourceType)) {
    const loose = (typeof raw === "object" && raw !== null ? raw : {}) as Partial<Record<keyof ActivityRow, unknown>>;
    return {
      rowId: String(loose.id ?? ""),
      orgUnit: String(loose.orgUnit ?? ""),
      period: String(loose.period ?? ""),
      scope: null,
      sourceType: null,
      status: "failed",
      failure: {
        kind: "invalid_activity",
        field: "sourceType",
        value: String(loose.sourceType ?? ""),
        reason: "알 수 없는 배출원 유형이거나 행이 객체가 아니다",
      },
    };
  }
  const identity = identityOf(row);
  const failed = (failure: Failure): RowFailed => ({ ...identity, status: "failed", failure });

  const amount = parseDecimal(row.amount);
  if (amount === null) {
    return failed({ kind: "invalid_activity", field: "amount", value: String(row.amount), reason: "십진 문자열이 아니다" });
  }
  if (isNegative(amount)) {
    return failed({ kind: "invalid_activity", field: "amount", value: row.amount, reason: "음수 사용량" });
  }
  const combustion = identity.scope === 1;
  if (combustion && (row.fuel === undefined || row.fuel === "") && row.factorId === undefined) {
    return failed({ kind: "invalid_activity", field: "fuel", value: "", reason: "연소 행에 연료도 factorId 도 없다" });
  }

  const gwpSet = selectGwpSet(options.gwpSetId, tables.gwpSets);
  if (!gwpSet.ok) return failed(gwpSet.failure);
  const factor = selectFactor(row, tables.factors);
  if (!factor.ok) return failed(factor.failure);

  const computed =
    factor.value.kind === "combustion"
      ? computeCombustion(amount, row, factor.value, gwpSet.value, tables, options)
      : computePurchased(amount, row, factor.value, gwpSet.value, tables);
  if (!computed.ok) return failed(computed.failure);

  let total = ZERO;
  for (const gas of computed.value.gases) {
    const co2e = parseDecimal(gas.co2e);
    if (co2e === null) throw new Error(`unreachable: formatted decimal failed to parse: ${gas.co2e}`);
    total = add(total, co2e);
  }
  const steps = computed.value.steps;
  steps.push({
    label: "합산",
    formula: computed.value.gases.map((g) => `${g.co2e} (${g.gas})`).join(" + "),
    result: formatDecimal(total),
    unit: "tCO2eq",
  });

  return {
    ...identity,
    status: "ok",
    co2e: formatDecimal(total),
    unit: "tCO2eq",
    energy: computed.value.energy,
    gases: computed.value.gases,
    evidence: {
      factor: { id: factor.value.id, version: factor.value.version, source: factor.value.source },
      gwpSet: { id: gwpSet.value.id, label: gwpSet.value.label, source: gwpSet.value.source },
      calorificBasis: computed.value.calorificBasis,
      oxidationFactor: computed.value.oxidationFactor,
      sector: factor.value.kind === "combustion" ? (factor.value.sector ?? null) : null,
      parameterTiers: factor.value.kind === "combustion" ? (factor.value.parameterTiers ?? null) : null,
      facilityUse: factor.value.kind === "combustion" ? (factor.value.facilityUse ?? null) : null,
      steps,
    },
  };
}

export function calculate(rows: readonly ActivityRow[], tables: ReferenceTables, options: CalcOptions): RowResult[] {
  return rows.map((row) => calculateRow(row, tables, options));
}
