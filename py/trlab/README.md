# trlab — 영문번역 인터뷰 후속 시험 셋 (TRX1, `MARC-TICK-479`)

컨설턴트 인터뷰(2026-11-03)가 「테스트 필요」로 남긴 셋을 잰다: (가) 목차 ↔ 본문 범위, (나) 용어집 위반·불일치 검출, (다) 회사 지칭.
책은 파일 키 `sam`·`cos`·`kb` 로만 부른다. 실제 회사 이름은 코드·시험·이 문서에 두지 않고 `prework/gold-trx1/names/<키>.json` 에서 읽는다.

## 경로
- 입력 분해본: `esg-lab/out/trx1/parse/<키>-<ko|en>/document.json` (docparse **layout 2**, 아래 명령)
- 산출: `esg-lab/out/trx1/` (git 밖, 환경 변수 `TRX1_OUT` 로 바꿈) · 정답: `prework/gold-trx1/` (git 밖, `TRX1_GOLD`)
- 의존: `rapidfuzz` 는 `--group scope3colab`, 실호출은 `--with openai==3.26.0`(키는 `OPENAI_API_KEY` 에서만, 호출마다 `out/trx1/calls.jsonl`)

## 명령 (`esg-lab/py` 에서)
```
# 0. 분해 (esg-app/services/parser 에서, 책마다 ko·en)
PARSER_REPORTS_DIR=…/prework/reports uv run --frozen python -m docparse parse …/<파일>.pdf --out …/esg-lab/out/trx1/parse/<키>-<ko|en> --layout 2
# 공통 바닥: 국·영 블록 짝 → out/trx1/align/<키>.json (덮음률 찍음)
uv run --frozen python -m trlab.align
# T-a: 목차 ↔ 본문 범위 (정답 있으면 채점) → out/trx1/toc/<키>-<lang>.json
uv run --frozen --group scope3colab python -m trlab.toc <키> <ko|en> --gold
# T-a luna 덧댐(조건부) · 코드+luna 합침 · 표
uv run --frozen --group scope3colab --with openai==3.26.0 python -m trlab.toc_luna <키> <lang> [--dry-run|--combine]
uv run --frozen python -m trlab.ta_report
# T-b: 용어집 후보(사람이 고름) · 검사기(공식판 / 엔진 산출 / 용어집 넣은 재실행)
uv run --frozen python -m trlab.glossary_cands <키>
uv run --frozen python -m trlab.glossary_check <키> [--site engine] [--model <m> --res <res 폴더>]
# T-c: 지칭 꼴 세기 · 지킴률 · we → the Company 바꾸기 스케치
uv run --frozen python -m trlab.subject <키> [--site engine]
# 시험
uv run --frozen pytest trlab -q
```

## 모듈
| 파일 | 하는 일 |
|---|---|
| `common.py` | document.json → `Block` 목록(겹쳐 그린 블록 하나로), 경로, 「영문 + 국문」 제목 떼기 |
| `align.py` | 쪽 짝(같은 PDF 쪽) · 쪽 안 블록 짝(자리·숫자/라틴 닻·차례·kind, 1:n 붙임, 표 칸 짝) · 되풀이 띠 거름 |
| `toc.py` | 목차 쪽 찾기 · 항목 뽑기(표 칸 짝·따로 떨어진 번호·장 머리) · 꼬리말 번호로 쪽 사상 · 항목→제목 블록 · 범위 · 중제목 후보 · 채점 |
| `toc_luna.py` | 목차 글 + 후보 제목 목록을 luna 에 주고 항목마다 시작 블록을 고르게 함, 코드와 합침 |
| `ta_report.py` | T-a 표 |
| `glossary_cands.py` | 용어집 후보: 「영문+국문」 제목 · T-a 토픽 짝 · 표 칸 짝 · 국문 끝말 + 짝 블록 n-gram Dice |
| `subject.py` | 문장 나누기 · 지칭 꼴(we / 이름 / the Company류 / 없음)과 주어 꼴 · 문단 지킴률 · 토픽 섞임 · 규칙 판 바꾸기 |
| `glossary_check.py` | 위반(엄격 = 1차 엔진 `in` / 관대 = 활용·하이픈·소유격·줄 끝) · 불일치(용어집 없이, Dice 대응 표현이 둘 이상) · 자리별 짝 |

## TRX2 — 목차 쪽 이미지 읽기 + 글자 속성 제목 후보 + AI 고르기 (`sid:MARC-TICK-482`)

입력: 분해본 `esg-lab/out/trx2/parse/<키>-<lang>/`(없으면 `out/trx1/parse/`), 키 → PDF 사상 `prework/gold-trx2/books.json`(git 밖),
정답 `prework/gold-trx2/toc/nb-ko.json` · `prework/gold-trx1/toc/*.json`. 산출 `esg-lab/out/trx2/`(env `TRX2_OUT`, 정답 `TRX2_GOLD`).
rapidfuzz 가 `scope3colab` 묶음이라 명령마다 `--group scope3colab`.

```
# G0 글자 속성(크기·굵기·색·자리) → out/trx2/attrs/<키>-<lang>.json (자기 점검: font_size 일치 세 값)
uv run --frozen --group scope3colab python -m trlab.glyphs <키> <lang>
# G0 제목 후보(쪽마다 상위 K + 간지 큰 블록) → out/trx2/cands/<키>-<lang>.json (정답 있으면 후보 재현율·간지 판정)
uv run --frozen --group scope3colab python -m trlab.cands <키> <lang>
# G1 목차 쪽 찾기 + 렌더 이미지를 gpt-6.1-sol 한 호출로 읽기 → out/trx2/g1/<키>-<lang>.json (읽기 정확, TRX1 코드 판 옆 열)
set -a; . ~/.config/marcspon-openai.env; set +a
uv run --frozen --group scope3colab --with openai==3.26.0 python -m trlab.toc_vision <키> <lang> [--dry-run|--score]
# G2 판 여섯(a 코드만 · b TRX1 luna · c G1+코드 · d 속성+AI luna · e 속성 뺀 AI · dsol) → out/trx2/g2/<키>-<lang>-<판>.json
uv run --frozen --group scope3colab --with openai==3.26.0 python -m trlab.toc_pick <키> <lang> <판> [--dry-run]
# 표 → out/trx2/summary.json (+ 마크다운)
uv run --frozen --group scope3colab python -m trlab.tx2_report
```

| 파일 | 하는 일 |
|---|---|
| `glyphs.py` | pypdfium2 raw 로 글자(실효 크기·weight·색·상자)를 읽어 블록에 붙임, 책 본문 기준, 블록 속성 |
| `toc_vision.py` | 목차 쪽 찾기(낱말·toc_score, 이어진 쪽 최대 3) · docparse render · 이미지 메시지 · 자리 상한 0.4 USD 셈 · 읽기 정확 |
| `toc_pick.py` | 창(번호 ±1 · 번호 없음 = 앞뒤 번호 사이 최대 12쪽 · 번호 0 = 책 전체) · 판 여섯 · "" 메움 · 자리 상한 셈 · 간지 두 판(A 그대로 / B 간지 다음 쪽) |
| `tx2_report.py` | G0 후보 재현율 · G1 읽기 정확 · G2 판 표 · 자리별 실호출 합 |
| `cands.py` | 되풀이 제목 거름(이어진 쪽 줄의 첫 쪽만) · 속성 후보 · 간지 쪽 · 후보 줄 · 후보 재현율(TRX1 두 후보 집합과 비교) |

## TRX3 — 목차↔본문 범위 잇기 100% 가까이 (`MARC-TICK-491`)

간지 항목은 간지 쪽(A)·다음 쪽(B) 어느 쪽이든 정답으로 센다(채점에서만, 정답 파일은 간지 쪽 그대로). 산출 `esg-lab/out/trx3/`(env `TRX3_OUT`),
자리 찾기는 `out/trx3` → `out/trx2` → `out/trx1`. 새 책의 분해·attrs·cands·G1 은 TRX2 모듈을 `TRX2_OUT=…/out/trx3` 로 돌린다.

```
cd esg-lab/py   # 워크트리에서는 esg-lab.worktrees/<밴드>/py
uv run --frozen --group scope3colab python -m trlab.tx3_pick rescore          # H0 A∨B 재셈 → out/trx3/h0.json
uv run --frozen --group scope3colab python -m trlab.pagemap <키> <lang>      # H1 쪽 번호 지도 → out/trx3/pagemap/
uv run --frozen --group scope3colab python -m trlab.order_win <키> <lang>    # H2 차례 DP·창 → out/trx3/win/ (H5 union 후보도 만듦)
uv run --frozen --group scope3colab python -m trlab.tx3_pick c3 <키> <lang>  # 코드만 판 → out/trx3/runs/<키>-<lang>-c3.json
set -a; . ~/.config/marcspon-openai.env; set +a
uv run --frozen --group scope3colab --with openai==3.26.0 python -m trlab.tx3_pick <d3|s3|A1|A2|A5|best> <키> <lang> [--dry-run]
uv run --frozen --group scope3colab --with openai==3.26.0 python -m trlab.page_vision <키> <lang> [--dry-run]   # H3
uv run --frozen --group scope3colab python -m trlab.tx3_report               # 표 → out/trx3/summary.{json,md}
```

| 파일 | 하는 일 |
|---|---|
| `pagemap.py` | 머리글·꼬리말·쪽 가장자리(위 8%·아래 8%) 블록의 1~3자리 번호 → `{찍힌: PDF 쪽}`, 덮음 80% 아래면 글자에서 더함, 보간·최빈 어긋남 · `window` = 직접이면 한 쪽, 아니면 ±1 |
| `order_win.py` | 차례 DP(시작 ≥ 앞 항목, 같은 쪽은 장·번호 없는 머리·같은 번호 뒤만, 목차 쪽 −0.2, 쪽 −0.001) · 번호 없는 항목 창(앞 DP 쪽 ~ 다음 번호 쪽, 13쪽 넘으면 DP 닻) · 거스름 |
| `cands.py` | `build(divider_rule=trx2|ratio|union)` — ratio = 쪽 안 최대 글자 ÷ 본문 ≥ 4.5(첫·끝 쪽 뺌). 기본 trx2 는 TRX2 그대로 |
| `page_vision.py` | 항목 하나 = 쪽 이미지 ≤ 3장(100dpi) = luna 한 호출, `page_idx`·`title` → 블록 |
| `tx3_pick.py` | A∨B 셈 · 장부·자리 상한(G1 0.05 · luna 0.6 · sol 1.0, 합 2) · d3/s3 3회 투표 · 거스름 다시 묻기 · 절제 판 A1/A2/A5 · best |
| `tx3_report.py` | 판별 표 · H0·H1·H5·H2 표 · 흔들림 · 방법별 고친 항목 · 자리별 USD |

## TRX4 — 번호 없는 목차에 책 전체 신호 (`MARC-TICK-496`)

번호 없는 목차(kb, 반쯤 본)와 번호 있는 책 여섯 판의 G1 번호를 지운 판(N-, 이름 꼬리 `-N` — 정답을 본 책이라 블라인드 아님)에
책 전체 신호를 DP 점수(bonus)로 더한다. 셈은 TRX3 `score_ab` 그대로. 산출 `esg-lab/out/trx4/`(env `TRX4_OUT`),
자리 찾기 `read4` = `out/trx4` → `out/trx3` → `out/trx2` → `out/trx1`(없으면 만들지 않고 멈춤). 규칙·상수는 kb 로 짜고
N- 전에 얼림(`out/trx4/first-run.txt`).

```
cd esg-lab/py   # 워크트리에서는 esg-lab.worktrees/<밴드>/py
uv run --frozen --group scope3colab python -m trlab.tx4_pick c3 <판>                 # 판 = kb-ko · kb-en · nb2-ko-N …
uv run --frozen --group scope3colab python -m trlab.tx4_pick c4 <판> K2+K3+K4        # 코드만 + 신호
uv run --frozen --group scope3colab python -m trlab.tx4_pick win <판>                # K1 창 덮음(TRX3 닻 · K1a · K1b)
uv run --frozen --group scope3colab python -m trlab.runhead <키> <lang>              # K3 머리글 띠 → out/trx4/runhead/
uv run --frozen --group scope3colab python -m trlab.sec_shape <판>                   # K2 꼴 → out/trx4/shape/
uv run --frozen --group scope3colab python -m trlab.thumb_vision grid <키> <lang>    # K5 격자 PNG → out/trx4/thumb/
set -a; . ~/.config/marcspon-openai.env; set +a
uv run --frozen --group scope3colab --with openai==3.26.0 python -m trlab.tx4_pick d4 <판> K1+K2+K3 [--dry-run]
uv run --frozen --group scope3colab --with openai==3.26.0 python -m trlab.thumb_vision <luna|sol> <판> [--dry-run]
uv run --frozen --group scope3colab --with openai==3.26.0 python -m trlab.tx4_pick best4 <판> [--dry-run]
uv run --frozen --group scope3colab python -m trlab.tx4_report                       # 표 → out/trx4/summary.{json,md}
```

| 파일 | 하는 일 |
|---|---|
| `order_win.py`(선택 인자) | `windows3(anchor_cut, hi_dp)` — K1a 다음 항목 DP 쪽까지 · K1b 책 끝, 닻 자르기 없음 · `item_cands/dp/plan(bonus)` |
| `sec_shape.py` | K2 — 확실 항목(fuzz ≥ 90)의 시작 블록 꼴(크기 비·위·왼·굵기·쪽 후보 수·간지) 빈도 → 후보 점수, 바로 앞 쪽 같은 글·크기면 −0.3 |
| `runhead.py` | K3 — 위·아래 8% 띠 글자(겹침 중복 제거) → 낱말(틈·스타일) → 같은 크기 붙은 줄 묶음의 소수 스타일 = 강조 · 경계 · 항목에 닻 |
| `thumb_vision.py` | K5 — 쪽 200px 격자 6×10(라벨 3×5 숫자), PNG 직접 · 책 한 호출 · 답 → 블록 · k5b(section_starts → DP) |
| `tx4_pick.py` | read4 · N- · 장부(luna 0.1 · sol 0.4 · 합 0.5) · K4 간격 · prep4 · c3/c4 · d4(luna, 새 열 header page·start-shape) · best4α/β |

**채택(2026-11-04 사용자, `MARC-ENT-577`)**: 쪽 번호 있는 목차 = 코드만(G1 sol 목차 읽기 + pagemap + 간지 비 + 차례 DP) · 쪽 번호 없는 목차 = K3 머리글 닻 + K1a 창 + luna 1회 + K5 격자 sol 1회 + 어긋남만 sol 되묻기(best4β) · 간지 = 시작 쪽 정답(A∨B). K2·K4·H3·3회 투표는 넣지 않음.
| `tx4_report.py` | 판별 표 · 방법별 고친/망친 · 머리글 유무 · K2 꼴 · 창 덮음 · K5 가독 · 자리별 USD |

## TRX5 — 용어 불일치 검출 luna span 판(세 책) · 지칭 검사기 it/its (`MARC-TICK-505`)

(1) KR 용어마다 「이 짝 EN 에서 어느 글자로 옮겨졌나」를 luna 에게 뽑게 하고(묶음 ≤ 12,000자 KO+EN, 짝·용어 풀은 TRX1
`glossary_check.inconsistency` 와 같은 규칙), 같은 용어의 `exact|variant` span 정규화 키(앞 the·끝 s 뗌)가 둘 이상이면 불일치 —
S1 그대로 · S2 품는 표현 합치기 · S3 키마다 출현 ≥ 2 · S4 sol 되묻기(같은 공식 명칭이면 남김, 판의 한 열). 지시문은 sam 으로 짜고
`out/trx5/first-run.txt` 에 얼린 뒤 kb → cos. (2) `subject.py` 에 선택 인자 `it_rule`(None|R1|R2|R3, 기본 None = TRX1 그대로)로
it/its 꼴 `pronoun` 을 더한다(허사 It 목록 `EXPLETIVE`, 블록 안 앞 문장 주어 `prev`). 산출 `esg-lab/out/trx5/`(env `TRX5_OUT`),
정답 `prework/gold-trx5/`(env `TRX5_GOLD`): 지칭 표본 25 + it 표본 25(규칙 전 얼림), 불일치 사후 라벨 11. `out/trx1~4` 는 읽기만.

```
cd esg-lab/py   # 워크트리에서는 esg-lab.worktrees/<밴드>/py
uv run --frozen --group scope3colab python -m trlab.tx5_pick sample-it                   # it 표본 문맥(seed 505) → out/trx5/subject/it25-context.json
uv run --frozen --group scope3colab python -m trlab.tx5_pick freeze                      # prework/gold-trx5/freeze.txt
uv run --frozen --group scope3colab python -m trlab.tx5_pick subject <키> [--it R1|R2|R3] # → out/trx5/subject/<키>-official-<rule>.json (+ sam 엔진 산출 셋)
uv run --frozen --group scope3colab python -m trlab.tx5_pick rescore                     # 갈래 × 표본 둘 → out/trx5/subject/rescore.json
uv run --frozen --group scope3colab python -m trlab.tx5_pick pool <키>                   # 풀 → out/trx5/pool/<키>.json
set -a; . ~/.config/marcspon-openai.env; set +a
uv run --frozen --group scope3colab --with openai==3.26.0 python -m trlab.tx5_pick span <키> [--batches n] [--dry-run]   # luna → out/trx5/span/<키>-<n>.json
uv run --frozen --group scope3colab python -m trlab.tx5_pick incons <키>                 # S1~S3 → out/trx5/incons/<키>-S{1,2,3}.json
uv run --frozen --group scope3colab --with openai==3.26.0 python -m trlab.tx5_pick judge <키> [--dry-run]               # S4 sol 책당 1호출 → <키>-S4.json
uv run --frozen --group scope3colab python -m trlab.tx5_report                           # 표 → out/trx5/summary.{json,md}
```

| 파일 | 하는 일 |
|---|---|
| `span_incons.py` | 풀(가림·≥3짝) · 묶음 · 지시문·스키마 · span 검증(`nospan`·`missing`) · 정규화 키 · S1~S3 · S4 되묻기 글·적용 |
| `tx5_pick.py` | 장부 `out/trx5/calls.jsonl`(luna 0.3 · sol 0.2 · 합 0.5, 누적+잡아 둔 몫+예상 > 상한이면 `over_cap`) · 예상(0.615 토큰/글자 → 첫 실측) · 명령 전부 |
| `subject.py`(선택 인자) | `refs/classify(it_rule, prev)` · `analyze(it_rule)` — 기본 동작·`rule_ok` 셈 그대로 |
| `tx5_report.py` | ① 불일치(Dice·S1~S4, 재현 분모 = 라벨된 진짜 합집합, 전수 아님) ② span 품질 ③ 지칭 표·재채점 ④ 자리별 USD·얼림 |

실측(2026-11-05): luna 105 묶음 0.077 USD(토큰/글자 중앙값 0.33, 출력 38.6 토큰/항목), sol 3 호출 0.027 USD. 불일치 정밀(라벨된 걸림 분모)
Dice 4/56 → S1 4/44 → S2 3/29 → S3 2/15 → S4 2/7, 재현(분모 4) S1 4 · S2 3 · S4 2 — S2 품는 합치기가 「Board ⊂ Board of Directors」·
「이름 ⊂ 이름 Inc.」 꼴 진짜를 지운다. 지칭 표본 25: 23 → R1 25 · R2 23 · R3 23, it 표본 25: 23 → R1 22 · R2 24 · R3 21(보고 `band-reports/TRX5.md`).

## TRX6 — PDF 문단 다시 잇기 측정 (`sid:MARC-TICK-506`, concerns `sid:MARC-ISSUE-566`)

줄마다 갈라진 파서 블록(layout 2 으뜸)을 글자 속성 규칙으로 같은 단·같은 문단으로 이으면 「문단 = 블록」에 얼마나 가까워지나.
정답 `prework/gold-trx6/para/<키>-ko.json`(19쪽 본문 문단 184 — cos 48 개발 셋, kb 47·sam 89 보류 셋, `freeze.txt` 로 얼림),
규칙은 cos 로 정해 `out/trx6/first-run.txt` 에 얼린 뒤 kb·sam.

- `para_join.py` 글자(pypdfium2 loose box) → 줄(y 묶음 · x 틈 2.0 · 위첨자 뗌) → 단 → 줄 짝(`neighbors`) → 규칙 R1 같은 단 · R2 줄 간격(절대 2.5 + 쪽 최빈 pitch × 1.12) · R3 같은 꼴(크기 12%·굵기·색, run-in 예외) · R4 끝 부호+들여쓰기/불릿 · R5 짧은 끝줄+끝 부호 · R6 제목/표/설명/탐색 띠 혼자 · R7 단·쪽 넘김 표시만(R7j 잇기) · G 파서 상수 흉내.
- `tx6_pick.py` 자리(`read6` → `read4`) · `stats`·`lines`(줄 자료 + 100dpi 렌더) · 정답 읽기 · 셈(잘못 > 덜 > 잘, 갈래 여섯, 줄 짝 정밀/재현, 문장 온전율, 단·쪽 넘김 짝) · 판 `run`/`runall`(L1·L2·G·R·R−Rk·R2r·R5(w)·R7j·P 격자) · 장부(상한 0.1, 실호출 0).
- `tx6_report.py` → `out/trx6/summary.{md,json}`(판별 표·갈래 표·규칙별·P 격자·G 표·부재·남은 틀림·대조 표본 셋).
- 결과: 잘 이음 L2 → R cos 27.1→83.3 % · kb 48.9→66.0 · sam 40.4→79.8(kb·sam 남은 것은 불릿 항목 잘못 이음). 추천: 파서는 틈 상수 0.65→0.8 만, 문단 잇기는 모듈 공통 읽기 함수에서 R1~R6 — 보고 `band-reports/TRX6.md`.

## TRX7 — 영문번역 2차 검수 단계 실험: 검수 판 넷 · 사람 이름 표기 판 셋 (`sid:MARC-TICK-510`, concerns `sid:MARC-ISSUE-382`·`sid:MARC-ISSUE-383`)

1차 엔진(esg-app colab_158)의 검수(review)·윤문 검사(editorial-qa) 프롬프트를 글자 그대로 베껴 **블록 단위**로 돌리고, 심은 오역(갈래 일곱 × 3~4 = 24)·
깨끗한 공식판 블록 14·TRX1 엔진 산출의 실제 오역 12 에서 재현·헛걸림·블록당 USD 를 잰다. 판: V0r(검수 그대로 + 거름 셋) · V0e(윤문 검사 + `must_apply`) ·
V1(V0e 답에서 이슈 ≥ 1, 호출 0) · V2(블록 체크리스트 — 개발 셋 sam 6 으로 짜고 `out/trx7/first-run.txt` 에 얼림) · V3(V2 를 sol 로, 부분집합 12).
이름: 지어낸 한국 이름 14 를 문장 12 에 넣어 N0(지시 없음) · N1(로마자 규칙 한 줄) · N2(이름 표) × 3회 × 4.1-mini·luna 로 3회 일관율·규칙·표 지킴.
정답 `prework/gold-trx7/`(env `TRX7_GOLD`)에는 **글을 넣지 않는다**(실제 회사·사람 이름 0) — `units.json` 은 짝 id·sha16·심은 before/after·용어집 index,
`real.json` 은 틀린 자리의 글자 offset. 글은 `review_probe.materialize` 가 `out/trx1` parse 와 `out/trpdf/res` 에서 다시 읽어 sha 를 대조한다.
산출 `esg-lab/out/trx7/`(env `TRX7_OUT`). `out/trx1~5` 는 읽기만, `out/trx6` 는 읽지 않는다.

```
cd esg-lab/py   # 워크트리에서는 esg-lab.worktrees/<밴드>/py
uv run --frozen --group scope3colab python -m trlab.tx7_pick draw           # seed 510 후보(자리마다 셋) → out/trx7/draw.json
uv run --frozen --group scope3colab python -m trlab.tx7_pick build-units    # + out/trx7/plants.json(손 심기)·real-spec.json → prework/gold-trx7/{units,real}.json
uv run --frozen --group scope3colab python -m trlab.tx7_pick freeze         # prework/gold-trx7/freeze.txt (지시문 전)
set -a; . ~/.config/marcspon-openai.env; set +a
uv run --frozen --group scope3colab --with openai==3.26.0 python -m trlab.tx7_pick names <N0|N1|N2> <모델> [--reps 3] [--dry-run]
uv run --frozen --group scope3colab --with openai==3.26.0 python -m trlab.tx7_pick review <V0r|V0e|V2|V3> <모델> [--split test|dev] [--dry-run] [--ids a,b]
uv run --frozen --group scope3colab python -m trlab.tx7_pick first-run      # 지시문·모듈 sha → out/trx7/first-run.txt (시험 셋 전)
uv run --frozen --group scope3colab python -m trlab.tx7_pick score          # → out/trx7/score/<판>-<모델>.json (V1 은 V0e 답에서)
uv run --frozen --group scope3colab python -m trlab.tx7_report              # → out/trx7/summary.{json,md}
```

| 파일 | 하는 일 |
|---|---|
| `review_probe.py` | 베낀 프롬프트 넷·`must_apply`·`ALLOWED_ISSUE_TYPES`(esg-app 줄 sha 같음) · 단위 뽑기(갈래별 조건)·심기·materialize · 답 꼴(json_schema) · V2 지시문 · 거름 셋 · 채점(span 겹침·누락·제목 정규식) |
| `names_probe.py` | 지시문 N0~N2 · 답 `{translation, person_names}` · 표기 뽑기(`nospan`) · 일관율·규칙·표 셈 |
| `tx7_pick.py` | 장부 `out/trx7/calls.jsonl`(모델별 상한 4.1-mini 0.25 · luna 0.1 · sol 0.15 · 합 0.5, 누적+잡아 둔 몫+예상 > 상한이면 `over_cap`) · 예상(0.40 토큰/글자 → 첫 실측) · 줄이는 차례 `shrink` · 명령 전부 |
| `tx7_report.py` | ① 검수 판 × 모델(심은·갈래별·실제·헛걸림·짚고도 안 고침·USD/블록) ② 이름 판 × 모델 ③ 자리별 USD·토큰/글자·얼림 |
| `semgraph/llm.py` | 더한 줄 3: `PRICES["gpt-4.1-mini"]`(models.json 2026-10-16) · `NO_REASONING` · 그 모델이면 `reasoning` 을 빼는 한 줄(다른 모델 그대로) |

실측(2026-11-05, 446 호출 0.155 USD): 심은 재현 V0r 18/24(제목 중복 0/3) · V0e = V1 18/24(점수 96 이상이 없어 `must_apply` 전부 참, 헛걸림 14/14) ·
V2 4.1-mini 22/24(헛걸림 7/14) · V2 luna 23/24(6/14) · V3 sol 8/8(0/2). 실제 오역 V0r 7/12 · V2 11~12/12. 블록당 USD 4.1-mini 0.0008(V0r·V0e)·0.0004(V2), luna 0.0002, sol 0.0019.
이름 3회 일관율 N0 7/15 · N1 6~8/15 · N2 15/15(두 모델), N1 규칙 지킴 76~80%. 보고 `band-reports/TRX7.md`.

## TRX8 — 목차↔본문 잇기 블라인드: 처음 보는 책 4권(nb3~nb6)에 결정 577 의 두 길 (`sid:MARC-TICK-512`, concerns `sid:MARC-ISSUE-542`)

규칙·문턱·지시문은 BASE 그대로(`out/trx8/first-run.txt` 모듈 sha 14). `tx8_pick` 은 자리·장부·자리 상한만 잡고 TRX2~4 함수를 차례대로 부르는 겉옷 —
허용 덮어쓰기 넷(`tx4_pick.OUT4 = OUT8` · `TOTAL_CAP4 = 0.6` · `SITE_PREFIX4["sol"] += ("TRX8/",)` · `BEST_BASE = ("K1","K3")`)만.
정답 `prework/gold-trx8/`(얼림 `freeze.txt`, 키 nb3~nb6, 예비 nb7 은 `books.json` 에만), 산출 `esg-lab/out/trx8/`(env `TRX8_OUT`, 장부 `calls.jsonl` 하나).
남은 책에 쪽 번호 없는 목차가 없어 길 (2)는 같은 4권의 G1 번호를 가린 판(`<키>-ko-N`, 번호 가린 블라인드)으로 쟀다.

```
cd esg-lab/py   # 워크트리에서는 esg-lab.worktrees/<밴드>/py — 셸에서 export (import 전에 모듈 상수가 읽는다)
export TRX8_OUT=…/esg-lab/out/trx8; export TRX2_OUT=$TRX8_OUT TRX3_OUT=$TRX8_OUT TRX4_OUT=$TRX8_OUT TRX2_GOLD=…/prework/gold-trx8
uv run --frozen --group scope3colab python -m trlab.tx8_pick sha | first-run | ledger
uv run --frozen --group scope3colab python -m trlab.glyphs <키> ko ; … trlab.cands <키> ko          # G0
set -a; . ~/.config/marcspon-openai.env; set +a
uv run --frozen --group scope3colab --with openai==3.26.0 python -m trlab.tx8_pick g1 all [--dry-run]   # G1 sol (자리 0.1 은 겉옷이 잼)
uv run --frozen --group scope3colab python -m trlab.pagemap <키> ko ; … trlab.order_win <키> ko       # H1·H2(union + win)
uv run --frozen --group scope3colab python -m trlab.tx8_pick c3 all                                   # 길 1 c3 · N- c3 · N- c4:K3
uv run --frozen --group scope3colab --with openai==3.26.0 python -m trlab.tx8_pick path2 all           # 길 2: d4 K1+K3 → K5 sol(+k5b) → best4β
uv run --frozen --group scope3colab --with openai==3.26.0 python -m trlab.tx8_pick ref all             # 참고 d4 K1+K2+K3
uv run --frozen --group scope3colab python -m trlab.tx8_pick cand1 all ; … check2 all                 # 후보 ① · 후보 ② 자기 검사
uv run --frozen --group scope3colab --with openai==3.26.0 python -m trlab.tx8_pick cand2 all ; … cand3 all   # 후보 ②(K3′ c4·d4·β′) · ③(sol30·β30)
uv run --frozen --group scope3colab python -m trlab.tx8_report                                        # 표 → out/trx8/summary.{json,md}
```
`best4` 는 반드시 `tx8_pick path2` 로(모듈 명령으로 직접 부르면 `BEST_BASE` 가 K1+K2+K3 라 「바닥 없음」).

| 파일 | 하는 일 |
|---|---|
| `tx8_pick.py` | 자리·장부(G1 = tag `TRX2/G1/`, luna = model, 그 밖 sol)·자리 상한 `room` · `g1`·`prep`·`c3`·`path2`·`ref` · 후보 ① `cand1_merge`(β 되돌린 항목만 α 블록, 다시 차례 검사) · ② `band_pages·common_words(≥ 90% 쪽 같은 띠·줄)·scan8·prep8·c4p·d8·best8·check2` · ③ `sheets30·k5sol30·cand3` |
| `tx8_report.py` | 판별 표(c3 · N- 열 · 후보 열) · G1 · 고친/망친 · 틀린 꼴 갈래 · 머리글 유무·닻 · K5 가독 60 대 30 · 자리별 USD — 항목은 n 번호만 |

## TRX10 — 블록 체크리스트 검수(V2)를 책 한 권 전량에 · 짧은 문장 누락 규칙 · 블록 묶음 호출 (`sid:MARC-TICK-514`, concerns `sid:MARC-ISSUE-382`)

TRX7 의 V2 체크리스트(지시문·답 꼴·채점 그대로 — `review_probe` import 만, `V2_INSTR` sha16 `71258cda68336c91`)를 sam 공식판 짝 **전량 1,391**(제목·표·mixed 포함, 꼴은 `kind`)에
luna 로 돌리고 4.1-mini 는 층화 300(kind × EN 길이 띠, seed 514)으로 — 걸림율·갈래·쪽당 걸림·지연·블록당 USD 와 걸림 표본 60 의 라벨 정밀(`real | context | noise`).
누락 규칙: KR 문장 수(`omit_rule.kr_sentences` — `(?<=[.!?])\s+` + 목록 번호 잡음 상수) 대 EN 문장 수(`subject.sentences`), `n_en < n_ko` 면 걸림(문단·mixed 만),
걸린 블록만 luna 되묻기(`REASK_INSTR` 527자, 번호 붙인 KR 문장 → `{missing:[int], reason}`). 묶음 호출: TRX7 38 단위를 호출 글자 ≤ 8,000 으로 묶어(seed 514, 7 묶음)
같은 지시문 + `[Block k]` 입력 + `BUNDLE_SCHEMA`(V2_SCHEMA + `block` 칸)로 — 낱개(`out/trx7/score/V2-*.json` 읽기)와 재현·헛걸림·블록당 USD·지연.
산출 `esg-lab/out/trx10/`(env `TRX10_OUT`), 라벨 `prework/gold-trx10/`(env `TRX10_GOLD` — `flags.json`·`omit.json`·`freeze.txt`, 글 0). `out/trx1~7` 읽기만, `out/trx8`·`trx9` 안 읽음.
얼림 차례: 규칙·지시문·뽑기 → `out/trx10/first-run.txt` → 실호출 → 라벨 → `gold-trx10/freeze.txt` → 표.

```
cd esg-lab/py   # 워크트리에서는 esg-lab.worktrees/<밴드>/py
uv run --frozen --group scope3colab python -m trlab.tx10_pick units        # sam 짝 1,391 → out/trx10/units.json (글 없음)
uv run --frozen --group scope3colab python -m trlab.tx10_pick first-run    # 모듈·지시문·규칙 sha → out/trx10/first-run.txt (실호출 전)
set -a; . ~/.config/marcspon-openai.env; set +a
uv run --frozen --group scope3colab --with openai==3.26.0 python -m trlab.tx10_pick bundle <모델> [--dry-run]
uv run --frozen --group scope3colab --with openai==3.26.0 python -m trlab.tx10_pick full gpt-6-luna --n 100   # 선실측 → plan 이 다시 셈
uv run --frozen --group scope3colab --with openai==3.26.0 python -m trlab.tx10_pick full gpt-6-luna          # 전량(plan 이 줄인 만큼)
uv run --frozen --group scope3colab --with openai==3.26.0 python -m trlab.tx10_pick full gpt-4.1-mini --strata
uv run --frozen --group scope3colab --with openai==3.26.0 python -m trlab.tx10_pick reask                    # 규칙 걸린 블록만(TRX7 38 + 전량)
uv run --frozen --group scope3colab python -m trlab.tx10_pick pick-labels  # seed 514 표본 → out/trx10/labels/ (라벨은 손으로 gold-trx10/)
uv run --frozen --group scope3colab python -m trlab.tx10_pick freeze       # gold-trx10/freeze.txt
uv run --frozen --group scope3colab python -m trlab.tx10_pick score        # → out/trx10/score/
uv run --frozen --group scope3colab python -m trlab.tx10_report            # → out/trx10/summary.{json,md}
```

| 파일 | 하는 일 |
|---|---|
| `fullrev.py` | 전량 단위(글 없음·sha 대조 `Texts`) · 층화 `stratify`(비례·최소 5) · 묶음 `bundle_plan`·`BUNDLE_SCHEMA`·`split_bundle_answer`(oob) · 라벨 표본 `pick_flags`(갈래 × 꼴, 같이 건 짝 먼저)·`pick_omit`(EN 띠) · 분포 `dist` |
| `omit_rule.py` | KR 문장 나누기(`KR_SPLIT`·`KR_NOISE`) · `n_en < n_ko` 규칙(`OMIT_MIN_GAP` 1, 문단·mixed) · 되묻기 지시문·입력(번호 1:1)·답 읽기 · 심은 번호 채점(`planted_ko_no`·`hit_of`) · 라벨 갈래 |
| `tx10_pick.py` | 장부 `out/trx10/calls.jsonl`(TRX7 꼴 + `elapsed_ms`·`site`·`unit_ids`) · 자리 셋(태그 앞머리 luna-full 0.45 · mini 0.25 · luna-reask 0.1, 합 0.8) · 예상(TRX7 상수 → 100 짝 실측 중앙값, `plan` 이 쪽 차례로 줄임) · 얼림 전 실호출 막기 · 명령 전부 |
| `tx10_report.py` | ① 전량 ② 누락 ③ 묶음 ④ 자리별 USD·「100쪽 책 = USD · 분」·얼림·100 짝 뒤 예상 대 실측 |

실측(2026-11-05, 1,795 호출 0.259 USD, 순차): luna 전량 걸림 529/1,391(문단 440/1,117 · 표 54/95), 라벨 정밀 luna 블록 real 20/40(이슈 real 20/48 — 그중 짝 어긋남·그림 라벨 14) ·
4.1-mini 300 걸림 189/300(real 5/20, noise 11/29), 같은 300 luna 121. 누락 규칙 심은 omit 4/4 span · 헛걸림 4 → 되묻기 뒤 2/34 · 전량 82/1,145 ·
라벨 missing 16(짝 어긋남 15)/merged 5/split_noise 19, 되묻기 일치 40/40. 묶음(7 묶음, 5~7 블록) luna 24/24 · 헛걸림 4/14 · 블록당 0.000086 USD(낱개 0.000163),
4.1-mini 묶음 12/24. 100쪽 책 = luna 낱개 0.17 USD · 76분(순차) · luna 묶음 0.14 USD · 37분 · 4.1-mini 낱개 0.49 USD · 49분. 보고 `band-reports/TRX10.md`.
