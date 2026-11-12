"""IRO 1차 산출 → 채점 단위 `iro.topic-branch@1`.

세 꼴(발주서 EVA §2 (2)):
- (a) `materiality.result@1|@2` JSON(앱 실행) — `convert_result`
- (b) 배포본 엑셀 `materiality-websearch.xlsx` 의 `RAW_JSON` A1 — `convert_raw_json`(글) · `read_raw_json_xlsx`(파일)
- (c) 코랩 기준 실행 응답 `responses/NNNN-c17.json`(규제) · `NNNN-c22.json`(컨트로버시) — `convert_colab`

단위 하나 = (토픽, 갈래). `key = "<토픽 열쇠>|<갈래>"`, `group = <토픽 열쇠>`, 토픽 열쇠 = 토픽 이름 NFC·공백 접기.
`payload = {topic, branch, items[{title, date, date_status, summary, urls, extra}], searched, citations, scope, note}`.
항목 URL 은 `sources[{kind: web, ref: url, check: None}]`(항목 차례대로, `payload.items[i].urls` 와 같은 글).
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any

from ..common.match import norm_text
from ..common.units import Origin, Source, Unit, converter
from . import BRANCHES, MODULE, UNIT

XLSX_CELL_MAX = 32767  # 엑셀 칸 글자 상한 — 닿으면 잘렸다고 본다
SUMMARY_HEAD = 80
_MISSING = {"", "none", "null", "n/a", "na", "unknown", "-", "없음", "미상", "해당 없음", "확인 불가"}
_DATE_RE = re.compile(r"^\s*(\d{4})[-./](\d{1,2})[-./](\d{1,2})\.?\s*$")


def topic_key(name: str) -> str:
    """토픽 이름 → 열쇠(NFC + 공백 접기). 2차는 입력 때 열쇠를 쓴다(`from_v2`)."""
    return norm_text(name)


def parse_date(v: Any) -> tuple[str | None, str]:
    """날짜 칸 → (ISO `YYYY-MM-DD` 또는 None, `ok|missing|unreadable`)."""
    if v is None:
        return None, "missing"
    s = str(v).strip()
    if s.lower() in _MISSING:
        return None, "missing"
    m = _DATE_RE.match(s)
    if not m:
        return None, "unreadable"
    try:
        d = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None, "unreadable"
    return d.isoformat(), "ok"


def _urls(it: dict[str, Any], branch: str) -> list[str]:
    keys = ("primary_url", "secondary_url", "url") if branch == "regulation" else ("url", "primary_url", "secondary_url")
    out = []
    for k in keys:
        v = it.get(k)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    return out


def _web_item(it: dict[str, Any], branch: str) -> dict[str, Any]:
    if branch == "regulation":
        raw = it.get("event_date")
        if raw in (None, "") or str(raw).strip().lower() in _MISSING:
            raw = it.get("publish_date")
        extra_keys = ("jurisdiction", "authority", "status", "region", "event_date", "publish_date")
    else:
        raw = it.get("date")
        extra_keys = ("severity", "status", "region")
    date, ds = parse_date(raw)
    return {
        "title": str(it.get("title") or ""),
        "date": date,
        "date_status": ds,
        "summary": str(it.get("summary") or "")[:SUMMARY_HEAD],
        "urls": _urls(it, branch),
        "extra": {k: it.get(k) for k in extra_keys if k in it},
    }


def _regulation_items(parsed: Any) -> list[dict[str, Any]]:
    """규제 `parsed.results` = {지역: [항목]}(지역 차례 그대로) 또는 [항목]."""
    if not isinstance(parsed, dict):
        return []
    res = parsed.get("results")
    if isinstance(res, dict):
        out = []
        for region, items in res.items():
            for it in items or []:
                if isinstance(it, dict):
                    out.append({**it, "region": it.get("region", region)})
        return out
    if isinstance(res, list):
        return [it for it in res if isinstance(it, dict)]
    return []


def _controversy_items(parsed: Any) -> list[dict[str, Any]]:
    if not isinstance(parsed, dict):
        return []
    res = parsed.get("results")
    if isinstance(res, dict):
        return [it for it in (res.get("items") or []) if isinstance(it, dict)]
    if isinstance(res, list):
        return [it for it in res if isinstance(it, dict)]
    return []


def _unit(
    topic: str,
    branch: str,
    items: list[dict[str, Any]],
    origin: Origin,
    *,
    status: str | None = None,
    searched: bool | None = None,
    citations: list[str] | None = None,
    scope: dict[str, Any] | None = None,
    note: str | None = None,
) -> Unit:
    if branch not in BRANCHES:
        raise ValueError(f"갈래 {branch!r} ∉ {BRANCHES}")
    tk = topic_key(topic)
    st = status if status is not None else ("ok" if items else "missing")
    srcs = [Source("web", u) for it in items for u in it.get("urls", [])]
    return Unit(
        module=MODULE,
        unit=UNIT,
        key=f"{tk}|{branch}",
        group=tk,
        payload={
            "topic": topic,
            "branch": branch,
            "items": items,
            "searched": searched,
            "citations": citations,
            "scope": scope or {},
            "note": note,
        },
        sources=tuple(srcs),
        status=st,
        origin=origin,
    )


# ---------- (a) materiality.result@1|@2 ----------
RESULT_SCHEMAS = ("materiality.result@1", "materiality.result@2")


def _part2_items(sub_branch: dict[str, Any], branch: str, subtopic: str) -> list[dict[str, Any]]:
    parsed = sub_branch.get("parsed") if isinstance(sub_branch, dict) else None
    rows = (parsed or {}).get("pathways" if branch == "pathway" else "impacts") or []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append({
            "title": str(r.get("impact_definition") or ""),
            "date": None,
            "date_status": "missing",
            "summary": str(r.get("business_impact") or r.get("context") or "")[:SUMMARY_HEAD],
            "urls": [],
            "extra": {"subtopic": subtopic, **{k: r.get(k) for k in ("impact_type", "risk_type", "impact_driver", "financial_impact") if k in r}},
        })
    return out


@converter("iro.result-json@1")
def convert_result(result: dict[str, Any], *, run: str, **_: Any) -> list[Unit]:
    """앱 결과 JSON → 단위. part1 규제·컨트로버시 + (part2 가 done 이면) 영향·재무·경로(서브토픽을 이어 한 토픽 갈래)."""
    sv = result.get("schemaVersion")
    if sv not in RESULT_SCHEMAS:
        raise ValueError(f"schemaVersion {sv!r} ∉ {RESULT_SCHEMAS}")
    origin = Origin("v1", sv, run, convert_result.name)
    scope = {k: (result.get("scope") or {}).get(k) for k in ("whitelist", "dateRange", "sector")}
    out: list[Unit] = []
    for t in (result.get("part1") or {}).get("topics") or []:
        name = t.get("topic") or ""
        for b in ("regulation", "controversy"):
            br = t.get(b) or {}
            raw_items = br.get("items")
            if raw_items is None:
                raw_items = (_regulation_items if b == "regulation" else _controversy_items)(br.get("parsed"))
            items = [_web_item(it, b) for it in raw_items if isinstance(it, dict)]
            ok = br.get("status") == "done"
            out.append(_unit(name, b, items, origin, status=None if ok else "failed", scope=scope,
                             note=None if ok else f"branch status {br.get('status')!r}"))
    p2 = result.get("part2") or {}
    if p2.get("status") == "done":
        for t in p2.get("topics") or []:
            name = t.get("topic") or ""
            for b in ("impact", "financial", "pathway"):
                items: list[dict[str, Any]] = []
                bad = []
                for st in t.get("subtopics") or []:
                    sb = st.get(b) or {}
                    if sb.get("status") != "done":
                        bad.append(st.get("name") or "")
                    items += _part2_items(sb, b, st.get("name") or "")
                out.append(_unit(name, b, items, origin, status="failed" if bad else None, scope=scope,
                                 note=f"subtopic not done: {len(bad)}" if bad else None))
    return out


# ---------- (b) 배포본 엑셀 RAW_JSON ----------
def read_raw_json_xlsx(path: str | Path) -> str:
    """`materiality-websearch.xlsx` 의 `RAW_JSON` 시트 A1 글(openpyxl 읽기 전용)."""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True)
    try:
        v = wb["RAW_JSON"]["A1"].value
    finally:
        wb.close()
    return "" if v is None else str(v)


@converter("iro.raw-json@1")
def convert_raw_json(text: str, *, run: str, scope: dict[str, Any] | None = None, **_: Any) -> list[Unit]:
    """RAW_JSON 글 → 단위. 32,767자에 닿거나 JSON 이 깨지면 단위 하나(`*|raw`, failed, note=truncated|parse_error)."""
    origin = Origin("v1", "materiality.websearch-xlsx@1", run, convert_raw_json.name)
    sc = dict(scope or {})

    def fail(why: str) -> list[Unit]:
        return [Unit(module=MODULE, unit=UNIT, key="*|raw", group="*", status="failed", origin=origin,
                     payload={"topic": None, "branch": None, "items": [], "searched": None, "citations": None,
                              "scope": sc, "note": why})]

    if len(text) >= XLSX_CELL_MAX:
        return fail("truncated")
    try:
        d = json.loads(text)
    except json.JSONDecodeError:
        return fail("parse_error")
    out: list[Unit] = []
    reg = d.get("regulation_updates_by_topic") or {}
    con = d.get("controversies_by_topic") or {}
    for name in list(reg) + [n for n in con if n not in reg]:
        for b, src, pk in (("regulation", reg, "parsed"), ("controversy", con, "json")):
            if name not in src:
                continue
            e = src[name] or {}
            parsed = e.get(pk)
            if e.get("parse_error") or not isinstance(parsed, dict):
                out.append(_unit(name, b, [], origin, status="failed", scope=sc, note="parse_error"))
                continue
            raw_items = (_regulation_items if b == "regulation" else _controversy_items)(parsed)
            out.append(_unit(name, b, [_web_item(it, b) for it in raw_items], origin, scope=sc))
    return out


# ---------- (c) 코랩 기준 실행 응답 ----------
COLAB_CELLS = {"c17": "regulation", "c22": "controversy"}
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.S)


def response_text(resp: dict[str, Any]) -> tuple[str, list[str] | None, bool]:
    """응답 → (마지막 message 의 output_text 글, url_citation URL 목록(주석이 하나도 없으면 None), web_search_call 유무)."""
    text, cites, searched = "", [], False
    for o in resp.get("output") or []:
        if o.get("type") == "web_search_call":
            searched = True
        if o.get("type") == "message":
            for c in o.get("content") or []:
                if c.get("type") == "output_text":
                    text = c.get("text") or ""
                    for a in c.get("annotations") or []:
                        if a.get("type") == "url_citation" and a.get("url"):
                            cites.append(a["url"])
    return text, (cites or None), searched


def parse_body(text: str) -> dict[str, Any] | None:
    """본문 JSON(코드 울타리·앞뒤 글 허용) → dict 또는 None."""
    s = _FENCE.sub("", text.strip())
    try:
        d = json.loads(s)
        return d if isinstance(d, dict) else None
    except json.JSONDecodeError:
        pass
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(s[i: j + 1])
        return d if isinstance(d, dict) else None
    except json.JSONDecodeError:
        return None


def read_topics_map(path: str | Path) -> list[str]:
    """`topics-map.txt`(줄마다 `<번호>\\t<토픽 이름>`) → 토픽 이름 차례."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(line.split("\t", 1)[1].strip() if "\t" in line else line.strip())
    return out


def colab_files(folder: str | Path) -> dict[str, list[Path]]:
    """`responses/` 의 셀별 파일(번호 차례)."""
    d = Path(folder)
    return {c: sorted(d.glob(f"*-{c}.json")) for c in COLAB_CELLS}


@converter("iro.colab-responses@1")
def convert_colab(responses: dict[str, list[dict[str, Any]]], *, run: str, topics: list[str],
                  scope: dict[str, Any] | None = None, **_: Any) -> list[Unit]:
    """셀별 응답 목록(번호 차례) → 단위. 응답 i 번째 = topics[i] 의 (토픽, 갈래). 수가 다르면 ValueError."""
    origin = Origin("v1", "openai.responses@colab", run, convert_colab.name)
    sc = dict(scope or {})
    out: list[Unit] = []
    for cell, branch in COLAB_CELLS.items():
        rs = responses.get(cell) or []
        if len(rs) != len(topics):
            raise ValueError(f"{cell}: 응답 {len(rs)} ≠ 토픽 {len(topics)}")
        for name, resp in zip(topics, rs):
            text, cites, searched = response_text(resp)
            body = parse_body(text)
            if body is None:
                out.append(_unit(name, branch, [], origin, status="failed", searched=searched, citations=cites,
                                 scope=sc, note="parse_error"))
                continue
            raw_items = (_regulation_items if branch == "regulation" else _controversy_items)(body)
            out.append(_unit(name, branch, [_web_item(it, branch) for it in raw_items], origin,
                             searched=searched, citations=cites, scope=sc))
    return out


def load_colab(folder: str | Path) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """코랩 기준 실행 폴더 → (셀별 응답, 토픽 차례). 글은 메모리에서만 쓴다(어디에도 옮기지 않음)."""
    root = Path(folder)
    topics = read_topics_map(root / "topics-map.txt")
    files = colab_files(root / "responses")
    return {c: [json.loads(p.read_text(encoding="utf-8")) for p in fs] for c, fs in files.items()}, topics
