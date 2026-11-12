# reconcile

데이터 검증 시제품. 두 문서(원천자료와 보고서)의 숫자를 서로 맞춰 본다. 화면·DB·서버가 없는 라이브러리와 CLI 다.
모의 공급사와 `--dry-run` 은 키 없이 돈다. 실제 공급사는 `--provider <공급사> --route <경로> --limit-usd <상한>` 을 다 줘야만 부른다 (돈이 든다). 발주서는 `MARC-TICK-19`(시제품), `MARC-TICK-20`(실제 공급사 배선과 채점 도구).

## 층마다 누가 하는가

| 층 | 누가 | 코드 |
|---|---|---|
| 숫자 값 | 코드. PDF 는 문서 파서(`py/docparse`)의 문서 모델에서, 엑셀은 셀의 저장 값에서 읽는다 | `domain/notation.ts`, `domain/facts.ts`, `adapters/xlsx.ts` |
| 표 격자 | 코드 (문서 파서, 엑셀은 시트의 빈 행으로 끊은 덩어리) | `adapters/xlsx.ts` |
| 뜻 (지표, 기간, 단위, 범위) | AI. 셀·블록 ID 를 가리키는 방식으로만 답한다 | `prompts/reconcile/label/1.md`, `domain/label.ts` |
| AI 답의 확인 | 코드. 없는 ID·없는 글자를 가리킨 답은 버린다 | `domain/label.ts` 의 `validateAnswers` |
| 빠짐없이 셌는가 | 코드. 답이 없거나 버려진 숫자는 미확인으로 남는다 | `domain/pipeline.ts`, `domain/summary.ts` |
| 짝짓기와 비교 | 코드. 뜻이 같은 것끼리만 짝짓고 그다음 값을 견준다 | `domain/match.ts` |

AI 의 답 스키마(`labelAnswerSchema`)에는 숫자 값을 적을 필드가 없다. `occurrence` 는 값이 아니라 「그 글자가 블록에서 몇 번째로 나온 것인가」의 순번이다.

## 명령

```
cd ts
pnpm -F reconcile exec tsx src/cli.ts facts <입력> --out <디렉터리>
pnpm -F reconcile exec tsx src/cli.ts count <입력> [<입력> ...] --out <디렉터리>
pnpm -F reconcile exec tsx src/cli.ts run <입력 A> <입력 B> --provider mock --out <디렉터리> [--kind-a .. --kind-b .. --memory <파일> --period-a 2025 --sentences]
pnpm -F reconcile exec tsx src/cli.ts run <입력 A> <입력 B> --dry-run --out <디렉터리> [--route <경로> ...] [--tables ..]
OPENAI_API_KEY=... pnpm -F reconcile exec tsx src/cli.ts run <입력 A> <입력 B> --provider openai --route light-openai --limit-usd <상한> --out <디렉터리> [--store-bodies --tables a:p46-t2,b:p21-t1 --timeout-ms .. --log ..]
pnpm -F reconcile exec tsx src/cli.ts score <입력 A> <입력 B> --gold <정답 파일> --run <run 의 출력 디렉터리> --out <디렉터리> [--transcribe <transcribe 의 출력 디렉터리>]
pnpm -F reconcile exec tsx src/cli.ts transcribe <입력 A> <입력 B> --tables <표 목록> (--provider mock | --provider openai --route .. --limit-usd .. | --dry-run) --out <디렉터리>
```

- 입력은 문서 파서의 `document.json` 또는 `.xlsx` 다. PDF 는 먼저 `py/docparse` 로 분해한다.
- `--out` 은 반드시 준다. 기본 출력 경로가 없다 (다른 실행의 산출물을 덮어쓰지 않게). 상대 경로는 `ts/` 기준이다.
- `run --provider mock` 은 `result.json`(전체 결과), `summary.md`(사람이 읽는 요약), `calls.jsonl`(호출 기록)을 쓴다. 같은 `--out` 으로 다시 돌리면 셋 다 새로 쓴다. 모의 응답기는 머리글 낱말이 지표 목록의 「다른 이름」과 글자로 같으면 그 지표라고 답하는 synthetic 이다. **결과 분포는 모델의 성능과 무관하다.**
- `run --dry-run` 은 공급사를 부르지 않고 경로별 호출 수·입력 토큰 어림·예상 비용·비용 상한을 낸다. 토큰은 글자 수 어림이고, 출력은 답 하나를 90 토큰으로 본 가정이다 (`data/reconcile/settings.json`).
- `run --provider <openai|anthropic|gemini>` 은 실제 공급사를 부른다. `--route` 는 그 공급사의 모델만 든 경로여야 하고(`light` 처럼 다른 공급사로 넘어가는 대체 모델이 섞인 경로는 거부한다), `--limit-usd` 와 그 공급사의 키 환경 변수가 있어야 한다. 하나라도 없으면 부르지 않고 종료 코드 2 다. 키는 명령 한 줄에만 싣는다.
- 실제 실행은 요청 단위(표, 큰 표는 행 묶음)의 결과를 `<out>/units.jsonl` 에 남긴다. 같은 `--out` 으로 다시 돌리면 답을 받은 요청(성공, 스키마 불일치, 잘림, 거부)은 다시 부르지 않고, 공급사 오류·시간 초과·상한에 막힌 요청만 다시 부른다. 호출 기록(`calls.jsonl`)은 이어 쓰고, 앞선 실행이 쓴 비용을 읽어 `--limit-usd` 에 같이 센다. 요청이 달라지면(표, 지표 목록, 프롬프트 버전, 경로, 출력 상한) 저장된 결과를 쓰지 않는다.
- `--tables a:p46-t2,b:p21-t1` 은 그 표만 모델에 보낸다 (작은 실행). 나머지 숫자는 미확인(`not_requested`)이다. 그 뒤 같은 `--out` 으로 전체를 돌리면 남은 표만 부른다.
- 상한은 이미 쓴 누적을 호출 직전에 본다. 동시에 나가는 요청 수(설정의 `concurrency`)만큼은 상한을 넘길 수 있다.

## 채점과 옮겨 쓰기 시험 (`src/eval/`, 측정 도구)

- **정답 파일** `data/reconcile/gold/kb-2025.gold.json`: KB금융 2025 보고서와 데이터북의 표 10개, 숫자 셀 312개에 단 뜻. **러너가 원문 PDF 를 보고 달았고 전문가 검토 전이다.** 지표 키는 synthetic 지표 목록의 것이라 목록이 바뀌면 다시 단다 (`checkGold` 가 목록의 해시, 표의 셀, 원문 글자를 대조한다). 정답의 `period`·`unit`·`boundary` 는 「요청에 실리는 글자만 보고 프롬프트 규칙대로 답할 때」의 값이고, 쪽의 본문이나 다른 각주까지 봐야 아는 값은 `truth` 에 따로 적었다 (채점에 쓰지 않는다). 원문 PDF 는 git 밖(`../prework/reports/`)이다.
- **`score`** 는 모델을 부르지 않는다. `units.jsonl` 의 답(코드 확인을 지나기 전의 것)을 정답과 셀마다 견줘 `score.json`·`score.md` 를 낸다: 지표·기간·단위·범위가 맞는 비율(분모는 정답이 지표인 셀 전부, 답이 없는 셀은 틀림), 목록에 없는 숫자에 억지로 지표를 붙인 수, 버려진 답과 빠뜨린 숫자, 짝의 뜻이 정답과 같은지(틀린 뜻으로 지어진 「일치」), 확신 등급별 정확도, 호출 기록에서 다시 센 토큰·비용·모델.
- **`transcribe`** 는 측정용 별도 경로다 (`prompts/reconcile/transcribe/1.md`). 뜻 붙이기와 같은 표를 보내고 대상 칸의 글자를 그대로 옮겨 적게 한 뒤, 요청에 실린 칸의 글자(텍스트 계층에서 코드가 읽은 것)와 견준다: 같음 / 표기만 다름(쉼표·공백) / 다름(글자 바뀜, 자리 바뀜, 빠짐, 겹친 답) / 지어냄(대상 밖의 칸). **이 글자는 대조에 쓰이지 않는다.** 대조에 쓰이는 숫자는 언제나 코드가 읽은 것이다. 모델에게 이미 칸으로 나뉜 표를 주므로, 모델이 PDF 에서 표를 직접 읽어 내는 경우보다 쉬운 조건이다.

## 방법 비교 (`src/compare/`, 측정 도구, MARC-TICK-21)

「AI 에게 일을 어디까지 맡기는가」가 다른 방법 여덟을 같은 시험지에서 재는 도구다. 기본 명령(`src/cli.ts`)과 따로 있고 기본 동작을 바꾸지 않는다.

- **시험지**: `data/reconcile/exam/kb-2025.pairs.json`(같은 항목임을 원문으로 확인한 쌍 58개)과 `kb-2025.plants.json`(문서 B 의 사본에 심는 오류 명세). **둘 다 러너 작성, 전문가 검토 전이다.** 심는 자리는 씨앗 고정 난수로 뽑았다 (`draw`). `build` 가 사본에 심고 「명세 밖의 칸은 원본과 같다」를 확인한다. 원본 문서 모델은 고치지 않는다.
- **선택 규칙** (기본은 끔, `MatchOptions`): `looseUnspecifiedBoundary`(범위가 한쪽만 표시 없음이면 짝짓고 값을 견준다), `dropRepeatedKeysInTable`(한 표 안에서 뜻이 같은 답이 둘 이상이면 버린다). `extractDocument` 의 `titles: "own"` 은 표 제목 후보로 그 표의 제목과 각주만 싣는다.
- **방법**: a(저장된 답), a2(+ 선택 규칙 둘), a3(제목·각주를 고쳐 다시 받음), b(표 하나 + 지표 목록 → 지표가 실린 칸), c(문서 통째 + 지표 목록), d(숫자를 가린 두 문서 → 칸의 짝), e(숫자째 두 문서 → 어긋나는 곳), f(숫자를 가린 표 목록 → 표의 짝, 그 안의 행·열은 코드가 맞춤). b·c·d·f 의 답 스키마에는 값 필드가 없고 값은 전부 `compareValues` 가 견준다.
- 요청 하나의 입력 어림이 250,000 토큰을 넘으면 만들지 않는다 (장문 단가의 경계 272,000). 넘으면 쪽 순서로 나눈다.

```
pnpm -F reconcile exec tsx src/compare/cli.ts build --a <A> --b <B 원본> --pairs .. --plants .. --out <디렉터리>
pnpm -F reconcile exec tsx src/compare/cli.ts run --method <a|a2|a3|b|c|d|e|f|g|h> --a .. --b .. --exam <시험지> --pairs .. --plants .. (--dry-run | --provider mock | --provider openai --route light-openai --limit-usd <상한> --log <공용 호출 기록> | --replay <실제 실행의 출력> --route light-openai --log <호출 기록>) --out <디렉터리>
pnpm -F reconcile exec tsx src/compare/cli.ts table --dir <방법별 출력> --plants .. --out <디렉터리>
```

### 방법 G 와 반복 실행 (MARC-TICK-22)

- **방법 g** (`src/compare/hybrid.ts`): 표 짝은 f 와 같은 요청(`reconcile/pair-tables@1`)으로 먼저 받고, 표 짝마다 요청 하나(`reconcile/pair-cells-in-tables@1`)로 칸 짝을 받는다. 그 요청에는 두 표와, 표마다 그 표의 제목·각주(`own`)와 같은 쪽의 글 블록만 싣는다 (같은 쪽의 다른 표는 싣지 않는다). 값 후보 숫자는 d 와 같은 방식으로 가린다. 조립한 요청 글에 값 후보 숫자가 남아 있으면(`unmaskedValues`) 요청을 만들지 않는다.
- **답 확인**: 없는 칸 ID, 그 요청의 두 표 밖의 칸, 원문에 없는 단위 글자를 가리킨 짝은 버린다. 남은 짝은 `compareValues` 가 값을 견준다. 답 스키마에 값 필드가 없다.
- **셈**: 두 문서의 모든 값 후보가 결과 하나에 들어간다: 견줌(일치·불일치·설명 필요) / 짝지어진 표 안인데 칸 짝이 안 나옴 / 표 짝 없음 / 표의 숫자 칸이 아님(문장 속, 숫자뿐인 블록, 표의 글 칸 속) / 이 칸을 가리킨 답이 버려짐. 합이 값 후보 수와 다르면 예외다. 한 칸이 여러 짝에서 견주어졌으면 가장 무거운 결과(불일치 > 설명 필요 > 일치)가 그 칸의 결과다. 신뢰도 낮은 표도 숫자 칸이 있으면 표 목록에 실린다 (f 의 요청과 같다).
- **dry-run**: 칸 짝 요청은 표 짝의 답이 와야 정해지므로 `--assume-table-pairs <표 짝 답 JSON>` 으로 준 표 짝으로 조립해 어림하고, 조립한 요청 글을 `<out>/requests/` 에 쓴다. 숫자 칸이 있는 모든 표의 한쪽 글에 값 후보가 남지 않는지도 같이 본다.
- **반복 실행** (`src/compare/runs.ts`, 명령 `runs`): 같은 방법을 `--out` 을 달리해 다시 돌린 결과를 나란히 놓는다: 방법 × 실행 비교표, 심은 칸이 몇 번 잡혔는지, 짝의 교집합·합집합, g 의 표 짝과 셈, 정답 쌍 밖의 일치의 합집합(d·g), d 는 견줬는데 g 는 못 견준 짝. 이어 하기는 `--out` 아래의 저장된 답만 다시 쓰므로, 실행마다 `--out` 이 다르면 같은 요청도 새로 부른다.
- **정답 밖 일치의 판정 기록**: `data/reconcile/exam/kb-2025.outside-gold.json`. d 와 g 가 정답 쌍 밖에서 「일치」로 낸 짝을 러너가 원문 PDF 로 본 것이다 (**러너 작성, 전문가 검토 전**). `runs --judgments` 가 건마다 붙여 낸다.

```
pnpm -F reconcile exec tsx src/compare/cli.ts run --method g ... --dry-run --assume-table-pairs <표 짝 답 JSON> --out <디렉터리>
pnpm -F reconcile exec tsx src/compare/cli.ts runs --run g=<실행 1>,<실행 2>,<실행 3> --run d=... --run c=... --pairs .. --plants .. [--judgments ..] --out <디렉터리>
```

### 방법 H: D 의 답 + 코드의 셈 + 좁은 검수 (MARC-TICK-23)

- **방법 h** (`src/compare/review.ts`): d 를 다시 부르지 않는다. `--d-run` 으로 준 d 의 실제 실행이 남긴 답(`units.jsonl`)을 읽기만 하고 그 뒤에 세 단계를 붙인다. 요청은 d 의 답과 문서에서만 조립한다 (`review.ts` 는 정답 쌍과 심은 오류 명세를 읽지 않는다. 채점은 `cli.ts` 와 `review-report.ts` 가 한다).
- **코드의 셈** (AI 없음): d 의 칸 짝에서 「표 짝」을 끌어낸다 (두 표 사이에 칸 짝이 `TABLE_PAIR_MIN_CELL_PAIRS` = 1개 이상이면 짝지어진 표). 두 문서의 모든 값 후보가 결과 하나에 들어간다: 견줌 / 짝지어진 표 안의 남은 칸 / 짝이 없는 표의 칸 / 짝이 없는 표의 칸인데 버려진 답이 가리킴 / 표의 숫자 칸이 아님. 합이 값 후보 수와 다르면 예외다.
- **칸 검수** (`reconcile/review-cells@1`): 남은 칸(짝지어진 표의 숫자 칸 가운데 어느 짝에서도 견주어지지 않은 칸)이 있는 표 짝마다 요청 하나. g 의 칸 짝 요청과 같은 두 표(값 후보는 가림) 뒤에 이미 짝지어진 칸 짝과 남은 칸의 ID 목록(`a:표ID:r행c열` 꼴)을 싣는다. 답은 남은 칸마다 하나: 상대 칸의 ID, 또는 사유(`no_such_row_or_column` 상대 표에 없는 행·열 / `different_item` 같은 항목이 아님 / `undecidable` 가를 수 없음). 값 필드가 없다. 남은 칸이 150개를 넘으면 목록을 나눠 요청을 여럿 만든다.
- **표 검수** (`reconcile/review-tables@1`): 요청 하나. f 의 표 목록에 이미 짝지어진 표와 그 상대를 표시해 주고, 짝이 없는 표의 상대가 더 있는지 표 ID 의 짝으로 받는다. 새 표 짝마다 g 의 칸 짝 요청(`reconcile/pair-cells-in-tables@1`)을 그대로 한 번 보낸다. 새 표 짝이 10개를 넘으면 멈춘다.
- **답 확인**: 남은 칸 목록에 없는 칸에 대한 답, 없는 ID, 그 요청의 두 표 밖의 상대 칸, 원문에 없는 단위 글자, 한 칸에 엇갈린 답은 버린다. **답이 안 온 남은 칸은 「미응답」이고 「상대 없음」으로 세지 않는다.** 새 짝은 `compareValues` 가 견준다. 한 칸이 여러 표 짝의 요청에서 물어졌으면 전부 「상대 없음」일 때만 「상대 없음」이다.
- **검수 뒤의 셈**: 견줌 / 검수가 「상대 없음」이라 한 칸(사유별) / 가를 수 없음·미응답·버려진 답 / 새로 짝지어진 표 안인데 칸 짝이 안 나옴 / 짝이 없는 표의 칸 / 표의 숫자 칸이 아님. 합 확인은 같다.
- 조립한 요청 글에 값 후보 숫자가 남아 있으면 요청을 만들지 않는다 (칸 검수는 g 의 `unmaskedValues`, 표 검수는 머리글 조각마다 같은 기준). `--dry-run` 은 요청 글을 `<out>/requests/` 에 쓴다.
- **`review-table`** (`src/compare/review-report.ts`): h 의 실행들을 나란히 놓는다: 검수 전·후 비교표, 되찾기(검수 전에 짝지어지지 않았던 정답 쌍), 단계별 셈, 끌어낸 표 짝, 새 짝, 「상대 없음」.
- **판정 기록**: `data/reconcile/exam/kb-2025.review-new-pairs.json`(검수가 새로 낸 정답 밖 짝), `kb-2025.review-no-counterpart.json`(검수가 「상대 없음」이라 한 남은 칸 × 상대 표). **둘 다 러너 작성, 전문가 검토 전이다.**

```
pnpm -F reconcile exec tsx src/compare/cli.ts run --method h ... --d-run <d 의 실제 실행 출력> (--dry-run | --provider openai --route light-openai --limit-usd <상한> --log <공용 호출 기록> | --replay <h 의 실제 실행 출력> --route light-openai --log <호출 기록>) --out <디렉터리>
pnpm -F reconcile exec tsx src/compare/cli.ts review-table --review <이름>=<h 의 실행 출력> [--review ...] --pairs .. --plants .. [--judgments ..] [--no-counterpart-judgments ..] --out <디렉터리>
```

### 서술 속 숫자와 같은 문서 안으로 넓히기: D2·H2·I (MARC-TICK-24)

앞 방법들은 표의 숫자 칸끼리, 두 문서 사이에서만 견줬다. 이 셋은 방법 h 의 뼈대(가린 문서에서 AI 가 자리만 답하고, 코드가 값을 견주고 세고, 남은 것만 검수)를 넓힌다. 명령은 `src/compare/cli2.ts` 에 따로 있고 방법 a~h 와 기본 동작을 바꾸지 않는다.

- **자리의 ID**: 값 후보의 ID 그대로다. 표의 숫자 칸은 `표 ID:r행c열`, 글 속 숫자는 `블록 ID#n순번`, 표의 글 칸 속 숫자는 `표 ID:r행c열#n순번`. 순번은 그 글에서 숫자 덩어리가 나온 차례라 값의 글자가 들어 있지 않다.
- **가림** (`src/compare/prose.ts` 의 `renderDocumentWithIds`): 방법 d 와 같은 꼴로 문서를 옮기되, 표의 숫자 칸이 아닌 값 후보는 `▒〔ID〕` 로 싣는다 (`%`·`%p` 는 남긴다). 값 후보 꼴이지만 뽑히지 않은 숫자는 ID 없이 `▒` 다. 조립한 요청 글에 값 후보 숫자가 남아 있으면(`unmaskedInIdDocument`) 요청을 만들지 않는다. `--dry-run` 은 요청 글을 `<out>/requests/` 에 쓰고, 심기 전의 원본 문서로 조립한 글과 글자까지 같은지도 본다 (심은 값이 요청 글과 ID 에 새지 않는다).
- **방법 d2** (`reconcile/pair-places@1`): 가린 두 문서 → 자리의 짝 (`a:…`, `b:…`). 표 칸끼리, 글 속 숫자와 표 칸, 글 속 숫자끼리 모두 대상이다. 없는 ID, 같은 문서끼리의 짝, 원문에 없는 단위 글자는 버린다. 요청을 나누는 방법은 d 와 같다. 셈은 방법 h 의 검수 전 셈과 같은 갈래이고, 짝이 안 나온 서술 숫자는 갈래별로 「짝 없음(서술)」이다. 표 짝은 표의 숫자 칸끼리의 짝에서만 끌어낸다.
- **방법 h2** (`reconcile/review-cells@2`): `--d2-run` 의 저장된 답 뒤에 코드의 셈과 칸 검수를 붙인다. 방법 h 와 다른 것 둘: 표 검수가 없고, **상대 칸은 그 요청의 남은 칸에서만 고른다** (남은 칸이 아닌 상대를 적은 답은 `counterpart_not_remaining` 으로 버린다. 사유에 `already_paired` 가 더해졌다). 서술 속 숫자는 칸 검수의 대상이 아니다.
- **방법 i** (`reconcile/group-places@1`): 문서 하나(`--doc a|b`)를 같은 방식으로 가려서 주고 「같은 항목·같은 기간·같은 범위·기준을 말하는 자리들」의 묶음을 받는다. 없는 ID 의 자리와 자리가 하나뿐인 묶음은 버린다. 한 자리가 두 묶음에 들면 묶음마다 견주고 그 사실을 남긴다. 묶음 안의 값은 코드가 둘씩 견준다 (`compareValues`). 문서의 모든 값 후보가 결과 하나에 들어간다: 묶여서 일치·불일치·설명 필요 / 묶이지 않음(갈래별).
- **넓힌 시험지** (`src/compare/exam2.ts`): `data/reconcile/exam/kb-2025.gold-v2.json`(서술이 낀 두 문서 사이의 정답 쌍, 문서 안 정답 묶음)과 `kb-2025.plants-v2.json`(새로 심는 자리). **둘 다 러너 작성, 전문가 검토 전이다.** 밴드 R 의 정답 쌍·명세는 그대로 두고 같이 쓴다. `draw2` 가 씨앗 고정 난수로 자리를 뽑고, `build2` 가 두 문서 모델의 사본에 심는다 (서술에 심을 때는 그 숫자 덩어리의 글자만 바꾼다) 그리고 「명세 밖은 원본과 같다」와 「값 후보의 ID·갈래가 그대로다」를 확인한다.
- **판정 기록**: `data/reconcile/exam/kb-2025.prose-judgments.json`. 서술이 낀 정답 밖의 짝, 묶음 안의 불일치 가운데 심은 것이 아닌 것, h2 의 새 짝을 원문으로 본 것이다 (**러너 작성, 전문가 검토 전**). `table2 --judgments` 가 건마다 붙여 낸다.
- 값 비교 규칙은 그대로다. 글 속 숫자에는 붙은 단위(`조 원`, `tCO2eq`)가 값 후보에 실려 있어, 상대 쪽의 단위를 AI 가 적지 않으면 「설명 필요」(`unit_missing_one_side`)가 된다.

```
pnpm -F reconcile exec tsx src/compare/cli2.ts draw2 --a <A 원본> --b <B 원본> --pairs .. --plants .. --gold2 <넓힌 정답> --seed <n> --out-spec <넓힌 명세>
pnpm -F reconcile exec tsx src/compare/cli2.ts build2 --a .. --b .. --pairs .. --plants .. --gold2 .. --plants2 .. --out <디렉터리>
pnpm -F reconcile exec tsx src/compare/cli2.ts run2 --method <d2|h2|i> --a .. --b .. --exam-a <시험지 A> --exam-b <시험지 B> --pairs .. --plants .. --gold2 .. --plants2 .. [--d2-run <d2 의 실제 실행 출력>] [--doc <a|b>] (--dry-run | --provider mock | --provider openai --route light-openai --limit-usd <상한> --log <공용 호출 기록> | --replay <실제 실행의 출력> --route light-openai --log <호출 기록>) --out <디렉터리>
pnpm -F reconcile exec tsx src/compare/cli2.ts table2 --d <d 의 실행,..> --d2 <이름=실행,..> --h2 <이름=실행,..> --i <이름=실행,..> --a .. --b .. --exam-a .. --exam-b .. --pairs .. --plants .. --gold2 .. --plants2 .. [--judgments ..] --out <디렉터리>
```

### 요약 숫자의 이름 묶기와 값 후보의 종류: D3a·D3b·I3 (MARC-TICK-25)

밴드 U 에서 짝지어지지 않은 「숫자뿐인 블록」(이름과 숫자가 따로 놓인 요약 숫자)과, 값 후보로 읽힌 값이 아닌 숫자(규격 번호, 기간, 눈금, 세어 본 수, 연도 표시)를 다룬다. 둘 다 옵션이다: 끈 채로는 방법 a~h·d2·h2·i 의 요청 글과 채점이 글자까지 같다.

- **한 줄 묶기** (`src/compare/prose.ts` 의 `pickNameBlocks`, `renderDocumentWithIds` 의 `bindNames`): 숫자뿐인 블록마다 이름 블록을 고른다. 문서 모델의 순서·좌표·블록의 높이만 본다: 같은 쪽의 글 블록, 값 후보가 들어 있지 않음, 가로로 겹침(좁은 쪽 너비의 절반 이상), 숫자 블록의 바로 위(사이가 이름의 높이 이하), 숫자 블록보다 높지 않음. 여럿이면 세로로 가장 가까운 것. 고르지 못하면 묶지 않는다(도표의 축 눈금처럼 이름 없이 쌓인 숫자). 묶인 숫자는 이름 블록의 자리에 `이름: ▒〔ID〕` 한 줄로 실린다. 가림과 ID 는 그대로이고, 값 후보가 남으면 요청을 만들지 않는 확인도 같다.
- **방법 d3a·d3b·i3** (`cli2.ts` 의 `run2`): d3a = d2 + 한 줄 묶기(프롬프트 `pair-places@1` 그대로). d3b = d3a 의 요청 글 + `reconcile/pair-places@2`(「요약 숫자」 갈래와 「기간이 쪽의 제목·각주에 있을 수 있다」를 더한 것). i3 = i + 한 줄 묶기(`group-places@1` 그대로). 답의 확인·셈·채점은 밑의 방법과 같다. `outcome.json` 의 `notes.binding` 에 묶인 블록과 못 묶은 블록이 남는다.
- **값 후보의 종류** (`src/domain/kinds.ts`, `extractDocument(…, { kinds: true })`): 값 후보마다 종류를 하나 붙인다. `value` 검증할 값 / `identifier` 가리키는 번호(규격·조항·각주·쪽 번호, 이름의 일부) / `duration` 기간·시점 / `count` 구성을 센 수와 정해 둔 빈도 / `year_label` 연도 표시 / `scale` 눈금·척도·기준 단위. **값 후보는 버리지 않는다.** 종류는 결과를 가르는 데만 쓰고 요청 조립(가림, ID)은 종류를 읽지 않는다. 규칙은 꼴(앞뒤 글자, 단위, 머리글, 좌표, 같은 간격의 수열)만 본다. 틀릴 수 있다.
- **종류의 정답** (`data/reconcile/exam/kb-2025.kinds.json`, `src/compare/kind-exam.ts`): 두 문서의 서술 값 후보 전부(268)와 표의 글 칸 속·표의 숫자 칸의 씨앗 고정 표본(각 150)에 원문을 보고 종류를 단 것. 경계에서 애매한 자리는 `ambiguousWith` 에 다른 쪽 종류를 적었다. **러너 작성, 전문가 검토 전이고, 규칙을 쓴 사람과 정답을 단 사람이 같다**(규칙의 성적은 이 두 문서 안의 값이다).
- **채점과 재분류** (`src/compare/kind-report.ts`, `cli3.ts`): `kinds` 는 종류별·갈래별 정밀도와 재현율, 틀린 것의 목록을 낸다. `reclass` 는 저장된 답의 짝(묶음 안의 두 자리)을 종류로 다시 가른다: 한 자리라도 「값이 아님」이면 걸러지는 것으로 세고, 한 자리만인 것은 따로 센다. 정답 쌍·서술 쌍·문서 안 묶음의 자리 가운데 「값이 아님」으로 갈린 것(잘못 걸러지는 것)도 낸다. `table3` 는 d2·d3a·d3b 와 i·i3 를 나란히 놓는다.
- **판정 기록**: `data/reconcile/exam/kb-2025.named-judgments.json` (밴드 U 의 판정 기록에 없는 것만. **러너 작성, 전문가 검토 전**).

```
pnpm -F reconcile exec tsx src/compare/cli2.ts run2 --method <d3a|d3b|i3> [--doc b] --a .. --b .. --exam-a .. --exam-b .. --pairs .. --plants .. --gold2 .. --plants2 .. (--dry-run | --provider openai --route light-openai --limit-usd <상한> --log <공용 호출 기록> | --replay <실제 실행의 출력> --route light-openai --log <호출 기록>) --out <디렉터리>
pnpm -F reconcile exec tsx src/compare/cli3.ts draw-kinds --seed <n> --per-bucket <n> <공통> --out <디렉터리>
pnpm -F reconcile exec tsx src/compare/cli3.ts kinds --kind-gold <종류의 정답> <공통> --out <디렉터리>
pnpm -F reconcile exec tsx src/compare/cli3.ts reclass --d2 <이름=실행,..> [--h2 ..] --i <이름=실행,..> --judgments <판정 기록> <공통> --out <디렉터리>
pnpm -F reconcile exec tsx src/compare/cli3.ts table3 --d2 .. --d3a .. --d3b .. --ib <i 의 실행(문서 b)> --i3 .. [--judgments <판정 기록,..>] <공통> --out <디렉터리>
```

## 숫자 표기 규칙 (`domain/notation.ts`)

표 셀은 셀 전체를 한 덩어리로 읽는다. 글자는 NFKC 로 맞춘 뒤 본다 (전각 숫자 → 반각).

| 꼴 | 예 | 갈래 | 읽은 값 |
|---|---|---|---|
| 쉼표 있는 수, 소수 | `1,234` `12.50` | 값 후보 | `1234`, `12.50` (표시 자릿수 2) |
| 음수: 부호, 괄호, 세모 | `-12` `(123)` `△12` | 값 후보 | `-12`, `-123`(괄호를 음수로 읽었다는 표시), `-12` |
| 증감 세모 | `▲ 3` `▼ 1` | 값 후보 | `3` (부호를 정할 수 없다는 표시 `sign_ambiguous`) |
| 퍼센트, 배, 단위 접미 | `31.0%` `1.2배` `59,498명` `24만 톤` | 값 후보 | `31.0`(단위 `%`), `1.2`(`배`), `59498`(`명`), `24`(`만 톤`. 수 단위 낱말은 곱하지 않고 단위로 넘긴다) |
| 연도 꼴 네 자리 | `2030` (본문 칸) | 값 후보 | `2030` + 표시 `year_like` (머리글 칸이면 머리글) |
| 값 없음 표시 | `-` `N/A` `해당 없음` | 값 후보 아님 | 0 이 아니다 |
| 연도 | `2025년` `FY2024` | 값 후보 아님 | |
| 번호 | `04` `6(1)` `1.2.3` | 값 후보 아님 | 앞자리 0, 조항 번호, 버전 |
| 날짜 | `2025.7.1` | 값 후보 아님 | |
| 범위 | `10~20` `65-66` `3~17%` | 값 후보 아님 | v1 은 범위를 견주지 않는다 |
| 숫자 여럿, 글 속 숫자 | `116,818 15,649` `기준연도 대비 42% 감축` | 셀 안의 숫자를 하나씩 | 글 속 숫자 규칙으로 읽어 「표 글 셀 속」 갈래로 센다 |

한 격자에 표가 위아래로 붙어 있으면(문서 파서가 한 표로 낸 경우) 아래 표의 머리글이 본문 행으로 들어온다. 첫 값 행 아래에서 값 칸(머리글 열이 아닌 칸)에 값 후보 숫자가 없고 글자 칸이 둘 이상인 행을 「본문 중간의 머리글 행」으로 보고(추정), 그 아래 숫자의 열 머리로 쓴다.

머리글 행·열의 숫자(연도 머리글 포함)와, 열 머리글이 `페이지`·`쪽`·`문단번호` 인 열의 숫자는 값 후보가 아니다. 숫자뿐인 블록이 쪽의 위·아래 8% 안에 있고 쉼표 없는 네 자리 이하 정수면 쪽 번호로 본다.

글 속 숫자(`scanNumbers`)는 덩어리마다 갈래를 붙인다: 값 후보 / 연도(`2025년`, `2050`, `’25`) / 날짜 조각(`3월`, `15일`, `2025.7.1`) / 식별자(`Scope 1`, `GRI 305-1`, `CO2`, 전화번호, URL) / 순번(`제3조`, `2차`) / 표시(`1)` 같은 각주·목록 번호) / 범위의 끝. 이 가름은 규칙이고 정답과 대조한 적이 없다.

값은 십진 문자열로만 다루고 연산은 `kernel` 의 십진 연산이다. JS number 를 거치지 않는다.

## 엑셀 (`adapters/xlsx.ts`, `domain/numfmt.ts`)

- xlsx 안의 XML 을 직접 읽어 저장 값의 글자(`<v>`)를 그대로 가져온다 (`fflate` 로 압축을 풀고 `fast-xml-parser` 로 읽는다). 스프레드시트 라이브러리는 저장 값을 JS number 로 돌려주므로 쓰지 않았다.
- 숫자 셀마다 **저장 값**(`stored`)과 **화면 표기**(`display`, 서식을 적용한 글자)를 둘 다 남긴다. 견줄 때는 저장 값을 쓴다.
- 화면 표기는 서식의 일부만 만든다: General(어림), 자릿수, 천 단위 쉼표, 퍼센트, 끝 쉼표, 음수 구획, 글자 접두·접미. 분수·지수·글자 서식은 `unsupported_format` 으로 남기고 지어내지 않는다.
- 퍼센트 서식 셀은 저장 값(0.123)을 % 단위의 값(12.3)으로 읽는다. 날짜 서식 셀은 값 후보가 아니다. 수식 셀은 저장된 결과를 쓰고, 결과가 없으면 「읽지 못한 숫자」로 센다.
- 시트 안의 빈 행을 표의 경계로 본다. 글자 셀 하나뿐인 덩어리는 다음 표의 제목 후보다. 머리글 행·열은 문서 파서와 같은 규칙으로 추정한다.
- 다루지 않는 것: 병합 범위, 숨긴 행·열, 1904 날짜 체계, xls·xlsb. **엑셀이 직접 저장한 파일로는 시험하지 않았다** (테스트 파일은 XML 을 직접 지은 synthetic 이다).

## 짝짓기와 비교 (`domain/match.ts`)

- 짝의 열쇠는 (지표, 기간, 범위) 다. 값은 열쇠가 아니다.
- 같은 열쇠가 한쪽에 여럿이면 값을 보고 고르지 않고 전부 미확인(`ambiguous_pair`)이다.
- 지표와 기간이 같고 범위만 다른 것이 양쪽에 하나씩이면 「설명 필요」(`boundary_differs`)다.
- 단위 환산은 `data/reconcile/units.json` 의 10 의 거듭제곱뿐이다 (수 단위 낱말, SI 접두어). 한쪽에만 단위가 있으면 「설명 필요」다.
- 반올림 허용폭: 두 값을 기준 단위로 옮긴 뒤, **표시가 거친 쪽의 끝자리**로 다른 쪽을 반올림해 같으면 일치다. 올림(half up)과 짝수 반올림(half even) 어느 쪽이든 받아들이고 적용한 규칙을 결과에 남긴다. 버림으로만 맞으면 「설명 필요」(`matches_if_truncated`)다. 근거: 보고서가 원천보다 적은 자릿수로 적는 것은 오류가 아니고, 어느 반올림을 썼는지는 문서에서 알 수 없다.

결과는 다섯 가지다: `match` 일치 / `mismatch` 불일치 / `unpaired` 짝 없음 / `unverified` 미확인 / `needs_explanation` 설명 필요. 요약(`domain/summary.ts`)은 문서마다 「숫자 N개 중 M개 대조, 짝 없음, 미확인 K개」를 내고, 합이 N 과 다르면 요약 대신 예외를 낸다. 범위 밖(문장 속 숫자, 신뢰도 낮은 표)의 숫자도 N 에 들어 미확인으로 센다. 추출 실패 표와 텍스트 없는 쪽은 숫자 수를 모르므로 따로 적는다.

## 갈아 끼우는 자리

| 자리 | 파일 | 지금 |
|---|---|---|
| 지표 목록 (짝짓기의 기준표) | `data/reconcile/indicators.synthetic.json`, `--catalog` | **synthetic.** 마크스폰·EDK 의 목록이 아니다. 출처도 synthetic 표시도 없는 목록은 `loadCatalog` 가 거부한다 |
| 단위 표 | `data/reconcile/units.json` | 환산이 있는 줄마다 출처. `checkedOn` 이 null 인 줄은 URL 을 열어 확인하지 못한 정의상 값 |
| 확정 기억 | `--memory <파일>` (`data/reconcile/memory.synthetic.json` 은 예시) | (문서 종류, 행 머리글 경로, 열 머리글 경로) → 지표. 맞는 셀은 AI 를 부르지 않는다 |
| 설정 | `data/reconcile/settings.json` | 호출당 숫자 수, 답 하나의 출력 토큰 어림, 받아들일 확신 등급. 전부 가정 |
| 모델 호출 | `domain/pipeline.ts` 의 `LlmGateway` | `llm` 의 `LlmClient`. 모의 공급사와 실제 공급사(`llm` 의 `liveProviders`) |
| 이어 하기 | `domain/pipeline.ts` 의 `UnitResultStore` | `adapters/unitStore.ts` 의 JSONL 파일 |

## 테스트

```
cd ts && pnpm -F reconcile test && pnpm -F reconcile typecheck
```

테스트의 문서와 숫자는 전부 지어낸 것이다. `test/planted.test.ts` 가 심은 오류 시험이다. 실제 공급사 배선(`test/live.test.ts`)은 SDK 클라이언트 자리에 가짜를 넣어 보고, 네트워크를 부르는 테스트는 없다. 테스트는 환경 변수의 키를 읽지 않는다.

## 도표의 값에 계열·범주 붙이기 (밴드 W)

도표의 값(막대·꺾은선 옆의 숫자)은 문서 모델에서 이름 없이 한 칸에 섞여 실린다. `py/docparse charts` 가 낸 도표 라벨을 값 후보의 ID 에 걸어 요청 글에 싣는 옵션이다. 기본은 끔이고, 끄면 요청 글이 밴드 U 와 글자까지 같다.

```
pnpm -F reconcile exec tsx src/compare/cli4.ts chart-facts --a .. --b .. --labels-a <A 의 chart-labels.json> --labels-b <..> --out <dir>
pnpm -F reconcile exec tsx src/compare/cli4.ts chart-score --chart-gold ../data/reconcile/exam/kb-2025.charts.json --labels-a .. --labels-b .. --facts <dir>/fact-charts.json --out <dir>
pnpm -F reconcile exec tsx src/compare/cli2.ts run2 --method <d4c|d4v|i4> --chart-labels <문서별 ID → 라벨 글> ...
```

- `src/compare/charts.ts`: 도표 낱말을 값 후보에 걸기(`linkFactsToCharts`: 그릇과 글자 자리로), 번호표(`assignTags`: 쪽마다 값 후보의 차례로 1 부터, 값과 무관), 라벨 글(`labelText`: `도표 제목 · 계열 · 범주`, 범주가 없으면 싣지 않음), 채점(`matchCharts`, `scoreLabels`).
- `renderDocumentWithIds` 의 `chartLabels` 옵션: 라벨이 있는 값 후보를 `[라벨: ▒〔ID〕]`(표의 숫자 칸은 `[라벨: ▒]`)로 싣는다. ID 와 가림은 그대로이고 라벨 글도 같은 가림을 거친다(`단기(1년)` → `단기(▒년)`). 연도·식별자 꼴(2025, Scope 1)은 남는다.
- 방법 `d4c`·`d4v`·`i4`: `d2`·`i` 와 프롬프트가 같고 요청 글에 라벨만 더 실린다. 라벨 파일은 코드 라벨(`chart-facts` 의 `code-labels.json`)이나 그림 라벨이다.
- 정답 `data/reconcile/exam/kb-2025.charts.json`: 도표 38개, 라벨 600자리(값 383). 러너 작성, 전문가 검토 전. `series`·`category` 는 받아들이는 답의 목록이다.
- 채점의 기준: 도표는 같은 쪽에서 겹친 넓이가 작은 상자의 절반 이상이면 같은 것. 라벨은 값 후보 ID 단위로 붙임·맞음·못 붙임을 따로 센다.

## 도표의 값에 계열·범주 붙이기 (밴드 W, MARC-TICK-26)

도표의 값(막대·꺾은선 옆의 숫자)은 문서 모델에서 표로 잘못 실리고 요청 글에서는 이름 없는 `▒` 로만 보인다. 값마다 「어느 계열의, 어느 범주의 값인가」를 붙여 `[도표 제목 · 계열 · 범주: ▒〔ID〕]` 꼴로 싣는 옵션이다. **기본은 끔**이고, 끄면 요청 글이 밴드 U 와 글자까지 같다. 값 후보의 목록·ID·값·비교 규칙은 그대로다. 라벨은 글일 뿐이다.

라벨을 만드는 길 셋 (전부 원본 PDF 와 문서 모델에서만 만든다. 정답·시험지 사본을 읽지 않는다):

| 길 | 어디서 | 무엇을 보고 | 호출 |
|---|---|---|---|
| (가) 좌표 | `py/docparse/charts.py` | 값 글자의 가로(가로 막대는 세로) 가운데를 범주 축 글자에 맞춘다. 계열은 모른다 | 0 |
| (나) 그림 요소 | 같은 파일 | 값 글자를 가까운 막대 끝·꺾은선 점에 걸고, 그 요소의 자리로 범주를, 색과 범례로 계열을 | 0 |
| 코드 라벨 | 같은 파일 | 범주는 (가)(나)가 같거나 한쪽만 붙였을 때만, 계열은 (나)의 것. 둘이 다르면 싣지 않는다 | 0 |
| (다) 그림 라벨 | `src/compare/vision.ts`, 프롬프트 `reconcile/label-chart-tags@1` | 값·눈금 자리를 번호표로 덮은 쪽 그림(`py/docparse/tagpages.py`)을 모델에게 주고 번호마다 갈래·제목·계열·범주·단위를 글로 받는다. 답 스키마에 값 필드가 없다 | 쪽마다 1 |

명령 (`out/band-W/` 의 `labels.sh`, `c4.sh`, `live.sh`, `table.sh`, `section4.sh` 가 묶어 부른다):

```
cd py && uv run python -m docparse charts <pdf> --document <document.json> --out <디렉터리>          # chart-labels.json (낱말 단위)
cd ts && pnpm -F reconcile exec tsx src/compare/cli4.ts chart-facts …                                 # 값 후보에 걸기, 번호표 tags-*.json, code-labels.json
cd ts && pnpm -F reconcile exec tsx src/compare/cli4.ts chart-score --chart-gold …                    # 도표 찾기와 코드 라벨의 채점
cd py && uv run python -m docparse tagpages <pdf> --tags <tags-*.json> --out <디렉터리> --dpi 200     # 번호표를 덮은 쪽 그림
cd ts && pnpm -F reconcile exec tsx src/compare/cli2.ts run2 --method lv --tags-a … --tag-images-a …  # 그림 라벨 (tag-answers.json, vision-labels.json)
cd ts && pnpm -F reconcile exec tsx src/compare/cli4.ts vision-merge --runs …  /  vision-score …      # 세 실행 합치기(과반), 채점
cd ts && pnpm -F reconcile exec tsx src/compare/cli2.ts run2 --method <d4c|d4v|i4> --chart-labels <문서별 ID → 라벨 글> …
```

정한 것:
- **애매하면 붙이지 않는다.** 범주 사이에 놓인 값, 두 색의 요소에서 똑같이 가까운 값, 범례에 그 색이 없거나 한 색에 이름이 둘인 값은 라벨 없이 싣고 까닭을 남긴다.
- **도표 찾기**: 밑선이 같고 높이가 다른 색 사각형의 묶음, 굵은 꺾은선. 글이 든 사각형과, **위·아래 변이 둘 다 가는 가로 괘선에 맞닿은 사각형(괘선 표의 칸 배경)은 막대가 아니다**(검증 지적 뒤 한 번 고침. `--no-grid-rule` 로 끄면 고치기 전의 성적이 다시 나온다).
- **번호표**는 값과 눈금만 덮는다(범주 축 글자는 보이게 둔다). 번호는 쪽마다 값 후보의 순서로 1 부터이고 값과 무관하다. 자리가 같은 값 후보는 번호 하나를 같이 쓴다.
- **그림 라벨의 답 확인**: 요청에 없는 번호, 같은 번호의 둘째 답, 답의 글에 그 번호표가 덮은 숫자(또는 그 쪽의 다른 번호표가 덮은 세 자리 이상 숫자)가 든 답은 버린다. 요청 글에 실릴 때 라벨 글은 문서 글과 같은 가림을 거친다.
- **세 실행을 합치기**(D4v 에 싣는 것): 값 후보마다 과반이 같은 (갈래, 계열, 범주)를 냈을 때만. 아니면 싣지 않는다.
- 채점의 분모는 정답의 자리 전부다(번호표가 없던 자리, 값으로 보지 못한 자리는 「못 붙임」).

실측(KB금융 2025 두 문서, `gpt-6-luna`, 세 번씩)의 요지. 수치와 단서는 `band-reports/W-reconcile-charts.md`:
- 라벨은 붙는다: 코드 라벨은 범주 378/378 맞음(못 붙임 5/383), 계열 24/24(못 붙임 116/140). 그림 라벨은 실행별 범주 371/379 · 379/381 · 376/381, 계열 91/93 · 117/117 · 89/89(못 붙임 47 · 23 · 51 /140).
- **붙여도 짝은 나오지 않았다**: 라벨이 붙은 도표의 값은 D4c·D4v·I4 아홉 실행의 답(버려진 답 포함)에 한 번도 나오지 않았다. 도표의 서술 쌍 N16~N21 은 여섯 실행 모두 「짝 없음」, 도표에 심은 다섯 오류는 전부 조용히 지나갔다.
- 규칙과 프롬프트는 이 두 문서의 도표를 본 사람이 만들었다. 다른 문서에서의 근거가 아니다.

## 낱개 숫자를 자리별로 묻기: 방법 Q (밴드 X, MARC-TICK-27)

지금까지의 방법(D·D2·D3·D4)은 「두 문서를 통째로 주고 같은 항목인 자리를 빠짐없이 적어라」였고, 낱개로 떠 있는 숫자(요약 쪽 큰 숫자, 도표의 값, 문장 속 숫자)는 답에 거의 나오지 않았다. 방법 Q 는 거꾸로 묻는다: **물을 자리(대상)의 목록을 코드가 쥐고, 대상마다 「상대 문서에 같은 항목의 자리가 있는가, 없으면 없다고」를 받는다.** 값은 여전히 가려져 있고 답에 값 필드가 없다. 답한 짝의 값은 코드(`compareValues`)가 견준다.

| 파일 | 하는 일 |
|---|---|
| `src/compare/ask.ts` | 대상 목록(`listTargets`), 요청으로 묶기(`planAsks`), 요청 글(`buildAskRequest`), 답 확인과 비교(`judgeAsk`), 셈(`tallyAsk`), 흔들림(`stability`). 정답·명세를 읽지 않는다 |
| `src/compare/cli5.ts` | 명령 `targets`(대상 목록, 호출 0), `runq`(dry-run / 모의 / 실제 / 저장된 답 다시 채점). 정답·명세 인자를 받지 않는다 |
| `src/compare/ask-report.ts`, `cli6.ts` | 채점 쪽: 대상 목록과 정답의 겹침(`coverq`), 저장된 실행의 표(`tableq`), 판정 기록의 형식 |
| `prompts/reconcile/ask-places/1.md` | 프롬프트. 변수는 상대 문서, 대상이 든 쪽, 대상 목록 차례다 |
| `data/reconcile/exam/kb-2025.ask-judgments.json` | 정답 밖의 짝과 요약 숫자 대상의 원문 판정 |

정한 것:
- **대상은 세 갈래**이고 한 값 후보는 한 갈래에만 든다: 요약 숫자(이름 블록이 묶인 숫자뿐인 블록, `pickNameBlocks`), 도표의 값(코드 라벨이 실리는 값 후보, `py/docparse charts`), 문장 속 숫자(문장 속 값 후보 가운데 종류 옵션이 「값」으로 본 것). 표의 숫자 칸끼리의 짝은 이 방법의 대상이 아니다.
- **요청 하나** = 상대 문서 전체(앞) + 대상이 든 쪽 전체 + 대상 목록(`- 대상 ID | 갈래 | 이름이나 라벨`). 문서 글은 D2 와 같은 가림과 ID 표시에 묶은 이름과 코드 라벨을 얹은 꼴이다. 한 요청에는 한 갈래·한 문서의 대상만, 최대 80개. 쪽을 쪼개지 않고, 한 쪽의 대상이 80개를 넘으면 그 쪽만 고르게 나눈다.
- **요청을 만들지 않는 확인**: 요청 글(상대 문서, 대상이 든 쪽, 대상 목록)에 값 후보 숫자가 남을 때, 대상의 자리가 문맥 글에 없을 때, 원본으로 조립한 글과 시험지로 조립한 글이 다를 때, 입력 어림이 250,000 토큰을 넘을 때.
- **답**: 대상마다 `counterparts`(상대 자리의 ID 와 단위, 여럿이면 전부) 또는 빈 배열과 `reason`(`not_in_other_document` / `undecidable`). 버리는 것: 대상 목록에 없는 자리에 대한 답(`not_a_target`), 문서에 없는 ID, 대상과 같은 문서의 자리, 원문에 없는 단위 글자, 상대도 사유도 없는 답, 한 대상에 엇갈린 답. 받아들인 상대가 하나도 남지 않으면 그 대상은 「버려진 답」이다.
- **셈**: 모든 대상이 결과 하나에 든다(상대를 답함 / 없음(사유별) / 답 없음 / 버려진 답). 합이 대상 수와 다르면 수를 내지 않는다.

실측(KB금융 2025 두 문서, `gpt-6-luna`, 세 번)의 요지. 수치와 단서는 `band-reports/X-reconcile-ask.md`:
- 대상 480개(요약 숫자 10, 도표의 값 378, 문장 속 92), 실행당 요청 10개.
- **문장 속 숫자와 요약 숫자는 물으면 나왔다.** 밴드 U·V 에서 한 번도 짝지어지지 않던 CET1 비율(요약 숫자 ↔ 보고서의 문장·표의 글 칸)이 세 번 다 나왔고, 문장 속에 심은 오류 셋은 세 번 다 잡혔다(밴드 U 의 D2 는 1 · 2 · 2). **도표의 값(62쪽)은 물어도 나오지 않았다**: 여섯 쌍이 세 번 다 「없음」이고 도표에 심은 다섯은 전부 조용히 지나갔다.
- 「없음」이 대부분이다(상대를 답한 대상 15 · 17 · 18 /480). 버려진 답과 답 없음은 0.
- **비용은 한 번에 약 0.16 USD 로 D2(약 0.05)의 세 배였다.** 앞부분(상대 문서)이 같은 서로 다른 요청 사이에서 캐시 읽기가 듣지 않았다. 같은 요청을 되풀이한 둘째·셋째 실행은 약 0.025 USD 였다.
- 대상 규칙과 프롬프트는 밴드 U·V·W 의 결과를 본 뒤에 만든 것이다. 다른 문서에서의 근거가 아니다.

## 열 밀림 검사와 빠진 표 되찾기: 방법 R (밴드 Y, MARC-TICK-28)

D 계열(가린 두 문서 통째 → 자리의 짝)은 같은 요청을 되풀이해도 답이 흔들린다: 표 하나를 통째로 안 적거나, 열 번호를 밀려 적는다(없는 칸이면 버려지고 있는 칸이면 다른 연도끼리 견준다). 방법 R 은 **저장된 한 실행의 답 뒤에** 코드의 검사와 좁은 다시 묻기를 붙인다. D 를 다시 부르지 않는다. 코드는 `src/compare/recover.ts`(검사, 지목, 요청 계획, 합치기, 셈. 정답을 읽지 않는다), `cli7.ts`(명령 `check`·`runr`. 정답·명세 인자를 받지 않는다), `recover-report.ts`·`cli8.ts`(채점과 표).

```
cd ts
pnpm -F reconcile exec tsx src/compare/cli7.ts check --a .. --b .. --exam-a .. --exam-b .. --run <저장된 D 계열 실행의 출력> --out <디렉터리>      # 호출 0
pnpm -F reconcile exec tsx src/compare/cli7.ts runr  (같은 인자) --dry-run | --provider openai --route light-openai --limit-usd <상한> --log <호출 기록> | --replay <실행의 출력>
pnpm -F reconcile exec tsx src/compare/cli8.ts tabler --exam-a .. --exam-b .. --pairs .. --plants .. --plants2 .. --judgments .. --runs <이름=저장된 실행=방법 R 의 출력,..> --out <디렉터리>
```

- **열 밀림 검사** (`checkShift`, 호출 0): 표의 숫자 칸끼리의 짝마다 두 칸의 열 머리글·행 머리글을 견준다(머리글의 열쇠는 `lineKey`: 단위 글자와 각주 표시를 빼고 정규화). 열쇠가 같으면 「같음」. 다르면, 한쪽의 열쇠가 **상대 표의 다른 열(행)** 에 그대로 있는지 본다. 기준 `both`(방법 R 이 쓰는 것)는 양쪽 다 그럴 때만, 기준 `either` 는 한쪽만 그래도 「밀림」이다. 열이나 행이 밀렸으면 그 짝은 「의심」, 둘 다 같으면 「정상」, 그 밖(머리글이 서지 않았거나 두 표가 머리글을 달리 적음)은 「판단 못 함」이다. 서술이 낀 짝은 대상이 아니다.
- **지목** (`nominate`, 호출 0): 다시 물을 표 짝을 코드가 고른다. (i) 버려진 답(없는 칸의 ID)이 가리키는 표 짝 (ii) 의심이 나온 표 짝 (iii) 짝지어진 표 짝 가운데 「행 머리글과 열 머리글이 같은, 견주어지지 않은 칸의 쌍」이 하나라도 있는 것(`expectedByHeader`) (iv) 어느 짝에도 들지 않은 표: 행 머리글 3개 이상과 열 머리글 1개 이상이 같은 표 짝만 후보로 추려, 그 표들만 실은 표 목록으로 묻는다(`review-tables@1`). 받은 새 표 짝은 최대 4개.
- **다시 묻기**: 지목된 표 짝마다 요청 하나. 의심을 뺀 뒤 그 두 표 사이에 남은 짝이 있으면 칸 검수(`review-cells@2`: 남은 칸마다 답 하나, 상대는 남은 칸에서만), 남은 짝이 없으면 칸 짝 요청(`pair-cells-in-tables@1`: 두 표의 칸 짝 전부). 어느 한쪽 표에 견주어지지 않은 칸이 없으면 묻지 않는다. 프롬프트는 셋 다 앞 밴드의 것 그대로다. 요청 글에 값 후보 숫자가 남거나 원본으로 조립한 글과 시험지로 조립한 글이 다르면 요청을 만들지 않는다.
- **합치기** (`mergeRecovered`): 원래 짝과 다시 받은 짝의 합집합(같은 짝은 한 번). **의심으로 걸린 짝은 원래 것이든 다시 받은 것이든 견준 결과에 넣지 않고 따로 센다.** 한 칸이 서로 다른 상대와 짝지어지면 둘 다 남기고 「엇갈림」으로 센다.
- **셈**: 원래 답 / 검사만 / 다시 물은 뒤의 세 단계마다 모든 값 후보가 결과 하나에 든다(`classifyRecovered`. 합이 값 후보 수와 다르면 예외). 다시 물은 뒤에도 머리글로 보면 상대가 있는 남은 칸을 표 짝마다 센다(`stillExpected`).

실측(KB금융 2025 두 문서, `gpt-6-luna`, 저장된 15실행에 한 번씩)의 요지. 수치와 단서는 `band-reports/Y-reconcile-recover.md`:
- 열 밀림 검사는 15실행의 표 칸 짝 1,623개 중 32개(`both`)·45개(`either`)를 가렸고, 가린 것 가운데 정답 쌍이나 「같은 항목」은 0, 전부 원문 판정이 「같은 항목 아님」이다. 판단 못 함이 1,022개(63%)로 가장 많다.
- 빠진 정답 쌍이 든 표 짝은 전부 지목됐다(12개 중 12). 되찾은 것은 요청의 종류에 따라 갈렸다: 칸 검수(코드가 남은 칸의 목록을 쥔다)는 3요청에서 빠진 정답 쌍 37개 중 37개, 표 검수 뒤의 칸 짝 요청(47쪽 ↔ 21쪽)은 3요청에서 96개 중 96개, **열이 밀렸던 표 짝의 칸 짝 요청(46쪽 ↔ 21쪽, 글자까지 같은 요청 6번)은 72개 중 14개**(1번은 전부, 2번은 한 쌍, 3번은 빈 답).
- 좋은 6실행에서 나빠진 것은 없다(사라진 정답 쌍 0, 틀린 새 짝 0). 비용은 15실행 합 0.0221 USD.
- 규칙은 이 15실행의 실패 꼴을 본 사람이 만든 것이다. 다른 문서·다른 실패 꼴에서의 근거가 아니다.

## 호출 없는 규칙을 다른 문서에서 재기 (밴드 G1, MARC-TICK-34)

값 후보의 종류 규칙(`domain/kinds.ts`)과 도표 찾기(`py/docparse/charts.py`)는 KB금융 두 문서를 보고 만들었다. 이 절의 도구는 그 규칙을 **고치지 않고** 다른 문서 하나에서 잰다. 모델을 부르지 않는다.

밴드 V·W 의 명령(`cli3.ts` 의 `draw-kinds`·`kinds`, `cli4.ts` 의 `chart-score`)은 KB 두 문서의 시험지·정답 쌍·라벨 정답을 인자로 요구해 다른 문서에 쓸 수 없다. 그래서 문서 하나를 받는 명령을 따로 뒀다(`src/compare/cli9.ts`, 셈은 `generalize.ts`). 있는 명령의 동작은 그대로다.

```
pnpm -s -F reconcile exec tsx src/compare/cli9.ts stats --doc <document.json> --out <디렉터리>
pnpm -s -F reconcile exec tsx src/compare/cli9.ts draw --doc <document.json> --seed <n> --quota <문장 속,숫자뿐인 블록,표의 글 칸 속,표의 숫자 칸> --out <디렉터리>
pnpm -s -F reconcile exec tsx src/compare/cli9.ts kinds --doc <document.json> --kind-gold <종류의 정답> --out <디렉터리>
pnpm -s -F reconcile exec tsx src/compare/cli9.ts chart-match --doc <document.json> --chart-gold <도표의 정답> --labels <chart-labels.json> --out <디렉터리>
```

- `stats`: 쪽·표·값 후보의 수와 머리글 통계. 「머리글이 선 칸」은 열 밀림 검사(`recover.ts` 의 `lineKey`)가 읽는 열쇠(단위 글자와 빈 조각을 뺀 머리글 경로)가 열·행 둘 다 비어 있지 않은 숫자 칸이다. 검사가 판단하려면 필요한 조건이고 충분한 조건은 아니다(두 표가 머리글을 달리 적으면 여전히 판단하지 못한다).
- `draw`: 갈래마다 정한 수를 씨앗 고정 난수로 뽑고(모자라면 전부) 자리마다 앞뒤 글과 표의 머리글을 싣는다. **종류 옵션을 켜지 않는다**: 표본 파일에 규칙의 출력이 없다. 밴드 V 의 `drawKindTargets` 는 서술 값 후보를 전부 싣는데, 긴 보고서에서는 문장 속만 수백 자리여서 갈래마다 수를 정했다.
- `kinds`: 규칙을 돌려 정답과 견준다. 종류별 셈은 밴드 V 의 `scoreKinds` 그대로이고, 여기에 헷갈린 짝의 표, **정답이 값인데 규칙이 거른 수**(대조 대상을 잃는 쪽)와 **정답이 값이 아닌데 규칙이 값으로 남긴 수**(거짓 경보의 재료)를 갈래별로 더한다. 정답이 「모르겠다」(`kind: null`)인 자리는 분모에서 빼고 따로 센다.
- `chart-match`: 도표 찾기의 대조. 같은 쪽에서 겹친 넓이가 작은 쪽 상자의 절반 이상이면 같은 도표다(밴드 W 의 `matchCharts`). 정답의 상자는 쪽 그림에서 읽은 어림이라, 못 찾은 것과 정답에 없는 것은 쪽 그림으로 다시 봐야 한다.

정답 (`data/reconcile/exam/`. **서브에이전트 작성, 전문가 검토 전**. 공개 보고서가 출처다):

- `kinds-guideline.md`: 종류 여섯의 뜻과 가르는 기준. 밴드 V 의 보고에 적힌 기준을 문서를 가리지 않는 꼴로 옮겼다. 규칙의 코드를 보고 쓴 것이 아니다.
- `samsung-2026.kinds.json`(285자리), `cosmax-2025.kinds.json`(299자리): 씨앗 `20261005`, 갈래별 100 · 50 · 50 · 100. 규칙의 코드와 출력을 보지 않은 서브에이전트가 지침, 앞뒤 글, 쪽 그림만 보고 달았다.
- `samsung-2026.charts.json`(36개), `cosmax-2025.charts.json`(14개): 전 쪽의 쪽 그림을 보고 적은 도표 목록(쪽, 갈래, 값 숫자가 그림에 있는가, 상자의 어림).
- 정답은 규칙을 돌리기 전에 해시로 확정했고 그 뒤 고치지 않는다. 채점 뒤 정답이 틀려 보이는 자리는 「정답 이의」로 따로 적는다(`band-reports/G1-generalize.md`).

성적과 틀린 자리의 분류는 `band-reports/G1-generalize.md`. 이 두 문서의 표본에서의 값이고, 규칙은 이 밴드에서 고치지 않았다.

## 자리 ID → 쪽과 상자 (`domain/places.ts`, 밴드 C1, MARC-TICK-35)

값 후보의 ID(`p3-t1:r2c4`, `p3-t1:r2c4#n1`, `p3-b2#n3`)에서 PDF 위의 상자를 얻는다. PDF 를 열지 않는다. 문서 분해가 `parse --places` 로 곁에 낸 글자 자리(`char-places.json`, `schemas/char-places.schema.json`, `py/docparse/README.md` 의 「글자 자리」)만 읽는다.

```ts
const extraction = extractDocument(document, "a");
const place = locatePlace("p3-b2#n3", extraction, charPlaces);
// { found: true, page: 3, boxes: [[x0, y0, x1, y1]], containerBox, covered, length }  또는  { found: false, reason }
```

- 열쇠는 글자 범위다. 숫자 뽑기(`facts.ts`)가 값 후보마다 남기는 `where.span.start`·`end`(그릇의 글에서의 UTF-16 자리)를 그대로 쓰고, 글자 자리의 그 범위 글자 상자를 줄마다 묶는다. 이 파일에는 숫자를 알아보는 규칙이 없고 `facts.ts`·`notation.ts` 는 고치지 않았다. 자리 ID 의 꼴도 그대로다.
- 표의 숫자 칸(`where.span` 이 없다)은 칸의 글 전체가 상자다. 칸이 `59,498명` 이면 `명` 까지 덮는다(값 후보의 원문 글자 `raw` 가 칸 전체이기 때문이다). 글 속 숫자는 숫자 덩어리(`raw`: 부호와 `%` 포함)만 덮는다.
- `boxes` 는 줄마다 하나다. 범위가 두 줄에 걸치면 둘이다. 같은 줄인지는 이웃한 두 토막의 세로 범위가 겹친 높이가 낮은 쪽 높이의 절반을 넘는지로 본다(`sameLine`). `containerBox` 는 그 칸·블록의 글자 전체의 상자다.
- 못 찾는 까닭(`reason`): `unknown_id`(그런 값 후보가 없다), `no_places`(글자 자리 없이 분해한 문서), `not_in_document`(엑셀의 자리), `container_unlinked`·`container_missing`(분해가 그 그릇을 잇지 못했거나 글자 자리에 없다), `no_glyphs_in_span`.
- `placeOfWhere(where, index)` 는 값이 아닌 숫자(`nonValues`)의 자리에도 쓴다. 여러 자리를 볼 때는 `indexPlaces(places)` 를 한 번 만들어 넘긴다. 문서 모델과 글자 자리가 같은 PDF 의 것인지는 `sameSource` 로 본다.
- 측정 명령 `src/compare/places-cli.ts fact-places` 는 값 후보 전부의 상자를 `fact-places.json` 으로 적는다. 그 상자가 숫자를 덮는지는 `py/docparse` 의 `placecheck` 가 PDF 를 다시 읽어 본다.

네 국문 보고서에서 값 후보 8,773개 전부가 상자를 얻었고, 상자 안의 글자를 PDF 에서 다시 읽으면 8,751개가 원문과 같았다(다른 22개: 코스맥스의 두 번 그려진 쪽 번호 20, 삼성전자에서 옆 글자의 가운데가 상자 끝에서 0.15pt 안에 있어 확인 도구의 여유에 걸린 것 2. 여유 없이 보면 이 둘은 같다). 결과와 한계는 `band-reports/C1-places.md`.

## 이름 붙은 도표를 표로 세우기 (`compare/chart-tables.ts`, `cli10.ts`, 밴드 CT1, MARC-TICK-36)

도표의 값은 문서 모델에 이름 없는 숫자 덩어리로 실린다(표로 잘못 선 칸에 여럿이 섞이거나 숫자뿐인 블록). 도표 라벨(값 후보 ID → 도표·계열·범주)이 있으면 **도표마다 표 하나(행 = 계열, 열 = 범주)를 더한 파생 문서**를 만든다. 모델을 부르지 않고 값을 읽지 않는다(값 후보의 원문 글자를 새 칸으로 옮길 뿐이다). 원래 문서는 고치지 않는다.

```
pnpm -F reconcile exec tsx src/compare/cli10.ts build --doc <document.json> --doc-id a --labels <chart-labels.json> [--vision <tag-answers.json> --source <이름>] [--mixed reject|unnamed-row] --out <dir>
pnpm -F reconcile exec tsx src/compare/cli10.ts score --chart-gold <도표 정답> --orig-a .. --orig-b .. --a <build 출력> --b <build 출력> --out <dir>
pnpm -F reconcile exec tsx src/compare/cli10.ts dry --orig-a .. --orig-b .. --a .. --b .. --exam-a <시험지 사본의 build 출력> --exam-b .. --show a:62,a:47 --out <dir>
```

`build` 가 내는 것: 파생 문서 `document.json`(원래와 같은 스키마 `schemas/document.schema.json` 을 통과한다. 읽는 쪽이 그대로 읽는다), 대응표 `chart-table-map.json`, 도표별 결과 `chart-tables.json`·`chart-tables.md`, 쓴 라벨 `input.json`.

- **새 표의 꼴**: 0행 = `[도표 제목, 범주…]`, 그 아래 행 = `[계열 이름, 값…]`. 머리글은 `rows: [0], cols: [0]`. 계열 이름이 없으면 이름 없는 한 행이다. 열의 차례는 범주가 전부 범주 축의 글자(`chart-labels.json` 의 `charts[].categories`)에 있으면 축의 차례, 아니면 값 글자의 자리다.
- **ID**: 새 표는 그 쪽의 표 번호를 이어 `p<쪽>-t<번호>`, 새 블록은 `p<쪽>-b<번호>` 다(읽는 쪽이 아는 ID 꼴이 그것뿐이다). 새 표의 블록은 값이 있던 블록의 바로 뒤에 같은 `order` 로 놓인다. `method` 는 `text`, `status` 는 `ok`, `notes` 에 `chart_table:<도표 ID>` 와 `chart_labels:<라벨의 출처>` 가 남는다. 스키마는 고치지 않았다.
- **원래의 숫자 덩어리**: 옮긴 값은 원래 그릇(칸, 블록)에서 뺀다. 같은 값이 두 번 실리지 않게 하려는 것이다. 눈금과 범주 축의 글자, 라벨이 없는 값은 원래 자리에 그대로 남는다.
- **대응표**: ID 가 바뀐 값 후보만 싣는다(`moved` = 새 칸으로 옮김, `renumbered` = 값을 뺀 그릇에 남아 순번 `#n` 이 당겨짐). 여기에 없는 값 후보는 원래 ID 그대로다. 앞 밴드의 정답 쌍·심은 자리·좌표(원래 ID 로 적혀 있다)는 `toOriginalId`·`toDerivedId` 로 따라간다.
- **세운 뒤의 확인**: 파생 문서에서 값 후보를 다시 뽑아, 원래의 값 후보 하나하나가 대응표의 ID 에 같은 값으로 있는지와 새로 생긴 값 후보가 없는지를 본다. 어긋나면 그 도표를 빼고 다시 세운다(`reads_differently`). 값 후보의 수는 변환 앞뒤로 같다.
- **세우지 않는 것**(애매하면 세우지 않는다): 값으로 걸린 자리가 없는 도표, 범주 라벨이 하나도 없는 도표, 같은 계열·범주에 값이 둘 이상인 도표(계열을 모르는 여러 계열 도표가 여기에 든다), 계열 이름이 있는 값과 없는 값이 섞인 도표. 마지막 것은 `--mixed unnamed-row` 로 이름 없는 한 행에 모아 세울 수 있다(범례가 한 막대에만 있는 도표가 서지만, 계열을 못 붙인 값이 이름 없는 행에 놓인다).
- **겹친 표가 같은 글자를 두 번 실은 자리**(문서 모델의 흠. 상자와 원문이 같은 값 후보가 둘)는 첫째만 옮기고 둘째는 원래 자리에 둔다.
- 라벨의 출처: 코드 라벨(`py/docparse charts`)이거나, 밴드 W 의 저장된 그림 라벨(`tag-answers.json`. AI 가 낸 답이다. 어느 도표의 값인지는 코드가 찾은 도표를 따른다).
- `score` 의 「옳은 칸」: 정답의 값 자리마다 새 표에서 그 값이 놓인 칸의 머리글을 **표에서 다시 읽어** 정답의 계열·범주와 견준다. 틀린 칸은 라벨의 틀림(표의 머리글이 라벨 그대로인데 라벨이 틀림)과 변환의 틀림(라벨은 맞는데 머리글이 다름)으로 가른다.
- `dry` 는 방법 D 의 문서 글 조립(`renderDocumentWithIds`)만 한다. **보내지 않는다.** 가리지 못한 값 후보, 원본 대 시험지 사본의 글 차이, 토큰 어림을 낸다. `cli2.ts run2 --dry-run` 에 파생 문서를 그대로 넣으면 정답 파일의 ID(원래 ID)가 파생 문서에 없어 거부된다. 실제로 재려면 정답과 심은 자리를 대응표로 옮기는 일이 먼저다.
- **이 변환이 보이는 것은 「표로 선다」까지다.** 표로 선 값이 짝지어지는지는 재지 않았다(모델 호출이 있어야 한다). KB금융 2025 두 문서의 수치와 눈 판정은 `band-reports/CT1-chart-tables.md`.

## 도표를 표로 세운 문서로 짝짓기 실측 (`compare/chart-live.ts`, `cli11.ts`, 밴드 CT2, MARC-TICK-38)

위의 파생 문서로 방법 D2(밴드 U)를 실제로 돌려 「도표의 값이 짝으로 나오는가」를 잰 것이다. 새 실행 길은 없다: 밴드 U 의 명령 `cli2.ts run2 --method d2` 를 한 글자도 고치지 않고 쓰고, 정답만 파생 문서의 ID 로 옮겨 준다.

```
pnpm -F reconcile exec tsx src/compare/cli11.ts move  --orig-a .. --orig-b .. --build <cli10 build 의 출력 묶음: a/ b/ exam-a/ exam-b/> --pairs .. --plants .. --gold2 .. --plants2 .. --out <dir>
pnpm -F reconcile exec tsx src/compare/cli11.ts aims  --orig-a .. --orig-b .. --chart-gold .. --gold2 .. --plants2 .. --aims <겨냥 1 의 자리> --sets <이름=build 묶음,..> --out <dir>
pnpm -F reconcile exec tsx src/compare/cli11.ts score --space <derived|original> --a .. --b .. --exam-a .. --exam-b .. --map-a .. --map-b .. --pairs .. --plants .. --gold2 .. --plants2 .. --chart-gold .. --aims .. --runs <이름=실행 출력,..> [--calls ..] [--judgments ..] --out <dir>
```

- `move`: 정답 쌍·심은 자리(원래 문서의 ID)를 대응표로 옮긴 **사본**을 낸다. 정답 파일은 고치지 않는다. 옮긴 ID 가 전부 파생 문서의 값 후보이고 글자가 같은지, 수가 앞뒤로 같은지, 파생 시험지(시험지 사본에 같은 변환을 한 것)가 「파생 문서에 옮긴 명세만 적용한 것」과 같은지 확인하고 어긋나면 실패한다. 옮긴 뒤 양쪽이 다 표의 숫자 칸이 된 서술 쌍은 넓힌 정답의 꼴(한쪽 이상이 서술)에 맞지 않아 `chart-pairs.json` 에 따로 싣는다.
- `aims`: 라벨 벌마다 겨냥의 자리가 표의 칸으로 옮겨졌는지와 옳은 칸인지를 세고, 고르는 규칙(옮겨진 것이 많은 벌 → 틀린 칸이 적은 벌 → `--tie-break` → 넘겨받은 차례)을 적용한다.
- `score`: 저장된 원답(`units.jsonl`)을 밴드 U 의 확인(`judgePlacePairs`)에 다시 넣어 겨냥 셋과 표 칸의 성적을 낸다. **받아들인 짝만 「짝으로 나옴」으로 세고 버려진 답에만 나온 자리는 따로 센다.** 정답은 그 실행이 쓴 문서의 ID 로 옮겨서 쓴다(`--space`). 문서와 정답의 ID 가 안 맞으면 거부한다. 모의 실행의 출력도 거부한다.
- 커밋된 자료(`data/reconcile/exam/`): `kb-2025.ct2.{pairs,plants,gold-v2,plants-v2,chart-pairs}.json`(고른 벌 = 그림 라벨 합친 것 / 이름 없는 행으로 옮긴 사본), `kb-2025.ct2-aims.json`(보고서 47쪽의 여섯 자리와 원문으로 확인한 「옳은 상대」. 정답 파일의 쌍이 아니다), `kb-2025.ct2-judgments.json`(판정 기록).

**결과 (KB금융 2025 보고서·데이터북, gpt-6-luna, 세 실행, 2026-10-05. 자세한 것은 `band-reports/CT2-chart-tables-live.md`)**: 표로 세운 도표의 값 자리 270개 가운데 답에 나온 것은 세 실행 모두 **0개**다(받아들인 짝 0, 버려진 답 0). 47쪽의 6칸 0/6, 도표가 낀 정답 쌍 0/8, 도표에 심은 오류 0/5 × 3. 표 칸의 원래 성적은 참 일치 42·39·39 /42, 잡은 오류 15·15·14 /15 였다(원래 문서의 D2 는 42·15 × 3). D 계열의 답은 실행마다 흔들리므로(밴드 V·W) 세 번으로는 이 차이가 파생 문서 탓인지 가를 수 없다. 한 문서 쌍, 라벨 한 벌, 세 번의 측정이다.

## 도표의 값을 좁혀 묻기: 방법 F (`compare/focus.ts`, `cli12.ts`, 밴드 CH1, MARC-TICK-40)

도표의 값은 「두 문서를 통째로 주고 짝을 적어라」(D 계열)에서도, 「상대 문서를 통째로 싣고 대상마다 물어라」(방법 Q)에서도 거의 나오지 않았다. 방법 F 는 **코드가 「도표가 든 쪽 → 상대 문서의 볼 만한 쪽」을 먼저 지목하고, 요청 하나에 그 쪽들만 싣는다.** 값은 여전히 가리고, 답에 값 필드가 없고, 답한 짝의 값은 코드(`compareCells`)가 견준다.

```
pnpm -F reconcile exec tsx src/compare/cli12.ts nominate --a .. --b .. --exam-a .. --exam-b .. --chart-labels <코드 라벨> --out <dir>
pnpm -F reconcile exec tsx src/compare/cli12.ts runf --a .. --b .. --exam-a .. --exam-b .. --chart-labels .. --nominate <value|names|either> --arm <ask|ask2|pair> [--split-by-name] (--dry-run | --provider mock | --provider openai --route light-openai --limit-usd <상한> --log <호출 기록> | --replay <실행 출력> --route .. --log ..) --out <dir>
pnpm -F reconcile exec tsx src/compare/cli12.ts tablef --a .. --b .. --exam-a .. --exam-b .. --chart-labels .. --gold2 .. --plants2 .. --aims <그 밖의 기대 자리> --runs <이름=실행 출력,..> --out <dir>
```

- **대상**: 방법 Q 의 도표 갈래 그대로(코드 라벨이 실리는 값 후보). `nominate` 와 `runf` 는 정답·명세를 읽지 않는다. 채점은 `tablef` 에만 있다.
- **지목** (`nominate`, 호출 0):
  - `value`: 대상의 값과 글자가 같은 값(`Fact.value`, 백분율 여부까지. 유효 숫자 `MIN_SIGNAL_DIGITS` = 4자리 이상)이 상대 문서의 값 후보에 있으면 그 쪽을 지목한다. **값이 같은 것을 지목의 신호로만 쓴다. 짝을 정하지 않는다.** 요청의 대상은 지목된 쪽의 도표 값 전부라서, 값이 틀린 자리도 같은 쪽의 다른 값이 신호를 내면 물음에 들어간다. 한 쪽의 긴 값이 전부 틀렸으면 지목되지 않는다(이 신호의 한계).
  - `names`: 값을 보지 않는다. 라벨(`제목 · 계열 · 범주`)의 이름과 범주가 상대 문서의 표의 숫자 칸의 행·열 머리글에 각각 있으면 그 쪽을 지목한다.
  - `either`: 둘의 합.
  - 지목은 시험지(받은 문서)에서 낸다. 지목되지 않은 쪽의 대상은 묻지 않고 「지목되지 않음」으로 센다.
- **요청 하나** = 상대 문서의 지목된 쪽 + 도표가 든 쪽 + 그 쪽의 도표 대상 전부. 조립과 확인은 방법 Q 의 것(`buildAskRequest`)을 그대로 탄다: 값 후보가 남으면, 대상이 문맥 글에 없으면, 같은 계획을 원본으로 조립한 글과 다르면 요청을 만들지 않는다. `--split-by-name` 은 한 쪽의 대상을 라벨의 이름이 같은 것끼리 나눠 요청을 여럿 만든다.
- **묻는 꼴**: `ask`(방법 Q 의 프롬프트 `ask-places@1`. 대상마다 상대 자리 또는 「없음」), `ask2`(`ask-places@2`. 상대 자리마다 근거의 세기 `basis`: `clear` / `likely` 를 같이 받는다. `likely` 는 이름·기간·단위는 맞는데 범위·기준을 글로 확인할 수 없는 자리), `pair`(방법 D2 의 프롬프트 `pair-places@1`. 두 글의 짝을 전부. 받아들인 짝에 대상이 나온 것만 「상대를 답함」).
- **셈** (`tallyFocus`): 모든 도표 대상이 결과 하나에 든다(지목되지 않음 / 상대를 답함 / 없음(사유별) / 답 없음 / 버려진 답). 합이 대상 수와 다르면 수를 내지 않는다.
- 커밋된 자료: `prompts/reconcile/ask-places/2.md`, `data/reconcile/exam/kb-2025.focus-judgments.json`(기대한 자리 밖의 짝의 원문 판정. 러너 작성, 전문가 검토 전).

**결과 (KB금융 2025 보고서·데이터북, gpt-6-luna, 2026-10-05. 자세한 것과 단서는 `band-reports/CH1-charts-why.md`)**

- 호출 0 으로 본 것: 도표의 값 383자리 가운데 같은 긴 숫자가 상대 문서에 있는 것은 **11자리(도표 5개)** 뿐이다(짧은 숫자 268자리는 이 셈으로 볼 수 없다). `value` 지목은 그 11자리가 든 네 쪽(보고서 37·39·47·62쪽)만 지목했고, `names` 는 62쪽만 지목했다.
- 그 11자리 가운데 옳은 상대와 짝지어진 것: `ask` 아홉 번 11·11·5·5·5·11·5·9·5, `ask2` 여섯 번 11·11·11·5·9·5, `ask` + `--split-by-name` 여섯 번 8·8·7·8·11·8, `pair` 세 번 5·1·7. 같은 자리들이 D 계열의 저장된 18실행(밴드 CT2 포함)에서는 0 이었다. `pair` 의 둘째 실행은 47쪽 막대 셋을 옳은 칸과 짝지었지만 ID 를 틀린 꼴로 적어 버려졌다.
- 47쪽의 세 값과 CET1 의 두 값은 `ask` 계열 21실행에서 전부 나왔다. **62쪽의 여섯 값은 절반쯤만 나온다**(나누지 않으면 여섯이 한꺼번에 나오거나 안 나오고, 나누면 도표 단위로 갈린다). 가운데 칸(`likely`)을 줘도, 요청을 나눠도 미리 적은 기준에 닿지 않았다.
- 틀린 짝(거짓 경보): `ask` 계열 21실행 합쳐 6건. 다섯은 상대가 있는 11자리 자신에 붙은 틀린 상대다. 넷은 데이터북 78쪽 표(연도가 적힌 칸이 가려지는 자리)에서 났다. 62쪽에 심은 오류 다섯을 잡은 것은 55/105 다. 심은 오류가 「일치」로 나온 것은 0.
- 비용은 한 번에 0.004~0.007 USD(나누면 0.007~0.014).
- **「범위를 좁혀서 나왔다」가 굳은 것은 `pair` 꼴까지다**(통째로 0/18실행 → 좁히면 세 번 다 나옴). 자리별로 묻는 꼴에서 62쪽이 나온 것은 같은 조건 비교가 0/3 실행 대 4/9 실행이라 우연과 가르지 못했고, 62쪽이 나온 실행은 추론 토큰이 많은 실행이기도 했다.
- 한 문서 쌍, 한 모델이다. 11자리는 긴 숫자로 센 아래쪽 끝이다. 지목 규칙·프롬프트 @2·나눔은 이 시험지의 실패를 본 사람이 만든 것이다.

## 도표가 든 쪽의 그림과 글로 번호표마다 도표·계열·범주 받기 (`compare/chart-places.ts`, `cli13.ts`, 밴드 VT1, MARC-TICK-42)

밴드 W 의 그림 라벨(`vision.ts`, 그림만)에 쪽의 글을 같이 실은 것이다. 번호표의 대상은 코드의 도표 찾기가 아니라 꼴을 보지 않는 기준(`py/docparse/tagtext.py`)이 고른다. 값은 받지 않는다.

```
cd ts && pnpm -F reconcile exec tsx src/compare/cli13.ts <raws|runp|mergep|scorep|inputp> ...
```

- **요청** (`runp`): 쪽마다 한 요청. 번호 목록 + 쪽의 글(`[왼쪽-오른쪽, 세로] 글`, 숫자는 `〔번호〕` 나 `▒`) + (갈래 `image-text` 만) 번호표를 덮은 쪽 그림. 갈래 `text` 는 그림 없이 같은 글만 싣는다. 프롬프트는 `reconcile/label-chart-places@1`, `reconcile/label-chart-places-text@1`.
- **요청을 막는 확인**: 글에서 번호표 표시를 뺀 뒤 숫자 글자가 든 낱말이 연도 꼴이 아니면(`unmaskedInLines`), 글에 요청에 없는 번호표가 있으면 요청을 만들지 않는다.
- **답**: 번호마다 `role`(value / tick / category / other), `chart`(그 쪽 안에서 도표를 가리키는 묶음 이름), `title`, `series`, `category`, `category_tag`(범주의 글자가 번호표에 덮였을 때 그 번호. 글자는 코드가 채운다), `unit`. 값 필드 없음.
- **답 확인** (`judgePlaceAnswers`): 요청에 없는 번호, 같은 번호의 둘째 답, 모델이 적은 글에 그 쪽 번호표가 덮은 값의 글자가 든 답을 버린다(밴드 W 와 같은 기준). **번호 칸은 숫자 글자만 받는다: `〔7〕` 처럼 괄호째 적은 답은 요청에 없는 번호로 버려진다**(실측에서 이 꼴로 버려진 답이 많았다. 보고에 두 가지 수를 나란히 적었다).
- **합치기** (`mergePlaces`): 자리마다 실행의 과반(`majority`) 또는 전부(`unanimous`)가 같은 (갈래, 계열, 범주)를 냈을 때만 쓴다. 한 실행만 낸 답은 쓰지 않는다. 도표 묶음은 이름이 아니라 「같은 묶음에 든 실행의 수」로 잇는다.
- **채점** (`scorep`): 범주·계열은 밴드 W 의 `scoreLabels` 그대로(분모는 정답의 자리 전부), 도표 묶기(`scoreGrouping`: 정답 값 자리의 절반 이상이 한 묶음에 들고 그 묶음의 절반 이상이 그 도표의 것이면 「묶어 냄」), 갈래의 가름, 실행 사이의 같음. `--w-arms` 로 밴드 W 의 저장된 답을 같은 채점에 태운다.
- **표로 세우기** (`inputp` → `cli10.ts build --input`): 답의 묶음을 도표로 삼아 밴드 CT1 의 변환에 넣는다. 번호표의 ID 가 숫자 낱말의 열쇠면 그릇·글자 자리로 값 후보에 잇는다(값 후보가 없는 자리는 옮기지 못한다).
- 정답: KB 는 밴드 W 의 `data/reconcile/exam/kb-2025.charts.json` 그대로, 삼성전자·코스맥스는 `samsung-cosmax.chart-places.json`(서브에이전트 작성, 전문가 검토 전).
- 실측의 수와 한계는 `band-reports/VT1-chart-image-text.md`. 이 조립과 프롬프트는 문서 세 벌에서만 돌려 봤다.

## 지목을 값 없이: 단위의 임베딩·낱말 검색 (밴드 EB1)

방법 F 의 지목(`focus.ts` 의 `value`·`names`)과 견주려고, 상대 문서의 볼 만한 쪽을 값 없이 줄 세우는 길을 따로 뒀다. `value`·`names` 는 그대로다.

- **단위** (`src/compare/embed-nominate.ts`): 표(`tableUnits`: 표 앞의 글 + 단위 줄 + 머리글 행·열의 글. 숫자 칸은 싣지 않는다), 도표(`chartUnits`: 라벨 `제목 · 계열 · 범주` 를 같은 쪽·같은 제목끼리 묶음), 쪽(`pageUnits`: 읽는 차례의 글과 표의 글 칸). 값 후보 숫자는 `render.ts` 의 `maskText` 로 가리고, 이어 붙인 뒤에 값 후보 꼴이 되는 것도 다시 가린다. 연도·식별자 꼴은 남는다. 글에 값 후보가 남으면 명령이 멈춘다.
- **순위**: `rankLexical`(낱말 단위 BM25, 한글 두 글자 조각), `rankEmbedding`(코사인), `fuseRrf`(k 60). 식은 scope3 의 `domain/ranking.ts` 와 같다. 그쪽 함수는 계수(`Factor`)에 묶여 있어 `{ id, text }` 꼴로 다시 썼고, 같은 순위·점수가 나오는지 테스트가 scope3 의 함수와 견준다.
- **색인** (`src/adapters/unitIndex.ts`): 단위 갈래 × 문서마다 파일 하나. 머리에 모델·문서 해시·글의 해시가 있고 하나라도 다르면 `StaleIndexError` 다. 있는 색인은 다시 만들지 않는다.
- **정답** (`data/reconcile/exam/kb-2025.nominate-gold.json`): 새 판정이 아니라 있는 정답 쌍·판정 기록에서 규칙으로 끌어낸 「표·도표 → 상대 쪽·표」와 「상대 없음」. 판정이 없는 단위는 분모에 없다.

```
cd ts
pnpm -F reconcile exec tsx src/compare/cli14.ts gold    --out <디렉터리> [--check <정답 파일>]
pnpm -F reconcile exec tsx src/compare/cli14.ts units   --a <문서 A> --b <문서 B> --chart-labels <이름=라벨 파일,..> --out <디렉터리>
pnpm -F reconcile exec tsx src/compare/cli14.ts dry-run --a .. --b .. --chart-labels .. --out <디렉터리>
pnpm -F reconcile exec tsx src/compare/cli14.ts index   --a .. --b .. --chart-labels .. --out <디렉터리> (--provider mock | --provider openai --route light-openai --limit-usd <상한> --log <호출 기록>)
pnpm -F reconcile exec tsx src/compare/cli14.ts rank    --a .. --b .. --chart-labels .. --gold <정답 파일> --index <색인 디렉터리> --out <디렉터리>
pnpm -F reconcile exec tsx src/compare/cli15.ts same-doc --doc <문서> --side <a|b> --chart-labels <이름=라벨 파일> --index <색인 디렉터리> --out <디렉터리>
```

공급사를 부르는 것은 `index` 뿐이고 임베딩 경로 하나만 탄다(생성 호출 없음). `rank` 는 방법마다 재현율@1·3·5, 순위 분포, 상대 없는 질의의 점수 분포, `value`·`names` 와의 비교, 방법 F 에 넘길 때의 요청 수·입력 토큰 어림을 낸다. `cli15.ts same-doc` 은 같은 문서 안에서 도표 → 표·쪽을 본다(정답을 값으로 세므로 아래쪽 끝).

KB 보고서 ↔ 데이터북의 실측(2026-10-06, `text-embedding-3-small`, 586건 481,681 토큰 0.0096 USD)은 `band-reports/EB1-embed-nominate.md` 에 있다. 요약: 표는 세 방법 모두 8개 가운데 7개를 1등으로 짚었고(낱말 검색만으로 같다), 도표는 임베딩만으로 5개 가운데 0개(상위 5쪽), 낱말 검색 3개, `value` 5개였다. 상대 없는 단위의 점수는 옳은 상대의 점수와 겹쳐 문턱이 서지 않았다. 분모가 작다(표 8·7, 도표 5).
