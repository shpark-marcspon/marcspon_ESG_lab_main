import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Ajv2020 } from "ajv/dist/2020.js";
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { documentSchema } from "../src/document.js";
import { parseDocument } from "../src/index.js";
// 대조 함수는 scope3 의 계약 테스트와 같은 것을 쓴다 (사본을 따로 두지 않는다).
import { contradictions } from "../../scope3/test/schemaContract.js";

// 계약 테스트: 문서 모델의 정본(`schemas/document.schema.json`, 밴드 A 소유)과 docpipe 가 읽는 쪽 정의(`src/document.ts` 의 zod 사본)가
// 어긋나면 빨개진다. 어긋남을 찾으면 여기서 고치지 않는다.
//
// 두 가지로 본다.
// 1. 구조 대조: zod 사본(`documentSchema`)을 JSON 스키마로 바꿔 정본과 견준다 (scope3 의 계수 계약 테스트와 같은 `contradictions`).
//    읽는 필드가 정본에 없거나, 정본에서 빠질 수 있는 필드를 필수로 읽거나, 정본이 낼 수 있는 타입·열거값을 받지 않으면 빨개진다.
// 2. 두드려 보기: 정본이 허용하는 문서(Ajv 로 확인)는 무엇이든 `parseDocument` 가 받아야 한다. 정본 쪽에서 문서를 조금씩 바꿔
//    (속성 하나 빼기, 열거값 바꾸기, null 넣기, 정수가 아닌 수 넣기) 정본이 여전히 허용하는 것만 골라 넣는다.
// 저장소 안에 밴드 A 가 실제로 낸 문서 JSON 은 없다 (`out/` 은 git 밖). 표본은 아래의 가상 문서(synthetic) 하나다.
//
// 정본 대신 다른 파일로 돌려 보려면 DOCUMENT_SCHEMA_PATH 를 준다 (심은 파손 확인용. `schemas/` 원본은 고치지 않는다):
//   DOCUMENT_SCHEMA_PATH=/tmp/document.schema.copy.json pnpm -F docpipe exec vitest run test/schema-contract.test.ts
// 사본에서 속성을 통째로 지우면 어느 필드든 빨개진다 (표본이 사본을 통과하지 못한다).
// `required` 목록에서만 지우면 docpipe 가 필수로 읽는 필드일 때 빨개진다. 읽지 않는 필드(쪽의 width 등)는 계약이 없다.

type Json = Record<string, unknown>;

const here = dirname(fileURLToPath(import.meta.url));
const schemaPath = process.env.DOCUMENT_SCHEMA_PATH ?? join(here, "..", "..", "..", "schemas", "document.schema.json");
const schema = JSON.parse(readFileSync(schemaPath, "utf8")) as Json;
const validate = new Ajv2020({ allErrors: true, strict: false }).compile(schema);
const valid = (document: unknown): boolean => validate(document);

/** 정본의 속성을 하나도 빠뜨리지 않은 가상 문서 (synthetic). 어느 보고서의 내용도 아니다. */
function sampleDocument(): Json {
  const bbox = [0, 0, 100.5, 20.25];
  return {
    schema_version: "0.1",
    source: { file_name: "synthetic_contract.pdf", sha256: "a".repeat(64), page_count: 3 },
    parser: { name: "docparse", version: "0.1.0", table_strategy: "auto", llm_used: false, ocr_used: false },
    pages: [
      { page: 1, width: 595.5, height: 842.25, text_layer: "present", char_count: 120, image_coverage: 0.1, columns: 1, notes: [] },
      { page: 2, width: 595.5, height: 842.25, text_layer: "none", char_count: 0, image_coverage: 1, columns: 0, notes: ["텍스트 계층 없음 (synthetic)"] },
    ],
    sections: [
      { id: "s1", title: "가상 개요 (synthetic)", level: 1, page_start: 1, page_end: 1, basis: "pdf_outline" },
      { id: "s2", title: null, level: 1, page_start: 2, page_end: 2, basis: "no_title_found" },
    ],
    blocks: [
      { id: "b1", kind: "heading", page: 1, bbox, order: 1, section_id: "s1", text: "가상 개요 (synthetic)", font_size: 18.5 },
      { id: "b2", kind: "paragraph", page: 1, bbox, order: 2, section_id: null, text: "가상 문단이다 (synthetic)." },
      { id: "b3", kind: "table", page: 1, bbox, order: 3, section_id: "s1", table_id: "t1" },
      { id: "b4", kind: "table", page: 1, bbox, order: 4, section_id: "s1", table_id: "t2" },
      { id: "b5", kind: "table", page: 1, bbox, order: 5, section_id: "s1", table_id: "t3" },
    ],
    tables: [
      { id: "t1", page: 1, bbox, n_rows: 2, n_cols: 2, cells: [["구분", "값"], ["가상 항목", "1"]], header: { rows: [0], cols: [0], basis: "rows_before_first_numeric_row" }, method: "lines", status: "ok", notes: [], footnote_marks: [{ row: 1, col: 1, mark: "1)" }] },
      { id: "t2", page: 1, bbox, n_rows: 2, n_cols: 1, cells: [["가"], ["나"]], header: { rows: [], cols: [], basis: "no_numeric_row" }, method: "text", status: "low_confidence", notes: ["열 경계가 불확실하다 (synthetic)"], footnote_marks: [] },
      { id: "t3", page: 1, bbox, n_rows: 0, n_cols: 0, cells: [], header: { rows: [], cols: [], basis: "empty" }, method: "none", status: "failed", notes: [], footnote_marks: [] },
    ],
  };
}

function deref(node: Json): Json {
  let current = node;
  while (typeof current.$ref === "string") {
    let target: unknown = schema;
    for (const part of current.$ref.slice(2).split("/")) target = (target as Json)[part];
    current = target as Json;
  }
  return current;
}

type Path = (string | number)[];

interface Mutation {
  label: string;
  path: Path;
  apply: (parent: Record<string | number, unknown>, key: string | number) => void;
}

/** 정본 스키마와 표본을 같이 걸으며 바꿔 볼 자리를 모은다. `seen` 에는 표본이 실제로 담은 속성 경로가 쌓인다. */
function walk(node: Json, value: unknown, path: Path, mutations: Mutation[], seen: Set<string>, where: string): void {
  const spec = deref(node);
  const types = Array.isArray(spec.type) ? (spec.type as string[]) : typeof spec.type === "string" ? [spec.type] : [];
  const at = path.join(".");
  if (types.includes("null") && value !== null) mutations.push({ label: `${at} 에 null`, path, apply: (parent, key) => (parent[key] = null) });
  if (types.includes("number") && typeof value === "number") mutations.push({ label: `${at} 에 정수가 아닌 수`, path, apply: (parent, key) => (parent[key] = 1.5) });
  for (const option of (spec.enum as unknown[] | undefined) ?? []) {
    if (option !== value) mutations.push({ label: `${at} 를 ${JSON.stringify(option)} 로`, path, apply: (parent, key) => (parent[key] = option) });
  }
  if (Array.isArray(value) && spec.items) value.forEach((item, index) => walk(spec.items as Json, item, [...path, index], mutations, seen, `${where}[]`));
  if (value !== null && typeof value === "object" && !Array.isArray(value) && spec.properties) {
    for (const [key, property] of Object.entries(spec.properties as Record<string, Json>)) {
      if (!(key in value)) continue;
      seen.add(`${where}.${key}`);
      mutations.push({ label: `${at === "" ? key : `${at}.${key}`} 빼기`, path: [...path, key], apply: (parent, name) => delete parent[name] });
      walk(property, (value as Json)[key], [...path, key], mutations, seen, `${where}.${key}`);
    }
  }
}

/** 정본이 정의한 속성 경로 전부 (배열은 `[]`). */
function schemaPaths(node: Json, where: string, out: string[] = []): string[] {
  const spec = deref(node);
  for (const [key, property] of Object.entries((spec.properties ?? {}) as Record<string, Json>)) {
    out.push(`${where}.${key}`);
    schemaPaths(property, `${where}.${key}`, out);
  }
  if (spec.items) schemaPaths(spec.items as Json, `${where}[]`, out);
  return out;
}

function mutated(mutation: Mutation): Json {
  const copy = structuredClone(sampleDocument());
  let parent: Record<string | number, unknown> = copy;
  for (const step of mutation.path.slice(0, -1)) parent = parent[step] as Record<string | number, unknown>;
  mutation.apply(parent, mutation.path.at(-1) as string | number);
  return copy;
}

/** 읽은 결과에 남은 속성 경로. zod 는 모르는 속성을 버리므로 이것이 docpipe 가 읽는 필드다. */
function keptPaths(value: unknown, where: string, out = new Set<string>()): Set<string> {
  if (Array.isArray(value)) for (const item of value) keptPaths(item, `${where}[]`, out);
  else if (value !== null && typeof value === "object") {
    for (const [key, inner] of Object.entries(value)) {
      out.add(`${where}.${key}`);
      keptPaths(inner, `${where}.${key}`, out);
    }
  }
  return out;
}

const mutations: Mutation[] = [];
const seen = new Set<string>();
walk(schema, sampleDocument(), [], mutations, seen, "$");

describe("문서 모델 계약 (schemas/document.schema.json ↔ docpipe)", () => {
  it("가상 표본이 정본 스키마를 통과한다", () => {
    validate(sampleDocument());
    expect((validate.errors ?? []).map((error) => `${error.instancePath} ${error.message}`)).toEqual([]);
  });

  it("표본이 정본의 속성을 하나도 빠뜨리지 않았다 (정본에 속성이 늘면 여기서 드러난다)", () => {
    expect(schemaPaths(schema, "$").filter((path) => !seen.has(path))).toEqual([]);
  });

  it("docpipe 가 표본을 읽고, 읽는 필드는 전부 정본에 있다", () => {
    const kept = keptPaths(parseDocument(sampleDocument()), "$");
    const defined = new Set(schemaPaths(schema, "$"));
    expect(kept.size).toBeGreaterThanOrEqual(30);
    expect([...kept].filter((path) => !defined.has(path))).toEqual([]);
  });

  it("docpipe 가 읽는 필드는 정본에 전부 있고 타입과 필수 여부가 모순되지 않는다 (구조 대조)", () => {
    const reader = z.toJSONSchema(documentSchema) as Json;
    expect(Object.keys(reader.properties as Json).sort()).toEqual(["blocks", "pages", "parser", "schema_version", "sections", "source", "tables"]);
    expect(contradictions(reader, schema)).toEqual([]);
  });

  it("구조 대조가 어긋남을 잡는다: 읽는 쪽에 정본에 없는 필드를 더하거나 선택 필드를 필수로 읽으면 모순이 나온다", () => {
    const renamed = z.toJSONSchema(documentSchema.extend({ page_total: z.number().int() })) as Json;
    expect(contradictions(renamed, schema)).toEqual(["$.page_total: 읽는 필드가 정본에 없다"]);
    const strictBlock = z.toJSONSchema(documentSchema.extend({ blocks: z.array(z.object({ id: z.string(), text: z.string() })) })) as Json;
    expect(contradictions(strictBlock, schema)).toEqual(["$.blocks[].text: 필수로 읽는데 정본에서는 빠질 수 있다"]);
  });

  it("정본이 허용하는 변형은 docpipe 도 받는다 (필수 여부, 열거값, null, 수의 종류)", () => {
    const allowed = mutations.filter((mutation) => valid(mutated(mutation)));
    // 바꿔 본 것 가운데 정본이 허용하는 것이 없으면 이 테스트는 아무것도 보지 않은 것이다.
    expect(allowed.length).toBeGreaterThanOrEqual(15);
    const rejected = allowed.flatMap((mutation) => {
      try {
        parseDocument(mutated(mutation));
        return [];
      } catch (error) {
        return [`${mutation.label}: ${(error as Error).message.split("\n").slice(0, 3).join(" / ")}`];
      }
    });
    expect(rejected).toEqual([]);
  });

  it("정본이 거부하는 변형이 있다 (변형이 실제로 정본을 건드린다)", () => {
    const refused = mutations.filter((mutation) => !valid(mutated(mutation))).map((mutation) => mutation.label);
    expect(refused).toContain("sections 빼기");
    expect(refused).toContain("blocks.1.text 빼기");
  });
});
