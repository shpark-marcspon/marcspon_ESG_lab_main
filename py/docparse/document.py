"""PDF 하나를 문서 모델(dict)로 분해한다. 상태 없음: 입력은 파일 경로, 출력은 dict."""
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from . import __version__
from . import charplaces
from .blocks import body_font_size, build_text_blocks, classify
from .config import PLACES_SCHEMA_PATH, SCHEMA_PATH
from .engine import PdfOpenError, open_pdf
from .layout import page_chunks
from .reading_order import reading_order
from .sections import page_title, sections_from_outline, sections_from_titles
from .tables import extract_tables

SCHEMA_VERSION = "0.1"
MAX_RULE_SEGMENTS = 8000  # 이보다 괘선 토막이 많은 쪽(벡터 그림)은 괘선 기반을 건너뛰고 그 사실을 적는다


class ParseError(Exception):
    pass


def load_schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate(doc):
    """스키마 위반 메시지 목록 (없으면 빈 목록)."""
    v = Draft202012Validator(load_schema())
    return [f"{'/'.join(str(p) for p in e.absolute_path)}: {e.message}" for e in v.iter_errors(doc)]


def validate_places(places):
    """글자 자리(곁 파일)의 스키마 위반 메시지 목록."""
    v = Draft202012Validator(json.loads(PLACES_SCHEMA_PATH.read_text(encoding="utf-8")))
    return [f"{'/'.join(str(p) for p in e.absolute_path)}: {e.message}" for e in v.iter_errors(places)]


def _inside(c, box, pad=1.0):
    return box[0] - pad <= c.xc <= box[2] + pad and box[1] - pad <= c.yc <= box[3] + pad


def _image_coverage(page):
    area = 0.0
    for x0, y0, x1, y1 in page.get_image_boxes():
        area += max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return round(min(1.0, area / (page.rect.width * page.rect.height)), 3)


def _table_strategy_for(page, strategy):
    if strategy == "text":
        return strategy, None
    n = sum(len(d["items"]) for d in page.get_drawings())
    if n > MAX_RULE_SEGMENTS:
        return "text", f"lines_strategy_skipped:too_many_drawing_items:{n}"
    return strategy, None


def parse_pdf(path, pages=None, strategy="auto", dedupe_overdrawn=False, engine=None):
    """pages: 1부터 세는 쪽 번호 목록(None 이면 전 쪽).

    dedupe_overdrawn=True 면 같은 글자가 같은 자리에 또 그려진 것을 한 번만 뽑는다(`layout.overdrawn_spans`).
    버린 span 의 수는 쪽의 `notes` 에 `overdrawn_spans_dropped:<수>` 로 남는다. 기본은 끔이고 끄면 산출이 그대로다.
    """
    return _parse(path, pages, strategy, False, dedupe_overdrawn, engine)[0]


def parse_pdf_places(path, pages=None, strategy="auto", dedupe_overdrawn=False, engine=None):
    """(문서 모델, 글자 자리). 문서 모델은 `parse_pdf` 의 것과 같다. 글자 자리의 형식은 `schemas/char-places.schema.json`."""
    return _parse(path, pages, strategy, True, dedupe_overdrawn, engine)


def _parse(path, pages, strategy, want_places, dedupe_overdrawn=False, engine=None):
    path = Path(path)
    if not path.is_file():
        raise ParseError(f"파일이 없다: {path}")
    try:
        doc = open_pdf(path, engine)
    except PdfOpenError as e:
        raise ParseError(f"PDF 를 열 수 없다: {path}: {e}") from e
    with doc:
        if doc.needs_pass:
            raise ParseError(f"암호가 걸린 PDF 다: {path}")
        n = len(doc)
        wanted = list(pages) if pages else list(range(1, n + 1))
        bad = [p for p in wanted if not 1 <= p <= n]
        if bad:
            raise ParseError(f"쪽 번호가 범위 밖이다 (1~{n}): {bad}")
        out_pages, blocks, tables, titles = [], [], [], []
        per_page = []
        for pno in wanted:
            page = doc[pno - 1]
            chunks, dropped = page_chunks(page, glyphs=want_places, dedupe_overdrawn=dedupe_overdrawn)
            notes = [f"{k}:{v}" for k, v in dropped.items() if v]
            chars = sum(len(c.text.replace(" ", "")) for c in chunks)
            has_text = chars > 0
            page_tables = []
            if has_text:
                used, note = _table_strategy_for(page, strategy)
                if note:
                    notes.append(note)
                page_tables = extract_tables(page, used, chunks=chunks)
            rest = [c for c in chunks if not any(_inside(c, t.bbox) for t in page_tables)]
            text_blocks = build_text_blocks(rest)
            per_page.append((pno, page_tables, text_blocks))
            titles.append((pno, page_title(chunks, page.rect.height)))
            out_pages.append({
                "page": pno,
                "width": round(page.rect.width, 1),
                "height": round(page.rect.height, 1),
                # 텍스트 계층이 없는 쪽(스캔본)은 none 으로 표시만 한다. OCR 을 하지 않는다.
                "text_layer": "present" if has_text else "none",
                "char_count": chars,
                "image_coverage": _image_coverage(page),
                "columns": 0,
                "notes": notes,
            })
        toc = doc.get_toc()
        if toc:
            sections = sections_from_outline(toc, n)
        else:
            sections = sections_from_titles(titles)
        body = body_font_size([b for _p, _t, bs in per_page for b in bs])

        def section_of(pno):
            hit = [s for s in sections if s["page_start"] <= pno <= s["page_end"]]
            return max(hit, key=lambda s: (s["level"], s["page_start"]))["id"] if hit else None

        containers, unlinked = [], []

        def place(cid, kind, pno, text, chunks):
            if not want_places or not text:
                return
            made, failed = charplaces.build_container(cid, kind, pno, text, chunks)
            (containers if made else unlinked).append(made or failed)

        for (pno, page_tables, text_blocks), meta in zip(per_page, out_pages):
            items = [("table", t) for t in page_tables] + [("text", b) for b in text_blocks]
            boxes = [(it.bbox if kind == "table" else it["bbox"]) for kind, it in items]
            order, meta["columns"] = reading_order(boxes, meta["height"])
            t_no = 0
            for rank, idx in enumerate(order, 1):
                kind, it = items[idx]
                block = {"id": f"p{pno}-b{rank}", "page": pno, "order": rank, "section_id": section_of(pno)}
                if kind == "table":
                    t_no += 1
                    tid = f"p{pno}-t{t_no}"
                    block.update({"kind": "table", "bbox": it.bbox, "table_id": tid})
                    tables.append({
                        "id": tid, "page": pno, "bbox": it.bbox, "n_rows": it.n_rows, "n_cols": it.n_cols,
                        "cells": it.grid, "header": it.header, "method": it.method, "status": it.status,
                        "notes": it.notes, "footnote_marks": it.footnote_marks,
                    })
                    for i, row in enumerate(it.grid):
                        for j, text in enumerate(row):
                            place(f"{tid}:r{i}c{j}", "cell", pno, text, it.cell_chunks.get((i, j), []))
                else:
                    block.update({"kind": classify(it, body), "bbox": it["bbox"], "text": it["text"],
                                  "font_size": round(it["size"], 1)})
                    place(block["id"], "block", pno, it["text"], it["chunks"])
                blocks.append(block)
        source = {"file_name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "page_count": n}
        places = None
        if want_places:
            places = {"schema_version": charplaces.SCHEMA_VERSION, "source": source,
                      "parser": {"name": "docparse", "version": __version__, "table_strategy": strategy},
                      "unit": "utf16", "containers": containers, "unlinked": unlinked}
        return {
            "schema_version": SCHEMA_VERSION,
            "source": source,
            "parser": {"name": "docparse", "version": __version__, "table_strategy": strategy,
                       "llm_used": False, "ocr_used": False},
            "pages": out_pages,
            "sections": sections,
            "blocks": blocks,
            "tables": tables,
        }, places
