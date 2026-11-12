"""KEITI 환경성적표지 평가계수 (CSV, cp949). 한 행 = 한 레코드. 값은 탄소발자국 열."""

from __future__ import annotations

import csv
import io

from ..record import make_record, parse_number
from ..sources import SOURCES, SourceFormatError

SOURCE_ID = "keiti-epd"

HEADER = [
    "대분류", "구분", "원료_에너지", "단위",
    "자원발자국(kg Sb eq)", "탄소발자국(kg CO2 eq)", "오존층영향(kg CFC11 eq)",
    "산성비(kg SO2 eq)", "부영양화(kg PO43- eq)", "광화학스모그(kg C2H4 eq)", "물소모계수(m3 H2O eq)",
]
NOT_APPLICABLE = "해당없음"

# 대분류 → (scope, Scope 3 카테고리 후보, 계수 유형, 기준)
MAPPING = {
    "원료 및 에너지 생산": ("scope3", [1, 2], "activity", "원료·에너지의 생산 단계 계수 → 구매한 제품(1), 자본재(2) 후보 (PRJ11-RPT-3 권장 조합)"),
    "수송": ("scope3", [4, 9], "distance", "ton·km 수송 계수 → 업스트림(4)·다운스트림(9) 운송 후보"),
    "폐기물 처리": ("scope3", [5, 12], "activity", "폐기물 처리 계수 → 사업장 폐기물(5), 판매 제품 폐기(12) 후보"),
    "연료원별 사용": ("scope1", [], "activity", None),
    "화학반응공정": ("scope1", [], "activity", None),
}


def read_rows() -> list[list[str]]:
    src = SOURCES[SOURCE_ID]
    text = src.require().read_bytes().decode("cp949")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or rows[0] != HEADER:
        raise SourceFormatError(f"{SOURCE_ID}: 머리행이 다르다 {rows[:1]}")
    return rows[1:]


def load() -> list[dict]:
    src = SOURCES[SOURCE_ID]
    records = []
    for i, row in enumerate(read_rows(), start=2):  # 파일의 2행부터 데이터
        if len(row) != len(HEADER):
            raise SourceFormatError(f"{SOURCE_ID}: {i}행 열 수 {len(row)}")
        major, group, item, unit, *_ = row
        if major not in MAPPING:
            raise SourceFormatError(f"{SOURCE_ID}: {i}행 모르는 대분류 {major!r}")
        scope, cats, factor_type, basis = MAPPING[major]
        raw = row[5]
        if raw == NOT_APPLICABLE:
            value, value_raw, status = None, raw, "not_applicable_in_source"
        else:
            value, value_raw, status = parse_number(raw, f"{SOURCE_ID} {i}행"), raw, "ok"
        records.append(
            make_record(
                src,
                key=f"row{i}",
                locator=f"CSV {i}행",
                scope=scope,
                scope3_categories=cats,
                scope3_mapping_basis=basis,
                factor_type=factor_type,
                name_original=item,
                name_lang="ko",
                category_path=[major, group],
                value=value,
                value_raw=value_raw,
                value_status=status,
                unit_numerator="kg CO2 eq",
                unit_denominator=unit,
                gas="CO2e",
                gas_basis="co2e",
                region="KR",
                year=None,
                extra={"other_impacts": dict(zip(HEADER[4:5] + HEADER[6:], row[4:5] + row[6:]))},
            )
        )
    return records
