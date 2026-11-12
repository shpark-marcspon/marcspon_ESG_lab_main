import type { InputRow } from "./types.js";
import { activityDim } from "./units.js";

/**
 * 품목명을 묶음 키로 바꾼다. 공백, 대소문자, 전각·반각, 괄호와 구두점 같은 규격 표기 차이를 지운다.
 * 예: "A4 용지 80g", "a4용지 80G", "Ａ４용지(80g)" → "a4용지80g".
 * 글자와 숫자가 하나도 남지 않으면 빈 문자열이다 (묶지 않는다).
 */
export function normalizeItemName(name: string): string {
  return name
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[×✕*]/g, "x")
    .replace(/[^\p{L}\p{N}.]/gu, "")
    .replace(/\.(?!\d)/g, "")
    .replace(/(?<!\d)\./g, "");
}

/**
 * 같은 판단을 공유할 행의 키. 품목명 키에 단위 차원과 운송 수단을 붙인다.
 * 단위가 다르면 맞는 계수가 달라지므로 같은 품목이라도 따로 묶는다.
 */
export function groupKey(row: Pick<InputRow, "itemName" | "unit" | "transport">): string {
  const name = normalizeItemName(row.itemName);
  const dim = activityDim(row);
  const unit = dim === "unknown" ? `?${normalizeItemName(row.unit ?? "")}` : dim;
  const mode = row.transport ? normalizeItemName(row.transport.mode) : "";
  return [name, unit, mode].join("|");
}

export interface RowGroup {
  key: string;
  rows: InputRow[];
}

/** 행을 묶음 키로 묶는다. 묶음과 묶음 안 행의 순서는 입력 순서를 따른다. 품목명 키가 비면 행 하나가 한 묶음이다. */
export function groupRows(rows: readonly InputRow[]): RowGroup[] {
  const groups = new Map<string, RowGroup>();
  for (const row of rows) {
    const base = groupKey(row);
    const key = normalizeItemName(row.itemName) === "" ? `${base}#${row.rowId}` : base;
    const group = groups.get(key);
    if (group) group.rows.push(row);
    else groups.set(key, { key, rows: [row] });
  }
  return [...groups.values()];
}
