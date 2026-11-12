"""채점. 정답(gold.json, 사용자 확인 뒤 얼린 것)에 대해 갈래마다 재현율@5·10·20(핵심), 정밀도@10(핵심+보조),
「맥락 근거」(핵심 중 ⓐ·ⓑ 둘 다 50위 밖) 잡은 수, 질의당 시간. 넓힌 갈래가 정답 밖에서 올린 상위 10은 판정용으로 따로 쓴다.
산출 <out>/eval.json · <out>/eval.md · <out>/extra-to-judge.md
"""

import json
import time
from pathlib import Path

from . import retrieve

KS = (5, 10, 20)
BUDGETS = (4000, 8000, 16000, 32000)  # AI 에 넣을 글자 수 예산
CODE_SEM = {"mentions_cell", "page_ref", "same_metric", "scopes"}
AI_SEM = {"supports", "target_of", "assures", "explains", "scopes_ai", "same_metric_ai"}


def _weights(keep):
    w = {**retrieve.STRUCT_W, **retrieve.SEM_W}
    return {k: v for k, v in w.items() if k in keep}


def variants(edges, ai):
    struct = set(retrieve.STRUCT_W)
    allr = struct | CODE_SEM | AI_SEM
    v = {
        "d0 구조만": (edges, struct),
        "d 구조+코드의미": (edges, struct | CODE_SEM),
        "d 구조+AI의미": (edges + ai, struct | AI_SEM),
        "d 구조+의미 전부": (edges + ai, allr),
        "d 의미만(구조 없음)": (edges + ai, CODE_SEM | AI_SEM),
    }
    for r in sorted(CODE_SEM | AI_SEM):
        v[f"d 전부 − {r}"] = (edges + ai, allr - {r})
    return v


def run(units, queries, gold, orders, edges, ai, out, extra_variants=None, enr=None, more=None):
    ids = [u.id for u in units]
    idx = {u: i for i, u in enumerate(ids)}
    U = {u.id: u for u in units}
    rows = []
    var = variants(edges, ai)
    if extra_variants:
        var.update(extra_variants)
    graphs = {name: retrieve.Graph(e, _weights(keep)) for name, (e, keep) in var.items()}
    settings = [(name, 2, retrieve.LAMBDA) for name in graphs]
    settings += [("d 구조+의미 전부", 1, retrieve.LAMBDA), ("d0 구조만", 1, retrieve.LAMBDA)]
    settings += [("d 구조+의미 전부", 2, lam) for lam in (0.25, 1.0)] + [("d0 구조만", 2, lam) for lam in (0.25, 1.0)]
    results = {}
    extra = {}
    for qi, (q, o) in enumerate(zip(queries, orders)):
        g = gold.get(q["id"], [])
        core = {x["id"] for x in g if x["label"] == "core" and x["id"] in idx}
        rel = {x["id"] for x in g if x["id"] in idx}
        ra = {ids[i]: r for r, i in enumerate(o["a"])}
        rb = {ids[i]: r for r, i in enumerate(o["b"])}
        hard = {c for c in core if ra.get(c, 9999) >= 50 and rb.get(c, 9999) >= 50}
        lists = {"a 벡터": o["a"], "b BM25": o["b"], "c 벡터+BM25": o["c"]}
        times = {}
        for name, hops, lam in settings:
            key = name if (hops, lam) == (2, retrieve.LAMBDA) else f"{name} [걸음 {hops}, λ {lam}]"
            t0 = time.perf_counter()
            lists[key] = retrieve.expand(o["c_scores"], ids, graphs[name], hops, lam)
            times[key] = time.perf_counter() - t0
        if enr:
            e = enr[qi]
            lists["e 보강색인 벡터"] = e["a"]
            lists["e 보강색인 벡터+BM25"] = e["c"]
            for name in ("d0 구조만", "d 구조+AI의미", "d 구조+코드의미v2+AI"):
                if name in graphs:
                    lists[f"e+{name}"] = retrieve.expand(e["c_scores"], ids, graphs[name], 2, retrieve.LAMBDA)
        if more:
            lists.update({k: v for k, v in more[qi].items() if not k.startswith("_")})
        for key, order in lists.items():
            top = [ids[i] for i in order[:max(KS)]]
            within = {}
            used, got = 0, []
            for i in order:
                used += len(U[ids[i]].text)
                for b in BUDGETS:
                    if used <= b:
                        within.setdefault(b, set()).add(ids[i])
                if used > max(BUDGETS):
                    break
            r = results.setdefault(key, {"type": {}, "hits": {k: 0 for k in KS}, "core": 0, "p10": 0.0, "hard": 0,
                                         "hard_hit": 0, "n": 0, "sec": 0.0, "bud": {b: 0 for b in BUDGETS}})
            for bucket in (r, r["type"].setdefault(q["type"], {"hits": {k: 0 for k in KS}, "core": 0, "p10": 0.0,
                                                                "hard": 0, "hard_hit": 0, "n": 0,
                                                                "bud": {b: 0 for b in BUDGETS}})):
                for b in BUDGETS:
                    bucket["bud"][b] += len(core & within.get(b, set()))
                for k in KS:
                    bucket["hits"][k] += len(core & set(top[:k]))
                bucket["core"] += len(core)
                bucket["p10"] += len(rel & set(top[:10])) / 10
                bucket["hard"] += len(hard)
                bucket["hard_hit"] += len(hard & set(top[:20]))
                bucket["n"] += 1
            r["sec"] += times.get(key, 0.0)
            if key == "d 구조+의미 전부":
                pool_seen = {ids[i] for k2 in ("a", "b", "c") for i in o[k2][:20]}
                extra[q["id"]] = [x for x in top[:10] if x not in rel and x not in pool_seen]
    json.dump(results, open(Path(out) / "eval.json", "w"), ensure_ascii=False, indent=1)

    def line(name, r):
        n = max(1, r["core"])
        return (f"| {name} | " + " | ".join(f"{r['hits'][k] / n:.1%}" for k in KS)
                + f" | {r['p10'] / max(1, r['n']):.2f} | {r['hard_hit']}/{r['hard']} | "
                + " | ".join(f"{r['bud'][b] / n:.1%}" for b in BUDGETS) + " |")

    md = []
    for scope in ("전체", "drafting", "greenwash"):
        md += [f"\n### {scope}\n", "| 갈래 | 재현@5 | 재현@10 | 재현@20 | 정밀@10 | 맥락 근거 @20 | 4천자 | 8천자 | 1.6만자 | 3.2만자 |",
               "|---|---|---|---|---|---|---|---|---|---|"]
        for name, r in results.items():
            md.append(line(name, r if scope == "전체" else r["type"].get(scope, r)))
    md.append("\n질의당 넓힘 시간(ms): " + ", ".join(f"{k} {1000 * r['sec'] / max(1, r['n']):.1f}"
                                            for k, r in results.items() if r["sec"]))
    (Path(out) / "eval.md").write_text("\n".join(md))
    with open(Path(out) / "extra-to-judge.md", "w") as f:
        for q in queries:
            f.write(f"## {q['id']} {q['text']}\n")
            for x in extra.get(q["id"], []):
                f.write(f"- {x} (p{U[x].page} {U[x].kind}) {U[x].text.replace(chr(10), ' / ')[:300]}\n")
            f.write("\n")
    print("\n".join(md))
