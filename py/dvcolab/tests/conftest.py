"""시험 공용: 소켓을 막고 돈다(네트워크 0). 글·수는 전부 지은 것(synthetic)이고, 받은 코랩은 돌리지 않는다."""
import pytest

from dvcolab.sandbox import socket_blocked


@pytest.fixture(autouse=True)
def _no_network():
    with socket_blocked():
        yield
