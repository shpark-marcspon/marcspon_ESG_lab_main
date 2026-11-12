"""evalrig.iro.gold · from_v2 자리."""
import pytest

from evalrig.common import gold as G
from evalrig.iro import GOLD_UNIT
from evalrig.iro.from_v2 import convert_result_v2
from evalrig.iro.gold import build_empty, from_consultant_xlsx


def test_empty_gold_meta():
    gs = build_empty("2026-01-01")
    assert G.check(gs) and gs["items"] == [] and gs["unit"] == GOLD_UNIT
    assert gs["provenance"]["kind"] == "runner_made" and gs["review"]["status"] == "none"
    assert gs["pending"] == [{"what": "컨설턴트가 고친 IRO 템플릿 엑셀(전·뒤)", "question": "MARC-ENT-607", "slot": "consultant"}]
    assert gs["coverage"] == "partial" and gs["synthetic"] is False


def test_item_schema_registered():
    gs = build_empty("2026-01-01")
    gs["items"] = [{"topic": "가상 토픽", "branch": "regulation", "title": "가상 제도"}]
    assert G.check(gs)
    gs["items"] = [{"topic": "가상 토픽", "branch": "other", "title": "x"}]
    with pytest.raises(G.GoldError):
        G.check(gs)


def test_slots_not_implemented():
    with pytest.raises(NotImplementedError, match="607"):
        from_consultant_xlsx("x.xlsx")
    with pytest.raises(NotImplementedError, match="result@2"):
        convert_result_v2({}, run="r")
    assert convert_result_v2.name == "iro.result-v2@1"
