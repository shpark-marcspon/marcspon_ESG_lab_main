"""L2 — AI 부름 묶기 전·후 부름 수(실비 0). 드라이버가 부름마다 `{promptId, keyHash}` 한 줄을 jsonl 로 내고(글·답 0), 이 모듈이
프롬프트마다 「전 = 부름 수, 후 = 서로 다른 (promptId, keyHash) 수」와 줄어든 비율을 낸다.

`key_hash` 는 드라이버가 import 해 쓴다(같은 정의 하나): 프롬프트 변수 값의 sha256, 재시도 때만 붙는 변수(`RETRY_VARS`)는 뺀다 —
같은 물음의 재시도가 다른 열쇠로 세지면 「후」가 부푼다. 1차에 이미 캐시가 있어 「전」은 캐시 뒤의 실제 부름이다.
"""

import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from evalrig.scope3.unit import ToolFailure

CALLS_SCHEMA = "evalrig.scope3.calls/1"
RETRY_VARS = frozenset({"json_retry"})  # S3L3 ⑥ 표·esg-app prompts/scope3-colab-*: 재시도 글을 받는 변수


def key_hash(variables: dict[str, Any]) -> str:
    kept = {str(k): str(v) for k, v in sorted(variables.items()) if k not in RETRY_VARS}
    return hashlib.sha256(json.dumps(kept, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def read_calls(path: Path) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    try:
        lines = path.read_text("utf-8").splitlines()
    except OSError as error:
        raise ToolFailure(f"부름 기록을 못 읽었다 — {type(error).__name__}") from error
    for n, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            out.append((str(d["promptId"]), str(d["keyHash"])))
        except (ValueError, KeyError, TypeError) as error:
            raise ToolFailure(f"부름 기록 {n}째 줄 꼴 어긋남") from error
        if set(d) - {"promptId", "keyHash"}:
            raise ToolFailure(f"부름 기록 {n}째 줄에 promptId·keyHash 밖 칸(글·답은 싣지 않는다)")
    return out


def summarize(calls: list[tuple[str, str]]) -> dict[str, Any]:
    before = Counter(p for p, _ in calls)
    keys: dict[str, set[str]] = defaultdict(set)
    for p, k in calls:
        keys[p].add(k)

    def row(b: int, a: int) -> dict[str, Any]:
        return {"before": b, "after": a, "reduction": None if b == 0 else str((Decimal(b - a) / Decimal(b)).quantize(Decimal("0.0001")))}

    prompts = {p: row(before[p], len(keys[p])) for p in sorted(before)}
    total = row(sum(before.values()), sum(len(v) for v in keys.values()))
    return {"schema": CALLS_SCHEMA, "total": total, "prompts": prompts}


def render_md(label: str, report: dict[str, Any]) -> str:
    t = report["total"]
    lines = [f"# L2 부름 수 — {label}", "", f"- 전 {t['before']} · 후 {t['after']} · 줄어듦 {t['reduction']}", "",
             "| 프롬프트 | 전 | 후 | 줄어듦 |", "|---|---|---|---|"]  # fmt: skip
    lines += [f"| {p} | {r['before']} | {r['after']} | {r['reduction']} |" for p, r in report["prompts"].items()]
    return "\n".join(lines) + "\n"
