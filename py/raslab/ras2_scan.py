"""TRX9 ① — 글자 층 없는 글이 있는 쪽을 실호출 0 으로 가르는 훑개: 19권 다시 훑기 → 특징 → 층화 표본 → (러너 눈 라벨) → 갈래 → 정밀·재현.
PIL 0(numpy 렌더, PNG 는 `trlab.thumb_vision.png_bytes`). `raslab.scan.scan` 위에 특징을 더한다. 정답을 보지 않는다.

  uv run --frozen --group scope3colab python -m raslab.ras2_scan scan [--books <books.json>] [keys…]   → OUT9/scan/<키>.json (+ times.json)
  … ras2_scan sample                        → OUT9/sample.json (층 셋 (가) textless_big ≥ 0.02 (나) chars < 50 (다) 나머지, Random(9), 문턱 안 씀)
  … ras2_scan render <키> <쪽,쪽,…> [--dpi 100]  → OUT9/render/<키>-p<쪽>.png (라벨용 낱장)
  … ras2_scan sheet <키> <쪽,쪽,…> [--dpi 100]   → OUT9/render/<키>-sheet-<쪽…>.png (라벨용 2×2 모음)
  … ras2_scan branch [--T1 --T2 --T3]       → OUT9/branch.json (쪽마다 갈래 none|raster|outline|both)
  … ras2_scan eval --labels <pages.json> [--T1 --T2 --T3] [--json <out>]  → 정밀·재현(개발·보류)·kinds 재현·민감도·놓침·헛잡음

특징(쪽마다, scan 칸 위에):
  textless_big — 되풀이·1% 미만 뺀 조각 중 넓이 ≥ BIG_PIECE(2%) 이고 겹친 글자 ≤ FEW(5) 인 것의 합집합 비율(scan.union_ratio)
  path_density — paths / (쪽 넓이 / 1만 pt²)
  img_text_est — 위 조각마다 108dpi 렌더해 「글 줄 꼴 점수」의 최대(img_lines 는 글 줄 띠 수의 최대): 가장자리 4% 를 자르고, 최빈색(바탕)과 RGB 차 합 > INK 인
     화소를 잉크로, 행별 잉크 비율에서 바닥(10분위)을 뺀 값이 ROW_LO 넘고 ROW_HI 아래인 행을 잉크 행으로 묶어 띠를 만들고, 높이 1.5~14pt 인 띠(글 줄)가 셋 이상일 때 (글 줄 띠 높이 합 / 전체 띠 높이 합).
     사진은 띠가 하나로 뭉쳐 0 에 가깝고 글·표는 높다.
  text_cover — chars / (이미지 밖 넓이 / 1만 pt²)
갈래: raster = textless_big ≥ T1 and img_text_est ≥ T2 · outline = chars < 50 and (path_density ≥ T3 or paths ≥ 300) · both · none.
문턱 T1~T3 의 기본값은 개발 셋(issi·fm) 라벨로 정해 얼린 값(first-run.txt).
"""

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

import numpy as np

from raslab import scan as base

ROOT = Path(__file__).resolve().parents[3]  # …/marcspon  (esg-lab(.worktrees/<밴드>)/py/raslab → 셋 위가 esg-lab, 넷 위가 marcspon)
OUT9 = Path(os.environ.get("TRX9_OUT", str(Path.home() / "Desktop" / "marcspon" / "esg-lab" / "out" / "trx9")))
BOOKS = Path(os.environ.get("TRX9_BOOKS", str(Path.home() / "Desktop" / "marcspon" / "prework" / "gold-trx9" / "books.json")))

BIG_PIECE = 0.02
FEW = 5
INK = 80
ROW_LO, ROW_HI = 0.02, 0.95
PIECE_SCALE = 1.5  # 108dpi
MARGIN = 0.04
LINE_PT = (1.5, 14.0)
MIN_LINES = 3
STRATA_A = 0.02
NO_TEXT = base.NO_TEXT
OUTLINE_PATHS = base.OUTLINE_PATHS
# 문턱 — 개발 셋으로 정한 뒤 얼린다(first-run.txt). 그 전까지는 자리 표시.
T = {"T1": 0.02, "T2": 0.2, "T3": 2.0}  # 개발 셋(issi·fm 47쪽)으로 정해 얼림 02:50 — first-run.txt


# ---------- 렌더(numpy) ----------
def render(page, scale):
    """(h, w, 3) uint8 RGB."""
    bm = page.render(scale=scale, rev_byteorder=True)
    a = bm.to_numpy()
    return np.ascontiguousarray(a[:, :, :3])


def crop_px(arr, bbox, scale):
    x0, y0, x1, y1 = (int(round(v * scale)) for v in bbox)
    h, w = arr.shape[:2]
    return arr[max(0, y0) : min(h, y1), max(0, x0) : min(w, x1)]


def mode_color(arr):
    """가장 흔한 색(32 단계로 묶은 뒤 그 묶음의 평균) — 바탕색. 액자·테두리가 있어도 안쪽 바탕을 잡는다."""
    q = (arr // 32).reshape(-1, 3)
    keys = q[:, 0] * 64 + q[:, 1] * 8 + q[:, 2]
    k = np.bincount(keys).argmax()
    return arr.reshape(-1, 3)[keys == k].mean(axis=0)


def stripe_score(arr, scale):
    """(글 줄 꼴 점수 0~1, 글 줄 띠 수). arr (h, w, 3), scale = px/pt. 가장자리 MARGIN 은 자르고, 행 잉크 비율에서 바닥(10분위 — 세로 괘선 몫)을 뺀다."""
    if arr.size == 0 or arr.shape[0] < 8 or arr.shape[1] < 8:
        return 0.0, 0
    my, mx = int(arr.shape[0] * MARGIN), int(arr.shape[1] * MARGIN)
    arr = arr[my : arr.shape[0] - my, mx : arr.shape[1] - mx]
    g = arr.astype(np.int16)
    ink = np.abs(g - mode_color(arr)).sum(axis=2) > INK
    rr = ink.mean(axis=1)
    rows = (rr - np.percentile(rr, 10) > ROW_LO) & (rr < ROW_HI)
    bands, h = [], 0
    for v in rows:
        if v:
            h += 1
        elif h:
            bands.append(h)
            h = 0
    if h:
        bands.append(h)
    if not bands:
        return 0.0, 0
    lo, hi = max(2, round(LINE_PT[0] * scale)), round(LINE_PT[1] * scale)
    lines = [b for b in bands if lo <= b <= hi]
    if len(lines) < MIN_LINES:
        return 0.0, len(lines)
    return round(sum(lines) / sum(bands), 4), len(lines)


# ---------- 특징 ----------
def big_pieces(rec):
    return [o for o in rec["images"] if not o["repeated"] and o["area"] >= BIG_PIECE and o["chars"] <= FEW]


def features(rec, page=None, cache=None):
    """scan 쪽 레코드(+ 열린 pdfium 쪽) → 특징 dict. page 가 None 이면 img_text_est 는 0 (시험용)."""
    w, h = rec["size"]
    big = big_pieces(rec)
    f = {
        "chars": rec["chars"],
        "paths": rec["paths"],
        "raster_union": rec["raster_union"],
        "chars_in_images": rec.get("chars_in_images", 0),
        "textless_big": round(base.union_ratio([o["bbox"] for o in big], w, h), 4),
        "n_big": len(big),
        "path_density": round(rec["paths"] / (w * h / 1e4), 3),
        "text_cover": round(rec["chars"] / max(1e-6, (1 - rec["raster_union"]) * w * h / 1e4), 3),
        "img_text_est": 0.0,
        "img_lines": 0,
        "pieces": [],
    }
    if page is not None and big:
        arr = render(page, PIECE_SCALE)
        for o in big:
            sc, nl = stripe_score(crop_px(arr, o["bbox"], PIECE_SCALE), PIECE_SCALE)
            f["pieces"].append({"bbox": o["bbox"], "area": o["area"], "score": sc, "lines": nl})
        f["img_text_est"] = max(p["score"] for p in f["pieces"])
        f["img_lines"] = max(p["lines"] for p in f["pieces"])
    return f


def branch(f, T=T):
    r = f["textless_big"] >= T["T1"] and f["img_text_est"] >= T["T2"]
    o = f["chars"] < NO_TEXT and (f["path_density"] >= T["T3"] or f["paths"] >= OUTLINE_PATHS)
    return "both" if r and o else "raster" if r else "outline" if o else "none"


def stratum(f):
    return "A" if f["textless_big"] >= STRATA_A else "B" if f["chars"] < NO_TEXT else "C"


# ---------- 표본 ----------
def book_n(n_pages):
    return n_pages if n_pages <= 15 else 15 if n_pages <= 40 else 10


def pick(pages_by_stratum, n, rng):
    """층마다 최소 2(층이 그보다 작으면 전부), 나머지는 크기 비례(큰 나머지 차례). 합 ≤ n."""
    groups = {k: sorted(v) for k, v in pages_by_stratum.items() if v}
    total = sum(len(v) for v in groups.values())
    if total <= n:
        return {k: v for k, v in groups.items()}
    alloc = {k: min(2, len(v)) for k, v in groups.items()}
    rest = n - sum(alloc.values())
    if rest > 0:
        room = {k: len(v) - alloc[k] for k, v in groups.items()}
        share = {k: rest * room[k] / max(1, sum(room.values())) for k in groups}
        for k in groups:
            alloc[k] += min(room[k], int(share[k]))
        left = rest - sum(alloc[k] - min(2, len(groups[k])) for k in groups)
        for k in sorted(groups, key=lambda k: -(share[k] - int(share[k]))):
            if left <= 0:
                break
            if alloc[k] < len(groups[k]):
                alloc[k] += 1
                left -= 1
    return {k: sorted(rng.sample(groups[k], alloc[k])) for k in groups}


# ---------- 셈 ----------
POS = {"raster", "outline", "both"}
RASTER_KINDS = {"raster_table", "raster_chart", "scan", "infographic", "screenshot"}
OUTLINE_KINDS = {"outline_table", "outline_body"}


def prf(rows):
    """rows: [(label_pos: bool, pred_pos: bool)] → dict."""
    tp = sum(1 for a, b in rows if a and b)
    fp = sum(1 for a, b in rows if not a and b)
    fn = sum(1 for a, b in rows if a and not b)
    tn = sum(1 for a, b in rows if not a and not b)
    return {
        "n": len(rows),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall": round(tp / (tp + fn), 4) if tp + fn else None,
    }


def evaluate(labels, feats, T=T):
    """labels: [{key, page, has_textless_text, kinds, set}], feats: {key: {page: features}}."""
    by_set, kinds, miss, false = {}, {}, [], []
    for lb in labels:
        f = feats[lb["key"]][lb["page"]]
        br = branch(f, T)
        pred = br in POS
        by_set.setdefault(lb.get("set", "?"), []).append((lb["has_textless_text"], pred))
        by_set.setdefault("all", []).append((lb["has_textless_text"], pred))
        for k in lb.get("kinds", []):
            want = "raster" if k in RASTER_KINDS else "outline"
            kinds.setdefault(k, {"n": 0, "any": 0, "match": 0})
            kinds[k]["n"] += 1
            kinds[k]["any"] += pred
            kinds[k]["match"] += br in (want, "both")
        if lb["has_textless_text"] and not pred:
            miss.append({"key": lb["key"], "page": lb["page"], "kinds": lb.get("kinds", []), "set": lb.get("set"), "f": _short(f)})
        if not lb["has_textless_text"] and pred:
            false.append({"key": lb["key"], "page": lb["page"], "branch": br, "set": lb.get("set"), "f": _short(f)})
    return {"T": dict(T), "sets": {k: prf(v) for k, v in by_set.items()}, "kinds": kinds, "missed": miss, "false": false}


def _short(f):
    return {k: f.get(k) for k in ("chars", "paths", "textless_big", "img_text_est", "img_lines", "path_density")}


def sensitivity(labels, feats, T=T):
    out = {"base": evaluate(labels, feats, T)["sets"]}
    for k in ("T1", "T2", "T3"):
        for m in (0.5, 2.0):
            t = dict(T)
            t[k] = T[k] * m
            out[f"{k}x{m:g}"] = evaluate(labels, feats, t)["sets"]
    return out


# ---------- 입출력 ----------
def load_books(path=BOOKS):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return Path.home() / "Desktop" / "marcspon" / d["dir"], d["books"]


def load_feats(keys=None):
    out = {}
    for p in sorted((OUT9 / "scan").glob("*.json")):
        if p.stem == "times":
            continue
        if keys and p.stem not in keys:
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        out[p.stem] = {pg["page"]: pg["features"] for pg in d["pages"]}
    return out


def cmd_scan(a):
    import pypdfium2 as pdfium

    pdfs, books = load_books(a.books)
    keys = a.keys or list(books)
    (OUT9 / "scan").mkdir(parents=True, exist_ok=True)
    times = json.loads((OUT9 / "scan" / "times.json").read_text()) if (OUT9 / "scan" / "times.json").is_file() else {}
    for key in keys:
        t0 = time.time()
        pdf = pdfs / books[key]["pdf"]
        pages = base.scan(pdf)
        t1 = time.time()
        doc = pdfium.PdfDocument(str(pdf))
        try:
            for rec in pages:
                page = doc[rec["page"] - 1] if big_pieces(rec) else None
                rec["features"] = features(rec, page)
        finally:
            doc.close()
        t2 = time.time()
        (OUT9 / "scan" / f"{key}.json").write_text(json.dumps({"key": key, "pages": pages}, ensure_ascii=False), encoding="utf-8")
        times[key] = {"pages": len(pages), "scan_s": round(t1 - t0, 1), "feat_s": round(t2 - t1, 1), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        (OUT9 / "scan" / "times.json").write_text(json.dumps(times, ensure_ascii=False, indent=1), encoding="utf-8")
        nA = sum(stratum(r["features"]) == "A" for r in pages)
        nB = sum(stratum(r["features"]) == "B" for r in pages)
        print(f"{key}: 쪽 {len(pages)} · 훑기 {t1 - t0:.0f}s · 특징 {t2 - t1:.0f}s · 층 A {nA} B {nB} C {len(pages) - nA - nB}", flush=True)


def cmd_sample(a):
    rng = random.Random(9)
    feats = load_feats()
    _, books = load_books(a.books)
    out, total = {}, 0
    for key in books:
        if key not in feats:
            continue
        strata = {}
        for pg, f in sorted(feats[key].items()):
            strata.setdefault(stratum(f), []).append(pg)
        chosen = pick(strata, book_n(len(feats[key])), rng)
        pages = sorted(p for v in chosen.values() for p in v)
        out[key] = {"n_pages": len(feats[key]), "strata": {k: len(v) for k, v in strata.items()}, "picked": chosen, "pages": pages}
        total += len(pages)
        print(f"{key}: {len(pages)} / {len(feats[key])} {out[key]['strata']} → {chosen}")
    (OUT9 / "sample.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("합", total)


def cmd_render(a):
    import pypdfium2 as pdfium

    from trlab.thumb_vision import png_bytes

    pdfs, books = load_books(a.books)
    (OUT9 / "render").mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(pdfs / books[a.key]["pdf"]))
    try:
        for pg in (int(x) for x in a.pages.split(",") if x):
            arr = render(doc[pg - 1], a.dpi / 72)
            p = OUT9 / "render" / f"{a.key}-p{pg}.png"
            p.write_bytes(png_bytes(arr))
            print(p, arr.shape[1], arr.shape[0])
    finally:
        doc.close()


def cmd_sheet(a):
    """라벨용 2×2 모음(층 (다) 쪽 — 큰 글없는 조각이 없는 쪽은 모음으로 보고 의심되면 낱장으로)."""
    import pypdfium2 as pdfium

    from trlab.thumb_vision import png_bytes

    pdfs, books = load_books(a.books)
    (OUT9 / "render").mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(pdfs / books[a.key]["pdf"]))
    try:
        pages = [int(x) for x in a.pages.split(",") if x]
        arrs = [render(doc[pg - 1], a.dpi / 72) for pg in pages]
    finally:
        doc.close()
    ch, cw = max(x.shape[0] for x in arrs), max(x.shape[1] for x in arrs)
    cols = 2
    rows = (len(arrs) + cols - 1) // cols
    gap = 6
    img = np.full((rows * (ch + gap) + gap, cols * (cw + gap) + gap, 3), 90, dtype=np.uint8)
    for k, x in enumerate(arrs):
        r, c = divmod(k, cols)
        y0, x0 = gap + r * (ch + gap), gap + c * (cw + gap)
        img[y0 : y0 + x.shape[0], x0 : x0 + x.shape[1]] = x
    p = OUT9 / "render" / f"{a.key}-sheet-{'-'.join(map(str, pages))}.png"
    p.write_bytes(png_bytes(img))
    print(p, img.shape[1], img.shape[0], "차례: 왼위→오른위→왼아래→오른아래")


def _T(a):
    return {"T1": a.T1 if a.T1 is not None else T["T1"], "T2": a.T2 if a.T2 is not None else T["T2"], "T3": a.T3 if a.T3 is not None else T["T3"]}


def cmd_branch(a):
    feats = load_feats()
    t = _T(a)
    out = {"T": t, "books": {}}
    print("| 키 | 쪽 | raster | outline | both | none | 있음 % |\n|---|---:|---:|---:|---:|---:|---:|")
    tot = {"n": 0, "raster": 0, "outline": 0, "both": 0, "none": 0}
    for key, pages in feats.items():
        rows = {pg: {"branch": branch(f, t), **_short(f)} for pg, f in sorted(pages.items())}
        c = {b: sum(1 for r in rows.values() if r["branch"] == b) for b in ("raster", "outline", "both", "none")}
        out["books"][key] = {"n": len(rows), "count": c, "pages": rows}
        tot["n"] += len(rows)
        for b in c:
            tot[b] += c[b]
        print(f"| {key} | {len(rows)} | {c['raster']} | {c['outline']} | {c['both']} | {c['none']} | {(len(rows) - c['none']) / len(rows):.1%} |")
    print(f"| 합 | {tot['n']} | {tot['raster']} | {tot['outline']} | {tot['both']} | {tot['none']} | {(tot['n'] - tot['none']) / max(1, tot['n']):.1%} |")
    out["total"] = tot
    (OUT9 / "branch.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


def cmd_eval(a):
    labels = json.loads(Path(a.labels).read_text(encoding="utf-8"))["pages"]
    feats = load_feats()
    t = _T(a)
    ev = evaluate(labels, feats, t)
    ev["sensitivity"] = sensitivity(labels, feats, t)
    for s, d in ev["sets"].items():
        print(f"{s}: n {d['n']} tp {d['tp']} fp {d['fp']} fn {d['fn']} tn {d['tn']} 정밀 {d['precision']} 재현 {d['recall']}")
    for k, d in sorted(ev["kinds"].items()):
        print(f"  kind {k}: {d['any']}/{d['n']} (갈래 맞음 {d['match']})")
    print("놓침", [(m["key"], m["page"]) for m in ev["missed"]])
    print("헛잡음", [(m["key"], m["page"], m["branch"]) for m in ev["false"]])
    for name, sets in ev["sensitivity"].items():
        print(f"  {name}: " + " · ".join(f"{s} P {d['precision']} R {d['recall']}" for s, d in sets.items()))
    if a.json:
        Path(a.json).write_text(json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--books", default=str(BOOKS))
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("keys", nargs="*")
    sub.add_parser("sample")
    for name in ("render", "sheet"):
        r = sub.add_parser(name)
        r.add_argument("key")
        r.add_argument("pages")
        r.add_argument("--dpi", type=float, default=100)
    for name in ("branch", "eval"):
        p = sub.add_parser(name)
        p.add_argument("--T1", type=float)
        p.add_argument("--T2", type=float)
        p.add_argument("--T3", type=float)
        if name == "eval":
            p.add_argument("--labels", required=True)
            p.add_argument("--json")
    a = ap.parse_args(argv)
    {"scan": cmd_scan, "sample": cmd_sample, "render": cmd_render, "sheet": cmd_sheet, "branch": cmd_branch, "eval": cmd_eval}[a.cmd](a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
