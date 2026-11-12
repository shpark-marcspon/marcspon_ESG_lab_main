"""PDX2 덧지표: 채점 JSON(table_score --json)과 표 격자 폴더로 세는 것. 표준 라이브러리만.

  자리만 틀린 칸  = Σ(관대로 맞은 칸 − 위치로 맞은 칸) (PDX1 E1 정의)
  헛되돌림        = 검사 산출의 정답 표 점수(엄격·위치 중 하나)가 검사 전(--raw 채점)보다 떨어진 정답 표 수
  숫자 바뀐 칸    = 산출 표의 칸을 NFKC·빈칸 지움으로 맞춘 뒤 숫자 칸(`-1,234.5%` 꼴)인 것 가운데 그 값이 그 쪽 docparse 글의 숫자에 없는 칸 수(guard `number` 와 같은 셈).
                    --gold 를 주면 정답 표 영역과 겹치는 산출 표만 센다(정답 쪽 밖의 표는 안 셈)
  칸 괘선 표만    = 정답 파일 `form` 이 --grid-form(기본 "ruled_cells")인 표의 합

  python3 -I run_metrics.py --score <s.json> [--raw <raw.json>] [--tables <dir> --docs r=<document.json>,... --gold <정답 폴더>]
"""

import argparse
import json
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from guard import NUM, Page, numval, overlap  # noqa: E402

M = ("recall", "recall_strict", "recall_position", "numeric_recall")


def agg(rows):
    g = sum(r["gold_cells"] for r in rows)
    n = sum(r["gold_numeric_cells"] for r in rows)
    pct = lambda a, b: round(100 * a / b, 1) if b else None  # noqa: E731
    return {
        "tables": len(rows),
        "cells": g,
        "lenient": pct(sum(r["correct"] for r in rows), g),
        "strict": pct(sum(r["correct_strict"] for r in rows), g),
        "position": pct(sum(r["correct_position"] for r in rows), g),
        "numeric": pct(sum(r["correct_numeric"] for r in rows), n),
        "place_only_wrong": sum(r["correct"] - r["correct_position"] for r in rows),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--score", required=True)
    ap.add_argument("--raw")
    ap.add_argument("--tables")
    ap.add_argument("--docs")
    ap.add_argument("--gold")
    ap.add_argument("--grid-form", default="ruled_cells")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    rows = json.loads(Path(a.score).read_text(encoding="utf-8"))["tables"]
    out = {"all": agg(rows), "grid": agg([r for r in rows if r["form"] == a.grid_form])}
    out["grid_per_table"] = {r["id"]: round(100 * r["recall_strict"], 1) for r in rows if r["form"] == a.grid_form}
    if a.raw:
        raw = {r["id"]: r for r in json.loads(Path(a.raw).read_text(encoding="utf-8"))["tables"]}
        out["false_revert"] = [
            r["id"]
            for r in rows
            if r["recall_strict"] < raw[r["id"]]["recall_strict"] - 1e-9
            or r["recall_position"] < raw[r["id"]]["recall_position"] - 1e-9
        ]
    if a.tables:
        docs = {k: json.loads(Path(v).read_text(encoding="utf-8")) for k, v in (x.split("=", 1) for x in a.docs.split(","))}
        golds = {}
        if a.gold:
            for f in sorted(Path(a.gold).glob("*.json")):
                g = json.loads(f.read_text(encoding="utf-8"))
                if "grid" in g:
                    golds.setdefault((g["report"], g["page"]), []).append(g["bbox"])
        bad = []
        for f in sorted(Path(a.tables).glob("*.json")):
            rep, pg = f.stem.rsplit("_p", 1)
            if rep not in docs:
                continue
            pg = int(pg)
            if a.gold and (rep, pg) not in golds:
                continue
            pp = Page(docs[rep], pg)
            for t in json.loads(f.read_text(encoding="utf-8")):
                if a.gold and not any(overlap(t["bbox"], gb) >= 0.5 for gb in golds[(rep, pg)]):
                    continue
                for row in t["grid"]:
                    for c in row:
                        c = unicodedata.normalize("NFKC", c or "").replace(" ", "").strip()  # `73², ³` → 732,3
                        if NUM.fullmatch(c) and numval(c) not in pp.nums:
                            bad.append(f"{f.stem}:{c}")
        out["number_changed"] = bad
    print(json.dumps(out, ensure_ascii=False))
    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
