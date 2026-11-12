"""1차 엔진(`colab_157`, 코랩 이식) 결과 → 채점 단위. 평가 쪽에만 둔다(`MARC-ENT-619` — esg-app 0줄).

- `from_rows1`: `scope3colab.rows/1`(대조 도구 꼴 — 1차 덤프와 코랩 결과 파일 둘 다) → 단위(`by: sheetOrder`). 읽기는
  `scope3colab.rows.rows_from_json`(새 읽개 없음).
- `from_result1`: `scope3.colab.result@1`(esg-app `contracts/shapes.py` `Scope3ColabDone`) → 단위(`by: rowId`). esg-app 을
  import 하지 않는다 — 칸 이름은 그 TypedDict 에서 옮겨 적었다(대응표 `README.md`).
"""

from dataclasses import dataclass, field
from typing import Any

from scope3colab.rows import Row, Rows, RowsFormatError, rows_from_json

from evalrig.scope3.unit import (
    ABSENT_NAICS,
    CATEGORIES,
    CLASSIFIED,
    DISTANCE,
    Factor,
    ROWS1_NO_CLASSIFICATION,
    Key,
    ToolFailure,
    Unit,
    decimal_text,
    dedup,
    factor_key_fields,
)

ROWS1 = "scope3colab.rows/1"
RESULT1 = "scope3.colab.result@1"

# rows/1 의 (시트 열쇠, 갈래) → 계수 값 칸(scope3colab `rows.py` 의 칸 표에서)
ROWS1_VALUE_FIELD: dict[tuple[str, str | None], str | None] = {
    ("C1", None): "대표EF_배출계수",
    ("C2", None): "대표EF_배출계수",
    ("C3", None): "배출계수(kgCO2eq/단위)",
    ("C4", None): "EF(kgCO2e/KRW)",
    ("C4", DISTANCE): "배출계수(거리기반)",
    ("C5", None): "배출계수(kgCO2eq/kg)",
    ("C6", None): "배출계수",
    ("C6", DISTANCE): "배출계수",
    ("C7", None): "배출계수",
    ("C8", None): "배출계수(kgCO2eq/단위)",
    ("C9", None): "EF(kgCO2e/KRW)",
    ("C9", DISTANCE): "배출계수(거리기반)",
    ("C11", None): "배출계수(kgCO2eq/단위)",
    ("C12", None): "배출계수",
    ("C13", None): "배출계수(kgCO2eq/단위)",
    ("C14", None): "배출계수(kgCO2eq/단위)",
    ("C15", None): None,  # 계수를 쓰지 않는다(배출량 하나뿐)
}
ROWS1_CO2E_FIELD: dict[str, str] = {cat: ("배출량" if cat == "C15" else "배출량(tCO2e)") for cat in CATEGORIES}


@dataclass(frozen=True)
class SheetState:
    sheet: str
    category: str | None
    status: str  # processed·failed·not_yet_supported·empty·not_target


@dataclass(frozen=True)
class AdapterOutput:
    adapter: str
    key_by: str
    units: tuple[Unit, ...]
    sheets: tuple[SheetState, ...] = field(default_factory=tuple)
    notes: dict[str, int] = field(default_factory=dict)


def _unit_from_row(r: Row) -> Unit:
    cat = r.sheet
    if cat not in CATEGORIES:
        raise ToolFailure(f"rows/1: 모르는 시트 열쇠 {cat!r}")
    value_field = ROWS1_VALUE_FIELD[(cat, r.branch)]
    keys = factor_key_fields(cat, r.branch)
    factor = None if value_field is None else Factor({k: r.fields.get(k) for k in keys}, r.fields.get(value_field))
    candidates = classification = None
    if cat in CLASSIFIED:
        candidates = dedup([r.fields.get("대표EF")] + [r.fields.get(f"추천EF{i}") for i in range(1, 5)])
        classification = dict(ROWS1_NO_CLASSIFICATION)
    return Unit(
        key=Key("sheetOrder", r.sheet, r.row, r.item),
        category=cat,
        factor=factor,
        co2e=r.fields.get(ROWS1_CO2E_FIELD[cat]),
        branch=r.branch,
        candidates=candidates,
        classification=classification,
    )


def units_from_rows(rows: Rows) -> list[Unit]:
    return [_unit_from_row(r) for r in rows.rows]


def from_rows1(data: Any) -> AdapterOutput:
    try:
        rows = rows_from_json(data, "engine")
    except RowsFormatError as error:
        raise ToolFailure(f"rows/1 꼴 어긋남 — {error}") from error
    return AdapterOutput("colab157.rows1", "sheetOrder", tuple(units_from_rows(rows)))


# ── result@1 ──────────────────────────────────────────────────────────────────────────────────────────────

ACTIVITY_DISTANCE_KINDS = frozenset({"transport_distance", "travel_distance"})
KNOWN_KINDS = frozenset({
    "purchase",
    "transport_spend", "travel_spend", "waste", "transport_distance", "travel_distance", "commute",
    "fuel_energy", "leased_asset", "leased_out_asset", "product_use", "product_eol", "franchise", "investment",
})  # fmt: skip


def category_of(text: Any) -> str:
    """`Category6` → `C6`."""
    s = str(text)
    if s.startswith("Category") and f"C{s[8:]}" in CATEGORIES:
        return f"C{s[8:]}"
    raise ToolFailure(f"result@1: 모르는 category {text!r}")


def _key_field_values(cat: str, branch: str | None, row: dict[str, Any]) -> dict[str, str | None]:
    """계수 열쇠 칸 → result@1 의 키(대응표는 README 「계수 열쇠 표」)."""
    factor = row.get("factor") or {}
    out: dict[str, str | None] = {}
    for name in factor_key_fields(cat, branch):
        if name in ("대표EF", "EF매핑명", "EF_DB"):
            value = factor.get("title")
        elif name == "NAICS":
            value = factor.get("code")
        elif name == "EF단위":
            value = factor.get("unit")
        elif name == "처리방법_정규화":
            value = row.get("treatment")
        elif name == "바이오매스 구분":
            value = (row.get("biogenic") or {}).get("note")
        elif name == "정규화이동수단":
            value = (row.get("mode") or {}).get("value")
        else:  # pragma: no cover — 표와 함께 고친다
            raise ToolFailure(f"대응 없는 계수 열쇠 칸 {name}")
        out[name] = None if value is None else str(value)
    return out


def unit_from_result_row(cat: str, row: dict[str, Any]) -> Unit:
    kind = row.get("kind")
    if kind not in KNOWN_KINDS:
        raise ToolFailure(f"result@1: 모르는 줄 kind {kind!r}")
    if (kind == "purchase") != (cat in CLASSIFIED):
        raise ToolFailure(f"result@1: {cat} 에 kind {kind}")
    branch = DISTANCE if kind in ACTIVITY_DISTANCE_KINDS else None
    raw_factor = row.get("factor")
    factor = None if raw_factor is None else Factor(_key_field_values(cat, branch, row), decimal_text(raw_factor.get("value")))
    candidates = classification = None
    if kind == "purchase":
        recs = row.get("recommendations") or []
        candidates = dedup([raw_factor.get("title") if raw_factor else None] + [r.get("title") for r in recs])
        group = row.get("group") or {}
        name = group.get("name")
        classification = {
            "group": name if name else {"status": "absent", "reason": "group.name 이 비었다"},
            "naics": ABSENT_NAICS,
        }
    return Unit(
        key=Key("rowId", row_id=str(row["rowId"])),
        category=cat,
        factor=factor,
        co2e=decimal_text(row.get("co2e")),
        branch=branch,
        candidates=candidates,
        classification=classification,
    )


def from_result1(data: Any) -> AdapterOutput:
    if not isinstance(data, dict) or data.get("schemaVersion") != RESULT1:
        raise ToolFailure(f"schemaVersion 이 {RESULT1!r} 가 아니다")
    if data.get("status") == "rejected":
        raise ToolFailure("result@1 이 rejected 다 — 채점할 줄이 없다")
    if data.get("status") != "done":
        raise ToolFailure(f"result@1: 모르는 status {data.get('status')!r}")
    units: list[Unit] = []
    sheets: list[SheetState] = []
    repeated_rep = 0
    try:
        for s in data["sheets"]:
            status = s["status"]
            cat = category_of(s["category"]) if "category" in s else None
            sheets.append(SheetState(str(s["sheet"]), cat, str(status)))
            if status != "processed":
                continue
            if cat is None:
                raise ToolFailure(f"result@1: processed 시트 {s['sheet']!r} 에 category 가 없다")
            for row in s["rows"]:
                if row.get("kind") == "purchase" and row.get("factor") and row["factor"].get("title") is not None:
                    titles = [r.get("title") for r in row.get("recommendations") or []]
                    repeated_rep += int(row["factor"]["title"] in titles)
                units.append(unit_from_result_row(cat, row))
    except (KeyError, TypeError, AttributeError) as error:
        raise ToolFailure(f"result@1 꼴 어긋남 — {error!r}") from error
    seen: set[str] = set()
    for u in units:
        if u.key.row_id in seen:
            raise ToolFailure(f"result@1: rowId {u.key.row_id} 가 둘 이상")
        seen.add(str(u.key.row_id))
    return AdapterOutput("colab157.result1", "rowId", tuple(units), tuple(sheets), {"recommendationRepeatsRepresentative": repeated_rep})
