"""TRX5 판별 표 → OUT5/summary.{json,md}. 읽기만(OUT5 · out/trx1 · prework/gold-trx5).

① 불일치: 행 sam·cos·kb·합, 열 Dice(TRX1)·S1·S2·S3·S4 — 걸림 / 라벨된 수 / 진짜 / 정밀 / 재현(분모 진짜 합집합) / 새 진짜 / 호출·USD.
   라벨 = TRX1 sample-inconsistency.json(50) ∪ prework/gold-trx5/incons-labels.json(책·용어 sha16·라벨). 전수 아님.
② span 품질: 항목 중 답 받음 · kind 분포 · 용어집 KR 의 exact 키가 용어집 EN 키와 같은 비율.
③ 지칭: 갈래 × 책 주어 꼴 · we 주어 비율 · 섞인 문단 · rule_ok · 엔진 산출(sam 6쪽) · 재채점 · 1차 엔진 기본값 한 줄.
④ 자리별 USD · 얼림 시각 대 cos·kb 첫 실행 시각 · 모듈 sha.
summary.md 에는 회사 이름이 들 수 있는 글자(span·KR 용어)를 싣지 않는다 — 수만.
명령: python -m trlab.tx5_report
"""

import hashlib
import json
import os
import time
from collections import Counter

from trlab import common
from trlab import span_incons as SI
from trlab import tx5_pick as X

TRUE = "진짜-공식 명칭 흔들림"
STAGES = ("Dice", "S1", "S2", "S3", "S4")


def _sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def labels():
    """{(책, 용어 sha16): 라벨} — TRX1 50 + gold-trx5 라벨."""
    out = {}
    for r in json.load(open(X.OUT1 / "glossary" / "sample-inconsistency.json")):
        out[(r["book"], _sha(r["kr"]))] = r["label"]
    p = X.GOLD5 / "incons-labels.json"
    if p.exists():
        for r in json.load(open(p))["items"]:
            out.setdefault((r["book"], r["sha"]), r["label"])
    return out


def flagged(key):
    """{판: 걸린 용어 sha 집합}."""
    out = {}
    d = json.load(open(X.OUT1 / "glossary" / f"check-{key}-official.json"))
    out["Dice"] = {_sha(f["kr"]) for f in d["flags"]}
    for s in ("S1", "S2", "S3", "S4"):
        p = X.OUT5 / "incons" / f"{key}-{s}.json"
        out[s] = {_sha(f["kr"]) for f in json.load(open(p))["flags"]} if p.exists() else None
    return out


def spend(key):
    rows = X.calls()
    luna = [r for r in rows if not r.get("dry_run") and r.get("tag", "").startswith(f"TRX5/span/{key}/")]
    sol = [r for r in rows if not r.get("dry_run") and r.get("tag", "").startswith(f"TRX5/judge/{key}")]
    return {"luna_calls": len(luna), "luna_usd": round(sum(r["costUsd"] for r in luna), 5),
            "sol_calls": len(sol), "sol_usd": round(sum(r["costUsd"] for r in sol), 5)}


def incons_table():
    lab = labels()
    books = {}
    union_all = set()
    for key in common.BOOKS:
        fl = flagged(key)
        truth = {sha for (b, sha), l in lab.items() if b == key and l == TRUE}
        union = set()
        for s, v in fl.items():
            if v:
                union |= v & truth
        union_all |= {(key, s) for s in union}
        row = {"union_true": len(union), "spend": spend(key), "stages": {}}
        for s in STAGES:
            v = fl[s]
            if v is None:
                row["stages"][s] = None
                continue
            labeled = {x for x in v if (key, x) in lab}
            true = labeled & truth
            row["stages"][s] = {"flags": len(v), "labeled": len(labeled), "true": len(true),
                                "precision": round(len(true) / len(labeled), 3) if labeled else None,
                                "recall": round(len(true) / len(union), 3) if union else None,
                                "new_true": len(true - fl["Dice"]), "unlabeled": len(v - labeled)}
        books[key] = row
    tot = {"union_true": len(union_all), "stages": {}}
    for s in STAGES:
        rs = [books[k]["stages"][s] for k in common.BOOKS if books[k]["stages"][s]]
        if not rs:
            tot["stages"][s] = None
            continue
        f, l, t, n = (sum(r[x] for r in rs) for x in ("flags", "labeled", "true", "new_true"))
        tot["stages"][s] = {"flags": f, "labeled": l, "true": t, "precision": round(t / l, 3) if l else None,
                            "recall": round(t / len(union_all), 3) if union_all else None, "new_true": n,
                            "unlabeled": sum(r["unlabeled"] for r in rs)}
    books["all"] = tot
    return books


def span_quality():
    out = {}
    for key in common.BOOKS:
        rows = X.span_rows(key)
        P = SI.pool(key)
        n_items = len(P["items"])
        kinds = Counter(r["kind"] for r in rows)
        gl = P["gloss"]
        ex = [r for r in rows if r["term"] in gl and r["kind"] == "exact"]
        same = sum(1 for r in ex if r["key"] == SI.norm_key(gl[r["term"]]))
        out[key] = {"items": n_items, "answered": sum(1 for r in rows if r["kind"] != "missing"), "rows": len(rows),
                    "kinds": dict(kinds), "gloss_exact_same_key": f"{same}/{len(ex)}",
                    "gloss_exact_same_ratio": round(same / len(ex), 3) if ex else None}
    return out


def subject_table():
    out = {"books": {}, "engine": {}, "rescore": None, "default_line": None}
    for key in common.BOOKS:
        out["books"][key] = {}
        for rule in X.IT_RULES:
            p = X.OUT5 / "subject" / f"{key}-official-{rule}.json"
            if not p.exists():
                continue
            res = json.load(open(p))["res"]
            para = res["subj"].get("paragraph", {})
            n = sum(para.values())
            refd = n - para.get("none", 0)
            third = para.get("name", 0) + para.get("generic", 0) + para.get("pronoun", 0)
            out["books"][key][rule] = {"para_sentences": n, "we": para.get("we", 0), "name": para.get("name", 0),
                                       "generic": para.get("generic", 0), "pronoun": para.get("pronoun", 0), "none": para.get("none", 0),
                                       "we_share": f"{para.get('we', 0)}/{refd}", "third": third,
                                       "para_mixed": f"{res['para_mixed']}/{res['para_n']}", "rule_ok": f"{res['rule_ok']}/{res['rule_n']}"}
    for m in ("gpt-6-luna", "gpt-6.1-sol", "engine-4.1-mini"):
        out["engine"][m] = {}
        for rule in X.IT_RULES:
            p = X.OUT5 / "subject" / f"sam-{m}-{rule}.json"
            if p.exists():
                res = json.load(open(p))["res"]
                allb = Counter()
                for b in res["subj"].values():
                    allb.update(b)
                out["engine"][m][rule] = {"sentences": sum(allb.values()), **{k: allb.get(k, 0) for k in ("we", "name", "generic", "pronoun", "none")}}
    p = X.OUT5 / "subject" / "rescore.json"
    if p.exists():
        r = json.load(open(p))
        out["rescore"] = {name: {rule: f"{v['ok']}/{v['n']}" for rule, v in d.items()} for name, d in r.items()}
    c = out["books"].get("cos", {})
    if "none" in c and "R2" in c:
        a, b = c["none"], c["R2"]
        out["default_line"] = (f"cos 문단 3인칭(name+generic+pronoun) 대 we: it 없이 {a['third']} 대 {a['we']}"
                               f"(we {round(100 * a['we'] / (a['third'] + a['we']), 1)}%, 분모 {a['third'] + a['we']}) → R2 {b['third']} 대 {b['we']}"
                               f"(we {round(100 * b['we'] / (b['third'] + b['we']), 1)}%, 분모 {b['third'] + b['we']})")
    return out


def sites():
    rows = X.calls()
    out = {s: {"usd": round(X.site_spent(s, rows), 5), "cap": X.CAPS5[s]} for s in X.CAPS5}
    out["total"] = {"usd": round(X.total_spent(rows), 5), "cap": X.TOTAL_CAP5, "calls": sum(1 for r in rows if not r.get("dry_run")),
                    "skipped": dict(Counter(r.get("skip") for r in rows if r.get("dry_run")))}
    fr = X.OUT5 / "first-run.txt"
    out["freeze"] = fr.read_text().splitlines()[:3] if fr.exists() else None
    gf = X.GOLD5 / "freeze.txt"
    out["gold_freeze"] = gf.read_text().splitlines() if gf.exists() else None
    first = {}
    for key in ("kb", "cos"):
        fs = list(X.OUT5.glob(f"span/{key}-[0-9]*.json"))
        first[key] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(min(os.path.getmtime(f) for f in fs))) if fs else None
    out["first_run_after_freeze"] = first
    out["module_sha"] = hashlib.sha256(open(SI.__file__, "rb").read()).hexdigest()[:16]
    return out


def md(S):
    L = ["# TRX5 summary", ""]
    L += ["## ① 불일치 (칸 = 걸림 / 라벨된 / 진짜 / 정밀 / 재현(분모 진짜 합집합) / 새 진짜) — 재현 분모 = TRX1 라벨 50 ∪ span 새 걸림 사후 라벨, 전수 아님", ""]
    L.append("| 책 | 진짜 합집합 | " + " | ".join(STAGES) + " | luna 호출·USD | sol 호출·USD |")
    L.append("|---|---|" + "---|" * len(STAGES) + "---|---|")
    for key, row in S["incons"].items():
        cells = []
        for s in STAGES:
            r = row["stages"][s]
            cells.append("–" if r is None else f"{r['flags']} / {r['labeled']} / {r['true']} / {r['precision']} / {r['recall']} / {r['new_true']}")
        sp = row.get("spend", {})
        L.append(f"| {key} | {row['union_true']} | " + " | ".join(cells)
                 + (f" | {sp['luna_calls']} · {sp['luna_usd']} | {sp['sol_calls']} · {sp['sol_usd']} |" if sp else " | | |"))
    L += ["", "## ② span 품질", "", "| 책 | 항목 | 답 받음 | exact | variant | pronoun | omitted | nospan | missing | 용어집 KR exact 가 용어집 EN 키와 같음 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for key, q in S["span"].items():
        k = q["kinds"]
        L.append(f"| {key} | {q['items']} | {q['answered']} | " + " | ".join(str(k.get(x, 0)) for x in ("exact", "variant", "pronoun", "omitted", "nospan", "missing")) + f" | {q['gloss_exact_same_key']} |")
    L += ["", "## ③ 지칭 — 문단 띠 주어 꼴(분모 문단 문장 수)", "", "| 책 | 갈래 | we | name | generic | pronoun | none | we 주어 비율 | 섞인 문단 | rule_ok |", "|---|---|---|---|---|---|---|---|---|---|"]
    for key, d in S["subject"]["books"].items():
        for rule, r in d.items():
            L.append(f"| {key} | {rule} | {r['we']} | {r['name']} | {r['generic']} | {r['pronoun']} | {r['none']} | {r['we_share']} | {r['para_mixed']} | {r['rule_ok']} |")
    L += ["", "엔진 산출(sam 6쪽, 참고만):", ""]
    for m, d in S["subject"]["engine"].items():
        L.append(f"- {m}: " + " · ".join(f"{rule} we {r['we']}/name {r['name']}/generic {r['generic']}/pronoun {r['pronoun']}/none {r['none']} (n {r['sentences']})" for rule, r in d.items()))
    if S["subject"]["rescore"]:
        L += ["", "재채점: " + json.dumps(S["subject"]["rescore"], ensure_ascii=False)]
    if S["subject"]["default_line"]:
        L += ["", "1차 엔진 기본값 한 줄: " + S["subject"]["default_line"]]
    L += ["", "## ④ 자리별 USD · 얼림", "", "```", json.dumps(S["sites"], ensure_ascii=False, indent=1), "```", ""]
    return "\n".join(L)


def main():
    S = {"incons": incons_table(), "span": span_quality(), "subject": subject_table(), "sites": sites()}
    X.dump(X.OUT5 / "summary.json", S)
    (X.OUT5 / "summary.md").write_text(md(S))
    print(X.OUT5 / "summary.md")
    print(json.dumps(S["incons"]["all"], ensure_ascii=False))
    print(S["sites"]["total"])


if __name__ == "__main__":
    main()
