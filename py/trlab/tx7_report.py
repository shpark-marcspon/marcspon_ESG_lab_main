"""TRX7 판별 표 → out/trx7/summary.{json,md}. 실호출 0.

① 검수: 행 = 판 × 모델, 열 = 심은 재현(span/block_only/분모)·갈래별·실제 오역 재현·헛걸림·짚고도 안 고침(V0e)·호출·블록당 USD(평균·중앙값)·
   parse_failed·over_cap·call_failed. ② 이름: 행 = 판 × 모델, 열 = 일관율 3/3·2/3·규칙·표·nospan·서명/본문·호출·USD.
③ 자리별 USD·얼림 시각 대 첫 실행 시각·모듈 sha·실측 토큰/글자. 글(블록 원문)은 싣지 않는다 — 회사 이름 0.
명령: python -m trlab.tx7_report
"""

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

from trlab import tx7_pick as X
from trlab.review_probe import CATS

ORDER = ("V0r-gpt-4.1-mini", "V0e-gpt-4.1-mini", "V1-gpt-4.1-mini", "V2-gpt-4.1-mini", "V2-gpt-6-luna", "V3-gpt-6.1-sol")
NAME_ORDER = tuple(f"{v}-{m}" for v in ("N0", "N1", "N2") for m in (X.MINI, X.LUNA))


def _f(n, d):
    return f"{n}/{d}" + (f" ({100 * n / d:.0f}%)" if d else "")


def review_table():
    rows = []
    calls = X.calls()
    for name in ORDER:
        p = X.OUT7 / "score" / f"{name}.json"
        if not p.exists():
            continue
        sc = [r for r in json.load(open(p))["rows"] if r["split"] == "test"]
        pl = [r for r in sc if r["cat"] in CATS]
        rl = [r for r in sc if r["cat"] == "real"]
        cl = [r for r in sc if r["cat"] == "clean"]
        hit = Counter(r["hit"] for r in pl)
        rh = Counter(r["hit"] for r in rl)
        variant, model = name.split("-", 1)
        tag = f"TRX7/review/{variant}/{model}/"
        mine = [c for c in calls if not c.get("dry_run") and c.get("tag", "").startswith(tag) and "/dev-" not in c.get("tag", "")]
        usd = [c["costUsd"] for c in mine]
        rows.append({"name": name, "n": len(sc), "planted": {"span": hit["span"], "block_only": hit["block_only"], "miss": hit["miss"], "d": len(pl)},
                     "by_cat": {c: {"span": sum(1 for r in pl if r["cat"] == c and r["hit"] == "span"),
                                    "block_only": sum(1 for r in pl if r["cat"] == c and r["hit"] == "block_only"),
                                    "d": sum(1 for r in pl if r["cat"] == c)} for c in CATS},
                     "real": {"span": rh["span"], "block_only": rh["block_only"], "miss": rh["miss"], "d": len(rl)},
                     "false_alarm": {"n": sum(1 for r in cl if r["false_alarm"]), "d": len(cl)},
                     "noted_not_applied": sum(1 for r in sc if r.get("noted_not_applied")) if variant == "V0e" else None,
                     "scores": sorted(Counter(r["score"] for r in sc).items()) if variant == "V0e" else None,
                     "calls": len(mine), "usd": round(sum(usd), 6), "usd_mean": round(statistics.mean(usd), 6) if usd else 0.0,
                     "usd_median": round(statistics.median(usd), 6) if usd else 0.0,
                     "status": dict(Counter(r.get("status", "ok") for r in sc)),
                     "issues_per_block": round(sum(r.get("n_issues", 0) for r in sc) / max(1, len(sc)), 2),
                     "kinds": dict(Counter(k for r in sc for k in r.get("kinds", [])).most_common(6))})
    return rows


def names_table():
    rows = []
    calls = X.calls()
    for name in NAME_ORDER:
        p = X.OUT7 / "score" / f"{name}.json"
        if not p.exists():
            continue
        s = json.load(open(p))
        variant, model = name.split("-", 1)
        mine = [c for c in calls if not c.get("dry_run") and c.get("tag", "").startswith(f"TRX7/names/{variant}/{model}/")]
        rows.append({"name": name, "occ": s["n_occ"], "all": s["all"], "sign": s["sign"], "body": s["body"], "calls": len(mine),
                     "usd": round(sum(c["costUsd"] for c in mine), 6),
                     "unstable": [o["kr"] for o in s["occ"] if not o["all3"]]})
    return rows


def cross_model_names():
    """N0 에서 두 모델의 첫 회차 표기가 같은 출현 수(같은 표기인지 한 줄)."""
    out = {}
    for v in ("N0", "N1", "N2"):
        a = X.OUT7 / "score" / f"{v}-{X.MINI}.json"
        b = X.OUT7 / "score" / f"{v}-{X.LUNA}.json"
        if not (a.exists() and b.exists()):
            continue
        oa = {(o["id"], o["kr"]): o for o in json.load(open(a))["occ"]}
        ob = {(o["id"], o["kr"]): o for o in json.load(open(b))["occ"]}
        same = sum(1 for k in oa if k in ob and oa[k]["ens"] and ob[k]["ens"] and Counter(oa[k]["ens"]).most_common(1)[0][0]
                   == Counter(ob[k]["ens"]).most_common(1)[0][0])
        out[v] = {"same_majority": same, "d": len(oa)}
    return out


def misc():
    calls = X.calls()
    real = [c for c in calls if not c.get("dry_run")]
    tpc = {}
    for m in (X.MINI, X.LUNA, X.SOL):
        ks = sorted(c["inputTokens"] / c["chars"] for c in real if c.get("model") == m and c.get("chars"))
        tpc[m] = {"n": len(ks), "min": round(ks[0], 3) if ks else None, "median": round(ks[len(ks) // 2], 3) if ks else None,
                  "max": round(ks[-1], 3) if ks else None,
                  "out_median_review": statistics.median([c["outputTokens"] for c in real if c.get("model") == m and "/review/" in c.get("tag", "")] or [0]),
                  "out_median_names": statistics.median([c["outputTokens"] for c in real if c.get("model") == m and "/names/" in c.get("tag", "")] or [0])}
    freeze = (X.GOLD7 / "freeze.txt").read_text().splitlines()
    first = (X.OUT7 / "first-run.txt").read_text().splitlines() if (X.OUT7 / "first-run.txt").exists() else []
    return {"spent": {m: round(X.site_spent(m, calls), 4) for m in (X.MINI, X.LUNA, X.SOL)}, "caps": X.CAPS7, "total": round(X.total_spent(calls), 4),
            "total_cap": X.TOTAL_CAP7, "calls": len(real), "dry_run_rows": sum(1 for c in calls if c.get("dry_run")),
            "over_cap": sum(1 for c in calls if c.get("skip") == "over_cap"), "tok_per_char": tpc, "freeze": freeze, "first_run": first}


def md(rep):
    L = ["# TRX7 요약 — 검수 판 × 모델 · 이름 판 × 모델 (글 없음, 숫자만)", ""]
    L.append("## ① 검수(시험 셋 50 = 심은 24 · 깨끗 14 · 실제 오역 12). 열: 심은 재현 span / block_only / 분모 · 갈래별 span(block_only) · 실제 span / block_only · 헛걸림 · 짚고도 안 고침 · 호출 · 블록당 USD 평균 / 중앙값 · 부재")
    L.append("| 판 | 심은 | " + " | ".join(CATS) + " | 실제 | 헛걸림 | 짚고도 안 고침 | 호출 | USD/블록 | 부재 |")
    L.append("|---|---|" + "---|" * len(CATS) + "---|---|---|---|---|---|")
    for r in rep["review"]:
        p, q = r["planted"], r["real"]
        cats = " | ".join(f"{r['by_cat'][c]['span']}({r['by_cat'][c]['block_only']})/{r['by_cat'][c]['d']}" for c in CATS)
        nna = "–" if r["noted_not_applied"] is None else str(r["noted_not_applied"])
        absent = ", ".join(f"{k} {v}" for k, v in r["status"].items() if k != "ok") or "0"
        L.append(f"| {r['name']} | {p['span']} / {p['block_only']} / {p['d']} | {cats} | {q['span']} / {q['block_only']} / {q['d']} | "
                 f"{_f(r['false_alarm']['n'], r['false_alarm']['d'])} | {nna} | {r['calls']} | {r['usd_mean']:.4f} / {r['usd_median']:.4f} | {absent} |")
    v0e = next((r for r in rep["review"] if r["name"].startswith("V0e")), None)
    if v0e:
        L.append(f"\n- V0e 점수 분포(50): {v0e['scores']} — 96 이상 0 이라 `must_apply` 가 전부 참(V0e = V1). 이슈 종류 상위: {v0e['kinds']}")
    L.append("")
    L.append("## ② 이름(출현 15 = 서명 4 + 본문 11, 3회). 열: 3/3 일관 · 2/3 · 규칙 지킴(N1) · 표 지킴(N2) · nospan · 서명 3/3 · 본문 3/3 · 호출 · USD")
    L.append("| 판 | 3/3 | 2/3 | 규칙 | 표 | nospan | 서명 3/3 | 본문 3/3 | 호출 | USD | 흔들린 이름 수 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rep["names"]:
        a, s, b = r["all"], r["sign"], r["body"]
        L.append(f"| {r['name']} | {_f(a['all3']['n'], a['all3']['d'])} | {_f(a['two3']['n'], a['two3']['d'])} | {_f(a['rule_ok']['n'], a['rule_ok']['d'])} | "
                 f"{_f(a['table_ok']['n'], a['table_ok']['d'])} | {a['nospan']['n']}/{a['nospan']['d']} | {s['all3']['n']}/{s['all3']['d']} | "
                 f"{b['all3']['n']}/{b['all3']['d']} | {r['calls']} | {r['usd']:.4f} | {len(r['unstable'])} |")
    L.append(f"\n- 두 모델의 다수 표기가 같은 출현: {rep['cross_model']}")
    m = rep["misc"]
    L.append("")
    L.append("## ③ 자리별 USD · 실측 토큰/글자 · 얼림")
    L.append(f"- 쓴 돈: {m['spent']} / 자리 {m['caps']} · 합 {m['total']} / {m['total_cap']} · 실호출 {m['calls']} · dry_run 줄 {m['dry_run_rows']} · over_cap {m['over_cap']}")
    for k, v in m["tok_per_char"].items():
        L.append(f"- {k}: 토큰/글자 n {v['n']} min {v['min']} 중앙값 {v['median']} max {v['max']} · 출력 중앙값 검수 {v['out_median_review']} 이름 {v['out_median_names']}")
    L.append("- 얼림: " + " · ".join(m["freeze"]))
    L.append("- 첫 실행: " + " · ".join(m["first_run"]))
    return "\n".join(L) + "\n"


def main(argv=None):
    rep = {"review": review_table(), "names": names_table(), "cross_model": cross_model_names(), "misc": misc()}
    X.dump(X.OUT7 / "summary.json", rep)
    (X.OUT7 / "summary.md").write_text(md(rep))
    print(X.OUT7 / "summary.md")


if __name__ == "__main__":
    main(sys.argv[1:])
