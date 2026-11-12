"""같은 입력의 두 결과 대조와 흔들림(발주서 EVB §2-10, 실비 0).

- `same(a, b)`: 두 변환 산출의 `source.sha256`·`pages`·`config` 가 다르면 `InputMismatch`(점수 없음). 같으면 그린 짝 규칙
  (`score.pair_units`)으로 짝·한쪽에만·등급 다름. 정답을 주면 둘 다 채점해 나란히.
- `jitter(convs)`: 같은 입력 결과 N(≥2)개의 모든 짝에서 등급 다름 비(다름/짝)·한쪽에만 비(한쪽에만/합집합)의 평균과
  최댓값 — RWL2 `compare.py` 의 diffRatio·onlyRatio 셈(`band-reports/archive/RWL2.md` 38~49줄).
"""
from __future__ import annotations

import itertools
from collections import Counter
from typing import Any

from evalrig.common import match as M
from evalrig.greenwash import score as S


class InputMismatch(ValueError):
    """두 결과의 입력(문서·쪽·설정)이 다르다."""


def _same_input(a: dict[str, Any], b: dict[str, Any]) -> None:
    for k in ("source", "pages", "config"):
        va = a[k]["sha256"] if k == "source" else a[k]
        vb = b[k]["sha256"] if k == "source" else b[k]
        if va != vb:
            raise InputMismatch(f"{k} 다름")


def same(a: dict[str, Any], b: dict[str, Any], gold: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    _same_input(a, b)
    ra = [u.payload for u in a["units"]]
    rb = [u.payload for u in b["units"]]
    pr = S.pair_units(ra, rb)
    diff = [(ra[d["gold"]]["level"], rb[d["pred"]]["level"]) for d in pr["pairs"] if ra[d["gold"]]["level"] != rb[d["pred"]]["level"]]
    union = len(pr["pairs"]) + len(pr["missed"]) + len(pr["extra"])
    out = {
        "source": {"sha12": a["source"]["sha256"][:12], "pages": a["pages"]},
        "models": [a.get("models"), b.get("models")],
        "units": [len(ra), len(rb)], "folded": [a.get("folded", 0), b.get("folded", 0)],
        "pairs": len(pr["pairs"]), "how": M.count_how(pr),
        "onlyA": len(pr["missed"]), "onlyB": len(pr["extra"]),
        "onlyAByLevel": dict(sorted(Counter(ra[i]["level"] for i in pr["missed"]).items())),
        "onlyBByLevel": dict(sorted(Counter(rb[j]["level"] for j in pr["extra"]).items())),
        "levelDiff": M.ratio(len(diff), len(pr["pairs"])),
        "moves": dict(sorted(Counter(f"{x}->{y}" for x, y in diff).items())),
        "onlyRatio": M.ratio(len(pr["missed"]) + len(pr["extra"]), union),
    }
    if gold:
        out["scores"] = [[S.score(g, a), S.score(g, b)] for g in gold]
    return out


def jitter(convs: list[dict[str, Any]]) -> dict[str, Any]:
    if len(convs) < 2:
        raise ValueError("흔들림은 결과 둘 이상")
    rows = []
    for i, j in itertools.combinations(range(len(convs)), 2):
        s = same(convs[i], convs[j])
        rows.append({"pair": [i, j], "pairs": s["pairs"], "levelDiff": s["levelDiff"], "onlyRatio": s["onlyRatio"]})
    return {"n": len(convs), "pairsCompared": len(rows), "rows": rows,
            "levelDiff": _agg([r["levelDiff"] for r in rows]), "onlyRatio": _agg([r["onlyRatio"] for r in rows])}


def _agg(rs: list[dict[str, Any]]) -> dict[str, Any]:
    vals = [r["value"] for r in rs if "value" in r]
    if not vals:
        return {"status": "no_denominator", "n": 0}
    return {"mean": round(sum(vals) / len(vals), 4), "max": round(max(vals), 4), "n": len(vals)}
