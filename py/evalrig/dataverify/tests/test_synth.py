"""T5·T6 — 가상 기업 확장판(발주서 EVB §2-6)."""
from __future__ import annotations

import collections
import json

import jsonschema

from evalrig.dataverify import gold as G
from evalrig.dataverify import synth
from evalrig.dataverify.units import ITEM_SCHEMA

META_KEYS = {"schema", "setId", "module", "unit", "version", "coverage", "synthetic", "provenance", "review",
             "pending", "conventions", "meta", "items"}


def _diff(rel, vals, y):
    return synth.relation_value(rel, vals, y) - vals[rel["target"]][y]


def _slack(rel):
    dp = synth.relation_dp(rel)
    return 1e-9 if dp is None else 0.5 * 10 ** -dp + 1e-9


def test_t5_relations_hold_outside_plants_and_every_plant_fails():
    inp, gs = synth.build()
    rep = {r["ID"]: r["values"] for r in inp["data"]["환경"]}
    true = synth.true_table()
    planted = {(i, y) for i, y, _k, _h in synth.PLANTS}
    # 참값 표에서는 모든 관계가 선다(반올림 자리 안)
    for rel in synth.RELATIONS:
        for y in synth.YEARS:
            assert abs(_diff(rel, true, y)) <= _slack(rel), (rel["id"], y)
    # 보고값: 심은 칸이 없는 (관계, 해)는 차 0, 있는 것은 하나뿐이고 어긋남
    for rel in synth.RELATIONS:
        mem = synth.relation_members(rel)
        for y in synth.YEARS:
            hits = [i for i in mem if (i, y) in planted]
            assert len(hits) <= 1, (rel["id"], y, hits)
            d = _diff(rel, rep, y)
            if not hits:
                assert abs(d) <= _slack(rel), (rel["id"], y, d)
            elif (hits[0], y) == ("E-002", "2022"):
                assert 0.9 < abs(d) <= synth.TOLERANCE  # 경계 안
            elif (hits[0], y) == ("E-006", "2024"):
                assert synth.TOLERANCE < abs(d) < 1.3  # 경계 넘음
            else:
                assert abs(d) > _slack(rel), (rel["id"], y, d)
    # 심은 칸마다(경계 under 빼고) 그 칸이 든 관계 하나 이상이 1차 허용 오차를 넘는다.
    # (넘지 않는 곁 관계도 있다 — E-003 2023 합 심기가 int1 2023 에 주는 차는 집약도 단위로 1.1 안. 보고 「물을 거리」)
    for i, y, _k, _h in synth.PLANTS:
        if synth.EDGE.get((i, y)) == "under":
            continue
        ds = [abs(_diff(r, rep, y)) for r in synth.RELATIONS if i in synth.relation_members(r)]
        assert max(ds) > synth.TOLERANCE, (i, y, ds)
    items = {(it["indicator"], it["year"]): it for it in gs["items"]}
    assert len(items) == len(synth.ROWS_E) * len(synth.YEARS) == len(gs["items"])
    for i, y, kind, _h in synth.PLANTS:
        it = items[(i, y)]
        if synth.EDGE.get((i, y)) == "under":
            assert (it["verdict"], it["accept"]) == ("OK", ["FAIL"])
        else:
            assert (it["verdict"], it["kind"]) == ("FAIL", kind)
        assert it["reported"] != it["true"]
    for k, it in items.items():
        if k not in planted:
            assert (it["verdict"], it["kind"], it["reported"]) == ("OK", None, it["true"])
    kinds = collections.Counter(it["kind"] for it in gs["items"] if it["verdict"] == "FAIL")
    assert set(kinds) == set(synth.KINDS) and min(kinds.values()) >= 2, kinds
    # 서는 관계 수: 합 ≥3 · 비율 ≥2 · 집약도 ≥2
    rules = collections.Counter(r["rule"] for r in synth.RELATIONS)
    assert rules["sum"] >= 3 and rules["ratio"] >= 2 and rules["intensity"] >= 2


def test_t5_unit_slip_is_thousandfold():
    true = synth.true_table()
    inp, _ = synth.build()
    rep = {r["ID"]: r["values"] for r in inp["data"]["환경"]}
    for i, y, kind, _h in synth.PLANTS:
        if kind == "unit_slip":
            ratio = rep[i][y] / true[i][y]
            assert abs(ratio - 1000) < 1e-6 or abs(ratio - 0.001) < 1e-6, (i, y, ratio)


def test_t5_shapes(input_schema):
    inp, gs = synth.build()
    jsonschema.validate(inp, input_schema)
    assert list(inp)[0] == "schemaVersion"
    assert set(gs) == META_KEYS
    assert gs["review"]["status"] == "none" and gs["coverage"] == "exhaustive" and gs["synthetic"] is True
    assert [p["question"] for p in gs["pending"]] == ["MARC-ENT-607", "MARC-ENT-8"]
    v = jsonschema.Draft202012Validator(ITEM_SCHEMA)
    for it in gs["items"]:
        v.validate(it)
    assert gs["meta"]["inputSha16"] == G.sha16_bytes(G.dumps_input(inp).encode("utf-8"))


def test_t6_same_seed_same_bytes_and_equals_git(git_file):
    a = synth.build()
    b = synth.build()
    for x, y in zip(a, b):
        assert json.dumps(x, ensure_ascii=False) == json.dumps(y, ensure_ascii=False)
    assert G.dumps_input(a[0]) == git_file("synthetic-company.input.json")
    assert G.dumps_gold(a[1]) == git_file("synthetic-company.gold.json")


def test_t6_other_seed_differs():
    assert synth.build(seed=1)[0] != synth.build()[0]
