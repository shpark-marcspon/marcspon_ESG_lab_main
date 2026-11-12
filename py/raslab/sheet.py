"""래스터 쪽 모음 그림 (RAS1 G0 눈 확인용). scan.py 산출에서 래스터 쪽을 렌더해 한 장에 늘어놓는다. 실호출 없음.

  uv run --no-project --python 3.12 --with pypdfium2==5.14.0 --with pillow==12.3.0 python -I sheet.py <PDF 폴더> <scan 폴더> <out 폴더> key ... [--cols 5 --thumb 420 --all]
  # 조각 모음(눈 분류): ... sheet.py crops <PDF 폴더> <scan 폴더> <out 폴더> key ... [--min-area 0.02 --max-chars 5]
  # 한 쪽 확대: ... sheet.py page <PDF 폴더> <out.png> <pdf 파일> <쪽> [--dpi 200] [--bbox x0,y0,x1,y1]
래스터 조각(되풀이·아주 작음 뺌)은 빨간 테로 그린다.
"""

import argparse
import json
import sys
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageDraw

SMALL = 0.01


def render(pdf, page, scale):
    doc = pdfium.PdfDocument(str(pdf))
    try:
        return doc[page - 1].render(scale=scale).to_pil().convert("RGB")
    finally:
        doc.close()


def cmd_sheet(a):
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for key in a.keys:
        d = json.loads((Path(a.scan) / f"{key}.json").read_text(encoding="utf-8"))
        pages = [p for p in d["pages"] if a.all or p["raster_page"]]
        thumbs = []
        for p in pages:
            w, h = p["size"]
            s = a.thumb / max(w, h)
            im = render(Path(a.pdfs) / d["pdf"], p["page"], s)
            dr = ImageDraw.Draw(im)
            for o in p["images"]:
                if not o["repeated"] and o["area"] >= SMALL:
                    dr.rectangle([v * s for v in o["bbox"]], outline=(255, 0, 0), width=2)
            dr.rectangle([0, 0, 60, 18], fill=(0, 0, 0))
            dr.text((3, 3), f"p{p['page']}", fill=(255, 255, 0))
            thumbs.append(im)
        if not thumbs:
            continue
        cw = max(t.size[0] for t in thumbs) + 6
        ch = max(t.size[1] for t in thumbs) + 6
        rows = (len(thumbs) + a.cols - 1) // a.cols
        sheet = Image.new("RGB", (a.cols * cw, rows * ch), "white")
        for j, t in enumerate(thumbs):
            sheet.paste(t, ((j % a.cols) * cw, (j // a.cols) * ch))
        sheet.save(out / f"{key}.png")
        print(key, len(thumbs), "쪽")


def cmd_crops(a):
    """후보 조각(넓이 --min-area 이상, 되풀이 아님, 겹친 글자 --max-chars 이하)을 잘라 번호 붙여 한 장에. 눈으로 분류할 때."""
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for key in a.keys:
        d = json.loads((Path(a.scan) / f"{key}.json").read_text(encoding="utf-8"))
        cand = [
            (p["page"], o)
            for p in d["pages"]
            for o in p["images"]
            if o["area"] >= a.min_area and not o["repeated"] and o["chars"] <= a.max_chars
        ]
        thumbs, index = [], []
        doc = pdfium.PdfDocument(str(Path(a.pdfs) / d["pdf"]))
        try:
            for j, (pg, o) in enumerate(cand):
                im = doc[pg - 1].render(scale=1.0).to_pil().convert("RGB").crop([int(v) for v in o["bbox"]])
                im.thumbnail((a.thumb, a.thumb))
                dr = ImageDraw.Draw(im)
                dr.rectangle([0, 0, 70, 14], fill=(0, 0, 0))
                dr.text((2, 1), f"{j} p{pg}", fill=(255, 255, 0))
                thumbs.append(im)
                index.append({"n": j, "page": pg, "bbox": o["bbox"], "area": o["area"]})
        finally:
            doc.close()
        if not thumbs:
            print(key, 0)
            continue
        c = a.thumb + 6
        rows = (len(thumbs) + a.cols - 1) // a.cols
        sheet = Image.new("RGB", (a.cols * c, rows * c), "white")
        for j, t in enumerate(thumbs):
            sheet.paste(t, ((j % a.cols) * c, (j // a.cols) * c))
        sheet.save(out / f"{key}_crops.png")
        (out / f"{key}_crops.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
        print(key, len(thumbs), "조각")


def cmd_page(a):
    im = render(Path(a.pdfs) / a.pdf, a.page, a.dpi / 72)
    if a.bbox:
        s = a.dpi / 72
        im = im.crop([float(v) * s for v in a.bbox.split(",")])
    im.save(a.out)
    print(a.out, im.size)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "crops":
        ap = argparse.ArgumentParser()
        ap.add_argument("cmd")
        ap.add_argument("pdfs")
        ap.add_argument("scan")
        ap.add_argument("out")
        ap.add_argument("keys", nargs="+")
        ap.add_argument("--min-area", type=float, default=0.02)
        ap.add_argument("--max-chars", type=int, default=5)
        ap.add_argument("--cols", type=int, default=8)
        ap.add_argument("--thumb", type=int, default=240)
        cmd_crops(ap.parse_args(argv))
        return 0
    if argv and argv[0] == "page":
        ap = argparse.ArgumentParser()
        ap.add_argument("cmd")
        ap.add_argument("pdfs")
        ap.add_argument("out")
        ap.add_argument("pdf")
        ap.add_argument("page", type=int)
        ap.add_argument("--dpi", type=int, default=200)
        ap.add_argument("--bbox")
        cmd_page(ap.parse_args(argv))
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs")
    ap.add_argument("scan")
    ap.add_argument("out")
    ap.add_argument("keys", nargs="+")
    ap.add_argument("--cols", type=int, default=5)
    ap.add_argument("--thumb", type=int, default=420)
    ap.add_argument("--all", action="store_true", help="래스터 쪽만이 아니라 모든 쪽")
    cmd_sheet(ap.parse_args(argv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
