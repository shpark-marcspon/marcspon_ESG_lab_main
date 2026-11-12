"""MA2 채점 — 표 % 칸은 분모 꼴 · NO_GOLD 줄과 정답 줄이 갈림 · 「낫다」 판정 · 쪽 단위 잇기·길이 비(지은 자료만)."""
import pytest

from evalrig.common import report as R
from evalrig.common.match import is_ratio
from evalrig.translation import labpipe as L
from evalrig.translation import measure_ma2 as M

BLOCKS = [{"id": "p1-b1", "page": 1, "order": 1, "kind": "paragraph", "ko": "가상위원회는 47 곳을 관리했다. 또한 점검했다."},
          {"id": "p1-b2", "page": 1, "order": 2, "kind": "heading", "ko": "물 관리"},
          {"id": "p2-b1", "page": 2, "order": 1, "kind": "paragraph", "ko": "사업장 12 곳."}]
EN = {"p1-b1": "The Virtual Committee managed 47 sites.", "p1-b2": "Water", "p2-b1": "Twelve sites."}
KP = {1: "가상위원회는 47 곳을 관리했다. 또한 점검했다.\n물 관리", 2: "사업장 12 곳."}
GLOSS = [{"kr": "가상위원회", "en": "Virtual Committee"}, {"kr": "사업장", "en": "worksite"}]


def test_lab_page_units_join_and_origin():
    us = M.lab_page_units("syn", BLOCKS, EN, KP)
    assert [u.key for u in us] == ["page:1", "page:2"]
    assert us[0].payload["en"] == "The Virtual Committee managed 47 sites. Water"
    assert us[0].origin.engine == "v2" and us[0].origin.schema == M.LABFORM
    miss = M.lab_page_units("syn", BLOCKS, {"p1-b1": "x"}, KP)
    assert [u.status for u in miss] == ["ok", "missing"]


def test_lab_block_units_key_matches_bleu_segments():
    us = M.lab_block_units("syn", BLOCKS, EN)
    assert us[0].key == "1:p1-b1" and us[0].key.split(":", 1)[1] == "p1-b1"  # bleu.block_segments 의 열쇠 꼴


def test_omit_and_num_pages_are_ratios():
    pd = M.page_dicts(M.lab_page_units("syn", BLOCKS, EN, KP))
    om, nm = M.omit_pages(pd), M.num_pages(pd)
    assert is_ratio(om["rate"]) and om["rate"]["den"] == 2 and om["n"] == 1  # 쪽 1: 국문 2문장 > 영문 1문장(+ 제목)
    assert is_ratio(nm["rate"]) and nm["rate"]["den"] == 2 and nm["missing"] == 1  # 12 가 Twelve 로


def test_better_verdicts():
    assert M.better(30.0, 29.5, True, 1.0) == "차이 없음"
    assert M.better(31.0, 29.5, True, 1.0) == "예"
    assert M.better(0.1, 0.2, False) == "예" and M.better(0.3, 0.2, False) == "아니오"
    assert M.better(None, 0.2, False) == "잴 수 없음"


def test_quality_and_nogold_rows_separate_and_ratio(monkeypatch):
    monkeypatch.setattr(M, "subject_dist", lambda key, units: {"den": 3, **{k: {"value": 1 / 3, "num": 1, "den": 3}
                                                                              for k in ("we", "name", "generic", "pronoun")}, "rule_ok": None})
    us = M.lab_page_units("syn", BLOCKS, EN, KP)
    refs = {1: {"ref": "The Virtual Committee managed 47 sites. Water", "coverage": {"value": 1.0}},
            2: {"ref": "Twelve sites.", "coverage": {"value": 0.2}}}
    q = M.quality_row("lab", us, refs, GLOSS, "off-set", "gloss-set", [1, 2])
    ng = M.nogold_rows("lab", "syn", M.page_dicts(us), {"S1": ["가"], "S4": [], "n_terms": 4}, None, "걸림 0")
    assert all(r["gold"] in ("off-set", "gloss-set") for r in q) and all(r["gold"] == R.NO_GOLD for r in ng)
    for r in q + ng:
        if r["지표"].endswith("rate") or "rate(" in r["지표"]:
            assert is_ratio(r["값"]), r["지표"]
    bleu = next(r for r in q if r["지표"].startswith("BLEU 쪽"))
    assert bleu["지표"].endswith("*") and bleu["값"] == pytest.approx(100.0)  # 덮음 < 0.5 쪽이 있으면 별표
    tv = next(r for r in q if r["지표"] == "위반 엄격 rate")
    assert tv["값"] == {"value": 0.5, "num": 1, "den": 2}  # 쪽 2 의 사업장 → worksite 없음
    s4 = next(r for r in ng if r["지표"].startswith("불일치 S4"))
    assert s4["값"] == {"value": 0.0, "num": 0, "den": 4}


def test_length_ratio():
    a = M.lab_page_units("a", BLOCKS, {k: v + v for k, v in EN.items()}, KP)
    b = M.lab_page_units("b", BLOCKS, EN, KP)
    r = M.length_ratio(a, b)
    assert r["n"] == 2 and r["median"] == pytest.approx(2.0, rel=0.05)


def test_split_inside_between_counts_feed_table():
    secs = [(1, 1), (2, 2)]
    rows = [{"term": "가", "page": 1, "kind": "exact", "key": "a", "pair": "page:1"},
            {"term": "가", "page": 2, "kind": "exact", "key": "b", "pair": "page:2"}]
    f = [{"kr": "가", "stage": "S1", "renderings": [["a", 1, [1]], ["b", 1, [2]]]}]
    r = L.split_inside_between(f, rows, secs)
    assert (r["n_inside"], r["n_between"]) == (0, 1)


# ---------- C3: M-T3 표본 적음 · 흔들림 · 판 지시 지킴 ----------
def test_share_or_small_threshold():
    assert M.share_or_small(5, 29) == M.SMALL  # 분모 30 미만은 수를 내지 않음
    assert M.share_or_small(6, 30) == {"value": 0.2, "num": 6, "den": 30}


def test_jitter():
    a, b = {"value": 0.5, "num": 15, "den": 30}, {"value": 0.4, "num": 12, "den": 30}
    assert M.jitter(a, b) == pytest.approx(0.1)
    assert M.jitter(a, M.SMALL) == M.SMALL


def _r(unit, subj):
    return {"unit": unit, "band": "paragraph", "subj": subj}


def test_rule_ok_we_and_third():
    rows = [_r("a", "name"), _r("a", "we"), _r("a", "we"),  # we 판 지킴 · third 판 어김
            _r("b", "name"), _r("b", "pronoun"),  # third 판 지킴 · we 판 어김
            _r("c", "we"), _r("c", "we"),  # 둘 다 어김(첫 주어가 이름 아님)
            _r("d", "name")]  # 주어 문장 1 — 분모 밖
    assert M.rule_ok(rows, "we") == (1, 3)
    assert M.rule_ok(rows, "third") == (1, 3)


def test_subj_counts_paragraph_only():
    rows = [_r("a", "we"), _r("a", "none"), {"unit": "t", "band": "table", "subj": "name"}, _r("b", "generic")]
    assert M.subj_counts(rows) == {"den": 2, "we": 1, "name": 0, "generic": 1, "pronoun": 0}

