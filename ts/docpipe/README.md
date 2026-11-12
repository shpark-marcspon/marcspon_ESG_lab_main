# docpipe

긴 문서(100쪽이 넘는 지속가능경영보고서)를 조각으로 나눠 LLM 작업을 병렬로 돌리는 파이프라인. 조각 단위로 재시도와 이어 하기가 되고, 번역 결과의 용어 일관성과 누락·중복을 LLM 없이 검사한다. 작업은 번역(국문 → 영문)과 그린워싱 태깅 두 가지다.

입력은 `py/docparse` 가 낸 문서 JSON(`schemas/document.schema.json`)이다. 이 스키마와 읽는 쪽 정의(`src/document.ts`)가 어긋나면 `test/schema-contract.test.ts` 가 빨개진다. PDF 를 직접 읽지 않는다. LLM 호출은 전부 `ts/llm` 을 거친다.

키가 없어도 동작한다. 테스트와 자기검증 명령은 모의 공급사와 `--dry-run` 만 쓴다. 모의 공급사의 응답은 synthetic 이라 번역 품질이나 판정 정확도에 대해 아무것도 말해 주지 않는다.

실제 공급사는 `--provider <공급사> --route <그 공급사의 경로> --limit-usd <상한>` 으로만 부른다 (예: `--provider openai --route light-openai`). 돈이 든다. 키는 환경 변수로 명령 한 줄에만 실어 넘기고, 키·경로·상한 중 하나라도 없으면 부르지 않고 종료 코드 2 로 끝난다. 공급사는 `openai`·`anthropic`·`gemini` 셋이고 경로는 각각 `light-openai`·`light-anthropic`·`light-gemini`, 키는 `OPENAI_API_KEY`·`ANTHROPIC_API_KEY`·`GEMINI_API_KEY` 다. 분당 요청 한도가 있는 키는 `--rpm <n>` 으로 호출 시작 간격을 벌린다. 호출 기록에 본문을 남기려면 `--store-bodies` 를 준다 (기본은 해시만). `--max-chunks <n>` 은 앞의 n 개 조각만 돌린다. 같은 `--out` 으로 다시 돌리면 끝난 조각을 건너뛰고 이어서 한다.

```
cd ts && pnpm -F docpipe test
cd ts && pnpm -F docpipe typecheck
cd ts && pnpm -F docpipe exec tsx src/cli.ts translate ../out/docparse-samsung/document.json --provider mock
cd ts && pnpm -F docpipe exec tsx src/cli.ts translate ../out/docparse-samsung/document.json --dry-run
cd ts && pnpm -F docpipe exec tsx src/cli.ts greenwash ../out/docparse-samsung/document.json --dry-run
cd ts && pnpm -F docpipe exec tsx src/cli.ts reread ../out/docparse-samsung/document.json --calls <저장된 calls.jsonl> --route light-openai --out <절대 경로>
```

`reread` 는 공급사를 부르지 않는다. `--store-bodies` 로 남긴 번역 호출 기록의 응답을 다시 읽어 지금의 표 셀 규칙에 통과시키고, `translate` 와 같은 꼴의 요약·`result.json`·`report.json`·`suspects.json` 을 낸다(호출 기록 파일은 남기지 않는다). 조각 구성이 그때와 같으면 그때의 응답을 시도 순서대로 돌려준다(`exactReplies`). 단위 셀을 빼서 조각 구성이 달라졌으면 항목별 저장 번역을 다시 묶어 돌려준다(`recomposedReplies`). 이 경우 글자는 전부 모델이 그 항목에 낸 번역이지만 모델이 그 조각 구성으로 낸 응답은 아니다. `--table-numbers strict --unit-cells model` 을 주면 옛 규칙의 결과가 그대로 다시 나온다.

`--out` 에 아직 없는 디렉터리를 상대 경로로 주면 `ts/docpipe` 아래에 생긴다. 절대 경로로 준다.

문서 JSON 은 저장소에 없다. `cd py && uv run python -m docparse parse <PDF> --out ../out/docparse-samsung` 으로 만든다. `pnpm -F docpipe exec` 는 `ts/docpipe` 에서 실행되므로 상대 경로는 현재 디렉터리, `ts/`, 저장소 루트 순서로 찾는다.

## 흐름

```
문서 JSON → 고르기(select) → 분할(chunk) → 실행기(runner) + 조각 저장(store) → 조립(translate) → 검사(coverage, consistency)
```

| 파일 | 하는 일 |
|---|---|
| `src/numeric.ts` | 글자 한 덩어리를 LLM 에 보낼지 정하는 **유일한 자리** (`classifyText`) |
| `src/select.ts` | 블록마다 처리 방식을 하나씩 매긴다 (보냄, 그대로 옮김, 뺌, 원본 추출 실패) |
| `src/chunk.ts` | 토큰 예산에 맞춰 조각으로 묶는다. 문단과 표는 쪼개지 않는다 |
| `src/runner.ts` | 동시 실행 수 제한, 조각 단위 재시도, 이어 하기, 진행률 |
| `src/store.ts` | 조각 id 하나에 파일 하나 (`chunks/<조각 id>.json`) |
| `src/units.ts` | 단위만 있는 표 셀을 가르고(`unitCell`) 고정 번역 표로 옮긴다 |
| `src/translate.ts` | 번역 요청 조립, 응답 검증, 결과 조립, 표 셀 표시(`cellFlags`) |
| `src/numbercheck.ts` | 표 셀의 숫자 차이와 의심 등급 (`numberDiff`, `gradeNumberChange`) |
| `src/reread.ts` | 저장된 호출 기록의 응답을 다시 읽어 돌려주는 응답기 (호출 없음) |
| `src/consistency.ts` | 용어 일관성, 되풀이 원문, 숫자 보존 검사 (LLM 없음) |
| `src/coverage.ts` | 원문 블록 id 와 번역 블록 id 의 대응 검사 |
| `src/greenwash.ts` | 환경 주장 문단 걸러 내기, 구조화 출력 태깅 |
| `src/dryrun.ts` | 조각 수, 조각별 예상 토큰, 총 예상 비용 |
| `src/mock-synthetic.ts` | `--provider mock` 용 synthetic 응답기 |
| `prompts/docpipe/<이름>/<버전>.md` | 프롬프트. 내용을 바꾸려면 새 버전 파일을 만든다 |

## 무엇을 LLM 에 보내는가

`classifyText` 가 블록의 글자와 표의 셀을 다섯으로 나눈다.

| 분류 | 기준 | 예 | 처리 |
|---|---|---|---|
| `blank` | 비었다 | | 그대로 |
| `symbol` | 글자도 숫자도 없다 | `-`, `※` | 그대로 |
| `numeric` | 숫자가 있고 어느 문자 체계의 글자도 없다 | `36,399`, `31.0%`, `(1,234)` | 그대로 |
| `passthrough` | 글자는 있지만 한글·한자가 없다 | `GWh`, `Scope 1, 2` | 그대로 |
| `translatable` | 한글이나 한자가 하나라도 있다 | `전력`, `2023년`, `187조 원` | LLM 에 보냄 (표의 단위 셀은 고정 번역) |

- **숫자 셀은 요청에 들어가지 않는다.** 모델이 숫자 셀의 id 로 값을 돌려줘도 조립 단계(`assembleTable`)가 원본 글자를 쓴다. 돌려준 것은 조각 기록의 `unexpectedIds` 에 남는다.
- 숫자와 한글이 섞인 셀(`2023년`, `59,498명`)은 LLM 을 거친다. 문단 속 숫자는 막지 않고 `consistency.numberDrifts` 로 보고한다 (`187조` → `187 trillion` 은 통과, 단위를 환산하면 걸린다). 표 셀은 아래 「표 셀 규칙」대로 다룬다.
- 표의 `footnote_marks`, `header`, `notes`, 추출 상태(`sourceStatus`)는 결과에 그대로 실린다.
- 쪽의 절반 이상에 되풀이되는 글자(머리말, 꼬리말, 내비게이션)는 `excluded: repeated_furniture` 로 빼고 보내지 않는다.
- `low_confidence` 표는 목차나 도표 숫자의 오탐일 수 있지만 셀의 글자는 텍스트 계층 그대로이므로 글자 셀을 번역하고 `sourceStatus` 를 싣는다. `failed` 표는 셀이 없어 `source_failed` 로 남는다.

## 표 셀 규칙

기본값은 `data/docpipe/settings.json` 의 `translate.tableNumbers: "flag"`, `translate.unitCells: "fixed"` 이고 `--table-numbers`, `--unit-cells` 로 실행마다 바꿀 수 있다 (사용자 결정 2026-10-02, MARC-ISSUE-16).

**단위 셀은 모델에 보내지 않는다.** 숫자 없이 단위 낱말만으로 된 셀(`억 원`, `천 톤`, `톤 CO₂e/억 원`)은 `unitCell` 이 문법으로 가른다: 셀의 토큰이 전부 수 단위 낱말(`천`, `만`, `억`)이나 계량 단위 낱말(`원`, `톤`, `건`)이거나 한글 없는 단위 기호(`CO₂e`, `MWh`, `%`)이고, 한글 토큰 묶음이 계량 단위로 끝나야 하며, 단위 기호는 한글 토큰 뒤에만 온다. `사업장`, `천연가스`, `조건` 같은 일반 낱말과 `ESG 건` 같은 항목명은 맞지 않는다. 낱말 목록과 고정 번역은 `data/docpipe/unit-cells.synthetic.json` 에 있다.

| 셀 | 처리 | 결과에 남는 것 |
|---|---|---|
| 단위 셀이고 표에 있다 | 모델에 보내지 않고 표의 영문을 쓴다. 숫자 대조 대상이 아니다 | `cellFlags` 의 `unit_fixed` (원문, 쓴 글자) |
| 단위 셀인데 표에 없는 낱말이 있다 (`시간`, `천 kWh`) | 모델에 보낸다. 실패가 아니다 | `cellFlags` 의 `unit_without_fixed_translation` (표에 없는 낱말) |
| 한글 셀이 전부 고정 번역하는 단위 셀인 표 | 조각에 싣지 않는다 | 표의 `status: "fixed"` |

**숫자가 달라진 셀은 의심 표시로 남긴다.** 모델이 옮긴 표 셀의 숫자 덩어리가 원문과 다르면 조각을 실패시키지 않고 다시 부르지도 않는다. 번역문을 그대로 쓰고 그 셀에 `number_change_suspected` 표시와 차이(`sourceOnly` = 원문에만 있는 숫자, `targetOnly` = 번역문에만 있는 숫자), 등급을 남긴다. 조각 기록에는 경고 `table_number_change_suspected` 가 붙는다.

- 등급 `notation_like`: 달라진 숫자가 전부 「값이 그대로인 표기 변환」으로 설명된다. 꼴은 넷이다. 수 단위를 숫자로 풀어 씀(`24만 톤` → `240,000 tons`, 값이 같아야 한다), 작은 수(1~12)를 같은 값의 영어 낱말로(`1차` → `first-tier`), 두 자리 연도를 네 자리로(`’30년` → `2030`, 원문에 아포스트로피나 `년` 이 있어야 한다), `N월` 을 달 이름으로.
- 등급 `other`: 설명되지 않는 숫자가 하나라도 남는다 (각주 번호가 빠짐, 숫자가 옆 셀로 옮겨 감, 숫자가 사라짐, 같은 값의 낱말이 없는 `월 1회` → `Monthly`).
- 등급은 검토 순서를 정하는 데만 쓴다. `notation_like` 도 검토 대상이다. 낱말 증거는 문자열로만 보므로 각주 번호 `1)` 이 빠졌는데 같은 셀에 `first` 가 있으면 가려내지 못한다.
- 사람이 훑어볼 목록은 `suspects.json` (조각, 표, 셀 id, 원문, 번역문, 차이, 등급. `other` 가 앞)이고, 합계는 요약과 `result.json`·`report.json` 의 `tableCells` 다. `tableCells` 에는 분모가 같이 있다: `sentCells`(모델에 보낸 표 셀) = `numberChecked`(끝난 조각의 셀, 견줬다) + `numberNotChecked`(실패했거나 안 돈 조각의 셀, 견주지 못했다). `numberNotChecked` 가 0 이 아니면 `suspected` 는 전체의 수가 아니다.

**옛 엄격 규칙**은 `--table-numbers strict` 로 남아 있다: 숫자가 달라지면 조각이 `table_number_changed` 로 실패하고 다시 부른다. `--unit-cells model` 은 단위 셀도 모델에 보낸다. 둘을 같이 주면 MARC-TICK-9 실측 때의 규칙이다. 규칙을 바꿔도 프롬프트(`docpipe/translate@1`)는 같다.

## 분할

- 조각당 원문 토큰 상한은 `data/docpipe/settings.json` 에 근거와 함께 있다 (번역 1,500, 그린워싱 6,000). 토큰은 `ts/llm` 의 글자 수 어림으로 센다.
- 문단과 표는 쪼개지 않는다. 예산보다 큰 단위는 혼자 조각이 되고 `overBudget` 으로 표시된다.
- PDF 책갈피에서 온 섹션(`pdf_outline`)은 반드시 조각을 가른다. 쪽 상단 큰 글자로 추정한 섹션은 그 자리에서 가르는 쪽을 먼저 고르되, 다음 섹션이 통째로 남은 예산에 들어가면 같은 조각에 싣는다.
- 조각마다 문서 전체를 넣지 않는다. 요청에는 고정 앞부분(지침, 용어집)과 그 조각의 글자, 위치 한 줄(쪽, 섹션 제목)만 들어간다.
- 조각 id 는 `c<순번>-<해시>` 이고 해시에는 프롬프트 템플릿, 용어집(또는 체크리스트), 경로, 조각의 글자가 들어간다. 이 중 하나라도 바뀌면 id 가 바뀌어 다시 실행된다.

## 실행과 이어 하기

- 결과는 `out/docpipe/<문서>-<sha 앞 8자>/<작업>-<경로>/chunks/<조각 id>.json` 에 조각마다 저장된다 (`out/` 은 git 밖).
- 다시 실행하면 `done` 으로 저장된 조각은 부르지 않는다. `failed` 와 기록 없는 조각만 돌린다. `--fresh` 는 전부 다시 돌린다.
- 읽을 수 없는 조각 기록 파일은 없는 것으로 치지 않고 실행을 멈춘다(`StoreError`). 파일을 지우거나 `--fresh` 로 넘어간다.
- `pnpm -F docpipe exec` 로 부르면 0 이 아닌 종료 코드는 pnpm 이 1 로 바꾼다. 3 과 1 을 가르려면 `ts/docpipe` 에서 `tsx src/cli.ts` 를 직접 부르거나 출력의 `failures` 를 본다.
- 조각 하나의 실패는 그 조각의 기록(`status: failed`, 실패 종류)으로 남고 다른 조각을 멈추지 않는다. 조립 결과에서 그 조각의 블록은 원문으로 메우지 않고 `not_translated` 다. CLI 는 실패가 남으면 종료 코드 3 을 낸다.
- 조각 단위로 다시 부르는 실패: 시간 초과, 재시도 가능한 공급사 오류, 번역이 빠졌거나(`incomplete_output`) id 가 겹친(`duplicate_ids`) 응답. 엄격 설정(`--table-numbers strict`)에서는 표 셀의 숫자가 바뀐(`table_number_changed`) 응답도 다시 부른다. 스키마 불일치는 호출 계층이 이미 다시 불렀고, 거부와 출력 잘림은 다시 부르지 않는다.

## 검사 (LLM 없음)

`report.json` 에 남는다.

- `coverage`: 원문에 있는데 결과에 없는 블록, 두 번 나온 블록, 원문에 없는 블록, 둘 이상의 조각에 실린 항목, 끝난 조각에서 번역이 빠진 항목, 조각에 없던 id.
- `consistency.termMisses`: 원문에 용어집의 국문 용어가 있는데 번역문에 짝지어진 영문 용어가 없는 곳 (대소문자 무시, 긴 용어 먼저).
- `consistency.termDrifts`: 같은 용어가 조각마다 다른 꼴(용어집 용어, 용어집에 적힌 다른 번역, 둘 다 아님)로 나온 곳.
- `consistency.repeatDrifts`: 글자가 같은 원문이 다르게 번역된 곳.
- `consistency.numberDrifts`: 원문의 숫자가 번역문에 없는 곳 (문단과 표 셀 모두).
- `tableCells`: 표 셀의 합계와 분모 (위 「표 셀 규칙」). 의심 셀 목록은 `suspects.json`.

문자열 검사라 복수형이나 하이픈이 다르면 빠진 것으로 나온다. 끝난 조각만 본다.

## synthetic 자료

- `data/docpipe/glossary.synthetic.json` — 예시 용어집. 실제 용어집은 결정 대기.
- `data/docpipe/greenwash-checklist.synthetic.json` — 예시 판정 유형과 걸러 내기 키워드. 실제 기준은 결정 대기이고 파일의 `decisionPending` 에 적혀 있다. 이 유형으로 낸 판정을 검토 결과로 쓰지 않는다.

- `data/docpipe/unit-cells.synthetic.json` — 단위 셀의 낱말 목록과 고정 번역 18개. 영어 표기(`KRW 100 million`, `1,000 tons`, `cases`)는 러너가 정했고 출처로 확인한 것이 없다. 실제 표기는 결정 대기.

셋 다 파일과 항목 양쪽에 `synthetic: true` 가 있어야 읽힌다.

## dry-run 의 수치를 읽는 법

- 토큰은 글자 수 어림이다 (`data/llm/models.json` 의 `tokenEstimate`). 한글 1자 = 1토큰은 출처 없는 가정이다.
- 단가는 `data/llm/models.json` 에서 읽는다. 이 패키지의 코드에 단가는 없다.
- 예상 비용 = 입력 어림 × 입력 단가 + (조각 원문 토큰 × 출력 비율) × 출력 단가. 출력 비율은 `settings.json` 의 가정이다. 캐시와 배치 할인은 넣지 않는다.
- 비용 상한은 `ts/llm` 의 `dryRun` 이 낸 값(조각마다 출력 토큰 상한을 다 쓸 때)의 합이다.

## 하지 않는 것

외부 큐, 번역 메모리(문장 쌍 재사용), 조각 사이 문맥 전달(앞 조각의 번역을 뒤 조각에 넘기기), 출력 잘림 때의 자동 재분할, 재시도 사이의 대기(기본 0), 데이터 검증·보고서 작성·IRO 모듈.
