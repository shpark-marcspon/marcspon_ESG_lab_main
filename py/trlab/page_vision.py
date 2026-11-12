"""TRX3 H3 — 쪽 이미지 고르기(luna). 사람이 보는 식: 항목 하나 = 쪽 이미지 ≤ 3장 = luna 한 호출.

쪽: 그 항목 창(order_win.windows3)이 ≤ 3쪽이면 그 쪽들, 더 크면 DP 쪽 ±1. 100dpi 렌더(docparse CLI, 책마다 한 번에).
답 {"page_idx": 0..3, "title": str} (0 = 이 쪽들에 없음). 블록 = 그 쪽 후보 행(cands union) 중 toc.match_score(title) 최고
≥ 0.55, 없으면 그 쪽 size_max 최대 후보(src h3-largest), 후보가 하나도 없으면 블록 없이 쪽만(src h3-page).
예상 = 첫 실측 장당 입력 토큰 × 장 수 + 글 300(없으면 3.5k/장), 출력 = 첫 실측(없으면 150).

명령: python -m trlab.page_vision <키> <lang> [--dry-run] [--workers n] → OUT3/runs/<키>-<lang>-h3.json
"""

import base64
import json
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from trlab import glyphs
from trlab import tx3_pick as T
from trlab.toc import _size_rank, match_score

MODEL = T.LUNA
DPI = 100
EST_IMG = 3500
EST_OUT = 150
MIN_MATCH = 0.55
QUESTION = ('Entry (level {level}): "{text}". Pages {pages} of the report follow in order. On which of these pages does '
            'this section start, and what is its title exactly as printed?')
INSTR = ("You look at page images of one sustainability report and find where a table-of-contents entry starts. "
         "page_idx = 1 for the first image, 2 for the second, 3 for the third; 0 if the section does not start on any of "
         "them. title = the section title exactly as printed on that page (\"\" if page_idx is 0).")
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["page_idx", "title"],
          "properties": {"page_idx": {"type": "integer", "enum": [0, 1, 2, 3]}, "title": {"type": "string"}}}


def item_pages(win, dp_page):
    if len(win) <= 3:
        return list(win)
    if dp_page is None:
        return list(win[:3])
    return [p for p in (dp_page - 1, dp_page, dp_page + 1) if p in win] or [dp_page]


def render(key, lang, pages, dpi=DPI):
    out = T.OUT3 / "render" / f"{key}-{lang}"
    need = sorted({p for p in pages if not (out / f"p{p:04d}.png").exists()})
    if need:
        cwd = glyphs.ROOT / "esg-app" / "services" / "parser"
        subprocess.run(["uv", "run", "--frozen", "python", "-m", "docparse", "render", str(glyphs.pdf_path(key, lang)),
                        "--out", str(out), "--pages", ",".join(map(str, need)), "--dpi", str(dpi)],
                       cwd=cwd, check=True, capture_output=True)
    return {p: out / f"p{p:04d}.png" for p in pages}


def png_px(path):
    b = open(path, "rb").read(24)
    return int.from_bytes(b[16:20], "big") * int.from_bytes(b[20:24], "big") if b[:4] == b"\x89PNG" else 0


def messages(item, pages, pngs):
    content = [{"type": "input_text", "text": QUESTION.format(level=item.get("level"), text=item["text"],
                                                             pages=", ".join(map(str, pages)))}]
    for p in pages:
        content.append({"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(
            open(pngs[p], "rb").read()).decode(), "detail": "high"})
    return [{"role": "user", "content": content}]


def estimate(n_img):
    from semgraph import llm
    rows = [r for r in T.calls() if r.get("tag", "").startswith("TRX3/h3/") and r.get("pages")]
    if rows:
        r = rows[0]
        per = (r["inputTokens"] - 300) / r["pages"]
        inp, out = int(per * n_img + 300), max(EST_OUT, r["outputTokens"])
    else:
        inp, out = EST_IMG * n_img + 300, EST_OUT
    return inp, out, llm.cost(MODEL, inp, 0, out)


def to_block(answer, pages, cand, ids):
    """→ (블록 | None, 쪽 | None, src)"""
    k = answer.get("page_idx", 0)
    if not k or k > len(pages):
        return None, None, "h3-none"
    page = pages[k - 1]
    cs = [ids[bid] for bid, r in cand["rows"].items() if r["page"] == page and bid in ids]
    if not cs:
        return None, page, "h3-page"
    rank = _size_rank(cs)
    title = answer.get("title") or ""
    best, bs = None, 0.0
    for b in cs:
        s = match_score(title, b, rank[b.id]) if title else 0.0
        if s > bs:
            best, bs = b, s
    if best is not None and bs >= MIN_MATCH:
        return best.id, page, "h3"
    big = max(cs, key=lambda b: (cand["rows"][b.id]["size"], -b.order))
    return big.id, page, "h3-largest"


def run(key, lang, dry=False, workers=6, only=None):
    P = T.prep(key, lang)
    items, win = P["items"], P["win"]
    ids = {b.id: b for b in P["blocks"]}
    plan = [item_pages(w, d["page"]) for w, d in zip(win["windows"], win["dp"])]
    idxs = list(range(len(items))) if only is None else list(only)
    prev = T.load_run(key, lang, "h3") or {}
    answers = dict(prev.get("answers") or {})
    todo = [i for i in idxs if str(i) not in answers]
    est = [estimate(len(plan[i]))[2] for i in todo]
    if dry:
        return {"status": "dry_run", "items": len(todo), "images": sum(len(plan[i]) for i in todo),
                "estimate_usd": round(sum(est), 5)}
    pngs = render(key, lang, sorted({p for i in todo for p in plan[i]})) if todo else {}

    def one(i, e):
        if not T.reserve("luna", e):
            return i, {"status": "over_cap"}
        msg = messages(items[i], plan[i], pngs)
        px = sum(png_px(pngs[p]) for p in plan[i])
        raw, row = T.send("luna", MODEL, INSTR, msg, SCHEMA, f"TRX3/h3/{key}-{lang}/{i}", e, pages=len(plan[i]), px=px,
                          chars=None)
        a = json.loads(raw)
        b, p, src = to_block(a, plan[i], P["cand"], ids)
        return i, {"status": "ok", "pages": plan[i], "page_idx": a["page_idx"], "title": a["title"], "block": b,
                   "page": p, "src": src, "usd": row["costUsd"], "inputTokens": row["inputTokens"]}

    if todo:  # 첫 호출은 혼자(장당 토큰을 재고 예상을 고친다)
        i0 = todo[0]
        i, a = one(i0, est[0])
        answers[str(i)] = a
        rest = todo[1:]
        est2 = [estimate(len(plan[i]))[2] for i in rest]
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, a in ex.map(lambda x: one(*x), zip(rest, est2)):
                answers[str(i)] = a
    ok = {k: v for k, v in answers.items() if v.get("status") == "ok"}
    picks = []
    for i in range(len(items)):
        a = ok.get(str(i))
        picks.append((a["block"], a["page"], a["src"]) if a and a.get("page") else (None, None, "h3-none"))
    filled = T.fill_dp([(b, p) for b, p, _ in picks], win, "h3")
    picks2 = [picks[i] if picks[i][1] else filled[i] for i in range(len(items))]  # 답 없음은 창 가운데 · DP
    wins = list(zip(win["mids"], win["windows"]))
    pred = T.make_pred(key, lang, P["blocks"], P["g1"]["pages"], items, picks2, wins)
    res = {"mode": "h3", "status": "ok" if len(ok) == len(items) else "partial", "answers": answers, "plan": plan,
           "usd": round(sum(v.get("usd", 0) for v in ok.values()), 6),
           "over_cap": sum(v.get("status") == "over_cap" for v in answers.values()),
           "src": dict(Counter(v["src"] for v in ok.values())),
           "grade": T.grade(key, lang, pred, P["blocks"]), "pred": pred}
    res["answers"] = {k: v for k, v in answers.items() if v.get("status") == "ok"}
    return T.save_run(key, lang, "h3", res)


def main(argv):
    key, lang = argv[0], argv[1]
    w = int(argv[argv.index("--workers") + 1]) if "--workers" in argv else 6
    res = run(key, lang, dry="--dry-run" in argv, workers=w)
    if res["status"] == "dry_run":
        print(key, lang, "h3 dry", res)
        return
    print(key, lang, "h3", res["status"], T.brief_line(res["grade"]), "틀림", res["grade"]["wrong_start"], "USD", res["usd"],
          "src", res["src"], "over_cap", res["over_cap"])


if __name__ == "__main__":
    main(sys.argv[1:])
