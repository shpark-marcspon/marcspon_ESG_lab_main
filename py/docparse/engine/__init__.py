"""PDF 엔진 이음매. 분해기의 PDF 읽기는 전부 여기를 지난다.

엔진 둘: `pymupdf`(기본, PyMuPDF) 와 `pdfium`(pypdfium2). 엔진 모듈은 고를 때 처음 불러온다:
`pdfium` 을 고르면 pymupdf 를 import 하지 않는다. 주고받는 자료의 꼴은 `types.py`.
"""
from importlib import import_module

from .types import PdfOpenError, Point, Rect

DEFAULT_ENGINE = "pymupdf"
ENGINES = ("pymupdf", "pdfium")
_MODULES = {"pymupdf": ".pymupdf_engine", "pdfium": ".pdfium_engine"}


def open_pdf(path, engine=None):
    """PDF 를 열어 엔진의 문서 객체를 돌려준다 (`with` 로 닫는다). 열지 못하면 `PdfOpenError`."""
    name = engine or DEFAULT_ENGINE
    if name not in _MODULES:
        raise ValueError(f"알 수 없는 엔진: {name} ({', '.join(ENGINES)} 가운데 하나)")
    return import_module(_MODULES[name], __name__).open_document(path)


def add_engine_argument(parser):
    parser.add_argument("--engine", choices=ENGINES, default=DEFAULT_ENGINE,
                        help=f"PDF 를 읽는 엔진 (기본 {DEFAULT_ENGINE})")


__all__ = ["DEFAULT_ENGINE", "ENGINES", "PdfOpenError", "Point", "Rect", "open_pdf", "add_engine_argument"]
