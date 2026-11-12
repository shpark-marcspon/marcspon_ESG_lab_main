"""`python -m evalrig.greenwash <명령>` — `gold-sample` · `gold-sg1` · `gold-check` · `gm1` · `convert` · `score` · `same` ·
`jitter` · `leak-check`(모두 실비 0).

py/ 에서:
  uv run --frozen --group greenwash python -m evalrig.greenwash gold-sample [--out <파일>]
  uv run --frozen --group greenwash python -m evalrig.greenwash gold-sg1 <SG1 권 폴더> --document <document.json> --out <폴더>
  uv run --frozen --group greenwash python -m evalrig.greenwash gold-check <정답 파일…>
  uv run --frozen --group greenwash python -m evalrig.greenwash gm1 <PDF…> --models <models.json> --out <폴더>
  uv run --frozen python -m evalrig.greenwash convert <result.json> [--out <파일>]
  uv run --frozen python -m evalrig.greenwash score <정답> <result.json> [--out <파일>]
  uv run --frozen python -m evalrig.greenwash same <result A> <result B> [--gold <정답…>] --out <폴더>
  uv run --frozen python -m evalrig.greenwash jitter <result…> [--out <폴더>]
  uv run --frozen python -m evalrig.greenwash leak-check <폴더·파일…> --against <SG1 queries.json…> [--skip gold]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from evalrig.common import gold as common_gold
from evalrig.greenwash import gold

REPO = Path(__file__).resolve().parents[3]
SAMPLE_GOLD = REPO / "data/evalrig/greenwash/synthetic-sample.gold.json"


def cmd_gold_sample(a) -> int:
    gs = gold.check(gold.sample_gold())
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(common_gold.dumps(gs), encoding="utf-8")
    print(json.dumps({"out": str(a.out), "items": len(gs["items"]), "sentences": gs["meta"]["sentences"]}, ensure_ascii=False))
    return 0


def cmd_gold_sg1(a) -> int:
    d = Path(a.book)
    gs = gold.from_sg1(json.loads((d / "queries.json").read_text(encoding="utf-8")),
                       json.loads((d / "check/claim-expected.json").read_text(encoding="utf-8")),
                       json.loads(Path(a.document).read_text(encoding="utf-8")))
    gold.check(gs)
    out = Path(a.out) / f"sg1-claims.{gs['meta']['source']['sha256'][:12]}.gold.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(common_gold.dumps(gs), encoding="utf-8")
    print(json.dumps({"out": out.name, "items": len(gs["items"]), "counts": gs["meta"]["counts"]}, ensure_ascii=False))
    return 0


def cmd_gold_check(a) -> int:
    bad = 0
    for p in a.files:
        try:
            gs = gold.check(json.loads(Path(p).read_text(encoding="utf-8")))
            print(json.dumps({"file": Path(p).name, **common_gold.header(gs)}, ensure_ascii=False))
        except (common_gold.GoldError, ValueError, KeyError) as e:
            bad += 1
            print(f"FAIL {Path(p).name}: {e}", file=sys.stderr)
    return 1 if bad else 0


def cmd_gm1(a) -> int:
    from evalrig.greenwash import gm1

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for pdf in a.pdfs:
        r = gm1.run_pdf(pdf, a.models)
        (out / f"{r['source']['sha12']}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        rows.append(r)
        print(json.dumps({"sha12": r["source"]["sha12"], "totals": r["totals"], "pages": r["pages"]}, ensure_ascii=False))
    (out / "gm1.md").write_text(gm1.to_md(rows), encoding="utf-8")
    return 0


def _sha256(p) -> str:
    import hashlib

    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _convs(path, run=""):
    from evalrig.greenwash import convert_e0

    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return convert_e0.convert_doc(doc, run=run or Path(path).parent.name)


def _write(out, name, obj) -> None:
    if out is None:
        print(json.dumps(obj, ensure_ascii=False, indent=1))
        return
    o = Path(out)
    o.mkdir(parents=True, exist_ok=True)
    (o / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(o / name)}, ensure_ascii=False))


def cmd_convert(a) -> int:
    from evalrig.greenwash import convert_e0

    cs = _convs(a.result)
    obj = {"input": {"file": Path(a.result).name, "sha256": _sha256(a.result)}, "results": [convert_e0.summary(c) for c in cs]}
    _write(a.out, "convert.json", obj)
    return 0


def cmd_score(a) -> int:
    from evalrig.greenwash import score

    gs = gold.check(json.loads(Path(a.gold).read_text(encoding="utf-8")))
    rows = [score.score(gs, c) for c in _convs(a.result)]
    _write(a.out, "score.json", {"input": {"file": Path(a.result).name, "sha256": _sha256(a.result)}, "gold": common_gold.header(gs), "scores": rows})
    return 0


def cmd_same(a) -> int:
    from evalrig.greenwash import sameinput

    ca, cb = _convs(a.a), _convs(a.b)
    if len(ca) != 1 or len(cb) != 1:
        print(f"결과가 하나씩이어야 함: {len(ca)}·{len(cb)}", file=sys.stderr)
        return 2
    golds = [gold.check(json.loads(Path(g).read_text(encoding="utf-8"))) for g in (a.gold or [])]
    try:
        res = sameinput.same(ca[0], cb[0], golds or None)
    except sameinput.InputMismatch as e:
        print(f"InputMismatch: {e}", file=sys.stderr)
        return 3
    res["input"] = [{"file": Path(p).name, "run": Path(p).parent.name, "sha256": _sha256(p)} for p in (a.a, a.b)]
    _write(a.out, "same.json", res)
    return 0


def cmd_jitter(a) -> int:
    from evalrig.greenwash import sameinput

    convs = [c for p in a.results for c in _convs(p)]
    res = sameinput.jitter(convs)
    res["input"] = [{"run": Path(p).parent.name, "sha256": _sha256(p)} for p in a.results]
    _write(a.out, "jitter.json", res)
    return 0


def cmd_leak_check(a) -> int:
    from evalrig.greenwash.units import norm

    needles = []
    for q in a.against:
        needles += [norm(r["text"]) for r in json.loads(Path(q).read_text(encoding="utf-8")) if r.get("type") == "greenwash"]
    files, skipped = [], 0
    for p in map(Path, a.paths):
        for f in ([p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file())):
            if any(part in a.skip for part in f.parts):
                skipped += 1
                continue
            files.append(f)
    hits = []
    for f in files:
        try:
            text = norm(f.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            continue
        n = sum(1 for s in needles if s and s in text)
        if n:
            hits.append({"file": str(f), "hits": n})
    print(json.dumps({"needles": len(needles), "files": len(files), "skipped": skipped, "hits": hits}, ensure_ascii=False))
    return 1 if hits else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m evalrig.greenwash")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("gold-sample", help="지은 표본 정답을 짓는다(git 파일)")
    s.add_argument("--out", default=str(SAMPLE_GOLD))
    s.set_defaults(fn=cmd_gold_sample)
    s = sub.add_parser("gold-sg1", help="SG1 한 권의 주장 판정 → 정답(git 밖)")
    s.add_argument("book")
    s.add_argument("--document", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_gold_sg1)
    s = sub.add_parser("gold-check", help="정답 파일 꼴 검사(공통 check + 모듈 약속)")
    s.add_argument("files", nargs="+")
    s.set_defaults(fn=cmd_gold_check)
    s = sub.add_parser("gm1", help="쪽 상한·2,500자 풂의 판정 후보 배수(실비 0)")
    s.add_argument("pdfs", nargs="+")
    s.add_argument("--models", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_gm1)
    s = sub.add_parser("convert", help="1차 결과 → 채점 단위")
    s.add_argument("result")
    s.add_argument("--out", default=None)
    s.set_defaults(fn=cmd_convert)
    s = sub.add_parser("score", help="정답 × 1차 결과")
    s.add_argument("gold")
    s.add_argument("result")
    s.add_argument("--out", default=None)
    s.set_defaults(fn=cmd_score)
    s = sub.add_parser("same", help="같은 입력 두 결과 대조")
    s.add_argument("a")
    s.add_argument("b")
    s.add_argument("--gold", nargs="*")
    s.add_argument("--out", default=None)
    s.set_defaults(fn=cmd_same)
    s = sub.add_parser("jitter", help="같은 입력 결과 N 개의 흔들림")
    s.add_argument("results", nargs="+")
    s.add_argument("--out", default=None)
    s.set_defaults(fn=cmd_jitter)
    s = sub.add_parser("leak-check", help="SG1 주장 문장(정규화)이 산출·보고에 들었나")
    s.add_argument("paths", nargs="+")
    s.add_argument("--against", nargs="+", required=True)
    s.add_argument("--skip", nargs="*", default=["gold"], help="건너뛸 폴더 이름(기본 gold — 정답 자체)")
    s.set_defaults(fn=cmd_leak_check)
    a = p.parse_args(argv)
    return a.fn(a)
