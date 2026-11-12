"""T9·T11·T15 — 1차 그린 결과 변환(지은 픽스처, 실비 0)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import jsonschema
import pytest

from evalrig.greenwash import adapt_v2, convert_e0, score
from evalrig.greenwash.tests.conftest import FIXTURES

REPO = Path(__file__).resolve().parents[4]


def _esg_app() -> Path:
    env = os.environ.get("ESG_APP_ROOT")
    if env:
        return Path(env)
    for base in (REPO.parent, REPO.parent.parent):
        if (base / "esg-app").is_dir():
            return base / "esg-app"
    return REPO.parent / "esg-app"


def _schema(path: Path) -> dict:
    if not path.exists():
        pytest.fail(f"고정 스키마 없음: {path} (ESG_APP_ROOT — 조용한 초록 금지)")
    return json.loads(path.read_text(encoding="utf-8"))


def _keys(c):
    return [(u.payload["page"], u.payload["sha12"], u.payload["level"]) for u in c["units"]]


def test_t9_three_shapes_same_units(fixture):
    v1 = fixture("gw-result-v1.json")
    v0 = fixture("gw-result-v0.1.json")
    a = convert_e0.convert(v1)
    b = convert_e0.convert_doc([v1])[0]  # mig1 꼴(최상위 목록)
    c = convert_e0.convert(v0)
    assert _keys(a) == _keys(b) == _keys(c)
    assert (a["schema"], c["schema"]) == ("greenwash.result@1", "greenwash-result@0.1")
    assert len(a["units"]) == 4 and a["items"] == 5


def test_t9_fold_duplicates_and_not_checked(fixture):
    a = convert_e0.convert(fixture("gw-result-v1.json"))
    assert a["folded"] == 1  # 쪽 3 이 두 파트라 같은 문장 둘
    dup = [u for u in a["units"] if u.payload["folded"]]
    assert len(dup) == 1 and dup[0].payload["rawIndex"] == [0, 1]
    assert a["notChecked"] == [5]
    full = [u for u in a["units"] if "모든 제품은 친환경" in u.payload["sentence"]][0]
    assert "　" not in full.payload["sentence"]  # 전각 공백 → 정규화
    assert all(u.payload["category"] == {"status": "absent"} for u in a["units"])


@pytest.mark.parametrize("bad", [{}, {"schemaVersion": "greenwash.result@9"}, [], "x"])
def test_t9_unknown_shape(bad):
    with pytest.raises(convert_e0.UnknownShape):
        convert_e0.convert_doc(bad)


def test_t11_adapter_slot_and_same_entry(fixture):
    with pytest.raises(adapt_v2.NotReady):
        adapt_v2.to_units({"schemaVersion": "greenwash.result@2"})
    # 채점기 입구 = 변환기 산출 꼴(source·notChecked·units). 공통 Converter 꼴도 같은 단위
    a = convert_e0.convert(fixture("gw-result-v1.json"))
    assert {"source", "notChecked", "units", "folded"} <= set(a)
    assert [u.key for u in convert_e0.from_v1(fixture("gw-result-v1.json"))] == [u.key for u in a["units"]]
    assert convert_e0.from_v1.name == convert_e0.NAME
    from evalrig.greenwash import gold

    assert score.score(gold.sample_gold(), a)["pred"] == 4  # 지은 표본 정답엔 문서 sha 가 없어 대조 안 함 — 입구 꼴만 본다


def test_t15_fixtures_pass_fixed_schemas():
    s1 = _schema(_esg_app() / "apps/api-py/esgapi/greenwash/tests/data/flow-shapes/greenwash.result@1.schema.json")
    s0 = _schema(REPO / "schemas/greenwash-result.schema.json")
    jsonschema.validate(json.loads((FIXTURES / "gw-result-v1.json").read_text(encoding="utf-8")), s1)
    jsonschema.validate(json.loads((FIXTURES / "gw-result-v0.1.json").read_text(encoding="utf-8")), s0)
