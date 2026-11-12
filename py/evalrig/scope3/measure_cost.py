"""MC1 — 호출 기록(esg-app jsonl 싱크) → 실비(USD) 셈과 누적 문(멈춤 규칙 2).

- 입력: 드라이버가 `LlmSettings(log_sink="jsonl")` 로 쓴 `calls.jsonl`(줄 꼴 = esg-app `core/llm/log.py` `CallRecord.to_json()`:
  `model`·`promptId`·`inputTokens`·`outputTokens`·`cacheReadTokens`·`cacheWriteTokens`·`costUsd`·`durationMs`·`resultKind`·
  있을 때만 `providerRetry`·`errorStatus`) + 단가 표 `models.json`(esg-app `core/llm/data/`).
- 손셈은 esg-app `cost.compute_cost_usd` 와 같은 규칙: `inputTokens` 는 캐시 몫을 뺀 입력이고, 입력 합(입력 + 캐시 읽기 + 캐시
  쓰기)이 `longContext.aboveInputTokens` 를 넘으면 요청 전체에 장문 단가. 셈은 `Decimal`(반올림 없음). 기록의 `costUsd`(장부)도
  더해 둘 다 싣고 차이를 적는다 — 실비 장부는 `costUsd` 합이고, 손셈은 그것을 맞대 보는 수다.
- 토큰이 없는 줄(시간 초과·연결 실패 등)은 손셈 0, 그 수를 `noUsage` 로 센다.
- 이 모듈은 esg-app 을 import 하지 않는다(칸 이름은 `CallRecord.to_json()` 에서 옮겨 적음).

    python -m evalrig.scope3.measure_cost --calls <calls.jsonl> --models <models.json> --out <cost.json>
"""

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from evalrig.scope3.unit import ToolFailure

COST_SCHEMA = "evalrig.scope3.mc1-cost/1"
STOP_AT = Decimal("4.50")
_PER_MTOK = Decimal(1_000_000)


@dataclass(frozen=True)
class Rates:
    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write: Decimal


@dataclass(frozen=True)
class Pricing:
    base: Rates
    long_above: int | None
    long: Rates | None


def _dec(value: Any) -> Decimal:
    if value is None:
        return Decimal(0)
    return Decimal(str(value))


def _rates(p: dict[str, Any]) -> Rates:
    return Rates(_dec(p["input"]), _dec(p["output"]), _dec(p.get("cacheRead")), _dec(p.get("cacheWrite")))


def load_pricing(models_path: Path) -> tuple[dict[str, Pricing], dict[str, str]]:
    raw = models_path.read_bytes()
    try:
        data = json.loads(raw)
        table: dict[str, Pricing] = {}
        for name, model in data["models"].items():
            p = model.get("pricing")
            if not p or p.get("input") is None or p.get("output") is None:
                continue
            long = p.get("longContext")
            table[name] = Pricing(
                _rates(p),
                None if long is None else int(long["aboveInputTokens"]),
                None if long is None else _rates(long),
            )
    except (ValueError, KeyError, TypeError) as error:
        raise ToolFailure("단가 표 꼴 어긋남") from error
    meta = {"sha256_16": hashlib.sha256(raw).hexdigest()[:16], "checkedOn": str(data.get("checkedOn"))}
    return table, meta


def hand_cost(record: dict[str, Any], pricing: Pricing) -> Decimal:
    inp = int(record.get("inputTokens") or 0)
    out = int(record.get("outputTokens") or 0)
    cr = int(record.get("cacheReadTokens") or 0)
    cw = int(record.get("cacheWriteTokens") or 0)
    rates = pricing.base
    if pricing.long is not None and pricing.long_above is not None and inp + cr + cw > pricing.long_above:
        rates = pricing.long
    micro = inp * rates.input + out * rates.output + cr * rates.cache_read + cw * rates.cache_write
    return micro / _PER_MTOK


def read_records(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        lines = path.read_text("utf-8").splitlines()
    except OSError as error:
        raise ToolFailure(f"호출 기록을 못 읽었다 — {type(error).__name__}") from error
    for n, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except ValueError as error:
            raise ToolFailure(f"호출 기록 {n}째 줄 JSON 아님") from error
        if not isinstance(d, dict) or "promptId" not in d or "resultKind" not in d:
            raise ToolFailure(f"호출 기록 {n}째 줄에 promptId·resultKind 가 없다")
        out.append(d)
    return out


def _bucket() -> dict[str, Any]:
    return {
        "calls": 0, "inputTokens": 0, "cacheReadTokens": 0, "cacheWriteTokens": 0, "outputTokens": 0,
        "handUsd": Decimal(0), "ledgerUsd": Decimal(0), "noUsage": 0, "retries": 0, "status429": 0,
        "resultKinds": Counter(),
    }  # fmt: skip


def _add(b: dict[str, Any], r: dict[str, Any], hand: Decimal) -> None:
    b["calls"] += 1
    for k in ("inputTokens", "cacheReadTokens", "cacheWriteTokens", "outputTokens"):
        b[k] += int(r.get(k) or 0)
    b["handUsd"] += hand
    b["ledgerUsd"] += _dec(r.get("costUsd"))
    b["noUsage"] += int(r.get("inputTokens") is None and r.get("outputTokens") is None)
    b["retries"] += int(r.get("providerRetry") is not None)
    b["status429"] += int(r.get("errorStatus") == 429)
    b["resultKinds"][str(r["resultKind"])] += 1


def _out(b: dict[str, Any]) -> dict[str, Any]:
    o = dict(b)
    o["handUsd"] = str(b["handUsd"])
    o["ledgerUsd"] = str(b["ledgerUsd"])
    o["diffUsd"] = str(b["ledgerUsd"] - b["handUsd"])
    o["resultKinds"] = dict(sorted(b["resultKinds"].items()))
    return o


def summarize(records: list[dict[str, Any]], table: dict[str, Pricing], meta: dict[str, str]) -> dict[str, Any]:
    by_prompt: dict[str, dict[str, Any]] = defaultdict(_bucket)
    by_model: dict[str, dict[str, Any]] = defaultdict(_bucket)
    total = _bucket()
    for r in records:
        model = r.get("model")
        if model is None:
            hand = Decimal(0)  # 공급사에 닿기 전에 막힌 줄(상한 등) — 토큰 없음
        elif model not in table:
            raise ToolFailure(f"단가 표에 없는 모델 {model!r}")
        else:
            hand = hand_cost(r, table[model])
        for b in (by_prompt[str(r["promptId"])], by_model[str(model)], total):
            _add(b, r, hand)
    return {
        "schema": COST_SCHEMA,
        "pricing": meta,
        "byPrompt": {k: _out(v) for k, v in sorted(by_prompt.items())},
        "byModel": {k: _out(v) for k, v in sorted(by_model.items())},
        "total": _out(total),
    }


# ── 멈춤 규칙 2 ────────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Go:
    spent: Decimal
    next_upper: Decimal
    kind: Literal["go"] = "go"


@dataclass(frozen=True)
class Stop:
    spent: Decimal
    next_upper: Decimal
    reason: str
    kind: Literal["stop"] = "stop"


def cumulative_gate(spent: Decimal, next_upper: Decimal, stop_at: Decimal = STOP_AT) -> Go | Stop:
    """「실제 누적 + 다음 것의 위끝」이 `stop_at` 을 **넘으면** 멈춤(같으면 감)."""
    if spent + next_upper > stop_at:
        return Stop(spent, next_upper, f"누적 {spent} + 다음 위끝 {next_upper} = {spent + next_upper} > {stop_at}")
    return Go(spent, next_upper)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evalrig.scope3.measure_cost")
    ap.add_argument("--calls", type=Path, required=True)
    ap.add_argument("--models", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    try:
        table, meta = load_pricing(args.models)
        result = summarize(read_records(args.calls), table, meta)
    except ToolFailure as error:
        print(f"도구 실패: {error}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8")
    t = result["total"]
    print(f"calls={t['calls']} ledgerUsd={t['ledgerUsd']} handUsd={t['handUsd']} retries={t['retries']} 429={t['status429']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
