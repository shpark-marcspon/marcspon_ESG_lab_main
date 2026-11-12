import { describe, expect, it } from "vitest";
import { main } from "../src/cli.js";

async function run(args: string[]) {
  const lines: string[] = [];
  const code = await main(args, (line) => lines.push(line));
  return { code, output: lines.join("\n") };
}

describe("cli call", () => {
  it("--dry-run 은 조립된 요청과 어림 토큰·비용을 출력한다", async () => {
    const { code, output } = await run(["call", "--prompt", "example/hello@1", "--provider", "mock", "--dry-run"]);
    expect(code).toBe(0);
    const report = JSON.parse(output);
    expect(report).toMatchObject({ dryRun: true, promptId: "example/hello@1", provider: "mock", model: "mock-synthetic" });
    expect(report.estimate.inputTokens).toBeGreaterThan(0);
    expect(report.warnings[0].code).toBe("cache_below_minimum");
  });

  it("실제 공급사도 --dry-run 은 요청 조립까지 한다 (Haiku 4.5 의 캐시 최소 길이 경고 포함)", async () => {
    const { code, output } = await run(["call", "--prompt", "example/hello@2", "--provider", "anthropic", "--var", "name=김", "--dry-run"]);
    expect(code).toBe(0);
    const report = JSON.parse(output);
    expect(report.model).toBe("claude-haiku-4-5");
    expect(report.request.model).toBe("claude-haiku-4-5-20251001");
    expect(report.request.system[0].cache_control).toEqual({ type: "ephemeral" });
    expect(report.fallbackModels).toEqual(["gpt-6-luna"]);
    expect(report.warnings[0].message).toContain("4096");
  });

  it("--dry-run 없이 실제 공급사를 고르면 부르지 않고 거부한다", async () => {
    const { code, output } = await run(["call", "--prompt", "example/hello@1", "--provider", "anthropic"]);
    expect(code).toBe(2);
    expect(output).toContain("지원하지 않는다");
  });

  it("모의 공급사는 --mock-text 로 심은 응답을 돌려준다", async () => {
    const ok = await run(["call", "--prompt", "example/hello@1", "--provider", "mock", "--mock-text", "안녕하세요"]);
    expect(ok.code).toBe(0);
    expect(JSON.parse(ok.output)).toMatchObject({ kind: "success", data: "안녕하세요" });

    const empty = await run(["call", "--prompt", "example/hello@1", "--provider", "mock"]);
    expect(empty.code).toBe(1);
    expect(JSON.parse(empty.output).kind).toBe("provider_error");
  });

  it("인자가 모자라면 사용법을 출력한다", async () => {
    expect((await run(["call", "--provider", "mock"])).code).toBe(2);
  });
});
