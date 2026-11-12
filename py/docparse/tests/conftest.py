"""테스트 공용 도구. 합성(synthetic) PDF 는 pymupdf 로 그 자리에서 만든다. 실제 보고서의 값이 아니다.

PDF 를 읽는 테스트는 두 엔진으로 돈다: 모듈에 `pytestmark = pytest.mark.usefixtures("engine")` 를 두면 `engine` 이
기본 엔진을 pymupdf, pdfium 으로 차례로 바꿔 가며 같은 테스트를 돌린다. PDF 를 고쳐 쓰는 도구(tagpages, placecheck)의
테스트는 `@pytest.mark.pymupdf_only` 로 pymupdf 에서만 돈다 (그 도구는 pymupdf 에 남아 있다).
"""
import os
import tempfile

import pymupdf
import pytest

import docparse.engine
from docparse.config import reports_dir
from docparse.engine import ENGINES, open_pdf

FONT = "helv"
SIZE = 8


def put(page, x, y, text, size=SIZE, right=False):
    """(x, y) 를 기준선 왼쪽 끝(right=True 면 오른쪽 끝)으로 글자를 쓴다."""
    if right:
        x -= pymupdf.get_text_length(text, fontname=FONT, fontsize=size)
    page.insert_text((x, y), text, fontname=FONT, fontsize=size)


def hline(page, x0, x1, y, width=0.25):
    page.draw_line((x0, y), (x1, y), width=width)


def pytest_configure(config):
    config.addinivalue_line("markers", "pymupdf_only: PDF 를 고쳐 쓰는 도구의 테스트. pymupdf 엔진으로만 돈다")


def pytest_generate_tests(metafunc):
    if "engine" in metafunc.fixturenames:
        only = metafunc.definition.get_closest_marker("pymupdf_only")
        metafunc.parametrize("engine", ["pymupdf"] if only else list(ENGINES), indirect=True)


_REOPENED = []


@pytest.fixture
def engine(request, monkeypatch):
    """기본 엔진을 바꿔 놓는다. 테스트가 `--engine` 이나 `engine=` 을 주지 않아도 이 엔진으로 읽는다."""
    monkeypatch.setattr(docparse.engine, "DEFAULT_ENGINE", request.param)
    yield request.param
    while _REOPENED:
        doc, path = _REOPENED.pop()
        doc.close()
        os.unlink(path)


def seam(obj):
    """pymupdf 로 지은 쪽(또는 문서)을 지금 엔진의 쪽(문서)으로 다시 연다. 분해 규칙에는 이것을 건넨다."""
    if isinstance(obj, pymupdf.Page):
        doc, index = obj.parent, obj.number
    elif isinstance(obj, pymupdf.Document):
        doc, index = obj, None
    else:
        return obj
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(doc.tobytes())
    opened = open_pdf(f.name)
    _REOPENED.append((opened, f.name))
    return opened if index is None else opened[index]


@pytest.fixture
def synthetic_ruled_page():
    """세로선 없이 셀마다 가로 토막만 있는 합성 표. 0열은 두 행이 세로 병합(토막 없음)."""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    xs = [40, 120, 160, 230, 300]
    rows = [("Item", "Unit", "2023", "2024"), ("Alpha", "t", "1,200", "1,304"), ("", "kg", "15", "-"),
            ("Beta", "t", "7.5", "8.25")]
    y = 50
    for i, r in enumerate(rows):
        base = y + 10
        if r[0]:
            put(page, xs[0] + 2, base, r[0])
        put(page, xs[1] + 2, base, r[1])
        put(page, xs[3] - 2, base, r[2], right=True)
        put(page, xs[4] - 2, base, r[3], right=True)
        y += 14
        for j in range(4):
            if i == 1 and j == 0:
                continue  # Alpha 는 아래 행과 병합: 0열 토막을 그리지 않는다
            hline(page, xs[j], xs[j + 1], y)
    for j in range(4):
        hline(page, xs[j], xs[j + 1], 50, width=1.0)
    yield page
    doc.close()


@pytest.fixture
def synthetic_ruleless_page():
    """괘선이 전혀 없는 합성 표 두 개가 좌우로 나란히 있는 쪽."""
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=300)
    for ox, name in ((40, "Left"), (320, "Right")):
        rows = [(name, "", "2023", "2024", "2025"), ("Energy use", "GWh", "100", "110", "121"),
                ("Water", "kt", "5.5", "6.0", "6.5"), ("Waste recycled", "%", "90", "91", "-"),
                ("Emissions", "tCO2e", "1,000", "2,000", "3,000")]
        for i, r in enumerate(rows):
            base = 60 + 14 * i
            put(page, ox, base, r[0])
            if r[1]:
                put(page, ox + 90, base, r[1])
            for k, v in enumerate(r[2:]):
                put(page, ox + 170 + 40 * k, base, v, right=True)
    yield page
    doc.close()


def require_reports():
    d = reports_dir()
    if not d.is_dir():
        pytest.fail(f"원본 PDF 디렉터리가 없다: {d}. DOCPARSE_REPORTS_DIR 로 위치를 지정한다. "
                    "원본이 없으면 회귀 테스트는 「못 쟀다」이지 통과가 아니다.")
    return d
