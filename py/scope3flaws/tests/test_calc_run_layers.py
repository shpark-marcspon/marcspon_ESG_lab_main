"""L5 곱셈·합산 · L6 검사·예외·재현 · a2 「 / 」 — 사례 S23·S25~S29(CASES.md). 자료는 전부 지은 것(synthetic), 호출 0.

S23 은 후처리 함수만 던지게 한다(`runner.post_fails` — 실행 동안만 `pipeline` 모듈 이름 하나를 갈아 끼움, 엔진 파일 0줄).
꼴은 `test_input_layers.py` 머리 그대로.
"""

import json

import pytest

from scope3colab import synthetic as syn
from scope3flaws.build import SheetCase, annual
from scope3flaws.runner import FAIL_5XX, CaseRun, answer, post_fails, run_case

FAIL_ALL = [lambda _p, _v: FAIL_5XX]
EF_BUS = next(ef for _, code, ef in syn.TRAVEL_COMMUTE_ROWS if code == "bus")


def _num(text: str | None) -> float:
    assert text is not None
    return float(text)


# ── S23 L5-1 두 번 셈 · 후처리 실패 ──
C6_ROWS = (annual(("가상국", "가상본부", "가상버스", 2, None, None, 50), 3, "왕복"),)


def _s23(tmp_path, fail: bool) -> CaseRun:
    if not fail:
        return run_case(tmp_path, [SheetCase(syn.C6_DIST_SHEET, C6_ROWS)])
    with post_fails(["postprocess_category"]):
        return run_case(tmp_path, [SheetCase(syn.C6_DIST_SHEET, C6_ROWS)])


def test_current_s23_main_loop_and_post_use_different_formulas(tmp_path):
    ok = _s23(tmp_path / "ok", fail=False)
    broken = _s23(tmp_path / "broken", fail=True)
    word_ok = ok.row_dicts(syn.C6_DIST_SHEET)[1]
    assert _num(word_ok["co2e"]) == pytest.approx(50 * 1 * 2 * 3 * EF_BUS / 1000)  # 후처리: 왕복 낱말 → × 1
    main_loop = broken.frame(syn.C6_DIST_SHEET)["배출량"].iloc[1]
    assert main_loop == pytest.approx(50 * 2 * 2 * 3 * EF_BUS / 1000)  # 본 고리: 낱말 없이 × 2 — 식이 둘


def test_current_s23_post_failure_blanks_result_without_flag(tmp_path):
    broken = _s23(tmp_path, fail=True)
    assert broken.status(syn.C6_DIST_SHEET) == "processed" and broken.result["status"] == "done"
    assert "배출량(tCO2e)" not in broken.frame(syn.C6_DIST_SHEET).columns
    rows = broken.row_dicts(syn.C6_DIST_SHEET)
    assert all(row["co2e"] is None and row["missing"] == [] for row in rows)  # 층 문서 「본 고리 값이 남는다」와 다름
    assert broken.category("C6").total is None


@pytest.mark.xfail(strict=True, reason="계약 L5·L6: 한 줄은 한 번 셈 · 후처리 실패도 값이 같거나 줄에 드러난다")
def test_contract_s23_post_failure_is_visible_or_harmless(tmp_path):
    ok = _s23(tmp_path / "ok", fail=False).row_dicts(syn.C6_DIST_SHEET)[1]
    broken = _s23(tmp_path / "broken", fail=True).row_dicts(syn.C6_DIST_SHEET)[1]
    assert broken["co2e"] == ok["co2e"] or broken["missing"] != []


# ── S25 L5-3 0 과 없음 ──
def _s25(tmp_path) -> CaseRun:
    def zero_ef(wb) -> None:
        wb["에너지_C3"].append(["가상연료영", "kWh", 0, "synthetic 0 계수(지은 줄)"])

    c3 = (annual(("가상국", "가상사업장A", "전력", "kWh"), 0), annual(("가상국", "가상사업장A", "가상연료영", "kWh"), 100))
    c5 = (annual(("가상국", "가상사업장A", "가상폐수지", None, "kg", "매립"), 0, "가상메모"),)
    return run_case(tmp_path, [SheetCase(syn.C3_SHEET, c3), SheetCase(syn.C5_SHEET, c5)], factor_edit=zero_ef, rules=FAIL_ALL)


def test_current_s25_zero_usage_and_zero_factor_become_empty(tmp_path):
    r = _s25(tmp_path)
    zero_usage, zero_ef = r.row_dicts(syn.C3_SHEET)
    assert zero_usage["co2e"] is None and zero_usage["missing"] == ["amount"]  # 0 이 「없음」
    assert zero_ef["factor"]["value"] == "0.0" and zero_ef["co2e"] is None and zero_ef["missing"] == []  # 표지도 없음
    waste = r.row_dicts(syn.C5_SHEET)[1]
    assert waste["co2e"] is None and waste["missing"] == ["amount"]


@pytest.mark.xfail(strict=True, reason="계약 L5: 0 과 없음을 가른다(0 × 계수 = 0, 계수 0 = 0)")
def test_contract_s25_zero_is_zero(tmp_path):
    zero_usage, zero_ef = _s25(tmp_path).row_dicts(syn.C3_SHEET)
    assert zero_usage["co2e"] in ("0.0", "0") and zero_ef["co2e"] in ("0.0", "0")


# ── S26 L5-4 C15 빈 칸 ──
def test_current_s26_investment_blank_is_empty_not_zero(tmp_path):
    rows = (("가상투자처A", 10, 100, None, None), ("가상투자처B", None, 100, 200, None))
    blank_s2, blank_share = run_case(tmp_path, [SheetCase(syn.C15_SHEET, rows)]).row_dicts(syn.C15_SHEET)
    assert blank_s2["co2e"] is None and blank_s2["missing"] == ["amount", "factor"]  # 층 문서 「빈 칸은 0」과 다름
    assert blank_share["activity"]["value"] == "300.0" and blank_share["co2e"] is None
    assert blank_share["missing"] == ["factor"]
    # 계약 「빈 칸은 드러난다」는 지금도 지켜진다(missing) — 다만 C15 의 factor 는 늘 null 이라 missing 「factor」 는 늘 붙는다


# ── S27 L6-1 · L8-2 실패해도 done, 꼭대기에 실패 수 없음 ──
def _s27(tmp_path) -> CaseRun:
    area = (annual(("가상국", "가상임차자산B", 1000, None, "전력", "kWh"), 9000),)
    c6 = (annual(("가상국", "가상본부", "가상버스", 1, "가상시P", "가상시Q", None), 1, "가상메모"),)
    return run_case(
        tmp_path,
        [SheetCase(syn.C1_SHEET, ()), SheetCase(syn.C6_DIST_SHEET, c6), SheetCase(syn.C8_AREA_SHEET, area)],
        rules=FAIL_ALL,
    )


def test_current_s27_failures_do_not_reach_run_top(tmp_path):
    r = _s27(tmp_path)
    statuses = {s["sheet"]: s["status"] for s in r.result["sheets"]}
    assert statuses[syn.C1_SHEET] == statuses[syn.C8_AREA_SHEET] == "failed"
    assert r.result["status"] == "done"
    assert set(r.result) == {"schemaVersion", "status", "reportYear", "factorBook", "factorSheets", "sheets", "llmCalls"}


@pytest.mark.xfail(strict=True, reason="계약 L6: 실행 상태 = 줄 상태 집계, 실패 줄·시트 수가 실행 꼭대기에")
def test_contract_s27_failure_counts_on_top(tmp_path):
    r = _s27(tmp_path)
    assert r.result["status"] != "done" or "failedSheets" in r.result


# ── S28 L6-3 재현 ──
def _s28(tmp_path, straight: float) -> CaseRun:
    def city(prompt: str, variables: dict[str, str]) -> str | None:
        return variables["city"] if "city-name" in prompt else None

    row = annual(("가상국", "가상사업장A", "가상트럭", "가상자재", "kg", "가상시가", "가상시나", None), 1000, "가상메모")
    rules = [city, answer("distance-straight", json.dumps({"distance_km": straight}))]
    return run_case(tmp_path, [SheetCase(syn.C4_DIST_SHEET, (row,))], rules=rules)


def test_current_s28_ai_cache_lives_in_one_run(tmp_path):
    first, second = _s28(tmp_path / "1", 300), _s28(tmp_path / "2", 400)  # 같은 입력, 가짜 AI 만 다른 답
    assert first.llm.prompts() == second.llm.prompts() and len(first.llm.prompts()) == 3  # 다시 부른다
    assert first.row_dicts(syn.C4_DIST_SHEET)[0]["co2e"] != second.row_dicts(syn.C4_DIST_SHEET)[0]["co2e"]
    assert set(first.result["factorBook"]) == {"sheet", "column", "year", "reload", "reloadProblem"}  # 책 판·지문·모델 없음


@pytest.mark.xfail(strict=True, reason="계약 L6: 같은 입력 → 같은 값(실행을 넘는 기억) · 실행 기록에 계수 책 판·지문")
def test_contract_s28_reproducible_and_versioned(tmp_path):
    first, second = _s28(tmp_path / "1", 300), _s28(tmp_path / "2", 400)
    assert first.row_dicts(syn.C4_DIST_SHEET)[0]["co2e"] == second.row_dicts(syn.C4_DIST_SHEET)[0]["co2e"]
    assert {"version", "sha256"} & set(first.result["factorBook"])


# ── S29 a2: 출장 Spend 수동 표 Title 의 「 / 」 조각 견줌(엔진 고유 — 코랩은 글자 그대로 같음) ──
def test_current_s29_manual_title_matches_slash_piece(tmp_path):
    combined = "Scheduled Passenger Air Transportation / Synthetic Combined Row"

    def add_combined(wb) -> None:
        wb["출장_Spend"].append(["가상 합본", "900299", combined, "가상 합본", "가상합본", 0.9, "synthetic 합본 줄(지은 값)", 0.000999])

    row = annual(("가상국", "가상본부", "항공권 구매", 1), 1_000_000)
    r = run_case(tmp_path, [SheetCase(syn.C6_SPEND_SHEET, (row,))], factor_edit=add_combined, rules=FAIL_ALL)
    (out,) = r.row_dicts(syn.C6_SPEND_SHEET)
    assert out["factor"]["title"] == combined and out["factor"]["code"] == "900299"
    assert {"step": "factor", "origin": "rule", "via": "manual"} in out["originSteps"]  # 「 / 」 조각 하나가 수동 표 Title 과 같아 고름
