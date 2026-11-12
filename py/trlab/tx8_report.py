"""TRX8 판별 표 — OUT8/runs 의 판을 모아 OUT8/summary.{json,md}. 호출 0, 항목 글은 싣지 않고 n 번호만.

열 묶음: G1(목차 쪽·항목 뽑힘·번호 있음·비용) · 길 1(c3) · 길 2(N- c3 · c4:K3 · d4:K1+K3 · 참고 d4:K1+K2+K3 · k5:sol · k5b · best4α · best4β)
· 후보(cand1 · c4-K3p · d4-K1+K3p · best4p · k5-sol30 · best4-30). 아래 표: 머리글 유무 · K5 가독 60 대 30 · 창 덮음 · 자리별 USD ·
「577 길이 새 책에서 틀린 꼴」(항목마다 갈래).

명령: python -m trlab.tx8_report → OUT8/summary.{json,md}
"""

import json
import sys
from collections import Counter

from trlab import tx4_pick as X
from trlab import tx8_pick as P8

COLS1 = ["c3"]
COLS2 = ["c3", "c4-K3", "d4-K1+K3", "d4-K1+K2+K3", "k5-sol", "k5b", "best4a", "best4b"]
COLS3 = ["cand1", "c4-K3p", "d4-K1+K3p", "best4pa", "best4pb", "k5-sol30", "best4-30a", "best4-30b"]
KINDS = ["닻 틀림", "창 밖", "요약 쪽 앞", "묶음 머리", "간지 없음", "G1 읽기 틀림", "차례 되돌림", "기타"]


def _g(name, mode):
    if mode in ("best4a", "best4b", "best4pa", "best4pb", "best4-30a", "best4-30b"):
        r = X.load_run(name, mode[:-1])
        return r and r["alpha" if mode.endswith("a") else "beta"]["grade"]
    r = X.load_run(name, mode)
    return r and r.get("grade")


def cell(g):
    if not g:
        return "–"
    f = lambda v: "–" if v is None else f"{v:g}"
    return f"{f(g['start_ab'])}/{f(g['end_ab'])}/{f(g['block'])}/{f(g['divider_start_ab'])}"


def fixed_broke(a, b):
    if not a or not b:
        return None
    wa, wb = set(a["wrong_start"]), set(b["wrong_start"])
    return {"fixed": sorted(wa - wb), "broke": sorted(wb - wa)}


def g1_row(key):
    p = P8.OUT8 / "g1" / f"{key}-ko.json"
    if not p.exists():
        return None
    r = json.load(open(p))
    sc = (r.get("score") or {}).get("g1") or {}
    items = r.get("items") or []
    return {"status": r.get("status"), "pages": r.get("pages"), "found": f"{sc.get('found')}/{sc.get('n')}",
            "printed_ok": f"{sc.get('printed_ok')}/{sc.get('found')}", "level_ok": f"{sc.get('level_ok')}/{sc.get('found')}",
            "items": len(items), "numbered": sum(1 for it in items if it.get("printed_page") is not None),
            "usd": round((r.get("call") or {}).get("costUsd", 0), 5)}


def wrong_kinds(name, mode="best4b"):
    """틀린 항목마다 갈래 — 코드로 가를 수 있는 것만(나머지 기타). 글은 싣지 않는다."""
    g = _g(name, mode)
    if not g:
        return []
    key, lang, bl = X.parse_name(name)
    gold = X.gold_for(key, lang)
    gi = {x["n"]: x for x in gold["items"]}
    rh = P8.OUT8 / "runhead" / f"{key}-{lang}.json"
    d4 = X.load_run(name, X.d4_mode(["K1", "K3"]))
    b4 = X.load_run(name, "best4")
    g1 = json.load(open(P8.OUT8 / "g1" / f"{key}-{lang}.json"))
    from trlab import tx4_report as R4
    pair = R4.pair(g1["items"], gold)
    out = []
    for n in g["wrong_start"]:
        row = next((r for r in g["rows"] if r["n"] == n), {})
        gg = gi[n]
        idx = pair.get(n)
        kind = "기타"
        if idx is None:
            kind = "G1 읽기 틀림"
        elif b4 and idx in (b4.get("beta_reverted") or []):
            kind = "차례 되돌림"
        elif d4 and idx is not None and gg["start_page"] not in d4["windows"][idx] and gg["start_page"] + 1 not in d4["windows"][idx]:
            kind = "창 밖"
        elif gg.get("level") == 1 and not gg.get("divider"):
            kind = "묶음 머리" if gg.get("printed_page") is None else "간지 없음"
        elif rh.exists():
            anchors = (X.prep4(key, lang, bl, ["K3"], win_mode="K1a")["runhead"]["anchors"]) if idx is not None else []
            a = anchors[idx] if idx is not None and idx < len(anchors) else None
            if a is not None and a not in (gg["start_page"], gg["start_page"] + 1 if gg.get("divider") else gg["start_page"]):
                kind = "닻 틀림"
        out.append({"n": n, "kind": kind, "pred": row.get("pred_start"), "gold": gg["start_page"], "divider": bool(gg.get("divider"))})
    return out


def runhead_table(key):
    p = P8.OUT8 / "runhead" / f"{key}-ko.json"
    if not p.exists():
        return None
    sc = json.load(open(p))
    kinds = Counter(v["kind"] for v in sc["pages"].values())
    P = X.prep4(key, "ko", True, ["K3"], win_mode="K1a")
    anchors = P["runhead"]["anchors"]
    gold = X.gold_for(key, "ko")
    from trlab import tx4_report as R4
    pair = {v: k for k, v in R4.pair(P["items"], gold).items()}
    gi = {x["n"]: x for x in gold["items"]}
    ok = sum(1 for i, a in enumerate(anchors) if a is not None and i in pair
             and a in ({gi[pair[i]]["start_page"]} | ({gi[pair[i]]["start_page"] + 1} if gi[pair[i]].get("divider") else set())))
    p8 = P8.OUT8 / "runhead8" / f"{key}-ko.json"
    dropped = json.load(open(p8))["n_dropped"] if p8.exists() else None
    return {"pages": len(sc["pages"]), "kinds": dict(kinds), "emph_pages": sum(1 for v in sc["pages"].values() if v["emph"]),
            "bounds": len(sc["bounds"]), "anchored": sum(a is not None for a in anchors), "n_items": len(anchors), "anchor_ab": ok,
            "dropped_k3p": dropped}


def k5_table(name):
    out = {}
    for mode in ("k5-sol", "k5-sol30"):
        r = X.load_run(name, mode)
        if r:
            out[mode] = {"images": r.get("images"), "Mpx": round(r.get("px", 0) / 1e6, 2), "in": r.get("inputTokens"),
                         "tok_per_Mpx": round(r.get("inputTokens", 0) / max(1e-6, r.get("px", 0) / 1e6)), "labels_read": len(r.get("labels_read", [])),
                         "basis": r.get("basis"), "usd": round(r.get("usd", 0), 5), "start_ab": r["grade"]["start_ab"]}
    return out


def coverage_table(name):
    p = P8.OUT8 / "win" / f"{name}-coverage.json"
    return json.load(open(p)) if p.exists() else None


def build():
    books = {}
    for key in P8.BOOKS8:
        n1, nN = P8.name1(key), P8.nameN(key)
        gold = X.gold_for(key, "ko")
        cols = {"c3": _g(n1, "c3")}
        cols.update({f"N-{c}": _g(nN, c) for c in COLS2})
        cols.update({f"N-{c}": _g(nN, c) for c in COLS3})
        books[key] = {"n_gold": len(gold["items"]), "g1": g1_row(key), "cols": {k: v for k, v in cols.items() if v},
                      "fixed_broke": {"cand1−best4b": fixed_broke(_g(nN, "best4b"), _g(nN, "cand1")),
                                      "best4pb−best4b": fixed_broke(_g(nN, "best4b"), _g(nN, "best4pb")),
                                      "c4-K3p−c4-K3": fixed_broke(_g(nN, "c4-K3"), _g(nN, "c4-K3p")),
                                      "d4-K1+K3p−d4-K1+K3": fixed_broke(_g(nN, "d4-K1+K3"), _g(nN, "d4-K1+K3p")),
                                      "best4-30b−best4b": fixed_broke(_g(nN, "best4b"), _g(nN, "best4-30b")),
                                      "k5-sol30−k5-sol": fixed_broke(_g(nN, "k5-sol"), _g(nN, "k5-sol30")),
                                      "best4b−d4-K1+K3": fixed_broke(_g(nN, "d4-K1+K3"), _g(nN, "best4b")),
                                      "c4-K3−c3": fixed_broke(_g(nN, "c3"), _g(nN, "c4-K3"))},
                      "runhead": runhead_table(key), "k5": k5_table(nN), "coverage": coverage_table(nN),
                      "best4": {k: (X.load_run(nN, "best4") or {}).get(k) for k in ("diff", "alpha_reverted", "beta_reverted", "usd")},
                      "d4": {k: (X.load_run(nN, X.d4_mode(["K1", "K3"])) or {}).get(k) for k in ("fill", "violations_to_dp", "usd")},
                      "wrong": {"c3": wrong_kinds(n1, "c3"), "N-best4b": wrong_kinds(nN, "best4b"), "N-c3": wrong_kinds(nN, "c3")}}
    return {"books": books, "ledger": P8.ledger()}


def md_of(s):
    L = ["# TRX8 summary — 시작 A∨B / 끝 A∨B / 블록 / 간지 시작 (%)", ""]
    books = s["books"]
    allcols = sorted({c for b in books.values() for c in b["cols"]}, key=lambda c: (c != "c3", c))
    L.append("| 책 | 정답 n | " + " | ".join(allcols) + " |")
    L.append("|---|---|" + "---|" * len(allcols))
    for k, b in books.items():
        L.append(f"| {k} | {b['n_gold']} | " + " | ".join(cell(b["cols"].get(c)) for c in allcols) + " |")
    L += ["", "## 틀린 항목 n (시작 A∨B)"]
    for k, b in books.items():
        for c, g in b["cols"].items():
            if g and g.get("wrong_start"):
                L.append(f"- {k} {c}: n {', '.join(map(str, g['wrong_start']))}")
    if any(b.get("g1") for b in books.values()):
        L += ["", "## G1", "| 책 | 상태 | 목차 쪽 | 항목 뽑힘 | 번호 맞음 | 수준 맞음 | 읽은 항목 | 번호 있음 | USD |", "|---|---|---|---|---|---|---|---|---|"]
        for k, b in books.items():
            r = b.get("g1") or {}
            L.append(f"| {k} | {r.get('status')} | {r.get('pages')} | {r.get('found')} | {r.get('printed_ok')} | {r.get('level_ok')} | {r.get('items')} | {r.get('numbered')} | {r.get('usd')} |")
    L += ["", "## 고친/망친 (n)"]
    for k, b in books.items():
        for lab, fb in (b.get("fixed_broke") or {}).items():
            if fb:
                L.append(f"- {k} {lab}: +{fb['fixed'] or 0} / −{fb['broke'] or 0}")
    L += ["", "## 577 길이 새 책에서 틀린 꼴 (n · 갈래 · 산출 쪽 → 정답 쪽)"]
    for k, b in books.items():
        for col, rows in (b.get("wrong") or {}).items():
            for w in rows:
                L.append(f"- {k} {col}: n {w['n']} · {w['kind']} · {w['pred']} → {w['gold']}{' (간지)' if w['divider'] else ''}")
    if s.get("wrong_kinds"):
        for name, by in s["wrong_kinds"].items():
            for col, rows in by.items():
                for w in rows:
                    L.append(f"- {name} {col}: n {w['n']} · {w['kind']}")
    L += ["", "## 머리글 유무 · 닻", "| 책 | 쪽 | 갈래 | 강조 쪽 | 경계 | 닻 붙은 항목 | 그중 A∨B | K3′ 뺀 낱말 |", "|---|---|---|---|---|---|---|---|"]
    for k, b in books.items():
        r = b.get("runhead")
        if r:
            L.append(f"| {k} | {r['pages']} | {r['kinds']} | {r['emph_pages']} | {r['bounds']} | {r['anchored']}/{r['n_items']} | {r['anchor_ab']} | {r['dropped_k3p']} |")
    L += ["", "## K5 가독 60 대 30", "| 책 | 판 | 장 | Mpx | 입력 토큰 | 토큰/Mpx | labels_read | basis | USD | 시작 A∨B |", "|---|---|---|---|---|---|---|---|---|---|"]
    for k, b in books.items():
        for mode, r in (b.get("k5") or {}).items():
            L.append(f"| {k} | {mode} | {r['images']} | {r['Mpx']} | {r['in']} | {r['tok_per_Mpx']} | {r['labels_read']} | {r['basis']} | {r['usd']} | {r['start_ab']} |")
    L += ["", "## 창 덮음 (N-)"]
    for k, b in books.items():
        if b.get("coverage"):
            L.append(f"- {k}: " + " · ".join(f"{m} {v['hit']}/{v['n']} 평균 {v['mean_pages']}쪽" for m, v in b["coverage"].items()))
    L += ["", "## 어긋남·되돌림·메움"]
    for k, b in books.items():
        d4, b4 = b.get("d4") or {}, b.get("best4") or {}
        L.append(f"- {k}: d4 메움 {d4.get('fill')} 거스름 {d4.get('violations_to_dp')} USD {d4.get('usd')} · best4 어긋남 {b4.get('diff')} α되돌림 {b4.get('alpha_reverted')} β되돌림 {b4.get('beta_reverted')} USD {b4.get('usd')}")
    led = s["ledger"]
    L += ["", "## 자리별 USD", "| 자리 | USD | 호출 |", "|---|---|---|"] + [f"| {k} | {v['usd']} | {v['n']} |" for k, v in led.items()]
    return "\n".join(L) + "\n"


def main(argv):
    s = build()
    json.dump(s, open(P8.OUT8 / "summary.json", "w"), ensure_ascii=False, indent=1)
    md = md_of(s)
    (P8.OUT8 / "summary.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main(sys.argv[1:])
