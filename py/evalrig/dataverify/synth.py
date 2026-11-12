"""데이터 시험지 ① 가상 기업 확장판 — 서는 관계 위에 오류 다섯 종을 심는다(발주서 EVB §2-6).

바탕 꼴은 `prework/dataverify-demo/` 의 지은 가상 기업(EDK 행 11키)이다. 그 파일은 복사하지 않고 같은 꼴을
코드로 짓는다. 값은 seed 고정 난수, 심는 자리는 손으로 고른 표(`PLANTS`)다 — 같은 (관계, 연도)에 심은 칸이
둘이 되지 않게 골랐다(시험 T5 가 못박음).

참값(true)은 모든 관계가 서는 표다. 보고값(reported)은 참값에서 심은 칸만 바꾼 것이다. 정답은 「환경」 테마의
(지표, 연도) 모든 칸에 `FAIL{kind}` | `OK` 를 단다(1차 흐름은 「환경」만 검토 — 코랩 셀 3).
"""
from __future__ import annotations

import random
from typing import Any

from evalrig.dataverify import gold as G

SEED = 20261109
YEARS = ("2022", "2023", "2024")
TOLERANCE = 1.1  # 1차(colab_156) 검사 도구의 허용 오차 — 절댓값(합은 단위 그대로, 비율은 %p). prompts.py TOLERANCE
RATIO_DP = 2  # 비율(%) 보고 자리
INTENSITY_DP = 4  # 집약도 보고 자리

# (ID, 항목, 대분류, 중분류, 소분류, 단위, 핵심데이터)
ROWS_E = [
    ("E-001", "가상 직접 배출량", "온실가스 배출량", "직접 배출량", "Scope 1", "tCO2eq", "Y"),
    ("E-002", "가상 간접 배출량", "온실가스 배출량", "간접 배출량", "Scope 2", "tCO2eq", "Y"),
    ("E-003", "가상 총 배출량", "온실가스 배출량", "총 배출량", "Scope 1+2", "tCO2eq", "Y"),
    ("E-004", "가상 총 에너지 사용량", "에너지", "에너지 사용량", "총 에너지", "MWh", "Y"),
    ("E-005", "가상 재생에너지 사용량", "에너지", "에너지 사용량", "재생에너지", "MWh", ""),
    ("E-006", "가상 비재생에너지 사용량", "에너지", "에너지 사용량", "비재생에너지", "MWh", ""),
    ("E-007", "가상 재생에너지 비율", "에너지", "재생에너지", "재생에너지 비율", "%", ""),
    ("E-008", "가상 매출액", "경영 일반", "재무", "매출액", "십억 원", ""),
    ("E-009", "가상 배출 집약도", "온실가스 배출량", "집약도", "매출당 배출량", "tCO2eq/십억 원", ""),
    ("E-010", "가상 폐기물 발생량", "폐기물", "발생량", "폐기물 발생량", "톤", ""),
    ("E-011", "가상 폐기물 재활용량", "폐기물", "처리량", "재활용", "톤", ""),
    ("E-012", "가상 폐기물 매립량", "폐기물", "처리량", "매립", "톤", ""),
    ("E-013", "가상 폐기물 재활용률", "폐기물", "재활용", "재활용률", "%", ""),
    ("E-014", "가상 생산량", "경영 일반", "생산", "생산량", "톤", ""),
    ("E-015", "가상 에너지 집약도", "에너지", "집약도", "생산량당 에너지", "MWh/톤", ""),
    ("E-016", "가상 용수 사용량", "용수", "사용량", "용수 사용량", "㎥", ""),
    ("E-017", "가상 용수 집약도", "용수", "집약도", "매출당 용수", "㎥/십억 원", ""),
]
ROWS_FILL = {  # 1차 검토 밖(「환경」만 검토). 정답 칸 0.
    "사회": [
        ("S-001", "가상 임직원 수", "임직원", "인원", "총 임직원 수", "명", ""),
        ("S-002", "가상 여성 임직원 수", "임직원", "인원", "여성 임직원 수", "명", ""),
    ],
    "지배구조": [
        ("G-001", "가상 이사회 개최 횟수", "이사회", "운영", "개최 횟수", "회", ""),
    ],
}

# 서는 관계. rule: sum(addends 합 = target) · ratio(num/den×100 = target, %) · intensity(num/den = target)
RELATIONS = [
    {"id": "sum1", "rule": "sum", "target": "E-003", "addends": ["E-001", "E-002"]},
    {"id": "sum2", "rule": "sum", "target": "E-004", "addends": ["E-005", "E-006"]},
    {"id": "sum3", "rule": "sum", "target": "E-010", "addends": ["E-011", "E-012"]},
    {"id": "ratio1", "rule": "ratio", "target": "E-007", "num": "E-005", "den": "E-004"},
    {"id": "ratio2", "rule": "ratio", "target": "E-013", "num": "E-011", "den": "E-010"},
    {"id": "int1", "rule": "intensity", "target": "E-009", "num": "E-003", "den": "E-008"},
    {"id": "int2", "rule": "intensity", "target": "E-015", "num": "E-004", "den": "E-014"},
    {"id": "int3", "rule": "intensity", "target": "E-017", "num": "E-016", "den": "E-008"},
]

# 심는 칸: (ID, 연도, 종, 어떻게). 종 다섯 × 둘 + 허용 오차 경계 둘.
PLANTS = [
    ("E-003", "2023", "sum", ("scale", 1.03)),
    ("E-012", "2023", "sum", ("scale", 0.95)),
    ("E-007", "2024", "ratio", ("add", 6.0)),
    ("E-013", "2022", "ratio", ("add", -4.0)),
    ("E-009", "2022", "intensity", ("scale", 1.10)),
    ("E-017", "2024", "intensity", ("scale", 0.85)),
    ("E-005", "2022", "unit_slip", ("scale", 0.001)),  # GWh 값을 MWh 줄에
    ("E-016", "2023", "unit_slip", ("scale", 1000.0)),  # 천㎥ 를 ㎥ 로 잘못 곱함
    ("E-015", "2023", "denominator_basis", ("rebase", "E-008")),  # 생산량 대신 매출로 나눔
    ("E-009", "2024", "denominator_basis", ("rebase", "E-014")),  # 매출 대신 생산량으로 나눔
    ("E-006", "2024", "sum", ("add", 1.2)),  # 허용 오차 경계 — 넘음(1차 FAIL)
    ("E-002", "2022", "tolerance_edge", ("add", 1.0)),  # 허용 오차 경계 — 안 넘음(1차 OK)
]
EDGE = {("E-006", "2024"): "over", ("E-002", "2022"): "under"}
KINDS = ("sum", "ratio", "intensity", "unit_slip", "denominator_basis")


def relation_members(rel: dict[str, Any]) -> list[str]:
    if rel["rule"] == "sum":
        return [*rel["addends"], rel["target"]]
    return [rel["num"], rel["den"], rel["target"]]


def relation_value(rel: dict[str, Any], vals: dict[str, dict[str, float]], y: str) -> float:
    """관계가 target 에 주는 값(참값 표로 부르면 참값)."""
    if rel["rule"] == "sum":
        return sum(vals[a][y] for a in rel["addends"])
    q = vals[rel["num"]][y] / vals[rel["den"]][y]
    return q * 100.0 if rel["rule"] == "ratio" else q


def relation_dp(rel: dict[str, Any]) -> int | None:
    return {"sum": None, "ratio": RATIO_DP, "intensity": INTENSITY_DP}[rel["rule"]]


def _round(x: float, dp: int | None) -> float | int:
    return int(round(x)) if dp is None else round(x, dp)


def true_table(seed: int = SEED) -> dict[str, dict[str, float]]:
    rng = random.Random(seed)
    v: dict[str, dict[str, float]] = {}

    def leaf(i: str, lo: int, hi: int, step: int = 1) -> None:
        v[i] = {y: rng.randint(lo, hi) * step for y in YEARS}

    leaf("E-001", 1100, 1300, 10)
    leaf("E-002", 400, 480, 10)
    leaf("E-005", 60, 140, 100)
    leaf("E-006", 380, 460, 100)
    leaf("E-008", 480, 560)
    leaf("E-011", 150, 210, 10)
    leaf("E-012", 60, 110, 10)
    leaf("E-014", 380, 440, 100)
    leaf("E-016", 700, 900, 100)
    for rel in RELATIONS:  # 차례대로 — sum 이 ratio·intensity 의 재료를 먼저 짓는다
        v[rel["target"]] = {y: _round(relation_value(rel, v, y), relation_dp(rel)) for y in YEARS}
    return v


def reported_table(true: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    rep = {i: dict(ys) for i, ys in true.items()}
    for i, y, _kind, (how, arg) in PLANTS:
        dp = _dp_of(i)
        if how == "scale":
            rep[i][y] = _round(true[i][y] * arg, dp if dp is not None else (3 if arg < 0.01 else None))  # 소수는 GWh 꼴 단위 미끄러짐만
        elif how == "add":
            rep[i][y] = round(true[i][y] + arg, 4)
        elif how == "rebase":
            rel = next(r for r in RELATIONS if r["target"] == i)
            rep[i][y] = _round(true[rel["num"]][y] / true[arg][y], dp)
        else:  # pragma: no cover
            raise ValueError(how)
    return rep


def _dp_of(i: str) -> int | None:
    rel = next((r for r in RELATIONS if r["target"] == i), None)
    return relation_dp(rel) if rel else None


def _fill_values(rng: random.Random) -> dict[str, int]:
    return {y: rng.randint(10, 99) * 10 for y in YEARS}


def build(seed: int = SEED) -> tuple[dict[str, Any], dict[str, Any]]:
    """(input, gold) 두 문서."""
    true = true_table(seed)
    rep = reported_table(true)
    fill_rng = random.Random(seed + 1)
    data: dict[str, list[dict[str, Any]]] = {"환경": []}
    for (i, name, a, b, c, unit, core) in ROWS_E:
        data["환경"].append(_edk(i, "환경", name, a, b, c, unit, rep[i], core))
    for theme, rows in ROWS_FILL.items():
        data[theme] = [_edk(i, theme, name, a, b, c, unit, _fill_values(fill_rng), core)
                       for (i, name, a, b, c, unit, core) in rows]
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
    planted = {(i, y): (kind, how) for i, y, kind, how in PLANTS}
    items = []
    for (i, *_rest) in ROWS_E:
        for y in YEARS:
            p = planted.get((i, y))
            edge = EDGE.get((i, y))
            if p is None or edge == "under":
                verdict, kind = "OK", ("tolerance_edge" if edge else None)
            else:
                verdict, kind = "FAIL", p[0]
            items.append({
                "indicator": i,
                "year": y,
                "verdict": verdict,
                "kind": kind,
                "accept": ["FAIL"] if edge == "under" else [],
                "edge": edge,
                "reported": rep[i][y],
                "true": true[i][y],
                "relations": [r["id"] for r in RELATIONS if i in relation_members(r)],
            })
    conventions = {
        "labels": ["FAIL", "OK"],
        "kinds": list(KINDS),
        "rules": [
            "참값 = 모든 관계가 서는 표. 보고값 = 참값에서 심은 칸만 바꾼 것. 심은 칸은 FAIL{kind}, 나머지는 OK.",
            "FAIL 은 심은 칸 하나에만 단다. 그 칸이 든 관계(relations)는 그 해에 다 어긋나지만 다른 칸은 OK 다.",
            "허용 오차 = 1.1(1차 colab_156 의 TOLERANCE, 절댓값 — 합은 단위 그대로, 비율은 %p). 경계 짝: "
            "edge=over 는 참값과 차 1.2 라 FAIL, edge=under 는 차 1.0 이라 OK(FAIL 도 accept).",
            "비율은 소수 2자리, 집약도는 소수 4자리로 보고한다 — 심지 않은 칸의 「차 0」은 그 자리의 반올림 안.",
            "정답 칸은 「환경」 테마만(1차 흐름이 「환경」만 검토). 「사회」·「지배구조」 행은 거르기 확인용.",
            "intensity 는 1차 ratio 도구(×100)로 맞게 잴 도구가 없다(prework/dataverify-demo 심은 오류 답 md).",
        ],
        "accept": "items[].accept — 판단이 갈리는 자리에서 같이 맞다고 볼 판정. 돌리기 전에 정했다.",
    }
    meta = {
        "input": "synthetic-company.input.json",
        "seed": seed,
        "years": list(YEARS),
        "relations": RELATIONS,
        "tolerance": TOLERANCE,
    }
    gs = G.header(
        set_id="dataverify.synthetic-company",
        coverage="exhaustive",
        synthetic=True,
        sources=[f"evalrig.dataverify.synth seed={seed}",
                 "꼴 바탕: prework/dataverify-demo/데이터검증-시연입력-가상기업-data.json(복사 0)"],
        conventions=conventions,
        meta=meta,
    )
    meta["inputSha16"] = G.sha16_bytes(G.dumps_input(inp).encode("utf-8"))
    gs["items"] = items
    return inp, gs


def _edk(i, theme, name, a, b, c, unit, values, core, memo: str = "") -> dict[str, Any]:
    return {
        "ID": i, "테마": theme, "항목": name, "대분류": a, "중분류": b, "소분류": c,
        "입력 방식": "직접 입력", "단위": unit, "values": dict(values), "핵심데이터": core, "메모": memo,
    }
