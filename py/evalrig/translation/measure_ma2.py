"""`python -m evalrig.translation.measure_ma2 <명령>` — MA2 측정(1차 먼저 잴 것 A② 영문번역, `sid:MARC-TICK-558`).

py/ 에서 `uv run --frozen --group scope3colab [--with openai==3.26.0] python -m evalrig.translation.measure_ma2 <명령>`
(실호출 명령만 `--with openai==3.26.0` + 마크스폰 키 `set -a; . ~/.config/marcspon-openai.env; set +a`).

명령(C1, 호출 0):
  gold    — `prework/gold-ma2/translation-{cos,kb}-official.json` 짓고 얼림(+ freeze.txt)
  freeze  — 지시문·지칭 설정 글·구획 표·용어집·숫자 규칙 sha → `out/ma2/first-run.txt`(이 뒤에만 실호출)
  plan    — 자리별 dry-run 어림(호출 0) → `out/ma2/plan.json`·표준 출력 표(1차는 드라이버 dry 의 plan.json 을 읽음)
  ledger  — 두 장부(랩 `out/ma2/calls.jsonl` + 1차 `out/ma2/v1/*/calls.jsonl`) 합·자리별

산출 `MA2_OUT`(기본 main 쪽 절대 경로 `<marcspon>/esg-lab/out/ma2` — 워크트리 out/ 은 밴드 정리 때 지워진다) ·
정답 `MA2_GOLD`(기본 `<marcspon>/prework/gold-ma2`).
랩 꼴 = 2차 엔진 꼴의 시제품(2차 엔진 아님). 단위 `Origin.engine = v2`, 판 칸 `translation.labform@1`(감독 답 ③).
랩 싼 모델 = gpt-6-luna(`sid:MARC-ENT-628(627 갈음)` — ③ 펼침 번역 · M-T2 · M-T3), 1차 쪽은 그대로 gpt-4.1-mini.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import labpipe as L

_HERE = Path(__file__).resolve()
ROOT = _HERE.parents[4]
if ROOT.name.endswith(".worktrees"):
    ROOT = ROOT.parent
OUT = Path(os.environ.get("MA2_OUT", ROOT / "esg-lab" / "out" / "ma2"))
GOLD = Path(os.environ.get("MA2_GOLD", ROOT / "prework" / "gold-ma2"))
EVA_GOLD = ROOT / "prework" / "gold-eva"
PROMPTS = ROOT / "esg-app" / "apps" / "api-py" / "esgapi" / "translation" / "engines" / "colab_158" / "prompts.py"
PROMPTS_SHA16 = "34c3aa323cc58a80"  # 발주서 §1 — 파일 sha16(git 2df772d7 2026-10-23)
MODELS_JSON = ROOT / "esg-app" / "apps" / "api-py" / "esgapi" / "core" / "llm" / "data" / "models.json"
MODELS_SHA16 = "116d61d561c98bec"
V2_INSTR_SHA16 = "71258cda68336c91"
GOLD_AT = "2026-11-09"  # 정답 셋 at — 고정(두 번 지어 sha 같음)
TRX3_RUNS = ROOT / "esg-lab" / "out" / "trx3" / "runs"

LUNA, MINI, SOL = "gpt-6-luna", "gpt-4.1-mini", "gpt-6.1-sol"
LAB_TR_MODEL = LUNA  # sid:MARC-ENT-628(627 갈음)
# 책 · 범위 · 1차 문체 칸(업종 글 — 이름 아님. sam 은 TRL3 드라이버 값 그대로, cos·kb 는 러너가 정함)
BOOKS = {"sam": {"subject": "전자·반도체 제조"}, "cos": {"subject": "화장품 연구·개발·제조"}, "kb": {"subject": "금융"}}
RANGES = {"mt1": ("sam", 35, 54), "mt6": ("sam", 35, 44), "t2-sam": ("sam", 35, 54), "t2-cos": ("cos", 20, 39),
          "t3-sam": ("sam", 45, 49), "t3-cos": ("cos", 23, 27), "t3-kb": ("kb", 17, 21)}

# ---------- 장부 · 자리 상한(발주서 §1 표 · 감독 답 2: lab/span 0.20) ----------
CAPS = {"lab/tr": 0.15, "lab/chk": 0.10, "lab/reask": 0.05, "lab/span": 0.20, "lab/fix": 0.35,
        "t6": 0.10, "t2": 0.50, "t3": 0.20}
V1_CAP = 0.80  # 1차 — 드라이버의 BudgetLimit 이 막는다(감독 답 2: 0.60 → 0.80)
BAND_CAP = 5.0
BAND_STOP = 4.5  # 누적 + 잡아 둔 몫 + 예상이 이를 넘으면 보내지 않는다(멈추고 감독에게)
EPS = 1e-9
# 어림 표(발주서 §1 「어림」 — luna 로 바꾼 뒤에도 같은 값을 기준으로, 1.5배 넘으면 멈춤)
PLAN_REF = {"lab/tr": 0.07, "lab/chk": 0.05, "lab/reask": 0.01, "lab/span": 0.10, "lab/fix": 0.25,
            "t6": 0.05, "t2": 0.32, "t3": 0.12, "v1": 2.5}
TAG_RE = re.compile(r"^MA2/(lab/[a-z]+|t6|t2|t3)/")
_LOCK = threading.Lock()
_RESERVED: dict[str, float] = {s: 0.0 for s in CAPS}


def site_of(tag: str) -> str | None:
    m = TAG_RE.match(tag or "")
    return m.group(1) if m else None


def _read_jsonl(p: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def lab_rows(out: Path | None = None) -> list[dict[str, Any]]:
    return _read_jsonl((out or OUT) / "calls.jsonl")


def v1_rows(out: Path | None = None) -> list[dict[str, Any]]:
    rows = []
    for p in sorted(((out or OUT) / "v1").glob("*/calls.jsonl")):
        if p.parent.name.endswith("-dry"):
            continue
        rows += [dict(r, site="v1") for r in _read_jsonl(p)]
    return rows


def _cost(r: dict[str, Any]) -> float:
    return float(r.get("costUsd") or 0.0) if not r.get("dry_run") else 0.0


def site_spent(site: str, rows: Iterable[dict[str, Any]]) -> float:
    return sum(_cost(r) for r in rows if (r.get("site") or site_of(r.get("tag", ""))) == site)


def band_spent(lab: Iterable[dict[str, Any]], v1: Iterable[dict[str, Any]]) -> float:
    return sum(_cost(r) for r in lab) + sum(_cost(r) for r in v1)


def over_cap(site: str, usd: float, lab: Sequence[dict[str, Any]], v1: Sequence[dict[str, Any]],
             reserved: dict[str, float] | None = None) -> str | None:
    """보내면 안 되면 까닭(`over_cap` 자리 상한 · `band_stop` 밴드 4.5), 되면 None. 누적 + 잡아 둔 몫 + 예상으로 본다."""
    held = reserved if reserved is not None else _RESERVED
    if site not in CAPS:
        raise ValueError(f"모르는 자리 {site!r}")
    if site_spent(site, lab) + held.get(site, 0.0) + usd > CAPS[site] + EPS:
        return "over_cap"
    if band_spent(lab, v1) + sum(held.values()) + usd > BAND_STOP + EPS:
        return "band_stop"
    return None


def reserve(site: str, usd: float) -> str | None:
    with _LOCK:
        why = over_cap(site, usd, lab_rows(), v1_rows())
        if why is None:
            _RESERVED[site] += usd
        return why


def release(site: str, usd: float) -> None:
    with _LOCK:
        _RESERVED[site] = max(0.0, _RESERVED[site] - usd)


def _append(row: dict[str, Any]) -> None:
    with _LOCK:
        OUT.mkdir(parents=True, exist_ok=True)
        with open(OUT / "calls.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def log_skip(model: str, tag: str, est: float, reason: str, **extra: Any) -> dict[str, Any]:
    row = {"tag": tag, "model": model, "dry_run": True, "skip": reason, "costUsd": 0.0, "estimateUsd": round(est, 6),
           "site": site_of(tag), **extra}
    _append(row)
    return row


def ledger_summary(lab: Sequence[dict[str, Any]], v1: Sequence[dict[str, Any]]) -> dict[str, Any]:
    from semgraph import llm
    by: dict[str, dict[str, Any]] = {}
    for r in list(lab) + list(v1):
        s = r.get("site") or site_of(r.get("tag", "")) or "?"
        d = by.setdefault(s, {"usd": 0.0, "calls": 0, "skips": 0, "in": 0, "cached": 0, "out": 0, "recomputed": 0.0})
        if r.get("dry_run"):
            d["skips"] += 1
            continue
        d["usd"] += _cost(r)
        d["calls"] += 1
        d["in"] += int(r.get("inputTokens") or 0)
        d["cached"] += int(r.get("cachedTokens") or 0)
        d["out"] += int(r.get("outputTokens") or 0)
        m = r.get("model")
        if m in llm.PRICES:
            if "cacheReadTokens" in r:  # 1차(esg-app CallRecord): inputTokens = 캐시 안 탄 몫, 캐시 읽기는 따로
                cr = int(r.get("cacheReadTokens") or 0)
                d["recomputed"] += llm.cost(m, int(r.get("inputTokens") or 0) + cr, cr, int(r.get("outputTokens") or 0))
            else:  # 랩(semgraph): inputTokens 가 캐시 몫을 품음
                d["recomputed"] += llm.cost(m, int(r.get("inputTokens") or 0), int(r.get("cachedTokens") or 0),
                                            int(r.get("outputTokens") or 0))
    total = band_spent(lab, v1)
    return {"sites": dict(sorted(by.items())), "band_usd": total, "band_stop": BAND_STOP, "band_ok": total <= BAND_STOP + EPS}


# ---------- 1차 프롬프트(파일 경로로 읽기) ----------
def file_sha16(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:16]


def load_prompts(path: Path = PROMPTS) -> Any:
    got = file_sha16(path)
    if got != PROMPTS_SHA16:
        raise SystemExit(f"prompts.py sha16 {got} ≠ {PROMPTS_SHA16} — 멈춤")
    spec = importlib.util.spec_from_file_location("colab158_prompts_ma2", path)
    m = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(m)
    return m


# ---------- 책 자료 ----------
def gloss(key: str) -> list[dict[str, Any]]:
    from trlab.glossary_check import load_gloss
    return [{"kr": it["kr"], "en": it["en"]} for it in load_gloss(key)["items"]]


def company_name(key: str) -> str:
    """이름 표 첫 이름 — 실행 때만 읽는다(코드·산출 요약에 싣지 않음)."""
    from trlab.common import GOLD as TRX1_GOLD
    return json.loads((Path(TRX1_GOLD) / "names" / f"{key}.json").read_text(encoding="utf-8"))["names"][0]


def toc_items(key: str) -> list[dict[str, Any]]:
    d = json.loads((TRX3_RUNS / f"{key}-ko-c3.json").read_text(encoding="utf-8"))
    if d.get("mode") != "c3" or d.get("status") != "ok":
        raise SystemExit(f"{key} c3 판이 아니다")
    return d["pred"]["items"]


def book_blocks(key: str, lo: int, hi: int) -> list[dict[str, Any]]:
    from trlab.common import load
    return L.input_blocks(load(key, "ko"), lo, hi)


def section_table(site: str) -> dict[str, Any]:
    key, lo, hi = RANGES[site]
    return {"book": key, "range": [lo, hi], "sections": L.sections(toc_items(key), lo, hi)}


# ---------- 어림 ----------
# 토큰/글자: 국문 ≈ 1, 영문·JSON ≈ 0.3(추정 — 발주서 「입력 1·출력 0.5 토큰/글자」의 국문 몫에 지시문 영문 몫을 따로).
KO_TOK, EN_TOK, KO_OUT_TOK = 1.0, 0.3, 0.5
REASON_FACTOR = {LUNA: 1.5, SOL: 1.5, MINI: 1.0}  # 추론 토큰 몫 어림(effort low, 추정)
TOK_PER_CHAR_CHK = 0.366  # TRX7 luna 실측(tx10_pick.TOK_PER_CHAR0)
OUT_TOK_BLOCK_CHK = 161
_HAN = re.compile(r"[가-힣]")


def in_tokens(text: str) -> int:
    k = len(_HAN.findall(text))
    return int(k * KO_TOK + (len(text) - k) * EN_TOK)


def est_usd(model: str, inp: int, outp: int) -> float:
    from semgraph import llm
    return llm.cost(model, inp, 0, int(outp * REASON_FACTOR.get(model, 1.0)))


def tr_jobs(P: Any, key: str, blocks: Sequence[dict[str, Any]], calls: Sequence[dict[str, Any]],
                subject: str = "we") -> list[dict[str, Any]]:
    """번역 호출 목록(지시·사용자 글·id). 지시문은 판마다 하나."""
    g = gloss(key)
    sysp = L.system_prompt(P, company_name(key), BOOKS[key]["subject"], subject)
    by = {b["id"]: b for b in blocks}
    out = []
    for c in calls:
        cb = [by[i] for i in c["ids"]]
        out.append({"call": c["call"], "pages": c["pages"], "section": c["section"], "ids": c["ids"], "system": sysp,
                    "user": L.user_prompt(P, cb, g), "ko_chars": c["ko_chars"]})
    return out


def est_tr(model: str, reqs: Sequence[dict[str, Any]]) -> float:
    return sum(est_usd(model, in_tokens(r["system"]) + in_tokens(r["user"]), int(r["ko_chars"] * KO_OUT_TOK) + 50 * len(r["ids"]))
               for r in reqs)


def est_chk(blocks: Sequence[dict[str, Any]]) -> tuple[float, int]:
    """체크리스트 어림 — EN 이 아직 없으니 EN ≈ 국문 × 2 글자(추정)로 묶음을 짓고 TRX7 luna 토큰/글자."""
    from trlab import fullrev as F
    from trlab import review_probe as R
    mats = [{"id": b["id"], "ko": b["ko"], "en": "x" * (2 * len(b["ko"])), "glossary": []} for b in blocks]
    plan = L.chk_plan(mats)
    usd = sum(est_usd(LUNA, int(F.bundle_chars(bs) * TOK_PER_CHAR_CHK), OUT_TOK_BLOCK_CHK * len(bs) // 4) for bs in plan)
    _ = R
    return usd, len(plan)


REASK_USD_TRX10 = 0.00007  # TRX10 실측 되묻기 한 호출(luna, 발주서 §1 어림 근거)


def est_reask(n: int) -> float:
    return n * REASK_USD_TRX10


def est_span(key: str, units: Sequence[dict[str, Any]], variants: int) -> tuple[float, int, int]:
    """S1(luna) 판마다 묶음 수 × 어림 + S4(sol) 판마다 1. EN ≈ 국문 × 2 글자(추정)."""
    from trlab import span_incons as SI
    pairs = [{"id": u["id"], "page": u["page"], "ko": u["ko"], "en": "x" * (2 * len(u["ko"]))} for u in units]
    P = SI.pool_from(pairs, gloss(key), L.SPAN_MIN_OCC)
    bs = SI.batches(P["items"], P["pairs"])
    one = sum(est_usd(LUNA, int((len(SI.INSTR) + len(SI.prompt(b))) * 0.35), 60 * sum(len(p["terms"]) for p in b["pairs"]))
              for b in bs)
    judge = est_usd(SOL, 2500, 1500)
    return variants * (one + judge), variants * len(bs), variants


def est_fix_block(ko: str) -> float:
    """고침 한 블록(sol) — 입력 = FIX_INSTR + 국문 + 지금 EN(≈ 국문 × 2 글자) + 까닭 몇 줄, 출력 = 고친 EN(추정)."""
    en_tok = int(2 * len(ko) * EN_TOK)
    return est_usd(SOL, int(len(L.FIX_INSTR) * EN_TOK) + in_tokens(ko) + en_tok + 150, en_tok + 40)


def est_fix(blocks: Sequence[dict[str, Any]], n: int = L.FIX_CAP) -> float:
    """위끝 — 국문이 가장 긴 문단 n 블록이 다 걸렸다고 봄."""
    paras = sorted((b for b in blocks if b["kind"] == "paragraph"), key=lambda b: -len(b["ko"]))[:n]
    return sum(est_fix_block(b["ko"]) for b in paras)


def page_units_ko(blocks: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    pages: dict[int, list[str]] = {}
    for b in blocks:
        pages.setdefault(b["page"], []).append(b["ko"])
    return [{"id": f"page:{p}", "page": p, "ko": "\n".join(v)} for p, v in sorted(pages.items())]


def plan_rows(P: Any) -> list[dict[str, Any]]:
    rows = []

    def add(site, what, model, n, usd):
        rows.append({"자리": site, "무엇": what, "모델": model, "건수": n, "어림 USD": round(usd, 4),
                     "발주서 어림": PLAN_REF.get(site), "1.5배 넘음": usd > 1.5 * PLAN_REF.get(site, 0) + EPS,
                     "자리 상한": CAPS.get(site, V1_CAP)})

    key, lo, hi = RANGES["mt1"]
    bs = book_blocks(key, lo, hi)
    secs = L.sections(toc_items(key), lo, hi)
    tr = tr_jobs(P, key, bs, L.toc_calls(bs, secs))
    add("lab/tr", "M-T1 펼침 번역(목차 구획) sam 35~54", LAB_TR_MODEL, len(tr), est_tr(LAB_TR_MODEL, tr))
    u, n = est_chk(bs)
    add("lab/chk", "M-T1 블록 체크리스트 V2 묶음", LUNA, n, u)
    add("lab/reask", "M-T1 누락·숫자 되묻기(상한 150)", LUNA, 150, est_reask(150))
    u, n, _ = est_span(key, page_units_ko(bs), 4)
    add("lab/span", "M-T1 S1+S4 판 넷(lab-raw·lab-fixed·v1 single·v1 +autofix, 쪽 단위)", f"{LUNA}+{SOL}", n + 4, u)
    add("lab/fix", "M-T1 고침(상한 60 블록, 가장 긴 문단 60 위끝)", SOL, L.FIX_CAP, est_fix(bs))
    k6, a6, b6 = RANGES["mt6"]
    bs6 = book_blocks(k6, a6, b6)
    tr6 = tr_jobs(P, k6, bs6, L.toc_calls(bs6, L.sections(toc_items(k6), a6, b6)))
    u6, n6 = est_chk(bs6)
    add("t6", "M-T6 견줌 판 mini 번역 sam 35~44 + 그 체크리스트", f"{MINI}+{LUNA}", len(tr6) + n6, est_tr(MINI, tr6) + u6)
    t2u, t2n = 0.0, 0
    for site, mode in (("t2-sam", "page"), ("t2-cos", "toc"), ("t2-cos", "page")):
        k, a, b = RANGES[site]
        bb = book_blocks(k, a, b)
        ss = L.sections(toc_items(k), a, b)
        rq = tr_jobs(P, k, bb, (L.toc_calls if mode == "toc" else L.page_calls)(bb, ss))
        su, sn, _ = est_span(k, page_units_ko(bb), 1)
        t2u += est_tr(LAB_TR_MODEL, rq) + su
        t2n += len(rq) + sn + 1
    add("t2", "M-T2 sam 쪽 구획 · cos 목차·쪽 구획 + S1·S4", f"{LAB_TR_MODEL}+{SOL}", t2n, t2u)
    t3u, t3n = 0.0, 0
    for site in ("t3-sam", "t3-cos", "t3-kb"):
        k, a, b = RANGES[site]
        bb = book_blocks(k, a, b)
        ss = L.sections(toc_items(k), a, b)
        for subj in ("we", "third"):
            rq = tr_jobs(P, k, bb, L.toc_calls(bb, ss), subj)
            t3u += 2 * est_tr(LAB_TR_MODEL, rq)
            t3n += 2 * len(rq)
    add("t3", "M-T3 책 셋 × 5쪽 × 판 둘 × 2회", LAB_TR_MODEL, t3n, t3u)
    v1p = OUT / "v1" / "sam-35-54-dry" / "plan.json"
    if v1p.exists():
        d = json.loads(v1p.read_text(encoding="utf-8"))
        rows.append({"자리": "v1", "무엇": "M-T1 1차 PDF 흐름 sam 35~54 문체 A + 자동수정(계획 위끝)", "모델": MINI,
                     "건수": d.get("calls"), "어림 USD": d.get("totalEstimateUsd"), "발주서 어림": PLAN_REF["v1"],
                     "1.5배 넘음": (d.get("totalEstimateUsd") or 0) > 1.5 * PLAN_REF["v1"] + EPS, "자리 상한": V1_CAP})
    else:
        rows.append({"자리": "v1", "무엇": "1차 드라이버 dry 먼저", "모델": MINI, "건수": None, "어림 USD": None,
                     "발주서 어림": PLAN_REF["v1"], "1.5배 넘음": None, "자리 상한": V1_CAP})
    return rows


# ---------- 얼림 ----------
def freeze_lines(P: Any) -> list[str]:
    """얼릴 값의 sha 줄(시각 없음 — assert_frozen 이 다시 지어 견준다)."""
    from trlab import fullrev as F
    from trlab import omit_rule as OR
    from trlab import review_probe as R
    from trlab import span_incons as SI
    if L.subject_block(P, P.SUBJECT_PRONOUN_PROMPT) != P.SUBJECT_FRAGMENT_RULES_BLOCK:
        raise SystemExit("third 판 틀이 1차 SUBJECT_FRAGMENT_RULES_BLOCK 틀과 다르다 — 멈춤")
    if L.sha16(R.V2_INSTR) != V2_INSTR_SHA16:
        raise SystemExit("V2_INSTR sha 가 TRX7 얼림과 다르다 — 멈춤")
    lines = [f"{PROMPTS_SHA16}  prompts.py (파일, esg-app colab_158)",
             f"{file_sha16(MODELS_JSON)}  models.json (단가 출처, 발주서 {MODELS_SHA16})",
             f"{L.sha16(L.subject_rules(P, 'we'))}  지칭 we = SUBJECT_FRAGMENT_RULES_BLOCK",
             f"{L.sha16(L.subject_rules(P, 'third'))}  지칭 third (THIRD_SUBJECT {L.sha16(L.THIRD_SUBJECT)})",
             f"{L.sha16(json.dumps(L.ITEMS_SCHEMA, sort_keys=True))}  ITEMS_SCHEMA",
             f"{L.sha16(L.NUM_REASK_INSTR)}  NUM_REASK_INSTR",
             f"{L.sha16(OR.REASK_INSTR)}  omit_rule.REASK_INSTR",
             f"{L.sha16(R.V2_INSTR)}  V2_INSTR",
             f"{L.sha16(SI.INSTR)}  span_incons.INSTR · {L.sha16(SI.JUDGE_INSTR)} JUDGE_INSTR",
             f"{L.sha16(L.FIX_INSTR)}  FIX_INSTR",
             f"{L.sha16(L.NUM.pattern + '|' + L.KO_SCALE.pattern + '|' + L.YEAR_RANGE.pattern)}  숫자 규칙 NUM·KO_SCALE·YEAR_RANGE (NUM_MIN_LEN {L.NUM_MIN_LEN})",
             f"상수 CALL_MAX_KO_CHARS {L.CALL_MAX_KO_CHARS} · SHORT_EN {L.SHORT_EN} · FIX_CAP {L.FIX_CAP} · CHK {L.CHK_MAX_CHARS}/{L.CHK_SEED} · "
             f"SPAN_MIN_OCC {L.SPAN_MIN_OCC} · BUNDLE_MAX_CHARS(fullrev) {F.BUNDLE_MAX_CHARS} · LAB_TR_MODEL {LAB_TR_MODEL} · CAPS {CAPS} · BAND_STOP {BAND_STOP}",
             f"{file_sha16(Path(L.__file__))}  labpipe.py"]
    for site in RANGES:
        st = section_table(site)
        lines.append(f"{L.sha16(json.dumps(st, sort_keys=True))}  구획 {site} {st['book']} {st['range']} → {st['sections']}")
    for key in BOOKS:
        from trlab.common import GOLD as TRX1_GOLD
        lines.append(f"{file_sha16(Path(TRX1_GOLD) / 'glossary' / f'{key}.json')}  용어집 {key} ({len(gloss(key))})")
    for site in ("mt1", "t2-cos", "t3-sam", "t3-cos", "t3-kb"):
        key, lo, hi = RANGES[site]
        bs = book_blocks(key, lo, hi)
        lines.append(f"{L.sha16(json.dumps([[b['id'], L.sha16(b['ko'])] for b in bs]))}  입력 블록 {site} {len(bs)} 블록 {sum(len(b['ko']) for b in bs)}자")
    return lines


def assert_frozen(P: Any) -> None:
    p = OUT / "first-run.txt"
    if not p.exists():
        raise SystemExit("out/ma2/first-run.txt 가 없다 — 얼리기 전에 실호출하지 않는다")
    kept = [x for x in p.read_text(encoding="utf-8").splitlines()[1:] if x.strip()]
    now = freeze_lines(P)
    if kept != now:
        diff = [x for x in now if x not in kept]
        raise SystemExit(f"얼린 값이 바뀌었다 — 멈춤: {diff[:3]}")


# ---------- 정답 셋 ----------
def freeze_gold(path: Path) -> str:
    from ..common import gold as G
    s = G.freeze(path)
    ft = GOLD / "freeze.txt"
    rows = {}
    if ft.exists():
        for line in ft.read_text(encoding="utf-8").splitlines():
            if line.strip():
                n, h = line.split()
                rows[n] = h
    rows[path.name] = s
    ft.write_text("".join(f"{n} {h}\n" for n, h in sorted(rows.items())), encoding="utf-8")
    return s


def cmd_gold(_: Any) -> None:
    from ..common import gold as G
    from . import gold as TG
    GOLD.mkdir(parents=True, exist_ok=True)
    for key in ("cos", "kb"):
        gs = TG.build_official(at=GOLD_AT, root=ROOT, key=key)
        p = GOLD / f"{gs['setId']}.json"
        p.write_text(G.dumps(gs), encoding="utf-8")
        print("gold", p.name, len(gs["items"]), freeze_gold(p))


def cmd_freeze(_: Any) -> None:
    P = load_prompts()
    lines = freeze_lines(P)
    OUT.mkdir(parents=True, exist_ok=True)
    head = f"# MA2 얼림 {time.strftime('%Y-%m-%d %H:%M:%S')} — 실호출 전(assert_frozen 이 둘째 줄부터 다시 지어 견줌)"
    (OUT / "first-run.txt").write_text("\n".join([head] + lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def cmd_plan(_: Any) -> None:
    P = load_prompts()
    rows = plan_rows(P)
    lab = sum(r["어림 USD"] or 0 for r in rows if r["자리"] != "v1")
    v1 = next((r["어림 USD"] for r in rows if r["자리"] == "v1"), None)
    doc = {"rows": rows, "lab_usd": lab, "v1_upper_usd": v1, "lab_tr_model": LAB_TR_MODEL,
           "stop": any(r["1.5배 넘음"] for r in rows) or (v1 or 0) > 3.75}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "plan.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("| 자리 | 모델 | 건수 | 어림 USD | 발주서 어림 | 1.5배 넘음 | 자리 상한 |")
    print("|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['자리']} | {r['모델']} | {r['건수']} | {r['어림 USD']} | {r['발주서 어림']} | {r['1.5배 넘음']} | {r['자리 상한']} |")
    print(f"랩 합 {lab:.4f} · 1차 위끝 {v1} · 멈춤 {doc['stop']}")


def cmd_ledger(_: Any) -> None:
    s = ledger_summary(lab_rows(), v1_rows())
    print(json.dumps(s, ensure_ascii=False, indent=1))
    if not s["band_ok"]:
        raise SystemExit(1)


COMMANDS = {"gold": cmd_gold, "freeze": cmd_freeze, "plan": cmd_plan, "ledger": cmd_ledger}


# ======================================================================================================
# C2 — 실호출(랩 ③~⑧ · M-T6 견줌 판 · span 판 넷) · 1차 변환 · M-T1·M-T6 표
# ======================================================================================================
from concurrent.futures import ThreadPoolExecutor  # noqa: E402

THREADS = 4  # 같은 키를 다섯 밴드가 쓴다 — 429 면 재시도 대기만(semgraph respond max_retries 1)
TR_MAX_OUTPUT = 32000
LABFORM = "translation.labform@1"
CONVERTER = "ma2.labform@1"
V1_DIR = "sam-35-54"
V1_OFFSET = 34
MISSING_STOP = 0.02  # §5 멈춤 3 — 재시도 한 번 뒤에도 빠진 블록 id > 2%
BLEU_SAME = 1.0  # 「차이 없음」 문턱(이 발주서의 문턱 — 통계 시험 아님)
FIX_STOP_USD = 1.5 * PLAN_REF["lab/fix"]  # R1 — ⑧ 앞 대상 수 × 블록당 어림이 넘으면 멈춤
_FROZEN_OK = False


def _dump(p: Path, obj: Any) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return p


def _load(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def _frozen(P: Any) -> None:
    global _FROZEN_OK
    if not _FROZEN_OK:
        assert_frozen(P)
        _FROZEN_OK = True


def send(P: Any, model: str, instr: str, text: str, schema: dict[str, Any], tag: str, est: float, raw_path: Path,
         max_output: int = 16000) -> dict[str, Any]:
    """호출 하나 — 이미 있으면 다시 부르지 않음(되살림) · 얼림 단언 · reserve(자리 상한·밴드 4.5) · respond · 장부 한 줄.
    → {status: ok|over_cap|band_stop|call_failed, raw, row}. 답 원문은 raw_path(out/ma2 안)에만."""
    if raw_path.exists():
        d = _load(raw_path)
        if d.get("status") == "ok":
            return d
    _frozen(P)
    site = site_of(tag)
    why = reserve(site, est)
    if why:
        log_skip(model, tag, est, why)
        d = {"status": why, "raw": None, "row": None, "tag": tag}
        _dump(raw_path, d)
        if why == "band_stop":
            raise SystemExit(f"밴드 누적 {BAND_STOP} 문턱 — 멈춤({tag})")
        return d
    from semgraph import llm
    scratch = OUT / ".scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        raw, row = llm.respond(model, instr, text, schema, scratch, tag, effort="low", max_output=max_output)
    except Exception as e:  # noqa: BLE001 — 부재는 갈래로
        release(site, est)
        d = {"status": "call_failed", "raw": None, "row": None, "tag": tag, "error": f"{type(e).__name__}: {str(e)[:300]}"}
        _dump(raw_path, d)
        return d
    release(site, est)
    row = dict(row, site=site, estimateUsd=round(est, 6), chars=len(instr) + len(text))
    _append(row)
    d = {"status": "ok", "raw": raw, "row": row, "tag": tag}
    _dump(raw_path, d)
    return d


def _pool(fn, items):
    with ThreadPoolExecutor(THREADS) as ex:
        return list(ex.map(fn, items))


# ---------- ③ 펼침 번역 ----------
def translate(P: Any, run: str, key: str, lo: int, hi: int, *, mode: str = "toc", subject: str = "we",
              model: str = LAB_TR_MODEL, site: str = "lab/tr") -> dict[str, Any]:
    """→ {blocks, secs, en, calls(상태·빠진 id), missing_rate}. 빠진 id 는 한 번만 다시(그 id 만)."""
    blocks = book_blocks(key, lo, hi)
    secs = L.sections(toc_items(key), lo, hi)
    calls = (L.toc_calls if mode == "toc" else L.page_calls)(blocks, secs)
    jobs = tr_jobs(P, key, blocks, calls, subject)
    rd = OUT / "lab" / run / "raw"
    by = {b["id"]: b for b in blocks}

    def one(j, suffix=""):
        est = est_tr(model, [j])
        d = send(P, model, j["system"], j["user"], L.ITEMS_SCHEMA, f"MA2/{site}/{run}/c{j['call']}{suffix}", est,
                 rd / f"tr-c{j['call']}{suffix}.json", TR_MAX_OUTPUT)
        return j, d, L.read_items(d.get("raw"), j["ids"])

    got = _pool(one, jobs)
    en: dict[str, str] = {}
    info = []
    retry_jobs = []
    for j, d, r in got:
        en.update(r["en"])
        info.append({"call": j["call"], "pages": j["pages"], "section": j["section"], "n_ids": len(j["ids"]), "status": d["status"],
                     "parsed": r["parsed"], "missing": r["missing"], "extra": r["extra"], "dup": r["dup"], "empty": r["empty"]})
        if r["missing"]:
            sub = [by[i] for i in r["missing"]]
            retry_jobs.append({**j, "ids": r["missing"], "user": L.user_prompt(P, sub, gloss(key)),
                               "ko_chars": sum(len(b["ko"]) for b in sub)})
    for j, d, r in _pool(lambda j: one(j, "-retry"), retry_jobs):
        en.update(r["en"])
        info.append({"call": j["call"], "pages": j["pages"], "section": j["section"], "retry": True, "n_ids": len(j["ids"]), "status": d["status"], "parsed": r["parsed"],
                     "missing": r["missing"]})
    miss = [b["id"] for b in blocks if b["id"] not in en]
    rate = len(miss) / max(1, len(blocks))
    out = {"run": run, "book": key, "range": [lo, hi], "mode": mode, "subject": subject, "model": model, "sections": secs,
           "n_blocks": len(blocks), "n_calls": len(calls), "calls": info, "missing_ids": miss, "missing_rate": rate}
    _dump(OUT / "lab" / run / "tr.json", out)
    _dump(OUT / "lab" / run / "blocks.json", [{"id": b["id"], "page": b["page"], "order": b["order"], "kind": b["kind"],
                                               "section": L.section_of(secs, b["page"]), "ko_sha16": L.sha16(b["ko"]),
                                               "en": en.get(b["id"])} for b in blocks])
    if rate > MISSING_STOP:
        raise SystemExit(f"{run}: 빠진 블록 id {len(miss)}/{len(blocks)} > 2% — 멈춤(§5 3)")
    return {"blocks": blocks, "secs": secs, "en": en, **out}


def load_lab(run: str, key: str, lo: int, hi: int) -> dict[str, Any]:
    """저장한 blocks.json + 원 국문(sha 대조) → translate 와 같은 꼴."""
    blocks = book_blocks(key, lo, hi)
    saved = {r["id"]: r for r in _load(OUT / "lab" / run / "blocks.json")}
    for b in blocks:
        if saved[b["id"]]["ko_sha16"] != L.sha16(b["ko"]):
            raise SystemExit(f"{run}: {b['id']} 국문 sha 가 다르다")
    en = {i: r["en"] for i, r in saved.items() if r.get("en")}
    tr = _load(OUT / "lab" / run / "tr.json")
    return {"blocks": blocks, "secs": [tuple(s) for s in tr["sections"]], "en": en, **tr}


# ---------- ④ 코드 검사 · ⑤ 되묻기 ----------
def checks(lab: dict[str, Any], key: str) -> dict[str, Any]:
    g = gloss(key)
    return {b["id"]: L.code_checks(b, lab["en"].get(b["id"]), g) for b in lab["blocks"]}


def reask(P: Any, run: str, lab: dict[str, Any], chk: dict[str, dict[str, Any]], site: str = "lab/reask") -> dict[str, Any]:
    from trlab import omit_rule as OR
    by = {b["id"]: b for b in lab["blocks"]}
    jobs = []
    for bid, c in chk.items():
        en = lab["en"].get(bid)
        if not en:
            continue
        if c["omit"].get("flagged"):
            ins, text, schema, ks = OR.build_reask(by[bid]["ko"], en)
            jobs.append(("omit", bid, ins, text, schema, ks))
        if c["num"]:
            ins, text, schema = L.build_num_reask(by[bid]["ko"], en, c["num"])
            jobs.append(("num", bid, ins, text, schema, c["num"]))
    rd = OUT / "lab" / run / "raw"

    def one(job):
        kind, bid, ins, text, schema, extra = job
        d = send(P, LUNA, ins, text, schema, f"MA2/{site}/{run}/{kind}/{bid}", REASK_USD_TRX10 * 2, rd / f"reask-{kind}-{bid}.json")
        if kind == "omit":
            r = OR.read_reask(d.get("raw"), len(extra))
            r["missing_text"] = [extra[i - 1] for i in r["missing"]]
        else:
            r = L.read_num_reask(d.get("raw"), extra)
        return {"kind": kind, "id": bid, "status": d["status"], **r}

    rows = _pool(one, jobs)
    _dump(OUT / "lab" / run / "reask.json", rows)
    return {"rows": rows, "omit_confirmed": {r["id"]: r["missing_text"] for r in rows if r["kind"] == "omit" and r["missing"]},
            "num_confirmed": {r["id"]: r["missing"] for r in rows if r["kind"] == "num" and r["missing"]}}


# ---------- ⑥ 블록 체크리스트 ----------
def checklist(P: Any, run: str, lab: dict[str, Any], key: str, site: str = "lab/chk") -> dict[str, Any]:
    from trlab import fullrev as F
    mats = L.chk_mats(lab["blocks"], lab["en"], gloss(key))
    plan = L.chk_plan(mats)
    rd = OUT / "lab" / run / "raw"

    def one(ib):
        i, bs = ib
        ins, text, schema = F.build_bundle_prompt(bs)
        est = est_usd(LUNA, int(F.bundle_chars(bs) * TOK_PER_CHAR_CHK), OUT_TOK_BLOCK_CHK * len(bs) // 4)
        d = send(P, LUNA, ins, text, schema, f"MA2/{site}/{run}/b{i}", est, rd / f"chk-b{i}.json")
        a = F.split_bundle_answer(d.get("raw"), len(bs)) if d["status"] == "ok" else {"parsed": False, "per_block": [[] for _ in bs], "oob": []}
        return [{"id": b["id"], "bundle": i, "status": d["status"], "parsed": a["parsed"], "issues": a["per_block"][k]}
                for k, b in enumerate(bs)] + ([{"id": None, "bundle": i, "oob": a["oob"]}] if a.get("oob") else [])

    rows = [r for part in _pool(one, list(enumerate(plan))) for r in part]
    _dump(OUT / "lab" / run / "chk.json", {"n_bundles": len(plan), "rows": rows})
    return {"n_bundles": len(plan), "per_block": {r["id"]: r["issues"] for r in rows if r.get("id")},
            "bad_bundles": sorted({r["bundle"] for r in rows if r.get("id") and (r["status"] != "ok" or not r["parsed"])})}


# ---------- ⑦ 모음(S1 · S4) — 쪽 단위, 판 넷 같은 풀 ----------
def span(P: Any, label: str, key: str, units: Sequence[dict[str, Any]], secs: Sequence[tuple[int, int]], site: str) -> dict[str, Any]:
    """units = 쪽 단위 {id, page, ko, en}. 풀은 국문만 보므로 같은 쪽·같은 국문이면 판마다 같다."""
    from trlab import span_incons as SI
    pairs = L.span_pairs(units)
    pool = SI.pool_from(pairs, gloss(key), L.SPAN_MIN_OCC)
    bs = SI.batches(pool["items"], pool["pairs"])
    rd = OUT / "span" / label / "raw"

    def one(ib):
        i, b = ib
        text = SI.prompt(b)
        est = est_usd(LUNA, int((len(SI.INSTR) + len(text)) * 0.35), 60 * sum(len(p["terms"]) for p in b["pairs"]))
        d = send(P, LUNA, SI.INSTR, text, SI.SCHEMA, f"MA2/{site}/span/{label}/b{i}", est, rd / f"b{i}.json")
        ans = SI.parse_answer(d["raw"]) if d["status"] == "ok" else []
        return d["status"], SI.check(b, ans)

    res = _pool(one, list(enumerate(bs)))
    rows = [r for _, rs in res for r in rs]
    s1 = SI.judge_code(rows, "S1", pool["gloss_kr"])
    s2 = SI.judge_code(rows, "S2", pool["gloss_kr"])
    s4, judge_status = [], "no_s2"
    if s2:
        text, _ = SI.judge_prompt(s2, pool["pairs"])
        d = send(P, SOL, SI.JUDGE_INSTR, text, SI.JUDGE_SCHEMA, f"MA2/{site}/judge/{label}", est_usd(SOL, len(text) // 3 + 400, 1500),
                 rd / "judge.json")
        judge_status = d["status"]
        s4 = SI.apply_judge(s2, SI.parse_answer(d["raw"])) if d["status"] == "ok" else s2
    out = {"label": label, "n_terms": len(pool["terms"]), "n_items": len(pool["items"]), "n_batches": len(bs),
           "batch_status": [s for s, _ in res], "judge_status": judge_status,
           "kinds": dict(sorted(__import__("collections").Counter(r["kind"] for r in rows).items())),
           "S1": [f["kr"] for f in s1], "S2": [f["kr"] for f in s2], "S4": [f["kr"] for f in s4],
           "S1_split": L.split_inside_between(s1, rows, secs), "S4_split": L.split_inside_between(s4, rows, secs),
           "terms_sha16": L.sha16(json.dumps(pool["terms"], ensure_ascii=False)),
           "residual_pages": L.residual_incons_blocks(s4, rows)}
    _dump(OUT / "span" / label / "span.json", out)
    return out


# ---------- ⑧ 고침 · ⑨ ----------
def fix(P: Any, run: str, lab: dict[str, Any], key: str, chk: dict[str, Any], rq: dict[str, Any], ck: dict[str, Any],
        sp: dict[str, Any], site: str = "lab/fix") -> dict[str, Any]:
    g = gloss(key)
    by = {b["id"]: b for b in lab["blocks"]}
    reasons: dict[str, dict[str, list[Any]]] = {}
    for bid, c in chk.items():
        reasons.setdefault(bid, {})["term"] = list(c["term"])
    for bid, ss in rq["omit_confirmed"].items():
        reasons.setdefault(bid, {})["omit"] = list(ss)
    for bid, ns in rq["num_confirmed"].items():
        reasons.setdefault(bid, {})["num"] = list(ns)
    for page_id, terms in sp["residual_pages"].items():  # 쪽 단위 남은 불일치 → 그 쪽의 그 용어가 든 블록
        pg = int(page_id.split(":", 1)[1])
        for b in lab["blocks"]:
            if b["page"] == pg:
                ts = [t for t in terms if t in b["ko"]]
                if ts:
                    reasons.setdefault(b["id"], {})["incons"] = ts
    for bid, issues in ck["per_block"].items():
        if issues:
            reasons.setdefault(bid, {})["chk"] = list(issues)
    sel = L.fix_targets(lab["blocks"], lab["en"], reasons)
    est_all = sum(est_fix_block(by[i]["ko"]) for i in sel["targets"])
    _dump(OUT / "lab" / run / "fix-plan.json", {**sel, "est_usd": est_all, "stop_usd": FIX_STOP_USD,
                                                "reason_kinds": {i: sorted(k for k, v in reasons[i].items() if v) for i in sel["targets"]}})
    if est_all > FIX_STOP_USD + EPS:
        raise SystemExit(f"⑧ 고침 어림 {est_all:.4f} > {FIX_STOP_USD:.3f}(1.5배) — 멈춤(R1)")
    rd = OUT / "lab" / run / "raw"

    def one(bid):
        text = L.fix_input(by[bid], lab["en"][bid], g, reasons[bid])
        d = send(P, SOL, L.FIX_INSTR, text, L.ITEMS_SCHEMA, f"MA2/{site}/{run}/{bid}", est_fix_block(by[bid]["ko"]), rd / f"fix-{bid}.json")
        r = L.read_items(d.get("raw"), [bid])
        return bid, d["status"], r["en"].get(bid)

    res = _pool(one, sel["targets"])
    fixed = dict(lab["en"])
    done, failed = [], []
    for bid, st, new in res:
        if st == "ok" and new:
            fixed[bid] = new
            done.append(bid)
        else:
            failed.append({"id": bid, "status": st})
    unfixed = [f["id"] for f in failed] + sel["over_cap"]
    resid = L.residual(lab["blocks"], fixed, g, unfixed)
    out = {"targets": len(sel["targets"]), "fixed": len(done), "failed": failed, "over_cap": sel["over_cap"],
           "excluded": sel["excluded"], "residual": resid, "est_usd": est_all}
    _dump(OUT / "lab" / run / "fix.json", out)
    _dump(OUT / "lab" / run / "blocks-fixed.json", [{"id": b["id"], "page": b["page"], "en": fixed.get(b["id"])} for b in lab["blocks"]])
    return {**out, "en": fixed}


# ---------- 단위 ----------
def ko_pages(key: str) -> dict[int, str]:
    from .cli import ko_pages as kp
    return kp(key)


def lab_page_units(run: str, blocks: Sequence[dict[str, Any]], en: dict[str, str], kp: dict[int, str]) -> list[Any]:
    from ..common.units import Origin, Unit
    from . import MODULE, UNIT
    o = Origin("v2", LABFORM, run, CONVERTER)
    joined = L.page_join(blocks, en)
    pages = sorted({b["page"] for b in blocks})
    return [Unit(module=MODULE, unit=UNIT, key=f"page:{p}", group=str(p), origin=o,
                 payload={"ko": kp.get(p), "en": joined.get(p, ""), "granularity": "page", "variant": run, "remainingKorean": None},
                 status="ok" if joined.get(p) else "missing") for p in pages]


def lab_block_units(run: str, blocks: Sequence[dict[str, Any]], en: dict[str, str]) -> list[Any]:
    from ..common.units import Origin, Unit
    from . import MODULE, UNIT
    o = Origin("v2", LABFORM, run, CONVERTER)
    return [Unit(module=MODULE, unit=UNIT, key=f"{b['page']}:{b['id']}", group=str(b["page"]), origin=o,
                 payload={"ko": b["ko"], "en": en.get(b["id"]) or "", "granularity": "block", "variant": run, "remainingKorean": None},
                 status="ok" if en.get(b["id"]) else "missing") for b in blocks]


def v1_units(variant: str, kp: dict[int, str]) -> list[Any]:
    from .from_v1 import convert_result
    d = _load(OUT / "v1" / V1_DIR / "result.json")
    if d.get("status") not in ("done", "succeeded", "ok"):
        raise SystemExit(f"1차 결과 status {d.get('status')!r} — 멈춤(§5 2)")
    us = convert_result(d, run=f"v1-{variant}", variant=variant, page_offset=V1_OFFSET, ko_pages=kp)
    if len(us) != 20:
        raise SystemExit(f"1차 쪽 수 {len(us)} ≠ 20 — 멈춤(§5 2)")
    for u in us:
        if u.payload["result_page"] != int(u.group):
            raise SystemExit(f"result_page {u.payload['result_page']} ≠ 쪽 {u.group} — 멈춤(§5 2, offset 을 맞추지 않음)")
    return us


def page_dicts(units: Sequence[Any]) -> list[dict[str, Any]]:
    return [{"id": u.key, "page": int(u.group), "ko": u.payload.get("ko") or "", "en": u.payload.get("en") or ""} for u in units]


# ---------- 채점 몫(순수) ----------
def bleu_pages(units: Sequence[Any], refs: dict[int, dict[str, Any]]) -> dict[str, Any]:
    from .bleu import LOW_COVER, corpus_bleu
    us = [u for u in units if u.status == "ok" and int(u.group) in refs]
    b = corpus_bleu([u.payload["en"] for u in us], [refs[int(u.group)]["ref"] for u in us])
    low = sum(refs[int(u.group)]["coverage"]["value"] < LOW_COVER for u in us)
    return {"bleu": b["bleu"], "bp": b["bp"], "segments": b["segments"], "low_cover": low}


def omit_pages(units: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """쪽 글 단위 누락 규칙(쪽 국문 문장 수 > 쪽 영문 문장 수) — 정답 없는 코드 지표."""
    from trlab import omit_rule as OR
    from ..common.match import ratio
    n = sum(1 for u in units if OR.flagged(*OR.counts(u["ko"], u["en"])))
    return {"rate": ratio(n, len(units)), "n": n}


def num_pages(units: Sequence[dict[str, Any]]) -> dict[str, Any]:
    from ..common.match import ratio
    per = [len(L.missing_numbers(u["ko"], u["en"])) for u in units]
    nums = sum(len(L.ko_numbers(u["ko"])) for u in units)
    return {"rate": ratio(sum(per), nums), "pages_flagged": sum(1 for x in per if x), "missing": sum(per)}


def subject_dist(key: str, units: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """R2 주어 꼴(문단 문장) — we·name·generic·pronoun 비율(분모 = 주어 있는 문단 문장)."""
    from trlab import subject as S
    from ..common.match import ratio
    name_re, gen_re = S.names_for(key)
    us = [{"id": u["id"], "page": u["page"], "kind": u.get("kind", "paragraph"), "text": u["en"]} for u in units if u.get("en")]
    res, rows = S.analyze(us, name_re, gen_re, (), it_rule="R2")
    sj = [r["subj"] for r in rows if r["band"] == "paragraph" and r["subj"] != "none"]
    den = len(sj)
    return {"den": den, **{k: ratio(sj.count(k), den) for k in ("we", "name", "generic", "pronoun")},
            "rule_ok": ratio(res["rule_ok"], res["rule_n"])}


def better(lab_v: float | None, v1_v: float | None, higher_is_better: bool, same: float = 0.0) -> str:
    if lab_v is None or v1_v is None:
        return "잴 수 없음"
    d = lab_v - v1_v
    if abs(d) <= same + EPS:
        return "차이 없음"
    return "예" if (d > 0) == higher_is_better else "아니오"


def _val(x: Any) -> float | None:
    return x.get("value") if isinstance(x, dict) else x


def site_usd(prefix: str, rows: Sequence[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = lab_rows() if rows is None else rows
    rs = [r for r in rows if not r.get("dry_run") and r.get("tag", "").startswith(prefix)]
    return {"usd": sum(_cost(r) for r in rs), "calls": len(rs), "seconds": round(sum(float(r.get("seconds") or 0) for r in rs), 1),
            "out_tokens": sum(int(r.get("outputTokens") or 0) for r in rs)}


# ---------- 명령 ----------
def _official(key: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from ..common import gold as G
    from . import gold as TG
    p = (EVA_GOLD / f"{TG.OFFICIAL_ID}.json") if key == "sam" else (GOLD / f"translation-{key}-official.json")
    gs = G.load(p)
    return gs, TG.official_texts(gs)


def head_stop(sites: Sequence[str]) -> None:
    """명령 머리: 밴드 누적 + 그 명령 자리들의 plan 어림 > BAND_STOP 이면 시작 전에 멈춤(R6)."""
    plan = _load(OUT / "plan.json")
    est = sum(r["어림 USD"] or 0 for r in plan["rows"] if r["자리"] in sites)
    now = band_spent(lab_rows(), v1_rows())
    if now + est > BAND_STOP + EPS:
        raise SystemExit(f"밴드 누적 {now:.4f} + 어림 {est:.4f} > {BAND_STOP} — 멈춤(다음 잴 것 전)")


def cmd_lab(_: Any) -> None:
    """M-T1 랩 판 ③~⑧ + span 판 넷(1차 결과가 있어야 v1 둘을 잼)."""
    P = load_prompts()
    head_stop(["lab/tr", "lab/chk", "lab/reask", "lab/span", "lab/fix"])
    if not (OUT / "v1" / V1_DIR / "result.json").exists():
        raise SystemExit("1차 결과 먼저(span 판 넷 — B3)")
    key, lo, hi = RANGES["mt1"]
    run = "sam-35-54-toc-luna"
    lab = translate(P, run, key, lo, hi)
    ck_code = checks(lab, key)
    _dump(OUT / "lab" / run / "checks.json", ck_code)
    rq = reask(P, run, lab, ck_code)
    ck = checklist(P, run, lab, key)
    kp = ko_pages(key)
    raw_units = page_dicts(lab_page_units(run, lab["blocks"], lab["en"], kp))
    sp_raw = span(P, "lab-raw", key, raw_units, lab["secs"], "lab/span")
    fx = fix(P, run, lab, key, ck_code, rq, ck, sp_raw)
    span(P, "lab-fixed", key, page_dicts(lab_page_units(run + "-fixed", lab["blocks"], fx["en"], kp)), lab["secs"], "lab/span")
    if (OUT / "v1" / V1_DIR / "result.json").exists():
        for v in ("single", "single+autofix"):
            span(P, f"v1-{v}", key, page_dicts(v1_units(v, kp)), lab["secs"], "lab/span")
    print(json.dumps({"tr": site_usd("MA2/lab/tr/"), "reask": site_usd("MA2/lab/reask/"), "chk": site_usd("MA2/lab/chk/"),
                      "span": site_usd("MA2/lab/span/"), "fix": site_usd("MA2/lab/fix/"), "fixed": fx["fixed"],
                      "targets": fx["targets"]}, ensure_ascii=False))


def cmd_t6(_: Any) -> None:
    """M-T6 견줌 판 — ③ 만 gpt-4.1-mini(sam 35~44), 그 산출에 ④·⑥ 만."""
    P = load_prompts()
    key, lo, hi = RANGES["mt6"]
    head_stop(["t6"])
    run = "sam-35-44-toc-mini"
    lab = translate(P, run, key, lo, hi, model=MINI, site="t6")
    _dump(OUT / "lab" / run / "checks.json", checks(lab, key))
    checklist(P, run, lab, key, site="t6")
    print(json.dumps(site_usd("MA2/t6/"), ensure_ascii=False))


# ---------- 표 ----------
GLOSS_ID = "translation-glossary-{}"


def _gloss_set(key: str) -> dict[str, Any]:
    from ..common import gold as G
    from . import gold as TG
    gs = G.load(EVA_GOLD / f"{GLOSS_ID.format(key)}.json")
    TG.check_ref(gs, ROOT)
    return gs


def length_ratio(a: Sequence[Any], b: Sequence[Any]) -> dict[str, Any]:
    """쪽별 영문 글자 수 비 a/b(B2 — 1차 태그 글은 visual·table 단위가 더 들 수 있음). 중앙값·최소·최대."""
    import statistics
    la = {int(u.group): len(u.payload.get("en") or "") for u in a}
    lb = {int(u.group): len(u.payload.get("en") or "") for u in b}
    rs = [la[p] / lb[p] for p in sorted(la) if lb.get(p)]
    return {"median": statistics.median(rs) if rs else None, "min": min(rs) if rs else None, "max": max(rs) if rs else None, "n": len(rs)}


def quality_row(name: str, units: Sequence[Any], refs: dict[int, dict[str, Any]], gl: list[dict[str, Any]],
                off_id: str, gloss_id: str, gold_pages: Sequence[int]) -> list[dict[str, Any]]:
    from .score import missing, remaining_korean, term_violations
    b = bleu_pages(units, refs)
    tv = term_violations(units, gl)
    rk = remaining_korean(units)
    ms = missing(units, [f"page:{p}" for p in gold_pages])
    return [
        {"gold": off_id, "판": name, "지표": "BLEU 쪽" + (" *" if b["low_cover"] else ""), "값": b["bleu"], "분모": b["segments"]},
        {"gold": gloss_id, "판": name, "지표": "위반 엄격 rate", "값": tv["strict"], "분모": tv["units_with_term"]},
        {"gold": gloss_id, "판": name, "지표": "위반 관대 rate", "값": tv["lenient"], "분모": tv["units_with_term"]},
        {"gold": off_id, "판": name, "지표": "남은 한국어 rate", "값": rk["rate"], "분모": len(units)},
        {"gold": off_id, "판": name, "지표": "빠진 쪽 rate", "값": ms["rate"], "분모": len(gold_pages)},
    ]


def nogold_rows(name: str, key: str, pdicts: Sequence[dict[str, Any]], sp: dict[str, Any] | None,
                blocks_for_subject: Sequence[dict[str, Any]] | None, human: Any) -> list[dict[str, Any]]:
    from ..common import report as R
    om, nm = omit_pages(pdicts), num_pages(pdicts)
    sd = subject_dist(key, blocks_for_subject if blocks_for_subject is not None else pdicts)
    rows = [
        {"gold": R.NO_GOLD, "판": name, "지표": "누락 규칙 걸림 쪽 rate", "값": om["rate"], "분모": len(pdicts)},
        {"gold": R.NO_GOLD, "판": name, "지표": "숫자 걸림 rate(국문 수 분모)", "값": nm["rate"], "분모": nm["rate"]["den"]},
        {"gold": R.NO_GOLD, "판": name, "지표": "지칭 we rate", "값": sd["we"], "분모": sd["den"]},
        {"gold": R.NO_GOLD, "판": name, "지표": "지칭 이름 rate", "값": sd["name"], "분모": sd["den"]},
        {"gold": R.NO_GOLD, "판": name, "지표": "지칭 generic rate", "값": sd["generic"], "분모": sd["den"]},
        {"gold": R.NO_GOLD, "판": name, "지표": "지칭 pronoun rate", "값": sd["pronoun"], "분모": sd["den"]},
        {"gold": R.NO_GOLD, "판": name, "지표": "사람 확인", "값": human, "분모": None},
    ]
    if sp:
        from ..common.match import ratio
        rows += [
            {"gold": R.NO_GOLD, "판": name, "지표": "불일치 S1 rate(풀 용어)", "값": ratio(len(sp["S1"]), sp["n_terms"]), "분모": sp["n_terms"]},
            {"gold": R.NO_GOLD, "판": name, "지표": "불일치 S4 rate(풀 용어)", "값": ratio(len(sp["S4"]), sp["n_terms"]), "분모": sp["n_terms"]},
        ]
    return rows


def v1_human(variant_label: str = "single") -> str:
    d = _load(OUT / "v1" / V1_DIR / "result.json")
    v = next(x for x in d["variants"] if x["label"] == variant_label)
    ps = (v.get("autofix") or {}).get("pages") or []
    issues = sum(int(p.get("issueCount") or 0) for p in ps)
    return f"QA 이슈 든 쪽 {sum(1 for p in ps if p.get('issueCount'))}/20 · 이슈 {issues}"


def cmd_mt1(_: Any) -> None:
    from ..common import report as R
    from ..common.match import ratio
    from . import gold as TG
    from .bleu import block_segments, corpus_bleu, page_refs
    from trlab.common import load
    key, lo, hi = RANGES["mt1"]
    run = "sam-35-54-toc-luna"
    lab = load_lab(run, key, lo, hi)
    fixed = {r["id"]: r["en"] for r in _load(OUT / "lab" / run / "blocks-fixed.json") if r.get("en")}
    fx = _load(OUT / "lab" / run / "fix.json")
    kp = ko_pages(key)
    off_gs, off = _official(key)
    gl_set = _gloss_set(key)
    gl = gloss(key)
    kb = load(key, "ko")
    pages = list(range(lo, hi + 1))
    refs = page_refs(off, kb, pages)
    gold_pages = sorted({it["page"] for it in off_gs["items"] if lo <= it["page"] <= hi})
    sites = {"v1 single": v1_units("single", kp), "v1 single+autofix": v1_units("single+autofix", kp),
             "lab-raw": lab_page_units(run, lab["blocks"], lab["en"], kp), "lab-fixed": lab_page_units(run + "-fixed", lab["blocks"], fixed, kp)}
    spans = {"v1 single": "v1-single", "v1 single+autofix": "v1-single+autofix", "lab-raw": "lab-raw", "lab-fixed": "lab-fixed"}
    rows: list[dict[str, Any]] = []
    for name, us in sites.items():
        rows += quality_row(name, us, refs, gl, off_gs["setId"], gl_set["setId"], gold_pages)
    for name, en in (("lab-raw", lab["en"]), ("lab-fixed", fixed)):  # 블록 단위 BLEU(lab 안 비교만)
        segs = block_segments(lab_block_units(name, lab["blocks"], en), [p for p in off if lo <= p["page"] <= hi], kb)
        bb = corpus_bleu([s["hyp"] for s in segs], [s["ref"] for s in segs])
        rows.append({"gold": off_gs["setId"], "판": name, "지표": "BLEU 블록(lab 안 비교만)", "값": bb["bleu"], "분모": bb["segments"]})
    for name, us in sites.items():
        sp = _load(OUT / "span" / spans[name] / "span.json")
        pd = page_dicts(us)
        if name.startswith("lab"):
            en = lab["en"] if name == "lab-raw" else fixed
            subj = [{"id": b["id"], "page": b["page"], "kind": b["kind"], "en": en.get(b["id"]) or ""} for b in lab["blocks"]]
            human = (f"걸림 블록 {fx['residual']['blocks']}(고침 건너뜀 {len(fx['over_cap'])} · 실패 {len(fx['failed'])})"
                     if name == "lab-fixed" else f"고침 대상 {fx['targets']}")
        else:
            subj = None
            human = v1_human("single")
        rows += nogold_rows(name, key, pd, sp, subj, human)
    if True:  # 블록 단위 누락·숫자(lab 안)
        for name, en in (("lab-raw", lab["en"]), ("lab-fixed", fixed)):
            cs = [L.code_checks(b, en.get(b["id"]), gl) for b in lab["blocks"]]
            app = [c for c in cs if c["omit"].get("applies")]
            rows.append({"gold": R.NO_GOLD, "판": name, "지표": "누락 규칙 걸림 문단 블록 rate", "값": ratio(sum(bool(c["omit"]["flagged"]) for c in app), len(app)), "분모": len(app)})
            rows.append({"gold": R.NO_GOLD, "판": name, "지표": "숫자 걸림 블록 rate", "값": ratio(sum(bool(c["num"]) for c in cs), len(cs)), "분모": len(cs)})
    lr = length_ratio(sites["v1 single+autofix"], sites["lab-fixed"])
    rows.append({"gold": R.NO_GOLD, "판": "v1 +autofix / lab-fixed", "지표": "쪽별 영문 글자 수 비 중앙값", "값": lr["median"], "분모": lr["n"]})
    led = lab_rows()
    v1run = _load(OUT / "v1" / V1_DIR / "run.json")
    v1plan = _load(OUT / "v1" / V1_DIR / "plan.json")
    cost = {"v1": {"usd": v1run["usd"], "calls": v1run["providerCalls"], "seconds": v1run["seconds"], "plan_upper": v1plan["totalEstimateUsd"]}}
    for s in ("tr", "chk", "reask", "span", "fix"):
        cost[f"lab/{s}"] = site_usd(f"MA2/lab/{s}/", led)
    for s, c in cost.items():
        rows.append({"gold": R.NO_GOLD, "판": s, "지표": "실제 USD", "값": c["usd"], "분모": c["calls"]})
    # 「낫다」 줄 — lab-fixed 대 v1 single+autofix(둘 다 쪽 단위, B2)
    def pick(site, metric):
        return _val(next(r["값"] for r in rows if r["판"] == site and r["지표"].startswith(metric)))
    verdict = [
        {"gold": off_gs["setId"], "판": "lab-fixed 대 v1 +autofix", "지표": "낫다: BLEU 쪽",
         "값": better(pick("lab-fixed", "BLEU 쪽"), pick("v1 single+autofix", "BLEU 쪽"), True, BLEU_SAME), "분모": None},
        {"gold": gl_set["setId"], "판": "lab-fixed 대 v1 +autofix", "지표": "낫다: 위반 엄격",
         "값": better(pick("lab-fixed", "위반 엄격"), pick("v1 single+autofix", "위반 엄격"), False), "분모": None},
        {"gold": R.NO_GOLD, "판": "lab-fixed 대 v1 +autofix", "지표": "낫다: 불일치 S4",
         "값": better(pick("lab-fixed", "불일치 S4"), pick("v1 single+autofix", "불일치 S4"), False), "분모": None},
        {"gold": off_gs["setId"], "판": "lab-fixed 대 v1 +autofix", "지표": "낫다: 남은 한국어",
         "값": better(pick("lab-fixed", "남은 한국어"), pick("v1 single+autofix", "남은 한국어"), False), "분모": None},
    ]
    rows = verdict + rows
    notes = [
        "랩 꼴 = 2차 엔진 꼴의 시제품(2차 엔진 아님) — 단위 origin.engine v2, 판 translation.labform@1. 랩 번역 모델 gpt-6-luna(MARC-ENT-628(627 갈음)), 1차 = gpt-4.1-mini.",
        "v1 과 견주는 지표(BLEU 쪽·위반·남은 한국어·S1·S4)는 lab 도 쪽 이음 단위(쪽마다 단위 하나, key page:<쪽>). 블록 단위 수는 lab 안 비교만.",
        "BLEU 쪽 참조 = 그 쪽 본문 국문 블록에 짝지어진 공식 영문(evalrig bleu.page_refs), 손 구현 BLEU-4. 끝 * = 덮음 < 0.5 쪽 있음.",
        "용어집 = runner_made(TRX1, 사람 검토 none, partial) — 위반 rate 만, 정밀 없음. 공식판 짝 = official_publication(짝 맞춤 agent_sampled).",
        "1차에도 sam 용어집 48 을 넣음(감독 답 ②) — TRL2·TRL3 은 빈 용어집이었다(바닥선 다름).",
        "무효 조건: 위반 엄격·S4 는 ⑧ 고침 대상을 고른 검출기와 같은 셈이라 lab-fixed 의 이득은 일부 짜인 것 — 독립 지표는 BLEU 쪽·남은 한국어.",
        f"「낫다」: BLEU 차 < {BLEU_SAME} = 차이 없음(이 발주서의 문턱, 통계 시험 아님). NO_GOLD 줄은 품질 수와 섞지 않는다.",
    ]
    R.write(OUT / "mt1", "summary", rows, [off_gs, gl_set], title="M-T1 랩 꼴 끝까지 vs 1차 (sam 35~54)", notes=notes,
            extra={"cost": cost, "length_ratio": lr})
    print(json.dumps(verdict, ensure_ascii=False, default=str))


def cmd_mt6(_: Any) -> None:
    from ..common import report as R
    from ..common.match import ratio
    from .bleu import block_segments, corpus_bleu, page_refs
    from .score import remaining_korean, term_violations
    from trlab.common import load
    key, lo, hi = RANGES["mt6"]
    kp = ko_pages(key)
    off_gs, off = _official(key)
    gl_set = _gloss_set(key)
    gl = gloss(key)
    kb = load(key, "ko")
    refs = page_refs(off, kb, range(lo, hi + 1))
    luna = load_lab("sam-35-54-toc-luna", *RANGES["mt1"])
    lb = [b for b in luna["blocks"] if lo <= b["page"] <= hi]
    mini = load_lab("sam-35-44-toc-mini", key, lo, hi)
    luna_chk = {r["id"]: r for r in _load(OUT / "lab" / "sam-35-54-toc-luna" / "chk.json")["rows"] if r.get("id")}
    mini_chk = {r["id"]: r for r in _load(OUT / "lab" / "sam-35-44-toc-mini" / "chk.json")["rows"] if r.get("id")}
    led = lab_rows()
    rows = []
    for name, blocks, en, chk, trp, model in (("luna(주 판)", lb, luna["en"], luna_chk, "MA2/lab/tr/sam-35-54-toc-luna/", LUNA),
                                               ("mini(견줌 판)", mini["blocks"], mini["en"], mini_chk, "MA2/t6/sam-35-44-toc-mini/c", MINI)):
        pu = lab_page_units(name, blocks, en, kp)
        bu = lab_block_units(name, blocks, en)
        b = bleu_pages(pu, refs)
        segs = block_segments(bu, [p for p in off if lo <= p["page"] <= hi], kb)
        bb = corpus_bleu([s["hyp"] for s in segs], [s["ref"] for s in segs])
        tv = term_violations(pu, gl)
        rk = remaining_korean(pu)
        cs = [L.code_checks(x, en.get(x["id"]), gl) for x in blocks]
        app = [c for c in cs if c["omit"].get("applies")]
        flagged6 = sum(1 for x in blocks if (chk.get(x["id"]) or {}).get("issues"))
        n_missing = sum(1 for x in blocks if x["id"] not in en)
        tr_rows = [r for r in led if not r.get("dry_run") and r.get("tag", "").startswith(trp) and r.get("model") == model]
        if model == LUNA:  # 주 판은 M-T1 lab-raw 의 그 10쪽 — 그 호출들 중 35~44 구획(35 · 36~44) 몫
            calls_info = [c for c in _load(OUT / "lab" / "sam-35-54-toc-luna" / "tr.json")["calls"] if max(c["pages"]) <= hi]
            want = {f"c{c['call']}" for c in calls_info}
            tr_rows = [r for r in tr_rows if r["tag"].rsplit("/", 1)[1].split("-")[0] in want]
        usd = sum(_cost(r) for r in tr_rows)
        secs = [float(r.get("seconds") or 0) for r in tr_rows]
        parse_bad = sum(1 for c in (luna if model == LUNA else mini)["calls"] if not c.get("parsed", True))
        g, ng = off_gs["setId"], R.NO_GOLD
        rows += [
            {"gold": g, "판": name, "지표": "BLEU 쪽" + (" *" if b["low_cover"] else ""), "값": b["bleu"], "분모": b["segments"]},
            {"gold": g, "판": name, "지표": "BLEU 블록", "값": bb["bleu"], "분모": bb["segments"]},
            {"gold": gl_set["setId"], "판": name, "지표": "위반 엄격 rate", "값": tv["strict"], "분모": tv["units_with_term"]},
            {"gold": gl_set["setId"], "판": name, "지표": "위반 관대 rate", "값": tv["lenient"], "분모": tv["units_with_term"]},
            {"gold": g, "판": name, "지표": "남은 한국어 rate", "값": rk["rate"], "분모": len(pu)},
            {"gold": ng, "판": name, "지표": "누락 규칙 걸림 문단 블록 rate", "값": ratio(sum(bool(c["omit"]["flagged"]) for c in app), len(app)), "분모": len(app)},
            {"gold": ng, "판": name, "지표": "숫자 걸림 블록 rate", "값": ratio(sum(bool(c["num"]) for c in cs), len(cs)), "분모": len(cs)},
            {"gold": ng, "판": name, "지표": "⑥ 걸림 블록 rate", "값": ratio(flagged6, len(blocks)), "분모": len(blocks)},
            {"gold": ng, "판": name, "지표": "잘림(답 꼴 못 읽은 호출 · 빠진 블록)", "값": f"{parse_bad} · {n_missing}", "분모": len(blocks)},
            {"gold": ng, "판": name, "지표": "번역 실제 USD", "값": usd, "분모": len(tr_rows)},
            {"gold": ng, "판": name, "지표": "번역 출력 토큰(추론 몫 포함 — 따로 못 잼)", "값": sum(int(r.get("outputTokens") or 0) for r in tr_rows), "분모": len(tr_rows)},
            {"gold": ng, "판": name, "지표": "호출당 초 중앙값", "값": sorted(secs)[len(secs) // 2] if secs else None, "분모": len(secs)},
        ]

    def pick(site, metric):
        return _val(next(r["값"] for r in rows if r["판"].startswith(site) and r["지표"].startswith(metric)))
    verdict = [
        {"gold": off_gs["setId"], "판": "luna 대 mini", "지표": "낫다: BLEU 쪽", "값": better(pick("luna", "BLEU 쪽"), pick("mini", "BLEU 쪽"), True, BLEU_SAME), "분모": None},
        {"gold": gl_set["setId"], "판": "luna 대 mini", "지표": "낫다: 위반 엄격", "값": better(pick("luna", "위반 엄격"), pick("mini", "위반 엄격"), False), "분모": None},
        {"gold": R.NO_GOLD, "판": "luna 대 mini", "지표": "낫다: 번역 USD", "값": better(pick("luna", "번역 실제 USD"), pick("mini", "번역 실제 USD"), False), "분모": None},
    ]
    notes = ["주 판 luna = M-T1 lab-raw 의 35~44 쪽(다시 부르지 않음, 구획 35 · 36~44 의 호출). 견줌 판 mini = ③ 만 같은 지시·같은 구획(MARC-ENT-628(627 갈음)).",
             "semgraph.llm.respond 의 장부 줄에 추론 토큰 칸이 없어 추론 몫은 따로 못 잼 — 출력 토큰에 든다.",
             "용어집 runner_made · 공식판 짝 official_publication(agent_sampled)."]
    R.write(OUT / "mt6", "summary", verdict + rows, [off_gs, gl_set], title="M-T6 번역 모델 luna vs 4.1-mini (sam 35~44)", notes=notes)
    print(json.dumps(verdict, ensure_ascii=False, default=str))


def cmd_golden(_: Any) -> None:
    """① EVA M-T4 TRL3 A 쪽 BLEU 다시 셈 = out/eva/mt4 의 값 · ② v1 단위 20·result_page 35~54 · ③ 장부 손셈(lab/fix)."""
    from semgraph import llm
    from ..iro.cli import OUT as EVA_OUT
    from ..iro.cli import load_units
    from .bleu import page_refs
    from trlab.common import load
    res: dict[str, Any] = {}
    mt4 = _load(EVA_OUT / "mt4" / "summary.json")
    want = next(r["BLEU"] for r in mt4["rows"] if r["자리"].rstrip(" *") == "TRL3-A")
    us = [u for u in load_units(EVA_OUT / "translation" / "units" / "TRL3-A.json") if u.status == "ok"]
    _, off = _official("sam")
    refs = page_refs(off, load("sam", "ko"), [int(u.group) for u in us])
    got = bleu_pages(us, refs)["bleu"]
    res["g1"] = {"eva": want, "ma2": got, "same": abs(got - want) < 1e-9}
    if (OUT / "v1" / V1_DIR / "result.json").exists():
        vu = v1_units("single", ko_pages("sam"))
        res["g2"] = {"units": len(vu), "pages": [int(u.group) for u in vu], "result_pages": [u.payload["result_page"] for u in vu],
                     "same": len(vu) == 20 and [u.payload["result_page"] for u in vu] == list(range(35, 55))}
    fr = [r for r in lab_rows() if not r.get("dry_run") and r.get("tag", "").startswith("MA2/lab/fix/")]
    hand = sum(((int(r["inputTokens"]) - int(r.get("cachedTokens") or 0)) * 2.0 + int(r.get("cachedTokens") or 0) * 0.1
                + int(r["outputTokens"]) * 10.0) / 1e6 for r in fr)
    res["g3"] = {"calls": len(fr), "ledger": sum(_cost(r) for r in fr), "hand": hand, "sol_price": list(llm.PRICES[SOL]),
                 "same": abs(hand - sum(_cost(r) for r in fr)) < 1e-9}
    res["same"] = all(v["same"] for k, v in res.items() if isinstance(v, dict))
    _dump(OUT / "golden.json", res)
    print(json.dumps(res, ensure_ascii=False))
    if not res["same"]:
        raise SystemExit(1)


COMMANDS.update({"lab": cmd_lab, "t6": cmd_t6, "mt1": cmd_mt1, "mt6": cmd_mt6, "golden": cmd_golden})


# ======================================================================================================
# C3 — M-T2 목차 구획 vs 쪽 구획 · M-T3 지칭 설정 · 묶음 표
# ======================================================================================================
SMALL_N = 30  # M-T3 — 분모(문단 주어 문장) 30 미만이면 수 대신 「표본 적음」
SMALL = "표본 적음"
T2_RUNS = {("sam", "toc"): "sam-35-54-toc-luna", ("sam", "page"): "sam-35-54-page-luna",
           ("cos", "toc"): "cos-20-39-toc-luna", ("cos", "page"): "cos-20-39-page-luna"}
T2_SPAN = {("sam", "toc"): "lab-raw", ("sam", "page"): "t2-sam-page", ("cos", "toc"): "t2-cos-toc", ("cos", "page"): "t2-cos-page"}
T3_SITES = ("t3-sam", "t3-cos", "t3-kb")


def share_or_small(num: int, den: int) -> Any:
    """분모 SMALL_N 미만이면 「표본 적음」, 아니면 ratio 꼴."""
    from ..common.match import ratio
    return SMALL if den < SMALL_N else ratio(num, den)


def jitter(a: Any, b: Any) -> Any:
    """같은 판 두 회의 we 몫 차(절댓값). 어느 쪽이든 표본 적음이면 표본 적음."""
    if not (isinstance(a, dict) and isinstance(b, dict)):
        return SMALL
    return abs(a["value"] - b["value"])


def subject_rows(key: str, units: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """R2 문장 줄(trlab subject.analyze 그대로) — units {id, page, kind, en}."""
    from trlab import subject as S
    name_re, gen_re = S.names_for(key)
    us = [{"id": u["id"], "page": u["page"], "kind": u["kind"], "text": u["en"]} for u in units if u.get("en")]
    res, rows = S.analyze(us, name_re, gen_re, (), it_rule="R2")
    return rows


def subj_counts(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    sj = [r["subj"] for r in rows if r["band"] == "paragraph" and r["subj"] != "none"]
    return {"den": len(sj), **{k: sj.count(k) for k in ("we", "name", "generic", "pronoun")}}


def rule_ok(rows: Sequence[dict[str, Any]], variant: str) -> tuple[int, int]:
    """문단 블록(주어 문장 ≥ 2)마다 그 판의 지시를 지켰나 — we: 첫 주어 이름·generic, 뒤 전부 we(TRX1 규칙) ·
    third: 첫 주어 이름·generic, 뒤 we 0. → (지킴, 분모)."""
    by: dict[str, list[str]] = {}
    for r in rows:
        if r["band"] == "paragraph" and r["subj"] != "none":
            by.setdefault(r["unit"], []).append(r["subj"])
    ok = n = 0
    for sj in by.values():
        if len(sj) < 2:
            continue
        n += 1
        head = sj[0] in ("name", "generic")
        ok += head and (all(x == "we" for x in sj[1:]) if variant == "we" else all(x != "we" for x in sj[1:]))
    return ok, n


def official_subject_units(key: str, lo: int, hi: int) -> list[dict[str, Any]]:
    """같은 국문 쪽의 공식 EN(짝) — kind 는 짝 국문 블록 꼴(하나면 그것, 섞이면 mixed)."""
    from trlab.common import load
    _, off = _official(key)
    kinds = {b.id: b.kind for b in load(key, "ko")}
    out = []
    for p in off:
        if lo <= p["page"] <= hi:
            ks = {kinds.get(i, "paragraph") for i in p["ko_ids"]}
            out.append({"id": p["id"], "page": p["page"], "kind": ks.pop() if len(ks) == 1 else "mixed", "en": p["en"]})
    return out


def cmd_t2(_: Any) -> None:
    """M-T2 — sam 쪽 구획(toc = M-T1 lab-raw) · cos 목차·쪽 구획 + S1·S4."""
    P = load_prompts()
    head_stop(["t2"])
    for (key, mode), run in T2_RUNS.items():
        if run == "sam-35-54-toc-luna":
            continue
        lo, hi = RANGES[f"t2-{key}"][1:]
        lab = translate(P, run, key, lo, hi, mode=mode, site="t2")
        span(P, T2_SPAN[(key, mode)], key, page_dicts(lab_page_units(run, lab["blocks"], lab["en"], ko_pages(key))), lab["secs"], "t2")
    print(json.dumps(site_usd("MA2/t2/"), ensure_ascii=False))


def cmd_t3(_: Any) -> None:
    """M-T3 — 책 셋 × 5쪽 × 판 둘(we·third) × 2회, 번역만."""
    P = load_prompts()
    head_stop(["t3"])
    for site in T3_SITES:
        key, lo, hi = RANGES[site]
        for subj in ("we", "third"):
            for rep in (1, 2):
                translate(P, f"{site}-{subj}-r{rep}", key, lo, hi, subject=subj, site="t3")
    print(json.dumps(site_usd("MA2/t3/"), ensure_ascii=False))


def cmd_mt2(_: Any) -> None:
    from ..common import report as R
    from ..common.match import ratio
    from .bleu import page_refs
    from .score import term_violations
    from trlab.common import load
    rows: list[dict[str, Any]] = []
    sets = []
    led = lab_rows()
    for key in ("sam", "cos"):
        lo, hi = RANGES[f"t2-{key}"][1:]
        off_gs, off = _official(key)
        gl_set = _gloss_set(key)
        sets += [off_gs, gl_set]
        refs = page_refs(off, load(key, "ko"), range(lo, hi + 1))
        kp = ko_pages(key)
        sysp = set()
        for mode in ("toc", "page"):
            run = T2_RUNS[(key, mode)]
            lab = load_lab(run, key, lo, hi)
            sysp.add(L.sha16(L.system_prompt(load_prompts(), company_name(key), BOOKS[key]["subject"], "we")))
            sp = _load(OUT / "span" / T2_SPAN[(key, mode)] / "span.json")
            pu = lab_page_units(run, lab["blocks"], lab["en"], kp)
            tv = term_violations(pu, gloss(key))
            b = bleu_pages(pu, refs)
            name = f"{key} {mode}({lab['n_calls']}호출)"
            usd = site_usd(f"MA2/{'lab/tr' if run == 'sam-35-54-toc-luna' else 't2'}/{run}/", led)["usd"]  # 번역 몫만(span 은 아래 합 줄)
            ng, n = R.NO_GOLD, sp["n_terms"]
            rows += [
                {"gold": ng, "판": name, "지표": "S1 불일치 rate(풀 용어)", "값": ratio(len(sp["S1"]), n), "분모": n},
                {"gold": ng, "판": name, "지표": "S1 구획 안", "값": sp["S1_split"]["n_inside"], "분모": n},
                {"gold": ng, "판": name, "지표": "S1 구획 사이", "값": sp["S1_split"]["n_between"], "분모": n},
                {"gold": ng, "판": name, "지표": "S4 불일치 rate(풀 용어)", "값": ratio(len(sp["S4"]), n), "분모": n},
                {"gold": ng, "판": name, "지표": "S4 구획 안", "값": sp["S4_split"]["n_inside"], "분모": n},
                {"gold": ng, "판": name, "지표": "S4 구획 사이", "값": sp["S4_split"]["n_between"], "분모": n},
                {"gold": gl_set["setId"], "판": name, "지표": "위반 엄격 rate", "값": tv["strict"], "분모": tv["units_with_term"]},
                {"gold": off_gs["setId"], "판": name, "지표": "BLEU 쪽" + (" *" if b["low_cover"] else ""), "값": b["bleu"], "분모": b["segments"]},
                {"gold": ng, "판": name, "지표": "번역 실제 USD", "값": usd, "분모": None},
            ]
        if len(sysp) != 1:
            raise SystemExit(f"{key}: 두 판의 지시문 sha 가 다르다 — 멈춤")
    for lab_name in ("v1-single", "v1-single+autofix"):
        sp = _load(OUT / "span" / lab_name / "span.json")
        rows.append({"gold": R.NO_GOLD, "판": f"참고 sam {lab_name}(M-T1)", "지표": "S1 · S4 불일치",
                     "값": f"{len(sp['S1'])} · {len(sp['S4'])} / {sp['n_terms']}", "분모": sp["n_terms"]})
    rows.append({"gold": R.NO_GOLD, "판": "t2 자리 합", "지표": "실제 USD(번역 + span)", "값": site_usd("MA2/t2/", led)["usd"],
                 "분모": site_usd("MA2/t2/", led)["calls"]})
    notes = ["toc = 목차 구획(c3 항목 ∩ 범위, ≤ 12,000 국문 글자 — 쪽 경계로만), page = 쪽 하나 = 호출 하나. 지시·용어집·모델(luna)·지칭 같음 — 두 판의 지시문 sha 같음을 단언.",
             "sam toc = M-T1 lab-raw 재사용(그 span 도). S1·S4 는 쪽 단위 짝으로 같은 풀(풀은 국문만 봄).",
             "구획 안 = 같은 구획 안에서 키가 갈림, 구획 사이 = 구획마다는 하나뿐이고 구획끼리만 다름.",
             "cos 공식판 짝 = prework/gold-ma2/translation-cos-official(official_publication, agent_sampled). 용어집 runner_made."]
    R.write(OUT / "mt2", "summary", rows, sets, title="M-T2 목차 구획 vs 쪽 구획 (sam 35~54 · cos 20~39)", notes=notes)
    print("mt2", len(rows))


def cmd_mt3(_: Any) -> None:
    from ..common import report as R
    rows: list[dict[str, Any]] = []
    sets = []
    for site in T3_SITES:
        key, lo, hi = RANGES[site]
        off_gs, _ = _official(key)
        sets.append(off_gs)
        oc = subj_counts(subject_rows(key, official_subject_units(key, lo, hi)))
        o_we = share_or_small(oc["we"], oc["den"])
        rows.append({"gold": off_gs["setId"], "판": f"{key} 공식 EN", "지표": "we 몫", "값": o_we, "분모": oc["den"]})
        for k in ("name", "generic", "pronoun"):
            rows.append({"gold": off_gs["setId"], "판": f"{key} 공식 EN", "지표": f"{k} 몫", "값": share_or_small(oc[k], oc["den"]), "분모": oc["den"]})
        for subj in ("we", "third"):
            shares = []
            for rep in (1, 2):
                run = f"{site}-{subj}-r{rep}"
                lab = load_lab(run, key, lo, hi)
                units = [{"id": b["id"], "page": b["page"], "kind": b["kind"], "en": lab["en"].get(b["id"]) or ""} for b in lab["blocks"]]
                sr = subject_rows(key, units)
                c = subj_counts(sr)
                we = share_or_small(c["we"], c["den"])
                shares.append(we)
                ok, n = rule_ok(sr, subj)
                name = f"{key} {subj} r{rep}"
                diff = abs(we["value"] - o_we["value"]) if isinstance(we, dict) and isinstance(o_we, dict) else SMALL
                rows += [
                    {"gold": off_gs["setId"], "판": name, "지표": "we 몫", "값": we, "분모": c["den"]},
                    {"gold": off_gs["setId"], "판": name, "지표": "|we 몫 − 공식|", "값": diff, "분모": c["den"]},
                    {"gold": off_gs["setId"], "판": name, "지표": "이름+generic 몫", "값": share_or_small(c["name"] + c["generic"], c["den"]), "분모": c["den"]},
                    {"gold": off_gs["setId"], "판": name, "지표": "pronoun 몫", "값": share_or_small(c["pronoun"], c["den"]), "분모": c["den"]},
                    {"gold": R.NO_GOLD, "판": name, "지표": "rule_ok rate(판 지시 지킴)", "값": share_or_small(ok, n) if n else SMALL, "분모": n},
                ]
            rows.append({"gold": R.NO_GOLD, "판": f"{key} {subj}", "지표": "2회 흔들림 |we r1 − r2|", "값": jitter(*shares), "분모": None})
    led = lab_rows()
    rows.append({"gold": R.NO_GOLD, "판": "t3 자리 합", "지표": "실제 USD", "값": site_usd("MA2/t3/", led)["usd"], "분모": site_usd("MA2/t3/", led)["calls"]})
    notes = ["we = 1차 기본 지칭 덩어리 그대로, third = 「Subject pronoun…」 조각만 THIRD_SUBJECT(문단 첫 회사 주어 = 이름, 뒤 it/its, we/our 금지).",
             "셈 = trlab subject.analyze(it_rule R2) 문단 문장의 주어 꼴. 공식 EN = 같은 국문 쪽에 짝지어진 공식 영문(짝 국문 블록 꼴로 문단 거름).",
             f"분모(문단 주어 문장) {SMALL_N} 미만 = 「{SMALL}」(수 대신). rule_ok: we 판 = 첫 이름·뒤 전부 we, third 판 = 첫 이름·뒤 we 0(주어 문장 ≥ 2 문단).",
             "공식판 we 몫(TRX5 R2, 책 전체): sam 68.3% · cos 6.7% · kb 19.3% — 이 표는 그 5쪽만."]
    R.write(OUT / "mt3", "summary", rows, sets, title="M-T3 지칭 설정 (책 셋 × 5쪽 × 판 둘 × 2회)", notes=notes)
    print("mt3", len(rows))


def cmd_summary(_: Any) -> None:
    """묶음 표 — 측정 넷의 「낫다」·머리 줄 + 자리별 실제 USD + 골든. 앞 명령들의 산출을 읽음(호출 0)."""
    from ..common import report as R
    rows: list[dict[str, Any]] = []
    sets: dict[str, Any] = {}
    for m in ("mt1", "mt6", "mt2", "mt3"):
        d = _load(OUT / m / "summary.json")
        for g in d["gold"] if isinstance(d["gold"], list) else []:
            sets[g["setId"]] = g
        for r in d["rows"]:
            if r["지표"].startswith("낫다") or (m == "mt2" and r["지표"].startswith("S4 불일치")) or (m == "mt3" and r["지표"].startswith(("|we", "2회"))):
                rows.append({"gold": r["gold"], "측정": m.upper().replace("MT", "M-T"), "판": r["판"], "지표": r["지표"], "값": r["값"]})
    led = ledger_summary(lab_rows(), v1_rows())
    for s, v in led["sites"].items():
        rows.append({"gold": R.NO_GOLD, "측정": "비용", "판": s, "지표": f"실제 USD({v['calls']}호출)", "값": round(v["usd"], 6)})
    rows.append({"gold": R.NO_GOLD, "측정": "비용", "판": "밴드", "지표": "누적 USD", "값": round(led["band_usd"], 6)})
    g = _load(OUT / "golden.json")
    rows.append({"gold": R.NO_GOLD, "측정": "골든", "판": "①②③", "지표": "같음", "값": g["same"]})
    from ..common import gold as G
    gsets = [G.load(EVA_GOLD / f"{n}.json") if (EVA_GOLD / f"{n}.json").exists() else G.load(GOLD / f"{n}.json") for n in sorted(sets)]
    R.write(OUT, "summary", rows, gsets, title="MA2 묶음 — M-T1 · M-T6 · M-T2 · M-T3", columns=["gold", "측정", "판", "지표", "값"],
            notes=["자세한 표: out/ma2/{mt1,mt6,mt2,mt3}/summary.md. 랩 꼴 = 2차 엔진 꼴의 시제품(2차 엔진 아님).",
                   "단가: esg-app core/llm/data/models.json sha16 116d61d561c98bec(2026-11-09 다시 잼). 키: 마크스폰 키."])
    print("summary", len(rows))


COMMANDS.update({"t2": cmd_t2, "t3": cmd_t3, "mt2": cmd_mt2, "mt3": cmd_mt3, "summary": cmd_summary})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evalrig.translation.measure_ma2")
    ap.add_argument("cmd", choices=sorted(COMMANDS))
    a = ap.parse_args(argv)
    COMMANDS[a.cmd](a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
