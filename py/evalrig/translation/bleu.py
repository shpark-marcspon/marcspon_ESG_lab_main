"""M-T4 BLEU 바닥선 — 손 구현 corpus BLEU-4(새 의존 0, 발주서 EVA §2 (6)).

sacrebleu 기본 꼴을 따른다: 토큰 `13a` · 1~4-gram 정밀(참조 하나, 잘라 셈) 기하 평균 · 짧음 벌점 · **다듬기 없음**
(n-gram 맞음이 하나라도 0 이면 BLEU 0 — sacrebleu 기본 `exp` 다듬기와 다름) · 대소문자 그대로 · 0~100.
sacrebleu 와 같은 값임은 재지 않았다(발주서 §7 4 — 일회 대조는 새 외부 도구라 사용자 몫).

짝(구획): TRL 쪽(국문 쪽 40~45) — 후보 = 그 쪽 번역 글, 참조 = 그 쪽 **본문** 국문 블록에 짝지어진 공식 영문(블록 차례로 이음).
trpdf 블록 — 공식 짝의 국문 블록이 trpdf 단위에 모두 있고 글이 같을 때만 그 짝 하나가 구획(후보 = 그 단위들 번역을 이음).
덮음 = 공식 짝이 있는 국문 글자 / 그 쪽 국문 글자(덮음 < 0.5 쪽은 표에 넣되 별표).
"""
from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Iterable, Sequence

MAX_N = 4

_13A = [
    (re.compile(r"([\{-\~\[-\` -\&\(-\+\:-\@\/])"), r" \1 "),
    (re.compile(r"([^0-9])([\.,])"), r"\1 \2 "),
    (re.compile(r"([\.,])([^0-9])"), r" \1 \2"),
    (re.compile(r"([0-9])(-)"), r"\1 \2 "),
]


def tok13a(line: str) -> list[str]:
    """sacrebleu `13a`(mteval-v13a) 토큰 나눔."""
    line = line.replace("<skipped>", "").replace("-\n", "").replace("\n", " ")
    if "&" in line:
        line = line.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    line = f" {line} "
    for rx, rep in _13A:
        line = rx.sub(rep, line)
    return line.split()


def ngrams(toks: Sequence[str], n: int) -> Counter:
    return Counter(tuple(toks[i: i + n]) for i in range(len(toks) - n + 1))


def stats(hyp: str, ref: str) -> list[int]:
    """[후보 길이, 참조 길이, 맞음1, 전체1, …, 맞음4, 전체4]."""
    h, r = tok13a(hyp), tok13a(ref)
    out = [len(h), len(r)]
    for n in range(1, MAX_N + 1):
        hc, rc = ngrams(h, n), ngrams(r, n)
        out += [sum(min(c, rc[g]) for g, c in hc.items()), max(len(h) - n + 1, 0)]
    return out


def corpus_bleu(hyps: Iterable[str], refs: Iterable[str]) -> dict:
    """→ `{bleu(0~100), bp, precisions[4](%), sys_len, ref_len, segments}`. 구획 0 이면 bleu None."""
    hs, rs = list(hyps), list(refs)
    if len(hs) != len(rs):
        raise ValueError("후보·참조 수가 다름")
    if not hs:
        return {"bleu": None, "bp": None, "precisions": None, "sys_len": 0, "ref_len": 0, "segments": 0}
    tot = [0] * (2 + 2 * MAX_N)
    for h, r in zip(hs, rs):
        tot = [a + b for a, b in zip(tot, stats(h, r))]
    c, rl = tot[0], tot[1]
    precs = [(tot[2 + 2 * k] / tot[3 + 2 * k]) if tot[3 + 2 * k] else 0.0 for k in range(MAX_N)]
    if c == 0 or min(precs) == 0:
        bleu = 0.0
        bp = 0.0 if c == 0 else (1.0 if c > rl else math.exp(1 - rl / c))
    else:
        bp = 1.0 if c > rl else math.exp(1 - rl / c)
        bleu = 100 * bp * math.exp(sum(math.log(p) for p in precs) / MAX_N)
    return {"bleu": bleu, "bp": bp, "precisions": [100 * p for p in precs], "sys_len": c, "ref_len": rl, "segments": len(hs)}


# ---------- 구획 짓기 ----------
def page_refs(pairs: Sequence[dict], ko_blocks: Sequence, pages: Iterable[int]) -> dict[int, dict]:
    """쪽 → `{ref(공식 영문 이음), coverage(ratio 꼴), n_pairs}`. 본문(region body) 국문 블록만 센다.

    `pairs` = `gold.official_texts` 꼴(`ko_ids`·`en`), `ko_blocks` = trlab Block 목록.
    """
    from ..common.match import ratio

    body = {b.id: b for b in ko_blocks if b.region == "body"}
    order = {b.id: (b.page, b.order) for b in ko_blocks}
    out = {}
    for pg in pages:
        pb = [b for b in body.values() if b.page == pg]
        ps = [p for p in pairs if p["page"] == pg and all(i in body for i in p["ko_ids"])]
        ps.sort(key=lambda p: min(order[i] for i in p["ko_ids"]))
        covered = {i for p in ps for i in p["ko_ids"]}
        num = sum(len(b.text) for b in pb if b.id in covered)
        out[pg] = {"ref": " ".join(p["en"] for p in ps), "coverage": ratio(num, sum(len(b.text) for b in pb)), "n_pairs": len(ps)}
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def block_segments(engine_units: Sequence, pairs: Sequence[dict], ko_blocks: Sequence) -> list[dict]:
    """trpdf 블록 구획 — 공식 짝의 국문 블록이 모두 엔진 단위에 있고 글이 같을 때 그 짝 하나 = 구획.

    `engine_units` = `convert_pairs(engine_pairs(model))` 단위(key `<쪽>:<블록 id>`). 돌려줌 `[{key, page, hyp, ref, ko}]`.
    """
    ko = {b.id: b for b in ko_blocks}
    eu = {u.key.split(":", 1)[1]: u for u in engine_units}
    segs = []
    for p in pairs:
        ids = p["ko_ids"]
        if not all(i in eu and i in ko and _norm(eu[i].payload["ko"]) == _norm(ko[i].text) for i in ids):
            continue
        if not all(ko[i].region == "body" for i in ids):
            continue
        segs.append({"key": p["id"], "page": p["page"], "hyp": " ".join(eu[i].payload["en"] for i in ids),
                     "ref": p["en"], "ko": p["ko"]})
    return segs


TRPDF_PAGES = (11, 30, 79)
LOW_COVER = 0.5


def run_mt4(out_dir: Path) -> dict:
    """M-T4 표 → `out_dir/mt4/summary.{json,md}`. 행 = 자리 × 변형 + 바닥 둘(공식 대 자기 · 국문 그대로)."""
    import statistics

    from trlab.common import load
    from trlab.glossary_check import engine_pairs, load_gloss

    from ..common import gold as G
    from ..common import report as R
    from ..iro.cli import GOLD, OUT, load_units
    from . import gold as TG
    from .cli import TRL, TRPDF_MODELS, VARIANTS, site_name
    from .from_v1 import convert_pairs
    from .score import term_violations

    off_gs = G.load(GOLD / f"{TG.OFFICIAL_ID}.json")
    off = TG.official_texts(off_gs)
    kb = load("sam", "ko")
    gloss = load_gloss("sam")["items"]
    rows = []

    def row(site, hyps, refs, covs, units_for_terms):
        b = corpus_bleu(hyps, refs)
        cv = [c["value"] for c in covs if "value" in c]
        low = sum(v < LOW_COVER for v in cv)
        tv = term_violations(units_for_terms, gloss) if units_for_terms is not None else None
        return {"gold": off_gs["setId"], "자리": site + (" *" if low else ""), "BLEU": b["bleu"], "BP": b["bp"],
                "구획": b["segments"], "덮음 중앙": statistics.median(cv) if cv else None, "덮음<0.5 구획": low,
                "용어 위반 엄격 rate": tv["strict"] if tv else None}

    trl_pages = None
    for r in TRL:
        for v in VARIANTS:
            us = load_units(OUT / "translation" / "units" / f"{site_name(r, v)}.json")
            us = [u for u in us if u.status == "ok"]
            pages = [int(u.group) for u in us]
            trl_pages = trl_pages or pages
            refs = page_refs(off, kb, pages)
            rows.append(row(site_name(r, v), [u.payload["en"] for u in us], [refs[p]["ref"] for p in pages],
                            [refs[p]["coverage"] for p in pages], us))
    refs = page_refs(off, kb, trl_pages)
    pr = [refs[p]["ref"] for p in trl_pages]
    rows.append(row("바닥: 공식 대 자기(쪽 40~45)", pr, pr, [refs[p]["coverage"] for p in trl_pages], None))
    ko_us = load_units(OUT / "translation" / "units" / f"{site_name(TRL[0], VARIANTS[0])}.json")
    rows.append(row("바닥: 국문 그대로(쪽 40~45)", [u.payload["ko"] or "" for u in ko_us], pr,
                    [refs[p]["coverage"] for p in trl_pages], None))
    tp = [p for p in off if p["page"] in TRPDF_PAGES]
    prefs = page_refs(off, kb, TRPDF_PAGES)
    for m in TRPDF_MODELS:
        eu = convert_pairs(engine_pairs(m), run=f"trpdf-{m}")
        segs = block_segments(eu, tp, kb)
        seg_units = [u for u in eu if any(u.key.split(":", 1)[1] in s["key"].split("+") for s in segs)]
        rows.append(row(f"trpdf-{m}(블록)", [s["hyp"] for s in segs], [s["ref"] for s in segs],
                        [prefs[s["page"]]["coverage"] for s in segs], seg_units))
    notes = [
        "손 구현 corpus BLEU-4(13a 토큰, 다듬기 없음, 대소문자 그대로) — sacrebleu 와 같은 값임은 재지 않음(새 외부 도구라 사용자 몫).",
        "TRL 구획 = 국문 쪽(결과 쪽 i → 39 + i), 참조 = 그 쪽 본문 국문 블록에 짝지어진 공식 영문(블록 차례). 덮음 = 공식 짝 있는 본문 국문 글자 / 본문 국문 글자.",
        "trpdf 구획 = 공식 짝 하나(그 국문 블록이 trpdf 단위에 모두 있고 글이 같은 짝만 — 파싱이 달라 적음), 덮음은 그 쪽의 값.",
        f"자리 끝 * = 덮음 < {LOW_COVER} 구획이 있음.",
        "용어 위반 엄격 = 그 구획 단위 중 용어가 든 단위에서 엄격 표기가 없는 단위.",
    ]
    R.write(out_dir / "mt4", "summary", rows, [off_gs], title="M-T4 번역 BLEU 바닥선", notes=notes)
    vals = [r["BLEU"] for r in rows if r["BLEU"] is not None and not r["자리"].startswith("바닥")]
    return {"rows": rows, "range": [min(vals), max(vals)] if vals else None}
