"""TRX5 — 용어 불일치 검출 luna span 판(세 책 공식판) · 지칭 검사기 it/its (MARC-TICK-505).

(1) span 판: KR 용어마다 「이 짝 EN 에서 어느 글자로 옮겨졌나」를 luna 에게 뽑게 하고(묶음 ≤ 12,000자, 짝마다 KO+EN),
    같은 용어의 exact|variant span 정규화 키가 둘 이상이면 불일치(S1 · S2 품는 합치기 · S3 출현 ≥ 2 · S4 sol 되묻기).
    짝·용어 풀은 TRX1 `glossary_check.inconsistency` 와 같은 규칙(span_incons.pool). 지시문은 sam 으로 짜고 얼린다(OUT5/first-run.txt).
(2) 지칭 it/its: `subject.analyze(..., it_rule=R1|R2|R3)` 로 세 책 공식판·sam 엔진 산출(6쪽)을 다시 세고, 얼린 표본(TRX1 25 + it 25)을 재채점.

쓰는 곳은 OUT5(env TRX5_OUT, 기본 esg-lab/out/trx5) 하나. out/trx1~4 는 읽기만. 정답 얼림은 prework/gold-trx5/ (GOLD5).
실호출: 장부 OUT5/calls.jsonl, 자리 상한 CAPS5 = luna 0.3 · sol 0.2 · 합 0.5. 누적 + 잡아 둔 몫 + 예상 > 상한이면 보내지 않음(over_cap).

명령(py/ 에서, --group scope3colab):
  python -m trlab.tx5_pick sample-it                      → OUT5/subject/it25-context.json (seed 505, sam 8 · cos 9 · kb 8 — 라벨용 문맥)
  python -m trlab.tx5_pick freeze                         → GOLD5/freeze.txt (정답 파일 sha256 앞 16자 + 시각)
  python -m trlab.tx5_pick subject <키> [--it R1|R2|R3]   → OUT5/subject/<키>-official-<rule>.json (+ sam 이면 엔진 산출 셋)
  python -m trlab.tx5_pick rescore                        → OUT5/subject/rescore.json (갈래 × 표본 둘)
  python -m trlab.tx5_pick pool <키>                      → OUT5/pool/<키>.json
  python -m trlab.tx5_pick span <키> [--batches n] [--dry-run]   (실호출은 --with openai==3.26.0) → OUT5/span/<키>-<n>.json
  python -m trlab.tx5_pick incons <키>                    → OUT5/incons/<키>-S{1,2,3}.json
  python -m trlab.tx5_pick judge <키> [--dry-run]         → OUT5/incons/<키>-S4.json (sol 책당 1호출)
"""

import hashlib
import json
import os
import random
import re
import sys
import threading
import time
from collections import Counter
from pathlib import Path

from trlab import common

ROOT = common.ROOT
OUT5 = Path(os.environ.get("TRX5_OUT", ROOT / "esg-lab" / "out" / "trx5"))
OUT1 = ROOT / "esg-lab" / "out" / "trx1"
OUT4 = ROOT / "esg-lab" / "out" / "trx4"
GOLD5 = Path(os.environ.get("TRX5_GOLD", ROOT / "prework" / "gold-trx5"))

LUNA, SOL = "gpt-6-luna", "gpt-6.1-sol"
CAPS5 = {"luna": 0.3, "sol": 0.2}
TOTAL_CAP5 = 0.5
SITE_PREFIX5 = {"luna": ("TRX5/span/",), "sol": ("TRX5/judge/",)}
TOK_PER_CHAR0 = 0.615  # 첫 실측 전: out/trx4 장부 TRX4/d4-K1/kb-ko 24,522자 → 15,081 토큰(발주서 ★2)
OUT_TOK_PER_ITEM = 25
IT_RULES = ("none", "R1", "R2", "R3")
IT_SAMPLE = {"sam": 8, "cos": 9, "kb": 8}
IT_SEED = 505


def sha16(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def file_sha16(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:16]


def dump(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    json.dump(obj, open(path, "w"), ensure_ascii=False, indent=1)
    return path


# ---------- 장부(OUT5, 자리 상한) — tx4_pick 꼴을 베낌(tx4 것은 out/trx4 에 쓴다) ----------
_LOCK = threading.Lock()
_RESERVED = {"luna": 0.0, "sol": 0.0}


def calls():
    p = OUT5 / "calls.jsonl"
    return [json.loads(x) for x in open(p) if x.strip()] if p.exists() else []


def site_spent(site, rows=None):
    rows = calls() if rows is None else rows
    return sum(r.get("costUsd", 0) for r in rows if not r.get("dry_run") and r.get("tag", "").startswith(SITE_PREFIX5[site]))


def total_spent(rows=None):
    rows = calls() if rows is None else rows
    return sum(r.get("costUsd", 0) for r in rows if not r.get("dry_run"))


def reserve(site, usd):
    """누적 + 잡아 둔 몫 + 예상 > 자리 상한(또는 합 0.5)이면 False — 보내지 않는다."""
    with _LOCK:
        rows = calls()
        held = sum(_RESERVED.values())
        if site_spent(site, rows) + _RESERVED[site] + usd > CAPS5[site] or total_spent(rows) + held + usd > TOTAL_CAP5:
            return False
        _RESERVED[site] += usd
        return True


def release(site, usd):
    with _LOCK:
        _RESERVED[site] = max(0.0, _RESERVED[site] - usd)


def send(site, model, instr, text, schema, tag, est_usd, **extra):
    """semgraph.llm.respond 를 OUT5/.scratch 로 부르고, 줄(+ estimateUsd·chars·items)은 OUT5/calls.jsonl 에 한 줄."""
    from semgraph import llm
    scratch = OUT5 / ".scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        raw, row = llm.respond(model, instr, text, schema, scratch, tag, effort="low")
    finally:
        release(site, est_usd)
    row.update(extra, estimateUsd=round(est_usd, 6))
    with _LOCK:
        OUT5.mkdir(parents=True, exist_ok=True)
        with open(OUT5 / "calls.jsonl", "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return raw, row


def log_skip(site, model, tag, est_usd, reason, **extra):
    """보내지 않은 호출(over_cap · dry_run)도 장부에 한 줄 — costUsd 0, dry_run True."""
    row = {"tag": tag, "model": model, "dry_run": True, "skip": reason, "costUsd": 0.0, "estimateUsd": round(est_usd, 6),
           "site": site}
    row.update(extra)
    with _LOCK:
        OUT5.mkdir(parents=True, exist_ok=True)
        with open(OUT5 / "calls.jsonl", "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def tok_per_char(model, rows=None):
    """첫 실측 토큰/글자 — OUT5 장부의 그 모델 글 호출 첫 줄(chars 있음), 없으면 0.615(발주서 ★2)."""
    rows = calls() if rows is None else rows
    mine = [r for r in rows if r.get("model") == model and r.get("chars") and not r.get("dry_run") and r.get("inputTokens")]
    if mine:
        r = mine[0]
        return r["inputTokens"] / r["chars"], "measured"
    return TOK_PER_CHAR0, "default"


def estimate(model, text, instr, n_items):
    """예상 = 글자 × 토큰/글자(+ 지시문 글자/4), 출력 = 항목 수 × 25 토큰 → (입력 토큰, 출력 토큰, USD, 출처)."""
    from semgraph import llm
    k, src = tok_per_char(model)
    inp = int(len(text) * k) + len(instr) // 4
    outp = max(50, n_items * OUT_TOK_PER_ITEM)
    return inp, outp, llm.cost(model, inp, 0, outp), src


# ---------- (2) 지칭 ----------
def _official_rows(key):
    """TRX1 의 rows(읽기만) — 표본 뽑기는 TRX1 산출 위에서(rows 차례가 고정)."""
    return json.load(open(OUT1 / "subject" / f"{key}-official.json"))["rows"]


PRON_ANY = re.compile(r"\b[Ii]t(?:s|self)?\b")


def sample_it(seed=IT_SEED, sizes=IT_SAMPLE):
    """세 책 공식판 문단 띠에서 it/its/itself 든 문장을 뽑고, 같은 블록의 앞 문장을 문맥으로 붙인다(라벨용)."""
    out = []
    for key, n in sizes.items():
        rows = _official_rows(key)
        pool = [i for i, r in enumerate(rows) if r["band"] == "paragraph" and PRON_ANY.search(r["sent"])]
        rnd = random.Random(seed + {"sam": 0, "cos": 1, "kb": 2}[key])
        for i in sorted(rnd.sample(pool, n)):
            r = rows[i]
            prev = []
            j = i - 1
            while j >= 0 and rows[j]["unit"] == r["unit"]:
                prev.insert(0, rows[j]["sent"])
                j -= 1
            out.append({"book": key, "unit": r["unit"], "page": r["page"], "band": r["band"], "sha": sha16(r["sent"]),
                        "trx1_subj": r["subj"], "prev": prev, "sent": r["sent"]})
    return out


def gold_rows(name):
    return json.load(open(GOLD5 / f"{name}.json"))["items"]


def freeze():
    files = sorted(p for p in GOLD5.glob("*.json"))
    lines = [f"# TRX5 지칭 표본 정답 얼림 {time.strftime('%Y-%m-%d %H:%M:%S')} — it 규칙(subject.py it_rule)을 짜기 전"]
    lines += [f"{file_sha16(p)}  {p.name}" for p in files]
    (GOLD5 / "freeze.txt").write_text("\n".join(lines) + "\n")
    return lines


def run_subject(key, it_rule=None):
    from trlab import subject as S
    name_re, gen_re = S.names_for(key)
    tops = S.topics(key)
    rule = it_rule or "none"
    jobs = [("official", S.official_units(key), tops)]
    if key == "sam":
        jobs += [(m, S.engine_units(m), [("CEO", 4, 4, True)]) for m in ("gpt-6-luna", "gpt-6.1-sol", "engine-4.1-mini")]
    outs = {}
    for tag, units, tp in jobs:
        res, rows = S.analyze(units, name_re, gen_re, tp, it_rule=it_rule)
        p = dump(OUT5 / "subject" / f"{key}-{tag}-{rule}.json", {"res": res, "rows": rows, "it_rule": rule})
        outs[tag] = (res, rows, p)
    return outs


def _lookup(rows):
    return {(r["unit"], sha16(r["sent"])): r for r in rows}


def rescore_one(gold, rows_by_rule):
    """표본(gold_subj) 대 갈래마다 subj → 맞음 수와 틀린 문장."""
    out = {}
    for rule, look in rows_by_rule.items():
        ok, wrong, miss = 0, [], 0
        for g in gold:
            r = look.get((g["unit"], g["sha"]))
            if r is None:
                miss += 1
                continue
            if r["subj"] == g["gold_subj"]:
                ok += 1
            else:
                wrong.append({"book": g["book"], "unit": g["unit"], "sha": g["sha"], "gold": g["gold_subj"], "got": r["subj"]})
        out[rule] = {"ok": ok, "n": len(gold), "missing": miss, "wrong": wrong}
    return out


def rescore():
    """두 표본(subject-25 · subject-it25) × 갈래(none·R1·R2·R3) → OUT5/subject/rescore.json."""
    looks = {}
    for rule in IT_RULES:
        d = {}
        for key in common.BOOKS:
            p = OUT5 / "subject" / f"{key}-official-{rule}.json"
            if not p.exists():
                raise SystemExit(f"없음: {p} — 먼저 subject {key} --it {rule}")
            d.update({(key,) + k: v for k, v in _lookup(json.load(open(p))["rows"]).items()})
        looks[rule] = d
    res = {}
    for name in ("subject-25", "subject-it25"):
        gold = gold_rows(name)
        res[name] = rescore_one(gold, {rule: _by_book(d, gold) for rule, d in looks.items()})
    dump(OUT5 / "subject" / "rescore.json", res)
    return res


def _by_book(d, gold):
    """책을 unit 키에 섞어 둔 사전을 표본 행마다 (unit, sha) 로 찾게 편다."""
    return {(g["unit"], g["sha"]): d[(g["book"], g["unit"], g["sha"])] for g in gold if (g["book"], g["unit"], g["sha"]) in d}


# ---------- (1) span · 불일치 ----------
def run_pool(key):
    from trlab import span_incons as SI
    P = SI.pool(key)
    items = P["items"]
    pairs_hit = {it["pair"] for it in items}
    chars = sum(len(p["ko"]) + len(p["en"]) for p in P["pairs"] if p["id"] in pairs_hit)
    summ = {"book": key, "pairs": len(P["pairs"]), "dropped": P["dropped"], "terms": len(P["terms"]),
            "in_glossary": sum(1 for t in P["terms"] if t in P["gloss_kr"]), "items": len(items),
            "pairs_hit": len(pairs_hit), "chars": chars, "term_occ": {t: c for t, c in Counter(it["term"] for it in items).items()}}
    dump(OUT5 / "pool" / f"{key}.json", summ)
    return P, summ


def run_span(key, max_batches=None, dry_run=False, max_chars=12000, threads=4):
    """묶음마다 luna 1호출(쓰레드 ≤ 4). 예상·상한·장부. 산출 OUT5/span/<키>-<n>.json(묶음 + 답 + 검증 꼴)."""
    from concurrent.futures import ThreadPoolExecutor
    from trlab import span_incons as SI
    P, _ = run_pool(key)
    batches = SI.batches(P["items"], P["pairs"], max_chars=max_chars)
    if max_batches:
        batches = batches[:max_batches]
    instr = SI.INSTR
    (OUT5 / "span").mkdir(parents=True, exist_ok=True)

    def one(n_b):
        n, b = n_b
        p = OUT5 / "span" / f"{key}-{n}.json"
        if p.exists():
            return json.load(open(p))
        text = SI.prompt(b)
        n_items = sum(len(x["terms"]) for x in b["pairs"])
        inp, outp, usd, src = estimate(LUNA, text, instr, n_items)
        tag = f"TRX5/span/{key}/{n}"
        row = {"batch": n, "chars": len(text), "items": n_items, "est": {"inp": inp, "outp": outp, "usd": round(usd, 6), "src": src}}
        if dry_run:
            log_skip("luna", LUNA, tag, usd, "dry_run", chars=len(text), items=n_items)
            return dict(row, status="dry_run")
        if not reserve("luna", usd):
            log_skip("luna", LUNA, tag, usd, "over_cap", chars=len(text), items=n_items)
            return dict(row, status="over_cap")
        raw, lrow = send("luna", LUNA, instr, text, SI.SCHEMA, tag, usd, chars=len(text), items=n_items)
        ans = SI.parse_answer(raw)
        checked = SI.check(b, ans)
        out = dict(row, status="ok", call=lrow, batch_pairs=b["pairs"], answer=ans, checked=checked,
                   kinds=dict(Counter(x["kind"] for x in checked)))
        dump(p, out)
        return out

    with ThreadPoolExecutor(max_workers=threads) as ex:
        res = list(ex.map(one, enumerate(batches)))
    summ = {"book": key, "batches": len(batches), "status": dict(Counter(r["status"] for r in res)),
            "kinds": dict(sum((Counter(r.get("kinds", {})) for r in res), Counter())),
            "est_usd": round(sum(r["est"]["usd"] for r in res), 6),
            "usd": round(sum(r.get("call", {}).get("costUsd", 0) for r in res), 6)}
    dump(OUT5 / "span" / f"{key}-summary.json", summ)
    return res, summ


def span_rows(key):
    """OUT5/span/<키>-*.json 의 검증된 항목 전부 → [{pair, page, term, span, kind, key}]."""
    out = []
    for p in sorted(OUT5.glob(f"span/{key}-[0-9]*.json"), key=lambda p: int(p.stem.split("-")[1])):
        d = json.load(open(p))
        out += d.get("checked", [])
    return out


def run_incons(key):
    from trlab import span_incons as SI
    P, _ = run_pool(key)
    rows = span_rows(key)
    gloss_kr = P["gloss_kr"]
    outs = {}
    for s in ("S1", "S2", "S3"):
        flags = SI.judge_code(rows, s, gloss_kr)
        outs[s] = flags
        dump(OUT5 / "incons" / f"{key}-{s}.json", {"book": key, "stage": s, "flags": flags, "n_flags": len(flags),
                                                   "items": len(rows), "kinds": dict(Counter(r["kind"] for r in rows))})
    return outs


def run_judge(key, dry_run=False):
    """S4 — S2 걸림 용어마다 「KR 용어 + 서로 다른 span 들(각 1예문 ±80자)」을 sol 에게 주고 same_official_name 을 받는다. 책당 1호출."""
    from trlab import span_incons as SI
    P, _ = run_pool(key)
    s2 = json.load(open(OUT5 / "incons" / f"{key}-S2.json"))["flags"]
    text, n_items = SI.judge_prompt(s2, P["pairs"])
    inp, outp, usd, src = estimate(SOL, text, SI.JUDGE_INSTR, n_items)
    tag = f"TRX5/judge/{key}"
    row = {"book": key, "chars": len(text), "items": n_items, "est": {"inp": inp, "outp": outp, "usd": round(usd, 6), "src": src}}
    if dry_run:
        log_skip("sol", SOL, tag, usd, "dry_run", chars=len(text), items=n_items)
        return dict(row, status="dry_run")
    if not reserve("sol", usd):
        log_skip("sol", SOL, tag, usd, "over_cap", chars=len(text), items=n_items)
        return dict(row, status="over_cap")
    raw, lrow = send("sol", SOL, SI.JUDGE_INSTR, text, SI.JUDGE_SCHEMA, tag, usd, chars=len(text), items=n_items)
    ans = SI.parse_answer(raw)
    flags = SI.apply_judge(s2, ans)
    out = dict(row, status="ok", call=lrow, answer=ans, flags=flags, n_flags=len(flags))
    dump(OUT5 / "incons" / f"{key}-S4.json", out)
    return out


# ---------- 명령 ----------
def main(argv):
    cmd = argv[0]
    if cmd == "sample-it":
        items = sample_it()
        p = dump(OUT5 / "subject" / "it25-context.json", {"seed": IT_SEED, "sizes": IT_SAMPLE, "items": items})
        print(p, len(items), Counter(x["book"] for x in items))
    elif cmd == "freeze":
        print("\n".join(freeze()))
    elif cmd == "subject":
        key = argv[1]
        rule = argv[argv.index("--it") + 1] if "--it" in argv else None
        for tag, (res, rows, p) in run_subject(key, rule).items():
            print(key, tag, rule or "none", json.dumps({"subj": res["subj"], "rule_ok": f"{res['rule_ok']}/{res['rule_n']}",
                                                       "para_mixed": f"{res['para_mixed']}/{res['para_n']}"}, ensure_ascii=False))
    elif cmd == "rescore":
        res = rescore()
        for name, d in res.items():
            print(name, {r: f"{v['ok']}/{v['n']}" + (f" miss{v['missing']}" if v["missing"] else "") for r, v in d.items()})
    elif cmd == "pool":
        _, s = run_pool(argv[1])
        print(json.dumps({k: v for k, v in s.items() if k != "term_occ"}, ensure_ascii=False))
    elif cmd == "span":
        key = argv[1]
        nb = int(argv[argv.index("--batches") + 1]) if "--batches" in argv else None
        _, s = run_span(key, nb, "--dry-run" in argv)
        print(json.dumps(s, ensure_ascii=False))
    elif cmd == "incons":
        outs = run_incons(argv[1])
        print(argv[1], {s: len(f) for s, f in outs.items()})
    elif cmd == "judge":
        r = run_judge(argv[1], "--dry-run" in argv)
        print(json.dumps({k: v for k, v in r.items() if k in ("book", "chars", "items", "est", "status", "n_flags")}, ensure_ascii=False))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
