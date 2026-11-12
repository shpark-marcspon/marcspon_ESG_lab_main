"""온실가스종합정보센터 승인 국가 배출계수 공고문에서 전력배출계수를 읽는다.

- 2025년 공고: HWPX (zip 안의 section0.xml 표). 본표 16개 + 주석의 기존 산정기준 표 16개.
- 2024년 공고: PDF 표. 본표 8개. 주석의 기존 산정기준 표는 표 안에 단위가 없어 읽지 않는다.

공고문의 농업·LULUCF·폐기물 표는 기업 Scope 1·2 계수가 아니라서 싣지 않는다.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile

import pymupdf

from ..record import make_record, parse_number
from ..sources import SOURCES, SourceFormatError

SOURCE_2025 = "gir-2025"
SOURCE_2024 = "gir-2024"

# 단위 문자열 → (기체, 값의 성격)
UNIT_GAS = {
    "t CO2-eq/MWh": ("CO2e", "co2e"),
    "t CO2eq/MWh": ("CO2e", "co2e"),  # 2024년 공고의 표기
    "t CO2/MWh": ("CO2", "gas_mass"),
    "kg CH4/MWh": ("CH4", "gas_mass"),
    "kg N2O/MWh": ("N2O", "gas_mass"),
}
BASIS_2006 = "’06 IPCC 지침"
BASIS_1996 = "’96 IPCC 지침 및 기존 에너지수급통계 (기존 공표 산정기준)"
_YEAR = re.compile(r"^[‘’'](\d\d)년$")


def _local(el: ET.Element) -> str:
    return el.tag.rsplit("}", 1)[-1]


def _text(el: ET.Element) -> str:
    return "".join(t.text or "" for t in el.iter() if _local(t) == "t").strip()


def hwpx_tables(path) -> list[list[list[str | None]]]:
    """HWPX 의 모든 표를 격자로. 병합 셀은 걸친 칸마다 같은 글자를 채운다. 바깥 표의 셀 글자는 안쪽 표 글자를 포함한다."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("Contents/section0.xml"))
    tables = []
    for tbl in root.iter():
        if _local(tbl) != "tbl":
            continue
        rows, cols = int(tbl.get("rowCnt")), int(tbl.get("colCnt"))
        grid: list[list[str | None]] = [[None] * cols for _ in range(rows)]
        for tr in tbl:
            if _local(tr) != "tr":
                continue
            for tc in tr:
                if _local(tc) != "tc":
                    continue
                parts = {_local(c): c for c in tc}
                addr, span = parts["cellAddr"], parts["cellSpan"]
                text = _text(parts["subList"])
                r0, c0 = int(addr.get("rowAddr")), int(addr.get("colAddr"))
                for r in range(r0, r0 + int(span.get("rowSpan"))):
                    for c in range(c0, c0 + int(span.get("colSpan"))):
                        grid[r][c] = text
        tables.append(grid)
    return tables


def _year(period: str) -> int | None:
    m = _YEAR.match(period)
    return 2000 + int(m.group(1)) if m else None


def _elec_record(src, *, key, locator, period, basis, label, raw, unit, guideline, extra=None):
    if unit not in UNIT_GAS:
        raise SourceFormatError(f"{src.id} {locator}: 모르는 단위 {unit!r}")
    gas, gas_basis = UNIT_GAS[unit]
    num, den = unit.split("/")
    conditions = {"기준": basis, "산정기준": guideline}
    if period:
        conditions["기간"] = period
    return make_record(
        src,
        key=key,
        locator=locator,
        scope="scope2",
        factor_type="activity",
        name_original=" ".join(x for x in ("전력배출계수", period, basis, label) if x),
        name_lang="ko",
        category_path=["에너지", "전력배출계수"],
        value=parse_number(raw, f"{src.id} {locator}"),
        value_raw=raw,
        unit=unit,
        unit_numerator=num,
        unit_denominator=den,
        gas=gas,
        gas_basis=gas_basis,
        conditions=conditions,
        region="KR",
        year=_year(period) if period else None,
        extra=extra,
    )


def load_2025() -> list[dict]:
    src = SOURCES[SOURCE_2025]
    tables = hwpx_tables(src.require())
    main = [t for t in tables if len(t[0]) == 6 and t[0][0] == "부문" and t[1][3:] == ["계수", "계수값", "단위"]]
    alt = [t for t in tables if len(t[0]) == 6 and t[0][2:] == ["CO2-eq 계수", "CO2 계수", "CH4 계수", "N2O 계수"]]
    note = [t for t in tables if len(t) == 1 and len(t[0]) == 1 and "’96 IPCC 지침" in (t[0][0] or "")]
    if len(main) != 1 or len(alt) != 1 or len(note) != 1:
        raise SourceFormatError(f"{src.id}: 전력배출계수 표를 찾지 못했다 (본표 {len(main)}, 주석 표 {len(alt)}, 주석 {len(note)})")
    records = []
    for r, row in enumerate(main[0][2:], start=2):
        sector, period, basis, label, raw, unit = row
        if not sector.startswith("간접"):
            raise SourceFormatError(f"{src.id}: 본표 {r}행 부문 {sector!r}")
        records.append(
            _elec_record(src, key=f"main:r{r}", locator=f"HWPX 전력배출계수 표 {r}행",
                         period=period, basis=basis, label=label, raw=raw, unit=unit, guideline=BASIS_2006)
        )
    labels = alt[0][0][2:]
    units = [u.strip("()") for u in alt[0][1][2:]]
    for r, row in enumerate(alt[0][2:], start=2):
        period, basis = row[0], row[1]
        for c, (label, unit, raw) in enumerate(zip(labels, units, row[2:]), start=2):
            records.append(
                _elec_record(src, key=f"alt:r{r}c{c}", locator=f"HWPX 주석 2) 표 {r}행 {c}열",
                             period=period, basis=basis, label=label, raw=raw, unit=unit, guideline=BASIS_1996)
            )
    return records


# PDF 는 아래첨자 숫자가 줄바꿈으로 떨어져 나온다. 추출된 꼴 → 원 단위
_PDF_UNITS = {
    "t CO eq/MWh\n2": "t CO2eq/MWh",
    "t CO /MWh\n2": "t CO2/MWh",
    "kg CH /MWh\n4": "kg CH4/MWh",
    "kg N O/MWh\n2": "kg N2O/MWh",
}
_PDF_LABELS = {
    "CO eq. 배출계수\n2": "CO2eq. 배출계수",
    "CO 배출계수\n2": "CO2 배출계수",
    "CH 배출계수\n4": "CH4 배출계수",
    "N O 배출계수\n2": "N2O 배출계수",
}


def load_2024() -> list[dict]:
    src = SOURCES[SOURCE_2024]
    doc = pymupdf.open(src.require())
    tables = [t.extract() for t in doc[0].find_tables().tables]
    main = [t for t in tables if t and t[0][:3] == ["부문", "항목", "배출계수"]]
    if len(main) != 1:
        raise SourceFormatError(f"{src.id}: 전력배출계수 표를 찾지 못했다 ({len(main)})")
    page_text = doc[0].get_text()
    records, basis = [], None
    for r, row in enumerate(main[0][2:], start=2):
        _, basis_cell, label, raw, unit = row
        basis = basis_cell or basis
        if basis is None or label not in _PDF_LABELS or unit not in _PDF_UNITS:
            raise SourceFormatError(f"{src.id}: 표 {r}행을 읽을 수 없다 {row}")
        if _PDF_UNITS[unit] not in page_text:
            raise SourceFormatError(f"{src.id}: 쪽 글자에 단위 {_PDF_UNITS[unit]!r} 가 없다")
        records.append(
            _elec_record(src, key=f"main:r{r}", locator=f"PDF 1쪽 전력배출계수 표 {r}행",
                         period="", basis=basis, label=_PDF_LABELS[label], raw=raw, unit=_PDF_UNITS[unit],
                         guideline=BASIS_2006,
                         extra={"unit_as_extracted": unit, "label_as_extracted": label,
                                "note": "단위와 계수명은 PDF 추출에서 떨어진 아래첨자를 제자리에 붙인 것"})
        )
    return records
