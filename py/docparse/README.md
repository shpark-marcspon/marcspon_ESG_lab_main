# docparse

PDF를 섹션·블록(문단, 제목, 표)·표 격자의 JSON으로 분해한다. LLM·OCR·DB를 쓰지 않고 상태가 없다.
출력 형식은 `schemas/document.schema.json` 이다.

```
cd py
uv run python -m docparse parse <pdf> --out <dir> [--pages 60-70] [--strategy auto|lines|text] [--render --dpi 150] [--places] [--dedupe-overdrawn]
uv run python -m docparse score --gold ../data/gold [--strategy auto|lines|text] [--json out.json]
uv run python -m docparse render <pdf> --out <dir> [--pages 64,66] [--dpi 200]
uv run pytest -q
```

원본 PDF는 저장소에 없다. `score` 와 회귀 테스트는 `../prework/reports/` 를 읽는다
(`DOCPARSE_REPORTS_DIR` 로 바꿀 수 있다).

## 표 추출 전략

| method | 내용 |
|---|---|
| `lines` | 괘선 기반. 가로 괘선 토막의 양 끝 x 를 열 경계, y 를 행 경계로 쓴다. 세로선이 없어도 된다. 토막이 없는 변은 병합으로 본다 |
| `text` | 글자 정렬 기반. 괘선을 쓰지 않고 글자 조각의 위치만으로 행·열을 추정한다. 항상 `low_confidence` |
| `none` | 표 후보였지만 격자를 세우지 못함 (`status=failed`, `cells=[]`) |

`auto` 는 괘선 기반으로 먼저 찾고, 남은 글자에 글자 정렬 기반을 쓴다. 표마다 `method` 와 `status` 를 남긴다.

- 글자는 텍스트 계층의 문자열 그대로다. 숫자를 고치거나 추측하지 않는다.
- 병합 셀의 값은 병합 범위의 첫 행·첫 열에만 둔다.
- 숫자 셀 뒤의 각주 위첨자(`1,304⁵⁾`)는 값에서 떼어 `footnote_marks` 에 둔다.
- 2pt 미만의 보이지 않는 글자는 버리고 쪽의 `notes` 에 개수를 적는다.
- 텍스트 계층이 없는 쪽은 `text_layer: none` 으로 표시만 한다.

## 일치율 지표 (`compare.py`)

분모는 모두 정답의 비어 있지 않은 셀 수다. 정규화: NFKC, 공백·천 단위 쉼표 제거, 대시 통일, 글자 뒤 각주 번호 제거.

- 관대(`recall`): 짝지은 행 안에서 비어 있지 않은 셀의 순서열이 맞으면 맞음. 열 위치는 보지 않는다.
- 엄격(`recall_strict`): 관대 + 추출 행의 셀 수가 정답 행보다 많으면 그 행은 0점.
- 위치(`recall_position`): 같은 열 번호의 셀이 같아야 맞음. 사전 측정에는 없던 지표.

회귀 하한은 `data/gold/floors.json`, 정답 표를 고친 기록은 `data/gold/CORRECTIONS.md`.

## 도표 라벨 (`places.py`, `charts.py`, `tagpages.py`)

문서 모델(`document.json`)은 고치지 않는다. 같은 PDF 를 다시 읽어 **새 산출** `chart-labels.json`(`schemas/chart-labels.schema.json`)을 낸다.

```
uv run python -m docparse charts <pdf> --document <document.json> --out <dir>     # chart-labels.json, places.json
uv run python -m docparse tagpages <pdf> --tags <tags.json> --out <dir> [--dpi 110]  # 숫자 자리를 번호표로 덮은 쪽 PNG
```

- `places.py`: 문서 모델의 글 블록·표 칸의 글자를 PDF 의 낱말 좌표에 잇는다(그릇 ID 와 글자 자리). 같은 글자의 조각이 여럿이면 격자의 띠 차례(열부터, 틀리면 행부터)로 가른다. 잇지 못한 그릇은 `unlinked` 에 남긴다.
- `charts.py`: 벡터 그림 요소(색 사각형, 굵은 선, 작은 원, 범례 표식)로 도표를 찾고, 도표 속 숫자 낱말을 값·눈금·범주 축의 글자로 가른 뒤 값마다 라벨을 붙인다.
  - (가) 좌표 `coord`: 값 글자의 가운데를 범주 축의 글자에 맞춘다. 가장 가까운 범주가 다음 범주보다 뚜렷이 가깝지 않으면 붙이지 않는다.
  - (나) 그림 요소 `elem`: 값 글자를 가까운 막대의 끝·꺾은선의 점에 하나씩 걸고(가는 이음선이 있으면 그 끝점에서), 그 요소의 자리로 범주를, 색과 범례로 계열을 붙인다.
  - 합친 것 `label`: 범주는 둘이 같거나 한쪽만 붙였을 때만 싣는다(다르면 `methods_disagree`). 계열은 (나)의 것.
- 그림으로 박힌 도표는 대상이 아니다. OCR·LLM 을 쓰지 않는다. 숫자의 값은 읽지 않는다.
- 문턱값(pt)은 `charts.py` 머리의 상수다. KB금융 두 문서의 조판을 보고 정한 추정이고 다른 문서로 재지 않았다.

## 도표 라벨과 번호표 그림 (밴드 W)

문서 모델(`document.json`)은 그대로 두고 새 산출 파일을 낸다. LLM·OCR 을 쓰지 않는다.

- `python -m docparse charts <pdf> --document <document.json> --out <디렉터리>`: 벡터 그림 요소(막대, 꺾은선, 표식, 범례)와 글자 좌표로 도표를 찾고, 도표 속 숫자 낱말마다 갈래(값·눈금·범주 축)와 범주·계열을 붙여 `chart-labels.json` 을 쓴다(`schemas/chart-labels.schema.json`). 문서 모델의 글자를 PDF 낱말 좌표에 잇는 일은 `places.py`. `--no-grid-rule` 은 괘선 표의 칸 배경을 막대에서 빼는 규칙을 끈다(그 규칙을 더하기 전의 성적을 다시 낼 때만).
- `python -m docparse tagpages <pdf> --tags <[{page, n, bbox}]> --out <디렉터리> --dpi 200`: 그 자리의 글자를 지우고 번호표를 그린 쪽 PNG 를 쓴다. 원본 PDF 는 고치지 않는다.
- 그림으로 박힌 도표는 대상이 아니다. 문턱값은 KB금융 2025 두 문서의 조판을 보고 정한 것이다(추정).

## 글자 자리: 표의 칸과 숫자 자리의 좌표 (`--places`, 밴드 C1, MARC-TICK-35)

문서 모델에는 블록과 표 전체의 상자만 있다. `parse --places` 는 `document.json` 곁에 **`char-places.json`**(`schemas/char-places.schema.json`)을 하나 더 쓴다. `document.json` 은 이 옵션을 켜도 꺼도 같은 글자다(곁 파일이라 문서 모델의 스키마도 그대로다).

```
uv run python -m docparse parse <pdf> --out <dir> --places        # document.json + char-places.json
```

꼴: 그릇마다 하나. 그릇은 글 블록(`p3-b2`, 문서 모델의 블록 ID)이거나 글이 있는 표의 칸(`p3-t1:r2c4`, 행·열은 `cells` 의 번호 그대로)이다.

```json
{"id": "p3-t1:r2c4", "kind": "cell", "page": 3, "bbox": [412.3, 201.0, 436.9, 210.6],
 "runs": [{"start": 0, "y0": 201.0, "y1": 210.6, "x0": [412.3, 417.2, 419.7, …], "x1": [417.2, 419.7, 424.6, …]}]}
```

- `runs` 는 줄 토막이다. `start` 는 그 토막의 첫 글자가 그릇의 글(블록의 `text`, 칸의 글)에서 몇 번째 자리인가, `x0[k]`·`x1[k]` 는 `start + k` 자리 글자의 왼쪽·오른쪽 끝, `y0`·`y1` 은 토막의 위·아래다. 좌표는 문서 모델과 같다(PDF pt, 왼쪽 위 원점).
- **자리의 단위는 UTF-16 자리다**(JavaScript 문자열의 자리). 보충 평면 글자는 두 자리를 차지하고 두 자리에 같은 상자가 실린다. Python 에서 쓸 때는 `len(text[:i].encode("utf-16-le")) // 2` 로 맞춘다.
- 글의 자리 가운데 어느 토막에도 들지 않는 자리는 분해가 조각을 이을 때 끼운 빈칸이다(좌표 없음).
- `bbox` 는 그릇의 글자 전체를 감싼 상자다. **칸의 상자는 칸의 경계가 아니라 칸 안의 글자 범위다.** 까닭: 글 기반으로 선 표에는 칸의 경계가 없고(열 경계만 추정한다), 괘선 표도 병합과 빈 행·열 정리를 거치면 경계가 칸 번호와 1:1 이 아니다. 글자 범위는 두 방법에서 똑같이 얻는다.
- 글과 글자 상자를 맞추지 못한 그릇은 `unlinked` 에 까닭과 함께 남는다(지어내지 않는다). 네 국문 보고서(KB금융 보고서·데이터북 2025, 삼성전자 2026, 코스맥스 2025)에서는 0/31,435 였다.

**숫자 자리의 상자는 읽는 쪽이 셈한다.** 여기에는 숫자를 알아보는 규칙이 없다(숫자 뽑기는 `ts/reconcile` 에 하나뿐이다). 읽는 쪽이 「이 그릇의 글에서 몇 번째 글자부터 몇 번째 글자까지」를 주면 그 범위의 글자 상자를 줄마다 묶는다: `charplaces.span_boxes(그릇, start, end)`, TypeScript 는 `ts/reconcile/src/domain/places.ts`. 같은 줄의 토막은 한 상자로 묶고, 범위가 두 줄에 걸치면 상자가 둘이다. **같은 줄의 기준**: 차례로 이웃한 두 토막의 세로 범위가 겹친 높이가 낮은 쪽 토막 높이의 절반을 넘으면 한 줄이고, 상자는 둘의 합집합이다(`y0`·`y1` 이 같은 토막끼리만 묶으면 안 된다. 한 줄 안에서 글자 크기가 다른 조각은 `y0`·`y1` 이 다르다).

어떻게 얻는가: 분해는 원래 글자마다의 상자를 읽을 수 있었지만 조각(`Chunk`)을 만들며 버렸고, 블록과 칸을 세울 때는 어느 조각이 들어갔는지를 버렸다. `--places` 일 때만 글자별 상자를 읽어(`rawdict`) 조각에 쥐여 두고(`Chunk.glyphs`), 블록과 칸이 제 조각을 차례대로 기억한다(`Table.cell_chunks`, 블록의 `chunks`). 묶는 규칙, 격자를 세우는 규칙은 그대로다. 밴드 W 의 `places.py`(낱말 단위, 분해가 끝난 뒤 글자로 다시 맞춤)와는 따로다. 그쪽은 도표 라벨이 쓰고 있어 그대로 두었다.

측정 도구(값 후보의 상자가 그 숫자를 덮는가):

```
(cd ../ts && pnpm -s -F reconcile exec tsx src/compare/places-cli.ts fact-places --doc <document.json> --places <char-places.json> --out <dir>)
uv run python -m docparse placecheck  <pdf> --fact-places <dir>/fact-places.json --out <dir>              # 상자 안의 글자를 PDF 에서 다시 읽어 원문과 견준다
uv run python -m docparse placesample <pdf> --fact-places <dir>/fact-places.json --out <dir> --seed <n>   # 눈으로 볼 표본 그림
```

못 하는 것:

- 글자 층이 없는 쪽(`text_layer: none`), 그림으로 박힌 표·도표: 글자가 없으니 그릇도 좌표도 없다. OCR 을 하지 않는다.
- 누운 글자, 2pt 미만의 글자: 분해가 버리므로 문서 모델에도 글자 자리에도 없다.
- 격자를 세우지 못한 표(`status: failed`): 칸이 없다.
- 줄 끝에서 끊긴 숫자: 분해가 줄을 이을 때 빈칸을 끼우므로 글에서 `1, 234` 가 되고, 숫자 뽑기는 두 덩어리로 본다. 덩어리마다 상자는 나오지만 한 숫자로 묶이지 않는다(네 문서에서 그런 곳은 찾지 못했다).
- 같은 글자가 같은 자리에 두 번 그려진 곳(코스맥스의 쪽 번호)은 분해가 두 번 뽑고, 두 자리의 상자가 같다.
- 상자의 위·아래는 글꼴의 줄 높이다(글자의 잉크보다 조금 넉넉하다). 좌표는 소수 한 자리로 반올림한다.
- 크기는 `document.json` 의 2.9~3.6배다(0.8~1.8MB → 2.3~6.1MB). 분해 시간은 문서 하나에 1.1~4.8초이던 것이 2.1~7.4초가 된다(네 문서, 세 번씩, 2026-10-05 이 기계).

## 겹쳐 그려진 글자를 한 번만 뽑기 (`--dedupe-overdrawn`, 밴드 CT1, MARC-TICK-36)

PDF 에 같은 글자가 같은 자리에 두 번 그려져 있으면 분해는 두 번 뽑는다(코스맥스 2025 의 쪽 번호가 `65 65`, 쪽 머리의 반복 글이 블록 둘). `parse --dedupe-overdrawn` 은 그런 것을 한 번만 뽑는다. **기본은 끔이고, 끄면 산출이 글자까지 그대로다.**

- 같은 자리의 기준(`layout.overdrawn_spans`): 같은 글자가, 같은 크기(2% 안)로, 가로·세로 어긋남이 글자 크기의 0.1배 안에서 앞서 그려져 있다. 굵게 보이려고 살짝 어긋나게 덧그린 글자(어긋남이 크기의 몇 %)는 들고, 바로 옆 글자(가장 좁은 글자의 폭이 크기의 약 0.2배)는 들지 않는다. 글꼴 이름과 색은 보지 않는다.
- **span 을 통째로만 버린다.** 보이는 글자 전부가 저마다 앞서 남긴 글자와 겹칠 때만 그 span 을 버리고, 일부만 겹치면 그대로 둔다(글자를 솎으면 남은 글이 깨진다). 먼저 나온 것이 남는다.
- 버린 span 의 수는 그 쪽의 `notes` 에 `overdrawn_spans_dropped:<수>` 로 남는다. `--places` 와 같이 쓰면 글자 자리도 남은 한 번만 가리킨다.
- 네 국문 보고서에서 버린 것(2026-10-05): KB금융 보고서 12 span(4쪽), 데이터북 0, 삼성전자 11(3쪽), 코스맥스 1,457(60쪽). 버린 글자마다 같은 글자가 같은 자리에 남아 있다(통째로 사라진 글자 0).
- **부작용이 있다.** 글자를 빼면 블록과 표가 다시 서므로 표의 꼴이 달라질 수 있다. 코스맥스에서는 쪽 번호 행이 사라지며 글자 정렬 기반 표 셋(45·65·97쪽의 요약 띠)의 머리글 열 추정이 바뀌어, 읽는 쪽(`ts/reconcile`)이 그 칸의 숫자 15개를 머리글로 보고 값 후보에서 뺀다(글자는 문서 모델에 그대로 있다). 표를 세우는 규칙은 고치지 않았다.
- 못 하는 것: 글꼴이 다른 덧그림처럼 글자 폭이 달라 뒤로 갈수록 어긋나는 것(코스맥스 4·11쪽 등)은 일부 글자만 겹쳐 그대로 남는다.

## 도표 후보 구역 (`chartcands`, `chartcand.py`, 밴드 CT1, MARC-TICK-36)

`charts` 는 막대·꺾은선의 **꼴**을 맞춰 도표를 찾는다(원·도넛, 위가 둥근 막대는 놓친다). `chartcands` 는 꼴을 알아맞히지 않고 **「이 쪽의 이 구역에 도표가 있을 것 같다」는 후보**만 낸다. 도표라고 판정한 것이 아니다. 문서 모델은 읽기만 하고 새 산출 `chart-candidates.json`(`schemas/chart-candidates.schema.json`)을 쓴다. OCR·LLM 을 쓰지 않는다.

```
uv run python -m docparse chartcands <pdf> --document <document.json> --out <dir>     # chart-candidates.json
```

신호는 둘이다.

- **그림 표식**: 벡터 그림 요소 가운데 괘선(가로·세로로 곧은 선, 두께 1.5pt 이하의 사각형)이 아닌 것. 글자를 담은 채운 도형(칸 배경, 글 상자)과 쪽 넓이의 25% 를 넘는 것은 뺀다.
- **떠 있는 숫자**: PDF 의 숫자 낱말 가운데, 문서 모델에서 머리글이 선 표(`status: ok` 이고 머리글 행이 있음)의 칸도 글 문단(숫자 낱말이 절반 미만인 블록)도 아닌 자리에 놓인 것.

| 정의 | 뜻 |
|---|---|
| `marks` | 표식이 3개 이상, 서로 40pt 안에 모인 구역. 여러 쪽의 같은 자리에 되풀이되는 구역(쪽의 20% 이상, 3쪽 이상)은 쪽 장식으로 뺀다 |
| `numbers` | 떠 있는 숫자가 3개 이상, 서로 40pt 안에 모인 구역 |
| `both` | `marks` 구역(여유 20pt) 안에 떠 있는 숫자가 2개 이상 든 것 |

- 문턱값은 `chartcand.py` 머리의 상수다. KB금융 2025 보고서의 몇 쪽을 보고 정한 추정이고, 정답을 보기 전에 굳힌 뒤 고치지 않았다(성적과 놓친 것의 까닭은 `band-reports/CT1-chart-tables.md`).
- 닿지 않는 것: 그림(비트맵)으로 박힌 도표, 표식이 한두 개뿐인 도표, 숫자가 머리글 선 표나 글 문단으로 들어간 도표(`numbers`·`both`).

## 번호표의 대상과 쪽의 글 (`tagtargets`, `tagtext`, `tagtext.py`, 밴드 VT1, MARC-TICK-42)

도표의 숫자에 계열·범주를 붙이는 일을 모델에게 맡길 때 쓰는 두 산출이다. `charts.py`(꼴을 맞춰 도표를 찾는다)에 기대지 않는다. 모델을 부르지 않고 값을 읽어 쓰지 않는다.

```
uv run python -m docparse tagtargets <PDF> --document <document.json> --out <디렉터리>   # number-words.json, tags.json
uv run python -m docparse tagtext    <PDF> --tags <tags.json> [--pages 8,11] --out <디렉터리>   # page-lines.json
uv run python -m docparse tagpages   <PDF> --tags <tags.json> --out <디렉터리> --dpi 200       # 번호표를 덮은 쪽 그림 (밴드 W 의 것 그대로)
```

- **숫자 낱말의 목록** (`number-words.json`): 쪽의 숫자 낱말(`layout.is_numeric`) 전부에 열쇠 `p<쪽>-w<순번>` 을 주고, 놓인 자리의 갈래(`headed_table` 머리글이 선 표의 칸 / `loose_table` 그 밖의 표 / `paragraph` 문단 / `label_block` 짧은 글 블록 / `none` 문서 모델에 이어지지 않음)와 문서 모델의 그릇·글자 자리(`links`)를 적는다. 번호표의 기준과 무관한 목록이다.
- **번호표의 대상** (`tags.json`): 그 목록에서 ① 표식 구역(`chartcand` 의 `marks`)이 하나라도 있는 쪽의 낱말이고 ② 연도 꼴(`is_year`)이 아니고 ③ 머리글이 선 표의 칸이 아니고 ④ 문단(낱말 8개 이상, 숫자 낱말이 절반 미만인 글 블록)에 놓이지 않은 것. **도표인지는 보지 않는다.** 그래서 큰 글씨 요약 숫자나 머리글 없는 표의 칸에도 붙는다. 번호표 목록에는 낱말의 글자를 싣지 않는다.
- **쪽의 글** (`page-lines.json`): PDF 의 낱말을 줄 토막(낱말 사이의 틈이 줄 높이의 0.8배를 넘으면 가른다)으로 묶어 `x0`·`x1`·`y`(쪽의 왼쪽 위 0, 오른쪽 아래 1000)와 함께 낸다. 번호표가 덮은 낱말은 `〔번호〕`, 그 밖의 숫자는 `▒` 로 바뀐다. 연도 꼴만 그대로 둔다(범주의 이름이다). `visible_numbers` 는 번호표가 덮지 않아 쪽 그림에 그대로 보이는 숫자 낱말의 수다.
- 문턱값(문단의 낱말 수 8, 토막의 틈 0.8)은 KB금융 2025 두 문서만 보고 정한 추정이다. 호출 전에 굳힌 뒤 고치지 않았다.
- 닿지 않는 것: 숫자에 다른 글자가 붙어 한 낱말이 된 것(`75점`, 연도 표시의 따옴표가 붙은 값)은 숫자 낱말이 아니라 번호표가 붙지 않는다. 머리글이 선 표로 읽힌 도표의 값, 문단으로 읽힌 조각 이름표의 값도 붙지 않는다. 성적과 놓친 수는 `band-reports/VT1-chart-image-text.md`.

## PDF 엔진 이음매 (`engine/`, `--engine`, 밴드 PD1, MARC-TICK-46)

분해기가 PDF 에서 읽는 것은 전부 `docparse/engine/` 을 지난다. 엔진은 둘이고 **기본은 `pymupdf`** 다(주지 않으면 산출이 이음매를 두기 전과 바이트까지 같다).

```
uv run python -m docparse parse <pdf> --out <dir> [--places] --engine pdfium      # parse·score·render·charts·chartcands·tagtargets·tagtext 에 같은 옵션
uv run python -m docparse enginediff <pdf> --a <pymupdf 산출 dir> --b <pdfium 산출 dir> --out <dir>   # engine-diff.json, engine-diff.md
```

| 엔진 | 라이브러리 | 라이선스 | 비고 |
|---|---|---|---|
| `pymupdf` | PyMuPDF (MuPDF) | AGPL-3.0 또는 Artifex 상용 | 기본. 이음매를 두기 전의 호출 그대로 |
| `pdfium` | pypdfium2 (PDFium) | Apache-2.0 또는 BSD-3-Clause (PDFium 은 BSD-3 과 딸린 라이브러리들의 라이선스) | pymupdf 를 import 하지 않는다 |

- 분해 규칙(`layout`, `tables_*`, `charts`, `chartcand`, `places`, `tagtext`, `document`, `render`, `score`)은 `pymupdf` 를 직접 부르지 않는다. 엔진이 건네는 자료의 꼴(쪽의 상자, 줄·스팬·글자, 낱말, 그리기 항목, 그림 상자, 책갈피, 쪽 그림)은 `engine/types.py` 머리에 적혀 있다.
- **pymupdf 가 남는 자리**: PDF 를 고쳐 쓰는 도구 둘(`tagpages.py` 의 글자 지우기·번호표 그리기, `placecheck.py` 의 글자 다시 읽기·견본 시트)과 테스트가 PDF 를 짓는 데.
- pdfium 은 낱 글자와 낱 경로만 준다. 줄·스팬·낱말·그리기 항목은 `engine/pdfium_engine.py` 가 다시 세운다: 내용 스트림의 차례로 글자를 따라가며 줄을 끊고(틈이 글자 크기의 0.15배를 넘으면 공백 하나, 0.8배를 넘으면 새 줄. 한글·한자 뒤에는 공백을 짓지 않는다), 글꼴·크기·색이 바뀌면 스팬을 가르고, 경로는 점의 꼴로 선 `l`·네모 `re`·사각 `qu`·곡선 `c` 를 가른다. 문턱은 PyMuPDF 가 내는 꼴을 지어낸 PDF 로 재서 맞춘 것이다.
- **두 엔진의 산출은 같지 않다.** 네 보고서에서 얼마나, 왜 다른지는 `band-reports/PD1-pdfium-engine.md`. 큰 까닭: pdfium 은 제어 글자(U+0007)와 공백뿐인 글 객체·잇단 공백을 글자 목록에서 빼고, 같은 자리에 잇달아 겹쳐 그린 글자를 스스로 버리고, 소프트 마스크 안의 그림을 쪽의 객체로 내지 않는다. 글꼴 파일이 PDF 에 없는 글꼴은 글자 상자의 높이가 엔진마다 다르다.
- git 워크트리(`<저장소>.worktrees/<밴드>`)에서는 원본 PDF 의 기본 자리(`<저장소>/../prework/reports`)가 맞지 않는다. `DOCPARSE_REPORTS_DIR=<prework/reports 의 절대 경로>` 를 주고 돌린다. 주지 않으면 원본을 읽는 테스트는 건너뛰지 않고 실패한다(`require_reports`).
- 엔진 테스트는 `tests/test_engine.py`(같은 PDF 를 두 엔진으로 읽어 나란히 견준다). PDF 를 읽는 기존 테스트는 `engine` 픽스처로 두 엔진에서 돈다(`tests/conftest.py`).
- `enginediff` 는 측정 도구다. 원재료(글자·줄·스팬·낱말·그리기 항목·그림 상자)와 산출 파일들을 견줘, `document.json` 이 다른 쪽마다 그 쪽의 원재료 차이를 까닭의 갈래로 붙인다.
