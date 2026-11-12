"""`python -m greenwash scan|wordcount|validate`. 노트북의 셀 16(파트별 쪽 입력과 실행)의 자리다.

Colab 의 `input()` 은 `--ceo`·`--company`·`--climate` 인자로 바꿨다. 스캔의 설정은 셀 16 그대로다.
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from greenwash import result as result_mod
from greenwash.checker import (
    MAX_TOKENS,
    MODEL_NAME,
    TEMPERATURE,
    GWConfig,
    ScanOutcome,
    sanitize_config_dict,
    scan_report_greenwashing_pages,
)
from greenwash.chunking import build_chunks_from_docs
from greenwash.dryrun import dry_run
from greenwash.parts import build_section_pages
from greenwash.pdftext import file_sha256, load_docs
from greenwash.ports import CallLog, MockEmbedding, RecordingChat, RecordingEmbedding, ScriptedChat, tiktoken_counter
from greenwash.retrieval import VectorIndex
from greenwash.wordcount import GREEN_TERMS, count_green_terms_in_docs

# 셀 16 이 `scan_report_greenwashing_pages` 에 주는 설정.
SCAN_CONFIG: Dict[str, Any] = {"debug": True, "debug_pages": 2}
RESULT_NAME = "greenwash-result.json"


def _cfg(config: Dict[str, Any]) -> GWConfig:
    cfg = GWConfig()
    for k, v in sanitize_config_dict(config).items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    return cfg


def _write(out_dir: Path, result: Dict[str, Any]) -> Path:
    result_mod.validate(result)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / RESULT_NAME
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def run_scan(docs, manual_pages: List[int], *, llm, embeddings) -> ScanOutcome:
    """셀 11(문서 전체를 조각내 색인)과 셀 16 의 3)(점검 쪽만 스캔, 근거 허용 쪽 = 점검 쪽 전부)."""
    if not manual_pages:
        # 노트북은 「점검할 페이지가 없습니다」를 내고 스캔을 부르지 않는다(결과는 빈 목록).
        return ScanOutcome(cfg=_cfg(SCAN_CONFIG), total_pages=len(docs))
    built = build_chunks_from_docs(
        docs,
        chunk_size=800,
        chunk_overlap=150,
        embedding_model="text-embedding-3-small",
        embedding_batch_size=50,
        normalize=True,
    )
    index = VectorIndex.from_documents(built["chunks"], embeddings)
    return scan_report_greenwashing_pages(
        docs,
        manual_pages,
        evidence_allowed_pages=manual_pages,
        config=dict(SCAN_CONFIG),
        vectorstore=index,
        llm=llm,
    )


def cmd_scan(args) -> int:
    if not args.dry_run and args.provider != "mock":
        print("실제 공급사는 이 밴드에 없다. `--provider mock` 또는 `--dry-run` 을 준다.", file=sys.stderr)
        return 2
    pdf = Path(args.pdf)
    docs = load_docs(pdf)
    section_pages, manual_pages = build_section_pages(args.ceo, args.company, args.climate, len(docs))
    chunk_config = result_mod.chunk_defaults()
    head = dict(file_name=pdf.name, sha256=file_sha256(pdf), page_count=len(docs), cfg=_cfg(SCAN_CONFIG),
                chunk_config=chunk_config, section_pages=section_pages, manual_pages=manual_pages)
    out_dir = Path(args.out)

    if args.dry_run:
        result = result_mod.header(provider="dry-run", **head)
        result["dry_run"] = dry_run(docs, manual_pages, manual_pages, SCAN_CONFIG, tiktoken_counter())
        path = _write(out_dir, result)
        d = result["dry_run"]
        print(f"dry-run: 쪽 {d['pages_checked']}, 조각 {d['chunks']}, 대화 요청 아래끝 {d['lower']['chat']['requests']} · "
              f"위끝 {d['upper']['chat']['requests']} → {path}")
        return 0

    script = json.loads(Path(args.script).read_text(encoding="utf-8")) if args.script else None
    log = CallLog()
    chat = RecordingChat(ScriptedChat(script), log, provider="mock", model=MODEL_NAME,
                         params={"temperature": TEMPERATURE, "max_tokens": MAX_TOKENS})
    embeddings = RecordingEmbedding(MockEmbedding(), log, provider="mock")
    outcome = run_scan(docs, manual_pages, llm=chat, embeddings=embeddings)
    total, per_page = count_green_terms_in_docs(docs, GREEN_TERMS)
    by_kind: Dict[str, int] = {}
    for r in log.records:
        if r["promptId"] != "embedding":
            kind = r["promptId"].split("/", 1)[-1]
            by_kind[kind] = by_kind.get(kind, 0) + 1
    calls: Dict[str, Any] = {
        "chat": by_kind,
        "embedding_document_requests": len(embeddings.inner.document_requests),
        "embedding_query_requests": embeddings.inner.query_requests,
    }
    result = result_mod.header(provider="mock", **head)
    result.update(result_mod.scan_body(outcome, section_pages, total, per_page, calls))
    path = _write(out_dir, result)
    log.dump_jsonl(out_dir / "calls.jsonl")
    print(f"mock(synthetic): 쪽 {len(manual_pages)}, 검출 {len(outcome.results)}, 실패 쪽 {outcome.failed} → {path}")
    return 0


def cmd_wordcount(args) -> int:
    docs = load_docs(Path(args.pdf))
    total, per_page = count_green_terms_in_docs(docs, GREEN_TERMS)
    text = json.dumps({"page_count": len(docs), "word_counts": result_mod.word_counts_json(total, per_page)}, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


def cmd_validate(args) -> int:
    bad = 0
    for p in args.json:
        try:
            result_mod.validate(json.loads(Path(p).read_text(encoding="utf-8")))
            print(f"OK {p}")
        except Exception as e:
            bad += 1
            print(f"FAIL {p}: {str(e).splitlines()[0]}")
    return 1 if bad else 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="greenwash", description="그린워싱 Colab 노트북의 재현(실제 공급사 호출 없음)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="파트별 쪽을 점검한다(모의 공급사 또는 dry-run)")
    s.add_argument("pdf")
    s.add_argument("--ceo", default="", help="CEO 메시지의 쪽. 예: 5 / 6~14 / 25-37,55~74")
    s.add_argument("--company", default="", help="회사소개의 쪽")
    s.add_argument("--climate", default="", help="기후/환경 토픽의 쪽")
    s.add_argument("--out", required=True, help="산출물 디렉터리")
    s.add_argument("--provider", choices=["mock"], default=None)
    s.add_argument("--script", default=None, help="모의 대화의 대본(JSON). 없으면 내장 기본 대본")
    s.add_argument("--dry-run", action="store_true", help="호출을 조립하되 보내지 않고 요청 수·글자·토큰만 센다")
    s.set_defaults(fn=cmd_scan)

    w = sub.add_parser("wordcount", help="의심 낱말을 문서 전체에서 센다(AI 없음)")
    w.add_argument("pdf")
    w.add_argument("--out", default=None, help="JSON 을 쓸 파일. 없으면 표준 출력")
    w.set_defaults(fn=cmd_wordcount)

    v = sub.add_parser("validate", help="결과 JSON 을 스키마로 검증한다")
    v.add_argument("json", nargs="+")
    v.set_defaults(fn=cmd_validate)

    args = ap.parse_args(argv)
    return args.fn(args)
