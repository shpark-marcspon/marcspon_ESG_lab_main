"""TRX11 ① 사진 특징·가르기, ② 글자 크기 패스·outline', ③ 작은 그림 묶음·216dpi 재점수 — 지어낸 배열·bbox 만, PDF 열지 않음, PIL 0."""

import numpy as np

from raslab import ras2_scan as r2
from raslab import ras3_eval as ev
from raslab import ras3_photo as ph

RNG = np.random.default_rng(7)


def text_arr(h=120, w=240):
    """흰 바탕에 검은 글 줄 띠(글: bg_frac 높·n_colors 적)."""
    a = np.full((h, w, 3), 255, dtype=np.uint8)
    for y in range(12, h - 12, 12):
        a[y : y + 5, 10 : w - 10] = 0
    return a


def flat_arr(h=120, w=240):
    """평면 넉 색 덩어리(인포그래픽: n_colors 적·채도 높)."""
    a = np.zeros((h, w, 3), dtype=np.uint8)
    a[: h // 2, : w // 2] = (230, 40, 40)
    a[: h // 2, w // 2 :] = (40, 200, 60)
    a[h // 2 :, : w // 2] = (30, 60, 220)
    a[h // 2 :, w // 2 :] = (250, 220, 30)
    return a


def photo_arr(h=120, w=240):
    """난수 컬러 잡음(사진: bg_frac 낮·n_colors 많)."""
    return RNG.integers(0, 256, size=(h, w, 3), dtype=np.uint8)


def test_photo_features_order():
    t, f, p = ph.photo_features(text_arr()), ph.photo_features(flat_arr()), ph.photo_features(photo_arr())
    assert t["bg_frac"] > 0.5 > p["bg_frac"]
    assert t["n_colors"] < 10 and f["n_colors"] <= 4 < 100 < p["n_colors"]
    assert f["sat_med"] > 0.5 > t["sat_med"]
    assert t["edge_density"] > p["edge_density"] or t["edge_density"] > 0.05
    assert set(t) == {"bg_frac", "n_colors", "sat_med", "edge_density"}


def test_photo_features_tiny_is_none():
    assert ph.photo_features(np.zeros((3, 3, 3), dtype=np.uint8)) == ph.EMPTY_PF
    assert ph.photo_features(np.zeros((0, 10, 3), dtype=np.uint8)) == ph.EMPTY_PF


def test_is_photo_only_photo_and_rule_limits():
    rule = [["bg_frac", "<", 0.5], ["n_colors", ">=", 100]]
    assert ph.is_photo(ph.photo_features(photo_arr()), rule)
    assert not ph.is_photo(ph.photo_features(text_arr()), rule)
    assert not ph.is_photo(ph.photo_features(flat_arr()), rule)
    assert not ph.is_photo(dict(ph.EMPTY_PF), rule)  # 못 셈 → 사진 아님
    assert ph.is_photo({"jpeg": True, "sat_med": 0.9}, [["jpeg", "==", True], ["sat_med", ">=", 0.5]])
    import pytest

    with pytest.raises(ValueError):
        ph.is_photo({}, [["a", "<", 1], ["b", "<", 1], ["c", "<", 1]])


def _f3(pieces, size=(600, 800), small_paths=0, chars=0):
    return {"size": list(size), "pieces3": pieces, "small_paths": small_paths, "chars": chars}


def _piece(bbox, area, score, photo, src="big", score216=None):
    return {
        "bbox": bbox,
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
        "edge_density": 0.15,
        "jpeg": photo,
        "colorspace": 2,
        "bpp": 24,
    }


CFG = {"T": {"T1": 0.02, "T2": 0.2, "T3": 2.0}, "rule": [["bg_frac", "<", 0.5], ["n_colors", ">=", 100]], "T4": 100, "R": 1.5, "T5": 0.01, "parts": "ab"}


def test_raster_drops_photo_piece_but_keeps_text_next_to_photo():
    # 사진 조각 하나(점수 0.8)뿐 → 가르기 뒤 raster 없음
    photo_only = _f3([_piece([0, 0, 300, 400], 0.25, 0.8, True)])
    assert ev.raster3(photo_only, CFG, photo=False, parts="")
    assert not ev.raster3(photo_only, CFG, photo=True, parts="")
    # 사진 옆에 글 조각(점수 0.6, 넓이 5%) → raster 유지
    both = _f3([_piece([0, 0, 300, 400], 0.25, 0.8, True), _piece([320, 0, 560, 100], 0.05, 0.6, False)])
    assert ev.raster3(both, CFG, photo=True, parts="")
    # 글 조각이 사진 가르기 뒤 1% 뿐이면 T1 에 못 미침
    small_text = _f3([_piece([0, 0, 300, 400], 0.25, 0.8, True), _piece([320, 0, 380, 80], 0.01, 0.9, False)])
    assert not ev.raster3(small_text, CFG, photo=True, parts="")


def test_small_paths_boundaries_and_outline2():
    boxes = [(0, 0, 40, 14), (0, 0, 40.1, 14), (0, 0, 40, 14.1), (10, 10, 20, 12), (0, 0, 500, 5)]
    assert ph.small_paths(boxes) == 2  # 40×14 경계 포함, 40.1·14.1 밖, 500 너비 밖
    assert ph.outline2({"small_paths": 100, "chars": 60}, T4=100, R=1.5)
    assert not ph.outline2({"small_paths": 99, "chars": 0}, T4=100, R=1.5)  # T4 아래
    assert not ph.outline2({"small_paths": 100, "chars": 70}, T4=100, R=1.5)  # R × chars = 105 > 100
    assert ph.outline2({"small_paths": 300, "chars": 200}, T4=100, R=1.5)


def _rec(images, size=(600, 800)):
    return {"size": list(size), "images": [{"bbox": b, "area": round((b[2] - b[0]) * (b[3] - b[1]) / (size[0] * size[1]), 4), "chars": 0, "repeated": False} for b in images]}


def test_clusters_gap_union_and_big_only():
    # 쪽 600×800, 짧은 변 600 → 틈 1.5% = 9pt. 조각 60×80 = 1% 씩.
    near = _rec([[0, 0, 60, 80], [70, 0, 130, 80]])  # 틈 10pt… 넓힘 9pt 씩 → 겹침(0+9 ≥ 70-9=61) → 하나
    cl = ph.clusters(near)
    assert len(cl) == 1 and cl[0]["n"] == 2 and cl[0]["area"] >= 0.02 and cl[0]["bbox"] == [0, 0, 130, 80]
    far = _rec([[0, 0, 60, 80], [100, 0, 160, 80]])  # 틈 40pt > 18 → 둘(각 1% ≥ T5)
    assert [c["n"] for c in ph.clusters(far)] == [1, 1]
    tiny = _rec([[0, 0, 30, 80], [40, 0, 70, 80]])  # 0.5% 둘, 합집합 1% ≥ T5 → 하나 · T5 를 2% 로 올리면 0
    assert len(ph.clusters(tiny)) == 1
    assert ph.clusters(tiny, t5=0.02) == []
    below = _rec([[0, 0, 20, 80]])  # 0.33% < FLOOR → 묶음에 안 듦
    assert ph.clusters(below) == []
    big_only = _rec([[0, 0, 300, 400], [300, 0, 600, 400]])  # 큰 조각(25%)끼리 → 이미 big 이라 뺌
    assert ph.clusters(big_only) == []
    big_plus_small = _rec([[0, 0, 300, 400], [305, 0, 365, 80]])  # 큰 + 작은 → 묶음 하나(n 2)
    assert [c["n"] for c in ph.clusters(big_plus_small)] == [2]


def test_cluster_pieces_counted_only_with_part_a_and_T5():
    cl = _piece([0, 0, 130, 80], 0.02, 0.9, False, src="cluster")
    f3 = _f3([cl])
    assert not ev.raster3(f3, CFG, photo=False, parts="")  # ③a 꺼짐 → 묶음 안 셈
    assert ev.raster3(f3, CFG, photo=False, parts="a")
    assert not ev.raster3(f3, dict(CFG, T5=0.03), photo=False, parts="a")  # T5 위로 → 빠짐
    # 묶음 혼자 T1(2%) 아래라도 합집합 ≥ T5 이고 점수 ≥ T2 면 raster(묶음의 바닥은 T5); 사진 묶음이면 아님
    small_cl = _f3([_piece([0, 0, 60, 80], 0.012, 0.9, False, src="cluster")])
    assert ev.raster3(small_cl, CFG, photo=True, parts="a")
    assert not ev.raster3(_f3([_piece([0, 0, 60, 80], 0.012, 0.9, True, src="cluster")]), CFG, photo=True, parts="a")
    assert not ev.raster3(_f3([_piece([0, 0, 60, 80], 0.012, 0.1, False, src="cluster")]), CFG, photo=True, parts="a")  # 점수 < T2


def test_rescore216_uses_scale_and_part_b():
    # stripe_score 의 띠 높이 기준은 scale 로 같이 커진다: 108dpi 에서 글 줄인 8px 띠(5.3pt)는 216dpi 의 16px 띠와 같은 점수
    a108 = np.full((120, 200, 3), 255, dtype=np.uint8)
    for y in range(10, 110, 16):
        a108[y : y + 8, 30:170] = 0  # 행 잉크 비율 0.76 (ROW_HI 0.95 아래)
    a216 = np.repeat(np.repeat(a108, 2, axis=0), 2, axis=1)
    s1, n1 = r2.stripe_score(a108, r2.PIECE_SCALE)
    s2, n2 = r2.stripe_score(a216, ph.RESCORE_SCALE)
    assert s1 > 0.9 and n1 == n2 and abs(s1 - s2) < 0.05
    # scale 을 안 넘기면 두꺼운 띠가 글 줄로 안 읽힌다: 30px 띠는 216dpi 면 10pt(글 줄, hi 42px) · 108dpi 기준이면 20pt(hi 21px 넘음)
    thick = np.full((240, 200, 3), 255, dtype=np.uint8)
    for y in range(10, 230, 60):  # 바탕(흰)이 최빈색으로 남게 띠 넷
        thick[y : y + 30, 30:170] = 0  # 30px: 216dpi 면 10pt(글 줄) · 108dpi 로 읽으면 20pt(글 줄 아님)
    assert r2.stripe_score(thick, ph.RESCORE_SCALE)[0] > 0.9
    assert r2.stripe_score(thick, r2.PIECE_SCALE)[0] == 0.0
    # ③b: 작은 조각은 score216 으로 셈
    low108 = _piece([0, 0, 120, 160], 0.04, 0.0, False, score216=0.9)
    assert not ev.raster3(_f3([low108]), CFG, photo=False, parts="")
    assert ev.raster3(_f3([low108]), CFG, photo=False, parts="b")


def test_kind_of():
    assert ph.kind_of(_piece([0, 0, 1, 1], 0.1, 0, True), CFG["rule"]) == "photo"
    assert ph.kind_of(_piece([0, 0, 1, 1], 0.1, 0, False), CFG["rule"]) == "text"
    assert ph.kind_of(_piece([0, 0, 1, 1], 0.015, 0, False, src="cluster"), CFG["rule"]) == "small"


def test_photo_rule_frozen_shape():
    assert 1 <= len(ph.PHOTO_RULE) <= 2
    names = [c[0] for c in ph.PHOTO_RULE]
    assert not (names == ["sat_med"] or names == ["jpeg"])  # 단독 sat_med·단독 jpeg 금지
