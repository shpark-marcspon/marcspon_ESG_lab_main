# miglab — 1차 엔진 모델 이행 시험 (MIG1, `MARC-TICK-478`)

esg-app 1차 엔진 일곱 흐름을 **코드 그대로** 두 판(옛 모델 / gpt-6-luna)으로 돌려 견준다. 바꾸는 것은 모델(랩 models 파일의
경로)과, luna 가 받지 않는 온도 칸뿐이다. 프롬프트·추론 강도·흐름 코드는 손대지 않는다.

esg-app 은 **main 체크아웃을 읽기만** 한다: 그 가상환경 python 을 `-I` 로 띄우고 `apps/api-py` 를 sys.path 에 넣는다
(`env.py`). esg-app 에서 `uv sync`·파일 쓰기 0. esg-app 시험 모듈의 지은 입력(`*/tests/*/support.py`)은 import 해서 쓴다.

## 명령

작업 폴더(marcspon)에서:

```
PY=esg-app/apps/api-py/.venv/bin/python
$PY -I esg-lab/py/miglab/__main__.py plan     --module all --pdf <공개 PDF> --pages 39~43   # 모의, 실호출 0
$PY -I esg-lab/py/miglab/__main__.py dry-run  --module all --pdf <공개 PDF> --pages 39~43   # assemble_only, 실호출 0
$PY -I esg-lab/py/miglab/__main__.py run --mock --variant old|luna --module tr               # 모의 한 판
set -a; . ~/.config/marcspon-openai.env; set +a                                              # 마크스폰 키(값 출력 0)
$PY -I esg-lab/py/miglab/__main__.py probe --module dv                                       # live · luna · 작은 입력
$PY -I esg-lab/py/miglab/__main__.py run   --module dv --variant old --label pair            # live · 전체 입력
$PY -I esg-lab/py/miglab/__main__.py compare --label pair                                    # 견줌 표(실호출 0)
cd esg-lab/py && ../../esg-app/apps/api-py/.venv/bin/python -I -m pytest miglab/tests -q -p no:cacheprovider
```

모듈 키: `gw` 그린워싱 e0 · `tr` 영문번역 colab_158 · `dv` 데이터 검증 colab_156 · `mt1` 중대성평가 1부 · `mt` 1부 → 2부 ·
`s3` Scope 3 colab_157 · `dr` 원고작성 v2. (`mt2` 는 혼자 돌지 않는다 — `mt` 가 1부 결과를 씨앗으로 2부를 잇는다.)
`gw` 만 공개 PDF 를 받는다(경로는 인자 — 기록에는 sha256 앞 12자). 나머지 입력은 `fixtures.py` 와 esg-app 시험 support 의
지은 것이다.

## 판과 모드

| 판 | 랩 models 파일(`out/mig1/models-<판>.json`, 실행 때 esg-app models.json 에서 지음) | 온도 | 도구 고리 |
|---|---|---|---|
| `old` | 그대로 | 코드 값 | 그대로 |
| `luna-raw` | **routes 만**: `light-openai-41`·`standard-openai-41`·`standard-openai-4o` → `["gpt-6-luna"]` (= 길 (ㄱ) 재지정만) | 코드 값 | 그대로 |
| `luna` | luna-raw + luna 항목 `hostedToolPricing.webSearchPerCall` 0.01(가격표 「Web search (all models)」 줄, 11-03) | None | `client.TOOL_MODELS` 에 luna(실행 하나 동안) |

`luna` 의 두 덧댐은 감독 판단 ①(발주서 §6)이 허락한 것이고, esg-app 객체를 바꾸는 자리는 `harness.patch_tool_models`
하나다(실행이 끝나면 `restore_tool_models`). 온도 None 은 감독 판단 ② — 「모델 + 온도 빠짐」을 한 변수로 본다.

| 모드 | 공급사 | 기록 |
|---|---|---|
| mock(`plan`·`--mock`) | 기록기 + 모의 답(`responders.py`, 뜻 없음) | 실행 폴더 `calls.jsonl` |
| dry(`dry-run`) | `build_client(assemble_only=True)` 의 실제 OpenAI 어댑터 — 몸만 짓고 「클라이언트 없음」 | 실행 폴더 |
| live(`probe`·`run`) | 실제 OpenAI(마크스폰 키, `LlmSettings` 를 하네스가 직접 지음 — esg-app 로더·`llm_call` 0) | **`out/mig1/calls.jsonl`(장부)** |

- 상한: `build_client(limits=[BudgetLimit(Scope(), 5.0)])`. 싱크(`harness.LabSink`)의 `spent` 가 장부의 live 합을 더하므로
  티켓 누적 5 USD 에서 core 가 공급사 전에 막는다. 한 번에 한 프로세스만 돈다(두 프로세스는 서로를 못 센다).
- 장부 줄 = `{"lab": {module, size, label, run, mode, variant}, **CallRecord.to_json()}` — 칸 이름은 core 그대로
  (`resultKind`·`costUsd`·`thinkingTokens`).
- 실행 폴더 `out/mig1/runs/<시각>-<모드>-<모듈>-<판>-<크기>/`: `summary.json`(호출 셈·지표·거름·덧댐),
  `result.json`(흐름 결과 — 보고서 글이 들 수 있다, git 밖만), `bodies.jsonl`(공급사에 보낸 몸).
- `compare --label L`: 장부에서 label L 의 live 실행을 모듈·판마다 마지막 것으로 골라, 지표를 result.json 에서 다시 셈해
  `out/mig1/compare-L.json` 에 쓴다. 겹침(gw 문장·dv 도구:종류)은 글의 sha256 앞 12자 열쇠로, s3 는 줄마다 카테고리·계수 제목·
  묶음 이름 일치.

## 시험

`tests/test_miglab.py` — 랩 models 파일이 routes(와 luna 의 요금 한 칸)만 바꾸는지, 다섯 모듈(tr·dv·mt·s3·dr)이 두 판에서
**assemble_only 로 지은 요청 몸이 model·temperature·max_output_tokens 셋 말고 같은지**(luna 의 max_output_tokens = 옛 값 +
reasoningOutputReserveTokens 2000), luna-raw 가 온도를 싣는 흐름에서 공급사 전에 거절되는지, 몽키패치가 다음 판에 새지
않는지, 장부 셈과 luna 단가 손셈. 실호출·키·네트워크 0.
