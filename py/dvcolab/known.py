"""알려진 차이 목록 — 엔진이 일부러 코랩과 다르게 둔 자리(esg-app DV1a 보고 「코랩과 다른 자리」, `prompts/README.md`
「바꾼 자리」). 프롬프트 hunk 하나가 아래 규칙 하나에 맞으면 「알려진 차이」, 아니면 「모르는 차이」다.

규칙의 열쇠는 **우리 쪽(엔진) 글자와 자리**로만 적는다. 코랩 쪽 글은 적지 않는다 — 코랩 쪽 줄은 「같은 머리로
시작한다」·「수만 가리면 우리 줄과 같다」처럼 우리 글자에 견주는 데만 쓴다.

도구 값·상수·테마 거르기에는 알려진 차이가 없다(DV1a 표 #1~#7·#12 는 값이 같은 자리). 약점 5(캐시)는 하네스가 사례마다
코랩의 `_value_cache` 를 갈아 끼워 차이가 나지 않게 하고, 코랩의 옛 값 동작은 `expectations` 사례가 따로 단언한다.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from dvcolab.cases import COMPANY

COMPANY_PREFIXES = ("    [기업명]:", "    [SASB 산업분류]:", "    [주요 중대토픽(Material Topics)]:", "    [Reporting year]:")
# 우리 쪽 줄은 지은 값(cases.COMPANY)으로 글자째 정해진다 — 값이 틀리게 렌더되면 알려진 차이가 아니다.
OUR_COMPANY_LINES = (
    f"{COMPANY_PREFIXES[0]} {COMPANY['company_name']}",
    f"{COMPANY_PREFIXES[1]} {COMPANY['sasb_industry']}",
    f"{COMPANY_PREFIXES[2]} {', '.join(COMPANY['material_topics'])}",
    f"{COMPANY_PREFIXES[3]} {COMPANY['reporting_year']}",
)
REVIEW_EXAMPLE = '"values": {"2022": 1200, "2023": 1300, "2024": 1400}'
_DIGITS = re.compile(r"\d+")


@dataclass(frozen=True)
class Hunk:
    prompt: str  # 프롬프트 id(plan·review·insight)
    part: str  # system·user
    tag: str  # difflib 의 replace·delete·insert
    colab_at: int  # 코랩 쪽 줄 번호(1부터)
    engine_at: int
    colab: tuple[str, ...]
    engine: tuple[str, ...]


@dataclass(frozen=True)
class Known:
    key: str
    what: str
    dv1a: str  # DV1a 「코랩과 다른 자리」 번호 · README 줄
    match: Callable[[Hunk], bool]


def _company(h: Hunk) -> bool:
    if h.part != "user" or h.prompt not in ("plan", "insight") or h.tag != "replace" or len(h.colab) != len(h.engine):
        return False
    for ours, theirs in zip(h.engine, h.colab, strict=True):
        if ours not in OUR_COMPANY_LINES:
            return False
        if not theirs.startswith(COMPANY_PREFIXES[OUR_COMPANY_LINES.index(ours)]):
            return False
    return True


def _review_example(h: Hunk) -> bool:
    if (h.prompt, h.part, h.tag) != ("review", "system", "replace") or len(h.colab) != len(h.engine):
        return False
    return all(
        REVIEW_EXAMPLE in ours and _DIGITS.sub("#", ours) == _DIGITS.sub("#", theirs)
        for ours, theirs in zip(h.engine, h.colab, strict=True)
    )


KNOWN: tuple[Known, ...] = (
    Known("company-lines", "USER 의 기업 정보 네 줄 — 셀 전역 글을 인자로(지은 값)", "#8 · README 바꾼 자리 1", _company),
    Known("review-example-numbers", "셀 14 SYSTEM JSON 예시의 수 셋 — 지어낸 1200·1300·1400", "#9 · README 바꾼 자리 2", _review_example),
)
# 값이 같아 hunk 가 나지 않는 「다른 자리」(보고 표에 0 건으로 센다).
NO_HUNK: tuple[tuple[str, str], ...] = (
    ("셀 전역 SET_MAX_STEPS·HINTS·data_split → 인자", "#8 · README 바꾼 자리 1"),
    ("f-문자열 {…} 안 코드 서식(ruff format)", "#10 · README 끝 문단"),
)


def classify(h: Hunk) -> Known | None:
    return next((k for k in KNOWN if k.match(h)), None)
