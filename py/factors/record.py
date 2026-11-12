"""정규화 레코드 조립과 스키마 검증."""

from __future__ import annotations

import json
import re
from functools import lru_cache

from jsonschema import Draft202012Validator

from .sources import SCHEMA_PATH, Source, SourceFormatError

SCHEMA_VERSION = "0.1"

_NUMBER = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$|^-?\d+(\.\d+)?([eE][-+]?\d+)?$")


class RecordInvalid(Exception):
    """레코드가 스키마를 통과하지 못했다."""


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validation_errors(record: dict) -> list[str]:
    return [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in _validator().iter_errors(record)
    ]


def validate(record: dict) -> None:
    errors = validation_errors(record)
    if errors:
        raise RecordInvalid(f"{record.get('id')!r}: " + "; ".join(errors[:5]))


def parse_number(text: str, where: str) -> float:
    """원자료의 숫자 표기를 읽는다. 숫자가 아니면 0 이나 None 으로 넘기지 않고 올린다."""
    t = text.strip()
    if not _NUMBER.match(t):
        raise SourceFormatError(f"{where}: 숫자가 아닌 값 {text!r}")
    return float(t.replace(",", ""))


def make_record(
    src: Source,
    *,
    key: str,
    locator: str,
    record_type: str = "emission_factor",
    scope: str,
    scope3_categories: list[int] | None = None,
    scope3_mapping_basis: str | None = None,
    factor_type: str,
    name_original: str,
    name_lang: str,
    category_path: list[str],
    classification: dict | None = None,
    value: float | None,
    value_raw: str | None,
    value_status: str = "ok",
    unit_numerator: str,
    unit_denominator: str,
    unit: str | None = None,
    gas: str,
    gas_basis: str,
    heating_value_basis: str | None = None,
    conditions: dict[str, str] | None = None,
    region: str,
    year: int | None,
    spend: dict | None = None,
    extra: dict | None = None,
) -> dict:
    cats = sorted(scope3_categories or [])
    return {
        "schema_version": SCHEMA_VERSION,
        "id": f"{src.id}:{key}",
        "record_type": record_type,
        "scope": scope,
        "scope3_categories": cats,
        "scope3_mapping_basis": scope3_mapping_basis if cats else None,
        "factor_type": factor_type,
        "name": {
            "original": name_original,
            "ko": name_original if name_lang == "ko" else None,
            "en": name_original if name_lang == "en" else None,
        },
        "category_path": category_path,
        "classification": classification,
        "value": value,
        "value_raw": value_raw,
        "value_status": value_status,
        "unit": unit or f"{unit_numerator}/{unit_denominator}",
        "unit_numerator": unit_numerator,
        "unit_denominator": unit_denominator,
        "gas": gas,
        "gas_basis": gas_basis,
        "heating_value_basis": heating_value_basis,
        "conditions": conditions or {},
        "region": region,
        "year": year,
        "spend": spend,
        "adjusted": None,
        "source": src.source_block(locator),
        "license": src.license_block(),
        "license_status": src.license_status,
        "synthetic": False,
        "extra": extra or {},
    }
