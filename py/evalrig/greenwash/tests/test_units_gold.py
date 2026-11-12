"""T1~T4 — 정규화·정답 메타·지은 표본·SG1 변환(발주서 EVB §3)."""
from __future__ import annotations

import copy
import hashlib

import pytest

from evalrig.common import gold as common_gold
from evalrig.greenwash import cli, gold
from evalrig.greenwash import units as U


@pytest.mark.parametrize(("raw", "want"), [
    ("ＡＢＣ　１２３ 가상", "ABC 123 가상"),  # 전각 글자·전각 공백 → NFKC
    ("탄소중립을\n달성\r\n하였다", "탄소중립을 달성 하였다"),  # 줄바꿈
    ("  재생   에너지\t\t확대  ", "재생 에너지 확대"),  # 연속 공백·탭·양끝
])
def test_t1_norm_matches_rwl2(raw, want):
    # RWL2 compare.py:18 의 식: " ".join(unicodedata.normalize("NFKC", s).split()) — 기대값은 손으로 쓴 글
    assert U.norm(raw) == want
    assert U.sha12(raw) == hashlib.sha256(want.encode("utf-8")).hexdigest()[:12]
    assert U.sha12(raw) == U.sha12(want)


def test_t1_norm_is_not_common_nfc():
    # 공통 match.norm_text 는 NFC 라 전각을 접지 않는다 — 그린 열쇠는 EVB 고유 norm(RWL2 와 같은 열쇠)
    from evalrig.common.match import norm_text

    assert norm_text("ＡＢＣ") != U.norm("ＡＢＣ")


@pytest.fixture()
def gs():
    return gold.sample_gold()


@pytest.mark.parametrize("field", ["review", "coverage", "pending", "provenance", "synthetic"])
def test_t2_meta_field_missing_is_error(gs, field):
    bad = copy.deepcopy(gs)
    del bad[field]
    with pytest.raises(common_gold.GoldError):
        gold.check(bad)


def test_t2_review_shape_checked(gs):
    bad = copy.deepcopy(gs)
    bad["review"]["status"] = "unknown"
    with pytest.raises(common_gold.GoldError):
        gold.check(bad)
    bad = copy.deepcopy(gs)
    bad["review"]["sample"] = {"ok": 1, "n": 1}  # status none 인데 표본
    with pytest.raises(common_gold.GoldError):
        gold.check(bad)


def test_t2_partial_refuses_precision(fixture):
    f = fixture("sg1-mini.json")
    g = gold.from_sg1(f["queries"], f["claimExpected"], f["document"])
    assert g["coverage"] == "partial"
    with pytest.raises(common_gold.GoldError):
        common_gold.guard_precision(g)
    common_gold.guard_precision(gold.sample_gold())  # exhaustive 는 통과


def test_t2_module_rules(gs):
    bad = copy.deepcopy(gs)
    bad["items"].append(copy.deepcopy(bad["items"][0]))
    with pytest.raises(common_gold.GoldError, match="겹침"):
        gold.check(bad)
    bad = copy.deepcopy(gs)
    bad["items"][0]["sentence"] += "x"
    with pytest.raises(common_gold.GoldError, match="sha12"):
        gold.check(bad)


def test_t3_sample_covers_every_sentence(gs):
    sents = gold.sample_sentences()
    assert sum(it["occurrences"] for it in gs["items"]) == len(sents) == gs["meta"]["sentences"]
    assert {(it["page"], it["sha12"]) for it in gs["items"]} == {(p, U.sha12(s)) for p, s in sents}
    per_page = {p: sum(it["occurrences"] for it in gs["items"] if it["page"] == p) for p in (1, 2, 3, 4)}
    assert per_page == {1: 7, 2: 7, 3: 8, 4: 100}  # split_sentences_kor 로 가른 수(손으로 센 것)
    labels = [it["label"] for it in gs["items"]]
    assert {lab: labels.count(lab) for lab in U.LEVELS} == {"High": 2, "Mid": 4, "Low": 9, "NonRisk": 69}
    assert all(it["category"] == {"status": "absent"} for it in gs["items"])
    assert gs["coverage"] == "exhaustive" and gs["synthetic"] is True and gs["review"]["status"] == "none"
    gold.check(gs)


def test_t3_page4_tail_after_cut_is_labelled(gs):
    from greenwash.heuristics import normalize_text_for_llm
    from greenwash.tests.sample_text import page_texts

    p4 = normalize_text_for_llm(page_texts()[3])
    assert len(p4) > 2500
    tail = [it for it in gs["items"] if it["page"] == 4 and p4.find(it["sentence"]) >= 2500]
    assert tail  # 2,500자 뒤에서 처음 나온 문장도 라벨이 있다


def test_t3_git_file_equals_build(gs):
    assert common_gold.dumps(gs) == cli.SAMPLE_GOLD.read_text(encoding="utf-8")


def test_t4_from_sg1(fixture):
    f = fixture("sg1-mini.json")
    g = gold.check(gold.from_sg1(f["queries"], f["claimExpected"], f["document"]))
    rows = [(it["claimId"], it["page"], it["label"], it["sentence"]) for it in g["items"]]
    assert rows == [
        ("G01", 12, "suspect", "가상상사는 모든 사업장의 전력을 100% 재생에너지로 바꿨다."),
        ("G02", 7, "supported", "가상상사는 2030년 감축 목표를 세웠다."),
        ("G03", 7, "partial", "가상상사의 포장재는 대부분 재활용된다."),
    ]
    assert all(it["level"] == {"status": "absent"} for it in g["items"])
    assert g["meta"]["counts"] == {"suspect": 1, "supported": 1, "partial": 1}
    assert g["setId"].endswith("001122334455") and g["synthetic"] is False


def test_t4_ids_must_agree(fixture):
    f = fixture("sg1-mini.json")
    ce = dict(f["claimExpected"])
    ce.pop("G03")
    with pytest.raises(KeyError):
        gold.from_sg1(f["queries"], ce, f["document"])
