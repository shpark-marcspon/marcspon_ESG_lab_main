"""자리: 작업 폴더(marcspon)와 esg-app main 체크아웃. esg-app 은 읽기만 한다(sys.path 에 api-py 를 넣을 뿐 — miglab `env.py` 꼴).

시험은 esg-app 의 가상환경 python 으로 `-I` 로 돈다(README 「명령」). 그래서 cwd·PYTHONPATH 를 보지 않고, 이 파일이 `py/` 와
`esg-app/apps/api-py` 를 sys.path 앞에 넣는다. 1차 엔진(`colab_157`)은 import 만 한다(`sid:MARC-ENT-683`).
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _find_root() -> Path:
    """위로 올라가며 esg-app/apps/api-py/esgapi 가 있는 폴더(작업 폴더). 워크트리(esg-lab.worktrees/EBS2)든 main 이든."""
    given = os.environ.get("SCOPE3FLAWS_ROOT")
    if given:
        return Path(given).resolve()
    for parent in HERE.parents:
        if (parent / "esg-app" / "apps" / "api-py" / "esgapi").is_dir():
            return parent
    raise SystemExit("작업 폴더(esg-app/apps/api-py 가 있는 곳)를 찾지 못했다 — SCOPE3FLAWS_ROOT 로 준다")


ROOT = _find_root()
API_PY = ROOT / "esg-app" / "apps" / "api-py"


def setup_paths() -> None:
    for path in (str(HERE.parent), str(API_PY)):
        if path not in sys.path:
            sys.path.insert(0, path)
