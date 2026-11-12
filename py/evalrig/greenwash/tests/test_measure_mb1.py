"""MB1 측정 셈 시험(발주서 MB1 §3-1 T1~T9·T11, 지은 자료만 — fixtures/mb1-*.json)."""
from __future__ import annotations

import copy
import io
import re
import tokenize
from pathlib import Path

import pytest

from evalrig.greenwash import convert_e0, sameinput
from evalrig.greenwash import measure_mb1 as M


def _run(start: int, n: int) -> str:
    return "".join(chr(0xAC00 + start + i) for i in range(n))


# ---- T1·T2 글자 규칙 ----
def test_t1_run_rule_60():
    p = _run(0, 120)
    assert M.found(p, _run(4000, 20) + p[30:90] + _run(4100, 20))  # 60자 연속
    assert not M.found(p, _run(4000, 20) + p[30:89] + _run(4100, 20))  # 59자


def test_t1_whitespace_and_fullwidth():
    p = "ABCDEFGHIJ" * 7  # 70자
    fw = "".join(chr(ord(c) + 0xFEE0) for c in p)  # 전각
    spaced = " ".join(fw[i:i + 5] for i in range(0, len(fw), 5))
    assert M.found(p, "xx\n" + spaced + "\tyy")


def test_t2_short_passage_needs_all():
    p = _run(0, 40)
    assert M.found(p, "앞" + p + "뒤")
    assert not M.found(p, p[:39])


# ---- T3·T4 recall ----
def test_t3_recall_hand(fixture):
    f = fixture("mb1-recall.json")
    res = M.recall(f["evidence"], f["books"])
    x, y = res["arms"]["X"], res["arms"]["Y"]
    assert x["denominator"] == 2 and y["denominator"] == 2  # core 0 주장은 분모 밖
    assert x["mean"] == pytest.approx(0.75) and x["anyRatio"] == pytest.approx(1.0)
    assert y["mean"] == pytest.approx(round((0 + 1 / 3) / 2, 4)) and y["anyRatio"] == pytest.approx(0.5)
    assert x["selfSentenceInEvidence"] == pytest.approx(round(1 / 3, 4))
    d = M.recall_diff(res, "X", "Y")
    assert d["denominator"] == 2 and d["diff"] == pytest.approx(round((0.5 + (1 - 1 / 3)) / 2, 4))


def test_t4_id_over_char_warns(fixture):
    f = fixture("mb1-recall.json")
    res = M.recall(f["evidence"], f["books"])
    assert len(res["warnings"]) == 1
    w = res["warnings"][0]
    assert (w["arm"], w["claim"], w["charRecall"], w["idRecall"]) == ("Y", "c1", 0.0, 1.0)


def test_recall_within_ceiling(fixture):
    f = fixture("mb1-recall.json")
    ev = copy.deepcopy(f["evidence"])
    P = f["books"]["x1"]["passages"]
    ev += [{"book": "x1", "claim": "c1", "arm": "ceil", "rep": 1, "text": P["p1"]},  # 천장 안 core = p1 하나
           {"book": "x1", "claim": "c2", "arm": "ceil", "rep": 1, "text": P["p3"] + P["p4"]}]
    res = M.recall(ev, f["books"], ceiling="ceil")
    assert res["arms"]["X"]["withinCeiling"] == pytest.approx(1.0)  # c1 1/1, c2 2/2
    assert res["arms"]["Y"]["withinCeiling"] == pytest.approx(0.25)  # c1 0/1, c2 1/2


# ---- T5·T6 flip ----
def test_t5_flip_hand(fixture):
    f = fixture("mb1-judge.json")
    res = M.flip(f["base"], [], [("m1/A", "m1/B")])["cross"][0]
    assert res["denominator"] == 4
    assert res["withinX"] == pytest.approx(0.25) and res["withinY"] == pytest.approx(0.25)
    assert res["between"] == pytest.approx(0.625)
    assert res["delta"] == pytest.approx(0.375)
    assert res["transitions"]["High->Mid"] == 4


def test_t6_invalid_outside_denominator(fixture):
    """심은 넷(주장마다 A 1회차 하나만 무효) — 단위 분모 −4, 따로 칸 1·1·1·1, 쌍 단위라 주장은 남는다."""
    f = fixture("mb1-judge.json")
    base = M.flip(f["base"], [], [("m1/A", "m1/B")])["cross"][0]
    res = M.flip(f["base"] + f["planted"], ["m1/A"], [("m1/A", "m1/B")])
    c = res["cross"][0]
    assert c["units"] == 32 - 4 and base["units"] == 16
    assert c["outside"] == {"low": 1, "parsedFalse": 1, "cutoff": 1, "failed": 1}
    assert res["within"]["m1/A"]["outside"] == {"low": 1, "parsedFalse": 1, "cutoff": 1, "failed": 1}
    assert c["denominator"] == 8 and c["claimsDropped"] == 0
    assert res["within"]["m1/A"]["denominator"] == 4  # 심은 주장은 A 안 쌍이 없음
    # 손셈: 안 A (0+1+0+0)/4 · 안 B (0+0+0+1+0+0+0+0)/8 · 사이 (1+.5+0+1+0·4)/8 → Δ .3125 − (.25+.125)/2
    assert c["withinX"] == pytest.approx(0.25) and c["withinY"] == pytest.approx(0.125)
    assert c["between"] == pytest.approx(0.3125) and c["delta"] == pytest.approx(0.125)


def test_t6b_one_rep_invalid_keeps_other_pairs():
    """한 회차만 무효인 주장은 그 회차가 든 쌍만 빠진다(주장째 빼지 않음 — rules.frozen.md 「쌍 단위」)."""
    row = lambda arm, rep, level, **kw: {"book": "x1", "claim": "c1", "arm": arm, "rep": rep, "model": "m1", "path": "eval",
                                         "level": level, "parsed": True, "cutoff": False, "failed": None, **kw}
    rows = [row("A", 1, "Mid", parsed=False), row("A", 2, "High"), row("B", 1, "High"), row("B", 2, "Mid")]
    c = M.flip(rows, [], [("m1/A", "m1/B")])["cross"][0]
    assert c["denominator"] == 1 and c["units"] == 3
    assert c["between"] == pytest.approx(0.5)  # (A2,B1) 같음 · (A2,B2) 다름
    assert c["withinXClaims"] == 0 and c["withinY"] == pytest.approx(1.0)
    assert c["delta"] == pytest.approx(-0.5)


# ---- T7 result1 ----
def _gold():
    items = [{"claimId": f"c{i}", "page": i, "sentence": f"지은 주장 문장 {i} 번입니다"} for i in range(1, 5)]
    return {"meta": {"source": {"sha256": "ab" * 32, "pageCount": 9}}, "items": items}


def test_t7_result1_converts_and_same(fixture):
    f = fixture("mb1-judge.json")
    res = M.result1(f["base"], _gold(), "ab" * 32, 9, {"engine": "e0"})
    assert set(res) == {("m1", "A", 1), ("m1", "A", 2), ("m1", "B", 1), ("m1", "B", 2)}
    a1 = res[("m1", "A", 1)]
    assert all(it["level"] != "NonRisk" for it in a1["items"]) and len(a1["items"]) == 3  # c4 NonRisk 빠짐
    ca, cb = convert_e0.convert(a1, run="A1"), convert_e0.convert(res[("m1", "B", 1)], run="B1")
    out = sameinput.same(ca, cb)
    assert out["units"] == [3, 4]
    with pytest.raises(ValueError):
        M.result1(f["base"], _gold(), "cd" * 32, 9, {"engine": "e0"})


# ---- T8·T9 ledger ----
def test_t8_ledger_live_only(fixture):
    f = fixture("mb1-ledger.json")
    res = M.ledger(f["core"], f["semgraph"], f["pdx"], M.load_prices(str(Path(__file__).parent / "fixtures/mb1-models.json")))
    assert res["calls"] == 5
    assert res["totalUsd"] == pytest.approx(0.002 + 0.00115 + 0.00517 + 0.0019 + 0.0015)
    assert res["byMeasure"]["gm2"]["usd"] == pytest.approx(0.0039)
    assert res["byMeasure"]["gm3"]["usd"] == pytest.approx(0.00115 + 0.00517)  # core 입력은 캐시 뺀 수 · 캐시 쓰기 단가
    assert res["byMeasure"]["gm4"]["usd"] == pytest.approx(0.0015)
    assert res["warnings"] == []


def test_t9_ledger_price_mismatch_warns(fixture):
    f = copy.deepcopy(fixture("mb1-ledger.json"))
    f["core"][0]["costUsd"] += 0.001
    res = M.ledger(f["core"], f["semgraph"], f["pdx"], M.load_prices(str(Path(__file__).parent / "fixtures/mb1-models.json")))
    assert len(res["warnings"]) == 1


# ---- T11 이름·단가 숫자 0 ----
PRICE_LITERALS = {0.4, 1.6, 0.1, 0.5, 2.0, 10.0, 0.02, 0.01, 0.125, 2.5}


def test_t11_no_prices_or_names():
    from semgraph.sg1d import BOOKS  # 권 열쇠는 semgraph 에서 읽는다(이 시험에도 글자로 두지 않음)

    src = Path(M.__file__).read_text(encoding="utf-8")
    floats = {float(t.string) for t in tokenize.generate_tokens(io.StringIO(src).readline)
              if t.type == tokenize.NUMBER and "." in t.string}
    assert not floats & PRICE_LITERALS
    assert len(BOOKS) >= 4
    for key in BOOKS:
        assert not re.search(rf"\b{re.escape(key)}\b", src, re.IGNORECASE), key


# ---- T10 gm4 (C2) ----
def _gm4_rows():
    plan = {  # 후보: (전 1, 전 2, 후 1, 후 2), 후 근거에 표(회차 1, 2)
        1: (("High", "High", "NonRisk", "NonRisk"), (True, True)),
        2: (("Mid", "Mid", "NonRisk", "NonRisk"), (False, False)),
        3: (("High", "NonRisk", "NonRisk", "NonRisk"), (True, True)),  # 전 두 회차 갈래 다름 → 바닥 1
        4: (("NonRisk", "NonRisk", "High", "High"), (True, False)),
        5: (("Mid", "Mid", "NonRisk", "NonRisk"), (True, False)),
    }
    rows = []
    for c, (lv, tb) in plan.items():
        for i, (arm, rep) in enumerate((("before", 1), ("before", 2), ("after", 1), ("after", 2))):
            rows.append({"book": "x1", "arm": arm, "rep": rep, "model": "m1", "page": 10 + c, "sentence": f"지은 후보 문장 {c}",
                         "path": "eval", "level": lv[i], "parsed": True, "cutoff": False, "failed": None,
                         "evidenceHasTable": tb[rep - 1] if arm == "after" else False})
    return rows


def test_t10_gm4_hand():
    rows = _gm4_rows()
    labels = {M.cand_key(11, "지은 후보 문장 1"): "table_resolves", M.cand_key(12, "지은 후보 문장 2"): "not_resolved",
              M.cand_key(14, "지은 후보 문장 4"): "spurious"}
    res = M.gm4(rows, labels)
    assert res["denominator"] == 5 and res["frozenOk"]
    assert res["floor"] == pytest.approx(0.2) and res["downRatio"] == pytest.approx(0.6)
    assert res["excess"] == pytest.approx(0.4)
    assert res["downLabels"] == {"not_resolved": 1, "table_resolves": 1, "unlabeled": 1}
    assert res["upCount"] == 1 and res["upLabels"] == {"spurious": 1}
    assert res["downWithoutTable"] == 1
    assert res["evidenceHasTableAfter"] == pytest.approx(0.6)


def test_t10_gm4_frozen_break_and_invalid():
    rows = _gm4_rows()
    rows[-1] = {**rows[-1], "sentence": "다른 지은 문장"}  # 후 2 회차의 후보가 다름 → 얼림 깨짐
    assert not M.gm4(rows, {})["frozenOk"]
    rows = _gm4_rows()
    rows[0] = {**rows[0], "parsed": False}
    res = M.gm4(rows, {})
    assert res["denominator"] == 4 and res["outside"]["parsedFalse"] == 1
