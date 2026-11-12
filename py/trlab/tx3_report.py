"""TRX3 표 — H0 재셈 · H1 덮음 · H5 세 규칙 · H2 거스름 · 판별 · 흔들림 · 방법별 고친 항목 · 자리별 USD.

명령: python -m trlab.tx3_report → OUT3/summary.json + OUT3/summary.md
"""

import json
import sys
from collections import Counter

from trlab import tx3_pick as T

MODES = ["c3", "d3:one", "d3:vote", "s3:one", "s3:vote", "h3", "A1", "A2", "A5", "best"]


def tag(key):
    return {"nb2": "블라인드", "sam": "개발"}.get(key, "반쯤 본")


def baseline(key, lang):
    """TRX2 (d) 를 A∨B 로 — 7권은 h0, nb2 는 out/trx3/g2."""
    if key == "nb2":
        r = json.load(open(T.OUT3 / "g2" / f"{key}-{lang}-d.json"))
        return T.score_ab(r["score"]["rows"], T.gold_for(key, lang))
    h0 = json.load(open(T.OUT3 / "h0.json"))[f"{key}-{lang}-d"]
    return {"rows": h0["rows"], **{k: {"pct": h0[k]} for k in ("found", "start_ab", "end_ab", "block", "divider_start_ab")},
            "wrong_start": h0["wrong_start"]}


def mode_grade(key, lang, m):
    name, _, part = m.partition(":")
    r = T.load_run(key, lang, name)
    if not r or r.get("status") not in ("ok", "partial"):
        return None, None
    if part:
        return r[part]["grade"], r[part]["pred"]
    return r["grade"], r["pred"]


def src_of(grade, pred):
    """정답 n → 산출 항목 src."""
    out = {}
    for row in grade["match"]:
        if row.get("found"):
            it = next((x for x in pred["items"] if x["text"] == row["pred"]["text"]), None)
            out[row["n"]] = it.get("src") if it else None
    return out


def h5_table(key, lang):
    from trlab import cands as C
    gold = T.gold_for(key, lang)
    D = {g["start_page"] for g in gold["items"] if g.get("divider")}
    blocks = T.load_blocks3(key, lang)
    attrs = T.load_json(key, lang, "attrs")
    out = {}
    for rule in ("trx2", "ratio", "union"):
        P = {p for p, v in C.build(blocks, attrs, divider_rule=rule)["pages"].items() if v["divider"]}
        out[rule] = {"pred": len(P), "hit": len(P & D), "D": len(D), "false": len(P - D),
                     "recall": round(len(P & D) / len(D), 3) if D else None,
                     "precision": round(len(P & D) / len(P), 3) if P and D else None}
    return out


def build():
    S = {"h0": {}, "h1": {}, "h5": {}, "h2": {}, "modes": {}, "shaky": {}, "fixed": {}, "usd": {}}
    h0 = json.load(open(T.OUT3 / "h0.json"))
    S["h0"] = {k: {x: v[x] for x in ("found", "start_ab", "end_ab", "block", "divider_start_ab", "wrong_start")}
               for k, v in h0.items()}
    for key, lang in T.BOOKS8:
        k = f"{key}-{lang}"
        pm = json.load(open(T.OUT3 / "pagemap" / f"{k}.json"))
        S["h1"][k] = {"cover": pm["cover"], "npages": pm["npages"], "offset": pm["offset"], "trx1": pm["trx1"],
                      "offsets": pm.get("offsets")}
        S["h5"][k] = h5_table(key, lang)
        win = json.load(open(T.OUT3 / "win" / f"{k}.json"))
        S["h2"][k] = {"dp_violations": len(win["dp_violations"]), "win_mean": round(
            sum(len(w) for w in win["windows"]) / len(win["windows"]), 2)}
        for m in ("d3", "s3"):
            r = T.load_run(key, lang, m)
            if r and r.get("status") == "ok":
                S["h2"][k][m] = {"one": r["one"].get("violations"), **r["violations"]}
                S["shaky"][f"{k}-{m}"] = {**r["shaky"], "undecided": len(r["undecided"])}
        base = baseline(key, lang)
        row = {"TRX2 d": {x: base[x]["pct"] for x in ("start_ab", "end_ab", "block", "divider_start_ab")}}
        for m in MODES:
            g, pred = mode_grade(key, lang, m)
            if g is None:
                continue
            name = m.partition(":")[0]
            r = T.load_run(key, lang, name)
            src = Counter(x["src"] for x in pred["items"])
            row[m] = {x: g[x] for x in ("found", "start_ab", "end_ab", "block", "divider_start_ab")}
            row[m].update(fill=src.get("fill", 0) + src.get("dp", 0), undecided=len(r.get("undecided", [])) if ":" in m else None,
                          usd=r.get("usd"), wrong=g["wrong_start"])
        S["modes"][k] = row
        g, pred = mode_grade(key, lang, "best")
        if g is not None:
            fx = T.fixed(base, g)
            src = src_of(g, pred)
            abl = {}
            for a in ("A1", "A2", "A5"):
                ga, _ = mode_grade(key, lang, a)
                if ga:
                    wa = {x["n"] for x in ga["rows"] if not x["start_ab"]}
                    abl[a] = sum(n in wa for n in fx["fixed"])
            S["fixed"][k] = {"fixed": fx["fixed"], "broke": fx["broke"], "src": {n: src.get(n) for n in fx["fixed"]},
                             "rewrong_without": abl, "best_src": dict(Counter(src.values()))}
    rows = T.calls()
    S["usd"] = {"G1": round(T.site_spent("G1", rows), 5), "luna": round(T.site_spent("luna", rows), 5),
                "sol": round(T.site_spent("sol", rows), 5),
                "trx2_d_nb2": round(sum(r["costUsd"] for r in rows if r["tag"].startswith("TRX2/G2/")), 5),
                "total": round(T.total_spent(rows), 5), "calls": len(rows)}
    return S


def pct(v):
    return "–" if v is None else (f"{v:g}" if not isinstance(v, dict) else f"{v.get('pct')}")


def md(S):
    L = ["# TRX3 표", "", "## 판별 — 시작 A∨B / 끝 A∨B / 블록 / 간지 시작 (%)", ""]
    L.append("| 책 | 처지 | " + " | ".join(["TRX2 d"] + MODES) + " |")
    L.append("|" + "---|" * (len(MODES) + 3))
    for k, row in S["modes"].items():
        cells = []
        for m in ["TRX2 d"] + MODES:
            v = row.get(m)
            cells.append("–" if v is None else f"{pct(v['start_ab'])}/{pct(v['end_ab'])}/{pct(v['block'])}/{pct(v['divider_start_ab'])}")
        L.append(f"| {k} | {tag(k.split('-')[0])} | " + " | ".join(cells) + " |")
    L += ["", "## 판별 — 메움(fill+dp)·undecided·USD·시작 틀린 정답 n", "", "| 책 | 판 | 메움 | undecided | USD | 틀림 |", "|---|---|---|---|---|---|"]
    for k, row in S["modes"].items():
        for m in MODES:
            v = row.get(m)
            if v:
                L.append(f"| {k} | {m} | {v['fill']} | {pct(v['undecided'])} | {pct(v['usd'])} | {v['wrong']} |")
    L += ["", "## H0 재셈 (TRX2 판 여섯) — 뽑힘/시작/끝/블록/간지", "", "| 판 | 값 | 시작 틀림 n |", "|---|---|---|"]
    for k, v in S["h0"].items():
        L.append(f"| {k} | {v['found']}/{v['start_ab']}/{v['end_ab']}/{v['block']}/{pct(v['divider_start_ab'])} | {v['wrong_start']} |")
    L += ["", "## H1 덮음", "", "| 책 | 쪽 | direct | interp | offset | TRX1 지도 쪽 | 같은 키 일치 | 어긋남 |", "|---|---|---|---|---|---|---|---|"]
    for k, v in S["h1"].items():
        a = v["trx1"]["agree"]
        L.append(f"| {k} | {v['npages']} | {v['cover']['direct']} | {v['cover']['interp']} | {v['cover']['offset']} | "
                 f"{v['trx1']['pages']} | {a['same']}/{a['keys']} | {v['offsets']} |")
    L += ["", "## H5 간지 판정 (정답 D 대비 맞음/판정, 헛잡음)", "", "| 책 | D | trx2 | ratio | union |", "|---|---|---|---|---|"]
    for k, v in S["h5"].items():
        c = [f"{v[r]['hit']}/{v[r]['pred']} 헛 {v[r]['false']}" for r in ("trx2", "ratio", "union")]
        L.append(f"| {k} | {v['trx2']['D']} | " + " | ".join(c) + " |")
    L += ["", "## H2 거스름", "", "| 책 | DP 거스름 | 창 평균 쪽 | d3 1회차 거스름 | d3 투표 거스름 / 다시 물어 고침 / dp | s3 1회차 | s3 투표 |", "|---|---|---|---|---|---|---|"]
    for k, v in S["h2"].items():
        d, s = v.get("d3", {}), v.get("s3", {})
        L.append(f"| {k} | {v['dp_violations']} | {v['win_mean']} | {pct(d.get('one'))} | {pct(d.get('violations'))} / "
                 f"{pct(d.get('fixed_by_ask'))} / {pct(d.get('to_dp'))} | {pct(s.get('one'))} | {pct(s.get('violations'))} |")
    L += ["", "## 흔들림 (3회 답이 다 같지 않은 항목 / G1 항목)", "", "| 판 | 쪽 | 블록 | undecided |", "|---|---|---|---|"]
    for k, v in S["shaky"].items():
        L.append(f"| {k} | {v['page']}/{v['n']} | {v['block']}/{v['n']} | {v['undecided']} |")
    L += ["", "## 방법별 고친 항목 (TRX2 d 에서 시작 틀림 → best 맞음)", "", "| 책 | 고침 n(src) | 망침 | 빼면 다시 틀림 A1/A2/A5 | best src |", "|---|---|---|---|---|"]
    for k, v in S["fixed"].items():
        L.append(f"| {k} | {[(n, v['src'][n]) for n in v['fixed']]} | {v['broke']} | {v['rewrong_without']} | {v['best_src']} |")
    L += ["", f"## USD: {S['usd']}"]
    return "\n".join(L) + "\n"


def main(argv):
    S = build()
    json.dump(S, open(T.OUT3 / "summary.json", "w"), ensure_ascii=False, indent=1, sort_keys=True, default=str)
    open(T.OUT3 / "summary.md", "w").write(md(S))
    print(md(S))


if __name__ == "__main__":
    main(sys.argv[1:])
