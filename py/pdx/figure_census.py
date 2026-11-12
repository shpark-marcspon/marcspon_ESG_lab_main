"""이미지 그림 세기 (PDX1 경계 F2). 쪽마다 래스터 이미지 조각의 위치·크기를 꺼내 큰 것 중 글자 층이 겹치지 않는 것을 고른다.

  # 세기: <out>/census.json (이미지마다 report·page·bbox(pt, 위-왼 원점)·넓이 비율·겹친 글자 수)
  uv run --no-project --python 3.12 --with pypdfium2==5.14.0 python -I figure_census.py census <PDF 폴더> <out> report=<pdf 파일> ...
  # 표본: 후보(넓이 비율 --min-area 넘고 겹친 글자 --max-chars 이하)에서 --n 개를 골라 낱장 PNG 와 한 장 모음
  uv run ... --with pillow==12.3.0 python -I figure_census.py sample <PDF 폴더> <out> [--n 30 --min-area 0.05 --max-chars 5 --seed 1]

겹친 글자 = 그 쪽 글자 층 글자(빈칸 빼고) 중 가운데가 이미지 영역 안인 것. 같은 자리의 같은 이미지(쪽마다 되풀이되는 배경·로고)는
보고서 안에서 한 번만 센다(자리·크기를 1pt 로 반올림해 같으면 되풀이).
"""

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c


def census(pdf, report):
    doc = pdfium.PdfDocument(str(pdf))
    out = []
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
            for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE]):
                x0, y0, x1, y1 = obj.get_bounds()
                bx = [max(0, x0), max(0, h - y1), min(w, x1), min(h, h - y0)]
                if bx[2] <= bx[0] or bx[3] <= bx[1]:
                    continue
                area = (bx[2] - bx[0]) * (bx[3] - bx[1]) / (w * h)
                n = sum(
                    1
                    for cx, cy in chars
                    if bx[0] <= cx <= bx[2] and bx[1] <= cy <= bx[3]
                )
                out.append(
                    {
                        "report": report,
                        "page": i + 1,
                        "bbox": [round(v, 1) for v in bx],
                        "area": round(area, 4),
                        "chars": n,
                    }
                )
    finally:
        doc.close()
    seen, keep = Counter(tuple(round(v) for v in o["bbox"]) for o in out), []
    for o in out:
        o["repeated"] = seen[tuple(round(v) for v in o["bbox"])] > 1
        keep.append(o)
    return keep


def cmd_census(a):
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    allimg, pages = [], {}
    for k, v in (x.split("=", 1) for x in a.items):
        imgs = census(Path(a.pdfs) / v, k)
        allimg += imgs
        pages[k] = len(pdfium.PdfDocument(str(Path(a.pdfs) / v)))
        big = [o for o in imgs if o["area"] >= a.min_area and not o["repeated"]]
        print(
            f"{k}: 쪽 {pages[k]} · 이미지 {len(imgs)} · 넓이 {a.min_area:.0%} 넘고 되풀이 아님 {len(big)} · 그중 겹친 글자 {a.max_chars} 이하 "
            f"{sum(o['chars'] <= a.max_chars for o in big)}"
        )
    (out / "census.json").write_text(
        json.dumps({"pages": pages, "images": allimg}, ensure_ascii=False),
        encoding="utf-8",
    )


def cmd_sample(a):
    from PIL import Image

    out = Path(a.out)
    data = json.loads((out / "census.json").read_text(encoding="utf-8"))
    cand = [
        o
        for o in data["images"]
        if o["area"] >= a.min_area and not o["repeated"] and o["chars"] <= a.max_chars
    ]
    random.Random(a.seed).shuffle(cand)
    pick = cand[: a.n]
    (out / "sample").mkdir(exist_ok=True)
    files = {}
    thumbs = []
    for j, o in enumerate(pick):
        pdf = files.setdefault(
            o["report"], pdfium.PdfDocument(str(Path(a.pdfs) / a.map[o["report"]]))
        )
        im = (
            pdf[o["page"] - 1]
            .render(scale=1.5)
            .to_pil()
            .crop([int(v * 1.5) for v in o["bbox"]])
        )
        im.save(out / "sample" / f"{j:02d}_{o['report']}_p{o['page']}.png")
        im.thumbnail((360, 260))
        thumbs.append(im)
        o["sample"] = j
    sheet = Image.new("RGB", (6 * 370, ((len(thumbs) + 5) // 6) * 270), "white")
    for j, im in enumerate(thumbs):
        sheet.paste(im, ((j % 6) * 370, (j // 6) * 270))
    sheet.save(out / "sample_sheet.png")
    (out / "sample.json").write_text(
        json.dumps(pick, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"후보 {len(cand)} 중 {len(pick)} 표본")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("census", "sample"))
    ap.add_argument("pdfs")
    ap.add_argument("out")
    ap.add_argument("items", nargs="*", help="census: report=<pdf 파일>")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--min-area", type=float, default=0.05)
    ap.add_argument("--max-chars", type=int, default=5)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)
    if a.cmd == "sample":
        sys.path.insert(
            0, str(Path(__file__).resolve().parent)
        )  # python -I 는 스크립트 폴더를 경로에 넣지 않는다
        from vision_run import REPORT_FILES

        a.map = REPORT_FILES
    {"census": cmd_census, "sample": cmd_sample}[a.cmd](a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
