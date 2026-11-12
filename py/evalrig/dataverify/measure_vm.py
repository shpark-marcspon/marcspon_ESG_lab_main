"""흔들림·견줌 셈(발주서 MB2 §2-8) — 1차 결과(result@2)를 `convert_156` 단위로 바꾼 뒤 코드로만 센다. 모델 0.

- `jitter(runs, gold)`: 검산 단위 열쇠 = (rule, 정렬한 풀린 항목, 안 풀린 수, year) — 안 풀린 수를 넣어 서로 다른 부분 단위가
  한 열쇠로 겹치지 않게(critic R6). 열쇠별 실행 수 분포, 둘 이상 실행에 있는 열쇠의 판정 엇갈림, 정답 FAIL 을 모든/일부/0 실행이
  맞힘(항목이 하나라도 풀린 FAIL 단위 — 재현과 같은 짝 규칙), 실행마다 plan 산식·검사·인사이트 행 수, 인사이트 ID 자카드(쌍 평균).
- `arms_table(arms, gold)`: 판(v1·labfix)마다 거짓 FAIL 단위(정답 FAIL·accept FAIL 을 품지 않은 완전 해석 FAIL), 심은 FAIL 재현,
  집약도 관계 판정, 곁 기록의 쓴 허용 오차·mode. labfix 판의 mode=intensity 호출이 0 이면 집약도 결론은 「판정 불가」(critic B3).
- `risk_table(...)`: `score._risk` 를 전체·환경만 두 분모로.
"""
from __future__ import annotations

from collections import Counter
from itertools import combinations
from typing import Any

from evalrig.common import match as M
from evalrig.dataverify import score as S


def _fails(conv: dict[str, Any]) -> list[dict[str, Any]]:
    return [u.payload for u in conv["units"] if u.unit.endswith("check_year@1")]


def unit_key(p: dict[str, Any]) -> tuple:
    return (p["rule"], tuple(sorted(p["items"])), len(p.get("unresolved") or []), p["year"])


def _gold_fail(gold: dict[str, Any]) -> set[tuple[str, str]]:
    return {(it["indicator"], it["year"]) for it in gold["items"] if it["verdict"] == "FAIL"}


def _hits(conv: dict[str, Any], gf: set[tuple[str, str]]) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for p in _fails(conv):
        if p["verdict"] == "FAIL" and p["items"]:
            out |= {(i, p["year"]) for i in p["items"]} & gf
    return out


def _insight_ids(conv: dict[str, Any]) -> set[str]:
    return {u.payload["id"] for u in conv["units"] if u.unit.endswith("insight_row@1") and u.payload["id"]}


def jitter(runs: list[dict[str, Any]], gold: dict[str, Any]) -> dict[str, Any]:
    """runs = [{"conv": convert 결과, "result": result@2 dict}]."""
    n = len(runs)
    seen: dict[tuple, list[str]] = {}
    for r in runs:
        for p in _fails(r["conv"]):
            seen.setdefault(unit_key(p), []).append(p["verdict"])
    per = Counter(len(v) for v in seen.values())
    multi = {k: v for k, v in seen.items() if len(v) >= 2}
    gf = _gold_fail(gold)
    hits = [_hits(r["conv"], gf) for r in runs]
    cnt = Counter(sum(g in h for h in hits) for g in gf)
    ins = [_insight_ids(r["conv"]) for r in runs]
    jac = [len(a & b) / len(a | b) if (a | b) else 1.0 for a, b in combinations(ins, 2)]
    return {
        "runs": n,
        "keys": len(seen),
        "keysByRuns": {str(k): per.get(k, 0) for k in range(n, 0, -1)},
        "keysInTwoOrMore": len(multi),
        "verdictDisagree": sum(1 for v in multi.values() if len(set(v)) > 1),
        "goldFailHit": {"all": cnt.get(n, 0), "some": sum(cnt.get(k, 0) for k in range(1, n)), "none": cnt.get(0, 0),
                        "den": len(gf)},
        "perRun": [{"planFormulas": len((r["result"].get("plan") or {}).get("formulas") or []),
                    "checks": len((r["result"].get("review") or {}).get("checks") or []),
                    "checkUnits": len(_fails(r["conv"])),
                    "insightRows": sum(1 for u in r["conv"]["units"] if u.unit.endswith("insight_row@1")),
                    "recall": S.score(gold, r["conv"])["recall"]} for r in runs],
        "insightJaccard": {"pairs": [round(x, 4) for x in jac], "mean": round(sum(jac) / len(jac), 4) if jac else None},
    }


def false_fail_units(conv: dict[str, Any], gold: dict[str, Any]) -> list[dict[str, Any]]:
    gf = _gold_fail(gold)
    acc = {(it["indicator"], it["year"]) for it in gold["items"] if "FAIL" in it.get("accept", [])}
    out = []
    for p in _fails(conv):
        if p["verdict"] != "FAIL" or not p["items"] or p.get("unresolved"):
            continue
        if not ({(i, p["year"]) for i in p["items"]} & (gf | acc)):
            out.append({"rule": p["rule"], "items": sorted(p["items"]), "year": p["year"], "tool": p.get("tool")})
    return out


def arms_table(arms: dict[str, dict[str, Any]], gold: dict[str, Any], intensity_ids: set[str]) -> dict[str, Any]:
    """arms = {이름: {"conv", "cond"(n·esgApp·stageCosts·webSearch), "side"(labfix.jsonl 줄 목록 | None), "v1RatioOnIntensity"}}."""
    out: dict[str, Any] = {}
    gf = _gold_fail(gold)
    for name, a in arms.items():
        ff = false_fail_units(a["conv"], gold)
        inten = [p for p in _fails(a["conv"]) if set(p["items"]) & intensity_ids]
        side = a.get("side")
        modes = Counter(x["mode"] for x in side) if side is not None else None
        row = {
            "cond": a["cond"],
            "falseFailUnits": len(ff),
            "falseFail": ff,
            "plantedRecall": M.ratio(len(_hits(a["conv"], gf)), len(gf)),
            "intensityRelation": dict(sorted(Counter(p["verdict"] for p in inten).items())),
            "intensityRatioToolCalls": a.get("v1RatioOnIntensity"),
        }
        if side is not None:
            row["labfixCalls"] = dict(sorted(modes.items()))
            row["appliedTolerance"] = sorted({(x["mode"], x["appliedTolerance"]) for x in side if x["appliedTolerance"] is not None})
            row["intensityConclusion"] = "판정 가능" if modes.get("intensity", 0) > 0 else "판정 불가(mode=intensity 호출 0)"
        out[name] = row
    return out


def _subset(gold: dict[str, Any], ids: set[str]) -> dict[str, Any]:
    items = [it for it in gold["items"] if it["indicator"] in ids]
    pairs = Counter(it["pair"] for it in items if it.get("pair"))
    return {**gold, "items": [it for it in items if not it.get("pair") or pairs[it["pair"]] == 2]}


def risk_table(gold: dict[str, Any], conv: dict[str, Any], env_ids: set[str]) -> dict[str, Any]:
    return {"all": S._risk(gold, conv), "envOnly": S._risk(_subset(gold, env_ids), conv)}
