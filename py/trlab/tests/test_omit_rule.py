"""TRX10 omit_rule — KR 나누기(잡음 상수)·EN subject.sentences·문턱·꼴 제외·되묻기 번호 1:1·채점·라벨 갈래. 지어낸 글, 네트워크 0."""

import hashlib
import json

from trlab import omit_rule as OR
from trlab.subject import sentences

KO3 = "첫째 문장입니다. 둘째 문장입니다. 셋째 문장입니다."
EN3 = "This is the first sentence. This is the second one. And the third one here."
EN2 = "This is the first sentence. And the third one here."


def test_kr_split_three_and_noise_constants():
    assert OR.kr_sentences(KO3) == ["첫째 문장입니다.", "둘째 문장입니다.", "셋째 문장입니다."]
    assert OR.kr_sentences("  첫째  문장입니다.\n둘째 문장입니다!  ") == ["첫째 문장입니다.", "둘째 문장입니다!"]
    # 목록 번호 「1. 」「2. 」「(XI. 」 는 다음 조각에 잇는다 — 문장이 늘지 않는다
    assert OR.kr_sentences("1. 가나 공장 입니다. 2. 다라 공장 입니다.") == ["1. 가나 공장 입니다.", "2. 다라 공장 입니다."]
    assert OR.kr_sentences("개요 (XI. 공정 관리) 를 봅니다. 다음 문장입니다.") == ["개요 (XI. 공정 관리) 를 봅니다.", "다음 문장입니다."]
    assert OR.kr_sentences("가나 Co. Ltd. 와 협력합니다. 다음 문장입니다.") == ["가나 Co. Ltd. 와 협력합니다.", "다음 문장입니다."]
    assert OR.kr_sentences("") == [] and OR.kr_sentences("짧습니다.") == ["짧습니다."] and OR.kr_sentences("가.") == []  # 3자 넘는 것만
    # 「비율.」 「면적).」 같은 진짜 문장 끝은 나뉜다
    assert len(OR.kr_sentences("재생 부품의 비율. 순수 재생 비율입니다.")) == 2


def test_en_is_subject_sentences_and_counts():
    assert OR.en_sentences(EN3) == sentences(EN3) and len(OR.en_sentences(EN3)) == 3
    assert OR.counts(KO3, EN3) == (3, 3) and OR.counts(KO3, EN2) == (3, 2)


def test_flag_only_when_en_fewer_and_kind_filter():
    assert OR.OMIT_MIN_GAP == 1
    assert OR.flagged(3, 2) and not OR.flagged(3, 3) and not OR.flagged(2, 3) and OR.flagged(5, 1)
    assert OR.applies("paragraph") and OR.applies("mixed") and not OR.applies("table") and not OR.applies("heading")
    c = OR.check(KO3, EN2, "paragraph")
    assert c == {"applies": True, "n_ko": 3, "n_en": 2, "flagged": True}
    assert OR.check(KO3, EN2, "table")["flagged"] is None and OR.check(KO3, EN3, "mixed")["flagged"] is False


def test_reask_numbers_match_kr_sentences_one_to_one():
    instr, text, schema, ks = OR.build_reask(KO3, EN2)
    assert ks == OR.kr_sentences(KO3) and len(ks) == 3
    assert "(1) 첫째 문장입니다." in text and "(2) 둘째 문장입니다." in text and "(3) 셋째 문장입니다." in text and "(4)" not in text
    assert text.index("[Korean sentences]") < text.index("(1)") < text.index("[English translation]") < text.index(EN2)
    assert instr == OR.REASK_INSTR and len(OR.REASK_INSTR) <= 600 and schema["properties"]["missing"]["items"]["type"] == "integer"
    assert schema["additionalProperties"] is False and set(schema["required"]) == {"missing", "reason"}


def test_read_reask_dedupe_oob_and_bad_json():
    a = OR.read_reask(json.dumps({"missing": [2, 2, 5, 0, 1.0, True], "reason": "r"}), 3)
    assert a["parsed"] and a["missing"] == [1, 2] and a["oob"] == [0, 5] and a["reason"] == "r"
    assert not OR.read_reask("nope", 3)["parsed"] and not OR.read_reask(json.dumps({"missing": "2"}), 3)["parsed"]
    assert OR.read_reask(json.dumps({"missing": [], "reason": ""}), 3)["missing"] == []


def test_planted_ko_no_exact_and_proportional():
    pk = OR.planted_ko_no(KO3, EN3, "This is the second one. ")
    assert pk == {"no": 2, "exact": True, "en_idx": 1, "n_ko": 3, "n_en_clean": 3}
    en4 = EN3 + " A fourth one appears."
    pk2 = OR.planted_ko_no(KO3, en4, "A fourth one appears.")
    assert pk2["exact"] is False and pk2["en_idx"] == 3 and pk2["no"] == 3  # 비례 자리(3*3/4 = 2.25 → 3)
    assert OR.planted_ko_no(KO3, EN3, "not there")["no"] is None


def test_hit_and_score_unit():
    assert OR.hit_of(2, [2]) == "span" and OR.hit_of(2, [1, 2]) == "span" and OR.hit_of(2, [3]) == "block_only" and OR.hit_of(2, []) == "miss"
    assert OR.hit_of(None, [1]) == "block_only"
    assert OR.score_unit("omit", 2, False, None) == {"rule_flag": False, "hit_rule": "miss", "hit": "miss"}
    assert OR.score_unit("omit", 2, True, None)["hit"] == "no_reask"
    assert OR.score_unit("omit", 2, True, {"missing": [2]})["hit"] == "span"
    assert OR.score_unit("clean", None, True, {"missing": []}) == {"rule_flag": True, "false_alarm_rule": True, "false_alarm": False}
    assert OR.score_unit("clean", None, True, {"missing": [1]})["false_alarm"] and not OR.score_unit("num", None, False, None)["false_alarm"]


def test_labels_fixed():
    assert OR.LABELS == ("missing", "merged", "split_noise")
    assert OR.valid_label("merged") and not OR.valid_label("real")


def test_v2_instr_untouched():
    from trlab.review_probe import V2_INSTR
    assert hashlib.sha256(V2_INSTR.encode()).hexdigest()[:16] == "71258cda68336c91"
