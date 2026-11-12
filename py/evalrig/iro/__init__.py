"""evalrig.iro — 중대성평가(IRO) 평가: 단위 `iro.topic-branch@1` = (토픽, 갈래) + 출처.

정답은 지금 0(컨설턴트 수정본 `sid:MARC-ENT-607` 을 기다림) — 정답 없는 코드 지표와 URL 검사(M-I2)만.
"""
from ..common import gold as _G

MODULE = "materiality"
UNIT = "iro.topic-branch@1"
GOLD_UNIT = "iro.item@1"
BRANCHES = ("regulation", "controversy", "impact", "financial", "pathway")
WEB_BRANCHES = ("regulation", "controversy")

GOLD_ITEM_SCHEMA = {
    "type": "object",
    "required": ["topic", "branch", "title"],
    "additionalProperties": False,
    "properties": {
        "topic": {"type": "string", "minLength": 1},
        "branch": {"enum": list(BRANCHES)},
        "title": {"type": "string", "minLength": 1},
        "urls": {"type": "array", "items": {"type": "string"}},
        "date": {"type": ["string", "null"]},
        "label": {"type": ["string", "null"]},
    },
}
_G.register_unit(GOLD_UNIT, GOLD_ITEM_SCHEMA)
