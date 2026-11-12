"""엑셀 원천 → 랩 문서 모델 다리(발주서 MB2 §2-2). 랩 대사(`ts/reconcile`)의 문서 a 로 쓴다. 표준 라이브러리만.

원천 행(`xpdf.read_xlsx` 꼴, 머리 13열 `xpdf.HEADER`) → 문서 모델 `schema_version "0.1"`: 쪽 1, 블록 둘(제목 heading ·
표 `p1-t1`), 표 cells = 머리 행 + 행마다 글자. 머리 열 0~7(ID~단위)은 이름이라 값 후보가 아니다(랩 `extractDocument` 가
머리 칸의 숫자를 가린다 — 경계 0 실측 ★2). 메모·핵심데이터(열 11·12)에 숫자가 있으면 값 후보(`table_text_cell`)가 생기므로
원천 행의 두 칸은 비워 둔다(KB 원천·xcopy 원천 모두 빈칸).
`source.sha256` 은 `xpdf.canonical_rows` 의 sha256 이다(엑셀 바이트는 zip 시각 때문에 지을 때마다 다르다).
"""
from __future__ import annotations

import hashlib
from typing import Any

from evalrig.dataverify import xpdf

TABLE_ID = "p1-t1"
TITLE = "원천 데이터 — 시트 data"
YEARS = ("2023", "2024", "2025")
YEAR_COL = {y: xpdf.HEADER.index(y) for y in YEARS}  # 8·9·10
HEADER_COLS = list(range(xpdf.HEADER.index("단위") + 1))  # 0~7


def cell_text(v: Any) -> str:
    """값 글자: 정수는 `str(int)`, 소수는 `repr(float)`, 없음은 「」."""
    if v is None or v == "":
        return ""
    if isinstance(v, bool):  # pragma: no cover — 원천에 없음
        raise ValueError("bool 값")
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    return str(v)


def _row_cells(r: dict[str, Any]) -> list[str]:
    out = []
    for h in xpdf.HEADER:
        if h in YEARS:
            out.append(cell_text(r["values"].get(h)))
        else:
            out.append(cell_text(r.get(h, "")))
    return out


def to_docmodel(rows: list[dict[str, Any]], *, file_name: str) -> dict[str, Any]:
    cells = [list(xpdf.HEADER)] + [_row_cells(r) for r in rows]
    sha = hashlib.sha256(xpdf.canonical_rows(rows).encode("utf-8")).hexdigest()
    n = len(cells)
    return {
        "schema_version": "0.1",
        "source": {"file_name": file_name, "sha256": sha, "page_count": 1},
        "parser": {"name": "evalrig.dataverify.xsrc", "version": "1", "table_strategy": "source", "llm_used": False,
                   "ocr_used": False},
        "pages": [{"page": 1, "width": 1000.0, "height": 1000.0 + 20.0 * n, "text_layer": "present",
                   "char_count": sum(len(c) for row in cells for c in row), "image_coverage": 0.0, "columns": 1,
                   "notes": []}],
        "sections": [{"id": "s1", "title": TITLE, "level": 1, "page_start": 1, "page_end": 1}],
        "blocks": [
            {"id": "p1-b1", "page": 1, "order": 1, "section_id": "s1", "kind": "heading", "bbox": [0.0, 0.0, 500.0, 20.0],
             "text": TITLE, "font_size": 14.0},
            {"id": "p1-b2", "page": 1, "order": 2, "section_id": "s1", "kind": "table",
             "bbox": [0.0, 40.0, 1000.0, 40.0 + 20.0 * n], "table_id": TABLE_ID},
        ],
        "tables": [{
            "id": TABLE_ID, "page": 1, "bbox": [0.0, 40.0, 1000.0, 40.0 + 20.0 * n], "n_rows": n,
            "n_cols": len(xpdf.HEADER), "header": {"rows": [0], "cols": list(HEADER_COLS), "basis": "source"},
            "method": "lines", "status": "ok", "notes": [], "footnote_marks": [], "cells": cells,
        }],
    }


def cell_map(rows: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    """{"p1-t1:r{i}c{j}": (ID, 연도)} — 값이 있는 연도 칸만(행 i 는 머리 행 0 다음부터 1)."""
    out: dict[str, tuple[str, str]] = {}
    for i, r in enumerate(rows, start=1):
        for y, j in YEAR_COL.items():
            if cell_text(r["values"].get(y)) != "":
                out[f"{TABLE_ID}:r{i}c{j}"] = (r["ID"], y)
    return out


def unplant(rows: list[dict[str, Any]], plants: dict[str, Any]) -> list[dict[str, Any]]:
    """심기 전 원천 — 같은 행·같은 칸에 심은 값만 `original`(보고서 글자)로 되돌린다(critic R1: cli7 `--a` 의 공정한 원본).
    report_only(원천에서 뺀 행)·source_only(원천에만 있는 행)는 행 구성이라 그대로 둔다 — 칸 ID 가 시험지와 같아야 한다."""
    back = {(p["indicator"], p["period"]): p["original"] for p in plants["plants"]
            if p["kind"] not in ("report_only", "source_only")}
    out = []
    for r in rows:
        vals = dict(r["values"])
        for y in list(vals):
            if (r["ID"], y) in back:
                vals[y] = xpdf.parse_raw(back[(r["ID"], y)])
        out.append({**r, "values": vals})
    return out
