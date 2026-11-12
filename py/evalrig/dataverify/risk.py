"""데이터 시험지 ③ 리스크 짝 — 3년 계열 변동을 짝으로 심는다(발주서 EVB §2-8).

짝 하나 = 같은 꼴 계열 둘(값은 배율만 다름 — 해마다 비가 같다). 하나는 `메모` 칸에 설명이 있고 하나는 없다.
정답: 설명 없는 변동 = `flag_no_note`(내야 함) · 설명 있는 변동 = `explained`(내면 거짓 양성) · 변동 없음 = `none`.
연도 칸은 변동 해(엇갈린 추세는 마지막 해, 변동 없음은 null). VM5 「주석 없음만 냄」 비율의 분모 = 짝 수.
가상 기업은 시험지 ①과 같은 「가상상사」 머리, 행은 따로 짓는다(① 의 난수 계열에 튐이 섞이지 않게).
"""
from __future__ import annotations

from typing import Any

from evalrig.dataverify import gold as G
from evalrig.dataverify.synth import YEARS, _edk

# 짝: (짝 id, 종, 변동 해, 설명 없는 행, 설명 있는 행, 바탕 계열, 둘째 행 배율, 메모)
#   행 = (ID, 테마, 항목, 대분류, 중분류, 소분류, 단위)
PAIRS = [
    ("J1", "jump_up", "2024",
     ("E-101", "환경", "가상 폐수 방류량", "용수", "방류", "폐수 방류량", "㎥"),
     ("E-102", "환경", "가상 지정폐기물 발생량", "폐기물", "발생량", "지정폐기물", "톤"),
     (20000, 20400, 29580), 0.02, "2024년 신규 공장 가동으로 증가"),
    ("J2", "jump_down", "2023",
     ("E-103", "환경", "가상 용수 취수량", "용수", "취수", "용수 취수량", "㎥"),
     ("E-104", "환경", "가상 LNG 사용량", "에너지", "연료", "LNG", "N㎥"),
     (30000, 18000, 18300), 2.0, "2023년 사업장 매각으로 감소"),
    ("D1", "diverging_trend", "2024",
     ("E-105", "환경", "가상 전력 사용량", "에너지", "에너지 사용량", "전력", "MWh"),
     ("E-106", "환경", "가상 스팀 사용량", "에너지", "에너지 사용량", "스팀", "톤"),
     (10000, 11200, 12544), 0.5, "2023~2024년 설비 증설로 생산량과 달리 증가"),
    ("B1", "blank", "2023",
     ("S-101", "사회", "가상 산업재해율", "안전보건", "재해", "산업재해율", "%"),
     ("S-102", "사회", "가상 1인당 교육시간", "인재", "교육", "1인당 교육시간", "시간"),
     (0.42, None, 0.38), 100.0, "2023년 집계 기준 변경으로 미집계"),
    ("B2", "blank", "2024",
     ("G-101", "지배구조", "가상 이사회 개최 횟수", "이사회", "운영", "개최 횟수", "회"),
     ("G-102", "지배구조", "가상 윤리교육 이수율", "윤리", "교육", "윤리교육 이수율", "%"),
     (8, 9, None), 12.0, "2024년 자료 집계 중"),
]
# 변동 없음(정답 none). D1 의 「생산량과 달리」를 받치는 생산량 줄 포함.
STEADY = [
    (("E-100", "환경", "가상 생산량", "경영 일반", "생산", "생산량", "톤"), (50000, 48000, 46000)),
    (("E-107", "환경", "가상 냉매 사용량", "기후", "냉매", "냉매 사용량", "kg"), (300, 305, 310)),
    (("S-103", "사회", "가상 임직원 수", "임직원", "인원", "총 임직원 수", "명"), (1000, 1020, 1050)),
]


def _scaled(series, k: float) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for y, v in zip(YEARS, series):
        out[y] = None if v is None else _num(v * k)
    return out


def _num(x: float) -> float | int:
    r = round(x, 4)
    return int(r) if float(r).is_integer() else r


def build() -> tuple[dict[str, Any], dict[str, Any]]:
    data: dict[str, list[dict[str, Any]]] = {"환경": [], "사회": [], "지배구조": []}
    items: list[dict[str, Any]] = []

    def put(row, values, memo, verdict, year, kind, pair):
        i, theme, name, a, b, c, unit = row
        data[theme].append(_edk(i, theme, name, a, b, c, unit, values, "", memo))
        items.append({"indicator": i, "year": year, "verdict": verdict, "kind": kind, "accept": [], "pair": pair})

    for row, series in STEADY:
        put(row, _scaled(series, 1.0), "", "none", None, None, None)
    for pid, kind, year, bare, noted, series, k, memo in PAIRS:
        put(bare, _scaled(series, 1.0), "", "flag_no_note", year, kind, pid)
        put(noted, _scaled(series, k), memo, "explained", year, kind, pid)
    inp = {
        "schemaVersion": "dataverify.input@1",
        "company": {
            "name": "가상상사(지은 기업)",
            "sasbIndustry": "가상 제조업",
            "materialTopics": ["기후변화 대응", "자원순환"],
            "reportingYear": YEARS[-1],
        },
        "data": data,
    }
    conventions = {
        "labels": ["flag_no_note", "explained", "none"],
        "kinds": ["jump_up", "jump_down", "diverging_trend", "blank"],
        "rules": [
            "튐 = 앞 해 대비 ±30% 이상(J1 +45%, J2 −40%). 엇갈린 추세 = 생산량(E-100)이 줄 때 해마다 +12%(D1).",
            "빈칸 = 그 해 값 null(B1 사회, B2 지배구조). 1차 흐름은 「환경」만 검토하므로 B1·B2 는 1차가 볼 수 없는 칸이다.",
            "짝의 두 행은 값의 해마다 비가 같고(배율만 다름) 메모만 다르다. 메모 있는 쪽을 내면 거짓 양성.",
            "연도 = 변동 해. 엇갈린 추세는 마지막 해, 변동 없음은 null.",
        ],
        "accept": "items[].accept — 이 셋에는 없음.",
    }
    meta = {"input": "synthetic-risk.input.json", "pairs": len(PAIRS), "years": list(YEARS)}
    gs = G.header(
        set_id="dataverify.synthetic-risk",
        coverage="exhaustive",
        synthetic=True,
        sources=["evalrig.dataverify.risk(손으로 쓴 계열, 난수 0)"],
        conventions=conventions,
        meta=meta,
    )
    meta["inputSha16"] = G.sha16_bytes(G.dumps_input(inp).encode("utf-8"))
    gs["items"] = items
    return inp, gs
