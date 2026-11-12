# greenwash — 그린워싱 점검의 기준선 (Colab 노트북을 그대로 옮긴 것)

마크스폰이 준 Colab 노트북(「AI그린워싱 점검_E0의 사본」, 프롬프트 판 `2026-01-15-v1`)이 하는 일을 **약점까지 그대로** 옮겼다. 고친 판이 아니다. 고친 판을 만들 때 「원래는 이랬다」를 재는 자가 된다.

노트북이 하는 일: PDF 의 쪽 글을 뽑고 → 사람이 고른 쪽(CEO 메시지, 회사소개, 기후/환경 토픽)마다 의심 문장 후보를 모으고(규칙 + AI) → 문장마다 근거 조각을 찾고 → 등급(High / Mid / Low)과 사유·수정 방향을 낸다. 따로, 의심 낱말을 문서 전체에서 센다(AI 없음).

**이 꾸러미는 실제 LLM·임베딩을 부르지 않는다.** 실제 공급사 어댑터가 없다. 모의 공급사(대본대로 답한다)와 `--dry-run`(보내지 않고 센다)뿐이다. 모의 공급사의 결과는 지은 답으로 만든 것이라 `synthetic: true` 다.

## 명령

`py/` 에서, 묶음 `greenwash` 로 돌린다.

```
uv sync --frozen --group greenwash
uv run --frozen --group greenwash pytest -q greenwash/tests

# 지은 글 네 쪽짜리 표본 PDF (out/ 아래에. 저장소에 넣지 않는다)
uv run --frozen --group greenwash python greenwash/tests/build_sample.py ../out/band-GW1/sample.pdf

# 모의 공급사로 점검 (대본은 --script <json>, 없으면 내장 대본)
uv run --frozen --group greenwash python -m greenwash scan <pdf> --provider mock --ceo 1 --company 2 --climate 3~4 --out <디렉터리>

# 보내지 않고 요청 수·글자·토큰 세기
TIKTOKEN_CACHE_DIR=<캐시 자리> uv run --frozen --group greenwash python -m greenwash scan <pdf> --dry-run --ceo 4 --company 5~19 --climate "37~39,45~64" --out <디렉터리>

uv run --frozen --group greenwash python -m greenwash wordcount <pdf>
uv run --frozen --group greenwash python -m greenwash validate <결과.json>…
```

쪽 입력은 `5`, `6~14`, `25-37,55~74` 꼴이고 쪽 번호는 1부터다. 결과는 `<디렉터리>/greenwash-result.json`(스키마 `schemas/greenwash-result.schema.json`), 모의 실행은 호출 기록 `calls.jsonl` 도 낸다(칸 이름은 `ts/llm/src/log.ts` 의 `CallRecord`. 본문은 싣지 않는다. `promptVersion` 만 숫자가 아니라 노트북의 글자열 판이다).

`--dry-run` 의 토큰은 `tiktoken` 이 센다. 부호화 파일을 처음 한 번 내려받는다(과금 없음). 못 받으면 토큰 칸이 0 이 아니라 `{"status": "unmeasured"}` 로 실리고 종료 코드는 0 이다.

## dry-run 이 세는 것

| 것 | 어떻게 |
|---|---|
| 색인 임베딩 | 문서 전체의 조각을 50개씩 묶은 요청 수와 글자·토큰 |
| 탐지 | 글이 있는 쪽마다 요청 1 (시스템 + 사용자) |
| 규칙 후보 | 코드가 정하므로 정확하다 |
| 아래끝 | AI 가 후보를 하나도 안 돌려줄 때. 규칙 후보 가운데 거름과 쿼터를 지난 것만 판정에 든다. 다시 묻기 0, 근거는 「[근거 없음]」. 판정 코드를 기록만 하는 포트로 실제로 돌려 센다 |
| 위끝 | 쪽마다 탐지 1 + 판정 4(평가 3 + Low 전용 1) + 다시 묻기 3 + 근거 물음 4. 문장 자리에 그 쪽의 가장 긴 문장, 근거 자리에 허용 쪽의 조각 가운데 근거 꼴로 다듬어 토큰이 가장 많은 둘을 넣는다. 출력 토큰은 요청 수 × 1,300 |

토큰은 메시지의 글만 센다(대화 API 가 메시지마다 덧붙이는 틀 토큰은 들지 않는다). 위끝의 문장 길이는 「쪽 글에서 가른 가장 긴 문장」이라서, AI 가 그보다 긴 글을 문장이라고 돌려주면 넘을 수 있다(흠 6). 근거의 합 1,600자 상한은 기본 설정(조각 2 × 450자)에서 닿지 않는다.

## 대응표 (노트북의 것 → 우리 것, 바꾼 것)

「그대로」는 함수 본문·정규식·문자열이 글자까지 같다는 뜻이다(`tests/notebook_probe.py` 가 AST 로 잰다). 바꿔도 되는 것은 셋뿐이었다: `print` 걷기, LLM·임베딩·벡터 검색을 포트로, Colab 전용 코드를 인자로.

| 노트북 | 우리 | 바꾼 것 |
|---|---|---|
| 셀 8 `PyPDFLoader(pdf_path).load()` | `pdftext.read_page_texts`, `load_docs` | `pypdf` 를 바로 부르고 양끝 공백을 뗀다(`PyPDFLoader` 가 하는 뒤처리). 메타는 `page`(0부터)만. 파일 올리기와 미리보기 `print` 는 없다 |
| 셀 11 `_normalize_for_retrieval`, `_ensure_page_metadata` | `chunking` 의 같은 이름 | 그대로 |
| 셀 11 `build_vectorstore_from_docs` | `chunking.build_chunks_from_docs` | 조각내기와 메타 붙이기는 그대로. `openai_api_key` 인자와 그 검사, `print` 둘, 임베딩·`FAISS.from_documents`(5절)를 뺐다. 돌려주는 것에서 `vectorstore` 가 빠졌다 |
| 셀 11 `_import_recursive_splitter` | (없음) | `langchain_text_splitters` 를 바로 import 한다 |
| 셀 11 `FAISS.from_documents`, 셀 14 `vectorstore.similarity_search` | `retrieval.VectorIndex.from_documents`, `.similarity_search` | numpy 의 정확 L2(제곱 거리, float32, 가까운 차례, 동점은 번호 작은 것 먼저) |
| 셀 14 `enable_safe_mode`, `enable_sqlite_llm_cache` | (없음) | 스레드 환경 변수와 캐시 켜기는 옮기지 않았다(노트북에서도 캐시는 켜지지 않았다. 흠 14) |
| 셀 14 `cap_text` ~ `_fallback_fix_paragraph`(0·2·3·4절의 함수 열아홉과 정규식·낱말 목록 여섯) | `heuristics` 의 같은 이름 | 그대로 |
| 셀 14 `SAFE_LIMITS_INT`, `GWConfig` | `checker` 의 같은 이름 | 그대로 |
| 셀 14 `sanitize_config_dict` | `checker.sanitize_config_dict` | `verbose` 일 때의 `print` 를 뺐다 |
| 셀 14 `PromptPack`, `GreenwashingChecker._default_prompts` | `prompts.PromptPack`, `prompts.default_prompts`(= `_NotebookPrompts._default_prompts`) | 그대로. 클래스에서 떼어 냈다. `PROMPT_VERSION` 상수를 더했다 |
| 셀 14 `GreenwashingChecker.__init__` | `checker.GreenwashingChecker.__init__` | `openai_api_key`·`model_name`·`temperature`·`max_tokens` 인자와 `ChatOpenAI(...)` 대신 대화 포트 `llm` 을 받는다. 세 값은 상수 `MODEL_NAME`·`TEMPERATURE`·`MAX_TOKENS` 로 남겼다 |
| 셀 14 `filter_and_rank`, `build_evidence`, `_is_generic_fix` | 같은 이름 | 그대로 |
| 셀 14 `detect_candidates`, `eval_high_mid_or_nonrisk`, `eval_low_only`, `analyze_page` | 같은 이름 | `debug` 일 때의 `print` 를 뺐다(다섯 군데). `SystemMessage`·`HumanMessage` 는 `ports` 의 것 |
| 셀 14 `scan_report_greenwashing_pages` | `checker.scan_report_greenwashing_pages` | `openai_api_key`·`enable_cache`·`cache_db_path` 인자 대신 `llm`. `print` 를 걷고, 결과 목록만 돌려주던 것을 `ScanOutcome`(결과, 후보 집계, 실패 수, 실패한 쪽과 까닭, 점검 쪽, 허용 쪽)으로 돌려준다 |
| 셀 16 `parse_pages`, `_clip_pages_to_doc_range` | `parts` 의 같은 이름 | 그대로 |
| 셀 16 의 `input()` 셋과 범위 자르기 | `parts.build_section_pages` | `input()` 을 인자로 |
| 셀 16 의 실행(`debug: True`, `debug_pages: 2`, 근거 허용 쪽 = 점검 쪽) | `cli.run_scan`, `cli.SCAN_CONFIG` | 필수 객체 검사와 `print` 를 뺐다 |
| 셀 19 `build_results_by_part` | `parts.build_results_by_part` | 그대로 |
| 셀 19 `print_overall_stats`, `_as_list`, `_print_item_pretty`, `summarize_by_part` | (없음) | 화면에 찍는 것이라 옮기지 않았다. JSON 으로 낸다. 그 출력 단계는 `fix_bullets` 를 먼저 찾는데 결과에는 그 칸이 늘 없다(흠 13) |
| 셀 22 `GREEN_TERMS` | `wordcount.GREEN_TERMS` | 그대로(빠진 쉼표 셋 포함) |
| 셀 24 `normalize_text` ~ `count_green_terms_in_text`(함수 여섯과 제외 패턴) | `wordcount` 의 같은 이름 | 그대로 |
| 셀 24 `count_green_terms_in_docs` | 같은 이름 | `print` 를 뺐다 |

노트북에 없던 것: `ports`(포트, 모의 대화·모의 임베딩, 토큰 세기, 호출 기록), `dryrun`, `result`(JSON 조립과 스키마 검증), `cli`.

## 일부러 남긴 흠 (고치지 않았다)

발주서의 열일곱에 이 밴드에서 찾은 것(18~)을 더했다. 원본 대조가 이것들이 그대로임을 잰다.

| # | 흠 | 자리 |
|---|---|---|
| 1 | 쪽 글은 앞 2,500자만 본다. 자르면 끝에 안내 문구가 붙고 규칙 후보도 잘린 글에서 뽑는다 | `get_page_text`, `analyze_page` |
| 2 | 쪽당 AI 후보 3개까지, 판정은 Low 아닌 것 3 + Low 1 | `detect_candidates`, `filter_and_rank`, `analyze_page` |
| 3 | 근거는 가까운 조각 12개(`max(k*5, 12)`)를 받아 점검 쪽 안의 것만 남기고 앞 2개, 조각당 450자, 합 1,600자. 남는 것이 없으면 「[근거 없음]」이고 그때 NonRisk 는 Mid. 문장이 든 그 조각이 근거로 돌아올 수 있다 | `build_evidence`, `eval_high_mid_or_nonrisk` |
| 4 | Low 는 어미 정규식(열셋)이 정하고 숫자·`%`·`검증` 따위가 있으면 Low 가 아니다 | `is_low_declarative` |
| 5 | 환경 낱말 정규식에 경계가 없다(`rec`, `ets`, `배출`, `전력` …) | `DEFAULT_ENV_KEYWORDS` |
| 6 | AI 가 돌려준 문장이 쪽 글에 그대로 있는지 확인하지 않는다 | `detect_candidates` |
| 7 | 「고쳐 쓴 예시 금지」를 프롬프트 둘에서 말만 하고 검사하지 않는다 | `prompts` |
| 8 | JSON 은 `[`…`]` 를 먼저, 그다음 `{`…`}` 로 구제한다. 잡글이나 코드 울타리에 싸인 dict 응답은 안쪽 배열이 잡혀 등급 Mid 로 떨어진다. 등급이 이상해도 Mid | `_extract_json_substring`, `_json_loads_safe` |
| 9 | NonRisk 가드 둘(누락 항목이 있으면 Mid, 확인된 사실이 둘 미만이면 Mid) | `eval_high_mid_or_nonrisk` |
| 10 | 수정 방향이 120자 미만이거나 일반론이면 한 번 다시 묻고, 60자 미만이면 폴백 문구, 끝맺음이 다르면 「추가 보완이 필요합니다.」를 붙인다 | `_is_generic_fix`, `_fallback_fix_paragraph`, `_ensure_polite_ending` |
| 11 | 낱말 목록에 쉼표가 빠진 곳이 셋이라 낱말 여섯이 셋으로 붙는다. 경계는 두 글자 이하와 띄어쓰기 없는 세 글자 낱말에 걸려 조사가 붙으면 세지 않는다. 제외 패턴은 둘 | `wordcount` |
| 12 | 쪽 하나가 예외로 죽으면 넘어간다(실패 수만 센다) | `scan_report_greenwashing_pages` |
| 13 | 한 쪽이 두 파트에 들면 결과가 두 파트에 다 실린다. `fix`·`action`·`fix_paragraph` 는 같은 글 세 벌 | `parts`, `analyze_page` |
| 14 | 응답 캐시가 없다. temperature 0, seed 없음, 응답 형식 지정 없음 | (노트북의 `ChatOpenAI(...)`) |
| 15 | Low 의 수정 방향이 40자 미만이면 고정 문구 | `eval_low_only` |
| 16 | 규칙 후보도 쪽당 `max(3, 상한)` 개에서 끊고, 합칠 때 AI 후보보다 앞에 선다 | `collect_rule_based_declaratives`, `analyze_page` |
| 17 | AI 응답의 `decision`·`sentence`·`fix_paragraph` 가 글자열이 아니면 `.strip()` 에서 예외가 나고 그 쪽 전체가 실패로 넘어간다 | `eval_high_mid_or_nonrisk`, `detect_candidates` |
| 18 | 환경 낱말을 넓히는 설정(`env_keywords_extra`, `include_general_env`)은 프로세스 전역을 바꾸고 되돌리지 않는다. 한 번 넓히면 그 뒤의 스캔에도 남는다 | `set_env_keywords`, `scan_report_greenwashing_pages` |
| 19 | 실패한 쪽은 후보 집계에도 들지 않고, 그 쪽에서 이미 판정이 끝난 문장도 버려진다 | `scan_report_greenwashing_pages` |
| 20 | 문장 가르기가 「다」+ 공백에서 어디서나 끊는다(낱말 가운데의 「다」도). 마침표 없는 긴 글은 한 문장이 된다 | `split_sentences_kor` |
| 21 | 낱말 세기는 쪽마다 되풀이되는 머리글도 센다. 낱말에 띄어쓰기가 없으면 띄어 쓴 꼴(`탄소 중립`)은 세지 않는다. 네 글자 이상 영문 낱말은 경계가 없다(`freedom` 의 `free`) | `wordcount` |
| 22 | `build_flexible_pattern` 의 문서 글에 `\s` 가 날것으로 있어 처음 컴파일할 때 `SyntaxWarning` 이 한 번 난다(동작은 같다) | `wordcount` |
| 23 | Low 문장도 근거를 찾는다(임베딩 물음이 한 번 나간다). 그 근거는 Low 전용 프롬프트에 실리지만 결과의 `evidence_internal` 은 늘 빈 글이다 | `analyze_page` |
| 24 | `candidate_stats.promo_dropped` 는 늘 0 — 환경 낱말이 없는 후보를 먼저 거르고(`non_env_dropped`), 홍보 판정은 환경 낱말이 있으면 거짓이라 홍보 거르기에 걸리는 후보가 없다 | `filter_and_rank`(`checker.py` 181~186줄), `is_promo_like`(`heuristics.py` 149줄) |

## 원본 대조

```
uv run --frozen --group greenwash python greenwash/tests/notebook_probe.py compare <노트북.ipynb> <PDF>… [--leak-file <보고 파일>]
```

노트북의 셀 11·14·16·19·22·24 의 소스를 하위 프로세스의 메모리에서 실행하고(가짜 `langchain_openai`·`langchain_community`·`faiss`, 소켓 막기, 키 환경 변수 지우기, 파일 쓰기 0) 같은 입력·같은 대본·같은 가짜 검색으로 우리 코드와 `==` 로 견준다. 줄마다 「다른 것 / 견준 수」와 `PROBE_DIFF=` 를 낸다. 노트북은 저장소에 없다(받은 자료 폴더에 있다. 경로를 인자로 준다). 노트북에 저장된 실행 출력에는 실재 기업의 보고서 문장이 있어 어디에도 옮기지 않는다. 「새지 않음」 줄이 그것을 센다.

`star1`(`--with langchain-community==0.4.2`)은 `PyPDFLoader` 의 쪽 글과 우리 쪽 글을, `star3`(`--with faiss-cpu==1.15.1`)은 `faiss.IndexFlatL2` 와 우리 L2 차례를 견준다. 둘 다 그때만 받는 꾸러미이고 묶음에 넣지 않았다.

## 의존성 (묶음 `greenwash`. 2026-10-13 PyPI 에서 확인)

| 꾸러미 | 판 | 라이선스 | 쓰는 곳 |
|---|---|---|---|
| `pypdf` | 6.20.1 | BSD-3-Clause | 쪽 글 |
| `langchain-text-splitters` | 1.1.3 | MIT | 조각내기 |
| `numpy` | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | L2 검색 |
| `tiktoken` | 0.14.0 | MIT | dry-run 의 토큰 세기 |

라이선스는 `uv.lock` 이 가리키는 배포판의 메타데이터에서 읽었다(`importlib.metadata`). `langchain-text-splitters` 가 `langchain-core` 1.6.10 을 끌고 오고 그 아래로 `langsmith`, `pydantic`, `httpx`, `requests` 따위가 따라 들어온다(잠금 파일에 꾸러미 37개가 더해졌다. 기존 꾸러미의 판은 바뀌지 않았다). 본 코드는 `langchain_text_splitters` 와 `langchain_core.documents` 만 import 한다. `docparse`·`factors` 의 기본 환경에는 들지 않는다.

들이지 않은 것: `langchain`, `langchain-community`, `langchain-openai`, `faiss-cpu`, `openai`.

노트북의 설치 셀에는 판이 없다(`!pip install -q langchain langchain-community langchain-openai faiss-cpu pypdf`). Colab 이 그때 받은 판은 알 수 없다. `pypdf` 의 판이 다르면 쪽 글이 달라질 수 있고, 그러면 2,500자에서 잘리는 자리와 조각이 달라진다.
