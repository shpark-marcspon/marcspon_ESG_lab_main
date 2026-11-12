# evalrig — 0차 평가 장치

1차 산출(지금 엔진)과 2차 산출(result@2, 아직 없음)을 **같은 정답 셋·같은 채점기**로 재기 위한 장치다
(`sid:MARC-TICK-552` EVA · EVB · EVC, 결정 `sid:MARC-ENT-619` — 1차 변환은 esg-lab 평가 쪽에만).
AI 호출 0 · 밖으로 나가는 HTTP 0 · 새 의존 0.

## 폴더

| 경로 | 내용 | 밴드 |
|---|---|---|
| `common/` | 정답 셋 꼴 · 채점 단위 꼴 · 짝짓기·비율 · 보고 표 | EVA (소유) |
| `iro/` · `drafting/` · `translation/` | 중대성평가 · 보고서 · 영문번역 | EVA |
| `greenwash/` · `dataverify/` | 그린워싱 · 데이터 검증 | EVB |
| `scope3/` | Scope 3 | EVC |
| `dataverify/` 의 `xsrc`·`xcopy`·`recon` | 엑셀↔PDF 랩 대사 다리·다른 회사 시험지·대사 채점 | MB2 |
| `tests/` | 지은 자료 시험(네트워크 0, 실재 글 0) | 각 밴드 |

**`common/` 은 EVA 소유다.** 다른 밴드는 import 만 하고 자기 모듈 폴더만 만든다. 공통 꼴(파일·함수·칸
이름)을 바꿔야 할 것 같으면 고치지 말고 엔진 감독에게 묻는다.

명령은 `py/` 에서 `uv run --frozen --group scope3colab python -m evalrig.<모듈> <명령>`.

## 산출·정답 자리 (git 밖)

- 산출: `esg-lab/out/<밴드 소문자>/`(EVA = `out/eva/`, env `EVA_OUT`).
- 정답 셋: `prework/gold-<밴드 소문자>/*.json` + `freeze.txt`. 실재 글이 든 정답은 여기에만 두고 저장소에 넣지 않는다.
- 시험은 지은 자료만 쓴다. 실제 산출을 읽는 골든 대조는 시험이 아니라 모듈의 `golden` 명령이다.

## 공통

### 정답 셋 꼴 `evalrig.gold@1` (`common/gold.py`)

```
{schema: "evalrig.gold@1", setId, module, unit: "<이름>@<판>", version,
 coverage: exhaustive|partial, synthetic: bool,
 provenance{kind, by, from[], at},
 review{status, by?, at?, note?, sample?{ok, n}},
 pending[{what, question, slot}],
 frozen?{sha16, at}, meta?{…}, conventions?{…},
 items[…]}
```

- `module` ∈ `materiality | drafting | translation | greenwash | dataverify | scope3`.
- `provenance.kind` ∈ `runner_made | agent_draft | agent_draft_checked | official_publication | consultant | human_label`
  — 넷 칸 다 필수. `from[]` 원소는 글(경로·shortId) 또는 `{ref, sha256|null}`(sha256 은 16~64 자 hex).
- `review.status` ∈ `none | agent_sampled | human_partial | human_full` — **필수는 status 하나**, 나머지는 빠지면
  null(`review_field`). 사람 검토가 없거나 모르면 `none`. `none` 이면 `sample` 없음.
- `coverage`: `partial` 이면 정밀·F1 을 세지 않는다(`precision_allowed` · `guard_precision`) — 재현만.
- `pending[]`: 아직 안 들어온 정답의 자리(예: 컨설턴트 수정본 → 질문 shortId, `slot` 은 읽개 이름). 지금 메우지 않는다.
- `conventions{}`(선택): 모듈 자유의 판정 약속 — 판정 값 목록 `labels`, 같이 맞다고 볼 값 `accept` 등.
- `meta{}`(선택): 모듈 칸(가리키는 원 파일과 그 sha16 등). 최상위에 그 밖의 칸은 없다(`additionalProperties: false`).
- `items[]` 꼴은 모듈이 `register_unit(unit, item_schema)` 로 등록한다. 등록이 없는 unit 은 `check` 가 실패한다
  (`require_unit=False` 로만 넘김).
- `freeze(path)` → sha16 = `frozen` 을 뺀 문서의 정규 JSON sha256 앞 16자. `frozen.at` 은 따로 주지 않으면
  `provenance.at` — 같은 내용이면 파일 바이트까지 같다. 얼린 뒤 고치면 `check` 가 실패.

### 채점 단위 꼴 `evalrig.unit@1` (`common/units.py`)

`Unit(module, unit, key, group, payload{}, origin, status, sources[])` — 채점기 입력은 이 꼴 하나.

- `key`: 한 구획(변환기 한 번이 낸 목록) 안에서 유일(`check_units` 가 중복을 ValueError).
- `group`: 묶음 열쇠(토픽·잎·쪽). `status` ∈ `ok | missing | failed`.
- `sources[]`: `Source(kind ∈ web|doc|data|ref|human, ref, check{…}|None)`.
- `origin`: `Origin(engine ∈ v1|v2, schema(원 결과 판), run, converter(이름@판))`. **채점기는 engine 을 보지 않는다.**
- 변환기: `@converter("<이름>@<판>")` 장식자 — `fn.name` 을 달고 돌려준 목록을 `check_units` 한다(`Converter` Protocol).

### 짝짓기·비율 (`common/match.py`)

- `pair(gold, pred, *, key=None, sim=None, threshold=0.0, group=None, order="score"|"gold")`
  → `{pairs: [{gold, pred, score, how}], missed, extra}`(목록 차례 번호). 열쇠 같음 먼저(how `exact`), 그다음
  `sim` 탐욕 1:1 — `sim` 은 `float`(how `fuzzy`) 또는 `(float, label)`(label 이 how, 예 `exact|contain|fuzzy`).
  `group` 이면 같은 묶음 안에서만. `order="score"` 점수 내림차순, `"gold"` 정답 차례. 같은 점수는 열쇠·차례 오름차순 — 결정적.
  `count_how(result)` = how 별 짝 수.
- `ratio(num, den)` → `{value, num, den}` · 분모 0 → `{status: no_denominator, num, den: 0}`.
  `unmeasurable(why)` → `{status: unmeasurable, why, num, den}`(partial 이라 정밀 거부 등).
- `prf(tp, n_gold, n_pred)` · `macro(per_group)`(no_denominator·unmeasurable 묶음은 빼고 `skipped`) ·
  `boot_ci(values, seed=552, n=2000)`(`random.Random`, 백분위 95%).
- `text_sim(a, b)` = `difflib.SequenceMatcher` 비율(NFC·공백 접기). 공통은 의존 그룹 0 — rapidfuzz 를 쓰지 않는다.

### 보고 표 (`common/report.py`)

`write(out_dir, name, rows, gold_sets, *, columns, title, notes, extra)` → `<name>.json` + `<name>.md`.

- 표 머리에 정답 셋마다 `setId · sha16 · provenance · review · coverage · synthetic · 항목 수 · pending` 한 줄을 늘 찍는다.
  얼리지 않은 셋·메타가 빠진 셋·빈 목록은 ValueError. 정답이 없는 코드 지표 표는 `NO_GOLD` 를 넘긴다.
- 모든 줄에 `gold`(그 줄이 쓴 setId 또는 `NO_GOLD`) — md 첫 칸 「정답」 = `setId(provenance/review)`.
- 정답 셋은 `gold.check` 를 다시 거친다 — `frozen.sha16` 이 내용과 다르면(얼린 뒤 고침) ValueError.
- % 칸(이름이 `%` 로 끝나거나 `rate·recall·precision·f1·비율·재현·정밀` 이 든 칸)은 ratio 꼴만. F1 은 `den: None` 이라 분모 없이 찍힌다.
- report 는 partial 셋의 정밀 칸을 막지 않는다 — 부르는 쪽이 `guard_precision`/`precision_allowed` 로 보고 `unmeasurable("partial")` 을 넣는다.
- 실행 시각·evalrig 코드 sha16 은 json 에만 — md 는 다시 뽑아도 바이트가 같다.

## EVA 모듈 (IRO · 보고서 · 번역)

| 모듈 | 채점 단위 | 정답 셋(`prework/gold-eva/`) | 1차 변환기 | 명령 |
|---|---|---|---|---|
| `iro` | `iro.topic-branch@1` = (토픽, 갈래) + 출처 URL | `iro-empty`(0 항목, pending 607) | 앱 결과 JSON · 배포본 RAW_JSON · 코랩 응답 | `gold · convert · score · urlcheck` |
| `drafting` | `drafting.leaf@1` = 목차 잎 · 정답 `drafting.fact@1` | `drafting-sg1-{sam,cos,kb,b4}`(SG1 사실) | `drafting.bullets@3` | `gold · convert · score · golden` |
| `translation` | `translation.block@1` = 국문 블록 또는 쪽 | `translation-sam-official` · `-glossary-*` · `-review-trx7` | `translation.result@1` · trpdf res | `gold · convert · score · golden · bleu · mt5 · eva` |

- 2차 변환기 `from_v2.py` 는 자리만(`NotImplementedError`) — 머리 문서 줄에 result@2 칸 → 단위 칸 대응 표.
- 코랩 기준 실행(IR0, 원본과 같은 취급)의 단위는 파일로 쓰지 않는다 — 쓸 때마다 메모리에서 다시 변환(`iro.cli.units_for`).
- 넷째 SG1 책은 책 이름이 이름 목록에 걸려 `b4` 로 부르고, 책 자리는 `semgraph.sg1d.BOOKS` 차례로 고른다.

## EVA 측정 (실비 0)

| 측정 | 파일 | 명령 | 산출 |
|---|---|---|---|
| M-I2 URL 검사 | `iro/urlcheck.py` | `python -m evalrig.iro urlcheck` | `out/eva/mi2/summary.*` |
| M-T4 BLEU 바닥선 | `translation/bleu.py` | `python -m evalrig.translation bleu` | `out/eva/mt4/summary.*` |
| M-T5 쪽 번호 지도 고침 | `translation/mt5.py` | `python -m evalrig.translation mt5` | `out/eva/mt5/summary.*` · `out/eva/golden/mt5.json` |
| 묶음 표 | — | `python -m evalrig.translation eva` | `out/eva/summary.*`(골든 ①~④ + 측정 머리 줄) |
| MC1 Scope 3 실호출 셈(L4·L3·L1) | `scope3/measure_{cost,l4,table}.py` | `python -m evalrig.scope3.measure_cost` · `measure_l4` · `measure_table` | `esg-lab/out/MC1/`(cost·score·table — 실비는 마크스폰 키) |
| MB1 그린 GM2·GM3·GM4(근거 재현·판정 바뀜·표 덧붙임) | `greenwash/measure_mb1.py` | `python -m evalrig.greenwash.measure_mb1 recall\|flip\|result1\|ledger\|gm4` | `out/MB1/`(git 밖, 드라이버·장부·근거 파일) |
| GFC1 그린 금융 대 일반(15줄 덮임·종류·탐지·후보 줄) | `greenwash/measure_gfc1.py` | `python -m evalrig.greenwash.measure_gfc1 pages\|tally\|floor\|detectcov\|eyecheck\|f5\|ledger\|verdict` | `out/GFC1/`(git 밖, 드라이버·장부·산출) |
| GE1c 그린 근거 재현(옛·머리 줄 뒤·구성 블록 셈 · 본문/도표 core 따로 · 예산 앞자리 자름) | `greenwash/measure_ge1c.py` | 함수만(드라이버 `out/GE1c/drivers/ge1c.py recall\|gate` 가 부름) | `out/GE1c/`(git 밖, 드라이버·정답 새 판·장부·산출) |
| MTG1 번역 블록 태그 방식(T-A)·블록 단위 용어 풀(T-B) | `translation/measure_mtg1.py` | `python -m evalrig.translation.measure_mtg1 pages\|freeze\|verdict-units\|tally\|quality\|pool\|span\|floor\|eyecheck-sample\|ledger\|verdict` | `out/MTG1/`(git 밖, 드라이버·장부·산출) |
| MIC1 1부 luna + 검색 수 상한 · 출처 걸림 · 되묻기 | `iro/measure_mic1.py` | `python -m evalrig.iro.measure_mic1 flags\|summary\|verdict <out/MIC1> [mock]` | `out/MIC1/`(git 밖, 드라이버·탐침·산출) |

- M-I2: 오프라인 검사 ①~⑥ + ⑦ 열림 `not_checked`(밖 HTTP 0). 화이트리스트는 esg-app `whitelists.py` 를 글로 읽음.
- M-T4: 손 구현 corpus BLEU-4(13a, 다듬기 없음) — sacrebleu 와 같은 값임은 재지 않음.
- M-T5: `trlab.pagemap._nums` 를 같은 프로세스에서 갈아 끼우고 되돌림(파일 0줄). `out/trx8` 사본을 `out/eva/mt5/work` 에 두고 끝나면 지운다.

## MA1a 측정 (마크스폰 키 실호출 — 1차 1부 판 다섯)

| 측정 | 파일 | 명령 | 산출 |
|---|---|---|---|
| M-I1 검색 강제 · M-I5 1부 교체 · M-I4 기업 맥락 · M-I3 칸 혼재 | `iro/measure_ma1a.py` | `python -m evalrig.iro.measure_ma1a summary <out/MA1a>` | `out/MA1a/summary.*` |

- 셈은 순수(esgapi·openai import 0, 호출 0). 실호출·1차 엔진(esg-app 스냅숏)은 git 밖 드라이버 `out/MA1a/drivers/` 몫이고, 이 모듈은 그 산출(`arms/<판>/{result.json,calls.jsonl,run.json}` · `judge/{j4,j3}.jsonl` · `prices.json`)만 읽는다.
- 표 「판」(지표 갈래 `code`, 정답 없음)과 표 「판정기」(`judge:luna`, 판정기 = luna, 정답 아님)를 가름 칸으로 나눈다. 검색 수는 드라이버 기록의 hostedCalls 합(모르면 None, 재시도·잘림 칸은 하한), 같은 규제는 제목·URL 열쇠의 같음만.
- J3 사람 라벨은 없다(`out/MA1a/mi3/labels.csv` 빈 칸) — 라벨 대조는 `unmeasurable(no_labels)`.
- 판 설정(드라이버 `out/MA1a/drivers/drive.py`): 강제는 Recorder 한 곳(도구 실린 `call_tools` 만 `tool_choice=required`) · A3 luna 는 같은 프로세스에서 `TOOL_MODELS` 다시 묶음 + 모델 표 사본(`models-a3.json`, luna 검색 0.01/건) + `temperature=None` · A4 맥락 단락은 `CTX_TEMPLATE` 을 입력 파일 칸만으로 채워 규제·컨트로버시 가변 입력 끝에(템플릿 들여쓰기에 맞춤) · esg-app 0줄.
- MA1a 실판(2026-11-09, 마크스폰 키 합 2.7150395 USD): A0 5/11(상한) · A1 26/26 · A2 뺌(감독 답 3) · A3 2/11(상한, 호출당 검색 8~19건) · A4 11/11 · J4 80줄 · J3 136줄. 읽기와 표는 `band-reports/MA1a.md`.
- 「USD 같음」 이 거짓이면 기록(core costUsd)이 정본 — 손셈과의 차는 summary 주의 까닭(실패 값에서 빠진 검색 수 `MARC-ISSUE-624` · 기록에 없던 캐시 쓰기 칸)으로 읽는다.

## MA2 측정 (1차 먼저 잴 것 A② 영문번역 — 실호출, 마크스폰 키)

| 측정 | 무엇 | 명령(`python -m evalrig.translation.measure_ma2 …`) | 산출 |
|---|---|---|---|
| 준비 | 정답 셋(cos·kb 공식판 짝) · 얼림 · dry-run 어림 · 장부 | `gold` · `freeze` · `plan` · `ledger` | `prework/gold-ma2/` · `out/ma2/{first-run.txt,plan.json}` |
| M-T1 | 랩 꼴(2차 엔진 꼴의 시제품) 끝까지 vs 1차 PDF 흐름, sam 35~54 | `lab` → `mt1` (1차는 git 밖 드라이버 `out/ma2/drivers/v1_run.py`) | `out/ma2/mt1/summary.*` |
| M-T6 | 번역 모델 luna(주) vs gpt-4.1-mini(견줌), sam 35~44 | `t6` → `mt6` | `out/ma2/mt6/summary.*` |
| M-T2 | 목차 구획 vs 쪽 구획 — 불일치 S1·S4 구획 안/사이 | `t2` → `mt2` | `out/ma2/mt2/summary.*` |
| M-T3 | 지칭 설정 we·third × 책 셋 × 5쪽 × 2회 | `t3` → `mt3` | `out/ma2/mt3/summary.*` |
| 묶음 | 「낫다」 줄 · 자리별 USD · 골든 ①②③ | `golden` · `summary` | `out/ma2/{golden.json,summary.*}` |

- 랩 꼴 단계 = `translation/labpipe.py`(순수 몫) — ① body 블록 ② c3 목차 구획(쪽 경계로만 ≤ 12,000 국문 글자) ③ 1차 지시(esg-app colab_158 `prompts.py` 를 파일 경로로 읽음, sha16 대조) ④ 코드 검사(용어집 엄격·문장 수·숫자·남은 한글) ⑤ 되묻기 ⑥ V2 체크리스트 묶음 ⑦ S1+S4 ⑧ 걸린 것만 sol 고침 ⑨ 남은 걸림.
- 랩 싼 모델 = gpt-6-luna(`MARC-ENT-628`). 단위 `origin.engine = v2`, 판 `translation.labform@1` — 2차 엔진 아님.
- 장부 `out/ma2/calls.jsonl`(자리 = 태그 앞머리 `MA2/<자리>/`) + 1차 `out/ma2/v1/*/calls.jsonl`. 자리 상한 넘는 호출은 `over_cap` 으로 건너뜀, 밴드 누적 4.5 문턱은 명령 머리와 호출마다. 얼리기 전(`first-run.txt`) 실호출 안 함.
- 산출 기본은 main 쪽 절대 경로 `esg-lab/out/ma2`(`MA2_OUT`). 답 원문은 `out/ma2/**/raw/` 에만 — 있으면 다시 부르지 않는다.

## MA1b 측정 (실호출 — 마크스폰 키)

1차 보고서 초안(esg-app `drafting.draft@v2` draft 지시문·gpt-4.1-mini)에 견준 사실 단위 판·서술 단계·표째 근거·F-b′ 재료.
셈은 `drafting/measure_ma1b.py`(순수 셈, 공급사·앱 import 0), 호출·재료 짓기는 git 밖 드라이버 `esg-lab/out/MA1b/drivers/`.

| 잴 것 | 드라이버 | 판 | 산출 |
|---|---|---|---|
| M-R1 0/9 원인 | `mr1.py` | RPL1 S10 재료로 map + draft, 두 번 | `out/MA1b/mr1/result.json` · `raw/` |
| M-R2 사실 단위 판 | `mr2.py` | R2-a 1차 지시문 mini · R2-b 사실 단위 mini · R2-c 사실 단위 luna | `out/MA1b/mr2/R2-*.json` · `rows.json` |
| M-R3 서술 단계 | `mr3.py` | R2-b 통과 bullet → NARRATIVE_SYSTEM, luna · gpt-4.1 | `out/MA1b/mr3/narratives.json` · `rows.json` |
| M-R4 표째 근거 | `mr4.py` | R4-a 1차 재료(R2-a 답) · R4-b ⑤ 표째 재료 + 1차 지시문 · R4-c 표째 + TABLE_FACT_SYSTEM | `out/MA1b/mr4/` |
| M-R5 F-b′ vs map | `mr5.py` | b4 잎 14 × map 재료 · F-b′ 같은 글자 재료 | `out/MA1b/mr5/` |
| 묶음 표 | — | `python -m evalrig.drafting.measure_ma1b summary ~/Desktop/marcspon/esg-lab/out/MA1b` | `out/MA1b/summary.{json,md}` |

- 드라이버 실행: `out/MA1b/drivers/run.sh <mrN.py> [--dry-run|score]` — esg-app `968de65f` 스냅숏(`out/MA1b/app/`) 위 venv,
  `PYTHONPATH=<스냅숏 api-py>:<워크트리 py>`. 키는 부르는 서브셸에서만, 그 셸에서 pytest 금지.
- 문: 잴 것마다 calls.jsonl 실제 합 ≥ 상한이면 부르지 않음 · 시작 전 (밴드 실제 합 + 상한) ≤ 4.5 · dry-run 건수 ≠ 표 또는
  어림 > 표 × 1.5 면 멈춤. dry-run 어림 = 입력 글자 수(1자 ≈ 1토큰) + 출력 1,000(+추론 2,000).
- 이어 돌기: 잎마다 답 원문을 `raw/` 에 쓰고, 다시 돌면 있는 잎은 부르지 않는다.
- 새 숫자·덮음은 숫자 토막(`from_v1.numbers`)끼리 견준다 — 공백을 접는 `fact_hit` 은 「Scope 2 145,298」 꼴에서 숫자를 붙인다.
  정답 재현은 `score.fact_hit` 그대로. 새 이름은 대리 지표(라틴 낱말·라틴+숫자·따옴표 안) — 한글 고유명은 못 잰다.

## MTG1 측정 (마크스폰 키 실호출 — 영문번역 블록 자리 꼴 · 블록 단위 용어 풀)

| 잴 것 | 파일 | 명령 | 산출 |
|---|---|---|---|
| T-A 팔(A0 1차 구획 · A1 태그 붙임 · A2 글 태그 쪽 묶음 · B 구조화 id 칸 · C 다시 부름) | `translation/measure_mtg1.py` + git 밖 `out/MTG1/drivers/mtg1_app.py` | 드라이버 `units`·`dry`·`run`·`place` → `verdict-units --run <판>` · `tally` · `quality` | `out/MTG1/runs/<판>.units.json` · `tally.json` · `quality.json` · `place/` |
| T-B 풀·span(S1·S2·S4)·바닥 | 같은 파일 | `pool` · `span --pool <풀>\|all [--dry\|--judge-dry] [--rep k]` · `floor` | `out/MTG1/pools.json` · `span/` · `floor.json` |
| 판정·장부 | 같은 파일 | `eyecheck-sample` · `ledger` · `verdict` | `out/MTG1/summary.{json,md}` · `ledger.json` |

- 1차 코드는 esg-app 사본(`out/MTG1/app-src/<해시12>/`, `git archive`)만 import 한다 — 드라이버는 esg-app venv `-I`, 이 파일은
  사본 도우미 셋(태그 줄 정규식 글 · `pdf_tags._ratio_ok` · `pdf_tags._numbers`)만. 단가는 `--models`(사본 `models.json`).
- 분모는 모든 팔에서 같다(표·국문 없는 단위는 입력에만). 답 원문 없는 분모 단위는 call_failed(not_run), 팔마다 분모 집합이
  다르면 `tally`·`verdict` 가 거부한다. T-B 전권 번역은 판·장부 `TB-<모델>-r<k>` 로 T-A 와 따로(S 쪽은 B luna r1 을 다시 씀).
- 규칙(문턱·갈래)은 첫 실호출 전 `out/MTG1/rules.frozen.md` 에 얼린다(`freeze`). 장부는 core(`cacheReadTokens` 꼴)와
  semgraph(`cachedTokens` 꼴)를 칸으로 갈라 다시 셈한다.

## MIC1 측정 (마크스폰 키 실호출 — 1부 luna + 검색 수 상한, `sid:MARC-TICK-584`)

| 잴 것 | 파일 | 명령 | 산출 |
|---|---|---|---|
| 팔(Llo k=2 · Lmid k=5 · Llo-b 되풀이) · 되묻기(LloR) · 걸림 | `iro/measure_mic1.py` + git 밖 `out/MIC1/drivers/{drive,reask}.py` | 드라이버 `run.sh <판> mock\|mockover\|dryrun` · `run_live.sh <판>` → `flags` · `summary` · `verdict` | `out/MIC1/arms/<판>/` · `reask/LloR/` · `summary.*` · `reading.md` |

- 셈은 순수(esgapi·openai import 0, 호출 0). 실호출·1차 엔진(esg-app `968de65f` 스냅숏)은 드라이버 몫이고, 이 모듈은 산출 파일만 읽는다.
- 검색 수는 **실행된(completed) 검색**(`searchCompleted`, 드라이버가 SDK 응답에서 셈). `max_tool_calls=k` 에서 넘는 시도 하나가 status searching 으로 응답에 남아 core hostedCalls 는 k+1 — 곁 칸(`coreHostedTotal`). 모르면 None(0 아님).
- 걸림 = 항목의 URL 하나라도 root_only · 화이트리스트 밖(규제만, 판 없음은 아님) · 기간 밖 · 날짜 없음·못 읽음, 또는 URL 없음. 되묻기 셈: 통과 항목 되풀이를 먼저 빼고 풀린 = min(앞 걸림, 답 중 통과), 잃은 = max(0, 앞 걸림 − 답 항목).
- 돌지 않은 판은 표에 「미실행(감독 판단)」. MIC1 실판(2026-11-10): Llo 17/17(감독 판단으로 Llo 하나로 닫음) · LloR·Lmid·Llo-b 미실행. 읽기와 표는 `band-reports/MIC1.md`·`out/MIC1/reading.md`.
