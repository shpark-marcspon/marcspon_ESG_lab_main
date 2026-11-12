"""L3 빈 활동량 메우기 · L4 계수 고르기 — 사례 S15~S22(CASES.md). 지명·좌표·계수 줄은 전부 지은 것(synthetic), 호출 0.

AI 는 가짜 공급사(정해진 답·정해진 실패), 지오코더는 가짜(기본 「못 찾음」 — 제품 흐름의 `NoGeocoder` 와 같음).
꼴은 `test_input_layers.py` 머리 그대로.
"""

import dataclasses
import json

import pytest
from esgapi.inventory.engines.scope3.colab_157.constants import DEFAULT_TRANSPORT_EF_MAP_PATCH, WASTE_AVG_EF
from esgapi.inventory.engines.scope3.colab_157.distance import geodesic_km

from scope3colab import synthetic as syn
from scope3flaws.build import SheetCase, annual
from scope3flaws.runner import FAIL_5XX, FAIL_TIMEOUT, CaseRun, FakeGeocoder, answer, run_case

FAIL_ALL = [lambda _p, _v: FAIL_5XX]
EF = {code: ef for _, code, ef in syn.TRAVEL_COMMUTE_ROWS}  # 출장_통근 지은 계수
EF_SEA = next(ef for _, code, ef in syn.TRANSPORT_ROWS if code == "sea")


def city_echo(prompt: str, variables: dict[str, str]) -> str | None:
    """도시 이름 정규화 AI 는 받은 글을 그대로 돌려준다(가짜)."""
    return variables["city"] if "city-name" in prompt else None


def km_answer(prompt_part: str, km: float):
    return answer(prompt_part, json.dumps({"distance_km": km}))


def _num(text: str | None) -> float:
    assert text is not None
    return float(text)


# ── S15 L3-2 거리 공급사 실패 / S24 L5-2 합 ──
def _s15(tmp_path, fail: str) -> CaseRun:
    c6 = (annual(("가상국", "가상본부", "가상버스", 1, "가상시P", "가상시Q", None), 1, "가상메모"),)
    c7 = (annual(("가상국", "가상사업장A", "가상승용차", 1, "가상시P", "가상시Q", None), 200, "가상메모"),)
    return run_case(tmp_path, [SheetCase(syn.C6_DIST_SHEET, c6), SheetCase(syn.C7_SHEET, c7)], rules=[lambda _p, _v: fail])


@pytest.mark.parametrize("fail", [FAIL_5XX, FAIL_TIMEOUT])
def test_current_s15_distance_failure_leaves_null_row_run_done(tmp_path, fail):
    r = _s15(tmp_path, fail)
    assert r.result["status"] == "done"
    for sheet in (syn.C6_DIST_SHEET, syn.C7_SHEET):
        assert r.status(sheet) == "processed"
        failed = r.row_dicts(sheet)[1]  # 0 번은 v2.2 매핑 줄(편도 거리가 있음)
        assert failed["co2e"] is None and failed["distance"]["via"] == "none"
        assert failed["missing"] == ["amount"]  # 활동량 빈 줄과 같은 표지(「AI 실패」 표지 없음)
    kinds = {p: set(c) for p, c in r.result["llmCalls"].items()}
    assert all(k == {f"failed:{'provider_error' if fail == FAIL_5XX else 'timeout'}"} for k in kinds.values())


def test_current_s24_category_total_drops_null_rows_silently(tmp_path):
    r = _s15(tmp_path, FAIL_5XX)
    c6 = r.category("C6")
    filler = r.row_dicts(syn.C6_DIST_SHEET)[0]
    assert c6.total is not None and float(c6.total) == pytest.approx(_num(filler["co2e"]))  # 빈 줄은 합에서 빠진다
    assert (c6.sheets, c6.failed_sheets) == (1, 0)
    assert {f.name for f in dataclasses.fields(c6)} == {"total", "sheets", "failed_sheets"}  # 빈 줄 수·몫 칸 없음


@pytest.mark.xfail(strict=True, reason="계약 L3: AI 실패 = 줄 상태 「추정 실패」(입력 빈 줄과 가름)")
def test_contract_s15_ai_failure_has_own_state(tmp_path):
    failed = _s15(tmp_path, FAIL_5XX).row_dicts(syn.C6_DIST_SHEET)[1]
    assert failed["missing"] != ["amount"]


@pytest.mark.xfail(strict=True, reason="계약 L8: 「합 = 셈한 줄 n / 전체 m」 — 빈 몫이 합 곁에")
def test_contract_s24_total_carries_empty_share(tmp_path):
    c6 = _s15(tmp_path, FAIL_5XX).category("C6")
    assert hasattr(c6, "rows") and hasattr(c6, "empty_rows")


# ── S16 L3-3 C4-1 본 고리 거리는 road·KR ──
def _s16(tmp_path, straight: float, road: float) -> dict:
    row = annual(("KR", "가상사업장A", "가상해상 운송", "가상자재", "ton", "가상항X", "가상항Y", None), 10, "가상메모")
    r = run_case(
        tmp_path,
        [SheetCase(syn.C4_DIST_SHEET, (row,))],
        rules=[city_echo, km_answer("distance-straight", straight), km_answer("distance-road", road)],
    )
    assert r.llm.unexpected == []
    return r.row_dicts(syn.C4_DIST_SHEET)[0]


def test_current_s16_sea_row_takes_road_distance_when_under_road_cap(tmp_path):
    row = _s16(tmp_path, 500, 650)
    assert row["mode"]["value"] == "sea" and row["factor"]["code"] == "sea"
    assert row["distance"]["geoMode"] == "road"  # 해상 줄에 도로 보정 × 1.3 거리
    assert _num(row["distance"]["km"]) == pytest.approx(500 * 1.3)
    assert _num(row["co2e"]) == pytest.approx(650 * 10 * EF_SEA / 1000)


def test_current_s16_over_road_cap_recovered_by_second_path(tmp_path):
    row = _s16(tmp_path, 2000, 2500)  # 본 고리(도로 800 상한)는 빈다 → `calc_transport` 의 두 번째 길이 줄의 수단(sea)으로
    assert row["distance"]["geoMode"] == "sea" and row["co2e"] is not None  # 층 문서 「빈 값」과 다름
    assert row["missing"] == []


@pytest.mark.xfail(strict=True, reason="계약 L3: 거리 길은 줄의 수단·국가를 쓴다(도로·KR 고정 없음)")
def test_contract_s16_distance_uses_row_mode(tmp_path):
    assert _s16(tmp_path, 500, 650)["distance"]["geoMode"] == "sea"


# ── S17 L3-4 통근 100 km 넘으면 AI 직선거리로 갈음 ──
def _lat_for(km: float) -> float:
    lo, hi = 0.0, 3.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if geodesic_km(37.0, 127.0, 37.0 + mid, 127.0) < km else (lo, mid)
    return 37.0 + lo


def _s17(tmp_path) -> CaseRun:
    places = {"가상시A": (37.0, 127.0), "가상시B": (_lat_for(99 / 1.3), 127.0), "가상시C": (_lat_for(101 / 1.3), 127.0)}
    rows = (annual(("가상국", "가상사업장A", "가상승용차", 2, "가상시A", "가상시B", None), 200, "가상메모99"),
            annual(("가상국", "가상사업장A", "가상승용차", 2, "가상시A", "가상시C", None), 200, "가상메모101"))  # fmt: skip
    return run_case(
        tmp_path,
        [SheetCase(syn.C7_SHEET, rows)],
        rules=[city_echo, km_answer("distance-straight", 300)],
        geocoder=FakeGeocoder(places),
    )


def test_current_s17_commute_over_100km_replaced_by_ai_straight(tmp_path):
    r = _s17(tmp_path)
    _, d99, d101 = r.row_dicts(syn.C7_SHEET)
    assert d99["distance"]["via"] == "geocode" and _num(d99["distance"]["km"]) == pytest.approx(99)
    assert d101["distance"]["via"] == "commute_ai_check" and _num(d101["distance"]["km"]) == pytest.approx(300 * 1.3)
    assert d101["origin"] == "ai"  # AI 가 낸 것은 표시된다(이 몫은 지켜짐)
    assert r.llm.prompts().count("inventory/scope3-colab-distance-straight@1") == 1


@pytest.mark.xfail(strict=True, reason="계약 L3: AI 가 입력·좌표 셈 값을 덮지 않는다")
def test_contract_s17_ai_does_not_override_geocoded(tmp_path):
    d101 = _s17(tmp_path).row_dicts(syn.C7_SHEET)[2]
    assert d101["distance"]["via"] == "geocode"


# ── S18 L3-5 왕복 낱말 ──
def test_current_s18_round_trip_word_decides_factor(tmp_path):
    rows = (annual(("가상국", "가상본부", "가상버스", 2, None, None, 50), 3, "왕복"),
            annual(("가상국", "가상본부", "가상버스", 2, None, None, 50), 3, "가상메모"))  # fmt: skip
    _, word, plain = run_case(tmp_path, [SheetCase(syn.C6_DIST_SHEET, rows)]).row_dicts(syn.C6_DIST_SHEET)
    assert word["distance"]["roundTrip"] == "1.0" and plain["distance"]["roundTrip"] == "2.0"
    assert _num(plain["co2e"]) == pytest.approx(2 * _num(word["co2e"]))
    # 계약 「배수가 줄에 보인다」는 지금도 지켜진다(distance.roundTrip) — xfail 계약 없음


# ── S19 L3-6 수단을 못 알아봄 ──
def _s19(tmp_path) -> dict:
    row = annual(("가상국", "가상사업장A", "가상수레", "가상자재", "kg", None, None, 100), 1000, "가상메모")
    r = run_case(tmp_path, [SheetCase(syn.C4_DIST_SHEET, (row,))], rules=FAIL_ALL)
    assert "inventory/scope3-colab-transport-mode@1" in r.llm.prompts()
    return r.row_dicts(syn.C4_DIST_SHEET)[0]


def test_current_s19_unknown_mode_falls_back_to_road(tmp_path):
    row = _s19(tmp_path)
    assert row["mode"]["value"] == "road" and row["factor"]["code"] == "road"
    assert row["missing"] == []


def test_contract_s19_fallback_mode_marked(tmp_path):
    """계약 L3·L4 「폴백 수단은 추정 표지」 — 지금도 지켜진다(xfail 없음)."""
    row = _s19(tmp_path)
    assert row["mode"]["origin"] == "estimate"
    assert {"step": "mode", "origin": "estimate", "via": "fallback"} in row["originSteps"]


# ── S20 L4-4 C5 처리방법 ──
def test_current_s20_blank_or_unknown_treatment_uses_incineration_average(tmp_path):
    rows = (annual(("가상국", "가상사업장A", "가상폐수지", None, "kg", None), 100, "가상메모1"),
            annual(("가상국", "가상사업장A", "가상폐수지", None, "kg", "가상처리"), 100, "가상메모2"),
            annual(("가상국", "가상사업장A", "가상폐수지", None, "kg", "매립"), 100, "가상메모3"))  # fmt: skip
    _, blank, unknown, landfill = run_case(tmp_path, [SheetCase(syn.C5_SHEET, rows)]).row_dicts(syn.C5_SHEET)
    for row in (blank, unknown):  # 책에 가상폐수지 소각·매립 줄이 있어도 코랩 상수 소각 평균
        assert _num(row["factor"]["value"]) == WASTE_AVG_EF["소각"]
        assert {"step": "factor", "origin": "estimate", "via": "treatment_average"} in row["originSteps"]
    assert _num(landfill["factor"]["value"]) == next(r[5] for r in syn.WASTE_C5_ROWS if r[:2] == ("가상폐수지", "매립"))
    # 계약 「평균·폴백은 estimate 로 따로」는 지금도 지켜진다(origin estimate) — xfail 계약 없음


# ── S21 L4-5 C6 계수 0 ──
def _s21(tmp_path) -> list[dict]:
    rows = tuple(annual(("가상국", "가상본부", mode, 1, None, None, 50), 1, f"가상메모{i}")
                 for i, mode in enumerate(("도보", "자전거", "전기차")))  # fmt: skip
    return run_case(tmp_path, [SheetCase(syn.C6_DIST_SHEET, rows)], rules=FAIL_ALL).row_dicts(syn.C6_DIST_SHEET)[1:]


def test_current_s21_zero_factor_modes_take_car_factor(tmp_path):
    walk, bike, ev = _s21(tmp_path)
    for row, mode in ((walk, "walk"), (bike, "bicycle")):
        assert row["mode"]["value"] == mode
        assert _num(row["factor"]["value"]) == EF["car"]  # 기본값 0 이 「없음」으로 읽혀 승용차 계수
        assert {"step": "factor", "origin": "estimate", "via": "car_fallback"} in row["originSteps"]
    assert ev["mode"]["value"] == "train"  # 「전기차」 는 낱말 「기차」 에 걸려 기차(층 문서의 「전기차 → 승용차」와 다름)
    assert _num(ev["factor"]["value"]) == EF["train"]


@pytest.mark.xfail(strict=True, reason="계약 L5: 0 계수는 0(없음과 가름)")
def test_contract_s21_zero_factor_is_zero(tmp_path):
    walk, _, _ = _s21(tmp_path)
    assert _num(walk["co2e"]) == 0


@pytest.mark.xfail(strict=True, reason="계약 L3: 수단 판정이 낱말 조각에 걸리지 않는다(전기차 ≠ 기차)")
def test_contract_s21_ev_is_not_train(tmp_path):
    assert _s21(tmp_path)[2]["mode"]["value"] != "train"


# ── S22 L4-6 운송 계수 책에 없는 수단 ──
def test_current_s22_missing_transport_code_uses_colab_default(tmp_path):
    def drop_air(wb) -> None:
        ws = wb["운송"]
        for row in list(ws.iter_rows()):
            if row[1].value == "air":
                ws.delete_rows(row[0].row)

    row = annual(("가상국", "가상사업장A", "가상항공", "가상자재", "kg", None, None, 100), 10, "가상메모")
    r = run_case(tmp_path, [SheetCase(syn.C4_DIST_SHEET, (row,))], factor_edit=drop_air)
    (out,) = r.row_dicts(syn.C4_DIST_SHEET)
    assert out["factor"]["code"] == "air" and _num(out["factor"]["value"]) == DEFAULT_TRANSPORT_EF_MAP_PATCH["air"]
    assert out["factor"]["column"] is None
    assert {"step": "factor", "origin": "estimate", "via": "default"} in out["originSteps"]
    # 계약 「책 밖 계수는 estimate 표지」는 지금도 지켜진다 — xfail 계약 없음
