"""로더별: 원자료 행 수와 레코드 수의 일치, 스키마 통과, 원 값 표본 대조.

행 수와 표본 값은 로더를 거치지 않고 원자료를 따로 읽어 비교한다.
PRJ11-RPT-3 에 적힌 건수와의 대조는 그 보고서의 수치를 그대로 옮긴 것이다.
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter

import openpyxl
import pymupdf
import pytest

from factors.loaders import LOADERS, defra, epa, keiti, kr_ets, kr_gir
from factors.mappings import MAPPING_ONLY_SOURCES
from factors.record import validation_errors
from factors.sources import RAW_DIR, SOURCES


def test_every_record_passes_schema(loaded):
    for sid, records in loaded.items():
        bad = [(r["id"], validation_errors(r)) for r in records if validation_errors(r)]
        assert not bad, f"{sid}: {bad[:3]}"


def test_ids_unique_across_sources(loaded):
    ids = Counter(r["id"] for records in loaded.values() for r in records)
    assert [k for k, v in ids.items() if v > 1] == []


def test_every_record_carries_source_and_license(loaded):
    for sid, records in loaded.items():
        for r in records:
            assert r["source"]["id"] == sid
            assert r["source"]["url"].startswith("https://")
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["source"]["retrieved"])
            assert r["source"]["version"] and r["unit"] and r["region"]
            assert r["license"]["name"] and r["license_status"] in ("ok", "disputed", "unverified")
            assert r["synthetic"] is False and r["adjusted"] is None
            assert (r["value"] is None) == (r["value_status"] != "ok")


# ---------------------------------------------------------------- KEITI

def _keiti_raw_rows():
    text = SOURCES[keiti.SOURCE_ID].path.read_bytes().decode("cp949")
    return list(csv.reader(io.StringIO(text)))[1:]


def test_keiti_row_count_matches_raw_and_rpt3(loaded):
    records = loaded[keiti.SOURCE_ID]
    assert len(records) == len(_keiti_raw_rows())
    assert len(records) == 398  # PRJ11-RPT-3: CSV 398행


def test_keiti_major_groups_vs_rpt3(loaded):
    counts = Counter(r["category_path"][0] for r in loaded[keiti.SOURCE_ID])
    # PRJ11-RPT-3 는 '연료원별 사용 118' 로 적었으나 파일은 113 이다 (198+10+32+113+45 = 398).
    assert counts == {"원료 및 에너지 생산": 198, "수송": 10, "폐기물 처리": 32, "연료원별 사용": 113, "화학반응공정": 45}


def test_keiti_values_equal_raw(loaded):
    raw = _keiti_raw_rows()
    for r, row in zip(loaded[keiti.SOURCE_ID], raw, strict=True):
        assert r["name"]["original"] == row[2]
        assert r["unit_denominator"] == row[3]
        assert r["value_raw"] == row[5]
        assert r["value"] == float(row[5])


def test_keiti_license_is_disputed_and_transport_maps_to_4_9(loaded):
    records = loaded[keiti.SOURCE_ID]
    assert {r["license_status"] for r in records} == {"disputed"}
    transport = [r for r in records if r["category_path"][0] == "수송"]
    assert all(r["scope3_categories"] == [4, 9] and r["factor_type"] == "distance" for r in transport)
    truck = [r for r in transport if r["name"]["original"] == "트럭"]
    # PRJ11-RPT-3 핵심 발견 3 은 트럭 0.1924 로 적었다. 파일의 원 값은 0.192419625 (반올림 전)
    assert [r["value_raw"] for r in truck] == ["0.192419625"]
    assert round(truck[0]["value"], 4) == 0.1924


# ---------------------------------------------------------------- DEFRA

@pytest.fixture(scope="module")
def defra_raw():
    wb = openpyxl.load_workbook(SOURCES[defra.SOURCE_ID].path, read_only=True, data_only=True)
    rows = [r[:10] for r in wb[defra.SHEET].iter_rows(values_only=True)]
    return {r[0]: r for r in rows if isinstance(r[0], str) and re.match(r"^\d+_\d+", r[0])}


def test_defra_row_count_matches_raw(loaded, defra_raw):
    records = loaded[defra.SOURCE_ID]
    assert len(records) == len(defra_raw)
    # PRJ11-RPT-3 는 8,742행으로 적었다. 그 수는 빈 행 하나와 END 표지 행을 포함한 것이고 계수 행은 8,740 이다.
    assert len(records) == 8740


def test_defra_level1_counts_vs_rpt3(loaded):
    counts = Counter(r["category_path"][0] for r in loaded[defra.SOURCE_ID])
    # PRJ11-RPT-3: 화물 1,352, 출장 육상 912, 항공 112, 숙박 55, 재택 3, 자재 168, 해상 12
    assert counts["Freighting goods"] == 1352
    assert counts["Business travel- land"] == 912
    assert counts["Business travel- air"] == 112
    assert counts["Business travel- sea"] == 12
    assert counts["Hotel stay"] == 55
    assert counts["Homeworking"] == 3
    assert counts["Material use"] == 168


def test_defra_values_equal_raw(loaded, defra_raw):
    for r in loaded[defra.SOURCE_ID]:
        raw = defra_raw[r["classification"]["code"]]
        assert r["value"] == raw[9]
        assert r["unit_denominator"] == raw[7] and r["unit_numerator"] == raw[8]


def test_defra_blank_values_are_not_zero(loaded, defra_raw):
    blank_ids = {k for k, row in defra_raw.items() if row[9] is None}
    assert blank_ids, "원자료에 빈 값이 없으면 이 테스트는 아무것도 지키지 못한다"
    got = {r["classification"]["code"]: r for r in loaded[defra.SOURCE_ID]}
    for k in blank_ids:
        assert got[k]["value"] is None
        assert got[k]["value_raw"] is None
        assert got[k]["value_status"] == "not_available_in_source"


def test_defra_scope3_mapping(loaded):
    records = loaded[defra.SOURCE_ID]
    for r in records:
        if r["scope"] == "scope3":
            assert r["scope3_categories"] and r["scope3_mapping_basis"]
        else:
            assert r["scope3_categories"] == [] and r["scope3_mapping_basis"] is None
    by_l1 = {r["category_path"][0]: r["scope3_categories"] for r in records}
    assert by_l1["Freighting goods"] == [4, 9]
    assert by_l1["Business travel- air"] == [6]
    assert by_l1["Business travel- land"] == [6, 7]
    assert by_l1["Homeworking"] == [7]
    assert by_l1["Material use"] == [1, 2]
    freight_units = {r["unit_denominator"] for r in records if r["category_path"][0] == "Freighting goods"}
    assert freight_units <= defra.DISTANCE_UNITS
    assert all(r["factor_type"] == "distance" for r in records if r["category_path"][0] == "Freighting goods")


# ---------------------------------------------------------------- EPA

def _epa_raw():
    with SOURCES[epa.SOURCE_ID].path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))[1:]


def test_epa_row_count_matches_raw_and_rpt3(loaded):
    records = loaded[epa.SOURCE_ID]
    assert len(records) == len(_epa_raw())
    assert len(records) == 1016  # PRJ11-RPT-3: 1,016품목


def test_epa_values_equal_raw(loaded):
    for r, row in zip(loaded[epa.SOURCE_ID], _epa_raw(), strict=True):
        assert r["classification"] == {"system": "NAICS 2017", "code": row[0]}
        assert r["value_raw"] == row[6] and r["value"] == float(row[6])
        assert r["unit"] == row[3]
        assert r["extra"]["sef_without_margins"] == row[4] and r["extra"]["margins"] == row[5]


def test_epa_is_spend_based_with_currency_and_price_year(loaded):
    for r in loaded[epa.SOURCE_ID]:
        assert r["factor_type"] == "spend"
        assert r["spend"] == {"currency": "USD", "price_year": 2022, "price_basis": "purchaser"}
        assert r["scope3_categories"] == [1, 2]
        assert r["adjusted"] is None  # 환율·물가 보정은 하지 않는다


# ---------------------------------------------------------------- 한국 Scope 1·2

def test_gir_2025_counts_and_cross_check_against_pdf(loaded):
    """HWPX 에서 읽은 값이, 같은 공고의 PDF 첨부에도 적혀 있다 (서로 다른 파일로 대조)."""
    records = loaded[kr_gir.SOURCE_2025]
    assert len(records) == 32  # 본표 16 (공고문 '전력배출계수(16개)') + 주석 2) 표 16
    main = [r for r in records if ":main:" in r["id"]]
    assert len(main) == 16
    pdf = pymupdf.open(RAW_DIR / "kr" / "gir-2025-approved-factors.pdf")
    text = "".join(p.get_text() for p in pdf)
    for r in records:
        assert r["value_raw"] in text, r["id"]
    cons = [r for r in main if r["conditions"] == {"기준": "소비단", "산정기준": "’06 IPCC 지침", "기간": "‘23년"} and r["gas"] == "CO2e"]
    assert [(r["value_raw"], r["unit"], r["year"]) for r in cons] == [("0.4173", "t CO2-eq/MWh", 2023)]  # PRJ11-RPT-3 전력배출계수


def test_gir_electricity_gas_basis(loaded):
    for sid in (kr_gir.SOURCE_2025, kr_gir.SOURCE_2024):
        for r in loaded[sid]:
            assert r["scope"] == "scope2" and r["region"] == "KR"
            assert r["gas_basis"] == ("co2e" if r["gas"] == "CO2e" else "gas_mass")
            assert r["conditions"]["기준"] in ("발전단", "소비단")


def test_gir_2024_count_and_values_in_page_text(loaded):
    records = loaded[kr_gir.SOURCE_2024]
    assert len(records) == 8  # 공고문 '전력배출계수(8개)'
    text = pymupdf.open(SOURCES[kr_gir.SOURCE_2024].path)[0].get_text()
    assert "전력배출계수(8개)" in text
    for r in records:
        assert r["value_raw"] in text


def _pdf_text(source_id):
    return "".join(p.get_text() for p in pymupdf.open(SOURCES[source_id].path))


def test_byl12_counts_and_structure(loaded):
    records = loaded[kr_ets.BYL12]
    hv = [r for r in records if r["record_type"] == "heating_value"]
    ef = [r for r in records if r["record_type"] == "emission_factor"]
    text = _pdf_text(kr_ets.BYL12)
    # 발열량 표의 연료 행마다 'MJ/' 로 시작하는 단위가 한 번 나온다
    assert len(hv) == 2 * len(re.findall(r"^MJ/", text, flags=re.M))
    assert {r["heating_value_basis"] for r in hv} == {"net", "gross"}
    # 배출계수 표 머리의 '석유(16)', '가스(3)', '석탄(6)' 을 더한 연료 수
    groups = [int(n) for n in re.findall(r"^(?:석유|가스|석탄)\((\d+)\)$", text, flags=re.M)]
    assert len(ef) == 2 * sum(groups)
    assert {r["gas"] for r in ef} == {"C", "CO2"}
    for r in records:
        assert r["value_raw"] in text, r["id"]


def test_byl12_gross_not_below_net_and_merged_cells_flagged(loaded):
    records = loaded[kr_ets.BYL12]
    by_key = {}
    for r in records:
        if r["record_type"] == "heating_value":
            by_key.setdefault(r["id"].rsplit(":", 1)[0], {})[r["heating_value_basis"]] = r["value"]
    assert all(v["gross"] >= v["net"] for v in by_key.values())
    merged = sorted({r["name"]["original"] for r in records if "병합 셀" in r["conditions"]})
    assert merged == ["경유", "도시가스(LNG)"]


def test_byl11_count_and_values(loaded):
    records = loaded[kr_ets.BYL11]
    text = _pdf_text(kr_ets.BYL11)
    assert len(records) == len(re.findall(r"^TJ/Gg$", text, flags=re.M))
    assert all(r["heating_value_basis"] == "net" and r["region"] == "GLOBAL" for r in records)
    missing = [r for r in records if r["value_status"] != "ok"]
    assert [(r["name"]["original"], r["value_raw"], r["value"]) for r in missing] == [("산업 폐기물", "-", None)]


def test_byl10_seven_series_per_fuel(loaded):
    records = loaded[kr_ets.BYL10]
    per_row = Counter(r["id"].rsplit(":", 2)[0] for r in records)
    assert set(per_row.values()) == {7}  # CO2 1 + CH4 부문 4 + N2O 부문 2
    # 별표 10 과 별표 11 은 같은 연료 목록을 쓴다
    assert len(per_row) == len(loaded[kr_ets.BYL11])
    text = _pdf_text(kr_ets.BYL10)
    for r in records:
        assert r["value_raw"] in text, r["id"]
        assert r["unit"] == f"kg{r['gas']}/TJ" and r["gas_basis"] == "gas_mass"


def test_loader_registry_covers_every_source():
    # 별표 5 는 계수 레코드가 없고 대응표(최소 산정등급)에만 쓰인다
    assert set(LOADERS) | MAPPING_ONLY_SOURCES == set(SOURCES)
    assert not set(LOADERS) & MAPPING_ONLY_SOURCES


# ---------------------------------------------------------------- 표의 행·열 밀림 (쪽 글자 순서와 대조)
# find_tables 가 준 격자를 쓰지 않고, 쪽 글자를 줄 단위로 읽어 숫자가 이어지는 묶음을 따로 만든다.

_NUM_LINE = re.compile(r"^-?\d[\d,]*(\.\d+)?$")


def _number_runs(source_id, length):
    """쪽 글자에서 숫자만 있는 줄이 정확히 length 개 이어진 묶음들 (나온 순서대로)."""
    runs, cur = [], []
    for line in _pdf_text(source_id).split("\n") + [""]:
        s = line.strip()
        if _NUM_LINE.match(s):
            cur.append(s)
        else:
            if len(cur) == length:
                runs.append(cur)
            cur = []
    return runs


def test_byl10_columns_follow_page_text_order(loaded):
    rows: dict[str, list[dict]] = {}
    for r in loaded[kr_ets.BYL10]:
        rows.setdefault(r["id"].rsplit(":", 2)[0], []).append(r)
    got = [[r["value_raw"] for r in rs] for rs in rows.values()]
    assert got == _number_runs(kr_ets.BYL10, 7)
    for rs in rows.values():
        assert [(r["gas"], r["conditions"].get("부문")) for r in rs] == [
            ("CO2", None), ("CH4", "에너지산업"), ("CH4", "제조업건설업"), ("CH4", "상업공공"), ("CH4", "가정기타"),
            ("N2O", "에너지산업제조업건설업"), ("N2O", "상업공공가정기타"),
        ]


def test_byl12_pairs_follow_page_text_order(loaded):
    records = loaded[kr_ets.BYL12]
    hv = [r for r in records if r["record_type"] == "heating_value"]
    ef = [r for r in records if r["record_type"] == "emission_factor" and "병합 셀" not in r["conditions"]]
    pairs = _number_runs(kr_ets.BYL12, 2)
    hv_pairs = [[a["value_raw"], b["value_raw"]] for a, b in zip(hv[::2], hv[1::2])]
    ef_pairs = [[a["value_raw"], b["value_raw"]] for a, b in zip(ef[::2], ef[1::2])]
    assert [(a["heating_value_basis"], b["heating_value_basis"]) for a, b in zip(hv[::2], hv[1::2])] == [("gross", "net")] * len(hv_pairs)
    assert [(a["gas"], b["gas"]) for a, b in zip(ef[::2], ef[1::2])] == [("C", "CO2")] * len(ef_pairs)
    # 쪽 순서: 발열량(1~2쪽) → 배출계수(3~4쪽)
    assert pairs == hv_pairs + ef_pairs
    # 이름과 값의 짝: 쪽 글자에서 연료명 줄 바로 뒤에 그 행의 값이 온다
    lines = [re.sub(r"\s+", "", s) for s in _pdf_text(kr_ets.BYL12).split("\n")]
    start = next(i for i, s in enumerate(lines) if s.endswith("연료별국가고유배출계수"))
    checked = 0
    for a, b in zip(ef[::2], ef[1::2]):
        name = re.sub(r"\s+", "", a["name"]["original"])
        if name not in lines[start:]:
            continue  # 칸 안에서 줄이 꺾여 쪽 글자에서는 두 줄로 나오는 이름 (수입무연탄 등)
        i = lines.index(name, start)
        assert lines[i + 1 : i + 3] == [a["value_raw"], b["value_raw"]], name
        checked += 1
    assert checked >= len(ef_pairs) - 4  # 꺾인 이름은 수입무연탄 2, 유연탄 2 까지


def test_byl11_values_follow_page_text_order(loaded):
    lines = [s.strip() for s in _pdf_text(kr_ets.BYL11).split("\n")]
    after_unit = [lines[i + 1] for i, s in enumerate(lines) if s == "TJ/Gg"]
    assert [r["value_raw"] for r in loaded[kr_ets.BYL11]] == after_unit


def test_gir_2024_order_units_and_basis(loaded):
    records = loaded[kr_gir.SOURCE_2024]
    lines = [s.strip() for s in pymupdf.open(SOURCES[kr_gir.SOURCE_2024].path)[0].get_text().split("\n")]
    first, second = lines.index("발전단"), lines.index("소비단")
    assert first < second
    # 값 줄 바로 다음 줄이 단위다. 본표의 여덟 쌍을 쪽 글자에서 따로 읽는다
    pairs = [(lines[i], lines[i + 1], "발전단" if i < second else "소비단")
             for i in range(first, len(lines) - 1) if re.fullmatch(r"\d\.\d{4}", lines[i]) and "/MWh" in lines[i + 1]]
    assert [(r["value_raw"], r["unit"], r["conditions"]["기준"]) for r in records] == pairs
    assert {r["unit"] for r in records} == {"t CO2eq/MWh", "t CO2/MWh", "kg CH4/MWh", "kg N2O/MWh"}


def _assert_names_sit_next_to_their_values(source_id, rows, min_checked, after_line=None):
    """rows: (연료명, [그 행의 값들]) 을 표 순서대로. 쪽 글자에서 이름 줄 뒤 몇 줄 안에 그 행의 값이 와야 한다.

    칸 안에서 줄이 꺾여 쪽 글자에 한 줄로 나오지 않는 이름은 대조하지 못한다. 대조한 수의 하한을 둔다.
    """
    lines = [re.sub(r"\s+", "", s) for s in _pdf_text(source_id).split("\n")]
    cursor = checked = 0
    for name, values in rows:
        key = re.sub(r"\s+", "", name)
        if key not in lines[cursor:]:
            continue
        i = lines.index(key, cursor)
        if after_line:  # 값이 단위 줄 바로 뒤에 온다
            j = lines.index(after_line, i) + 1
        else:
            j = next(k for k in range(i + 1, len(lines)) if _NUM_LINE.match(lines[k]))
        assert j - i <= 4, (name, lines[i : j + 1])
        assert lines[j : j + len(values)] == values, name
        cursor, checked = i + 1, checked + 1
    assert checked >= min_checked, checked


def test_byl10_names_sit_next_to_their_values(loaded):
    rows: dict[str, list[dict]] = {}
    for r in loaded[kr_ets.BYL10]:
        rows.setdefault(r["id"].rsplit(":", 2)[0], []).append(r)
    pairs = [(rs[0]["name"]["original"], [r["value_raw"] for r in rs]) for rs in rows.values()]
    _assert_names_sit_next_to_their_values(kr_ets.BYL10, pairs, min_checked=40)


def test_byl11_names_sit_next_to_their_values(loaded):
    pairs = [(r["name"]["original"], [r["value_raw"]]) for r in loaded[kr_ets.BYL11]]
    _assert_names_sit_next_to_their_values(kr_ets.BYL11, pairs, min_checked=40, after_line="TJ/Gg")


def test_byl12_heating_names_sit_next_to_their_values(loaded):
    hv = [r for r in loaded[kr_ets.BYL12] if r["record_type"] == "heating_value"]
    pairs = [(a["name"]["original"], [a["value_raw"], b["value_raw"]]) for a, b in zip(hv[::2], hv[1::2])]
    _assert_names_sit_next_to_their_values(kr_ets.BYL12, pairs, min_checked=24)
