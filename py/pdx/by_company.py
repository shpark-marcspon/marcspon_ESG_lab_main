"""PDX2: 후보 × 지표 표를 회사(report)별·칸 괘선 표만으로 나눠 찍는다. 표준 라이브러리만.

table_score --json 산출 여럿을 받아 마크다운 표 둘을 찍는다.
  1) 후보 × (전체 관대/엄격/위치/숫자, 칸 괘선만 엄격/위치/숫자, 자리만 틀린 칸)
  2) 칸 괘선 표마다 후보별 엄격(표본이 작아 표별 값), 회사별 칸 괘선 엄격 합

  python3 -I by_company.py 이름=<score.json> 이름=<score.json> ... [--grid-form ruled_cells]
"""

import argparse
import json
from pathlib import Path



def pct(a, b):
    return "—" if not b else f"{100 * a / b:.1f}"


def agg(rows, key):
    return sum(r[key] for r in rows)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--grid-form", default="ruled_cells")
    a = ap.parse_args(argv)
    runs = [(k, json.loads(Path(v).read_text(encoding="utf-8"))["tables"]) for k, v in (x.split("=", 1) for x in a.runs)]
    print("| 후보 | 관대 | 엄격 | 위치 | 숫자 | 칸 괘선 엄격 | 칸 괘선 위치 | 칸 괘선 숫자 | 자리만 틀린 칸 |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for k, rows in runs:
        g = [r for r in rows if r["form"] == a.grid_form]
        c, gc = agg(rows, "gold_cells"), agg(g, "gold_cells")
        print(
            f"| {k} | {pct(agg(rows, 'correct'), c)} | {pct(agg(rows, 'correct_strict'), c)} | {pct(agg(rows, 'correct_position'), c)} "
            f"| {pct(agg(rows, 'correct_numeric'), agg(rows, 'gold_numeric_cells'))} | {pct(agg(g, 'correct_strict'), gc)} "
            f"| {pct(agg(g, 'correct_position'), gc)} | {pct(agg(g, 'correct_numeric'), agg(g, 'gold_numeric_cells'))} "
            f"| {agg(rows, 'correct') - agg(rows, 'correct_position')} |"
        )
    print()
    ids = [r["id"] for r in runs[0][1] if r["form"] == a.grid_form]
    print("| 칸 괘선 표 (회사) | 칸 | " + " | ".join(k for k, _ in runs) + " |")
    print("|---|---:|" + "---:|" * len(runs))
    by = {k: {r["id"]: r for r in rows} for k, rows in runs}
    for i in ids:
        r0 = by[runs[0][0]][i]
        print(f"| {i} ({r0['report']}) | {r0['gold_cells']} | " + " | ".join(f"{100 * by[k][i]['recall_strict']:.1f}" for k, _ in runs) + " |")
    for rep in sorted({by[runs[0][0]][i]["report"] for i in ids}):
        sel = [i for i in ids if by[runs[0][0]][i]["report"] == rep]
        cells = sum(by[runs[0][0]][i]["gold_cells"] for i in sel)
        print(
            f"| **{rep} 합** ({len(sel)}표) | {cells} | "
            + " | ".join(pct(sum(by[k][i]["correct_strict"] for i in sel), cells) for k, _ in runs)
            + " |"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
