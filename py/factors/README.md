# factors

공개 배출계수를 출처·버전·통화·단위가 붙은 하나의 표로 정규화해 싣는다. 발주서는 `PRJ11-TICK-5`, 설계 근거는 `PRJ11-RPT-3`.

## 쓰는 법

```
cd py
uv run python -m factors fetch                          # 공개 원자료를 ../data/raw 로 (EPA, GIR, 법령정보센터)
uv run python -m factors build --out ../data/factors    # factors.jsonl, factors.sqlite, mappings.kr-ets.json, SUMMARY.md
uv run python -m factors summary                        # 출처별·카테고리별 건수
uv run python -m factors query 4 distance               # Scope 3 카테고리 4 의 거리 기반 후보
uv run pytest -q factors
```

```python
from factors import find_candidates
find_candidates(1, "spend")                                   # 후보 레코드(dict) 리스트
find_candidates(4, "distance", license_statuses=("ok",))      # 라이선스가 확정된 출처만
```

빈 리스트는 「조건에 맞는 계수가 없다」는 뜻이다. 적재본이 없으면 `FactorStoreMissing`, 인자가 틀리면 `ValueError` 다.

TypeScript 쪽은 `data/factors/factors.jsonl` 을 읽는다. 형식은 `schemas/factor.schema.json`.

## 원자료

| 출처 id | 위치 | 형식 |
|---|---|---|
| `keiti-epd` | `../prework/ef-sources/keiti-환경성적표지-평가계수.csv` | CSV (cp949) |
| `defra-2026` | `../prework/ef-sources/defra-2026-conversion-factors-flat.xlsx` | xlsx |
| `epa-seef-v1.3` | `data/raw/epa/` | CSV |
| `gir-2025`, `gir-2024` | `data/raw/kr/` | HWPX, PDF |
| `kr-ets-byl10`, `-byl11`, `-byl12` | `data/raw/kr/` | PDF |
| `kr-ets-byl06` (고정연소 산화계수, 구매 전력 <표-69>) | `data/raw/kr/` | PDF (본문 문장, 표) |
| `kr-ets-byl05` (최소 산정등급. 대응표에만 쓴다) | `data/raw/kr/` | PDF |

출처 URL, 버전, 확인 일자, 이용 조건은 `sources.py` 한 곳에 있다. `data/raw/` 는 git 밖이라 새로 받은 저장소에서는 `fetch` 를 먼저 돌린다. 원자료가 없으면 로더와 테스트는 건너뛰지 않고 실패한다.

## 규칙

- 값은 전부 원자료 파일에서 기계로 읽는다. 코드에 계수 값을 두지 않는다. 테스트에는 PRJ11-RPT-3 과 대조하려고 그 보고서에 적힌 값 두 개(KEITI 트럭, 2023년 소비단 전력배출계수)만 적었다.
- 원 값은 바꾸지 않는다. `value_raw` 가 출처 표기 그대로의 문자열이고, 십진 정확도가 필요한 쪽은 이것을 읽는다.
- 출처가 빈칸이나 `-` 로 둔 값은 `value: null` 과 `value_status` 로 싣는다. 0 으로 채우지 않는다.
- 머리행이나 표 구조가 로더가 아는 것과 다르면 `SourceFormatError` 로 멈춘다. 한 출처라도 실패하면 `build` 는 아무것도 쓰지 않는다.
- 환율·물가 보정은 하지 않는다. `adjusted` 는 구조만 있고 전부 null 이다.
- `license_status`: `ok` / `disputed`(KEITI) / `unverified`(GIR 공고, 고시 별표). 조회에서 걸러 낼 수 있다.

## 기체 값의 성격 (`gas_basis`)

- DEFRA 의 기체별 값(`kg CO2e of CH4 per unit`)은 이미 GWP 가 곱해진 CO2 환산량이다 → `co2e`.
- 한국 전력배출계수와 별표 10·12 의 기체별 값은 그 기체의 질량이다 → `gas_mass`. GWP 는 계산하는 쪽이 곱한다.
- 별표 12 의 탄소 배출계수(kgC/TJ)는 `carbon_mass`.

## 조립용 적재분 (MARC-TICK-17)

계산 엔진의 연소 계수는 여러 낱줄을 지침의 규칙대로 묶은 것이다. 묶는 일은 `ts/factor-assembly` 가 하고, 여기서는 그때 필요한 것을 싣는다.

- **산화계수** (`record_type: oxidation_factor`, 9건): 별표 6 의 고정연소 세 절(고체·기체·액체연료) 「④ 산화계수」 아래 Tier 별 문장에서 정규식으로 읽는다. 표가 아니라 문장이라, 문장이 로더가 아는 꼴과 한 글자라도 다르면 `SourceFormatError` 다. 단위가 없는 비율이라 `unit` 은 `무차원`, `gas` 는 `CO2`(CO2 에만 곱한다), 조건은 `conditions` 의 `연료 상태`·`산정등급`·`산화계수 부문`. 고체연료 Tier 3 과 Tier 4 는 고시에 값이 없어 싣지 않는다.
- **구매 전력 <표-69>** (`record_type: emission_factor`, `scope: scope2`, 3건, MARC-TICK-18): 별표 6 「39. 외부에서 공급된 전기 사용」 의 국가 고유 전력배출계수('14∼'16년 평균) CO2 tCO2/MWh, CH4 kgCH4/MWh, N2O kgN2O/MWh. 기체별 질량이라 `gas_basis: gas_mass`, 조건은 `기간`. 쪽 글자의 줄 순서(표 이름, 열 머리, 행 이름, 값 셋)와 같은 쪽의 「② 배출계수 Tier 2」 문장이 로더가 아는 것과 다르면 실패한다. 산화계수 9건 뒤에 붙는다. 같은 쪽 주석의 「재생에너지·폐열 전력은 계수 0」 은 싣지 않았다 (계수 값이 아니라 적용 규칙이다).
- **대응표** (`data/factors/mappings.kr-ets.json`, 형식 `schemas/factor-mapping.schema.json`): 값 하나짜리가 아니라서 계수 레코드와 따로 둔다.
  - `sectors`, `sector_overlaps`: 별표 10 의 부문 정의 (한국표준산업분류 중분류 코드). 두 부문에 같이 적힌 코드(38, 58, 59)는 풀지 않고 그대로 싣는다.
  - `fuel_names`: 별표 10·11 의 「국내에너지원 기준」 열 (경유 → 가스/디젤 오일). 칸 안에 줄바꿈이 있는 칸 세 건은 기계 규칙으로 가르지 않고 `LINE_BREAK_CELLS` 에 건별로 판단해 적었다(별표 10·11 무연탄은 두 이름, 별표 10 가스 코크스는 꺾인 한 이름). 판단하지 않은 줄바꿈 칸이 새로 나오면 적재가 실패한다. 「국내에너지원 기준」 열 없이 행 이름이 국내 이름인 B-A유·B-B유(`ROW_NAME_FUELS`)도 지침의 대응으로 싣는다. 항목에 `parameters` 가 있으면 그 매개변수에만 쓴다(부생연료 1·2호의 CO2 Tier 1, 별표 10 주3·주4).
  - `national_ef_names`: 별표 12 안에서 발열량 표와 배출계수 표가 같은 연료를 다르게 적는 것 (항공유 → 항공유(JET-A1), 부탄(LPG3호) → 원문 그대로의 부탄(LPP3호)). 전부 추정이다.
  - `fuel_states`: 연료 → 고체·기체·액체. 근거 문장이 별표 6 에 실제로 있는지 적재 때 확인한다. 별표 12 발열량 표의 연료(전기 제외 27개)는 전부 상태가 있다. 별표 6 의 문장에 이름이 그대로 없는 것은 `ASSUMED_FUEL_STATES` 의 추정이고, 까닭에 적은 별표 12 의 분류(석유·가스·석탄)를 적재 때 계수 레코드와 대조한다.
  - `tier_minimums`: 별표 5 의 그룹(A·B·C)별 매개변수별 최소 산정등급.
- **추정 대응**: 지침에 글자 그대로 없는 대응은 `mappings.py` 의 `ASSUMED_FUEL_NAMES`, `NATIONAL_EF_NAMES`, `FUEL_STATES`, `ASSUMED_FUEL_STATES` 에 까닭과 함께 적고 `basis: "assumed"` 로 싣는다. 까닭의 근거 문장 자리는 `evidence` 로 싣고, 그 문장이 원문 PDF 의 그 쪽에 있는지 적재 때 확인한다. 원문 오자로 보이는 표기는 고치지 않고 대응으로 잇는다. 목록은 `SUMMARY.md` 에 찍힌다.
- 새 출처는 `LOADERS` 의 끝에 붙인다. 앞선 줄은 순서와 글자가 바뀌면 안 된다 (`test_legacy_lines_are_byte_identical` 이 처음 10,742줄의 sha256 을 본다).

## Scope 3 카테고리 후보의 기준

`scope3_categories` 는 후보일 뿐 확정이 아니다. 레코드마다 `scope3_mapping_basis` 에 기준을 적는다. DEFRA 는 `loaders/defra.py` 의 `SCOPE3_LEVEL1` 표(Level 1 → 카테고리), KEITI 는 `loaders/keiti.py` 의 `MAPPING`(대분류 → 카테고리), EPA 는 전부 1·2 다. 표에 없는 Scope 3 Level 1 이 나오면 적재가 실패한다.
