"""2차 result@2 → 번역 채점 단위 — **자리만**.

| result@2 칸 | 단위 칸 |
|---|---|
| 실행 id · 결과 판 | `origin.run` · `origin.schema` · `origin.engine = "v2"` |
| 구획 = 국문 블록(또는 쪽), 구획 `key` | `key` = 국문 블록 id(`"+".join(ko ids)`) 또는 `page:<쪽>` · `group` = 쪽 |
| 구획 원문·번역 | `payload.ko` · `payload.en` · `payload.granularity`(block·page) |
| 변형(라벨·고침 판) | `payload.variant` |
| 남은 한국어 표시 | `payload.remainingKorean` |
| 항목 `stableKey` | `payload.stableKey` |
| 구획 상태 | `status` |
"""
from __future__ import annotations

from typing import Any

from ..common.units import Unit, converter


@converter("translation.result-v2@1")
def convert_result_v2(result: dict[str, Any], *, run: str, **ctx: Any) -> list[Unit]:
    raise NotImplementedError("result@2 없음 — 2차 결과 계약이 선 뒤 몸통을 채운다")
