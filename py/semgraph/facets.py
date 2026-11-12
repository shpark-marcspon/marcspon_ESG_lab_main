"""SG1f — 키워드 갈래 검색. 질의를 보고서의 말 목록에 비춰 「찾을 갈래」 3~8개로 나누고, 갈래마다 따로 찾는다.

① vocab (코드, AI 0): 섹션 제목 · 소제목 · 표 행 머리 · 표 열 머리 — 빈도 차례로 약 1.2만 자까지 → sg1f/vocab.txt
② plan (luna, 질의당 한 번): 갈래 이름 · 키워드 3~8 · 연관 가능성(high/mid/low) · 까닭. 그린워싱은 「반대·한정 사실」 갈래를 늘 둔다.
   말 목록을 instructions 머리에 둬 같은 책 안에서는 캐시가 걸리게 한다(차례로 부름).
③ 갈래마다 BM25 + 벡터 → RRF, 상위 15.
④ 갈래: F-a 갈래별 예산(가능성 높은 갈래부터 돌아가며 한 개씩) · F-b (F-a ∪ P-ts 상위 50 을 번갈아 80개까지) → luna 재순위
   · F-c F-b 후보 ∪ Q-f 지표 마디 → luna 재순위(예산이 남을 때만)
⑤ 채점: evaluate2 지표(거시 평균·짝 부트스트랩, P-s·Q-r 대비) · 원고 사실 재현 · 비슷함으로 못 찾는 근거(P-a·P-b 둘 다 50위 밖) · 반대 근거
산출 <out>/sg1f/: vocab.txt · plan.part.jsonl · emb-facets.npy · rerank-b|c.part.jsonl · orders.json · eval.json|md
py/ 에서: python -m semgraph.facets <vocab|plan|orders|rerank_b|rerank_c|eval|all> --book samsung|cosmax|kb
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import numpy as np

from . import lexical, llm
from .evaluate2 import boot, per_query
from .facts import NUMERIC, _div_ok
from .sg1d import MODEL, RR_INSTR, RR_SCHEMA, Book, _norm, _pid
from .taskeval import _budget_text, _keep, _kept, _num, _respond

TOP = 15
CAP_B, CAP_C = 80, 110
VOCAB_CHARS = 12000
LIKE = {"high": 0, "mid": 1, "low": 2}
REF = "P-s P-c+구조"
QR = "Q-r 재순위(P-ts 상위 50)"
QF = "Q-f 지표 마디 먼저(+P-ts)"
SHOW = [REF, "Q-x 질의 확장", QR, QF]
F_ARMS = ["F-a 갈래별 예산", "F-b 갈래∪P-ts → 재순위", "F-c 갈래∪P-ts∪지표 마디 → 재순위"]

PLAN_INSTR = """너는 ESG 보고서에서 근거를 찾는 사람이다. 아래 [보고서 말 목록]은 이 보고서의 섹션 제목·소제목·표 행/열 머리다(괄호는 나온 횟수).
[질의]를 받으면, 답(원고 질의) 또는 판정(그린워싱 주장)에 필요한 근거를 찾을 「갈래」를 3~8개 세워라.
- 갈래마다 keywords 3~8개: 보고서 말 목록에 실제로 있는 말을 우선 쓰고, 일반 말·업종 말도 섞는다.
  업종 추론을 하라(예: 가치사슬 배출 → 제품 사용 단계 → 저전력·소비전력 / 수자원 → 취수·재이용·방류·환원 / 공급망 → 협력회사 점검·컨설팅).
- likelihood: 그 갈래에서 핵심 근거가 나올 가능성 high · mid · low. reason: 한 줄.
- 원고 질의는 필요한 사실(목표·기준연도·실적 표·활동·검증)을 갈래로 나눈다.
- 그린워싱 주장은 같은 지표의 실적 표·각주·검증 범위 갈래와 함께, 반드시 이름이 「반대·한정 사실」인 갈래 하나를 둔다
  (다른 경계·기준, 제외·예외, 제재·소송·과징금, 감소·미달, 범위 주석처럼 주장을 뒤집거나 좁힐 사실).

[보고서 말 목록]
"""
PLAN_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["facets"], "properties": {"facets": {
    "type": "array", "items": {"type": "object", "additionalProperties": False,
                               "required": ["name", "keywords", "likelihood", "reason"],
                               "properties": {"name": {"type": "string"},
                                              "keywords": {"type": "array", "items": {"type": "string"}},
                                              "likelihood": {"type": "string", "enum": list(LIKE)},
                                              "reason": {"type": "string"}}}}}}


def _d(b):
    d = Path(b.out) / "sg1f"
    d.mkdir(exist_ok=True)
    return d


def _clean(s):
    s = re.sub(r"\s+", " ", re.sub(r"\d\)|\*", "", s or "")).strip(" ·-|")
    return s if 2 <= len(s) <= 40 and not NUMERIC.match(s) else None


# ---- ① 말 목록 ----
def cmd_vocab(b):
    c = Counter()
    for s in b.doc["sections"]:
        if _clean(s.get("title")):
            c[_clean(s["title"])] += 1
    for u in b.units:
        if u.kind == "heading" and _clean(u.text):
            c[_clean(u.text)] += 1
    for p in b.ps:
        if p.kind != "table" or not p.rows:
            continue
        for cell in p.rows[0]:
            if _clean(cell):
                c[_clean(cell)] += 1
        for r in p.rows[1:]:
            for cell in r:
                if _clean(cell):
                    c[_clean(cell)] += 1
                    break
    items, used = [], 0
    for k, n in sorted(c.items(), key=lambda x: (-x[1], x[0])):
        t = f"{k}({n})"
        if used + len(t) + 3 > VOCAB_CHARS:
            break
        items.append(t)
        used += len(t) + 3
    (_d(b) / "vocab.txt").write_text(" · ".join(items))
    print(b.name, "말", len(c), "→ 목록", len(items), f"{used:,}자")


# ---- ② 갈래 세우기 ----
def cmd_plan(b):
    f = _d(b) / "plan.part.jsonl"
    have = _kept(f)
    instr = PLAN_INSTR + (_d(b) / "vocab.txt").read_text()
    for q in b.queries:
        if q["id"] in have:
            continue
        kind = "원고 질의" if q["type"] == "drafting" else "그린워싱 주장"
        raw, row = _respond(MODEL, instr, f"[질의 종류] {kind}\n[질의]\n{q['text']}", PLAN_SCHEMA, b.out, f"sg1f:plan:{q['id']}")
        fs = json.loads(raw)["facets"][:8]
        _keep(f, q["id"], {"facets": fs, "usd": row["costUsd"], "sec": row["seconds"], "cached": row["cachedTokens"]})
        print(q["id"], len(fs), f"${row['costUsd']:.4f} 캐시 {row['cachedTokens']}", flush=True)


def _plans(b):
    return _kept(_d(b) / "plan.part.jsonl")


# ---- ③④ 차례 ----
def _facet_strings(b, plans):
    texts, owner = [], []
    for q in b.queries:
        for k, fc in enumerate(plans[q["id"]]["facets"]):
            texts.append(fc["name"] + " " + " ".join(fc["keywords"]))
            owner.append((q["id"], k))
    return texts, owner


def cmd_orders(b):
    d = _d(b)
    plans = _plans(b)
    texts, owner = _facet_strings(b, plans)
    fe = d / "emb-facets.npy"
    if not fe.exists() or len(np.load(fe)) != len(texts):
        np.save(fe, np.array(llm.embed(texts, b.out, "sg1f:facets"), dtype="float32"))
    FV = _norm(np.load(fe))
    U = _norm(np.load(b.d / "emb-pass.npy"))
    bm = lexical.BM25([p.text for p in b.ps])
    from . import retrieve
    base = json.load(open(b.d / "orders.json"))
    rb, rc = _kept(d / "rerank-b.part.jsonl"), _kept(d / "rerank-c.part.jsonl")
    hits = _qf_hits(b)
    out = {}
    for q in b.queries:
        drop = b.drop(q)
        per = []  # (가능성, 차례, 상위 단락 id)
        for k_, (t, (qid, k)) in enumerate(zip(texts, owner)):
            if qid != q["id"]:
                continue
            va = [i for i in retrieve.ranks(list(U @ FV[k_])) if i not in drop]
            vb = [i for i in retrieve.ranks(bm.scores(t)) if i not in drop]
            cs = retrieve.rrf(va, vb)
            top = [b.ids[i] for i in sorted(cs, key=lambda i: -cs[i])[:TOP]]
            per.append((LIKE.get(plans[qid]["facets"][k]["likelihood"], 2), k, top))
        per.sort()
        rr, seen = [], set()
        for r in range(TOP):
            for _, _, top in per:
                if r < len(top) and top[r] not in seen:
                    seen.add(top[r])
                    rr.append(top[r])
        pts = base[q["id"]]["P-ts P-t+구조"]
        o = {a: base[q["id"]][a] for a in SHOW if a in base[q["id"]]}
        o[F_ARMS[0]] = rr + [p for p in pts if p not in seen]
        cand_b = _alt(rr, pts[:50], CAP_B)
        cand_c = list(dict.fromkeys(cand_b + [p for p in hits.get(q["id"], []) if p not in cand_b]))[:CAP_C]
        for arm, cand, got in ((F_ARMS[1], cand_b, rb), (F_ARMS[2], cand_c, rc)):
            if q["id"] in got:
                top = [p for p in got[q["id"]]["order"] if p in set(cand)]
                top += [p for p in cand if p not in set(top)]
                o[arm] = top + [p for p in pts if p not in set(top)]
        o["_cand_b"], o["_cand_c"] = cand_b, cand_c
        o["_facet_top"] = [{"like": l, "k": k, "top": t} for l, k, t in per]
        out[q["id"]] = o
    json.dump(out, open(d / "orders.json", "w"), ensure_ascii=False)
    print(b.name, "차례", len(out), "갈래 후보 중앙", sorted(len(v["_cand_b"]) for v in out.values())[len(out) // 2])


def _alt(a, c, cap):
    """두 차례를 번갈아 합쳐 cap 개까지(갈래 후보가 많아도 P-ts 상위가 빠지지 않게)."""
    out, seen = [], set()
    for x, y in zip(a + [None] * max(0, len(c) - len(a)), c + [None] * max(0, len(a) - len(c))):
        for p in (x, y):
            if p and p not in seen and len(out) < cap:
                seen.add(p)
                out.append(p)
    return out


def _qf_hits(b):
    """Q-f 의 지표 마디 단락(부문 맞는 것) — sg1d.cmd_orders 와 같은 셈."""
    qm = json.load(open(b.d / "qmap2.json"))
    canon = json.load(open(b.d / "canon2.json"))
    tags = b.tags()
    base = json.load(open(b.d / "orders.json"))
    out = {}
    for q in b.queries:
        m = qm.get(q["id"], {"ids": [], "division": None})
        drop = {q.get("self_passage")}
        hit = {p for cid in m["ids"] for p in canon[cid]["passages"]} - drop
        div = {p for p in hit if _div_ok((tags.get(p) or {}).get("division"), m["division"])}
        pos = {p: r for r, p in enumerate(base[q["id"]]["P-ts P-t+구조"])}
        out[q["id"]] = sorted(div, key=lambda p: pos.get(p, 1e9))
    return out


def _rerank(b, key, fname):
    d = _d(b)
    orders = json.load(open(d / "orders.json"))
    f = d / fname
    have = _kept(f)
    P_ = {p.id: p for p in b.ps}
    for q in b.queries:
        if q["id"] in have and have[q["id"]]["returned"] >= len(orders[q["id"]][key]) // 2:
            continue
        cand = orders[q["id"]][key]
        lines = [f"[{p}] " + P_[p].text.replace("\n", " / ")[:300] for p in cand]
        raw, row = _respond(MODEL, RR_INSTR, f"[질의]\n{q['text']}\n\n[단락]\n" + "\n".join(lines), RR_SCHEMA, b.out,
                            f"sg1f:{fname[:8]}:{q['id']}")
        try:
            ids = [_pid(x) for x in json.loads(raw)["order"]]
        except json.JSONDecodeError:  # 출력 상한에서 잘린 응답(KB 한 건, 3.7만 자) — 앞에서 id 만 건진다
            ids = re.findall(r"P\d+", raw)
        got = [p for p in dict.fromkeys(ids) if p in set(cand)]
        _keep(f, q["id"], {"order": got, "returned": len(got), "n": len(cand), "usd": row["costUsd"], "sec": row["seconds"]})
        print(q["id"], f"{len(got)}/{len(cand)}", f"${row['costUsd']:.4f}", flush=True)


def cmd_rerank_b(b):
    _rerank(b, "_cand_b", "rerank-b.part.jsonl")


def cmd_rerank_c(b):
    _rerank(b, "_cand_c", "rerank-c.part.jsonl")


# ---- ⑤ 채점 ----
def cmd_eval(b):
    d = _d(b)
    orders = json.load(open(d / "orders.json"))
    L = {p.id: len(p.text) for p in b.ps}
    total = sum(L.values())
    base = json.load(open(b.d / "orders.json"))
    arms = [a for a in SHOW + F_ARMS if a in next(iter(orders.values()))]
    rows, qtype, hard = {}, {}, {}
    for q in b.queries:
        g = b.gold.get(q["id"], [])
        core = {x["id"] for x in g if x["label"] == "core"} - {q.get("self_passage")}
        rel = {x["id"] for x in g} - {q.get("self_passage")}
        if not core:
            continue
        qtype[q["id"]] = q["type"]
        ra = {p: r for r, p in enumerate(base[q["id"]]["P-a 벡터"])}
        rb = {p: r for r, p in enumerate(base[q["id"]]["P-b BM25"])}
        hard[q["id"]] = {c for c in core if ra.get(c, 9e9) >= 50 and rb.get(c, 9e9) >= 50}
        for a in arms:
            rows.setdefault(a, {})[q["id"]] = per_query(orders[q["id"]][a], core, rel, L, total)

    def within(order, x):
        used, got = 0, set()
        for p in order:
            used += L[p]
            if used > x:
                break
            got.add(p)
        return got

    hard_catch = {a: sum(len(hard[q] & within(orders[q][a], 16000)) for q in hard) for a in arms}
    exp_f = Path(b.out) / "check" / "claim-expected.json"
    exp = json.load(open(exp_f)) if exp_f.exists() else {}
    contrary = {k: set(v["contrary"]) for k, v in exp.items() if v.get("contrary")}
    con_catch = {a: sum(len(c & within(orders[k][a], 8000)) for k, c in contrary.items() if k in orders) for a in arms}
    # 원고 사실 재현(코드만, sg1d/task/facts.json)
    fx_f = b.d / "task" / "facts.json"
    fx = json.load(open(fx_f)) if fx_f.exists() else {}
    facts = {}
    for a in arms:
        for x in (8000, 16000):
            per = {}
            for qid, v in fx.items():
                if v["facts"]:
                    t = _num(_budget_text(b, orders[qid][a], x))
                    per[qid] = float(np.mean([any(_num(s) in t for s in f_["match"]) for f_ in v["facts"]]))
            facts[f"{a}|{x}"] = per
    rng = np.random.default_rng(0)
    metrics = ["재현@10", "재현@20", "8천자", "1.6만자", "90%까지 글자"]
    res = {}
    for scope in ("전체", "drafting", "greenwash"):
        qs = [q for q in qtype if scope == "전체" or qtype[q] == scope]
        sc = res[scope] = {"n": len(qs), "arms": {}}
        for a in arms:
            e = {"mean": {m: float(np.mean([rows[a][q][m] for q in qs])) for m in metrics},
                 "median_90": float(np.median([rows[a][q]["90%까지 글자"] for q in qs])), "vs": {}}
            for ref in (REF, QR):
                if ref != a and ref in rows:
                    e["vs"][ref] = {}
                    for m in metrics:
                        dd = [rows[a][q][m] - rows[ref][q][m] for q in qs]
                        mean, lo, hi = boot(dd, rng)
                        e["vs"][ref][m] = {"diff": mean, "lo": lo, "hi": hi, "win": sum(x > 0 for x in dd),
                                           "loss": sum(x < 0 for x in dd)}
            sc["arms"][a] = e
    fsum = {}
    for a in arms:
        for x in (8000, 16000):
            per = facts[f"{a}|{x}"]
            ent = {"mean": float(np.mean(list(per.values()))) if per else None, "n": len(per)}
            for ref in (REF, QR):
                if ref != a and per and ref in arms:
                    rp = facts[f"{ref}|{x}"]
                    ent[f"vs {ref}"] = boot([per[k] - rp[k] for k in per], rng)
            fsum[f"{a}|{x}"] = ent
    cost = {}
    for name, fn in (("plan", "plan.part.jsonl"), ("rerank_b", "rerank-b.part.jsonl"), ("rerank_c", "rerank-c.part.jsonl")):
        r = _kept(d / fn)
        cost[name] = {"usd_per_q": sum(v["usd"] for v in r.values()) / max(1, len(r)), "n": len(r)}
    out = {"book": b.name, "summary": res, "hard_n": sum(len(v) for v in hard.values()), "hard_catch16k": hard_catch,
           "contrary_n": sum(len(v) for v in contrary.values()), "contrary_catch8k": con_catch, "facts": fsum,
           "cost": cost, "per_query": rows}
    json.dump(out, open(d / "eval.json", "w"), ensure_ascii=False, indent=1)
    md = [f"# SG1f — {b.name}\n", "| 갈래 | 재현@10 | 8천자 | 1.6만자 | 90% 글자 | 사실 1.6만 | 못 찾던 근거 @1.6만 | 반대 근거 @8천 |",
          "|---|---|---|---|---|---|---|---|"]
    for a in arms:
        m = res["전체"]["arms"][a]["mean"]
        fm = fsum[f"{a}|16000"]["mean"]
        md.append(f"| {a} | {m['재현@10']:.1%} | {m['8천자']:.1%} | {m['1.6만자']:.1%} | {res['전체']['arms'][a]['median_90']:,.0f} | "
                  + (f"{fm:.1%}" if fm is not None else "-") + f" | {hard_catch[a]}/{out['hard_n']} | {con_catch[a]}/{out['contrary_n']} |")
    for ref in (REF, QR):
        md += [f"\n**{ref} 대비** 차이 %p [95% 구간] 이김/짐\n", "| 갈래 | 재현@10 | 8천자 | 1.6만자 |", "|---|---|---|---|"]
        for a in arms:
            v = res["전체"]["arms"][a]["vs"].get(ref)
            if v:
                cells = [f"{v[m]['diff'] * 100:+.1f} [{v[m]['lo'] * 100:+.1f}, {v[m]['hi'] * 100:+.1f}] {v[m]['win']}/{v[m]['loss']}"
                         + (" ✓" if v[m]["lo"] > 0 or v[m]["hi"] < 0 else "") for m in ("재현@10", "8천자", "1.6만자")]
                md.append(f"| {a} | " + " | ".join(cells) + " |")
    md.append(f"\n질의당 USD: 갈래 세우기 {cost['plan']['usd_per_q']:.4f} · F-b 재순위 {cost['rerank_b']['usd_per_q']:.4f}"
              f" · F-c 재순위 {cost['rerank_c']['usd_per_q']:.4f}")
    (d / "eval.md").write_text("\n".join(md))
    print("\n".join(md))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("--book", required=True)
    ap.add_argument("--gold", default="gold-final.json")
    a = ap.parse_args(argv)
    b = Book(a.book, a.gold)
    steps = {"all": ["vocab", "plan", "orders", "rerank_b", "orders", "eval"]}.get(a.cmd, [a.cmd])
    for s in steps:
        globals()[f"cmd_{s}"](b)


if __name__ == "__main__":
    main()
