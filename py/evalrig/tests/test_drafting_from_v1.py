"""evalrig.drafting.from_v1 — 지은 bullets@3."""
import pytest

from evalrig.drafting.from_v1 import convert_bullets, num, numbers
from evalrig.drafting.from_v2 import convert_result_v2

RES = {
    "schemaVersion": "drafting.bullets@3",
    "leaves": [
        {"key": "L1", "titlePath": ["가", "나"], "pages": [3], "outcome": {"status": "done", "bullets": [
            {"text": "배출량 1,120 t 감축", "sources": [{"documentId": 1, "blockId": "p3-b1"}], "unsupported": False},
            {"text": "목표 2030년 12.5%", "sources": [], "unsupported": True}]}},
        {"key": "L2", "titlePath": ["다"], "pages": [], "outcome": {"status": "no_material"}},
    ],
    "warnings": [{"code": "number_not_in_source", "key": "L1"}, {"code": "number_not_in_source", "key": "L1"},
                 {"code": "bullets_capped", "key": "L2"}, {"code": "x"}],
}


def test_leaf_units():
    us = convert_bullets(RES, run="syn")
    assert [(u.key, u.status) for u in us] == [("L1", "ok"), ("L2", "failed")]
    p = us[0].payload
    assert p["text"] == "배출량 1,120 t 감축\n목표 2030년 12.5%"
    assert p["bullets"] == [{"text": "배출량 1,120 t 감축", "unsupported": False, "n_sources": 1},
                            {"text": "목표 2030년 12.5%", "unsupported": True, "n_sources": 0}]
    assert p["numbers"] == ["1120", "2030", "12.5"]
    assert p["warnings"] == {"number_not_in_source": 2}
    assert us[1].payload["warnings"] == {"bullets_capped": 1} and us[1].payload["outcome_status"] == "no_material"
    assert [s.ref for s in us[0].sources] == ["1:p3-b1"]


def test_num_helpers():
    assert num("1, 234 5") == "12345"
    assert numbers("7 와 12 와 1,000") == ["1000"]


def test_schema_and_v2_slot():
    with pytest.raises(ValueError):
        convert_bullets({"schemaVersion": "drafting.bullets@2"}, run="r")
    with pytest.raises(NotImplementedError):
        convert_result_v2({}, run="r")
