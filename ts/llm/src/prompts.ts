import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { SystemSegment } from "./types.js";

export interface PromptRef {
  module: string;
  name: string;
  version: number;
}

export interface PromptTemplate extends PromptRef {
  id: string;
  system: SystemSegment[];
  user: string;
  /** 템플릿 원문의 sha256. 호출 기록에 남겨 어떤 본문으로 불렀는지 추적한다. */
  hash: string;
}

export interface RenderedPrompt {
  system: SystemSegment[];
  user: string;
}

/** 프롬프트 저장소. 지금은 파일이지만 나중에 DB 로 바꿀 수 있게 인터페이스 뒤에 둔다. */
export interface PromptStore {
  get(ref: PromptRef): PromptTemplate;
}

export class PromptError extends Error {
  override name = "PromptError";
}

const ID = /^([a-z0-9][a-z0-9-]*)\/([a-z0-9][a-z0-9-]*)@([1-9]\d*)$/;

/** `모듈/이름@버전` 을 읽는다. 버전 없는 id 는 거부해 「최신」으로 흘러가지 않게 한다. */
export function parsePromptId(id: string): PromptRef {
  const match = ID.exec(id);
  if (!match) throw new PromptError(`프롬프트 id 형식이 틀렸다: ${id} (모듈/이름@버전, 버전 필수)`);
  return { module: match[1] as string, name: match[2] as string, version: Number(match[3]) };
}

export function formatPromptId(ref: PromptRef): string {
  return `${ref.module}/${ref.name}@${ref.version}`;
}

const HEADER = /^=== (system|system cache|user) ===$/;

export function parseTemplate(ref: PromptRef, source: string): PromptTemplate {
  const id = formatPromptId(ref);
  const sections: { header: string; lines: string[] }[] = [];
  for (const line of source.split(/\r?\n/)) {
    const header = HEADER.exec(line)?.[1];
    if (header) {
      sections.push({ header, lines: [] });
    } else if (sections.length > 0) {
      (sections[sections.length - 1] as { lines: string[] }).lines.push(line);
    } else if (line.trim() !== "") {
      throw new PromptError(`${id}: 구획 머리(=== system ===, === system cache ===, === user ===) 앞에 본문이 있다`);
    }
  }

  const system: SystemSegment[] = [];
  let user: string | undefined;
  for (const section of sections) {
    const text = section.lines.join("\n").trim();
    if (text === "") throw new PromptError(`${id}: ${section.header} 구획이 비었다`);
    if (section.header === "user") {
      if (user !== undefined) throw new PromptError(`${id}: user 구획이 둘 이상이다`);
      user = text;
    } else {
      if (user !== undefined) throw new PromptError(`${id}: system 구획은 user 구획 앞에 와야 한다`);
      const cache = section.header === "system cache";
      // 캐시는 앞부분 일치로 걸리므로 캐시 구획 뒤에 비캐시 구획이 와도 되지만 그 반대는 뜻이 없다.
      if (cache && system.some((segment) => !segment.cache)) {
        throw new PromptError(`${id}: 캐시 구획은 캐시하지 않는 system 구획보다 앞에 와야 한다`);
      }
      system.push({ text, cache });
    }
  }
  if (user === undefined) throw new PromptError(`${id}: user 구획이 없다`);

  return { ...ref, id, system, user, hash: createHash("sha256").update(source).digest("hex") };
}

const VARIABLE = /\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}/g;

function substitute(id: string, text: string, vars: Record<string, string>, used: Set<string>): string {
  return text.replace(VARIABLE, (_, name: string) => {
    const value = vars[name];
    if (value === undefined) throw new PromptError(`${id}: 변수 ${name} 의 값이 없다`);
    used.add(name);
    return value;
  });
}

/** 변수를 치환한다. 값이 없는 변수와 템플릿에 없는 변수는 둘 다 오류다. */
export function renderPrompt(template: PromptTemplate, vars: Record<string, string> = {}): RenderedPrompt {
  const used = new Set<string>();
  const system = template.system.map((segment) => ({
    cache: segment.cache,
    text: substitute(template.id, segment.text, vars, used),
  }));
  const user = substitute(template.id, template.user, vars, used);
  const unused = Object.keys(vars).filter((name) => !used.has(name));
  if (unused.length > 0) throw new PromptError(`${template.id}: 템플릿에 없는 변수를 넘겼다: ${unused.join(", ")}`);
  return { system, user };
}

/** `<root>/<모듈>/<이름>/<버전>.md` 에서 읽는 파일 저장소. */
export class FilePromptStore implements PromptStore {
  constructor(private readonly root: string) {}

  get(ref: PromptRef): PromptTemplate {
    const path = join(this.root, ref.module, ref.name, `${ref.version}.md`);
    if (!existsSync(path)) throw new PromptError(`프롬프트가 없다: ${formatPromptId(ref)} (${path})`);
    return parseTemplate(ref, readFileSync(path, "utf8"));
  }
}
