"""정답 표 격자를 짓는 보조 도구 (PDX1 경계 D, 처음 보는 보고서). pypdfium2 로 다시 쓴 판.

`prework/scripts/make_gold.py`(pymupdf, 사전 측정)와 같은 spec 꼴이다: 사람이 표 영역(bbox, pt, 위-왼 원점)과
열 경계(col_x)를 적으면 글자 층 낱말을 그 경계로 잘라 격자를 짓고, 사람이 렌더 이미지와 대조해 merge_rows·span_up·
fixes·drop_rows 로 바로잡는다. 자동 추출 결과가 아니다. docparse 를 부르지 않는다(처음 보는 표의 정답이 docparse 에
기울지 않게).

낱말: pdfium 글자 상자를 같은 줄(글자 가운데가 낱말 세로 범위 안)에서 빈칸 글자나 낱말 높이 0.3배 넘는 틈으로 끊는다.

  uv run --no-project --python 3.12 --with pypdfium2==5.14.0 python -I make_gold.py probe <pdf> <page> x0 y0 x1 y1
  uv run --no-project --python 3.12 --with pypdfium2==5.14.0 python -I make_gold.py build <specs.json> <pdf 폴더> <out 폴더> [id ...]

spec 항목: id, report, pdf(파일 이름), page, bbox, col_x, form, [row_tol 3.0, merge_rows, span_up, fixes, drop_rows, note]
"""

import json
import re
import sys
from pathlib import Path

import pypdfium2 as pdfium


def page_words(pdf, page_no):
    doc = pdfium.PdfDocument(str(pdf))
    try:
        page = doc[page_no - 1]
        h = page.get_height()
        tp = page.get_textpage()
        words, cur = [], None
        for i in range(tp.count_chars()):
            ch = tp.get_text_range(i, 1)
            if not ch or ch in "\r\n":
                cur = None
                continue
            left, bottom, right, top = tp.get_charbox(i)
            y0, y1 = h - top, h - bottom
            if ch.isspace():
                cur = None
                continue
            cy = (y0 + y1) / 2
            # 같은 낱말: 글자 가운데가 그 낱말의 세로 범위 안(쉼표·마침표는 아래로 처짐), 틈이 낱말 높이의 0.3배 안
            if cur and cur["y0"] - 1 <= cy <= cur["y1"] + 1 and -1 <= left - cur["x1"] <= 0.3 * max(1.0, cur["y1"] - cur["y0"]):
                cur["t"] += ch
                cur["x1"] = max(cur["x1"], right)
                cur["y0"], cur["y1"] = min(cur["y0"], y0), max(cur["y1"], y1)
            else:
                cur = {"t": ch, "x0": left, "x1": right, "y0": y0, "y1": y1, "cy": cy}
                words.append(cur)
        return [(w["x0"], w["y0"], w["x1"], w["y1"], w["t"]) for w in words]
    finally:
        doc.close()


def words_in(pdf, page_no, bbox):
    x0, y0, x1, y1 = bbox
    return [w for w in page_words(pdf, page_no) if x0 <= (w[0] + w[2]) / 2 <= x1 and y0 <= (w[1] + w[3]) / 2 <= y1]


def cluster_rows(ws, tol=3.0):
    ws = sorted(ws, key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    rows = []
    for w in ws:
        cy = (w[1] + w[3]) / 2
        if rows and abs(rows[-1]["cy"] - cy) <= tol:
            rows[-1]["ws"].append(w)
            rows[-1]["cy"] = (rows[-1]["cy"] * (len(rows[-1]["ws"]) - 1) + cy) / len(rows[-1]["ws"])
        else:
            rows.append({"cy": cy, "ws": [w]})
    for r in rows:
        r["ws"].sort(key=lambda w: w[0])
    return rows


def build(spec, pdf_dir):
    ws = words_in(Path(pdf_dir) / spec["pdf"], spec["page"], spec["bbox"])
    bounds = spec["col_x"]
    grid = []
    for r in cluster_rows(ws, spec.get("row_tol", 3.0)):
        cells = [[] for _ in range(len(bounds) + 1)]
        for w in r["ws"]:
            cells[sum(1 for b in bounds if (w[0] + w[2]) / 2 > b)].append(w[4])
        # 숫자 칸 안의 빈칸은 글자 층의 자간 탓이다(`1 81` → `181`) — 숫자·쉼표·점·%·- 만이면 붙인다
        grid.append([re.sub(r"\s+", "", t) if re.fullmatch(r"[\d.,%\-\s]+", t) else t for t in (" ".join(c) for c in cells)])
    for a, b in sorted(spec.get("merge_rows", []), key=lambda p: -p[1]):
        grid[a] = [(x + " " + y).strip() for x, y in zip(grid[a], grid[b], strict=False)]
        grid[b] = None
    for row, col, to_row in spec.get("span_up", []):
        grid[to_row][col], grid[row][col] = grid[row][col], ""
    for row, col, val in spec.get("fixes", []):
        grid[row][col] = val
    for d in spec.get("drop_rows", []):
        grid[d] = None
    return [g for g in grid if g is not None and any(g)]


def show(grid):
    for i, r in enumerate(grid):
        print(f"{i:>2} | " + " | ".join(c if c else "·" for c in r))


def main():
    cmd = sys.argv[1]
    if cmd == "probe":
        pdf, page = sys.argv[2], int(sys.argv[3])
        bbox = [float(v) for v in sys.argv[4:8]]
        for i, r in enumerate(cluster_rows(words_in(pdf, page, bbox))):
            print(f"{i:>2} y={r['cy']:.0f} " + "  ".join(f"{w[4]}@{w[0]:.0f}-{w[2]:.0f}" for w in r["ws"]))
    elif cmd == "build":
        specs = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        pdf_dir, out = sys.argv[3], Path(sys.argv[4])
        out.mkdir(parents=True, exist_ok=True)
        only = sys.argv[5:]
        for s in specs:
            if only and s["id"] not in only:
                continue
            grid = build(s, pdf_dir)
            d = {k: s[k] for k in ("id", "report", "page", "bbox", "form")}
            d["lang"] = "kor"
            d["note"] = s.get("note", "")
            d["grid"] = grid
            (out / f"{s['id']}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"== {s['id']} ({len(grid)} rows x {max(len(r) for r in grid)} cols) {s.get('note', '')}")
            show(grid)


if __name__ == "__main__":
    main()
