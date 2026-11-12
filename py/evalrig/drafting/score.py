"""보고서 채점 — 사실 재현(정답 있을 때) · 정답 없는 코드 지표(늘).

- 맞음 = 사실의 `match` 글자 중 하나가 잎 글에 **숫자 경계**로 있음: 둘 다 쉼표·공백을 빼고
  `(?<![\\d.])<match>(?![\\d.])` 로 찾는다(SG1 `sg1i._strict(s, taskeval._num(t))` 과 같은 셈 — 「120」 은 「1,120」 에 없음).
- 잎 점수 = 그 잎 사실 중 맞은 비율 · 거시 평균 + `boot_ci`(seed 552).
- 잎 열쇠가 정답 질의 id 와 다르면 `leaf_map{질의 id: 잎 key}`. 짝이 없는 질의는 `missed`(점수 0 으로 넣지 않고 따로 셈).
- 코드 지표: 근거 없는 숫자 bullet 비율(`number_not_in_source` 경고 수 / bullet 수) · `unsupported` 비율.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from ..common import gold as G
from ..common.match import boot_ci, macro, ratio
from ..common.units import Unit, check_units
from .from_v1 import num


def fact_hit(match: Iterable[str], text: str) -> bool:
    t = num(text)
    return any(re.search(r"(?<![\d.])" + re.escape(num(s)) + r"(?![\d.])", t) for s in match)


def code_metrics(units: Iterable[Unit]) -> dict[str, Any]:
    us = check_units(units)
    n_b = sum(len(u.payload["bullets"]) for u in us)
    nis = sum(u.payload.get("warnings", {}).get("number_not_in_source", 0) for u in us)
    uns = sum(b["unsupported"] for u in us for b in u.payload["bullets"])
    return {
        "leaves": len(us),
        "failed": sum(u.status == "failed" for u in us),
        "bullets": n_b,
        "number_not_in_source_rate": ratio(nis, n_b),
        "unsupported_rate": ratio(uns, n_b),
    }


def score(units: Iterable[Unit], gs: dict[str, Any], leaf_map: dict[str, str] | None = None) -> dict[str, Any]:
    """→ `{setId, recall(거시), ci, per_leaf{질의: ratio}, missed[질의], facts{num, den}}`. 정밀은 세지 않는다(사실 재현만)."""
    G.check(gs)
    by = {u.key: u for u in check_units(units)}
    lm = leaf_map or {}
    per: dict[str, list[dict[str, Any]]] = {}
    for it in gs["items"]:
        per.setdefault(it["leaf"], []).append(it)
    per_leaf, missed, hits, den = {}, [], 0, 0
    for leaf, fs in per.items():
        u = by.get(lm.get(leaf, leaf))
        if u is None:
            missed.append(leaf)
            continue
        h = sum(fact_hit(f["match"], u.payload["text"]) for f in fs)
        per_leaf[leaf] = ratio(h, len(fs))
        hits += h
        den += len(fs)
    vals = [r["value"] for r in per_leaf.values() if "value" in r]
    return {
        "setId": gs["setId"],
        "recall": macro(per_leaf),
        "ci": boot_ci(vals),
        "per_leaf": per_leaf,
        "missed": missed,
        "facts": ratio(hits, den),
        "leaves_with_gold": ratio(len(per_leaf), len(per)),
    }
