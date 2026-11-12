"""evalrig.common.units — 지은 단위."""
import pytest

from evalrig.common.units import Origin, Source, Unit, by_group, by_key, check_units, converter

O = Origin(engine="v1", schema="synthetic.result@1", run="run-a", converter="synthetic.conv@1")


def _u(key, group="g", **kw):
    return Unit(module="drafting", unit="synthetic.u@1", key=key, group=group, payload={"x": 1}, origin=O, **kw)


def test_duplicate_key_raises():
    with pytest.raises(ValueError, match="중복"):
        check_units([_u("a"), _u("b"), _u("a")])
    with pytest.raises(ValueError):
        by_key([_u("a"), _u("a")])


def test_status_only_known():
    for s in ("ok", "missing", "failed"):
        assert _u("a", status=s).status == s
    with pytest.raises(ValueError):
        _u("a", status="partial")


def test_origin_fields():
    with pytest.raises(ValueError):
        Origin(engine="v3", schema="s", run="r", converter="c@1")
    with pytest.raises(ValueError):
        Origin(engine="v2", schema="", run="r", converter="c@1")
    with pytest.raises(ValueError):
        Origin(engine="v2", schema="s", run="r", converter="c")
    d = _u("a").to_dict()
    assert d["origin"] == {"engine": "v1", "schema": "synthetic.result@1", "run": "run-a", "converter": "synthetic.conv@1"}
    assert d["schema"] == "evalrig.unit@1"


def test_sources_and_roundtrip():
    u = _u("a", sources=[Source("web", "https://example.invalid/x"), Source("doc", "p3", {"ok": True})])
    d = u.to_dict()
    assert d["sources"][1] == {"kind": "doc", "ref": "p3", "check": {"ok": True}}
    assert Unit.from_dict(d) == u
    with pytest.raises(ValueError):
        Source("blog", "x")


def test_key_must_be_nonempty_string():
    with pytest.raises(ValueError):
        _u("")
    with pytest.raises(ValueError):
        _u(3)  # type: ignore[arg-type]


def test_group_and_converter_decorator():
    g = by_group([_u("a", "t1"), _u("b", "t2"), _u("c", "t1")])
    assert [u.key for u in g["t1"]] == ["a", "c"]

    @converter("synthetic.conv@1")
    def conv(result, **ctx):
        return [_u(k) for k in result]

    assert conv.name == "synthetic.conv@1"
    assert [u.key for u in conv(["x", "y"])] == ["x", "y"]
    with pytest.raises(ValueError, match="중복"):
        conv(["x", "x"])
