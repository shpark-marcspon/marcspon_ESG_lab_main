"""T8 — 리스크 짝(발주서 EVB §2-8)."""
from __future__ import annotations

import jsonschema

from evalrig.dataverify import gold as G
from evalrig.dataverify import risk
from evalrig.dataverify.units import ITEM_SCHEMA


def _rows(inp):
    return {r["ID"]: r for rows in inp["data"].values() for r in rows}


def _shape(values):
    vs = [values[y] for y in sorted(values)]
    base = next(v for v in vs if v is not None)
    return [None if v is None else round(v / base, 6) for v in vs]


def test_t8_pairs_differ_only_in_memo_and_labels_pair_up():
    inp, gs = risk.build()
    rows = _rows(inp)
    items = {it["indicator"]: it for it in gs["items"]}
    assert len(risk.PAIRS) >= 4
    for pid, kind, year, bare, noted, _s, _k, _m in risk.PAIRS:
        a, b = rows[bare[0]], rows[noted[0]]
        assert _shape(a["values"]) == _shape(b["values"]), pid
        assert a["메모"] == "" and b["메모"] != "", pid
        assert a["테마"] == b["테마"]
        assert (items[bare[0]]["verdict"], items[noted[0]]["verdict"]) == ("flag_no_note", "explained")
        assert items[bare[0]]["year"] == items[noted[0]]["year"] == year
        assert items[bare[0]]["pair"] == items[noted[0]]["pair"] == pid
    nones = [it for it in gs["items"] if it["verdict"] == "none"]
    assert nones and all(it["year"] is None and it["pair"] is None for it in nones)
    assert len(gs["items"]) == len(rows)  # 행마다 정답 하나(exhaustive)


def test_t8_variation_is_real():
    inp, _ = risk.build()
    rows = _rows(inp)
    for pid, kind, year, bare, _n, _s, _k, _m in risk.PAIRS:
        v = rows[bare[0]]["values"]
        prev = str(int(year) - 1)
        if kind in ("jump_up", "jump_down"):
            assert abs(v[year] / v[prev] - 1) >= 0.30, pid
        elif kind == "blank":
            assert v[year] is None
        elif kind == "diverging_trend":
            prod = rows["E-100"]["values"]
            assert v[year] > v[prev] and prod[year] < prod[prev]
    # 변동 없음 행은 ±30% 튐도 빈칸도 없다
    for row, _s in risk.STEADY:
        v = rows[row[0]]["values"]
        ys = sorted(v)
        assert all(v[y] is not None for y in ys)
        assert all(abs(v[b] / v[a] - 1) < 0.30 for a, b in zip(ys, ys[1:]))


def test_t8_shapes_and_git(input_schema, git_file):
    inp, gs = risk.build()
    jsonschema.validate(inp, input_schema)
    v = jsonschema.Draft202012Validator(ITEM_SCHEMA)
    for it in gs["items"]:
        v.validate(it)
    assert gs["meta"]["pairs"] == len(risk.PAIRS)
    assert G.dumps_input(inp) == git_file("synthetic-risk.input.json")
    assert G.dumps_gold(gs) == git_file("synthetic-risk.gold.json")
