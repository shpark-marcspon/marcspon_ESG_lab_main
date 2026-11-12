"""TRX9 판별 표 — `python -m raslab.ras2_report` → OUT9/summary.{json,md}. 산출만 읽는다(실호출 0, 정답은 score.json 을 통해서만).
표 ① 19권 갈래·정밀·재현·민감도 · 표 ② 분류×갈래 recall(c3·c4·②a·②b 셋) + 규칙별 걸림·영역당 USD · 표 ③ 6항목 180dpi vs native.
"""

import json
import sys
from pathlib import Path

from raslab import ras2_pick as P
from raslab import ras2_scan as S

OUT9 = S.OUT9
RAS1 = P.RAS1
GROUPS = [("raster/table", "래스터 표 5"), ("raster/chart", "차트 6"), ("raster/scan", "스캔 3"), ("raster/infographic", "인포 1"), ("outline/table", "윤곽선 3"), ("raster/ALL", "래스터 15"), ("ALL/ALL", "전체 18")]


def jload(p, default=None):
    p = Path(p)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else default


def pct(v):
    return "—" if v is None else f"{v * 100:.1f}"


def cell(summary, key):
    d = (summary or {}).get(key)
    return "—" if not d else f"{pct(d['word_recall'])} / {pct(d['num_recall'])} / {d['nums_changed']}"


def items_map(score):
    return {r["id"]: r for r in (score or {}).get("items", [])}


def table1(ev, br):
    lines = ["| 셋 | n | tp | fp | fn | tn | 정밀 | 재현 |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for s in ("dev", "hold", "all"):
        d = ev["sets"][s]
        lines.append(f"| {s} | {d['n']} | {d['tp']} | {d['fp']} | {d['fn']} | {d['tn']} | {d['precision']} | {d['recall']} |")
    lines += ["", "| 민감도 | hold P | hold R | dev P | dev R |", "|---|---:|---:|---:|---:|"]
    for name, sets in ev["sensitivity"].items():
        lines.append(f"| {name} | {sets['hold']['precision']} | {sets['hold']['recall']} | {sets['dev']['precision']} | {sets['dev']['recall']} |")
    lines += ["", "| kind | 잡힘/라벨 | 갈래 맞음 |", "|---|---:|---:|"]
    for k, d in sorted(ev["kinds"].items()):
        lines.append(f"| {k} | {d['any']}/{d['n']} | {d['match']} |")
    lines += ["", "| 키 | 쪽 | raster | outline | both | none | 있음 % |", "|---|---:|---:|---:|---:|---:|---:|"]
    for k, b in br["books"].items():
        c = b["count"]
        lines.append(f"| {k} | {b['n']} | {c['raster']} | {c['outline']} | {c['both']} | {c['none']} | {(b['n'] - c['none']) / b['n']:.1%} |")
    t = br["total"]
    lines.append(f"| 합 | {t['n']} | {t['raster']} | {t['outline']} | {t['both']} | {t['none']} | {(t['n'] - t['none']) / t['n']:.1%} |")
    return lines


def usd_rows():
    rows = [r for r in P.calls() if not r.get("dry_run")]
    by = {}
    for r in rows:
        by.setdefault(r["site"], {"n": 0, "usd": 0.0, "over_cap": 0})
        by[r["site"]]["n"] += r.get("status") != "over_cap"
        by[r["site"]]["usd"] += r.get("usd", 0)
        by[r["site"]]["over_cap"] += r.get("status") == "over_cap"
    sub = {}
    for r in rows:
        k = r["tag"].split("/")[1]
        sub.setdefault(k, {"n": 0, "usd": 0.0, "in": 0, "out": 0})
        if r.get("status") not in ("over_cap",):
            sub[k]["n"] += 1
            sub[k]["usd"] += r.get("usd", 0)
            sub[k]["in"] += r.get("input_tokens", 0) or 0
            sub[k]["out"] += r.get("output_tokens", 0) or 0
    return {"total": round(sum(r.get("usd", 0) for r in rows), 4), "by_site": by, "by_run": sub, "n": len(rows)}


def table2(scores, checks, usd):
    cols = [("c3", "c3 luna"), ("c4", "c4 sol(기준)"), ("b2a", "②a 흉내"), ("b2_luna", "②b luna 기본"), ("b2_hint", "②b luna 소수점"), ("b2_combo", "②b luna+검사→sol")]
    lines = ["| 분류×갈래 | " + " | ".join(c[1] for c in cols) + " |", "|---|" + "---:|" * len(cols)]
    for key, label in GROUPS:
        lines.append(f"| {label} | " + " | ".join(cell((scores.get(c[0]) or {}).get("summary"), key) for c in cols) + " |")
    lines.append("(칸 = 낱말 recall / 숫자 recall / 숫자 바뀐 칸)")
    lines += ["", "| 판 | 영역 | 걸림(sol 후보) | no_table | R1 | R2 | R3 | R4 | R5 |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, ck in (("②a 흉내(R4 없음)", checks.get("b2a")), ("②b luna 둘", checks.get("b2"))):
        if ck:
            br = ck["by_rule"]
            lines.append(f"| {name} | {ck['n']} | {ck['sol']} | {ck['no_table']} | {br.get('R1', 0)} | {br.get('R2', 0)} | {br.get('R3', 0)} | {br.get('R4', '—')} | {br.get('R5', 0)} |")
    b2 = checks.get("b2") or {}
    flagged = [x["name"] + ":" + "+".join(x.get("rules", [])) for x in b2.get("regions", []) if x.get("sol")]
    lines.append("②b 걸린 영역: " + (", ".join(flagged) or "—"))
    run = usd["by_run"]
    luna2 = run.get("b2luna", {}).get("usd", 0) + run.get("b2lunah", {}).get("usd", 0)
    sol2 = run.get("b2sol", {}).get("usd", 0)
    per_region = (luna2 + sol2) / 27
    lines += ["", f"영역당 USD(②b, luna 둘 + sol 걸린 것 ÷ 27) = **${per_region:.5f}** (luna 둘 ${luna2:.4f} + sol ${sol2:.4f}, sol 되묻기 {run.get('b2sol', {}).get('n', 0)} 영역) ↔ c4 sol 전부 $0.00637/영역 · c3 luna $0.00044/영역",
              f"100쪽 셈(RAS1 영역/쪽 1.65 조각만 · 3.14 덧길까지): ②b ${per_region * 165:.3f} · ${per_region * 314:.3f} ↔ c4 $1.05 · $2.00"]
    return lines, per_region


def table3(scores, crops, calls):
    c3, c4, l3, s3 = (items_map(scores.get(k)) for k in ("c3", "c4", "b3_luna", "b3_sol"))
    lines = ["| 항목 | 180 luna | 180 sol | native luna | native sol | 180 px → native px | 입력 토큰 sol 180→native | USD sol 180→native |", "|---|---:|---:|---:|---:|---|---|---|"]
    tok180 = {(r["report"], r["page"], r["region"]): r for r in [json.loads(x) for x in open(RAS1 / "calls.jsonl", encoding="utf-8") if x.strip()] if r.get("mode") == "region"}
    l2s, s2l = P.keymap()
    reg_of = {"is_p45_c1": ("issi", 45, 0), "is_p45_c2": ("issi", 45, 0), "is_p45_c3": ("issi", 45, 0), "is_p46_tbl": ("issi", 46, 1), "is_p50_tbl": ("issi", 50, 0), "is_p74_tbl": ("issi", 74, 1)}
    f = lambda r: "—" if not r else f"{r['found']}/{r['gold_words']} · {r['nums_found']}/{r['gold_nums']} · {r['nums_changed']}"  # noqa: E731
    for gid in sorted(P.B3_GOLD):
        k, pg, i = reg_of[gid]
        old = tok180.get((s2l[k], pg, i, ), None)
        old_sol = next((r for r in tok180.values() if r.get("report") == s2l[k] and r["page"] == pg and r["region"] == i and "sol" in r["model"]), None)
        new_sol = next((r for r in calls if r.get("tag") == f"TRX9/b3sol/{k}_p{pg}_r{i}" and r.get("status") == "completed"), None)
        cr = crops.get(f"{k}_p{pg}_r{i}-180", {}), crops.get(f"{k}_p{pg}_r{i}-native", {})
        px = f"{cr[0].get('px')} → {cr[1].get('px')} ({cr[1].get('dpi')}dpi)"
        tk = f"{old_sol and old_sol.get('input_tokens')} → {new_sol and new_sol.get('input_tokens')}"
        us = f"{old_sol and old_sol.get('usd')} → {new_sol and new_sol.get('usd')}"
        lines.append(f"| {gid} | {f(c3.get(gid))} | {f(c4.get(gid))} | {f(l3.get(gid))} | {f(s3.get(gid))} | {px} | {tk} | {us} |")
    lines.append("(칸 = 낱말 찾음/정답 · 숫자 찾음/정답 · 바뀐 칸)")

    def agg(m):
        rs = [m[g] for g in P.B3_GOLD if g in m]
        if not rs:
            return "—"
        return f"낱말 {sum(r['found'] for r in rs)}/{sum(r['gold_words'] for r in rs)} · 숫자 {sum(r['nums_found'] for r in rs)}/{sum(r['gold_nums'] for r in rs)} · 바뀐 {sum(r['nums_changed'] for r in rs)}"

    lines.append(f"6항목 합 — 180 luna {agg(c3)} · 180 sol {agg(c4)} · native luna {agg(l3)} · native sol {agg(s3)}")
    return lines


def fixed(scores):
    """방법별 고친/망친 항목(기준 c3 luna → 각 판, 낱말 recall 항목 단위)."""
    base = items_map(scores.get("c3"))
    out = {}
    for name in ("c4", "b2a", "b2_luna", "b2_hint", "b2_combo", "b3_luna", "b3_sol"):
        m = items_map(scores.get(name))
        if not m:
            continue
        up = [g for g in m if g in base and m[g]["found"] > base[g]["found"]]
        down = [g for g in m if g in base and m[g]["found"] < base[g]["found"]]
        out[name] = {"up": up, "down": down}
    return out


def main(argv=None):
    ev, br = jload(OUT9 / "eval.json"), jload(OUT9 / "branch.json")
    scores = {
        "c3": jload(RAS1 / "c3" / "score.json"), "c4": jload(RAS1 / "c4" / "score.json"),
        "b2a": jload(OUT9 / "b2a" / "cand" / "score.json"),
        "b2_luna": jload(OUT9 / "b2" / "luna" / "cand" / "score.json"), "b2_hint": jload(OUT9 / "b2" / "luna_hint" / "cand" / "score.json"), "b2_combo": jload(OUT9 / "b2" / "combo" / "cand" / "score.json"),
        "b3_luna": jload(OUT9 / "b3" / "luna" / "cand" / "score.json"), "b3_sol": jload(OUT9 / "b3" / "sol" / "cand" / "score.json"),
    }
    checks = {"b2a": jload(OUT9 / "b2a" / "check.json"), "b2": jload(OUT9 / "b2" / "check.json")}
    crops = jload(OUT9 / "crop" / "crops.json", {})
    usd = usd_rows()
    md = ["# TRX9 summary", "", "## 표 ① 훑개 갈래"] + (table1(ev, br) if ev and br else ["(없음)"])
    t2, per_region = table2(scores, checks, usd)
    md += ["", "## 표 ② 수치 자리 검사"] + t2
    md += ["", "## 표 ③ 자르기 배율"] + table3(scores, crops, P.calls())
    fx = fixed(scores)
    md += ["", "## 방법별 고친/망친 항목(c3 luna 대비, 낱말)"] + [f"- {k}: ↑ {v['up'] or '—'} · ↓ {v['down'] or '—'}" for k, v in fx.items()]
    md += ["", "## 실호출", f"- 합 ${usd['total']} / 상한 $0.6 — 자리별 " + ", ".join(f"{s} ${d['usd']:.4f} ({d['n']}건, over_cap {d['over_cap']})" for s, d in usd["by_site"].items()),
           "- 판별 " + ", ".join(f"{k} {d['n']}건 ${d['usd']:.4f} in {d['in']} out {d['out']}" for k, d in usd["by_run"].items())]
    fr = (OUT9 / "first-run.txt").read_text(encoding="utf-8") if (OUT9 / "first-run.txt").is_file() else ""
    md += ["", "## 얼림", "```", fr.strip(), "```"]
    (OUT9 / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (OUT9 / "summary.json").write_text(json.dumps({"eval": ev and ev["sets"], "branch_total": br and br["total"], "scores": {k: (v or {}).get("summary") for k, v in scores.items()}, "checks": {k: (v and {kk: vv for kk, vv in v.items() if kk != "regions"}) for k, v in checks.items()}, "usd": usd, "per_region_usd": per_region, "fixed": fx}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    sys.exit(main())
