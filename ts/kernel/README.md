# kernel

공유 커널. 여러 패키지가 같은 뜻으로 쓰는 값 타입만 둔다. 다른 워크스페이스 패키지에 기대지 않고 파일·네트워크·시계를 쓰지 않는다. 발주서는 `MARC-TICK-12`, 근거는 `MARC-RPT-8`.

| 이름 | 내용 | 쓰는 곳 |
|---|---|---|
| `Decimal`, `parseDecimal`, `formatDecimal`, `isDecimalString`, `mul`, `add`, `ZERO`, `equals`, `isNegative`, `roundDecimal`, `RoundingMode` | 십진 정확 연산. 값 = coef / 10^scale (`bigint`). 나눗셈이 없고 반올림은 `roundDecimal` 한 곳뿐이다 | `ghg-calc`, `scope3` (각자의 `src/decimal.ts` 가 재수출한다) |
| `Provenance` | 수치의 출처. `cited`(제목·URL·확인 일자·위치), `definition`, `synthetic` | `ghg-calc` (`src/types.ts` 가 이것을 재수출한다). `scope3` 와 `docpipe` 는 아직 쓰지 않는다 |

여기에 넣지 않는 것: 단위(`ghg-calc` 의 환산표와 `scope3` 의 차원 모델은 용도가 다르다), 배출계수, 비용 반올림(`llm` 의 `roundUsd`).

```
cd ts && pnpm -F kernel test
cd ts && pnpm -F kernel typecheck
```
