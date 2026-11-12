"""TRX10 판별 표 → out/trx10/summary.{json,md} (MARC-TICK-514).

① 전량(luna 1,391 · 4.1-mini 300 · luna 같은 300): 걸림율(전체·꼴·띠) · 블록당 이슈 0/1/2/3+ · 갈래 · 쪽당 걸림 · 라벨 정밀 · 지연 · USD · 상태
② 누락 규칙(규칙만 · 규칙 + 되묻기): 심은 omit /4 · 헛걸림 /34 · 전량 걸림 · 라벨 /40 · 되묻기 일치 · 호출·USD
③ 묶음(낱개 TRX7 읽기 · 묶음 × 모델 둘): 심은 /24 · 갈래별 · 헛걸림 /14 · 묶음 수·블록/묶음 · 블록당 USD·지연 · oob · parse_failed
④ 자리별 USD · 「100쪽 책 = n USD · m 분」(sam 87쪽 × 100/87) · 얼림 시각 대 첫 실행 · 모듈 sha · 100 짝 뒤 예상 대 실측
호출 0 — 읽기만. 두 번 뽑아 cmp 같아야 한다(시각은 장부·first-run 에서 읽는다).
"""

import json
import re
import sys
from collections import Counter

from semgraph import llm
from trlab import tx10_pick as X

PAGES_SAM = 87
CATS = ("num", "unit", "neg", "term", "omit", "heading", "year")


def _load(name):
    p = X.OUT10 / "score" / f"{name}.json"
    return json.load(open(p)) if p.exists() else None


def _f(x, d=4):
    return "–" if x is None else (f"{x:.{d}f}" if isinstance(x, float) else str(x))


def _ms(x):
    return "–" if x is None else f"{x:,.0f}"


def table1():
    out = {}
    flags = _load("flags") or {"by_model": {}, "blocks": {}}
    agree = _load("full-agree")
    rows = []
    for name, d in (("luna", _load(f"full-{X.LUNA}")), ("4.1-mini", _load(f"full-{X.MINI}")),
                    ("luna 같은", {"dist": agree["luna_same300"], "n": agree["n_same"]} if agree else None)):
        if not d:
            continue
        name = f"{name} {d['n']:,}"
        ds = d["dist"]
        model = X.LUNA if name.startswith("luna") else X.MINI
        lab = flags["by_model"].get(model, {})
        blk = flags["blocks"].get(model, {})
        row = {"행": name, "n": d["n"], "걸림": ds["flagged"], "꼴별": ds["by_kind"], "띠별": ds["by_band"], "이슈 0/1/2/3+": ds["issues_hist"],
               "갈래": ds["cats"], "쪽당 중앙값·최대": f"{ds['per_page']['median']} · {ds['per_page']['max']} (쪽 {ds['per_page']['n_pages']})",
               "라벨 real/context/noise": (f"{lab.get('real', 0)}/{lab.get('context', 0)}/{lab.get('noise', 0)} (이슈 {sum(lab.values())}, "
                                          f"블록 real {blk.get('real', 0)}/{blk.get('n', 0)}, real 중 짝 어긋남 {flags.get('pair', {}).get(model, {}).get('real_pair', 0)})")
               if lab and not name.startswith("luna 같은") else "–",
               "지연 ms 중앙값·p90·최대": f"{_ms(ds['elapsed_ms']['median'])} · {_ms(ds['elapsed_ms']['p90'])} · {_ms(ds['elapsed_ms']['max'])}",
               "USD/블록 평균·중앙값": f"{_f(ds['usd']['mean'], 6)} · {_f(ds['usd']['median'], 6)}", "상태": ds["status"], "USD 합": round(ds["usd"]["sum"], 4),
               "지연 합 s": round(ds["elapsed_ms"]["sum"] / 1000, 1)}
        rows.append(row)
    out["rows"] = rows
    if agree:
        out["agree"] = {k: agree[k] for k in ("n_same", "agree", "both_flag", "mini_only", "luna_only")}
    return out


def table2():
    om = _load("omit")
    if not om:
        return None
    t7 = om["trx7"]
    omit = [r for r in t7 if r["cat"] == "omit"]
    others = [r for r in t7 if r["cat"] != "omit"]
    lab = om["labels"]
    full = om["full"]
    rule_only = {"행": "규칙만", "심은 omit /4": f"걸림 {sum(1 for r in omit if r['rule_flag'])}/4 (되묻기 전)",
                 "헛걸림 /34": f"{sum(1 for r in others if r.get('false_alarm_rule'))}/34", "전량 걸림": f"{full['n_flag']}/{full['n_apply']} (꼴 {full['by_kind']} · 띠 {full['by_band']})",
                 "라벨 /40": "–", "되묻기 일치": "–", "호출·USD": "0 · 0"}
    hits = Counter(r["hit"] for r in omit)
    reask_fa = sum(1 for r in others if r.get("false_alarm"))
    with_reask = {"행": "규칙 + 되묻기", "심은 omit /4": f"span {hits.get('span', 0)} · block_only {hits.get('block_only', 0)} · miss {hits.get('miss', 0)} /4",
                  "헛걸림 /34": f"{reask_fa}/34 (규칙 걸림 {sum(1 for r in others if r['rule_flag'])} 중 되묻기 missing 비지 않음 {reask_fa})",
                  "전량 걸림": f"되묻기 missing 비지 않음 {full['reask_missing_nonempty']}/{full['n_reask']} (상태 {full['reask_status']})",
                  "라벨 /40": f"missing {lab['labels'].get('missing', 0)}(짝 어긋남 {lab.get('missing_pair', 0)}) · merged {lab['labels'].get('merged', 0)} · split_noise {lab['labels'].get('split_noise', 0)} /{lab['n']}",
                  "되묻기 일치": f"{lab['reask_agree']}/{lab['n']}",
                  "호출·USD": f"{len([r for r in t7 if r.get('reask_status')]) + full['n_reask']} · {sum(r.get('usd', 0) for r in t7) + full['reask_usd']:.4f}"}
    el = sorted(x for x in full["reask_elapsed"] if x)
    return {"rows": [rule_only, with_reask], "trx7_rows": t7, "labels_by_band": lab["by_band"],
            "reask_elapsed": {"median": el[len(el) // 2] if el else None, "max": el[-1] if el else None, "n": len(el)},
            "false_alarm_ids_rule": [r["id"] for r in others if r.get("false_alarm_rule")],
            "false_alarm_ids_reask": [r["id"] for r in others if r.get("false_alarm")]}


def table3():
    rows = []
    for m in (X.MINI, X.LUNA):
        d = _load(f"bundle-{m}")
        if not d:
            continue
        for kind, rs in (("낱개(TRX7)", d["single_rows"]), ("묶음", d["rows"])):
            pl = [r for r in rs if r["cat"] in CATS]
            cl = [r for r in rs if r["cat"] == "clean"]
            by = {c: f"{sum(1 for r in pl if r['cat'] == c and r['hit'] == 'span')}({sum(1 for r in pl if r['cat'] == c and r['hit'] == 'block_only')})"
                  f"/{sum(1 for r in pl if r['cat'] == c)}" for c in CATS}
            if kind == "묶음":
                bs = d["bundles"]
                usd_b = sum(b["usd"] for b in bs) / max(1, len(rs))
                lat_b = sum((b["elapsed_ms"] or 0) for b in bs) / max(1, len(rs))
                extra = {"묶음 수·블록/묶음": f"{len(bs)} · {[b['n_blocks'] for b in bs]}", "block_oob": sum(b["oob"] for b in bs),
                         "parse_failed": sum(1 for b in bs if b["status"] == "parse_failed"),
                         "지연 ms/묶음": [b["elapsed_ms"] for b in bs], "토큰 입력·출력/묶음": [(b["inputTokens"], b["outputTokens"]) for b in bs]}
            else:
                usd_b = sum(r.get("usd", 0) for r in rs) / max(1, len(rs))
                lat_b = None
                extra = {"묶음 수·블록/묶음": "–", "block_oob": "–", "parse_failed": "–"}
            rows.append({"행": f"{kind} {m}", "심은 /24": f"{sum(1 for r in pl if r['hit'] == 'span')} / {sum(1 for r in pl if r['hit'] == 'block_only')} / {len(pl)}",
                         "갈래": by, "헛걸림 /14": f"{sum(1 for r in cl if r['false_alarm'])}/{len(cl)}", "USD/블록": round(usd_b, 6),
                         "지연 ms/블록": None if lat_b is None else round(lat_b), **extra})
    return {"rows": rows}


def table4():
    rows = X.calls()
    real = [r for r in rows if not r.get("dry_run")]
    sites = {s: round(X.site_spent(s, rows), 4) for s in X.SITES}
    by_kind = {}
    for r in real:
        m = X.TAG_RE.match(r["tag"])
        if m:
            k = f"{m.group(1)}/{m.group(2)}"
            d = by_kind.setdefault(k, {"n": 0, "usd": 0.0, "ms": 0, "items": 0})
            d["n"] += 1
            d["usd"] += r.get("costUsd", 0)
            d["ms"] += r.get("elapsed_ms", 0)
            d["items"] += r.get("items", 1)
    book = {}
    n_units = len(X.load_units())
    for k, d in by_kind.items():
        if d["items"]:
            per_block_usd, per_block_ms = d["usd"] / d["items"], d["ms"] / d["items"]
            if k.startswith("full/") or k.startswith("bundle/"):
                book[k] = {"USD/블록": round(per_block_usd, 6), "ms/블록": round(per_block_ms), "sam 87쪽(1,391 블록) USD": round(per_block_usd * n_units, 4),
                           "sam 분(순차)": round(per_block_ms * n_units / 60000, 1), "100쪽 USD": round(per_block_usd * n_units * 100 / PAGES_SAM, 4),
                           "100쪽 분(순차)": round(per_block_ms * n_units * 100 / PAGES_SAM / 60000, 1),
                           "100쪽 분(동시 4, 어림)": round(per_block_ms * n_units * 100 / PAGES_SAM / 60000 / 4, 1)}
    full_luna = [r for r in real if r["tag"].startswith(f"TRX10/full/{X.LUNA}/")]
    pre = full_luna[:X.PRE_N]
    rest = full_luna[X.PRE_N:]
    k, o, src, n = X.calib(X.LUNA, "full", rows, first_n=X.PRE_N)
    est_rest = sum(llm.cost(X.LUNA, int(r["chars"] * k), 0, int(o)) for r in rest)
    fr = (X.OUT10 / "first-run.txt").read_text().splitlines() if (X.OUT10 / "first-run.txt").exists() else []
    gold = (X.GOLD10 / "freeze.txt").read_text().splitlines() if (X.GOLD10 / "freeze.txt").exists() else []
    return {"sites": sites, "total": round(X.total_spent(rows), 4), "caps": X.CAPS10, "total_cap": X.TOTAL_CAP10, "n_calls": len(real),
            "status": dict(Counter(r.get("status") for r in real)), "by_kind": {k: {"n": d["n"], "usd": round(d["usd"], 4), "items": d["items"], "s": round(d["ms"] / 1000)} for k, d in by_kind.items()},
            "book100": book, "first_run": fr[:1] + [l for l in fr if re.match(r"^[0-9a-f]{16} ", l)] + [l for l in fr if l.startswith("# plan")],
            "gold_freeze": gold,
            "pre100": {"n": len(pre), "usd": round(sum(r["costUsd"] for r in pre), 4), "k": round(k, 3), "o": round(o, 1), "src": src,
                       "est_rest_after_100": round(est_rest, 4), "actual_rest": round(sum(r["costUsd"] for r in rest), 4), "n_rest": len(rest)}}


def build():
    return {"table1": table1(), "table2": table2(), "table3": table3(), "table4": table4()}


def md(s):
    L = ["# TRX10 판별 표 (out/trx10/summary.md — tx10_report)", ""]
    t1 = s["table1"]
    L += ["## ① 전량 — 행 × 걸림 /분모 · 꼴별 · 띠별 · 블록당 이슈 · 갈래 · 쪽당 · 라벨 정밀 · 지연 · USD · 상태", "",
          "| 행 | n | 걸림 | 꼴별 | 띠별 | 이슈 0/1/2/3+ | 갈래 | 쪽당 중앙값·최대 | 라벨 real/context/noise | 지연 ms | USD/블록 | 상태 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in t1["rows"]:
        L.append(f"| {r['행']} | {r['n']} | {r['걸림']} | {' · '.join(f'{k} {v}' for k, v in r['꼴별'].items())} | {' · '.join(f'{k} {v}' for k, v in r['띠별'].items())} | "
                 f"{' / '.join(str(v) for v in r['이슈 0/1/2/3+'].values())} | {' · '.join(f'{k} {v}' for k, v in r['갈래'].items())} | {r['쪽당 중앙값·최대']} | "
                 f"{r['라벨 real/context/noise']} | {r['지연 ms 중앙값·p90·최대']} | {r['USD/블록 평균·중앙값']} | {r['상태']} |")
    if t1.get("agree"):
        a = t1["agree"]
        L += ["", f"- 같은 300 에서 걸림 일치 {a['agree']}/{a['n_same']} (둘 다 {a['both_flag']} · 4.1-mini 만 {a['mini_only']} · luna 만 {a['luna_only']})"]
    t2 = s["table2"]
    if t2:
        L += ["", "## ② 누락 규칙 — 행 × 심은 omit /4 · 헛걸림 /34 · 전량 걸림 · 라벨 /40 · 되묻기 일치 · 호출·USD", "",
              "| 행 | 심은 omit /4 | 헛걸림 /34 | 전량 걸림 | 라벨 /40 | 되묻기 일치 | 호출·USD |", "|---|---|---|---|---|---|---|"]
        for r in t2["rows"]:
            L.append("| " + " | ".join(str(r[k]) for k in ("행", "심은 omit /4", "헛걸림 /34", "전량 걸림", "라벨 /40", "되묻기 일치", "호출·USD")) + " |")
        L += ["", f"- 규칙 헛걸림 단위: {t2['false_alarm_ids_rule']} → 되묻기 뒤 남음: {t2['false_alarm_ids_reask']}",
              f"- 전량 되묻기 지연 ms 중앙값 {t2['reask_elapsed']['median']} · 최대 {t2['reask_elapsed']['max']} (n {t2['reask_elapsed']['n']}) · 라벨 띠별 {t2['labels_by_band']}"]
    t3 = s["table3"]
    L += ["", "## ③ 묶음 — 행 × 심은 span / block_only / 24 · 갈래별 span(block_only)/분모 · 헛걸림 /14 · USD/블록 · 지연/블록 · 묶음 · oob · parse_failed", "",
          "| 행 | 심은 /24 | " + " | ".join(CATS) + " | 헛걸림 /14 | USD/블록 | 지연 ms/블록 | 묶음 수·블록/묶음 | oob | parse_failed |", "|---|---|" + "---|" * len(CATS) + "---|---|---|---|---|---|"]
    for r in t3["rows"]:
        L.append(f"| {r['행']} | {r['심은 /24']} | " + " | ".join(r["갈래"][c] for c in CATS) + f" | {r['헛걸림 /14']} | {r['USD/블록']} | {r['지연 ms/블록'] or '–'} | "
                 f"{r['묶음 수·블록/묶음']} | {r['block_oob']} | {r['parse_failed']} |")
    t4 = s["table4"]
    L += ["", "## ④ 자리별 USD · 100쪽 책 · 얼림 · 100 짝 뒤 예상 대 실측", "",
          f"- 자리: {t4['sites']} / 상한 {t4['caps']} · 합 {t4['total']} / {t4['total_cap']} · 실호출 {t4['n_calls']} (상태 {t4['status']})",
          f"- 갈래별: {t4['by_kind']}", "", "| 호출 꼴 | USD/블록 | ms/블록 | sam 87쪽 USD | sam 분(순차) | 100쪽 USD | 100쪽 분(순차) | 100쪽 분(동시 4 어림) |", "|---|---|---|---|---|---|---|---|"]
    for k, b in t4["book100"].items():
        L.append(f"| {k} | {b['USD/블록']} | {b['ms/블록']} | {b['sam 87쪽(1,391 블록) USD']} | {b['sam 분(순차)']} | {b['100쪽 USD']} | {b['100쪽 분(순차)']} | {b['100쪽 분(동시 4, 어림)']} |")
    p = t4["pre100"]
    L += ["", f"- 100 짝 선실측: USD {p['usd']} · 토큰/글자 {p['k']} · 블록당 출력 {p['o']} ({p['src']}) → 나머지 {p['n_rest']} 예상 {p['est_rest_after_100']} 대 실측 {p['actual_rest']}",
          "- 얼림(first-run.txt): " + " · ".join(t4["first_run"]), "- 라벨 얼림(gold-trx10/freeze.txt): " + " · ".join(t4["gold_freeze"])]
    return "\n".join(L) + "\n"


def main():
    s = build()
    X.dump(X.OUT10 / "summary.json", s)
    (X.OUT10 / "summary.md").write_text(md(s))
    print(X.OUT10 / "summary.md")


if __name__ == "__main__":
    main()
