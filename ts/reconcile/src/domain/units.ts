// 단위 읽기와 환산. 환산은 정의상 10 의 거듭제곱인 것만 있다 (수 단위 낱말, SI 접두어).
// 표의 정본은 `data/reconcile/units.json` 이고 여기에는 수치가 없다.

export interface UnitDef {
  symbol: string;
  family: string;
  /** 그 계열의 기준 단위 대비 10 의 지수. `kg` 는 기준이 `t` 일 때 -3. */
  exp: number;
  aliases: string[];
  /** 수 단위 낱말을 띄어쓰기 없이 붙여 써도 단위로 읽는가 (`억원`, `천톤`). 꺼져 있으면 띄어 쓴 꼴(`천 명`)만 읽는다: `조건`, `조명` 이 단위로 읽히지 않게. */
  compactScale: boolean;
}

export interface UnitTable {
  /** 수 단위 낱말 → 10 의 지수 (`천` → 3, `억` → 8). */
  scaleWords: Record<string, number>;
  units: UnitDef[];
}

export interface ResolvedUnit {
  /** 원문에서 읽은 단위 글자 (정규화 뒤). */
  text: string;
  family: string;
  /** 기준 단위 대비 10 의 지수 (수 단위 낱말 포함). */
  exp: number;
  symbol: string;
  scaleWord: string | null;
}

export function normalizeUnitText(text: string): string {
  return text
    .normalize("NFKC")
    .replace(/^\(?\s*단위\s*[:：]\s*/u, "")
    .replace(/[()\s]/g, "")
    .replace(/₂/g, "2");
}

/** 단위 글자를 계열과 지수로 읽는다. 표에 없으면 null (지어내지 않는다). */
export function resolveUnit(text: string, table: UnitTable): ResolvedUnit | null {
  const normalized = normalizeUnitText(text);
  if (normalized === "") return null;
  const names = table.units.flatMap((unit) => [unit.symbol, ...unit.aliases].map((name) => ({ name: normalizeUnitText(name), unit })));
  const exact = names.find((entry) => entry.name === normalized);
  if (exact) return { text: normalized, family: exact.unit.family, exp: exact.unit.exp, symbol: exact.unit.symbol, scaleWord: null };
  // 수 단위 낱말 + 단위 (`천 톤`, `억원`). 긴 낱말부터 본다.
  const spaced = text.normalize("NFKC").replace(/^\(?\s*단위\s*[:：]\s*/u, "").replace(/[()]/g, "").trim();
  const words = Object.keys(table.scaleWords).sort((a, b) => b.length - a.length);
  for (const word of words) {
    if (!normalized.startsWith(word)) continue;
    const rest = normalized.slice(word.length);
    const hit = names.find((entry) => entry.name === rest);
    if (hit && !hit.unit.compactScale && !new RegExp(`^${word}\\s`, "u").test(spaced)) continue;
    if (hit) return { text: normalized, family: hit.unit.family, exp: hit.unit.exp + (table.scaleWords[word] as number), symbol: hit.unit.symbol, scaleWord: word };
  }
  return null;
}
