"""2차 데이터 검증 결과(result@2 새 엔진) → 채점 단위 — 자리만(발주서 EVB §2-5).

채점기 입구는 1차 변환기(`convert_156.convert`)와 같은 꼴을 받는다: {reviewStatus, units[Unit], unresolved{…}}.
새 엔진은 항목을 ID 로 실어야 한다(1차는 [대분류, 중분류, 소분류, 단위] 넷이라 입력 없이는 못 푼다 — ★4).
칸 이름은 짐작하지 않는다.
"""
from __future__ import annotations

from typing import Any


class NotReady(NotImplementedError):
    """result@2(새 엔진) 꼴이 아직 없다."""


def to_units(result: Any) -> dict[str, Any]:
    raise NotReady("result@2 미정 — core-io/6-result-contract-proposal.md 「리뷰 반영」, 데이터 새 엔진 밴드에서")
