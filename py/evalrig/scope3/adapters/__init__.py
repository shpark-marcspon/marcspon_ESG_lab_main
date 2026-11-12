"""결과 꼴 → 어댑터 고르기(입력의 꼴 이름으로). 모르는 꼴은 도구 실패 2."""

import re
from typing import Any

from evalrig.scope3.adapters.colab157 import RESULT1, ROWS1, AdapterOutput, from_result1, from_rows1
from evalrig.scope3.adapters.result2 import AdapterUnavailable, from_result2
from evalrig.scope3.unit import ToolFailure

RESULT2_PATTERN = re.compile(r"^scope3\.[A-Za-z0-9_.-]+\.result@2$")

__all__ = ["AdapterOutput", "AdapterUnavailable", "adapt", "schema_of"]


def schema_of(data: Any) -> str:
    if not isinstance(data, dict):
        raise ToolFailure("결과가 객체가 아니다")
    name = data.get("schema") or data.get("schemaVersion")
    if not isinstance(name, str):
        raise ToolFailure("결과에 schema·schemaVersion 이 없다")
    return name


def adapt(data: Any) -> AdapterOutput:
    name = schema_of(data)
    if name == ROWS1:
        return from_rows1(data)
    if name == RESULT1:
        return from_result1(data)
    if RESULT2_PATTERN.match(name):
        from_result2(data)
    raise ToolFailure(f"모르는 결과 꼴 {name!r}")
