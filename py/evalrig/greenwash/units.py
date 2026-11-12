"""그린워싱 채점 단위 = (쪽, 정규화 문장) + 등급 + (있으면) 카테고리. 발주서 EVB §1-3 1.

정규화는 RWL2 의 비교 도구(`esg-app.worktrees/RWL2.out/compare.py` 의 `norm`, 18줄)와 같은 식이고,
열쇠는 (쪽, 정규화 글 sha256 앞 12자)다. 정답 items 한 줄의 꼴은 `greenwash.page_sentence@1` 로 공통에 등록한다.
"""
from __future__ import annotations

import hashlib
import unicodedata
from typing import Any

from evalrig.common import gold as common_gold

UNIT = "greenwash.page_sentence@1"
LEVELS = ("High", "Mid", "Low", "NonRisk")
RISK_LEVELS = ("High", "Mid")
ABSENT = {"status": "absent"}


def norm(s: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", s).split())


def sha12(s: str) -> str:
    """정규화한 글의 sha256 앞 12자(인자는 날 글 — 여기서 정규화한다)."""
    return hashlib.sha256(norm(s).encode("utf-8")).hexdigest()[:12]


_ABSENT_SCHEMA = {"type": "object", "required": ["status"], "properties": {"status": {"const": "absent"}},
                  "additionalProperties": False}

ITEM_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["page", "sentence", "sha12", "label", "level", "category", "accept", "occurrences"],
    "properties": {
        "page": {"type": "integer", "minimum": 1},
        "sentence": {"type": "string", "minLength": 1},
        "sha12": {"type": "string", "pattern": "^[0-9a-f]{12}$"},
        "label": {"type": "string", "minLength": 1},
        "level": {"oneOf": [{"enum": list(LEVELS)}, _ABSENT_SCHEMA]},
        "category": {"oneOf": [{"type": "string", "minLength": 1}, _ABSENT_SCHEMA]},
        "accept": {"type": "array", "items": {"type": "string"}},
        "occurrences": {"type": "integer", "minimum": 1},
    },
}

common_gold.register_unit(UNIT, ITEM_SCHEMA)
