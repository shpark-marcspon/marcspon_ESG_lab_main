"""출처 등록부: 원자료 위치, 출처 URL, 버전, 확인 일자, 이용 조건.

수치는 여기에 두지 않는다. 값은 전부 원자료 파일에서 읽는다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PREWORK_DIR = REPO_ROOT.parent / "prework" / "ef-sources"
RAW_DIR = REPO_ROOT / "data" / "raw"
SCHEMA_PATH = REPO_ROOT / "schemas" / "factor.schema.json"
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "factors"


class SourceUnavailable(Exception):
    """원자료 파일이 없다. 빈 결과로 접지 않고 올린다."""


class SourceFormatError(Exception):
    """원자료의 구조가 로더가 아는 것과 다르다. 추측해 읽지 않고 올린다."""


@lru_cache(maxsize=None)
def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    publisher: str
    url: str
    version: str
    retrieved: str
    path: Path
    download_url: str | None
    license_name: str
    license_url: str
    license_status: str
    license_note: str | None

    def require(self) -> Path:
        if not self.path.is_file():
            hint = (
                "`uv run python -m factors fetch` 로 내려받는다"
                if self.download_url
                else "../prework/ef-sources 에 있어야 한다"
            )
            raise SourceUnavailable(f"{self.id}: 원자료 없음 {self.path} ({hint})")
        return self.path

    def sha256(self) -> str:
        return _sha256(self.require())

    def source_block(self, locator: str) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "publisher": self.publisher,
            "url": self.url,
            "version": self.version,
            "retrieved": self.retrieved,
            "file_name": self.path.name,
            "file_sha256": self.sha256(),
            "locator": locator,
        }

    def license_block(self) -> dict:
        return {"name": self.license_name, "url": self.license_url, "note": self.license_note}


_GIR_DL = "https://www.gir.go.kr/home/file/readDownloadFile.do"
_GIR_POLICY = "https://www.gir.go.kr/home/index.do?menuId=43"
_GIR_NOTE = (
    "게시물에 공공누리 표시가 없다. GIR 저작권정책은 공공누리 표시가 없는 자료는 "
    "담당자와 사전 협의 후 이용하라고 적고 있다 (2026-10-01 확인)."
)
_LAW_URL = "https://www.law.go.kr/LSW/admRulLsInfoP.do?admRulSeq=2100000257748"
_LAW_VERSION = "환경부고시 제2025-64호 (2025-04-11 시행)"
_LAW_NOTE = (
    "행정규칙(고시)의 별표다. 고시는 저작권법 제7조의 보호받지 못하는 저작물에 "
    "해당할 것으로 보이나 이 작업에서 조문 원문을 열어 확인하지 않았다."
)
_LAW_DL = "https://www.law.go.kr/LSW/flDownload.do?flSeq="

SOURCES: dict[str, Source] = {
    s.id: s
    for s in [
        Source(
            id="keiti-epd",
            name="환경성적표지 평가계수",
            publisher="한국환경산업기술원 (KEITI)",
            url="https://www.data.go.kr/data/15120968/fileData.do",
            version="공공데이터포털 수정일 2025-08-05",
            retrieved="2026-10-01",
            path=PREWORK_DIR / "keiti-환경성적표지-평가계수.csv",
            download_url=None,
            license_name="공공데이터포털 '이용허락범위 제한 없음' / KEITI 사업 페이지 '상업용 목적으로 사용할 수 없습니다'",
            license_url="https://www.keiti.re.kr/site/keiti/02/10202100000002023101610.jsp",
            license_status="disputed",
            license_note="두 공식 페이지의 조건이 충돌한다 (PRJ11-RPT-3). KEITI 서면 확인 전에는 상업 서비스에 쓰지 않는다.",
        ),
        Source(
            id="defra-2026",
            name="UK Government GHG Conversion Factors for Company Reporting 2026 (flat file)",
            publisher="UK Department for Energy Security and Net Zero (DESNZ)",
            url="https://www.gov.uk/government/publications/greenhouse-gas-reporting-conversion-factors-2026",
            version="2026 v1.2",
            retrieved="2026-10-01",
            path=PREWORK_DIR / "defra-2026-conversion-factors-flat.xlsx",
            download_url=None,
            license_name="Open Government Licence v3.0",
            license_url="https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
            license_status="ok",
            license_note="출처 표시 필요.",
        ),
        Source(
            id="epa-seef-v1.3",
            name="Supply Chain Greenhouse Gas Emission Factors v1.3 by NAICS-6",
            publisher="U.S. Environmental Protection Agency",
            url="https://catalog.data.gov/dataset/supply-chain-greenhouse-gas-emission-factors-v1-3-by-naics-6",
            version="v1.3.0",
            retrieved="2026-10-01",
            path=RAW_DIR / "epa" / "SupplyChainGHGEmissionFactors_v1.3.0_NAICS_CO2e_USD2022.csv",
            download_url="https://pasteur.epa.gov/uploads/10.23719/1531143/SupplyChainGHGEmissionFactors_v1.3.0_NAICS_CO2e_USD2022.csv",
            license_name="U.S. Public Domain (17 U.S.C. § 105)",
            license_url="https://pasteur.epa.gov/license/sciencehub-license.html",
            license_status="ok",
            license_note=None,
        ),
        Source(
            id="gir-2025",
            name="2025년 승인 국가 온실가스 배출·흡수계수",
            publisher="온실가스종합정보센터 (GIR)",
            url="https://www.gir.go.kr/home/board/read.do?menuId=36&boardId=86&boardMasterId=2",
            version="2025-12-18 공고",
            retrieved="2026-10-01",
            path=RAW_DIR / "kr" / "gir-2025-approved-factors.hwpx",
            download_url=_GIR_DL + "?encFileId=27e1e38294da64ec8535ca668ab94d35c0b401aea6f27d19&fileSeq=1",
            license_name="공공누리 표시 없음 (GIR 저작권정책 적용)",
            license_url=_GIR_POLICY,
            license_status="unverified",
            license_note=_GIR_NOTE,
        ),
        Source(
            id="gir-2024",
            name="2024년 승인 국가 온실가스 배출계수_전력배출계수",
            publisher="온실가스종합정보센터 (GIR)",
            url="https://www.gir.go.kr/home/board/read.do?menuId=36&boardId=82&boardMasterId=2",
            version="2025-03-31 게시",
            retrieved="2026-10-01",
            path=RAW_DIR / "kr" / "gir-2024-electricity-factor.pdf",
            download_url=_GIR_DL + "?encFileId=dc243f7e60f37fa8c910bdde2f1dc835b782fdf540f56d0e&fileSeq=1",
            license_name="공공누리 표시 없음 (GIR 저작권정책 적용)",
            license_url=_GIR_POLICY,
            license_status="unverified",
            license_note=_GIR_NOTE,
        ),
        Source(
            id="kr-ets-byl10",
            name="온실가스 배출권거래제의 배출량 보고 및 인증에 관한 지침 [별표 10] 2006 IPCC 국가 인벤토리 가이드라인 기본 배출계수",
            publisher="환경부",
            url=_LAW_URL,
            version=_LAW_VERSION,
            retrieved="2026-10-01",
            path=RAW_DIR / "kr" / "ets-byl10-ipcc-default-ef.pdf",
            download_url=_LAW_DL + "151024595",
            license_name="행정규칙(고시) 별표",
            license_url=_LAW_URL,
            license_status="unverified",
            license_note=_LAW_NOTE,
        ),
        Source(
            id="kr-ets-byl11",
            name="온실가스 배출권거래제의 배출량 보고 및 인증에 관한 지침 [별표 11] 2006 IPCC 국가 인벤토리 가이드라인 연료별 기본 발열량",
            publisher="환경부",
            url=_LAW_URL,
            version=_LAW_VERSION,
            retrieved="2026-10-01",
            path=RAW_DIR / "kr" / "ets-byl11-ipcc-default-ncv.pdf",
            download_url=_LAW_DL + "151024603",
            license_name="행정규칙(고시) 별표",
            license_url=_LAW_URL,
            license_status="unverified",
            license_note=_LAW_NOTE,
        ),
        Source(
            id="kr-ets-byl12",
            name="온실가스 배출권거래제의 배출량 보고 및 인증에 관한 지침 [별표 12] 연료별 국가 고유 발열량 및 배출계수",
            publisher="환경부",
            url=_LAW_URL,
            version=_LAW_VERSION,
            retrieved="2026-10-01",
            path=RAW_DIR / "kr" / "ets-byl12-kr-cv-ef.pdf",
            download_url=_LAW_DL + "151024615",
            license_name="행정규칙(고시) 별표",
            license_url=_LAW_URL,
            license_status="unverified",
            license_note=_LAW_NOTE,
        ),
        Source(
            id="kr-ets-byl06",
            name="온실가스 배출권거래제의 배출량 보고 및 인증에 관한 지침 [별표 6] 배출활동별 온실가스 배출량 등의 세부산정방법 및 기준",
            publisher="환경부",
            url=_LAW_URL,
            version=_LAW_VERSION,
            retrieved="2026-10-02",
            path=RAW_DIR / "kr" / "ets-byl06-methods.pdf",
            download_url=_LAW_DL + "151024511",
            license_name="행정규칙(고시) 별표",
            license_url=_LAW_URL,
            license_status="unverified",
            license_note=_LAW_NOTE,
        ),
        Source(
            id="kr-ets-byl05",
            name="온실가스 배출권거래제의 배출량 보고 및 인증에 관한 지침 [별표 5] 배출활동별, 시설규모별 산정등급(Tier) 최소적용기준",
            publisher="환경부",
            url=_LAW_URL,
            version=_LAW_VERSION,
            retrieved="2026-10-02",
            path=RAW_DIR / "kr" / "ets-byl05-tier-minimum.pdf",
            download_url=_LAW_DL + "151023935",
            license_name="행정규칙(고시) 별표",
            license_url=_LAW_URL,
            license_status="unverified",
            license_note=_LAW_NOTE,
        ),
    ]
}

MAPPING_SCHEMA_PATH = REPO_ROOT / "schemas" / "factor-mapping.schema.json"
