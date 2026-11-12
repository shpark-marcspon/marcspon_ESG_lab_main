"""TRX6 tx6_pick — 지어낸 정답·예측 묶음으로 셈(잘/덜/잘못 우선순위, 갈래, 줄 짝 분모, 문장 나누기, 단 넘김 조각), 줄 → 블록 사상,
read6 차례, 장부 상한. 네트워크 0, 실제 보고서 문장 0."""

import json

import pytest

from trlab import tx6_pick
from trlab.common import Block


def L(i, y, x0=100.0, x1=400.0, size=8.0, text="가나다라마바사.", col=1, n=None):
    return {"id": f"L{i}", "x0": x0, "x1": x1, "y": y, "size": size, "bold_share": 0.0, "color": "#000000",
            "text": text, "n": n if n is not None else len(text), "col": col}


def gold_of(paras):
    of = {}
    for p in paras:
        p.setdefault("col_break_to", None)
        p.setdefault("page_break_to", None)
        for lid in p["lines"]:
            of[lid] = p
    return {"paras": paras, "of": of}


def para(pid, lines, kind="body", **kw):
    return {"id": pid, "kind": kind, "lines": lines, **kw}


# ---------- 셈 ----------
def test_verdict_priority_over_beats_under_beats_good():
    ls = [L(1, 100), L(2, 114), L(3, 128), L(4, 142, text="제목글"), L(5, 156)]
    gold = gold_of([para("g1", ["L1", "L2", "L3"]), para("h", ["L4"], kind="heading"), para("g2", ["L5"])])
    # g1 이 두 묶음에 흩어지고(덜) 그중 하나가 제목 줄도 품음(잘못) → 잘못 이음 하나로만 센다
    groups = {"L1": "a", "L2": "b", "L3": "b", "L4": "b", "L5": "c"}
    sc = tx6_pick.score_page(ls, groups, gold)
    v = {p["id"]: p for p in sc["paras"]}
    assert v["g1"]["verdict"] == "over" and v["g1"]["kind"] == "제목"
    assert v["g2"]["verdict"] == "good"
    # 흩어지기만 하면 덜 이음(조각 수)
    groups = {"L1": "a", "L2": "b", "L3": "b", "L4": "h", "L5": "c"}
    v = {p["id"]: p for p in tx6_pick.score_page(ls, groups, gold)["paras"]}
    assert v["g1"]["verdict"] == "under" and v["g1"]["pieces"] == 2
    # 묶음 하나 = 문단 줄 집합이면 잘 이음
    groups = {"L1": "a", "L2": "a", "L3": "a", "L4": "h", "L5": "c"}
    v = {p["id"]: p for p in tx6_pick.score_page(ls, groups, gold)["paras"]}
    assert v["g1"]["verdict"] == "good"


def test_over_kinds_and_col_break_fragments():
    ls = [L(1, 100, col=1), L(2, 114, col=1), L(3, 100, x0=450, x1=750, col=2), L(4, 114, x0=450, x1=750, col=2),
          L(5, 128, x0=450, x1=750, col=2, text="〉그림설명"), L(6, 142, x0=450, x1=750, col=2, text="*주")]
    gold = gold_of([para("g1", ["L1", "L2"], col_break_to="g2"), para("g2", ["L3", "L4"]),
                    para("c", ["L5"], kind="caption"), para("n", ["L6"], kind="note")])
    # 단 넘김 조각은 각각 정답 문단 — 둘 다 따로 잘 이으면 good 둘, 단 넘김 짝은 안 이은 것으로
    groups = {"L1": "a", "L2": "a", "L3": "b", "L4": "b", "L5": "c", "L6": "d"}
    sc = tx6_pick.score_page(ls, groups, gold)
    assert [p["verdict"] for p in sc["paras"]] == ["good", "good"]
    assert sc["breaks"] == [{"kind": "col_break", "a": "L2", "b": "L3", "joined": False}]
    # 단 넘김을 실제로 이으면 두 조각 다 「잘못 이음·단 넘김」, 짝은 joined
    groups = {"L1": "a", "L2": "a", "L3": "a", "L4": "a", "L5": "c", "L6": "d"}
    sc = tx6_pick.score_page(ls, groups, gold)
    assert [p["kind"] for p in sc["paras"]] == ["단 넘김", "단 넘김"]
    assert sc["breaks"][0]["joined"] is True
    # 설명·각주를 품으면 갈래가 표·그림 설명 / 그 밖
    groups = {"L1": "a", "L2": "a", "L3": "b", "L4": "b", "L5": "b", "L6": "e"}
    assert {p["id"]: p.get("kind") for p in tx6_pick.score_page(ls, groups, gold)["paras"]}["g2"] == "표·그림 설명"
    groups = {"L1": "a", "L2": "a", "L3": "b", "L4": "b", "L5": "c", "L6": "b"}
    assert {p["id"]: p.get("kind") for p in tx6_pick.score_page(ls, groups, gold)["paras"]}["g2"] == "그 밖"


def test_pair_denominator_only_body_pairs_and_neighbors_skip_tiny():
    ls = [L(1, 100, text="제목"), L(2, 114), L(3, 128), L(4, 131, x0=380, x1=384, size=4.0, text="1)", n=2),
          L(5, 142), L(6, 100, x0=450, x1=750, col=2)]
    gold = gold_of([para("h", ["L1"], kind="heading"), para("g1", ["L2", "L3"]), para("n", ["L4"], kind="note"),
                    para("g2", ["L5"]), para("g3", ["L6"])])
    pairs = tx6_pick.neighbors(ls)
    assert ("L2", "L3") in pairs and ("L3", "L5") in pairs and ("L1", "L2") in pairs
    assert not any("L4" in p for p in pairs)  # tiny 위첨자는 짝에 안 든다
    assert not any("L6" in p for p in pairs)  # 다른 단
    groups = {"L1": "h", "L2": "a", "L3": "a", "L4": "a", "L5": "a", "L6": "z"}
    sc = tx6_pick.score_page(ls, groups, gold)
    # 분모 = 둘 다 본문인 짝 (L2,L3)·(L3,L5) = 2; (L1,L2) 는 제목이라 뺌. tp 1, fp 1
    assert sc["pairs"] == {"n": 2, "tp": 1, "fp": 1, "fn": 0}
    # tiny 줄(L4)이 같은 묶음이어도 잘못 이음으로 세지 않는다
    v = {p["id"]: p for p in sc["paras"]}
    assert v["g1"]["verdict"] == "over" and v["g1"]["kind"] == "다른 문단"  # g2 의 L5 를 품어서
    assert v["g1"]["extra"] == ["L5"]  # tiny L4 는 밖 줄로 안 센다


def test_sentences_split_rules():
    assert tx6_pick.sentences("가나다.라마바!사아자?차카") == [(0, 4), (4, 8), (8, 12), (12, 14)]
    assert tx6_pick.sentences("매출3.4ton을썼다.끝") == [(0, 12), (12, 13)]  # 숫자 사이 . 은 안 자른다
    assert tx6_pick.sentences("했습니다.또한") == [(0, 5), (5, 7)]


def test_sentence_integrity_counts_lines_of_each_sentence():
    ls = [L(1, 100, text="가나다라마바"), L(2, 114, text="사아.자차카타"), L(3, 128, text="파하.")]
    gold = gold_of([para("g1", ["L1", "L2", "L3"])])
    groups = {"L1": "a", "L2": "b", "L3": "b"}  # 첫 문장(L1+L2)이 갈라짐, 둘째(L2+L3)는 한 묶음
    p = tx6_pick.score_page(ls, groups, gold)["paras"][0]
    assert (p["sentences"], p["sentences_ok"]) == (2, 1)


def test_summarize_percentages_and_denominators():
    pages = [{"status": "ok", "lines": 3, "out_of_block": 1,
              "score": {"paras": [{"verdict": "good", "sentences": 2, "sentences_ok": 2},
                                  {"verdict": "over", "kind": "제목", "sentences": 1, "sentences_ok": 0},
                                  {"verdict": "under", "sentences": 1, "sentences_ok": 1}],
                        "pairs": {"n": 4, "tp": 2, "fp": 1, "fn": 1},
                        "breaks": [{"kind": "col_break", "joined": True}]}}]
    s = tx6_pick.summarize(pages)
    assert (s["paragraphs"], s["good_pct"], s["over_pct"]) == (3, 33.3, 33.3)
    assert s["over_kinds"]["제목"] == 1 and s["sentence_pct"] == 75.0
    assert s["pair_precision"] == round(2 / 3, 4) and s["pair_recall"] == round(2 / 3, 4)
    assert s["breaks"] == {"col_break": 1, "col_break_joined": 1, "page_break": 0}
    assert s["out_of_block"] == 1 and s["page_status"] == {"ok": 1}


# ---------- 줄 → 블록 ----------
def test_map_lines_block_table_out_of_block():
    ls = [L(1, 100), L(2, 300), L(3, 500)]
    blocks = [Block("p1-b1", "paragraph", 1, 1, "x", (90.0, 90.0, 410.0, 110.0), 8.0, "body", 600.0, 800.0),
              Block("p1-t1", "table", 1, 2, "", (90.0, 290.0, 410.0, 310.0), 0.0, "body", 600.0, 800.0, "t1")]
    tables = [("t1", (90.0, 290.0, 410.0, 310.0))]
    mp = tx6_pick.map_lines(ls, blocks, tables)
    assert mp == {"L1": "p1-b1", "L2": "table", "L3": "out_of_block"}
    g = tx6_pick.groups_from_blocks(ls, mp)
    assert g["L1"] == "p1-b1" and g["L3"] == "oob:L3"


# ---------- 자리 ----------
def test_read6_prefers_out6_then_read4(tmp_path, monkeypatch):
    monkeypatch.setattr(tx6_pick, "OUT6", tmp_path)
    monkeypatch.setattr(tx6_pick.tx4_pick, "read4", lambda kind, name: f"read4:{kind}/{name}")
    assert tx6_pick.read6("parse", "zz-ko") == "read4:parse/zz-ko"
    (tmp_path / "parse" / "zz-ko").mkdir(parents=True)
    (tmp_path / "parse" / "zz-ko" / "document.json").write_text("{}")
    assert tx6_pick.read6("parse", "zz-ko") == tmp_path / "parse" / "zz-ko" / "document.json"
    (tmp_path / "attrs").mkdir()
    (tmp_path / "attrs" / "zz-ko.json").write_text("{}")
    assert tx6_pick.read6("attrs", "zz-ko") == tmp_path / "attrs" / "zz-ko.json"


# ---------- 장부 ----------
def test_reserve_refuses_over_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(tx6_pick, "OUT6", tmp_path)
    tx6_pick._RESERVED[0] = 0.0
    with open(tmp_path / "calls.jsonl", "w") as f:
        f.write(json.dumps({"tag": "TRX6/luna-pairs/cos-ko", "costUsd": 0.08}) + "\n")
        f.write(json.dumps({"tag": "TRX6/luna-pairs/kb-ko", "costUsd": 0.5, "dry_run": True}) + "\n")
    assert tx6_pick.spent() == pytest.approx(0.08)
    assert tx6_pick.reserve(0.03) is False  # 0.08 + 0.03 > 0.1 → 보내지 않음
    assert tx6_pick.reserve(0.015) is True
    assert tx6_pick.reserve(0.01) is False  # 잡아 둔 몫까지 더해 넘침
    tx6_pick._RESERVED[0] = 0.0
