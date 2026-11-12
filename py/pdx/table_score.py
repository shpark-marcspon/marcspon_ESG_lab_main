"""다른 추출기가 낸 표 격자를 docparse `score` 와 같은 정의로 채점한다 (PDX1).

정답 표 고르기(`pick`: 정답 영역과 절반 이상 겹치는 표를 위→아래로 이어 붙임), 셀 비교(`compare.score_tables`),
합계(`aggregate`)는 esg-app 의 docparse 것을 그대로 부른다(읽기만). 그래서 esg-app 파서의 환경에서 돈다:

  cd ~/Desktop/marcspon/esg-app/services/parser && uv run --frozen python <이 파일> \
      --gold ~/Desktop/marcspon/prework/gold --tables <후보>/tables --label <이름> [--json <파일>]

<후보>/tables/<report>_p<page>.json = [{"bbox":[x0,y0,x1,y1] (위-왼 원점, pt), "grid":[[...]]}, ...]
"""

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from docparse.compare import score_tables
from docparse.score import FORMS, METRICS, aggregate, load_gold, pick


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("--tables", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    results = []
    for g in load_gold(a.gold):
        f = Path(a.tables) / f"{g['report']}_p{g['page']}.json"
        tables = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else []
        objs = [SimpleNamespace(bbox=t["bbox"], grid=t["grid"]) for t in tables]
        sel = pick(objs, g["bbox"])
        s = score_tables(g["grid"], [r for t in sel for r in t.grid])
        results.append(
            {
                "id": g["id"],
                "report": g["report"],
                "form": FORMS.get(g["report"], g.get("form")),  # 처음 보는 보고서는 정답 파일의 form
                "page": g["page"],
                "label": a.label,
                "has_output": f.is_file(),
                "n_tables": len(sel),
                "tables_on_page": len(tables),
                **{k: v for k, v in s.items() if k != "wrong_rows"},
                "wrong_rows": s["wrong_rows"],
            }
        )
    pct = lambda v: "—" if v is None else f"{v * 100:.1f}"  # noqa: E731
    print(f"## {a.label}\n\n| 표 | 정답 셀 | 고른 표 | 관대 | 엄격 | 위치 | 숫자 |\n|---|---:|---:|---:|---:|---:|---:|")
    for r in results:
        print(f"| {r['id']} | {r['gold_cells']} | {r['n_tables']} | " + " | ".join(pct(r[m]) for m in METRICS) + " |")
    for label, key in (("양식", "form"), ("전체", None)):
        for name, ag in sorted(aggregate(results, key).items()):
            print(f"| **{label} {name}** | {ag['gold_cells']} | | " + " | ".join(pct(ag[m]) for m in METRICS) + " |")
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        dump = {"tables": results, "by_form": aggregate(results, "form"), "all": aggregate(results, None)["ALL"]}
        Path(a.json).write_text(json.dumps(dump, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
