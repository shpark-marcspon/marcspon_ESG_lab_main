import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { main } from "../src/cli.js";
import { ReconcileFileError, loadCatalog, loadMemory, loadUnits } from "../src/index.js";
import { asDocumentJson, buildXlsx, catalog, syntheticDocument, tempDir } from "./helpers.js";

// 가상(synthetic) 입력 한 쌍: 원천은 엑셀, 보고서는 문서 JSON.
function inputs(): { source: string; report: string } {
  const dir = tempDir();
  const source = join(dir, "source.synthetic.xlsx");
  writeFileSync(
    source,
    buildXlsx(
      [
        {
          name: "환경",
          cells: [
            { ref: "A1", text: "지표" },
            { ref: "B1", text: "단위" },
            { ref: "C1", v: "2023" },
            { ref: "D1", v: "2024" },
            { ref: "A2", text: "직접 온실가스(Scope 1)" },
            { ref: "B2", text: "tCO2eq" },
            { ref: "C2", v: "1200.4", s: 1 },
            { ref: "D2", v: "1530", s: 1 },
            { ref: "A3", text: "총 에너지 사용량" },
            { ref: "B3", text: "GJ" },
            { ref: "C3", v: "45324.9", s: 1 },
            { ref: "D3", v: "47080", s: 1 },
          ],
        },
      ],
      [0, 3],
    ),
  );
  const report = join(dir, "report.json");
  const document = syntheticDocument("report", [
    {
      id: "p1-t1",
      cells: [
        ["구분", "단위", "2023", "2024"],
        ["직접 온실가스(Scope 1)", "tCO2eq", "1,200", "1,350"],
        ["총 에너지 사용량", "TJ", "45.3", "47.1"],
      ],
    },
  ]);
  writeFileSync(report, JSON.stringify(asDocumentJson(document)));
  return { source, report };
}

async function run(argv: string[]) {
  const lines: string[] = [];
  // 환경을 비워 넘긴다: 테스트는 실제 키가 환경에 있어도 쓰지 않는다.
  const code = await main(argv, (line) => lines.push(line), () => undefined, { env: {} });
  return { code, text: lines.join("\n") };
}

describe("CLI", () => {
  it("run --provider mock: 엑셀 원천과 보고서를 끝까지 잇고 결과 JSON 과 사람이 읽는 요약을 쓴다", async () => {
    const { source, report } = inputs();
    const out = tempDir();
    const { code, text } = await run(["run", source, report, "--provider", "mock", "--out", out]);
    expect(code).toBe(0);
    const stdout = JSON.parse(text) as { synthetic: string; catalogSynthetic: boolean; providerCalls: number; pairs: Record<string, number>; a: { sentence: string } };
    expect(stdout.synthetic).toContain("synthetic");
    expect(stdout.catalogSynthetic).toBe(true);
    expect(stdout.providerCalls).toBe(2);
    expect(stdout.pairs).toEqual({ match: 3, mismatch: 1, needs_explanation: 0 });
    expect(stdout.a.sentence).toBe("숫자 4개 중 4개 대조 (일치 3, 불일치 1, 설명 필요 0), 짝 없음 0개, 미확인 0개");
    expect(readdirSync(out).sort()).toEqual(["calls.jsonl", "result.json", "summary.md"]);

    const result = JSON.parse(readFileSync(join(out, "result.json"), "utf8")) as { synthetic: string; inputs: { catalog: { synthetic: boolean } }; findings: { outcome: string; a: { raw: string; value: string; where: { sheet: string; ref: string }; stored: { storedRaw: string; display: string } }; b: { raw: string }; comparison: { expA: number; rule: string } }[] };
    expect(result.synthetic).toContain("synthetic");
    expect(result.inputs.catalog.synthetic).toBe(true);
    const mismatch = result.findings.find((finding) => finding.outcome === "mismatch");
    expect(mismatch).toMatchObject({ a: { raw: "1,530", value: "1530", where: { sheet: "환경", ref: "D2" } }, b: { raw: "1,350" } });
    // 저장 값(1200.4)과 화면 표기(1,200)가 다른 셀: 견줄 때는 저장 값을 쓰고 둘 다 결과에 남는다.
    const rounded = result.findings.find((finding) => finding.a?.where.ref === "C2");
    expect(rounded).toMatchObject({ outcome: "match", a: { value: "1200.4", raw: "1,200", stored: { storedRaw: "1200.4", display: "1,200" } }, comparison: { rule: "rounded_half_up" } });
    // 단위가 다른 짝 (GJ 대 TJ): 환산해 견준다.
    expect(result.findings.find((finding) => finding.a?.where.ref === "C3")).toMatchObject({ outcome: "match", comparison: { expA: -3 } });

    const summary = readFileSync(join(out, "summary.md"), "utf8");
    expect(summary).toContain("모의 실행이다");
    expect(summary).toContain("지표 목록이 synthetic 이다");
    expect(summary).toContain("시트 환경 D2");
    expect(summary).toContain("1쪽 표 p1-t1 r1c3");
    // 호출 기록은 전부 모의 공급사다.
    const calls = readFileSync(join(out, "calls.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line) as { provider: string });
    expect(calls.map((call) => call.provider)).toEqual(["mock", "mock"]);
  });

  it("run --dry-run: 공급사를 부르지 않고 경로별 호출 수·입력 토큰·비용 상한을 낸다", async () => {
    const { source, report } = inputs();
    const out = tempDir();
    const { code, text } = await run(["run", source, report, "--dry-run", "--out", out]);
    expect(code).toBe(0);
    expect(text).toContain("[경로 light-openai] 모델 gpt-6-luna");
    expect(text).toContain("[경로 light-anthropic] 모델 claude-haiku-4-5");
    expect(text).toContain("[경로 light-gemini-35] 모델 gemini-3.5-flash-lite");
    const report2 = JSON.parse(readFileSync(join(out, "dry-run.json"), "utf8")) as { routes: { calls: number; targets: number; inputTokens: number; expectedCostUsd: number; costUpperBoundUsd: number }[] };
    expect(report2.routes).toHaveLength(3);
    for (const row of report2.routes) {
      expect(row).toMatchObject({ calls: 2, targets: 8 });
      expect(row.inputTokens).toBeGreaterThan(0);
      expect(row.costUpperBoundUsd).toBeGreaterThanOrEqual(row.expectedCostUsd);
    }
    expect(existsSync(join(out, "calls.jsonl"))).toBe(false);
  });

  it("확정 기억 파일을 주면 그만큼 호출이 준다", async () => {
    const { source, report } = inputs();
    const memory = join(tempDir(), "memory.synthetic.json");
    writeFileSync(
      memory,
      JSON.stringify({
        synthetic: true,
        note: "테스트용 (synthetic)",
        entries: [
          { docKind: "report", rowPath: ["직접 온실가스(Scope 1)", "tCO2eq"], colPath: [], indicatorKey: "ghg.scope1", unit: "tCO2eq", boundary: "unspecified", basisNote: null, period: { from: "header_year" }, confirmedBy: "synthetic", confirmedAt: "2026-10-04" },
          { docKind: "report", rowPath: ["총 에너지 사용량", "TJ"], colPath: [], indicatorKey: "energy.total", unit: "TJ", boundary: "unspecified", basisNote: null, period: { from: "header_year" }, confirmedBy: "synthetic", confirmedAt: "2026-10-04" },
        ],
      }),
    );
    const { code, text } = await run(["run", source, report, "--provider", "mock", "--kind-b", "report", "--memory", memory, "--out", tempDir()]);
    expect(code).toBe(0);
    const stdout = JSON.parse(text) as { providerCalls: number; labeling: { b: { fromMemory: number; llmCalls: number } }; pairs: Record<string, number> };
    expect(stdout.providerCalls).toBe(1);
    expect(stdout.labeling.b).toMatchObject({ fromMemory: 4, llmCalls: 0 });
    expect(stdout.pairs).toEqual({ match: 3, mismatch: 1, needs_explanation: 0 });
  });

  it("count 와 facts: 모델 없이 비율 표와 사실 후보 파일을 쓴다", async () => {
    const { source, report } = inputs();
    const out = tempDir();
    const count = await run(["count", source, report, "--out", out]);
    expect(count.code).toBe(0);
    expect(count.text).toContain("| source.synthetic.xlsx | 4 | 4 (100.0%)");
    expect(readdirSync(out).sort()).toEqual(["ratio.json", "ratio.md"]);
    const facts = await run(["facts", report, "--out", out]);
    expect(facts.code).toBe(0);
    expect((JSON.parse(readFileSync(join(out, "facts.json"), "utf8")) as { facts: unknown[] }).facts).toHaveLength(4);
  });

  it("--out 이 없으면 아무것도 쓰지 않고 끝난다. 실제 공급사는 경로·상한·키 없이 부르지 않는다", async () => {
    const { source, report } = inputs();
    expect((await run(["run", source, report, "--provider", "mock"])).code).toBe(2);
    expect((await run(["count", report])).code).toBe(2);
    const live = await run(["run", source, report, "--provider", "openai", "--out", tempDir()]);
    expect(live.code).toBe(2);
    expect(live.text).toContain("--route");
    expect((await run(["run", source, report, "--provider", "mock", "--dry-run", "--out", tempDir()])).code).toBe(2);
  });

  it("없는 입력은 오류로 끝난다", async () => {
    const missing = await run(["facts", join(tempDir(), "none.json"), "--out", tempDir()]);
    expect(missing.code).toBe(1);
    expect(missing.text).toContain("입력 파일이 없다");
  });
});

describe("데이터 파일", () => {
  it("기본 지표 목록은 synthetic 으로 표시돼 있고, 출처도 synthetic 표시도 없는 목록은 거부한다", () => {
    expect(catalog.synthetic).toBe(true);
    const dir = tempDir();
    const body = { id: "x", note: "", boundaries: [{ key: "unspecified", label: "", aliases: [] }], indicators: [{ key: "k", label: "지표", unitFamily: "mass", aliases: [] }] };
    const unsourced = join(dir, "indicators.json");
    writeFileSync(unsourced, JSON.stringify({ ...body, synthetic: false, source: null }));
    expect(() => loadCatalog(unsourced)).toThrow(/출처 없는 지표 목록/);
    writeFileSync(unsourced, JSON.stringify({ ...body, synthetic: true, source: null }));
    expect(() => loadCatalog(unsourced)).toThrow(/파일 이름에 synthetic/);
    const sourced = join(dir, "indicators-sourced.json");
    writeFileSync(sourced, JSON.stringify({ ...body, synthetic: false, source: { name: "가상 출처", url: "https://example.com/list", checkedOn: "2026-10-04", terms: "가상" } }));
    expect(loadCatalog(sourced).indicators).toHaveLength(1);
  });

  it("단위 표의 환산은 전부 10 의 거듭제곱이고 출처가 있다", () => {
    const table = loadUnits();
    expect(table.scaleWords).toMatchObject({ 천: 3, 만: 4, 억: 8, 조: 12 });
    const dir = tempDir();
    const path = join(dir, "units.json");
    writeFileSync(path, JSON.stringify({ note: "", scaleWords: [], units: [{ symbol: "kg", family: "mass", exp: -3, aliases: [], compactScale: false, sourceUrl: null, checkedOn: null, sourceNote: "" }] }));
    expect(() => loadUnits(path)).toThrow(/출처가 없다/);
  });

  it("저장소의 확정 기억 예시(synthetic)가 지표 목록과 맞는다", () => {
    const entries = loadMemory(join(import.meta.dirname, "..", "..", "..", "data", "reconcile", "memory.synthetic.json"), catalog);
    expect(entries).toHaveLength(2);
    expect(() => loadMemory(join(tempDir(), "none.json"), catalog)).toThrow(ReconcileFileError);
  });
});
