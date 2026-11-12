"""TRX10 — 블록 체크리스트 검수(V2)를 sam 전량에 · 누락 규칙 되묻기 · 묶음 호출 (MARC-TICK-514, 이슈 382).

쓰는 곳은 OUT10(env TRX10_OUT, 기본 esg-lab/out/trx10) 하나. out/trx1~7 는 읽기만, out/trx8·trx9 는 읽지도 않는다.
새 라벨은 GOLD10(env TRX10_GOLD, 기본 prework/gold-trx10): flags.json(걸림 표본 60) · omit.json(문장 수 어긋남 표본 40) · freeze.txt.
실호출: 장부 OUT10/calls.jsonl(TRX7 꼴 + elapsed_ms·site·unit_ids), 자리 셋은 태그 앞머리로 — CAPS10 luna-full 0.45(full·bundle 의 luna) ·
mini 0.25(4.1-mini 전부) · luna-reask 0.1(reask 의 luna), 합 0.8. 누적 + 잡아 둔 몫 + 예상 > 자리 상한(또는 합)이면 보내지 않음(over_cap).
tx7_pick.send/reserve 는 out/trx7 장부에 쓰므로 부르지 않고 같은 꼴을 베꼈다(발주서 ★1). 동시 호출 없음(THREADS = 1 — 지연을 재기 위해).

명령(py/ 에서, --group scope3colab, 실호출은 --with openai==3.26.0 + 마크스폰 키):
  python -m trlab.tx10_pick units                         → OUT10/units.json (sam 짝 1,391, 글 없음)
  python -m trlab.tx10_pick first-run                     → OUT10/first-run.txt (모듈 sha + V2_INSTR·REASK_INSTR sha + 규칙 상수 + 시각)
  python -m trlab.tx10_pick bundle <모델> [--dry-run]      → OUT10/bundle/<모델>/b<n>.json + units/<id>.json (TRX7 38 단위, seed 514)
  python -m trlab.tx10_pick full <모델> [--n 100] [--strata] [--dry-run]   → OUT10/full/<모델>/<id>.json
                                                            --n 100: 쪽 차례 처음 100 짝(선실측) · --strata: 층화 300 · 없으면 plan 이 줄인 전량
  python -m trlab.tx10_pick plan                          → 100 짝 실측으로 토큰/글자·출력 다시 셈, 남은 짝 예상, 넘으면 쪽 차례로 줄임
  python -m trlab.tx10_pick reask [--dry-run] [--source trx7|full]  → OUT10/reask/gpt-6-luna/<id>.json (규칙 걸린 블록만)
  python -m trlab.tx10_pick pick-labels                   → OUT10/labels/{flags,omit}-sample.json (seed 514 — 라벨은 손으로 GOLD10 에)
  python -m trlab.tx10_pick freeze                        → GOLD10/freeze.txt
  python -m trlab.tx10_pick score                         → OUT10/score/{full-<모델>,bundle-<모델>,omit,flags}.json
"""

import hashlib
import json
import os
import re
import sys
import threading
import time
from collections import Counter
from pathlib import Path

from trlab import common

ROOT = common.ROOT
OUT10 = Path(os.environ.get("TRX10_OUT", ROOT / "esg-lab" / "out" / "trx10"))
GOLD10 = Path(os.environ.get("TRX10_GOLD", ROOT / "prework" / "gold-trx10"))
OUT7 = Path(os.environ.get("TRX7_OUT", ROOT / "esg-lab" / "out" / "trx7"))  # 읽기만(낱개 V2 채점)

MINI, LUNA = "gpt-4.1-mini", "gpt-6-luna"
SITES = ("luna-full", "mini", "luna-reask")
CAPS10 = {"luna-full": 0.45, "mini": 0.25, "luna-reask": 0.1}
TOTAL_CAP10 = 0.8
EPS = 1e-9
THREADS = 1  # 순차 — 지연을 재기 위해(발주서 §1 5)
BOOK = "sam"
PRE_N = 100  # 전량 선실측 짝 수(쪽 차례)
STRATA_N = 300
# 첫 실측 전 상수 — TRX7 실측(band-reports/archive/TRX7.md ③: 토큰/글자 중앙값 4.1-mini 0.286 · luna 0.366, 출력 토큰 중앙값 63 · 161, 시험 셋 50)
TOK_PER_CHAR0 = {MINI: 0.286, LUNA: 0.366}
OUT_TOK0 = {MINI: 63, LUNA: 161}  # 블록 하나의 출력 어림(묶음은 × 블록 수)
TAG_RE = re.compile(r"^TRX10/(full|bundle|reask)/([^/]+)/")


def sha16(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def file_sha16(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:16]


def dump(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    json.dump(obj, open(path, "w"), ensure_ascii=False, indent=1)
    return path


def median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


# ---------- 장부(OUT10, 자리 = 태그 앞머리) — tx7_pick 꼴을 베낌(그것은 out/trx7 에 쓴다, ★1) ----------
_LOCK = threading.Lock()
_RESERVED = {s: 0.0 for s in SITES}


def site_of(tag, model=None):
    """TRX10/full|bundle/gpt-6-luna/… → luna-full · 4.1-mini 전부 → mini · TRX10/reask/gpt-6-luna/… → luna-reask. 그 밖은 None."""
    m = TAG_RE.match(tag or "")
    if not m:
        return None
    kind, mdl = m.group(1), m.group(2)
    if mdl == MINI:
        return "mini"
    if mdl == LUNA:
        return "luna-reask" if kind == "reask" else "luna-full"
    return None


def calls():
    p = OUT10 / "calls.jsonl"
    return [json.loads(x) for x in open(p) if x.strip()] if p.exists() else []


def site_spent(site, rows=None):
    rows = calls() if rows is None else rows
    return sum(r.get("costUsd", 0) for r in rows if not r.get("dry_run") and (r.get("site") or site_of(r.get("tag", ""))) == site)


def total_spent(rows=None):
    rows = calls() if rows is None else rows
    return sum(r.get("costUsd", 0) for r in rows if not r.get("dry_run"))


def reserve(site, usd):
    """누적 + 잡아 둔 몫 + 예상 > 자리 상한(또는 합 0.8)이면 False — 보내지 않는다."""
    with _LOCK:
        rows = calls()
        held = sum(_RESERVED.values())
        if site_spent(site, rows) + _RESERVED[site] + usd > CAPS10[site] + EPS or total_spent(rows) + held + usd > TOTAL_CAP10 + EPS:
            return False
        _RESERVED[site] += usd
        return True


def release(site, usd):
    with _LOCK:
        _RESERVED[site] = max(0.0, _RESERVED[site] - usd)


def _append(row):
    with _LOCK:
        OUT10.mkdir(parents=True, exist_ok=True)
        with open(OUT10 / "calls.jsonl", "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def send(model, instr, text, schema, tag, est_usd, unit_ids, **extra):
    """semgraph.llm.respond 를 OUT10/.scratch 로 부르고 줄(+ estimateUsd·chars·items·elapsed_ms·site·unit_ids)은 OUT10/calls.jsonl 에.
    luna 는 effort low, 4.1-mini 는 llm.NO_REASONING 이라 reasoning 을 싣지 않는다(TRX7 그대로)."""
    from semgraph import llm
    site = site_of(tag)
    scratch = OUT10 / ".scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        raw, row = llm.respond(model, instr, text, schema, scratch, tag, effort="low")
    finally:
        release(site, est_usd)
    row.update(extra, estimateUsd=round(est_usd, 6), elapsed_ms=round(float(row.get("seconds", 0)) * 1000), site=site, unit_ids=list(unit_ids))
    _append(row)
    return raw, row


def log_skip(model, tag, est_usd, reason, unit_ids=(), **extra):
    row = {"tag": tag, "model": model, "dry_run": True, "skip": reason, "costUsd": 0.0, "estimateUsd": round(est_usd, 6),
           "site": site_of(tag), "unit_ids": list(unit_ids)}
    row.update(extra)
    _append(row)
    return row


# ---------- 예상 ----------
def calib(model, kind, rows=None, first_n=None):
    """그 모델·그 갈래(full|bundle|reask)의 실측 줄 → (토큰/글자 중앙값, 블록당 출력 토큰 중앙값, 출처, n).
    first_n 이면 장부 차례로 처음 n 줄만(100 짝 선실측 뒤 그 100 만 쓰기). 실측이 없으면 TRX7 상수."""
    rows = calls() if rows is None else rows
    mine = [r for r in rows if r.get("model") == model and not r.get("dry_run") and r.get("inputTokens") and r.get("chars")
            and r.get("tag", "").startswith(f"TRX10/{kind}/")]
    if first_n:
        mine = mine[:first_n]
    if not mine:
        return TOK_PER_CHAR0[model], OUT_TOK0[model], "default", 0
    k = median(r["inputTokens"] / r["chars"] for r in mine)
    o = median(r.get("outputTokens", 0) / max(1, r.get("items", 1)) for r in mine)
    return k, o, "measured", len(mine)


def estimate(model, chars, items=1, kind="full", rows=None, first_n=None):
    """예상 = 글자 × 토큰/글자 + 블록당 출력 × 블록 수 → (입력 토큰, 출력 토큰, USD, 출처)."""
    from semgraph import llm
    k, o, src, n = calib(model, kind, rows, first_n)
    inp = int(chars * k)
    outp = int(o * items)
    return inp, outp, llm.cost(model, inp, 0, outp), f"{src}({n})"


def shrink_pages(units, est_of, cap_left):
    """단위 목록이 cap_left 안에 들 때까지 쪽 차례(큰 쪽부터) 통째로 뺀다 → (남긴 단위, 뺀 쪽 목록)."""
    kept = list(units)
    cut = []
    while kept and sum(est_of(u) for u in kept) > cap_left + EPS:
        last = max(u["page"] for u in kept)
        kept = [u for u in kept if u["page"] != last]
        cut.append(last)
    return kept, sorted(cut)


# ---------- 단위 ----------
def load_units():
    return json.load(open(OUT10 / "units.json"))["units"]


def build_units():
    from trlab import fullrev as F
    us = F.make_units(BOOK)
    dump(OUT10 / "units.json", {"book": BOOK, "seed": F.SEED, "how": "review_probe.book_pairs(sam) 짝 전부(용어집 근거 짝 뺌); 글 없음 — "
                                "fullrev.Texts.texts 가 out/trx1 parse 에서 다시 읽어 sha 대조", "n": len(us), "units": us})
    return us


def first_run(extra_lines=()):
    from trlab import fullrev as F
    from trlab import omit_rule as OR
    from trlab import review_probe as R
    lines = [f"# TRX10 규칙·지시문·뽑기 얼림 {time.strftime('%Y-%m-%d %H:%M:%S')} — 실호출 전",
             f"{file_sha16(Path(F.__file__))}  fullrev.py", f"{file_sha16(Path(OR.__file__))}  omit_rule.py",
             f"{file_sha16(Path(__file__))}  tx10_pick.py", f"{file_sha16(Path(R.__file__))}  review_probe.py (import 만)",
             f"{sha16(R.V2_INSTR)}  V2_INSTR ({len(R.V2_INSTR)}자) — TRX7 얼림 71258cda68336c91 과 같아야 한다",
             f"{sha16(OR.REASK_INSTR)}  REASK_INSTR ({len(OR.REASK_INSTR)}자)",
             f"{sha16(OR.KR_SPLIT.pattern)}  KR_SPLIT {OR.KR_SPLIT.pattern!r}", f"{sha16(OR.KR_NOISE.pattern)}  KR_NOISE {OR.KR_NOISE.pattern!r}",
             f"OMIT_MIN_GAP {OR.OMIT_MIN_GAP} · APPLY_KINDS {OR.APPLY_KINDS} · SEED {F.SEED} · BUNDLE_MAX_CHARS {F.BUNDLE_MAX_CHARS} · "
             f"STRATA_MIN {F.STRATA_MIN} · STRATA_N {STRATA_N} · PRE_N {PRE_N} · CAPS10 {CAPS10} · TOK_PER_CHAR0 {TOK_PER_CHAR0} · OUT_TOK0 {OUT_TOK0}"]
    lines += list(extra_lines)
    (OUT10 / "first-run.txt").write_text("\n".join(lines) + "\n")
    return lines


def assert_frozen():
    """실호출 전: first-run.txt 가 있어야 하고 V2_INSTR sha 가 TRX7 얼림과 같아야 한다."""
    from trlab import review_probe as R
    p = OUT10 / "first-run.txt"
    if not p.exists():
        raise RuntimeError("out/trx10/first-run.txt 가 없다 — 얼림 전에 실호출하지 않는다")
    if sha16(R.V2_INSTR) != "71258cda68336c91":
        raise RuntimeError("V2_INSTR sha 가 TRX7 얼림(71258cda68336c91)과 다르다")


# ---------- 호출 하나(공통) ----------
def _call(model, instr, text, schema, tag, unit_ids, kind, items, dry_run, out_path, row):
    """예상 → (dry_run 이면 장부만) → reserve → send. 산출 json 은 out_path 에. → (status, raw, lrow)."""
    chars = len(instr) + len(text)
    inp, outp, usd, src = estimate(model, chars, items, kind)
    row.update(chars=chars, items=items, est={"inp": inp, "outp": outp, "usd": round(usd, 6), "src": src})
    if out_path.exists() and not dry_run:
        return "exists", None, None
    if dry_run:
        log_skip(model, tag, usd, "dry_run", unit_ids, chars=chars, items=items)
        return "dry_run", None, None
    assert_frozen()
    if not reserve(site_of(tag), usd):
        log_skip(model, tag, usd, "over_cap", unit_ids, chars=chars, items=items)
        dump(out_path, dict(row, status="over_cap"))
        return "over_cap", None, None
    try:
        raw, lrow = send(model, instr, text, schema, tag, usd, unit_ids, chars=chars, items=items)
    except Exception as e:  # noqa: BLE001 — 부재는 갈래로(call_failed)
        dump(out_path, dict(row, status="call_failed", error=f"{type(e).__name__}: {str(e)[:300]}"))
        return "call_failed", None, None
    return "sent", raw, lrow


# ---------- (1) 전량 ----------
def select_full(units, n=None, strata=False, model=LUNA):
    """--n: 쪽 차례 처음 n · --strata: 층화 300 · 그 밖: plan 이 줄인 전량."""
    from trlab import fullrev as F
    us = sorted(units, key=lambda u: (u["page"], u["id"]))
    if n:
        return us[:n], []
    if strata:
        return F.stratify(us, STRATA_N, F.SEED), []
    pl = plan(model)
    return pl["kept"], pl["cut_pages"]


def run_full(model, n=None, strata=False, dry_run=False):
    from trlab import fullrev as F
    from trlab import review_probe as R
    T = F.Texts(BOOK)
    units = load_units()
    sel, cut = select_full(units, n, strata, model)
    if cut:
        with open(OUT10 / "first-run.txt", "a") as f:
            f.write(f"# plan {time.strftime('%Y-%m-%d %H:%M:%S')}: {model} 전량에서 뺀 쪽(쪽 차례) {cut}\n")
    folder = OUT10 / "full" / model
    res = []
    for u in sel:
        mat = T.mat(u)
        instr, text, schema = R.build_prompt("V2", mat)
        tag = f"TRX10/full/{model}/{u['id']}"
        row = {"id": u["id"], "model": model, "page": u["page"], "kind": u["kind"], "en_band": u["en_band"], "ko_len": u["ko_len"],
               "en_len": u["en_len"], "n_ko_sent": u["n_ko_sent"], "n_en_sent": u["n_en_sent"], "omit_flag": u["omit_flag"]}
        p = folder / f"{u['id']}.json"
        st, raw, lrow = _call(model, instr, text, schema, tag, [u["id"]], "full", 1, dry_run, p, row)
        if st == "exists":
            res.append(dict(json.load(open(p)), status="exists"))
            continue
        if st != "sent":
            res.append(dict(row, status=st))
            continue
        ans = R.read_answer("V2", raw, mat)
        out = dict(row, status="ok" if ans["parsed"] else "parse_failed", call=lrow, elapsed_ms=lrow.get("elapsed_ms"),
                   prompt={"instructions": instr, "input": text}, raw=raw, answer=ans, score=R.score_unit(mat, ans))
        dump(p, out)
        res.append(out)
    summ = {"model": model, "n": len(res), "status": dict(Counter(r["status"] for r in res)), "cut_pages": cut,
            "est_usd": round(sum(r.get("est", {}).get("usd", 0) for r in res), 6),
            "usd": round(sum(r.get("call", {}).get("costUsd", 0) for r in res), 6)}
    dump(folder / f"_summary-{'n' + str(n) if n else 'strata' if strata else 'all'}.json", summ)
    return res, summ


def plan(model=LUNA, rows=None):
    """100 짝 선실측(장부 차례 처음 PRE_N 줄의 full/<model>)으로 토큰/글자·출력을 다시 셈 → 아직 없는 짝의 예상 합과 자리 남은 몫 →
    넘으면 쪽 차례(큰 쪽부터)로 뺀다. → {k, o, src, n_calib, n_done, n_left, est_left, cap_left, kept, cut_pages}."""
    from trlab import fullrev as F
    from trlab import review_probe as R
    rows = calls() if rows is None else rows
    site = "mini" if model == MINI else "luna-full"
    k, o, src, nc = calib(model, "full", rows, first_n=PRE_N)
    units = sorted(load_units(), key=lambda u: (u["page"], u["id"]))
    folder = OUT10 / "full" / model
    done = {p.stem for p in folder.glob("*.json") if not p.name.startswith("_")} if folder.exists() else set()
    left = [u for u in units if u["id"] not in done]
    T = F.Texts(BOOK)
    from semgraph import llm

    def est_of(u):
        mat = T.mat(u)
        instr, text, _ = R.build_prompt("V2", mat)
        return llm.cost(model, int((len(instr) + len(text)) * k), 0, int(o))

    cap_left = CAPS10[site] - site_spent(site, rows) - sum(_RESERVED.values())
    kept, cut = shrink_pages(left, est_of, cap_left)
    return {"model": model, "k": k, "o": o, "src": src, "n_calib": nc, "n_done": len(done), "n_left": len(left),
            "est_left": round(sum(est_of(u) for u in left), 6), "est_kept": round(sum(est_of(u) for u in kept), 6),
            "cap_left": round(cap_left, 6), "kept": kept, "cut_pages": cut}


def plan_text():
    rows = calls()
    lines = [f"{s}: 누적 {site_spent(s, rows):.4f} / {CAPS10[s]}" for s in SITES]
    lines.append(f"합 {total_spent(rows):.4f} / {TOTAL_CAP10} · 장부 {len(rows)}줄(dry_run {sum(1 for r in rows if r.get('dry_run'))})")
    for m in (LUNA, MINI):
        k, o, src, n = calib(m, "full", rows, first_n=PRE_N)
        lines.append(f"{m} full 토큰/글자 {k:.3f} · 블록당 출력 {o:.0f} ({src} n={n})")
    pl = plan(LUNA, rows)
    lines.append(f"luna 전량: 됨 {pl['n_done']} · 남음 {pl['n_left']} 예상 {pl['est_left']:.4f} · 자리 남음 {pl['cap_left']:.4f} · "
                 f"남길 {len(pl['kept'])} ({pl['est_kept']:.4f}) · 뺀 쪽 {pl['cut_pages']}")
    return "\n".join(lines)


# ---------- (3) 묶음(TRX7 38) ----------
def trx7_units():
    from trlab import tx7_pick as X
    return [u for u in X.load_units("test") if u["cat"] != "real"]


def trx7_mats():
    from trlab import review_probe as R
    from trlab.glossary_check import load_gloss
    G = {k: load_gloss(k)["items"] for k in ("sam", "cos", "kb")}
    return [R.materialize(u, G[u["book"]]) for u in trx7_units()]


def run_bundle(model, dry_run=False):
    from trlab import fullrev as F
    mats = trx7_mats()
    bundles = F.bundle_plan(mats, F.BUNDLE_MAX_CHARS, F.SEED)
    folder = OUT10 / "bundle" / model
    res = []
    for bi, blocks in enumerate(bundles, 1):
        instr, text, schema = F.build_bundle_prompt(blocks)
        ids = [b["id"] for b in blocks]
        tag = f"TRX10/bundle/{model}/{bi}"
        row = {"bundle": bi, "model": model, "unit_ids": ids, "n_blocks": len(blocks)}
        p = folder / f"b{bi}.json"
        st, raw, lrow = _call(model, instr, text, schema, tag, ids, "bundle", len(blocks), dry_run, p, row)
        if st == "exists":
            res.append(dict(json.load(open(p)), status="exists"))
            continue
        if st != "sent":
            res.append(dict(row, status=st))
            continue
        split = F.split_bundle_answer(raw, len(blocks))
        per_block = []
        for k, (b, issues) in enumerate(zip(blocks, split["per_block"]), 1):
            sc = F.score_bundle_block(b, issues, split["parsed"])
            ub = {"id": b["id"], "bundle": bi, "block": k, "cat": b["cat"], "kind": b["kind"], "status": "ok" if split["parsed"] else "parse_failed",
                  "issues": issues, "score": sc, "usd_share": round(lrow.get("costUsd", 0) / len(blocks), 7),
                  "elapsed_share_ms": round(lrow.get("elapsed_ms", 0) / len(blocks))}
            dump(folder / "units" / f"{b['id']}.json", ub)
            per_block.append(ub)
        out = dict(row, status="ok" if split["parsed"] else "parse_failed", call=lrow, elapsed_ms=lrow.get("elapsed_ms"),
                   prompt={"instructions": instr, "input": text}, raw=raw, oob=split["oob"], n_issues=split["n_issues"],
                   blocks=[{"id": u["id"], "block": u["block"], "score": u["score"], "n_issues": len(u["issues"])} for u in per_block])
        dump(p, out)
        res.append(out)
    summ = {"model": model, "n_bundles": len(bundles), "blocks_per_bundle": [len(b) for b in bundles],
            "status": dict(Counter(r["status"] for r in res)), "est_usd": round(sum(r.get("est", {}).get("usd", 0) for r in res), 6),
            "usd": round(sum(r.get("call", {}).get("costUsd", 0) for r in res), 6), "oob": sum(len(r.get("oob", [])) for r in res)}
    dump(folder / "_summary.json", summ)
    return res, summ


# ---------- (2) 되묻기 ----------
def reask_targets(source="both"):
    """규칙에 걸린 블록: TRX7 38(심은 EN, 전부에 적용 — 보고에 적음) + 전량(units.json omit_flag). → [(id, source, ko, en, cat, planted_before)]."""
    from trlab import fullrev as F
    from trlab import omit_rule as OR
    out = []
    if source in ("both", "trx7"):
        for m in trx7_mats():
            n_ko, n_en = OR.counts(m["ko"], m["en"])
            if OR.flagged(n_ko, n_en):
                out.append({"id": m["id"], "source": "trx7", "ko": m["ko"], "en": m["en"], "en_clean": m["en_clean"], "cat": m["cat"],
                            "kind": m["kind"], "planted": m["planted"], "n_ko": n_ko, "n_en": n_en})
    if source in ("both", "full"):
        T = F.Texts(BOOK)
        for u in sorted(load_units(), key=lambda u: (u["page"], u["id"])):
            if u.get("omit_flag"):
                ko, en = T.texts(u)
                out.append({"id": u["id"], "source": "full", "ko": ko, "en": en, "en_clean": en, "cat": "clean", "kind": u["kind"],
                            "planted": None, "n_ko": u["n_ko_sent"], "n_en": u["n_en_sent"], "page": u["page"], "en_band": u["en_band"]})
    return out


def run_reask(dry_run=False, source="both"):
    from trlab import omit_rule as OR
    model = LUNA
    folder = OUT10 / "reask" / model
    res = []
    for t in reask_targets(source):
        instr, text, schema, ks = OR.build_reask(t["ko"], t["en"])
        tag = f"TRX10/reask/{model}/{t['id']}"
        pk = OR.planted_ko_no(t["ko"], t["en_clean"], t["planted"]["before"]) if t.get("planted") and t["planted"]["cat"] == "omit" else None
        row = {"id": t["id"], "source": t["source"], "model": model, "cat": t["cat"], "kind": t["kind"], "n_ko": t["n_ko"], "n_en": t["n_en"],
               "planted_no": pk, "page": t.get("page"), "en_band": t.get("en_band")}
        p = folder / f"{t['id']}.json"
        st, raw, lrow = _call(model, instr, text, schema, tag, [t["id"]], "reask", 1, dry_run, p, row)
        if st == "exists":
            res.append(dict(json.load(open(p)), status="exists"))
            continue
        if st != "sent":
            res.append(dict(row, status=st))
            continue
        ans = OR.read_reask(raw, len(ks))
        out = dict(row, status="ok" if ans["parsed"] else "parse_failed", call=lrow, elapsed_ms=lrow.get("elapsed_ms"),
                   prompt={"instructions": instr, "input": text}, raw=raw, answer=ans,
                   score=OR.score_unit(t["cat"], (pk or {}).get("no"), True, ans))
        dump(p, out)
        res.append(out)
    summ = {"model": model, "n": len(res), "status": dict(Counter(r["status"] for r in res)),
            "by_source": dict(Counter(r["source"] for r in res)),
            "est_usd": round(sum(r.get("est", {}).get("usd", 0) for r in res), 6),
            "usd": round(sum(r.get("call", {}).get("costUsd", 0) for r in res), 6)}
    dump(folder / "_summary.json", summ)
    return res, summ


# ---------- 라벨 표본·얼림 ----------
def full_rows(model):
    """OUT10/full/<model>/*.json → score 줄(글 없음)."""
    folder = OUT10 / "full" / model
    rows = []
    for p in sorted(folder.glob("*.json")) if folder.exists() else []:
        if p.name.startswith("_"):
            continue
        d = json.load(open(p))
        iss = (d.get("answer") or {}).get("issues", [])
        rows.append({"id": d["id"], "page": d["page"], "kind": d["kind"], "en_band": d["en_band"], "status": d["status"],
                     "flagged": bool((d.get("answer") or {}).get("flagged")), "n_issues": len(iss), "cats": [str(i.get("category", "")) for i in iss],
                     "elapsed_ms": d.get("elapsed_ms"), "usd": (d.get("call") or {}).get("costUsd"),
                     "inputTokens": (d.get("call") or {}).get("inputTokens"), "outputTokens": (d.get("call") or {}).get("outputTokens"),
                     "n_ko_sent": d.get("n_ko_sent"), "n_en_sent": d.get("n_en_sent"), "omit_flag": d.get("omit_flag")})
    return rows


def pick_labels():
    from trlab import fullrev as F
    U = {u["id"]: u for u in load_units()}
    luna, mini = full_rows(LUNA), full_rows(MINI)
    flags = F.pick_flags(luna, mini, F.SEED)
    for f in flags:
        d = json.load(open(OUT10 / "full" / f["model"] / f"{f['id']}.json"))
        f["block_sha"] = U[f["id"]]["en_sha"]
        f["kind"] = U[f["id"]]["kind"]
        f["issues"] = [{"issue_idx": i, "cat": it.get("category")} for i, it in enumerate(d["answer"]["issues"])]
    omit = F.pick_omit(list(U.values()), F.SEED)
    for o in omit:
        o["block_sha"] = U[o["id"]]["en_sha"]
        rp = OUT10 / "reask" / LUNA / f"{o['id']}.json"
        o["reask_missing"] = json.load(open(rp))["answer"]["missing"] if rp.exists() else None
    dump(OUT10 / "labels" / "flags-sample.json", {"seed": F.SEED, "n": len(flags), "how": "fullrev.pick_flags — luna 걸림 40(갈래 × 꼴) + "
                                                  "4.1-mini 걸림 20(같이 건 짝 먼저)", "items": flags})
    dump(OUT10 / "labels" / "omit-sample.json", {"seed": F.SEED, "n": len(omit), "how": "fullrev.pick_omit — 규칙 걸림에서 EN 띠 층화", "items": omit})
    return flags, omit


def freeze():
    files = sorted(p for p in GOLD10.glob("*.json"))
    lines = [f"# TRX10 라벨 얼림 {time.strftime('%Y-%m-%d %H:%M:%S')} — 채점·표 전"]
    lines += [f"{file_sha16(p)}  {p.name}" for p in files]
    (GOLD10 / "freeze.txt").write_text("\n".join(lines) + "\n")
    return lines


# ---------- 채점 ----------
def score_full():
    from trlab import fullrev as F
    out = {}
    for m in (LUNA, MINI):
        rows = full_rows(m)
        if not rows:
            continue
        out[m] = {"name": f"full-{m}", "rows": rows, "n": len(rows), "dist": F.dist(rows)}
        dump(OUT10 / "score" / f"full-{m}.json", out[m])
    if LUNA in out and MINI in out:  # 같은 짝(4.1-mini 300)의 걸림 일치율
        L = {r["id"]: r for r in out[LUNA]["rows"] if r["status"] == "ok"}
        both = [(r, L[r["id"]]) for r in out[MINI]["rows"] if r["status"] == "ok" and r["id"] in L]
        agree = sum(1 for a, b in both if a["flagged"] == b["flagged"])
        sub = F.dist([L[r["id"]] for r in out[MINI]["rows"] if r["id"] in L])
        dump(OUT10 / "score" / "full-agree.json", {"n_same": len(both), "agree": agree, "both_flag": sum(1 for a, b in both if a["flagged"] and b["flagged"]),
                                                    "mini_only": sum(1 for a, b in both if a["flagged"] and not b["flagged"]),
                                                    "luna_only": sum(1 for a, b in both if b["flagged"] and not a["flagged"]), "luna_same300": sub})
    return out


def score_bundle():
    out = {}
    for m in (LUNA, MINI):
        folder = OUT10 / "bundle" / m
        if not folder.exists():
            continue
        rows, bundles = [], []
        for p in sorted(folder.glob("b*.json")):
            d = json.load(open(p))
            bundles.append({"bundle": d["bundle"], "status": d["status"], "n_blocks": d["n_blocks"], "oob": len(d.get("oob", [])),
                            "usd": (d.get("call") or {}).get("costUsd", 0), "elapsed_ms": d.get("elapsed_ms"), "chars": d.get("chars"),
                            "inputTokens": (d.get("call") or {}).get("inputTokens"), "outputTokens": (d.get("call") or {}).get("outputTokens")})
            for b in d.get("blocks", []):
                rows.append(dict(b["score"], bundle=d["bundle"], status=d["status"], usd=bundles[-1]["usd"] / d["n_blocks"],
                                 elapsed_ms=(d.get("elapsed_ms") or 0) / d["n_blocks"]))
            if d["status"] not in ("ok", "parse_failed"):
                for uid in d["unit_ids"]:
                    rows.append({"id": uid, "status": d["status"], "flagged": False, "hit": None, "false_alarm": False, "bundle": d["bundle"], "usd": 0})
        single = OUT7 / "score" / f"V2-{m}.json"
        srows = json.load(open(single))["rows"] if single.exists() else []
        out[m] = {"name": f"bundle-{m}", "rows": rows, "bundles": bundles, "n": len(rows), "single_rows": [r for r in srows if r["cat"] != "real" and r["split"] == "test"],
                  "single_path": str(single.relative_to(ROOT))}
        dump(OUT10 / "score" / f"bundle-{m}.json", out[m])
    return out


def score_omit():
    """TRX7 38(규칙 + 되묻기) · 전량 규칙 걸림 · 라벨 40."""
    from trlab import omit_rule as OR
    rows7 = []
    reask = {}
    folder = OUT10 / "reask" / LUNA
    if folder.exists():
        for p in folder.glob("*.json"):
            if not p.name.startswith("_"):
                d = json.load(open(p))
                reask[d["id"]] = d
    for m in trx7_mats():
        n_ko, n_en = OR.counts(m["ko"], m["en"])
        rf = OR.flagged(n_ko, n_en)
        r = reask.get(m["id"])
        pk = OR.planted_ko_no(m["ko"], m["en_clean"], m["planted"]["before"]) if m["planted"] and m["planted"]["cat"] == "omit" else None
        ans = r.get("answer") if r and r.get("status") == "ok" else None
        sc = OR.score_unit(m["cat"], (pk or {}).get("no"), rf, ans)
        rows7.append({"id": m["id"], "cat": m["cat"], "kind": m["kind"], "n_ko": n_ko, "n_en": n_en, "planted_no": pk,
                      "reask_status": (r or {}).get("status"), "missing": (ans or {}).get("missing"), **sc,
                      "usd": ((r or {}).get("call") or {}).get("costUsd", 0), "elapsed_ms": (r or {}).get("elapsed_ms")})
    units = load_units()
    full_flag = [u for u in units if u.get("omit_flag")]
    full_reask = {u["id"]: reask[u["id"]] for u in full_flag if u["id"] in reask}
    gold = GOLD10 / "omit.json"
    labels = json.load(open(gold))["items"] if gold.exists() else []
    lab = {"n": len(labels), "labels": dict(Counter(x["label"] for x in labels)),
           "missing_pair": sum(1 for x in labels if x["label"] == "missing" and x.get("pair")),
           "reask_agree": sum(1 for x in labels if (bool(x.get("reask_missing")) == (x["label"] == "missing"))),
           "by_band": {}}
    for x in labels:
        lab["by_band"].setdefault(x.get("en_band"), Counter())[x["label"]] += 1
    lab["by_band"] = {k: dict(v) for k, v in lab["by_band"].items()}
    out = {"trx7": rows7, "full": {"n_apply": sum(1 for u in units if u["omit_applies"]), "n_flag": len(full_flag),
                                   "by_kind": dict(Counter(u["kind"] for u in full_flag)), "by_band": dict(Counter(u["en_band"] for u in full_flag)),
                                   "apply_by_kind": dict(Counter(u["kind"] for u in units if u["omit_applies"])),
                                   "n_reask": len(full_reask), "reask_status": dict(Counter(r["status"] for r in full_reask.values())),
                                   "reask_missing_nonempty": sum(1 for r in full_reask.values() if r.get("status") == "ok" and r["answer"]["missing"]),
                                   "reask_usd": sum((r.get("call") or {}).get("costUsd", 0) for r in full_reask.values()),
                                   "reask_elapsed": [r.get("elapsed_ms") for r in full_reask.values() if r.get("elapsed_ms")]},
           "labels": lab}
    dump(OUT10 / "score" / "omit.json", out)
    return out


def score_flags():
    gold = GOLD10 / "flags.json"
    if not gold.exists():
        return None
    items = json.load(open(gold))["items"]
    by_model, by_cat, blocks = {}, {}, {}
    for it in items:
        m = it["model"]
        by_model.setdefault(m, Counter())[it["label"]] += 1
        by_cat.setdefault((m, it["cat"]), Counter())[it["label"]] += 1
        blocks.setdefault((m, it["id"]), []).append(it["label"])
    pair = {m: {"real_pair": sum(1 for it in items if it["model"] == m and it["label"] == "real" and it.get("pair")),
                "real_nonpair": sum(1 for it in items if it["model"] == m and it["label"] == "real" and not it.get("pair"))} for m in by_model}
    out = {"n_issues": len(items), "by_model": {m: dict(c) for m, c in by_model.items()}, "pair": pair,
           "by_cat": {f"{m}/{c}": dict(v) for (m, c), v in by_cat.items()},
           "blocks": {m: {"n": sum(1 for (mm, _) in blocks if mm == m), "real": sum(1 for (mm, _), ls in blocks.items() if mm == m and "real" in ls),
                          "by_kind": {}} for m in by_model}}
    for (m, uid), ls in blocks.items():
        k = next(it["kind"] for it in items if it["id"] == uid and it["model"] == m)
        d = out["blocks"][m]["by_kind"].setdefault(k, {"n": 0, "real": 0})
        d["n"] += 1
        d["real"] += 1 if "real" in ls else 0
    dump(OUT10 / "score" / "flags.json", out)
    return out


def score_all():
    a = score_full()
    b = score_bundle()
    c = score_omit()
    d = score_flags()
    return {"full": {k: v["n"] for k, v in a.items()}, "bundle": {k: v["n"] for k, v in b.items()}, "omit_trx7": len(c["trx7"]),
            "omit_full_flag": c["full"]["n_flag"], "flags": (d or {}).get("n_issues")}


def main(argv):
    cmd = argv[0]
    dry = "--dry-run" in argv
    if cmd == "units":
        us = build_units()
        print(OUT10 / "units.json", len(us), Counter(u["kind"] for u in us), "omit_flag", sum(1 for u in us if u["omit_flag"]))
    elif cmd == "first-run":
        print("\n".join(first_run()))
    elif cmd == "full":
        model = argv[1]
        n = int(argv[argv.index("--n") + 1]) if "--n" in argv else None
        _, s = run_full(model, n, "--strata" in argv, dry)
        print(json.dumps(s, ensure_ascii=False))
    elif cmd == "plan":
        print(plan_text())
    elif cmd == "bundle":
        _, s = run_bundle(argv[1], dry)
        print(json.dumps(s, ensure_ascii=False))
    elif cmd == "reask":
        src = argv[argv.index("--source") + 1] if "--source" in argv else "both"
        _, s = run_reask(dry, src)
        print(json.dumps(s, ensure_ascii=False))
    elif cmd == "pick-labels":
        f, o = pick_labels()
        print("flags", len(f), Counter(x["model"] for x in f), "omit", len(o), Counter(x["en_band"] for x in o))
    elif cmd == "freeze":
        print("\n".join(freeze()))
    elif cmd == "score":
        print(json.dumps(score_all(), ensure_ascii=False))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
