"""정답 셋 변환 둘(받은 예시 꼴·사람 수정 기록)과 채점기. 고정물은 지은 것(synthetic). import 는 절대 경로만."""

import json
from decimal import Decimal

import pytest

from evalrig.scope3 import cli
from evalrig.scope3.adapters import adapt
from evalrig.scope3.adapters.colab157 import from_rows1
from evalrig.scope3.gold_edits import gold_from_edits
from evalrig.scope3.gold_example import C1C2_ABSENT, gold_from_example
from evalrig.scope3.score import co2e_error, score
from evalrig.scope3.tests import build
from evalrig.scope3.unit import CATEGORIES, GoldSet, ToolFailure, Unit, categories_meta


def _gold_from_rows(rows, absent=None):
    units = list(from_rows1(build.rows1(rows)).units)
    meta = {"keyBy": "sheetOrder", "producedBy": "synthetic", "reviewed": "no", "source": {"kind": "synthetic"},
            "categories": categories_meta(units, absent or {})}  # fmt: skip
    return GoldSet(meta, tuple(units))


def _edits_gold(tmp_path, result, edit_rows):
    r, e = tmp_path / "r.json", tmp_path / "e.json"
    r.write_text(json.dumps(result), "utf-8")
    e.write_text(json.dumps(build.edits(edit_rows)), "utf-8")
    return gold_from_edits(r, e, result, build.edits(edit_rows))


# ── 받은 예시 꼴 → 정답 셋 ────────────────────────────────────────────────────────────────────────────


def test_gold_example_categories_and_c1c2_absent(tmp_path):
    gold = gold_from_example(build.write_result_xlsx(tmp_path / "x.xlsx"))
    cats = gold.meta["categories"]
    assert set(cats) == set(CATEGORIES)
    assert cats["C3"] == {"status": "present", "rows": 2} and cats["C6"] == {"status": "present", "rows": 2}
    assert cats["C1"] == cats["C2"] == {"status": "absent", "reason": C1C2_ABSENT}
    assert gold.meta["reviewed"] == "unknown" and gold.meta["producedBy"] == "colab"
    distance = [u for u in gold.units if u.category == "C6" and u.branch == "Distance"]
    assert distance[0].factor.key_fields == {"정규화이동수단": "가상 버스"}


def test_gold_example_meta_has_no_path(tmp_path):
    path = build.write_result_xlsx(tmp_path / "가상회사_template_result.xlsx")
    text = json.dumps(gold_from_example(path).to_json(), ensure_ascii=False)
    assert "가상회사" not in text and str(tmp_path) not in text
    assert gold_from_example(path).meta["source"]["sha256"]


def test_gold_example_out_only(tmp_path, capsys):
    """출력이 out/ 아래가 아니면 쓰지 않고 2."""
    x = build.write_result_xlsx(tmp_path / "x.xlsx")
    bad = tmp_path / "elsewhere" / "gold.json"
    assert cli.main(["gold-example", "--result-xlsx", str(x), "--out", str(bad)]) == 2
    assert not bad.exists()
    good = tmp_path / "out" / "gold.json"
    assert cli.main(["gold-example", "--result-xlsx", str(x), "--out", str(good)]) == 0
    assert GoldSet.from_json(json.loads(good.read_text("utf-8"))).units


def test_gold_set_rejects_mixed_key_kinds():
    gold = _gold_from_rows([build.energy_row(1, "가상", "kg", "0.9", "0.9")])
    with pytest.raises(ToolFailure):
        GoldSet(gold.meta | {"keyBy": "rowId"}, gold.units)


# ── 사람 수정 기록 → 정답 셋 ──────────────────────────────────────────────────────────────────────────


def test_edits_untouched_is_unreviewed(tmp_path):
    gold = _edits_gold(tmp_path, build.base_result1(), [])
    assert {u.gold for u in gold.units} == {"unreviewed"}
    rep = score(gold, adapt(build.base_result1()))
    assert rep["categories"]["C1"]["n"] == 0 and rep["categories"]["C1"]["unreviewed"] == 2
    assert rep["categories"]["C1"]["hit1"]["rate"] is None


def test_edits_choices(tmp_path):
    gold = _edits_gold(tmp_path, build.base_result1(), [
        build.edit("C1_가상!4", "C1", choice="recommended", rank=2, co2e="0.99"),
        build.edit("C1_가상!5", "C1", choice="custom", title="가상 직접 계수", value="0.999", co2e="0.9999"),
        build.edit("C6-2_가상!4", "C6", choice="applied"),
    ])  # fmt: skip
    by = {u.key.row_id: u for u in gold.units}
    assert by["C1_가상!4"].factor.key_fields == {"대표EF": "가상 계수 C"} and by["C1_가상!4"].source == "recommended:2"
    assert by["C1_가상!4"].co2e == "0.99"
    assert by["C1_가상!5"].factor.value == "0.999" and by["C1_가상!5"].co2e == "0.9999"
    assert by["C6-2_가상!4"].factor.key_fields == {"EF_DB": "가상 항공 지출", "NAICS": "999999"}
    rep = score(gold, adapt(build.base_result1()))
    c1 = rep["categories"]["C1"]
    assert c1["hit1"]["hits"] == 0 and c1["hit1"]["of"] == 2
    assert c1["hit3"]["hits"] == 1 and c1["hit3"]["of"] == 2  # 추천 2 는 앞 셋 안, 직접 입력은 늘 놓침
    assert rep["categories"]["C6"]["hit1"]["hits"] == 1
    assert c1["classification"]["all"]["status"] == "no_gold"


def test_excluded_out_of_denominator(tmp_path):
    gold = _edits_gold(tmp_path, build.base_result1(), [
        build.edit("C1_가상!4", "C1", excluded=True),
        build.edit("C1_가상!5", "C1", choice="applied"),
    ])  # fmt: skip
    c1 = score(gold, adapt(build.base_result1()))["categories"]["C1"]
    assert c1["excluded"] == 1 and c1["n"] == 1
    assert c1["hit1"] == {"hits": 1, "of": 1, "rate": "1.0000", "miss": 0, "missing": 0, "noFactor": 0}


def test_edits_excluded_wins_over_factor_and_restored_is_unreviewed(tmp_path):
    """R3: 제외 + 계수 사건 → excluded, 제외했다 되돌림(factor null) → unreviewed."""
    gold = _edits_gold(tmp_path, build.base_result1(), [
        build.edit("C1_가상!4", "C1", excluded=True, choice="applied"),
        build.edit("C1_가상!5", "C1"),
    ])  # fmt: skip
    by = {u.key.row_id: u.gold for u in gold.units}
    assert by["C1_가상!4"] == "excluded" and by["C1_가상!5"] == "unreviewed"
    assert gold.meta["marks"]["restoredOnly"] == 1


def test_edits_drop_person(tmp_path):
    gold = _edits_gold(tmp_path, build.base_result1(), [build.edit("C1_가상!4", "C1", choice="applied")])
    text = json.dumps(gold.to_json(), ensure_ascii=False)
    assert "가상사람이름" not in text and "가상사람id" not in text
    assert "가상 사유 글" not in text and "editedBy" not in text


def test_edits_unknown_row_fails(tmp_path):
    with pytest.raises(ToolFailure):
        _edits_gold(tmp_path, build.base_result1(), [build.edit("가상!0", "C1", choice="applied")])
    with pytest.raises(ToolFailure):
        _edits_gold(tmp_path, build.base_result1(), [build.edit("C1_가상!4", "C1", choice="recommended", rank=5)])


def test_edits_failed_sheet_category_absent(tmp_path):
    gold = _edits_gold(tmp_path, build.base_result1(), [])
    assert gold.meta["categories"]["C5"]["status"] == "absent"
    rep = score(gold, adapt(build.base_result1()))
    assert rep["result"]["sheetsFailed"] == 1


# ── 채점기 ──────────────────────────────────────────────────────────────────────────────────────────


def test_score_hit3_window():
    gold = _gold_from_rows([build.purchase_row(1, "가상 넷째", "0.9", [])])
    got = from_rows1(build.rows1([build.purchase_row(1, "가상 첫째", "0.9", ["가상 둘째", "가상 셋째", "가상 넷째"])]))
    c1 = score(gold, got)["categories"]["C1"]
    assert c1["hit3"]["hits"] == 0 and c1["hit3"]["window"] == 3
    got3 = from_rows1(build.rows1([build.purchase_row(1, "가상 첫째", "0.9", ["가상 넷째"])]))
    assert score(gold, got3)["categories"]["C1"]["hit3"]["hits"] == 1


def test_co2e_relative_error_uses_gold():
    assert co2e_error("9", "18").error == Decimal(1)  # |18-9|/9 — 결과로 나누면 0.5
    assert co2e_error("0", "0.9").kind == "zero_gold" and co2e_error("0", "0.9").error == Decimal("0.9")
    assert co2e_error(None, "0.9").kind == "missing"
    assert co2e_error(None, None).kind == "both_empty"


def test_no_gold_is_not_zero():
    gold = _gold_from_rows([build.energy_row(1, "가상", "kg", "0.9", "0.9")], absent={"C1": "가상 까닭", "C2": "가상 까닭"})
    rep = score(gold, from_rows1(build.rows1([build.energy_row(1, "가상", "kg", "0.9", "0.9")])))
    for name in ("classification", "top3"):
        t = rep["targets"][name]
        assert t["status"] == "no_gold" and t["reason"] == "가상 까닭" and "rate" not in t
    assert rep["categories"]["C1"] == {"status": "no_gold", "reason": "가상 까닭"}


def test_classification_no_result_for_rows1():
    """정답엔 분류가 있는데 결과가 rows/1(분류 통째 absent)이면 no_result — 0 이 아니다."""
    res = build.result1([build.processed("C1", "Category1", [build.purchase1("C1!4", "가상 A", "0.9", [])])])
    gold = GoldSet({"keyBy": "rowId", "categories": categories_meta(list(adapt(res).units), {})}, adapt(res).units)
    rep = score(gold, adapt(res))
    assert rep["targets"]["classification"]["rate"] == "1.0000"
    rows_gold = _gold_from_rows([build.purchase_row(1, "가상 A", "0.9", [])])
    rep2 = score(rows_gold, from_rows1(build.rows1([build.purchase_row(1, "가상 A", "0.9", [])])))
    assert rep2["categories"]["C1"]["classification"]["all"]["status"] == "no_gold"
    # 정답(sheetOrder)에 분류가 있고 결과가 rows/1(분류 통째 absent) → no_result, 0 으로 접지 않는다(검증 C1 B1)
    (base,) = from_rows1(build.rows1([build.purchase_row(1, "가상 A", "0.9", [])])).units
    labelled = Unit(base.key, "C1", base.factor, base.co2e, candidates=base.candidates,
                    classification={"group": "가상 묶음", "naics": {"status": "absent", "reason": "가상"}})  # fmt: skip
    gold3 = GoldSet({"keyBy": "sheetOrder", "categories": categories_meta([labelled], {})}, (labelled,))
    rep3 = score(gold3, from_rows1(build.rows1([build.purchase_row(1, "가상 A", "0.9", [])])))
    cls = rep3["categories"]["C1"]["classification"]
    assert cls["group"]["status"] == "no_result" and cls["all"]["status"] == "no_result" and "rate" not in cls["group"]
    assert cls["naics"]["status"] == "no_gold"
    assert rep3["targets"]["classification"]["status"] == "no_result" and "rate" not in rep3["targets"]["classification"]
    assert rep3["targets"]["top3"]["rate"] == "1.0000"


def test_sheet_order_key_uses_item():
    """같은 (시트, 차례)라도 품목 글이 다르면 다른 줄 — 짝짓지 않는다(검증 C1 R2)."""
    gold = _gold_from_rows([build.energy_row(1, "가상", "kg", "0.9", "0.9")])
    other = build.energy_row(1, "가상", "kg", "0.9", "0.9") | {"item": "가상 다른 연료"}
    c3 = score(gold, from_rows1(build.rows1([other])))["categories"]["C3"]
    assert c3["missingRows"] == 1 and c3["extraRows"] == 1 and c3["hit1"]["hits"] == 0


def test_classification_group_mismatch_counts():
    res = build.result1([build.processed("C1", "Category1", [build.purchase1("C1!4", "가상 A", "0.9", [])])])
    gold = GoldSet({"keyBy": "rowId", "categories": categories_meta(list(adapt(res).units), {})}, adapt(res).units)
    other = build.result1([build.processed("C1", "Category1", [build.purchase1("C1!4", "가상 A", "0.9", [], group="가상 딴 묶음")])])
    rep = score(gold, adapt(other))
    assert rep["categories"]["C1"]["classification"]["group"]["hits"] == 0
    assert rep["categories"]["C1"]["classification"]["naics"]["status"] == "no_gold"
    assert rep["targets"]["classification"]["met"] is False


def test_key_kind_mismatch_exit2(tmp_path):
    gold = _gold_from_rows([build.energy_row(1, "가상", "kg", "0.9", "0.9")])
    with pytest.raises(ToolFailure):
        score(gold, adapt(build.base_result1()))
    g, r = tmp_path / "out" / "g.json", tmp_path / "r.json"
    g.parent.mkdir()
    g.write_text(json.dumps(gold.to_json()), "utf-8")
    r.write_text(json.dumps(build.base_result1()), "utf-8")
    assert cli.main(["score", "--gold", str(g), "--result", str(r), "--out", str(tmp_path / "out" / "s")]) == 2


def test_score_missing_extra_and_mismatch():
    gold = _gold_from_rows([build.energy_row(1, "가상 A", "kg", "0.9", "0.9"), build.energy_row(2, "가상 B", "kg", "0.9", "0.9")])
    got = from_rows1(build.rows1([build.energy_row(1, "가상 A", "L", "0.9", "0.99"), build.energy_row(3, "가상 C", "kg", "0.9", "0.9")]))
    c3 = score(gold, got)["categories"]["C3"]
    assert c3["hit1"] | {} == {"hits": 0, "of": 2, "rate": "0.0000", "miss": 1, "missing": 1, "noFactor": 0}
    assert c3["missingRows"] == 1 and c3["extraRows"] == 1
    assert c3["co2e"]["ok"] == 1 and c3["co2e"]["missing"] == 1 and c3["co2e"]["max"] == "0.1"


def test_score_cli_writes_md_without_item_text(tmp_path):
    x = build.write_result_xlsx(tmp_path / "x.xlsx")
    g = tmp_path / "out" / "gold.json"
    assert cli.main(["gold-example", "--result-xlsx", str(x), "--out", str(g)]) == 0
    res = tmp_path / "eng.json"
    from scope3colab.rows import rows_from_result_xlsx

    res.write_text(json.dumps(rows_from_result_xlsx(x).to_json() | {"side": "engine"}, ensure_ascii=False), "utf-8")
    assert cli.main(["score", "--gold", str(g), "--result", str(res), "--out", str(tmp_path / "out" / "s")]) == 0
    md = (tmp_path / "out" / "s" / "score.md").read_text("utf-8")
    assert "가상" not in md
    rep = json.loads((tmp_path / "out" / "s" / "score.json").read_text("utf-8"))
    assert rep["categories"]["C3"]["hit1"]["hits"] == 2 and rep["categories"]["C6"]["hit1"]["hits"] == 2
