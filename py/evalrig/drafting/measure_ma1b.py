"""MA1b 측정 셈 — 1차 보고서 초안(`drafting.draft@v2` draft 지시문)에 견준 사실 단위 판·서술 단계·표째 근거·F-b′ 재료.

순수 셈만(공급사·앱 import 0, 밖 요청 0). 호출·재료 짓기는 git 밖 드라이버(`esg-lab/out/MA1b/drivers/`)가 하고, 여기는
드라이버가 짠 `drafting.bullets@3` 꼴 dict 와 글을 받아 센다.

- 지시문 셋(우리가 지은 글): `FACT_UNIT_SYSTEM`(E-R1 사실 단위) · `NARRATIVE_SYSTEM`(E-R2 서술, 새 숫자·고유명 금지) ·
  `TABLE_FACT_SYSTEM`(E-R6 표째). 1차 draft 칸을 갈음하는 글이고, 근거 표시 꼬리(MARKER_RULES·WRITING_RULES)는 드라이버가
  1차 그대로 붙인다(같은 잣대로 검사하려고). 1차 지시문은 여기 두지 않는다(스냅숏 `DEFAULT_TEXTS`).
- `claim_rate` 주장어 든 bullet 비율 — `CLAIM_WORDS` 는 우리가 지은 평가어 목록(낱말 경계: 앞뒤가 한글·라틴 글자가 아님,
  뒤에 붙는 조사·「하는」「적」 등 정한 꼬리(`_CLAIM_TAILS`)만 받음 — 「우수관」 은 아님).
- `new_numbers` 서술 글의 숫자(`from_v1.numbers` 규칙 — 쉼표 접고 3자 이상) 중 받은 글의 숫자 토막에 없는 것(토막끼리 견줌).
- `new_names` 고유명 **대리** 지표 — 라틴 낱말(2자 이상) · 「」『』“”"" 따옴표 안 글 · 라틴+숫자 섞인 토막 중 받은 글에
  없는 것. 한글 고유명은 못 잰다(무효 조건).
- `cover` 받은 bullet 중(숫자 든 것만) 그 숫자 토막이 서술의 숫자 토막에 다 있는 비율.
- `table_leaves` 잎마다 정답 사실 중 표 블록(`-t<n>` id) 글에 숫자 경계로 있는 비율 · ≥ `TABLE_LEAF_MIN` 이면 표 잎.
- `score_arm` `convert_bullets` → `score.score`(재현·boot_ci) + `code_metrics` + 주장어 · partial 셋 정밀 = unmeasurable.
  `score_subset` 사실 idx 일부만(표 사실)의 재현.
- `summary(out)` `<out>/mr*/rows.json`(드라이버가 쓴 줄) → `common.report.write(out, "summary")`.

`python -m evalrig.drafting.measure_ma1b summary <out/MA1b>` — py/ 에서.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..common import gold as G
from ..common import report as R
from ..common.match import boot_ci, macro, ratio, unmeasurable
from .from_v1 import convert_bullets, numbers
from .score import code_metrics, fact_hit, score

TABLE_LEAF_MIN = 0.5
"""표 잎 문턱 — 잎의 정답 사실 중 표 블록에 있는 비율이 이 값 **이상**(≥)."""

FACT_UNIT_SYSTEM = (
    "너는 지속가능경영 보고서 초안 작성 도우미다. 목차 잎 하나와 전년도 보고서에서 뽑은 자료를 받는다.\n"
    "준 자료에 실제로 적힌 사실을 하나씩 떼어 bullet 로 쓴다. bullet 하나에 사실 하나만 담는다.\n"
    "bullet 글은 「무엇: 값 단위(연도)」 꼴로 쓴다. 주어(회사 이름)·꾸밈말·평가하는 말(뛰어남·앞섬 같은 말)은 뺀다.\n"
    "표에서 뽑은 사실은 「항목·연도·값·단위」 가 다 드러나게 쓴다. 값이 없는 활동·체계는 「무엇: 한 줄 요약」 으로 쓴다.\n"
    "자료에 없는 수치·연도는 지어내지 않는다. 근거가 없으면 그 bullet 은 쓰지 않는다. JSON 으로만 답한다."
)

NARRATIVE_SYSTEM = (
    "너는 지속가능경영 보고서 원고 작성 도우미다. 목차 잎 하나와 그 잎의 사실 bullet 목록(열쇠 F1, F2, …)을 받는다.\n"
    "받은 bullet 만으로 문단 원고를 문장 단위로 쓴다. 문장마다 그 문장이 기댄 bullet 열쇠를 keys 에 단다.\n"
    "받은 bullet 에 없는 숫자·연도·고유명(회사·사업장·인증·기관 이름)을 새로 쓰지 않는다.\n"
    "bullet 에 기대지 않는 잇는 문장(배경·의의·전망을 덧붙이는 문장)은 쓰지 않는다. keys 가 빈 문장은 쓰지 않는다.\n"
    "문장 끝은 「~했다」·「~이다」 꼴의 평서문으로 맞춘다. JSON 으로만 답한다."
)

TABLE_FACT_SYSTEM = (
    "너는 지속가능경영 보고서 초안 작성 도우미다. 목차 잎 하나와 전년도 보고서에서 뽑은 자료를 받는다.\n"
    "자료의 표는 칸이 그대로 살아 있는 표째로 온다. 표의 칸에서 사실을 하나씩 뽑아 bullet 로 쓴다.\n"
    "표 사실은 「항목·연도·값·단위」 가 다 드러나게 한 bullet 에 하나씩 쓰고, 근거는 그 표 id 와 행 번호로 단다.\n"
    "글 블록의 사실도 같은 꼴(「무엇: 값 단위(연도)」)로 쓴다. 평가하는 말은 뺀다.\n"
    "자료에 없는 수치·연도는 지어내지 않는다. 근거가 없으면 그 bullet 은 쓰지 않는다. JSON 으로만 답한다."
)

NARRATIVE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["sentences"],
    "properties": {"sentences": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["text", "keys"],
        "properties": {"text": {"type": "string"}, "keys": {"type": "array", "items": {"type": "string"}}}}}},
}

CLAIM_WORDS = (
    # 우리가 지은 평가어 목록(실재 글에서 옮긴 것 아님) — 「선도」「최고」 꼴.
    "선도", "최고", "최초", "최상", "최대", "최우수", "업계 최고", "독보적", "탁월", "우수", "뛰어난", "앞장",
    "글로벌 리더", "혁신적", "획기적", "모범", "적극적", "성공적", "세계적", "탁월한",
)
_CLAIM_TAILS = ("하는", "하며", "하게", "하고", "했다", "해", "한", "적인", "적으로", "적", "성", "의", "을", "를", "이", "가",
                "은", "는", "으로", "로", "와", "과", "도", "에", "인", "함")
_CLAIM_TAIL = "(?:" + "|".join(sorted(_CLAIM_TAILS, key=len, reverse=True)) + ")?"
_CLAIM_RE = [re.compile(r"(?<![가-힣A-Za-z])" + re.escape(w) + _CLAIM_TAIL + r"(?![가-힣A-Za-z])") for w in CLAIM_WORDS]

_TABLE_ID = re.compile(r"-t\d+$")
_LATIN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z&.'-]*[A-Za-z](?![A-Za-z0-9])")
_MIXED = re.compile(r"(?<![A-Za-z0-9-])(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9][A-Za-z0-9-]*[A-Za-z0-9](?![A-Za-z0-9-])")
_QUOTED = re.compile(r"「([^」]+)」|『([^』]+)』|“([^”]+)”|\"([^\"]+)\"")
_SEP = " | "  # 허용 글 잇개 — num() 이 공백을 접으므로 숫자 아닌 글자로 가른다


def has_claim(text: str) -> bool:
    return any(r.search(text) for r in _CLAIM_RE)


def claim_rate(texts: Iterable[str]) -> dict[str, Any]:
    """주장어가 든 bullet / bullet."""
    ts = list(texts)
    return ratio(sum(has_claim(t) for t in ts), len(ts))


def _joined(allowed_texts: Iterable[str]) -> str:
    return _SEP.join(allowed_texts)


def _number_set(texts: Iterable[str]) -> set[str]:
    return {n for t in texts for n in numbers(t)}


def new_numbers(text: str, allowed_texts: Iterable[str]) -> dict[str, Any]:
    """서술 글의 숫자(서로 다른 것) 중 받은 글의 숫자 토막에 없는 것 → `{new[], rate(ratio 새/전체)}`.

    토막끼리 견준다(`from_v1.numbers` — 쉼표는 토막 안에서만 접힘). 공백을 접고 찾는 `fact_hit` 을 쓰지 않는다 —
    「Scope 2 145,298」 이 「2145298」 로 붙어 받은 145,298 을 새 숫자로 셈(MA1b M-R3 에서 찾음)."""
    allowed = _number_set(allowed_texts)
    got = sorted(set(numbers(text)))
    new = [n for n in got if n not in allowed]
    return {"new": new, "rate": ratio(len(new), len(got))}


def name_tokens(text: str) -> list[str]:
    """고유명 대리 토막(서로 다른 것, 나온 차례) — 라틴 낱말 · 라틴+숫자 섞임 · 따옴표 안 글."""
    out: list[str] = []
    for m in _QUOTED.finditer(text):
        out.append(next(g for g in m.groups() if g is not None).strip())
    for r in (_MIXED, _LATIN):
        out += [m.group(0) for m in r.finditer(text)]
    seen, uniq = set(), []
    for t in out:
        if t and t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def new_names(text: str, allowed_texts: Iterable[str]) -> dict[str, Any]:
    """대리 토막 중 받은 글(공백 접음)에 글자 그대로 없는 것 → `{new[], rate}`. 한글 고유명은 못 잰다."""
    allowed = re.sub(r"\s+", "", _joined(allowed_texts))
    got = name_tokens(text)
    new = [t for t in got if re.sub(r"\s+", "", t) not in allowed]
    return {"new": new, "rate": ratio(len(new), len(got))}


def cover(narr: str, bullets: Sequence[str]) -> dict[str, Any]:
    """숫자 든 bullet 중 그 숫자가 서술에 다 실린 bullet 의 비율(숫자 없는 bullet 은 분모 밖)."""
    with_num = [b for b in bullets if numbers(b)]
    have = _number_set([narr])
    hit = sum(set(numbers(b)) <= have for b in with_num)
    return ratio(hit, len(with_num))


def is_table_block(block_id: str) -> bool:
    return bool(_TABLE_ID.search(block_id))


def table_leaves(gold_items: Iterable[dict[str, Any]], blocks: dict[str, dict[str, str]]) -> dict[str, dict[str, Any]]:
    """`blocks{잎: {블록 id: 글}}`(그 잎 재료의 블록) → 잎마다 `{rate, table(bool), facts[표 사실 idx]}`.

    표 사실 = match 중 하나가 그 잎 재료의 표 블록(`-t<n>`) 글에 숫자 경계로 있는 사실. 재료 블록이 없는 잎은 뺀다."""
    per: dict[str, list[dict[str, Any]]] = {}
    for it in gold_items:
        per.setdefault(it["leaf"], []).append(it)
    out = {}
    for leaf, fs in per.items():
        if leaf not in blocks:
            continue
        tables = [t for b, t in blocks[leaf].items() if is_table_block(b)]
        joined = _joined(tables)
        idx = [f["idx"] for f in fs if tables and fact_hit(f["match"], joined)]
        r = ratio(len(idx), len(fs))
        out[leaf] = {"rate": r, "table": r.get("value", 0) >= TABLE_LEAF_MIN, "facts": idx}
    return out


def _bullet_texts(result: dict[str, Any]) -> list[str]:
    return [str(b.get("text") or "") for lf in result.get("leaves") or [] for b in (lf.get("outcome") or {}).get("bullets") or []]


def score_arm(result: dict[str, Any], gs: dict[str, Any], leaf_map: dict[str, str] | None = None, *, run: str = "ma1b") -> dict[str, Any]:
    """한 판 × 한 책. 산출 잎이 없는 정답 잎은 missed(점수 0 으로 넣지 않음). partial 셋의 정밀은 unmeasurable."""
    units = convert_bullets(result, run=run)
    sc = score(units, gs, leaf_map)
    cm = code_metrics(units)
    prec = unmeasurable(gs["coverage"]) if not G.precision_allowed(gs) else None
    return {"setId": gs["setId"], "recall": sc["recall"], "ci": sc["ci"], "facts": sc["facts"], "missed": sc["missed"],
            "per_leaf": sc["per_leaf"], "precision": prec, "claim_rate": claim_rate(_bullet_texts(result)), **cm}


def score_subset(texts: dict[str, str], gs: dict[str, Any], idx_by_leaf: dict[str, Sequence[int]]) -> dict[str, Any]:
    """잎 글 `texts{잎: 글}` 에서 사실 idx 일부(잎마다)만의 재현 — 거시 · boot_ci · 미시. 글이 없는 잎은 missed."""
    G.check(gs)
    items = {(it["leaf"], it["idx"]): it for it in gs["items"]}
    per, missed, hits, den = {}, [], 0, 0
    for leaf, idxs in idx_by_leaf.items():
        if not idxs:
            continue
        if leaf not in texts:
            missed.append(leaf)
            continue
        h = sum(fact_hit(items[(leaf, i)]["match"], texts[leaf]) for i in idxs)
        per[leaf] = ratio(h, len(idxs))
        hits, den = hits + h, den + len(idxs)
    return {"recall": macro(per), "ci": boot_ci([r["value"] for r in per.values()]), "facts": ratio(hits, den),
            "per_leaf": per, "missed": missed}


def summary(out: str | Path, gold_dir: str | Path) -> tuple[Path, Path]:
    """`<out>/mr*/rows.json`(줄 목록, 줄마다 `gold`=setId 또는 NO_GOLD) → `<out>/summary.{json,md}`."""
    out = Path(out)
    gsets = [G.load(Path(gold_dir) / f"drafting-sg1-{s}.json") for s in ("sam", "cos", "kb", "b4")]
    rows: list[dict[str, Any]] = []
    for p in sorted(out.glob("mr*/rows.json")):
        rows += json.loads(p.read_text(encoding="utf-8"))
    notes = [
        "정답 셋 넷 다 coverage partial — 재현만, 정밀·F1 은 셀 수 없음. b4 는 agent_draft · 검토 없음.",
        "사실 재현 = 숫자 경계 맞춤(score.fact_hit) — 글로 쓴 수·단위 바꿈은 놓친다.",
        "nis·unsupported·주장어·새 숫자·새 이름 대리·덮음은 코드 지표(정답 없음) — 정답 줄과 한 칸에 섞지 않는다.",
        "새 이름은 대리 지표(라틴 낱말·라틴+숫자·따옴표 안 글) — 한글 고유명은 못 잰다.",
    ]
    return R.write(out, "summary", rows, gsets, title="MA1b — 1차 먼저 잴 것 A① 보고서 작성", notes=notes)


def main(argv: list[str] | None = None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) != 2 or a[0] != "summary":
        print("쓰는 법: python -m evalrig.drafting.measure_ma1b summary <out/MA1b>", file=sys.stderr)
        return 2
    from ..iro.cli import GOLD
    jp, mp = summary(a[1], GOLD)
    print(jp, mp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
