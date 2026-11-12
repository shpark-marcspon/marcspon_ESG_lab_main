"""TRX3 H2 — 차례 창. 목차 항목은 책 차례대로 — 시작[i] ≥ 시작[i−1], 같음은 앞 항목이 level 1 이거나
번호 있는 목차에서 앞 항목에 찍힌 번호가 없을 때(묶음 머리) · 두 항목의 찍힌 번호가 같을 때만(장 머리가 간지 없이 첫 하위 항목과 한 쪽 ·
목차가 한 쪽에 두 항목을 둠). 발주서는 「level 1 일 때만」 — nb 「검증서」 묶음 머리를 목차 읽기가 level 2 로 내서 넓힘.

dp(items, cand, blocks, wins): 항목마다 후보 = 창 안 cands 행(+ 블록 없는 자리 하나: 번호 있으면 창 가운데, 없으면 창 첫 쪽).
  점수 = toc.match_score(항목 글, 블록, 창 안 크기 순위) + (level 1 이고 그 쪽이 간지면 +0.3) − 0.001 × 쪽
  − (목차 쪽이면 0.2). 차례를 거스르는 짝은 − 2(사실상 금지, 풀 수 없는 목차에서도 답이 나오게). Σ 최대(O(n·m²)).
  (발주서의 「− 0.002 × (쪽 − 앞 항목 쪽)」은 경로 합에서 (끝 − 처음)으로 줄어 가운데 항목엔 힘이 없어 쪽 자체에 건다.
   목차 쪽 감점은 목차가 항목 글을 그대로 싣기 때문 — kb ko 를 보고 넣음, 반쯤 본.)
windows3(items, pm, dp_pages, npages): 번호 있음 = H1 창(pagemap.window). 번호 없음 = [lo, hi] — lo = 앞 항목 DP 쪽
  (앞이 level 1 이면 그 쪽부터, 아니면 +1), hi = 다음 번호 항목 쪽(포함) 또는 책 끝. 13쪽 넘으면 [DP−2, DP+9] ∩ [lo, hi].
violations(pages, items) → 거스른 항목 idx.

명령: python -m trlab.order_win <키> <lang> [union|trx2] → OUT3/win/<키>-<lang>.json
"""

import json
import sys

from trlab import pagemap
from trlab.toc import _size_rank, match_score

DIV_BONUS = 0.3
GAP = 0.001
TOC_PEN = 0.2
BAD = 2.0
BIG_WIN = 13


def same_ok(prev_item, item, numbered=True):
    """같은 쪽 허용: 앞이 level 1 · 번호 있는 목차에서 앞이 번호 없는 머리(목차 읽기가 묶음 머리를 level 2 로 낼 때) ·
    찍힌 번호가 같음."""
    return (prev_item.get("level") == 1 or (numbered and prev_item.get("printed_page") is None)
            or (item.get("printed_page") is not None and item.get("printed_page") == prev_item.get("printed_page")))


def order_ok(prev_item, prev_page, item, page, numbered=True):
    if prev_page is None or page is None:
        return True
    return page > prev_page or (page == prev_page and same_ok(prev_item, item, numbered))


def is_numbered(items):
    return any(it.get("printed_page") is not None for it in items)


def item_cands(item, win, cand, ids, divs, mid=None, toc_pages=(), bonus=None):
    """→ [(쪽, 블록 id | None, 점수)]"""
    # TRX4: bonus = {블록 id: 더할 점수}(K2·K3·K4) — 없으면 TRX3 그대로
    cs = [ids[bid] for bid, r in cand["rows"].items() if r["page"] in win and bid in ids]
    rank = _size_rank(cs)
    out = []
    for b in cs:
        s = match_score(item["text"], b, rank[b.id])
        if item.get("level") == 1 and b.page in divs:
            s += DIV_BONUS
        s -= GAP * b.page + (TOC_PEN if b.page in toc_pages else 0.0)
        if bonus:
            s += bonus.get(b.id, 0.0)
        out.append((b.page, b.id, s))
    out.sort(key=lambda x: (x[0], ids[x[1]].order))
    hole = mid  # 번호 없는 항목의 빈 자리는 쪽 없음(차례 셈에서 빠짐)
    out.append((hole, None, 0.0))
    return out


def dp(items, cand, blocks, wins, divs, toc_pages=(), bonus=None):
    """wins[i] = (가운데|None, [쪽…]). → [(쪽, 블록 id | None, 점수)] 항목마다."""
    # TRX4: bonus[i] = {블록 id: 점수}
    ids = {b.id: b for b in blocks}
    C = [item_cands(it, w[1], cand, ids, divs, w[0], set(toc_pages), (bonus[i] if bonus else None))
         for i, (it, w) in enumerate(zip(items, wins))]
    if not C:
        return []
    num = is_numbered(items)
    best = [[c[2] for c in C[0]]]
    back = [[None] * len(C[0])]
    for i in range(1, len(items)):
        row, bk = [], []
        for (p, _, s) in C[i]:
            top, arg = None, None
            for j, (pp, _, _) in enumerate(C[i - 1]):
                v = best[i - 1][j] + s
                if not order_ok(items[i - 1], pp, items[i], p, num):
                    v -= BAD
                if top is None or v > top:
                    top, arg = v, j
            row.append(top)
            bk.append(arg)
        best.append(row)
        back.append(bk)
    j = max(range(len(C[-1])), key=lambda k: best[-1][k])
    path = [j]
    for i in range(len(items) - 1, 0, -1):
        j = back[i][j]
        path.append(j)
    path.reverse()
    return [C[i][j] for i, j in enumerate(path)]


def windows3(items, pm, dp_pages, npages, anchor_cut=True, hi_dp=None):
    """→ (가운데|None, [쪽…]) 항목마다, 그리고 bounds[(lo, hi)]."""
    """TRX4 K1: anchor_cut=False 면 13쪽 닻 자르기를 하지 않는다. hi_dp(기본 = not anchor_cut) 면 번호 없는 항목의
    hi = 다음 항목(쪽 있는 것)의 DP 쪽(포함, K1a) — 그 쪽이 lo 보다 앞이면(차례 어긋남) 책 끝. hi_dp=False 면 책 끝(K1b).
    어느 쪽이든 창에 그 항목의 DP 쪽을 넣는다(DP 고름이 창 밖으로 버려지지 않게)."""
    if hi_dp is None:
        hi_dp = not anchor_cut
    nums = [pagemap.to_pdf3(pm, it.get("printed_page"))[0] if it.get("printed_page") is not None else None for it in items]
    out, bounds = [], []
    for i, it in enumerate(items):
        if nums[i] is not None:
            w = pagemap.window(pm, it["printed_page"])
            out.append((nums[i], w))
            bounds.append((w[0], w[-1]))
            continue
        if i == 0 or dp_pages[i - 1] is None:
            lo = 1
        else:
            lo = dp_pages[i - 1] if items[i - 1].get("level") == 1 else dp_pages[i - 1] + 1
        nxt = next((nums[j] for j in range(i + 1, len(items)) if nums[j] is not None), None)
        hi = nxt if nxt is not None else npages
        if not anchor_cut and hi_dp and nxt is None:
            nd = next((dp_pages[j] for j in range(i + 1, len(items)) if dp_pages[j] is not None), None)
            own = dp_pages[i] if dp_pages[i] is not None else lo
            hi = nd if nd is not None and nd >= max(lo, own) else npages
        lo, hi = max(1, min(lo, npages)), max(1, min(hi, npages))
        if hi < lo:
            lo, hi = hi, lo
        if not anchor_cut and dp_pages[i] is not None:
            lo, hi = min(lo, dp_pages[i]), max(hi, dp_pages[i])
        if anchor_cut and hi - lo + 1 > BIG_WIN and dp_pages[i] is not None:
            a = dp_pages[i]
            lo, hi = max(lo, a - 2), min(hi, a + 9)
            if hi < lo:
                lo = hi = a
        out.append((None, list(range(lo, hi + 1))))
        bounds.append((lo, hi))
    return out, bounds


def violations(pages, items):
    bad = []
    for i in range(1, len(items)):
        if not order_ok(items[i - 1], pages[i - 1], items[i], pages[i], is_numbered(items)):
            bad.append(i)
    return bad


def full_windows(items, pm, npages):
    """DP 첫 판의 창: 번호 있음 = H1 창, 번호 없음 = 책 전체(DP 가 차례로 좁힌다)."""
    out = []
    for it in items:
        if it.get("printed_page") is not None:
            out.append((pagemap.to_pdf3(pm, it["printed_page"])[0], pagemap.window(pm, it["printed_page"])))
        else:
            out.append((None, list(range(1, npages + 1))))
    return out


def plan(items, pm, cand, blocks, toc_pages=(), anchor_cut=True, bonus=None, hi_dp=None):
    """DP(번호 없음 = 책 전체) → windows3 → 결과."""
    # TRX4: anchor_cut·hi_dp 는 windows3 로, bonus 는 dp 로(기본 = TRX3)
    npages = max(b.page for b in blocks)
    divs = {int(p) for p, v in cand["pages"].items() if v["divider"]}
    res = dp(items, cand, blocks, full_windows(items, pm, npages), divs, toc_pages, bonus)
    pages = [r[0] for r in res]
    wins, bounds = windows3(items, pm, pages, npages, anchor_cut, hi_dp)
    return {"dp": [{"page": p, "block": b, "score": round(s, 3)} for p, b, s in res], "windows": [w[1] for w in wins],
            "mids": [w[0] for w in wins], "bounds": bounds, "dp_violations": violations(pages, items)}


def main(argv):
    from trlab import tx3_pick as T
    key, lang = argv[0], argv[1]
    rule = argv[2] if len(argv) > 2 else "union"
    out = T.prep(key, lang, rule)
    print(key, lang, rule, "items", len(out["win"]["dp"]), "dp 거스름", out["win"]["dp_violations"],
          "창 쪽 평균", round(sum(len(w) for w in out["win"]["windows"]) / max(1, len(out["win"]["windows"])), 2))


if __name__ == "__main__":
    main(sys.argv[1:])
