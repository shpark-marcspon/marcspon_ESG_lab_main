"""국·영 블록 짝(align). AI 0.

1. 쪽 짝: 같은 PDF 쪽(TRX1 세 책은 쪽 수가 같고, 책마다 10쪽을 눈으로 봐 어긋남 0 — 보고 「공통 바닥」).
2. 쪽 안 블록 짝: 본문 띠(region=body) 블록끼리 점수
   = 자리(정규화 중심 거리) 0.45 + 닻 겹침(숫자·라틴 낱말 Jaccard) 0.30 + 읽는 차례 0.15 + 같은 kind 0.10,
   길이 비(영/국 글자 수)가 터무니없으면 깎는다. 점수 높은 것부터 1:1 로 잡고, 남은 블록은
   바로 이웃(차례 ±1)이 이미 짝이고 자리가 가까우면 그 짝에 붙인다(1:n).
3. 표는 표끼리만. 같은 모양(행·열)이면 칸 짝(cells)도 낸다.

명령: python -m trlab.align [sam cos kb] → OUT/align/<키>.json, 덮음률 요약을 찍는다.
"""

import json
import sys
from collections import defaultdict

from trlab.common import BOOKS, OUT, Block, load

TH = 0.42  # 1:1 짝 문턱(sam 50짝 표본으로 정함)


def _jacc(a, b):
    if not a and not b:
        return None
    return len(a & b) / len(a | b)


def score(k: Block, e: Block, rk: float, re_: float) -> float:
    if (k.kind == "table") != (e.kind == "table"):
        return 0.0
    d = ((k.cx - e.cx) ** 2 + (k.cy - e.cy) ** 2) ** 0.5
    pos = max(0.0, 1 - d / 0.25)
    j = _jacc(k.nums() | k.latin(), e.nums() | e.latin())
    anchor = 0.5 if j is None else j
    rank = 1 - min(1.0, abs(rk - re_) * 2)
    kind = 1.0 if k.kind == e.kind else 0.5
    s = 0.45 * pos + 0.30 * anchor + 0.15 * rank + 0.10 * kind
    lk, le = max(1, len(k.text)), max(1, len(e.text))
    ratio = le / lk
    if k.kind != "table" and (ratio < 0.4 or ratio > 8):
        s *= 0.6
    return s


def chrome_ids(blocks, share=0.2):
    """쪽마다 같은 자리에 되풀이되는 글(탐색 띠·장 이름) — layout 2 가 body 로 남긴 것. 쪽의 share 이상에 나오면 띠."""
    from collections import Counter
    pages = {b.page for b in blocks}
    sig = lambda b: (b.text.strip(), round(b.cx, 2), round(b.cy, 2))
    c = Counter(sig(b) for b in blocks)
    return {b.id for b in blocks if c[sig(b)] >= max(3, share * len(pages))}


_CHROME = {}


def body(blocks):
    key = id(blocks)
    if key not in _CHROME:
        _CHROME[key] = chrome_ids(blocks)
    ch = _CHROME[key]
    return [b for b in blocks if b.region == "body" and b.text.strip() and b.id not in ch]


def align_page(ko, en):
    """한 쪽의 짝 목록. 짝 = {'ko':[id..], 'en':[id..], 'score':s, 'cells':[...]}"""
    if not ko or not en:
        return []
    rk = {b.id: i / max(1, len(ko) - 1) for i, b in enumerate(ko)}
    re_ = {b.id: i / max(1, len(en) - 1) for i, b in enumerate(en)}
    cand = []
    for k in ko:
        for e in en:
            s = score(k, e, rk[k.id], re_[e.id])
            if s >= TH:
                cand.append((s, k.id, e.id))
    cand.sort(reverse=True)
    used_k, used_e, pairs = set(), set(), {}
    for s, a, b in cand:
        if a in used_k or b in used_e:
            continue
        used_k.add(a)
        used_e.add(b)
        pairs[a] = {"ko": [a], "en": [b], "score": round(s, 3)}
    # 1:n — 남은 블록을 이웃 짝에 붙인다
    kb = {b.id: b for b in ko}
    eb = {b.id: b for b in en}
    e2pair = {p["en"][0]: p for p in pairs.values()}

    def near(x: Block, y: Block):
        return ((x.cx - y.cx) ** 2 + (x.cy - y.cy) ** 2) ** 0.5 < 0.12 and x.kind != "table" and y.kind != "table"

    for side, blocks, owner in (("en", en, e2pair), ("ko", ko, pairs)):
        for i, b in enumerate(blocks):
            if b.id in (used_e if side == "en" else used_k):
                continue
            for j in (i - 1, i + 1):
                if 0 <= j < len(blocks) and blocks[j].id in owner and near(b, blocks[j]):
                    owner[blocks[j].id][side].append(b.id)
                    (used_e if side == "en" else used_k).add(b.id)
                    break
    out = []
    for p in pairs.values():
        a, b = kb[p["ko"][0]], eb[p["en"][0]]
        if a.kind == "table" and len(a.rows) == len(b.rows) and all(len(x) == len(y) for x, y in zip(a.rows, b.rows)):
            p["cells"] = [[r, c] for r in range(len(a.rows)) for c in range(len(a.rows[r])) if a.rows[r][c] and b.rows[r][c]]
        out.append(p)
    return out


def align_book(key):
    ko, en = load(key, "ko"), load(key, "en")
    bp_k, bp_e = defaultdict(list), defaultdict(list)
    for b in body(ko):
        bp_k[b.page].append(b)
    for b in body(en):
        bp_e[b.page].append(b)
    pairs = []
    for p in sorted(set(bp_k) | set(bp_e)):
        for x in align_page(bp_k[p], bp_e[p]):
            x["page"] = p
            pairs.append(x)
    return ko, en, pairs


def coverage(blocks, pairs, side):
    bod = body(blocks)
    got = {i for p in pairs for i in p[side]}
    n = sum(1 for b in bod if b.id in got)
    c = sum(len(b.text) for b in bod if b.id in got)
    return {"blocks": len(bod), "paired": n, "cover": round(n / len(bod), 3),
            "cover_chars": round(c / max(1, sum(len(b.text) for b in bod)), 3)}


def load_pairs(key):
    return json.load(open(OUT / "align" / f"{key}.json"))["pairs"]


def main(argv):
    keys = argv or list(BOOKS)
    (OUT / "align").mkdir(parents=True, exist_ok=True)
    for key in keys:
        ko, en, pairs = align_book(key)
        res = {"book": key, "threshold": TH, "ko": coverage(ko, pairs, "ko"), "en": coverage(en, pairs, "en"),
               "pairs": pairs}
        json.dump(res, open(OUT / "align" / f"{key}.json", "w"), ensure_ascii=False, indent=1)
        print(key, "pairs", len(pairs), "ko", res["ko"], "en", res["en"])


if __name__ == "__main__":
    main(sys.argv[1:])
