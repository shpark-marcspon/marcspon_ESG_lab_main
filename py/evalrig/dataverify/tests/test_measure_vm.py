"""MB2 C3 — 흔들림·견줌 셈 손셈(발주서 MB2 §2-8). 지은 세 실행(fixtures/vm-runs.json)."""
from __future__ import annotations

import json
from pathlib import Path

from evalrig.common.units import Origin, Unit
from evalrig.dataverify import convert_156, measure_vm
from evalrig.dataverify.tests.conftest import DATA_DIR

FIX = Path(__file__).resolve().parent / "fixtures"
O = Origin(engine="v1", schema="dataverify.result@2", run="t", converter="t@1")


def _conv(run):
    units = [Unit(module="dataverify", unit=convert_156.CHECK_UNIT, key=f"c{n}", group=p["year"], origin=O,
                  payload={**p, "seq": n, "tool": "t"}) for n, p in enumerate(run["units"])]
    units += [Unit(module="dataverify", unit=convert_156.INSIGHT_UNIT, key=f"i{n}", group="환경", origin=O,
                   payload={"indicatorText": i, "id": i, "year": {"status": "absent"}, "verdict": "flag", "area": "환경"})
              for n, i in enumerate(run["insight"])]
    return {"reviewStatus": "done", "units": units}


def _runs():
    raw = json.loads((FIX / "vm-runs.json").read_text(encoding="utf-8"))["runs"]
    return [{"conv": _conv(r), "result": {"plan": {"formulas": ["f"] * r["plan"]}, "review": {"checks": [{}] * r["checks"]}}}
            for r in raw]


def _gold():
    return json.loads((DATA_DIR / "synthetic-company.gold.json").read_text(encoding="utf-8"))


def test_jitter_hand_counts():
    j = measure_vm.jitter(_runs(), _gold())
    assert (j["keys"], j["keysByRuns"], j["keysInTwoOrMore"]) == (5, {"3": 1, "2": 2, "1": 2}, 3)
    assert j["verdictDisagree"] == 2  # 에너지 합 2022(FAIL/OK) · 비율 2024(OK/FAIL)
    # E-003 2023 은 셋 다 · E-005 2022(실행 1·3) · E-012 2023(실행 2) · E-007 2024(실행 3) 은 일부
    assert j["goldFailHit"] == {"all": 1, "some": 3, "none": 7, "den": 11}
    assert [r["planFormulas"] for r in j["perRun"]] == [3, 2, 4]
    assert j["insightJaccard"] == {"pairs": [0.5, 0.0, 0.0], "mean": 0.1667}


def test_partial_key_keeps_unresolved_count():
    p = {"rule": "sum", "items": ["E-004", "E-005"], "year": "2022"}
    assert measure_vm.unit_key({**p, "unresolved": []}) != measure_vm.unit_key({**p, "unresolved": [["x"]]})


def test_arms_false_fail_and_intensity_conclusion():
    runs = _runs()
    arms = {"v1": {"conv": runs[1]["conv"], "cond": {"n": 1}, "side": None, "v1RatioOnIntensity": 2},
            "labfix": {"conv": runs[2]["conv"], "cond": {"n": 1}, "side": [{"mode": "pct", "appliedTolerance": 0.05}]}}
    t = measure_vm.arms_table(arms, _gold(), {"E-007"})
    # 실행 2: 에너지 합 2022 OK, 폐기물 합 2023 FAIL 은 정답 FAIL(E-012) 품음 → 거짓 FAIL 0
    assert t["v1"]["falseFailUnits"] == 0 and t["v1"]["intensityRatioToolCalls"] == 2
    # 실행 3: 비율 2024 FAIL 은 E-007 2024 정답 FAIL 품음, 부분 단위는 셈 밖 → 0
    assert t["labfix"]["falseFailUnits"] == 0 and t["labfix"]["intensityRelation"] == {"FAIL": 1}
    assert t["labfix"]["intensityConclusion"].startswith("판정 불가")
    fake = {"conv": _conv({"units": [{"rule": "sum", "items": ["E-010", "E-011", "E-012"], "unresolved": [], "year": "2022",
                                      "verdict": "FAIL"}], "insight": []}), "cond": {}, "side": [{"mode": "intensity", "appliedTolerance": 0.005}]}
    t2 = measure_vm.arms_table({"x": fake}, _gold(), set())
    assert t2["x"]["falseFailUnits"] == 1 and t2["x"]["intensityConclusion"] == "판정 가능"
