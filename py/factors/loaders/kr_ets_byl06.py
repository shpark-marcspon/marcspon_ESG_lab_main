"""배출권거래제 배출량 보고·인증 지침 별표 6 의 고정연소 산화계수 (PDF 본문 문장)와 구매 전력 배출계수 <표-69>.

별표 6 은 배출활동마다 한 절이고, 고정연소는 고체·기체·액체연료 세 절이다. 산화계수는 표가 아니라
각 절의 「5. 매개변수별 관리기준 ④ 산화계수」 아래 Tier 별 문장에 적혀 있다.

값은 그 문장에서 정규식으로 읽는다. 문장이 로더가 아는 꼴과 다르면 추측하지 않고 실패한다.
Tier 3 의 고체연료(사업자가 개발한 고유 산화계수)와 Tier 4(연속측정)는 고시에 값이 없어 싣지 않는다.

「39. 외부에서 공급된 전기 사용」 절의 <표-69> (국가 고유 전력배출계수, '14∼'16년 평균)는 한 행 세 칸(CO2·CH4·N2O)이다.
쪽 글자의 줄 순서(표 이름, 열 머리 여섯 줄, 행 이름, 값 세 줄)가 로더가 아는 것과 한 줄이라도 다르면 실패한다.
산화계수 9건 뒤에 붙는다 (계수 표의 앞선 줄을 바꾸지 않는다).
"""

from __future__ import annotations

import re

import pymupdf

from ..record import make_record, parse_number
from ..sources import SOURCES, SourceFormatError

BYL06 = "kr-ets-byl06"

STATES = ("고체", "기체", "액체")

_HEADING = re.compile(r"^(\d+)\.\s*고정연소\s*\((고체|기체|액체)연료\)$")
_ANY_HEADING = re.compile(r"^\d+\.\s*\S")
_TIER_LINE = re.compile(r"^Tier\s*([1-4])$")

_NUM = r"(\d+\.\d+)"
_PROVISO = "단,온실가스종합정보센터에서별도의계수를공표하여지침에수록된경우그값을적용한다."
_DEFAULT = re.compile(rf"^산화계수\(fi?\)는기본값(?:인|으로){_NUM}을적용한다\.$")
_FLAT = re.compile(rf"^산화계수\(fi\)는{_NUM}를적용한다\.({re.escape(_PROVISO)})?$")
_SOLID_T2 = re.compile(rf"^발전부문은산화계수\(f\){_NUM}를적용하고,기타부문은{_NUM}을적용한다\.{re.escape(_PROVISO)}$")
_SOLID_T3 = "제16조에따라사업자가자체개발하거나연료공급자가분석하여제공한고유산화계수를사용한다."
_CEM = "연속측정방식(CEM)을사용한다."


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def stationary_sections(doc) -> dict[str, dict]:
    """연료 상태 → {"number": 절 번호, "lines": [(쪽, 줄)]}. 절은 쪽 첫 줄의 「n. 제목」과 둘째 줄의 IPCC 분류로 시작한다."""
    starts = []
    for page in doc:
        lines = [ln.strip() for ln in page.get_text().split("\n") if ln.strip()]
        if len(lines) >= 2 and _ANY_HEADING.match(lines[0]) and lines[1].startswith("IPCC"):
            starts.append((page.number, lines[0]))
    sections: dict[str, dict] = {}
    for k, (first, title) in enumerate(starts):
        m = _HEADING.match(title)
        if not m:
            continue
        if m.group(2) in sections:
            raise SourceFormatError(f"{BYL06}: 고정연소 ({m.group(2)}연료) 절이 둘이다")
        last = starts[k + 1][0] if k + 1 < len(starts) else len(doc)
        lines = [
            (pno + 1, ln.strip())
            for pno in range(first, last)
            for ln in doc[pno].get_text().split("\n")
            if ln.strip()
        ]
        sections[m.group(2)] = {"number": int(m.group(1)), "lines": lines}
    if tuple(sorted(sections)) != tuple(sorted(STATES)):
        raise SourceFormatError(f"{BYL06}: 고정연소 세 절(고체·기체·액체)을 찾지 못했다. 찾은 것: {sorted(sections)}")
    return sections


def _oxidation_blocks(state: str, lines: list[tuple[int, str]]) -> dict[int, tuple[int, str]]:
    """Tier → (그 Tier 줄이 있는 쪽, 문장을 공백 없이 이은 것)."""
    heads = [i for i, (_, ln) in enumerate(lines) if _squash(ln).startswith("④산화계수")]
    if len(heads) != 1:
        raise SourceFormatError(f"{BYL06} 고정연소({state}연료): 「④ 산화계수」 머리가 {len(heads)}개다")
    blocks: dict[int, tuple[int, str]] = {}
    tier, page, buf = None, None, []
    for pno, ln in lines[heads[0] + 1:]:
        m = _TIER_LINE.match(ln)
        if m:
            if tier is not None:
                blocks[tier] = (page, _squash("".join(buf)))
            tier, page, buf = int(m.group(1)), pno, []
            if tier in blocks:
                raise SourceFormatError(f"{BYL06} 고정연소({state}연료): 산화계수 Tier {tier} 가 두 번 나온다")
        elif tier is None:
            raise SourceFormatError(f"{BYL06} 고정연소({state}연료): 산화계수 머리 다음에 Tier 줄이 아닌 것이 온다 {ln!r}")
        else:
            buf.append(ln)
    if tier is not None:
        blocks[tier] = (page, _squash("".join(buf)))
    if sorted(blocks) != [1, 2, 3, 4]:
        raise SourceFormatError(f"{BYL06} 고정연소({state}연료): 산화계수 Tier 1~4 가 아니다 {sorted(blocks)}")
    return blocks


def _values(state: str, tier: int, text: str) -> list[tuple[str | None, str]]:
    """그 Tier 문장에서 (산화계수 부문 또는 None, 값 표기) 들. 고시에 값이 없는 Tier 는 빈 리스트."""
    where = f"{BYL06} 고정연소({state}연료) 산화계수 Tier {tier}"
    if tier == 4:
        if text != _CEM:
            raise SourceFormatError(f"{where}: 아는 문장이 아니다 {text!r}")
        return []
    if tier == 1:
        m = _DEFAULT.match(text)
        if not m:
            raise SourceFormatError(f"{where}: 아는 문장이 아니다 {text!r}")
        return [(None, m.group(1))]
    if state == "고체":
        if tier == 3:
            if not text.startswith(_SOLID_T3):
                raise SourceFormatError(f"{where}: 아는 문장이 아니다 {text!r}")
            return []
        m = _SOLID_T2.match(text)
        if not m:
            raise SourceFormatError(f"{where}: 아는 문장이 아니다 {text!r}")
        return [("발전부문", m.group(1)), ("기타부문", m.group(2))]
    m = _FLAT.match(text)
    if not m or (tier == 2) != bool(m.group(2)):
        raise SourceFormatError(f"{where}: 아는 문장이 아니다 {text!r}")
    return [(None, m.group(1))]


def load_byl06() -> list[dict]:
    """산화계수 9건, 그 뒤에 전력배출계수 3건. 순서가 계수 표의 순서다."""
    return load_oxidation() + load_electricity()


def load_oxidation() -> list[dict]:
    src = SOURCES[BYL06]
    doc = pymupdf.open(src.require())
    records = []
    for state in STATES:
        section = stationary_sections(doc)[state]
        title = f"{section['number']}. 고정연소 ({state}연료)"
        for tier, (page, text) in sorted(_oxidation_blocks(state, section["lines"]).items()):
            for sector, raw in _values(state, tier, text):
                conditions = {"연료 상태": state, "산정등급": f"Tier {tier}"}
                if sector:
                    conditions["산화계수 부문"] = sector
                records.append(
                    make_record(
                        src,
                        key=f"ox:{section['number']}:t{tier}" + (f":{sector}" if sector else ""),
                        locator=f"PDF {page}쪽 「{title}」 5. 매개변수별 관리기준 ④ 산화계수 Tier {tier}" + (f" {sector}" if sector else ""),
                        record_type="oxidation_factor",
                        scope="scope1",
                        factor_type="activity",
                        name_original=f"고정연소 ({state}연료) 산화계수",
                        name_lang="ko",
                        category_path=["고정연소", f"{state}연료", "산화계수"],
                        value=parse_number(raw, f"{src.id} {page}쪽 Tier {tier}"),
                        value_raw=raw,
                        unit="무차원",
                        unit_numerator="무차원",
                        unit_denominator="무차원",
                        gas="CO2",
                        gas_basis="not_applicable",
                        conditions=conditions,
                        region="KR",
                        year=None,
                        extra={"원문 (PDF 추출, 공백 없음)": text},
                    )
                )
    if len(records) != 9:
        # 고체 3 (Tier 1, Tier 2 발전·기타. Tier 3 은 값 없음), 기체 3, 액체 3
        raise SourceFormatError(f"{src.id}: 산화계수 값이 9개가 아니다 ({len(records)})")
    return records


# ---------------------------------------------------------------- 구매 전력 <표-69>

ELECTRICITY_TITLE = "39. 외부에서 공급된 전기 사용"
_T69_CAPTION = "<표-69>국가고유전력배출계수('14∼'16년평균)"
#: 표 이름 다음의 열 머리 (공백을 뺀 줄). 기체와 단위가 한 줄씩이다.
_T69_HEADER = ["구분", "CO2", "(tCO2/MWh)", "CH4", "(kgCH4/MWh)", "N2O", "(kgN2O/MWh)"]
_T69_ROW = "3개년평균('14∼'16)"
#: 같은 쪽의 「② 배출계수 Tier 2」 문장 (공백 없음). 표-69 를 어디에 쓰는지가 여기 적혀 있다.
_T69_RULE = (
    "전력배출계수는아래<표-69>에서제시된기준연도에해당하는3개년도('14∼'16년)평균값을적용한다."
    "향후한국전력거래소에서제공하는전력배출계수를온실가스종합정보센터에서확인하여지침에수록된경우그값을적용한다."
)
_T69_NOTE = "*출처:국가온실가스배출계수(온실가스종합정보센터,2018년)"
#: 같은 쪽 ** 주석 (재생에너지·폐열 전력은 계수 0). 값이 아니라 적용 규칙이라 레코드로 싣지 않고 extra 에 둔다.
_T69_ZERO = re.compile(r"\*\*「신에너지및재생에너지.*?전력배출계수‘0’사용")
_UNIT = re.compile(r"^\((t|kg)(CO2|CH4|N2O)/(MWh)\)$")
_VALUE = re.compile(r"^\d+\.\d+$")


def load_electricity() -> list[dict]:
    src = SOURCES[BYL06]
    doc = pymupdf.open(src.require())
    pages = [p for p in doc if _T69_CAPTION in _squash(p.get_text())]
    if len(pages) != 1:
        raise SourceFormatError(f"{src.id}: <표-69> 가 있는 쪽이 {len(pages)}개다")
    page = pages[0]
    page_no = page.number + 1
    # 표가 그 절 안에 있는지: 절 첫 쪽(「39. 외부에서 공급된 전기 사용」)과 다음 절 첫 쪽 사이
    starts = [p.number for p in doc if [ln.strip() for ln in p.get_text().split("\n") if ln.strip()][:1] == [ELECTRICITY_TITLE]]
    if len(starts) != 1 or not starts[0] <= page.number < starts[0] + 4:
        raise SourceFormatError(f"{src.id}: <표-69> 가 「{ELECTRICITY_TITLE}」 절 안에 있지 않다 (절 첫 쪽 {starts})")
    text = _squash(page.get_text())
    zero = _T69_ZERO.search(text)
    if _T69_RULE not in text or _T69_NOTE not in text or zero is None:
        raise SourceFormatError(f"{src.id} {page_no}쪽: 「② 배출계수 Tier 2」 문장이나 표의 출처 주석이 로더가 아는 꼴이 아니다")
    lines = [_squash(ln) for ln in page.get_text().split("\n") if ln.strip()]
    at = lines.index(_T69_CAPTION)
    header = lines[at + 1 : at + 1 + len(_T69_HEADER)]
    row = lines[at + 1 + len(_T69_HEADER) : at + 1 + len(_T69_HEADER) + 4]
    if header != _T69_HEADER or len(row) != 4 or row[0] != _T69_ROW or not all(_VALUE.match(v) for v in row[1:]):
        raise SourceFormatError(f"{src.id} {page_no}쪽: <표-69> 의 머리나 행이 로더가 아는 꼴이 아니다 {header} {row}")
    after = lines[at + 1 + len(_T69_HEADER) + 4]
    if not after.startswith("*출처"):
        raise SourceFormatError(f"{src.id} {page_no}쪽: <표-69> 가 한 행이 아니다 (값 뒤에 {after!r})")
    records = []
    for gas_label, unit_raw, raw in zip(_T69_HEADER[1::2], _T69_HEADER[2::2], row[1:]):
        m = _UNIT.match(unit_raw)
        if not m or m.group(2) != gas_label:
            raise SourceFormatError(f"{src.id} {page_no}쪽: <표-69> 의 단위를 읽을 수 없다 {gas_label} {unit_raw}")
        numerator = m.group(1) + m.group(2)
        records.append(
            make_record(
                src,
                key=f"elec:t69:{gas_label}",
                locator=f"PDF {page_no}쪽 「{ELECTRICITY_TITLE}」 5. 매개변수별 관리기준 ② 배출계수 <표-69> {gas_label} 열",
                record_type="emission_factor",
                scope="scope2",
                factor_type="activity",
                name_original="국가 고유 전력배출계수 (\'14∼\'16년 평균)",
                name_lang="ko",
                category_path=["외부에서 공급된 전기 사용", "전력배출계수"],
                value=parse_number(raw, f"{src.id} {page_no}쪽 <표-69> {gas_label}"),
                value_raw=raw,
                unit=f"{numerator}/MWh",
                unit_numerator=numerator,
                unit_denominator="MWh",
                gas=gas_label,
                gas_basis="gas_mass",
                conditions={"기간": "3개년 평균(\'14∼\'16)"},
                region="KR",
                year=None,
                extra={
                    "표 이름 (PDF 추출, 공백 없음)": _T69_CAPTION,
                    "적용 문장의 자리": "5. 매개변수별 관리기준 ② 배출계수 Tier 2",
                    "적용 문장 (PDF 추출, 공백 없음)": _T69_RULE,
                    "표의 출처 주석 (PDF 추출, 공백 없음)": _T69_NOTE,
                    "표의 ** 주석 (PDF 추출, 공백 없음, 싣지 않은 적용 규칙)": zero.group(0),
                },
            )
        )
    return records
