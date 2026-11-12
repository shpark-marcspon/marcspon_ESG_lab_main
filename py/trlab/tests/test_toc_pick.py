"""창 셈·메움·끝 쪽·간지 두 판·(e) 줄 꼴(지어낸 자료, 네트워크 0)."""

from trlab.cands import line
from trlab.common import Block
from trlab.toc_pick import divider_views, prompt, to_pred, windows


def it(text, printed=None, level=2):
    return {"text": text, "printed_page": printed, "level": level}


def test_windows_numbered_unnumbered_and_none():
    items = [it("Chapter A", None, 1), it("One", 5), it("Two", 9), it("Chapter B", None, 1), it("Three", 40)]
    w = windows(items, {}, 0, 60)
    assert w[1] == (5, [4, 5, 6]) and w[2] == (9, [8, 9, 10])
    assert w[0] == (None, [1, 2, 3, 4, 5])  # 앞 번호 없음 → 1 ~ 다음 번호 쪽
    assert w[3][1] == list(range(29, 41))  # 8~40 은 12쪽을 넘어 다음 번호 쪽 쪽으로 12쪽
    # 꼬리말 사상·어긋남을 쓴다
    assert windows([it("X", 10)], {10: 12}, 0, 60)[0] == (12, [11, 12, 13])
    # 목차에 번호가 하나도 없으면 책 전체
    w0 = windows([it("P"), it("Q")], {}, 0, 7)
    assert w0 == [(None, list(range(1, 8)))] * 2


def blocks():
    return [Block(f"p{p}-b1", "heading", p, 1, f"title {p}", (0, 0, 10, 10), 20.0, "body", 100.0, 100.0, "", [])
            for p in range(1, 21)]


def test_fill_and_end_from_all_starts():
    items = [it("One", 5), it("Two", 9), it("Extra not in gold", 12)]
    wins = windows(items, {}, 0, 20)
    pred = to_pred("x", "ko", blocks(), [2], items, ["p5-b1", "", "p20-b1"], wins)
    r = pred["items"]
    assert r[0]["start_block"] == "p5-b1" and r[0]["start_page"] == 5
    assert r[1]["start_block"] is None and r[1]["start_page"] == 9  # "" → 창 가운데로 메움
    assert r[2]["start_block"] is None and r[2]["start_page"] == 12  # 창 밖 블록도 메움
    assert pred["filled"] == 2
    assert r[0]["end_page"] == 8 and r[1]["end_page"] == 11 and r[2]["end_page"] == 20  # 정답에 없는 항목도 끝 셈에


def _row(s, e, ok=True):
    return {"found": True, "block_ok": ok, "pred": {"start_page": s, "end_page": e}}


def test_divider_two_views():
    gold = {"items": [
        {"start_page": 4, "end_page": 4, "divider": True},   # 장 간지, 시작 = 끝
        {"start_page": 5, "end_page": 9, "divider": False},
        {"start_page": 10, "end_page": 20, "divider": False},
    ]}
    # 산출 간지 4쪽 → A·B 맞음 / 산출 5쪽 → A ±1·B 맞음
    v = divider_views([_row(4, 4), _row(5, 9), _row(10, 20)], gold)
    assert v["all"]["A_start"] == 100.0 and v["all"]["B_start"] == 100.0
    v = divider_views([_row(5, 5), _row(5, 9), _row(10, 20)], gold)
    d = v["divider"]
    assert d["n"] == 1 and d["A_start"] == 0.0 and d["A_start_pm1"] == 100.0 and d["B_start"] == 100.0
    # 정답 (4,4) → 옮긴 시작 5 > 끝 4 라 끝 = 시작 (5,5) — 산출 (5,5) 와 같음(이 규칙이 없으면 (5,4) 로 틀림)
    assert d["A_end"] == 0.0 and d["B_end"] == 100.0
    # 앞 항목 끝 3 은 3+1 = 4 가 간지라 4 로 옮겨진다
    g2 = {"items": [{"start_page": 1, "end_page": 3, "divider": False}, {"start_page": 4, "end_page": 4, "divider": True}]}
    v2 = divider_views([_row(1, 4), _row(4, 4)], g2)
    assert v2["all"]["A_end"] == 50.0 and v2["all"]["B_end"] == 100.0


def test_no_divider_book_A_equals_B():
    gold = {"items": [{"start_page": 3, "end_page": 6, "divider": False}, {"start_page": 7, "end_page": 9, "divider": False}]}
    v = divider_views([_row(3, 6), _row(8, 9)], gold)
    assert v["D"] == [] and v["divider"]["n"] == 0
    for k in ("start", "start_pm1", "end", "end_pm1"):
        assert v["all"][f"A_{k}"] == v["all"][f"B_{k}"]


def test_plain_lines_have_no_attrs():
    r = {"page": 4, "pos": "상", "size": 24.0, "x": 2.7, "bold": 100, "color": True, "rep": False, "text": "Chapter"}
    assert line("p4-b1", r, attrs=False) == "p4-b1 | 4 | Chapter"
    assert line("p4-b1", r) == "p4-b1 | 4 | 상 | 24.0 | 2.7 | 100 | y | n | Chapter"
    cand = {"rows": {"p4-b1": r}}
    txt_e = prompt([it("Chapter", 4)], [(4, [3, 4, 5])], cand, {4}, attrs=False)
    txt_d = prompt([it("Chapter", 4)], [(4, [3, 4, 5])], cand, {4}, attrs=True)
    assert "p4-b1 | 4 | Chapter" in txt_e and "24.0" not in txt_e and "| 24.0 |" in txt_d
    assert "## Divider pages\n4" in txt_e
