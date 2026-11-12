"""TRX8 — 목차↔본문 잇기 블라인드(처음 보는 책 nb3~nb6, sid:MARC-TICK-512). 얇은 겉옷: 자리(OUT8)·장부·자리 상한을
잡고 TRX2~4 모듈의 함수를 차례대로 부르기만 한다. 규칙·문턱·지시문은 BASE 그대로(블라인드 규약, out/trx8/first-run.txt).

길 1(번호 있음, 판 <키>-ko) = G1 sol 1회 + pagemap + 간지 union + 차례 DP(tx4_pick.run_code c3).
길 2(번호 가림, 판 <키>-ko-N) = K3 닻 + K1a 창 + luna d4 K1+K3 + K5 격자 sol + 어긋남만 sol 되묻기(best4β, BEST_BASE 를 K1+K3 로).
후보 셋(C3, 기본 꺼짐): ① β→α(호출 0) ② 띠 회사 줄(≥ 90% 쪽에 같은 띠·줄 자리) 뺀 K3′ (c4·d4·β′) ③ 격자 30쪽(sol30·β30).

허용 덮어쓰기 넷(§1): tx4_pick.OUT4 = OUT8 · TOTAL_CAP4 = 0.6 · SITE_PREFIX4["sol"] += ("TRX8/",) · BEST_BASE = ("K1","K3").
환경 변수(import 전 setdefault, 셸에서도 export): TRX2_OUT=TRX3_OUT=TRX4_OUT=OUT8, TRX2_GOLD=prework/gold-trx8.
장부 하나 OUT8/calls.jsonl — 자리 G1(tag TRX2/G1/) · luna(model) · sol(그 밖) ≤ 0.1 · 0.1 · 0.4, 합 0.6.

명령(py/ 에서, --group scope3colab, 실호출은 --with openai==3.26.0):
  python -m trlab.tx8_pick sha | first-run | ledger
  python -m trlab.tx8_pick <g1|prep|c3|path2|ref|check2|cand1|cand2|cand3> <키|all> [--dry-run]
"""

import hashlib
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

from trlab import common

ROOT = common.ROOT
OUT8 = Path(os.environ.get("TRX8_OUT", ROOT / "esg-lab" / "out" / "trx8"))
GOLD8 = ROOT / "prework" / "gold-trx8"
for _k in ("TRX2_OUT", "TRX3_OUT", "TRX4_OUT"):
    os.environ.setdefault(_k, str(OUT8))
os.environ.setdefault("TRX2_GOLD", str(GOLD8))

from trlab import cands as C  # noqa: E402
from trlab import glyphs  # noqa: E402
from trlab import order_win as W  # noqa: E402
from trlab import pagemap as PM  # noqa: E402
from trlab import runhead as R  # noqa: E402
from trlab import thumb_vision as TV  # noqa: E402
from trlab import toc_vision as G  # noqa: E402
from trlab import tx3_pick as T  # noqa: E402
from trlab import tx4_pick as X  # noqa: E402

# ---------- 속성 덮어쓰기 넷 (발주서 §1 — 모듈 파일은 0줄) ----------
X.OUT4 = OUT8                                    # 모든 TRX4 산출·장부를 out/trx8 로
X.TOTAL_CAP4 = 0.6                               # 이 티켓 상한(합)
X.SITE_PREFIX4["sol"] = X.SITE_PREFIX4["sol"] + ("TRX8/",)   # 후보 ②③ 의 sol 호출도 sol 자리에
X.BEST_BASE = ("K1", "K3")                       # 577 이 K2 를 뺌 → best4 의 luna 바닥 = d4 K1+K3

BOOKS8 = ["nb3", "nb4", "nb5", "nb6"]
LANG = "ko"
CAPS8 = {"G1": 0.1, "luna": 0.1, "sol": 0.4}
TOTAL8 = 0.6
DROP_SHARE = 0.9   # 후보 ②: 낱말이 책 쪽의 ≥ 90% 에 같은 띠·같은 줄 자리로 나오면 회사 이름 줄
MODULES = ["tx3_pick", "tx4_pick", "order_win", "runhead", "thumb_vision", "pagemap", "cands", "toc_pick", "toc_vision",
           "glyphs", "toc", "sec_shape", "page_vision", "common"]


def name1(key):
    return X.name_of(key, LANG, False)


def nameN(key):
    return X.name_of(key, LANG, True)


# ---------- 장부·자리 상한 ----------
def calls():
    p = OUT8 / "calls.jsonl"
    return [json.loads(x) for x in open(p) if x.strip()] if p.exists() else []


def site_of(row):
    if row.get("tag", "").startswith("TRX2/G1/"):
        return "G1"
    return "luna" if row.get("model") == X.LUNA else "sol"


def ledger(rows=None):
    rows = [r for r in (calls() if rows is None else rows) if not r.get("dry_run")]
    out = {s: {"usd": 0.0, "n": 0} for s in CAPS8}
    for r in rows:
        s = site_of(r)
        out[s]["usd"] += r.get("costUsd", 0)
        out[s]["n"] += 1
    out["total"] = {"usd": sum(v["usd"] for k, v in out.items() if k in CAPS8), "n": len(rows)}
    for v in out.values():
        v["usd"] = round(v["usd"], 6)
    return out


def room(site, est):
    """누적 + 예상 > 자리 상한 또는 합 0.6 이면 False(보내지 않음). 겉옷은 순차라 잡아 둔 몫은 tx4_pick 의 것뿐."""
    L = ledger()
    return L[site]["usd"] + est <= CAPS8[site] and L["total"]["usd"] + est <= TOTAL8


# ---------- 얼림 ----------
def module_sha():
    d = Path(__file__).parent
    return {m: hashlib.sha256((d / f"{m}.py").read_bytes()).hexdigest()[:16] for m in MODULES + ["tx8_pick"]}


def first_run():
    p = OUT8 / "first-run.txt"
    if p.exists():
        return p.read_text()
    OUT8.mkdir(parents=True, exist_ok=True)
    lines = [f"{time.strftime('%Y-%m-%d %H:%M:%S')} 모듈 얼림 — nb3~nb6 에 첫 trlab 명령 전(정답 얼림 gold-trx8/freeze.txt 뒤). "
             "규칙·문턱·지시문 = BASE 9a360a8, 후보 셋 포함(C3 에서 켜기만)"]
    lines += [f"  {v}  py/trlab/{k}.py" for k, v in module_sha().items()]
    p.write_text("\n".join(lines) + "\n")
    return p.read_text()


# ---------- C1: G1 · 준비 · 코드만 ----------
def g1(key, dry=False):
    """G1 sol 1회 — 겉옷이 먼저 자리 상한 0.1 을 재고 toc_vision.run 을 부른다. → OUT8/g1/<키>-ko.json"""
    path = OUT8 / "g1" / f"{key}-{LANG}.json"
    if path.exists() and json.load(open(path)).get("status") == "ok":
        return json.load(open(path))
    blocks = glyphs.load_blocks(key, LANG)
    pages = G.find_pages(blocks)
    if pages:
        inp, outp, usd = G.estimate(len(pages))
        res = {"book": key, "lang": LANG, "pages": pages, "estimate": {"input": inp, "output": outp, "usd": round(usd, 5)}}
        if dry:
            return dict(res, status="dry_run", ledger=ledger())
        if not room("G1", usd):
            return dict(res, status="over_cap", ledger=ledger())
    res = G.run(key, LANG)
    path.parent.mkdir(parents=True, exist_ok=True)
    json.dump(res, open(path, "w"), ensure_ascii=False, indent=1)
    return res


def prep(key):
    """glyphs → cands(trx2) → pagemap → order_win(union + win) — 모듈 main 을 차례대로."""
    glyphs.main([key, LANG])
    C.main([key, LANG])
    PM.main([key, LANG])
    W.main([key, LANG])


def c3(key):
    """길 1 c3 · N- c3 · N- c4:K3 (호출 0)."""
    return [X.run_code(name1(key)), X.run_code(nameN(key)), X.run_code(nameN(key), ["K3"])]


# ---------- C2: 길 2 ----------
def path2(key, dry=False):
    """N- 판: d4 K1+K3(luna) → K5 격자 60 sol(+ k5b) → best4β(BEST_BASE K1+K3)."""
    name = nameN(key)
    out = {"d4": X.run_d4(name, ["K1", "K3"], dry)}
    if out["d4"].get("status") != "ok":
        return out
    out["k5"] = TV.run_k5(name, "sol", dry)
    if out["k5"].get("status") != "ok":
        return out
    out["best4"] = X.run_best4(name, dry)
    return out


def ref(key, dry=False):
    return X.run_d4(nameN(key), ["K1", "K2", "K3"], dry)


# ---------- C3 후보 ① β→α ----------
def cand1_merge(beta_pk, reverted, k5_by, items, cand, ids):
    """β 고름에서 되돌린 항목만 K5 답(α 블록)으로 → 다시 차례 검사 → 거스르면 β(base) 로. → (picks, 바꾼 idx, 되돌린 idx)"""
    out, changed = list(beta_pk), []
    for i in reverted:
        x = k5_by.get(i)
        if not x or not x.get("page"):
            continue
        bid, _ = TV.to_block(items[i], x["page"], cand, ids)
        out[i] = (bid, x["page"], "k5")
        changed.append(i)
    bad = W.violations([p for _, p, _ in out], items)
    for i in bad:
        out[i] = beta_pk[i]
    return out, changed, bad


def _all_windows(P):
    npages = max(b.page for b in P["blocks"])
    return dict(P, win=dict(P["win"], windows=[list(range(1, npages + 1))] * len(P["items"]), mids=[None] * len(P["items"])))


def cand1(key):
    name = nameN(key)
    P = X.prep4(key, LANG, True, ["K3"], win_mode="K1a")
    b4 = X.load_run(name, "best4")
    k5 = json.load(open(OUT8 / "k5" / f"{name}-sol.json"))
    beta_pk = [(r["start_block"], r["start_page"], r["src"]) for r in b4["beta"]["pred"]["items"]]
    by = {x["idx"]: x for x in k5["answer"].get("items", [])}
    ids = {b.id: b for b in P["blocks"]}
    pk, changed, bad = cand1_merge(beta_pk, b4.get("beta_reverted", []), by, P["items"], P["cand"], ids)
    pred = X.make_pred(_all_windows(P), pk)
    return X.save_run(name, "cand1", {"mode": "cand1", "book": name, "status": "ok", "usd": 0.0, "changed": changed,
                                      "reverted_again": bad, "same_as_beta": not changed, "grade": X.grade(P, pred), "pred": pred})


# ---------- C3 후보 ② 띠 회사 줄 빼기 (K3′) ----------
def band_pages(key, npages):
    """쪽마다 위·아래 띠 낱말(runhead.band_words 그대로). → {p: {"top": words, "bot": words}}"""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(glyphs.pdf_path(key, LANG)))
    per = {}
    try:
        for q in range(1, npages + 1):
            pg = pdf[q - 1]
            chars, H = glyphs.page_chars(pg), pg.get_height()
            per[q] = {"top": R.band_words(chars, H, True), "bot": R.band_words(chars, H, False)}
            pg.close()
    finally:
        pdf.close()
    return per


def common_words(per, npages, share=DROP_SHARE):
    """(띠, 줄, 낱말 글 norm) 이 책 쪽의 ≥ share 에 나오면 회사 이름 줄 낱말."""
    cnt = Counter()
    for d in per.values():
        for band in ("top", "bot"):
            for k in {(band, w[4], R.norm(w[0])) for w in d[band]}:
                cnt[k] += 1
    return {k for k, n in cnt.items() if k[2] and n >= share * npages}


def drop_words(words, band, common):
    return [w for w in words if (band, w[4], R.norm(w[0])) not in common]


def record8(blocks, p, d, common):
    texts = R.running(blocks, p)
    emph = [R.norm(w) for w in R.emphasized(drop_words(d["top"], "top", common)) + R.emphasized(drop_words(d["bot"], "bot", common))]
    emph = [e for e in emph if len(e) >= 2]
    return {"texts": texts, "emph": emph, "kind": "both" if texts and emph else "text" if texts else "emph" if emph else "none"}


def scan8(key, blocks, drop=True):
    """후보 ② 의 runhead.scan — 띠 낱말에서 회사 이름 줄을 뺀 뒤 emphasized → 기록 → boundaries. → {pages, bounds, dropped}"""
    p = OUT8 / "runhead8" / f"{key}-{LANG}{'' if drop else '-nodrop'}.json"
    if p.exists():
        return json.load(open(p))
    npages = max(b.page for b in blocks)
    per = band_pages(key, npages)
    common = common_words(per, npages) if drop else set()
    pages = {q: record8(blocks, q, per[q], common) for q in range(1, npages + 1)}
    out = {"book": f"{key}-{LANG}", "drop": drop, "n_dropped": len(common), "dropped": sorted(list(k) for k in common),
           "pages": {str(k): v for k, v in pages.items()}, "bounds": R.boundaries(pages)}
    p.parent.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(p, "w"), ensure_ascii=False, indent=1, sort_keys=True)
    return json.load(open(p))


def prep8(key, drop=True):
    """prep4(…, ["K3"], K1a) 와 같은 차례 — scan 만 scan8. → P(+ win, bonus, runhead)"""
    P = X.load4(key, LANG, True)
    items, blocks, cand = P["items"], P["blocks"], P["cand"]
    base = W.plan(items, P["pm"], cand, blocks, P["toc_pages"])
    sc = scan8(key, blocks, drop)
    bounds = [tuple(b) for b in sc["bounds"]]
    anchors = R.attach(items, bounds)
    bonus = R.bonus_of(items, anchors, cand, T.divs_of(cand))
    win = W.plan(items, P["pm"], cand, blocks, P["toc_pages"], bonus=bonus)
    wins, bnds = X.windows_for(P, [d["page"] for d in win["dp"]], "K1a")
    win = dict(win, windows=[w[1] for w in wins], mids=[w[0] for w in wins], bounds=bnds, win_mode="K1a")
    P.update(win=win, base_win=base, bonus=bonus, methods=["K3p" if drop else "K3"],
             runhead={"anchors": anchors, "bonus": bonus, "n_bounds": len(bounds), "n_dropped": sc["n_dropped"]})
    return P


def c4p(key):
    name = nameN(key)
    P = prep8(key)
    X.save_win(name, P)
    pred = X.make_pred(P, X.dp_picks(P["win"]))
    return X.save_run(name, "c4-K3p", {"mode": "c4-K3p", "book": name, "status": "ok", "usd": 0.0, "n_dropped": P["runhead"]["n_dropped"],
                                       "grade": X.grade(P, pred), "pred": pred})


def d8(key, dry=False, drop=True, picks=None):
    """run_d4(K1+K3) 의 ask4·settle·make_pred·grade·save_run 조합, 닻만 K3′. picks 가 있으면 호출 없이 그 답을 settle."""
    name = nameN(key)
    mode = "d4-K1+K3p" if drop else "d4-K1+K3-nodrop"
    P = prep8(key, drop)
    wins = list(zip(P["win"]["mids"], P["win"]["windows"]))
    if picks is None:
        prev = X.load_run(name, mode)
        if prev and prev.get("status") == "ok" and not dry:
            picks, info = {int(i): b for i, b in prev["picks"].items()}, prev["info"]
        else:
            _, est = X.ask4("luna", X.LUNA, f"TRX8/{mode}/{name}", P, wins, dry=True)
            if not dry and not room("luna", est["estimate_usd"]):
                return dict(est, status="over_cap", mode=mode, book=name, ledger=ledger())
            picks, info = X.ask4("luna", X.LUNA, f"TRX8/{mode}/{name}", P, wins, dry=dry)
            if picks is None:
                return dict(info, mode=mode, book=name)
    else:
        info = {"status": "given"}
    pk, bad = X.settle(P, picks, "ai")
    pred = X.make_pred(P, pk)
    res = {"mode": mode, "book": name, "status": "ok", "picks": {str(i): b for i, b in picks.items()},
           "info": {k: v for k, v in info.items() if k != "calls"}, "usd": info.get("usd", 0.0), "violations_to_dp": bad,
           "windows": P["win"]["windows"], "fill": sum(1 for x in pk if x[2] != "ai"), "n_dropped": P["runhead"]["n_dropped"],
           "grade": X.grade(P, pred), "pred": pred}
    return res if picks is not None and info.get("status") == "given" else X.save_run(name, mode, res)


def best8(key, d4res, k5ans, tag, mode, dry=False, drop=True, picks=None):
    """run_best4 의 alpha_merge·ask4·answer_pairs 조합 — luna 바닥 d4res, K5 답 k5ans(60 또는 30 격자), 닻 K3′(drop).
    picks 가 있으면 호출 없이 그 sol 답을 쓴다(자기 검사)."""
    from trlab import sec_shape as S
    name = nameN(key)
    P = prep8(key, drop)
    conf = S.confident(P["items"], P["base_win"]["dp"], P["cand"], P["blocks"])
    P["shape"] = S.book_shape(P, conf)   # TRX4 run_best4 는 prep4(["K2","K3"]) 라 되묻기 프롬프트에 shape 열이 있다 — 그대로
    base_pk = [(r["start_block"], r["start_page"], r["src"]) for r in d4res["pred"]["items"]]
    ids = {b.id: b for b in P["blocks"]}
    by = {x["idx"]: x for x in k5ans.get("items", [])}
    npages = max(b.page for b in P["blocks"])
    alpha, diff, bad_a = X.alpha_merge(base_pk, by, P["items"], P["cand"], ids)
    Pall = _all_windows(P)
    pa = X.make_pred(Pall, alpha)
    res = {"mode": mode, "book": name, "status": "ok", "diff": diff, "alpha_reverted": bad_a, "n_dropped": P["runhead"]["n_dropped"],
           "alpha": {"grade": X.grade(P, pa), "pred": pa}}
    beta, info = list(base_pk), {"status": "skip", "usd": 0.0}
    if diff:
        w2 = list(zip(P["win"]["mids"], P["win"]["windows"]))
        for i in diff:
            ps = sorted({q for c in (base_pk[i][1], by[i]["page"]) if c for q in (c - 1, c, c + 1) if 1 <= q <= npages})
            w2[i] = (None, ps)
        if picks is None:
            _, est = X.ask4("sol", X.SOL, f"{tag}/{name}", P, w2, diff, nearest=True, dry=True)
            if not dry and not room("sol", est["estimate_usd"]):
                return dict(est, status="over_cap", mode=mode, book=name, diff=diff, ledger=ledger())
            picks, info = X.ask4("sol", X.SOL, f"{tag}/{name}", P, w2, diff, nearest=True, dry=dry)
            if dry:
                return dict(info, mode=mode, book=name, diff=diff)
            info = {k: v for k, v in info.items() if k != "calls"}
        else:
            info = {"status": "given", "usd": 0.0}
        info["picks"] = {str(i): b for i, b in (picks or {}).items()}
        pr = T.answer_pairs(picks or {}, P["items"], w2, ids)
        for i in diff:
            if pr[i][0]:
                beta[i] = (pr[i][0], pr[i][1], "sol")
        bad_b = W.violations([p for _, p, _ in beta], P["items"])
        for i in bad_b:
            beta[i] = base_pk[i]
        res["beta_reverted"] = bad_b
    pb = X.make_pred(Pall, beta)
    res.update(beta={"grade": X.grade(P, pb), "pred": pb}, beta_info=info, usd=info.get("usd", 0.0))
    return res if info.get("status") == "given" else X.save_run(name, mode, res)


def check2(key):
    """후보 ② 자기 검사(호출 0): 빼는 낱말 0 인 겉옷 길이 C2 의 run_d4·run_best4 와 같은가 —
    (가) 프롬프트 글(prompt4) 같음 (나) C2 의 luna 답을 겉옷 settle 로 넣은 pred == C2 pred (다) C2 의 sol 답으로 β pred == C2 β pred."""
    name = nameN(key)
    P4 = X.prep4(key, LANG, True, ["K3"], win_mode="K1a")
    P8 = prep8(key, drop=False)
    divs = T.divs_of(P4["cand"])
    w4 = list(zip(P4["win"]["mids"], P4["win"]["windows"]))
    w8 = list(zip(P8["win"]["mids"], P8["win"]["windows"]))
    t4 = X.prompt4(P4["items"], w4, P4["cand"], divs, None, P4["runhead"]["anchors"], None)
    t8 = X.prompt4(P8["items"], w8, P8["cand"], divs, None, P8["runhead"]["anchors"], None)
    out = {"book": name, "prompt_same": t4 == t8, "anchors_same": P4["runhead"]["anchors"] == P8["runhead"]["anchors"],
           "dp_same": P4["win"]["dp"] == P8["win"]["dp"], "windows_same": P4["win"]["windows"] == P8["win"]["windows"]}
    d4 = X.load_run(name, X.d4_mode(["K1", "K3"]))
    if d4:
        mine = d8(key, drop=False, picks={int(i): b for i, b in d4["picks"].items()})
        out["d4_pred_same"] = mine["pred"]["items"] == d4["pred"]["items"]
    b4 = X.load_run(name, "best4")
    k5 = OUT8 / "k5" / f"{name}-sol.json"
    if d4 and b4 and k5.exists():
        pk = {int(i): b for i, b in (b4.get("beta_info") or {}).get("picks", {}).items()}
        mine = best8(key, d4, json.load(open(k5))["answer"], "TRX8/check2", "check2", drop=False, picks=pk)
        out["beta_pred_same"] = mine["beta"]["pred"]["items"] == b4["beta"]["pred"]["items"]
        out["diff_same"] = mine["diff"] == b4["diff"]
    (OUT8 / "runs").mkdir(parents=True, exist_ok=True)
    json.dump(out, open(OUT8 / "runs" / f"{name}-check2.json", "w"), ensure_ascii=False, indent=1)
    return out


def cand2(key, dry=False):
    """K3′: c4 → d4(luna 1회) → β′(sol, K5 60 답 재사용)."""
    name = nameN(key)
    out = {"c4": c4p(key), "d4": d8(key, dry)}
    if out["d4"].get("status") != "ok":
        return out
    k5 = json.load(open(OUT8 / "k5" / f"{name}-sol.json"))["answer"]
    out["best4p"] = best8(key, out["d4"], k5, "TRX8/best4p", "best4p", dry)
    return out


# ---------- C3 후보 ③ 격자 30쪽 ----------
def sheets30(key, arrays=None):
    return TV.grid(key, LANG, rows=5, arrays=arrays, out_dir=OUT8 / "thumb30")


def px_tokens30():
    """첫 sol30 실측이 있으면 그 (입력 − 글/3)/px, 없으면 thumb_vision.tokens_per_px(60 격자 첫 실측)."""
    mine = [r for r in calls() if r.get("tag", "").startswith("TRX8/k5sol30/") and r.get("px") and not r.get("dry_run")]
    if mine:
        r = mine[0]
        return max(1e-5, (r["inputTokens"] - r.get("chars", 0) / 3.0) / r["px"]), r["outputTokens"]
    return TV.tokens_per_px(X.SOL)


def k5sol30(key, dry=False, arrays=None):
    """격자 5행(30쪽/장) sol 1회 → OUT8/k5/<판>-sol30.json + runs/<판>-k5-sol30.json(채점 k5_picks)."""
    from semgraph import llm
    name = nameN(key)
    p_out = OUT8 / "k5" / f"{name}-sol30.json"
    if p_out.exists() and not dry:
        res = json.load(open(p_out))
    else:
        P = X.load4(key, LANG, True)
        sheets = sheets30(key, arrays)
        npages = max(s["last"] for s in sheets)
        msg = TV.messages(sheets, P["items"], npages)
        chars = len(msg[0]["content"][0]["text"])
        k, outp = px_tokens30()
        px = sum(s["px"] for s in sheets)
        inp = int(px * k + chars / 3.0)
        usd = llm.cost(X.SOL, inp, 0, outp)
        info = {"model": X.SOL, "estimate_usd": round(usd, 6), "estimate_in": inp, "px": px, "images": len(sheets), "chars": chars,
                "sheets": [(s["first"], s["last"]) for s in sheets]}
        if dry:
            return dict(info, status="dry_run")
        if not room("sol", usd) or not X.reserve("sol", usd):
            return dict(info, status="over_cap", ledger=ledger())
        raw, row = X.send("sol", X.SOL, "Answer in the JSON schema.", msg, TV.SCHEMA5, f"TRX8/k5sol30/{name}", usd,
                          chars=chars, items=len(P["items"]), px=px, images=len(sheets))
        res = {"book": name, "model": X.SOL, "status": "ok", "info": info, "row": row, "answer": json.loads(raw),
               "sheets": [{k2: v for k2, v in s.items() if k2 != "path"} for s in sheets]}
        p_out.parent.mkdir(parents=True, exist_ok=True)
        json.dump(res, open(p_out, "w"), ensure_ascii=False, indent=1)
    P = _all_windows(X.prep4(key, LANG, True, (), win_mode="K1b"))
    ans = res["answer"]
    pred = X.make_pred(P, TV.k5_picks(P, ans))
    return X.save_run(name, "k5-sol30", {"mode": "k5-sol30", "book": name, "status": "ok", "usd": res["row"]["costUsd"],
                                         "grade": X.grade(P, pred), "pred": pred, "labels_read": ans.get("labels_read", []),
                                         "section_starts": ans.get("section_starts", []),
                                         "basis": dict(Counter(x.get("basis") for x in ans.get("items", []))),
                                         "px": res["info"]["px"], "inputTokens": res["row"]["inputTokens"], "images": res["info"]["images"]})


def cand3(key, dry=False):
    """sol30 → β30(luna 바닥 = C2 d4 K1+K3, 닻 K3 그대로 drop=False — 격자만 바꾼 열)."""
    name = nameN(key)
    out = {"k5sol30": k5sol30(key, dry)}
    if out["k5sol30"].get("status") != "ok":
        return out
    d4 = X.load_run(name, X.d4_mode(["K1", "K3"]))
    ans = json.load(open(OUT8 / "k5" / f"{name}-sol30.json"))["answer"]
    out["best4_30"] = best8(key, d4, ans, "TRX8/best4-30", "best4-30", dry, drop=False)
    return out


# ---------- main ----------
def _brief(r):
    g = r.get("grade") or (r.get("beta") or {}).get("grade")
    return (X.brief_line(g) + f" 틀림 {g['wrong_start']}") if g else json.dumps({k: v for k, v in r.items() if k not in ("calls", "pred")}, ensure_ascii=False)[:300]


def main(argv):
    cmd = argv[0]
    if cmd == "sha":
        for k, v in module_sha().items():
            print(v, k)
        return
    if cmd == "first-run":
        print(first_run())
        return
    if cmd == "ledger":
        print(json.dumps(ledger(), ensure_ascii=False))
        return
    keys = BOOKS8 if argv[1] == "all" else [argv[1]]
    dry = "--dry-run" in argv
    for key in keys:
        if cmd == "g1":
            r = g1(key, dry)
            print(key, "g1", r.get("status"), r.get("pages"), r.get("estimate"), json.dumps(r.get("score"), ensure_ascii=False))
        elif cmd == "prep":
            prep(key)
        elif cmd == "c3":
            for r in c3(key):
                print(r["book"], r["mode"], _brief(r))
        elif cmd == "path2":
            for k, r in path2(key, dry).items():
                print(key, k, _brief(r), "USD", r.get("usd"))
        elif cmd == "ref":
            r = ref(key, dry)
            print(key, "ref", _brief(r), "USD", r.get("usd"))
        elif cmd == "check2":
            print(key, "check2", json.dumps(check2(key)))
        elif cmd == "cand1":
            r = cand1(key)
            print(key, "cand1", _brief(r), "changed", r["changed"], "reverted_again", r["reverted_again"])
        elif cmd == "cand2":
            for k, r in cand2(key, dry).items():
                print(key, k, _brief(r), "USD", r.get("usd"), "dropped", r.get("n_dropped"))
        elif cmd == "cand3":
            for k, r in cand3(key, dry).items():
                print(key, k, _brief(r), "USD", r.get("usd"), "labels", len(r.get("labels_read", [])) if "labels_read" in r else "")
        else:
            raise SystemExit(f"모름: {cmd}")
    print("ledger", json.dumps(ledger()))


if __name__ == "__main__":
    main(sys.argv[1:])
