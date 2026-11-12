"""2차 result@2 → 보고서 채점 단위 — **자리만**.

| result@2 칸 | 단위 칸 |
|---|---|
| 실행 id · 결과 판 | `origin.run` · `origin.schema` · `origin.engine = "v2"` |
| 구획 = 목차 잎, 구획 `key` | `key` = 잎 `key`(부록 「보고서 = 목차 잎 key」) · `group` = 같은 값 |
| 구획 상태 | `status` |
| 항목(bullet) 글·근거 없음 표시·근거 수 | `payload.bullets[].{text, unsupported, n_sources}` · `payload.text`(줄로 이음) |
| 항목 `stableKey` | `payload.bullets[].stableKey` |
| 항목 근거(문서·블록) | `sources[{kind: doc, ref: "<문서>:<블록>"}]` |
| 경고 코드 | `payload.warnings{code: 수}` |
"""
from __future__ import annotations

from typing import Any

from ..common.units import Unit, converter


@converter("drafting.result-v2@1")
def convert_result_v2(result: dict[str, Any], *, run: str, **ctx: Any) -> list[Unit]:
    raise NotImplementedError("result@2 없음 — 2차 결과 계약이 선 뒤 몸통을 채운다")
