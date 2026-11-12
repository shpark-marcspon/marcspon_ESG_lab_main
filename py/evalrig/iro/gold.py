"""IRO 정답 셋 — 지금은 0 항목(`iro-empty`). 컨설턴트가 고친 IRO 템플릿 엑셀(`sid:MARC-ENT-607`)이 들어올 자리만 둔다."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..common import gold as G
from . import GOLD_UNIT, MODULE

EMPTY_ID = "iro-empty"


def build_empty(at: str) -> dict[str, Any]:
    """항목 0 셋. 러너가 정답을 지어 넣지 않는다(혼재·맞음 라벨은 사람 몫)."""
    return G.new(
        set_id=EMPTY_ID,
        module=MODULE,
        unit=GOLD_UNIT,
        coverage="partial",
        synthetic=False,
        provenance={"kind": "runner_made", "by": "EVA runner", "from": [], "at": at},
        review={"status": "none", "by": None, "at": None, "note": "정답 0 — 자리만", "sample": None},
        pending=[{"what": "컨설턴트가 고친 IRO 템플릿 엑셀(전·뒤)", "question": "MARC-ENT-607", "slot": "consultant"}],
        items=[],
    )


def from_consultant_xlsx(path: str | Path) -> dict[str, Any]:
    """컨설턴트 수정본 엑셀 → 정답 셋(`iro.item@1`). 꼴을 받은 뒤 채운다(`sid:MARC-ENT-607`)."""
    raise NotImplementedError("컨설턴트 수정본 꼴 없음 — MARC-ENT-607 답 뒤")
