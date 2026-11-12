"""MC1 — 산출 → 잴 것마다 한 줄 표와 L1 배출량 오차 분포.

입력(뿌리 `--root`, 보통 `esg-lab/out/MC1/` — 이름이 정해진 자리):
- L4: `l4/score-{a,b,c}.json`(measure_l4) · `l4/{a,b,c}/cost.json`(measure_cost)
- L3: `l3/live/summary.json`(드라이버 — 벽시계·시트 상태) · `l3/live/cost.json` · `l3/live/calls.jsonl`(지연 p50·p95)
- L1: `l1/score/score.json`(evalrig.scope3 score) · `l1/live/cost.json` — 없으면 그 줄은 「안 돎」.
출력 `table.json`·`table.md`: 줄마다 다섯 칸(무엇을 · 1차 판 · 결과 수(분모) · 실제 USD · 무효 조건)과 「2차 엔진에 주는 읽기」 빈 칸.

분포(시트별, `score.json` 의 `rows` 가운데 `co2e.kind == "ok"` 줄의 상대 오차 — 분모는 정답): 중앙값(짝수면 가운데 둘의
평균) · p90(가장 가까운 순위: 오름차순 ⌈0.9n⌉ 째) · 최대 · 5% 안(≤ 0.05) · 20% 안(≤ 0.20) · 빈칸(`missing`) · 오차 0. 셈은 `Decimal`.
"""

import argparse
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from evalrig.scope3.unit import ToolFailure

TABLE_SCHEMA = "evalrig.scope3.mc1-table/1"
CELLS = ("what", "run", "result", "usd", "invalid")
FIVE = Decimal("0.05")
TWENTY = Decimal("0.20")


def median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def p90(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    s = sorted(values)
    return s[math.ceil(Decimal("0.9") * len(s)) - 1]


def distribution(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_sheet: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_sheet[str(r.get("category"))].append(r)
    out: dict[str, dict[str, Any]] = {}
    for sheet, rs in sorted(by_sheet.items()):
        errs = [Decimal(str(r["co2e"]["error"])) for r in rs if r.get("co2e", {}).get("kind") == "ok"]
        m, p, mx = median(errs), p90(errs), (max(errs) if errs else None)
        out[sheet] = {
            "n": len(rs),
            "ok": len(errs),
            "median": None if m is None else str(m),
            "p90": None if p is None else str(p),
            "max": None if mx is None else str(mx),
            "within5": sum(1 for e in errs if e <= FIVE),
            "within20": sum(1 for e in errs if e <= TWENTY),
            "zero": sum(1 for e in errs if e == 0),
            "blank": sum(1 for r in rs if r.get("co2e", {}).get("kind") == "missing"),
            "hit1": sum(1 for r in rs if r.get("hit1") is True),
        }
    return out


def percentile_ms(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[math.ceil(q * len(s)) - 1]


def latency_by_prompt(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by: dict[str, list[float]] = defaultdict(list)
    for r in records:
        if r.get("durationMs") is not None:
            by[str(r["promptId"])].append(float(r["durationMs"]))
    return {k: {"n": len(v), "p50Ms": percentile_ms(v, 0.5), "p95Ms": percentile_ms(v, 0.95)} for k, v in sorted(by.items())}


OUTAGE_MS = 60_000.0  # 이보다 긴 호출 하나 = 끊김(잠듦·네트워크)으로 본다. L3 호출의 정상 지연은 수 초(S3L2 p95 ≈ 1.8초)


@dataclass(frozen=True)
class RunTiming:
    span_s: float  # 기록 ts 첫~끝
    outage_s: float  # OUTAGE_MS 넘는 호출들의 지연 합
    outage_calls: int
    outage_kinds: dict[str, int]

    @property
    def net_s(self) -> float:
        return self.span_s - self.outage_s


def run_timing(records: list[dict[str, Any]]) -> RunTiming:
    ts = sorted(datetime.fromisoformat(str(r["ts"]).replace("Z", "+00:00")) for r in records)
    long_calls = [r for r in records if float(r.get("durationMs") or 0) > OUTAGE_MS]
    kinds: dict[str, int] = defaultdict(int)
    for r in long_calls:
        kinds[str(r["resultKind"])] += 1
    span = (ts[-1] - ts[0]).total_seconds() if ts else 0.0
    return RunTiming(span, sum(float(r["durationMs"]) for r in long_calls) / 1000, len(long_calls), dict(sorted(kinds.items())))


def kinds_text(records: list[dict[str, Any]]) -> str:
    """결과 종류 합 `success n · truncated n / 부름` 과 성공 아닌 프롬프트별 `<프롬프트> <종류> n/<그 프롬프트 부름>`."""
    total: dict[str, int] = defaultdict(int)
    per_prompt: dict[str, int] = defaultdict(int)
    bad: dict[tuple[str, str], int] = defaultdict(int)
    for r in records:
        prompt, kind = str(r["promptId"]).split("/")[-1].split("@")[0], str(r["resultKind"])
        total[kind] += 1
        per_prompt[prompt] += 1
        if kind != "success":
            bad[(prompt, kind)] += 1
    head = " · ".join(f"{k} {v}" for k, v in sorted(total.items(), key=lambda kv: (kv[0] != "success", kv[0])))
    tail = " · ".join(f"{p} {k} {n}/{per_prompt[p]}" for (p, k), n in sorted(bad.items()))
    return f"결과 종류 {head} / {len(records)}" + (f" · {tail}" if tail else "")


def l3_cells(summ: dict[str, Any], records: list[dict[str, Any]]) -> tuple[str, str]:
    """L3 줄의 결과 칸과 무효 조건 칸. 단조 시계는 맥이 잠들면 멈춰 참고로만 내린다(MC1 검증 ② B2)."""
    invalid = ["지은 판(가상)", "지오코더 없음", "같은 키로 다른 밴드가 같이 돎(속도 제한 공유)", "한 번 잼"]
    if not records:
        return f"기록 없음 · 시트 {summ['sheetStatus']} · 덤프 {summ['dumpRows']}줄", " · ".join(invalid)
    t = run_timing(records)
    result = (f"끊김 뺀 {t.net_s:.1f}초(기록 첫~끝 {t.span_s:.1f} − 끊김 {t.outage_s:.1f}) · {kinds_text(records)} · "
              f"시트 {summ['sheetStatus']} · 덤프 {summ['dumpRows']}줄 · 깨어 있던 시간 {summ['seconds']}초(참고)")  # fmt: skip
    if t.outage_calls:
        kinds = " · ".join(f"{k} {n}" for k, n in t.outage_kinds.items())
        invalid.insert(0, f"기계 잠듦·끊김 {t.outage_s:.1f}초({kinds}) — 단조 시계는 잠듦 동안 멈춤")
    return result, " · ".join(invalid)


def _load(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except ValueError as error:
        raise ToolFailure(f"{path.name} JSON 아님") from error


def _usd(cost: Any) -> str:
    if cost is None:
        return "—"
    t = cost["total"]
    return (f"{t['ledgerUsd']} USD (부름 {t['calls']} · 입력 {t['inputTokens']} · 캐시 쓰기 {t['cacheWriteTokens']} · "
            f"출력 {t['outputTokens']} · 재시도 {t['retries']} · 429 {t['status429']})")  # fmt: skip


def _rate(r: dict[str, Any]) -> str:
    return f"{r['hit']}/{r['n']}"


def row(what: str, run: str, result: str, usd: str, invalid: str) -> dict[str, str]:
    return {"what": what, "run": run, "result": result, "usd": usd, "invalid": invalid, "reading": ""}


def build(root: Path, engine: str) -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    label = "러너 라벨, 검토 0"
    for key, run in (("a", "colab_157 · gpt-4.1(standard-openai-41)"), ("b", "colab_157 · gpt-6-luna(light-openai)")):
        s = _load(root / f"l4/score-{key}.json")
        if s is None:
            rows.append(row(f"L4-{key}", run, "안 돎", "—", "—"))
            continue
        miss = s["structuralMiss"]
        rows.append(row(
            f"L4-{key} C1·C2 계수 고르기", f"esg-app {engine} · {run} · 148행 중 91",
            f"hit@1 {_rate(s['hit1'])} · hit@3 {_rate(s['hit3'])} (책 안 NAICS, {label}) · 구조상 못 맞춤 책 밖 "
            f"{miss['naicsOffBook']} · 활동 {miss['activityOnly']}",
            _usd(_load(root / f"l4/{key}/cost.json")),
            "정답이 러너 라벨(검토 0) · 품목군 비움 · 지오코더 없음 · 분류는 시트가 정함(n/a)",
        ))  # fmt: skip
    s = _load(root / "l4/score-c.json")
    if s is None:
        rows.append(row("L4-c", "llm_first_1 · gpt-6-luna", "안 돎", "—", "—"))
    else:
        rows.append(row(
            "L4-c C1·C2 계수 고르기·분류", f"esg-app {engine} · llm_first_1 · gpt-6-luna(light-openai) · 추린 수 25 · 기억 비움",
            f"91행 hit@1 {_rate(s['all91']['hit1'])} · hit@3 {_rate(s['all91']['hit3'])} · 책 안 37 hit@3 "
            f"{_rate(s['inBook37']['hit3'])} · 37 코드만 hit@3 {_rate(s['inBook37CodeOnly']['hit3'])} · 분류 "
            f"{_rate(s['classification'])} ({label})",
            _usd(_load(root / "l4/c/cost.json")),
            "정답이 러너 라벨(검토 0)이고 같은 계수 목록 id 체계 · 148행 전체를 넣음(91 밖 57행도 부름에 듦)",
        ))  # fmt: skip
    summ = _load(root / "l3/live/summary.json")
    calls = root / "l3/live/calls.jsonl"
    records = [json.loads(x) for x in calls.read_text("utf-8").splitlines() if x.strip()] if calls.exists() else []
    if summ is None:
        rows.append(row("L3", "colab_157 · gpt-4.1", "안 돎", "—", "—"))
    else:
        result, invalid = l3_cells(summ, records)
        rows.append(row(
            "L3 2,000행 한 번 시간·비용", f"esg-app {engine} · colab_157 · gpt-4.1 · S3R1 big-x31 1,984행(가상)",
            result, _usd(_load(root / "l3/live/cost.json")), invalid,
        ))  # fmt: skip
    l1 = _load(root / "l1/score/score.json")
    dist = None
    if l1 is None:
        rows.append(row("L1 받은 입력 배출량 오차", "colab_157 · gpt-4.1", "안 돎(사용자 허락 없음 — L1 의 문)", "0", "—"))
    else:
        dist = distribution(l1["rows"])
        rows.append(row(
            "L1 받은 입력 배출량 오차", f"esg-app {engine} · colab_157 · gpt-4.1",
            " · ".join(f"{k} 오차 0 {v['zero']}/{v['n']} · 빈칸 {v['blank']}" for k, v in dist.items()),
            _usd(_load(root / "l1/live/cost.json")),
            "정답 = 받은 예시 결과(코랩) · 지오코더 없음",
        ))  # fmt: skip
    latency = latency_by_prompt(records) if records else None
    return {"schema": TABLE_SCHEMA, "engine": engine, "rows": rows, "l1Distribution": dist, "l3Latency": latency}


def to_md(table: dict[str, Any]) -> str:
    out = ["# MC1 잴 것 표", "", "| 무엇을 | 1차 판 | 결과 수(분모) | 실제 USD | 무효 조건 | 2차 엔진에 주는 읽기 |",
           "|---|---|---|---|---|---|"]  # fmt: skip
    for r in table["rows"]:
        out.append("| " + " | ".join(r[c] for c in (*CELLS, "reading")) + " |")
    if table["l1Distribution"]:
        out += ["", "## L1 오차 분포(시트별)", "", "| 시트 | n | ok | 중앙값 | p90 | 최대 | 5% 안 | 20% 안 | 오차 0 | 빈칸 | hit@1 |",
                "|---|---|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
        for k, v in table["l1Distribution"].items():
            out.append(f"| {k} | {v['n']} | {v['ok']} | {v['median']} | {v['p90']} | {v['max']} | {v['within5']} | "
                       f"{v['within20']} | {v['zero']} | {v['blank']} | {v['hit1']} |")  # fmt: skip
    if table["l3Latency"]:
        out += ["", "## L3 프롬프트별 지연", "", "| 프롬프트 | 부름 | p50 ms | p95 ms |", "|---|---|---|---|"]
        for k, v in table["l3Latency"].items():
            out.append(f"| {k} | {v['n']} | {v['p50Ms']} | {v['p95Ms']} |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evalrig.scope3.measure_table")
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--engine", required=True, help="esg-app 고정 해시")
    args = ap.parse_args(argv)
    try:
        table = build(args.root, args.engine)
    except (ToolFailure, KeyError, TypeError) as error:
        print(f"도구 실패: {error}", file=sys.stderr)
        return 2
    (args.root / "table.json").write_text(json.dumps(table, ensure_ascii=False, indent=2) + "\n", "utf-8")
    (args.root / "table.md").write_text(to_md(table), "utf-8")
    print(to_md(table), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
