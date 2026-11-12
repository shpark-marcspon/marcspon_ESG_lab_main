"""조립용 대응표: 계수 표의 낱줄을 지침의 규칙대로 묶을 때 필요한 「값 하나」가 아닌 기준정보.

- sectors / sector_overlaps: 별표 10 의 부문 정의 (한국표준산업분류 중분류 코드 → CH4·N2O 계수의 부문)
- fuel_names: 국내 연료 이름 → 별표 10·11 의 IPCC 연료 이름 (그 표의 「국내에너지원 기준」 열)
- fuel_states: 연료 → 고체·기체·액체 (별표 6 의 어느 고정연소 절인가. 산화계수가 이것으로 갈린다)
- tier_minimums: 별표 5 의 시설 규모 그룹(A·B·C)별 매개변수별 최소 산정등급 (고정연소)

전부 원자료에서 읽거나, 원자료에 그 문장이 있는지 확인한 뒤에만 싣는다.
지침에 없는 대응은 ASSUMED_* 에 까닭과 함께 적고 `basis: "assumed"` 로 싣는다. 쓰는 쪽은 이 표시를 결과의 근거에 남겨야 한다.
형식은 `schemas/factor-mapping.schema.json`.

MARC-TICK-18 에서 더한 것:
- national_ef_names: 별표 12 안에서 발열량 표와 배출계수 표가 같은 연료를 다르게 적는 것 (항공유 / 항공유(JET-A1))
- fuel_names 의 parameters: 그 대응이 일부 매개변수에만 쓰일 때 (부생연료 1·2호의 CO2 Tier 1)
- fuel_names 의 줄바꿈 셀 세 건은 건별 판단(LINE_BREAK_CELLS), 행 이름이 곧 국내 이름인 별표 10·11 의 B-A유·B-B유(ROW_NAME_FUELS)
- evidence: 추정의 근거 문장. 원자료의 그 쪽에 있는지 적재 때 확인한다
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

import pymupdf
from jsonschema import Draft202012Validator

from .loaders import kr_ets, kr_ets_byl06
from .sources import MAPPING_SCHEMA_PATH, SOURCES, SourceFormatError

MAPPING_NAME = "mappings.kr-ets.json"
MAPPING_SCHEMA_VERSION = "0.1"

BYL05 = "kr-ets-byl05"
#: 계수 레코드는 없고 대응표에만 쓰이는 출처
MAPPING_ONLY_SOURCES = frozenset({BYL05})

DOMESTIC_KEY = "국내에너지원 기준"

#: 별표 10 의 부문 이름 → 조립 조건과 엔진이 쓰는 키. 이름이 이 표에 없으면 적재가 실패한다.
SECTOR_KEYS = {
    "에너지산업": "energy_industries",
    "제조업ㆍ건설업": "manufacturing_construction",
    "상업ㆍ공공": "commercial_public",
    "가정ㆍ기타": "residential_other",
}

BYL12 = kr_ets.BYL12

#: 칸 안에 줄바꿈이 있는 「국내에너지원 기준」 칸. 기계 규칙으로 가르지 않고 건별로 판단해 적는다.
#: (표, IPCC 연료명) → (원문 칸 그대로, 국내 이름들, 판단 근거). 원문 칸이 이 글자와 다르면 적재가 실패한다.
LINE_BREAK_CELLS = {
    (kr_ets.BYL10, "무연탄"): (
        "국내 무연탄\n수입 무연탄",
        ["국내 무연탄", "수입 무연탄"],
        "두 이름으로 갈랐다. (1) 같은 고시의 hwp 원본(ets-byl10.hwp)에서 이 칸은 문단 둘(「국내 무연탄」, 「수입 무연탄」)이다. "
        "칸 폭 때문에 꺾인 한 문단이 아니다. (2) 별표 12 가 국내무연탄과 수입무연탄을 다른 행(다른 발열량·배출계수)으로 둔다. "
        "(3) 「국내 무연탄 수입 무연탄」 이라는 한 연료는 어느 별표에도 없다.",
    ),
    (kr_ets.BYL10, "가스 코크스"): (
        "가스공장\n코크스",
        ["가스공장 코크스"],
        "꺾인 한 이름으로 읽었다. (1) hwp 원본에서 이 칸은 문단 하나(「가스공장 코크스」)이고 PDF 의 줄바꿈은 칸 폭 때문이다. "
        "(2) 별표 10 의 [참고] 연료 정의가 「가스 코크스(가스공장 코크스)(Gas Coke)」 라고 한 이름으로 적는다. "
        "(3) 「가스공장」 만으로는 연료가 아니다. 별표 12 에는 이 연료가 없어 조립에는 쓰이지 않는다.",
    ),
    (kr_ets.BYL11, "무연탄"): (
        "국내 무연탄\n수입 무연탄",
        ["국내 무연탄", "수입 무연탄"],
        "별표 10 의 무연탄 칸과 같은 판단. hwp 원본(ets-byl11.hwp)에서도 문단 둘이다.",
    ),
}

#: 「국내에너지원 기준」 열 없이 행 이름이 곧 국내 이름인 연료 (별표 10 3쪽·별표 11 2쪽의 B-A유·B-B유 표, 각 표의 주2).
ROW_NAME_FUELS = {kr_ets.BYL10: ("B-A유", "B-B유"), kr_ets.BYL11: ("B-A유", "B-B유")}
ROW_NAME_NOTE = (
    "이 표(주2 아래의 B-A유·B-B유 표)에는 「국내에너지원 기준」 열이 없고 행 이름이 별표 12 의 국내 이름과 글자까지 같다. "
    "값은 주2 의 혼합비 규칙으로 고시가 계산해 실은 것이다."
)


def _ev(source_id: str, page: int, quote: str) -> dict:
    """추정의 근거 한 자리. quote 는 그 쪽 글자에 공백을 뺀 채로 있어야 한다 (적재 때 확인)."""
    return {"source_id": source_id, "locator": f"PDF {page}쪽", "quote": quote}


_REF3 = _ev(BYL12, 3, "LPG 사용에 따른 고정연소 배출활동의 경우, 프로판에 따른 발열량을 활용하여")
_REF4 = _ev(BYL12, 3, "LPG 사용에 따른 이동연소(도로) 배출활동의 경우, 부탄에 따른 발열량을 활용하여")
_BOTH = (kr_ets.BYL10, kr_ets.BYL11)

#: 지침에 글자 그대로 없는 이름 대응 (추정). 표마다 IPCC 연료명이 다를 수 있다.
#: parameters 가 있으면 그 매개변수(별표 10 의 기체)에만 쓰인다. evidence 는 까닭의 근거 문장 자리다.
ASSUMED_FUEL_NAMES = [
    {
        "domestic": "도시가스(LNG)",
        "ipcc": {kr_ets.BYL10: "천연가스", kr_ets.BYL11: "천연가스"},
        "parameters": None,
        "note": "별표 10·11 의 「국내에너지원 기준」 열에는 천연가스(LNG) 만 있고 도시가스(LNG) 는 없다. "
        "별표 12 의 배출계수 표에서 도시가스(LNG) 가 천연가스(LNG) 와 CO2 계수 칸을 같이 쓰는 것(병합 셀)을 근거로 "
        "같은 IPCC 연료(천연가스)로 보았다. 지침의 문장으로 확인한 대응이 아니다.",
        "evidence": [],
    },
    {
        "domestic": "프로판(LPG1호)",
        "ipcc": {kr_ets.BYL10: "액화석유가스", kr_ets.BYL11: "액화석유가스"},
        "parameters": None,
        "note": "별표 10·11 의 국내에너지원 기준은 「LPG」 하나이고 프로판·부탄으로 가르지 않는다. 별표 12 의 이름이 「프로판(LPG1호)」 이고, "
        "별표 12 [참고] 3) 이 LPG 의 고정연소에 프로판의 발열량을 쓰라고 한다(LPG 와 프로판을 잇는 문장). 프로판을 LPG 로 본 것은 추정이다.",
        "evidence": [_REF3, _ev(kr_ets.BYL10, 1, "액화석유가스 LPG")],
    },
    {
        "domestic": "부탄(LPG3호)",
        "ipcc": {kr_ets.BYL10: "액화석유가스", kr_ets.BYL11: "액화석유가스"},
        "parameters": None,
        "note": "별표 12 의 이름이 「부탄(LPG3호)」 이고 [참고] 4) 가 LPG 의 이동연소에 부탄의 발열량을 쓰라고 한다. "
        "고정연소의 부탄을 별표 10·11 의 LPG(액화석유가스)로 본 것은 추정이다.",
        "evidence": [_REF4, _ev(kr_ets.BYL10, 1, "액화석유가스 LPG")],
    },
    {
        "domestic": "나프타",
        "ipcc": {kr_ets.BYL10: "나프타", kr_ets.BYL11: "나프타"},
        "parameters": None,
        "note": "별표 10·11 의 그 행은 연료명이 「나프타」, 국내에너지원 기준이 「납사」 다. 별표 12 는 「나프타」 라고 적는다. "
        "같은 행의 IPCC 연료명이 글자까지 같지만 국내에너지원 기준 열의 이름은 아니어서 추정으로 둔다.",
        "evidence": [_ev(kr_ets.BYL10, 1, "나프타 납사"), _ev(kr_ets.BYL11, 1, "나프타 납사")],
    },
    {
        "domestic": "항공유",
        "ipcc": {kr_ets.BYL10: "제트용 등유", kr_ets.BYL11: "제트용 등유"},
        "parameters": None,
        "note": "별표 12 의 발열량 표는 「항공유」, 배출계수 표는 「항공유(JET-A1)」 이고, 별표 10·11 의 제트용 등유 행의 국내에너지원 기준이 「JET A-1, JP-8」 이다. "
        "괄호의 JET-A1 을 JET A-1 로 보아 이었다. 항공유가 JP-8 등을 포함하는지는 지침이 말하지 않는다.",
        "evidence": [_ev(BYL12, 3, "항공유(JET-A1)"), _ev(kr_ets.BYL10, 1, "JET A-1, JP-8")],
    },
    {
        "domestic": "석유코크스",
        "ipcc": {kr_ets.BYL10: "석유 코크스", kr_ets.BYL11: "석유 코크스"},
        "parameters": None,
        "note": "별표 10 의 국내에너지원 기준은 「석유코크스(고체)」, 별표 11 은 「석유코크」 로 별표 12 의 「석유코크스」 와 글자가 다르다. 같은 연료로 보았다.",
        "evidence": [_ev(kr_ets.BYL10, 1, "석유 코크스 석유코크스(고체)"), _ev(kr_ets.BYL11, 1, "석유 코크스 석유코크")],
    },
    {
        "domestic": "국내무연탄",
        "ipcc": {kr_ets.BYL10: "무연탄", kr_ets.BYL11: "무연탄"},
        "parameters": None,
        "note": "별표 10·11 의 무연탄 칸의 「국내 무연탄」(LINE_BREAK_CELLS 의 판단)과 별표 12 의 「국내무연탄」 은 띄어쓰기만 다르다.",
        "evidence": [_ev(kr_ets.BYL10, 2, "국내 무연탄"), _ev(kr_ets.BYL11, 1, "국내 무연탄")],
    },
    {
        "domestic": "연료용 수입무연탄",
        "ipcc": {kr_ets.BYL10: "무연탄", kr_ets.BYL11: "무연탄"},
        "parameters": None,
        "note": "별표 10·11 은 「수입 무연탄」 하나이고 연료용·원료용을 가르지 않는다. 별표 12 의 연료용 수입무연탄을 그 수입 무연탄으로 보았다.",
        "evidence": [_ev(kr_ets.BYL10, 2, "수입 무연탄"), _ev(kr_ets.BYL11, 1, "수입 무연탄")],
    },
    {
        "domestic": "원료용 수입무연탄",
        "ipcc": {kr_ets.BYL10: "무연탄", kr_ets.BYL11: "무연탄"},
        "parameters": None,
        "note": "별표 10·11 은 「수입 무연탄」 하나이고 연료용·원료용을 가르지 않는다. 별표 12 의 원료용 수입무연탄을 그 수입 무연탄으로 보았다.",
        "evidence": [_ev(kr_ets.BYL10, 2, "수입 무연탄"), _ev(kr_ets.BYL11, 1, "수입 무연탄")],
    },
    {
        "domestic": "연료용 유연탄(역청탄)",
        "ipcc": {kr_ets.BYL10: "기타 역청탄", kr_ets.BYL11: "기타 역청탄"},
        "parameters": None,
        "note": "별표 10·11 의 국내에너지원 기준은 「연료용 유연탄」, 별표 12 는 「연료용 유연탄(역청탄)」 이다. 괄호만 다르다.",
        "evidence": [_ev(kr_ets.BYL10, 2, "기타 역청탄 연료용 유연탄"), _ev(kr_ets.BYL11, 1, "기타 역청탄 연료용 유연탄")],
    },
    {
        "domestic": "원료용 유연탄(역청탄)",
        "ipcc": {kr_ets.BYL10: "점결탄", kr_ets.BYL11: "점결탄(Coking coal)"},
        "parameters": None,
        "note": "별표 10·11 의 국내에너지원 기준은 「원료용 유연탄」, 별표 12 는 「원료용 유연탄(역청탄)」 이다 (별표 12 원본에서 「(역청탄)」 은 같은 칸의 둘째 문단이고 PDF 에서는 다음 쪽으로 넘어간다).",
        "evidence": [_ev(kr_ets.BYL10, 2, "점결탄 원료용 유연탄"), _ev(kr_ets.BYL11, 1, "원료용 유연탄")],
    },
    {
        "domestic": "코크스",
        "ipcc": {kr_ets.BYL10: "코크스로 코크스"},
        "parameters": None,
        "note": "별표 10 의 국내에너지원 기준은 「코크스(석탄)」 이고 별표 12 는 「코크스」 다. 별표 11 은 같은 IPCC 연료(코크스로 코크스)의 국내 이름을 「코크스」 로 적어 그쪽은 지침의 대응이다. "
        "별표 10 에서 같은 「코크스」 묶음의 가스 코크스(가스공장 코크스)가 아니라 코크스로 코크스로 본 것은 별표 11 을 따른 추정이다.",
        "evidence": [_ev(kr_ets.BYL10, 2, "코크스로 코크스 코크스(석탄)"), _ev(kr_ets.BYL11, 1, "코크스로 코크스 코크스")],
    },
    {
        "domestic": "부생연료유1호",
        "ipcc": {kr_ets.BYL10: "기타 등유"},
        "parameters": ["CO2"],
        "note": "별표 10 주3 「‘부생연료 1호’의 CO2 배출계수는 기타등유의 IPCC 기본 배출계수를 적용」과 별표 12 [참고] 1) 「산정등급 1(Tier 1) CO2 배출계수는 등유 계수를 활용」 의 규칙이다. "
        "CO2 에만 쓴다 (CH4·N2O 는 두 문장 모두 말하지 않는다). 추정인 것은 이름이다: 주3·참고는 「부생연료 1호」, 별표 12 발열량 표는 「부생연료유1호」 라고 적는다.",
        "evidence": [_ev(kr_ets.BYL10, 3, "‘부생연료 1호’의 CO2 배출계수는 기타등유의 IPCC 기본 배출계수를 적용하여 활용한다."),
                     _ev(BYL12, 2, "산정등급 1(Tier 1) CO2 배출계수는 등유 계수를 활용하여")],
    },
    {
        "domestic": "부생연료유2호",
        "ipcc": {kr_ets.BYL10: "잔여 연료유"},
        "parameters": ["CO2"],
        "note": "별표 10 주4 「‘부생연료 2호’의 CO2 배출계수는 B-C유의 IPCC 기본 배출계수를 적용」과 별표 12 [참고] 2) 의 규칙이다. 별표 10 에서 B-C유 의 행은 잔여 연료유다. "
        "CO2 에만 쓴다. 추정인 것은 이름이다 (주4·참고 「부생연료 2호」, 발열량 표 「부생연료유2호」).",
        "evidence": [_ev(kr_ets.BYL10, 3, "‘부생연료 2호’의 CO2 배출계수는 B-C유의 IPCC 기본 배출계수를 적용하여 활용한다."),
                     _ev(BYL12, 2, "산정등급 1(Tier 1) CO2 배출 계수는 B-C유 계수를 활용하여"),
                     _ev(kr_ets.BYL10, 1, "잔여 연료유 B-C유")],
    },
]

#: 별표 12 안에서 발열량 표와 배출계수 표의 이름이 다른 연료 (추정). (발열량 표 이름, 배출계수 표 이름, 까닭)
NATIONAL_EF_NAMES = [
    ("항공유", "항공유(JET-A1)", "발열량 표는 「항공유」, 배출계수 표는 「항공유(JET-A1)」 다. 두 표에 항공유는 이것 하나씩이다."),
    ("부탄(LPG3호)", "부탄(LPP3호)", "배출계수 표의 「부탄(LPP3호)」 는 발열량 표의 「부탄(LPG3호)」 의 오자로 보인다 (hwp 원본도 LPP3호). 원문 표기는 고치지 않고 대응으로 잇는다."),
    ("부생연료유1호", "부생연료 1호", "발열량 표는 「부생연료유1호」, 배출계수 표와 [참고] 1) 은 「부생연료 1호」 다."),
    ("부생연료유2호", "부생연료 2호", "발열량 표는 「부생연료유2호」, 배출계수 표와 [참고] 2) 는 「부생연료 2호」 다."),
    ("연료용 수입무연탄", "수입무연탄(연료용)", "발열량 표는 용도를 앞에, 배출계수 표는 괄호 안에 적는다."),
    ("원료용 수입무연탄", "수입무연탄(원료용)", "발열량 표는 용도를 앞에, 배출계수 표는 괄호 안에 적는다."),
    ("연료용 유연탄(역청탄)", "유연탄(연료용)", "발열량 표는 「연료용 유연탄(역청탄)」, 배출계수 표는 「유연탄(연료용)」 이다."),
    ("원료용 유연탄(역청탄)", "유연탄(원료용)", "발열량 표는 「원료용 유연탄(역청탄)」, 배출계수 표는 「유연탄(원료용)」 이다."),
]

#: 연료의 상태. quote 는 별표 6 의 그 절에 실제로 있어야 한다 (공백을 뺀 글자로 대조). 없으면 적재가 실패한다.
_LIQUID_QUOTE = "원유, 휘발유, 등유, 경유, B-A/B/C와 같은 액체 화석연료"
_GAS_QUOTE = "LNG, LPG, 프로판, 부탄 및 기타 부생가스 등 기체연료"
_SOLID_QUOTE = "무연탄, 유연탄, 갈탄, 코크스와 같은 고체 화석연료"
FUEL_STATES = [
    ("무연탄", "고체", "guideline", _SOLID_QUOTE, None),
    ("유연탄", "고체", "guideline", _SOLID_QUOTE, None),
    ("갈탄", "고체", "guideline", _SOLID_QUOTE, None),
    ("코크스", "고체", "guideline", _SOLID_QUOTE, None),
    ("LNG", "기체", "guideline", _GAS_QUOTE, None),
    ("LPG", "기체", "guideline", _GAS_QUOTE, None),
    ("프로판", "기체", "guideline", _GAS_QUOTE, None),
    ("부탄", "기체", "guideline", _GAS_QUOTE, None),
    ("원유", "액체", "guideline", _LIQUID_QUOTE, None),
    ("휘발유", "액체", "guideline", _LIQUID_QUOTE, None),
    ("등유", "액체", "guideline", _LIQUID_QUOTE, None),
    ("경유", "액체", "guideline", _LIQUID_QUOTE, None),
    (
        "도시가스(LNG)",
        "기체",
        "assumed",
        _GAS_QUOTE,
        "별표 6 의 문장은 LNG 를 기체연료로 들 뿐 「도시가스(LNG)」 라는 이름을 적지 않는다. LNG 로 만든 도시가스라는 이름 풀이에 기댄 추정이다.",
    ),
]

#: 별표 12 분류 말고 상태를 직접 적은 근거가 따로 있는 연료 (적재 때 원문의 그 쪽에 있는지 확인한다)
EXTRA_STATE_EVIDENCE = {
    "석유코크스": [_ev(kr_ets.BYL10, 1, "석유코크스(고체)"), _ev(kr_ets.BYL10, 6, "흑색 고체")],
}

#: 별표 12 발열량 표의 연료 가운데 위 FUEL_STATES 에 이름이 없는 것의 상태 (추정, MARC-TICK-18).
#: (연료, 상태, 별표 12 배출계수 표의 분류 또는 None, 까닭). 분류는 적재 때 계수 레코드의 category_path 와 대조한다.
_OIL, _GAS, _COAL = "석유(16)", "가스(3)", "석탄(6)"
_LIQ_WHY = "별표 12 배출계수 표가 석유(16)로 분류하고, 별표 6 의 기체연료 문장(LNG, LPG, 프로판, 부탄)에도 고체연료 문장에도 이름이 없어 액체연료 절로 보았다."
ASSUMED_FUEL_STATES = [
    ("B-A유", "액체", _OIL, "별표 6 액체연료 문장의 「B-A/B/C」 를 B-A유·B-B유·B-C유로 풀었다. 별표 12 도 석유(16)로 분류한다."),
    ("B-B유", "액체", _OIL, "별표 6 액체연료 문장의 「B-A/B/C」 를 B-A유·B-B유·B-C유로 풀었다. 별표 12 도 석유(16)로 분류한다."),
    ("B-C유", "액체", _OIL, "별표 6 액체연료 문장의 「B-A/B/C」 를 B-A유·B-B유·B-C유로 풀었다. 별표 12 도 석유(16)로 분류한다."),
    ("나프타", "액체", _OIL, _LIQ_WHY),
    ("용제", "액체", _OIL, _LIQ_WHY),
    ("항공유", "액체", _OIL, _LIQ_WHY),
    ("아스팔트", "액체", _OIL, _LIQ_WHY + " 다만 별표 12 의 단위는 MJ/kg 로 질량 기준이다."),
    ("윤활유", "액체", _OIL, _LIQ_WHY),
    ("부생연료유1호", "액체", _OIL, _LIQ_WHY + " 별표 12 [참고] 1) 은 「등유 성상에 해당하는 제품」 이라고 쓴다."),
    ("부생연료유2호", "액체", _OIL, _LIQ_WHY + " 별표 12 [참고] 2) 는 「등유ㆍ중유 성상에 해당하는 제품」 이라고 쓴다."),
    ("프로판(LPG1호)", "기체", _OIL, "별표 6 기체연료 문장이 「프로판」 을 기체연료로 든다. 별표 12 는 석유(16)로 분류해 두 근거가 어긋나며, 별표 6 의 문장을 따랐다."),
    ("부탄(LPG3호)", "기체", _OIL, "별표 6 기체연료 문장이 「부탄」 을 기체연료로 든다. 별표 12 는 석유(16)로 분류해 두 근거가 어긋나며, 별표 6 의 문장을 따랐다."),
    ("천연가스(LNG)", "기체", _GAS, "별표 6 기체연료 문장의 「LNG」 와 별표 12 의 가스(3) 분류가 같다. 이름(천연가스(LNG))이 문장에 그대로 있지는 않다."),
    ("도시가스(LPG)", "기체", _GAS, "별표 6 기체연료 문장의 「LPG」 와 별표 12 의 가스(3) 분류가 같다. 이름(도시가스(LPG))이 문장에 그대로 있지는 않다."),
    ("국내무연탄", "고체", _COAL, "별표 6 고체연료 문장의 「무연탄」 과 별표 12 의 석탄(6) 분류가 같다."),
    ("연료용 수입무연탄", "고체", _COAL, "별표 6 고체연료 문장의 「무연탄」 과 별표 12 의 석탄(6) 분류가 같다."),
    ("원료용 수입무연탄", "고체", _COAL, "별표 6 고체연료 문장의 「무연탄」 과 별표 12 의 석탄(6) 분류가 같다."),
    ("연료용 유연탄(역청탄)", "고체", _COAL, "별표 6 고체연료 문장의 「유연탄」 과 별표 12 의 석탄(6) 분류가 같다."),
    ("원료용 유연탄(역청탄)", "고체", _COAL, "별표 6 고체연료 문장의 「유연탄」 과 별표 12 의 석탄(6) 분류가 같다."),
    ("아역청탄", "고체", _COAL, "별표 12 가 석탄(6)으로 분류한다. 별표 6 고체연료 문장은 무연탄·유연탄·갈탄·코크스를 들 뿐 아역청탄을 적지 않는다."),
    (
        "석유코크스",
        "고체",
        _OIL,
        "근거가 어긋난다. 별표 12 는 석유(16)로 분류하지만 별표 10 의 국내에너지원 기준이 「석유코크스(고체)」 이고 별표 10 [참고] 의 정의가 「흑색 고체」 라고 쓴다. "
        "별표 6 의 문장에는 이름이 없다. 석유(16)는 원료에 따른 분류이고(프로판·부탄도 석유(16)이지만 별표 6 은 기체연료로 든다) 상태를 직접 적은 쪽은 별표 10 이라 고체로 보았다.",
    ),
]

TIER_PARAMETERS = ("산정방법론", "연료사용량", "순발열량", "배출계수", "산화계수")
GROUPS = ("A", "B", "C")


class MappingInvalid(Exception):
    """대응표가 스키마를 통과하지 못했다."""


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = json.loads(MAPPING_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validation_errors(mapping: dict) -> list[str]:
    return [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in _validator().iter_errors(mapping)
    ]


def _source_block(source_id: str) -> dict:
    src = SOURCES[source_id]
    return {
        "name": src.name,
        "publisher": src.publisher,
        "url": src.url,
        "version": src.version,
        "retrieved": src.retrieved,
        "file_name": src.path.name,
        "file_sha256": src.sha256(),
    }


# ---------------------------------------------------------------- 부문 (별표 10)

_SECTOR_LINE = re.compile(r"([①②③④])([^:①②③④]+):(?:중분류코드)?([\d∼,*]+)인사업장")
_CODE_TOKEN = re.compile(r"^(\d{2})(?:∼(\d{2}))?(\**)$")
_EXCEPTION = re.compile(r"\((\d{3,4})\)은(" + "|".join(re.escape(k) for k in SECTOR_KEYS) + r")(?:배출)?계수를적용한다")


def _expand(codes_raw: str, where: str) -> list[str]:
    out: list[str] = []
    for token in codes_raw.split(","):
        m = _CODE_TOKEN.match(token)
        if not m:
            raise SourceFormatError(f"{where}: 업종 코드 표기를 읽을 수 없다 {token!r}")
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        if hi < lo:
            raise SourceFormatError(f"{where}: 코드 범위가 거꾸로다 {token!r}")
        out += [f"{n:02d}" for n in range(lo, hi + 1)]
    if len(set(out)) != len(out):
        raise SourceFormatError(f"{where}: 한 부문 안에서 코드가 겹친다 {codes_raw!r}")
    return out


def _sectors(records: list[dict]) -> tuple[list[dict], list[dict]]:
    src = SOURCES[kr_ets.BYL10]
    doc = pymupdf.open(src.require())
    pages = [p for p in doc if "한국표준산업분류" in _squash(p.get_text())]
    if len(pages) != 1:
        raise SourceFormatError(f"{src.id}: 부문 정의(한국표준산업분류)가 있는 쪽이 {len(pages)}개다")
    page_no, text = pages[0].number + 1, _squash(pages[0].get_text())
    found = _SECTOR_LINE.findall(text)
    if [mark for mark, _, _ in found] != ["①", "②", "③", "④"]:
        raise SourceFormatError(f"{src.id} {page_no}쪽: 부문 정의 네 줄을 읽지 못했다 {found}")

    by_gas: dict[str, set[str]] = {"CH4": set(), "N2O": set()}
    for r in records:
        if r["source"]["id"] == kr_ets.BYL10 and r["gas"] in by_gas and "부문" in r["conditions"]:
            by_gas[r["gas"]].add(r["conditions"]["부문"])

    sectors = []
    for mark, label, codes_raw in found:
        if label not in SECTOR_KEYS:
            raise SourceFormatError(f"{src.id} {page_no}쪽: 모르는 부문 이름 {label!r}")
        plain = label.replace("ㆍ", "")
        if plain not in by_gas["CH4"]:
            raise SourceFormatError(f"{src.id}: 부문 {label!r} 의 CH4 열을 계수 표에서 찾지 못했다 {sorted(by_gas['CH4'])}")
        n2o = [c for c in sorted(by_gas["N2O"]) if plain in c]
        if len(n2o) != 1:
            raise SourceFormatError(f"{src.id}: 부문 {label!r} 의 N2O 열이 하나가 아니다 {n2o}")
        sectors.append({
            "key": SECTOR_KEYS[label],
            "label": label,
            "ksic_divisions": _expand(codes_raw, f"{src.id} {page_no}쪽 {mark}"),
            "ch4_condition": plain,
            "n2o_condition": n2o[0],
            "codes_raw": codes_raw,
            "source_id": src.id,
            "locator": f"PDF {page_no}쪽 {mark} {label}",
        })

    exceptions = [(code, SECTOR_KEYS[label], m) for m in _EXCEPTION.finditer(text) for code, label in [m.groups()]]
    overlaps = []
    divisions = sorted({d for s in sectors for d in s["ksic_divisions"]})
    for division in divisions:
        holders = [s["key"] for s in sectors if division in s["ksic_divisions"]]
        if len(holders) < 2:
            continue
        own = [
            {
                "code_prefix": code,
                "sector": key,
                "raw": m.group(0),
            }
            for code, key, m in exceptions
            if code.startswith(division)
        ]
        if not own or any(e["sector"] not in holders for e in own):
            raise SourceFormatError(f"{src.id} {page_no}쪽: 겹치는 코드 {division} 를 푸는 주석을 읽지 못했다")
        overlaps.append({
            "division": division,
            "sectors": holders,
            "exceptions": own,
            "source_id": src.id,
            "locator": f"PDF {page_no}쪽 부문 정의 아래 주석 (*, **)",
        })
    covered = {e["code_prefix"] for o in overlaps for e in o["exceptions"]}
    if {code for code, _, _ in exceptions} != covered:
        raise SourceFormatError(f"{src.id} {page_no}쪽: 겹치지 않는 코드에 붙은 예외 주석이 있다")
    return sectors, overlaps


# ---------------------------------------------------------------- 연료 이름 (별표 10·11)

def _row_locator(record: dict) -> str:
    """별표 10 은 한 행이 7줄(기체·부문)이다. 행까지만 남긴다."""
    return re.sub(r" (CO2|CH4|N2O)( \S+)?$", "", record["source"]["locator"])


@lru_cache(maxsize=None)
def _page_text(source_id: str, page: int) -> str:
    doc = pymupdf.open(SOURCES[source_id].require())
    if not 1 <= page <= len(doc):
        raise SourceFormatError(f"{source_id}: {page}쪽이 없다")
    return _squash(doc[page - 1].get_text())


def _check_evidence(evidence: list[dict], where: str) -> None:
    """근거 문장이 그 출처의 그 쪽 글자에 공백을 뺀 채로 있는지."""
    for ev in evidence:
        m = re.fullmatch(r"PDF (\d+)쪽", ev["locator"])
        if ev["source_id"] not in SOURCES or not m:
            raise SourceFormatError(f"{where}: 근거 자리를 읽을 수 없다 {ev}")
        if _squash(ev["quote"]) not in _page_text(ev["source_id"], int(m.group(1))):
            raise SourceFormatError(f"{where}: 근거 문장이 {ev['source_id']} {ev['locator']} 에 없다 {ev['quote']!r}")


def _fuel_names(records: list[dict]) -> list[dict]:
    entries: list[dict] = []
    for table in (kr_ets.BYL10, kr_ets.BYL11):
        seen: set[str] = set()
        ipcc_names: set[str] = set()
        cells_used: set[str] = set()
        for r in records:
            if r["source"]["id"] != table or DOMESTIC_KEY not in r["extra"]:
                continue
            ipcc_names.add(r["name"]["original"])
            locator = _row_locator(r)
            if locator in seen:
                continue
            seen.add(locator)
            raw = r["extra"][DOMESTIC_KEY]
            if raw == "-":
                continue  # 원문이 국내 이름을 비워 둔 연료
            note = None
            if (table, r["name"]["original"]) in LINE_BREAK_CELLS:
                # 칸 안의 줄바꿈: 건별 판단 (기계 규칙으로 일반화하지 않는다)
                expected_raw, names, note = LINE_BREAK_CELLS[(table, r["name"]["original"])]
                if raw != expected_raw:
                    raise SourceFormatError(f"{table} {locator}: 줄바꿈 칸이 판단할 때 본 글자와 다르다 {raw!r} != {expected_raw!r}")
                cells_used.add(r["name"]["original"])
            elif "," in raw:
                names = [_squash_inner(x) for x in raw.split(",")]
            elif "\n" in raw:
                raise SourceFormatError(f"{table} {locator}: 칸 안의 줄바꿈을 판단한 적이 없다 {raw!r} (LINE_BREAK_CELLS 에 건별로 적는다)")
            else:
                names = [raw.strip()]
            if any(not n for n in names):
                raise SourceFormatError(f"{table} {locator}: 국내에너지원 기준을 읽을 수 없다 {raw!r}")
            entries.append({
                "table": table,
                "ipcc_name": r["name"]["original"],
                "domestic_names": names,
                "domestic_raw": raw,
                "basis": "guideline",
                "note": note,
                "source_id": table,
                "locator": f"{locator} 국내에너지원 기준",
            })
        if not seen:
            raise SourceFormatError(f"{table}: 국내에너지원 기준 열이 있는 행이 없다")
        missing_cells = {name for t, name in LINE_BREAK_CELLS if t == table} - cells_used
        if missing_cells:
            raise SourceFormatError(f"{table}: 판단해 둔 줄바꿈 칸이 표에 없다 {sorted(missing_cells)}")

        for name in ROW_NAME_FUELS.get(table, ()):
            rows = sorted({_row_locator(r) for r in records if r["source"]["id"] == table and r["name"]["original"] == name})
            if len(rows) != 1 or any(DOMESTIC_KEY in r["extra"] for r in records if r["source"]["id"] == table and r["name"]["original"] == name):
                raise SourceFormatError(f"{table}: 행 이름이 국내 이름인 {name!r} 가 국내에너지원 기준 열 없는 한 행이 아니다 {rows}")
            if any(name in e["domestic_names"] for e in entries if e["table"] == table):
                raise SourceFormatError(f"{table}: {name!r} 가 국내에너지원 기준 열에도 있다")
            entries.append({
                "table": table,
                "ipcc_name": name,
                "domestic_names": [name],
                "domestic_raw": None,
                "basis": "guideline",
                "note": ROW_NAME_NOTE,
                "source_id": table,
                "locator": f"{rows[0]} 연료명",
            })

        guideline = [e for e in entries if e["table"] == table]
        for item in ASSUMED_FUEL_NAMES:
            if table not in item["ipcc"]:
                continue
            domestic, ipcc = item["domestic"], item["ipcc"][table]
            if any(domestic in e["domestic_names"] for e in guideline):
                raise SourceFormatError(f"{table}: 추정 대응 {domestic!r} 가 지침의 대응과 겹친다. 추정을 지운다")
            target = [e for e in guideline if e["ipcc_name"] == ipcc]
            if ipcc not in ipcc_names or len(target) != 1:
                raise SourceFormatError(f"{table}: 추정 대응의 대상 {ipcc!r} 가 표에 하나가 아니다")
            if not item["note"]:
                raise SourceFormatError(f"{table}: 추정 대응 {domestic!r} 에 까닭이 없다")
            _check_evidence(item["evidence"], f"{table} 추정 대응 {domestic!r}")
            entry = {
                "table": table,
                "ipcc_name": ipcc,
                "domestic_names": [domestic],
                "domestic_raw": None,
                "basis": "assumed",
                "note": item["note"],
                "source_id": table,
                "locator": target[0]["locator"].removesuffix(" 국내에너지원 기준"),
            }
            if item["parameters"] is not None:
                entry["parameters"] = list(item["parameters"])
            if item["evidence"]:
                entry["evidence"] = [dict(ev) for ev in item["evidence"]]
            entries.append(entry)
        own = [e for e in entries if e["table"] == table]
        for domestic in {n for e in own for n in e["domestic_names"]}:
            holders = [e for e in own if domestic in e["domestic_names"]]
            scopes = [tuple(e.get("parameters") or ("*",)) for e in holders]
            if len(holders) > 1 and ("*",) in scopes:
                raise SourceFormatError(f"{table}: 국내 이름 {domestic!r} 가 IPCC 연료 둘을 가리킨다 {[e['ipcc_name'] for e in holders]}")
    return entries


def _national_ef_names(records: list[dict]) -> list[dict]:
    """별표 12 의 발열량 표 이름 → 같은 별표 배출계수 표의 이름 (추정)."""
    hv = {r["name"]["original"] for r in records if r["source"]["id"] == BYL12 and r["record_type"] == "heating_value"}
    co2 = {r["name"]["original"]: r for r in records if r["source"]["id"] == BYL12 and r["record_type"] == "emission_factor" and r["gas"] == "CO2"}
    entries, seen = [], set()
    for fuel, ef_name, why in NATIONAL_EF_NAMES:
        if fuel in seen:
            raise SourceFormatError(f"{BYL12}: 배출계수 표 이름 대응이 두 번 적혔다 {fuel!r}")
        seen.add(fuel)
        if fuel not in hv or ef_name not in co2:
            raise SourceFormatError(f"{BYL12}: 대응의 양쪽 이름이 표에 없다 {fuel!r} → {ef_name!r}")
        if fuel in co2 or ef_name in hv:
            raise SourceFormatError(f"{BYL12}: {fuel!r} / {ef_name!r} 는 두 표에 같은 글자로 있다. 대응이 필요 없다")
        entries.append({
            "fuel": fuel,
            "ef_name": ef_name,
            "basis": "assumed",
            "note": why,
            "source_id": BYL12,
            "locator": co2[ef_name]["source"]["locator"].removesuffix(" 이산화탄소 배출계수"),
        })
    return entries


def _squash_inner(text: str) -> str:
    """쉼표로 나눈 이름 하나. 칸 안의 줄바꿈만 걷어 낸다 (낱말 사이 띄어쓰기는 둔다)."""
    return re.sub(r"\s*\n\s*", "", text).strip()


# ---------------------------------------------------------------- 연료 상태 (별표 6)

def _fuel_states(records: list[dict]) -> list[dict]:
    src = SOURCES[kr_ets_byl06.BYL06]
    sections = kr_ets_byl06.stationary_sections(pymupdf.open(src.require()))
    quotes = {"고체": _SOLID_QUOTE, "기체": _GAS_QUOTE, "액체": _LIQUID_QUOTE}
    rows = list(FUEL_STATES) + [(fuel, state, "assumed", quotes[state], why) for fuel, state, _, why in ASSUMED_FUEL_STATES]
    classes = {fuel: cls for fuel, _, cls, _ in ASSUMED_FUEL_STATES}
    hv = {r["name"]["original"] for r in records if r["source"]["id"] == BYL12 and r["record_type"] == "heating_value"}
    ef_name = {fuel: name for fuel, name, _ in NATIONAL_EF_NAMES}
    co2 = {r["name"]["original"]: r for r in records if r["source"]["id"] == BYL12 and r["record_type"] == "emission_factor" and r["gas"] == "CO2"}
    entries, seen = [], set()
    for fuel, state, basis, quote, note in rows:
        if fuel in seen:
            raise SourceFormatError(f"연료 상태가 두 번 적혔다: {fuel}")
        seen.add(fuel)
        section = sections[state]
        hits = sorted({page for page, _ in section["lines"]
                       if _squash(quote) in _squash("".join(ln for p, ln in section["lines"] if p == page))})
        if len(hits) != 1:
            raise SourceFormatError(f"{src.id} 고정연소({state}연료): 인용문이 한 쪽에 있지 않다 {quote!r} {hits}")
        # 인용문의 낱말(쉼표·공백으로 가른 것)에 그 이름이 그대로, 또는 조사 「와」가 붙어 있어야 한다
        if basis == "guideline" and not any(tok in (fuel, fuel + "와") for tok in re.findall(r"[^\s,]+", quote)):
            raise SourceFormatError(f"연료 상태 {fuel}: 인용문에 그 이름이 그대로 없는데 guideline 으로 적었다")
        if (basis == "assumed") != bool(note):
            raise SourceFormatError(f"연료 상태 {fuel}: 추정이면 까닭을 적고, 아니면 적지 않는다")
        entry = {
            "fuel": fuel,
            "state": state,
            "basis": basis,
            "quote": quote,
            "note": note,
            "source_id": src.id,
            "locator": f"PDF {hits[0]}쪽 「{section['number']}. 고정연소 ({state}연료)」 1. 배출활동 개요",
        }
        if fuel in classes:
            # 까닭에 적은 별표 12 의 분류가 계수 레코드의 분류와 같은지
            if fuel not in hv:
                raise SourceFormatError(f"연료 상태 {fuel}: 별표 12 발열량 표에 없는 이름이다")
            ef = co2.get(ef_name.get(fuel, fuel))
            if ef is None or ef["category_path"][1:] != [classes[fuel]]:
                raise SourceFormatError(f"연료 상태 {fuel}: 별표 12 의 분류가 {classes[fuel]!r} 가 아니다 ({ef and ef['category_path']})")
            page = int(re.match(r"PDF (\d+)쪽", ef["source"]["locator"]).group(1))
            # 표가 쪽을 넘어가면 분류 머리(병합 칸)는 앞 쪽에만 찍힌다 (아역청탄)
            if _squash(classes[fuel]) not in _page_text(BYL12, page):
                page -= 1
            entry["evidence"] = [{"source_id": BYL12, "locator": f"PDF {page}쪽", "quote": classes[fuel]}] + [dict(ev) for ev in EXTRA_STATE_EVIDENCE.get(fuel, [])]
            _check_evidence(entry["evidence"], f"연료 상태 {fuel}")
        entries.append(entry)
    return entries


# ---------------------------------------------------------------- 최소 산정등급 (별표 5)

_TIER_HEADER = "산정방법론연료사용량순발열량배출계수산화계수시설규모" + "ABC" * len(TIER_PARAMETERS)
_TIER_ROWS = re.compile(r"1\.고정연소①고체연료([1-4]{15})②기체연료([1-4]{15})③액체연료([1-4]{15})2\.이동연소")


def _tier_minimums() -> list[dict]:
    src = SOURCES[BYL05]
    doc = pymupdf.open(src.require())
    pages = [p for p in doc if _TIER_ROWS.search(_squash(p.get_text()))]
    if len(pages) != 1:
        raise SourceFormatError(f"{src.id}: 고정연소 최소 산정등급 표가 있는 쪽이 {len(pages)}개다")
    page_no, text = pages[0].number + 1, _squash(pages[0].get_text())
    if text.count(_TIER_HEADER) < 1 or text.index(_TIER_HEADER) > _TIER_ROWS.search(text).start():
        raise SourceFormatError(f"{src.id} {page_no}쪽: 표 머리(매개변수 5개 × A·B·C)가 다르다")
    digits = _TIER_ROWS.search(text).groups()
    entries = []
    for state, row in zip(kr_ets_byl06.STATES, digits):
        for pi, parameter in enumerate(TIER_PARAMETERS):
            for gi, group in enumerate(GROUPS):
                entries.append({
                    "activity": "고정연소",
                    "fuel_state": state,
                    "parameter": parameter,
                    "group": group,
                    "tier": int(row[pi * len(GROUPS) + gi]),
                    "source_id": src.id,
                    "locator": f"PDF {page_no}쪽 「연소시설에서 에너지 이용에 따른 온실가스 배출」 표 1. 고정연소 {state}연료 {parameter} {group}",
                })
    return entries


# ---------------------------------------------------------------- 조립

def build_mappings(records: list[dict]) -> dict:
    """계수 레코드와 원자료에서 대응표를 만든다. 읽지 못하면 SourceFormatError, 형식이 틀리면 MappingInvalid."""
    sectors, overlaps = _sectors(records)
    mapping = {
        "schema_version": MAPPING_SCHEMA_VERSION,
        "guideline": {
            "name": "온실가스 배출권거래제의 배출량 보고 및 인증에 관한 지침",
            "version": SOURCES[kr_ets.BYL10].version,
            "url": SOURCES[kr_ets.BYL10].url,
        },
        "sources": {sid: _source_block(sid) for sid in (BYL05, kr_ets_byl06.BYL06, kr_ets.BYL10, kr_ets.BYL11, BYL12)},
        "sectors": sectors,
        "sector_overlaps": overlaps,
        "fuel_names": _fuel_names(records),
        "national_ef_names": _national_ef_names(records),
        "fuel_states": _fuel_states(records),
        "tier_minimums": _tier_minimums(),
    }
    errors = validation_errors(mapping)
    if errors:
        raise MappingInvalid("; ".join(errors[:5]))
    return mapping


def write_mappings(mapping: dict, path) -> None:
    path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def summary_lines(mapping: dict) -> list[str]:
    """SUMMARY.md 에 붙는 절. 수치는 전부 mapping 에서 센 것이다."""
    names = mapping["fuel_names"]
    assumed_names = [e for e in names if e["basis"] == "assumed"]
    states = mapping["fuel_states"]
    out = ["", f"## 조립용 대응표 (`{MAPPING_NAME}`)", "",
           "계수 레코드가 아니다. 낱줄을 지침의 규칙대로 묶을 때 쓰는 기준정보이고 형식은 `schemas/factor-mapping.schema.json` 이다.", "",
           "| 항목 | 건수 | 그중 추정(assumed) |", "|---|---|---|",
           f"| 부문 (sectors) | {len(mapping['sectors'])} | 0 |",
           f"| 두 부문에 겹치는 업종 코드 (sector_overlaps) | {len(mapping['sector_overlaps'])} | 0 |",
           f"| 연료 이름 대응 (fuel_names) | {len(names)} | {len(assumed_names)} |",
           f"| 그중 국내 이름을 가르지 못한 것 | {sum(1 for e in names if not e['domestic_names'])} | |",
           f"| 그중 일부 매개변수에만 쓰는 것 (parameters) | {sum(1 for e in names if e.get('parameters'))} | |",
           f"| 별표 12 배출계수 표 이름 대응 (national_ef_names) | {len(mapping.get('national_ef_names', []))} | {sum(1 for e in mapping.get('national_ef_names', []) if e['basis'] == 'assumed')} |",
           f"| 연료 상태 (fuel_states) | {len(states)} | {sum(1 for e in states if e['basis'] == 'assumed')} |",
           f"| 최소 산정등급 (tier_minimums) | {len(mapping['tier_minimums'])} | 0 |",
           "", "추정 항목:", ""]
    out += [f"- 이름: {e['domestic_names'][0]} → {e['ipcc_name']} ({e['table']}{', ' + '·'.join(e['parameters']) + ' 만' if e.get('parameters') else ''})" for e in assumed_names]
    out += [f"- 별표 12 이름: {e['fuel']} → {e['ef_name']}" for e in mapping.get("national_ef_names", []) if e["basis"] == "assumed"]
    out += [f"- 상태: {e['fuel']} → {e['state']}" for e in states if e["basis"] == "assumed"]
    return out
