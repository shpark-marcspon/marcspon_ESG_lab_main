"""가짜 google.colab·requests, 짜 둔 답 차례, 소켓 막힘, env 지움."""
import os
import socket
import sys

import pytest

from dvcolab.cells import CodeCell
from dvcolab.sandbox import (
    FAKE_KEY,
    RESPONSES_URL,
    AnswersExhausted,
    BlockedUse,
    CellFailed,
    NetworkBlocked,
    Sandbox,
    run_cells,
    sandboxed,
    socket_blocked,
)


def cells(*sources: str) -> list[CodeCell]:
    return [CodeCell(i, src) for i, src in enumerate(sources, start=1)]


ASK = (
    "import requests\n"
    f"URL = {RESPONSES_URL!r}\n"
    "def ask(s, u):\n"
    "    r = requests.post(URL, headers={}, json={'model': '가상', 'input': [{'role': 'system', 'content': s},"
    " {'role': 'user', 'content': u}]}, timeout=1)\n"
    "    return r.json()['output'][0]['content'][0]['text']\n"
)


def test_fake_requests_returns_answers_in_order_and_records_the_texts(tmp_path):
    box = Sandbox(["하나", "둘"])
    ns: dict = {}
    with sandboxed(box, tmp_path):
        run_cells(cells(ASK, "r1 = ask('가상 S1', '가상 U1')\nr2 = ask('가상 S2', '가상 U2')\n"), ns)
    assert (ns["r1"], ns["r2"]) == ("하나", "둘")
    assert [(p["system"], p["user"]) for p in box.posts] == [("가상 S1", "가상 U1"), ("가상 S2", "가상 U2")]
    assert box.aborts == []


def test_running_out_of_answers_passes_through_except_exception(tmp_path):
    box = Sandbox(["하나"])
    code = "ask('a', 'b')\ntry:\n    ask('c', 'd')\nexcept Exception:\n    pass\n"
    with pytest.raises(AnswersExhausted), sandboxed(box, tmp_path):
        run_cells(cells(ASK, code), {})
    assert len(box.aborts) == 1


def test_another_url_is_blocked(tmp_path):
    box = Sandbox(["하나"])
    with pytest.raises(BlockedUse), sandboxed(box, tmp_path):
        run_cells(cells("import requests\nrequests.post('https://example.invalid/')\n"), {})


def test_userdata_gives_a_fake_and_files_drive_are_blocked(tmp_path):
    box = Sandbox([])
    ns: dict = {}
    with sandboxed(box, tmp_path):
        run_cells(cells("from google.colab import userdata\nk = userdata.get('가상키')\n"), ns)
    assert ns["k"] == FAKE_KEY
    assert box.events == ["userdata.get(가상키)"]
    for code in ("from google.colab import files\nfiles.upload()\n", "from google.colab import drive\ndrive.mount('/x')\n"):
        with pytest.raises(BlockedUse), sandboxed(box, tmp_path):
            run_cells(cells(code), {})


def test_fakes_and_env_are_restored(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-env-value")
    before = {name: sys.modules.get(name) for name in ("google.colab", "requests")}
    seen: dict = {}
    with sandboxed(Sandbox([]), tmp_path):
        seen["key"] = os.environ.get("OPENAI_API_KEY")
    assert seen["key"] is None
    assert os.environ["OPENAI_API_KEY"] == "synthetic-env-value"
    assert {name: sys.modules.get(name) for name in before} == before


def test_socket_blocked_inside_cells_is_recorded(tmp_path):
    box = Sandbox([])
    code = "import socket\ntry:\n    socket.socket()\nexcept Exception:\n    pass\n"
    with pytest.raises(NetworkBlocked), sandboxed(box, tmp_path):
        run_cells(cells(code), {})
    assert box.aborts and box.aborts[0].startswith("NetworkBlocked")


def test_socket_blocked_raises():
    with socket_blocked():
        with pytest.raises(NetworkBlocked):
            socket.create_connection(("example.invalid", 80))
        with pytest.raises(NetworkBlocked):
            socket.getaddrinfo("example.invalid", 80)


def test_cell_failure_names_the_cell():
    with pytest.raises(CellFailed, match="셀 2: ZeroDivisionError"):
        run_cells(cells("a = 1\n", "b = 1 / 0\n"), {})
