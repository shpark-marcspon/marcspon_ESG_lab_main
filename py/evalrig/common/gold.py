"""정답 셋 꼴 `evalrig.gold@1` — 읽기(load) · 얼리기(freeze) · 검사(check).

정답 셋 하나 = JSON 파일 하나. 머리(메타)는 모든 모듈이 같고 `items[]` 의 꼴은 모듈이
`unit` 이름으로 등록한다(`register_unit`). 메타 규칙(발주서 EVA §1 「이미 정해진 것」 3):

- `provenance`(누가·무엇으로 지었나)와 `review`(사람 검토가 있었나)는 **필수**. 모르면 「모름」이
  아니라 갈래로 적는다 — 검토가 없거나 모르면 `review.status = "none"`.
  `provenance` 는 `kind·by·from[]·at` 넷 다 필수, `from[]` 원소는 글(경로·shortId) 또는 `{ref, sha256|null}`
  (sha256 은 16~64 자 hex). `review` 의 필수 칸은 `status` 하나 — `by·at·note·sample` 은 빠지면 null 로 본다
  (`review_field`). `status=none` 이면 `sample` 은 null 이거나 없어야 한다.
- `pending[]` 은 아직 안 들어온 정답의 자리(예: 컨설턴트 수정본 → 질문 shortId). 지금 메우지 않는다.
- `coverage`: `exhaustive`(정답이 그 범위를 다 덮음) | `partial`(일부만 — 빠진 것이 틀린 것이 아님).
  partial 이면 정밀(precision)·F1 셈을 거부하고 재현(recall)만 낸다(`precision_allowed`·`guard_precision`).
- `synthetic`: 지은 글·가상 데이터로 만든 셋이면 true.
- `conventions{}`(선택): 모듈이 자유로 쓰는 판정 약속 — 예 `labels`(판정 값 목록)·`accept`(같이 맞다고 볼 값).
  sha16 에 든다(약속을 바꾸면 얼림이 깨진다).
- `frozen{sha16, at}`: `freeze` 가 채운다. sha16 = `frozen` 칸을 뺀 문서의 정규 JSON sha256 앞 16자.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema

SCHEMA_ID = "evalrig.gold@1"

MODULES = ("materiality", "drafting", "translation", "greenwash", "dataverify", "scope3")
PROVENANCE_KINDS = (
    "runner_made",  # 러너가 코드·규칙으로 지음
    "agent_draft",  # 에이전트(LLM) 초안, 확인 없음
    "agent_draft_checked",  # 에이전트 초안 + 독립 판정·표본 확인
    "official_publication",  # 공식 간행물(예: 공식 영문판) — 사람이 쓴 글
    "consultant",  # 컨설턴트가 고친 판
    "human_label",  # 사람이 직접 단 라벨
)
REVIEW_STATUSES = ("none", "agent_sampled", "human_partial", "human_full")
COVERAGES = ("exhaustive", "partial")

_SHA16 = {"type": "string", "pattern": "^[0-9a-f]{16}$"}
_NULLSTR = {"type": ["string", "null"]}

GOLD_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": [
        "schema", "setId", "module", "unit", "version", "coverage", "synthetic",
        "provenance", "review", "pending", "items",
    ],
    "additionalProperties": False,
    "properties": {
        "schema": {"const": SCHEMA_ID},
        "setId": {"type": "string", "minLength": 1},
        "module": {"enum": list(MODULES)},
        "unit": {"type": "string", "pattern": "^[a-z0-9_.-]+@[0-9]+$"},
        "version": {"type": "integer", "minimum": 1},
        "coverage": {"enum": list(COVERAGES)},
        "synthetic": {"type": "boolean"},
        "provenance": {
            "type": "object",
            "required": ["kind", "by", "from", "at"],
            "additionalProperties": False,
            "properties": {
                "kind": {"enum": list(PROVENANCE_KINDS)},
                "by": {"type": "string", "minLength": 1},
                "from": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {"type": "string", "minLength": 1},
                            {
                                "type": "object",
                                "required": ["ref", "sha256"],
                                "additionalProperties": False,
                                "properties": {
                                    "ref": {"type": "string", "minLength": 1},
                                    "sha256": {"oneOf": [{"type": "null"}, {"type": "string", "pattern": "^[0-9a-f]{16,64}$"}]},
                                },
                            },
                        ]
                    },
                },
                "at": {"type": "string", "minLength": 1},
            },
        },
        "review": {
            "type": "object",
            "required": ["status"],  # by·at·note·sample 은 빠지면 null 로 본다
            "additionalProperties": False,
            "properties": {
                "status": {"enum": list(REVIEW_STATUSES)},
                "by": _NULLSTR,
                "at": _NULLSTR,
                "note": _NULLSTR,
                "sample": {
                    "oneOf": [
                        {"type": "null"},
                        {
                            "type": "object",
                            "required": ["ok", "n"],
                            "additionalProperties": False,
                            "properties": {
                                "ok": {"type": "integer", "minimum": 0},
                                "n": {"type": "integer", "minimum": 1},
                            },
                        },
                    ]
                },
            },
        },
        "pending": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["what", "question", "slot"],
                "additionalProperties": False,
                "properties": {
                    "what": {"type": "string", "minLength": 1},
                    "question": _NULLSTR,
                    "slot": {"type": "string", "minLength": 1},
                },
            },
        },
        "frozen": {
            "type": "object",
            "required": ["sha16", "at"],
            "additionalProperties": False,
            "properties": {"sha16": _SHA16, "at": {"type": "string"}},
        },
        "meta": {"type": "object"},  # 모듈 칸(가리키는 원 파일·그 sha16 등)
        "conventions": {"type": "object"},  # 선택 — 모듈 자유(판정 값 목록 labels·같이 맞다고 볼 값 accept 등)
        "items": {"type": "array"},
    },
}

_UNITS: dict[str, dict[str, Any]] = {}


class GoldError(ValueError):
    """정답 셋이 꼴에 맞지 않음."""


def register_unit(name: str, item_schema: dict[str, Any]) -> None:
    """`unit` 이름(예: `drafting.fact@1`)에 items 한 줄의 JSON Schema 를 등록한다.

    같은 이름에 다른 꼴을 다시 등록하면 ValueError(모듈 둘이 이름을 겹쳐 쓰는 것을 막음).
    """
    jsonschema.Draft202012Validator.check_schema(item_schema)
    old = _UNITS.get(name)
    if old is not None and old != item_schema:
        raise ValueError(f"unit {name!r} 는 이미 다른 꼴로 등록됨")
    _UNITS[name] = item_schema


def registered_units() -> tuple[str, ...]:
    return tuple(sorted(_UNITS))


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha16(gs: dict[str, Any]) -> str:
    """`frozen` 칸을 뺀 문서의 정규 JSON sha256 앞 16자."""
    body = {k: v for k, v in gs.items() if k != "frozen"}
    return hashlib.sha256(_canonical(body)).hexdigest()[:16]


def file_sha16(path: str | Path) -> str:
    """파일 바이트의 sha256 앞 16자(가리키는 원 파일을 메타에 적을 때)."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def check(gs: dict[str, Any], *, require_unit: bool = True) -> dict[str, Any]:
    """꼴 검사. 맞으면 gs 를 그대로 돌려주고, 틀리면 GoldError.

    - 메타: GOLD_SCHEMA(필수 칸·갈래).
    - items: `unit` 에 등록된 꼴. 등록이 없으면 `require_unit=True` 일 때 실패.
    - `review.status == "none"` 이면 `sample` 은 null 이어야 함(표본 없이 검토 없음).
    - `frozen` 이 있으면 sha16 이 내용과 같아야 함(얼린 뒤 고침 적발).
    """
    v = jsonschema.Draft202012Validator(GOLD_SCHEMA)
    errs = sorted(v.iter_errors(gs), key=lambda e: list(e.absolute_path))
    if errs:
        e = errs[0]
        where = "/".join(str(p) for p in e.absolute_path) or "(root)"
        raise GoldError(f"gold 꼴 틀림 @ {where}: {e.message}")
    if gs["review"]["status"] == "none" and gs["review"].get("sample") is not None:
        raise GoldError("review.status=none 인데 sample 이 있음")
    item_schema = _UNITS.get(gs["unit"])
    if item_schema is None:
        if require_unit:
            raise GoldError(f"unit {gs['unit']!r} 가 등록되지 않음(register_unit)")
    else:
        iv = jsonschema.Draft202012Validator(item_schema)
        for i, it in enumerate(gs["items"]):
            ie = next(iter(iv.iter_errors(it)), None)
            if ie is not None:
                raise GoldError(f"items[{i}] 꼴 틀림({gs['unit']}): {ie.message}")
    fz = gs.get("frozen")
    if fz is not None and fz["sha16"] != sha16(gs):
        raise GoldError(f"frozen.sha16 {fz['sha16']} ≠ 내용 {sha16(gs)} — 얼린 뒤 고쳐짐")
    return gs


def load(path: str | Path, *, require_unit: bool = True) -> dict[str, Any]:
    """파일을 읽고 `check` 한다."""
    gs = json.loads(Path(path).read_text(encoding="utf-8"))
    return check(gs, require_unit=require_unit)


def dumps(gs: dict[str, Any]) -> str:
    """정규 꼴로 글 만들기(키 차례 고정, 들여쓰기 1) — 두 번 써도 바이트가 같다."""
    return json.dumps(gs, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def freeze(path: str | Path, *, at: str | None = None, require_unit: bool = True) -> str:
    """파일을 검사하고 `frozen{sha16, at}` 을 채워 다시 쓴 뒤 sha16 을 돌려준다.

    `at` 이 없으면 `provenance.at` 을 쓴다 — 같은 내용이면 파일 바이트까지 같다(결정적).
    """
    p = Path(path)
    gs = json.loads(p.read_text(encoding="utf-8"))
    gs.pop("frozen", None)
    check(gs, require_unit=require_unit)
    s = sha16(gs)
    gs["frozen"] = {"sha16": s, "at": at if at is not None else gs["provenance"]["at"]}
    p.write_text(dumps(gs), encoding="utf-8")
    return s


def new(
    *,
    set_id: str,
    module: str,
    unit: str,
    provenance: dict[str, Any],
    review: dict[str, Any],
    items: list[Any],
    coverage: str,
    synthetic: bool,
    pending: list[dict[str, Any]] | None = None,
    version: int = 1,
    meta: dict[str, Any] | None = None,
    conventions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """정답 셋 dict 짓기(검사 포함, 얼리지 않음). 필수 메타는 키워드로만 받는다."""
    gs: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "setId": set_id,
        "module": module,
        "unit": unit,
        "version": version,
        "coverage": coverage,
        "synthetic": synthetic,
        "provenance": copy.deepcopy(provenance),
        "review": copy.deepcopy(review),
        "pending": copy.deepcopy(pending or []),
        "items": items,
    }
    if meta is not None:
        gs["meta"] = meta
    if conventions is not None:
        gs["conventions"] = copy.deepcopy(conventions)
    return check(gs, require_unit=False)


def review_field(gs: dict[str, Any], name: str) -> Any:
    """`review` 의 선택 칸 읽기 — 빠졌으면 None."""
    return gs["review"].get(name)


def precision_allowed(gs: dict[str, Any]) -> bool:
    """정밀·F1 을 셀 수 있나 — coverage 가 exhaustive 일 때만."""
    return gs["coverage"] == "exhaustive"


def guard_precision(gs: dict[str, Any]) -> None:
    """partial 정답으로 정밀을 세려 하면 GoldError(재현만 낼 것)."""
    if not precision_allowed(gs):
        raise GoldError(f"{gs['setId']}: coverage=partial — 정밀·F1 셈 거부(재현만)")


def header(gs: dict[str, Any]) -> dict[str, Any]:
    """보고 표 머리에 찍는 메타(report 가 쓴다). 얼리지 않은 셋은 sha16 이 None."""
    fz = gs.get("frozen") or {}
    return {
        "setId": gs["setId"],
        "sha16": fz.get("sha16"),
        "provenance": gs["provenance"]["kind"],
        "review": gs["review"]["status"],
        "coverage": gs["coverage"],
        "synthetic": gs["synthetic"],
        "pending": [f"{p['slot']}:{p['question'] or '-'}" for p in gs["pending"]],
        "n": len(gs["items"]),
    }
