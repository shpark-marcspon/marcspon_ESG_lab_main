"""표본 지킴이: 지어낸 입력과 짜 둔 답에 실제 이름·실제 수가 섞이지 않는다.

이름 칸(국가·사업장·품목·자산·거래처·계정·자산번호, 계수 책의 Title·상세 카테고리·키워드)은 `가상`(한글) 또는
`Synthetic`(NAICS Title 꼴)으로 시작하고, 수는 첫 유효 숫자가 `9` 다(S3C-a 표본의 「9 로 시작」 약속을 0.0009… 꼴 계수까지 넓힘). 품목군·자산분류는
코랩 규칙이 읽는 일반 분류어(원재료·포장재 …)라 이름 검사에서 뺀다. 머리글·안내 줄(1~3행)도 뺀다.
"""
import json
import re
from pathlib import Path

import pytest
from openpyxl import load_workbook

from scope3colab.cells import default_colab_md
from scope3colab.cli import DEFAULT_ANSWERS
from scope3colab.synthetic import (
    C1_ROWS,
    C1_SHEET,
    C2_ROWS,
    C2_SHEET,
    C4_SPEND_ROWS,
    C4_SPEND_SHEET,
    C5_ROWS,
    C5_SHEET,
    C6_SPEND_ROWS,
    C6_SPEND_SHEET,
    C9_SPEND_ROWS,
    C9_SPEND_SHEET,
    DISTANCE_SHEETS,
    SPEND_HEADERS,
    TRANSPORT_SPEND_ROWS,
    TRAVEL_SPEND_ROWS,
    USEPA_ROWS,
    WASTE_C5_HEADERS,
    WASTE_C5_ROWS,
    build_inputs,
)

HEADER_ROWS = 3
# 템플릿 줄에서 이름 칸의 자리(C1: 국가·사업장·상세 품목명·계정과목·거래처명, C2 는 같은 자리에 상세 자산명·자산번호·계정과목).
# 2번 칸(품목군·자산분류)은 분류어라 뺀다. C2 의 6번(거래처명)도 이름이다.
NAME_COLUMNS = frozenset({0, 1, 3, 4, 5, 6})


def _cells(path: Path, skip_rows: int):
    wb = load_workbook(path, read_only=True)
    try:
        for ws in wb.worksheets:
            for n, row in enumerate(ws.iter_rows(values_only=True), start=1):
                if n > skip_rows:
                    yield from (v for v in row if v is not None)
    finally:
        wb.close()


def test_numbers_are_synthetic(tmp_path):
    inputs = build_inputs(tmp_path)
    for path in (inputs.template, inputs.factor_book):
        numbers = [v for v in _cells(path, HEADER_ROWS) if isinstance(v, int | float)]
        assert len(numbers) >= 10  # 분모: 실제로 본다
        assert [n for n in numbers if not repr(n).lstrip("-0.").startswith("9")] == []  # 첫 유효 숫자가 9


# 새 시트의 이름 칸: C4-2·C9-2(국가·사업장·운송수단·자재명·제품명·거래처명), C6-2(국가·출장주체·출장항목),
# C5(국가·사업장·폐기물 종류·상세 폐기물명). C5 의 단위·처리방법은 코랩 규칙이 읽는 분류어라 뺀다.
NEW_NAME_COLUMNS = (
    (C4_SPEND_ROWS, frozenset({0, 1, 2, 3, 4})),
    (C9_SPEND_ROWS, frozenset({0, 1, 2, 3, 4})),
    (C6_SPEND_ROWS, frozenset({0, 1, 2})),
    (C5_ROWS, frozenset({0, 1, 2, 3})),
)


def test_name_cells_are_synthetic():
    names = [v for row in (*C1_ROWS, *C2_ROWS) for i, v in enumerate(row) if i in NAME_COLUMNS and isinstance(v, str)]
    names += [v for rows, cols in NEW_NAME_COLUMNS for row in rows for i, v in enumerate(row) if i in cols and isinstance(v, str)]
    names += [v for r in USEPA_ROWS for v in (r[0], r[1], r[3], *r[4].split(", "))]
    names += [v for r in (*TRANSPORT_SPEND_ROWS, *TRAVEL_SPEND_ROWS) for v in (r[0], r[1], r[2], r[3], *r[4].split(", "))]
    names += [v for r in WASTE_C5_ROWS for v in (r[0], *r[2].split(", "), r[3], r[4])]
    assert len(names) >= 120
    assert [v for v in names if not v.startswith(("가상", "Synthetic", "9"))] == []


TEMPLATE_SHEETS = [C1_SHEET, C2_SHEET, "C4-1_운송거리", C4_SPEND_SHEET, C5_SHEET, "C6-1_출장거리", C6_SPEND_SHEET,
                   "C7_임직원통근", "C9-1_운송거리", C9_SPEND_SHEET]  # fmt: skip


def test_template_sheets_and_distance_sheets_are_header_only(tmp_path):
    wb = load_workbook(build_inputs(tmp_path).template, read_only=True)
    try:
        assert wb.sheetnames == TEMPLATE_SHEETS
        for name, _, _ in DISTANCE_SHEETS:
            assert len(list(wb[name].iter_rows(values_only=True))) == HEADER_ROWS  # 안내·주 머리·서브 머리 — 줄 0
        assert [len(list(wb[n].iter_rows())) - HEADER_ROWS for n in (C4_SPEND_SHEET, C9_SPEND_SHEET, C6_SPEND_SHEET, C5_SHEET)] == [
            len(C4_SPEND_ROWS), len(C9_SPEND_ROWS), len(C6_SPEND_ROWS), len(C5_ROWS)]  # fmt: skip
    finally:
        wb.close()


def test_factor_book_sheets(tmp_path):
    wb = load_workbook(build_inputs(tmp_path).factor_book, read_only=True)
    try:
        assert wb.sheetnames == ["구매_USEPA", "운송_Spend", "출장_Spend", "폐기물_C5"]
        headers = {ws.title: list(ws.iter_rows(min_row=3, max_row=3, values_only=True))[0] for ws in wb.worksheets}
    finally:
        wb.close()
    assert headers["운송_Spend"] == ("운송 구분", *SPEND_HEADERS)
    assert headers["출장_Spend"] == ("출장 항목", *SPEND_HEADERS)
    assert headers["폐기물_C5"] == WASTE_C5_HEADERS


def _colab_text() -> str:
    try:
        path = default_colab_md()
    except FileNotFoundError:
        pytest.skip("받은 읽기판이 없는 자리(received-derived/ 밖)")
    if not path.is_file():
        pytest.skip(f"받은 읽기판이 없다: {path}")
    return path.read_text("utf-8")


def test_names_and_headers_match_the_colab_reading_copy(tmp_path):
    """§3-1: 템플릿 시트 이름은 코랩 `SHEET_PROCESSOR_MAP` 키(소문자), 계수 시트는 코랩이 읽는 이름·첫 읽기 머리 줄
    (0부터)이고, 우리 머리 줄은 셀 25 머리글 자동 찾기의 낱말을 다 품는다. 코랩 글은 실행 때 읽기판에서 뽑는다(사본 0)."""
    text = _colab_text()
    keys = set(re.findall(r'^\s*"([^"]+)":\s*\(standardize_', text, re.M))
    assert {n.lower() for n in TEMPLATE_SHEETS} <= keys
    first_read = dict(re.findall(r'_load_ef_sheet\("([^"]+)",\s*header_row=(\d+)', text))
    must = {n: re.findall(r'"([^"]+)"', words)
            for n, words in re.findall(r'_ef_reload_sheet_auto\("([^"]+)", must_contain=\[([^\]]*)\]', text)}  # fmt: skip
    wb = load_workbook(build_inputs(tmp_path).factor_book, read_only=True)
    try:
        for ws in wb.worksheets:
            if ws.title == "구매_USEPA":
                continue  # C1·C2 의 시트는 셀 9·26 이 따로 읽는다(S3C-o)
            assert first_read[ws.title] == "2"  # 0부터 2 = 셋째 줄
            header = " ".join(str(v) for v in list(ws.iter_rows(min_row=3, max_row=3, values_only=True))[0] if v)
            assert must[ws.title] and all(w in header for w in must[ws.title]), ws.title
    finally:
        wb.close()


def test_inputs_are_byte_identical_across_builds(tmp_path):
    a = build_inputs(tmp_path / "a")
    b = build_inputs(tmp_path / "b")
    # openpyxl 은 문서 속성에 지은 시각을 적는다 — 같은 초 안이 아니면 바이트가 다를 수 있어 시트 값으로 본다
    assert list(_cells(a.template, 0)) == list(_cells(b.template, 0))
    assert list(_cells(a.factor_book, 0)) == list(_cells(b.factor_book, 0))


def test_default_answers_are_marked_synthetic():
    data = json.loads(DEFAULT_ANSWERS.read_text("utf-8"))
    assert data["synthetic"] is True
    assert data["answers"] and all("가상" in a["expect"] for a in data["answers"])
