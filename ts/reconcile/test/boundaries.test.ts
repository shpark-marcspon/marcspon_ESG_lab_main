import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const src = join(import.meta.dirname, "..", "src");
const read = (folder: string) => readdirSync(join(src, folder)).map((file) => ({ file, text: readFileSync(join(src, folder, file), "utf8") }));

describe("폴더 경계", () => {
  it("domain 은 adapters 와 파일 시스템, 공급사 SDK 를 import 하지 않는다", () => {
    for (const { file, text } of read("domain")) {
      const imports = [...text.matchAll(/from "([^"]+)"/g)].map((match) => match[1] as string);
      const bad = imports.filter((name) => name.includes("adapters") || name.startsWith("node:") || ["fflate", "fast-xml-parser", "openai", "@anthropic-ai/sdk", "@google/genai"].includes(name));
      expect(bad, file).toEqual([]);
    }
  });

  it("값을 다루는 domain 코드는 parseFloat·Number() 로 원문 숫자를 읽지 않는다", () => {
    for (const { file, text } of read("domain")) {
      expect(text.includes("parseFloat"), file).toBe(false);
      // 값이 지나는 파일에는 Number( 가 없다. 행 번호·지수처럼 값이 아닌 정수에만 쓰는 파일은 따로 적는다.
      if (!["label.ts", "numfmt.ts"].includes(file)) expect(/\bNumber\(/.test(text), file).toBe(false);
    }
  });
});
