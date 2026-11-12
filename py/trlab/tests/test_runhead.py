"""K3 머리글 바뀜·강조 — 지어낸 글자 (x, y, size, weight, font, rgb, code)."""

from trlab import runhead as R

GRAY, DARK = (150, 150, 150), (20, 20, 20)


def word(text, x, y=20.0, size=7.0, rgb=GRAY, weight=400, step=7.0):
    return [(x + k * step, y, size, weight, "F", rgb, ord(ch)) for k, ch in enumerate(text)]


def test_dedupe_overprinted_chars():
    chs = word("Risk", 50) + word("Risk", 50)  # 겹쳐 찍힘
    ws = R.band_words(chs, 800.0, True)
    assert [w[0] for w in ws] == ["Risk"]


def test_words_split_by_gap_and_style():
    chs = word("Alpha", 50) + word("Beta", 50 + 5 * 7 + 20) + word("Gamma", 200, rgb=DARK)
    ws = R.band_words(chs, 800.0, True)
    assert [w[0] for w in ws] == ["Alpha", "Beta", "Gamma"]
    # 붙은 글자(틈 ≤ 1.6 × 크기)는 한 낱말
    assert [w[0] for w in R.band_words(word("AB", 50) + word("CD", 50 + 2 * 7 + 3), 800.0, True)] == ["ABCD"]


def test_emphasis_needs_group_of_three():
    three = word("One", 50) + word("Two", 120, rgb=DARK) + word("Six", 190)
    assert R.emphasized(R.band_words(three, 800.0, True)) == ["Two"]
    two = word("One", 50) + word("Two", 120, rgb=DARK)
    assert R.emphasized(R.band_words(two, 800.0, True)) == []
    # 다수 스타일이 뚜렷하지 않으면(셋이 다 다름) 없음
    tie = word("One", 50) + word("Two", 120, rgb=DARK) + word("Six", 190, rgb=(200, 0, 0))
    assert R.emphasized(R.band_words(tie, 800.0, True)) == []
    # 아래 띠는 top=False 로
    low = [(x, 790.0, *rest) for x, _, *rest in three]
    assert R.band_words(low, 800.0, True) == [] and R.emphasized(R.band_words(low, 800.0, False)) == ["Two"]


def test_boundaries_first_appearance():
    pages = {1: {"texts": [], "emph": []}, 2: {"texts": [], "emph": ["Risk"]}, 3: {"texts": [], "emph": ["Risk"]},
             4: {"texts": [], "emph": ["Climate"]}}
    assert R.boundaries(pages) == [(2, "Risk", "emph"), (4, "Climate", "emph")]


def test_attach_keeps_order_and_fuzz():
    bounds = [(2, "Overview", "emph"), (5, "RiskManagement", "emph"), (9, "Overview", "emph"), (12, "Climate", "emph")]
    items = [{"text": "Risk Management"}, {"text": "Overview"}, {"text": "Climate  Change"}, {"text": "Nothing"}]
    # Overview 는 앞 닻(5) 보다 앞인 2 가 아니라 9 · 「Climate Change」 ↔ 「Climate」 는 85 아래
    assert R.attach(items, bounds) == [5, 9, None, None]
    assert R.attach([{"text": "기후위기 대응"}], [(17, "기후위기대응", "emph")]) == [17]  # 공백 제거 비교


def test_bonus_on_anchor_and_next_if_divider():
    cand = {"rows": {"p4-b1": {"page": 4}, "p5-b1": {"page": 5}, "p9-b1": {"page": 9}}}
    b = R.bonus_of([{}, {}], [4, None], cand, {4})
    assert b[0] == {"p4-b1": R.K3_W, "p5-b1": R.K3_W} and b[1] == {}
