"""2차 엔진 결과(`scope3.*.result@2`) → 채점 단위 — 자리만. 꼴이 아직 없어 늘 `AdapterUnavailable`(CLI 종료 2).

빈 목록을 돌려주지 않는다: 빈 목록은 「2차가 줄을 하나도 안 냄」(전부 missing)으로 읽혀 점수 0 이 된다.
"""

from typing import Any, NoReturn

from evalrig.scope3.unit import ToolFailure


class AdapterUnavailable(ToolFailure):
    """그 결과 꼴의 어댑터가 아직 없다(도구 실패 2)."""


REASON = "result@2 꼴이 아직 없다 — core-io/6 「리뷰 반영」, 그린워싱 새 엔진 밴드 뒤"


def from_result2(data: Any) -> NoReturn:
    raise AdapterUnavailable(REASON)
