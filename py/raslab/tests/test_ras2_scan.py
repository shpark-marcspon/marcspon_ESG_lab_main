"""TRX9 ① 훑개 시험 — 지어낸 쪽 레코드와 배열, PDF 0, 네트워크 0, PIL 0."""

import random

import numpy as np

from raslab import ras2_scan as S

T0 = {"T1": 0.02, "T2": 0.5, "T3": 5.0}


def rec(chars=500, paths=10, images=(), size=(600, 800)):
    w, h = size
    imgs = []
    for bbox, ch, rep in images:
        imgs.append({"bbox": list(bbox), "area": round((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) / (w * h), 4), "chars": ch, "repeated": rep, "form_level": 0, "form_matrix_identity": True})
    keep = [o["bbox"] for o in imgs if not o["repeated"] and o["area"] >= 0.01]
    return {"page": 1, "size": [w, h], "chars": chars, "paths": paths, "images": imgs, "raster_union": S.base.union_ratio(keep, w, h), "chars_in_images": 0}


def test_textless_big_excludes_repeated_small_and_texty():
    big = ((0, 0, 300, 400), 0, False)  # 25%, 글자 0
    small = ((400, 0, 440, 40), 0, False)  # 0.3%
    rep = ((0, 500, 300, 800), 0, True)  # 되풀이
    texty = ((300, 400, 600, 800), 50, False)  # 글자 겹침
    f = S.features(rec(images=[big, small, rep, texty]))
    assert 0.24 <= f["textless_big"] <= 0.26
    assert f["n_big"] == 1
    assert f["img_text_est"] == 0.0  # page 없음


def test_path_density_and_text_cover():
    f = S.features(rec(chars=480, paths=96, size=(600, 800)))  # 48 만 pt² → 48 단위
    assert f["path_density"] == 2.0
    assert f["text_cover"] == 10.0


def test_branch_four_ways():
    base = {"chars": 500, "paths": 10, "textless_big": 0.0, "img_text_est": 0.0, "path_density": 0.2}
    assert S.branch(base, T0) == "none"
    assert S.branch({**base, "textless_big": 0.3, "img_text_est": 0.8}, T0) == "raster"
    assert S.branch({**base, "textless_big": 0.3, "img_text_est": 0.1}, T0) == "none"  # 사진 꼴
    assert S.branch({**base, "chars": 10, "paths": 400}, T0) == "outline"
    assert S.branch({**base, "chars": 10, "paths": 50, "path_density": 6.0}, T0) == "outline"
    assert S.branch({**base, "chars": 10, "paths": 400, "textless_big": 0.3, "img_text_est": 0.8}, T0) == "both"
    assert S.branch({**base, "chars": 500, "paths": 400}, T0) == "none"  # 글자 층 있으면 윤곽선 아님


def _striped(h=200, w=160, line=6, gap=5, seed=0):
    """흰 바탕에 검은 글 줄 띠(line px)와 틈(gap px)이 되풀이되는 그림."""
    a = np.full((h, w, 3), 255, dtype=np.uint8)
    y = 10
    while y + line < h - 10:
        a[y : y + line, 12 : w - 12] = 0
        y += line + gap
    return a


def _noise(h=200, w=160, seed=1):
    r = np.random.default_rng(seed)
    return r.integers(0, 256, size=(h, w, 3), dtype=np.uint8)


def test_stripe_score_text_lines_beat_noise_and_solid():
    sc, nl = S.stripe_score(_striped(), 1.0)
    assert sc >= 0.9 and nl >= 10
    sn, _ = S.stripe_score(_noise(), 1.0)
    assert sn < 0.2
    solid = np.full((200, 160, 3), 255, dtype=np.uint8)
    solid[20:180, 20:140] = 30  # 한 덩어리(사진·색 블록 꼴)
    assert S.stripe_score(solid, 1.0)[0] == 0.0
    assert S.stripe_score(np.zeros((3, 3, 3), dtype=np.uint8), 1.0) == (0.0, 0)


def test_stripe_score_ignores_frame_and_vertical_rules():
    a = _striped()
    a[:, :3] = 0
    a[:, -3:] = 0
    a[:3, :] = 0
    a[-3:, :] = 0  # 액자
    a[:, 80:82] = 0  # 세로 괘선
    sc, _ = S.stripe_score(a, 1.0)
    assert sc >= 0.9


def test_pick_min_two_per_stratum_and_cap():
    rng = random.Random(9)
    strata = {"A": list(range(1, 4)), "B": list(range(4, 60)), "C": list(range(60, 101))}
    got = S.pick(strata, 10, rng)
    assert sum(len(v) for v in got.values()) == 10
    assert all(len(v) >= 2 for v in got.values())
    assert all(sorted(v) == v and set(v) <= set(strata[k]) for k, v in got.items())
    one = S.pick({"A": [7], "C": list(range(1, 50))}, 10, random.Random(9))
    assert one["A"] == [7] and len(one["C"]) == 9
    small = S.pick({"B": [1, 2, 3], "C": [4, 5]}, 15, random.Random(9))
    assert small == {"B": [1, 2, 3], "C": [4, 5]}
    assert S.book_n(11) == 11 and S.book_n(31) == 15 and S.book_n(100) == 10


def test_stratum_uses_coarse_values_only():
    assert S.stratum({"textless_big": 0.02, "chars": 900}) == "A"
    assert S.stratum({"textless_big": 0.0, "chars": 49}) == "B"
    assert S.stratum({"textless_big": 0.0, "chars": 50}) == "C"


def test_evaluate_precision_recall_denominators():
    feats = {
        "x": {
            1: {"chars": 10, "paths": 400, "textless_big": 0.0, "img_text_est": 0.0, "path_density": 7.0},  # outline
            2: {"chars": 900, "paths": 5, "textless_big": 0.3, "img_text_est": 0.9, "path_density": 0.1},  # raster
            3: {"chars": 900, "paths": 5, "textless_big": 0.3, "img_text_est": 0.1, "path_density": 0.1},  # none(사진)
            4: {"chars": 900, "paths": 5, "textless_big": 0.0, "img_text_est": 0.0, "path_density": 0.1},  # none
        }
    }
    labels = [
        {"key": "x", "page": 1, "has_textless_text": True, "kinds": ["outline_body"], "set": "hold"},
        {"key": "x", "page": 2, "has_textless_text": False, "kinds": [], "set": "hold"},  # 헛잡음
        {"key": "x", "page": 3, "has_textless_text": True, "kinds": ["raster_chart"], "set": "hold"},  # 놓침
        {"key": "x", "page": 4, "has_textless_text": False, "kinds": [], "set": "dev"},
    ]
    ev = S.evaluate(labels, feats, T0)
    h = ev["sets"]["hold"]
    assert (h["tp"], h["fp"], h["fn"], h["tn"]) == (1, 1, 1, 0)
    assert h["precision"] == 0.5 and h["recall"] == 0.5
    assert ev["sets"]["dev"]["precision"] is None and ev["sets"]["dev"]["recall"] is None
    assert ev["kinds"]["raster_chart"] == {"n": 1, "any": 0, "match": 0}
    assert ev["kinds"]["outline_body"] == {"n": 1, "any": 1, "match": 1}
    assert [(m["key"], m["page"]) for m in ev["missed"]] == [("x", 3)]
    assert [(m["page"], m["branch"]) for m in ev["false"]] == [(2, "raster")]
    sens = S.sensitivity(labels, feats, T0)
    assert set(sens) == {"base", "T1x0.5", "T1x2", "T2x0.5", "T2x2", "T3x0.5", "T3x2"}
    assert sens["T2x0.5"]["hold"]["tp"] == 1  # 0.1 < 0.25 라 3쪽은 여전히 놓침
