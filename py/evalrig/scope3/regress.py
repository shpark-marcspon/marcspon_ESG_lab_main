"""회귀 묶음 W3(낱말 오검출)·W5(드롭다운 이름 ↔ 계수 책 줄) — 드라이버가 낸 답을 묶음과 대 「맞음·틀림·답 없음」 셋으로.

답 없음은 틀림에 접지 않는다. W5 의 `undecided` 사례(같은 글자의 책 줄이 없음 — 사람 판정 몫)는 맞음·틀림 밖이고, 1차가 붙인
답만 표(out/ 안)에 적는다. 묶음 파일은 엔진에 매이지 않는다(1차가 타는 함수의 대응표는 드라이버 쪽).

- W3 사례 `{id, category, sheet, field, text, expect: {kind: mode|family|codeNot, value}, why}`, 답 `{caseId: {mode|family: 코드}}`.
- W5 짝 파일(`regress/w5.spec.json`)은 이름 0 — `build_w5` 가 실행 때 템플릿 `배출계수_드롭다운` 과 계수 책에서 이름을 읽어
  사례를 짓는다(`{id, pair, name, expect: exact|undecided, bookRow, expectAnswer}`). 사례 파일은 `out/` 에만(이름이 든다).
  답 `{caseId: {title: 글}}` 은 `expectAnswer`(그 책 줄의 답 열 글)와 글자 같음.
"""

from collections import Counter
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from evalrig.scope3.unit import ToolFailure

W3_SCHEMA = "evalrig.scope3.regress-w3/1"
W5_SCHEMA = "evalrig.scope3.regress-w5/1"
W5_CASES_SCHEMA = "evalrig.scope3.regress-w5-cases/1"
REGRESS_SCHEMA = "evalrig.scope3.regress/1"
DEFAULT_W3 = Path(__file__).parent / "regress" / "w3.cases.json"
DEFAULT_W5 = Path(__file__).parent / "regress" / "w5.spec.json"
DROPDOWN_SHEET = "배출계수_드롭다운"
PASS, FAIL, NO_ANSWER = "pass", "fail", "no_answer"


def judge_w3(case: dict[str, Any], answer: dict[str, Any] | None) -> str:
    expect = case["expect"]
    kind, value = expect["kind"], expect["value"]
    field = "family" if kind == "family" or case["category"] in ("C1", "C2") else "mode"
    got = None if answer is None else answer.get(field)
    if got is None:
        return NO_ANSWER
    if kind == "codeNot":
        return FAIL if got == value else PASS
    if kind in ("mode", "family"):
        return PASS if got == value else FAIL
    raise ToolFailure(f"{case['id']}: 모르는 expect.kind {kind!r}")


def _summary(outcomes: dict[str, str]) -> dict[str, int]:
    c = Counter(outcomes.values())
    return {PASS: c[PASS], FAIL: c[FAIL], NO_ANSWER: c[NO_ANSWER], "of": len(outcomes)}


def score_w3(cases_doc: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    if cases_doc.get("schema") != W3_SCHEMA:
        raise ToolFailure(f"W3 묶음 schema 가 {W3_SCHEMA!r} 가 아니다")
    got = answers.get("answers", answers)
    ids = [c["id"] for c in cases_doc["cases"]]
    if len(set(ids)) != len(ids):
        raise ToolFailure("W3 사례 id 가 겹친다")
    unknown = sorted(set(got) - set(ids))
    if unknown:
        raise ToolFailure(f"묶음에 없는 답 {len(unknown)}개")
    rows = []
    for c in cases_doc["cases"]:
        outcome = judge_w3(c, got.get(c["id"]))
        rows.append({"id": c["id"], "category": c["category"], "kind": c["expect"]["kind"], "outcome": outcome})
    by_kind: dict[str, dict[str, int]] = {}
    for kind in sorted({r["kind"] for r in rows}):
        by_kind[kind] = _summary({r["id"]: r["outcome"] for r in rows if r["kind"] == kind})
    return {"schema": REGRESS_SCHEMA, "suite": "W3", "summary": _summary({r["id"]: r["outcome"] for r in rows}),
            "byKind": by_kind, "cases": rows}  # fmt: skip


# ── W5 ────────────────────────────────────────────────────────────────────────────────────────────────


def _text(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def dropdown_names(template: Path, column: str, skip: list[str], skip_first: bool) -> list[str]:
    wb = load_workbook(template, read_only=True, data_only=True)
    try:
        rows = list(wb[DROPDOWN_SHEET].iter_rows(values_only=True))
    except KeyError as error:
        raise ToolFailure(f"템플릿에 {DROPDOWN_SHEET} 시트가 없다") from error
    finally:
        wb.close()
    header = [_text(h) for h in rows[0]]
    if column not in header:
        raise ToolFailure(f"드롭다운 열 {column} 이 없다")
    i = header.index(column)
    names = [t for r in rows[1:] if i < len(r) and (t := _text(r[i])) is not None]
    if skip_first:
        names = names[1:]
    return [n for n in names if n not in skip]


def book_rows(book: Path, sheet: str, name_column: str, answer_column: str) -> list[tuple[int, str | None, str | None]]:
    """(엑셀 줄, 이름 열 글, 답 열 글). 머리 줄은 앞 열 줄 가운데 이름 열 글자가 그대로 있는 줄."""
    wb = load_workbook(book, read_only=True, data_only=True)
    try:
        rows = list(wb[sheet].iter_rows(values_only=True))
    except KeyError as error:
        raise ToolFailure(f"계수 책에 {sheet} 시트가 없다") from error
    finally:
        wb.close()
    for hi, r in enumerate(rows[:10]):
        cells = [None if c is None else str(c) for c in r]
        if name_column in cells and answer_column in cells:
            ni, ai = cells.index(name_column), cells.index(answer_column)
            return [(hi + 2 + n, _text(b[ni]) if ni < len(b) else None, _text(b[ai]) if ai < len(b) else None)
                    for n, b in enumerate(rows[hi + 1:])]  # fmt: skip
    raise ToolFailure(f"{sheet}: 머리 줄(이름 열·답 열)을 못 찾았다")


def build_w5(spec: dict[str, Any], template: Path, book: Path) -> dict[str, Any]:
    if spec.get("schema") != W5_SCHEMA:
        raise ToolFailure(f"W5 짝 파일 schema 가 {W5_SCHEMA!r} 가 아니다")
    cases: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    for pair in spec["pairs"]:
        names = dropdown_names(template, pair["dropdown"], spec.get("skip", []), bool(spec.get("skipFirst")))
        rows = book_rows(book, pair["bookSheet"], pair["nameColumn"], pair["answerColumn"])
        exact = 0
        for i, name in enumerate(names, start=1):
            same = [(n, ans) for n, nm, ans in rows if nm == name]
            case: dict[str, Any] = {"id": f"{pair['id']}-{i:02d}", "pair": pair["id"], "name": name}
            if same:
                exact += 1
                case |= {"expect": "exact", "bookRow": same[0][0], "expectAnswer": same[0][1], "sameNameRows": len(same)}
            else:
                case |= {"expect": "undecided"}
            cases.append(case)
        base = pair.get("baseline") or {}
        pairs.append({"id": pair["id"], "dropdown": len(names), "exact": exact, "undecided": len(names) - exact,
                      "baseline": base, "baselineSame": base == {"same": exact, "of": len(names)} if base else None})  # fmt: skip
    return {"schema": W5_CASES_SCHEMA, "pairs": pairs, "cases": cases}


def score_w5(cases_doc: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    if cases_doc.get("schema") != W5_CASES_SCHEMA:
        raise ToolFailure(f"W5 사례 schema 가 {W5_CASES_SCHEMA!r} 가 아니다")
    got = answers.get("answers", answers)
    rows = []
    for c in cases_doc["cases"]:
        title = (got.get(c["id"]) or {}).get("title")
        if c["expect"] == "undecided":
            outcome = "undecided"
        elif title is None:
            outcome = NO_ANSWER
        else:
            outcome = PASS if title == c["expectAnswer"] else FAIL
        rows.append({"id": c["id"], "pair": c["pair"], "expect": c["expect"], "outcome": outcome, "answered": title is not None})
    pairs = {}
    for p in cases_doc["pairs"]:
        mine = [r for r in rows if r["pair"] == p["id"]]
        judged = {r["id"]: r["outcome"] for r in mine if r["expect"] == "exact"}
        pairs[p["id"]] = _summary(judged) | {
            "undecided": sum(1 for r in mine if r["expect"] == "undecided"),
            "undecidedAnswered": sum(1 for r in mine if r["expect"] == "undecided" and r["answered"]),
        }
    judged_all = {r["id"]: r["outcome"] for r in rows if r["expect"] == "exact"}
    return {"schema": REGRESS_SCHEMA, "suite": "W5", "summary": _summary(judged_all), "pairs": pairs, "cases": rows}


def render_md(report: dict[str, Any]) -> str:
    """수·id 만(이름 글 0)."""
    s = report["summary"]
    lines = [f"# 회귀 {report['suite']}", "", f"- 맞음 {s[PASS]} · 틀림 {s[FAIL]} · 답 없음 {s[NO_ANSWER]} / {s['of']}", ""]
    if report["suite"] == "W3":
        lines += ["| 갈래 | 맞음 | 틀림 | 답 없음 | 수 |", "|---|---|---|---|---|"]
        lines += [f"| {k} | {v[PASS]} | {v[FAIL]} | {v[NO_ANSWER]} | {v['of']} |" for k, v in report["byKind"].items()]
        lines += ["", "| id | 카테고리 | 갈래 | 결과 |", "|---|---|---|---|"]
        lines += [f"| {c['id']} | {c['category']} | {c['kind']} | {c['outcome']} |" for c in report["cases"]]
    else:
        lines += ["| 짝 | 맞음 | 틀림 | 답 없음 | exact | undecided(1차가 붙임) |", "|---|---|---|---|---|---|"]
        lines += [f"| {k} | {v[PASS]} | {v[FAIL]} | {v[NO_ANSWER]} | {v['of']} | {v['undecided']}({v['undecidedAnswered']}) |"
                  for k, v in report["pairs"].items()]  # fmt: skip
    return "\n".join(lines) + "\n"
