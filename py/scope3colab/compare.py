"""코랩 쪽과 엔진 쪽의 줄을 시트·갈래마다 정한 칸(`rows.SHEET_FIELDS`·`rows.BRANCH_FIELDS`)으로 견준다. 숫자는 `Decimal` 같음
(허용 오차 0), 글은 그대로."""

from dataclasses import dataclass
from decimal import Decimal

from scope3colab.rows import SHEET_FIELDS, SPEND, BRANCH_SHEETS, Row, Rows, fields_of, numeric_fields


@dataclass(frozen=True)
class Diff:
    key: tuple[str, int, str]
    field: str  # 칸 이름, 줄 빠짐이면 "(줄)", 두 쪽 갈래가 다르면 "(갈래)"
    colab: str | None
    engine: str | None


def _same(row: Row, name: str, a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return a is b
    if name in numeric_fields(row.sheet, row.branch):
        return Decimal(a) == Decimal(b)
    return a == b


def compare(colab: Rows, engine: Rows) -> list[Diff]:
    """줄 열쇠(sheet, row, item)로 짝짓는다. 한쪽에만 있는 줄도 차이 한 줄이다. 차례는 코랩 쪽 줄 차례 → 엔진에만 있는 줄.

    엔진 쪽이 아직 모르는 시트 열쇠(예: S3C-b0 dump 는 C1·C2 만 낸다)는 그 시트의 줄마다 「(줄)」 차이로 나온다 — 조용히
    넘어가지 않는다. 같은 열쇠인데 갈래(거리·지출)가 다르면 칸을 견주지 않고 「(갈래)」 차이 한 줄이다.
    """
    left: dict[tuple[str, int, str], Row] = {r.key: r for r in colab.rows}
    right: dict[tuple[str, int, str], Row] = {r.key: r for r in engine.rows}
    diffs: list[Diff] = []
    for key, row in left.items():
        other = right.get(key)
        if other is None:
            diffs.append(Diff(key, "(줄)", "있음", None))
            continue
        if row.branch != other.branch:
            diffs.append(Diff(key, "(갈래)", _branch_label(row), _branch_label(other)))
            continue
        for name, _, _ in fields_of(row.sheet, row.branch):
            a, b = row.fields.get(name), other.fields.get(name)
            if not _same(row, name, a, b):
                diffs.append(Diff(key, name, a, b))
    for key in right:
        if key not in left:
            diffs.append(Diff(key, "(줄)", None, "있음"))
    return diffs


def _branch_label(row: Row) -> str | None:
    return row.branch or (SPEND if row.sheet in BRANCH_SHEETS else None)


def _cell(value: str | None) -> str:
    return "—" if value is None else value.replace("|", "\\|")


def _sheets(rows: Rows) -> list[str]:
    return list(dict.fromkeys(r.sheet for r in rows.rows))


def render_diff_md(colab: Rows, engine: Rows | None, diffs: list[Diff], meta: dict[str, str]) -> str:
    lines = ["# Scope 3 코랩 대조 — " + "·".join(SHEET_FIELDS), ""]
    lines += [f"- {k}: {v}" for k, v in meta.items()]
    per_sheet = ", ".join(f"{s} {sum(r.sheet == s for r in colab.rows)}" for s in _sheets(colab))
    lines.append(f"- 코랩 쪽 줄: {len(colab.rows)}" + (f" ({per_sheet})" if per_sheet else ""))
    for sheet in _sheets(colab):
        for branch in dict.fromkeys(r.branch for r in colab.rows if r.sheet == sheet):
            label = f"{sheet} ({branch})" if branch else sheet
            lines.append(f"- 견준 칸 {label}: {', '.join(name for name, _, _ in fields_of(sheet, branch))}")
    not_compared = sorted(set(colab.not_compared) | set(engine.not_compared if engine else ()))
    lines.append(f"- 안 견줌(견주는 시트 밖): {len(not_compared)}" + (f" — {', '.join(not_compared)}" if not_compared else ""))
    lines.append("")
    if engine is None:
        lines += ["## 결과", "", "엔진 결과 없음(`--engine-result` 없이 돌림) — 코랩 쪽만 냈다(`colab.json`).", ""]
        return "\n".join(lines)
    lines.append(f"- 엔진 쪽 줄: {len(engine.rows)}")
    missing = [s for s in _sheets(colab) if s not in set(_sheets(engine))]
    if missing:
        lines.append(f"- 엔진 쪽 줄 없음(열쇠): {', '.join(missing)}")
    lines += ["", "## 결과", ""]
    if not diffs:
        lines += ["같음 — 차이 0.", ""]
        return "\n".join(lines)
    lines += [f"차이 {len(diffs)}줄.", "", "| 시트 | 줄 | 품목 | 칸 | 코랩 | 엔진 |", "|---|---|---|---|---|---|"]
    for d in diffs:
        sheet, row, item = d.key
        lines.append(f"| {sheet} | {row} | {_cell(item)} | {d.field} | {_cell(d.colab)} | {_cell(d.engine)} |")
    lines.append("")
    return "\n".join(lines)
