"""엑셀↔PDF 랩 대사 채점(발주서 MB2 §2-4). 셈은 코드, 모델 0. 표준 라이브러리 + 공통 `match.ratio`.

입력: 엑셀↔PDF 정답(`evalrig.gold@1`, items[{indicator, year, verdict, kind, accept, cell}] — `cell` 은 보고서(문서 b) 값 후보 ID),
`cell_map`(`xsrc.cell_map` — 원천(문서 a) 값 후보 ID → (ID, 연도)), 대사 실행의 verdicts(`[{a, b, outcome, reason}]` — a·b 는
그 문서의 값 후보 ID, 앞머리 `a:`/`b:` 없음. D2·H2 는 `outcome.json` 의 `outcome.verdicts`, R 은 `after.verdicts`).

정답 칸마다 갈래 하나:
- match·mismatch 정답 → `paired_right{outcome}`(a = 그 원천 칸, b = 정답 칸) / `paired_elsewhere{b}`(a 는 맞고 b 가 다른 칸) / `unpaired`.
- source_only → `unpaired`(맞음) / `paired_false{b}`. report_only → `unpaired`(맞음) / `paired_false{a}`.
verdicts 의 a 가 하나도 `cell_map` 에 없으면 ID 꼴이 어긋난 것이라 멈춘다(`ReconError` — 조용히 0 을 내지 않음, critic R2).
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from evalrig.common import match as M

PLANTED = ("digit", "unit_scale", "year_swap")
STAGES = ("d2", "r", "h2")


class ReconError(ValueError):
    pass


def verdicts_of(obj: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    if stage not in STAGES:
        raise ReconError(f"모르는 단계 {stage}")
    v = obj["after"]["verdicts"] if stage == "r" else obj["outcome"]["verdicts"]
    return [{"a": x["a"], "b": x["b"], "outcome": x["outcome"], "reason": x.get("reason", "")} for x in v]


def branches(gs: dict[str, Any], cells: dict[str, Any], verdicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    src_of = {(v[0], v[1]): k for k, v in cells.items()}
    if verdicts and not any(v["a"] in cells for v in verdicts):
        raise ReconError("verdicts 의 a 가 하나도 cell_map 에 없다 — ID 꼴 어긋남")
    by_a: dict[str, list[dict[str, Any]]] = {}
    by_b: dict[str, list[dict[str, Any]]] = {}
    for v in verdicts:
        by_a.setdefault(v["a"], []).append(v)
        by_b.setdefault(v["b"], []).append(v)
    out = []
    for it in gs["items"]:
        key = (it["indicator"], it["year"])
        a = src_of.get(key)
        row = {"indicator": it["indicator"], "year": it["year"], "verdict": it["verdict"], "kind": it.get("kind"),
               "accept": it.get("accept", []), "a": a, "cell": it.get("cell")}
        if it["verdict"] in ("match", "mismatch"):
            if a is None:
                raise ReconError(f"{key}: 원천 칸이 cell_map 에 없다")
            mine = by_a.get(a, [])
            right = [v for v in mine if v["b"] == it["cell"]]
            if right:
                row.update(branch="paired_right", outcome=right[0]["outcome"], reason=right[0]["reason"])
            elif mine:
                row.update(branch="paired_elsewhere", b=[v["b"] for v in mine])
            else:
                row.update(branch="unpaired")
        elif it["verdict"] == "source_only":
            mine = by_a.get(a, []) if a else []
            row.update(branch="paired_false", b=[v["b"] for v in mine]) if mine else row.update(branch="unpaired")
        elif it["verdict"] == "report_only":
            mine = by_b.get(it["cell"], [])
            row.update(branch="paired_false", a=[v["a"] for v in mine]) if mine else row.update(branch="unpaired")
        else:  # pragma: no cover
            raise ReconError(f"모르는 정답 {it['verdict']}")
        out.append(row)
    return out


def score(gs: dict[str, Any], cells: dict[str, Any], verdicts: list[dict[str, Any]]) -> dict[str, Any]:
    rows = branches(gs, cells, verdicts)
    paired = [r for r in rows if r["verdict"] in ("match", "mismatch")]
    planted = [r for r in rows if r["kind"] in PLANTED]
    plain = [r for r in rows if r["verdict"] == "match" and r["kind"] != "rounding"]
    right = lambda r: r["branch"] == "paired_right"  # noqa: E731
    fa = [r for r in plain if right(r) and r["outcome"] != "match"]
    so = [r for r in rows if r["verdict"] == "source_only"]
    ro = [r for r in rows if r["verdict"] == "report_only"]
    return {
        "pairRecall": M.ratio(sum(map(right, paired)), len(paired)),
        "plantedCaught": M.ratio(sum(1 for r in planted if right(r) and r["outcome"] != "match"), len(planted)),
        "falseMatch": M.ratio(sum(1 for r in planted if right(r) and r["outcome"] == "match"), len(planted)),
        "falseAlarm": {
            "mismatch": M.ratio(sum(1 for r in fa if r["outcome"] == "mismatch"), len(plain)),
            "needsExplanation": M.ratio(sum(1 for r in fa if r["outcome"] == "needs_explanation"), len(plain)),
            "byReason": dict(sorted(Counter(r["reason"] for r in fa).items())),
        },
        "rounding": [r.get("outcome", r["branch"]) for r in rows if r["kind"] == "rounding"],
        "sourceOnlyCorrect": M.ratio(sum(1 for r in so if r["branch"] == "unpaired"), len(so)),
        "reportOnlyCorrect": M.ratio(sum(1 for r in ro if r["branch"] == "unpaired"), len(ro)),
        "pairedElsewhere": sum(1 for r in rows if r["branch"] == "paired_elsewhere"),
        "branches": dict(sorted(Counter(r["branch"] for r in rows).items())),
        "verdicts": len(verdicts),
        "verdictsOnSourceCells": sum(1 for v in verdicts if v["a"] in cells),
    }
