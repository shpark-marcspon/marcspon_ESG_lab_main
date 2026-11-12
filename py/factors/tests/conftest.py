from __future__ import annotations

import pytest

from factors import build as build_mod
from factors.loaders import LOADERS


@pytest.fixture(scope="session")
def loaded() -> dict[str, list[dict]]:
    """출처 id → 레코드. 원자료가 없으면 건너뛰지 않고 실패한다 (`python -m factors fetch`)."""
    return {sid: fn() for sid, fn in LOADERS.items()}


@pytest.fixture(scope="session")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("factors-out")
    records = build_mod.build(out)
    return out, records
