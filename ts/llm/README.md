# llm

공급사에 묶이지 않는 LLM 호출 계층. 프롬프트 버전, 구조화 출력 검증, 호출 기록, 비용 상한, 대체 호출을 한 곳에 둔다. AI 프레임워크 없이 공식 SDK(`@anthropic-ai/sdk`, `openai`, `@google/genai`)를 직접 쓴다 (근거: `PRJ11-RPT-5`).

키가 없어도 동작한다. 테스트는 모의 공급사와 가짜 SDK 클라이언트만 쓴다. 세 어댑터 모두 실제 호출(`probe` 6종)로 확인했다: OpenAI 는 2026-10-01 `gpt-6-luna`, Anthropic 은 2026-10-02 `claude-haiku-4-5`, Gemini 는 2026-10-02 `gemini-3.1-flash-lite` 와 `gemini-3.5-flash-lite`. 이미지 첨부는 OpenAI `gpt-6-luna` 에서만 실제로 불러 봤다(2026-10-04, 밴드 W: PNG 쪽 그림 한 장씩 34회. 그림 토큰은 `input_tokens` 에 들어 비용에 세어진다. 이 모델의 그림 토큰 곱수는 공급사 문서의 표에 없다). PDF 첨부와 거부 응답은 어느 공급사에서도 실제로 받아 보지 않았다.

실제 공급사는 `--provider <공급사> --route <그 공급사의 경로> --limit-usd <상한>` 으로만 부른다. 돈이 든다. 키는 그 공급사의 환경 변수로 명령 한 줄에만 실어 넘기고, 키·경로·상한 중 하나라도 없으면 부르지 않고 종료 코드 2 로 끝난다. 다른 공급사의 키가 환경에 있어도 쓰지 않는다.

| `--provider` | 경로 | 환경 변수 | SDK |
|---|---|---|---|
| `openai` | `light-openai` | `OPENAI_API_KEY` | `openai` (Responses API) |
| `anthropic` | `light-anthropic` | `ANTHROPIC_API_KEY` | `@anthropic-ai/sdk` (Messages API, 스트림) |
| `gemini` | `light-gemini` (3.1 Flash-Lite), `light-gemini-35` (3.5 Flash-Lite) | `GEMINI_API_KEY` | `@google/genai` (`models.generateContent`) |

호출 기록에 본문을 남기려면 `--store-bodies` 를 준다 (기본은 해시만). 분당 요청 한도가 있는 키는 `--rpm <n>` 으로 호출 시작 간격을 벌린다 (`throttled`). 기다리는 시간도 호출의 시간 제한 안에 들고, 호출 기록의 `durationMs` 에도 들어간다. `probe` 명령은 실제 공급사에 작은 호출 몇 개를 보내 어댑터의 가정(구조화 출력 수용, 출력 잘림, 시간 초과, 캐시 토큰)을 확인하고 원본 응답 모양을 출력한다.

```
cd ts && GEMINI_API_KEY=... pnpm -F llm exec tsx src/cli.ts probe --provider gemini --route light-gemini --limit-usd 0.20
```

공급사마다 다른 것 (어댑터 안에만 있다):

| | OpenAI | Anthropic | Gemini |
|---|---|---|---|
| 캐시 | 자동 (앞부분 일치). 쓰기 토큰이 따로 보고되고 설정 단가로는 입력의 1.25배 | `cache_control` 표시. 쓰기 1.25배, 읽기 0.1배 | 자동 (implicit). 쓰기 과금 없음, 읽기만 싸다 |
| 입력 토큰 필드 | 캐시 읽기·쓰기를 포함한 합계 | 캐시를 뺀 값 | 캐시 읽기를 포함한 합계 |
| 구조화 출력 | `text.format` strict | `output_config.format`. 길이·범위 제약(`minLength`, `maxItems` …)은 지우고 보낸다 | `responseMimeType` + `responseJsonSchema`. 지원 안 하는 속성은 공급사가 무시한다 |
| 시간 제한 | SDK `timeout` + 중단 신호 | SDK `timeout` + 중단 신호 | 중단 신호만 (SDK 의 `timeout` 은 서버 마감 시간이 돼 10초 미만이면 400) |
| 일시 오류 재시도 | SDK 가 2회 (기본값) | SDK 가 2회 (기본값) | 호출 계층이 2회 (SDK 의 `httpOptions.retryOptions` 는 켜지 않는다). 아래 「공급사 오류 재시도」 |

어느 경우든 값의 검증은 이 계층이 zod 로 다시 한다.

실제 호출 조립은 `liveProviders(config, { provider, route }, { env })` 한 곳이다. 환경은 CLI 가 넘기고 이 패키지는 `process.env` 를 읽지 않는다. 경로에 다른 공급사의 모델이 섞여 있으면 거부한다.

```
cd ts && pnpm -F llm test
cd ts && pnpm -F llm typecheck
cd ts && pnpm -F llm exec tsx src/cli.ts call --prompt example/hello@1 --provider mock --dry-run
```

## 진입점

```ts
import { z } from "zod";
import { BudgetGuard, FilePromptStore, JsonlFileSink, LlmClient, defaultConfigPaths, loadConfig } from "llm";

const client = new LlmClient({
  config: loadConfig(defaultConfigPaths({ synthetic: false })),
  providers: { anthropic, openai },              // 공급사 이름 → 어댑터 (아래)
  prompts: new FilePromptStore("prompts"),
  log: new JsonlFileSink("var/llm-calls.jsonl"),
  budget: new BudgetGuard([{ scope: { tenant: "t1" }, limitUsd: 5 }]),
});

const result = await client.call({
  prompt: "scope3/classify@1",                   // 모듈/이름@버전, 버전 필수
  vars: { item: "..." },
  route: "light",                                // 설정 파일의 경로 이름
  tags: { tenant: "t1", module: "scope3" },
  schema: z.object({ category: z.string() }),    // 없으면 data 는 응답 글자
});
```

호출하는 쪽은 공급사와 모델을 이름으로 부르지 않는다. `route` 만 고르고, 경로가 어떤 모델을 어떤 순서로 쓰는지는 `data/llm/models.json` 의 `routes` 가 정한다 (첫 모델이 주, 나머지가 대체 순서).

`client.dryRun(request)` 는 공급사를 부르지 않고 조립된 요청, 어림 토큰, 비용 상한, 경고를 돌려준다.

## 결과 (판별 유니온)

`result.kind` 로 나눈다. 실패는 빈 결과가 아니라 종류가 다른 값이다.

| kind | 뜻 | 대체 호출 | 주요 필드 |
|---|---|---|---|
| `success` | 완료, 스키마 통과 | | `data`, `text` |
| `schema_mismatch` | JSON 이 아니거나 스키마 불일치. 같은 모델로 `maxSchemaRetries`(기본 2)번 다시 부른 뒤에도 실패 | 안 함 | `issues`, `rawText` |
| `refusal` | 모델이 거부 | 안 함 | `text`, `category` |
| `truncated` | 출력 토큰 상한에서 잘림 | 안 함 | `partialText` |
| `provider_error` | 공급사 오류 (HTTP 오류, 연결 실패). 재시도가 설정된 공급사는 같은 모델을 다시 부른 뒤에도 실패 | `retryable` 일 때만 | `status`, `retryable`, `message` |
| `timeout` | `timeoutMs`(기본 120초) 초과 | 함 | `message` |
| `budget_exceeded` | 누적 비용이 상한에 닿아 공급사를 부르지 않음 | | `limitUsd`, `spentUsd`, `scope` |

모든 결과에 `meta` 가 붙는다: `callId`, `promptId`, `attempts`(시도별 공급사·모델·종류·사용량·비용·시간, 다시 부른 시도에는 `providerRetry`), `totalCostUsd`, `warnings`.

같은 모델을 다시 부르는 것은 둘이다: 스키마 불일치(`maxSchemaRetries`)와, 재시도 가능한 공급사 오류(아래). 시간 초과·거부·잘림은 다시 부르지 않는다. 순서는 「같은 모델 다시 부르기 → 그래도 실패하면 경로의 다음 모델」이다.

### 공급사 오류 재시도

`LlmClientOptions.providerRetry` 가 공급사 이름 → `{ maxRetries, baseDelayMs, maxDelayMs }` 다. 기본은 `DEFAULT_PROVIDER_RETRY`(`src/retry.ts`): **`gemini` 만 2회, 1초부터 두 배, 상한 60초.** 주면 기본을 통째로 바꾸고 `{}` 는 끈다. 발주서는 `MARC-TICK-16`.

- 대상은 `retryable` 인 `provider_error` 뿐이다 (Gemini 어댑터 기준 408·429·5xx 와 연결 실패). 400·401·403·404 같은 재시도 불가 오류는 한 번만 부른다.
- 다시 부를 때마다 **비용 상한을 다시 보고**(막히면 `budget_exceeded`), 공급사 어댑터를 그대로 지나므로 **호출 간격 조절(`throttled`)도 그대로 탄다.** 공급사 오류는 사용량이 없어 비용이 0 이고, 비용은 사용량을 받은 시도만 한 번씩 세어진다.
- **조용히 넘기지 않는다.** 시도마다 호출 기록이 한 줄 남는다. 다시 부른 시도의 줄에는 `providerRetry`(몇 번째 재시도인지, 1부터), `previousErrorStatus`(바로 앞 시도의 상태 코드), `retryWaitMs`(그 전에 기다린 시간)가 붙고, 공급사가 대기를 알려 준 오류 줄에는 `retryAfterMs` 가 붙는다. 다 쓰고도 실패하면 전과 같이 `provider_error` 다.
- 대기: 공급사가 알려 준 값이 있으면 그 값을 따른다. Gemini 는 응답 머리가 아니라 오류 본문의 `google.rpc.RetryInfo.retryDelay` 로 알려 준다 (SDK 의 `ApiError` 는 머리를 주지 않는다). 그 값이 `maxDelayMs` 보다 길면 다시 부르지 않는다. 없으면 `baseDelayMs × 2^n` 을 상한에서 자른다. 흩뜨리기(jitter)는 없다. 대기는 호출의 `timeoutMs` 와 `durationMs` 밖이다.
- OpenAI·Anthropic 은 SDK 가 이미 2회 다시 부르므로 기본 설정에 넣지 않았다 (넣으면 시도 수가 곱해진다). SDK 안의 재시도는 호출 기록에 한 줄로만 보인다.
- `probe` 명령은 응답을 한 번씩 그대로 보는 확인이라 재시도를 끈다 (`providerRetry: {}`).
- 테스트는 `sleep` 을 주입해 실제로 기다리지 않는다 (`LlmClientOptions.sleep`, CLI 는 `LiveDeps.sleep`).

한 번의 `client.call` 이 공급사에 실제로 보내는 요청 수 (모델이 하나인 경로, 기본 설정):

| | OpenAI | Anthropic | Gemini |
|---|---|---|---|
| 다시 부르는 층 | SDK (`openai` 7.25.0) | SDK (`@anthropic-ai/sdk` 0.131.0) | 호출 계층 (`@google/genai` 2.26.0 의 재시도는 끔) |
| 대상 | 408·409·429·5xx, 연결 실패와 SDK 자체의 시간 초과 (`x-should-retry` 머리가 있으면 그것을 따른다) | 왼쪽과 같다 | 408·429·5xx, 연결 실패. 시간 초과는 다시 부르지 않는다 |
| 횟수 | 2회 (`maxRetries` 기본값) | 2회 | 2회 |
| 대기 | `retry-after-ms`·`retry-after` 머리(0~60초일 때), 없으면 0.5초부터 두 배·상한 8초·흩뜨림 최대 25% | 머리의 값(양수면 따른다), 없으면 0.5초부터 두 배·상한 8초·흩뜨림 최대 25% | 본문의 `retryDelay`(60초 이하일 때), 없으면 1초·2초, 흩뜨림 없음 |
| 호출 기록 | 한 줄 (SDK 안의 시도는 안 보인다) | 한 줄 | 시도마다 한 줄 |
| 일시 오류가 계속될 때 | 3 | 3 | 3 |
| 최악 (스키마 불일치 2회와 섞일 때) | 9 | 9 | 9 |

대체 모델이 있는 경로는 모델마다 위 수만큼 더해진다. 호출하는 쪽이 또 다시 부르면 곱해진다: `docpipe` 의 조각 실행기는 재시도 가능한 실패를 조각당 3회까지 부르므로(`data/docpipe/settings.json` 의 `runner.maxAttempts`) 일시 오류가 계속되면 조각당 9번이다 (Gemini 는 이 변경으로 3 → 9 가 됐고 OpenAI·Anthropic 은 전부터 9 였다). `scope3` 는 스스로 다시 부르지 않는다. SDK 는 자기 시간 초과도 다시 부르지만, 어댑터가 SDK 에 주는 `timeout` 과 호출 계층의 시간 제한이 같은 값이라 그때는 호출 계층의 중단 신호가 같이 걸리고 SDK 는 중단된 요청을 다시 부르지 않는다 (결과는 `timeout`). 두 타이머 중 어느 쪽이 먼저 걸리는지는 실측하지 않았다. SDK 의 수치는 설치된 소스에서 읽었다 (2026-10-02, `client.js` 의 `shouldRetry`·`retryRequest`·`calculateDefaultRetryTimeoutMillis`, `@google/genai` 의 `apiCall`). 429·503 에서의 실제 동작은 실측하지 않았다.

대체 대상은 `timeout` 과 `retryable` 인 `provider_error`(한도 초과 429, 5xx, 연결 실패)뿐이다 (`FALLBACK_ON`). 잘못된 요청·인증 실패 같은 재시도 불가 오류는 설정이나 요청의 문제라 덮지 않고 그대로 돌려준다. 스키마 불일치·거부·잘림은 입력이 같으면 다른 공급사에서도 되풀이되거나 뜻이 달라지므로 넘기지 않는다. 설정 오류(없는 경로, 없는 프롬프트, 빠진 변수, 모델 상한을 넘는 `maxOutputTokens`)는 결과가 아니라 예외(`ConfigError`, `PromptError`)다.

## 프롬프트 저장소

`PromptStore` 인터페이스 뒤에 있고 지금 구현은 파일(`FilePromptStore`)이다. 경로는 `<root>/<모듈>/<이름>/<버전>.md`.

```
=== system cache ===
고정 지침과 용어집. 캐시 대상.

=== system ===
캐시하지 않는 지침.

=== user ===
{{item}} 을 분류해 줘.
```

- 버전은 양의 정수이고 id 에 반드시 적는다. `@latest` 나 버전 생략은 거부한다. 내용을 바꾸려면 새 버전 파일을 만든다.
- `{{이름}}` 은 `vars` 로 치환한다. 값이 빠진 변수와 템플릿에 없는 변수는 둘 다 오류다.
- `=== system cache ===` 구획은 캐시 대상이며 캐시하지 않는 구획보다 앞에 와야 한다. 캐시 대상의 어림 토큰이 모델의 최소 길이(`minCacheTokens`)에 못 미치면 `cache_below_minimum` 경고가 호출 전에 나온다.

## 모의 공급사에 응답 심기

```ts
import { MockProvider } from "llm";

const mock = new MockProvider();                 // 이름 기본값 "mock"
mock.enqueueText('{"category":"cat-1"}');        // 완료 응답
mock.enqueue({ kind: "refusal", text: "...", category: null, usage });
mock.enqueue({ kind: "error", status: 503, retryable: true, message: "..." });
mock.enqueue({ kind: "completed", text: "...", usage }, { delayMs: 500 });  // 시간 초과 시험
mock.enqueue((request) => ({ kind: "completed", text: request.user.length.toString(), usage }));
```

심은 순서대로 하나씩 나간다. 심어 둔 것이 없으면 성공을 지어내지 않고 `provider_error` 를 돌려준다. 받은 요청은 `mock.calls` 에, 남은 응답 수는 `mock.pending` 에 있다. 모의 모델과 경로(`mock`, `mock-with-fallback`)는 `data/llm/models.synthetic.json` 에 있고 `loadConfig(defaultConfigPaths({ synthetic: true }))` 로 읽는다. 대체 호출을 시험하려면 `new MockProvider("mock-b")` 를 `"mock-b"` 로 함께 등록한다.

## 실제 공급사 어댑터

```ts
import Anthropic from "@anthropic-ai/sdk";
import { AnthropicProvider, OpenAIProvider } from "llm";

const anthropic = new AnthropicProvider(new Anthropic({ apiKey }));
```

SDK 클라이언트는 호출하는 쪽이 만들어 넣는다. 이 패키지는 환경 변수에서 키를 찾지 않는다. `createOpenAIClient(apiKey)`, `createAnthropicClient(apiKey)`, `createGeminiClient(apiKey)` 도 키를 인자로만 받는다. 프롬프트 캐싱 표시(`cache_control`), PDF·이미지 블록, 구조화 출력 방식(`output_config.format`, `text.format`, `responseJsonSchema`)은 어댑터 안에만 있다. 오류 문구에 섞여 온 키 모양 글자(`sk-…`, `AIza…`)는 `redactKeys` 로 가린다. 새 공급사는 `Provider` 인터페이스(`buildRequest`, `invoke`)를 구현해 `providers` 에 등록하고 설정 파일에 모델을 추가하면 된다.

## 임베딩 호출

```ts
import { EmbeddingClient, MockEmbeddingProvider } from "llm";

const embeddings = new EmbeddingClient({ config, providers: { mock: new MockEmbeddingProvider(64) }, log, budget });
const result = await embeddings.embed({ route: "mock", inputs: ["합성 강판"], tags });
if (result.kind === "success") result.vectors; // 입력 순서 그대로
```

호출하는 쪽은 임베딩 경로 이름만 안다 (공급사와 모델 이름은 설정 파일의 `embeddingRoutes` → `embeddingModels`). 입력이 요청 한도(입력 수, 토큰 합)를 넘으면 여러 요청으로 나눈다. 결과는 `success` · `provider_error` · `timeout` · `budget_exceeded` 로 나뉘고 실패를 빈 벡터로 바꾸지 않는다. 벡터 수나 차원이 설정과 다른 응답은 `provider_error` 다. 요청마다 호출 기록 한 줄이 남고(`promptId` 는 `embedding`, 출력 토큰 0, `embeddingInputs` 에 입력 수), 비용(입력 토큰 × 단가)은 `budget` 에 쌓인다. `LlmClient` 와 같은 `BudgetGuard` 를 넘기면 생성 호출과 한 상한을 탄다. 임베딩 경로는 모델 하나만 가리킨다 (다른 모델의 벡터는 서로 견줄 수 없어 대체 순서가 없다).

실제 공급사는 `liveEmbeddingProviders(config, { provider, route }, deps)` 로 조립한다. 키가 없거나 경로의 모델이 그 공급사의 것이 아니면 `LiveSetupError` 다. `MockEmbeddingProvider` 는 낱말 해시로 만든 가상(synthetic) 벡터를 돌려주고 `enqueue` 로 실패를 심을 수 있다. 가상 임베딩 모델의 설정은 `ts/llm/config/embeddings.synthetic.json` 에 있다. 벡터를 저장하거나 검색하는 일은 이 패키지가 하지 않는다 (`scope3` 의 색인 파일).

## 설정 파일

제품 쪽 정본은 esg-app `apps/api-py/esgapi/core/llm/data/models.json` 이다(2026-10-16 `MARC-ENT-249`). 이 저장소의 `data/llm/models.json` 은 esg-lab 측정용 옛 판이고 그 뒤 항목(gpt-4.1-mini 등)이 없다.

모델 ID, 단가, 출력 토큰 상한, 캐시 최소 길이, PDF 한도는 코드에 없고 `data/llm/models.json` 에 출처 URL·확인 일자와 함께 있다. 출처 없는 모델은 `loadConfig` 가 거부한다. 테스트용 가상 값은 `data/llm/models.synthetic.json` 에 `synthetic: true` 로 따로 둔다.

### 출력 상한과 추론 여유분

`CallRequest.maxOutputTokens` 는 **답의 상한**이다. 추론(reasoning) 토큰이 출력 상한을 같이 쓰는 모델은 설정에 `reasoningOutputReserveTokens` 를 두고, 호출 계층이 `min(모델의 maxOutputTokens, 요청 상한 + 여유분)` 을 공급사에 보낸다 (`buildProviderRequest`). 상한을 주지 않은 호출(기본 4096)에도 붙는다. 여유분이 없는 모델에는 요청 상한이 그대로 나간다. 공급사에 실제로 보낸 값은 dry-run 의 `estimate.maxOutputTokens` 와 본문을 저장한 호출 기록의 `body.request.maxOutputTokens` 에 있고, dry-run 의 비용 상한도 그 값으로 센다.

지금 여유분이 있는 모델은 `gpt-6-luna` 하나(2000)다. 값은 공식 문서가 아니라 저장된 실측 호출 기록에서 정했고 근거는 그 모델의 `notes` 에 있다. `gpt-6.1-sol` 은 실측이 없어 두지 않았다. Claude Haiku 4.5 는 추론 토큰이 없고, Gemini 3.5 Flash-Lite 는 생각 토큰이 붙은 기록이 0건(408건 중)이라 두지 않았다. 추론 토큰 수는 OpenAI 어댑터도 호출 기록의 `thinkingTokens` 에 남긴다 (`output_tokens_details.reasoning_tokens`).

## 호출 기록

공급사를 한 번 부를 때마다(재시도, 대체 호출 포함) JSONL 한 줄: 시각, `callId`, 시도 번호, 공급사, 모델, 프롬프트 id·버전, 템플릿 해시, 요청 해시, 입력·출력·캐시 읽기·캐시 쓰기 토큰, 비용, 소요 시간, 결과 종류, 테넌트·모듈, 경고. 상한에 막힌 호출도 한 줄 남는다. 공급사가 생각(thinking)·추론(reasoning) 토큰 수를 보고하면 `thinkingTokens` 에 따로 남는다(출력 토큰에 이미 든 몫이다. Gemini 와 OpenAI 어댑터가 채운다). 공급사 오류에는 `errorStatus`(HTTP 상태 코드, 상태 없이 실패하면 null)가 붙는다. 같은 모델을 다시 부른 시도에는 `providerRetry`·`previousErrorStatus`·`retryWaitMs` 가 붙는다 (위 「공급사 오류 재시도」).

요청 본문과 응답 글자는 기본으로 저장하지 않고 sha256 해시만 남긴다 (공시 전 고객 데이터, 결정 대기). `storeBodies: true` 로 켠다.

## 비용

`computeCostUsd(usage, pricing)` — 단가는 USD/100만 토큰. 비캐시 입력, 출력, 캐시 읽기, 캐시 쓰기(5분)를 따로 곱해 더한다. 장문 구간이 있는 모델은 입력 합계가 기준을 넘으면 요청 전체에 장문 단가를 쓴다. 실패한 시도라도 공급사가 사용량을 돌려줬으면 비용에 넣는다.

`BudgetGuard` 는 꼬리표(`tenant`, `module`) 범위별 누적 비용이 상한에 닿았는지를 **매 공급사 호출 직전**에 본다 (스키마 재시도와 공급사 오류 재시도 포함). 상한은 이미 쓴 누적만 보고 다음 호출의 예상 비용은 보지 않으므로 마지막 한 번(동시 호출이면 여러 번)은 상한을 넘길 수 있다. 시간 초과로 끊긴 호출은 사용량을 받지 못해 누적에 들어가지 않는다. 누적은 프로세스 메모리에 있다. 재기동 뒤 이어 가려면 호출 기록을 읽어 `record()` 로 다시 채워야 한다 (아직 없음).

## 하지 않는 것

에이전트 루프, 도구 호출, 벡터 저장·검색(임베딩 호출만 한다), 작업 큐, 문서 분할(밴드 F), 배치 API, 1시간 캐시 TTL, 스트리밍 출력 전달.
