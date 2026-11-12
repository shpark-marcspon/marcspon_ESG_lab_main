"""`python -m evalrig.drafting <gold|convert|score|golden>` — py/ 에서 `uv run --frozen --group scope3colab`.

- gold    : SG1 책 넷 → `prework/gold-eva/drafting-sg1-*.json` 짓고 얼림
- convert : 1차 산출 넷(RPL1·RPQ1 × live-1·2) → `out/eva/drafting/units/<자리>.json`
- score   : 정답 없는 코드 지표 → `out/eva/drafting/summary.{json,md}`(SG1 책에 1차 초안을 돌린 산출이 없다 — 정답 점수는 M-R2)
- golden  : 골든 ③ — 책 넷 사실 × (자기 핵심 단락 글, 다음 질의 핵심 단락 글)에서 `fact_hit` 과
            `any(sg1i._strict(s, taskeval._num(t)))` 가 하나도 다르지 않음 → `out/eva/golden/drafting.json`
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from ..common import gold as G
from ..common import report as R
from ..iro.cli import APPW, GOLD, OUT, ROOT, dump_units, freeze_line, load_units
from . import gold as DG
from .from_v1 import convert_bullets
from .score import code_metrics, fact_hit

GOLD_AT = "2026-11-09"
LAB_OUT = ROOT / "esg-lab" / "out"
SITES = tuple(f"{r}-{n}" for r in ("RPL1", "RPQ1") for n in ("live-1", "live-2"))


def _gold_path(i: int):
    return GOLD / f"drafting-sg1-{DG.SET_IDS[i]}.json"


def cmd_gold(_: Any) -> None:
    GOLD.mkdir(parents=True, exist_ok=True)
    for i in range(4):
        gs = DG.build(i, DG.book_paths(i, LAB_OUT), at=GOLD_AT, rel_to=ROOT)
        p = _gold_path(i)
        p.write_text(G.dumps(gs), encoding="utf-8")
        freeze_line(p)
        print("gold", p.name, len(gs["items"]), "facts", gs["meta"]["n_leaves"], "leaves", G.load(p)["frozen"]["sha16"])


def cmd_convert(_: Any) -> None:
    for s in SITES:
        r, n = s.split("-", 1)
        d = json.loads((APPW / f"{r}.out" / n / "result.json").read_text(encoding="utf-8"))
        us = convert_bullets(d, run=s)
        dump_units(OUT / "drafting" / "units" / f"{s}.json", us)
        print("convert", s, len(us), "failed", sum(u.status == "failed" for u in us))


def cmd_score(_: Any) -> None:
    gsets = [G.load(_gold_path(i)) for i in range(4)]
    rows = []
    for s in SITES:
        m = code_metrics(load_units(OUT / "drafting" / "units" / f"{s}.json"))
        rows.append({"gold": R.NO_GOLD, "자리": s, "잎": m["leaves"], "실패 잎": m["failed"], "bullet": m["bullets"],
                     "number_not_in_source_rate": m["number_not_in_source_rate"], "unsupported_rate": m["unsupported_rate"]})
    for gs in gsets:
        rows.append({"gold": gs["setId"], "자리": "(정답 셋)", "잎": gs["meta"]["n_leaves"], "실패 잎": None,
                     "bullet": None, "number_not_in_source_rate": None, "unsupported_rate": None, "사실": len(gs["items"])})
    R.write(OUT / "drafting", "summary", rows, gsets, title="EVA 보고서 — 1차 산출 코드 지표 · 정답 셋",
            notes=["SG1 책에 1차 초안을 돌린 산출은 없다 — 1차 대 정답 사실 재현은 M-R2(실호출)에서.",
                   "실패 잎 = outcome.status 가 done 이 아닌 잎(no_material 포함)."])
    print("score", len(rows))


def cmd_golden(_: Any) -> None:
    from semgraph import sg1i, taskeval

    res = {"books": {}, "diff": 0, "n": 0}
    for i in range(4):
        gs = G.load(_gold_path(i))
        P = DG.passages(DG.book_paths(i, LAB_OUT))
        leaves = sorted(gs["meta"]["leaves"], key=DG._qorder)
        txt = {k: "\n".join(P[p] for p in gs["meta"]["leaves"][k]["core"] if p in P) for k in leaves}
        nxt = {k: leaves[(j + 1) % len(leaves)] for j, k in enumerate(leaves)}
        n = d = hit_own = hit_other = 0
        for f in gs["items"]:
            for which, t in (("own", txt[f["leaf"]]), ("other", txt[nxt[f["leaf"]]])):
                a = fact_hit(f["match"], t)
                b = any(sg1i._strict(s, taskeval._num(t)) for s in f["match"])
                n += 1
                d += a != b
                if which == "own":
                    hit_own += a
                else:
                    hit_other += a
        res["books"][gs["setId"]] = {"judged": n, "diff": d, "own_hit": hit_own, "other_hit": hit_other, "facts": len(gs["items"])}
        res["n"] += n
        res["diff"] += d
    res["same"] = res["diff"] == 0
    p = OUT / "golden" / "drafting.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("golden ③", "같음" if res["same"] else "다름", res["n"], "판정, 다름", res["diff"])
    if not res["same"]:
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evalrig.drafting")
    ap.add_argument("cmd", choices=["gold", "convert", "score", "golden"])
    a = ap.parse_args(argv)
    {"gold": cmd_gold, "convert": cmd_convert, "score": cmd_score, "golden": cmd_golden}[a.cmd](a)
    return 0
