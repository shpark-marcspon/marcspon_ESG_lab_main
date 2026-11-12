# scope3

구매·물류·출장·통근 내역의 한 행을 Scope 3 카테고리로 분류하고 배출계수 후보 top-3 를 추천하는 시제품과, 정답이 붙은 예제 세트로 그 흐름을 재는 평가 도구. 발주서는 `PRJ11-TICK-7`, 설계 근거는 `PRJ11-RPT-6`·`PRJ11-RPT-2`·`PRJ11-RPT-3`.

키가 없어도 동작한다. 테스트와 자기검증 명령은 모의 공급사와 `--dry-run` 만 쓴다. 모의 공급사로 나온 정확도는 성능과 무관하다.

실제 공급사는 `--provider <공급사> --route <그 공급사의 경로> --limit-usd <상한>` 으로만 부른다 (예: `--provider openai --route light-openai`). 돈이 든다. 키는 환경 변수로 명령 한 줄에만 실어 넘기고, 키·경로·상한 중 하나라도 없으면 부르지 않고 종료 코드 2 로 끝난다. 공급사는 `openai`·`anthropic`·`gemini` 셋이고 경로는 각각 `light-openai`·`light-anthropic`·`light-gemini`, 키는 `OPENAI_API_KEY`·`ANTHROPIC_API_KEY`·`GEMINI_API_KEY` 다. 분당 요청 한도가 있는 키는 `--rpm <n>` 으로 호출 시작 간격을 벌린다. 호출 기록에 본문을 남기려면 `--store-bodies` 를 준다 (기본은 해시만). `--limit-rows <n>` 은 예제의 앞 n 행만 돌리고, `--out <디렉터리>` 는 행별 판단(`rows.json`)과 지표(`report.json`)를 남긴다. 평가는 후보 추리기의 재현율(정답 계수가 추린 목록 안에 든 비율)과 LLM 단계 실패의 종류별 수도 낸다.

```
cd ts && pnpm -F scope3 test
cd ts && pnpm -F scope3 typecheck
cd ts && pnpm -F scope3 exec tsx src/cli.ts eval ../data/scope3-examples --provider mock
cd ts && pnpm -F scope3 exec tsx src/cli.ts eval ../data/scope3-examples --dry-run
```

예제 폴더 경로는 현재 디렉터리 기준으로 찾고, 없으면 `ts/` 기준으로 찾는다 (`pnpm -F scope3 exec` 는 `ts/scope3` 에서 실행되기 때문이다).

## 배치와 포트

`src/` 는 폴더 셋으로 갈린다 (발주서 `MARC-TICK-12`, 근거 `MARC-RPT-8`). 폴더 사이의 import 방향은 `test/ports.test.ts` 가 지킨다.

| 폴더 | 내용 | 기대는 곳 |
|---|---|---|
| `domain/` | 순수 코드. 타입, 묶음 키, 단위, 계산, 검토 항목, 프롬프트 요청 조립, 순위 함수, 파이프라인, 포트 선언(`ports.ts`) | `kernel`, `zod`, `llm` 의 타입과 `roundUsd`. 파일·네트워크 모듈과 `adapters/`·`eval/` 을 import 하지 않는다 |
| `adapters/` | 포트의 구현 중 파일이나 공급사 계층을 아는 것 | `domain/`, `llm`, `node:fs` |
| `eval/` | 평가·측정. 예제 세트 읽기, 지표, dry-run 계획, 재현율 재측정, 모의 응답기. 제품 코드가 아니다 | `domain/`, `llm` |
| `cli.ts`, `index.ts` | 조립 지점과 공개 표면 | 전부 |

`src/decimal.ts`, `src/evaluate.ts`, `src/examples.ts`, `src/recall.ts`, `src/mockHeuristic.ts` 는 옛 경로를 지키는 재수출이다.

| 포트 (`domain/`) | 뜻 | 구현 |
|---|---|---|
| `FactorCatalog` (`get`, `candidates`) | 계수 카탈로그 읽기 | `adapters/jsonlFactorCatalog.ts` 의 `FactorStore` (jsonl 파일) |
| `MappingMemory` (`lookup`, `confirm`, `size`) | 확정 매핑 기억 | `domain/memory.ts` 의 `InMemoryMappingMemory`, `adapters/fileMappingMemory.ts` 의 `FileMappingMemory` (JSON 파일) |
| `LlmGateway` (`call`) | 생성 모델 호출 | `llm` 의 `LlmClient` (그대로 맞는다) |
| `Shortlister` (`shortlist`) | 후보 추리기 | `domain/shortlister.ts` 의 `LexicalV1Shortlister`·`LexicalV2Shortlister`·`VectorShortlister`, `adapters/embeddingShortlister.ts` 의 `EmbeddingShortlister` (`VectorShortlister` 를 `llm` 과 파일 색인으로 조립한 것) |
| `QueryEmbedder` (`model`, `embed`) | 질의 글 → 벡터와 비용 | `adapters/embeddingShortlister.ts` 의 `LlmQueryEmbedder` (`llm` 의 `EmbeddingClient`) |
| `FactorVectorIndex` (`header`, `vector`) | 계수 id → 벡터, 신선도 머리 | `adapters/embeddingIndex.ts` 의 `EmbeddingIndex` (파일 하나, 메모리 배열) |

임베딩을 쓰는 추리기(`embedding`, `hybrid`)는 `QueryEmbedder` 와 `FactorVectorIndex` 뒤에 있다. 임베딩 공급사나 색인 저장 방식을 바꿀 때 고치는 곳은 이 두 포트의 구현과 `cli.ts` 의 조립뿐이고, 순위·합산(`domain/ranking.ts`)과 파이프라인은 그대로다. 추리기 실패의 종류(`ShortlistFailure.kind`)는 포트가 스스로 정한 문자열이다.

`test/factor-contract.test.ts` 는 계수 레코드의 정본(`schemas/factor.schema.json`)과 `FactorStore` 가 읽는 필드의 사본(`factorRecordSchema`)을 대조한다. 계수 표의 표본(출처 × 레코드 종류마다 고른 간격으로 최대 20건)이 정본을 통과하는지도 본다.

## 흐름

순서는 고정이다. `runPipeline(rows, options)` 이 한다.

1. **묶음** — 품목명을 정규화해(공백, 대소문자, 전각, 괄호·구두점) 단위 차원, 운송 수단과 함께 키로 만든다. 판단은 묶음 단위로 하고 행에 펼친다.
2. **확정 매핑 기억** — 키가 기억에 있으면 그 카테고리와 계수를 쓴다. 이 묶음은 LLM 을 부르지 않는다.
3. **분류** — 프롬프트 `scope3/classify@1`. 카테고리(1·2·4·6·7·9 또는 other)와 영어 검색어를 받는다.
4. **후보 조회** — `data/factors/factors.jsonl` 에서 그 카테고리의 활동(물량·거리) 기반 계수 중 행의 단위와 맞는 것을 찾는다. 없으면 지출 기반으로 넘어간다.
5. **추천** — 프롬프트 `scope3/recommend@1`. 후보를 25건(기본)으로 추려 넣고 최대 3개를 이유·신뢰도와 함께 받는다. 활동 기반 후보 중 맞는 것이 없다는 답이 오면 지출 기반으로 한 번 더 묻는다.
6. **계산** — 첫째 추천으로 활동량(또는 보정한 금액) × 계수를 십진 정확 연산으로 낸다.

결과의 `decision.basis` 에 어느 기반을 썼는지(`activity`·`distance`·`spend`)와 지출 기반으로 넘어간 이유(`no_activity_candidates`·`unit_mismatch`·`llm_found_none_suitable`)가 실린다.

출력 상한은 단계별 상수다 (`domain/llmSteps.ts` 의 `CLASSIFY_MAX_OUTPUT_TOKENS` 400, `RECOMMEND_MAX_OUTPUT_TOKENS` 800). 이 값은 답의 상한이고, 추론 토큰이 출력 상한을 같이 쓰는 모델(`gpt-6-luna`)에는 호출 계층이 모델 설정의 여유분(2000)을 더해 보낸다 (분류 2400, 추천 2800. `ts/llm/README.md` 의 「출력 상한과 추론 여유분」). 그래서 그 모델의 `--dry-run` 비용 상한은 커지고 다른 모델의 것은 그대로다.

## 지키는 것

- **숫자는 LLM 에 넘기지 않는다.** 프롬프트에는 품목명, 단위, 통화, 운송의 출발지·도착지·수단만 들어간다. 수량, 금액, 무게, 거리, 계수 값은 들어가지 않는다.
- **LLM 은 후보 목록 안의 id 만 고를 수 있다.** 추천 응답 스키마의 `factor_id` 가 그 호출에 넘긴 후보 id 의 열거형이다(`recommendationSchema`). 목록 밖 id 는 스키마 불일치가 되고, 호출 계층이 두 번 다시 부른 뒤에도 같으면 `recommendation_failed` 로 끝난다.
- **일시 오류는 호출 계층이 다시 부른다.** `scope3` 에는 행 단위 재시도가 없다. Gemini 의 429·503 은 호출 계층이 같은 모델을 2회까지 다시 부르고(OpenAI·Anthropic 은 SDK 가 한다), 그래도 실패하면 그 묶음은 `classification_failed`·`recommendation_failed` 로 남는다. 다시 부른 수는 `report.llm.attempts`(공급사 시도 수)와 호출 기록에 보인다 (`ts/llm/README.md` 의 「공급사 오류 재시도」).
- **실패는 빈 결과가 아니다.** 묶음의 판단은 `recommended` 말고도 `classification_failed`, `recommendation_failed`, `unsupported_category`, `no_candidates`, `no_suitable_factor`, `memory_factor_missing`, `memory_category_mismatch`, `memory_factor_excluded` 로 나뉜다. 계수 표·기억 파일·예제 파일을 읽지 못하면 예외다.
- **계산할 수 없는 사정도 상태다.** `needs_distance`(운송 행에 거리 없음. 추정하지 않는다), `needs_currency_adjustment`, `unit_mismatch`, `missing_quantity`, `missing_amount`, `invalid_number`.

## 후보

- 후보는 `record_type == emission_factor`, `gas == CO2e`, 값이 있고, Scope 3 카테고리 후보가 붙은 레코드뿐이다 (10,742건 중 2,964건). 기체별 행, 값 없는 행, 발열량·에너지 환산 레코드는 후보가 아니다. 계산은 `value_raw` 를 읽는다.
- `excludeLicenseStatuses`(CLI `--exclude-license disputed,unverified`)로 라이선스가 확정되지 않은 출처를 뺀다. 기본은 빼지 않는다.
- 단위는 차원으로 맞춘다(`domain/units.ts`). 환산은 kg ↔ t 뿐이다. 개수나 「대·식·건」 같은 단위는 무게 기반 계수와 맞지 않아 지출 기반으로 넘어간다.
- 후보를 추리는 단계는 방식을 설정으로 고른다 (아래 「후보 추리기」). 기본 `lexical-v2`(`domain/ranking.ts` 의 `rankLexicalV2`)는 낱말 단위 BM25 검색이고 뜻을 아는 검색이 아니다. 영문 계수 이름과 맞추는 낱말은 분류 단계가 낸 영어 검색어에서 온다.

## 후보 추리기

추천 호출에 넣을 후보를 고르는 방식 (순위 함수는 `domain/ranking.ts`, `Shortlister` 구현은 `domain/shortlister.ts`). CLI 는 `--shortlist-method`, 라이브러리는 `PipelineOptions.shortlister` 로 고른다. 발주서는 `MARC-TICK-10`.

고르지 않았을 때의 방식은 `domain/ports.ts` 의 `DEFAULT_SHORTLIST_METHOD` 한 자리에 있다 (`lexical-v2`, 발주서 `MARC-TICK-16`, 근거 `MARC-ISSUE-17` 의 2026-10-02 노트). CLI 의 기본, `PipelineOptions.shortlister` 를 안 줬을 때(`defaultShortlister()`), `--dry-run` 의 어림이 모두 이 값을 읽는다. `lexical-v1` 은 견줄 기준으로 남아 있고 `--shortlist-method lexical-v1` 또는 `new LexicalV1Shortlister()` 로 고른다.

| 방식 | 내용 | 키 |
|---|---|---|
| `lexical-v1` | 낱말이 계수 이름에 부분 문자열로 들어 있으면 3점, 한글 두 글자 겹침 1점. 동점은 표 순서 | 필요 없음 |
| `lexical-v2` (기본) | 낱말 단위 BM25 (k1 1.2, b 0.75). 낱말이 통째로 같아야 맞고(`car` 는 `cargo` 에 안 맞는다) 드문 낱말이 무겁다. 영어 복수형만 접는다. 한글 두 글자 조각은 기본으로 쓰고 `--no-bigrams` 로 끈다 | 필요 없음 |
| `embedding` | 질의와 계수 이름의 임베딩 코사인 유사도. 질의는 `--embedding-query item`(품목명만) 또는 `item+keywords`(기본, 영어 검색어를 붙임) | 질의 임베딩에 필요 |
| `hybrid` | `lexical-v2` 순위와 `embedding` 순위를 순위 합산(RRF, k 60)으로 합친다 | 질의 임베딩에 필요 |

- 조정값(k1, b, k)은 미리 정한 통상 기본값이고 예제 세트에 맞춰 고른 값이 아니다.
- 동점 규칙은 모든 방식에서 같다: 점수가 같으면 후보 표의 순서. `lexical-v2` 는 겹친 낱말이 없는 후보를 순위 뒤에 표 순서로 이어 붙여 추린 수를 채운다.
- 임베딩 색인은 `index` 명령으로 한 번 만들어 파일로 쓴다 (`out/` 아래, git 밖). 파일에는 모델 키와 ID, 차원, 계수 표 파일의 sha256, 만든 때가 실린다. 계수 표나 모델이 달라지면 `StaleIndexError` 로 거부하므로 다시 만든다. 벡터 DB 는 쓰지 않는다 (메모리에서 코사인 유사도).
- **임베딩이 실패하면 낱말 검색으로 넘어가지 않는다.** 그 묶음의 판단은 `shortlist_failed` 로 끝나고 추천 호출도 하지 않는다. 평가의 `llmFailures` 에 `shortlist:<실패 종류>` 로 세어진다.
- 임베딩 호출은 호출 계층(`llm` 의 `EmbeddingClient`)을 지나므로 호출 기록에 남고 `--limit-usd` 한 상한을 생성 호출과 같이 탄다. 비용은 `report.shortlist.embeddingCostUsd` 에 따로 실린다 (`report.llm.costUsd` 에는 없다).
- 결과에는 어느 방식으로 추렸는지(`decision.candidates.method`, `shortlists[].method`)와 후보별 근거(`shortlists[].evidence`: 겹친 낱말, 낱말 순위와 점수, 유사도와 그 순위, 합산 점수)가 실린다.

```
# 모의 임베딩(synthetic)으로 배선만 본다. 그 재현율은 성능과 무관하다.
cd ts && pnpm -F scope3 exec tsx src/cli.ts index --index ../../out/index.mock.bin --provider mock
cd ts && pnpm -F scope3 exec tsx src/cli.ts eval ../data/scope3-examples --provider mock --shortlist-method hybrid --index ../../out/index.mock.bin
# 생성 모델을 부르지 않고, 앞선 실행의 분류 결과로 방식별 재현율만 다시 잰다.
cd ts && pnpm -F scope3 exec tsx src/cli.ts recall ../data/scope3-examples --from <앞선 실행의 rows.json> [--index <색인> --provider ...]
```

실제 공급사에서는 `--provider openai --embedding-route light-openai --limit-usd <상한>` 을 준다. `recall` 은 앞선 실행이 남긴 카테고리·영어 검색어·어느 기반을 불렀는지를 그대로 쓰고 추리기만 바꾼다. 분모는 정답 계수가 있고 기억에서 오지 않았고 추천 호출까지 간 행이다.

## 지출 기반 계산

지출 기반 계수(EPA v1.3)는 2022년 USD 구매자 가격 기준이다. 환율·물가 보정을 어떤 값으로 할지는 결정 대기라 저장소에 실제 보정 계수가 없다. 보정 계수(`CurrencyAdjustment`)를 주지 않으면 계산은 `needs_currency_adjustment` 로 끝난다. 출처(URL, 확인 일자)가 없는 보정 계수는 `synthetic: true` 여야 하고, 그것으로 계산한 결과에는 `synthetic: true` 가 붙는다.

## 확정 매핑 기억

`MappingMemory` 인터페이스(`lookup`, `confirm`) 뒤에 있고 지금 구현은 JSON 파일 하나(`FileMappingMemory`)다. 항목은 묶음 키 → 카테고리, 계수 id, 확정자, 확정 일시다. 기억의 계수가 표에서 사라졌거나 라이선스 설정으로 제외돼 있으면 LLM 으로 넘기지 않고 상태로 알린다.

## 검토 항목 모델

`domain/review.ts`. 상태는 AI 제안(`ai_proposed`), 반영(`accepted`), 미반영(`rejected`), 수정(`edited`), 재추천 요청(`rerecommend_requested`), 최종 확정(`finalized`)이다. 전이 함수(`accept`, `reject`, `edit`, `requestRerecommend`, `receiveProposal`, `finalize`)는 허용된 출발 상태만 인자로 받으므로 허용되지 않는 전이는 컴파일되지 않는다. 타입을 모르는 입력에는 `applyAction` 이 같은 표(`TRANSITIONS`)로 검사한다. AI 원본 제안(`aiProposal`, `proposalHistory`)과 사람의 수정값(`humanEdit`)은 다른 필드에 남는다. `toMemoryEntry` 가 확정된 항목을 기억 항목으로 바꾼다.

## 평가 도구

`evaluate(examples, result, store, options)` 가 내는 것: 분류 정확도, top-1·top-3 적중률, 기억 적중 비율, 정답을 달 수 없는 행에서 추천을 낸 수, 쓴 계수 기반과 지출 기반으로 넘어간 이유, 판단·계산 상태별 행 수, LLM 호출 수와 비용.

「정답」의 정의는 결정 대기다(`PRJ11-ISSUE-6`). 두 기준을 모두 계산하고 대표 기준만 `--criterion` 으로 고른다.

- `factor_id`: 추천한 계수 id 가 정답 id 중 하나와 같다.
- `sector`: 추천한 계수가 정답 계수와 같은 분류 부문이다. NAICS 는 앞 4자리, 그 밖은 출처와 분류 계층 2단계가 기본이고 `evaluate` 의 `sector` 옵션으로 바꾼다. 출처가 다르면 같은 부문이 되지 않는다.

`--dry-run` 은 공급사를 부르지 않고 예상 호출 수(최소·최대), 입력 토큰 어림, 비용 상한, 한 카테고리의 후보 전체를 넣었을 때의 토큰 어림을 낸다. 분류가 정답 카테고리를 낸다고 가정하고 추천 프롬프트를 조립한다. 추리기는 실제 실행과 같은 방식(`--shortlist-method`, 기본 `lexical-v2`)으로 어림하고 결과의 `plan.shortlistMethod` 에 적는다. 영어 검색어는 분류 단계가 내는 값이라 어림에서는 품목명만 쓴다. 공급사를 부르는 방식(`embedding`·`hybrid`)과는 같이 쓸 수 없다.

`analyzeCoverage` 는 LLM 없이 단위와 후보 유무만 보고, 활동 기반이 가능한 행과 지출 기반으로만 가능한 행을 센다.

## 하지 않는 것

엑셀 파싱, 화면, 서버, 거리 추정(지도 API), 환율·물가 보정 값의 결정, 벡터 DB, 문서 검색 챗봇, 에이전트 루프.
