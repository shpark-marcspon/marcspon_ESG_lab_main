"""TRX5 span_incons — 지어낸 짝 넷·용어 둘(위원회 이름은 지어낸 것). 네트워크 0. KR 용어는 끝말 뒤가 한글이 아닐 때만 잡힌다(glossary_cands.SUFFIX)."""

import json

from trlab import span_incons as SI

GLOSS = [{"kr": "지속가능경영위원회", "en": "Sustainability Committee"}]


def pairs4():
    return [
        {"id": "p1-b1", "page": 1, "ko": "지속가능경영위원회, 경영위원회 모두 안건을 본다.",
         "en": "The Sustainability Committee reviews agenda items with the Management Committee."},
        {"id": "p2-b1", "page": 2, "ko": "지속가능경영위원회 목표 설정. 경영위원회 별도 개최.",
         "en": "The Sustainable Management Committee set the targets. The Management Committee meets separately."},
        {"id": "p3-b1", "page": 3, "ko": "경영위원회 분기 개최. 지속가능경영위원회 동일.",
         "en": "Management Committees meet quarterly, and so does the Sustainability Committee."},
        {"id": "p4-b1", "page": 4, "ko": "지속가능경영위원회 보고. 경영위원회 보고.",
         "en": "Report to the committee. Report to the Management Committee."},
    ]


def test_pool_masks_longer_term_and_counts_min_occ():
    P = SI.pool_from(pairs4(), GLOSS)
    # 「경영위원회」⊂「지속가능경영위원회」: 긴 것을 가린 뒤에도 경영위원회가 따로 있어 네 짝 모두 셈
    assert set(P["terms"]) == {"지속가능경영위원회", "경영위원회"}
    assert len(P["occ"]["지속가능경영위원회"]) == 4 and len(P["occ"]["경영위원회"]) == 4
    assert len(P["items"]) == 8
    # 가림: 긴 용어만 있는 짝에서는 짧은 용어를 세지 않는다
    only_long = [{"id": "x", "page": 1, "ko": "지속가능경영위원회 보고.", "en": "x"}] * 1
    occ = SI.occ_by_term([dict(only_long[0], id=f"x{i}") for i in range(3)], {"지속가능경영위원회", "경영위원회"})
    assert "경영위원회" not in occ and len(occ["지속가능경영위원회"]) == 3
    # 짝 3개 미만 용어는 안 센다
    assert "없는위원회" not in SI.pool_from(pairs4(), GLOSS + [{"kr": "없는위원회", "en": "None Committee"}])["terms"]


def test_batches_under_max_chars_and_big_pair_alone():
    P = SI.pool_from(pairs4(), GLOSS)
    one = SI.batches(P["items"], P["pairs"], max_chars=100000)
    assert len(one) == 1 and [p["id"] for p in one[0]["pairs"]] == ["p1-b1", "p2-b1", "p3-b1", "p4-b1"]
    assert one[0]["pairs"][0]["terms"] == ["경영위원회", "지속가능경영위원회"]
    small = SI.batches(P["items"], P["pairs"], max_chars=150)
    assert len(small) == 4 and all(len(b["pairs"]) == 1 for b in small)  # 넘는 짝은 혼자
    mid = SI.batches(P["items"], P["pairs"], max_chars=260)
    assert all(sum(len(p["ko"]) + len(p["en"]) for p in b["pairs"]) <= 260 or len(b["pairs"]) == 1 for b in mid)
    assert sum(len(b["pairs"]) for b in mid) == 4
    txt = SI.prompt(one[0])
    assert "### pair p1-b1 (page 1)" in txt and "terms: 경영위원회 | 지속가능경영위원회" in txt and "EN: The Sustainability" in txt


def test_check_span_in_en_case_space_insensitive_else_nospan():
    b = SI.batches(SI.pool_from(pairs4(), GLOSS)["items"], pairs4(), max_chars=100000)[0]
    ans = [
        {"pair": "p1-b1", "term": "지속가능경영위원회", "span": "sustainability   committee", "kind": "exact"},
        {"pair": "p1-b1", "term": "경영위원회", "span": "Board of Directors", "kind": "exact"},  # EN 밖 → nospan
        {"pair": "p2-b1", "term": "지속가능경영위원회", "span": "Sustainable Management Committee", "kind": "exact"},
        {"pair": "p2-b1", "term": "경영위원회", "span": "", "kind": "omitted"},
        {"pair": "p3-b1", "term": "경영위원회", "span": "Management Committees", "kind": "exact"},
        {"pair": "p3-b1", "term": "지속가능경영위원회", "span": "the Sustainability Committee", "kind": "exact"},
        {"pair": "p4-b1", "term": "지속가능경영위원회", "span": "the committee", "kind": "variant"},
        # p4 경영위원회 답 없음 → missing
    ]
    rows = {(r["pair"], r["term"]): r for r in SI.check(b, ans)}
    assert rows[("p1-b1", "지속가능경영위원회")]["kind"] == "exact" and rows[("p1-b1", "지속가능경영위원회")]["key"] == "sustainability committee"
    assert rows[("p1-b1", "경영위원회")]["kind"] == "nospan" and rows[("p1-b1", "경영위원회")]["key"] == ""
    assert rows[("p2-b1", "경영위원회")]["kind"] == "omitted"
    assert rows[("p3-b1", "경영위원회")]["key"] == "management committee"  # 끝 s
    assert rows[("p3-b1", "지속가능경영위원회")]["key"] == "sustainability committee"  # 앞 the
    assert rows[("p4-b1", "경영위원회")]["kind"] == "missing"
    assert len(rows) == 8


def rows_for_judge():
    mk = lambda t, k, s, pg, pair="p": {"pair": f"{pair}{pg}", "page": pg, "term": t, "span": s, "kind": k, "key": SI.norm_key(s)}  # noqa: E731
    return [
        mk("지속가능경영위원회", "exact", "Sustainability Committee", 1),
        mk("지속가능경영위원회", "exact", "Sustainable Management Committee", 2),
        mk("지속가능경영위원회", "exact", "the Sustainability Committee", 3),
        mk("지속가능경영위원회", "variant", "the committee", 4),
        mk("지속가능경영위원회", "pronoun", "we", 5),
        mk("경영위원회", "exact", "Management Committee", 1),
        mk("경영위원회", "exact", "Management Committees", 3),
        mk("경영위원회", "variant", "Committee", 4),
        mk("경영위원회", "nospan", "Board", 2),
    ]


def test_stages_s1_s2_s3():
    rows = rows_for_judge()
    s1 = {f["kr"]: f for f in SI.judge_code(rows, "S1", gloss_kr={"지속가능경영위원회"})}
    assert set(s1) == {"지속가능경영위원회", "경영위원회"}  # 경영위원회: management committee / committee 두 키
    assert s1["지속가능경영위원회"]["in_glossary"] and not s1["경영위원회"]["in_glossary"]
    assert [k for k, _, _ in s1["지속가능경영위원회"]["renderings"]] == ["sustainability committee", "sustainable management committee", "committee"]
    assert s1["지속가능경영위원회"]["kinds"] == {"exact": 3, "variant": 1, "pronoun": 1}  # pronoun·nospan 은 키에 안 들어감
    s2 = {f["kr"]: f for f in SI.judge_code(rows, "S2")}
    assert set(s2) == {"지속가능경영위원회"}  # 「committee」⊂「management committee」 합쳐져 경영위원회는 키 하나
    # 「committee」는 가장 긴 키(sustainable management committee)로 합쳐진다(inconsistency:155-161 — 긴 쪽부터 품는 자리를 찾음)
    assert [r[:2] for r in s2["지속가능경영위원회"]["renderings"]] == [["sustainable management committee", 2], ["sustainability committee", 2]]
    s3 = SI.judge_code(rows, "S3")
    assert len(s3) == 1 and [r[1] for r in s3[0]["renderings"]] == [2, 2]  # 합친 뒤 둘 다 출현 2 → S3 도 걸림
    rows2 = [r for r in rows if r["span"] != "the committee"]
    assert SI.judge_code(rows2, "S3") == []  # 합칠 것이 없으면 sustainable management committee 는 출현 1 → 뺌 → 키 하나


def test_judge_prompt_and_apply():
    rows = rows_for_judge()
    s2 = SI.judge_code(rows, "S2")
    pairs = [{"id": f"p{i}", "page": i, "ko": "", "en": f"page {i} text: the Sustainability Committee met and the Sustainable Management Committee did too."} for i in range(1, 6)]
    txt, n = SI.judge_prompt(s2, pairs)
    assert n == 1 and "### term 1: 지속가능경영위원회" in txt and '"Sustainability Committee" (x2)' in txt and '"Sustainable Management Committee" (x2)' in txt
    kept = SI.apply_judge(s2, [{"term": "지속가능경영위원회", "same_official_name": False, "reason": "x"}])
    assert kept == []
    kept = SI.apply_judge(s2, [{"term": "지속가능경영위원회", "same_official_name": True, "reason": "same body"}])
    assert len(kept) == 1 and kept[0]["judge"] is True and kept[0]["stage"] == "S4"
    assert SI.apply_judge(s2, [])[0]["judge"] is None  # 답 빠짐 → 걸림 유지


def test_fake_send_round_trip(tmp_path, monkeypatch):
    """가짜 send 로 프롬프트에 짝 id·terms 가 들어가고 답 → 산출 꼴(tx5_pick.run_span)."""
    from trlab import tx5_pick as X
    monkeypatch.setattr(X, "OUT5", tmp_path)
    P = SI.pool_from(pairs4(), GLOSS)
    monkeypatch.setattr(X, "run_pool", lambda key: (P, {}))
    seen = {}

    def fake_send(site, model, instr, text, schema, tag, est_usd, **extra):
        seen["text"], seen["tag"], seen["extra"] = text, tag, extra
        ans = {"items": [{"pair": p["id"], "term": t, "span": "Management Committee" if t == "경영위원회" else "Sustainability Committee", "kind": "exact"}
                         for p in SI.batches(P["items"], P["pairs"])[0]["pairs"] for t in p["terms"]]}
        return json.dumps(ans, ensure_ascii=False), {"tag": tag, "model": model, "costUsd": 0.001, "inputTokens": 100, "outputTokens": 10}

    monkeypatch.setattr(X, "send", fake_send)
    monkeypatch.setattr(X, "reserve", lambda site, usd: True)
    res, summ = X.run_span("zz", threads=1)
    assert summ["batches"] == 1 and summ["status"] == {"ok": 1} and seen["tag"] == "TRX5/span/zz/0"
    assert "### pair p4-b1" in seen["text"] and "terms: 경영위원회 | 지속가능경영위원회" in seen["text"]
    assert seen["extra"] == {"chars": len(seen["text"]), "items": 8}
    d = json.load(open(tmp_path / "span" / "zz-0.json"))
    assert len(d["checked"]) == 8 and d["kinds"]["exact"] == 6 and d["kinds"]["nospan"] == 2  # p2·p4 에는 Sustainability Committee 가 없다
    assert X.span_rows("zz") == d["checked"]
