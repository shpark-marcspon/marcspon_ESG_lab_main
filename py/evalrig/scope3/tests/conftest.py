"""시험 공용: 소켓을 막고 돈다(네트워크 0 — scope3colab 의 `socket_blocked` 를 그대로 씀). 글·수는 전부 지은 것(synthetic)."""

import pytest

from scope3colab.sandbox import socket_blocked


@pytest.fixture(autouse=True)
def _no_network():
    with socket_blocked():
        yield
