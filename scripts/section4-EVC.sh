#!/bin/bash
# 밴드 EVC 의 자기검증(발주서 §4). 워크트리 뿌리에서: bash scripts/section4-EVC.sh [out/ 아래의 산출물 자리]
# 줄마다 EXIT= 를 찍고 마지막 줄에 SECTION4_FAIL=<0 이 아닌 EXIT 의 수> 를 찍는다. 고정물은 전부 지은 것(synthetic) —
# 받은 자료(received/)·패키지 파일은 이 스크립트가 읽지 않는다(M1~M5 측정은 git 밖 드라이버 몫, 보고에).
B="${B:-1b1cca0}"
OUT="$(pwd)/${1:-out/band-EVC/section4}"
case "$OUT" in "$(pwd)/out/"*) ;; *) echo "OUT 은 out/ 아래여야 한다"; exit 2 ;; esac
rm -rf "$OUT"; mkdir -p "$OUT"
S="uv run --frozen --group scope3colab"
M="uv run --frozen python -m evalrig.scope3"
FAIL=0
t() { local rc=$?; echo "EXIT=$rc"; [ "$rc" = 0 ] || FAIL=$((FAIL + 1)); }
x2() { local rc=$?; echo "EXIT=$rc (기대 2)"; [ "$rc" = 2 ] || FAIL=$((FAIL + 1)); }

# 1. 묶음 없이 V 가 돈다
(cd py && uv sync --frozen --exact) ; t
(cd py && uv run --frozen pytest -q evalrig/scope3/tests) ; t
(cd py && uv run --frozen python -c "import sys, evalrig.scope3.cli; sys.exit(any(m.split('.')[0] in ('pandas', 'rapidfuzz', 'numpy') for m in sys.modules))") ; t   # pandas 를 끌어오지 않음
# 2. 대조 도구 시험 그대로 139 · 이름공간 겹침 · 랩 게이트 192
(cd py && uv sync --frozen --group scope3colab) ; t
(cd py && $S pytest -q --collect-only scope3colab/tests | tail -1 | grep -q '^139 tests collected') ; t   # BASE 와 같은 139
(cd py && $S pytest -q scope3colab/tests) ; t
(cd py && $S pytest -q evalrig/scope3/tests scope3colab/tests) ; t
(cd py && $S pytest trlab raslab -q | tail -1 | grep -q '^192 passed') ; t
(cd py && uv sync --frozen --exact) ; t
# 3. 새 건수
(cd py && uv run --frozen pytest --collect-only -q evalrig/scope3/tests | tail -1) ; t
# 4. CLI 여섯(지은 고정물) — 종료 코드
(cd py && uv run --frozen python -c "
import json, sys
from pathlib import Path
from evalrig.scope3.tests import build
from scope3colab.rows import rows_from_result_xlsx
o = Path(sys.argv[1]); x = build.write_result_xlsx(o / 'fixture.xlsx')
(o / 'engine-rows1.json').write_text(json.dumps(rows_from_result_xlsx(x).to_json() | {'side': 'engine'}, ensure_ascii=False), 'utf-8')
(o / 'result1.json').write_text(json.dumps(build.base_result1(), ensure_ascii=False), 'utf-8')
(o / 'edits.json').write_text(json.dumps(build.edits([build.edit('C1_가상!4', 'C1', choice='recommended', rank=2), build.edit('C1_가상!5', 'C1', excluded=True)]), ensure_ascii=False), 'utf-8')
(o / 'result2.json').write_text(json.dumps({'schemaVersion': 'scope3.next.result@2'}), 'utf-8')
" "$OUT") ; t
(cd py && $M gold-example --result-xlsx "$OUT/fixture.xlsx" --out "$OUT/gold/example.json") ; t
(cd py && $M score --gold "$OUT/gold/example.json" --result "$OUT/engine-rows1.json" --out "$OUT/score-example") ; t
(cd py && $M gold-edits --result "$OUT/result1.json" --edits "$OUT/edits.json" --out "$OUT/gold/edits.json") ; t
(cd py && $M score --gold "$OUT/gold/edits.json" --result "$OUT/result1.json" --out "$OUT/score-edits") ; t
(cd py && $M score --gold "$OUT/gold/example.json" --result "$OUT/result2.json" --out "$OUT/score-r2" 2>/dev/null) ; x2    # result@2 자리
(cd py && $M score --gold "$OUT/gold/example.json" --result "$OUT/result1.json" --out "$OUT/score-by" 2>/dev/null) ; x2    # by 다름
(cd py && $M gold-example --result-xlsx "$OUT/fixture.xlsx" --out "$(pwd)/../scripts/not-out.json" 2>/dev/null) ; x2        # out/ 밖 출력
test ! -e scripts/not-out.json ; t
(cd py && uv run --frozen python -c "
import json, sys
from pathlib import Path
from evalrig.scope3.tests import build
o = Path(sys.argv[1]); spec, tp, bp = build.write_w5_files(o)
(o / 'w5spec.json').write_text(json.dumps(spec, ensure_ascii=False), 'utf-8')
(o / 'w3answers.json').write_text(json.dumps({'answers': {'w3-ctl-ktx-c4': {'mode': 'rail'}}}), 'utf-8')
(o / 'w5answers.json').write_text(json.dumps({'answers': {'energy-01': {'title': '가상 전기'}}}, ensure_ascii=False), 'utf-8')
(o / 'calls.jsonl').write_text(json.dumps({'promptId': 'p@1', 'keyHash': 'a'}) + '\\n', 'utf-8')
(o / 'l5.json').write_text(json.dumps({'schema': 'evc.l5-driver/1', 'rows': [{'source': 'dropdown', 'name': '가상', 'sheet': '에너지', 'matched': None, 'ef': None, 'via': 'fallback_patch', 'score': None}]}, ensure_ascii=False), 'utf-8')
" "$OUT") ; t
(cd py && $M regress w3 --answers "$OUT/w3answers.json" --out "$OUT/w3") ; t
(cd py && $M regress w5 --template "$OUT/t.xlsx" --book "$OUT/b.xlsx" --spec "$OUT/w5spec.json" --answers "$OUT/w5answers.json" --out "$OUT/w5") ; t
(cd py && $M calls --calls "$OUT/calls.jsonl" --label fixture --out "$OUT/l2") ; t
(cd py && $M fuzzy --driver "$OUT/l5.json" --out "$OUT/l5") ; t
(cd py && $M regress w3 --answers "$OUT/w3answers.json" --out "$(pwd)/../scripts/not-out" 2>/dev/null) ; x2                  # out/ 밖 출력(C2 명령)
# 5. 경계
test -z "$({ git diff --name-only "$B"; git ls-files -o --exclude-standard; } | sort -u | grep -v -E '^(py/evalrig/scope3/|scripts/section4-EVC\.sh$|py/pyproject\.toml$|README\.md$)')" ; t   # 허용 경로 밖 0
test ! -e py/evalrig/__init__.py ; t
git diff --quiet "$B" -- py/scope3colab py/uv.lock data schemas ts ; t
test "$(git diff --numstat "$B" -- py/pyproject.toml README.md | awk '{d+=$2} END{print d+0}')" = 0 ; t   # 있는 글은 더하기만
# 6. 위생
test -z "$({ git diff --name-only "$B"; git ls-files -o --exclude-standard; } | grep -i -E '\.(xlsx|ipynb|csv)$')" ; t   # 자료 파일 0
test -z "$(grep -rnE 'sk-[A-Za-z0-9_-]{20,}' py/evalrig/scope3 scripts/section4-EVC.sh)" ; t   # 키 꼴 0
test -z "$(grep -rnE '^[[:space:]]*(import|from)[[:space:]]+(esgapi|openai|anthropic|httpx|requests|socket)([^A-Za-z0-9_]|$)' py/evalrig/scope3 --include='*.py')" ; t   # 금지 import 0(socket_blocked 은 scope3colab.sandbox 에서)
R_SHA="$(cd ~/Desktop/marcspon && shasum -a 256 received/2026-11-02/* esg-app/apps/api-py/esgapi/inventory/data/scope3/* 2>/dev/null | cut -d' ' -f1)"
test -z "$(for f in $({ git diff --name-only "$B"; git ls-files -o --exclude-standard; } | sort -u); do [ -f "$f" ] && shasum -a 256 "$f" | cut -d' ' -f1; done | grep -F -x -f <(echo "$R_SHA"))" ; t   # 받은 파일과 같은 바이트 0
echo "SECTION4_FAIL=$FAIL"
[ "$FAIL" = 0 ]
