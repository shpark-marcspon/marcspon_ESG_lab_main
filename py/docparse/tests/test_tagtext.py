"""번호표의 대상 고르기와 쪽의 글 (`tagtext`, 밴드 VT1). 꼴을 보지 않는 기준과, 글·그림에 값이 남지 않는지를 본다.

합성(synthetic) 쪽만 쓴다. 실제 보고서의 값이 아니다.
"""
import json
import re

import pymupdf
import pytest

from docparse import tagtext
from docparse.cli import main
from docparse.document import parse_pdf
from docparse.engine import open_pdf
from docparse.tagpages import tag_page, tag_rect

from .conftest import put, seam
from .test_chartcand import bars, new_doc, ruled_table

pytestmark = pytest.mark.usefixtures("engine")

SENTENCE = "The synthetic total rose by 37 percent over the previous period according to the note"


def _page(doc):
    """막대 넷과 그 위의 값, 가로축의 연도, 머리글이 선 괘선 표, 숫자가 든 문단."""
    page = doc[0]
    bars(page, labels=("12", "25", "18", "3,141"))
    for i, year in enumerate(("2021", "2022", "2023", "2024")):
        put(page, 60 + i * 30, 212, year)
    ruled_table(page, y=225)
    put(page, 40, 30, SENTENCE)
    return page


def _targets(tmp_path, doc):
    path = tmp_path / "synthetic_tagtext.pdf"
    doc.save(path)
    model = parse_pdf(path)
    with open_pdf(path) as pdf:
        words, tags, marks = tagtext.targets_document(pdf, model)
    return path, model, words, tags, marks


def test_targets_are_the_loose_numbers_and_nothing_about_shape(tmp_path):
    doc = new_doc()
    _page(doc)
    _path, _model, words, tags, marks = _targets(tmp_path, doc)
    by_text = {w["text"]: w for w in words}
    assert marks == [1]
    assert by_text["37"]["place"] == "paragraph" and by_text["1,200"]["place"] == "headed_table"
    assert by_text["2022"]["year"] and not by_text["25"]["year"]
    tagged = {t["id"] for t in tags}
    # 막대 위의 값만 번호표를 받는다: 연도, 머리글이 선 표의 칸, 문단 속 숫자는 받지 않는다.
    assert {w["text"] for w in words if w["key"] in tagged} == {"12", "25", "18", "3,141"}
    assert [t["n"] for t in tags] == [1, 2, 3, 4]
    assert all("text" not in t for t in tags)  # 번호표 목록에는 값의 글자가 없다


def test_select_targets_rules_one_by_one():
    word = lambda key, page, place, year=False: {"key": key, "page": page, "bbox": [0, 0, 5, 5], "text": "9", "links": [], "place": place, "year": year}
    words = [word("p1-w1", 1, "label_block"), word("p1-w2", 1, "none"), word("p1-w3", 1, "loose_table"), word("p1-w4", 1, "headed_table"),
             word("p1-w5", 1, "paragraph"), word("p1-w6", 1, "none", year=True), word("p2-w1", 2, "none"), word("p3-w1", 3, "label_block")]
    tags = tagtext.select_targets(words, {1, 3})
    assert [(t["id"], t["n"]) for t in tags] == [("p1-w1", 1), ("p1-w2", 2), ("p1-w3", 3), ("p3-w1", 1)]  # 표식 구역이 없는 2쪽은 통째로 빠진다


def test_page_text_carries_no_value_digits(tmp_path):
    doc = new_doc()
    _page(doc)
    path, _model, _words, tags, _marks = _targets(tmp_path, doc)
    with open_pdf(path) as pdf:
        lines = tagtext.page_lines(pdf[0], tags)
        open_lines = tagtext.page_lines(pdf[0], tags, mask=False)
    text = "\n".join(line["text"] for line in lines)
    # 번호표가 덮은 값은 번호로, 그 밖의 숫자는 ▒ 로. 연도 꼴만 남는다.
    assert sorted(int(n) for n in re.findall(r"〔(\d+)〕", text)) == [1, 2, 3, 4]
    left = [w for w in re.sub(r"〔\d+〕", " ", text).split() if re.search(r"\d", w)]
    assert left and all(tagtext.is_year(w) for w in left)
    for value in ("3,141", "1,200", "1,304", "8.25", "37"):
        assert value not in text
    assert "▒ percent" in text and "Alpha" in text
    # 가림을 끄면 번호표가 덮지 않은 숫자가 그대로 실린다 (번호표가 덮은 값은 그래도 번호다).
    assert "1,200" in "\n".join(line["text"] for line in open_lines) and "3,141" not in "\n".join(line["text"] for line in open_lines)
    assert all(0 <= line["x0"] <= line["x1"] <= tagtext.GRID and 0 <= line["y"] <= tagtext.GRID for line in lines)


def test_axis_labels_get_their_own_place(tmp_path):
    doc = new_doc()
    _page(doc)
    path, _model, _words, tags, _marks = _targets(tmp_path, doc)
    with open_pdf(path) as pdf:
        lines = tagtext.page_lines(pdf[0], tags)
    axis_y = min(line["y"] for line in lines if line["text"] == "2023")  # 표 머리글의 연도가 아니라 막대 밑의 것
    years = [line for line in lines if line["text"] in ("2021", "2022", "2023", "2024") and line["y"] == axis_y]
    assert len(years) == 4 and len({line["x0"] for line in years}) == 4  # 한 줄로 뭉치지 않는다
    marks = {int(re.fullmatch(r"〔(\d+)〕", line["text"]).group(1)): line for line in lines if re.fullmatch(r"〔\d+〕", line["text"])}
    # 값의 번호표와 그 아래 연도가 같은 세로줄에 놓인다 (가로 자리가 겹친다).
    for n, year in zip(sorted(marks, key=lambda k: marks[k]["x0"]), sorted(years, key=lambda line: line["x0"])):
        assert marks[n]["x0"] <= year["x1"] and year["x0"] <= marks[n]["x1"] + 30


@pytest.mark.pymupdf_only
def test_tagged_image_has_no_value_glyphs(tmp_path):
    doc = new_doc()
    _page(doc)
    path, _model, words, tags, _marks = _targets(tmp_path, doc)
    raw = {w["key"]: w["text"] for w in words}
    with pymupdf.open(path) as pdf:
        page = pdf[0]
        assert tagtext.visible_numbers(seam(page), tags) == {"year": 6, "other": 7}  # 연도 넷 + 표 머리글의 둘, 표의 여섯 칸 + 문단의 하나
        tag_page(page, tags)
        after = page.get_text("text")
        for t in tags:
            assert raw[t["id"]] not in after.split()            # 글자 층에서 사라졌다
            clip = pymupdf.Rect(tag_rect(t["bbox"], t["n"]))
            pix = page.get_pixmap(dpi=144, clip=clip, colorspace=pymupdf.csRGB)
            dark = sum(1 for i in range(0, len(pix.samples), 3) if max(pix.samples[i:i + 3]) < 80)
            assert dark == 0                                     # 번호표 안에 검은 값 글자가 남지 않았다 (붉은 번호뿐)
        assert "1,200" in after and "2022" in after              # 번호표를 붙이지 않은 글자는 그대로다


def test_cli_writes_targets_and_lines(tmp_path, capsys):
    doc = new_doc()
    _page(doc)
    path = tmp_path / "synthetic_cli.pdf"
    doc.save(path)
    (tmp_path / "document.json").write_text(json.dumps(parse_pdf(path)), encoding="utf-8")
    assert main(["tagtargets", str(path), "--document", str(tmp_path / "document.json"), "--out", str(tmp_path / "t")]) == 0
    assert json.loads(capsys.readouterr().out)["tags"] == 4
    assert main(["tagtext", str(path), "--tags", str(tmp_path / "t" / "tags.json"), "--out", str(tmp_path / "t")]) == 0
    lines = json.loads((tmp_path / "t" / "page-lines.json").read_text(encoding="utf-8"))
    assert list(lines) == ["1"] and lines["1"]["tags"] == 4 and lines["1"]["visible_numbers"] == {"year": 6, "other": 7}
