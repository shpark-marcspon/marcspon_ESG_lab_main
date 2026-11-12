import { parseArgs } from "node:util";
import { BudgetGuard } from "./budget.js";
import { LlmClient } from "./client.js";
import { loadConfig, type LlmConfig } from "./config.js";
import { defaultConfigPaths, defaultPromptRoot } from "./defaults.js";
import { LIVE_PROVIDERS, LiveSetupError, liveProviders, parseRequestsPerMinute, requireLimitUsd, type LiveDeps } from "./live.js";
import { JsonlFileSink, MemorySink } from "./log.js";
import { PROBE_CHECKS, recordingAnthropic, recordingGemini, recordingOpenAI, runProbe, type ProbeCheckName, type RawExchange } from "./probe.js";
import { FilePromptStore } from "./prompts.js";
import { AnthropicProvider, createAnthropicClient } from "./providers/anthropic.js";
import { GeminiProvider, createGeminiClient } from "./providers/gemini.js";
import { MockProvider } from "./providers/mock.js";
import { OpenAIProvider, createOpenAIClient } from "./providers/openai.js";

const USAGE = `사용법:
  tsx src/cli.ts call --prompt <모듈/이름@버전> --provider <이름> [--dry-run]
      [--route <경로>] [--var 이름=값]... [--tenant <t>] [--module <m>]
      [--mock-text <응답>] [--log <jsonl 경로>] [--limit-usd <상한>] [--store-bodies]
      [--max-output-tokens <n>] [--timeout-ms <n>] [--rpm <분당 요청 수>]
  tsx src/cli.ts probe --provider <${LIVE_PROVIDERS.join("|")}> --route <경로> --limit-usd <상한>
      [--only ${PROBE_CHECKS.join(",")}] [--log <jsonl 경로>] [--store-bodies]

--dry-run 은 공급사를 부르지 않고 조립된 요청과 어림 토큰·비용만 출력한다.
--provider mock 은 모의 공급사를 부른다 (키가 필요 없다).
--provider ${LIVE_PROVIDERS.join("|")} 는 실제 공급사를 부른다. 돈이 든다. --route 와 --limit-usd 가 필요하고\n키는 환경 변수(OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY 중 그 공급사의 것)로 받는다.
probe 는 실제 공급사에 작은 호출 몇 개를 보내 어댑터의 가정(요청 모양, 구조화 출력, 출력 잘림, 시간 초과, 캐시 토큰)을 확인한다.
--store-bodies 는 호출 기록에 요청 본문과 응답 글자를 그대로 남긴다 (기본은 해시만).
--rpm 은 실제 공급사 호출의 시작 간격을 벌려 분당 그 수를 넘지 않게 한다 (분당 한도가 있는 키용).`;

/** 첫 모델이 주어진 공급사인 경로를 찾는다. */
function routeFor(config: LlmConfig, provider: string): string {
  for (const [name, keys] of Object.entries(config.routes)) {
    if (config.models[keys[0] as string]?.provider === provider) return name;
  }
  throw new Error(`공급사 ${provider} 로 시작하는 경로가 설정에 없다`);
}

function positive(name: string, raw: string | undefined): number | undefined {
  if (raw === undefined) return undefined;
  const parsed = Number(raw);
  if (!Number.isInteger(parsed) || parsed <= 0) throw new Error(`--${name} 는 양의 정수여야 한다: ${raw}`);
  return parsed;
}

// 키는 여기(CLI 조립 지점)에서만 환경에서 읽어 넘긴다. 라이브러리는 환경 변수를 읽지 않는다.
export async function main(argv: string[], out: (line: string) => void = console.log, deps: LiveDeps = { env: process.env }): Promise<number> {
  const { values, positionals } = parseArgs({
    args: argv,
    allowPositionals: true,
    options: {
      prompt: { type: "string" },
      provider: { type: "string" },
      route: { type: "string" },
      var: { type: "string", multiple: true },
      tenant: { type: "string", default: "cli" },
      module: { type: "string", default: "cli" },
      "dry-run": { type: "boolean", default: false },
      "mock-text": { type: "string" },
      log: { type: "string" },
      "limit-usd": { type: "string" },
      "store-bodies": { type: "boolean", default: false },
      "max-output-tokens": { type: "string" },
      "timeout-ms": { type: "string" },
      only: { type: "string" },
      rpm: { type: "string" },
    },
  });
  const command = positionals[0];
  if ((command !== "call" && command !== "probe") || !values.provider || (command === "call" && !values.prompt)) {
    out(USAGE);
    return 2;
  }

  const config = loadConfig(defaultConfigPaths({ synthetic: true }));
  const prompts = new FilePromptStore(defaultPromptRoot());
  const log = values.log ? new JsonlFileSink(values.log) : new MemorySink();
  const tags = { tenant: values.tenant, module: values.module };
  const live = values.provider !== "mock" && !values["dry-run"];

  if (!live) {
    if (command === "probe") {
      out(`probe 는 실제 공급사에만 쓴다 (--provider ${LIVE_PROVIDERS.join("|")}). 모의 공급사와 --dry-run 으로는 확인할 것이 없다.`);
      return 2;
    }
    const vars = parseVars(values.var);
    const mock = new MockProvider();
    const client = new LlmClient({
      config,
      // 실제 공급사 어댑터는 클라이언트 없이 등록한다. 요청 조립(dry-run)만 할 수 있다.
      providers: { mock, "mock-b": new MockProvider("mock-b"), anthropic: new AnthropicProvider(), openai: new OpenAIProvider(), gemini: new GeminiProvider() },
      prompts,
      log,
      storeBodies: values["store-bodies"],
    });
    const request = { prompt: values.prompt as string, vars, route: values.route ?? routeFor(config, values.provider), tags };
    if (values["dry-run"]) {
      out(JSON.stringify(client.dryRun(request), null, 2));
      return 0;
    }
    if (values["mock-text"] !== undefined) mock.enqueueText(values["mock-text"]);
    const result = await client.call(request);
    out(JSON.stringify(result, null, 2));
    return result.kind === "success" ? 0 : 1;
  }

  // 실제 공급사. 경로와 상한을 명시하지 않으면 부르지 않는다.
  const raw: RawExchange[] = [];
  let client: LlmClient;
  let route: string;
  try {
    if (!values.route) throw new LiveSetupError("실제 공급사 호출에는 --route <경로> 가 필요하다");
    route = values.route;
    const limitUsd = requireLimitUsd(values["limit-usd"]);
    // 어댑터가 접어 버리는 원본 응답 모양을 보려고 SDK 클라이언트를 기록 래퍼로 감싼다.
    const providers = liveProviders(
      config,
      { provider: values.provider, route, ...parseRequestsPerMinute(values.rpm) },
      {
        env: deps.env,
        openaiClient: (apiKey) => recordingOpenAI((deps.openaiClient ?? createOpenAIClient)(apiKey), raw),
        anthropicClient: (apiKey) => recordingAnthropic((deps.anthropicClient ?? createAnthropicClient)(apiKey), raw),
        geminiClient: (apiKey) => recordingGemini((deps.geminiClient ?? createGeminiClient)(apiKey), raw),
      },
    );
    client = new LlmClient({
      config,
      providers,
      prompts,
      log,
      budget: new BudgetGuard([{ scope: {}, limitUsd }]),
      storeBodies: values["store-bodies"],
      // probe 는 어댑터가 받은 응답을 한 번씩 그대로 보는 확인이라 호출 계층이 다시 부르지 않게 한다.
      ...(command === "probe" ? { providerRetry: {} } : {}),
      ...(deps.sleep ? { sleep: deps.sleep } : {}),
    });
  } catch (error) {
    if (!(error instanceof LiveSetupError)) throw error;
    out(`실제 공급사 호출을 지원하지 않는다: ${error.message}`);
    return 2;
  }

  if (command === "probe") {
    const only = values.only?.split(",").filter((name) => name !== "");
    const unknown = (only ?? []).filter((name) => !(PROBE_CHECKS as readonly string[]).includes(name));
    if (unknown.length > 0) {
      out(`--only 에 모르는 확인이 있다: ${unknown.join(",")}`);
      return 2;
    }
    const timeoutProbeMs = positive("timeout-ms", values["timeout-ms"]);
    const report = await runProbe({
      client,
      route,
      tags: { ...tags, module: "probe" },
      raw,
      nonce: new Date().toISOString(),
      ...(only ? { only: only as ProbeCheckName[] } : {}),
      ...(timeoutProbeMs === undefined ? {} : { timeoutProbeMs }),
    });
    out(JSON.stringify(report, null, 2));
    return report.checks.every((check) => check.pass) ? 0 : 1;
  }

  const maxOutputTokens = positive("max-output-tokens", values["max-output-tokens"]);
  const timeoutMs = positive("timeout-ms", values["timeout-ms"]);
  const result = await client.call({
    prompt: values.prompt as string,
    vars: parseVars(values.var),
    route,
    tags,
    ...(maxOutputTokens === undefined ? {} : { maxOutputTokens }),
    ...(timeoutMs === undefined ? {} : { timeoutMs }),
  });
  out(JSON.stringify({ result, raw }, null, 2));
  return result.kind === "success" ? 0 : 1;
}

function parseVars(pairs: string[] | undefined): Record<string, string> {
  const vars: Record<string, string> = {};
  for (const pair of pairs ?? []) {
    const at = pair.indexOf("=");
    if (at <= 0) throw new Error(`--var 는 이름=값 꼴이어야 한다: ${pair}`);
    vars[pair.slice(0, at)] = pair.slice(at + 1);
  }
  return vars;
}

if (process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href) {
  main(process.argv.slice(2)).then(
    (code) => {
      process.exitCode = code;
    },
    (error: unknown) => {
      console.error(error instanceof Error ? `${error.name}: ${error.message}` : error);
      process.exitCode = 1;
    },
  );
}
