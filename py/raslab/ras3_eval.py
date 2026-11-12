"""TRX11 — 판(기준선·①·②·③·합)마다 갈래를 셈해 정밀·재현(개발·보류)·갈래별·kinds·고친/망친 쪽·민감도·19권 갈래 표를 낸다. 실호출 0.
기준선은 `out/trx9/scan` 의 TRX9 특징을 `ras2_scan.load_feats` 로 읽어 `ras2_scan.branch` 로 다시 셈한다(TRX9 훑기는 다시 안 함).
새 판은 `out/trx11/scan` 의 `features3`(ras3_photo) 로 셈한다. 라벨은 `prework/gold-trx9/pages.json`(얼린 것, 고치지 않음).

  uv run --frozen --group scope3colab python -m raslab.ras3_eval check                    → 기준선 재현 == out/trx9/branch.json, raster(① 끔·③ 끔) == 기준선 raster
  … ras3_eval grid --labels <pages.json>                                                  → 개발 셋 격자(① 규칙 · ② T4·R · ③ T5·parts) — 보류 셋은 보지 않는다
  … ras3_eval eval dev|hold|all --labels <pages.json> [--json <out>]                      → 판 다섯 × P/R·갈래·kinds·고친/망친·민감도 (hold 첫 실행 시각을 first-run.txt 셋째 줄에)
  … ras3_eval branch                                                                      → out/trx11/branch.json (19권 × 판 다섯, 쪽마다 갈래)
  … ras3_eval summary --labels <pages.json>                                               → out/trx11/summary.md (표 전부)

셋 가름(코드로, pages.json 안 고침): dev = TRX9 dev(issi·fm) 또는 key ∈ DEV_ADD · hold = 나머지 13권. ko/en 쌍둥이는 같은 셋.
판: base = ras2_scan.branch(TRX9 특징) · photo = ① 사진 가르기 뒤 raster + 옛 outline · outline = 기준선 raster + ② outline' · small = ③(묶음·216dpi 재점수) raster + 옛 outline · all = ①+②+③.
raster 셈(새 판): 사진 아닌 조각(큰 조각 + ③a 묶음(합집합 ≥ T5))의 합집합 비율 ≥ T1 and 그 조각들의 글 줄 점수 최대 ≥ T2(③b 면 넓이 < RESCORE_BELOW 조각은 216dpi 점수),
  또는 사진 아닌 묶음 하나가 점수 ≥ T2(묶음의 넓이 바닥은 T5 — 사진 조각이 빠진 뒤 작은 묶음 혼자 T1 에 못 미치는 쪽을 살린다, 개발 셋 b7 p74). T1·T2·T3 는 ras2_scan.T 그대로.
"""

import argparse
import itertools
import json
import sys
import time
from pathlib import Path

from raslab import ras2_scan as r2
from raslab import ras3_photo as ph
from raslab import scan as base

DEV_ADD = frozenset({"b7", "b3", "nb", "nb2"})  # 물음 1 (first-run.txt 첫 줄로 얼림) — ko/en 쌍둥이는 둘 다 보류
TWINS = (("sam-ko", "sam-en"), ("cos-ko", "cos-en"), ("kb-ko", "kb-en"), ("kbd-ko", "kbd-en"))
VARIANTS = ("base", "photo", "outline", "small", "all")
# 판 → (raster 를 어떻게: None=기준선 | (사진 가르기 켬?, parts), outline 을 어떻게: "old"|"new")
VARIANT_DEF = {"base": (None, "old"), "photo": ((True, ""), "old"), "outline": (None, "new"), "small": ((False, "ab"), "old"), "all": ((True, "ab"), "new")}
POS = r2.POS
FIRST_RUN = ph.OUT11 / "first-run.txt"


def default_cfg():
    return {"T": dict(r2.T), "rule": [list(c) for c in ph.PHOTO_RULE], "T4": ph.T4, "R": ph.R, "T5": ph.T5, "parts": "ab"}


def which_set(lb):
    return "dev" if lb.get("set") == "dev" or lb["key"] in DEV_ADD else "hold"


# ---------- 읽기 ----------
def load_feats3(keys=None):
    """out/trx11/scan/*.json → {키: {쪽: {size, chars, paths, path_density, textless_big, pieces3, small_paths, …}}}."""
    out = {}
    for p in sorted((ph.OUT11 / "scan").glob("*.json")):
        if p.stem == "times":
            continue
        if keys and p.stem not in keys:
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        out[p.stem] = {pg["page"]: {"size": pg["size"], **pg["features"], **pg["features3"]} for pg in d["pages"]}
    return out


# ---------- 갈래 ----------
def raster_pieces(f3, cfg, photo, parts):
    """새 판에서 raster 셈에 드는 (bbox, 점수) 목록 — 사진 조각은 빠지고, ③a 묶음은 parts 에 a 가 있고 합집합 ≥ T5 일 때, ③b 면 작은 조각은 216dpi 점수."""
    out = []
    for p in f3["pieces3"]:
        if p["src"] == "cluster" and ("a" not in parts or p["area"] < cfg["T5"]):
            continue
        if photo and ph.is_photo(p, cfg["rule"]):
            continue
        sc = p["score"]
        if "b" in parts and p.get("score216") is not None:
            sc = p["score216"]
        out.append((p["bbox"], sc, p["src"]))
    return out


def raster3(f3, cfg, photo, parts):
    """raster' = 사진 아닌 조각 합집합 ≥ T1 and 그 점수 최대 ≥ T2 · 또는(③a) 사진 아닌 묶음 하나가 합집합 ≥ T5 and 점수 ≥ T2(묶음의 바닥은 T1 이 아니라 T5)."""
    pcs = raster_pieces(f3, cfg, photo, parts)
    w, h = f3["size"]
    union = base.union_ratio([b for b, _, _ in pcs], w, h)
    top = max((s for _, s, _ in pcs), default=0.0)
    if union >= cfg["T"]["T1"] and top >= cfg["T"]["T2"]:
        return True
    return any(src == "cluster" and s >= cfg["T"]["T2"] for _, s, src in pcs)


def outline_old(f, cfg):
    return f["chars"] < r2.NO_TEXT and (f["path_density"] >= cfg["T"]["T3"] or f["paths"] >= r2.OUTLINE_PATHS)


def branch3(f9, f3, variant, cfg):
    """f9 = TRX9 특징(기준선), f3 = ras3 특징. variant ∈ VARIANTS."""
    rmode, omode = VARIANT_DEF[variant]
    if rmode is None:
        r = f9["textless_big"] >= cfg["T"]["T1"] and f9["img_text_est"] >= cfg["T"]["T2"]
    else:
        r = raster3(f3, cfg, *rmode)
    o = outline_old(f9, cfg) if omode == "old" else ph.outline2(f3, cfg["T4"], cfg["R"])
    return "both" if r and o else "raster" if r else "outline" if o else "none"


def why(f9, f3, cfg):
    """고친/망친 목록에 붙이는 까닭 — 조각마다 kind·점수·특징 요약과 small_paths."""
    pcs = []
    for p in f3["pieces3"]:
        kind = "photo" if ph.is_photo(p, cfg["rule"]) else ("small" if p["src"] == "cluster" or p["area"] < r2.BIG_PIECE else "text")
        pcs.append(
            f"{p['src']}{'×' + str(p['n']) if p['src'] == 'cluster' else ''} {p['area']:.3f} {kind} s{p['score']}"
            + (f"/s216 {p['score216']}" if p.get("score216") is not None else "")
            + f" bg{p['bg_frac']} nc{p['n_colors']} sat{p['sat_med']} ed{p['edge_density']} j{int(bool(p['jpeg']))}"
        )
    return {"chars": f9["chars"], "paths": f9["paths"], "small_paths": f3["small_paths"], "textless_big": f9["textless_big"], "img_text_est": f9["img_text_est"], "pieces": pcs}


# ---------- 셈 ----------
def evaluate3(labels, feats9, feats3, variant, cfg, only_set=None):
    by_set, kinds, miss, false, br_count = {}, {}, [], [], {}
    for lb in labels:
        s = which_set(lb)
        if only_set and only_set != "all" and s != only_set:
            continue
        f9, f3 = feats9[lb["key"]][lb["page"]], feats3[lb["key"]][lb["page"]]
        br = branch3(f9, f3, variant, cfg)
        pred = br in POS
        for name in (s, "all"):
            by_set.setdefault(name, []).append((lb["has_textless_text"], pred))
            br_count.setdefault(name, {"raster": 0, "outline": 0, "both": 0, "none": 0})[br] += 1
        for k in lb.get("kinds", []):
            want = "raster" if k in r2.RASTER_KINDS else "outline"
            kinds.setdefault(k, {"n": 0, "any": 0, "match": 0})
            kinds[k]["n"] += 1
            kinds[k]["any"] += pred
            kinds[k]["match"] += br in (want, "both")
        if lb["has_textless_text"] and not pred:
            miss.append({"key": lb["key"], "page": lb["page"], "kinds": lb.get("kinds", []), "set": s, "why": why(f9, f3, cfg)})
        if not lb["has_textless_text"] and pred:
            false.append({"key": lb["key"], "page": lb["page"], "branch": br, "set": s, "why": why(f9, f3, cfg)})
    return {"variant": variant, "sets": {k: r2.prf(v) for k, v in by_set.items()}, "branches": br_count, "kinds": kinds, "missed": miss, "false": false}


def diff_vs_base(labels, feats9, feats3, variant, cfg, only_set=None):
    """기준선 대비 고친 쪽(fp→tn, fn→tp)·망친 쪽(tp→fn, tn→fp)."""
    out = {"fixed_fp": [], "fixed_fn": [], "broken_tp": [], "broken_fp": []}
    for lb in labels:
        s = which_set(lb)
        if only_set and only_set != "all" and s != only_set:
            continue
        f9, f3 = feats9[lb["key"]][lb["page"]], feats3[lb["key"]][lb["page"]]
        b0, b1 = branch3(f9, f3, "base", cfg), branch3(f9, f3, variant, cfg)
        p0, p1 = b0 in POS, b1 in POS
        if p0 == p1:
            continue
        row = {"key": lb["key"], "page": lb["page"], "set": s, "kinds": lb.get("kinds", []), "base": b0, "new": b1, "why": why(f9, f3, cfg)}
        pos = lb["has_textless_text"]
        key = ("fixed_fp" if not pos else "broken_tp") if p0 and not p1 else ("fixed_fn" if pos else "broken_fp")
        out[key].append(row)
    return out


def scaled_cfgs(cfg):
    """민감도: 규칙 clause 값(P1·P2)·T4·R·T5 각 ×0.5·×2. bool clause(jpeg)는 못 늘림."""
    out = {}
    for i, (name, op, v) in enumerate(cfg["rule"]):
        if isinstance(v, bool):
            continue
        for m in (0.5, 2.0):
            c = json.loads(json.dumps(cfg))
            c["rule"][i][2] = v * m
            out[f"P{i + 1}x{m:g}"] = c
    for k in ("T4", "R", "T5"):
        for m in (0.5, 2.0):
            c = json.loads(json.dumps(cfg))
            c[k] = cfg[k] * m
            out[f"{k}x{m:g}"] = c
    return out


def sensitivity3(labels, feats9, feats3, cfg, only_set=None, variant="all"):
    out = {"base": evaluate3(labels, feats9, feats3, variant, cfg, only_set)["sets"]}
    for name, c in scaled_cfgs(cfg).items():
        out[name] = evaluate3(labels, feats9, feats3, variant, c, only_set)["sets"]
    return out


def book_table(feats9, feats3, variant, cfg):
    books, tot = {}, {"n": 0, "raster": 0, "outline": 0, "both": 0, "none": 0}
    for key in feats9:
        rows = {pg: branch3(feats9[key][pg], feats3[key][pg], variant, cfg) for pg in sorted(feats9[key])}
        c = {b: sum(1 for v in rows.values() if v == b) for b in ("raster", "outline", "both", "none")}
        books[key] = {"n": len(rows), "count": c, "pages": rows}
        tot["n"] += len(rows)
        for b in c:
            tot[b] += c[b]
    return {"books": books, "total": tot}


# ---------- 격자 (개발 셋만) ----------
GRID_DIR = {"bg_frac": "<", "n_colors": ">=", "sat_med": ">=", "edge_density": "<", "jpeg": "=="}
SINGLE_OK = ("bg_frac", "n_colors", "edge_density")  # 단독 sat_med·단독 jpeg 금지(물음 2)


def _mids(values, cap=40):
    vs = sorted(set(v for v in values if v is not None))
    mids = [(a + b) / 2 for a, b in zip(vs, vs[1:])]
    if len(mids) > cap:
        step = len(mids) / cap
        mids = [mids[int(i * step)] for i in range(cap)]
    return mids, vs


def _gap(v, vs):
    lo = max((x for x in vs if x < v), default=None)
    hi = min((x for x in vs if x > v), default=None)
    if lo is None or hi is None or vs[-1] == vs[0]:
        return 0.0
    return (hi - lo) / (vs[-1] - vs[0])


def grid_photo(labels, feats9, feats3, cfg, keep=2):
    """① 규칙 격자(판 photo): 개발 셋 tp ≥ 기준선 tp − keep 안에서 정밀 최대, 동률이면 특징 적은 쪽 → tp 큰 쪽 → 문턱 틈 넓은 쪽."""
    dev = [lb for lb in labels if which_set(lb) == "dev"]
    base_tp = evaluate3(dev, feats9, feats3, "base", cfg)["sets"]["dev"]["tp"]
    vals = {k: [] for k in GRID_DIR}
    for lb in dev:
        for p in feats3[lb["key"]][lb["page"]]["pieces3"]:
            for k in vals:
                vals[k].append(p.get(k))
    cands = {}
    for k in ("bg_frac", "n_colors", "sat_med", "edge_density"):
        mids, vs = _mids(vals[k])
        cands[k] = [(m, _gap(m, vs)) for m in mids]
    cands["jpeg"] = [(True, 0.0)]
    rules = []
    for k in SINGLE_OK:
        for v, g in cands[k]:
            rules.append(([[k, GRID_DIR[k], v]], g))
    for k1, k2 in itertools.combinations(GRID_DIR, 2):
        for (v1, g1), (v2, g2) in itertools.product(cands[k1], cands[k2]):
            rules.append(([[k1, GRID_DIR[k1], v1], [k2, GRID_DIR[k2], v2]], min(g1, g2)))
    rows = []
    for rule, gap in rules:
        c = dict(cfg)
        c["rule"] = rule
        s = evaluate3(dev, feats9, feats3, "photo", c)["sets"]["dev"]
        if s["tp"] >= base_tp - keep:
            rows.append({"rule": rule, "tp": s["tp"], "fp": s["fp"], "fn": s["fn"], "precision": s["precision"], "recall": s["recall"], "gap": round(gap, 4)})
    rows.sort(key=lambda r: (-(r["precision"] or 0), len(r["rule"]), -r["tp"], -r["gap"]))
    return {"base_tp": base_tp, "n_rules": len(rules), "n_ok": len(rows), "top": rows[:25]}


def grid_outline(labels, feats9, feats3, cfg, keep=2):
    dev = [lb for lb in labels if which_set(lb) == "dev"]
    base_tp = evaluate3(dev, feats9, feats3, "base", cfg)["sets"]["dev"]["tp"]
    rows = []
    for T4, R in itertools.product((25, 50, 75, 100, 150, 200, 300), (0.5, 1.0, 1.5, 2.0, 3.0, 5.0)):
        c = dict(cfg)
        c["T4"], c["R"] = T4, R
        s = evaluate3(dev, feats9, feats3, "outline", c)["sets"]["dev"]
        rows.append({"T4": T4, "R": R, "tp": s["tp"], "fp": s["fp"], "fn": s["fn"], "precision": s["precision"], "recall": s["recall"], "ok": s["tp"] >= base_tp - keep})
    rows.sort(key=lambda r: (not r["ok"], -(r["precision"] or 0), -r["tp"], abs(r["T4"] - 100) + abs(r["R"] - 1.5)))
    return {"base_tp": base_tp, "rows": rows}


def grid_small(labels, feats9, feats3, cfg, keep=2):
    dev = [lb for lb in labels if which_set(lb) == "dev"]
    base_tp = evaluate3(dev, feats9, feats3, "base", cfg)["sets"]["dev"]["tp"]
    rows = []
    for T5, parts in itertools.product((0.005, 0.0075, 0.01, 0.015, 0.02), ("a", "b", "ab")):
        c = dict(cfg)
        c["T5"], c["parts"] = T5, parts
        rmode = (False, parts)
        s = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
        for lb in dev:
            f9, f3 = feats9[lb["key"]][lb["page"]], feats3[lb["key"]][lb["page"]]
            r = raster3(f3, c, *rmode)
            pred = r or outline_old(f9, c)
            k = ("tp" if pred else "fn") if lb["has_textless_text"] else ("fp" if pred else "tn")
            s[k] += 1
        pr = round(s["tp"] / (s["tp"] + s["fp"]), 4) if s["tp"] + s["fp"] else None
        rows.append({"T5": T5, "parts": parts, **s, "precision": pr, "recall": round(s["tp"] / (s["tp"] + s["fn"]), 4), "ok": s["tp"] >= base_tp - keep})
    rows.sort(key=lambda r: (not r["ok"], -(r["precision"] or 0), -r["tp"], abs(r["T5"] - 0.01), -len(r["parts"])))
    return {"base_tp": base_tp, "rows": rows}


# ---------- 명령 ----------
def _load(a):
    labels = json.loads(Path(a.labels).read_text(encoding="utf-8"))["pages"] if getattr(a, "labels", None) else None
    return labels, r2.load_feats(), load_feats3()


def cmd_check(a):
    _, feats9, feats3 = _load(a)
    cfg = default_cfg()
    bj = json.loads((r2.OUT9 / "branch.json").read_text(encoding="utf-8"))
    n = ok = ok2 = 0
    for key in feats9:
        for pg, f9 in feats9[key].items():
            n += 1
            b = branch3(f9, feats3[key][pg], "base", cfg)
            ok += b == bj["books"][key]["pages"][str(pg)]["branch"] == r2.branch(f9)
            r0 = f9["textless_big"] >= cfg["T"]["T1"] and f9["img_text_est"] >= cfg["T"]["T2"]
            ok2 += r0 == raster3(feats3[key][pg], cfg, False, "")
    print(f"기준선 재현 == out/trx9/branch.json: {ok}/{n}")
    print(f"raster(① 끔·③ 끔, ras3 점수) == 기준선 raster: {ok2}/{n}")
    return 0 if ok == n == ok2 else 1


def cmd_grid(a):
    labels, feats9, feats3 = _load(a)
    cfg = default_cfg()
    out = {"photo": grid_photo(labels, feats9, feats3, cfg), "outline": grid_outline(labels, feats9, feats3, cfg), "small": grid_small(labels, feats9, feats3, cfg)}
    (ph.OUT11 / "grid-dev.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"① 규칙 {out['photo']['n_rules']} 중 tp ≥ {out['photo']['base_tp']}−2: {out['photo']['n_ok']}")
    for r in out["photo"]["top"][:15]:
        print("  ", r["rule"], f"tp {r['tp']} fp {r['fp']} fn {r['fn']} P {r['precision']} R {r['recall']} gap {r['gap']}")
    print("② T4·R (ok 먼저, 정밀 차례):")
    for r in out["outline"]["rows"][:10]:
        print("  ", r)
    print("③ T5·parts:")
    for r in out["small"]["rows"][:10]:
        print("  ", r)


def _note_first_hold():
    if not FIRST_RUN.is_file():
        return
    txt = FIRST_RUN.read_text(encoding="utf-8")
    if "holdout first eval" not in txt:
        with FIRST_RUN.open("a", encoding="utf-8") as fh:
            fh.write(f"holdout first eval at {time.strftime('%Y-%m-%dT%H:%M:%S')} (ras3_eval eval hold — 판 다섯 + 민감도 한 번에)\n")


def cmd_eval(a):
    labels, feats9, feats3 = _load(a)
    cfg = default_cfg()
    if a.set in ("hold", "all"):
        _note_first_hold()
    out = {"set": a.set, "cfg": cfg, "variants": {}, "diff": {}, "sensitivity": sensitivity3(labels, feats9, feats3, cfg, a.set)}
    for v in VARIANTS:
        ev = evaluate3(labels, feats9, feats3, v, cfg, a.set)
        out["variants"][v] = ev
        out["diff"][v] = diff_vs_base(labels, feats9, feats3, v, cfg, a.set) if v != "base" else None
        s = ev["sets"].get(a.set if a.set != "all" else "all")
        print(f"{v:8s} {a.set}: n {s['n']} tp {s['tp']} fp {s['fp']} fn {s['fn']} tn {s['tn']} P {s['precision']} R {s['recall']} · 갈래 {ev['branches'].get(a.set if a.set != 'all' else 'all')}")
        if out["diff"][v]:
            d = out["diff"][v]
            print(f"         고침 fp→tn {[(r['key'], r['page']) for r in d['fixed_fp']]} fn→tp {[(r['key'], r['page']) for r in d['fixed_fn']]}")
            print(f"         망침 tp→fn {[(r['key'], r['page']) for r in d['broken_tp']]} tn→fp {[(r['key'], r['page']) for r in d['broken_fp']]}")
    print("민감도(all):")
    for name, sets in out["sensitivity"].items():
        s = sets.get(a.set if a.set != "all" else "all")
        print(f"  {name}: tp {s['tp']} fp {s['fp']} fn {s['fn']} P {s['precision']} R {s['recall']}")
    path = Path(a.json) if a.json else ph.OUT11 / f"eval-{a.set}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("→", path)


def cmd_branch(a):
    _, feats9, feats3 = _load(a)
    cfg = default_cfg()
    out = {"cfg": cfg, "variants": {v: book_table(feats9, feats3, v, cfg) for v in VARIANTS}}
    hdr = "| 키 | 쪽 | " + " | ".join(f"{v} r/o/b (있음)" for v in VARIANTS) + " |"
    print(hdr + "\n|---|---:|" + "---:|" * len(VARIANTS))
    for key in feats9:
        cells = []
        for v in VARIANTS:
            b = out["variants"][v]["books"][key]
            c = b["count"]
            cells.append(f"{c['raster']}/{c['outline']}/{c['both']} ({b['n'] - c['none']})")
        print(f"| {key} | {out['variants']['base']['books'][key]['n']} | " + " | ".join(cells) + " |")
    cells = []
    for v in VARIANTS:
        t = out["variants"][v]["total"]
        cells.append(f"{t['raster']}/{t['outline']}/{t['both']} ({t['n'] - t['none']}, {(t['n'] - t['none']) / t['n']:.1%})")
    print(f"| 합 | {out['variants']['base']['total']['n']} | " + " | ".join(cells) + " |")
    (ph.OUT11 / "branch.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


def _prf_row(s):
    return f"{s['n']} | {s['tp']} | {s['fp']} | {s['fn']} | {s['tn']} | {s['precision']} | {s['recall']}"


def cmd_summary(a):
    labels, feats9, feats3 = _load(a)
    cfg = default_cfg()
    L = [f"# TRX11 summary — cfg {json.dumps(cfg, ensure_ascii=False)}", ""]
    for s in ("dev", "hold", "all"):
        L += [f"## {s}", "", "| 판 | n | tp | fp | fn | tn | P | R | raster | outline | both |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for v in VARIANTS:
            ev = evaluate3(labels, feats9, feats3, v, cfg, s)
            st, br = ev["sets"][s], ev["branches"][s]
            L.append(f"| {v} | {_prf_row(st)} | {br['raster']} | {br['outline']} | {br['both']} |")
        L.append("")
        for v in VARIANTS[1:]:
            d = diff_vs_base(labels, feats9, feats3, v, cfg, s)
            L.append(f"- {v} 고침 fp→tn {[(r['key'], r['page']) for r in d['fixed_fp']]} · fn→tp {[(r['key'], r['page']) for r in d['fixed_fn']]} · 망침 tp→fn {[(r['key'], r['page']) for r in d['broken_tp']]} · tn→fp {[(r['key'], r['page']) for r in d['broken_fp']]}")
        L.append("")
        ev = evaluate3(labels, feats9, feats3, "all", cfg, s)
        L.append("kinds(all): " + " · ".join(f"{k} {d['any']}/{d['n']}·{d['match']}" for k, d in sorted(ev["kinds"].items())))
        L.append("")
        L.append("| 민감도(all) | tp | fp | fn | P | R |\n|---|---:|---:|---:|---:|---:|")
        for name, sets in sensitivity3(labels, feats9, feats3, cfg, s).items():
            st = sets[s]
            L.append(f"| {name} | {st['tp']} | {st['fp']} | {st['fn']} | {st['precision']} | {st['recall']} |")
        L.append("")
    tbl = {v: book_table(feats9, feats3, v, cfg) for v in VARIANTS}
    L += ["## 19권 갈래 (raster/outline/both, 있음)", "", "| 키 | 쪽 | " + " | ".join(VARIANTS) + " |", "|---|---:|" + "---:|" * len(VARIANTS)]
    for key in feats9:
        cells = [f"{tbl[v]['books'][key]['count']['raster']}/{tbl[v]['books'][key]['count']['outline']}/{tbl[v]['books'][key]['count']['both']} ({tbl[v]['books'][key]['n'] - tbl[v]['books'][key]['count']['none']})" for v in VARIANTS]
        L.append(f"| {key} | {tbl['base']['books'][key]['n']} | " + " | ".join(cells) + " |")
    cells = [f"{tbl[v]['total']['raster']}/{tbl[v]['total']['outline']}/{tbl[v]['total']['both']} ({tbl[v]['total']['n'] - tbl[v]['total']['none']}, {(tbl[v]['total']['n'] - tbl[v]['total']['none']) / tbl[v]['total']['n']:.1%})" for v in VARIANTS]
    L.append(f"| 합 | {tbl['base']['total']['n']} | " + " | ".join(cells) + " |")
    tpath = ph.OUT11 / "scan" / "times.json"
    if tpath.is_file():
        times = json.loads(tpath.read_text(encoding="utf-8"))
        tot_pages = sum(t["pages"] for t in times.values())
        tot_s = sum(t["feat_s"] + t["scan_s"] for t in times.values())
        L += ["", f"특징 계산: {tot_pages}쪽 {tot_s:.1f}s (쪽당 {tot_s / tot_pages * 1000:.1f}ms · 렌더 쪽 {sum(t['rendered_pages'] for t in times.values())} · 패스 걷기 {sum(t['paths_s'] for t in times.values()):.1f}s · 렌더 {sum(t['render_s'] for t in times.values()):.1f}s)"]
    (ph.OUT11 / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("branch")
    for name in ("grid", "summary"):
        sub.add_parser(name).add_argument("--labels", required=True)
    e = sub.add_parser("eval")
    e.add_argument("set", choices=("dev", "hold", "all"))
    e.add_argument("--labels", required=True)
    e.add_argument("--json")
    a = ap.parse_args(argv)
    return {"check": cmd_check, "grid": cmd_grid, "eval": cmd_eval, "branch": cmd_branch, "summary": cmd_summary}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
