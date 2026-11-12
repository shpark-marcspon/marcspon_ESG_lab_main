"""evalrig.translation — 영문번역 평가: 단위 `translation.block@1`(국문 블록 또는 쪽 구획) + 용어 위반.

정답 셋 셋: 공식 영문판 짝(`translation.pair@1`) · 용어집 가리킴(`translation.glossary-ref@1`) ·
TRX7 검수 단위 가리킴(`translation.review-ref@1`). 셈은 trlab(`glossary_check`·`review_probe`)을 그대로 부른다.
"""
from ..common import gold as _G

MODULE = "translation"
UNIT = "translation.block@1"
PAIR_UNIT = "translation.pair@1"
GLOSS_REF_UNIT = "translation.glossary-ref@1"
REVIEW_REF_UNIT = "translation.review-ref@1"

_SHA = {"type": "string", "pattern": "^[0-9a-f]{16}$"}
_G.register_unit(PAIR_UNIT, {
    "type": "object",
    "required": ["id", "page", "ko", "en", "ko_sha16", "en_sha16"],
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "page": {"type": "integer"},
        "ko": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "en": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "ko_sha16": _SHA,
        "en_sha16": _SHA,
        "ko_len": {"type": "integer"},
    },
})
_G.register_unit(GLOSS_REF_UNIT, {"type": "object"})
_G.register_unit(REVIEW_REF_UNIT, {"type": "object"})
