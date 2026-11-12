import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Ajv2020 } from "ajv/dist/2020.js";
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { defaultFactorsPath, factorRecordSchema } from "../src/index.js";
import { contradictions } from "./schemaContract.js";

// 계약 테스트: 계수 레코드의 정본(`schemas/factor.schema.json`, 밴드 C 소유)과 scope3 가 손으로 베낀 부분 사본(`factorRecordSchema`)이 어긋나면 빨개진다.
// 어긋남을 찾으면 여기서 고치지 않는다. 정본과 계수 표는 다른 밴드의 것이다.
//
// 정본 대신 다른 파일로 돌려 보려면 FACTOR_SCHEMA_PATH 를 준다 (심은 파손 확인용. `schemas/` 원본은 고치지 않는다):
//   FACTOR_SCHEMA_PATH=/tmp/factor.schema.copy.json pnpm -F scope3 exec vitest run test/factor-contract.test.ts
// 사본에서 속성을 통째로 지우면 어느 필드든 빨개진다 (표본 검증 또는 「읽는 필드가 정본에 없다」).
// `required` 목록에서만 지우면 scope3 가 읽는 필드일 때 빨개진다 (「필수로 읽는데 정본에서는 빠질 수 있다」). 읽지 않는 필드는 계약이 없다.

type Json = Record<string, unknown>;

const here = dirname(fileURLToPath(import.meta.url));
const schemaPath = process.env.FACTOR_SCHEMA_PATH ?? join(here, "..", "..", "..", "schemas", "factor.schema.json");
const canonical = JSON.parse(readFileSync(schemaPath, "utf8")) as Json;

/** 출처 × 레코드 종류 묶음마다 고르는 수. */
const PER_GROUP = 20;

/**
 * 표본 규칙: 계수 표의 레코드를 (source.id, record_type) 으로 묶고, 묶음마다 파일 순서에서 고른 간격으로 최대 20건을 고른다
 * (첫 건과 마지막 건 포함, k 번째는 round(k × (n−1) / 19)). 20건이 안 되는 묶음은 전부 쓴다. 난수를 쓰지 않는다.
 */
function sample(): { groups: Map<string, number>; records: { line: number; record: Json }[] } {
  const byGroup = new Map<string, { line: number; record: Json }[]>();
  readFileSync(defaultFactorsPath(), "utf8")
    .split("\n")
    .forEach((text, index) => {
      if (text.trim() === "") return;
      const record = JSON.parse(text) as Json;
      const key = `${(record.source as Json).id as string} / ${record.record_type as string}`;
      byGroup.set(key, [...(byGroup.get(key) ?? []), { line: index + 1, record }]);
    });
  const records: { line: number; record: Json }[] = [];
  const groups = new Map<string, number>();
  for (const [key, entries] of byGroup) {
    const count = Math.min(entries.length, PER_GROUP);
    const positions = new Set(Array.from({ length: count }, (_, k) => (count === 1 ? 0 : Math.round((k * (entries.length - 1)) / (count - 1)))));
    for (const position of positions) records.push(entries[position] as { line: number; record: Json });
    groups.set(key, positions.size);
  }
  return { groups, records };
}

const { groups, records } = sample();

describe("계수 레코드 계약 (schemas/factor.schema.json ↔ scope3)", () => {
  it("표본은 출처와 레코드 종류의 묶음을 모두 덮는다", () => {
    expect(groups.size).toBeGreaterThanOrEqual(10);
    expect([...groups.values()].every((count) => count >= 1 && count <= PER_GROUP)).toBe(true);
    expect(records.length).toBe([...groups.values()].reduce((sum, count) => sum + count, 0));
  });

  it("계수 표의 표본이 정본 스키마를 통과한다", () => {
    const validate = new Ajv2020({ allErrors: true, strict: false }).compile(canonical);
    const failures = records.flatMap(({ line, record }) => {
      const valid: boolean = validate(record);
      return valid ? [] : [`${line}행 ${String(record.id)}: ${(validate.errors ?? []).map((error) => `${error.instancePath} ${error.message}`).join("; ")}`];
    });
    expect(failures).toEqual([]);
  });

  it("scope3 의 사본이 계수 표의 표본을 읽는다", () => {
    const failures = records.flatMap(({ line, record }) => {
      const parsed = factorRecordSchema.safeParse(record);
      return parsed.success ? [] : [`${line}행 ${String(record.id)}: ${parsed.error.issues.map((issue) => `${issue.path.join(".")} ${issue.message}`).join("; ")}`];
    });
    expect(failures).toEqual([]);
  });

  it("scope3 가 읽는 필드는 정본에 전부 있고 타입과 필수 여부가 모순되지 않는다", () => {
    const reader = z.toJSONSchema(factorRecordSchema) as Json;
    expect(Object.keys(reader.properties as Json).length).toBeGreaterThanOrEqual(18);
    expect(contradictions(reader, canonical)).toEqual([]);
  });
});

describe("대조 함수가 어긋남을 잡는지", () => {
  const canon: Json = {
    type: "object",
    required: ["id", "value_raw", "kind", "spend"],
    properties: {
      id: { type: "string" },
      value_raw: { type: ["string", "null"] },
      kind: { enum: ["a", "b"] },
      count: { type: "number" },
      spend: { type: ["object", "null"], required: ["year"], properties: { year: { type: "integer" } } },
      tags: { type: "array", items: { $ref: "#/$defs/tag" } },
    },
    $defs: { tag: { type: "string" } },
  };
  const find = (reader: z.ZodType) => contradictions(z.toJSONSchema(reader) as Json, canon);

  it("맞는 사본은 모순이 없다 (느슨하게 읽는 것은 모순이 아니다)", () => {
    expect(find(z.object({ id: z.string(), value_raw: z.string().nullable(), kind: z.string(), count: z.number().optional(), spend: z.object({ year: z.number() }).nullable(), tags: z.array(z.string()).optional() }))).toEqual([]);
  });

  it("이름이 바뀐 필드", () => {
    expect(find(z.object({ valueRaw: z.string().nullable() }))).toEqual(["$.valueRaw: 읽는 필드가 정본에 없다"]);
  });

  it("정본에서 빠질 수 있는 필드를 필수로 읽음", () => {
    expect(find(z.object({ count: z.number() }))).toEqual(["$.count: 필수로 읽는데 정본에서는 빠질 수 있다"]);
  });

  it("null·타입·열거값·안쪽 필드·배열 원소", () => {
    expect(find(z.object({ value_raw: z.string() }))).toEqual(["$.value_raw: 정본은 null 을 낼 수 있는데 읽는 쪽은 string 만 받는다"]);
    expect(find(z.object({ count: z.number().int().optional() }))).toEqual(["$.count: 정본은 number 을 낼 수 있는데 읽는 쪽은 integer 만 받는다"]);
    expect(find(z.object({ kind: z.enum(["a"]) }))).toEqual(['$.kind: 정본의 값 "b" 를 읽는 쪽이 받지 않는다']);
    expect(find(z.object({ id: z.enum(["x"]) }))).toEqual(['$.id: 읽는 쪽은 ["x"] 만 받는데 정본에는 값의 제한이 없다']);
    expect(find(z.object({ spend: z.object({ price_year: z.number() }).nullable() }))).toEqual(["$.spend.price_year: 읽는 필드가 정본에 없다"]);
    expect(find(z.object({ tags: z.array(z.number()).optional() }))).toEqual(["$.tags[]: 정본은 string 을 낼 수 있는데 읽는 쪽은 number 만 받는다"]);
  });
});
