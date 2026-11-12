"""MB2 C2 — 엑셀↔PDF 랩 대사 채점(발주서 MB2 §2-4). 정답·cell_map·verdicts 는 지은 값."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from evalrig.dataverify import recon

FIX = Path(__file__).resolve().parent / "fixtures"


def _it(ind, year, verdict, kind=None, cell=None, accept=()):
    return {"indicator": ind, "year": year, "verdict": verdict, "kind": kind, "accept": list(accept), "cell": cell}


GOLD = {"items": [
    _it("X-001", "2023", "match", cell="p9-t1:r1c1"),                  # paired_right match
    _it("X-001", "2024", "match", cell="p9-t1:r1c2"),                  # paired_elsewhere(b 다름)
    _it("X-002", "2023", "mismatch", "digit", "p9-t1:r2c1"),           # 잡음(mismatch)
    _it("X-002", "2024", "mismatch", "unit_scale", "p9-t1:r2c2"),      # 거짓 일치
    _it("X-003", "2023", "mismatch", "year_swap", "p9-t1:r3c1"),       # 잡음(needs_explanation)
    _it("X-003", "2024", "match", cell="p9-t1:r3c2"),                  # 거짓 경보 needs_explanation
    _it("X-004", "2023", "match", cell="p9-t1:r4c1"),                  # 거짓 경보 mismatch
    _it("X-004", "2024", "match", "rounding", "p9-t1:r4c2", ["mismatch"]),  # 대조군 match
    _it("X-005", "2023", "match", cell="p9-t1:r5c1"),                  # unpaired
    _it("X-006", "2023", "source_only", "source_only"),                # paired_false
    _it("X-006", "2024", "source_only", "source_only"),                # 맞음
    _it("X-007", "2023", "report_only", "report_only", "p9-t1:r8c1"),  # paired_false
    _it("X-007", "2024", "report_only", "report_only", "p9-t1:r8c2"),  # 맞음
]}
CELLS = {"p1-t1:r1c8": ["X-001", "2023"], "p1-t1:r1c9": ["X-001", "2024"], "p1-t1:r2c8": ["X-002", "2023"],
         "p1-t1:r2c9": ["X-002", "2024"], "p1-t1:r3c8": ["X-003", "2023"], "p1-t1:r3c9": ["X-003", "2024"],
         "p1-t1:r4c8": ["X-004", "2023"], "p1-t1:r4c9": ["X-004", "2024"], "p1-t1:r5c8": ["X-005", "2023"],
         "p1-t1:r6c8": ["X-006", "2023"], "p1-t1:r6c9": ["X-006", "2024"]}


def _v():
    return recon.verdicts_of(json.loads((FIX / "recon-outcome.json").read_text(encoding="utf-8")), "d2")


def _r(x):
    return (x["num"], x["den"])


def test_branches():
    rows = {(r["indicator"], r["year"]): r for r in recon.branches(GOLD, CELLS, _v())}
    assert rows[("X-001", "2023")]["branch"] == "paired_right" and rows[("X-001", "2023")]["outcome"] == "match"
    assert rows[("X-001", "2024")]["branch"] == "paired_elsewhere" and rows[("X-001", "2024")]["b"] == ["p9-t1:r9c9"]
    assert rows[("X-005", "2023")]["branch"] == "unpaired"
    assert rows[("X-006", "2023")]["branch"] == "paired_false" and rows[("X-006", "2023")]["b"] == ["p9-t1:r7c7"]
    assert rows[("X-006", "2024")]["branch"] == "unpaired"
    assert rows[("X-007", "2023")]["branch"] == "paired_false" and rows[("X-007", "2023")]["a"] == ["p1-t1:r99c8"]
    assert rows[("X-007", "2024")]["branch"] == "unpaired"


def test_score_hand_counts():
    s = recon.score(GOLD, CELLS, _v())
    assert _r(s["pairRecall"]) == (7, 9)
    assert _r(s["plantedCaught"]) == (2, 3)  # digit(mismatch) · year_swap(needs_explanation)
    assert _r(s["falseMatch"]) == (1, 3)     # unit_scale 를 match
    assert _r(s["falseAlarm"]["mismatch"]) == (1, 5) and _r(s["falseAlarm"]["needsExplanation"]) == (1, 5)
    assert s["falseAlarm"]["byReason"] == {"unit_one_side": 1, "value": 1}
    assert s["rounding"] == ["match"]
    assert _r(s["sourceOnlyCorrect"]) == (1, 2) and _r(s["reportOnlyCorrect"]) == (1, 2)
    assert s["pairedElsewhere"] == 1
    assert s["branches"] == {"paired_elsewhere": 1, "paired_false": 2, "paired_right": 7, "unpaired": 3}
    assert (s["verdicts"], s["verdictsOnSourceCells"]) == (10, 9)


def test_stage_r_reads_after():
    v = _v()
    assert recon.verdicts_of({"after": {"verdicts": v}}, "r") == v
    with pytest.raises(recon.ReconError):
        recon.verdicts_of({"outcome": {"verdicts": v}}, "x")


def test_id_shape_mismatch_stops():
    bad = [{**v, "a": "a:" + v["a"]} for v in _v()]
    with pytest.raises(recon.ReconError):
        recon.score(GOLD, CELLS, bad)
    empty = recon.score(GOLD, CELLS, [])  # 빈 verdicts(짝 0)는 멈추지 않고 전부 unpaired 로 센다
    assert empty["branches"] == {"unpaired": 13} and _r(empty["pairRecall"]) == (0, 9)
