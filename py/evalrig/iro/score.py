"""IRO 채점 — 정답 있으면 (토픽, 갈래)마다 제목 짝짓기 → 재현·정밀·출처 맞음, 정답 없이(코드, 늘) 셈.

정답 0(`iro-empty`)이면 재현·정밀·출처 칸은 `no_denominator`. 정답 coverage=partial 이면 정밀은 `unmeasurable`.
채점기는 `origin.engine` 을 보지 않는다.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable
from urllib.parse import urlsplit

from ..common import gold as G
from ..common.match import pair, prf, ratio, text_sim, unmeasurable
from ..common.units import Unit, check_units
from . import BRANCHES, WEB_BRANCHES
from .from_v1 import topic_key

TITLE_SIM = 0.6  # 얼림(발주서 EVA §2 (2)) — 바꾸면 이전 점수와 견줄 수 없다


def host_of(url: str) -> str | None:
    """URL → 소문자 host(`www.` 접음). http/https 꼴이 아니면 None."""
    try:
        sp = urlsplit(url.strip())
    except ValueError:
        return None
    if sp.scheme not in ("http", "https") or not sp.hostname:
        return None
    h = sp.hostname.lower().rstrip(".")
    return h[4:] if h.startswith("www.") else h


def code_metrics(units: Iterable[Unit]) -> dict[str, dict[str, Any]]:
    """갈래별 정답 없는 셈: 단위 수·실패 수·항목 수·URL 수·searched 비율(모름 뺌)·날짜 갈래 분포."""
    us = check_units(units)
    out: dict[str, dict[str, Any]] = {}
    for b in BRANCHES:
        bu = [u for u in us if u.payload.get("branch") == b]
        if not bu:
            continue
        items = [it for u in bu for it in u.payload["items"]]
        known = [u for u in bu if u.payload.get("searched") is not None]
        out[b] = {
            "units": len(bu),
            "failed": sum(u.status == "failed" for u in bu),
            "missing": sum(u.status == "missing" for u in bu),
            "items": len(items),
            "urls": sum(len(it["urls"]) for it in items),
            "searched_rate": ratio(sum(bool(u.payload["searched"]) for u in known), len(known)),
            "date_status": dict(sorted(Counter(it["date_status"] for it in items).items())),
        }
    return out


def _gold_by_cell(gs: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    cells: dict[str, list[dict[str, Any]]] = {}
    for it in gs["items"]:
        cells.setdefault(f"{topic_key(it['topic'])}|{it['branch']}", []).append(it)
    return cells


def score(units: Iterable[Unit], gs: dict[str, Any]) -> dict[str, Any]:
    """단위 + 정답 셋 → `{code, gold{recall, precision, source_ok, per_branch}, setId}`."""
    G.check(gs)
    us = check_units(units)
    by = {u.key: u for u in us}
    cells = _gold_by_cell(gs)
    per_branch: dict[str, dict[str, int]] = {}
    for cell, gitems in cells.items():
        b = cell.rsplit("|", 1)[1]
        acc = per_branch.setdefault(b, {"tp": 0, "n_gold": 0, "n_pred": 0, "src_ok": 0})
        u = by.get(cell)
        preds = u.payload["items"] if u is not None else []
        r = pair(gitems, preds, sim=lambda g, p: text_sim(g["title"], p["title"]), threshold=TITLE_SIM, order="gold")
        acc["tp"] += len(r["pairs"])
        acc["n_gold"] += len(gitems)
        for d in r["pairs"]:
            gh = {host_of(x) for x in gitems[d["gold"]].get("urls", [])} - {None}
            ph = {host_of(x) for x in preds[d["pred"]]["urls"]} - {None}
            acc["src_ok"] += bool(gh & ph)
    # 정밀의 분모는 정답이 있는 칸의 후보 항목만(정답 없는 칸의 항목은 셀 수 없음)
    for cell in cells:
        u = by.get(cell)
        if u is not None:
            per_branch[cell.rsplit("|", 1)[1]]["n_pred"] += len(u.payload["items"])
    tot = {k: sum(v[k] for v in per_branch.values()) for k in ("tp", "n_gold", "n_pred", "src_ok")}
    m = prf(tot["tp"], tot["n_gold"], tot["n_pred"])
    precision = m["precision"] if G.precision_allowed(gs) else unmeasurable("partial", tot["tp"], tot["n_pred"])
    return {
        "setId": gs["setId"],
        "code": code_metrics(us),
        "gold": {
            "recall": m["recall"],
            "precision": precision,
            "source_ok": ratio(tot["src_ok"], tot["tp"]),
            "per_branch": per_branch,
        },
    }


__all__ = ["TITLE_SIM", "WEB_BRANCHES", "code_metrics", "host_of", "score"]
