"""MB2 C2 — 엑셀 원천 → 랩 문서 모델 다리(발주서 MB2 §2-2). 행은 지은 값."""
from __future__ import annotations

import copy

from evalrig.dataverify import xpdf, xsrc


def _rows():
    base = {"테마": "환경", "대분류": "지은 대분류", "중분류": "지은 중분류", "입력 방식": "직접 입력", "핵심데이터": "", "메모": ""}
    return [
        {**base, "ID": "Z-001", "항목": "지은 지표 하나", "소분류": "지은 지표 하나", "단위": "톤", "values": {"2023": 1200, "2024": 1180.4}},
        {**base, "ID": "Z-002", "항목": "지은 지표 둘", "소분류": "지은 지표 둘", "단위": "명", "values": {"2024": 41, "2025": 40}},
        {**base, "ID": "Z-003", "항목": "지은 지표 셋", "소분류": "지은 지표 셋", "단위": "%", "values": {"2023": 7.5, "2025": 6}},
    ]


def test_table_text_and_header():
    d = xsrc.to_docmodel(_rows(), file_name="made-up.xlsx")
    t = d["tables"][0]
    assert d["schema_version"] == "0.1" and d["source"]["file_name"] == "made-up.xlsx"
    assert [b["kind"] for b in d["blocks"]] == ["heading", "table"] and d["blocks"][1]["table_id"] == t["id"] == "p1-t1"
    assert t["cells"][0] == xpdf.HEADER
    assert t["cells"][1][8:11] == ["1200", "1180.4", ""]
    assert t["cells"][2][8:11] == ["", "41", "40"]
    assert t["cells"][3][8:11] == ["7.5", "", "6"]
    assert t["cells"][1][:8] == ["Z-001", "환경", "지은 지표 하나", "지은 대분류", "지은 중분류", "지은 지표 하나", "직접 입력", "톤"]
    assert t["header"] == {"rows": [0], "cols": [0, 1, 2, 3, 4, 5, 6, 7], "basis": "source"}
    assert (t["n_rows"], t["n_cols"], t["status"], t["method"]) == (4, 13, "ok", "lines")


def test_cell_map():
    assert xsrc.cell_map(_rows()) == {
        "p1-t1:r1c8": ("Z-001", "2023"), "p1-t1:r1c9": ("Z-001", "2024"),
        "p1-t1:r2c9": ("Z-002", "2024"), "p1-t1:r2c10": ("Z-002", "2025"),
        "p1-t1:r3c8": ("Z-003", "2023"), "p1-t1:r3c10": ("Z-003", "2025"),
    }


def test_sha_deterministic_and_value_sensitive():
    a = xsrc.to_docmodel(_rows(), file_name="x.xlsx")
    b = xsrc.to_docmodel(_rows(), file_name="y.xlsx")
    assert a["source"]["sha256"] == b["source"]["sha256"] and len(a["source"]["sha256"]) == 64
    rows = _rows()
    rows[0]["values"]["2023"] = 1201
    assert xsrc.to_docmodel(rows, file_name="x.xlsx")["source"]["sha256"] != a["source"]["sha256"]


def test_unplant_restores_only_planted_cells():
    rows = _rows()
    planted = copy.deepcopy(rows)
    planted[0]["values"]["2023"] = 1200000
    planted[1]["values"]["2024"], planted[1]["values"]["2025"] = 40, 41
    plants = {"plants": [
        {"kind": "unit_scale", "indicator": "Z-001", "period": "2023", "original": "1,200"},
        {"kind": "year_swap", "indicator": "Z-002", "period": "2024", "original": "41"},
        {"kind": "year_swap", "indicator": "Z-002", "period": "2025", "original": "40"},
        {"kind": "source_only", "indicator": "Z-003", "period": "2023", "original": None},
    ]}
    back = xsrc.unplant(planted, plants)
    assert [r["values"] for r in back] == [r["values"] for r in rows]
    assert xsrc.cell_map(back) == xsrc.cell_map(planted)
