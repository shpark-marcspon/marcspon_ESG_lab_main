"""TRX4 — 번호 없는 목차에 책 전체 신호. N-(번호 가린 판) · K1(닻 자르기 빼기) · K2·K3·K4 를 DP 점수에 · 판 · best4 · 장부.

책의 처지: kb 두 판 = 반쯤 본(규칙·문턱은 kb 로 짬). N- 여섯 판(번호 있는 책의 G1 번호를 지운 판, 이름 꼬리 -N) =
번호 가린 판(정답을 본 책이라 블라인드 아님) — 규칙을 얼린 뒤(out/trx4/first-run.txt) 처음 돌린다.
셈은 TRX3 tx3_pick.score_ab 그대로(시작 A∨B 으뜸 · 끝 A∨B · 블록). 기준선 TRX3 best · A2, N- 은 N- c3.

자리 찾기 read4(kind, name) = OUT4/<kind> → out/trx3 → out/trx2 → out/trx1 (<name>.json | <name>/document.json).
없으면 만들지 않는다(멈춤). 쓰는 곳은 OUT4 하나.

판(코드만): c3(= TRX3 c3, N- 면 번호 가린) · c4:<methods>(DP 점수에 K2·K3·K4 bonus). K1 은 창만 바꾼다
(코드만 판의 고름은 같다 — 창 덮음으로 비교). AI 판: d4:<methods>(luna, K1a 창 + 새 열) · best4α·β · k5(thumb_vision).
실호출: 장부 OUT4/calls.jsonl, 자리 상한 CAPS4 = luna 0.1 · sol 0.4 · 합 0.5. 누적 + 잡아 둔 몫 + 예상 > 상한이면 보내지 않음.

명령(py/ 에서, --group scope3colab):
  python -m trlab.tx4_pick c3 <판>                 → OUT4/runs/<판>-c3.json   (판 = kb-ko · kb-en · nb2-ko-N …)
  python -m trlab.tx4_pick c4 <판> <methods>       → OUT4/runs/<판>-c4-<methods>.json (methods = K2 · K2+K3 · K2+K3+K4 …)
  python -m trlab.tx4_pick win <판>                → OUT4/win/<판>-<창 판>.json + 창 덮음 표
  python -m trlab.tx4_pick d4 <판> <methods> [--dry-run]   (실호출은 --with openai==3.26.0)
  python -m trlab.tx4_pick best4 <판> [--dry-run]
"""

import json
import os
import sys
import threading
from pathlib import Path

from trlab import common
from trlab.common import load_doc

ROOT = common.ROOT
OUT4 = Path(os.environ.get("TRX4_OUT", ROOT / "esg-lab" / "out" / "trx4"))
OUT3 = ROOT / "esg-lab" / "out" / "trx3"
OUT2 = ROOT / "esg-lab" / "out" / "trx2"
OUT1 = ROOT / "esg-lab" / "out" / "trx1"

KB = [("kb", "ko"), ("kb", "en")]
NBOOKS = [("nb2", "ko"), ("nb", "ko"), ("cos", "ko"), ("cos", "en"), ("sam", "ko"), ("sam", "en")]
LUNA, SOL = "gpt-6-luna", "gpt-6.1-sol"
CAPS4 = {"luna": 0.1, "sol": 0.4}
TOTAL_CAP4 = 0.5
SITE_PREFIX4 = {"luna": ("TRX4/d4", "TRX4/k5luna/"), "sol": ("TRX4/k5sol/", "TRX4/best4/")}
K4_PEN = 0.1
K4_MULT = 3.0


def name_of(key, lang, blind):
    """판 이름 — N- 면 -N 꼬리(kb 는 원래 번호가 없어 꼬리 없음)."""
    return f"{key}-{lang}" + ("-N" if blind and key != "kb" else "")


def parse_name(name):
    parts = name.split("-")
    return parts[0], parts[1], len(parts) > 2 and parts[2] == "N"


def all_names():
    return [name_of(k, l, False) for k, l in KB] + [name_of(k, l, True) for k, l in NBOOKS]


# ---------- 자리 ----------
def read4(kind, name):
    """kind 폴더에서 name(.json | /document.json) 을 OUT4 → out/trx3 → out/trx2 → out/trx1 차례로. 없으면 None."""
    for base in (OUT4, OUT3, OUT2, OUT1):
        for p in (base / kind / f"{name}.json", base / kind / name / "document.json"):
            if p.exists():
                return p
    return None


def _need(kind, name):
    p = read4(kind, name)
    if p is None:
        raise SystemExit(f"read4 로 못 찾음: {kind}/{name} — 만들지 않고 멈춤(감독에게)")
    return p


def blind(items):
    """복사본의 printed_page 를 전부 None 으로(번호 가린 판)."""
    return [dict(it, printed_page=None) for it in items]


def load4(key, lang, blind_=False):
    """→ {blocks, pm, cand, g1, items, attrs, toc_pages}. 모두 읽기만."""
    bk = f"{key}-{lang}"
    blocks = load_doc(_need("parse", bk))
    pm = json.load(open(_need("pagemap", bk)))
    cand = json.load(open(_need("cands", f"{bk}-union")))
    g1 = json.load(open(_need("g1", bk)))
    attrs = json.load(open(_need("attrs", bk)))
    items = blind(g1["items"]) if blind_ else [dict(it) for it in g1["items"]]
    return {"key": key, "lang": lang, "blind": blind_, "blocks": blocks, "pm": pm, "cand": cand, "g1": g1,
            "items": items, "attrs": attrs, "toc_pages": g1.get("pages") or []}


def gold_for(key, lang):
    from trlab import tx3_pick as T
    return T.gold_for(key, lang)


# ---------- 장부(OUT4, 자리 상한) ----------
_LOCK = threading.Lock()
_RESERVED = {"luna": 0.0, "sol": 0.0}


def calls():
    p = OUT4 / "calls.jsonl"
    return [json.loads(x) for x in open(p) if x.strip()] if p.exists() else []


def site_spent(site, rows=None):
    rows = calls() if rows is None else rows
    return sum(r.get("costUsd", 0) for r in rows if not r.get("dry_run") and r.get("tag", "").startswith(SITE_PREFIX4[site]))


def total_spent(rows=None):
    rows = calls() if rows is None else rows
    return sum(r.get("costUsd", 0) for r in rows if not r.get("dry_run"))


def reserve(site, usd):
    """누적 + 잡아 둔 몫 + 예상 > 자리 상한(또는 합 0.5)이면 False — 보내지 않는다."""
    with _LOCK:
        rows = calls()
        held = sum(_RESERVED.values())
        if site_spent(site, rows) + _RESERVED[site] + usd > CAPS4[site] or total_spent(rows) + held + usd > TOTAL_CAP4:
            return False
        _RESERVED[site] += usd
        return True


def send(site, model, instr, text, schema, tag, est_usd, **extra):
    """semgraph.llm.respond 를 OUT4/.scratch 로 부르고, 줄(+ estimateUsd·chars·items·px·images)은 OUT4/calls.jsonl 에 한 줄."""
    from semgraph import llm
    scratch = OUT4 / ".scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        raw, row = llm.respond(model, instr, text, schema, scratch, tag, effort="low")
    finally:
        with _LOCK:
            _RESERVED[site] = max(0.0, _RESERVED[site] - est_usd)
    row.update(extra, estimateUsd=round(est_usd, 6))
    with _LOCK:
        OUT4.mkdir(parents=True, exist_ok=True)
        with open(OUT4 / "calls.jsonl", "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return raw, row


def tok_per_char(model, rows=None):
    """첫 실측 토큰/글자와 출력 토큰 — OUT4 장부의 글 호출(이미지 없음), 없으면 out/trx3 장부의 d3/s3 첫 줄."""
    rows = calls() if rows is None else rows
    mine = [r for r in rows if r.get("model") == model and r.get("chars") and not r.get("images") and not r.get("dry_run")]
    if not mine:
        p = OUT3 / "calls.jsonl"
        if p.exists():
            r3 = [json.loads(x) for x in open(p) if x.strip()]
            mine = [r for r in r3 if r.get("model") == model and r.get("chars")
                    and r.get("tag", "").startswith(("TRX3/d3/", "TRX3/s3/"))]
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


# ---------- 창(K1) ----------
WIN_MODES = ("trx3", "K1a", "K1b")


def win_args(mode):
    return {"trx3": dict(anchor_cut=True), "K1a": dict(anchor_cut=False, hi_dp=True),
            "K1b": dict(anchor_cut=False, hi_dp=False)}[mode]


def windows_for(P, dp_pages, mode):
    from trlab import order_win as W
    npages = max(b.page for b in P["blocks"])
    wins, bounds = W.windows3(P["items"], P["pm"], dp_pages, npages, **win_args(mode))
    return wins, bounds


def coverage(wins, gold, items):
    """창 덮음: 정답 짝 항목 중 정답 시작(또는 alt 쪽 · 간지면 +1)이 창 안인 수 / 짝 수, 창 평균 쪽."""
    from rapidfuzz import fuzz
    hit = n = 0
    used = set()
    for g in gold["items"]:
        best, bs = None, 0
        for i, it in enumerate(items):
            if i in used:
                continue
            s = fuzz.ratio(g["text"].lower(), it["text"].lower())
            if s > bs:
                best, bs = i, s
        if best is None or bs < 80:
            continue
        used.add(best)
        n += 1
        ok = {g["start_page"]} | ({g["start_page"] + 1} if g.get("divider") else set())
        hit += bool(ok & set(wins[best][1]))
    return {"hit": hit, "n": n, "mean_pages": round(sum(len(w[1]) for w in wins) / max(1, len(wins)), 1)}


# ---------- K4 간격 ----------
def k4_bonus(items, dp_pages, conf_idx, cand, npages):
    """확실 항목의 DP 쪽 간격(앞 항목 DP 쪽부터) 중앙값 m. 항목 i 후보 쪽 p 의 g = p − 앞 항목 DP 쪽 > 3m 이면 −K4_PEN.
    → (bonus[i] = {블록 id: −0.1}, m)"""
    gaps = sorted(dp_pages[i] - dp_pages[i - 1] for i in conf_idx
                  if i > 0 and dp_pages[i] is not None and dp_pages[i - 1] is not None)
    if not gaps:
        return [dict() for _ in items], None
    m = gaps[len(gaps) // 2] if len(gaps) % 2 else (gaps[len(gaps) // 2 - 1] + gaps[len(gaps) // 2]) / 2
    m = max(1.0, float(m))
    out = []
    for i in range(len(items)):
        prev = next((dp_pages[j] for j in range(i - 1, -1, -1) if dp_pages[j] is not None), None)
        b = {}
        if prev is not None:
            for bid, r in cand["rows"].items():
                if r["page"] - prev > K4_MULT * m:
                    b[bid] = -K4_PEN
        out.append(b)
    return out, m


def merge_bonus(*bs):
    n = max(len(b) for b in bs)
    out = [dict() for _ in range(n)]
    for b in bs:
        for i, d in enumerate(b):
            for k, v in d.items():
                out[i][k] = out[i].get(k, 0.0) + v
    return out


# ---------- 준비 ----------
def methods_of(s):
    return [m for m in (s or "").split("+") if m]


def prep4(key, lang, blind_=False, methods=(), win_mode="K1a"):
    """pm·cands·g1(blind)·blocks → DP 첫 판(bonus 없음) → 확실 항목 → bonus(K2·K3·K4 중 methods) → plan(bonus) → 창(win_mode).
    → P(+ win, base_win, bonus, shape, runhead, k4m)"""
    from trlab import order_win as W
    P = load4(key, lang, blind_)
    items, blocks, cand = P["items"], P["blocks"], P["cand"]
    base = W.plan(items, P["pm"], cand, blocks, P["toc_pages"])
    methods = list(methods)
    bonuses, extra = [], {}
    if any(m in methods for m in ("K2", "K4")):
        from trlab import sec_shape as S
        conf = S.confident(items, base["dp"], cand, blocks)
        extra["confident"] = conf
        if "K2" in methods:
            sh = S.book_shape(P, conf)
            extra["shape"] = sh
            bonuses.append(sh["bonus"])
        if "K4" in methods:
            npages = max(b.page for b in blocks)
            kb, m = k4_bonus(items, [d["page"] for d in base["dp"]], conf, cand, npages)
            extra["k4m"] = m
            bonuses.append(kb)
    if "K3" in methods:
        from trlab import runhead as R
        rh = R.book_bonus(P)
        extra["runhead"] = rh
        bonuses.append(rh["bonus"])
    bonus = merge_bonus(*bonuses) if bonuses else None
    win = W.plan(items, P["pm"], cand, blocks, P["toc_pages"], bonus=bonus) if bonus else base
    pages = [d["page"] for d in win["dp"]]
    wins, bounds = windows_for(P, pages, win_mode)
    win = dict(win, windows=[w[1] for w in wins], mids=[w[0] for w in wins], bounds=bounds, win_mode=win_mode)
    P.update(win=win, base_win=base, bonus=bonus, methods=methods, **extra)
    return P


def save_win(name, P):
    tag = "+".join(P["methods"]) or "none"
    (OUT4 / "win").mkdir(parents=True, exist_ok=True)
    bsum = [round(sum(b.values()), 3) if b else 0.0 for b in (P["bonus"] or [{} for _ in P["items"]])]
    out = {"book": name, "methods": P["methods"], "win_mode": P["win"]["win_mode"], "dp": P["win"]["dp"],
           "windows": P["win"]["windows"], "bounds": P["win"]["bounds"], "dp_violations": P["win"]["dp_violations"],
           "bonus_sum": bsum}
    json.dump(out, open(OUT4 / "win" / f"{name}-{tag}-{P['win']['win_mode']}.json", "w"), ensure_ascii=False, indent=1,
              sort_keys=True)
    return out


# ---------- 산출 · 채점 ----------
def make_pred(P, picks):
    from trlab import tx3_pick as T
    wins = list(zip(P["win"]["mids"], P["win"]["windows"]))
    return T.make_pred(P["key"], P["lang"], P["blocks"], P["toc_pages"], P["items"], picks, wins)


def grade(P, pred):
    from trlab import tx3_pick as T
    return T.grade(P["key"], P["lang"], pred, P["blocks"])


def dp_picks(win):
    return [(d["block"], d["page"], "dp") for d in win["dp"]]


def save_run(name, mode, res):
    (OUT4 / "runs").mkdir(parents=True, exist_ok=True)
    json.dump(res, open(OUT4 / "runs" / f"{name}-{mode}.json", "w"), ensure_ascii=False, indent=1)
    return res


def load_run(name, mode):
    p = OUT4 / "runs" / f"{name}-{mode}.json"
    return json.load(open(p)) if p.exists() else None


def run_code(name, methods=()):
    """c3(methods 없음) · c4:<methods> — DP 고름 그대로(코드만, 호출 0)."""
    key, lang, bl = parse_name(name)
    P = prep4(key, lang, bl, methods, win_mode="trx3" if not methods else "K1a")
    save_win(name, P)
    pred = make_pred(P, dp_picks(P["win"]))
    mode = "c3" if not methods else "c4-" + "+".join(methods)
    res = {"mode": mode, "book": name, "status": "ok", "usd": 0.0, "grade": grade(P, pred), "pred": pred}
    if "K4" in P["methods"]:
        res["k4m"] = P.get("k4m")
    return save_run(name, mode, res)


# ---------- K1 창 덮음(코드만 판의 K1 비교) ----------
def run_win(name):
    """창 판 셋(trx3 닻 · K1a 다음 DP · K1b 책 끝)의 덮음 — DP 는 bonus 없음(c3 와 같음)."""
    key, lang, bl = parse_name(name)
    gold = gold_for(key, lang)
    out = {}
    for mode in WIN_MODES:
        P = prep4(key, lang, bl, (), win_mode=mode)
        save_win(name, P)
        out[mode] = coverage(list(zip(P["win"]["mids"], P["win"]["windows"])), gold, P["items"])
    (OUT4 / "win").mkdir(parents=True, exist_ok=True)
    json.dump(out, open(OUT4 / "win" / f"{name}-coverage.json", "w"), ensure_ascii=False, indent=1, sort_keys=True)
    return out


# ---------- luna d4 ----------
COLS4_TAIL = " | start-shape score 0..1"
HINT4 = ("`header page` is where the running header/nav bar first marks this section; `start-shape` is how much the block "
         "looks like this report's other section openings — both are hints, not proof.")


def instr4(model, shape=False, hint=False, nearest=False):
    from trlab import toc_pick as TP
    s = TP.INSTR.replace("COLUMNS", TP.COLS_ATTR + (COLS4_TAIL if shape else ""))
    if nearest:
        s += "\n" + TP.INSTR_NEAREST
    if hint:
        s += "\n" + HINT4
    return s


def prompt4(items, wins, cand, divs, idxs=None, header=None, shapes=None):
    """toc_pick.prompt 꼴 + 항목 줄 끝 header page(header 가 있으면) + 후보 줄 끝 shape(shapes 가 있으면)."""
    from trlab import cands as C
    from trlab import toc_pick as TP
    idxs = list(range(len(items))) if idxs is None else list(idxs)
    pages = sorted({p for i in idxs for p in wins[i][1]})
    hcol = " | header page" if header is not None else ""
    ent = []
    for i in idxs:
        it = items[i]
        pp = it.get("printed_page") if it.get("printed_page") is not None else "-"
        line = f"{i} | {it.get('level')} | {pp} | {it['text']} | {wins[i][1][0]}-{wins[i][1][-1]}"
        if header is not None:
            line += f" | {header[i] if header[i] is not None else '-'}"
        ent.append(line)
    ps = set(pages)
    rows = sorted(((r["page"], bid) for bid, r in cand["rows"].items() if r["page"] in ps),
                  key=lambda x: (x[0], int(x[1].split("-b")[1]) if "-b" in x[1] else 0))
    lines = []
    for _, bid in rows:
        ln = C.line(bid, cand["rows"][bid], True)
        if shapes is not None:
            ln += f" | {shapes.get(bid, 0.0):.2f}"
        lines.append(ln)
    dv = [p for p in pages if p in divs]
    cols = TP.COLS_ATTR + (COLS4_TAIL if shapes is not None else "")
    return (f"## Entries (idx | level | printed page | text | window pdf pages{hcol})\n" + "\n".join(ent)
            + f"\n\n## Divider pages\n{', '.join(map(str, dv)) or '-'}\n\n## Candidate blocks ({cols})\n"
            + "\n".join(lines))


def ask4(site, model, tag, P, wins, idxs=None, nearest=False, dry=False):
    """한 판 한 호출(60k 토큰 넘으면 15항목 쪼갬). P 의 shape·runhead 가 있으면 새 열. → (picks{idx: 블록 id}, info)"""
    from trlab import toc_pick as TP
    from trlab import tx3_pick as T
    items, cand = P["items"], P["cand"]
    shapes = P["shape"]["scores"] if P.get("shape") else None
    header = P["runhead"]["anchors"] if P.get("runhead") else None
    instr = instr4(model, shapes is not None, shapes is not None or header is not None, nearest)
    divs = T.divs_of(cand)
    idxs = list(range(len(items))) if idxs is None else list(idxs)
    k, _ = tok_per_char(model)
    full = prompt4(items, wins, cand, divs, idxs, header, shapes)
    chunks = [idxs] if len(full) * k <= T.CHUNK_TOKENS else [idxs[i:i + 15] for i in range(0, len(idxs), 15)]
    texts = [prompt4(items, wins, cand, divs, c, header, shapes) for c in chunks]
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


def settle(P, picks_raw, src):
    """AI 답 → 창 안 블록만 → 빈 것은 DP(번호 없음)/창 가운데 → 거스름은 DP(다시 묻기 없음). → (picks, 거스름 수)"""
    from trlab import order_win as W
    from trlab import tx3_pick as T
    wins = list(zip(P["win"]["mids"], P["win"]["windows"]))
    ids = {b.id: b for b in P["blocks"]}
    pairs = T.answer_pairs(picks_raw, P["items"], wins, ids)
    pk = T.fill_dp(pairs, P["win"], src)
    bad = W.violations([p for _, p, _ in pk], P["items"])
    for i in bad:
        d = P["win"]["dp"][i]
        pk[i] = (d["block"], d["page"], "dp")
    return pk, bad


def d4_mode(methods):
    return "d4-" + "+".join(methods)


def run_d4(name, methods, dry=False):
    """luna 1호출 — methods 는 K1 을 꼭 포함(창 = K1a). K2 → shape 열, K3 → header page 열, K4 → DP 점수만."""
    key, lang, bl = parse_name(name)
    rest = [m for m in methods if m != "K1"]
    P = prep4(key, lang, bl, rest, win_mode="K1a")
    P["methods"] = list(methods)
    save_win(name, P)
    mode = d4_mode(methods)
    prev = load_run(name, mode)
    if prev and prev.get("status") == "ok" and not dry:
        got, info = {int(i): b for i, b in prev["picks"].items()}, prev["info"]
    else:
        wins = list(zip(P["win"]["mids"], P["win"]["windows"]))
        got, info = ask4("luna", LUNA, f"TRX4/{mode}/{name}", P, wins, dry=dry)
        if got is None:
            return dict(info, mode=mode, book=name)
    pk, bad = settle(P, got, "ai")
    pred = make_pred(P, pk)
    return save_run(name, mode, {"mode": mode, "book": name, "status": "ok", "picks": {str(i): b for i, b in got.items()},
                                 "info": {k: v for k, v in info.items() if k != "calls"}, "usd": info.get("usd", 0.0),
                                 "violations_to_dp": bad, "windows": P["win"]["windows"],
                                 "fill": sum(1 for x in pk if x[2] != "ai"), "grade": grade(P, pred), "pred": pred})


# ---------- best4 ----------
BEST_BASE = ("K1", "K2", "K3")


def alpha_merge(base_pk, k5_by, items, cand, ids):
    """best4α: base 고름과 K5 답 쪽이 다른 항목만 K5 답(블록 = thumb_vision.to_block). 바꾼 뒤 거스른 항목은 base 로.
    → (picks, 어긋남 idx, 되돌린 idx)"""
    from trlab import order_win as W
    from trlab import thumb_vision as TV
    diff = [i for i in range(len(items)) if k5_by.get(i) and k5_by[i].get("page") and k5_by[i]["page"] != base_pk[i][1]]
    alpha = list(base_pk)
    for i in diff:
        bid, _ = TV.to_block(items[i], k5_by[i]["page"], cand, ids)
        alpha[i] = (bid, k5_by[i]["page"], "k5")
    bad = W.violations([p for _, p, _ in alpha], items)
    for i in bad:
        alpha[i] = base_pk[i]
    return alpha, diff, bad


def run_best4(name, dry=False):
    """α = d4:K1+K2+K3 고름에서 K5 sol 답과 쪽이 다른 항목은 K5 답(호출 0).
    β = 그 어긋남 항목만 sol 되묻기 1호출(창 = {luna 쪽, K5 쪽} 각 ±1, nearest + 새 열). 어긋남 0 이면 호출 없음."""
    from trlab import order_win as W
    key, lang, bl = parse_name(name)
    P = prep4(key, lang, bl, ["K2", "K3"], win_mode="K1a")
    d4 = load_run(name, d4_mode(BEST_BASE))
    k5 = json.load(open(OUT4 / "k5" / f"{name}-sol.json"))
    if not d4 or k5.get("status") != "ok":
        raise SystemExit(f"best4 바닥 없음: {name}")
    base_pk = [(r["start_block"], r["start_page"], r["src"]) for r in d4["pred"]["items"]]
    ids = {b.id: b for b in P["blocks"]}
    by = {x["idx"]: x for x in k5["answer"].get("items", [])}
    npages = max(b.page for b in P["blocks"])
    alpha, diff, bad_a = alpha_merge(base_pk, by, P["items"], P["cand"], ids)
    Pall = dict(P, win=dict(P["win"], windows=[list(range(1, npages + 1))] * len(P["items"]),
                            mids=[None] * len(P["items"])))
    pa = make_pred(Pall, alpha)
    res = {"mode": "best4", "book": name, "status": "ok", "diff": diff, "alpha_reverted": bad_a,
           "alpha": {"grade": grade(P, pa), "pred": pa}}
    # β
    beta, info = list(base_pk), {"status": "skip", "usd": 0.0}
    if diff:
        wins = list(zip(P["win"]["mids"], P["win"]["windows"]))
        w2 = list(wins)
        for i in diff:
            ps = sorted({q for c in (base_pk[i][1], by[i]["page"]) if c for q in (c - 1, c, c + 1) if 1 <= q <= npages})
            w2[i] = (None, ps)
        prev = (load_run(name, "best4") or {}).get("beta_info")
        if prev and prev.get("status") == "ok" and not dry:
            got, info = {int(i): b for i, b in prev["picks"].items()}, prev
        else:
            got, info = ask4("sol", SOL, f"TRX4/best4/{name}", P, w2, diff, nearest=True, dry=dry)
            if dry:
                return dict(info, diff=diff)
            info = {k: v for k, v in info.items() if k != "calls"}
            info["picks"] = {str(i): b for i, b in (got or {}).items()}
        from trlab import tx3_pick as T
        pr = T.answer_pairs(got or {}, P["items"], w2, ids)
        for i in diff:
            if pr[i][0]:
                beta[i] = (pr[i][0], pr[i][1], "sol")
        bad_b = W.violations([p for _, p, _ in beta], P["items"])
        for i in bad_b:
            beta[i] = base_pk[i]
        res["beta_reverted"] = bad_b
    pb = make_pred(Pall, beta)
    res.update(beta={"grade": grade(P, pb), "pred": pb}, beta_info=info, usd=info.get("usd", 0.0))
    return save_run(name, "best4", res)


def brief_line(g):
    return f"S {g['start_ab']} E {g['end_ab']} B {g['block']} 간지 {g['divider_start_ab']}"


def main(argv):
    cmd = argv[0]
    if cmd in ("c3", "c4"):
        name = argv[1]
        res = run_code(name, methods_of(argv[2]) if cmd == "c4" else ())
        g = res["grade"]
        print(name, res["mode"], brief_line(g), "틀림", g["wrong_start"])
        return
    if cmd == "win":
        for mode, v in run_win(argv[1]).items():
            print(argv[1], mode, v)
        return
    dry = "--dry-run" in argv
    if cmd == "d4":
        res = run_d4(argv[1], methods_of(argv[2]), dry)
        if res.get("status") != "ok":
            print(argv[1], res.get("mode"), json.dumps({k: v for k, v in res.items() if k != "calls"}, ensure_ascii=False))
            return
        g = res["grade"]
        print(argv[1], res["mode"], brief_line(g), "틀림", g["wrong_start"], "메움", res["fill"], "거스름", len(res["violations_to_dp"]),
              "USD", res["usd"])
        return
    if cmd == "best4":
        res = run_best4(argv[1], dry)
        if res.get("status") != "ok":
            print(argv[1], "best4", json.dumps(res, ensure_ascii=False))
            return
        for k in ("alpha", "beta"):
            g = res[k]["grade"]
            print(argv[1], "best4" + k[0], brief_line(g), "틀림", g["wrong_start"], "어긋남", res["diff"], "USD", res["usd"])
        return
    raise SystemExit(f"모름: {cmd}")


if __name__ == "__main__":
    main(sys.argv[1:])
