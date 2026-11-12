"""TRX9 ② 검사 규칙 시험 — 지어낸 격자, 네트워크 0, 정답 0."""

from raslab import ras2_check as C


def tbl(grid):
    return {"items": [], "tables": [{"bbox": [0, 0, 1, 1], "grid": grid}]}


def test_num_parsing():
    assert C.num("286.284") == (286.284, 3, 3)
    assert C.num("5,100,570") == (5100570.0, None, 7)
    assert C.num(" 12.5% ") == (12.5, 1, 2)
    assert C.num("△1,234") == (1234.0, None, 4)
    assert C.num("Scope 1") is None
    assert C.num("") is None
    assert C.is_year("2024") and not C.is_year("2,024") and not C.is_year("2024.0")


def test_r1_mixed_decimal_flags_and_single_half_does_not():
    mixed = [["구분", "값"], ["a", "286.284"], ["b", "23.176"], ["c", "5100570"], ["d", "12"]]
    assert C.r1(mixed) == ["c1"]
    one_half = [["h", "v"], ["a", "1"], ["b", "2"], ["c", "0.5"]]
    assert C.r1(one_half) == []
    all_dec = [["h", "2022"], ["a", "1.5"], ["b", "2.5"], ["c", "3.5"]]  # 머리 연도는 숫자 칸에서 뺀다
    assert C.r1(all_dec) == []
    diff_places = [["h", "v"], ["a", "1.5"], ["b", "2.25"], ["c", "3"]]
    assert C.r1(diff_places) == []


def test_r2_digit_jump():
    assert C.r2([["h", "v"], ["a", "286284"], ["b", "23"], ["c", "40"]]) == ["c1"]
    assert C.r2([["h", "v"], ["a", "286.284"], ["b", "23"]]) == []  # 소수점 있는 칸은 안 봄
    assert C.r2([["h", "v"], ["a", "2024"], ["b", "5"]]) == []  # 연도는 뺌
    assert C.r2([["h", "v"], ["a", "1,234"], ["b", "", ], ["c", "999"]]) == []


def test_r3_total_row_and_col():
    ok = [["구분", "2022", "2023"], ["Scope 1", "286.284", "300.1"], ["Scope 2", "23.176", "25.0"], ["합계", "309.460", "325.1"]]
    assert C.r3(ok) == []
    within = [["구분", "v"], ["a", "100"], ["b", "100"], ["Total", "200.9"]]  # 0.45% 안
    assert C.r3(within) == []
    bad = [["구분", "v"], ["a", "100"], ["b", "100"], ["총 배출량", "210"]]
    assert C.r3(bad) == ["r3:c1"]
    col = [["구분", "a", "b", "합계"], ["x", "1", "2", "3"], ["y", "1", "2", "4"]]
    assert C.r3(col) == ["c3:r2"]
    not_total = [["구분", "v"], ["회계", "1"], ["설계", "2"], ["계", "9"]]  # 「계」 칸 전체만 합계
    assert C.r3(not_total) == ["r3:c1"]
    assert C.r3([["구분", "v"], ["a", "1"], ["합계", "1"]]) == []  # 숫자 칸 < 2 면 안 봄


def test_r4_second_reading():
    a = tbl([["h", "v"], ["a", "286.284"], ["b", "23.176"]])
    b = tbl([["h", "v"], ["a", "286,284"], ["b", "23.176"]])
    assert C.r4(a, b) == ["t0:r1:c1"]
    c = tbl([["h", "v"], ["a", "286.284"]])
    assert C.r4(a, c) == ["shape"]
    assert C.r4(a, {"items": [], "tables": []}) == ["shape"]
    assert C.r4({"items": [], "tables": []}, {"items": [], "tables": []}) == []
    assert C.r4(a, tbl([["h", "v"], ["a ", "286.284"], ["b", "23.176"]])) == []  # 글 칸 차이는 안 봄


def test_r5_unit_prior():
    big = tbl([["구분", "v"], ["a", "5,100,570"]])
    assert C.r5(big) == []  # 단위 없음
    big["items"] = ["단위 tCO2-eq"]
    assert C.r5(big) == ["unit"]
    small = tbl([["구분", "v"], ["a", "5,100.570"]])
    small["items"] = ["톤"]
    assert C.r5(small) == []
    no_tbl = {"items": ["배출량 1,200,000 톤"], "tables": []}
    assert C.r5(no_tbl) == ["unit"]


def test_uniform_x1000_grid_caught_only_by_r4_r5():
    """RAS1 의 1000배 오독 꼴: 16칸 전부 균일하게 소수점 → 쉼표. 합이 맞아 R1·R3 은 못 잡고 R4(둘째 읽기)·R5(단위) 만 잡는다."""
    good = tbl([["구분", "2022", "2023"], ["Scope 1", "286.284", "300.100"], ["Scope 2", "23.176", "25.000"], ["Scope 3", "5,100.570", "5,000.000"], ["합계", "5,410.030", "5,325.100"]])
    bad = tbl([["구분", "2022", "2023"], ["Scope 1", "286,284", "300,100"], ["Scope 2", "23,176", "25,000"], ["Scope 3", "5,100,570", "5,000,000"], ["합계", "5,410,030", "5,325,100"]])
    good["items"] = bad["items"] = ["단위: tCO₂eq"]
    assert C.r1(bad["tables"][0]["grid"]) == [] and C.r3(bad["tables"][0]["grid"]) == [] and C.r2(bad["tables"][0]["grid"]) == []
    alone = C.check(bad)
    assert C.rules_hit(alone["flags"]) == ["R5"]
    both = C.check(bad, good)
    assert C.rules_hit(both["flags"]) == ["R4", "R5"]
    assert C.check(good)["status"] == "pass"


def test_check_status_and_no_table():
    assert C.check({"items": ["글만"], "tables": []}) == {"status": "no_table", "flags": [], "sol": False}
    assert C.check({"items": [], "tables": []}, {"items": [], "tables": []})["status"] == "no_table"
    r = C.check({"items": [], "tables": []}, tbl([["h", "v"], ["a", "1"]]))
    assert r["status"] == "flag" and r["flags"] == ["R4:shape"] and r["sol"]
    assert C.check(tbl([["h", "v"], ["a", "1"], ["b", "2"]]))["status"] == "pass"


def test_decimal_hint_frozen_text():
    assert "소수점" in C.DECIMAL_HINT and "286.284" in C.DECIMAL_HINT and C.DECIMAL_HINT.startswith("\n- ")
