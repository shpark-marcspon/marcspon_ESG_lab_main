"""evalrig.translation.from_v1 — 지은 translation.result@1."""
import pytest

from evalrig.translation.from_v1 import convert_pairs, convert_result, split_pages, strip_heads
from evalrig.translation.from_v2 import convert_result_v2


def _res(pages=(7, 8, 9), af_status="done"):
    def txt(tag):
        return "".join(f"[PAGE {n}]\n\n[PAGE {n}]\n[BLOCK 1]\n{tag} page {n} one\n[BLOCK 2]  \n{tag} two\n" for n in pages)
    return {"schemaVersion": "translation.result@1", "document": {"pageNumbers": list(pages)},
            "variants": [{"label": "A", "translatedText": txt("a"), "fragments": [{"pageNum": 7, "remainingKorean": []}, {"pageNum": 8, "remainingKorean": ["가"]}, {"pageNum": 9}],
                          "autofix": {"status": af_status, "translatedText": txt("fixed"), "pages": [{"pageNum": 9, "remainingKoreanAfter": ["나"]}]}},
                         {"label": "B", "translatedText": txt("b"), "fragments": []}]}


def test_split_and_strip():
    t = "앞 글\n[PAGE 3]\n\n[PAGE 3]\n[BLOCK 1]\nx\n[PAGE 4]\ny"
    sp = split_pages(t)
    assert [n for n, _ in sp] == [3, 4]
    assert strip_heads(sp[0][1]) == "x"
    assert strip_heads("[BLOCK 12]  \n  a  \n\n[BLOCK 2]\nb") == "a\nb"


def test_page_offset_keys():
    us = convert_result(_res(), run="syn", variant="A", page_offset=39, ko_pages={40: "국문 가", 41: "국문 나"})
    assert [u.key for u in us] == ["page:40", "page:41", "page:42"]  # 결과 쪽 i → 39 + i (머리 번호 7·8·9 아님)
    assert [u.group for u in us] == ["40", "41", "42"]
    assert us[0].payload["en"] == "a page 7 one\na two" and us[0].payload["ko"] == "국문 가" and us[2].payload["ko"] is None
    assert us[0].payload["result_page"] == 7 and us[1].payload["remainingKorean"] == ["가"]
    assert all(u.payload["granularity"] == "page" for u in us)


def test_autofix_variant():
    us = convert_result(_res(), run="syn", variant="A+autofix", page_offset=0)
    assert us[0].payload["en"].startswith("fixed page 7") and us[0].payload["variant"] == "A+autofix"
    assert [u.payload["remainingKorean"] for u in us] == [None, None, ["나"]]  # 고친 쪽만 — pageNum 으로 짝
    bad = convert_result(_res(af_status="not_run"), run="syn", variant="A+autofix", page_offset=0)
    assert {u.status for u in bad} == {"failed"}


def test_page_count_mismatch_and_errors():
    r = _res()
    r["document"]["pageNumbers"] = [1, 2]
    with pytest.raises(ValueError, match="쪽"):
        convert_result(r, run="syn", variant="A", page_offset=0)
    with pytest.raises(ValueError):
        convert_result(_res(), run="syn", variant="C", page_offset=0)
    with pytest.raises(NotImplementedError):
        convert_result_v2({}, run="r")


def test_pairs_units():
    us = convert_pairs([{"id": "11:p11-b1", "page": 11, "ko": "가", "en": "a"}, {"id": "11:p11-b2", "page": 11, "ko": "나", "en": " "}], run="m")
    assert [(u.key, u.group, u.status, u.payload["granularity"]) for u in us] == [("11:p11-b1", "11", "ok", "block"), ("11:p11-b2", "11", "missing", "block")]
