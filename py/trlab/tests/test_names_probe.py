"""TRX7 names_probe — 지어낸 이름 셋·문장 둘, 네트워크 0. 지시문 N0~N2·답 읽기·nospan·일관율·규칙·표."""

import json

from trlab import names_probe as N

ITEMS = {"sign-01": {"id": "sign-01", "kind": "sign", "ko": "대표이사 가나다",
                     "names": [{"kr": "가나다", "table_en": "Na-Da Ga", "rule_ok_re": r"(Na)-(Da) (Ga|Ka)"}]},
         "body-01": {"id": "body-01", "kind": "body", "ko": "마바사 위원장과 아자차 이사가 서명했다.",
                     "names": [{"kr": "마바사", "table_en": "Ma Basa", "rule_ok_re": r"(Ba)-(Sa) (Ma)"},
                               {"kr": "아자차", "table_en": "J. C. Ah", "rule_ok_re": r"(Ja)-(Cha) (Ah|A)"}]}}


def test_prompts_n0_n1_n2():
    i0, t0, s0 = N.build_prompt("N0", ITEMS["body-01"])
    i1, _, _ = N.build_prompt("N1", ITEMS["body-01"])
    i2, _, _ = N.build_prompt("N2", ITEMS["body-01"])
    assert i0 == N.COMMON and t0 == ITEMS["body-01"]["ko"] and s0["required"] == ["translation", "person_names"]
    assert N.N1_RULE in i1 and "마바사" not in i1 and "never initials" in i1
    assert N.N2_HEAD in i2 and "- 마바사 -> Ma Basa" in i2 and "- 아자차 -> J. C. Ah" in i2 and "가나다" not in i2
    assert N.N1_RULE not in i0 and N.N2_HEAD not in i0


def test_read_answer_and_nospan():
    assert N.read_answer("{bad") is None and N.read_answer(json.dumps({"translation": 3})) is None
    a = N.read_answer(json.dumps({"translation": "Chairman Ba-Sa Ma and Director Ja-Cha Ah signed.", "person_names": ["Ba-Sa Ma", "J. C. Ah"]}))
    r = N.renderings(ITEMS["body-01"], a)
    assert r[0] == {"kr": "마바사", "en": "Ba-Sa Ma", "nospan": False} and r[1]["nospan"] is True  # 답 목록의 이름이 번역문 밖
    assert N.renderings(ITEMS["body-01"], N.read_answer(json.dumps({"translation": "x", "person_names": ["only one"]})))[1]["en"] is None


def _ans(tr, names):
    return {"translation": tr, "person_names": names}


def test_score_consistency_rule_table():
    runs = {"sign-01": {1: _ans("CEO Na-Da Ga", ["Na-Da Ga"]), 2: _ans("CEO Na-Da Ga", ["Na-Da Ga"]), 3: _ans("CEO Nada Ga", ["Nada Ga"])},
            "body-01": {1: _ans("Chair Ba-Sa Ma and Director J. C. Ah signed.", ["Ba-Sa Ma", "J. C. Ah"]),
                        2: _ans("Chair Ma Basa and Director Ja-Cha Ah signed.", ["Ma Basa", "Ja-Cha Ah"]),
                        3: _ans("Chair Ba-Sa Ma and Director Ja-Cha Ah signed.", ["Ba-Sa Ma", "Jacha A"])}}
    s = N.score(ITEMS, runs)
    assert s["n_occ"] == 3
    o = {x["kr"]: x for x in s["occ"]}
    assert o["가나다"]["agree"] == 2 and o["가나다"]["all3"] is False and o["가나다"]["two3"] is True and o["가나다"]["rule_ok"] == 2
    assert o["마바사"]["distinct"] == 2 and o["마바사"]["two3"] and o["마바사"]["table_ok"] == 1 and o["마바사"]["rule_ok"] == 2
    assert o["아자차"]["distinct"] == 3 and not o["아자차"]["two3"] and o["아자차"]["table_ok"] == 1 and o["아자차"]["rule_ok"] == 1
    assert o["아자차"]["nospan"] == 1  # 3회차 답 목록 "Jacha A" 가 번역문 밖
    assert s["all"]["all3"] == {"n": 0, "d": 3} and s["all"]["two3"] == {"n": 2, "d": 3}
    assert s["sign"]["occ"] == 1 and s["body"]["occ"] == 2 and s["body"]["rule_ok"] == {"n": 3, "d": 6}
    assert s["all"]["nospan"] == {"n": 1, "d": 9}


def test_score_missing_runs_count_as_missing():
    runs = {"sign-01": {1: _ans("CEO Na-Da Ga", ["Na-Da Ga"]), 2: None, 3: _ans("CEO Na-Da Ga", ["Na-Da Ga"])}}
    s = N.score({"sign-01": ITEMS["sign-01"]}, runs)
    o = s["occ"][0]
    assert o["missing"] == 1 and o["agree"] == 2 and o["all3"] is False and o["two3"] is True
