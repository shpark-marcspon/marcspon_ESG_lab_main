"""적재: 모든 출처를 읽어 factors.jsonl, factors.sqlite, mappings.kr-ets.json, SUMMARY.md 를 쓴다.

한 출처라도 읽지 못하면 아무것도 쓰지 않고 실패한다. 일부만 적재된 표를 남기지 않는다.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

from . import mappings as mappings_mod
from .loaders import LOADERS
from .record import validate
from .sources import SOURCES

JSONL_NAME = "factors.jsonl"
SQLITE_NAME = "factors.sqlite"
SUMMARY_NAME = "SUMMARY.md"


class BuildError(Exception):
    pass


def load_all() -> list[dict]:
    records: list[dict] = []
    for source_id, loader in LOADERS.items():
        loaded = loader()
        if not loaded:
            raise BuildError(f"{source_id}: 레코드가 0건이다. 빈 적재를 성공으로 치지 않는다")
        records.extend(loaded)
    seen: set[str] = set()
    for r in records:
        validate(r)
        if not r["id"].startswith(r["source"]["id"] + ":") or r["synthetic"]:
            raise BuildError(f"id 가 출처 id 로 시작하지 않거나 synthetic 레코드다: {r['id']}")
        if r["id"] in seen:
            raise BuildError(f"id 중복: {r['id']}")
        seen.add(r["id"])
    return records


def write_jsonl(records: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(f"{path} 없음. 먼저 `uv run python -m factors build --out ../data/factors`")
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_sqlite(records: list[dict], path: Path) -> None:
    """JSONL 에서 다시 만들 수 있는 조회용 파생물. 커밋하지 않는다."""
    path.unlink(missing_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(
            """
            CREATE TABLE factors (
              id TEXT PRIMARY KEY, source_id TEXT NOT NULL, record_type TEXT NOT NULL, scope TEXT NOT NULL,
              factor_type TEXT NOT NULL, name_original TEXT NOT NULL, value REAL, value_raw TEXT,
              value_status TEXT NOT NULL, unit TEXT NOT NULL, gas TEXT NOT NULL, gas_basis TEXT NOT NULL,
              region TEXT NOT NULL, year INTEGER, license_status TEXT NOT NULL, source_url TEXT NOT NULL,
              json TEXT NOT NULL
            );
            CREATE TABLE factor_scope3 (
              id TEXT NOT NULL REFERENCES factors(id), category INTEGER NOT NULL, PRIMARY KEY (id, category)
            );
            CREATE INDEX factor_scope3_cat ON factor_scope3(category);
            CREATE INDEX factors_type ON factors(factor_type, scope);
            """
        )
        con.executemany(
            "INSERT INTO factors VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (r["id"], r["source"]["id"], r["record_type"], r["scope"], r["factor_type"], r["name"]["original"],
                 r["value"], r["value_raw"], r["value_status"], r["unit"], r["gas"], r["gas_basis"], r["region"],
                 r["year"], r["license_status"], r["source"]["url"], json.dumps(r, ensure_ascii=False))
                for r in records
            ],
        )
        con.executemany(
            "INSERT INTO factor_scope3 VALUES (?,?)",
            [(r["id"], c) for r in records for c in r["scope3_categories"]],
        )
        con.commit()
    finally:
        con.close()


def _table(header: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def render_summary(records: list[dict], mapping: dict | None = None) -> str:
    """출처별·카테고리별·유형별 건수. 수치는 전부 records 와 mapping 에서 센 것이다."""
    types = ["activity", "distance", "spend"]
    by_source = Counter(r["source"]["id"] for r in records)
    no_value = Counter(r["source"]["id"] for r in records if r["value_status"] != "ok")
    out = ["# 정규화 계수 요약", "",
           "`uv run python -m factors build` 가 쓴 파일이다. 손으로 고치지 않는다.", "",
           f"전체 {len(records)}건. 값이 없는 행(`value_status` 가 ok 가 아닌 것)도 건수에 든다.", "",
           "## 출처별", ""]
    out.append(_table(
        ["출처 id", "출처", "버전", "확인 일자", "라이선스 상태", "건수", "그중 값 없음"],
        [[sid, SOURCES[sid].name, SOURCES[sid].version, SOURCES[sid].retrieved, SOURCES[sid].license_status,
          by_source[sid], no_value[sid]] for sid in LOADERS],
    ))
    out += ["", "## Scope 3 카테고리 후보 × 계수 유형", "",
            "한 레코드가 여러 카테고리의 후보일 수 있어 합이 전체 건수와 맞지 않는다. 기체별 행(CO2, CH4, N2O)과 값 없는 행을 포함한 건수다.", ""]
    cats = sorted({c for r in records for c in r["scope3_categories"]})
    cell = Counter((c, r["factor_type"]) for r in records for c in r["scope3_categories"])
    usable = Counter(
        c for r in records for c in r["scope3_categories"]
        if r["value_status"] == "ok" and r["gas"] == "CO2e" and r["record_type"] == "emission_factor"
    )
    out.append(_table(
        ["카테고리"] + types + ["계", "그중 CO2e 값 있음"],
        [[c] + [cell[(c, t)] for t in types] + [sum(cell[(c, t)] for t in types), usable[c]] for c in cats],
    ))
    out += ["", "## Scope 3 카테고리 후보 × 출처", ""]
    src_cell = Counter((c, r["source"]["id"]) for r in records for c in r["scope3_categories"])
    used = [sid for sid in LOADERS if any(src_cell[(c, sid)] for c in cats)]
    out.append(_table(["카테고리"] + used, [[c] + [src_cell[(c, sid)] for sid in used] for c in cats]))
    out += ["", "## Scope × 레코드 종류", ""]
    kinds = sorted({r["record_type"] for r in records})
    sc = Counter((r["scope"], r["record_type"]) for r in records)
    scopes = sorted({r["scope"] for r in records})
    out.append(_table(["scope"] + kinds, [[s] + [sc[(s, k)] for k in kinds] for s in scopes]))
    out += ["", "## 라이선스 상태", ""]
    lic = Counter(r["license_status"] for r in records)
    out.append(_table(["license_status", "건수"], [[k, lic[k]] for k in sorted(lic)]))
    if mapping is not None:
        out += mappings_mod.summary_lines(mapping)
    return "\n".join(out) + "\n"


def read_mapping(out_dir: Path) -> dict | None:
    path = out_dir / mappings_mod.MAPPING_NAME
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def build(out_dir: Path) -> list[dict]:
    records = load_all()
    mapping = mappings_mod.build_mappings(records)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(records, out_dir / JSONL_NAME)
    write_sqlite(records, out_dir / SQLITE_NAME)
    mappings_mod.write_mappings(mapping, out_dir / mappings_mod.MAPPING_NAME)
    (out_dir / SUMMARY_NAME).write_text(render_summary(records, mapping), encoding="utf-8")
    return records
