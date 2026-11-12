import type { ProviderRequest, ProviderResponse, TokenEstimator, Usage } from "llm";
import type { Extraction, Fact } from "../domain/facts.js";
import { UNSPECIFIED_BOUNDARY, type Catalog, type RawLabel } from "../domain/label.js";
import { headerKey, headerPaths } from "../domain/memory.js";
import { resolveUnit, type UnitTable } from "../domain/units.js";

/**
 * `--provider mock` 으로 끝까지 돌려 보기 위한 synthetic 응답기다. 모델이 아니다.
 * 머리글 낱말이 기준 목록의 「다른 이름」과 글자로 같으면 그 지표라고 답한다. 뜻을 이해하지 않는다.
 * 여기서 나온 일치·불일치 수는 모델의 성능과 아무 관계가 없다.
 *
 * 요청에서 읽는 것은 문서와 표의 ID, 답할 대상뿐이고, 머리글은 코드가 뽑아 둔 사실 후보에서 본다
 * (요청에 실린 것과 같은 글자다).
 */

type Responder = (request: ProviderRequest) => ProviderResponse;

interface TablePayload {
  kind: "table";
  doc: string;
  table_id: string;
  targets: string[];
}

interface BlocksPayload {
  kind: "blocks";
  doc: string;
  targets: { block: string; quote: string; occurrence: number }[];
}

function payloadOf(request: ProviderRequest): TablePayload | BlocksPayload {
  const text = request.user.map((part) => (part.type === "text" ? part.text : "")).join("\n");
  const at = text.indexOf("\n{");
  if (at === -1) throw new Error("모의 응답기: 요청에서 입력 JSON 을 찾지 못했다");
  return JSON.parse(text.slice(at + 1)) as TablePayload | BlocksPayload;
}

const stripUnitNote = (text: string): string => text.replace(/\((?:단위\s*[:：]\s*)?[^()]*\)$/u, (match) => (/단위|CO2|TJ|Wh|원|톤|명|%/.test(match) && !/Scope/.test(match) ? "" : match)).trim();
const aliasKey = (alias: string): string => alias.split(">").map((part) => headerKey(part.trim())).join(">");

/** 머리글 낱말로 지표를 정하는 결정적 응답기. */
export function syntheticLabeler(catalog: Catalog, units: UnitTable, extractions: Extraction[], estimator: TokenEstimator): Responder {
  const facts = new Map<string, Fact>();
  const unitLines = new Map<string, string[]>();
  for (const extraction of extractions) {
    for (const fact of extraction.facts) facts.set(`${extraction.docId}/${fact.id}`, fact);
    for (const grid of extraction.grids) unitLines.set(`${extraction.docId}/${grid.id}`, grid.unitLines);
  }
  const aliases = catalog.indicators.flatMap((indicator) => indicator.aliases.map((alias) => ({ key: aliasKey(alias), indicator }))).sort((a, b) => b.key.length - a.key.length);
  const boundaries = catalog.boundaries.flatMap((boundary) => boundary.aliases.map((alias) => ({ key: headerKey(alias), boundary: boundary.key })));

  const labelCell = (fact: Fact, target: string, gridKey: string): RawLabel => {
    const base = { target, quote: null, occurrence: null, free_text: null, basis_note: null } as const;
    const paths = headerPaths(fact);
    const evidence = [...(fact.headers?.row ?? []), ...(fact.headers?.col ?? [])].map((ref) => ref.cell);
    const texts = [...(fact.headers?.row ?? []), ...(fact.headers?.col ?? [])].map((ref) => ref.text);
    const cells = texts.map((text) => headerKey(stripUnitNote(text)));
    const joined = (paths?.rowPath ?? []).join(">");
    const hit = aliases.find((alias) => (alias.key.includes(">") ? joined.includes(alias.key) : cells.includes(alias.key)));
    if (!hit) return { ...base, kind: "not_in_catalog", indicator_key: null, free_text: `[mock synthetic] ${texts.join(" / ")}`.slice(0, 120), period: null, unit: null, boundary: null, confidence: "low", evidence };
    // 단위: 머리글 칸이 단위 표에 있는 글자면 그것, 없으면 머리글의 괄호 속, 그다음 단위 줄.
    const candidates = [...texts, ...texts.map((text) => /\((?:단위\s*[:：]\s*)?([^()]+)\)$/u.exec(text)?.[1] ?? ""), ...(unitLines.get(gridKey) ?? [])];
    const unit = fact.unitSuffix ?? candidates.find((text) => text !== "" && !/^(19|20)\d{2}$/.test(text) && resolveUnit(text, units) !== null) ?? null;
    const boundary = boundaries.find((item) => cells.includes(item.key))?.boundary ?? UNSPECIFIED_BOUNDARY;
    const period = paths && paths.years.length === 1 ? (paths.years[0] as string) : null;
    return { ...base, kind: "indicator", indicator_key: hit.indicator.key, period, unit, boundary, confidence: "high", evidence };
  };

  return (request) => {
    const payload = payloadOf(request);
    const labels: RawLabel[] =
      payload.kind === "table"
        ? payload.targets.flatMap((target) => {
            const fact = facts.get(`${payload.doc}/${payload.table_id}:${target}`);
            return fact ? [labelCell(fact, target, `${payload.doc}/${payload.table_id}`)] : [];
          })
        : payload.targets.map((target) => ({
            target: target.block,
            quote: target.quote,
            occurrence: target.occurrence,
            kind: "not_in_catalog" as const,
            indicator_key: null,
            free_text: "[mock synthetic] 문장 속 숫자",
            period: null,
            unit: null,
            boundary: null,
            basis_note: null,
            confidence: "low" as const,
            evidence: [target.block],
          }));
    const output = JSON.stringify({ labels });
    const input = [...request.system.map((segment) => segment.text), ...request.user.map((part) => (part.type === "text" ? part.text : ""))].join("\n");
    const usage: Usage = { inputTokens: estimator.estimate(input), outputTokens: estimator.estimate(output), cacheReadTokens: 0, cacheWriteTokens: 0 };
    return { kind: "completed", text: output, usage };
  };
}
