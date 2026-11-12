"""SG1d 채점 — 질의별(거시) 평균 · 짝 부트스트랩.

질의마다: 재현@10·@20(핵심) · 정밀@10(핵심+보조) · 글자 예산 재현(4천·8천·1.6만·3.2만 자, 단락 글 전부를 셈) ·
핵심 90% 까지 든 글자(작을수록 덜 읽음, 못 채우면 문서 전체 글자). 핵심이 없는 질의는 뺀다.
기준 둘(P-c · P-s)에 대해 갈래마다 차이의 95% 구간(5,000번 다시 뽑기)과 이긴·진 질의 수.
질의 때 AI 비용·시간은 sg1d/ 의 qx·rerank·qmap2 에서.
산출 <out>/eval2.json · <out>/eval2.md
"""

import json
import math
from pathlib import Path

import numpy as np

KS = (10, 20)
BUDGETS = (4000, 8000, 16000, 32000)
REFS = ("P-c 벡터+BM25", "P-s P-c+구조")
B = 5000


def per_query(order, core, rel, L, total):
    top = order[:max(KS)]
    m = {f"재현@{k}": len(core & set(top[:k])) / len(core) for k in KS}
    m["정밀@10"] = len(rel & set(top[:10])) / 10
    used, got, need, mc = 0, set(), math.ceil(0.9 * len(core)), None
    bud = {x: None for x in BUDGETS}
    for p in order:
        used += L[p]
        for x in BUDGETS:
            if bud[x] is None and used > x:
                bud[x] = len(got) / len(core)
        if p in core:
            got.add(p)
        if mc is None and len(got) >= need:
            mc = used
        if mc is not None and all(v is not None for v in bud.values()):
            break
    for x in BUDGETS:
        m[f"{x // 1000}천자" if x < 10000 else f"{x / 10000:g}만자"] = bud[x] if bud[x] is not None else len(got) / len(core)
    m["90%까지 글자"] = mc if mc is not None else total
    return m


def boot(d, rng):
    d = np.asarray(d, dtype=float)
    s = rng.integers(0, len(d), (B, len(d)))
    means = d[s].mean(1)
    return float(d.mean()), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def run(b):
    from .sg1d import WITH_LLM, ZERO
    orders = json.load(open(b.d / "orders.json"))
    L = {p.id: len(p.text) for p in b.ps}
    total = sum(L.values())
    arms = [a for a in ZERO + WITH_LLM if a in next(iter(orders.values()))]
    rows = {}  # 갈래 → 질의 → 지표
    qtype = {}
    for q in b.queries:
        g = b.gold.get(q["id"], [])
        core = {x["id"] for x in g if x["label"] == "core"} - {q.get("self_passage")}
        rel = {x["id"] for x in g} - {q.get("self_passage")}
        if not core:
            continue
        qtype[q["id"]] = q["type"]
        for a in arms:
            rows.setdefault(a, {})[q["id"]] = per_query(orders[q["id"]][a], core, rel, L, total)
    # 질의 때 AI 비용
    cost = {a: {"usd": 0.0, "sec": 0.0} for a in arms}
    for a, f, key in (("Q-x 질의 확장", "qx.json", "res"), ("Q-r 재순위(P-ts 상위 50)", "rerank.json", None),
                      ("Q-f 지표 마디 먼저(+P-ts)", "qmap2.json", None)):
        p = b.d / f
        if a in cost and p.exists():
            r = json.load(open(p))
            r = r[key] if key else r
            n = max(1, len(r))
            cost[a] = {"usd": sum(x["usd"] for x in r.values()) / n, "sec": sum(x["sec"] for x in r.values()) / n}
    metrics = list(next(iter(rows[arms[0]].values())))
    rng = np.random.default_rng(0)
    res = {}
    for scope in ("전체", "drafting", "greenwash"):
        qs = [q for q in qtype if scope == "전체" or qtype[q] == scope]
        if not qs:
            continue
        sc = res[scope] = {"n": len(qs), "arms": {}}
        for a in arms:
            ent = {"mean": {m: float(np.mean([rows[a][q][m] for q in qs])) for m in metrics},
                   "median_90": float(np.median([rows[a][q]["90%까지 글자"] for q in qs])), "vs": {}}
            for ref in REFS:
                if ref == a or ref not in rows:
                    continue
                ent["vs"][ref] = {}
                for m in metrics:
                    d = [rows[a][q][m] - rows[ref][q][m] for q in qs]
                    mean, lo, hi = boot(d, rng)
                    ent["vs"][ref][m] = {"diff": mean, "lo": lo, "hi": hi, "win": sum(x > 0 for x in d),
                                         "loss": sum(x < 0 for x in d)}
            ent["query_llm"] = cost[a]
            sc["arms"][a] = ent
    json.dump({"book": b.name, "total_chars": total, "per_query": rows, "summary": res}, open(Path(b.out) / "eval2.json", "w"),
              ensure_ascii=False, indent=1)
    md = [f"# SG1d 채점 — {b.name} (단락 {len(b.ps)}, 문서 {total:,}자)\n",
          "거시 평균(질의마다 잰 뒤 평균). 구간 = P-s 대비 차이의 95% 부트스트랩, 이김/짐 = 질의 수. 「90% 글자」는 중앙값(작을수록 덜 읽음; 차이는 음수가 좋음).\n"]
    show = ["재현@10", "재현@20", "정밀@10", "8천자", "1.6만자", "90%까지 글자"]
    for scope, sc in res.items():
        md += [f"\n## {scope} (질의 {sc['n']})\n",
               "| 묶음 | 갈래 | 재현@10 | 재현@20 | 정밀@10 | 4천자 | 8천자 | 1.6만자 | 3.2만자 | 90% 글자(중앙) | 질의 때 AI USD/질의 · 초 |",
               "|---|---|---|---|---|---|---|---|---|---|---|"]
        for a, e in sc["arms"].items():
            m = e["mean"]
            grp = "AI 0" if a in ZERO else "AI 씀"
            c = e["query_llm"]
            md.append(f"| {grp} | {a} | {m['재현@10']:.1%} | {m['재현@20']:.1%} | {m['정밀@10']:.2f} | {m['4천자']:.1%} | "
                      f"{m['8천자']:.1%} | {m['1.6만자']:.1%} | {m['3.2만자']:.1%} | {e['median_90']:,.0f} | "
                      + (f"{c['usd']:.4f} · {c['sec']:.1f}" if c["usd"] else "0") + " |")
        for ref in REFS:
            md += [f"\n{scope} — **{ref} 대비** 차이 [95% 구간] 이김/짐\n",
                   "| 갈래 | " + " | ".join(show) + " |", "|---|" + "---|" * len(show)]
            for a, e in sc["arms"].items():
                v = e["vs"].get(ref)
                if not v:
                    continue
                cells = []
                for m in show:
                    x = v[m]
                    f = (lambda t: f"{t:+,.0f}") if m == "90%까지 글자" else (lambda t: f"{t * 100:+.1f}")
                    star = " ✓" if (x["lo"] > 0 or x["hi"] < 0) else ""
                    cells.append(f"{f(x['diff'])} [{f(x['lo'])}, {f(x['hi'])}] {x['win']}/{x['loss']}{star}")
                md.append(f"| {a} | " + " | ".join(cells) + " |")
    md.append("\n✓ = 구간이 0 을 벗어남. 재현·정밀 차이는 %p.")
    (Path(b.out) / "eval2.md").write_text("\n".join(md))
    print("\n".join(md[:2] + [l for l in md if l.startswith("| AI") or l.startswith("## ")]))
