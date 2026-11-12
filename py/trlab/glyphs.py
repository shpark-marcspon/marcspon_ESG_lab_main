"""TRX2 G0 — PDF 글자 속성(크기·굵기·색·자리)을 layout 2 블록에 붙인다. AI 0.

글자는 pypdfium2 raw 로 읽는다(새 의존 0, pymupdf 쓰지 않음):
- 실효 크기 = FPDFText_GetFontSize × sqrt(|a·d − b·c|) (글 행렬, docparse pdfium_engine 와 같은 셈)
- 굵기 = FPDFText_GetFontWeight ≥ 600. weight ≤ 0(기술자 없는 표준 글꼴)일 때만 글꼴 이름 정규식
- 색 = FPDFText_GetFillColor, 자리 = FPDFText_GetLooseCharBox 를 왼쪽 위 원점(y 아래)으로 옮긴 것
- pdfium 이 지어낸 글자(글 객체 없음, 또는 코드 ≤ 32 이고 IsGenerated)와 공백(코드 ≤ 32)은 뺀다
글자 상자 가운데가 블록 bbox(1pt 넓힘) 안이면 그 블록, 둘 이상이면 넓이가 작은 블록.

경로: TRX2_OUT(산출, 기본 메인 체크아웃 esg-lab/out/trx2), TRX2_GOLD(정답·books.json, prework/gold-trx2).
명령: python -m trlab.glyphs <키> <lang> → OUT2/attrs/<키>-<lang>.json (자기 점검: 블록 최빈 크기 대 document.json font_size)
"""

import ctypes
import json
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as C

from trlab import common
from trlab.common import load_doc

ROOT = common.ROOT
OUT2 = Path(os.environ.get("TRX2_OUT", ROOT / "esg-lab" / "out" / "trx2"))
GOLD2 = Path(os.environ.get("TRX2_GOLD", ROOT / "prework" / "gold-trx2"))

BOLD_NAME = re.compile(r"(?i)(bold|black|heavy|bd\b|cbd|extrab|semib|demi)")
BOLD_W = 600
COLOR_DIFF = 60
SIZE_TOL = 0.15


# ---------- 경로 ----------
def books():
    return json.load(open(GOLD2 / "books.json"))


def pdf_path(key, lang):
    b = books()
    return ROOT / b.get("dir", "prework/reports") / b[key][lang]


def parse_path(key, lang):
    p = OUT2 / "parse" / f"{key}-{lang}" / "document.json"
    return p if p.exists() else common.OUT / "parse" / f"{key}-{lang}" / "document.json"


def load_blocks(key, lang):
    return load_doc(parse_path(key, lang))


# ---------- 글자 읽기 ----------
def is_bold(weight, font):
    if weight > 0:
        return weight >= BOLD_W
    return bool(BOLD_NAME.search(font or ""))


def page_chars(page):
    """쪽 하나의 글자 [(x, y, size, weight, font, (r,g,b), code)] — x·y 는 상자 가운데, 쪽 좌표(pt, 왼쪽 위 원점)."""
    H = page.get_height()
    tp = page.get_textpage()
    raw = tp.raw
    out = []
    try:
        n = C.FPDFText_CountChars(raw)
        rect, mat = C.FS_RECTF(), C.FS_MATRIX()
        r, g, b, a = (ctypes.c_uint() for _ in range(4))
        name, flags = ctypes.create_string_buffer(256), ctypes.c_int()
        styles = {}
        for i in range(n):
            obj = C.FPDFText_GetTextObject(raw, i)
            if not obj:
                continue  # 지어낸 글자
            code = C.FPDFText_GetUnicode(raw, i)
            if code <= 32:
                continue  # 지어낸 공백·줄바꿈과 PDF 의 공백 — 속성 셈에 넣지 않는다
            key = ctypes.addressof(obj.contents)
            st = styles.get(key)
            if st is None:
                C.FPDFText_GetMatrix(raw, i, mat)
                fs = C.FPDFText_GetFontSize(raw, i)
                size = fs * math.sqrt(abs(mat.a * mat.d - mat.b * mat.c))
                weight = C.FPDFText_GetFontWeight(raw, i)
                ln = C.FPDFText_GetFontInfo(raw, i, name, 256, flags)
                font = name.value.decode("utf-8", "replace").split("+", 1)[-1] if ln else ""
                C.FPDFText_GetFillColor(raw, i, r, g, b, a)
                st = styles[key] = (round(size, 2), int(weight), font, (r.value, g.value, b.value))
            C.FPDFText_GetLooseCharBox(raw, i, rect)
            x = (rect.left + rect.right) / 2
            y = H - (rect.top + rect.bottom) / 2  # PDF 사용자 공간(y 위) → 왼쪽 위 원점
            out.append((x, y, *st, code))
    finally:
        tp.close()
    return out


def assign(chars, blocks, pad=1.0):
    """글자 → 블록 id(없으면 None). 둘 이상이면 넓이가 작은 블록."""
    bx = sorted(((b.bbox[2] - b.bbox[0]) * (b.bbox[3] - b.bbox[1]), b.id, b.bbox) for b in blocks)
    res = []
    for ch in chars:
        x, y = ch[0], ch[1]
        hit = None
        for _, bid, (x0, y0, x1, y1) in bx:
            if x0 - pad <= x <= x1 + pad and y0 - pad <= y <= y1 + pad:
                hit = bid
                break
        res.append(hit)
    return res


def _rgb_hex(c):
    return "#%02x%02x%02x" % tuple(int(v) & 255 for v in c)


def _dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _big_text(chs):
    """표 안 가장 큰 크기 글자들의 줄 글(같은 y 줄끼리, 왼쪽부터)."""
    if not chs:
        return ""
    top = max(c[2] for c in chs)
    big = sorted((c for c in chs if c[2] >= top - 0.3), key=lambda c: (round(c[1] / max(1.0, top * 0.6)), c[0]))
    lines, cur, last = [], [], None
    for c in big:
        row = round(c[1] / max(1.0, top * 0.6))
        if last is not None and row != last:
            lines.append(cur)
            cur = []
        cur.append(c)
        last = row
    lines.append(cur)
    return " ".join("".join(chr(c[6]) for c in ln) for ln in lines)


def block_attrs(chs, base, ph, bbox):
    """블록 하나의 속성. chs = [(x, y, size, weight, font, rgb, code)]"""
    if not chs:
        return {"status": "no_chars", "n": 0, "pos": _pos(bbox, ph)}
    n = len(chs)
    sizes = Counter(round(c[2], 1) for c in chs)
    bold = sum(is_bold(c[3], c[4]) for c in chs)
    heavier = sum(c[3] > base["weight"] for c in chs)
    col = Counter(c[5] for c in chs).most_common(1)[0][0]
    return {"status": "ok", "n": n, "size_max": round(max(c[2] for c in chs), 1),
            "size_mode": sizes.most_common(1)[0][0], "weight_max": max(c[3] for c in chs),
            "bold_share": round(bold / n, 3), "heavier": round(heavier / n, 3), "color": _rgb_hex(col),
            "color_diff": _dist(col, base["rgb"]) > COLOR_DIFF, "pos": _pos(bbox, ph)}


def _pos(bbox, ph):
    t = bbox[1] / ph
    return "상" if t < 0.33 else "중" if t < 0.66 else "하"


def run(key, lang, pdf=None, doc_path=None):
    doc_path = Path(doc_path or parse_path(key, lang))
    doc = json.load(open(doc_path))
    blocks = load_doc(doc_path)
    pinfo = {p["page"]: p for p in doc["pages"]}
    fsize = {b["id"]: float(b.get("font_size") or 0) for b in doc["blocks"]}
    by_page = {}
    for b in blocks:
        by_page.setdefault(b.page, []).append(b)
    pdf = pdfium.PdfDocument(str(pdf or pdf_path(key, lang)))
    per_block, pages = {}, {}
    try:
        for pno in sorted(pinfo):
            page = pdf[pno - 1]
            w, h = page.get_size()
            pi = pinfo[pno]
            st = "ok"
            if abs(w - pi["width"]) > 0.5 or abs(h - pi["height"]) > 0.5:
                st = "size_mismatch"
            chars = page_chars(page) if st == "ok" else []
            if st == "ok" and not chars:
                st = "no_text"
            pb = by_page.get(pno, [])
            owner = assign(chars, pb)
            for ch, bid in zip(chars, owner):
                if bid is not None:
                    per_block.setdefault(bid, []).append(ch)
            pages[pno] = {"status": st, "chars": len(chars), "unassigned": sum(o is None for o in owner),
                          "image_coverage": pi.get("image_coverage", 0.0), "height": pi["height"]}
            page.close()
    finally:
        pdf.close()
    allc = [c for v in per_block.values() for c in v]
    base = {"size": Counter(round(c[2], 1) for c in allc).most_common(1)[0][0] if allc else 0.0,
            "weight": Counter(c[3] for c in allc).most_common(1)[0][0] if allc else 0,
            "rgb": Counter(c[5] for c in allc).most_common(1)[0][0] if allc else (0, 0, 0)}
    attrs = {}
    for b in blocks:
        chs = per_block.get(b.id, [])
        a = block_attrs(chs, base, b.ph, b.bbox)
        if b.kind == "table" and chs:
            a["big_text"] = _big_text(chs)
        attrs[b.id] = a
    # 자기 점검(★3): 글 블록의 최빈 크기 대 document.json font_size. docparse 의 font_size 는 블록 첫 조각의 크기라
    # (굵은 머리말 + 본문 꼴 블록에서 최빈과 갈림) 첫 글자 크기·블록 안 어떤 글자 크기와의 일치도 같이 센다.
    tb = [b for b in blocks if b.kind != "table" and attrs[b.id]["status"] == "ok" and fsize.get(b.id)]
    chk = [abs(attrs[b.id]["size_mode"] - fsize[b.id]) <= SIZE_TOL for b in tb]
    chk_first = [abs(per_block[b.id][0][2] - fsize[b.id]) <= SIZE_TOL for b in tb]
    chk_any = [any(abs(c[2] - fsize[b.id]) <= SIZE_TOL for c in per_block[b.id]) for b in tb]
    unas = [p["unassigned"] / p["chars"] for p in pages.values() if p["chars"]]
    return {"book": key, "lang": lang,
            "base": {"size": base["size"], "weight": base["weight"], "color": _rgb_hex(base["rgb"])},
            "check": {"size_match": round(sum(chk) / max(1, len(chk)), 4), "blocks": len(chk),
                      "size_match_first": round(sum(chk_first) / max(1, len(tb)), 4),
                      "size_match_any": round(sum(chk_any) / max(1, len(tb)), 4),
                      "unassigned_mean": round(sum(unas) / max(1, len(unas)), 4),
                      "pages": dict(Counter(p["status"] for p in pages.values())),
                      "no_chars_blocks": sum(a["status"] == "no_chars" for a in attrs.values())},
            "pages": {str(k): {x: v for x, v in p.items() if x != "height"} for k, p in pages.items()},
            "blocks": attrs}


def load_attrs(key, lang):
    return json.load(open(OUT2 / "attrs" / f"{key}-{lang}.json"))


def main(argv):
    key, lang = argv[0], argv[1]
    res = run(key, lang)
    (OUT2 / "attrs").mkdir(parents=True, exist_ok=True)
    p = OUT2 / "attrs" / f"{key}-{lang}.json"
    json.dump(res, open(p, "w"), ensure_ascii=False, indent=1, sort_keys=True)
    print(key, lang, "base", res["base"], "check", json.dumps(res["check"], ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
