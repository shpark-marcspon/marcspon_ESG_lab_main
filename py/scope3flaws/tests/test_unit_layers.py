"""L2 활동량·단위 — 사례 S07~S14(CASES.md). 줄·계수 줄은 전부 지은 것(synthetic), 호출 0(AI 는 가짜 공급사).

꼴은 `test_input_layers.py` 머리 그대로(`test_current_*` 고정 · `test_contract_*` 계약).
"""

import pytest
from esgapi.inventory.engines.scope3.colab_157.purchase import get_bok_usd_krw_annual_avg

from scope3colab import synthetic as syn
from scope3flaws.build import SheetCase, annual
from scope3flaws.runner import FAIL_5XX, CaseRun, run_case

C1 = syn.C1_SHEET
EF_C1 = syn.USEPA_ROWS[0][6]
AMOUNT = 9100
FAIL_ALL = [lambda _p, _v: FAIL_5XX]  # 모든 AI 부름이 5xx
# C1 에 「단위」 칸(받은 v2.2 C1 에는 없음 — 사용자가 더한 칸)을 비고 앞에 둔다
C1_UNIT_MAIN = (*syn.C1_MAIN[:-1], "단위", syn.C1_MAIN[-1])
C1_UNIT_SUB = (*syn.C1_SUB[:-1], None, syn.C1_SUB[-1])
C1_CUR_MAIN = (*syn.C1_MAIN[:-1], "통화", syn.C1_MAIN[-1])


def _c1_unit_row(unit: str | None, note: str) -> tuple:
    return ("가상국", "가상사업장A", "원재료", "가상수지 가상펠릿", None, None, AMOUNT, *([None] * 12), unit, note)


def _num(text: str | None) -> float:
    assert text is not None
    return float(text)


# ── S07 L2-1 C1 외화 줄 ──
def _s07(tmp_path, year: int = 2025) -> CaseRun:
    rows = tuple(_c1_unit_row(u, f"가상메모-{u}") for u in ("KRW", "USD", "EUR", "가상화폐", None))
    return run_case(tmp_path, [SheetCase(C1, rows, main=C1_UNIT_MAIN, sub=C1_UNIT_SUB)], report_year=year)


def test_current_s07_foreign_currency_row_scored_on_raw_amount(tmp_path):
    rows = _s07(tmp_path).row_dicts(C1)
    fx = get_bok_usd_krw_annual_avg()
    krw, usd, eur, unknown, blank = rows
    for row in rows:  # 다섯 줄의 co2e 가 다 같다 — 환산 전 「구매금액」 × 원화 계수(후처리 `fix_result_c1_c2`)
        assert _num(row["co2e"]) == pytest.approx(AMOUNT * EF_C1 / 1000)
    assert _num(usd["spendKrw"]) == pytest.approx(AMOUNT * fx)  # 화면의 원화 금액은 환산 값
    assert _num(usd["spendKrw"]) * EF_C1 / 1000 / _num(usd["co2e"]) == pytest.approx(fx)  # 그 차이 = 환율 배
    assert _num(eur["spendKrw"]) == pytest.approx(AMOUNT * 1.09 * fx)  # 셀에 박힌 EUR→USD 1.09
    assert unknown["spendNote"] == "미지원 통화(가상화폐) → KRW 간주" and unknown["missing"] == []
    assert krw["spendNote"] == blank["spendNote"] == "KRW 그대로"


@pytest.mark.xfail(strict=True, reason="계약 L5: 화면에 보이는 원화 금액 × 계수 = co2e(한 줄은 식 하나)")
def test_contract_s07_co2e_matches_shown_krw_amount(tmp_path):
    usd = _s07(tmp_path).row_dicts(C1)[1]
    assert _num(usd["co2e"]) == pytest.approx(_num(usd["spendKrw"]) * EF_C1 / 1000)


# ── S08 L2-1 곁: 「통화」 칸만 ──
def test_current_s08_currency_column_alone_is_ignored(tmp_path):
    row = _c1_unit_row("USD", "가상메모")
    r = run_case(tmp_path, [SheetCase(C1, (row,), main=C1_CUR_MAIN, sub=C1_UNIT_SUB)])
    (out,) = r.row_dicts(C1)
    assert out["spendNote"] == "KRW 그대로"  # 표준화가 「단위」=KRW 를 먼저 채워 「통화」 USD 를 읽지 않는다
    assert _num(out["spendKrw"]) == AMOUNT


# ── S09 L2-2 환율 표 연도 ──
@pytest.mark.parametrize("year", [2024, 2026])
def test_current_s09_c1_fx_is_2025_whatever_report_year(tmp_path, year):
    usd = _s07(tmp_path, year).row_dicts(C1)[1]
    assert _num(usd["spendKrw"]) == pytest.approx(AMOUNT * get_bok_usd_krw_annual_avg(2025))
    assert usd["missing"] == []


def _c4_spend(tmp_path, year: int) -> dict:
    row = annual(("가상국", "가상사업장A", "가상트럭 배송", "가상자재", None), 1_400_000, "가상메모")
    r = run_case(tmp_path, [SheetCase(syn.C4_SPEND_SHEET, (row,))], report_year=year)
    return r.row_dicts(syn.C4_SPEND_SHEET)[0]


@pytest.mark.parametrize("year", [2024, 2026])
def test_current_s09_c4_spend_uses_2025_krw_column_for_other_years(tmp_path, year):
    row = _c4_spend(tmp_path, year)
    assert "2025" in row["factor"]["column"]  # 그 해 열이 없으면 가장 뒤 연도 열(말없이)
    assert row["missing"] == [] and row["activity"]["note"] is None


@pytest.mark.xfail(strict=True, reason="계약 L2·L4: 보고연도 밖 환율·계수 열을 쓰면 줄에 드러난다")
def test_contract_s09_other_year_column_is_flagged(tmp_path):
    row = _c4_spend(tmp_path, 2026)
    assert row["missing"] != [] or row["activity"]["note"]


# ── S10 L2-3 AI 단위 배수 실패 ──
def _s10(tmp_path) -> CaseRun:
    c3 = (annual(("가상국", "가상사업장A", "전력", "가상와트"), 100),)
    c4 = (annual(("가상국", "가상사업장A", "가상트럭", "가상자재", "가상상자", None, None, 100), 10, "가상메모"),)
    return run_case(tmp_path, [SheetCase(syn.C3_SHEET, c3), SheetCase(syn.C4_DIST_SHEET, c4)], rules=FAIL_ALL)


def test_current_s10_unit_ai_failure_multiplies_unconverted(tmp_path):
    r = _s10(tmp_path)
    (c3,) = r.row_dicts(syn.C3_SHEET)
    assert c3["activity"]["baseUnit"] == "가상와트" and _num(c3["activity"]["baseValue"]) == 100  # 값 미변환
    assert _num(c3["co2e"]) == pytest.approx(100 * syn.ENERGY_C3_ROWS[0][2] / 1000)  # 그대로 kWh 계수를 곱함
    assert c3["missing"] == [] and c3["origin"] == "estimate"
    (c4,) = r.row_dicts(syn.C4_DIST_SHEET)
    assert {"step": "unit", "origin": "estimate", "via": "ai_failed"} in c4["originSteps"]
    assert _num(c4["activity"]["baseValue"]) == 10 and c4["activity"]["baseUnit"] == "kg"  # 모르는 단위 × 1 = kg
    assert c4["co2e"] is not None and c4["missing"] == []
    assert r.llm.unexpected == []


@pytest.mark.xfail(strict=True, reason="계약 L2: 모르는 단위는 멈춤 상태로 둔다(그대로 곱하지 않는다)")
def test_contract_s10_unknown_unit_halts(tmp_path):
    (c3,) = _s10(tmp_path).row_dicts(syn.C3_SHEET)
    assert c3["co2e"] is None and c3["missing"] != []


# ── S11 L2-4 같은 에너지 줄, 시트만 다름 ──
def _s11(tmp_path) -> CaseRun:
    return run_case(
        tmp_path,
        [
            SheetCase(syn.C3_SHEET, (annual(("가상국", "가상사업장A", "전력", "가상와트"), 100),)),
            SheetCase(syn.C8_ENERGY_SHEET, (annual(("가상국", "가상임차자산A", "전력", "가상와트"), 100),)),
            SheetCase(syn.C11_SHEET, (("가상국", None, None, "가상제품A", 1, "전력", 100, "가상와트", 12, "가상 적요"),)),
        ],
        rules=FAIL_ALL,
    )


def test_current_s11_same_line_multiplied_in_c3_c8_held_in_c11(tmp_path):
    r = _s11(tmp_path)
    (c3,) = r.row_dicts(syn.C3_SHEET)
    (c8,) = r.row_dicts(syn.C8_ENERGY_SHEET)
    (c11,) = r.row_dicts(syn.C11_SHEET)
    assert c3["co2e"] is not None and c8["co2e"] is not None  # 곱함
    assert c11["co2e"] is None and c11["activity"]["note"] == "[단위 불일치로 산정 보류: 가상와트→kWh]"  # 보류
    assert c11["missing"] == []  # 보류도 missing 에는 안 남는다


@pytest.mark.xfail(strict=True, reason="계약 L2: 같은 에너지 사슬의 같은 상황은 시트와 상관없이 같은 처리")
def test_contract_s11_same_treatment_across_sheets(tmp_path):
    r = _s11(tmp_path)
    held = [r.row_dicts(s)[0]["co2e"] is None for s in (syn.C3_SHEET, syn.C8_ENERGY_SHEET, syn.C11_SHEET)]
    assert len(set(held)) == 1


# ── S12 L2-5 빈 칸 기본값 ──
def _s12(tmp_path) -> CaseRun:
    c6 = (annual(("가상국", "가상본부", "가상버스", None, None, None, 50), None, "가상메모"),)
    c7 = (annual(("가상국", "가상사업장A", "가상승용차", None, None, None, 10), None, "가상메모"),)
    c4 = (annual(("가상국", "가상사업장A", "가상트럭", "가상자재", None, None, None, 100), 1000, "가상메모"),)
    c11 = (("가상국", None, None, "가상제품B", 10, "전력", 100, "kWh", "가상수명", "가상 적요"),)
    c12 = (("가상국", "92 | 가상 사업장", "92-01 | 가상 수지", "92-01-01 | 가상 폐수지 묶음", "가상용기", 100, 1, "lb",
            9, 1, 90, None, "가상 적요"),)  # fmt: skip
    return run_case(
        tmp_path,
        [
            SheetCase(syn.C4_DIST_SHEET, c4),
            SheetCase(syn.C6_DIST_SHEET, c6),
            SheetCase(syn.C7_SHEET, c7),
            SheetCase(syn.C11_SHEET, c11),
            SheetCase(syn.C12_SHEET, c12),
        ],
    )


def test_current_s12_blank_cells_get_silent_defaults(tmp_path):
    r = _s12(tmp_path)
    c6 = r.row_dicts(syn.C6_DIST_SHEET)[1]  # 0 번은 v2.2 매핑 줄(build.MAPPING_FILLER)
    assert (c6["distance"]["people"], c6["distance"]["trips"]) == ("1.0", "1.0") and c6["missing"] == []
    c7 = r.row_dicts(syn.C7_SHEET)[1]
    assert (c7["distance"]["people"], c7["distance"]["workdays"]) == ("1.0", "245.0") and c7["missing"] == []
    (c4,) = r.row_dicts(syn.C4_DIST_SHEET)
    assert c4["activity"]["unit"] == "kg" and _num(c4["activity"]["baseValue"]) == 1000 and c4["missing"] == []
    (c11,) = r.row_dicts(syn.C11_SHEET)
    assert c11["product"]["years"] == "1.0" and c11["missing"] == []  # 수명 글 「가상수명」 → 1년
    (c12,) = r.row_dicts(syn.C12_SHEET)
    assert c12["activity"]["unit"] == "lb" and c12["activity"]["baseUnit"] == "kg"
    assert _num(c12["activity"]["baseValue"]) == 100  # 100 × 1 lb 를 100 kg 로(g·t 밖은 kg)
    assert c12["missing"] == []


@pytest.mark.xfail(strict=True, reason="계약 L2: 기본값을 넣었으면 줄에 「기본값: 무엇」")
def test_contract_s12_defaults_are_marked(tmp_path):
    c7 = _s12(tmp_path).row_dicts(syn.C7_SHEET)[1]
    assert c7["missing"] != []


# ── S13 L2-6 C12 처리 비중 ──
def _s13(tmp_path) -> CaseRun:
    code = ("가상국", "92 | 가상 사업장", "92-01 | 가상 수지", "92-01-01 | 가상 폐수지 묶음")
    rows = ((*code, "가상용기A", 100, 1, "kg", 1, 9, 90, None, "가상 적요"),  # 재활용 「1」(1% 뜻)
            (*code, "가상용기B", 100, 1, "kg", 10, 9, 90, None, "가상 적요"))  # 합 109%  # fmt: skip
    return run_case(tmp_path, [SheetCase(syn.C12_SHEET, rows)])


def test_current_s13_share_one_is_hundred_percent_and_sum_unchecked(tmp_path):
    one, over = _s13(tmp_path).row_dicts(syn.C12_SHEET)
    parts = {p["treatment"]: p["share"] for p in one["treatments"]["parts"]}
    assert parts["재활용"] == "1.0"  # 1 이하는 비율로 읽어 100%
    assert one["treatments"]["shareTotal"] == "1.99" and one["missing"] == []
    assert over["treatments"]["shareTotal"] == "1.09" and over["co2e"] is not None and over["missing"] == []


@pytest.mark.xfail(strict=True, reason="계약 L6: 비중 합이 100% 가 아니면 드러난다")
def test_contract_s13_share_sum_checked(tmp_path):
    _, over = _s13(tmp_path).row_dicts(syn.C12_SHEET)
    assert over["missing"] != [] or over["co2e"] is None


# ── S14 L2-7 C8-2 면적 배분 ──
def test_current_s14_blank_used_area_fails_whole_sheet(tmp_path):
    rows = (annual(("가상국", "가상임차자산A", 1000, 100, "전력", "kWh"), 9000),
            annual(("가상국", "가상임차자산B", 1000, None, "전력", "kWh"), 9000))  # fmt: skip
    r = run_case(tmp_path, [SheetCase(syn.C8_AREA_SHEET, rows)])
    o = r.outcome(syn.C8_AREA_SHEET)
    assert o.status == "failed" and o.error.startswith("TypeError")  # 멀쩡한 첫 줄도 같이 빠진다
    assert r.result["status"] == "done"


def test_current_s14_ratio_over_one_accepted(tmp_path):
    rows = (annual(("가상국", "가상임차자산A", 1000, 1500, "전력", "kWh"), 9000),)
    (row,) = run_case(tmp_path, [SheetCase(syn.C8_AREA_SHEET, rows)]).row_dicts(syn.C8_AREA_SHEET)
    assert row["allocation"]["areaRatio"] == "1.5" and _num(row["activity"]["value"]) == 13500
    assert row["missing"] == []


@pytest.mark.xfail(strict=True, reason="계약 L2·L6: 빈 칸 하나는 그 줄만 실패시킨다(시트는 processed)")
def test_contract_s14_one_blank_fails_one_row(tmp_path):
    rows = (annual(("가상국", "가상임차자산A", 1000, 100, "전력", "kWh"), 9000),
            annual(("가상국", "가상임차자산B", 1000, None, "전력", "kWh"), 9000))  # fmt: skip
    assert run_case(tmp_path, [SheetCase(syn.C8_AREA_SHEET, rows)]).status(syn.C8_AREA_SHEET) == "processed"


@pytest.mark.xfail(strict=True, reason="계약 L6: 값 범위 검사 — 배분 비율 > 1 은 드러난다")
def test_contract_s14_ratio_over_one_flagged(tmp_path):
    rows = (annual(("가상국", "가상임차자산A", 1000, 1500, "전력", "kWh"), 9000),)
    (row,) = run_case(tmp_path, [SheetCase(syn.C8_AREA_SHEET, rows)]).row_dicts(syn.C8_AREA_SHEET)
    assert row["missing"] != []
