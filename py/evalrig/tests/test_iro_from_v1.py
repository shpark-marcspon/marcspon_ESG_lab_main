"""evalrig.iro.from_v1 — 지은 결과(실재 글 0)."""
import json

import pytest

from evalrig.iro import from_v1 as V


def _reg(url, ev=None, pub=None):
    return {"title": "가상 제도", "summary": "가" * 100, "event_date": ev, "publish_date": pub, "status": "s", "primary_url": url}


def _con(url, date):
    return {"title": "가상 사건", "summary": "요약", "date": date, "severity": "중간", "url": url}


RESULT = {
    "schemaVersion": "materiality.result@2",
    "scope": {"whitelist": "cell73", "dateRange": "2024-01-01~오늘까지", "sector": "non_financial"},
    "part1": {"topics": [
        {"topic": "토픽  가", "regulation": {"status": "done", "items": [
            {**_reg("https://a.example.invalid/x", ev="2025-03-01"), "region": "KR"},
            {**_reg("https://b.example.invalid/", pub="2024/1/5"), "region": "EU"}]},
         "controversy": {"status": "done", "items": [_con("https://c.example.invalid/n", "2025-13-01"), _con("", None)]}},
        {"topic": "토픽 나", "regulation": {"status": "failed", "items": []}, "controversy": {"status": "done", "items": []}},
    ]},
    "part2": {"status": "done", "topics": [{"topic": "토픽  가", "subtopics": [
        {"name": "서브", "impact": {"status": "done", "parsed": {"impacts": [{"impact_definition": "영향", "impact_type": "실제"}]}},
         "financial": {"status": "done", "parsed": {"impacts": []}},
         "pathway": {"status": "failed", "parsed": None}}]}]},
}


def test_result_units_keys_and_branches():
    us = V.convert_result(RESULT, run="syn")
    keys = [u.key for u in us]
    assert keys == ["토픽 가|regulation", "토픽 가|controversy", "토픽 나|regulation", "토픽 나|controversy",
                    "토픽 가|impact", "토픽 가|financial", "토픽 가|pathway"]
    by = {u.key: u for u in us}
    assert all(u.payload["branch"] == u.key.split("|")[1] for u in us)  # 갈래가 섞이지 않음
    assert by["토픽 가|regulation"].group == "토픽 가"
    assert [u.status for u in us] == ["ok", "ok", "failed", "missing", "ok", "missing", "failed"]
    assert by["토픽 가|regulation"].origin.to_dict() == {"engine": "v1", "schema": "materiality.result@2", "run": "syn",
                                                       "converter": "iro.result-json@1"}


def test_dates_and_urls():
    by = {u.key: u for u in V.convert_result(RESULT, run="syn")}
    reg = by["토픽 가|regulation"].payload["items"]
    assert [(i["date"], i["date_status"]) for i in reg] == [("2025-03-01", "ok"), ("2024-01-05", "ok")]  # event 없으면 publish
    assert len(reg[0]["summary"]) == 80
    con = by["토픽 가|controversy"].payload["items"]
    assert [(i["date"], i["date_status"]) for i in con] == [(None, "unreadable"), (None, "missing")]
    assert [s.ref for s in by["토픽 가|controversy"].sources] == ["https://c.example.invalid/n"]
    assert [s.ref for s in by["토픽 가|regulation"].sources] == ["https://a.example.invalid/x", "https://b.example.invalid/"]
    assert by["토픽 가|regulation"].payload["scope"]["whitelist"] == "cell73"


@pytest.mark.parametrize("v,exp", [("2025-1-2", ("2025-01-02", "ok")), ("unknown", (None, "missing")), ("", (None, "missing")),
                                   ("2025년", (None, "unreadable")), ("2025-02-30", (None, "unreadable"))])
def test_parse_date(v, exp):
    assert V.parse_date(v) == exp


def test_wrong_schema():
    with pytest.raises(ValueError):
        V.convert_result({"schemaVersion": "x"}, run="r")


def _raw():
    return json.dumps({
        "regulation_updates_by_topic": {"토픽 가": {"parsed": {"results": {"KR": [_reg("https://a.example.invalid/1")]}}, "raw": None, "parse_error": None},
                                        "토픽 나": {"parsed": None, "raw": "x", "parse_error": "bad"}},
        "controversies_by_topic": {"토픽 가": {"json": {"results": {"items": [_con("https://c.example.invalid/2", "2025-01-01")]}}, "raw_text": None, "parse_error": None}},
    }, ensure_ascii=False)


def test_raw_json():
    us = V.convert_raw_json(_raw(), run="syn")
    assert [(u.key, u.status) for u in us] == [("토픽 가|regulation", "ok"), ("토픽 가|controversy", "ok"), ("토픽 나|regulation", "failed")]
    assert us[0].payload["items"][0]["extra"]["region"] == "KR"


def test_raw_json_truncated_and_broken():
    t = V.convert_raw_json("{" + "a" * (V.XLSX_CELL_MAX - 1), run="syn")
    assert len(t) == 1 and t[0].status == "failed" and t[0].payload["note"] == "truncated"
    b = V.convert_raw_json("{not json", run="syn")
    assert b[0].status == "failed" and b[0].payload["note"] == "parse_error"


def _resp(body, searched=False, cites=()):
    out = [{"type": "web_search_call"}] if searched else []
    out.append({"type": "message", "content": [{"type": "output_text", "text": body,
                                                 "annotations": [{"type": "url_citation", "url": c} for c in cites]}]})
    return {"output": out}


def test_colab():
    reg = json.dumps({"results": {"KR": [_reg("https://a.example.invalid/1")], "EU": []}})
    con = "```json\n" + json.dumps({"results": {"items": [_con("https://c.example.invalid/2", "2025-01-01")]}}) + "\n```"
    resp = {"c17": [_resp(reg), _resp("깨진 글")], "c22": [_resp(con, True, ["https://c.example.invalid/2"]), _resp("{}")]}
    us = V.convert_colab(resp, run="IR0", topics=["토픽 가", "토픽 나"], scope={"whitelist": "cell17"})
    by = {u.key: u for u in us}
    assert by["토픽 가|regulation"].payload["searched"] is False and by["토픽 가|regulation"].payload["citations"] is None
    assert by["토픽 나|regulation"].status == "failed"
    assert by["토픽 가|controversy"].payload["searched"] is True
    assert by["토픽 가|controversy"].payload["citations"] == ["https://c.example.invalid/2"]
    assert by["토픽 나|controversy"].status == "missing"
    with pytest.raises(ValueError, match="토픽"):
        V.convert_colab(resp, run="IR0", topics=["하나"])


def test_topics_map(tmp_path):
    p = tmp_path / "t.txt"
    p.write_text("T01\t토픽 가\nT02\t토픽 나\n\n", encoding="utf-8")
    assert V.read_topics_map(p) == ["토픽 가", "토픽 나"]
