"""GE1c 그린 근거 재현 셈 시험(지어낸 글만 — 실재 글·권 열쇠·단가 숫자 0)."""
from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

from evalrig.greenwash import measure_ge1c as M


def _run(start: int, n: int) -> str:
    return "".join(chr(0xAC00 + start + i) for i in range(n))


HEAD = "가상 섹션 · 가상 제목 " + _run(500, 60)  # 머리 줄 60자 넘음


def test_old_window_is_60_chars_and_ignores_whitespace():
    p = _run(0, 120)
    assert M.found_window(p, _run(4000, 20) + p[30:90] + _run(4100, 20))
    assert not M.found_window(p, _run(4000, 20) + p[30:89] + _run(4100, 20))
    assert M.found_window("가 나 다", "가나다")  # 짧으면 그 길이


def test_new_window_skips_the_head_line():
    body = _run(0, 80)
    passage = HEAD + "\n" + body
    only_head = HEAD + " 다른 글"
    assert M.found_window(passage, only_head)  # 옛 셈은 머리 줄로 맞는다
    assert not M.found_window(passage, only_head, skip_head=True)
    assert M.found_window(passage, body[10:75], skip_head=True)


def test_new_window_without_a_newline_uses_the_whole_text():
    p = _run(0, 70)
    assert M.found_window(p, p, skip_head=True)


def test_block_score_needs_half_of_the_non_empty_blocks():
    blocks = ("12 | 34", "가상 조각", "", "다른 조각")
    assert M.found_blocks(blocks, "앞 12 / 34 뒤 가상 조각")  # 셋 중 둘(빈 글 뺌)… 1·2 맞음
    assert not M.found_blocks(blocks, "앞 12 / 34 뒤")  # 셋 중 하나
    assert not M.found_blocks(("", " "), "아무 글")


def test_block_norm_drops_pipes_and_slashes():
    assert M.bnorm("가 | 나 / 다\n라") == "가나다라"


def test_chart_rule_matches_the_ge1_rule():
    assert M.is_chart("도표: 가상")
    assert M.is_chart("가상 머리 줄\n도표: 가상")
    assert not M.is_chart("가상 문단\n본문")


def test_cut_stops_at_the_first_overflow_and_skip_continues():
    length = {"a": 5, "b": 9, "c": 2, "d": 3}
    assert M.cut(["a", "b", "c", "d"], length, 10) == ["a"]
    assert M.cut_skip(["a", "b", "c", "d"], length, 10) == ["a", "c", "d"]
    small, big = M.cut(["a", "c", "d", "b"], length, 7), M.cut(["a", "c", "d", "b"], length, 19)
    assert big[: len(small)] == small


def test_summarize_splits_body_and_chart_and_counts_pairs():
    per = {
        "Q1": [{"id": "P1", "chart": False, "old": True, "new": True, "block": True},
               {"id": "P2", "chart": True, "old": True, "new": False, "block": True}],
        "Q2": [{"id": "P3", "chart": False, "old": False, "new": False, "block": True}],
    }
    s = M.summarize(per)
    assert s["all"]["old"] == {"mean": 0.5, "n": 2, "found": 2, "cores": 3}
    assert s["body"]["new"] == {"mean": 0.5, "n": 2, "found": 1, "cores": 2}
    assert s["chart"]["new"] == {"mean": 0.0, "n": 1, "found": 0, "cores": 1}
    assert s["chart"]["block"]["found"] == 1


def test_score_claim_reports_the_three_metrics():
    c = M.Core("P1", HEAD + "\n" + _run(0, 70), ("가상 블록",))
    (row,) = M.score_claim([c], HEAD)
    assert row == {"id": "P1", "chart": False, "old": True, "new": False, "block": False}


def test_no_book_keys_or_prices_in_the_source():
    src = Path(M.__file__).read_text(encoding="utf-8")
    strings = [t.string for t in tokenize.generate_tokens(io.StringIO(src).readline) if t.type == tokenize.STRING]
    assert not any(re.search(r"\b\d+\.\d+\s*(USD|\$)", s) for s in strings)
    assert "/Users/" not in src


def test_block_half_threshold_is_inclusive():
    """맞은 블록이 정확히 절반이면 찾음(>=), 절반 미만이면 못 찾음."""
    assert M.found_blocks(("가상 하나", "가상 둘"), "가상 하나")  # 1/2
    assert not M.found_blocks(("가상 하나", "가상 둘", "가상 셋"), "가상 하나")  # 1/3


def test_cut_keeps_a_passage_that_lands_exactly_on_the_budget():
    """더한 글자가 예산과 같으면 넣고(> 일 때만 멈춤), 한 글자 넘으면 멈춘다."""
    length = {"a": 4, "b": 6, "c": 1}
    assert M.cut(["a", "b", "c"], length, 10) == ["a", "b"]
    assert M.cut(["a", "b", "c"], length, 9) == ["a"]
    assert M.cut_skip(["a", "b", "c"], length, 9) == ["a", "c"]
