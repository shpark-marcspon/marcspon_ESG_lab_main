"""읽기판 md 에서 코랩 코드 블록을 차례대로 뽑는다. 실행할 때마다 읽는다(저장소에 코랩 코드를 두지 않는다).

읽기판의 꼴: `### 셀 <n> · code` 머리 아래 펜스 블록 하나(```` ```python ```` 또는 백틱 넷). 펜스 길이는 여는 줄과
같은 길이의 닫는 줄로 맞춘다 — 본문 안의 더 짧은 펜스(글 속 ``` )는 블록을 닫지 않는다.

건너뛰는 줄은 `SKIP_RULES` 의 것뿐이다. 각 규칙은 이름과 줄 판정이고, 건너뛴 줄은 셀 번호·규칙 이름과 함께 센다.
건너뛴 줄 자리에는 같은 들여쓰기의 `pass` 를 두어 블록 꼴(if 몸통 등)을 깨지 않는다.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

_COLAB_MD_REL = Path("received-derived") / "2026-10-15" / "Scope 3 코랩" / "Scope3_생물기원적용_.md"


def default_colab_md() -> Path:
    """기본 읽기판 자리: 위로 올라가며 `received-derived/` 가 있는 폴더(작업 폴더 marcspon)를 찾는다.

    저장소(`esg-lab/`)와 밴드 워크트리(`esg-lab.worktrees/<밴드>/`)의 깊이가 달라 고정 깊이로 잡지 않는다.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "received-derived").is_dir():
            return parent / _COLAB_MD_REL
    raise FileNotFoundError("received-derived/ 를 위 폴더에서 찾지 못했다 — --colab-md 로 읽기판 자리를 준다")

_CELL_HEAD = re.compile(r"^### 셀 (\d+) · (code|markdown)\s*$")
_FENCE_OPEN = re.compile(r"^(`{3,})python\s*$")


@dataclass(frozen=True)
class SkipRule:
    name: str
    pattern: re.Pattern[str]


# 미리 정한 건너뛰기 목록. 이 밖의 줄은 그대로 돈다.
SKIP_RULES: tuple[SkipRule, ...] = (
    # 셀 1 `!pip install …` — 셸 명령(IPython 문법). 의존성은 uv group 이 깐다.
    SkipRule("shell-magic", re.compile(r"^\s*[!%]")),
    # 셀 3 `base_path = '/content/drive/…'` — Drive 자리. 하네스가 지어낸 계수 책의 폴더를 미리 넣는다.
    SkipRule("drive-base-path", re.compile(r"""^\s*base_path\s*=\s*['"]/content/drive/""")),
)


@dataclass(frozen=True)
class SkippedLine:
    cell: int
    line: int  # 블록 안 1부터
    rule: str
    text: str


@dataclass(frozen=True)
class CodeCell:
    number: int  # 읽기판의 셀 번호(원본 노트북의 0부터 센 차례)
    source: str  # 건너뛴 줄을 `pass` 로 바꾼 뒤의 코드
    skipped: tuple[SkippedLine, ...] = field(default=())


class CellFormatError(ValueError):
    """읽기판 꼴이 기대와 다르다(닫히지 않은 펜스, code 셀에 블록 없음 등)."""


def _apply_skips(number: int, lines: list[str], rules: tuple[SkipRule, ...]) -> tuple[str, tuple[SkippedLine, ...]]:
    out: list[str] = []
    skipped: list[SkippedLine] = []
    for i, line in enumerate(lines, start=1):
        rule = next((r for r in rules if r.pattern.search(line)), None)
        if rule is None:
            out.append(line)
            continue
        skipped.append(SkippedLine(number, i, rule.name, line))
        indent = line[: len(line) - len(line.lstrip())]
        out.append(f"{indent}pass  # scope3colab: 건너뜀({rule.name})")
    return "\n".join(out) + "\n", tuple(skipped)


def extract_cells(text: str, rules: tuple[SkipRule, ...] = SKIP_RULES) -> list[CodeCell]:
    """md 글에서 code 셀을 차례대로 뽑는다. markdown 셀과 셀 밖의 블록(입력 칸 표 등)은 건너뛴다."""
    lines = text.splitlines()
    cells: list[CodeCell] = []
    current: tuple[int, str] | None = None  # (셀 번호, 종류)
    i = 0
    while i < len(lines):
        head = _CELL_HEAD.match(lines[i])
        if head:
            current = (int(head.group(1)), head.group(2))
            i += 1
            continue
        if lines[i].startswith("## "):
            current = None  # 「## 2. 입력 칸」 같은 다음 절 — 셀 전문이 끝났다
        fence = _FENCE_OPEN.match(lines[i])
        if fence and current is not None and current[1] == "code":
            closing = fence.group(1)
            body: list[str] = []
            j = i + 1
            while j < len(lines) and lines[j].rstrip() != closing:
                body.append(lines[j])
                j += 1
            if j >= len(lines):
                raise CellFormatError(f"셀 {current[0]}: 펜스 {closing!r} 가 닫히지 않았다")
            if any(c.number == current[0] for c in cells):
                raise CellFormatError(f"셀 {current[0]}: 코드 블록이 둘 이상이다")
            source, skipped = _apply_skips(current[0], body, rules)
            cells.append(CodeCell(current[0], source, skipped))
            i = j + 1
            continue
        i += 1
    numbers = [c.number for c in cells]
    if numbers != sorted(numbers):
        raise CellFormatError(f"셀 차례가 어긋났다: {numbers}")
    return cells


def read_cells(path: Path, rules: tuple[SkipRule, ...] = SKIP_RULES) -> list[CodeCell]:
    return extract_cells(path.read_text(encoding="utf-8"), rules)
