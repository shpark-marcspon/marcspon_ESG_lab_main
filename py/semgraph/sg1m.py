"""SG1m — 지금 원고작성 map 과 F-b′ 비교(현대건설, 얼린 시험지). 계획은 TICK-429 노트.

map 은 esg-app 코드를 읽기 전용으로 그대로 부른다(PYTHONPATH + 바이트코드 끔): 쪽 글·묶음·지시문·재료.
잎 = 원고 질의 15. 재료 글자와 같은 글자의 F-b′·P-s 를 같은 정답·사실로 채점한다.
py/ 에서: python -m semgraph.sg1m <size|run|eval>
산출 <out>/sg1m/: map.part.jsonl · eval.json
"""

import argparse
import json
import re
import time
from collections import defaultdict

import numpy as np

from esgapi.core.documents.view import DocArtifactView
from esgapi.drafting.engines.v1 import mapping, pages as PG
from esgapi.drafting.engines.v1 import prompts as P1
from esgapi.drafting.engines.v2 import material as M
from esgapi.drafting.engines.v2 import prompts as P2
from esgapi.drafting.engines.v2.texts import DEFAULT_TEXTS
from esgapi.drafting.tools import blocks_text

from . import llm
from .evaluate2 import boot
from .sg1i import C, PS, _arm_orders, _strict, _within
from .taskeval import _keep, _kept, _num

MODEL = "gpt-4.1-mini"  # esg-app 경로 light-openai-41
PRICE = (0.4, 0.1, 1.6)  # USD/MTok 입력·캐시·출력 — esg-app core/llm/data/models.json (2026-10-16 확인)
CAP_USD = 0.11
FB = "F-b′"
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["pages"], "properties": {"pages": {
    "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["page", "keys"], "properties": {
        "page": {"type": "integer"}, "keys": {"type": "array", "items": {"type": "string"}}}}}}}


def setup():
    c = C()
    c.m = c.d.parent / "sg1m"
    c.m.mkdir(exist_ok=True)
    c.view = DocArtifactView(artifact_id=0, document=json.load(open(c.d.parent / "sg1g/app-l2/document.json")), char_places=None)
    c.dq = [q for q in c.qs if q["type"] == "drafting"]
    c.leaves = []
    for q in c.dq:
        title, _, req = q["text"].partition(":")
        c.leaves.append((q["id"], (title.strip(),), req.strip() or q["text"]))
    c.groups = PG.batches(PG.pages_of(blocks_text.page_texts(c.view)))
    return c


def request(c, batch):
    return P2.map_texts(DEFAULT_TEXTS, P1.outline_lines(c.leaves), PG.batch_text(batch))


def cmd_luna(c):
    """같은 묶음·지시문, 모델만 luna(다른 luna 호출과 같은 effort low). 산출 map-luna.part.jsonl."""
    cap = 0.0266  # SG1d 이후 상한 3.0 까지 남은 몫(노트)
    f = c.m / "map-luna.part.jsonl"
    for i, batch in enumerate(c.groups):
        k = str(i)
        if k in _kept(f):
            continue
        system, user = request(c, batch)
        raw, row = llm.respond("gpt-6-luna", system, user, SCHEMA, c.root, f"sg1l:map:{i}")
        if row["status"] != "completed":
            raise SystemExit(f"묶음 {i} status={row['status']}")
        _keep(f, k, {"answer": json.loads(raw), "pages": [p.number for p in batch], "usd": row["costUsd"]})
        spent = sum(v["usd"] for v in _kept(f).values())
        print(i, f"${row['costUsd']:.5f} 입력 {row['inputTokens']} 캐시 {row['cachedTokens']} 출력 {row['outputTokens']} 누적 {spent:.4f}", flush=True)
        if spent + 0.002 > cap and len(_kept(f)) < len(c.groups):
            raise SystemExit(f"{len(_kept(f))} 묶음 뒤 {spent:.4f} USD — 다음 호출이 상한 {cap} 을 넘을 수 있어 멈춤")


def cmd_size(c):
    sizes = [sum(map(len, request(c, b))) for b in c.groups]
    # 한글 혼합 글은 글자당 약 0.45~0.6 토큰(앞 호출 실측 범위). 위쪽으로 0.6 을 쓴다.
    est = sum(sizes) * 0.6 * PRICE[0] / 1e6 + len(sizes) * 1500 * PRICE[2] / 1e6
    print(f"쪽 {sum(len(b) for b in c.groups)} · 묶음 {len(sizes)} · 호출 글자 {sum(sizes):,} · 위쪽 어림 {est:.4f} USD")


def _spent(c):
    f = c.root / "calls.jsonl"
    return sum(r.get("costUsd", 0) for r in map(json.loads, open(f)) if str(r.get("tag", "")).startswith("sg1m:"))


def cmd_run(c):
    from openai import OpenAI

    client = OpenAI(timeout=300.0, max_retries=2)
    f = c.m / "map.part.jsonl"
    have = _kept(f)
    for i, batch in enumerate(c.groups):
        k = str(i)
        if k in have:
            continue
        if _spent(c) > CAP_USD:
            raise SystemExit(f"상한 넘음 {_spent(c):.4f}")
        system, user = request(c, batch)
        t0 = time.time()
        r = client.responses.create(model=MODEL, instructions=system, input=user, temperature=0.0, max_output_tokens=4000,
                                    text={"format": {"type": "json_schema", "name": "page_map", "schema": SCHEMA, "strict": True}})
        u = r.usage
        cached = getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0
        usd = ((u.input_tokens - cached) * PRICE[0] + cached * PRICE[1] + u.output_tokens * PRICE[2]) / 1e6
        llm._log(c.root, {"tag": f"sg1m:map:{i}", "model": r.model, "key": "marcspon", "inputTokens": u.input_tokens, "cachedTokens": cached,
                          "outputTokens": u.output_tokens, "costUsd": usd, "seconds": round(time.time() - t0, 2), "status": r.status})
        if r.status != "completed":  # 앱은 잘린 답을 실패로 친다 — 답을 지어내지 않고 멈춘다
            raise SystemExit(f"묶음 {i} status={r.status}")
        _keep(f, k, {"answer": json.loads(r.output_text), "pages": [p.number for p in batch], "usd": usd})
        done = len(_kept(f))
        if _spent(c) / done * len(c.groups) > CAP_USD:  # 끝까지 못 갈 판이면 돈만 쓰지 않는다 — 결과가 묶음 전부를 요구한다
            raise SystemExit(f"{done} 묶음 뒤 어림 {_spent(c) / done * len(c.groups):.4f} USD 가 상한을 넘어 멈춤")
        print(i, len(batch), f"${usd:.5f} 입력 {u.input_tokens} 출력 {u.output_tokens} 누적 {_spent(c):.4f}", flush=True)


def map_material(c, name="map"):
    got = _kept(c.m / f"{name}.part.jsonl")
    assert len(got) == len(c.groups), f"묶음 {len(got)}/{len(c.groups)}"
    leaf_keys = [x[0] for x in c.leaves]
    page_keys, warns = {}, []
    for v in got.values():
        found, w = mapping.map_batch(v["answer"], batch_pages=v["pages"], leaves=leaf_keys)
        warns += w
        for p, ks in found.items():
            page_keys.setdefault(p, []).extend(ks)
    lp = mapping.leaf_pages(page_keys, leaf_keys)
    return lp, {k: M.material(lp[k], c.view) for k in leaf_keys}, warns


def _text(mat):
    return "\n".join(b.text for b in mat.given.values())


def cmd_eval_luna(c):
    """mini map·luna map 을 같은 잣대로. 짝 차이는 luna − mini, F-b′ 는 luna 재료와 같은 글자."""
    facts = _kept(c.d / "facts.part.jsonl")
    orders = _arm_orders(c)
    res, per = {}, {}
    for name in ("map", "map-luna"):
        lp, mats, warns = map_material(c, name)
        rows = {}
        for q in c.dq:
            k = q["id"]
            core = {x["id"] for x in c.gold.get(k, []) if x["label"] == "core"}
            if not core:
                continue
            mat, pages = mats[k], set(lp[k])
            hit = {p for p in core if set(c.P[p].pages) & pages and set(c.P[p].members) & set(mat.given)}
            page = {p for p in core if set(c.P[p].pages) & pages}
            txt = _text(mat)
            fs = (facts.get(k) or {}).get("facts") or []
            sc = lambda t: float(np.mean([any(_strict(s, _num(t)) for s in f["match"]) for f in fs]))
            w = _within(orders[k][FB], c.L, len(txt))
            rows[k] = {"핵심": len(hit) / len(core), "쪽만": len(page) / len(core), "사실": sc(txt), "글자": len(txt), "쪽": len(pages), "잘림": mat.cut,
                       "F핵심": len(set(w) & core) / len(core), "F사실": sc(" ".join(c.P[p].text for p in w))}
        per[name] = rows
        res[name] = {m: float(np.mean([r[m] for r in rows.values()])) for m in ("핵심", "쪽만", "사실", "글자", "쪽", "F핵심", "F사실")}
        res[name].update({"글자 중앙": float(np.median([r["글자"] for r in rows.values()])), "잘림": sum(r["잘림"] for r in rows.values()), "경고": len(warns), "n": len(rows)})
    rng = np.random.default_rng(0)
    for m in ("핵심", "쪽만", "사실"):
        res[f"luna − mini {m}"] = list(boot([per["map-luna"][k][m] - per["map"][k][m] for k in per["map"]], rng))
    for m in ("핵심", "사실"):
        res[f"F-b′ − luna {m}(같은 글자)"] = list(boot([per["map-luna"][k]["F" + m] - per["map-luna"][k][m] for k in per["map"]], rng))
    res["USD"] = {n: sum(v["usd"] for v in _kept(c.m / f"{n}.part.jsonl").values()) for n in ("map", "map-luna")}
    res["per"] = {k: {n: {m: per[n][k][m] for m in ("핵심", "사실", "글자", "쪽", "잘림")} for n in per} for k in per["map"]}
    json.dump(res, open(c.m / "eval-luna.json", "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "per"}, ensure_ascii=False))
    for k, v in res["per"].items():
        print(k, "mini", v["map"], "luna", v["map-luna"])


def cmd_eval(c):
    lp, mats, warns = map_material(c)
    facts = _kept(c.d / "facts.part.jsonl")
    orders = _arm_orders(c)
    rows = defaultdict(dict)
    for q in c.dq:
        k = q["id"]
        core = {x["id"] for x in c.gold.get(k, []) if x["label"] == "core"}
        if not core:
            continue
        mat = mats[k]
        given, pages = set(mat.given), set(lp[k])
        hit = {p for p in core if set(c.P[p].pages) & pages and set(c.P[p].members) & given}
        pagehit = {p for p in core if set(c.P[p].pages) & pages}
        txt = _text(mat)
        n = len(txt)
        fs = (facts.get(k) or {}).get("facts") or []
        fsc = lambda t: float(np.mean([any(_strict(s, _num(t)) for s in f["match"]) for f in fs])) if fs else None
        rows["map"][k] = {"핵심": len(hit) / len(core), "핵심(쪽만)": len(pagehit) / len(core), "글자": n, "쪽": len(pages), "사실": fsc(txt), "잘림": mat.cut}
        for a in (FB, PS):
            for x, nm in ((n, "같은글자"), (8000, "8천"), (16000, "1.6만")):
                w = _within(orders[k][a], c.L, x)
                rows[a][k + nm] = {"핵심": len(set(w) & core) / len(core), "사실": fsc(" ".join(c.P[p].text for p in w))}
    ks = sorted(rows["map"], key=lambda s: int(s[1:]))
    rng = np.random.default_rng(0)
    res = {"n": len(ks), "warnings": len(warns), "cut": sum(rows["map"][k]["잘림"] for k in ks),
           "map": {m: float(np.mean([rows["map"][k][m] for k in ks])) for m in ("핵심", "핵심(쪽만)", "글자", "쪽")},
           "map 글자 중앙": float(np.median([rows["map"][k]["글자"] for k in ks])), "map 쪽 범위": [min(rows["map"][k]["쪽"] for k in ks), max(rows["map"][k]["쪽"] for k in ks)]}
    fk = [k for k in ks if rows["map"][k]["사실"] is not None]
    res["map"]["사실"] = float(np.mean([rows["map"][k]["사실"] for k in fk]))
    res["n 사실"] = len(fk)
    for a in (FB, PS):
        for nm in ("같은글자", "8천", "1.6만"):
            res[f"{a} {nm}"] = {m: float(np.mean([rows[a][k + nm][m] for k in (ks if m == "핵심" else fk)])) for m in ("핵심", "사실")}
        for m, kk, base in (("핵심", ks, "핵심"), ("사실", fk, "사실")):
            d = [rows[a][k + "같은글자"][m] - rows["map"][k][base] for k in kk]
            mean, lo, hi = boot(d, rng)
            res[f"{a} − map {m}(같은 글자)"] = [mean, lo, hi, sum(x > 0 for x in d), sum(x < 0 for x in d)]
        d = [rows[a][k + "같은글자"]["핵심"] - rows["map"][k]["핵심(쪽만)"] for k in ks]
        res[f"{a} − map 핵심(쪽만 넉넉히)"] = list(boot(d, rng))
    res["per"] = {k: {"map": rows["map"][k], FB: rows[FB][k + "같은글자"], "F8": rows[FB][k + "8천"], "F16": rows[FB][k + "1.6만"]} for k in ks}
    # 비용
    calls = [json.loads(x) for x in open(c.root / "calls.jsonl")]
    res["map 호출 USD"] = sum(r["costUsd"] for r in calls if str(r.get("tag", "")).startswith("sg1m:"))
    usd = lambda f: [v["usd"] for v in _kept(f).values() if "usd" in v]
    pb, rr = usd(c.d / "plan-b.part.jsonl"), usd(c.d / "rerank-u.part.jsonl")
    res["F-b′ 질의당 USD"] = {"갈래": float(np.mean(pb)), "재순위(F-g1 후보 합집합이라 위쪽)": float(np.mean(rr))}
    json.dump(res, open(c.m / "eval.json", "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "per"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("cmd", choices=["size", "run", "eval", "luna", "eval-luna"])
    x = a.parse_args()
    ctx = setup()
    {"size": cmd_size, "run": cmd_run, "eval": cmd_eval, "luna": cmd_luna, "eval-luna": cmd_eval_luna}[x.cmd](ctx)
