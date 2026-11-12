"""`python -m evalrig.dataverify <명령>` — `build-exam`(시험지 셋 + 정답) · `convert` · `score`(1차 결과 채점). 실비 0.

py/ 에서:
  uv run --frozen python -m evalrig.dataverify build-exam --out <폴더> [--xlsx <경로>] [--pdf <경로>]
  uv run --frozen python -m evalrig.dataverify convert <result.json> --input <input.json> [--out <폴더>]
  uv run --frozen python -m evalrig.dataverify score <정답> <result.json> --input <input.json> [--out <폴더>]
  uv run --frozen python -m evalrig.dataverify xsrc <xlsx> --out <out/MB2/…json> [--cells <json>]
  uv run --frozen python -m evalrig.dataverify xcopy <document.json> --facts <facts.json> --out <out/MB2/…>
  uv run --frozen python -m evalrig.dataverify measure jitter|arms|risk --runs <폴더,…> --input <input.json> --gold <정답> [--out <폴더>]
  uv run --frozen python -m evalrig.dataverify score-recon <정답> --cells <json> --verdicts <json> --stage d2|r|h2 [--out <폴더>]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from evalrig.dataverify import gold as G
from evalrig.dataverify import risk, synth, xpdf

REPO = Path(__file__).resolve().parents[3]
KB_GOLD = REPO / "data/reconcile/gold/kb-2025.gold.json"
CATALOG = REPO / "data/reconcile/indicators.synthetic.json"
DATA_DIR = REPO / "data/evalrig/dataverify"
XLSX = REPO / "out/evb/xpdf/kb-2025.source.xlsx"
PDF_NAME = "prework/reports/kb_2025_kor.pdf"


def default_pdf() -> Path | None:
    """main 체크아웃(esg-lab/)과 워크트리(esg-lab.worktrees/<밴드>/) 둘 다에서 marcspon/prework 를 찾는다."""
    for base in (REPO.parent, REPO.parent.parent):
        p = base / PDF_NAME
        if p.exists():
            return p
    return None


def build_all(kb_gold_path: Path = KB_GOLD, catalog_path: Path = CATALOG) -> tuple[dict[str, str], list[dict]]:
    """{파일 이름: 글} 여섯과 엑셀 원천 행."""
    files: dict[str, str] = {}
    inp, gs = synth.build()
    files["synthetic-company.input.json"] = G.dumps_input(inp)
    files["synthetic-company.gold.json"] = G.dumps_gold(gs)
    inp, gs = risk.build()
    files["synthetic-risk.input.json"] = G.dumps_input(inp)
    files["synthetic-risk.gold.json"] = G.dumps_gold(gs)
    kb = json.loads(kb_gold_path.read_text(encoding="utf-8"))
    cat = json.loads(catalog_path.read_text(encoding="utf-8"))
    rows, plants, gs = xpdf.build(kb, cat, kb_gold_sha16=G.file_sha16(kb_gold_path), catalog_sha16=G.file_sha16(catalog_path))
    files["kb-2025-xpdf.plants.json"] = G.dumps_gold(plants)
    files["kb-2025-xpdf.gold.json"] = G.dumps_gold(gs)
    return files, rows


def cmd_build_exam(a: argparse.Namespace) -> int:
    kb = json.loads(Path(a.kb_gold).read_text(encoding="utf-8"))
    pdf = Path(a.pdf) if a.pdf else default_pdf()
    if pdf is None or not pdf.exists():
        print(f"PDF 없음: {a.pdf or PDF_NAME}", file=sys.stderr)
        return 2
    want = kb["documents"][xpdf.DOC]["pdfSha256"]
    got = xpdf.pdf_sha256(pdf)
    if got != want:
        print(f"PDF sha256 다름: {got[:12]} ≠ gold {want[:12]}", file=sys.stderr)
        return 3
    files, rows = build_all(Path(a.kb_gold), Path(a.catalog))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")
    xpdf.write_xlsx(rows, a.xlsx)
    back = xpdf.read_xlsx(a.xlsx)
    if xpdf.canonical_rows(back) != xpdf.canonical_rows(rows):
        print("엑셀 다시 읽기 ≠ 원천", file=sys.stderr)
        return 4
    print(json.dumps({"out": str(out), "files": sorted(files), "xlsx": str(a.xlsx), "xlsxRows": len(rows),
                      "pdfSha12": got[:12]}, ensure_ascii=False))
    return 0


def _convs(a):
    from evalrig.dataverify import convert_156

    doc = json.loads(Path(a.result).read_text(encoding="utf-8"))
    inp = json.loads(Path(a.input).read_text(encoding="utf-8"))
    return convert_156.convert_doc(doc, inp, run=Path(a.result).parent.name)


def _emit(a, name, obj) -> int:
    import hashlib

    obj = {"input": {"result": Path(a.result).name, "resultSha256": hashlib.sha256(Path(a.result).read_bytes()).hexdigest(),
                     "inputSha256": hashlib.sha256(Path(a.input).read_bytes()).hexdigest()}, **obj}
    text = json.dumps(obj, ensure_ascii=False, indent=1) + "\n"
    if a.out:
        Path(a.out).mkdir(parents=True, exist_ok=True)
        (Path(a.out) / name).write_text(text, encoding="utf-8")
        print(json.dumps({"out": str(Path(a.out) / name)}, ensure_ascii=False))
    else:
        print(text, end="")
    return 0


def cmd_convert(a) -> int:
    from evalrig.dataverify import convert_156

    return _emit(a, "convert.json", {"results": [convert_156.summary(c) for c in _convs(a)]})


def cmd_score(a) -> int:
    from evalrig.common import gold as common_gold
    from evalrig.dataverify import score

    gs = common_gold.check(json.loads(Path(a.gold).read_text(encoding="utf-8")))
    return _emit(a, "score.json", {"gold": common_gold.header(gs), "scores": [score.score(gs, c) for c in _convs(a)]})


def _under_mb2(path: str | Path) -> Path:
    """랩 산출(보고서 머리글 글자를 품을 수 있음)은 git 밖 `out/MB2/` 아래에만(발주서 MB2 §1-1)."""
    p = Path(path).resolve()
    if "/out/MB2/" not in f"{p}/":
        raise SystemExit(f"out/MB2/ 밖에는 쓰지 않는다: {p}")
    return p


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def cmd_xsrc(a) -> int:
    from evalrig.dataverify import xsrc

    out = _under_mb2(a.out)
    rows = xpdf.read_xlsx(a.xlsx)
    if a.unplant:
        rows = xsrc.unplant(rows, json.loads(Path(a.unplant).read_text(encoding="utf-8")))
    _write_json(out, xsrc.to_docmodel(rows, file_name=Path(a.xlsx).name))
    cells = _under_mb2(a.cells) if a.cells else out.with_name(out.stem + ".cells.json")
    _write_json(cells, {k: list(v) for k, v in xsrc.cell_map(rows).items()})
    print(json.dumps({"out": str(out), "cells": str(cells), "rows": len(rows)}, ensure_ascii=False))
    return 0


def cmd_xcopy(a) -> int:
    from evalrig.common import gold as common_gold
    from evalrig.dataverify import xcopy

    out = _under_mb2(a.out)
    doc = json.loads(Path(a.document).read_text(encoding="utf-8"))
    facts = json.loads(Path(a.facts).read_text(encoding="utf-8"))
    rows, plants, gs, picked = xcopy.build(doc, facts, seed=a.seed)
    common_gold.check(gs)
    _write_json(out / "gold.json", gs)
    _write_json(out / "plants.json", plants)
    _write_json(out / "picked.json", picked)
    xpdf.write_xlsx(rows, out / "source.xlsx")
    if xpdf.canonical_rows(xpdf.read_xlsx(out / "source.xlsx")) != xpdf.canonical_rows(rows):
        print("엑셀 다시 읽기 ≠ 원천", file=sys.stderr)
        return 4
    print(json.dumps({"out": str(out), "rows": len(rows), "items": len(gs["items"]), "cellsUsed": gs["meta"]["cellsUsed"],
                      "setId": gs["setId"]}, ensure_ascii=False))
    return 0


def cmd_score_recon(a) -> int:
    from evalrig.common import gold as common_gold
    from evalrig.dataverify import recon

    gs = common_gold.check(json.loads(Path(a.gold).read_text(encoding="utf-8")))
    cells = json.loads(Path(a.cells).read_text(encoding="utf-8"))
    run = json.loads(Path(a.verdicts).read_text(encoding="utf-8"))
    try:
        verdicts = recon.verdicts_of(run, a.stage)
        s = recon.score(gs, cells, verdicts)
        rows = recon.branches(gs, cells, verdicts)
    except recon.ReconError as e:
        print(f"score-recon: {e}", file=sys.stderr)
        return 5
    obj = {"gold": common_gold.header(gs), "stage": a.stage, "verdictsFile": str(a.verdicts), "score": s}
    if a.out:
        out = _under_mb2(Path(a.out) / "x").parent
        _write_json(out / f"recon-{a.stage}.json", obj)
        _write_json(out / f"recon-{a.stage}.branches.json", rows)
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    return 0


def _load(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _conv_dir(d: Path, inp: dict) -> dict:
    from evalrig.dataverify import convert_156

    return convert_156.convert(_load(d / "result.json"), inp, run=d.name)


def cmd_measure(a) -> int:
    from evalrig.common import gold as common_gold
    from evalrig.dataverify import measure_vm, riskrules

    gs = common_gold.check(_load(a.gold))
    inp = _load(a.input)
    dirs = [Path(x) for x in a.runs.split(",") if x] if a.runs else []
    if a.kind == "jitter":
        obj = measure_vm.jitter([{"conv": _conv_dir(d, inp), "result": _load(d / "result.json")} for d in dirs], gs)
    elif a.kind == "arms":
        arms = {}
        for d in dirs:
            side = d / "labfix.jsonl"
            v1r = d / "v1-ratio-on-intensity.json"
            arms[d.name] = {"conv": _conv_dir(d, inp), "cond": _load(d / "cond.json"),
                            "side": [json.loads(x) for x in side.read_text(encoding="utf-8").splitlines() if x.strip()]
                            if side.exists() else None,
                            "v1RatioOnIntensity": _load(v1r) if v1r.exists() else None}
        obj = measure_vm.arms_table(arms, gs, set(a.intensity_ids.split(",")) if a.intensity_ids else set())
    else:  # risk
        env = {r["ID"] for r in inp["data"].get("환경", [])}
        obj = {"A": [{"run": d.name, **measure_vm.risk_table(gs, _conv_dir(d, inp), env)} for d in dirs],
               "B0": {f"{t:.2f}": measure_vm.risk_table(gs, riskrules.as_conv(riskrules.b0_flags(riskrules.candidates(inp, t))), env)
                      for t in riskrules.SENSITIVITY},
               "B": [{"answer": Path(f).name, **measure_vm.risk_table(gs, riskrules.as_conv(_load(f)["flagged"]), env)}
                     for f in (a.b.split(",") if a.b else [])]}
    text = json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True, default=str) + "\n"
    if a.out:
        out = _under_mb2(Path(a.out) / "x").parent
        out.mkdir(parents=True, exist_ok=True)
        (out / f"measure-{a.kind}.json").write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m evalrig.dataverify")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build-exam", help="시험지 셋 + 정답(가상 기업·리스크 짝·엑셀↔PDF)을 짓는다")
    b.add_argument("--out", required=True)
    b.add_argument("--xlsx", default=str(XLSX))
    b.add_argument("--pdf", default=None)
    b.add_argument("--kb-gold", default=str(KB_GOLD))
    b.add_argument("--catalog", default=str(CATALOG))
    b.set_defaults(fn=cmd_build_exam)
    c = sub.add_parser("convert", help="1차 result@2 → 채점 단위")
    c.add_argument("result")
    c.add_argument("--input", required=True)
    c.add_argument("--out", default=None)
    c.set_defaults(fn=cmd_convert)
    c = sub.add_parser("score", help="정답 × 1차 result@2")
    c.add_argument("gold")
    c.add_argument("result")
    c.add_argument("--input", required=True)
    c.add_argument("--out", default=None)
    c.set_defaults(fn=cmd_score)
    c = sub.add_parser("xsrc", help="엑셀 원천 → 랩 문서 모델(+ cell_map), out/MB2/ 아래에만")
    c.add_argument("xlsx")
    c.add_argument("--out", required=True)
    c.add_argument("--cells", default=None)
    c.add_argument("--unplant", default=None, help="심은 목록 — 심기 전 원본 문서 모델을 짓는다(cli7 --a)")
    c.set_defaults(fn=cmd_xsrc)
    c = sub.add_parser("xcopy", help="보고서 문서 모델 + facts → 다른 회사 시험지(원천 엑셀·정답·심은 목록), out/MB2/ 아래에만")
    c.add_argument("document")
    c.add_argument("--facts", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--seed", type=int, default=20261111)
    c.set_defaults(fn=cmd_xcopy)
    c = sub.add_parser("score-recon", help="엑셀↔PDF 정답 × 랩 대사 verdicts(d2·r·h2)")
    c.add_argument("gold")
    c.add_argument("--cells", required=True)
    c.add_argument("--verdicts", required=True)
    c.add_argument("--stage", required=True, choices=["d2", "r", "h2"])
    c.add_argument("--out", default=None)
    c.set_defaults(fn=cmd_score_recon)
    c = sub.add_parser("measure", help="1차 실행 흔들림(jitter)·판 견줌(arms)·리스크(risk) — 실행 폴더의 result.json")
    c.add_argument("kind", choices=["jitter", "arms", "risk"])
    c.add_argument("--runs", default="")
    c.add_argument("--input", required=True)
    c.add_argument("--gold", required=True)
    c.add_argument("--intensity-ids", default="")
    c.add_argument("--b", default="", help="risk: AI 판 답 확인 파일(쉼표) — riskrules.check_answer 출력")
    c.add_argument("--out", default=None)
    c.set_defaults(fn=cmd_measure)
    a = p.parse_args(argv)
    return a.fn(a)
