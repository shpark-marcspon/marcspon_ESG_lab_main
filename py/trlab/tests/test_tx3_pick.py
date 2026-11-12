"""H0 셈 · 투표 · 자리 상한."""

import json

from trlab import tx3_pick as T


def gold():
    return {"items": [{"n": 0, "start_page": 4, "end_page": 4, "divider": True},
                      {"n": 1, "start_page": 5, "end_page": 9, "divider": False},
                      {"n": 2, "start_page": 10, "end_page": 11, "divider": False},
                      {"n": 3, "start_page": 12, "end_page": 20, "divider": True}]}


def row(n, s, e, block=False):
    return {"n": n, "found": True, "block_ok": block, "pred": {"start_page": s, "end_page": e}}


def test_score_ab_divider_plus_one():
    rows = [row(0, 5, 5), row(1, 6, 9), row(2, 10, 12), row(3, 13, 20, True)]
    s = T.score_ab(rows, gold())
    r = {x["n"]: x for x in s["rows"]}
    assert r[0]["start_ab"] and r[3]["start_ab"]  # 간지 +1 맞음
    assert not r[1]["start_ab"]  # 간지 아닌 +1 틀림
    assert r[2]["end_ab"]  # 끝 11 + 1 = 12 가 간지 쪽 → 맞음
    assert not r[0]["end_ab"]  # 끝 4 + 1 = 5 는 간지 아님
    assert s["start_ab"] == {"hit": 3, "n": 4, "pct": 75.0} and s["divider_start_ab"]["pct"] == 100.0
    assert s["block"]["hit"] == 1 and s["wrong_start"] == [1]


def test_vote_majority_undecided_tie():
    order = {"a": 1, "b": 2, "c": 3}.get
    assert T.vote([("a", 3), ("b", 3), ("c", 4)], order) == ("a", 3, "vote")  # 쪽 3 다수, 블록 동률 → order 작은 것
    assert T.vote([("b", 3), ("b", 3), ("a", 3)], order) == ("b", 3, "vote")
    assert T.vote([("a", 3), ("b", 4), ("c", 5)], order) == (None, None, "undecided")
    assert T.vote([(None, None), (None, None), ("a", 3)], order)[2] == "undecided"
    assert T.shaky([[("a", 3)], [("a", 3)], [("b", 3)]]) == {"page": 0, "block": 1, "n": 1}


def test_cap_blocks_send(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "OUT3", tmp_path)
    monkeypatch.setattr(T, "_RESERVED", {"G1": 0.0, "luna": 0.0, "sol": 0.0})
    (tmp_path / "calls.jsonl").write_text(json.dumps({"tag": "TRX3/d3/x-ko/1", "costUsd": 0.59}) + "\n")
    assert not T.reserve("luna", 0.02)  # 0.59 + 0.02 > 0.6
    assert T.reserve("luna", 0.005)
    assert not T.reserve("luna", 0.006)  # 잡아 둔 0.005 까지 셈
    assert T.reserve("sol", 0.5)
    (tmp_path / "calls.jsonl").write_text(json.dumps({"tag": "TRX3/d3/x-ko/1", "costUsd": 0.5999999}) + "\n")
    sent = []
    monkeypatch.setattr(T, "send", lambda *a, **k: sent.append(a))
    got, info = T.ask("luna", T.LUNA, "TRX3/d3/x-ko/2", [{"text": "A", "level": 2, "printed_page": 1}], [(1, [1])],
                      {"rows": {"p1-b1": {"page": 1, "pos": "상", "size": 20, "x": 2, "bold": 0, "color": False, "rep": False,
                                          "text": "A"}}, "pages": {"1": {"divider": False}}})
    assert got is None and info["status"] == "over_cap" and sent == []


def test_fixed_items_count():
    # 방법별 고친 항목: 앞 판에서 틀린 항목이 뒤 판에서 맞은 수
    before = {"rows": [{"n": 0, "start_ab": False}, {"n": 1, "start_ab": True}, {"n": 2, "start_ab": False}]}
    after = {"rows": [{"n": 0, "start_ab": True}, {"n": 1, "start_ab": False}, {"n": 2, "start_ab": False}]}
    assert T.fixed(before, after) == {"fixed": [0], "broke": [1]}
