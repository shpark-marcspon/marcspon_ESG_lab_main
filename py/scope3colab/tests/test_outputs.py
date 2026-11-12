"""S3C-o4b: 출력 파일 둘의 꼴 대조(§3-1 ㉠ 결과 파일, §3-2 ㉡ 회색 칸), 회색 칸 판 `filled`, o4a 검증 권고 2(§3-5 — 칸 표와 v2.2 저장 값).

결과 파일·템플릿·filled 파일은 전부 시험 안에서 openpyxl 로 짓는 작은 지어낸 표다. 받은 코랩은 돌리지 않는다.
"""
import json
import re
import shutil
import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from scope3colab.cli import FILLED_ANSWERS, INPUTS, main
from scope3colab.outputs import (
    compare_filled_files,
    compare_result_files,
    ef_scale,
    filled_report,
    find_gray_columns,
    find_note_column,
)
from scope3colab.rows import BRANCH_FIELDS, SHEET_FIELDS
from scope3colab.sandbox import load_answers
from scope3colab.synthetic_filled import FILLED_TEMPLATE, build_filled_inputs
from scope3colab.synthetic_v22 import _sheet_paths, build_base_v22_inputs, build_distance_v22_inputs, build_distance_v22map_inputs
from scope3colab.tests.test_compare import FAKE_COLAB_MD


# ── ㉠ 결과 파일 꼴 ──
C6_UNION = ("국가", "이동수단", "이동거리(km)", "비고", "출장비용", "배출계수", "배출량(tCO2e)", "EF_DB")  # C6-1 열 뒤에 C6-2 만의 열
RESULT_SHEETS = (
    ("C4_업스트림운송", ("국가", "자재명", "운송금액", "배출량(tCO2e)"), (("가상국", "가상자재", 9100, 8.2),)),
    ("C6_출장", C6_UNION, (("가상국", "가상버스", 99, "가상 비고", None, 0.0923, 1.48, None),
                          ("가상국", "가상항공권", None, None, 9310000, 0.000921, 8.57, "Synthetic Flights"))),
    ("C14_프랜차이즈", ("국가", "프랜차이즈명", "사용량", "배출량(tCO2e)"), (("가상국", "가상가맹A", 9100, 8.5),)),
)  # fmt: skip


def write_result(path: Path, sheets=RESULT_SHEETS) -> Path:
    wb = Workbook()
    wb.remove(wb.active)
    for name, header, rows in sheets:
        ws = wb.create_sheet(name)
        ws.append(list(header))
        for r in rows:
            ws.append(list(r))
    wb.save(path)
    return path


def _swap_first_two(header):
    return (header[1], header[0], *header[2:])


@pytest.mark.parametrize(
    ("label", "engine_sheets", "expected"),
    [
        ("같음", RESULT_SHEETS, []),
        ("시트 빠짐", RESULT_SHEETS[:2], [("C14_프랜차이즈", "(시트)")]),
        ("열 차례", (RESULT_SHEETS[0], ("C6_출장", _swap_first_two(C6_UNION), RESULT_SHEETS[1][2]), RESULT_SHEETS[2]),
         [("C6_출장", "(열)")]),
        ("열 더함", ((RESULT_SHEETS[0][0], (*RESULT_SHEETS[0][1], "가상열"), RESULT_SHEETS[0][2]), *RESULT_SHEETS[1:]),
         [("C4_업스트림운송", "(열)")]),
        ("줄 수", (*RESULT_SHEETS[:2], (RESULT_SHEETS[2][0], RESULT_SHEETS[2][1], RESULT_SHEETS[2][2] * 2)),
         [("C14_프랜차이즈", "(줄 수)")]),
        ("C6 합집합 빠짐(C6-1 열만)", (RESULT_SHEETS[0], ("C6_출장", C6_UNION[:4] + C6_UNION[5:7], RESULT_SHEETS[1][2]),
                                    RESULT_SHEETS[2]), [("C6_출장", "(열)")]),
        ("시트 차례", (RESULT_SHEETS[1], RESULT_SHEETS[0], RESULT_SHEETS[2]), [("", "(시트 차례)")]),
    ],
)  # fmt: skip
def test_result_file_shape(tmp_path, label, engine_sheets, expected):
    colab = write_result(tmp_path / "colab_result.xlsx")
    engine = write_result(tmp_path / "engine_result.xlsx", engine_sheets)
    assert [(d.where, d.what) for d in compare_result_files(colab, engine)] == expected, label


def test_result_file_extra_engine_sheet_is_one_diff(tmp_path):
    colab = write_result(tmp_path / "colab_result.xlsx", RESULT_SHEETS[:2])
    engine = write_result(tmp_path / "engine_result.xlsx")
    diffs = compare_result_files(colab, engine)
    assert [(d.where, d.what, d.left, d.right) for d in diffs] == [("C14_프랜차이즈", "(시트)", None, "있음")]


# ── ㉡ 회색 칸 ──
# 지출 시트(kg 머리, 두 줄 머리 + 병합, 비고 있음)·거리 시트(t 머리, 한 줄 머리)·안내 시트(수식 칸, 회색 열 없음).
EF_SPEND, EF_DIST = 0.000911, 0.0923


def write_template(path: Path) -> Path:
    wb = Workbook()
    wb.remove(wb.active)
    info = wb.create_sheet("기업정보")
    info["C7"] = "가상기업A"
    ws = wb.create_sheet("C4-2_가상운송")
    ws.append(["가상 안내"])
    ws.append(["*국가", "*품목", "연간 입력", "비고(선택)", "배출계수(kgCO2e/원)", "배출량(tCO2e)"])
    ws.append([None, None, "연간운송금액(원)", None, None, None])
    ws.merge_cells("E2:E3")
    ws.merge_cells("F2:F3")
    ws.append(["가상국", "가상자재 가", 9100000, "가상 비고"])
    ws.append(["가상국", "가상자재 나", None, None])
    ws["C5"] = "=IF(SUM(9,9)=0,\"\",SUM(9,9))"  # 수식 칸(회색 열 밖)
    ws2 = wb.create_sheet("C6-1_가상출장")
    ws2.append(["가상 안내"])
    ws2.append(["*국가", "*교통수단", "편도거리(km)(선택)", "배출계수(tCO2e/km)", "배출량(tCO2e)"])
    ws2.append(["가상국", "가상버스", 99])
    wb.save(path)
    return path


def write_filled(template: Path, path: Path, edits: dict[tuple[str, str], object]) -> Path:
    wb = load_workbook(template)
    for (sheet, coord), value in edits.items():
        wb[sheet][coord] = value
    wb.save(path)
    return path


COLAB_FILL = {
    ("C4-2_가상운송", "E4"): EF_SPEND,  # kg 머리 — 배율 1
    ("C4-2_가상운송", "F4"): 8.29921,
    ("C6-1_가상출장", "D3"): EF_DIST * (1.0 / 1000.0),  # t 머리 — 코랩은 1/1000 을 곱한다
    ("C6-1_가상출장", "E3"): 1.4803074,
}


@pytest.fixture
def files(tmp_path):
    template = write_template(tmp_path / "template.xlsx")
    colab = write_filled(template, tmp_path / "colab_filled.xlsx", COLAB_FILL)
    return template, colab, tmp_path


def engine_with(files, edits) -> Path:
    template, _, tmp = files
    return write_filled(template, tmp / "engine_filled.xlsx", {**COLAB_FILL, **edits})


def test_filled_same_is_zero(files):
    template, colab, _ = files
    assert compare_filled_files(template, colab, engine_with(files, {})) == []


@pytest.mark.parametrize(
    ("label", "edits", "expected"),
    [
        ("회색 칸 값", {("C4-2_가상운송", "F4"): 8.29922}, [("C4-2_가상운송!F4", "회색 칸")]),
        ("회색 칸 채움 빠짐", {("C4-2_가상운송", "E4"): None}, [("C4-2_가상운송!E4", "회색 칸")]),
        ("회색 칸 밖", {("C4-2_가상운송", "B4"): "가상자재 다"}, [("C4-2_가상운송!B4", "회색 칸 밖(engine)")]),
        ("회색 열 머리 칸", {("C4-2_가상운송", "E2"): "배출계수(가상)"}, [("C4-2_가상운송!E2", "회색 칸 밖(engine)")]),
        ("t 머리 배율 빠짐", {("C6-1_가상출장", "D3"): EF_DIST}, [("C6-1_가상출장!D3", "회색 칸")]),
        ("수식 칸을 값으로", {("C4-2_가상운송", "C5"): 18}, [("C4-2_가상운송!C5", "수식 칸(engine)")]),
        ("회색 열 아래 원본 밖 줄", {("C6-1_가상출장", "D9"): 0.5}, [("C6-1_가상출장!D9", "회색 칸")]),
        ("회색 열 없는 시트", {("기업정보", "C7"): "가상기업B"}, [("기업정보!C7", "회색 칸 밖(engine)")]),
    ],
)  # fmt: skip
def test_filled_one_change_is_one_diff(files, label, edits, expected):
    template, colab, _ = files
    diffs = compare_filled_files(template, colab, engine_with(files, edits))
    assert [(d.where, d.what) for d in diffs] == expected, label


def test_filled_formula_kept_as_text_and_numbers_compare_as_decimal(files):
    template, colab, _ = files
    diffs = compare_filled_files(template, colab, engine_with(files, {("C4-2_가상운송", "F4"): 8.299210}))
    assert diffs == []
    diffs = compare_filled_files(template, colab, engine_with(files, {("C4-2_가상운송", "C5"): "=IF(SUM(9,9)=0,\"\",SUM(9,9,0))"}))
    assert [(d.what, d.left, d.right) for d in diffs] == [
        ("수식 칸(engine)", '=IF(SUM(9,9)=0,"",SUM(9,9))', '=IF(SUM(9,9)=0,"",SUM(9,9,0))')
    ]


def test_filled_colab_side_touching_original_is_counted_too(files):
    template, _, tmp = files
    colab = write_filled(template, tmp / "colab2.xlsx", {**COLAB_FILL, ("C6-1_가상출장", "C3"): 98})
    diffs = compare_filled_files(template, colab, engine_with(files, {}))
    assert [(d.where, d.what) for d in diffs] == [("C6-1_가상출장!C3", "회색 칸 밖(colab)")]


def test_filled_sheet_list_must_match_original(files):
    template, colab, tmp = files
    wb = load_workbook(engine_with(files, {}))
    wb.remove(wb["기업정보"])
    wb.save(tmp / "engine_filled.xlsx")
    diffs = compare_filled_files(template, colab, tmp / "engine_filled.xlsx")
    assert [d.what for d in diffs] == ["(시트)(engine)"]


def test_gray_column_rules_follow_colab(files):
    template, _, _ = files
    wb = load_workbook(template)
    spend = find_gray_columns(wb["C4-2_가상운송"])
    assert (spend.header_end, spend.ef_col, spend.em_col) == (3, 5, 6)  # 병합 끝 줄
    dist = find_gray_columns(wb["C6-1_가상출장"])
    assert (dist.header_end, dist.ef_col, dist.em_col) == (2, 4, 5)
    assert find_gray_columns(wb["기업정보"]) is None
    ws = Workbook().active
    ws.append(["배출계수 가", "배출량 가", "배출계수 나", "배출계수·배출량 함께"])  # 마지막 「배출계수」 칸이 이김, 둘 다 든 글은 배출계수
    got = find_gray_columns(ws)
    assert (got.ef_col, got.em_col, got.ef_header) == (4, 2, "배출계수·배출량 함께")


@pytest.mark.parametrize(
    ("header", "scale"),
    [("배출계수(kgCO2e/원)", "1"), ("배출계수(kgCO2eq/단위)", "1"), ("배출계수 (tCO2e / km)", "1/1000"),
     ("배출계수(tCO2eq/단위)", "1/1000"), ("배출계수(gCO2/단위)", "1(그 밖)"), ("배출계수", "1(그 밖)")],
)  # fmt: skip
def test_ef_scale(header, scale):
    assert ef_scale(header) == scale


def test_note_column_is_short_label_only():
    ws = Workbook().active
    ws.append(["이 칸에는 분류방법 설명서를 따라 적는다(가상 안내 긴 글)", "가상"])
    ws.append(["*국가", "설명/적요(선택)"])
    assert find_note_column(ws, 2) == 2
    assert find_note_column(ws, 1) is None  # 코랩은 머리 마지막 줄까지만 본다


def test_filled_report_pairing_scale_and_counts(files):
    template, colab, tmp = files
    result = write_result(tmp / "result.xlsx")
    report = {e["sheet"]: e for e in filled_report(template, colab, result)}
    spend, dist = report["C4-2_가상운송"]["gray"], report["C6-1_가상출장"]["gray"]
    assert (spend["scale"], spend["pairing"], spend["note_column"], spend["result_sheet"]) == ("1", "위치", "D", "C4_업스트림운송")
    assert (spend["gray_cells"], spend["filled_cells"]) == (4, 2)
    assert report["C4-2_가상운송"]["formula_cells"] == 1 and report["C4-2_가상운송"]["formula_changed"] == 0
    assert (dist["scale"], dist["pairing"], dist["result_sheet"], dist["gray_cells"], dist["filled_cells"]) == (
        "1/1000", "위치", "C6_출장", 2, 2)  # fmt: skip
    assert report["기업정보"]["gray"] is None


# ── CLI: 엔진 출력 파일 인자 ──
FAKE_BOTH_MD = FAKE_COLAB_MD.replace(
    'result = {"result_file": "result/가상_result.xlsx"}',
    'import shutil\nshutil.copy(input_file, "result/가상_filled_template.xlsx")\n'
    'result = {"result_file": "result/가상_result.xlsx", "filled_template_file": "result/가상_filled_template.xlsx"}',
)


def _cli(tmp_path, md_text, *extra) -> tuple[int, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    md = tmp_path / "가상_읽기판.md"
    md.write_text(md_text, encoding="utf-8")
    answers = tmp_path / "answers.synthetic.json"
    answers.write_text(json.dumps({"synthetic": True, "answers": []}), encoding="utf-8")
    out = tmp_path / "out"
    return main(["run", "--colab-md", str(md), "--answers", str(answers), "--out", str(out), *map(str, extra)]), out


def test_cli_outputs_same_0_changed_1_and_missing_filled_2(tmp_path):
    code, out = _cli(tmp_path / "a", FAKE_BOTH_MD)
    assert code == 0
    data = json.loads((out / "outputs.json").read_text("utf-8"))
    assert [s["sheet"] for s in data["result"]] == ["C1_구매품서비스", "C2_자본재", "C10_가상가공"] and "diffs" not in data
    result, filled = out / "work/result/가상_result.xlsx", out / "work/result/가상_filled_template.xlsx"
    keep = tmp_path / "keep"
    keep.mkdir()
    shutil.copy(result, keep / "r.xlsx")
    shutil.copy(filled, keep / "f.xlsx")
    code, out = _cli(tmp_path / "b", FAKE_BOTH_MD, "--engine-result-file", keep / "r.xlsx", "--engine-filled-file", keep / "f.xlsx")
    assert code == 0
    assert json.loads((out / "outputs.json").read_text("utf-8"))["diffs"] == []
    write_filled(keep / "f.xlsx", keep / "f2.xlsx", {("C1_구매품서비스", "B4"): "가상사업장Z"})
    code, out = _cli(tmp_path / "c", FAKE_BOTH_MD, "--engine-filled-file", keep / "f2.xlsx")
    assert code == 1
    assert [d["where"] for d in json.loads((out / "outputs.json").read_text("utf-8"))["diffs"]] == ["C1_구매품서비스!B4"]
    shutil.copy(out.parent.parent / "a" / "out" / "colab.json", keep / "rows.json")  # 줄·칸은 같고 출력 파일만 다름 → 1
    code, _ = _cli(tmp_path / "e", FAKE_BOTH_MD, "--engine-result", keep / "rows.json", "--engine-filled-file", keep / "f2.xlsx")
    assert code == 1
    code, _ = _cli(tmp_path / "f", FAKE_BOTH_MD, "--engine-result", keep / "rows.json", "--engine-filled-file", keep / "f.xlsx")
    assert code == 0
    code, _ = _cli(tmp_path / "d", FAKE_COLAB_MD, "--engine-filled-file", keep / "f.xlsx")  # 코랩이 filled 를 남기지 않음
    assert code == 2


# ── 회색 칸 판 `filled` ──
def test_filled_input_template_has_gray_columns_after_note(tmp_path):
    inputs = build_filled_inputs(tmp_path)
    wb = load_workbook(inputs.template)
    assert wb.sheetnames == [name for name, *_ in FILLED_TEMPLATE] == [
        "C4-2_운송금액", "C6-1_출장거리", "C6-2_출장비", "C9-2_운송금액", "C14_프랜차이즈"]  # fmt: skip
    got = {}
    for ws in wb.worksheets:
        gray = find_gray_columns(ws)
        note = find_note_column(ws, gray.header_end)
        got[ws.title] = (gray.header_end, ef_scale(gray.ef_header), None if note is None else gray.ef_col - note, gray.em_col - gray.ef_col)
    assert got == {
        "C4-2_운송금액": (3, "1", 1, 1),
        "C6-1_출장거리": (3, "1/1000", 1, 1),
        "C6-2_출장비": (3, "1", 1, 1),
        "C9-2_운송금액": (3, "1", 1, 1),
        "C14_프랜차이즈": (2, "1/1000", None, 1),  # 비고 열 없음 — 위치 짝
    }
    names = {c.value for ws in wb.worksheets for row in ws.iter_rows(min_row=find_gray_columns(ws).header_end + 1) for c in row
             if isinstance(c.value, str)}  # fmt: skip
    # 단위·코랩 분류어(에너지원 — 나머지 판과 같은 취급) 밖은 모두 `가상` 으로 시작한다
    assert {n for n in names if not n.startswith("가상")} == {"L", "kWh", "경유", "전력"}


def test_filled_input_answers_and_geocoder(tmp_path):
    build, answers, geocode_none = INPUTS["filled"]
    assert answers == FILLED_ANSWERS and load_answers(answers) == [] and geocode_none is False
    assert json.loads(answers.read_text("utf-8"))["synthetic"] is True
    a, b = build(tmp_path / "a"), build(tmp_path / "b")
    for x, y in ((a.template, b.template), (a.factor_book, b.factor_book)):
        wx, wy = load_workbook(x), load_workbook(y)
        assert [[c.value for row in ws.iter_rows() for c in row] for ws in wx] == [[c.value for row in ws.iter_rows() for c in row] for ws in wy]
    assert {"운송", "출장_통근", "에너지"} <= set(load_workbook(a.factor_book).sheetnames)


# ── §3-5 o4a 검증 권고 2: 칸 표를 글자로 ──
# 「JSON 칸」, 코랩 결과 열 이름이 다르면 「JSON 칸=결과 열」, 숫자 칸은 끝에 「#」.
_REC = tuple(f"추천EF{i}{s}" for i in range(1, 5) for s in ("", "_배출계수#"))
PURCHASE = ("구매금액(KRW)=EF적용금액(KRW)#", "대표EF=EF_DB", "대표EF_배출계수=배출계수#", "배출량(tCO2e)#", "산정출처=EF_DB_추천사유", *_REC)
TRANSPORT_SPEND = ("운송금액#", "EF(kgCO2e/KRW)#", "배출계수단위", "배출량(tCO2e)#")
LEASED = ("산정방식", "건물전체사용량#", "면적비율#", "인원비율#", "사용량#", "배출계수(kgCO2eq/단위)#", "EF단위", "단위환산비고", "배출량(tCO2e)#")
TRANSPORT_DISTANCE = ("월운송량#", "화물중량(kg)#", "운송거리(km)#", "배출계수(거리기반)#", "배출량(tCO2e)#")
EXPECTED_SHEET_FIELDS = {
    "C1": PURCHASE,
    "C2": PURCHASE,
    "C4": TRANSPORT_SPEND,
    "C5": ("처리량#", "배출계수(kgCO2eq/kg)#", "처리방법_정규화", "화석기원 CO2e(tCO2e)#", "생물기원 CO2e(tCO2e)#", "화석탄소비율(FCF)#",
           "바이오매스 구분", "배출량(tCO2e)#", "단위환산비고"),
    "C6": ("출장비용#", "EF_DB", "NAICS", "배출계수#", "배출계수단위", "배출량(tCO2e)#", "계산식", "정규화이동수단"),
    "C7": ("평균통근거리(km)#", "직원수#", "근무일수#", "거리왕복계수#", "정규화이동수단", "배출계수#", "배출량(tCO2e)#", "계산식"),
    "C9": TRANSPORT_SPEND,
    "C3": ("사용량#", "배출계수(kgCO2eq/단위)#", "EF단위", "EF매핑명", "단위환산비고", "배출량(tCO2e)#"),
    "C8": LEASED,
    "C11": ("사용기간(년)#", "총사용량#", "배출계수(kgCO2eq/단위)#", "EF단위", "EF매핑명", "단위환산비고", "배출량(tCO2e)#"),
    "C12": ("배출계수#", "화석기원 CO2e(tCO2e)#", "생물기원 CO2e(tCO2e)#", "화석탄소비율(FCF)#", "바이오매스 구분", "배출량(tCO2e)#"),
    "C13": LEASED,
    "C14": ("사용량#", "배출계수(kgCO2eq/단위)#", "EF단위", "EF매핑명", "단위환산비고", "배출량(tCO2e)#", "계산식"),
    "C15": ("배출량#",),
}  # fmt: skip
EXPECTED_BRANCH_FIELDS = {
    ("C4", "Distance"): TRANSPORT_DISTANCE,
    ("C6", "Distance"): ("이동거리(km)#", "출장인원#", "이동횟수#", "거리왕복계수#", "정규화이동수단", "배출계수#", "배출량(tCO2e)#", "계산식"),
    ("C9", "Distance"): TRANSPORT_DISTANCE,
}


def _spell(fields) -> tuple[str, ...]:
    return tuple((name if name == column else f"{name}={column}") + ("#" if numeric else "") for name, column, numeric in fields)


def test_sheet_fields_table_spelled_out():
    assert list(SHEET_FIELDS) == list(EXPECTED_SHEET_FIELDS)
    for key, expected in EXPECTED_SHEET_FIELDS.items():
        assert _spell(SHEET_FIELDS[key]) == expected, key
    assert SHEET_FIELDS["C13"] is SHEET_FIELDS["C8"] and SHEET_FIELDS["C9"] is SHEET_FIELDS["C4"]  # 짝 시트는 같은 표


def test_branch_fields_table_spelled_out():
    assert list(BRANCH_FIELDS) == list(EXPECTED_BRANCH_FIELDS)
    for key, expected in EXPECTED_BRANCH_FIELDS.items():
        assert _spell(BRANCH_FIELDS[key]) == expected, key


# ── §3-5 o4a 검증 권고 2: v2.2 수식 칸의 저장 값(시트 xml 의 `<v>`) ──
_CELL = re.compile(r'<c r="(?P<r>[A-Z]+[0-9]+)"(?P<attrs>[^>]*)><f>[^<]*</f>(?P<v><v>[^<]*</v>|<v/>|<v\s*/>)?</c>')


def stored_values(template: Path) -> dict[tuple[str, str], tuple[bool, str | None]]:
    """(시트, 칸) → (t="str" 인가, `<v>` 꼴 — 요소가 없으면 None)."""
    out = {}
    with zipfile.ZipFile(template) as z:
        for sheet, path in _sheet_paths(z).items():
            for m in _CELL.finditer(z.read(path).decode()):
                out[(sheet, m.group("r"))] = (' t="str"' in m.group("attrs"), m.group("v"))
    return out


V22_STORED = {
    "base-v22": {
        ("C1_구매품서비스", "G24"): (False, "<v>28800</v>"),  # 월 합(9500 + 9600 + 9700)
        ("C1_구매품서비스", "G25"): (True, "<v/>"),  # 빈 줄 — 빈 글
        ("C2_자본재", "H22"): (False, "<v>1870000</v>"),
        ("C5_사업장폐기물", "B14"): (True, "<v>가상기업A</v>"),  # 적용 기업명
        ("C5_사업장폐기물", "C21"): (True, "<v>가상폐지</v>"),  # 매핑 결과(가상기업A × 가상 사무 폐지)
        ("C5_사업장폐기물", "C26"): (True, "<v/>"),
        ("C9-2_운송금액", "F22"): (False, "<v>1845000</v>"),
        ("회사별_폐기물매핑", "E3"): (True, "<v>가상기업B</v>"),
        ("회사별_교통수단매핑", "E4"): (True, "<v/>"),
    },
    "distance-v22": {
        ("C4-1_운송거리", "I26"): (False, "<v>1930</v>"),
        ("C6-1_출장거리", "B14"): (True, "<v>가상기업A</v>"),
        ("C6-1_출장거리", "C21"): (True, "<v>가상버스</v>"),  # 가상기업A × 가상 상세수단 1
        ("C6-1_출장거리", "H23"): (False, "<v>18</v>"),
        ("C7_임직원통근", "C22"): (True, "<v>가상승용차</v>"),  # 가상기업A × 가상 상세수단 2(가상기업B 줄에 가려지지 않음)
        ("C9-1_운송거리", "I23"): (False, "<v>18.299999999999997</v>"),  # 9.1 + 9.2 의 float 그대로
        ("회사별_교통수단매핑", "E2"): (True, "<v>가상기업A</v>"),
    },
}
BUILDERS = {"base-v22": build_base_v22_inputs, "distance-v22": build_distance_v22_inputs}


@pytest.mark.parametrize("kind", sorted(V22_STORED))
def test_v22_stored_values(tmp_path, kind):
    got = stored_values(BUILDERS[kind](tmp_path).template)
    for cell, expected in V22_STORED[kind].items():
        assert got.get(cell) == expected, cell


def test_v22map_formula_cells_have_no_stored_value(tmp_path):
    got = stored_values(build_distance_v22map_inputs(tmp_path).template)
    assert got  # 수식 칸은 있다
    assert {v for _, v in got.values()} == {None}
    assert not any(t for t, _ in got.values())
