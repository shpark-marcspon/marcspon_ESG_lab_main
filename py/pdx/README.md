# py/pdx — PDF 추출 방식 비교 조사 (PDX1 `MARC-TICK-339` · PDX2 `MARC-TICK-476`)

조사용 스크립트다. 제품 코드가 아니고 esg-lab 의 의존(`py/pyproject.toml`)에 아무것도 넣지 않는다.
공개 보고서 PDF·정답·쪽 이미지·모델 응답은 저장소 밖이다(경로는 인자로). 원시 산출은 메인 체크아웃의 `esg-lab/out/pdx1/`.

| 파일 | 하는 일 | 도는 자리 |
|---|---|---|
| `order_score.py` | 읽는 순서 채점 (정답 `prework/gold-order/*.json` × 후보의 쪽 글 흐름) | 표준 라이브러리 |
| `docparse_streams.py` | docparse `document.json` → 쪽 글 흐름 | 표준 라이브러리 |
| `docling_run.py` | Docling 으로 쪽 분해 → 표 격자·글 흐름 | `uv run --no-project --python 3.12 --with docling==2.138.0 python -I` |
| `table_score.py` | 바깥 표 격자를 docparse `score` 와 같은 정의로 채점 | esg-app `services/parser` 환경 (`PYTHONPATH=.`) |
| `vision_run.py` | ③ 혼합·④ 비전만 OpenAI 호출(`--dry-run`), 호출 장부 `calls.jsonl` | `--with openai==3.26.0 --with pypdfium2==5.14.0 --with pillow==12.3.0` |
| `cell_check.py` | 모델이 낸 칸 글이 그 쪽 글자 층에 있는지 | 표준 라이브러리 |
| `make_gold.py` | 처음 보는 보고서의 정답 표 짓기(사람이 열 경계, 글자 층을 잘라 격자) | `--with pypdfium2==5.14.0` |
| `figure_run.py` | 그림 영역만 잘라 보내 그림 속 글 뽑기 — 정답 짓기·부르기·채점 | 위와 같음(+openai) |
| `guard.py` | ③ 혼합 표에 검사(칸 글·위첨자·숫자·열 수·빠짐·숫자 열)를 붙이고 걸린 표만 docparse 로 되돌림, `--second` 로 luna→sol. PDX2 G0 규칙이 기본(`--rules e` 는 경계 E 판), `--layout 1\|2` | 표준 라이브러리 |
| `select_pages.py` | ③ 혼합에 보낼 쪽 고르기(숫자 표 ∪ 위첨자·각주 조각). PDX2 G0 에서 쪽 번호 블록을 조각에서 뺌(`--rules e` 는 경계 E 판) | 표준 라이브러리 |
| `docparse_tables.py` | docparse `document.json`(판 1·2) → 채점 꼴 표 격자 (PDX2) | 표준 라이브러리 |
| `by_company.py` | 여러 채점 JSON 을 후보 × 지표 표와 칸 괘선 표 회사별 표로 (PDX2) | 표준 라이브러리 |
| `run_metrics.py` | 채점 JSON 에서 칸 괘선 표만·자리만 틀린 칸·헛되돌림·숫자 바뀐 칸 (PDX2) | 표준 라이브러리 |
| `figure_census.py` | 쪽마다 래스터 이미지 조각의 자리·크기·겹친 글자 수 세기와 표본 렌더 | `--with pypdfium2==5.14.0` (+pillow) |
| `to_page_tags.py` | docparse `document.json` → 번역 모듈의 `[PAGE n]` 구조 글 | 표준 라이브러리 |

정답 묶음: `prework/gold`(15표, 제자리) · `gold-unseen`(9, PDX1 D — layout 2 가 보며 고친 표) · `gold-unseen2`(9, PDX1 F) · `gold-unseen3`(13, PDX2 G1 — 칸 괘선 8, spec sha256 앞 16자 `72e1f08d3cea7d90`) · `gold-unseen4`(6, PDX2 G3 — 효성화학 97·98쪽 칸 괘선, spec `1be376bdd79f5478`. 판 2 가 같은 디자인을 보고 고친 보고서라 docparse 값은 판 2 에 유리).
재현 명령과 숫자는 보고 `band-reports/PDX1.md`·`band-reports/PDX2.md` 와 리포트 `MARC-RPT-24` 에 있다.
docparse 판(`--layout 1|2`)은 esg-app `services/parser` 에서 `python -m docparse parse … --layout N` 으로 뽑고, 위 스크립트의 `--layout` 은 받은 `document.json` 의 판이 맞는지만 본다(기본 1, 판 1 산출은 그대로).
