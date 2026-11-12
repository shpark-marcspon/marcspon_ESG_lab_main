"""랩 실험 판 검산 도구(발주서 MB2 §2-6) — 1차(colab_156) 검사 도구 셋을 집약도·표시 자릿수 허용 오차만 고친 꼴로.

근거(흠): 1차 `tool_ratio_check` 는 늘 d/e×100 이라 집약도(%가 아닌 비)를 거짓 FAIL 로 낸다(DVL1 3/5, MARC-ENT-519).
허용 오차는 단위와 무관한 절댓값 1.1 이고 모델이 인자로 0.01 등으로 바꾼다(DVL1·review2-dv). 여기서는:
- 합: 허용 오차 = 0.5 × 10^(−d) × (더하는 항 수 + 1). d = 더하는 항·목표 가운데 가장 긴 소수 자릿수. 「+1」 은 목표 값의
  표시 반올림 몫이다 — 표시된 값 n+1 개가 저마다 반 칸까지 어긋날 수 있다(가장 나쁜 경우의 합).
- 비율(%)·집약도: 허용 오차 = 0.5 × 10^(−decimals(목표)) — 목표 표시 자릿수의 반 칸. 기대값 단위에 「%」(NFKC — 「％」 도)가
  있으면 비율(d/e×100, 1차처럼 f 가 0~1 이면 ×100 정규화), 없으면 집약도(d/e, 정규화 없음).
- 모델이 준 tolerance 는 쓰지 않는다. 결과 dict 의 키 집합·실패 갈래(숫자 아님·0 나눔)의 글은 1차 도구와 같다(critic B2).
이것은 **실험 가정이지 판정 규칙이 아니다** — 마크스폰 판정 규칙 물음 MARC-ENT-41 은 열린 채 둔다.
랩 대사의 값 비교(`ts/reconcile/src/domain/match.ts` compareValues 의 표시 단위 반올림)와 같은 뜻이다.
표준 라이브러리만(esg-app import 0).
"""
from __future__ import annotations

import math
import unicodedata
from typing import Any

# 1차 도구(esg-app engines/colab_156/tools.py)가 내는 키 집합 — 갈래마다(시험이 이것과 견준다)
V1_KEYS_OK = ("ok", "status", "diff", "explanation")
V1_KEYS_NOTE = ("ok", "status", "diff", "note", "explanation")


def decimals(x: Any) -> int:
    """JSON 수의 소수 자릿수(파이썬 repr 기준). 글이면 쉼표를 뺀 글자로. 「26.00」 은 float 로 들어오면 1 로 읽힌다(한계)."""
    if isinstance(x, bool):
        return 0
    if isinstance(x, int):
        return 0
    s = x.replace(",", "").strip() if isinstance(x, str) else repr(float(x))
    if "e" in s or "E" in s:  # 지수 표기
        mant, _, exp = s.lower().partition("e")
        frac = mant.split(".")[1] if "." in mant else ""
        return max(0, len(frac) - int(exp))
    return len(s.split(".")[1]) if "." in s else 0


def rounding_tolerance(operands: list[Any], target: Any, *, mode: str) -> float:
    """mode = sum | pct | intensity."""
    if mode == "sum":
        d = max(decimals(v) for v in [*operands, target])
        return 0.5 * 10 ** (-d) * (len(operands) + 1)
    if mode in ("pct", "intensity"):
        return 0.5 * 10 ** (-decimals(target))
    raise ValueError(mode)


def is_pct(unit: Any) -> bool:
    return "%" in unicodedata.normalize("NFKC", str(unit or ""))


def _non_numeric() -> dict[str, Any]:
    return {"ok": False, "status": "FAIL", "diff": None, "note": "non-numeric", "explanation": "Input values are not numeric."}


def sum_rounded(a: Any, b: Any, c: Any) -> tuple[dict[str, Any], float | None]:
    """1차 tool_sum_check 와 같은 셈. 숫자 아님은 1차처럼 NameError(약점 1 — 분배기가 error dict 로 바꿈)."""
    try:
        aa, bb, cc = float(a), float(b), float(c)
    except (TypeError, ValueError):
        raise NameError("name 'e' is not defined")  # noqa: B904 — 1차 그대로
    tol = rounding_tolerance([a, b], c, mode="sum")
    diff = (aa + bb) - cc
    ok = abs(diff) <= tol + 1e-12
    explanation = f"Sum({aa} + {bb}) - Target({cc}) = {diff:.6f} → {'within' if ok else 'exceeds'} tolerance ({tol})."
    return {"ok": ok, "status": "OK" if ok else "FAIL", "diff": round(diff, 10), "explanation": explanation}, tol


def sum_n_rounded(addends: Any, target: Any) -> tuple[dict[str, Any], float | None]:
    try:
        nums = [float(x) for x in addends]
        tgt = float(target)
    except (TypeError, ValueError):
        return _non_numeric(), None
    tol = rounding_tolerance(list(addends), target, mode="sum")
    diff = sum(nums) - tgt
    ok = abs(diff) <= tol + 1e-12
    explanation = f"Sum({nums}) - Target({tgt}) = {diff:.6f} → {'within' if ok else 'exceeds'} tolerance ({tol})."
    return {"ok": ok, "status": "OK" if ok else "FAIL", "diff": round(diff, 10), "explanation": explanation}, tol


def ratio_or_intensity(d: Any, e: Any, f: Any, expected_unit: Any) -> tuple[dict[str, Any], float | None, str]:
    """(결과 dict, 쓴 허용 오차, mode). 1차 tool_ratio_check 와 같은 실패 갈래·키."""
    mode = "pct" if is_pct(expected_unit) else "intensity"
    try:
        dd, ee, ff = float(d), float(e), float(f)
    except (TypeError, ValueError):
        return _non_numeric(), None, mode
    if abs(ee) < 1e-15:
        return ({"ok": False, "status": "FAIL", "diff": None, "note": "division by zero",
                 "explanation": "Cannot divide by zero."}, None, mode)
    tol = rounding_tolerance([d, e], f, mode=mode)
    if mode == "pct":
        calc = dd / ee * 100.0
        if 0.0 <= ff <= 1.0:  # 1차 정규화 그대로(critic R4) — 허용 오차도 같은 눈금으로
            expected, tol = ff * 100.0, tol * 100.0
        else:
            expected = ff
        diff = calc - expected
        ok = abs(diff) <= tol + 1e-12
        explanation = (f"Ratio = {dd}/{ee} × 100 = {calc:.4f}%, Expected ≈ {expected:.4f}%, Diff = {diff:.4f} → "
                       f"{'within' if ok else 'exceeds'} tolerance ({tol} p.p.).")
    else:
        calc = dd / ee
        diff = calc - ff
        ok = abs(diff) <= tol + 1e-12
        explanation = (f"Intensity = {dd}/{ee} = {calc:.6f}, Expected ≈ {ff}, Diff = {diff:.6f} → "
                       f"{'within' if ok else 'exceeds'} tolerance ({tol}).")
    if not math.isfinite(diff):  # pragma: no cover
        return _non_numeric(), None, mode
    return {"ok": ok, "status": "OK" if ok else "FAIL", "diff": round(diff, 6), "explanation": explanation}, tol, mode
