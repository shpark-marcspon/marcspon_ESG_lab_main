import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from miglab import env  # noqa: E402

env.setup_paths()
