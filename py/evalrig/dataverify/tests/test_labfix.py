"""MB2 C3 — 랩 실험 판 검산 도구 손셈(발주서 MB2 §2-6). 값은 지은 가상 기업 꼴."""
from __future__ import annotations

import pytest

from evalrig.dataverify import labfix as L


def test_decimals():
    assert [L.decimals(x) for x in (26.0, 2.06, 12000, "1,234.50", 1e-05, True)] == [1, 2, 0, 2, 5, 0]


def test_sum_tolerance_and_verdicts():
    assert L.rounding_tolerance([12000, 4200], 16200, mode="sum") == pytest.approx(1.5)  # 0.5 × 1 × (2+1)
    ok, tol = L.sum_rounded(12000, 4200, 16200)
    assert ok["status"] == "OK" and tol == pytest.approx(1.5) and tuple(ok) == L.V1_KEYS_OK
    bad, _ = L.sum_rounded(12500, 4200, 17201)
    assert bad["status"] == "FAIL" and bad["diff"] == -501.0
    edge, tol = L.sum_rounded(4371.0, 12000, 16370)  # 소수 한 자리 → 0.5×0.1×3 = 0.15, 차 1.0 → FAIL(1차 1.1 이면 OK)
    assert edge["status"] == "FAIL" and tol == pytest.approx(0.15)
    n_ok, _ = L.sum_n_rounded([180, 120], 300)
    assert n_ok["status"] == "OK"


def test_failure_branches_keep_v1_keys():
    with pytest.raises(NameError):
        L.sum_rounded(None, 1, 2)  # 1차 tool_sum_check 그대로(분배기가 error dict 로)
    nn, tol = L.sum_n_rounded([1, "x"], 3)
    assert tuple(nn) == L.V1_KEYS_NOTE and nn["note"] == "non-numeric" and tol is None
    z, tol, mode = L.ratio_or_intensity(1, 0, 2, "%")
    assert tuple(z) == L.V1_KEYS_NOTE and z["note"] == "division by zero" and z["explanation"] == "Cannot divide by zero."
    nz, _, _ = L.ratio_or_intensity("a", 1, 2, "%")
    assert tuple(nz) == L.V1_KEYS_NOTE and nz["explanation"] == "Input values are not numeric."


def test_ratio_pct_and_intensity():
    bad, tol, mode = L.ratio_or_intensity(108, 540, 26.0, "%")  # DVL1 P2 꼴: 20.0 대 26.0
    assert (bad["status"], mode, bad["diff"]) == ("FAIL", "pct", -6.0) and tol == pytest.approx(0.05)
    ok, _, _ = L.ratio_or_intensity(108, 540, 20.0, "％")  # NFKC
    assert ok["status"] == "OK" and tuple(ok) == L.V1_KEYS_OK
    frac, tol, _ = L.ratio_or_intensity(108, 540, 0.2, "%")  # f 0~1 → ×100(1차 정규화), 허용 오차도 ×100
    assert frac["status"] == "OK" and tol == pytest.approx(5.0)
    inten, tol, mode = L.ratio_or_intensity(17201, 8350, 2.06, "tCO2eq/억원")  # DVL1 집약도 꼴 → OK
    assert (inten["status"], mode) == ("OK", "intensity") and tol == pytest.approx(0.005)
    off, _, _ = L.ratio_or_intensity(17201, 8350, 2.2, "tCO2eq/억원")
    assert off["status"] == "FAIL"
