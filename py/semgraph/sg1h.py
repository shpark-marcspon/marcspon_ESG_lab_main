"""SG1h — 새 단위(SG1g) 위에서 갈래+재순위를 다시, 그리고 용어 사전·반대 꼴을 더한 갈래(F-g).

단위·정답·임베딩은 <out>/sg1g/ 그대로(fixunits.New). 후보 = 갈래 상위 15 를 번갈아 ∪ P-ts 상위 50 → 80개, 섹션째 넓히기 → 120개.
- F-b′: 말 목록을 SG1g 단위에서 다시 세우고 갈래도 다시 세운다 → 후보 → luna 재순위.
- F-g1: F-b′ 입력에 기준 사전(glossary STANDARD)과 그린워싱 「반대·한정 사실」 꼴 일곱을 더한다 → 같은 후보·재순위.
- F-g2: F-g1 + 동의어 사전(SYNONYMS). ★ 동의어 예시 일부와 꼴 일곱은 이 세 권의 놓친 근거를 본 뒤 고른 것 — F-g2 는 누설, 이득이 부풀려진다.
재순위: F-b′∪F-g1 후보를 한 번에(갈래마다 제 후보만 그 차례로), F-g2 는 따로 한 번.
채점: 묶음 정답(옛 핵심 하나 = 새 단락 묶음 하나) · 원고 사실 재현 · SG1f 가 놓친 53 의 1.6만 자 안 · 그린워싱 판정(F-g 8천 자, 한 번).
산출 <out>/sg1h/: vocab.txt · plan-b|g1|g2.part.jsonl · emb-facets-*.npy · pool-*.json · rerank-u|g2.part.jsonl · judge-g1|g2.part.jsonl · eval.json
py/ 에서: python -m semgraph.sg1h <stage1|stage2|judge_g2|eval|…> --book samsung|cosmax|kb
"""

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from . import glossary, lexical, llm, retrieve
from .evaluate2 import boot, per_query
from .facets import CAP_B, LIKE, PLAN_INSTR, PLAN_SCHEMA, TOP, _alt, _clean
from .fixunits import New, _graph, _orders, _section_expand, _tagtext, per_query_grouped
from .sg1d import BOOKS, MODEL, RR_INSTR, RR_SCHEMA, Book, _norm, _pid
from .taskeval import COARSE, GW_INSTR, GW_SCHEMA, _keep, _kept, _num, _respond

VOCAB_CHARS = 12000
VARS = ("b", "g1", "g2")
ARM = {"b": "F-b′ 갈래∪P-ts∪섹션 → 재순위", "g1": "F-g1 기준 사전·반대 꼴 → 재순위",
       "g2": "F-g2 기준+동의어 사전·반대 꼴 → 재순위(누설 있음)"}
RR_FILE = {"b": "rerank-u", "g1": "rerank-u", "g2": "rerank-g2"}
PS = "P-s P-c+구조"
FB_OLD = "F-b 갈래∪P-ts → 재순위"

CONTRARY_INSTR = """
그린워싱 주장의 「반대·한정 사실」 갈래는 아래 꼴 가운데 그 주장에 맞는 것마다 키워드를 세워라(보고서 말 목록의 말을 우선):
""" + glossary.contrary_text()


class Ctx:
    def __init__(self, name):
        self.nb, self.ob = New(name), Book(name, "gold-final.json")
        saved = [x["id"] for x in json.load(open(self.nb.d / "passages.json"))]
        assert [p.id for p in self.nb.ps] == saved, "sg1g/passages.json 과 다시 세운 단락이 다르다"
        self.d = Path(self.nb.out) / "sg1h"
        self.d.mkdir(exist_ok=True)
        self.qs = json.load(open(self.nb.d / "queries.json"))
        self.gold = json.load(open(self.nb.d / "gold-final.json"))
        self.P = {p.id: p for p in self.nb.ps}
        self.L = {p.id: len(p.text) for p in self.nb.ps}

    def instr(self, v):
        vocab = (self.d / "vocab.txt").read_text()
        if v == "b":
            return PLAN_INSTR + vocab
        return PLAN_INSTR.replace("[보고서 말 목록]\n", "") + CONTRARY_INSTR + "\n\n[보고서 말 목록]\n" + vocab \
            + "\n\n" + glossary.text(synonyms=(v == "g2"))


# ---- 말 목록(SG1g 단위) ----
def cmd_vocab(c):
    cnt = Counter()
    for s in c.nb.doc["sections"]:
        if _clean(s.get("title")):
            cnt[_clean(s["title"])] += 1
    for u in c.nb.units:
        if u.kind == "heading" and _clean(u.text):
            cnt[_clean(u.text)] += 1
    for p in c.nb.ps:
        rows = getattr(p, "rows", None)
        if p.kind != "table" or not rows:
            continue
        for cell in rows[0]:
            if _clean(cell):
                cnt[_clean(cell)] += 1
        for r in rows[1:]:
            for cell in r:
                if _clean(cell):
                    cnt[_clean(cell)] += 1
                    break
    items, used = [], 0
    for k, n in sorted(cnt.items(), key=lambda x: (-x[1], x[0])):
        t = f"{k}({n})"
        if used + len(t) + 3 > VOCAB_CHARS:
            break
        items.append(t)
        used += len(t) + 3
    (c.d / "vocab.txt").write_text(" · ".join(items))
    print(c.nb.name, "말", len(cnt), "→ 목록", len(items), f"{used:,}자", flush=True)


# ---- 갈래 세우기 ----
def cmd_plan(c, vs=VARS):
    for v in vs:
        f = c.d / f"plan-{v}.part.jsonl"
        have = _kept(f)
        instr = c.instr(v)
        for q in c.qs:
            if q["id"] in have:
                continue
            kind = "원고 질의" if q["type"] == "drafting" else "그린워싱 주장"
            raw, row = _respond(MODEL, instr, f"[질의 종류] {kind}\n[질의]\n{q['text']}", PLAN_SCHEMA, c.nb.out,
                                f"sg1h:plan-{v}:{q['id']}")
            fs = json.loads(raw)["facets"][:8]
            _keep(f, q["id"], {"facets": fs, "usd": row["costUsd"], "cached": row["cachedTokens"]})
            print(v, q["id"], len(fs), f"${row['costUsd']:.4f} 캐시 {row['cachedTokens']}", flush=True)


# ---- 후보 ----
def cmd_orders(c):
    nb = c.nb
    U, UT = _norm(np.load(nb.d / "emb-pass.npy")), _norm(np.load(nb.d / "emb-tagtext.npy"))
    Q = _norm(np.load(c.ob.d / "emb-q.npy"))
    tags = json.load(open(nb.d / "tags.json"))
    bm, bmt = lexical.BM25([p.text for p in nb.ps]), lexical.BM25(_tagtext(nb.ps, tags))
    g = _graph(nb.ps)
    sec_of = {p.id: p.section_id for p in nb.ps}
    members_of = defaultdict(list)
    for p in nb.ps:
        members_of[p.section_id].append(p.id)
    base = {}
    for qi, q in enumerate(c.qs):
        drop = {nb.idx[p] for p in q.get("self_passages", [])}
        o = _orders(nb.ps, nb.ids, U, UT, bm, bmt, g, Q[qi], q["text"], drop)
        base[q["id"]] = {k: [nb.ids[i] for i in o[k]] for k in (PS, "P-ts P-t+구조")}
    json.dump(base, open(c.d / "base.json", "w"), ensure_ascii=False)
    for v in VARS:
        if not (c.d / f"plan-{v}.part.jsonl").exists():
            continue
        plans = _kept(c.d / f"plan-{v}.part.jsonl")
        texts, owner = [], []
        for q in c.qs:
            for k, fc in enumerate(plans[q["id"]]["facets"]):
                texts.append(fc["name"] + " " + " ".join(fc["keywords"]))
                owner.append((q["id"], k))
        fe = c.d / f"emb-facets-{v}.npy"
        if not fe.exists() or len(np.load(fe)) != len(texts):
            np.save(fe, np.array(llm.embed(texts, nb.out, f"sg1h:facets-{v}"), dtype="float32"))
        FV = _norm(np.load(fe))
        pools = {}
        for q in c.qs:
            drop = {nb.idx[p] for p in q.get("self_passages", [])}
            per = []
            for k_, (t, (qid, k)) in enumerate(zip(texts, owner)):
                if qid != q["id"]:
                    continue
                va = [i for i in retrieve.ranks(list(U @ FV[k_])) if i not in drop]
                vb = [i for i in retrieve.ranks(bm.scores(t)) if i not in drop]
                cs = retrieve.rrf(va, vb)
                per.append((LIKE.get(plans[qid]["facets"][k]["likelihood"], 2), k,
                            [nb.ids[i] for i in sorted(cs, key=lambda i: -cs[i])[:TOP]]))
            per.sort()
            rr, seen = [], set()
            for r in range(TOP):
                for _, _, top in per:
                    if r < len(top) and top[r] not in seen:
                        seen.add(top[r])
                        rr.append(top[r])
            cand = _alt(rr, base[q["id"]]["P-ts P-t+구조"][:50], CAP_B)
            pools[q["id"]] = _section_expand(cand, sec_of, members_of)
        json.dump(pools, open(c.d / f"pool-{v}.json", "w"), ensure_ascii=False)
        print(nb.name, v, "후보 중앙", sorted(len(x) for x in pools.values())[len(pools) // 2], flush=True)


# ---- 재순위 ----
# 비용을 줄이려 F-b′·F-g 후보의 합집합을 한 번만 재순위하고, 갈래마다 제 후보만 그 차례대로 건진다(목록 재순위의 상대 차례를 그대로 씀).
def _pools(c, which):
    if which == "rerank-u":
        pb, pg = json.load(open(c.d / "pool-b.json")), json.load(open(c.d / "pool-g1.json"))
        return {q: _alt(pb[q], pg[q], 10 ** 6) for q in pb}
    return json.load(open(c.d / "pool-g2.json"))


def cmd_rerank(c, which="rerank-u"):
    pools = _pools(c, which)
    f = c.d / f"{which}.part.jsonl"
    have = _kept(f)
    for q in c.qs:
        cand = pools[q["id"]]
        if q["id"] in have and have[q["id"]]["returned"] >= len(cand) // 2:
            continue
        lines = [f"[{p}] " + c.P[p].text.replace("\n", " / ")[:300] for p in cand]
        raw, row = _respond(MODEL, RR_INSTR, f"[질의]\n{q['text']}\n\n[단락]\n" + "\n".join(lines), RR_SCHEMA,
                            c.nb.out, f"sg1h:{which}:{q['id']}")
        try:
            ids = [_pid(x) for x in json.loads(raw)["order"]]
        except json.JSONDecodeError:  # 출력 상한에서 잘린 응답 — 앞에서 id 만 건진다
            ids = re.findall(r"P\d+", raw)
        got = [p for p in dict.fromkeys(ids) if p in set(cand)]
        _keep(f, q["id"], {"order": got, "returned": len(got), "n": len(cand), "usd": row["costUsd"]})
        print(which, q["id"], f"{len(got)}/{len(cand)}", f"${row['costUsd']:.4f}", flush=True)


def cmd_rerank_g2(c):
    cmd_rerank(c, "rerank-g2")


def _avail(c):
    return [v for v in VARS if (c.d / f"pool-{v}.json").exists() and (c.d / f"{RR_FILE[v]}.part.jsonl").exists()]


def _arm_orders(c):
    base = json.load(open(c.d / "base.json"))
    out = {q["id"]: {PS: base[q["id"]][PS]} for q in c.qs}
    for v in _avail(c):
        pools = json.load(open(c.d / f"pool-{v}.json"))
        rr = _kept(c.d / f"{RR_FILE[v]}.part.jsonl")
        for q in c.qs:
            cand = pools[q["id"]]
            top = [p for p in rr[q["id"]]["order"] if p in set(cand)] if q["id"] in rr else []
            top += [p for p in cand if p not in set(top)]
            out[q["id"]][ARM[v]] = top + [p for p in base[q["id"]]["P-ts P-t+구조"] if p not in set(top)]
    return out


def _within(order, L, x):
    used, got = 0, []
    for p in order:
        used += L[p]
        if used > x:
            break
        got.append(p)
    return got


# ---- 그린워싱 판정(F-g1 먼저, 8천 자, 한 번) ----
def cmd_judge(c, v="g1"):
    orders = _arm_orders(c)
    f = c.d / f"judge-{v}.part.jsonl"
    have = _kept(f)
    for q in c.qs:
        if q["type"] != "greenwash" or q["id"] in have:
            continue
        mat = "\n".join(f"[{p}] " + c.P[p].text.replace("\n", " / ") for p in _within(orders[q["id"]][ARM[v]], c.L, 8000))
        sp = ", ".join(q.get("self_passages", []))
        note = f"\n(이 주장은 단락 [{sp}] 에서 나왔다 — 그 단락은 근거로 쓰지 않는다)" if sp else ""
        raw, row = _respond(MODEL, GW_INSTR, f"[자료]\n{mat}\n\n[주장]\n{q['text']}{note}", GW_SCHEMA, c.nb.out,
                            f"sg1h:judge-{v}:{q['id']}")
        r = json.loads(raw)
        r["usd"] = row["costUsd"]
        _keep(f, q["id"], r)
        print(q["id"], r["verdict"], f"${row['costUsd']:.4f}", flush=True)


# ---- 채점 ----
def cmd_eval(c):
    nb, ob = c.nb, c.ob
    orders = _arm_orders(c)
    oldp2new = json.load(open(nb.d / "oldp2new.json"))
    fo = json.load(open(Path(ob.out) / "sg1f" / "orders.json"))
    facts = json.load(open(ob.d / "task" / "facts.json"))
    OL = {p.id: len(p.text) for p in ob.ps}
    OP = {p.id: p for p in ob.ps}
    tot_n, tot_o = sum(c.L.values()), sum(OL.values())

    def fact_recall(order, Pm, L, x, qid):
        fs = facts.get(qid, {}).get("facts") or []
        if not fs:
            return None
        t = _num(" ".join(Pm[p].text for p in _within(order, L, x)))
        return float(np.mean([any(_num(s) in t for s in f["match"]) for f in fs]))

    arms = [PS] + [ARM[v] for v in _avail(c)]
    rows, oldfb = defaultdict(dict), {}
    for q in c.qs:
        ocore = {x["id"] for x in ob.gold.get(q["id"], []) if x["label"] == "core"} - {q.get("self_passage")}
        orel = {x["id"] for x in ob.gold.get(q["id"], [])} - {q.get("self_passage")}
        ncore = {x["id"] for x in c.gold.get(q["id"], []) if x["label"] == "core"} - set(q.get("self_passages", []))
        if not ocore or not ncore:
            continue
        groups = [set(oldp2new.get(x, [])) - set(q.get("self_passages", [])) for x in sorted(ocore)]
        groups = [g_ for g_ in groups if g_]
        for a in arms:
            m = per_query_grouped(orders[q["id"]][a], groups, c.L, tot_n)
            if q["type"] == "drafting":
                for x, nm in ((8000, "사실8천"), (16000, "사실1.6만")):
                    m[nm] = fact_recall(orders[q["id"]][a], c.P, c.L, x, q["id"])
            rows[a][q["id"]] = m
        m = per_query(fo[q["id"]][FB_OLD], ocore, orel, OL, tot_o)
        if q["type"] == "drafting":
            for x, nm in ((8000, "사실8천"), (16000, "사실1.6만")):
                m[nm] = fact_recall(fo[q["id"]][FB_OLD], OP, OL, x, q["id"])
        oldfb[q["id"]] = m
    rng = np.random.default_rng(0)
    MET = ("재현@10", "8천자", "1.6만자", "90%까지 글자", "사실8천", "사실1.6만")
    summ = {}
    for a in arms + ["SG1f F-b(옛 단위)"]:
        src = oldfb if a.startswith("SG1f") else rows[a]
        e = {}
        for m in MET:
            qq = [k for k in src if src[k].get(m) is not None]
            if not qq:
                continue
            vals = [src[k][m] for k in qq]
            e[m] = {"mean": float(np.mean(vals)), "median": float(np.median(vals)), "n": len(qq)}
            if a != PS and not a.startswith("SG1f"):
                qq2 = [k for k in qq if rows[PS][k].get(m) is not None]
                d = [src[k][m] - rows[PS][k][m] for k in qq2]
                mean, lo, hi = boot(d, rng)
                e[m].update({"vsPs": mean, "lo": lo, "hi": hi,
                             "win": sum(x > 0 for x in d), "loss": sum(x < 0 for x in d)})
        summ[a] = e
    # SG1f 가 놓친 53
    missed = json.load(open(nb.d / "missed-before.json"))
    trk, rest = Counter(), []
    for x in missed:
        grp = "pool37" if x["step1+2"] else "nopool16"
        hit = {}
        for v in _avail(c):
            w = set(_within(orders[x["query"]][ARM[v]], c.L, 16000))
            hit[v] = bool(set(x["new"]) & w)
            trk[(grp, x["category"], v)] += hit[v]
        trk[(grp, x["category"], "n")] += 1
        if not hit.get("g1", True):
            pools = json.load(open(c.d / "pool-g1.json"))
            inpool = bool(set(x["new"]) & set(pools[x["query"]]))
            pos = min((orders[x["query"]][ARM["g1"]].index(p) for p in x["new"] if p in orders[x["query"]][ARM["g1"]]),
                      default=None)
            rest.append({"query": x["query"], "category": x["category"], "new": x["new"], "in_pool_g1": inpool,
                         "rank_g1": pos, "text": c.P[x["new"][0]].text[:160].replace("\n", " / ") if x["new"] else ""})
    track = {}
    for (grp, cat, k), n in trk.items():
        track.setdefault(grp, {}).setdefault(cat, {})[k] = n
    # 그린워싱 판정
    exp = json.load(open(Path(ob.out) / "check" / "claim-expected.json"))
    judge = {}
    for v in ("g1", "g2"):
        jg = _kept(c.d / f"judge-{v}.part.jsonl")
        if not jg:
            continue
        ks = [k for k in jg if k in exp]
        judge[v] = {"n": len(ks), "coarse": float(np.mean([COARSE[jg[k]["verdict"]] == COARSE[exp[k]["verdict"]] for k in ks])),
                    "exact": float(np.mean([jg[k]["verdict"] == exp[k]["verdict"] for k in ks])),
                    "partially": float(np.mean([jg[k]["verdict"] == "partially_supported" for k in ks])),
                    "contradicted": sum(jg[k]["verdict"] == "contradicted" for k in ks),
                    "hits": {k: [COARSE[jg[k]["verdict"]] == COARSE[exp[k]["verdict"]], jg[k]["verdict"] == "partially_supported"] for k in ks}}
    res = {"book": nb.name, "nq": len(rows[PS]), "arms": summ, "track53": track, "remaining": rest,
           "judge": {v: {k: x for k, x in j.items() if k != "hits"} for v, j in judge.items()},
           "judge_hits": {v: j["hits"] for v, j in judge.items()},
           "pool_median": {v: float(np.median([len(x) for x in json.load(open(c.d / f"pool-{v}.json")).values()]))
                           for v in _avail(c)}}
    json.dump(res, open(c.d / "eval.json", "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: res[k] for k in ("book", "nq", "judge", "pool_median")}, ensure_ascii=False))
    for a, e in summ.items():
        print(a, {m: (round(v["mean"], 3), round(v.get("vsPs", 0), 3), round(v.get("lo", 0), 3), round(v.get("hi", 0), 3))
                  for m, v in e.items()})
    print(json.dumps(track, ensure_ascii=False))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("--book", required=True, choices=list(BOOKS))
    a = ap.parse_args(argv)
    c = Ctx(a.book)
    steps = {"stage1": ["vocab", "plan1", "orders", "rerank", "judge", "eval"],
             "stage2": ["plan2", "orders", "rerank_g2", "eval"]}
    for s in steps.get(a.cmd, [a.cmd]):
        globals()[f"cmd_{s}"](c)


def cmd_plan1(c):
    cmd_plan(c, ("b", "g1"))


def cmd_plan2(c):
    cmd_plan(c, ("g2",))


if __name__ == "__main__":
    main()


def cmd_judge_g2(c):
    cmd_judge(c, "g2")
