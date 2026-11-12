"""evalrig.iro.urlcheck — ①~⑦ 갈래마다(지은 URL, 네트워크 0)."""
from evalrig.common.units import Origin, Unit
from evalrig.iro.urlcheck import check_units_urls, headline, in_whitelist, is_root, load_whitelists, norm_url, table_row

O = Origin("v1", "materiality.result@1", "syn", "iro.result-json@1")
WL_TEXT = '''x = 1
WHITELIST_COLAB17: Final = """\\
      1. 가상 기관 (https://www.mcee.go.kr/home/web/main.do)
      2. 가상 (https://eur-lex.europa.eu/)
"""
WHITELIST_CELL73: Final = """\\
      1. https://finance.ec.europa.eu/x
"""
'''


def _item(urls, date=None, ds="missing"):
    return {"title": "t", "date": date, "date_status": ds, "summary": "", "urls": urls, "extra": {}}


def _u(topic, branch, items, wl="cell17", searched=None, cites=None):
    return Unit(module="materiality", unit="iro.topic-branch@1", key=f"{topic}|{branch}", group=topic, origin=O,
                payload={"topic": topic, "branch": branch, "items": items, "searched": searched, "citations": cites,
                         "scope": {"whitelist": wl, "dateRange": "2024-01-01~오늘까지"}})


def test_load_whitelists(tmp_path):
    p = tmp_path / "w.py"
    p.write_text(WL_TEXT, encoding="utf-8")
    wl = load_whitelists(p)
    assert wl == {"cell17": {"mcee.go.kr", "eur-lex.europa.eu"}, "cell73": {"finance.ec.europa.eu"}}


def test_whitelist_host_suffix_not_substring():
    hs = {"mcee.go.kr"}
    assert in_whitelist("mcee.go.kr", hs) and in_whitelist("eng.mcee.go.kr", hs)
    assert not in_whitelist("evil-mcee.go.kr", hs)
    assert not in_whitelist("mcee.go.kr.evil.example", hs)
    assert not in_whitelist("ec.europa.eu", {"finance.ec.europa.eu"})
    assert not in_whitelist(None, hs)


def test_root_only():
    assert is_root("https://a.example.invalid") and is_root("https://a.example.invalid/")
    assert is_root("https://a.example.invalid/home/web/main.do") and is_root("https://a.example.invalid/index.html")
    assert is_root("https://a.example.invalid/fss/main/main.do?menuNo=1")
    assert not is_root("https://a.example.invalid/news/2025/article-1")


def test_checks_each_kind():
    wl = {"cell17": {"mcee.go.kr"}}
    us = [
        _u("가", "regulation", [_item(["https://www.mcee.go.kr/", "ftp://bad", "https://other.example.invalid/law/1"], "2023-12-31", "ok")]),
        _u("나", "regulation", [_item(["https://other.example.invalid/law/1/"], "2024-01-01", "ok")], searched=True),
        _u("가", "controversy", [_item(["https://news.example.invalid/a#frag"], None, "unreadable")], cites=["https://news.example.invalid/a"]),
    ]
    out = check_units_urls(us, wl)
    c = [s.check for s in out[0].sources]
    assert [x["form"] for x in c] == [True, False, True]  # ①
    assert c[0]["root_only"] is True and c[2]["root_only"] is False and c[1]["root_only"] is None  # ②
    assert [x["whitelist"] for x in c] == [True, False, False]  # ③ www 접기
    assert c[0]["date"] == "out"  # ④ 기간 앞끝 앞
    assert out[1].sources[0].check["date"] == "in"
    assert c[2]["repeat"] == 1 and out[1].sources[0].check["repeat"] == 1 and c[0]["repeat"] == 0  # ⑤ 다른 토픽 같은 URL(끝 / 접음)
    cc = out[2].sources[0].check
    assert cc["whitelist"] is None and cc["date"] == "date_unreadable"
    assert cc["evidence"] == "cited" and c[0]["evidence"] == "unknown"  # ⑥
    assert out[1].sources[0].check["searched"] is True
    assert all(s.check["open"] == "not_checked" for u in out for s in u.sources)  # ⑦


def test_same_topic_repeat_not_counted():
    us = [_u("가", "regulation", [_item(["https://x.example.invalid/a"]), _item(["https://x.example.invalid/a"])])]
    out = check_units_urls(us, {})
    assert {s.check["repeat"] for s in out[0].sources} == {0}
    assert out[0].sources[0].check["whitelist"] is False or out[0].sources[0].check["whitelist"] is None


def test_table_and_headline():
    wl = {"cell17": {"mcee.go.kr"}}
    us = check_units_urls([
        _u("가", "regulation", [_item(["https://www.mcee.go.kr/", "https://o.example.invalid/x/1"], "2023-01-01", "ok")], searched=False),
        _u("나", "regulation", [], searched=True),
    ], wl)
    r = table_row(us, "regulation")
    assert r["URL"] == 2 and r["루트만"] == 1 and r["기간 밖"] == 2
    assert r["③ 화이트리스트 안 rate"] == {"value": 0.5, "num": 1, "den": 2}
    assert r["검색 호출 있던 칸 rate"] == {"value": 0.5, "num": 1, "den": 2}
    assert r["⑥ 인용 안 rate"]["status"] == "no_denominator"
    assert r["토픽당 URL 중앙"] == 1.0
    h = headline(us)
    assert h["n"] == 2 and h["outside_whitelist"]["num"] == 1 and h["root_only"]["num"] == 1 and h["out_of_range"]["num"] == 2
    assert norm_url("https://WWW.A.example.invalid/p/") == "a.example.invalid/p"
