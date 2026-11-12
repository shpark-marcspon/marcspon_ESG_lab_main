"""회귀 W3·W5, L2 부름 수, L5 퍼지 표. 고정물은 지은 것(synthetic). import 는 절대 경로만."""

import json

import pytest
from evalrig.scope3 import cli
from evalrig.scope3.calls import key_hash, read_calls, summarize
from evalrig.scope3.fuzzy import bucket, render_md, table
from evalrig.scope3.tests import build
from evalrig.scope3.regress import DEFAULT_W3, DEFAULT_W5, build_w5, judge_w3, score_w3, score_w5
from evalrig.scope3.unit import ToolFailure

W3_DOC = json.loads(DEFAULT_W3.read_text("utf-8"))


# ── W3 ─────────────────────────────────────────────────────────────────────────────────────────────


def test_w3_shipped_cases_shape():
    cases = W3_DOC["cases"]
    assert len({c["id"] for c in cases}) == len(cases)
    sheets_ev = {c["sheet"] for c in cases if "전기차" in c["text"] and c["expect"]["kind"] == "codeNot"}
    assert {"C4-2", "C6-2"} <= sheets_ev
    controls = [c for c in cases if c["expect"]["kind"] in ("mode", "family")]
    assert len(controls) >= 3  # 늘 「틀림」만 내는 묶음이 아님
    assert {c["expect"]["kind"] for c in cases} <= {"mode", "family", "codeNot"}


def test_regress_no_answer_is_not_pass():
    case = {"id": "x", "category": "C4", "expect": {"kind": "codeNot", "value": "rail"}}
    assert judge_w3(case, None) == "no_answer"
    assert judge_w3(case, {}) == "no_answer"
    rep = score_w3(W3_DOC, {"answers": {}})
    assert rep["summary"]["pass"] == 0 and rep["summary"]["no_answer"] == len(W3_DOC["cases"])


def test_w3_judging():
    ev = {"id": "x", "category": "C4", "expect": {"kind": "codeNot", "value": "rail"}}
    assert judge_w3(ev, {"mode": "rail"}) == "fail" and judge_w3(ev, {"mode": "road"}) == "pass"
    ktx = {"id": "y", "category": "C6", "expect": {"kind": "mode", "value": "train"}}
    assert judge_w3(ktx, {"mode": "train"}) == "pass" and judge_w3(ktx, {"mode": "bus"}) == "fail"
    coffee = {"id": "z", "category": "C1", "expect": {"kind": "family", "value": "pantry_supplies"}}
    assert judge_w3(coffee, {"family": "pantry_supplies"}) == "pass"
    assert judge_w3(coffee, {"mode": "pantry_supplies"}) == "no_answer"  # C1 은 family 칸만 본다


def test_w3_unknown_answer_id_fails():
    with pytest.raises(ToolFailure):
        score_w3(W3_DOC, {"answers": {"가상-없는-id": {"mode": "road"}}})


# ── W5 ─────────────────────────────────────────────────────────────────────────────────────────────


def _w5_files(tmp_path):
    return build.write_w5_files(tmp_path)


def test_w5_build_from_runtime_names(tmp_path):
    spec, tp, bp = _w5_files(tmp_path)
    doc = build_w5(spec, tp, bp)
    by = {c["id"]: c for c in doc["cases"]}
    assert set(by) == {"energy-01", "energy-02", "transport-spend-01", "transport-spend-02"}  # 소제목·선택안함 뺌
    assert by["energy-01"] | {} == by["energy-01"] and by["energy-01"]["expect"] == "exact" and by["energy-01"]["bookRow"] == 3
    assert by["energy-02"]["expect"] == "undecided"
    assert by["transport-spend-01"]["sameNameRows"] == 2 and by["transport-spend-01"]["expectAnswer"] == "Synthetic Truck"
    pairs = {p["id"]: p for p in doc["pairs"]}
    assert pairs["energy"]["baselineSame"] is True and pairs["transport-spend"]["baselineSame"] is False


def test_w5_scoring_undecided_out_of_pass_fail(tmp_path):
    spec, tp, bp = _w5_files(tmp_path)
    doc = build_w5(spec, tp, bp)
    rep = score_w5(doc, {"answers": {"energy-01": {"title": "가상 전기"}, "energy-02": {"title": "가상 전기"},
                                     "transport-spend-01": {"title": "Synthetic Truck 2"}}})  # fmt: skip
    assert rep["pairs"]["energy"] | {} == {"pass": 1, "fail": 0, "no_answer": 0, "of": 1, "undecided": 1, "undecidedAnswered": 1}
    assert rep["pairs"]["transport-spend"]["fail"] == 1 and rep["pairs"]["transport-spend"]["undecided"] == 1
    assert rep["summary"] == {"pass": 1, "fail": 1, "no_answer": 0, "of": 2}


def test_w5_spec_carries_no_names():
    spec = json.loads(DEFAULT_W5.read_text("utf-8"))
    assert {p["dropdown"] for p in spec["pairs"]} == {"DD_TRANSPORT_SPEND", "DD_TRAVEL_SPEND", "DD_ENERGY"}
    for p in spec["pairs"]:
        assert set(p) == {"id", "dropdown", "bookSheet", "nameColumn", "answerColumn", "baseline"}


def test_w5_cli_out_only(tmp_path):
    spec, tp, bp = _w5_files(tmp_path)
    sp = tmp_path / "spec.json"
    sp.write_text(json.dumps(spec, ensure_ascii=False), "utf-8")
    assert cli.main(["regress", "w5", "--template", str(tp), "--book", str(bp), "--spec", str(sp), "--out", str(tmp_path / "x")]) == 2
    assert cli.main(["regress", "w5", "--template", str(tp), "--book", str(bp), "--spec", str(sp), "--out", str(tmp_path / "out" / "w5")]) == 0
    assert (tmp_path / "out" / "w5" / "cases.json").exists()


# ── L2 ─────────────────────────────────────────────────────────────────────────────────────────────


def test_calls_key_excludes_retry():
    a = key_hash({"origin": "가상A", "dest": "가상B", "json_retry": ""})
    b = key_hash({"origin": "가상A", "dest": "가상B", "json_retry": "\n가상 재시도 글"})
    assert a == b
    assert key_hash({"origin": "가상A", "dest": "가상C"}) != a


def test_calls_summary(tmp_path):
    p = tmp_path / "calls.jsonl"
    p.write_text("".join(json.dumps({"promptId": pid, "keyHash": k}) + "\n" for pid, k in
                         [("p@1", "a"), ("p@1", "a"), ("p@1", "b"), ("q@1", "c")]), "utf-8")  # fmt: skip
    rep = summarize(read_calls(p))
    assert rep["prompts"]["p@1"] == {"before": 3, "after": 2, "reduction": "0.3333"}
    assert rep["total"] == {"before": 4, "after": 3, "reduction": "0.2500"}


def test_calls_rejects_text_fields(tmp_path):
    p = tmp_path / "calls.jsonl"
    p.write_text(json.dumps({"promptId": "p@1", "keyHash": "a", "text": "가상 글"}) + "\n", "utf-8")
    with pytest.raises(ToolFailure):
        read_calls(p)


# ── L5 ─────────────────────────────────────────────────────────────────────────────────────────────


def _driver(rows):
    return {"schema": "evc.l5-driver/1", "rows": rows}


def _row(name, via, score=None):
    return {"source": "dropdown", "name": name, "sheet": "에너지", "matched": "가상 붙은 줄", "ef": 0.9, "via": via, "matchType": via, "score": score}


def test_fuzzy_keeps_fallback_rows():
    rep = table(_driver([_row("가상 전기", "fallback_patch"), _row("가상 가스", "exact_raw")]))
    fb = [r for r in rep["rows"] if r["fallbackConstant"]]
    assert len(fb) == 1 and fb[0]["human"] is None
    assert rep["counts"]["fallback"] == 1 and rep["counts"]["exact"] == 1


def test_fuzzy_buckets():
    assert bucket("fuzzy", 0.75) == "fuzzy 0.72~0.8" and bucket("fuzzy", 0.8) == "fuzzy 0.8~0.93"
    assert bucket("fuzzy", 0.95) == "fuzzy other" and bucket("alias_contains", None) == "alias"
    assert bucket("none", None) == "not_found" and bucket("unit_priority_alias", 1.0) == "exact"
    with pytest.raises(ToolFailure):
        bucket("가상", None)


def test_fuzzy_report_md_without_names():
    rep = table(_driver([_row("가상 전기", "fuzzy", 0.75)]))
    assert "가상" not in render_md(rep, with_names=False)
    assert "가상 전기" in render_md(rep, with_names=True)
    assert "MARC-ENT-6" in render_md(rep, with_names=False)


def test_regress_calls_fuzzy_cli(tmp_path):
    o = tmp_path / "out"
    ans = tmp_path / "a.json"
    ans.write_text(json.dumps({"answers": {}}), "utf-8")
    assert cli.main(["regress", "w3", "--answers", str(ans), "--out", str(o / "w3")]) == 0
    calls = tmp_path / "c.jsonl"
    calls.write_text(json.dumps({"promptId": "p@1", "keyHash": "a"}) + "\n", "utf-8")
    assert cli.main(["calls", "--calls", str(calls), "--label", "가상", "--out", str(o / "l2")]) == 0
    drv = tmp_path / "d.json"
    drv.write_text(json.dumps(_driver([_row("가상 전기", "none")])), "utf-8")
    assert cli.main(["fuzzy", "--driver", str(drv), "--out", str(o / "l5")]) == 0
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "가상"}), "utf-8")
    assert cli.main(["fuzzy", "--driver", str(bad), "--out", str(o / "l5b")]) == 2
