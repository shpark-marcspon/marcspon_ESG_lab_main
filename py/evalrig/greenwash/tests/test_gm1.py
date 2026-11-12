"""T14 — GM1 손셈(지은 표본 PDF · 지은 쪽 글 · 지은 단가). 실비 0."""
from __future__ import annotations

from evalrig.greenwash import gm1

FILLER = "가상상사의 이사회는 정기 회의를 열어 안건을 논의했습니다. "  # 환경 낱말 없음, 규칙 후보 아님
PLEDGES = ("재생에너지 사용을 확대해 나가겠습니다. 온실가스 감축에 기여하고자 합니다. "
           "폐기물 재활용률을 높이기 위해 노력하고 있습니다.")


def test_t14_sample_pdf_hand_counts(sample_pdf):
    from greenwash.pdftext import read_page_texts

    bc = gm1.book_counts(read_page_texts(sample_pdf))
    got = {p["page"]: (p["a"], p["b"], p["c"], p["truncated"]) for p in bc["perPage"]}
    # 손셈: ⓐ=ⓑ= 규칙 후보(상한 3) + 3. 쪽 2 는 「높이고자 합니다」가 규칙의 「하고자 합니다」에 안 걸려 후보 0. 쪽 4 는 앞 2,500자 안에서 규칙 후보 셋이 다 차서 ⓐ=ⓑ, ⓒ = 환경 문장 100
    assert got == {1: (6, 6, 5, False), 2: (3, 3, 2, False), 3: (6, 6, 8, False), 4: (6, 6, 100, True)}
    assert bc["totals"] == {"a": 21, "b": 21, "c": 115}
    assert bc["pagesTruncated"] == 1 and bc["pagesEmpty"] == 0
    assert bc["extra"] == {"b": 0, "c": 94 + 2}  # 쪽 4: 100−6, 쪽 3: 8−6 (쪽 1·2 는 ⓒ<ⓐ 라 0 — 상쇄 안 함)
    assert bc["pagesWhereCgtA"] == 2


def test_t14_cut_hides_candidates():
    text = FILLER * 80 + PLEDGES  # 앞 2,500자가 넘는 환경 아닌 글 뒤에 선언 셋
    assert len(FILLER * 80) > 2500
    c = gm1.page_counts(text)
    assert (c["a"], c["b"], c["c"], c["truncated"]) == (3, 6, 3, True)  # ⓐ<ⓑ≤ⓒ+3
    assert c["a"] < c["b"]


def test_t14_empty_page():
    assert gm1.page_counts("") == {"a": 0, "b": 0, "c": 0, "chars": 0, "truncated": False, "empty": True}


def test_t14_cost_hand(tmp_path, fixture):
    from evalrig.greenwash.tests.conftest import FIXTURES

    price = gm1.pricing(FIXTURES / "models.synthetic.json", "gpt-4.1-mini")
    assert (price["input"], price["output"], price["checkedOn"]) == (2.0, 8.0, "2000-01-01")
    assert len(price["sha16"]) == 16
    dry = {"lower": {"chat_by_kind": {"eval": {"requests": 4, "input_tokens": {"status": "measured", "value": 2000}}}}}
    pe = gm1.eval_tokens(dry)
    assert pe == {"status": "measured", "perRequest": 500.0, "requests": 4, "tokens": 2000}
    c = gm1.cost(10, pe, price, 1300)
    assert (c["inputUsd"], c["outputMaxUsd"]) == (0.01, 0.104)  # 10×500×2/1M · 10×1300×8/1M


def test_t14_unmeasured_tokens_stay_a_branch():
    dry = {"lower": {"chat_by_kind": {"eval": {"requests": 4, "input_tokens": {"status": "unmeasured", "reason": "x"}}}}}
    pe = gm1.eval_tokens(dry)
    assert pe["status"] == "unmeasured"
    assert gm1.cost(10, pe, {"input": 1, "output": 1}, 1)["status"] == "unmeasured"
    assert gm1.eval_tokens({"lower": {"chat_by_kind": {"eval": {"requests": 0, "input_tokens": {"status": "measured", "value": 0}}}}})["status"] == "unmeasurable"
