"""사람 수정 기록 → 정답 셋(S3-E1, 들어올 자리 — 실제 기록은 아직 없다. 시험은 지은 고정물만).

입력 둘 — 같은 실행의 `GET …/scope3/runs/{run_id}/result`(result@1) JSON 과 `GET …/scope3/runs/{run_id}/edits`
(esg-app `Scope3RowEditsOut`: `runId`·`rows[{rowId, category, excluded, factor, editedAt, editedBy}]`). 줄마다:

| 지금 상태 | 정답 단위 |
|---|---|
| `excluded: true` | `gold: excluded` — hit 분모 밖, 「제외 n」 |
| `factor.choice = applied` | 정답 계수 = 그 줄 대표(AI 원안이 맞았다) |
| `recommended`, `rank r` | 정답 계수 = 추천 r(1..4) |
| `custom` | 정답 계수 = 사람이 넣은 `title`·`value`(후보 밖), 배출량 = 사건의 `co2e` |
| 사건 없는 줄 | `gold: unreviewed` — 분모 밖(「손대지 않음 = 맞음」으로 읽지 않는다, 감독 답 ③) |
| 제외 뒤 복구만 있고 계수 사건 없음(`excluded:false`·`factor:null`) | `gold: unreviewed`(계수를 판단한 기록이 아니다) |
| `excluded:true` 이면서 계수 사건도 있음 | `gold: excluded`(제외가 먼저) |

`editedBy`(사람 이름·id)·사유 글은 버린다 — 정답 셋 어디에도 싣지 않는다. 분류(C1·C2 그룹)는 편집이 고치지 않으므로
정답 분류는 `absent` 다.
"""

from pathlib import Path
from typing import Any

from evalrig.scope3.adapters.colab157 import from_result1
from evalrig.scope3.unit import (
    CLASSIFIED,
    Factor,
    GoldMark,
    GoldSet,
    ToolFailure,
    Unit,
    categories_meta,
    decimal_text,
    factor_key_fields,
    fingerprint,
)

RANK_FIRST, RANK_LAST = 1, 4  # esg-app `FACTOR_RANK_FIRST..LAST`(service/utils/scope3_factors.py)
NO_CLASS = {"status": "absent", "reason": "편집 기록은 분류를 고치지 않는다"}


def _gold_unit(base: Unit, row_result: dict[str, Any], edit: dict[str, Any] | None, counts: dict[str, int]) -> Unit:
    def mark(kind: GoldMark, why: str) -> Unit:
        counts[why] = counts.get(why, 0) + 1
        return Unit(base.key, base.category, None, None, base.branch, gold=kind)

    if edit is None:
        return mark("unreviewed", "unreviewed")
    if edit.get("excluded") is True:
        return mark("excluded", "excluded")
    factor = edit.get("factor")
    if factor is None:
        return mark("unreviewed", "restoredOnly")
    choice = factor.get("choice")
    edit_co2e = decimal_text(factor.get("co2e"))
    classification = dict(group=NO_CLASS, naics=NO_CLASS) if base.category in CLASSIFIED else None
    if choice == "applied":
        counts["applied"] = counts.get("applied", 0) + 1
        return Unit(base.key, base.category, base.factor, base.co2e, base.branch, classification=classification, source="applied")
    if choice == "recommended":
        rank = factor.get("rank")
        if not isinstance(rank, int) or not RANK_FIRST <= rank <= RANK_LAST:
            raise ToolFailure(f"{base.key.row_id}: recommended 의 rank 가 {RANK_FIRST}..{RANK_LAST} 밖")
        recs = row_result.get("recommendations") or []
        if rank <= len(recs):
            title, value, co2e = recs[rank - 1].get("title"), recs[rank - 1].get("value"), recs[rank - 1].get("expectedCo2e")
        elif factor.get("title") is not None:
            title, value, co2e = factor.get("title"), factor.get("value"), None
        else:
            raise ToolFailure(f"{base.key.row_id}: 추천 {rank} 이 결과에도 사건에도 없다")
        counts["recommended"] = counts.get("recommended", 0) + 1
        gold_factor = _titled_factor(base, title, decimal_text(value), factor.get("unit"))
        return Unit(base.key, base.category, gold_factor, edit_co2e or decimal_text(co2e), base.branch,
                    classification=classification, source=f"recommended:{rank}")  # fmt: skip
    if choice == "custom":
        counts["custom"] = counts.get("custom", 0) + 1
        gold_factor = _titled_factor(base, factor.get("title"), decimal_text(factor.get("value")), factor.get("unit"))
        return Unit(base.key, base.category, gold_factor, edit_co2e, base.branch, classification=classification, source="custom")
    raise ToolFailure(f"{base.key.row_id}: 모르는 factor.choice {choice!r}")


def _titled_factor(base: Unit, title: Any, value: str | None, unit: Any) -> Factor:
    """사람이 고른 계수의 열쇠 칸. 제목에서 오는 칸(대표EF·EF매핑명)과 단위(EF단위)만 채운다 — 정규화 처리방법·이동수단처럼
    제목이 아닌 칸(정규화 처리방법·이동수단·바이오매스 구분·NAICS 코드)은 사람이 넣은 계수에 없으니 빼고 남은 칸 + 값으로 견준다."""
    keys: dict[str, str | None] = {}
    for name in factor_key_fields(base.category, base.branch):
        if name in ("대표EF", "EF매핑명", "EF_DB"):
            keys[name] = None if title is None else str(title)
        elif name == "EF단위":
            keys[name] = None if unit is None else str(unit)
    return Factor(keys, value)


def gold_from_edits(result_path: Path, edits_path: Path, result: Any, edits: Any) -> GoldSet:
    adapted = from_result1(result)
    if not isinstance(edits, dict) or not isinstance(edits.get("rows"), list) or "runId" not in edits:
        raise ToolFailure("편집 기록 꼴 어긋남 — runId·rows 가 있어야 한다")
    rows_by_id: dict[str, dict[str, Any]] = {}
    for sheet in result["sheets"]:
        for row in sheet.get("rows") or []:
            rows_by_id[str(row["rowId"])] = row
    by_unit = {u.key.row_id: u for u in adapted.units}
    edits_by_id: dict[str, dict[str, Any]] = {}
    for e in edits["rows"]:
        rid = str(e.get("rowId"))
        if rid not in by_unit:
            raise ToolFailure(f"편집 줄 {rid} 가 결과에 없다(다른 실행?)")
        if str(e.get("category")) != by_unit[rid].category:
            raise ToolFailure(f"편집 줄 {rid} 의 category 가 결과와 다르다")
        if rid in edits_by_id:
            raise ToolFailure(f"편집 줄 {rid} 가 둘 이상")
        edits_by_id[rid] = e
    counts: dict[str, int] = {}
    units = [_gold_unit(u, rows_by_id[str(u.key.row_id)], edits_by_id.get(str(u.key.row_id)), counts) for u in adapted.units]
    failed = sorted({s.category for s in adapted.sheets if s.status not in ("processed", "not_target") and s.category})
    absent = {cat: "1차 결과에서 그 시트가 실패·지원 안 함" for cat in failed if not any(u.category == cat for u in units)}
    meta: dict[str, Any] = {
        "source": {"kind": "human-edits", "runId": str(edits["runId"]), **fingerprint(edits_path),
                   "result": fingerprint(result_path)},  # fmt: skip
        "keyBy": "rowId",
        "producedBy": "human",
        "reviewed": "yes",
        "note": "정답 = 사람이 고친 줄만. 손대지 않은 줄은 unreviewed(분모 밖). 사람 이름·사유 글은 싣지 않는다.",
        "categories": categories_meta(units, absent),
        "marks": dict(sorted(counts.items())),
    }
    return GoldSet(meta, tuple(units))
