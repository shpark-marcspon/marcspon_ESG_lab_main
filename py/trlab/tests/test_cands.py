"""지어낸 블록 + 속성으로 제목 후보 규칙을 본다."""

from trlab.cands import build, recall
from trlab.common import Block


def blk(id, page, text, kind="paragraph", y=200, order=1, x=50, region="body"):
    return Block(id, kind, page, order, text, (x, y, x + 100, y + 20), 9.0, region, 600.0, 800.0, "", [])


def at(size, bold=0.0, pos="중", color=False):
    return {"status": "ok", "n": 10, "size_max": size, "size_mode": size, "weight_max": 400, "bold_share": bold,
            "heavier": bold, "color": "#000000", "color_diff": color, "pos": pos}


def book():
    bs, A = [], {}
    body = "가나다라 마바사 아자차카 타파하 " * 8
    for p in range(1, 9):
        # 쪽마다 본문 다섯
        for i in range(5):
            b = blk(f"p{p}-b{10 + i}", p, body + str(i), y=300 + 40 * i, order=10 + i, x=40 + 7 * p)
            bs.append(b)
            A[b.id] = at(9.0)
    # 3~6쪽 맨 위 달린 제목(같은 글·같은 크기) → 3쪽 것만
    for p in (3, 4, 5, 6):
        b = blk(f"p{p}-b1", p, "Running Title", kind="heading", y=40, order=1, x=10 + 40 * p)  # 자리가 달라 띠 거름엔 안 걸림
        bs.append(b)
        A[b.id] = at(18.0, 1.0, "상")
    # 7쪽 간지: 글 블록 하나(작은 사진 설명) + 큰 글이 든 표 블록
    bs = [b for b in bs if b.page != 7]
    A = {k: v for k, v in A.items() if not k.startswith("p7-")}
    cap = blk("p7-b1", 7, "photo caption", y=100, order=1)
    tab_text = "41 | Chapter\n" + "\n".join(f"{42 + i} | Item number {i} of this chapter" for i in range(6))  # 120자 넘어 (가)로는 안 걸림
    tab = Block("p7-b2", "table", 7, 2, tab_text, (40, 500, 400, 600), 0.0, "body", 600.0, 800.0, "t1", [])
    bs += [cap, tab]
    A["p7-b1"] = at(6.0)
    A["p7-b2"] = dict(at(40.0), big_text="Chapter")
    attrs = {"base": {"size": 9.0, "weight": 400, "color": "#000000"}, "blocks": A,
             "pages": {str(p): {"status": "ok", "chars": 100, "unassigned": 0, "image_coverage": 0.0} for p in range(1, 9)}}
    attrs["pages"]["8"]["status"] = "no_text"
    return bs, attrs


def test_running_title_first_page_only():
    bs, attrs = book()
    r = build(bs, attrs)
    assert "p3-b1" in r["rows"]
    assert not any(f"p{p}-b1" in r["rows"] for p in (4, 5, 6))


def test_divider_largest_block_is_candidate():
    bs, attrs = book()
    r = build(bs, attrs)
    assert r["pages"][7]["divider"] is True
    assert "p7-b2" in r["pages"][7]["cands"] and r["rows"]["p7-b2"]["text"] == "Chapter"
    assert r["pages"][8] == {"status": "no_text", "divider": False, "cands": []}


def test_nav_strip_filtered():
    bs, attrs = book()
    # 쪽마다 같은 자리 같은 글(탐색 띠) — body() 가 거른다
    for p in range(1, 7):
        b = Block(f"p{p}-b90", "heading", p, 90, "NAV MENU", (10, 10, 60, 20), 9.0, "body", 600.0, 800.0, "", [])
        bs.append(b)
        attrs["blocks"][b.id] = at(20.0, 1.0, "상")
    r = build(bs, attrs)
    assert not any(k.endswith("-b90") for k in r["rows"])


def test_recall_alt_and_divider_split():
    gold = {"items": [
        {"start_block": "a", "alt_blocks": ["b"], "divider": False},
        {"start_block": "c", "alt_blocks": [], "divider": False},
        {"start_block": "d", "alt_blocks": ["e", "f"], "divider": True},
    ]}
    r = recall(gold, {"b", "f"})
    assert r["all"] == {"hit": 2, "n": 3, "pct": 66.7}
    assert r["divider"]["hit"] == 1 and r["divider"]["n"] == 1
    assert r["other"] == {"hit": 1, "n": 2, "pct": 50.0}


def test_divider_ratio_and_union():
    """TRX3 H5: 쪽 안 최대 크기 ÷ 본문 ≥ 4.5 인 가운데 쪽만, 첫·끝 쪽은 뺌, union = trx2 ∪ ratio."""
    bs, attrs = book()
    A = attrs["blocks"]
    attrs["pages"]["8"]["status"] = "ok"
    for p in (1, 4, 8):  # 1·8쪽(첫·끝)과 4쪽에 큰 글자(45pt ÷ 9 = 5)
        b = blk(f"p{p}-b30", p, f"Big Title {p}", y=60 + 30 * p, order=30, x=100 + 20 * p)  # 쪽마다 다른 글·자리(띠 거름 피함)
        bs.append(b)
        A[b.id] = at(45.0)
    r = build(bs, attrs, divider_rule="ratio")
    assert [p for p, v in r["pages"].items() if v["divider"]] == [4]
    u = build(bs, attrs, divider_rule="union")
    assert sorted(p for p, v in u["pages"].items() if v["divider"]) == [4, 7]  # 7 은 trx2 규칙
    t = build(bs, attrs)
    assert 4 not in [p for p, v in t["pages"].items() if v["divider"]]  # 기본은 TRX2 그대로
