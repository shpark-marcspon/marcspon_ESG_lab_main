"""MB1 측정 셈(발주서 MB1 §2-5, 호출 0). `python -m evalrig.greenwash.measure_mb1 <명령>`.

명령:
  recall  --evidence <jsonl…> --books <b…> --gold-final <…> --passages <…> --queries <…> [--ceiling <판>] [--diff X:Y]
  flip    --judge <jsonl…> [--within G,…] [--cross G:H …]          G = <model>/<arm> (예 mini/A)
  result1 --judge <jsonl…> --book <b> --gold <정답> --pdf-sha256 <hex> --page-count <n> --out <폴더>
  ledger  --core <calls.jsonl> [--semgraph <…>] [--pdx <…>] [--models <models.json>] [--by tag]
  gm4     --judge <jsonl> --labels <json>                               (C2)

경로·권 이름은 모두 인자다 — 이 파일에 권 열쇠·회사 이름·단가 숫자를 쓰지 않는다(시험 T11).
근거 줄 꼴(git 밖 드라이버가 씀): {book, claim, arm, rep, text, ids?}. 판정 줄 꼴: {book, claim, arm, rep, model, path, level,
parsed, cutoff, failed}. 셈 정의는 `out/MB1/rules.frozen.md`(실호출 전에 얼림)와 같다.
"""
from __future__ import annotations

import argparse
import itertools
import json
import random
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

RUN_LEN = 60
RISK = ("High", "Mid")
SAFE = ("NonRisk", "Low")
BOOT_N, BOOT_SEED = 2000, 0
PRICE_TOL = 0.0005
SCHEMA_V1 = "greenwash.result@1"


# ---------------- 글자 규칙 ----------------
def norm(s: str) -> str:
    """NFKC + 공백 전부 지움."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def found(passage: str, evidence: str, run: int = RUN_LEN) -> bool:
    """단락이 근거 안에 길이 min(run, 단락 길이) 이상의 연속 같은 글자열을 가지면 찾음."""
    p, e = norm(passage), norm(evidence)
    n = min(run, len(p))
    if n == 0:
        return False
    return any(p[i:i + n] in e for i in range(len(p) - n + 1))


def read_jsonl(paths: Iterable[str]) -> list[dict[str, Any]]:
    out = []
    for p in paths:
        out += [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines() if x.strip()]
    return out


def boot_ci(values: list[float], n: int = BOOT_N, seed: int = BOOT_SEED) -> list[float] | None:
    """평균의 부트스트랩 95% 범위(단위 재표집)."""
    if not values:
        return None
    rnd = random.Random(seed)
    k = len(values)
    ms = sorted(sum(values[rnd.randrange(k)] for _ in range(k)) / k for _ in range(n))
    return [round(ms[int(0.025 * n)], 4), round(ms[int(0.975 * n) - 1], 4)]


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


# ---------------- recall ----------------
def recall(rows: list[dict[str, Any]], books: dict[str, dict[str, Any]], ceiling: str | None = None) -> dict[str, Any]:
    """books[b] = {core: {claim: [pid]}, passages: {pid: text}, claims: {claim: text}}.
    판(arm)마다: 주장별 찾은 core / core 수 → 회차 평균 → 미시 평균(분모 = core ≥ 1 인 주장)."""
    by = defaultdict(lambda: defaultdict(dict))  # arm → (book, claim) → rep → row
    for r in rows:
        by[r["arm"]][(r["book"], r["claim"])][r.get("rep", 1)] = r
    ceil_found: dict[tuple[str, str], set[str]] = {}
    if ceiling and ceiling in by:
        for (b, c), reps in by[ceiling].items():
            r = next(iter(reps.values()))
            ceil_found[(b, c)] = {p for p in books[b]["core"].get(c, []) if found(books[b]["passages"][p], r["text"])}
    out: dict[str, Any] = {"runLen": RUN_LEN, "arms": {}, "warnings": []}
    for arm, units in sorted(by.items()):
        per_claim, per_any, per_ceil, per_id, self_in = {}, {}, {}, {}, []
        for (b, c), reps in sorted(units.items()):
            core = books[b]["core"].get(c, [])
            if c in books[b]["claims"]:
                self_in += [found(books[b]["claims"][c], r["text"]) for r in reps.values()]
            if not core:
                continue
            fr, an, ce, idr = [], [], [], []
            for r in reps.values():
                got = {p for p in core if found(books[b]["passages"][p], r["text"])}
                fr.append(len(got) / len(core))
                an.append(1.0 if got else 0.0)
                if (b, c) in ceil_found and ceil_found[(b, c)]:
                    ce.append(len(got & ceil_found[(b, c)]) / len(ceil_found[(b, c)]))
                if "ids" in r:
                    ir = len(set(core) & set(r["ids"])) / len(core)
                    idr.append(ir)
                    if len(got) / len(core) < ir:
                        out["warnings"].append({"arm": arm, "book": b, "claim": c, "rep": r.get("rep", 1),
                                                "charRecall": round(len(got) / len(core), 4), "idRecall": round(ir, 4)})
            per_claim[(b, c)] = sum(fr) / len(fr)
            per_any[(b, c)] = sum(an) / len(an)
            if ce:
                per_ceil[(b, c)] = sum(ce) / len(ce)
            if idr:
                per_id[(b, c)] = sum(idr) / len(idr)
        bks = sorted({b for b, _ in per_claim})
        out["arms"][arm] = {
            "denominator": len(per_claim),
            "mean": _mean(list(per_claim.values())),
            "ci95": boot_ci(list(per_claim.values())),
            "anyRatio": _mean(list(per_any.values())),
            "withinCeiling": _mean(list(per_ceil.values())),
            "ceilingDenominator": len(per_ceil),
            "idMean": _mean(list(per_id.values())),
            "selfSentenceInEvidence": _mean([1.0 if x else 0.0 for x in self_in]),
            "perBook": {b: {"denominator": sum(1 for k in per_claim if k[0] == b),
                            "mean": _mean([v for k, v in per_claim.items() if k[0] == b]),
                            "anyRatio": _mean([v for k, v in per_any.items() if k[0] == b])} for b in bks},
            "_perClaim": {f"{b}:{c}": v for (b, c), v in per_claim.items()},
        }
    return out


def recall_diff(res: dict[str, Any], x: str, y: str) -> dict[str, Any]:
    """판 x − 판 y 재현(같은 주장 교집합, 미시 평균) + 부트스트랩 95%."""
    px, py = res["arms"][x]["_perClaim"], res["arms"][y]["_perClaim"]
    ks = sorted(set(px) & set(py))
    d = [px[k] - py[k] for k in ks]
    return {"x": x, "y": y, "denominator": len(ks), "diff": _mean(d), "ci95": boot_ci(d)}


# ---------------- flip ----------------
def valid(r: dict[str, Any]) -> bool:
    return r.get("path") == "eval" and bool(r.get("parsed")) and not r.get("cutoff") and not r.get("failed") \
        and r.get("level") is not None


def _outside(rows: list[dict[str, Any]]) -> dict[str, int]:
    c = Counter()
    for r in rows:
        if r.get("path") == "low":
            c["low"] += 1
        elif r.get("failed"):
            c["failed"] += 1
        elif r.get("cutoff"):
            c["cutoff"] += 1
        elif not r.get("parsed"):
            c["parsedFalse"] += 1
    return {k: c.get(k, 0) for k in ("low", "parsedFalse", "cutoff", "failed")}


def _group(rows: list[dict[str, Any]], sel: str) -> dict[tuple[str, str], dict[int, dict[str, Any]]]:
    model, arm = sel.split("/")
    g: dict[tuple[str, str], dict[int, dict[str, Any]]] = defaultdict(dict)
    for r in rows:
        if r.get("model") == model and r.get("arm") == arm:
            g[(r["book"], r["claim"])][int(r["rep"])] = r
    return g


def _pairs_within(g, k):
    """그 주장의 같은 판 회차 쌍 중 둘 다 유효한 쌍(쌍 단위 — rules.frozen.md)."""
    reps = sorted(r for r in g.get(k, {}) if valid(g[k][r]))
    return [(g[k][i]["level"], g[k][j]["level"]) for i, j in itertools.combinations(reps, 2)]


def _pairs_between(gx, gy, k):
    rx = sorted(r for r in gx.get(k, {}) if valid(gx[k][r]))
    ry = sorted(r for r in gy.get(k, {}) if valid(gy[k][r]))
    return [(gx[k][i]["level"], gy[k][j]["level"]) for i, j in itertools.product(rx, ry)]


def _rate(pairs) -> float:
    return sum(1.0 for a, b in pairs if a != b) / len(pairs)


def _units(groups) -> int:
    return sum(1 for g in groups for k in g for r in g[k].values() if valid(r))


def flip(rows: list[dict[str, Any]], within: list[str], cross: list[tuple[str, str]]) -> dict[str, Any]:
    """판 안·사이 등급 다름. 쌍은 둘 다 유효(`valid`)일 때만 센다 — 한 회차만 무효인 주장은 남은 쌍으로 남는다.
    주장마다 센 쌍의 다름 비 → 주장 평균. Δ = 사이 − 평균(안 X, 안 Y), 안은 사이가 센 주장 안에서만."""
    out: dict[str, Any] = {"within": {}, "cross": []}
    for sel in within:
        g = _group(rows, sel)
        per = [_rate(ps) for k in sorted(g) if (ps := _pairs_within(g, k))]
        sub = [r for k in g for r in g[k].values()]
        out["within"][sel] = {"denominator": len(per), "units": _units([g]), "diff": _mean(per), "ci95": boot_ci(per),
                              "outside": _outside([r for r in sub if not valid(r)]), "rows": len(sub)}
    for x, y in cross:
        gx, gy = _group(rows, x), _group(rows, y)
        ks = [k for k in sorted(set(gx) & set(gy)) if _pairs_between(gx, gy, k)]
        tr: Counter = Counter()
        bt, wx, wy, per_d = [], [], [], []
        for k in ks:
            pb = _pairs_between(gx, gy, k)
            tr.update(f"{a}->{b}" for a, b in pb)
            b_k = _rate(pb)
            bt.append(b_k)
            ws = []
            for g, acc in ((gx, wx), (gy, wy)):
                pw = _pairs_within(g, k)
                if pw:
                    acc.append(_rate(pw))
                    ws.append(_rate(pw))
            per_d.append(b_k - (sum(ws) / len(ws) if ws else 0.0))
        w_avg = [v for v in (_mean(wx), _mean(wy)) if v is not None]
        w_mean = sum(w_avg) / len(w_avg) if w_avg else 0.0
        sub = [r for g in (gx, gy) for k in g for r in g[k].values()]
        out["cross"].append({
            "x": x, "y": y, "denominator": len(ks), "units": _units([gx, gy]),
            "withinX": _mean(wx), "withinXClaims": len(wx), "withinY": _mean(wy), "withinYClaims": len(wy),
            "between": _mean(bt),
            "delta": round((_mean(bt) or 0.0) - w_mean, 4) if ks else None,
            "deltaCi95": boot_ci(per_d),
            "transitions": dict(sorted(tr.items())),
            "outside": _outside([r for r in sub if not valid(r)]),
            "claimsDropped": len(set(gx) | set(gy)) - len(ks),
        })
    return out


# ---------------- result1 ----------------
def result1(rows: list[dict[str, Any]], gold: dict[str, Any], sha256: str, page_count: int,
            config: dict[str, Any]) -> dict[tuple[str, str, int], dict[str, Any]]:
    """(model, arm, rep)마다 최소 greenwash.result@1. NonRisk·실패는 items 밖. pages = 정답 주장 쪽들(판 사이 같음)."""
    src = gold["meta"]["source"]
    if src["sha256"] != sha256:
        raise ValueError("정답의 source.sha256 과 --pdf-sha256 이 다르다")
    claims = {it["claimId"]: it for it in gold["items"]}
    pages = sorted({int(it["page"]) for it in gold["items"]})
    runs: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        runs[(r["model"], r["arm"], int(r["rep"]))].append(r)
    out = {}
    for k, rs in sorted(runs.items()):
        items = [{"page": int(claims[r["claim"]]["page"]), "sentence": claims[r["claim"]]["sentence"], "level": r["level"]}
                 for r in sorted(rs, key=lambda r: r["claim"]) if r.get("level") in ("High", "Mid", "Low")]
        out[k] = {"schemaVersion": SCHEMA_V1, "source": {"sha256": sha256, "pageCount": page_count}, "pages": pages,
                  "config": config, "models": {"chat": k[0]}, "candidateStats": {"failedPages": []}, "items": items}
    return out


# ---------------- ledger ----------------
def _cost(price: tuple[float, float, float, float], inp: int, cached: int, write: int, outp: int) -> float:
    """inp = 입력 토큰 전부(캐시 읽기·쓰기 포함)."""
    pi, pc, pw, po = price
    return ((inp - cached - write) * pi + cached * pc + write * pw + outp * po) / 1e6


def load_prices(path: str) -> dict[str, tuple[float, float, float, float]]:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for k, v in d.get("models", {}).items():
        p = v.get("pricing")
        if p:
            out[k] = (p["input"], p.get("cacheRead", p["input"]), p.get("cacheWrite", p["input"]), p.get("output", 0.0))
    for k, v in d.get("embeddingModels", {}).items():
        p = v["pricing"]
        out[k] = (p["input"], p["input"], p["input"], 0.0)
    return out


def _measure(label: str) -> str:
    m = re.match(r"mb1:(gm\d)", label or "")
    return m.group(1) if m else "other"


def ledger(core: list[dict[str, Any]], semgraph: list[dict[str, Any]], pdx: list[dict[str, Any]],
           prices: dict[str, tuple[float, float, float, float]] | None = None) -> dict[str, Any]:
    """입력 토큰은 출처마다 뜻이 다르다 — core `inputTokens` 는 캐시 읽기·쓰기를 **뺀** 수(core cost.py), semgraph·pdx 는
    캐시를 **포함한** 수(OpenAI usage). 줄마다 입력 전부(inp)·캐시 읽기·캐시 쓰기로 맞춘 뒤 셈한다."""
    lines = []  # (source, measure, model, inp 전부, cached, write, out, usd)
    for r in core:
        if r.get("lab", {}).get("mode") != "live" or r.get("costUsd") is None:
            continue
        cr, cw = r.get("cacheReadTokens") or 0, r.get("cacheWriteTokens") or 0
        lines.append(("core", _measure(r["lab"].get("label")), r["model"], (r.get("inputTokens") or 0) + cr + cw,
                      cr, cw, r.get("outputTokens") or 0, float(r["costUsd"])))
    for r in semgraph:
        if r.get("dry_run"):
            continue
        lines.append(("semgraph", _measure(r.get("tag")), r["model"], r.get("inputTokens") or 0,
                      r.get("cachedTokens") or 0, 0, r.get("outputTokens") or 0, float(r["costUsd"])))
    for r in pdx:
        if r.get("dry_run"):
            continue
        lines.append(("pdx", "gm4", r["model"], r.get("input_tokens") or r.get("inputTokens") or 0,
                      r.get("cached_tokens") or r.get("cachedTokens") or 0, 0,
                      r.get("output_tokens") or r.get("outputTokens") or 0, float(r.get("usd", r.get("costUsd", 0.0)))))
    out: dict[str, Any] = {"totalUsd": round(sum(x[7] for x in lines), 6), "calls": len(lines),
                           "bySource": {}, "byMeasure": {}, "byModel": {}, "warnings": []}
    for idx, key in ((0, "bySource"), (1, "byMeasure"), (2, "byModel")):
        agg: dict[str, dict[str, float]] = defaultdict(lambda: {"calls": 0, "inputTokens": 0, "outputTokens": 0, "usd": 0.0})
        for x in lines:
            a = agg[x[idx]]
            a["calls"] += 1
            a["inputTokens"] += x[3]
            a["outputTokens"] += x[6]
            a["usd"] = round(a["usd"] + x[7], 6)
        out[key] = dict(sorted(agg.items()))
    by_mm: dict[str, dict[str, float]] = defaultdict(lambda: {"calls": 0, "inputTokens": 0, "usd": 0.0})
    for x in lines:
        a = by_mm[f"{x[1]}/{x[2]}"]
        a["calls"] += 1
        a["inputTokens"] += x[3]
        a["usd"] = round(a["usd"] + x[7], 6)
    out["byMeasureModel"] = dict(sorted(by_mm.items()))
    if prices is not None:
        for i, x in enumerate(lines):
            if x[2] not in prices:
                out["warnings"].append({"line": i, "model": x[2], "why": "단가 없음"})
                continue
            c = _cost(prices[x[2]], x[3], x[4], x[5], x[6])
            if abs(c - x[7]) > PRICE_TOL:
                out["warnings"].append({"line": i, "source": x[0], "model": x[2], "ledgerUsd": x[7], "recomputedUsd": round(c, 6)})
    return out


# ---------------- gm4 (C2) ----------------
def cand_key(page: int, sentence: str) -> str:
    """후보 열쇠 = p<쪽>:<정규화 문장 sha12> — 라벨 파일의 열쇠."""
    from evalrig.greenwash import units as U
    return f"p{int(page)}:{U.sha12(sentence)}"


def gm4(rows: list[dict[str, Any]], labels: dict[str, str]) -> dict[str, Any]:
    """표 덧붙임 전후(arm before|after × rep 1·2). 등급을 위험(High·Mid)/비위험(NonRisk·Low)으로 접어 센다.
    다수 = 두 회차가 같음. 분모 = 네 줄이 모두 유효인 후보. 바닥 = 전 두 회차의 갈래가 다른 후보 비."""
    runs: dict[tuple[str, int], set[str]] = defaultdict(set)
    by: dict[str, dict[tuple[str, int], dict[str, Any]]] = defaultdict(dict)
    for r in rows:
        k = cand_key(r["page"], r["sentence"])
        runs[(r["arm"], int(r["rep"]))].add(k)
        by[k][(r["arm"], int(r["rep"]))] = r
    sets = list(runs.values())
    frozen_ok = bool(sets) and all(x == sets[0] for x in sets)
    cls = lambda r: "risk" if r["level"] in RISK else "safe"  # noqa: E731
    need = [("before", 1), ("before", 2), ("after", 1), ("after", 2)]
    den, floor, down, up, outside = [], 0, [], [], []
    for k, d in sorted(by.items()):
        if not all(x in d and valid(d[x]) for x in need):
            outside += [d[x] for x in d if not valid(d[x])]
            continue
        den.append(k)
        b1, b2, a1, a2 = (cls(d[x]) for x in need)
        if b1 != b2:
            floor += 1
        if b1 == b2 == "risk" and a1 == a2 == "safe":
            down.append(k)
        if b1 == b2 == "safe" and a1 == a2 == "risk":
            up.append(k)
    lab = lambda ks: dict(sorted(Counter(labels.get(k, "unlabeled") for k in ks).items()))  # noqa: E731
    no_table = [k for k in down if not any(by[k][("after", r)].get("evidenceHasTable") for r in (1, 2))]
    after_rows = [r for r in rows if r["arm"] == "after" and valid(r)]
    n = len(den)
    ratio = len(down) / n if n else None
    fl = floor / n if n else None
    return {
        "candidates": len(by), "denominator": n, "frozenOk": frozen_ok,
        "floor": round(fl, 4) if fl is not None else None, "floorCount": floor,
        "downCount": len(down), "downRatio": round(ratio, 4) if ratio is not None else None,
        "downRatioOfBeforeRisk": _mean([1.0 if k in down else 0.0 for k in den
                                        if cls(by[k][("before", 1)]) == cls(by[k][("before", 2)]) == "risk"]),
        "excess": round(ratio - fl, 4) if n else None,
        "downLabels": lab(down), "upCount": len(up), "upLabels": lab(up),
        "downWithoutTable": len(no_table),
        "evidenceHasTableAfter": _mean([1.0 if r.get("evidenceHasTable") else 0.0 for r in after_rows]),
        "outside": _outside(outside),
        "_down": down, "_up": up,
    }


# ---------------- CLI ----------------
def _books(a) -> dict[str, dict[str, Any]]:
    if not (len(a.books) == len(a.gold_final) == len(a.passages) == len(a.queries)):
        raise SystemExit("--books·--gold-final·--passages·--queries 의 수가 같아야 한다")
    out = {}
    for b, gf, ps, qs in zip(a.books, a.gold_final, a.passages, a.queries):
        g = json.loads(Path(gf).read_text(encoding="utf-8"))
        out[b] = {"core": {k: [x["id"] for x in v if x.get("label") == "core"] for k, v in g.items()},
                  "passages": {p["id"]: p["text"] for p in json.loads(Path(ps).read_text(encoding="utf-8"))},
                  "claims": {q["id"]: q["text"] for q in json.loads(Path(qs).read_text(encoding="utf-8"))
                             if q.get("type") == "greenwash"}}
    return out


def _dump(obj: Any, out: str | None) -> None:
    s = json.dumps(obj, ensure_ascii=False, indent=1)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(s + "\n", encoding="utf-8")
    print(s)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="measure_mb1")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("recall")
    s.add_argument("--evidence", nargs="+", required=True)
    s.add_argument("--books", nargs="+", required=True)
    s.add_argument("--gold-final", nargs="+", required=True)
    s.add_argument("--passages", nargs="+", required=True)
    s.add_argument("--queries", nargs="+", required=True)
    s.add_argument("--ceiling", default=None)
    s.add_argument("--diff", nargs="*", default=[])
    s.add_argument("--out", default=None)
    s = sub.add_parser("flip")
    s.add_argument("--judge", nargs="+", required=True)
    s.add_argument("--within", default="")
    s.add_argument("--cross", nargs="*", default=[])
    s.add_argument("--out", default=None)
    s = sub.add_parser("result1")
    s.add_argument("--judge", nargs="+", required=True)
    s.add_argument("--book", required=True)
    s.add_argument("--gold", required=True)
    s.add_argument("--pdf-sha256", required=True)
    s.add_argument("--page-count", type=int, required=True)
    s.add_argument("--config", default=None, help="판 사이 같은 설정 JSON 파일(없으면 {'engine': 'e0'})")
    s.add_argument("--out", required=True)
    s = sub.add_parser("ledger")
    s.add_argument("--core", nargs="*", default=[])
    s.add_argument("--semgraph", nargs="*", default=[])
    s.add_argument("--pdx", nargs="*", default=[])
    s.add_argument("--models", default=None)
    s.add_argument("--by", default=None)
    s.add_argument("--out", default=None)
    s = sub.add_parser("gm4")
    s.add_argument("--judge", nargs="+", required=True)
    s.add_argument("--labels", required=True)
    s.add_argument("--keys", action="store_true", help="옮긴 후보 열쇠도 싣는다(라벨 달기용, git 밖 산출에만)")
    s.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "recall":
        res = recall(read_jsonl(a.evidence), _books(a), a.ceiling)
        res["diffs"] = [recall_diff(res, *d.split(":")) for d in a.diff]
        for v in res["arms"].values():
            v.pop("_perClaim")
        _dump(res, a.out)
    elif a.cmd == "flip":
        _dump(flip(read_jsonl(a.judge), [x for x in a.within.split(",") if x],
                   [tuple(x.split(":")) for x in a.cross]), a.out)
    elif a.cmd == "result1":
        rows = [r for r in read_jsonl(a.judge) if r["book"] == a.book]
        cfg = json.loads(Path(a.config).read_text(encoding="utf-8")) if a.config else {"engine": "e0"}
        res = result1(rows, json.loads(Path(a.gold).read_text(encoding="utf-8")), a.pdf_sha256, a.page_count, cfg)
        for (model, arm, rep), doc in res.items():
            d = Path(a.out) / f"{model}.{arm}.r{rep}"
            d.mkdir(parents=True, exist_ok=True)
            (d / "result.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps({"runs": len(res), "out": a.out}, ensure_ascii=False))
    elif a.cmd == "ledger":
        res = ledger(read_jsonl(a.core), read_jsonl(a.semgraph), read_jsonl(a.pdx),
                     load_prices(a.models) if a.models else None)
        _dump(res, a.out)
        if res["warnings"]:
            print(f"경고 {len(res['warnings'])}", file=sys.stderr)
    elif a.cmd == "gm4":
        lb = json.loads(Path(a.labels).read_text(encoding="utf-8")) if Path(a.labels).exists() else {}
        res = gm4(read_jsonl(a.judge), {k: v["label"] if isinstance(v, dict) else v for k, v in lb.items()})
        if not a.keys:
            res.pop("_down")
            res.pop("_up")
        _dump(res, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
