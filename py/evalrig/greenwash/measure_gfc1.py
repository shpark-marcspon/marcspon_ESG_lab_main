"""GFC1 — 그린워싱 금융 대 일반 보고서 비교 측정의 셈(호출 0, 발주서 `GFC1-order.md` §2-6).

묻는 것: 코랩 판정 기준 15줄(G1 기본 판)만으로 금융업 보고서의 환경·ESG 주장을 일반 업종 보고서만큼 덮는가.
LLM(G1 흐름·종류 나눔) 호출은 git 밖 드라이버가 하고, 이 파일은 그 산출을 읽어 센다. 정답 없음 — 기준 줄 hit 는
luna 의 답이고, 「덮임」은 hit 집합의 코드 정의다(품질 주장 아님).

명령(`python -m evalrig.greenwash.measure_gfc1 <명령>`):
  pages     대상 쪽 거름(파트 합집합이 cap 넘으면 쪽 점수 높은 차례, 같으면 앞 쪽)
  tally     권·무리별 후보 갈래·종류 분포·덮임 갈래·U·줄별 hit
  floor     GE1 r4 회차 쌍의 덮임 갈래 다름(흔들림 바닥)
  detectcov 금융 낱말 문장의 탐지 덮임(F3)
  eyecheck  러너 눈검사 표본 뽑기 · 일치율
  f5        후보 줄 다시 판정(A15 대 A15K)
  ledger    장부 다시 셈
  verdict   §3-4 규칙 대입 → summary.{json,md}
권 열쇠·회사 이름·단가 숫자는 이 파일에 없다(경로·열쇠는 인자, 단가는 models 파일에서). 산출은 수·코드·줄 id·이름표만.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import random
import re
import unicodedata
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable

# ── 얼린 정의(rules.frozen.md 와 같은 값 — 고치면 실호출 전 감독에게) ──
LINES: tuple[str, ...] = (
    "keyword-ghg", "keyword-exclusion", "evidence-quantity", "evidence-baseline", "evidence-boundary",
    "evidence-offset", "high-1", "high-2", "high-3", "high-4", "mid-1",
    "exception-1", "exception-2", "exception-3", "exception-4",
)
SPECIFIC = frozenset({"keyword-ghg", "evidence-offset", "high-1", "high-2", "high-3",
                      "exception-1", "exception-2", "exception-3", "exception-4"})
GENERIC = frozenset({"keyword-exclusion", "evidence-quantity", "evidence-baseline", "evidence-boundary", "high-4", "mid-1"})
CANDIDATE_PREFIX = "fin-"  # F5 후보 줄 id 머리 — 후보 줄은 특정 쟁점 줄이라 specific 쪽

G_TYPES = ("G-ghg-target", "G-ghg-result", "G-energy", "G-resource", "G-product", "G-nature", "G-cert", "G-gov", "G-other")
F_TYPES = ("F-product", "F-label", "F-financed", "F-portfolio-target", "F-exclusion", "F-integration",
           "F-climate-risk", "F-other")
TYPES = G_TYPES + F_TYPES
UNSTABLE = "unstable"
UNSTABLE_FAILED = "unstable_failed"

FIN_TERMS: tuple[str, ...] = (
    "녹색금융", "지속가능금융", "ESG 금융", "녹색채권", "지속가능채권", "사회적채권", "그린본드", "녹색여신", "ESG 여신",
    "ESG 투자", "책임투자", "임팩트 투자", "금융배출량", "투자배출량", "financed emissions", "PCAF", "NZBA", "NZAM",
    "GFANZ", "탈석탄", "석탄금융", "투자 배제", "네거티브 스크리닝", "스튜어드십", "기후 스트레스", "ESG 펀드", "전환금융",
    "녹색분류체계", "K-택소노미",
    # 러너가 더함(실호출 전, rules.frozen.md 에 적음)
    "녹색 금융", "지속가능 금융", "그린 본드", "녹색 채권", "지속가능연계", "ESG채권", "ESG 채권", "ESG여신", "ESG투자",
    "ESG금융", "기후금융", "석탄 금융", "석탄발전", "Net-Zero Banking", "Net Zero Asset", "포트폴리오 배출",
    "자산 포트폴리오", "택소노미", "스트레스 테스트", "스트레스테스트", "적도원칙",
)

# 판정 규칙(§3-4 + critic B1·B2). 백분율 정수로 둔다.
F_SHARE_PCT = 15  # (a) 금융 판정 받음 중 F-*
F_SHARE_BOOK_PCT = 10  # (a) 권별
BOOKS_MIN = 3  # 금융 넷 중
U_DIFF_PCT = 10  # (b) U(금융 F-*) − U(일반 전부)
F_MIN = 20  # F-* 판정 받음 분모
EYE_MIN_PCT = 70  # B1 눈검사 일치율(두 무리 각각)
DETECT_GAP_PCT = 20  # B2 F3 ⓑ 차
F5_HIT_PCT = 50  # 받침: 후보 줄 hit
F5_FALSE_PCT = 10  # 받침: 일반 오히트
UNSTABLE_WARN_PCT = 20
FAIL_WARN_PCT = 25
CAP_PAGES = 35
PAGE_CANDIDATES_FULL = 4  # 쪽당 후보 상한(비-Low 3 + Low 1)
PAGE_CHARS = 2500
LEDGER_TOL = 5e-4

FAILED_CODES = ("judge_failed", "judge_unparsed", "judge_cut_off", "evidence_search_failed")
EYE_MAP = {"fits": "specific", "forced": "generic_only", "none": "none"}


def pct(n: int, d: int) -> float | None:
    return None if d == 0 else round(100 * n / d, 1)


def squash(s: str | None) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or "")).lower()


def read_json(p: str | Path) -> Any:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def read_jsonl(p: str | Path) -> list[dict[str, Any]]:
    path = Path(p)
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def write_json(p: str | Path, obj: Any) -> None:
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def sha12(p: str | Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:12]


# ══ 덮임 갈래 ══
def coverage_class(hit: Iterable[str] | None, verdict: Any = True) -> str | None:
    """판정 받은 항목의 hit 집합 → specific / generic_only / none. verdict 가 없으면(판정 실패·unplaced) None(분모 밖).
    후보 줄(`fin-*`)은 specific 쪽. 15줄 밖의 다른 id 는 버린다."""
    if verdict is None:
        return None
    hits = {h for h in (hit or ()) if h in SPECIFIC or h in GENERIC or h.startswith(CANDIDATE_PREFIX)}
    if not hits:
        return "none"
    if any(h in SPECIFIC or h.startswith(CANDIDATE_PREFIX) for h in hits):
        return "specific"
    return "generic_only"


def uncovered(cls: str | None) -> bool:
    return cls in ("none", "generic_only")


# ══ 종류 ══
def resolve_type(r1: dict[str, Any] | None, r2: dict[str, Any] | None) -> str:
    """두 회차 → 코드. 한쪽이라도 없음·failed 면 unstable_failed(분모 밖), 다르면 unstable."""
    if not r1 or not r2 or r1.get("failed") or r2.get("failed") or r1.get("type") not in TYPES or r2.get("type") not in TYPES:
        return UNSTABLE_FAILED
    if r1["type"] != r2["type"]:
        return UNSTABLE
    return str(r1["type"])


def is_f(t: str) -> bool:
    return t.startswith("F-")


def is_g(t: str) -> bool:
    return t.startswith("G-")


# ══ 후보 펼침 ══
def candidates_of(book: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    """G1 result@2 → 후보 줄 [{book, key, page, text, status, code, level, hit, cls, blockIds}].
    status: judged(verdict 있음) · failed(verdict 없음, 코드) · unplaced. 구획 실패는 그 구획 errorCode 하나로 센다."""
    out: list[dict[str, Any]] = []
    u = 0
    for seg in result.get("segments", []):
        for it in seg.get("items", []):
            v = it.get("verdict")
            hit = list((it.get("module") or {}).get("criteriaLines") or [])
            out.append({
                "book": book, "key": it.get("stableKey"), "page": (it.get("target") or {}).get("page"),
                "text": (it.get("target") or {}).get("text"), "blockIds": (it.get("target") or {}).get("blockIds"),
                "status": "judged" if v is not None else "failed",
                "code": it.get("errorCode"), "level": (v or {}).get("level"), "hit": hit,
                "cls": coverage_class(hit, v),
            })
        for x in seg.get("unplaced", []):
            out.append({"book": book, "key": f"u{u}", "page": x.get("page"), "text": x.get("text"), "blockIds": None,
                        "status": "unplaced", "code": x.get("errorCode"), "level": None, "hit": [], "cls": None})
            u += 1
        if seg.get("status") == "failed":
            out.append({"book": book, "key": f"seg:{seg.get('key')}", "page": None, "text": None, "blockIds": None,
                        "status": "failed", "code": seg.get("errorCode") or "segment_failed", "level": None, "hit": [],
                        "cls": None, "segment": True})
    return out


def type_maps(r1: list[dict[str, Any]], r2: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    a = {(r["book"], str(r["key"])): r for r in r1}
    b = {(r["book"], str(r["key"])): r for r in r2}
    return {k: resolve_type(a.get(k), b.get(k)) for k in set(a) | set(b)}


# ══ tally ══
def _cov(rows: list[dict[str, Any]]) -> dict[str, Any]:
    j = [r for r in rows if r["cls"] is not None]
    c = Counter(r["cls"] for r in j)
    u = c["none"] + c["generic_only"]
    return {"n": len(j), "specific": c["specific"], "generic_only": c["generic_only"], "none": c["none"],
            "U": u, "Upct": pct(u, len(j))}


def _lines(rows: list[dict[str, Any]]) -> dict[str, Any]:
    j = [r for r in rows if r["cls"] is not None]
    return {"n": len(j), "hit": {ln: sum(ln in r["hit"] for r in j) for ln in LINES},
            "pct": {ln: pct(sum(ln in r["hit"] for r in j), len(j)) for ln in LINES}}


def tally(cands: list[dict[str, Any]], types: dict[tuple[str, str], str], groups: dict[str, str],
          labels: dict[str, str] | None = None) -> dict[str, Any]:
    labels = labels or {}
    for r in cands:
        r["type"] = types.get((r["book"], str(r["key"])), UNSTABLE_FAILED)
    books: dict[str, Any] = {}
    for b in sorted({r["book"] for r in cands}):
        rows = [r for r in cands if r["book"] == b]
        books[b] = _book(rows) | {"group": groups.get(b), "label": labels.get(b)}
    out: dict[str, Any] = {"books": books, "groups": {}}
    for g in ("general", "finance"):
        rows = [r for r in cands if groups.get(r["book"]) == g]
        out["groups"][g] = _book(rows)
    gen = [r for r in cands if groups.get(r["book"]) == "general"]
    fin = [r for r in cands if groups.get(r["book"]) == "finance"]
    out["coverage"] = {
        "general_all": _cov(gen),
        "general_G": _cov([r for r in gen if is_g(r["type"])]),
        "general_F": _cov([r for r in gen if is_f(r["type"])]),
        "finance_G": _cov([r for r in fin if is_g(r["type"])]),
        "finance_F": _cov([r for r in fin if is_f(r["type"])]),
        "finance_all": _cov(fin),
        "byType": {t: {"general": _cov([r for r in gen if r["type"] == t]),
                       "finance": _cov([r for r in fin if r["type"] == t])} for t in TYPES},
    }
    out["lines"] = {"general": _lines(gen), "finance_G": _lines([r for r in fin if is_g(r["type"])]),
                    "finance_F": _lines([r for r in fin if is_f(r["type"])])}
    out["financeBooksF"] = {b: _cov([r for r in fin if r["book"] == b and is_f(r["type"])]) for b in sorted({r["book"] for r in fin})}
    return out


def _book(rows: list[dict[str, Any]]) -> dict[str, Any]:
    st = Counter(r["status"] for r in rows)
    codes = Counter(r["code"] for r in rows if r["status"] == "failed")
    judged = [r for r in rows if r["status"] == "judged"]
    stable_j = [r for r in judged if r["type"] in TYPES]
    stable_all = [r for r in rows if r["type"] in TYPES]
    nf = sum(is_f(r["type"]) for r in stable_j)
    lv = Counter(r["level"] for r in judged)
    bad = st["failed"] + st["unplaced"]
    return {
        "candidates": len(rows), "judged": st["judged"], "failed": st["failed"], "unplaced": st["unplaced"],
        "failedCodes": dict(sorted(codes.items(), key=lambda x: str(x[0]))),
        "badPct": pct(bad, len(rows)), "badOver": (pct(bad, len(rows)) or 0) > FAIL_WARN_PCT,
        "levels": {k: lv[k] for k in ("High", "Mid", "Low", "None")},
        "types": {t: sum(r["type"] == t for r in stable_all) for t in TYPES},
        "typesJudged": {t: sum(r["type"] == t for r in stable_j) for t in TYPES},
        "unstable": sum(r["type"] == UNSTABLE for r in rows), "unstableFailed": sum(r["type"] == UNSTABLE_FAILED for r in rows),
        "unstablePct": pct(sum(r["type"] in (UNSTABLE, UNSTABLE_FAILED) for r in rows), len(rows)),
        "stableJudged": len(stable_j), "fJudged": nf, "fSharePct": pct(nf, len(stable_j)),
        "fAll": sum(is_f(r["type"]) for r in stable_all), "fShareAllPct": pct(sum(is_f(r["type"]) for r in stable_all), len(stable_all)),
        "coverage": _cov(judged),
    }


# ══ floor (GE1 r4) ══
def floor(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[(r["book"], r["claim"])].append(r)
    acc: dict[str, Counter[str]] = defaultdict(Counter)
    for (b, _), reps in by.items():
        ok = [r for r in reps if r.get("failure") is None and r.get("level") is not None]
        for x, y in combinations(sorted(ok, key=lambda r: r.get("rep", 0)), 2):
            for k in (b, "all"):
                acc[k]["pairs"] += 1
                acc[k]["clsDiff"] += coverage_class(x.get("hit"), x["level"]) != coverage_class(y.get("hit"), y["level"])
                acc[k]["hitDiff"] += set(x.get("hit") or []) != set(y.get("hit") or [])
                acc[k]["levelDiff"] += x["level"] != y["level"]
    return {k: {"pairs": v["pairs"], "clsDiff": v["clsDiff"], "clsDiffPct": pct(v["clsDiff"], v["pairs"]),
                "hitDiff": v["hitDiff"], "hitDiffPct": pct(v["hitDiff"], v["pairs"]),
                "levelDiff": v["levelDiff"], "levelDiffPct": pct(v["levelDiff"], v["pairs"])} for k, v in sorted(acc.items())}


# ══ pages (대상 쪽 거름) ══
def fin_hit(s: str, terms: Iterable[str] = FIN_TERMS) -> bool:
    q = squash(s)
    return any(squash(t) in q for t in terms)


def page_scores(text: str, terms: Iterable[str] = FIN_TERMS) -> tuple[int, int]:
    """쪽 글 → (환경 신호 문장 수, 금융 낱말 문장 수). 1차 heuristics 를 읽어 부름."""
    from greenwash.heuristics import has_env_signal, split_sentences_kor

    ss = split_sentences_kor(text or "")
    return sum(1 for s in ss if has_env_signal(s)), sum(1 for s in ss if fin_hit(s, terms))


def select_pages(union: list[int], scores: dict[int, int], cap: int = CAP_PAGES) -> tuple[list[int], list[int]]:
    """점수 높은 차례 cap 쪽, 같으면 앞 쪽. → (남김 오름차순, 뺌 오름차순)."""
    if len(union) <= cap:
        return sorted(union), []
    order = sorted(union, key=lambda p: (-scores.get(p, 0), p))
    keep = sorted(order[:cap])
    return keep, sorted(set(union) - set(keep))


def cmd_pages(a: argparse.Namespace) -> int:
    parts = read_json(a.parts)
    out: dict[str, Any] = {"cap": a.cap, "rule": "점수 = 환경 신호 문장 + 금융 낱말 문장, 높은 차례, 같으면 앞 쪽", "books": {}}
    for spec in a.book:
        b, ptext = spec.split("=", 1)
        texts = {int(k): v for k, v in read_json(ptext).items()}
        entry = parts["books"][b]
        union = sorted({p for v in entry["parts"].values() for p in v})
        sc = {p: page_scores(texts.get(p, "")) for p in union}
        total = {p: e + f for p, (e, f) in sc.items()}
        env_only = {p: e for p, (e, _) in sc.items()}
        keep, drop = select_pages(union, total, a.cap)
        keep_env, _ = select_pages(union, env_only, a.cap)
        out["books"][b] = {
            "union": len(union), "kept": keep, "dropped": drop, "droppedN": len(drop),
            "finOnlyKept": sorted(set(keep) - set(keep_env)) if drop else [],
            "scores": {str(p): {"env": e, "fin": f} for p, (e, f) in sc.items()},
            "parts": {k: [p for p in v if p in set(keep)] for k, v in entry["parts"].items()},
        }
        print(b, "합집합", len(union), "남김", len(keep), "뺌", len(drop), "금융 낱말로만 듦", len(out["books"][b]["finOnlyKept"]))
    write_json(a.out, out)
    return 0


# ══ detectcov (F3) ══
def detectcov(pages: dict[int, str], cands: list[dict[str, Any]], *, fin: bool = True,
              terms: Iterable[str] = FIN_TERMS) -> dict[str, Any]:
    """쪽 글(G1 이 본 글)마다 문장 — fin=True 면 금융 낱말 문장, False 면 환경 신호 문장(일반 뽑힘 바닥).
    ⓐ has_env_signal · ⓑ 그 쪽 후보(unplaced 포함)와 squash 포함으로 겹침. 쪽당 후보가 상한(4)에 찬 쪽은 따로."""
    from greenwash.heuristics import has_env_signal, split_sentences_kor

    by_page: dict[int, list[str]] = defaultdict(list)
    for c in cands:
        if c.get("page") is not None and c.get("text"):
            by_page[int(c["page"])].append(squash(c["text"]))
    acc = Counter()
    for p, text in pages.items():
        full = p in by_page and len(by_page[p]) >= PAGE_CANDIDATES_FULL
        for s in split_sentences_kor(text or ""):
            pick = fin_hit(s, terms) if fin else has_env_signal(s)
            if not pick:
                continue
            q = squash(s)
            got = any(q and (q in c or c in q) for c in by_page.get(p, []))
            k = "full" if full else "open"
            acc[f"{k}N"] += 1
            acc[f"{k}Env"] += has_env_signal(s)
            acc[f"{k}Got"] += got
    return {"n": acc["openN"], "env": acc["openEnv"], "envPct": pct(acc["openEnv"], acc["openN"]),
            "got": acc["openGot"], "gotPct": pct(acc["openGot"], acc["openN"]),
            "fullPageN": acc["fullN"], "fullPageGot": acc["fullGot"]}


def _merge_dc(xs: list[dict[str, Any]]) -> dict[str, Any]:
    n = sum(x["n"] for x in xs)
    env = sum(x["env"] for x in xs)
    got = sum(x["got"] for x in xs)
    return {"n": n, "env": env, "envPct": pct(env, n), "got": got, "gotPct": pct(got, n),
            "fullPageN": sum(x["fullPageN"] for x in xs), "fullPageGot": sum(x["fullPageGot"] for x in xs)}


def cmd_detectcov(a: argparse.Namespace) -> int:
    books = read_json(a.books)
    res: dict[str, Any] = {"books": {}}
    fin_rows, base_rows, genfin_rows = [], [], []
    for b, meta in sorted(books.items()):
        d = Path(a.f1) / b
        if not (d / "result.json").exists():
            continue
        pages = {int(k): v for k, v in read_json(d / "pagetext.json").items()}
        cands = candidates_of(b, read_json(d / "result.json"))
        f = detectcov(pages, cands, fin=True)
        e = detectcov(pages, cands, fin=False)
        res["books"][b] = {"group": meta["group"], "fin": f, "env": e}
        if meta["group"] == "finance":
            fin_rows.append(f)
        else:
            base_rows.append(e)
            genfin_rows.append(f)
    res["financeFin"] = _merge_dc(fin_rows)
    res["generalEnvBase"] = _merge_dc(base_rows)
    res["generalFin"] = _merge_dc(genfin_rows)
    fb, gb = res["financeFin"]["gotPct"], res["generalEnvBase"]["gotPct"]
    res["gapPctPoint"] = None if fb is None or gb is None else round(gb - fb, 1)
    write_json(a.out, res)
    print(json.dumps({k: res[k] for k in ("financeFin", "generalEnvBase", "gapPctPoint")}, ensure_ascii=False))
    return 0


# ══ eyecheck ══
def eye_sample(rows: list[dict[str, Any]], seed: int, per: int = 10) -> list[dict[str, Any]]:
    """갈래마다 per 건(모자라면 있는 만큼), 씨앗 고정. 열쇠 차례로 정렬한 뒤 뽑는다."""
    rng = random.Random(seed)
    out: list[dict[str, Any]] = []
    for cls in ("specific", "generic_only", "none"):
        pool = sorted([r for r in rows if r["cls"] == cls], key=lambda r: (r["book"], str(r["key"])))
        out += rng.sample(pool, min(per, len(pool)))
    return out


def eye_agreement(labels: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for g in sorted({x["group"] for x in labels}):
        xs = [x for x in labels if x["group"] == g and x.get("label") in EYE_MAP]
        agree = sum(EYE_MAP[x["label"]] == x["cls"] for x in xs)
        out[g] = {"n": len(xs), "agree": agree, "pct": pct(agree, len(xs)),
                  "confusion": dict(Counter(f"{x['cls']}->{x['label']}" for x in xs))}
    return out


# ══ f5 ══
def f5(rows: list[dict[str, Any]], floor_level_pct: float | None) -> dict[str, Any]:
    """rows {book, key, sample: holdoutF|general, arm: A15|A15K, level, hit, failure}."""
    by: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    sample: dict[tuple[str, str], str] = {}
    for r in rows:
        by[(r["book"], str(r["key"]))][r["arm"]] = r
        sample[(r["book"], str(r["key"]))] = r["sample"]
    out: dict[str, Any] = {}
    for s in ("holdoutF", "general"):
        acc = Counter()
        moves = Counter()
        for k, arms in by.items():
            if sample[k] != s:
                continue
            x, y = arms.get("A15"), arms.get("A15K")
            if not x or not y or x.get("failure") or y.get("failure") or x.get("level") is None or y.get("level") is None:
                acc["skipped"] += 1
                continue
            cx, cy = coverage_class(x.get("hit"), x["level"]), coverage_class(y.get("hit"), y["level"])
            acc["n"] += 1
            acc["candHit"] += any(h.startswith(CANDIDATE_PREFIX) for h in y.get("hit") or [])
            acc["U15"] += uncovered(cx)
            acc["U15K"] += uncovered(cy)
            acc["levelDiff"] += x["level"] != y["level"]
            moves[f"{cx}->{cy}"] += 1
        lvp = pct(acc["levelDiff"], acc["n"])
        out[s] = {"n": acc["n"], "skipped": acc["skipped"], "candHit": acc["candHit"], "candHitPct": pct(acc["candHit"], acc["n"]),
                  "U15": acc["U15"], "U15pct": pct(acc["U15"], acc["n"]), "U15K": acc["U15K"], "U15Kpct": pct(acc["U15K"], acc["n"]),
                  "levelDiffPct": lvp,
                  "delta": None if lvp is None or floor_level_pct is None else round(lvp - floor_level_pct, 1),
                  "moves": dict(sorted(moves.items()))}
    h, g = out["holdoutF"], out["general"]
    out["useful"] = (h["candHitPct"] is not None and g["candHitPct"] is not None
                     and h["candHitPct"] >= F5_HIT_PCT and g["candHitPct"] <= F5_FALSE_PCT)
    return out


# ══ ledger ══
def recompute(row: dict[str, Any], models: dict[str, Any]) -> float | None:
    m = row.get("model")
    if m in models.get("embeddingModels", {}):
        return (row.get("inputTokens") or 0) * models["embeddingModels"][m]["pricing"]["input"] / 1e6
    if m not in models.get("models", {}):
        return None
    pr = models["models"][m]["pricing"]
    i, cr, cw, o = (row.get(k) or 0 for k in ("inputTokens", "cacheReadTokens", "cacheWriteTokens", "outputTokens"))
    long = pr.get("longContext")
    if long and i + cr + cw > long["aboveInputTokens"]:
        pr = long
    return (i * pr["input"] + cr * pr.get("cacheRead", pr["input"]) + cw * pr.get("cacheWrite", pr["input"]) + o * pr["output"]) / 1e6


def ledger(files: list[str], models: dict[str, Any]) -> dict[str, Any]:
    per: dict[str, Any] = {}
    warns: list[dict[str, Any]] = []
    tot = float(0)
    for f in sorted(files):
        name = Path(f).stem
        rows = read_jsonl(f)
        kinds: Counter[str] = Counter()
        usd: defaultdict[str, float] = defaultdict(float)
        tok = Counter()
        for i, r in enumerate(rows):
            c = float(r.get("costUsd") or 0)
            kinds[r.get("resultKind") or "?"] += 1
            usd[f"{r.get('model')}|{r.get('resultKind')}"] += c
            for k in ("inputTokens", "cacheReadTokens", "cacheWriteTokens", "outputTokens", "thinkingTokens"):
                tok[k] += r.get(k) or 0
            rc = recompute(r, models)
            if rc is not None and abs(rc - c) > LEDGER_TOL:
                warns.append({"file": name, "row": i, "costUsd": c, "recomputed": round(rc, 8)})
            tot += c
        per[name] = {"rows": len(rows), "kinds": dict(kinds), "usd": {k: round(v, 6) for k, v in sorted(usd.items())},
                     "usdTotal": round(sum(usd.values()), 6), "tokens": dict(tok)}
    return {"files": per, "usdTotal": round(tot, 6), "warnings": warns}


# ══ verdict ══
def verdict(t: dict[str, Any], floor_pct: float | None, eye: dict[str, Any] | None, gap_pp: float | None) -> dict[str, Any]:
    """§3-4 + critic B1·B2. 차례: 분모 → ¬(a)(탐지 누락이면 못 가름) → 눈검사 타당성 → 바닥 → (b)."""
    fin_books = {b: x for b, x in t["books"].items() if x["group"] == "finance"}
    fin = t["groups"]["finance"]
    gen_u = t["coverage"]["general_all"]["Upct"]
    fin_f = t["coverage"]["finance_F"]
    a_share = (fin["fSharePct"] or 0) >= F_SHARE_PCT
    a_books = sum((x["fSharePct"] or 0) >= F_SHARE_BOOK_PCT for x in fin_books.values())
    a = a_share and a_books >= BOOKS_MIN
    diff = None if fin_f["Upct"] is None or gen_u is None else round(fin_f["Upct"] - gen_u, 1)
    b_books = 0
    for bk in fin_books:
        u_book = t["financeBooksF"].get(bk, {}).get("Upct")
        b_books += u_book is not None and gen_u is not None and u_book > gen_u
    b = (diff is not None and diff >= U_DIFF_PCT and (floor_pct is None or diff > floor_pct) and b_books >= BOOKS_MIN)
    # rules §6·§8-2: 눈검사는 필수 — 없거나 한 무리뿐이면 타당성 문을 못 넘는다(검증 권고 R3)
    eye_ok = bool(eye) and len(eye) >= 2 and all((g.get("pct") or 0) >= EYE_MIN_PCT for g in eye.values())
    detect_gap = gap_pp is not None and gap_pp >= DETECT_GAP_PCT
    why: list[str] = []
    if fin_f["n"] < F_MIN:
        answer = "못 가름 — F-* 판정 받음 분모 부족"
    elif not a:
        answer = "못 가름 — 탐지 누락" if detect_gap else "아니오 — 금융 고유 주장이 드묾"
    elif not eye_ok:
        answer = "못 가름 — 덮임 지표 타당성 부족"
    elif diff is None or floor_pct is not None and abs(diff) <= floor_pct:
        answer = "못 가름 — 차가 흔들림 바닥 안"
    elif b:
        answer = "예 — 금융 특화 기준이 따로 필요"
    else:
        answer = "아니오 — 15줄이 덮음"
    if detect_gap:
        why.append("탐지(②)가 먼저")
    return {"answer": answer, "a": a, "aSharePct": fin["fSharePct"], "aBooks": a_books, "b": b, "diffPctPoint": diff,
            "bBooks": b_books, "floorPct": floor_pct, "eyeOk": eye_ok, "detectGapPctPoint": gap_pp, "fJudged": fin_f["n"],
            "notes": why}


# ══ 명령 ══
def _results(a: argparse.Namespace) -> list[dict[str, Any]]:
    cands: list[dict[str, Any]] = []
    for spec in a.results:
        b, p = spec.split("=", 1)
        cands += candidates_of(b, read_json(p))
    return cands


def _books(path: str) -> tuple[dict[str, str], dict[str, str]]:
    bk = read_json(path)
    return {b: v["group"] for b, v in bk.items()}, {b: v.get("label", b) for b, v in bk.items()}


def cmd_tally(a: argparse.Namespace) -> int:
    groups, labels = _books(a.books)
    cands = _results(a)
    types = type_maps(read_jsonl(a.types[0]), read_jsonl(a.types[1])) if a.types else {}
    out = tally(cands, types, groups, labels)
    write_json(a.out, out)
    print(json.dumps({g: {k: v for k, v in x.items() if k in ("candidates", "judged", "fSharePct", "unstablePct")}
                      for g, x in out["groups"].items()}, ensure_ascii=False))
    print(json.dumps({k: v["Upct"] for k, v in out["coverage"].items() if k != "byType"}, ensure_ascii=False))
    return 0


def cmd_floor(a: argparse.Namespace) -> int:
    rows = [r for f in sorted(glob.glob(str(Path(a.rounds) / "judge.*.r*.jsonl"))) for r in read_jsonl(f)]
    out = floor(rows)
    write_json(a.out, out)
    print(json.dumps(out, ensure_ascii=False))
    return 0


def cmd_eyecheck(a: argparse.Namespace) -> int:
    if a.labels:
        out = eye_agreement(read_json(a.labels))
        print(json.dumps(out, ensure_ascii=False))
        if a.out:
            write_json(a.out, out)
        return 0
    groups, _ = _books(a.books)
    cands = _results(a)
    types = type_maps(read_jsonl(a.types[0]), read_jsonl(a.types[1]))
    for r in cands:
        r["type"] = types.get((r["book"], str(r["key"])), UNSTABLE_FAILED)
    fin_f = [r for r in cands if groups.get(r["book"]) == "finance" and is_f(r["type"]) and r["cls"]]
    gen_g = [r for r in cands if groups.get(r["book"]) == "general" and is_g(r["type"]) and r["cls"]]
    pick = [r | {"group": "finance_F"} for r in eye_sample(fin_f, a.seed)] + \
           [r | {"group": "general_G"} for r in eye_sample(gen_g, a.seed + 1)]
    write_json(a.out, [{k: r[k] for k in ("group", "book", "key", "page", "type", "cls", "hit", "text")} | {"label": None}
                       for r in pick])
    print("눈검사 표본", Counter(r["group"] for r in pick), Counter((r["group"], r["cls"]) for r in pick))
    return 0


def cmd_f5(a: argparse.Namespace) -> int:
    fl = read_json(a.floor)["all"]["levelDiffPct"] if a.floor else None
    out = f5(read_jsonl(a.judge), fl)
    write_json(a.out, out)
    print(json.dumps(out, ensure_ascii=False))
    return 0


def cmd_ledger(a: argparse.Namespace) -> int:
    files = sorted({f for pat in a.calls for f in glob.glob(pat)})
    models = read_json(a.models)
    out = ledger(files, models) | {"models": {"path": a.models, "sha16": hashlib.sha256(Path(a.models).read_bytes()).hexdigest()[:16],
                                              "checkedOn": models.get("checkedOn")}}
    if a.out:
        write_json(a.out, out)
    print(json.dumps({"usdTotal": out["usdTotal"], "warnings": len(out["warnings"]),
                      "files": {k: v["usdTotal"] for k, v in out["files"].items()}}, ensure_ascii=False))
    return 0


def cmd_verdict(a: argparse.Namespace) -> int:
    t = read_json(a.tally)
    fl = read_json(a.floor)["all"]["clsDiffPct"] if a.floor else None
    eye = read_json(a.eye) if a.eye else None
    dc = read_json(a.detect) if a.detect else None
    f5r = read_json(a.f5) if a.f5 else None
    v = verdict(t, fl, eye, dc["gapPctPoint"] if dc else None)
    summary = {"verdict": v, "tally": {"groups": t["groups"], "coverage": {k: t["coverage"][k] for k in t["coverage"] if k != "byType"},
                                       "financeBooksF": t["financeBooksF"]},
               "floor": read_json(a.floor) if a.floor else None, "eye": eye, "detect": dc, "f5": f5r,
               "ledger": read_json(a.ledger) if a.ledger else None}
    write_json(Path(a.out) / "summary.json", summary)
    (Path(a.out) / "summary.md").write_text(render_md(summary, t), encoding="utf-8")
    print(json.dumps(v, ensure_ascii=False))
    return 0


def render_md(s: dict[str, Any], t: dict[str, Any]) -> str:
    v = s["verdict"]
    L = ["# GFC1 summary (수·코드·이름표만 — LLM(luna) hit, 정답 없음)", "",
         f"**§0 답: {v['answer']}** — (a) {v['a']}(F-* {v['aSharePct']}%, 권 {v['aBooks']}/4) · (b) {v['b']}"
         f"(차 {v['diffPctPoint']}%p, 바닥 {v['floorPct']}%, 권 {v['bBooks']}/4) · 눈검사 {v['eyeOk']} · 탐지 차 {v['detectGapPctPoint']}%p", "",
         "## 권", "| 권 | 무리 | 후보 | 판정 받음 | 실패 | unplaced | 나쁨% | F-*(판정)% | unstable% | U% |", "|---|---|---|---|---|---|---|---|---|---|"]
    for b, x in t["books"].items():
        L.append(f"| {x['label']} | {x['group']} | {x['candidates']} | {x['judged']} | {x['failed']} | {x['unplaced']} | {x['badPct']} | "
                 f"{x['fSharePct']} | {x['unstablePct']} | {x['coverage']['Upct']} |")
    L += ["", "## 덮임 (U = none + generic_only, 분모 = 판정 받음)", "| 무리 | n | specific | generic_only | none | U% |", "|---|---|---|---|---|---|"]
    for k, c in s["tally"]["coverage"].items():
        L.append(f"| {k} | {c['n']} | {c['specific']} | {c['generic_only']} | {c['none']} | {c['Upct']} |")
    L += ["", "## 종류별 U", "| 종류 | 일반 n | 일반 U% | 금융 n | 금융 U% |", "|---|---|---|---|---|"]
    for ty, c in t["coverage"]["byType"].items():
        if c["general"]["n"] or c["finance"]["n"]:
            L.append(f"| {ty} | {c['general']['n']} | {c['general']['Upct']} | {c['finance']['n']} | {c['finance']['Upct']} |")
    L += ["", "## 줄별 hit %", "| 줄 | 일반 | 금융 G-* | 금융 F-* |", "|---|---|---|---|"]
    for ln in LINES:
        L.append(f"| {ln} | {t['lines']['general']['pct'][ln]} | {t['lines']['finance_G']['pct'][ln]} | {t['lines']['finance_F']['pct'][ln]} |")
    for name in ("floor", "eye", "detect", "f5"):
        if s.get(name):
            L += ["", f"## {name}", "```", json.dumps(s[name], ensure_ascii=False, indent=1), "```"]
    if s.get("ledger"):
        L += ["", "## 장부", f"합 {s['ledger']['usdTotal']} USD · 경고 {len(s['ledger']['warnings'])}"]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="measure_gfc1")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("pages")
    s.add_argument("--parts", required=True)
    s.add_argument("--book", action="append", required=True, help="<권>=<pagetext.json>")
    s.add_argument("--cap", type=int, default=CAP_PAGES)
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_pages)
    for name, fn in (("tally", cmd_tally), ("eyecheck", cmd_eyecheck)):
        s = sub.add_parser(name)
        s.add_argument("--results", nargs="*", default=[], help="<권>=<result.json>")
        s.add_argument("--types", nargs=2)
        s.add_argument("--books")
        s.add_argument("--out")
        if name == "eyecheck":
            s.add_argument("--seed", type=int, default=0)
            s.add_argument("--labels")
        s.set_defaults(fn=fn)
    s = sub.add_parser("floor")
    s.add_argument("--rounds", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_floor)
    s = sub.add_parser("detectcov")
    s.add_argument("--books", required=True)
    s.add_argument("--f1", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_detectcov)
    s = sub.add_parser("f5")
    s.add_argument("--judge", required=True)
    s.add_argument("--floor")
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_f5)
    s = sub.add_parser("ledger")
    s.add_argument("--calls", nargs="+", required=True)
    s.add_argument("--models", required=True)
    s.add_argument("--out")
    s.set_defaults(fn=cmd_ledger)
    s = sub.add_parser("verdict")
    for k in ("tally", "floor", "eye", "detect", "f5", "ledger"):
        s.add_argument(f"--{k}", required=k == "tally")
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_verdict)
    a = ap.parse_args(argv)
    return int(a.fn(a))


if __name__ == "__main__":
    raise SystemExit(main())
