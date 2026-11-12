"""T7 — 엑셀 원천 ↔ PDF 대사(발주서 EVB §2-7). KB 값은 git 에 이미 있는 kb-2025.gold.json 의 raw 뿐."""
from __future__ import annotations

import collections

import jsonschema

from evalrig.dataverify import cli, xpdf
from evalrig.dataverify import gold as G
from evalrig.dataverify.units import ITEM_SCHEMA


def _build(kb_gold, catalog):
    return xpdf.build(kb_gold, catalog, kb_gold_sha16=G.file_sha16(cli.KB_GOLD), catalog_sha16=G.file_sha16(cli.CATALOG))


def _raw_at(kb_gold):
    out = {}
    for tb in kb_gold["tables"]:
        for c in tb["cells"]:
            out[f"{tb['tableId']}:{c['cell']}"] = c["raw"]
    return out


def test_t7_original_equals_gold_raw(kb_gold, catalog, git_file):
    import json

    raw = _raw_at(kb_gold)
    for plants in (_build(kb_gold, catalog)[1], json.loads(git_file("kb-2025-xpdf.plants.json"))):
        for p in plants["plants"]:
            if p["cell"] is None:
                assert p["kind"] == "source_only" and p["original"] is None
            else:
                assert p["original"] == raw[p["cell"]], p


def test_t7_plants_count_and_kinds(kb_gold, catalog):
    _rows, plants, _gs = _build(kb_gold, catalog)
    events = {p["event"]: p["kind"] for p in plants["plants"]}
    assert len(events) == plants["events"] >= 8
    kinds = collections.Counter(events.values())
    assert set(kinds) == {"digit", "unit_scale", "rounding", "year_swap", "source_only", "report_only"}
    per_ind = collections.defaultdict(set)
    for p in plants["plants"]:
        per_ind[p["indicator"]].add(p["event"])
    assert all(len(v) == 1 for v in per_ind.values())  # 한 지표에 심기 하나


def test_t7_gold_cells_are_rows_by_years(kb_gold, catalog):
    rows, _plants, gs = _build(kb_gold, catalog)
    cells, no_year = xpdf.indicator_cells(kb_gold)
    assert (len(cells), len(no_year)) == (44, 4)
    report = {(c["indicator"], c["period"]) for c in cells}
    source = {(r["ID"], y) for r in rows for y in r["values"]}
    gold = {(it["indicator"], it["year"]) for it in gs["items"]}
    assert len(gold) == len(gs["items"])
    assert gold == report | source
    v = collections.Counter(it["verdict"] for it in gs["items"])
    assert v["report_only"] == len(report - source) and v["source_only"] == len(source - report)
    assert gs["coverage"] == "exhaustive" and gs["synthetic"] is False
    assert gs["meta"]["v1Score"]["status"] == "not_applicable"
    iv = jsonschema.Draft202012Validator(ITEM_SCHEMA)
    for it in gs["items"]:
        iv.validate(it)
    # 정답 판정이 원천·보고서 값의 견줌과 맞는다
    rep = {(c["indicator"], c["period"]): xpdf.parse_raw(c["raw"]) for c in cells}
    src = {(r["ID"], y): val for r in rows for y, val in r["values"].items()}
    for it in gs["items"]:
        k = (it["indicator"], it["year"])
        if it["verdict"] == "match" and it["kind"] is None:
            assert src[k] == rep[k]
        elif it["verdict"] == "mismatch":
            assert src[k] != rep[k]
        elif it["kind"] == "rounding":
            assert src[k] != rep[k] and round(src[k]) == rep[k]


def test_t7_xlsx_reads_back_as_source(kb_gold, catalog, tmp_path):
    rows, _p, _g = _build(kb_gold, catalog)
    path = tmp_path / "src.xlsx"
    xpdf.write_xlsx(rows, path)
    assert xpdf.canonical_rows(xpdf.read_xlsx(path)) == xpdf.canonical_rows(rows)


def test_t7_pdf_is_the_gold_document(kb_gold):
    pdf = cli.default_pdf()
    assert pdf is not None, f"{cli.PDF_NAME} 를 찾지 못함 — 조용한 초록 금지"
    assert xpdf.pdf_sha256(pdf) == kb_gold["documents"]["a"]["pdfSha256"]


def test_t7_git_files_equal_build(kb_gold, catalog, git_file):
    _rows, plants, gs = _build(kb_gold, catalog)
    assert G.dumps_gold(plants) == git_file("kb-2025-xpdf.plants.json")
    assert G.dumps_gold(gs) == git_file("kb-2025-xpdf.gold.json")
