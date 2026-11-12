"""PyMuPDF 엔진. 이음매를 두기 전의 호출을 그대로 옮긴 것이다 (플래그, 차례, 숫자 그대로)."""
import pymupdf

from .types import PdfOpenError, Point, Rect

_TEXT_FLAGS = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES


class Page:
    def __init__(self, page):
        self._page = page
        self.number = page.number

    @property
    def rect(self):
        return Rect(self._page.rect)

    def get_text_blocks(self, chars=False):
        return self._page.get_text("rawdict" if chars else "dict", flags=_TEXT_FLAGS)["blocks"]

    def get_words(self):
        return self._page.get_text("words")

    def get_drawings(self):
        out = []
        for d in self._page.get_drawings():
            items = []
            for it in d["items"]:
                if it[0] == "re":
                    items.append(("re", Rect(it[1])))
                elif it[0] == "qu":
                    items.append(("qu", Rect(it[1].rect)))
                else:
                    items.append((it[0],) + tuple(Point(p.x, p.y) for p in it[1:]))
            entry = {"items": items, "rect": Rect(d["rect"]), "color": d.get("color"), "fill": d.get("fill"),
                     "width": d.get("width")}
            if "fill_opacity" in d:
                entry["fill_opacity"] = d["fill_opacity"]
            out.append(entry)
        return out

    def get_image_boxes(self):
        return [tuple(img["bbox"]) for img in self._page.get_image_info()]

    def render_png(self, path, dpi, clip=None):
        self._page.get_pixmap(dpi=dpi, clip=pymupdf.Rect(clip) if clip else None).save(path)


class Document:
    def __init__(self, doc):
        self._doc = doc

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self._doc.close()

    def __len__(self):
        return len(self._doc)

    def __getitem__(self, index):
        return Page(self._doc[index])

    @property
    def needs_pass(self):
        return bool(self._doc.needs_pass)

    def get_toc(self):
        return self._doc.get_toc()


def open_document(path):
    try:
        return Document(pymupdf.open(path))
    except Exception as e:  # noqa: BLE001
        raise PdfOpenError(str(e)) from e
