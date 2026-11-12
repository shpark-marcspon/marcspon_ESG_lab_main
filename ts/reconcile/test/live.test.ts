import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { CallRecord, OpenAILike } from "llm";
import { describe, expect, it } from "vitest";
import { main } from "../src/cli.js";
import { asDocumentJson, syntheticDocument, tempDir } from "./helpers.js";

// 실제 공급사로 가는 호출은 없다. SDK 클라이언트 자리에 가짜를 넣어 CLI 의 배선만 본다.
// 키, 토큰 수, 응답, 문서의 숫자는 전부 가상(synthetic) 값이다.
const KEY_SYNTHETIC = "key-synthetic";

type Create = OpenAILike["responses"]["create"];
type Body = Parameters<Create>[0];

interface Payload {
  doc: string;
  table_id: string;
  rows: { r: number; cells: Record<string, string> }[];
  targets: string[];
}

function payloadOf(body: Body): Payload {
  const text = JSON.stringify(body.input);
  const parts = (body.input as { content: { text?: string }[] | string }[]).flatMap((item) => (typeof item.content === "string" ? [item.content] : item.content.map((part) => part.text ?? "")));
  const joined = parts.join("\n");
  const at = joined.indexOf("\n{");
  if (at === -1) throw new Error(`가짜 SDK: 요청에서 입력 JSON 을 찾지 못했다: ${text.slice(0, 80)}`);
  return JSON.parse(joined.slice(at + 1)) as Payload;
}

const MEANING: Record<string, string> = { "직접 온실가스(Scope 1)": "ghg.scope1", "총 에너지 사용량": "energy.total" };

/** 머리글을 읽어 뜻을 붙이는 가짜 응답 (뜻 붙이기), 요청의 글자를 그대로 돌려주는 가짜 응답 (옮겨 쓰기). */
function reply(body: Body, mutate: (target: string, text: string) => string): string {
  const payload = payloadOf(body);
  const cell = (r: number, c: string): string => payload.rows.find((row) => row.r === r)?.cells[c] ?? "";
  const format = body.text?.format as { name?: string } | undefined;
  if (format?.name === "reconcile_transcription") {
    return JSON.stringify({ cells: payload.targets.map((target) => ({ target, text: mutate(target, cell(Number(/^r(\d+)/.exec(target)?.[1]), /c\d+$/.exec(target)?.[0] as string)) })) });
  }
  const labels = payload.targets.map((target) => {
    const r = Number(/^r(\d+)/.exec(target)?.[1]);
    const c = /c\d+$/.exec(target)?.[0] as string;
    const key = MEANING[cell(r, "c0")];
    const base = { target, quote: null, occurrence: null, free_text: null, basis_note: null, confidence: "high", evidence: [`r${r}c0`] };
    if (!key) return { ...base, kind: "not_in_catalog", indicator_key: null, free_text: "가짜 응답 (synthetic)", period: null, unit: null, boundary: null };
    return { ...base, kind: "indicator", indicator_key: key, period: cell(0, c), unit: cell(r, "c1"), boundary: "unspecified" };
  });
  return JSON.stringify({ labels });
}

/** 한 번에 입력 1000 + 출력 1000 토큰: gpt-6-luna 단가(입력 0.10, 출력 0.50 USD/MTok)로 0.0006 USD. */
function fakeDeps(env: Record<string, string | undefined> = { OPENAI_API_KEY: KEY_SYNTHETIC }, mutate: (target: string, text: string) => string = (_target, text) => text) {
  const keys: string[] = [];
  const bodies: Body[] = [];
  const client: OpenAILike = {
    responses: {
      create(body) {
        bodies.push(body);
        return Promise.resolve({
          id: "resp_synthetic",
          model: String(body.model),
          status: "completed",
          incomplete_details: null,
          output: [{ type: "message", id: "msg", role: "assistant", status: "completed", content: [{ type: "output_text", text: reply(body, mutate), annotations: [] }] }],
          usage: { input_tokens: 1000, input_tokens_details: { cached_tokens: 0, cache_write_tokens: 0 }, output_tokens: 1000, output_tokens_details: { reasoning_tokens: 400 }, total_tokens: 2000 },
        } as unknown as Awaited<ReturnType<Create>>);
      },
    },
  };
  return { keys, bodies, deps: { env, openaiClient: (apiKey: string) => (keys.push(apiKey), client) } };
}

/** 가상 문서 한 쌍. 표가 둘씩이라 요청 단위가 문서마다 둘이다 (합쳐서 넷). */
function inputs(): { a: string; b: string } {
  const dir = tempDir();
  const table = (id: string, rows: string[][]) => ({ id, cells: [["구분", "단위", "2023", "2024"], ...rows] });
  const a = syntheticDocument("source", [table("p1-t1", [["직접 온실가스(Scope 1)", "tCO2eq", "1,200", "1,530"]]), table("p1-t2", [["총 에너지 사용량", "TJ", "45.3", "47.1"]])]);
  const b = syntheticDocument("report", [table("p1-t1", [["직접 온실가스(Scope 1)", "tCO2eq", "1,200", "1,350"]]), table("p1-t2", [["총 에너지 사용량", "TJ", "45.3", "47.1"], ["사업장 수", "개소", "12", "14"]])]);
  writeFileSync(join(dir, "a.json"), JSON.stringify(asDocumentJson(a)));
  writeFileSync(join(dir, "b.json"), JSON.stringify(asDocumentJson(b)));
  return { a: join(dir, "a.json"), b: join(dir, "b.json") };
}

async function run(args: string[], deps: ReturnType<typeof fakeDeps>["deps"]) {
  const lines: string[] = [];
  const code = await main(args, (line) => lines.push(line), () => undefined, deps);
  return { code, output: lines.join("\n") };
}

const records = (path: string): CallRecord[] => readFileSync(path, "utf8").trim().split("\n").filter((line) => line !== "").map((line) => JSON.parse(line) as CallRecord);
const LIVE = ["--provider", "openai", "--route", "light-openai"];

interface Stdout {
  provider: string;
  model: string;
  providerCalls: number;
  spend: { limitUsd: number; spentBeforeUsd: number; spentUsd: number };
  labeling: Record<"a" | "b", { llmCalls: number; resumedUnits: number; skippedUnits: number; llmFailures: { unitId: string; kind: string }[]; costUsd: number; resumedCostUsd: number }>;
  pairs: Record<string, number>;
  a: { unverifiedByReason: Record<string, number> };
}

describe("실제 공급사 배선 (가짜 SDK 클라이언트)", () => {
  it("키가 없으면 SDK 클라이언트를 만들지 않고 명시적 오류로 끝난다", async () => {
    const { a, b } = inputs();
    for (const env of [{}, { OPENAI_API_KEY: "" }, { ANTHROPIC_API_KEY: KEY_SYNTHETIC }]) {
      const fake = fakeDeps(env);
      for (const command of ["run", "transcribe"]) {
        const { code, output } = await run([command, a, b, ...LIVE, "--limit-usd", "0.5", "--tables", "a:p1-t1", "--out", tempDir()], fake.deps);
        expect(code).toBe(2);
        expect(output).toContain("OPENAI_API_KEY");
      }
      expect(fake.keys).toEqual([]);
      expect(fake.bodies).toEqual([]);
    }
  });

  it("경로·상한이 없거나 다른 공급사의 모델이 섞인 경로면 부르지 않는다", async () => {
    const { a, b } = inputs();
    const fake = fakeDeps();
    const base = ["run", a, b, "--out", tempDir()];
    expect((await run([...base, ...LIVE], fake.deps)).output).toContain("--limit-usd");
    expect((await run([...base, "--provider", "openai", "--limit-usd", "0.5"], fake.deps)).output).toContain("--route");
    // `light` 는 대체 모델(다른 공급사)이 있는 경로다.
    const mixed = await run([...base, "--provider", "openai", "--route", "light", "--limit-usd", "0.5"], fake.deps);
    expect(mixed.output).toContain("openai 가 아닌 모델");
    for (const args of [[...base, ...LIVE], [...base, "--provider", "openai", "--limit-usd", "0.5"], [...base, "--provider", "openai", "--route", "light", "--limit-usd", "0.5"], [...base, ...LIVE, "--limit-usd", "0"], [...base, ...LIVE, "--route", "light-anthropic", "--limit-usd", "0.5"]]) {
      expect((await run(args, fake.deps)).code).toBe(2);
    }
    expect(fake.bodies).toEqual([]);
  });

  it("끝까지 잇는다: 요청은 경로의 모델 하나로만 가고, 호출 기록·요청 단위 결과·실행 정보가 남는다", async () => {
    const { a, b } = inputs();
    const out = tempDir();
    const fake = fakeDeps();
    const { code, output } = await run(["run", a, b, ...LIVE, "--limit-usd", "0.5", "--store-bodies", "--out", out], fake.deps);
    expect(code).toBe(0);
    expect(fake.keys).toEqual([KEY_SYNTHETIC]);
    expect(fake.bodies).toHaveLength(4);
    expect(new Set(fake.bodies.map((body) => body.model))).toEqual(new Set(["gpt-6-luna"]));
    const stdout = JSON.parse(output) as Stdout & { synthetic?: string };
    expect(stdout.synthetic).toBeUndefined();
    expect(stdout).toMatchObject({ provider: "openai", model: "gpt-6-luna", providerCalls: 4, pairs: { match: 3, mismatch: 1, needs_explanation: 0 }, spend: { limitUsd: 0.5, spentBeforeUsd: 0, spentUsd: 0.0024 } });
    const calls = records(join(out, "calls.jsonl"));
    expect(calls).toHaveLength(4);
    for (const call of calls) expect(call).toMatchObject({ provider: "openai", apiModelId: "gpt-6-luna", responseModelId: "gpt-6-luna", promptId: "reconcile/label@1", resultKind: "success", costUsd: 0.0006, thinkingTokens: 400, tenant: "measure", module: "reconcile" });
    expect(calls[0]?.body?.responseText).toContain("labels");
    // 키 값은 어디에도 남지 않는다.
    for (const file of ["calls.jsonl", "units.jsonl", "result.json", "summary.md"]) expect(readFileSync(join(out, file), "utf8")).not.toContain(KEY_SYNTHETIC);
    expect(output).not.toContain(KEY_SYNTHETIC);
    const result = JSON.parse(readFileSync(join(out, "result.json"), "utf8")) as { synthetic?: string; run: { provider: string; route: string; model: string; units: string } };
    expect(result.synthetic).toBeUndefined();
    expect(result.run).toMatchObject({ provider: "openai", route: "light-openai", model: "gpt-6-luna", units: join(out, "units.jsonl") });
    expect(readFileSync(join(out, "summary.md"), "utf8")).not.toContain("모의 실행이다");
  });

  it("이어 하기: 같은 --out 으로 다시 돌리면 답을 받은 표는 다시 부르지 않고 결과는 같다", async () => {
    const { a, b } = inputs();
    const out = tempDir();
    const first = fakeDeps();
    const one = JSON.parse((await run(["run", a, b, ...LIVE, "--limit-usd", "0.5", "--out", out], first.deps)).output) as Stdout;
    const second = fakeDeps();
    const two = JSON.parse((await run(["run", a, b, ...LIVE, "--limit-usd", "0.5", "--out", out], second.deps)).output) as Stdout;
    expect(second.bodies).toEqual([]);
    expect(two.providerCalls).toBe(0);
    expect(two.labeling.a).toMatchObject({ llmCalls: 0, resumedUnits: 2, costUsd: 0, resumedCostUsd: 0.0012 });
    expect(two.pairs).toEqual(one.pairs);
    // 앞선 실행이 쓴 비용이 상한 계산에 들어 있다.
    expect(two.spend).toMatchObject({ spentBeforeUsd: 0.0024, spentUsd: 0.0024 });
    expect(records(join(out, "calls.jsonl"))).toHaveLength(4);
  });

  it("상한에 닿으면 멈추고, 상한을 올려 이어 하면 막혔던 표만 부른다", async () => {
    const { a, b } = inputs();
    const out = tempDir();
    const first = fakeDeps();
    // 호출 하나가 0.0006 USD. 상한 0.001 이면 둘째 호출 뒤(0.0012)에 막힌다. 문서 안에서는 동시에 돌므로 문서 a 의 둘은 나가고 b 는 막힌다.
    const one = JSON.parse((await run(["run", a, b, ...LIVE, "--limit-usd", "0.001", "--out", out], first.deps)).output) as Stdout;
    expect(first.bodies).toHaveLength(2);
    expect(one.spend.spentUsd).toBe(0.0012);
    expect(one.labeling.b.llmFailures.map((failure) => failure.kind)).toEqual(["budget_exceeded", "budget_exceeded"]);
    expect(records(join(out, "calls.jsonl")).map((call) => call.resultKind)).toEqual(["success", "success", "budget_exceeded", "budget_exceeded"]);
    // 상한을 그대로 두고 이어 하면 앞선 비용 때문에 하나도 부르지 않는다.
    const again = fakeDeps();
    await run(["run", a, b, ...LIVE, "--limit-usd", "0.001", "--out", out], again.deps);
    expect(again.bodies).toEqual([]);
    const second = fakeDeps();
    const two = JSON.parse((await run(["run", a, b, ...LIVE, "--limit-usd", "0.5", "--out", out], second.deps)).output) as Stdout;
    expect(second.bodies).toHaveLength(2);
    expect(two.labeling.a).toMatchObject({ llmCalls: 0, resumedUnits: 2 });
    expect(two.labeling.b).toMatchObject({ llmCalls: 2, resumedUnits: 0 });
    expect(two.spend).toMatchObject({ spentBeforeUsd: 0.0012, spentUsd: 0.0024 });
    expect(two.pairs).toEqual({ match: 3, mismatch: 1, needs_explanation: 0 });
  });

  it("--tables: 고른 표만 모델에 보내고 나머지 숫자는 미확인(not_requested)으로 남는다", async () => {
    const { a, b } = inputs();
    const out = tempDir();
    const fake = fakeDeps();
    const stdout = JSON.parse((await run(["run", a, b, ...LIVE, "--limit-usd", "0.5", "--tables", "a:p1-t1,b:p1-t1", "--out", out], fake.deps)).output) as Stdout;
    expect(fake.bodies).toHaveLength(2);
    expect(stdout.labeling.a).toMatchObject({ llmCalls: 1, skippedUnits: 1 });
    expect(stdout.a.unverifiedByReason).toMatchObject({ not_requested: 2 });
    expect(stdout.pairs).toEqual({ match: 1, mismatch: 1, needs_explanation: 0 });
    const unknown = await run(["run", a, b, ...LIVE, "--limit-usd", "0.5", "--tables", "a:p9-t9", "--out", tempDir()], fake.deps);
    expect(unknown.code).toBe(1);
    expect(unknown.output).toContain("뜻 붙이기 대상에 없는 표");
    // 작은 실행 뒤에 같은 --out 으로 전체를 돌리면 남은 표만 부른다.
    const rest = fakeDeps();
    await run(["run", a, b, ...LIVE, "--limit-usd", "0.5", "--out", out], rest.deps);
    expect(rest.bodies).toHaveLength(2);
  });

  it("옮겨 쓰기 시험: 고른 표의 칸을 옮겨 적게 하고 원문과 칸마다 견준다. 이어 하면 다시 부르지 않는다", async () => {
    const { a, b } = inputs();
    const out = tempDir();
    // 가짜 모델이 한 칸의 쉼표를 빼고(표기만 다름), 한 칸의 숫자를 바꾼다(다름).
    const fake = fakeDeps(undefined, (target, text) => (target === "r1c2" ? text.replace(",", "") : target === "r1c3" ? "1,580" : text));
    const { code, output } = await run(["transcribe", a, b, ...LIVE, "--limit-usd", "0.5", "--tables", "a:p1-t1", "--store-bodies", "--out", out], fake.deps);
    expect(code).toBe(0);
    expect(fake.bodies).toHaveLength(1);
    const stdout = JSON.parse(output) as { providerCalls: number; total: Record<string, number> };
    expect(stdout.total).toMatchObject({ cells: 2, same: 0, notationOnly: 1, different: 1, charsChanged: 1 });
    expect(records(join(out, "calls.jsonl")).map((call) => [call.promptId, call.apiModelId, call.module])).toEqual([["reconcile/transcribe@1", "gpt-6-luna", "reconcile-transcribe"]]);
    const again = fakeDeps();
    expect(JSON.parse((await run(["transcribe", a, b, ...LIVE, "--limit-usd", "0.5", "--tables", "a:p1-t1", "--out", out], again.deps)).output)).toMatchObject({ providerCalls: 0, resumedUnits: 1, total: { notationOnly: 1, charsChanged: 1 } });
    expect(again.bodies).toEqual([]);
    expect((await run(["transcribe", a, b, ...LIVE, "--limit-usd", "0.5", "--out", tempDir()], fake.deps)).code).toBe(2);
  });

  it("옮겨 쓰기 시험도 상한에 닿으면 멈추고, 이어 할 때 앞선 비용을 같이 센다", async () => {
    const { a, b } = inputs();
    const out = tempDir();
    const fake = fakeDeps();
    // 호출 하나가 0.0006 USD. 상한 0.0005 면 첫 호출 뒤에 막힌다.
    const args = ["transcribe", a, b, ...LIVE, "--tables", "a:p1-t1,a:p1-t2,b:p1-t1", "--out", out];
    const one = JSON.parse((await run([...args, "--limit-usd", "0.0005"], fake.deps)).output) as { providerCalls: number; costUsd: number; failedUnits: { unitId: string; kind: string }[]; total: Record<string, number> };
    expect(fake.bodies).toHaveLength(1);
    expect(one).toMatchObject({ providerCalls: 1, costUsd: 0.0006, failedUnits: [{ unitId: "a:p1-t2", kind: "budget_exceeded" }, { unitId: "b:p1-t1", kind: "budget_exceeded" }] });
    // 막힌 요청의 칸은 분모에 남고 「빠짐」으로 센다.
    expect(one.total).toMatchObject({ cells: 6, same: 2, missing: 4, inFailedUnits: 4 });
    const again = fakeDeps();
    await run([...args, "--limit-usd", "0.0005"], again.deps);
    expect(again.bodies).toEqual([]);
    const rest = fakeDeps();
    expect(JSON.parse((await run([...args, "--limit-usd", "0.5"], rest.deps)).output)).toMatchObject({ providerCalls: 2, resumedUnits: 1, spentBeforeUsd: 0.0006, total: { cells: 6, same: 6 } });
  });

  it("옮겨 쓰기 시험의 모의 실행과 dry-run 은 키 없이 돈다", async () => {
    const { a, b } = inputs();
    const out = tempDir();
    const none = fakeDeps({});
    const mock = await run(["transcribe", a, b, "--provider", "mock", "--tables", "a:p1-t1,b:p1-t2", "--out", out], none.deps);
    expect(mock.code).toBe(0);
    expect(JSON.parse(mock.output)).toMatchObject({ synthetic: expect.stringContaining("synthetic") as string, providerCalls: 2, total: { cells: 6, same: 6, different: 0 } });
    const dry = await run(["transcribe", a, b, "--dry-run", "--route", "light-openai", "--tables", "a:p1-t1", "--out", tempDir()], none.deps);
    expect(dry.code).toBe(0);
    expect(dry.output).toContain("모델 gpt-6-luna · 호출 수 1 · 옮겨 적을 칸 2");
    expect(none.keys).toEqual([]);
  });

  it("score: 저장된 실행 결과를 정답과 견줘 채점표를 낸다 (호출 없음)", async () => {
    const { a, b } = inputs();
    const runDir = tempDir();
    const transcribeDir = tempDir();
    const fake = fakeDeps();
    await run(["run", a, b, ...LIVE, "--limit-usd", "0.5", "--store-bodies", "--out", runDir], fake.deps);
    await run(["transcribe", a, b, ...LIVE, "--limit-usd", "0.5", "--tables", "b:p1-t2", "--out", transcribeDir], fake.deps);
    const called = fake.bodies.length;
    const catalogSha = (JSON.parse(readFileSync(join(runDir, "result.json"), "utf8")) as { inputs: { catalog: { id: string; sha256: string } } }).inputs.catalog;
    const cell = (id: string, raw: string, patch: Record<string, unknown>) => ({ cell: id, raw, kind: "indicator", indicator: null, period: null, unit: null, boundary: "unspecified", ...patch });
    const gold = {
      id: "gold-synthetic",
      status: "테스트용 가상 정답 (synthetic)",
      author: "test",
      createdOn: "2026-10-04",
      note: "",
      conventions: [],
      catalog: catalogSha,
      documents: { a: { fileName: "source.synthetic.pdf", pdfSha256: "0" }, b: { fileName: "report.synthetic.pdf", pdfSha256: "0" } },
      tables: [
        { doc: "a", tableId: "p1-t1", page: 1, why: "", cells: [cell("r1c2", "1,200", { indicator: "ghg.scope1", period: "2023", unit: "tCO2eq" }), cell("r1c3", "1,530", { indicator: "ghg.scope1", period: "2023", unit: "tCO2eq" })] },
        { doc: "b", tableId: "p1-t2", page: 1, why: "", cells: [cell("r1c2", "45.3", { indicator: "energy.total", period: "2023", unit: "TJ" }), cell("r1c3", "47.1", { indicator: "energy.total", period: "2024", unit: "TJ" }), cell("r2c2", "12", { kind: "not_in_catalog", period: "2023", unit: "개소" }), cell("r2c3", "14", { kind: "not_in_catalog", period: "2024", unit: "개소" })] },
      ],
    };
    const goldPath = join(tempDir(), "gold.json");
    writeFileSync(goldPath, JSON.stringify(gold));
    const out = tempDir();
    const scored = await run(["score", a, b, "--gold", goldPath, "--run", runDir, "--transcribe", transcribeDir, "--out", out], fakeDeps({}).deps);
    expect(scored.code).toBe(0);
    expect(fake.bodies).toHaveLength(called);
    const report = JSON.parse(readFileSync(join(out, "score.json"), "utf8")) as { score: { accuracy: { total: Record<string, number> }; restraint: { total: Record<string, number> }; errorCounts: Record<string, number> }; usage: { lines: number; costUsd: number; recomputedCostUsd: number; byApiModel: Record<string, number>; perTarget: { targets: number; outputTokensPerTarget: number } }; transcribe: { report: { total: Record<string, number> } } };
    // 정답에 일부러 넣은 틀린 기간(a 의 r1c3 은 2024 열이다) 하나가 「기간 틀림」으로 잡힌다.
    expect(report.score.accuracy.total).toMatchObject({ indicatorCells: 4, answered: 4, indicatorOk: 4, periodOk: 3, unitOk: 4, boundaryOk: 4, allFour: 3 });
    expect(report.score.errorCounts).toMatchObject({ period_wrong_year: 1 });
    expect(report.score.restraint.total).toMatchObject({ outsideCells: 2, keptOutside: 2, forced: 0 });
    expect(report.usage).toMatchObject({ lines: 4, costUsd: 0.0024, recomputedCostUsd: 0.0024, byApiModel: { "gpt-6-luna": 4 }, perTarget: { targets: 10, outputTokensPerTarget: 400 } });
    expect(report.transcribe.report.total).toMatchObject({ cells: 4, same: 4 });
    const md = readFileSync(join(out, "score.md"), "utf8");
    expect(md).toContain("| 전체 | 4 | 4 | 4/4 (100.0%) | 3/4 (75.0%)");
    expect(md).toContain("## 6. 옮겨 쓰기 비교");
    // 정답이 문서와 맞지 않으면 채점하지 않는다.
    writeFileSync(goldPath, JSON.stringify({ ...gold, tables: [{ ...gold.tables[0], cells: [gold.tables[0]?.cells[0]] }] }));
    const broken = await run(["score", a, b, "--gold", goldPath, "--run", runDir, "--out", tempDir()], fakeDeps({}).deps);
    expect(broken.code).toBe(1);
    expect(broken.output).toContain("에 정답이 없다");
    expect(existsSync(join(runDir, "units.jsonl"))).toBe(true);
  });
});
