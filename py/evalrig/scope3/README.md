# evalrig.scope3 — 0차 평가 장치 C (Scope 3 정답 셋·채점기)

밴드 EVC(`sid:MARC-TICK-554`, 발주서 `band-handoffs/EVC-order.md`). 1차(`colab_157`)와 2차 엔진을 **같은 정답 셋에 같은 채점
단위로** 채점한다(`MARC-ENT-619`). 1차 결과 → 채점 단위 변환은 여기(평가 쪽)에만 있다 — esg-app 은 import 하지 않는다.
AI 실호출 0. Scope 3 전용 꼴이다(EVA 공통 모듈 `py/evalrig/` 로 옮김은 후속).

`py/` 에서(**scope3colab 묶음 없이** 돈다 — `scope3colab.rows`·`sandbox` 는 openpyxl·표준 라이브러리만):

```
uv run --frozen python -m evalrig.scope3 gold-example --result-xlsx <받은 결과 파일> --out <esg-lab/out/…/gold.json>
uv run --frozen python -m evalrig.scope3 gold-edits   --result <result@1.json> --edits <edits.json> --out <out/…/gold.json>
uv run --frozen python -m evalrig.scope3 score        --gold <gold.json> --result <rows/1 | result@1 JSON> --out <out/…/폴더>
uv run --frozen pytest -q evalrig/scope3/tests
```

종료 코드: 셈 끝 0(목표 미달도 0) · 도구 실패 2(꼴 어긋남·열쇠 갈래 다름·`out/` 밖 출력·result@2·rejected·모르는 꼴).
받은 자료에서 지은 것(정답 셋·채점 JSON)은 `out/` 아래에만 쓴다. 메타에는 sha256·바이트 수만(경로·파일 이름 0).

## 꼴

| 꼴 | 자리 | 요점 |
|---|---|---|
| `evalrig.scope3.unit/1` | `unit.py` `Unit` | 열쇠(`by`: `sheetOrder` = rows/1 의 (시트 열쇠, 결과 시트 안 차례, 품목 글) · `rowId` = result@1 `시트!엑셀 줄`) + `category` + `factor{keyFields, value}` + `co2e` (+ C1·C2 `candidates`·`classification`) |
| `evalrig.scope3.gold/1` | `unit.py` `GoldSet` | `meta`(source·keyBy·producedBy·reviewed·categories 열넷 present/absent) + `units`(정답 줄 — `gold: excluded·unreviewed` 갈래) |
| `evalrig.scope3.score/1` | `score.py` | targets(분류 85%·top-3 80% — 없으면 `no_gold`/`no_result`) · 카테고리마다 n·제외·미검토·hit1·hit3·분류·co2e·missingRows·extraRows · rows |

부재는 판별 유니온: 정답 없음(`absent`/`no_gold`) · 결과 쪽 칸 없음(`no_result`) · 검토 안 함(`unreviewed`) · 결과가 줄을 안 냄
(`missing`) · 둘 다 빔(`both_empty`) · 어댑터 없음(`AdapterUnavailable`, 2). 비율은 `{"hits", "of", "rate"}` 로 분모와 같이,
분모 0 이면 `rate: null`.

## 계수 열쇠 표 (critic 1 B2 — 「같은 계수」를 가르는 칸, `unit.py` `FACTOR_KEY_FIELDS`)

hit1 = 정답이 가진 열쇠 칸이 모두 같고 계수 값이 `Decimal` 같음. 칸은 그 시트·갈래의 `scope3colab.rows.fields_of` 안의 계수
이름·단위 칸이다(시험 `test_factor_key_fields_within_fields_of`).

| 카테고리(갈래) | 열쇠 칸 | 값 칸(rows/1) | result@1 의 키 |
|---|---|---|---|
| C1·C2 | `대표EF` | `대표EF_배출계수` | `factor.title` · 값 `factor.value` |
| C3 · C11 · C14 | `EF매핑명`·`EF단위` | `배출계수(kgCO2eq/단위)` | `factor.title`·`factor.unit` (ColabEnergyRowJson) |
| C8 · C13 | `EF단위` | `배출계수(kgCO2eq/단위)` | `factor.unit` |
| C5 | `처리방법_정규화`·`바이오매스 구분` | `배출계수(kgCO2eq/kg)` | `treatment`·`biogenic.note` (ColabActivityRowJson) |
| C12 | `바이오매스 구분` | `배출계수` | `biogenic.note` |
| C6 지출 | `EF_DB`·`NAICS` | `배출계수` | `factor.title`·`factor.code` (esg-app `spend.py` 가 EF_DB = title) |
| C6 거리 · C7 | `정규화이동수단` | `배출계수` | `mode.value` (kind `travel_distance`·`commute`) |
| C4·C9(두 갈래) | 없음 — 값만 | 지출 `EF(kgCO2e/KRW)` · 거리 `배출계수(거리기반)` | `factor.value` |
| C15 | 계수 없음(`factor: null`) | — | `factor` 가 null |

- 배출량: rows/1 `배출량(tCO2e)`(C15 `배출량`) · result@1 `co2e`.
- result@1 카테고리는 시트의 `category`(`Category6` → `C6`), 갈래는 kind `transport_distance`·`travel_distance` 만 `Distance`.
- [추정] C4·C9 지출의 USD 갈래에서 result@1 `factor.value` 는 USD 계수일 수 있어 rows/1 `EF(kgCO2e/KRW)` 와 값이 다를 수 있다 —
  같은 어댑터끼리(정답과 결과가 같은 꼴) 채점할 때는 영향 없음.

## 분류·후보 (C1·C2)

- **rows/1 에는 묶음·NAICS 칸이 없다**(`scope3colab/rows.py` `FIELDS`) — rows/1 쪽(`from_rows1`·`gold-example`) 분류는 통째로
  `{"status": "absent", "reason": "rows/1 에 묶음·NAICS 칸 없음"}`. 다른 칸(산정출처·품목 글)으로 메우지 않는다(critic 1 B1).
- result@1 쪽: 묶음 = `group.name`, NAICS = 구매 줄에 코드 칸이 없어 `absent`(`ColabFactorJson` 은 title·value·unit·column·source).
- 후보 = 대표 + 추천(겹친 제목은 앞의 것만, 빈 자리는 뺌 — 두 어댑터 같은 규칙). 1차는 `추천 = candidates[1:5]`(esg-app
  `purchase.py`)라 추천이 대표를 다시 싣지 않는다 — `from_result1` 이 실제로 겹친 수를 `notes.recommendationRepeatsRepresentative`
  로 센다. hit3 = 정답 대표 제목이 후보 **앞 셋** 안.
- 채점: 정답에 분류가 없으면 `no_gold`, 정답은 있는데 결과 쪽이 통째 absent 면 `no_result`.

## 정답 셋 변환 둘

- `gold-example`(`gold_example.py`): 받은 정답 예시(`MARC-ENT-537`, 코랩 결과 파일) → `rows_from_result_xlsx` → 단위(`sheetOrder`).
  메타 `producedBy: colab`·`reviewed: unknown` — 1차(코랩 이식)를 이 셋으로 잰 값은 「코랩과 같은가」이지 정확도가 아니다.
  C1·C2 는 `absent`(「MARC-ENT-537 에 C1·C2 시트 없음 — MARC-ENT-165 에 덧댐」). 가상 148행(`data/scope3-examples/`)은 꼴이
  달라 쓰지 않는다.
- `gold-edits`(`gold_edits.py`, 들어올 자리 — 실제 기록 없음): result@1 + `Scope3RowEditsOut` → 단위(`rowId`). 표는 모듈 머리.
  사건 없는 줄·복구만 있는 줄 = `unreviewed`(분모 밖), 제외 = `excluded`(제외가 먼저), applied = 대표, recommended r = 추천 r,
  custom = 사람이 넣은 제목·값(제목이 아닌 열쇠 칸은 빼고 견줌). 사람 이름·id·사유 글은 버린다. 분류 정답은 `absent`.

## 2차 자리

`adapters/result2.py` `from_result2` 는 늘 `AdapterUnavailable`(빈 목록 아님). `adapters/__init__.py` 가 `schema`/`schemaVersion`
으로 고른다: `scope3colab.rows/1` · `scope3.colab.result@1` · `scope3.*.result@2`(자리 — 옛 `scope3.llm-first.result@2` 도 여기서 2).
2차가 채울 것: `from_result2(json) -> AdapterOutput`(`key_by`, `units`, `sheets`) — 계수 열쇠 칸은 위 표의 이름 그대로.

## C2 — 회귀 묶음·실비 0 측정

```
uv run --frozen python -m evalrig.scope3 regress w3 [--cases regress/w3.cases.json] --answers <드라이버 답> --out <out/…>
uv run --frozen python -m evalrig.scope3 regress w5 --template <P 템플릿> --book <P 계수 책> [--answers <드라이버 답>] --out <out/…>
uv run --frozen python -m evalrig.scope3 calls --calls <calls-*.jsonl> --label <이름> --out <out/…>
uv run --frozen python -m evalrig.scope3 fuzzy --driver <L5 드라이버 JSON> --out <out/…>
```

1차 엔진을 부르는 것은 전부 git 밖 드라이버(`esg-lab/out/EVC/drivers/` — esg-app venv 로 돌고 `colab_157` 를 import)다. 이 폴더는
esg-app 을 import 하지 않고 드라이버가 낸 JSON 만 읽는다.

| 묶음 | 파일 | 결과 갈래 | 무효 조건 |
|---|---|---|---|
| W3 낱말 오검출 | `regress/w3.cases.json`(지은 일반 낱말, `expect: mode·family·codeNot`) | 맞음·틀림·답 없음(답 없음은 틀림 아님) | 1차 낱말 표(`TRANSPORT_CODE_KEYWORDS`·`detect_travel_mode_rule`·`TRAVEL_MODE_FIX_KEYWORDS`·`_FAMILY_FROM_USER_GROUP`)가 바뀌면 다시 잼. 코드 이름은 1차 체계(C4·C9 rail·sea…, C6 train·car_ev…, C1 family) |
| W5 드롭다운 ↔ 책 | `regress/w5.spec.json`(짝만 — 이름 0) | exact 사례만 맞음·틀림·답 없음, `undecided`(같은 글자 줄 없음 — 사람 판정 몫)는 밖 | 템플릿·책이 바뀌면 사례를 다시 지음(실행 때 지으니 자동). 드롭다운 열 첫 값 = 소제목 줄이라 뺌, 「선택안함」 뺌. 답은 그 책 줄의 답 열 글자와 같음(같은 이름 줄이 둘 이상이면 첫 줄) |
| L2 부름 수 | 드라이버 `calls-*.jsonl`(`{promptId, keyHash}` 만) | 프롬프트마다 전(부름 수)·후(서로 다른 열쇠 수)·줄어듦 | `key_hash` 가 `json_retry` 를 뺌 — 재시도 변수가 늘면 `RETRY_VARS` 에 더함. 1차 캐시 뒤의 부름이다 |
| L5 퍼지 표 | 드라이버 JSON(`evc.l5-driver/1`) | 정확·별칭·포함·퍼지 점수대(0.72~0.8·0.8~0.93·그 밖)·옛 판·폴백 상수·못 찾음 + 「사람 판정」 빈 칸 | 폴백 상수는 코드 값 그대로 — 출처 없음(`MARC-ENT-6`). C3 줄(`C3Lookup`)은 점수 absent |

W5 에 더하지 않은 짝(★): `DD_PURCHASE_GROUP`→`구매_USEPA`·`DD_WASTE_C5_TYPE`→`폐기물_C5`·`DD_TRANSPORT_DISTANCE`→`운송` 은 같은 글자 줄이
있으나(보고 표) 1차가 그 이름으로 책 줄을 고르는 함수가 아니다(구매 = 품목군 갈래, 폐기물 = 수만, 거리 = 표준코드) — 답 열을 정할 수 없어 뺐다.
`DD_TRAVEL_DISTANCE`→`출장_통근` 은 같은 글자 줄 0.

## MC1 측정 도구 (1차 실호출 측정 — `MARC-TICK-561`)

1차 엔진을 실제 모델로 돌린 측정(L4·L3·L1)의 셈만 둔다. 엔진을 부르는 드라이버와 산출은 git 밖 `esg-lab/out/MC1/`(main 쪽).

```
uv run --frozen python -m evalrig.scope3.measure_cost --calls <calls.jsonl> --models <esg-app models.json> --out <cost.json>
uv run --frozen python -m evalrig.scope3.measure_l4 --rows ../data/scope3-examples/rows.synthetic.jsonl --side colab \
    --result <덤프 rows/1> --order <order.json> --title-codes <title_codes.json> --out <out/…/score-<판>>
uv run --frozen python -m evalrig.scope3.measure_l4 --rows … --side llm-first --result <result@2> --title-codes … --out …
```

| 도구 | 결과 | 무효 조건 |
|---|---|---|
| `measure_cost` | 프롬프트별·모델별 부름·토큰·손셈 USD·장부(`costUsd`) USD·차이·재시도·429, 단가 표 sha256 앞 16·`checkedOn`. `cumulative_gate(spent, next_upper)` = 멈춤 규칙 2(합 > 4.50 이면 Stop, 같으면 Go) | 손셈은 esg-app `cost.compute_cost_usd` 규칙(`inputTokens` 는 캐시 뺀 입력, 장문 문턱은 입력 합)을 옮겨 적은 것 — 그 규칙이 바뀌면 같이 고침. 토큰 없는 줄은 손셈 0(`noUsage`) |
| `measure_l4` | 148행 C1·C2 91행 → 갈래 셋 `naics_in_book`(37) · `naics_off_book`(16) · `activity_only`(38). 코랩 hit@1·hit@3 분모 37(책 밖 16·활동 38 은 `structuralMiss`), llm-first 는 91 · 53 · 37 · 53/37 NAICS 코드만 · 분류 91 | 정답은 러너 라벨(검토 0). 책 코드는 `title_codes.json`(책 `구매_USEPA` 에서 뽑음) — 책이 바뀌면 37/16 이 바뀐다. 코랩 top-3 = `adapt()` 후보(겹침 뺌)의 앞 셋. llm-first 결과(`scope3.llm-first.result@2`)는 `adapt()` 가 2차 자리로 거절하므로 직접 읽는다 |
| `measure_table` | 잴 것마다 한 줄(무엇을 · 1차 판 · 결과 수(분모) · 실제 USD · 무효 조건) + 「2차 엔진에 주는 읽기」 빈 칸, L1 시트별 배출량 상대 오차 분포(중앙값 · p90 · 최대 · 5%·20% 안 · 오차 0 · 빈칸), L3 프롬프트별 지연 p50·p95 | 분포는 `score.json` `rows` 의 `co2e.kind == ok` 줄만(분모 = 정답). 중앙값은 짝수면 가운데 둘 평균, p90·p95 는 가장 가까운 순위. 입력 자리 이름이 정해져 있음(모듈 머리) — 없는 잴 것은 「안 돎」 |

```
uv run --frozen python -m evalrig.scope3.measure_table --root <esg-lab/out/MC1> --engine <esg-app 해시>
```
