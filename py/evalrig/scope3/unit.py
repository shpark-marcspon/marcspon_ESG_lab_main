"""채점 단위 `evalrig.scope3.unit/1` 과 정답 셋 `evalrig.scope3.gold/1` 의 꼴(Scope 3 전용 — EVA 공통 꼴로 옮김은 후속 ①).

한 줄 = 열쇠 + 카테고리 + 계수 + 배출량 (+ C1·C2 는 분류·후보). 1차·2차 결과와 정답을 같은 단위로 바꿔 같은 채점기에 넣는다
(`MARC-ENT-619`).

- 열쇠는 갈래 둘(판별 칸 `by`): `sheetOrder`(대조 도구 `scope3colab.rows/1` 의 줄 열쇠 — 시트 열쇠·결과 시트 안 차례·품목 글)와
  `rowId`(1차 결과 `scope3.colab.result@1` 의 `시트!엑셀 줄`). 한 채점에서 두 쪽의 `by` 가 다르면 도구 실패(조용히 짝짓지 않는다).
- 계수 `factor = {"keyFields": {...}, "value": "<십진 글자>"|None}`. keyFields 는 「같은 계수」를 가르는 칸(카테고리마다
  `FACTOR_KEY_FIELDS`), 그 칸이 결과에 없는 카테고리는 빈 객체(값만 견줌). `factor` 가 None 이면 계수를 쓰지 않는 줄(C15).
- 부재는 판별 유니온으로 남긴다: 정답 줄의 `gold` 칸 `excluded`(사람이 뺌)·`unreviewed`(검토 안 함), 분류 칸의 `absent`
  (`{"status": "absent", "reason": …}`), 카테고리의 `absent`. 0·빈 배열·None 하나로 접지 않는다.
"""

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

UNIT_SCHEMA = "evalrig.scope3.unit/1"
GOLD_SCHEMA = "evalrig.scope3.gold/1"
SCORE_SCHEMA = "evalrig.scope3.score/1"

# Scope 3 카테고리 열넷(C10 은 코랩·템플릿에 없다)
CATEGORIES: tuple[str, ...] = ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9", "C11", "C12", "C13", "C14", "C15")
CLASSIFIED: frozenset[str] = frozenset({"C1", "C2"})  # 분류·후보(hit@3)가 있는 카테고리
DISTANCE = "Distance"

# 카테고리(·갈래) → 「같은 계수」를 가르는 칸 = 그 시트·갈래의 `scope3colab.rows.fields_of(sheet, branch)` 칸 가운데 계수 이름·
# 단위 칸(critic 1 B2 — 2차가 물려받는 채점 단위 계약). 표에 없는 (카테고리, 갈래)는 값만 견준다: C4·C9(두 갈래)·C15.
# 각 칸이 `fields_of` 안에 있는지는 시험 `test_factor_key_fields_within_fields_of` 가 단언한다.
FACTOR_KEY_FIELDS: dict[tuple[str, str | None], tuple[str, ...]] = {
    ("C1", None): ("대표EF",),
    ("C2", None): ("대표EF",),
    ("C3", None): ("EF매핑명", "EF단위"),
    ("C11", None): ("EF매핑명", "EF단위"),
    ("C14", None): ("EF매핑명", "EF단위"),
    ("C8", None): ("EF단위",),
    ("C13", None): ("EF단위",),
    ("C5", None): ("처리방법_정규화", "바이오매스 구분"),
    ("C12", None): ("바이오매스 구분",),
    ("C6", None): ("EF_DB", "NAICS"),  # C6 지출 갈래
    ("C6", DISTANCE): ("정규화이동수단",),
    ("C7", None): ("정규화이동수단",),
}


def factor_key_fields(category: str, branch: str | None) -> tuple[str, ...]:
    return FACTOR_KEY_FIELDS.get((category, branch), ())


class ToolFailure(Exception):
    """도구 실패(CLI 종료 2) — 꼴 어긋남·짝지을 수 없음·출력 자리 어긋남."""


GoldMark = Literal["excluded", "unreviewed"]


@dataclass(frozen=True)
class Key:
    by: Literal["sheetOrder", "rowId"]
    sheet: str | None = None
    row: int | None = None
    item: str | None = None
    row_id: str | None = None

    def __post_init__(self) -> None:
        if self.by == "sheetOrder":
            if self.sheet is None or self.row is None or self.item is None or self.row_id is not None:
                raise ToolFailure("sheetOrder 열쇠는 sheet·row·item 만 갖는다")
        elif self.by == "rowId":
            if self.row_id is None or (self.sheet, self.row, self.item) != (None, None, None):
                raise ToolFailure("rowId 열쇠는 rowId 만 갖는다")
        else:
            raise ToolFailure(f"모르는 열쇠 갈래 {self.by!r}")

    @property
    def ident(self) -> tuple[Any, ...]:
        return (self.sheet, self.row, self.item) if self.by == "sheetOrder" else (self.row_id,)

    def to_json(self) -> dict[str, Any]:
        if self.by == "sheetOrder":
            return {"by": "sheetOrder", "sheet": self.sheet, "row": self.row, "item": self.item}
        return {"by": "rowId", "rowId": self.row_id}

    @classmethod
    def from_json(cls, data: Any) -> "Key":
        if not isinstance(data, dict):
            raise ToolFailure("열쇠가 객체가 아니다")
        by = data.get("by")
        if by == "sheetOrder":
            return cls("sheetOrder", str(data["sheet"]), int(data["row"]), str(data["item"]))
        if by == "rowId":
            return cls("rowId", row_id=str(data["rowId"]))
        raise ToolFailure(f"모르는 열쇠 갈래 {by!r}")


@dataclass(frozen=True)
class Factor:
    key_fields: dict[str, str | None]
    value: str | None

    def to_json(self) -> dict[str, Any]:
        return {"keyFields": dict(self.key_fields), "value": self.value}

    @classmethod
    def from_json(cls, data: Any) -> "Factor | None":
        if data is None:
            return None
        if not isinstance(data, dict) or not isinstance(data.get("keyFields"), dict):
            raise ToolFailure("factor 는 keyFields 객체와 value 를 갖는다")
        return cls({str(k): (None if v is None else str(v)) for k, v in data["keyFields"].items()}, decimal_text(data.get("value")))


ABSENT_NAICS = {"status": "absent", "reason": "result@1 의 구매 줄에 NAICS 코드 칸이 없다"}
# rows/1 에는 묶음·NAICS 칸이 없다(scope3colab `rows.py` `FIELDS`) — 분류는 통째로 absent. 다른 칸에서 끌어오지 않는다(critic 1 B1).
ROWS1_NO_CLASSIFICATION = {"status": "absent", "reason": "rows/1 에 묶음·NAICS 칸 없음"}


@dataclass(frozen=True)
class Unit:
    key: Key
    category: str
    factor: Factor | None
    co2e: str | None
    branch: str | None = None
    candidates: tuple[str, ...] | None = None  # C1·C2 만 — 대표 + 추천(겹침 뺌, 빈 자리 뺌, 길이 0~5)
    classification: dict[str, Any] | None = None  # C1·C2 만 — {"group": 글|absent, "naics": 글|absent} 또는 통째 absent
    gold: GoldMark | None = None  # 정답 셋에서만: excluded·unreviewed(그 밖은 None = 정답 있음)
    source: str | None = None  # 정답 계수의 자리(gold-edits: applied·recommended:r·custom) — 정답 셋에서만

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ToolFailure(f"모르는 카테고리 {self.category!r}")
        if self.category in CLASSIFIED:
            if self.candidates is not None and len(self.candidates) > 5:
                raise ToolFailure("후보는 다섯까지")
        elif self.candidates is not None or self.classification is not None:
            raise ToolFailure(f"{self.category}: 후보·분류는 C1·C2 만")

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"key": self.key.to_json(), "category": self.category}
        if self.branch is not None:
            out["branch"] = self.branch
        if self.gold is not None:
            out["gold"] = self.gold
            return out
        out["factor"] = None if self.factor is None else self.factor.to_json()
        out["co2e"] = self.co2e
        if self.candidates is not None:
            out["candidates"] = list(self.candidates)
        if self.classification is not None:
            out["classification"] = dict(self.classification)
        if self.source is not None:
            out["source"] = self.source
        return out

    @classmethod
    def from_json(cls, data: Any) -> "Unit":
        if not isinstance(data, dict):
            raise ToolFailure("단위가 객체가 아니다")
        try:
            gold = data.get("gold")
            if gold not in (None, "excluded", "unreviewed"):
                raise ToolFailure(f"모르는 gold 갈래 {gold!r}")
            cands = data.get("candidates")
            return cls(
                key=Key.from_json(data["key"]),
                category=str(data["category"]),
                factor=None if gold else Factor.from_json(data.get("factor")),
                co2e=None if gold else decimal_text(data.get("co2e")),
                branch=data.get("branch"),
                candidates=None if cands is None else tuple(str(c) for c in cands),
                classification=data.get("classification"),
                gold=gold,
                source=data.get("source"),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ToolFailure(f"단위 꼴 어긋남 — {error!r}") from error


def decimal_text(value: Any) -> str | None:
    """수 → `Decimal` 글. None·빈 글은 None. 수가 아니면 도구 실패. (repr(float) 지수 표기도 받는다)"""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ToolFailure(f"수 칸에 참거짓 {value!r}")
    if isinstance(value, float):
        if value != value:
            return None
        return str(Decimal(repr(value)))
    if isinstance(value, int | Decimal):
        return str(Decimal(value))
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    try:
        return str(Decimal(text))
    except InvalidOperation as error:
        raise ToolFailure(f"수 칸에 수가 아닌 글 {value!r}") from error


def same_decimal(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return Decimal(a) == Decimal(b)


def dedup(titles: list[str | None]) -> tuple[str, ...]:
    """빈 자리를 빼고 겹친 제목을 앞의 것만 남긴다(차례 유지)."""
    seen: list[str] = []
    for t in titles:
        if t is None or not str(t).strip() or t in seen:
            continue
        seen.append(t)
    return tuple(seen)


# ── 정답 셋 ───────────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GoldSet:
    meta: dict[str, Any]
    units: tuple[Unit, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        by = self.meta.get("keyBy")
        if by not in ("sheetOrder", "rowId"):
            raise ToolFailure(f"meta.keyBy 가 sheetOrder·rowId 가 아니다: {by!r}")
        cats = self.meta.get("categories")
        if not isinstance(cats, dict) or set(cats) != set(CATEGORIES):
            raise ToolFailure("meta.categories 는 열넷을 다 갖는다")
        seen: set[tuple[Any, ...]] = set()
        for u in self.units:
            if u.key.by != by:
                raise ToolFailure(f"정답 단위의 열쇠 갈래 {u.key.by} 가 meta.keyBy {by} 와 다르다")
            if u.key.ident in seen:
                raise ToolFailure(f"정답 열쇠 {u.key.ident} 가 둘 이상")
            seen.add(u.key.ident)
            if cats[u.category].get("status") != "present":
                raise ToolFailure(f"{u.category}: absent 카테고리에 줄이 있다")

    @property
    def key_by(self) -> str:
        return str(self.meta["keyBy"])

    def to_json(self) -> dict[str, Any]:
        return {"schema": GOLD_SCHEMA, "meta": self.meta, "units": [u.to_json() for u in self.units]}

    @classmethod
    def from_json(cls, data: Any) -> "GoldSet":
        if not isinstance(data, dict) or data.get("schema") != GOLD_SCHEMA:
            raise ToolFailure(f"정답 셋 schema 가 {GOLD_SCHEMA!r} 가 아니다")
        return cls(dict(data["meta"]), tuple(Unit.from_json(u) for u in data.get("units") or ()))


def categories_meta(units: list[Unit], absent: dict[str, str]) -> dict[str, dict[str, Any]]:
    """열넷 각각 present(줄 수) | absent(까닭). 줄이 없고 absent 까닭도 안 받은 카테고리는 「입력에 시트 없음」."""
    out: dict[str, dict[str, Any]] = {}
    for cat in CATEGORIES:
        n = sum(1 for u in units if u.category == cat)
        if cat in absent:
            if n:
                raise ToolFailure(f"{cat}: absent 로 적었는데 줄 {n}")
            out[cat] = {"status": "absent", "reason": absent[cat]}
        elif n:
            out[cat] = {"status": "present", "rows": n}
        else:
            out[cat] = {"status": "absent", "reason": "입력에 그 카테고리 줄이 없다"}
    return out


def fingerprint(path: Path) -> dict[str, Any]:
    """sha256 과 바이트 수만(scope3colab `file_fingerprint` 꼴) — 경로·파일 이름 글자 0(받은 파일 이름에 기업명)."""
    body = path.read_bytes()
    return {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as error:
        raise ToolFailure(f"JSON 을 못 읽었다 — {type(error).__name__}") from error


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")


def require_out_dir(path: Path) -> Path:
    """출력 자리는 `out/` 아래만(받은 값에서 지은 것은 git 밖). 경로 마디에 `out` 이 없으면 도구 실패."""
    resolved = path.resolve()
    if "out" not in resolved.parts:
        raise ToolFailure("출력 경로가 out/ 아래가 아니다")
    return resolved
