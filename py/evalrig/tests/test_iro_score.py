"""evalrig.iro.score — 정답 0 → no_denominator, 지은 정답 → 재현·정밀·출처."""
from evalrig.common import gold as G
from evalrig.iro.gold import build_empty
from evalrig.iro.score import TITLE_SIM, code_metrics, host_of, score
from evalrig.common.units import Origin, Source, Unit

O = Origin("v1", "materiality.result@1", "syn", "iro.result-json@1")


def _u(topic, branch, titles, urls=None, searched=None):
    items = [{"title": t, "date": None, "date_status": "missing", "summary": "", "urls": [u] if u else [], "extra": {}}
             for t, u in zip(titles, urls or [None] * len(titles))]
    return Unit(module="materiality", unit="iro.topic-branch@1", key=f"{topic}|{branch}", group=topic,
                payload={"topic": topic, "branch": branch, "items": items, "searched": searched, "citations": None, "scope": {}},
                sources=tuple(Source("web", u) for u in (urls or []) if u), status="ok" if items else "missing", origin=O)


def test_empty_gold_no_denominator():
    gs = build_empty("2026-01-01")
    r = score([_u("가", "regulation", ["a"])], gs)
    assert r["gold"]["recall"]["status"] == "no_denominator"
    assert r["gold"]["precision"]["status"] == "unmeasurable"  # partial
    assert r["code"]["regulation"]["items"] == 1


def test_code_metrics():
    us = [_u("가", "regulation", ["a", "b"], ["https://x.example.invalid/1", None], searched=True),
          _u("나", "regulation", [], searched=False), _u("가", "controversy", ["c"], ["https://y.example.invalid/"])]
    m = code_metrics(us)
    assert m["regulation"]["items"] == 2 and m["regulation"]["urls"] == 1 and m["regulation"]["missing"] == 1
    assert m["regulation"]["searched_rate"] == {"value": 0.5, "num": 1, "den": 2}
    assert m["controversy"]["searched_rate"]["status"] == "no_denominator"
    assert m["regulation"]["date_status"] == {"missing": 2}


def test_with_synthetic_gold():
    gs = build_empty("2026-01-01")
    gs["coverage"] = "exhaustive"
    gs["synthetic"] = True
    gs["items"] = [
        {"topic": "가", "branch": "regulation", "title": "대기 배출 기준 개정", "urls": ["https://www.x.example.invalid/a"]},
        {"topic": "가", "branch": "regulation", "title": "전혀 다른 제목", "urls": []},
        {"topic": "가", "branch": "controversy", "title": "사건 하나", "urls": []},
    ]
    us = [_u("가", "regulation", ["대기 배출 기준 개정안", "무관"], ["https://x.example.invalid/b", None]),
          _u("가", "controversy", ["사건 하나"])]
    r = score(us, gs)["gold"]
    assert r["recall"] == {"value": 2 / 3, "num": 2, "den": 3}
    assert r["precision"] == {"value": 2 / 3, "num": 2, "den": 3}
    assert r["source_ok"] == {"value": 0.5, "num": 1, "den": 2}  # www 접은 host 같음 1, 정답 URL 없음 1
    assert TITLE_SIM == 0.6


def test_branch_not_mixed():
    gs = build_empty("2026-01-01")
    gs["items"] = [{"topic": "가", "branch": "controversy", "title": "사건", "urls": []}]
    r = score([_u("가", "regulation", ["사건"])], gs)["gold"]
    assert r["recall"]["num"] == 0  # 다른 갈래 항목과 짝짓지 않음


def test_host_of():
    assert host_of("https://WWW.Ex.example.invalid/p?q") == "ex.example.invalid"
    assert host_of("ftp://x") is None and host_of("not a url") is None
