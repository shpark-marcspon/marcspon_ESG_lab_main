"""evalrig.drafting.score — 숫자 경계·거시 평균·leaf_map."""
import pytest

from evalrig.common import gold as G
from evalrig.common.units import Origin, Unit
from evalrig.drafting.score import code_metrics, fact_hit, score

O = Origin("v1", "drafting.bullets@3", "syn", "drafting.bullets3@1")


def _u(key, text, bullets=None, warnings=None):
    return Unit(module="drafting", unit="drafting.leaf@1", key=key, group=key, origin=O,
                payload={"text": text, "bullets": bullets or [{"text": text, "unsupported": False, "n_sources": 1}],
                         "numbers": [], "warnings": warnings or {}})


def _gold(items):
    return G.new(set_id="synthetic-d", module="drafting", unit="drafting.fact@1", coverage="partial", synthetic=True,
                 provenance={"kind": "runner_made", "by": "t", "from": [], "at": "d"}, review={"status": "none"}, items=items)


def _f(leaf, idx, *m):
    return {"leaf": leaf, "idx": idx, "metric": "", "value": "", "unit": "", "year": "", "match": list(m)}


@pytest.mark.parametrize("m,t,exp", [
    ("120", "총 1,120 t", False), ("1,120", "총 1120 t", True), ("120", "120.5", False), ("12", "3.12", False),
    ("46.2%", "46.2 %", True), ("384,836", "합계 384,836tCO2e", True), ("2019년", "2019년 대비", True), ("20", "2020", False),
])
def test_fact_hit_number_boundary(m, t, exp):
    assert fact_hit([m], t) is exp


def test_macro_recall_and_missed():
    gs = _gold([_f("Q1", 0, "100"), _f("Q1", 1, "200"), _f("Q2", 0, "300"), _f("Q3", 0, "400")])
    us = [_u("Q1", "100 과 999"), _u("Q2", "300")]
    r = score(us, gs)
    assert r["per_leaf"] == {"Q1": {"value": 0.5, "num": 1, "den": 2}, "Q2": {"value": 1.0, "num": 1, "den": 1}}
    assert r["recall"]["value"] == 0.75 and r["missed"] == ["Q3"]
    assert r["facts"] == {"value": 2 / 3, "num": 2, "den": 3}
    assert r["ci"]["n"] == 2 and r["ci"]["seed"] == 552


def test_leaf_map():
    gs = _gold([_f("Q1", 0, "100")])
    assert score([_u("leaf-a", "100")], gs)["missed"] == ["Q1"]
    r = score([_u("leaf-a", "100")], gs, leaf_map={"Q1": "leaf-a"})
    assert r["per_leaf"]["Q1"]["value"] == 1.0 and r["missed"] == []


def test_code_metrics():
    us = [_u("a", "x", bullets=[{"text": "1", "unsupported": True, "n_sources": 0}, {"text": "2", "unsupported": False, "n_sources": 1}],
             warnings={"number_not_in_source": 1}), _u("b", "y")]
    m = code_metrics(us)
    assert m["bullets"] == 3
    assert m["number_not_in_source_rate"] == {"value": 1 / 3, "num": 1, "den": 3}
    assert m["unsupported_rate"]["num"] == 1
