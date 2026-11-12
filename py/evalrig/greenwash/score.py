"""그린 채점기 — EVA 공통 `match.pair`·`ratio`·`unmeasurable` 위의 얇은 층(발주서 EVB §2-10).

짝 규칙(같은 쪽 안에서만): sha12 같음 → `exact` | 정규화 글 한쪽이 다른 쪽을 품음 → `contain` |
`difflib` 비 ≥ 0.90 → `fuzzy`. 결과 항목 하나는 정답 하나에만(공통 `pair(order="gold")` — 정답 차례 그리디).

셈(모두 분모와 함께):
- exhaustive 정답(지은 표본): 위험 재현 = 위험(High·Mid) 정답 중 위험 결과와 짝지은 수 / 위험 정답 수 ·
  위험 정밀 = 위험 결과 중 위험 정답(또는 accept 가 그 등급을 받는 정답)과 짝지은 수 / 위험 결과 수 ·
  Low 재현·정밀(같은 꼴, 등급 Low) · 등급 일치 = 등급이 같거나 accept 안 / 짝 수 · 카테고리 = 둘 다 있을 때만.
- partial 정답(SG1 주장): suspect 재현 = suspect 중 위험 결과와 짝지은 수 / suspect 수 · supported 를 위험으로 낸 비 ·
  partial 은 두 분모 밖(수만) · 정밀 unmeasurable(공통 `guard_precision` 의 갈래).
- 결과가 점검하지 않은 쪽(결과 `pages` 밖)과 읽지 못한 쪽(notChecked)에 있는 정답은 분모에서 빼고 그 수를 따로 낸다.
"""
from __future__ import annotations

import difflib
from typing import Any

from evalrig.common import gold as common_gold
from evalrig.common import match as M
from evalrig.greenwash import units as U

FUZZY = 0.90
CONTAIN_SCORE = 0.995  # contain 은 fuzzy 보다 앞(정답 차례 안에서 점수 높은 후보부터)


def sim(g: dict[str, Any], p: dict[str, Any]) -> tuple[float, str]:
    a, b = g["sentence"], p["sentence"]
    if a and b and (a in b or b in a):
        return CONTAIN_SCORE, "contain"
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio(), "fuzzy"


def pair_units(gold_rows: list[dict[str, Any]], pred_rows: list[dict[str, Any]]) -> dict[str, list]:
    return M.pair(gold_rows, pred_rows, key=lambda x: (x["page"], x["sha12"]), sim=sim, threshold=FUZZY,
                  group=lambda x: x["page"], order="gold")


def _ok_level(g: dict[str, Any], p: dict[str, Any]) -> bool:
    return p["level"] == g["level"] or p["level"] in g.get("accept", [])


def score(gs: dict[str, Any], conv: dict[str, Any]) -> dict[str, Any]:
    """정답 셋 하나 × 변환 산출 하나(같은 문서여야 함 — 정답 meta.source.sha256 이 있으면 대조)."""
    common_gold.check(gs)
    want = (gs.get("meta") or {}).get("source", {}).get("sha256")
    if want and want != conv["source"]["sha256"]:
        raise ValueError(f"정답 문서 {want[:12]} ≠ 결과 문서 {conv['source']['sha256'][:12]}")
    nc = set(conv.get("notChecked") or [])
    checked = set(conv.get("pages") or [])
    outside = [it for it in gs["items"] if checked and it["page"] not in checked]
    gold_rows = [it for it in gs["items"] if (not checked or it["page"] in checked) and it["page"] not in nc]
    dropped = len(gs["items"]) - len(outside) - len(gold_rows)
    pred_rows = [u.payload for u in conv["units"]]
    pr = pair_units(gold_rows, pred_rows)
    pairs = [(gold_rows[d["gold"]], pred_rows[d["pred"]], d["how"]) for d in pr["pairs"]]
    out: dict[str, Any] = {
        "setId": gs["setId"], "coverage": gs["coverage"],
        "gold": len(gs["items"]), "goldOutsidePages": len(outside), "goldDroppedNotChecked": dropped, "pred": len(pred_rows),
        "pairs": len(pairs), "how": M.count_how(pr), "missed": len(pr["missed"]), "extra": len(pr["extra"]),
        "folded": conv.get("folded", 0),
    }
    risk = set(U.RISK_LEVELS)
    if gs["coverage"] == "exhaustive":
        out["risk"] = _pr(gold_rows, pred_rows, pairs, risk)
        out["low"] = _pr(gold_rows, pred_rows, pairs, {"Low"})
        lv = [(g, p) for g, p, _ in pairs if isinstance(g["level"], str)]
        out["levelAgreement"] = M.ratio(sum(_ok_level(g, p) for g, p in lv), len(lv))
    else:
        labs = gs.get("conventions", {}).get("labels", [])
        n = {lab: sum(1 for g in gold_rows if g["label"] == lab) for lab in labs}
        flagged = {lab: sum(1 for g, p, _ in pairs if g["label"] == lab and p["level"] in risk) for lab in labs}
        out["suspectRecall"] = M.ratio(flagged.get("suspect", 0), n.get("suspect", 0))
        out["supportedFlagged"] = M.ratio(flagged.get("supported", 0), n.get("supported", 0))
        out["partialCounts"] = {"n": n.get("partial", 0), "flagged": flagged.get("partial", 0)}
        out["precision"] = M.unmeasurable("partial")
        out["levelAgreement"] = M.unmeasurable("정답에 등급 없음(level absent)")
    out["category"] = M.unmeasurable("카테고리 없음(1차 결과·정답 모두 absent — MARC-ENT-612)")
    return out


def _pr(gold_rows, pred_rows, pairs, levels: set[str]) -> dict[str, Any]:
    gpos = [g for g in gold_rows if g["level"] in levels]
    ppos = [p for p in pred_rows if p["level"] in levels]
    hit_g = sum(1 for g, p, _ in pairs if g["level"] in levels and p["level"] in levels)
    hit_p = sum(1 for g, p, _ in pairs if p["level"] in levels and (g["level"] in levels or p["level"] in g.get("accept", [])))
    return {"recall": M.ratio(hit_g, len(gpos)), "precision": M.ratio(hit_p, len(ppos))}
