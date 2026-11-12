"""MC1 측정 도구 시험 — 고정물은 전부 지은 것(가상 Title·가상 코드·가상 id). 148행 파일만 저장소의 가상 자료를 읽는다."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from evalrig.scope3 import adapters
from evalrig.scope3.measure_cost import Go, Stop, cumulative_gate, load_pricing, read_records, summarize
from evalrig.scope3.measure_l4 import GoldRow, L4Gold, book_codes_of, gold_l4, score_colab, score_llm_first
from evalrig.scope3.tests.build import purchase_row, rows1

ROWS_148 = Path(__file__).resolve().parents[4] / "data" / "scope3-examples" / "rows.synthetic.jsonl"
# 148행 C1·C2 정답 NAICS 48 코드 가운데 책 `구매_USEPA` 밖인 16(MC1 critic 1 B1 — esg-app 968de65f 책에서 셈. 공개 NAICS 코드)
OFF_BOOK = [
    "236210", "325611", "333517", "333914", "334118", "335122", "339940", "517311",
    "524126", "532420", "541199", "541219", "541330", "541380", "541611", "561730",
]  # fmt: skip


# ── ① 정답 갈래 ────────────────────────────────────────────────────────────────────────────────────────────


def _all_gold_naics() -> set[str]:
    out = set()
    for line in ROWS_148.read_text("utf-8").splitlines():
        g = json.loads(line)["gold"]
        if g.get("category") in (1, 2):
            out |= {i.split(":", 1)[1] for i in g["factor_ids"] if i.startswith("epa-seef-v1.3:")}
    return out


def test_gold_l4_branches_91_53_37_16_38():
    gold_codes = _all_gold_naics()
    assert len(gold_codes) == 48 and set(OFF_BOOK) <= gold_codes
    book = frozenset(gold_codes - set(OFF_BOOK)) | {"999999"}  # 책 안 코드 = 정답 코드 − 책 밖 16(+ 정답에 없는 코드)
    gold = gold_l4(ROWS_148, book)
    assert len(gold.rows) == 91
    assert len(gold.by_branch("naics_in_book", "naics_off_book")) == 53
    assert len(gold.by_branch("naics_in_book")) == 37
    assert len(gold.by_branch("naics_off_book")) == 16
    assert len(gold.by_branch("activity_only")) == 38
    assert gold.meta["producedBy"] == "runner-label (synthetic, review 0)"


# ── ② 코랩 순서 채점 ────────────────────────────────────────────────────────────────────────────────────────


def _gold() -> L4Gold:
    return L4Gold(
        (
            GoldRow("g1", 1, frozenset({"epa-seef-v1.3:999991"}), frozenset({"999991"}), "naics_in_book"),
            GoldRow("g2", 1, frozenset({"epa-seef-v1.3:999992"}), frozenset({"999992"}), "naics_in_book"),
            GoldRow("g3", 2, frozenset({"epa-seef-v1.3:999993"}), frozenset({"999993"}), "naics_in_book"),
            GoldRow("g4", 1, frozenset({"epa-seef-v1.3:999994"}), frozenset({"999994"}), "naics_in_book"),
            GoldRow("g5", 1, frozenset({"가상-db:a1"}), frozenset(), "activity_only"),
            GoldRow("g6", 1, frozenset({"epa-seef-v1.3:888888"}), frozenset({"888888"}), "naics_off_book"),
        ),
        {"producedBy": "test", "source": "test"},
    )


TITLE_CODES = {
    "가상 T1": ["999991"],
    "가상 T2": ["999992"],
    "가상 T3": ["999993", "999990"],  # 한 Title 에 코드 둘
    "가상 T4": ["999994"],
    "가상 X": ["999900"],
    "가상 Y": ["999901"],
    "가상 Z": ["999902"],
}
ORDER = [
    {"row_id": "g1", "sheet": "C1", "row": 1},
    {"row_id": "g2", "sheet": "C1", "row": 2},
    {"row_id": "g4", "sheet": "C1", "row": 3},
    {"row_id": "g5", "sheet": "C1", "row": 4},
    {"row_id": "g3", "sheet": "C2", "row": 1},
    {"row_id": "g6", "sheet": "C1", "row": 5},
]


def _colab_result() -> dict:
    return rows1(
        [
            purchase_row(1, "가상 T1", "0.9", ["가상 X", "가상 Y", "가상 Z"]),  # g1: 대표 맞음 → hit@1·hit@3
            purchase_row(2, "가상 X", "0.9", ["가상 T2", "가상 Y"]),  # g2: 둘째(추천 1) 맞음 → hit@3 만
            purchase_row(3, "가상 X", "0.9", ["가상 Y", "가상 Z", "가상 T4"]),  # g4: 넷째(추천 3) → hit@3 아님
            purchase_row(4, "가상 X", "0.9", []),  # g5: activity_only — 분모 밖
            purchase_row(1, "가상 Y", "0.9", ["가상 Z", "가상 T3"], sheet="C2"),  # g3: 셋째, 코드 둘 Title → hit@3
            purchase_row(5, "가상 X", "0.9", []),  # g6: 책 밖 NAICS — 분모 밖
        ]
    )


def test_score_colab_top1_top3_and_structural():
    s = score_colab(_gold(), _colab_result(), ORDER, TITLE_CODES)
    assert s["hit1"] == {"hit": 1, "n": 4}
    assert s["hit3"] == {"hit": 3, "n": 4}  # g1·g2·g3, g4(넷째) 아님
    assert s["structuralMiss"] == {"naicsOffBook": 1, "activityOnly": 1}
    assert s["multiCodeTitles"] == 1
    assert s["missingRows"] == 0


def test_score_colab_fourth_candidate_is_not_top3():
    gold = L4Gold(
        (GoldRow("g4", 1, frozenset({"epa-seef-v1.3:999994"}), frozenset({"999994"}), "naics_in_book"),),
        {"producedBy": "t", "source": "t"},
    )
    s = score_colab(gold, _colab_result(), ORDER, TITLE_CODES)
    assert s["hit3"] == {"hit": 0, "n": 1}


def test_score_colab_missing_row_counts_as_miss():
    order = [o for o in ORDER if o["row_id"] != "g1"]
    s = score_colab(_gold(), _colab_result(), order, TITLE_CODES)
    assert s["missingRows"] == 1
    assert s["hit1"] == {"hit": 0, "n": 4}


# ── ③ llm-first 채점 ───────────────────────────────────────────────────────────────────────────────────────


def _llm_first_result() -> dict:
    def group(key, rows, status, picks, category):
        d = {"status": status, "category": category}
        if status == "recommended":
            d["picks"] = [{"factorId": p, "reason": "가상", "confidence": "low"} for p in picks]
        return {"key": key, "rowIds": rows, "decision": d, "shortlists": []}

    return {
        "schemaVersion": "scope3.llm-first.result@2",
        "status": "done",
        "rows": [{"rowId": r, "groupKey": k, "calc": {}} for r, k in
                 [("g1", "k1"), ("g2", "k2"), ("g3", "k3"), ("g4", "k4"), ("g5", "k5"), ("g6", "k6")]],
        "groups": [
            group("k1", ["g1"], "recommended", ["epa-seef-v1.3:999991"], 1),  # 53 id·코드 hit@1
            group("k2", ["g2"], "recommended", ["가상-db:b", "epa-seef-v1.3:999992"], 2),  # hit@3, 분류 틀림
            group("k3", ["g3"], "recommended", ["epa-seef-v1.3:999990", "epa-seef-v1.3:999993"], 2),
            group("k4", ["g4"], "classification_failed", [], None),
            group("k5", ["g5"], "recommended", ["가상-db:a1"], 1),  # activity_only: 91 분모에서만 맞음
            group("k6", ["g6"], "recommended", ["epa-seef-v1.3:888888"], 2),  # 책 밖: 53 에서 맞고 37 밖, 분류 틀림
        ],
        "usage": {},
        "shortlist": {"method": "lexical", "description": "가상"},
    }


def test_score_llm_first_three_denominators_differ():
    s = score_llm_first(_gold(), _llm_first_result())
    assert s["all91"]["hit1"] == {"hit": 3, "n": 6}  # g1·g5·g6
    assert s["all91"]["hit3"] == {"hit": 5, "n": 6}  # g1·g2·g3·g5·g6
    assert s["naics53"]["hit1"] == {"hit": 2, "n": 5}  # g1·g6
    assert s["naics53"]["hit3"] == {"hit": 4, "n": 5}
    assert s["inBook37"]["hit1"] == {"hit": 1, "n": 4}
    assert s["inBook37"]["hit3"] == {"hit": 3, "n": 4}
    assert s["naics53CodeOnly"]["hit1"] == {"hit": 2, "n": 5}
    assert s["classification"] == {"hit": 3, "n": 6}  # g1·g3·g5 (g2·g6 틀림, g4 실패)
    assert s["decisionStatus"] == {"classification_failed": 1, "recommended": 5}


def test_score_llm_first_code_only_differs_from_id():
    # 코드는 같고 id 집합에는 없는 꼴: 정답 id 를 다른 글로 바꿔 둠
    gold = L4Gold(
        (GoldRow("g1", 1, frozenset({"epa-seef-v1.3:999991x"}), frozenset({"999991"}), "naics_in_book"),),
        {"producedBy": "t", "source": "t"},
    )
    s = score_llm_first(gold, _llm_first_result())
    assert s["naics53"]["hit1"] == {"hit": 0, "n": 1}
    assert s["naics53CodeOnly"]["hit1"] == {"hit": 1, "n": 1}
    assert s["inBook37"]["hit1"] == {"hit": 0, "n": 1}
    assert s["inBook37CodeOnly"]["hit1"] == {"hit": 1, "n": 1}


def test_book_codes_of_title_codes():
    assert book_codes_of({"가상 A": ["999991", 999992], "가상 B": []}) == {"999991", "999992"}


# ── ⑤ llm-first 는 adapt() 를 거치지 않는다 ────────────────────────────────────────────────────────────────


def test_llm_first_result_rejected_by_adapt_but_scored_here(monkeypatch):
    with pytest.raises(adapters.AdapterUnavailable):
        adapters.adapt(_llm_first_result())

    def boom(_):  # 채점이 adapt 를 부르면 빨강
        raise AssertionError("adapt() 를 불렀다")

    monkeypatch.setattr("evalrig.scope3.measure_l4.adapt", boom)
    s = score_llm_first(_gold(), _llm_first_result())
    assert s["all91"]["hit3"]["n"] == 6


# ── ④ 실비 셈과 누적 문 ────────────────────────────────────────────────────────────────────────────────────


MODELS = {
    "checkedOn": "2099-01-01",
    "models": {
        "가상-big": {"provider": "openai", "pricing": {"input": 2.0, "output": 8.0, "cacheRead": 0.5, "cacheWrite": 2.0}},
        "가상-small": {
            "provider": "openai",
            "pricing": {"input": 0.1, "output": 0.5, "cacheRead": 0.01, "cacheWrite": 0.125,
                        "longContext": {"aboveInputTokens": 1000, "input": 0.2, "output": 0.75, "cacheRead": 0.02,
                                        "cacheWrite": 0.25}},
        },
    },
}


def _rec(model, prompt, inp, out, cr=0, cost=None, **extra):
    d = {"model": model, "promptId": prompt, "inputTokens": inp, "outputTokens": out, "cacheReadTokens": cr,
         "cacheWriteTokens": 0, "costUsd": cost, "durationMs": 9.0, "resultKind": "success"}  # fmt: skip
    d.update(extra)
    return d


def test_measure_cost_two_models_hand_sum(tmp_path):
    models = tmp_path / "models.json"
    models.write_text(json.dumps(MODELS), "utf-8")
    calls = tmp_path / "calls.jsonl"
    records = [
        _rec("가상-big", "p-a", 1000, 100, cr=200, cost=0.0029),
        _rec("가상-small", "p-b", 900, 100),  # 장문 아님(900 ≤ 1000)
        _rec("가상-small", "p-b", 900, 100, cr=200, providerRetry=1, errorStatus=429, resultKind="provider_error"),
        _rec(None, "p-b", None, None, resultKind="budget_exceeded"),
    ]
    calls.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", "utf-8")
    table, meta = load_pricing(models)
    s = summarize(read_records(calls), table, meta)
    big = Decimal(1000) * Decimal("2.0") + Decimal(100) * Decimal("8.0") + Decimal(200) * Decimal("0.5")
    small1 = Decimal(900) * Decimal("0.1") + Decimal(100) * Decimal("0.5")
    small2 = Decimal(900) * Decimal("0.2") + Decimal(100) * Decimal("0.75") + Decimal(200) * Decimal("0.02")  # 1100 > 1000
    assert Decimal(s["byModel"]["가상-big"]["handUsd"]) == big / Decimal(1_000_000)
    assert Decimal(s["byModel"]["가상-small"]["handUsd"]) == (small1 + small2) / Decimal(1_000_000)
    assert Decimal(s["total"]["handUsd"]) == (big + small1 + small2) / Decimal(1_000_000)
    assert s["total"]["calls"] == 4
    assert s["total"]["retries"] == 1 and s["total"]["status429"] == 1
    assert s["total"]["noUsage"] == 1
    assert Decimal(s["total"]["ledgerUsd"]) == Decimal("0.0029")
    assert s["byPrompt"]["p-b"]["resultKinds"] == {"budget_exceeded": 1, "provider_error": 1, "success": 1}
    assert len(meta["sha256_16"]) == 16 and meta["checkedOn"] == "2099-01-01"


@pytest.mark.parametrize(
    ("spent", "kind"), [(Decimal("4.49"), "go"), (Decimal("4.50"), "go"), (Decimal("4.51"), "stop")]
)
def test_cumulative_gate_boundary(spent, kind):
    gate = cumulative_gate(spent - Decimal("0.40"), Decimal("0.40"))
    assert gate.kind == kind
    assert isinstance(gate, Go if kind == "go" else Stop)
    if isinstance(gate, Stop):
        assert gate.spent + gate.next_upper == spent


# ── C2: 갈래 섞인 행(검증 ① R2) · 표 · 분포 ───────────────────────────────────────────────────────────────


def test_branch_of_mixed_row_is_in_book():
    from evalrig.scope3.measure_l4 import branch_of

    book = frozenset({"999991"})
    assert branch_of(frozenset({"999991", "888888"}), book) == "naics_in_book"  # 하나라도 책 안
    assert branch_of(frozenset({"888888"}), book) == "naics_off_book"
    assert branch_of(frozenset(), book) == "activity_only"


def _l1_rows():
    # 지은 10줄: C6 여덟(ok) + 빈칸 하나 + C7 ok 하나. 오차(글자) 0·0·0.01·0.04·0.05·0.06·0.10·0.30
    errs = ["0", "0", "0.01", "0.04", "0.05", "0.06", "0.10", "0.30"]
    rows = [{"category": "C6", "hit1": True, "co2e": {"kind": "ok", "error": e}} for e in errs]
    rows.append({"category": "C6", "hit1": False, "co2e": {"kind": "missing", "error": None}})
    rows.append({"category": "C7", "hit1": True, "co2e": {"kind": "ok", "error": "0.5"}})
    return rows


def test_measure_table_distribution():
    from evalrig.scope3.measure_table import distribution

    d = distribution(_l1_rows())
    c6 = d["C6"]
    assert (c6["n"], c6["ok"], c6["blank"], c6["zero"], c6["hit1"]) == (9, 8, 1, 2, 8)
    assert Decimal(c6["median"]) == Decimal("0.045")  # (0.04 + 0.05) / 2
    assert Decimal(c6["p90"]) == Decimal("0.30")  # ⌈0.9×8⌉ = 8째
    assert Decimal(c6["max"]) == Decimal("0.30")
    assert c6["within5"] == 5 and c6["within20"] == 7
    assert d["C7"]["median"] == d["C7"]["p90"] == "0.5"


def test_measure_table_rows_have_five_cells(tmp_path):
    from evalrig.scope3.measure_table import CELLS, build, to_md

    (tmp_path / "l1/score").mkdir(parents=True)
    (tmp_path / "l1/score/score.json").write_text(json.dumps({"rows": _l1_rows()}), "utf-8")
    table = build(tmp_path, "가상해시")
    assert [r["what"].split()[0] for r in table["rows"]] == ["L4-a", "L4-b", "L4-c", "L3", "L1"]
    for r in table["rows"]:
        assert all(r[c] for c in CELLS) and r["reading"] == ""
    assert "L1 오차 분포" in to_md(table)


def _l3_records():
    # 지은 기록 셋: 성공 하나 · 잘림 하나(같은 프롬프트) · 끊김 timeout 하나(300초)
    def rec(ts, prompt, kind, ms):
        return {"ts": ts, "promptId": f"가상/{prompt}@1", "resultKind": kind, "durationMs": ms}

    return [
        rec("2099-01-01T00:00:00.000Z", "가상-city", "success", 900.0),
        rec("2099-01-01T00:01:00.000Z", "가상-city", "truncated", 950.0),
        rec("2099-01-01T00:10:00.500Z", "가상-other", "timeout", 300000.0),
    ]


def test_measure_table_l3_cells_outage_and_kinds():
    from evalrig.scope3.measure_table import l3_cells, run_timing

    recs = _l3_records()
    t = run_timing(recs)
    assert (t.span_s, t.outage_s, t.outage_calls, t.outage_kinds) == (600.5, 300.0, 1, {"timeout": 1})
    summ = {"seconds": 123.4, "sheetStatus": {"processed": 1}, "dumpRows": 9}
    result, invalid = l3_cells(summ, recs)
    assert result.startswith("끊김 뺀 300.5초")
    assert "결과 종류 success 1 · timeout 1 · truncated 1 / 3" in result
    assert "가상-city truncated 1/2" in result and "가상-other timeout 1/1" in result
    assert "깨어 있던 시간 123.4초(참고)" in result
    assert invalid.startswith("기계 잠듦·끊김 300.0초(timeout 1)")


def test_measure_table_l3_cells_no_outage():
    from evalrig.scope3.measure_table import l3_cells

    summ = {"seconds": 60.0, "sheetStatus": {"processed": 1}, "dumpRows": 9}
    result, invalid = l3_cells(summ, _l3_records()[:2])
    assert result.startswith("끊김 뺀 60.0초") and "끊김" not in invalid
