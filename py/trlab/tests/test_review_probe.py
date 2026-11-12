"""TRX7 review_probe — 지어낸 짝·용어집, 네트워크 0. 단위 꼴·심기·거름 셋·must_apply·V1·채점·프롬프트."""

import json

from trlab import review_probe as R

GLOSS = [{"cat": "조직", "kr": "환경위원회", "en": "Environment Committee"}, {"cat": "기업", "kr": "가나전자", "en": "Gana Electronics"},
         {"cat": "제도", "kr": "푸른길", "en": "Blue Road Program"}]
KO = "가나전자 환경위원회는 2025년 배출량을 12.5% 줄였습니다. 두 번째 문장입니다."
EN = "Gana Electronics' Environment Committee cut emissions by 12.5% in 2025. This is the second sentence. Third one here."


def _unit(planted=None, cat="num", en=EN):
    return R.make_unit("planted-01-x-" + cat, "sam", "test", "paragraph", cat, [{"ko": ["p1-b1"], "en": ["p1-b2"]}], 1, KO, en, planted, GLOSS)


def test_unit_shape_and_glossary_only_present_terms():
    u = _unit()
    assert u["ko_sha"] == R.sha16(KO) and u["en_sha"] == R.sha16(EN) and u["en_planted_sha"] is None
    assert u["glossary"] == [0, 1]  # 푸른길 은 KO 에 없다
    assert "ko" not in u and "en" not in u  # 정답 파일에 글은 없다


def test_plant_applies_once_and_records_sha():
    p = {"cat": "num", "before": "12.5%", "after": "13.5%"}
    u = _unit(p)
    assert R.apply_plant(EN, p) == EN.replace("12.5%", "13.5%") and u["en_planted_sha"] == R.sha16(EN.replace("12.5%", "13.5%"))
    try:
        R.apply_plant(EN + " 12.5%", p)
        raise AssertionError("before 가 둘이면 실패해야")
    except ValueError:
        pass
    om = {"cat": "omit", "before": "This is the second sentence. ", "after": ""}
    assert R.apply_plant(EN, om) == "Gana Electronics' Environment Committee cut emissions by 12.5% in 2025. Third one here."


def test_heading_unit_joins_four_pairs(monkeypatch):
    pairs = [{"ko": ["h1"], "en": ["h1"]}, {"ko": ["p1"], "en": ["p1"]}, {"ko": ["h2"], "en": ["h2"]}, {"ko": ["p2"], "en": ["p2"]}]

    class B:
        def __init__(self, i, t):
            self.id, self.text = i, t

    monkeypatch.setattr(R.common, "load", lambda key, lang: [B("h1", "A"), B("p1", "a a"), B("h2", "B"), B("p2", "b b")])
    ko, en = R._texts_official("sam", pairs)
    assert ko == "A\n\na a\n\nB\n\nb b" and en == ko
    u = R.make_unit("planted-02-sam-heading", "sam", "test", "heading", "heading", pairs, 3, ko, en,
                    {"cat": "heading", "before": "\n\nB\n\n", "after": "\n\nA\n\n"}, [])
    m = R.materialize(u, [])
    assert m["en"] == "A\n\na a\n\nA\n\nb b" and m["en_clean"] == en and m["planted"]["after"] == "\n\nA\n\n"


def test_sanitize3_type_span_dedupe():
    issues = [{"issue_type": "number_unit_error", "severity": "high", "original_span": "12.5%", "suggestion": "13.5%", "reason": "r"},
              {"issue_type": "ADDITION", "original_span": "x", "suggestion": "y", "reason": "허용 목록 밖"},
              {"issue_type": "OMISSION", "original_span": "not in the text", "suggestion": "s", "reason": "span 이 번역문 밖"},
              {"issue_type": "NUMBER_UNIT_ERROR", "severity": "HIGH", "original_span": "12.5%", "suggestion": "13.5%", "reason": "r"}]
    out = R.sanitize3(issues, EN)
    assert [i["issue_type"] for i in out] == ["NUMBER_UNIT_ERROR", "OMISSION"]
    assert out[1]["original_span"] == "" and out[0]["severity"] == "HIGH"
    assert R.sanitize3("not a list", EN) == [] and R.sanitize3([{"original_span": "12.5%", "suggestion": "x"}], EN)[0]["issue_type"] == "UNNATURAL"


def test_must_apply_threshold_and_v1():
    assert R.must_apply(98, False, {"COLLOCATION"}, 1) is False  # 이슈 382 의 꼴 — 짚고도 안 고침
    assert R.must_apply(98, False, {"GRAMMAR"}, 1) is True
    assert R.must_apply(95, False, set(), 0) is True and R.must_apply(96, False, set(), 0) is False
    assert R.must_apply(94, False, {"FLOW"}, 2) is True and R.must_apply(96, False, {"FLOW"}, 5) is False  # 약한 이슈만·96 이상은 안 걸림
    raw = json.dumps({"quality_score": 98, "has_critical_issue": False, "revised_text": EN,
                      "issues": [{"type": "COLLOCATION", "problem": "12.5% reads oddly", "recommendation": "13.5%"}]})
    mat = _mat(_unit())
    a0 = R.read_answer("V0e", raw, mat)
    a1 = R.read_answer("V1", raw, mat)
    assert a0["flagged"] is False and a0["extra"]["noted_not_applied"] is True and a0["extra"]["score"] == 98
    assert a1["flagged"] is True and len(a1["issues"]) == 1
    assert R.read_answer("V0r", "{not json", mat) == {"parsed": False, "issues": [], "flagged": False, "extra": {}}


def _mat(u, en=None):
    return {"id": u["id"], "book": "sam", "split": "test", "kind": "paragraph", "cat": u["cat"], "page": 1, "ko": KO,
            "en": en or EN, "en_clean": EN, "planted": u["planted"], "wrong": [], "glossary": [GLOSS[i] for i in u["glossary"]]}


def test_hit_span_overlap_rules():
    p = {"cat": "num", "before": "12.5%", "after": "13.5%"}
    mat = _mat(_unit(p), R.apply_plant(EN, p))
    assert R.hit_of(mat, [{"issue_type": "NUMBER_UNIT_ERROR", "original_span": "cut emissions by 13.5% in 2025"}]) == "span"  # 품음
    assert R.hit_of(mat, [{"issue_type": "GRAMMAR", "original_span": "Third one here"}]) == "block_only"
    assert R.hit_of(mat, []) == "miss"
    assert R.overlap("Environment Committee cut", "committee cut emissions by") is True  # 낱말 겹침 2/3 ≥ 50%
    assert R.overlap("alpha beta gamma delta", "delta epsilon zeta eta") is False
    om = {"cat": "omit", "before": "This is the second sentence. ", "after": ""}
    mo = _mat(_unit(om, "omit"), R.apply_plant(EN, om))
    assert R.hit_of(mo, [{"issue_type": "OMISSION", "original_span": ""}]) == "span"
    assert R.hit_of(mo, [{"category": "other", "original_span": "", "reason": "one sentence is missing"}]) == "span"
    assert R.hit_of(mo, [{"issue_type": "GRAMMAR", "original_span": "Third one here"}]) == "block_only"
    hd = {"cat": "heading", "before": "Third one here.", "after": "Gana Electronics' Environment Committee"}
    mh = _mat(_unit(hd, "heading"), R.apply_plant(EN, hd))
    assert R.hit_of(mh, [{"issue_type": "UNNATURAL", "original_span": "", "reason": "the same heading appears twice"}]) == "span"
    assert R.hit_of(mh, [{"issue_type": "UNNATURAL", "original_span": "Environment Committee"}]) == "span"
    real = dict(_mat(_unit()), cat="real", planted=None, wrong=["Environment Committee"])
    assert R.hit_of(real, [{"issue_type": "GLOSSARY_VIOLATION", "original_span": "Environment Committee"}]) == "span"
    clean = dict(_mat(_unit()), cat="clean", planted=None)
    assert R.hit_of(clean, [{"issue_type": "GRAMMAR", "original_span": "x"}]) is None


def test_score_unit_and_v0e_text_as_span():
    p = {"cat": "num", "before": "12.5%", "after": "13.5%"}
    mat = _mat(_unit(p), R.apply_plant(EN, p))
    raw = json.dumps({"quality_score": 80, "has_critical_issue": False, "revised_text": "",
                      "issues": [{"type": "GLOSSARY", "problem": "13.5% is wrong", "recommendation": "12.5%"}]})
    s = R.score_unit(mat, R.read_answer("V0e", raw, mat))
    assert s["flagged"] and s["hit"] == "span" and s["applied"] and s["kinds"] == ["GLOSSARY"] and not s["false_alarm"]
    clean = dict(_mat(_unit()), cat="clean", planted=None)
    s2 = R.score_unit(clean, R.read_answer("V2", json.dumps({"issues": [{"category": "other", "original_span": "x", "suggestion": "y", "reason": "z"}]}), clean))
    assert s2["false_alarm"] and s2["hit"] is None
    s3 = R.score_unit(mat, R.read_answer("V2", json.dumps({"issues": []}), mat))
    assert s3["flagged"] is False and s3["hit"] == "miss"


def test_prompts_carry_glossary_page_and_en():
    p = {"cat": "num", "before": "12.5%", "after": "13.5%"}
    mat = _mat(_unit(p), R.apply_plant(EN, p))
    instr, text, schema = R.build_prompt("V0r", mat)
    assert instr == R.QA_SYSTEM_PROMPT and "- 환경위원회 -> Environment Committee" in text and "[PAGE 1]" in text
    assert "13.5%" in text and "Style A" in text and "[Korean annotations found in the bilingual output" in text
    assert schema["properties"]["issues"]["items"]["required"] == ["issue_type", "severity", "page_num", "original_span", "suggestion", "reason"]
    i2, t2, s2 = R.build_prompt("V0e", mat)
    assert i2 == R.GENERIC_EDITORIAL_QA_PROMPT and "PAGE 1" in t2 and "13.5%" in t2 and s2["required"][0] == "quality_score"
    assert R.glossary_text([]) == "(No relevant glossary terms for this source text.)"
    assert R.glossary_text([{"kr": "짧", "en": "s"}, {"kr": "긴 용어", "en": "l"}]).split("\n")[0] == "- 긴 용어 -> l"


def test_eligible_rules():
    g = GLOSS
    pr = {"ko_text": KO, "en_text": EN}
    assert R.eligible("num", pr, g) and R.eligible("year", pr, g) and R.eligible("omit", pr, g)
    assert R.eligible("term", pr, g)  # 환경위원회(조직) — 기업 갈래만 있으면 False
    assert not R.eligible("term", {"ko_text": "가나전자는", "en_text": "Gana Electronics is"}, g)
    assert not R.eligible("num", {"ko_text": "둘", "en_text": "1) note 2) note"}, g)  # 각주 번호·KO 에 없는 수
    assert not R.eligible("neg", {"ko_text": "", "en_text": "not only this but also"}, g) and R.eligible("neg", {"ko_text": "", "en_text": "it was not applied"}, g)
    assert R.eligible("unit", {"ko_text": "", "en_text": "KRW 10 million"}, g) and not R.eligible("unit", {"ko_text": "", "en_text": "many tons"}, g)
