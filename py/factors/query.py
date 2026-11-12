"""조회: Scope 3 카테고리와 계수 유형으로 후보 계수를 돌려준다.

빈 리스트는 「조건에 맞는 계수가 적재본에 없다」는 뜻이다. 적재본이 없거나 인자가 틀리면 예외다.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .build import SQLITE_NAME
from .sources import DEFAULT_OUT_DIR

FACTOR_TYPES = ("activity", "distance", "spend")
LICENSE_STATUSES = ("ok", "disputed", "unverified")


class FactorStoreMissing(Exception):
    """조회용 SQLite 가 없다. 「후보 없음」과 다르다."""


def find_candidates(
    scope3_category: int,
    factor_type: str,
    *,
    db_path: Path | None = None,
    license_statuses: tuple[str, ...] = LICENSE_STATUSES,
    with_value_only: bool = True,
) -> list[dict]:
    """license_statuses 로 disputed·unverified 출처를 걸러 낼 수 있다. 값 없는 행은 기본으로 뺀다."""
    if not isinstance(scope3_category, int) or isinstance(scope3_category, bool) or not 1 <= scope3_category <= 15:
        raise ValueError(f"scope3_category 는 1~15 의 정수여야 한다: {scope3_category!r}")
    if factor_type not in FACTOR_TYPES:
        raise ValueError(f"factor_type 은 {FACTOR_TYPES} 중 하나여야 한다: {factor_type!r}")
    unknown = set(license_statuses) - set(LICENSE_STATUSES)
    if unknown or not license_statuses:
        raise ValueError(f"license_statuses 가 틀렸다: {license_statuses!r}")
    path = Path(db_path) if db_path else DEFAULT_OUT_DIR / SQLITE_NAME
    if not path.is_file():
        raise FactorStoreMissing(f"{path} 없음. 먼저 `uv run python -m factors build --out ../data/factors`")
    marks = ",".join("?" for _ in license_statuses)
    sql = (
        "SELECT f.json FROM factors f JOIN factor_scope3 s ON s.id = f.id "
        f"WHERE s.category = ? AND f.factor_type = ? AND f.license_status IN ({marks})"
        + (" AND f.value_status = 'ok'" if with_value_only else "")
        + " ORDER BY f.rowid"
    )
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute(sql, (scope3_category, factor_type, *license_statuses)).fetchall()
    finally:
        con.close()
    return [json.loads(r[0]) for r in rows]
