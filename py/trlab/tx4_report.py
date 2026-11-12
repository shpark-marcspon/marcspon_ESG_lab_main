"""TRX4 판별 표 — OUT4/runs 의 판들을 모아 OUT4/summary.{json,md}. 호출 0, 글(항목·띠 글)은 싣지 않고 수·항목 n 만.

표: 판별(시작 A∨B / 끝 A∨B / 블록 / 간지 시작) · 방법별 고친/망친 항목 · 머리글 유무 · K2 꼴 · 창 덮음(K1) · K5 가독 · 자리별 USD.
K2b 는 REP_PEN 을 0 으로 둔 c4-K2 를 이 자리에서 다시 셈(모듈 상수만 잠깐 바꿈, 파일 고침 없음).

명령: python -m trlab.tx4_report → OUT4/summary.{json,md}
"""

import json
import re
import statistics
from collections import Counter

from rapidfuzz import fuzz

from trlab import tx4_pick as X

COLS = ["trx3-best", "trx3-A2", "c3", "c4-K2", "c4-K3", "c4-K2+K3", "c4-K2+K3+K4", "d4-K1", "d4-K1+K2", "d4-K1+K2+K3",
        "d4-K1+K2+K3+K4", "k5-luna", "k5-sol", "k5b", "best4a", "best4b"]


def _g(name, mode):
    if mode.startswith("trx3-"):
        key, lang, _ = X.parse_name(name)
        r = X.OUT3 / "runs" / f"{key}-{lang}-{mode[5:]}.json"
        if not r.exists():
            return None
        return json.load(open(r)).get("grade")
    if mode in ("best4a", "best4b"):
        r = X.load_run(name, "best4")
        return r and r["alpha" if mode == "best4a" else "beta"]["grade"]
    r = X.load_run(name, mode)
    return r and r.get("grade")


def cell(g):
    if not g:
        return "–"
    f = lambda v: "–" if v is None else (f"{v:g}")
    return f"{f(g['start_ab'])}/{f(g['end_ab'])}/{f(g['block'])}/{f(g['divider_start_ab'])}"


def wrong(g):
    return set(g["wrong_start"]) if g else None


def fixed_broke(a, b):
    """a = 끈 판, b = 켠 판 → (고침, 망침)"""
    if not a or not b:
        return None
    wa, wb = wrong(a), wrong(b)
    return sorted(wa - wb), sorted(wb - wa)


# ---------- 짝(toc.score 와 같은 법) ----------
def pair(items, gold):
    """정답 n → 항목 idx (fuzz ≥ 80, [..] 머리 뗌, 차례대로 남은 것 중 최고)."""
    used, out = set(), {}
    for g in gold["items"]:
        gt = re.sub(r"^\[[^\]]*\]\s*", "", g["text"]).lower()
        best, bs = None, 0
        for i, it in enumerate(items):
            if i in used:
                continue
            s = fuzz.ratio(gt, re.sub(r"^\[[^\]]*\]\s*", "", it["text"]).lower())
            if s > bs:
                best, bs = i, s
        if best is not None and bs >= 80:
            used.add(best)
            out[g["n"]] = best
    return out


def ab_ok(g, page):
    return page is not None and (page == g["start_page"] or (bool(g.get("divider")) and page == g["start_page"] + 1))


# ---------- 표 ----------
def table_main(names):
    rows = {}
    for n in names:
        rows[n] = {c: cell(_g(n, c)) for c in COLS}
    return rows


def method_table(names):
    """방법마다 켠 판 − 끈 판. 코드만은 c 열끼리, AI 는 d4 열끼리."""
    pairs = [("K2 (코드)", "c3", "c4-K2"), ("K3 (코드)", "c3", "c4-K3"), ("K2 | K3 켜짐 (코드)", "c4-K3", "c4-K2+K3"),
             ("K3 | K2 켜짐 (코드)", "c4-K2", "c4-K2+K3"), ("K4 (코드)", "c4-K2+K3", "c4-K2+K3+K4"),
             ("K1 (AI, TRX3 d3 1회 → d4-K1)", "trx3-d3one", "d4-K1"), ("K2 (AI)", "d4-K1", "d4-K1+K2"),
             ("K3 (AI)", "d4-K1+K2", "d4-K1+K2+K3"), ("K2+K3 (AI)", "d4-K1", "d4-K1+K2+K3"),
             ("K4 (AI)", "d4-K1+K2+K3", "d4-K1+K2+K3+K4"), ("K5 α (best4α − d4-K1+K2+K3)", "d4-K1+K2+K3", "best4a"),
             ("K5 β (best4β − d4-K1+K2+K3)", "d4-K1+K2+K3", "best4b"), ("k5b (코드, K5 starts − c3)", "c3", "k5b")]
    out = {}
    for label, a, b in pairs:
        out[label] = {}
        for n in names:
            ga = _d3one(n) if a == "trx3-d3one" else _g(n, a)
            out[label][n] = fixed_broke(ga, _g(n, b))
    out["K2b (REP_PEN 0 → 0.3, 코드 c4-K2)"] = {n: k2b(n) for n in names}
    return out


def _d3one(name):
    key, lang, bl = X.parse_name(name)
    if bl:
        return None
    p = X.OUT3 / "runs" / f"{key}-{lang}-d3.json"
    return json.load(open(p))["one"]["grade"] if p.exists() else None


def k2b(name):
    from trlab import sec_shape as S
    key, lang, bl = X.parse_name(name)
    keep = S.REP_PEN
    try:
        S.REP_PEN = 0.0
        P = X.prep4(key, lang, bl, ["K2"], win_mode="K1a")
    finally:
        S.REP_PEN = keep
    g0 = X.grade(P, X.make_pred(P, X.dp_picks(P["win"])))
    return fixed_broke(g0, _g(name, "c4-K2"))


def header_table(names):
    from trlab import runhead as R
    out = {}
    for n in names:
        key, lang, bl = X.parse_name(n)
        p = X.OUT4 / "runhead" / f"{key}-{lang}.json"
        if not p.exists():
            continue
        sc = json.load(open(p))
        pages = sc["pages"]
        P = X.load4(key, lang, bl)
        gold = X.gold_for(key, lang)
        anchors = R.attach(P["items"], [tuple(b) for b in sc["bounds"]])
        pr = pair(P["items"], gold)
        gi = {g["n"]: g for g in gold["items"]}
        att = [(gn, anchors[i]) for gn, i in pr.items() if anchors[i] is not None]
        out[n] = {"edge_text_pages": sum(1 for v in pages.values() if v["texts"]), "pages": len(pages),
                  "kinds": dict(Counter(v["kind"] for v in pages.values())),
                  "emph_pages": sum(1 for v in pages.values() if v["emph"]), "bounds": len(sc["bounds"]),
                  "bounds_emph": sum(1 for b in sc["bounds"] if b[2] == "emph"),
                  "anchored_gold": len(att), "gold_n": len(gold["items"]),
                  "anchored_items": sum(a is not None for a in anchors), "items": len(anchors),
                  "anchor_right": sum(ab_ok(gi[gn], a) for gn, a in att),
                  "anchor_wrong_n": sorted(gn for gn, a in att if not ab_ok(gi[gn], a))}
    return out


def shape_table(names):
    out = {}
    for n in names:
        p = X.OUT4 / "shape" / f"{n}.json"
        if not p.exists():
            continue
        sh = json.load(open(p))
        key, lang, _ = X.parse_name(n)
        gold = X.gold_for(key, lang)
        gs = [sh["scores"].get(g["start_block"]) for g in gold["items"]]
        gs = [s for s in gs if s is not None]
        gold_ids = {g["start_block"] for g in gold["items"]} | {a for g in gold["items"] for a in g["alt_blocks"]}
        other = [s for b, s in sh["scores"].items() if b not in gold_ids]
        top = max(sh["model"]["xr"].items(), key=lambda kv: kv[1]) if sh["model"].get("xr") else None
        out[n] = {"confident": len(sh["confident"]), "items": sh["n_items"], "rep_cut": len(sh["rep"]),
                  "gold_in_cands": len(gs), "gold_n": len(gold["items"]),
                  "gold_mean": round(statistics.mean(gs), 3) if gs else None,
                  "other_mean": round(statistics.mean(other), 3) if other else None,
                  "gold_ge_0.8": sum(s >= 0.8 for s in gs), "other_ge_0.8": sum(s >= 0.8 for s in other),
                  "other_n": len(other), "top_xr": top}
    return out


def coverage_table(names):
    out = {}
    for n in names:
        key, lang, bl = X.parse_name(n)
        gold = X.gold_for(key, lang)
        P = X.load4(key, lang, bl)
        pr = pair(P["items"], gold)
        gi = {g["n"]: g for g in gold["items"]}
        row = {}
        for mode in X.WIN_MODES:
            p = X.OUT4 / "win" / f"{n}-none-{mode}.json"
            if not p.exists():
                continue
            w = json.load(open(p))["windows"]
            hit = sum(1 for gn, i in pr.items() if _in_win(gi[gn], w[i]))
            row[mode] = {"hit": hit, "n": len(pr), "mean_pages": round(statistics.mean(len(x) for x in w), 1)}
        out[n] = row
    return out


def _in_win(g, w):
    ok = {g["start_page"]} | ({g["start_page"] + 1} if g.get("divider") else set())
    return bool(ok & set(w))


def k5_table(names):
    out = {}
    for n in names:
        for which in ("luna", "sol"):
            r = X.load_run(n, f"k5-{which}")
            if not r:
                continue
            out[f"{n} {which}"] = {"px": r["px"], "images": r["images"], "inputTokens": r["inputTokens"],
                                   "tok_per_mpx": round(r["inputTokens"] / (r["px"] / 1e6), 1),
                                   "labels_read": len(r["labels_read"]), "section_starts": len(r["section_starts"]),
                                   "basis": r["basis"], "usd": round(r["usd"], 5)}
    return out


def usd_table():
    rows = X.calls()
    out = {site: round(X.site_spent(site, rows), 6) for site in X.CAPS4}
    by = Counter()
    for r in rows:
        t = r.get("tag", "").split("/")
        by["/".join(t[:2])] += r.get("costUsd", 0)
    out["by_판"] = {k: round(v, 6) for k, v in sorted(by.items())}
    out["total"] = round(X.total_spent(rows), 6)
    out["calls"] = len(rows)
    return out


def detail_rows(names):
    """판별 표 아래: 메움·거스름·USD·틀린 정답 n(AI 판)."""
    out = {}
    for n in names:
        for mode in ("d4-K1", "d4-K1+K2", "d4-K1+K2+K3", "d4-K1+K2+K3+K4"):
            r = X.load_run(n, mode)
            if r:
                out[f"{n} {mode}"] = {"fill": r.get("fill"), "violations": len(r.get("violations_to_dp", [])),
                                      "usd": r.get("usd"), "wrong": r["grade"]["wrong_start"]}
        r = X.load_run(n, "best4")
        if r:
            out[f"{n} best4"] = {"diff": r["diff"], "alpha_reverted": r.get("alpha_reverted"),
                                 "beta_reverted": r.get("beta_reverted"), "usd": r.get("usd"),
                                 "wrong_a": r["alpha"]["grade"]["wrong_start"], "wrong_b": r["beta"]["grade"]["wrong_start"]}
        for mode in ("c3", "c4-K2+K3+K4"):
            r = X.load_run(n, mode)
            if r and r.get("k4m") is not None:
                out[f"{n} {mode}"] = {"k4m": r["k4m"]}
    return out


def build():
    names = X.all_names()
    res = {"main": table_main(names), "methods": method_table(names), "header": header_table(names),
           "shape": shape_table(names), "coverage": coverage_table(names), "k5": k5_table(names), "usd": usd_table(),
           "detail": detail_rows(names)}
    X.OUT4.mkdir(parents=True, exist_ok=True)
    json.dump(res, open(X.OUT4 / "summary.json", "w"), ensure_ascii=False, indent=1, sort_keys=True)
    open(X.OUT4 / "summary.md", "w").write(to_md(res, names))
    return res


def to_md(res, names):
    L = ["# TRX4 판별 (`python -m trlab.tx4_report`)", "",
         "칸 = 시작 A∨B / 끝 A∨B / 블록 / 간지 시작 (%). kb = 반쯤 본, -N = 번호 가린 판(정답을 본 책 — 블라인드 아님). "
         "trx3-best·A2 는 kb 만 같은 처지(N- 줄은 번호 있는 원래 판).", ""]
    L.append("| 판 | " + " | ".join(COLS) + " |")
    L.append("|---" * (len(COLS) + 1) + "|")
    for n in names:
        L.append(f"| {n} | " + " | ".join(res["main"][n][c] for c in COLS) + " |")
    L += ["", "## 방법별 고친/망친 항목 (정답 n — 고침 / 망침)", "", "| 방법 | " + " | ".join(names) + " |",
          "|---" * (len(names) + 1) + "|"]
    for label, row in res["methods"].items():
        cells = []
        for n in names:
            v = row.get(n)
            cells.append("–" if v is None else f"+{v[0] or '0'} / −{v[1] or '0'}")
        L.append(f"| {label} | " + " | ".join(cells) + " |")
    L += ["", "## 머리글 유무 (K3)", "",
          "| 판 | 가장자리 글 쪽 | 갈래 | 강조 쪽 | 경계(강조) | 닻 붙은 항목 | 닻 붙은 정답 | 그중 A∨B 맞음 | 틀린 닻 n |",
          "|---|---|---|---|---|---|---|---|---|"]
    for n, h in res["header"].items():
        L.append(f"| {n} | {h['edge_text_pages']}/{h['pages']} | {h['kinds']} | {h['emph_pages']} | {h['bounds']}({h['bounds_emph']}) | "
                 f"{h['anchored_items']}/{h['items']} | {h['anchored_gold']}/{h['gold_n']} | {h['anchor_right']} | {h['anchor_wrong_n']} |")
    L += ["", "## K2 꼴", "", "| 판 | 확실/항목 | 되풀이 깎임 | 정답 블록 후보 안 | 정답 평균 | 다른 후보 평균 | ≥0.8 정답 | ≥0.8 다른 | 최빈 xr |",
          "|---|---|---|---|---|---|---|---|---|"]
    for n, s in res["shape"].items():
        L.append(f"| {n} | {s['confident']}/{s['items']} | {s['rep_cut']} | {s['gold_in_cands']}/{s['gold_n']} | {s['gold_mean']} | "
                 f"{s['other_mean']} | {s['gold_ge_0.8']} | {s['other_ge_0.8']}/{s['other_n']} | {s['top_xr']} |")
    L += ["", "## 창 덮음 (K1 — 정답 시작이 창 안인 짝 / 짝, 창 평균 쪽)", "", "| 판 | TRX3 닻 | K1a 다음 DP | K1b 책 끝 |", "|---|---|---|---|"]
    for n, row in res["coverage"].items():
        f = lambda m: f"{row[m]['hit']}/{row[m]['n']} · {row[m]['mean_pages']}" if m in row else "–"
        L.append(f"| {n} | {f('trx3')} | {f('K1a')} | {f('K1b')} |")
    if res["k5"]:
        L += ["", "## K5 가독", "", "| 판 | 장 | px | 입력 토큰 | 토큰/Mpx | labels_read | section_starts | basis | USD |",
              "|---|---|---|---|---|---|---|---|---|"]
        for k, v in res["k5"].items():
            L.append(f"| {k} | {v['images']} | {v['px']} | {v['inputTokens']} | {v['tok_per_mpx']} | {v['labels_read']} | "
                     f"{v['section_starts']} | {v['basis']} | {v['usd']} |")
    if res["detail"]:
        L += ["", "## 판 상세 (메움·거스름·USD·틀린 정답 n · best4 어긋남 · K4 m)", ""]
        for k, v in res["detail"].items():
            L.append(f"- {k}: {json.dumps(v, ensure_ascii=False)}")
    u = res["usd"]
    L += ["", f"## USD — 합 {u['total']} / 0.5 · luna {u['luna']} / 0.1 · sol {u['sol']} / 0.4 · 호출 {u['calls']}", ""]
    for k, v in u["by_판"].items():
        L.append(f"- {k}: {v}")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    r = build()
    print(open(X.OUT4 / "summary.md").read())
