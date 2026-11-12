# evalrig.dataverify — 데이터 검증 평가 장치 (EVB, `MARC-TICK-553`)

채점 단위는 (지표 ID, 연도) + 판정이고, 셈은 코드로만 한다(모델 0). 정답 셋 머리는 EVA 공통 `evalrig.gold@1` 꼴을 따르고, items 한 줄의 꼴은 `units.py` 의 `dataverify.indicator_year@1` 이다. 정답은 모두 러너가 지었고 사람 검토는 0 이다(`review.status: none`). 컨설턴트 정답이 들어올 자리는 `pending[]`(`MARC-ENT-607`·`MARC-ENT-8`)에 둔다.

## 시험지 (C1)

| 셋 | 코드 | git 파일(`data/evalrig/dataverify/`) | 단위 | coverage |
|---|---|---|---|---|
| ① 가상 기업 확장판 — 서는 관계 8(합 3·비율 2·집약도 3), 심는 오류 다섯 종 × 2와 허용 오차 경계 짝 | `synth.py` | `synthetic-company.input.json` · `.gold.json` | 「환경」 17지표 × 3년 = 51 | exhaustive |
| ② 엑셀 원천 ↔ PDF 대사 — KB 2025(공개 보고서) 문서 a 의 지표 칸 44, 심기 여덟 건(종 여섯) | `xpdf.py` | `kb-2025-xpdf.plants.json` · `.gold.json` | 44칸 + 원천에만 있는 3칸 = 47 | exhaustive(연도 없는 4칸 뺌) |
| ③ 리스크 짝 — 메모 칸만 다른 짝 다섯(튐 둘·엇갈린 추세 하나·S/G 빈칸 둘) | `risk.py` | `synthetic-risk.input.json` · `.gold.json` | 13행 | exhaustive |

- 다시 짓기(`py/` 에서): `uv run --frozen python -m evalrig.dataverify build-exam --out <폴더>`. 여섯 파일이 git 판과 바이트까지 같아야 한다(시험 T6·T7·T8). ②의 엑셀은 git 밖 `out/evb/xpdf/kb-2025.source.xlsx` 에만 짓는다. 결정성은 엑셀 바이트가 아니라 시트 값을 다시 읽어 잰다.
- ②는 PDF 의 sha256 이 `kb-2025.gold.json` 의 `documents.a.pdfSha256` 와 같을 때만 짓는다. KB 수치는 git 에 이미 있는 gold 의 `raw` 만 쓴다.
- 허용 오차 1.1 은 1차(colab_156)의 값을 그대로 쓴다. 이 값은 절댓값이다(합은 단위 그대로, 비율은 %p). 집약도 단위에서는 몇 % 어긋나도 이 오차 안에 든다(①의 E-003 2023 → int1 의 차 0.88).
- 1차(colab_156)에는 두 문서 대사가 없다. 그래서 ②의 1차 점수는 `not_applicable` 이다.

## 시험

`uv run --frozen --group greenwash pytest -q evalrig/dataverify/tests`. 입력 꼴은 esg-app 의 고정 스키마 `dataverify.input@1.schema.json` 을 읽기만 해서 검증한다(`ESG_APP_ROOT`). 스키마 파일이 없으면 건너뛰지 않고 실패로 친다.

## 변환기·채점기 (C3, 실비 0)

- `convert_156.py`: `dataverify.result@2` 를 그 실행의 입력과 함께 받아 채점 단위로 바꾼다.
  - 검산은 해마다 단위 하나 `{items[ID], unresolved[넷], year, verdict, rule}` 다. 항목 넷 → ID 는 입력 행의 네 칸 글자가 같은 것으로 푼다.
  - 인사이트 행은 소분류 글자로, 그다음 글 안의 ID(NFKC·대시 맞춤)로 ID 를 푼다.
  - 못 푼 것은 `unresolved` 로 센다. `review.status` 가 failed·not_run 이면 머리에 싣는다.
  - mig1 목록 안의 `dataverify.digest@1` 은 결과가 아니라서 건너뛰고 수만 센다.
- `adapt_v2.py`: 2차 결과 어댑터의 자리다(`NotReady`). 새 엔진은 항목을 ID 로 실어야 한다.
- `score.py`:
  - 검산 정답(가상 기업): 짝 규칙은 정답 (ID, 연도) ∈ FAIL 단위의 items × {year} 다. 재현·정밀·종류별 재현을 내고, 항목이 안 풀린 FAIL 단위는 따로 센다. 정밀 분모는 완전 해석 단위(unresolved 빔)만이고, 부분 미해석 단위는 `failUnitsPartiallyUnresolved`·`partialWithGoldFail` 로 따로 센다(재현에는 든다, MARC-ISSUE-623).
  - 리스크 정답: flag_no_note 재현, explained 를 낸 비, 「주석 없음만 냄」 짝 비를 낸다.
  - 엑셀↔PDF 정답: `not_applicable` 이다.
- CLI 명령은 `convert` · `score` 다(`--input` 이 꼭 있어야 함).

## 랩 대사 다리·다른 회사 시험지·대사 채점 (MB2 C2, `MARC-TICK-560`)

- `xsrc.py`: 엑셀 원천 행 → 랩 문서 모델(쪽 1, 표 `p1-t1`, 머리 행 0·머리 열 0~7) 과 `cell_map`(값 후보 ID → (ID, 연도)). `unplant` 는 심기 전 원천(같은 행·칸 ID, 심은 칸만 보고서 글자로)을 짓는다 — 랩 `cli7 runr --a` 의 원본.
- `xcopy.py`: 보고서 문서 모델 + 랩 `facts` 출력 → 다른 회사 엑셀↔PDF 시험지(씨앗 `20261111`, 44칸 이상, 심기 `xpdf.PLAN` + source_only). 원천 이름이 보고서 머리글 글자 그대로라 KB 판(목록 이름)보다 쉬운 조건이다. 산출은 보고서 글자를 품으므로 git 밖에만.
- `recon.py`: 엑셀↔PDF 정답 × 랩 대사 verdicts(d2·r·h2) → 정답 칸마다 갈래(paired_right·paired_elsewhere·unpaired·paired_false)와 수(짝 재현·심은 오류 잡음·거짓 일치·거짓 경보·대조군·source/report_only). verdicts 의 a 가 하나도 `cell_map` 에 없으면 멈춘다.
- 명령(`py/` 에서, 산출은 `out/MB2/` 아래에만): `xsrc <xlsx> --out <json> [--unplant <plants.json>]` · `xcopy <document.json> --facts <facts.json> --out <폴더>` · `score-recon <정답> --cells <json> --verdicts <outcome.json> --stage d2|r|h2 [--out <폴더>]`.
- `xcopy` 의 고르는 규칙(실측 전에 고정, 보고에도 적음 — 정답 rules 칸에 싣는 것은 다음 밴드, 정답 version 2): ① 연도는 2023~2025 만, 같은 연도가 두 열이면 그 연도는 버린다 ② 이름·연도 열이 아닌 열이 있는 표(연도 하나가 열 둘에 걸친 표)는 뺀다 ③ 값 후보는 % 아님·단위 꼬리 없음·부호 없음 ④ 단위는 머리 글이 비었거나 「단위」 인 머리 열 하나에서(없으면 `unitLines`).

## 랩 실험 판·리스크 규칙·흔들림 셈 (MB2 C3, `MARC-TICK-560`)

- `labfix.py`: 1차 검사 도구 셋과 같은 셈·같은 키(실패 갈래 포함)에 허용 오차만 표시 자릿수 반올림 규칙으로(합 0.5×10^−d×(항 수+1) · 비율·집약도 0.5×10^−decimals(목표)), 기대값 단위에 「%」 가 없으면 집약도(×100 없음). **실험 가정이지 판정 규칙이 아니다**(`MARC-ENT-41` 열림). git 밖 드라이버가 실행 중에만 1차 분배기의 이름 셋을 감싼다(1차 코드 그대로).
- `riskrules.py`: θ 10%(민감도 20·30%) 의 jump·mixed_trend·blank 후보, 메모가 빈 후보만 내는 B0(호출 0), AI 해석 요청 글(`render_request`)과 답 확인(`check_answer`).
- `measure_vm.py`: 같은 입력 여러 실행의 흔들림(`jitter`), 판 견줌(`arms_table` — 집약도 호출 0 이면 「판정 불가」), 리스크 두 분모(`risk_table` 전체·환경만).
- 명령: `measure jitter|arms|risk --runs <실행 폴더,…> --input <input.json> --gold <정답> [--intensity-ids …] [--b <답 확인 파일,…>] [--out <out/MB2/…>]`.
