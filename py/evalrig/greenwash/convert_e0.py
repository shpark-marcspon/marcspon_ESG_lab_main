"""1차 그린 결과 → 채점 단위(발주서 EVB §2-4). 1차 결과는 읽기만 한다.

입력 갈래(판별은 `schemaVersion` / `schema_version`):
- `greenwash.result@1`(esg-app, camel) — 최상위 dict, 또는 mig1 실행처럼 그 dict 의 목록(원소마다 한 결과)
- `greenwash-result 0.1`(esg-lab, snake, `schema_version: "0.1"`)
둘 다 아니면 `UnknownShape`(빈 결과로 삼키지 않음). 꼴의 정본은 esg-app `greenwash.result@1.schema.json`·
esg-lab `schemas/greenwash-result.schema.json`(시험 T15 가 픽스처를 두 스키마로 검증).

항목 → 단위 `payload{page, sentence(정규화), sha12, level, category: absent, rawIndex[], folded}`. 한 쪽이 두 파트에
들어 같은 문장이 두 번 실리면(GW1 흠 13) (쪽, sha12) 로 하나로 접고 접은 수를 센다. 읽지 못한 쪽
(`candidateStats.failedPages` — ★2 실측 자리)은 `notChecked` 로 따로 싣는다(채점기가 재현 분모에서 뺌).
카테고리 칸은 1차에 없다 — `riskTags` 를 카테고리로 읽지 않는다.
"""
from __future__ import annotations

from typing import Any

from evalrig.common.units import Origin, Unit, converter
from evalrig.greenwash import units as U

NAME = "evb.greenwash.convert_e0@1"
V1 = "greenwash.result@1"
V01 = "greenwash-result@0.1"


class UnknownShape(ValueError):
    """1차 그린 결과 꼴이 아님."""


def detect(result: Any) -> str:
    if not isinstance(result, dict):
        raise UnknownShape(f"dict 가 아님: {type(result).__name__}")
    if result.get("schemaVersion") == V1:
        return V1
    if result.get("schema_version") == "0.1":
        return V01
    raise UnknownShape(f"schemaVersion/schema_version 모름: {result.get('schemaVersion')!r}/{result.get('schema_version')!r}")


def _fields(result: dict[str, Any], shape: str) -> dict[str, Any]:
    if shape == V1:
        src = result["source"]
        failed = result["candidateStats"]["failedPages"]
        return {"sha256": src["sha256"], "pageCount": src["pageCount"], "config": result["config"],
                "models": result.get("models"), "failed": failed}
    src = result["source"]
    cs = result.get("candidate_stats") or {"failed_pages": []}
    return {"sha256": src["sha256"], "pageCount": src.get("page_count"), "config": result["config"],
            "models": result.get("models"), "failed": cs["failed_pages"]}


def convert(result: dict[str, Any], *, run: str = "") -> dict[str, Any]:
    """결과 하나 → {schema, source, pages, config, units[Unit], folded, notChecked[], items}."""
    shape = detect(result)
    f = _fields(result, shape)
    payloads: dict[tuple[int, str], dict[str, Any]] = {}
    folded = 0
    for idx, it in enumerate(result["items"]):
        page, sent, level = int(it["page"]), it["sentence"], it["level"]
        k = (page, U.sha12(sent))
        if k in payloads:
            payloads[k]["rawIndex"].append(idx)
            payloads[k]["folded"] += 1
            folded += 1
            continue
        payloads[k] = {"page": page, "sentence": U.norm(sent), "sha12": k[1], "level": level,
                       "category": dict(U.ABSENT), "rawIndex": [idx], "folded": 0}
    origin = Origin(engine="v1", schema=shape, run=run or "-", converter=NAME)
    units = [Unit(module="greenwash", unit=U.UNIT, key=f"p{p}:{s}", group=f"p{p}", payload=pl, origin=origin)
             for (p, s), pl in payloads.items()]
    return {
        "schema": shape,
        "source": {"sha256": f["sha256"], "pageCount": f["pageCount"]},
        "pages": list(result.get("pages") or []),
        "config": f["config"],
        "models": f["models"],
        "units": units,
        "folded": folded,
        "notChecked": sorted({int(x["page"]) for x in f["failed"]}),
        "items": len(result["items"]),
    }


def convert_doc(doc: Any, *, run: str = "") -> list[dict[str, Any]]:
    """파일 하나(dict 또는 dict 의 목록) → 결과마다 하나. 빈 목록·모르는 원소는 UnknownShape."""
    if isinstance(doc, list):
        if not doc:
            raise UnknownShape("빈 목록")
        return [convert(r, run=f"{run}#{i}") for i, r in enumerate(doc)]
    return [convert(doc, run=run)]


@converter(NAME)
def from_v1(result: Any, **ctx: Any) -> list[Unit]:
    """공통 Converter 꼴 — 단위만(접은 수·notChecked 는 `convert`)."""
    return convert(result, run=ctx.get("run", ""))["units"]


def summary(c: dict[str, Any]) -> dict[str, Any]:
    """산출 파일용(단위는 dict 로)."""
    return {**{k: v for k, v in c.items() if k != "units"}, "units": [u.to_dict() for u in c["units"]]}
