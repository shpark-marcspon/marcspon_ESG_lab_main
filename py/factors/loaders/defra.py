"""영국 DESNZ GHG Conversion Factors 2026 flat file (xlsx). 한 행 = 한 레코드.

열: ID, Scope, Level 1~4, Column Text, UOM, GHG/Unit, GHG Conversion Factor 2026.
값이 빈칸인 행은 출처가 자료 없음으로 둔 것이다 (Front page 의 v1.2 수정 안내). 0 으로 채우지 않는다.
"""

from __future__ import annotations

import openpyxl

from ..record import make_record
from ..sources import SOURCES, SourceFormatError

SOURCE_ID = "defra-2026"
SHEET = "Factors by Category"
HEADER = ("ID", "Scope", "Level 1", "Level 2", "Level 3", "Level 4", "Column Text", "UOM", "GHG/Unit", "GHG Conversion Factor 2026")
HEADER_ROW = 6  # 시트의 6행이 머리행

SCOPES = {"Scope 1": "scope1", "Scope 2": "scope2", "Scope 3": "scope3", "Outside of Scopes": "outside_of_scopes"}

GAS = {
    "kg CO2e": ("emission_factor", "CO2e", "co2e"),
    "kg CO2e of CO2 per unit": ("emission_factor", "CO2", "co2e"),
    "kg CO2e of CH4 per unit": ("emission_factor", "CH4", "co2e"),
    "kg CO2e of N2O per unit": ("emission_factor", "N2O", "co2e"),
    "kWh (Net CV)": ("energy_conversion", "none", "not_applicable"),
    "kWh (net)": ("energy_conversion", "none", "not_applicable"),
}

DISTANCE_UNITS = {"km", "miles", "tonne.km", "passenger.km"}

# Scope 3 인 Level 1 → (카테고리 후보, 기준). Scope 3 행의 Level 1 이 여기에 없으면 실패한다.
SCOPE3_LEVEL1 = {
    "Freighting goods": ([4, 9], "화물 운송 → 업스트림(4)·다운스트림(9) 운송. 누가 운임을 내는지는 계수로 가를 수 없어 둘 다 후보"),
    "WTT- delivery vehs & freight": ([4, 9], "화물 운송의 연료 생산 단계(WTT) → 4·9 에 얹는 값"),
    "Business travel- air": ([6], "항공 출장"),
    "WTT- business travel- air": ([6], "항공 출장의 WTT"),
    "Business travel- sea": ([6], "해상 출장"),
    "WTT- business travel- sea": ([6], "해상 출장의 WTT"),
    "Business travel- land": ([6, 7], "육상 여객 계수. 출장(6)과 통근(7)이 같은 차량·대중교통 계수를 쓴다"),
    "WTT- pass vehs & travel- land": ([6, 7], "육상 여객의 WTT. 출장(6)과 통근(7)"),
    "Hotel stay": ([6], "출장 숙박"),
    "Homeworking": ([7], "재택근무"),
    "Material use": ([1, 2], "자재 생산 단계 계수 → 구매한 제품(1), 자본재(2) 후보"),
    "Water supply": ([1], "상수 공급 → 구매한 제품·서비스"),
    "Waste disposal": ([5, 12], "폐기물 처리 → 사업장 폐기물(5), 판매 제품 폐기(12) 후보"),
    "Water treatment": ([5], "하수 처리 → 사업장 폐기물"),
    "WTT- fuels": ([3], "연료의 생산 단계 → 연료·에너지 관련 활동"),
    "WTT- bioenergy": ([3], "바이오연료의 생산 단계 → 연료·에너지 관련 활동"),
    "WTT- UK electricity": ([3], "전력의 연료 생산 단계 → 연료·에너지 관련 활동"),
    "WTT- heat and steam": ([3], "열·스팀의 연료 생산 단계 → 연료·에너지 관련 활동"),
    "Transmission and distribution": ([3], "송배전 손실 → 연료·에너지 관련 활동"),
    "UK electricity T&D for EVs": ([3], "전기차 충전 전력의 송배전 손실 → 연료·에너지 관련 활동"),
    "Managed assets- vehicles": ([8, 13], "운영하지만 소유하지 않은 차량 → 임차(8)·임대(13) 자산 후보"),
    "Managed assets- electricity": ([8, 13], "운영하지만 소유하지 않은 자산의 전력 → 임차(8)·임대(13) 자산 후보"),
}


def read_rows() -> list[tuple[int, tuple]]:
    """(시트 행 번호, 셀 값들). 머리행 아래에서 END 표지 앞까지, 완전히 빈 행은 뺀다."""
    src = SOURCES[SOURCE_ID]
    wb = openpyxl.load_workbook(src.require(), read_only=True, data_only=True)
    if SHEET not in wb.sheetnames:
        raise SourceFormatError(f"{SOURCE_ID}: 시트 {SHEET!r} 없음")
    out = []
    for n, row in enumerate(wb[SHEET].iter_rows(values_only=True), start=1):
        if n < HEADER_ROW:
            continue
        row = tuple(row[: len(HEADER)])
        if n == HEADER_ROW:
            if row != HEADER:
                raise SourceFormatError(f"{SOURCE_ID}: 머리행이 다르다 {row}")
            continue
        if all(c is None for c in row):
            continue
        if row[0] is None and row[1] == "END":
            break
        out.append((n, row))
    return out


def load() -> list[dict]:
    src = SOURCES[SOURCE_ID]
    records = []
    for n, (rid, scope_raw, l1, l2, l3, l4, coltext, uom, ghg_unit, val) in read_rows():
        where = f"{SOURCE_ID} {n}행"
        if not rid or scope_raw not in SCOPES or not l1 or not uom or ghg_unit not in GAS:
            raise SourceFormatError(f"{where}: 읽을 수 없는 행 {(rid, scope_raw, l1, uom, ghg_unit)}")
        scope = SCOPES[scope_raw]
        cats, basis = [], None
        if scope == "scope3":
            if l1 not in SCOPE3_LEVEL1:
                raise SourceFormatError(f"{where}: 대응 기준이 없는 Scope 3 Level 1 {l1!r}")
            cats, basis = SCOPE3_LEVEL1[l1]
        record_type, gas, gas_basis = GAS[ghg_unit]
        if val is None:
            value, value_raw, status = None, None, "not_available_in_source"
        elif isinstance(val, (int, float)) and not isinstance(val, bool):
            value, value_raw, status = val, repr(val), "ok"
        else:
            raise SourceFormatError(f"{where}: 숫자가 아닌 값 {val!r}")
        levels = [str(x) for x in (l1, l2, l3, l4) if x is not None]
        name = " / ".join(levels + ([str(coltext)] if coltext is not None else []))
        records.append(
            make_record(
                src,
                key=str(rid),
                locator=f"시트 '{SHEET}' {n}행",
                record_type=record_type,
                scope=scope,
                scope3_categories=cats,
                scope3_mapping_basis=basis,
                factor_type="distance" if uom in DISTANCE_UNITS else "activity",
                name_original=name,
                name_lang="en",
                category_path=levels,
                classification={"system": "DESNZ flat file ID", "code": str(rid)},
                value=value,
                value_raw=value_raw,
                value_status=status,
                unit_numerator=ghg_unit,
                unit_denominator=str(uom),
                gas=gas,
                gas_basis=gas_basis,
                conditions={"column_text": str(coltext)} if coltext is not None else {},
                region="GB",
                year=2026,
            )
        )
    return records
