"""읽기판 md 에서 데이터 검증 코랩의 code 셀을 뽑는다. 실행할 때마다 읽는다(저장소에 코랩 코드를 두지 않는다).

셀 뽑기는 `scope3colab.cells.extract_cells` 를 그대로 쓴다(밴드 DV1c 감독 결정 ③ — scope3colab 은 고치지 않는다).
건너뛰기 규칙은 이 도구의 것이다: 실측(2026-10-19)으로 돌리는 셀(3·4·7·10·14·18)에 건너뛸 줄이 없어 빈 목록이다.
`!pip`·`drive.mount`·`files.upload/download` 줄은 그 셀들에 없고, `userdata.get` 은 가짜 `google.colab` 이 받는다.
목록 밖 실패는 도구 실패(2)다.
"""

from pathlib import Path

from scope3colab.cells import CellFormatError, CodeCell, SkipRule, extract_cells

__all__ = ["CellFormatError", "CodeCell", "RUN", "NOT_RUN", "SKIP_RULES", "default_colab_md", "read_cells", "pick"]

_COLAB_MD_REL = (
    Path("received-derived") / "2026-10-15" / "데이터 검증 코랩" / "데이터리뷰_v7_4평가테스트용_Input2(코랩1~3번)_test시연.md"
)

# 돌리는 셀과 하는 일. 차례대로 한 이름공간에서 돈다.
RUN: dict[int, str] = {
    3: "테마 거르기(하네스가 data 를 미리 넣음)",
    4: "data_split 을 파일로 씀(작업 폴더 안)",
    7: "도구·스키마·분배기·고리 정의, 파일에서 data 를 다시 읽음",
    10: "Plan 프롬프트(가짜 requests 가 받음)",
    14: "검토 프롬프트(HINTS = 셀 10 의 짜 둔 답)",
    18: "인사이트 프롬프트",
}
# 안 돌리는 code 셀과 까닭.
NOT_RUN: dict[int, str] = {
    2: "입력 붙여넣기 자리 — 하네스가 지은 data·fname 을 미리 넣는다(발주서 §7-4)",
    12: "이어 묻기(previous_response_id)",
    16: "이어 묻기",
    20: "이어 묻기",
    23: "이어 묻기",
    28: "docx 생성(Code Interpreter)",
    30: "docx 생성",
    37: "#OLD 옛 판 고리",
}
SKIP_RULES: tuple[SkipRule, ...] = ()


def default_colab_md() -> Path:
    """기본 읽기판 자리: 위로 올라가며 `received-derived/` 가 있는 폴더(작업 폴더 marcspon)를 찾는다."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "received-derived").is_dir():
            return parent / _COLAB_MD_REL
    raise FileNotFoundError("received-derived/ 를 위 폴더에서 찾지 못했다 — --colab-md 로 읽기판 자리를 준다")


def read_cells(path: Path) -> list[CodeCell]:
    return extract_cells(path.read_text(encoding="utf-8"), SKIP_RULES)


def pick(cells: list[CodeCell]) -> list[CodeCell]:
    """돌릴 셀만 차례대로. 하나라도 없으면 CellFormatError. 셀 목록이 RUN·NOT_RUN 밖의 code 셀을 가지면 그것도 실패."""
    numbers = {c.number for c in cells}
    missing = sorted(set(RUN) - numbers)
    if missing:
        raise CellFormatError(f"돌릴 셀이 없다: {missing}")
    unknown = sorted(numbers - set(RUN) - set(NOT_RUN))
    if unknown:
        raise CellFormatError(f"모르는 code 셀: {unknown} — 읽기판이 바뀌었으면 RUN·NOT_RUN 을 다시 잰다")
    return [c for c in cells if c.number in RUN]
