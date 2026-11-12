"""S3C-o2 새 열쇠 C4·C5·C6·C9: 합친 결과 시트의 row 이어 세기(§3-2), item 열 고르기(§3-3), 새 열쇠 견줌(§3-4).

결과 엑셀은 코랩 결과 꼴을 흉내 낸 작은 지어낸 표다(openpyxl). 받은 코랩은 돌리지 않는다.
"""
import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from scope3colab.cli import main
from scope3colab.compare import compare, render_diff_md
from scope3colab.rows import (
    ITEM_COLUMNS,
    SHEET_FIELDS,
    Row,
    Rows,
    RowsFormatError,
    rows_from_json,
    rows_from_result_xlsx,
)


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


C4_HEADER = ["국가", "자재명", "운송금액", "배출량(tCO2e)", "EF(kgCO2e/KRW)", "배출계수단위"]
# C4-1(거리) 둘 + C4-2(지출) 둘이 코랩에서 한 결과 시트 `C4_업스트림운송` 으로 이어 붙은 꼴
C4_MERGED = [
    C4_HEADER,
    ["가상국", "가상거리 자재 가", None, 9.1, None, None],
    ["가상국", "가상거리 자재 나", None, 9.2, None, None],
    ["가상국", "가상지출 자재 다", 9300000, 8.4723, 0.000911, "kgCO2e/KRW"],
    ["가상국", "가상지출 자재 라", 9400000, 8.5634, 0.000911, "kgCO2e/KRW"],
]


def test_merged_result_sheet_counts_rows_through(tmp_path):
    got = rows_from_result_xlsx(_xlsx(tmp_path / "r.xlsx", {"C4_업스트림운송": C4_MERGED}))
    assert [r.key for r in got.rows] == [
        ("C4", 1, "가상거리 자재 가"),
        ("C4", 2, "가상거리 자재 나"),
        ("C4", 3, "가상지출 자재 다"),
        ("C4", 4, "가상지출 자재 라"),
    ]
    assert got.rows[2].fields == {
        "운송금액": "9300000",
        "EF(kgCO2e/KRW)": "0.000911",
        "배출계수단위": "kgCO2e/KRW",
        "배출량(tCO2e)": "8.4723",
    }


def test_each_result_sheet_counts_from_one_and_other_sheets_are_not_compared(tmp_path):
    path = _xlsx(tmp_path / "r.xlsx", {
        "C4_업스트림운송": C4_MERGED[:2],
        "C9_다운스트림운송": [["제품명", "운송금액"], ["가상제품 가", 9100000]],
        "C10_가상가공": [["가상품목"], ["가상연료"]],
        "가상요약": [["임차자산명"], ["가상건물"]],  # S3C-o4a 부터 C3·C8 도 견주는 열쇠라 코랩에 없는 C10·이름을 쓴다
    })  # fmt: skip
    got = rows_from_result_xlsx(path)
    assert [r.key for r in got.rows] == [("C4", 1, "가상거리 자재 가"), ("C9", 1, "가상제품 가")]
    assert got.not_compared == ("C10_가상가공", "가상요약")


def test_item_column_skips_a_column_with_no_values(tmp_path):
    # 「자재명」 열은 있으나 전부 빔 → 다음 후보 「제품명」
    values = [["자재명", "제품명", "운송금액"], [None, "가상제품 가", 9100000], ["", "가상제품 나", 9200000]]
    got = rows_from_result_xlsx(_xlsx(tmp_path / "r.xlsx", {"C4_업스트림운송": values}))
    assert [r.item for r in got.rows] == ["가상제품 가", "가상제품 나"]
    assert ITEM_COLUMNS["C4"] == ("자재명", "제품명")


def test_item_column_missing_everywhere_is_format_error(tmp_path):
    values = [["자재명", "운송금액"], [None, 9100000]]
    with pytest.raises(RowsFormatError, match="품목 열"):
        rows_from_result_xlsx(_xlsx(tmp_path / "r.xlsx", {"C4_업스트림운송": values}))


def test_c5_and_c6_fields_follow_their_own_table(tmp_path):
    path = _xlsx(tmp_path / "r.xlsx", {
        "C5_사업장폐기물": [
            ["폐기물 종류", "처리량", "배출계수(kgCO2eq/kg)", "처리방법_정규화", "화석기원 CO2e(tCO2e)",
             "생물기원 CO2e(tCO2e)", "화석탄소비율(FCF)", "바이오매스 구분", "배출량(tCO2e)"],
            ["가상폐지", 9100, 0.931, "소각", 0.08472100000000005, 8.387379000000001, 0.01, "가상 구분", 0.08472100000000005],
        ],
        "C6_출장": [["이동수단", "출장비용", "EF_DB", "배출계수", "정규화이동수단"],
                    ["가상항공권", 9310000, "Synthetic Flights", 0.000921, "flight"]],
    })  # fmt: skip
    got = {r.sheet: r for r in rows_from_result_xlsx(path).rows}
    assert list(got["C5"].fields) == [name for name, _, _ in SHEET_FIELDS["C5"]]
    assert got["C5"].fields["생물기원 CO2e(tCO2e)"] == "8.387379000000001"
    assert got["C5"].fields["단위환산비고"] is None  # 결과에 없는 열은 null
    assert got["C6"].item == "가상항공권"
    assert got["C6"].fields["EF_DB"] == "Synthetic Flights"
    assert got["C6"].fields["계산식"] is None


def _row(sheet: str, n: int, item: str, **fields: str | None) -> Row:
    base: dict[str, str | None] = {name: None for name, _, _ in SHEET_FIELDS[sheet]}
    base.update(fields)
    return Row(sheet, n, item, base)


def _rows(*rs: Row, side: str = "colab") -> Rows:
    return Rows(side, tuple(rs), ())


C5_ROW = {"처리량": "9100", "배출계수(kgCO2eq/kg)": "0.931", "배출량(tCO2e)": "0.084721", "바이오매스 구분": "가상 구분"}


def test_new_key_same_is_zero_one_changed_cell_is_one():
    colab = _rows(_row("C5", 1, "가상폐지", **C5_ROW), _row("C9", 1, "가상제품 가", 운송금액="9210000"))
    same = _rows(_row("C5", 1, "가상폐지", **{**C5_ROW, "처리량": "9100.0"}), _row("C9", 1, "가상제품 가", 운송금액="9210000"),
                 side="engine")  # fmt: skip
    assert compare(colab, same) == []
    changed = _rows(_row("C5", 1, "가상폐지", **{**C5_ROW, "바이오매스 구분": "가상 다른 구분"}),
                    _row("C9", 1, "가상제품 가", 운송금액="9210000"), side="engine")  # fmt: skip
    assert [(d.key, d.field) for d in compare(colab, changed)] == [(("C5", 1, "가상폐지"), "바이오매스 구분")]


def test_engine_side_without_a_new_key_is_a_diff_per_row():
    colab = _rows(_row("C1", 1, "가상품목 가"), _row("C5", 1, "가상폐지", **C5_ROW), _row("C5", 2, "가상잔반"))
    engine = _rows(_row("C1", 1, "가상품목 가"), side="engine")  # S3C-b0 dump 꼴: C1·C2 만
    diffs = compare(colab, engine)
    assert [(d.key, d.field, d.colab, d.engine) for d in diffs] == [
        (("C5", 1, "가상폐지"), "(줄)", "있음", None),
        (("C5", 2, "가상잔반"), "(줄)", "있음", None),
    ]
    md = render_diff_md(colab, engine, diffs, {})
    assert "- 엔진 쪽 줄 없음(열쇠): C5" in md
    assert "- 견준 칸 C5: 처리량," in md


def test_engine_json_with_unknown_sheet_key_is_format_error():
    data = {"schema": "scope3colab.rows/1", "rows": [{"sheet": "C10", "row": 1, "item": "가상연료", "fields": {}}]}  # C3 은 S3C-o4a 부터 견줌
    with pytest.raises(RowsFormatError, match="견주지 않는 시트 열쇠"):
        rows_from_json(data, "engine")


def test_engine_json_normalizes_by_the_row_sheet_fields():
    data = {"schema": "scope3colab.rows/1", "rows": [
        {"sheet": "C6", "row": 1, "item": "가상항공권", "fields": {"배출계수": 0.000921, "NAICS": 900201, "운송금액": "9"}},
    ]}  # fmt: skip
    fields = rows_from_json(data, "engine").rows[0].fields
    assert list(fields) == [name for name, _, _ in SHEET_FIELDS["C6"]]  # C6 칸만 — 「운송금액」(C4·C9 칸)은 버린다
    assert fields["배출계수"] == "0.000921"
    assert fields["NAICS"] == "900201"


FAKE_MD_NEW_KEYS = '''\
### 셀 1 · code

```python
from openpyxl import Workbook
import os
os.makedirs("result", exist_ok=True)
wb = Workbook()
ws = wb.active
ws.title = "C1_구매품서비스"
ws.append(["품목명", "EF적용금액(KRW)"])
ws.append(["가상품목 가", 9100000])
ws2 = wb.create_sheet("C5_사업장폐기물")
ws2.append(["폐기물 종류", "처리량", "배출계수(kgCO2eq/kg)", "배출량(tCO2e)"])
ws2.append(["가상폐지", 9100, 0.931, 8.4721])
wb.save("result/가상_result.xlsx")
result = {"result_file": "result/가상_result.xlsx"}
```
'''


def test_cli_engine_without_new_key_is_1(tmp_path):
    md = tmp_path / "가상_읽기판.md"
    md.write_text(FAKE_MD_NEW_KEYS, encoding="utf-8")
    answers = tmp_path / "answers.synthetic.json"
    answers.write_text(json.dumps({"synthetic": True, "answers": []}), encoding="utf-8")
    base = ["run", "--colab-md", str(md), "--answers", str(answers)]
    assert main([*base, "--out", str(tmp_path / "first")]) == 0
    colab = json.loads((tmp_path / "first" / "colab.json").read_text("utf-8"))
    assert [(r["sheet"], r["item"]) for r in colab["rows"]] == [("C1", "가상품목 가"), ("C5", "가상폐지")]

    full = tmp_path / "engine-full.json"
    full.write_text(json.dumps({**colab, "side": "engine"}, ensure_ascii=False), "utf-8")
    assert main([*base, "--out", str(tmp_path / "same"), "--engine-result", str(full)]) == 0

    only_c1 = tmp_path / "engine-c1.json"
    only_c1.write_text(json.dumps({**colab, "side": "engine", "rows": colab["rows"][:1]}, ensure_ascii=False), "utf-8")
    assert main([*base, "--out", str(tmp_path / "c1"), "--engine-result", str(only_c1)]) == 1
    assert "엔진 쪽 줄 없음(열쇠): C5" in (tmp_path / "c1" / "diff.md").read_text("utf-8")
