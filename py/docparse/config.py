"""경로 설정. 원본 PDF는 저장소에 두지 않고 `../prework/reports/` 를 참조한다."""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schemas" / "document.schema.json"
PLACES_SCHEMA_PATH = REPO_ROOT / "schemas" / "char-places.schema.json"
DEFAULT_GOLD_DIR = REPO_ROOT / "data" / "gold"

# 정답 표의 report 키 -> 국문판 파일명
REPORT_FILES = {
    "samsung": "samsung_2026_kor.pdf",
    "kb": "kb_2025_kor.pdf",
    "kb_databook": "kb_2025_databook_kor.pdf",
    "cosmax": "cosmax_2025_kor.pdf",
}


def reports_dir() -> Path:
    """환경 변수 DOCPARSE_REPORTS_DIR 이 있으면 그 경로, 없으면 `<저장소>/../prework/reports`."""
    env = os.environ.get("DOCPARSE_REPORTS_DIR")
    return Path(env) if env else REPO_ROOT.parent / "prework" / "reports"


def report_path(report: str) -> Path:
    return reports_dir() / REPORT_FILES[report]
