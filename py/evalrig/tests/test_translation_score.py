"""evalrig.translation.score — violations 그대로 · 남은 한국어 · 빠짐 · 검수 요약."""
from trlab.glossary_check import violations

from evalrig.translation.from_v1 import convert_pairs
from evalrig.translation.score import missing, remaining_korean, review_summary, term_violations

GLOSS = [{"kr": "온실가스", "en": "greenhouse gas"}, {"kr": "협력회사", "en": "suppliers"}, {"kr": "공급망", "en": "supply chain"}]
PAIRS = [
    {"id": "1:a", "page": 1, "ko": "공급망 위험", "en": "supply-chain risk"},
    {"id": "1:b", "page": 1, "ko": "온실가스 배출", "en": "GHG emissions"},
    {"id": "2:c", "page": 2, "ko": "협력회사 지원", "en": "support for supplier"},
    {"id": "2:d", "page": 2, "ko": "기타", "en": "남은 글 other"},
]


def test_violations_same_as_trlab():
    tv = term_violations(convert_pairs(PAIRS, run="syn"), GLOSS)
    direct = violations(PAIRS, GLOSS)[0]
    assert sorted((h["pair"], h["kr"]) for h in tv["hits"]) == sorted((h["pair"], h["kr"]) for h in direct)
    assert tv["units_with_term"] == 3
    assert tv["strict"] == {"value": 1.0, "num": 3, "den": 3}  # 1:a 는 엄격만 걸림(관대 = 붙임표 허용)
    assert tv["lenient"] == {"value": 2 / 3, "num": 2, "den": 3}
    assert {h["pair"] for h in direct if h["lenient_hit"]} == {"1:b", "2:c"}


def test_remaining_korean_flag_field():
    from evalrig.common.units import Origin, Unit

    o = Origin("v1", "translation.result@1", "syn", "translation.result-json@1")
    us = [Unit(module="translation", unit="translation.block@1", key=f"page:{i}", group=str(i), origin=o,
               payload={"ko": "가", "en": "clean english", "granularity": "page", "remainingKorean": rk})
          for i, rk in ((1, []), (2, ["가"]), (3, None))]
    r = remaining_korean(us)
    assert r["keys"] == ["page:2"]  # 영문에 한글이 없어도 산출이 남은 한국어를 적었으면 셈


def test_remaining_and_missing():
    us = convert_pairs(PAIRS, run="syn")
    rk = remaining_korean(us)
    assert rk["keys"] == ["2:d"] and rk["rate"]["den"] == 4
    m = missing(us, ["1:a", "9:z", "1:a"])
    assert m["keys"] == ["9:z"] and m["rate"] == {"value": 0.5, "num": 1, "den": 2}


def test_review_summary():
    rows = [{"split": "test", "cat": "num", "hit": "span", "false_alarm": False},
            {"split": "test", "cat": "omit", "hit": "block_only", "false_alarm": False},
            {"split": "test", "cat": "clean", "hit": None, "false_alarm": True},
            {"split": "test", "cat": "real", "hit": "miss", "false_alarm": False},
            {"split": "dev", "cat": "num", "hit": "span", "false_alarm": False}]
    s = review_summary(rows)
    assert s["planted_span"] == {"value": 0.5, "num": 1, "den": 2}
    assert s["planted_any"]["num"] == 2 and s["false_alarm"]["num"] == 1 and s["real_span"]["num"] == 0
