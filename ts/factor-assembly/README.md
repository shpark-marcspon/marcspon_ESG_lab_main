# factor-assembly

계수 조합기. 계수 표(`data/factors/factors.jsonl`, 값 하나당 한 줄)의 낱줄을 지침의 규칙대로 묶어 계산 엔진(`ts/ghg-calc`)의 연소 계수(`CombustionFactor`) 한 건으로 조립한다. 범위는 **고정연소**와 **구매 전력**(엔진의 `PurchasedEnergyFactor`)이다. 발주서는 `MARC-TICK-17`, `MARC-TICK-18`, 배경은 `MARC-ISSUE-14`, `MARC-ISSUE-21`.

지침: 「온실가스 배출권거래제의 배출량 보고 및 인증에 관한 지침」 환경부고시 제2025-64호 (2025-04-11 시행). 규칙의 원문 대조는 밴드 보고 `band-reports/N-factor-assembly.md`.

적재는 Python(`py/factors`), 조립은 TypeScript 다. 두 언어의 접점은 `schemas/` 와 `data/` 의 파일뿐이다.

## 실행

```
cd ts && pnpm install
pnpm -F factor-assembly test
pnpm -F factor-assembly typecheck
pnpm -F factor-assembly verify-handcalc      # 계수 표에서 조립 → 엔진의 검산 사례를 다시 통과하는지
cd factor-assembly && pnpm tsx src/cli.ts assemble <조립 조건.json>
cd factor-assembly && pnpm tsx src/cli.ts coverage [--json]     # 별표 12 연료 29개의 덮인 범위 (조합 셋 × 상태 지정 여부)
cd factor-assembly && pnpm tsx src/cli.ts assemble-electricity <조립 조건.json>
cd factor-assembly && pnpm tsx src/cli.ts compare-electricity --amount 1234.567 --unit MWh --gwp kr-ets-decree-annex2   # 비교 표 (기대값이 아니다)
```

CLI 는 표준 출력으로만 낸다 (파일을 쓰지 않는다). 종료 코드는 0 전부 일치 / 1 어긋남·실패 / 2 입력 오류. `--factors`, `--mapping` 으로 다른 파일을 줄 수 있다. `coverage` 의 `--states-from <대응표>` 는 「상태를 준 경우」에 줄 상태를 다른 대응표에서 읽는다 (변경 전 대응표와 견줄 때).

## 무엇을 묶는가

연료 연소의 배출량은 `사용량 × 발열량 × 기체별 배출계수 (× 산화계수, CO2 에만) × GWP` 다. 엔진은 이 가운데 발열량, CO2·CH4·N2O 계수, 산화계수를 한 덩어리로 받는데, 지침에서는 이 다섯 값이 서로 다른 표에 있고 **산정등급(Tier)** 에 따라 가져오는 표가 바뀐다.

| 매개변수 | Tier 1 | Tier 2 | 고르는 열쇠 |
|---|---|---|---|
| 발열량 (순발열량) | 별표 11 (IPCC 기본) | 별표 12 (국가 고유) | 연료 이름 |
| CO2 배출계수 | 별표 10 (IPCC 기본) | 별표 12 (국가 고유) | 연료 이름 |
| CH4·N2O 배출계수 | 별표 10 (Tier 1 뿐이다) | — | 연료 이름 + 사업장의 부문 |
| 산화계수 | 별표 6: 1.0 | 별표 6: 액체 0.99, 기체 0.995, 고체는 발전 0.99·그 밖 0.98 | 연료 상태 (+ 고체는 발전 여부) |

- 산화계수의 등급은 CO2 배출계수의 등급과 같아야 한다 (별표 6).
- 별표 10·11 은 IPCC 연료 이름(가스/디젤 오일)을, 별표 12 는 국내 이름(경유)을 쓴다. 둘을 잇는 것이 대응표의 `fuel_names` 다. 항목에 `parameters` 가 있으면 그 매개변수에만 쓴다 (부생연료 1·2호는 CO2 만 기타 등유·B-C유 계수를 쓰고 CH4·N2O 는 대응이 없어 실패한다).
- 별표 12 안에서도 발열량 표와 배출계수 표가 이름을 다르게 적는 연료가 있다 (항공유 / 항공유(JET-A1)). CO2 Tier 2 낱줄은 대응표의 `national_ef_names` 로 찾고, 없으면 같은 이름으로 찾는다.
- Tier 3·4 는 사업장이 측정·개발한 값이 있어야 해서 고시의 표로는 조립할 수 없다. 실패로 돌려준다.
- 어느 등급을 써야 하는지의 최소기준은 시설 규모 그룹에 달려 있다 (별표 5). A 그룹(연 5만 t 미만)은 발열량만 Tier 2 이고 계수와 산화계수는 Tier 1 이다.

## 입력과 출력

```ts
assembleCombustionFactor(request, records, mapping) → { ok: true, value: { factor, evidence } } | { ok: false, failure }
```

조립 조건(`AssemblyRequest`)은 전부 고객사나 사업장 설정에서 와야 하는 값이고 **기본값이 없다**. 빠지면 조립하지 않고 `missing_input` 으로 무엇이 빠졌는지 돌려준다.

| 필드 | 뜻 | 없으면 |
|---|---|---|
| `fuel` | 국내 연료명 (별표 12 의 이름) | 실패 |
| `combustionType` | `stationary` 만 | 실패 |
| `industry` | `{ sector }` 또는 `{ ksicCode }` (한국표준산업분류 코드) | 실패 |
| `tiers` | `{ kind: "explicit", calorific, co2, oxidation }` 또는 `{ kind: "group_minimum", group }` | 실패 |
| `fuelState` | 고체·기체·액체. 대응표에 그 연료의 상태가 없을 때만 | `fuel_state_unknown` |
| `powerGeneration` | 고체연료에 Tier 2 산화계수를 쓸 때만 | `missing_input` |

출력의 `factor` 는 엔진이 그대로 받는 계수이고 `sector`, `parameterTiers` 가 채워져 있다. `evidence` 에는 매개변수마다 고른 낱줄(레코드 id, 원문 위치, 파일 해시, 확인 일자, `value_raw`)과 적용한 규칙, 부문과 등급을 정한 근거, 그리고 **추정 대응을 탄 자리**(`assumptions`)가 실린다. 추정을 탔다는 것은 `factor.source.note` 에도 적혀 엔진의 계산 근거까지 따라간다.

## 실패

| kind | 뜻 |
|---|---|
| `missing_input`, `invalid_input` | 조립 조건이 빠졌거나 틀렸다 (채워 넣지 않는다) |
| `unsupported_combustion_type` | 고정연소가 아니다 |
| `fuel_state_unknown` | 그 연료가 고체·기체·액체 중 무엇인지 대응표에 없다 |
| `fuel_state_conflict` | 입력한 연료 상태가 대응표와 다르다 |
| `sector_not_defined` | 지침의 부문 정의에 없는 업종 코드 |
| `sector_ambiguous` | 두 부문에 같이 적힌 업종 코드(38, 58, 59). 고르지 않는다. 부문을 직접 주거나 원문 주석이 정해 준 더 자세한 코드(383, 5821, 5912)를 준다 |
| `tier_combination_invalid` | 지침이 허용하지 않는 등급 조합 |
| `tier_requires_site_values` | Tier 3·4 |
| `tier_minimum_not_found` | 대응표에 그 그룹의 최소기준이 없다 |
| `fuel_name_not_mapped`, `fuel_name_ambiguous` | 국내 이름을 IPCC 이름으로 바꿀 수 없다 (비슷한 연료로 대신하지 않는다) |
| `record_not_found`, `record_ambiguous` | 조건에 맞는 낱줄이 없거나 둘 이상 |
| `record_value_missing`, `invalid_value_raw`, `unit_notation_unknown` | 낱줄의 값이 비었거나, 십진 표기가 아니거나, 모르는 단위 |
| `guideline_version_mismatch` | 다른 고시 판의 낱줄이 섞였다 |

## 십진 정확도

계수 값은 레코드의 `value`(number)가 아니라 `value_raw` 문자열에서 읽는다 (`readValueRaw`). 규칙: 십진 표기는 그대로, 세 자리마다 쉼표를 찍은 표기(`73,200`)는 쉼표만 걷어 낸다. 그 밖(지수 표기, 공백, `+`·`-` 부호, 자리가 틀린 쉼표, 빈 값)은 실패다. JS `number` 를 거치지 않는다.

## 배치와 포트

```
src/domain/types.ts     조립 조건, 근거, 실패의 형식
src/domain/policy.ts    정책: 「그룹 + 최소기준」이나 직접 지정 → 매개변수별 산정등급 (resolveTiers)
src/domain/sector.ts    업종 코드 → 부문 (resolveSector)
src/domain/assemble.ts  낱줄 고르기와 조립 (assembleCombustionFactor). 순수 함수
src/domain/valueRaw.ts  value_raw → 십진 문자열
src/domain/replay.ts    검산 재통과 (조립 → 엔진 → 기대값 대조). 순수 함수
src/domain/electricity.ts  구매 전력 조립과 선택지 비교. 순수 함수
src/domain/coverage.ts  덮인 범위 세기. 순수 함수
src/adapters/files.ts   계수 표와 대응표를 읽는 얇은 파일 어댑터 (zod 로 검증)
src/cli.ts              verify-handcalc, assemble
```

`domain/` 은 파일·네트워크·시계를 쓰지 않는다 (`test/files.test.ts` 가 지킨다). 조립 함수의 포트는 인자 둘이다: 계수 레코드 묶음(`FactorRecord[]`)과 대응표(`AssemblyMapping`). 지금 구현은 `loadGuidelineRecords`(jsonl), `loadMapping`(json) 이고, DB 로 옮기면 이 둘만 바꾼다.

## 구매 전력

```ts
assemblePurchasedElectricityFactor(request, records, mapping) → { ok: true, value: { factor, evidence } } | { ok: false, failure }
```

어느 전력배출계수를 쓸지는 고객 답 대기다 (`MARC-ISSUE-20` 15·17번). 그래서 **전부 입력이고 기본값이 없다**.

| 필드 | 값 | 없으면 |
|---|---|---|
| `energyType` | `electricity` (열은 범위 밖) | 실패 |
| `source` | `guideline_table69` (지침 별표 6 <표-69>, '14∼'16 평균) / `gir_published` (온실가스종합정보센터 공표) | 실패 |
| `gasForm` | `per_gas` (CO2·CH4·N2O 셋, GWP 는 엔진이 곱한다) / `co2e` (공표된 CO2 환산 값 하나, GWP 를 다시 곱하지 않는다). 섞는 입력은 실패 | 실패 |
| `gir.edition` | 2024, 2025 (계수 표의 `gir-2024`, `gir-2025`) | GIR 이면 실패 |
| `gir.period` | `{kind:"single_year", year}` / `{kind:"three_year_average", from, to}` / `{kind:"not_stated"}` (2024 공표는 표에 기간이 없다) | GIR 이면 실패 |
| `gir.boundary` | `generation` (발전단) / `consumption` (소비단) | GIR 이면 실패 |
| `gir.methodology` | `ipcc_2006` (본표) / `ipcc_1996_legacy` (2025 공표 주석의 기존 산정기준 표) | GIR 이면 실패 |

- 표-69 에는 판·기간 선택·발전단/소비단·산정기준 구분이 없다. `gir` 를 주면 실패, `co2e` 를 고르면 그 낱줄이 없어 실패다.
- 낱줄은 기체마다 정확히 하나여야 한다. 없는 해는 가까운 해로 대신하지 않는다.
- 지침 Tier 2 문장의 단서(「향후 … 센터에서 확인하여 지침에 수록된 경우 그 값을 적용」)로 GIR 값이 표-69 를 대신하는지는 판단하지 않는다. 근거의 `sourceChoice` 와 계수의 `source.note` 에 「출처는 입력이었다」 가 남는다.
- 출력 계수의 `id`·`variant` 에 출처·발전단/소비단·기간·산정기준·기체 구분이 들어간다. 단년이면 `year` 가 실린다.
- 검산 H06·H07 은 표-69 조립 결과로 다시 통과한다 (`data/ghg-calc/handcalc-assembly.json` 의 `electricityReplays`. 표본이 표-69 를 썼기 때문이고 고객사 계수를 정한 것이 아니다). `compare-electricity` 는 같은 사용량을 선택지 21개로 계산한 비교 표다.

## 덮인 범위 (`coverage`)

별표 12 순발열량 표의 연료 29개(전기 2개 포함)를 전부 Tier 2, A 그룹 최소기준, 전부 Tier 1 로, 연료 상태를 주지 않은 경우와 준 경우로 조립하고, 조립되면 그 연료의 별표 12 단위(L, kg, Nm3)로 사용량 1000 을 엔진에 넣어 계산되는지까지 센다. 업종은 제조업·건설업, 발전 여부는 false 로 고정한다. 결과는 `test/coverage.expected.json` 에 고정돼 있고 `test/coverage.test.ts` 가 지킨다 (덮인 범위가 조용히 줄거나 늘면 빨개진다). 전부 Tier 1 은 조립돼도 발열량이 TJ/Gg 라서 계산이 안 된다 (L·Nm3 는 밀도, kg 는 kg→Gg 환산이 엔진의 환산표에 없다).

## 정하지 않은 것

- 적용 Tier, 시설 규모 그룹, 업종 코드, 발전 여부의 기본값. 전부 입력이다.
- 배출량으로 그룹을 판정하는 일 (그룹을 알려면 배출량이 있어야 해서 순환이다).
- Tier 1 발열량(별표 11)은 질량 기준(TJ/Gg)이다. 리터로 잰 액체연료에 쓰려면 밀도가 필요하고, 조합기는 밀도를 갖고 있지 않다. 계수는 조립되지만 엔진에서 단위 환산이 없어 실패한다.
- 이동연소, 구매 열(스팀), 연료 성상(밀도). 구매 전력 계수의 기본값.
