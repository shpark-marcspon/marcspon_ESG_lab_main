"""겹쳐 그려진 글자를 한 번만 뽑는 옵션 (`parse --dedupe-overdrawn`, 밴드 CT1).

합성(synthetic) PDF 만 쓴다. 실제 보고서의 값이 아니다.
"""
import json

import pymupdf
import pytest

from docparse.cli import main
from docparse.document import parse_pdf, parse_pdf_places, validate
from docparse.layout import OVERDRAWN_OFFSET_RATIO

from .conftest import FONT, put

pytestmark = pytest.mark.usefixtures("engine")

# pdfium 은 같은 글이 같은 자리에 잇달아 또 그려지면 뒤의 것을 스스로 버린다 (글자 목록에 나오지 않는다).
# 그래서 「옵션을 끄면 두 번 뽑힌다」는 기대가 pdfium 엔진에서는 서지 않는다. 엔진에서 되살릴 길이 없어 xfail 로 둔다.
# `test_nearby_but_different_glyphs_stay` 는 pdfium 이 글자 단위로 버려서다 (재 본 것): `ABC` 위의 `ABD` 는 `A`·`B` 가 버려져
# `ABCD` 가 되고, 같은 자리의 크기 다른 `42` 는 `4` 가 버려져 `42` 와 `2` 가 된다.
PDFIUM_DROPS_OVERDRAWN_TEXT = {
    "test_glyphs_drawn_twice_at_the_same_place_come_out_once",
    "test_nearby_but_different_glyphs_stay",
    "test_faux_bold_drawn_slightly_shifted_comes_out_once",
    "test_option_off_is_the_default_and_leaves_the_output_alone",
}


@pytest.fixture(autouse=True)
def _pdfium_drops_overdrawn_text(request, engine):
    if engine == "pdfium" and request.node.originalname in PDFIUM_DROPS_OVERDRAWN_TEXT:
        request.applymarker(pytest.mark.xfail(strict=True, reason="pdfium 이 겹쳐 그린 글을 스스로 버린다"))


def page_of():
    doc = pymupdf.open()
    return doc, doc.new_page(width=400, height=300)


def save(tmp_path, doc, name="synthetic_overdrawn.pdf"):
    path = tmp_path / name
    doc.save(path)
    return path


def texts(model):
    return [b["text"] for b in model["blocks"] if b["kind"] != "table"]


def count(model, needle):
    return sum(t.split().count(needle) for t in texts(model))


def test_glyphs_drawn_twice_at_the_same_place_come_out_once(tmp_path):
    doc, page = page_of()
    put(page, 40, 60, "Synthetic body line")
    put(page, 40, 280, "65")
    put(page, 40, 280, "65")
    pdf = save(tmp_path, doc)
    off, on = parse_pdf(pdf), parse_pdf(pdf, dedupe_overdrawn=True)
    assert count(off, "65") == 2  # 옵션을 끄면 지금처럼 두 번 뽑힌다
    assert count(on, "65") == 1  # 한 번은 남는다 (둘 다 사라지면 안 된다)
    assert count(on, "Synthetic") == 1
    assert on["pages"][0]["notes"] == ["overdrawn_spans_dropped:1"]
    assert validate(on) == []


def test_nearby_but_different_glyphs_stay(tmp_path):
    doc, page = page_of()
    put(page, 40, 60, "1111 8888")  # 같은 글자가 바로 옆에 이어진 것은 겹친 것이 아니다
    put(page, 40, 80, "ABC")
    put(page, 40, 80, "ABD")  # 같은 자리에서 시작하지만 다른 글이다: 통째로 남긴다
    put(page, 40, 100, "77")
    put(page, 40, 100 + 3.0, "77")  # 석 pt 아래에 걸쳐 그린 다른 줄
    put(page, 200, 100, "42", size=8)
    put(page, 200, 100, "42", size=12)  # 같은 기준점이지만 크기가 다르다
    pdf = save(tmp_path, doc)
    on = parse_pdf(pdf, dedupe_overdrawn=True)
    joined = " ".join(texts(on))
    assert "1111 8888" in joined
    assert count(on, "ABC") == 1 and count(on, "ABD") == 1
    assert count(on, "77") == 2 and joined.count("42") == 2
    assert on["pages"][0]["notes"] == []
    assert on == parse_pdf(pdf)


def test_faux_bold_drawn_slightly_shifted_comes_out_once(tmp_path):
    doc, page = page_of()
    size = 10
    shift = 0.03 * size  # 굵게 보이려고 0.3pt 어긋나게 한 번 더 그린 숫자
    assert shift < OVERDRAWN_OFFSET_RATIO * size
    put(page, 40, 60, "1,234", size=size)
    put(page, 40 + shift, 60, "1,234", size=size)
    put(page, 40, 120, "Title", size=size)
    put(page, 40 + shift, 120 + shift, "Title", size=size)
    pdf = save(tmp_path, doc)
    off, on = parse_pdf(pdf), parse_pdf(pdf, dedupe_overdrawn=True)
    assert count(off, "1,234") == 2 and count(on, "1,234") == 1
    assert count(on, "Title") == 1
    # 반 글자쯤 어긋난 것은 겹쳐 그린 것으로 보지 않는다
    doc2, page2 = page_of()
    put(page2, 40, 60, "1,234", size=size)
    put(page2, 40 + 0.25 * size, 60, "1,234", size=size)
    far = parse_pdf(save(tmp_path, doc2, "synthetic_far.pdf"), dedupe_overdrawn=True)
    assert far["pages"][0]["notes"] == []


def test_overdrawn_in_another_colour_and_in_a_table_cell(tmp_path, synthetic_ruled_page):
    page = synthetic_ruled_page
    put(page, 230 - 2, 74, "1,200", right=True)  # 표의 숫자 칸(행 1, 2023 열)에 같은 글자를 한 번 더
    x = 300 - 2 - pymupdf.get_text_length("1,304", fontname=FONT, fontsize=8)
    page.insert_text((x, 74), "1,304", fontname=FONT, fontsize=8, color=(1, 0, 0))  # 색만 다른 덧그림
    pdf = save(tmp_path, page.parent)
    base = parse_pdf(pdf)
    on = parse_pdf(pdf, dedupe_overdrawn=True)
    assert base["tables"][0]["cells"][1] != ["Alpha", "t", "1,200", "1,304"]  # 끄면 칸의 글이 두 번이다
    assert on["tables"][0]["cells"][1] == ["Alpha", "t", "1,200", "1,304"]
    assert on["pages"][0]["notes"] == ["overdrawn_spans_dropped:2"]


def test_option_off_is_the_default_and_leaves_the_output_alone(tmp_path, capsys):
    doc, page = page_of()
    put(page, 40, 60, "Synthetic body line")
    put(page, 40, 280, "65")
    put(page, 40, 280, "65")
    pdf = save(tmp_path, doc)
    assert main(["parse", str(pdf), "--out", str(tmp_path / "off")]) == 0
    assert main(["parse", str(pdf), "--out", str(tmp_path / "on"), "--dedupe-overdrawn"]) == 0
    assert main(["parse", str(pdf), "--out", str(tmp_path / "onp"), "--dedupe-overdrawn", "--places"]) == 0
    capsys.readouterr()
    off = json.loads((tmp_path / "off" / "document.json").read_text(encoding="utf-8"))
    on = json.loads((tmp_path / "on" / "document.json").read_text(encoding="utf-8"))
    assert off == parse_pdf(pdf) == parse_pdf(pdf, dedupe_overdrawn=False)
    assert all(not n.startswith("overdrawn") for p in off["pages"] for n in p["notes"])
    assert count(off, "65") == 2 and count(on, "65") == 1
    # 글자 자리(곁 파일)도 남은 한 번만 가리킨다
    assert (tmp_path / "onp" / "document.json").read_bytes() == (tmp_path / "on" / "document.json").read_bytes()
    places = json.loads((tmp_path / "onp" / "char-places.json").read_text(encoding="utf-8"))
    model, places2 = parse_pdf_places(pdf, dedupe_overdrawn=True)
    assert places == places2 and places["unlinked"] == []
    ids = {b["id"] for b in model["blocks"] if b.get("text") == "65"}
    assert len(ids) == 1 and len([c for c in places["containers"] if c["id"] in ids]) == 1
