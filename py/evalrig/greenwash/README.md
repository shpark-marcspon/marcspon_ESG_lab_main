# evalrig.greenwash — 그린워싱 평가 장치 (EVB, `MARC-TICK-553`)

채점 단위는 (쪽, 정규화 문장) + 등급 + (있으면) 카테고리다. 열쇠는 (쪽, sha12)이고, 정규화는 RWL2 `compare.py` 의 `norm` 과 같은 NFKC + 공백 접기다(`units.py`). 공통 `match.norm_text` 는 NFC 라서 전각 글자를 접지 않으므로 그린 열쇠로는 쓰지 않는다. 정답 머리는 EVA 공통 `evalrig.gold@1` 꼴이다. items 꼴 `greenwash.page_sentence@1` 은 `units.py` 를 import 할 때 공통에 등록된다. 정답은 모두 러너가 지었고 사람 검토는 0 이다. 카테고리 목록은 아직 받지 못해(`MARC-ENT-612`) 모든 줄이 `absent` 다.

## 정답 셋 (C2)

| 셋 | 자리 | 줄 | coverage | 셀 수 있는 것 |
|---|---|---|---|---|
| ① 지은 표본 — `greenwash/tests/sample_text.py` 네 쪽의 모든 문장 | `data/evalrig/greenwash/synthetic-sample.gold.json`(git) | 84줄(문장 122, 같은 (쪽, sha12) 는 `occurrences` 로 접음) | exhaustive | 재현·정밀·등급 일치 |
| ② SG1 주장 — 권마다 15 | `out/evb/gold/sg1-claims.<PDF sha12>.gold.json`(git 밖 — 실재 글) | 15 × 3 | partial | suspect 를 위험(High·Mid)으로 냈나 · supported 를 위험으로 냈나. `partial`(partially_supported)은 두 분모 밖에서 수만 낸다. 정밀은 unmeasurable |

- 지은 표본의 라벨 기준(R1~R4)과 판단이 갈리는 줄의 `accept` 는 `gold.py` 의 `RULES`·`SAMPLE_LABELS` 에 있고, 정답 파일의 `conventions` 에도 실린다.
- 짓기와 검사(`py/` 에서):
  - `uv run --frozen --group greenwash python -m evalrig.greenwash gold-sample`
  - `… gold-sg1 <SG1 권 폴더> --document <그 권 document.json> --out ../out/evb/gold`
  - `… gold-check <파일…>`

## GM1 — 쪽 상한·2,500자 풂 (`gm1.py`, 실비 0)

`… gm1 <PDF…> --models <esg-app models.json> --out ../out/evb/gm1` 는 권마다 `<sha12>.json` 과 `gm1.md` 를 낸다. 쪽마다 세는 것은 셋이다.
- ⓐ 1차 기준선: 2,500자 안의 규칙 후보(상한 3) + AI 탐지 상한 3
- ⓑ 2,500자만 푼 수
- ⓒ 둘 다 푼 수: 쪽 글 전체의 환경 신호 문장 수

순증은 쪽마다 max(0, ⓧ−ⓐ) 의 합이다. 비용은 순증 × `dry_run` 아래끝 eval 요청당 입력 토큰 × 단가다. 단가는 실행할 때 models 파일에서 읽고, 그 파일의 sha16·확인일과 함께 싣는다. 산출에는 수·쪽 번호·sha12 만 싣는다.

## 시험

`uv run --frozen --group greenwash pytest -q evalrig/greenwash/tests` — 쓰는 자료는 지은 것뿐이다: 표본 PDF 는 tmp 에 `build_sample.py` 로 짓고, SG1·단가는 `tests/fixtures/` 의 지은 값을 쓴다.

## 변환기·채점기·같은 입력 대조 (C3, 실비 0)

- `convert_e0.py`: 1차 결과를 채점 단위로 바꾼다. 입력은 `greenwash.result@1`(dict, 또는 mig1 꼴의 목록) 또는 esg-lab `0.1` 이다. 두 파트에 실린 같은 문장은 (쪽, sha12) 하나로 접고 그 수를 센다. 읽지 못한 쪽(`candidateStats.failedPages`)은 `notChecked` 로 둔다. 모르는 꼴은 `UnknownShape` 다.
- `adapt_v2.py`: 2차 결과(result@2) 어댑터의 자리다. 부르면 `NotReady` 를 낸다. 채점기 입구는 1차 변환 산출과 같은 꼴이다. 새 엔진은 최소한 page·sentence·sha12·level·category 를 실어야 한다.
- `score.py`: 짝짓기는 같은 쪽 안에서 exact(sha12) → contain(한쪽이 다른 쪽을 품음) → fuzzy(difflib ≥ 0.90) 차례로, 공통 `pair(order="gold")` 로 한다.
  - 지은 표본(exhaustive) 정답: 위험·Low 의 재현과 정밀, 등급 일치(accept 포함)를 낸다.
  - SG1(partial) 정답: suspect 재현과 「supported 를 위험으로 냄」을 내고, 정밀은 `unmeasurable` 이다.
  - 결과가 점검하지 않은 쪽(`pages` 밖)과 notChecked 쪽의 정답은 분모에서 빼고 그 수를 따로 낸다.
- `sameinput.py`:
  - `same`: 문서 sha·쪽·설정이 다르면 `InputMismatch` 다. 같으면 짝·한쪽에만·등급 다름(분모 포함)을 낸다.
  - `jitter`: 모든 짝에 대해 RWL2 의 diffRatio·onlyRatio 를 셈하고 그 평균·최댓값을 낸다.
- CLI 명령은 `convert` · `score` · `same` · `jitter` · `leak-check` 다. `leak-check` 는 SG1 주장 문장(정규화)이 산출·보고에 들었는지 본다. 정답 폴더 `gold/` 는 건너뛴다.

## MB1 측정 (그린 GM2·GM3·GM4, 셈만 — 호출 0)

- `measure_mb1.py` 는 git 밖 드라이버(`out/MB1/drivers/`)가 쓴 근거·판정·장부 파일을 셈한다. 권 열쇠·경로·단가는 모두 인자다.
- `recall`: 단락 글과 근거 글을 NFKC + 공백 지움으로 맞춘 뒤, 단락이 근거 안에 `min(60, 단락 길이)` 자 연속 같은 글자열을 가지면 찾음. 판마다 주장별 core 재현의 미시 평균(분모 = core ≥ 1 인 주장), 「하나라도」, 천장 판 안 몫(`--ceiling`), id 재현(글자 < id 면 경고), 판 사이 차이(`--diff X:Y`, 부트스트랩 95%).
- `flip`: 판(`<model>/<arm>`) 안 회차 쌍 등급 다름과 판 사이 전 쌍, Δ = 사이 − 평균(안 X, 안 Y). 쌍은 둘 다 `path=eval`·`parsed`·성공일 때만 센다(쌍 단위 — 한 회차만 무효인 주장은 남은 쌍으로 남음). Low 길·깨짐·cutoff·failed 는 `outside` 로 따로.
- `result1`: (모델·판·회차)마다 최소 `greenwash.result@1`(NonRisk 는 items 밖) → 기존 `score`·`same`·`jitter` 에 넣는다. 판 이름은 폴더 이름(`<model>.<arm>.r<rep>`)뿐 — config 에 넣지 않는다.
- `gm4`: 표 덧붙임 전·후 × 2회. 등급을 위험(High·Mid)/비위험으로 접어 전 바닥(전 두 회차 갈래 다름)·전→후 위험→비위험·반대 방향·라벨별 수(라벨 없으면 `unlabeled`)·표 없는 옮김 수·후보 얼림(`frozenOk`). 열쇠 `p<쪽>:<문장 sha12>`.
- `ledger`: core(`lab.mode == live`)·semgraph(`dry_run` 뺌)·pdx 장부 합, 잴 것·모델별. `--models` 를 주면 단가로 다시 셈해 0.0005 USD 넘게 다른 줄을 경고.
- 시험 `tests/test_measure_mb1.py`(지은 자료 `tests/fixtures/mb1-*.json`).

## GFC1 측정 (금융 대 일반 보고서 — 15줄 덮임, 셈만 — 호출 0)

- `measure_gfc1.py` 는 git 밖 드라이버(`out/GFC1/drivers/`)가 G1 흐름(esg-app `git archive` 사본)으로 낸 result@2·종류 나눔·다시 판정·장부 파일을 셈한다. 권 열쇠·경로·단가는 인자다. 얼린 규칙은 `out/GFC1/rules.frozen.md`.
- 덮임 갈래: 판정 받은 항목(verdict 있음 — 검사 실패여도)의 `module.criteriaLines` 로 specific(특정 쟁점 줄 하나 이상) · generic_only(점검 줄·넓은 낱말 줄뿐) · none. U = none + generic_only. 판정 실패·unplaced 는 분모 밖.
- 명령:
  - `pages`: 파트 합집합이 35쪽 넘으면 쪽 점수(환경 신호 문장 + 금융 낱말 문장) 높은 차례, 같으면 앞 쪽.
  - `tally`: 권·무리별 후보 갈래, 종류(두 회차 다르면 unstable) 분포·F-* 비율, 덮임 U·줄별 hit.
  - `floor`: 같은 근거로 판정만 3회 돈 회차 쌍의 갈래·hit·등급 다름(흔들림 바닥).
  - `detectcov`: 쪽 글의 금융 낱말 문장이 탐지 후보에 들었나(일반 권 환경 문장 바닥과 견줌, 후보 상한 찬 쪽은 따로).
  - `eyecheck`: 눈검사 표본(무리·갈래마다 씨앗 고정)·일치율. `f5`: 기본 15줄 대 + 후보 줄 두 판의 갈래 옮김·후보 줄 hit·등급 바뀜 Δ.
  - `ledger`: 장부 다시 셈(입력·cacheRead·cacheWrite·출력 네 칸, 긴 문맥 단가 포함, 0.0005 USD 넘게 다르면 경고). `verdict`: 예/아니오 규칙 대입 → `summary.{json,md}`.
- hit 는 LLM 답이고 정답이 없다 — 이 측정은 판정 품질이 아니라 기준 줄 hit 분포를 잰다.
- 시험 `tests/test_measure_gfc1.py`(지은 자료만).
