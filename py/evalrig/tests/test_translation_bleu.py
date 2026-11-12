"""evalrig.translation.bleu — 손셈 벡터(sacrebleu 13a 꼴, 다듬기 없음)."""
import math

import pytest

from evalrig.translation.bleu import corpus_bleu, ngrams, stats, tok13a


def test_tok13a():
    # 붙임표는 숫자 뒤에서만 뗀다(13a 규칙 넷째) — 글자 사이 붙임표는 한 토큰
    assert tok13a("Hello, world. 3.5 kg-CO2 (x)") == ["Hello", ",", "world", ".", "3.5", "kg-CO2", "(", "x", ")"]
    assert tok13a("a&amp;b") == ["a", "&", "b"]
    assert tok13a("12-3") == ["12", "-", "3"]
    assert tok13a("1,000 t") == ["1,000", "t"]


def test_identical_is_100():
    s = "the company reduced emissions by ten percent this year"
    assert corpus_bleu([s], [s])["bleu"] == pytest.approx(100.0)


def test_no_overlap_is_0():
    r = corpus_bleu(["alpha beta gamma delta"], ["one two three four"])
    assert r["bleu"] == 0.0


def test_hand_counted_example():
    # 후보 a b c d e (5) · 참조 a b c x e f (6)
    # 1-gram 4/5 · 2-gram (ab, bc) 2/4 · 3-gram (abc) 1/3 · 4-gram 0/2 → 다듬기 없음이라 BLEU 0
    st = stats("a b c d e", "a b c x e f")
    assert st == [5, 6, 4, 5, 2, 4, 1, 3, 0, 2]
    assert corpus_bleu(["a b c d e"], ["a b c x e f"])["bleu"] == 0.0
    # 후보 a b c d (4) · 참조 a b c d e (5) — p = 1,1,1,1 · 짧음 벌점 exp(1 − 5/4)
    r = corpus_bleu(["a b c d"], ["a b c d e"])
    assert r["bp"] == pytest.approx(math.exp(1 - 5 / 4))
    assert r["bleu"] == pytest.approx(100 * math.exp(1 - 5 / 4))


def test_brevity_penalty_only_when_shorter():
    r = corpus_bleu(["a b c d e f"], ["a b c d e"])
    assert r["bp"] == 1.0 and r["precisions"][0] == pytest.approx(100 * 5 / 6)


def test_corpus_level_and_clip():
    assert ngrams(["a", "a"], 1) == {("a",): 2}
    st = stats("the the the the", "the cat")
    assert st[2:4] == [1, 4]  # 잘라 셈
    r = corpus_bleu(["a b c d", "e f g h"], ["a b c d", "e f g h"])
    assert r["segments"] == 2 and r["bleu"] == pytest.approx(100.0)
    assert corpus_bleu([], [])["bleu"] is None
    with pytest.raises(ValueError):
        corpus_bleu(["a"], [])
