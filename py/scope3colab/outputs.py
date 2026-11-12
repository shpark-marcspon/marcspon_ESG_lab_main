"""코랩이 내는 출력 파일 둘(`run_scope3_pipeline` 셀 30 `:9476`)의 꼴 대조(S3C-o4b). 줄·칸 값 대조(`rows.py`)와 따로 돈다.

㉠ `result_file`(`<base>_result.xlsx`) — **꼴만** 본다(값은 `rows.py` 몫):
- 시트 이름과 차례. 코랩은 Category 가 `results` 에 처음 들어간 차례, 곧 템플릿 시트 차례로 쓴다(셀 22 `:6195`·`:6237`).
- 시트마다 열 이름과 차례. 같은 Category 의 템플릿 시트 frame 을 `pd.concat`(`:6238`)하니 열은 **처음 나온 차례의 합집합**이다
  (C6-1 + C6-2 → `C6_출장`). 그 뒤 `COLS_TO_DROP`(`:6223`)과 전부 빈 열(`:6243`)을 지운다. 결과 후처리
  `_postprocess_scope3_result_file`(`:8878`)가 C1_·C2_·C6_·C7_·C11_·C14_ 시트를 다시 써 열이 늘 수 있다.
- 줄 수.

㉡ `filled_template_file`(`<base>_filled_template.xlsx`, `build_filled_template` `:9355`) — 원본 템플릿과 함께 본다:
- 회색 칸: 원본 템플릿 시트마다 `_find_ef_em_columns`(`:9216`) 규칙으로 찾은 배출계수 열·배출량 열의 머리 아래 칸. 두 쪽 값을 견준다
  (숫자는 `Decimal` 같음 — 허용 0, 빈 칸은 null).
- 회색 칸 밖: 칸마다 원본과 같은지만 센다(코랩 「원본 형식 유지」 `:9182` — 어느 쪽이든 원본 칸을 바꾸면 차이). 수식 칸은
  수식 글자를 견준다(openpyxl 로 다시 쓴 파일은 저장 값이 비니 저장 값은 보지 않는다 — 수식 칸을 값으로 바꾸면 차이).
- 시트 이름과 차례(원본과 같아야 함). 서식·병합·드롭다운은 보지 않는다.

견줌 단위는 차이 한 줄(`OutputDiff`)이다. 산출 꼴(`outputs.json`, `scope3colab.outputs/1`)은 아래 `to_json`.
"""

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

SCHEMA = "scope3colab.outputs/1"
RESULT, FILLED = "result", "filled"
MAX_SCAN_ROW = 30  # `_find_ef_em_columns`·`_find_note_column` 의 max_scan_row
# 템플릿 시트 이름 → 결과 시트 접두(`_match_sheet_processor` 의 Category → `_OUT_SHEET_NAME_MAP` `:9337`). `C1_`·`C4-1_` 꼴의 머리.
_SHEET_KEY = re.compile(r"^(C\d+)[-_]")


@dataclass(frozen=True)
class OutputDiff:
    file: str  # "result" | "filled"
    where: str  # 시트 이름, 또는 `시트!칸`
    what: str  # "(시트)"·"(시트 차례)"·"(열)"·"(줄 수)"·"회색 칸"·"회색 칸 밖(colab|engine)"·"수식 칸(colab|engine)"
    left: str | None  # ㉠·회색 칸: 코랩 쪽. 회색 칸 밖·수식 칸: 원본
    right: str | None  # ㉠·회색 칸: 엔진 쪽. 회색 칸 밖·수식 칸: 그 쪽 파일


# ── ㉠ result_file ──────────────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SheetShape:
    sheet: str
    columns: tuple[str, ...]
    rows: int


def result_shape(path: Path) -> tuple[SheetShape, ...]:
    """시트마다 (이름, 첫 줄의 열 이름, 머리 아래 줄 수)."""
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        shapes = []
        for ws in wb.worksheets:
            values = list(ws.iter_rows(values_only=True))
            header = tuple("" if h is None else str(h) for h in values[0]) if values else ()
            shapes.append(SheetShape(ws.title, header, max(len(values) - 1, 0)))
        return tuple(shapes)
    finally:
        wb.close()


def compare_result_files(colab: Path, engine: Path) -> list[OutputDiff]:
    """빠진·더한 시트마다 하나, 같이 있는 시트의 차례가 다르면 하나, 시트마다 열 목록(이름·차례)이 다르면 하나, 줄 수가 다르면 하나."""
    left = {s.sheet: s for s in result_shape(colab)}
    right = {s.sheet: s for s in result_shape(engine)}
    diffs = [OutputDiff(RESULT, name, "(시트)", "있음", None) for name in left if name not in right]
    diffs += [OutputDiff(RESULT, name, "(시트)", None, "있음") for name in right if name not in left]
    common_l = [n for n in left if n in right]
    common_r = [n for n in right if n in left]
    if common_l != common_r:
        diffs.append(OutputDiff(RESULT, "", "(시트 차례)", " · ".join(common_l), " · ".join(common_r)))
    for name in common_l:
        a, b = left[name], right[name]
        if a.columns != b.columns:
            diffs.append(OutputDiff(RESULT, name, "(열)", " · ".join(a.columns), " · ".join(b.columns)))
        if a.rows != b.rows:
            diffs.append(OutputDiff(RESULT, name, "(줄 수)", str(a.rows), str(b.rows)))
    return diffs


# ── ㉡ filled_template_file ─────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GrayColumns:
    """`_find_ef_em_columns`(`:9216`) 규칙으로 찾은 회색 열. 열 번호는 1부터."""

    header_end: int  # 머리 마지막 줄(머리 칸이 병합이면 병합의 끝 줄)
    ef_col: int
    em_col: int
    ef_header: str


def find_gray_columns(ws: Worksheet) -> GrayColumns | None:
    """같은 줄에 「배출계수」 글 칸과 「배출량」 글 칸이 있는 첫 줄(1~30). 한 줄 안에서는 **마지막** 칸이 이긴다(코랩 고리 그대로),
    「배출계수」 와 「배출량」 이 함께 든 글은 배출계수 칸이다(`elif`)."""
    for r in range(1, min(MAX_SCAN_ROW, ws.max_row) + 1):
        ef = em = None
        ef_text = ""
        for c in range(1, ws.max_column + 1):
            v = ws.cell(r, c).value
            if isinstance(v, str):
                if "배출계수" in v:
                    ef, ef_text = c, v
                elif "배출량" in v:
                    em = c
        if ef and em:
            end = r
            for col in (ef, em):
                coord = ws.cell(r, col).coordinate
                for mc in ws.merged_cells.ranges:
                    if coord in mc:
                        end = max(end, mc.max_row)
            return GrayColumns(end, ef, em, ef_text)
    return None


def ef_scale(header: str) -> str:
    """`_ef_unit_scale_factor`(`:9245`): 띄어쓰기 지운 소문자에 kgco2e(q) 가 있으면 1, 아니고 tco2e(q) 가 있으면 1/1000, 그 밖은 1.
    코랩은 배출계수에 `1.0 / 1000.0` 을 **곱한다**(1000 으로 나누기와 끝자리가 다를 수 있다 — 0.0941 × (1/1000) = 9.410000000000001e-05)."""
    t = header.replace(" ", "").lower()
    if "kgco2e" in t:  # kgco2eq 도 여기 걸린다
        return "1"
    if "tco2e" in t:
        return "1/1000"
    return "1(그 밖)"


def find_note_column(ws: Worksheet, max_scan_row: int) -> int | None:
    """`_find_note_column`(`:9264`): 1~max_scan_row 줄에서 15자 이하이고 「비고」·「설명」·「적요」 가 든 첫 글 칸. 코랩은
    `max_scan_row` 에 머리 마지막 줄을 넘긴다(`:9407`)."""
    for r in range(1, min(max_scan_row, ws.max_row) + 1):
        for c in range(1, ws.max_column + 1):
            v = ws.cell(r, c).value
            if isinstance(v, str) and len(v.strip()) <= 15 and ("비고" in v or "설명" in v or "적요" in v):
                return c
    return None


def data_rows(ws: Worksheet, header_end: int, scan_max_col: int) -> list[int]:
    """`_find_data_rows`(`:9277`): 머리 아래에서 1~scan_max_col 열에 값이 하나라도 든 줄."""
    out = []
    for r in range(header_end + 1, ws.max_row + 1):
        if any(v is not None and str(v).strip() != "" for v in (ws.cell(r, c).value for c in range(1, scan_max_col + 1))):
            out.append(r)
    return out


def cell_text(value: Any) -> str | None:
    """견줌 글: 빈 칸·빈 글은 null, 수는 `Decimal` 글(float 는 `repr`), 시각은 ISO, 그 밖은 글 그대로(수식은 `=…`)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return None if value != value else str(Decimal(repr(value)))
    if isinstance(value, int | Decimal):
        return str(Decimal(value))
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    text = str(value)
    return text if text.strip() else None


def _same(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return a is b
    try:
        return Decimal(a) == Decimal(b)
    except ArithmeticError:
        return a == b


def _is_formula(text: str | None) -> bool:
    return text is not None and text.startswith("=")




def _load(path: Path) -> Workbook:
    return load_workbook(path, data_only=False)  # 수식은 글자(`=…`)로


def compare_filled_files(template: Path, colab: Path, engine: Path) -> list[OutputDiff]:
    """회색 칸은 코랩 ↔ 엔진, 회색 칸 밖은 원본 ↔ 각 쪽. 회색 칸은 원본의 마지막 줄 밖으로도 두 쪽 끝 줄까지 본다."""
    tpl, left, right = _load(template), _load(colab), _load(engine)
    diffs: list[OutputDiff] = []
    names = tpl.sheetnames
    for side, wb in (("colab", left), ("engine", right)):
        if wb.sheetnames != names:
            diffs.append(OutputDiff(FILLED, "", f"(시트)({side})", " · ".join(names), " · ".join(wb.sheetnames)))
    for name in names:
        if name not in left.sheetnames or name not in right.sheetnames:
            continue
        t, a, b = tpl[name], left[name], right[name]
        cols = find_gray_columns(t)  # 회색 칸 = 머리 아래 회색 열의 칸 전부(원본·두 쪽 가운데 마지막 줄까지)
        if cols is not None:
            last = max(t.max_row, a.max_row, b.max_row)
            for r in range(cols.header_end + 1, last + 1):
                for c in (cols.ef_col, cols.em_col):
                    x, y = cell_text(a.cell(r, c).value), cell_text(b.cell(r, c).value)
                    if not _same(x, y):
                        diffs.append(OutputDiff(FILLED, f"{name}!{get_column_letter(c)}{r}", "회색 칸", x, y))
        for side, ws in (("colab", a), ("engine", b)):
            rows = max(t.max_row, ws.max_row)
            width = max(t.max_column, ws.max_column)
            for r in range(1, rows + 1):
                for c in range(1, width + 1):
                    if cols is not None and r > cols.header_end and c in (cols.ef_col, cols.em_col):
                        continue
                    want, got = cell_text(t.cell(r, c).value), cell_text(ws.cell(r, c).value)
                    if want != got:
                        kind = "수식 칸" if _is_formula(want) else "회색 칸 밖"
                        diffs.append(OutputDiff(FILLED, f"{name}!{get_column_letter(c)}{r}", f"{kind}({side})", want, got))
    return diffs


# ── 코랩 쪽 실측(보고·S3C-c2 표) ──────────────────────────────────────────────────────────────────────────────────


def result_sheet_for(template_sheet: str, shapes: tuple[SheetShape, ...]) -> SheetShape | None:
    m = _SHEET_KEY.match(template_sheet)
    if m is None:
        return None
    return next((s for s in shapes if s.sheet.startswith(f"{m.group(1)}_")), None)


def filled_report(template: Path, filled: Path, result: Path) -> list[dict[str, Any]]:
    """원본 템플릿 시트마다: 회색 열(없으면 null)·배율·비고 열·줄 짝 길(비고/위치)·회색 칸 수(데이터 줄 × 2)·채운 칸 수(원본에서
    빈 회색 칸 가운데 채워진 것)·수식 칸 수·수식 글자가 원본과 다른 칸 수. 줄 짝 길은 원본 시트의 비고 열과 결과 시트의
    비고 열(이름에 「비고」·「설명」·「적요」 — `_find_result_note_column` `:9328`)이 둘 다 있을 때 「비고」."""
    tpl, out = _load(template), _load(filled)
    shapes = result_shape(result)
    report = []
    for ws in tpl.worksheets:
        filled_ws = out[ws.title] if ws.title in out.sheetnames else None
        formulas = [(r, c) for r in range(1, ws.max_row + 1) for c in range(1, ws.max_column + 1)
                    if _is_formula(cell_text(ws.cell(r, c).value))]  # fmt: skip
        changed = sum(
            1 for r, c in formulas if filled_ws is None or cell_text(filled_ws.cell(r, c).value) != cell_text(ws.cell(r, c).value)
        )
        entry: dict[str, Any] = {"sheet": ws.title, "gray": None, "formula_cells": len(formulas), "formula_changed": changed}
        gray = find_gray_columns(ws)
        if gray is not None:
            rows = data_rows(ws, gray.header_end, gray.em_col)
            note_ws = find_note_column(ws, gray.header_end)
            target = result_sheet_for(ws.title, shapes)
            note_df = None if target is None else next((c for c in target.columns if "비고" in c or "설명" in c or "적요" in c), None)
            filled_count = 0
            if filled_ws is not None:
                for r in range(gray.header_end + 1, ws.max_row + 1):
                    for c in (gray.ef_col, gray.em_col):
                        if cell_text(ws.cell(r, c).value) is None and cell_text(filled_ws.cell(r, c).value) is not None:
                            filled_count += 1
            entry["gray"] = {
                "header_end": gray.header_end,
                "ef": f"{get_column_letter(gray.ef_col)} {gray.ef_header}",
                "em": get_column_letter(gray.em_col),
                "scale": ef_scale(gray.ef_header),
                "note_column": None if note_ws is None else get_column_letter(note_ws),
                "result_sheet": None if target is None else target.sheet,
                "result_note_column": note_df,
                "pairing": "비고" if note_ws and note_df else "위치",
                "data_rows": len(rows),
                "gray_cells": 2 * len(rows),
                "filled_cells": filled_count,
            }
        report.append(entry)
    return report


def to_json(result: Path, template: Path, filled: Path | None, diffs: list[OutputDiff] | None) -> dict[str, Any]:
    """`outputs.json`: 코랩 결과 파일 꼴·회색 칸 실측, 엔진 파일을 받았으면 차이. 경로·시각을 싣지 않는다(같은 입력이면 같은 바이트)."""
    data: dict[str, Any] = {
        "schema": SCHEMA,
        "result": [{"sheet": s.sheet, "columns": list(s.columns), "rows": s.rows} for s in result_shape(result)],
        "filled": None if filled is None else filled_report(template, filled, result),
    }
    if diffs is not None:
        data["diffs"] = [asdict(d) for d in diffs]
    return data


def _md(value: str | None) -> str:
    return "—" if value is None else value.replace("|", "\\|").replace("\n", "↵")


def render_outputs_md(data: dict[str, Any], engine: dict[str, str]) -> str:
    lines = ["# Scope 3 코랩 출력 파일 둘 — 꼴 대조", ""]
    lines += [f"- 결과 파일 시트: " + " · ".join(f"{s['sheet']}({s['rows']}줄 {len(s['columns'])}열)" for s in data["result"])]
    for f in data["filled"] or ():
        g = f["gray"]
        if g is None:
            lines.append(f"- 회색 칸 {f['sheet']}: 없음(배출계수·배출량 머리 없음) · 수식 칸 {f['formula_cells']}(바뀜 {f['formula_changed']})")
        else:
            lines.append(
                f"- 회색 칸 {f['sheet']}: 머리 끝 {g['header_end']}줄 · {g['ef']} / {g['em']} · 배율 {g['scale']} · "
                f"{g['pairing']} 짝 · 회색 {g['gray_cells']} · 채움 {g['filled_cells']} · 수식 칸 {f['formula_cells']}(바뀜 {f['formula_changed']})"
            )
    lines += [f"- 엔진 {k}: {v}" for k, v in engine.items()]
    lines += ["", "## 결과", ""]
    if "diffs" not in data:
        lines += ["엔진 출력 파일 없음(`--engine-result-file`·`--engine-filled-file` 없이 돌림) — 코랩 쪽만 냈다(`outputs.json`).", ""]
        return "\n".join(lines)
    diffs = data["diffs"]
    if not diffs:
        lines += ["같음 — 차이 0.", ""]
        return "\n".join(lines)
    lines += [f"차이 {len(diffs)}줄.", "", "| 파일 | 자리 | 무엇 | 왼쪽 | 오른쪽 |", "|---|---|---|---|---|"]
    lines += [f"| {d['file']} | {_md(d['where'])} | {d['what']} | {_md(d['left'])} | {_md(d['right'])} |" for d in diffs]
    lines.append("")
    return "\n".join(lines)
