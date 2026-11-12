"""스키마가 막아야 하는 것과, 실패가 빈 결과로 접히지 않는지."""

from __future__ import annotations

import copy
import dataclasses

import pytest

from factors import build as build_mod
from factors.loaders import defra, epa, keiti
from factors.record import RecordInvalid, parse_number, validate, validation_errors
from factors.sources import SOURCES, SourceFormatError, SourceUnavailable


@pytest.fixture()
def sample(loaded):
    return copy.deepcopy(loaded[epa.SOURCE_ID][0])


def test_blank_source_url_fails_schema(sample):
    """발주서 §3 심은 파손: 출처 URL 을 비우면 빨개져야 한다."""
    assert validation_errors(sample) == []
    sample["source"]["url"] = ""
    assert any(e.startswith("source/url") for e in validation_errors(sample))
    with pytest.raises(RecordInvalid):
        validate(sample)


@pytest.mark.parametrize("path", [("source", "url"), ("source", "version"), ("source", "retrieved"), ("license", "name"), ("unit",), ("region",)])
def test_missing_provenance_field_fails_schema(sample, path):
    target = sample
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]
    assert validation_errors(sample)


def test_spend_factor_requires_currency_and_price_year(sample):
    sample["spend"] = None
    assert validation_errors(sample)


def test_value_and_status_must_agree(sample):
    sample["value"] = None
    assert validation_errors(sample)
    sample["value_status"] = "not_available_in_source"
    assert validation_errors(sample) == []
    sample["value"] = 0
    assert validation_errors(sample)  # 값 없음을 0 으로 적을 수 없다


def test_adjusted_needs_inputs_with_source(sample):
    sample["adjusted"] = {"value": 1.0, "unit": "kg CO2e/KRW", "method": "synthetic", "inputs": []}
    assert validation_errors(sample)
    sample["adjusted"]["inputs"] = [{"name": "synthetic_fx", "value": 1.0, "source_url": "", "retrieved": "2026-10-01"}]
    assert validation_errors(sample)


def test_parse_number_rejects_non_numbers():
    assert parse_number("73,300", "t") == 73300.0
    assert parse_number("1.34e-01", "t") == 0.134
    for bad in ("", "-", "해당없음", "1,2", "abc"):
        with pytest.raises(SourceFormatError):
            parse_number(bad, "t")


def test_missing_raw_file_raises_not_empty(monkeypatch, tmp_path):
    gone = dataclasses.replace(SOURCES[keiti.SOURCE_ID], path=tmp_path / "synthetic-missing.csv")
    monkeypatch.setitem(SOURCES, keiti.SOURCE_ID, gone)
    with pytest.raises(SourceUnavailable):
        keiti.load()


def test_unexpected_header_raises(monkeypatch, tmp_path):
    p = tmp_path / "synthetic-bad-header.csv"
    p.write_bytes("이름,값\nsynthetic,1\n".encode("cp949"))
    monkeypatch.setitem(SOURCES, keiti.SOURCE_ID, dataclasses.replace(SOURCES[keiti.SOURCE_ID], path=p))
    with pytest.raises(SourceFormatError):
        keiti.load()


def test_non_numeric_value_raises(monkeypatch, tmp_path):
    p = tmp_path / "synthetic-bad-value.csv"
    row = ["수송", "synthetic", "synthetic", "ton.km", "해당없음", "약 0.2", "해당없음", "해당없음", "해당없음", "해당없음", "해당없음"]
    p.write_bytes((",".join(keiti.HEADER) + "\n" + ",".join(row) + "\n").encode("cp949"))
    monkeypatch.setitem(SOURCES, keiti.SOURCE_ID, dataclasses.replace(SOURCES[keiti.SOURCE_ID], path=p))
    with pytest.raises(SourceFormatError):
        keiti.load()


def test_unmapped_scope3_level1_raises(monkeypatch):
    monkeypatch.delitem(defra.SCOPE3_LEVEL1, "Hotel stay")
    with pytest.raises(SourceFormatError):
        defra.load()


def test_build_writes_nothing_when_a_source_fails(monkeypatch, tmp_path):
    def boom():
        raise SourceUnavailable("synthetic failure")

    monkeypatch.setitem(build_mod.LOADERS, epa.SOURCE_ID, boom)
    out = tmp_path / "out"
    with pytest.raises(SourceUnavailable):
        build_mod.build(out)
    assert not out.exists()


def test_build_rejects_empty_source(monkeypatch, tmp_path):
    monkeypatch.setitem(build_mod.LOADERS, epa.SOURCE_ID, lambda: [])
    with pytest.raises(build_mod.BuildError):
        build_mod.build(tmp_path / "out")


def test_scope_and_scope3_categories_must_agree(sample):
    sample["scope3_categories"] = []
    sample["scope3_mapping_basis"] = None
    assert validation_errors(sample)  # scope3 인데 후보가 없다
    sample["scope"] = "scope1"
    sample["factor_type"], sample["spend"] = "activity", None
    assert validation_errors(sample) == []
    sample["scope3_categories"] = [1]
    sample["scope3_mapping_basis"] = "synthetic"
    assert validation_errors(sample)  # scope1 인데 후보가 있다
