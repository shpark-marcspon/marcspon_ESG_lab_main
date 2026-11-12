import { existsSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

export const repoRoot = join(here, "..", "..", "..");
export const promptRoot = join(here, "..", "prompts");
export const dataDir = join(repoRoot, "data", "docpipe");
export const defaultOutRoot = join(repoRoot, "out", "docpipe");

/**
 * 명령줄에서 받은 경로를 찾는다. `pnpm -F docpipe exec` 는 패키지 디렉터리(`ts/docpipe`)에서 실행되고
 * 명령을 친 자리를 넘겨 주지 않는다(tsx 아래에서 PWD 도 패키지 디렉터리로 바뀐다). 그래서 상대 경로는
 * 현재 디렉터리, 워크스페이스(`ts/`), 저장소 루트 순서로 찾아 처음 있는 것을 쓴다.
 */
export function resolveInputPath(path: string): string {
  if (isAbsolute(path)) return path;
  for (const base of [process.cwd(), join(repoRoot, "ts"), repoRoot]) {
    const candidate = resolve(base, path);
    if (existsSync(candidate)) return candidate;
  }
  return resolve(process.cwd(), path);
}
