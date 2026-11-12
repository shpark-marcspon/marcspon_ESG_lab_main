import type { InputRow } from "./types.js";

// 단위의 차원과 환산. 환산 계수는 정의상 정확한 값(1 t = 1000 kg)뿐이고 여기 말고는 두지 않는다.
// 차원이 다르면 환산하지 않는다 (예: 개수 → 무게). 그런 행은 활동 기반 후보가 없는 것으로 보고 지출 기반으로 넘긴다.

export type UnitDim =
  | "kg"
  | "tonne"
  | "km"
  | "mile"
  | "passenger_km"
  | "tonne_km"
  | "room_night"
  | "fte_hour"
  | "kwh"
  | "mj"
  | "m3"
  | "megalitre"
  | "m2"
  | "ea"
  | "usd_2022_purchaser"
  | "unknown";

const ALIASES: Record<Exclude<UnitDim, "unknown">, string[]> = {
  kg: ["kg", "킬로그램", "키로"],
  tonne: ["t", "ton", "tons", "tonne", "tonnes", "톤"],
  km: ["km", "킬로미터"],
  mile: ["mile", "miles", "마일"],
  passenger_km: ["passenger.km", "pkm", "인km", "인·km", "인.km"],
  tonne_km: ["tonne.km", "ton.km", "tkm", "톤km", "톤·km", "톤.km"],
  room_night: ["room per night", "room night", "박", "객실박"],
  fte_hour: ["per fte working hour", "fte시간", "fte hour"],
  kwh: ["kwh"],
  mj: ["mj"],
  m3: ["m3", "㎥", "m³", "cubic metres", "세제곱미터"],
  megalitre: ["million litres"],
  m2: ["m2", "㎡", "m²"],
  ea: ["ea", "개"],
  usd_2022_purchaser: ["2022 usd, purchaser price"],
};

const LOOKUP = new Map<string, UnitDim>();
for (const [dim, names] of Object.entries(ALIASES)) {
  for (const name of names) LOOKUP.set(name, dim as UnitDim);
}

/** 단위 표기를 차원으로 바꾼다. 모르는 표기(대, 식, 건, box 등)는 "unknown" 이다. */
export function unitDim(text: string | null): UnitDim {
  if (text === null) return "unknown";
  return LOOKUP.get(text.normalize("NFKC").trim().toLowerCase()) ?? "unknown";
}

/** `from` 단위의 양에 곱하면 `to` 단위의 양이 되는 계수(십진 문자열). 환산할 수 없으면 null. */
export function conversionFactor(from: UnitDim, to: UnitDim): string | null {
  if (from === "unknown" || to === "unknown") return null;
  if (from === to) return "1";
  if (from === "kg" && to === "tonne") return "0.001";
  if (from === "tonne" && to === "kg") return "1000";
  return null;
}

/** 행의 활동량이 어떤 단위 차원인지. 운송 행에 무게가 있으면 톤·km 이다. 숫자를 읽지 않는다. */
export function activityDim(row: Pick<InputRow, "unit" | "transport">): UnitDim {
  if (row.transport && row.transport.weightKg !== null) return "tonne_km";
  return unitDim(row.unit);
}
