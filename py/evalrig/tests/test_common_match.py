"""evalrig.common.match — 손셈 값."""
from evalrig.common.match import boot_ci, count_how, is_ratio, unmeasurable, macro, norm_text, pair, prf, ratio, text_sim


def test_key_first_then_sim_one_to_one():
    gold = [{"k": "a", "t": "alpha"}, {"k": "b", "t": "beta"}, {"k": "c", "t": "gamma"}]
    pred = [{"k": "z", "t": "beta"}, {"k": "a", "t": "zzz"}, {"k": "y", "t": "beta"}]
    r = pair(gold, pred, key=lambda d: d["k"], sim=lambda g, p: 1.0 if g["t"] == p["t"] else 0.0, threshold=0.5)
    assert r["pairs"] == [
        {"gold": 0, "pred": 1, "score": 1.0, "how": "exact"},
        {"gold": 1, "pred": 2, "score": 1.0, "how": "fuzzy"},  # 같은 점수 → 후보 열쇠 'y' < 'z'
    ]
    assert r["missed"] == [2] and r["extra"] == [0]


def test_one_to_one_no_reuse():
    # 후보 하나에 정답 둘이 같은 점수로 몰려도 하나만 짝.
    r = pair(["x", "x"], ["x"], sim=lambda g, p: 1.0)
    assert len(r["pairs"]) == 1 and r["pairs"][0]["gold"] == 0
    assert r["missed"] == [1] and r["extra"] == []


def test_tie_deterministic_and_threshold():
    sim = lambda g, p: 0.7  # noqa: E731
    a = pair(["g1", "g2"], ["p1", "p2"], key=lambda s: s, sim=sim, threshold=0.6)
    b = pair(["g1", "g2"], ["p1", "p2"], key=lambda s: s, sim=sim, threshold=0.6)
    assert a == b
    assert [(d["gold"], d["pred"]) for d in a["pairs"]] == [(0, 0), (1, 1)]
    c = pair(["g1"], ["p1"], sim=sim, threshold=0.8)
    assert c["pairs"] == [] and c["missed"] == [0] and c["extra"] == [0]


def test_greedy_highest_first():
    s = {("a", "x"): 0.9, ("a", "y"): 0.8, ("b", "x"): 0.85, ("b", "y"): 0.1}
    r = pair(["a", "b"], ["x", "y"], sim=lambda g, p: s[(g, p)], threshold=0.0)
    assert [(d["gold"], d["pred"]) for d in r["pairs"]] == [(0, 0), (1, 1)]


def test_ratio_zero_den():
    assert ratio(3, 4) == {"value": 0.75, "num": 3, "den": 4}
    assert ratio(0, 0) == {"status": "no_denominator", "num": 0, "den": 0}
    assert ratio(2, 0)["status"] == "no_denominator"


def test_prf():
    r = prf(2, 4, 5)
    assert r["recall"]["value"] == 0.5 and r["precision"]["value"] == 0.4
    assert abs(r["f1"]["value"] - 2 * 0.5 * 0.4 / 0.9) < 1e-12
    z = prf(0, 0, 3)
    assert z["recall"]["status"] == "no_denominator" and z["f1"]["status"] == "no_denominator"
    assert prf(0, 2, 2)["f1"]["value"] == 0.0


def test_macro_skips_no_denominator():
    m = macro({"a": ratio(1, 2), "b": ratio(1, 1), "c": ratio(0, 0)})
    assert m == {"value": 0.75, "num": 2, "den": 2, "skipped": 1}
    assert macro({"c": ratio(0, 0)})["status"] == "no_denominator"


def test_boot_ci_seeded():
    v = [0.0, 0.5, 1.0, 1.0, 0.25]
    a, b = boot_ci(v), boot_ci(v)
    assert a == b and a["seed"] == 552 and a["B"] == 2000
    assert a["lo"] <= a["mean"] <= a["hi"]
    assert abs(a["mean"] - 0.55) < 1e-12
    assert boot_ci(v, seed=1) != a
    assert boot_ci([])["status"] == "no_denominator"
    assert boot_ci([0.4]) == {"mean": 0.4, "lo": 0.4, "hi": 0.4, "n": 1, "B": 2000, "seed": 552}


def test_text_sim():
    assert text_sim("a  b", "a b") == 1.0
    assert text_sim("", "") == 1.0
    assert 0 < text_sim("abcd", "abce") < 1
    assert norm_text("  가\n나 ") == "가 나"


def test_group_restricts_pairs():
    gold = [{"pg": 1, "t": "x"}, {"pg": 2, "t": "y"}]
    pred = [{"pg": 2, "t": "x"}, {"pg": 1, "t": "y"}]
    same = lambda g, p: 1.0 if g["t"] == p["t"] else 0.0  # noqa: E731
    r = pair(gold, pred, sim=same, threshold=0.5, group=lambda d: d["pg"])
    assert r["pairs"] == [] and r["missed"] == [0, 1] and r["extra"] == [0, 1]  # 묶음 밖 짝 0
    r2 = pair(gold, pred, sim=same, threshold=0.5)
    assert len(r2["pairs"]) == 2
    # 열쇠 짝도 묶음 안에서만
    r3 = pair([{"pg": 1, "t": "x"}], [{"pg": 2, "t": "x"}], key=lambda d: d["t"], group=lambda d: d["pg"])
    assert r3["pairs"] == []


def test_order_gold_deterministic_and_differs_from_score():
    s = {("a", "x"): 0.8, ("a", "y"): 0.7, ("b", "x"): 0.9, ("b", "y"): 0.1}
    f = lambda g, p: s[(g, p)]  # noqa: E731
    g1 = pair(["a", "b"], ["x", "y"], sim=f, order="gold")
    assert g1 == pair(["a", "b"], ["x", "y"], sim=f, order="gold")
    assert [(d["gold"], d["pred"]) for d in g1["pairs"]] == [(0, 0), (1, 1)]  # a 가 먼저 x 를 가짐
    sc = pair(["a", "b"], ["x", "y"], sim=f, order="score")
    assert [(d["gold"], d["pred"]) for d in sc["pairs"]] == [(0, 1), (1, 0)]  # 0.9 가 먼저
    import pytest
    with pytest.raises(ValueError):
        pair([], [], order="other")


def test_sim_label_is_how():
    def f(g, p):
        if g == p:
            return (1.0, "exact")
        if g in p:
            return (0.95, "contain")
        return (0.5, "fuzzy")
    r = pair(["ab", "cd", "zz"], ["xxcdxx", "ab", "zq"], sim=f, threshold=0.4, order="gold")
    assert [d["how"] for d in r["pairs"]] == ["exact", "contain", "fuzzy"]
    assert count_how(r) == {"contain": 1, "exact": 1, "fuzzy": 1}


def test_unmeasurable_is_ratio():
    u = unmeasurable("partial")
    assert is_ratio(u) and u["status"] == "unmeasurable"
