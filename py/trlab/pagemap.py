"""TRX3 H1 — 찍힌 쪽 번호 → PDF 쪽 지도. AI 0.

TRX1 toc.page_map 은 region == footer 블록만 읽어 번호가 머리글(쪽 위)에 찍힌 책에서 지도가 빈다. 여기서는
(가) region ∈ {header, footer} 이거나 쪽 가장자리(블록 위끝 < 쪽 높이 8% 또는 아래끝 > 92%)의 글 ≤ 9자 블록에서
    1~3자리 수(abs(n − 쪽) < 60) → 쪽마다 {printed:[…], src: block}(펼침면은 번호 둘).
(나) (가)로 덮인 쪽이 80% 아래인 책만 글자(glyphs.page_chars 꼴 [(x, y, size, …, code)])에서 가장자리 숫자 글자
    (크기 ≤ 1.3 × 본문)를 같은 줄·붙은 것끼리 이어 붙여 더한다(src: glyph).
(다) 번호 없는 쪽은 앞뒤 직접 쪽의 번호가 쪽 차이만큼 벌어져 있을 때만 보간(interp), 그 밖은 어긋남 최빈(offset).
찍힌 번호 하나가 여러 쪽에서 읽히면(그림 축의 수 등) 어긋남 최빈에 가장 가까운 쪽을 직접으로 둔다.

명령: python -m trlab.pagemap <키> <lang> → OUT3/pagemap/<키>-<lang>.json
"""

import json
import re
import sys
from collections import Counter

NUMRE = re.compile(r"(?<!\d)\d{1,3}(?!\d)")
EDGE = 0.08
COVER = 0.8
GLYPH_SIZE = 1.3


def _nums(text, page):
    t = text.strip()
    if len(t) > 9:
        return []
    return [int(n) for n in NUMRE.findall(t) if 0 < int(n) < 1000 and abs(int(n) - page) < 60]


def block_hits(blocks):
    """(가) → {pdf: [찍힌…]}"""
    hits = {}
    for b in blocks:
        if b.kind == "table":
            continue
        edge = b.bbox[1] < EDGE * b.ph or b.bbox[3] > (1 - EDGE) * b.ph
        if b.region not in ("header", "footer") and not edge:
            continue
        for n in _nums(b.text, b.page):
            hits.setdefault(b.page, [])
            if n not in hits[b.page]:
                hits[b.page].append(n)
    return hits


def glyph_hits(chars, heights, base):
    """(나) chars = {pdf: [(x, y, size, …, code)]}, heights = {pdf: 쪽 높이} → {pdf: [찍힌…]}"""
    hits = {}
    for p, chs in chars.items():
        H = heights[p]
        dig = sorted((c for c in chs if 48 <= c[-1] <= 57 and c[2] <= GLYPH_SIZE * base
                      and (c[1] < EDGE * H or c[1] > (1 - EDGE) * H)), key=lambda c: (round(c[1]), c[0]))
        runs, cur = [], []
        for c in dig:
            if cur and (abs(c[1] - cur[-1][1]) > 2 or c[0] - cur[-1][0] > 1.2 * c[2]):
                runs.append(cur)
                cur = []
            cur.append(c)
        if cur:
            runs.append(cur)
        for r in runs:
            s = "".join(chr(c[-1]) for c in r)
            if 1 <= len(s) <= 3 and 0 < int(s) < 1000 and abs(int(s) - p) < 60:
                hits.setdefault(p, [])
                if int(s) not in hits[p]:
                    hits[p].append(int(s))
    return hits


def build(blocks, chars=None, heights=None, base=None, npages=None):
    """→ {"pages": {pdf: {printed, src}}, "map": {찍힌: [pdf, how]}, "offset", "cover": {direct, interp, offset, none}}"""
    npages = npages or max(b.page for b in blocks)
    hits = block_hits(blocks)
    src = {p: "block" for p in hits}
    if chars is not None and len(hits) < COVER * npages:
        for p, ns in glyph_hits(chars, heights or {}, base or 1.0).items():
            if p not in hits:
                hits[p] = ns
                src[p] = "glyph"
    offs = Counter(p - n for p, ns in hits.items() for n in ns)
    off = offs.most_common(1)[0][0] if offs else 0
    seen = {}
    for p, ns in sorted(hits.items()):
        for n in ns:
            seen.setdefault(n, []).append(p)
    direct = {n: min(ps, key=lambda p: (abs((p - n) - off), p)) for n, ps in seen.items()}
    m = {n: [p, "direct"] for n, p in direct.items()}
    # (다) 보간 — 앞뒤 직접 쪽(번호 하나씩)의 번호 차이 = 쪽 차이일 때만
    single = sorted((p, ns[0]) for p, ns in hits.items() if len(ns) == 1 and direct.get(ns[0]) == p)
    interp_pages = {}
    for (p0, n0), (p1, n1) in zip(single, single[1:]):
        if p1 - p0 > 1 and n1 - n0 == p1 - p0:
            for k in range(1, p1 - p0):
                if (p0 + k) not in hits:
                    interp_pages[p0 + k] = n0 + k
    for p, n in interp_pages.items():
        if n not in m:
            m[n] = [p, "interp"]
    pages = {p: {"printed": sorted(ns), "src": src[p]} for p, ns in sorted(hits.items())}
    for p, n in interp_pages.items():
        pages[p] = {"printed": [n], "src": "interp"}
    cover = {"direct": len(hits), "interp": len(interp_pages), "offset": 0, "none": 0}
    rest = npages - len(hits) - len(interp_pages)
    cover["offset" if offs else "none"] = rest
    return {"pages": {str(k): v for k, v in sorted(pages.items())}, "map": {str(k): v for k, v in sorted(m.items())},
            "offset": off, "npages": npages, "cover": cover}


def to_pdf3(pm, printed):
    """→ (pdf 쪽 | None, how: direct | interp | offset | none)"""
    if printed is None:
        return None, "none"
    v = pm["map"].get(str(printed))
    if v:
        return v[0], v[1]
    if pm["cover"]["direct"]:
        return max(1, min(pm["npages"], printed + pm["offset"])), "offset"
    return max(1, min(pm["npages"], printed)), "none"


def window(pm, printed):
    p, how = to_pdf3(pm, printed)
    if p is None:
        return []
    if how == "direct":
        return [p]
    return [x for x in (p - 1, p, p + 1) if 1 <= x <= pm["npages"]]


def agree(pm, trx1_map):
    """TRX1 toc.page_map 의 {찍힌: pdf} 와 같은 키에서 일치율."""
    keys = [k for k in trx1_map if str(k) in pm["map"]]
    same = sum(pm["map"][str(k)][0] == trx1_map[k] for k in keys)
    return {"keys": len(keys), "same": same, "pct": round(100 * same / len(keys), 1) if keys else None}


def run(key, lang):
    import pypdfium2 as pdfium

    from trlab import glyphs
    from trlab.toc import page_map
    from trlab.tx3_pick import load_blocks3, load_json
    blocks = load_blocks3(key, lang)
    npages = max(b.page for b in blocks)
    pm = build(blocks, npages=npages)
    if pm["cover"]["direct"] < COVER * npages:
        attrs = load_json(key, lang, "attrs")
        pdf = pdfium.PdfDocument(str(glyphs.pdf_path(key, lang)))
        chars, heights = {}, {}
        try:
            for p in range(1, npages + 1):
                pg = pdf[p - 1]
                heights[p] = pg.get_height()
                chars[p] = glyphs.page_chars(pg)
                pg.close()
        finally:
            pdf.close()
        pm = build(blocks, chars, heights, attrs["base"]["size"], npages)
    t1, t1off = page_map(blocks)
    offs = Counter(p - n for p, v in pm["pages"].items() for n in v["printed"] for p in [int(p)] if v["src"] != "interp")
    pm.update(book=key, lang=lang, trx1={"pages": len(set(t1.values())), "offset": t1off, "agree": agree(pm, t1)},
              offsets=dict(sorted(Counter({str(k): v for k, v in offs.items()}).items())))
    return pm


def main(argv):
    from trlab.tx3_pick import OUT3
    key, lang = argv[0], argv[1]
    pm = run(key, lang)
    (OUT3 / "pagemap").mkdir(parents=True, exist_ok=True)
    json.dump(pm, open(OUT3 / "pagemap" / f"{key}-{lang}.json", "w"), ensure_ascii=False, indent=1, sort_keys=True)
    print(key, lang, "cover", pm["cover"], "offset", pm["offset"], "trx1", pm["trx1"])


if __name__ == "__main__":
    main(sys.argv[1:])
