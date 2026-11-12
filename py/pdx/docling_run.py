"""Docling 으로 쪽을 분해해 표 격자와 읽는 차례 글 흐름을 쓴다 (PDX1 후보 ②). CPU, OCR 끔.

의존은 저장소에 넣지 않고 그 자리에서만 쓴다:
  uv run --no-project --python 3.12 --with docling==2.138.0 python -I docling_run.py \
      --pdf <pdf> --name <report> --pages 64,66 --out <산출 폴더> [--mode accurate|fast]

산출:
  <out>/tables/<report>_p<page>.json   [{"bbox":[x0,y0,x1,y1] (위-왼 원점, pt), "grid":[[...]]}] — Docling 격자 그대로
  <out>/tables_origin/…               같은 꼴, 병합 칸 글은 원점 칸에만
  <out>/streams/<report>_p<page>.txt   읽는 차례대로 이은 글 (표 자리에는 칸 글을 행 우선으로)
  <out>/timing.jsonl                   쪽마다 걸린 초
"""

import argparse
import json
import time
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.document import TableItem
from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
from docling.document_converter import DocumentConverter, PdfFormatOption


def build(mode):
    opts = PdfPipelineOptions()
    opts.do_ocr = False
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.ACCURATE if mode == "accurate" else TableFormerMode.FAST
    opts.table_structure_options.do_cell_matching = True  # 칸 글은 PDF 글자 층에서
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})


def grid_of(t):
    return [[c.text for c in row] for row in t.data.grid]


def grid_origin(t):
    """병합 칸의 글을 원점(맨 위·맨 왼쪽) 칸에만 둔 격자. Docling 의 `data.grid` 는 펼친 칸마다 같은 글을 되풀이한다."""
    g = [["" for _ in range(t.data.num_cols)] for _ in range(t.data.num_rows)]
    for c in t.data.table_cells:
        g[c.start_row_offset_idx][c.start_col_offset_idx] = c.text
    return g


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--pages", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=("accurate", "fast"), default="accurate")
    a = ap.parse_args(argv)
    out = Path(a.out)
    (out / "tables").mkdir(parents=True, exist_ok=True)
    (out / "streams").mkdir(parents=True, exist_ok=True)
    (out / "tables_origin").mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    conv = build(a.mode)
    init_s = time.time() - t0
    for pg in (int(x) for x in a.pages.split(",")):
        t1 = time.time()
        doc = conv.convert(a.pdf, page_range=(pg, pg)).document
        secs = time.time() - t1
        h = doc.pages[pg].size.height
        tables, origin, parts = [], [], []
        for item, _level in doc.iterate_items(page_no=pg):
            if isinstance(item, TableItem):
                g = grid_of(item)
                bb = item.prov[0].bbox.to_top_left_origin(page_height=h)
                tables.append({"bbox": [bb.l, bb.t, bb.r, bb.b], "grid": g})
                origin.append({"bbox": [bb.l, bb.t, bb.r, bb.b], "grid": grid_origin(item)})
                parts.append(" ".join(c for row in g for c in row if c))
            elif getattr(item, "text", None):
                parts.append(item.text)
        (out / "tables" / f"{a.name}_p{pg}.json").write_text(json.dumps(tables, ensure_ascii=False), encoding="utf-8")
        (out / "tables_origin" / f"{a.name}_p{pg}.json").write_text(json.dumps(origin, ensure_ascii=False), encoding="utf-8")
        (out / "streams" / f"{a.name}_p{pg}.txt").write_text("\n".join(parts), encoding="utf-8")
        with open(out / "timing.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"name": a.name, "page": pg, "mode": a.mode, "secs": round(secs, 2), "init_secs": round(init_s, 2)}) + "\n")
        print(a.name, pg, f"{secs:.1f}s", len(tables), "tables")


if __name__ == "__main__":
    main()
