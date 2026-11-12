"""TRX11 판·셈 — 지어낸 특징 dict 만. 기준선 판 == ras2_scan.branch, 셋 가름(DEV_ADD·쌍둥이), 고친/망친 넷 칸, P/R 분모, 민감도 키 꼴."""

from raslab import ras2_scan as r2
from raslab import ras3_eval as ev

CFG = {"T": {"T1": 0.02, "T2": 0.2, "T3": 2.0}, "rule": [["bg_frac", "<", 0.5], ["n_colors", ">=", 100]], "T4": 100, "R": 1.5, "T5": 0.01, "parts": "ab"}


def f9(chars=0, paths=0, textless_big=0.0, img_text_est=0.0, path_density=0.0):
    return {"chars": chars, "paths": paths, "textless_big": textless_big, "img_text_est": img_text_est, "path_density": path_density, "img_lines": 0}


def piece(area, score, photo, src="big", score216=None, bbox=None):
    return {
        "bbox": bbox or [0, 0, 100, 100],
        "area": area,
        "src": src,
        "n": 1,
        "score": score,
        "lines": 3,
        "score216": score216,
        "lines216": None,
        "bg_frac": 0.1 if photo else 0.9,
        "n_colors": 900 if photo else 20,
        "sat_med": 0.3 if photo else 0.0,
        "edge_density": 0.1,
        "jpeg": photo,
        "colorspace": 2,
        "bpp": 24,
    }


def f3(pieces=(), small_paths=0, chars=0, size=(100, 100)):
    return {"size": list(size), "chars": chars, "pieces3": list(pieces), "small_paths": small_paths}


def test_base_variant_equals_ras2_branch():
    cases = [
        f9(chars=10, paths=400, textless_big=0.3, img_text_est=0.5),  # both
        f9(chars=10, paths=400),  # outline
        f9(chars=500, textless_big=0.3, img_text_est=0.5),  # raster
        f9(chars=500, textless_big=0.3, img_text_est=0.1),  # none
        f9(chars=49, path_density=2.0),  # outline(밀도)
        f9(chars=50, path_density=2.0),  # none
    ]
    for f in cases:
        assert ev.branch3(f, f3(), "base", CFG) == r2.branch(f, CFG["T"])


def test_which_set_dev_add_and_twins():
    assert ev.which_set({"key": "issi", "set": "dev"}) == "dev"
    assert ev.which_set({"key": "b7", "set": "hold"}) == "dev"
    assert ev.which_set({"key": "b2", "set": "hold"}) == "hold"
    for a, b in ev.TWINS:
        assert ev.which_set({"key": a, "set": "hold"}) == ev.which_set({"key": b, "set": "hold"}) == "hold"
    assert not (ev.DEV_ADD & {k for t in ev.TWINS for k in t})


def _world():
    """쪽 넷: 양성 둘·음성 둘. 기준선 raster 는 사진 쪽을 헛잡고 작은 스캔을 놓친다."""
    labels = [
        {"key": "x", "page": 1, "has_textless_text": False, "kinds": [], "set": "hold"},  # 사진 헛잡음 → ① 고침(fp→tn)
        {"key": "x", "page": 2, "has_textless_text": True, "kinds": ["scan"], "set": "hold"},  # 작은 스캔 → ③ 고침(fn→tp)
        {"key": "x", "page": 3, "has_textless_text": True, "kinds": ["infographic"], "set": "hold"},  # 사진 꼴 인포 → ① 망침(tp→fn)
        {"key": "x", "page": 4, "has_textless_text": False, "kinds": [], "set": "hold"},  # 글자 크기 패스 많은 벡터 쪽 → ② 망침(tn→fp)
        {"key": "b7", "page": 5, "has_textless_text": True, "kinds": ["outline_body"], "set": "hold"},  # dev(DEV_ADD) outline 양성
    ]
    feats9 = {
        "x": {
            1: f9(chars=300, textless_big=0.3, img_text_est=0.6),
            2: f9(chars=300, textless_big=0.0, img_text_est=0.0),
            3: f9(chars=300, textless_big=0.1, img_text_est=0.5),
            4: f9(chars=300, paths=200, path_density=1.0),
        },
        "b7": {5: f9(chars=10, paths=400, path_density=5.0)},
    }
    feats3 = {
        "x": {
            1: f3([piece(0.3, 0.6, True)]),
            2: f3([piece(0.015, 0.0, False, src="cluster", score216=0.8)]),
            3: f3([piece(0.1, 0.5, True)]),
            4: f3([], small_paths=400, chars=300),  # 400 ≥ 100 and 400 ≥ 450? 아니 → R 1.5 면 안 걸림; R 1.0 이면 걸림
        },
        "b7": {5: f3([], small_paths=400, chars=10)},
    }
    return labels, feats9, feats3


def test_evaluate_sets_and_denominator():
    labels, feats9, feats3 = _world()
    e = ev.evaluate3(labels, feats9, feats3, "base", CFG)
    assert e["sets"]["hold"]["n"] == 4 and e["sets"]["dev"]["n"] == 1 and e["sets"]["all"]["n"] == 5  # 분모 = 라벨 쪽
    assert e["sets"]["hold"] == {"n": 4, "tp": 1, "fp": 1, "fn": 1, "tn": 1, "precision": 0.5, "recall": 0.5}
    assert e["branches"]["hold"] == {"raster": 2, "outline": 0, "both": 0, "none": 2}
    assert [(m["key"], m["page"]) for m in e["missed"]] == [("x", 2)]
    assert [(m["key"], m["page"]) for m in e["false"]] == [("x", 1)]
    assert e["kinds"]["scan"] == {"n": 1, "any": 0, "match": 0}
    only = ev.evaluate3(labels, feats9, feats3, "base", CFG, only_set="dev")
    assert set(only["sets"]) == {"dev", "all"} and only["sets"]["all"]["n"] == 1


def test_diff_four_cells():
    labels, feats9, feats3 = _world()
    d = ev.diff_vs_base(labels, feats9, feats3, "all", dict(CFG, R=1.0))
    cells = {k: [(r["key"], r["page"]) for r in v] for k, v in d.items()}
    assert cells == {"fixed_fp": [("x", 1)], "fixed_fn": [("x", 2)], "broken_tp": [("x", 3)], "broken_fp": [("x", 4)]}
    assert d["fixed_fp"][0]["base"] == "raster" and d["fixed_fp"][0]["new"] == "none"
    assert "pieces" in d["fixed_fp"][0]["why"] and "small_paths" in d["broken_fp"][0]["why"]
    # ② 만 켜고 R 1.5 면 x p4 는 안 걸린다(400 < 450)
    assert ev.diff_vs_base(labels, feats9, feats3, "outline", CFG)["broken_fp"] == []


def test_variants_change_only_their_part():
    labels, feats9, feats3 = _world()
    br = {v: [ev.branch3(feats9[l["key"]][l["page"]], feats3[l["key"]][l["page"]], v, CFG) for l in labels] for v in ev.VARIANTS}
    assert br["base"] == ["raster", "none", "raster", "none", "outline"]
    assert br["photo"] == ["none", "none", "none", "none", "outline"]
    assert br["small"] == ["raster", "raster", "raster", "none", "outline"]
    assert br["outline"] == ["raster", "none", "raster", "none", "outline"]
    assert br["all"] == ["none", "raster", "none", "none", "outline"]


def test_sensitivity_keys_and_scaling():
    labels, feats9, feats3 = _world()
    s = ev.sensitivity3(labels, feats9, feats3, CFG)
    assert set(s) == {"base", "P1x0.5", "P1x2", "P2x0.5", "P2x2", "T4x0.5", "T4x2", "Rx0.5", "Rx2", "T5x0.5", "T5x2"}
    assert s["base"]["all"]["n"] == 5
    scaled = ev.scaled_cfgs(CFG)
    assert scaled["P1x2"]["rule"][0][2] == 1.0 and scaled["P2x0.5"]["rule"][1][2] == 50 and scaled["T5x2"]["T5"] == 0.02
    assert "P1x0.5" not in ev.scaled_cfgs(dict(CFG, rule=[["jpeg", "==", True]]))  # bool 은 못 늘림
    # T5×2 면 1.5% 묶음이 빠져 x p2 를 다시 놓친다
    assert s["T5x2"]["all"]["fn"] == s["base"]["all"]["fn"] + 1


def test_book_table_counts():
    labels, feats9, feats3 = _world()
    t = ev.book_table(feats9, feats3, "base", CFG)
    assert t["total"] == {"n": 5, "raster": 2, "outline": 1, "both": 0, "none": 2}
    assert t["books"]["x"]["count"]["raster"] == 2 and t["books"]["b7"]["pages"][5] == "outline"
