"""데이터 검증 평가 장치 시험의 공통 자리. 자료는 지은 것이거나 git 에 이미 있는 KB gold 만."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from evalrig.dataverify import cli

DATA_DIR = cli.DATA_DIR


def esg_app_root() -> Path:
    """esg-app 고정 스키마 자리 — ESG_APP_ROOT, 없으면 저장소 옆 esg-app(main·워크트리 둘 다)."""
    import os

    env = os.environ.get("ESG_APP_ROOT")
    if env:
        return Path(env)
    for base in (cli.REPO.parent, cli.REPO.parent.parent):
        if (base / "esg-app").is_dir():
            return base / "esg-app"
    return cli.REPO.parent / "esg-app"


@pytest.fixture(scope="session")
def kb_gold() -> dict:
    return json.loads(cli.KB_GOLD.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def catalog() -> dict:
    return json.loads(cli.CATALOG.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def git_file():
    def read(name: str) -> str:
        return (DATA_DIR / name).read_text(encoding="utf-8")

    return read


@pytest.fixture(scope="session")
def input_schema() -> dict:
    p = esg_app_root() / "apps/api-py/esgapi/dataverify/tests/data/flow-shapes/dataverify.input@1.schema.json"
    if not p.exists():
        pytest.fail(f"esg-app 고정 스키마 없음: {p} (ESG_APP_ROOT 로 자리를 준다 — 조용한 초록 금지)")
    return json.loads(p.read_text(encoding="utf-8"))
