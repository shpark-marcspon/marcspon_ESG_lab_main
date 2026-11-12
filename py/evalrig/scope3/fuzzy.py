"""L5 — 퍼지 사슬 붙은 줄 표(실비 0). 드라이버가 에너지 이름마다 1차 계수 사슬(`lookup_energy_ef`·`lookup_energy_ef_c3`)의 답을
JSON 으로 내고, 이 모듈이 갈래를 나누고 「사람 판정」 빈 칸(맞음/틀림/모름 — 감독이 채움)을 단 표를 낸다. 표·JSON 은 out/ 에만.

갈래: 정확(`exact_raw`·`unit_priority_alias`) · 별칭(`alias_exact`·`alias_contains`) · 포함(`contains`) · 퍼지 점수대
(0.72~0.8 · 0.8~0.93 · 그 밖) · 옛 판(`legacy_479`) · 폴백 상수(`fallback_patch` — 줄을 빼지 않는다) · 못 찾음(`none`).
폴백 상수(전력 0.45941 등, esg-app `colab_157/constants.py` `ENERGY_FALLBACK_EF_PATCH`)는 코드에 있는 값을 옮겨 적을 뿐 출처를
단 것이 아니다 — 출처 없음(`MARC-ENT-6`).
"""

from collections import Counter
from decimal import Decimal
from typing import Any

from evalrig.scope3.unit import ToolFailure

FUZZY_SCHEMA = "evalrig.scope3.fuzzy/1"
DRIVER_SCHEMA = "evc.l5-driver/1"
EXACT = frozenset({"exact_raw", "unit_priority_alias"})
ALIAS = frozenset({"alias_exact", "alias_contains"})
BANDS = ((Decimal("0.72"), Decimal("0.8"), "fuzzy 0.72~0.8"), (Decimal("0.8"), Decimal("0.93"), "fuzzy 0.8~0.93"))
ORDER = ("exact", "alias", "contains", "fuzzy 0.72~0.8", "fuzzy 0.8~0.93", "fuzzy other", "legacy", "fallback", "not_found")


def bucket(via: str, score: Any) -> str:
    if via in EXACT:
        return "exact"
    if via in ALIAS:
        return "alias"
    if via == "contains":
        return "contains"
    if via == "fuzzy":
        if isinstance(score, int | float | str) and not isinstance(score, bool):
            s = Decimal(str(score))
            for lo, hi, name in BANDS:
                if lo <= s < hi:
                    return name
        return "fuzzy other"
    if via == "legacy_479":
        return "legacy"
    if via == "fallback_patch":
        return "fallback"
    if via == "none":
        return "not_found"
    raise ToolFailure(f"모르는 via {via!r}")


def table(driver: dict[str, Any]) -> dict[str, Any]:
    if driver.get("schema") != DRIVER_SCHEMA:
        raise ToolFailure(f"L5 드라이버 schema 가 {DRIVER_SCHEMA!r} 가 아니다")
    rows = []
    for r in driver["rows"]:
        b = bucket(str(r["via"]), r.get("score"))
        rows.append({
            "source": r["source"], "sheet": r["sheet"], "name": r["name"], "matched": r.get("matched"), "via": r["via"],
            "score": r.get("score"), "bucket": b, "fallbackConstant": b == "fallback", "human": None,
        })  # fmt: skip
    counts = Counter(r["bucket"] for r in rows)
    return {
        "schema": FUZZY_SCHEMA,
        "note": "폴백 상수는 코드 값 그대로 — 출처 없음(MARC-ENT-6). 「human」 칸은 감독이 채운다(맞음/틀림/모름).",
        "counts": {k: counts.get(k, 0) for k in ORDER},
        "bySource": dict(sorted(Counter(r["source"] for r in rows).items())),
        "rows": rows,
    }


def render_md(report: dict[str, Any], with_names: bool) -> str:
    """with_names=False 면 수만(보고용). True 면 이름·붙은 줄까지(out/ 안 표 — 사람 판정용)."""
    lines = ["# L5 퍼지 사슬 붙은 줄", "", "폴백 상수: 출처 없음(`MARC-ENT-6`).", "", "| 갈래 | 수 |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in report["counts"].items()]
    if with_names:
        lines += ["", "| 출처 | 시트 | 이름 | 붙은 줄 | via | 점수 | 폴백 | 사람 판정 |", "|---|---|---|---|---|---|---|---|"]
        for r in report["rows"]:
            score = r["score"] if not isinstance(r["score"], dict) else r["score"].get("status")
            lines.append(f"| {r['source']} | {r['sheet']} | {r['name']} | {r['matched']} | {r['via']} | {score} | "
                         f"{'예' if r['fallbackConstant'] else ''} |  |")  # fmt: skip
    return "\n".join(lines) + "\n"
