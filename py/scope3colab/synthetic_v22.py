"""v2.2 꼴 판 넷(S3C-o4a·S3C-o5, `sid:MARC-ISSUE-274`): 기본판·거리판 지어낸 입력의 템플릿 위에 템플릿 v2.2 차이를 얹는다. 계수 책은 그대로.

측정 밴드 S3C-v22 의 짓개(`esg-lab/out/S3C-v22/make_v22.py`, git 밖)에서 판 셋에 쓰는 것만 옮겼다(하나씩 얹은 측정 판과 저장 값 없는
㉮ 꼴은 옮기지 않음). 받은 v2.2 템플릿에서 잰 것은 **구조뿐**이다(머리글 줄 자리·열 차례·수식 글자 — S3C-v22 보고 §1-1). 값·회사명·
예시 줄은 옮기지 않았고, 회사 이름은 `가상기업A`·`가상기업B` 뿐이다.

  ① `기업정보` 시트(맨 앞) — C7 = 가상기업A, E열 회사 목록 가상기업A·가상기업B
  ② C5·C6-1·C7 머리글 위 「*적용 기업명」 두 줄(값 칸 = `=기업정보!$C$7`, 둘째 줄 ※ 안내)
  ③ C6-1·C7 「상세 교통수단명(선택)」 열 — 받은 파일과 같은 상대 자리(비고 + 3칸, 맨 끝 — 앞 열은 밀리지 않음)
  ④ 연간 칸 `=IF(SUM(<1월>:<12월>)=0,"",SUM(…))` — 받은 파일과 같은 줄 수(C5·C6-1·C7 201, 나머지 36). 연간만 든 줄은 값 그대로
  ⑤ 매핑 시트 둘(맨 뒤) — A 회사·B 상세명·C 값, E열 `=IF(기업정보!E<n>="","",기업정보!E<n>)`, 가상 줄만
  ⑥ C5·C6-1·C7 「폐기물 종류」/「교통수단」 칸의 INDEX/MATCH 자동매칭 수식 — 빈 줄 전부와 「매핑 줄」 하나(1번 줄: 친 값을 수식으로
     바꿔 매핑 시트에서 원래 값을 되찾게 함)
  ⑦ 머리글 줄 자리를 받은 파일과 같게(C1·C2 18, C4-2·C7 20, 나머지 19)

수식 칸은 두 꼴이다. openpyxl 이 쓴 그대로(`<f>…</f><v />`)는 엔진 읽개가 파일을 통째로 거절해(`sid:MARC-ISSUE-241`) 이 판들에 쓰지 않는다.
- ㉯ 엑셀 저장 꼴(`base-v22`·`distance-v22`) — 저장 뒤 시트 xml 의 수식 칸에 엑셀이 저장했을 값을 넣는다(수 `<v>수</v>`, 글 `t="str"` +
  `<v>글</v>`, 빈 글 `t="str"` + `<v/>` — 받은 파일의 꼴).
- ㉰ 저장 값도 `<v>` 요소도 없음(`distance-v22map`·`base-v22map`) — `<v />` 를 지운다. 코랩·엔진이 매핑 폴백 갈래를 탄다.

나머지 시트(C3·C8·C11~C15)의 v2.2 표(⑦ 머리글 자리·④ 연간 열 이름·C15 산정 배출량 수식)도 여기 둔다(S3C-o5). 그 시트를 가진
템플릿은 합본판(`synthetic_merged.py`)뿐이라 기존 판의 바이트는 그대로다.
어느 꼴이든 못 바꾼 수식 칸이 하나라도 남으면 짓기 실패다(`CachedValueError`).

같은 입력이면 지은 바이트가 늘 같다: 저장 뒤 zip 을 다시 쓰며 칸마다 시각을 `FIXED_ZIP_TIME` 으로, `docProps/core.xml` 의 만든·고친 시각을
`FIXED_CORE_TIME` 으로 둔다(openpyxl 은 저장할 때 고친 시각을 지금으로 적고 zip 칸 시각도 지금이다).
"""

import re
import zipfile
from collections.abc import Callable, Iterable
from pathlib import Path
from xml.sax.saxutils import escape

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from scope3colab import synthetic as syn

COMPANY = "가상기업A"
COMPANIES = ("가상기업A", "가상기업B")
COMPANY_SHEET = "기업정보"
WASTE_MAP_SHEET = "회사별_폐기물매핑"
TRAVEL_MAP_SHEET = "회사별_교통수단매핑"
DETAIL_COL = "상세 교통수단명(선택)"
ANNUAL_NAMES = frozenset({"연간지출금액(원)", "연간취득금액(원)", "연간운송량", "연간운송금액(원)", "연간처리량", "연간이동횟수",
                          "연간근무일수",
                          "연간사용량", "건물전체 연간사용량"})  # fmt: skip — 둘째 줄은 나머지 시트(S3C-o5: C3·C8-1·C13-1 / C8-2·3·C13-2·3)
# 받은 v2.2 의 머리글 줄(⑦)과 연간 수식 줄 수(④) — 구조만 잼(S3C-v22 보고 §1-1)
RECEIVED_HEADER_ROW = {"C1_구매품서비스": 18, "C2_자본재": 18, "C4-1_운송거리": 19, "C4-2_운송금액": 20, "C5_사업장폐기물": 19,
                       "C6-1_출장거리": 19, "C6-2_출장비": 19, "C7_임직원통근": 20, "C9-1_운송거리": 19, "C9-2_운송금액": 19,
                       # 나머지 시트(S3C-o5 — 합본판만 이 시트를 둔다. 기존 판의 템플릿에는 없어 바이트가 그대로다)
                       "C3_연료에너지": 19, "C8-1_에너지사용량 아는 경우": 19, "C8-2_면적": 19, "C8-3_인원": 19,
                       "C11_판매제품사용": 19, "C12_판매제품폐기": 20, "C13-1_에너지사용량 아는경우": 19, "C13-2_면적": 19,
                       "C13-3_인원": 19, "C14_프랜차이즈": 19, "C15_투자": 14}  # fmt: skip
FORMULA_ROWS = {"C5_사업장폐기물": 201, "C6-1_출장거리": 201, "C7_임직원통근": 201}  # 나머지 36(연간 열이 없는 C11·C12·C14 는 0)
DEFAULT_FORMULA_ROWS = 36
# C15 「산정 배출량(tCO2e)」(S3C-o5 — 받은 v2.2 의 수식 11칸, 머리글 다음 줄부터). 받은 꼴은 피투자기업명·지분율·Scope1·Scope2 네 칸 뒤
# 한 칸에 `=IF(OR(<넷 중 빈 칸>),"",(<S1>+<S2>)*<지분율>/100)`. 템플릿에 이 머리 열이 있을 때만 얹는다(④ 묶음).
INVESTMENT_SHEET = "C15_투자"
INVESTMENT_RESULT = "산정 배출량(tCO2e)"
INVESTMENT_FORMULA_ROWS = 11
COMPANY_SHEETS = ("C5_사업장폐기물", "C6-1_출장거리", "C7_임직원통근")
DETAIL_SHEETS = ("C6-1_출장거리", "C7_임직원통근")
# 매핑 시트 줄: (회사, 상세명, 매핑값). 다른 회사(`가상기업B`)의 같은 상세명 줄을 하나씩 둔다 — 적용 기업명으로 가려지는지 보게
WASTE_MAP_ROWS = (("가상기업A", "가상 사무 폐지", "가상폐지"), ("가상기업B", "가상 사무 폐지", "가상폐수지"),
                  ("가상기업A", "가상 기타 상세", "가상잔반"))  # fmt: skip
TRAVEL_MAP_ROWS = (("가상기업A", "가상 상세수단 1", "가상버스"), ("가상기업A", "가상 상세수단 2", "가상승용차"),
                   ("가상기업B", "가상 상세수단 1", "가상기차"))  # fmt: skip
MAPPED_DETAIL = {"C6-1_출장거리": "가상 상세수단 1", "C7_임직원통근": "가상 상세수단 2"}  # C5 는 1번 줄의 상세명 그대로

ALL_DIFFS = frozenset("1234567")
MAPPING_DIFFS = frozenset("12356")  # 매핑 묶음(④ 연간 수식·⑦ 머리글 자리 없음)
CACHED, NO_VALUE = "cached", "no-value"  # ㉯ 엑셀 저장 꼴 · ㉰ 저장 값도 <v> 도 없음

FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)
FIXED_CORE_TIME = "2026-01-01T00:00:00Z"

type Cached = str | int | float


class CachedValueError(ValueError):
    """저장 값을 넣지(또는 지우지) 못한 수식 칸이 남았다 — 짓기 실패."""


HEADER_FIRST = ("*국가", "*피투자기업명 (필수)")  # 머리글 줄의 첫 칸(C15 만 둘째 글 — S3C-o5)


def _header_row(ws: Worksheet) -> int:
    for r in range(1, 40):
        if ws.cell(r, 1).value in HEADER_FIRST:
            return r
    raise ValueError(f"{ws.title}: 머리글(*국가)을 못 찾았다")


def _col_of(ws: Worksheet, row: int, names: Iterable[str]) -> int | None:
    wanted = set(names)
    for c in range(1, ws.max_column + 1):
        if ws.cell(row, c).value in wanted:
            return c
    return None


def _month_cols(ws: Worksheet, sub: int) -> list[int]:
    return [c for c in range(1, ws.max_column + 1) if ws.cell(sub, c).value in syn.MONTHS]


def _num_text(x: float | int) -> str:
    if isinstance(x, int) or float(x).is_integer():
        return str(int(x))
    return repr(float(x))


class Builder:
    """v2.1 지어낸 템플릿 위에 차이(`diffs` — ①~⑦ 의 숫자 글자)를 얹고, 수식 칸마다 엑셀이 저장했을 값을 적어 둔다."""

    def __init__(self, diffs: frozenset[str], form: str) -> None:
        self.diffs = diffs
        self.form = form
        self.cache: dict[tuple[str, str], Cached] = {}  # (시트, 칸) → 엑셀이 저장했을 값("" 포함)
        self.company_cell: dict[str, str] = {}  # 시트 → 적용 기업명 값 칸 주소

    def formula(self, ws: Worksheet, coord: str, text: str, value: Cached) -> None:
        ws[coord] = text
        self.cache[(ws.title, coord)] = value

    # ① 기업정보
    def company_sheet(self, wb: Workbook) -> None:
        ws = wb.create_sheet(COMPANY_SHEET, 0)
        ws["B2"] = "기업정보(가상 안내)"
        ws["B7"] = "적용 기업명"
        ws["C7"] = COMPANY
        ws["D7"] = "(가상 선택)"
        ws["E1"] = "회사 목록"
        for i, name in enumerate(COMPANIES, start=2):
            ws[f"E{i}"] = name

    # ⑦ 머리글 줄 자리
    def pad_header(self, ws: Worksheet) -> None:
        target = RECEIVED_HEADER_ROW.get(ws.title)
        h = _header_row(ws)
        if target is None or target <= h:
            return
        ws.insert_rows(h, target - h)
        for r in range(h, target):
            ws.cell(r, 1, f"※ 가상 안내 {r}")

    # ② 적용 기업명 두 줄
    def company_rows(self, ws: Worksheet) -> None:
        h = _header_row(ws)
        if "7" in self.diffs and h - 5 >= 2:  # 받은 파일처럼 머리글 다섯 줄 위(안내 줄 자리를 덮음)
            r = h - 5
        else:
            ws.insert_rows(h, 2)
            r = h
        ws.cell(r, 1, "*적용 기업명")
        coord = f"B{r}"
        self.formula(ws, coord, f"={COMPANY_SHEET}!$C$7", COMPANY)
        ws.cell(r + 1, 1, "※ 이 값은 '기업정보' 시트에서 고른 적용 기업명이다(가상 안내)")
        self.company_cell[ws.title] = coord

    # ③ 상세 교통수단명 열
    def detail_col(self, ws: Worksheet) -> int:
        h = _header_row(ws)
        note = _col_of(ws, h, ["비고(선택)"])
        assert note is not None
        col = note + 3
        ws.cell(h, col, DETAIL_COL)
        first = h + 2
        if ws.cell(first, 1).value is not None:  # 데이터 줄이 있으면 1번 줄에 상세명(매핑 줄)
            ws.cell(first, col, MAPPED_DETAIL[ws.title])
        return col

    # ④ 연간 칸 수식
    def annual_formulas(self, ws: Worksheet) -> None:
        h = _header_row(ws)
        sub = h + 1
        annual = _col_of(ws, sub, ANNUAL_NAMES)
        months = _month_cols(ws, sub)
        if annual is None or not months:
            return
        a, m0, m1 = get_column_letter(annual), get_column_letter(months[0]), get_column_letter(months[-1])
        for r in range(sub + 1, sub + 1 + FORMULA_ROWS.get(ws.title, DEFAULT_FORMULA_ROWS)):
            nums = [v for v in (ws.cell(r, c).value for c in months) if isinstance(v, int | float)]
            if not nums and ws.cell(r, annual).value is not None:
                continue  # 연간만 든 줄 — 사용자가 수식을 덮어썼다
            total = sum(nums)
            self.formula(ws, f"{a}{r}", f'=IF(SUM({m0}{r}:{m1}{r})=0,"",SUM({m0}{r}:{m1}{r}))', total if total else "")

    # ④ C15 산정 배출량 수식(S3C-o5)
    def investment_formulas(self, ws: Worksheet) -> None:
        h = _header_row(ws)
        result = _col_of(ws, h, [INVESTMENT_RESULT])
        if result is None:
            return
        name, share, s1, s2 = (get_column_letter(c) for c in range(result - 4, result))
        for r in range(h + 1, h + 1 + INVESTMENT_FORMULA_ROWS):
            cells = [ws[f"{c}{r}"].value for c in (name, share, s1, s2)]
            value: Cached = "" if any(v is None for v in cells) else (cells[2] + cells[3]) * cells[1] / 100
            text = f'=IF(OR({name}{r}="",{share}{r}="",{s1}{r}="",{s2}{r}=""),"",({s1}{r}+{s2}{r})*{share}{r}/100)'
            self.formula(ws, f"{get_column_letter(result)}{r}", text, value)

    # 저장 직전 손질(하위 짓개 몫 — 기본은 없음)
    def finish(self, wb: Workbook) -> None:
        pass

    # ⑥ 자동매칭 수식
    def match_formulas(self, ws: Worksheet, detail: int | None) -> None:
        h = _header_row(ws)
        if ws.title == "C5_사업장폐기물":
            target = _col_of(ws, h, ["*폐기물 종류"])
            key = _col_of(ws, h, ["상세 폐기물명(선택)"])
            sheet, table = WASTE_MAP_SHEET, WASTE_MAP_ROWS
        else:
            target = _col_of(ws, h, ["*교통수단"])
            key = detail
            sheet, table = TRAVEL_MAP_SHEET, TRAVEL_MAP_ROWS
        assert target is not None and key is not None
        company = self.company_cell[ws.title].replace("B", "$B$")
        k = get_column_letter(key)
        lookup = {(c, d): v for c, d, v in table}
        first = h + 2
        for r in range(first, first + FORMULA_ROWS[ws.title]):
            if ws.cell(r, 1).value is not None and r != first:
                continue  # 사용자가 값을 친 줄
            text = (f"=IFERROR(INDEX({sheet}!$C$2:$C$1000,MATCH(1,INDEX(({company}={sheet}!$A$2:$A$1000)*"
                    f"(${k}{r}={sheet}!$B$2:$B$1000),0),0)),\"\")")  # fmt: skip
            self.formula(ws, f"{get_column_letter(target)}{r}", text, lookup.get((COMPANY, ws.cell(r, key).value), ""))

    # ⑤ 매핑 시트
    def mapping_sheets(self, wb: Workbook) -> None:
        for name, rows in ((WASTE_MAP_SHEET, WASTE_MAP_ROWS), (TRAVEL_MAP_SHEET, TRAVEL_MAP_ROWS)):
            ws = wb.create_sheet(name)
            ws.append(["회사명", "상세명", "매핑값", None, "회사 목록(가상)", None, "가상 안내"])
            for i, row in enumerate(rows, start=2):
                for c, v in enumerate(row, start=1):
                    ws.cell(i, c, v)
            for r in range(2, 23):
                value = COMPANIES[r - 2] if r - 2 < len(COMPANIES) else ""
                self.formula(ws, f"E{r}", f'=IF({COMPANY_SHEET}!E{r}="","",{COMPANY_SHEET}!E{r})', value)

    def apply(self, path: Path) -> None:
        wb = load_workbook(path)
        d = self.diffs
        if "1" in d:
            self.company_sheet(wb)
        for ws in wb.worksheets:
            if ws.title not in RECEIVED_HEADER_ROW:
                continue
            if "7" in d:
                self.pad_header(ws)
            if "2" in d and ws.title in COMPANY_SHEETS:
                self.company_rows(ws)
            detail = self.detail_col(ws) if "3" in d and ws.title in DETAIL_SHEETS else None
            if "6" in d and ws.title in COMPANY_SHEETS:
                self.match_formulas(ws, detail)
            if "4" in d:
                self.annual_formulas(ws)
                if ws.title == INVESTMENT_SHEET:
                    self.investment_formulas(ws)
        if "5" in d:
            self.mapping_sheets(wb)
        self.finish(wb)
        wb.save(path)
        write_cached_values(path, self.cache, drop_v=self.form == NO_VALUE)


# openpyxl 이 쓴 수식 칸: `<c r="A1" s="…"><f>…</f><v /></c>`(t 없음, 빈 값)
_CELL = re.compile(r'<c r="(?P<r>[A-Z]+[0-9]+)"(?P<attrs>[^>]*)><f>(?P<f>[^<]*)</f><v\s*/></c>')
_CORE_TIME = re.compile(r"(<dcterms:(?:created|modified)\b[^>]*>)[^<]*(</dcterms:(?:created|modified)>)")


def _sheet_paths(z: zipfile.ZipFile) -> dict[str, str]:
    """시트 이름 → zip 안 시트 xml 자리."""
    wbx = z.read("xl/workbook.xml").decode()
    rels = z.read("xl/_rels/workbook.xml.rels").decode()

    def attr(tag: str, key: str) -> str:
        m = re.search(rf'\s{key}="([^"]*)"', tag)
        assert m is not None, tag
        return m.group(1)

    targets = {attr(t, "Id"): attr(t, "Target") for t in re.findall(r"<Relationship\s[^>]*>", rels)}
    out = {}
    for name, rid in ((attr(t, "name"), attr(t, "r:id")) for t in re.findall(r"<sheet\s[^>]*>", wbx)):
        t = targets[rid].lstrip("/")
        out[name] = t if t.startswith("xl/") else f"xl/{t}"
    return out


def _rewrite(path: Path, edit: Callable[[str, bytes], bytes]) -> None:
    """zip 을 칸마다 `edit(이름, 바이트)` 로 다시 쓴다. 칸 시각은 `FIXED_ZIP_TIME`, 차례·압축은 그대로."""
    tmp = path.with_suffix(".tmp.xlsx")
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            fixed = zipfile.ZipInfo(info.filename, FIXED_ZIP_TIME)
            fixed.compress_type = info.compress_type
            fixed.external_attr = info.external_attr
            out.writestr(fixed, edit(info.filename, src.read(info)))
    tmp.replace(path)


def write_cached_values(path: Path, cache: dict[tuple[str, str], Cached], drop_v: bool = False) -> None:
    """저장 뒤 수식 칸 xml 에 저장 값을 넣는다(`drop_v`: 값 대신 `<v />` 를 지운다). 시각 칸도 고정한다.

    `cache` 에 있는 칸을 못 찾거나, 바꾼 뒤에도 openpyxl 꼴 수식 칸(`<f>…</f><v />`)이 하나라도 남으면 `CachedValueError`.
    """
    with zipfile.ZipFile(path) as z:
        paths = _sheet_paths(z)
    by_path: dict[str, dict[str, Cached]] = {}
    for (sheet, coord), value in cache.items():
        by_path.setdefault(paths[sheet], {})[coord] = value
    sheet_xml = set(paths.values())

    def edit(name: str, body: bytes) -> bytes:
        if name == "docProps/core.xml":
            return _CORE_TIME.sub(rf"\g<1>{FIXED_CORE_TIME}\g<2>", body.decode()).encode()
        if name not in sheet_xml:
            return body
        wanted = by_path.get(name, {})
        done: set[str] = set()

        def sub(m: re.Match[str]) -> str:
            coord = m.group("r")
            if coord not in wanted:
                return m.group(0)
            done.add(coord)
            attrs, f = m.group("attrs"), m.group("f")
            if ' t="' in attrs:
                raise CachedValueError(f"{name} {coord}: 수식 칸에 이미 t 가 있다")
            if drop_v:
                return f'<c r="{coord}"{attrs}><f>{f}</f></c>'
            value = wanted[coord]
            if isinstance(value, str):
                v = f"<v>{escape(value)}</v>" if value else "<v/>"
                return f'<c r="{coord}"{attrs} t="str"><f>{f}</f>{v}</c>'
            return f'<c r="{coord}"{attrs}><f>{f}</f><v>{_num_text(value)}</v></c>'

        text = _CELL.sub(sub, body.decode())
        missing = sorted(set(wanted) - done)
        if missing:
            raise CachedValueError(f"{name}: 저장 값을 못 넣은 수식 칸 {missing[:5]}")
        left = [m.group("r") for m in _CELL.finditer(text) if ' t="' not in m.group("attrs")]  # 빈 글 결과(t="str" <v/>)는 넣은 것
        if left:
            raise CachedValueError(f"{name}: 저장 값이 없는 수식 칸이 남았다 {left[:5]}")
        return text.encode()

    _rewrite(path, edit)


def _build(base: Callable[[Path], syn.Inputs], diffs: frozenset[str], form: str) -> Callable[[Path], syn.Inputs]:
    def build(out_dir: Path) -> syn.Inputs:
        inputs = base(out_dir)
        Builder(diffs, form).apply(inputs.template)
        return inputs

    return build


# `--input` 판 셋. 계수 책·답 파일은 바탕 판 것 그대로다(계수 책은 바탕 짓개가 짓는 그 파일 — v2.2 는 템플릿만 바꾼다).
build_base_v22_inputs = _build(syn.build_inputs, ALL_DIFFS, CACHED)
build_distance_v22_inputs = _build(syn.build_distance_inputs, ALL_DIFFS, CACHED)
build_distance_v22map_inputs = _build(syn.build_distance_inputs, MAPPING_DIFFS, NO_VALUE)
# S3C-o5: 기본판의 C5 매핑 폴백 갈래(㉰ — 저장 값 없음). 답 파일은 기본판 것 그대로.
build_base_v22map_inputs = _build(syn.build_inputs, MAPPING_DIFFS, NO_VALUE)
