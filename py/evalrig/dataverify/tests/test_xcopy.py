"""MB2 C2 — 다른 회사 시험지 짓개(발주서 MB2 §2-3). 지은 문서 모델 + 그 문서에 랩 facts 를 돌린 출력(픽스처)."""
from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest

from evalrig.common import gold as common_gold
from evalrig.dataverify import xcopy, xpdf

FIX = Path(__file__).resolve().parent / "fixtures"


def _in():
    return (json.loads((FIX / "xcopy-doc.json").read_text(encoding="utf-8")),
            json.loads((FIX / "xcopy-facts.json").read_text(encoding="utf-8")))


def test_candidates_skip_tables_and_rows():
    doc, facts = _in()
    c = xcopy.candidate_rows(doc, facts)
    # p2-t1 low_confidence · p2-t2 연도 하나 · p3-t1 연도가 열 둘에 걸침 → 뺌. p1-t1 r12(값 하나)도 뺌.
    assert {r["table"] for r in c} == {"p1-t1"}
    assert [r["row"] for r in c] == list(range(1, 12))
    pct = c[-1]
    assert list(pct["cells"]) == ["2024", "2025"] and pct["unit"] == "%"  # 「5%」 칸은 값 후보에서 뺌, 단위는 머리 열
    assert c[0]["name"] == "지은 회사 환경 성과 · 지표 가" and c[0]["unit"] == "톤"
    assert c[0]["cells"]["2023"] == {"cell": "p1-t1:r1c2", "raw": "1,210", "value": "1210", "decimals": 0}


def test_pick_drops_duplicate_names():
    doc, facts = _in()
    picked = xcopy.pick_rows(xcopy.candidate_rows(doc, facts), min_cells=1000)
    names = [r["name"] for r in picked]
    assert len(names) == len(set(names)) == 9
    assert not any("겹침" in n for n in names)


def test_build_plants_gold_and_seed():
    doc, facts = _in()
    rows, plants, gs, picked = xcopy.build(doc, facts, min_cells=1000)
    common_gold.check(gs)
    assert gs["setId"] == "dataverify.xcopy-0f0f0f0f0f0f" and gs["coverage"] == "exhaustive"
    assert gs["provenance"]["kind"] == "runner_made" and gs["review"]["status"] == "none"
    assert xcopy.EASY_RULE in gs["conventions"]["rules"]
    kinds = collections.Counter({p["event"]: p["kind"] for p in plants["plants"]}.values())
    assert kinds == collections.Counter(xpdf.PLAN + ["source_only"]) and plants["events"] == 8
    report = {(f"X-{k + 1:03d}", y) for k, r in enumerate(picked) for y in r["cells"]}
    source = {(r["ID"], y) for r in rows for y in r["values"]}
    assert {(i["indicator"], i["year"]) for i in gs["items"]} == report | source
    v = collections.Counter(i["verdict"] for i in gs["items"])
    assert (v["report_only"], v["source_only"], v["mismatch"]) == (len(report - source), 3, 6)
    assert all(r["메모"] == "" and r["핵심데이터"] == "" for r in rows)
    for p in plants["plants"]:
        if p["kind"] == "unit_scale":
            assert p["planted"] == pytest.approx(float(p["original"].replace(",", "")) * 1000)
        if p["kind"] == "rounding":
            assert p["planted"] != float(p["original"].replace(",", ""))
    again = xcopy.build(doc, facts, min_cells=1000)
    assert json.dumps(again[:3], ensure_ascii=False, sort_keys=True) == json.dumps((rows, plants, gs), ensure_ascii=False, sort_keys=True)
    other = xcopy.build(doc, facts, seed=1, min_cells=1000)
    assert json.dumps(other[1], sort_keys=True) != json.dumps(plants, sort_keys=True)


def test_min_cells_stops_picking():
    doc, facts = _in()
    picked = xcopy.pick_rows(xcopy.candidate_rows(doc, facts), min_cells=10)
    n = [len(r["cells"]) for r in picked]
    assert sum(n) >= 10 and sum(n[:-1]) < 10


def test_same_year_two_columns_dropped():
    """C2 검증 권고 R3 — 같은 연도가 두 열이면 그 연도는 버린다(한 (행, 연도)에 값 둘)."""
    t = {"header": {"rows": [0], "cols": [0]}, "cells": [["이름", "2023년", "2024년 계획", "2024년"], ["가", "1", "2", "3"]]}
    assert xcopy.year_cols(t) == {1: "2023"}
