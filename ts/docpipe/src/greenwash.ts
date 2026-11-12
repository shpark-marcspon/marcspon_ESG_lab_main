import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { z } from "zod";
import type { CallRequest, LlmClient, Tags } from "llm";
import type { Chunk } from "./chunk.js";
import type { ParsedDocument } from "./document.js";
import { describeLocation, failureOf, renderItems } from "./llm-work.js";
import { dataDir } from "./paths.js";
import type { WorkResult } from "./runner.js";
import { planUnits, type SelectOptions, type TextUnit } from "./select.js";

export const GREENWASH_PROMPT = "docpipe/greenwash@1";

const checklistSchema = z.object({
  synthetic: z.boolean(),
  /** 실제 판정 기준이 정해지지 않았음을 적는 자리. 가상 체크리스트에는 반드시 있어야 한다. */
  decisionPending: z.string().optional(),
  note: z.string(),
  sectionHitMinChars: z.number().int().nonnegative(),
  sectionHitMinCharsBasis: z.string(),
  sectionTitleKeywords: z.array(z.string().min(1)),
  claimKeywords: z.array(z.string().min(1)).min(1),
  types: z.array(z.object({ id: z.string().regex(/^[a-z_]+$/), label: z.string(), description: z.string(), synthetic: z.boolean().optional() })).min(1),
});

export type Checklist = z.infer<typeof checklistSchema> & { path: string; sha256: string };

export class ChecklistError extends Error {
  override name = "ChecklistError";
}

export const defaultChecklistPath = join(dataDir, "greenwash-checklist.synthetic.json");

export function parseChecklist(raw: unknown, path: string, source: string): Checklist {
  const parsed = checklistSchema.safeParse(raw);
  if (!parsed.success) throw new ChecklistError(`${path}: ${z.prettifyError(parsed.error)}`);
  if (parsed.data.synthetic) {
    if (!parsed.data.decisionPending) throw new ChecklistError(`${path}: 가상 체크리스트에 decisionPending 이 없다`);
    for (const type of parsed.data.types) {
      if (type.synthetic !== true) throw new ChecklistError(`${path}: 가상 체크리스트의 유형 ${type.id} 에 synthetic 표시가 없다`);
    }
  }
  return { ...parsed.data, path, sha256: createHash("sha256").update(source).digest("hex") };
}

export function loadChecklist(path: string = defaultChecklistPath): Checklist {
  const source = readFileSync(path, "utf8");
  return parseChecklist(JSON.parse(source), path, source);
}

export function renderChecklist(checklist: Checklist): string {
  return checklist.types.map((type) => `- ${type.id}: ${type.label}. ${type.description}`).join("\n");
}

export interface ClaimSelection {
  units: TextUnit[];
  /** 문단 블록 중 LLM 에 보낼 수 있는 것 (장식, 숫자, 로마자만 있는 것을 뺀 뒤). 걸러 내기의 분모다. */
  paragraphsConsidered: number;
  byKeyword: number;
  bySectionTitleOnly: number;
}

/**
 * 환경 주장 문단만 먼저 걸러 낸다 (LLM 없이). 표와 제목은 보지 않는다.
 * - 문단에 주장 키워드가 있으면 넣는다.
 * - 섹션 제목에 환경 키워드가 있고 문단이 `sectionHitMinChars` 이상이면 넣는다. 섹션 제목은 쪽 상단 큰 글자로
 *   추정한 것이라(`활동`, `Our`) 제목만으로는 걸러 내지 못하는 섹션이 있다. 그래서 키워드가 주 기준이다.
 */
export function selectClaimUnits(document: ParsedDocument, checklist: Checklist, options: SelectOptions): ClaimSelection {
  const titles = new Map(document.sections.map((section) => [section.id, (section.title ?? "").toLowerCase()]));
  const sectionKeywords = checklist.sectionTitleKeywords.map((keyword) => keyword.toLowerCase());
  const selection: ClaimSelection = { units: [], paragraphsConsidered: 0, byKeyword: 0, bySectionTitleOnly: 0 };
  for (const unit of planUnits(document, options).units) {
    if (unit.type !== "text" || unit.blockKind !== "paragraph") continue;
    selection.paragraphsConsidered += 1;
    const text = unit.items[0].text;
    const title = unit.sectionId === null ? "" : (titles.get(unit.sectionId) ?? "");
    if (checklist.claimKeywords.some((keyword) => text.includes(keyword))) {
      selection.byKeyword += 1;
      selection.units.push(unit);
    } else if (text.length >= checklist.sectionHitMinChars && sectionKeywords.some((keyword) => title.includes(keyword))) {
      selection.bySectionTitleOnly += 1;
      selection.units.push(unit);
    }
  }
  return selection;
}

export interface Finding {
  blockId: string;
  /** 의심 문장. 원문에서 따온 것이어야 한다. */
  sentence: string;
  type: string;
  reason: string;
  /** 대체 표현 */
  suggestion: string;
  /** `sentence` 가 그 블록의 원문에 글자 그대로 있는가 (공백 차이는 무시). 없으면 모델이 문장을 고쳐 쓴 것이다. */
  quoteVerified: boolean;
}

export interface GreenwashOutput {
  findings: Finding[];
  /** 조각에 없는 블록 id 를 가리켜 버린 판정 수 */
  droppedUnknownBlock: number;
}

export interface GreenwashContext {
  client: LlmClient;
  route: string;
  tags: Tags;
  document: ParsedDocument;
  checklist: Checklist;
}

function outputSchema(checklist: Checklist) {
  const typeIds = checklist.types.map((type) => type.id) as [string, ...string[]];
  return z.object({
    findings: z.array(
      z.object({ block_id: z.string(), sentence: z.string().min(1), type: z.enum(typeIds), reason: z.string(), suggestion: z.string() }),
    ),
  });
}

export function greenwashRequest(chunk: Chunk, context: Omit<GreenwashContext, "client">): CallRequest<z.infer<ReturnType<typeof outputSchema>>> {
  return {
    prompt: GREENWASH_PROMPT,
    vars: {
      checklist_label: context.checklist.synthetic ? "synthetic 예시, 실제 기준은 결정 대기" : "확정 기준",
      checklist: renderChecklist(context.checklist),
      document: context.document.source.file_name,
      location: describeLocation(chunk, context.document.sections),
      items: renderItems(chunk.items),
    },
    route: context.route,
    tags: context.tags,
    schema: outputSchema(context.checklist),
    schemaName: "greenwash_findings",
  };
}

const squeeze = (text: string): string => text.replace(/\s+/g, "");

export async function reviewChunk(chunk: Chunk, context: GreenwashContext): Promise<WorkResult<GreenwashOutput>> {
  const result = await context.client.call(greenwashRequest(chunk, context));
  const costUsd = result.meta.totalCostUsd;
  const warnings = result.meta.warnings.map((warning) => warning.code as string);
  if (result.kind !== "success") return { ok: false, error: failureOf(result), costUsd, warnings };

  const sources = new Map(chunk.items.map((item) => [item.id, item.text]));
  const findings: Finding[] = [];
  let droppedUnknownBlock = 0;
  for (const finding of result.data.findings) {
    const source = sources.get(finding.block_id);
    if (source === undefined) {
      droppedUnknownBlock += 1;
      continue;
    }
    findings.push({
      blockId: finding.block_id,
      sentence: finding.sentence,
      type: finding.type,
      reason: finding.reason,
      suggestion: finding.suggestion,
      quoteVerified: squeeze(source).includes(squeeze(finding.sentence)),
    });
  }
  if (droppedUnknownBlock > 0) warnings.push("unknown_block_id");
  if (findings.some((finding) => !finding.quoteVerified)) warnings.push("quote_not_verbatim");
  return { ok: true, output: { findings, droppedUnknownBlock }, costUsd, warnings };
}
