"""밴드 MA1a — 1차 1부(`materiality.colab@155`) 판 다섯의 정답 없는 셈(`sid:MARC-TICK-557`).

판: A0 지금(gpt-4o·tool_choice auto) · A1 검색 강제(iro@155, 1부 required + 2부) · A2 gpt-4.1 강제 ·
A3 gpt-6-luna 강제(조건) · A4 검색 강제 + 기업 맥락. 판정기 J4(무관 규제)·J3(칸 혼재) = gpt-6-luna(정답 아님).

이 모듈은 **순수 셈**이다 — esgapi·openai import 0, 네트워크 0. 실호출·1차 엔진은 git 밖 드라이버
(`esg-lab/out/MA1a/drivers/`)가 하고, 이 모듈은 그 산출 파일만 읽는다.

산출 자리(`<out>` = `esg-lab/out/MA1a`):
- `arms/<판>/result.json`(result@1, A1 은 result@2) · `calls.jsonl`(드라이버 Recorder 한 줄 = 1차 `run_call` 의 시도 하나)
  · `run.json`(ending · wallS · spentUsd · blocked)
- `judge/calls.jsonl`(semgraph.llm 이 남김) · `judge/j4.jsonl` · `judge/j3.jsonl`(줄마다 {id, group, answer})
- `prices.json`(드라이버가 스냅숏 models.json(+ A3 사본)에서 뽑은 단가 — 모델 → input·cacheRead·output·webSearchPerCall)

드라이버 calls.jsonl 줄 꼴(core 의 토큰 — `inputTokens` 는 캐시 **뺀** 입력, core `compute_cost_usd` 꼴):
  {seq, entry: call_tools|call, phase, topic, flowAttempt, toolsPresent, toolChoice, model, inputTokens,
   cacheReadTokens, outputTokens, hostedCalls, costUsd, resultType, failure, wallS}
semgraph 줄 꼴(`inputTokens` 는 캐시 **든** 입력, `cachedTokens`): {tag, model, inputTokens, cachedTokens, outputTokens, costUsd}

    python -m evalrig.iro.measure_ma1a summary <out>      # 호출 0, 키 없는 셸
"""
from __future__ import annotations

import json
import math
import sys
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..common import gold as G
from ..common import report as R
from ..common.match import norm_text, ratio, unmeasurable
from ..common.units import Unit
from .from_v1 import convert_result, topic_key
from .urlcheck import check_units_urls, load_whitelists, norm_url, table_row

ARMS = ("A0", "A1", "A2", "A3", "A4")
PART1 = ("regulation", "controversy")
PART2 = ("impact", "financial", "pathway")
J4_LABELS = ("related", "unrelated", "unknown")
JUDGE_MODEL = "gpt-6-luna"
JUDGE_NOTE = "판정기 = luna, 정답 아님"
WHITELISTS_REL = Path("app/apps/api-py/esgapi/materiality/engines/colab_155/whitelists.py")
GOLD_PATH_REL = Path("prework/gold-eva/iro-empty.json")

# ── 판정기 지시문·꼴(우리 글 — 실재 글 0) ────────────────────────────────────────────────────

CTX_TEMPLATE = (
    "[기업 맥락 — 입력 파일의 칸을 그대로 옮김]\n"
    "- 회사: {company}\n"
    "- 산업: {industries}\n"
    "- 가치사슬 활동: {activities}\n"
    "- 지역 범위: {geo}\n"
    "위 맥락의 회사에 적용될 법한 항목을 우선한다."
)
"""A4 의 1부 가변 입력 끝과 J4 판정에 같이 붙는 맥락 단락의 틀(빈칸 넷 — 값은 드라이버가 입력 파일에서만 채운다)."""

J4_SYSTEM = (
    "너는 규제 목록 검토자다. 아래 [기업 맥락]의 회사와 [규제 항목] 목록이 주어진다. 항목마다 그 회사에 적용될 법한지 "
    "판정한다: related(사업·산업·지역상 적용될 법함) · unrelated(이 회사의 사업·산업·지역과 무관) · unknown(글만으로 "
    "가를 수 없음). 항목 id 를 그대로 돌려주고, why 에 까닭을 한 줄로 적는다. 모든 id 에 답한다."
)
J4_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rows"],
    "properties": {"rows": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "label", "why"],
        "properties": {"id": {"type": "string"}, "label": {"type": "string", "enum": list(J4_LABELS)},
                       "why": {"type": "string"}}}}},
}

J3_SYSTEM = (
    "너는 중대성평가 표의 칸 검토자다. 줄마다 그 줄이 놓인 칸(갈래)과 글이 주어진다. 갈래의 뜻: regulation = 법령·정책·"
    "기준(사건 아님) · controversy = 회사·업계의 사건·제재·논란 기사(법령 아님) · impact = 회사가 환경·사회에 미치는 "
    "영향의 정의(재무 효과 아님) · financial = 그 주제가 회사 재무에 주는 효과(영향 정의 아님) · pathway = 영향이 재무로 "
    "이어지는 경로. 글이 놓인 칸이 아니라 다른 칸의 것이면 mixed=true 이고 cols 에 그 글이 속할 칸 이름을 적는다(아니면 "
    "mixed=false, cols 빈 목록). 줄 id 를 그대로 돌려주고 why 에 까닭을 한 줄로. 모든 id 에 답한다."
)
J3_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rows"],
    "properties": {"rows": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "mixed", "cols", "why"],
        "properties": {"id": {"type": "string"}, "mixed": {"type": "boolean"},
                       "cols": {"type": "array", "items": {"type": "string", "enum": [*PART1, *PART2]}},
                       "why": {"type": "string"}}}}},
}


# ── 읽기 ────────────────────────────────────────────────────────────────────────────────────


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_arm(d: str | Path, arm: str) -> dict[str, Any]:
    """판 하나: run.json · result.json(→ 단위, `convert_result(run=<판>)` 그대로) · calls.jsonl."""
    d = Path(d)
    run = json.loads((d / "run.json").read_text(encoding="utf-8")) if (d / "run.json").exists() else None
    rp = d / "result.json"
    result = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else None
    units = convert_result(result, run=arm) if result is not None else []
    return {"arm": arm, "run": run, "result": result, "units": units, "calls": read_jsonl(d / "calls.jsonl")}


# ── 검색 수 ─────────────────────────────────────────────────────────────────────────────────


def _lower_bound_row(c: dict[str, Any]) -> bool:
    """재시도(1차 run_call 의 둘째 시도 · core 안 공급사 재시도 coreAttempts ≥ 2)·잘림 줄 — 그 칸의 검색 수는 하한
    (MARC-ISSUE-349 ① 꼴). core 의 hosted_calls 는 마지막 응답 몫만 더한다(앞 시도의 검색은 비용에만 남음)."""
    return (c.get("flowAttempt") or 1) >= 2 or (c.get("coreAttempts") or 1) >= 2 or c.get("failure") == "truncated"


def searched(units: Iterable[Unit], calls: Sequence[dict[str, Any]]) -> list[Unit]:
    """1부 단위(규제·컨트로버시)의 `payload.searched` 를 (토픽, 갈래)마다 calls 의 hostedCalls 합으로 채운 **새** 목록.

    그 칸의 줄이 calls 에 없거나 hostedCalls 가 None 인 줄이 있으면 None(0 아님). 재시도·잘림 줄이 있으면
    `payload.searchedLowerBound = True`. 원 단위는 고치지 않는다. 2부 단위는 그대로(검색 없음 — 손대지 않음)."""
    out = []
    for u in units:
        p = dict(u.payload)
        if p.get("branch") in PART1:
            rows = [c for c in calls if c.get("phase") == p["branch"] and c.get("topic") is not None
                    and topic_key(c["topic"]) == u.group]
            if not rows or any(c.get("hostedCalls") is None for c in rows):
                p["searched"] = None
                p["searchedLowerBound"] = None
            else:
                p["searched"] = sum(int(c["hostedCalls"]) for c in rows)
                p["searchedLowerBound"] = any(_lower_bound_row(c) for c in rows)
        out.append(Unit(module=u.module, unit=u.unit, key=u.key, group=u.group, payload=p,
                        sources=u.sources, status=u.status, origin=u.origin))
    return out


# ── 토픽 사이 같은 규제 ──────────────────────────────────────────────────────────────────────


def norm_title(s: str | None) -> str:
    """NFC · 소문자 · 문장부호(유니코드 P*)를 빈칸으로 · 공백 접기. 같음만 견준다(부분 문자열 아님)."""
    t = unicodedata.normalize("NFC", s or "").lower()
    t = "".join(" " if unicodedata.category(ch).startswith("P") else ch for ch in t)
    return norm_text(t)


def _across(pairs: Iterable[tuple[str, str]]) -> tuple[int, int]:
    """(열쇠, 토픽) 짝 → (둘 이상 토픽에 나온 열쇠 수, 그 열쇠에 든 짝 수). 같은 토픽 안 되풀이는 토픽 하나로."""
    by: dict[str, set[str]] = {}
    occ: dict[str, int] = {}
    for k, g in pairs:
        by.setdefault(k, set()).add(g)
        occ[k] = occ.get(k, 0) + 1
    multi = [k for k, gs in by.items() if len(gs) >= 2]
    return len(multi), sum(occ[k] for k in multi)


def same_regulation_across_topics(units: Iterable[Unit]) -> dict[str, Any]:
    """규제 갈래의 「토픽 사이 같은 규제」: 제목 열쇠(norm_title) · URL 열쇠(urlcheck.norm_url).

    꼴 {title: {keys, items, den, emptyTitle, rate}, url: {keys, urls, den, rate}} — keys = 둘 이상 토픽에 나온 열쇠 수,
    items/urls = 그 열쇠에 든 항목/URL 수, den = 규제 항목 수/URL 수(빈 제목 항목은 분모에 들되 열쇠는 안 셈)."""
    reg = [u for u in units if u.payload.get("branch") == "regulation"]
    tpairs, upairs, n_items, empty, n_urls = [], [], 0, 0, 0
    for u in reg:
        for it in u.payload.get("items") or []:
            n_items += 1
            k = norm_title(it.get("title"))
            if k:
                tpairs.append((k, u.group))
            else:
                empty += 1
            for url in it.get("urls") or []:
                n_urls += 1
                upairs.append((norm_url(url), u.group))
    tk, ti = _across(tpairs)
    uk, uu = _across(upairs)
    return {"title": {"keys": tk, "items": ti, "den": n_items, "emptyTitle": empty, "rate": ratio(ti, n_items)},
            "url": {"keys": uk, "urls": uu, "den": n_urls, "rate": ratio(uu, n_urls)}}


# ── 결과 칸 셈(JSON 실패·self_check·잘림) ──────────────────────────────────────────────────────


def outcome_counts(result: dict[str, Any] | None) -> dict[str, Any]:
    """1부 (토픽, 갈래) 칸: done · failed(실패 갈래별) · selfCheck 있음 · selfCheck.ok 가 false 인 칸."""
    if result is None:
        return {"cells": 0, "done": 0, "failed": 0, "unparsable": 0, "failedKinds": {}, "selfCheck": 0, "selfCheckNotOk": 0}
    cells = done = failed = sc = sc_bad = 0
    kinds: dict[str, int] = {}
    for t in (result.get("part1") or {}).get("topics") or []:
        for b in PART1:
            br = t.get(b) or {}
            cells += 1
            if br.get("status") == "done":
                done += 1
                s = br.get("selfCheck")
                if isinstance(s, dict):
                    sc += 1
                    if s.get("ok") is False:
                        sc_bad += 1
            elif br.get("status") == "failed":
                failed += 1
                k = ((br.get("failure") or {}).get("kind")) or "unknown"
                kinds[k] = kinds.get(k, 0) + 1
    return {"cells": cells, "done": done, "failed": failed, "unparsable": kinds.get("unparsable", 0),
            "failedKinds": dict(sorted(kinds.items())),
            "selfCheck": sc, "selfCheckNotOk": sc_bad}


# ── 판정기 답 ────────────────────────────────────────────────────────────────────────────────

UNPARSED = {"status": "unparsed"}


def parse_judge(row: Any, kind: str) -> dict[str, Any]:
    """판정기 답 한 줄(dict 또는 JSON 글) → J4 `{label}` / J3 `{mixed, cols}`. 못 읽으면 `{status: unparsed}`
    (related·unrelated·mixed 어느 쪽으로도 접지 않는다)."""
    if kind not in ("j4", "j3"):
        raise ValueError(f"kind {kind!r} ∉ (j4, j3)")
    if isinstance(row, str):
        try:
            row = json.loads(row)
        except json.JSONDecodeError:
            return dict(UNPARSED)
    if not isinstance(row, dict):
        return dict(UNPARSED)
    if kind == "j4":
        lab = row.get("label")
        return {"label": lab} if lab in J4_LABELS else dict(UNPARSED)
    mixed, cols = row.get("mixed"), row.get("cols")
    if not isinstance(mixed, bool) or not isinstance(cols, list) or not all(c in (*PART1, *PART2) for c in cols):
        return dict(UNPARSED)
    return {"mixed": mixed, "cols": list(cols)}


def split_answer(text: str | None, ids: Sequence[str], kind: str) -> dict[str, dict[str, Any]]:
    """호출 하나의 답 글({rows: [{id, …}]}) → id 마다 parse_judge. 글이 깨졌거나 id 가 빠지면 그 id 는 unparsed.
    같은 id 가 두 번이면 unparsed(어느 쪽인지 가를 수 없음)."""
    out = {i: dict(UNPARSED) for i in ids}
    try:
        doc = json.loads(text) if text is not None else None
    except json.JSONDecodeError:
        return out
    rows = doc.get("rows") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        return out
    seen: dict[str, int] = {}
    for r in rows:
        if isinstance(r, dict) and isinstance(r.get("id"), str):
            seen[r["id"]] = seen.get(r["id"], 0) + 1
    for r in rows:
        if isinstance(r, dict) and r.get("id") in out and seen[r["id"]] == 1:
            out[r["id"]] = parse_judge(r, kind)
    return out


def tally_judge(rows: Iterable[dict[str, Any]], kind: str) -> dict[str, dict[str, Any]]:
    """줄 [{group, parsed}] → 갈래(group)별 수 + unparsed + 분모(den = 줄 수, unparsed 포함).

    J4: related·unrelated·unknown · 「무관 비율」 = unrelated/den. J3: mixed·notMixed · 「혼재 비율」 = mixed/den."""
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        g = r["group"]
        p = r["parsed"]
        t = out.setdefault(g, {"den": 0, "unparsed": 0, **({k: 0 for k in J4_LABELS} if kind == "j4"
                                                         else {"mixed": 0, "notMixed": 0})})
        t["den"] += 1
        if p.get("status") == "unparsed":
            t["unparsed"] += 1
        elif kind == "j4":
            t[p["label"]] += 1
        else:
            t["mixed" if p["mixed"] else "notMixed"] += 1
    for t in out.values():
        num = t["unrelated"] if kind == "j4" else t["mixed"]
        t["rate"] = ratio(num, t["den"])
    return dict(sorted(out.items()))


# ── 비용 ─────────────────────────────────────────────────────────────────────────────────────


def round7(x: float) -> float:
    return math.floor(x * 1e7 + 0.5) / 1e7


def load_prices(models_json: dict[str, Any]) -> dict[str, dict[str, float]]:
    """models.json 꼴 → {모델: {input, cacheRead, output, webSearchPerCall(없으면 None)}} (USD / 1M 토큰 · 건)."""
    out = {}
    for k, m in (models_json.get("models") or {}).items():
        p = m.get("pricing") or {}
        out[k] = {"input": p["input"], "cacheRead": p["cacheRead"], "output": p["output"], "cacheWrite": p.get("cacheWrite"),
                  "webSearchPerCall": (m.get("hostedToolPricing") or {}).get("webSearchPerCall")}
    return out


def call_cost(c: dict[str, Any], prices: dict[str, dict[str, float]]) -> float:
    """호출 한 줄의 손셈 USD. semgraph 줄(`cachedTokens` 칸)은 입력이 캐시를 품고, core 줄(`cacheReadTokens`)은 뺀다."""
    p = prices[c["model"]]
    out_t = c.get("outputTokens") or 0
    if "cachedTokens" in c:
        cached = c.get("cachedTokens") or 0
        fresh = (c.get("inputTokens") or 0) - cached
    else:
        cached = c.get("cacheReadTokens") or 0
        fresh = c.get("inputTokens") or 0
    write = c.get("cacheWriteTokens") or 0  # core 줄만(없으면 0 — 기록에 칸이 없던 판은 손셈이 그만큼 작다)
    usd = (fresh * p["input"] + cached * p["cacheRead"] + out_t * p["output"] + write * (p.get("cacheWrite") or 0)) / 1e6
    hosted = c.get("hostedCalls") or 0
    if hosted:
        web = p.get("webSearchPerCall")
        if web is None:
            raise ValueError(f"모델 {c['model']} 에 검색 건당 요금이 없는데 hostedCalls={hosted}")
        usd += hosted * web
    return usd


def cost(calls: Sequence[dict[str, Any]], prices: dict[str, dict[str, float]]) -> dict[str, Any]:
    """손셈 합 · 기록(costUsd) 합 · 같은지(7자리). 모델 없는 줄(공급사 안 부름)은 0."""
    hand = sum(call_cost(c, prices) for c in calls if c.get("model"))
    rec = sum(c.get("costUsd") or 0.0 for c in calls)
    return {"calls": len(calls), "handUsd": round7(hand), "recordedUsd": round7(rec),
            "same": round7(hand) == round7(rec)}


# ── 판 하나의 코드 지표 ───────────────────────────────────────────────────────────────────────


def arm_metrics(a: dict[str, Any], whitelists: dict[str, set[str]], prices: dict[str, dict[str, float]]) -> dict[str, Any]:
    run = a.get("run") or {}
    calls1 = [c for c in a["calls"] if c.get("phase") in ("regulation", "keywords", "controversy")]
    us = searched(a["units"], calls1)
    us1 = check_units_urls([u for u in us if u.payload.get("branch") in PART1], whitelists)
    known = [u for u in us1 if u.payload.get("searched") is not None]
    c_all = cost(a["calls"], prices) if a["calls"] else None
    c_1 = cost(calls1, prices) if calls1 else None
    return {
        "arm": a["arm"],
        "ending": run.get("ending"),
        "blocked": run.get("blocked"),
        "skipped": run.get("skipped"),
        "calls": len(a["calls"]),
        "calls1": len(calls1),
        "toolCalls": sum(bool(c.get("toolsPresent")) for c in calls1),
        "requiredCalls": sum(c.get("toolChoice") == "required" for c in calls1 if c.get("toolsPresent")),
        "hosted": sum(c["hostedCalls"] for c in calls1) if calls1 and all(c.get("hostedCalls") is not None for c in calls1) else None,
        "searchedRate": ratio(sum(bool(u.payload["searched"]) for u in known), len(known)),
        "lowerBoundCells": sum(bool(u.payload.get("searchedLowerBound")) for u in us1),
        "url": {b: table_row(us1, b) for b in PART1},
        "same": same_regulation_across_topics(us1),
        "outcomes": outcome_counts(a.get("result")),
        "truncated": sum(c.get("failure") == "truncated" for c in calls1),
        "wallS": run.get("wallS"),
        "spentUsd": run.get("spentUsd"),
        "cost": c_all,
        "cost1": c_1,
        "units": us1,
    }


# ── 요약 ─────────────────────────────────────────────────────────────────────────────────────

ARM_COLS = ["표", "지표 갈래", "판", "갈래", "끝", "호출", "1부 호출", "도구 실린 호출", "강제(required) 호출", "실제 검색 수",
            "검색 있던 칸 rate", "하한 칸", "URL", "① 꼴 rate", "루트만", "③ 화이트리스트 안 rate", "④ 기간 안 rate",
            "기간 밖", "날짜 없음·못 읽음", "같은 규제(제목) 열쇠", "같은 규제(제목) rate", "같은 규제(URL) 열쇠",
            "같은 규제(URL) rate", "JSON 못 읽음", "실패(갈래)", "selfCheck ok=false", "잘림", "초", "USD 손셈", "USD 기록", "USD 흐름", "USD 같음"]
JUDGE_COLS = ["판정 대상", "related", "unrelated", "unknown", "mixed", "not mixed", "unparsed", "분모", "무관 rate",
              "혼재 rate", "라벨 대조", "주"]


def arm_rows(m: dict[str, Any]) -> list[dict[str, Any]]:
    base = {"gold": R.NO_GOLD, "표": "판", "지표 갈래": "code", "판": m["arm"]}
    if m["blocked"]:
        return [{**base, "갈래": "—", "끝": f"막힘: {m['blocked']}"}]
    if m["skipped"]:
        return [{**base, "갈래": "—", "끝": f"뺌: {m['skipped']}"}]
    rows = []
    for b in PART1:
        t = m["url"][b]
        r = {**base, "갈래": b, "URL": t["URL"], "① 꼴 rate": t["① 꼴 rate"], "루트만": t["루트만"],
             "③ 화이트리스트 안 rate": t["③ 화이트리스트 안 rate"], "④ 기간 안 rate": t["④ 기간 안 rate"],
             "기간 밖": t["기간 밖"], "날짜 없음·못 읽음": t["날짜 없음·못 읽음"], "검색 있던 칸 rate": t["검색 호출 있던 칸 rate"]}
        if b == "regulation":
            s = m["same"]
            r.update({"같은 규제(제목) 열쇠": s["title"]["keys"], "같은 규제(제목) rate": s["title"]["rate"],
                      "같은 규제(URL) 열쇠": s["url"]["keys"], "같은 규제(URL) rate": s["url"]["rate"]})
        rows.append(r)
    o, c = m["outcomes"], m["cost"] or {}
    rows.append({**base, "갈래": "판 전체", "끝": m["ending"], "호출": m["calls"], "1부 호출": m["calls1"],
                 "도구 실린 호출": m["toolCalls"], "강제(required) 호출": m["requiredCalls"], "실제 검색 수": m["hosted"],
                 "검색 있던 칸 rate": m["searchedRate"], "하한 칸": m["lowerBoundCells"],
                 "JSON 못 읽음": f"{o['unparsable']}/{o['cells']}",
                 "실패(갈래)": f"{o['failed']}/{o['cells']}" + (f" {o['failedKinds']}" if o["failedKinds"] else ""),
                 "selfCheck ok=false": f"{o['selfCheckNotOk']}/{o['selfCheck']}", "잘림": m["truncated"], "초": m["wallS"],
                 "USD 손셈": c.get("handUsd"), "USD 기록": c.get("recordedUsd"),
                 "USD 흐름": None if m["spentUsd"] is None else round7(m["spentUsd"]),
                 "USD 같음": None if not c else (c["same"] and (m["spentUsd"] is None or round7(m["spentUsd"]) == c["recordedUsd"]))})
    return rows


def judge_rows(j4: dict[str, dict[str, Any]], j3: dict[str, dict[str, Any]], labels_present: bool = False) -> list[dict[str, Any]]:
    base = {"gold": R.NO_GOLD, "표": "판정기", "지표 갈래": f"judge:{JUDGE_MODEL.split('-')[-1]}"}
    rows = []
    for g, t in j4.items():
        rows.append({**base, "판정 대상": f"J4 {g}", "related": t["related"], "unrelated": t["unrelated"],
                     "unknown": t["unknown"], "unparsed": t["unparsed"], "분모": t["den"], "무관 rate": t["rate"],
                     "주": JUDGE_NOTE})
    for g, t in j3.items():
        rows.append({**base, "판정 대상": f"J3 {g}", "mixed": t["mixed"], "not mixed": t["notMixed"],
                     "unparsed": t["unparsed"], "분모": t["den"], "혼재 rate": t["rate"],
                     "라벨 대조": None if labels_present else unmeasurable("no_labels"), "주": JUDGE_NOTE})
    return rows


def judge_tallies(jdir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """j4.jsonl·j3.jsonl(줄 {id, group, answer}) → tally. answer 는 split_answer 가 낸 id 의 parse 결과.
    J3 group 은 「<재료 출처>|<갈래>」 — 갈래별 + 출처별 합(critic R6, 출처마다 분모) + 전체."""
    j4 = [{"group": r["group"], "parsed": parse_judge(r.get("answer"), "j4")} for r in read_jsonl(jdir / "j4.jsonl")]
    j3 = [{"group": r["group"], "parsed": parse_judge(r.get("answer"), "j3")} for r in read_jsonl(jdir / "j3.jsonl")]
    t3 = tally_judge(j3, "j3")
    if j3:
        srcs = tally_judge([{**r, "group": r["group"].split("|", 1)[0] + "|합"} for r in j3], "j3")
        tot = tally_judge([{**r, "group": "전체"} for r in j3], "j3")
        t3 = {**t3, **srcs, **tot}
    return tally_judge(j4, "j4"), t3


def build_summary(out: str | Path, gold_sets: Any, *, arms: Sequence[str] = ARMS, arms_sub: str = "arms",
                  judge_sub: str = "judge", dest: str | Path | None = None) -> tuple[Path, Path]:
    """`<out>/summary.{json,md}` — 코드 지표(표 「판」, 지표 갈래 code)와 판정기 지표(표 「판정기」, judge:luna)를 가름 칸으로 나눔."""
    out = Path(out)
    wl = load_whitelists(out / WHITELISTS_REL)
    prices = load_prices(json.loads((out / "prices.json").read_text(encoding="utf-8")))
    rows: list[dict[str, Any]] = []
    extra: dict[str, Any] = {"arms": {}}
    for arm in arms:
        d = out / arms_sub / arm
        if not d.exists():
            continue
        m = arm_metrics(load_arm(d, arm), wl, prices)
        rows += arm_rows(m)
        extra["arms"][arm] = {k: v for k, v in m.items() if k != "units"}
    j4, j3 = judge_tallies(out / judge_sub)
    rows += judge_rows(j4, j3)
    jc = read_jsonl(out / judge_sub / "calls.jsonl")
    if jc:
        extra["judgeCost"] = {tag: cost([c for c in jc if c.get("tag", "").startswith(tag)], prices) for tag in ("j4", "j3")}
    notes = [
        "표 「판」 = 코드 지표(정답 없음) · 표 「판정기」 = " + JUDGE_NOTE + " — 두 표를 섞어 읽지 않는다.",
        "검색 수 = 드라이버 calls.jsonl 의 hostedCalls 합((토픽, 갈래)마다). 칸 줄이 없으면 None(—), 재시도(흐름·core)·잘림이 든 칸은 「하한 칸」.",
        "「실제 검색 수」(판 전체)는 1부 호출 전부의 합 — 키워드 호출(토픽 칸 없음)의 검색도 든다. 「검색 있던 칸 rate」 는 (토픽, 규제|컨트로버시) 칸만.",
        "「USD 같음」 거짓의 까닭(MA1a 실판): A0 = 잘린 호출의 검색 1건이 core 비용에는 들고 ToolLoopFailed.steps 에는 안 실림(검색 수 하한) · A3 = 드라이버 기록에 캐시 쓰기 토큰 칸이 없었음(10,000 × 0.125/1M). 기록(core costUsd)이 정본.",
        "「JSON 못 읽음」 = 실패 갈래 unparsable 만 · 「실패(갈래)」 = 모든 failed(budget_exceeded·truncated 등 갈래별).",
        "같은 규제 = 규제 항목의 제목 열쇠(NFC·소문자·문장부호 접기, 같음만)/URL 열쇠가 둘 이상 토픽에 나옴. rate 분모 = 규제 항목 수/URL 수.",
        "URL ①~④ 는 evalrig.iro.urlcheck 그대로(화이트리스트 = 스냅숏 whitelists.py, 판 = 결과 scope.whitelist).",
        "J3 라벨 대조 = unmeasurable(no_labels) — 사람 라벨 0(mi3/labels.csv 빈 칸, 발주서 §7 1 (가)).",
    ]
    return R.write(out if dest is None else dest, "summary", rows, gold_sets, columns=["gold", *ARM_COLS, *JUDGE_COLS],
                   title="MA1a — 1차 1부 판 다섯(정답 없는 코드 지표 · 판정기 지표)", notes=notes, extra=extra)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) not in (2, 3) or argv[0] != "summary" or (len(argv) == 3 and argv[2] != "mock"):
        print("쓰는 법: python -m evalrig.iro.measure_ma1a summary <out/MA1a> [mock]", file=sys.stderr)
        return 2
    out = Path(argv[1])
    root = out.resolve().parents[2]  # <marcspon>/esg-lab/out/MA1a → <marcspon>
    gs = G.load(root / GOLD_PATH_REL)
    if len(argv) == 3:  # 모의 판(mock/·mock-judge/) → mock-summary/
        jp, mp = build_summary(out, [gs], arms_sub="mock", judge_sub="mock-judge", dest=out / "mock-summary")
    else:
        jp, mp = build_summary(out, [gs])
    print("summary", jp, mp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
