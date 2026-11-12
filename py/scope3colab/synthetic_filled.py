"""회색 칸 판(S3C-o4b, `--input filled`): 코랩 `build_filled_template`(셀 30 `:9355`)이 실제로 칸을 채우는 지어낸 입력.

기존 일곱 판의 템플릿에는 「배출계수」·「배출량」 머리 열이 없어 코랩이 모든 시트를 「배출계수/배출량 칸을 찾지 못함」으로 건너뛴다
(실측 — 채운 칸 0). 그래서 판을 하나 더한다(감독 marcspon-67 10-27, 발주서 판단 요청 o4b-1 「새 판을 만들지 말고」를 갈음). 기존 판의
입력·답 파일·계수 책은 그대로다.

받은 템플릿 v2.1 에서 잰 것은 **구조뿐**이다(회색 열의 자리와 머리 글 — 값·예시 줄은 옮기지 않음):
- 회색 열 둘은 「비고(선택)」 바로 뒤 두 칸(비고 + 1 = 배출계수, + 2 = 배출량)이고, 머리 칸은 주 머리·서브 머리 두 줄 병합이다.
- 머리 글: 지출 시트(C4-2·C6-2·C9-2) `배출계수(kgCO2e/원)` → 배율 1, C6-1 `배출계수(tCO2e/km)`·C14 `배출계수(tCO2e/단위)` → 1/1000
  (`_ef_unit_scale_factor` `:9245`). 배출량은 모두 `배출량(tCO2e)`.
- C14 는 비고 열이 없고 한 줄 머리다(병합 없음) — 회색 열은 `*연료 단위` 바로 뒤.

시트와 노린 갈래(AI 부름 0 — 줄은 기존 판에서 AI 를 부르지 않던 것만 골랐다. 지오코더·geodesic 은 부르면 멈춤):

| 시트 | 줄 | 줄 짝 길 |
|---|---|---|
| C4-2 | 기본판 C4-2 세 줄(2번만 비고) | 비고 길 — 2번은 비고 글로, 비고 빈 1·3번은 남은 결과 줄과 차례대로 |
| C6-1 | 편도거리 있는 버스(연간)·기차(월별), 비고 둘 다 있음 | 비고 길 — 결과에서 월별 줄이 앞이라(녹이기) 위치로 짝지으면 틀림 |
| C6-2 | 기본판 C6-2 1·2번(항공권·숙박), 비고 없음 | 비고 길(결과 `C6_출장` 에 C6-1 의 비고 열이 있음) — 비고 빈 줄은 차례대로. **C6_ 합집합**(C6-1 + C6-2 열) |
| C9-2 | 기본판 C9-2 두 줄, 비고 없음 | 위치 길 — 비고 열이 전부 비어 결과에서 지워짐(`:6241`) |
| C14 | 나머지 판 C14 두 줄(경유·전력) | 위치 길 — 템플릿에 비고 열 없음 |

계수 책은 나머지 판 계수 책(기본 네 시트 + 에너지·에너지_C3·폐기물_C12)에 거리판의 `운송`·`출장_통근` 을 더한 것이다.
"""

from pathlib import Path

from openpyxl import Workbook, load_workbook

from scope3colab import synthetic as syn

FILLED_FACTOR_BOOK_DIR = "factors-filled"
FILLED_TEMPLATE_NAME = "synthetic_scope3_filled_template.xlsx"
KG_PER_KRW = "배출계수(kgCO2e/원)"
EMISSION = "배출량(tCO2e)"

type Row = tuple[syn.Cell, ...]

C6_DIST_ROWS: tuple[Row, ...] = (
    # 1 편도거리 있음(버스, 연간): AI 0
    syn._annual(("가상국", "가상본부", "가상버스", 9, "가상시사", "가상시아", 99), 9, "가상 출장 비고 1"),
    # 2 편도거리 있음(기차, 월별) — 결과에서 이 줄이 앞(월별 먼저)
    syn._monthly(("가상국", "가상지사", "가상기차", 9, "가상시가", "가상시다", 98), {2: 9, 5: 9}, "가상 출장 비고 2"),
)
# (시트, 주 머리, 서브 머리 — None 이면 한 줄 머리, 배출계수 머리 글, 줄). 받은 템플릿 차례(C4 → C6 → C9 → C14).
FILLED_TEMPLATE: tuple[tuple[str, tuple[syn.Cell, ...], tuple[syn.Cell, ...] | None, str, tuple[Row, ...]], ...] = (
    (syn.C4_SPEND_SHEET, syn.C4_SPEND_MAIN, syn.C4_SPEND_SUB, KG_PER_KRW, syn.C4_SPEND_ROWS),
    (syn.C6_DIST_SHEET, syn._DIST_MAIN[syn.C6_DIST_SHEET], syn._DIST_SUB[syn.C6_DIST_SHEET], "배출계수(tCO2e/km)", C6_DIST_ROWS),
    (syn.C6_SPEND_SHEET, syn.C6_SPEND_MAIN, syn.C6_SPEND_SUB, KG_PER_KRW, syn.C6_SPEND_ROWS[:2]),
    (syn.C9_SPEND_SHEET, syn.C9_SPEND_MAIN, syn.C4_SPEND_SUB, KG_PER_KRW, syn.C9_SPEND_ROWS),
    (syn.C14_SHEET, syn.C14_MAIN[:-1], None, "배출계수(tCO2e/단위)", tuple(r[:-1] for r in syn.C14_ROWS)),  # 비고 열 없음
)  # fmt: skip


def build_filled_factor_book(path: Path) -> Path:
    """나머지 판 계수 책 + `운송`·`출장_통근`(둘째 줄 머리글)."""
    syn.build_rest_factor_book(path)
    wb = load_workbook(path)
    syn._factor_sheet_header_row1(wb, "운송", syn.TRANSPORT_HEADERS, tuple((*r, None) for r in syn.TRANSPORT_ROWS))
    syn._factor_sheet_header_row1(wb, "출장_통근", syn.TRAVEL_COMMUTE_HEADERS, tuple((*r, None) for r in syn.TRAVEL_COMMUTE_ROWS))
    wb.save(path)
    return path


def build_filled_template(path: Path) -> Path:
    """시트마다 맨 끝(비고 뒤, C14 는 연료 단위 뒤)에 회색 열 둘. 두 줄 머리 시트는 그 머리 칸을 주·서브 두 줄로 병합한다."""
    wb = Workbook()
    default = wb.active
    assert default is not None
    wb.remove(default)
    for name, main, sub, ef_header, rows in FILLED_TEMPLATE:
        ws = wb.create_sheet(name)
        ws.append([f"카테고리 안내(가상) — {name}"])
        ws.append([*main, ef_header, EMISSION])
        if sub is not None:
            ws.append([*sub, None, None])
            for col in (len(main) + 1, len(main) + 2):
                ws.merge_cells(start_row=2, start_column=col, end_row=3, end_column=col)
        for row in rows:
            ws.append(list(row))
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def build_filled_inputs(out_dir: Path) -> syn.Inputs:
    """`<out_dir>/factors-filled/배출계수_통합_DB.xlsx` 와 `<out_dir>/synthetic_scope3_filled_template.xlsx`."""
    return syn.Inputs(
        build_filled_factor_book(out_dir / FILLED_FACTOR_BOOK_DIR / syn.FACTOR_BOOK_NAME),
        build_filled_template(out_dir / FILLED_TEMPLATE_NAME),
    )
