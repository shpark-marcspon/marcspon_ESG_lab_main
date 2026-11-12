"""K2 절 시작 꼴 — 지어낸 블록·속성·후보."""

import pytest

from trlab import sec_shape as S
from trlab.common import Block


def blk(id, page, text, top=40.0, left=40.0, kind="heading"):
    return Block(id, kind, page, 1, text, (left, top, left + 200, top + 20), 20.0, "body", 600.0, 800.0, "", [])


def book():
    bs = [blk("p3-b1", 3, "Alpha"), blk("p7-b1", 7, "Beta"), blk("p11-b1", 11, "Gamma"),
          blk("p15-b1", 15, "Delta"),  # 같은 꼴(위 왼쪽 큰 굵은)
          blk("p15-b2", 15, "small note", top=400.0, left=300.0, kind="paragraph"),  # 다른 꼴
          blk("p16-b1", 16, "Delta"),  # 바로 앞 쪽에 같은 글·같은 크기 → 되풀이
          blk("p20-b1", 20, "Epsilon"), blk("p22-b1", 22, "Epsilon")]  # 두 쪽 떨어짐 → 안 깎임
    big = {"status": "ok", "size_max": 30.0, "bold_share": 1.0}
    attrs = {"base": {"size": 10.0}, "blocks": {b.id: dict(big) for b in bs}}
    attrs["blocks"]["p15-b2"] = {"status": "ok", "size_max": 11.0, "bold_share": 0.0}
    cand = {"rows": {b.id: {"page": b.page, "size": attrs["blocks"][b.id]["size_max"], "text": b.text} for b in bs},
            "pages": {str(p): {"divider": False, "cands": [b.id for b in bs if b.page == p]} for p in range(1, 25)}}
    return bs, attrs, cand


def test_confident_and_same_shape_scores_higher():
    bs, attrs, cand = book()
    items = [{"text": "Alpha"}, {"text": "Beta"}, {"text": "Gamma"}, {"text": "Zeta"}]
    dp = [{"block": "p3-b1"}, {"block": "p7-b1"}, {"block": "p11-b1"}, {"block": "p15-b1"}]
    conf = S.confident(items, dp, cand, bs)
    assert conf == [0, 1, 2]  # Zeta ↔ Delta 는 확실 아님
    ids = {b.id: b for b in bs}
    model = S.learn([S.features(ids[dp[i]["block"]], attrs, cand, set()) for i in conf])
    scores, rep = S.shape_scores(bs, attrs, cand, model)
    assert scores["p15-b1"] > scores["p15-b2"]
    # 빈도 평균(최대가 아님): 다른 꼴 블록은 특징 여섯 중 nc 만 맞음
    f = S.features(ids["p15-b2"], attrs, cand, set())
    want = sum(model[k].get(f[k], 0.0) for k in S.FEATS) / len(S.FEATS)
    assert scores["p15-b2"] == pytest.approx(round(want, 4))
    assert 0 < scores["p15-b2"] < 1


def test_repeat_penalty_only_previous_page():
    bs, attrs, cand = book()
    ids = {b.id: b for b in bs}
    model = S.learn([S.features(ids[x], attrs, cand, set()) for x in ("p3-b1", "p7-b1", "p11-b1")])
    scores, rep = S.shape_scores(bs, attrs, cand, model)
    assert rep["p16-b1"] and not rep["p15-b1"]
    assert scores["p20-b1"] - scores["p16-b1"] == pytest.approx(S.REP_PEN)  # 꼴 같음(쪽 후보 1), 되풀이만 다름
    assert not rep["p22-b1"] and scores["p22-b1"] == scores["p20-b1"]  # 두 쪽 떨어진 것은 안 깎임


def test_bonus_sign():
    b = S.bonus_of({"a": 0.9, "b": 0.2, "c": 0.5}, 2)
    assert len(b) == 2 and b[0]["a"] > 0 > b[0]["b"] and b[0]["c"] == 0
    assert b[0]["a"] == pytest.approx(S.K2_W * 0.4)
