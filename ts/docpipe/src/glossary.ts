import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { z } from "zod";
import { dataDir } from "./paths.js";

const glossarySchema = z.object({
  synthetic: z.boolean(),
  note: z.string(),
  sourceLang: z.literal("ko"),
  targetLang: z.literal("en"),
  terms: z
    .array(
      z.object({
        ko: z.string().min(1),
        en: z.string().min(1),
        /** 같은 국문 용어의 다른 번역으로 알려진 것. 조각 사이의 번역 흔들림을 문자열로 잡는 데 쓴다. */
        variants: z.array(z.string().min(1)).default([]),
        synthetic: z.boolean().optional(),
      }),
    )
    .min(1),
});

export type GlossaryTerm = z.infer<typeof glossarySchema>["terms"][number];

export interface Glossary {
  path: string;
  synthetic: boolean;
  sha256: string;
  terms: GlossaryTerm[];
}

export class GlossaryError extends Error {
  override name = "GlossaryError";
}

export const defaultGlossaryPath = join(dataDir, "glossary.synthetic.json");

export function parseGlossary(raw: unknown, path: string, source: string): Glossary {
  const parsed = glossarySchema.safeParse(raw);
  if (!parsed.success) throw new GlossaryError(`${path}: ${z.prettifyError(parsed.error)}`);
  const seen = new Set<string>();
  for (const term of parsed.data.terms) {
    if (seen.has(term.ko)) throw new GlossaryError(`${path}: 국문 용어가 겹친다: ${term.ko}`);
    seen.add(term.ko);
    // 가상 용어집은 파일과 항목 양쪽에 표시가 있어야 한다.
    if (parsed.data.synthetic && term.synthetic !== true) throw new GlossaryError(`${path}: 가상 용어집의 항목 ${term.ko} 에 synthetic 표시가 없다`);
    if (!parsed.data.synthetic && term.synthetic) throw new GlossaryError(`${path}: 가상 항목 ${term.ko} 는 synthetic 파일에 둬야 한다`);
  }
  return { path, synthetic: parsed.data.synthetic, sha256: createHash("sha256").update(source).digest("hex"), terms: parsed.data.terms };
}

export function loadGlossary(path: string = defaultGlossaryPath): Glossary {
  const source = readFileSync(path, "utf8");
  return parseGlossary(JSON.parse(source), path, source);
}

/** 프롬프트의 고정 앞부분에 넣는 꼴. 조각마다 같아야 캐시가 걸리므로 용어집 전체를 넣는다. */
export function renderGlossary(glossary: Glossary): string {
  return glossary.terms.map((term) => `${term.ko} => ${term.en}`).join("\n");
}
