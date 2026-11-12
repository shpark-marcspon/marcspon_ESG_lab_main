"""evalrig.iro.measure_mic1 — 지은 자료(실재 글 0, 네트워크 0)."""
import filecmp
import json

import pytest

from evalrig.common import gold as G
from evalrig.common.units import Origin, Unit
from evalrig.iro import measure_mic1 as M
from evalrig.iro.gold import build_empty
from evalrig.iro.urlcheck import check_units_urls

O = Origin("v1", "materiality.result@1", "syn", "iro.result-json@1")
WLS = {"cell73": {"ok.example.invalid"}}
IN = "2025-02-03"
OLD = "2020-01-01"
DOC = "https://ok.example.invalid/doc/"
OUTSIDE = "https://far.example.invalid/doc/"
ROOT = "https://ok.example.invalid/"


def _it(title, urls, date=IN):
    return {"title": title, "date": date, "date_status": "ok" if date else "missing", "summary": "", "extra": {},
            "urls": list(urls)}


def _u(topic, branch, items, whitelist="cell73"):
    return Unit(module="materiality", unit="iro.topic-branch@1", key=f"{topic}|{branch}", group=topic, origin=O,
                payload={"topic": topic, "branch": branch, "items": items, "searched": None, "citations": None,
                         "scope": {"whitelist": whitelist, "dateRange": "2024-01-01~오늘까지"}})


def _chk(units):
    return check_units_urls(units, WLS)


# ── flag_items ──


def test_flag_items_reasons_four_and_no_url():
    u = _u("가", "regulation", [
        _it("통과", [DOC + "1"]),
        _it("첫 화면", [ROOT]),
        _it("목록 밖", [OUTSIDE + "1"]),
        _it("기간 밖", [DOC + "2"], date=OLD),
        _it("날짜 없음", [DOC + "3"], date=None),
        _it("URL 없음", []),
    ])
    (f,) = M.flag_items(_chk([u]))
    assert [it["reasons"] for it in f["items"]] == [
        [], ["root_only"], ["outside_whitelist"], ["out_of_range"], ["date_unknown"], ["no_url"]]


def test_flag_items_unreadable_date_is_date_unknown():
    it = _it("못 읽음", [DOC + "1"])
    it["date_status"], it["date"] = "unreadable", None
    (f,) = M.flag_items(_chk([_u("가", "regulation", [it])]))
    assert f["items"][0]["reasons"] == ["date_unknown"]


def test_flag_items_controversy_has_no_whitelist_reason():
    (f,) = M.flag_items(_chk([_u("가", "controversy", [_it("사건", [OUTSIDE + "9"])])]))
    assert f["items"][0]["reasons"] == []


def test_flag_items_one_of_many_urls_flags_item():
    (f,) = M.flag_items(_chk([_u("가", "regulation", [_it("둘", [DOC + "1", ROOT]), _it("하나", [DOC + "2"])])]))
    assert f["items"][0]["reasons"] == ["root_only"]
    assert f["items"][1]["reasons"] == []  # 항목 ↔ 출처는 payload 차례로 이음


def test_flag_items_whitelist_unknown_not_outside():
    (f,) = M.flag_items(_chk([_u("가", "regulation", [_it("판 없음", [OUTSIDE + "1"])], whitelist="nope")]))
    assert f["items"][0]["reasons"] == []
    assert f["whitelistUnknown"] == 1


# ── reask_targets · 틀 ──


def test_reask_targets_order_and_scope():
    us = [_u("나", "controversy", [_it("c", [DOC + "5"], date=None)]),
          _u("가", "controversy", [_it("c", [DOC + "4"], date=None)]),
          _u("가", "regulation", [_it("r", [ROOT])]),
          _u("나", "regulation", [_it("r", [DOC + "6"])]),  # 통과 — 대상 밖
          _u("가", "impact", [_it("i", [ROOT])])]  # 2부 — 대상 밖
    fl = M.flag_items(_chk(us))
    assert [t["key"] for t in M.reask_targets(fl)] == ["나|controversy", "가|regulation", "가|controversy"]


def test_reask_paragraph_templates_by_branch():
    r = {"branch": "regulation", "items": [{"idx": 0, "title": "가상", "urls": [ROOT], "reasons": ["root_only"]},
                                           {"idx": 1, "title": "통과", "urls": [DOC], "reasons": []}]}
    c = {"branch": "controversy", "items": [{"idx": 2, "title": "가상 사건", "urls": [], "reasons": ["no_url"]}]}
    pr, pc = M.reask_paragraph(r, "기간X"), M.reask_paragraph(c, "기간X")
    assert "신뢰 출처 목록" in pr and "신뢰 출처 목록" not in pc
    assert "항목 1: 가상" in pr and "통과" not in pr and "항목 3: 가상 사건" in pc and "기간X" in pc
    assert "신뢰 출처 목록" not in M.REASON_TEXT["root_only"] + M.REASON_TEXT["no_url"]


# ── merge_reask ──


def _cell_before():
    u = _u("가", "regulation", [_it("통과", [DOC + "1"]), _it("걸림", [ROOT]), _it("걸림2", [OUTSIDE + "1"])])
    return _chk([u])


def test_merge_reask_keeps_passed_adds_answers_and_skips_repeat():
    us = _cell_before()
    fl = M.flag_items(us)
    ans = {"가|regulation": {"items": [_it("새", [DOC + "2"]), _it("되풀이", [DOC + "1/"])], "failure": None}}
    (m,) = M.merge_reask(us, fl, ans)
    assert [it["title"] for it in m.payload["items"]] == ["통과", "새"]
    assert m.payload["items"][1]["fromReask"] is True
    assert m.payload["reask"] == "reasked" and m.payload["reaskRepeatedPassed"] == 1
    assert [s.ref for s in m.sources] == [DOC + "1", DOC + "2"]
    assert [it["title"] for it in us[0].payload["items"]] == ["통과", "걸림", "걸림2"]  # 원 단위 그대로
    assert "reask" not in us[0].payload


def test_merge_reask_failed_cell_keeps_original():
    us = _cell_before()
    (m,) = M.merge_reask(us, M.flag_items(us), {"가|regulation": {"items": [], "failure": "unparsable"}})
    assert m.payload["reask"] == "reask_failed"
    assert [it["title"] for it in m.payload["items"]] == ["통과", "걸림", "걸림2"]
    assert len(m.sources) == 3


def test_merge_reask_untouched_cell_same():
    us = _cell_before()
    assert M.merge_reask(us, M.flag_items(us), {}) == us


# ── reask_tally (손셈 한 벡터) ──


def _tally_case():
    c1 = _u("가", "regulation", [_it("p", [DOC + "1"]), _it("f1", [ROOT]), _it("f2", [OUTSIDE + "1"]),
                                 _it("f3", [DOC + "2"], date=OLD)])  # B=3
    c2 = _u("나", "regulation", [_it("f", [ROOT])])  # B=1
    c3 = _u("다", "controversy", [_it("f", [DOC + "3"], date=None), _it("g", [ROOT])])  # B=2, 실패
    before = _chk([c1, c2, c3])
    fl = M.flag_items(before)
    ans = {
        "가|regulation": {"items": [_it("a ok", [DOC + "7"]), _it("a bad", [ROOT]), _it("a rep", [DOC + "1"])],
                          "failure": None},  # 되풀이 1 뺌 → A=2 · 통과 1 · 걸림 1
        "나|regulation": {"items": [_it("b1", [DOC + "8"]), _it("b2", [DOC + "9"]), _it("b3", [DOC + "10"])],
                          "failure": None},  # A=3 통과 3 → 풀린 min(1,3)=1
        "다|controversy": {"items": [], "failure": "provider_error"},
    }
    after = _chk(M.merge_reask(before, fl, ans))
    return fl, after


def test_reask_tally_hand_vector():
    fl, after = _tally_case()
    t = M.reask_tally(fl, after)
    # 가: B3 A2 P1 → 풀린 1 · 잃은 1 · 나: B1 A3 P3 → 풀린 1 · 잃은 0 · 다: 실패 B2 → 풀린 0 · 잃은 0
    assert (t["cells"], t["failedCells"], t["flaggedBefore"]) == (3, 1, 6)
    assert (t["answerItems"], t["answerPassed"], t["answerFlagged"], t["repeatedPassed"]) == (5, 4, 1, 1)
    assert t["lost"] == 1
    assert t["resolved"] == 2
    assert t["resolvedRate"] == {"value": 2 / 6, "num": 2, "den": 6}
    assert t["flaggedAfter"] == 1 + 2
    assert t["reasonsBefore"]["root_only"] == 3 and t["reasonsBefore"]["outside_whitelist"] == 1
    assert t["reasonsAfter"]["root_only"] == 2  # 가 답의 a bad + 다 실패 칸의 g


def test_reask_tally_ignore_whitelist_side_line():
    fl, after = _tally_case()
    t = M.reask_tally(fl, after, ignore=("outside_whitelist",))
    # 가: B2(f1·f3) A2 P1 → 풀린 1 · 잃은 0
    assert t["flaggedBefore"] == 5 and t["resolved"] == 2 and t["lost"] == 0
    assert t["reasonsBefore"]["outside_whitelist"] == 0


# ── call_stats ──


def _call(h, usd=0.02, ins=1000, cr=0, failure=None, text=10, phase="regulation"):
    return {"entry": "call_tools", "phase": phase, "searchCompleted": h, "searchIgnored": None if h is None else 1,
            "hostedCalls": None if h is None else h + 1, "costUsd": usd, "inputTokens": ins,
            "cacheReadTokens": cr, "cacheWriteTokens": 0, "outputTokens": 50, "failure": failure, "textLen": text,
            "wallS": 1.0}


def test_call_stats_median_dist_overcap_and_none():
    cs = [_call(0, 0.01), _call(2, 0.03, cr=500), _call(2, 0.05), _call(3, 0.07, failure="truncated", text=None),
          _call(None, 0.09)]
    s = M.call_stats(cs, cap=2)
    assert s["calls"] == 5 and s["usdMedian"] == 0.05
    assert s["inputMedian"] == 1000 and s["inputMax"] == 1500
    assert s["searchDist"] == {"0": 1, "2": 2, "3": 1}
    assert s["searchUnknown"] == 1 and s["searchTotal"] == 7 and s["searchMedian"] == 2
    assert s["overCap"] == 1 and s["atCap"] == 2
    assert s["truncated"] == 1 and s["failures"] == {"truncated": 1}


def test_call_stats_all_none_is_none_not_zero():
    s = M.call_stats([_call(None), _call(None), _call(None)], cap=2)
    assert s["searchTotal"] is None and s["searchMedian"] is None and s["searchUnknown"] == 3
    assert s["overCap"] is None and s["atCap"] is None and s["atCapNoText"] is None
    assert s["searchDist"] == {}


def test_call_stats_counts_completed_not_core_k_plus_one():
    """맛보기: 상한 k 에서 completed k + 무시된 시도 1 → core hostedCalls k+1. over_cap 은 completed 로만."""
    s = M.call_stats([_call(2), _call(2), _call(1)], cap=2)
    assert s["overCap"] == 0 and s["atCap"] == 2
    assert s["searchTotal"] == 5 and s["coreHostedTotal"] == 8 and s["searchIgnoredTotal"] == 3


def test_call_stats_at_cap_no_text_and_ignored_total():
    cs = [_call(2, text=None), _call(2, text=0), _call(2, text=5), _call(1, text=None)]
    cs[3]["searchIgnored"] = 0
    s = M.call_stats(cs, cap=2)
    assert s["atCap"] == 3 and s["atCapNoText"] == 2
    assert s["searchIgnoredTotal"] == 3


def test_call_stats_core_only_row_is_unknown():
    """searchCompleted 칸이 없는 줄(SDK 셈 없음 — MA1a 실판·모의)은 core hostedCalls 가 있어도 검색 수 모름."""
    s = M.call_stats([{"entry": "call_tools", "phase": "regulation", "hostedCalls": 3, "costUsd": 0.01}], cap=2)
    assert s["searchUnknown"] == 1 and s["searchTotal"] is None and s["overCap"] is None
    assert s["coreHostedTotal"] == 3


def test_call_stats_no_cap_is_none():
    assert M.call_stats([_call(4)], cap=None)["overCap"] is None


# ── quality_rows ──


def test_quality_rows_topic_subsets_denominators():
    us = _chk([_u("T1", "regulation", [_it("a", [DOC + "1"]), _it("b", [ROOT])]),
               _u("T2", "regulation", [_it("c", [OUTSIDE + "1"], date=OLD)]),
               _u("T6", "regulation", [_it("d", [DOC + "2"], date=None)]),
               _u("T1", "controversy", [_it("x", [ROOT])])])
    q = M.quality_rows(us, ["T1", "T2", "T3", "T4", "T5", "T6"])
    assert q["T01~T02"]["urls"] == 3 and q["T01~T05"]["urls"] == 3 and q["전부"]["urls"] == 4
    assert q["T01~T02"]["rootOnly"]["num"] == 1
    assert q["T01~T02"]["whitelistIn"] == {"value": 2 / 3, "num": 2, "den": 3}
    assert q["전부"]["dateUnknown"]["num"] == 1 and q["전부"]["dateIn"]["num"] == 2
    assert q["T01~T05"]["itemsPerCellMedian"] == 1.5


# ── verdict ──


def _a(**kw):
    base = {"cellsDone": 17, "cells": 17, "budgetExceeded": 0, "truncated": 0, "unparsable": 0, "usdMedian": 0.03,
            "inputMedian": 20_000, "overCap": 0, "searchUnknown": 0}
    return {**base, **kw}


def test_verdict_a_branches():
    assert M.verdict_a({"Llo": _a(), "Lmid": _a(), "Llo-b": _a()})["answer"] == "예"
    assert M.verdict_a({"Llo": _a(usdMedian=0.041), "Lmid": _a()})["answer"] == "못 가름"
    assert M.verdict_a({"Llo": _a(inputMedian=30_001), "Lmid": _a()})["answer"] == "못 가름"
    assert M.verdict_a({"Llo": _a(usdMedian=0.0901), "Lmid": _a()})["answer"] == "아니오"
    assert M.verdict_a({"Llo": _a(), "Lmid": _a(usdMedian=0.0901)})["answer"] == "아니오"  # 고침 1: Lmid 도
    assert M.verdict_a({"Llo": _a(), "Lmid": _a(cellsDone=14)})["answer"] == "아니오"
    assert M.verdict_a({"Llo": _a(), "Lmid": _a(cellsDone=15)})["answer"] == "못 가름"
    assert M.verdict_a({"Llo": _a(), "Lmid": _a(unparsable=2)})["answer"] == "못 가름"
    assert M.verdict_a({"Llo": _a(), "Lmid": _a(), "Llo-b": _a(overCap=1)})["answer"] == "못 가름"
    assert M.verdict_a({"Llo": _a()})["answer"] == "못 가름"


def test_verdict_a_search_unknown_is_undecided():
    r = M.verdict_a({"Llo": _a(searchUnknown=1), "Lmid": _a()})
    assert r["answer"] == "못 가름" and "검색 수 모름" in r["why"][0]
    assert M.verdict_a({"Llo": _a(), "Lmid": _a(overCap=None, searchUnknown=17)})["answer"] == "못 가름"
    assert M.verdict_a({"Llo": _a(), "Lmid": _a(overCap=None)})["answer"] == "못 가름"


def test_gate_a_view_carries_search_unknown():
    v = {"stats": M.call_stats([_call(None), _call(1)], cap=2), "cellsDone": 17, "cells": 17, "budgetExceeded": 0,
         "outcomes": {"unparsable": 0}}
    g = M.gate_a_view(v)
    assert g["searchUnknown"] == 1 and g["overCap"] == 0


def _q(wl, date, root, urls=40):
    return {"urls": urls, "whitelistIn": {"value": wl, "num": 0, "den": urls}, "dateIn": {"value": date, "num": 0, "den": urls},
            "rootOnly": {"value": root, "num": 0, "den": urls}}


BASE_Q = _q(0.30, 0.60, 0.05)


def test_verdict_b_branches():
    ok, bad, mid = _q(0.28, 0.57, 0.08), _q(0.19, 0.60, 0.05), _q(0.23, 0.60, 0.05)
    assert M.verdict_b({"Llo": bad, "Llo-b": _q(0.30, 0.49, 0.05)}, BASE_Q)["answer"] == "예"
    assert M.verdict_b({"Llo": ok, "Llo-b": ok}, BASE_Q)["answer"] == "아니오"
    assert M.verdict_b({"Llo": mid, "Llo-b": mid}, BASE_Q)["answer"] == "못 가름"
    assert M.verdict_b({"Llo": bad, "Llo-b": ok}, BASE_Q)["answer"] == "못 가름"  # 갈림 — Llo 하나만 보면 「예」
    assert M.verdict_b({"Llo": _q(0.30, 0.60, 0.16), "Llo-b": _q(0.30, 0.60, 0.16)}, BASE_Q)["answer"] == "예"
    assert M.verdict_b({"Llo": ok, "Llo-b": ok}, None)["answer"] == "못 가름"


def test_verdict_b_missing_repeat_and_small_denominator():
    ok = _q(0.28, 0.57, 0.08)
    r = M.verdict_b({"Llo": ok}, BASE_Q)
    assert r["answer"] == "못 가름" and "되풀이 없음" in r["why"][0]
    assert M.verdict_b({"Llo": _q(0.28, 0.57, 0.08, urls=19), "Llo-b": ok}, BASE_Q)["answer"] == "못 가름"
    assert M.verdict_b({"Lmid": ok}, BASE_Q, arms=("Lmid",))["answer"] == "아니오"


def _t(before, resolved, lost):
    return {"flaggedBefore": before, "resolved": resolved, "lost": lost}


def test_verdict_c_branches():
    assert M.verdict_c(_t(10, 5, 0), 0.40, 0.40)["answer"] == "예"
    assert M.verdict_c(_t(10, 4, 0), 0.40, 0.30)["answer"] == "못 가름"
    assert M.verdict_c(_t(10, 5, 0), 0.40, 0.41)["answer"] == "못 가름"
    assert M.verdict_c(_t(10, 1, 0), 0.40, 0.30)["answer"] == "아니오"
    assert M.verdict_c(_t(10, 2, 0), 0.40, 0.30)["answer"] == "못 가름"
    assert M.verdict_c(_t(10, 5, 0), 0.40, 0.61)["answer"] == "아니오"
    assert M.verdict_c(_t(10, 3, 6), 0.40, 0.30)["answer"] == "아니오"
    assert M.verdict_c(_t(0, 0, 0), 0.40, 0.30)["answer"] == "못 가름"


@pytest.mark.parametrize("res", range(0, 11))
@pytest.mark.parametrize("ans", range(0, 11))
def test_verdict_c_yes_and_no_exclusive(res, ans):
    """풀린 ≤ 답 항목 수(merge 꼴) 아래에서 「예」 조건과 「아니오」 조건은 함께 참일 수 없다."""
    if res > ans:
        return
    b = 10
    t = _t(b, min(res, b), max(0, b - ans))
    yes = t["resolved"] / b >= M.GATE_C["resolvedYes"]
    no = t["resolved"] / b < M.GATE_C["resolvedNo"] or t["lost"] / b > M.GATE_C["lostNo"]
    assert not (yes and no)
    assert M.verdict_c(t, 0.4, 0.4)["answer"] == ("예" if yes else "아니오" if no else "못 가름")


# ── summary ──

WL = 'WHITELIST_CELL73: Final = """\\\n      1. https://ok.example.invalid/\n"""\n'


def _reg(t, url, date=IN):
    return {"status": "done", "selfCheck": {"ok": True}, "items": [
        {"title": f"가상 규정 {t}", "summary": "", "publish_date": date, "primary_url": url}]}


def _con(t):
    return {"status": "done", "selfCheck": None, "items": [{"title": f"가상 사건 {t}", "url": DOC + f"c{t}", "date": IN}]}


def _result(names):
    return {"schemaVersion": "materiality.result@1",
            "scope": {"whitelist": "cell73", "dateRange": "2024-01-01~오늘까지", "sector": "non_financial"},
            "part1": {"keywords": {"status": "done"}, "topics": [
                {"topic": n, "regulation": _reg(n, ROOT if i == 0 else DOC + n), "controversy": _con(n)}
                for i, n in enumerate(names)]}}


def _layout(tmp_path):
    out = tmp_path / "MIC1"
    wl = out / M.WHITELISTS_REL
    wl.parent.mkdir(parents=True)
    wl.write_text(WL, encoding="utf-8")
    (out / "models-luna.json").write_text(json.dumps({"models": {"gpt-6-luna": {
        "pricing": {"input": 0.1, "cacheRead": 0.01, "output": 0.5}, "hostedToolPricing": {"webSearchPerCall": 0.01}}}}))
    names = ["가", "나", "다"]
    (out / "input").mkdir()
    (out / "input" / "input.json").write_text(json.dumps({"topics": [{"topic": n} for n in names]}), encoding="utf-8")
    for arm in M.ARMS:
        a = out / "arms" / arm
        a.mkdir(parents=True)
        (a / "result.json").write_text(json.dumps(_result(names)), encoding="utf-8")
        calls = [{"seq": i, "entry": "call_tools", "phase": ph, "topic": tp, "flowAttempt": 1, "toolsPresent": True,
                  "toolChoice": "required", "model": "gpt-6-luna", "inputTokens": 1000, "cacheReadTokens": 0,
                  "cacheWriteTokens": 0, "outputTokens": 100, "searchCompleted": 1, "searchIgnored": 0,
                  "hostedCalls": 1, "costUsd": 0.00015 + 0.01,
                  "failure": None, "textLen": 5, "wallS": 1.0}
                 for i, (ph, tp) in enumerate([("regulation", n) for n in names] + [("keywords", None)]
                                              + [("controversy", n) for n in names], 1)]
        (a / "calls.jsonl").write_text("".join(json.dumps(c) + "\n" for c in calls), encoding="utf-8")
        (a / "run.json").write_text(json.dumps({"ending": "done", "wallS": 3.0, "spentUsd": 0.07105}))
    g = tmp_path / "iro-empty.json"
    g.write_text(G.dumps(build_empty("2026-11-10")), encoding="utf-8")
    G.freeze(g)
    return out, G.load(g)


def test_summary_not_run_arms_and_side_verdict(tmp_path):
    out, gs = _layout(tmp_path)
    import shutil
    for arm in ("Lmid", "Llo-b"):
        shutil.rmtree(out / "arms" / arm)
    jp, mp = M.build_summary(out, [gs])
    md = mp.read_text(encoding="utf-8")
    for arm in ("Lmid", "Llo-b", "LloR"):
        assert f"| {arm} | — | {M.NOT_RUN} |" in md
    v = json.loads(jp.read_text(encoding="utf-8"))["extra"]["verdicts"]
    assert v["A"]["answer"] == "못 가름" and v["C"]["answer"] == "안 돎"
    assert "LloR" in v["C"]["why"][0] and len(v["C"]["why"]) == 1  # flags 도 안 씀 → 걸림 줄 없음
    assert v["A-Llo만(곁)"]["answer"].endswith(f"(Llo) — Lmid {M.NOT_RUN}")
    assert v["B"]["answer"] == "못 가름"  # 바닥 A1 없는 자료 — 「되풀이 없음」 갈래는 test_verdict_b_missing_repeat_and_small_denominator


def test_summary_flags_only_when_reask_not_run(tmp_path):
    out, gs = _layout(tmp_path)
    M.write_flags(out)  # flags-before.json 만, run.json 없음
    col = M.collect(out)
    assert col["reask"] is None
    assert col["flagsOnly"]["all"] == {"cells": 1, "items": 1, "den": 6, "reasons": {"root_only": 1}}
    v = M.verdicts(col)["C"]
    assert v["answer"] == "안 돎" and "칸 1 · 항목 1/6" in v["why"][1]


def test_summary_deterministic_md(tmp_path):
    out, gs = _layout(tmp_path)
    _, mp = M.build_summary(out, [gs])
    first = tmp_path / "first.md"
    first.write_bytes(mp.read_bytes())
    M.build_summary(out, [gs])
    assert filecmp.cmp(first, mp, shallow=False)


def test_summary_flags_then_reask_path(tmp_path):
    """flags → (드라이버 몫을 흉내: result 사본에 outcome 갈아 끼움) → summary 의 되묻기 줄."""
    out, gs = _layout(tmp_path)
    fp = M.write_flags(out)
    fb = json.loads(fp.read_text(encoding="utf-8"))
    assert fb["targets"] == ["가|regulation"]
    rdir = fp.parent
    res = _result(["가", "나", "다"])
    res["part1"]["topics"][0]["regulation"] = _reg("가", DOC + "new")
    (rdir / "result-reask.json").write_text(json.dumps(res), encoding="utf-8")
    (rdir / "answers.jsonl").write_text(json.dumps({"key": "가|regulation", "failure": None}) + "\n", encoding="utf-8")
    (rdir / "run.json").write_text(json.dumps({"ending": "done"}), encoding="utf-8")  # run.json 없으면 「걸림만」(안 돎)
    col = M.collect(out)
    t = col["reask"]["tally"]
    assert (t["flaggedBefore"], t["answerItems"], t["resolved"], t["lost"]) == (1, 1, 1, 0)
    jp, mp = M.build_summary(out, [gs])
    assert "되묻기" in mp.read_text(encoding="utf-8")
    assert json.loads(jp.read_text(encoding="utf-8"))["extra"]["verdicts"]["C"]["answer"] == "못 가름"  # LloR calls 없음 → USD 없음
