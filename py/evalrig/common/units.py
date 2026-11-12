"""채점 단위 꼴 `evalrig.unit@1` — 채점기 입력은 이 꼴 하나다.

1차 산출 → 단위 = 모듈의 `from_v1`, 2차 result@2 → 단위 = `from_v2`. 채점기는 `origin.engine`
(`v1|v2`)을 보지 않는다 — 같은 정답·같은 셈.

칸:
- `module`·`unit`(단위 이름@판, 예 `iro.topic-branch@1`)
- `key` — 한 구획(변환기 한 번이 낸 목록) 안에서 유일한 문자열. 정답 항목과 짝짓는 열쇠.
- `group` — 묶음 열쇠(토픽·잎·쪽 등). 거시 평균의 단위.
- `payload` — 모듈 칸(dict).
- `sources[]` — `{kind: web|doc|data|ref|human, ref, check}`. `check` 는 검사기가 채우는 dict 또는 null.
- `status` — `ok | missing | failed`(산출이 비었으면 missing, 깨졌으면 failed).
- `origin` — `{engine: v1|v2, schema(원 결과 판), run(실행 자리), converter(이름@판)}`.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

SCHEMA_ID = "evalrig.unit@1"
STATUSES = ("ok", "missing", "failed")
SOURCE_KINDS = ("web", "doc", "data", "ref", "human")
ENGINES = ("v1", "v2")


@dataclass(frozen=True)
class Origin:
    engine: str
    schema: str
    run: str
    converter: str

    def __post_init__(self) -> None:
        if self.engine not in ENGINES:
            raise ValueError(f"origin.engine {self.engine!r} ∉ {ENGINES}")
        for k in ("schema", "run", "converter"):
            if not isinstance(getattr(self, k), str) or not getattr(self, k):
                raise ValueError(f"origin.{k} 는 빈 글이 아닌 문자열")
        if "@" not in self.converter:
            raise ValueError("origin.converter 는 이름@판")

    def to_dict(self) -> dict[str, str]:
        return {"engine": self.engine, "schema": self.schema, "run": self.run, "converter": self.converter}


@dataclass(frozen=True)
class Source:
    kind: str
    ref: str
    check: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.kind not in SOURCE_KINDS:
            raise ValueError(f"source.kind {self.kind!r} ∉ {SOURCE_KINDS}")
        if not isinstance(self.ref, str):
            raise ValueError("source.ref 는 문자열")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "ref": self.ref, "check": copy.deepcopy(self.check)}


@dataclass(frozen=True)
class Unit:
    module: str
    unit: str
    key: str
    group: str
    payload: dict[str, Any]
    origin: Origin
    status: str = "ok"
    sources: tuple[Source, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key:
            raise ValueError("unit.key 는 빈 글이 아닌 문자열")
        if not isinstance(self.group, str):
            raise ValueError("unit.group 은 문자열")
        if "@" not in self.unit:
            raise ValueError("unit.unit 은 이름@판")
        if self.status not in STATUSES:
            raise ValueError(f"unit.status {self.status!r} ∉ {STATUSES}")
        if not isinstance(self.payload, dict):
            raise ValueError("unit.payload 는 dict")
        if not isinstance(self.origin, Origin):
            raise ValueError("unit.origin 은 Origin")
        object.__setattr__(self, "sources", tuple(self.sources))
        for s in self.sources:
            if not isinstance(s, Source):
                raise ValueError("unit.sources 는 Source 의 목록")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_ID,
            "module": self.module,
            "unit": self.unit,
            "key": self.key,
            "group": self.group,
            "payload": copy.deepcopy(self.payload),
            "sources": [s.to_dict() for s in self.sources],
            "status": self.status,
            "origin": self.origin.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Unit":
        if d.get("schema", SCHEMA_ID) != SCHEMA_ID:
            raise ValueError(f"schema {d.get('schema')!r} ≠ {SCHEMA_ID}")
        return cls(
            module=d["module"],
            unit=d["unit"],
            key=d["key"],
            group=d["group"],
            payload=copy.deepcopy(d["payload"]),
            sources=tuple(Source(**s) for s in d.get("sources", [])),
            status=d["status"],
            origin=Origin(**d["origin"]),
        )


def check_units(units: Iterable[Unit]) -> list[Unit]:
    """한 구획의 단위 목록 검사 — `key` 중복은 ValueError. 목록을 돌려준다."""
    out = list(units)
    seen: set[str] = set()
    for u in out:
        if not isinstance(u, Unit):
            raise ValueError("Unit 이 아닌 것이 섞임")
        if u.key in seen:
            raise ValueError(f"unit.key 중복: {u.key!r}")
        seen.add(u.key)
    return out


def by_key(units: Iterable[Unit]) -> dict[str, Unit]:
    """key → Unit(중복 검사 포함)."""
    return {u.key: u for u in check_units(units)}


def by_group(units: Iterable[Unit]) -> dict[str, list[Unit]]:
    """group → 단위 목록(목록 차례 유지)."""
    out: dict[str, list[Unit]] = {}
    for u in check_units(units):
        out.setdefault(u.group, []).append(u)
    return out


class Converter(Protocol):
    """원 결과 → 채점 단위. 모듈의 `from_v1`·`from_v2` 가 이 꼴을 따른다.

    돌려주기 전에 `check_units` 를 통과해야 한다(key 유일).
    """

    name: str  # 이름@판 — origin.converter 에 들어감

    def __call__(self, result: Any, **ctx: Any) -> list[Unit]: ...


def converter(name: str):
    """함수를 Converter 로 표시하는 장식자 — `fn.name = name` 을 달고, 돌려준 목록을 `check_units` 한다.

    >>> @converter("iro.result-json@1")
    ... def conv(result, **ctx): ...
    """
    if "@" not in name:
        raise ValueError("converter 이름은 이름@판")

    def deco(fn):
        def wrapped(result: Any, **ctx: Any) -> list[Unit]:
            return check_units(fn(result, **ctx))

        wrapped.name = name  # type: ignore[attr-defined]
        wrapped.__name__ = fn.__name__
        wrapped.__doc__ = fn.__doc__
        wrapped.__wrapped__ = fn  # type: ignore[attr-defined]
        return wrapped

    return deco
