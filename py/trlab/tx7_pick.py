"""TRX7 — 영문번역 검수 단계 실험: 검수 판 넷·사람 이름 표기 판 셋 (MARC-TICK-510, 이슈 382·383).

쓰는 곳은 OUT7(env TRX7_OUT, 기본 esg-lab/out/trx7) 하나. out/trx1~5 는 읽기만, out/trx6 는 읽지도 않는다.
정답 얼림은 prework/gold-trx7/ (GOLD7): units.json(표본 단위 — 글 없이 id·sha·offset·심은 조각) · real.json(실제 오역 라벨) ·
names.json(이름 표본) · freeze.txt. 지시문(V1·V2·N1·N2)은 얼린 뒤에 쓴다(§1 이미 정해진 것 3).
실호출: 장부 OUT7/calls.jsonl(TRX5 꼴 + estimateUsd·chars·items), 자리 상한 CAPS7 = 모델별(gpt-4.1-mini 0.25 · luna 0.1 · sol 0.15), 합 0.5.
누적 + 잡아 둔 몫 + 예상 > 자리 상한(또는 합)이면 보내지 않음(over_cap). 예상이 안 들면 줄이는 차례(SHRINK)로 단위를 뺀다.

명령(py/ 에서, --group scope3colab, 실호출은 --with openai==3.26.0 + 마크스폰 키):
  python -m trlab.tx7_pick draw                                   → OUT7/draw.json (seed 510, 자리마다 후보 셋 — 손으로 심을 재료)
  python -m trlab.tx7_pick build-units                            → GOLD7/units.json (draw + OUT7/plants.json + OUT7/real-spec.json)
  python -m trlab.tx7_pick freeze                                 → GOLD7/freeze.txt
  python -m trlab.tx7_pick first-run                              → OUT7/first-run.txt (모듈 sha + 지시문 sha + 시각)
  python -m trlab.tx7_pick review <V0r|V0e|V2|V3> <모델> [--split test|dev] [--dry-run] [--ids a,b]  → OUT7/review/<판>-<모델>/<id>.json
  python -m trlab.tx7_pick names <N0|N1|N2> <모델> [--reps 3] [--dry-run]   → OUT7/names/<판>-<모델>/<회차>-<id>.json
  python -m trlab.tx7_pick score                                  → OUT7/score/<판>-<모델>.json (V1 은 V0e 답에서)
  python -m trlab.tx7_pick plan                                   → 남은 자리와 예상(줄이는 차례 적용)
"""

import hashlib
import json
import os
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from trlab import common

ROOT = common.ROOT
OUT7 = Path(os.environ.get("TRX7_OUT", ROOT / "esg-lab" / "out" / "trx7"))
GOLD7 = Path(os.environ.get("TRX7_GOLD", ROOT / "prework" / "gold-trx7"))

MINI, LUNA, SOL = "gpt-4.1-mini", "gpt-6-luna", "gpt-6.1-sol"
CAPS7 = {MINI: 0.25, LUNA: 0.1, SOL: 0.15}
TOTAL_CAP7 = 0.5
EPS = 1e-9  # 부동소수 비교 여유
TOK_PER_CHAR0 = 0.40  # 첫 실측 전(TRX5 luna 중앙값 0.33 의 위쪽, 추측)
OUT_TOK = {"review": {MINI: 250, LUNA: 600, SOL: 600}, "names": {MINI: 80, LUNA: 80, SOL: 80}}  # 출력 어림(추측 — 첫 실측으로 고침)
THREADS = 4
V3_SUBSET = {"planted": 8, "clean": 2, "real": 2}  # sol 부분집합 12(seed 510)
REVIEW_ORDER = ("V2", "V0e", "V0r")  # 4.1-mini 줄이는 차례: 이 차례로 실제 오역 단위를 뺀다


def sha16(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def file_sha16(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:16]


def dump(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    json.dump(obj, open(path, "w"), ensure_ascii=False, indent=1)
    return path


# ---------- 장부(OUT7, 자리 = 모델) — tx5_pick 꼴을 베낌(tx5 것은 out/trx5 에 쓴다) ----------
_LOCK = threading.Lock()
_RESERVED = {MINI: 0.0, LUNA: 0.0, SOL: 0.0}


def calls():
    p = OUT7 / "calls.jsonl"
    return [json.loads(x) for x in open(p) if x.strip()] if p.exists() else []


def site_spent(model, rows=None):
    rows = calls() if rows is None else rows
    return sum(r.get("costUsd", 0) for r in rows if not r.get("dry_run") and r.get("model") == model)


def total_spent(rows=None):
    rows = calls() if rows is None else rows
    return sum(r.get("costUsd", 0) for r in rows if not r.get("dry_run"))


def reserve(model, usd):
    """누적 + 잡아 둔 몫 + 예상 > 자리 상한(또는 합 0.5)이면 False — 보내지 않는다."""
    with _LOCK:
        rows = calls()
        held = sum(_RESERVED.values())
        if site_spent(model, rows) + _RESERVED[model] + usd > CAPS7[model] + EPS or total_spent(rows) + held + usd > TOTAL_CAP7 + EPS:
            return False
        _RESERVED[model] += usd
        return True


def release(model, usd):
    with _LOCK:
        _RESERVED[model] = max(0.0, _RESERVED[model] - usd)


def _append(row):
    with _LOCK:
        OUT7.mkdir(parents=True, exist_ok=True)
        with open(OUT7 / "calls.jsonl", "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def send(model, instr, text, schema, tag, est_usd, **extra):
    """semgraph.llm.respond 를 OUT7/.scratch 로 부르고 줄(+ estimateUsd·chars·items)은 OUT7/calls.jsonl 에. luna·sol 은 effort low,
    gpt-4.1-mini 는 llm.NO_REASONING 이라 reasoning 을 싣지 않는다(temperature 도 안 보냄 — 1차 엔진과 같음)."""
    from semgraph import llm
    scratch = OUT7 / ".scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        raw, row = llm.respond(model, instr, text, schema, scratch, tag, effort="low")
    finally:
        release(model, est_usd)
    row.update(extra, estimateUsd=round(est_usd, 6))
    _append(row)
    return raw, row


def log_skip(model, tag, est_usd, reason, **extra):
    row = {"tag": tag, "model": model, "dry_run": True, "skip": reason, "costUsd": 0.0, "estimateUsd": round(est_usd, 6)}
    row.update(extra)
    _append(row)
    return row


def tok_per_char(model, rows=None):
    """첫 실측 토큰/글자 — 장부의 그 모델 첫 실호출 줄(chars 있음), 없으면 0.40."""
    rows = calls() if rows is None else rows
    mine = [r for r in rows if r.get("model") == model and r.get("chars") and not r.get("dry_run") and r.get("inputTokens")]
    if mine:
        return mine[0]["inputTokens"] / mine[0]["chars"], "measured"
    return TOK_PER_CHAR0, "default"


def out_tok(model, kind, rows=None):
    """출력 어림 — 그 모델·그 갈래의 실측이 있으면 중앙값, 없으면 OUT_TOK."""
    rows = calls() if rows is None else rows
    mine = sorted(r["outputTokens"] for r in rows if r.get("model") == model and not r.get("dry_run")
                  and r.get("outputTokens") and r.get("tag", "").startswith(f"TRX7/{kind}/"))
    return (mine[len(mine) // 2], "measured") if mine else (OUT_TOK[kind][model], "default")


def estimate(model, instr, text, kind="review"):
    """예상 = 글자(지시문 + 입력) × 토큰/글자 + 출력 어림 → (입력 토큰, 출력 토큰, USD, 출처)."""
    from semgraph import llm
    k, src = tok_per_char(model)
    chars = len(instr) + len(text)
    inp = int(chars * k)
    outp, osrc = out_tok(model, kind)
    return inp, outp, llm.cost(model, inp, 0, outp), f"{src}/{osrc}"


def shrink(model, plan, cap_left):
    """plan = [(판, [단위 id], 단위별 예상 USD)] 가 cap_left 안에 들 때까지 줄이는 차례(§1 실호출)로 단위를 뺀다.
    4.1-mini: V2 → V0e → V0r 차례로 실제 오역 단위(cat real)를 뺌 · luna: 심은 단위만 남김 · sol: 12 → 8 → V3 뺌.
    → (줄인 plan, 밟은 단계 목록)."""
    steps = []

    def total(pl):
        return sum(sum(c for _, c in ids) for _, ids in pl) - EPS

    pl = [(v, list(ids)) for v, ids, in plan]
    if total(pl) <= cap_left:
        return pl, steps
    if model == MINI:
        for v in REVIEW_ORDER:
            for i, (vv, ids) in enumerate(pl):
                if vv == v:
                    pl[i] = (vv, [(u, c) for u, c in ids if not u.startswith("real")])
                    steps.append(f"{v}: 실제 오역 단위 뺌")
            if total(pl) <= cap_left:
                return pl, steps
    elif model == LUNA:
        pl = [(v, [(u, c) for u, c in ids if u.startswith("planted") or u.startswith("dev")]) for v, ids in pl]
        steps.append("V2: 심은 단위만")
        if total(pl) <= cap_left:
            return pl, steps
    elif model == SOL:
        pl = [(v, ids[:8]) for v, ids in pl]
        steps.append("V3: 12 → 8")
        if total(pl) <= cap_left:
            return pl, steps
        pl = [(v, []) for v, ids in pl]
        steps.append("V3 뺌")
        return pl, steps
    while total(pl) > cap_left and any(ids for _, ids in pl):  # 그래도 안 들면 뒤에서부터 하나씩
        for i in range(len(pl) - 1, -1, -1):
            if pl[i][1]:
                pl[i] = (pl[i][0], pl[i][1][:-1])
                break
        steps.append("뒤에서 하나 뺌")
    return pl, steps


# ---------- 정답: 단위 짓기 ----------
def load_units(split=None):
    us = json.load(open(GOLD7 / "units.json"))["units"]
    return [u for u in us if split is None or u["split"] == split]


def build_units():
    """OUT7/draw.json(후보) + OUT7/plants.json(손으로 심은 before/after, git 밖) + OUT7/real-spec.json(실제 오역의 틀린 글, git 밖)
    → GOLD7/units.json(글 없음) + GOLD7/real.json. 같은 짝이 두 단위에 들어가면 ValueError."""
    from trlab import review_probe as R
    from trlab.glossary_check import load_gloss
    draw = json.load(open(OUT7 / "draw.json"))
    plants = json.load(open(OUT7 / "plants.json"))
    G = {k: load_gloss(k)["items"] for k in ("sam", "cos", "kb")}
    units, seen = [], set()
    n = {"planted": 0, "clean": 0, "dev": 0}
    for s in draw["slots"]:
        spec = plants.get(s["slot"], {"cand": 0})
        c = s["cands"][spec.get("cand", 0)]
        key = tuple((tuple(p["ko"]), tuple(p["en"])) for p in c["pairs"])
        if key in seen:
            raise ValueError(f"{s['slot']}: 같은 짝이 다른 단위에 있다")
        seen.add(key)
        planted = None
        if s["cat"] != "clean":
            if "omit_sent" in spec:
                sents = R._sents(c["en"])
                sent = sents[spec["omit_sent"]]
                i = c["en"].index(sent)
                j = i + len(sent)
                while j < len(c["en"]) and c["en"][j] == " ":
                    j += 1
                planted = {"cat": "omit", "before": c["en"][i:j], "after": ""}
            else:
                planted = {"cat": s["cat"], "before": spec["before"], "after": spec["after"]}
        kind = "dev" if s["split"] == "dev" else ("clean" if s["cat"] == "clean" else "planted")
        n[kind] += 1
        uid = f"{kind}-{n[kind]:02d}-{s['book']}-{s['cat']}"
        units.append(R.make_unit(uid, s["book"], s["split"], s["kind"], s["cat"], c["pairs"], c["page"], c["ko"], c["en"],
                                 planted, G[s["book"]]))
        units[-1]["slot"] = s["slot"]
        units[-1]["cand"] = spec.get("cand", 0)
    # 실제 오역(엔진 산출 sam)
    real_spec = json.load(open(OUT7 / "real-spec.json"))
    real_rows, k = [], 0
    for r in real_spec["blocks"]:
        ko, en = R._texts_engine(r["model"], r["pair"])
        terms = []
        for t in r["terms"]:
            gi = next(i for i, it in enumerate(G["sam"]) if it["kr"] == t["kr"])
            row = {"gidx": gi, "kr_sha": sha16(t["kr"]), "label": t["label"], "why": t["why"]}
            if t["label"] == "real":
                i = en.index(t["wrong"])
                row["wrong_at"] = [i, i + len(t["wrong"])]
            terms.append(row)
        real_rows.append({"model": r["model"], "pair": r["pair"], "page": int(r["pair"].split(":")[0]), "ko_sha": sha16(ko),
                          "en_sha": sha16(en), "terms": terms, "unit": None})
        if any(t["label"] == "real" for t in terms):
            k += 1
            uid = f"real-{k:02d}-sam-{r['model'].replace('gpt-', '').replace('.', '')}-p{r['pair'].split(':')[0]}"
            real_rows[-1]["unit"] = uid
            units.append(R.make_unit(uid, "sam", "test", "paragraph", "real", [], real_rows[-1]["page"], ko, en, None, G["sam"],
                                     engine={"model": r["model"], "pair": r["pair"]},
                                     real=[t for t in terms if t["label"] == "real"]))
    gold_sha = {k: file_sha16(common.GOLD / "glossary" / f"{k}.json") for k in G}
    dump(GOLD7 / "units.json", {"seed": draw["seed"], "glossary_sha": gold_sha, "how": "draw(seed 510) + 손 심기(plants.json) + 엔진 산출 재라벨; "
                                "글은 out/trx1 parse·out/trpdf/res 에서 materialize 로 다시 읽는다(sha 대조)", "units": units})
    dump(GOLD7 / "real.json", {"how": "TRX1 out/trx1/glossary/check-sam-engine-*.json 의 위반 18 을 §T-b 기준으로 블록마다 다시 라벨. "
                               "용어는 gold-trx1 glossary 의 index(gidx)와 sha16, 틀린 자리는 엔진 EN 의 글자 offset", "blocks": real_rows,
                               "n_real_units": k})
    return units, real_rows


def freeze():
    files = sorted(p for p in GOLD7.glob("*.json"))
    lines = [f"# TRX7 표본·실제 오역 라벨·이름 표본 얼림 {time.strftime('%Y-%m-%d %H:%M:%S')} — V1·V2·N1·N2 지시문을 쓰기 전"]
    lines += [f"{file_sha16(p)}  {p.name}" for p in files]
    (GOLD7 / "freeze.txt").write_text("\n".join(lines) + "\n")
    return lines


def first_run():
    from trlab import names_probe as N
    from trlab import review_probe as R
    lines = [f"# TRX7 지시문·채점 확정 {time.strftime('%Y-%m-%d %H:%M:%S')} — 시험 셋 전량 전",
             f"{file_sha16(Path(R.__file__))}  review_probe.py", f"{file_sha16(Path(N.__file__))}  names_probe.py",
             f"{file_sha16(Path(__file__))}  tx7_pick.py", f"{sha16(R.V2_INSTR)}  V2_INSTR ({len(R.V2_INSTR)}자)",
             f"{sha16(N.N1_RULE)}  N1_RULE", f"{sha16(N.N2_HEAD)}  N2_HEAD"]
    (OUT7 / "first-run.txt").write_text("\n".join(lines) + "\n")
    return lines


# ---------- (1) 검수 판 ----------
def v3_subset(units, seed=510):
    import random
    rnd = random.Random(seed)
    out = []
    for cat, n in V3_SUBSET.items():
        pool = [u for u in units if u["split"] == "test" and (u["cat"] == cat if cat != "planted" else u["cat"] in common_cats())]
        out += rnd.sample(pool, min(n, len(pool)))
    return out


def common_cats():
    from trlab.review_probe import CATS
    return set(CATS)


def run_review(variant, model, split="test", dry_run=False, ids=None, threads=THREADS):
    from trlab import review_probe as R
    from trlab.glossary_check import load_gloss
    G = {k: load_gloss(k)["items"] for k in ("sam", "cos", "kb")}
    units = load_units(split)
    if variant == "V3":
        units = v3_subset(units)
    if ids:
        units = [u for u in units if u["id"] in set(ids)]
    folder = OUT7 / "review" / f"{variant}-{model}"
    kind = "review"

    def one(u):
        mat = R.materialize(u, G[u["book"]])
        instr, text, schema = R.build_prompt(variant, mat)
        inp, outp, usd, src = estimate(model, instr, text, kind)
        tag = f"TRX7/review/{variant}/{model}/{u['id']}"
        row = {"id": u["id"], "variant": variant, "model": model, "cat": u["cat"], "split": u["split"],
               "chars": len(instr) + len(text), "est": {"inp": inp, "outp": outp, "usd": round(usd, 6), "src": src}}
        p = folder / f"{u['id']}.json"
        if p.exists() and not dry_run:
            return dict(json.load(open(p)), status="exists")
        if dry_run:
            log_skip(model, tag, usd, "dry_run", chars=row["chars"], items=1)
            return dict(row, status="dry_run")
        if not reserve(model, usd):
            log_skip(model, tag, usd, "over_cap", chars=row["chars"], items=1)
            dump(p, dict(row, status="over_cap"))
            return dict(row, status="over_cap")
        try:
            raw, lrow = send(model, instr, text, schema, tag, usd, chars=row["chars"], items=1)
        except Exception as e:  # noqa: BLE001 — 부재는 갈래로(call_failed)
            out = dict(row, status="call_failed", error=f"{type(e).__name__}: {str(e)[:300]}")
            dump(p, out)
            return out
        ans = R.read_answer(variant, raw, mat)
        out = dict(row, status="ok" if ans["parsed"] else "parse_failed", call=lrow, prompt={"instructions": instr, "input": text},
                   raw=raw, answer=ans, score=R.score_unit(mat, ans), planted=mat["planted"], wrong=mat["wrong"])
        dump(p, out)
        return out

    with ThreadPoolExecutor(max_workers=threads) as ex:
        res = list(ex.map(one, units))
    summ = {"variant": variant, "model": model, "split": split, "n": len(res), "status": dict(Counter(r["status"] for r in res)),
            "est_usd": round(sum(r["est"]["usd"] for r in res), 6),
            "usd": round(sum(r.get("call", {}).get("costUsd", 0) for r in res), 6)}
    dump(folder / "_summary.json", summ)
    return res, summ


def score_all():
    """OUT7/review/<판>-<모델>/*.json → OUT7/score/<판>-<모델>.json. V1 은 V0e 답을 다시 읽는다(호출 0)."""
    from trlab import review_probe as R
    from trlab.glossary_check import load_gloss
    G = {k: load_gloss(k)["items"] for k in ("sam", "cos", "kb")}
    U = {u["id"]: u for u in load_units()}
    outs = {}
    for folder in sorted((OUT7 / "review").glob("V*-*")):
        variant, model = folder.name.split("-", 1)
        rows = []
        for p in sorted(folder.glob("*.json")):
            if p.name.startswith("_"):
                continue
            d = json.load(open(p))
            if d.get("status") not in ("ok", "parse_failed"):
                rows.append({"id": d["id"], "cat": d["cat"], "split": d["split"], "status": d["status"], "flagged": False,
                             "hit": "miss" if d["cat"] != "clean" else None, "false_alarm": False, "usd": 0.0})
                continue
            mat = R.materialize(U[d["id"]], G[U[d["id"]]["book"]])
            ans = R.read_answer(variant, d["raw"], mat)
            sc = R.score_unit(mat, ans)
            sc.update(status=d["status"], usd=d.get("call", {}).get("costUsd", 0.0),
                      inputTokens=d.get("call", {}).get("inputTokens"), outputTokens=d.get("call", {}).get("outputTokens"))
            rows.append(sc)
            if variant == "V0e":
                a1 = R.read_answer("V1", d["raw"], mat)
                s1 = R.score_unit(mat, a1)
                s1.update(status=d["status"], usd=0.0)
                outs.setdefault(f"V1-{model}", []).append(s1)
        outs[f"{variant}-{model}"] = rows
    for name, rows in outs.items():
        dump(OUT7 / "score" / f"{name}.json", {"name": name, "rows": rows, "n": len(rows)})
    return outs


# ---------- (2) 이름 판 ----------
def run_names(variant, model, reps=3, dry_run=False, threads=THREADS):
    from trlab import names_probe as N
    items = json.load(open(GOLD7 / "names.json"))["items"]
    folder = OUT7 / "names" / f"{variant}-{model}"
    jobs = [(rep, it) for rep in range(1, reps + 1) for it in items]

    def one(job):
        rep, it = job
        instr, text, schema = N.build_prompt(variant, it)
        inp, outp, usd, src = estimate(model, instr, text, "names")
        tag = f"TRX7/names/{variant}/{model}/{rep}/{it['id']}"
        row = {"id": it["id"], "rep": rep, "variant": variant, "model": model, "chars": len(instr) + len(text),
               "est": {"inp": inp, "outp": outp, "usd": round(usd, 6), "src": src}}
        p = folder / f"{rep}-{it['id']}.json"
        if p.exists() and not dry_run:
            return dict(json.load(open(p)), status="exists")
        if dry_run:
            log_skip(model, tag, usd, "dry_run", chars=row["chars"], items=len(it["names"]))
            return dict(row, status="dry_run")
        if not reserve(model, usd):
            log_skip(model, tag, usd, "over_cap", chars=row["chars"], items=len(it["names"]))
            dump(p, dict(row, status="over_cap"))
            return dict(row, status="over_cap")
        try:
            raw, lrow = send(model, instr, text, schema, tag, usd, chars=row["chars"], items=len(it["names"]))
        except Exception as e:  # noqa: BLE001
            out = dict(row, status="call_failed", error=f"{type(e).__name__}: {str(e)[:300]}")
            dump(p, out)
            return out
        ans = N.read_answer(raw)
        out = dict(row, status="ok" if ans is not None else "parse_failed", call=lrow, prompt={"instructions": instr, "input": text},
                   raw=raw, answer=ans)
        dump(p, out)
        return out

    with ThreadPoolExecutor(max_workers=threads) as ex:
        res = list(ex.map(one, jobs))
    summ = {"variant": variant, "model": model, "reps": reps, "n": len(res), "status": dict(Counter(r["status"] for r in res)),
            "est_usd": round(sum(r["est"]["usd"] for r in res), 6),
            "usd": round(sum(r.get("call", {}).get("costUsd", 0) for r in res), 6)}
    dump(folder / "_summary.json", summ)
    return res, summ


def score_names():
    from trlab import names_probe as N
    items = {it["id"]: it for it in json.load(open(GOLD7 / "names.json"))["items"]}
    outs = {}
    for folder in sorted((OUT7 / "names").glob("N*-*")):
        runs = {}
        for p in sorted(folder.glob("*.json")):
            if p.name.startswith("_"):
                continue
            d = json.load(open(p))
            runs.setdefault(d["id"], {})[d["rep"]] = d.get("answer") if d.get("status") == "ok" else None
        outs[folder.name] = N.score(items, runs)
        dump(OUT7 / "score" / f"{folder.name}.json", outs[folder.name])
    return outs


# ---------- 계획 ----------
def plan_text():
    rows = calls()
    lines = []
    for m in (MINI, LUNA, SOL):
        k, src = tok_per_char(m, rows)
        lines.append(f"{m}: 누적 {site_spent(m, rows):.4f} / {CAPS7[m]} USD · 토큰/글자 {k:.3f}({src}) · 출력 "
                     + " ".join(f"{kind} {out_tok(m, kind, rows)[0]}" for kind in ("review", "names")))
    lines.append(f"합 {total_spent(rows):.4f} / {TOTAL_CAP7} · 장부 {len(rows)}줄(dry_run {sum(1 for r in rows if r.get('dry_run'))})")
    return "\n".join(lines)


def main(argv):
    cmd = argv[0]
    if cmd == "draw":
        from trlab import review_probe as R
        d = R.draw()
        p = dump(OUT7 / "draw.json", d)
        print(p, len(d["slots"]), Counter((s["split"], s["cat"]) for s in d["slots"]))
    elif cmd == "build-units":
        us, rr = build_units()
        print(GOLD7 / "units.json", len(us), Counter(u["cat"] for u in us), "real blocks", len(rr))
    elif cmd == "freeze":
        print("\n".join(freeze()))
    elif cmd == "first-run":
        print("\n".join(first_run()))
    elif cmd == "review":
        variant, model = argv[1], argv[2]
        split = argv[argv.index("--split") + 1] if "--split" in argv else "test"
        ids = argv[argv.index("--ids") + 1].split(",") if "--ids" in argv else None
        _, s = run_review(variant, model, split, "--dry-run" in argv, ids)
        print(json.dumps(s, ensure_ascii=False))
    elif cmd == "names":
        variant, model = argv[1], argv[2]
        reps = int(argv[argv.index("--reps") + 1]) if "--reps" in argv else 3
        _, s = run_names(variant, model, reps, "--dry-run" in argv)
        print(json.dumps(s, ensure_ascii=False))
    elif cmd == "score":
        a = score_all()
        b = score_names()
        print({k: len(v) for k, v in a.items()}, {k: v.get("n_occ") for k, v in b.items()})
    elif cmd == "plan":
        print(plan_text())
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
