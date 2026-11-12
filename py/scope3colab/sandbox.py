"""코랩 셀을 빈 이름공간 하나에서 차례대로 돌리는 자리와 가짜들.

- `google.colab`(drive·files·userdata), `anthropic`, `geopy` 를 `sys.modules` 에 가짜로 끼운다. 진짜는 깔지 않는다.
- 소켓을 막는다(`socket.socket`·`create_connection`·`getaddrinfo`).
- 하네스가 멈춰야 하는 일(네트워크, 짜 둔 답이 모자람·어긋남, 막은 모듈을 실제로 씀)은 `HarnessAbort`(BaseException)로
  던진다. 코랩의 `except Exception` 이 삼키지 못하게. 코랩에는 맨 `except:` 도 있어(셀 11·13) 던진 것과 별도로
  `Sandbox.aborts` 에 적고, 실행 뒤 하네스가 그 목록을 본다 — 삼켜져도 조용히 지나가지 않는다.
"""

import builtins
import contextlib
import io
import json
import os
import shutil
import socket
import sys
import types
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scope3colab.cells import CodeCell

FAKE_KEY = "synthetic-not-a-key"
# 실행 동안 환경에서 지우는 변수(키·추적). 끝나면 되돌린다.
_SCRUB_ENV = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_BASE_URL", "HTTP_PROXY", "HTTPS_PROXY")
_FAKE_MODULES = ("google", "google.colab", "anthropic", "geopy", "geopy.distance", "geopy.geocoders")


class HarnessAbort(BaseException):
    """하네스를 멈추는 일. 코랩의 `except Exception` 을 지나간다."""


class NetworkBlocked(HarnessAbort):
    pass


class AnswersExhausted(HarnessAbort):
    pass


class AnswerMismatch(HarnessAbort):
    pass


class BlockedModuleUsed(HarnessAbort):
    pass


class CellFailed(Exception):
    def __init__(self, cell: int, error: BaseException) -> None:
        super().__init__(f"셀 {cell}: {type(error).__name__}: {error}")
        self.cell = cell
        self.error = error


@dataclass(frozen=True)
class Answer:
    text: str
    expect: str | None = None  # 프롬프트(시스템+사용자)에 들어 있어야 하는 글. 없으면 보지 않는다


def load_answers(path: Path) -> list[Answer]:
    """짜 둔 답 JSON: `{"synthetic": true, "answers": [{"text": …, "expect": …}, …]}`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("synthetic") is not True:
        raise ValueError(f"{path}: 짜 둔 답 파일은 `\"synthetic\": true` 를 달아야 한다")
    return [Answer(a["text"], a.get("expect")) for a in data["answers"]]


@dataclass
class Sandbox:
    """가짜들이 함께 쓰는 기록."""

    answers: list[Answer]
    template: Path | None = None  # files.upload() 가 올리는 파일
    calls: list[dict[str, Any]] = field(default_factory=list)
    events: list[str] = field(default_factory=list)  # drive.mount·userdata.get·files.* 부름
    downloads: list[str] = field(default_factory=list)
    aborts: list[str] = field(default_factory=list)
    # 거리판(S3C-o3): 가짜 Nominatim 의 `geocode` 가 늘 None(「못 찾음」)을 내고 물음 글을 차례대로 적는다. 끄면(기본판)
    # `geocode` 도 부르면 멈춤. `geodesic` 과 그 밖의 Nominatim 속성은 어느 판이든 부르면 멈춤.
    geocode_none: bool = False
    geocode_queries: list[str] = field(default_factory=list)

    def abort(self, exc: HarnessAbort) -> HarnessAbort:
        self.aborts.append(f"{type(exc).__name__}: {exc}")
        return exc

    # ── anthropic ──
    def create_message(self, **kw: Any) -> Any:
        n = len(self.calls)
        system = kw.get("system") or ""
        messages = kw.get("messages") or []
        prompt = "\n".join(str(m.get("content", "")) for m in messages)
        if n >= len(self.answers):
            raise self.abort(AnswersExhausted(f"부름 {n + 1}: 짜 둔 답이 {len(self.answers)}개뿐 — 첫 줄 {_head(prompt)!r}"))
        answer = self.answers[n]
        if answer.expect is not None and answer.expect not in f"{system}\n{prompt}":
            raise self.abort(AnswerMismatch(f"부름 {n + 1}: 기대 글 {answer.expect!r} 가 프롬프트에 없다 — {_head(prompt)!r}"))
        self.calls.append(
            {"n": n + 1, "model": kw.get("model"), "max_tokens": kw.get("max_tokens"), "system": _head(str(system)),
             "prompt": _head(prompt), "answer": answer.text}
        )
        if self.geocode_none:  # 거리판: 이 부름 앞에 지오코더 물음이 몇 개 있었나(차례 표)
            self.calls[-1]["geocode_before"] = len(self.geocode_queries)
        block = types.SimpleNamespace(type="text", text=answer.text)
        return types.SimpleNamespace(content=[block], model=kw.get("model"), stop_reason="end_turn")


def _head(text: str, limit: int = 80) -> str:
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return first[:limit]


def _fake_modules(box: Sandbox) -> dict[str, types.ModuleType]:
    google = types.ModuleType("google")
    colab = types.ModuleType("google.colab")
    google.colab = colab  # type: ignore[attr-defined]

    drive = types.SimpleNamespace(mount=lambda path, *a, **k: box.events.append(f"drive.mount({path})"))

    def userdata_get(name: str) -> str:
        box.events.append(f"userdata.get({name})")
        return FAKE_KEY

    def files_upload() -> dict[str, bytes]:
        if box.template is None:
            raise box.abort(BlockedModuleUsed("files.upload: 올릴 템플릿이 정해지지 않았다"))
        dest = Path.cwd() / box.template.name
        shutil.copyfile(box.template, dest)
        box.events.append(f"files.upload() -> {box.template.name}")
        return {box.template.name: dest.read_bytes()}

    def files_download(path: str) -> None:
        box.events.append(f"files.download({Path(path).name})")
        box.downloads.append(str(path))

    colab.drive = drive  # type: ignore[attr-defined]
    colab.userdata = types.SimpleNamespace(get=userdata_get)  # type: ignore[attr-defined]
    colab.files = types.SimpleNamespace(upload=files_upload, download=files_download)  # type: ignore[attr-defined]

    anthropic = types.ModuleType("anthropic")

    class Anthropic:  # noqa: D401 — 코랩이 `Anthropic()` 으로 짓는다
        def __init__(self, *a: Any, **k: Any) -> None:
            self.messages = types.SimpleNamespace(create=box.create_message)

    anthropic.Anthropic = Anthropic  # type: ignore[attr-defined]

    def blocked(what: str) -> Any:
        def _raise(*a: Any, **k: Any) -> Any:
            raise box.abort(BlockedModuleUsed(f"{what} 를 불렀다(가짜로 막은 모듈)"))

        return _raise

    class Nominatim:
        """지을 수는 있다(셀 5·7 이 import 때 짓는다). `geocode` 는 `box.geocode_none` 이면 None, 그 밖은 부르면 멈춘다."""

        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def geocode(self, query: Any, *a: Any, **k: Any) -> None:
            if not box.geocode_none:
                raise box.abort(BlockedModuleUsed("geopy Nominatim.geocode 를 불렀다(가짜로 막은 모듈)"))
            box.geocode_queries.append(str(query))
            return None

        def __getattr__(self, name: str) -> Any:
            return blocked(f"geopy Nominatim.{name}")

    geopy = types.ModuleType("geopy")
    distance = types.ModuleType("geopy.distance")
    distance.geodesic = blocked("geopy.distance.geodesic")  # type: ignore[attr-defined]
    geocoders = types.ModuleType("geopy.geocoders")
    geocoders.Nominatim = Nominatim  # type: ignore[attr-defined]
    geopy.distance = distance  # type: ignore[attr-defined]
    geopy.geocoders = geocoders  # type: ignore[attr-defined]

    return {
        "google": google, "google.colab": colab, "anthropic": anthropic,
        "geopy": geopy, "geopy.distance": distance, "geopy.geocoders": geocoders,
    }  # fmt: skip


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


def run_cells(
    cells: Sequence[CodeCell], namespace: dict[str, Any], stdout: io.TextIOBase | None = None
) -> dict[str, Any]:
    """셀을 차례대로 한 이름공간에서 돌린다. 셀이 실패하면 `CellFailed`(건너뛰기 목록 밖의 실패 — 멈춤)."""
    namespace.setdefault("__name__", "__main__")
    namespace.setdefault("__builtins__", builtins)
    sink = stdout if stdout is not None else io.StringIO()
    for cell in cells:
        code = compile(cell.source, f"<colab cell {cell.number}>", "exec")
        with contextlib.redirect_stdout(sink):
            try:
                exec(code, namespace)  # noqa: S102 — 코랩 코드를 그대로 돌리는 것이 이 도구의 일이다
            except Exception as error:
                raise CellFailed(cell.number, error) from error
    return namespace


def run_in_sandbox(
    cells: Sequence[CodeCell], box: Sandbox, workdir: Path, preset: dict[str, Any], stdout: io.TextIOBase | None = None
) -> dict[str, Any]:
    """가짜·소켓 막힘·환경 지움·작업 폴더 안에서 셀을 돌린다. 이름공간을 돌려준다."""
    namespace: dict[str, Any] = dict(preset)
    with scrubbed_env(), fakes_installed(box), socket_blocked(box), working_dir(workdir):
        run_cells(cells, namespace, stdout)
    return namespace
