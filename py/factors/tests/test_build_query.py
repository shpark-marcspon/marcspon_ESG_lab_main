"""적재 결과물과 조회 함수."""

from __future__ import annotations

import json
import sqlite3

import pytest

from factors import build as build_mod
from factors.cli import main
from factors.query import FactorStoreMissing, find_candidates
from factors.record import validation_errors


def test_jsonl_round_trip_and_schema(built):
    out, records = built
    lines = (out / build_mod.JSONL_NAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(records)
    assert [json.loads(line) for line in lines] == records
    assert all(validation_errors(json.loads(line)) == [] for line in lines[::97])


def test_sqlite_matches_jsonl(built):
    out, records = built
    con = sqlite3.connect(out / build_mod.SQLITE_NAME)
    assert con.execute("SELECT count(*) FROM factors").fetchone()[0] == len(records)
    links = sum(len(r["scope3_categories"]) for r in records)
    assert con.execute("SELECT count(*) FROM factor_scope3").fetchone()[0] == links
    assert con.execute("SELECT count(*) FROM factors WHERE source_url = '' OR source_url IS NULL").fetchone()[0] == 0
    con.close()


def test_build_is_deterministic(built, tmp_path):
    out, _ = built
    build_mod.build(tmp_path)
    assert (tmp_path / build_mod.JSONL_NAME).read_bytes() == (out / build_mod.JSONL_NAME).read_bytes()
    assert (tmp_path / build_mod.SUMMARY_NAME).read_text() == (out / build_mod.SUMMARY_NAME).read_text()


def test_find_candidates_matches_a_scan_of_the_records(built):
    out, records = built
    db = out / build_mod.SQLITE_NAME
    for cat in range(1, 16):
        for ftype in ("activity", "distance", "spend"):
            want = [r["id"] for r in records
                    if cat in r["scope3_categories"] and r["factor_type"] == ftype and r["value_status"] == "ok"]
            assert [r["id"] for r in find_candidates(cat, ftype, db_path=db)] == want


def test_find_candidates_for_the_ticket_categories(built):
    out, _ = built
    db = out / build_mod.SQLITE_NAME
    freight = find_candidates(4, "distance", db_path=db)
    assert {r["source"]["id"] for r in freight} == {"keiti-epd", "defra-2026"}
    assert find_candidates(4, "distance", db_path=db) == [
        r for r in find_candidates(9, "distance", db_path=db)
    ]
    assert {r["source"]["id"] for r in find_candidates(1, "spend", db_path=db)} == {"epa-seef-v1.3"}
    assert find_candidates(6, "distance", db_path=db) and find_candidates(7, "activity", db_path=db)
    # 조건에 맞는 계수가 없는 것은 실패가 아니라 빈 결과다
    assert find_candidates(6, "spend", db_path=db) == []
    assert find_candidates(15, "activity", db_path=db) == []


def test_disputed_license_can_be_filtered_out(built):
    out, _ = built
    db = out / build_mod.SQLITE_NAME
    only_ok = find_candidates(4, "distance", db_path=db, license_statuses=("ok",))
    assert only_ok and {r["source"]["id"] for r in only_ok} == {"defra-2026"}
    disputed = find_candidates(4, "distance", db_path=db, license_statuses=("disputed",))
    assert len(disputed) == 10 and {r["source"]["id"] for r in disputed} == {"keiti-epd"}  # KEITI 수송 10행


def test_valueless_rows_are_excluded_unless_asked(built):
    out, _ = built
    db = out / build_mod.SQLITE_NAME
    with_value = find_candidates(6, "activity", db_path=db)
    everything = find_candidates(6, "activity", db_path=db, with_value_only=False)
    assert len(everything) > len(with_value)
    assert all(r["value"] is not None for r in with_value)


def test_missing_store_and_bad_arguments_raise(tmp_path):
    with pytest.raises(FactorStoreMissing):
        find_candidates(4, "distance", db_path=tmp_path / "synthetic-none.sqlite")
    for bad in (0, 16, "4", True):
        with pytest.raises(ValueError):
            find_candidates(bad, "distance", db_path=tmp_path / "x.sqlite")
    with pytest.raises(ValueError):
        find_candidates(4, "mass", db_path=tmp_path / "x.sqlite")
    with pytest.raises(ValueError):
        find_candidates(4, "distance", db_path=tmp_path / "x.sqlite", license_statuses=("free",))


def test_summary_counts_come_from_records(built):
    out, records = built
    text = (out / build_mod.SUMMARY_NAME).read_text(encoding="utf-8")
    assert f"전체 {len(records)}건" in text
    assert "| keiti-epd |" in text and "| 398 |" in text
    assert build_mod.render_summary(records, build_mod.read_mapping(out)) == text


def test_cli_summary_fails_without_build(tmp_path, capsys):
    assert main(["summary", "--dir", str(tmp_path)]) == 1
    assert "실패" in capsys.readouterr().err


def test_cli_summary_and_query(built, capsys):
    out, records = built
    assert main(["summary", "--dir", str(out)]) == 0
    assert f"전체 {len(records)}건" in capsys.readouterr().out
    assert main(["query", "4", "distance", "--dir", str(out), "--limit", "2"]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 2
