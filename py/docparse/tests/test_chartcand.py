"""도표 후보 구역 (`chartcands`, 밴드 CT1). 꼴을 보지 않고 그림 표식과 떠 있는 숫자로 가려낸다.

합성(synthetic) 쪽만 쓴다. 실제 보고서의 값이 아니다.
"""
import json

import pymupdf
import pytest
from jsonschema import Draft202012Validator

from docparse import chartcand
from docparse.cli import main
from docparse.config import REPO_ROOT
from docparse.document import parse_pdf
from docparse.engine import open_pdf

from .conftest import hline, put

pytestmark = pytest.mark.usefixtures("engine")


def new_doc(pages=1):
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page(width=400, height=300)
    return doc


def bars(page, x=60, base=200, heights=(40, 70, 55, 90), labels=("12", "25", "18", "31"), fill=(0.8, 0.6, 0.3)):
    """막대 넷과 막대 위의 숫자. 머리글도 괘선 표도 없다."""
    for i, (h, text) in enumerate(zip(heights, labels)):
        x0 = x + i * 30
        page.draw_rect((x0, base - h, x0 + 14, base), color=None, fill=fill)
        put(page, x0 + 1, base - h - 3, text)


def ruled_table(page, y=60):
    """머리글 행이 있는 괘선 표 (가로·세로 줄뿐)."""
    xs = [40, 140, 220, 300]
    rows = [("Item", "2023", "2024"), ("Alpha", "1,200", "1,304"), ("Beta", "7.5", "8.25"), ("Gamma", "15", "16")]
    for i, r in enumerate(rows):
        for j, text in enumerate(r):
            put(page, xs[j] + 4, y + 10 + i * 14, text)
    for i in range(len(rows) + 1):
        hline(page, xs[0], xs[-1], y + i * 14)
    for x in xs:
        page.draw_line((x, y), (x, y + len(rows) * 14), width=0.25)


def candidates(tmp_path, doc, name="synthetic_cand.pdf"):
    path = tmp_path / name
    doc.save(path)
    model = parse_pdf(path)
    with open_pdf(path) as pdf:
        return chartcand.candidates_document(pdf, model), model


def of(cands, definition, page=1):
    return [c for c in cands["candidates"] if c["definition"] == definition and c["page"] == page]


def test_marks_overlapping_loose_numbers_make_a_candidate(tmp_path):
    doc = new_doc()
    bars(doc[0])
    cands, _ = candidates(tmp_path, doc)
    assert len(of(cands, "marks")) == 1 and len(of(cands, "numbers")) == 1 and len(of(cands, "both")) == 1
    both = of(cands, "both")[0]
    assert both["marks"] == 4 and both["loose_numbers"] == 4
    x0, y0, x1, y1 = both["bbox"]
    assert x0 <= 61 and x1 >= 163 and y0 <= 105 and y1 >= 199  # 막대와 숫자를 다 감싼다
    assert cands["pages"] == [{"page": 1, "marks": 1, "numbers": 1, "both": 1}]


def test_a_ruled_table_alone_is_not_a_candidate(tmp_path):
    doc = new_doc()
    ruled_table(doc[0])
    cands, model = candidates(tmp_path, doc)
    assert model["tables"] and model["tables"][0]["status"] == "ok" and model["tables"][0]["header"]["rows"] == [0]
    assert cands["candidates"] == []  # 괘선은 표식이 아니고, 머리글이 선 표의 숫자는 떠 있는 숫자가 아니다


def test_shape_does_not_matter_and_text_boxes_are_not_marks(tmp_path):
    doc = new_doc()
    page = doc[0]
    # 막대가 아닌 꼴: 원 셋과 비스듬한 선. 숫자가 옆에 있다
    for i, text in enumerate(("40%", "35%", "25%")):
        page.draw_circle((80 + i * 35, 120), 10, color=None, fill=(0.2, 0.5, 0.7))
        put(page, 72 + i * 35, 145, text)
    page.draw_line((70, 180), (160, 160), width=1.5)
    # 글자를 담은 채운 네모(글 상자) 셋: 표식이 아니다
    for i in range(3):
        page.draw_rect((250, 60 + i * 30, 360, 80 + i * 30), color=None, fill=(0.9, 0.9, 0.9))
        put(page, 256, 73 + i * 30, "Synthetic box")
    cands, _ = candidates(tmp_path, doc)
    assert len(of(cands, "both")) == 1 and of(cands, "both")[0]["marks"] == 4
    assert all(c["bbox"][2] < 240 for c in cands["candidates"])  # 글 상자 쪽에는 후보가 없다


def test_too_few_marks_or_numbers_is_not_a_candidate(tmp_path):
    doc = new_doc()
    bars(doc[0], heights=(40, 70), labels=("12", "25"))
    cands, _ = candidates(tmp_path, doc)
    assert cands["candidates"] == []
    doc = new_doc()
    put(doc[0], 60, 100, "A sentence with 12 and 25 and 18 and 31 inside it is prose, not a chart.")
    cands, _ = candidates(tmp_path, doc, "synthetic_prose.pdf")
    assert cands["candidates"] == []  # 글 문단 속 숫자는 떠 있는 숫자가 아니다


def test_both_needs_two_loose_numbers_and_numbers_do_not_need_marks(tmp_path):
    doc = new_doc()
    bars(doc[0], labels=("12", "", "", ""))  # 막대 넷에 숫자는 하나뿐
    cands, _ = candidates(tmp_path, doc)
    assert len(of(cands, "marks")) == 1 and of(cands, "both") == [] and of(cands, "numbers") == []
    doc = new_doc()
    for i, text in enumerate(("12", "25", "18")):  # 그림 요소 없이 숫자만 흩어져 있다
        put(doc[0], 60 + i * 30, 100 + i * 12, text)
    cands, _ = candidates(tmp_path, doc, "synthetic_numbers.pdf")
    assert len(of(cands, "numbers")) == 1 and of(cands, "marks") == [] and of(cands, "both") == []
    assert of(cands, "numbers")[0]["loose_numbers"] == 3


def test_marks_repeated_at_the_same_place_on_many_pages_are_page_furniture(tmp_path):
    doc = new_doc(pages=5)
    for page in doc:
        for i in range(3):  # 쪽 머리의 아이콘
            page.draw_circle((340 + i * 12, 20), 4, color=None, fill=(0.3, 0.3, 0.3))
    bars(doc[2])
    cands, _ = candidates(tmp_path, doc)
    assert [p["marks"] for p in cands["pages"]] == [0, 0, 1, 0, 0]
    assert of(cands, "marks", 3)[0]["bbox"][1] > 50


def test_cluster_links_boxes_within_the_gap_only():
    boxes = [(0, 0, 10, 10), (30, 0, 40, 10), (200, 0, 210, 10), (30, 45, 40, 55), (500, 500, 510, 510)]
    groups = sorted(sorted(g) for g in chartcand.cluster(boxes, 40.0))
    assert groups == [[0, 1, 3], [2], [4]]
    assert sorted(sorted(g) for g in chartcand.cluster(boxes, 10.0)) == [[0], [1], [2], [3], [4]]


def test_cli_writes_a_schema_valid_file_and_leaves_the_document_alone(tmp_path, capsys):
    doc = new_doc()
    bars(doc[0])
    pdf = tmp_path / "synthetic_cand.pdf"
    doc.save(pdf)
    assert main(["parse", str(pdf), "--out", str(tmp_path / "doc")]) == 0
    before = (tmp_path / "doc" / "document.json").read_bytes()
    assert main(["chartcands", str(pdf), "--document", str(tmp_path / "doc" / "document.json"), "--out", str(tmp_path / "cand")]) == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["schema_valid"] is True and summary["candidate_pages"] == {"marks": 1, "numbers": 1, "both": 1}
    out = json.loads((tmp_path / "cand" / "chart-candidates.json").read_text(encoding="utf-8"))
    schema = json.loads((REPO_ROOT / "schemas" / "chart-candidates.schema.json").read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(out)) == []
    broken = json.loads(json.dumps(out))
    broken["candidates"][0]["definition"] = "bar"
    assert list(Draft202012Validator(schema).iter_errors(broken))
    assert (tmp_path / "doc" / "document.json").read_bytes() == before
    assert out["method"]["llm_used"] is False and out["method"]["ocr_used"] is False
