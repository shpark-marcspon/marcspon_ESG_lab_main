"""`python -m evalrig.translation.measure_mtg1 <명령>` — MTG1 측정(영문번역 블록 태그 방식 T-A + 블록 단위 용어 풀 T-B,
`sid:MARC-TICK-580`, 발주서 `band-handoffs/MTG1-order.md`).

py/ 에서 `uv run --frozen --group scope3colab --group greenwash python -m evalrig.translation.measure_mtg1 <명령>`
(span 실호출만 `--with openai==3.26.0` + 마크스폰 키를 그 명령 환경에만).

1차 호출은 git 밖 드라이버 `out/MTG1/drivers/mtg1_app.py`(esg-app 사본 · esg-app venv)가 한다. 여기는 **순수 셈**(판별 유니온·
tally·쪽 고름·규칙·장부 다시 셈·풀) + span 실호출(semgraph). 사본(`--app`, 기본 `out/MTG1/app-src/<해시12>/apps/api-py`)은
도우미 셋(태그 줄 정규식 글 · `pdf_tags._ratio_ok` · `pdf_tags._numbers`)만 import 한다 — 순수 함수는 그것을 인자로 받는다.
단가 숫자는 코드에 없다(`--models` = 사본 `models.json`). 회사 이름·보고서 글은 코드·summary·reading 에 없다.

명령: pages · freeze · verdict-units --run · a1 --run · tally · quality · pool · span --pool [--dry] [--rep] · floor ·
      eyecheck-sample · ledger · verdict
"""
from __future__ import annotations

import argparse
import ast
import glob
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[4]
if ROOT.name.endswith(".worktrees"):
    ROOT = ROOT.parent
OUT = ROOT / "esg-lab" / "out" / "MTG1"
APP = OUT / "app-src" / "8508814181a0" / "apps" / "api-py"
BOOKS = ("sam", "cos", "kb")

# ---------- 얼린 값(§3-4 · 감독 덧붙임 1·2 · critic B1·B2) ----------
SHORT_MEMBERS = 3
SHORT_CHARS = 60
SENS = ((1, 30), (3, 120))  # 민감도 문턱(규칙 밖 곁 줄): (멤버 ≤, 글자 ≤)
PAGES_TOP = 8
LETTER_MAX = 4
LETTER_RE = re.compile(r"CEO|CHAIRM|인사|메시지|MESSAGE", re.I)
MIN_SHORT_DEN = 100
CF_MAX = 0.05
EYE_N = 20
EYE_BAD_MAX = 10
EYE_MIN = 16
VERIFIER_MIN = 8
VERIFIER_N = 10
VERIFIER_RATIO = 0.8  # J2(감독 f4 11-10, 사후 셈 정의): 검증자 일치는 8/10 = 비율 ≥ 80% 로 읽는다(표본이 10 보다 적을 때)
GAP_MIN = 3
HALF = 0.5
BLEU_SAME = 1.0  # MA2 measure_ma2.BLEU_SAME
VIOL_PP = 0.05
RK_PLUS = 2
USD_X = 1.5
TB_TERMS = 30
TB_FIX = 3
TB_BOOKS = 2
TB_DELTA = 10
SEED = 580
SPAN_MIN_OCC = 3  # labpipe.SPAN_MIN_OCC 와 같음
LEDGER_TOL = 0.0005
VERDICTS = ("ok", "dropped", "shifted", "call_failed")
FAILED_STATUS = ("call_failed", "cutoff", "refused", "not_run")


def sha16(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def squeezed(s: str) -> int:
    return len("".join((s or "").split()))


def is_short(n_members: int, ko: str, members: int = SHORT_MEMBERS, chars: int = SHORT_CHARS) -> bool:
    """짧음 = 단위 멤버 ≤ 3 ∧ 국문(공백 뺀) ≤ 60자."""
    return n_members <= members and squeezed(ko) <= chars


def in_denom(u: Mapping[str, Any]) -> bool:
    """분모 = 표가 아니고 국문이 있는 단위 — **모든 팔에서 같다**(입력엔 남김)."""
    return u["kind"] != "table" and bool(u["has_ko"])


# ---------- 쪽 고름 ----------
def select_pages(units: Sequence[Mapping[str, Any]], toc_items: Iterable[Mapping[str, Any]], text_pages: Iterable[int],
                 top: int = PAGES_TOP) -> dict[str, Any]:
    """① 편지 쪽 = 목차 항목 글이 LETTER_RE 인 항목의 시작 쪽(책당 ≤ 4, 앞 쪽부터) ② 나머지 글자 층 쪽을 짧은 비표
    단위 수 내림차순(같으면 앞 쪽) 위 `top` → 합집합."""
    tp = set(int(p) for p in text_pages)
    letter = sorted({int(it["start_page"]) for it in toc_items
                     if it.get("start_page") and LETTER_RE.search(str(it.get("text") or "")) and int(it["start_page"]) in tp})
    letter = letter[:LETTER_MAX]
    cnt: Counter = Counter()
    for u in units:
        if u["kind"] != "table" and is_short(u["n_members"], u["ko"]):
            cnt[int(u["page"])] += 1
    rest = sorted((p for p in tp if p not in letter), key=lambda p: (-cnt.get(p, 0), p))[:top]
    return {"letter": letter, "top": sorted(rest), "S": sorted(set(letter) | set(rest))}


# ---------- 태그 줄 ----------
def tag_pattern_from_gates(src: str) -> str:
    """1차 `gates.structure_tag_signature` 의 정규식 글(사본 파일 글에서 ast 로 — 얼림 sha 의 대상)."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "structure_tag_signature":
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "findall":
                    arg = call.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        return arg.value
    raise ValueError("structure_tag_signature 정규식을 못 찾음")


def compile_tag_re(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE | re.MULTILINE)


def _label(line: str, tag_re: re.Pattern[str]) -> str | None:
    if not tag_re.match(line):  # 정규식이 ^…$(MULTILINE) 이라 한 줄 전체 일치
        return None
    m =re.search(r"\[([^\]]+)\]", line)
    return re.sub(r"\s+", " ", m.group(1).strip()).upper() if m else None


def segments(text: str, tag_re: re.Pattern[str]) -> tuple[str, list[tuple[str, str]]]:
    """답 글 → (첫 태그 앞 글, [(태그, 몸)]). `PAGE n` 줄은 건너뛴다(쪽 머리)."""
    prefix: list[str] = []
    segs: list[tuple[str, list[str]]] = []
    for line in (text or "").splitlines():
        lab = _label(line, tag_re) if line.strip().startswith("[") else None
        if lab is not None and lab.startswith("PAGE"):
            continue
        if lab is not None:
            segs.append((lab, []))
            continue
        (segs[-1][1] if segs else prefix).append(line)
    return "\n".join(prefix).strip(), [(lab, "\n".join(b).strip()) for lab, b in segs]


# ---------- 판별 유니온 ----------
def verdict_a0(tag: str, answer: str | None, status: str, tag_re: re.Pattern[str]) -> tuple[str, str]:
    """1차 구획 하나의 답 → (판별, 그 단위에 붙은 글). 제 태그 줄만 + 몸 있음 → ok · 태그 없음 → dropped(앞 단위에 붙음) ·
    제 태그 뒤 몸이 공백뿐 → dropped(critic B2) · 남의 태그·여럿·태그 앞 글 → shifted · 실패·잘림·거절 → call_failed."""
    if status in FAILED_STATUS:
        return "call_failed", ""
    prefix, segs = segments(answer or "", tag_re)
    labels = [lab for lab, _ in segs]
    if not labels:
        return "dropped", ""
    if labels == [tag.upper()] and not prefix:
        body = segs[0][1]
        return ("ok", body) if body.strip() else ("dropped", "")
    own = [b for lab, b in segs if lab == tag.upper()]
    return "shifted", (own[0] if own else "")


def a1_fix(tag: str, answer: str, tag_re: re.Pattern[str]) -> str:
    """A1(호출 0): 답에 태그 줄이 하나도 없으면 제 태그 줄을 맨 앞에 붙인다(몸 바이트 그대로)."""
    _, segs = segments(answer or "", tag_re)
    if segs:
        return answer
    return f"[{tag}]\n{answer}"


def verdict_page_tags(expected: Sequence[str], answer: str | None, status: str,
                      tag_re: re.Pattern[str]) -> dict[str, Any]:
    """A2 쪽 하나 → {verdict: {태그: 판별}, en: {태그: 글}, unknown, outside}. 기대 차례 대비 없는 태그 → dropped ·
    겹친 태그 → shifted · 뒤집힘(뒤에 더 앞 번호가 나오는 자리)부터 끝까지 → shifted(pdf_tags 와 같은 엄격) · 몸이
    공백뿐 → dropped(critic B2) · 나머지 ok."""
    exp = [t.upper() for t in expected]
    if status in FAILED_STATUS:
        return {"verdict": {t: "call_failed" for t in exp}, "en": {}, "unknown": 0, "outside": 0}
    prefix, segs = segments(answer or "", tag_re)
    pos = {t: i for i, t in enumerate(exp)}
    known = [(lab, body) for lab, body in segs if lab in pos]
    unknown = sum(1 for lab, _ in segs if lab not in pos)
    cnt = Counter(lab for lab, _ in known)
    idx = [pos[lab] for lab, _ in known]
    start = len(idx)
    for i in range(len(idx)):
        if any(idx[j] < idx[i] for j in range(i + 1, len(idx))):
            start = i
            break
    verdict: dict[str, str] = {}
    en: dict[str, str] = {}
    for i, (lab, body) in enumerate(known):
        if lab in verdict:
            en[lab] = (en[lab] + "\n\n" + body).strip()
            continue
        en[lab] = body
        if cnt[lab] > 1 or i >= start:
            verdict[lab] = "shifted"
        else:
            verdict[lab] = "ok" if body.strip() else "dropped"
    for t in exp:
        verdict.setdefault(t, "dropped")
    return {"verdict": verdict, "en": en, "unknown": unknown, "outside": int(bool(prefix))}


def verdict_items(expected: Sequence[str], items: Sequence[Mapping[str, Any]] | None, status: str) -> dict[str, Any]:
    """B·C 쪽 하나 → id 없음·빈 글 → dropped · 겹친 id → shifted · 모르는 id 는 단위 없음(셈 따로)."""
    exp = list(expected)
    if status in FAILED_STATUS or items is None:
        return {"verdict": {t: "call_failed" for t in exp}, "en": {}, "unknown": 0, "retry": []}
    cnt = Counter(str(it.get("id")) for it in items)
    en: dict[str, str] = {}
    for it in items:
        i = str(it.get("id"))
        if i in exp and i not in en:
            en[i] = str(it.get("translation") or "").strip()
    verdict = {}
    for t in exp:
        if cnt[t] == 0 or not en.get(t, "").strip():
            verdict[t] = "dropped"
        elif cnt[t] > 1:
            verdict[t] = "shifted"
        else:
            verdict[t] = "ok"
    unknown = sum(c for i, c in cnt.items() if i not in exp)
    return {"verdict": verdict, "en": en, "unknown": unknown, "retry": [t for t in exp if verdict[t] == "dropped"]}


def merge_retry(b_items: Sequence[Mapping[str, Any]], c_items: Sequence[Mapping[str, Any]] | None,
                retried: Iterable[str]) -> list[dict[str, Any]]:
    """C = B + 빠진·빈 id 만 다시 받은 답. 다시 받은 id 의 B 쪽 빈 항목은 C 답으로 갈음."""
    rs = set(retried)
    keep = [dict(it) for it in b_items if not (str(it.get("id")) in rs and not str(it.get("translation") or "").strip())]
    return keep + [dict(it) for it in (c_items or []) if str(it.get("id")) in rs]


def complete_verdicts(units: Sequence[Mapping[str, Any]], ver: Mapping[str, str]) -> tuple[dict[str, str], list[str]]:
    """분모 단위인데 판별이 없으면(답 원문 없음 — cap 멈춤·도중 죽음) call_failed 로 채운다(검증 B2). → (판별, not_run 태그)."""
    out = dict(ver)
    not_run = [u["tag"] for u in units if in_denom(u) and u["tag"] not in out]
    for t in not_run:
        out[t] = "call_failed"
    return out, not_run


def run_pages(run: str, book_pages: Mapping[str, Any]) -> list[int]:
    """판이 맡는 쪽(얼림 pages.json): TB = S 밖 쪽 · 편지 쪽 4.1 판 = 편지 쪽 · 그 밖 = S."""
    if run.startswith("TB-"):
        return list(book_pages["rest"])
    if run.startswith(("A0-41", "A1-41")):
        return list(book_pages["letter"])
    return list(book_pages["S"])


def denom_keys(units: Iterable[Mapping[str, Any]], book: str, pages: Iterable[int]) -> set[str]:
    ps = set(int(p) for p in pages)
    return {f"{book}:{u['page']}:{u['tag']}" for u in units if in_denom(u) and int(u["page"]) in ps}


def check_denoms(rows_by_run: Mapping[str, Sequence[Mapping[str, Any]]], expected: set[str]) -> None:
    """모든 팔의 분모 집합이 얼린 쪽에서 낸 집합과 같아야 한다 — 어긋나면 판정 거부(검증 B2)."""
    bad = {}
    for run, rows in rows_by_run.items():
        got = {f"{r['book']}:{r['page']}:{r['tag']}" for r in rows if r["in_denom"]}
        if got != expected:
            bad[run] = {"missing": len(expected - got), "extra": len(got - expected)}
    if bad:
        raise SystemExit(f"분모 집합이 팔마다 다르다 — 판정 거부: {bad}")


def run_usd(by: Mapping[str, Mapping[str, Any]], run: str) -> float:
    """판 하나의 실비 = 장부 묶음 이름이 `<판>-<책>` 인 것만(T-B 의 `TB-…` 는 섞지 않음, 검증 B1)."""
    names = {f"{run}-{b}" for b in BOOKS}
    return sum(float(d["usd"]) for k, d in by.items() if k.split("|")[0] in names)


# ---------- tally ----------
def tally(rows: Sequence[Mapping[str, Any]], cut_pages: Iterable[tuple[str, int]] = ()) -> dict[str, Any]:
    """rows = [{book, page, tag, short, verdict, ratio_flag?, num_mismatch?, dup_en?}](분모 단위만) → 짧음·긺·전체 셈과
    D = (dropped + shifted)/(분모 − call_failed), 쪽 일치율(쪽의 모든 단위 ok), 곁: 잘린 쪽(어느 팔이든) 단위 빼고 D."""
    cut = {(b, int(p)) for b, p in cut_pages}

    def part(rs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        c = Counter(r["verdict"] for r in rs)
        den = len(rs) - c["call_failed"]
        bad = c["dropped"] + c["shifted"]
        return {"n": len(rs), "ok": c["ok"], "dropped": c["dropped"], "shifted": c["shifted"], "call_failed": c["call_failed"],
                "ratio_flag": sum(1 for r in rs if r["verdict"] == "ok" and r.get("ratio_flag")),
                "num_mismatch": sum(1 for r in rs if r["verdict"] == "ok" and r.get("num_mismatch")),
                "dup_en": sum(1 for r in rs if r.get("dup_en")),
                "bad": bad, "D": (bad / den) if den else None}

    pages: dict[tuple[str, int], list[str]] = {}
    for r in rows:
        pages.setdefault((r["book"], int(r["page"])), []).append(r["verdict"])
    full = [k for k, vs in pages.items() if "call_failed" not in vs]
    match = sum(1 for k in full if all(v == "ok" for v in pages[k]))
    keep = [r for r in rows if (r["book"], int(r["page"])) not in cut]
    return {"short": part([r for r in rows if r["short"]]), "long": part([r for r in rows if not r["short"]]), "all": part(rows),
            "pages": len(pages), "pages_full": len(full), "page_match": (match / len(full)) if full else None,
            "excl_cut": {"pages_cut": len(cut), "short": part([r for r in keep if r["short"]])}}


def bad_set(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    return {f"{r['book']}:{r['page']}:{r['tag']}" for r in rows if r["short"] and r["verdict"] in ("dropped", "shifted")}


# ---------- 판정 T-A ----------
def verdict_ta(x: Mapping[str, Any]) -> dict[str, Any]:
    """§3-4 T-A — 위에서부터 첫 갈래. x 칸: short_den{A0,A2,B}(call_failed 뺀 짧은 분모) · cf_rate{A0,A2,B} ·
    eye{A0,A2,B}(일치 수/20) · verifier(일치 수/10 | None) · bad{A0,A2,A2r2,B,Br2} · bleu{A0,A2,B} · viol{A2,B} ·
    rk{A2,B} · usd{A2,B}."""
    bad = x["bad"]
    F = max(abs(bad["A2"] - bad["A2r2"]), abs(bad["B"] - bad["Br2"]))
    gap = max(GAP_MIN, 2 * F)
    why: list[str] = []
    if min(x["short_den"].values()) < MIN_SHORT_DEN:
        why.append("짧은 분모 < 100")
    if any(v > CF_MAX for v in x["cf_rate"].values()):
        why.append("call_failed > 5%")
    if any(v < EYE_MIN for v in x["eye"].values()):
        why.append("눈검사 일치 < 16/20")
    if x.get("verifier") is not None and x["verifier"] < VERIFIER_MIN:
        why.append("검증자 일치 < 8/10")
    base = {"F": F, "gap": gap, "verifier_pending": x.get("verifier") is None}
    if why:
        return {**base, "answer": "못 가름", "branch": 1, "why": why}
    if bad["A2"] <= gap:
        return {**base, "answer": "아니오 — 글 태그도 지킴", "branch": 2}
    a = (bad["A2"] - bad["B"] >= gap) and (bad["B"] <= HALF * bad["A2"])
    if not a:
        return {**base, "answer": "아니오 — 구조화도 못 줄임", "branch": 3}
    if bad["B"] > bad["A0"] + max(1, F):
        return {**base, "answer": "아니오 — 1차 구획 부름보다 못 지킴", "branch": 4}
    bl = x["bleu"]
    c = (bl["B"] >= bl["A2"] - BLEU_SAME and bl["B"] >= bl["A0"] - BLEU_SAME
         and x["viol"]["B"] <= x["viol"]["A2"] + VIOL_PP and x["rk"]["B"] <= x["rk"]["A2"] + RK_PLUS)
    d = x["usd"]["B"] <= USD_X * x["usd"]["A2"]
    if not (c and d):
        return {**base, "answer": "아니오 — 품질·비용 해침", "branch": 5, "c": c, "d": d}
    return {**base, "answer": "예", "branch": 6}


def verifier_ok(agree: int, n: int) -> bool:
    """J2: 일치 비율 ≥ 80%(얼린 글 「< 8/10」의 비율 읽기 — 표본이 모집단 전부라 10 보다 적을 때)."""
    return n > 0 and agree / n >= VERIFIER_RATIO


def verdict_tb(terms: Mapping[str, int], fix: Mapping[str, int], verifier: tuple[int, int] | None) -> dict[str, Any]:
    """§3-4 T-B: 분모 섬 = 문서 전체 단위 풀 용어 ≥ 30 인 책 ≥ 2 · 잡음 = S4 눈검사 fix ≥ 3 인 책 ≥ 2.
    verifier = (일치, 건수). 판정은 비율(J2), `literal` 은 글자대로(일치 < 8 이면 못 가름) 읽은 답을 나란히."""
    island = sum(1 for v in terms.values() if v >= TB_TERMS) >= TB_BOOKS
    noisy = sum(1 for v in fix.values() if v >= TB_FIX) >= TB_BOOKS
    base = {"island": island, "noisy": noisy, "verifier_pending": verifier is None}
    answer = "예" if noisy else "아니오 — 드묾"
    if not island:
        return {**base, "answer": "못 가름", "literal": "못 가름"}
    if verifier is None:
        return {**base, "answer": answer, "literal": answer}
    agree, n = verifier
    literal = "못 가름" if agree < VERIFIER_MIN else answer
    return {**base, "answer": answer if verifier_ok(agree, n) else "못 가름", "literal": literal, "verifier": [agree, n]}


def denominator_side(unit_terms: Mapping[str, int], page_terms: Mapping[str, int]) -> str:
    """곁: 같은 범위 단위 − 쪽 용어 Δ ≥ 10 이 책 둘 이상이면 블록 단위가 분모를 세움."""
    n = sum(1 for b in unit_terms if unit_terms[b] - page_terms.get(b, 0) >= TB_DELTA)
    return "블록 단위가 분모를 세움" if n >= TB_BOOKS else "범위가 세움"


# ---------- 눈검사 표본 ----------
def eyecheck_sample(rows: Sequence[Mapping[str, Any]], seed: int = SEED, n: int = EYE_N,
                    bad_max: int = EYE_BAD_MAX) -> list[dict[str, Any]]:
    """짧은 분모 단위에서 코드 dropped|shifted|ratio_flag ≤ bad_max + 나머지 ok 로 n 건(씨앗 고정)."""
    rng = random.Random(seed)
    short = sorted((r for r in rows if r["short"] and r["verdict"] != "call_failed"),
                   key=lambda r: (r["book"], int(r["page"]), r["tag"]))
    bad = [r for r in short if r["verdict"] in ("dropped", "shifted") or r.get("ratio_flag")]
    ok = [r for r in short if r not in bad]
    pick_bad = rng.sample(bad, min(bad_max, len(bad)))
    pick_ok = rng.sample(ok, min(n - len(pick_bad), len(ok)))
    out = pick_bad + pick_ok
    rng.shuffle(out)
    return [dict(r) for r in out]


def eye_agree(items: Sequence[Mapping[str, Any]]) -> int:
    """코드 ok(ratio_flag 아님) ↔ 라벨 own 이 일치한 수."""
    return sum(1 for it in items if (it["code"] == "ok") == (it["label"] == "own"))


# ---------- 장부 ----------
def recompute(row: Mapping[str, Any], prices: Mapping[str, Mapping[str, float]]) -> float | None:
    """★9 두 꼴: core(`cacheReadTokens` 칸 있음 — inputTokens = 캐시 안 탄 몫, 추론은 outputTokens 안) ·
    semgraph(`cachedTokens` — inputTokens 가 캐시 몫을 품음)."""
    p = prices.get(str(row.get("apiModelId") or row.get("model")))
    if p is None:
        return None
    out = int(row.get("outputTokens") or 0)
    if "cacheReadTokens" in row:
        return (int(row.get("inputTokens") or 0) * p["input"] + int(row.get("cacheReadTokens") or 0) * p["cacheRead"]
                + int(row.get("cacheWriteTokens") or 0) * p["cacheWrite"] + out * p["output"]) / 1e6
    cached = int(row.get("cachedTokens") or 0)
    return ((int(row.get("inputTokens") or 0) - cached) * p["input"] + cached * p["cacheRead"] + out * p["output"]) / 1e6


def ledger(groups: Mapping[str, Sequence[Mapping[str, Any]]], prices: Mapping[str, Mapping[str, float]]) -> dict[str, Any]:
    """groups = {잴 것: 장부 줄} → 잴 것·모델별 호출·토큰 네 칸·USD·다시 셈, 꼴별 합, 경고(|다시 셈 − costUsd| > 0.0005)."""
    by: dict[str, dict[str, Any]] = {}
    form: Counter = Counter()
    warns: list[dict[str, Any]] = []
    for name, rows in sorted(groups.items()):
        for r in rows:
            if r.get("dry_run"):
                continue
            model = str(r.get("apiModelId") or r.get("model"))
            d = by.setdefault(f"{name}|{model}", {"calls": 0, "ok": 0, "in": 0, "cache_read": 0, "cache_write": 0, "out": 0,
                                                  "thinking": 0, "usd": 0.0, "recomputed": 0.0})
            d["calls"] += 1
            d["ok"] += int(r.get("resultKind", "success") == "success" and r.get("status", "completed") != "incomplete")
            core = "cacheReadTokens" in r
            d["in"] += int(r.get("inputTokens") or 0)
            d["cache_read"] += int((r.get("cacheReadTokens") if core else r.get("cachedTokens")) or 0)
            d["cache_write"] += int(r.get("cacheWriteTokens") or 0)
            d["out"] += int(r.get("outputTokens") or 0)
            d["thinking"] += int(r.get("thinkingTokens") or 0)
            usd = float(r.get("costUsd") or 0.0)
            d["usd"] += usd
            form["core" if core else "semgraph"] += usd
            rc = recompute(r, prices)
            if rc is not None:
                d["recomputed"] += rc
                if abs(rc - usd) > LEDGER_TOL:
                    warns.append({"group": name, "model": model, "costUsd": usd, "recomputed": round(rc, 6)})
    return {"by": dict(sorted(by.items())), "form": dict(form), "total": sum(form.values()), "warnings": warns}


def prices_from(models_json: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    return {k: v["pricing"] for k, v in models_json["models"].items() if "pricing" in v}


# ---------- 풀 · span ----------
def unit_pairs(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """단위 짝 {id: <쪽>:<태그>, page, ko, en} — dropped·call_failed 는 뺀다."""
    return [{"id": f"{r['page']}:{r['tag']}", "page": int(r["page"]), "ko": r["ko"], "en": r.get("en") or ""}
            for r in sorted(rows, key=lambda r: (int(r["page"]), r.get("order", 0)))
            if r["verdict"] not in ("dropped", "call_failed")]


def page_pairs(units: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """쪽 짝 — 같은 단위를 쪽마다 이음(국문은 빈 줄, 영문은 공백 하나)."""
    by: dict[int, list[Mapping[str, Any]]] = {}
    for u in units:
        by.setdefault(int(u["page"]), []).append(u)
    return [{"id": f"page:{p}", "page": p, "ko": "\n\n".join(u["ko"] for u in us),
             "en": " ".join(u["en"] for u in us if u["en"])} for p, us in sorted(by.items())]


def window(pairs: Sequence[Mapping[str, Any]], lo: int, hi: int) -> list[dict[str, Any]]:
    return [dict(p) for p in pairs if lo <= int(p["page"]) <= hi]


def span_key(pair: Mapping[str, Any], term: str) -> str:
    """span 재사용 열쇠 = (짝 id · 짝 글 sha · 용어)."""
    return f"{pair['id']}|{sha16(pair['ko'] + chr(10) + pair['en'])}|{term}"


def split_cached(batch: Mapping[str, Any], cache: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """묶음 → (부를 몫(캐시에 없는 (짝, 용어)만) | None, 캐시 답)."""
    todo, hit = [], []
    for p in batch["pairs"]:
        miss = []
        for t in p["terms"]:
            k = span_key(p, t)
            if k in cache:
                hit.append(dict(cache[k], pair=p["id"], term=t))
            else:
                miss.append(t)
        if miss:
            todo.append({**p, "terms": miss})
    return ({"pairs": todo} if todo else None), hit


def remember(batch: Mapping[str, Any], answer: Sequence[Mapping[str, Any]], cache: dict[str, Any]) -> None:
    pairs = {p["id"]: p for p in batch["pairs"]}
    for a in answer:
        p = pairs.get(a.get("pair"))
        if p is not None and a.get("term") in p["terms"]:
            cache[span_key(p, a["term"])] = {k: a.get(k) for k in ("span", "kind")}


# ---------- 얼림 글 ----------
def rules_text(pages_sha: str, tag_sha: str, tag_pattern: str) -> str:
    return f"""# MTG1 rules.frozen (첫 실호출 전 — 실호출 뒤 바꾸지 않음)

- 짧음 = 단위 멤버 ≤ {SHORT_MEMBERS} ∧ 국문(공백 뺀) ≤ {SHORT_CHARS}자. 민감도(규칙 밖 곁 줄): {SENS}.
- 분모 = 표가 아니고 국문(한글) 있는 단위, **모든 팔에서 같다**. 국문 없는 단위·표 단위는 입력엔 남기고 분모 밖.
- 쪽 고름: 편지 쪽 = 목차 항목 글 /{LETTER_RE.pattern}/i 의 시작 쪽(책당 ≤ {LETTER_MAX}) ∪ 나머지 글자 층 쪽의 짧은 비표 단위 수
  내림차순(같으면 앞 쪽) 위 {PAGES_TOP}. pages.json sha16 `{pages_sha}`.
- 태그 줄 = 1차 `gates.structure_tag_signature` 정규식(사본 글, sha16 `{tag_sha}`): `{tag_pattern}` (IGNORECASE·MULTILINE).
  `PAGE n` 줄은 쪽 머리로 건너뜀.
- 판별 유니온(단위·팔마다): ok · dropped · shifted · call_failed(실패·잘림 CutOff·거절·스키마 못 읽음 — 분모 밖, 팔별 수) ·
  곁 ratio_flag(ok 인데 사본 `pdf_tags._ratio_ok` 거짓).
  - A0(1차 구획 하나 = section@2 하나, Translator, 출력 상한 1차 그대로 4096): 제 태그 줄만 + 몸 → ok · 태그 없음 → dropped ·
    제 태그 뒤 몸 공백뿐 → dropped(critic B2) · 남의 태그·여럿·태그 앞 글 → shifted · Translator `warnings` 의
    llm_truncated·llm_refused → call_failed(critic B1).
  - A1(호출 0): A0 답에 태그 줄이 없으면 제 태그 줄을 맨 앞에 붙인 뒤 A0 와 같은 셈.
  - A2(쪽 하나 = section@2 하나, core CallRequest max_output_tokens=8192): 없는 태그 → dropped · 겹친 태그 → shifted ·
    뒤집힘(뒤에 더 앞 번호가 나오는 첫 자리)부터 끝까지 → shifted · 몸 공백뿐 → dropped(critic B2) · 모르는 태그·태그 앞 글은 셈 따로.
  - B(쪽 하나 = table-batch@2 + strict 스키마 {{items:[{{id, translation}}]}}, 8192): id 없음·빈 글 → dropped · 겹친 id → shifted ·
    모르는 id 셈 따로. C = B + dropped id 만 그 id 로 한 번 더(같은 꼴).
  - 한 팔에서라도 잘린(CutOff) 쪽은 모든 팔에서 그 쪽 단위를 따로 세어 곁 줄(excl_cut).
  - 답 원문이 없는 분모 단위(cap 멈춤·도중 죽음)는 call_failed(not_run)로 센다. 분모 집합은 얼린 쪽에서 내고, 모든 팔의
    분모 집합이 그것과 같지 않으면 tally·판정을 거부한다(검증 B2).
- D = (dropped + shifted)/(분모 − call_failed), 짧음·긺·전체. 쪽 일치율 = call_failed 없는 쪽 중 모든 단위 ok 비율.
- 곁(호출 0, critic R3·R4): ok 단위의 국문·영문 숫자 어긋남 수(사본 `pdf_tags._numbers`) · 같은 쪽에서 영문이 똑같은 단위 수 ·
  F 곁에 r1·r2 의 걸린 짧은 단위 집합 대칭차.
- 지시(팔 사이 같음): system = `texts.page_system(company_style(파트, 이름, 기본 지칭, IS, None), STYLE_A, IS)` · 용어집 =
  `build_relevant_glossary(runner_made 용어집, 그 쪽 1차 태그 글 전체)` · 파트 = 편지 쪽 ceo_message, 그 밖 general.

## §3-4 T-A (luna 판정, mini 곁 줄)
bad(판) = 짧은 단위 dropped + shifted. F = max(|bad(A2 r1) − bad(A2 r2)|, |bad(B r1) − bad(B r2)|). 위에서부터 첫 갈래:
1. 못 가름: 짧은 분모 < {MIN_SHORT_DEN} · 견주는 판(A0·A2·B r1) 중 call_failed > {CF_MAX:.0%} · 눈검사 일치 < {EYE_MIN}/{EYE_N} 인 판 ·
   검증자 일치 < {VERIFIER_MIN}/{VERIFIER_N}.
2. 아니오 — 글 태그도 지킴: bad(A2) ≤ max({GAP_MIN}, 2F).
3. 아니오 — 구조화도 못 줄임: ¬(a), (a) = bad(A2) − bad(B) ≥ max({GAP_MIN}, 2F) ∧ bad(B) ≤ {HALF}·bad(A2).
4. 아니오 — 1차 구획 부름보다 못 지킴: bad(B) > bad(A0) + max(1, F).
5. 아니오 — 품질·비용 해침: ¬(c)∨¬(d). (c) BLEU 쪽(B) ≥ BLEU(A2) − {BLEU_SAME} ∧ ≥ BLEU(A0) − {BLEU_SAME} · 위반 엄격 rate(B) ≤ rate(A2) +
   {VIOL_PP * 100:.0f}%p · 남은 한국어 단위(B) ≤ A2 + {RK_PLUS}. (d) USD(B) ≤ {USD_X} × USD(A2).
6. 예.
받침(규칙 밖): C 가 B 의 dropped 를 얼마나 줄이나 · A1 이 A0 의 dropped 를 0 으로 · place missing+unplaced 차.

## §3-4 T-B
분모 섬 = 문서 전체 단위 풀 용어 ≥ {TB_TERMS} 이 책 셋 중 {TB_BOOKS} 이상 · 잡음 = 그 풀 S4 눈검사 fix ≥ {TB_FIX} 이 {TB_BOOKS} 책 이상.
못 가름 = ¬분모 섬 ∨ 검증자 일치 < {VERIFIER_MIN}/{VERIFIER_N} · 예 = 분모 섬 ∧ 잡음 · 아니오 — 드묾 = 분모 섬 ∧ ¬잡음.
곁: 같은 범위 단위 − 쪽 용어 Δ ≥ {TB_DELTA} 이 책 {TB_BOOKS} 이상이면 「블록 단위가 분모를 세움」, 아니면 「범위가 세움」. S1 바닥보다 작은 차는
「차이 없음」. 풀 = `trlab.span_incons.pool_from(짝, 그 책 용어집, {SPAN_MIN_OCC})`(단위 짝 = B luna r1 의 dropped·call_failed 뺀 단위).
span 재사용 열쇠 = (짝 id · 짝 글 sha · 용어).

## 눈검사
- T-A: 판 셋(A0·A2·B luna r1)마다 짧은 단위에서 씨앗 {SEED} 로 {EYE_N}건(코드 dropped|shifted|ratio_flag ≤ {EYE_BAD_MAX} + 나머지 ok).
  팔 이름을 가리고 단위 국문 + 그 팔이 그 단위에 붙인 영문만: 「이 영문이 이 국문을, 그것만 옮겼나」 own · mixed · partial · none.
  일치 = 코드 ok(ratio_flag 아님) ↔ own. 러너 라벨(정답 아님).
- T-B: 문서 전체 단위 풀의 S4 걸림 전부(책마다 ≤ 20, 넘으면 씨앗 {SEED} 표본) — fix · fine · noise.

## 장부·멈춤(감독 f4 11-10, 검증 B1·J1)
- T-B 전권 번역은 판 `TB-luna-r1`·장부 `calls/TB-luna-r1-<책>.jsonl` 로 T-A 와 따로. S 쪽은 T-A 의 B luna r1 을 다시 쓰고
  (비용은 T-A 에만, 두 번 세지 않음) T-A 의 (d) 비용 문 USD(판) = 장부 묶음 `<판>-<책>` 만.
- S4 sol 표 값 0.15 → 0.24(감독 결정, 첫 dry 판은 dry.json 에 그대로), 문 = 0.24 × 1.5 = 0.36. span(S1·S2) 뒤 실제 judge 글로
  다시 잰 어림(`span --judge-dry`)이 0.36 을 넘으면 judge 를 부르지 않고 멈추고 묻는다(다시 재서 통과시키지 않음).
- 얼림 판: 첫 판 sha12 `a002f99a8c9f`(C1 검증 전) → 이 판.

## 실측으로 발주서를 갈음(감독 f4 11-10)
- ★5 국문자 40,571 은 공백 포함 셈(공백 뺀 31,584) · 짧음 ∧ 국문 277(sam 118 · cos 107 · kb 52) · T-B 남은 쪽 324.
"""


# ---------- 파일 몫(CLI) ----------
def _load(p: Path) -> Any:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _dump(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def file_sha16(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:16]


class App:
    """사본 도우미 셋 — 사본에서 import 됨을 단언."""

    def __init__(self, app: Path):
        import sys
        self.root = Path(app).resolve()
        sys.path.insert(0, str(self.root))
        import esgapi
        assert Path(esgapi.__file__).resolve().is_relative_to(self.root), f"사본에서 import 되지 않음: {esgapi.__file__}"
        from esgapi.translation.tools import pdf_tags
        assert Path(pdf_tags.__file__).resolve().is_relative_to(self.root)
        self.pdf_tags = pdf_tags
        gates = self.root / "esgapi/translation/engines/colab_158/gates.py"
        self.tag_pattern = tag_pattern_from_gates(gates.read_text(encoding="utf-8"))
        self.tag_re = compile_tag_re(self.tag_pattern)

    def ratio_ok(self, unit_map: Mapping[str, Any], body: str) -> bool:
        return bool(self.pdf_tags._ratio_ok(unit_map, body))

    def numbers(self, s: str) -> list[str]:
        return list(self.pdf_tags._numbers(s))


def units_doc(book: str) -> dict[str, Any]:
    return _load(OUT / "units" / f"{book}.json")


def cmd_pages(a: argparse.Namespace) -> None:
    out: dict[str, Any] = {"rule": "letter ∪ top8(short desc, page asc)", "books": {}}
    for b in BOOKS:
        ud = units_doc(b)
        toc = _load(ROOT / "prework" / "gold-trx1" / "toc" / f"{b}-ko.json")["items"]
        tp = [p["page"] for p in ud["pages"] if p["textLayer"] == "present"]
        sel = select_pages(ud["units"], toc, tp)
        S = set(sel["S"])
        ko_pages = {u["page"] for u in ud["units"] if in_denom(u)}
        out["books"][b] = {**sel, "rest": sorted(ko_pages - S)}
    _dump(OUT / "pages.json", out)
    print(json.dumps({b: v["S"] for b, v in out["books"].items()}), "sha16", file_sha16(OUT / "pages.json"))


def cmd_freeze(a: argparse.Namespace) -> None:
    app = App(a.app)
    p = OUT / "rules.frozen.md"
    if p.exists() and not a.force:
        raise SystemExit("rules.frozen.md 가 이미 있다 — 덮어쓰지 않는다")
    p.write_text(rules_text(file_sha16(OUT / "pages.json"), sha16(app.tag_pattern), app.tag_pattern), encoding="utf-8")
    print("rules sha12", hashlib.sha256(p.read_bytes()).hexdigest()[:12])


def _arm(run: str) -> str:
    return run.split("-")[0]


def _raws(run: str) -> dict[str, dict[str, Any]]:
    d = OUT / "raw" / run
    return {p.stem: _load(p) for p in sorted(d.glob("*.json"))} if d.exists() else {}


def unit_rows(run: str, app: App) -> dict[str, Any]:
    """판 하나 → 단위 행(분모 단위 + 국문 없는 단위는 in_denom False) · 쪽 글(place 용) · C 용 다시 부를 id · 잘린 쪽."""
    arm = _arm(run)
    src = run.replace("A1-", "A0-", 1) if arm == "A1" else run
    raws = _raws(src)
    if arm == "C":
        braws = _raws(run.replace("C-", "B-", 1))
    pj = _load(OUT / "pages.json")
    rows: list[dict[str, Any]] = []
    page_texts: dict[str, dict[str, str]] = {}
    retry: dict[str, dict[str, list[str]]] = {}
    cut: list[tuple[str, int]] = []
    other = {"unknown": 0, "outside": 0}
    for b in BOOKS:
        ud = units_doc(b)
        maps = {(int(m["page"]), m["tag"]): m for m in ud["map"] if m["tag"] is not None}
        pt: dict[str, str] = {}
        for pg in run_pages(run, pj["books"][b]):
            us = [u for u in ud["units"] if u["page"] == pg and u["kind"] != "table"]
            exp = [u["tag"] for u in us]
            ver: dict[str, str] = {}
            en: dict[str, str] = {}
            if arm in ("A0", "A1"):
                parts = [f"[PAGE {pg}]"]
                for u in us:
                    r = raws.get(f"{b}-p{pg}-{u['tag'].replace(' ', '')}")
                    if r is None:  # 국문 없는 구획 — 1차는 원문 그대로 둔다(분모 단위면 아래에서 not_run)
                        parts.append(f"[{u['tag']}]\n{u['body']}".strip())
                        continue
                    if r["status"] == "cutoff":
                        cut.append((b, pg))
                    text = r.get("text") or ""
                    if arm == "A1" and r["status"] == "ok":
                        text = a1_fix(u["tag"], text, app.tag_re)
                    v, body = verdict_a0(u["tag"], text, r["status"], app.tag_re)
                    ver[u["tag"]], en[u["tag"]] = v, body
                    if r["status"] not in FAILED_STATUS:
                        parts.append(text)
                pt[str(pg)] = "\n\n".join(parts)
            elif arm == "A2":
                r = raws.get(f"{b}-p{pg}")
                if r is None:
                    r = {"status": "not_run"}
                if r["status"] == "cutoff":
                    cut.append((b, pg))
                res = verdict_page_tags(exp, r.get("text"), r["status"], app.tag_re)
                ver, en = res["verdict"], res["en"]
                other["unknown"] += res["unknown"]
                other["outside"] += res["outside"]
                if r["status"] not in FAILED_STATUS:
                    t = r.get("text") or ""
                    pt[str(pg)] = t if re.search(rf"^\s*\[PAGE\s+{pg}\]", t, re.M | re.I) else f"[PAGE {pg}]\n\n{t}"
            else:  # B · C · TB
                rb = (braws if arm == "C" else raws).get(f"{b}-p{pg}")
                if rb is None:
                    rb = {"status": "not_run"}
                if rb["status"] == "cutoff":
                    cut.append((b, pg))
                items = rb.get("items") if rb["status"] == "ok" else None
                status = rb["status"]
                if arm == "C" and items is not None:
                    rc = raws.get(f"{b}-p{pg}")
                    if rc is not None:
                        retried = rc.get("ids") or []
                        items = merge_retry(items, rc.get("items") if rc["status"] == "ok" else None, retried)
                res = verdict_items(exp, items, status)
                ver, en = res["verdict"], res["en"]
                other["unknown"] += res["unknown"]
                if res["retry"]:
                    retry.setdefault(b, {})[str(pg)] = res["retry"]
                if items is not None:
                    pt[str(pg)] = f"[PAGE {pg}]\n\n" + "\n\n".join(f"[{it.get('id')}]\n{it.get('translation') or ''}"
                                                                   for it in items)
            ver, not_run = complete_verdicts(us, ver)
            ens = Counter((en.get(t) or "").strip() for t in exp if (en.get(t) or "").strip())
            for order, u in enumerate(us):
                if u["tag"] not in ver:
                    continue
                v = ver[u["tag"]]
                e = en.get(u["tag"]) or ""
                m = maps[(pg, u["tag"])]
                rows.append({"book": b, "page": pg, "tag": u["tag"], "order": order, "short": is_short(u["n_members"], u["ko"]),
                             "in_denom": in_denom(u), "verdict": v, "ko": u["ko"], "en": e, "n_members": u["n_members"],
                             "ratio_flag": v == "ok" and not app.ratio_ok(m, e),
                             "num_mismatch": v == "ok" and app.numbers(u["ko"]) != app.numbers(e),
                             "dup_en": bool(e.strip()) and ens[e.strip()] > 1, "not_run": u["tag"] in not_run})
        page_texts[b] = pt
    return {"run": run, "rows": rows, "page_texts": page_texts, "retry_ids": retry, "cut_pages": sorted(set(cut)), "other": other}


def cmd_verdict_units(a: argparse.Namespace) -> None:
    app = App(a.app)
    v = unit_rows(a.run, app)
    _dump(OUT / "runs" / f"{a.run}.units.json", v)
    den = [r for r in v["rows"] if r["in_denom"]]
    t = tally(den, v["cut_pages"])
    print(a.run, json.dumps({"short": t["short"], "long": t["long"], "page_match": t["page_match"], "other": v["other"],
                             "cut": len(v["cut_pages"])}, ensure_ascii=False))


def _ledger_groups() -> dict[str, list[dict[str, Any]]]:
    g: dict[str, list[dict[str, Any]]] = {}
    for p in sorted(glob.glob(str(OUT / "calls" / "*.jsonl"))) + sorted(glob.glob(str(OUT / "span" / "calls*.jsonl"))):
        name = Path(p).stem
        g[name] = [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines() if x.strip()]
    return g


def cmd_ledger(a: argparse.Namespace) -> None:
    prices = prices_from(_load(Path(a.models)))
    if a.calls:
        groups = {Path(p).stem: [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines() if x.strip()]
                  for p in sorted(glob.glob(a.calls))}
    else:
        groups = _ledger_groups()
    led = ledger(groups, prices)
    _dump(OUT / "ledger.json", led)
    print(json.dumps({"total": round(led["total"], 6), "form": led["form"], "warnings": len(led["warnings"])}, ensure_ascii=False))


def kopool(a: argparse.Namespace) -> dict[str, Any]:
    """호출 0 몫: 국문만의 풀 셈(단위·쪽, 문서 전체·20쪽 창) — 풀은 KO 만 본다."""
    from trlab import span_incons as SI
    from trlab.glossary_check import load_gloss
    win = {"sam": (35, 54), "cos": (20, 39)}
    out: dict[str, Any] = {}
    for b in BOOKS:
        ud = units_doc(b)
        us = [{"page": u["page"], "tag": u["tag"], "ko": u["ko"], "en": "-", "verdict": "ok"} for u in ud["units"] if in_denom(u)]
        up = unit_pairs(us)
        pp = page_pairs([{"page": p["page"], "ko": p["ko"], "en": p["en"]} for p in up])
        gl = load_gloss(b)["items"]
        row = {"unit": len(SI.pool_from(up, gl, SPAN_MIN_OCC)["terms"]), "page": len(SI.pool_from(pp, gl, SPAN_MIN_OCC)["terms"])}
        if b in win:
            lo, hi = win[b]
            row["win_unit"] = len(SI.pool_from(window(up, lo, hi), gl, SPAN_MIN_OCC)["terms"])
            row["win_page"] = len(SI.pool_from(window(pp, lo, hi), gl, SPAN_MIN_OCC)["terms"])
        out[b] = row
    return out


def cmd_kopool(a: argparse.Namespace) -> None:
    out = kopool(a)
    _dump(OUT / "kopool.json", out)
    print(json.dumps(out))


# ---------- 판 모음 · 품질 ----------
TA_RUNS = ("A0-luna-r1", "A1-luna-r1", "A2-luna-r1", "A2-luna-r2", "B-luna-r1", "B-luna-r2", "C-luna-r1",
           "A0-mini-r1", "A1-mini-r1", "A2-mini-r1", "B-mini-r1", "A0-41-r1", "A1-41-r1")


def load_units(run: str) -> dict[str, Any] | None:
    p = OUT / "runs" / f"{run}.units.json"
    return _load(p) if p.exists() else None


def s_rows(v: Mapping[str, Any], pj: Mapping[str, Any], letter_only: bool = False) -> list[dict[str, Any]]:
    """분모 단위 중 S 쪽(또는 편지 쪽)만 — T-B 로 넓힌 B luna r1 도 T-A 셈은 S 만."""
    keep = {(b, p) for b, d in pj["books"].items() for p in (d["letter"] if letter_only else d["S"])}
    return [r for r in v["rows"] if r["in_denom"] and (r["book"], int(r["page"])) in keep]


def all_cut(pj: Mapping[str, Any]) -> list[tuple[str, int]]:
    cut: set[tuple[str, int]] = set()
    for run in TA_RUNS:
        v = load_units(run)
        if v:
            cut |= {(b, int(p)) for b, p in v["cut_pages"]}
    return sorted(cut)


def expected_denoms(pj: Mapping[str, Any], which: str) -> set[str]:
    out: set[str] = set()
    for b in BOOKS:
        out |= denom_keys(units_doc(b)["units"], b, pj["books"][b][which])
    return out


def cmd_tally(a: argparse.Namespace) -> None:
    pj = _load(OUT / "pages.json")
    cut = all_cut(pj)
    out: dict[str, Any] = {"cut_pages_any_arm": cut, "runs": {}}
    got = {r: load_units(r) for r in TA_RUNS}
    got = {r: v for r, v in got.items() if v}
    exp_s = expected_denoms(pj, "S")
    exp_l = expected_denoms(pj, "letter")
    check_denoms({r: s_rows(v, pj) for r, v in got.items() if not r.startswith(("A0-41", "A1-41"))}, exp_s)
    check_denoms({r: s_rows(v, pj, letter_only=True) for r, v in got.items() if r.startswith(("A0-41", "A1-41"))}, exp_l)
    for run, v in got.items():
        rows = s_rows(v, pj, letter_only=run.startswith(("A0-41", "A1-41")))
        t = tally(rows, cut)
        t["by_book"] = {b: tally([r for r in rows if r["book"] == b])["short"] for b in BOOKS}
        t["sens"] = {f"m{m}c{c}": tally([dict(r, short=is_short(r["n_members"], r["ko"], m, c)) for r in rows])["short"]
                     for m, c in SENS}
        t["bad_set"] = sorted(bad_set(rows))
        t["other"] = v["other"]
        out["runs"][run] = t
    for arm in ("A2", "B"):
        r1, r2 = out["runs"].get(f"{arm}-luna-r1"), out["runs"].get(f"{arm}-luna-r2")
        if r1 and r2:
            out[f"symdiff_{arm}"] = len(set(r1["bad_set"]) ^ set(r2["bad_set"]))
    _dump(OUT / "tally.json", out)
    for run, t in out["runs"].items():
        print(run, "short", {k: t["short"][k] for k in ("n", "dropped", "shifted", "call_failed", "D")},
              "long", {k: t["long"][k] for k in ("n", "dropped", "shifted", "D")}, "match", t["page_match"])


def _unit_objs(rows: Sequence[Mapping[str, Any]], run: str) -> list[Any]:
    from ..common.units import Origin, Unit
    from . import MODULE, UNIT
    o = Origin("v2", "mtg1.unit@1", run, "mtg1.units@1")
    return [Unit(module=MODULE, unit=UNIT, key=f"{r['book']}:{r['page']}:{r['tag']}", group=str(r["page"]), origin=o,
                 payload={"ko": r["ko"], "en": r["en"], "granularity": "block", "variant": run, "remainingKorean": None},
                 status="ok" if r["verdict"] == "ok" else "missing") for r in rows]


def cmd_quality(a: argparse.Namespace) -> None:
    """판마다 같은 쪽: S 쪽 중 공식 영문 덮음 ≥ 0.5 이고 견주는 판 모두 call_failed 없는 쪽. 쪽 영문 = 그 쪽 단위 영문을
    차례대로 공백 하나로(표 빼고 상대 비교만)."""
    from trlab.common import load as tload
    from .bleu import LOW_COVER, corpus_bleu, page_refs
    from .measure_ma2 import _official, gloss
    from .score import remaining_korean, term_violations
    pj = _load(OUT / "pages.json")
    runs = {r: load_units(r) for r in TA_RUNS if not r.startswith(("A0-41", "A1-41"))}
    runs = {r: v for r, v in runs.items() if v}
    out: dict[str, Any] = {"pages": {}, "runs": {}}
    refs: dict[str, dict[int, dict[str, Any]]] = {}
    for b in BOOKS:
        _, off = _official(b)
        refs[b] = page_refs(off, tload(b, "ko"), pj["books"][b]["S"])
        bad = {int(r["page"]) for v in runs.values() for r in s_rows(v, pj) if r["book"] == b and r["verdict"] == "call_failed"}
        out["pages"][b] = sorted(p for p, d in refs[b].items() if d["coverage"]["value"] >= LOW_COVER and p not in bad)
    for run, v in runs.items():
        rows = s_rows(v, pj)
        hyps, rfs = [], []
        for b in BOOKS:
            for p in out["pages"][b]:
                rs = sorted((r for r in rows if r["book"] == b and int(r["page"]) == p), key=lambda r: r["order"])
                hyps.append(" ".join(r["en"] for r in rs if r["en"]))
                rfs.append(refs[b][p]["ref"])
        bl = corpus_bleu(hyps, rfs)
        tv = {b: term_violations(_unit_objs([r for r in rows if r["book"] == b], run), gloss(b)) for b in BOOKS}
        n_term = sum(t["units_with_term"] for t in tv.values())
        n_strict = sum(len({h["pair"] for h in t["hits"]}) for t in tv.values())
        rk = remaining_korean(_unit_objs(rows, run))
        out["runs"][run] = {"bleu": bl["bleu"], "segments": bl["segments"], "viol_strict": (n_strict / n_term) if n_term else None,
                            "units_with_term": n_term, "remaining_korean": len(rk["keys"])}
    _dump(OUT / "quality.json", out)
    print(json.dumps({r: {k: (round(x, 3) if isinstance(x, float) else x) for k, x in d.items()} for r, d in out["runs"].items()},
                     ensure_ascii=False), {b: len(p) for b, p in out["pages"].items()})


# ---------- 풀 · span · 바닥 ----------
WINDOWS = {"sam": (35, 54), "cos": (20, 39)}
TB_RUN = "B-luna-r1"  # S 쪽(다시 씀)
TB_REST = "TB-luna-r1"  # 남은 쪽


def v1_ref_pairs() -> list[dict[str, Any]]:
    """1차 참고: MA2 v1 sam 35~54 layout.json 의 block·group 단위 번역으로 지은 단위 짝."""
    d = _load(ROOT / "esg-lab" / "out" / "ma2" / "v1" / "sam-35-54" / "layout.json")
    lv = {(u["page"], u["tag"]): u for u in d["variants"][0]["units"]}
    out = []
    for u in d["units"]:
        if not u["tag"] or u["kind"] == "table":
            continue
        ko = "\n\n".join(m["source"] for m in u["members"])
        v = lv.get((u["page"], u["tag"]))
        if not re.search(r"[가-힣]", ko) or v is None or v["level"] not in ("block", "group"):
            continue
        out.append({"id": f"{u['page']}:{u['tag']}", "page": int(u["page"]), "ko": ko, "en": " ".join(v["translations"] or [])})
    return out


def tb_rows() -> list[dict[str, Any]]:
    """T-B 단위 = S 쪽은 T-A 의 B luna r1 을 다시 씀(비용은 T-A 에만) + 남은 쪽은 TB luna r1(장부 따로, 검증 B1)."""
    s, rest = load_units(TB_RUN), load_units(TB_REST)
    if s is None or rest is None:
        raise SystemExit(f"{TB_RUN} · {TB_REST} .units.json 이 다 있어야 한다")
    pj = _load(OUT / "pages.json")
    rows = [dict(r, reused=True) for r in s_rows(s, pj)] + [dict(r, reused=False) for r in rest["rows"] if r["in_denom"]]
    keys = [f"{r['book']}:{r['page']}:{r['tag']}" for r in rows]
    assert len(keys) == len(set(keys)), "S 쪽과 남은 쪽이 겹친다"
    return rows


def build_pools() -> dict[str, list[dict[str, Any]]]:
    allrows = tb_rows()
    pools: dict[str, list[dict[str, Any]]] = {}
    for b in BOOKS:
        rows = [r for r in allrows if r["book"] == b]
        up = unit_pairs(rows)
        pp = page_pairs([{"page": p["page"], "ko": p["ko"], "en": p["en"]} for p in up])
        pools[f"{b}-doc-unit"], pools[f"{b}-doc-page"] = up, pp
        if b in WINDOWS:
            lo, hi = WINDOWS[b]
            pools[f"{b}-win-unit"], pools[f"{b}-win-page"] = window(up, lo, hi), window(pp, lo, hi)
    pools["sam-v1ref-unit"] = v1_ref_pairs()
    return pools


def cmd_pool(a: argparse.Namespace) -> None:
    from trlab import span_incons as SI
    from trlab.glossary_check import load_gloss
    allrows = tb_rows()
    summ: dict[str, Any] = {"excluded": {}, "reused_from": TB_RUN,
                            "reused_units": sum(1 for r in allrows if r["reused"]), "tb_units": sum(1 for r in allrows if not r["reused"])}
    for b in BOOKS:
        rows = [r for r in allrows if r["book"] == b]
        summ["excluded"][b] = dict(Counter(r["verdict"] for r in rows if r["verdict"] in ("dropped", "call_failed")))
    for label, pairs in build_pools().items():
        b = label.split("-")[0]
        pool = SI.pool_from(pairs, load_gloss(b)["items"], SPAN_MIN_OCC)
        bs = SI.batches(pool["items"], pool["pairs"])
        _dump(OUT / "span" / "pools" / f"{label}.json", {"pairs": pairs})
        summ[label] = {"pairs": len(pairs), "terms": len(pool["terms"]), "items": len(pool["items"]), "batches": len(bs),
                       "terms_sha16": sha16(json.dumps(pool["terms"], ensure_ascii=False))}
    _dump(OUT / "pools.json", summ)
    print(json.dumps({k: v for k, v in summ.items() if k != "excluded"}, ensure_ascii=False), summ["excluded"])


def _span_est(text_len: int, n_terms: int, p: Mapping[str, float]) -> float:
    """MA2 span 어림 꼴(추정): 입력 = 글자 × 0.35 토큰, 출력 = 용어당 60 토큰 × luna 추론 1.5."""
    return (int(text_len * 0.35) * p["input"] + int(60 * n_terms * 1.5) * p["output"]) / 1e6


def judge_est(text: str, p: Mapping[str, float]) -> float:
    """S4 sol 어림(MA2 꼴, 추정): 입력 = 글자/3 + 400 토큰, 출력 1,500 토큰."""
    return ((len(text) // 3 + 400) * p["input"] + 1500 * p["output"]) / 1e6


def run_span(label: str, rep: int, dry: bool, cap: float, judge: bool, models: Mapping[str, Any],
             send: Callable[..., tuple[str | None, dict[str, Any] | None]] | None = None,
             judge_dry: bool = False) -> dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor
    from trlab import span_incons as SI
    from trlab.glossary_check import load_gloss
    b = label.split("-")[0]
    pairs = _load(OUT / "span" / "pools" / f"{label}.json")["pairs"]
    pool = SI.pool_from(pairs, load_gloss(b)["items"], SPAN_MIN_OCC)
    bs = SI.batches(pool["items"], pool["pairs"])
    cpath = OUT / "span" / f"cache-r{rep}.json"
    cache: dict[str, Any] = _load(cpath) if cpath.exists() else {}
    prices = prices_from(models)
    rd = OUT / "span" / f"{label}-r{rep}"
    plan = [(i, bt, *split_cached(bt, cache)) for i, bt in enumerate(bs)]
    todo = [(i, bt, t) for i, bt, t, _ in plan if t is not None]
    est = sum(_span_est(len(SI.INSTR) + len(SI.prompt(t)), sum(len(p["terms"]) for p in t["pairs"]), prices["gpt-6-luna"])
              for _, _, t in todo)
    info = {"label": label, "rep": rep, "terms": len(pool["terms"]), "items": len(pool["items"]), "batches": len(bs),
            "to_call": len(todo), "reused_batches": len(bs) - len(todo), "est_usd": round(est, 6)}
    if dry:
        return info
    if send is None:
        from semgraph import llm

        def send(model: str, instr: str, text: str, schema: Any, tag: str) -> tuple[str | None, dict[str, Any] | None]:
            return llm.respond(model, instr, text, schema, OUT / "span", tag, effort="low")
    spent = [0.0]

    def one(job: tuple[int, dict[str, Any], dict[str, Any]]) -> tuple[int, str, list[dict[str, Any]]]:
        i, bt, t = job
        rp = rd / f"b{i}.json"
        if rp.exists():
            d = _load(rp)
            if d["status"] == "ok":
                return i, "ok", d["answer"]
        if spent[0] >= cap:
            return i, "over_cap", []
        try:
            raw, row = send("gpt-6-luna", SI.INSTR, SI.prompt(t), SI.SCHEMA, f"MTG1/span/{label}/r{rep}/b{i}")
            ans = SI.parse_answer(raw)
            spent[0] += float((row or {}).get("costUsd") or 0.0)
            _dump(rp, {"status": "ok", "answer": ans})
            return i, "ok", ans
        except Exception as e:  # noqa: BLE001 — 부재는 갈래로
            _dump(rp, {"status": "call_failed", "error": f"{type(e).__name__}: {str(e)[:300]}"})
            return i, "call_failed", []

    with ThreadPoolExecutor(4) as ex:
        res = {i: (st, ans) for i, st, ans in ex.map(one, todo)}
    rows: list[dict[str, Any]] = []
    status = Counter()
    for i, bt, t, hit in plan:
        st, ans = res.get(i, ("reused", []))
        status[st] += 1
        if t is not None and st == "ok":
            remember(t, ans, cache)
        rows += SI.check(bt, hit + list(ans))
    _dump(cpath, cache)
    s1 = SI.judge_code(rows, "S1", pool["gloss_kr"])
    s2 = SI.judge_code(rows, "S2", pool["gloss_kr"])
    s4, jst = s2, "skipped"
    jtext = SI.judge_prompt(s2, pool["pairs"])[0] if s2 else ""
    jest = judge_est(jtext, prices["gpt-6.1-sol"]) if s2 else 0.0
    jp = rd / "judge.json"
    if judge_dry:
        jst = "dry"
    elif judge and s2 and spent[0] + jest > cap and not jp.exists():
        jst = "over_cap"
    elif judge and s2:
        text = jtext
        if jp.exists() and _load(jp)["status"] == "ok":
            jans, jst = _load(jp)["answer"], "ok"
        else:
            try:
                raw, _row = send("gpt-6.1-sol", SI.JUDGE_INSTR, text, SI.JUDGE_SCHEMA, f"MTG1/judge/{label}/r{rep}")
                jans, jst = SI.parse_answer(raw), "ok"
                _dump(jp, {"status": "ok", "answer": jans})
            except Exception as e:  # noqa: BLE001
                jans, jst = [], "call_failed"
                _dump(jp, {"status": "call_failed", "error": f"{type(e).__name__}: {str(e)[:300]}"})
        s4 = SI.apply_judge(s2, jans) if jst == "ok" else s2
    out = {**info, "status": dict(status), "judge_status": jst, "judge_est_usd": round(jest, 6), "kinds": dict(Counter(r["kind"] for r in rows)),
           "S1": sorted(f["kr"] for f in s1), "S2": sorted(f["kr"] for f in s2), "S4": sorted(f["kr"] for f in s4),
           "S4_flags": s4 if judge else []}
    if not judge_dry:
        _dump(rd / "span.json", out)
    return out


def cmd_span(a: argparse.Namespace) -> None:
    models = _load(Path(a.models))
    labels = [a.pool] if a.pool != "all" else sorted(p.stem for p in (OUT / "span" / "pools").glob("*.json"))
    tot = 0.0
    for lab in labels:
        r = run_span(lab, a.rep, a.dry, a.cap, judge=not a.no_judge, models=models, judge_dry=a.judge_dry)
        tot += r["judge_est_usd"] if a.judge_dry else r["est_usd"]
        print(json.dumps({k: r[k] for k in r if k not in ("S1", "S2", "S4", "S4_flags")}, ensure_ascii=False),
              "" if a.dry else {"S1": len(r["S1"]), "S2": len(r["S2"]), "S4": len(r["S4"])})
    print("est_usd", round(tot, 6))


def cmd_floor(a: argparse.Namespace) -> None:
    r1 = _load(OUT / "span" / "sam-doc-unit-r1" / "span.json")
    r2 = _load(OUT / "span" / "sam-doc-unit-r2" / "span.json")
    out = {"S1_r1": len(r1["S1"]), "S1_r2": len(r2["S1"]), "floor": len(set(r1["S1"]) ^ set(r2["S1"]))}
    _dump(OUT / "floor.json", out)
    print(out)


# ---------- 눈검사 ----------
EYE_RUNS = ("A0-luna-r1", "A2-luna-r1", "B-luna-r1")


def cmd_eyecheck_sample(a: argparse.Namespace) -> None:
    pj = _load(OUT / "pages.json")
    items, key = [], {}
    for run in EYE_RUNS:
        v = load_units(run)
        for r in eyecheck_sample(s_rows(v, pj)):
            code = "ok" if r["verdict"] == "ok" and not r.get("ratio_flag") else ("ratio_flag" if r["verdict"] == "ok" else r["verdict"])
            items.append(({"ko": r["ko"], "en": r["en"], "label": None},
                          {"run": run, "book": r["book"], "page": r["page"], "tag": r["tag"], "code": code}))
    random.Random(SEED).shuffle(items)
    out = {"question": "이 영문이 이 국문을, 그것만 옮겼나 — own · mixed · partial · none", "items": []}
    for q, (it, k) in enumerate(items):
        out["items"].append({"q": q, **it})
        key[str(q)] = k
    p = OUT / "labels" / "eyecheck-ta.json"
    if p.exists() and not a.force:
        raise SystemExit("eyecheck-ta.json 이 이미 있다(라벨을 덮지 않음)")
    _dump(p, out)
    _dump(OUT / "labels" / "eyecheck-ta.key.json", key)
    print("items", len(out["items"]))


def eye_ta_agree() -> dict[str, int]:
    lab = _load(OUT / "labels" / "eyecheck-ta.json")["items"]
    key = _load(OUT / "labels" / "eyecheck-ta.key.json")
    out: dict[str, int] = {}
    for run in EYE_RUNS:
        its = [{"code": key[str(it["q"])]["code"], "label": it["label"]} for it in lab if key[str(it["q"])]["run"] == run]
        out[run] = eye_agree(its)
    return out


def cmd_eyecheck_tb_sample(a: argparse.Namespace) -> None:
    out: dict[str, Any] = {"question": "같은 이름을 다르게 옮김 fix · 문맥상 다른 말 fine · 뽑기 잡음 noise", "books": {}}
    for b in BOOKS:
        d = _load(OUT / "span" / f"{b}-doc-unit-r1" / "span.json")
        flags = list(d["S4_flags"])
        if len(flags) > EYE_N:
            flags = random.Random(SEED).sample(flags, EYE_N)
        out["books"][b] = [{"kr": f["kr"], "renderings": f.get("renderings"), "examples": f.get("examples"),
                            "judge_reason": f.get("judge_reason"), "label": None} for f in flags]
    p = OUT / "labels" / "eyecheck-tb.json"
    if p.exists() and not a.force:
        raise SystemExit("eyecheck-tb.json 이 이미 있다")
    _dump(p, out)
    print({b: len(v) for b, v in out["books"].items()})


# ---------- 판정 · summary ----------
def _r(x: Any, n: int = 4) -> Any:
    return round(x, n) if isinstance(x, float) else x


def cmd_verdict(a: argparse.Namespace) -> None:
    pj = _load(OUT / "pages.json")
    check_denoms({r: s_rows(v, pj) for r in ("A0-luna-r1", "A2-luna-r1", "A2-luna-r2", "B-luna-r1", "B-luna-r2")
                  if (v := load_units(r))}, expected_denoms(pj, "S"))
    ta = _load(OUT / "tally.json")
    q = _load(OUT / "quality.json")
    led = _load(OUT / "ledger.json")
    R = ta["runs"]
    eye_p = OUT / "labels" / "eyecheck-ta.json"
    eye = eye_ta_agree() if eye_p.exists() else {r: 0 for r in EYE_RUNS}
    ver_ta = _load(eye_p).get("verifier") if eye_p.exists() else None

    def usd(run: str) -> float:
        return run_usd(led["by"], run)

    x = {"short_den": {k: R[f"{k}-luna-r1"]["short"]["n"] - R[f"{k}-luna-r1"]["short"]["call_failed"] for k in ("A0", "A2", "B")},
         "cf_rate": {k: (R[f"{k}-luna-r1"]["short"]["call_failed"] + R[f"{k}-luna-r1"]["long"]["call_failed"])
                     / max(1, R[f"{k}-luna-r1"]["all"]["n"]) for k in ("A0", "A2", "B")},
         "eye": {k: eye[f"{k}-luna-r1"] for k in ("A0", "A2", "B")}, "verifier": ver_ta,
         "bad": {"A0": R["A0-luna-r1"]["short"]["bad"], "A2": R["A2-luna-r1"]["short"]["bad"], "A2r2": R["A2-luna-r2"]["short"]["bad"],
                 "B": R["B-luna-r1"]["short"]["bad"], "Br2": R["B-luna-r2"]["short"]["bad"]},
         "bleu": {k: q["runs"][f"{k}-luna-r1"]["bleu"] for k in ("A0", "A2", "B")},
         "viol": {k: q["runs"][f"{k}-luna-r1"]["viol_strict"] or 0.0 for k in ("A2", "B")},
         "rk": {k: q["runs"][f"{k}-luna-r1"]["remaining_korean"] for k in ("A2", "B")},
         "usd": {"A2": usd("A2-luna-r1"), "B": usd("B-luna-r1")}}
    vta = verdict_ta(x)
    tb: dict[str, Any] = {}
    pools = _load(OUT / "pools.json") if (OUT / "pools.json").exists() else {}
    tbl = OUT / "labels" / "eyecheck-tb.json"
    if pools and tbl.exists():
        lab = _load(tbl)
        fix = {b: sum(1 for it in lab["books"].get(b, []) if it["label"] == "fix") for b in BOOKS}
        terms = {b: pools[f"{b}-doc-unit"]["terms"] for b in BOOKS}
        vr = lab.get("verifier")
        tb = {"terms": terms, "fix": fix, **verdict_tb(terms, fix, None if vr is None else (int(vr["agree"]), int(vr["n"]))),
              "side": denominator_side(terms, {b: pools[f"{b}-doc-page"]["terms"] for b in BOOKS})}
    summ = {"T-A": {"inputs": {k: ({kk: _r(vv) for kk, vv in v.items()} if isinstance(v, dict) else v) for k, v in x.items()},
                    "verdict": vta},
            "T-B": tb, "symdiff": {k: ta.get(f"symdiff_{k}") for k in ("A2", "B")}, "ledger_total": _r(led["total"], 6)}
    _dump(OUT / "summary.json", summ)
    lines = ["# MTG1 summary", "", f"- §0-A: **{vta['answer']}** (갈래 {vta['branch']}, F {vta['F']}, 문 {vta['gap']}"
             + (", 검증자 일치 대기" if vta.get("verifier_pending") else f", 검증자 일치 {ver_ta}/{VERIFIER_N}") + ")"]
    if tb:
        lines.append(f"- §0-B: **{tb['answer']}** (분모 섬 {tb['island']} · 잡음 {tb['noisy']} · 곁 {tb['side']}"
                     + (", 검증자 일치 대기" if tb.get("verifier_pending") else f", 검증자 일치 {tb['verifier'][0]}/{tb['verifier'][1]}") + ")")
        if tb.get("literal") != tb["answer"]:
            lines.append(f"  - 글자대로(일치 < {VERIFIER_MIN}) 읽으면 **{tb['literal']}** — 규칙을 얼린 뒤 감독이 정한 셈 정의(사후, J2): "
                         f"일치 비율 ≥ {VERIFIER_RATIO:.0%}")
    lines += ["", "| 판 | 짧음 n | dropped | shifted | call_failed | D 짧음 | D 긺 | 쪽 일치율 |", "|---|---|---|---|---|---|---|---|"]
    for run, t in R.items():
        s, lg = t["short"], t["long"]
        lines.append(f"| {run} | {s['n']} | {s['dropped']} | {s['shifted']} | {s['call_failed']} | {_r(s['D'])} | {_r(lg['D'])} | "
                     f"{_r(t['page_match'])} |")
    lines += ["", "| 판 | BLEU 쪽 | 쪽 수 | 위반 엄격 | 남은 한국어 |", "|---|---|---|---|---|"]
    for run, d in q["runs"].items():
        lines.append(f"| {run} | {_r(d['bleu'], 2)} | {d['segments']} | {_r(d['viol_strict'])} | {d['remaining_korean']} |")
    lines += ["", f"- 장부 합 {_r(led['total'], 4)} USD · 꼴별 {json.dumps({k: _r(v, 4) for k, v in led['form'].items()})}"]
    (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:4]))


COMMANDS: dict[str, Callable[[argparse.Namespace], None]] = {
    "pages": cmd_pages, "freeze": cmd_freeze, "verdict-units": cmd_verdict_units, "ledger": cmd_ledger, "kopool": cmd_kopool,
    "tally": cmd_tally, "quality": cmd_quality, "pool": cmd_pool, "span": cmd_span, "floor": cmd_floor,
    "eyecheck-sample": cmd_eyecheck_sample, "eyecheck-tb-sample": cmd_eyecheck_tb_sample, "verdict": cmd_verdict,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="measure_mtg1")
    ap.add_argument("cmd", choices=sorted(COMMANDS))
    ap.add_argument("--app", default=str(APP))
    ap.add_argument("--run")
    ap.add_argument("--calls")
    ap.add_argument("--models", default=str(APP / "esgapi/core/llm/data/models.json"))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--pool", default="all")
    ap.add_argument("--rep", type=int, default=1)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--cap", type=float, default=0.0)
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--judge-dry", action="store_true")
    a = ap.parse_args(argv)
    COMMANDS[a.cmd](a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
