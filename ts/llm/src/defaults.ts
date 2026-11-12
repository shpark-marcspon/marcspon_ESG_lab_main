import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "..", "..", "..");

/** 저장소의 설정 파일 경로. 가상 모델 파일은 `synthetic` 을 켰을 때만 넣는다. */
export function defaultConfigPaths(options: { synthetic: boolean }): string[] {
  const paths = [join(repoRoot, "data", "llm", "models.json")];
  if (options.synthetic) {
    paths.push(join(repoRoot, "data", "llm", "models.synthetic.json"));
    // 가상 임베딩 모델. 테스트와 모의 실행 전용이라 패키지 안에 둔다.
    paths.push(join(here, "..", "config", "embeddings.synthetic.json"));
  }
  return paths;
}

export function defaultPromptRoot(): string {
  return join(here, "..", "prompts");
}
