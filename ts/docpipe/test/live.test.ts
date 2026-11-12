import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { CallRecord, OpenAILike } from "llm";
import { describe, expect, it } from "vitest";
import { main } from "../src/cli.js";
import { fixtureDocument, tempDir } from "./helpers.js";

// 실제 공급사로 가는 호출은 없다. SDK 클라이언트 자리에 가짜를 넣어 CLI 의 배선만 본다. 키, 토큰 수, 응답은 전부 가상(synthetic) 값이다.
const KEY_SYNTHETIC = "key-synthetic";
const LIVE = ["--provider", "openai", "--route", "light-openai"];

type Create = OpenAILike["responses"]["create"];
type Body = Parameters<Create>[0];

/** 번역에는 원문을 그대로(숫자가 보존된다), 그린워싱에는 빈 판정을 답하는 가짜. 한 번에 입력 1000 + 출력 1000 토큰(gpt-6-luna 단가로 0.0006 USD). */
function reply(body: Body): string {
  if ((body.text?.format as { name?: string } | undefined)?.name === "greenwash_findings") return JSON.stringify({ findings: [] });
  const input = body.input as { content: { text: string }[] }[];
  const text = input[0]?.content[0]?.text ?? "";
  return JSON.stringify({ items: JSON.parse(text.slice(text.indexOf("[\n"))) as unknown });
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

function documentFile(): string {
  const path = join(tempDir(), "document.json");
  writeFileSync(path, JSON.stringify(fixtureDocument()));
  return path;
}

async function run(argv: string[], deps: ReturnType<typeof fakeDeps>["deps"]) {
  const lines: string[] = [];
  const code = await main(argv, (line) => lines.push(line), () => undefined, deps);
  return { code, text: lines.join("\n") };
}

interface Summary {
  provider: string;
  synthetic: false | string;
  plannedChunks: number;
  chunks: { total: number; done: number; failed: number; skipped: number };
  providerCallsThisRun: number;
  costUsd?: number;
  costUsdSyntheticPricing?: number;
  failures: { kind: string }[];
}

describe("실제 공급사 배선 (가짜 SDK 클라이언트)", () => {
  it("키가 없으면 SDK 클라이언트를 만들지 않고 명시적 오류로 끝난다", async () => {
    for (const task of ["translate", "greenwash"]) {
      const fake = fakeDeps({});
      const { code, text } = await run([task, documentFile(), ...LIVE, "--limit-usd", "1", "--out", tempDir()], fake.deps);
      expect(code).toBe(2);
      expect(text).toContain("OPENAI_API_KEY");
      expect(fake.keys).toEqual([]);
      expect(fake.bodies).toEqual([]);
    }
  });

  it("경로·상한이 없거나 다른 공급사의 모델이 섞인 경로면 부르지 않는다", async () => {
    const fake = fakeDeps();
    const path = documentFile();
    const noLimit = await run(["translate", path, ...LIVE], fake.deps);
    expect(noLimit.code).toBe(2);
    expect(noLimit.text).toContain("--limit-usd");
    const noRoute = await run(["translate", path, "--provider", "openai", "--limit-usd", "1"], fake.deps);
    expect(noRoute.code).toBe(2);
    expect(noRoute.text).toContain("--route");
    expect((await run(["translate", path, "--provider", "openai", "--route", "light", "--limit-usd", "1"], fake.deps)).code).toBe(2);
    expect((await run(["translate", path, "--provider", "anthropic", "--route", "light", "--limit-usd", "1"], fake.deps)).code).toBe(2);
    expect(fake.bodies).toEqual([]);
  });

  it("translate: 키가 있으면 경로의 모델로 끝까지 돌고, --max-chunks 로 끊어 돌린 뒤 같은 자리에서 이어 한다", async () => {
    const fake = fakeDeps();
    const out = tempDir();
    const argv = ["translate", documentFile(), ...LIVE, "--limit-usd", "1", "--budget-tokens", "400", "--out", out];

    const head = await run([...argv, "--max-chunks", "2"], fake.deps);
    expect(head.code).toBe(0);
    const first = JSON.parse(head.text) as Summary;
    expect(first).toMatchObject({ provider: "openai", synthetic: false, chunks: { total: 2, done: 2, failed: 0 }, providerCallsThisRun: 2, costUsd: 0.0012 });
    expect(first.costUsdSyntheticPricing).toBeUndefined();
    expect(first.plannedChunks).toBeGreaterThan(2);
    expect(fake.keys).toEqual([KEY_SYNTHETIC]);
    expect(new Set(fake.bodies.map((body) => body.model))).toEqual(new Set(["gpt-6-luna"]));

    const rest = JSON.parse((await run(argv, fake.deps)).text) as Summary;
    expect(rest.chunks).toMatchObject({ total: first.plannedChunks, skipped: 2, done: first.plannedChunks - 2, failed: 0 });
    expect(fake.bodies).toHaveLength(first.plannedChunks);

    const records = readFileSync(join(out, "calls.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line) as CallRecord);
    expect(records).toHaveLength(first.plannedChunks);
    expect(records.every((record) => record.provider === "openai" && record.responseModelId === "gpt-6-luna" && record.body === undefined)).toBe(true);
    expect(readFileSync(join(out, "calls.jsonl"), "utf8")).not.toContain(KEY_SYNTHETIC);
    expect(existsSync(join(out, "result.json"))).toBe(true);
  });

  it("비용 상한이 호출 계층에 전달돼 상한에 닿은 뒤로는 공급사를 부르지 않고 남은 조각은 budget_exceeded 로 실패한다", async () => {
    const fake = fakeDeps();
    // 한 번에 0.0006 USD 이므로 상한 0.0005 는 첫 호출 뒤에 닿는다. 동시 실행 1 이라 넘겨 부르는 호출이 없다.
    const { code, text } = await run(["translate", documentFile(), ...LIVE, "--limit-usd", "0.0005", "--budget-tokens", "400", "--concurrency", "1", "--out", tempDir()], fake.deps);
    expect(code).toBe(3);
    const summary = JSON.parse(text) as Summary;
    expect(fake.bodies).toHaveLength(1);
    expect(summary.providerCallsThisRun).toBe(1);
    expect(summary.chunks).toMatchObject({ done: 1, failed: summary.plannedChunks - 1 });
    expect(new Set(summary.failures.map((failure) => failure.kind))).toEqual(new Set(["budget_exceeded"]));
  });

  it("greenwash: 실제 공급사 경로로 돌고, --store-bodies 를 줄 때만 호출 기록에 본문이 남는다", async () => {
    const fake = fakeDeps();
    const out = tempDir();
    const { code, text } = await run(["greenwash", documentFile(), ...LIVE, "--limit-usd", "1", "--out", out, "--store-bodies"], fake.deps);
    expect(code).toBe(0);
    expect(JSON.parse(text)).toMatchObject({ provider: "openai", synthetic: false });
    const records = readFileSync(join(out, "calls.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line) as CallRecord);
    expect(records.length).toBeGreaterThan(0);
    expect(records.every((record) => record.body !== undefined)).toBe(true);
  });
});
