"""짝짓기와 비율 — 모든 모듈이 같은 셈을 쓴다.

- `pair(gold, pred, *, key, sim, threshold, group, order)` — 열쇠 같음을 먼저(1:1), 남은 것은 `sim`
  1:1 탐욕(`order="score"` 점수 내림차순 · `"gold"` 정답 차례). `group` 이면 같은 묶음 안에서만. 결정적.
- `ratio(num, den)` — 분모를 늘 같이. 분모 0 이면 `{"status": "no_denominator"}`(0 나눔 없음).
  `unmeasurable(why)` — 셀 수 없는 칸(partial 정답의 정밀 등). 둘 다 `is_ratio` 가 받는 ratio 꼴.
- `prf(tp, n_gold, n_pred)` · `macro(per_group)` · `boot_ci(values, seed=552, n=2000)`(numpy 없이).
- `text_sim(a, b)` — stdlib `difflib.SequenceMatcher` 비율(공통은 의존 그룹 0 — rapidfuzz 쓰지 않음).
"""
from __future__ import annotations

import difflib
import random
import re
import unicodedata
from typing import Any, Callable, Hashable, Sequence

BOOT_SEED = 552
BOOT_N = 2000


def norm_text(s: str) -> str:
    """NFC + 공백 접기 + 앞뒤 공백 걷기."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s or "")).strip()


def text_sim(a: str, b: str) -> float:
    """정규화한 두 글의 difflib 비율(0~1). 둘 다 비면 1.0."""
    a, b = norm_text(a), norm_text(b)
    if not a and not b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _sim_out(v: Any) -> tuple[float, str]:
    if isinstance(v, tuple):
        return float(v[0]), str(v[1])
    return float(v), "fuzzy"


def pair(
    gold: Sequence[Any],
    pred: Sequence[Any],
    *,
    key: Callable[[Any], Hashable] | None = None,
    sim: Callable[[Any, Any], Any] | None = None,
    threshold: float = 0.0,
    group: Callable[[Any], Hashable] | None = None,
    order: str = "score",
) -> dict[str, list]:
    """정답·후보 1:1 짝짓기.

    - `key(x)` 가 주어지면 열쇠 같음을 먼저 짝짓는다(정답 차례, 같은 열쇠 후보는 차례대로 하나씩) — how `exact`.
    - 남은 것은 `sim(g, p)` 로: `float` 또는 `(float, label)` 을 돌려준다(label 이 how — 예
      `exact|contain|fuzzy`; float 만이면 how `fuzzy`). score ≥ threshold 인 것만.
    - `group(x)` 가 주어지면 같은 묶음 안에서만 짝짓는다(열쇠 짝도).
    - `order="score"`: 전체 후보 쌍을 score 내림차순 탐욕, 같은 점수는 (정답 열쇠, 정답 차례, 후보 열쇠,
      후보 차례) 오름차순. `order="gold"`: 정답 차례로, 정답마다 남은 후보 중 가장 높은 것(같은 점수는
      후보 열쇠·차례 오름차순). 둘 다 결정적.

    돌려줌 `{pairs: [{gold: i, pred: j, score, how}], missed: [i…], extra: [j…]}`(i·j 는 목록 차례,
    pairs 는 정답 차례로 정렬). 열쇠 짝은 score 1.0.
    """
    if order not in ("score", "gold"):
        raise ValueError("order 는 'score' 또는 'gold'")
    grp = group if group is not None else (lambda x: None)
    pairs: list[dict[str, Any]] = []
    used_g: set[int] = set()
    used_p: set[int] = set()
    if key is not None:
        pool: dict[Hashable, list[int]] = {}
        for j, p in enumerate(pred):
            pool.setdefault((grp(p), key(p)), []).append(j)
        for i, g in enumerate(gold):
            js = pool.get((grp(g), key(g)))
            if js:
                j = js.pop(0)
                pairs.append({"gold": i, "pred": j, "score": 1.0, "how": "exact"})
                used_g.add(i)
                used_p.add(j)
    if sim is not None:
        gk = (lambda i: str(key(gold[i]))) if key is not None else (lambda i: "")
        pk = (lambda j: str(key(pred[j]))) if key is not None else (lambda j: "")
        rest_p = [j for j in range(len(pred)) if j not in used_p]
        cand = []
        for i in range(len(gold)):
            if i in used_g:
                continue
            gi = grp(gold[i])
            for j in rest_p:
                if grp(pred[j]) != gi:
                    continue
                s, how = _sim_out(sim(gold[i], pred[j]))
                if s >= threshold:
                    cand.append((i, j, s, how))
        if order == "score":
            cand.sort(key=lambda c: (-c[2], gk(c[0]), c[0], pk(c[1]), c[1]))
        else:
            cand.sort(key=lambda c: (c[0], -c[2], pk(c[1]), c[1]))
        for i, j, s, how in cand:
            if i in used_g or j in used_p:
                continue
            pairs.append({"gold": i, "pred": j, "score": s, "how": how})
            used_g.add(i)
            used_p.add(j)
    pairs.sort(key=lambda d: (d["gold"], d["pred"]))
    return {
        "pairs": pairs,
        "missed": [i for i in range(len(gold)) if i not in used_g],
        "extra": [j for j in range(len(pred)) if j not in used_p],
    }


def count_how(result: dict[str, list]) -> dict[str, int]:
    """`pair` 결과의 how 별 짝 수(방식별 셈)."""
    out: dict[str, int] = {}
    for d in result["pairs"]:
        out[d["how"]] = out.get(d["how"], 0) + 1
    return dict(sorted(out.items()))


def ratio(num: int | float, den: int | float) -> dict[str, Any]:
    """`{"value": num/den, "num", "den"}` · 분모 0 이면 `{"status": "no_denominator", "num", "den": 0}`."""
    if den == 0:
        return {"status": "no_denominator", "num": num, "den": 0}
    return {"value": num / den, "num": num, "den": den}


def unmeasurable(why: str, num: int | float = 0, den: int | float = 0) -> dict[str, Any]:
    """셀 수 없는 칸(예: coverage=partial 이라 정밀 거부) — `{"status": "unmeasurable", "why", "num", "den"}`."""
    return {"status": "unmeasurable", "why": why, "num": num, "den": den}


def is_ratio(x: Any) -> bool:
    return (
        isinstance(x, dict)
        and "num" in x
        and "den" in x
        and ("value" in x or x.get("status") in ("no_denominator", "unmeasurable"))
    )


def prf(tp: int, n_gold: int, n_pred: int) -> dict[str, Any]:
    """재현 = tp/정답 수 · 정밀 = tp/후보 수 · F1(둘 다 숫자일 때만, 아니면 no_denominator)."""
    r = ratio(tp, n_gold)
    p = ratio(tp, n_pred)
    if "value" in r and "value" in p and (r["value"] + p["value"]) > 0:
        f1: dict[str, Any] = {"value": 2 * r["value"] * p["value"] / (r["value"] + p["value"]), "num": tp, "den": None}
    elif "value" in r and "value" in p:
        f1 = {"value": 0.0, "num": 0, "den": None}
    else:
        f1 = {"status": "no_denominator", "num": tp, "den": 0}
    return {"recall": r, "precision": p, "f1": f1, "tp": tp, "n_gold": n_gold, "n_pred": n_pred}


def _value(x: Any) -> float | None:
    if is_ratio(x):
        return x.get("value")
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return float(x)
    return None


def macro(per_group: dict[str, Any]) -> dict[str, Any]:
    """묶음별 값(숫자 또는 ratio)의 단순 평균. 값이 없는 묶음(no_denominator·unmeasurable)은 빼고 `skipped` 에 센다.

    돌려줌 `{"value", "num"(묶음 수), "den"(묶음 수), "skipped"}` — 셀 묶음이 0 이면 no_denominator 꼴.
    """
    vals = [v for v in (_value(x) for x in per_group.values()) if v is not None]
    skipped = len(per_group) - len(vals)
    if not vals:
        return {"status": "no_denominator", "num": 0, "den": 0, "skipped": skipped}
    return {"value": sum(vals) / len(vals), "num": len(vals), "den": len(vals), "skipped": skipped}


def _quantile(sorted_vals: list[float], q: float) -> float:
    """선형 보간 분위(numpy 기본 'linear' 와 같은 꼴)."""
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def boot_ci(values: Sequence[float], seed: int = BOOT_SEED, n: int = BOOT_N, alpha: float = 0.05) -> dict[str, Any]:
    """평균의 백분위 부트스트랩 구간(`random.Random(seed)` — 결정적).

    돌려줌 `{"mean", "lo", "hi", "n"(값 수), "B", "seed"}` · 값 0 개면 no_denominator 꼴.
    """
    vals = [float(v) for v in values]
    if not vals:
        return {"status": "no_denominator", "num": 0, "den": 0, "B": n, "seed": seed}
    rng = random.Random(seed)
    k = len(vals)
    means = sorted(sum(vals[rng.randrange(k)] for _ in range(k)) / k for _ in range(n))
    return {
        "mean": sum(vals) / k,
        "lo": _quantile(means, alpha / 2),
        "hi": _quantile(means, 1 - alpha / 2),
        "n": k,
        "B": n,
        "seed": seed,
    }
