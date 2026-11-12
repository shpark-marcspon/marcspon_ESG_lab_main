"""MA1b 측정 셈 — 지은 자료만(실재 기업·보고서 글 0). 주장어 목록은 measure_ma1b.CLAIM_WORDS(우리가 지은 목록)."""
import filecmp
import json

import pytest

from evalrig.common import gold as G
from evalrig.common import report as R
from evalrig.drafting import measure_ma1b as MM

ALLOWED = ["가나 배출량: 1,120 tCO2eq(2023)", "다라 재활용률: 45.5%(2024)"]


# ── new_numbers ──

def test_new_numbers_comma_folded():
    r = MM.new_numbers("배출량은 1120 톤이다.", ["배출량 1,120 톤"])
    assert r["new"] == [] and r["rate"]["den"] == 1


def test_new_numbers_digit_boundary_not_substring():
    r = MM.new_numbers("배출량 120 톤", ALLOWED)
    assert r["new"] == ["120"]
    assert r["rate"] == {"value": 1.0, "num": 1, "den": 1}


def test_new_numbers_decimal_and_distinct():
    r = MM.new_numbers("재활용률 45.5% · 45.5% · 2024년 · 2031년", ALLOWED)
    assert r["new"] == ["2031"]
    assert r["rate"]["num"] == 1 and r["rate"]["den"] == 3  # 45.5 · 2024 · 2031


def test_new_numbers_only_given_texts_are_allowed():
    """허용 글은 받은 bullet 글뿐 — 재료 전체에 있는 숫자라도 받은 글에 없으면 새 숫자."""
    material_only = "자료 원문에만 있는 값 7,777"
    r = MM.new_numbers("값 7777", ALLOWED)
    assert r["new"] == ["7777"]
    assert MM.new_numbers("값 7777", ALLOWED + [material_only])["new"] == []


def test_new_numbers_space_before_number_not_glued():
    """「Scope 2 145,298」 — 앞 숫자와 공백으로 떨어진 숫자는 따로(공백 접어 붙이면 145298 이 새 숫자로 잘못 셈)."""
    assert MM.new_numbers("목표 2 145,298 톤", ["목표: 범위 2 145,298 톤(2030)"])["new"] == []
    assert MM.new_numbers("2026년 0.1, 2028년 0.09", ["목표: 2026년 0.1, 2028년 0.09"])["new"] == []


def test_cover_space_before_number_not_glued():
    assert MM.cover("범위 2 145,298 톤이다.", ["목표: 범위 2 145,298 톤"])["num"] == 1


def test_new_numbers_no_denominator():
    assert MM.new_numbers("숫자 없음", ALLOWED)["rate"]["status"] == "no_denominator"


# ── new_names ──

def test_new_names_latin_word():
    r = MM.new_names("가나 회사는 Zentora 인증을 받았다.", ["가나 회사 인증"])
    assert r["new"] == ["Zentora"]


def test_new_names_quoted():
    r = MM.new_names("「푸른숲 약속」 활동과 『바람길』 사업을 했다.", ["푸른숲 약속 활동"])
    assert r["new"] == ["바람길"]
    assert r["rate"]["den"] == 2


def test_new_names_latin_digit_mix():
    r = MM.new_names("QX-200 설비와 ABC 를 썼다.", ["ABC 설비"])
    assert "QX-200" in r["new"] and "ABC" not in r["new"]


def test_new_names_korean_name_not_measured():
    """한글 고유명은 못 잰다(무효 조건) — 토막이 없으면 분모 0."""
    assert MM.new_names("하늘마루 사업장을 지었다.", [])["rate"]["status"] == "no_denominator"


# ── cover ──

def test_cover_counts_only_bullets_with_numbers():
    bullets = ["배출량: 1,120 tCO2eq(2023)", "재활용률: 45.5%(2024)", "체계: 위원회 운영"]
    r = MM.cover("2023년 배출량은 1,120 tCO2eq 였다.", bullets)
    assert r == {"value": 0.5, "num": 1, "den": 2}


def test_cover_needs_all_numbers():
    r = MM.cover("배출량은 1,120 이었다.", ["배출량: 1,120 tCO2eq(2023)"])
    assert r["num"] == 0


# ── claim_rate ──

def test_claim_rate_word_boundary():
    texts = ["업계를 선도하는 체계", "선도적 활동", "최고 수준", "배출량: 120 t"]
    assert MM.claim_rate(texts) == {"value": 0.75, "num": 3, "den": 4}


def test_claim_rate_not_substring():
    """주장어가 다른 낱말 안에 든 것은 세지 않음(「우수」 ⊂ 「우수관」, 「최대」 ⊂ 「최대치값」 아님)."""
    assert not MM.has_claim("우수관 정비")
    assert not MM.has_claim("모범택시")
    assert MM.has_claim("우수한 성과")
    assert MM.has_claim("선도")


# ── table_leaves ──

def _items(leaf, matches):
    return [{"leaf": leaf, "idx": i, "match": [m]} for i, m in enumerate(matches)]


def test_table_leaves_threshold_inclusive():
    items = _items("D01", ["1,120", "45.5"])
    blocks = {"D01": {"p3-t1": "| 배출량 | 1,120 |", "p3-b2": "재활용률 45.5%"}}
    r = MM.table_leaves(items, blocks)["D01"]
    assert r["rate"]["value"] == 0.5 and r["table"] is True and r["facts"] == [0]


def test_table_leaves_below_threshold():
    items = _items("D01", ["1,120", "45.5", "300"])
    blocks = {"D01": {"p3-t1": "| 배출량 | 1,120 |", "p3-b2": "45.5 300"}}
    r = MM.table_leaves(items, blocks)["D01"]
    assert r["table"] is False and r["rate"]["num"] == 1


def test_table_leaves_digit_boundary():
    items = _items("D01", ["120"])
    blocks = {"D01": {"p3-t1": "| 배출량 | 1,120 |"}}
    assert MM.table_leaves(items, blocks)["D01"]["facts"] == []


def test_table_leaves_text_block_not_table():
    items = _items("D01", ["1,120"])
    assert MM.table_leaves(items, {"D01": {"p3-b1": "1,120"}})["D01"]["table"] is False
    assert MM.table_leaves(items, {}) == {}


# ── score_arm · score_subset ──

def _gold(leaves_facts, coverage="partial"):
    items, idx = [], {}
    for leaf, m in leaves_facts:
        items.append({"leaf": leaf, "idx": idx.setdefault(leaf, 0), "metric": "지표", "value": m, "unit": "", "year": "", "match": [m]})
        idx[leaf] += 1
    gs = G.new(set_id="drafting-test", module="drafting", unit="drafting.fact@1", coverage=coverage, synthetic=True,
               provenance={"kind": "runner_made", "by": "시험", "from": [], "at": "2026-11-09"},
               review={"status": "none", "by": None, "at": None, "note": "", "sample": None}, pending=[], items=items,
               meta={"leaves": {}})
    gs["frozen"] = {"sha16": G.sha16(gs), "at": "2026-11-09"}
    return gs


def _result(leaves):
    return {"schemaVersion": "drafting.bullets@3",
            "leaves": [{"key": k, "titlePath": [k], "pages": [1], "outcome": {"status": "done", "bullets": [
                {"text": t, "sources": [{"documentId": 0, "blockId": "p1-b1", "page": 1, "index": 0}], "unsupported": u}
                for t, u in bs]}} for k, bs in leaves.items()],
            "warnings": []}


def test_score_arm_missed_not_zero():
    gs = _gold([("D01", "1,120"), ("D02", "300")])
    s = MM.score_arm(_result({"D01": [("배출량 1,120", False)]}), gs)
    assert s["missed"] == ["D02"]
    assert s["recall"]["value"] == 1.0  # D02 를 0 점으로 넣으면 0.5


def test_score_arm_partial_precision_unmeasurable():
    s = MM.score_arm(_result({"D01": [("최고 수준 1,120", True)]}), _gold([("D01", "1,120")]))
    assert s["precision"]["status"] == "unmeasurable" and s["precision"]["why"] == "partial"
    assert s["claim_rate"]["num"] == 1 and s["unsupported_rate"]["num"] == 1


def test_score_subset_only_given_idx():
    gs = _gold([("D01", "1,120"), ("D01", "300"), ("D02", "45")])
    r = MM.score_subset({"D01": "1,120 만"}, gs, {"D01": [0], "D02": [0]})
    assert r["per_leaf"]["D01"]["value"] == 1.0 and r["missed"] == ["D02"]


# ── summary ──

def test_summary_twice_same_md(tmp_path):
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    gsets = []
    for s in ("sam", "cos", "kb", "b4"):
        gs = _gold([("D01", "1,120")])
        gs["setId"] = f"drafting-sg1-{s}"
        gs["frozen"] = {"sha16": G.sha16(gs), "at": "2026-11-09"}
        (gold_dir / f"drafting-sg1-{s}.json").write_text(G.dumps(gs), encoding="utf-8")
        gsets.append(gs)
    out = tmp_path / "out"
    (out / "mr2").mkdir(parents=True)
    rows = [{"gold": "drafting-sg1-sam", "잴 것": "M-R2", "재현": {"value": 0.5, "num": 1, "den": 2}},
            {"gold": R.NO_GOLD, "잴 것": "M-R2", "unsupported_rate": {"value": 0.0, "num": 0, "den": 3}}]
    (out / "mr2" / "rows.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    _, md1 = MM.summary(out, gold_dir)
    first = md1.read_bytes()
    (tmp_path / "first.md").write_bytes(first)
    _, md2 = MM.summary(out, gold_dir)
    assert filecmp.cmp(tmp_path / "first.md", md2, shallow=False)
    assert "정답 없음" in md2.read_text(encoding="utf-8")


def test_main_usage():
    assert MM.main([]) == 2


def test_no_outside_imports():
    import inspect
    src = inspect.getsource(MM)
    for bad in ("import openai", "from openai", "import esgapi", "from esgapi", "urlopen", "requests"):
        assert bad not in src
