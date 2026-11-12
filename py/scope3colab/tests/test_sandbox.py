"""§3-2 이름공간 차례, §3-3 가짜 anthropic, §3-4 소켓 막힘(과 막은 모듈)."""
import socket
import sys

import pytest

from scope3colab.cells import CodeCell
from scope3colab.sandbox import (
    Answer,
    AnswerMismatch,
    AnswersExhausted,
    BlockedModuleUsed,
    CellFailed,
    NetworkBlocked,
    Sandbox,
    run_cells,
    run_in_sandbox,
    socket_blocked,
)


def cells(*sources: str) -> list[CodeCell]:
    return [CodeCell(i, src) for i, src in enumerate(sources, start=1)]


# ── §3-2 ──
def test_later_cell_redefines_and_calls_captured_original():
    ns = run_cells(
        cells(
            "def price(x):\n    return x * 2\n",
            # 코랩의 `_ORIGINAL_* = globals().get(…)` 패치 사슬 꼴
            "_ORIGINAL_price = globals().get('price')\n"
            "def price(x):\n    return _ORIGINAL_price(x) + 1\n",
            "_ORIGINAL_price_FULL_FIX = globals().get('price')\n"
            "def price(x):\n    return _ORIGINAL_price_FULL_FIX(x) * 10\n",
            "result = price(4)\n",
        ),
        {},
    )
    assert ns["result"] == ((4 * 2) + 1) * 10


def test_late_binding_sees_last_definition():
    # 붙잡은 옛 함수 안의 전역 이름은 부를 때의 마지막 정의로 풀린다
    ns = run_cells(
        cells(
            "def helper():\n    return 'old'\ndef outer():\n    return helper()\n",
            "_ORIGINAL_outer = globals().get('outer')\ndef helper():\n    return 'new'\n",
            "result = _ORIGINAL_outer()\n",
        ),
        {},
    )
    assert ns["result"] == "new"


def test_cell_failure_names_the_cell():
    with pytest.raises(CellFailed, match="셀 2: ZeroDivisionError"):
        run_cells(cells("a = 1\n", "b = 1 / 0\n"), {})


# ── §3-3 ──
ASK = (
    "from anthropic import Anthropic\n"
    "client = Anthropic()\n"
    "def ask(p):\n"
    "    res = client.messages.create(model='가상', max_tokens=9, messages=[{'role': 'user', 'content': p}])\n"
    "    return res.content[0].text\n"
)


def test_fake_anthropic_returns_answers_in_order(tmp_path):
    box = Sandbox([Answer("하나"), Answer("둘", expect="가상 물음 2")])
    ns = run_in_sandbox(cells(ASK, "r1 = ask('가상 물음 1')\nr2 = ask('가상 물음 2')\n"), box, tmp_path, {})
    assert (ns["r1"], ns["r2"]) == ("하나", "둘")
    assert [c["n"] for c in box.calls] == [1, 2]
    assert box.aborts == []


def test_running_out_of_answers_fails_loudly_through_except_exception(tmp_path):
    box = Sandbox([Answer("하나")])
    code = "r1 = ask('가상 1')\ntry:\n    r2 = ask('가상 2')\nexcept Exception:\n    r2 = ''\n"
    with pytest.raises(AnswersExhausted):
        run_in_sandbox(cells(ASK, code), box, tmp_path, {})
    assert len(box.aborts) == 1


def test_bare_except_swallowing_is_still_recorded(tmp_path):
    box = Sandbox([])
    code = "try:\n    r = ask('가상')\nexcept:\n    r = ''\n"
    ns = run_in_sandbox(cells(ASK, code), box, tmp_path, {})
    assert ns["r"] == ""
    assert box.aborts and box.aborts[0].startswith("AnswersExhausted")


def test_answer_expect_mismatch_fails(tmp_path):
    box = Sandbox([Answer("하나", expect="가상 기대 글")])
    with pytest.raises(AnswerMismatch):
        run_in_sandbox(cells(ASK, "ask('다른 물음')\n"), box, tmp_path, {})


def test_fakes_are_removed_after_run(tmp_path):
    before = {name: sys.modules.get(name) for name in ("anthropic", "google.colab", "geopy")}
    run_in_sandbox(cells(ASK), Sandbox([]), tmp_path, {})
    assert {name: sys.modules.get(name) for name in before} == before


def test_colab_fakes_upload_and_download(tmp_path):
    template = tmp_path / "가상템플릿.xlsx"
    template.write_bytes(b"synthetic")
    box = Sandbox([], template)
    code = (
        "from google.colab import files, drive, userdata\n"
        "drive.mount('/content/drive')\n"
        "key = userdata.get('가상키')\n"
        "uploaded = files.upload()\n"
        "input_file = list(uploaded.keys())[0]\n"
        "files.download(input_file)\n"
    )
    ns = run_in_sandbox(cells(code), box, tmp_path / "work", {})
    assert ns["input_file"] == "가상템플릿.xlsx"
    assert (tmp_path / "work" / "가상템플릿.xlsx").read_bytes() == b"synthetic"
    assert ns["key"] == "synthetic-not-a-key"
    assert box.downloads == ["가상템플릿.xlsx"]


# ── §3-4 ──
def test_socket_blocked_raises():
    with socket_blocked():
        with pytest.raises(NetworkBlocked):
            socket.create_connection(("example.invalid", 80))
        with pytest.raises(NetworkBlocked):
            socket.getaddrinfo("example.invalid", 80)


def test_network_inside_cells_fails_and_is_recorded(tmp_path):
    box = Sandbox([])
    code = "import socket\ntry:\n    socket.socket()\nexcept Exception:\n    pass\n"
    with pytest.raises(NetworkBlocked):
        run_in_sandbox(cells(code), box, tmp_path, {})
    assert box.aborts and box.aborts[0].startswith("NetworkBlocked")


def test_socket_restored_after_block():
    before = socket.socket
    with socket_blocked():
        assert socket.socket is not before
    assert socket.socket is before


def test_geopy_can_be_constructed_but_not_used(tmp_path):
    box = Sandbox([])
    code = (
        "from geopy.distance import geodesic\n"
        "from geopy.geocoders import Nominatim\n"
        "geolocator = Nominatim(user_agent='가상', timeout=10)\n"
    )
    run_in_sandbox(cells(code), box, tmp_path, {})
    with pytest.raises(BlockedModuleUsed):
        run_in_sandbox(cells(code, "geolocator.geocode('가상시')\n"), box, tmp_path, {})
    with pytest.raises(BlockedModuleUsed):
        run_in_sandbox(cells(code, "geodesic((0, 0), (1, 1))\n"), box, tmp_path, {})
