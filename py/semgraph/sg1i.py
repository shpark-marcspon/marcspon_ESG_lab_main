"""SG1i — 넷째 책(세 권을 안 본 업종)으로 누설 없는 재측정 · 반대 근거 갈래 F-n.

시험지는 방법 모르는 에이전트가 검색 전에 달았다(blind/a·b·c). freeze 가 sha 로 얼린다 — 그 전에는 검색·호출을 하지 않는다.
갈래(바닥은 모두 P-s 상위 50 — 이름표층 없음):
- P-s: AI 0.
- F-b′: 말 목록 + 갈래(예시 줄 뺀 지시문) → 후보 → 재순위.
- F-g1: F-b′ + 기준 사전 + 「반대·한정」 꼴 일곱(세 권을 보고 적음 — 누설 크기 대조).
- F-n: 주장 분해 + 자유 서술 「무엇이 뒤집나」 가설(꼴 없음) + 같은 지표 표 끌어오기(코드) → 가설을 함께 준 재순위.
재순위: F-b′∪F-g1 한 번, F-n 따로.
주지표: 반대 단락 재현@8천 자. 사실 재현은 숫자 경계 매칭. 묶음 채점(per_query_grouped)은 쓰지 않는다.
산출 <out>/sg1i/ · py/ 에서: python -m semgraph.sg1i <freeze|embed|vocab|plan|orders|rerank|facts|judge|eval> (또는 run = freeze 뒤 전부)
"""

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

import numpy as np

from . import glossary, lexical, llm, retrieve
from .evaluate2 import boot, per_query
from .facets import CAP_B, LIKE, PLAN_INSTR, PLAN_SCHEMA, TOP, _alt
from .fixunits import New, _graph, _section_expand
from .sg1d import MODEL, RR_INSTR, RR_SCHEMA, _norm, _pid
from .sg1h import CONTRARY_INSTR, cmd_vocab
from .taskeval import FACT_INSTR, FACT_SCHEMA, GW_INSTR, GW_SCHEMA, _keep, _kept, _num, _respond

BOOK = "hdec"
PS = "P-s"
ARMS = {"b": "F-b′", "g1": "F-g1", "n": "F-n"}
PULL = 15
ROUNDS = (1, 2)
CAP_USD = 0.70  # 0.65 + 사후 정답 근거 판정 # 이 판 상한(TICK-429 노트). 넘으면 멈춘다


def _guard(c):
    spent = sum(r.get("costUsd", 0) for r in map(json.loads, open(c.root / "calls.jsonl")) if str(r.get("tag", "")).startswith("sg1i:"))
    if spent > CAP_USD:
        raise SystemExit(f"상한 넘음: {spent:.3f} USD")
    return spent

_EXAMPLE = re.compile(r"\n\s*업종 추론을 하라\(예:[^\n]*")
PLAN_B = _EXAMPLE.sub("", PLAN_INSTR)
assert "저전력" not in PLAN_B
_HEAD, _TAIL = PLAN_B.split("- 그린워싱 주장은", 1)
_TAIL_VOCAB = _TAIL[_TAIL.index("[보고서 말 목록]"):]
PLAN_N = _HEAD + """- 그린워싱 주장은 먼저 주장을 조각으로 나눠라: 지표, 범위(대상·사업장·기준), 기간, 방향(늘었다·줄었다·전부 같은 말).
  조각마다 「이 조각이 틀리거나 좁다면 보고서 어딘가에 무엇이 어떻게 적혀 있겠는가」 가설을 세워, 가설 하나를 갈래 하나로 둔다.
  그 갈래 이름은 「뒤집기: <가설 한 줄>」로 쓴다. 주장을 그대로 뒷받침할 갈래도 하나 둔다.
- metrics: 이 질의와 같은 것을 재는 지표 이름 1~6개. 보고서 말 목록에 있는 말 그대로 쓴다.

""" + _TAIL_VOCAB
PLAN_N_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["facets", "metrics"], "properties": {
    "facets": PLAN_SCHEMA["properties"]["facets"], "metrics": {"type": "array", "items": {"type": "string"}}}}
RR_N = RR_INSTR + """
[뒤집기 가설]이 있으면, 주장을 뒷받침하는 근거뿐 아니라 그 가설에 맞는 단락(같은 지표의 다른 값·다른 범위·다른 기간·반대 방향)도 앞에 둬라."""


class C:
    def __init__(self):
        self.nb = New(BOOK)
        self.root = Path(self.nb.out)
        self.d = self.root / "sg1i"
        self.d.mkdir(exist_ok=True)
        saved = [x["id"] for x in json.load(open(self.nb.d / "passages.json"))]
        assert [p.id for p in self.nb.ps] == saved
        self.P = {p.id: p for p in self.nb.ps}
        self.L = {p.id: len(p.text) for p in self.nb.ps}
        self.total = sum(self.L.values())
        if (self.d / "queries.json").exists():
            self.qs = json.load(open(self.d / "queries.json"))
            self.gold = json.load(open(self.d / "gold.json"))
            self.contrary = json.load(open(self.d / "contrary.json"))
            self.exp = json.load(open(self.d / "expected.json"))

    def instr(self, v):
        vocab = (self.d / "vocab.txt").read_text()
        if v == "b":
            return PLAN_B + vocab
        if v == "n":
            return PLAN_N + vocab
        return PLAN_B.replace("[보고서 말 목록]\n", "") + CONTRARY_INSTR + "\n\n[보고서 말 목록]\n" + vocab \
            + "\n\n" + glossary.text(synonyms=False)


def cmd_freeze(c):
    b = c.root / "blind"
    A = {x["id"]: x for x in json.load(open(b / "a" / "claims-A.json"))}
    B = {x["id"]: x for x in json.load(open(b / "b" / "claims-B.json"))}
    D = json.load(open(b / "queries-D.json"))
    goldD = json.load(open(b / "c" / "gold-D.json"))
    qs = [dict(q, type="drafting") for q in D]
    gold = {q["id"]: goldD.get(q["id"], []) for q in qs}
    contrary, exp, log = {}, {}, []
    for k, a in A.items():
        x = B[k]
        ca, cb = set(a["contrary"]), set(x["contrary"])
        same = bool(ca) == bool(cb) and (not ca or bool(ca & cb))
        log.append({"id": k, "keep": same, "A": sorted(ca), "B": sorted(cb), "vA": a["verdict"], "vB": x["verdict"], "source": a["source"]})
        if not same:
            continue
        qs.append({"id": k, "type": "greenwash", "text": a["text"], "self_passages": a["self_passages"], "source": a["source"]})
        selfp = set(a["self_passages"])
        core = (set(a["core"]) | set(x["core"]) | (ca & cb)) - selfp
        gold[k] = [{"id": p, "label": "core"} for p in sorted(core, key=lambda s: int(s[1:]))]
        contrary[k] = sorted((ca & cb) - selfp, key=lambda s: int(s[1:]))
        exp[k] = {"verdict": a["verdict"], "verdict_b": x["verdict"], "agree": a["verdict"] == x["verdict"]}
    for name, v in (("queries", qs), ("gold", gold), ("contrary", contrary), ("expected", exp), ("agree-log", log)):
        json.dump(v, open(c.d / f"{name}.json", "w"), ensure_ascii=False, indent=1)
    h = {n: hashlib.sha256(open(c.d / f"{n}.json", "rb").read()).hexdigest()[:16] for n in ("queries", "gold", "contrary", "expected")}
    (c.d / "freeze.json").write_text(json.dumps(h))
    print("주장", len(A), "남김", len(contrary), "반대", sum(bool(v) for v in contrary.values()),
          "깨끗", sum(not v for v in contrary.values()), "판정 일치", sum(e["agree"] for e in exp.values()), h)


def cmd_embed(c):
    if not (c.d / "emb-pass.npy").exists():
        np.save(c.d / "emb-pass.npy", np.array(llm.embed([p.text for p in c.nb.ps], c.nb.out, "sg1i:pass"), dtype="float32"))
    if not (c.d / "emb-q.npy").exists():
        np.save(c.d / "emb-q.npy", np.array(llm.embed([q["text"] for q in c.qs], c.nb.out, "sg1i:q"), dtype="float32"))


def cmd_plan(c):
    for v in ARMS:
        f = c.d / f"plan-{v}.part.jsonl"
        have = _kept(f)
        instr = c.instr(v)
        schema = PLAN_N_SCHEMA if v == "n" else PLAN_SCHEMA
        for q in c.qs:
            if q["id"] in have:
                continue
            kind = "원고 질의" if q["type"] == "drafting" else "그린워싱 주장"
            raw, row = _respond(MODEL, instr, f"[질의 종류] {kind}\n[질의]\n{q['text']}", schema, c.nb.out, f"sg1i:plan-{v}:{q['id']}")
            r = json.loads(raw)
            _keep(f, q["id"], {"facets": r["facets"][:8], "metrics": r.get("metrics", []), "usd": row["costUsd"], "cached": row["cachedTokens"]})
            print(v, q["id"], len(r["facets"]), f"${row['costUsd']:.4f} 캐시 {row['cachedTokens']}", flush=True)


def _compact(s):
    return re.sub(r"\s+", "", s or "").lower()


def _pull_tables(c, metrics, bm, drop):
    """같은 지표 표: 지표 이름이 표 첫 줄(이름표)·행/열 머리에 있는 표를 비슷함과 무관하게 끌어온다."""
    ms = [_compact(m) for m in metrics if len(_compact(m)) >= 2]
    if not ms:
        return []
    bs = bm.scores(" ".join(metrics))
    hit = []
    for i, p in enumerate(c.nb.ps):
        if p.kind != "table" or p.id in drop:
            continue
        rows = getattr(p, "rows", None) or []
        heads = [p.text.split("\n", 1)[0]] + [str(x) for x in (rows[0] if rows else [])] + [str(r[0]) for r in rows[1:] if r]
        h = _compact(" ".join(heads))
        n = sum(m in h for m in ms)
        if n:
            hit.append((-n, -bs[i], p.id))
    return [x[2] for x in sorted(hit)[:PULL]]


def cmd_orders(c):
    nb = c.nb
    U, Q = _norm(np.load(c.d / "emb-pass.npy")), _norm(np.load(c.d / "emb-q.npy"))
    bm = lexical.BM25([p.text for p in nb.ps])
    g = _graph(nb.ps)
    sec_of = {p.id: p.section_id for p in nb.ps}
    members = defaultdict(list)
    for p in nb.ps:
        members[p.section_id].append(p.id)
    base = {}
    for qi, q in enumerate(c.qs):
        drop = {nb.idx[p] for p in q.get("self_passages", [])}
        va = [i for i in retrieve.ranks(list(U @ Q[qi])) if i not in drop]
        vb = [i for i in retrieve.ranks(bm.scores(q["text"])) if i not in drop]
        cs = retrieve.rrf(va, vb)
        base[q["id"]] = [nb.ids[i] for i in retrieve.expand(cs, nb.ids, g) if i not in drop]
    json.dump(base, open(c.d / "base.json", "w"), ensure_ascii=False)
    stats = {}
    for v in ARMS:
        plans = _kept(c.d / f"plan-{v}.part.jsonl")
        texts, owner = [], []
        for q in c.qs:
            for k, fc in enumerate(plans[q["id"]]["facets"]):
                texts.append(fc["name"] + " " + " ".join(fc["keywords"]))
                owner.append((q["id"], k))
        fe = c.d / f"emb-facets-{v}.npy"
        if not fe.exists() or len(np.load(fe)) != len(texts):
            np.save(fe, np.array(llm.embed(texts, nb.out, f"sg1i:facets-{v}"), dtype="float32"))
        FV = _norm(np.load(fe))
        pools, pulled = {}, {}
        for q in c.qs:
            selfp = set(q.get("self_passages", []))
            drop = {nb.idx[p] for p in selfp}
            per = []
            for j, (t, (qid, k)) in enumerate(zip(texts, owner)):
                if qid != q["id"]:
                    continue
                va = [i for i in retrieve.ranks(list(U @ FV[j])) if i not in drop]
                vb = [i for i in retrieve.ranks(bm.scores(t)) if i not in drop]
                cs = retrieve.rrf(va, vb)
                per.append((LIKE.get(plans[qid]["facets"][k]["likelihood"], 2), k, [nb.ids[i] for i in sorted(cs, key=lambda i: -cs[i])[:TOP]]))
            per.sort(key=lambda x: x[:2])
            rr, seen = [], set()
            for r in range(TOP):
                for _, _, top in per:
                    if r < len(top) and top[r] not in seen:
                        seen.add(top[r])
                        rr.append(top[r])
            cand = _alt(rr, base[q["id"]][:50], CAP_B)
            if v == "n":
                pl = [p for p in _pull_tables(c, plans[q["id"]]["metrics"], bm, selfp) if p not in cand]
                pulled[q["id"]] = pl
                cand = cand + pl
            pools[q["id"]] = [p for p in _section_expand(cand, sec_of, members) if p not in selfp]
        json.dump(pools, open(c.d / f"pool-{v}.json", "w"), ensure_ascii=False)
        if v == "n":
            json.dump(pulled, open(c.d / "pulled-n.json", "w"), ensure_ascii=False)
        stats[v] = sorted(len(x) for x in pools.values())[len(pools) // 2]
    print("후보 중앙", stats, flush=True)


def _rerank_one(c, q, cand, instr, extra, f, tag):
    lines = [f"[{p}] " + c.P[p].text.replace("\n", " / ")[:300] for p in cand]
    raw, row = _respond(MODEL, instr, f"[질의]\n{q['text']}{extra}\n\n[단락]\n" + "\n".join(lines), RR_SCHEMA, c.nb.out, tag)
    try:
        ids = [_pid(x) for x in json.loads(raw)["order"]]
    except json.JSONDecodeError:  # 출력 상한에서 잘린 응답 — 앞에서 id 만 건진다
        ids = re.findall(r"P\d+", raw)
    got = [p for p in dict.fromkeys(ids) if p in set(cand)]
    _keep(f, q["id"], {"order": got, "returned": len(got), "n": len(cand), "usd": row["costUsd"]})
    print(tag, f"{len(got)}/{len(cand)}", f"${row['costUsd']:.4f}", flush=True)


def cmd_rerank(c):
    pb, pg, pn = (json.load(open(c.d / f"pool-{v}.json")) for v in ("b", "g1", "n"))
    fu, fn = c.d / "rerank-u.part.jsonl", c.d / "rerank-n.part.jsonl"
    hu, hn = _kept(fu), _kept(fn)
    plans = _kept(c.d / "plan-n.part.jsonl")
    for q in c.qs:
        if q["id"] not in hu:
            _rerank_one(c, q, _alt(pb[q["id"]], pg[q["id"]], 10 ** 6), RR_INSTR, "", fu, f"sg1i:rerank-u:{q['id']}")
        if q["id"] not in hn:
            hyp = [f["name"] for f in plans[q["id"]]["facets"] if f["name"].startswith("뒤집기")]
            extra = "\n\n[뒤집기 가설]\n" + "\n".join(f"- {h}" for h in hyp) if hyp else ""
            _rerank_one(c, q, pn[q["id"]], RR_N, extra, fn, f"sg1i:rerank-n:{q['id']}")


def _arm_orders(c):
    base = json.load(open(c.d / "base.json"))
    out = {q["id"]: {PS: base[q["id"]]} for q in c.qs}
    for v, name in ARMS.items():
        pools = json.load(open(c.d / f"pool-{v}.json"))
        rr = _kept(c.d / ("rerank-n.part.jsonl" if v == "n" else "rerank-u.part.jsonl"))
        for q in c.qs:
            cand = pools[q["id"]]
            top = [p for p in rr[q["id"]]["order"] if p in set(cand)] if q["id"] in rr else []
            top += [p for p in cand if p not in set(top)]
            out[q["id"]][name] = top + [p for p in base[q["id"]] if p not in set(top)]
    return out


def _within(order, L, x):
    used, got = 0, []
    for p in order:
        used += L[p]
        if used > x:
            break
        got.append(p)
    return got


def _line(c, p):
    return f"[{p}] " + c.P[p].text.replace("\n", " / ")


def cmd_facts(c):
    f = c.d / "facts.part.jsonl"
    have = _kept(f)
    for q in c.qs:
        core = [x["id"] for x in c.gold.get(q["id"], []) if x["label"] == "core"]
        if q["type"] != "drafting" or q["id"] in have or not core:
            continue
        mat = "\n".join(_line(c, p) for p in core)
        raw, row = _respond(MODEL, FACT_INSTR, f"[단락]\n{mat}\n\n[질의]\n{q['text']}", FACT_SCHEMA, c.nb.out, f"sg1i:facts:{q['id']}")
        corpus, seen, keep = _num(mat), set(), []
        for x in json.loads(raw)["facts"]:
            m = [s for s in x["match"] if len(_num(s)) >= 3 and _num(s) in corpus]
            key = tuple(sorted(_num(s) for s in m))
            if m and key not in seen:
                seen.add(key)
                keep.append({**x, "match": m})
        _keep(f, q["id"], {"facts": keep, "usd": row["costUsd"]})
        print("facts", q["id"], len(keep), flush=True)


def cmd_judge(c):
    orders = _arm_orders(c)
    full = "\n".join(_line(c, p.id) for p in c.nb.ps)
    jobs = [(n, None) for n in ARMS.values()] + [("문서 전체", full)]  # P-s 판정·오라클 둘째 회는 비용으로 뺌(노트)
    for name, mat0 in jobs:
        tag = {"F-b′": "b", "F-g1": "g1", "F-n": "n", "문서 전체": "full"}[name]
        for r in ((1,) if mat0 else ROUNDS):
            f = c.d / f"judge-{tag}-{r}.part.jsonl"
            have = _kept(f)
            for q in c.qs:
                if q["type"] != "greenwash" or q["id"] in have:
                    continue
                _guard(c)
                mat = mat0 or "\n".join(_line(c, p) for p in _within(orders[q["id"]][name], c.L, 8000))
                sp = ", ".join(q.get("self_passages", []))
                note = f"\n(이 주장은 단락 [{sp}] 에서 나왔다 — 그 단락은 근거로 쓰지 않는다)" if sp else ""
                raw, row = _respond(MODEL, GW_INSTR, f"[자료]\n{mat}\n\n[주장]\n{q['text']}{note}", GW_SCHEMA, c.nb.out, f"sg1i:judge-{tag}-{r}:{q['id']}")
                v = json.loads(raw)
                v["usd"] = row["costUsd"]
                _keep(f, q["id"], v)
                print(tag, r, q["id"], v["verdict"], f"${row['costUsd']:.4f}", flush=True)


def cmd_judge_gold(c):
    """사후(사전 등록 아님): 정답 핵심 단락만 주고 판정 — 깨끗한 주장에 supported 가 안 나오는 것이 검색 탓인지 판정 탓인지."""
    for r in ROUNDS:
        f = c.d / f"judge-gold-{r}.part.jsonl"
        have = _kept(f)
        for q in c.qs:
            if q["type"] != "greenwash" or q["id"] in have:
                continue
            _guard(c)
            core = [x["id"] for x in c.gold.get(q["id"], [])]
            mat = "\n".join(_line(c, p) for p in core)
            sp = ", ".join(q.get("self_passages", []))
            note = f"\n(이 주장은 단락 [{sp}] 에서 나왔다 — 그 단락은 근거로 쓰지 않는다)" if sp else ""
            raw, row = _respond(MODEL, GW_INSTR, f"[자료]\n{mat}\n\n[주장]\n{q['text']}{note}", GW_SCHEMA, c.nb.out, f"sg1i:judge-gold-{r}:{q['id']}")
            v = json.loads(raw)
            v["usd"] = row["costUsd"]
            _keep(f, q["id"], v)
            print("gold", r, q["id"], v["verdict"], flush=True)


def _strict(s, t):
    return bool(re.search(r"(?<![\d.])" + re.escape(_num(s)) + r"(?![\d.])", t))


def cmd_eval(c):
    orders = _arm_orders(c)
    names = [PS] + list(ARMS.values())
    rng = np.random.default_rng(0)
    facts = _kept(c.d / "facts.part.jsonl")
    gw = [q for q in c.qs if q["type"] == "greenwash"]
    conq = [q["id"] for q in gw if c.contrary[q["id"]]]
    clean = [q["id"] for q in gw if not c.contrary[q["id"]]]
    res = {"n": {"drafting_core": 0, "greenwash": len(gw), "contrary": len(conq), "clean": len(clean)}, "arms": {}}

    def rc(order, ids, x):
        w = set(_within(order, c.L, x))
        return sum(p in w for p in ids) / len(ids)

    def anyh(order, ids, x):
        return float(bool(set(_within(order, c.L, x)) & set(ids)))

    rows = defaultdict(dict)
    for q in c.qs:
        core = {x["id"] for x in c.gold.get(q["id"], []) if x["label"] == "core"}
        rel = {x["id"] for x in c.gold.get(q["id"], [])}
        for a in names:
            o = orders[q["id"]][a]
            m = {}
            if core:
                pq = per_query(o, core, rel, c.L, c.total)
                m.update({f"핵심 {k}": pq[k] for k in ("재현@10", "8천자", "1.6만자")})
                m["90%글자"] = pq["90%까지 글자"]
            con = c.contrary.get(q["id"])
            if con:
                m.update({"반대@8천": rc(o, con, 8000), "반대@1.6만": rc(o, con, 16000), "반대하나@8천": anyh(o, con, 8000), "반대 순위": min((o.index(p) for p in con if p in o), default=999)})
            fs = (facts.get(q["id"]) or {}).get("facts") or []
            if q["type"] == "drafting" and fs:
                for x, nm in ((8000, "사실8천"), (16000, "사실1.6만")):
                    t = _num(" ".join(c.P[p].text for p in _within(o, c.L, x)))
                    m[nm] = float(np.mean([any(_strict(s, t) for s in f_["match"]) for f_ in fs]))
            rows[a][q["id"]] = m
    res["n"]["drafting_core"] = sum("핵심 8천자" in rows[PS][q["id"]] for q in c.qs if q["type"] == "drafting")
    MET = ("반대@8천", "반대@1.6만", "반대하나@8천", "핵심 재현@10", "핵심 8천자", "핵심 1.6만자", "사실8천", "사실1.6만", "90%글자")
    for a in names:
        e = {}
        for m in MET:
            for scope, ty in (("전체", None), ("원고", "drafting"), ("주장", "greenwash")):
                qq = [q["id"] for q in c.qs if (ty is None or q["type"] == ty) and m in rows[a][q["id"]]]
                if not qq or (m.startswith(("반대", "사실")) and scope != "전체"):
                    continue
                vals = [rows[a][k][m] for k in qq]
                ent = {"mean": float(np.mean(vals)), "median": float(np.median(vals)), "n": len(qq)}
                for ref in (PS, "F-b′"):
                    if ref != a:
                        d = [rows[a][k][m] - rows[ref][k][m] for k in qq]
                        mean, lo, hi = boot(d, rng)
                        ent[f"vs {ref}"] = [mean, lo, hi, sum(x > 0 for x in d), sum(x < 0 for x in d)]
                e[f"{m}|{scope}"] = ent
        res["arms"][a] = e
    res["contrary_rank"] = {k: {a: rows[a][k]["반대 순위"] for a in names} for k in conq}
    # 판정
    jres = {}
    for tag, name in (("b", "F-b′"), ("g1", "F-g1"), ("n", "F-n"), ("full", "문서 전체")):
        R = [_kept(c.d / f"judge-{tag}-{r}.part.jsonl") for r in ROUNDS]
        if not R[0]:
            continue
        if not R[1]:
            R[1] = R[0]  # 한 회만 돈 갈래(오라클) — 두 번 같음은 뜻 없음
        ks = [q["id"] for q in gw if q["id"] in R[0]]  # 오라클은 상한에 걸려 앞 12 주장만
        cq, cl = [k for k in conq if k in R[0]], [k for k in clean if k in R[0]]
        same = [R[0][k]["verdict"] == R[1][k]["verdict"] for k in ks]
        ns2 = [all(R[i][k]["verdict"] != "supported" for i in (0, 1)) for k in cq]
        ns1 = [R[0][k]["verdict"] != "supported" for k in cq]
        cite = [any(p in R[i][k].get("evidence", []) for i in (0, 1) for p in c.contrary[k]) for k in cq]
        jres[name] = {"주장 수": len(ks), "두 번 같음": float(np.mean(same)),
                      "반대: 두 회 다 supported 아님": float(np.mean(ns2)) if cq else None, "반대: 1회 supported 아님": float(np.mean(ns1)) if cq else None,
                      "반대: 반대 단락 인용(두 회 중)": float(np.mean(cite)) if cq else None,
                      "깨끗: 거짓 contradicted": sum(R[i][k]["verdict"] == "contradicted" for i in (0, 1) for k in cl) / 2,
                      "깨끗: 1회 supported": float(np.mean([R[0][k]["verdict"] == "supported" for k in cl])) if cl else None,
                      "정확 일치(1회, A 기대)": float(np.mean([R[0][k]["verdict"] == c.exp[k]["verdict"] for k in ks])),
                      "partially 비율": float(np.mean([R[0][k]["verdict"] == "partially_supported" for k in ks])),
                      "판정표": {k: [R[0][k]["verdict"], R[1][k]["verdict"]] for k in ks}}
    res["judge"] = jres
    res["floor"] = {"늘 supported: 반대 잡기": 0.0, "늘 not-supported: 깨끗 거짓 경보 전부": len(clean),
                    "기대 supported 비율": float(np.mean([c.exp[q["id"]]["verdict"] == "supported" for q in gw]))}
    json.dump(res, open(c.d / "eval.json", "w"), ensure_ascii=False, indent=1)
    print(json.dumps({"n": res["n"], "floor": res["floor"]}, ensure_ascii=False))
    for a in names:
        print(a, {k: (round(v["mean"], 3), [round(x, 3) for x in v.get("vs P-s", [])[:3]], [round(x, 3) for x in v.get("vs F-b′", [])[:3]]) for k, v in res["arms"][a].items()})
    for a, j in jres.items():
        print(a, {k: v for k, v in j.items() if k != "판정표"})
    print(json.dumps(res["contrary_rank"], ensure_ascii=False))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    a = ap.parse_args(argv)
    c = C()
    if a.cmd == "freeze":
        return cmd_freeze(c)
    steps = ["embed", "vocab", "plan", "orders", "rerank", "facts", "judge", "eval"] if a.cmd == "run" else [a.cmd]
    for s in steps:
        if s == "vocab":
            cmd_vocab(c)
        else:
            globals()[f"cmd_{s}"](c)
        if (c.root / "calls.jsonl").exists():
            print(f"[{s}] 누적 {_guard(c):.4f} USD", flush=True)


if __name__ == "__main__":
    main()
