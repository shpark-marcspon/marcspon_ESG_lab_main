"""그림 영역 찾기 (RAS1 G2 ③④ 의 앞단). 정답을 보지 않는다. 실호출 없음.

  uv run --no-project --python 3.12 --with pypdfium2==5.14.0 --with pillow==12.3.0 python -I regions.py \
      <PDF 폴더> <scan 폴더> <out.json> key ... [--pages key:1,2,…]

규칙 (G0 훑개 산출만 보고 정함 — 얼린 커밋은 보고에):
  1. 이미지 조각: scan.py 의 조각(쪽 좌표, form 안 포함). 되풀이(같은 자리·크기)와 넓이 MIN_PIECE 미만은 뺀다.
  2. 이웃 합치기: 두 조각의 틈(가로·세로 중 큰 쪽)이 쪽 짧은 변의 GAP 이하이면 한 영역으로(바뀌지 않을 때까지).
  3. 덧길: 글자 층이 NO_TEXT 자 미만이고 1·2 에서 영역이 하나도 없는 쪽은 렌더(BLOB_DPI)에서 바탕색이 아닌 화소
     덩어리(DILATE 화소 넓혀 이음)를 찾아 넓이 MIN_BLOB 이상을 영역으로. 쪽 전체 한 장 스캔이면 1 에서 쪽 전체가 영역.
산출: {key: {page: [{"bbox": [x0,y0,x1,y1] (pt, 위-왼), "src": "image"|"blob", "pieces": n}]}}
"""

import argparse
import json
import sys
from collections import deque
from pathlib import Path

MIN_PIECE = 0.01  # 쪽 대비 넓이
GAP = 0.015  # 쪽 짧은 변 대비
NO_TEXT = 50
BLOB_DPI = 36
BG_TOL = 40  # 바탕색과 RGB 차 합이 이보다 크면 「그린 화소」
DILATE = 3  # 화소(36dpi 에서 약 3pt)
MIN_BLOB = 0.02


def gap(a, b):
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return max(dx, dy)


def merge(boxes, tol):
    rs = [[list(b), 1] for b in boxes]
    changed = True
    while changed:
        changed = False
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                if gap(rs[i][0], rs[j][0]) <= tol:
                    a, b = rs[i][0], rs[j][0]
                    rs[i] = [[min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])], rs[i][1] + rs[j][1]]
                    del rs[j]
                    changed = True
                    break
            if changed:
                break
    return rs


def blobs(pdf, page, size):
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    try:
        im = doc[page - 1].render(scale=BLOB_DPI / 72).to_pil().convert("RGB")
    finally:
        doc.close()
    w, h = im.size
    px = im.load()
    border = [px[x, 0] for x in range(w)] + [px[x, h - 1] for x in range(w)] + [px[0, y] for y in range(h)] + [px[w - 1, y] for y in range(h)]
    bg = max(set(border), key=border.count)
    ink = [[sum(abs(c - d) for c, d in zip(px[x, y], bg, strict=True)) > BG_TOL for x in range(w)] for y in range(h)]
    grow = [[False] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            if ink[y][x]:
                for yy in range(max(0, y - DILATE), min(h, y + DILATE + 1)):
                    row = grow[yy]
                    for xx in range(max(0, x - DILATE), min(w, x + DILATE + 1)):
                        row[xx] = True
    seen = [[False] * w for _ in range(h)]
    out = []
    s = size[0] / w
    for y in range(h):
        for x in range(w):
            if grow[y][x] and not seen[y][x]:
                q, x0, y0, x1, y1 = deque([(x, y)]), x, y, x, y
                seen[y][x] = True
                while q:
                    cx, cy = q.popleft()
                    x0, y0, x1, y1 = min(x0, cx), min(y0, cy), max(x1, cx), max(y1, cy)
                    for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                        if 0 <= nx < w and 0 <= ny < h and grow[ny][nx] and not seen[ny][nx]:
                            seen[ny][nx] = True
                            q.append((nx, ny))
                if (x1 - x0 + 1) * (y1 - y0 + 1) >= MIN_BLOB * w * h:
                    out.append([round(x0 * s, 1), round(y0 * s, 1), round((x1 + 1) * s, 1), round((y1 + 1) * s, 1)])
    return out


def find(pdf, page_rec):
    w, h = page_rec["size"]
    pieces = [o["bbox"] for o in page_rec["images"] if not o["repeated"] and o["area"] >= MIN_PIECE]
    regs = [{"bbox": [round(v, 1) for v in b], "src": "image", "pieces": n} for b, n in merge(pieces, GAP * min(w, h))]
    if not regs and page_rec["chars"] < NO_TEXT:
        regs = [{"bbox": b, "src": "blob", "pieces": 0} for b in blobs(pdf, page_rec["page"], (w, h))]
    return regs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs")
    ap.add_argument("scan")
    ap.add_argument("out")
    ap.add_argument("keys", nargs="+")
    ap.add_argument("--pages", default="", help="key:1,2,3;key2:4 — 이 쪽만(없으면 전 쪽)")
    a = ap.parse_args(argv)
    only = {}
    for part in filter(None, a.pages.split(";")):
        k, ps = part.split(":")
        only[k] = {int(p) for p in ps.split(",")}
    res = {}
    for key in a.keys:
        d = json.loads((Path(a.scan) / f"{key}.json").read_text(encoding="utf-8"))
        res[key] = {}
        n_img = n_blob = 0
        for p in d["pages"]:
            if key in only and p["page"] not in only[key]:
                continue
            regs = find(Path(a.pdfs) / d["pdf"], p)
            if regs:
                res[key][p["page"]] = regs
                n_img += sum(r["src"] == "image" for r in regs)
                n_blob += sum(r["src"] == "blob" for r in regs)
        print(f"{key}: 쪽 {len(d['pages'])} · 영역 있는 쪽 {len(res[key])} · 영역 {n_img + n_blob} (조각 {n_img} · 덧길 {n_blob})")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
