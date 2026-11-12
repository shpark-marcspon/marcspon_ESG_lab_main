"""python -m factors {build,summary,fetch,query}"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from . import build as build_mod
from . import mappings as mappings_mod
from .query import FACTOR_TYPES, FactorStoreMissing, find_candidates
from .record import RecordInvalid
from .sources import DEFAULT_OUT_DIR, SOURCES, SourceFormatError, SourceUnavailable


def _cmd_build(args) -> int:
    out = Path(args.out)
    records = build_mod.build(out)
    size = (out / build_mod.JSONL_NAME).stat().st_size
    print(f"{len(records)}건 → {out / build_mod.JSONL_NAME} ({size / 1e6:.1f} MB), {build_mod.SQLITE_NAME}, {mappings_mod.MAPPING_NAME}, {build_mod.SUMMARY_NAME}")
    return 0


def _cmd_summary(args) -> int:
    records = build_mod.read_jsonl(Path(args.dir) / build_mod.JSONL_NAME)
    print(build_mod.render_summary(records, build_mod.read_mapping(Path(args.dir))), end="")
    return 0


def _cmd_fetch(args) -> int:
    """공개 원자료를 data/raw 로 내려받는다. 이미 있으면 건드리지 않는다. 로그인이 필요한 출처는 없다."""
    for src in SOURCES.values():
        if not src.download_url:
            continue
        if src.path.is_file():
            print(f"있음   {src.id} {src.path}")
            continue
        src.path.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(src.download_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        if len(data) < 1024:
            raise SourceUnavailable(f"{src.id}: 내려받은 파일이 {len(data)}바이트다. 주소가 바뀌었을 수 있다 {src.download_url}")
        src.path.write_bytes(data)
        print(f"받음   {src.id} {src.path} ({len(data)} bytes)")
    return 0


def _cmd_query(args) -> int:
    found = find_candidates(args.category, args.type, db_path=Path(args.dir) / build_mod.SQLITE_NAME)
    for r in found[: args.limit]:
        print(json.dumps({k: r[k] for k in ("id", "value_raw", "unit", "gas", "license_status")}
                         | {"name": r["name"]["original"]}, ensure_ascii=False))
    print(f"후보 {len(found)}건 (카테고리 {args.category}, {args.type})", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="factors", description="공개 배출계수 정규화 적재")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="모든 출처를 읽어 JSONL·SQLite·대응표·SUMMARY.md 를 쓴다")
    b.add_argument("--out", default=str(DEFAULT_OUT_DIR))
    b.set_defaults(fn=_cmd_build)
    s = sub.add_parser("summary", help="적재본의 출처별·카테고리별 건수를 찍는다")
    s.add_argument("--dir", default=str(DEFAULT_OUT_DIR))
    s.set_defaults(fn=_cmd_summary)
    f = sub.add_parser("fetch", help="공개 원자료를 data/raw 로 내려받는다")
    f.set_defaults(fn=_cmd_fetch)
    q = sub.add_parser("query", help="Scope 3 카테고리와 계수 유형으로 후보를 찍는다")
    q.add_argument("category", type=int)
    q.add_argument("type", choices=FACTOR_TYPES)
    q.add_argument("--dir", default=str(DEFAULT_OUT_DIR))
    q.add_argument("--limit", type=int, default=20)
    q.set_defaults(fn=_cmd_query)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except (SourceUnavailable, SourceFormatError, RecordInvalid, mappings_mod.MappingInvalid, build_mod.BuildError,
            FactorStoreMissing, FileNotFoundError, ValueError) as e:
        print(f"실패: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
