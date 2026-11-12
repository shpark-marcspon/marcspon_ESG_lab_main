"""도표 찾기와 코드 라벨 (지어낸 좌표·그림 요소). 원본 PDF 를 읽지 않는다."""
import pymupdf
import pytest

from docparse.charts import _nearest_category, label_page
from docparse.layout import Chunk
from docparse.places import _link_table, _segment, _split_by_bands
from docparse.tagpages import render_tagged, tag_rect

from .conftest import seam

pytestmark = pytest.mark.usefixtures("engine")

YELLOW, GREY = (0.97, 0.71, 0.0), (0.49, 0.45, 0.4)


def _chart_page(legend=True, same_color=False, values=(("16.2", 100), ("15.9", 114), ("16.4", 200), ("16.0", 214))):
    """묶은 막대 도표 하나: 범주 A·B 에 막대 둘씩, 막대 위에 값, (선택) 범례."""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    for x, h, color in ((100, 60, YELLOW), (114, 50, GREY), (200, 70, YELLOW), (214, 55, YELLOW if same_color else GREY)):
        page.draw_rect(pymupdf.Rect(x, 200 - h, x + 12, 200), fill=color, color=None)
    page.draw_line((80, 200), (260, 200), color=(0.7, 0.7, 0.7), width=0.3)
    heights = {100: 60, 114: 50, 200: 70, 214: 55}
    for text, x in values:
        page.insert_text((x - 3, 200 - heights[x] - 3), text, fontsize=6.5)
    page.insert_text((104, 212), "Alpha", fontsize=6.5)
    page.insert_text((204, 212), "Beta", fontsize=6.5)
    page.insert_text((80, 110), "Sample ratio", fontsize=9.5)
    if legend:
        page.draw_rect(pymupdf.Rect(300, 100, 305, 105), fill=YELLOW, color=None)
        page.insert_text((308, 105), "Plan", fontsize=6.5)
        page.draw_rect(pymupdf.Rect(300, 112, 305, 117), fill=GREY, color=None)
        page.insert_text((308, 117), "Actual", fontsize=6.5)
    return doc, page


def _places(page):
    return [{"container": "p1-b1", "start": i, "end": i + 1, "text": w[4], "bbox": [round(v, 1) for v in w[:4]]} for i, w in enumerate(page.get_words())]


def _label(page, **options):
    """pymupdf 로 지은 쪽을 지금 엔진으로 다시 열어 라벨을 붙인다."""
    page = seam(page)
    return label_page(page, _places(page), **options)


def _values(page):
    charts, words = _label(page)
    return charts, {w["text"]: w for w in words if w["role"] == "value"}


def test_bar_chart_gets_category_from_axis_and_series_from_legend_color():
    _doc, page = _chart_page()
    charts, values = _values(page)
    assert len(charts) == 1 and charts[0]["title"] == "Sample ratio" and charts[0]["categories"] == ["Alpha", "Beta"]
    assert values["16.2"]["label"] == {"title": "Sample ratio", "category": "Alpha", "status": "ok", "series": "Plan", "series_status": "ok"}
    assert (values["15.9"]["label"]["category"], values["15.9"]["label"]["series"]) == ("Alpha", "Actual")
    assert (values["16.0"]["label"]["category"], values["16.0"]["label"]["series"]) == ("Beta", "Actual")
    assert values["16.4"]["coord"]["category"] == "Beta" and values["16.4"]["elem"]["category"] == "Beta"


def test_no_legend_means_no_series_name():
    _doc, page = _chart_page(legend=False)
    _charts, values = _values(page)
    assert values["16.2"]["label"]["series"] is None and values["16.2"]["label"]["series_status"] == "no_legend_for_color"
    assert values["16.2"]["label"]["category"] == "Alpha"


def test_text_boxes_are_not_bars():
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    for x, h in ((100, 20), (160, 30)):  # 표의 칸 배경처럼 글이 든 사각형
        page.draw_rect(pymupdf.Rect(x, 200 - h, x + 50, 200), fill=YELLOW, color=None)
        page.insert_text((x + 4, 195), "Header", fontsize=6.5)
    page.insert_text((104, 212), "12", fontsize=6.5)
    page.insert_text((164, 212), "34", fontsize=6.5)
    charts, _words = _label(page)
    assert charts == []


def test_value_between_two_categories_is_left_unlabelled():
    cats = [{"text": "2022", "pos": 100.0}, {"text": "2023", "pos": 116.0}]
    assert _nearest_category(101.0, cats) == ("2022", "ok")
    assert _nearest_category(108.5, cats) == (None, "between_categories")
    assert _nearest_category(200.0, cats)[0] is None  # 축 밖
    assert _nearest_category(50.0, []) == (None, "no_category_axis")


def test_methods_disagree_gives_no_category():
    # 값 글자가 옆 범주 쪽으로 밀려 있으면 좌표는 옆 범주를, 그림 요소는 제 막대의 범주를 낸다 → 합친 것은 붙이지 않는다
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    for x, h in ((100, 60), (130, 30)):
        page.draw_rect(pymupdf.Rect(x, 200 - h, x + 12, 200), fill=YELLOW, color=None)
    page.insert_text((128, 137), "77.0", fontsize=6.5)   # 첫 막대(높은 것)의 끝 가까이, 가로로는 둘째 범주에 더 가깝다
    page.insert_text((128, 215), "9.0", fontsize=6.5)
    page.insert_text((101, 212), "Aa", fontsize=6.5)
    page.insert_text((131, 212), "Bb", fontsize=6.5)
    _charts, values = _values(page)
    word = values["77.0"]
    assert word["elem"]["category"] == "Aa" and word["coord"]["category"] == "Bb"
    assert word["label"]["category"] is None and word["label"]["status"] == "methods_disagree"


def _chunk(text, x, y):
    return Chunk(x, y, x + 10, y + 8, text, 6.5)


def test_segment_and_band_split_for_duplicate_texts():
    assert _segment("12 0 감축", {"12": 1, "0": 2, "감축": 1}) == ["12", "0", "감축"]
    assert _segment("12 9", {"12": 1}) is None
    left, right = _chunk("0", 50, 100), _chunk("0", 150, 100)
    got = _split_by_bands([((5, 1), 0), ((5, 0), 0)], [right, left], 1)
    assert got[((5, 0), 0)] is left and got[((5, 1), 0)] is right


def test_link_table_binds_duplicates_by_column_band():
    table = {"method": "text", "cells": [["A", "B"], ["0", "0"]]}
    chunks = [_chunk("A", 50, 80), _chunk("B", 150, 80), _chunk("0", 150, 100), _chunk("0", 50, 100)]
    linked, failed = _link_table(table, chunks)
    assert failed == [] and linked[(1, 0)][0].x0 == 50 and linked[(1, 1)][0].x0 == 150


@pytest.mark.pymupdf_only
def test_tag_covers_the_value_text(tmp_path):
    doc, page = _chart_page()
    word = next(w for w in page.get_text("words") if w[4] == "16.2")
    pdf = tmp_path / "chart.pdf"
    doc.save(pdf)
    r = tag_rect(list(word[:4]), 7)
    assert r[0] <= word[0] and r[2] >= word[2] and r[1] <= word[1] and r[3] >= word[3]
    files = render_tagged(pdf, [{"page": 1, "n": 7, "bbox": list(word[:4])}], tmp_path / "out", dpi=72)
    assert files[0].name == "p0001.png" and files[0].stat().st_size > 0
    with pymupdf.open(pdf) as again:  # 원본 PDF 는 그대로다
        assert any(w[4] == "16.2" for w in again[0].get_text("words"))


def test_output_matches_schema():
    import json

    from jsonschema import Draft202012Validator

    from docparse.config import REPO_ROOT
    schema = json.loads((REPO_ROOT / "schemas" / "chart-labels.schema.json").read_text(encoding="utf-8"))
    _doc, page = _chart_page()
    charts, words = _label(page)
    out = {"schema_version": "0.1", "source": {"file_name": "synthetic.pdf", "sha256": "0" * 64, "page_count": 1}, "llm_used": False, "ocr_used": False,
           "charts": json.loads(json.dumps(charts)), "words": words, "unlinked": [], "place_count": 0}
    assert [e.message for e in Draft202012Validator(schema).iter_errors(out)] == []


def test_legend_color_shared_by_two_names_gives_no_series():
    # 범례의 두 이름이 같은 색이면 색으로는 계열을 가를 수 없다 → 붙이지 않는다
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    for x, h in ((100, 60), (200, 70)):
        page.draw_rect(pymupdf.Rect(x, 200 - h, x + 12, 200), fill=YELLOW, color=None)
    page.insert_text((97, 137), "16.2", fontsize=6.5)
    page.insert_text((197, 127), "16.4", fontsize=6.5)
    page.insert_text((104, 212), "Alpha", fontsize=6.5)
    page.insert_text((204, 212), "Beta", fontsize=6.5)
    for y, name in ((105, "Plan"), (117, "Actual")):
        page.draw_rect(pymupdf.Rect(300, y - 5, 305, y), fill=YELLOW, color=None)
        page.insert_text((308, y), name, fontsize=6.5)
    _charts, values = _values(page)
    assert values["16.2"]["label"]["series"] is None and values["16.2"]["label"]["series_status"] == "legend_color_shared"
    assert values["16.2"]["label"]["category"] == "Alpha"


def test_adjacent_bars_of_two_colors_at_same_distance_give_no_series():
    # 값 글자가 두 색의 막대에서 똑같이 가까우면 어느 계열인지 가를 수 없다 → 계열을 붙이지 않는다 (범주는 붙인다)
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    for x, h, color in ((100, 60, YELLOW), (112, 60, GREY), (200, 70, YELLOW), (212, 40, GREY)):
        page.draw_rect(pymupdf.Rect(x, 200 - h, x + 12, 200), fill=color, color=None)
    width = pymupdf.get_text_length("16.2", fontsize=6.5)
    page.insert_text((112 - width / 2, 137), "16.2", fontsize=6.5)   # 두 막대의 경계 바로 위
    page.insert_text((199, 127), "16.4", fontsize=6.5)
    page.insert_text((104, 212), "Alpha", fontsize=6.5)
    page.insert_text((204, 212), "Beta", fontsize=6.5)
    for y, name, color in ((105, "Plan", YELLOW), (117, "Actual", GREY)):
        page.draw_rect(pymupdf.Rect(300, y - 5, 305, y), fill=color, color=None)
        page.insert_text((308, y), name, fontsize=6.5)
    _charts, values = _values(page)
    assert values["16.2"]["label"]["series"] is None and values["16.2"]["label"]["series_status"] == "marks_overlap"
    assert values["16.2"]["label"]["category"] == "Alpha"
    assert values["16.4"]["label"]["series"] == "Plan"


def test_axis_ticks_and_numeric_categories_are_not_values():
    # 세로축 눈금(왼쪽에 세로로 줄지은 숫자)과 범주 축의 연도는 값이 아니다. 막대 위의 숫자만 값이다.
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    for x, h in ((100, 60), (160, 40)):
        page.draw_rect(pymupdf.Rect(x, 200 - h, x + 12, 200), fill=YELLOW, color=None)
    page.insert_text((99, 137), "61.5", fontsize=6.5)
    page.insert_text((159, 157), "40.2", fontsize=6.5)
    page.insert_text((99, 212), "2023", fontsize=6.5)
    page.insert_text((159, 212), "2024", fontsize=6.5)
    for text, y in (("0", 202), ("30", 172), ("60", 142)):
        page.insert_text((82 - pymupdf.get_text_length(text, fontsize=6.5), y), text, fontsize=6.5)
    _charts, words = _label(page)
    roles = {w["text"]: w["role"] for w in words}
    assert roles == {"61.5": "value", "40.2": "value", "0": "tick", "30": "tick", "60": "tick", "2023": "category", "2024": "category"}
    assert next(w for w in words if w["text"] == "61.5")["label"]["category"] == "2023"


def _grid_page():
    """괘선 표의 숫자 칸 배경: 한 줄의 칸들은 밑변이 같고, 합친 칸은 높이가 달라 「밑선이 같고 높이가 다른 사각형」이 된다."""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    for x, top in ((100, 160), (160, 180), (220, 180)):
        page.draw_rect(pymupdf.Rect(x, top, x + 60, 200), fill=(1.0, 0.96, 0.88), color=None)
        page.insert_text((x + 20, 195), "7.5", fontsize=6.5)
    for y in (160, 180, 200):
        page.draw_line((40, y), (300, y), color=(0.6, 0.6, 0.6), width=0.3)
    for i, name in enumerate(("Aa", "Bb", "Cc")):
        page.insert_text((120 + 60 * i, 212), name, fontsize=6.5)
    return doc, page


def test_cell_backgrounds_between_table_rules_are_not_bars():
    _doc, page = _grid_page()
    assert _label(page)[0] == []
    # 규칙을 끄면(고치기 전) 칸 배경이 막대로 읽힌다
    assert len(_label(page, grid_rule=False)[0]) == 1
    # 막대는 밑변만 축선에 닿는다 → 그대로 도표다
    _doc2, chart = _chart_page()
    assert len(_label(chart)[0]) == 1


@pytest.mark.pymupdf_only
def test_tag_replaces_the_text_and_puts_the_number_on_its_place():
    from docparse.tagpages import tag_page
    doc, page = _chart_page()
    words = {w[4]: list(w[:4]) for w in page.get_text("words")}
    tag_page(page, [{"n": 7, "bbox": words["16.2"]}, {"n": 12, "bbox": words["15.9"]}])
    after = {w[4]: list(w[:4]) for w in page.get_text("words")}
    assert "16.2" not in after and "15.9" not in after  # 값 글자는 지워졌다 (그림에도 텍스트 계층에도 없다)
    assert "16.4" in after and "Alpha" in after          # 번호표를 붙이지 않은 글자는 그대로다
    for n, raw in ((7, "16.2"), (12, "15.9")):
        box, rect = after[str(n)], tag_rect(words[raw], n)
        assert rect[0] <= box[0] and box[2] <= rect[2] and rect[1] <= box[1] and box[3] <= rect[3]  # 번호는 제 값의 자리에 있다
    # 그림에서도: 번호표 사각형 안은 흰 바탕에 붉은 번호뿐이다 (검은 값 글자가 남지 않는다)
    pix = page.get_pixmap(dpi=144, clip=pymupdf.Rect(tag_rect(words["16.2"], 7)))
    dark = sum(1 for i in range(0, len(pix.samples), pix.n) if max(pix.samples[i:i + 3]) < 90)
    assert dark == 0
