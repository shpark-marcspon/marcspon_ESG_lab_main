"""`python -m dvcolab run [--colab-md …] [--engine-result …] [--cases-out …] [--out …] [--random N --seed S]`.

종료 코드: 같음 0, 모르는 차이 1, 도구 실패 2(읽기판 꼴, 셀 실패, 멈춤 사건, 하네스 단언, 출력의 금지 낱말).
`--engine-result` 가 없으면 코랩 쪽만 돌고 사례 JSON 을 낸다(0) — 그것을 esg-app 의
`scripts/dataverify_compare_dump.py --cases` 에 넘겨 엔진 결과를 받는다.

`<out>/` 에 남는 것(전부 git 밖):
- `cases.json` — 사례(`dvcolab.cases/1`, `--cases-out` 이 있으면 거기에도)
- `colab.json` — 코랩 쪽 결과(`dvcolab.result/1`). 시각 칸이 없어 같은 seed 면 바이트가 같다
- `diff.md` — 견줌 결과(알려진 n · 모르는 m)
- `run.json` — 걸린 시간, 셀, 건너뛴 줄, 하네스 조작, 가짜 부름, seed, 금지 낱말 검사
- `colab-stdout.log` — 코랩 `print` 출력(지은 입력의 것)
- `work/` — 코랩의 작업 폴더(셀 4 가 쓰는 JSON)
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from dvcolab import cases as cases_mod
from dvcolab.cells import NOT_RUN, RUN, CellFormatError, default_colab_md, pick, read_cells
from dvcolab.compare import ResultFormatError, check_result, compare, known_table, render_md
from dvcolab.harness import HarnessError, run_colab
from dvcolab.sandbox import CellFailed, HarnessAbort, Sandbox, load_answers, sandboxed

ESG_LAB_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ANSWERS = Path(__file__).resolve().parent / "answers.synthetic.json"
SAME, DIFFERENT, TOOL_FAILED = 0, 1, 2


def _default_out() -> Path:
    return ESG_LAB_ROOT / "out" / "dvcolab" / datetime.now().strftime("%Y%m%d-%H%M%S")


def default_forbidden() -> Path | None:
    """금지 낱말 목록(실재 기업명 — 저장소 밖 작업 폴더의 것). 없으면 None."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "esg-app.worktrees" / "G1a.out" / "forbidden-words.txt"
        if candidate.is_file():
            return candidate
    return None


def forbidden_hits(paths: list[Path], words_file: Path) -> dict[str, int]:
    """파일마다 금지 낱말이 걸린 줄 수. 낱말은 찍지 않는다."""
    words = [w.strip() for w in words_file.read_text(encoding="utf-8").splitlines() if w.strip()]
    hits: dict[str, int] = {}
    for path in paths:
        lines = path.read_text(encoding="utf-8").splitlines()
        hits[path.name] = sum(1 for line in lines if any(w in line for w in words))
    return hits


def _fail(message: str) -> int:
    print(f"도구 실패: {message}", file=sys.stderr)
    return TOOL_FAILED


def _dump(path: Path, doc: Any) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def run(
    colab_md: Path,
    engine_result: Path | None,
    out: Path,
    answers_path: Path,
    random_n: int,
    seed: int | None,
    cases_out: Path | None,
    forbidden: Path | None,
) -> int:
    out = out.resolve()  # 코랩은 작업 폴더(`<out>/work`)로 옮겨 돈다
    out.mkdir(parents=True, exist_ok=True)
    try:
        all_cells = read_cells(colab_md)
        cells = pick(all_cells)
    except (OSError, CellFormatError) as error:
        return _fail(f"읽기판을 못 읽었다 — {error}")
    try:
        answers = load_answers(answers_path)
        cases = cases_mod.build(random_n, seed)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return _fail(f"사례·짜 둔 답을 못 지었다 — {error!r}")
    _dump(out / "cases.json", cases)
    if cases_out is not None:
        _dump(cases_out, cases)
    cases = json.loads((out / "cases.json").read_text(encoding="utf-8"))  # 엔진 쪽과 같은 JSON 을 읽는다
    box = Sandbox(answers)
    started = time.monotonic()
    failure: str | None = None
    colab: dict[str, Any] = {}
    record: dict[str, Any] = {}
    with (out / "colab-stdout.log").open("w", encoding="utf-8") as log:
        try:
            with sandboxed(box, out / "work"):
                colab, record = run_colab(cells, cases, box, log)
        except CellFailed as error:
            failure = f"건너뛰기 목록 밖의 셀 실패 — {error}"
        except HarnessError as error:
            failure = f"하네스 단언 — {error}"
        except HarnessAbort as error:
            failure = f"{type(error).__name__}: {error}"
    seconds = round(time.monotonic() - started, 2)
    if failure is None and box.aborts:
        failure = f"코랩이 삼킨 멈춤 사건 {len(box.aborts)} — 첫째: {box.aborts[0]}"
    if failure is None and len(box.posts) != len(box.answers):
        failure = f"짜 둔 답 {len(box.answers)} 가운데 {len(box.posts)} 만 쓰였다"
    meta: dict[str, Any] = {
        "colab_md": str(colab_md),
        "seconds": seconds,
        "code_cells": [c.number for c in all_cells],
        "run_cells": RUN,
        "not_run_cells": NOT_RUN,
        "skipped_lines": [{"cell": s.cell, "line": s.line, "rule": s.rule} for c in cells for s in c.skipped],
        "seed": seed,
        "random": random_n,
        "calls": len(cases["calls"]),
        "loops": len(cases.get("loops", [])),
        "aborts": box.aborts,
        **record,
    }
    if failure is not None:
        meta["failure"] = failure
        _dump(out / "run.json", meta)
        return _fail(failure)
    _dump(out / "colab.json", colab)

    report = None
    exit_code = SAME
    header = {
        "읽기판": colab_md.name,
        "걸린 시간": f"{seconds}s",
        "seed": str(seed),
        "건너뛴 줄": str(len(meta["skipped_lines"])),
    }
    if engine_result is None:
        (out / "diff.md").write_text("# 데이터 검증 코랩 대조\n\n엔진 결과 없음 — 코랩 쪽만 냈다(`colab.json`).\n", "utf-8")
        print(f"코랩 쪽 사례 {len(colab['calls'])} · 프롬프트 {len(colab['prompts'])} → {out}")
    else:
        try:
            engine = check_result(json.loads(engine_result.read_text("utf-8")), "engine")
        except (OSError, ValueError) as error:
            return _fail(f"엔진 결과를 못 읽었다 — {error}")
        except ResultFormatError as error:
            return _fail(str(error))
        header["엔진 결과"] = str(engine_result)
        report = compare(colab, engine)
        (out / "diff.md").write_text(render_md(report, header), "utf-8")
        meta["known"] = [{"what": w, "dv1a": r, "count": n} for w, r, n in known_table(report)]
        meta["unknown"] = report.unknown
        exit_code = DIFFERENT if report.unknown else SAME
        print(f"사례 {report.calls} · 고리 {report.loops} · 알려진 차이 {len(report.known)} · 모르는 차이 {report.unknown} → {out}")

    if forbidden is None:
        meta["forbidden_words"] = "안 봄(목록 없음 — --forbidden 으로 준다)"
    else:
        hits = forbidden_hits([out / "colab.json", out / "diff.md", out / "colab-stdout.log"], forbidden)
        meta["forbidden_words"] = hits
        if any(hits.values()):
            _dump(out / "run.json", meta)
            return _fail(f"출력에 금지 낱말 — {hits}")
    _dump(out / "run.json", meta)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dvcolab", description="데이터 검증 코랩 대조 도구")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run", help="코랩 쪽을 돌리고(엔진 결과가 있으면) 견준다")
    p.add_argument("--colab-md", type=Path, default=None, help="읽기판 md(기본: received-derived 의 데이터 검증 코랩)")
    p.add_argument("--engine-result", type=Path, default=None, help="엔진 쪽 JSON(dvcolab.result/1)")
    p.add_argument("--cases-out", type=Path, default=None, help="사례 JSON 을 여기에도 쓴다")
    p.add_argument("--out", type=Path, default=None, help="산출 폴더(기본: esg-lab/out/dvcolab/<날짜시각>)")
    p.add_argument("--answers", type=Path, default=DEFAULT_ANSWERS, help="짜 둔 가짜 AI 답(synthetic)")
    p.add_argument("--random", type=int, default=0, help="무작위 사례 수(--seed 와 함께)")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--forbidden", type=Path, default=None, help="금지 낱말 목록(기본: 작업 폴더의 G1a.out 목록)")
    args = parser.parse_args(argv)
    try:
        colab_md = args.colab_md or default_colab_md()
    except FileNotFoundError as error:
        return _fail(str(error))
    return run(
        colab_md,
        args.engine_result,
        args.out or _default_out(),
        args.answers,
        args.random,
        args.seed,
        args.cases_out,
        args.forbidden or default_forbidden(),
    )
