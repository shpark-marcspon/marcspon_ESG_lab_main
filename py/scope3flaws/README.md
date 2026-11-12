# scope3flaws — Scope 3 1차 엔진 흠 회귀 묶음 (EBS2)

밴드 EBS2 `sid:MARC-TICK-629` · `sid:MARC-ISSUE-678`. 층 문서 `band-handoffs/s3-layers-1112.md`(11-12, 코드 한 번 읽음)가 적은 흠을
**지어낸 입력으로 1차 엔진(`esg-app/apps/api-py/esgapi/inventory/engines/scope3/colab_157/`)을 실제로 돌려** 「생김 / 안 생김 / 문서와 다름」을
가르고, 지금 동작을 시험으로 고정한다. 로직을 처음부터 다시 짤 때(2차 새 흐름) 「새 흐름은 여기서 달라야 한다」의 잣대다.

- 사례 목록은 `CASES.md`(S01~S29 — id · 고정 · 바꿀 하나 · 볼 값 · 층 문서의 주장 · 계약).
- `test_current_*` = 지금 동작 고정(통과해야 함). `test_contract_*` = 층 문서 2-1 「처음부터 짠다면」 한 줄. 지금 지켜지지 않으면
  `xfail(strict=True)` — 새 흐름이 계약을 지키면 XPASS 로 빨개지니 그때 표지를 뗀다. 지금도 지켜지는 계약은 xfail 없이 둔다.
- 1차 엔진·`scope3colab` 은 import 만 한다(`sid:MARC-ENT-683`, 0줄). 엔진 객체를 바꾸는 자리는 `runner.post_fails` 하나(S23 —
  실행 동안만 `pipeline.postprocess_category` 이름을 던지는 가짜로 갈아 끼우고 되돌림).
- 자료는 전부 지은 것(synthetic): 템플릿은 v2.2 합본판 짓개(`scope3colab.synthetic_merged`, 57c211a) 위에 사례 줄만 바꿔 얹고(`build.py`),
  계수 책은 회색 칸 판의 것(`synthetic_filled`)이다. 사례가 더한 계수 줄(S25 0 계수, S29 합본 Title)은 비고에 `synthetic`.
  받은 자료·읽기판은 열지 않는다. 배출계수 수는 새로 적지 않았다(엔진 상수는 엔진에서 import 해 단언).
- AI·지오코더는 가짜(`runner.FakeLlm`·`FakeGeocoder`) — 정해진 답·정해진 실패(5xx·시간 초과)만. 실호출 0, 키 파일 0, 시험은 소켓을 막고 돈다.

## 명령

esg-app 가상환경 python 으로 돈다(엔진이 pandas 3.0.6·Django 설정 없는 esgapi 를 쓴다 — miglab 꼴, `env.py` 가 `py/` 와
`esg-app/apps/api-py` 를 sys.path 앞에 넣는다. esg-app 은 읽기만). esg-lab 가상환경으로 돌리면 conftest 가 이 안내와 함께 멈춘다(코드 4).

```
cd esg-lab/py && <작업 폴더>/esg-app/apps/api-py/.venv/bin/python -I -m pytest scope3flaws -q -p no:cacheprovider
<작업 폴더>/esg-app/apps/api-py/.venv/bin/ruff check --isolated --line-length 140 --select E,F,W,I,B,UP,SIM scope3flaws
<작업 폴더>/esg-app/apps/api-py/.venv/bin/ruff format --isolated --line-length 140 --check scope3flaws
```

esg-lab 에는 저장소 린트 설정이 없다(`scope3colab` 도 위 규칙으로 깨끗하지 않음) — 이 패키지만 위 꼴로 깨끗하게 둔다.
`OPENPYXL_LXML=False`(`__init__.py`): esg-app 가상환경의 lxml 쓰기 꼴을 v2.2 짓개의 정규식이 못 잡아서 끈다(엔진 읽개 값과 무관).

## 가름표

29 사례: **생김 21 · 문서와 다름 6 · 안 생김 0 · 층 문서에 없음 2**. 계약 시험 25 — 지금 실패(xfail) 24, 지켜짐 1(S19). 계약 시험 없이
「지켜짐」을 주석으로 적은 것 넷(S18·S20·S22·S26). 코드 자리 줄임 `c/` = `colab_157/`.

| 사례 | 층 문서 ID | 가름 | 실제 동작(시험이 고정한 값) | 코드 자리 |
|---|---|---|---|---|
| S01 | L0-1 | 생김 | `C13-2 면적` → C13-1 에너지 처리기(면적 배분 안 함, 산정방식 「에너지사용량」) | `c/template.py:136-145` |
| S02 | L0-2 | **문서와 다름** | 안내 낱말·80자 넘는 줄은 말없이 빠짐(5줄 중 3) — 그러나 보는 것은 **첫 칸(국가)** 뿐이라 90자 품목명 줄은 남는다(「긴 품목명 줄도 빠진다」 아님) | `c/template.py:305-314`(`first_col = df.columns[0]`) |
| S03 | L0-3 | **문서와 다름**(일부) | 달 칸이 차면 연간 5,000 버림 · 두 달만 차도 그 합 그대로(1년 값) · 달 합 -200 → 빈 값(missing amount). 그러나 **연간 칸의 음수는 남는다**(co2e 음수) — 「음수 사라짐」은 달 합만 | `c/template.py:370-383`(`s.where(s > 0)`) |
| S04 | L0-4 | 생김 | 「(예시)」 표지 줄도 셈·합에 든다 | 거름 0 |
| S05 | L0-5 | 생김 | 머리 줄만 → `failed`(ValueError), 실행 done | `c/template.py:296-297` → `c/pipeline.py:248-252` |
| S06 | L1-2 | 생김 | C10 시트 → `{"sheet", "status": "not_target"}` 뿐(카테고리·까닭 칸 0) | `c/template.py:112-134` |
| S07 | L2-1 | 생김(조건) | 「단위」 칸이 있을 때 USD·EUR·모르는 통화·KRW 다섯 줄 co2e 가 다 같다(환산 전 9,100 × 원화 계수). USD 줄 spendKrw = 9,100 × 1,422.44, 차이 = 환율 배. **받은 v2.2 C1 에는 단위 칸이 없어** 사용자가 칸을 더해야 생긴다 | `c/purchase.py:1025-1047`(덮기) 대 `:839` |
| S08 | (없음) | 층 문서에 없음 | 「통화」 칸만 있으면 무시(표준화가 「단위」=KRW 를 먼저 채움 → `단위 or 통화` 에서 단위가 이김) | `c/purchase.py:1011-1018`·`:219` |
| S09 | L2-2 | 생김 | C1 USD 환율은 보고연도 2024·2026 에도 2025 연평균. C4-2 는 그 해 원화 열이 없으면 「2025년말」 열을 말없이 씀 | `c/purchase.py:196-201` · `c/factor_book.py:102-117` |
| S10 | L2-3 | 생김 | AI 배수 실패: C3 `가상와트` 100 을 그대로 kWh 계수와 곱함(origin estimate, missing 0). C4-1 모르는 무게 단위 × 1 = kg(via ai_failed) | `c/energy.py:612-654` · `c/transport.py:238` |
| S11 | L2-4 · 591 | 생김 | 같은 줄(전력 100 `가상와트`, AI 실패): C3·C8-1 은 곱함, C11 은 「[단위 불일치로 산정 보류]」 null — 보류도 missing 0 | `c/energy.py:1013-1026`·`:1052-1055` 대 `:718-740` |
| S12 | L2-5 | 생김 | C6 인원·횟수 1 · C7 직원 1·근무일 245 · C4-1 무게 단위 빈 칸 = kg · C12 `lb` = kg · C11 수명 글 = 1년. 모두 missing 0 | `c/travel.py:219-220`·`:252-254` · `c/transport.py:224-225` · `c/product.py:278-284` |
| S13 | L2-6 | 생김 | 재활용 비중 `1` → 100%(합 199%), 합 109% 도 그대로 셈, missing 0 | `c/product.py:258-261`(`_pct`) |
| S14 | L2-7 | 생김 | 사용 면적 빈 칸 하나 → 시트 failed(TypeError, 멀쩡한 줄도 빠짐). 비율 1.5 받아 13,500 kWh | `c/energy.py:922-950`(`:935`) |
| S15 | L3-2 | 생김 | 5xx·시간 초과 → 거리 줄 co2e null, via none, 실행 done. 표지는 `missing: ["amount"]` — 활동량 빈 줄과 같은 표지(「AI 실패」 가름 없음) | `c/distance.py:228-255` · `c/serialize.py:156-162`(missing amount) |
| S16 | L3-3 | **문서와 다름** | 본 고리 road·KR 고정은 생김(해상 줄에 도로 × 1.3 거리 650 km). 그러나 도로 상한 800 을 넘으면 「빈 값」이 아니라 `calc_transport` 의 **두 번째 길**(`infer_distance`)이 줄의 수단(sea)으로 메운다(2,600 km) | `c/activity.py:590-598` · `c/transport.py:382-386` |
| S17 | L3-4 | 생김 | 지오코더 99 km 는 그대로, 101 km 는 AI 직선 300 × 1.3 = 390 으로 갈음(via commute_ai_check, origin ai) | `c/travel.py:263-273` |
| S18 | L3-5 | 생김 | 비고 「왕복」 → × 1, 없으면 × 2(배수는 `distance.roundTrip` 에 보임 — 계약 지켜짐) | `c/travel.py:162-169` |
| S19 | L3-6 | 생김 | 키워드 밖 수단 + AI 실패 → road(계수 road). 폴백은 mode origin estimate(via fallback) — 계약 지켜짐 | `c/transport.py:206` |
| S20 | L4-4 | 생김 | 처리방법 빔·모름 → 코랩 상수 소각 평균(`WASTE_AVG_EF["소각"]`), 책에 그 폐기물의 줄이 있어도. origin estimate — 계약 지켜짐 | `c/waste.py:46-60`(`:49`) |
| S21 | L4-5 | **문서와 다름**(일부) | 도보·자전거는 기본값 0 이 「없음」으로 읽혀 승용차 계수(via car_fallback). **전기차는 낱말 「기차」 에 걸려 train**(기차 계수) — 「전기차 → 승용차」 아님, 다른 흠 | `c/travel.py:233-238` · `c/constants.py:676`(`TRAVEL_MODE_FIX_KEYWORDS` 「기차」) |
| S22 | L4-6 | 생김 | 운송 책에서 air 를 빼면 코랩 예시값(`DEFAULT_TRANSPORT_EF_MAP_PATCH["air"]`), 열 null, origin estimate — 계약 지켜짐 | `c/transport.py:270-287` |
| S23 | L5-1 | **문서와 다름** | 두 식은 생김(본 고리 × 2 고정 0.05538 대 후처리 왕복 × 1 0.02769). 후처리 실패 때 **본 고리 값이 결과에 남지 않는다**: frame 에 「배출량(tCO2e)」 열이 없어 C6 줄 co2e 전부 null, missing 0, 시트 processed, 카테고리 합 None | `c/activity.py:633-640`(`:639`) · `c/pipeline.py:285-296`(`:293`) · `c/serialize.py:189` |
| S24 | L5-2 | 생김 | 카테고리 합(`category_value`)은 null 줄을 말없이 뺀다. 값 칸은 total·sheets·failed_sheets 셋뿐 | `s/scope3_state.py:166-196` |
| S25 | L5-3 | 생김 | C3 사용량 0 → null(missing amount, 0 이 「없음」) · 계수 0 → null(missing 0) · C5 처리량 0 → null | `c/energy.py:1007`(`a or b` — 0 이 거짓이라 NaN 쪽) · `:1026`(`and ef_val`) · `c/waste.py:234` |
| S26 | L5-4 | **문서와 다름** | 빈 칸은 0 이 **아니다**: Scope2 빈 칸 → co2e null(missing amount·factor), 지분율 빈 칸 → 활동량 300 인데 co2e null. NaN 이 `or 0` 을 지난다(엔진 주석 「흠 ③」) | `c/product.py:372-383` |
| S27 | L6-1 · L8-2 | 생김 | 머리만 시트·면적 빈 칸 시트 failed, 거리 AI 실패가 있어도 실행 done. 결과 꼭대기 칸은 일곱(실패 수 칸 0) | `c/pipeline.py` · `c/serialize.py:516-535` |
| S28 | L6-3 | 생김 | 같은 입력 두 번 → 두 번 다 부름(도시 2·직선 1), 가짜 답이 다르면 co2e 다름. `factorBook` 칸은 sheet·column·year·reload·reloadProblem(책 판·지문·모델 0) | `c/distance.py:69-81` · `c/serialize.py:516-535` |
| S29 | a2 · S3V1 | 층 문서에 없음 | 출장 Spend 「항공권」 → 수동 표 Title 이 계수 책 Title 의 「 / 」 조각 하나와 같으면 그 줄(via manual). 코랩 쪽(글자 그대로 같음)은 a2 노트(S3C-o5 보고 ⑤-1)에 기댐 — 이 밴드는 코랩을 돌리지 않았다 | `c/spend.py:207-215` |

### 층 문서에서 바로잡을 자리(층 문서는 감독이 고친다)

1. L0-2 「긴 품목명 줄도 빠진다」 → 첫 칸(양식상 `*국가`)만 본다. 품목명 길이로는 안 빠진다.
2. L0-3 「음수(반품) 사라짐」 → 달 합 ≤ 0 만. 연간 칸 음수는 남는다.
3. L3-3 「국제 해상·항공 운송도 국내 도로 상한으로 걸러 빈 값」 → 상한을 넘으면 두 번째 길(`c/transport.py:382-386` `infer_distance`)이 줄의 수단·국가로 다시 구한다. 실제 흠은 **상한 밑**일 때 도로 거리(× 1.3)가 해상·항공 줄에 그대로 쓰이는 것.
4. L4-5 → 「전기차」 는 `car_ev` 가 아니라 낱말 조각 「기차」 로 train(`c/constants.py:676`). 0 계수 → 승용차는 도보·자전거에서 생김.
5. L5-1 「후처리가 실패하면 본 고리 값이 남고」 → 결과 dict 의 co2e 는 null(본 고리 값은 frame 「배출량」 열에만), missing 0.
6. L5-4 「빈 칸은 0」 → null(NaN 이 `or 0` 을 지남). 계약 「드러난다」는 missing 으로 지금도 지켜짐.
7. L2-1 → 받은 v2.2 C1·C2 에 단위·통화 칸이 없어 표준 양식으로는 안 생긴다. 「통화」 칸만 더하면 무시된다(S08).
8. L3-2 「말없이」 → `missing: ["amount"]` 은 붙는다(다만 입력 빈 줄과 같은 표지).
