"""TRX6 판별 표 — out/trx6/runs/<키>-ko-<판>.json 을 모아 OUT6/summary.{json,md}.

표: 판별 표(책 × 판: 잘/덜/잘못 % · 문장 온전율 · 줄 짝 정밀/재현) · 잘못 이음 갈래 표 · 갈래 표(단·쪽 넘김) · 규칙별 고친/망친
(R 대 R-Rk) · P 격자(cos) · G 표 · 부재 표 · 남은 틀림의 꼴(R) · 대조 표본 셋(cos p25 · cos p59 · kb p47). 사람 이름·회사 이름 0.

  python -m trlab.tx6_report
"""

import json
import sys
from collections import Counter

from trlab import tx6_pick
from trlab.tx6_pick import BOOKS, LANG, MODES, OUT6, P_GRID


def load(key, mode):
    p = OUT6 / "runs" / f"{key}-{LANG}-{mode}.json"
    return json.load(open(p)) if p.exists() else None


def pct(x):
    return "—" if x is None else f"{x}"


def t_main(runs):
    rows = ["| 책 (쪽·문단·문장·짝) | 판 | 잘 % | 덜 % | 잘못 % | 문장 % | 짝 정밀 / 재현 |", "|---|---|---|---|---|---|---|"]
    for key in BOOKS:
        for mode in MODES:
            r = runs.get((key, mode))
            if not r:
                continue
            s = r["summary"]
            head = f"{key} ({len(r['pages'])}·{s['paragraphs']}·{s['sentences']}·{s['pairs']['n']})"
            rows.append(f"| {head} | {mode} | {pct(s['good_pct'])} | {pct(s['under_pct'])} | {pct(s['over_pct'])} | "
                        f"{pct(s['sentence_pct'])} | {pct(s['pair_precision'])} / {pct(s['pair_recall'])} |")
    return "\n".join(rows)


def t_over(runs):
    ks = tx6_pick.OVER_KINDS
    rows = ["| 책 | 판 | " + " | ".join(ks) + " |", "|---|---|" + "---|" * len(ks)]
    for key in BOOKS:
        for mode in ("L2", "G0.8", "R", "R7j"):
            r = runs.get((key, mode))
            if r:
                ok = r["summary"]["over_kinds"]
                rows.append(f"| {key} | {mode} | " + " | ".join(str(ok[k]) for k in ks) + " |")
    return "\n".join(rows)


def t_breaks(runs):
    rows = ["| 책 | 정답 단 넘김 짝 | 정답 쪽 넘김 | R7 표시(맞음/헛것) | R7j 가 이은 짝 잘/잘못 |", "|---|---|---|---|---|"]
    for key in BOOKS:
        r, j = runs.get((key, "R")), runs.get((key, "R7j"))
        if not r:
            continue
        gold = r["summary"]["breaks"]
        gold_pairs = {(b["a"], b["b"]) for pg in r["pages"] for b in pg["score"]["breaks"]}
        flagged = [(f["a"], f["b"]) for pg in r["pages"] for f in pg["flags"]]
        hit = sum(f in gold_pairs for f in flagged)
        jb = j["summary"]["breaks"]["col_break_joined"] if j else 0
        jover = j["summary"]["over_kinds"]["단 넘김"] if j else 0
        rows.append(f"| {key} | {gold['col_break']} | {gold['page_break']} | {len(flagged)} ({hit}/{len(flagged) - hit}) | {jb} / {jover}(문단) |")
    return "\n".join(rows)


def verdicts(r):
    return {p["id"]: p["verdict"] for pg in r["pages"] for p in pg["score"]["paras"]}


def t_rules(runs):
    rows = ["| 책 | 규칙 | 끄면 고쳐짐(잘못→잘) | 끄면 망가짐(잘→틀림) | 문단 id |", "|---|---|---|---|---|"]
    for key in BOOKS:
        base = runs.get((key, "R"))
        if not base:
            continue
        vb = verdicts(base)
        for k in ("R1", "R2", "R3", "R4", "R5", "R6"):
            r = runs.get((key, f"R-{k}"))
            if not r:
                continue
            v = verdicts(r)
            fixed = [i for i in vb if vb[i] != "good" and v.get(i) == "good"]
            broke = [i for i in vb if vb[i] == "good" and v.get(i) != "good"]
            rows.append(f"| {key} | {k} | {len(fixed)} | {len(broke)} | {' '.join(fixed[:6])}{' / ' if fixed and broke else ''}{' '.join(broke[:6])} |")
    return "\n".join(rows)


def t_grid(runs, key="cos"):
    rows = ["| P | 잘 % | 덜 % | 잘못 % | 문장 % |", "|---|---|---|---|---|"]
    for p in P_GRID:
        r = runs.get((key, f"P{p}"))
        if r:
            s = r["summary"]
            rows.append(f"| {p} | {s['good_pct']} | {s['under_pct']} | {s['over_pct']} | {s['sentence_pct']} |")
    return "\n".join(rows)


def t_g(runs):
    rows = ["| g | " + " | ".join(f"{k} 잘/덜/잘못 %" for k in BOOKS) + " |", "|---|" + "---|" * len(BOOKS)]
    for g in ("0.65", "0.8", "1.0", "1.2"):
        cells = []
        for key in BOOKS:
            r = runs.get((key, f"G{g}"))
            s = r["summary"] if r else None
            cells.append(f"{s['good_pct']} / {s['under_pct']} / {s['over_pct']}" if s else "—")
        rows.append(f"| {g} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def t_absent(runs):
    rows = ["| 책 | 쪽 상태 | out_of_block 줄 / 전체(L2) | 정답 other 수 |", "|---|---|---|---|"]
    for key in BOOKS:
        r = runs.get((key, "L2"))
        if not r:
            continue
        g = json.load(open(tx6_pick.GOLD6 / "para" / f"{key}-{LANG}.json"))
        rows.append(f"| {key} | {r['summary']['page_status']} | {r['summary']['out_of_block']} / {r['summary']['lines']} | {g['how']['kinds']['other']} |")
    return "\n".join(rows)


def t_remaining(runs):
    out = []
    for key in BOOKS:
        r = runs.get((key, "R"))
        if not r:
            continue
        for pg in r["pages"]:
            for p in pg["score"]["paras"]:
                if p["verdict"] == "good":
                    continue
                S = p["lines"]
                cuts = [f"{a}>{b}:{'+'.join(pg['cut_by'][f'{a}>{b}'])}" for a, b in zip(S, S[1:]) if pg["cut_by"].get(f"{a}>{b}")]
                why = f"조각 {p.get('pieces')} — {' '.join(cuts)[:120]}" if p["verdict"] == "under" else f"{p['kind']} 밖 줄 {' '.join(p['extra'][:6])}"
                out.append(f"- {key} p{pg['page']} `{p['id']}` {S[0]}~{S[-1]} **{p['verdict']}** {why}")
    return "\n".join(out)


def t_samples(runs):
    out = []
    # ① cos p25: L2 블록 대 R 묶음 — 정답 문단 둘(p25-g5 L14~L29 · p25-g7 L32~L42) + 이슈 566 짝 L33/L34
    for key, pno, want in (("cos", 25, ("L14", "L29", "L32", "L33", "L34", "L42", "L43", "L49")),
                           ("cos", 59, ("L9", "L12", "L27", "L31", "L41", "L8", "L26", "L30", "L47", "L75", "L86", "L80", "L87")),
                           ("kb", 47, ("L10", "L16", "L9", "L59", "L69", "L73"))):
        l2, r = runs.get((key, "L2")), runs.get((key, "R"))
        if not l2 or not r:
            continue
        p2 = next(pg for pg in l2["pages"] if pg["page"] == pno)
        pr = next(pg for pg in r["pages"] if pg["page"] == pno)
        out.append(f"### {key} p{pno} — 렌더 `out/trx6/render/{key}-ko/p{pno:04d}-lines.png`, 줄 `out/trx6/lines/{key}-ko-p{pno}.json`")
        out.append("| 줄 | L2 블록 | R 묶음 | R6 갈래 |\n|---|---|---|---|")
        for lid in want:
            out.append(f"| {lid} | {p2['map'].get(lid)} | {pr['groups'].get(lid)} | {pr['kinds'].get(lid) or ''} |")
        out.append(f"R 의 단 넘김 표시: {pr['flags']} · 이 쪽 R 판정: " + ", ".join(f"{p['id']} {p['verdict']}" for p in pr["score"]["paras"]))
        out.append("")
    return "\n".join(out)


def main(argv):
    runs = {}
    for key in BOOKS:
        for mode in list(MODES) + [f"P{p}" for p in P_GRID]:
            r = load(key, mode)
            if r:
                runs[(key, mode)] = r
    th = runs[("cos", "R")]["th"]
    md = [
        "# TRX6 판별 표 (out/trx6/summary.md)", "", f"th(얼림): `{json.dumps(th, sort_keys=True)}`", "",
        "## 판별 표 (분모: 정답 본문 문단 · 문장 · 둘 다 본문인 줄 짝)", t_main(runs), "",
        "## 잘못 이음 갈래 표", t_over(runs), "",
        "## 갈래 표 — 단 넘김·쪽 넘김", t_breaks(runs), "",
        "## 규칙별 고친/망친 (R 대 R−Rk)", t_rules(runs), "",
        "## P 격자 (cos, R2 의 절대 P — R2r 은 그대로)", t_grid(runs), "",
        "## G 표 (파서 상수 흉내, 잘/덜/잘못 %)", t_g(runs), "",
        "## 부재 표", t_absent(runs), "",
        "## 남은 틀림의 꼴 (R)", t_remaining(runs), "",
        "## 대조 표본 셋", t_samples(runs),
    ]
    (OUT6 / "summary.md").write_text("\n".join(md))
    json.dump({f"{k}-{m}": r["summary"] for (k, m), r in runs.items()}, open(OUT6 / "summary.json", "w"),
              ensure_ascii=False, indent=1, sort_keys=True)
    print(OUT6 / "summary.md", len(runs), "판")


if __name__ == "__main__":
    main(sys.argv[1:])
