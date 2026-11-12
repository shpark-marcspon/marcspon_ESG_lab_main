"""③ 혼합에 보낼 쪽 고르기 (PDX1 경계 E3 규칙, 경계 F 에서 얼림). docparse `document.json` 만 본다. 표준 라이브러리만.

고르는 쪽 = 숫자 표가 있는 쪽 ∪ 위첨자·각주 조각이 있는 쪽.
  숫자 표: docparse 표 중 3행 이상이고 칸(빈칸 빼고)의 20% 이상이 숫자(`-1,234.5%` 꼴)인 것
  조각: 표가 아닌 블록 중 글이 3자 이하이고, 글자 크기가 그 쪽 중앙값의 0.8배 이하이거나 `1)` 꼴로 시작하는 것
        (PDX2 G0) 글이 그 쪽 번호와 같은 숫자인 블록(쪽 번호)은 조각이 아니다. `--rules e` 면 경계 E 규칙 그대로
다단은 넣지 않는다(docparse 의 읽는 순서는 다단 쪽에서 맞았다 — 경계 A·D).

  python3 -I select_pages.py samsung=<document.json> ... [--json <파일>] [--layout 1|2] [--rules e|g0]
"""

import argparse
import json
import re
import statistics as st
from pathlib import Path

NUM = re.compile(r"[-+−△▲]?\d[\d,]*(?:\.\d+)?%?")


def select(doc, rules="g0"):
    nt, frag = set(), set()
    for t in doc["tables"]:
        cs = [c for r in t["cells"] for c in r if c and c.strip()]
        if (
            len(t["cells"]) >= 3
            and cs
            and sum(bool(NUM.fullmatch(c.strip())) for c in cs) >= 0.2 * len(cs)
        ):
            nt.add(t["page"])
    for pg in doc["pages"]:
        bs = [
            b for b in doc["blocks"] if b["page"] == pg["page"] and b["kind"] != "table"
        ]
        if rules == "g0":
            bs = [b for b in bs if (b.get("text") or "").strip() != str(pg["page"])]
        fs = [b["font_size"] for b in bs if b.get("font_size")]
        if fs and any(
            len((b.get("text") or "").strip()) <= 3
            and (
                (b.get("font_size") or 99) <= 0.8 * st.median(fs)
                or re.match(r"\d+\)", b["text"].strip())
            )
            for b in bs
        ):
            frag.add(pg["page"])
    return nt, frag


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("docs", nargs="+", help="report=<document.json>")
    ap.add_argument("--json")
    ap.add_argument("--layout", type=int, choices=(1, 2), default=1)
    ap.add_argument("--rules", choices=("e", "g0"), default="g0")
    a = ap.parse_args(argv)
    out, n_all, n_sel = {}, 0, 0
    for k, v in (x.split("=", 1) for x in a.docs):
        doc = json.loads(Path(v).read_text(encoding="utf-8"))
        if (doc.get("parser") or {}).get("layout", 1) != a.layout:
            raise SystemExit(f"--layout {a.layout} 인데 {k} 의 판이 다르다")
        nt, frag = select(doc, a.rules)
        sel = sorted(nt | frag)
        out[k] = {
            "pages": len(doc["pages"]),
            "numeric_table": len(nt),
            "fragment": len(frag),
            "selected": sel,
        }
        n_all, n_sel = n_all + len(doc["pages"]), n_sel + len(sel)
        print(
            f"{k} {len(sel)} / {len(doc['pages'])} (숫자 표 {len(nt)} · 조각 {len(frag)})"
        )
    print(f"합 {n_sel} / {n_all} ({n_sel / max(1, n_all):.1%})")
    if a.json:
        Path(a.json).write_text(
            json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
