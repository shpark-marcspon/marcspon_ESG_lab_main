"""엔진 이음매: 같은 PDF 를 두 엔진으로 읽어 나란히 견준다 (밴드 PD1).

PDF 는 pymupdf 로 그 자리에서 짓는다. 글꼴은 파일을 PDF 에 심는다: 심지 않은 기본 글꼴(Helvetica)은 엔진마다 대신 쓰는
글꼴이 달라 글자 상자의 높이가 다르게 나온다 (`test_unembedded_font_boxes_differ_in_height_only` 가 그 사실을 적어 둔다).
"""
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest

import docparse.engine
from docparse.cli import main
from docparse.document import ParseError, parse_pdf
from docparse.engine import DEFAULT_ENGINE, ENGINES, open_pdf

LATIN, KOREAN = "EH", "EK"
NEAR = 0.002  # pt. 두 엔진의 좌표는 단정밀도 셈의 끝자리만 다르다


def new_page(doc, width=400, height=300):
    page = doc.new_page(width=width, height=height)
    page.insert_font(fontname=LATIN, fontbuffer=pymupdf.Font("helv").buffer)
    page.insert_font(fontname=KOREAN, fontbuffer=pymupdf.Font("cjk").buffer)
    return page


def put(page, x, y, text, size=10, font=LATIN):
    page.insert_text((x, y), text, fontname=font, fontsize=size)


def width_of(text, size=10, font="helv"):
    return pymupdf.Font(font).text_length(text, fontsize=size)


def save(tmp_path, doc, name="engine.pdf", **options):
    path = tmp_path / name
    doc.save(path, **options)
    return path


def read(path, what):
    """{엔진 이름: what(쪽)} 첫 쪽을 두 엔진으로 읽는다."""
    out = {}
    for name in ENGINES:
        with open_pdf(path, name) as doc:
            out[name] = what(doc[0])
    return out


def lines_of(page):
    return [[(s["text"], round(s["size"], 3)) for s in line["spans"]] for block in page.get_text_blocks() for line in block["lines"]]


def words_of(page):
    return [(w[4], w[:4]) for w in page.get_words()]


def drawings_of(page):
    hexa = lambda c: None if c is None else "#%02x%02x%02x" % tuple(int(round(v * 255)) for v in c)
    return [("".join(it[0][0] for it in d["items"]), tuple(d["rect"]), hexa(d.get("fill")), hexa(d.get("color")),
             round(d["width"], 3) if d.get("width") is not None else None) for d in page.get_drawings()]


def assert_boxes_near(a, b, tol=NEAR):
    assert len(a) == len(b)
    for x, y in zip(a, b):
        assert all(abs(u - v) <= tol for u, v in zip(x, y)), (x, y)


# ── 고르기 ────────────────────────────────────────────────────────────────

def test_default_engine_is_pymupdf(tmp_path):
    assert DEFAULT_ENGINE == "pymupdf" and docparse.engine.DEFAULT_ENGINE == "pymupdf"
    doc = pymupdf.open()
    put(new_page(doc), 40, 60, "Default engine")
    pdf = save(tmp_path, doc)
    with open_pdf(pdf) as opened:
        assert type(opened).__module__ == "docparse.engine.pymupdf_engine"
    assert main(["parse", str(pdf), "--out", str(tmp_path / "plain")]) == 0
    assert main(["parse", str(pdf), "--out", str(tmp_path / "named"), "--engine", "pymupdf"]) == 0
    assert (tmp_path / "plain" / "document.json").read_bytes() == (tmp_path / "named" / "document.json").read_bytes()


def test_unknown_engine_is_refused(tmp_path):
    with pytest.raises(ValueError):
        open_pdf(tmp_path / "x.pdf", "ghostscript")


def test_rules_do_not_import_pymupdf():
    """분해 규칙은 이음매만 쓴다. pymupdf 를 직접 부르는 것은 엔진 하나와, PDF 를 고쳐 쓰는 도구 둘뿐이다."""
    root = Path(docparse.engine.__file__).resolve().parents[1]
    imports = re.compile(r"^\s*(import|from)\s+(pymupdf|fitz)\b", re.M)
    assert imports.search("import fitz") and imports.search("    from pymupdf import Rect") and not imports.search("# import pymupdf")
    users = sorted(p.name for p in root.glob("*.py") if imports.search(p.read_text(encoding="utf-8")))
    assert users == ["placecheck.py", "tagpages.py"]
    assert [p.name for p in (root / "engine").glob("*.py") if imports.search(p.read_text(encoding="utf-8"))] == ["pymupdf_engine.py"]


def test_pdfium_path_runs_without_pymupdf(tmp_path):
    """pymupdf 를 import 할 수 없게 막은 프로세스에서 pdfium 엔진의 명령들이 끝까지 돈다."""
    doc = pymupdf.open()
    page = new_page(doc)
    put(page, 40, 60, "Blocked import 12.5")
    page.draw_rect(pymupdf.Rect(40, 100, 60, 180), fill=(0.9, 0.5, 0.1), color=None)
    pdf = save(tmp_path, doc)
    out = tmp_path / "out"
    script = (
        "import sys\n"
        "sys.modules['pymupdf'] = None\nsys.modules['fitz'] = None\n"
        "from docparse.cli import main\n"
        f"pdf, out = {str(pdf)!r}, {str(out)!r}\n"
        "codes = [main(['parse', pdf, '--out', out, '--places', '--render', '--dpi', '50', '--engine', 'pdfium']),\n"
        "         main(['charts', pdf, '--document', out + '/document.json', '--out', out, '--engine', 'pdfium']),\n"
        "         main(['chartcands', pdf, '--document', out + '/document.json', '--out', out, '--engine', 'pdfium']),\n"
        "         main(['tagtargets', pdf, '--document', out + '/document.json', '--out', out, '--engine', 'pdfium']),\n"
        "         main(['tagtext', pdf, '--tags', out + '/tags.json', '--pages', '1', '--out', out, '--engine', 'pdfium'])]\n"
        "assert 'pymupdf' not in [k for k, v in sys.modules.items() if v is not None], 'pymupdf 가 불려 왔다'\n"
        "sys.exit(max(codes))\n"
    )
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=Path(docparse.engine.__file__).resolve().parents[2])
    assert done.returncode == 0, done.stderr[-2000:]
    model = json.loads((out / "document.json").read_text(encoding="utf-8"))
    assert "Blocked import 12.5" in " ".join(b.get("text", "") for b in model["blocks"])
    assert (out / "pages" / "p0001.png").stat().st_size > 0
    # 막아 둔 채 기본 엔진(pymupdf)을 쓰면 바로 실패한다: 막기가 실제로 듣는다는 확인
    blocked = subprocess.run([sys.executable, "-c", script.replace("'--engine', 'pdfium'", "'--engine', 'pymupdf'")],
                             capture_output=True, text=True, cwd=Path(docparse.engine.__file__).resolve().parents[2])
    assert blocked.returncode != 0


# ── 글자, 줄, 스팬, 낱말 ─────────────────────────────────────────────────

def test_char_boxes_and_sizes_agree(tmp_path):
    doc = pymupdf.open()
    page = new_page(doc)
    put(page, 40, 60, "Alpha 12.5%", size=10)
    put(page, 40, 90, "한글 표 1,304", size=9, font=KOREAN)
    pdf = save(tmp_path, doc)

    def chars(p):
        return [(c["c"], tuple(c["bbox"]), s["size"]) for b in p.get_text_blocks(chars=True) for line in b["lines"] for s in line["spans"] for c in s["chars"]]
    got = read(pdf, chars)
    assert [c[0] for c in got["pymupdf"]] == [c[0] for c in got["pdfium"]] == list("Alpha 12.5%한글 표 1,304")
    assert_boxes_near([c[1] for c in got["pymupdf"]], [c[1] for c in got["pdfium"]])
    assert [c[2] for c in got["pymupdf"]] == [c[2] for c in got["pdfium"]]
    assert {c[2] for c in got["pdfium"]} == {10.0, 9.0}


def test_gaps_make_spaces_and_line_breaks_the_same_way(tmp_path):
    """글자 사이의 틈: 0.1em 은 붙고, 0.3em 은 공백 하나, 0.5em 은 공백이 따로 스팬, 0.9em 은 새 줄. 한글 뒤의 틈에는 공백을 넣지 않는다."""
    # 틈은 그 글 「뒤」의 것이다: AB·0.1·CD·0.3·EF·0.5·GH·0.9·IJ
    doc = pymupdf.open()
    page = new_page(doc)
    x = 40
    for text, gap in (("AB", 0.1), ("CD", 0.3), ("EF", 0.5), ("GH", 0.9), ("IJ", 0.0)):
        put(page, x, 60, text)
        x += width_of(text) + gap * 10
    put(page, 40, 100, "가나", font=KOREAN)
    put(page, 40 + width_of("가나", font="cjk") + 4, 100, "다라", font=KOREAN)   # 0.4em 뒤: 같은 줄, 공백 없음
    put(page, 40, 140, "Mm")
    put(page, 40 + width_of("Mm"), 140 - 3, "2", size=6)                           # 올려 쓴 작은 글자: 같은 줄, 다른 스팬
    put(page, 40, 180, "Q")
    put(page, 40.4, 180, "Q")                                                      # 같은 글자를 0.04em 옆에 또: 한 번만
    put(page, 395, 220, "ZZZZ")                                                    # 쪽 밖으로 나가는 글자는 빠진다
    pdf = save(tmp_path, doc)
    got = read(pdf, lines_of)
    assert got["pymupdf"] == got["pdfium"]
    flat = ["".join(t for t, _ in line) for line in got["pdfium"]]
    assert flat == ["ABCD EF GH", "IJ", "가나다라", "Mm2", "Q", "Z"]
    assert got["pdfium"][0] == [("ABCD EF", 10.0), (" ", 10.0), ("GH", 10.0)]
    assert got["pdfium"][3] == [("Mm", 10.0), ("2", 6.0)]


def test_words_agree(tmp_path):
    doc = pymupdf.open()
    page = new_page(doc)
    put(page, 40, 60, "Total 1,304 tCO2e (2024)")
    put(page, 40, 90, "합계 12.5 %", font=KOREAN)
    put(page, 200, 90, "따로", font=KOREAN)
    pdf = save(tmp_path, doc)
    got = read(pdf, words_of)
    assert [w[0] for w in got["pymupdf"]] == [w[0] for w in got["pdfium"]] == ["Total", "1,304", "tCO2e", "(2024)", "합계", "12.5", "%", "따로"]
    assert_boxes_near([w[1] for w in got["pymupdf"]], [w[1] for w in got["pdfium"]])
    numbering = read(pdf, lambda p: [w[5:] for w in p.get_words()])
    assert [n[1:] for n in numbering["pymupdf"]] == [n[1:] for n in numbering["pdfium"]]  # 줄 번호와 낱말 번호 (블록 번호는 맞추지 않는다)


def test_unembedded_font_boxes_differ_in_height_only(tmp_path):
    """글꼴 파일이 PDF 에 없으면 엔진이 저마다 다른 글꼴로 대신한다: 가로 자리는 같고 상자의 높이가 다르다."""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((40, 60), "Plain 12", fontname="helv", fontsize=10)
    pdf = save(tmp_path, doc)
    got = read(pdf, words_of)
    for (_t, a), (_u, b) in zip(got["pymupdf"], got["pdfium"]):
        assert abs(a[0] - b[0]) <= NEAR and abs(a[2] - b[2]) <= NEAR
        assert abs((a[3] - a[1]) - (b[3] - b[1])) > 1.0


# ── 벡터 그림 ─────────────────────────────────────────────────────────────

def test_drawing_items_colors_and_widths_agree(tmp_path):
    doc = pymupdf.open()
    page = new_page(doc)
    page.draw_rect(pymupdf.Rect(40, 40, 90, 70), fill=(1, 0, 0), color=None)                       # 채운 네모
    page.draw_rect(pymupdf.Rect(100, 40, 150, 70), fill=None, color=(0, 0, 1), width=2)            # 테두리 네모
    page.draw_rect(pymupdf.Rect(160, 40, 210, 70), fill=(0, 1, 0), color=(0, 0, 0), width=0.5)     # 한 번에 채우고 긋기
    page.draw_rect(pymupdf.Rect(220, 40, 270, 70), fill=(1, 1, 0), color=None)                     # 따로 채우고
    page.draw_rect(pymupdf.Rect(220, 40, 270, 70), fill=None, color=(0, 0, 0), width=1)            # 같은 꼴로 따로 긋기: 한 항목
    page.draw_line((40, 100), (200, 100), color=(0.2, 0.2, 0.2), width=0.25)                       # 선
    page.draw_polyline([(40, 150), (80, 120), (120, 140), (160, 110)], color=(1, 0, 1), width=1.5)  # 꺾은선
    page.draw_circle((60, 200), 4, fill=(0, 0, 1), color=None)                                     # 원 (곡선)
    pdf = save(tmp_path, doc)
    got = read(pdf, drawings_of)
    assert len(got["pymupdf"]) == len(got["pdfium"]) == 7
    for a, b in zip(got["pymupdf"], got["pdfium"]):
        assert (a[0], a[2], a[3], a[4]) == (b[0], b[2], b[3], b[4])
        assert all(abs(u - v) <= NEAR for u, v in zip(a[1], b[1]))
    kinds = [d[0] for d in got["pdfium"]]
    assert kinds[:5] == ["r", "r", "r", "r", "l"] and kinds[5] == "lll" and set(kinds[6]) == {"c"}
    assert got["pdfium"][2][2:] == ("#00ff00", "#000000", 0.5) and got["pdfium"][3][2:] == ("#ffff00", "#000000", 1.0)


def test_closed_four_line_outline_is_read_differently(tmp_path):
    """알려진 차이: 선 넷으로 처음 점까지 돌아온 뒤 닫은 윤곽선(`m l l l l h S`).

    pdfium 은 경로 닫기를 「처음 점으로 가는 선」으로 적어서 `re`·`m l l l h` 와 `m l l l l h` 가 같은 점 목록이 된다.
    pdfium 엔진은 앞의 것(흔한 쪽)으로 읽는다. 그래서 뒤의 꼴은 pymupdf 에서 사각 `qu`, pdfium 에서 선 넷이다.
    """
    doc = pymupdf.open()
    page = new_page(doc)
    page.draw_quad(pymupdf.Quad((120, 190), (160, 195), (118, 220), (158, 225)), color=(0, 0, 0), width=1)
    pdf = save(tmp_path, doc)
    got = read(pdf, drawings_of)
    assert [d[0] for d in got["pymupdf"]] == ["q"] and [d[0] for d in got["pdfium"]] == ["llll"]
    assert_boxes_near([d[1] for d in got["pymupdf"]], [d[1] for d in got["pdfium"]])


def test_image_boxes_agree(tmp_path):
    doc = pymupdf.open()
    page = new_page(doc)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 30, 20))
    pix.clear_with(180)
    page.insert_image(pymupdf.Rect(50, 60, 250, 180), pixmap=pix, keep_proportion=False)
    pdf = save(tmp_path, doc)
    got = read(pdf, lambda p: p.get_image_boxes())
    assert len(got["pdfium"]) == 1
    assert_boxes_near(got["pymupdf"], got["pdfium"])
    for name in ENGINES:
        assert parse_pdf(pdf, engine=name)["pages"][0]["image_coverage"] == 0.2


# ── 쪽의 좌표: CropBox, 회전, 폼 ────────────────────────────────────────

def _geometry_page(doc):
    page = new_page(doc)
    put(page, 120, 150, "Corner 42")
    page.draw_rect(pymupdf.Rect(110, 160, 180, 200), fill=(0, 0.5, 1), color=None)
    return page


@pytest.mark.parametrize("crop, rotation", [(None, 0), ((50, 40, 350, 260), 0), (None, 90), ((50, 40, 350, 260), 270)])
def test_cropbox_and_rotation_agree(tmp_path, crop, rotation):
    doc = pymupdf.open()
    page = _geometry_page(doc)
    if crop:
        page.set_cropbox(pymupdf.Rect(crop))
    page.set_rotation(rotation)
    pdf = save(tmp_path, doc)
    rects = read(pdf, lambda p: tuple(p.rect))
    assert rects["pymupdf"] == rects["pdfium"]
    words = read(pdf, words_of)
    assert [w[0] for w in words["pdfium"]] == ["Corner", "42"]
    assert_boxes_near([w[1] for w in words["pymupdf"]], [w[1] for w in words["pdfium"]])
    drawings = read(pdf, drawings_of)
    assert_boxes_near([d[1] for d in drawings["pymupdf"]], [d[1] for d in drawings["pdfium"]])
    left, top = (crop[0], crop[1]) if crop else (0, 0)
    assert abs(drawings["pdfium"][0][1][0] - (110 - left)) <= NEAR and abs(drawings["pdfium"][0][1][1] - (160 - top)) <= NEAR  # y 는 위에서 아래로


def test_text_and_paths_inside_a_form_agree(tmp_path):
    """다른 쪽을 줄여 붙인 폼(XObject): 그 안의 글자·경로는 폼의 행렬을 곱해야 제자리고, 글자 크기에도 배율이 든다."""
    src = pymupdf.open()
    _geometry_page(src)
    doc = pymupdf.open()
    page = new_page(doc)
    page.show_pdf_page(pymupdf.Rect(100, 50, 300, 200), src, 0)  # 400×300 을 200×150 으로: 배율 0.5, 옮김 (100, 50)
    pdf = save(tmp_path, doc)
    words = read(pdf, words_of)
    assert [w[0] for w in words["pdfium"]] == ["Corner", "42"]
    assert_boxes_near([w[1] for w in words["pymupdf"]], [w[1] for w in words["pdfium"]])
    assert abs(words["pdfium"][0][1][0] - (100 + 120 * 0.5)) <= NEAR
    sizes = read(pdf, lambda p: [s["size"] for b in p.get_text_blocks() for line in b["lines"] for s in line["spans"]])
    assert sizes["pymupdf"] == sizes["pdfium"] == [5.0]
    drawings = read(pdf, drawings_of)
    assert [d[0] for d in drawings["pymupdf"]] == [d[0] for d in drawings["pdfium"]] == ["r"]
    assert_boxes_near([d[1] for d in drawings["pymupdf"]], [d[1] for d in drawings["pdfium"]])
    assert_boxes_near([drawings["pdfium"][0][1]], [(155.0, 130.0, 190.0, 150.0)])


# ── 문서: 책갈피, 암호, 렌더 ─────────────────────────────────────────────

def test_outline_agrees(tmp_path):
    doc = pymupdf.open()
    for title in ("One", "Two", "Three"):
        put(new_page(doc), 40, 60, title)
    doc.set_toc([[1, "첫 장", 1], [2, "첫 절", 2], [1, "둘째 장", 3]])
    pdf = save(tmp_path, doc)
    tocs = {}
    for name in ENGINES:
        with open_pdf(pdf, name) as opened:
            tocs[name] = opened.get_toc()
            assert len(opened) == 3
    assert tocs["pymupdf"] == tocs["pdfium"] == [[1, "첫 장", 1], [2, "첫 절", 2], [1, "둘째 장", 3]]
    assert parse_pdf(pdf, engine="pymupdf")["sections"] == parse_pdf(pdf, engine="pdfium")["sections"]


def test_password_protected_pdf_is_refused_by_both(tmp_path):
    doc = pymupdf.open()
    put(new_page(doc), 40, 60, "Secret")
    pdf = save(tmp_path, doc, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="user", owner_pw="owner")
    for name in ENGINES:
        with pytest.raises(ParseError, match="암호"):
            parse_pdf(pdf, engine=name)


def test_broken_file_is_a_parse_error_in_both(tmp_path):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf at all")
    for name in ENGINES:
        with pytest.raises(ParseError):
            parse_pdf(bad, engine=name)


def test_render_writes_a_png_of_the_same_size(tmp_path):
    doc = pymupdf.open()
    _geometry_page(doc)
    pdf = save(tmp_path, doc)
    sizes = {}
    for name in ENGINES:
        assert main(["render", str(pdf), "--out", str(tmp_path / name), "--dpi", "72", "--engine", name]) == 0
        data = (tmp_path / name / "p0001.png").read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        sizes[name] = struct.unpack(">II", data[16:24])
        r, g, b = pymupdf.Pixmap(str(tmp_path / name / "p0001.png")).pixel(145, 180)[:3]  # 채운 네모의 자리가 푸르다
        assert b > 200 and r < 60, (name, r, g, b)
    assert sizes["pymupdf"] == sizes["pdfium"] == (400, 300)


def test_document_models_agree_on_a_synthetic_table(tmp_path, synthetic_ruled_page):
    """괘선 표 하나짜리 쪽: 두 엔진의 문서 모델이 글자까지 같다 (글꼴을 심지 않아 상자의 세로만 다르다)."""
    pdf = save(tmp_path, synthetic_ruled_page.parent, "ruled.pdf")
    a, b = parse_pdf(pdf, engine="pymupdf"), parse_pdf(pdf, engine="pdfium")
    assert [t["cells"] for t in a["tables"]] == [t["cells"] for t in b["tables"]]
    assert [(t["method"], t["status"], t["header"]) for t in a["tables"]] == [(t["method"], t["status"], t["header"]) for t in b["tables"]]
    assert a["pages"] == b["pages"] and a["sections"] == b["sections"]


# ── 보강 (검증 지적): 다시 세우는 규칙 하나하나를 pymupdf 엔진과 나란히 ──────────

def spans_of(page):
    return [[s["text"] for s in line["spans"]] for block in page.get_text_blocks() for line in block["lines"]]


def test_space_threshold_and_overprint(tmp_path):
    """틈 0.2em 은 공백 하나(0.15 와 0.25 사이). 같은 글자를 0.09em 옆에 또 찍으면 한 번만.

    (0.2em 옆에 또 찍은 것은 pymupdf 가 둘 다 내고 pdfium 은 스스로 하나를 버린다. 알려진 차이라 여기서 견주지 않는다.)
    """
    doc = pymupdf.open()
    page = new_page(doc)
    put(page, 40, 60, "AB")
    put(page, 40 + width_of("AB") + 2.0, 60, "CD")
    put(page, 40, 100, "Q")
    put(page, 40.9, 100, "Q")
    put(page, 40, 140, "Q")
    put(page, 40.5, 140, "Q", font=KOREAN)                           # 글꼴이 달라도 같은 글자면 버린다 (pdfium 은 스스로 버리지 않는다)
    xref = page.get_contents()[-1]
    doc.update_stream(xref, doc.xref_stream(xref) + b"\nq 200 200 50 50 re W n Q\n260 200 30 30 re n\n")   # 클립만 하는 경로, 칠하지 않는 경로
    pdf = save(tmp_path, doc)
    got = read(pdf, lines_of)
    assert ["".join(t for t, _ in line) for line in got["pymupdf"]] == ["".join(t for t, _ in line) for line in got["pdfium"]] == ["AB CD", "Q", "Q"]
    drawings = read(pdf, drawings_of)
    assert drawings["pymupdf"] == drawings["pdfium"] == []


def test_spans_split_on_raised_glyph_and_color(tmp_path):
    doc = pymupdf.open()
    page = new_page(doc)
    put(page, 40, 60, "Mm")
    put(page, 40 + width_of("Mm"), 60 - 3, "x")                      # 같은 크기, 올려 쓴 글자
    put(page, 40, 100, "Mm")
    put(page, 40 + width_of("Mm"), 100 + 3, "x")                     # 내려 쓴 글자는 가르지 않는다
    put(page, 40, 140, "Red")
    page.insert_text((40 + width_of("Red"), 140), "Blue", fontname=LATIN, fontsize=10, color=(0, 0, 1))
    pdf = save(tmp_path, doc)
    got = read(pdf, spans_of)
    assert got["pymupdf"] == got["pdfium"] == [["Mm", "x"], ["Mmx"], ["Red", "Blue"]]


def test_line_direction_and_content_order(tmp_path):
    doc = pymupdf.open()
    page = new_page(doc)
    put(page, 200, 60, "World")                                      # 내용 스트림에서 먼저, 자리는 오른쪽
    put(page, 40, 60, "Hello")
    page.insert_text((100, 250), "Up", fontname=LATIN, fontsize=10, rotate=90)
    pdf = save(tmp_path, doc)
    got = read(pdf, lambda p: [("".join(s["text"] for s in line["spans"]), tuple(round(v, 3) for v in line["dir"]))
                               for b in p.get_text_blocks() for line in b["lines"]])
    assert got["pymupdf"] == got["pdfium"] == [("World", (1.0, 0.0)), ("Hello", (1.0, 0.0)), ("Up", (0.0, -1.0))]
    words = read(pdf, words_of)
    assert_boxes_near([w[1] for w in words["pymupdf"]], [w[1] for w in words["pdfium"]])


def test_glyph_left_of_origin_and_nbsp_and_hyphen(tmp_path):
    doc = pymupdf.open()
    page = new_page(doc)
    put(page, 40, 60, "jar jig")                                     # j 는 글리프가 원점보다 왼쪽으로 나온다
    put(page, 40, 100, "A\u00a0B")                                   # 줄 바꾸지 않는 공백도 낱말을 가른다
    put(page, 40, 140, "Self-")                                      # 줄 끝의 붙임표
    put(page, 40, 152, "made text")
    pdf = save(tmp_path, doc)
    words = read(pdf, words_of)
    assert [w[0] for w in words["pymupdf"]] == [w[0] for w in words["pdfium"]] == ["jar", "jig", "A", "B", "Self-", "made", "text"]
    assert_boxes_near([w[1] for w in words["pymupdf"]], [w[1] for w in words["pdfium"]])


def test_whitespace_only_object_is_restored(tmp_path):
    """공백 하나만 든 글 객체: pdfium 은 글자로 내지 않는다. 한글 사이에서는 틈으로 공백을 짓지도 않으므로 되살려야 낱말이 갈린다."""
    doc = pymupdf.open()
    page = new_page(doc)
    x = 40
    for text in ("가나", " ", "다라"):
        put(page, x, 60, text, font=KOREAN)
        x += width_of(text, font="cjk")
    pdf = save(tmp_path, doc)
    got = read(pdf, lambda p: [w[4] for w in p.get_words()])
    assert got["pymupdf"] == got["pdfium"] == ["가나", "다라"]
    lines = read(pdf, lines_of)
    assert ["".join(t for t, _ in line) for line in lines["pdfium"]] == ["".join(t for t, _ in line) for line in lines["pymupdf"]] == ["가나 다라"]


def test_run_of_spaces_at_object_end_keeps_the_line(tmp_path):
    """글 객체 끝의 잇단 공백: pdfium 은 하나만 남긴다. 빠진 공백의 자리만큼 펜을 옮기지 않으면 다음 글이 새 줄로 끊긴다."""
    doc = pymupdf.open()
    page = new_page(doc)
    head = "가나" + " " * 8
    put(page, 40, 60, head, font=KOREAN)
    put(page, 40 + width_of(head, font="cjk"), 60, "다라", font=KOREAN)
    pdf = save(tmp_path, doc)
    lines = read(pdf, lambda p: [re.sub(r" +", " ", "".join(s["text"] for s in line["spans"])) for b in p.get_text_blocks() for line in b["lines"]])
    assert lines["pymupdf"] == lines["pdfium"] == ["가나 다라"]
    words = read(pdf, lambda p: [(w[4], w[6]) for w in p.get_words()])
    assert words["pymupdf"] == words["pdfium"] == [("가나", 0), ("다라", 0)]


def test_stroke_quad_width_scale_opacity_and_curve_box(tmp_path):
    src = pymupdf.open()
    inner = new_page(src)
    inner.draw_rect(pymupdf.Rect(40, 40, 140, 100), fill=None, color=(0, 0, 0), width=3)
    doc = pymupdf.open()
    page = new_page(doc)
    page.show_pdf_page(pymupdf.Rect(0, 0, 200, 150), src, 0)                                   # 배율 0.5: 굵기 3 → 1.5
    page.draw_polyline([(220, 40), (300, 50), (290, 100), (215, 90), (220, 40)], color=(0, 0, 0), width=1)   # 닫지 않고 처음 점으로: 사각
    page.draw_rect(pymupdf.Rect(40, 180, 90, 220), fill=(1, 0, 0), color=None, fill_opacity=0)   # 보이지 않는 채움
    page.draw_bezier((120, 200), (140, 150), (180, 260), (200, 200), color=(0, 0, 1), width=1)   # 제어점이 끝점 밖
    pdf = save(tmp_path, doc)
    got = read(pdf, drawings_of)
    assert [d[0] for d in got["pymupdf"]] == [d[0] for d in got["pdfium"]] == ["r", "q", "r", "c"]
    assert [d[4] for d in got["pymupdf"]] == [d[4] for d in got["pdfium"]] == [1.5, 1.0, None, 1.0]
    assert_boxes_near([d[1] for d in got["pymupdf"]], [d[1] for d in got["pdfium"]])
    assert abs(got["pdfium"][3][1][1] - 150) <= NEAR and abs(got["pdfium"][3][1][3] - 260) <= NEAR
    opacity = read(pdf, lambda p: [d.get("fill_opacity") for d in p.get_drawings()])
    assert opacity["pymupdf"] == opacity["pdfium"] == [None, None, 0.0, None]


def test_image_inside_a_form_and_text_blocks_without_images(tmp_path):
    src = pymupdf.open()
    inner = new_page(src)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 30, 20))
    pix.clear_with(120)
    inner.insert_image(pymupdf.Rect(100, 100, 300, 200), pixmap=pix, keep_proportion=False)
    put(inner, 40, 60, "Caption")
    doc = pymupdf.open()
    page = new_page(doc)
    page.show_pdf_page(pymupdf.Rect(100, 50, 300, 200), src, 0)
    pdf = save(tmp_path, doc)
    boxes = read(pdf, lambda p: p.get_image_boxes())
    assert_boxes_near(boxes["pymupdf"], boxes["pdfium"])
    assert_boxes_near(boxes["pdfium"], [(150.0, 100.0, 250.0, 150.0)])
    blocks = read(pdf, lambda p: p.get_text_blocks())
    for name in ENGINES:   # 글 블록만 온다 (그림 블록이 섞이지 않는다)
        assert len(blocks[name]) == 1 and all("lines" in b for b in blocks[name])


def test_render_clip_gives_the_same_size(tmp_path):
    from docparse.render import render_page
    doc = pymupdf.open()
    _geometry_page(doc)
    pdf = save(tmp_path, doc)
    for name in ENGINES:
        out = render_page(pdf, 1, tmp_path / f"{name}.png", dpi=72, clip=(100, 150, 200, 210), engine=name)
        data = out.read_bytes()
        assert struct.unpack(">II", data[16:24]) == (100, 60), name
        r, g, b = pymupdf.Pixmap(str(out)).pixel(40, 30)[:3]        # 잘라 낸 그림의 (40, 30) = 쪽의 (140, 180): 푸른 네모 안
        assert b > 200 and r < 60, (name, r, g, b)


def test_score_and_render_commands_pass_the_engine_on(tmp_path, monkeypatch):
    import docparse.cli as cli
    seen = {}
    monkeypatch.setattr(cli, "score_gold", lambda gold, strategy, engine=None: seen.setdefault("score", engine) and [] or [])
    monkeypatch.setattr(cli, "format_report", lambda results, title: "")
    monkeypatch.setattr(cli, "aggregate", lambda results, key: {"ALL": {}})
    monkeypatch.setattr(cli, "render_pages", lambda pdf, out, pages=None, dpi=200, engine=None: seen.setdefault("render", engine) and [] or [])
    assert main(["score", "--gold", str(tmp_path), "--strategy", "auto", "--engine", "pdfium"]) == 0
    assert main(["render", str(tmp_path / "x.pdf"), "--out", str(tmp_path), "--engine", "pdfium"]) == 0
    assert seen == {"score": "pdfium", "render": "pdfium"}


# ── 다시 쓰기(밴드 PD1)에서 재서 찾은 것: 명세에 없던 동작을 pymupdf 엔진과 나란히 ──────────

def add_content(doc, page, more):
    xref = page.get_contents()[-1]
    doc.update_stream(xref, doc.xref_stream(xref) + b"\n" + more + b"\n")


def test_wide_space_splits_just_above_three_tenths(tmp_path):
    """지어 넣은 공백이 따로 스팬이 되는 틈은 0.3em 을 넘을 때다 (0.25 는 한 스팬, 0.35 는 따로)."""
    doc = pymupdf.open()
    page = new_page(doc)
    for y, gap in ((60, 0.25), (100, 0.35)):
        put(page, 40, y, "AB")
        put(page, 40 + width_of("AB") + gap * 10, y, "CD")
    pdf = save(tmp_path, doc)
    got = read(pdf, spans_of)
    assert got["pymupdf"] == got["pdfium"] == [["AB CD"], ["AB", " ", "CD"]]


def test_blocks_follow_indent_and_list_markers(tmp_path):
    """한 줄 아래(1.1em)의 줄: 앞 줄의 머리로 돌아오면 같은 블록, 들여 쓰면 새 블록. 앞 줄이 번호·글머리표로 시작하면 들여 써도 같은 블록."""
    doc = pymupdf.open()
    page = new_page(doc)
    put(page, 40, 40, "Head")
    put(page, 40, 51, "back")          # 머리로 돌아온 줄: 같은 블록의 둘째 줄
    put(page, 40, 100, "Head")
    put(page, 60, 111, "inset")        # 들여 쓴 줄: 새 블록
    put(page, 40, 160, "1 Item")
    put(page, 60, 171, "inset")        # 번호로 시작한 줄 아래: 같은 블록
    put(page, 40, 220, "• Item")
    put(page, 60, 231, "inset")        # 글머리표
    put(page, 40, 260, "- Item")
    put(page, 60, 271, "inset")        # 붙임표는 글머리표로 치지 않는다
    pdf = save(tmp_path, doc)
    shape = read(pdf, lambda p: [["".join(s["text"] for s in line["spans"]) for line in b["lines"]] for b in p.get_text_blocks()])
    assert shape["pymupdf"] == shape["pdfium"] == [["Head", "back"], ["Head"], ["inset"], ["1 Item", "inset"], ["• Item", "inset"], ["- Item"], ["inset"]]
    numbering = read(pdf, lambda p: [(w[4], w[6], w[7]) for w in p.get_words()])
    assert numbering["pymupdf"] == numbering["pdfium"]
    assert ("back", 1, 0) in numbering["pdfium"] and numbering["pdfium"].count(("inset", 1, 0)) == 2


def test_flat_curves_implicit_subpaths_and_in_place_lines(tmp_path):
    doc = pymupdf.open()
    page = new_page(doc)
    put(page, 300, 280, "x")
    add_content(doc, page, b"100 100 m 100 100 150 130 150 130 c S\n"                 # 제어점이 양 끝점에 겹친 곡선은 선이다
                           b"200 100 m 200 100 250 100 250 100 c 250 100 250 130 250 130 c 250 130 200 130 200 130 c h f\n"  # 그런 곡선 셋 + 닫기: 네모
                           b"100 200 m 150 200 l h 150 220 l 100 220 l h S\n"         # 닫은 뒤 옮김 없이 이어 그은 것은 닫아도 선이 더해지지 않는다
                           b"200 200 m 250 200 l 250 220 l 200 200 m 200 200 l h S\n"  # 옮김 바로 뒤의 제자리 선은 남는다
                           b"300 100 50 0 re f 300 150 0 30 re S\n")                   # 높이·너비가 없는 네모도 네모다
    pdf = save(tmp_path, doc)
    got = read(pdf, drawings_of)
    assert got["pymupdf"] == got["pdfium"]
    assert [d[0] for d in got["pdfium"]] == ["l", "r", "llll", "lll", "r", "r"]


def test_shading_box_is_grown_to_the_pixel_grid(tmp_path):
    """음영으로 칠한 자리의 그림 상자: 바깥쪽 정수 눈금으로. 눈금을 살짝 넘은 변도 다음 눈금으로 간다. 칠할 자리가 없으면 (0, 0, 0, 0)."""
    doc = pymupdf.open()
    page = new_page(doc)
    put(page, 300, 280, "x")
    kind, value = doc.xref_get_key(page.xref, "Resources")
    holder, key = (int(value.split()[0]), "Shading") if kind == "xref" else (page.xref, "Resources/Shading")
    doc.xref_set_key(holder, key, "<< /Sh0 << /ShadingType 2 /ColorSpace /DeviceRGB /Coords [0 0 400 0] "
                     "/Function << /FunctionType 2 /Domain [0 1] /C0 [1 0 0] /C1 [0 0 1] /N 1 >> /Extend [true true] >> >>")
    add_content(doc, page, b"q 1 0 0 -1 0 300 cm 47.07 76.11 6.84 6.93 re W n /Sh0 sh Q\n"      # (47.07, 76.11, 53.91, 83.04)
                           b"q 1 0 0 -1 0 300 cm 109.9999 120 20.0002 20 re W n /Sh0 sh Q\n"    # 눈금을 0.0001 넘음
                           b"q 10 20 30 30 re W n 200 200 5 5 re W n /Sh0 sh Q\n")              # 겹치지 않는 클립 둘: 빈 자리
    pdf = save(tmp_path, doc)
    got = read(pdf, lambda p: p.get_image_boxes())
    assert got["pymupdf"] == got["pdfium"] == [(47.0, 76.0, 54.0, 84.0), (109.0, 120.0, 131.0, 140.0), (0.0, 0.0, 0.0, 0.0)]


def test_outline_empty_title_and_missing_target(tmp_path):
    doc = pymupdf.open()
    for _ in range(3):
        new_page(doc)
    doc.set_toc([[1, "장", 1], [2, "", 2], [1, "밖", -1], [2, "웹", 1, {"kind": pymupdf.LINK_URI, "uri": "https://example.com"}], [1, "끝", 3]])
    pdf = save(tmp_path, doc)
    tocs = {}
    for name in ENGINES:
        with open_pdf(pdf, name) as opened:
            tocs[name] = opened.get_toc()
    assert tocs["pymupdf"] == tocs["pdfium"] == [[1, "장", 1], [2, " ", 2], [1, "밖", -1], [2, "웹", -1], [1, "끝", 3]]


# ── 재검증 지적: 경계를 못 박는다 (기준은 pymupdf 엔진을 돌린 결과) ─────────────

def test_wide_space_boundary_lies_between_three_tenths_and_one_third(tmp_path):
    """지어 넣은 공백이 따로 스팬이 되는 틈: 0.29em 은 한 스팬, 0.32em 은 따로다 (문턱이 1/3 이면 0.32 가 한 스팬으로 남는다)."""
    doc = pymupdf.open()
    page = new_page(doc)
    put(page, 40, 60, "AB")
    put(page, 40 + width_of("AB") + 2.9, 60, "CD")
    put(page, 40, 100, "AB")
    put(page, 40 + width_of("AB") + 3.2, 100, "CD")
    pdf = save(tmp_path, doc)
    got = read(pdf, spans_of)
    assert got["pymupdf"] == got["pdfium"] == [["AB CD"], ["AB", " ", "CD"]]


def test_slightly_tilted_text_stays_on_the_line_and_more_tilt_breaks_it(tmp_path):
    """앞 글에 바로 이어 2도 기운 글은 같은 줄, 10도 기운 글은 새 줄이다."""
    doc = pymupdf.open()
    page = new_page(doc)
    for y, degrees in ((60, 2), (120, 10)):
        put(page, 40, y, "AB")
        at = pymupdf.Point(40 + width_of("AB"), y)
        page.insert_text(at, "CD", fontname=LATIN, fontsize=10, morph=(at, pymupdf.Matrix(degrees)))
    pdf = save(tmp_path, doc)
    got = read(pdf, lambda p: ["".join(s["text"] for s in line["spans"]) for b in p.get_text_blocks() for line in b["lines"]])
    assert got["pymupdf"] == got["pdfium"] == ["ABCD", "AB", "CD"]


def test_closed_rectangle_starting_with_a_vertical_side_is_not_a_rect_item(tmp_path):
    """닫힌 직사각형이라도 세로 변부터 그은 것은 네모 항목이 아니라 선 넷이다 (가로 변부터 그은 것은 네모)."""
    doc = pymupdf.open()
    page = new_page(doc)
    page.draw_polyline([(100, 200), (100, 170), (150, 170), (150, 200)], closePath=True, fill=(0, 1, 0), color=None)   # 세로 먼저
    page.draw_polyline([(200, 200), (250, 200), (250, 170), (200, 170)], closePath=True, fill=(0, 1, 0), color=None)   # 가로 먼저
    pdf = save(tmp_path, doc)
    got = read(pdf, drawings_of)
    assert [d[0] for d in got["pymupdf"]] == [d[0] for d in got["pdfium"]] == ["llll", "r"]
    assert_boxes_near([d[1] for d in got["pymupdf"]], [d[1] for d in got["pdfium"]])
