"""미국 EPA Supply Chain GHG Emission Factors v1.3 (NAICS-6, CO2e, 2022 USD). 한 행 = 한 레코드.

값은 'Supply Chain Emission Factors with Margins' (구매자 가격 기준). 마진 제외 값과 마진은 extra 에 원문 그대로 둔다.
"""

from __future__ import annotations

import csv
import re

from ..record import make_record, parse_number
from ..sources import SOURCES, SourceFormatError

SOURCE_ID = "epa-seef-v1.3"
HEADER = [
    "2017 NAICS Code", "2017 NAICS Title", "GHG", "Unit",
    "Supply Chain Emission Factors without Margins", "Margins of Supply Chain Emission Factors",
    "Supply Chain Emission Factors with Margins", "Reference USEEIO Code",
]
_UNIT = re.compile(r"^(kg CO2e)/((\d{4}) (USD), (purchaser) price)$")
BASIS = "지출 기반 산업 부문 계수 → 구매한 제품·서비스(1), 자본재(2) 후보 (PRJ11-RPT-3)"


def read_rows() -> list[list[str]]:
    src = SOURCES[SOURCE_ID]
    with src.require().open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows or rows[0] != HEADER:
        raise SourceFormatError(f"{SOURCE_ID}: 머리행이 다르다 {rows[:1]}")
    return rows[1:]


def load() -> list[dict]:
    src = SOURCES[SOURCE_ID]
    records = []
    for i, row in enumerate(read_rows(), start=2):
        where = f"{SOURCE_ID} {i}행"
        if len(row) != len(HEADER):
            raise SourceFormatError(f"{where}: 열 수 {len(row)}")
        code, title, ghg, unit, without, margins, with_margins, useeio = row
        m = _UNIT.match(unit)
        if ghg != "All GHGs" or not m:
            raise SourceFormatError(f"{where}: 예상과 다른 GHG·단위 {(ghg, unit)}")
        records.append(
            make_record(
                src,
                key=code,
                locator=f"CSV {i}행",
                scope="scope3",
                scope3_categories=[1, 2],
                scope3_mapping_basis=BASIS,
                factor_type="spend",
                name_original=title,
                name_lang="en",
                category_path=[],
                classification={"system": "NAICS 2017", "code": code},
                value=parse_number(with_margins, where),
                value_raw=with_margins,
                unit=unit,
                unit_numerator=m.group(1),
                unit_denominator=m.group(2),
                gas="CO2e",
                gas_basis="co2e",
                region="US",
                year=None,
                spend={"currency": m.group(4), "price_year": int(m.group(3)), "price_basis": m.group(5)},
                extra={
                    "sef_without_margins": without,
                    "margins": margins,
                    "reference_useeio_code": useeio,
                    "value_column": HEADER[6],
                },
            )
        )
    return records
