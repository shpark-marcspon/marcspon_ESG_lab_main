"""견줌의 줄 꼴(양쪽 공통 JSON)과 코랩 결과 파일에서 견주는 시트(C1~C9·C11~C15 — C10 은 코랩에 없음)의 줄 뽑기.

JSON 꼴(`scope3colab.rows/1`) — 엔진 쪽(`--engine-result`)도 이 꼴로 낸다:

    {"schema": "scope3colab.rows/1", "side": "colab" | "engine",
     "rows": [{"sheet": "C1", "row": 1, "item": "<품목 열의 글>", ["branch": "Distance",]
               "fields": {"<칸>": "<글>" | null, …}}, …],
     "not_compared": ["<견주지 않는 결과 시트 이름>", …]}

- 줄 열쇠는 (sheet, row, item). sheet 는 결과 시트 접두의 카테고리 열쇠(`SHEET_FIELDS` 의 키). 코랩은 같은 카테고리의 템플릿
  시트를 한 결과 시트로 합친다(C4-1+C4-2 → `C4_업스트림운송`, C6-1+C6-2, C9-1+C9-2 — 셀 22 `:6237-6246`). 그래서 row 는
  **합친 결과 시트 안의 차례**(1부터)다. item 은 그 결과 시트에서 `ITEM_COLUMNS[sheet]` 가운데 값이 하나라도 있는 첫 열의
  글이다(코랩은 쓰기 전 전부 빈 열을 지운다 — b1 리뷰 R7).
- 줄 갈래(S3C-o3): C4·C6·C9 결과 시트에는 거리 줄과 지출 줄이 섞인다. 코랩 결과의 `계산방식` 이 `Distance` 인 줄은
  `"branch": "Distance"` 를 달고 `BRANCH_FIELDS[(sheet, "Distance")]` 칸으로 견준다. `Spend`(또는 그 열이 없는 시트)는 branch 를
  싣지 않고 `SHEET_FIELDS[sheet]`(S3C-o2 표 그대로 — b1 dump 가 따름)로 견준다. 그래서 기본판 `colab.json` 은 o2 와 같다.
- 칸은 시트마다 `SHEET_FIELDS[sheet]`(거리 갈래는 `BRANCH_FIELDS`). 숫자 칸은 `Decimal` 글로 싣는다(float 는 `repr` 로 — 가장 짧은 되돌림 글). 견줌은
  `Decimal` 같음, 허용 오차 0.
- 코랩 쪽 숫자는 코랩 결과 파일(`result_file`)의 값이다. 코랩은 결과를 pandas → openpyxl 로 쓰고 openpyxl 은 수를
  `%.16g` 로 적는다 — 17자리가 있어야 되돌아오는 float 는 파일에서 잘린다(0.1 + 0.2 → `0.3`). 엔진 쪽이 메모리의
  float 를 그대로 내면 그 자리에서 끝자리 차이가 난다(시험 `test_cli_same_is_0_changed_cell_is_1`).
- 그 시트의 칸 밖은 싣지 않는다. 코랩 결과에 그 열이 없으면(예: 후보가 하나뿐인 시트의 `추천EF2`) null.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

SCHEMA = "scope3colab.rows/1"

type Field = tuple[str, str, bool]  # (JSON 칸, 코랩 결과 열, 숫자인가)

# C1·C2: 대표 계수·추천 4·tCO2eq·산정 출처(+ 계수를 곱한 금액).
FIELDS: tuple[Field, ...] = (
    ("구매금액(KRW)", "EF적용금액(KRW)", True),
    ("대표EF", "EF_DB", False),
    ("대표EF_배출계수", "배출계수", True),
    ("배출량(tCO2e)", "배출량(tCO2e)", True),
    ("산정출처", "EF_DB_추천사유", False),
    *(
        item
        for i in range(1, 5)
        for item in ((f"추천EF{i}", f"추천EF{i}", False), (f"추천EF{i}_배출계수", f"추천EF{i}_배출계수", True))
    ),
)
# C4·C9(운송 지출): 코랩 결과 열 이름 그대로. 계수 이름(`NAICS_Title`)·코드(`NAICS`)·`Spend방식` 과 USD 갈래의
# `EF(kgCO2e/USD)` 는 코랩이 `COLS_TO_DROP`(`:6223`)으로 지워 결과에 없다 — 「안 견줌」(README).
TRANSPORT_SPEND_FIELDS: tuple[Field, ...] = (
    ("운송금액", "운송금액", True),
    ("EF(kgCO2e/KRW)", "EF(kgCO2e/KRW)", True),
    ("배출계수단위", "배출계수단위", False),
    ("배출량(tCO2e)", "배출량(tCO2e)", True),
)
# C6(출장 지출): C6 후처리(`_fix_result_c6`)가 결과 파일을 다시 써 `NAICS` 가 남는다. 산정 근거 글은 `계산식`.
TRAVEL_SPEND_FIELDS: tuple[Field, ...] = (
    ("출장비용", "출장비용", True),
    ("EF_DB", "EF_DB", False),
    ("NAICS", "NAICS", False),
    ("배출계수", "배출계수", True),
    ("배출계수단위", "배출계수단위", False),
    ("배출량(tCO2e)", "배출량(tCO2e)", True),
    ("계산식", "계산식", False),
    ("정규화이동수단", "정규화이동수단", False),
)
# C5(사업장 폐기물): 처리량(kg 환산 전, 월별은 합)·계수·정규화 처리방법·생물기원 나눔 넷·배출량(= 화석기원)·단위 환산 글.
# 계수 이름은 코랩이 결과에 싣지 않는다(`lookup_waste_ef` 는 수만 돌려줌 `:653`).
WASTE_FIELDS: tuple[Field, ...] = (
    ("처리량", "처리량", True),
    ("배출계수(kgCO2eq/kg)", "배출계수(kgCO2eq/kg)", True),
    ("처리방법_정규화", "처리방법_정규화", False),
    ("화석기원 CO2e(tCO2e)", "화석기원 CO2e(tCO2e)", True),
    ("생물기원 CO2e(tCO2e)", "생물기원 CO2e(tCO2e)", True),
    ("화석탄소비율(FCF)", "화석탄소비율(FCF)", True),
    ("바이오매스 구분", "바이오매스 구분", False),
    ("배출량(tCO2e)", "배출량(tCO2e)", True),
    ("단위환산비고", "단위환산비고", False),
)
# C7(통근, S3C-o3): 결과 시트 `C7_통근`. 칸은 C7 후처리 `_fix_result_c7`(`:8845`)이 다시 쓴 것 — 거리(입력이 없으면 AI 길로 채운
# 값)·인원·근무일수(월별은 합)·왕복계수·정규화 이동수단·계수·배출량·산정 근거 글. 계수 단위 열은 결과에 없다.
COMMUTE_FIELDS: tuple[Field, ...] = (
    ("평균통근거리(km)", "평균통근거리(km)", True),
    ("직원수", "직원수", True),
    ("근무일수", "근무일수", True),
    ("거리왕복계수", "거리왕복계수", True),
    ("정규화이동수단", "정규화이동수단", False),
    ("배출계수", "배출계수", True),
    ("배출량(tCO2e)", "배출량(tCO2e)", True),
    ("계산식", "계산식", False),
)
# ── 나머지 일곱(S3C-o4a, `--input rest`) — 칸은 코랩 결과 열 이름 그대로(실측) ──
# C3(연료·에너지, 셀 22 본 고리 `:6070-6113`): 사용량(월별은 합, **단위 환산 전** — 본 고리는 환산한 값을 행에 다시 쓰지 않는다)·
# 계수(`에너지_C3` 시트 `lookup_energy_ef_c3` `:9113`)·계수 단위·계수 이름·환산 글(단위 AI·발열량 다리)·배출량.
ENERGY_C3_FIELDS: tuple[Field, ...] = (
    ("사용량", "사용량", True),
    ("배출계수(kgCO2eq/단위)", "배출계수(kgCO2eq/단위)", True),
    ("EF단위", "EF단위", False),
    ("EF매핑명", "EF매핑명", False),
    ("단위환산비고", "단위환산비고", False),
    ("배출량(tCO2e)", "배출량(tCO2e)", True),
)
# C8·C13(임차·임대자산, 본 고리 `:6146-6168`): 세 템플릿 시트(에너지·면적·인원)가 한 결과 시트로 합쳐진다. 사용량은 표준화(뒤 정의
# `:6901-6958`)가 접고 배분한 값(환산 전), 산정방식·배분 비율·건물전체사용량(월별은 합)도 표준화가 짓는다. 계수 이름 열은 결과에 없다.
LEASED_FIELDS: tuple[Field, ...] = (
    ("산정방식", "산정방식", False),
    ("건물전체사용량", "건물전체사용량", True),
    ("면적비율", "면적비율", True),
    ("인원비율", "인원비율", True),
    ("사용량", "사용량", True),
    ("배출계수(kgCO2eq/단위)", "배출계수(kgCO2eq/단위)", True),
    ("EF단위", "EF단위", False),
    ("단위환산비고", "단위환산비고", False),
    ("배출량(tCO2e)", "배출량(tCO2e)", True),
)
# C11(판매제품 사용): 셈은 표준화 안(본 고리 갈래 없음) — 결과 후처리(`:8898`)가 셀 27 정의 `standardize_c11_sheet`(`:8720`)로
# 결과 시트를 다시 셈한 값이 남는다.
PRODUCT_USE_FIELDS: tuple[Field, ...] = (
    ("사용기간(년)", "사용기간(년)", True),
    ("총사용량", "총사용량", True),
    ("배출계수(kgCO2eq/단위)", "배출계수(kgCO2eq/단위)", True),
    ("EF단위", "EF단위", False),
    ("EF매핑명", "EF매핑명", False),
    ("단위환산비고", "단위환산비고", False),
    ("배출량(tCO2e)", "배출량(tCO2e)", True),
)
# C12(판매제품 폐기, `calc_c12_ef_and_emission` `:8949`): 처리비중 가중 계수·생물기원 나눔 넷·배출량(= 화석기원).
PRODUCT_EOL_FIELDS: tuple[Field, ...] = (
    ("배출계수", "배출계수", True),
    ("화석기원 CO2e(tCO2e)", "화석기원 CO2e(tCO2e)", True),
    ("생물기원 CO2e(tCO2e)", "생물기원 CO2e(tCO2e)", True),
    ("화석탄소비율(FCF)", "화석탄소비율(FCF)", True),
    ("바이오매스 구분", "바이오매스 구분", False),
    ("배출량(tCO2e)", "배출량(tCO2e)", True),
)
# C14(프랜차이즈): 본 고리 갈래는 없으나 결과 후처리(`_postprocess_scope3_result_file` `:8896-8897` → `_fix_result_energy_sheet`
# `:8860`)가 결과 시트를 다시 셈한다(발주서 초안 예상과 다름 — 실측).
FRANCHISE_FIELDS: tuple[Field, ...] = (
    ("사용량", "사용량", True),
    ("배출계수(kgCO2eq/단위)", "배출계수(kgCO2eq/단위)", True),
    ("EF단위", "EF단위", False),
    ("EF매핑명", "EF매핑명", False),
    ("단위환산비고", "단위환산비고", False),
    ("배출량(tCO2e)", "배출량(tCO2e)", True),
    ("계산식", "계산식", False),
)
# C15(투자): 본 고리 `pass`(`:6190`), 표준화(`:5610`)가 짓는 `배출량`(단위 없는 이름) 하나뿐.
INVESTMENT_FIELDS: tuple[Field, ...] = (("배출량", "배출량", True),)
SHEET_FIELDS: dict[str, tuple[Field, ...]] = {
    "C1": FIELDS,
    "C2": FIELDS,
    "C4": TRANSPORT_SPEND_FIELDS,
    "C5": WASTE_FIELDS,
    "C6": TRAVEL_SPEND_FIELDS,
    "C7": COMMUTE_FIELDS,
    "C9": TRANSPORT_SPEND_FIELDS,
    "C3": ENERGY_C3_FIELDS,
    "C8": LEASED_FIELDS,
    "C11": PRODUCT_USE_FIELDS,
    "C12": PRODUCT_EOL_FIELDS,
    "C13": LEASED_FIELDS,
    "C14": FRANCHISE_FIELDS,
    "C15": INVESTMENT_FIELDS,
}
# C4·C9 거리 갈래(C4-1·C9-1, 셀 22 본 고리 `:6008-6018` + `calc_transport` `:6628`): 운송량(월별은 합, 단위 환산 전)·화물중량
# (kg 환산 뒤)·거리·계수·배출량. 거리는 입력 칸이거나 본 고리 `get_distance_km` 이 채운 값이다 — 본 고리 길이 None 이면
# `calc_transport` 가 `infer_distance` 로 다시 구해 배출량은 내지만 그 거리를 결과에 적지 않아 빈칸이다(실측, C9-1 1·2번).
# 정규화 운송수단·계수 단위·산정 근거 글은 C4·C9 결과에 없다(운송수단은 계수 값으로만 드러남).
TRANSPORT_DISTANCE_FIELDS: tuple[Field, ...] = (
    ("월운송량", "월운송량", True),
    ("화물중량(kg)", "화물중량(kg)", True),
    ("운송거리(km)", "운송거리(km)", True),
    ("배출계수(거리기반)", "배출계수(거리기반)", True),
    ("배출량(tCO2e)", "배출량(tCO2e)", True),
)
# C6 거리 갈래(C6-1): C6 후처리 `_fix_result_c6`(`:8821`)의 Distance 갈래가 다시 쓴 것. 계수 단위 열은 결과에 없다.
TRAVEL_DISTANCE_FIELDS: tuple[Field, ...] = (
    ("이동거리(km)", "이동거리(km)", True),
    ("출장인원", "출장인원", True),
    ("이동횟수", "이동횟수", True),
    ("거리왕복계수", "거리왕복계수", True),
    ("정규화이동수단", "정규화이동수단", False),
    ("배출계수", "배출계수", True),
    ("배출량(tCO2e)", "배출량(tCO2e)", True),
    ("계산식", "계산식", False),
)
BRANCH_COLUMN = "계산방식"
DISTANCE = "Distance"
SPEND = "Spend"
# (열쇠, 갈래) → 칸. 이 표에 없는 갈래(Spend·갈래 없음)는 `SHEET_FIELDS[열쇠]`.
BRANCH_FIELDS: dict[tuple[str, str], tuple[Field, ...]] = {
    ("C4", DISTANCE): TRANSPORT_DISTANCE_FIELDS,
    ("C6", DISTANCE): TRAVEL_DISTANCE_FIELDS,
    ("C9", DISTANCE): TRANSPORT_DISTANCE_FIELDS,
}
BRANCH_SHEETS = frozenset(sheet for sheet, _ in BRANCH_FIELDS)
NUMERIC_FIELDS: dict[str, frozenset[str]] = {
    sheet: frozenset(name for name, _, numeric in fields if numeric) for sheet, fields in SHEET_FIELDS.items()
}
BRANCH_NUMERIC_FIELDS: dict[tuple[str, str], frozenset[str]] = {
    key: frozenset(name for name, _, numeric in fields if numeric) for key, fields in BRANCH_FIELDS.items()
}
# 결과 시트 접두 → 줄 열쇠의 sheet(`C1_구매품서비스`·`C4_업스트림운송`·`C5_사업장폐기물`·`C6_출장`·`C9_다운스트림운송` …).
COMPARED_SHEETS = {f"{sheet}_": sheet for sheet in SHEET_FIELDS}
# 품목 열 후보(앞에서부터, 값이 하나라도 있는 첫 열). C1·C2: 코랩은 표준화에서 「상세 품목명/서비스명」·「상세 자산명」을
# 「품목/자산명」으로 바꾸고(셀 23) 그 칸을 그룹명으로 덮지만, `calc_category1_2_detail`(셀 27)이 옛 값을 「품목명」(C1)·
# 「자산명」(C2)에 남긴다. C4: 「자재명/품목명」→「자재명」, C9: 「제품명」(둘 다 `standardize_c4_spend_sheet` `:6701`이
# 바꿈). C6: 「출장항목」→「이동수단」(`:6734`). C5: 「폐기물 종류」(템플릿 그대로, 별표만 지움). 거리 줄도 같은 열에 든다
# (C4-1 「자재명/품목명」→「자재명」 `:6668`, C9-1 「제품명」, C6-1 「교통수단」→「이동수단」 `:5239`). C7: 「교통수단」→「이동수단」
# (`:5319`, `:5333` 이 「교통수단」 열을 다시 지어 둘 다 남음 — 실측).
ITEM_COLUMNS: dict[str, tuple[str, ...]] = {
    "C1": ("품목명", "자산명"),
    "C2": ("품목명", "자산명"),
    "C4": ("자재명", "제품명"),
    "C9": ("제품명", "자재명"),
    "C6": ("이동수단", "출장수단"),
    "C5": ("폐기물 종류", "상세폐기물명"),
    "C7": ("이동수단", "교통수단"),
    # S3C-o4a(실측): C3·C8·C13 「연료/에너지명」·「에너지종류」→「에너지원」(표준화 rename), C11·C12 「제품명」, C14 「프랜차이즈명」,
    # C15 「피투자기업명 (필수)」→「피투자기업명」.
    "C3": ("에너지원", "연료/에너지명"),
    "C8": ("에너지원", "에너지종류"),
    "C11": ("제품명", "에너지명"),
    "C12": ("제품명",),
    "C13": ("에너지원", "에너지종류"),
    "C14": ("프랜차이즈명",),
    "C15": ("피투자기업명",),
}


class RowsFormatError(ValueError):
    pass


@dataclass(frozen=True)
class Row:
    sheet: str
    row: int
    item: str
    fields: dict[str, str | None]
    branch: str | None = None  # 거리 갈래면 "Distance". None 은 그 열쇠의 기본 표(C4·C6·C9 는 Spend)

    @property
    def key(self) -> tuple[str, int, str]:
        return (self.sheet, self.row, self.item)


@dataclass(frozen=True)
class Rows:
    side: str
    rows: tuple[Row, ...]
    not_compared: tuple[str, ...]

    def __post_init__(self) -> None:
        # 견줌은 열쇠로 줄을 맞춘다 — 겹친 열쇠는 한 줄이 다른 줄을 조용히 덮어 차이를 숨기니 꼴 어긋남으로 막는다
        seen: set[tuple[str, int, str]] = set()
        for r in self.rows:
            if r.key in seen:
                raise RowsFormatError(f"{self.side}: 줄 열쇠 {r.key} 가 둘 이상이다")
            seen.add(r.key)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "side": self.side,
            "rows": [
                {"sheet": r.sheet, "row": r.row, "item": r.item, **({"branch": r.branch} if r.branch else {}), "fields": r.fields}
                for r in self.rows
            ],
            "not_compared": list(self.not_compared),
        }


def number_text(value: Any) -> str | None:
    """숫자를 `Decimal` 글로. 빈 값(None·NaN·빈 글)은 None. 숫자가 아니면 `RowsFormatError`."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise RowsFormatError(f"숫자 칸에 참거짓 {value!r}")
    if isinstance(value, float):
        if value != value:  # NaN
            return None
        return str(Decimal(repr(value)))
    if isinstance(value, int | Decimal):
        return str(Decimal(value))
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() == "nan":
            return None
        try:
            return str(Decimal(text))
        except InvalidOperation as error:
            raise RowsFormatError(f"숫자 칸에 숫자가 아닌 글 {value!r}") from error
    raise RowsFormatError(f"숫자 칸에 알 수 없는 값 {value!r}")


def text_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    text = str(value)
    return text if text.strip() else None


def _compared_sheet(title: str) -> str | None:
    return next((short for prefix, short in COMPARED_SHEETS.items() if title.startswith(prefix)), None)


def fields_of(sheet: str, branch: str | None = None) -> tuple[Field, ...]:
    if branch is not None:
        try:
            return BRANCH_FIELDS[(sheet, branch)]
        except KeyError as error:
            known = " · ".join(f"{s}/{b}" for s, b in BRANCH_FIELDS)
            raise RowsFormatError(f"{sheet}: 모르는 갈래 {branch!r} — {known} 가운데 하나이거나 없어야 한다") from error
    try:
        return SHEET_FIELDS[sheet]
    except KeyError as error:
        raise RowsFormatError(f"견주지 않는 시트 열쇠 {sheet!r} — {' · '.join(SHEET_FIELDS)} 가운데 하나여야 한다") from error


def numeric_fields(sheet: str, branch: str | None = None) -> frozenset[str]:
    return BRANCH_NUMERIC_FIELDS[(sheet, branch)] if branch is not None else NUMERIC_FIELDS[sheet]


def branch_of(sheet: str, value: Any) -> str | None:
    """코랩 결과의 `계산방식` 글 → 갈래. 갈래를 나누는 열쇠(C4·C6·C9)에서 `Distance` 만 갈래를 달고, `Spend`·빈 칸은 기본 표.
    그 밖의 글은 `RowsFormatError`(모르는 산정 방식을 기본 표로 조용히 견주지 않는다)."""
    if sheet not in BRANCH_SHEETS:
        return None
    text = text_value(value)
    if text is None or text.strip() == SPEND:
        return None
    if text.strip() == DISTANCE:
        return DISTANCE
    raise RowsFormatError(f"{sheet}: 모르는 {BRANCH_COLUMN} {text!r} — {DISTANCE} 또는 {SPEND} 여야 한다")


def item_column(sheet: str, header: list[str], body: list[tuple[Any, ...]]) -> int:
    """`ITEM_COLUMNS[sheet]` 가운데 결과에 있고 값이 하나라도 든 첫 열의 자리. 없으면 `RowsFormatError`."""
    index = {h: i for i, h in enumerate(header) if h}
    for name in ITEM_COLUMNS[sheet]:
        col = index.get(name)
        if col is not None and any(col < len(r) and text_value(r[col]) is not None for r in body):
            return col
    raise RowsFormatError(f"{sheet}: 값이 든 품목 열({' / '.join(ITEM_COLUMNS[sheet])})이 없다")


def rows_from_result_xlsx(path: Path) -> Rows:
    """코랩의 `result_file`(진단용 상세 결과)에서 견주는 시트의 줄을 뽑는다. 다른 시트는 이름만 「안 견줌」에 센다."""
    wb = load_workbook(path, read_only=True, data_only=True)
    rows: list[Row] = []
    not_compared: list[str] = []
    try:
        for ws in wb.worksheets:
            short = _compared_sheet(ws.title)
            if short is None:
                not_compared.append(ws.title)
                continue
            values = list(ws.iter_rows(values_only=True))
            if len(values) < 2:
                continue
            header = [str(h) if h is not None else "" for h in values[0]]
            index = {h: i for i, h in enumerate(header) if h}
            body = values[1:]
            item_col = item_column(short, header, body)
            branch_col = index.get(BRANCH_COLUMN)
            for n, raw in enumerate(body, start=1):
                branch = branch_of(short, raw[branch_col] if branch_col is not None and branch_col < len(raw) else None)
                fields: dict[str, str | None] = {}
                for name, column, numeric in fields_of(short, branch):
                    cell = raw[index[column]] if column in index and index[column] < len(raw) else None
                    fields[name] = number_text(cell) if numeric else text_value(cell)
                item = raw[item_col] if item_col < len(raw) else None
                rows.append(Row(short, n, text_value(item) or "", fields, branch))
    finally:
        wb.close()
    return Rows("colab", tuple(rows), tuple(not_compared))


def rows_from_json(data: Any, side: str) -> Rows:
    """엔진 쪽(또는 저장한 코랩 쪽) JSON 을 읽는다. 꼴이 어긋나면 `RowsFormatError`."""
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise RowsFormatError(f"schema 가 {SCHEMA!r} 가 아니다")
    rows: list[Row] = []
    for i, r in enumerate(data.get("rows") or []):
        try:
            sheet, row, item, fields = r["sheet"], r["row"], r["item"], r["fields"]
        except (KeyError, TypeError) as error:
            raise RowsFormatError(f"rows[{i}]: sheet·row·item·fields 가 다 있어야 한다") from error
        if not isinstance(fields, dict):
            raise RowsFormatError(f"rows[{i}].fields 가 객체가 아니다")
        sheet = str(sheet)
        branch = r.get("branch")
        if branch is not None and not isinstance(branch, str):
            raise RowsFormatError(f"rows[{i}].branch 가 글이 아니다")
        normalized = {
            name: (number_text(fields.get(name)) if numeric else text_value(fields.get(name)))
            for name, _, numeric in fields_of(sheet, branch)
        }
        rows.append(Row(sheet, int(row), str(item), normalized, branch))
    return Rows(str(data.get("side") or side), tuple(rows), tuple(data.get("not_compared") or ()))
