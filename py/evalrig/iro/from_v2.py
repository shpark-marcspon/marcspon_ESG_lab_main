"""2차 result@2 → IRO 채점 단위 — **자리만**(result@2 가 서기 전, 발주서 EVA §1 「이미 정해진 것」 1).

대응 표(result@2 = 실행 → 구획 → 항목, `core-io/6-result-contract-proposal.md` 「리뷰 반영」):

| result@2 칸 | 단위 칸 |
|---|---|
| 실행 id | `origin.run` |
| 결과 판(`schema`) | `origin.schema` · `origin.engine = "v2"` |
| 구획 `key`(토픽 입력 열쇠) + 구획 갈래 | `key = "<토픽 열쇠>|<갈래>"` · `group = <토픽 열쇠>` — 1차와 달리 이름 정규화가 아니라 입력 때 열쇠 |
| 구획 상태 | `status`(ok·missing·failed) |
| 항목 `stableKey` | `payload.items[].extra.stableKey` |
| 항목 제목·날짜·요약 | `payload.items[].{title, date, date_status, summary}` |
| 항목 출처 URL | `payload.items[].urls` · `sources[{kind: web, ref}]` |
| 검색 근거(검색 호출·인용) | `payload.searched` · `payload.citations` |
| 범위(화이트리스트 판·기간) | `payload.scope.{whitelist, dateRange}` |
"""
from __future__ import annotations

from typing import Any

from ..common.units import Unit, converter


@converter("iro.result-v2@1")
def convert_result_v2(result: dict[str, Any], *, run: str, **ctx: Any) -> list[Unit]:
    raise NotImplementedError("result@2 없음 — 2차 결과 계약이 선 뒤 몸통을 채운다")
