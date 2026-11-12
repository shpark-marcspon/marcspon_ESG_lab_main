"""H2 차례 창 — 지어낸 항목·후보."""

from trlab import order_win as W
from trlab.common import Block


def blk(id, page, text, size=20.0, order=1):
    return Block(id, "heading", page, order, text, (50, 50, 300, 80), size, "body", 600.0, 800.0, "", [])


def setup():
    bs = [blk("p3-b1", 3, "Alpha"), blk("p5-b1", 5, "Beta"), blk("p5-b2", 5, "Alpha", order=2), blk("p9-b1", 9, "Gamma"),
          blk("p12-b1", 12, "Beta"), blk("p2-b1", 2, "Alpha"), blk("p7-b1", 7, "Chapter")]
    cand = {"rows": {b.id: {"page": b.page, "size": b.font_size, "text": b.text} for b in bs},
            "pages": {str(p): {"divider": p == 7} for p in range(1, 21)}}
    return bs, cand


def test_dp_keeps_order():
    bs, cand = setup()
    items = [{"text": "Alpha", "level": 2, "printed_page": None}, {"text": "Beta", "level": 2, "printed_page": None},
             {"text": "Gamma", "level": 2, "printed_page": None}]
    wins = [(None, list(range(1, 21)))] * 3
    res = W.dp(items, cand, bs, wins, set())
    pages = [r[0] for r in res]
    assert pages == sorted(pages) and pages[2] == 9 and pages[1] == 5  # Beta 12 는 Gamma 9 뒤라 버림
    assert W.violations(pages, items) == []


def test_same_page_only_after_level1():
    a = {"text": "Chapter", "level": 1, "printed_page": None}
    b = {"text": "Beta", "level": 2, "printed_page": None}
    c = {"text": "Alpha", "level": 2, "printed_page": None}
    assert W.order_ok(a, 5, b, 5, numbered=False)
    assert not W.order_ok(b, 5, c, 5, numbered=False)
    assert W.violations([5, 5, 5], [a, b, c]) == [2]
    assert W.violations([5, 4], [b, c]) == [1]
    # 번호 있는 목차: 번호 없는 머리 뒤 같은 쪽 · 찍힌 번호가 같은 두 항목은 허용
    h = {"text": "Group", "level": 2, "printed_page": None}
    n1 = {"text": "One", "level": 2, "printed_page": 5}
    n2 = {"text": "Two", "level": 2, "printed_page": 5}
    assert W.order_ok(h, 5, n1, 5, numbered=True) and W.order_ok(n1, 5, n2, 5)
    assert not W.order_ok(h, 5, n1, 5, numbered=False)


def test_level1_divider_bonus_and_window_anchor():
    bs, cand = setup()
    items = [{"text": "Chapter", "level": 1, "printed_page": None}]
    res = W.dp(items, cand, bs, [(None, list(range(1, 21)))], {7})
    assert res[0][0] == 7
    pm = {"map": {"30": [30, "direct"]}, "offset": 0, "npages": 40, "cover": {"direct": 1}}
    items = [{"text": "A", "level": 2, "printed_page": None}, {"text": "B", "level": 2, "printed_page": None},
             {"text": "C", "level": 2, "printed_page": 30}]
    wins, bounds = W.windows3(items, pm, [2, 15, 30], 40)
    assert wins[2] == (30, [30])  # 직접 → 한 쪽
    assert bounds[1] == (13, 24)  # 3~30 은 13쪽 넘음 → DP 15 를 닻으로 [13, 24]
    assert wins[0][1][0] == 1


def test_violations_found():
    items = [{"text": t, "level": 2, "printed_page": None} for t in "ABCD"]
    assert W.violations([3, 8, 6, 9], items) == [2]
