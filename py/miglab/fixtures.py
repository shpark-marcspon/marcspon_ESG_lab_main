"""지은 입력(synthetic). 실제 기업명·실제 보고서 문장 0. 회사 이름은 「가상」 꼴만 쓴다.

그린워싱만 공개 PDF 를 쓴다 — 경로는 명령 인자로 받고(파일 이름을 코드에 적지 않는다), 기록에는 sha256 앞 12자를
「파일 키」로 남긴다.
"""

from typing import Any, Final

COMPANY: Final = "가상전자"

# ── 영문번역: 태그 글 세 쪽(엔진 입력 꼴 [PAGE n]·[BLOCK n]·[TABLE n] — esg-app translation 시험의 SIMPLE 꼴) ──

TRANSLATION_TEXT: Final = """[PAGE 1]
[BLOCK 1]
기후변화 대응 전략

[BLOCK 2]
가상전자는 2050년 탄소중립 달성을 목표로 사업장 온실가스 배출량을 단계적으로 줄이고 있습니다. 2024년 Scope 1·2 배출량은 128,400 tCO2eq로 전년 대비 6.2% 감소했으며, 재생에너지 전환율은 38%에 이르렀습니다.

[BLOCK 3]
당사는 TCFD 권고안에 따라 기후 관련 위험과 기회를 식별하고, 이사회 산하 지속가능경영위원회가 분기마다 이행 현황을 점검합니다.

[PAGE 2]
[BLOCK 1]
에너지 사용 현황

[TABLE 1]
| 구분 | 2022 | 2023 | 2024 |
| --- | --- | --- | --- |
| 전력 사용량(MWh) | 412,300 | 398,750 | 381,020 |
| 재생에너지 비율(%) | 24 | 31 | 38 |
| 에너지 원단위(MWh/억원) | 1.82 | 1.71 | 1.63 |

[BLOCK 2]
국내 3개 사업장에 태양광 설비 12.4 MW를 새로 설치했고, 해외 사업장은 전력구매계약(PPA)을 통해 재생에너지를 조달하고 있습니다.

[PAGE 3]
[BLOCK 1]
공급망 협력

[BLOCK 2]
가상전자는 주요 협력사 214곳을 대상으로 ESG 자가진단을 실시했으며, 고위험으로 분류된 17곳에 대해 현장 실사와 개선 계획 수립을 지원했습니다. 협력사 탄소 데이터 플랫폼을 도입해 Scope 3 범주 1의 1차 데이터 비중을 22%까지 높였습니다.

[FOOTER]
가상전자 지속가능경영보고서 2024 · 12
"""

TRANSLATION_GLOSSARY: Final = (
    ("탄소중립", "carbon neutrality"),
    ("지속가능경영위원회", "Sustainability Management Committee"),
    ("전력구매계약", "Power Purchase Agreement"),
    ("협력사", "suppliers"),
)
INDUSTRY_STANDARDS: Final = "GRI Standards 2021, IFRS S1/S2 (ISSB), SASB, TCFD"
PREFERRED_SUBJECT: Final = "electronic components and devices"

# ── Scope 3: C1 구매품 24줄 + C2 자본재 6줄(esg-app scope3 시험 support 의 칸 꼴, 품목은 흔한 낱말) ──

C1_HEAD: Final[list[Any]] = [
    "*국가", "*사업장", "*품목군", "*상세 품목명/서비스명", "거래처명(선택)",
    "연간 입력", "월별 지출금액", None, "비고(선택)", "단위",
]  # fmt: skip
C1_SUB: Final[list[Any]] = [None, None, None, None, None, "연간지출금액(원)", "1월", "2월", None, None]
C1_ITEMS: Final[tuple[tuple[str | None, str, int], ...]] = (
    ("사무용품", "A4 복사용지", 18_400_000),
    ("사무용품", "프린터 토너 카트리지", 9_200_000),
    (None, "사무용 의자", 14_800_000),
    (None, "노트북 컴퓨터", 96_000_000),
    (None, "모니터 27인치", 31_500_000),
    ("컨설팅", "경영 자문 용역", 120_000_000),
    (None, "법률 자문 수수료", 44_000_000),
    (None, "회계 감사 용역", 58_000_000),
    (None, "사내 식당 위탁 운영", 210_000_000),
    (None, "생수 배달", 3_600_000),
    (None, "원두 커피", 7_800_000),
    (None, "청소 용역", 66_000_000),
    (None, "건물 경비 용역", 72_000_000),
    (None, "클라우드 서버 이용료", 135_000_000),
    (None, "소프트웨어 라이선스", 88_000_000),
    (None, "포장용 골판지 상자", 52_000_000),
    (None, "윤활유", 11_300_000),
    (None, "작업용 장갑", 4_900_000),
    (None, "산업용 세척제", 8_700_000),
    (None, "직원 교육 위탁", 39_000_000),
    (None, "광고 대행 수수료", 150_000_000),
    (None, "택배 발송비", 27_000_000),
    (None, "휴대전화 통신요금", 19_200_000),
    (None, "전자부품 시험 의뢰", 63_000_000),
)
C2_HEAD: Final[list[Any]] = [
    "*국가", "*사업장", "*자산분류", "*상세 자산명", "자산번호(선택)", "거래처명(선택)",
    "연간 입력", "월별 취득금액", None, "비고(선택)",
]  # fmt: skip
C2_SUB: Final[list[Any]] = [None, None, None, None, None, None, "연간취득금액(원)", "1월", "2월", None]
C2_ITEMS: Final[tuple[tuple[str, str, int], ...]] = (
    ("차량", "1톤 화물차", 38_000_000),
    ("차량", "전동 지게차", 52_000_000),
    ("기계장치", "표면실장 장비", 640_000_000),
    ("기계장치", "공기압축기", 85_000_000),
    ("건물", "창고 증축 공사", 1_200_000_000),
    ("비품", "서버 랙", 46_000_000),
)


def scope3_rows(limit: int | None = None) -> tuple[dict[int, list[Any]], dict[int, list[Any]]]:
    """(C1 행, C2 행). limit 을 주면 C1 만 그 줄 수, C2 는 1줄(탐침)."""
    c1_items = C1_ITEMS if limit is None else C1_ITEMS[:limit]
    c2_items = C2_ITEMS if limit is None else C2_ITEMS[:1]
    c1: dict[int, list[Any]] = {4: list(C1_HEAD), 5: list(C1_SUB)}
    for n, (group, name, amount) in enumerate(c1_items, start=6):
        c1[n] = ["대한민국", "가상 본사", group, name, None, amount]
    c2: dict[int, list[Any]] = {4: list(C2_HEAD), 5: list(C2_SUB)}
    for n, (kind, name, amount) in enumerate(c2_items, start=6):
        c2[n] = ["대한민국", "가상 공장", kind, name, None, None, amount]
    return c1, c2


# ── 원고작성 v2: 목차와 쪽 글(esg-app drafting 시험 support 의 꼴) ──

DRAFT_OUTLINE: Final[dict[str, Any]] = {
    "nodes": [
        {"key": "env", "parentKey": None, "depth": 1, "title": "환경", "requirement": ""},
        {"key": "ghg", "parentKey": "env", "depth": 2, "title": "온실가스 배출", "requirement": "Scope 1·2 배출량과 감축 실적"},
        {"key": "energy", "parentKey": "env", "depth": 2, "title": "에너지", "requirement": "에너지 사용량과 재생에너지 비율"},
        {"key": "safety", "parentKey": None, "depth": 1, "title": "안전보건", "requirement": "재해율과 예방 활동"},
    ]
}
DRAFT_PAGES: Final[tuple[str, ...]] = (
    "가상전자 지속가능경영 자료 2024",
    "2024년 Scope 1 배출량은 41,200 tCO2eq, Scope 2 배출량은 87,200 tCO2eq이다. 전년 대비 합계 6.2% 줄었다.",
    "공정 냉매를 저온난화 냉매로 바꾸어 연 3,100 tCO2eq를 줄였다. 2030년까지 2021년 대비 42% 감축이 목표다.",
    "전력 사용량은 381,020 MWh이고 재생에너지 비율은 38%다. 태양광 12.4 MW를 새로 설치했다.",
    "근로손실재해율(LTIR)은 0.21로 전년 0.27에서 낮아졌다. 위험성평가를 분기마다 하고 협력사 작업자도 포함한다.",
    "안전 교육 이수율은 99.2%다. 아차사고 신고 1,284건을 받아 전부 조치했다.",
)
