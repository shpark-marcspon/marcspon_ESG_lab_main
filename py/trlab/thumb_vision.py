"""TRX4 K5 — 책 전체 썸네일 격자 한 호출. 새 의존 0(pypdfium2 render → numpy, PNG 는 zlib + struct 로 직접, PIL 없음).

grid(key, lang): 쪽마다 pypdfium2 render(scale = CELL_W / 쪽 너비 pt).to_numpy()(BGR) → RGB → 흰 바탕 장(6 열 × 10 행 =
  60쪽), 칸 틈 4px, 칸 아래 20px 라벨 띠에 쪽 번호를 3×5 비트맵 숫자 ×3(9×15px)로. → OUT4/thumb/<키>-<lang>-<k>.png.
call(...): input_text(지시문 — kb 를 보기 전에 쓰고 얼림) + 격자 이미지 전부(detail high) 한 호출.
  구조화 {items:[{idx, page, basis}], section_starts:[int], labels_read:[int]}.
채점 k5: 항목 쪽 = 답 page, 블록 = 그 쪽 후보 중 toc.match_score ≥ 0.55 최고, 없으면 그 쪽 size_max 최대 후보.
k5b: section_starts 를 경계 목록(글 없음 → 차례만)으로 — 모든 항목의 그 쪽 후보에 +K3_W → DP(코드만 판).

명령: python -m trlab.thumb_vision grid <키> <lang>
     python -m trlab.thumb_vision <luna|sol> <판> [--dry-run]   (실호출은 --with openai==3.26.0)
"""

import base64
import json
from collections import Counter
import struct
import sys
import zlib

import numpy as np

CELL_W = 200
COLS = 6
ROWS = 10
GAP = 4
LABEL_H = 20
DIGIT_SCALE = 3
PX_TOKENS = 1.2e-3  # 첫 실측 전 어림: 1.2k 토큰/Mpx(★6)
MATCH_MIN = 0.55

DIGITS = {  # 3×5
    "0": ["111", "101", "101", "101", "111"], "1": ["010", "110", "010", "010", "111"],
    "2": ["111", "001", "111", "100", "111"], "3": ["111", "001", "111", "001", "111"],
    "4": ["101", "101", "111", "001", "001"], "5": ["111", "100", "111", "001", "111"],
    "6": ["111", "100", "111", "101", "111"], "7": ["111", "001", "001", "001", "001"],
    "8": ["111", "101", "111", "101", "111"], "9": ["111", "101", "111", "001", "111"],
}

INSTR5 = ("Report of N pages shown as thumbnail grids (6 per row, row-major; image k covers pages a..b; the number under "
          "each cell is its page). Titles are too small to read — use layout: chapter dividers (mostly picture + one big "
          "title), repeated section-opening layouts, header bars. Table of contents entries: idx | level | text. For each "
          "entry give the page where that section starts, how you decided (layout / read_title / guess), list the pages "
          "that look like section openings, and the page labels you could actually read in the first row.")
SCHEMA5 = {"type": "object", "additionalProperties": False, "required": ["items", "section_starts", "labels_read"],
           "properties": {
               "items": {"type": "array", "items": {
                   "type": "object", "additionalProperties": False, "required": ["idx", "page", "basis"],
                   "properties": {"idx": {"type": "integer"}, "page": {"type": "integer"},
                                  "basis": {"type": "string", "enum": ["layout", "read_title", "guess"]}}}},
               "section_starts": {"type": "array", "items": {"type": "integer"}},
               "labels_read": {"type": "array", "items": {"type": "integer"}}}}


# ---------- 그림 ----------
def draw_number(img, n, x, y, scale=DIGIT_SCALE):
    """img(h, w, 3) 의 (x, y) 에 검은 숫자."""
    cx = x
    for ch in str(n):
        pat = DIGITS[ch]
        for r, row in enumerate(pat):
            for c, bit in enumerate(row):
                if bit == "1":
                    img[y + r * scale:y + (r + 1) * scale, cx + c * scale:cx + (c + 1) * scale] = 0
        cx += 3 * scale + scale
    return img


def sheets_of(npages, per=COLS * ROWS):
    return [(a, min(npages, a + per - 1)) for a in range(1, npages + 1, per)]


def compose(arrays, first_page, cell_w=CELL_W, cols=COLS, label=True):
    """arrays = 쪽 순서의 (h, w, 3) uint8 RGB(너비 ≤ cell_w) → 장 하나(흰 바탕)."""
    n = len(arrays)
    rows = (n + cols - 1) // cols
    cell_h = max(a.shape[0] for a in arrays)
    W = cols * cell_w + (cols + 1) * GAP
    H = rows * (cell_h + LABEL_H) + (rows + 1) * GAP
    img = np.full((H, W, 3), 255, dtype=np.uint8)
    for k, a in enumerate(arrays):
        r, c = divmod(k, cols)
        x = GAP + c * (cell_w + GAP)
        y = GAP + r * (cell_h + LABEL_H + GAP)
        h, w = a.shape[:2]
        img[y:y + h, x:x + w] = a
        if label:
            draw_number(img, first_page + k, x + 2, y + cell_h + (LABEL_H - 5 * DIGIT_SCALE) // 2)
    return img


def png_bytes(img):
    """(h, w, 3) uint8 → PNG(필터 0, zlib)."""
    h, w = img.shape[:2]
    raw = b"".join(b"\x00" + img[y].tobytes() for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def render_pages(key, lang, cell_w=CELL_W):
    import pypdfium2 as pdfium

    from trlab import glyphs
    pdf = pdfium.PdfDocument(str(glyphs.pdf_path(key, lang)))
    out = []
    try:
        for i in range(len(pdf)):
            pg = pdf[i]
            a = pg.render(scale=cell_w / pg.get_width()).to_numpy()
            out.append(np.ascontiguousarray(a[:, :, 2::-1]))  # BGR → RGB
            pg.close()
    finally:
        pdf.close()
    return out


def grid(key, lang, cell_w=CELL_W, cols=COLS, rows=ROWS, arrays=None, out_dir=None):
    """→ [{path, first, last, px, w, h}] (OUT4/thumb/<키>-<lang>-<k>.png)"""
    from trlab import tx4_pick as X
    arrays = arrays if arrays is not None else render_pages(key, lang, cell_w)
    out_dir = out_dir or (X.OUT4 / "thumb")
    out_dir.mkdir(parents=True, exist_ok=True)
    res = []
    for k, (a, b) in enumerate(sheets_of(len(arrays), cols * rows), 1):
        img = compose(arrays[a - 1:b], a, cell_w, cols)
        p = out_dir / f"{key}-{lang}-{k}.png"
        p.write_bytes(png_bytes(img))
        res.append({"path": str(p), "first": a, "last": b, "w": img.shape[1], "h": img.shape[0],
                    "px": img.shape[0] * img.shape[1]})
    return res


# ---------- 호출 ----------
def messages(sheets, items, npages):
    head = INSTR5.replace("Report of N pages", f"Report of {npages} pages")
    ranges = "; ".join(f"image {k} covers pages {s['first']}..{s['last']}" for k, s in enumerate(sheets, 1))
    ent = "\n".join(f"{i} | {it.get('level')} | {it['text']}" for i, it in enumerate(items))
    content = [{"type": "input_text", "text": f"{head}\n{ranges}\n\n## Entries (idx | level | text)\n{ent}"}]
    for s in sheets:
        content.append({"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(
            open(s["path"], "rb").read()).decode(), "detail": "high"})
    return [{"role": "user", "content": content}]


def tokens_per_px(model, rows=None):
    """첫 실측 이미지 호출의 (입력 − 글 어림) / px. 없으면 어림 1.2k/Mpx."""
    from trlab import tx4_pick as X
    rows = X.calls() if rows is None else rows
    mine = [r for r in rows if r.get("model") == model and r.get("images") and r.get("px") and not r.get("dry_run")]
    if mine:
        r = mine[0]
        return max(1e-5, (r["inputTokens"] - r.get("chars", 0) / 3.0) / r["px"]), r["outputTokens"]
    return PX_TOKENS, 1500


def estimate5(model, sheets, text_chars):
    from semgraph import llm
    k, outp = tokens_per_px(model)
    px = sum(s["px"] for s in sheets)
    inp = int(px * k + text_chars / 3.0)
    return inp, outp, llm.cost(model, inp, 0, outp)


def call(name, model, dry=False):
    """판 하나 1호출 → OUT4/k5/<판>-<luna|sol>.json"""
    from trlab import tx4_pick as X
    key, lang, bl = X.parse_name(name)
    P = X.load4(key, lang, bl)
    which = "luna" if model == X.LUNA else "sol"
    p_out = X.OUT4 / "k5" / f"{name}-{which}.json"
    if p_out.exists() and not dry:
        return json.load(open(p_out))
    sheets = grid(key, lang)
    npages = max(s["last"] for s in sheets)
    msg = messages(sheets, P["items"], npages)
    chars = len(msg[0]["content"][0]["text"])
    inp, outp, usd = estimate5(model, sheets, chars)
    info = {"model": model, "estimate_usd": round(usd, 6), "estimate_in": inp, "px": sum(s["px"] for s in sheets),
            "images": len(sheets), "chars": chars}
    if dry:
        return dict(info, status="dry_run")
    if not X.reserve(which, usd):
        return dict(info, status="over_cap")
    tag = f"TRX4/k5{which}/{name}"
    raw, row = X.send(which, model, "Answer in the JSON schema.", msg, SCHEMA5, tag, usd, chars=chars,
                      items=len(P["items"]), px=info["px"], images=len(sheets))
    ans = json.loads(raw)
    res = {"book": name, "model": model, "status": "ok", "info": info, "row": row, "answer": ans,
           "sheets": [{k: v for k, v in s.items() if k != "path"} for s in sheets]}
    p_out.parent.mkdir(parents=True, exist_ok=True)
    json.dump(res, open(p_out, "w"), ensure_ascii=False, indent=1)
    return res


# ---------- 답 → 블록 ----------
def to_block(item, page, cand, ids):
    """그 쪽 후보 중 match_score ≥ 0.55 최고, 없으면 size_max 최대 후보. → (블록 id | None, src)"""
    from trlab.toc import _size_rank, match_score
    cs = [ids[bid] for bid, r in cand["rows"].items() if r["page"] == page and bid in ids]
    if not cs:
        return None, "k5-page"
    rank = _size_rank(cs)
    best = max(cs, key=lambda b: (match_score(item["text"], b, rank[b.id]), -b.order))
    if match_score(item["text"], best, rank[best.id]) >= MATCH_MIN:
        return best.id, "k5"
    big = max(cs, key=lambda b: (cand["rows"][b.id]["size"], -b.order))
    return big.id, "k5"


def k5_picks(P, ans):
    ids = {b.id: b for b in P["blocks"]}
    by = {x["idx"]: x for x in ans.get("items", [])}
    out = []
    for i, it in enumerate(P["items"]):
        x = by.get(i)
        if not x or not x.get("page"):
            d = P["win"]["dp"][i]
            out.append((d["block"], d["page"], "dp"))
            continue
        bid, src = to_block(it, x["page"], P["cand"], ids)
        out.append((bid, x["page"], src))
    return out


def k5b_bonus(P, starts, w=None):
    from trlab import runhead as R
    w = R.K3_W if w is None else w
    divs = {int(p) for p, v in P["cand"]["pages"].items() if v["divider"]}
    pages = set(starts) | {p + 1 for p in starts if p in divs}
    one = {bid: w for bid, r in P["cand"]["rows"].items() if r["page"] in pages}
    return [one for _ in P["items"]]


def run_k5(name, which, dry=False):
    """k5:<which>(답 그대로) · k5b(sol 답의 section_starts → DP bonus, 코드만) 채점 → OUT4/runs."""
    from trlab import order_win as W
    from trlab import tx4_pick as X
    key, lang, bl = X.parse_name(name)
    model = X.LUNA if which == "luna" else X.SOL
    res = call(name, model, dry)
    if res.get("status") != "ok":
        return res
    P = X.prep4(key, lang, bl, (), win_mode="K1b")
    # 창 밖 개념 없음 — make_pred 가 버리지 않게 창을 책 전체로
    npages = max(b.page for b in P["blocks"])
    P["win"] = dict(P["win"], windows=[list(range(1, npages + 1)) for _ in P["items"]], mids=[None] * len(P["items"]))
    ans = res["answer"]
    pk = k5_picks(P, ans)
    pred = X.make_pred(P, pk)
    out = {"mode": f"k5-{which}", "book": name, "status": "ok", "usd": res["row"]["costUsd"], "grade": X.grade(P, pred),
           "pred": pred, "labels_read": ans.get("labels_read", []), "section_starts": ans.get("section_starts", []),
           "basis": dict(Counter(x.get("basis") for x in ans.get("items", []))),
           "px": res["info"]["px"], "inputTokens": res["row"]["inputTokens"], "images": res["info"]["images"]}
    X.save_run(name, f"k5-{which}", out)
    if which == "sol":
        Pb = X.load4(key, lang, bl)
        bonus = k5b_bonus(Pb, ans.get("section_starts", []))
        win = W.plan(Pb["items"], Pb["pm"], Pb["cand"], Pb["blocks"], Pb["toc_pages"], bonus=bonus)
        Pb["win"] = win
        pred_b = X.make_pred(Pb, X.dp_picks(win))
        X.save_run(name, "k5b", {"mode": "k5b", "book": name, "status": "ok", "usd": 0.0, "grade": X.grade(Pb, pred_b),
                                 "pred": pred_b})
    return out


def main(argv):
    cmd = argv[0]
    if cmd == "grid":
        for s in grid(argv[1], argv[2]):
            print(s["path"].split("/")[-1], s["first"], s["last"], s["w"], "×", s["h"], s["px"])
        return
    name = argv[1]
    dry = "--dry-run" in argv
    res = run_k5(name, cmd, dry)
    if res.get("status") != "ok":
        print(name, cmd, json.dumps(res, ensure_ascii=False))
        return
    g = res["grade"]
    print(name, res["mode"], f"S {g['start_ab']} E {g['end_ab']} B {g['block']}", "labels_read", len(res["labels_read"]),
          "px", res["px"], "in", res["inputTokens"], "USD", round(res["usd"], 5), "basis", res["basis"])


if __name__ == "__main__":
    main(sys.argv[1:])
