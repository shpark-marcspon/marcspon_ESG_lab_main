"""docparse `document.json` → 채점 꼴 표 격자 `<out>/<report>_p<page>.json` (PDX2). 표준 라이브러리만.

`table_score.py` 가 읽는 꼴 [{"bbox", "grid"}] 로 쪽마다 쓴다. 정답 쪽만 쓰려면 --pages.
--layout 은 document.json 이 그 docparse 판(`parser.layout`, 없으면 1)으로 뽑힌 것인지 확인만 한다.

  # docparse 판 1·2 로 뽑기 (esg-app main 읽기 전용)
  cd ~/Desktop/marcspon/esg-app/services/parser && uv run --frozen python -m docparse parse <pdf> --out <폴더> --layout 1|2
  python3 -I docparse_tables.py --layout 2 --out <후보>/tables hdec=<document.json> ... [--pages hdec:118,hdec:124]
"""

import argparse
import json
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("docs", nargs="+", help="report=<document.json>")
    ap.add_argument("--out", required=True)
    ap.add_argument("--layout", type=int, choices=(1, 2), default=1)
    ap.add_argument("--pages", default="", help="report:page,... (기본: 표가 있는 모든 쪽)")
    a = ap.parse_args(argv)
    want = {(r, int(p)) for r, p in (x.split(":") for x in a.pages.split(",") if x)}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for k, v in (x.split("=", 1) for x in a.docs):
        doc = json.loads(Path(v).read_text(encoding="utf-8"))
        if (doc.get("parser") or {}).get("layout", 1) != a.layout:
            raise SystemExit(f"--layout {a.layout} 인데 {k} 의 판이 다르다")
        pages = {t["page"] for t in doc["tables"]} | {p for r, p in want if r == k}
        for pg in sorted(pages):
            if want and (k, pg) not in want:
                continue
            ts = [{"bbox": t["bbox"], "grid": t["cells"]} for t in doc["tables"] if t["page"] == pg]
            (out / f"{k}_p{pg}.json").write_text(json.dumps(ts, ensure_ascii=False), encoding="utf-8")
            n += 1
    print(f"{n} 쪽 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
