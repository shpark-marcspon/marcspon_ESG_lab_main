# esg-lab

마크스폰 ESG AI 솔루션의 사전 구현 조각 모음. 기술 스택과 EDK 연동 방식이 확정되기 전이라 **화면·서버·인프라 없이 스크립트와 라이브러리 수준**으로만 둔다. 각 조각은 입력과 출력이 분명한 모듈이고, 나중에 어떤 구조에도 끼울 수 있어야 한다.

계획과 발주서는 maricom 프로젝트 `PRJ11`에 있다 (부모 티켓과 그 자식들).

## 배치

| 경로 | 언어 | 내용 |
|---|---|---|
| `py/docparse/` | Python | PDF를 섹션·문단·표 JSON으로 분해 |
| `py/factors/` | Python | 공개 배출계수를 정규화해 하나의 표로 적재 |
| `py/greenwash/` | Python | 그린워싱 점검의 기준선: 마크스폰의 Colab 노트북을 약점까지 그대로 옮긴 것 (모의 공급사와 `--dry-run` 만, 실제 호출 없음) |
| `py/scope3colab/` | Python | Scope 3 코랩 대조 도구: 실행 때 읽기판에서 코랩 코드를 읽어 지은 입력으로 돌리고 esg-app 엔진 결과와 견줌 (사본·실호출 0) |
| `py/scope3flaws/` | Python | Scope 3 1차 엔진 흠 회귀 묶음(EBS2): 지은 입력으로 colab_157 을 돌려 층 문서의 흠을 「생김/문서와 다름」으로 가르고 지금 동작·바라는 계약(xfail)을 시험으로 고정 (esg-app 가상환경 python, 실호출 0) |
| `py/dvcolab/` | Python | 데이터 검증 코랩 대조 도구: 실행 때 읽기판에서 코랩 코드를 읽어 지은 사례로 돌리고 esg-app 엔진 결과와 견줌 (사본·실호출 0) |
| `py/evalrig/` | Python | 0차 평가 장치: 모듈별 정답 셋 꼴 · 채점 단위 꼴 · 1차 결과 변환기 · 채점기 · 실비 0 측정 (공통 `common/` 은 EVA 소유, AI 호출·밖 HTTP 0) |
| `ts/kernel/` | TypeScript | 패키지들이 같이 쓰는 작은 커널 (십진 연산, 수치의 출처 타입) |
| `ts/ghg-calc/` | TypeScript | Scope 1·2 계산 엔진 |
| `ts/factor-assembly/` | TypeScript | 계수 조합기: 계수 표의 낱줄을 지침의 규칙대로 묶어 계산 엔진의 계수로 조립 (고정연소, 구매 전력) |
| `ts/llm/` | TypeScript | LLM 호출 계층 |
| `ts/scope3/` | TypeScript | Scope 3 분류·계수 추천 시제품과 평가 도구 |
| `ts/docpipe/` | TypeScript | 긴 문서 분할 처리 파이프라인 |
| `ts/reconcile/` | TypeScript | 데이터 검증 시제품: 두 문서(PDF 문서 모델, 엑셀)의 숫자를 코드가 뽑고, 뜻은 AI 가 셀·블록 ID 를 가리켜 붙이고, 짝짓기·비교는 코드가 한다 |
| `schemas/` | JSON Schema | 두 언어가 공유하는 데이터 형식 (문서 모델, 계수 레코드, 계수 조립용 대응표) |
| `data/` | | 정답 표, 정규화된 계수, 예제 입력. 큰 원본(PDF, xlsx)은 git 밖 |

## 규칙

- Python은 `py/`에서 `uv`로, TypeScript는 `ts/`에서 `pnpm`으로 관리한다. 전역 설치 금지.
- 두 언어 사이의 접점은 `schemas/`의 JSON Schema와 `data/`의 파일뿐이다.
- 수치(배출계수, 발열량, GWP, 단가)는 출처 URL과 확인 일자 없이 넣지 않는다. 테스트용 가상 값은 파일명과 필드에 `synthetic`을 명시한다.
- LLM을 실제로 호출하는 코드는 키가 없을 때 동작하는 모의 공급사와 `--dry-run`을 반드시 갖춘다.
- 원본 자료 위치: `../prework/` (공개 보고서 PDF, 정답 표 15개, 측정 스크립트, 배출계수 원자료).
