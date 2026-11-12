"""계수 조합에 필요한 적재분: 별표 6 의 산화계수 레코드와 조립용 대응표.

원자료는 로더를 거치지 않고 따로 읽어 대조한다. 기존 레코드는 적재 뒤에도 글자 그대로 같아야 한다.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re

import pymupdf
import pytest

from factors import build as build_mod
from factors import mappings as mappings_mod
from factors.loaders import kr_ets, kr_ets_byl06
from factors.record import validation_errors
from factors.sources import SOURCES, SourceFormatError

BYL06 = kr_ets_byl06.BYL06

#: 이 밴드 이전(커밋 4b13d4e)의 계수 표. 그 10,742줄은 새 출처를 더한 뒤에도 바이트까지 같아야 한다.
LEGACY_LINES = 10742
LEGACY_SHA256 = "8868d4a80719d06bd5d4a0c95b664c6ed739d32c1a99e9dbd33b1ca3d5600cc1"
#: 밴드 N 뒤(커밋 5f1cd85)의 계수 표. 그 10,751줄(산화계수 9줄 포함)도 표-69 를 더한 뒤 바이트까지 같아야 한다 (MARC-TICK-18).
LEGACY_LINES_N = 10751
LEGACY_SHA256_N = "af2c64e9431773902d52517f1d13d936515fb6f7d347c6dafbe9cf01dd9fa894"


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _page(source_id: str, number: int) -> str:
    return _squash(pymupdf.open(SOURCES[source_id].path)[number - 1].get_text())


# ---------------------------------------------------------------- 기존 레코드 불변

def test_legacy_lines_are_byte_identical(built):
    out, records = built
    lines = (out / build_mod.JSONL_NAME).read_bytes().split(b"\n")
    head = b"\n".join(lines[:LEGACY_LINES]) + b"\n"
    assert hashlib.sha256(head).hexdigest() == LEGACY_SHA256
    tail = [json.loads(line) for line in lines[LEGACY_LINES:] if line]
    assert tail and {r["source"]["id"] for r in tail} == {BYL06}
    assert len(records) == LEGACY_LINES + len(tail)
    head_n = b"\n".join(lines[:LEGACY_LINES_N]) + b"\n"
    assert hashlib.sha256(head_n).hexdigest() == LEGACY_SHA256_N
    assert [json.loads(line)["id"] for line in lines[LEGACY_LINES_N:] if line] == [
        "kr-ets-byl06:elec:t69:CO2", "kr-ets-byl06:elec:t69:CH4", "kr-ets-byl06:elec:t69:N2O",
    ]


def _oxidation(loaded):
    return [r for r in loaded[BYL06] if r["record_type"] == "oxidation_factor"]


def _table69(loaded):
    return [r for r in loaded[BYL06] if r["record_type"] == "emission_factor"]


def test_new_records_do_not_reach_scope3(loaded):
    assert len(_oxidation(loaded)) + len(_table69(loaded)) == len(loaded[BYL06])
    for r in _oxidation(loaded):
        assert r["scope"] == "scope1" and r["scope3_categories"] == []
    for r in _table69(loaded):
        # scope3 의 후보는 emission_factor + CO2e + 카테고리 후보 있음이다. 표-69 는 기체별 값이고 카테고리가 없다.
        assert r["scope"] == "scope2" and r["scope3_categories"] == [] and r["gas"] in ("CO2", "CH4", "N2O") and r["gas_basis"] == "gas_mass"
    assert not any(r["record_type"] == "oxidation_factor" for sid, rs in loaded.items() if sid != BYL06 for r in rs)


# ---------------------------------------------------------------- 산화계수 (별표 6)

def _oxidation_numbers_in_text(state: str) -> list[str]:
    """그 절의 「④산화계수」 뒤부터 절 끝까지 나오는 소수 표기 (로더와 따로, 쪽 글자에서 바로 센다)."""
    doc = pymupdf.open(SOURCES[BYL06].path)
    starts = [p.number for p in doc if re.match(rf"\s*\d+\.\s*고정연소\s*\({state}연료\)", p.get_text())]
    assert len(starts) == 1
    text = ""
    for pno in range(starts[0], len(doc)):
        page = doc[pno].get_text()
        if pno > starts[0] and re.match(r"\s*\d+\.\s*\S+연소", page):
            break
        text += page
    tail = _squash(text).split("④산화계수", 1)[1]
    return re.findall(r"\d+\.\d+", tail)


def test_oxidation_values_match_page_text(loaded):
    records = _oxidation(loaded)
    assert len(records) == 9  # 고체 3 (Tier 1, Tier 2 발전·기타), 기체 3, 액체 3
    for state in kr_ets_byl06.STATES:
        own = [r for r in records if r["conditions"]["연료 상태"] == state]
        assert [r["value_raw"] for r in own] == _oxidation_numbers_in_text(state)
        assert [r["conditions"]["산정등급"] for r in own] == (
            ["Tier 1", "Tier 2", "Tier 2"] if state == "고체" else ["Tier 1", "Tier 2", "Tier 3"]
        )
    solid_t2 = [r["conditions"]["산화계수 부문"] for r in records if "산화계수 부문" in r["conditions"]]
    assert solid_t2 == ["발전부문", "기타부문"]


def test_oxidation_records_unit_locator_and_source(loaded):
    src = SOURCES[BYL06]
    sha = hashlib.sha256(src.path.read_bytes()).hexdigest()
    for r in _oxidation(loaded):
        assert validation_errors(r) == []
        assert (r["unit"], r["unit_numerator"], r["unit_denominator"]) == ("무차원", "무차원", "무차원")
        assert r["gas"] == "CO2" and r["gas_basis"] == "not_applicable" and r["heating_value_basis"] is None
        assert r["value"] == float(r["value_raw"]) and 0 < r["value"] <= 1
        assert r["source"]["file_sha256"] == sha and r["source"]["url"] == src.url and r["source"]["retrieved"] == src.retrieved
        page = int(re.match(r"PDF (\d+)쪽", r["source"]["locator"]).group(1))
        sentence = r["extra"]["원문 (PDF 추출, 공백 없음)"]
        # 문장이 쪽을 넘어갈 수 있어 그 쪽과 다음 쪽을 이어 본다
        assert sentence in _page(BYL06, page) + _page(BYL06, page + 1)
        assert r["value_raw"] in sentence
        assert f"고정연소 ({r['conditions']['연료 상태']}연료)" in r["source"]["locator"]


@pytest.mark.parametrize(
    ("state", "tier", "text"),
    [
        ("액체", 2, "산화계수(fi)는대략0.99를적용한다."),
        ("액체", 2, "산화계수(fi)는0.99를적용한다."),  # Tier 2 에는 단서가 붙어 있어야 한다
        ("기체", 1, "산화계수(fi)는1.0을적용한다."),
        ("고체", 2, "산화계수(fi)는0.99를적용한다."),
        ("고체", 3, "산화계수(f)는0.98을적용한다."),
        ("기체", 4, "산화계수(fi)는0.995를적용한다."),
    ],
)
def test_unknown_oxidation_sentence_raises(state, tier, text):
    with pytest.raises(SourceFormatError):
        kr_ets_byl06._values(state, tier, text)


# ---------------------------------------------------------------- 구매 전력 <표-69> (별표 6)

def test_table69_values_match_page_text(loaded):
    """로더와 따로 쪽 글자에서 읽는다: 표 이름 줄 뒤의 소수 셋이 CO2·CH4·N2O 순서다."""
    doc = pymupdf.open(SOURCES[BYL06].path)
    pages = [p for p in doc if "<표-69>" in p.get_text()]
    assert [p.number + 1 for p in pages] == [274]
    text = _squash(pages[0].get_text())
    after = text.split("<표-69>", 1)[1]
    assert re.findall(r"\d\.\d{4}", after)[:3] == ["0.4567", "0.0036", "0.0085"]
    records = _table69(loaded)
    assert [(r["gas"], r["value_raw"], r["unit"]) for r in records] == [
        ("CO2", "0.4567", "tCO2/MWh"), ("CH4", "0.0036", "kgCH4/MWh"), ("N2O", "0.0085", "kgN2O/MWh"),
    ]
    sha = hashlib.sha256(SOURCES[BYL06].path.read_bytes()).hexdigest()
    for r in records:
        assert validation_errors(r) == []
        assert r["value"] == float(r["value_raw"]) and r["year"] is None and r["region"] == "KR"
        assert r["conditions"] == {"기간": "3개년 평균('14∼'16)"}
        assert r["source"]["file_sha256"] == sha and r["source"]["locator"].startswith("PDF 274쪽 「39. 외부에서 공급된 전기 사용」")
        assert r["extra"]["적용 문장 (PDF 추출, 공백 없음)"] in text and "②배출계수Tier2" in text
        assert r["extra"]["표의 ** 주석 (PDF 추출, 공백 없음, 싣지 않은 적용 규칙)"] in text
        assert r["extra"]["표의 출처 주석 (PDF 추출, 공백 없음)"] in text


def test_table69_loader_refuses_unknown_layout(monkeypatch):
    monkeypatch.setattr(kr_ets_byl06, "_T69_HEADER", ["구분", "CO2", "(tCO2/MWh)", "CH4", "(kgCH4/MWh)", "N2O", "(gN2O/MWh)"])
    with pytest.raises(SourceFormatError):
        kr_ets_byl06.load_electricity()
    monkeypatch.undo()
    monkeypatch.setattr(kr_ets_byl06, "_T69_RULE", "전력배출계수는아래<표-69>의값을적용한다.")
    with pytest.raises(SourceFormatError):
        kr_ets_byl06.load_electricity()


# ---------------------------------------------------------------- 대응표

@pytest.fixture(scope="module")
def mapping(loaded):
    return mappings_mod.build_mappings([r for rs in loaded.values() for r in rs])


def test_mapping_passes_schema_and_cites_sources(mapping):
    assert mappings_mod.validation_errors(mapping) == []
    for key in ("sectors", "sector_overlaps", "fuel_names", "national_ef_names", "fuel_states", "tier_minimums"):
        for entry in mapping[key]:
            assert entry["source_id"] in mapping["sources"] and entry["locator"]
    for sid, block in mapping["sources"].items():
        assert block["file_sha256"] == hashlib.sha256(SOURCES[sid].path.read_bytes()).hexdigest()
        assert block["url"].startswith("https://") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", block["retrieved"])


def test_mapping_schema_rejects_unmarked_or_unsourced_entries(mapping):
    broken = copy.deepcopy(mapping)
    assumed = next(e for e in broken["fuel_names"] if e["basis"] == "assumed")
    assumed["note"] = None
    assert mappings_mod.validation_errors(broken)
    broken = copy.deepcopy(mapping)
    del broken["sectors"][0]["locator"]
    assert mappings_mod.validation_errors(broken)
    broken = copy.deepcopy(mapping)
    broken["fuel_states"][0]["basis"] = "guess"
    assert mappings_mod.validation_errors(broken)
    broken = copy.deepcopy(mapping)
    broken["tier_minimums"][0]["tier"] = 0
    assert mappings_mod.validation_errors(broken)


def test_sectors_match_page_text(mapping, loaded):
    text = _page(kr_ets.BYL10, 4)
    assert [s["label"] for s in mapping["sectors"]] == ["에너지산업", "제조업ㆍ건설업", "상업ㆍ공공", "가정ㆍ기타"]
    for s in mapping["sectors"]:
        assert f"{s['label']}:" in text and s["codes_raw"] + "인사업장" in text
        # 범위를 편 코드 수 = 원문 표기의 낱개 수와 범위 길이의 합
        expected = sum(
            int(hi or lo) - int(lo) + 1 for lo, hi in re.findall(r"(\d{2})(?:∼(\d{2}))?\**", s["codes_raw"])
        )
        assert len(s["ksic_divisions"]) == expected
        ch4 = {r["conditions"]["부문"] for r in loaded[kr_ets.BYL10] if r["gas"] == "CH4"}
        n2o = {r["conditions"]["부문"] for r in loaded[kr_ets.BYL10] if r["gas"] == "N2O"}
        assert s["ch4_condition"] in ch4 and s["n2o_condition"] in n2o
        assert s["label"].replace("ㆍ", "") in s["n2o_condition"]
    assert len({s["key"] for s in mapping["sectors"]}) == 4


def test_overlapping_divisions_are_listed_not_resolved(mapping):
    by_division = {o["division"]: o for o in mapping["sector_overlaps"]}
    assert sorted(by_division) == ["38", "58", "59"]
    holders = {d: [s["key"] for s in mapping["sectors"] if d in s["ksic_divisions"]] for d in by_division}
    for division, overlap in by_division.items():
        assert overlap["sectors"] == holders[division] and len(holders[division]) == 2
        for exception in overlap["exceptions"]:
            assert exception["code_prefix"].startswith(division) and exception["raw"] in _page(kr_ets.BYL10, 4)
    got = {e["code_prefix"]: e["sector"] for o in mapping["sector_overlaps"] for e in o["exceptions"]}
    assert got == {"383": "manufacturing_construction", "5821": "commercial_public", "5912": "commercial_public"}


def test_fuel_names_come_from_the_domestic_column(mapping, loaded):
    for table in (kr_ets.BYL10, kr_ets.BYL11):
        raw = {(r["name"]["original"], r["extra"]["국내에너지원 기준"]) for r in loaded[table] if "국내에너지원 기준" in r["extra"]}
        own = [e for e in mapping["fuel_names"] if e["table"] == table]
        guideline = [e for e in own if e["basis"] == "guideline" and e["domestic_raw"] is not None]
        assert {(e["ipcc_name"], e["domestic_raw"]) for e in guideline} == {x for x in raw if x[1] != "-"}
        diesel = [e for e in own if "경유" in e["domestic_names"]]
        assert [(e["ipcc_name"], e["basis"]) for e in diesel] == [("가스/디젤 오일", "guideline")]
        for e in guideline:
            assert e["domestic_names"] and all("\n" not in n for n in e["domestic_names"])
            if "\n" in e["domestic_raw"] and "," not in e["domestic_raw"]:
                # 줄바꿈 칸은 건별 판단이고 그 까닭이 note 에 있다
                assert (table, e["ipcc_name"]) in mappings_mod.LINE_BREAK_CELLS and e["note"]
        # 한 국내 이름이 (매개변수를 가리지 않고) 두 IPCC 연료를 가리키지 않는다
        names = [n for e in own if not e.get("parameters") for n in e["domestic_names"]]
        assert len(names) == len(set(names))


def test_line_break_cells_are_decided_one_by_one(mapping):
    """별표 10 무연탄(두 이름), 가스 코크스(꺾인 한 이름), 별표 11 무연탄(두 이름). 다른 칸에 규칙으로 번지지 않는다."""
    got = {(e["table"], e["ipcc_name"]): e["domestic_names"] for e in mapping["fuel_names"] if e["domestic_raw"] and "\n" in e["domestic_raw"] and "," not in e["domestic_raw"]}
    assert got == {
        (kr_ets.BYL10, "무연탄"): ["국내 무연탄", "수입 무연탄"],
        (kr_ets.BYL10, "가스 코크스"): ["가스공장 코크스"],
        (kr_ets.BYL11, "무연탄"): ["국내 무연탄", "수입 무연탄"],
    }
    # 쉼표가 있는 칸(JET A-1,⏎JP-8)은 예전처럼 쉼표로 가른다
    jet = [e["domestic_names"] for e in mapping["fuel_names"] if e["ipcc_name"] == "제트용 등유" and e["basis"] == "guideline"]
    assert jet == [["JET A-1", "JP-8"], ["JET A-1", "JP-8"]]


def test_line_break_cell_must_match_what_was_judged(monkeypatch, loaded):
    records = [r for rs in loaded.values() for r in rs]
    original = dict(mappings_mod.LINE_BREAK_CELLS)
    cells = dict(original)
    cells[(kr_ets.BYL10, "무연탄")] = ("국내 무연탄\n수입 무연탄\n(synthetic)", ["국내 무연탄"], "synthetic")
    monkeypatch.setattr(mappings_mod, "LINE_BREAK_CELLS", cells)
    with pytest.raises(SourceFormatError, match="판단할 때 본 글자와 다르다"):
        mappings_mod.build_mappings(records)
    # 판단하지 않은 줄바꿈 칸이 나오면 기계로 가르지 않고 실패한다
    cells = {k: v for k, v in original.items() if k != (kr_ets.BYL10, "가스 코크스")}
    monkeypatch.setattr(mappings_mod, "LINE_BREAK_CELLS", cells)
    with pytest.raises(SourceFormatError, match="판단한 적이 없다"):
        mappings_mod.build_mappings(records)


def test_row_name_fuels_are_guideline_entries(mapping, loaded):
    for table in (kr_ets.BYL10, kr_ets.BYL11):
        rows = [e for e in mapping["fuel_names"] if e["table"] == table and e["domestic_raw"] is None and e["basis"] == "guideline"]
        assert [(e["ipcc_name"], e["domestic_names"]) for e in rows] == [("B-A유", ["B-A유"]), ("B-B유", ["B-B유"])]
        for e in rows:
            own = [r for r in loaded[table] if r["name"]["original"] == e["ipcc_name"]]
            assert own and all("국내에너지원 기준" not in r["extra"] for r in own) and e["note"]


def test_assumed_entries_are_marked_and_explained(mapping):
    assumed = [e for e in mapping["fuel_names"] if e["basis"] == "assumed"]
    pairs = [(e["table"], e["domestic_names"][0], e["ipcc_name"], e.get("parameters")) for e in assumed]
    byl10 = [
        ("도시가스(LNG)", "천연가스", None), ("프로판(LPG1호)", "액화석유가스", None), ("부탄(LPG3호)", "액화석유가스", None),
        ("나프타", "나프타", None), ("항공유", "제트용 등유", None), ("석유코크스", "석유 코크스", None),
        ("국내무연탄", "무연탄", None), ("연료용 수입무연탄", "무연탄", None), ("원료용 수입무연탄", "무연탄", None),
        ("연료용 유연탄(역청탄)", "기타 역청탄", None), ("원료용 유연탄(역청탄)", "점결탄", None), ("코크스", "코크스로 코크스", None),
        ("부생연료유1호", "기타 등유", ["CO2"]), ("부생연료유2호", "잔여 연료유", ["CO2"]),
    ]
    byl11 = [(d, "점결탄(Coking coal)" if i == "점결탄" else i, p) for d, i, p in byl10 if d not in ("코크스", "부생연료유1호", "부생연료유2호")]
    assert pairs == [(kr_ets.BYL10, *x) for x in byl10] + [(kr_ets.BYL11, *x) for x in byl11]
    assert all(e["note"] and e["domestic_raw"] is None and len(e["domestic_names"]) == 1 for e in assumed)
    # 도시가스(LNG) 말고는 근거 문장 자리가 있고, 그 문장은 원문의 그 쪽에 있다
    for e in assumed:
        assert bool(e.get("evidence")) == (e["domestic_names"] != ["도시가스(LNG)"])
        for ev in e.get("evidence", []):
            page = int(re.fullmatch(r"PDF (\d+)쪽", ev["locator"]).group(1))
            assert _squash(ev["quote"]) in _page(ev["source_id"], page)

    national = [(e["fuel"], e["ef_name"], e["basis"]) for e in mapping["national_ef_names"]]
    assert national == [
        ("항공유", "항공유(JET-A1)", "assumed"), ("부탄(LPG3호)", "부탄(LPP3호)", "assumed"),
        ("부생연료유1호", "부생연료 1호", "assumed"), ("부생연료유2호", "부생연료 2호", "assumed"),
        ("연료용 수입무연탄", "수입무연탄(연료용)", "assumed"), ("원료용 수입무연탄", "수입무연탄(원료용)", "assumed"),
        ("연료용 유연탄(역청탄)", "유연탄(연료용)", "assumed"), ("원료용 유연탄(역청탄)", "유연탄(원료용)", "assumed"),
    ]
    assert all(e["note"] for e in mapping["national_ef_names"])

    states = {e["fuel"]: e for e in mapping["fuel_states"]}
    assert states["경유"]["state"] == "액체" and states["경유"]["basis"] == "guideline" and states["경유"]["note"] is None
    assert states["도시가스(LNG)"]["state"] == "기체" and states["도시가스(LNG)"]["basis"] == "assumed" and states["도시가스(LNG)"]["note"]
    by_state = {s: sorted(e["fuel"] for e in mapping["fuel_states"] if e["state"] == s and e["basis"] == "guideline") for s in ("고체", "기체", "액체")}
    assert by_state == {"고체": ["갈탄", "무연탄", "유연탄", "코크스"], "기체": ["LNG", "LPG", "부탄", "프로판"], "액체": ["경유", "등유", "원유", "휘발유"]}
    for e in mapping["fuel_states"]:
        page = int(re.match(r"PDF (\d+)쪽", e["locator"]).group(1))
        assert _squash(e["quote"]) in _page(BYL06, page)


def test_every_national_fuel_has_a_state(mapping, loaded):
    """별표 12 발열량 표의 연료(전기 제외)는 전부 상태가 있다. 지침 문장에 이름이 그대로 없는 것은 추정이고 별표 12 의 분류를 근거로 단다."""
    fuels = sorted({r["name"]["original"] for r in loaded[kr_ets.BYL12] if r["record_type"] == "heating_value"} - {"전기(발전기준)", "전기(소비기준)"})
    assert len(fuels) == 27
    states = {e["fuel"]: e for e in mapping["fuel_states"]}
    assert [f for f in fuels if f not in states] == []
    assumed = sorted(f for f in fuels if states[f]["basis"] == "assumed")
    assert len(assumed) == 22  # 도시가스(LNG) + 이 밴드의 21
    expected = {
        "액체": {"B-A유", "B-B유", "B-C유", "나프타", "용제", "항공유", "아스팔트", "윤활유", "부생연료유1호", "부생연료유2호"},
        "기체": {"프로판(LPG1호)", "부탄(LPG3호)", "천연가스(LNG)", "도시가스(LPG)", "도시가스(LNG)"},
        "고체": {"국내무연탄", "연료용 수입무연탄", "원료용 수입무연탄", "연료용 유연탄(역청탄)", "원료용 유연탄(역청탄)", "아역청탄", "석유코크스"},
    }
    assert {s: {f for f in assumed if states[f]["state"] == s} for s in expected} == expected
    for f in assumed:
        if f == "도시가스(LNG)":
            continue
        ev, *extra = states[f]["evidence"]
        assert ev["source_id"] == kr_ets.BYL12 and ev["quote"] in ("석유(16)", "가스(3)", "석탄(6)")
        for item in [ev, *extra]:
            page = int(re.fullmatch(r"PDF (\d+)쪽", item["locator"]).group(1))
            assert _squash(item["quote"]) in _page(item["source_id"], page)
        # 상태를 직접 적은 근거가 따로 있는 것은 석유코크스뿐이다 (별표 12 분류와 어긋나는 쪽을 따랐다)
        assert bool(extra) == (f == "석유코크스")


def test_assumed_state_class_must_match_the_records(monkeypatch, loaded):
    records = [r for rs in loaded.values() for r in rs]
    wrong = [("아역청탄", "고체", "가스(3)", "synthetic") if x[0] == "아역청탄" else x for x in mappings_mod.ASSUMED_FUEL_STATES]
    monkeypatch.setattr(mappings_mod, "ASSUMED_FUEL_STATES", wrong)
    with pytest.raises(SourceFormatError, match="분류"):
        mappings_mod.build_mappings(records)


def test_evidence_quote_must_exist_in_the_source(monkeypatch, loaded):
    records = [r for rs in loaded.values() for r in rs]
    items = [dict(x) for x in mappings_mod.ASSUMED_FUEL_NAMES]
    items[1]["evidence"] = [mappings_mod._ev(kr_ets.BYL12, 3, "synthetic: 원문에 없는 문장")]
    monkeypatch.setattr(mappings_mod, "ASSUMED_FUEL_NAMES", items)
    with pytest.raises(SourceFormatError, match="근거 문장"):
        mappings_mod.build_mappings(records)


def test_national_name_needs_both_names_in_the_table(monkeypatch, loaded):
    records = [r for rs in loaded.values() for r in rs]
    monkeypatch.setattr(mappings_mod, "NATIONAL_EF_NAMES", [("항공유", "항공유(JET-A2)", "synthetic")])
    with pytest.raises(SourceFormatError, match="양쪽 이름"):
        mappings_mod.build_mappings(records)
    monkeypatch.setattr(mappings_mod, "NATIONAL_EF_NAMES", [("경유", "등유", "synthetic")])
    with pytest.raises(SourceFormatError, match="같은 글자"):
        mappings_mod.build_mappings(records)


def test_fuel_state_quote_must_exist_in_the_source(monkeypatch, loaded):
    records = [r for rs in loaded.values() for r in rs]
    monkeypatch.setattr(mappings_mod, "FUEL_STATES", [("경유", "액체", "guideline", "경유는 액체연료다 (synthetic 인용문)", None)])
    with pytest.raises(SourceFormatError):
        mappings_mod.build_mappings(records)
    # 인용문에 이름이 그대로 없으면 guideline 이라고 적을 수 없다
    monkeypatch.setattr(mappings_mod, "FUEL_STATES", [("도시가스(LNG)", "기체", "guideline", mappings_mod._GAS_QUOTE, None)])
    with pytest.raises(SourceFormatError):
        mappings_mod.build_mappings(records)
    monkeypatch.setattr(mappings_mod, "FUEL_STATES", [("도시가스(LNG)", "기체", "assumed", mappings_mod._GAS_QUOTE, None)])
    with pytest.raises(SourceFormatError):
        mappings_mod.build_mappings(records)


def _assumed(domestic, ipcc, parameters=None):
    return {"domestic": domestic, "ipcc": {kr_ets.BYL10: ipcc, kr_ets.BYL11: ipcc}, "parameters": parameters, "note": "synthetic", "evidence": []}


def test_assumed_name_cannot_shadow_or_miss_the_guideline(monkeypatch, loaded):
    records = [r for rs in loaded.values() for r in rs]
    monkeypatch.setattr(mappings_mod, "ASSUMED_FUEL_NAMES", [_assumed("경유", "천연가스")])
    with pytest.raises(SourceFormatError):
        mappings_mod.build_mappings(records)
    monkeypatch.setattr(mappings_mod, "ASSUMED_FUEL_NAMES", [_assumed("synthetic-연료", "없는 IPCC 연료")])
    with pytest.raises(SourceFormatError):
        mappings_mod.build_mappings(records)
    # 한 국내 이름을 매개변수 구분 없이 두 IPCC 연료에 잇지 않는다
    monkeypatch.setattr(mappings_mod, "ASSUMED_FUEL_NAMES", [_assumed("synthetic-연료", "천연가스"), _assumed("synthetic-연료", "원유")])
    with pytest.raises(SourceFormatError, match="IPCC 연료 둘"):
        mappings_mod.build_mappings(records)


def test_tier_minimums_match_page_text(mapping):
    entries = mapping["tier_minimums"]
    assert len(entries) == 3 * 5 * 3  # 연료 상태 3 × 매개변수 5 × 그룹 3
    # 쪽 글자에서 한 자리 숫자만 있는 줄이 15개 이어진 묶음 (표의 한 행) 을 따로 읽는다
    lines = pymupdf.open(SOURCES[mappings_mod.BYL05].path)[3].get_text().split("\n")
    runs, cur = [], []
    for line in lines + [""]:
        if re.fullmatch(r"\s*[1-4]\s*", line):
            cur.append(int(line))
        else:
            if len(cur) == 15:
                runs.append(cur)
            cur = []
    for k, state in enumerate(("고체", "기체", "액체")):
        got = [e["tier"] for e in entries if e["fuel_state"] == state]
        assert got == runs[k]
    pick = {(e["fuel_state"], e["parameter"], e["group"]): e["tier"] for e in entries}
    # 발주서가 대조하라고 한 주장: A 그룹은 순발열량만 Tier 2 이고 배출계수·산화계수는 Tier 1
    for state in ("고체", "기체", "액체"):
        assert (pick[(state, "순발열량", "A")], pick[(state, "배출계수", "A")], pick[(state, "산화계수", "A")]) == (2, 1, 1)
        assert (pick[(state, "순발열량", "B")], pick[(state, "배출계수", "B")], pick[(state, "산화계수", "B")]) == (2, 2, 2)
        assert (pick[(state, "순발열량", "C")], pick[(state, "배출계수", "C")], pick[(state, "산화계수", "C")]) == (3, 3, 3)


# ---------------------------------------------------------------- 적재 결과물

def test_build_writes_mapping_and_summary(built, mapping):
    out, records = built
    written = json.loads((out / mappings_mod.MAPPING_NAME).read_text(encoding="utf-8"))
    assert written == mapping
    summary = (out / build_mod.SUMMARY_NAME).read_text(encoding="utf-8")
    assert f"| {BYL06} |" in summary and "oxidation_factor" in summary
    assert f"## 조립용 대응표 (`{mappings_mod.MAPPING_NAME}`)" in summary
    assert "- 이름: 도시가스(LNG) → 천연가스 (kr-ets-byl10)" in summary and "- 상태: 도시가스(LNG) → 기체" in summary
    assert build_mod.render_summary(records, written) == summary


def test_build_writes_nothing_when_the_mapping_fails(monkeypatch, tmp_path):
    def boom(records):
        raise SourceFormatError("synthetic failure")

    monkeypatch.setattr(mappings_mod, "build_mappings", boom)
    out = tmp_path / "out"
    with pytest.raises(SourceFormatError):
        build_mod.build(out)
    assert not out.exists()
