"""S3C-o3 거리판: 템플릿·계수 책 머리(§3-1), 가짜 지오코더(§3-2), 줄 갈래(§3-3), C7 열쇠(§3-4), 거리판 답 차례(§3-5).

결과 엑셀은 코랩 결과 꼴을 흉내 낸 작은 지어낸 표다(openpyxl). 받은 코랩은 돌리지 않는다 — 읽기판 글은 §3-1 에서만
실행 때 뽑아 견준다(읽기판이 없으면 건너뜀).
"""
import json
import re
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from scope3colab.cells import CodeCell, default_colab_md
from scope3colab.cli import DISTANCE_ANSWERS, DISTANCE_TRANSPORT_ANSWERS, main
from scope3colab.compare import compare, render_diff_md
from scope3colab.rows import (
    BRANCH_FIELDS,
    ITEM_COLUMNS,
    SHEET_FIELDS,
    Row,
    Rows,
    RowsFormatError,
    rows_from_json,
    rows_from_result_xlsx,
)
from scope3colab.sandbox import (
    Answer,
    AnswerMismatch,
    AnswersExhausted,
    BlockedModuleUsed,
    Sandbox,
    load_answers,
    run_in_sandbox,
)
from scope3colab.synthetic import (
    C4_DIST_ROWS,
    C6_DIST_ROWS,
    C7_ROWS,
    C9_DIST_ROWS,
    DISTANCE_SHEETS,
    DISTANCE_TEMPLATE_ROWS,
    TRANSPORT_HEADERS,
    TRANSPORT_ROWS,
    TRAVEL_COMMUTE_HEADERS,
    TRAVEL_COMMUTE_ROWS,
    build_distance_inputs,
    build_distance_transport_inputs,
)

HEADER_ROWS = 3


# ── 거리판 입력 ──
def test_distance_template_has_only_the_four_distance_sheets_with_rows(tmp_path):
    wb = load_workbook(build_distance_inputs(tmp_path).template, read_only=True)
    try:
        assert wb.sheetnames == ["C4-1_운송거리", "C6-1_출장거리", "C7_임직원통근", "C9-1_운송거리"]
        assert [len(list(wb[n].iter_rows())) - HEADER_ROWS for n in wb.sheetnames] == [
            len(C4_DIST_ROWS), len(C6_DIST_ROWS), len(C7_ROWS), len(C9_DIST_ROWS)]  # fmt: skip
    finally:
        wb.close()


def test_distance_factor_book_adds_transport_and_travel_sheets(tmp_path):
    wb = load_workbook(build_distance_inputs(tmp_path).factor_book, read_only=True)
    try:
        assert wb.sheetnames == ["구매_USEPA", "운송_Spend", "출장_Spend", "폐기물_C5", "운송", "출장_통근"]
        header = {n: list(wb[n].iter_rows(min_row=2, max_row=2, values_only=True))[0] for n in ("운송", "출장_통근")}
    finally:
        wb.close()
    assert header["운송"] == TRANSPORT_HEADERS
    assert header["출장_통근"] == TRAVEL_COMMUTE_HEADERS


# 이름 칸: C4-1·C9-1(국가·사업장·운송수단·자재명/제품명·출발지·도착지), C6-1·C7(국가·출장주체/사업장·교통수단·출발지·도착지).
# 중량단위는 코랩 규칙이 읽는 단위어(ton·kg) — 사전 밖 단위 갈래의 `가상팔레트` 만 이름 꼴. 공항 세 글자 꼴 `ZZQ` 는 지은 코드.
_DIST_NAME_COLUMNS = (
    (C4_DIST_ROWS, frozenset({0, 1, 2, 3, 5, 6})),
    (C9_DIST_ROWS, frozenset({0, 1, 2, 3, 5, 6})),
    (C6_DIST_ROWS, frozenset({0, 1, 2, 4, 5})),
    (C7_ROWS, frozenset({0, 1, 2, 4, 5})),
)
_CODES = frozenset({"ZZQ"})


def test_distance_names_and_numbers_are_synthetic(tmp_path):
    names = [v for rows, cols in _DIST_NAME_COLUMNS for row in rows for i, v in enumerate(row) if i in cols and isinstance(v, str)]
    names += [r[0] for r in (*TRANSPORT_ROWS, *TRAVEL_COMMUTE_ROWS)]
    assert len(names) >= 70
    assert [v for v in names if not v.startswith(("가상", "Synthetic")) and v not in _CODES] == []
    inputs = build_distance_inputs(tmp_path)
    numbers: list[float] = []
    for path in (inputs.template, inputs.factor_book):
        wb = load_workbook(path, read_only=True)
        try:
            for ws in wb.worksheets:
                skip = 2 if ws.title in ("운송", "출장_통근") else HEADER_ROWS
                numbers += [v for row in ws.iter_rows(min_row=skip + 1, values_only=True) for v in row
                            if isinstance(v, int | float) and not isinstance(v, bool)]  # fmt: skip
        finally:
            wb.close()
    assert len(numbers) >= 60
    assert [n for n in numbers if not repr(n).lstrip("-0.").startswith("9")] == []


def _colab_text() -> str:
    try:
        path = default_colab_md()
    except FileNotFoundError:
        pytest.skip("받은 읽기판이 없는 자리(received-derived/ 밖)")
    if not path.is_file():
        pytest.skip(f"받은 읽기판이 없다: {path}")
    return path.read_text("utf-8")


_GROUP_HEADERS = ("연간 입력", "월별 ")


def _flat_header(main: tuple[object, ...], sub: tuple[object, ...]) -> list[str]:
    """두 줄 머리 → 표준화 docstring 꼴의 열 목록(별표·「(선택)」 뺌, 그룹 머리는 서브로, 1월~12월 은 하나)."""
    out: list[str] = []
    for m, s in zip(main, sub, strict=True):
        name = str(s) if (m is None or str(m).startswith(_GROUP_HEADERS)) and s is not None else str(m) if m else None
        if name is None:
            continue
        name = name.lstrip("*").replace("(선택)", "").strip()
        if re.fullmatch(r"\d+월", name):
            if name != "1월":
                continue
            name = "1월~12월"
        out.append(name)
    return out


def test_distance_headers_match_the_colab_reading_copy():
    """§3-1: 거리 시트 이름은 `SHEET_PROCESSOR_MAP` 키, 머리는 표준화 docstring(`:5102`·`:5223`·`:5304`·`:5446`)의 「컬럼:」 꼴,
    계수 책 `운송`·`출장_통근` 은 첫 읽기 header_row 1(`:330`·`:334`)과 재읽기 낱말(`:7210`·`:7213`)을 맞춘다."""
    text = _colab_text()
    keys = set(re.findall(r'^\s*"([^"]+)":\s*\(standardize_', text, re.M))
    for name, main, sub in DISTANCE_SHEETS:
        assert name.lower() in keys
        m = re.search(rf'"""{re.escape(name)}[^\n]*\n\s*컬럼:(.*?)\n\s*산정식', text, re.S)
        assert m, name
        doc = [c.replace("(선택)", "").strip() for c in m.group(1).replace("\n", " ").split(",") if c.strip()]
        assert _flat_header(main, sub) == doc, name
    first_read = dict(re.findall(r'_load_ef_sheet\("([^"]+)",\s*header_row=(\d+)', text))
    must = {n: re.findall(r'"([^"]+)"', words)
            for n, words in re.findall(r'_ef_reload_sheet_auto\("([^"]+)", must_contain=\[([^\]]*)\]', text)}  # fmt: skip
    for sheet, headers in (("운송", TRANSPORT_HEADERS), ("출장_통근", TRAVEL_COMMUTE_HEADERS)):
        assert first_read[sheet] == "1"  # 0부터 1 = 둘째 줄(제목 줄 뒤)
        joined = " ".join(headers)
        assert must[sheet] and all(w in joined for w in must[sheet]), sheet
    # 코드·계수 열 고르기(`:422-423` 「표준코드」·「배출계수」, `:452-454` 「표준코드」·「EF」+「kgCO2」)
    assert any("표준코드" in h for h in TRANSPORT_HEADERS) and any("배출계수" in h for h in TRANSPORT_HEADERS)
    assert any("EF" in h and "kgCO2" in h for h in TRAVEL_COMMUTE_HEADERS)


# ── §3-2 가짜 지오코더 ──
GEO = (
    "from geopy.distance import geodesic\n"
    "from geopy.geocoders import Nominatim\n"
    "geolocator = Nominatim(user_agent='가상', timeout=10)\n"
)


def _cells(*sources: str) -> list[CodeCell]:
    return [CodeCell(i, src) for i, src in enumerate(sources, start=1)]


def test_geocode_returns_none_and_records_queries_in_order(tmp_path):
    box = Sandbox([], geocode_none=True)
    ns = run_in_sandbox(_cells(GEO, "a = geolocator.geocode('가상시가, KR')\nb = geolocator.geocode('가상시나')\n"), box, tmp_path, {})
    assert (ns["a"], ns["b"]) == (None, None)
    assert box.geocode_queries == ["가상시가, KR", "가상시나"]
    assert box.aborts == []


def test_geodesic_and_other_nominatim_attributes_still_stop(tmp_path):
    for code in ("geodesic((0, 0), (1, 1))\n", "geolocator.reverse('9, 9')\n"):
        box = Sandbox([], geocode_none=True)
        with pytest.raises(BlockedModuleUsed):
            run_in_sandbox(_cells(GEO, code), box, tmp_path, {})
        assert box.aborts and box.aborts[0].startswith("BlockedModuleUsed")


FAKE_MD_BARE_EXCEPT_GEODESIC = '''\
### 셀 1 · code

```python
from geopy.distance import geodesic
try:
    d = geodesic((0, 0), (1, 1)).km
except:
    d = None
```
'''


def test_stop_swallowed_by_bare_except_is_still_tool_failure(tmp_path):
    # 코랩 `estimate_straight_distance`(`:1214`)는 맨 `except: pass` 로 감싸여 있다 — 삼켜져도 `aborts` 로 도구 실패(2)
    md = tmp_path / "가상_읽기판.md"
    md.write_text(FAKE_MD_BARE_EXCEPT_GEODESIC, encoding="utf-8")
    answers = tmp_path / "answers.synthetic.json"
    answers.write_text(json.dumps({"synthetic": True, "answers": []}), encoding="utf-8")
    code = main(["run", "--input", "distance", "--colab-md", str(md), "--answers", str(answers), "--out", str(tmp_path / "o")])
    assert code == 2
    run = json.loads((tmp_path / "o" / "run.json").read_text("utf-8"))
    assert run["input"] == "distance"
    assert run["aborts"] and "geodesic" in run["aborts"][0]


# ── §3-3 줄 갈래 ──
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


C4_HEADER = ["자재명", "계산방식", "운송금액", "EF(kgCO2e/KRW)", "배출계수단위", "월운송량", "화물중량(kg)", "운송거리(km)",
             "배출계수(거리기반)", "배출량(tCO2e)"]  # fmt: skip
# C4-1(거리) 둘 + C4-2(지출) 하나가 한 결과 시트로 이어진 꼴. 지출 줄에도 「운송거리(km)」 값이 있다(Spend 견줌은 그 칸을 안 봄).
C4_MIXED = [
    C4_HEADER,
    ["가상거리 자재 가", "Distance", None, None, None, 9.1, 9100, 91, 0.0911, 0.07543991],
    ["가상거리 자재 나", "Distance", None, None, None, 9200, 9200, None, 0.915, 0.9],
    ["가상지출 자재 다", "Spend", 9300000, 0.000911, "kgCO2e/KRW", None, None, 99, None, 8.4723],
]


def test_rows_pick_fields_by_branch(tmp_path):
    got = rows_from_result_xlsx(_xlsx(tmp_path / "r.xlsx", {"C4_업스트림운송": C4_MIXED}))
    assert [(r.key, r.branch) for r in got.rows] == [
        (("C4", 1, "가상거리 자재 가"), "Distance"),
        (("C4", 2, "가상거리 자재 나"), "Distance"),
        (("C4", 3, "가상지출 자재 다"), None),
    ]
    assert list(got.rows[0].fields) == [name for name, _, _ in BRANCH_FIELDS[("C4", "Distance")]]
    assert got.rows[0].fields["운송거리(km)"] == "91"
    assert got.rows[1].fields["운송거리(km)"] is None
    # 지출 줄: o2 표 그대로 — 「운송거리(km)」 값이 있어도 칸에 없다
    assert got.rows[2].fields == {"운송금액": "9300000", "EF(kgCO2e/KRW)": "0.000911", "배출계수단위": "kgCO2e/KRW",
                                  "배출량(tCO2e)": "8.4723"}  # fmt: skip
    data = got.to_json()["rows"]
    assert [r.get("branch") for r in data] == ["Distance", "Distance", None]
    assert "branch" not in data[2]  # 지출 줄은 o2 꼴 그대로(키 없음)


def test_branch_same_is_zero_one_distance_cell_changed_is_one(tmp_path):
    colab = rows_from_result_xlsx(_xlsx(tmp_path / "c.xlsx", {"C4_업스트림운송": C4_MIXED}))
    same = rows_from_json({**colab.to_json(), "side": "engine"}, "engine")
    assert compare(colab, same) == []
    changed = [list(r) for r in C4_MIXED]
    changed[1][7] = 92  # 거리 줄 가의 「운송거리(km)」
    other = rows_from_result_xlsx(_xlsx(tmp_path / "e.xlsx", {"C4_업스트림운송": changed}))
    assert [(d.key, d.field, d.colab, d.engine) for d in compare(colab, other)] == [
        (("C4", 1, "가상거리 자재 가"), "운송거리(km)", "91", "92")]  # fmt: skip


def test_spend_row_ignores_distance_cells_and_branch_mismatch_is_one_diff(tmp_path):
    colab = rows_from_result_xlsx(_xlsx(tmp_path / "c.xlsx", {"C4_업스트림운송": C4_MIXED}))
    changed = [list(r) for r in C4_MIXED]
    changed[3][7] = 999  # 지출 줄의 「운송거리(km)」 — Spend 견줌 밖
    changed[3][8] = 0.9  # 지출 줄의 「배출계수(거리기반)」 — Spend 견줌 밖
    other = rows_from_result_xlsx(_xlsx(tmp_path / "e.xlsx", {"C4_업스트림운송": changed}))
    assert compare(colab, other) == []
    data = colab.to_json()
    data["rows"][0].pop("branch")  # 엔진이 거리 줄을 지출 갈래로 냄(b1 dump 꼴)
    engine = rows_from_json({**data, "side": "engine"}, "engine")
    assert [(d.key, d.field, d.colab, d.engine) for d in compare(colab, engine)] == [
        (("C4", 1, "가상거리 자재 가"), "(갈래)", "Distance", "Spend")]  # fmt: skip


def test_unknown_branch_is_format_error(tmp_path):
    bad = [list(r) for r in C4_MIXED]
    bad[1][1] = "가상방식"
    with pytest.raises(RowsFormatError, match="계산방식"):
        rows_from_result_xlsx(_xlsx(tmp_path / "r.xlsx", {"C4_업스트림운송": bad}))
    for sheet, branch in (("C4", "Spend"), ("C7", "Distance"), ("C1", "Distance")):
        data = {"schema": "scope3colab.rows/1", "rows": [{"sheet": sheet, "row": 1, "item": "가상", "branch": branch, "fields": {}}]}
        with pytest.raises(RowsFormatError, match="모르는 갈래"):
            rows_from_json(data, "engine")


# ── §3-4 C7 열쇠 ──
C7_HEADER = ["국가", "이동수단", "교통수단", "평균통근거리(km)", "직원수", "근무일수", "거리왕복계수", "정규화이동수단", "배출계수",
             "배출량(tCO2e)", "계산식"]  # fmt: skip
C7_ROWS_RESULT = [
    C7_HEADER,
    ["가상국", "가상지하철", "가상지하철", 9.9, 9, 18, 2, "subway", 0.0926, 0.29702376, "가상 산식 9.9"],
    ["가상국", "가상승용차", "가상승용차", 122.2, 9, 99, 2, "car", 0.0924, 20.12106096, "가상 산식 122.2"],
]


def test_c7_key_same_changed_missing(tmp_path):
    colab = rows_from_result_xlsx(_xlsx(tmp_path / "c.xlsx", {"C7_통근": C7_ROWS_RESULT}))
    assert [r.key for r in colab.rows] == [("C7", 1, "가상지하철"), ("C7", 2, "가상승용차")]
    assert ITEM_COLUMNS["C7"] == ("이동수단", "교통수단")
    assert list(colab.rows[0].fields) == [name for name, _, _ in SHEET_FIELDS["C7"]]
    assert colab.rows[0].branch is None
    engine = rows_from_json({**colab.to_json(), "side": "engine"}, "engine")
    assert compare(colab, engine) == []
    changed = [list(r) for r in C7_ROWS_RESULT]
    changed[2][7] = "bus"
    other = rows_from_result_xlsx(_xlsx(tmp_path / "e.xlsx", {"C7_통근": changed}))
    assert [(d.key, d.field) for d in compare(colab, other)] == [(("C7", 2, "가상승용차"), "정규화이동수단")]
    missing = Rows("engine", engine.rows[:1], ())
    diffs = compare(colab, missing)
    assert [(d.key, d.field, d.colab, d.engine) for d in diffs] == [(("C7", 2, "가상승용차"), "(줄)", "있음", None)]
    assert "- 견준 칸 C7: 평균통근거리(km)," in render_diff_md(colab, missing, diffs, {})
    with pytest.raises(RowsFormatError, match="견주지 않는 시트 열쇠"):
        rows_from_json({"schema": "scope3colab.rows/1", "rows": [{"sheet": "C10", "row": 1, "item": "가상", "fields": {}}]}, "engine")


def test_engine_without_c7_or_distance_rows_is_a_diff_per_row(tmp_path):
    path = _xlsx(tmp_path / "c.xlsx", {"C4_업스트림운송": C4_MIXED, "C7_통근": C7_ROWS_RESULT})
    colab = rows_from_result_xlsx(path)
    only_spend = Rows("engine", tuple(r for r in colab.rows if r.sheet == "C4" and r.branch is None), ())
    diffs = compare(colab, only_spend)
    assert [(d.key[0], d.key[1], d.field) for d in diffs] == [("C4", 1, "(줄)"), ("C4", 2, "(줄)"), ("C7", 1, "(줄)"), ("C7", 2, "(줄)")]
    md = render_diff_md(colab, only_spend, diffs, {})
    assert "- 엔진 쪽 줄 없음(열쇠): C7" in md
    assert "- 견준 칸 C4 (Distance): 월운송량," in md and "- 견준 칸 C4: 운송금액," in md


# ── §3-5 거리판 답 차례 ──
_JSON_FREE = "변환하라."  # `normalize_city`(`:1044`)만 글 그대로 답한다 — 나머지는 `_call_claude_json` 부름


def test_distance_answers_are_synthetic_json_and_carry_expect():
    data = json.loads(DISTANCE_ANSWERS.read_text("utf-8"))
    assert data["synthetic"] is True
    answers = data["answers"]
    assert len(answers) == 32
    assert all(a.get("expect") for a in answers)
    for a in answers:
        if _JSON_FREE in a["expect"]:
            assert a["text"].startswith("Synthetic ")
        else:
            assert isinstance(json.loads(a["text"]), dict), a  # 올바른 JSON — 아니면 코랩이 같은 물음을 다시 부른다
    assert all(any(w in a["expect"] for w in ("가상", "Synthetic", "ZZQ")) for a in answers)


ASK = (
    "from anthropic import Anthropic\n"
    "client = Anthropic()\n"
    "def ask(p):\n"
    "    return client.messages.create(model='가상', max_tokens=9, messages=[{'role': 'user', 'content': p}]).content[0].text\n"
)


def test_distance_answers_in_order_pass_and_out_of_order_or_extra_stop(tmp_path):
    answers = load_answers(DISTANCE_ANSWERS)
    prompts = [a.expect for a in answers]
    box = Sandbox(answers)
    ns = run_in_sandbox(_cells(ASK, "out = [ask(p) for p in PROMPTS]\n"), box, tmp_path, {"PROMPTS": prompts})
    assert ns["out"] == [a.text for a in answers]
    # 차례가 어긋남(둘째와 셋째를 바꿈) → AnswerMismatch
    swapped = [prompts[0], prompts[2], prompts[1], *prompts[3:]]
    with pytest.raises(AnswerMismatch):
        run_in_sandbox(_cells(ASK, "out = [ask(p) for p in PROMPTS]\n"), Sandbox(answers), tmp_path, {"PROMPTS": swapped})
    # 답이 모자람 — 맨 except 가 삼켜도 aborts 에 남는다
    box = Sandbox(answers)
    code = "out = [ask(p) for p in PROMPTS]\ntry:\n    ask('가상 서른셋째')\nexcept:\n    pass\n"
    run_in_sandbox(_cells(ASK, code), box, tmp_path, {"PROMPTS": prompts})
    assert box.aborts and box.aborts[0].startswith("AnswersExhausted")


def test_expect_mismatch_inside_bare_except_is_recorded(tmp_path):
    box = Sandbox([Answer("Synthetic Ga", expect="변환하라.\n\n    가상시가\n")])
    code = "try:\n    ask('가상 다른 물음')\nexcept:\n    pass\n"
    run_in_sandbox(_cells(ASK, code), box, tmp_path, {})
    assert box.aborts and box.aborts[0].startswith("AnswerMismatch")
    with pytest.raises(AnswersExhausted):
        run_in_sandbox(_cells(ASK, "ask('가상')\n"), Sandbox([]), tmp_path, {})


def test_distance_template_rows_follow_sheet_order():
    assert list(DISTANCE_TEMPLATE_ROWS) == ["C4-1_운송거리", "C6-1_출장거리", "C7_임직원통근", "C9-1_운송거리"]


# ── 감독 처분 4: 운송 갈래(`--input distance-transport`, C4-1·C9-1 만) ──
def test_distance_transport_template_has_only_c4_1_and_c9_1(tmp_path):
    inputs = build_distance_transport_inputs(tmp_path)
    wb = load_workbook(inputs.template, read_only=True)
    try:
        assert wb.sheetnames == ["C4-1_운송거리", "C9-1_운송거리"]
        assert [len(list(wb[n].iter_rows())) - HEADER_ROWS for n in wb.sheetnames] == [len(C4_DIST_ROWS), len(C9_DIST_ROWS)]
    finally:
        wb.close()
    assert inputs.factor_book.parent.name == build_distance_inputs(tmp_path / "d").factor_book.parent.name  # 계수 책은 같은 짓개


def test_distance_transport_answers_are_the_distance_answers_without_c6_c7():
    full = json.loads(DISTANCE_ANSWERS.read_text("utf-8"))["answers"]
    data = json.loads(DISTANCE_TRANSPORT_ANSWERS.read_text("utf-8"))
    assert data["synthetic"] is True
    assert data["answers"] == full[:9] + full[18:]  # C6-1(10~15)·C7(16~18) 몫을 뺌
