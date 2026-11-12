import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { DocumentError } from "../src/index.js";
import { main } from "../src/cli.js";
import { fixtureDocument, tempDir } from "./helpers.js";

function documentFile(): string {
  const path = join(tempDir(), "document.json");
  // 스키마가 요구하는 나머지 필드는 CLI 가 읽지 않는다. 읽는 필드만 실어 쓴다.
  writeFileSync(path, JSON.stringify(fixtureDocument()));
  return path;
}

async function run(argv: string[]) {
  const lines: string[] = [];
  const logs: string[] = [];
  const code = await main(argv, (line) => lines.push(line), (line) => logs.push(line));
  return { code, text: lines.join("\n"), logs };
}

describe("CLI", () => {
  it("translate --provider mock: 끝까지 돌고 상태·결과·보고 파일을 남긴다. 다시 돌리면 건너뛴다", async () => {
    const out = tempDir();
    const argv = ["translate", documentFile(), "--provider", "mock", "--budget-tokens", "400", "--out", out];
    const first = await run(argv);
    expect(first.code).toBe(0);
    const summary = JSON.parse(first.text) as { chunks: { total: number; done: number; skipped: number }; providerCallsThisRun: number; coverage: { ok: boolean }; glossary: { synthetic: boolean }; synthetic: string };
    expect(summary.chunks.done).toBe(summary.chunks.total);
    expect(summary.providerCallsThisRun).toBe(summary.chunks.total);
    expect(summary.coverage.ok).toBe(true);
    expect(summary.glossary.synthetic).toBe(true);
    expect(summary.synthetic).toMatch(/번역이 아니다/);
    expect(readdirSync(join(out, "chunks"))).toHaveLength(summary.chunks.total);
    for (const file of ["plan.json", "result.json", "report.json", "calls.jsonl"]) expect(existsSync(join(out, file))).toBe(true);
    const result = JSON.parse(readFileSync(join(out, "result.json"), "utf8")) as { tables: { id: string; cells: string[][] | null }[] };
    expect(result.tables.find((table) => table.id === "p1-t1")!.cells![1]!.slice(1)).toEqual(["GWh", "1,234", "1,567.8"]);
    expect(first.logs.some((line) => line.includes(`진행 ${summary.chunks.total}/${summary.chunks.total}`))).toBe(true);

    const second = await run(argv);
    expect(second.code).toBe(0);
    expect(JSON.parse(second.text)).toMatchObject({ chunks: { done: 0, skipped: summary.chunks.total, workCalls: 0 }, providerCallsThisRun: 0 });

    const fresh = await run([...argv, "--fresh"]);
    expect(JSON.parse(fresh.text)).toMatchObject({ chunks: { done: summary.chunks.total, skipped: 0 } });
  });

  it("translate --dry-run: 공급사를 부르지 않고 조각 수·조각별 예상 토큰·총 예상 비용과 토큰 어림의 가정을 낸다", async () => {
    const { code, text } = await run(["translate", documentFile(), "--dry-run", "--budget-tokens", "400"]);
    expect(code).toBe(0);
    expect(text).toContain("[경로 light] 모델 claude-haiku-4-5");
    expect(text).toContain("[경로 standard] 모델 claude-sonnet-4-6");
    expect(text).toMatch(/조각 수: \d+/);
    expect(text).toMatch(/총 예상 비용: \d+\.\d{4} USD/);
    expect(text).toContain("한글·한자 1자 = 1토큰");
    expect(text).toContain("가정");
    expect(text).toMatch(/c0001-[0-9a-f]{12}\s+1/);
    expect(text).toContain("캐시가 걸리지 않는다");
  });

  it("greenwash: dry-run 과 모의 실행이 돌고 체크리스트가 synthetic·결정 대기임을 출력에 싣는다", async () => {
    const path = documentFile();
    const dry = await run(["greenwash", path, "--dry-run"]);
    expect(dry.code).toBe(0);
    expect(dry.text).toContain('"synthetic": true');
    expect(dry.text).toContain("결정 대기");

    const out = tempDir();
    const mock = await run(["greenwash", path, "--provider", "mock", "--out", out]);
    expect(mock.code).toBe(0);
    const findings = JSON.parse(readFileSync(join(out, "findings.json"), "utf8")) as { checklist: { synthetic: boolean; decisionPending: string }; findings: unknown[] };
    expect(findings.checklist.synthetic).toBe(true);
    expect(findings.checklist.decisionPending).toMatch(/결정 대기/);
    expect(findings.findings.length).toBeGreaterThan(0);
  });

  it("실제 공급사는 dry-run 없이 부를 수 없다", async () => {
    const path = documentFile();
    expect((await run(["translate", path, "--provider", "anthropic"])).code).toBe(2);
    expect((await run(["translate", path])).code).toBe(2);
    expect((await run(["summarize", path, "--dry-run"])).code).toBe(2);
  });

  it("문서 JSON 이 없거나 모양이 틀리면 빈 결과가 아니라 오류다", async () => {
    await expect(run(["translate", join(tempDir(), "없음.json"), "--dry-run"])).rejects.toThrow(DocumentError);
    const broken = join(tempDir(), "document.json");
    writeFileSync(broken, JSON.stringify({ schema_version: "0.1", blocks: [] }));
    await expect(run(["translate", broken, "--dry-run"])).rejects.toThrow(/형식이 맞지 않는다/);
    const pdf = join(tempDir(), "report.pdf");
    writeFileSync(pdf, "%PDF-1.7");
    await expect(run(["translate", pdf, "--dry-run"])).rejects.toThrow(/JSON 으로 읽을 수 없다/);
  });
});
