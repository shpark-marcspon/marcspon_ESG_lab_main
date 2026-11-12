"""지어낸 블록으로 쪽 번호 어긋남·목차 칸 짝·블록 짝을 본다."""

from trlab.align import align_page
from trlab.common import Block, split_bilingual
from trlab.toc import _pairs_from_rows, page_map, to_pdf


def blk(id, page, text, region="body", kind="paragraph", bbox=(0, 0, 10, 10), fs=9.0, rows=None):
    return Block(id, kind, page, 1, text, bbox, fs, region, 100.0, 100.0, "", rows or [])


def test_page_map_offset_from_footer():
    # 표지·간지 둘이 번호 없이 앞에 붙은 책: PDF 3쪽에 「1」이 찍힘 → 어긋남 2
    bs = [blk(f"p{p}-b1", p, str(p - 2), region="footer") for p in range(3, 20)]
    m, off = page_map(bs)
    assert off == 2
    assert to_pdf(10, m, off, 30) == 12
    assert to_pdf(99, m, off, 30) == 30  # 끝을 넘지 않는다


def test_page_map_spread_two_numbers():
    # 펼침면: PDF 한 쪽에 「8」「9」 두 번호 → 둘 다 그 쪽으로
    bs = [blk("p5-b1", 5, "8", region="footer"), blk("p5-b2", 5, "9", region="footer"),
          blk("p6-b1", 6, "10", region="footer"), blk("p6-b2", 6, "11", region="footer")]
    m, _ = page_map(bs)
    assert m[8] == 5 and m[9] == 5 and m[11] == 6


def test_toc_rows_side_by_side_and_tail_number():
    rows = [["가나 소개", "04", "다라 전략", "10"], ["", "", "마바 계획 21", ""], ["[갑 부문]", "", "", ""]]
    got = _pairs_from_rows(rows)
    assert ("가나 소개", 4, 0) in got and ("다라 전략", 10, 2) in got
    assert ("마바 계획", 21, 2) in got
    assert ("[갑 부문]", None, 0) in got


def test_split_bilingual():
    assert split_bilingual("Green Office 녹색 사무실") == ("Green Office", "녹색 사무실")


def test_align_page_pairs_by_position_and_numbers():
    ko = [blk("k1", 1, "가나 회사는 2025년 12.5% 줄였습니다", bbox=(10, 10, 40, 20)),
          blk("k2", 1, "다라 위원회", bbox=(60, 10, 90, 20), kind="heading")]
    en = [blk("e1", 1, "The Garden Co. cut 12.5% in 2025", bbox=(11, 10, 41, 21)),
          blk("e2", 1, "Dara Committee", bbox=(60, 11, 90, 20), kind="heading")]
    pairs = {p["ko"][0]: p["en"] for p in align_page(ko, en)}
    assert pairs == {"k1": ["e1"], "k2": ["e2"]}
