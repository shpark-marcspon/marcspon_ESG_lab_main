"""사례 입력 짓개 — v2.2 합본판 짓개(`scope3colab.synthetic_merged`, 커밋 57c211a) 위에 사례 줄만 바꿔 얹는다.

`scope3colab` 은 import 만 한다(0줄). 시트 꼴(머리 글·회색 열·v2.2 차이 ①~⑦ ㉯ 엑셀 저장 꼴)은 합본판 그대로이고, 바뀌는 것은
- 시트마다 줄(`rows`) — 사례가 지은 줄,
- 주 머리(`main`) — 사례가 칸을 더할 때만(예: C1 에 「통화」 칸, L2-1),
- 시트 이름 — 사례가 이름을 바꿀 때만(예: L0-1 `C13-2 면적`, L1-2 C10 시트),
- 시트 고름 — 사례가 쓰는 시트만 둔다(합본판의 다른 시트는 두지 않는다. 기업정보·매핑 시트는 `Builder` 가 늘 더한다).

C5·C6-1·C7 의 1번 데이터 줄은 v2.2 ⑥ 의 「매핑 줄」이다(친 값이 매핑 시트를 찾는 수식으로 바뀐다 — `synthetic_v22.match_formulas`).
그래서 이 셋에는 합본판의 1번 줄을 **앞에 붙여 두고**(`MAPPING_FILLER`) 사례 줄은 2번부터 둔다.

계수 책은 회색 칸 판의 것(`synthetic_filled.build_filled_factor_book` — 시트 아홉, 지은 값)이고, 사례가 줄을 더할 때(`factor_edit`)
더한 줄은 이름에 `synthetic` 을 단다. 자료는 전부 지은 것이다(받은 자료의 값·회사 이름·문장 0).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.worksheet import Worksheet
from openpyxl.xml import LXML

from scope3colab import synthetic as syn
from scope3colab import synthetic_filled as filled
from scope3colab import synthetic_merged as merged
from scope3colab import synthetic_v22 as v22

type Cell = syn.Cell
type Row = tuple[Cell, ...]

KEEP_ANNUAL = "[연간 직접]"  # 비고에 이 글이 든 줄은 연간 칸을 값 그대로 둔다(수식으로 바꾸지 않음)
SPECS: dict[str, merged.Sheet] = {spec[0]: spec for spec in merged.MERGED_TEMPLATE}
MAPPING_FILLER: dict[str, Row] = {
    syn.C5_SHEET: syn.C5_ROWS[0],  # 가상폐지 · 상세 「가상 사무 폐지」 → 매핑 값도 가상폐지
    syn.C6_DIST_SHEET: syn.C6_DIST_ROWS[0],  # 가상버스 편도 99 km(AI 0) → 매핑 값 가상버스
    syn.C7_SHEET: syn.C7_ROWS[1],  # 가상지하철 편도 9.9 km(AI 0) → 매핑 값 가상승용차
}


@dataclass(frozen=True)
class SheetCase:
    """사례 시트 하나. `like` 는 합본판 시트 이름(꼴을 빌림), `name` 은 템플릿에 적을 이름(없으면 like)."""

    like: str
    rows: tuple[Row, ...]
    name: str | None = None
    main: tuple[Cell, ...] | None = None
    sub: tuple[Cell, ...] | None = None  # main 을 바꿀 때 두 줄 머리 시트는 sub 도 같은 길이로

    @property
    def title(self) -> str:
        return self.name or self.like


class _CaseBuilder(merged.MergedBuilder):
    """합본판의 `finish`(회색 머리 병합)를 있는 시트에만 — 사례 템플릿은 시트 일부만 둔다."""

    def __init__(self, present: dict[str, str], gray_col: dict[str, int]) -> None:
        super().__init__(v22.ALL_DIFFS, v22.CACHED)
        self.present = present  # 템플릿 시트 이름 → 꼴을 빌린 합본판 시트 이름
        self.gray_col = gray_col  # 템플릿 시트 이름 → 회색 열 첫 칸(1부터)

    def annual_formulas(self, ws: Worksheet) -> None:
        """v2.2 ④ 그대로이되, 비고에 `KEEP_ANNUAL` 이 든 줄은 사용자가 연간 수식을 값으로 덮어쓴 줄로 둔다(L0-3)."""
        h = v22._header_row(ws)
        keep = {r for r in range(h + 2, ws.max_row + 1) if any(KEEP_ANNUAL in str(c.value) for c in ws[r] if c.value)}
        saved = {r: [c.value for c in ws[r]] for r in keep}
        super().annual_formulas(ws)
        annual = v22._col_of(ws, h + 1, v22.ANNUAL_NAMES)
        for r, values in saved.items():
            if annual is None:
                continue
            ws.cell(r, annual, values[annual - 1])
            self.cache.pop((ws.title, ws.cell(r, annual).coordinate), None)

    def finish(self, wb: Workbook) -> None:
        for title, like in self.present.items():
            if like not in merged.TWO_ROW_GRAY:
                continue
            ws = wb[title]
            h = v22._header_row(ws)
            col = self.gray_col[title]
            for c in (*range(1, merged.FULL_MERGE.get(like, 0) + 1), col, col + 1):
                ws.merge_cells(start_row=h, start_column=c, end_row=h + 1, end_column=c)


def _check_writer() -> None:
    if LXML:  # scope3flaws/__init__.py 머리 — openpyxl 이 이 패키지보다 먼저 import 되면 lxml 쓰기가 켜진 채다
        raise RuntimeError("openpyxl lxml 쓰기가 켜져 있다 — scope3flaws 를 openpyxl 보다 먼저 import 하라(__init__.py)")


def build_template(path: Path, sheets: Sequence[SheetCase]) -> Path:
    """사례 시트들(차례 그대로)로 v2.2 합본판 꼴 템플릿을 짓는다."""
    _check_writer()
    wb = Workbook()
    default = wb.active
    assert default is not None
    wb.remove(default)
    present: dict[str, str] = {}
    gray_col: dict[str, int] = {}
    for case in sheets:
        _, main0, sub0, ef, _ = SPECS[case.like]
        main = case.main if case.main is not None else main0
        sub = case.sub if case.sub is not None else sub0
        gray = (ef, filled.EMISSION) if ef is not None else ()
        ws = wb.create_sheet(case.title)
        ws.append([f"카테고리 안내(가상) — {case.title}"])
        ws.append([*main, *gray])
        if sub is not None:
            ws.append([*sub, *([None] * len(gray))])
        filler = (MAPPING_FILLER[case.like],) if case.like in MAPPING_FILLER else ()
        for row in (*filler, *case.rows):
            ws.append(list(row))
        present[case.title] = case.like
        gray_col[case.title] = len(main) + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    _CaseBuilder(present, gray_col).apply(path)
    return path


def build_factor_book(path: Path, edit: Callable[[Workbook], None] | None = None) -> Path:
    """회색 칸 판 계수 책(시트 아홉, 지은 값). `edit` 이 있으면 열어 고친 뒤 다시 저장한다(더한 줄은 synthetic 표시)."""
    filled.build_filled_factor_book(path)
    if edit is not None:
        wb = load_workbook(path)
        edit(wb)
        wb.save(path)
    return path


def annual(prefix: Row, amount: Cell, note: str | None = None) -> Row:
    """연간 칸 하나만 든 줄(합본판 `_annual` 과 같은 꼴 — 연간 값에 수 아닌 것도 받는다)."""
    return (*prefix, amount, *([None] * 12), note)


def monthly(prefix: Row, months: dict[int, Cell], note: str | None = None, annual_value: Cell = None) -> Row:
    """달 칸 줄. `annual_value` 를 주면 연간 칸도 친다(L0-3 「연간+월 둘 다」)."""
    return (*prefix, annual_value, *(months.get(i) for i in range(1, 13)), note)
