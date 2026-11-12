"""`python -m evalrig.scope3 <명령>` — 종료 코드: 셈 끝 0 · 도구 실패 2(목표 미달은 종료 코드가 아니다).

    gold-example --result-xlsx <받은 결과 파일> --out <out/…/gold.json>
    gold-edits   --result <result@1.json> --edits <edits.json> --out <out/…/gold.json>
    score        --gold <gold.json> --result <rows/1 | result@1 JSON> --out <out/… 폴더>
    regress w3   [--cases <w3.cases.json>] --answers <드라이버 답> --out <out/… 폴더>
    regress w5   --template <P 템플릿> --book <P 계수 책> [--spec <w5.spec.json>] [--answers <드라이버 답>] --out <out/… 폴더>
    calls        --calls <calls-*.jsonl> --label <이름> --out <out/… 폴더>
    fuzzy        --driver <L5 드라이버 JSON> --out <out/… 폴더>

받은 자료에서 지은 것은 `out/` 아래에만 쓴다(아니면 2). 경로·파일 이름 글자는 출력에 싣지 않는다.
"""

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from evalrig.scope3 import calls as l2
from evalrig.scope3 import fuzzy as l5
from evalrig.scope3 import regress
from evalrig.scope3.adapters import adapt
from evalrig.scope3.gold_edits import gold_from_edits
from evalrig.scope3.gold_example import gold_from_example
from evalrig.scope3.score import render_md, score
from evalrig.scope3.unit import GoldSet, ToolFailure, read_json, require_out_dir, write_json

OK, FAIL = 0, 2
COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {}


def command(name: str) -> Callable[[Callable[[argparse.Namespace], int]], Callable[[argparse.Namespace], int]]:
    def wrap(fn: Callable[[argparse.Namespace], int]) -> Callable[[argparse.Namespace], int]:
        COMMANDS[name] = fn
        return fn

    return wrap


@command("gold-example")
def _gold_example(a: argparse.Namespace) -> int:
    out = require_out_dir(a.out)
    gold = gold_from_example(a.result_xlsx)
    write_json(out, gold.to_json())
    cats = gold.meta["categories"]
    present = " · ".join(f"{c} {m['rows']}" for c, m in cats.items() if m["status"] == "present")
    print(f"정답 셋 {len(gold.units)}줄 ({present}) → {out.name}")
    return OK


@command("gold-edits")
def _gold_edits(a: argparse.Namespace) -> int:
    out = require_out_dir(a.out)
    gold = gold_from_edits(a.result, a.edits, read_json(a.result), read_json(a.edits))
    write_json(out, gold.to_json())
    print(f"정답 셋 {len(gold.units)}줄 · {gold.meta['marks']} → {out.name}")
    return OK


@command("score")
def _score(a: argparse.Namespace) -> int:
    out = require_out_dir(a.out)
    gold = GoldSet.from_json(read_json(a.gold))
    report = score(gold, adapt(read_json(a.result)))
    write_json(out / "score.json", report)
    (out / "score.md").write_text(render_md(report), "utf-8")
    t = report["targets"]
    print(f"채점 끝 — 분류 {t['classification'].get('rate', t['classification'].get('status'))} · "
          f"top-3 {t['top3'].get('rate', t['top3'].get('status'))} → {out.name}/score.json")  # fmt: skip
    return OK


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="evalrig.scope3", description="Scope 3 정답 셋·채점기(0차 평가 장치 C)")
    sub = p.add_subparsers(dest="command", required=True)
    g = sub.add_parser("gold-example", help="받은 정답 예시(코랩 결과 파일) → 정답 셋")
    g.add_argument("--result-xlsx", type=Path, required=True)
    g.add_argument("--out", type=Path, required=True)
    e = sub.add_parser("gold-edits", help="result@1 + 사람 수정 기록 → 정답 셋")
    e.add_argument("--result", type=Path, required=True)
    e.add_argument("--edits", type=Path, required=True)
    e.add_argument("--out", type=Path, required=True)
    s = sub.add_parser("score", help="정답 셋 × 결과 → score.json·score.md")
    s.add_argument("--gold", type=Path, required=True)
    s.add_argument("--result", type=Path, required=True)
    s.add_argument("--out", type=Path, required=True)
    extend_parser(sub)
    return p


def extend_parser(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """C2 의 명령 — 회귀 W3·W5, L2 부름 수, L5 퍼지 표."""
    r = sub.add_parser("regress", help="회귀 묶음 W3·W5 채점")
    rs = r.add_subparsers(dest="suite", required=True)
    w3 = rs.add_parser("w3")
    w3.add_argument("--cases", type=Path, default=regress.DEFAULT_W3)
    w3.add_argument("--answers", type=Path, required=True)
    w3.add_argument("--out", type=Path, required=True)
    w5 = rs.add_parser("w5")
    w5.add_argument("--template", type=Path, required=True)
    w5.add_argument("--book", type=Path, required=True)
    w5.add_argument("--spec", type=Path, default=regress.DEFAULT_W5)
    w5.add_argument("--answers", type=Path, default=None)
    w5.add_argument("--out", type=Path, required=True)
    c = sub.add_parser("calls", help="L2 — 부름 기록 → 프롬프트별 전·후")
    c.add_argument("--calls", type=Path, required=True)
    c.add_argument("--label", required=True)
    c.add_argument("--out", type=Path, required=True)
    f = sub.add_parser("fuzzy", help="L5 — 드라이버 JSON → 갈래 표(사람 판정 빈 칸)")
    f.add_argument("--driver", type=Path, required=True)
    f.add_argument("--out", type=Path, required=True)


@command("regress")
def _regress(a: argparse.Namespace) -> int:
    out = require_out_dir(a.out)
    if a.suite == "w3":
        report = regress.score_w3(read_json(a.cases), read_json(a.answers))
    else:
        cases = regress.build_w5(read_json(a.spec), a.template, a.book)
        write_json(out / "cases.json", cases)
        if a.answers is None:
            print(" · ".join(f"{p['id']} exact {p['exact']}/{p['dropdown']}" for p in cases["pairs"]) + f" → {out.name}/cases.json")
            return OK
        report = regress.score_w5(cases, read_json(a.answers))
    write_json(out / "regress.json", report)
    (out / "regress.md").write_text(regress.render_md(report), "utf-8")
    s = report["summary"]
    print(f"{report['suite']} 맞음 {s['pass']} · 틀림 {s['fail']} · 답 없음 {s['no_answer']} / {s['of']} → {out.name}/regress.json")
    return OK


@command("calls")
def _calls(a: argparse.Namespace) -> int:
    out = require_out_dir(a.out)
    report = l2.summarize(l2.read_calls(a.calls))
    write_json(out / f"calls-{a.label}.json", report)
    (out / f"calls-{a.label}.md").write_text(l2.render_md(a.label, report), "utf-8")
    t = report["total"]
    print(f"L2 {a.label}: 전 {t['before']} · 후 {t['after']} · 줄어듦 {t['reduction']}")
    return OK


@command("fuzzy")
def _fuzzy(a: argparse.Namespace) -> int:
    out = require_out_dir(a.out)
    report = l5.table(read_json(a.driver))
    write_json(out / "fuzzy.json", report)
    (out / "fuzzy.md").write_text(l5.render_md(report, with_names=True), "utf-8")
    (out / "fuzzy-counts.md").write_text(l5.render_md(report, with_names=False), "utf-8")
    print("L5 " + " · ".join(f"{k} {v}" for k, v in report["counts"].items() if v))
    return OK


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except ToolFailure as error:
        print(f"도구 실패: {error}", file=sys.stderr)
        return FAIL
