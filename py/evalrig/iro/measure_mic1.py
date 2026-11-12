"""밴드 MIC1 — 중대성평가 1부 2차 방향 「luna + 검색 수 상한 → 검색 강제 → 출처 코드 검사 → 걸린 것만 다시 묻기」
첫 측정(M-I5′, `sid:MARC-TICK-584`).

판: Llo(luna · required · 상한 k=2) · Lmid(k=5) · Llo-b(Llo 되풀이) · LloR(Llo 의 걸린 칸만 한 번 다시, k=2).
견줌 바닥(옆 줄): MA1a A1(검색 강제 gpt-4o) · A3(luna required, 상한 없음) — `<out>/../MA1a/arms/` 실판을 읽기만.

이 모듈은 **순수 셈**이다 — 1차 엔진·공급사 SDK import 0, 네트워크 0. 실호출·1차 엔진은 git 밖 드라이버
(`esg-lab/out/MIC1/drivers/`)가 하고, 이 모듈은 그 산출 파일만 읽는다. 품질 정답 0(사람 라벨 0) — 주 지표는 코드로
세는 것만(끝난 칸 · 호출당 USD·입력 토큰 · 검색 수 · URL 루트만/화이트리스트 안/기간 안 · 걸림 수).

산출 자리(`<out>` = `esg-lab/out/MIC1`):
- `arms/<판>/` — run.json · result.json(result@1) · calls.jsonl(MA1a 칸 + maxHostedCalls·overCap) · steps.json
- `reask/LloR/` — flags-before.json(이 모듈 `flags` 가 씀) · calls.jsonl · answers.jsonl(줄 {key, items, failure}) · run.json
- `models-luna.json`(스냅숏 models.json + luna 검색 건당 요금) · `input/input.json`
- 모의: `mock/<판>/` · `mock/reask/LloR/` → `mock-summary/`

    python -m evalrig.iro.measure_mic1 flags|summary|verdict <out> [mock]     # 호출 0, 키 없는 셸
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..common import gold as G
from ..common import report as R
from ..common.match import ratio
from ..common.units import Source, Unit
from .from_v1 import convert_result, topic_key
from .measure_ma1a import (
    GOLD_PATH_REL,
    PART1,
    WHITELISTS_REL,
    load_arm,
    load_prices,
    outcome_counts,
    read_jsonl,
    round7,
    same_regulation_across_topics,
    searched,
)
from .measure_ma1a import cost as hand_cost
from .urlcheck import check_units_urls, load_whitelists, norm_url, table_row

ARMS = ("Llo", "Lmid", "Llo-b")
REASK = "LloR"
CAPS = {"Llo": 2, "Lmid": 5, "Llo-b": 2, "LloR": 2}
BASES = ("A1", "A3")
"""견줌 바닥 — MA1a 실판(다른 날·다른 토픽 수). 문에는 A1 만(B), A3 는 곁 줄."""
PHASES1 = ("regulation", "keywords", "controversy")
CELLS_PER_RUN = 17
"""1부 칸 = 규제 N + 키워드 1 + 컨트로버시 N (N=8)."""

REASONS = ("root_only", "outside_whitelist", "out_of_range", "date_unknown", "no_url")
REASON_TEXT = {
    "root_only": "URL 이 사이트 첫 화면이다(해당 문서의 URL 이 아님)",
    "outside_whitelist": "URL 이 신뢰 출처 목록 밖이다",
    "out_of_range": "날짜가 조사 기간 앞이다",
    "date_unknown": "날짜가 없거나 읽을 수 없다(기간을 확인할 수 없음)",
    "no_url": "URL 이 없다",
}
"""까닭 코드 → 되묻기 단락의 사람 말(우리 글)."""

REASK_TEMPLATE_REGULATION = (
    "[출처 다시 확인]\n"
    "앞 답의 다음 항목은 출처 검사에 걸렸다:\n"
    "{flagged}\n"
    "이 항목들만, 같은 꼴로, 다음 조건을 지키는 출처로 다시 찾아 답하라: 신뢰 출처 목록(system 에 있는 것) 안 · "
    "조사 기간 {dateRange} 안 · 사이트 첫 화면이 아닌 해당 문서의 URL. 못 찾으면 그 항목을 빼라."
)
REASK_TEMPLATE_CONTROVERSY = (
    "[출처 다시 확인]\n"
    "앞 답의 다음 항목은 출처 검사에 걸렸다:\n"
    "{flagged}\n"
    "이 항목들만, 같은 꼴로, 다음 조건을 지키는 출처로 다시 찾아 답하라: "
    "조사 기간 {dateRange} 안 · 사이트 첫 화면이 아닌 해당 문서의 URL. 못 찾으면 그 항목을 빼라."
)
"""되묻기 단락의 틀 — 갈래별 둘(고침 1 B2: 신뢰 출처 목록은 규제 system 에만 있어 컨트로버시 틀에는 그 조건이 없다).
빈칸 둘 — 항목 줄과 기간은 실행 때 드라이버가 flags-before.json·입력에서 채운다. git 에는 틀만."""
REASK_TEMPLATES = {"regulation": REASK_TEMPLATE_REGULATION, "controversy": REASK_TEMPLATE_CONTROVERSY}
REASK_ITEM_LINE = "- 항목 {n}: {title} — {urls} — 까닭: {reasons}"

NOT_RUN = "미실행(감독 판단)"
"""산출 폴더가 없는 판의 줄 글 — 돌리지 않은 판을 빠진 판(실패)과 가른다."""

TOPIC_SETS = (("T01~T05", 5), ("T01~T02", 2), ("전부", None))
"""B 의 견줌 자리 = T01~T05(A1 과 같은 토픽) · A3 곁 = T01~T02 · 전부."""

GATE_A = {"cells": CELLS_PER_RUN, "minCellsNo": 15, "unparsableMax": 1, "usdMedianYes": 0.04, "inputMedianYes": 30_000,
          "usdMedianNo": 0.09, "usdNoArms": ("Llo", "Lmid")}
GATE_B = {"worsePpYes": 10.0, "worsePpNo": 5.0, "arms": ("Llo", "Llo-b"), "base": "A1", "topics": "T01~T05",
          "minUrls": 20}
GATE_C = {"resolvedYes": 0.5, "resolvedNo": 0.2, "usdNoRatio": 1.5, "lostNo": 0.5}


# ── 걸림 ─────────────────────────────────────────────────────────────────────────────────────


def _reasons(item: dict[str, Any], checks: Sequence[dict[str, Any]], branch: str) -> list[str]:
    """항목 하나의 걸림 까닭. 규제 whitelist None(판을 못 찾음)은 outside_whitelist 로 읽지 않는다(고침 1)."""
    if not item.get("urls"):
        return ["no_url"]
    rs: set[str] = set()
    for c in checks:
        if c.get("root_only"):
            rs.add("root_only")
        if branch == "regulation" and c.get("whitelist") is False:
            rs.add("outside_whitelist")
        if c.get("date") == "out":
            rs.add("out_of_range")
        elif c.get("date") in ("date_missing", "date_unreadable"):
            rs.add("date_unknown")
    return [r for r in REASONS if r in rs]


def flag_items(checked_units: Iterable[Unit]) -> list[dict[str, Any]]:
    """URL 검사를 마친 단위(`urlcheck.check_units_urls`) → 1부 칸마다 항목별 걸림 까닭.

    꼴 [{key, group, topic, branch, status, items: [{idx, title, urls, reasons}]}] — reasons 빈 목록 = 통과.
    항목의 URL 중 하나라도 걸리면 그 항목은 걸림. 컨트로버시는 화이트리스트 까닭을 갖지 않는다(urlcheck 가 None)."""
    out = []
    for u in checked_units:
        branch = u.payload.get("branch")
        if branch not in PART1:
            continue
        checks = [s.check or {} for s in u.sources]
        pos = 0
        items = []
        for i, it in enumerate(u.payload.get("items") or []):
            n = len(it.get("urls") or [])
            mine = checks[pos:pos + n]
            pos += n
            items.append({"idx": i, "title": it.get("title") or "", "urls": list(it.get("urls") or []),
                          "reasons": _reasons(it, mine, branch), "fromReask": bool(it.get("fromReask"))})
        wl_unknown = sum(c.get("whitelist") is None for c in checks) if branch == "regulation" else 0
        out.append({"key": u.key, "group": u.group, "topic": u.payload.get("topic"), "branch": branch,
                    "status": u.status, "items": items, "whitelistUnknown": wl_unknown})
    return out


def reask_targets(flags: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """걸린 항목이 하나라도 있는 칸 — 차례: 토픽(flags 에 처음 나온 차례) → 규제 → 컨트로버시. 키워드는 단위가 없어 대상 밖."""
    order: dict[str, int] = {}
    for f in flags:
        order.setdefault(f["group"], len(order))
    hit = [f for f in flags if f["branch"] in PART1 and any(it["reasons"] for it in f["items"])]
    return sorted(hit, key=lambda f: (order[f["group"]], PART1.index(f["branch"])))


def reask_paragraph(target: dict[str, Any], date_range: str) -> str:
    """칸 하나의 되묻기 단락(걸린 항목만, 차례 그대로 · 틀은 갈래별)."""
    lines = [REASK_ITEM_LINE.format(n=it["idx"] + 1, title=it["title"] or "(제목 없음)",
                                    urls=", ".join(it["urls"]) or "(URL 없음)",
                                    reasons="; ".join(REASON_TEXT[r] for r in it["reasons"]))
             for it in target["items"] if it["reasons"]]
    return REASK_TEMPLATES[target["branch"]].format(flagged="\n".join(lines), dateRange=date_range)


# ── 합치기·셈 ────────────────────────────────────────────────────────────────────────────────


def _url_keys(it: dict[str, Any]) -> set[str]:
    return {norm_url(u) for u in it.get("urls") or []}


def merge_reask(units: Sequence[Unit], flags: Sequence[dict[str, Any]],
                answers: Mapping[str, dict[str, Any]]) -> list[Unit]:
    """되묻기 뒤 단위 목록(**새** 목록 — 원 단위는 고치지 않는다).

    answers = {칸 열쇠: {items: [단위 꼴 항목], failure: None | 갈래}} — items 는 드라이버가 Llo result 사본에 되묻기
    outcome 을 갈아 끼워 `convert_result` 로 지은 그 칸 단위의 항목(고침 1 B3, 한 길). 되물은 칸 = 통과 항목(차례 그대로)
    + 답 항목(`fromReask: True`) · `payload.reask = "reasked"`. 답 항목 가운데 같은 칸 통과 항목과 URL(norm_url)이 겹치는
    것은 다시 넣지 않고 `payload.reaskRepeatedPassed` 로 센다(고침 1 B4). failure 가 있는 칸 = `reask_failed` — 원 항목
    그대로(빈 칸으로 접지 않는다). answers 에 없는 칸은 그대로."""
    fby = {f["key"]: f for f in flags}
    out = []
    for u in units:
        a = answers.get(u.key)
        if a is None:
            out.append(u)
            continue
        p = dict(u.payload)
        if a.get("failure"):
            p["reask"] = "reask_failed"
            p["reaskFailure"] = a["failure"]
            out.append(Unit(module=u.module, unit=u.unit, key=u.key, group=u.group, payload=p,
                            sources=u.sources, status=u.status, origin=u.origin))
            continue
        f = fby.get(u.key)
        passed_idx = {it["idx"] for it in f["items"] if not it["reasons"]} if f else set()
        orig = list(u.payload.get("items") or [])
        kept = [dict(it) for i, it in enumerate(orig) if i in passed_idx]
        seen: set[str] = set().union(*(_url_keys(it) for it in kept)) if kept else set()
        new, repeated = [], 0
        for it in a.get("items") or []:
            if _url_keys(it) & seen:
                repeated += 1
                continue
            new.append({**dict(it), "fromReask": True})
        items = kept + new
        p["items"] = items
        p["reask"] = "reasked"
        p["reaskAnswerItems"] = len(new)
        p["reaskRepeatedPassed"] = repeated
        srcs = tuple(Source("web", url) for it in items for url in it.get("urls") or [])
        out.append(Unit(module=u.module, unit=u.unit, key=u.key, group=u.group, payload=p, sources=srcs,
                        status="ok" if items else "missing", origin=u.origin))
    return out


def _flagged(it: dict[str, Any], ignore: frozenset[str]) -> bool:
    return bool(set(it["reasons"]) - ignore)


def reask_tally(before_flags: Sequence[dict[str, Any]], after_units: Sequence[Unit],
                ignore: Iterable[str] = ()) -> dict[str, Any]:
    """되묻기 앞뒤 셈. after_units = `merge_reask` 뒤 단위를 `check_units_urls` 로 검사한 것.

    칸마다(되물은 칸만): B = 앞 걸림 · A = 답 항목 수(통과 항목 되풀이는 merge 에서 이미 뺌 — `repeatedPassed` 로 따로) ·
    P = 답 항목 중 통과 · 풀린 = min(B, P) · 잃은 = max(0, B − A). `reask_failed` 칸은 풀림 0 · 잃음 0(원 항목 그대로
    걸림). 풀림 = 걸린 자리를 통과한 다른 항목이 채운 수이지 같은 규제를 고쳤다는 뜻이 아니다. ignore = 걸림에서 뺄 까닭
    (곁 줄 — 화이트리스트 까닭을 뺀 눈금)."""
    ig = frozenset(ignore)
    after_units = list(after_units)
    after = {f["key"]: f for f in flag_items(after_units)}
    by_unit = {u.key: u for u in after_units}
    tot = {"cells": 0, "failedCells": 0, "flaggedBefore": 0, "answerItems": 0, "answerPassed": 0, "answerFlagged": 0,
           "repeatedPassed": 0, "lost": 0, "resolved": 0, "flaggedAfter": 0}
    why_before = {r: 0 for r in REASONS}
    why_after = {r: 0 for r in REASONS}
    for bf in before_flags:
        au = by_unit.get(bf["key"])
        state = au.payload.get("reask") if au is not None else None
        if state not in ("reasked", "reask_failed"):
            continue
        b_items = [it for it in bf["items"] if _flagged(it, ig)]
        b = len(b_items)
        tot["cells"] += 1
        tot["flaggedBefore"] += b
        for it in b_items:
            for r in set(it["reasons"]) - ig:
                why_before[r] += 1
        if state == "reask_failed":
            tot["failedCells"] += 1
            tot["flaggedAfter"] += b
            for it in b_items:
                for r in set(it["reasons"]) - ig:
                    why_after[r] += 1
            continue
        ans = [it for it in after[bf["key"]]["items"] if it["fromReask"]]
        a = len(ans)
        bad = [it for it in ans if _flagged(it, ig)]
        passed = a - len(bad)
        tot["answerItems"] += a
        tot["answerPassed"] += passed
        tot["answerFlagged"] += len(bad)
        tot["repeatedPassed"] += int(au.payload.get("reaskRepeatedPassed") or 0)
        tot["lost"] += max(0, b - a)
        tot["resolved"] += min(b, passed)
        tot["flaggedAfter"] += len(bad)
        for it in bad:
            for r in set(it["reasons"]) - ig:
                why_after[r] += 1
    return {**tot, "resolvedRate": ratio(tot["resolved"], tot["flaggedBefore"]),
            "lostRate": ratio(tot["lost"], tot["flaggedBefore"]),
            "reasonsBefore": why_before, "reasonsAfter": why_after, "ignore": sorted(ig)}


def _median(xs: Sequence[float]) -> float | None:
    return statistics.median(xs) if xs else None


def call_stats(calls: Sequence[dict[str, Any]], cap: int | None) -> dict[str, Any]:
    """호출(도구 실린 call_tools 줄 = 1부 호출)마다 USD·입력 토큰(캐시 포함)·검색 수 — 중앙·최소·최대·분포.

    검색 수 = 실행된(completed) 검색 수 `searchCompleted`(맛보기 P1~P3: 상한을 넘는 시도 하나가 status searching 으로 남고
    core hostedCalls 는 그것까지 세어 k+1 — 감독 판단 11-10). core 셈은 곁 칸 `coreHostedTotal`. 검색 수 None(칸 없음
    포함)은 셈에서 빼고 `searchUnknown` 으로 따로 센다(0 으로 접지 않는다). over_cap = completed > cap 인 호출 수
    (cap None 이거나 검색 수를 아는 호출이 0 이면 None — 0 으로 접지 않는다, 검증 1 B1)."""
    rows = [c for c in calls if c.get("entry", "call_tools") == "call_tools" and c.get("phase") in PHASES1]
    usd = [float(c["costUsd"]) for c in rows if c.get("costUsd") is not None]
    ins = [int(c.get("inputTokens") or 0) + int(c.get("cacheReadTokens") or 0) + int(c.get("cacheWriteTokens") or 0)
           for c in rows if c.get("inputTokens") is not None]
    outs = [int(c["outputTokens"]) for c in rows if c.get("outputTokens") is not None]
    hosted = [int(c["searchCompleted"]) for c in rows if c.get("searchCompleted") is not None]
    core = [int(c["hostedCalls"]) for c in rows if c.get("hostedCalls") is not None]
    ignored = [int(c["searchIgnored"]) for c in rows if c.get("searchIgnored") is not None]
    dist: dict[str, int] = {}
    for h in sorted(hosted):
        dist[str(h)] = dist.get(str(h), 0) + 1
    fails: dict[str, int] = {}
    for c in rows:
        if c.get("failure"):
            fails[c["failure"]] = fails.get(c["failure"], 0) + 1
    walls = [float(c["wallS"]) for c in rows if c.get("wallS") is not None]
    return {
        "calls": len(rows),
        "usdTotal": round7(sum(usd)) if usd else None,
        "usdMedian": _median(usd), "usdMin": min(usd) if usd else None, "usdMax": max(usd) if usd else None,
        "inputMedian": _median(ins), "inputMin": min(ins) if ins else None, "inputMax": max(ins) if ins else None,
        "outputMedian": _median(outs),
        "searchTotal": sum(hosted) if hosted else None,
        "searchMedian": _median(hosted), "searchMin": min(hosted) if hosted else None,
        "searchMax": max(hosted) if hosted else None, "searchDist": dist,
        "searchUnknown": sum(c.get("searchCompleted") is None for c in rows),
        "searchIgnoredTotal": sum(ignored) if ignored else None,
        "coreHostedTotal": sum(core) if core else None,
        "cap": cap,
        "overCap": None if cap is None or not hosted else sum(h > cap for h in hosted),
        "atCap": None if cap is None or not hosted else sum(h == cap for h in hosted),
        "atCapNoText": None if cap is None or not hosted else sum(
            c.get("searchCompleted") == cap and not c.get("textLen") for c in rows),
        "truncated": sum(c.get("failure") == "truncated" for c in rows),
        "failures": dict(sorted(fails.items())),
        "wallTotal": round(sum(walls), 2) if walls else None, "wallMedian": _median(walls),
    }


def topic_keys(received: Mapping[str, Any]) -> list[str]:
    return [topic_key(t["topic"]) for t in received.get("topics") or []]


def _done_reg(units: Iterable[Unit], keys: set[str] | None) -> list[Unit]:
    return [u for u in units if u.payload.get("branch") == "regulation" and u.status != "failed"
            and (keys is None or u.group in keys)]


def quality_rows(units: Sequence[Unit], topics: Sequence[str]) -> dict[str, dict[str, Any]]:
    """검사한 단위 → 토픽 부분집합(TOPIC_SETS)마다 규제 URL 루트만·③ 화이트리스트 안·④ 기간 안·날짜 없음 비율(분모 =
    규제 URL 수, 실패 칸 뺌)과 칸당 항목 수 중앙. topics = 입력 토픽 열쇠 차례(T01…)."""
    out = {}
    for name, n in TOPIC_SETS:
        keys = set(topics[:n]) if n is not None else set(topics)
        reg = _done_reg(units, keys)
        cs = [s.check for u in reg for s in u.sources if s.check is not None]
        wl = [c for c in cs if c.get("whitelist") is not None]
        den = len(cs)
        out[name] = {
            "cells": len(reg),
            "urls": den,
            "rootOnly": ratio(sum(bool(c.get("root_only")) for c in cs), den),
            "whitelistIn": ratio(sum(bool(c.get("whitelist")) for c in wl), len(wl)),
            "dateIn": ratio(sum(c.get("date") == "in" for c in cs), den),
            "dateUnknown": ratio(sum(c.get("date") in ("date_missing", "date_unreadable") for c in cs), den),
            "itemsPerCellMedian": _median([len(u.payload.get("items") or []) for u in reg]),
        }
    return out


# ── §0 문 ────────────────────────────────────────────────────────────────────────────────────


def _v(r: Any) -> float | None:
    return r.get("value") if isinstance(r, dict) else None


def verdict_a(arms: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    """§0-A. arms = {판: {cellsDone, cells, budgetExceeded, truncated, unparsable, usdMedian, inputMedian, overCap}}.

    예 = Llo·Lmid 모두 칸 17/17 끝남(budget_exceeded 0 · 잘림 0 · JSON 못 읽음 ≤ 1) ∧ Llo 호출당 USD 중앙 ≤ 0.04 ∧ Llo
    입력 중앙 ≤ 30k · 아니오 = 어느 상한 판이든 끝난 칸 < 15, 또는 Llo·Lmid 어느 쪽이든 USD 중앙 > 0.09(고침 1) · 못 가름 = 그 사이, 또는 어느
    호출이든 검색 수 > 상한 · 검색 수 모름(searchUnknown > 0 또는 overCap None — 검증 1 B1)(둘 다 먼저 봄), 또는 판이 빠짐."""
    g = GATE_A
    why: list[str] = []
    missing = [a for a in ("Llo", "Lmid") if a not in arms]
    if missing:
        return {"answer": "못 가름", "why": [f"판 없음: {', '.join(missing)}"]}
    unknown = {a: m.get("searchUnknown") for a, m in arms.items() if m.get("searchUnknown") or m.get("overCap") is None}
    if unknown:
        return {"answer": "못 가름", "why": [f"검색 수 모름 — 상한 지킴을 확인 못 함: {unknown}"]}
    over = {a: m.get("overCap") for a, m in arms.items() if m.get("overCap")}
    if over:
        return {"answer": "못 가름", "why": [f"검색 수 > 상한 호출: {over}"]}
    llo = arms["Llo"]
    short = [a for a, m in arms.items() if (m.get("cellsDone") or 0) < g["minCellsNo"]]
    if short:
        why.append(f"끝난 칸 < {g['minCellsNo']}: {short}")
    for a in g["usdNoArms"]:
        m = arms[a].get("usdMedian")
        if m is not None and m > g["usdMedianNo"]:
            why.append(f"{a} 호출당 USD 중앙 {m:.4f} > {g['usdMedianNo']}")
    if why:
        return {"answer": "아니오", "why": why}
    full = all(arms[a].get("cellsDone") == g["cells"] and not arms[a].get("budgetExceeded")
               and not arms[a].get("truncated") and (arms[a].get("unparsable") or 0) <= g["unparsableMax"]
               for a in ("Llo", "Lmid"))
    usd_ok = llo.get("usdMedian") is not None and llo["usdMedian"] <= g["usdMedianYes"]
    in_ok = llo.get("inputMedian") is not None and llo["inputMedian"] <= g["inputMedianYes"]
    why = [f"Llo·Lmid 17/17 끝남={full}", f"Llo USD 중앙={llo.get('usdMedian')} (≤ {g['usdMedianYes']}: {usd_ok})",
           f"Llo 입력 중앙={llo.get('inputMedian')} (≤ {g['inputMedianYes']}: {in_ok})"]
    return {"answer": "예" if (full and usd_ok and in_ok) else "못 가름", "why": why}


def _worse_pp(row: dict[str, Any], base: dict[str, Any]) -> dict[str, float | None]:
    """나쁜 쪽으로의 %p 차(양수 = 나쁨). 화이트리스트 안·기간 안은 낮을수록, 루트만은 높을수록 나쁨."""
    out: dict[str, float | None] = {}
    for k, sign in (("whitelistIn", -1), ("dateIn", -1), ("rootOnly", +1)):
        a, b = _v(row.get(k)), _v(base.get(k))
        out[k] = None if a is None or b is None else round(sign * (a - b) * 100, 4)
    return out


def _b_one(row: dict[str, Any] | None, base: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if row is None:
        return "못 가름", {"missing": True}
    if (row.get("urls") or 0) < GATE_B["minUrls"] or (base.get("urls") or 0) < GATE_B["minUrls"]:
        return "못 가름", {"분모": row.get("urls"), "바닥 분모": base.get("urls"), "minUrls": GATE_B["minUrls"]}
    w = _worse_pp(row, base)
    if any(v is None for v in w.values()):
        return "못 가름", {"worsePp": w, "no_denominator": True}
    if any(v > GATE_B["worsePpYes"] for v in w.values()):
        return "예", {"worsePp": w}
    if all(v <= GATE_B["worsePpNo"] for v in w.values()):
        return "아니오", {"worsePp": w}
    return "못 가름", {"worsePp": w}


def verdict_b(rows: Mapping[str, dict[str, Any] | None], base: dict[str, Any] | None,
              arms: Sequence[str] = GATE_B["arms"]) -> dict[str, Any]:
    """§0-B. rows = {판: quality_rows(...)[T01~T05]} · base = A1 의 같은 줄. 예(떨어짐) = arms **둘 다** 어느 지표든 10%p
    넘게 나쁨 · 아니오 = 둘 다 세 지표 모두 5%p 안 · 못 가름 = 그 사이 · 둘이 갈림 · 바닥 없음 · 판이 빠짐(Llo-b 없음
    = 「되풀이 없음」) · 규제 URL 분모 < 20(고침 1)."""
    if base is None:
        return {"answer": "못 가름", "why": ["바닥(A1) 없음"]}
    gone = [a for a in arms if rows.get(a) is None]
    if gone:
        return {"answer": "못 가름", "why": [f"판 없음: {', '.join(gone)}" + (" — 되풀이 없음" if "Llo-b" in gone else "")]}
    each = {a: _b_one(rows.get(a), base) for a in arms}
    answers = {a: e[0] for a, e in each.items()}
    why = [f"{a}: {e[0]} {e[1]}" for a, e in each.items()]
    vals = set(answers.values())
    if len(vals) == 1 and vals <= {"예", "아니오"}:
        return {"answer": vals.pop(), "why": why, "each": answers}
    if len(vals) > 1:
        why.append("판끼리 갈림")
    return {"answer": "못 가름", "why": why, "each": answers}


def verdict_c(tally: dict[str, Any], llo_usd: float | None, llor_usd: float | None) -> dict[str, Any]:
    """§0-C(고침 1 B4). 예 = 풀린 / 앞 걸림 ≥ 0.5 ∧ LloR USD ≤ Llo USD · 아니오 = 풀린 / 앞 걸림 < 0.2 · LloR USD >
    Llo × 1.5 · 잃은 > 앞 걸림의 절반 · 못 가름 = 그 사이(분모 0·USD 없음 포함). 풀린 ≤ 답 항목 수라 풀린 ≥ 절반이면
    잃은 ≤ 절반 — 예와 아니오는 함께 참일 수 없다."""
    g = GATE_C
    b = tally.get("flaggedBefore") or 0
    if b == 0 or llo_usd is None or llor_usd is None:
        return {"answer": "못 가름", "why": [f"분모·USD 없음 (앞 걸림 {b}, Llo {llo_usd}, LloR {llor_usd})"]}
    res = tally["resolved"] / b
    lost = tally["lost"] / b
    why = []
    if res < g["resolvedNo"]:
        why.append(f"풀림 {res:.3f} < {g['resolvedNo']}")
    if llor_usd > llo_usd * g["usdNoRatio"]:
        why.append(f"LloR USD {llor_usd:.4f} > Llo × {g['usdNoRatio']}")
    if lost > g["lostNo"]:
        why.append(f"잃은 항목 {lost:.3f} > {g['lostNo']}")
    if why:
        return {"answer": "아니오", "why": why}
    yes = res >= g["resolvedYes"] and llor_usd <= llo_usd
    return {"answer": "예" if yes else "못 가름",
            "why": [f"풀림 {res:.3f} (≥ {g['resolvedYes']}: {res >= g['resolvedYes']})",
                    f"LloR USD {llor_usd:.4f} ≤ Llo {llo_usd:.4f}: {llor_usd <= llo_usd}", f"잃은 {lost:.3f}"]}


# ── 판 읽기 ───────────────────────────────────────────────────────────────────────────────────


def keywords_done(result: dict[str, Any] | None) -> bool:
    return ((result or {}).get("part1") or {}).get("keywords", {}).get("status") == "done"


def arm_view(a: dict[str, Any], whitelists: dict[str, set[str]], prices: dict[str, dict[str, float]],
             cap: int | None) -> dict[str, Any]:
    """판 하나(load_arm 꼴) → 셈 꼴. 단위는 검색 수를 채우고 URL 검사까지 한 것."""
    run = a.get("run") or {}
    calls1 = [c for c in a["calls"] if c.get("phase") in PHASES1]
    us = check_units_urls([u for u in searched(a["units"], calls1) if u.payload.get("branch") in PART1], whitelists)
    o = outcome_counts(a.get("result"))
    cells_done = o["done"] + (1 if keywords_done(a.get("result")) else 0)
    st = call_stats(calls1, cap)
    hc = hand_cost(calls1, prices) if calls1 else None
    return {"arm": a["arm"], "ending": run.get("ending"), "stop": run.get("stop"), "units": us, "outcomes": o,
            "cellsDone": cells_done, "cells": o["cells"] + (1 if a.get("result") else 0), "stats": st,
            "cost": hc, "spentUsd": run.get("spentUsd"),
            "budgetExceeded": o["failedKinds"].get("budget_exceeded", 0), "url": {b: table_row(us, b) for b in PART1},
            "same": same_regulation_across_topics(us), "wallS": run.get("wallS")}


def gate_a_view(v: dict[str, Any]) -> dict[str, Any]:
    st = v["stats"]
    return {"cellsDone": v["cellsDone"], "cells": v["cells"], "budgetExceeded": v["budgetExceeded"],
            "truncated": st["truncated"], "unparsable": v["outcomes"]["unparsable"], "usdMedian": st["usdMedian"],
            "inputMedian": st["inputMedian"], "overCap": st["overCap"], "searchUnknown": st["searchUnknown"]}


def load_answers(rdir: Path) -> dict[str, dict[str, Any]]:
    """되묻기 산출 → merge_reask 의 answers. `answers.jsonl` 줄 {key, failure} · 답 항목은 `result-reask.json`(Llo result
    사본에 되물은 칸의 outcome 만 1차 해석 부품 결과로 갈아 끼운 것)을 `convert_result` 로 지은 그 칸 단위에서(고침 1 B3 —
    date_status·scope 가 Llo 와 같은 길로 생긴다)."""
    rows = read_jsonl(rdir / "answers.jsonl")
    rp = rdir / "result-reask.json"
    units = {u.key: u for u in convert_result(json.loads(rp.read_text(encoding="utf-8")), run=REASK)} if rp.exists() else {}
    out = {}
    for r in rows:
        if r.get("failure"):
            out[r["key"]] = {"items": [], "failure": r["failure"]}
        elif r["key"] not in units:
            out[r["key"]] = {"items": [], "failure": "missing_in_result"}
        else:
            out[r["key"]] = {"items": list(units[r["key"]].payload.get("items") or []), "failure": None}
    return out


def write_flags(out: str | Path, *, mock: bool = False) -> Path:
    """Llo 결과의 걸림 → `<reask>/flags-before.json`(되묻기 드라이버가 읽음). 대상 칸 목록과 날짜 범위를 함께."""
    out = Path(out)
    base = out / ("mock" if mock else "arms")
    rdir = out / ("mock/reask" if mock else "reask") / REASK
    wl = load_whitelists(out / WHITELISTS_REL)
    a = load_arm(base / "Llo", "Llo")
    calls1 = [c for c in a["calls"] if c.get("phase") in PHASES1]
    us = check_units_urls([u for u in searched(a["units"], calls1) if u.payload.get("branch") in PART1], wl)
    flags = flag_items(us)
    targets = reask_targets(flags)
    rdir.mkdir(parents=True, exist_ok=True)
    p = rdir / "flags-before.json"
    p.write_text(json.dumps({"from": "Llo", "dateRange": ((a.get("result") or {}).get("scope") or {}).get("dateRange"),
                             "flags": flags, "targets": [t["key"] for t in targets]},
                            ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return p


# ── 요약 ─────────────────────────────────────────────────────────────────────────────────────

COLS = ["표", "판", "갈래", "끝", "끝난 칸", "호출", "검색 합", "검색 분포", "검색 None", "상한", "over_cap", "상한 닿음",
        "상한 닿음·글 없음", "무시된 검색 합", "core 검색 합(k+1 셈)", "입력 중앙(캐시 포함)", "출력 중앙", "USD 중앙", "USD 최대", "URL", "루트만", "③ 화이트리스트 안 rate",
        "④ 기간 안 rate", "날짜 없음 rate", "칸당 항목 중앙", "같은 규제(제목) 열쇠", "같은 규제(URL) 열쇠", "JSON 못 읽음",
        "실패(갈래)", "잘림", "초", "USD 손셈", "USD 기록", "USD 흐름", "USD 같음",
        "되물은 칸", "실패 칸", "앞 걸림", "답 항목", "답 중 통과", "답 중 걸림", "통과 되풀이 뺌", "잃은 항목", "풀린 걸림", "풀림 rate", "까닭 앞", "까닭 뒤",
        "답", "문에 쓴 수"]


def _arm_rows(name: str, v: dict[str, Any], quality: dict[str, dict[str, Any]], label: str) -> list[dict[str, Any]]:
    st, o, c = v["stats"], v["outcomes"], v["cost"] or {}
    base = {"gold": R.NO_GOLD, "표": "판", "판": label}
    spent = v["spentUsd"]
    rows = [{**base, "갈래": "판 전체", "끝": v["ending"] if not v["stop"] else f"{v['ending']} (멈춤: {v['stop']})",
             "끝난 칸": f"{v['cellsDone']}/{v['cells']}", "호출": st["calls"], "검색 합": st["searchTotal"],
             "검색 분포": st["searchDist"], "검색 None": st["searchUnknown"], "상한": st["cap"], "over_cap": st["overCap"],
             "상한 닿음": st["atCap"], "상한 닿음·글 없음": st["atCapNoText"], "무시된 검색 합": st["searchIgnoredTotal"],
             "core 검색 합(k+1 셈)": st["coreHostedTotal"], "입력 중앙(캐시 포함)": st["inputMedian"],
             "출력 중앙": st["outputMedian"], "USD 중앙": st["usdMedian"], "USD 최대": st["usdMax"],
             "JSON 못 읽음": f"{o['unparsable']}/{o['cells']}",
             "실패(갈래)": f"{o['failed']}/{o['cells']}" + (f" {o['failedKinds']}" if o["failedKinds"] else ""),
             "잘림": st["truncated"], "초": v["wallS"], "USD 손셈": c.get("handUsd"), "USD 기록": c.get("recordedUsd"),
             "USD 흐름": None if spent is None else round7(spent),
             "USD 같음": None if not c else (c["same"] and (spent is None or round7(spent) == c["recordedUsd"]))}]
    for b in PART1:
        t = v["url"][b]
        r = {**base, "갈래": b, "URL": t["URL"], "루트만": t["루트만"], "③ 화이트리스트 안 rate": t["③ 화이트리스트 안 rate"],
             "④ 기간 안 rate": t["④ 기간 안 rate"], "날짜 없음 rate": ratio(t["날짜 없음·못 읽음"], t["URL"])}
        if b == "regulation":
            r.update({"같은 규제(제목) 열쇠": v["same"]["title"]["keys"], "같은 규제(URL) 열쇠": v["same"]["url"]["keys"]})
        rows.append(r)
    for sub, q in quality.items():
        rows.append({"gold": R.NO_GOLD, "표": "부분집합(규제)", "판": label, "갈래": sub, "끝난 칸": q["cells"],
                     "URL": q["urls"], "루트만": q["rootOnly"]["num"], "③ 화이트리스트 안 rate": q["whitelistIn"],
                     "④ 기간 안 rate": q["dateIn"], "날짜 없음 rate": q["dateUnknown"], "칸당 항목 중앙": q["itemsPerCellMedian"]})
    return rows


def _tally_row(label: str, t: dict[str, Any], usd: float | None) -> dict[str, Any]:
    return {"gold": R.NO_GOLD, "표": "되묻기", "판": REASK, "갈래": label, "되물은 칸": t["cells"], "실패 칸": t["failedCells"],
            "앞 걸림": t["flaggedBefore"], "답 항목": t["answerItems"], "답 중 통과": t["answerPassed"], "답 중 걸림": t["answerFlagged"],
            "통과 되풀이 뺌": t["repeatedPassed"], "잃은 항목": t["lost"],
            "풀린 걸림": t["resolved"], "풀림 rate": t["resolvedRate"],
            "까닭 앞": {k: n for k, n in t["reasonsBefore"].items() if n},
            "까닭 뒤": {k: n for k, n in t["reasonsAfter"].items() if n}, "USD 기록": usd}


def collect(out: str | Path, *, mock: bool = False) -> dict[str, Any]:
    """판 셋 + 되묻기 + 바닥(A1·A3)을 읽어 셈 꼴로 — summary·verdict 가 같이 쓴다."""
    out = Path(out)
    base = out / ("mock" if mock else "arms")
    rdir = out / ("mock/reask" if mock else "reask") / REASK
    wl = load_whitelists(out / WHITELISTS_REL)
    prices = load_prices(json.loads((out / "models-luna.json").read_text(encoding="utf-8")))
    topics = topic_keys(json.loads((out / "input" / "input.json").read_text(encoding="utf-8")))
    views: dict[str, dict[str, Any]] = {}
    for arm in ARMS:
        if (base / arm).exists():
            views[arm] = arm_view(load_arm(base / arm, arm), wl, prices, CAPS[arm])
    bases: dict[str, dict[str, Any]] = {}
    ma1a = out.parent / "MA1a" / "arms"
    for arm in BASES:
        if (ma1a / arm).exists():
            v = arm_view(load_arm(ma1a / arm, arm), wl, prices, None)
            if arm == "A1":
                v["spentUsd"] = None  # iro 흐름 — 지출에 2부가 듦(1부 셈과 견주지 않음)
            bases[arm] = v
    reask: dict[str, Any] | None = None
    fp = rdir / "flags-before.json"
    flags_only: dict[str, Any] | None = None
    if fp.exists() and "Llo" in views and not (rdir / "run.json").exists():  # 걸림만 셈(되묻기 안 돎)
        fb = json.loads(fp.read_text(encoding="utf-8"))
        flags_only = {"targets": len(fb["targets"]), **{k: _flag_count(fb["flags"], ig) for k, ig in
                                                         (("all", ()), ("noWl", ("outside_whitelist",)))}}
    elif fp.exists() and "Llo" in views:
        fb = json.loads(fp.read_text(encoding="utf-8"))
        answers = load_answers(rdir)
        merged = check_units_urls(merge_reask(views["Llo"]["units"], fb["flags"], answers), wl)
        rcalls = read_jsonl(rdir / "calls.jsonl")
        rrun = json.loads((rdir / "run.json").read_text(encoding="utf-8")) if (rdir / "run.json").exists() else {}
        reask = {"flags": fb["flags"], "targets": fb["targets"], "answers": len(answers), "merged": merged,
                 "tally": reask_tally(fb["flags"], merged), "tallyNoWl": reask_tally(fb["flags"], merged,
                                                                                   ignore=("outside_whitelist",)),
                 "stats": call_stats(rcalls, CAPS[REASK]), "cost": hand_cost(rcalls, prices) if rcalls else None,
                 "run": rrun}
    quality = {a: quality_rows(v["units"], topics) for a, v in {**views, **bases}.items()}
    if reask is not None:
        quality[REASK] = quality_rows(reask["merged"], topics)
    return {"views": views, "bases": bases, "reask": reask, "quality": quality, "topics": topics,
            "flagsOnly": flags_only}


def _flag_count(flags: Sequence[dict[str, Any]], ignore: Iterable[str]) -> dict[str, Any]:
    """걸림 수만(되묻기 없이): 걸린 칸·걸린 항목/전체 항목·까닭별."""
    ig = frozenset(ignore)
    items = [it for f in flags for it in f["items"]]
    hit = [it for it in items if _flagged(it, ig)]
    why = {r: sum(r in set(it["reasons"]) - ig for it in hit) for r in REASONS}
    return {"cells": sum(any(_flagged(it, ig) for it in f["items"]) for f in flags), "items": len(hit),
            "den": len(items), "reasons": {k: n for k, n in why.items() if n}}


def _usd(v: dict[str, Any] | None) -> float | None:
    c = (v or {}).get("cost")
    return c.get("recordedUsd") if c else None


def verdicts(col: dict[str, Any]) -> dict[str, dict[str, Any]]:
    views, q = col["views"], col["quality"]
    a = verdict_a({k: gate_a_view(v) for k, v in views.items()})
    rows = {k: q[k][GATE_B["topics"]] for k in views}
    base = q.get(GATE_B["base"], {}).get(GATE_B["topics"])
    b = verdict_b(rows, base)
    b_mid = verdict_b(rows, base, arms=("Lmid",))
    r = col["reask"]
    fo = col.get("flagsOnly")
    c = verdict_c(r["tally"], _usd(views.get("Llo")), _usd(r)) if r else {"answer": "안 돎", "why": [f"LloR {NOT_RUN}"] + (
        [f"Llo 걸림(문 눈금): 칸 {fo['all']['cells']} · 항목 {fo['all']['items']}/{fo['all']['den']} · {fo['all']['reasons']}",
         f"화이트리스트 뺀 눈금(곁): 칸 {fo['noWl']['cells']} · 항목 {fo['noWl']['items']}/{fo['noWl']['den']}"] if fo else [])}
    c_nowl = verdict_c(r["tallyNoWl"], _usd(views.get("Llo")), _usd(r)) if r else None
    out = {"A": a, "B": b, "B-Lmid": b_mid, "C": c, "C-화이트리스트 뺌(곁)": c_nowl}
    if "Llo" in views and "Lmid" not in views:  # 곁 갈래: Llo 쪽 조건만(문은 위 A 그대로)
        g = gate_a_view(views["Llo"])
        side = verdict_a({"Llo": g, "Lmid": g})
        out["A-Llo만(곁)"] = {"answer": f"{side['answer']}(Llo) — Lmid {NOT_RUN}", "why": side["why"]}
    return out


def build_summary(out: str | Path, gold_sets: Any, *, mock: bool = False,
                  dest: str | Path | None = None) -> tuple[Path, Path]:
    out = Path(out)
    col = collect(out, mock=mock)
    rows: list[dict[str, Any]] = []
    for arm, v in col["views"].items():
        rows += _arm_rows(arm, v, col["quality"][arm], arm)
    r = col["reask"]
    if r is not None:
        st = r["stats"]
        c = r["cost"] or {}
        rows.append({"gold": R.NO_GOLD, "표": "판", "판": REASK, "갈래": "되묻기 호출", "끝": (r["run"] or {}).get("ending"),
                     "호출": st["calls"], "검색 합": st["searchTotal"], "검색 분포": st["searchDist"],
                     "검색 None": st["searchUnknown"], "상한": st["cap"], "over_cap": st["overCap"], "상한 닿음": st["atCap"],
                     "상한 닿음·글 없음": st["atCapNoText"], "무시된 검색 합": st["searchIgnoredTotal"],
             "core 검색 합(k+1 셈)": st["coreHostedTotal"], "입력 중앙(캐시 포함)": st["inputMedian"],
                     "출력 중앙": st["outputMedian"], "USD 중앙": st["usdMedian"], "USD 최대": st["usdMax"],
                     "실패(갈래)": st["failures"] or None, "잘림": st["truncated"], "초": st["wallTotal"],
                     "USD 손셈": c.get("handUsd"), "USD 기록": c.get("recordedUsd"),
                     "USD 같음": c.get("same") if c else None})
        for sub, q in col["quality"][REASK].items():
            rows.append({"gold": R.NO_GOLD, "표": "부분집합(규제)", "판": f"{REASK}(Llo 합침)", "갈래": sub,
                         "끝난 칸": q["cells"], "URL": q["urls"], "루트만": q["rootOnly"]["num"],
                         "③ 화이트리스트 안 rate": q["whitelistIn"], "④ 기간 안 rate": q["dateIn"],
                         "날짜 없음 rate": q["dateUnknown"], "칸당 항목 중앙": q["itemsPerCellMedian"]})
        rows.append(_tally_row("걸림 전부(문)", r["tally"], _usd(r)))
        rows.append(_tally_row("화이트리스트 까닭 뺌(곁)", r["tallyNoWl"], _usd(r)))
    for arm in (*ARMS, REASK):
        if arm not in col["views"] and not (arm == REASK and r is not None):
            rows.append({"gold": R.NO_GOLD, "표": "판", "판": arm, "갈래": "—", "끝": NOT_RUN})
    for arm, v in col["bases"].items():
        rows += _arm_rows(arm, v, col["quality"][arm], f"MA1a {arm}(실판·다른 토픽 수)")
    vs = verdicts(col)
    for k, v in vs.items():
        if v is None:
            continue
        rows.append({"gold": R.NO_GOLD, "표": "§0 답", "판": k, "답": v["answer"], "문에 쓴 수": "; ".join(v["why"])})
    notes = [
        "정답 셋 없음(IRO 정답 0) — 주 지표는 코드로 세는 것만. 판정기(LLM)·사람 라벨 0.",
        "호출 = 1부 call_tools 줄(규제 N · 키워드 1 · 컨트로버시 N). 입력 = inputTokens + cacheRead + cacheWrite(캐시 포함).",
        "검색 수 = 실행된(completed) web_search_call 수(드라이버가 SDK 응답에서 셈). 상한을 넘는 시도는 status searching 으로 하나 남아 「무시된 검색」 — core hostedCalls 는 그것까지 세어 k+1(곁 칸 「core 검색 합」). None(SDK 셈 없음 — 모의·MA1a 실판)은 셈 밖(「검색 None」).",
        "over_cap = 검색 수 > 그 판의 상한 · 상한 닿음 = 검색 수 = 상한 · 「글 없음」 = 그 호출 textLen 0·None.",
        "부분집합(규제) = 토픽 T01~T05(B 의 견줌 자리, A1 과 같은 토픽) · T01~T02(A3 곁) · 전부 — 분모 = 규제 URL 수(실패 칸 뺌).",
        "되묻기 셈(고침 1 B4): 답 항목 중 같은 칸 통과 항목과 URL 이 겹치는 것은 먼저 뺌(「통과 되풀이 뺌」) · 칸마다 풀린 = min(앞 걸림, 답 중 통과) · 잃은 = max(0, 앞 걸림 − 답 항목). reask_failed 칸은 풀림 0.",
        "풀림 = 걸린 자리를 통과한 다른 항목이 채운 수이지, 같은 규제를 고쳤다는 뜻이 아니다.",
        "A1 대 견줌은 모델과 상한이 같이 바뀐 것 — 상한만의 효과는 Llo 대 Lmid. MA1a A1 의 「USD 흐름」 은 2부까지 든 실행 지출이라 1부 셈과 견주지 않는다(—).",
        "걸림 까닭: root_only · outside_whitelist(규제만) · out_of_range · date_unknown(날짜 없음·못 읽음) · no_url.",
        "MA1a 줄은 실판(2026-11-09, A1 = T01~T05 · A3 = 예산에 2칸) — 같은 스냅숏 코드, 다른 날·다른 토픽 수. A3 는 문에 쓰지 않음.",
        "화이트리스트·기간은 코드 잣대이지 「맞는 규제」가 아니다. URL 열림(⑦) 은 검사하지 않음.",
    ]
    extra = {"verdicts": vs, "views": {k: {kk: vv for kk, vv in v.items() if kk != "units"} for k, v in
                                       {**col["views"], **col["bases"]}.items()},
             "quality": col["quality"],
             "reask": None if r is None else {k: v for k, v in r.items() if k not in ("merged", "flags")}}
    return R.write(out if dest is None else dest, "summary", rows, gold_sets, columns=["gold", *COLS],
                   title="MIC1 — luna + 검색 수 상한 · 출처 코드 검사 · 되묻기 (정답 없는 코드 지표)", notes=notes,
                   extra=extra)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) not in (2, 3) or argv[0] not in ("flags", "summary", "verdict") or (len(argv) == 3 and argv[2] != "mock"):
        print("쓰는 법: python -m evalrig.iro.measure_mic1 flags|summary|verdict <out/MIC1> [mock]", file=sys.stderr)
        return 2
    out = Path(argv[1])
    mock = len(argv) == 3
    if argv[0] == "flags":
        print("flags", write_flags(out, mock=mock))
        return 0
    if argv[0] == "verdict":
        print(json.dumps(verdicts(collect(out, mock=mock)), ensure_ascii=False, indent=1))
        return 0
    root = out.resolve().parents[2]  # <marcspon>/esg-lab/out/MIC1 → <marcspon>
    gs = G.load(root / GOLD_PATH_REL)
    jp, mp = build_summary(out, [gs], mock=mock, dest=out / "mock-summary" if mock else None)
    print("summary", jp, mp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
