import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { z } from "zod";
import { ReconcileFileError, documentSchema, readJsonFile, resolveInputPath, resolveOutputPath } from "../adapters/files.js";
import { extractDocument, type DocModel, type Extraction, type FactWhere } from "../domain/facts.js";
import { containerIdOf, indexPlaces, placeOfWhere, sameSource, type CharPlaces } from "../domain/places.js";

// 밴드 C1 (MARC-TICK-35) 의 측정 명령: 문서 모델의 값 후보 전부에 대해, 글자 자리(`char-places.json`)에서 쪽과 상자를 얻어 적는다.
// 모델을 부르지 않고 PDF 를 열지 않는다. 상자가 그 숫자를 덮는지는 `py/docparse` 의 `placecheck` 가 PDF 를 다시 읽어 본다.

const USAGE = `쓰임: tsx src/compare/places-cli.ts fact-places --doc <document.json> --places <char-places.json> --out <디렉터리>
  값 후보(와 값이 아닌 숫자)마다 그릇·글자 범위·상자를 적은 fact-places.json 을 쓴다. 상대 경로는 ts/ 기준이다.`;

const box = z.array(z.number()).length(4);
export const charPlacesSchema = z.object({
  schema_version: z.literal("0.1"),
  source: z.object({ file_name: z.string(), sha256: z.string(), page_count: z.number().int() }),
  unit: z.literal("utf16"),
  containers: z.array(z.object({ id: z.string(), kind: z.enum(["block", "cell"]), page: z.number().int(), bbox: box, runs: z.array(z.object({ start: z.number().int(), y0: z.number(), y1: z.number(), x0: z.array(z.number()), x1: z.array(z.number()) })).min(1) })),
  unlinked: z.array(z.object({ id: z.string(), page: z.number().int(), reason: z.string() })),
});

export interface FactPlaceRow {
  id: string | null;
  bucket: string;
  page: number | null;
  container: string | null;
  start: number | null;
  end: number | null;
  raw: string;
  found: boolean;
  reason: string | null;
  boxes: number[][];
  covered: number;
  length: number;
}

function row(id: string | null, bucket: string, where: FactWhere, raw: string, index: ReturnType<typeof indexPlaces>): FactPlaceRow {
  const place = placeOfWhere(where, index);
  const base = { id, bucket, page: where.page, container: containerIdOf(where), start: where.span?.start ?? null, end: where.span?.end ?? null, raw };
  return place.found ? { ...base, found: true, reason: null, boxes: place.boxes, covered: place.covered, length: place.length } : { ...base, found: false, reason: place.reason, boxes: [], covered: 0, length: 0 };
}

/** 값 후보 전부와 값이 아닌 숫자 전부의 자리. */
export function factPlaces(extraction: Extraction, places: CharPlaces): { facts: FactPlaceRow[]; nonValues: FactPlaceRow[] } {
  const index = indexPlaces(places);
  return {
    facts: extraction.facts.map((fact) => row(fact.id, fact.bucket, fact.where, fact.raw, index)),
    nonValues: extraction.nonValues.map((item) => row(null, item.class, item.where, item.raw, index)),
  };
}

export function main(argv: string[], out: (line: string) => void = console.log): number {
  const { values, positionals } = parseArgs({ args: argv, allowPositionals: true, options: { doc: { type: "string" }, places: { type: "string" }, out: { type: "string" } } });
  if (positionals[0] !== "fact-places" || !values.doc || !values.places || !values.out) {
    out(USAGE);
    return 2;
  }
  try {
    const parsedDoc = documentSchema.safeParse(readJsonFile(resolveInputPath(values.doc), "문서 JSON"));
    if (!parsedDoc.success) throw new ReconcileFileError(`${values.doc}: 문서 JSON 형식이 맞지 않는다`);
    const document = parsedDoc.data as DocModel;
    const parsedPlaces = charPlacesSchema.safeParse(readJsonFile(resolveInputPath(values.places), "글자 자리 JSON"));
    if (!parsedPlaces.success) throw new ReconcileFileError(`${values.places}: 글자 자리 JSON 형식이 맞지 않는다\n${z.prettifyError(parsedPlaces.error)}`);
    const places = parsedPlaces.data as CharPlaces;
    if (!sameSource(document, places)) throw new ReconcileFileError("문서 모델과 글자 자리가 서로 다른 PDF 의 것이다 (source.sha256 이 다르다)");
    const result = factPlaces(extractDocument(document, "doc"), places);
    const dir = resolveOutputPath(values.out);
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, "fact-places.json"), `${JSON.stringify({ source: document.source, facts: result.facts, non_values: result.nonValues })}\n`);
    out(JSON.stringify({ out: join(dir, "fact-places.json"), facts: result.facts.length, facts_found: result.facts.filter((item) => item.found).length, non_values: result.nonValues.length, non_values_found: result.nonValues.filter((item) => item.found).length }));
    return 0;
  } catch (error) {
    if (error instanceof ReconcileFileError) {
      out(`오류: ${error.message}`);
      return 2;
    }
    throw error;
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) process.exitCode = main(process.argv.slice(2));
