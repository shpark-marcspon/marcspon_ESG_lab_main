import { describe, expect, it } from "vitest";
import {
  ChecklistError,
  FileChunkStore,
  GlossaryError,
  loadChecklist,
  parseChecklist,
  parseGlossary,
  planGreenwash,
  reviewChunk,
  runGreenwash,
  selectClaimUnits,
  syntheticReviewer,
  type GreenwashOutput,
} from "../src/index.js";
import { TAGS, estimator, fixtureDocument, harness, itemsIn, parseDocumentWith, settings, tempDir } from "./helpers.js";

const checklist = loadChecklist();
const document = fixtureDocument();
const base = { document, checklist, settings, estimator, route: "mock", tags: TAGS };

describe("그린워싱 태깅", () => {
  it("체크리스트는 synthetic 이고 실제 기준이 결정 대기임을 싣고 있다", () => {
    expect(checklist.synthetic).toBe(true);
    expect(checklist.path).toMatch(/synthetic\.json$/);
    expect(checklist.decisionPending).toMatch(/결정 대기/);
    expect(checklist.types.every((type) => type.synthetic === true)).toBe(true);
  });

  it("synthetic 표시가 빠진 가상 기준과 가상 용어집은 거부한다", () => {
    const raw = { synthetic: true, decisionPending: "결정 대기", note: "", sectionHitMinChars: 0, sectionHitMinCharsBasis: "", sectionTitleKeywords: [], claimKeywords: ["친환경"], types: [{ id: "vague_claim", label: "", description: "" }] };
    expect(() => parseChecklist(raw, "x.json", "")).toThrow(ChecklistError);
    expect(() => parseChecklist({ ...raw, decisionPending: undefined, types: [{ ...raw.types[0], synthetic: true }] }, "x.json", "")).toThrow(/decisionPending/);
    const glossary = { synthetic: true, note: "", sourceLang: "ko", targetLang: "en", terms: [{ ko: "공급망", en: "supply chain" }] };
    expect(() => parseGlossary(glossary, "g.json", "")).toThrow(GlossaryError);
    expect(() => parseGlossary({ ...glossary, synthetic: false, terms: [{ ko: "공급망", en: "supply chain", synthetic: true }] }, "g.json", "")).toThrow(/synthetic 파일/);
  });

  it("환경 주장 문단만 먼저 걸러 낸다: 키워드가 있는 문단과 환경 섹션의 긴 문단, 표·제목·장식은 뺀다", () => {
    const custom = parseDocumentWith([
      { section: "기후변화 대응", kind: "heading", text: "친환경 제품" },
      { section: "기후변화 대응", kind: "paragraph", text: "당사는 친환경 소재만 사용합니다." },
      { section: "기후변화 대응", kind: "paragraph", text: "이 문단에는 키워드가 없지만 환경 섹션에 있고 길이가 충분해서 검토 대상에 들어가야 합니다." },
      { section: "기후변화 대응", kind: "paragraph", text: "짧은 도표 글자" },
      { section: "임직원", kind: "paragraph", text: "임직원 교육 시간을 늘렸고 다양한 복지 제도를 운영하고 있으며 채용을 확대했습니다." },
      { section: "임직원", kind: "paragraph", text: "사무실의 온실가스 감축 캠페인을 진행했습니다." },
    ]);
    const selection = selectClaimUnits(custom, checklist, settings);
    expect(selection.units.map((unit) => unit.items[0].text)).toEqual([
      "당사는 친환경 소재만 사용합니다.",
      "이 문단에는 키워드가 없지만 환경 섹션에 있고 길이가 충분해서 검토 대상에 들어가야 합니다.",
      "사무실의 온실가스 감축 캠페인을 진행했습니다.",
    ]);
    expect(selection).toMatchObject({ paragraphsConsidered: 5, byKeyword: 2, bySectionTitleOnly: 1 });
  });

  it("의심 문장·유형·대체 표현을 구조화 출력으로 받고, 원문에 없는 문장과 조각 밖 블록을 가려낸다", async () => {
    const { client, mock } = harness();
    const chunk = planGreenwash(base).chunks[0]!;
    const first = chunk.items[0]!;
    mock.enqueueText(
      JSON.stringify({
        findings: [
          { block_id: first.id, sentence: first.text.slice(0, 20), type: "no_evidence", reason: "근거 없음", suggestion: "수치를 밝힌다" },
          { block_id: first.id, sentence: "모델이 고쳐 쓴 문장", type: "vague_claim", reason: "모호", suggestion: "범위를 밝힌다" },
          { block_id: "p99-b1", sentence: "없는 블록", type: "vague_claim", reason: "", suggestion: "" },
        ],
      }),
    );
    const result = await reviewChunk(chunk, { client, route: "mock", tags: TAGS, document, checklist });
    expect(result.ok && result.output).toEqual({
      findings: [
        { blockId: first.id, sentence: first.text.slice(0, 20), type: "no_evidence", reason: "근거 없음", suggestion: "수치를 밝힌다", quoteVerified: true },
        { blockId: first.id, sentence: "모델이 고쳐 쓴 문장", type: "vague_claim", reason: "모호", suggestion: "범위를 밝힌다", quoteVerified: false },
      ],
      droppedUnknownBlock: 1,
    });
    expect(result.warnings).toEqual(expect.arrayContaining(["unknown_block_id", "quote_not_verbatim"]));
    expect(mock.calls[0]!.system[0]!.text).toContain("synthetic 예시, 실제 기준은 결정 대기");
    expect(mock.calls[0]!.jsonSchema?.name).toBe("greenwash_findings");
  });

  it("체크리스트에 없는 유형은 스키마 불일치로 실패한다", async () => {
    const { client, mock } = harness();
    const chunk = planGreenwash(base).chunks[0]!;
    for (let i = 0; i < 3; i++) mock.enqueueText(JSON.stringify({ findings: [{ block_id: chunk.items[0]!.id, sentence: "x", type: "지어낸_유형", reason: "", suggestion: "" }] }));
    expect(await reviewChunk(chunk, { client, route: "mock", tags: TAGS, document, checklist })).toMatchObject({ ok: false, error: { kind: "schema_mismatch" } });
  });

  it("모의 공급사 종단: 걸러 낸 문단만 조각으로 보내고, 의심 문장이 없다는 응답은 실패가 아니다", async () => {
    const { client, mock } = harness();
    const plan = planGreenwash({ ...base, budgetTokens: 400 });
    expect(plan.chunks.length).toBeGreaterThan(1);
    mock.enqueueText(JSON.stringify({ findings: [] }));
    for (let i = 1; i < plan.chunks.length; i++) mock.enqueue(syntheticReviewer(checklist, estimator));
    const outcome = await runGreenwash({ ...base, budgetTokens: 400, concurrency: 1, client, store: new FileChunkStore<GreenwashOutput>(tempDir()) });
    expect(outcome.run).toMatchObject({ done: plan.chunks.length, failed: 0 });
    expect(outcome.run.records[0]).toMatchObject({ status: "done", output: { findings: [] } });
    const sent = mock.calls.flatMap((request) => itemsIn(request).map((item) => item.id));
    expect(sent).toEqual(plan.selection.units.map((unit) => unit.id));
    expect(sent.some((id) => id.includes(":r"))).toBe(false);
    const findings = outcome.run.records.flatMap((record) => (record.status === "done" ? record.output.findings : []));
    expect(findings).toHaveLength(plan.chunks.length - 1);
    expect(findings.every((finding) => finding.quoteVerified && finding.type === checklist.types[0]!.id)).toBe(true);
  });
});
