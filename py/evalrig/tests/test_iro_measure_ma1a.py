"""evalrig.iro.measure_ma1a — 지은 자료(실재 글 0, 네트워크 0)."""
import filecmp
import json

from evalrig.common import gold as G
from evalrig.common.units import Origin, Unit
from evalrig.iro import measure_ma1a as M
from evalrig.iro.gold import build_empty

O = Origin("v1", "materiality.result@1", "syn", "iro.result-json@1")


def _u(topic, branch, titles=(), urls=None):
    items = [{"title": t, "date": None, "date_status": "missing", "summary": "", "extra": {},
              "urls": [] if urls is None else [urls[i]]} for i, t in enumerate(titles)]
    return Unit(module="materiality", unit="iro.topic-branch@1", key=f"{topic}|{branch}", group=topic, origin=O,
                payload={"topic": topic, "branch": branch, "items": items, "searched": None, "citations": None,
                         "scope": {"whitelist": "cell73", "dateRange": "2024-01-01~오늘까지"}})


def _c(phase, topic, hosted, attempt=1, failure=None):
    return {"phase": phase, "topic": topic, "hostedCalls": hosted, "flowAttempt": attempt, "failure": failure}


# ── searched ──


def test_searched_fills_sum_per_topic_branch():
    us = [_u("가", "regulation"), _u("가", "controversy"), _u("나", "regulation"), _u("가", "impact")]
    calls = [_c("regulation", "가", 2), _c("controversy", "가", 0), _c("keywords", None, 5), _c("regulation", "나", 1)]
    out = M.searched(us, calls)
    assert [u.payload["searched"] for u in out] == [2, 0, 1, None]
    assert out[0].payload["searchedLowerBound"] is False
    assert "searchedLowerBound" not in out[3].payload  # 2부 단위는 손대지 않음
    assert us[0].payload["searched"] is None  # 원 단위 그대로


def test_searched_lower_bound_retry_and_truncated():
    us = [_u("가", "regulation"), _u("나", "regulation")]
    calls = [_c("regulation", "가", 1), _c("regulation", "가", 1, attempt=2), _c("regulation", "나", 0, failure="truncated"),
             {**_c("controversy", "가", 1), "coreAttempts": 2}, {**_c("controversy", "나", 1), "coreAttempts": 1}]
    us += [_u("가", "controversy"), _u("나", "controversy")]
    out = M.searched(us, calls)
    assert out[0].payload["searched"] == 2 and out[0].payload["searchedLowerBound"] is True
    assert out[1].payload["searched"] == 0 and out[1].payload["searchedLowerBound"] is True
    assert out[2].payload["searchedLowerBound"] is True  # core 안 공급사 재시도
    assert out[3].payload["searchedLowerBound"] is False


def test_searched_missing_is_none_not_zero():
    us = [_u("가", "regulation"), _u("나", "controversy")]
    out = M.searched(us, [_c("regulation", "가", None)])
    assert out[0].payload["searched"] is None
    assert out[1].payload["searched"] is None  # 칸 줄 없음


# ── 같은 규제 ──


def test_same_regulation_across_topics_title_and_url():
    us = [
        _u("가", "regulation", ["가상 법 A", "가상 법 A 시행령", "가상 법 A"], urls=["https://x.example.invalid/a", "https://x.example.invalid/b", "https://x.example.invalid/a"]),
        _u("나", "regulation", ["가상  법 a.", "다른 법"], urls=["https://www.x.example.invalid/a/", "https://y.example.invalid/c"]),
        _u("다", "regulation", ["가상 법 A 시행령 개정"], urls=["https://z.example.invalid/q"]),
        _u("나", "controversy", ["다른 법"]),  # 컨트로버시는 안 셈
    ]
    s = M.same_regulation_across_topics(us)
    # 제목: 「가상 법 a」 열쇠만 두 토픽(가·나) — 같은 토픽 안 되풀이는 토픽 하나, 「시행령」은 부분 문자열이라 다른 열쇠
    assert s["title"]["keys"] == 1
    assert s["title"]["items"] == 3 and s["title"]["den"] == 6
    assert s["title"]["rate"] == {"value": 0.5, "num": 3, "den": 6}
    # URL: x/a 가 두 토픽(www·끝 / 접음)
    assert s["url"]["keys"] == 1 and s["url"]["urls"] == 3 and s["url"]["den"] == 6


def test_same_regulation_same_topic_repeat_not_counted():
    s = M.same_regulation_across_topics([_u("가", "regulation", ["가상 법", "가상 법"], urls=["https://a.example.invalid/1"] * 2)])
    assert s["title"]["keys"] == 0 and s["title"]["items"] == 0 and s["title"]["den"] == 2
    assert s["url"]["keys"] == 0


def test_same_regulation_substring_not_same_and_empty_title():
    s = M.same_regulation_across_topics([_u("가", "regulation", ["가상 기준", ""]), _u("나", "regulation", ["가상 기준 해설", ""])])
    assert s["title"]["keys"] == 0 and s["title"]["emptyTitle"] == 2 and s["title"]["den"] == 4


def test_same_regulation_no_denominator():
    s = M.same_regulation_across_topics([])
    assert s["title"]["rate"]["status"] == "no_denominator" and s["url"]["rate"]["status"] == "no_denominator"


# ── 판정기 ──


def test_parse_judge_j4_three_labels_and_unparsed():
    assert [M.parse_judge({"label": x}, "j4") for x in M.J4_LABELS] == [{"label": x} for x in M.J4_LABELS]
    for bad in ({"label": "maybe"}, {}, "not json", '{"label": 1}', None, ["related"]):
        assert M.parse_judge(bad, "j4") == {"status": "unparsed"}
    assert M.parse_judge('{"label": "unrelated"}', "j4") == {"label": "unrelated"}


def test_parse_judge_j3_and_unparsed():
    assert M.parse_judge({"mixed": True, "cols": ["controversy"]}, "j3") == {"mixed": True, "cols": ["controversy"]}
    assert M.parse_judge({"mixed": False, "cols": []}, "j3") == {"mixed": False, "cols": []}
    for bad in ({"mixed": "yes", "cols": []}, {"mixed": True}, {"mixed": True, "cols": ["다른 칸"]}, "{"):
        assert M.parse_judge(bad, "j3") == {"status": "unparsed"}


def test_split_answer_missing_duplicate_broken():
    text = json.dumps({"rows": [{"id": "a", "label": "related"}, {"id": "b", "label": "unrelated"},
                                {"id": "b", "label": "related"}, {"id": "z", "label": "related"}]})
    out = M.split_answer(text, ["a", "b", "c"], "j4")
    assert out == {"a": {"label": "related"}, "b": {"status": "unparsed"}, "c": {"status": "unparsed"}}
    assert M.split_answer("{깨짐", ["a"], "j4") == {"a": {"status": "unparsed"}}
    assert M.split_answer(None, ["a"], "j3") == {"a": {"status": "unparsed"}}


def test_tally_judge_denominator_keeps_unparsed():
    rows = [{"group": "A1", "parsed": p} for p in ({"label": "related"}, {"label": "unrelated"}, {"status": "unparsed"},
                                                    {"label": "unknown"})]
    t = M.tally_judge(rows, "j4")["A1"]
    assert (t["related"], t["unrelated"], t["unknown"], t["unparsed"], t["den"]) == (1, 1, 1, 1, 4)
    assert t["rate"] == {"value": 0.25, "num": 1, "den": 4}
    t3 = M.tally_judge([{"group": "g", "parsed": {"mixed": True, "cols": []}}, {"group": "g", "parsed": {"status": "unparsed"}}], "j3")["g"]
    assert (t3["mixed"], t3["notMixed"], t3["unparsed"], t3["den"]) == (1, 0, 1, 2)


# ── 비용 ──

PRICES = {"m": {"input": 2.5, "cacheRead": 1.25, "output": 10.0, "webSearchPerCall": 0.01},
          "j": {"input": 0.1, "cacheRead": 0.01, "output": 0.5, "webSearchPerCall": None}}


def test_cost_hand_vector_seven_digits():
    # core 줄: 입력(캐시 뺌) 5,253 × 2.5 + 캐시 1,000 × 1.25 + 출력 1,131 × 10 = 13,132.5 + 1,250 + 11,310 → 0.0256925 + 검색 2 × 0.01
    core = {"model": "m", "inputTokens": 5253, "cacheReadTokens": 1000, "outputTokens": 1131, "hostedCalls": 2, "costUsd": 0.0456925}
    # semgraph 줄: 입력(캐시 듦) 3,000 중 캐시 1,000 → 2,000 × 0.1 + 1,000 × 0.01 + 출력 700 × 0.5 = 200 + 10 + 350 → 0.00056
    sg = {"model": "j", "inputTokens": 3000, "cachedTokens": 1000, "outputTokens": 700, "costUsd": 0.00056}
    assert M.round7(M.call_cost(core, PRICES)) == 0.0456925
    assert M.round7(M.call_cost(sg, PRICES)) == 0.00056
    c = M.cost([core, sg], PRICES)
    assert c == {"calls": 2, "handUsd": 0.0462525, "recordedUsd": 0.0462525, "same": True}
    assert M.cost([{**core, "costUsd": 0.0256925}], PRICES)["same"] is False
    # 캐시 쓰기: 10,000 × 0.125 / 1M = 0.00125 (칸이 없으면 0)
    pw = {"j": {**PRICES["j"], "cacheWrite": 0.125}}
    row = {"model": "j", "inputTokens": 0, "cacheReadTokens": 0, "outputTokens": 0, "cacheWriteTokens": 10000}
    assert M.round7(M.call_cost(row, pw)) == 0.00125
    assert M.call_cost({**row, "cacheWriteTokens": None}, pw) == 0


def test_load_prices():
    p = M.load_prices({"models": {"m": {"pricing": {"input": 1, "cacheRead": 0.5, "output": 2},
                                        "hostedToolPricing": {"webSearchPerCall": 0.01}},
                                  "n": {"pricing": {"input": 1, "cacheRead": 0.5, "output": 2}}}})
    assert p["m"]["webSearchPerCall"] == 0.01 and p["n"]["webSearchPerCall"] is None


# ── summary ──

WL = 'WHITELIST_CELL73: Final = """\\\n      1. https://law.example.invalid/\n"""\n'


def _reg_done(t):
    return {"status": "done", "selfCheck": {"ok": True}, "items": [
        {"title": "가상 규정", "summary": "", "publish_date": "2025-01-02", "primary_url": f"https://law.example.invalid/{t}"}]}


def _failed(kind):
    return {"status": "failed", "failure": {"kind": kind, "message": "m"}, "items": []}


def _result():
    """A0 꼴(실판처럼 상한에 닿은 판): 규제 가·나 done, 다 unparsable · 컨트로버시 셋 다 budget_exceeded(calls 줄 없음)."""
    return {"schemaVersion": "materiality.result@1",
            "scope": {"whitelist": "cell73", "dateRange": "2024-01-01~오늘까지", "sector": "non_financial"},
            "part1": {"topics": [
                {"topic": "가", "regulation": _reg_done("가"), "controversy": _failed("budget_exceeded")},
                {"topic": "나", "regulation": _reg_done("나"), "controversy": _failed("budget_exceeded")},
                {"topic": "다", "regulation": _failed("unparsable"), "controversy": _failed("budget_exceeded")}]}}


def _layout(tmp_path):
    out = tmp_path / "MA1a"
    wl = out / M.WHITELISTS_REL
    wl.parent.mkdir(parents=True)
    wl.write_text(WL, encoding="utf-8")
    (out / "prices.json").write_text(json.dumps({"models": {"m": {"pricing": {"input": 2.5, "cacheRead": 1.25, "output": 10.0},
                                                                   "hostedToolPricing": {"webSearchPerCall": 0.01}},
                                                             "gpt-6-luna": {"pricing": {"input": 0.1, "cacheRead": 0.01, "output": 0.5}}}}))
    a = out / "arms" / "A0"
    a.mkdir(parents=True)
    (a / "result.json").write_text(json.dumps(_result()), encoding="utf-8")
    calls = [{"seq": i, "entry": "call_tools", "phase": ph, "topic": tp, "flowAttempt": 1, "toolsPresent": True,
              "toolChoice": "auto", "model": "m", "inputTokens": 1000, "cacheReadTokens": 0, "outputTokens": 100,
              "hostedCalls": h, "costUsd": 0.0035 + 0.01 * h, "failure": None}
             for i, (ph, tp, h) in enumerate([("regulation", "가", 1), ("regulation", "나", 0), ("regulation", "다", 1),
                                               ("keywords", None, 0)], 1)]  # 컨트로버시 줄 없음(budget_exceeded)
    (a / "calls.jsonl").write_text("".join(json.dumps(c) + "\n" for c in calls), encoding="utf-8")
    (a / "run.json").write_text(json.dumps({"ending": "done", "wallS": 3.0, "spentUsd": 0.034, "blocked": None}))
    b = out / "arms" / "A3"
    b.mkdir()
    (b / "run.json").write_text(json.dumps({"ending": "failed", "blocked": "가상 까닭"}))
    c = out / "arms" / "A2"
    c.mkdir()
    (c / "run.json").write_text(json.dumps({"ending": "skipped", "skipped": "가상 까닭", "blocked": None}))
    j = out / "judge"
    j.mkdir()
    (j / "j4.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [
        {"id": "1", "group": "A1", "answer": {"label": "unrelated"}}, {"id": "2", "group": "A1", "answer": {"status": "unparsed"}}]))
    (j / "j3.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [
        {"id": "1", "group": "A1 1부|regulation", "answer": {"mixed": True, "cols": ["controversy"]}},
        {"id": "2", "group": "A1 2부|impact", "answer": {"mixed": False, "cols": []}}]))
    g = tmp_path / "iro-empty.json"
    g.write_text(G.dumps(build_empty("2026-11-09")), encoding="utf-8")
    G.freeze(g)
    return out, G.load(g)


def test_summary_deterministic_md(tmp_path):
    out, gs = _layout(tmp_path)
    _, mp = M.build_summary(out, [gs])
    first = tmp_path / "first.md"
    first.write_bytes(mp.read_bytes())
    M.build_summary(out, [gs])
    assert filecmp.cmp(first, mp, shallow=False)


def test_summary_splits_code_and_judge_rows(tmp_path):
    out, gs = _layout(tmp_path)
    jp, mp = M.build_summary(out, [gs])
    rows = json.loads(jp.read_text(encoding="utf-8"))["rows"]
    code = [r for r in rows if r["표"] == "판"]
    judge = [r for r in rows if r["표"] == "판정기"]
    assert code and judge and len(code) + len(judge) == len(rows)
    assert {r["지표 갈래"] for r in code} == {"code"}
    assert {r["지표 갈래"] for r in judge} == {"judge:luna"}
    assert all(r["주"] == M.JUDGE_NOTE for r in judge)
    whole = next(r for r in code if r["판"] == "A0" and r["갈래"] == "판 전체")
    # 분모는 calls 줄이 있는(끝난) 칸만 — 컨트로버시 셋(budget_exceeded, 줄 없음)은 빠진다(감독 답 3)
    assert whole["실제 검색 수"] == 2 and whole["검색 있던 칸 rate"] == {"value": 2 / 3, "num": 2, "den": 3}
    con = next(r for r in code if r["판"] == "A0" and r["갈래"] == "controversy")
    assert con["검색 있던 칸 rate"] == {"status": "no_denominator", "num": 0, "den": 0}
    reg = next(r for r in code if r["판"] == "A0" and r["갈래"] == "regulation")
    assert reg["검색 있던 칸 rate"] == {"value": 2 / 3, "num": 2, "den": 3}
    assert whole["USD 손셈"] == whole["USD 기록"] == 0.034 and whole["USD 같음"] is True
    # JSON 못 읽음 = unparsable 만, 실패(갈래) = 모든 failed — 분자가 다르다
    assert whole["JSON 못 읽음"] == "1/6"
    assert whole["실패(갈래)"] == "4/6 {'budget_exceeded': 3, 'unparsable': 1}"
    a3 = [r for r in code if r["판"] == "A3"]
    assert len(a3) == 1 and a3[0]["끝"] == "막힘: 가상 까닭"  # 빈 줄이 아니라 막힘 줄
    a2 = [r for r in code if r["판"] == "A2"]
    assert len(a2) == 1 and a2[0]["끝"] == "뺌: 가상 까닭"  # 뺀 판은 막힘과 다른 줄
    by = {r["판정 대상"]: r for r in judge}
    assert by["J4 A1"]["unparsed"] == 1 and by["J4 A1"]["분모"] == 2
    assert by["J3 A1 1부|합"]["분모"] == 1 and by["J3 전체"]["분모"] == 2
    assert by["J3 전체"]["라벨 대조"]["status"] == "unmeasurable"
    assert "판정기 = luna, 정답 아님" in mp.read_text(encoding="utf-8")
