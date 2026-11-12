"""데이터 검증 정답 셋의 items 한 줄 꼴(`dataverify.indicator_year@1`).

정답 셋 머리(메타)는 EVA 공통 `evalrig.gold@1` 꼴이고, 이 파일은 items 한 줄만 정한다(C3 에서 공통
`register_unit` 에 등록한다 — 이 파일을 import 하면 등록된다). 판정 값은 시험지마다 다르다 — 어느 값을 쓰는지는 그 셋의 `conventions.labels`.
"""
from __future__ import annotations

from typing import Any

from evalrig.common import gold as common_gold

UNIT = "dataverify.indicator_year@1"

# 시험지 셋이 쓰는 판정 값 전부(셋마다 쓰는 것은 conventions.labels).
VERDICTS = (
    "OK", "FAIL",  # 가상 기업(검산)
    "match", "mismatch", "source_only", "report_only",  # 엑셀 ↔ PDF 대사
    "flag_no_note", "explained", "none",  # 리스크 짝
)

ITEM_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["indicator", "year", "verdict", "kind", "accept"],
    "properties": {
        "indicator": {"type": "string", "minLength": 1},
        "year": {"type": ["string", "null"], "pattern": "^[0-9]{4}$"},
        "verdict": {"enum": list(VERDICTS)},
        "kind": {"type": ["string", "null"]},
        "accept": {"type": "array", "items": {"enum": list(VERDICTS)}},
    },
}

common_gold.register_unit(UNIT, ITEM_SCHEMA)
