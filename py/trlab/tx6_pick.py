"""TRX6 — PDF 문단 다시 잇기 측정: 자리(OUT6·read6) · 줄 자료 · 줄↔블록 사상 · 렌더 · 표본 고름 · 정답 읽기 · 셈 · 판 돌리기 · 장부.

책의 처지: cos 국문 표본 = 개발 셋(규칙·문턱). kb·sam 국문 표본 = 보류 셋(규칙을 얼린 뒤 out/trx6/first-run.txt 처음 돌림).
정답(prework/gold-trx6/para/<키>-ko.json)은 규칙을 짜기 전에 만들고 얼린다(freeze.txt). 줄 id 는 lines/ 의 L<k>.

자리 찾기 read6(kind, name) = OUT6/<kind>/<name>.json | <name>/document.json → tx4_pick.read4(out/trx4 → 3 → 2 → 1).
없으면 만들지 않는다(멈춤). 쓰는 곳은 OUT6 하나. layout 1 분해는 OUT6/parse/<키>-ko-L1, layout 2 는 read6("parse", "<키>-ko").

명령(py/ 에서, --group scope3colab):
  python -m trlab.tx6_pick stats <키>              → OUT6/stats/<키>-ko.json (쪽마다 본문 글자 수·2단·표·설명 — 표본 고름 재료)
  python -m trlab.tx6_pick lines <키> <쪽>…        → OUT6/lines/<키>-ko-p<n>.json + render/<키>-ko/p<nnnn>.png·-lines.png
  python -m trlab.tx6_pick run <키>-ko <판> [th json]  → OUT6/runs/<키>-ko-<판>.json (판 = L1 · L2 · G0.65 … · R · R-R1 … · R7j · P2.0)
  python -m trlab.tx6_pick runall <키>                 → 판 전부(cos 는 P 격자도)
"""

import json
import os
import re
import sys
import threading
from collections import Counter
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium

from trlab import common, glyphs, para_join, thumb_vision, tx4_pick

ROOT = common.ROOT
OUT6 = Path(os.environ.get("TRX6_OUT", ROOT / "esg-lab" / "out" / "trx6"))
GOLD6 = Path(os.environ.get("TRX6_GOLD", ROOT / "prework" / "gold-trx6"))
BOOKS = ("cos", "kb", "sam")
LANG = "ko"
DPI = 100
LUNA = tx4_pick.LUNA
CAP6 = 0.1  # 이 밴드 luna 상한(USD)
SITE_PREFIX6 = "TRX6/luna-pairs/"
KINDS = ("body", "heading", "table", "caption", "note", "nav", "other")


# ---------- 자리 ----------
def read6(kind, name):
    for p in (OUT6 / kind / f"{name}.json", OUT6 / kind / name / "document.json"):
        if p.exists():
            return p
    return tx4_pick.read4(kind, name)


def _need(kind, name):
    p = read6(kind, name)
    if p is None:
        raise SystemExit(f"read6 로 못 찾음: {kind}/{name} — 만들지 않고 멈춤(감독에게)")
    return p


def doc_path(key, layout):
    return _need("parse", f"{key}-{LANG}" + ("-L1" if layout == 1 else ""))


def base_size(key):
    return float(glyphs.load_attrs(key, LANG)["base"]["size"])


def pdf_of(key):
    return pdfium.PdfDocument(str(glyphs.pdf_path(key, LANG)))


# ---------- 줄 자료 ----------
def page_status(page, pinfo):
    w, h = page.get_size()
    if abs(w - pinfo["width"]) > 0.5 or abs(h - pinfo["height"]) > 0.5:
        return "size_mismatch"
    return "ok"


def page_lines(key, pno, pdf=None, base=None):
    """쪽 하나의 줄 자료(규칙 없음 — 기하만). {book, page, status, base, width, height, cols, lines}."""
    own = pdf is None
    pdf = pdf or pdf_of(key)
    base = base or base_size(key)
    try:
        page = pdf[pno - 1]
        w, h = page.get_size()
        gl = para_join.page_glyphs(page)
        page.close()
    finally:
        if own:
            pdf.close()
    ls = para_join.lines(gl)
    cols = para_join.columns(ls, base)
    st = "ok" if ls else "no_text"
    return {"book": key, "lang": LANG, "page": pno, "status": st, "base": base, "width": round(w, 1),
            "height": round(h, 1), "cols": cols, "lines": ls}


def load_blocks(key, layout):
    """document.json → 쪽 → (글 블록 목록, 표 bbox 목록). 블록은 common.load_doc(겹침 접음)."""
    p = doc_path(key, layout)
    doc = json.load(open(p))
    tables = {}
    for t in doc["tables"]:
        tables.setdefault(t["page"], []).append((t["id"], tuple(t["bbox"])))
    blocks = {}
    for b in common.load_doc(p):
        blocks.setdefault(b.page, []).append(b)
    return blocks, tables


def map_lines(ls, blocks, tables, pad=1.0):
    """줄 → 블록 id | 'table' | 'out_of_block'. 줄 가운데(y, (x0+x1)/2)가 블록 bbox(±pad) 안이면 그 블록(둘 이상이면 넓이
    작은 쪽), 표 bbox 안이면 table(표 블록이 글 블록보다 먼저)."""
    out = {}
    tb = sorted(((b.bbox[2] - b.bbox[0]) * (b.bbox[3] - b.bbox[1]), b.id, b.bbox) for b in blocks if b.kind != "table")
    for L in ls:
        x, y = (L["x0"] + L["x1"]) / 2, L["y"]
        hit = None
        for _tid, (x0, y0, x1, y1) in tables:
            if x0 - pad <= x <= x1 + pad and y0 - pad <= y <= y1 + pad:
                hit = "table"
                break
        if hit is None:
            for _, bid, (x0, y0, x1, y1) in tb:
                if x0 - pad <= x <= x1 + pad and y0 - pad <= y <= y1 + pad:
                    hit = bid
                    break
        out[L["id"]] = hit or "out_of_block"
    return out


# ---------- 렌더 ----------
def render_page(pdf, pno):
    pg = pdf[pno - 1]
    a = pg.render(scale=DPI / 72).to_numpy()
    img = np.ascontiguousarray(a[:, :, 2::-1])
    pg.close()
    return img


def draw_lines(img, ls, color=(220, 0, 0)):
    """줄 상자 테두리 1px + 왼쪽 밖에 줄 번호(3×5 비트맵 ×2, 빨강)."""
    s = DPI / 72
    H, W = img.shape[:2]
    out = img.copy()
    for L in ls:
        x0, x1 = int(L["x0"] * s), int(L["x1"] * s)
        y0, y1 = int((L["y"] - 0.6 * L["size"]) * s), int((L["y"] + 0.6 * L["size"]) * s)
        x0, x1 = max(0, x0), min(W - 1, x1)
        y0, y1 = max(0, y0), min(H - 1, y1)
        out[y0, x0:x1 + 1] = color
        out[y1, x0:x1 + 1] = color
        out[y0:y1 + 1, x0] = color
        out[y0:y1 + 1, x1] = color
        n = L["id"][1:]
        nx = max(0, x0 - (len(n) * 8 + 3))
        ny = max(0, min(H - 11, (y0 + y1) // 2 - 5))
        tmp = np.full((10, len(n) * 8, 3), 255, dtype=np.uint8)
        thumb_vision.draw_number(tmp, n, 0, 0, scale=2)
        mask = tmp[:, :, 0] == 0
        reg = out[ny:ny + 10, nx:nx + tmp.shape[1]]
        reg[mask[: reg.shape[0], : reg.shape[1]]] = color
    return out


def make_lines(key, pages, render=True):
    """표본 쪽의 줄 자료와 렌더를 OUT6 에 쓴다. 산출 경로 목록을 돌려준다."""
    base = base_size(key)
    pdf = pdf_of(key)
    (OUT6 / "lines").mkdir(parents=True, exist_ok=True)
    rd = OUT6 / "render" / f"{key}-{LANG}"
    rd.mkdir(parents=True, exist_ok=True)
    paths = []
    try:
        for pno in pages:
            d = page_lines(key, pno, pdf, base)
            p = OUT6 / "lines" / f"{key}-{LANG}-p{pno}.json"
            json.dump(d, open(p, "w"), ensure_ascii=False, indent=1)
            paths.append(p)
            if render:
                img = render_page(pdf, pno)
                (rd / f"p{pno:04d}.png").write_bytes(thumb_vision.png_bytes(img))
                (rd / f"p{pno:04d}-lines.png").write_bytes(thumb_vision.png_bytes(draw_lines(img, d["lines"])))
    finally:
        pdf.close()
    return paths


def load_lines(key, pno):
    return json.load(open(OUT6 / "lines" / f"{key}-{LANG}-p{pno}.json"))


# ---------- 표본 고름 재료 ----------
_CAP = re.compile(r"^(〉|\*|주\)|※|단위)")


def page_stats(key):
    """layout 2 로 쪽마다: 본문 paragraph 글자 수 · 한 줄 블록 수 · 2단(본문 x0 가 쪽 너비 0.45 아래·0.5 위 둘 다 ≥ 3) ·
    표 수 · 설명 꼴 블록 수(작은 크기 + 머리 기호). 표본 고름의 재료(★5·★6 의 셈)."""
    p = doc_path(key, 2)
    doc = json.load(open(p))
    sizes = {pg["page"]: (pg["width"], pg["height"], pg.get("columns")) for pg in doc["pages"]}
    base = base_size(key)
    tcount = Counter(t["page"] for t in doc["tables"])
    st = {}
    for b in common.load_doc(p):
        pw, ph, ncol = sizes[b.page]
        s = st.setdefault(b.page, {"page": b.page, "body_chars": 0, "para": 0, "one_line": 0, "left": 0, "right": 0,
                                   "tables": tcount.get(b.page, 0), "captions": 0, "headings": 0, "columns": ncol})
        if b.kind == "table":
            continue
        if b.kind == "heading":
            s["headings"] += 1
        if b.font_size and b.font_size < 0.9 * base and _CAP.match(b.text.strip()):
            s["captions"] += 1
        if b.kind == "paragraph" and b.region == "body":
            s["body_chars"] += len(b.text)
            s["para"] += 1
            if b.bbox[3] - b.bbox[1] <= 1.6 * max(b.font_size, 1.0):
                s["one_line"] += 1
            if abs(b.font_size - base) <= 0.5:
                if b.bbox[0] < 0.45 * pw:
                    s["left"] += 1
                if b.bbox[0] > 0.5 * pw:
                    s["right"] += 1
    for s in st.values():
        s["two_col"] = s["left"] >= 3 and s["right"] >= 3
    return {"book": key, "base": base, "pages": [st[k] for k in sorted(st)]}


# ---------- 정답 ----------
def load_gold(key):
    """prework/gold-trx6/para/<키>-ko.json → {page: {"paras": [...], "of": {line_id: para}}}."""
    g = json.load(open(GOLD6 / "para" / f"{key}-{LANG}.json"))
    out = {}
    for pg in g["pages"]:
        of = {}
        for p in pg["paragraphs"]:
            for lid in p["lines"]:
                of[lid] = p
        out[pg["page"]] = {"paras": pg["paragraphs"], "of": of}
    return out


def gold_pages(key):
    return sorted(load_gold(key))


# ---------- 셈 ----------
is_tiny = para_join.is_tiny
neighbors = para_join.neighbors


_SENT = re.compile(r"(?<!\d)[.!?](?!\d)")


def sentences(text):
    """글 → [(시작, 끝)] 글자 범위. 끝 부호 .·!·? (숫자 사이 . 은 빼고). 마지막 조각은 부호 없이도 문장."""
    out, start = [], 0
    for m in _SENT.finditer(text):
        out.append((start, m.end()))
        start = m.end()
    if start < len(text):
        out.append((start, len(text)))
    return out


def over_kind(extra, P, of):
    """문단 P 밖 줄 extra 의 갈래."""
    q = of.get(extra["id"])
    if q is None:
        return "그 밖"
    if q["kind"] == "body":
        if P.get("col_break_to") == q["id"] or q.get("col_break_to") == P["id"]:
            return "단 넘김"
        if P.get("page_break_to") == q["id"] or q.get("page_break_to") == P["id"]:
            return "쪽 넘김"
        return "다른 문단"
    if q["kind"] == "heading":
        return "제목"
    if q["kind"] in ("table", "caption"):
        return "표·그림 설명"
    return "그 밖"


OVER_KINDS = ("다른 문단", "제목", "표·그림 설명", "단 넘김", "쪽 넘김", "그 밖")


def score_page(ls, groups, gold):
    """ls = 줄 목록, groups = {line_id: group_id}(예측 묶음), gold = {"paras", "of"}. 문단마다 잘/덜/잘못, 줄 짝, 문장 온전."""
    byid = {L["id"]: L for L in ls}
    of = gold["of"]
    members = {}
    for L in ls:
        if is_tiny(L):
            continue
        members.setdefault(groups[L["id"]], []).append(L)
    paras = []
    for P in gold["paras"]:
        if P["kind"] != "body":
            continue
        S = [lid for lid in P["lines"] if lid in byid and not is_tiny(byid[lid])]
        if not S:
            continue
        gs = sorted({groups[lid] for lid in S}, key=str)
        extras = [L for g in gs for L in members[g] if L["id"] not in S]
        if extras:
            kinds = Counter(over_kind(L, P, of) for L in extras)
            verdict, detail = "over", {"kinds": dict(kinds), "kind": kinds.most_common(1)[0][0],
                                       "extra": [L["id"] for L in extras][:12]}
        elif len(gs) > 1:
            verdict, detail = "under", {"pieces": len(gs)}
        else:
            verdict, detail = "good", {}
        text = "".join(byid[lid]["text"] for lid in S)
        spans, pos = [], 0
        for lid in S:
            spans.append((lid, pos, pos + len(byid[lid]["text"])))
            pos += len(byid[lid]["text"])
        n_s, ok_s = 0, 0
        for a, b in sentences(text):
            cover = {groups[lid] for lid, s0, s1 in spans if s0 < b and s1 > a}
            n_s += 1
            ok_s += len(cover) == 1
        paras.append({"id": P["id"], "lines": S, "groups": [str(g) for g in gs], "verdict": verdict, **detail,
                      "sentences": n_s, "sentences_ok": ok_s})
    # 줄 짝(둘 다 본문 줄)
    pr = {"n": 0, "tp": 0, "fp": 0, "fn": 0}
    for a, b in neighbors(ls):
        pa, pb = of.get(a), of.get(b)
        if not pa or not pb or pa["kind"] != "body" or pb["kind"] != "body":
            continue
        gold_join = pa["id"] == pb["id"]
        pred_join = groups[a] == groups[b]
        pr["n"] += 1
        pr["tp"] += gold_join and pred_join
        pr["fp"] += (not gold_join) and pred_join
        pr["fn"] += gold_join and (not pred_join)
    # 단 넘김·쪽 넘김 짝: 예측이 이었나
    breaks = []
    para_of = {p["id"]: p for p in gold["paras"]}
    for P in gold["paras"]:
        for field, kind in (("col_break_to", "col_break"), ("page_break_to", "page_break")):
            q = P.get(field)
            if q and q in para_of and P["lines"] and para_of[q]["lines"]:
                a, b = P["lines"][-1], para_of[q]["lines"][0]
                breaks.append({"kind": kind, "a": a, "b": b, "joined": groups.get(a) == groups.get(b)})
    return {"paras": paras, "pairs": pr, "breaks": breaks}


def summarize(pages):
    """쪽 셈 목록 → 책 합계."""
    paras = [p for pg in pages for p in pg["score"]["paras"]]
    n = len(paras)
    v = Counter(p["verdict"] for p in paras)
    ok = Counter(p["kind"] for p in paras if p["verdict"] == "over")
    s_n = sum(p["sentences"] for p in paras)
    s_ok = sum(p["sentences_ok"] for p in paras)
    pr = {k: sum(pg["score"]["pairs"][k] for pg in pages) for k in ("n", "tp", "fp", "fn")}
    prec = pr["tp"] / (pr["tp"] + pr["fp"]) if pr["tp"] + pr["fp"] else None
    rec = pr["tp"] / (pr["tp"] + pr["fn"]) if pr["tp"] + pr["fn"] else None
    br = [b for pg in pages for b in pg["score"]["breaks"]]
    return {"paragraphs": n, "good": v["good"], "under": v["under"], "over": v["over"],
            "good_pct": round(100 * v["good"] / n, 1) if n else None,
            "under_pct": round(100 * v["under"] / n, 1) if n else None,
            "over_pct": round(100 * v["over"] / n, 1) if n else None,
            "over_kinds": {k: ok.get(k, 0) for k in OVER_KINDS},
            "sentences": s_n, "sentences_ok": s_ok, "sentence_pct": round(100 * s_ok / s_n, 1) if s_n else None,
            "pairs": pr, "pair_precision": None if prec is None else round(prec, 4),
            "pair_recall": None if rec is None else round(rec, 4),
            "breaks": {"col_break": sum(b["kind"] == "col_break" for b in br),
                       "col_break_joined": sum(b["kind"] == "col_break" and b["joined"] for b in br),
                       "page_break": sum(b["kind"] == "page_break" for b in br)},
            "out_of_block": sum(pg.get("out_of_block", 0) for pg in pages),
            "lines": sum(pg.get("lines", 0) for pg in pages),
            "page_status": dict(Counter(pg["status"] for pg in pages))}


# ---------- 판 돌리기 ----------
def groups_from_blocks(ls, mapping):
    """줄 → 블록 사상 → 묶음 id(out_of_block 은 줄마다 따로)."""
    return {L["id"]: (mapping[L["id"]] if mapping[L["id"]] != "out_of_block" else f"oob:{L['id']}") for L in ls}


MODES = ("L1", "L2", "G0.65", "G0.8", "G1.0", "G1.2", "R", "R-R1", "R-R2", "R-R3", "R-R4", "R-R5", "R-R6", "R2r", "R5(0.8)",
         "R5(0.9)", "R7j")
P_GRID = (1.8, 2.0, 2.3, 2.5, 3.0)


def join_mode(ls, tables, base, W, H, mode, th):
    """판 이름 → para_join.join 호출. G<g> · R · R-Rk · R2r · R5(w) · R7j · P<p>(cos 격자)."""
    tb = [bb for _tid, bb in tables]
    if mode.startswith("G"):
        return para_join.join(ls, tb, base, W, H, th=dict(th, g=float(mode[1:])), mode="G")
    rules = set(para_join.RULES)
    m = "R"
    if mode.startswith("R-"):
        rules.discard(mode[2:])
        if mode == "R-R2":
            rules.discard("R2r")
    elif mode == "R2r":
        rules.discard("R2")  # 상대 pitch 만
    elif mode.startswith("R5("):
        th = dict(th, width=float(mode[3:-1]))
    elif mode == "R7j":
        m = "R7j"
    elif mode.startswith("P"):
        th = dict(th, P=float(mode[1:]))
    return para_join.join(ls, tb, base, W, H, rules=rules, th=th, mode=m)


def run(key, mode, th=None):
    """판 하나를 표본 쪽 전부에 돌려 OUT6/runs/<키>-ko-<판>.json 에 쓴다. mode = MODES | P<p>."""
    gold = load_gold(key)
    pages = []
    th = dict(th or {})
    blocks, tables = load_blocks(key, 1 if mode == "L1" else 2)
    base = base_size(key)
    for pno in sorted(gold):
        d = load_lines(key, pno)
        ls = d["lines"]
        oob = 0
        if mode in ("L1", "L2"):
            mp = map_lines(ls, blocks.get(pno, []), tables.get(pno, []))
            groups = groups_from_blocks(ls, mp)
            oob = sum(v == "out_of_block" for L, v in mp.items() if not is_tiny(next(x for x in ls if x["id"] == L)))
            extra = {"map": mp}
        else:
            res = join_mode(ls, tables.get(pno, []) if tables else [], base, d["width"], d["height"], mode, th)
            groups = res["groups"]
            extra = {"cut_by": res["cut_by"], "kinds": res["kinds"], "flags": res["flags"]}
        sc = score_page(ls, groups, gold[pno])
        pages.append({"page": pno, "status": d["status"], "lines": sum(not is_tiny(L) for L in ls),
                      "out_of_block": oob, "groups": groups, "score": sc, **extra})
    # 쪽 넘김 후보(이어진 표본 쪽끼리만)
    if mode not in ("L1", "L2") and not mode.startswith("G"):
        for p1, p2 in zip(pages, pages[1:]):
            if p2["page"] == p1["page"] + 1:
                l1 = [L for L in load_lines(key, p1["page"])["lines"] if not is_tiny(L) and not p1["kinds"].get(L["id"])]
                l2 = [L for L in load_lines(key, p2["page"])["lines"] if not is_tiny(L) and not p2["kinds"].get(L["id"])]
                f = para_join.page_break_flag(l1, l2, dict(para_join.TH, **th))
                if f:
                    p1["flags"].append(dict(f, next_page=p2["page"]))
    res = {"book": key, "mode": mode, "th": dict(para_join.TH, **th), "summary": summarize(pages), "pages": pages}
    (OUT6 / "runs").mkdir(parents=True, exist_ok=True)
    p = OUT6 / "runs" / f"{key}-{LANG}-{mode}.json"
    json.dump(res, open(p, "w"), ensure_ascii=False, indent=1, sort_keys=True)
    return res


# ---------- 장부(OUT6, 상한 0.1) ----------
_LOCK = threading.Lock()
_RESERVED = [0.0]


def calls():
    p = OUT6 / "calls.jsonl"
    return [json.loads(x) for x in open(p) if x.strip()] if p.exists() else []


def spent(rows=None):
    rows = calls() if rows is None else rows
    return sum(r.get("costUsd", 0) for r in rows if not r.get("dry_run"))


def reserve(usd, cap=CAP6):
    """누적 + 잡아 둔 몫 + 예상 > 상한이면 False — 보내지 않는다."""
    with _LOCK:
        if spent() + _RESERVED[0] + usd > cap:
            return False
        _RESERVED[0] += usd
        return True


def send(model, instr, text, schema, tag, est_usd, **extra):
    from semgraph import llm
    scratch = OUT6 / ".scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        raw, row = llm.respond(model, instr, text, schema, scratch, tag, effort="low")
    finally:
        with _LOCK:
            _RESERVED[0] = max(0.0, _RESERVED[0] - est_usd)
    row.update(extra, estimateUsd=round(est_usd, 6))
    with _LOCK:
        OUT6.mkdir(parents=True, exist_ok=True)
        with open(OUT6 / "calls.jsonl", "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return raw, row


def estimate(model, text, instr, n_items=None):
    return tx4_pick.estimate(model, text, instr, n_items)


def brief(s):
    ok = s["over_kinds"]
    return (f"잘 {s['good_pct']}% 덜 {s['under_pct']}% 잘못 {s['over_pct']}% ({' '.join(f'{k[:2]}{v}' for k, v in ok.items() if v)}) "
            f"문장 {s['sentence_pct']}% 짝 P{s['pair_precision']} R{s['pair_recall']} 단넘김 {s['breaks']['col_break_joined']}/{s['breaks']['col_break']}")


# ---------- 명령 ----------
def main(argv):
    cmd = argv[0]
    if cmd == "stats":
        key = argv[1]
        (OUT6 / "stats").mkdir(parents=True, exist_ok=True)
        res = page_stats(key)
        json.dump(res, open(OUT6 / "stats" / f"{key}-{LANG}.json", "w"), ensure_ascii=False, indent=1)
        top = sorted(res["pages"], key=lambda s: -s["body_chars"])[:12]
        print(key, "base", res["base"], "2단 쪽", sum(s["two_col"] for s in res["pages"]), "/", len(res["pages"]))
        for s in top:
            print(" ", {k: s[k] for k in ("page", "body_chars", "para", "one_line", "two_col", "tables", "captions", "headings")})
    elif cmd == "run":
        name, mode = argv[1], argv[2]
        th = json.loads(argv[3]) if len(argv) > 3 else None
        res = run(name.split("-")[0], mode, th)
        print(name, mode, brief(res["summary"]))
    elif cmd == "runall":
        key = argv[1]
        modes = list(MODES) + ([f"P{p}" for p in P_GRID] if key == "cos" else [])
        for m in modes:
            print(f"{key} {m:8s}", brief(run(key, m)["summary"]))
    elif cmd == "lines":
        key = argv[1]
        pages = [int(x) for x in argv[2:]]
        for p in make_lines(key, pages):
            print(p)
    else:
        raise SystemExit(f"모르는 명령 {cmd}")


if __name__ == "__main__":
    main(sys.argv[1:])
