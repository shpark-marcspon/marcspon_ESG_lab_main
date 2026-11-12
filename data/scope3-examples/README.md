# scope3-examples

`ts/scope3` 평가 도구의 예제 세트다. **전부 가상(synthetic) 내역**이고 실제 회사의 구매·물류·출장·통근 자료가 아니다. 품목, 수량, 금액, 거리는 지어낸 값이다.

| 파일 | 내용 |
|---|---|
| `rows.synthetic.jsonl` | 내역 148행. 행마다 `synthetic: true` 와 정답(`gold`)이 붙어 있다 |
| `memory.synthetic.json` | 가상의 확정 매핑 기억 9건. 실제 사람이 확정한 것이 아니다 |
| `adjustments.synthetic.json` | 가상의 환율·물가 보정 계수. 실제 환율이 아니다. `--adjustments` 로 줄 때만 쓰인다 |

## 정답

- `gold.category`: Scope 3 카테고리 번호. 사람도 가르기 어려운 행은 `null`.
- `gold.factor_ids`: 정답으로 인정하는 계수 id. `data/factors/factors.jsonl` 에 실제로 있는 id 이고 테스트(`ts/scope3/test/examples.test.ts`)가 지킨다. 계수 값은 적지 않는다.
- 한 행에 정답 id 가 여럿일 수 있다. 같은 재질의 한국 계수(KEITI, 라이선스 `disputed`)와 영국 계수(DEFRA)가 둘 다 있으면 둘 다 정답으로 둔다.
- `gold.factor_ids` 가 빈 배열이면 정답을 달 수 없는 행이다 (표에 맞는 계수가 없거나, AI 적용 범위 밖이거나, 내용을 알 수 없다). 이유는 `gold.note` 에 있다.

정답은 러너가 계수 이름을 보고 단 것이고 도메인 전문가의 검토를 받지 않았다. 정답의 정의(계수 id 일치 | 같은 분류 부문)는 `PRJ11-ISSUE-6` 에서 결정 대기다.

출장·통근의 정답은 DEFRA 의 영국 기준 계수다. 표에 한국 고유의 여객 계수가 없어 가장 가까운 것을 달았다 (예: KTX → National rail).
