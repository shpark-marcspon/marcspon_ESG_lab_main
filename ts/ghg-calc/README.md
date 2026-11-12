# ghg-calc

Scope 1·2 온실가스 배출량 계산 엔진. 활동 데이터와 기준정보 표를 값으로 받아 배출량과 근거를 값으로 돌려주는 순수 함수다. 파일·네트워크·시계를 쓰지 않는다 (파일을 읽는 것은 예제 CLI 인 `src/cli.ts` 뿐이다).

계수, 발열량, GWP, 단위 환산 계수는 코드에 없다. 전부 인자로 받는 표에 있다.

## 실행

```
cd ts && pnpm install
pnpm -F ghg-calc test
pnpm -F ghg-calc typecheck
cd ghg-calc && pnpm tsx src/cli.ts calc ../../data/ghg-calc/examples/activity.synthetic.json --factors ../../data/ghg-calc/examples/factors.synthetic.json
```

CLI 옵션: `--factors` 는 여러 번 줄 수 있고 표를 이어 붙인다. `--gwp <id>` 가 없으면 활동 데이터 파일의 `gwpSetId` 를 쓴다. 둘 다 없으면 계산하지 않는다. `--group-by orgUnit,period,scope,sourceType`. 종료 코드는 0 전부 성공, 1 실패한 행 있음, 2 입력 오류.

## 식

연료 연소 (고정·이동):

```
에너지량        = 사용량 × 발열량
기체별 배출량   = 에너지량 × 기체별 배출계수 (× 산화계수, CO2 에만, 계수에 있을 때만)
tCO2eq         = Σ 기체별 배출량(t) × GWP
```

구매 전력·열: `기체별 배출량 = 사용량 × 기체별 배출계수`. 계수가 이미 CO2eq 로 환산된 값 하나(`CO2e`)이면 GWP 를 곱하지 않는다.

이 구조는 「온실가스 배출권거래제의 배출량 보고 및 인증에 관한 지침」 별표 6 의 식을 따른 것이다 (근거와 확인 일자는 `../../data/ghg-calc/` 의 각 파일과 밴드 보고).

## 계수의 구분

같은 연료·연소 유형의 연소 계수를 가르는 것은 네 필드다. 계수와 활동 데이터 행이 같은 필드를 갖는다. 예전에는 `variant` 문자열 하나에 부문·산정등급·발전 여부가 섞여 있었다.

| 필드 | 뜻 |
|---|---|
| `sector` | 부문. CH4·N2O 기본 배출계수가 부문마다 다르다. 엔진은 뜻을 풀지 않고 같은지만 본다 |
| `parameterTiers` | 매개변수별 산정등급 `{ calorific, co2, nonCo2, oxidation }`. 한 계수 안에서도 다를 수 있다 (A 그룹 최소기준은 발열량 Tier 2, 계수·산화계수 Tier 1). 산화계수가 없으면 `oxidation: null` |
| `facilityUse` | `power_generation` / `other`. 고체연료의 Tier 2 산화계수가 이것으로 갈린다 |
| `variant` | 위 셋에 들지 않는 그 밖의 구분 (이동연소의 차종 등) |

구매 전력·열 계수에는 `variant` 와 연도만 있다 (발전단·소비단, 공급자 등). 행에 `sector` 나 `parameterTiers` 를 적으면 맞는 계수가 없다.

계산 결과의 근거(`evidence`)에는 쓴 계수의 `sector`, `parameterTiers`, `facilityUse` 가 실린다. 계수에 적혀 있지 않으면 `null` 이다.

## 십진 연산과 반올림

- 모든 수치는 십진 문자열로 받고 `bigint` 기반 십진수(공유 커널 `ts/kernel` 의 `Decimal`. `src/decimal.ts` 는 그 재수출이다)로만 연산한다. JS `number` 로 적힌 수치는 거부한다.
- 식에 나눗셈이 없다. 단위 환산도 곱셈 계수로 받는다. 그래서 결과는 항상 유한 소수이고 **계산과 집계에는 반올림이 없다**. 행 결과와 합계는 정확한 값의 문자열이다.
- 반올림은 `roundDecimal` (`roundForReport`) 한 곳에만 있고, 자릿수와 방식(`half_up`, `half_even`, `down`)에 기본값이 없다. 보고 단계에서 호출자가 정한다.
- 집계 규칙: 합계는 행의 정확한 값을 더한 것이다. 반올림한 행 값을 더하지 않는다. 어떤 기준으로 묶어도 묶음 합의 합, 전체 합, 행 값의 합이 문자열까지 같다. 보고용 자릿수는 합계에 한 번만 적용한다.

## 실패

계산하지 못한 행은 0 이나 빈 결과가 아니라 `status: "failed"` 와 판별 유니온 `failure` 로 돌아온다.

| kind | 뜻 |
|---|---|
| `invalid_activity` | 사용량이 십진 문자열이 아니거나 음수, 연소 행에 연료가 없음 |
| `factor_not_found` | 조건에 맞는 계수가 없음 (비슷한 계수로 대신하지 않는다) |
| `factor_ambiguous` | 조건에 맞는 계수가 둘 이상 |
| `factor_kind_mismatch` | `factorId` 가 다른 종류의 계수를 가리킴 |
| `factor_incomplete` | 기체 하나가 빠진 계수 (빠진 기체를 0 으로 치지 않는다) |
| `invalid_reference_value` | 표의 값이 십진 문자열이 아님 |
| `calorific_basis_mismatch` | `requireCalorificBasis` 와 다른 종류의 발열량 |
| `unit_conversion_not_found` | 환산표에 없는 단위 조합 |
| `unit_conversion_ambiguous` | 같은 조합에 계수가 둘 |
| `gwp_set_not_found`, `gwp_set_ambiguous`, `gwp_value_missing` | GWP 세트를 정할 수 없음 |

집계는 실패한 행을 합계에서 빼고 `failedRowIds` 와 `complete: false` 로 드러낸다.

## 정하지 않은 것

- **GWP 세트의 기본값이 없다.** 한국의 배출권거래제(시행령 별표 2: CH4 21, N2O 310)와 국가 인벤토리(IPCC 제5차 평가보고서: CH4 28, N2O 265)가 서로 다른 값을 쓴다. 호출자가 `gwpSetId` 를 준다.
- **계수 선택은 정확히 일치할 때만.** 연료 이름, 연소 유형, 구분, 연도가 모두 같아야 한다. 비워 둔 쪽은 비워 둔 계수에만 맞는다. 어느 배출원에 어느 계수를 쓰는지는 표를 만드는 쪽이 정한다 (한국 지침의 고정연소 계수와 구매 전력 계수는 `ts/factor-assembly` 가 계수 표에서 조립한다).
- 단위 환산은 표의 (from, to) 쌍을 그대로 찾는다. 역방향 유도나 다단계 연결을 하지 않는다.

## 파일

```
src/decimal.ts    십진 연산, 반올림 (ts/kernel 의 재수출)
src/types.ts      표·활동 데이터·결과의 형식
src/units.ts      단위 환산
src/calc.ts       행 계산
src/aggregate.ts  집계, 보고용 반올림
src/tables.ts     표 검사(출처 필수), 병합
src/cli.ts        예제 CLI
test/             단위·검산 사례·속성·CLI 테스트
```

데이터는 `../../data/ghg-calc/` 에 있다: `gwp-sets.json`, `unit-conversions.json`, `factors.kr-ets-sample.json` (손으로 묶은 표본 3건. 조합기의 결과와 대조하려고 남겼다), `handcalc-cases.json` (검산 사례), `handcalc-assembly.json` (검산 사례를 조립한 계수로 다시 돌릴 때의 조립 조건), `examples/*.synthetic.json` (가상 값).
