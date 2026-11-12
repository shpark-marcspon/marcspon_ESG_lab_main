"""S3C-o4a: 나머지 판 입력·머리(§3-1), 새 열쇠 일곱(§3-2), C12 생물기원(§3-3), 나머지 판 답 차례(§3-4), v2.2 짓개(§3-5).

결과 엑셀은 코랩 결과 꼴을 흉내 낸 작은 지어낸 표다(openpyxl). 받은 코랩은 돌리지 않는다 — 읽기판 글은 §3-1 에서만 실행 때 뽑아
견준다(읽기판이 없으면 건너뜀).
"""
import json
import re
import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from scope3colab.cells import CodeCell, default_colab_md
from scope3colab.cli import INPUTS, REST_ANSWERS
from scope3colab.compare import compare, render_diff_md
from scope3colab.rows import ITEM_COLUMNS, SHEET_FIELDS, Rows, RowsFormatError, rows_from_json, rows_from_result_xlsx
from scope3colab.sandbox import AnswerMismatch, BlockedModuleUsed, Sandbox, load_answers, run_in_sandbox
from scope3colab.synthetic import (
    ENERGY_C3_HEADERS,
    ENERGY_C3_ROWS,
    ENERGY_HEADERS,
    ENERGY_ROWS,
    REST_TEMPLATE,
    WASTE_C12_HEADERS,
    WASTE_C12_ROWS,
    build_rest_inputs,
)
from scope3colab.synthetic_v22 import (
    COMPANY_SHEET,
    TRAVEL_MAP_SHEET,
    WASTE_MAP_SHEET,
    Builder,
    CachedValueError,
    build_base_v22_inputs,
    build_distance_v22_inputs,
    build_distance_v22map_inputs,
    write_cached_values,
)

NEW_KEYS = ("C3", "C8", "C11", "C12", "C13", "C14", "C15")
REST_SHEETS = [name for name, _, _, _ in REST_TEMPLATE]


def _colab_text() -> str:
    try:
        path = default_colab_md()
    except FileNotFoundError:
        pytest.skip("받은 읽기판이 없는 자리(received-derived/ 밖)")
    if not path.is_file():
        pytest.skip(f"받은 읽기판이 없다: {path}")
    return path.read_text("utf-8")


# ── §3-1 나머지 판 입력 ──
def test_rest_template_sheets_in_received_order_with_rows(tmp_path):
    wb = load_workbook(build_rest_inputs(tmp_path).template, read_only=True)
    try:
        assert wb.sheetnames == REST_SHEETS
        for name, _, sub, rows in REST_TEMPLATE:
            head = 3 if sub is not None else 2  # 안내 줄 + 주 머리(+ 서브 머리)
            assert len(list(wb[name].iter_rows())) - head == len(rows), name
    finally:
        wb.close()


def test_rest_factor_book_adds_energy_energy_c3_and_waste_c12(tmp_path):
    wb = load_workbook(build_rest_inputs(tmp_path).factor_book, read_only=True)
    try:
        assert wb.sheetnames == ["구매_USEPA", "운송_Spend", "출장_Spend", "폐기물_C5", "에너지", "에너지_C3", "폐기물_C12"]
        got = {
            title: list(wb[title].iter_rows(min_row=n + 1, max_row=n + 1, values_only=True))[0]
            for title, n in (("에너지", 3), ("에너지_C3", 2), ("폐기물_C12", 1))
        }
    finally:
        wb.close()
    assert got == {"에너지": ENERGY_HEADERS, "에너지_C3": ENERGY_C3_HEADERS, "폐기물_C12": WASTE_C12_HEADERS}


def _strings(path: Path, skip_rows: int = 0) -> list[str]:
    wb = load_workbook(path, read_only=True)
    try:
        return [v for ws in wb.worksheets for n, row in enumerate(ws.iter_rows(values_only=True), start=1) if n > skip_rows
                for v in row if isinstance(v, str)]  # fmt: skip
    finally:
        wb.close()


# 코랩 규칙이 읽는 분류어(에너지원·단위·코드 선택지)는 이름 검사에서 뺀다(C5 의 처리방법·단위와 같은 취급).
CLASS_WORDS = frozenset({"전력", "경유", "도시가스", "도시가스(LNG)", "천연가스(LNG)", "kWh", "L", "Nm³", "kg", "g", "ton", "MWh",
                         "선택안함"})  # fmt: skip


def test_rest_names_and_numbers_are_synthetic(tmp_path):
    inputs = build_rest_inputs(tmp_path)
    data = [v for _, _, _, rows in REST_TEMPLATE for row in rows for v in row if v is not None]
    data += [v for r in (*ENERGY_ROWS, *ENERGY_C3_ROWS, *WASTE_C12_ROWS) for v in r if v is not None]
    names = [v for v in data if isinstance(v, str) and v not in CLASS_WORDS]
    numbers = [v for v in data if isinstance(v, int | float)]
    assert len(names) >= 60 and len(numbers) >= 60  # 분모: 실제로 본다
    assert [v for v in names if not v.startswith(("가상", "Synthetic", "9"))] == []  # 코드 글은 9x 꼴
    assert [n for n in numbers if not repr(n).lstrip("-0.").startswith("9")] == []
    assert {v for v in _strings(inputs.template) if v.startswith("C")} == set()  # 안내 줄 밖에 시트 이름 따위가 새지 않음


def test_rest_headers_match_the_colab_reading_copy(tmp_path):
    """시트 이름은 `SHEET_PROCESSOR_MAP` 키(소문자), 머리글 낱말은 코랩 글자(`KNOWN_TEMPLATE_HEADERS`·rename 표·`ANNUAL_SET`·
    `GROUP_HEADERS`) 그대로, 계수 책 셋은 첫 읽기 header_row 와 재읽기 낱말을 다 지난다."""
    text = _colab_text()
    keys = set(re.findall(r'^\s*"([^"]+)":\s*\(standardize_', text, re.M))
    assert {n.lower() for n in REST_SHEETS} <= keys
    for name, main, sub, _ in REST_TEMPLATE:
        for h in (*main, *(sub or ())):
            if h is None or h in {f"{i}월" for i in range(1, 13)}:
                continue
            assert f'"{h.lstrip("*")}"' in text, (name, h)
    first_read = dict(re.findall(r'_load_ef_sheet\("([^"]+)",\s*header_row=(\d+)', text))
    must = {n: re.findall(r'"([^"]+)"', words)
            for n, words in re.findall(r'_ef_reload_sheet_auto\("([^"]+)", must_contain=\[([^\]]*)\]', text)}  # fmt: skip
    assert (first_read["에너지"], first_read["에너지_C3"], first_read["폐기물_C12"]) == ("3", "2", "1")
    for sheet, headers in (("에너지", ENERGY_HEADERS), ("폐기물_C12", WASTE_C12_HEADERS)):
        joined = " ".join(headers)
        assert must[sheet] and all(w in joined for w in must[sheet]), sheet
    assert "에너지_C3" not in must  # C3 시트는 다시 읽지 않는다
    str_cols = re.search(r'_load_ef_sheet\("폐기물_C12".*?str_cols=\[([^\]]*)\]', text, re.S)
    assert str_cols and set(re.findall(r'"([^"]+)"', str_cols.group(1).replace("\\n", "\n"))) <= set(WASTE_C12_HEADERS)
    # 생물기원 접두(셀 8)가 우리 폐지 코드를 덮는다
    assert '("91-02", "폐지류")' in text


# ── §3-2 새 열쇠 일곱 ──
def _xlsx(path: Path, sheets: dict[str, list[list[object]]]) -> Path:
    wb = Workbook()
    default = wb.active
    assert default is not None
    wb.remove(default)
    for title, values in sheets.items():
        ws = wb.create_sheet(title)
        for v in values:
            ws.append(v)
    wb.save(path)
    return path


def _sheet(item: str, key: str, items: list[str]) -> list[list[object]]:
    """열쇠의 칸을 다 가진 결과 시트(칸 값은 지은 9 꼴)."""
    fields = SHEET_FIELDS[key]
    header = [item, *(column for _, column, _ in fields)]
    body = [[name, *((9.1 + i if numeric else f"가상 {column} {i}") for _, column, numeric in fields)] for i, name in enumerate(items)]
    return [header, *body]


RESULT = {
    "C3_연료에너지": _sheet("에너지원", "C3", ["경유", "전력"]),
    "C8_임차자산": _sheet("에너지원", "C8", ["전력", "경유", "전력"]),  # 세 템플릿 시트(에너지·면적·인원)를 이은 꼴
    "C11_판매제품사용": _sheet("제품명", "C11", ["가상제품A"]),
    "C12_판매제품폐기": _sheet("제품명", "C12", ["가상종이상자", "가상수지용기"]),
    "C13_임대자산": _sheet("에너지원", "C13", ["경유", "전력", "천연가스(LNG)"]),
    "C14_프랜차이즈": _sheet("프랜차이즈명", "C14", ["가상가맹A"]),
    "C15_투자": _sheet("피투자기업명", "C15", ["가상투자처A", "가상투자처B"]),
}


def test_new_keys_rows_items_and_merged_counting(tmp_path):
    colab = rows_from_result_xlsx(_xlsx(tmp_path / "r.xlsx", RESULT))
    assert colab.not_compared == ()
    assert [r.key for r in colab.rows if r.sheet == "C8"] == [("C8", 1, "전력"), ("C8", 2, "경유"), ("C8", 3, "전력")]
    assert [r.key for r in colab.rows if r.sheet == "C13"] == [("C13", 1, "경유"), ("C13", 2, "전력"), ("C13", 3, "천연가스(LNG)")]
    assert {r.sheet for r in colab.rows} == set(NEW_KEYS)
    for r in colab.rows:
        assert list(r.fields) == [name for name, _, _ in SHEET_FIELDS[r.sheet]]
    assert {k: ITEM_COLUMNS[k][0] for k in NEW_KEYS} == {
        "C3": "에너지원", "C8": "에너지원", "C11": "제품명", "C12": "제품명", "C13": "에너지원", "C14": "프랜차이즈명",
        "C15": "피투자기업명"}  # fmt: skip
    # C1 접두가 C11~C15 결과 시트를 잡지 않는다
    assert not any(r.sheet in ("C1", "C2") for r in colab.rows)


@pytest.mark.parametrize("key", NEW_KEYS)
def test_new_key_same_changed_missing(tmp_path, key):
    colab = rows_from_result_xlsx(_xlsx(tmp_path / "c.xlsx", RESULT))
    engine = rows_from_json({**colab.to_json(), "side": "engine"}, "engine")
    assert compare(colab, engine) == []  # 같음 → 0
    name, _, numeric = SHEET_FIELDS[key][-1]
    data = engine.to_json()
    target = next(r for r in data["rows"] if r["sheet"] == key)
    target["fields"][name] = "9.9999" if numeric else "가상 다른 글"
    diffs = compare(colab, rows_from_json(data, "engine"))
    assert [(d.key, d.field) for d in diffs] == [((key, target["row"], target["item"]), name)]  # 칸 하나 → 차이 1
    without = Rows("engine", tuple(r for r in engine.rows if r.sheet != key), ())
    diffs = compare(colab, without)
    assert {d.field for d in diffs} == {"(줄)"} and {d.key[0] for d in diffs} == {key}  # 엔진 쪽 줄 없음
    assert f"- 엔진 쪽 줄 없음(열쇠): {key}" in render_diff_md(colab, without, diffs, {})


def test_unknown_key_is_format_error():
    with pytest.raises(RowsFormatError, match="견주지 않는 시트 열쇠"):
        rows_from_json({"schema": "scope3colab.rows/1", "rows": [{"sheet": "C10", "row": 1, "item": "가상", "fields": {}}]}, "engine")


# ── §3-3 C12 생물기원 ──
def test_c12_biogenic_cell_change_is_one_diff(tmp_path):
    header = ["제품명", "배출계수", "배출량(tCO2e)", "화석기원 CO2e(tCO2e)", "생물기원 CO2e(tCO2e)", "화석탄소비율(FCF)", "바이오매스 구분"]
    values = [header, ["가상종이상자", 0.25974, 1.425271302, 1.425271302, 0.701999298, 0.01, "가상 구분"]]
    colab = rows_from_result_xlsx(_xlsx(tmp_path / "c.xlsx", {"C12_판매제품폐기": values}))
    changed = [header, [*values[1][:4], 0.7019993, *values[1][5:]]]
    engine = rows_from_result_xlsx(_xlsx(tmp_path / "e.xlsx", {"C12_판매제품폐기": changed}))
    diffs = compare(colab, engine)
    assert [(d.field, d.colab, d.engine) for d in diffs] == [("생물기원 CO2e(tCO2e)", "0.701999298", "0.7019993")]


# ── §3-4 나머지 판 답 차례 ──
ASK = (
    "from anthropic import Anthropic\n"
    "client = Anthropic()\n"
    "def ask(p):\n"
    "    return client.messages.create(model='가상', max_tokens=9, messages=[{'role': 'user', 'content': p}]).content[0].text\n"
)
GEO = "from geopy.geocoders import Nominatim\ngeolocator = Nominatim(user_agent='가상')\n"


def _cells(*sources: str) -> list[CodeCell]:
    return [CodeCell(i, src) for i, src in enumerate(sources, start=1)]


def test_rest_answers_are_synthetic_json_with_expect():
    data = json.loads(REST_ANSWERS.read_text("utf-8"))
    assert data["synthetic"] is True
    assert [a["expect"] for a in data["answers"]] == ["입력 단위: 가상와트"]
    assert all(isinstance(json.loads(a["text"]), dict) for a in data["answers"])  # 올바른 JSON(`_call_claude_json`)


def test_rest_answers_mismatch_or_exhausted_stop_even_inside_bare_except(tmp_path):
    answers = load_answers(REST_ANSWERS)
    with pytest.raises(AnswerMismatch):
        run_in_sandbox(_cells(ASK, "ask('가상 다른 물음')\n"), Sandbox(answers), tmp_path, {})
    box = Sandbox(answers)
    code = "ask('입력 단위: 가상와트')\ntry:\n    ask('입력 단위: 가상와트')\nexcept:\n    pass\n"
    run_in_sandbox(_cells(ASK, code), box, tmp_path, {})
    assert box.aborts and box.aborts[0].startswith("AnswersExhausted")


def test_rest_input_stops_on_geocoder_and_geodesic(tmp_path):
    build, answers_path, geocode_none = INPUTS["rest"]
    assert (build, answers_path, geocode_none) == (build_rest_inputs, REST_ANSWERS, False)
    for code in ("geolocator.geocode('가상시가')\n", "from geopy.distance import geodesic\ngeodesic((0, 0), (9, 9))\n"):
        box = Sandbox([], geocode_none=geocode_none)
        with pytest.raises(BlockedModuleUsed):
            run_in_sandbox(_cells(GEO, code), box, tmp_path, {})
        assert box.aborts


# ── §3-5 v2.2 짓개 ──
V22 = {"base-v22": build_base_v22_inputs, "distance-v22": build_distance_v22_inputs, "distance-v22map": build_distance_v22map_inputs}
_FORMULA = re.compile(r'<c r="[A-Z]+[0-9]+"(?P<attrs>[^>]*)><f>[^<]*</f>(?P<rest><v\s*/>|<v>[^<]*</v>|<v/>)?</c>')


def _formula_cells(path: Path) -> list[tuple[str, str]]:
    with zipfile.ZipFile(path) as z:
        xml = [z.read(n).decode() for n in z.namelist() if n.startswith("xl/worksheets/sheet")]
    return [(m.group("attrs"), m.group("rest") or "") for x in xml for m in _FORMULA.finditer(x)]


@pytest.mark.parametrize("kind", ["base-v22", "distance-v22"])
def test_v22_cached_form_has_no_empty_formula_values(tmp_path, kind):
    cells = _formula_cells(V22[kind](tmp_path).template)
    assert len(cells) >= 100  # 분모
    assert [c for c in cells if ' t="' not in c[0] and re.fullmatch(r"<v\s*/>", c[1])] == []  # openpyxl 꼴 0
    assert [c for c in cells if c[1] == ""] == []  # 저장 값 칸이 다 있음
    assert any(' t="str"' in a and v == "<v/>" for a, v in cells)  # 빈 글 결과(받은 파일의 꼴)도 있음


def test_v22map_has_no_value_elements_in_formula_cells(tmp_path):
    cells = _formula_cells(build_distance_v22map_inputs(tmp_path).template)
    assert len(cells) >= 50
    assert [c for c in cells if c[1]] == []


@pytest.mark.parametrize("kind", list(V22))
def test_v22_builds_are_byte_identical(tmp_path, kind):
    a, b = V22[kind](tmp_path / "a").template, V22[kind](tmp_path / "b").template
    assert a.read_bytes() == b.read_bytes()


def test_v22_unconvertible_formula_cell_fails_the_build(tmp_path):
    path = tmp_path / "t.xlsx"
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "가상"
    ws["A1"] = "=9+9"
    ws["A2"] = "=9*9"
    wb.save(path)
    with pytest.raises(CachedValueError, match="남았다"):  # A2 의 저장 값을 모름
        write_cached_values(path, {("가상", "A1"): 18})
    with pytest.raises(CachedValueError, match="못 넣은"):  # 수식이 아닌 칸에 값을 넣으라 함
        write_cached_values(path, {("가상", "A1"): 18, ("가상", "A2"): 81, ("가상", "B9"): 9})
    write_cached_values(path, {("가상", "A1"): 18, ("가상", "A2"): 81})
    assert [v for _, v in _formula_cells(path)] == ["<v>18</v>", "<v>81</v>"]
    assert Builder  # 판 셋은 이 짓개로 짓는다(위 시험들)


@pytest.mark.parametrize("kind", list(V22))
def test_v22_sheet_order_is_received_form(tmp_path, kind):
    wb = load_workbook(V22[kind](tmp_path).template, read_only=True)
    try:
        names = wb.sheetnames
    finally:
        wb.close()
    assert names[0] == COMPANY_SHEET
    assert names[-2:] == [WASTE_MAP_SHEET, TRAVEL_MAP_SHEET]


@pytest.mark.parametrize("kind", list(V22))
def test_v22_company_names_are_synthetic_only(tmp_path, kind):
    inputs = V22[kind](tmp_path)
    text = "\n".join(s for s in _strings(inputs.template) if not s.startswith("="))  # 수식 글(시트 이름 참조)은 뺀다
    assert set(re.findall(r"[\w]*기업[A-Za-z0-9]+", text)) == {"가상기업A", "가상기업B"}  # 회사 이름 꼴은 둘뿐
    wb = load_workbook(inputs.template, read_only=True)
    try:
        companies = [r[0] for name in (WASTE_MAP_SHEET, TRAVEL_MAP_SHEET) for r in wb[name].iter_rows(min_row=2, values_only=True) if r[0]]
        companies += [r[4] for r in wb[COMPANY_SHEET].iter_rows(min_row=2, values_only=True) if len(r) > 4 and r[4]]
    finally:
        wb.close()
    assert companies and all(c.startswith("가상") for c in companies)
