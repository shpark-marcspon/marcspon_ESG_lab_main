"""TRX4 K2 — 절 시작 꼴 배우기. AI 0. 규칙·상수는 kb 두 판으로 정하고 얼림(반쯤 본).

confident(items, dp_res, cand, blocks): DP 고름 중 fuzz.ratio(항목 글, 블록 lang-part) ≥ CONF 이고 후보 행에 든 것의 idx.
features(block, attrs, cand, divs): xr round(size_max/base, 1) · yb int(위끝/ph × 10) · xl int(왼끝/pw × 10) ·
  bold bold_share ≥ 0.6 · nc 그 쪽 후보 수 묶음(1 / 2-3 / 4+) · div 간지 쪽 여부.
learn(feats): 특징마다 값의 빈도(분모 = 확실 항목 수). score(f, model) = 특징 여섯의 빈도 평균 ∈ [0, 1].
K2b 되풀이: 같은 글(fuzz ≥ 90)·같은 xr 블록이 바로 앞 쪽에 있으면 score −= REP_PEN.
DP bonus = K2_W × (score − 0.5). AI 후보 줄 끝 열 shape(0.00~1.00).

명령: python -m trlab.sec_shape <판> → OUT4/shape/<판>.json
"""

import json
import re
import sys
from collections import Counter

from rapidfuzz import fuzz

CONF = 90
K2_W = 0.4
REP_PEN = 0.3
REP_SIM = 90
FEATS = ("xr", "yb", "xl", "bold", "nc", "div")


def _strip(t):
    return re.sub(r"^\[[^\]]*\]\s*", "", t or "").lower()


def confident(items, dp_res, cand, blocks):
    from trlab.toc import _lang_part
    ids = {b.id: b for b in blocks}
    out = []
    for i, (it, d) in enumerate(zip(items, dp_res)):
        b = ids.get(d.get("block") or "")
        if b is None or b.id not in cand["rows"]:
            continue
        t = _strip(it["text"])
        if fuzz.ratio(t, _lang_part(t, b.text).lower()) >= CONF:
            out.append(i)
    return out


def nc_bucket(n):
    return "1" if n <= 1 else "2-3" if n <= 3 else "4+"


def features(b, attrs, cand, divs):
    a = attrs["blocks"].get(b.id, {})
    base = attrs["base"]["size"] or 1.0
    size = a.get("size_max") or b.font_size or 0.0
    n = len(cand["pages"].get(str(b.page), {}).get("cands", []))
    return {"xr": round(size / base, 1), "yb": int(b.bbox[1] / b.ph * 10), "xl": int(b.bbox[0] / b.pw * 10),
            "bold": (a.get("bold_share") or 0) >= 0.6, "nc": nc_bucket(n), "div": b.page in divs}


def learn(feats):
    """feats = 확실 항목 시작 블록의 특징 목록 → {특징: {값: 빈도}}"""
    n = len(feats)
    model = {}
    for k in FEATS:
        c = Counter(f[k] for f in feats)
        model[k] = {v: m / n for v, m in c.items()} if n else {}
    return model


def score(f, model):
    if not model or not any(model.values()):
        return 0.5
    return sum(model[k].get(f[k], 0.0) for k in FEATS) / len(FEATS)


def repeated(b, by_page, attrs, base):
    """같은 글·같은 xr 블록이 바로 앞 쪽에 있나."""
    a = attrs["blocks"].get(b.id, {})
    xr = round((a.get("size_max") or b.font_size or 0) / base, 1)
    for o in by_page.get(b.page - 1, []):
        ao = attrs["blocks"].get(o.id, {})
        if ao.get("status") != "ok":
            continue
        if round((ao.get("size_max") or 0) / base, 1) == xr and fuzz.ratio(b.text.strip().lower(), o.text.strip().lower()) >= REP_SIM:
            return True
    return False


def shape_scores(blocks, attrs, cand, model):
    """→ {후보 블록 id: score(K2b 깎음 포함)}, {id: 깎였나}"""
    divs = {int(p) for p, v in cand["pages"].items() if v["divider"]}
    base = attrs["base"]["size"] or 1.0
    ids = {b.id: b for b in blocks}
    by_page = {}
    for b in blocks:
        by_page.setdefault(b.page, []).append(b)
    out, rep = {}, {}
    for bid in cand["rows"]:
        b = ids.get(bid)
        if b is None:
            continue
        s = score(features(b, attrs, cand, divs), model)
        rep[bid] = repeated(b, by_page, attrs, base)
        if rep[bid]:
            s -= REP_PEN
        out[bid] = round(max(0.0, s), 4)
    return out, rep


def bonus_of(scores, n_items, w=K2_W):
    one = {bid: w * (s - 0.5) for bid, s in scores.items()}
    return [one for _ in range(n_items)]


def book_shape(P, conf):
    """prep4 용 — 확실 항목 → 꼴 → 후보 점수 → bonus. → {confident, model, scores, rep, bonus}"""
    ids = {b.id: b for b in P["blocks"]}
    divs = {int(p) for p, v in P["cand"]["pages"].items() if v["divider"]}
    base_dp = P.get("base_win") or {}
    from trlab import order_win as W
    dp_res = (base_dp or W.plan(P["items"], P["pm"], P["cand"], P["blocks"], P["toc_pages"]))["dp"]
    feats = [features(ids[dp_res[i]["block"]], P["attrs"], P["cand"], divs) for i in conf]
    model = learn(feats)
    scores, rep = shape_scores(P["blocks"], P["attrs"], P["cand"], model)
    return {"confident": conf, "n_items": len(P["items"]),
            "model": {k: {str(v): round(f, 3) for v, f in m.items()} for k, m in model.items()},
            "scores": scores, "rep": sorted(b for b, r in rep.items() if r), "bonus": bonus_of(scores, len(P["items"]))}


def main(argv):
    from trlab import tx4_pick as X
    name = argv[0]
    key, lang, bl = X.parse_name(name)
    P = X.prep4(key, lang, bl, ["K2"])
    sh = P["shape"]
    out = {k: v for k, v in sh.items() if k != "bonus"}
    (X.OUT4 / "shape").mkdir(parents=True, exist_ok=True)
    json.dump(out, open(X.OUT4 / "shape" / f"{name}.json", "w"), ensure_ascii=False, indent=1, sort_keys=True)
    print(name, "확실", len(sh["confident"]), "/", sh["n_items"], "되풀이 깎임", len(sh["rep"]))


if __name__ == "__main__":
    main(sys.argv[1:])
