"""채점 단위·어댑터(rows/1·result@1·result@2 자리). 고정물은 지은 것(synthetic). import 는 절대 경로만(critic 1 R1)."""

import json

import pytest

from scope3colab.rows import fields_of
from evalrig.scope3 import cli
from evalrig.scope3.adapters import AdapterUnavailable, adapt
from evalrig.scope3.adapters.colab157 import ROWS1_VALUE_FIELD, from_result1, from_rows1
from evalrig.scope3.tests import build
from evalrig.scope3.unit import (
    CATEGORIES,
    FACTOR_KEY_FIELDS,
    ROWS1_NO_CLASSIFICATION,
    Factor,
    Key,
    ToolFailure,
    Unit,
)


def test_unit_json_round_trip():
    u = Unit(Key("sheetOrder", "C1", 9, "가상 품목"), "C1", Factor({"대표EF": "가상"}, "0.9"), "0.99",
             candidates=("가상", "가상2"), classification={"group": "가상 묶음", "naics": {"status": "absent", "reason": "가상"}})  # fmt: skip
    assert Unit.from_json(json.loads(json.dumps(u.to_json()))) == u
    k = Key("rowId", row_id="C6-1_가상!9")
    assert Key.from_json(k.to_json()) == k


def test_key_shapes_are_exclusive():
    with pytest.raises(ToolFailure):
        Key("sheetOrder", "C1", 9, "가상", row_id="x")
    with pytest.raises(ToolFailure):
        Key("rowId", sheet="C1", row_id="x")


def test_candidates_only_for_c1_c2():
    with pytest.raises(ToolFailure):
        Unit(Key("rowId", row_id="x"), "C3", None, None, candidates=("가상",))


def test_factor_key_fields_within_fields_of():
    """계수 열쇠 칸(critic 1 B2)은 그 시트·갈래의 scope3colab `fields_of` 안에 있다. 값 칸도."""
    for (cat, branch), names in FACTOR_KEY_FIELDS.items():
        have = {name for name, _, _ in fields_of(cat, branch)}
        assert set(names) <= have, (cat, branch)
    for (cat, branch), value_field in ROWS1_VALUE_FIELD.items():
        have = {name for name, _, _ in fields_of(cat, branch)}
        assert value_field is None or value_field in have, (cat, branch)
    assert {c for c, _ in ROWS1_VALUE_FIELD} == set(CATEGORIES)


def test_factor_key_table_spot_checks():
    assert FACTOR_KEY_FIELDS[("C6", None)] == ("EF_DB", "NAICS")
    assert FACTOR_KEY_FIELDS[("C11", None)] == FACTOR_KEY_FIELDS[("C14", None)] == ("EF매핑명", "EF단위")
    assert FACTOR_KEY_FIELDS[("C12", None)] == ("바이오매스 구분",)
    assert ("C4", None) not in FACTOR_KEY_FIELDS and ("C15", None) not in FACTOR_KEY_FIELDS


def test_rows1_classification_absent_not_filled():
    """rows/1 에는 묶음·NAICS 칸이 없다 — 산정출처·품목 글로 메우지 않는다(critic 1 B1)."""
    out = from_rows1(build.rows1([build.purchase_row(1, "가상 계수 A", "0.9", ["가상 계수 B"])]))
    (u,) = out.units
    assert u.classification == ROWS1_NO_CLASSIFICATION
    assert out.key_by == "sheetOrder"


def test_rows1_candidates_dedup_and_drop_empty():
    """R4: 추천이 대표를 다시 실으면 겹친 것을 빼고, 빈 자리는 목록에서 뺀다(None 아님)."""
    row = build.purchase_row(1, "가상 계수 A", "0.9", ["가상 계수 A", None, "가상 계수 B", "가상 계수 C"])
    (u,) = from_rows1(build.rows1([row])).units
    assert u.candidates == ("가상 계수 A", "가상 계수 B", "가상 계수 C")


def test_rows1_energy_factor_keys():
    (u,) = from_rows1(build.rows1([build.energy_row(1, "가상 계수", "kgCO2eq/L", "0.9", "0.09")])).units
    assert u.factor == Factor({"EF매핑명": "가상 계수", "EF단위": "kgCO2eq/L"}, "0.9")
    assert u.candidates is None and u.classification is None


def test_result1_purchase_and_activity_mapping():
    out = from_result1(build.base_result1())
    by = {u.key.row_id: u for u in out.units}
    c1 = by["C1_가상!4"]
    assert c1.candidates == ("가상 계수 A", "가상 계수 B", "가상 계수 C", "가상 계수 D", "가상 계수 E")
    assert c1.classification == {"group": "가상 묶음", "naics": {"status": "absent", "reason": "result@1 의 구매 줄에 NAICS 코드 칸이 없다"}}
    assert by["C1_가상!5"].candidates == ("가상 계수 B", "가상 계수 A")
    c6 = by["C6-2_가상!4"]
    assert c6.category == "C6" and c6.branch is None
    assert c6.factor == Factor({"EF_DB": "가상 항공 지출", "NAICS": "999999"}, "0.9")
    assert by["C15_가상!4"].factor is None
    assert out.notes["recommendationRepeatsRepresentative"] == 0


def test_result1_other_kinds_mapping():
    data = build.result1([
        build.processed("C5", "Category5", [build.activity1("C5!4", "waste", treatment="가상 소각", biogenic_note="가상 화석")]),
        build.processed("C7", "Category7", [build.activity1("C7!4", "commute", mode="가상 버스")]),
        build.processed("C6-1", "Category6", [build.activity1("C6-1!4", "travel_distance", mode="가상 버스")]),
        build.processed("C11", "Category11", [build.energy1("C11!4", "product_use", title="가상 전기", unit="kgCO2eq/kWh")]),
        build.processed("C13", "Category13", [build.energy1("C13!4", "leased_out_asset", unit="kgCO2eq/kWh")]),
        build.processed("C4-2", "Category4", [build.activity1("C4-2!4", "transport_spend", title="가상", code="999")]),
    ])  # fmt: skip
    by = {u.key.row_id: u for u in from_result1(data).units}
    assert by["C5!4"].factor.key_fields == {"처리방법_정규화": "가상 소각", "바이오매스 구분": "가상 화석"}
    assert by["C7!4"].factor.key_fields == {"정규화이동수단": "가상 버스"}
    assert by["C6-1!4"].branch == "Distance" and by["C6-1!4"].factor.key_fields == {"정규화이동수단": "가상 버스"}
    assert by["C11!4"].factor.key_fields == {"EF매핑명": "가상 전기", "EF단위": "kgCO2eq/kWh"}
    assert by["C13!4"].factor.key_fields == {"EF단위": "kgCO2eq/kWh"}
    assert by["C4-2!4"].factor.key_fields == {}


def test_result1_failed_sheet_kept_as_sheet_state():
    out = from_result1(build.base_result1())
    assert [(s.category, s.status) for s in out.sheets if s.status == "failed"] == [("C5", "failed")]
    assert not any(u.category == "C5" for u in out.units)


def test_result1_rejected_and_unknown_version_fail():
    with pytest.raises(ToolFailure):
        from_result1({"schemaVersion": "scope3.colab.result@1", "status": "rejected", "rejected": {"reason": "reader_problem", "detail": ""}})
    with pytest.raises(ToolFailure):
        adapt({"schemaVersion": "scope3.colab.result@9", "status": "done"})


def test_result1_duplicate_row_id_fails():
    data = build.result1([build.processed("C3", "Category3", [build.energy1("C3!4", "fuel_energy"), build.energy1("C3!4", "fuel_energy")])])
    with pytest.raises(ToolFailure):
        from_result1(data)


def test_result2_unavailable_exit2(tmp_path, capsys):
    """2차 자리: 빈 목록이 아니라 AdapterUnavailable(도구 실패 2)."""
    with pytest.raises(AdapterUnavailable):
        adapt({"schemaVersion": "scope3.next.result@2"})
    gold = tmp_path / "out" / "gold.json"
    gold.parent.mkdir()
    gold.write_text(json.dumps(_empty_gold()), "utf-8")
    res = tmp_path / "r2.json"
    res.write_text(json.dumps({"schemaVersion": "scope3.next.result@2", "rows": []}), "utf-8")
    assert cli.main(["score", "--gold", str(gold), "--result", str(res), "--out", str(tmp_path / "out" / "s")]) == 2
    assert "result@2" in capsys.readouterr().err


def _empty_gold():
    cats = {c: {"status": "absent", "reason": "가상"} for c in CATEGORIES}
    return {"schema": "evalrig.scope3.gold/1", "meta": {"keyBy": "rowId", "categories": cats}, "units": []}
