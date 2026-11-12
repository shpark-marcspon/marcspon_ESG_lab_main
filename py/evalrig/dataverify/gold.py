"""정답 셋 머리 짓기 — EVA 공통 `evalrig.gold@1` 꼴의 칸을 채운다(검사는 C3 에서 공통 `check`).

이 파일은 꼴을 새로 정하지 않는다. 칸 이름·갈래는 EVA `common/gold.py` 의 것이고, `conventions{}` 는
EVA 가 더하는 선택 칸이다(critic 1 반영 B2). 머리의 날짜는 고정 값이라 다시 지어도 바이트가 같다.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from evalrig.dataverify.units import UNIT

SCHEMA_ID = "evalrig.gold@1"
AUTHOR = "runner-EVB (MARC-TICK-553)"
MADE_ON = "2026-11-09"
REVIEW_NONE = {
    "status": "none",
    "by": None,
    "at": None,
    "note": "러너가 지음. 사람(컨설턴트) 검토 0",
    "sample": None,
}
PENDING = [
    {"what": "컨설턴트가 쓰는 결과 양식(데이터 리스크 워드)과 그 정답", "question": "MARC-ENT-607", "slot": "consultant"},
    {"what": "정확도 합격 기준·정답셋·기존 컨설팅 오류 기록", "question": "MARC-ENT-8", "slot": "consultant"},
]


def sha16_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def file_sha16(path: str | Path) -> str:
    return sha16_bytes(Path(path).read_bytes())


def header(
    *,
    set_id: str,
    coverage: str,
    synthetic: bool,
    sources: list[str],
    conventions: dict[str, Any],
    meta: dict[str, Any],
) -> dict[str, Any]:
    """items 를 뺀 머리. `sources` 는 `provenance.from[]`(지은 코드·바탕 파일과 그 sha16)."""
    return {
        "schema": SCHEMA_ID,
        "setId": set_id,
        "module": "dataverify",
        "unit": UNIT,
        "version": 1,
        "coverage": coverage,
        "synthetic": synthetic,
        "provenance": {"kind": "runner_made", "by": AUTHOR, "from": list(sources), "at": MADE_ON},
        "review": dict(REVIEW_NONE),
        "pending": [dict(p) for p in PENDING],
        "conventions": conventions,
        "meta": meta,
    }


def dumps_gold(gs: dict[str, Any]) -> str:
    """정답 셋 글 — EVA `common/gold.dumps` 와 같은 꼴(키 차례 고정, 들여쓰기 1)."""
    return json.dumps(gs, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def dumps_input(doc: dict[str, Any]) -> str:
    """입력 글 — 키 차례를 지킨다(`dataverify.input@1` 는 schemaVersion 이 첫 키)."""
    return json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
