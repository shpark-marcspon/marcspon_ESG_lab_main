"""T13 — 데이터 채점 손셈(가상 기업 정답 + 지은 result@2), 리스크 짝, not_applicable 갈래."""
from __future__ import annotations

import json
from pathlib import Path

from evalrig.common.units import Origin, Unit
from evalrig.dataverify import convert_156, score
from evalrig.dataverify.tests.conftest import DATA_DIR

FIX = Path(__file__).resolve().parent / "fixtures"


def _j(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _r(x):
    return (x["num"], x["den"])


def test_t13_synthetic_company_hand_counts():
    gs = _j(DATA_DIR / "synthetic-company.gold.json")
    conv = convert_156.convert(_j(FIX / "dv-result-v2.json"), _j(DATA_DIR / "synthetic-company.input.json"))
    s = score.score(gs, conv)
    # FAIL 정답 11 중 맞힘 3: E-003 2023(c1) · E-005 2022(c2·c5) · E-007 2024(c2)
    assert _r(s["recall"]) == (3, 11)
    # 완전 해석 FAIL 단위 3(c1:2023·c2:2022·c2:2024) 모두 정답 FAIL 을 품음. 부분 미해석 c5:2022·c5:2024 는 분모 밖(623)
    assert _r(s["precision"]) == (3, 3)
    assert (s["failUnitsPartiallyUnresolved"], s["partialWithGoldFail"]) == (2, 1)  # 정답 FAIL 을 품은 부분 단위 = c5:2022
    assert {k: _r(v) for k, v in s["byKind"].items()} == {
        "denominator_basis": (0, 2), "intensity": (0, 2), "ratio": (1, 2), "sum": (1, 3), "unit_slip": (1, 2)}
    assert (s["failUnits"], s["failUnitsUnresolved"]) == (6, 1)


def _chk(key, items, unresolved, year):
    o = Origin(engine="v1", schema="dataverify.result@2", run="t", converter="t@1")
    return Unit(module="dataverify", unit=convert_156.CHECK_UNIT, key=key, group=year, origin=o,
                payload={"items": items, "unresolved": unresolved, "year": year, "verdict": "FAIL",
                         "rule": "sum", "seq": 1, "tool": "sum_check_by_name"})


def test_t13b_partial_out_of_precision():
    gs = _j(DATA_DIR / "synthetic-company.gold.json")
    q = ["환경", "지은", "없는 항목", "t"]
    # 같은 정답 FAIL(E-003 2023)을 완전 단위·부분 단위가 각각 품음 / 부분 단위 하나는 거짓(E-001 2022 는 OK)
    conv = {"reviewStatus": "done", "units": [
        _chk("full", ["E-003"], [], "2023"),
        _chk("part-hit", ["E-003"], [q], "2023"),
        _chk("part-false", ["E-001"], [q], "2022")]}
    s = score.score(gs, conv)
    assert _r(s["precision"]) == (1, 1)  # 부분 단위가 분모에 들면 (2, 3)
    assert _r(s["recall"]) == (1, 11)  # 재현은 부분 단위까지 — 같은 칸이라 1
    assert (s["failUnitsPartiallyUnresolved"], s["partialWithGoldFail"], s["failUnitsUnresolved"]) == (2, 1, 0)


def test_t13_xpdf_not_applicable():
    s = score.score(_j(DATA_DIR / "kb-2025-xpdf.gold.json"), {"units": [], "reviewStatus": "done"})
    assert s["status"] == "not_applicable" and "대사" in s["reason"]


def _ins(ids):
    o = Origin(engine="v1", schema="dataverify.result@2", run="t", converter="t@1")
    return {"reviewStatus": "done", "units": [
        Unit(module="dataverify", unit=convert_156.INSIGHT_UNIT, key=f"i{n}", group="환경", origin=o,
             payload={"indicatorText": "x", "id": i, "year": {"status": "absent"}, "verdict": "flag", "area": "환경"})
        for n, i in enumerate(ids)]}


def test_t13_risk_pairs():
    gs = _j(DATA_DIR / "synthetic-risk.gold.json")
    # 낸 것: J1 둘 다(설명 있는 쪽은 거짓 양성) · J2 설명 없는 쪽만 · 변동 없음 하나
    s = score.score(gs, _ins(["E-101", "E-102", "E-103", "E-107", None]))
    assert _r(s["flagNoNoteRecall"]) == (2, 5)
    assert _r(s["explainedFlagged"]) == (1, 5)
    assert _r(s["noneFlagged"]) == (1, 3)
    assert _r(s["onlyNoNotePairs"]) == (1, 5)  # J2 만


def test_t13c_partial_unit_counts_for_recall():
    """C1 검증 권고 R1 — 부분 미해석 단위만 맞힌 정답 FAIL 도 재현에 든다(정밀 분모 밖이어도)."""
    gs = _j(DATA_DIR / "synthetic-company.gold.json")
    conv = {"reviewStatus": "done", "units": [_chk("part-only", ["E-003"], [["환경", "지은", "없는 항목", "t"]], "2023")]}
    s = score.score(gs, conv)
    assert _r(s["recall"]) == (1, 11)  # 재현을 완전 해석 단위에서만 세면 (0, 11)
    assert s["precision"]["status"] == "no_denominator" and s["partialWithGoldFail"] == 1
