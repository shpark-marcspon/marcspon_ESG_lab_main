"""그린워싱 평가 장치 시험의 공통 자리. 자료는 지은 것만(실재 보고서 글 0)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def fixture():
    def read(name: str):
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    return read


@pytest.fixture(scope="session")
def sample_pdf(tmp_path_factory) -> Path:
    from greenwash.tests.build_sample import build

    p = tmp_path_factory.mktemp("gw") / "sample.pdf"
    build(p)
    return p
