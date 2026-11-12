"""docparse 산출(document.json) → 번역 모듈이 받는 구조 글(txt) 시험 변환 (PDX1 §1.4). 표준 라이브러리만.

꼴은 번역 모듈의 docx 읽개가 내는 것을 따른다(esg-app `apps/api-py/esgapi/translation/tools/docx_reader.py` 머리말):
쪽마다 한 줄 `[PAGE n]`, 글 블록은 `[BLOCK]` 다음 줄에 글, 표는 `[MARKDOWN_TABLE]` 다음 줄부터 pipe 표(칸의 `|`·줄바꿈은
`\\|`·빈칸으로), 표 뒤 빈 줄. 쪽 머리·꼬리 띠(쪽 높이 위 5%·아래 8%)는 `[HEADER]`·`[FOOTER]` 로 따로 둔다.

python3 to_page_tags.py --doc <document.json> --out <txt> [--pages 26,40]
"""

import argparse
import json
from pathlib import Path


def cell(s):
    return " ".join((s or "").split()).replace("|", "\\|")


def table_lines(rows):
    kept = [[cell(c) for c in r] for r in rows if any((c or "").strip() for c in r)]
    if not kept:
        return []
    n = max(len(r) for r in kept)
    kept = [r + [""] * (n - len(r)) for r in kept]
    return ["| " + " | ".join(kept[0]) + " |", "| " + " | ".join(["---"] * n) + " |"] + [
        "| " + " | ".join(r) + " |" for r in kept[1:]
    ]


def convert(doc, pages=None):
    tables = {t["id"]: t for t in doc["tables"]}
    heights = {p["page"]: p["height"] for p in doc["pages"]}
    out = []
    for p in doc["pages"]:
        pg = p["page"]
        if pages and pg not in pages:
            continue
        out.append(f"[PAGE {pg}]")
        for b in sorted((b for b in doc["blocks"] if b["page"] == pg), key=lambda b: b["order"]):
            if b["kind"] == "table":
                out.append("[MARKDOWN_TABLE]")
                out.extend(table_lines(tables[b["table_id"]]["cells"]))
                out.append("")
                continue
            y0, y1 = b["bbox"][1], b["bbox"][3]
            tag = "[HEADER]" if y1 < 0.05 * heights[pg] else "[FOOTER]" if y0 > 0.92 * heights[pg] else "[BLOCK]"
            out.extend([tag, b.get("text", "")])
        out.append("")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pages")
    a = ap.parse_args(argv)
    doc = json.loads(Path(a.doc).read_text(encoding="utf-8"))
    pages = {int(x) for x in a.pages.split(",")} if a.pages else None
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(convert(doc, pages), encoding="utf-8")


if __name__ == "__main__":
    main()
