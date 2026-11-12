"""TRX2 G2 — G1 목차 항목마다 창(볼 PDF 쪽) 안 G0 후보에서 시작 블록을 고른다. 판 여섯을 같은 toc.score 와 간지 두 판으로 채점.

판: a 코드만(TRX1 toc 그대로) · b TRX1 luna(저장본 다시 채점, nb 는 toc_luna 를 env 로 돌린 산출) ·
    c G1 목차 + 코드 고르기(toc.match_score) · d 속성 + AI(luna) · e 속성 뺀 AI(luna) · dsol = d 를 sol 로.
창: 찍힌 번호 → PDF 쪽(toc.page_map·to_pdf) ±1. 번호 없는 항목 = 앞 번호 항목 쪽 − 1 ~ 다음 번호 항목 쪽(최대 12쪽,
    다음 쪽 쪽으로 붙임). 목차에 번호가 하나도 없으면 책 전체.
AI 가 "" 이거나 창 밖·없는 블록을 내면 코드가 찍힌 번호 → PDF 쪽(창 가운데)으로 메운다(블록 없음).
지시문은 sam 으로만 다듬고 얼린다. 실호출 예상 = 프롬프트 글자 수 × 첫 실측 토큰/글자(모델별), 누적 + 예상 > 자리 상한이면 보내지 않음.

명령: uv run --frozen --group scope3colab --with openai==3.26.0 python -m trlab.toc_pick <키> <lang> <a|b|c|d|e|dsol> [--dry-run]
     → OUT2/g2/<키>-<lang>-<판>.json
"""

import json
import os
import subprocess
import sys

from trlab import cands as C
from trlab import toc_vision as tv
from trlab.common import OUT as OUT1
from trlab.glyphs import GOLD2, OUT2, load_blocks, parse_path
from trlab.toc import _size_rank, extract_entries, find_toc_pages, locate, match_score, page_map, score, to_pdf

LUNA, SOL = "gpt-6-luna", "gpt-6.1-sol"
CAPS = {LUNA: 0.2, SOL: 0.3}
CHUNK_TOKENS = 60000
MAXWIN = 12

INSTR = """You map each table-of-contents entry of a sustainability report to the block where that section STARTS.
You get (1) the entries: idx | level | printed page | entry text | window (pdf pages to search),
(2) the divider pages (chapter dividers: mostly a big title and a picture), and
(3) candidate title blocks on those pages: COLUMNS.
For each entry pick ONE candidate id whose pdf page lies inside that entry's window:
- the block where the section begins — usually the title near the top of the page that matches the entry;
- a title repeated as a running header on following pages starts on its first page;
- if a divider page carrying that section's own big title comes right before, pick the block on the divider page;
- a level-1 (chapter) entry starts on its chapter divider page;
- if nothing fits, give block_id "".
Return one item per entry idx."""
# TRX3 s3 — INSTR 끝에 이 한 줄을 덧붙여 sol 이 "" 를 덜 내게(TRX2 고칠 후보 3)
INSTR_NEAREST = ('- if no candidate fits exactly, pick the NEAREST candidate (same page first, else the closest page '
                 'inside the window); never give "".')
COLS_ATTR = ("id | pdf page | position (상 top / 중 middle / 하 bottom) | font size pt | × body size | bold % | "
             "colour differs from body (y/n) | repeated running title (y/n) | text")
COLS_PLAIN = "id | pdf page | text"
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {
    "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["idx", "block_id"],
                               "properties": {"idx": {"type": "integer"}, "block_id": {"type": "string"}}}}}}


# ---------- 창 ----------
def windows(items, pmap, off, npages, bounds=None):
    """항목마다 (가운데 쪽|None, [쪽…]). bounds[i] = (lo, hi) 가 있으면 그 항목 창은 그것(TRX3 차례 창)."""
    pg = [to_pdf(it.get("printed_page"), pmap, off, npages) for it in items]
    if not any(pg) and not bounds:
        return [(None, list(range(1, npages + 1))) for _ in items]
    out = []
    for i, p in enumerate(pg):
        if bounds and bounds[i]:
            lo, hi = bounds[i]
            out.append((p, list(range(max(1, lo), min(npages, hi) + 1))))
            continue
        if p:
            out.append((p, [x for x in (p - 1, p, p + 1) if 1 <= x <= npages]))
            continue
        prev = next((pg[j] for j in range(i - 1, -1, -1) if pg[j]), None)
        nxt = next((pg[j] for j in range(i + 1, len(pg)) if pg[j]), None)
        lo = max(1, (prev - 1) if prev else 1)
        hi = min(npages, nxt if nxt else npages)
        if hi < lo:
            lo, hi = hi, lo
        if hi - lo + 1 > MAXWIN:
            if nxt:
                lo = hi - MAXWIN + 1
            else:
                hi = lo + MAXWIN - 1
        out.append((None, list(range(lo, hi + 1))))
    return out


# ---------- 산출 꼴 ----------
def to_pred(key, lang, blocks, tp, items, picks, wins):
    """picks[i] = 블록 id | None. None 이면 창 가운데 쪽(번호 있을 때)으로 메움."""
    ids = {b.id: b for b in blocks}
    npages = max(b.page for b in blocks)
    res, filled = [], 0
    for it, bid, (mid, win) in zip(items, picks, wins):
        b = ids.get(bid) if bid else None
        if b is not None and b.page not in win:
            b = None
        if b is None:
            filled += 1
        res.append({"text": it["text"], "printed": it.get("printed_page"), "level": it.get("level"),
                    "start_block": b.id if b else None, "start_page": b.page if b else mid})
    starts = sorted({r["start_page"] for r in res if r["start_page"]})
    for r in res:
        nx = [s for s in starts if r["start_page"] and s > r["start_page"]]
        r["end_page"] = (nx[0] - 1 if nx else npages) if r["start_page"] else None
    return {"book": key, "lang": lang, "toc_pages": tp, "items": res, "filled": filled}


# ---------- 간지 두 판 ----------
def divider_views(rows, gold):
    """A = toc.score 그대로, B = 정답·산출 시작이 간지 쪽이면 +1, 끝 e 는 e+1 이 간지면 +1, 옮긴 시작 > 끝이면 끝 = 시작."""
    D = {g["start_page"] for g in gold["items"] if g.get("divider")}

    def mv(s, e):
        if s is None:
            return None, e
        s2 = s + 1 if s in D else s
        e2 = (e + 1 if (e is not None and e + 1 in D) else e)
        if e2 is not None and s2 > e2:
            e2 = s2
        return s2, e2

    out = {}
    for name, sel in (("all", lambda g: True), ("divider", lambda g: g.get("divider"))):
        sub = [(g, r) for g, r in zip(gold["items"], rows) if sel(g)]
        n = len(sub)
        acc = {k: 0 for k in ("A_start", "A_start_pm1", "A_end", "A_end_pm1", "B_start", "B_start_pm1", "B_end",
                              "B_end_pm1", "block")}
        for g, r in sub:
            if not r.get("found"):
                continue
            p = r["pred"]
            acc["block"] += r["block_ok"]
            for tag, (gs, ge), (ps, pe) in (("A", (g["start_page"], g["end_page"]), (p["start_page"], p["end_page"])),
                                            ("B", mv(g["start_page"], g["end_page"]), mv(p["start_page"], p["end_page"]))):
                acc[f"{tag}_start"] += ps == gs
                acc[f"{tag}_end"] += pe == ge
                acc[f"{tag}_start_pm1"] += ps is not None and abs(ps - gs) <= 1
                acc[f"{tag}_end_pm1"] += pe is not None and ge is not None and abs(pe - ge) <= 1
        out[name] = {"n": n, **{k: (round(100 * v / n, 1) if n else None) for k, v in acc.items()}}
    out["D"] = sorted(D)
    return out


# ---------- 프롬프트 ----------
def prompt(items, wins, cand, divs, attrs=True, idxs=None):
    idxs = idxs if idxs is not None else range(len(items))
    pages = sorted({p for i in idxs for p in wins[i][1]})
    ent = [f"{i} | {items[i].get('level')} | {items[i].get('printed_page') if items[i].get('printed_page') is not None else '-'} | "
           f"{items[i]['text']} | {wins[i][1][0]}-{wins[i][1][-1]}" for i in idxs]
    rows = sorted(((r["page"], bid) for bid, r in cand["rows"].items() if r["page"] in set(pages)),
                  key=lambda x: (x[0], int(x[1].split("-b")[1]) if "-b" in x[1] else 0))
    lines = [C.line(bid, cand["rows"][bid], attrs) for _, bid in rows]
    dv = [p for p in pages if p in divs]
    return (f"## Entries (idx | level | printed page | text | window pdf pages)\n" + "\n".join(ent)
            + f"\n\n## Divider pages\n{', '.join(map(str, dv)) or '-'}\n\n## Candidate blocks ({COLS_ATTR if attrs else COLS_PLAIN})\n"
            + "\n".join(lines))


def _calls():
    p = OUT2 / "calls.jsonl"
    return [json.loads(x) for x in open(p) if x.strip()] if p.exists() else []


def site_spent(model):
    rows = [r for r in _calls() if r.get("model") == model and r.get("tag", "").startswith(("TRX2/G2/", "TRX1/T-a/nb"))
            and not r.get("dry_run")]
    return sum(r.get("costUsd", 0) for r in rows)


def tok_per_char(model):
    rows = [r for r in _calls() if r.get("model") == model and r.get("tag", "").startswith("TRX2/G2/") and r.get("chars")]
    if rows:
        r = rows[0]
        return r["inputTokens"] / r["chars"], r["outputTokens"]
    return 1 / 2.2, 60 * 40


def est(model, text):
    from semgraph import llm
    k, outp = tok_per_char(model)
    inp = int(len(text) * k) + len(INSTR) // 4
    return inp, outp, llm.cost(model, inp, 0, outp)


def ai_pick(key, lang, mode, items, wins, cand, divs, dry=False):
    model = SOL if mode == "dsol" else LUNA
    attrs = mode != "e"
    k, _ = tok_per_char(model)
    full = prompt(items, wins, cand, divs, attrs)
    chunks = [list(range(len(items)))]
    if len(full) * k > CHUNK_TOKENS:
        chunks = [list(range(i, min(len(items), i + 15))) for i in range(0, len(items), 15)]
    texts = [prompt(items, wins, cand, divs, attrs, c) for c in chunks]
    ests = [est(model, t) for t in texts]
    info = {"model": model, "chunks": len(chunks), "estimate_usd": round(sum(e[2] for e in ests), 5),
            "estimate_in": sum(e[0] for e in ests), "chars": sum(len(t) for t in texts)}
    if dry:
        return None, info
    from semgraph import llm
    picks, rows = [""] * len(items), []
    for c, t, e in zip(chunks, texts, ests):
        if site_spent(model) + e[2] > CAPS[model]:
            info["status"] = "over_cap"
            return None, info
        raw, row = llm.respond(model, INSTR.replace("COLUMNS", COLS_ATTR if attrs else COLS_PLAIN), t, SCHEMA, OUT2,
                               f"TRX2/G2/{mode}/{key}-{lang}", effort="low")
        row["chars"] = len(t)
        p = OUT2 / "calls.jsonl"
        lines = open(p).read().splitlines()
        lines[-1] = json.dumps(row, ensure_ascii=False)
        open(p, "w").write("\n".join(lines) + "\n")
        rows.append(row)
        for x in json.loads(raw)["items"]:
            if x["idx"] in c:
                picks[x["idx"]] = x["block_id"]
    info.update(status="ok", calls=rows, usd=round(sum(r["costUsd"] for r in rows), 6))
    return picks, info


def code_pick(items, wins, cand, blocks):
    ids = {b.id: b for b in blocks}
    picks = []
    for it, (_, win) in zip(items, wins):
        cs = [ids[bid] for bid, r in cand["rows"].items() if r["page"] in win and bid in ids]
        rank = _size_rank(cs)
        best, bs = None, 0.0
        for b in cs:
            s = match_score(it["text"], b, rank[b.id])
            if s > bs:
                best, bs = b, s
        picks.append(best.id if best and bs >= 0.55 else "")
    return picks


# ---------- 판 ----------
def gold_for(key, lang):
    return json.load(open(C.gold_path(key, lang)))


def run_mode(key, lang, mode, dry=False):
    blocks = load_blocks(key, lang)
    gold = gold_for(key, lang)
    info = {}
    if mode == "a":
        tp = find_toc_pages(blocks)
        res, _ = locate(blocks, extract_entries(blocks, tp), tp)
        pred = {"book": key, "lang": lang, "toc_pages": tp, "items": res, "filled": None}
    elif mode == "b":
        p = OUT1 / "toc" / f"{key}-{lang}-luna.json"
        if not p.exists():
            p = OUT2 / "toc" / f"{key}-{lang}-luna.json"
        if not p.exists():
            if dry:
                return {"mode": mode, "status": "dry_run", "note": "nb: toc_luna 를 TRX1_OUT/TRX1_GOLD env 로"}
            env = dict(os.environ, TRX1_OUT=str(OUT2), TRX1_GOLD=str(GOLD2))
            subprocess.run([sys.executable, "-m", "trlab.toc_luna", key, lang], env=env, check=True)
        pred = json.load(open(p))
        info = {"usd": pred.get("call", {}).get("costUsd"), "source": str(p.relative_to(p.parents[3]))}
        pred["filled"] = None
    else:
        g1 = tv.load(key, lang)
        if g1.get("status") != "ok":
            return {"mode": mode, "status": "no_toc"}
        items = g1["items"]
        pmap, off = page_map(blocks)
        npages = max(b.page for b in blocks)
        wins = windows(items, pmap, off, npages)
        cand = C.load(key, lang)
        divs = {int(p) for p, v in cand["pages"].items() if v["divider"]}
        if mode == "c":
            picks = code_pick(items, wins, cand, blocks)
        else:
            picks, info = ai_pick(key, lang, mode, items, wins, cand, divs, dry)
            if picks is None:
                return {"mode": mode, "status": info.get("status", "dry_run"), **info}
        pred = to_pred(key, lang, blocks, g1["pages"], items, picks, wins)
        pred["windows"] = [w[1] for w in wins]
    sc = score(pred, gold, blocks)
    return {"mode": mode, "status": "ok", **info, "filled": pred.get("filled"),
            "score": {k: v for k, v in sc.items() if k not in ("mid",)},
            "views": divider_views(sc["rows"], gold), "pred": pred}


def load(key, lang, mode):
    return json.load(open(OUT2 / "g2" / f"{key}-{lang}-{mode}.json"))


def main(argv):
    key, lang, mode = argv[0], argv[1], argv[2]
    res = run_mode(key, lang, mode, dry="--dry-run" in argv)
    if res["status"] != "ok":
        print(key, lang, mode, json.dumps({k: v for k, v in res.items() if k != "calls"}, ensure_ascii=False))
        return
    (OUT2 / "g2").mkdir(parents=True, exist_ok=True)
    json.dump(res, open(OUT2 / "g2" / f"{key}-{lang}-{mode}.json", "w"), ensure_ascii=False, indent=1)
    s = res["score"]
    print(key, lang, mode, "뽑힘", s["extracted_pct"], "제목", s["block_ok_pct"], "시작", s["start_ok_pct"], "/",
          s["start_pm1_pct"], "끝", s["end_ok_pct"], "/", s["end_pm1_pct"], "B시작", res["views"]["all"]["B_start"],
          "메움", res["filled"], "USD", res.get("usd"))


if __name__ == "__main__":
    main(sys.argv[1:])
