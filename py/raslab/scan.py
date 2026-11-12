"""래스터 쪽 훑개 (RAS1 G0, `MARC-TICK-480`). 쪽마다 래스터 이미지 조각·글자 층을 센다. 실호출 없음.

  uv run --no-project --python 3.12 --with pypdfium2==5.14.0 python -I scan.py <PDF 폴더> <out> key=<pdf 파일> ...

쪽마다: 이미지 조각(bbox pt 위-왼 원점, 쪽 대비 넓이, 되풀이, form 깊이), 이미지 넓이 합집합 비율(되풀이 뺌),
가장 큰 조각 비율, 글자 층 글자 수(빈칸 뺌)와 밀도(1만 pt² 당), 이미지 영역 안 글자 수.
겹친 글자·되풀이는 `py/pdx/figure_census.py` 정의 그대로: 글자 가운데가 이미지 영역 안이면 겹침,
보고서 안에서 bbox 를 1pt 로 반올림해 같은 것이 둘 이상이면 되풀이.
form XObject 안 조각: pdfium `FPDFPageObj_GetBounds` 는 그 form 의 좌표로 준다 — 품은 form 들의 행렬을 차례로 곱해
쪽 좌표로 바꾼다(`form_level`·`form_matrix_identity` 로 기록).
산출 <out>/<key>.json 과 줄 요약.
"""

import argparse
import json
import sys
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

GRID = 2.0  # 합집합 넓이를 셀 때 칸 크기(pt)
SMALL = 0.01  # 이 비율 아래 조각은 「아주 작음」
RASTER_UNION = 0.10  # 래스터 쪽 = 되풀이·아주 작음 뺀 조각 합집합이 쪽의 10% 이상
BIG = 0.25  # 큰 래스터 조각
FEW_CHARS = 5  # 「글자 층 거의 없음」 = 그 조각 안 글자 이 수 이하
NO_TEXT = 50  # 쪽 글자 층이 이 수 아래면 「글자 층 거의 없는 쪽」
OUTLINE_PATHS = 300  # 그 쪽의 패스 조각이 이 수 이상이면 「글자 윤곽선 쪽」 의심


def apply(m, x, y):
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


def to_page(obj):
    """form 안 조각의 bounds 를 쪽 좌표(아래-왼 원점)로. (x0, y0, x1, y1), 깊이, 행렬이 모두 단위인지."""
    x0, y0, x1, y1 = obj.get_bounds()
    k, ident = obj.container, True
    while k is not None:
        m = k.get_matrix().get()
        if tuple(round(v, 4) for v in m) != (1, 0, 0, 1, 0, 0):
            ident = False
            pts = [apply(m, x, y) for x, y in ((x0, y0), (x0, y1), (x1, y0), (x1, y1))]
            x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
            y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
        k = k.container
    return (x0, y0, x1, y1), obj.level, ident


def union_ratio(boxes, w, h):
    if not boxes:
        return 0.0
    nx, ny = int(w / GRID) + 1, int(h / GRID) + 1
    cells = set()
    for x0, y0, x1, y1 in boxes:
        for i in range(int(x0 / GRID), min(nx, int(x1 / GRID) + 1)):
            for j in range(int(y0 / GRID), min(ny, int(y1 / GRID) + 1)):
                cells.add((i, j))
    return min(1.0, len(cells) * GRID * GRID / (w * h))


def scan(pdf):
    doc = pdfium.PdfDocument(str(pdf))
    pages = []
    try:
        for i in range(len(doc)):
            page = doc[i]
            w, h = page.get_size()
            tp = page.get_textpage()
            chars = []
            for k in range(tp.count_chars()):
                if not tp.get_text_range(k, 1).strip():
                    continue
                left, bottom, right, top = tp.get_charbox(k)
                chars.append(((left + right) / 2, h - (bottom + top) / 2))
            imgs = []
            for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE]):
                (x0, y0, x1, y1), lvl, ident = to_page(obj)
                bx = [max(0, x0), max(0, h - y1), min(w, x1), min(h, h - y0)]
                if bx[2] <= bx[0] or bx[3] <= bx[1]:
                    continue
                n = sum(1 for cx, cy in chars if bx[0] <= cx <= bx[2] and bx[1] <= cy <= bx[3])
                imgs.append(
                    {
                        "bbox": [round(v, 1) for v in bx],
                        "area": round((bx[2] - bx[0]) * (bx[3] - bx[1]) / (w * h), 4),
                        "chars": n,
                        "form_level": lvl,
                        "form_matrix_identity": ident,
                    }
                )
            npath = sum(1 for _ in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_PATH]))
            pages.append(
                {"page": i + 1, "size": [round(w, 1), round(h, 1)], "chars": len(chars), "paths": npath, "images": imgs, "_char_pts": chars}
            )
    finally:
        doc.close()
    seen = {}
    for p in pages:
        for o in p["images"]:
            key = tuple(round(v) for v in o["bbox"])
            seen[key] = seen.get(key, 0) + 1
    for p in pages:
        w, h = p["size"]
        for o in p["images"]:
            o["repeated"] = seen[tuple(round(v) for v in o["bbox"])] > 1
        keep = [o for o in p["images"] if not o["repeated"] and o["area"] >= SMALL]
        boxes = [o["bbox"] for o in keep]
        p["raster_union"] = round(union_ratio(boxes, w, h), 4)
        p["max_piece"] = max((o["area"] for o in keep), default=0.0)
        p["chars_in_images"] = sum(
            1 for cx, cy in p.pop("_char_pts") if any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes)
        )
        p["char_density"] = round(p["chars"] / (w * h) * 1e4, 2)
        p["raster_page"] = p["raster_union"] >= RASTER_UNION
        p["big_textless"] = any(o["area"] >= BIG and o["chars"] <= FEW_CHARS for o in keep)
        # 글자 윤곽선 쪽: 글자 층이 거의 없는데 패스 조각이 많다(글자를 곡선으로 바꾼 PDF — 래스터는 아니지만 파서는 못 본다)
        p["outlined_text"] = p["chars"] < NO_TEXT and p["paths"] >= OUTLINE_PATHS
    return pages


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs")
    ap.add_argument("out")
    ap.add_argument("items", nargs="+", help="key=<pdf 파일>")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    print("| 보고서 | 쪽 | 래스터 쪽 | 비율 | 큰 글없는 래스터 쪽 | 글자 층 거의 없는 쪽(<50자) | 그중 윤곽선 의심 | form 안 조각(단위 아님) |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for key, f in (x.split("=", 1) for x in a.items):
        pages = scan(Path(a.pdfs) / f)
        n = len(pages)
        rp = sum(p["raster_page"] for p in pages)
        bt = sum(p["big_textless"] for p in pages)
        nt = sum(p["chars"] < NO_TEXT for p in pages)
        ol = sum(p["outlined_text"] for p in pages)
        fo = sum(1 for p in pages for o in p["images"] if o["form_level"] > 0)
        fn = sum(1 for p in pages for o in p["images"] if not o["form_matrix_identity"])
        (out / f"{key}.json").write_text(
            json.dumps({"report": key, "pdf": f, "pages": pages}, ensure_ascii=False), encoding="utf-8"
        )
        print(f"| {key} | {n} | {rp} | {rp / max(1, n):.1%} | {bt} | {nt} | {ol} | {fo}({fn}) |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
