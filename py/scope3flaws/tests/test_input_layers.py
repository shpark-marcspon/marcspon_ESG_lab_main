"""L0 입력 양식 · L1 카테고리 가름 — 사례 S01~S06(CASES.md). 줄·이름은 전부 지은 것(synthetic), 호출 0.

`test_current_*` = 지금 동작 고정(통과해야 함). `test_contract_*` = 층 문서 2-1 「처음부터 짠다면」의 계약(지금 실패가 맞으면
`xfail(strict=True)` — 새 흐름이 계약을 지키면 XPASS 로 빨개져 이 표지를 떼라는 신호가 된다).
"""

import pytest

from scope3colab import synthetic as syn
from scope3flaws.build import KEEP_ANNUAL, SheetCase, annual, monthly
from scope3flaws.runner import CaseRun, run_case

C1 = syn.C1_SHEET
ITEM = ("가상사업장A", "원재료", "가상수지 가상펠릿", None, None)  # 사업장 · 품목군 · 상세 품목명 · 계정과목 · 거래처명
EF_C1 = syn.USEPA_ROWS[0][6]  # 「가상수지」 키워드가 고르는 지은 원화 계수


def c1(rows) -> list[SheetCase]:
    return [SheetCase(C1, tuple(rows))]


# ── S01 L0-1 시트 이름 앞 4글자 ──
def _s01(tmp_path) -> CaseRun:
    row = annual(("가상국", "가상임대자산A", 1000, 100, "전력", "kWh"), 9000, "가상메모")
    return run_case(tmp_path, [SheetCase(syn.C13_AREA_SHEET, (row,), name="C13-2 면적")])


def test_current_s01_space_name_goes_to_c13_energy_processor(tmp_path):
    r = _s01(tmp_path)
    o = r.outcome("C13-2 면적")
    assert o.status == "processed"
    assert o.processor.key == "c13_energy"  # 면적 시트인데 에너지 처리기(앞 4글자 「c13-」 이 C13-1 키에 먼저 걸림)
    (row,) = r.row_dicts("C13-2 면적")
    assert row["kind"] == "leased_out_asset" and row["allocation"]["method"] == "에너지사용량"  # 면적 배분을 안 함


@pytest.mark.xfail(strict=True, reason="계약 L0: 이름 추측 매칭은 하지 않는다 — 모르는 이름은 not_target")
def test_contract_s01_no_name_guessing(tmp_path):
    assert _s01(tmp_path).status("C13-2 면적") == "not_target"


# ── S02 L0-2 안내 잔재 거름(첫 칸 80자·안내 낱말) ──
def _s02(tmp_path) -> CaseRun:
    long_item = "가상" * 45  # 90자 품목명(첫 칸이 아님)
    return run_case(
        tmp_path,
        c1(
            [
                annual(("가상국", *ITEM), 9100, "가상메모1"),
                annual(("※ 가상 안내 낱말로 시작하는 국가 칸", *ITEM), 9100, "가상메모2"),
                annual(("가상국" * 30, *ITEM), 9100, "가상메모3"),  # 첫 칸 90자
                annual(("가상국", "가상사업장A", "원재료", long_item, None, None), 9100, "가상메모4"),
                annual(("1) 가상국", *ITEM), 9100, "가상메모5"),
            ]
        ),
    )


def test_current_s02_guide_rows_dropped_by_first_column_only(tmp_path):
    r = _s02(tmp_path)
    f = r.frame(C1)
    assert list(f["비고"]) == ["가상메모1", "가상메모4"]  # 셋은 결과에 없다(까닭 칸 0)
    assert len(r.row_dicts(C1)) == 2
    assert len(f.loc[f["비고"] == "가상메모4", "품목명"].iloc[0]) == 90  # 긴 품목명 줄은 남는다 — 첫 칸(국가)만 본다


@pytest.mark.xfail(strict=True, reason="계약 L0: 버린 줄도 까닭과 함께 결과에 싣는다")
def test_contract_s02_dropped_rows_are_reported(tmp_path):
    assert len(_s02(tmp_path).row_dicts(C1)) == 5


# ── S03 L0-3 연간·달 칸 ──
def _s03(tmp_path) -> CaseRun:
    return run_case(
        tmp_path,
        c1(
            [
                monthly(("가상국", *ITEM), {1: 100, 2: 100}, "가상메모-일부달"),
                monthly(("가상국", *ITEM), {1: 100}, f"가상메모-둘다 {KEEP_ANNUAL}", annual_value=5000),
                monthly(("가상국", *ITEM), {1: 100, 2: -300}, "가상메모-음수달"),
                annual(("가상국", *ITEM), -500, "가상메모-음수연간"),
            ]
        ),
    )


def _by_note(r: CaseRun, part: str) -> dict:
    f = r.frame(C1)
    i = next(i for i, v in enumerate(f["비고"]) if part in str(v))
    return {"amount": f["구매금액"].iloc[i], "row": r.row_dicts(C1)[i]}


def test_current_s03_months_win_and_nonpositive_month_sum_is_empty(tmp_path):
    r = _s03(tmp_path)
    assert _by_note(r, "일부달")["amount"] == 200  # 두 달 합 그대로 — 1년 값으로 본다(늘리지도 표시도 않음)
    both = _by_note(r, "둘다")
    assert both["amount"] == 100  # 연간 5,000 은 버리고 달 합
    assert both["row"]["missing"] == []
    neg = _by_note(r, "음수달")["row"]
    assert neg["co2e"] is None and neg["missing"] == ["amount"]  # 달 합 -200 → 빈 값
    annual_neg = _by_note(r, "음수연간")["row"]
    assert annual_neg["co2e"] == repr(-500 * EF_C1 / 1000)  # 연간 칸의 음수는 남는다(층 문서 「음수 사라짐」은 달 합만)


@pytest.mark.xfail(strict=True, reason="계약 L0·L2: 연간과 달 합이 어긋나면 줄에 드러난다(기본값·버림은 「무엇」과 함께)")
def test_contract_s03_annual_month_conflict_is_flagged(tmp_path):
    assert _by_note(_s03(tmp_path), "둘다")["row"]["missing"] != []


# ── S04 L0-4 예시 줄 ──
def _s04(tmp_path) -> CaseRun:
    return run_case(
        tmp_path,
        c1(
            [
                annual(("(예시) 가상국", *ITEM), 9100, "예시 줄 — 지우고 쓰세요(가상)"),
                annual(("가상국", *ITEM), 9200, "가상메모"),
            ]
        ),
    )


def test_current_s04_example_row_is_counted(tmp_path):
    r = _s04(tmp_path)
    rows = r.row_dicts(C1)
    assert len(rows) == 2 and all(row["co2e"] is not None for row in rows)
    assert r.category("C1").total is not None and float(r.category("C1").total) == pytest.approx((9100 + 9200) * EF_C1 / 1000)


@pytest.mark.xfail(strict=True, reason="계약 L0: 예시 줄은 표지와 함께 셈 밖에 둔다")
def test_contract_s04_example_row_excluded(tmp_path):
    assert len(_s04(tmp_path).row_dicts(C1)) == 1


# ── S05 L0-5 머리 줄만 ──
def test_current_s05_header_only_sheet_is_failed(tmp_path):
    r = run_case(tmp_path, c1([]))
    o = r.outcome(C1)
    assert o.status == "failed" and o.error.startswith("ValueError")
    assert r.result["status"] == "done"


@pytest.mark.xfail(strict=True, reason="계약 L0: 데이터 줄이 없는 시트는 empty")
def test_contract_s05_header_only_sheet_is_empty(tmp_path):
    assert run_case(tmp_path, c1([])).status(C1) == "empty"


# ── S06 L1-2 C10 처리기 없음 ──
def _s06(tmp_path) -> CaseRun:
    row = annual(("가상국", "가상자산X", "전력", "kWh"), 9000)
    return run_case(tmp_path, [SheetCase(syn.C13_ENERGY_SHEET, (row,), name="C10_판매제품가공")])


def test_current_s06_c10_sheet_is_not_target_without_reason(tmp_path):
    sheet = _s06(tmp_path).sheet_dict("C10_판매제품가공")
    assert sheet == {"sheet": "C10_판매제품가공", "status": "not_target"}  # 카테고리·까닭 칸 없음


@pytest.mark.xfail(strict=True, reason="계약 L1: 카테고리마다 「해당 없음/뺌 + 까닭」을 칸으로 낸다(C10 포함)")
def test_contract_s06_c10_carries_category_and_reason(tmp_path):
    sheet = _s06(tmp_path).sheet_dict("C10_판매제품가공")
    assert sheet.get("category") == "Category10" and sheet.get("reason")
