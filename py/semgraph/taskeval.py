"""SG1d 과제 기준 — 찾은 글로 실제 일을 시켜 본다.

그린워싱: 주장마다 luna 가 판정(supported · partially_supported · unsupported · contradicted · not_verifiable).
  오라클 = 문서 전체(map 처럼 다 읽음), 갈래 = 그 갈래 차례로 예산(4천·8천·1.6만 자)까지 채운 글. 같은 지시문.
  오라클 반복(코스맥스)으로 자기 일치(소음 천장)를 잰다. 반대 근거는 정답 핵심 단락만 주고 luna 에 한 번 물어 둔다.
원고작성: 정답 핵심 단락에서 luna 가 「있어야 할 사실」(수치·단위·연도 + 찾을 숫자 글자)을 한 번 뽑고,
  갈래의 예산 글에 그 숫자 글자가 있는지는 코드로 본다(쉼표·빈칸 뺌). 같은 사실은 한 번만.
산출 <out>/sg1d/task/ · py/ 에서: python -m semgraph.taskeval <oracle|oracle2|contrary|judge|facts|report> --book …
"""

import argparse
import json
import re
from pathlib import Path

import numpy as np

from . import llm
from .sg1d import MODEL, Book, _par

ARMS = ["P-c 벡터+BM25", "P-s P-c+구조", "쪽 퍼넣기", "Q-r 재순위(P-ts 상위 50)", "Q-f 지표 마디 먼저(+P-ts)"]
FACT_ARMS = ARMS + ["P-ts P-t+구조", "Q-x 질의 확장"]
BUDGETS = (4000, 8000, 16000)
# 실비 상한(이 판 0.9 USD) 안에 들게 줄인 판정 묶음 — 오라클 캐시가 안 먹어서(cachedTokens 0, prompt_cache_key 도 0) 4천 자·쪽 퍼넣기·P-c 를 뺌
JUDGE_JOBS = [("P-s P-c+구조", 8000), ("Q-r 재순위(P-ts 상위 50)", 8000), ("Q-f 지표 마디 먼저(+P-ts)", 8000),
              ("P-s P-c+구조", 16000), ("Q-r 재순위(P-ts 상위 50)", 16000)]
VERDICTS = ["supported", "partially_supported", "unsupported", "contradicted", "not_verifiable"]
COARSE = {"supported": "holds", "partially_supported": "holds", "unsupported": "fails", "contradicted": "fails",
          "not_verifiable": "nv"}

GW_INSTR = """너는 ESG 보고서의 그린워싱 점검자다. [자료]는 같은 보고서에서 뽑은 단락들이다(각 줄 [단락id] 글).
[주장]이 [자료]에 비추어 성립하는지 판정하라. 자료에 없는 지식은 쓰지 않는다.
- supported: 자료의 근거(수치·검증·실적)가 주장을 그대로 뒷받침
- partially_supported: 일부만 뒷받침되거나, 범위·조건·기준이 주장보다 좁음
- unsupported: 자료에 주장을 뒷받침할 근거가 없음(주장 문장을 되풀이한 것은 근거가 아님)
- contradicted: 자료에 주장과 어긋나는 사실(다른 수치, 반대 사례, 범위 제외)이 있음
- not_verifiable: 성격상 자료로 확인할 수 없는 주장(포부·의견)
evidence 에는 판정의 핵심 단락 id(최대 6), reason 은 한 문장."""

GW_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["verdict", "evidence", "reason"],
             "properties": {"verdict": {"type": "string", "enum": VERDICTS},
                            "evidence": {"type": "array", "items": {"type": "string"}},
                            "reason": {"type": "string"}}}

CT_INSTR = """[주장]과 같은 보고서의 [단락]들이 있다. 단락 중에 주장과 어긋나거나 주장을 실질적으로 좁히는 사실
(다른 수치, 반대 사례·제재, 검증 범위 제외, 기준·경계 차이)이 있는가? 없으면 contrary=false, ids 빈 목록."""

CT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["contrary", "ids", "what"],
             "properties": {"contrary": {"type": "boolean"}, "ids": {"type": "array", "items": {"type": "string"}},
                            "what": {"type": "string"}}}

FACT_INSTR = """[질의]는 ESG 보고서 원고의 한 절을 쓰라는 일감이고, [단락]은 그 절을 쓰려면 꼭 읽어야 할 근거다.
원고에 들어가야 할 사실을 뽑아라(최대 15). 사실 = 지표·값·단위·연도(목표면 목표연도).
match 에는 이 값을 글에서 찾을 수 있는 숫자 글자를 단락에 적힌 그대로 1~3개(예: 「256,000」「25.6만」).
연도만, 한 자리 수만, 흔한 수(100, 2025 등)만인 것은 match 로 쓰지 않는다 — 그런 사실은 빼라.
같은 값이 여러 단락에 있어도 한 번만."""

FACT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["facts"], "properties": {"facts": {
    "type": "array", "items": {"type": "object", "additionalProperties": False,
                               "required": ["metric", "value", "unit", "year", "match"],
                               "properties": {"metric": {"type": "string"}, "value": {"type": "string"},
                                              "unit": {"type": "string"}, "year": {"type": "string"},
                                              "match": {"type": "array", "items": {"type": "string"}}}}}}}


def _d(b):
    d = b.d / "task"
    d.mkdir(exist_ok=True)
    return d


def _line(p):
    return f"[{p.id}] " + p.text.replace("\n", " / ")


def _budget_text(b, order, budget):
    P = {p.id: p for p in b.ps}
    out, used = [], 0
    for pid in order:
        t = _line(P[pid])
        if used + len(P[pid].text) > budget:
            break
        out.append(t)
        used += len(P[pid].text)
    return "\n".join(out)


def _gw(b):
    return [q for q in b.queries if q["type"] == "greenwash"]


def _respond(*a):
    """분당 토큰 한도(2M) 429 는 기다렸다 다시 — 오라클 한 번이 12만~19만 토큰이라 나란히 넷이면 걸린다(SG1d 첫 판, 응답 17건을 잃음)."""
    import time

    import openai
    for k in range(6):
        try:
            return llm.respond(*a)
        except openai.RateLimitError:
            time.sleep(15 * (k + 1))
    return llm.respond(*a)


def _keep(f, k, v):
    """받은 즉시 한 줄씩 적어 둔다 — 끝에 한꺼번에 쓰다 도중에 죽으면 돈만 나간다."""
    with open(f, "a") as h:
        h.write(json.dumps({"k": k, "v": v}, ensure_ascii=False) + "\n")


def _kept(f):
    return {r["k"]: r["v"] for r in map(json.loads, open(f))} if f.exists() else {}


def _judge(b, material, q, tag):
    note = f"\n(이 주장은 단락 [{q['self_passage']}] 에서 나왔다 — 그 단락은 근거로 쓰지 않는다)" if q.get("self_passage") else ""
    raw, row = _respond(MODEL, GW_INSTR, f"[자료]\n{material}\n\n[주장]\n{q['text']}{note}", GW_SCHEMA, b.out, tag)
    r = json.loads(raw)
    r["usd"] = row["costUsd"]
    return r


def _oracle(b, name):
    """문서 전체를 준다. 하나씩 차례로 부르면 같은 머리가 캐시된다(SG1d 라 단계 실측: 둘째 호출부터 cachedTokens ≈ 입력 전부, 1/10 값).
    다 단계에서 0 이던 것은 나란히 부른 탓으로 보인다."""
    f, part = _d(b) / f"{name}.json", _d(b) / f"{name}.part.jsonl"
    res = json.load(open(f)) if f.exists() else {}
    res.update(_kept(part))
    qs = [q for q in _gw(b) if q["id"] not in res]
    doc = "\n".join(_line(p) for p in b.ps)
    for q in qs:
        r = _judge(b, doc, q, f"sg1d:{name}:{q['id']}")
        _keep(part, q["id"], r)
        res[q["id"]] = r
    json.dump(res, open(f, "w"), ensure_ascii=False, indent=1)
    print(name, len(res), f"${sum(r['usd'] for r in res.values()):.4f}")


def cmd_oracle(b):
    _oracle(b, "oracle")


def cmd_oracle2(b):
    _oracle(b, "oracle2")


def cmd_contrary(b):
    f = _d(b) / "contrary.json"
    res = json.load(open(f)) if f.exists() else {}
    P = {p.id: p for p in b.ps}

    def one(q):
        core = [x["id"] for x in b.gold.get(q["id"], []) if x["label"] == "core" and x["id"] != q.get("self_passage")]
        mat = "\n".join(_line(P[c]) for c in core if c in P)
        raw, row = llm.respond(MODEL, CT_INSTR, f"[단락]\n{mat}\n\n[주장]\n{q['text']}", CT_SCHEMA, b.out,
                               f"sg1d:contrary:{q['id']}")
        r = json.loads(raw)
        r["usd"] = row["costUsd"]
        return q["id"], r

    res.update(dict(_par(one, [q for q in _gw(b) if q["id"] not in res])))
    json.dump(res, open(f, "w"), ensure_ascii=False, indent=1)
    print("contrary", sum(r["contrary"] for r in res.values()), "/", len(res))


def cmd_judge(b):
    f, part = _d(b) / "judge.json", _d(b) / "judge.part.jsonl"
    res = json.load(open(f)) if f.exists() else {}
    res.update(_kept(part))
    orders = json.load(open(b.d / "orders.json"))
    jobs = [(q, a, x) for q in _gw(b) for a, x in JUDGE_JOBS if f"{q['id']}|{a}|{x}" not in res]

    def one(job):
        q, a, x = job
        k, r = f"{q['id']}|{a}|{x}", _judge(b, _budget_text(b, orders[q["id"]][a], x), q, f"sg1d:judge:{q['id']}:{x}")
        _keep(part, k, r)
        return k, r

    res.update(dict(_par(one, jobs, 4)))
    json.dump(res, open(f, "w"), ensure_ascii=False, indent=1)
    print("judge", len(res), f"${sum(r['usd'] for r in res.values()):.4f}")


def _num(s):
    return re.sub(r"[,\s]", "", s)


def cmd_facts(b):
    f = _d(b) / "facts.json"
    res = json.load(open(f)) if f.exists() else {}
    P = {p.id: p for p in b.ps}
    qs = [q for q in b.queries if q["type"] == "drafting" and q["id"] not in res
          and any(x["label"] == "core" for x in b.gold.get(q["id"], []))]

    def one(q):
        core = [x["id"] for x in b.gold[q["id"]] if x["label"] == "core"]
        mat = "\n".join(_line(P[c]) for c in core if c in P)
        raw, row = llm.respond(MODEL, FACT_INSTR, f"[단락]\n{mat}\n\n[질의]\n{q['text']}", FACT_SCHEMA, b.out,
                               f"sg1d:facts:{q['id']}")
        r = json.loads(raw)
        # 정답 단락 글에 실제로 있는 match 만 남긴다(지어낸 글자 거름)
        corpus = _num(mat)
        seen, keep = set(), []
        for x in r["facts"]:
            m = [s for s in x["match"] if len(_num(s)) >= 3 and _num(s) in corpus]
            key = tuple(sorted(_num(s) for s in m))
            if m and key not in seen:
                seen.add(key)
                keep.append({**x, "match": m})
        return q["id"], {"facts": keep, "raw_n": len(r["facts"]), "usd": row["costUsd"]}

    res.update(dict(_par(one, qs)))
    json.dump(res, open(f, "w"), ensure_ascii=False, indent=1)
    print("facts", {k: len(v["facts"]) for k, v in res.items()})


def _boot(d, seed=0):
    d = np.asarray(d, dtype=float)
    s = np.random.default_rng(seed).integers(0, len(d), (5000, len(d)))
    m = d[s].mean(1)
    return float(d.mean()), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def report(b):
    d = _d(b)
    out = {"book": b.name}
    orders = json.load(open(b.d / "orders.json"))
    if (d / "judge.json").exists():
        j = json.load(open(d / "judge.json"))
        o = json.load(open(d / "oracle.json")) if (d / "oracle.json").exists() else {}  # 코스맥스는 오라클 없음(상한)
        ct = json.load(open(d / "contrary.json")) if (d / "contrary.json").exists() else {}
        qs = [q["id"] for q in _gw(b)]
        gw = {}
        for a in ARMS:
            for x in BUDGETS:
                ks = [k for k in qs if f"{k}|{a}|{x}" in j]
                ex = [j[f"{k}|{a}|{x}"]["verdict"] == o[k]["verdict"] for k in ks if k in o]
                co = [COARSE[j[f"{k}|{a}|{x}"]["verdict"]] == COARSE[o[k]["verdict"]] for k in ks if k in o]
                cq = [k for k in ks if ct.get(k, {}).get("contrary")]
                caught = [j[f"{k}|{a}|{x}"]["verdict"] in ("contradicted", "partially_supported", "unsupported") for k in cq]
                gw[f"{a}|{x}"] = {"n": len(ks), "exact": float(np.mean(ex)) if ex else None,
                                  "coarse": float(np.mean(co)) if co else None, "contrary_n": len(cq),
                                  "caught": float(np.mean(caught)) if caught else None}
        out["oracle_verdicts"] = {k: o[k]["verdict"] for k in qs if k in o}
        out["oracle_contrary_caught"] = (lambda cq: float(np.mean([o[k]["verdict"] in ("contradicted", "partially_supported", "unsupported") for k in cq])) if cq else None)(
            [k for k in qs if k in o and ct.get(k, {}).get("contrary")])
        if (d / "oracle2.json").exists():
            o2 = json.load(open(d / "oracle2.json"))
            ks = [k for k in qs if k in o2]
            out["oracle_self"] = {"exact": float(np.mean([o[k]["verdict"] == o2[k]["verdict"] for k in ks])),
                                  "coarse": float(np.mean([COARSE[o[k]["verdict"]] == COARSE[o2[k]["verdict"]] for k in ks])),
                                  "n": len(ks)}
        out["greenwash"] = gw
        out["contrary_claims"] = [k for k in qs if ct.get(k, {}).get("contrary")]
    if (d / "facts.json").exists():
        fx = json.load(open(d / "facts.json"))
        fr = {}
        for a in FACT_ARMS:
            for x in (8000, 16000):
                per = []
                for qid, v in fx.items():
                    if not v["facts"] or a not in orders[qid]:
                        continue
                    t = _num(_budget_text(b, orders[qid][a], x))
                    per.append(np.mean([any(_num(s) in t for s in f_["match"]) for f_ in v["facts"]]))
                fr[f"{a}|{x}"] = {"n": len(per), "mean": float(np.mean(per)) if per else None, "per": per}
        ref = "P-s P-c+구조"
        for a in FACT_ARMS:
            for x in (8000, 16000):
                if a != ref and fr[f"{a}|{x}"]["per"]:
                    dd = [p - r for p, r in zip(fr[f"{a}|{x}"]["per"], fr[f"{ref}|{x}"]["per"])]
                    fr[f"{a}|{x}"]["vs_P-s"] = _boot(dd)
        out["facts"] = fr
        out["facts_n"] = {k: len(v["facts"]) for k, v in fx.items()}
    json.dump(out, open(d / "report.json", "w"), ensure_ascii=False, indent=1)
    return out


def cmd_report(b):
    r = report(b)
    for k, v in r.get("greenwash", {}).items():
        print(k, v)
    print("oracle_self", r.get("oracle_self"), "contrary", r.get("contrary_claims"))
    for k, v in r.get("facts", {}).items():
        print(k, round(v["mean"], 3) if v["mean"] is not None else None, v["n"], v.get("vs_P-s"))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("--book", required=True)
    ap.add_argument("--gold", default="gold-final.json")
    a = ap.parse_args(argv)
    b = Book(a.book, a.gold)
    globals()[f"cmd_{a.cmd}"](b)


if __name__ == "__main__":
    main()
