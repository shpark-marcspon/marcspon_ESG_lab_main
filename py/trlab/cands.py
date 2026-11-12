"""TRX2 G0 — 글자 속성으로 좁힌 쪽마다의 제목 후보. AI 0. 규칙·문턱은 sam 으로만 정했다(간지 규칙은 발주서 판으로 얼림).

블록은 trlab.align.body()(본문 띠·되풀이 탐색 띠 거름) 위에서:
- rep: 같은 글(공백 접고 소문자)이 3쪽 이상 → 첫 쪽 것만 후보에(달린 제목)
- 후보 = 글 ≤ 120자이고 (가) size_max ≥ 1.15 × 본문 크기 (나) bold_share ≥ 0.6 이고 (pos 상 또는 color_diff)
  (다) document.json kind == heading. 쪽마다 점수(크기 순위 0.5 · 상 0.2 · 굵음 0.15 · 색 다름 0.15) 상위 K
- 간지 쪽 = (본문 글 블록 ≤ 4 이고 글자 합 < 250) 또는 (image_coverage ≥ 0.5 이고 글자 합 < 400)
  → 그 쪽 size_max 가 가장 큰 블록 하나(표 포함)를 반드시 후보에
- no_text 쪽은 후보 0

후보 줄: id | 쪽 | 위치 | 크기pt | ×본문 | 굵음% | 색다름 | 되풀이 | 글 60자
명령: python -m trlab.cands <키> <lang> → OUT2/cands/<키>-<lang>.json (+ 정답 있으면 후보 재현율)
"""

import json
import re
import statistics
import sys
from collections import Counter, defaultdict

from trlab.align import body
from trlab.glyphs import GOLD2, OUT2, load_attrs, load_blocks

K = 6
BIG = 1.15
BOLD = 0.6
MAXLEN = 120


def norm(t):
    return re.sub(r"\s+", " ", t).strip().lower()


def rep_ids(bod, A=None):
    """3쪽 이상 되풀이되는 글 블록 → {id: 거를 것이면 True}. 이어진 쪽 줄(run)의 첫 쪽은 남긴다 — 같은 절 제목이
    떨어진 두 곳(부문별 같은 이름 절)에 나오면 둘 다 시작이다. 줄은 같은 글 + 같은 크기(속성이 있으면)로 잇는다 —
    앞 쪽의 작은 같은 낱말(본문 표의 칸)이 다음 쪽 큰 제목을 「둘째 쪽」으로 만들지 않게(sam 영문으로 고침)."""
    A = A or {}
    sig = lambda b: (norm(b.text), round(A.get(b.id, {}).get("size_max", 0)))
    pages, by_text = defaultdict(set), defaultdict(set)
    for b in bod:
        pages[sig(b)].add(b.page)
        by_text[norm(b.text)].add(b.page)
    out = {}
    for b in bod:
        if len(by_text[norm(b.text)]) >= 3:
            out[b.id] = (b.page - 1) in pages[sig(b)]
    return out


def is_divider(pb, pinfo):
    txt = [b for b in pb if b.kind != "table"]
    chars = sum(len(b.text) for b in pb)
    return (len(txt) <= 4 and chars < 250) or (pinfo.get("image_coverage", 0) >= 0.5 and chars < 400)


RATIO = 4.5  # TRX3 H5 — cos·nb 정답 간지를 보고 정함(반쯤 본)


def is_divider_ratio(pb, A, base, p, npages):
    """TRX3 H5: 쪽 안 size_max 최대 ÷ 본문 크기 ≥ RATIO 이고 첫·끝 쪽이 아님."""
    sz = [A[b.id]["size_max"] for b in pb if A.get(b.id, {}).get("status") == "ok"]
    return bool(sz) and 1 < p < npages and max(sz) / base >= RATIO


def build(blocks, attrs, K=K, divider_rule="trx2"):
    """→ {"pages": {쪽: {status, divider, cands:[id..]}}, "rows": {id: 줄 칸}}
    divider_rule: trx2(TRX2 그대로) | ratio(is_divider_ratio 만) | union(둘의 합, 첫·끝 쪽은 둘 다 뺌)."""
    bod = body(blocks)
    base = attrs["base"]["size"] or 1.0
    A = attrs["blocks"]
    npages = max(int(k) for k in attrs["pages"])
    rep = rep_ids(bod, A)
    by = defaultdict(list)
    for b in bod:
        by[b.page].append(b)
    pages, rows = {}, {}
    for p in sorted({int(k) for k in attrs["pages"]}):
        pinfo = attrs["pages"][str(p)]
        pb = by.get(p, [])
        st = pinfo["status"]
        if st != "ok":
            pages[p] = {"status": st, "divider": False, "cands": []}
            continue
        div = bool(pb) and is_divider(pb, pinfo)
        if divider_rule != "trx2":
            rat = bool(pb) and is_divider_ratio(pb, A, base, p, npages)
            div = rat if divider_rule == "ratio" else ((div or rat) and 1 < p < npages)
        ok = [b for b in pb if A.get(b.id, {}).get("status") == "ok"]
        cs = []
        for b in ok:
            a = A[b.id]
            if rep.get(b.id):
                continue  # 달린 제목의 둘째 쪽부터
            if len(b.text) > MAXLEN:
                continue
            if a["size_max"] >= BIG * base or (a["bold_share"] >= BOLD and (a["pos"] == "상" or a["color_diff"])) \
                    or b.kind == "heading":
                cs.append(b)
        sizes = sorted({A[b.id]["size_max"] for b in ok}, reverse=True)

        def sc(b):
            a = A[b.id]
            r = 1 - sizes.index(a["size_max"]) / max(1, len(sizes))
            return 0.5 * r + 0.2 * (a["pos"] == "상") + 0.15 * (a["bold_share"] >= BOLD) + 0.15 * a["color_diff"]

        top = sorted(cs, key=lambda b: (-sc(b), b.order))[:K]
        if div and ok:
            big = max(ok, key=lambda b: (A[b.id]["size_max"], -b.order))
            if big not in top:
                top.append(big)
        top = sorted(top, key=lambda b: b.order)
        pages[p] = {"status": st, "divider": div, "cands": [b.id for b in top]}
        for b in top:
            a = A[b.id]
            text = a.get("big_text") or b.text if b.kind == "table" else b.text
            rows[b.id] = {"page": p, "pos": a["pos"], "size": a["size_max"], "x": round(a["size_max"] / base, 2),
                          "bold": round(100 * a["bold_share"]), "color": a["color_diff"], "rep": b.id in rep,
                          "text": re.sub(r"\s+", " ", text)[:60]}
    return {"pages": pages, "rows": rows}


def line(bid, r, attrs=True):
    if not attrs:
        return f"{bid} | {r['page']} | {r['text']}"
    return (f"{bid} | {r['page']} | {r['pos']} | {r['size']} | {r['x']} | {r['bold']} | {'y' if r['color'] else 'n'} | "
            f"{'y' if r['rep'] else 'n'} | {r['text']}")


# ---------- 재현율 ----------
def recall(gold, cand_ids):
    """정답 항목 중 start_block·alt_blocks 의 하나가 후보에 든 비율 — 전체·간지·아님 따로(분모 같이)."""
    out = {}
    for name, sel in (("all", lambda g: True), ("divider", lambda g: g.get("divider")),
                      ("other", lambda g: not g.get("divider"))):
        its = [g for g in gold["items"] if sel(g)]
        hit = sum(any(x in cand_ids for x in [g["start_block"]] + g["alt_blocks"]) for g in its)
        out[name] = {"hit": hit, "n": len(its), "pct": round(100 * hit / len(its), 1) if its else None}
    return out


def trx1_sets(blocks, gold):
    """비교 열: TRX1 toc.heading_cands(정답 쪽 ±1 창) · toc_luna.candidates(per_page=3)."""
    from trlab.toc import heading_cands
    from trlab.toc_luna import candidates
    bod = body(blocks)
    hc = set()
    for g in gold["items"]:
        hc |= {b.id for b in heading_cands(bod, {g["start_page"] - 1, g["start_page"], g["start_page"] + 1})}
    lc = {ln.split(" | ")[0] for ln in candidates(blocks, per_page=3)}
    return hc, lc


def divider_check(pages, gold):
    """간지 판정 재현/정밀: 정답 divider 쪽 대비."""
    D = {g["start_page"] for g in gold["items"] if g.get("divider")}
    P = {p for p, v in pages.items() if v["divider"]}
    return {"gold": sorted(D), "pred": sorted(P), "recall": round(len(D & P) / len(D), 3) if D else None,
            "precision": round(len(D & P) / len(P), 3) if P else None}


def gold_path(key, lang):
    from trlab.common import GOLD
    p2 = GOLD2 / "toc" / f"{key}-{lang}.json"
    return p2 if p2.exists() else GOLD / "toc" / f"{key}-{lang}.json"


def run(key, lang):
    blocks = load_blocks(key, lang)
    attrs = load_attrs(key, lang)
    res = build(blocks, attrs)
    n = [len(v["cands"]) for v in res["pages"].values() if v["status"] == "ok"]
    out = {"book": key, "lang": lang, "K": K, "big": BIG, "bold": BOLD, "base_size": attrs["base"]["size"],
           "per_page": {"mean": round(statistics.mean(n), 2), "median": statistics.median(n), "max": max(n)},
           "pages": {str(p): v for p, v in res["pages"].items()}, "rows": res["rows"]}
    gp = gold_path(key, lang)
    if gp.exists():
        gold = json.load(open(gp))
        ids = set(res["rows"])
        hc, lc = trx1_sets(blocks, gold)
        out["recall"] = {"trx2": recall(gold, ids), "trx1_heading_cands": recall(gold, hc),
                         "trx1_luna_cands": recall(gold, lc)}
        out["divider"] = divider_check(res["pages"], gold)
        out["sizes"] = {"trx2": len(ids), "trx1_luna_cands": len(lc)}
    return out


def load(key, lang):
    return json.load(open(OUT2 / "cands" / f"{key}-{lang}.json"))


def main(argv):
    key, lang = argv[0], argv[1]
    out = run(key, lang)
    (OUT2 / "cands").mkdir(parents=True, exist_ok=True)
    json.dump(out, open(OUT2 / "cands" / f"{key}-{lang}.json", "w"), ensure_ascii=False, indent=1, sort_keys=True)
    s = {k: out[k] for k in ("per_page", "recall", "divider", "sizes") if k in out}
    print(key, lang, json.dumps(s, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
