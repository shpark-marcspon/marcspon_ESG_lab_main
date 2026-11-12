"""시험 공용: esg-app 가상환경 python 으로만 돈다(README 「명령」), 소켓을 막고 돈다(네트워크 0). 글·수는 전부 지은 것(synthetic)."""

import importlib.util

import pytest

from scope3flaws import env

env.setup_paths()

if importlib.util.find_spec("esgapi") is None or importlib.util.find_spec("django") is None:
    pytest.exit(
        "scope3flaws 시험은 esg-app 가상환경 python 으로 돈다: "
        "cd esg-lab/py && <작업 폴더>/esg-app/apps/api-py/.venv/bin/python -I -m pytest scope3flaws -q -p no:cacheprovider",
        returncode=4,
    )

from scope3colab.sandbox import socket_blocked  # noqa: E402


@pytest.fixture(autouse=True)
def _no_network():
    with socket_blocked():
        yield
