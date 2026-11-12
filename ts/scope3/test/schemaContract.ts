// JSON 스키마 둘을 견준다: 읽는 쪽(reader, TS 가 손으로 베낀 부분 사본)이 정본(canonical)과 모순되는 자리를 찾는다.
// 읽는 쪽이 정본보다 느슨한 것(문자열로 읽는데 정본은 열거값)은 모순이 아니다. 모순으로 치는 것:
//   - 읽는 필드가 정본에 없다
//   - 읽는 쪽은 필수로 읽는데 정본에서는 빠질 수 있다
//   - 정본이 낼 수 있는 타입(null 포함)이나 열거값을 읽는 쪽이 받지 않는다
// 정본의 `allOf`/`if` 는 보지 않는다 (기본 `properties` 가 가장 넓은 타입이다).

type Json = Record<string, unknown>;
type JsonType = "string" | "number" | "integer" | "boolean" | "null" | "object" | "array";

function deref(schema: Json, root: Json): Json {
  let current = schema;
  while (typeof current.$ref === "string") {
    if (!current.$ref.startsWith("#/")) throw new Error(`밖을 가리키는 $ref 는 다루지 않는다: ${current.$ref}`);
    let target: unknown = root;
    for (const part of current.$ref.slice(2).split("/")) target = (target as Json)[part];
    current = target as Json;
  }
  return current;
}

function typeOfValue(value: unknown): JsonType {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  if (typeof value === "number") return Number.isInteger(value) ? "integer" : "number";
  return typeof value as JsonType;
}

/** 스키마 하나를 타입이 하나뿐인 갈래들로 편다 (`anyOf`, `type` 배열, 타입 없는 `enum`·`const`). */
function branches(schema: Json, root: Json): (Json & { type: JsonType })[] {
  const node = deref(schema, root);
  const alternatives = (node.anyOf ?? node.oneOf) as Json[] | undefined;
  if (alternatives) return alternatives.flatMap((entry) => branches(entry, root));
  if (Array.isArray(node.type)) return (node.type as JsonType[]).map((type) => ({ ...node, type }));
  if (typeof node.type === "string") return [{ ...node, type: node.type as JsonType }];
  const values = node.enum !== undefined ? (node.enum as unknown[]) : "const" in node ? [node.const] : null;
  if (!values) throw new Error(`타입을 알 수 없는 스키마다: ${JSON.stringify(node).slice(0, 120)}`);
  return [...new Set(values.map(typeOfValue))].map((type) => ({ type, enum: values.filter((value) => typeOfValue(value) === type) }));
}

function allowed(branch: Json): unknown[] | null {
  if (branch.enum !== undefined) return branch.enum as unknown[];
  return "const" in branch ? [branch.const] : null;
}

/** 읽는 쪽 스키마가 정본과 모순되는 자리의 목록. 비어 있으면 모순이 없다. */
export function contradictions(reader: Json, canonical: Json, path = "$", readerRoot: Json = reader, canonicalRoot: Json = canonical): string[] {
  const issues: string[] = [];
  const readerBranches = branches(reader, readerRoot);
  for (const canon of branches(canonical, canonicalRoot)) {
    const read = readerBranches.find((branch) => branch.type === canon.type || (branch.type === "number" && canon.type === "integer"));
    if (!read) {
      issues.push(`${path}: 정본은 ${canon.type} 을 낼 수 있는데 읽는 쪽은 ${readerBranches.map((branch) => branch.type).join("|")} 만 받는다`);
      continue;
    }
    const readValues = allowed(read);
    if (readValues) {
      const canonValues = allowed(canon);
      if (!canonValues) issues.push(`${path}: 읽는 쪽은 ${JSON.stringify(readValues)} 만 받는데 정본에는 값의 제한이 없다`);
      else for (const value of canonValues) if (!readValues.includes(value)) issues.push(`${path}: 정본의 값 ${JSON.stringify(value)} 를 읽는 쪽이 받지 않는다`);
    }
    if (canon.type === "object") {
      const canonProperties = (canon.properties ?? {}) as Record<string, Json>;
      const canonRequired = (canon.required ?? []) as string[];
      for (const [key, property] of Object.entries((read.properties ?? {}) as Record<string, Json>)) {
        const target = canonProperties[key];
        if (!target) {
          issues.push(`${path}.${key}: 읽는 필드가 정본에 없다`);
          continue;
        }
        if (((read.required ?? []) as string[]).includes(key) && !canonRequired.includes(key)) issues.push(`${path}.${key}: 필수로 읽는데 정본에서는 빠질 수 있다`);
        issues.push(...contradictions(property, target, `${path}.${key}`, readerRoot, canonicalRoot));
      }
    }
    if (canon.type === "array" && read.items && canon.items) issues.push(...contradictions(read.items as Json, canon.items as Json, `${path}[]`, readerRoot, canonicalRoot));
  }
  return issues;
}
