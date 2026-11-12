"""명령줄 진입점: parse, score, render."""
import argparse
import json
import sys
from pathlib import Path

from .engine import add_engine_argument, open_pdf
from .document import ParseError, parse_pdf, parse_pdf_places, validate, validate_places
from .render import render_pages
from .score import ScoreError, aggregate, format_report, score_gold
from .tables import STRATEGIES


def parse_pages(spec):
    """'3,10-12' → [3, 10, 11, 12]"""
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def cmd_parse(a):
    pages = parse_pages(a.pages) if a.pages else None
    places = None
    if a.places:
        doc, places = parse_pdf_places(a.pdf, pages=pages, strategy=a.strategy, dedupe_overdrawn=a.dedupe_overdrawn, engine=a.engine)
    else:
        doc = parse_pdf(a.pdf, pages=pages, strategy=a.strategy, dedupe_overdrawn=a.dedupe_overdrawn, engine=a.engine)
    errors = validate(doc)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    target = out / "document.json"
    target.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    if places is not None:
        # 곁 파일. document.json 은 이 옵션과 무관하게 같은 글자다.
        errors = errors + [f"char-places: {e}" for e in validate_places(places)]
        (out / "char-places.json").write_text(json.dumps(places, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    rendered = []
    if a.render:
        rendered = render_pages(a.pdf, out / "pages", pages=pages, dpi=a.dpi, engine=a.engine)
    by_status = {}
    for t in doc["tables"]:
        by_status[t["status"]] = by_status.get(t["status"], 0) + 1
    by_method = {}
    for t in doc["tables"]:
        by_method[t["method"]] = by_method.get(t["method"], 0) + 1
    summary = {} if places is None else {"char_places": {
        "out": str(out / "char-places.json"), "containers": len(places["containers"]), "unlinked": len(places["unlinked"])}}
    print(json.dumps({
        "out": str(target), "pages_parsed": len(doc["pages"]), "page_count": doc["source"]["page_count"],
        "pages_without_text_layer": [p["page"] for p in doc["pages"] if p["text_layer"] == "none"],
        "sections": len(doc["sections"]), "blocks": len(doc["blocks"]), "tables": len(doc["tables"]),
        "tables_by_status": by_status, "tables_by_method": by_method,
        "rendered_pages": len(rendered), "schema_valid": not errors, "schema_errors": errors[:20],
        **summary,
    }, ensure_ascii=False, indent=1))
    return 1 if errors else 0


def cmd_score(a):
    strategies = [a.strategy] if a.strategy else ["auto", "text"]
    titles = {"auto": "전략 auto (괘선 기반 → 실패 시 글자 정렬 기반)", "lines": "전략 lines (괘선 기반만)",
              "text": "전략 text (글자 정렬 기반만, 괘선을 쓰지 않음)"}
    dump = {}
    for s in strategies:
        results = score_gold(a.gold, s, engine=a.engine)
        print(format_report(results, titles[s]))
        print()
        dump[s] = {"tables": results, "by_report": aggregate(results, "report"),
                   "by_form": aggregate(results, "form"), "all": aggregate(results, None)["ALL"]}
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(dump, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


def cmd_render(a):
    files = render_pages(a.pdf, a.out, pages=parse_pages(a.pages) if a.pages else None, dpi=a.dpi, engine=a.engine)
    print(json.dumps({"rendered_pages": len(files), "out": str(a.out), "dpi": a.dpi}, ensure_ascii=False))
    return 0


def cmd_charts(a):
    """도표 라벨 산출 (새 파일). 문서 모델은 읽기만 한다."""
    from .charts import label_document
    model = json.loads(Path(a.document).read_text(encoding="utf-8"))
    with open_pdf(a.pdf, a.engine) as pdf:
        labels, places = label_document(pdf, model, grid_rule=not a.no_grid_rule)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "chart-labels.json").write_text(json.dumps(labels, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "places.json").write_text(json.dumps(places, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"out": str(out / "chart-labels.json"), "charts": len(labels["charts"]), "words": len(labels["words"]),
                      "values": sum(1 for w in labels["words"] if w["role"] == "value"), "places": len(places),
                      "unlinked": len(labels["unlinked"])}, ensure_ascii=False))
    return 0


def cmd_chartcands(a):
    """도표 후보 구역 (새 파일). 꼴을 보지 않는다. 문서 모델은 읽기만 한다."""
    from jsonschema import Draft202012Validator

    from .chartcand import DEFINITIONS, candidates_document
    from .config import REPO_ROOT
    model = json.loads(Path(a.document).read_text(encoding="utf-8"))
    with open_pdf(a.pdf, a.engine) as pdf:
        cands = candidates_document(pdf, model)
    schema = json.loads((REPO_ROOT / "schemas" / "chart-candidates.schema.json").read_text(encoding="utf-8"))
    errors = [e.message for e in Draft202012Validator(schema).iter_errors(cands)]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "chart-candidates.json").write_text(json.dumps(cands, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"out": str(out / "chart-candidates.json"), "pages": len(cands["pages"]),
                      "candidate_pages": {d: sum(1 for p in cands["pages"] if p[d]) for d in DEFINITIONS},
                      "regions": {d: sum(p[d] for p in cands["pages"]) for d in DEFINITIONS},
                      "schema_valid": not errors, "schema_errors": errors[:20]}, ensure_ascii=False))
    return 1 if errors else 0


def cmd_tagpages(a):
    """번호표를 덮은 쪽 그림. 번호표의 자리와 번호는 입력 파일이 정한다 (값을 읽지 않는다)."""
    from .tagpages import render_tagged
    tags = json.loads(Path(a.tags).read_text(encoding="utf-8"))
    files = render_tagged(a.pdf, tags, a.out, dpi=a.dpi)
    print(json.dumps({"rendered_pages": len(files), "out": str(a.out), "dpi": a.dpi, "tags": len(tags)}, ensure_ascii=False))
    return 0


def cmd_tagtargets(a):
    """번호표의 대상을 꼴을 보지 않고 고른다 (밴드 VT1, 새 파일). 문서 모델은 읽기만 한다."""
    from .tagtext import targets_document
    model = json.loads(Path(a.document).read_text(encoding="utf-8"))
    with open_pdf(a.pdf, a.engine) as pdf:
        words, tags, marks = targets_document(pdf, model)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "number-words.json").write_text(json.dumps({"source": model["source"], "mark_pages": marks, "words": words}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "tags.json").write_text(json.dumps(tags, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"out": str(out), "number_words": len(words), "tags": len(tags), "mark_pages": len(marks),
                      "tag_pages": len({t["page"] for t in tags})}, ensure_ascii=False))
    return 0


def cmd_tagtext(a):
    """번호표가 있는 쪽의 글을 값 없이 좌표와 함께 낸다 (밴드 VT1, 새 파일)."""
    from .tagtext import lines_document
    tags = json.loads(Path(a.tags).read_text(encoding="utf-8"))
    pages = [int(p) for p in a.pages.split(",")] if a.pages else None
    with open_pdf(a.pdf, a.engine) as pdf:
        lines = lines_document(pdf, tags, pages)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "page-lines.json").write_text(json.dumps(lines, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"out": str(out / "page-lines.json"), "pages": len(lines), "lines": sum(len(p["lines"]) for p in lines.values()),
                      "visible_numbers": {k: sum(p["visible_numbers"][k] for p in lines.values()) for k in ("year", "other")}}, ensure_ascii=False))
    return 0


def cmd_placecheck(a):
    """값 후보의 상자 안 글자를 PDF 에서 다시 읽어 원문 글자와 견준다 (측정 도구)."""
    from . import placecheck
    fact_places = json.loads(Path(a.fact_places).read_text(encoding="utf-8"))
    rows = placecheck.check(a.pdf, fact_places)
    summary = placecheck.summarize(rows)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "check.json").write_text(json.dumps({"source": fact_places["source"], "summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "check.md").write_text(placecheck.render_summary(fact_places["source"]["file_name"], summary), encoding="utf-8")
    print(json.dumps({"out": str(out / "check.json"), **{k: summary["all"][k] for k in ("facts", "box", "no_box", "same", "differs")}}, ensure_ascii=False))
    return 0


def cmd_placesample(a):
    """눈으로 볼 표본: 갈래마다 씨앗으로 뽑아 상자를 그린 조각 그림을 쓴다 (기계 확인의 결과는 싣지 않는다)."""
    from . import placecheck
    fact_places = json.loads(Path(a.fact_places).read_text(encoding="utf-8"))
    sample = placecheck.draw_sample(fact_places, a.seed, a.per_bucket, a.total)
    files, listing = placecheck.render_sheets(a.pdf, sample, a.out)
    (Path(a.out) / "sample.json").write_text(json.dumps({"source": fact_places["source"], "seed": a.seed, "per_bucket": a.per_bucket, "total": a.total, "places": listing},
                                                        ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"out": str(a.out), "places": len(listing), "sheets": len(files), "seed": a.seed}, ensure_ascii=False))
    return 0


def cmd_enginediff(a):
    """두 엔진의 원재료와 산출을 견준다 (측정 도구). 두 엔진을 다 불러온다."""
    from . import enginediff
    raw, outputs, attribution = enginediff.run(a.pdf, a.a, a.b, a.out)
    print(json.dumps({"out": str(Path(a.out) / "engine-diff.json"),
                      "sha256_same": {n: v["same"] for n, v in outputs["sha256"].items()},
                      "pages_differing": attribution["pages_differing"], "by_cause": attribution["by_cause"]}, ensure_ascii=False))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="docparse", description="PDF → 섹션·문단·표 JSON (LLM·OCR 없음)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("parse", help="PDF 를 분해해 <out>/document.json 을 쓴다")
    p.add_argument("pdf")
    p.add_argument("--out", required=True)
    p.add_argument("--pages", help="예: 60-70,142 (기본: 전 쪽)")
    p.add_argument("--strategy", choices=STRATEGIES, default="auto")
    p.add_argument("--render", action="store_true", help="쪽 PNG 도 <out>/pages/ 에 쓴다")
    p.add_argument("--dpi", type=int, default=150)
    p.add_argument("--places", action="store_true",
                   help="글자 자리(표의 칸·글 블록의 글자마다 좌표)도 <out>/char-places.json 에 쓴다. document.json 은 그대로다")
    p.add_argument("--dedupe-overdrawn", action="store_true",
                   help="같은 글자가 같은 자리에 또 그려진 것을 한 번만 뽑는다 (기본 끔. 끄면 산출이 그대로다)")
    add_engine_argument(p)
    p.set_defaults(fn=cmd_parse)
    s = sub.add_parser("score", help="정답 표에 대한 셀 단위 일치율")
    s.add_argument("--gold", required=True)
    s.add_argument("--strategy", choices=STRATEGIES, help="기본: auto 와 text 를 둘 다 잰다")
    s.add_argument("--json", help="결과를 JSON 으로도 쓴다")
    add_engine_argument(s)
    s.set_defaults(fn=cmd_score)
    r = sub.add_parser("render", help="쪽을 PNG 로 렌더링한다")
    r.add_argument("pdf")
    r.add_argument("--out", required=True)
    r.add_argument("--pages")
    r.add_argument("--dpi", type=int, default=200)
    add_engine_argument(r)
    r.set_defaults(fn=cmd_render)
    c = sub.add_parser("charts", help="도표를 찾고 도표 속 숫자에 범주·계열을 붙여 <out>/chart-labels.json 을 쓴다 (문서 모델은 읽기만)")
    c.add_argument("pdf")
    c.add_argument("--document", required=True, help="같은 PDF 의 document.json")
    c.add_argument("--out", required=True)
    c.add_argument("--no-grid-rule", action="store_true", help="괘선 표의 칸 배경을 막대에서 빼는 규칙을 끈다 (그 규칙을 더하기 전의 성적을 다시 낼 때만)")
    add_engine_argument(c)
    c.set_defaults(fn=cmd_charts)
    cc = sub.add_parser("chartcands", help="도표가 있을 것 같은 구역의 후보를 꼴을 보지 않고 가려 <out>/chart-candidates.json 을 쓴다 (문서 모델은 읽기만)")
    cc.add_argument("pdf")
    cc.add_argument("--document", required=True, help="같은 PDF 의 document.json")
    cc.add_argument("--out", required=True)
    add_engine_argument(cc)
    cc.set_defaults(fn=cmd_chartcands)
    g = sub.add_parser("tagpages", help="숫자 자리를 번호표로 덮은 쪽 PNG 를 쓴다")
    g.add_argument("pdf")
    g.add_argument("--tags", required=True, help='[{"page", "n", "bbox"}] JSON')
    g.add_argument("--out", required=True)
    g.add_argument("--dpi", type=int, default=110)
    g.set_defaults(fn=cmd_tagpages)
    tt = sub.add_parser("tagtargets", help="번호표의 대상을 꼴을 보지 않고 골라 <out>/number-words.json, tags.json 을 쓴다 (문서 모델은 읽기만)")
    tt.add_argument("pdf")
    tt.add_argument("--document", required=True, help="같은 PDF 의 document.json")
    tt.add_argument("--out", required=True)
    add_engine_argument(tt)
    tt.set_defaults(fn=cmd_tagtargets)
    tx = sub.add_parser("tagtext", help="번호표가 있는 쪽의 글을 줄 토막과 좌표로 <out>/page-lines.json 에 쓴다 (숫자는 번호나 ▒ 로만)")
    tx.add_argument("pdf")
    tx.add_argument("--tags", required=True, help='[{"page", "n", "bbox"}] JSON')
    tx.add_argument("--pages", help="쉼표로 가른 쪽 번호 (번호표가 없는 쪽도 낼 때)")
    tx.add_argument("--out", required=True)
    add_engine_argument(tx)
    tx.set_defaults(fn=cmd_tagtext)
    k = sub.add_parser("placecheck", help="값 후보의 상자 안 글자를 PDF 에서 다시 읽어 원문과 견준다 (check.json, check.md)")
    k.add_argument("pdf")
    k.add_argument("--fact-places", required=True, help="ts/reconcile 의 places-cli.ts fact-places 가 낸 fact-places.json")
    k.add_argument("--out", required=True)
    k.set_defaults(fn=cmd_placecheck)
    m = sub.add_parser("placesample", help="값 후보의 표본에 상자를 그린 조각 그림을 쓴다 (눈으로 볼 것)")
    m.add_argument("pdf")
    m.add_argument("--fact-places", required=True)
    m.add_argument("--out", required=True)
    m.add_argument("--seed", type=int, required=True)
    m.add_argument("--per-bucket", type=int, default=25)
    m.add_argument("--total", type=int, default=0, help="갈래가 모자라 덜 뽑힌 만큼을 남은 값 후보에서 채워 이 수에 맞춘다")
    m.set_defaults(fn=cmd_placesample)
    ed = sub.add_parser("enginediff", help="두 엔진(pymupdf, pdfium)의 원재료와 산출을 견줘 <out>/engine-diff.json, engine-diff.md 를 쓴다")
    ed.add_argument("pdf")
    ed.add_argument("--a", required=True, help="pymupdf 엔진으로 낸 산출 디렉터리")
    ed.add_argument("--b", required=True, help="pdfium 엔진으로 낸 산출 디렉터리")
    ed.add_argument("--out", required=True)
    ed.set_defaults(fn=cmd_enginediff)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except (ParseError, ScoreError, ValueError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 2
