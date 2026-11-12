"""evalrig.drafting — 보고서 초안 평가: 단위 `drafting.leaf@1`(목차 잎) · 정답 항목 `drafting.fact@1`(SG1 사실).

맞음 판정 = 사실의 `match` 글자 중 하나가 잎 글에 숫자 경계로 있음(SG1 `sg1i._strict` + `taskeval._num` 과 같은 셈).
"""
from ..common import gold as _G

MODULE = "drafting"
UNIT = "drafting.leaf@1"
GOLD_UNIT = "drafting.fact@1"

GOLD_ITEM_SCHEMA = {
    "type": "object",
    "required": ["leaf", "idx", "metric", "value", "unit", "year", "match"],
    "additionalProperties": False,
    "properties": {
        "leaf": {"type": "string", "minLength": 1},
        "idx": {"type": "integer", "minimum": 0},
        "metric": {"type": "string"},
        "value": {"type": "string"},
        "unit": {"type": "string"},
        "year": {"type": "string"},
        "match": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
    },
}
_G.register_unit(GOLD_UNIT, GOLD_ITEM_SCHEMA)
