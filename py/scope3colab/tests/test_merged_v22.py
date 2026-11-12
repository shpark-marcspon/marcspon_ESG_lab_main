"""합본판 `merged-v22`·`base-v22map`(S3C-o5, `sid:MARC-ISSUE-274`). 받은 v2.2 에서 잰 구조(시트 차례·머리글 줄·회색 열 자리·수식 칸
수)는 글자로 적어 단언한다(받은 파일은 열지 않는다). 글·수는 전부 지은 것(synthetic)이다."""

import json
import re
import zipfile
from pathlib import Path

import pytest
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from scope3colab import cli
from scope3colab import synthetic as syn
from scope3colab.cells import default_colab_md
from scope3colab.cli import DEFAULT_ANSWERS, INPUTS, MERGED_ANSWERS
from scope3colab.synthetic_merged import (
    DROPDOWN_SHEETS,
    FULL_MERGE,
    GRAY_SHEETS,
    MERGED_TEMPLATE,
    TWO_ROW_GRAY,
    build_merged_inputs,
)
from scope3colab.synthetic_v22 import (
    COMPANY_SHEET,
    INVESTMENT_RESULT,
    RECEIVED_HEADER_ROW,
    TRAVEL_MAP_SHEET,
    WASTE_MAP_SHEET,
    _header_row,
    build_base_v22map_inputs,
)

# 받은 v2.2 시트 차례(26) — esg-app 의 v2.2 사본에서 잰 이름 그대로
RECEIVED_ORDER = [
    "기업정보", "C1_구매품서비스", "C2_자본재", "C3_연료에너지", "C4-1_운송거리", "C4-2_운송금액", "C5_사업장폐기물", "C6-1_출장거리",
    "C6-2_출장비", "C7_임직원통근", "C8-1_에너지사용량 아는 경우", "C8-2_면적", "C8-3_인원", "C9-1_운송거리", "C9-2_운송금액",
    "C11_판매제품사용", "C12_판매제품폐기", "C13-1_에너지사용량 아는경우", "C13-2_면적", "C13-3_인원", "C14_프랜차이즈", "C15_투자",
    "배출계수_드롭다운", "드롭다운_매핑검토", "회사별_폐기물매핑", "회사별_교통수단매핑",
]  # fmt: skip
# 받은 머리글 줄(별표 둘 이상인 줄 — C15 는 받은 것도 그 줄)
RECEIVED_HEADER = {"C1_구매품서비스": 18, "C2_자본재": 18, "C4-2_운송금액": 20, "C7_임직원통근": 20, "C12_판매제품폐기": 20,
                   "C15_투자": 14}  # fmt: skip — 나머지 활동 시트 19
# 수식 칸 수: 받은 수(발주서 §0) → 합본판 수. 차이는 지어낸 값 줄(사용자가 값을 친 줄에는 수식을 두지 않음 — `Builder`)과 매핑 시트
# 행 수(⑤ 는 S3C-o4a 짓개 그대로 2~22 행 21칸, 받은 것은 49칸)다. 연간: 연간만 든 줄 수만큼 빠짐. 자동매칭(C5·C6-1·C7): 1번 줄
# (매핑 줄)을 뺀 데이터 줄 수만큼 빠짐.
FORMULA_CELLS = {
    "C1_구매품서비스": 36 - 4, "C2_자본재": 36 - 2, "C3_연료에너지": 36 - 3, "C4-1_운송거리": 36 - 5, "C4-2_운송금액": 36 - 2,
    "C5_사업장폐기물": (201 - 4) + (201 - 4) + 1, "C6-1_출장거리": (201 - 3) + (201 - 3) + 1, "C6-2_출장비": 36 - 2,
    "C7_임직원통근": (201 - 2) + (201 - 2) + 1, "C8-1_에너지사용량 아는 경우": 36 - 2, "C8-2_면적": 36 - 1, "C8-3_인원": 36 - 1,
    "C9-1_운송거리": 36 - 2, "C9-2_운송금액": 36 - 1, "C11_판매제품사용": 0, "C12_판매제품폐기": 0,
    "C13-1_에너지사용량 아는경우": 36 - 1, "C13-2_면적": 36 - 1, "C13-3_인원": 36, "C14_프랜차이즈": 0, "C15_투자": 11,
    WASTE_MAP_SHEET: 21, TRAVEL_MAP_SHEET: 21, COMPANY_SHEET: 0, "배출계수_드롭다운": 0, "드롭다운_매핑검토": 0,
}  # fmt: skip
NOTE_NAMES = ("비고(선택)", "설명/적요(선택)")
CLASS_WORDS = frozenset({"전력", "경유", "도시가스", "도시가스(LNG)", "천연가스(LNG)", "kWh", "L", "Nm³", "kg", "g", "ton", "MWh",
                         "선택안함", "톤", "소각", "재활용", "매립", "ZZQ", "원재료", "포장재", "사무용품", "생산설비", "IT장비"})  # fmt: skip
# ↑ 코랩 규칙이 읽는 분류어(에너지원·단위·처리방법·품목군·자산분류 선택지)와 지은 공항 코드


@pytest.fixture(scope="module")
def merged(tmp_path_factory) -> syn.Inputs:
    return build_merged_inputs(tmp_path_factory.mktemp("merged"))


@pytest.fixture(scope="module")
def wb(merged):
    book = load_workbook(merged.template)
    yield book
    book.close()


def _formulas(ws) -> list[str]:
    return [c.coordinate for row in ws.iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("=")]


# ── 구조 ──
def test_sheet_order_is_received_v22_order(wb):
    assert wb.sheetnames == RECEIVED_ORDER


def test_header_rows_are_the_received_ones(wb):
    seen = {}
    for name, *_ in MERGED_TEMPLATE:
        seen[name] = _header_row(wb[name])
        assert seen[name] == RECEIVED_HEADER.get(name, 19) == RECEIVED_HEADER_ROW[name], name
    assert len(seen) == 21


def test_gray_columns_on_twenty_sheets_right_after_note(wb):
    assert len(GRAY_SHEETS) == 20 and "C15_투자" not in GRAY_SHEETS
    for name, main, sub, ef, _ in MERGED_TEMPLATE:
        ws = wb[name]
        h = _header_row(ws)
        heads = [ws.cell(h, c).value for c in range(1, ws.max_column + 1)]
        gray = [i for i, v in enumerate(heads, start=1) if isinstance(v, str) and v.startswith(("배출계수(", "배출량("))]
        if ef is None:
            assert gray == [], name
            continue
        notes = [i for i, v in enumerate(heads, start=1) if v in NOTE_NAMES]
        anchor = notes[0] if notes else heads.index("*연료 단위") + 1  # C14 는 비고가 없다
        assert name != "C14_프랜차이즈" or notes == []
        assert gray == [anchor + 1, anchor + 2], name
        assert (heads[anchor], heads[anchor + 1]) == (ef, "배출량(tCO2e)"), name
        # 회색 머리 병합은 머리글 줄을 내린 뒤의 (머리 줄, 머리 줄 + 1) — 두 줄 머리 시트만
        merges = {str(m) for m in ws.merged_cells.ranges}
        for c in gray:
            cell = f"{get_column_letter(c)}{h}:{get_column_letter(c)}{h + 1}"
            assert (cell in merges) == (name in TWO_ROW_GRAY), (name, cell)
        assert [m for m in ws.merged_cells.ranges if m.min_row < h] == [], name  # 옛 줄에 남은 병합 없음


def test_c11_c12_two_row_headers_fully_merged(wb):
    for name, width in FULL_MERGE.items():
        ws = wb[name]
        h = _header_row(ws)
        merges = {str(m) for m in ws.merged_cells.ranges}
        assert all(f"{get_column_letter(c)}{h}:{get_column_letter(c)}{h + 1}" in merges for c in range(1, width + 3)), name
        assert all(ws.cell(h + 1, c).value is None for c in range(1, width + 3))  # 빈 서브 줄


def test_c15_has_investment_column_instead_of_note(wb):
    ws = wb["C15_투자"]
    heads = [ws.cell(14, c).value for c in range(1, ws.max_column + 1)]
    assert heads == ["*피투자기업명 (필수)", "지분율(%) (선택)", "*피투자기업 Scope1배출량", "*피투자기업 Scope2배출량", INVESTMENT_RESULT]
    assert ws["E15"].value == '=IF(OR(A15="",B15="",C15="",D15=""),"",(C15+D15)*B15/100)'


def test_formula_cell_counts_per_sheet(wb):
    assert {ws.title: len(_formulas(ws)) for ws in wb.worksheets} == FORMULA_CELLS


def test_cached_values_by_value(merged):
    book = load_workbook(merged.template, data_only=True)
    try:
        c3, c8, c13, c15 = book["C3_연료에너지"], book["C8-2_면적"], book["C13-3_인원"], book["C15_투자"]
        assert (c3["E22"].value, c3["E23"].value, c3["E25"].value) == (1830, 99, None)  # 월별 줄 합(910+920) · 연간만(값) · 빈 줄(빈 글 `<v/>` — openpyxl 은 None)
        assert (c8["G21"].value, c8["G22"].value) == (9100, 18300)  # 연간만 · 월별 9100+9200
        assert c13["G21"].value == 1930  # 960+970
        assert (c15["E15"].value, c15["E16"].value, c15["E17"].value) == (173.85, None, None)  # (910+920)×9.5/100 · Scope2 빔 · 빈 줄
        for name in ("C5_사업장폐기물", "C6-1_출장거리", "C7_임직원통근"):
            ws = book[name]
            h = _header_row(ws)
            assert ws[f"B{h - 5}"].value == "가상기업A", name  # 적용 기업명
        assert book["C5_사업장폐기물"]["C21"].value == "가상폐지"  # 매핑 줄(상세명 → 매핑 시트)
        assert book["C6-1_출장거리"]["C21"].value == "가상버스"
        assert book["C7_임직원통근"]["C22"].value == "가상승용차"
        assert [book[WASTE_MAP_SHEET][f"E{r}"].value for r in (2, 3, 4)] == ["가상기업A", "가상기업B", None]
    finally:
        book.close()


def test_builds_are_byte_identical(tmp_path):
    a, b = build_merged_inputs(tmp_path / "a"), build_merged_inputs(tmp_path / "b")
    assert a.template.read_bytes() == b.template.read_bytes()


def test_names_and_numbers_are_synthetic(merged, wb):
    data = [v for _, _, _, _, rows in MERGED_TEMPLATE for row in rows for v in row if v is not None]
    names = [v for v in data if isinstance(v, str) and v not in CLASS_WORDS]
    numbers = [v for v in data if isinstance(v, int | float)]
    assert len(names) >= 100 and len(numbers) >= 100  # 분모
    assert [v for v in names if not v.startswith(("가상", "Synthetic", "9"))] == []
    assert [n for n in numbers if not repr(n).lstrip("-0.").startswith("9")] == []
    heads = {v for _, main, sub, ef, _ in MERGED_TEMPLATE for v in (*main, *(sub or ()), ef, "배출량(tCO2e)") if v}
    fixed = {"*적용 기업명", "적용 기업명", "회사 목록", "(가상 선택)", "회사명", "상세명", "매핑값", "회사 목록(가상)", "가상 안내", "상세 교통수단명(선택)"}
    other = [v for ws in wb.worksheets for row in ws.iter_rows(values_only=True) for v in row
             if isinstance(v, str) and not v.startswith(("=", "가상", "Synthetic", "9", "※", "카테고리 안내(가상)", "기업정보(가상"))
             and v not in heads | fixed | CLASS_WORDS]  # fmt: skip
    assert other == []
    text = "\n".join(v for ws in wb.worksheets for row in ws.iter_rows(values_only=True) for v in row if isinstance(v, str))
    assert set(re.findall(r"[\w]*기업[A-Za-z0-9]+", text)) == {"가상기업A", "가상기업B"}
    assert all(wb[n].max_row == 1 and str(wb[n]["A1"].value).startswith("가상 안내") for n in DROPDOWN_SHEETS)


# ── 판 ──
def test_inputs_entries():
    assert INPUTS["merged-v22"] == (build_merged_inputs, MERGED_ANSWERS, True)  # 거리 줄이 있다 — 지오코더는 늘 None
    assert INPUTS["base-v22map"] == (build_base_v22map_inputs, DEFAULT_ANSWERS, False)


def test_merged_answers_are_synthetic_with_expect():
    data = json.loads(MERGED_ANSWERS.read_text("utf-8"))
    assert data["synthetic"] is True
    assert len(data["answers"]) == 42 and all(a.get("expect") for a in data["answers"])


_FORMULA = re.compile(r'<c r="(?P<r>[A-Z]+[0-9]+)"(?P<attrs>[^>]*)><f>[^<]*</f>(?P<rest>.*?)</c>')


def test_base_v22map_has_no_value_elements_and_c5_mapping_row_is_formula(tmp_path):
    inputs = build_base_v22map_inputs(tmp_path)
    with zipfile.ZipFile(inputs.template) as z:
        xml = [z.read(n).decode() for n in z.namelist() if n.startswith("xl/worksheets/sheet")]
    cells = [m for x in xml for m in _FORMULA.finditer(x)]
    assert len(cells) >= 50
    assert [m.group("r") for m in cells if "<v" in m.group("rest")] == []
    wb = load_workbook(inputs.template)
    try:
        ws = wb["C5_사업장폐기물"]
        h = _header_row(ws)
        assert h == 2 + 2  # ⑦ 없음 — 적용 기업명 두 줄만 끼움
        kind = [c for c in range(1, ws.max_column + 1) if ws.cell(h, c).value == "*폐기물 종류"][0]
        assert str(ws.cell(h + 2, kind).value).startswith("=IFERROR(INDEX(회사별_폐기물매핑!")
        assert ws.cell(h + 3, kind).value == "가상수지찌꺼기 묶음"  # 값을 친 줄은 그대로
    finally:
        wb.close()


# ── 합본판 실행(받은 읽기판이 있는 자리에서만) ──
def _colab_md() -> Path:
    try:
        path = default_colab_md()
    except FileNotFoundError:
        pytest.skip("받은 읽기판이 없는 자리(received-derived/ 밖)")
    if not path.is_file():
        pytest.skip(f"받은 읽기판이 없다: {path}")
    return path


# 시트마다 회색 칸 채운 수(코랩 로그 `[filled-template] … n칸 채움` 과 같음 — 0 = 0 헛통과를 막는 분모). 데이터 줄 × 2 에서 빠진 줄은
# 계수를 못 찾는 줄(C1 4번·C4-2 3번·C6-2 3번 — 기본판의 「아무것도 안 맞음」 줄)과 코랩 결함 줄(C12 5·6번 — 생물기원 코드에
# 처리비중 빔, `sid:MARC-ENT-442` 로 기대값도 코랩)이다.
FILLED_CELLS = {
    "C1_구매품서비스": 8, "C2_자본재": 6, "C3_연료에너지": 8, "C4-1_운송거리": 12, "C4-2_운송금액": 4, "C5_사업장폐기물": 10,
    "C6-1_출장거리": 8, "C6-2_출장비": 4, "C7_임직원통근": 6, "C8-1_에너지사용량 아는 경우": 6, "C8-2_면적": 4, "C8-3_인원": 2,
    "C9-1_운송거리": 6, "C9-2_운송금액": 4, "C11_판매제품사용": 6, "C12_판매제품폐기": 10, "C13-1_에너지사용량 아는경우": 2,
    "C13-2_면적": 2, "C13-3_인원": 2, "C14_프랜차이즈": 4,
}  # fmt: skip


def test_merged_run_uses_every_answer_and_fills_every_gray_sheet(tmp_path):
    md = _colab_md()
    assert cli.run(md, None, tmp_path / "ok", input_kind="merged-v22") == cli.SAME
    meta = json.loads((tmp_path / "ok" / "run.json").read_text("utf-8"))
    assert (meta["ai_calls"], meta["answers"], meta["aborts"]) == (42, 42, [])
    assert len(json.loads((tmp_path / "ok" / "colab.json").read_text("utf-8"))["rows"]) == 64
    filled = {s["sheet"]: s["gray"]["filled_cells"] for s in json.loads((tmp_path / "ok" / "outputs.json").read_text("utf-8"))["filled"]
              if s["gray"]}  # fmt: skip
    assert filled == FILLED_CELLS and all(filled.values())
    log = (tmp_path / "ok" / "colab-stdout.log").read_text("utf-8")
    assert {m[0]: int(m[1]) for m in re.findall(r"\[filled-template\] (.+?): (\d+)칸 채움", log)} == FILLED_CELLS


@pytest.mark.parametrize("change", ["expect", "short"])
def test_merged_run_stops_when_answers_are_wrong_or_short(tmp_path, change):
    md = _colab_md()
    data = json.loads(MERGED_ANSWERS.read_text("utf-8"))
    if change == "expect":
        data["answers"][20]["expect"] = "가상 다른 물음"
    else:
        data["answers"] = data["answers"][:-1]
    bad = tmp_path / "answers.json"
    bad.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    assert cli.run(md, None, tmp_path / "out", answers_path=bad, input_kind="merged-v22") == cli.TOOL_FAILED
