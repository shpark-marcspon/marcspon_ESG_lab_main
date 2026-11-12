"""모델이 낸 표 칸 글이 그 쪽 글자 층에 그대로 있는지 (PDX1 ③·④ — 「지어낸 글자」 재기). 표준 라이브러리만.

칸 글과 쪽 글자 층(docparse document.json 의 그 쪽 블록 글 + 표 칸 글)을 NFKC·공백 지움·쉼표 지움·가운뎃점 통일로 맞춘 뒤,
칸 글이 쪽 글의 부분 문자열이면 「있음」. 비어 있지 않은 칸만 센다.

python3 cell_check.py --tables <후보>/tables --docs samsung=<document.json>,... [--json <파일>]
"""

import argparse
import json
import re
import unicodedata
from pathlib import Path


def norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"[∙·•‧ㆍ・]", "·", re.sub(r"\s+", "", s).replace(",", ""))


def page_text(doc, page):
    parts = [b.get("text", "") for b in doc["blocks"] if b["page"] == page]
    parts += [c for t in doc["tables"] if t["page"] == page for row in t["cells"] for c in row if c]
    return norm("".join(parts))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", required=True)
    ap.add_argument("--docs", required=True)
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    docs = {k: json.loads(Path(v).read_text(encoding="utf-8")) for k, v in (x.split("=", 1) for x in a.docs.split(","))}
    res, cells, hits = {}, 0, 0
    for f in sorted(Path(a.tables).glob("*.json")):
        rep, pg = f.stem.rsplit("_p", 1)
        text = page_text(docs[rep], int(pg))
        cs = [norm(c) for t in json.loads(f.read_text(encoding="utf-8")) for row in t["grid"] for c in row if norm(c)]
        miss = [c for c in cs if c not in text]
        res[f.stem] = {"cells": len(cs), "in_text_layer": len(cs) - len(miss), "missing_examples": miss[:8]}
        cells += len(cs)
        hits += len(cs) - len(miss)
    tot = {"cells": cells, "in_text_layer": hits, "rate": round(hits / cells, 4) if cells else None}
    print(json.dumps(tot, ensure_ascii=False))
    if a.json:
        Path(a.json).write_text(json.dumps({"pages": res, "all": tot}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
