"""클라이언트 짓기: esg-app core/llm 의 build_client 를 그대로 쓰고, 공급사 자리에 기록기(Recorder)를 끼운다.

모드 셋:
  mock  공급사 = 기록기(OpenAIProvider(None).build_request 로 요청 몸을 짓고 적은 뒤, 모듈의 대본 답을 돌려준다).
        키·네트워크 0. 하네스가 끝까지 도는지와 호출 수를 본다
  dry   build_client(assemble_only=True) — 실제 OpenAI 어댑터가 키 없이 몸만 짓고 「클라이언트 없음」으로 돌린다
        (공급사 전 거름 LlmConfigError 는 그 앞에서 난다 — 그것을 적는다). 몸은 기록기가 적는다
  live  build_client(키는 LlmSettings 에 명시, 싱크는 LabSink, limits=[BudgetLimit(Scope(), 5.0)]) — 마크스폰 키

★ esg-app 객체를 바꾸는 자리는 `patch_tool_models` 하나뿐이다(감독 판단 ①). 판 luna 일 때만 부르고 보고에 적는다.
"""

import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from miglab import env, labmodels

env.setup_paths()

from esgapi.config.env import LlmSettings  # noqa: E402
from esgapi.core.llm import client as client_mod  # noqa: E402
from esgapi.core.llm.budget import BudgetLimit  # noqa: E402
from esgapi.core.llm.client import LlmClient  # noqa: E402
from esgapi.core.llm.cost import round_usd  # noqa: E402
from esgapi.core.llm.errors import LlmConfigError  # noqa: E402
from esgapi.core.llm.log import CallRecord, json_line  # noqa: E402
from esgapi.core.llm.setup import build_client  # noqa: E402
from esgapi.core.llm.types import (  # noqa: E402
    Completed,
    ProviderRequest,
    ProviderResponse,
    Scope,
    Usage,
)
from esgapi.core.resources import llm as llm_adapters  # noqa: E402

MODES: Final = ("mock", "dry", "live")
TICKET_CAP_USD: Final = 5.0
"""이 티켓 상한(발주서 §3). calls.jsonl 의 live 줄 합 + 이 프로세스의 지출이 닿으면 core 가 공급사 전에 막는다."""
KEY_ENV: Final = "OPENAI_API_KEY"

PATCHES: list[str] = []
"""이 프로세스에서 esg-app 객체를 바꾼 것(보고에 그대로)."""


ORIGINAL_TOOL_MODELS: Final = client_mod.TOOL_MODELS
PATCH_NOTE: Final = "esgapi.core.llm.client.TOOL_MODELS |= {'gpt-6-luna'} (감독 판단 ①(가), 하네스 프로세스 안, 실행 하나 동안)"


def patch_tool_models() -> None:
    """감독 판단 ①(가): 도구 고리 허용 목록에 luna 를 더한다 — `client.TOOL_MODELS` 이름 재묶음(client.py:1064 가 부를
    때마다 모듈 전역을 읽는다). 파일은 0줄. 실행이 끝나면 `restore_tool_models` 로 되돌린다(다음 판에 새지 않게)."""
    client_mod.TOOL_MODELS = ORIGINAL_TOOL_MODELS | frozenset({labmodels.LUNA})
    PATCHES.append(PATCH_NOTE)


def restore_tool_models() -> None:
    client_mod.TOOL_MODELS = ORIGINAL_TOOL_MODELS
    PATCHES.clear()


# ── 공급사 자리의 기록기 ───────────────────────────────────────────────────────────────────────


type Responder = Callable[[ProviderRequest], ProviderResponse]


def fallback_responder(request: ProviderRequest) -> ProviderResponse:
    """대본이 없는 모듈의 모의 답: 빈 JSON 객체(스키마가 있으면 대개 어긋남 → 흐름의 실패 갈래가 돈다)."""
    return Completed(text="{}", usage=_mock_usage(request, 20), model_id=request.api_model_id)


def _mock_usage(request: ProviderRequest, output_tokens: int) -> Usage:
    """가상 사용량: 입력 = 글자 수 / 2(어림), 출력은 준 값. 모의 비용은 실비가 아니다."""
    chars = sum(len(s.text) for s in request.system) + sum(len(getattr(p, "text", "")) for p in request.user)
    return Usage(input_tokens=max(1, chars // 2), output_tokens=output_tokens, cache_read_tokens=0, cache_write_tokens=0)


@dataclass
class Recorder:
    """openai 자리의 공급사. 요청 몸(실제 OpenAI 어댑터의 build_request)을 bodies 에 적고, inner 나 responder 로 답한다."""

    inner: Any
    responder: Responder | None = None
    bodies: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    seconds: list[float] = field(default_factory=list[float])

    @property
    def name(self) -> str:
        return "openai"

    def build_request(self, request: ProviderRequest) -> object:
        return self.inner.build_request(request)

    def invoke(self, request: ProviderRequest, options: Any) -> ProviderResponse:
        self.bodies.append(dict(self.inner.build_request(request)))
        started = time.monotonic()
        try:
            if self.responder is not None:
                return self.responder(request)
            return self.inner.invoke(request, options)
        finally:
            self.seconds.append(round(time.monotonic() - started, 3))


# ── 싱크: core 기록 줄 + 랩 칸 ─────────────────────────────────────────────────────────────────


def ledger_total(path: Path | None = None) -> float:
    """calls.jsonl 의 live 줄 costUsd 합(core 셈)."""
    target = path or env.CALLS
    if not target.exists():
        return 0.0
    total = 0.0
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("lab", {}).get("mode") == "live" and row.get("costUsd") is not None:
            total += float(row["costUsd"])
    return round_usd(total)


class LabSink:
    """core 의 CallLogSink. 줄마다 `{"lab": {...}, **CallRecord.to_json()}` 를 path 에 덧붙인다.

    spent(scope) = 앞 실행들의 live 합(장부, 시작 때 한 번 읽음 — scope 무관한 전역 몫) + 이 객체가 본 줄의 합.
    그래서 BudgetLimit(Scope(), 5.0) 이 티켓 전체 누적의 상한이 된다(프로세스를 넘어). ★ 무효 조건: 같은 때 두
    프로세스가 돌면 서로를 못 센다 — 하네스는 한 번에 하나만 돌린다.
    """

    def __init__(self, path: Path, lab: Mapping[str, Any], *, prior_usd: float = 0.0) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lab = dict(lab)
        self.prior_usd = prior_usd
        self.records: list[CallRecord] = []

    def record(self, record: CallRecord) -> None:
        self.records.append(record)
        line = {"lab": self.lab, **record.to_json()}
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json_line(line) + "\n")

    def spent(self, scope: Scope) -> float:
        own = sum(r.cost_usd for r in self.records if r.cost_usd is not None and scope.matches(r.tenant, r.module))
        return round_usd(self.prior_usd + own)

    def run_usd(self) -> float:
        return round_usd(sum(r.cost_usd or 0.0 for r in self.records))


# ── 클라이언트를 감싸 요청·거름을 적는 대역 ──────────────────────────────────────────────────


@dataclass
class Event:
    kind: str  # call | call_tools
    prompt: str
    route: str
    estimate_usd: float | None
    estimate_input_tokens: int | None
    max_output_tokens: int | None
    error: str | None = None


class ClientProxy:
    """LlmClient 를 감싼다. call·call_tools 마다 그 요청의 dry-run 위끝을 재고(공급사 0), 공급사 전 거름
    (LlmConfigError)을 적고 다시 던진다. 그 밖은 안의 클라이언트로 그대로."""

    def __init__(self, inner: LlmClient) -> None:
        self.inner = inner
        self.events: list[Event] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def _estimate(self, kind: str, request: Any) -> tuple[float | None, int | None, int | None, str | None]:
        try:
            report = self.inner.dry_run_tools(request) if kind == "call_tools" else self.inner.dry_run(request)
        except LlmConfigError as error:
            return None, None, None, f"LlmConfigError: {error}"
        bound = report.tool_loop.loop_upper_bound_usd if report.tool_loop else report.estimate.worst_case_usd
        return bound, report.estimate.input_tokens, report.estimate.max_output_tokens, None

    def call(self, request: Any) -> Any:
        usd, tokens, out, error = self._estimate("call", request)
        self.events.append(Event("call", request.prompt, request.route, usd, tokens, out, error))
        return self.inner.call(request)

    def call_tools(self, request: Any, execute: Any) -> Any:
        usd, tokens, out, error = self._estimate("call_tools", request)
        self.events.append(Event("call_tools", request.prompt, request.route, usd, tokens, out, error))
        return self.inner.call_tools(request, execute)


# ── 짓기 ──────────────────────────────────────────────────────────────────────────────────


@dataclass
class Built:
    client: ClientProxy
    recorder: Recorder
    sink: LabSink
    models_file: Path


def build(
    *,
    mode: str,
    variant: str,
    lab: Mapping[str, Any],
    calls_path: Path,
    responder: Responder | None = None,
    cap_usd: float = TICKET_CAP_USD,
) -> Built:
    """모드·판의 클라이언트. live 만 키를 읽는다(환경 OPENAI_API_KEY — 값은 어디에도 적지 않는다)."""
    if mode not in MODES:
        raise ValueError(f"모드는 {MODES}: {mode}")
    models_file = labmodels.write_lab_models(variant, calls_path.parent)
    if variant == "luna":
        patch_tool_models()
    key = None
    if mode == "live":
        key = os.environ.get(KEY_ENV)
        if not key:
            raise SystemExit(f"{KEY_ENV} 가 환경에 없다 — `set -a; . ~/.config/marcspon-openai.env; set +a` 뒤에 돈다")
    settings = LlmSettings(openai_api_key=key, models_file=str(models_file), log_sink="jsonl", jsonl_path=str(calls_path))
    prior = ledger_total() if mode == "live" else 0.0
    sink = LabSink(calls_path, {**lab, "mode": mode, "variant": variant}, prior_usd=prior)
    holder: dict[str, Recorder] = {}

    def make(name: str, *, api_key: str | None = None, assemble_only: bool = False) -> Any:
        if name != "openai":
            return llm_adapters.make(name, api_key=api_key, assemble_only=assemble_only)
        inner = llm_adapters.make(name, api_key=api_key, assemble_only=assemble_only or mode != "live")
        holder["openai"] = Recorder(inner=inner, responder=responder if mode == "mock" else None)
        return holder["openai"]

    client = build_client(
        settings,
        limits=[BudgetLimit(scope=Scope(), limit_usd=cap_usd)],
        assemble_only=mode != "live",
        sink=sink,
        make=make,
    )
    proxy = ClientProxy(client)
    # 기록기는 처음 부를 때 만들어진다 — 미리 꺼내 둔다(키가 없어도 assemble_only 라 선다).
    client.provider("openai")
    return Built(client=proxy, recorder=holder["openai"], sink=sink, models_file=models_file)
