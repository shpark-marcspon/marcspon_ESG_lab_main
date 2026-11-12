"""TRX4 tx4_pick — N- · K1 창 · K4 · 자리 상한 · best4α. 지어낸 자료."""

import json

from trlab import order_win as W
from trlab import tx4_pick as X
from trlab.common import Block


def test_blind_removes_numbers():
    items = [{"text": "A", "printed_page": 5, "level": 1}, {"text": "B", "printed_page": 9, "level": 2}]
    b = X.blind(items)
    assert all(it["printed_page"] is None for it in b) and not W.is_numbered(b)
    assert items[0]["printed_page"] == 5  # 복사본
    assert X.name_of("cos", "en", True) == "cos-en-N" and X.name_of("kb", "ko", True) == "kb-ko"
    assert X.parse_name("cos-en-N") == ("cos", "en", True) and X.parse_name("kb-ko") == ("kb", "ko", False)


def test_k1_window_next_dp_and_default_unchanged():
    pm = {"map": {}, "offset": 0, "npages": 80, "cover": {"direct": 0}}
    items = [{"text": t, "level": 2, "printed_page": None} for t in "ABCD"]
    dp = [3, 10, 50, 60]
    wins, bounds = W.windows3(items, pm, dp, 80, anchor_cut=False)  # K1a
    assert bounds == [(1, 10), (4, 50), (11, 60), (51, 80)]  # hi = 다음 DP(포함), 끝 항목은 책 끝 · 닻 자르기 없음
    _, b2 = W.windows3(items, pm, dp, 80, anchor_cut=False, hi_dp=False)  # K1b
    assert b2[1] == (4, 80)
    _, b3 = W.windows3(items, pm, dp, 80)  # 기본 = TRX3(닻 [DP−2, DP+9])
    assert b3[1] == (8, 19)
    # 다음 DP 가 앞서면(차례 어긋남) 책 끝
    _, b4 = W.windows3(items, pm, [3, 40, 20, 60], 80, anchor_cut=False)
    assert b4[1] == (4, 80) and b4[2][0] <= 20 <= b4[2][1]  # DP 쪽은 늘 창 안


def test_k4_only_beyond_3m():
    items = [{"text": t} for t in "ABCDE"]
    dp = [2, 4, 6, 8, 40]
    cand = {"rows": {"p9-b1": {"page": 9}, "p20-b1": {"page": 20}, "p40-b1": {"page": 40}}}
    b, m = X.k4_bonus(items, dp, [1, 2, 3], cand, 50)
    assert m == 2.0
    assert b[4] == {"p20-b1": -X.K4_PEN, "p40-b1": -X.K4_PEN}  # 8 에서 6 넘게
    assert "p9-b1" not in b[4] and b[0] == {}


def test_reserve_over_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(X, "OUT4", tmp_path)
    (tmp_path / "calls.jsonl").write_text(json.dumps({"tag": "TRX4/d4-K1/kb-ko", "costUsd": 0.0995}) + "\n")
    assert not X.reserve("luna", 0.001)  # 0.0995 + 0.001 > 0.1
    assert X.reserve("sol", 0.3)
    assert not X.reserve("sol", 0.15)  # 잡아 둔 0.3 + 0.15 > 0.4 (합도 넘음)
    X._RESERVED.update(luna=0.0, sol=0.0)


def blk(id, page, text, size=20.0):
    return Block(id, "heading", page, 1, text, (40, 40, 200, 60), size, "body", 600.0, 800.0, "", [])


def test_best4_alpha_only_diff_items():
    items = [{"text": "Alpha", "level": 2, "printed_page": None}, {"text": "Beta", "level": 2, "printed_page": None},
             {"text": "Gamma", "level": 2, "printed_page": None}]
    bs = [blk("p3-b1", 3, "Alpha"), blk("p8-b1", 8, "Beta"), blk("p6-b1", 6, "Beta"), blk("p12-b1", 12, "Gamma")]
    ids = {b.id: b for b in bs}
    cand = {"rows": {b.id: {"page": b.page, "size": b.font_size} for b in bs}}
    base = [("p3-b1", 3, "ai"), ("p8-b1", 8, "ai"), ("p12-b1", 12, "ai")]
    k5 = {0: {"page": 3}, 1: {"page": 6}, 2: {"page": 12}}
    alpha, diff, bad = X.alpha_merge(base, k5, items, cand, ids)
    assert diff == [1] and bad == []
    assert alpha[0] == base[0] and alpha[2] == base[2] and alpha[1] == ("p6-b1", 6, "k5")
    # K5 쪽이 차례를 거스르면 base 로 되돌림
    alpha, diff, bad = X.alpha_merge(base, {1: {"page": 2}}, items, cand, ids)
    assert diff == [1] and bad == [1] and alpha[1] == base[1]
