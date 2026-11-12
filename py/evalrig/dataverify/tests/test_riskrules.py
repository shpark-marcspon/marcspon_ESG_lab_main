"""MB2 C3 — 리스크 규칙 후보·B0 손셈·답 확인(발주서 MB2 §2-7). 입력은 git 의 지은 리스크 시험지."""
from __future__ import annotations

import json

from evalrig.dataverify import riskrules as R
from evalrig.dataverify import score
from evalrig.dataverify.tests.conftest import DATA_DIR


def _j(name):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def _r(x):
    return (x["num"], x["den"])


def test_candidates_theta10():
    c = R.candidates(_j("synthetic-risk.input.json"))
    assert sorted(x["cid"] for x in c) == sorted([
        "E-101:jump:2024", "E-102:jump:2024", "E-103:jump:2023", "E-104:jump:2023", "E-105:jump:2023", "E-105:jump:2024",
        "E-106:jump:2023", "E-106:jump:2024", "S-101:blank:2023", "S-102:blank:2023", "G-101:blank:2024", "G-101:jump:2023",
        "G-102:blank:2024", "G-102:jump:2023"])
    assert {x["id"] for x in c if x["note"]} == {"E-102", "E-104", "E-106", "S-102", "G-102"}


def test_b0_and_sensitivity():
    inp, gs = _j("synthetic-risk.input.json"), _j("synthetic-risk.gold.json")
    f = {t: R.b0_flags(R.candidates(inp, t)) for t in R.SENSITIVITY}
    assert f[0.10] == {"E-101", "E-103", "E-105", "S-101", "G-101"}
    assert f[0.20] == f[0.30] == {"E-101", "E-103", "S-101", "G-101"}  # D1(E-105, 12%)는 θ20·30 에서 놓침
    s = score.score(gs, R.as_conv(f[0.10]))
    assert (_r(s["flagNoNoteRecall"]), _r(s["explainedFlagged"]), _r(s["noneFlagged"]), _r(s["onlyNoNotePairs"])) == \
        ((5, 5), (0, 5), (0, 3), (5, 5))


def test_mixed_trend():
    inp = {"data": {"환경": [{"ID": "Z-1", "테마": "환경", "values": {"2022": 100, "2023": 120, "2024": 96}, "메모": ""}]}}
    rules = sorted(x["rule"] for x in R.candidates(inp))
    assert rules == ["jump", "jump", "mixed_trend"]


def test_check_answer_and_request():
    c = R.candidates(_j("synthetic-risk.input.json"))
    sys_, user = R.render_request(c, _j("synthetic-risk.input.json")["company"])
    assert all(x["cid"] in user for x in c) and "10%" in user and "flag" in sys_
    ans = {"items": [{"cid": "E-101:jump:2024", "verdict": "flag"}, {"cid": "E-102:jump:2024", "verdict": "explained"},
                     {"cid": "NOPE", "verdict": "flag"}, {"cid": "E-103:jump:2023", "verdict": "flag"},
                     {"cid": "E-103:jump:2023", "verdict": "not_risk"}, {"cid": "E-105:jump:2023", "verdict": "maybe"}]}
    got = R.check_answer(ans, c)
    assert got["dropped"] == {"unknown_cid": 1, "duplicate_cid": 1, "bad_verdict": 1}
    assert got["flagged"] == ["E-101"] and got["verdicts"]["E-103:jump:2023"] == R.UNANSWERED
    assert got["unanswered"] == len(c) - 2
