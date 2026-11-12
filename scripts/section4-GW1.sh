#!/bin/bash
# 밴드 GW1 의 자기검증(발주서 §4). 워크트리 뿌리에서: bash scripts/section4-GW1.sh [out/ 아래의 산출물 자리]
# 줄마다 EXIT= 를 찍고 마지막 줄에 SECTION4_FAIL=<0 이 아닌 EXIT 의 수> 를 찍는다.
# 공개 보고서의 dry-run 과 원본 대조(py/greenwash/tests/notebook_probe.py)는 여기에 없다(파일이 저장소 밖이다).
B="${B:-f1b2c77}"
OUT="$(pwd)/${1:-out/band-GW1/section4}"
case "$OUT" in "$(pwd)/out/"*) ;; *) echo "OUT 은 out/ 아래여야 한다"; exit 2 ;; esac
rm -rf "$OUT"; mkdir -p "$OUT"
export TIKTOKEN_CACHE_DIR="$(pwd)/out/band-GW1/tiktoken-cache"
G="uv run --frozen --group greenwash"
FAIL=0
t() { local rc=$?; echo "EXIT=$rc"; [ "$rc" = 0 ] || FAIL=$((FAIL + 1)); }

(cd py && uv sync --frozen --exact) ; t                                     # 묶음 없이 정확히
(cd py && uv run --frozen pytest -q --ignore=greenwash) ; t                 # 묶음 없이도 기존 것이 그대로 돈다(기준 212건)
(cd py && uv sync --frozen --group greenwash) ; t
(cd py && $G pytest -q) ; t                                                 # 전량
(cd py && $G pytest -q greenwash/tests --collect-only | tail -1) ; t        # 새 건수
(cd py && $G python greenwash/tests/build_sample.py "$OUT/sample.pdf") ; t
(cd py && $G python -m greenwash scan "$OUT/sample.pdf" --provider mock --ceo 1 --company 2 --climate 3~4 --out "$OUT/mock") ; t
(cd py && $G python -m greenwash scan "$OUT/sample.pdf" --dry-run --ceo 1 --company 2 --climate 3~4 --out "$OUT/dry") ; t
(cd py && $G python -m greenwash validate "$OUT"/mock/*.json "$OUT"/dry/*.json) ; t
git diff --quiet "$B" -- ts py/docparse py/factors data scripts/git-hooks schemas/char-places.schema.json schemas/chart-candidates.schema.json schemas/chart-labels.schema.json schemas/document.schema.json schemas/factor-mapping.schema.json schemas/factor.schema.json ; t
test "$(git diff --numstat "$B" -- py/pyproject.toml README.md | awk '{d+=$2} END{print d+0}')" = 0 ; t   # 있는 글은 더하기만
test -z "$(grep -rnE '(^|[^A-Za-z0-9_])(import|from)[[:space:]]|import_module\(|__import__\(' py/greenwash --include='*.py' | grep -v -E '^py/greenwash/tests/(notebook_probe|build_sample)\.py:' | grep -E '(^|[^A-Za-z0-9_.])(openai|langchain_openai|langchain_community|langchain|faiss|fitz|pymupdf|httpx|requests|urllib|socket)([^A-Za-z0-9_]|$)')" ; t
test -z "$({ git diff --name-only "$B"; git ls-files -o --exclude-standard; } | grep -i -E '\.(pdf|ipynb|xlsx|zip)$')" ; t   # 자료 파일 0
test -z "$(grep -rnE 'sk-[A-Za-z0-9_-]{20,}' py/greenwash schemas/greenwash-result.schema.json scripts/section4-GW1.sh)" ; t   # 키 꼴 0
test -z "$({ git diff --name-only "$B"; git ls-files -o --exclude-standard; } | sort -u | grep -v -E '^(py/greenwash/|schemas/greenwash-result\.schema\.json$|scripts/section4-GW1\.sh$|py/pyproject\.toml$|py/uv\.lock$|README\.md$)')" ; t   # 허용 경로 밖 0
echo "SECTION4_FAIL=$FAIL"
[ "$FAIL" = 0 ]
