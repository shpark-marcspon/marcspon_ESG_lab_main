"""자리: 작업 폴더(marcspon)·esg-app main 체크아웃·산출 폴더. esg-app 은 읽기만 한다(sys.path 에 api-py 를 넣을 뿐).

하네스는 esg-app 의 가상환경 python 으로 `-I` 로 돈다(README). 그래서 cwd·PYTHONPATH 를 보지 않고, 이 파일이
`py/` 와 `esg-app/apps/api-py` 를 sys.path 앞에 넣는다.
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _find_root() -> Path:
    """위로 올라가며 esg-app/apps/api-py 가 있는 폴더(작업 폴더). 워크트리(esg-lab.worktrees/MIG1)든 main 이든."""
    given = os.environ.get("MIGLAB_ROOT")
    if given:
        return Path(given).resolve()
    for parent in HERE.parents:
        if (parent / "esg-app" / "apps" / "api-py" / "esgapi").is_dir():
            return parent
    raise SystemExit("작업 폴더(esg-app/apps/api-py 가 있는 곳)를 찾지 못했다 — MIGLAB_ROOT 로 준다")


ROOT = _find_root()
API_PY = ROOT / "esg-app" / "apps" / "api-py"
ESGAPP_MODELS = API_PY / "esgapi" / "core" / "llm" / "data" / "models.json"
OUT = Path(os.environ.get("MIGLAB_OUT", ROOT / "esg-lab" / "out" / "mig1"))
"""원시 산출(메인 체크아웃의 esg-lab/out/mig1 — git 밖). 시험은 MIGLAB_OUT 으로 임시 폴더를 준다."""
CALLS = OUT / "calls.jsonl"


def setup_paths() -> None:
    for path in (str(HERE.parent), str(API_PY)):
        if path not in sys.path:
            sys.path.insert(0, path)
