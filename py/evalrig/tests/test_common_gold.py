"""evalrig.common.gold — 지은 정답 셋(synthetic)."""
import copy
import json

import pytest

from evalrig.common import gold as G

ITEM = {"type": "object", "required": ["k"], "properties": {"k": {"type": "string"}}}
G.register_unit("synthetic.t@1", ITEM)


def _gs(**over):
    gs = {
        "schema": G.SCHEMA_ID,
        "setId": "synthetic-a",
        "module": "drafting",
        "unit": "synthetic.t@1",
        "version": 1,
        "coverage": "exhaustive",
        "synthetic": True,
        "provenance": {"kind": "runner_made", "by": "test", "from": [], "at": "2026-11-09"},
        "review": {"status": "none", "by": None, "at": None, "note": None, "sample": None},
        "pending": [{"what": "가상 수정본", "question": "MARC-ENT-0", "slot": "consultant"}],
        "items": [{"k": "a"}, {"k": "b"}],
    }
    gs.update(over)
    return gs


def test_ok():
    assert G.check(_gs())["setId"] == "synthetic-a"


@pytest.mark.parametrize("field", ["provenance", "review", "coverage", "synthetic", "pending", "items", "setId"])
def test_required_field_missing_fails(field):
    gs = _gs()
    del gs[field]
    with pytest.raises(G.GoldError):
        G.check(gs)


def test_provenance_subfield_missing_fails():
    gs = _gs()
    del gs["provenance"]["kind"]
    with pytest.raises(G.GoldError, match="kind"):
        G.check(gs)


def test_unknown_kind_and_status_fail():
    with pytest.raises(G.GoldError):
        G.check(_gs(provenance={"kind": "guess", "by": "x", "from": [], "at": "d"}))
    gs = _gs()
    gs["review"]["status"] = "unknown"
    with pytest.raises(G.GoldError):
        G.check(gs)
    with pytest.raises(G.GoldError):
        G.check(_gs(coverage="some"))
    with pytest.raises(G.GoldError):
        G.check(_gs(module="nope"))


def test_none_review_with_sample_fails():
    gs = _gs()
    gs["review"]["sample"] = {"ok": 1, "n": 2}
    with pytest.raises(G.GoldError, match="sample"):
        G.check(gs)


def test_item_schema_enforced_and_unregistered():
    with pytest.raises(G.GoldError, match="items"):
        G.check(_gs(items=[{"x": 1}]))
    with pytest.raises(G.GoldError, match="등록"):
        G.check(_gs(unit="synthetic.none@1"))
    assert G.check(_gs(unit="synthetic.none@1"), require_unit=False)


def test_register_conflict():
    with pytest.raises(ValueError):
        G.register_unit("synthetic.t@1", {"type": "object"})
    G.register_unit("synthetic.t@1", copy.deepcopy(ITEM))  # 같은 꼴은 다시 해도 됨


def test_freeze_deterministic_and_detects_edit(tmp_path):
    p1, p2 = tmp_path / "a.json", tmp_path / "b.json"
    p1.write_text(json.dumps(_gs()), encoding="utf-8")
    p2.write_text(json.dumps(_gs(), indent=3), encoding="utf-8")  # 글 꼴만 다름
    s1, s2 = G.freeze(p1), G.freeze(p2)
    assert s1 == s2 and len(s1) == 16
    assert p1.read_bytes() == p2.read_bytes()
    assert G.freeze(p1) == s1  # 다시 얼려도 같음
    loaded = G.load(p1)
    assert loaded["frozen"] == {"sha16": s1, "at": "2026-11-09"}
    loaded["items"].append({"k": "c"})
    with pytest.raises(G.GoldError, match="얼린 뒤"):
        G.check(loaded)


def test_precision_guard():
    assert G.precision_allowed(_gs())
    part = _gs(coverage="partial")
    assert not G.precision_allowed(part)
    with pytest.raises(G.GoldError, match="partial"):
        G.guard_precision(part)


def test_new_and_header():
    gs = G.new(
        set_id="synthetic-b", module="materiality", unit="synthetic.t@1",
        provenance={"kind": "agent_draft_checked", "by": "x", "from": ["f"], "at": "2026-11-09"},
        review={"status": "agent_sampled", "by": "y", "at": "2026-11-09", "note": "n", "sample": {"ok": 3, "n": 4}},
        items=[], coverage="partial", synthetic=True,
    )
    h = G.header(gs)
    assert h == {
        "setId": "synthetic-b", "sha16": None, "provenance": "agent_draft_checked", "review": "agent_sampled",
        "coverage": "partial", "synthetic": True, "pending": [], "n": 0,
    }
    with pytest.raises(TypeError):
        G.new(set_id="x", module="drafting", unit="synthetic.t@1", items=[], coverage="partial", synthetic=True)  # type: ignore[call-arg]


def test_conventions_optional_and_in_sha():
    gs = _gs()
    assert "conventions" not in gs and G.check(gs)
    c = _gs(conventions={"labels": ["pass", "fail"], "accept": {"pass": ["ok"]}})
    assert G.check(c)["conventions"]["labels"] == ["pass", "fail"]
    assert G.sha16(c) != G.sha16(gs)
    with pytest.raises(G.GoldError):
        G.check(_gs(conventions=["labels"]))
    with pytest.raises(G.GoldError):
        G.check(_gs(other={}))  # 다른 칸은 여전히 막힘
    assert G.new(
        set_id="s", module="drafting", unit="synthetic.t@1",
        provenance={"kind": "runner_made", "by": "x", "from": [], "at": "d"},
        review={"status": "none", "by": None, "at": None, "note": None, "sample": None},
        items=[], coverage="exhaustive", synthetic=True, conventions={"labels": []},
    )["conventions"] == {"labels": []}


def test_review_only_status_required():
    gs = _gs(review={"status": "none"})
    assert G.check(gs) and G.review_field(gs, "note") is None
    gs2 = _gs(review={"status": "agent_sampled", "note": "n", "sample": {"ok": 1, "n": 2}})
    assert G.check(gs2) and G.review_field(gs2, "by") is None
    with pytest.raises(G.GoldError):
        G.check(_gs(review={"note": "x"}))
    with pytest.raises(G.GoldError):
        G.check(_gs(review={"status": "none", "extra": 1}))


def test_provenance_from_string_or_ref_sha():
    ok = _gs(provenance={"kind": "runner_made", "by": "t", "at": "d",
                         "from": ["a/b.json", {"ref": "c.pdf", "sha256": "0" * 64}, {"ref": "d", "sha256": None},
                                  {"ref": "e", "sha256": "ab" * 8}]})
    assert G.check(ok)
    for bad in ({"ref": "c"}, {"ref": "c", "sha256": "xyz"}, {"sha256": None}, 3, ""):
        with pytest.raises(G.GoldError):
            G.check(_gs(provenance={"kind": "runner_made", "by": "t", "at": "d", "from": [bad]}))
