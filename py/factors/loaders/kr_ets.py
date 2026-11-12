"""배출권거래제 배출량 보고·인증 지침 별표 10·11·12 (PDF 표).

- 별표 12: 연료별 국가 고유 발열량(총·순)과 국가 고유 배출계수(탄소, CO2)
- 별표 11: 2006 IPCC 연료별 기본 순발열량
- 별표 10: 2006 IPCC 기본 배출계수 (CO2, 부문별 CH4·N2O)

값은 전부 PDF 의 표 셀에서 읽는다. 세로로 병합된 셀(별표 12 의 등유·경유, 천연가스·도시가스)은
셀 테두리 좌표로 병합을 확인한 때에만 윗줄 값을 물려준다. 확인하지 못하면 실패한다.
"""

from __future__ import annotations

import re

import pymupdf

from ..record import make_record, parse_number
from ..sources import SOURCES, SourceFormatError

BYL10, BYL11, BYL12 = "kr-ets-byl10", "kr-ets-byl11", "kr-ets-byl12"


def _clean(cell: str | None) -> str:
    """셀 안의 줄바꿈은 좁은 칸에서 낱말이 꺾인 것이다. 붙여 읽는다."""
    return re.sub(r"\s*\n\s*", "", cell or "").strip()


def _squash(cell: str | None) -> str:
    return re.sub(r"\s+", "", cell or "")


def _tables(src_id: str):
    """(쪽 번호, 쪽 안 표 순번, 표 객체, 추출 행들)"""
    doc = pymupdf.open(SOURCES[src_id].require())
    out = []
    for page in doc:
        for ti, t in enumerate(page.find_tables().tables):
            out.append((page.number + 1, ti, t, t.extract()))
    return out, doc


# ---------------------------------------------------------------- 별표 12

def _load_byl12_heating(src, tables) -> list[dict]:
    records, fuels = [], []
    for page, ti, _, rows in tables:
        if [_squash(c) for c in rows[0]] != ["연료명", "단위", "", "총발열량", "순발열량"]:
            continue
        for ri, row in enumerate(rows[2:], start=2):
            name, unit_law, unit_tj, gross, net = (_clean(c) for c in row)
            if name and not (unit_law or unit_tj or gross or net):
                # 쪽이 넘어가며 잘린 연료명의 뒷부분
                if not fuels:
                    raise SourceFormatError(f"{src.id} {page}쪽: 이어 붙일 앞 행이 없다 {row}")
                fuels[-1]["name"] += name
                fuels[-1]["joined"] = True
                continue
            if not (name and unit_law.count("/") == 1 and gross and net):
                raise SourceFormatError(f"{src.id} {page}쪽 표{ti} {ri}행: 읽을 수 없다 {row}")
            fuels.append({"name": name, "unit_law": unit_law, "unit_tj": unit_tj, "gross": gross, "net": net,
                          "loc": f"PDF {page}쪽 발열량 표 {ri}행", "key": f"hv:p{page}r{ri}", "joined": False})
    for f in fuels:
        num, den = f["unit_law"].split("/")
        for basis, col in (("gross", "총발열량"), ("net", "순발열량")):
            records.append(
                make_record(
                    src,
                    key=f"{f['key']}:{basis}",
                    locator=f"{f['loc']} {col}",
                    record_type="heating_value",
                    scope="scope2" if f["name"].startswith("전기") else "scope1",
                    factor_type="activity",
                    name_original=f["name"],
                    name_lang="ko",
                    category_path=["연료별 국가 고유 발열량"],
                    value=parse_number(f[basis], f["loc"]),
                    value_raw=f[basis],
                    unit=f["unit_law"],
                    unit_numerator=num,
                    unit_denominator=den,
                    gas="none",
                    gas_basis="not_applicable",
                    heating_value_basis=basis,
                    region="KR",
                    year=None,
                    extra={"unit_tj": f["unit_tj"], "name_joined_across_pages": f["joined"]},
                )
            )
    return records


_EF_HEADER = ["구분", "연료", "탄소배출계수(kgC/TJ)", "이산화탄소배출계수(kgCO/TJ)2"]
_EF_COLS = ((2, "C", "carbon_mass", "kgC", "탄소 배출계수"), (3, "CO2", "gas_mass", "kgCO2", "이산화탄소 배출계수"))


def _merged_from(table, ri: int, col: int) -> int | None:
    """ri 행의 col 칸이 비어 있을 때, 그 자리를 세로로 덮는 윗줄 셀이 있으면 그 행 번호."""
    fuel_cell = table.rows[ri].cells[1]
    if fuel_cell is None:
        return None
    y_mid = (fuel_cell[1] + fuel_cell[3]) / 2
    for rj in range(ri - 1, 0, -1):
        c = table.rows[rj].cells[col]
        if c is not None:
            return rj if c[1] < y_mid < c[3] else None
    return None


def _load_byl12_ef(src, tables) -> list[dict]:
    records, group = [], None
    for page, ti, table, rows in tables:
        if [_squash(c) for c in rows[0]] != _EF_HEADER:
            continue
        for ri, row in enumerate(rows[1:], start=1):
            group = _clean(row[0]) or group
            fuel = _clean(row[1])
            if not fuel or group is None:
                raise SourceFormatError(f"{src.id} {page}쪽 배출계수 표 {ri}행: 읽을 수 없다 {row}")
            for col, gas, gas_basis, num, label in _EF_COLS:
                raw, conditions = _clean(row[col]), {}
                if not raw:
                    rj = _merged_from(table, ri, col)
                    if rj is None:
                        raise SourceFormatError(f"{src.id} {page}쪽 배출계수 표 {ri}행 {fuel}: 값이 비었고 병합 셀도 아니다")
                    raw = _clean(rows[rj][col])
                    conditions = {"병합 셀": f"원문 표에서 {_clean(rows[rj][1])} 행과 한 칸을 같이 쓴다"}
                records.append(
                    make_record(
                        src,
                        key=f"ef:p{page}r{ri}:{gas}",
                        locator=f"PDF {page}쪽 배출계수 표 {ri}행 {label}",
                        scope="scope1",
                        factor_type="activity",
                        name_original=fuel,
                        name_lang="ko",
                        category_path=["연료별 국가 고유 배출계수", group],
                        value=parse_number(raw, f"{src.id} {page}쪽 {ri}행"),
                        value_raw=raw,
                        unit_numerator=num,
                        unit_denominator="TJ",
                        gas=gas,
                        gas_basis=gas_basis,
                        conditions=conditions,
                        region="KR",
                        year=None,
                    )
                )
    return records


def load_byl12() -> list[dict]:
    src = SOURCES[BYL12]
    tables, _ = _tables(BYL12)
    heating, ef = _load_byl12_heating(src, tables), _load_byl12_ef(src, tables)
    if not heating or not ef:
        raise SourceFormatError(f"{src.id}: 표를 찾지 못했다 (발열량 {len(heating)}, 배출계수 {len(ef)})")
    return heating + ef


# ---------------------------------------------------------------- 별표 11

def _is_section(row) -> bool:
    return bool(_clean(row[0])) and all(not _clean(c) for c in row[1:]) and not _clean(row[0]).startswith("*")


def load_byl11() -> list[dict]:
    src = SOURCES[BYL11]
    tables, _ = _tables(BYL11)
    records, section, group = [], None, None
    for page, ti, _, rows in tables:
        head = [_squash(c) for c in rows[0]]
        if head[-1] != "순발열량" or head[0] != "연료명":
            continue  # 순/총 비율 표 등
        simple = len(head) == 3  # 비고의 B-A유·B-B유 표: 연료명, 단위, 순발열량
        for ri, row in enumerate(rows[1:], start=1):
            cells = [_clean(c) for c in row]
            if cells[0].startswith("*"):
                continue  # 표 아래 주석 문단
            if _is_section(row):
                section, group = cells[0], None
                continue
            names = cells[:-2] if simple else cells[:-3]
            domestic = None if simple else (row[-3] or "").strip()
            unit, raw = cells[-2], cells[-1]
            filled = [n for n in names if n]
            if not filled or unit.count("/") != 1 or not raw:
                raise SourceFormatError(f"{src.id} {page}쪽 표{ti} {ri}행: 읽을 수 없다 {row}")
            if names[0]:
                group = names[0] if len(filled) > 1 else None
            name = filled[-1]
            if raw == "-":
                value, status = None, "not_available_in_source"
            else:
                value, status = parse_number(raw, f"{src.id} {page}쪽 {ri}행"), "ok"
            num, den = unit.split("/")
            path = ["비고 표"] if simple else [x for x in (section, group) if x]
            records.append(
                make_record(
                    src,
                    key=f"p{page}t{ti}r{ri}",
                    locator=f"PDF {page}쪽 표{ti} {ri}행",
                    record_type="heating_value",
                    scope="scope1",
                    factor_type="activity",
                    name_original=name,
                    name_lang="ko",
                    category_path=path,
                    value=value,
                    value_raw=raw,
                    value_status=status,
                    unit=unit,
                    unit_numerator=num,
                    unit_denominator=den,
                    gas="none",
                    gas_basis="not_applicable",
                    heating_value_basis="net",
                    region="GLOBAL",
                    year=None,
                    extra={"국내에너지원 기준": domestic} if domestic is not None else {},
                )
            )
    if not records:
        raise SourceFormatError(f"{src.id}: 표를 찾지 못했다")
    return records


# ---------------------------------------------------------------- 별표 10

_CH4_SECTORS = ["에너지산업", "제조업건설업", "상업공공", "가정기타"]
_N2O_SECTORS = ["에너지산업제조업건설업", "상업공공가정기타"]
_UNIT_LINE = "(단위: kgGHG/TJ)"


def load_byl10() -> list[dict]:
    src = SOURCES[BYL10]
    tables, doc = _tables(BYL10)
    if _UNIT_LINE.replace(" ", "") not in doc[0].get_text().replace(" ", ""):
        raise SourceFormatError(f"{src.id}: 1쪽에서 단위 표기 {_UNIT_LINE} 를 찾지 못했다")
    records, section, group = [], None, None
    for page, ti, _, rows in tables:
        head0 = [_squash(c) for c in rows[0]]
        if head0[0] != "연료명" or "CH4" not in head0:
            continue
        n = len(rows[0])
        n_names = n - 8 if n == 10 else 1  # 10열: 연료명 2칸 + 국내에너지원 기준, 8열(비고 표): 연료명 1칸
        if n not in (10, 8):
            raise SourceFormatError(f"{src.id} {page}쪽 표{ti}: 열 수 {n}")
        head1 = [_squash(c) for c in rows[1]]
        if head1[-6:-2] != _CH4_SECTORS or head1[-2:] != _N2O_SECTORS:
            raise SourceFormatError(f"{src.id} {page}쪽 표{ti}: 부문 머리행이 다르다 {head1}")
        for ri, row in enumerate(rows[2:], start=2):
            cells = [_clean(c) for c in row]
            if cells[0].startswith("*"):
                continue  # 표 아래 주석 문단
            if _is_section(row):
                section, group = cells[0], None
                continue
            names = cells[:n_names]
            domestic = (row[2] or "").strip() if n == 10 else None
            filled = [x for x in names if x]
            if not filled or any(not c for c in cells[-7:]):
                raise SourceFormatError(f"{src.id} {page}쪽 표{ti} {ri}행: 읽을 수 없다 {row}")
            if names[0]:
                group = names[0] if len(filled) > 1 else None
            name = filled[-1]
            path = ["비고 표"] if n == 8 else [x for x in (section, group) if x]
            series = [("CO2", None, cells[-7])]
            series += [("CH4", s, v) for s, v in zip(_CH4_SECTORS, cells[-6:-2])]
            series += [("N2O", s, v) for s, v in zip(_N2O_SECTORS, cells[-2:])]
            for k, (gas, sector, raw) in enumerate(series):
                records.append(
                    make_record(
                        src,
                        key=f"p{page}t{ti}r{ri}:{gas}:{k}",
                        locator=f"PDF {page}쪽 표{ti} {ri}행 {gas}" + (f" {sector}" if sector else ""),
                        scope="scope1",
                        factor_type="activity",
                        name_original=name,
                        name_lang="ko",
                        category_path=path,
                        value=parse_number(raw, f"{src.id} {page}쪽 {ri}행"),
                        value_raw=raw,
                        unit_numerator=f"kg{gas}",
                        unit_denominator="TJ",
                        gas=gas,
                        gas_basis="gas_mass",
                        conditions={"부문": sector} if sector else {},
                        region="GLOBAL",
                        year=None,
                        extra={"국내에너지원 기준": domestic} if domestic is not None else {},
                    )
                )
    if not records:
        raise SourceFormatError(f"{src.id}: 표를 찾지 못했다")
    return records
