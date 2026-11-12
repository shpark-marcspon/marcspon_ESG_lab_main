"""T12 — 그린 채점 손셈 · 같은 입력 대조 · 흔들림 · 새는 것 검사(지은 자료)."""
from __future__ import annotations

import copy
import json

import pytest

from evalrig.common import gold as common_gold
from evalrig.common.units import Origin, Unit
from evalrig.greenwash import cli, gold, sameinput, score
from evalrig.greenwash import units as U

LONG_A = "가상상사는 협력사와 함께 포장재를 줄이는 지은 문장을 길게 이어 씁니다"
LONG_B = "가상상사는 2030년까지 모든 물류 차량을 친환경 차량으로 바꾸는 지은 계획을 세웠습니다"
GOLD = [  # (쪽, 문장, 등급, accept)
    (1, "가상 문장 영", "High", []), (1, "가상 문장 하나", "Mid", []), (1, "가상 문장 둘", "Mid", []),
    (2, "가상 문장 셋", "High", []), (2, "가상 문장 넷", "Low", []), (2, LONG_A, "Mid", []),
    (3, LONG_B, "High", []), (3, "가상 문장 일곱", "NonRisk", []), (3, "가상 문장 여덟", "Mid", []),
    (4, "가상 문장 아홉", "High", []),
]
PRED = [  # (쪽, 문장, 등급) — exact 5 · contain 1 · fuzzy 1 · 거짓 2
    (1, "가상 문장 영", "High"), (1, "가상 문장 하나", "High"), (1, "가상 문장 둘", "Mid"),
    (2, "가상 문장 셋", "High"), (2, "가상 문장 넷", "Low"),
    (2, LONG_A[:-3], "Mid"),  # 품음
    (3, LONG_B.replace("모든", "모두"), "Mid"),  # 한 글자 다름(비 ≥ 0.90)
    (1, "전혀 다른 지은 문장입니다", "High"),
    (4, "가상 문장 일곱", "Mid"),  # 정답 쪽 3 의 글 — 쪽이 달라 짝 아님
]


def _gold():
    items = [{"page": p, "sentence": U.norm(s), "sha12": U.sha12(s), "label": lv, "level": lv,
              "category": {"status": "absent"}, "accept": acc, "occurrences": 1} for p, s, lv, acc in GOLD]
    return common_gold.new(set_id="t12", module="greenwash", unit=U.UNIT, items=items, coverage="exhaustive", synthetic=True,
                           provenance={"kind": "runner_made", "by": "test", "from": [], "at": "2000-01-01"},
                           review={"status": "none", "by": None, "at": None, "note": None, "sample": None})


def _conv(rows=PRED, not_checked=(), sha="a" * 64, pages=(1, 2, 3, 4), config=None):
    o = Origin(engine="v1", schema="greenwash.result@1", run="t", converter="t@1")
    units = [Unit(module="greenwash", unit=U.UNIT, key=f"p{p}:{U.sha12(s)}", group=f"p{p}", origin=o,
                  payload={"page": p, "sentence": U.norm(s), "sha12": U.sha12(s), "level": lv, "category": {"status": "absent"}})
             for p, s, lv in rows]
    return {"source": {"sha256": sha, "pageCount": 4}, "pages": list(pages), "config": config or {"c": 1},
            "units": units, "folded": 0, "notChecked": list(not_checked)}


def _r(x):
    return (x["num"], x["den"])


def test_t12_hand_counts():
    s = score.score(_gold(), _conv())
    assert s["how"] == {"contain": 1, "exact": 5, "fuzzy": 1}
    assert (s["pairs"], s["missed"], s["extra"]) == (7, 3, 2)
    assert _r(s["risk"]["recall"]) == (6, 8)  # 위험 정답 8 중 위험 결과와 짝 6
    assert _r(s["risk"]["precision"]) == (6, 8)  # 위험 결과 8 중 위험 정답과 짝 6
    assert _r(s["levelAgreement"]) == (5, 7)  # 짝 7 중 등급 같음 5(쪽1 하나·쪽3 fuzzy 다름)
    assert _r(s["low"]["recall"]) == (1, 1) and _r(s["low"]["precision"]) == (1, 1)
    assert s["category"]["status"] == "unmeasurable"


def test_t12_not_checked_pages_leave_denominator():
    s = score.score(_gold(), _conv(not_checked=[4]))
    assert s["goldDroppedNotChecked"] == 1
    assert _r(s["risk"]["recall"]) == (6, 7)


def test_t12_pages_outside_result_leave_denominator():
    s = score.score(_gold(), _conv(pages=(1, 2, 3)))
    assert (s["goldOutsidePages"], s["goldDroppedNotChecked"]) == (1, 0)
    assert _r(s["risk"]["recall"]) == (6, 7)


def test_t12_accept_counts_as_agreement():
    g = _gold()
    g["items"][1]["accept"] = ["High"]  # 쪽1 「하나」: Mid 정답, High 도 받음
    assert _r(score.score(g, _conv())["levelAgreement"]) == (6, 7)


def test_t12_partial_gold_refuses_precision(fixture):
    f = fixture("sg1-mini.json")
    gs = gold.from_sg1(f["queries"], f["claimExpected"], f["document"])
    rows = [(12, "가상상사는 모든 사업장의 전력을 100% 재생에너지로 바꿨다.", "High"),
            (7, "가상상사는 2030년 감축 목표를 세웠다.", "Low"), (7, "가상상사의 포장재는 대부분 재활용된다.", "Mid")]
    s = score.score(gs, _conv(rows, sha=f["document"]["source"]["sha256"], pages=(7, 12)))
    assert _r(s["suspectRecall"]) == (1, 1) and _r(s["supportedFlagged"]) == (0, 1)
    assert s["partialCounts"] == {"n": 1, "flagged": 1}
    assert s["precision"]["status"] == "unmeasurable"
    with pytest.raises(ValueError, match="정답 문서"):
        score.score(gs, _conv(rows, sha="b" * 64, pages=(7, 12)))


def test_same_and_input_mismatch():
    a = _conv()
    b = _conv(PRED[:6] + [(3, LONG_B.replace("모든", "모두"), "High")])
    s = sameinput.same(a, b, [_gold()])
    assert s["pairs"] == 7 and (s["onlyA"], s["onlyB"]) == (2, 0)
    assert _r(s["levelDiff"]) == (1, 7) and s["moves"] == {"Mid->High": 1}
    assert _r(s["onlyRatio"]) == (2, 9)
    assert len(s["scores"]) == 1 and len(s["scores"][0]) == 2
    for kw in ({"sha": "b" * 64}, {"pages": (1, 2)}, {"config": {"c": 2}}):
        with pytest.raises(sameinput.InputMismatch):
            sameinput.same(a, _conv(**kw))


def test_jitter_mean_max():
    a, b, c = _conv(), _conv(PRED[:7]), _conv([(p, s, "Low") for p, s, _ in PRED])
    j = sameinput.jitter([a, b, c])
    assert j["pairsCompared"] == 3
    # a-b: 짝 7·다름 0·한쪽 2/9 · a-c: 짝 9·다름 8(Low 하나 빼고) · b-c: 짝 7·다름 6
    assert [(_r(r["levelDiff"]), _r(r["onlyRatio"])) for r in j["rows"]] == [((0, 7), (2, 9)), ((8, 9), (0, 9)), ((6, 7), (2, 9))]
    assert j["levelDiff"]["max"] == round(8 / 9, 4)
    with pytest.raises(ValueError):
        sameinput.jitter([a])


def test_leak_check(tmp_path, capsys):
    q = tmp_path / "queries.json"
    q.write_text(json.dumps([{"id": "G01", "type": "greenwash", "text": "지은 주장 문장\n하나"},
                             {"id": "D01", "type": "data", "text": "지은 질의"}], ensure_ascii=False), encoding="utf-8")
    d = tmp_path / "out"
    (d / "gold").mkdir(parents=True)
    (d / "gold" / "g.json").write_text("지은 주장 문장 하나", encoding="utf-8")  # 정답 자체는 건너뜀
    (d / "ok.md").write_text("수만 있음 12", encoding="utf-8")
    assert cli.main(["leak-check", str(d), "--against", str(q)]) == 0
    (d / "bad.md").write_text("앞 글 지은  주장 문장 하나 뒤 글", encoding="utf-8")
    assert cli.main(["leak-check", str(d), "--against", str(q)]) == 1
    assert '"skipped": 1' in capsys.readouterr().out
