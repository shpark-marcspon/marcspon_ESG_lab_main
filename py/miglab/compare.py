"""`compare --label <이름>`: 같은 label 의 live 실행 폴더(모듈마다 old·luna 마지막 것)를 견주는 표. 실호출 0.

수는 summary.json 의 것 그대로, 겹침은 metrics 의 열쇠(글의 sha256 앞 12자)로. 비율에는 분모를 단다.
"""

import json
from pathlib import Path
from typing import Any

from miglab import env, fixtures, metrics, modules


def latest(label: str) -> dict[tuple[str, str], dict[str, Any]]:
    found: dict[tuple[str, str], dict[str, Any]] = {}
    rows = [json.loads(x) for x in (env.CALLS.read_text(encoding="utf-8").splitlines() if env.CALLS.exists() else [])]
    for path in sorted((env.OUT / "runs").glob("*-live-*/summary.json")):
        summary = json.loads(path.read_text(encoding="utf-8"))
        run = path.parent.name
        if not any(r["lab"].get("run") == run and r["lab"].get("label") == label for r in rows):
            continue
        summary["folder"] = str(path.parent)
        result_path = path.parent / "result.json"
        if result_path.exists():  # 지표는 결과에서 다시 셈한다(지표 코드가 실행 뒤에 바뀌어도 같은 셈)
            source = fixtures.TRANSLATION_TEXT if summary["module"] == "tr" and summary["size"] == "full" else None
            summary["metrics"] = metrics.for_module(summary["module"], json.loads(result_path.read_text(encoding="utf-8")), source)
        found[(summary["module"], summary["variant"])] = summary
    return found


def _brief(module: str, m: dict[str, Any] | None) -> dict[str, Any]:
    if m is None:
        return {}
    match module:
        case "gw":
            return {k: m[k] for k in ("items", "byLevel", "candidatesRaw", "candidatesKept", "failedPages", "missingFieldsItems")}
        case "tr":
            (v,) = m["variants"]
            keep = ("qaRan", "qaScores", "qaBelow96", "qaApplied", "reviewIssues", "glossaryRows", "numbersKept", "tagsKept",
                    "remainingKoreanFragments", "autofixStatus", "autofixStop", "fragmentStatus", "translatedChars")  # fmt: skip
            return {**{k: v.get(k) for k in keep}, "warnings": m["warnings"]}
        case "dv":
            return {k: m[k] for k in m if k != "keys"}
        case "mt":
            return m
        case "s3":
            return {k: m[k] for k in m if k != "picks"}
        case "dr":
            return m
    return m


def table(label: str) -> dict[str, Any]:
    runs = latest(label)
    out: dict[str, Any] = {"label": label, "modules": {}}
    total = 0.0
    for module in modules.MODULES:
        old, luna = runs.get((module, "old")), runs.get((module, "luna"))
        if old is None and luna is None:
            continue
        entry: dict[str, Any] = {}
        for name, s in (("old", old), ("luna", luna)):
            if s is None:
                continue
            c = s["calls"]
            total += c["costUsd"]
            entry[name] = {
                "folder": Path(s["folder"]).name,
                "calls": c["records"],
                "resultKinds": c["resultKinds"],
                "models": c["models"],
                "usd": c["costUsd"],
                "seconds": s["seconds"],
                "inputTokens": c["inputTokens"],
                "cacheReadTokens": c["cacheReadTokens"],
                "outputTokens": c["outputTokens"],
                "thinkingTokens": c["thinkingTokens"],
                "cutOff": c["cutOff"],
                "schemaMismatch": c["schemaMismatch"],
                "error": s["error"],
                "metrics": _brief(module, s["metrics"]),
            }
        if old and luna and old["metrics"] and luna["metrics"]:
            a, b = old["metrics"], luna["metrics"]
            if module in ("gw", "dv"):
                entry["overlap"] = metrics.overlap(a.get("keys", []), b.get("keys", []))
            if module == "s3":
                entry["agreement"] = metrics.agreement(a["picks"], b["picks"])
        out["modules"][module] = entry
    out["totalUsd"] = round(total, 6)
    return out


def main(label: str) -> int:
    result = table(label)
    path = env.OUT / f"compare-{label}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for module, entry in result["modules"].items():
        for name in ("old", "luna"):
            if name in entry:
                e = entry[name]
                print(
                    f"{module:<3} {name:<4} 호출 {e['calls']:>3} {e['resultKinds']} ${e['usd']:.4f} {e['seconds']}s "
                    f"in {e['inputTokens']} cache {e['cacheReadTokens']} out {e['outputTokens']} think {e['thinkingTokens']} "
                    f"잘림 {e['cutOff']}"
                )
                print(f"      {json.dumps(e['metrics'], ensure_ascii=False)}")
        for key in ("overlap", "agreement"):
            if key in entry:
                print(f"      {key}: {entry[key]}")
    print(f"합 ${result['totalUsd']:.4f} → {path}")
    return 0
