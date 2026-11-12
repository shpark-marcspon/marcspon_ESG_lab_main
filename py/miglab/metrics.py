"""지표: 호출 기록(core CallRecord) 셈과 모듈별 결과의 수·꼴. 보고서 글은 싣지 않는다(수·꼴만)."""

import hashlib
import json
import re
from collections import Counter
from collections.abc import Sequence
from typing import Any


def call_stats(records: Sequence[Any]) -> dict[str, Any]:
    kinds = Counter(r.result_kind for r in records)
    models = Counter(r.api_model_id for r in records)
    prompts = Counter(r.prompt_id for r in records)
    return {
        "records": len(records),
        "resultKinds": dict(kinds),
        "models": dict(models),
        "prompts": dict(prompts),
        "inputTokens": sum(r.input_tokens or 0 for r in records),
        "outputTokens": sum(r.output_tokens or 0 for r in records),
        "thinkingTokens": sum(r.thinking_tokens or 0 for r in records),
        "cacheReadTokens": sum(r.cache_read_tokens or 0 for r in records),
        "costUsd": round(sum(r.cost_usd or 0.0 for r in records), 6),
        "durationS": round(sum(r.duration_ms for r in records) / 1000, 1),
        "cutOff": kinds.get("truncated", 0),
        "schemaMismatch": kinds.get("schema_mismatch", 0),
    }


def shape(value: Any, depth: int = 0) -> Any:
    """값의 꼴(열쇠와 길이만)."""
    if depth > 3:
        return type(value).__name__
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, list):
        return [f"len={len(value)}", shape(value[0], depth + 1)] if value else ["len=0"]
    return type(value).__name__


def _h(text: Any) -> str:
    """글의 sha256 앞 12자 — 겹침 셈용(글은 싣지 않는다)."""
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:12]


NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
TAG = re.compile(r"\[(?:PAGE|BLOCK|TABLE|VISUAL|FOOTER)[^\]]*\]")


def _gw(result: list[dict[str, Any]]) -> dict[str, Any]:
    (found,) = result
    items = found.get("items", [])
    stats = found.get("candidateStats", {})
    return {
        "items": len(items),
        "byLevel": dict(Counter(i.get("level") for i in items)),
        "candidatesRaw": stats.get("raw"),
        "candidatesKept": stats.get("kept"),
        "failedPages": len(stats.get("failedPages", [])),
        "keys": sorted({f"{i.get('page')}:{_h(i.get('sentence'))}" for i in items}),
        "missingFieldsItems": sum(1 for i in items if i.get("missingFields")),
    }


def _tr(result: list[dict[str, Any]], source: str | None) -> dict[str, Any]:
    found = result[0]
    out: dict[str, Any] = {"status": found.get("status"), "warnings": dict(Counter(w["code"] for w in found.get("warnings", [])))}
    variants = []
    for variant in found.get("variants", []):
        pages = variant.get("pages", [])
        reviews = [p.get("review") or {} for p in pages]
        qa = [(f.get("editorialQa") or {}) for f in variant.get("fragments", [])]
        scores = [q.get("qualityScore") for q in qa if q.get("ran")]
        text = variant.get("translatedText") or ""
        entry: dict[str, Any] = {
            "label": variant.get("label"),
            "pages": len(pages),
            "fragmentStatus": dict(Counter(f.get("status") for f in variant.get("fragments", []))),
            "reviewStatus": dict(Counter(r.get("status") for r in reviews)),
            "reviewIssues": {k: sum((r.get("counts") or {}).get(k, 0) for r in reviews) for k in ("high", "medium", "low")},
            "qaRan": len(scores),
            "qaScores": scores,
            "qaBelow96": sum(1 for s in scores if s is not None and s < 96),
            "qaApplied": sum(1 for q in qa if q.get("applied")),
            "remainingKoreanFragments": sum(1 for f in variant.get("fragments", []) if f.get("remainingKorean")),
            "glossaryRows": len(variant.get("glossaryCheck", [])),
            "autofixStatus": (variant.get("autofix") or {}).get("status"),
            "autofixStop": ((variant.get("autofix") or {}).get("stop") or {}).get("kind"),
            "translatedChars": len(text),
        }
        if source is not None:
            src_numbers = Counter(NUMBER.findall(source))
            out_numbers = Counter(NUMBER.findall(text))
            entry["numbersKept"] = f"{sum((src_numbers & out_numbers).values())}/{sum(src_numbers.values())}"
            src_tags = Counter(TAG.findall(source))
            entry["tagsKept"] = f"{sum((src_tags & Counter(TAG.findall(text))).values())}/{sum(src_tags.values())}"
        variants.append(entry)
    out["variants"] = variants
    return out


def _dv(result: list[dict[str, Any]]) -> dict[str, Any]:
    found = result[0]
    plan, review, insight = found.get("plan", {}), found.get("review", {}), found.get("insight", {})
    checks = review.get("checks") or []
    return {
        "plan": plan.get("status"),
        "planFailure": (plan.get("failure") or {}).get("kind"),
        "formulas": len(plan.get("formulas") or []),
        "review": review.get("status"),
        "reviewPartial": review.get("partial"),
        "reviewFailure": (review.get("failure") or {}).get("kind"),
        "checks": len(checks),
        "checkKinds": dict(Counter(str(c.get("kind")) for c in checks)),
        "checkTools": dict(Counter(str(c.get("tool")) for c in checks)),
        "keys": sorted({f"{c.get('tool')}:{c.get('kind')}" for c in checks}),
        "insight": insight.get("status"),
        "insightPartial": insight.get("partial"),
        "insightRows": len(insight.get("rows") or []),
        "insightSources": len(insight.get("sources") or []),
        "insightFailure": (insight.get("failure") or {}).get("kind"),
        "warnings": dict(Counter(w["code"] for w in found.get("warnings", []))),
    }


def _mt_part1(found: dict[str, Any]) -> dict[str, Any]:
    topics = found.get("part1", {}).get("topics", [])
    keywords = found.get("part1", {}).get("keywords", {})
    return {
        "topics": len(topics),
        "regulationStatus": dict(Counter(t["regulation"]["status"] for t in topics)),
        "regulationItems": sum(len(t["regulation"].get("items") or []) for t in topics),
        "controversyStatus": dict(Counter(t["controversy"]["status"] for t in topics)),
        "controversyItems": sum(len(t["controversy"].get("items") or []) for t in topics),
        "keywordsStatus": keywords.get("status"),
        "keywordTopics": len(keywords.get("byTopic") or []),
        "warnings": dict(Counter(w["code"] for w in found.get("warnings", []))),
    }


def _mt(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):  # mt: {"part1": [...], "part2": [...] | None}
        out = {"part1": _mt_part1(result["part1"][0])}
        if result.get("part2"):
            found = result["part2"][0]
            subs = [s for t in found.get("part2", {}).get("topics", []) for s in t.get("subtopics", [])]
            out["part2"] = {
                "status": found.get("part2", {}).get("status"),
                "subtopics": len(subs),
                "impact": dict(Counter(s["impact"]["status"] for s in subs)),
                "impactRows": sum(len(s["impact"].get("rows") or []) for s in subs),
                "financial": dict(Counter(s["financial"]["status"] for s in subs)),
                "financialRows": sum(len(s["financial"].get("rows") or []) for s in subs),
                "pathway": dict(Counter(s["pathway"]["status"] for s in subs)),
            }
        return out
    return {"part1": _mt_part1(result[0])}


def _s3(result: list[dict[str, Any]]) -> dict[str, Any]:
    (found,) = result
    rows = [(sheet["sheet"], row) for sheet in found.get("sheets", []) for row in sheet.get("rows") or []]
    picks = {
        f"{sheet}:{row.get('rowId')}": [
            (row.get("group") or {}).get("category"),
            (row.get("factor") or {}).get("title"),
            (row.get("group") or {}).get("name"),
        ]
        for sheet, row in rows
    }
    return {
        "status": found.get("status"),
        "sheets": {s["sheet"]: s["status"] for s in found.get("sheets", [])},
        "rows": len(rows),
        "origin": dict(Counter(row.get("origin") for _, row in rows)),
        "withFactor": sum(1 for _, row in rows if row.get("factor")),
        "llmCalls": found.get("llmCalls"),
        "picks": picks,
    }


def _dr(result: list[dict[str, Any]]) -> dict[str, Any]:
    (found,) = result
    leaves = found.get("leaves", [])
    outcomes = [leaf.get("outcome") or {} for leaf in leaves]
    bullets = [b for o in outcomes for b in (o.get("bullets") or [])]
    return {
        "status": (found.get("status") or {}).get("status"),
        "leafOutcome": dict(Counter(o.get("status") for o in outcomes)),
        "bullets": len(bullets),
        "bulletChars": sum(len(str(b.get("text", ""))) for b in bullets),
        "mappingBatches": (found.get("mapping") or {}).get("batches"),
        "mappingFailed": len((found.get("mapping") or {}).get("failedBatches") or []),
        "warnings": dict(Counter(w["code"] for w in found.get("warnings", []))),
    }


def for_module(module: str, result: Any, source: str | None = None) -> dict[str, Any]:
    try:
        match module:
            case "gw":
                return _gw(result)
            case "tr":
                return _tr(result, source)
            case "dv":
                return _dv(result)
            case "mt" | "mt1":
                return _mt(result)
            case "s3":
                return _s3(result)
            case "dr":
                return _dr(result)
    except (KeyError, TypeError, ValueError) as error:
        return {"metricsError": f"{type(error).__name__}: {error}", "shape": shape(result)}
    return {"shape": shape(result)}


def overlap(a: Sequence[str], b: Sequence[str]) -> str:
    """두 판 열쇠의 겹침 「같음/합집합」."""
    left, right = set(a), set(b)
    union = left | right
    return f"{len(left & right)}/{len(union)}"


def agreement(a: dict[str, Any], b: dict[str, Any]) -> dict[str, str]:
    """s3 의 줄마다 (카테고리, 계수 제목, 묶음 이름) 일치 — 「같음/둘 다 있는 줄」."""
    common = sorted(set(a) & set(b))
    same = [sum(1 for k in common if a[k][i] == b[k][i]) for i in range(3)]
    return {
        "category": f"{same[0]}/{len(common)}",
        "factor": f"{same[1]}/{len(common)}",
        "groupName": f"{same[2]}/{len(common)}",
    }
