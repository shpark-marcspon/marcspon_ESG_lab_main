"""1차 데이터 검증 결과(`dataverify.result@2`, colab_156) → 채점 단위(발주서 EVB §2-4). 결과는 읽기만 한다.

입력 = 결과 + 그 실행의 입력(`dataverify.input@1` 또는 `synth.py` 산출). 1차 결과의 항목은 ID 가 아니라
[대분류, 중분류, 소분류, 단위] 넷이다(★4). 그래서 입력 행의 네 칸 글자가 같은 것으로 ID 를 푼다. 못 푼 것은
`unresolved` 로 따로 세고 버리지 않는다.

- 검산(`review.checks[]` 의 `kind:"check"`) 한 번의 해마다 → 단위 `payload{items[ID…], unresolved[넷…], year, verdict:
  OK|FAIL, rule, seq, tool}`. 단건 도구(raw 인자, 이름 없음)는 항목이 없어 `unresolvedCheck` 로 센다.
- 인사이트 행 → 단위 `payload{indicatorText, id|null, year: absent, verdict: "flag", area}`. ID 는 소분류 글자가 하나로
  맞으면 그것, 아니면 글 안의 「E-001」 꼴(NFKC + 대시 맞춤), 그도 아니면 unresolved.
- `review.status` 가 `failed`·`not_run` 이면 산출 머리에 그 갈래를 싣는다(단위 0 을 「잘 돌았는데 없음」으로 읽지 않게).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from evalrig.common.units import Origin, Unit, converter

NAME = "evb.dataverify.convert_156@1"
SHAPE = "dataverify.result@2"
CHECK_UNIT = "dataverify.check_year@1"
INSIGHT_UNIT = "dataverify.insight_row@1"
_ID = re.compile(r"\b([A-Z]-\d{3})\b")
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—−"), "-")


class UnknownShape(ValueError):
    """dataverify.result@2 가 아님."""


def _quad_key(q: Any) -> tuple | None:
    if isinstance(q, list) and len(q) == 4:
        return tuple(q)
    return None


def index_rows(inp: dict[str, Any]) -> dict[str, Any]:
    by_quad: dict[tuple, str] = {}
    by_small: dict[str, list[str]] = {}
    ids = set()
    for rows in inp["data"].values():
        for r in rows:
            by_quad[(r["대분류"], r["중분류"], r["소분류"], r["단위"])] = r["ID"]
            by_small.setdefault(r["소분류"], []).append(r["ID"])
            ids.add(r["ID"])
    return {"quad": by_quad, "small": by_small, "ids": ids}


def resolve_quad(ix: dict[str, Any], q: Any) -> str | None:
    k = _quad_key(q)
    return ix["quad"].get(k) if k is not None else None


def _quads(tool_items: dict[str, Any]) -> list[Any]:
    by = tool_items.get("byName")
    if not by:
        return []
    out: list[Any] = []
    for k in ("total", "a", "b", "target", "numerator", "denominator", "expected"):
        if k in by:
            out.append(by[k])
    if "addends" in by and isinstance(by["addends"], list):
        out[0:0] = list(by["addends"])
    return out


def resolve_text(ix: dict[str, Any], text: str) -> str | None:
    hit = ix["small"].get(text)
    if hit and len(hit) == 1:
        return hit[0]
    t = unicodedata.normalize("NFKC", text).translate(_DASHES)
    found = [m for m in _ID.findall(t) if m in ix["ids"]]
    return found[0] if len(set(found)) == 1 else None


def convert(result: dict[str, Any], inp: dict[str, Any], *, run: str = "") -> dict[str, Any]:
    if not isinstance(result, dict) or result.get("schemaVersion") != SHAPE:
        raise UnknownShape(f"schemaVersion {result.get('schemaVersion') if isinstance(result, dict) else type(result).__name__!r} ≠ {SHAPE}")
    ix = index_rows(inp)
    origin = Origin(engine="v1", schema=SHAPE, run=run or "-", converter=NAME)
    review = result["review"]
    units: list[Unit] = []
    unresolved_quads = 0
    unresolved_checks = 0
    for c in review.get("checks") or []:
        if c.get("kind") != "check":
            continue
        quads = _quads(c["items"])
        if not quads:
            unresolved_checks += 1
        ids, bad = [], []
        for q in quads:
            i = resolve_quad(ix, q)
            if i is None:
                bad.append(q)
            elif i not in ids:
                ids.append(i)
        unresolved_quads += len(bad)
        for y in c["years"]:
            year = None if y["year"] is None else str(y["year"])
            units.append(Unit(module="dataverify", unit=CHECK_UNIT, key=f"c{c['seq']}:{year}", group=str(year),
                              payload={"items": ids, "unresolved": bad, "year": year, "verdict": y["status"],
                                       "rule": c["rule"], "seq": c["seq"], "tool": c["tool"]}, origin=origin))
    ins = result.get("insight") or {}
    unresolved_rows = 0
    for n, row in enumerate(ins.get("rows") or []):
        i = resolve_text(ix, row["indicator"])
        unresolved_rows += i is None
        units.append(Unit(module="dataverify", unit=INSIGHT_UNIT, key=f"i{n}", group=row.get("area", ""),
                          payload={"indicatorText": row["indicator"], "id": i, "year": {"status": "absent"},
                                   "verdict": "flag", "area": row.get("area")}, origin=origin))
    return {
        "schema": SHAPE,
        "reviewStatus": review["status"],
        "insightStatus": ins.get("status"),
        "failure": review.get("failure"),
        "units": units,
        "unresolved": {"quads": unresolved_quads, "checksWithoutNames": unresolved_checks, "insightRows": unresolved_rows},
    }


SIDE_SHAPES = ("dataverify.digest@1",)  # mig1 실행 목록에 결과와 같이 실리는 요약 — 결과가 아니라 건너뜀(수는 셈)


def convert_doc(doc: Any, inp: dict[str, Any], *, run: str = "") -> list[dict[str, Any]]:
    """파일 하나 → 결과마다 하나. 목록 안의 요약(SIDE_SHAPES)은 건너뛰고 `skippedSide` 에 센다. 그 밖의 모르는 꼴은 에러."""
    if isinstance(doc, list):
        if not doc:
            raise UnknownShape("빈 목록")
        side = [r for r in doc if isinstance(r, dict) and r.get("schemaVersion") in SIDE_SHAPES]
        rest = [r for r in doc if r not in side]
        if not rest:
            raise UnknownShape("결과 없이 요약뿐")
        out = [convert(r, inp, run=f"{run}#{i}") for i, r in enumerate(rest)]
        for c in out:
            c["skippedSide"] = len(side)
        return out
    return [convert(doc, inp, run=run)]


@converter(NAME)
def from_v1(result: Any, **ctx: Any) -> list[Unit]:
    return convert(result, ctx["input"], run=ctx.get("run", ""))["units"]


def summary(c: dict[str, Any]) -> dict[str, Any]:
    return {**{k: v for k, v in c.items() if k != "units"}, "units": [u.to_dict() for u in c["units"]]}
