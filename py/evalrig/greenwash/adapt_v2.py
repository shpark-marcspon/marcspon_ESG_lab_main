"""2차 그린 결과(result@2) → 채점 단위 — 자리만(발주서 EVB §2-5).

채점기 입구는 1차 변환기(`convert_e0.convert`)와 같은 꼴을 받는다: {source, pages, config, units[Unit],
folded, notChecked[]}. 단위 payload 의 최소 칸은 page·sentence·sha12·level·category 다(다음 밴드가 채울 것).
result@2 꼴이 정해지기 전에는 칸 이름을 짐작하지 않는다.
"""
from __future__ import annotations

from typing import Any


class NotReady(NotImplementedError):
    """result@2 꼴이 아직 없다."""


def to_units(result: Any) -> dict[str, Any]:
    raise NotReady("result@2 미정 — core-io/6-result-contract-proposal.md 「리뷰 반영」, 그린 새 엔진 밴드에서")
