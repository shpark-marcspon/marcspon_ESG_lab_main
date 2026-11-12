"""TRX3 — 목차↔본문 범위 잇기 100% 가까이. H0 셈(A∨B) · H4 투표(luna d3 · sol s3) · 절제 판 · best.

셈(H0, 채점에서만): 시작 A∨B = 산출 시작 = 정답 시작, 또는 정답이 divider 이고 산출 시작 = 정답 시작 + 1.
끝 A∨B = 산출 끝 = 정답 끝, 또는 정답 끝 + 1 이 정답 간지 쪽(D)이고 산출 끝 = 정답 끝 + 1. 블록 = toc.score 그대로.
항목 짝은 toc.score 와 같은 법(fuzz ≥ 80), 분모 = 정답 항목.

자리 찾기: read_dir(key, lang, kind) = OUT3/<kind> → out/trx2/<kind> → out/trx1/<kind>. 블록은 common.load_doc 로 직접.
판: c3 코드만(G1 + H1 창 + H2 DP) · d3 luna 3회 투표 · s3 sol 3회 투표(INSTR_NEAREST) · h3 쪽 이미지(page_vision) ·
    A1(H1 좁힘 뺌) · A2(H2 뺌) · A5(H5 union 대신 trx2) · best.
실호출 예상 = 프롬프트 글자 수 × 그 모델 첫 실측 토큰/글자, 누적 + 예상 > 자리 상한이면 보내지 않음(over_cap).

명령(py/ 에서, --group scope3colab):
  python -m trlab.tx3_pick rescore                       → OUT3/h0.json (TRX2 판 여섯 × 7권)
  python -m trlab.tx3_pick prep <키> <lang>              → OUT3/pagemap·win·cands-union
  python -m trlab.tx3_pick c3 <키> <lang>                → OUT3/runs/<키>-<lang>-c3.json
  python -m trlab.tx3_pick <d3|s3|A1|A2|A5|best> <키> <lang> [--dry-run]  (실호출은 --with openai==3.26.0)
"""

import json
import os
import re
import sys
import threading
from collections import Counter
from pathlib import Path

from trlab import common
from trlab.common import load_doc

ROOT = common.ROOT
OUT3 = Path(os.environ.get("TRX3_OUT", ROOT / "esg-lab" / "out" / "trx3"))
OUT2_DEFAULT = ROOT / "esg-lab" / "out" / "trx2"
OUT1_DEFAULT = ROOT / "esg-lab" / "out" / "trx1"
GOLD2 = Path(os.environ.get("TRX2_GOLD", ROOT / "prework" / "gold-trx2"))
GOLD1 = Path(os.environ.get("TRX1_GOLD", ROOT / "prework" / "gold-trx1"))

BOOKS7 = [("sam", "ko"), ("sam", "en"), ("cos", "ko"), ("cos", "en"), ("kb", "ko"), ("kb", "en"), ("nb", "ko")]
BOOKS8 = BOOKS7 + [("nb2", "ko")]
SOL_BOOKS = [("nb2", "ko"), ("kb", "ko"), ("kb", "en"), ("cos", "en"), ("cos", "ko"), ("nb", "ko")]
LUNA, SOL = "gpt-6-luna", "gpt-6.1-sol"
CAPS = {"G1": 0.05, "luna": 0.6, "sol": 1.0}
TOTAL_CAP = 2.0
CHUNK_TOKENS = 60000



# ---------- 자리 ----------
def read_dir(key, lang, kind, name=None):
    """kind 폴더에서 <키>-<lang>(.json | /document.json) 을 OUT3 → out/trx2 → out/trx1 차례로."""
    name = name or f"{key}-{lang}"
    for base in (OUT3, OUT2_DEFAULT, OUT1_DEFAULT):
        for p in (base / kind / f"{name}.json", base / kind / name / "document.json"):
            if p.exists():
                return p
    return None


def load_blocks3(key, lang):
    return load_doc(read_dir(key, lang, "parse"))


def load_json(key, lang, kind, name=None):
    p = read_dir(key, lang, kind, name)
    return json.load(open(p)) if p else None


def gold_path(key, lang):
    p2 = GOLD2 / "toc" / f"{key}-{lang}.json"
    return p2 if p2.exists() else GOLD1 / "toc" / f"{key}-{lang}.json"


def gold_for(key, lang):
    return json.load(open(gold_path(key, lang)))


# ---------- H0 셈 ----------
def score_ab(rows, gold):
    """toc.score 의 rows(정답 차례) + 정답 → 항목마다 start_ab·end_ab·block 과 % (전체·간지 항목 따로)."""
    gi = {g["n"]: g for g in gold["items"]}
    D = {g["start_page"] for g in gold["items"] if g.get("divider")}
    per = []
    for r in rows:
        g = gi[r["n"]]
        ok_s = ok_e = False
        if r.get("found"):
            p = r["pred"]
            ps, pe = p.get("start_page"), p.get("end_page")
            ok_s = ps is not None and (ps == g["start_page"] or (bool(g.get("divider")) and ps == g["start_page"] + 1))
            ok_e = pe is not None and (pe == g["end_page"] or ((g["end_page"] + 1) in D and pe == g["end_page"] + 1))
        per.append({"n": r["n"], "divider": bool(g.get("divider")), "found": bool(r.get("found")),
                    "start_ab": ok_s, "end_ab": ok_e, "block": bool(r.get("block_ok")),
                    "pred_start": (r.get("pred") or {}).get("start_page"), "gold_start": g["start_page"]})

    def pct(sel, k):
        sub = [x for x in per if sel(x)]
        return {"hit": sum(x[k] for x in sub), "n": len(sub),
                "pct": round(100 * sum(x[k] for x in sub) / len(sub), 1) if sub else None}

    out = {"rows": per}
    for k in ("found", "start_ab", "end_ab", "block"):
        out[k] = pct(lambda x: True, k)
    out["divider_start_ab"] = pct(lambda x: x["divider"], "start_ab")
    out["wrong_start"] = [x["n"] for x in per if not x["start_ab"]]
    return out


def brief(s):
    return {k: s[k]["pct"] for k in ("found", "start_ab", "end_ab", "block", "divider_start_ab")}


def rescore():
    """TRX2 g2 판 여섯 × 7권 → OUT3/h0.json."""
    res = {}
    for key, lang in BOOKS7:
        gold = gold_for(key, lang)
        for mode in ("a", "b", "c", "d", "e", "dsol"):
            p = OUT2_DEFAULT / "g2" / f"{key}-{lang}-{mode}.json"
            if not p.exists():
                continue
            g2 = json.load(open(p))
            s = score_ab(g2["score"]["rows"], gold)
            res[f"{key}-{lang}-{mode}"] = {"book": key, "lang": lang, "mode": mode, **brief(s),
                                           "wrong_start": s["wrong_start"], "rows": s["rows"]}
    OUT3.mkdir(parents=True, exist_ok=True)
    json.dump(res, open(OUT3 / "h0.json", "w"), ensure_ascii=False, indent=1, sort_keys=True)
    return res


# ---------- 투표 ----------
def vote(answers, order_of):
    """answers = 3회의 [(블록 id | None, 쪽 | None)]. 쪽 다수(≥ 2) → 그 쪽 고름 중 최다 블록(동률이면 order 작은 것).
    3회 다 다르면 undecided. → (블록, 쪽, 갈래)"""
    pages = Counter(p for _, p in answers if p is not None)
    if not pages:
        return None, None, "undecided"
    page, c = pages.most_common(1)[0]
    if c < 2:
        return None, None, "undecided"
    blks = Counter(b for b, p in answers if p == page and b)
    if not blks:
        return None, page, "vote"
    top = max(blks.values())
    best = min((b for b, n in blks.items() if n == top), key=lambda b: order_of(b))
    return best, page, "vote"


def shaky(runs):
    """흔들림: 3회 답이 다 같지 않은 항목 수(쪽 기준·블록 기준)."""
    n = len(runs[0])
    pg = sum(len({r[i][1] for r in runs}) > 1 for i in range(n))
    bk = sum(len({r[i][0] for r in runs}) > 1 for i in range(n))
    return {"page": pg, "block": bk, "n": n}


# ---------- 장부 ----------
def calls():
    p = OUT3 / "calls.jsonl"
    return [json.loads(x) for x in open(p) if x.strip()] if p.exists() else []


SITE_PREFIX = {"G1": ("TRX2/G1/",), "luna": ("TRX3/d3/", "TRX3/A", "TRX3/h3/", "TRX3/d3fix/"),
               "sol": ("TRX3/s3/", "TRX3/s3fix/", "TRX3/best/")}


def site_spent(site, rows=None):
    rows = calls() if rows is None else rows
    return sum(r.get("costUsd", 0) for r in rows if not r.get("dry_run") and r.get("tag", "").startswith(SITE_PREFIX[site]))


def total_spent(rows=None):
    rows = calls() if rows is None else rows
    return sum(r.get("costUsd", 0) for r in rows if not r.get("dry_run"))


def tok_per_char(model, rows=None):
    """첫 실측 토큰/글자와 출력 토큰. 없으면 TRX2 장부의 첫 실측, 그것도 없으면 어림(1/2.2, 2400)."""
    rows = calls() if rows is None else rows
    mine = [r for r in rows if r.get("model") == model and r.get("chars") and not r.get("pages")]
    if not mine:
        p = OUT2_DEFAULT / "calls.jsonl"
        if p.exists():
            mine = [json.loads(x) for x in open(p) if x.strip()]
            mine = [r for r in mine if r.get("model") == model and r.get("chars") and r.get("tag", "").startswith("TRX2/G2/")]
    if mine:
        r = mine[0]
        return r["inputTokens"] / r["chars"], r["outputTokens"]
    return 1 / 2.2, 2400


def estimate(model, text, instr, n_items=None):
    from semgraph import llm
    k, outp = tok_per_char(model)
    inp = int(len(text) * k) + len(instr) // 4
    if n_items is not None:
        outp = max(200, int(outp * min(1.0, n_items / 30 + 0.2)))
    return inp, outp, llm.cost(model, inp, 0, outp)




# ---------- 보내기(장부 한 곳, 자리 상한) ----------
_LOCK = threading.Lock()
_RESERVED = {"G1": 0.0, "luna": 0.0, "sol": 0.0}


def reserve(site, usd):
    """누적 + 잡아 둔 몫 + 예상 > 자리 상한(또는 합 2 USD)이면 False — 보내지 않는다."""
    with _LOCK:
        rows = calls()
        held = sum(_RESERVED.values())
        if site_spent(site, rows) + _RESERVED[site] + usd > CAPS[site] or total_spent(rows) + held + usd > TOTAL_CAP:
            return False
        _RESERVED[site] += usd
        return True


def send(site, model, instr, text, schema, tag, est_usd, **extra):
    """semgraph.llm.respond 를 OUT3/.scratch 로 부르고, 줄(+ chars·pages·px)은 잠금 안에서 OUT3/calls.jsonl 에 한 줄."""
    from semgraph import llm
    scratch = OUT3 / ".scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        raw, row = llm.respond(model, instr, text, schema, scratch, tag, effort="low")
    finally:
        with _LOCK:
            _RESERVED[site] = max(0.0, _RESERVED[site] - est_usd)
    row.update(extra, estimateUsd=round(est_usd, 6))
    with _LOCK:
        with open(OUT3 / "calls.jsonl", "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return raw, row


# ---------- 준비: H1 지도 · H5 후보 · H2 DP·창 ----------
def get_pm(key, lang):
    from trlab import pagemap
    p = OUT3 / "pagemap" / f"{key}-{lang}.json"
    if not p.exists():
        pm = pagemap.run(key, lang)
        p.parent.mkdir(parents=True, exist_ok=True)
        json.dump(pm, open(p, "w"), ensure_ascii=False, indent=1, sort_keys=True)
    return json.load(open(p))


def get_cands(key, lang, rule, blocks=None):
    if rule == "trx2":
        return load_json(key, lang, "cands")
    from trlab import cands as C
    p = OUT3 / "cands" / f"{key}-{lang}-{rule}.json"
    if not p.exists():
        blocks = blocks or load_blocks3(key, lang)
        res = C.build(blocks, load_json(key, lang, "attrs"), divider_rule=rule)
        out = {"book": key, "lang": lang, "rule": rule, "pages": {str(k): v for k, v in res["pages"].items()},
               "rows": res["rows"]}
        p.parent.mkdir(parents=True, exist_ok=True)
        json.dump(out, open(p, "w"), ensure_ascii=False, indent=1, sort_keys=True)
    return json.load(open(p))


def divs_of(cand):
    return {int(p) for p, v in cand["pages"].items() if v["divider"]}


def prep(key, lang, rule="union"):
    from trlab import order_win as W
    blocks = load_blocks3(key, lang)
    pm = get_pm(key, lang)
    cand = get_cands(key, lang, rule, blocks)
    g1 = load_json(key, lang, "g1")
    items = g1["items"]
    win = W.plan(items, pm, cand, blocks, g1.get("pages") or [])
    name = f"{key}-{lang}" + ("" if rule == "union" else f"-{rule}")
    (OUT3 / "win").mkdir(parents=True, exist_ok=True)
    json.dump(dict(win, book=key, lang=lang, rule=rule), open(OUT3 / "win" / f"{name}.json", "w"), ensure_ascii=False,
              indent=1, sort_keys=True)
    return {"blocks": blocks, "pm": pm, "cand": cand, "g1": g1, "items": items, "win": win}


# ---------- 산출 꼴 · 채점 ----------
def make_pred(key, lang, blocks, toc_pages, items, picks, wins):
    """picks[i] = (블록 id | None, 쪽 | None, src). 블록이 창 밖이면 버림. 쪽이 없으면 창 가운데(번호 있을 때)."""
    ids = {b.id: b for b in blocks}
    npages = max(b.page for b in blocks)
    res = []
    for it, (bid, page, src), (mid, win) in zip(items, picks, wins):
        b = ids.get(bid) if bid else None
        if b is not None and b.page not in win:
            b, page, src = None, None, "fill"
        if b is not None:
            page = b.page
        if page is None:
            page, src = mid, "fill"
        res.append({"text": it["text"], "printed": it.get("printed_page"), "level": it.get("level"),
                    "start_block": b.id if b else None, "start_page": page, "src": src})
    starts = sorted({r["start_page"] for r in res if r["start_page"]})
    for r in res:
        nx = [s for s in starts if r["start_page"] and s > r["start_page"]]
        r["end_page"] = (nx[0] - 1 if nx else npages) if r["start_page"] else None
    return {"book": key, "lang": lang, "toc_pages": toc_pages, "items": res,
            "src": dict(Counter(r["src"] for r in res))}


def grade(key, lang, pred, blocks):
    from trlab.toc import score
    gold = gold_for(key, lang)
    sc = score(pred, gold, blocks)
    ab = score_ab(sc["rows"], gold)
    return {**brief(ab), "wrong_start": ab["wrong_start"], "rows": ab["rows"], "match": sc["rows"]}


def save_run(key, lang, mode, res):
    (OUT3 / "runs").mkdir(parents=True, exist_ok=True)
    json.dump(res, open(OUT3 / "runs" / f"{key}-{lang}-{mode}.json", "w"), ensure_ascii=False, indent=1)
    return res


def load_run(key, lang, mode):
    p = OUT3 / "runs" / f"{key}-{lang}-{mode}.json"
    return json.load(open(p)) if p.exists() else None


def dp_picks(win):
    return [(d["block"], d["page"], "dp") for d in win["dp"]]


def run_c3(key, lang):
    P = prep(key, lang)
    wins = list(zip(P["win"]["mids"], P["win"]["windows"]))
    pred = make_pred(key, lang, P["blocks"], P["g1"]["pages"], P["items"], dp_picks(P["win"]), wins)
    return save_run(key, lang, "c3", {"mode": "c3", "status": "ok", "usd": 0.0, "grade": grade(key, lang, pred, P["blocks"]), "pred": pred})


# ---------- AI 고르기(d3 · s3 · 절제) ----------
def instr_for(model, nearest=False):
    from trlab import toc_pick as TP
    s = TP.INSTR.replace("COLUMNS", TP.COLS_ATTR)
    return s + "\n" + TP.INSTR_NEAREST if nearest else s


def ask(site, model, tag, items, wins, cand, idxs=None, nearest=False, dry=False):
    """한 판 한 번(15항목 쪼갬은 60k 토큰 넘을 때만) → (picks{idx: 블록 id}, info)."""
    from trlab import toc_pick as TP
    instr = instr_for(model, nearest)
    divs = divs_of(cand)
    idxs = list(range(len(items))) if idxs is None else list(idxs)
    k, _ = tok_per_char(model)
    full = TP.prompt(items, wins, cand, divs, True, idxs)
    chunks = [idxs] if len(full) * k <= CHUNK_TOKENS else [idxs[i:i + 15] for i in range(0, len(idxs), 15)]
    texts = [TP.prompt(items, wins, cand, divs, True, c) for c in chunks]
    ests = [estimate(model, t, instr, len(c)) for t, c in zip(texts, chunks)]
    info = {"model": model, "tag": tag, "chunks": len(chunks), "chars": sum(len(t) for t in texts),
            "estimate_usd": round(sum(e[2] for e in ests), 6)}
    if dry:
        return None, dict(info, status="dry_run")
    picks, rows = {}, []
    for c, t, e in zip(chunks, texts, ests):
        if not reserve(site, e[2]):
            return None, dict(info, status="over_cap", calls=rows)
        raw, row = send(site, model, instr, t, TP.SCHEMA, tag, e[2], chars=len(t), items=len(c))
        rows.append(row)
        for x in json.loads(raw)["items"]:
            if x["idx"] in c:
                picks[x["idx"]] = x["block_id"]
    return picks, dict(info, status="ok", calls=rows, usd=round(sum(r["costUsd"] for r in rows), 6))


def answer_pairs(picks, items, wins, ids):
    """AI 답 → [(블록 | None, 쪽 | None)] — 창 밖·없는 블록·"" 은 (None, None)."""
    out = []
    for i in range(len(items)):
        b = ids.get(picks.get(i) or "")
        out.append((b.id, b.page) if b is not None and b.page in wins[i][1] else (None, None))
    return out


def fill_dp(pairs, win, src):
    """빈 답은 번호 있으면 창 가운데(make_pred), 없으면 DP 쪽."""
    out = []
    for (b, p), d, mid in zip(pairs, win["dp"], win["mids"]):
        if b is None and p is None:
            out.append((None, None, "fill") if mid is not None else (d["block"], d["page"], "dp"))
        else:
            out.append((b, p, src))
    return out


def fix_violations(site, model, tag, P, picks, wins, nearest=False, ask_again=True):
    """거스른 항목을 한 번 다시 묻고(이웃 고름으로 창 좁힘), 그래도 거스르면 DP. → picks, 셈"""
    from trlab import order_win as W
    items, win = P["items"], P["win"]
    pages = [p for _, p, _ in picks]
    bad = W.violations(pages, items)
    stat = {"violations": len(bad), "fixed_by_ask": 0, "to_dp": 0, "usd": 0.0}
    if not bad:
        return picks, stat
    V = sorted(set(bad) | {i - 1 for i in bad})
    picks = list(picks)
    if ask_again:
        w2 = list(wins)
        for k in V:
            lo = next((pages[j] for j in range(k - 1, -1, -1) if j not in V and pages[j]), 1)
            hi = next((pages[j] for j in range(k + 1, len(items)) if j not in V and pages[j]), 10 ** 6)
            sub = [p for p in wins[k][1] if lo <= p <= hi]
            w2[k] = (wins[k][0], sub or wins[k][1])
        got, info = ask(site, model, tag, items, w2, P["cand"], V, nearest)
        stat["usd"] = info.get("usd", 0.0)
        stat["status"] = info["status"]
        if got is not None:
            ids = {b.id: b for b in P["blocks"]}
            pr = answer_pairs(got, items, w2, ids)
            for k in V:
                if pr[k][0]:
                    picks[k] = (pr[k][0], pr[k][1], picks[k][2] + "+ask")
            left = W.violations([p for _, p, _ in picks], items)
            stat["fixed_by_ask"] = len(bad) - len(left)
            bad = left
    for i in bad:
        d = win["dp"][i]
        picks[i] = (d["block"], d["page"], "dp")
    stat["to_dp"] = len(bad)
    left = W.violations([p for _, p, _ in picks], items)
    stat["left"] = len(left)
    return picks, stat


def run_vote(key, lang, mode, dry=False):
    """d3 = luna 3회 · s3 = sol 3회(INSTR_NEAREST). 1회차 판과 투표 판을 같이 채점."""
    from trlab import order_win as W
    site, model, nearest = ("luna", LUNA, False) if mode == "d3" else ("sol", SOL, True)
    P = prep(key, lang)
    wins = list(zip(P["win"]["mids"], P["win"]["windows"]))
    ids = {b.id: b for b in P["blocks"]}
    order_of = lambda bid: ids[bid].order if bid in ids else 10 ** 6
    prev = load_run(key, lang, mode) or {}
    runs, infos = [], []
    for k in range(3):
        old = (prev.get("runs") or [None] * 3)[k] if len(prev.get("runs") or []) > k else None
        if old is not None and not dry:
            runs.append({int(i): b for i, b in old["picks"].items()})
            infos.append(old["info"])
            continue
        got, info = ask(site, model, f"TRX3/{mode}/{key}-{lang}/{k + 1}", P["items"], wins, P["cand"], nearest=nearest, dry=dry)
        if got is None:
            if dry:
                return info
            save_run(key, lang, mode, {"mode": mode, "status": info["status"],
                                       "runs": [{"picks": {str(i): b for i, b in r.items()}, "info": f} for r, f in zip(runs, infos)]})
            return info
        runs.append(got)
        infos.append(info)
    pairs = [answer_pairs(r, P["items"], wins, ids) for r in runs]
    one = fill_dp(pairs[0], P["win"], "ai")
    voted = []
    for i in range(len(P["items"])):
        b, p, how = vote([pr[i] for pr in pairs], order_of)
        voted.append((b, p))
    und = [i for i in range(len(P["items"])) if voted[i] == (None, None)]
    vpicks = fill_dp(voted, P["win"], "vote")
    vpicks, vstat = fix_violations(site, model, f"TRX3/{mode}fix/{key}-{lang}", P, vpicks, wins, nearest)
    res = {"mode": mode, "status": "ok", "model": model, "undecided": und, "shaky": shaky(pairs),
           "violations": vstat, "runs": [{"picks": {str(i): b for i, b in r.items()}, "info": f} for r, f in zip(runs, infos)],
           "usd": round(sum(f.get("usd", 0) for f in infos) + vstat["usd"], 6)}
    p1 = make_pred(key, lang, P["blocks"], P["g1"]["pages"], P["items"], one, wins)
    pv = make_pred(key, lang, P["blocks"], P["g1"]["pages"], P["items"], vpicks, wins)
    res["one"] = {"grade": grade(key, lang, p1, P["blocks"]), "pred": p1,
                  "violations": len(W.violations([x["start_page"] for x in p1["items"]], P["items"]))}
    res["vote"] = {"grade": grade(key, lang, pv, P["blocks"]), "pred": pv}
    return save_run(key, lang, mode, res)


def ablation_wins(key, lang, name, P):
    """A1 = H1 좁힘 뺌(번호 있는 항목 ±1) · A2 = H2 뺌(번호 없는 항목은 TRX2 windows, 번호 있는 것은 H1 창) · A5 = 창은 d3."""
    from trlab import pagemap
    from trlab import toc_pick as TP
    items, pm = P["items"], P["pm"]
    npages = max(b.page for b in P["blocks"])
    base = list(zip(P["win"]["mids"], P["win"]["windows"]))
    if name == "A1":
        out = []
        for it, (mid, w) in zip(items, base):
            if it.get("printed_page") is not None and mid is not None:
                out.append((mid, [x for x in (mid - 1, mid, mid + 1) if 1 <= x <= npages]))
            else:
                out.append((mid, w))
        return out
    if name == "A2":
        simple = {int(k): v[0] for k, v in pm["map"].items()}
        tw = TP.windows(items, simple, pm["offset"], npages)
        return [((mid, pagemap.window(pm, it["printed_page"])) if it.get("printed_page") is not None else t)
                for it, (mid, _), t in zip(items, base, tw)]
    return base


def run_ablation(key, lang, name, dry=False):
    P = prep(key, lang)
    if name == "A5":
        P = dict(P, cand=get_cands(key, lang, "trx2"))
    wins = ablation_wins(key, lang, name, P)
    prev = load_run(key, lang, name)
    if prev and prev.get("status") == "ok" and not dry:
        got, info = {int(i): b for i, b in prev["picks"].items()}, prev["info"]
    else:
        got, info = ask("luna", LUNA, f"TRX3/{name}/{key}-{lang}", P["items"], wins, P["cand"], dry=dry)
        if got is None:
            return info
    ids = {b.id: b for b in P["blocks"]}
    pairs = answer_pairs(got, P["items"], wins, ids)
    pk = fill_dp(pairs, P["win"], "ai")
    pred = make_pred(key, lang, P["blocks"], P["g1"]["pages"], P["items"], pk, wins)
    return save_run(key, lang, name, {"mode": name, "status": "ok", "picks": {str(i): b for i, b in got.items()},
                                      "info": info, "usd": info.get("usd", 0.0), "windows": [w[1] for w in wins],
                                      "grade": grade(key, lang, pred, P["blocks"]), "pred": pred})


# ---------- best ----------
def run_best(key, lang, dry=False):
    """d3 투표 → undecided 만 H3 → H3 0 이거나 블록 못 이은 것만 sol nearest 1호출 → 그래도 없으면 DP → 거스름은 DP."""
    from trlab import order_win as W
    P = prep(key, lang)
    wins = list(zip(P["win"]["mids"], P["win"]["windows"]))
    d3 = load_run(key, lang, "d3")
    h3 = load_run(key, lang, "h3")
    ids = {b.id: b for b in P["blocks"]}
    order_of = lambda bid: ids[bid].order if bid in ids else 10 ** 6
    pairs = [answer_pairs({int(i): b for i, b in r["picks"].items()}, P["items"], wins, ids) for r in d3["runs"]]
    picks, need_sol = [], []
    for i in range(len(P["items"])):
        b, p, _ = vote([pr[i] for pr in pairs], order_of)
        if p is not None:
            picks.append((b, p, "vote"))
            continue
        a = (h3 or {}).get("answers", {}).get(str(i)) if h3 else None
        if a and a.get("page"):
            picks.append((a.get("block"), a["page"], "h3"))
            continue
        picks.append(None)
        need_sol.append(i)
    info = {"status": "skip", "usd": 0.0}
    if need_sol:
        got, info = ask("sol", SOL, f"TRX3/best/{key}-{lang}", P["items"], wins, P["cand"], need_sol, nearest=True, dry=dry)
        if dry:
            return info
        pr = answer_pairs(got or {}, P["items"], wins, ids)
        for i in need_sol:
            picks[i] = (pr[i][0], pr[i][1], "sol") if pr[i][0] else None
    for i, x in enumerate(picks):
        if x is None:
            d = P["win"]["dp"][i]
            picks[i] = (d["block"], d["page"], "dp")
    bad = W.violations([p for _, p, _ in picks], P["items"])
    for i in bad:
        d = P["win"]["dp"][i]
        picks[i] = (d["block"], d["page"], "dp")
    pred = make_pred(key, lang, P["blocks"], P["g1"]["pages"], P["items"], picks, wins)
    return save_run(key, lang, "best", {"mode": "best", "status": "ok", "need_sol": need_sol, "sol": info,
                                        "violations_to_dp": bad, "usd": info.get("usd", 0.0),
                                        "grade": grade(key, lang, pred, P["blocks"]), "pred": pred})


def fixed(before, after):
    """방법별 고친 항목: before 에서 시작 A∨B 가 틀린 항목 중 after 에서 맞은 것(fixed), 그 반대(broke)."""
    b = {r["n"]: r["start_ab"] for r in before["rows"]}
    a = {r["n"]: r["start_ab"] for r in after["rows"]}
    return {"fixed": [n for n in b if not b[n] and a.get(n)], "broke": [n for n in b if b[n] and not a.get(n)]}


def main(argv):
    cmd = argv[0]
    if cmd == "rescore":
        res = rescore()
        for k, v in res.items():
            print(k, v["found"], v["start_ab"], v["end_ab"], v["block"], "간지", v["divider_start_ab"], "틀림", v["wrong_start"])
        return
    key, lang = argv[1], argv[2]
    dry = "--dry-run" in argv
    if cmd == "prep":
        P = prep(key, lang)
        print(key, lang, "dp 거스름", P["win"]["dp_violations"])
        return
    if cmd == "c3":
        res = run_c3(key, lang)
    elif cmd in ("d3", "s3"):
        res = run_vote(key, lang, cmd, dry)
    elif cmd in ("A1", "A2", "A5"):
        res = run_ablation(key, lang, cmd, dry)
    elif cmd == "best":
        res = run_best(key, lang, dry)
    else:
        raise SystemExit(f"모름: {cmd}")
    if res.get("status") != "ok":
        print(key, lang, cmd, json.dumps({k: v for k, v in res.items() if k != "calls"}, ensure_ascii=False))
        return
    g = res.get("grade") or res["vote"]["grade"]
    extra = f" 1회차 {brief_line(res['one']['grade'])} 흔들림 {res['shaky']} 거스름 {res['violations']}" if "vote" in res else ""
    print(key, lang, cmd, brief_line(g), "틀림", g["wrong_start"], "USD", res.get("usd"), extra)


def brief_line(g):
    return f"S {g['start_ab']} E {g['end_ab']} B {g['block']} 간지 {g['divider_start_ab']}"


if __name__ == "__main__":
    main(sys.argv[1:])
