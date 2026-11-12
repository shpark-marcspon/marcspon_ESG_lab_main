# py/raslab — 래스터 그림 보고서 측정 (RAS1 `MARC-TICK-480`)

조사용 스크립트. 제품 코드가 아니고 esg-lab 의존에 아무것도 넣지 않는다(`uv run --no-project --with …`).
보고서 PDF·정답·이미지·응답은 저장소 밖(경로는 인자로, 보고서는 `key=<pdf 파일>` 로만 부른다). 원시 산출은 메인 체크아웃 `esg-lab/out/ras1/`.
`py/pdx/` 모듈은 import 로만 다시 쓰고 고치지 않는다.

| 파일 | 하는 일 | 도는 자리 |
|---|---|---|
| `scan.py` | 쪽마다 래스터 조각(form XObject 안은 쪽 좌표로)·합집합 넓이·글자 층·겹친 글자·패스 수(글자 윤곽선 의심) | `--with pypdfium2==5.14.0` |
| `sheet.py` | 래스터 쪽 모음·조각 모음(눈 분류용)·한 쪽 확대 렌더 | `--with pypdfium2==5.14.0 --with pillow==12.3.0` |
| `gold.py` | 손으로 옮긴 정답 spec(격자·글 줄)을 검사해 `tables/`(table_score 꼴)·`figures/`(figure_run 꼴) 로 쓰고 해시 | 표준 라이브러리 |
| `run.py` | G2: 찾은 영역 잘라 부르기(call)·후보 산출을 정답 항목마다 모으기(collect)·분류×갈래 채점(score) | `--with openai==3.26.0 --with pypdfium2==5.14.0 --with pillow==12.3.0`, collect·score 는 표준 라이브러리 |
| `regions.py` | 그림 영역 찾기(이미지 조각 → 이웃 합치기 → 글자 층 없는 쪽은 화소 덩어리 덧길). 정답을 보지 않음 | 위와 같음 |

재현 명령과 숫자는 보고 `band-reports/RAS1.md`.

## TRX9 — 글자 층 없는 쪽 갈래(훑개 정밀·재현 19권) · luna 읽기→검사→걸린 영역만 sol · 자르기 배율 (`MARC-TICK-513`)

venv 안에서 돈다(`uv run --frozen --group scope3colab [--with openai==3.26.0] python -m raslab.ras2_<x> …`, 자리 `esg-lab(.worktrees/<밴드>)/py`). PIL 없이 numpy 렌더 + `trlab.thumb_vision.png_bytes`. 책은 `prework/gold-trx9/books.json` 의 짧은 키로만 부른다. 산출 `esg-lab/out/trx9/`(env `TRX9_OUT`).

| 파일 | 하는 일 |
|---|---|
| `ras2_scan.py` | ① `scan.scan` 위에 특징(textless_big·path_density·글 줄 꼴 점수)을 더해 쪽마다 갈래 `none|raster|outline|both`. `scan`·`sample`(층화 표본)·`render`/`sheet`(라벨용)·`branch`·`eval`(정밀·재현·민감도). 문턱 `T` 는 개발 셋으로 정해 얼림(`out/trx9/first-run.txt`) |
| `ras2_check.py` | ② 영역 응답의 수치 검사 R1 소수점 섞임·R2 자리수 뜀·R3 합계·R4 둘째 읽기 불일치·R5 단위 어림·`no_table`. 얼린 뒤 고치지 않음. 소수점 지시문 `DECIMAL_HINT` |
| `ras2_pick.py` | ②·③ 자리·장부(`out/trx9/calls.jsonl`)·자리 상한(luna 0.1·sol 0.5·합 0.6, 판 b2/sol 0.25·b3/sol 0.15, over_cap)·자르기(180dpi·native ≤ 600dpi·4Mpx)·부르기(`run.INSTR`·`SCHEMA` 그대로)·②a 흉내(RAS1 raw)·채점(`run.cmd_score`) |
| `ras2_report.py` | 표 ①②③ → `out/trx9/summary.{json,md}` |
| `tests/test_ras2_*.py` | 지어낸 자료, 네트워크 0 — `pytest trlab raslab -q` 에 같이 돈다 |

숫자와 재현 명령은 보고 `band-reports/TRX9.md`.

## TRX11 — raster 갈래 앞 사진 가르기(①) · outline'(②) · 작은 그림 묶음·216dpi 재점수(③) (`MARC-TICK-519`)

`ras2_scan` 위에 얹는 둘째 겹. 실호출 0 · PIL 0 · 새 의존 0. `ras2_scan`·`scan` 은 import 만. 문턱은 개발 셋(TRX9 dev 47 + `ras3_eval.DEV_ADD` = b7·b3·nb·nb2 40쪽)으로 정해 얼렸고(`out/trx11/first-run.txt`), 보류 13권은 한 번만 평가했다.

| 파일 | 하는 일 |
|---|---|
| `ras3_photo.py` | `scan`: 19권 쪽마다 ① 큰 조각의 사진 특징(`bg_frac`·`n_colors`·`sat_med`·`edge_density` + 메타 `jpeg`·`colorspace`·`bpp`)과 가르기 `is_photo`(둘 이하 AND, 얼린 값 `PHOTO_RULE = bg_frac < 0.436`) · ② 글자 크기 패스 수 `small_paths`(높이 ≤ 14pt·너비 ≤ 40pt)와 `outline2`(T4 100·R 1.5) · ③a 작은 조각 묶음 `clusters`(바닥 0.5%·틈 1.5%·합집합 ≥ T5 1%) · ③b 넓이 < 5% 조각 216dpi 재점수(`score216`). 산출 `out/trx11/scan/<키>.json`(TRX9 레코드 + `features3{pieces3, small_paths, n_clusters}`) · `times.json`. `render`: 낱장 PNG |
| `ras3_eval.py` | 판 다섯 `base`(TRX9 특징을 `ras2_scan.branch` 로 다시 셈) · `photo`(①) · `outline`(②, 옛 규칙 대체) · `small`(③a+b) · `all`. `check`(기준선 == `out/trx9/branch.json`) · `grid`(개발 셋 격자) · `eval dev|hold|all`(P/R·갈래·kinds·기준선 대비 고친/망친·민감도 ×0.5·×2) · `branch`(19권 × 판) · `summary` → `out/trx11/eval-*.json`·`branch.json`·`summary.md` |
| `tests/test_ras3_*.py` | 지어낸 배열·bbox·특징 dict 만, PDF 안 염 |

함정: `pieces3[].kind` 는 **스캔 때의 자리 표시 규칙**으로 적힌 값이다(얼린 규칙 전). 판정은 `ras3_eval` 이 `is_photo(p, cfg["rule"])` 로 다시 셈하니 결과는 맞고, 조각의 사진 여부를 볼 때는 `eval-*.json` 의 `why.pieces` 줄을 본다. 묶음의 넓이 바닥은 T1(2%)이 아니라 T5(1%)다(`ras3_eval.raster3`).

숫자와 재현 명령은 보고 `band-reports/TRX11.md`.
