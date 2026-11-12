"""실행기 — 사례 입력으로 1차 엔진 `colab_157.pipeline.run` 을 그대로 부르고 시트 상태·줄 결과·`missing`·`origin` 을 모은다.

- AI 는 가짜 공급사(`FakeLlm`): 사례가 준 규칙(프롬프트 id·변수 → 답 글 또는 정해진 실패)으로만 답한다. 규칙이 없는 부름은
  실패로 답하고 `unexpected` 에 남긴다(시험이 0 을 단언). 지오코더도 가짜(`FakeGeocoder`) — 기본은 늘 「못 찾음」(제품 흐름의
  `NoGeocoder` 와 같음), 사례가 좌표를 줄 수 있다. 네트워크·키 0(시험은 소켓을 막고 돈다 — `tests/conftest.py`).
- 1차 엔진은 import 만 한다(`sid:MARC-ENT-683`). 엔진 객체를 바꾸는 자리는 `post_fails` 하나 — L5-1 사례가 후처리 함수만
  던지게 할 때 `pipeline` 모듈의 이름 하나를 실행 동안만 갈아 끼우고 되돌린다(엔진 파일 0줄).
"""

import contextlib
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scope3flaws import env

env.setup_paths()

import pandas as pd  # noqa: E402
from esgapi.core.llm.client import CallRequest  # noqa: E402
from esgapi.core.llm.types import CallMeta, ProviderFailed, Success, Tags, TimedOut  # noqa: E402
from esgapi.inventory.engines.scope3.colab_157 import pipeline  # noqa: E402
from esgapi.inventory.engines.scope3.colab_157.pipeline import (  # noqa: E402
    ColabRejected,
    ColabRun,
    ProcessedSheet,
    SheetOutcome,
)
from esgapi.inventory.engines.scope3.colab_157.ports import Coordinates, NotFound  # noqa: E402
from esgapi.inventory.engines.scope3.colab_157.serialize import run_to_dict  # noqa: E402
from esgapi.inventory.service.utils.scope3_state import Value, category_value  # noqa: E402
from esgapi.inventory.tools.scope3_sheets.grid import Workbook  # noqa: E402
from esgapi.inventory.tools.xlsx_reader import ReaderProblem, read_workbook  # noqa: E402

from scope3flaws import build  # noqa: E402

TAGS = Tags(tenant="tenant-synthetic", module="inventory")
ROUTE = "mock"
FAIL_5XX = "fail:5xx"
FAIL_TIMEOUT = "fail:timeout"
type Answer = str  # 답 글, 또는 FAIL_5XX·FAIL_TIMEOUT
type Rule = Callable[[str, dict[str, str]], Answer | None]  # (프롬프트 id, 변수) → 답(None = 이 규칙 아님)


def _meta(prompt: str) -> CallMeta:
    return CallMeta(call_id=str(uuid.uuid4()), prompt_id=prompt, attempts=(), total_cost_usd=0.0, warnings=())


@dataclass
class FakeLlm:
    """정해진 답·정해진 실패만 내는 가짜 공급사(포트 `LlmGateway`). 실호출 0."""

    rules: Sequence[Rule] = ()
    calls: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)

    def call(self, request: CallRequest[Any]) -> Any:
        prompt = request.prompt
        variables = {k: str(v) for k, v in request.vars.items()}
        self.calls.append((prompt, variables))
        answer = next((a for rule in self.rules if (a := rule(prompt, variables)) is not None), None)
        if answer is None:
            self.unexpected.append(f"{prompt} {variables}")
            answer = FAIL_5XX
        if answer == FAIL_5XX:
            return ProviderFailed(status=503, retryable=False, message="가상 5xx", meta=_meta(prompt))
        if answer == FAIL_TIMEOUT:
            return TimedOut(message="가상 시간 초과", meta=_meta(prompt))
        return Success(data=answer, text=answer, meta=_meta(prompt))

    def prompts(self) -> list[str]:
        return [p for p, _ in self.calls]


def answer(prompt_part: str, text: Answer) -> Rule:
    """프롬프트 id 에 `prompt_part` 가 든 부름에 늘 `text`."""
    return lambda prompt, _vars: text if prompt_part in prompt else None


@dataclass
class FakeGeocoder:
    """지명 → 좌표. `places` 에 없으면 못 찾음(제품 흐름의 `NoGeocoder` 와 같은 꼴)."""

    places: dict[str, tuple[float, float]] = field(default_factory=dict)
    queries: list[str] = field(default_factory=list)

    def geocode(self, query: str) -> Coordinates | NotFound:
        self.queries.append(query)
        for name, (lat, lon) in self.places.items():
            if name in query:
                return Coordinates(lat, lon)
        return NotFound(query)


@dataclass
class CaseRun:
    run: ColabRun
    llm: FakeLlm
    geocoder: FakeGeocoder

    @property
    def result(self) -> dict[str, Any]:
        """제품이 저장·화면에 싣는 결과 dict(`serialize.run_to_dict` — 꼴 id 는 지은 글자)."""
        return run_to_dict(self.run, "synthetic-ebs2")

    def sheet_dict(self, sheet: str) -> dict[str, Any]:
        return next(s for s in self.result["sheets"] if s["sheet"] == sheet)

    def row_dicts(self, sheet: str) -> list[dict[str, Any]]:
        return list(self.sheet_dict(sheet).get("rows", []))

    def category(self, category: str) -> Value:
        """카테고리 합(`service/utils/scope3_state.category_value` — 대시보드·잠금이 읽는 값). category 는 `C6` 꼴."""
        return category_value(self.result, category)

    def outcome(self, sheet: str) -> SheetOutcome:
        return next(o for o in self.run.sheets if o.sheet == sheet)

    def frame(self, sheet: str) -> pd.DataFrame:
        o = self.outcome(sheet)
        assert isinstance(o, ProcessedSheet), f"{sheet}: {o}"
        return o.frame.reset_index(drop=True)

    def metas(self, sheet: str) -> tuple[Any, ...]:
        o = self.outcome(sheet)
        assert isinstance(o, ProcessedSheet), f"{sheet}: {o}"
        return tuple(o.metas)

    def status(self, sheet: str) -> str:
        return self.outcome(sheet).status

    def rows(self, sheet: str, column: str, value: object) -> pd.DataFrame:
        """`column` 이 `value` 인(글이면 포함) 줄만."""
        f = self.frame(sheet)
        if isinstance(value, str):
            return f[f[column].astype(str).str.contains(value, regex=False)].reset_index(drop=True)
        return f[f[column] == value].reset_index(drop=True)


def _read(path: Path) -> Workbook:
    book = read_workbook(path.read_bytes())
    assert not isinstance(book, ReaderProblem), f"{path}: {book}"
    return book


@contextlib.contextmanager
def post_fails(names: Sequence[str]) -> Iterator[None]:
    """`pipeline` 모듈의 후처리 이름(`postprocess_category`·`postprocess_product_category`)을 실행 동안만 던지는 가짜로."""

    def boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("가상: 후처리 실패(주입)")

    saved = {n: getattr(pipeline, n) for n in names}
    try:
        for n in names:
            setattr(pipeline, n, boom)
        yield
    finally:
        for n, f in saved.items():
            setattr(pipeline, n, f)


def run_case(
    tmp: Path,
    sheets: Sequence[build.SheetCase],
    *,
    rules: Sequence[Rule] = (),
    geocoder: FakeGeocoder | None = None,
    report_year: int = 2025,
    factor_edit: Callable[[Any], None] | None = None,
) -> CaseRun:
    template = build.build_template(tmp / "synthetic_case_template.xlsx", sheets)
    book = build.build_factor_book(tmp / "factors" / "synthetic_factor_book.xlsx", factor_edit)
    llm = FakeLlm(rules)
    geo = geocoder if geocoder is not None else FakeGeocoder()
    result = pipeline.run(_read(template), _read(book), report_year, llm, geo, TAGS, ROUTE)
    assert not isinstance(result, ColabRejected), result
    return CaseRun(result, llm, geo)
