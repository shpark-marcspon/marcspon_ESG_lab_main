import { existsSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { CallRecord, OpenAILike } from "llm";
import { describe, expect, it } from "vitest";
import { main } from "../src/cli.js";
import type { EvalReport } from "../src/evaluate.js";

// 실제 공급사로 가는 호출은 없다. SDK 클라이언트 자리에 가짜를 넣어 CLI 의 배선만 본다. 키, 토큰 수, 응답은 전부 가상(synthetic) 값이다.
const KEY_SYNTHETIC = "key-synthetic";
const EXAMPLES = "../data/scope3-examples";
const LIVE = ["eval", EXAMPLES, "--provider", "openai", "--route", "light-openai"];

type Create = OpenAILike["responses"]["create"];
type Body = Parameters<Create>[0];

/** 분류에는 카테고리 1, 추천에는 스키마가 허용한 첫 후보 id 를 답하는 가짜. 한 번에 입력 1000 + 출력 1000 토큰(gpt-6-luna 단가로 0.0006 USD). */
function reply(body: Body): string {
  const format = body.text?.format as { name?: string; schema?: { properties?: { picks?: { items?: { properties?: { factor_id?: { enum?: string[] } } } } } } } | undefined;
  if (format?.name === "scope3_classification") return JSON.stringify({ category: 1, keywords_en: ["steel"], confidence: "high", reason: "가짜 응답 (synthetic)" });
  const first = format?.schema?.properties?.picks?.items?.properties?.factor_id?.enum?.[0];
  if (!first) throw new Error("추천 스키마에 후보 id 열거형이 없다");
  return JSON.stringify({ picks: [{ factor_id: first, reason: "가짜 응답 (synthetic)", confidence: "low" }], none_suitable: false });
}

function fakeDeps(env: Record<string, string | undefined> = { OPENAI_API_KEY: KEY_SYNTHETIC }) {
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
          output: [{ type: "message", id: "msg", role: "assistant", status: "completed", content: [{ type: "output_text", text: reply(body), annotations: [] }] }],
          usage: { input_tokens: 1000, input_tokens_details: { cached_tokens: 0, cache_write_tokens: 0 }, output_tokens: 1000, output_tokens_details: { reasoning_tokens: 0 }, total_tokens: 2000 },
        } as unknown as Awaited<ReturnType<Create>>);
      },
    },
  };
  return {
    keys,
    bodies,
    deps: {
      env,
      openaiClient: (apiKey: string) => {
        keys.push(apiKey);
        return client;
      },
    },
  };
}

async function run(args: string[], deps: ReturnType<typeof fakeDeps>["deps"]) {
  const lines: string[] = [];
  const code = await main(args, (line) => lines.push(line), deps);
  return { code, output: lines.join("\n") };
}

describe("실제 공급사 배선 (가짜 SDK 클라이언트)", () => {
  it("키가 없으면 SDK 클라이언트를 만들지 않고 명시적 오류로 끝난다", async () => {
    for (const env of [{}, { OPENAI_API_KEY: "" }]) {
      const fake = fakeDeps(env);
      const { code, output } = await run([...LIVE, "--limit-usd", "0.6"], fake.deps);
      expect(code).toBe(2);
      expect(output).toContain("OPENAI_API_KEY");
      expect(fake.keys).toEqual([]);
      expect(fake.bodies).toEqual([]);
    }
  });

  it("경로·상한이 없거나 다른 공급사의 모델이 섞인 경로면 부르지 않는다", async () => {
    const fake = fakeDeps();
    expect((await run([...LIVE], fake.deps)).output).toContain("--limit-usd");
    expect((await run(["eval", EXAMPLES, "--provider", "openai", "--limit-usd", "0.6"], fake.deps)).output).toContain("--route");
    for (const args of [[...LIVE], ["eval", EXAMPLES, "--provider", "openai", "--limit-usd", "0.6"], ["eval", EXAMPLES, "--provider", "openai", "--route", "light", "--limit-usd", "0.6"], ["eval", EXAMPLES, "--provider", "anthropic", "--route", "light", "--limit-usd", "0.6"]]) {
      expect((await run(args, fake.deps)).code).toBe(2);
    }
    expect(fake.bodies).toEqual([]);
  });

  it("키가 있으면 경로의 모델로 끝까지 돌고 호출 기록·행별 판단·지표를 남긴다", async () => {
    const fake = fakeDeps();
    const dir = mkdtempSync(join(tmpdir(), "scope3-live-"));
    const log = join(dir, "calls.jsonl");
    const { code, output } = await run([...LIVE, "--limit-usd", "0.6", "--limit-rows", "12", "--log", log, "--out", dir, "--json"], fake.deps);
    expect(code).toBe(0);
    expect(fake.keys).toEqual([KEY_SYNTHETIC]);
    expect(fake.bodies.length).toBeGreaterThan(0);
    expect(new Set(fake.bodies.map((body) => body.model))).toEqual(new Set(["gpt-6-luna"]));

    const summary = JSON.parse(output) as { provider: string; syntheticProvider: boolean; route: string; model: string; rowsEvaluated: number; rowsInSet: number; report: EvalReport };
    expect(summary).toMatchObject({ provider: "openai", syntheticProvider: false, route: "light-openai", model: "gpt-6-luna", rowsEvaluated: 12, rowsInSet: 148 });
    expect(summary.report.llm.attempts).toBe(fake.bodies.length);
    expect(summary.report.llm.costUsd).toBeCloseTo(fake.bodies.length * 0.0006, 8);

    const records = readFileSync(log, "utf8").trim().split("\n").map((line) => JSON.parse(line) as CallRecord);
    expect(records).toHaveLength(fake.bodies.length);
    expect(records.every((record) => record.provider === "openai" && record.responseModelId === "gpt-6-luna" && record.body === undefined)).toBe(true);
    expect(readFileSync(log, "utf8")).not.toContain(KEY_SYNTHETIC);
    expect(JSON.parse(readFileSync(join(dir, "rows.json"), "utf8"))).toHaveLength(12);
    expect(existsSync(join(dir, "report.json"))).toBe(true);
  });

  it("비용 상한이 호출 계층에 전달돼 상한에 닿은 뒤로는 공급사를 부르지 않는다", async () => {
    const fake = fakeDeps();
    // 한 번에 0.0006 USD 이므로 상한 0.0005 는 첫 호출 뒤에 닿는다.
    const { code, output } = await run([...LIVE, "--limit-usd", "0.0005", "--limit-rows", "12", "--json"], fake.deps);
    expect(code).toBe(0);
    expect(fake.bodies).toHaveLength(1);
    const { report } = JSON.parse(output) as { report: EvalReport };
    expect(report.llm.attempts).toBe(1);
    expect(Object.keys(report.llmFailures).some((key) => key.endsWith(":budget_exceeded"))).toBe(true);
  });

  it("--store-bodies 를 줄 때만 호출 기록에 본문이 남는다", async () => {
    const fake = fakeDeps();
    const log = join(mkdtempSync(join(tmpdir(), "scope3-live-")), "calls.jsonl");
    await run([...LIVE, "--limit-usd", "0.6", "--limit-rows", "12", "--log", log, "--store-bodies"], fake.deps);
    const records = readFileSync(log, "utf8").trim().split("\n").map((line) => JSON.parse(line) as CallRecord);
    expect(records.length).toBeGreaterThan(0);
    expect(records.every((record) => record.body !== undefined)).toBe(true);
  });
});

describe("후보 추리기 재현율", () => {
  it("추천 호출까지 간 행과 가지 못한 행을 나눠 세고, 합은 기억 적중을 뺀 정답 있는 행 수다", async () => {
    const { output } = await run(["eval", EXAMPLES, "--provider", "mock", "--json"], fakeDeps({}).deps);
    const { report } = JSON.parse(output) as { report: EvalReport };
    const recall = report.shortlistRecall;
    expect(recall.evaluated + recall.notReached).toBe(report.top3LlmOnly.factor_id.evaluated);
    expect(recall.hits).toBeLessThanOrEqual(recall.evaluated);
    // 추린 목록에 정답이 없으면 top-3 에도 있을 수 없다.
    expect(report.top3LlmOnly.factor_id.hits).toBeLessThanOrEqual(recall.hits);
    expect(recall.correctCategory.evaluated).toBeLessThanOrEqual(recall.evaluated);
  });

  it("추린 수가 1 이면 정답이 목록 밖으로 밀려 재현율이 떨어진다", async () => {
    const at = async (size: string) => (JSON.parse((await run(["eval", EXAMPLES, "--provider", "mock", "--json", "--shortlist", size], fakeDeps({}).deps)).output) as { report: EvalReport }).report.shortlistRecall;
    const [narrow, wide] = [await at("1"), await at("100")];
    expect(narrow.hits).toBeLessThan(wide.hits);
  });
});
