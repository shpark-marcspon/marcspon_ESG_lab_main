"""`python -m scope3colab run [--input base|distance-transport|distance|rest|base-v22|distance-v22|distance-v22map|filled|base-v22map|merged-v22] [--colab-md …] [--engine-result …] [--engine-result-file …] [--engine-filled-file …] [--out …] [--answers …]`.

`python -m scope3colab compare --colab-result-file … --engine-result … [--colab-filled-file … --template …] [--engine-result-file …]
[--engine-filled-file …] [--out …]` — 코랩을 돌리지 않고 주어진 코랩 결과 파일을 코랩 쪽으로 견준다(S3L3, 아래 `compare_files`).

입력 판(S3C-o3): `base`(기본값 — S3C-o2 입력·답 9개 바이트 그대로), `distance`(거리 시트 넷에만 줄, 계수 책에
`운송`·`출장_통근` 을 더함, 가짜 지오코더가 늘 None), `distance-transport`(그 가운데 C4-1·C9-1 만 — 짝 b2a 몫, 계수 책은
`distance` 와 같음), `rest`(S3C-o4a — C3·C8·C11~C15, 계수 책에 `에너지`·`에너지_C3`·`폐기물_C12` 를 더함, 지오코더는 부르면 멈춤),
v2.2 꼴 `base-v22`·`distance-v22`·`distance-v22map`(바탕 판의 계수 책·답 파일 그대로 — `synthetic_v22.py`), 회색 칸 판 `filled`(S3C-o4b —
템플릿에 배출계수·배출량 열이 있어 코랩이 filled template 의 회색 칸을 채움, 답 0 — `synthetic_filled.py`). `--answers` 를 주지 않으면
그 판의 답 파일을 쓴다. S3C-o5 가 둘을 더했다: `base-v22map`(기본판 + v2.2 매핑 묶음 ㉰ — C5 매핑 폴백 갈래, 답은 기본판 것),
합본판 `merged-v22`(받은 v2.2 차례로 기본·거리·나머지 판 줄을 한 장에, 회색 열 스무 시트, v2.2 ①~⑦ ㉯ — `synthetic_merged.py`,
답은 `answers.merged-v22.synthetic.json`, 거리 줄이 있어 지오코더는 늘 None).

출력 파일 둘의 꼴 대조(S3C-o4b, `outputs.py`): `--engine-result-file`(엔진의 `<base>_result.xlsx`)·`--engine-filled-file`(엔진의
`<base>_filled_template.xlsx`)을 주면 코랩이 낸 같은 이름의 파일과 견준다(filled 는 원본 템플릿 `inputs/` 와 함께). 둘은 따로 줄 수 있다.

종료 코드: 같음 0, 차이 1(줄·칸 대조나 출력 파일 대조 어느 쪽이든), 도구 실패 2(코랩 셀 실패·멈춤 사건·꼴 어긋남). 엔진 쪽을 하나도
주지 않으면 코랩 쪽만 돌고 0.

`<out>/` 에 남는 것(전부 git 밖):
- `colab.json` — 코랩 쪽 견주는 시트(C1~C9·C11~C15) 줄(`rows.py` 꼴)
- `diff.md` — 견줌 결과(엔진 결과가 없으면 그 사실만)
- `outputs.json`·`outputs.md` — 코랩 출력 파일 둘의 꼴(결과 파일 시트·열·줄 수, 템플릿 시트마다 회색 칸 실측)과 엔진 출력 파일을
  받았으면 그 차이(`scope3colab.outputs/1`)
- `run.json` — 입력 판, 걸린 시간, 셀 수, 건너뛴 줄, AI 부름 수, 가짜 부름 기록, 지오코더 물음(거리판)
- `calls.jsonl` — 가짜 AI 부름(프롬프트 첫 줄과 짜 둔 답)
- `colab-stdout.log` — 코랩 `print` 출력(지어낸 입력의 것)
- `inputs/` — 지어낸 계수 책과 템플릿(엔진 쪽에 같은 바이트로 준다), `work/` — 코랩의 작업 폴더(결과 엑셀 둘)
"""

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from scope3colab.cells import CellFormatError, default_colab_md, read_cells
from scope3colab.compare import Diff, compare, render_diff_md
from scope3colab.outputs import OutputDiff, compare_filled_files, compare_result_files, render_outputs_md, to_json
from scope3colab.rows import Row, Rows, RowsFormatError, fields_of, rows_from_json, rows_from_result_xlsx
from scope3colab.sandbox import CellFailed, HarnessAbort, Sandbox, load_answers, run_in_sandbox
from scope3colab.synthetic import build_distance_inputs, build_distance_transport_inputs, build_inputs, build_rest_inputs
from scope3colab.synthetic_filled import build_filled_inputs
from scope3colab.synthetic_merged import build_merged_inputs
from scope3colab.synthetic_v22 import (
    build_base_v22_inputs,
    build_base_v22map_inputs,
    build_distance_v22_inputs,
    build_distance_v22map_inputs,
)

ESG_LAB_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ANSWERS = Path(__file__).resolve().parent / "answers.synthetic.json"
DISTANCE_ANSWERS = Path(__file__).resolve().parent / "answers.distance.synthetic.json"
DISTANCE_TRANSPORT_ANSWERS = Path(__file__).resolve().parent / "answers.distance-transport.synthetic.json"
REST_ANSWERS = Path(__file__).resolve().parent / "answers.rest.synthetic.json"
FILLED_ANSWERS = Path(__file__).resolve().parent / "answers.filled.synthetic.json"
MERGED_ANSWERS = Path(__file__).resolve().parent / "answers.merged-v22.synthetic.json"
# 입력 판 → (입력 짓개, 기본 답 파일, 가짜 지오코더가 None 을 내는가)
INPUTS = {
    "base": (build_inputs, DEFAULT_ANSWERS, False),
    "distance-transport": (build_distance_transport_inputs, DISTANCE_TRANSPORT_ANSWERS, True),  # C4-1·C9-1 만(감독 처분 4)
    "distance": (build_distance_inputs, DISTANCE_ANSWERS, True),
    "rest": (build_rest_inputs, REST_ANSWERS, False),  # C3·C8·C11~C15(S3C-o4a)
    # 템플릿 v2.2 꼴(S3C-o4a, `sid:MARC-ISSUE-274`) — 바탕 판의 계수 책·답 파일 그대로
    "base-v22": (build_base_v22_inputs, DEFAULT_ANSWERS, False),
    "distance-v22": (build_distance_v22_inputs, DISTANCE_ANSWERS, True),
    "distance-v22map": (build_distance_v22map_inputs, DISTANCE_ANSWERS, True),  # 매핑 묶음, 저장 값 없음(㉰)
    "filled": (build_filled_inputs, FILLED_ANSWERS, False),  # 회색 칸 판(S3C-o4b) — 답 0
    "base-v22map": (build_base_v22map_inputs, DEFAULT_ANSWERS, False),  # C5 매핑 폴백(S3C-o5) — 매핑 묶음, 저장 값 없음(㉰)
    "merged-v22": (build_merged_inputs, MERGED_ANSWERS, True),  # 합본판(S3C-o5) — 거리 줄이 있음
}
SAME, DIFFERENT, TOOL_FAILED = 0, 1, 2


def _default_out() -> Path:
    return ESG_LAB_ROOT / "out" / "scope3colab" / datetime.now().strftime("%Y%m%d-%H%M%S")


def _fail(message: str) -> int:
    print(f"도구 실패: {message}", file=sys.stderr)
    return TOOL_FAILED


def run(colab_md: Path, engine_result: Path | None, out: Path, answers_path: Path | None = None, input_kind: str = "base",
        engine_result_file: Path | None = None, engine_filled_file: Path | None = None) -> int:  # fmt: skip
    out = out.resolve()  # 코랩은 작업 폴더(`<out>/work`)로 옮겨 돈다 — 상대 경로가 그 안에서 풀리지 않게
    out.mkdir(parents=True, exist_ok=True)
    try:
        cells = read_cells(colab_md)
    except (OSError, CellFormatError) as error:
        return _fail(f"읽기판을 못 읽었다 — {error}")
    build, default_answers, geocode_none = INPUTS[input_kind]
    answers_path = answers_path or default_answers
    try:
        answers = load_answers(answers_path)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return _fail(f"짜 둔 답을 못 읽었다 — {error!r}")
    inputs = build(out / "inputs")
    box = Sandbox(answers, inputs.template, geocode_none=geocode_none)
    started = time.monotonic()
    failure: str | None = None
    namespace: dict[str, object] = {}
    with (out / "colab-stdout.log").open("w", encoding="utf-8") as log:
        try:
            namespace = run_in_sandbox(cells, box, out / "work", {"base_path": str(inputs.factor_book.parent)}, log)
        except CellFailed as error:
            failure = f"건너뛰기 목록 밖의 셀 실패 — {error}"
        except HarnessAbort as error:
            failure = f"{type(error).__name__}: {error}"
    seconds = round(time.monotonic() - started, 2)
    skipped = [{"cell": s.cell, "line": s.line, "rule": s.rule, "text": s.text} for c in cells for s in c.skipped]
    meta = {
        "colab_md": str(colab_md),
        "seconds": seconds,
        "cells": len(cells),
        "skipped_lines": skipped,
        "ai_calls": len(box.calls),
        "answers": len(box.answers),
        "fake_events": box.events,
        "aborts": box.aborts,
    }
    if input_kind != "base":  # 기본판 run.json 꼴은 S3C-o2 그대로 둔다
        meta = {"input": input_kind, **meta, "geocode_queries": box.geocode_queries}
    (out / "calls.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in box.calls), "utf-8")
    (out / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", "utf-8")
    if failure is None and box.aborts:
        failure = f"코랩이 삼킨 멈춤 사건 {len(box.aborts)} — 첫째: {box.aborts[0]}"
    if failure is None and len(box.calls) != len(box.answers):
        failure = f"짜 둔 답 {len(box.answers)} 가운데 {len(box.calls)} 만 쓰였다(부름 차례가 바뀌었다)"
    if failure is not None:
        return _fail(failure)

    result = namespace.get("result")
    if not isinstance(result, dict) or "result_file" not in result:
        return _fail("코랩 실행 셀이 `result[\"result_file\"]` 를 남기지 않았다")
    try:
        colab = rows_from_result_xlsx(out / "work" / str(result["result_file"]))
    except (OSError, RowsFormatError) as error:
        return _fail(f"코랩 결과를 못 읽었다 — {error}")
    (out / "colab.json").write_text(json.dumps(colab.to_json(), ensure_ascii=False, indent=2) + "\n", "utf-8")
    try:
        output_diffs = _outputs(out, inputs.template, result, engine_result_file, engine_filled_file)
    except (OSError, KeyError, ValueError) as error:  # openpyxl 이 못 여는 파일(zip 아님 등)은 ValueError 갈래
        return _fail(f"출력 파일을 못 읽었다 — {error!r}")

    header = {
        "읽기판": colab_md.name,
        "걸린 시간": f"{seconds}s",
        "건너뛴 줄": f"{len(skipped)} ({', '.join(f'셀 {s['cell']} {s['rule']}' for s in skipped)})",
        "가짜 AI 부름": str(len(box.calls)),
        "입력": f"{inputs.template.name}, {inputs.factor_book.name}(synthetic)",
    }
    if input_kind != "base":
        header["입력 판"] = input_kind
        header["가짜 지오코더 물음"] = f"{len(box.geocode_queries)}(늘 None)"
    if engine_result is None:
        (out / "diff.md").write_text(render_diff_md(colab, None, [], header), "utf-8")
        print(f"코랩 쪽 {len(colab.rows)}줄" + _outputs_line(output_diffs) + f" → {out}")
        return DIFFERENT if output_diffs else SAME
    try:
        engine = rows_from_json(json.loads(engine_result.read_text("utf-8")), "engine")
    except (OSError, ValueError) as error:
        return _fail(f"엔진 결과를 못 읽었다 — {error}")
    header["엔진 결과"] = str(engine_result)
    diffs = compare(colab, engine)
    (out / "diff.md").write_text(render_diff_md(colab, engine, diffs, header), "utf-8")
    print(f"코랩 {len(colab.rows)}줄 · 엔진 {len(engine.rows)}줄 · 차이 {len(diffs)}" + _outputs_line(output_diffs) + f" → {out}")
    return DIFFERENT if diffs or output_diffs else SAME


def _outputs(out: Path, template: Path, result: dict[str, object], engine_result_file: Path | None,
             engine_filled_file: Path | None) -> list[OutputDiff] | None:  # fmt: skip
    """`outputs.json`·`outputs.md` 를 쓰고 출력 파일 차이를 돌려준다(엔진 파일을 하나도 받지 않았으면 None).

    코랩 `run_scope3_pipeline` 은 늘 두 파일을 돌려준다. `filled_template_file` 이 없는 것은 시험의 가짜 읽기판뿐이라 그때는 filled 실측을
    null 로 두고, 엔진 filled 파일을 받았으면 견줄 것이 없으니 실패(KeyError)다."""
    colab_result = out / "work" / str(result["result_file"])
    colab_filled = out / "work" / str(result["filled_template_file"]) if "filled_template_file" in result else None
    diffs: list[OutputDiff] | None = None
    engine: dict[str, str] = {}
    if engine_result_file is not None or engine_filled_file is not None:
        diffs = []
        if engine_result_file is not None:
            diffs += compare_result_files(colab_result, engine_result_file)
            engine["결과 파일"] = str(engine_result_file)
        if engine_filled_file is not None:
            if colab_filled is None:
                raise KeyError("코랩이 `result[\"filled_template_file\"]` 를 남기지 않았다")
            diffs += compare_filled_files(template, colab_filled, engine_filled_file)
            engine["filled template"] = str(engine_filled_file)
    data = to_json(colab_result, template, colab_filled, diffs)
    (out / "outputs.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
    (out / "outputs.md").write_text(render_outputs_md(data, engine), "utf-8")
    return diffs


def _outputs_line(diffs: list[OutputDiff] | None) -> str:
    return "" if diffs is None else f" · 출력 파일 차이 {len(diffs)}"


# ── compare: 받은 코랩 결과 파일을 코랩 쪽으로(S3L3, `sid:MARC-ENT-537`) ─────────────────────────────────────────────
# 코랩을 다시 돌리지 않고 이미 코랩이 낸 결과 파일을 코랩 쪽 줄로 읽는다. 견줌 규칙은 `run` 과 같다(`Decimal` 같음·오차 0).
# 차이 줄마다 그 줄의 「매핑 칸」 을 두 쪽 나란히 실은 `diff.json` 을 더 낸다(원인 가르기는 git 밖 몫).
MAPPING_FIELDS: dict[str, tuple[str, ...]] = {
    "C3": ("EF매핑명", "EF단위"),
    "C13": ("EF매핑명", "EF단위"),
    "C5": ("처리방법_정규화", "바이오매스 구분"),
    "C6": ("정규화이동수단", "이동거리(km)", "거리왕복계수"),
    "C7": ("정규화이동수단", "평균통근거리(km)", "거리왕복계수"),
}
DIFF_SCHEMA = "scope3colab.diff/1"


def file_fingerprint(path: Path) -> dict[str, object]:
    """입력 파일은 sha256 과 바이트 수만 싣는다 — 경로·파일 이름 글자는 싣지 않는다(받은 파일 이름에 기업명이 있다)."""
    body = path.read_bytes()
    return {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}


def mapping_of(row: Row | None) -> dict[str, str | None] | None:
    """그 줄의 매핑 칸(그 시트·갈래의 견주는 칸 가운데 `MAPPING_FIELDS` 에 든 것). 줄이 없으면 None."""
    if row is None:
        return None
    names = {name for name, _, _ in fields_of(row.sheet, row.branch)}
    return {name: row.fields.get(name) for name in MAPPING_FIELDS.get(row.sheet, ()) if name in names}


def diff_json(colab: Rows, engine: Rows, diffs: list[Diff]) -> dict[str, object]:
    left = {r.key: r for r in colab.rows}
    right = {r.key: r for r in engine.rows}
    items = []
    for d in diffs:
        sheet, row, item = d.key
        a, b = left.get(d.key), right.get(d.key)
        items.append({
            "sheet": sheet, "row": row, "item": item, "field": d.field, "colab": d.colab, "engine": d.engine,
            "branch": {"colab": a.branch if a else None, "engine": b.branch if b else None},
            "mapping": {"colab": mapping_of(a), "engine": mapping_of(b)},
        })  # fmt: skip
    return {"schema": DIFF_SCHEMA, "diffs": items}


def compare_files(colab_result: Path, engine_result: Path, out: Path, colab_filled: Path | None = None,
                  template: Path | None = None, engine_result_file: Path | None = None,
                  engine_filled_file: Path | None = None) -> int:  # fmt: skip
    """주어진 코랩 결과 파일(과 채운 템플릿) ↔ 엔진 쪽. 종료 코드·산출 꼴은 `run` 과 같다(같음 0·차이 1·도구 실패 2)."""
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if engine_filled_file is not None and (colab_filled is None or template is None):
        return _fail("`--engine-filled-file` 은 `--colab-filled-file` 과 `--template` 이 함께 있어야 한다")
    if (colab_filled is None) != (template is None):
        return _fail("`--colab-filled-file` 과 `--template` 은 함께 준다")
    try:
        colab = rows_from_result_xlsx(colab_result)
        engine = rows_from_json(json.loads(engine_result.read_text("utf-8")), "engine")
    except (OSError, ValueError, KeyError) as error:  # RowsFormatError 는 ValueError, openpyxl 이 못 여는 파일도 ValueError 갈래
        return _fail(f"줄을 못 읽었다 — {error!r}")
    diffs = compare(colab, engine)
    try:
        output_diffs: list[OutputDiff] | None = None
        if engine_result_file is not None or engine_filled_file is not None:
            output_diffs = []
            if engine_result_file is not None:
                output_diffs += compare_result_files(colab_result, engine_result_file)
            if engine_filled_file is not None and colab_filled is not None and template is not None:
                output_diffs += compare_filled_files(template, colab_filled, engine_filled_file)
        data = to_json(colab_result, template or colab_result, colab_filled if template is not None else None, output_diffs)
    except (OSError, KeyError, ValueError) as error:
        return _fail(f"출력 파일을 못 읽었다 — {error!r}")
    given = {"코랩 결과 파일": colab_result, "엔진 결과": engine_result, "코랩 채운 템플릿": colab_filled,
             "원본 템플릿": template, "엔진 결과 파일": engine_result_file, "엔진 채운 템플릿": engine_filled_file}  # fmt: skip
    inputs = {name: file_fingerprint(path) for name, path in given.items() if path is not None}
    header = {"코랩 쪽": "주어진 결과 파일(코랩을 돌리지 않음)"}
    header |= {name: f"sha256 {fp['sha256']}"[:23] for name, fp in inputs.items()}
    (out / "colab.json").write_text(json.dumps(colab.to_json(), ensure_ascii=False, indent=2) + "\n", "utf-8")
    (out / "diff.md").write_text(render_diff_md(colab, engine, diffs, header), "utf-8")
    (out / "diff.json").write_text(json.dumps(diff_json(colab, engine, diffs), ensure_ascii=False, indent=2) + "\n", "utf-8")
    (out / "outputs.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
    (out / "outputs.md").write_text(render_outputs_md(data, {}), "utf-8")
    code = DIFFERENT if diffs or output_diffs else SAME
    meta = {"command": "compare", "inputs": inputs, "colab_rows": len(colab.rows), "engine_rows": len(engine.rows),
            "diffs": len(diffs), "output_diffs": None if output_diffs is None else len(output_diffs), "exit": code}  # fmt: skip
    (out / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"코랩 {len(colab.rows)}줄 · 엔진 {len(engine.rows)}줄 · 차이 {len(diffs)}" + _outputs_line(output_diffs) + f" → {out}")
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scope3colab", description="Scope 3 코랩 대조 도구(C1~C9·C11~C15)")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run", help="코랩 쪽을 돌리고(엔진 결과가 있으면) 견준다")
    p.add_argument("--colab-md", type=Path, default=None, help="읽기판 md(기본: received-derived 의 생물기원 적용판)")
    p.add_argument("--engine-result", type=Path, default=None, help="엔진 쪽 JSON(scope3colab.rows/1)")
    p.add_argument("--engine-result-file", type=Path, default=None, help="엔진의 결과 파일 `<base>_result.xlsx`(꼴 대조)")
    p.add_argument("--engine-filled-file", type=Path, default=None, help="엔진의 `<base>_filled_template.xlsx`(회색 칸 대조)")
    p.add_argument("--out", type=Path, default=None, help="산출 폴더(기본: esg-lab/out/scope3colab/<날짜시각>)")
    p.add_argument("--input", choices=tuple(INPUTS), default="base", help="입력 판(기본: base — S3C-o2 그대로)")
    p.add_argument("--answers", type=Path, default=None, help="짜 둔 가짜 AI 답(synthetic, 기본: 그 판의 답 파일)")
    c = sub.add_parser("compare", help="주어진 코랩 결과 파일(코랩을 돌리지 않음)을 엔진 쪽과 견준다(S3L3)")
    c.add_argument("--colab-result-file", type=Path, required=True, help="코랩이 낸 `<base>_result.xlsx`")
    c.add_argument("--colab-filled-file", type=Path, default=None, help="코랩이 낸 `<base>_filled_template.xlsx`(회색 칸 대조)")
    c.add_argument("--template", type=Path, default=None, help="원본 템플릿(`--colab-filled-file` 과 함께)")
    c.add_argument("--engine-result", type=Path, required=True, help="엔진 쪽 JSON(scope3colab.rows/1)")
    c.add_argument("--engine-result-file", type=Path, default=None, help="엔진의 결과 파일(꼴 대조)")
    c.add_argument("--engine-filled-file", type=Path, default=None, help="엔진의 채운 템플릿(회색 칸 대조)")
    c.add_argument("--out", type=Path, default=None, help="산출 폴더(기본: esg-lab/out/scope3colab/<날짜시각>)")
    args = parser.parse_args(argv)
    if args.command == "compare":
        return compare_files(args.colab_result_file, args.engine_result, args.out or _default_out(), args.colab_filled_file,
                             args.template, args.engine_result_file, args.engine_filled_file)  # fmt: skip
    try:
        colab_md = args.colab_md or default_colab_md()
    except FileNotFoundError as error:
        return _fail(str(error))
    return run(colab_md, args.engine_result, args.out or _default_out(), args.answers, args.input, args.engine_result_file,
               args.engine_filled_file)  # fmt: skip
