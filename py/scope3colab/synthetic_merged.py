"""합본판(S3C-o5, `--input merged-v22`, `sid:MARC-ISSUE-274`): 받은 템플릿 v2.2 의 시트 차례로 기본판·거리판·나머지 판의 줄을 한 장에
두고, 회색 열(배출계수·배출량)을 받은 스무 시트에 다 둔 뒤 v2.2 차이 ①~⑦ 을 ㉯ 엑셀 저장 꼴로 얹는다.

기존 짓개는 import 만 한다 — 줄·머리 글은 `synthetic`(기본·거리·나머지)과 `synthetic_filled`(회색 머리 글·계수 책), v2.2 차이는
`synthetic_v22.Builder(ALL_DIFFS, CACHED)`. 기존 판의 입력 바이트는 그대로다.

받은 v2.2 에서 잰 것은 **구조뿐**이다(esg-app 의 v2.2 사본 — 구조는 받은 것과 같음, 발주서 Q8):
- 시트 차례 26: 기업정보 · C1 · C2 · C3 · C4-1 · C4-2 · C5 · C6-1 · C6-2 · C7 · C8-1~3 · C9-1 · C9-2 · C11 · C12 · C13-1~3 · C14 · C15 ·
  배출계수_드롭다운 · 드롭다운_매핑검토 · 매핑 시트 둘. 드롭다운 시트 둘은 이름과 안내 한 칸만 둔다(받은 목록 값은 옮기지 않음).
- 회색 열은 C15 를 뺀 스무 시트, 자리는 비고(C11·C12 는 「설명/적요(선택)」) 바로 뒤 두 칸 — C14 는 비고 열이 없어 `*연료 단위` 뒤.
  두 줄 머리 시트는 그 두 칸을 주·서브 두 줄로 병합한다(병합은 머리글 줄을 내린 뒤 `finish` 에서 — openpyxl `insert_rows` 는
  병합 범위를 옮기지 않는다). C11·C12 는 받은 꼴처럼 빈 서브 줄을 두고 머리 칸 전부를 두 줄로 병합한다. C14 는 한 줄 머리(병합 없음).
- 회색 머리 글(배출계수): 지출 시트(C1·C2·C4-2·C6-2·C9-2) `배출계수(kgCO2e/원)`, 거리 운송(C4-1·C9-1) `배출계수(tCO2e/ton-km)`,
  출장·통근 거리(C6-1·C7) `배출계수(tCO2e/km)`, 그 밖 `배출계수(tCO2e/단위)`. 배출량은 모두 `배출량(tCO2e)`.
- C15 는 받은 꼴처럼 비고 열이 없고 Scope2 뒤에 `산정 배출량(tCO2e)` 수식 열이 있다(`synthetic_v22.investment_formulas`).

받은 머리 글과 다른 채 둔 것(기존 판 꼴 그대로 — README 「합본판」): 열은 A 부터(받은 것은 B), C8-1·C13-1 의 `*단위`(받은 것은 서브 줄)·C13-1 비고(받은 것은 서브 줄),
서브 연간 열의 별표(받은 C8-1 `*연간사용량`·면적·인원 `*건물전체 연간사용량`), C15 첫 머리의 별표.
"""

from pathlib import Path

from openpyxl import Workbook

from scope3colab import synthetic as syn
from scope3colab import synthetic_filled as filled
from scope3colab import synthetic_v22 as v22

MERGED_FACTOR_BOOK_DIR = "factors-merged"
MERGED_TEMPLATE_NAME = "synthetic_scope3_merged_v22_template.xlsx"
PER_KRW = filled.KG_PER_KRW
PER_TON_KM = "배출계수(tCO2e/ton-km)"
PER_KM = "배출계수(tCO2e/km)"
PER_UNIT = "배출계수(tCO2e/단위)"
DROPDOWN_SHEETS = ("배출계수_드롭다운", "드롭다운_매핑검토")

type Row = tuple[syn.Cell, ...]
type Sheet = tuple[str, tuple[syn.Cell, ...], tuple[syn.Cell, ...] | None, str | None, tuple[Row, ...]]

_D = syn._DIST_MAIN
_DS = syn._DIST_SUB
C14_MAIN = syn.C14_MAIN[:-1]  # 비고 열 없음(받은 꼴)
C14_ROWS = tuple(r[:-1] for r in syn.C14_ROWS)
C15_MAIN = (*syn.C15_MAIN[:-1], v22.INVESTMENT_RESULT)  # 비고 열 대신 산정 배출량(받은 꼴)
C15_ROWS = tuple((*r[:-1], None) for r in syn.C15_ROWS)

# (시트, 주 머리, 서브 머리 — None 이면 한 줄 머리, 배출계수 머리 글 — None 이면 회색 열 없음, 줄). 받은 v2.2 차례.
MERGED_TEMPLATE: tuple[Sheet, ...] = (
    (syn.C1_SHEET, syn.C1_MAIN, syn.C1_SUB, PER_KRW, syn.C1_ROWS),
    (syn.C2_SHEET, syn.C2_MAIN, syn.C2_SUB, PER_KRW, syn.C2_ROWS),
    (syn.C3_SHEET, syn.C3_MAIN, syn.C3_SUB, PER_UNIT, syn.C3_ROWS),
    (syn.C4_DIST_SHEET, _D[syn.C4_DIST_SHEET], _DS[syn.C4_DIST_SHEET], PER_TON_KM, syn.C4_DIST_ROWS),
    (syn.C4_SPEND_SHEET, syn.C4_SPEND_MAIN, syn.C4_SPEND_SUB, PER_KRW, syn.C4_SPEND_ROWS),
    (syn.C5_SHEET, syn.C5_MAIN, syn.C5_SUB, PER_UNIT, syn.C5_ROWS),
    (syn.C6_DIST_SHEET, _D[syn.C6_DIST_SHEET], _DS[syn.C6_DIST_SHEET], PER_KM, syn.C6_DIST_ROWS),
    (syn.C6_SPEND_SHEET, syn.C6_SPEND_MAIN, syn.C6_SPEND_SUB, PER_KRW, syn.C6_SPEND_ROWS),
    (syn.C7_SHEET, _D[syn.C7_SHEET], _DS[syn.C7_SHEET], PER_KM, syn.C7_ROWS),
    (syn.C8_ENERGY_SHEET, syn.C8_ENERGY_MAIN, syn.ENERGY_SUB, PER_UNIT, syn.C8_ENERGY_ROWS),
    (syn.C8_AREA_SHEET, syn.C8_AREA_MAIN, syn.SHARE_SUB, PER_UNIT, syn.C8_AREA_ROWS),
    (syn.C8_PERSON_SHEET, syn.C8_PERSON_MAIN, syn.SHARE_SUB, PER_UNIT, syn.C8_PERSON_ROWS),
    (syn.C9_DIST_SHEET, _D[syn.C9_DIST_SHEET], _DS[syn.C9_DIST_SHEET], PER_TON_KM, syn.C9_DIST_ROWS),
    (syn.C9_SPEND_SHEET, syn.C9_SPEND_MAIN, syn.C4_SPEND_SUB, PER_KRW, syn.C9_SPEND_ROWS),
    (syn.C11_SHEET, syn.C11_MAIN, (None,) * len(syn.C11_MAIN), PER_UNIT, syn.C11_ROWS),  # 빈 서브 줄 — 두 줄 병합 머리(받은 꼴)
    (syn.C12_SHEET, syn.C12_MAIN, (None,) * len(syn.C12_MAIN), PER_UNIT, syn.C12_ROWS),
    (syn.C13_ENERGY_SHEET, syn.C13_ENERGY_MAIN, syn.ENERGY_SUB, PER_UNIT, syn.C13_ENERGY_ROWS),
    (syn.C13_AREA_SHEET, syn.C13_AREA_MAIN, syn.SHARE_SUB, PER_UNIT, syn.C13_AREA_ROWS),
    (syn.C13_PERSON_SHEET, syn.C13_PERSON_MAIN, syn.SHARE_SUB, PER_UNIT, syn.C13_PERSON_ROWS),
    (syn.C14_SHEET, C14_MAIN, None, PER_UNIT, C14_ROWS),
    (syn.C15_SHEET, C15_MAIN, None, None, C15_ROWS),
)  # fmt: skip
GRAY_SHEETS = tuple(name for name, _, _, ef, _ in MERGED_TEMPLATE if ef is not None)  # 스물
TWO_ROW_GRAY = {name: len(main) + 1 for name, main, sub, ef, _ in MERGED_TEMPLATE if ef is not None and sub is not None}
# 서브 줄이 비어 머리 칸마다 두 줄로 병합하는 시트(받은 v2.2 C11·C12 꼴 — 지어낸 v2.1 꼴 나머지 판은 한 줄 머리)
FULL_MERGE = {syn.C11_SHEET: len(syn.C11_MAIN), syn.C12_SHEET: len(syn.C12_MAIN)}


class MergedBuilder(v22.Builder):
    """v2.2 차이를 얹은 뒤(머리글 줄이 제자리에 온 뒤) 두 줄 머리 시트의 회색 머리 두 칸을 주·서브로 병합한다."""

    def finish(self, wb: Workbook) -> None:
        for name, col in TWO_ROW_GRAY.items():
            ws = wb[name]
            h = v22._header_row(ws)
            for c in (*range(1, FULL_MERGE.get(name, 0) + 1), col, col + 1):
                ws.merge_cells(start_row=h, start_column=c, end_row=h + 1, end_column=c)


def build_merged_template(path: Path) -> Path:
    """받은 v2.2 차례의 시트 스물하나(기업정보·드롭다운·매핑 시트는 아래·`Builder` 가 더함) + 회색 열, 그 위에 v2.2 ①~⑦(㉯)."""
    wb = Workbook()
    default = wb.active
    assert default is not None
    wb.remove(default)
    for name, main, sub, ef, rows in MERGED_TEMPLATE:
        gray = (ef, filled.EMISSION) if ef is not None else ()
        ws = wb.create_sheet(name)
        ws.append([f"카테고리 안내(가상) — {name}"])
        ws.append([*main, *gray])
        if sub is not None:
            ws.append([*sub, *([None] * len(gray))])
        for row in rows:
            ws.append(list(row))
    for name in DROPDOWN_SHEETS:
        wb.create_sheet(name).append([f"가상 안내 — {name}"])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    MergedBuilder(v22.ALL_DIFFS, v22.CACHED).apply(path)
    return path


def build_merged_inputs(out_dir: Path) -> syn.Inputs:
    """`<out_dir>/factors-merged/배출계수_통합_DB.xlsx`(회색 칸 판의 계수 책) 와 `<out_dir>/synthetic_scope3_merged_v22_template.xlsx`."""
    return syn.Inputs(
        filled.build_filled_factor_book(out_dir / MERGED_FACTOR_BOOK_DIR / syn.FACTOR_BOOK_NAME),
        build_merged_template(out_dir / MERGED_TEMPLATE_NAME),
    )
