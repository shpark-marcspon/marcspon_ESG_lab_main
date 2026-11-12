"""H1 쪽 번호 지도 — 지어낸 블록."""

from trlab import pagemap
from trlab.common import Block


def blk(id, page, text, region="body", y=300):
    return Block(id, "paragraph", page, 1, text, (500, y, 520, y + 10), 7.0, region, 600.0, 800.0, "", [])


def book():
    bs = []
    for p in range(1, 11):
        bs.append(blk(f"p{p}-b1", p, "본문 글 " * 20))
        if 2 <= p <= 9 and p != 5:
            bs.append(blk(f"p{p}-b9", p, f"{p + 2:02d}", region="header", y=20))  # 쪽 위 오른쪽 작은 번호, 찍힌 = 쪽 + 2
    return bs


def test_header_numbers_and_interp():
    pm = pagemap.build(book())
    assert pm["cover"]["direct"] == 7  # 2~9쪽 중 5쪽 없음 — header 를 읽음(TRX1 page_map 은 footer 만)
    assert pagemap.to_pdf3(pm, 8) == (6, "direct")
    assert pm["map"]["7"] == [5, "interp"]  # 4쪽 = 6, 6쪽 = 8 → 5쪽 = 7
    assert pm["offset"] == -2
    assert pagemap.to_pdf3(pm, 12) == (10, "offset")


def test_window_direct_one_page_interp_three():
    pm = pagemap.build(book())
    assert pagemap.window(pm, 8) == [6]
    assert pagemap.window(pm, 7) == [4, 5, 6]
    assert pagemap.window(pm, None) == []


def test_edge_block_and_spread():
    bs = [blk("p1-b1", 1, "본문 " * 30), blk("p2-b1", 2, "본문 " * 30), blk("p3-b1", 3, "본문 " * 30),
          blk("p2-b2", 2, "4  5", y=780),  # 펼침면 번호 둘, 쪽 아래 가장자리(region body)
          blk("p3-b2", 3, "6  7", y=780), blk("p3-b3", 3, "12", y=400)]  # 쪽 가운데 수는 번호 아님
    pm = pagemap.build(bs)
    assert pm["pages"]["2"]["printed"] == [4, 5] and pm["pages"]["2"]["src"] == "block"
    assert pagemap.to_pdf3(pm, 5)[0] == 2 and pagemap.to_pdf3(pm, 6)[0] == 3
    assert "12" not in pm["map"]


def test_glyph_fallback_when_blocks_cover_little():
    bs = [blk(f"p{p}-b1", p, "본문 " * 30) for p in range(1, 6)]
    chars = {p: [(560.0, 20.0, 7.0, 400, "f", (0, 0, 0), ord(c)) for c in []] for p in range(1, 6)}
    for p in range(1, 6):
        s = str(p + 10)
        chars[p] = [(560.0 + 4 * k, 20.0, 7.0, 400, "f", (0, 0, 0), ord(c)) for k, c in enumerate(s)]
        chars[p].append((300.0, 400.0, 7.0, 400, "f", (0, 0, 0), ord("9")))  # 가운데 글자는 뺌
    pm = pagemap.build(bs, chars, {p: 800.0 for p in range(1, 6)}, 7.0)
    assert pm["cover"]["direct"] == 5 and pm["pages"]["3"] == {"printed": [13], "src": "glyph"}
