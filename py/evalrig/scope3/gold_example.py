"""받은 정답 예시(`MARC-ENT-537`, 코랩이 낸 결과 파일) → 정답 셋 `evalrig.scope3.gold/1`(`by: sheetOrder`).

결과 파일은 `scope3colab.rows.rows_from_result_xlsx` 로 읽고 1차 덤프와 같은 단위 변환(`units_from_rows`)을 탄다 — 그래서
정답과 1차 쪽이 같은 칸·같은 열쇠로 견줘진다. 경로는 CLI 인자로만 받고, 메타에는 sha256·바이트 수만 싣는다.

이 정답의 「정답」은 코랩이 낸 값이다(사람 검토 여부 모름 — `reviewed: "unknown"`). 1차(코랩 이식)를 이 셋으로 채점하면
「코랩과 같은가」를 다시 재는 것이지 정확도가 아니다.
"""

from pathlib import Path
from typing import Any

from scope3colab.rows import RowsFormatError, rows_from_result_xlsx

from evalrig.scope3.adapters.colab157 import units_from_rows
from evalrig.scope3.unit import GoldSet, ToolFailure, categories_meta, fingerprint

C1C2_ABSENT = "MARC-ENT-537 에 C1·C2 시트 없음 — MARC-ENT-165 에 덧댐"
NOTE = (
    "정답 = 코랩이 낸 결과 파일(사람 검토 여부 모름). 1차(colab_157)를 이 셋으로 채점한 값은 「코랩과 같은가」이지 정확도가 아니다. "
    "가상 148행(data/scope3-examples/)은 후보였으나 꼴이 달라(계수 id·llm-first) 쓰지 않음."
)


def gold_from_example(result_xlsx: Path, ref: str = "MARC-ENT-537") -> GoldSet:
    try:
        rows = rows_from_result_xlsx(result_xlsx)
    except (OSError, ValueError, RowsFormatError, KeyError) as error:
        raise ToolFailure(f"결과 파일을 못 읽었다 — {type(error).__name__}") from error
    units = units_from_rows(rows)
    absent = {cat: C1C2_ABSENT for cat in ("C1", "C2") if not any(u.category == cat for u in units)}
    meta: dict[str, Any] = {
        "source": {"kind": "received-example", "ref": ref, **fingerprint(result_xlsx)},
        "keyBy": "sheetOrder",
        "producedBy": "colab",
        "reviewed": "unknown",
        "note": NOTE,
        "categories": categories_meta(units, absent),
        "notCompared": len(rows.not_compared),
    }
    return GoldSet(meta, tuple(units))
