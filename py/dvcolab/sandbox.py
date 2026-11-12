"""코랩 셀을 빈 이름공간 하나에서 돌리는 자리와 가짜들(데이터 검증 코랩 몫).

- `google.colab`(userdata·drive·files)과 `requests` 를 `sys.modules` 에 가짜로 끼운다. 진짜는 깔지 않는다.
  셀 7 이 import 때 `userdata.get('OPENAI_API_KEY')` 를 부르므로 가짜 글을 돌려준다(키 아님).
- 가짜 `requests.post` 는 Responses API 자리를 받는다: 보낸 몸(system·user 글, 모델, 도구 이름)을 적고, 짜 둔 답
  (`answers.synthetic.json`, 도구 호출 없음·글 하나)을 차례대로 Responses 꼴로 돌려준다.
- 고리 모드(DV1b): `start_loop(script)` 뒤의 부름은 짜 둔 답 대신 그 고리의 답(함수 호출·글)을 Responses 꼴로 차례대로
  돌려주고, 받은 몸의 `input` 에서 `function_call_output` 을 읽어 실행된 (이름, 인자 글)·결과 글을 적는다(`end_loop`).
  고리의 부름은 `posts`(셀 10·14·18 의 짜 둔 답 몫)에 세지 않는다.
- 소켓을 막는다(`socket.socket`·`create_connection`·`getaddrinfo`). env 의 키를 실행 동안 지운다.
- 하네스가 멈춰야 하는 일(네트워크, 답 모자람, 막은 것을 씀)은 `HarnessAbort`(BaseException)로 던진다 — 코랩의
  `except Exception` 이 삼키지 못하게(scope3colab 과 같은 까닭). 던진 것은 `Sandbox.aborts` 에도 적는다.
"""

import builtins
import contextlib
import io
import json
import os
import socket
import sys
import types
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dvcolab.cells import CodeCell

FAKE_KEY = "synthetic-not-a-key"
_SCRUB_ENV = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_BASE_URL", "HTTP_PROXY", "HTTPS_PROXY")
_FAKE_MODULES = ("google", "google.colab", "requests")
RESPONSES_URL = "https://api.openai.com" + "/v1/responses"  # 코랩 셀 10·14·18 이 보내는 자리


class HarnessAbort(BaseException):
    """하네스를 멈추는 일. 코랩의 `except Exception` 을 지나간다."""


class NetworkBlocked(HarnessAbort):
    pass


class AnswersExhausted(HarnessAbort):
    pass


class BlockedUse(HarnessAbort):
    pass


class CellFailed(Exception):
    def __init__(self, cell: int, error: BaseException) -> None:
        super().__init__(f"셀 {cell}: {type(error).__name__}: {error}")
        self.cell = cell
        self.error = error


def load_answers(path: Path) -> list[str]:
    """짜 둔 답 JSON: `{"synthetic": true, "answers": [{"for": …, "text": …}, …]}` — 글만 차례대로."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("synthetic") is not True:
        raise ValueError(f"{path}: 짜 둔 답 파일은 `\"synthetic\": true` 를 달아야 한다")
    return [a["text"] for a in data["answers"]]


@dataclass
class Sandbox:
    """가짜들이 함께 쓰는 기록."""

    answers: list[str]
    posts: list[dict[str, Any]] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    aborts: list[str] = field(default_factory=list)

    loop_script: list[dict[str, Any]] | None = None
    loop_record: dict[str, Any] = field(default_factory=dict)
    _loop_calls: dict[str, list[str]] = field(default_factory=dict)

    def start_loop(self, script: list[dict[str, Any]]) -> None:
        self.loop_script = list(script)
        self.loop_record = {"executed": [], "outputs": [], "providerCalls": 0, "lastHadCalls": False}
        self._loop_calls = {}

    def end_loop(self) -> dict[str, Any]:
        left = len(self.loop_script or [])
        self.loop_script = None
        return {**self.loop_record, "unusedAnswers": left}

    def _loop_post(self, body: Any) -> Any:
        record = self.loop_record
        for item in body.get("input") or []:
            if isinstance(item, dict) and item.get("type") == "function_call_output":
                record["executed"].append(self._loop_calls[item["call_id"]])
                record["outputs"].append(item["output"])
        if not self.loop_script:
            raise self.abort(AnswersExhausted(f"고리 부름 {record['providerCalls'] + 1}: 짜 둔 고리 답이 모자라다"))
        entry = self.loop_script.pop(0)
        record["providerCalls"] += 1
        output: list[dict[str, Any]] = []
        if "text" in entry:
            output.append({"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": entry["text"]}]})
        for name, arguments in entry.get("calls", []):
            call_id = f"synthetic-call-{len(self._loop_calls) + 1}"
            self._loop_calls[call_id] = [name, arguments]
            output.append({"type": "function_call", "id": f"fc-{call_id}", "call_id": call_id, "name": name, "arguments": arguments})
        record["lastHadCalls"] = bool(entry.get("calls"))
        payload = {"id": f"synthetic-loop-response-{record['providerCalls']}", "output": output, "usage": {}}
        return types.SimpleNamespace(ok=True, status_code=200, text="", json=lambda: payload)

    def abort(self, exc: HarnessAbort) -> HarnessAbort:
        self.aborts.append(f"{type(exc).__name__}: {exc}")
        return exc

    # ── requests.post(Responses API) ──
    def post(self, url: str, headers: Any = None, json: Any = None, timeout: Any = None, **kw: Any) -> Any:  # noqa: A002
        n = len(self.posts)
        if url != RESPONSES_URL:
            raise self.abort(BlockedUse(f"requests.post({url!r}) — Responses 자리 밖"))
        if self.loop_script is not None:
            return self._loop_post(json or {})
        if n >= len(self.answers):
            raise self.abort(AnswersExhausted(f"부름 {n + 1}: 짜 둔 답이 {len(self.answers)}개뿐"))
        body = json or {}
        messages = body.get("input") or []
        by_role = {m.get("role"): m.get("content") for m in messages if isinstance(m, dict) and "role" in m}
        self.posts.append(
            {
                "n": n + 1,
                "model": body.get("model"),
                "tools": [t.get("name") or t.get("type") for t in body.get("tools") or []],
                "previous_response_id": body.get("previous_response_id"),
                "system": by_role.get("system"),
                "user": by_role.get("user"),
            }
        )
        text = self.answers[n]
        payload = {
            "id": f"synthetic-response-{n + 1}",
            "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}],
            "usage": {},
        }
        return types.SimpleNamespace(ok=True, status_code=200, text="", json=lambda: payload)


def _fake_modules(box: Sandbox) -> dict[str, types.ModuleType]:
    google = types.ModuleType("google")
    colab = types.ModuleType("google.colab")
    google.colab = colab  # type: ignore[attr-defined]

    def userdata_get(name: str) -> str:
        box.events.append(f"userdata.get({name})")
        return FAKE_KEY

    def blocked(what: str) -> Any:
        def _raise(*a: Any, **k: Any) -> Any:
            raise box.abort(BlockedUse(f"{what} 를 불렀다(가짜로 막음)"))

        return _raise

    colab.userdata = types.SimpleNamespace(get=userdata_get)  # type: ignore[attr-defined]
    colab.drive = types.SimpleNamespace(mount=blocked("drive.mount"))  # type: ignore[attr-defined]
    colab.files = types.SimpleNamespace(upload=blocked("files.upload"), download=blocked("files.download"))  # type: ignore[attr-defined]

    requests = types.ModuleType("requests")

    class HTTPError(Exception):
        pass

    requests.HTTPError = HTTPError  # type: ignore[attr-defined]
    requests.post = box.post  # type: ignore[attr-defined]
    requests.get = blocked("requests.get")  # type: ignore[attr-defined]
    return {"google": google, "google.colab": colab, "requests": requests}


@contextlib.contextmanager
def socket_blocked(box: Sandbox | None = None) -> Iterator[None]:
    """소켓을 막는다. 부르면 `NetworkBlocked`."""
    saved = (socket.socket, socket.create_connection, socket.getaddrinfo)

    def deny(what: str) -> Any:
        def _raise(*a: Any, **k: Any) -> Any:
            exc = NetworkBlocked(f"{what} 를 불렀다(소켓 막힘)")
            if box is not None:
                box.abort(exc)
            raise exc

        return _raise

    socket.socket = deny("socket.socket")  # type: ignore[assignment,misc]
    socket.create_connection = deny("socket.create_connection")  # type: ignore[assignment]
    socket.getaddrinfo = deny("socket.getaddrinfo")  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket, socket.create_connection, socket.getaddrinfo = saved  # type: ignore[misc]


@contextlib.contextmanager
def fakes_installed(box: Sandbox) -> Iterator[None]:
    saved = {name: sys.modules.get(name) for name in _FAKE_MODULES}
    sys.modules.update(_fake_modules(box))
    try:
        yield
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


@contextlib.contextmanager
def scrubbed_env() -> Iterator[None]:
    saved = {k: os.environ.get(k) for k in _SCRUB_ENV}
    for k in _SCRUB_ENV:
        os.environ.pop(k, None)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def working_dir(path: Path) -> Iterator[None]:
    before = Path.cwd()
    path.mkdir(parents=True, exist_ok=True)
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(before)


@contextlib.contextmanager
def sandboxed(box: Sandbox, workdir: Path) -> Iterator[None]:
    """가짜·소켓 막힘·환경 지움·작업 폴더를 한꺼번에."""
    with scrubbed_env(), fakes_installed(box), socket_blocked(box), working_dir(workdir):
        yield


def run_cell(cell: CodeCell, namespace: dict[str, Any], stdout: io.TextIOBase) -> None:
    """셀 하나를 이름공간에서 돌린다. 실패하면 `CellFailed`(건너뛰기 목록 밖의 실패 — 도구 실패)."""
    namespace.setdefault("__name__", "__main__")
    namespace.setdefault("__builtins__", builtins)
    with contextlib.redirect_stdout(stdout):
        try:
            code = compile(cell.source, f"<colab cell {cell.number}>", "exec")
            exec(code, namespace)  # noqa: S102 — 코랩 코드를 그대로 돌리는 것이 이 도구의 일이다
        except Exception as error:
            raise CellFailed(cell.number, error) from error


def run_cells(cells: Sequence[CodeCell], namespace: dict[str, Any], stdout: io.TextIOBase | None = None) -> None:
    sink = stdout if stdout is not None else io.StringIO()
    for cell in cells:
        run_cell(cell, namespace, sink)
