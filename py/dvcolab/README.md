# dvcolab — 데이터 검증 코랩 대조 도구

받은 데이터 검증 코랩(데이터리뷰 v7.4, `MARC-ENT-156`)의 도구 여덟·분배기·테마 거르기·프롬프트 셋을 **코랩 코드 그대로** 지은
사례로 돌리고, 같은 사례에 대한 esg-app 엔진(`esgapi/dataverify/engines/colab_156`)의 결과와 견준다(`MARC-ENT-310`).
esg-app 시험은 손셈 기대값으로 엔진을 못박고, 「코랩과 같은가」는 이 도구가 잰다.

**저장소에 코랩 코드를 두지 않는다.** 실행할 때마다 읽기판 md(`received-derived/2026-10-15/데이터 검증 코랩/…_test시연.md`)에서
code 셀을 뽑아(`scope3colab.cells.extract_cells`) 빈 이름공간 하나에서 돌린다. 입력·가짜 AI 답은 전부 지은 것이다(`synthetic`).
LLM·네트워크 호출 0 — 소켓을 막고 돈다. 새 의존성 0(stdlib 와 scope3colab 의 셀 뽑기만).

## 명령

`py/` 에서:

```
uv run --frozen python -m dvcolab run [--colab-md <읽기판.md>] [--engine-result <엔진.json>] [--cases-out <사례.json>]
                                      [--out <폴더>] [--random N --seed S] [--forbidden <낱말 목록>]
uv run --frozen pytest -q dvcolab/tests
```

엔진 쪽 JSON 은 esg-app 워크트리의 `apps/api-py` 에서 낸다(esg-lab 은 esg-app 을 import 하지 않는다):

```
uv run --frozen python scripts/dataverify_compare_dump.py --cases <out>/cases.json --out <엔진.json>
```

흐름: ① `--engine-result` 없이 돌려 `cases.json` 을 얻는다 → ② 엔진 명령 → ③ 같은 `--random/--seed` 로 `--engine-result` 를 주고
다시 돌린다. 종료 코드: 같음 0, 모르는 차이 1, 도구 실패 2. 산출은 `esg-lab/out/dvcolab/<날짜시각>/`(git 밖):
`cases.json`·`colab.json`·`diff.md`·`run.json`·`colab-stdout.log`·`work/`.

## 어떻게 도는가

| 것 | 어떻게 |
|---|---|
| 돌리는 셀 | 3·4·7·10·14·18(`cells.RUN`). 안 돌리는 code 셀과 까닭은 `cells.NOT_RUN`. 둘 밖의 code 셀이 나오면 도구 실패 |
| 셀 2 | 돌리지 않는다. 하네스가 지은 `data`·`fname` 과 셀 2 의 import 셋(json·os·islice)을 미리 넣는다 |
| 건너뛰는 줄 | 없다(`cells.SKIP_RULES = ()`, 2026-10-19 실측) |
| `google.colab` | 가짜: `userdata.get` 은 가짜 글(키 아님), `drive`·`files` 는 부르면 멈춤 |
| `requests` | 가짜 `post`: Responses 자리만 받는다. 보낸 system·user 를 적고 짜 둔 답(`answers.synthetic.json`, 도구 호출 없음)을 차례대로 |
| 캐시 | 사례마다 하네스가 코랩의 `_value_cache` 를 새 dict 로 갈아 끼운다(코랩 코드 고침 아님, `run.json` 에 적음) |
| 셀 14 의 `HINTS` | 셀 10 의 짜 둔 답(목록 글)을 코랩이 `ast.literal_eval` 한다 — 사례의 hints 와 같은지 하네스가 단언 |
| 멈춤 | 네트워크·답 모자람·막은 것 사용은 `BaseException` 으로 던져 코랩의 `except Exception` 을 지나간다 |
| 출력 취급 | `out/dvcolab/` 의 `colab.json`·`diff.md`·`colab-stdout.log` 는 코랩이 보낸 글(셀 14 예시 수 등)을 담는다 — 읽기판과 같은 취급(수정·외부 전송·저장소 복사 0) |
| 금지 낱말 | `colab.json`·`diff.md`·`colab-stdout.log` 에 걸리면 도구 실패(기본 목록: 작업 폴더의 `esg-app.worktrees/G1a.out/forbidden-words.txt`) |

## 사례와 견줌

- 사례 꼴 `dvcolab.cases/1` 의 정본은 `cases.py` 머리. 손 사례는 esg-app DV1a 시험의 사례 전부, 무작위는 `--random N --seed S`
  (같은 seed 면 `colab.json` 바이트가 같다).
- 견줌(`compare.py`): 사례는 `outcome` 과 `value` 의 json 글(키 차례·NaN), 프롬프트는 바이트(다르면 줄 hunk), 상수는 json 글.
- 알려진 차이(`known.py`): 기업 정보 네 줄(DV1a 「다른 자리」 #8), 셀 14 예시 수(#9). 열쇠는 우리 쪽 글자로만 적는다.
  목록 밖 hunk·사례·상수 차이와 코랩 쪽 기대 사례(약점 5 의 옛 값 등) 어긋남은 「모르는 차이」다.

## 고리 대조(DV1b)

사례의 선택 칸 `loops`(꼴은 `cases.py` 머리)는 셀 7 의 Responses 고리를 짜 둔 답 열로 돌린다. 코랩 쪽은 셀 10·14·18 뒤
이름공간의 고리 함수를 가짜 `requests.post`(고리 모드 — `sandbox.Sandbox.start_loop`)로 부르고, 엔진 쪽은 esg-app 흐름
`dataverify.colab@156` 의 `run_loop` 을 core.llm 의 모의 공급사로 돈다. 견줌 칸은 실행된 (이름, 인자 글) 차례 ·
`function_call_output` 글 · 공급사 호출 수 · 끝남/부분 · 마지막 글이고, 알려진 차이는 0 이 기대값이다. 손 사례 ⓐ~ⓚ 열하나,
`--random N --seed S` 면 무작위 고리 N/50 개(1 이상)를 더한다. 고리 사례는 셀 10 뒤에 돈다 — 고리가 쓰는 주소 상수를 셀 7 이
아니라 셀 10 이 정의한다(셀 7 의 정의 줄은 글자 안·주석).
