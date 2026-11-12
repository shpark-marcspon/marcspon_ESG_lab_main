"""TRX11 — 글자 층 없는 쪽 훑개의 raster 갈래 앞에 「사진 가르기」 한 겹(①), outline 갈래 다듬기(②), 작은 그림 묶음(③). 실호출 0 · PIL 0(numpy 렌더).
`ras2_scan`(TRX9) 위에 얹는다 — `ras2_scan`·`scan` 은 import 만, 0줄 고침. 라벨(`prework/gold-trx9/pages.json`)은 보지 않는다.

  uv run --frozen --group scope3colab python -m raslab.ras3_photo scan [--books <books.json>] [keys…]   → OUT11/scan/<키>.json (+ times.json)
  … ras3_photo render <키> <쪽,쪽,…> [--dpi 100]                      → OUT11/render/<키>-p<쪽>.png

쪽마다 더하는 것(`out/trx9/scan` 레코드를 다시 쓰지 않고 PDF 에서 다시 센다):
  pieces3 — 큰 조각(ras2_scan.big_pieces)과 ③a 묶음마다
     {bbox, area, src(big|cluster), n(묶음 조각 수), score(108dpi 글 줄 점수, TRX9 와 같은 셈), lines, score216·lines216(넓이 < RESCORE_BELOW 면 216dpi 재점수),
      bg_frac, n_colors, sat_med, edge_density(사진 특징 ①), jpeg, colorspace, bpp(이미지 메타 — bbox 로 짝지음), kind(photo|text|small)}
  small_paths — 글자 크기 패스(bbox 높이 ≤ 14pt and 너비 ≤ 40pt) 수 ②, small_path_density(/1만 pt²)
  n_clusters — ③a 묶음 수

① 사진 특징(조각을 108dpi 로 자르고 가장자리 MARGIN 을 버린 뒤, 화소가 MAX_PX 를 넘으면 성기게 뽑아):
  bg_frac      — 최빈색(ras2_scan.mode_color)과 RGB 차 합 ≤ INK 인 화소 비율(바탕 비율). 글·표·스캔·인포그래픽은 크고 사진은 작다.
  n_colors     — 채널 5bit 양자화(32,768 칸) 뒤 화소의 COVER(95%)를 덮는 데 드는 칸 수(내림차순 누적). 사진 수백~수천, 평면 그림·글 수십.
  sat_med      — 화소별 (max−min)/max(1,max) 의 중앙값(채도). 보조(단독 금지 — 컬러 인포그래픽도 높다).
  edge_density — 회색으로 바꾼 뒤 가로·세로 이웃 차 절대값 > EDGE 인 화소 비율. 글은 높고 사진은 중간.
  jpeg·colorspace·bpp — pypdfium2 이미지 메타(DCTDecode 여부, 1 Gray·2 RGB·3 CMYK). 보조(스캔도 JPEG).
  가르기 is_photo(pf, rule) — 특징 둘 이하의 AND(PHOTO_RULE). 사진 조각은 raster 셈에서 빠진다(쪽을 통째로 버리지 않는다).
② outline' = small_paths ≥ T4 and small_paths ≥ R × chars (옛 규칙 chars < 50 and (밀도 ≥ T3 or paths ≥ 300) 은 ras2_scan 에 그대로).
③a 묶음 — 되풀이 아님·겹친 글자 ≤ FEW·넓이 ≥ FLOOR 인 조각의 bbox 를 쪽 짧은 변의 GAP 만큼 넓혀 겹치는 것끼리 묶고, 합집합 ≥ T5 이면 묶음 bbox 를 조각처럼 렌더·점수.
    큰 조각(≥ BIG_PIECE)뿐인 묶음은 이미 big 조각이라 빼고(작은 조각이 하나라도 든 묶음만), 묶음은 더하기만 한다(큰 조각의 셈은 그대로).
③b 재점수 — 넓이 < RESCORE_BELOW 인 조각(묶음 포함)을 216dpi(RESCORE_SCALE)로 다시 렌더해 stripe_score(arr, RESCORE_SCALE).
문턱 PHOTO_RULE·T4·R·T5 는 개발 셋(TRX9 dev + ras3_eval.DEV_ADD)으로 정해 얼린다(out/trx11/first-run.txt 둘째 줄).
"""

import argparse
import json
import operator
import os
import sys
import time
from pathlib import Path

import numpy as np

from raslab import ras2_scan as r2
from raslab import scan as base

OUT11 = Path(os.environ.get("TRX11_OUT", str(Path.home() / "Desktop" / "marcspon" / "esg-lab" / "out" / "trx11")))

INK = r2.INK
MARGIN = r2.MARGIN
EDGE = 40
COVER = 0.95
QSHIFT = 3  # 8 − 3 = 5bit
MAX_PX = 400_000
SMALL_PT = (14.0, 40.0)  # ② 글자 크기 패스: 높이 ≤ 14pt and 너비 ≤ 40pt
FLOOR = 0.005  # ③a 묶음에 드는 조각 바닥(쪽 넓이 비율)
GAP = 0.015  # ③a bbox 넓힘(쪽 짧은 변 비율)
RESCORE_BELOW = 0.05  # ③b 이 넓이 아래 조각은 216dpi 재점수
RESCORE_SCALE = r2.PIECE_SCALE * 2  # 216dpi
OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "==": operator.eq}

# ---- 문턱 (개발 셋으로 정해 얼림 — first-run.txt 둘째 줄) ----
# 개발 셋(TRX9 dev 47 + b7·b3·nb·nb2 40 = 87쪽, 양성 45) 격자(ras3_eval grid, out/trx11/grid-dev.json)로 정해 얼림 — 2026-11-05 06:2x, first-run.txt 둘째 줄.
PHOTO_RULE = [["bg_frac", "<", 0.436]]  # ① 특징 하나로 개발 셋 P 1.0(헛잡음 6→0)·tp 42 그대로; 둘짜리(bg_frac+sat_med 등)는 동률이라 「특징 적은 쪽」
T4 = 100  # ② small_paths ≥ T4 — 격자 42칸 전부 동률(tp 41 fp 6) → 출발값
R = 1.5  # ② small_paths ≥ R × chars — 위와 같음
T5 = 0.01  # ③a 묶음 합집합 ≥ T5 — 0.01·parts ab 가 유일하게 tp +1(b7 p74)


# ---------- ① 사진 특징 ----------
def _trim(arr):
    my, mx = int(arr.shape[0] * MARGIN), int(arr.shape[1] * MARGIN)
    return arr[my : arr.shape[0] - my, mx : arr.shape[1] - mx]


def _thin(arr, max_px=MAX_PX):
    n = arr.shape[0] * arr.shape[1]
    if n <= max_px:
        return arr
    k = int(np.ceil(np.sqrt(n / max_px)))
    return arr[::k, ::k]


EMPTY_PF = {"bg_frac": None, "n_colors": None, "sat_med": None, "edge_density": None}


def photo_features(arr):
    """arr (h, w, 3) uint8(조각 자름) → {bg_frac, n_colors, sat_med, edge_density}. 너무 작으면 None 값."""
    if arr.size == 0 or arr.shape[0] < 8 or arr.shape[1] < 8:
        return dict(EMPTY_PF)
    a = _thin(_trim(arr))
    if a.shape[0] < 2 or a.shape[1] < 2:
        return dict(EMPTY_PF)
    g = a.astype(np.int16)
    bg_frac = float((np.abs(g - r2.mode_color(a)).sum(axis=2) <= INK).mean())
    q = (a >> QSHIFT).reshape(-1, 3).astype(np.int32)
    cnt = np.sort(np.bincount(q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2], minlength=32768))[::-1]
    cum = np.cumsum(cnt)
    n_colors = int(np.searchsorted(cum, COVER * cum[-1]) + 1)
    mx = a.max(axis=2).astype(np.float32)
    mn = a.min(axis=2).astype(np.float32)
    sat_med = float(np.median((mx - mn) / np.maximum(1.0, mx)))
    gray = g.mean(axis=2)
    dx = np.abs(gray[:, 1:] - gray[:, :-1]) > EDGE
    dy = np.abs(gray[1:, :] - gray[:-1, :]) > EDGE
    edge = float((dx[1:, :] | dy[:, 1:]).mean())
    return {"bg_frac": round(bg_frac, 4), "n_colors": n_colors, "sat_med": round(sat_med, 4), "edge_density": round(edge, 4)}


def is_photo(pf, rule=None):
    """특징 둘 이하의 AND. 값이 None(못 셈)이면 사진 아님."""
    rule = PHOTO_RULE if rule is None else rule
    if not 1 <= len(rule) <= 2:
        raise ValueError("가르기 규칙은 특징 하나나 둘의 AND 만")
    for name, op, v in rule:
        x = pf.get(name)
        if x is None or not OPS[op](x, v):
            return False
    return True


# ---------- ② 글자 크기 패스 ----------
def path_boxes(page):
    """쪽의 패스 객체 bbox 목록(쪽 좌표, 위-왼 원점; form 안은 scan.to_page 로 쪽 좌표로)."""
    import pypdfium2.raw as pdfium_c

    w, h = page.get_size()
    out = []
    for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_PATH]):
        (x0, y0, x1, y1), _, _ = base.to_page(obj)
        out.append((x0, h - y1, x1, h - y0))
    return out


def small_paths(boxes, small=SMALL_PT):
    """글자 크기 패스 수: 높이 ≤ small[0] and 너비 ≤ small[1] (pt)."""
    return sum(1 for x0, y0, x1, y1 in boxes if (y1 - y0) <= small[0] and (x1 - x0) <= small[1])


def outline2(f, T4=T4, R=R):
    """② outline' = small_paths ≥ T4 and small_paths ≥ R × chars."""
    return f["small_paths"] >= T4 and f["small_paths"] >= R * f["chars"]


# ---------- ③a 작은 그림 묶음 ----------
def _overlap(a, b):
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def clusters(rec, floor=FLOOR, gap=GAP, t5=T5):
    """되풀이 아님·겹친 글자 ≤ FEW·넓이 ≥ floor 인 조각을 bbox 를 쪽 짧은 변의 gap 만큼 넓혀 겹치는 것끼리 묶는다.
    → [{bbox, area(합집합 비율), n, members:[조각 bbox]}] 중 합집합 ≥ t5 이고 작은 조각(< BIG_PIECE)이 하나라도 든 것(큰 조각뿐이면 이미 big 조각)."""
    w, h = rec["size"]
    cand = [o for o in rec["images"] if not o["repeated"] and o["area"] >= floor and o["chars"] <= r2.FEW]
    if not cand:
        return []
    g = min(w, h) * gap
    grown = [(o["bbox"][0] - g, o["bbox"][1] - g, o["bbox"][2] + g, o["bbox"][3] + g) for o in cand]
    parent = list(range(len(cand)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(cand)):
        for j in range(i + 1, len(cand)):
            if _overlap(grown[i], grown[j]):
                parent[find(i)] = find(j)
    groups = {}
    for i, o in enumerate(cand):
        groups.setdefault(find(i), []).append(o)
    out = []
    for members in groups.values():
        if all(o["area"] >= r2.BIG_PIECE for o in members):
            continue  # 큰 조각뿐인 묶음은 이미 big 조각으로 셈한다
        boxes = [o["bbox"] for o in members]
        area = round(base.union_ratio(boxes, w, h), 4)
        if area < t5:
            continue
        bbox = [round(min(b[0] for b in boxes), 1), round(min(b[1] for b in boxes), 1), round(max(b[2] for b in boxes), 1), round(max(b[3] for b in boxes), 1)]
        out.append({"bbox": bbox, "area": area, "n": len(members), "members": boxes})
    out.sort(key=lambda c: (c["bbox"][1], c["bbox"][0]))
    return out


# ---------- 조각 특징 (①·③b·메타) ----------
def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def image_meta(page):
    """쪽의 이미지 객체마다 {bbox(위-왼 원점), jpeg, colorspace, bpp}. bbox 셈은 scan.scan 과 같다."""
    import pypdfium2.raw as pdfium_c

    w, h = page.get_size()
    out = []
    for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE]):
        (x0, y0, x1, y1), _, _ = base.to_page(obj)
        bx = [max(0, x0), max(0, h - y1), min(w, x1), min(h, h - y0)]
        if bx[2] <= bx[0] or bx[3] <= bx[1]:
            continue
        try:
            filters = obj.get_filters()
            m = obj.get_metadata()
            cs, bpp = int(m.colorspace), int(m.bits_per_pixel)
        except Exception:
            filters, cs, bpp = [], None, None
        out.append({"bbox": bx, "jpeg": any(f in ("DCTDecode", "JPXDecode") for f in filters), "colorspace": cs, "bpp": bpp})
    return out


def match_meta(bbox, metas):
    best, bi = 0.0, None
    for m in metas:
        v = _iou(bbox, m["bbox"])
        if v > best:
            best, bi = v, m
    return bi if best >= 0.5 else None


def crop216(page, bbox):
    """조각을 216dpi 로 다시 렌더(쪽 전체가 아니라 crop)."""
    w, h = page.get_size()
    x0, y0, x1, y1 = bbox
    bm = page.render(scale=RESCORE_SCALE, crop=(max(0, x0), max(0, h - y1), max(0, w - x1), max(0, y0)), rev_byteorder=True)
    a = bm.to_numpy()
    return np.ascontiguousarray(a[:, :, :3])


def piece_record(page, arr108, bbox, area, src, n, metas, rescore=True):
    crop = r2.crop_px(arr108, bbox, r2.PIECE_SCALE)
    sc, nl = r2.stripe_score(crop, r2.PIECE_SCALE)
    rec = {"bbox": bbox, "area": area, "src": src, "n": n, "score": sc, "lines": nl, "score216": None, "lines216": None}
    rec.update(photo_features(crop))
    if src == "big":
        m = match_meta(bbox, metas)
    else:
        ms = inside(bbox, metas)
        m = {"jpeg": all(x["jpeg"] for x in ms), "colorspace": ms[0]["colorspace"], "bpp": ms[0]["bpp"]} if ms else None
    rec["jpeg"] = m["jpeg"] if m else None
    rec["colorspace"] = m["colorspace"] if m else None
    rec["bpp"] = m["bpp"] if m else None
    if rescore and area < RESCORE_BELOW:
        sc2, nl2 = r2.stripe_score(crop216(page, bbox), RESCORE_SCALE)
        rec["score216"], rec["lines216"] = sc2, nl2
    return rec


def inside(bbox, metas):
    """묶음 bbox 안에 든 이미지 메타들(묶음의 jpeg = 조각 전부 jpeg)."""
    return [m for m in metas if m["bbox"][0] >= bbox[0] - 1 and m["bbox"][1] >= bbox[1] - 1 and m["bbox"][2] <= bbox[2] + 1 and m["bbox"][3] <= bbox[3] + 1]


def kind_of(p, rule=None):
    if is_photo(p, rule):
        return "photo"
    return "small" if p["src"] == "cluster" or p["area"] < r2.BIG_PIECE else "text"


def page_features(rec, page):
    """scan 쪽 레코드 + 열린 pdfium 쪽 → {pieces3, small_paths, small_path_density, n_clusters, t_paths, t_render}."""
    w, h = rec["size"]
    t0 = time.time()
    boxes = path_boxes(page)
    sp = small_paths(boxes)
    t1 = time.time()
    big = r2.big_pieces(rec)
    cl = clusters(rec, t5=FLOOR)  # 합집합 ≥ FLOOR 를 다 적고 T5 는 ras3_eval 이 건다(민감도 T5×0.5 를 셀 수 있게)
    pieces = []
    if big or cl:
        metas = image_meta(page)
        arr = r2.render(page, r2.PIECE_SCALE)
        for o in big:
            pieces.append(piece_record(page, arr, o["bbox"], o["area"], "big", 1, metas))
        for c in cl:
            pieces.append(piece_record(page, arr, c["bbox"], c["area"], "cluster", c["n"], metas))
    t2 = time.time()
    for p in pieces:
        p["kind"] = kind_of(p)
    return {
        "pieces3": pieces,
        "small_paths": sp,
        "small_path_density": round(sp / (w * h / 1e4), 3),
        "n_clusters": len(cl),
        "t_paths": round(t1 - t0, 4),
        "t_render": round(t2 - t1, 4),
    }


# ---------- 명령 ----------
def cmd_scan(a):
    import pypdfium2 as pdfium

    pdfs, books = r2.load_books(a.books)
    keys = a.keys or list(books)
    (OUT11 / "scan").mkdir(parents=True, exist_ok=True)
    tpath = OUT11 / "scan" / "times.json"
    times = json.loads(tpath.read_text()) if tpath.is_file() else {}
    for key in keys:
        t0 = time.time()
        pdf = pdfs / books[key]["pdf"]
        pages = base.scan(pdf)
        t1 = time.time()
        doc = pdfium.PdfDocument(str(pdf))
        n_render = 0
        tp = tr = 0.0
        try:
            for rec in pages:
                page = doc[rec["page"] - 1]
                rec["features"] = r2.features(rec, None)  # 렌더 없는 특징(textless_big·path_density…); 점수는 pieces3 에
                f3 = page_features(rec, page)
                tp += f3.pop("t_paths")
                tr += f3.pop("t_render")
                n_render += bool(f3["pieces3"])
                rec["features3"] = f3
        finally:
            doc.close()
        t2 = time.time()
        (OUT11 / "scan" / f"{key}.json").write_text(json.dumps({"key": key, "pages": pages}, ensure_ascii=False), encoding="utf-8")
        times[key] = {
            "pages": len(pages),
            "scan_s": round(t1 - t0, 2),
            "paths_s": round(tp, 2),
            "render_s": round(tr, 2),
            "feat_s": round(t2 - t1, 2),
            "rendered_pages": n_render,
            "ms_per_page": round((t2 - t0) / len(pages) * 1000, 1),
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        tpath.write_text(json.dumps(times, ensure_ascii=False, indent=1), encoding="utf-8")
        print(
            f"{key}: 쪽 {len(pages)} · 훑기 {t1 - t0:.1f}s · 패스 {tp:.1f}s · 렌더 {tr:.1f}s(쪽 {n_render}) · 합 {t2 - t0:.1f}s · "
            f"묶음 {sum(r['features3']['n_clusters'] for r in pages)} · 쪽당 {times[key]['ms_per_page']}ms",
            flush=True,
        )


def cmd_render(a):
    import pypdfium2 as pdfium

    from trlab.thumb_vision import png_bytes

    pdfs, books = r2.load_books(a.books)
    (OUT11 / "render").mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(pdfs / books[a.key]["pdf"]))
    try:
        for pg in (int(x) for x in a.pages.split(",") if x):
            arr = r2.render(doc[pg - 1], a.dpi / 72)
            p = OUT11 / "render" / f"{a.key}-p{pg}.png"
            p.write_bytes(png_bytes(arr))
            print(p, arr.shape[1], arr.shape[0])
    finally:
        doc.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--books", default=str(r2.BOOKS))
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("keys", nargs="*")
    r = sub.add_parser("render")
    r.add_argument("key")
    r.add_argument("pages")
    r.add_argument("--dpi", type=float, default=100)
    a = ap.parse_args(argv)
    {"scan": cmd_scan, "render": cmd_render}[a.cmd](a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
