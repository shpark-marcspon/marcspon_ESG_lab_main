import { isNegative, parseDecimal } from "./decimal.js";
import type { Provenance, ReferenceTables } from "./types.js";

export interface TableProblem {
  where: string;
  problem: string;
}

type Loose = Record<string, unknown>;

const isObject = (v: unknown): v is Loose => typeof v === "object" && v !== null && !Array.isArray(v);
const isText = (v: unknown): v is string => typeof v === "string" && v.length > 0;

function checkSource(source: unknown, where: string, problems: TableProblem[]): void {
  if (!isObject(source)) {
    problems.push({ where, problem: "출처(source)가 없다" });
    return;
  }
  const kind = (source as Partial<Provenance>).kind;
  if (kind === "cited") {
    if (!isText(source.url) || !/^https?:\/\//.test(source.url)) problems.push({ where, problem: "cited 출처에 url 이 없다" });
    if (!isText(source.checkedOn) || !/^\d{4}-\d{2}-\d{2}$/.test(source.checkedOn)) {
      problems.push({ where, problem: "cited 출처에 확인 일자(checkedOn, YYYY-MM-DD)가 없다" });
    }
    if (!isText(source.title)) problems.push({ where, problem: "cited 출처에 title 이 없다" });
  } else if (kind === "definition" || kind === "synthetic") {
    if (!isText(source.note)) problems.push({ where, problem: `${kind} 출처에 note 가 없다` });
  } else {
    problems.push({ where, problem: `알 수 없는 출처 종류: ${String(kind)}` });
  }
}

function checkDecimal(value: unknown, where: string, problems: TableProblem[]): void {
  const parsed = parseDecimal(value);
  if (parsed === null) {
    problems.push({ where, problem: `십진 문자열이 아니다: ${JSON.stringify(value)}` });
  } else if (isNegative(parsed)) {
    problems.push({ where, problem: `음수다: ${JSON.stringify(value)}` });
  }
}

const isTier = (v: unknown): boolean => v === 1 || v === 2 || v === 3 || v === 4;

/** 매개변수별 산정등급. 산화계수의 등급은 산화계수가 있을 때만, 그리고 있을 때는 반드시 적는다. */
function checkTiers(tiers: unknown, hasOxidation: boolean, where: string, problems: TableProblem[]): void {
  if (!isObject(tiers)) return void problems.push({ where, problem: "객체가 아니다" });
  for (const key of ["calorific", "co2", "nonCo2"]) {
    if (!isTier(tiers[key])) problems.push({ where: `${where}.${key}`, problem: `산정등급(1~4)이 아니다: ${JSON.stringify(tiers[key])}` });
  }
  if (hasOxidation ? !isTier(tiers.oxidation) : tiers.oxidation !== null) {
    problems.push({
      where: `${where}.oxidation`,
      problem: hasOxidation ? `산화계수가 있는데 그 산정등급(1~4)이 없다: ${JSON.stringify(tiers.oxidation)}` : "산화계수가 없으면 그 산정등급은 null 이어야 한다",
    });
  }
}

/**
 * 외부에서 읽은 기준정보 표의 구조와 출처를 검사한다. 문제가 하나라도 있으면 그 표로 계산하지 않는다.
 * 계수 조합이 맞는지(어느 연료에 어느 계수인지)는 보지 않는다. 그것은 표를 만드는 쪽의 일이다.
 */
export function validateTables(input: unknown): { ok: true; tables: ReferenceTables } | { ok: false; problems: TableProblem[] } {
  const problems: TableProblem[] = [];
  if (!isObject(input)) return { ok: false, problems: [{ where: "$", problem: "객체가 아니다" }] };

  for (const name of ["unitConversions", "factors", "gwpSets"] as const) {
    if (!Array.isArray(input[name])) problems.push({ where: name, problem: "배열이 없다" });
  }
  if (problems.length > 0) return { ok: false, problems };

  (input.unitConversions as unknown[]).forEach((c, i) => {
    const where = `unitConversions[${i}]`;
    if (!isObject(c)) return void problems.push({ where, problem: "객체가 아니다" });
    if (!isText(c.from) || !isText(c.to)) problems.push({ where, problem: "from/to 가 없다" });
    checkDecimal(c.factor, `${where}.factor`, problems);
    checkSource(c.source, where, problems);
  });

  (input.gwpSets as unknown[]).forEach((s, i) => {
    const where = `gwpSets[${i}]`;
    if (!isObject(s)) return void problems.push({ where, problem: "객체가 아니다" });
    if (!isText(s.id) || !isText(s.label)) problems.push({ where, problem: "id/label 이 없다" });
    checkSource(s.source, where, problems);
    if (!isObject(s.values)) return void problems.push({ where, problem: "values 가 없다" });
    for (const [gas, value] of Object.entries(s.values)) checkDecimal(value, `${where}.values.${gas}`, problems);
  });

  (input.factors as unknown[]).forEach((f, i) => {
    const where = `factors[${i}]`;
    if (!isObject(f)) return void problems.push({ where, problem: "객체가 아니다" });
    if (!isText(f.id) || !isText(f.version)) problems.push({ where, problem: "id/version 이 없다" });
    checkSource(f.source, where, problems);
    if (!Array.isArray(f.emissionFactors)) return void problems.push({ where, problem: "emissionFactors 가 없다" });
    f.emissionFactors.forEach((e: unknown, j: number) => {
      if (!isObject(e)) return void problems.push({ where: `${where}.emissionFactors[${j}]`, problem: "객체가 아니다" });
      checkDecimal(e.value, `${where}.emissionFactors[${j}].value`, problems);
      if (!isText(e.gas) || !isText(e.massUnit)) problems.push({ where: `${where}.emissionFactors[${j}]`, problem: "gas/massUnit 이 없다" });
    });
    if (f.kind === "combustion") {
      if (!isText(f.fuel)) problems.push({ where, problem: "fuel 이 없다" });
      if (f.combustionType !== "stationary" && f.combustionType !== "mobile") problems.push({ where, problem: "combustionType 이 stationary/mobile 이 아니다" });
      if (!isObject(f.calorific)) return void problems.push({ where, problem: "calorific 이 없다" });
      checkDecimal(f.calorific.value, `${where}.calorific.value`, problems);
      if (f.calorific.basis !== "net" && f.calorific.basis !== "gross") problems.push({ where, problem: "calorific.basis 가 net/gross 가 아니다" });
      if (!isText(f.calorific.energyUnit) || !isText(f.calorific.perUnit)) problems.push({ where, problem: "calorific 단위가 없다" });
      if (f.oxidationFactor !== undefined) checkDecimal(f.oxidationFactor, `${where}.oxidationFactor`, problems);
      if (f.sector !== undefined && !isText(f.sector)) problems.push({ where, problem: "sector 가 빈 문자열이거나 문자열이 아니다" });
      if (f.facilityUse !== undefined && f.facilityUse !== "power_generation" && f.facilityUse !== "other") {
        problems.push({ where, problem: "facilityUse 가 power_generation/other 가 아니다" });
      }
      if (f.parameterTiers !== undefined) checkTiers(f.parameterTiers, f.oxidationFactor !== undefined, `${where}.parameterTiers`, problems);
    } else if (f.kind === "purchased_energy") {
      if (f.energyType !== "electricity" && f.energyType !== "heat") problems.push({ where, problem: "energyType 이 electricity/heat 가 아니다" });
      if (!isText(f.perUnit)) problems.push({ where, problem: "perUnit 이 없다" });
    } else {
      problems.push({ where, problem: `알 수 없는 계수 종류: ${String(f.kind)}` });
    }
  });

  if (problems.length > 0) return { ok: false, problems };
  return { ok: true, tables: input as unknown as ReferenceTables };
}

/** 여러 표를 이어 붙인다. 중복 id 는 여기서 걸러내지 않는다 (계산 때 ambiguous 로 드러난다). */
export function mergeTables(...parts: ReadonlyArray<Partial<ReferenceTables>>): ReferenceTables {
  return {
    unitConversions: parts.flatMap((p) => p.unitConversions ?? []),
    factors: parts.flatMap((p) => p.factors ?? []),
    gwpSets: parts.flatMap((p) => p.gwpSets ?? []),
  };
}
