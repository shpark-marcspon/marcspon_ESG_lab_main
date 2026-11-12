"""MC1 L4 — 가상 148행 → C1·C2 정답(NAICS·id), 코랩 순서·llm-first 두 결과 → hit@1·hit@3.

- 정답은 `esg-lab/data/scope3-examples/rows.synthetic.jsonl` 의 러너 라벨(전문가 검토 0 — 그 폴더 README). C1·C2(정답
  카테고리 1·2) 91행. 갈래 셋: 정답 NAICS(`epa-seef-v1.3:<NAICS>`) 가운데 하나라도 책 `구매_USEPA` 의 코드 안 = `naics_in_book`
  (37) · NAICS 정답이 있지만 전부 책 밖 = `naics_off_book`(16) · 활동 기반 계수만 = `activity_only`(38). 책 코드 집합은 드라이버의
  `title_codes.json`(책에서 뽑은 Title → 코드)에서 받는다(`book_codes_of`). 수는 MC1 critic 1 B1(esg-app `968de65f` 책: 데이터
  190줄 · 고유 코드 182).
- 코랩 순서(`colab_157`)는 C1·C2 에서 지출 기반 `구매_USEPA` 책 줄(NAICS 2017 Title)만 고른다 — 그래서 `naics_off_book`·
  `activity_only` 행은 구조상 맞출 수 없고 분모에서 빼 `structuralMiss` 로만 센다(틀림 아님). 코랩 분모 = `naics_in_book`.
  - 결과는 드라이버가 낸 덤프 `scope3colab.rows/1` 을 **기존 어댑터 `adapt()`** 로 읽는다(후보 = 대표 + 추천, 겹침·빈 자리 뺌).
    top-3 = 그 후보 차례의 앞 셋(겹침이 없으면 대표 + 추천 앞 둘). 줄 짝은 `order.json`([{row_id, sheet, row}]), Title → NAICS
    코드는 `title_codes.json`(Title → 코드 목록 — 한 Title 에 코드가 여럿일 수 있다).
- llm-first(`llm_first_1`)의 흐름 결과 `scope3.llm-first.result@2` 는 `adapt()` 가 2차 자리(`RESULT2_PATTERN`)로 보고 거절하므로
  **`adapt()` 를 부르지 않고** 이 모듈이 묶음의 `decision.picks[].factorId` 차례를 직접 읽는다.
- 품질 수만 낸다(부름 수·실비는 `measure_cost`).
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from evalrig.scope3.adapters import adapt
from evalrig.scope3.unit import ToolFailure

L4_SCHEMA = "evalrig.scope3.mc1-l4/1"
LLM_FIRST_RESULT = "scope3.llm-first.result@2"
NAICS_PREFIX = "epa-seef-v1.3:"
PRODUCED_BY = "runner-label (synthetic, review 0)"
TOP = 3

Branch = Literal["naics_in_book", "naics_off_book", "activity_only"]


@dataclass(frozen=True)
class GoldRow:
    row_id: str
    category: int
    factor_ids: frozenset[str]
    naics: frozenset[str]
    branch: Branch


@dataclass(frozen=True)
class L4Gold:
    rows: tuple[GoldRow, ...]
    meta: dict[str, str]

    def by_branch(self, *branches: Branch) -> tuple[GoldRow, ...]:
        return tuple(r for r in self.rows if r.branch in branches)


def book_codes_of(title_codes_json: Any) -> frozenset[str]:
    try:
        return frozenset(str(c) for codes in title_codes_json.values() for c in codes)
    except (AttributeError, TypeError) as error:
        raise ToolFailure("title_codes.json 꼴 어긋남") from error


def branch_of(naics: frozenset[str], book_codes: frozenset[str]) -> Branch:
    if not naics:
        return "activity_only"
    return "naics_in_book" if naics & book_codes else "naics_off_book"


def gold_l4(rows_path: Path, book_codes: frozenset[str]) -> L4Gold:
    out: list[GoldRow] = []
    for n, line in enumerate(rows_path.read_text("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            gold = d.get("gold") or {}
            cat = gold.get("category")
            if cat not in (1, 2):
                continue
            ids = frozenset(str(i) for i in gold["factor_ids"])
        except (ValueError, KeyError, TypeError) as error:
            raise ToolFailure(f"148행 {n}째 줄 꼴 어긋남") from error
        if not ids:
            raise ToolFailure(f"148행 {n}째 줄: C1·C2 인데 정답 id 가 비었다")
        naics = frozenset(i[len(NAICS_PREFIX):] for i in ids if i.startswith(NAICS_PREFIX))
        out.append(GoldRow(str(d["row_id"]), int(cat), ids, naics, branch_of(naics, book_codes)))
    return L4Gold(tuple(out), {"producedBy": PRODUCED_BY, "source": rows_path.name})


def _rate(hit: int, n: int) -> dict[str, Any]:
    return {"hit": hit, "n": n}


# ── 코랩 순서 ──────────────────────────────────────────────────────────────────────────────────────────────


def _title_hit(titles: tuple[str, ...], naics: frozenset[str], title_codes: dict[str, set[str]], unmapped: set[str]) -> bool:
    for t in titles:
        if t not in title_codes:
            unmapped.add(t)
            continue
        if title_codes[t] & naics:
            return True
    return False


def score_colab(gold: L4Gold, rows1_json: Any, order_json: Any, title_codes_json: Any) -> dict[str, Any]:
    out = adapt(rows1_json)
    units = {(u.key.sheet, u.key.row): u for u in out.units if u.category in ("C1", "C2")}
    try:
        order = {str(o["row_id"]): (str(o["sheet"]), int(o["row"])) for o in order_json}
        title_codes = {str(t): {str(c) for c in codes} for t, codes in title_codes_json.items()}
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise ToolFailure("order.json·title_codes.json 꼴 어긋남") from error
    naics_rows = gold.by_branch("naics_in_book")
    hit1 = hit3 = missing = no_candidates = 0
    unmapped: set[str] = set()
    for g in naics_rows:
        unit = units.get(order[g.row_id]) if g.row_id in order else None
        if unit is None:
            missing += 1
            continue
        cands = unit.candidates or ()
        if not cands:
            no_candidates += 1

        hit1 += int(_title_hit(cands[:1], g.naics, title_codes, unmapped))
        hit3 += int(_title_hit(cands[:TOP], g.naics, title_codes, unmapped))
    return {
        "schema": L4_SCHEMA,
        "side": "colab_157",
        "gold": dict(gold.meta),
        "hit1": _rate(hit1, len(naics_rows)),
        "hit3": _rate(hit3, len(naics_rows)),
        "structuralMiss": {
            "naicsOffBook": len(gold.by_branch("naics_off_book")),
            "activityOnly": len(gold.by_branch("activity_only")),
        },
        "missingRows": missing,
        "noCandidates": no_candidates,
        "unmappedTitles": len(unmapped),
        "classification": {"status": "n/a", "reason": "코랩 순서는 시트가 카테고리를 정한다(W9)"},
        "multiCodeTitles": sum(1 for codes in title_codes.values() if len(codes) > 1),
    }


# ── llm-first ─────────────────────────────────────────────────────────────────────────────────────────────


def _llm_first_rows(result_json: Any) -> dict[str, tuple[list[str], Any, str]]:
    """rowId → (고른 id 차례, 예측 카테고리, 판단 status)."""
    if not isinstance(result_json, dict) or result_json.get("schemaVersion") != LLM_FIRST_RESULT:
        raise ToolFailure(f"schemaVersion 이 {LLM_FIRST_RESULT!r} 가 아니다")
    if result_json.get("status") != "done":
        raise ToolFailure(f"llm-first 결과 status {result_json.get('status')!r} — 채점할 줄이 없다")
    try:
        groups = {}
        for g in result_json["groups"]:
            d = g["decision"]
            picks = [str(p["factorId"]) for p in d.get("picks") or []] if d.get("status") == "recommended" else []
            cat = d.get("category")
            if cat is None and isinstance(d.get("classification"), dict):
                cat = d["classification"].get("category")
            groups[g["key"]] = (picks, cat, str(d.get("status")))
        return {str(r["rowId"]): groups[r["groupKey"]] for r in result_json["rows"]}
    except (KeyError, TypeError) as error:
        raise ToolFailure("llm-first 결과 꼴 어긋남") from error


def score_llm_first(gold: L4Gold, result_json: Any) -> dict[str, Any]:
    rows = _llm_first_rows(result_json)
    naics_rows = gold.by_branch("naics_in_book", "naics_off_book")
    book_rows = gold.by_branch("naics_in_book")

    def picks(g: GoldRow) -> list[str]:
        return rows[g.row_id][0] if g.row_id in rows else []

    def any_id(g: GoldRow, k: int) -> bool:
        return bool(set(picks(g)[:k]) & g.factor_ids)

    def naics_only(g: GoldRow, k: int) -> bool:
        codes = {p[len(NAICS_PREFIX):] for p in picks(g)[:k] if p.startswith(NAICS_PREFIX)}
        return bool(codes & g.naics)

    statuses: dict[str, int] = {}
    for g in gold.rows:
        s = rows[g.row_id][2] if g.row_id in rows else "missing"
        statuses[s] = statuses.get(s, 0) + 1
    return {
        "schema": L4_SCHEMA,
        "side": "llm_first_1",
        "gold": dict(gold.meta),
        "all91": {
            "hit1": _rate(sum(any_id(g, 1) for g in gold.rows), len(gold.rows)),
            "hit3": _rate(sum(any_id(g, TOP) for g in gold.rows), len(gold.rows)),
        },
        "naics53": {
            "hit1": _rate(sum(any_id(g, 1) for g in naics_rows), len(naics_rows)),
            "hit3": _rate(sum(any_id(g, TOP) for g in naics_rows), len(naics_rows)),
        },
        "naics53CodeOnly": {
            "hit1": _rate(sum(naics_only(g, 1) for g in naics_rows), len(naics_rows)),
            "hit3": _rate(sum(naics_only(g, TOP) for g in naics_rows), len(naics_rows)),
        },
        "inBook37": {
            "hit1": _rate(sum(any_id(g, 1) for g in book_rows), len(book_rows)),
            "hit3": _rate(sum(any_id(g, TOP) for g in book_rows), len(book_rows)),
        },
        "inBook37CodeOnly": {
            "hit1": _rate(sum(naics_only(g, 1) for g in book_rows), len(book_rows)),
            "hit3": _rate(sum(naics_only(g, TOP) for g in book_rows), len(book_rows)),
        },
        "classification": _rate(
            sum(1 for g in gold.rows if g.row_id in rows and rows[g.row_id][1] == g.category), len(gold.rows)
        ),
        "missingRows": sum(1 for g in gold.rows if g.row_id not in rows),
        "decisionStatus": dict(sorted(statuses.items())),
    }


def _md(score: dict[str, Any]) -> str:
    lines = [f"# L4 채점 — {score['side']}", "", f"정답: {score['gold']['producedBy']} · {score['gold']['source']}", ""]
    def fmt(r: dict[str, Any]) -> str:
        return f"{r['hit']}/{r['n']}"
    if score["side"] == "colab_157":
        lines += [
            f"- 책 안 NAICS 정답 행 hit@1 {fmt(score['hit1'])} · hit@3 {fmt(score['hit3'])}",
            (
                f"- 구조상 못 맞춤: 책 밖 NAICS {score['structuralMiss']['naicsOffBook']} · 활동 기반만 "
                f"{score['structuralMiss']['activityOnly']} · 결과 없는 줄 {score['missingRows']} · "
                f"후보 0 {score['noCandidates']} · 코드 못 찾은 Title {score['unmappedTitles']}"
            ),
            "- 분류: n/a(시트가 정함)",
        ]
    else:
        lines += [
            f"- 91행 hit@1 {fmt(score['all91']['hit1'])} · hit@3 {fmt(score['all91']['hit3'])}",
            f"- 53행 hit@1 {fmt(score['naics53']['hit1'])} · hit@3 {fmt(score['naics53']['hit3'])}",
            (
                f"- 책 안 37행 hit@1 {fmt(score['inBook37']['hit1'])} · hit@3 {fmt(score['inBook37']['hit3'])} · "
                f"코드만 hit@1 {fmt(score['inBook37CodeOnly']['hit1'])} · hit@3 {fmt(score['inBook37CodeOnly']['hit3'])}"
            ),
            f"- 53행 NAICS 코드만 hit@1 {fmt(score['naics53CodeOnly']['hit1'])} · hit@3 {fmt(score['naics53CodeOnly']['hit3'])}",
            f"- 분류(정답 카테고리와 같음) {fmt(score['classification'])} · 결과 없는 줄 {score['missingRows']}",
            f"- 판단 status {score['decisionStatus']}",
        ]
    return "\n".join(lines) + "\n"


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as error:
        raise ToolFailure(f"{path.name} 를 못 읽었다 — {type(error).__name__}") from error


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evalrig.scope3.measure_l4")
    ap.add_argument("--rows", type=Path, required=True, help="148행 rows.synthetic.jsonl")
    ap.add_argument("--side", choices=("colab", "llm-first"), required=True)
    ap.add_argument("--result", type=Path, required=True, help="colab: 덤프 rows/1 · llm-first: result@2")
    ap.add_argument("--order", type=Path, help="colab 만: order.json")
    ap.add_argument("--title-codes", type=Path, required=True, help="책에서 뽑은 title_codes.json(정답 갈래·코랩 Title→코드)")
    ap.add_argument("--out", type=Path, required=True, help="score-<판> (.json·.md 를 붙여 씀)")
    args = ap.parse_args(argv)
    try:
        title_codes = _load(args.title_codes)
        gold = gold_l4(args.rows, book_codes_of(title_codes))
        if args.side == "colab":
            if args.order is None:
                raise ToolFailure("colab 은 --order 가 있어야 한다")
            score = score_colab(gold, _load(args.result), _load(args.order), title_codes)
        else:
            score = score_llm_first(gold, _load(args.result))
    except ToolFailure as error:
        print(f"도구 실패: {error}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    Path(f"{args.out}.json").write_text(json.dumps(score, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8")
    Path(f"{args.out}.md").write_text(_md(score), "utf-8")
    print(_md(score), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
