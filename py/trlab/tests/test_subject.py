"""지어낸 문장으로 지칭 꼴 세기와 we → the Company 바꾸기를 본다(회사 이름은 지어낸 것)."""

import re

from trlab.subject import classify, sentences, to_company

NAME = re.compile(r"(?<![A-Za-z])(?:Garam Holdings|Garam)(?:['’]s)?(?![A-Za-z])")
GEN = re.compile(r"(?<![A-Za-z])(?:the Company)(?:['’]s)?(?![A-Za-z])", re.I)


def test_classify_kinds_and_subject():
    assert classify("In 2025, we cut waste by 10%.", NAME, GEN) == ("we", "we")
    assert classify("Garam Holdings’s board met twice.", NAME, GEN) == ("name", "name")
    assert classify("The Company reviews our targets.", NAME, GEN) == ("mixed", "generic")
    assert classify("Water use fell in the US plants.", NAME, GEN) == ("none", "none")  # 대문자 US 는 나라


def test_sentences_keep_abbreviations():
    s = sentences("Garam Co. Ltd. grew. We plan more. Our team agrees!")
    assert s == ["Garam Co. Ltd. grew.", "We plan more.", "Our team agrees!"]


def test_to_company_agreement_and_possessive():
    out, sites = to_company("We also operate our plants and we have reduced emissions for us.")
    assert out == "The Company also operates its plants and the Company has reduced emissions for it."
    assert ("we", True) in sites and ("our", False) in sites and ("us", False) in sites


def test_to_company_modal_and_past_unchanged_verb():
    assert to_company("We will expand and we expanded.")[0] == "The Company will expand and the Company expanded."


# ---------- TRX5 it/its(꼴 pronoun, 선택 인자 it_rule — 기본 None 은 그대로) ----------
from trlab.subject import analyze  # noqa: E402


def test_it_rule_none_keeps_it_as_none():
    assert classify("It expanded its plants in 2025.", NAME, GEN) == ("none", "none")
    assert classify("It expanded its plants in 2025.", NAME, GEN, None, [("name", "name")]) == ("none", "none")


def test_it_rule_r1_all_pronoun_except_expletive():
    assert classify("It expanded its plants in 2025.", NAME, GEN, "R1") == ("pronoun", "pronoun")
    assert classify("In 2025, it hired 300 staff.", NAME, GEN, "R1", None) == ("pronoun", "pronoun")
    assert classify("Garam said it will grow.", NAME, GEN, "R1") == ("mixed", "name")
    assert classify("It is important to note that water fell.", NAME, GEN, "R1") == ("none", "none")  # 허사
    assert classify("If it is confirmed that fees were paid, refund them.", NAME, GEN, "R1") == ("none", "none")  # 허사
    assert classify("The IT team upgraded ITS systems.", NAME, GEN, "R1") == ("none", "none")  # 대문자 IT 는 정보기술


def test_it_rule_r2_needs_company_before_or_prev_subject():
    s = "It expanded its plants in 2025."
    assert classify(s, NAME, GEN, "R2", None) == ("none", "none")  # 블록 첫 문장, 같은 문장에 회사 없음
    assert classify(s, NAME, GEN, "R2", [("name", "name")]) == ("pronoun", "pronoun")  # 앞 문장 주어 이름
    assert classify(s, NAME, GEN, "R2", [("mixed", "generic")]) == ("pronoun", "pronoun")
    assert classify(s, NAME, GEN, "R2", [("name", "name"), ("pronoun", "pronoun")]) == ("pronoun", "pronoun")  # 앞이 pronoun 이면 이어짐
    assert classify(s, NAME, GEN, "R2", [("name", "name"), ("none", "none")]) == ("none", "none")  # 앞 문장 none 이면 끊김
    assert classify(s, NAME, GEN, "R2", [("we", "we")]) == ("none", "none")  # 앞 we 는 R2 에서 안 이음
    assert classify("Garam Holdings says it will grow.", NAME, GEN, "R2", None) == ("mixed", "name")  # 같은 문장 앞 이름 → it 도 pronoun
    assert classify("In its view, Garam will grow.", NAME, GEN, "R2", None) == ("name", "name")  # 이름이 뒤에 오면 its 는 안 셈


def test_it_rule_r3_chains_through_we_and_one_none():
    s = "It expanded its plants in 2025."
    assert classify(s, NAME, GEN, "R3", [("we", "we")]) == ("pronoun", "pronoun")
    assert classify(s, NAME, GEN, "R3", [("name", "name"), ("none", "none")]) == ("pronoun", "pronoun")  # 하나 건너
    assert classify(s, NAME, GEN, "R3", [("name", "name"), ("none", "none"), ("none", "none")]) == ("pronoun", "pronoun")  # 둘 건너(최대 2)
    assert classify(s, NAME, GEN, "R3", [("name", "name"), ("none", "none"), ("none", "none"), ("none", "none")]) == ("none", "none")
    assert classify(s, NAME, GEN, "R3", None) == ("none", "none")


def test_analyze_prev_chain_breaks_at_block_boundary():
    units = [{"id": "p1-b1", "page": 1, "kind": "paragraph", "text": "Garam grew fast. It opened two plants. It is important to note this."},
             {"id": "p1-b2", "page": 1, "kind": "paragraph", "text": "It closed one plant. We regret it."}]
    res0, rows0 = analyze(units, NAME, GEN)
    assert [r["subj"] for r in rows0] == ["name", "none", "none", "none", "we"]
    res, rows = analyze(units, NAME, GEN, it_rule="R2")
    assert [r["subj"] for r in rows] == ["name", "pronoun", "none", "none", "we"]  # 둘째 블록 첫 문장은 앞 블록을 안 잇는다
    assert res["subj"]["paragraph"] == {"name": 1, "pronoun": 1, "none": 2, "we": 1}
    assert res["rule_ok"] == res0["rule_ok"] == 0  # 지킴률 셈은 그대로(pronoun 은 we 로 안 침)
    assert res0["subj"]["paragraph"] == {"name": 1, "none": 3, "we": 1}
