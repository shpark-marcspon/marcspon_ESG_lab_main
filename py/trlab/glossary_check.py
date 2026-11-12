"""T-b 검사기 둘(AI 0).

1. 용어집 위반: 짝(국문 글, 영문 글)에서 KR 용어가 국문에 있고(`in`, 1차 엔진 glossary.py:39-43 와 같음) 기대 EN 이 영문에 없으면 걸림.
   EN 있음 판정 두 판 — 엄격 = glossary.py:46-54 그대로(공백 접고 소문자 `in`),
   관대 = 엄격 + 복수 -s/-es · 소유격 's · 하이픈/공백 · 앞 the · 줄 끝 끊김(「Manage- ment」).
   관대 걸림 ⊂ 엄격 걸림.
2. 불일치(용어집 없이): KR 용어(용어집 KR + 끝말 정규식으로 뽑은 명칭, 짝 3개 이상)마다 짝 영문에서 대응 표현을
   공기 Dice 최고 n-gram 으로 고르고(관대 정규화, 서로 품는 표현은 하나로), 표현이 둘 이상이면 걸림.

자리: official(공식 영문판, 정답 근거 블록을 뺌 — 발주서 §7-1) · engine(1차 엔진 산출 out/trpdf/res, sam) · glossary-in((iii) 산출).
명령: python -m trlab.glossary_check <키> [--site official|engine]
"""

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from trlab.common import GOLD, OUT, ROOT

WS = re.compile(r"\s+")


# ---------- EN 있음 판정 ----------
def normalize_space(s):
    return WS.sub(" ", s or "").strip()


def strict_present(expected, text):
    """glossary.py target_term_present 와 같다."""
    e = normalize_space(expected).lower()
    return (not e) or e in normalize_space(text).lower()


def _lenient_norm(s):
    s = (s or "").replace("’", "'").replace("‘", "'").replace("‧", " ").replace("·", " ")
    s = re.sub(r"(\w)-\s+(\w)", r"\1\2", s)  # 줄 끝 끊김
    s = s.lower().replace("-", " ").replace("/", " ")
    s = re.sub(r"'s\b", "", s)
    return normalize_space(s)


def lenient_present(expected, text):
    if strict_present(expected, text):
        return True
    e = _lenient_norm(expected)
    e = re.sub(r"^the ", "", e)
    if not e:
        return True
    toks = e.split(" ")
    pat = r"\b" + r"\s*".join(re.escape(t) + r"(?:s|es)?" for t in toks) + r"\b"
    return re.search(pat, _lenient_norm(text)) is not None


# ---------- 자리별 짝 ----------
def official_pairs(key, exclude_ev=True):
    from trlab.glossary_cands import pair_texts
    ev = set()
    if exclude_ev:
        for it in load_gloss(key)["items"]:
            for e in it["evidence"]:
                ev.update(e["ko"])
                ev.update(e["en"])
    out, dropped = [], 0
    for p, kt, et, _, _ in pair_texts(key):
        if set(p["ko"]) & ev or set(p["en"]) & ev:
            dropped += 1
            continue
        out.append({"id": "+".join(p["ko"]), "page": p["page"], "ko": kt, "en": et})
    return out, dropped


def engine_pairs(model, res_dir=None):
    res = Path(res_dir or ROOT / "esg-lab" / "out" / "trpdf" / "res") / model
    out = []
    for f in sorted(res.glob("p*.json")):
        d = json.load(open(f))
        tr = d["translations"]
        for u in d["units"]:
            t = tr.get(u["id"])
            if not t:
                continue
            out.append({"id": f"{d['page']}:{u['id']}", "page": d["page"], "ko": u.get("text", ""),
                        "en": " ".join(t) if isinstance(t, list) else str(t)})
    return out


def load_gloss(key):
    return json.load(open(GOLD / "glossary" / f"{key}.json"))


# ---------- 1. 위반 ----------
def violations(pairs, gloss):
    hits = []
    occ = Counter()
    ok_s, ok_l = Counter(), Counter()
    for p in pairs:
        for it in gloss:
            if it["kr"] not in p["ko"]:
                continue
            occ[it["kr"]] += 1
            s, l = strict_present(it["en"], p["en"]), lenient_present(it["en"], p["en"])
            ok_s[it["kr"]] += s
            ok_l[it["kr"]] += l
            if not s:
                hits.append({"pair": p["id"], "page": p["page"], "kr": it["kr"], "en": it["en"], "lenient_hit": not l})
    return hits, occ, ok_s, ok_l


# ---------- 2. 불일치 ----------
def _ngrams(text, nmax=6):
    from trlab.glossary_cands import ngrams
    return ngrams(text, nmax)


def inconsistency(pairs, gloss, min_occ=3, min_dice=0.2):
    from trlab.glossary_cands import kr_terms
    terms = Counter()
    for p in pairs:
        terms.update(kr_terms(p["ko"]))
    kr = {t for t, c in terms.items() if c >= min_occ} | {it["kr"] for it in gloss}
    # 겹치는 KR(「경영위원회」⊂「지속가능경영위원회」): 긴 것이 그 자리에 있으면 짧은 것은 그 짝에서 세지 않는다
    kr_sorted = sorted(kr, key=len, reverse=True)
    df_e = Counter()
    for p in pairs:
        df_e.update(_ngrams(p["en"]))
    occ_pairs = defaultdict(list)
    for i, p in enumerate(pairs):
        masked = p["ko"]
        for t in kr_sorted:
            if t in masked:
                occ_pairs[t].append(i)
                masked = masked.replace(t, "□" * len(t))
    flags, n_terms = [], 0
    for t, idx in occ_pairs.items():
        if len(idx) < min_occ:
            continue
        n_terms += 1
        co = Counter()
        for i in idx:
            co.update(_ngrams(pairs[i]["en"]))
        dice = {e: 2 * c / (len(idx) + df_e[e]) for e, c in co.items()}
        rend = Counter()
        where = defaultdict(list)
        for i in idx:
            cands = [e for e in _ngrams(pairs[i]["en"]) if dice.get(e, 0) >= min_dice]
            if not cands:
                continue
            e = max(cands, key=lambda e: (round(dice[e], 2), len(e)))
            k = _lenient_norm(e)
            rend[k] += 1
            where[k].append(pairs[i]["page"])
        # 서로 품는 표현은 하나로(긴 쪽)
        keys = sorted(rend, key=len, reverse=True)
        merged = Counter()
        for k in keys:
            host = next((m for m in merged if re.search(r"\b" + re.escape(k) + r"\b", m)), None)
            merged[host or k] += rend[k]
            if host:
                where[host] += where[k]
        if len(merged) >= 2:
            flags.append({"kr": t, "occ": len(idx), "renderings": [[k, c, sorted(set(where[k]))[:8]] for k, c in merged.most_common()],
                          "in_glossary": t in {it["kr"] for it in gloss}})
    return flags, n_terms


def run(key, site, model=None):
    gloss = load_gloss(key)["items"]
    dropped = 0
    if site == "official":
        pairs, dropped = official_pairs(key)
    else:
        pairs = engine_pairs(model)
    hits, occ, ok_s, ok_l = violations(pairs, gloss)
    flags, n_terms = inconsistency(pairs, gloss)
    n = len(pairs)
    tot = sum(occ.values())
    res = {"book": key, "site": site, "model": model, "pairs": n, "dropped_evidence_pairs": dropped,
           "violation": {"strict_hits": len(hits), "lenient_hits": sum(h["lenient_hit"] for h in hits),
                         "per100_strict": round(100 * len(hits) / max(1, n), 2),
                         "per100_lenient": round(100 * sum(h["lenient_hit"] for h in hits) / max(1, n), 2),
                         "term_occurrences": tot,
                         "consistency_strict": round(sum(ok_s.values()) / max(1, tot), 3),
                         "consistency_lenient": round(sum(ok_l.values()) / max(1, tot), 3),
                         "terms_seen": len(occ)},
           "inconsistency": {"flags": len(flags), "terms_ge3": n_terms, "rate": round(len(flags) / max(1, n_terms), 3),
                             "per100": round(100 * len(flags) / max(1, n), 2)},
           "hits": hits, "flags": flags,
           "per_term": {k: {"occ": occ[k], "strict_ok": ok_s[k], "lenient_ok": ok_l[k]} for k in occ}}
    return res


def main(argv):
    key = argv[0]
    site = argv[argv.index("--site") + 1] if "--site" in argv else "official"
    models = ["gpt-6-luna", "gpt-6.1-sol", "engine-4.1-mini"] if site == "engine" else [None]
    if "--model" in argv:
        models = [argv[argv.index("--model") + 1]]
    res_dir = argv[argv.index("--res") + 1] if "--res" in argv else None
    (OUT / "glossary").mkdir(parents=True, exist_ok=True)
    for m in models:
        if res_dir:
            global engine_pairs
            _ep = engine_pairs
            engine_pairs = lambda model, _r=res_dir, _f=_ep: _f(model, _r)  # noqa: E731
        r = run(key, site, m)
        tag = f"{key}-{site}" + (f"-{m}" if m else "") + (f"-{Path(res_dir).name}" if res_dir else "")
        json.dump(r, open(OUT / "glossary" / f"check-{tag}.json", "w"), ensure_ascii=False, indent=1)
        print(tag, json.dumps({k: r[k] for k in ("pairs", "dropped_evidence_pairs", "violation", "inconsistency")}, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
