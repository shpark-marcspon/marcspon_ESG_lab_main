"""TRX4 K3 — 머리글·꼬리말 띠의 바뀜과 강조. AI 0.

band_words(chars, H, top): 위 8%(또는 아래 8%) 띠 글자 → 같은 (round x, round y, code) 중복 제거(겹쳐 찍힌 글자) →
  줄(|Δy| ≤ 3pt) · 낱말(앞 글자와 x 틈 > 1.6 × 크기, 또는 스타일(크기·굵기·색)이 바뀜) → [(글, 크기, bold, rgb, 줄, y)].
  크기는 0.5pt 로 반올림.
emphasized(words): 같은 크기이고 붙은 줄(앞 줄과 Δy ≤ 2 × 크기)끼리의 묶음에 낱말 ≥ 3 이면 (bold, rgb) 다수와 다른
  낱말들(소수 스타일 = 지금 장·절 표시). 다수 스타일은 ≥ 2 이고 둘째보다 많아야. 발주서의 「(줄, 크기) 묶음」을 붙은 줄로 넓힘 — kb en 탐색 띠가 두 줄(반쯤 본).
running(blocks, p): 가장자리 블록(region header/footer 또는 위끝 < 8% · 아래끝 > 92%, 표 제외) 글에서 숫자·공백을 뗀 것 ≥ 3자.
scan(key, lang) → 쪽마다 {texts, emph, kind: none|text|emph|both} + 경계 목록 [(p, 글, src)] — p−1 에 없던 글/강조가 p 에 나타남.
attach(items, boundaries): 항목 차례대로, 앞 항목 닻 이상인 가장 작은 경계 쪽 중 글이 맞는 것(공백 제거 뒤
  fuzz.ratio ≥ 85, 국문 항목은 경계 글의 lang-part) → anchor[i] | None.
DP bonus = 닻 쪽 후보 +K3_W, 닻 쪽이 간지면 다음 쪽 후보도.

명령: python -m trlab.runhead <키> <lang> → OUT4/runhead/<키>-<lang>.json
"""

import json
import re
import sys
from collections import Counter

from rapidfuzz import fuzz

EDGE = 0.08
LINE_DY = 3.0
WORD_GAP = 1.6
GROUP_DY = 2.0
MIN_GROUP = 3
MATCH = 85
K3_W = 0.4


def _bold(c):
    from trlab.glyphs import is_bold
    return is_bold(c[3], c[4])


def dedupe(chars):
    seen, out = set(), []
    for c in chars:
        k = (round(c[0]), round(c[1]), c[-1])
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


def band_words(chars, H, top=True, edge=EDGE):
    """→ [(글, 크기, bold, rgb, 줄 번호, y)]"""
    band = [c for c in chars if (c[1] < edge * H if top else c[1] > (1 - edge) * H)]
    band = sorted(dedupe(band), key=lambda c: (c[1], c[0]))
    lines, cur = [], []
    for c in band:
        if cur and abs(c[1] - cur[0][1]) > LINE_DY:
            lines.append(cur)
            cur = []
        cur.append(c)
    if cur:
        lines.append(cur)
    words = []
    for li, ln in enumerate(lines):
        ln = sorted(ln, key=lambda c: c[0])
        w = []
        for c in ln:
            if w and (c[0] - w[-1][0] > WORD_GAP * max(c[2], w[-1][2]) or _style(c) != _style(w[-1])):
                words.append(_word(w, li))
                w = []
            w.append(c)
        if w:
            words.append(_word(w, li))
    return words


def _half(x):
    return round(x * 2) / 2


def _style(c):
    return (_half(c[2]), _bold(c), tuple(c[5]))


def _word(cs, li):
    size = Counter(_half(c[2]) for c in cs).most_common(1)[0][0]
    bold = sum(_bold(c) for c in cs) * 2 >= len(cs)
    rgb = Counter(tuple(c[5]) for c in cs).most_common(1)[0][0]
    return ("".join(chr(c[-1]) for c in cs), size, bold, rgb, li, sum(c[1] for c in cs) / len(cs))


def groups_of(words):
    """같은 크기 · 붙은 줄(앞 줄과 Δy ≤ GROUP_DY × 크기)끼리 묶음."""
    by = {}
    for w in words:
        by.setdefault(w[1], []).append(w)
    out = []
    for size, ws in by.items():
        ws = sorted(ws, key=lambda w: (w[4], w[0]))
        cur, last_y = [], None
        for w in ws:
            y = w[5] if len(w) > 5 else w[4] * LINE_DY * 10
            if cur and last_y is not None and abs(y - last_y) > GROUP_DY * size and w[4] != cur[-1][4]:
                out.append(cur)
                cur = []
            cur.append(w)
            last_y = y
        if cur:
            out.append(cur)
    return out


def emphasized(words):
    """같은 크기·붙은 줄 묶음에 낱말 ≥ MIN_GROUP 이면 (bold, rgb) 다수와 다른 낱말들의 글."""
    out = []
    for g in groups_of(words):
        if len(g) < MIN_GROUP:
            continue
        cnt = Counter((w[2], w[3]) for w in g).most_common()
        if cnt[0][1] < 2 or (len(cnt) > 1 and cnt[1][1] == cnt[0][1]):
            continue  # 다수 스타일이 뚜렷하지 않으면 강조 없음(kb ko 6쪽 — 셋이 다 다른 색)
        major = cnt[0][0]
        out += [w[0] for w in g if (w[2], w[3]) != major]
    return out


def norm(t):
    return re.sub(r"[\s\d]+", "", t or "")


def running(blocks, p):
    out = []
    for b in blocks:
        if b.page != p or b.kind == "table":
            continue
        edge = b.bbox[1] < EDGE * b.ph or b.bbox[3] > (1 - EDGE) * b.ph
        if b.region not in ("header", "footer") and not edge:
            continue
        t = norm(b.text)
        if len(t) >= 3:
            out.append(t)
    return out


def page_record(blocks, p, chars, H):
    texts = running(blocks, p)
    emph = [norm(w) for w in emphasized(band_words(chars, H, True)) + emphasized(band_words(chars, H, False))]
    emph = [e for e in emph if len(e) >= 2]
    kind = "both" if texts and emph else "text" if texts else "emph" if emph else "none"
    return {"texts": texts, "emph": emph, "kind": kind}


def boundaries(pages):
    """pages = {p: record} → [(p, 글, src)] — p−1 에 없던 글/강조."""
    out = []
    for p in sorted(pages):
        prev = pages.get(p - 1, {"texts": [], "emph": []})
        for src in ("emph", "texts"):
            for t in dict.fromkeys(pages[p][src]):
                if t not in prev[src]:
                    out.append((p, t, src))
    return out


def attach(items, bounds):
    """→ anchor[i] | None. 차례 지킴(앞 닻 이상), 공백 제거 뒤 fuzz.ratio ≥ MATCH(국문 항목은 경계 글의 lang-part)."""
    from trlab.toc import _lang_part
    out, last = [], 0
    for it in items:
        t = re.sub(r"^\[[^\]]*\]\s*", "", it["text"])
        hit = None
        for p, bt, _ in sorted(bounds, key=lambda x: x[0]):
            if p < last:
                continue
            part = _lang_part(t, bt)
            if fuzz.ratio(norm(t).lower(), norm(part).lower()) >= MATCH:
                hit = p
                break
        out.append(hit)
        if hit is not None:
            last = hit
    return out


def bonus_of(items, anchors, cand, divs, w=K3_W):
    out = []
    for i, a in enumerate(anchors):
        b = {}
        if a is not None:
            pages = {a} | ({a + 1} if a in divs else set())
            for bid, r in cand["rows"].items():
                if r["page"] in pages:
                    b[bid] = w
        out.append(b)
    return out


def scan(key, lang, blocks=None):
    """→ {"pages": {p: record}, "bounds": [...]} (OUT4/runhead 에 있으면 읽기)."""
    from trlab import tx4_pick as X
    p = X.OUT4 / "runhead" / f"{key}-{lang}.json"
    if p.exists():
        return json.load(open(p))
    import pypdfium2 as pdfium

    from trlab import glyphs
    from trlab.common import load_doc
    blocks = blocks or load_doc(X._need("parse", f"{key}-{lang}"))
    npages = max(b.page for b in blocks)
    pdf = pdfium.PdfDocument(str(glyphs.pdf_path(key, lang)))
    pages = {}
    try:
        for q in range(1, npages + 1):
            pg = pdf[q - 1]
            pages[q] = page_record(blocks, q, glyphs.page_chars(pg), pg.get_height())
            pg.close()
    finally:
        pdf.close()
    out = {"book": "-".join((key, lang)), "pages": {str(k): v for k, v in pages.items()}, "bounds": boundaries(pages)}
    p.parent.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(p, "w"), ensure_ascii=False, indent=1, sort_keys=True)
    return json.load(open(p))


def book_bonus(P):
    """prep4 용 — scan → attach → bonus. → {anchors, bonus, n_bounds}"""
    sc = scan(P["key"], P["lang"], P["blocks"])
    bounds = [tuple(b) for b in sc["bounds"]]
    anchors = attach(P["items"], bounds)
    divs = {int(p) for p, v in P["cand"]["pages"].items() if v["divider"]}
    return {"anchors": anchors, "bonus": bonus_of(P["items"], anchors, P["cand"], divs), "n_bounds": len(bounds)}


def main(argv):
    key, lang = argv[0], argv[1]
    sc = scan(key, lang)
    kinds = Counter(v["kind"] for v in sc["pages"].values())
    print(key, lang, "쪽", len(sc["pages"]), dict(kinds), "경계", len(sc["bounds"]))


if __name__ == "__main__":
    main(sys.argv[1:])
