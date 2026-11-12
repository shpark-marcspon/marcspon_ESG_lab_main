"""지어낸 글로 용어집 위반(엄격/관대)과 불일치 검사기를 본다."""

from trlab.glossary_check import inconsistency, lenient_present, strict_present, violations

G = [{"kr": "녹색위원회", "en": "Green Committee"}, {"kr": "가람재단", "en": "Garam Foundation"}]


def test_strict_is_engine_rule():
    assert strict_present("Green Committee", "the green  committee met")  # 대소문자·공백만
    assert strict_present("Green Committee", "both Green Committees met")  # 복수 -s 는 부분 문자열로 이미 받음
    assert not strict_present("Green Committee", "the Green-Committee")
    assert not strict_present("Green Committee", "the Green Com- mittee")


def test_lenient_variants():
    assert lenient_present("Green Committee", "both Green Committees met")  # 복수
    assert lenient_present("Green Committee", "the Green Committee’s chair")  # 소유격
    assert lenient_present("Green Committee", "the Green-Committee")  # 하이픈
    assert lenient_present("Green Committee", "the Green Com- mittee")  # 줄 끝 끊김
    assert lenient_present("the Green Committee", "Green Committee")  # 앞 the
    assert not lenient_present("Green Committee", "the Blue Committee")


def test_violation_lenient_subset_of_strict():
    pairs = [{"id": "a", "page": 1, "ko": "녹색위원회는 회의했다", "en": "The Green-Committee met"},
             {"id": "b", "page": 1, "ko": "녹색위원회가 정했다", "en": "The Eco Board decided"},
             {"id": "c", "page": 2, "ko": "가람재단 후원", "en": "Garam Foundation support"}]
    hits, occ, ok_s, ok_l = violations(pairs, G)
    assert {h["pair"] for h in hits} == {"a", "b"}  # 엄격: 하이픈도 걸림
    assert {h["pair"] for h in hits if h["lenient_hit"]} == {"b"}  # 관대 ⊂ 엄격
    assert occ["녹색위원회"] == 2 and ok_l["녹색위원회"] == 1


def test_inconsistency_flags_two_renderings_and_masks_longer_term():
    pairs = [{"id": str(i), "page": i, "ko": "녹색위원회 보고", "en": "Green Committee report"} for i in range(4)]
    pairs += [{"id": "x", "page": 9, "ko": "녹색위원회 보고", "en": "Eco Board report"},
              {"id": "y", "page": 9, "ko": "큰녹색위원회 회의", "en": "Grand Board meeting"}]
    flags, n = inconsistency(pairs, [{"kr": "녹색위원회", "en": "Green Committee"}, {"kr": "큰녹색위원회", "en": "Grand Board"}],
                             min_occ=3, min_dice=0.1)
    f = {x["kr"]: x for x in flags}
    assert "녹색위원회" in f
    rend = {r[0] for r in f["녹색위원회"]["renderings"]}
    assert any("green committee" in r for r in rend) and any("eco board" in r for r in rend)
    assert all("grand" not in r for r in rend)  # 긴 KR(큰녹색위원회) 자리는 짧은 것으로 세지 않음
