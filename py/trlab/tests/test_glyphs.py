"""합성 PDF(pypdfium2 로 지음, 표준 글꼴·라틴 글만)로 글자 속성 셈을 본다. 네트워크 0."""

import ctypes
import json

import pypdfium2 as pdfium
import pypdfium2.raw as C

from trlab import glyphs

W, H = 300.0, 400.0


def _text(pdf, page, font, size, s, x, y, rgb, scale=1.0):
    o = C.FPDFPageObj_NewTextObj(pdf.raw, font, size)
    b = (s + "\0").encode("utf-16-le")
    C.FPDFText_SetText(o, ctypes.cast(ctypes.c_char_p(b), ctypes.POINTER(C.FPDF_WCHAR)))
    C.FPDFPageObj_SetFillColor(o, *rgb, 255)
    C.FPDFPageObj_Transform(o, scale, 0, 0, scale, x, y)
    C.FPDFPage_InsertObject(page.raw, o)


def make(tmp_path):
    pdf = pdfium.PdfDocument.new()
    page = pdf.new_page(W, H)
    _text(pdf, page, b"Helvetica-Bold", 24, "Title", 20, 360, (220, 0, 0))  # 위(사용자 공간 y 360 → 위에서 40)
    _text(pdf, page, b"Helvetica", 10, "Body text here", 20, 200, (0, 0, 0))
    _text(pdf, page, b"Helvetica", 10, "More body text", 20, 180, (0, 0, 0))
    _text(pdf, page, b"Helvetica", 5, "Scaled", 20, 100, (0, 0, 0), 2.0)  # 5pt × 2 = 10pt
    C.FPDFPage_GenerateContent(page.raw)
    p = tmp_path / "t.pdf"
    pdf.save(str(p))
    blocks = [  # 왼쪽 위 원점 bbox
        {"id": "p1-b1", "page": 1, "order": 1, "kind": "heading", "bbox": [15, 15, 120, 50], "text": "Title", "font_size": 24.0, "region": "body"},
        {"id": "p1-b2", "page": 1, "order": 2, "kind": "paragraph", "bbox": [15, 190, 200, 225], "text": "Body text here More body text", "font_size": 10.0, "region": "body"},
        {"id": "p1-b3", "page": 1, "order": 3, "kind": "paragraph", "bbox": [15, 285, 120, 305], "text": "Scaled", "font_size": 10.0, "region": "body"},
        {"id": "p1-b4", "page": 1, "order": 4, "kind": "paragraph", "bbox": [200, 330, 290, 390], "text": "", "font_size": 0, "region": "body"},
    ]
    doc = {"pages": [{"page": 1, "width": W, "height": H, "image_coverage": 0.0}], "blocks": blocks, "tables": []}
    d = tmp_path / "document.json"
    d.write_text(json.dumps(doc))
    return p, d


def test_attrs_on_synthetic_pdf(tmp_path):
    p, d = make(tmp_path)
    r = glyphs.run("t", "ko", pdf=p, doc_path=d)
    a = r["blocks"]
    # y 뒤집기: 위쪽 큰 글이 왼쪽 위 원점 bbox(15~50) 블록에 붙는다
    assert a["p1-b1"]["n"] == 5 and a["p1-b1"]["size_max"] == 24.0
    # 이름 갈래 굵음(표준 글꼴은 weight 0) · 색 다름 · 자리 상
    assert a["p1-b1"]["bold_share"] == 1.0 and a["p1-b2"]["bold_share"] == 0.0
    assert a["p1-b1"]["color_diff"] is True and a["p1-b2"]["color_diff"] is False
    assert a["p1-b1"]["pos"] == "상" and a["p1-b3"]["pos"] == "하"
    # 글 행렬 배율: 5pt × 2 = 10.0
    assert a["p1-b3"]["size_max"] == 10.0
    # 글자 없는 블록은 0 으로 접지 않고 갈래로
    assert a["p1-b4"]["status"] == "no_chars"
    assert r["base"]["size"] == 10.0 and r["pages"]["1"]["status"] == "ok" and r["pages"]["1"]["unassigned"] == 0
    assert r["check"]["size_match_any"] == 1.0


def test_size_mismatch_page(tmp_path):
    p, d = make(tmp_path)
    doc = json.loads(d.read_text())
    doc["pages"][0]["height"] = H + 5
    d.write_text(json.dumps(doc))
    r = glyphs.run("t", "ko", pdf=p, doc_path=d)
    assert r["pages"]["1"]["status"] == "size_mismatch"


def test_is_bold_weight_then_name():
    assert glyphs.is_bold(700, "Any-Regular") and not glyphs.is_bold(400, "Any-Bold")
    assert glyphs.is_bold(0, "X-Bold") and glyphs.is_bold(-1, "YcBd") and not glyphs.is_bold(0, "Helvetica")
