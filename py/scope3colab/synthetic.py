"""지어낸 입력: 계수 책(`배출계수_통합_DB.xlsx` 꼴)과 입력 템플릿(C1·C2 + C4-2·C9-2·C6-2·C5, 거리 시트는 머리글만). 전부 synthetic.

- 이름은 `가상`(한글 칸) 또는 `Synthetic`(NAICS Title 칸)으로 시작한다. 실제 기업·실제 계수 0.
- 수는 `9` 로 시작한다(S3C-a 표본 짓개 `support.py` 와 같은 약속). 계수 값도 지은 것이다 — 출처 없음.
- 같은 바이트 파일을 엔진 쪽(S3C-a `colab_157`)에도 준다: `build_inputs(out_dir)` 가 두 파일을 쓰고 자리를 돌려준다.

계수 책은 C1·C2 가 읽는 `구매_USEPA`(셀 9 `:540`, 셀 26 `:7890`)와 C4-2·C9-2·C6-2·C5 가 읽는 `운송_Spend`·`출장_Spend`·
`폐기물_C5`(셀 8 `:338`·`:346-347` 첫 읽기 header 2 → 셀 25 `:7216-7226` 머리글 자동 찾기로 다시 읽음) 넷이다. 머리글은 모두
0부터 2번째 줄(제목 줄·빈 줄 뒤). 다른 시트(에너지·운송·출장_통근 …)는 없고, 코랩 셀 8 의 `_load_ef_sheet` 는 없는 시트를 빈
frame 으로 둔다(코랩 그대로 경고만 찍음).

템플릿의 거리 시트(C4-1·C9-1·C6-1·C7)는 머리글만 두고 줄 0 이다 — 코랩 `read_template_sheet`(셀 19 `:2263-2264`)가 빈 frame 에
「입력방식」 칸을 넣다 ValueError 를 내고, 셀 22 의 시트별 `try`(`:6198-6200`)가 `[error]` 를 찍고 그 시트를 건너뛴다(실측 —
`[empty]` 길 `:5970-5973` 은 타지 않는다). 거리 시트에 줄을 넣는 것은 S3C-o3 몫이다(geopy 가짜가 부르는 순간 멈춘다).

거리판(S3C-o3, 아래쪽 `build_distance_inputs`·`build_distance_transport_inputs`)은 기본판을 건드리지 않고 따로 짓는다.
"""

from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook, load_workbook

FACTOR_BOOK_NAME = "배출계수_통합_DB.xlsx"
TEMPLATE_NAME = "synthetic_scope3_template.xlsx"

USEPA_HEADERS = (
    "NAICS 코드",
    "NAICS Title",
    "품목군",
    "상세 카테고리",
    "한국어매핑키워드",
    "EF (kgCO2e/USD)",
    "EF (kgCO2e/KRW) 2025년말 기준 (1,400 KRW/USD)",
)

# (코드, Title, 품목군, 상세 카테고리, 한국어매핑키워드, USD 계수, 2025 원화 계수) — 지은 값
USEPA_ROWS: tuple[tuple[str, str, str, str, str, float, float], ...] = (
    ("900001", "Synthetic Plastics Material Manufacturing", "원재료/부자재", "가상 수지", "가상수지, 가상펠릿", 0.91, 0.00091),
    ("900002", "Synthetic Paperboard Container Manufacturing", "사무용품/포장재", "가상 포장", "가상상자, 가상완충재, 가상펠릿", 0.92, 0.00092),
    ("900003", "Synthetic Computer Equipment Manufacturing", "IT장비", "가상 전산", "가상단말, 가상서버", 0.93, 0.00093),
    ("900004", "Synthetic Management Consulting Services", "외주서비스", "가상 용역", "가상자문, 가상용역", 0.94, 0.00094),
    ("900005", "Synthetic Industrial Machinery Manufacturing", "생산설비", "가상 설비", "가상압착기, 가상설비, 가상펠릿", 0.95, 0.00095),
    ("900006", "Synthetic Motor Vehicle Manufacturing", "차량/운송장비", "가상 차량", "가상화물차, 가상펠릿", 0.96, 0.00096),
    ("900007", "Synthetic Office Furniture Manufacturing", "사무가구", "가상 가구", "가상책상, 가상의자, 가상펠릿", 0.97, 0.00097),
    ("900008", "Synthetic Cleaning Services", "외주서비스", "가상 청소", "가상청소", 0.98, 0.00098),
)

# 운송_Spend·출장_Spend: (구분·항목, NAICS 코드, Title, 한국어 품목명, 한국어 매핑 키워드, USD 계수, 2025 원화 계수) — 지은 값.
# Title 에 코랩 fallback 이 찾는 실제 Title 글(`General Freight Trucking, Local`·`Scheduled Passenger Air`)은 넣지 않는다.
SPEND_HEADERS = ("NAICS 코드", "NAICS Title (US EPA)", "한국어 품목명", "한국어 매핑 키워드", "EF\n(kgCO2e/USD)", "비고",
                 "EF (kgCO2e/KRW) 2025년말 기준 (1,400 KRW/USD)")  # fmt: skip
TRANSPORT_SPEND_ROWS: tuple[tuple[str, str, str, str, str, float, float], ...] = (
    ("가상 도로", "900101", "Synthetic Truck Transportation", "가상 트럭 운송", "가상트럭, 가상화물", 0.911, 0.000911),
    ("가상 택배", "900102", "Synthetic Couriers and Messengers", "가상 택배", "가상택배", 0.912, 0.000912),
    ("가상 항공", "900103", "Synthetic Air Transportation", "가상 항공 화물", "가상항공", 0.913, 0.000913),
    ("가상 해상", "900104", "Synthetic Water Transportation", "가상 해상 운송", "가상해상", 0.914, 0.000914),
)
TRAVEL_SPEND_ROWS: tuple[tuple[str, str, str, str, str, float, float], ...] = (
    ("가상 항공", "900201", "Synthetic Passenger Flights", "가상 항공권", "가상항공권, 가상비행", 0.921, 0.000921),
    ("가상 숙박", "900202", "Synthetic Traveler Accommodation", "가상 숙박", "가상숙박, 가상호텔", 0.922, 0.000922),
    ("가상 택시", "900203", "Synthetic Taxi Service", "가상 택시", "가상택시", 0.923, 0.000923),
)
# 폐기물_C5: (폐기물 종류, 처리방법, 매칭 키워드, 구분, 매핑 EPD 원본명, 배출계수 kgCO2eq/kg) — 지은 값
WASTE_C5_HEADERS = ("폐기물 종류\n(템플릿 드롭다운)", "처리방법\n(템플릿 드롭다운)", "매칭 키워드\n(추가 동의어)",
                    "구분\n(지정/일반/생활)", "매핑 EPD 원본명", "배출계수\n(kgCO2eq/kg)", "비고")  # fmt: skip
WASTE_C5_ROWS: tuple[tuple[str, str, str, str, str, float], ...] = (
    ("가상폐지", "소각", "가상골판지", "가상 생활", "가상 EPD 폐지 소각", 0.931),
    ("가상폐지", "재활용", "가상골판지", "가상 생활", "가상 EPD 폐지 재활용", 0.0932),
    ("가상폐수지", "소각", "가상수지찌꺼기", "가상 일반", "가상 EPD 수지 소각", 0.933),
    ("가상폐수지", "매립", "가상수지찌꺼기", "가상 일반", "가상 EPD 수지 매립", 0.934),
    ("가상잔반", "소각", "가상식당", "가상 생활", "가상 EPD 잔반 소각", 0.935),
)

C1_SHEET = "C1_구매품서비스"
C2_SHEET = "C2_자본재"
MONTHS = tuple(f"{i}월" for i in range(1, 13))

C1_MAIN = ("*국가", "*사업장", "*품목군", "*상세 품목명/서비스명", "계정과목(선택)", "거래처명(선택)", "연간 입력",
           "월별 지출금액", *([None] * 11), "비고(선택)")  # fmt: skip
C1_SUB = (None, None, None, None, None, None, "연간지출금액(원)", *MONTHS, None)
C2_MAIN = ("*국가", "*사업장", "*자산분류", "*상세 자산명", "자산번호(선택)", "계정과목(선택)", "거래처명(선택)",
           "연간 입력", "월별 취득금액", *([None] * 11), "비고(선택)")  # fmt: skip
C2_SUB = (None, None, None, None, None, None, None, "연간취득금액(원)", *MONTHS, None)

type Cell = str | int | float | None


def _annual(prefix: tuple[Cell, ...], amount: int, note: str | None = None) -> tuple[Cell, ...]:
    return (*prefix, amount, *([None] * 12), note)


def _monthly(prefix: tuple[Cell, ...], months: dict[int, int], note: str | None = None) -> tuple[Cell, ...]:
    return (*prefix, None, *(months.get(i) for i in range(1, 13)), note)


# C1 줄: 품목군 있음(사용자 입력 그룹)·없음(규칙 키워드 / AI 그룹핑 / 아무것도 안 맞음)·월별 입력을 고루 둔다.
# 1번은 계수 책 키워드 다섯이 걸려(「가상펠릿」 넷은 같은 점수 — 동점 차례) 추천 넷이 다 찬다.
C1_ROWS: tuple[tuple[Cell, ...], ...] = (
    _annual(("가상국", "가상사업장A", "원재료", "가상수지 가상펠릿", "가상원재료비", "가상상사"), 9100000),
    _annual(("가상국", "가상사업장A", "포장재", "가상상자 묶음", None, "가상포장"), 9200000, "가상 비고"),
    _annual(("가상국", "가상사업장B", None, "가상자문 용역 계약", "가상지급수수료", None), 9300000),
    _annual(("가상국", "가상사업장B", None, "가상부속 세트", None, None), 9400000),
    _monthly(("가상국", "가상사업장A", "사무용품", "가상의자 교체", None, "가상가구"), {1: 9500, 3: 9600, 12: 9700}),
)
# C2 줄: 자산분류 있음·없음, 월별 하나.
C2_ROWS: tuple[tuple[Cell, ...], ...] = (
    _annual(("가상국", "가상사업장A", "생산설비", "가상압착기 1호", "가상-0001", "가상기계장치", "가상기계"), 91000000),
    _annual(("가상국", "가상사업장B", None, "가상화물차 2대", "가상-0002", None, None), 92000000),
    _monthly(("가상국", "가상사업장A", "IT장비", "가상서버 증설", "가상-0003", None, None), {6: 930000, 7: 940000}),
)


# C4-2·C9-2·C6-2·C5 — 코랩 `SHEET_PROCESSOR_MAP`(`:5641`, 소문자로 견줌) 키와 같은 이름, 머리글은 각 표준화 함수의
# docstring(`:5169`·`:5199`·`:5272`·`:5457`)과 `read_template_sheet`(`:2128`)의 두 줄 머리(주 머리 + 연간·월 서브) 꼴.
C4_SPEND_SHEET = "C4-2_운송금액"
C9_SPEND_SHEET = "C9-2_운송금액"
C6_SPEND_SHEET = "C6-2_출장비"
C5_SHEET = "C5_사업장폐기물"
C4_SPEND_MAIN = ("*국가", "*사업장", "운송수단(선택)", "*자재명/품목명", "거래처명(선택)", "연간 입력", "월별 운송금액",
                 *([None] * 11), "비고(선택)")  # fmt: skip
C4_SPEND_SUB = (None, None, None, None, None, "연간운송금액(원)", *MONTHS, None)
C9_SPEND_MAIN = ("*국가", "*사업장", "운송수단(선택)", "*제품명", "거래처명(선택)", "연간 입력", "월별 운송금액",
                 *([None] * 11), "비고(선택)")  # fmt: skip
C6_SPEND_MAIN = ("*국가", "*출장주체", "*출장항목", "인원수", "연간 입력", "월별 지출금액", *([None] * 11), "비고(선택)")
C6_SPEND_SUB = (None, None, None, None, "연간지출금액(원)", *MONTHS, None)
C5_MAIN = ("*국가", "*사업장", "*폐기물 종류", "상세 폐기물명(선택)", "*단위", "*처리방법", "연간 입력", "월별 처리량",
           *([None] * 11), "비고(선택)")  # fmt: skip
C5_SUB = (None, None, None, None, None, None, "연간처리량", *MONTHS, None)

# C4-2 줄: 운송수단 키워드 걸림 · 운송수단 빈 칸(코랩 `x or y` 에서 NaN 이 이겨 q="" → 첫 줄) · 아무것도 안 맞음(계수 없음)·월별.
C4_SPEND_ROWS: tuple[tuple[Cell, ...], ...] = (
    _annual(("가상국", "가상사업장A", "가상트럭 배송", "가상수지 원료", "가상운송사"), 9110000),
    _annual(("가상국", "가상사업장A", None, "가상택배 부품", None), 9120000, "가상 비고"),
    _monthly(("가상국", "가상사업장B", "가상수레", "가상상자 자재", None), {2: 913000, 5: 914000}),
)
# C9-2 줄: 항공·해상 키워드, 월별 하나.
C9_SPEND_ROWS: tuple[tuple[Cell, ...], ...] = (
    _annual(("가상국", "가상사업장A", "가상항공 화물", "가상의자 완제품", "가상고객사"), 9210000),
    _monthly(("가상국", "가상사업장B", "가상해상 운송", "가상책상 완제품", None), {4: 922000, 9: 923000}),
)
# C6-2 줄: 키워드 걸림 둘(항공권·숙박) · 아무것도 안 맞음 · 월별.
C6_SPEND_ROWS: tuple[tuple[Cell, ...], ...] = (
    _annual(("가상국", "가상본부", "가상항공권", 9), 9310000),
    _annual(("가상국", "가상본부", "가상호텔 숙박", 9), 9320000),
    _monthly(("가상국", "가상지사", "가상기타 경비", 9), {3: 933000, 11: 934000}),
)
# C5 줄: 정확 일치 소각(생물기원 폐지류 FCF 0.01) · 키워드 걸림 소각(생물기원 아님) · 매립·톤 단위 · 계수 책에 없는 처리방법
# 조합(전체 평균 fallback) · 월별.
C5_ROWS: tuple[tuple[Cell, ...], ...] = (
    _annual(("가상국", "가상사업장A", "가상폐지", "가상 사무 폐지", "kg", "소각"), 9100),
    _annual(("가상국", "가상사업장A", "가상수지찌꺼기 묶음", None, "kg", "소각"), 9200),
    _annual(("가상국", "가상사업장B", "가상폐수지", None, "톤", "매립"), 9.3),
    _annual(("가상국", "가상사업장B", "가상잔반", None, "kg", "재활용"), 9400),
    _monthly(("가상국", "가상사업장A", "가상폐지", None, "kg", "재활용"), {1: 950, 6: 960}),
)

# 거리 시트 — 머리글만(줄 0). 머리글은 표준화 함수 docstring(`:5102`·`:5446`·`:5223`·`:5304`) 꼴.
DISTANCE_SHEETS: tuple[tuple[str, tuple[Cell, ...], tuple[Cell, ...]], ...] = (
    ("C4-1_운송거리",
     ("*국가", "*사업장", "운송수단(선택)", "*자재명/품목명", "*중량단위", "출발지", "도착지", "운송거리(km)(선택)",
      "연간 입력", "월별 운송량", *([None] * 11), "비고(선택)"),
     (None, None, None, None, None, None, None, None, "연간운송량", *MONTHS, None)),
    ("C6-1_출장거리",
     ("*국가", "*출장주체", "*교통수단", "*인원수", "출발지", "도착지", "편도거리(km)(선택)", "연간 입력", "월별 이동횟수",
      *([None] * 11), "비고(선택)"),
     (None, None, None, None, None, None, None, "연간이동횟수", *MONTHS, None)),
    ("C7_임직원통근",
     ("*국가", "*사업장", "*교통수단", "*인원수", "출발지", "도착지", "편도거리(km)(선택)", "연간 입력", "월별 근무일수",
      *([None] * 11), "비고(선택)"),
     (None, None, None, None, None, None, None, "연간근무일수", *MONTHS, None)),
    ("C9-1_운송거리",
     ("*국가", "*사업장", "운송수단(선택)", "*제품명", "*중량단위", "출발지", "도착지", "운송거리(km)(선택)",
      "연간 입력", "월별 운송량", *([None] * 11), "비고(선택)"),
     (None, None, None, None, None, None, None, None, "연간운송량", *MONTHS, None)),
)  # fmt: skip


@dataclass(frozen=True)
class Inputs:
    factor_book: Path
    template: Path


def _factor_sheet(wb: Workbook, title: str, headers: tuple[str, ...], rows: tuple[tuple[Cell, ...], ...]) -> None:
    ws = wb.create_sheet(title)
    ws.append(["가상 계수 책 — synthetic(지은 값, 출처 없음)"])
    ws.append([None])
    ws.append(list(headers))
    for row in rows:
        ws.append(list(row))


def _spend_row(row: tuple[str, str, str, str, str, float, float]) -> tuple[Cell, ...]:
    return (*row[:6], None, row[6])  # 비고 칸은 비운다


def build_factor_book(path: Path) -> Path:
    wb = Workbook()
    default = wb.active
    assert default is not None
    wb.remove(default)
    _factor_sheet(wb, "구매_USEPA", USEPA_HEADERS, USEPA_ROWS)
    _factor_sheet(wb, "운송_Spend", ("운송 구분", *SPEND_HEADERS), tuple(map(_spend_row, TRANSPORT_SPEND_ROWS)))
    _factor_sheet(wb, "출장_Spend", ("출장 항목", *SPEND_HEADERS), tuple(map(_spend_row, TRAVEL_SPEND_ROWS)))
    _factor_sheet(wb, "폐기물_C5", WASTE_C5_HEADERS, tuple((*r, None) for r in WASTE_C5_ROWS))
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def _template_sheet(wb: Workbook, title: str, main: tuple[Cell, ...], sub: tuple[Cell, ...],
                    rows: tuple[tuple[Cell, ...], ...]) -> None:  # fmt: skip
    ws = wb.create_sheet(title)
    ws.append([f"카테고리 안내(가상) — {title}"])
    ws.append(list(main))
    ws.append(list(sub))
    for row in rows:
        ws.append(list(row))


def build_template(path: Path) -> Path:
    wb = Workbook()
    default = wb.active
    assert default is not None
    wb.remove(default)
    _template_sheet(wb, C1_SHEET, C1_MAIN, C1_SUB, C1_ROWS)
    _template_sheet(wb, C2_SHEET, C2_MAIN, C2_SUB, C2_ROWS)
    # 시트 차례는 받은 템플릿 차례(C4 → C5 → C6 → C7 → C9). 코랩은 이 차례로 돌고 AI 부름 차례도 이것을 따른다.
    distance = {name: (main, sub) for name, main, sub in DISTANCE_SHEETS}
    _template_sheet(wb, "C4-1_운송거리", *distance["C4-1_운송거리"], ())
    _template_sheet(wb, C4_SPEND_SHEET, C4_SPEND_MAIN, C4_SPEND_SUB, C4_SPEND_ROWS)
    _template_sheet(wb, C5_SHEET, C5_MAIN, C5_SUB, C5_ROWS)
    _template_sheet(wb, "C6-1_출장거리", *distance["C6-1_출장거리"], ())
    _template_sheet(wb, C6_SPEND_SHEET, C6_SPEND_MAIN, C6_SPEND_SUB, C6_SPEND_ROWS)
    _template_sheet(wb, "C7_임직원통근", *distance["C7_임직원통근"], ())
    _template_sheet(wb, "C9-1_운송거리", *distance["C9-1_운송거리"], ())
    _template_sheet(wb, C9_SPEND_SHEET, C9_SPEND_MAIN, C4_SPEND_SUB, C9_SPEND_ROWS)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def build_inputs(out_dir: Path) -> Inputs:
    """`<out_dir>/factors/배출계수_통합_DB.xlsx` 와 `<out_dir>/synthetic_scope3_template.xlsx`."""
    return Inputs(build_factor_book(out_dir / "factors" / FACTOR_BOOK_NAME), build_template(out_dir / TEMPLATE_NAME))


# ── 거리판(S3C-o3, `--input distance`) ──────────────────────────────────────────────────────────────────────────────
# 기본판(위)은 바이트 그대로 두고 거리 시트 넷에만 줄이 있는 템플릿과 계수 책을 따로 짓는다(발주서 §1 「입력은 두 판」).
# 계수 책은 기본판 네 시트에 `운송`(C4·C9 거리, 셀 8 `:330` header_row=1 → 셀 25 `:7210` 재읽기 낱말 「표준코드」·「배출계수」)과
# `출장_통근`(C6·C7, `:334` header_row=1 → `:7213` 「표준코드」·「EF」)을 더한다. 두 시트는 제목 줄 하나 뒤 둘째 줄이 머리글이다.
# 표준코드는 코랩이 읽는 코드 그대로(운송: `_template_transport_code` `:7644` 의 일곱, 출장_통근: 우리 줄이 닿는 여섯).
# 계수는 지은 값이다(출처 없음). 코랩 `TRAVEL_EF_MAP` 은 DEFRA 기본값 위에 이 시트를 덮으니(`:441-471`) 우리 줄이 닿는 코드는
# 다 넣는다 — 기본값(실제 수)이 결과에 섞이지 않게.
TRANSPORT_HEADERS = ("운송수단", "표준코드", "배출계수\n(kgCO2eq/ton·km)", "비고")
TRANSPORT_ROWS: tuple[tuple[str, str, float], ...] = (
    ("가상 도로", "road", 0.0911),
    ("가상 택배", "parcel", 0.0912),
    ("가상 철도", "rail", 0.0913),
    ("가상 해상", "sea", 0.0914),
    ("가상 항공", "air", 0.915),
    ("가상 벌크", "sea_bulk", 0.0916),
    ("가상 내륙수로", "inland_water", 0.0917),
)
TRAVEL_COMMUTE_HEADERS = ("이동수단", "표준코드", "EF\n(kgCO2eq/인·km)", "비고")
TRAVEL_COMMUTE_ROWS: tuple[tuple[str, str, float], ...] = (
    ("가상 항공", "flight", 0.0921),
    ("가상 기차", "train", 0.0922),
    ("가상 버스", "bus", 0.0923),
    ("가상 승용차", "car", 0.0924),
    ("가상 택시", "taxi", 0.0925),
    ("가상 지하철", "subway", 0.0926),
)

# 거리판 줄 — 줄마다 노린 갈래는 README 「거리판 줄」 표. 국가는 `가상국`(KR 아님): 코랩 C4·C9 본 고리는 국가와 상관없이
# `get_distance_km(출발, 도착)`(도로·KR 기본값, 셀 22 `:6013`)을 부르고, C6·C7 도 KR 기본값으로 부른다. 행의 국가가 쓰이는 것은
# C4·C9 에서 첫 거리가 못 나와 `calc_transport` → `infer_distance`(`:1709`)로 다시 갈 때뿐이다(C9-1 1번).
# 지명은 `가상시<글자>`·`가상공항<글자>`, 공항 코드 꼴은 지은 세 글자 `ZZQ` 하나(`_looks_like_airport` `:1625` 의 IATA 갈래).
C4_DIST_SHEET = "C4-1_운송거리"
C6_DIST_SHEET = "C6-1_출장거리"
C7_SHEET = "C7_임직원통근"
C9_DIST_SHEET = "C9-1_운송거리"
_DIST_MAIN = {name: main for name, main, _ in DISTANCE_SHEETS}
_DIST_SUB = {name: sub for name, _, sub in DISTANCE_SHEETS}

# C4-1: (국가, 사업장, 운송수단, 자재명/품목명, 중량단위, 출발지, 도착지, 운송거리) + 연간·월별 운송량 + 비고
C4_DIST_ROWS: tuple[tuple[Cell, ...], ...] = (
    # 1 거리 있음 + 운송수단 키워드(트럭 → road): AI 0
    _annual(("가상국", "가상사업장A", "가상트럭", "가상수지 원료", "ton", "가상시가", "가상시나", 91), 9.1),
    # 2 거리 빔 + 지명, 도로: 도시 정규화 둘 → 지오코더 둘(None) → AI 직선 × 1.3 → 검증 통과
    _annual(("가상국", "가상사업장A", "가상화물차", "가상상자 자재", "kg", "가상시가", "가상시다", None), 9200),
    # 3 직선 × 1.3 이 국내 도로 상한 800 km 를 넘음 → AI 실거리
    _annual(("가상국", "가상사업장B", "가상트럭", "가상부품 자재", "ton", "가상시라", "가상시마", None), 9.3),
    # 4 중량단위가 사전에 없음 → 표준화 때 단위 AI(`_patch_weight_to_kg` `:6486`)
    _annual(("가상국", "가상사업장B", "가상트럭", "가상서랍 자재", "가상팔레트", "가상시가", "가상시나", 94), 9.4),
    # 5 운송수단이 키워드로 안 잡힘 → 운송수단 AI(`_ai_infer_transport_mode` `:7684`)
    _annual(("가상국", "가상사업장A", "가상수레", "가상부속 세트", "kg", "가상시가", "가상시나", 95), 9500),
    # 6 2번과 같은 도시(캐시 — 다시 부르지 않음) + 월별 입력(접혀 템플릿 차례 그대로)
    _monthly(("가상국", "가상사업장A", "가상화물차", "가상의자 자재", "kg", "가상시가", "가상시다", None), {2: 960, 8: 970}),
)
# C9-1: (국가, 사업장, 운송수단, 제품명, 중량단위, 출발지, 도착지, 운송거리) + 운송량 + 비고
C9_DIST_ROWS: tuple[tuple[Cell, ...], ...] = (
    # 1 항공 + 공항이 아닌 지명: 본 고리 도로 길이 상한에 걸려 None → `infer_distance`(국가 `가상국`)의 항공 길 → 공항 AI 둘
    _annual(("가상국", "가상사업장A", "가상항공 화물", "가상의자 완제품", "kg", "가상시파", "가상시하", None), 9100),
    # 2 항공 + 공항 이름·세 글자 코드: 공항 AI 0(`_looks_like_airport`)
    _annual(("가상국", "가상사업장B", "가상항공", "가상책상 완제품", "kg", "가상공항가", "ZZQ", None), 9200),
    # 3 거리 있음 + 해상 키워드 + 월별: AI 0
    _monthly(("가상국", "가상사업장B", "가상해상 운송", "가상선반 완제품", "ton", None, None, 999), {4: 9.1, 9: 9.2}),
)
# C6-1: (국가, 출장주체, 교통수단, 인원수, 출발지, 도착지, 편도거리) + 연간·월별 이동횟수 + 비고
C6_DIST_ROWS: tuple[tuple[Cell, ...], ...] = (
    # 1 편도거리 있음(버스): AI 0
    _annual(("가상국", "가상본부", "가상버스", 9, "가상시사", "가상시아", 99), 9),
    # 2 비행기 + 지명: 공항 AI 둘 → 공항 이름으로 도시 정규화 둘 → 지오코더 둘 → AI 직선 × 1.09
    _annual(("가상국", "가상본부", "가상비행기", 9, "가상시자", "가상시차", None), 9),
    # 3 이동수단을 모름 → 이동수단 AI(`_ai_normalize_travel_mode` `:8523`), 월별 입력
    _monthly(("가상국", "가상지사", "가상이동", 9, "가상시사", "가상시아", 9.9), {3: 9, 7: 9}),
    # 4 기차 + C4-1 2번과 같은 도시: 캐시 — AI 0, 철도 보정 1.2
    _annual(("가상국", "가상지사", "가상기차", 9, "가상시가", "가상시다", None), 9),
)
# C7: (국가, 사업장, 교통수단, 인원수, 출발지, 도착지, 편도거리) + 연간·월별 근무일수 + 비고
C7_ROWS: tuple[tuple[Cell, ...], ...] = (
    # 1 거리 빔 + 직선 × 1.3 이 100 km 를 넘음 → 교차검증 `_ai_estimate_straight_km`(같은 인자 — 캐시)
    _annual(("가상국", "가상사업장A", "가상승용차", 9, "가상시카", "가상시타", None), 99),
    # 2 편도거리 있음(지하철) + 월별 입력
    _monthly(("가상국", "가상사업장B", "가상지하철", 9, None, None, 9.9), {1: 9, 2: 9}),
    # 3 버스·지하철 섞임(평균 계수) + C4-1 2번과 같은 도시(캐시)
    _annual(("가상국", "가상사업장A", "가상버스 지하철", 9, "가상시가", "가상시다", None), 99),
)
DISTANCE_TEMPLATE_ROWS: dict[str, tuple[tuple[Cell, ...], ...]] = {
    C4_DIST_SHEET: C4_DIST_ROWS,
    C6_DIST_SHEET: C6_DIST_ROWS,
    C7_SHEET: C7_ROWS,
    C9_DIST_SHEET: C9_DIST_ROWS,
}  # 받은 템플릿 차례(C4 → C6 → C7 → C9) — 코랩은 이 차례로 돌고 AI 부름 차례도 이것을 따른다

# 운송 갈래(감독 처분 4 — 짝 b2a 몫): C4-1·C9-1 만 둔 템플릿. C6-1·C7 시트는 두지 않는다(b2a dump 는 그 시트에 줄이 있으면 멈춤).
DISTANCE_TRANSPORT_SHEETS = (C4_DIST_SHEET, C9_DIST_SHEET)

DISTANCE_FACTOR_BOOK_DIR = "factors-distance"
DISTANCE_TEMPLATE_NAME = "synthetic_scope3_distance_template.xlsx"
DISTANCE_TRANSPORT_TEMPLATE_NAME = "synthetic_scope3_distance_transport_template.xlsx"


def _factor_sheet_header_row1(wb: Workbook, title: str, headers: tuple[str, ...], rows: tuple[tuple[Cell, ...], ...]) -> None:
    ws = wb.create_sheet(title)
    ws.append(["가상 계수 책 — synthetic(지은 값, 출처 없음)"])
    ws.append(list(headers))
    for row in rows:
        ws.append(list(row))


def build_distance_factor_book(path: Path) -> Path:
    """기본판 네 시트 + `운송`·`출장_통근`(둘째 줄 머리글)."""
    build_factor_book(path)
    wb = load_workbook(path)
    _factor_sheet_header_row1(wb, "운송", TRANSPORT_HEADERS, tuple((*r, None) for r in TRANSPORT_ROWS))
    _factor_sheet_header_row1(wb, "출장_통근", TRAVEL_COMMUTE_HEADERS, tuple((*r, None) for r in TRAVEL_COMMUTE_ROWS))
    wb.save(path)
    return path


def build_distance_template(path: Path, sheets: tuple[str, ...] = tuple(DISTANCE_TEMPLATE_ROWS)) -> Path:
    """거리 시트(기본 넷, `sheets` 로 고름)에만 줄. C1·C2·지출·폐기물 시트는 두지 않는다. 차례는 늘 받은 템플릿 차례."""
    wb = Workbook()
    default = wb.active
    assert default is not None
    wb.remove(default)
    for name, rows in DISTANCE_TEMPLATE_ROWS.items():
        if name not in sheets:
            continue
        _template_sheet(wb, name, _DIST_MAIN[name], _DIST_SUB[name], rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def build_distance_inputs(out_dir: Path) -> Inputs:
    """`<out_dir>/factors-distance/배출계수_통합_DB.xlsx` 와 `<out_dir>/synthetic_scope3_distance_template.xlsx`."""
    return Inputs(
        build_distance_factor_book(out_dir / DISTANCE_FACTOR_BOOK_DIR / FACTOR_BOOK_NAME),
        build_distance_template(out_dir / DISTANCE_TEMPLATE_NAME),
    )


def build_distance_transport_inputs(out_dir: Path) -> Inputs:
    """운송 갈래: 계수 책은 거리판과 같고 템플릿은 C4-1·C9-1 만."""
    return Inputs(
        build_distance_factor_book(out_dir / DISTANCE_FACTOR_BOOK_DIR / FACTOR_BOOK_NAME),
        build_distance_template(out_dir / DISTANCE_TRANSPORT_TEMPLATE_NAME, DISTANCE_TRANSPORT_SHEETS),
    )


# ── 나머지 판(S3C-o4a, `--input rest`) ──────────────────────────────────────────────────────────────────────────────
# 기본판·거리판은 바이트 그대로 두고 나머지 결과 시트 일곱(C3·C8·C11·C12·C13·C14·C15)의 템플릿 시트 열하나에만 줄이 있는 템플릿과
# 계수 책을 따로 짓는다(발주서 §1-1). v2.1 꼴(「적용 기업명」 줄·매핑 시트 없음).
# 계수 책은 기본판 네 시트에 셋을 더한다:
# - `에너지`(C8·C11·C13·C14): 셀 8 `:326` 첫 읽기 header_row=3 → 셀 25 `:7205` 재읽기 낱말 「활동유형」·「CO2eq」·「기준단위」.
#   넷째 줄이 머리글(제목 줄 하나·안내 줄 둘 뒤).
# - `에너지_C3`(C3 — 셀 29 `:9048` header_row=2, 재읽기 없음): 셋째 줄이 머리글. 발주서 초안에 없던 시트다(C3 는 `에너지` 를
#   읽지 않는다 — `lookup_energy_ef_c3` `:9113`). `도시가스(LNG)` 줄을 일부러 빼 C3 이 `천연가스(LNG)` 로 대체 매칭되고
#   발열량 다리(`_bridge_convert_via_lhv` `:9146`)를 타게 한다.
# - `폐기물_C12`(C12): 셀 8 `:342` 첫 읽기 header_row=1, 코드 칸 셋은 글(`str_cols`) → 셀 25 `:7219` 재읽기 「폐기물 명칭」·「매립 EF」.
#   둘째 줄이 머리글. 코드는 지은 `9x` 꼴이고 `91-02` 만 코랩 생물기원 접두(`_BIOGENIC_C12_CODE_PREFIX` 셀 8 `:365`, 폐지류)다.
# 에너지원 이름(전력·경유·도시가스(LNG)·천연가스(LNG))은 코랩 규칙이 읽는 분류어다(`ENERGY_BASE_UNIT` `:3723` — 처리방법과 같은
# 취급). 계수 값은 지은 것이다(출처 없음). 이 이름들을 `에너지` 시트에 다 넣어 코랩의 폴백 계수(`ENERGY_FALLBACK_EF_PATCH` `:6805`
# — 실제 수)가 결과에 섞이지 않게 한다. 단위 환산 배수와 발열량(`ENERGY_BASE_UNIT`)·평균 폐기물 계수(`WASTE_AVG_EF`)는 코랩
# 코드의 상수라 그 갈래의 결과에는 그 수가 든다(README 「나머지 판」).
ENERGY_HEADERS = ("구분", "배출활동", "활동유형(에너지원)", "투입량 기준단위", "순발열량", "CO2eq ★\n(kgCO2eq/단위)")
ENERGY_ROWS: tuple[tuple[Cell, ...], ...] = (
    ("가상 간접", "가상 전력 사용", "전력", "kWh", None, 0.0941),
    ("가상 직접", "가상 이동연소", "경유", "L", 9.1, 0.942),
    ("가상 직접", "가상 고정연소", "도시가스(LNG)", "Nm³", 9.2, 0.943),
    ("가상 직접", "가상 고정연소", "천연가스(LNG)", "kg", 9.3, 0.944),
)
ENERGY_C3_HEADERS = ("활동유형(에너지원)", "기준단위", "EF\n(kgCO2eq/단위)", "비고")
ENERGY_C3_ROWS: tuple[tuple[Cell, ...], ...] = (
    ("전력", "kWh", 0.0951, None),
    ("경유", "L", 0.0952, None),
    ("천연가스(LNG)", "kg", 0.0953, None),
)
WASTE_C12_HEADERS = ("대분류\n코드", "대분류", "중분류\n코드", "중분류", "소분류\n코드", "폐기물 명칭",
                     "매립 EF\n(kgCO2eq/kg)", "소각 EF\n(kgCO2eq/kg)", "재활용 EF\n(kgCO2eq/kg)")  # fmt: skip
WASTE_C12_ROWS: tuple[tuple[Cell, ...], ...] = (
    ("91", "가상 생활", "91-02", "가상 종이", "91-02-01", "가상 폐지 묶음", 0.961, 0.962, 0.0963),
    ("92", "가상 사업장", "92-01", "가상 수지", "92-01-01", "가상 폐수지 묶음", 0.964, 0.965, 0.0966),
    ("93", "가상 건설", "93-01", "가상 잔재", "93-01-01", "가상 잔재 묶음", 0.967, 0.968, 0.0969),
)

C3_SHEET = "C3_연료에너지"
C8_ENERGY_SHEET = "C8-1_에너지사용량 아는 경우"
C8_AREA_SHEET = "C8-2_면적"
C8_PERSON_SHEET = "C8-3_인원"
C11_SHEET = "C11_판매제품사용"
C12_SHEET = "C12_판매제품폐기"
C13_ENERGY_SHEET = "C13-1_에너지사용량 아는경우"
C13_AREA_SHEET = "C13-2_면적"
C13_PERSON_SHEET = "C13-3_인원"
C14_SHEET = "C14_프랜차이즈"
C15_SHEET = "C15_투자"

# 머리글은 표준화 docstring·rename 표(`:5078`·`:5353`·`:5375`·`:5411`·`:5466`·`:5485`·`:5505`·`:5526`·`:5558`·`:5590`·`:5610`과 뒤
# 정의 `:6870-6960`)와 `KNOWN_TEMPLATE_HEADERS`(셀 19 `:2295-2326`)의 글자. 월별 칸이 있는 시트(C3·C8·C13)는 두 줄 머리(주 머리 +
# 연간·월 서브 — `read_template_sheet` `:2128`), 없는 시트(C11·C12·C14·C15)는 한 줄 머리다. 면적·인원 시트의 연간 칸 서브
# 「건물전체 연간사용량」은 코랩 `ANNUAL_SET` 밖이라 그 시트는 줄마다 입력방식이 「월별」로 붙는다(실측 — 접기 `:6509` 가 연간 칸으로 되돌림).
_MONTH_GROUP = (*([None] * 11),)
C3_MAIN = ("*국가", "*사업장", "*연료/에너지명", "*단위", "연간 입력", "월별 사용량", *_MONTH_GROUP, "비고(선택)")
C3_SUB = (None, None, None, None, "연간사용량", *MONTHS, None)
C8_ENERGY_MAIN = ("*국가", "*임차자산명", "*에너지종류", "*단위", "연간 입력", "월별 사용량", *_MONTH_GROUP, "비고(선택)")
C13_ENERGY_MAIN = ("*국가", "*임대자산명", "*에너지종류", "*단위", "연간 입력", "월별 사용량", *_MONTH_GROUP, "비고(선택)")
ENERGY_SUB = (None, None, None, None, "연간사용량", *MONTHS, None)
C8_AREA_MAIN = ("*국가", "*임차자산명", "*건물전체 면적", "*사용 면적", "*에너지종류", "*단위", "연간 입력", "건물전체 월별사용량",
                *_MONTH_GROUP, "비고(선택)")  # fmt: skip
C13_AREA_MAIN = ("*국가", "*임대자산명", "*건물전체 면적", "*사용 면적", "*에너지종류", "*단위", "연간 입력", "건물전체 월별사용량",
                 *_MONTH_GROUP, "비고(선택)")  # fmt: skip
C8_PERSON_MAIN = ("*국가", "*임차자산명", "*건물전체 인원수", "*자사 인원수", "*에너지종류", "*단위", "연간 입력",
                  "건물전체 월별사용량", *_MONTH_GROUP, "비고(선택)")  # fmt: skip
C13_PERSON_MAIN = ("*국가", "*임대자산명", "*건물전체 인원수", "*자사 인원수", "*에너지종류", "*단위", "연간 입력",
                   "건물전체 월별사용량", *_MONTH_GROUP, "비고(선택)")  # fmt: skip
SHARE_SUB = (None, None, None, None, None, None, "건물전체 연간사용량", *MONTHS, None)
C11_MAIN = ("*국가", "그룹(선택)", "*보고연도", "*제품명", "*제품수량", "*에너지명", "*제품당 연간 에너지 사용량", "*단위",
            "수명(개월) 혹은 평균 사용기간(개월)", "설명/적요(선택)")  # fmt: skip
C12_MAIN = ("*국가", "폐기물 대분류(선택)", "폐기물 중분류(선택)", "폐기물 소분류(선택)", "*제품명", "*판매량", "*제품중량",
            "*제품중량 단위", "재활용 비중(%)(선택)", "소각 비중(%)(선택)", "매립 비중(%)(선택)", "기타 비중(%)(선택)",
            "설명/적요(선택)")  # fmt: skip
C14_MAIN = ("*국가", "*프랜차이즈명", "*가맹점 수", "*연료 종류", "*연료 사용량", "*연료 단위", "비고(선택)")
C15_MAIN = ("*피투자기업명 (필수)", "지분율(%) (선택)", "*피투자기업 Scope1배출량", "*피투자기업 Scope2배출량", "비고(선택)")

# 줄마다 노린 갈래는 README 「나머지 판」 표. 같은 에너지명·단위(`가상와트`·전력)는 C3 에서 한 번 단위 AI 를 부르고 C8-1 에서
# `lru_cache`(`:3808`)로 다시 부르지 않는다.
C3_ROWS: tuple[tuple[Cell, ...], ...] = (
    # 1 입력 단위 = 계수 기준 단위(kWh): 환산 0
    _annual(("가상국", "가상사업장A", "전력", "kWh"), 9100),
    # 2 월별 입력(경유 L)
    _monthly(("가상국", "가상사업장A", "경유", "L"), {1: 910, 2: 920}),
    # 3 단위가 사전에 없음(`가상와트`) → 단위 AI(`_ai_unit_conversion_factor` `:3809`, kWh 갈래 `:3896`)
    _annual(("가상국", "가상사업장B", "전력", "가상와트"), 99),
    # 4 계수 책에 `도시가스(LNG)` 가 없어 `천연가스(LNG)`(kg)로 대체 매칭 + 입력 Nm³ → 발열량 다리
    _annual(("가상국", "가상사업장B", "도시가스", "Nm³"), 9300),
)
C8_ENERGY_ROWS: tuple[tuple[Cell, ...], ...] = (
    # 1 에너지 사용량을 앎(연간, kWh)
    _annual(("가상국", "가상임차자산A", "전력", "kWh"), 91000),
    # 2 월별(도시가스 Nm³ — 단위 우선 별칭 `:8240`)
    _monthly(("가상국", "가상임차자산A", "도시가스(LNG)", "Nm³"), {3: 930, 4: 940}),
    # 3 C3 3번과 같은 에너지명·단위 → 단위 AI 캐시(다시 부르지 않음)
    _annual(("가상국", "가상임차자산B", "전력", "가상와트"), 99),
)
C8_AREA_ROWS: tuple[tuple[Cell, ...], ...] = (
    # 1 면적 배분(연간, 경유 L)
    _annual(("가상국", "가상임차자산A", 9000, 900, "경유", "L"), 9100),
    # 2 면적 배분(월별, 전력 kWh)
    _monthly(("가상국", "가상임차자산B", 9500, 950, "전력", "kWh"), {1: 9100, 2: 9200}),
)
C8_PERSON_ROWS: tuple[tuple[Cell, ...], ...] = (
    # 1 인원 배분(연간, 전력 kWh)
    _annual(("가상국", "가상임차자산A", 90, 9, "전력", "kWh"), 99000),
)
C11_ROWS: tuple[tuple[Cell, ...], ...] = (
    # 1 수명(개월) 있음, 단위 = 계수 단위
    ("가상국", "가상그룹A", None, "가상제품A", 900, "전력", 9.1, "kWh", 96, "가상 적요 1"),
    # 2 수명 빔(= 1년), 단위가 계수 단위와 다름(MWh → kWh)
    ("가상국", None, None, "가상제품B", 950, "전력", 0.92, "MWh", None, "가상 적요 2"),
    # 3 경유(L)
    ("가상국", "가상그룹A", None, "가상제품C", 99, "경유", 9.3, "L", 90, None),
)
C12_ROWS: tuple[tuple[Cell, ...], ...] = (
    # 1 소분류 코드로 맞음 + 생물기원 코드(91-02 폐지류) + 처리비중 셋 다 있음
    ("가상국", "91 | 가상 생활", "91-02 | 가상 종이", "91-02-01 | 가상 폐지 묶음", "가상종이상자", 9100, 0.9, "kg", 90, 9, 9, None, "가상 적요 1"),
    # 2 소분류 「선택안함」 → 중분류로 내려감(생물기원 아님)
    ("가상국", "92 | 가상 사업장", "92-01 | 가상 수지", "선택안함", "가상수지용기", 9200, 0.95, "kg", 9, 9, 90, None, None),
    # 3 중·소분류 「선택안함」 → 대분류로 내려감, 톤 단위
    ("가상국", "93 | 가상 건설", "선택안함", "선택안함", "가상잔재판", 93, 0.009, "ton", 9, 9, 90, None, None),
    # 4 코드 셋 다 「선택안함」 → 제품명으로 C5 계수 찾기(`lookup_waste_ef` — 폐기물_C5 의 `가상폐지` 줄), 이름으로 생물기원(폐지류 `:375`)
    ("가상국", "선택안함", "선택안함", "선택안함", "가상폐지", 940, 0.96, "kg", 9, 9, 90, None, None),
    # 5 생물기원 코드인데 처리비중이 모두 빔, g 단위. 코랩은 빈 칸(NaN)을 None 으로 보지 않아 평균 EF 갈래(`:8998`)로 가지 않고
    #   배출계수·배출량이 비고 생물기원 칸도 빈다(코랩 결함 후보 — 고치지 않음)
    ("가상국", "91 | 가상 생활", "91-02 | 가상 종이", "91-02-01 | 가상 폐지 묶음", "가상종이봉투", 950, 0.99, "g", None, None, None, None, None),
    # 6 생물기원 코드 + 소각 비중은 있고 매립 비중만 빔 → 배출계수·배출량은 비고 생물기원만 남음(같은 결함)
    ("가상국", "91 | 가상 생활", "91-02 | 가상 종이", "91-02-01 | 가상 폐지 묶음", "가상종이컵", 960, 0.9, "kg", 90, 9, None, None, None),
    # 7 대분류만 있고 중·소분류 칸이 빔 → 코랩은 빈 칸을 글 「nan」 으로 읽어(`_c12_clean_code` `:8941`) 소분류 「nan」 을 찾다 못 찾고
    #   평균 EF(`WASTE_AVG_EF`)로 떨어진다 — 대분류로 내려가지 않음(같은 결함)
    ("가상국", "93 | 가상 건설", None, None, "가상잔재블록", 97, 0.9, "kg", 9, 9, 90, None, None),
)
C13_ENERGY_ROWS: tuple[tuple[Cell, ...], ...] = (
    _annual(("가상국", "가상임대자산A", "경유", "L"), 9400),
)
C13_AREA_ROWS: tuple[tuple[Cell, ...], ...] = (
    _annual(("가상국", "가상임대자산A", 9000, 900, "전력", "kWh"), 94000),
)
C13_PERSON_ROWS: tuple[tuple[Cell, ...], ...] = (
    _monthly(("가상국", "가상임대자산B", 90, 9, "천연가스(LNG)", "kg"), {6: 960, 7: 970}),
)
C14_ROWS: tuple[tuple[Cell, ...], ...] = (
    ("가상국", "가상가맹A", 9, "경유", 9100, "L", "가상 비고 1"),
    ("가상국", "가상가맹B", 95, "전력", 92000, "kWh", None),
)
C15_ROWS: tuple[tuple[Cell, ...], ...] = (
    # 1 지분율·Scope1·Scope2 다 있음
    ("가상투자처A", 9.5, 910, 920, "가상 비고 1"),
    # 2 Scope2 빔 — 표준화는 「빈 것은 0」(`:5634`)을 노리나 빈 칸은 NaN 이라 `or 0` 을 지나 배출량이 빈다(코랩 결함 후보)
    ("가상투자처B", 90, 930, None, None),
)
# (시트, 주 머리, 서브 머리 — None 이면 한 줄 머리, 줄). 받은 템플릿 차례 — 코랩은 이 차례로 돌고 AI 부름 차례도 이것을 따른다.
REST_TEMPLATE: tuple[tuple[str, tuple[Cell, ...], tuple[Cell, ...] | None, tuple[tuple[Cell, ...], ...]], ...] = (
    (C3_SHEET, C3_MAIN, C3_SUB, C3_ROWS),
    (C8_ENERGY_SHEET, C8_ENERGY_MAIN, ENERGY_SUB, C8_ENERGY_ROWS),
    (C8_AREA_SHEET, C8_AREA_MAIN, SHARE_SUB, C8_AREA_ROWS),
    (C8_PERSON_SHEET, C8_PERSON_MAIN, SHARE_SUB, C8_PERSON_ROWS),
    (C11_SHEET, C11_MAIN, None, C11_ROWS),
    (C12_SHEET, C12_MAIN, None, C12_ROWS),
    (C13_ENERGY_SHEET, C13_ENERGY_MAIN, ENERGY_SUB, C13_ENERGY_ROWS),
    (C13_AREA_SHEET, C13_AREA_MAIN, SHARE_SUB, C13_AREA_ROWS),
    (C13_PERSON_SHEET, C13_PERSON_MAIN, SHARE_SUB, C13_PERSON_ROWS),
    (C14_SHEET, C14_MAIN, None, C14_ROWS),
    (C15_SHEET, C15_MAIN, None, C15_ROWS),
)

REST_FACTOR_BOOK_DIR = "factors-rest"
REST_TEMPLATE_NAME = "synthetic_scope3_rest_template.xlsx"


def _factor_sheet_at(wb: Workbook, title: str, header_row: int, headers: tuple[str, ...], rows: tuple[tuple[Cell, ...], ...]) -> None:
    """머리글이 0부터 `header_row` 째 줄인 계수 시트. 앞줄은 제목 하나와 안내."""
    ws = wb.create_sheet(title)
    ws.append(["가상 계수 책 — synthetic(지은 값, 출처 없음)"])
    for n in range(1, header_row):
        ws.append([f"가상 안내 {n}"])
    ws.append(list(headers))
    for row in rows:
        ws.append(list(row))


def build_rest_factor_book(path: Path) -> Path:
    """기본판 네 시트 + `에너지`(넷째 줄 머리글)·`에너지_C3`(셋째 줄)·`폐기물_C12`(둘째 줄)."""
    build_factor_book(path)
    wb = load_workbook(path)
    _factor_sheet_at(wb, "에너지", 3, ENERGY_HEADERS, ENERGY_ROWS)
    _factor_sheet_at(wb, "에너지_C3", 2, ENERGY_C3_HEADERS, ENERGY_C3_ROWS)
    _factor_sheet_at(wb, "폐기물_C12", 1, WASTE_C12_HEADERS, WASTE_C12_ROWS)
    wb.save(path)
    return path


def build_rest_template(path: Path) -> Path:
    """나머지 시트 열하나에만 줄. C1·C2·C4~C7·C9 시트는 두지 않는다."""
    wb = Workbook()
    default = wb.active
    assert default is not None
    wb.remove(default)
    for name, main, sub, rows in REST_TEMPLATE:
        if sub is not None:
            _template_sheet(wb, name, main, sub, rows)
            continue
        ws = wb.create_sheet(name)
        ws.append([f"카테고리 안내(가상) — {name}"])
        ws.append(list(main))
        for row in rows:
            ws.append(list(row))
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def build_rest_inputs(out_dir: Path) -> Inputs:
    """`<out_dir>/factors-rest/배출계수_통합_DB.xlsx` 와 `<out_dir>/synthetic_scope3_rest_template.xlsx`."""
    return Inputs(
        build_rest_factor_book(out_dir / REST_FACTOR_BOOK_DIR / FACTOR_BOOK_NAME),
        build_rest_template(out_dir / REST_TEMPLATE_NAME),
    )
