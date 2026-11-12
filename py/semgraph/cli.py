"""SG1 의미 관계 그래프 실험. py/ 에서:

  set -a; . ~/.config/marcspon-openai.env; set +a
  uv run --no-project --python 3.12 --with openai==3.26.0 --with numpy==2.5.4 python -m semgraph.cli <명령> [--out ../out/semgraph]

명령: embed · pool · review · label · eval
"""

import argparse
import json
import sys
from pathlib import Path

from . import lexical, llm, retrieve
from .codeedges import code_edges
from .units import load, structure_edges

DOC = "../out/docparse-samsung/document.json"


def _ctx(a):
    doc, units = load(a.doc)
    queries = json.load(open(Path(a.out) / "queries.json"))
    return doc, units, queries


def _vecs(a):
    import numpy as np
    z = np.load(Path(a.out) / "emb.npz")
    u, q = z["units"], z["queries"]
    u = u / np.linalg.norm(u, axis=1, keepdims=True)
    q = q / np.linalg.norm(q, axis=1, keepdims=True)
    return u, q


def cmd_embed(a):
    import numpy as np
    _, units, queries = _ctx(a)
    u = llm.embed([x.text for x in units], a.out, "units", a.dry_run)
    q = llm.embed([x["text"] for x in queries], a.out, "queries", a.dry_run)
    if not a.dry_run:
        np.savez(Path(a.out) / "emb.npz", units=np.array(u, dtype="float32"), queries=np.array(q, dtype="float32"),
                 ids=np.array([x.id for x in units]))


def cmd_enrich(a):
    import numpy as np
    from .enrich import enriched_texts
    doc, units, _ = _ctx(a)
    e = llm.embed(enriched_texts(doc, units), a.out, "units-enriched", a.dry_run)
    if not a.dry_run:
        np.save(Path(a.out) / "emb-enriched.npy", np.array(e, dtype="float32"))


def cmd_tag(a):
    from .facts import cmd_tag as f
    doc, units, _ = _ctx(a)
    f(doc, units, a.out, a.model, a.dry_run)


def cmd_canon(a):
    from .facts import cmd_canon as f
    _, units, _ = _ctx(a)
    f(units, a.out, a.model)


def cmd_qmap(a):
    from .facts import cmd_qmap as f
    _, _, queries = _ctx(a)
    f(queries, a.out, a.model)


def base_orders(units, queries, uv, qv, texts=None):
    """질의마다 ⓐⓑⓒ 순서(단위 색인 목록)와 ⓒ 점수. 그린워싱 질의는 자기 블록을 뺀다. texts = 색인할 글(기본은 블록 글)."""
    bm = lexical.BM25(texts or [x.text for x in units])
    ids = [x.id for x in units]
    out = []
    for qi, q in enumerate(queries):
        drop = {ids.index(q["self"])} if q.get("self") else set()
        va = [i for i in retrieve.ranks(list(uv @ qv[qi])) if i not in drop]
        vb = [i for i in retrieve.ranks(bm.scores(q["text"])) if i not in drop]
        cs = retrieve.rrf(va, vb)
        vc = sorted(cs, key=lambda i: -cs[i])
        out.append({"a": va, "b": vb, "c": vc, "c_scores": cs})
    return out


def cmd_pool(a):
    _, units, queries = _ctx(a)
    uv, qv = _vecs(a)
    U = {x.id: x for x in units}
    orders = base_orders(units, queries, uv, qv)
    pool = []
    for q, o in zip(queries, orders):
        cand = []
        for key in ("a", "b", "c"):
            for i in o[key][:20]:
                if units[i].id not in cand:
                    cand.append(units[i].id)
        pool.append({"id": q["id"], "candidates": cand})
    json.dump(pool, open(Path(a.out) / "pool.json", "w"), ensure_ascii=False, indent=1)
    print("후보 수", [len(p["candidates"]) for p in pool])


def cmd_review(a):
    from .review import build
    _, units, queries = _ctx(a)
    draft = {}
    for part in ("D", "G"):
        draft.update(json.load(open(Path(a.out) / f"gold-draft-{part}.json")))
    pool = {p["id"]: p["candidates"] for p in json.load(open(Path(a.out) / "pool.json"))}
    html = build(units, queries, draft, pool)
    (Path(a.out) / "review.html").write_text(html)
    print(Path(a.out).resolve() / "review.html")


def cmd_label(a):
    from .label import run
    _, units, _ = _ctx(a)
    run(units, a.out, a.model, a.sections, a.dry_run)


def ai_edges(out, model):
    """{섹션: [(가, 나, 이름)]} — 호출은 했으나 관계 0 인 섹션도 빈 목록으로."""
    p = Path(out) / f"ai-edges-{model}.jsonl"
    by = {}
    if p.exists():
        for line in open(p):
            e = json.loads(line)
            by.setdefault(e["section"], [])
            if e["rel"] != "none":
                by[e["section"]].append((e["src"], e["dst"], e["rel"]))
    return by


def all_edges(doc, units, out, model):
    edges = structure_edges(doc, units) + code_edges(units)
    return edges, [e for es in ai_edges(out, model).values() for e in es]


def cmd_eval(a):
    from .evaluate import run
    doc, units, queries = _ctx(a)
    uv, qv = _vecs(a)
    gold = json.load(open(a.gold))
    from .evaluate import AI_SEM, CODE_SEM
    edges, ai = all_edges(doc, units, a.out, a.model)
    # sol 표본 섹션은 sol 관계로 갈아 끼운 갈래 하나 더(관계 품질이 검색에 주는 차이)
    luna, sol = ai_edges(a.out, a.model), ai_edges(a.out, "gpt-6.1-sol")
    extra = {}
    if sol:
        mixed = [e for s, es in luna.items() if s not in sol for e in es] + [e for es in sol.values() for e in es]
        from . import retrieve
        extra["d 구조+의미 전부 (sol 10섹션 갈음)"] = (edges + mixed, set(retrieve.STRUCT_W) | CODE_SEM | AI_SEM)
    # 코드 의미 v2(관계 표본 채점 뒤 손질, 검색 채점 전에 고정)
    from . import retrieve
    from .codeedges import code_edges_v2
    v2 = structure_edges(doc, units) + code_edges_v2(units)
    extra["d 구조+코드의미v2"] = (v2, set(retrieve.STRUCT_W) | CODE_SEM)
    extra["d 구조+코드의미v2+AI"] = (v2 + ai, set(retrieve.STRUCT_W) | CODE_SEM | AI_SEM)
    orders = base_orders(units, queries, uv, qv)
    # ⓔ 파싱 보강 색인: 이름표 붙인 글로 ⓐⓑⓒ 를 다시 — ⓔ 와 ⓔ+그래프는 run 이 c_scores 를 갈아 끼워 셈
    import numpy as np
    from .enrich import enriched_texts
    p = Path(a.out) / "emb-enriched.npy"
    enr = None
    if p.exists():
        ue = np.load(p)
        ue = ue / np.linalg.norm(ue, axis=1, keepdims=True)
        enr = base_orders(units, queries, ue, qv, enriched_texts(doc, units))
    more = None
    if enr and (Path(a.out) / "facts" / "qmap.json").exists():
        from .facts import entity_lists
        g0 = retrieve.Graph(structure_edges(doc, units), dict(retrieve.STRUCT_W))
        more = entity_lists(units, queries, a.out, enr, lambda cs: retrieve.expand(cs, [u.id for u in units], g0))
    run(units, queries, gold, orders, edges, ai, a.out, extra, enr, more)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["embed", "enrich", "tag", "canon", "qmap", "pool", "review", "label", "eval"])
    p.add_argument("--doc", default=DOC)
    p.add_argument("--out", default="../out/semgraph")
    p.add_argument("--model", default="gpt-6-luna")
    p.add_argument("--sections", default="all")
    p.add_argument("--gold", default="../out/semgraph/gold.json")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    globals()[f"cmd_{a.cmd}"](a)


if __name__ == "__main__":
    sys.exit(main())
