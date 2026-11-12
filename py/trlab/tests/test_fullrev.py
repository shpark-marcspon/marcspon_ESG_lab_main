"""TRX10 fullrev — 지어낸 짝 여섯(문단·제목·표·mixed, 길이 띠 넷): 단위 꼴·sha, 층화, 묶음, 답 나누기, 라벨 표본, 분포. 네트워크 0."""

import hashlib
import json

from trlab import fullrev as F
from trlab import review_probe as R

GLOSS = [{"cat": "조직", "kr": "환경위원회", "en": "Environment Committee"}, {"cat": "제도", "kr": "푸른길", "en": "Blue Road Program"},
         {"cat": "조직", "kr": "안전협의회", "en": "Safety Council"}]


def _pairs():
    """짝 여섯: 문단 <100 · 100~300 · 300~800 · 800+, 제목, 표(+ mixed 하나)."""
    ps = [("p1-b1", "paragraph", "환경위원회는 배출을 줄였습니다. 둘째 문장입니다.", "The Environment Committee cut emissions. Second sentence."),
          ("p1-b2", "paragraph", "푸른길 사업을 이어 갑니다. " * 4, "We continue the Blue Road Program with partners every year. " * 3),
          ("p2-b1", "paragraph", "안전협의회가 점검했습니다. " * 20, "The Safety Council inspected the sites together with staff. " * 8),
          ("p2-b2", "paragraph", "장문의 문장입니다. " * 60, "This is a long sentence about the plan and the sites. " * 18),
          ("p3-b1", "heading", "환경 성과", "Environmental Performance"),
          ("p3-b2", "table", "항목 | 값 | 2025", "Item | Value | 2025"),
          ("p3-b3", "mixed", "혼합 블록입니다. 둘째 문장입니다.", "A mixed block only.")]
    out = []
    for i, (bid, kind, ko, en) in enumerate(ps):
        page = int(bid[1])
        out.append({"ko": [bid], "en": [bid.replace("b", "e")], "page": page, "ko_text": ko, "en_text": en, "kind": kind})
    return out


class _B:
    def __init__(self, i, t):
        self.id, self.text = i, t


def _patch(monkeypatch, pairs=None):
    pairs = _pairs() if pairs is None else pairs
    monkeypatch.setattr(R, "book_pairs", lambda key: pairs)
    import trlab.glossary_check as GC
    monkeypatch.setattr(GC, "load_gloss", lambda key: {"items": GLOSS})
    blocks = {"ko": [_B(p["ko"][0], p["ko_text"]) for p in pairs], "en": [_B(p["en"][0], p["en_text"]) for p in pairs]}
    monkeypatch.setattr(F.common, "load", lambda key, lang: blocks[lang])
    return pairs


def test_units_have_no_text_and_sha_checks(monkeypatch):
    _patch(monkeypatch)
    us = F.make_units("sam")
    assert len(us) == 7 and [u["id"] for u in us][:2] == ["p1-b1", "p1-b2"]
    u = us[0]
    assert "ko" not in u and "en" not in u and "ko_text" not in u
    assert u["ko_sha"] == hashlib.sha256(_pairs()[0]["ko_text"].encode()).hexdigest()[:16] and u["glossary"] == [0]
    assert u["n_ko_sent"] == 2 and u["n_en_sent"] == 2 and u["omit_flag"] is False and u["omit_applies"]
    assert [u["en_band"] for u in us] == ["<100", "100~300", "300~800", "800+", "<100", "<100", "<100"]
    assert us[5]["omit_applies"] is False and us[5]["omit_flag"] is None and us[6]["omit_flag"] is True  # mixed: KR 2 · EN 1 → 걸림
    T = F.Texts("sam")
    ko, en = T.texts(us[0])
    assert ko == _pairs()[0]["ko_text"] and en == _pairs()[0]["en_text"]
    m = T.mat(us[0])
    assert m["glossary"] == [{"kr": "환경위원회", "en": "Environment Committee"}] and m["cat"] == "clean" and m["planted"] is None
    bad = dict(us[0], en_sha="0000000000000000")
    try:
        T.texts(bad)
        raise AssertionError("sha 가 다르면 ValueError")
    except ValueError:
        pass


def test_mixed_omit_flag_reason(monkeypatch):
    _patch(monkeypatch)
    u = [x for x in F.make_units("sam") if x["id"] == "p3-b3"][0]
    assert (u["n_ko_sent"], u["n_en_sent"]) == (2, 1) and u["omit_flag"] is True


def test_en_band_edges():
    assert F.en_band(0) == "<100" and F.en_band(99) == "<100" and F.en_band(100) == "100~300" and F.en_band(299) == "100~300"
    assert F.en_band(300) == "300~800" and F.en_band(799) == "300~800" and F.en_band(800) == "800+" and F.en_band(5000) == "800+"


def test_allocate_proportional_with_min_and_cap():
    a = F.allocate({"a": 700, "b": 200, "c": 100, "d": 3}, 100, min_per=5)
    assert a["d"] == 3 and a["c"] >= 5 and a["b"] >= 5 and sum(a.values()) == 100 and a["a"] > a["b"] > a["c"]
    assert F.allocate({}, 10) == {} and F.allocate({"a": 2}, 10) == {"a": 2}
    a2 = F.allocate({"a": 50, "b": 50}, 10, min_per=1)
    assert a2 == {"a": 5, "b": 5}


def test_stratify_seed_fixed_and_min_per_stratum():
    units = [{"id": f"p{i // 10}-b{i % 10}", "page": i // 10, "kind": "paragraph" if i % 7 else "table", "en_band": F.en_band((i * 37) % 1000)}
             for i in range(400)]
    a = F.stratify(units, 60, 514)
    b = F.stratify(units, 60, 514)
    assert a == b and len(a) == 60 and [u["id"] for u in a] == sorted((u["id"] for u in a), key=lambda s: (int(s[1:].split("-")[0]), s))
    c = F.stratify(units, 60, 515)
    assert [u["id"] for u in c] != [u["id"] for u in a]
    from collections import Counter
    sizes = Counter(F.stratum(u) for u in units)
    got = Counter(F.stratum(u) for u in a)
    for k, n in sizes.items():
        assert got[k] >= min(5, n) and got[k] <= n


def _mats():
    return [{"id": f"u{i}", "book": "sam", "split": "test", "kind": "paragraph", "cat": "clean", "page": 1, "ko": "국문 " * (200 + 50 * i),
             "en": "english text " * (60 + 20 * i), "en_clean": "", "planted": None, "wrong": [],
             "glossary": [GLOSS[i % 3], GLOSS[0]]} for i in range(10)]


def test_bundle_plan_cap_blocks_and_glossary_union():
    mats = _mats()
    bs = F.bundle_plan(mats, 8000, 514)
    assert sum(len(b) for b in bs) == 10 and len(bs) >= 2
    for b in bs:
        assert F.bundle_chars(b) <= 8000
    assert bs == F.bundle_plan(mats, 8000, 514) and bs != F.bundle_plan(mats, 8000, 1) and F.bundle_plan(mats, 8000, 514, shuffle=False)[0][0]["id"] == "u0"
    instr, text, schema = F.build_bundle_prompt(bs[0])
    assert instr == R.V2_INSTR and text.startswith("[Glossary]\n") and "[Block 1]\n[Korean source]\n" in text and f"[Block {len(bs[0])}]" in text
    assert f"[Block {len(bs[0]) + 1}]" not in text and "[English translation]" in text
    g = F.glossary_union(bs[0])
    assert len(g) == len({(x["kr"], x["en"]) for x in g}) and {"kr": "환경위원회", "en": "Environment Committee"} in g
    assert text.count("- 환경위원회 -> Environment Committee") == 1
    big = [dict(mats[0], ko="국" * 9000)]
    assert len(F.bundle_plan(big + mats[:1], 8000, 514)) == 2  # 혼자서도 넘는 블록은 혼자


def test_bundle_schema_adds_block_only_and_v2_untouched():
    items = F.BUNDLE_SCHEMA["properties"]["issues"]["items"]
    assert items["properties"]["block"] == {"type": "integer"} and "block" in items["required"] and items["additionalProperties"] is False
    assert "block" not in R.V2_SCHEMA["properties"]["issues"]["items"]["properties"]
    assert set(items["properties"]) - {"block"} == set(R.V2_SCHEMA["properties"]["issues"]["items"]["properties"])
    assert hashlib.sha256(R.V2_INSTR.encode()).hexdigest()[:16] == "71258cda68336c91"


def test_split_bundle_answer_and_oob():
    raw = json.dumps({"issues": [{"block": 2, "category": "number", "original_span": "13", "suggestion": "12", "reason": "r"},
                                 {"block": 5, "category": "term", "original_span": "x", "suggestion": "y", "reason": "밖"},
                                 {"block": 0, "category": "term", "original_span": "x", "suggestion": "y", "reason": "밖"},
                                 {"block": 2, "category": "year", "original_span": "2024", "suggestion": "2025", "reason": "r"}]})
    s = F.split_bundle_answer(raw, 3)
    assert s["parsed"] and [len(p) for p in s["per_block"]] == [0, 2, 0] and len(s["oob"]) == 2 and s["n_issues"] == 4
    assert "block" not in s["per_block"][1][0] and s["per_block"][1][0]["category"] == "number"
    bad = F.split_bundle_answer("not json", 3)
    assert not bad["parsed"] and bad["per_block"] == [[], [], []]
    mat = dict(_mats()[0], en="The value is 13 in 2024.", planted={"cat": "num", "before": "12", "after": "13"}, cat="num")
    assert F.score_bundle_block(mat, s["per_block"][1])["hit"] == "span" and F.score_bundle_block(mat, [])["hit"] == "miss"
    clean = dict(_mats()[0], en="The value is 13 in 2024.")
    assert F.score_bundle_block(clean, s["per_block"][1])["false_alarm"] and not F.score_bundle_block(clean, [])["false_alarm"]


def test_pick_flags_strata_and_co_flagged_first():
    luna = [{"id": f"u{i}", "kind": "paragraph" if i % 2 else "table", "status": "ok", "flagged": i % 3 != 0, "cats": ["term"] if i % 4 else ["number", "term"]}
            for i in range(60)]
    mini = [{"id": f"u{i}", "kind": "paragraph" if i % 2 else "table", "status": "ok", "flagged": i % 5 != 0, "cats": ["omission"]} for i in range(30)]
    picks = F.pick_flags(luna, mini, 514, n_luna=10, n_mini=5)
    assert picks == F.pick_flags(luna, mini, 514, n_luna=10, n_mini=5)
    lp = [p for p in picks if p["model"] == "gpt-6-luna"]
    mp = [p for p in picks if p["model"] == "gpt-4.1-mini"]
    assert len(lp) == 10 and len(mp) == 5 and all(int(p["id"][1:]) % 3 != 0 for p in lp) and all(int(p["id"][1:]) % 5 != 0 for p in mp)
    luna_ids = {r["id"] for r in luna if r["flagged"]}
    assert all(p["co_flagged"] == (p["id"] in luna_ids) for p in mp)
    assert all(p["co_flagged"] for p in mp)  # 같이 건 짝이 층마다 충분하면 전부 같이 건 짝
    assert F.primary_cat(["term", "number", "term"]) == "term" and F.primary_cat(["number", "term"]) == "number" and F.primary_cat([]) == "none"
    assert len({tuple(p["stratum"]) for p in lp}) >= 2  # 층이 둘 이상 섞인다


def test_pick_omit_by_band(monkeypatch):
    units = [{"id": f"p{i}-b1", "page": i, "kind": "paragraph", "en_band": F.en_band(i * 23), "n_ko_sent": 3, "n_en_sent": 2, "omit_flag": i % 2 == 0}
             for i in range(80)]
    o = F.pick_omit(units, 514, 20)
    assert len(o) == 20 and all(int(x["id"][1:].split("-")[0]) % 2 == 0 for x in o) and o == F.pick_omit(units, 514, 20)
    assert all(set(x.keys()) == {"id", "en_band", "n_ko", "n_en"} for x in o)


def test_dist_counts_with_denominators():
    rows = [{"id": "a", "page": 1, "kind": "paragraph", "en_band": "<100", "status": "ok", "flagged": True, "n_issues": 2, "cats": ["term", "number"], "elapsed_ms": 1000, "usd": 0.0002},
            {"id": "b", "page": 1, "kind": "paragraph", "en_band": "<100", "status": "ok", "flagged": False, "n_issues": 0, "cats": [], "elapsed_ms": 3000, "usd": 0.0001},
            {"id": "c", "page": 2, "kind": "table", "en_band": "800+", "status": "ok", "flagged": True, "n_issues": 4, "cats": ["term"] * 4, "elapsed_ms": 2000, "usd": 0.0003},
            {"id": "d", "page": 2, "kind": "table", "en_band": "800+", "status": "call_failed"}]
    d = F.dist(rows)
    assert d["n"] == 4 and d["n_ok"] == 3 and d["status"] == {"ok": 3, "call_failed": 1} and d["flagged"] == "2/3 (67%)"
    assert d["by_kind"]["paragraph"] == "1/2 (50%)" and d["by_kind"]["table"] == "1/1 (100%)" and d["by_kind"]["heading"] == "0/0"
    assert d["issues_hist"] == {"0": 1, "1": 0, "2": 1, "3+": 1} and d["cats"] == {"term": 5, "number": 1}
    assert d["per_page"] == {"median": 1, "max": 1, "n_pages": 2} and d["elapsed_ms"]["median"] == 2000 and d["elapsed_ms"]["max"] == 3000
    assert abs(d["usd"]["mean"] - 0.0002) < 1e-12 and d["usd"]["median"] == 0.0002
