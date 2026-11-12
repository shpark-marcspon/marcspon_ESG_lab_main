"""docparse `parse` 산출(document.json)에서 쪽마다 읽는 차례대로 이은 글 흐름을 쓴다 (PDX1). 표준 라이브러리만.

블록을 `order` 차례로 잇는다. 표 블록 자리에는 그 표의 칸 글을 행 우선으로 넣는다.

python3 docparse_streams.py --doc <document.json> --name <report> --pages 26,40 --out <흐름 폴더>
"""

import argparse
import json
from pathlib import Path


def table_text(t):
    return " ".join(c for row in t.get("cells", []) for c in row if c)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--pages", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    d = json.loads(Path(a.doc).read_text(encoding="utf-8"))
    tables = {t["id"]: t for t in d["tables"]}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for pg in (int(x) for x in a.pages.split(",")):
        parts = []
        for b in sorted((b for b in d["blocks"] if b["page"] == pg), key=lambda b: b["order"]):
            parts.append(table_text(tables[b["table_id"]]) if b["kind"] == "table" else b.get("text", ""))
        (out / f"{a.name}_p{pg}.txt").write_text("\n".join(parts), encoding="utf-8")


if __name__ == "__main__":
    main()
