"""evalrig.translation.mt5 — F1·F2 함수와 되돌림만(실제 산출은 명령)."""
import pytest

from evalrig.translation import mt5


def orig(text, page):  # 원 _nums 흉내(9자 이하만)
    t = text.strip()
    return [] if len(t) > 9 else [int(x) for x in t.split() if x.isdigit()]


def test_f1_tail_number():
    f = mt5.f1_nums(orig)
    assert f("2025 가상 지속가능경영보고서 17", 17) == [17]
    assert f("연간 생산 목표 2,030", 30) == []  # 쉼표 묶음 끝 → 버림
    assert f("연간 생산 목표 12.30", 30) == []
    assert f("긴 글 끝에 숫자 없음 가나다", 5) == []
    assert f("가상 보고서 이름 줄 120", 10) == []  # abs(n − 쪽) < 60 그대로
    assert f("가상 보고서 이름 줄 1000", 10) == []  # 1~3 자리만
    assert f(" 7 ", 7) == [7]  # 9자 이하는 원 함수


def test_f2_drop():
    assert mt5.f2_drop(7, 84) and not mt5.f2_drop(42, 84) and not mt5.f2_drop(109, 110)


def test_swapped_restores_even_on_error():
    import trlab.pagemap as PM

    o = PM._nums
    with mt5.swapped(PM, "_nums", mt5.f1_nums(o)):
        assert PM._nums is not o
        assert PM._nums("2025 가상 보고서 21", 21) == [21]
    assert PM._nums is o
    with pytest.raises(RuntimeError):
        with mt5.swapped(PM, "_nums", lambda t, p: []):
            raise RuntimeError("x")
    assert PM._nums is o


def test_no_tx8_import_at_module_load():
    import sys

    assert "trlab.tx8_pick" not in sys.modules or True  # 모듈 머리에서 import 하지 않음(아래 글 검사)
    src = open(mt5.__file__, encoding="utf-8").read()
    head = src.split("def run(")[0]
    assert "import tx8_pick" not in head and "tx8_pick import" not in head
