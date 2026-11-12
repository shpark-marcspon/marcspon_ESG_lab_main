"""T10·T11·T15 — 1차 데이터 검증 결과(result@2) 변환(지은 픽스처, 실비 0)."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from evalrig.dataverify import adapt_v2, convert_156
from evalrig.dataverify.tests.conftest import DATA_DIR, esg_app_root

FIX = Path(__file__).resolve().parent / "fixtures"


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def inp():
    return json.loads((DATA_DIR / "synthetic-company.input.json").read_text(encoding="utf-8"))


def test_t10_checks_to_units(inp):
    c = convert_156.convert(_load("dv-result-v2.json"), inp)
    chk = {u.key: u.payload for u in c["units"] if u.unit == convert_156.CHECK_UNIT}
    assert len(chk) == 13  # 검산 다섯 × 해(단건 도구는 해 null 하나), 값 조회는 단위 아님
    assert chk["c1:2023"]["items"] == ["E-003", "E-001", "E-002"] and chk["c1:2023"]["verdict"] == "FAIL"
    assert chk["c2:2022"]["items"] == ["E-005", "E-004", "E-007"] and chk["c2:2022"]["rule"] == "ratio"
    assert chk["c5:2022"]["items"] == ["E-005", "E-004"] and len(chk["c5:2022"]["unresolved"]) == 1
    assert chk["c4:None"]["items"] == [] and chk["c4:None"]["year"] is None
    assert c["unresolved"] == {"quads": 1, "checksWithoutNames": 1, "insightRows": 1}
    assert c["reviewStatus"] == "done"


def test_t10_insight_rows(inp):
    c = convert_156.convert(_load("dv-result-v2.json"), inp)
    rows = [u.payload for u in c["units"] if u.unit == convert_156.INSIGHT_UNIT]
    assert [r["id"] for r in rows] == ["E-012", "E-017", None]  # 소분류 글자 · 글 안 ID(유니코드 대시) · 못 풂
    assert all(r["year"] == {"status": "absent"} and r["verdict"] == "flag" for r in rows)


def test_t10_failed_review_is_a_branch(inp):
    c = convert_156.convert(_load("dv-result-v2-failed.json"), inp)
    assert c["reviewStatus"] == "failed" and c["failure"]["kind"] == "timeout"
    assert len([u for u in c["units"] if u.unit == convert_156.CHECK_UNIT]) == 3


def test_t10_list_and_unknown(inp):
    assert len(convert_156.convert_doc([_load("dv-result-v2.json")], inp)) == 1
    side = {"schemaVersion": "dataverify.digest@1"}
    got = convert_156.convert_doc([_load("dv-result-v2.json"), side], inp)  # mig1 꼴: 결과 + 요약
    assert len(got) == 1 and got[0]["skippedSide"] == 1
    for bad in ({}, {"schemaVersion": "dataverify.result@1"}, [], [side], [_load("dv-result-v2.json"), {"schemaVersion": "x"}]):
        with pytest.raises(convert_156.UnknownShape):
            convert_156.convert_doc(bad, inp)


def test_t11_adapter_slot(inp):
    with pytest.raises(adapt_v2.NotReady):
        adapt_v2.to_units({})
    c = convert_156.convert(_load("dv-result-v2.json"), inp)
    assert {"reviewStatus", "units", "unresolved"} <= set(c)
    assert [u.key for u in convert_156.from_v1(_load("dv-result-v2.json"), input=inp)] == [u.key for u in c["units"]]


def test_t15_fixtures_pass_fixed_schema():
    p = esg_app_root() / "apps/api-py/esgapi/dataverify/tests/data/flow-shapes/dataverify.result@2.schema.json"
    if not p.exists():
        pytest.fail(f"고정 스키마 없음: {p} (ESG_APP_ROOT — 조용한 초록 금지)")
    s = json.loads(p.read_text(encoding="utf-8"))
    for name in ("dv-result-v2.json", "dv-result-v2-failed.json"):
        jsonschema.validate(_load(name), s)
