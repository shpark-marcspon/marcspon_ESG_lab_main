"""evalrig.translation.gold — 가리킴 셋(지은 파일)."""
import json

import pytest

from evalrig.common import gold as G
from evalrig.translation import gold as TG


def test_review_ref(tmp_path):
    g7 = tmp_path / "g7"
    g7.mkdir()
    units = [{"id": f"u{i}", "split": s, "cat": c} for i, (s, c) in enumerate([("test", "num"), ("test", "clean"), ("test", "real"), ("dev", "term")])]
    (g7 / "units.json").write_text(json.dumps({"units": units}), encoding="utf-8")
    (g7 / "real.json").write_text("{}", encoding="utf-8")
    gs = TG.build_review_ref(at="2026-01-01", root=tmp_path, gold7=g7)
    assert G.check(gs) and gs["setId"] == "translation-review-trx7"
    assert gs["meta"]["counts"] == {"dev:planted": 1, "test:clean": 1, "test:planted": 1, "test:real": 1}
    assert gs["provenance"]["from"][0]["ref"] == "g7/units.json"
    TG.check_ref(gs, tmp_path)
    (g7 / "real.json").write_text('{"x": 1}', encoding="utf-8")
    with pytest.raises(ValueError, match="바뀜"):
        TG.check_ref(gs, tmp_path)


def test_glossary_ref(tmp_path, monkeypatch):
    import trlab.common

    (tmp_path / "glossary").mkdir()
    (tmp_path / "glossary" / "zz.json").write_text(json.dumps({"items": [{"kr": "가", "en": "a"}] * 3}), encoding="utf-8")
    monkeypatch.setattr(trlab.common, "GOLD", tmp_path)
    gs = TG.build_glossary_ref("zz", at="2026-01-01", root=tmp_path)
    assert G.check(gs) and gs["meta"]["n_items"] == 3 and gs["meta"]["file"] == "glossary/zz.json"
    assert gs["provenance"]["kind"] == "runner_made" and gs["review"]["status"] == "none" and gs["coverage"] == "partial"


def test_pair_item_schema():
    gs = G.new(set_id="synthetic-p", module="translation", unit="translation.pair@1", coverage="exhaustive", synthetic=True,
               provenance={"kind": "official_publication", "by": "t", "from": [], "at": "d"}, review={"status": "none"},
               items=[{"id": "p1-b1", "page": 1, "ko": ["p1-b1"], "en": ["p1-b2"], "ko_sha16": "0" * 16, "en_sha16": "1" * 16}])
    assert G.check(gs)
    gs["items"][0]["ko"] = []
    with pytest.raises(G.GoldError):
        G.check(gs)
