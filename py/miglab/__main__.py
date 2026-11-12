"""`<esg-app venv>/bin/python -I esg-lab/py/miglab/__main__.py <명령> …` — -I 라 sys.path 를 이 파일이 세운다."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miglab import env  # noqa: E402

env.setup_paths()

from miglab.cli import main  # noqa: E402

sys.exit(main())
