"""결과 JSON 조립과 스키마 검증(`schemas/greenwash-result.schema.json`)."""
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import jsonschema

from greenwash import checker as checker_mod
from greenwash.checker import GWConfig, ScanOutcome
from greenwash.ports import EMBEDDING_BATCH_SIZE, EMBEDDING_MODEL

SCHEMA_VERSION = "0.1"
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "greenwash-result.schema.json"
PROVIDERS = ("mock", "dry-run", "live")


def load_schema() -> Dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate(result: Dict[str, Any]) -> None:
    jsonschema.Draft202012Validator(load_schema()).validate(result)


def config_json(cfg: GWConfig, chunk_config: Dict[str, Any]) -> Dict[str, Any]:
    c = asdict(cfg)
    c["report_levels"] = list(c["report_levels"])
    return {
        "checker": c,
        "chunking": {
            "chunk_size": chunk_config["chunk_size"],
            "chunk_overlap": chunk_config["chunk_overlap"],
            "separators": list(chunk_config["separators"]),
            "normalize": chunk_config["normalize"],
            "embedding_batch_size": chunk_config["embedding_batch_size"],
        },
        "chat": {"temperature": checker_mod.TEMPERATURE, "max_tokens": checker_mod.MAX_TOKENS},
    }


def header(*, file_name: str, sha256: str, page_count: int, cfg: GWConfig, chunk_config: Dict[str, Any], provider: str,
           section_pages: Dict[str, List[int]], manual_pages: List[int]) -> Dict[str, Any]:
    if provider not in PROVIDERS:
        raise ValueError(f"공급사 갈래는 {PROVIDERS} 가운데 하나다: {provider!r}")
    return {
        "schema_version": SCHEMA_VERSION,
        "source": {"file_name": file_name, "sha256": sha256, "page_count": page_count},
        "config": config_json(cfg, chunk_config),
        "models": {"chat": checker_mod.MODEL_NAME, "embedding": chunk_config.get("embedding_model", EMBEDDING_MODEL)},
        "prompt_version": cfg.prompt_version,
        "provider": provider,
        "synthetic": provider == "mock",
        "parts": [{"name": name, "pages": list(pages)} for name, pages in section_pages.items()],
        "pages": list(manual_pages),
    }


def item_json(r: Dict[str, Any], section_pages: Dict[str, List[int]]) -> Dict[str, Any]:
    """노트북의 결과 dict 한 건 → JSON 한 건. 세 벌 칸(`fix`·`action`·`fix_paragraph`)은 `fix_paragraph` 한 칸으로 싣는다."""
    return {
        "page": r["page"],
        "parts": [name for name, pages in section_pages.items() if r["page"] in pages],
        "sentence": r["sentence"],
        "level": r["level"],
        "risk_tags": list(r.get("risk_tags", [])),
        "confirmed_in_evidence": list(r.get("confirmed_in_evidence", [])),
        "missing_fields": list(r.get("missing_fields", [])),
        "reason_bullets": list(r.get("reason_bullets", [])),
        "fix_paragraph": r.get("fix_paragraph", ""),
        "evidence_pages": list(r.get("evidence_pages", [])),
        "evidence_internal": r.get("evidence_internal", ""),
        # 문장이 쪽 글의 어디인지. 노트북은 AI 가 돌려준 문장이 쪽 글에 있는지 확인하지 않으므로 이 밴드에서는 늘 null 이다.
        "span": None,
    }


def counts_json(counter: Counter) -> List[Dict[str, Any]]:
    return [{"term": term, "count": n} for term, n in counter.most_common()]


def word_counts_json(total: Counter, per_page: Sequence[Tuple[int, Counter]]) -> Dict[str, Any]:
    return {"total": counts_json(total), "pages": [{"page": p, "counts": counts_json(c)} for p, c in per_page]}


def scan_body(outcome: ScanOutcome, section_pages: Dict[str, List[int]], total: Counter, per_page: Sequence[Tuple[int, Counter]],
              calls: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "items": [item_json(r, section_pages) for r in outcome.results],
        "candidate_stats": {**{k: int(v) for k, v in outcome.agg.items()}, "failed_pages": list(outcome.failed_pages)},
        "word_counts": word_counts_json(total, per_page),
    }
    if calls is not None:
        body["calls"] = calls
    return body


def chunk_defaults() -> Dict[str, Any]:
    """노트북의 셀 11 이 `build_vectorstore_from_docs` 에 주는 값."""
    return {
        "chunk_size": 800,
        "chunk_overlap": 150,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_batch_size": EMBEDDING_BATCH_SIZE,
        "separators": ["\n\n", "\n", " "],
        "normalize": True,
    }
