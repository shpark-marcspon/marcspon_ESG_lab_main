"""GM1 — 쪽 상한·2,500자를 풀면 판정 후보가 몇 배가 되나(실비 0, 발주서 EVB §2-9).

1차 설정은 `SAFE_LIMITS_INT` 가 쪽 글 800~5000·후보 0~5 로 잘라 풀 수 없다(★5). 그래서 1차 `heuristics` 함수를
쪽 글에 직접 부른다. 쪽마다 셋:
  ⓐ 기준선 = 2,500자로 자른 쪽 글의 규칙 후보 수(`collect_rule_based_declaratives`, max_n = max(3, 3)) + AI 탐지 상한 3
     (글이 있는 쪽만 — `dryrun.py` 148·160줄과 같은 셈)
  ⓑ 2,500자만 풂 = 쪽 글 전체, 같은 상한
  ⓒ 둘 다 풂 = 쪽 글 전체의 `split_sentences_kor` 중 `has_env_signal` 참인 문장 수(블록 단위 후보의 위끝)
늘어난 판정 수 = 쪽마다 max(0, ⓑ−ⓐ)·max(0, ⓒ−ⓐ) 의 합(상한에 걸린 쪽의 순증 — ⓐ 의 AI 상한 3 이 환경 문장보다 많은
쪽에서 빠지는 수를 다른 쪽의 늘어남과 상쇄하지 않는다). 합 ⓒ/ⓐ 배수는 그대로 따로 싣는다.
비용 어림 = 늘어난 판정 수 × 판정 한 건 입력 토큰(그 PDF 의 `dry_run` 아래끝 eval 요청당 평균) × 단가.
단가는 실행 때 models 파일에서 읽고(코드에 숫자 0) 파일 sha16·확인일을 산출에 싣는다. 산출에는 수·쪽 번호·sha12 만.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

PAGE_CHARS = 2500  # 1차 max_chars_page_text 기본
CAP = 3  # 1차 max_candidates_per_page 기본(규칙 max_n = max(3, CAP), AI 탐지 상한)
UNLIMITED = 10**9


def page_counts(text: str) -> dict[str, Any]:
    from langchain_core.documents import Document

    from greenwash.heuristics import (
        collect_rule_based_declaratives,
        get_page_text,
        has_env_signal,
        normalize_text_for_llm,
        split_sentences_kor,
    )

    doc = Document(page_content=text)
    cut = get_page_text(doc, PAGE_CHARS)
    full = get_page_text(doc, UNLIMITED)
    n_rule = max(3, CAP)
    a = (len(collect_rule_based_declaratives(cut, max_n=n_rule)) + CAP) if cut else 0
    b = (len(collect_rule_based_declaratives(full, max_n=n_rule)) + CAP) if full else 0
    c = sum(1 for s in split_sentences_kor(full) if has_env_signal(s))
    return {"a": a, "b": b, "c": c, "chars": len(normalize_text_for_llm(text.strip())), "truncated": len(full) > PAGE_CHARS,
            "empty": not full}


def book_counts(texts: list[str]) -> dict[str, Any]:
    per = [dict(page=i, **page_counts(t)) for i, t in enumerate(texts, start=1)]
    tot = {k: sum(p[k] for p in per) for k in ("a", "b", "c")}
    return {
        "pages": len(per),
        "pagesTruncated": sum(p["truncated"] for p in per),
        "pagesEmpty": sum(p["empty"] for p in per),
        "totals": tot,
        "ratio": {"b/a": _ratio(tot["b"], tot["a"]), "c/a": _ratio(tot["c"], tot["a"])},
        "extra": {"b": sum(max(0, p["b"] - p["a"]) for p in per), "c": sum(max(0, p["c"] - p["a"]) for p in per)},
        "pagesWhereBgtA": [p["page"] for p in per if p["b"] > p["a"]],
        "pagesWhereCgtA": sum(1 for p in per if p["c"] > p["a"]),
        "perPage": [{k: p[k] for k in ("page", "a", "b", "c", "truncated", "empty")} for p in per],
    }


def _ratio(num: int, den: int) -> dict[str, Any]:
    if den == 0:
        return {"status": "unmeasurable", "reason": "분모 0", "num": num, "den": den}
    return {"status": "measured", "num": num, "den": den, "value": round(num / den, 4)}


def pricing(models_path: str | Path, model: str) -> dict[str, Any]:
    p = Path(models_path)
    raw = p.read_bytes()
    m = json.loads(raw)
    entry = m["models"][model]
    return {"model": model, "input": entry["pricing"]["input"], "output": entry["pricing"]["output"],
            "per": "USD / 1M tokens", "file": p.name, "sha16": hashlib.sha256(raw).hexdigest()[:16],
            "checkedOn": m.get("checkedOn"), "notes": [n[:40] for n in entry.get("notes", [])[:1]]}


def eval_tokens(dry: dict[str, Any]) -> dict[str, Any]:
    """dry_run 산출 → 판정(eval) 한 건 입력 토큰 평균(아래끝). 못 쟀으면 그 갈래 그대로."""
    ev = dry["lower"]["chat_by_kind"]["eval"]
    tok = ev["input_tokens"]
    if tok.get("status") != "measured":
        return {"status": "unmeasured", "reason": tok.get("reason", ""), "requests": ev["requests"]}
    if ev["requests"] == 0:
        return {"status": "unmeasurable", "reason": "아래끝 eval 요청 0", "requests": 0}
    return {"status": "measured", "perRequest": round(tok["value"] / ev["requests"], 2), "requests": ev["requests"],
            "tokens": tok["value"]}


def cost(extra: int, per_eval: dict[str, Any], price: dict[str, Any], max_output_tokens: int) -> dict[str, Any]:
    """늘어난 판정 수 extra 의 비용 어림(USD). 입력만 · 출력 상한(max_tokens) 따로."""
    if per_eval.get("status") != "measured":
        return {"status": per_eval.get("status", "unmeasured"), "reason": per_eval.get("reason", ""), "extra": extra}
    inp = extra * per_eval["perRequest"] * price["input"] / 1e6
    out = extra * max_output_tokens * price["output"] / 1e6
    return {"status": "measured", "extra": extra, "inputUsd": round(inp, 6), "outputMaxUsd": round(out, 6),
            "basis": f"extra × {per_eval['perRequest']} 입력 토큰 × {price['input']} / 1M · 출력 상한 extra × {max_output_tokens} × {price['output']} / 1M"}


def run_pdf(pdf: str | Path, models: str | Path) -> dict[str, Any]:
    from greenwash import checker
    from greenwash.cli import SCAN_CONFIG
    from greenwash.dryrun import dry_run
    from greenwash.pdftext import docs_from_texts, read_page_texts
    from greenwash.ports import tiktoken_counter

    pdf = Path(pdf)
    raw = pdf.read_bytes()
    texts = read_page_texts(pdf)
    bc = book_counts(texts)
    docs = docs_from_texts(texts)
    pages = list(range(1, len(docs) + 1))
    dry = dry_run(docs, pages, pages, SCAN_CONFIG, tiktoken_counter())
    per_eval = eval_tokens(dry)
    price = pricing(models, checker.MODEL_NAME)
    ex = bc["extra"]
    return {
        "schema": "evb.gm1@1",
        "source": {"sha12": hashlib.sha256(raw).hexdigest()[:12], "pageCount": len(texts)},
        "params": {"pageChars": PAGE_CHARS, "cap": CAP, "model": checker.MODEL_NAME, "maxTokens": checker.MAX_TOKENS},
        **bc,
        "dryRun": {"lowerEval": per_eval, "lowerJudged": dry["lower"]["judged"], "ruleCandidates": dry["rule_candidates"]},
        "price": price,
        "cost": {"b": cost(ex["b"], per_eval, price, checker.MAX_TOKENS),
                 "c": cost(ex["c"], per_eval, price, checker.MAX_TOKENS)},
    }


def to_md(rows: list[dict[str, Any]]) -> str:
    lines = ["# GM1 — 쪽 상한·2,500자 풂의 판정 후보 배수 (실비 0)", "",
             "| 권(sha12) | 쪽 | 잘린 쪽 | 글 없는 쪽 | ⓐ 기준선 | ⓑ 2,500자 풂 | ⓒ 둘 다 풂 | ⓑ/ⓐ | ⓒ/ⓐ | 순증 ⓑ | 순증 ⓒ(쪽 수) | 판정 한 건 입력 토큰 | ⓑ 순증 입력 USD | ⓒ 순증 입력 USD | ⓒ 순증 출력 상한 USD |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        t, ra, pe = r["totals"], r["ratio"], r["dryRun"]["lowerEval"]
        cb, cc = r["cost"]["b"], r["cost"]["c"]
        lines.append(f"| {r['source']['sha12']} | {r['pages']} | {r['pagesTruncated']} | {r['pagesEmpty']} | {t['a']} | {t['b']} | {t['c']} | "
                     f"{_fmt(ra['b/a'])} | {_fmt(ra['c/a'])} | {r['extra']['b']} | {r['extra']['c']}({r['pagesWhereCgtA']}) | {pe.get('perRequest', pe['status'])} | "
                     f"{cb.get('inputUsd', cb['status'])} | {cc.get('inputUsd', cc['status'])} | {cc.get('outputMaxUsd', cc['status'])} |")
    if rows:
        p = rows[0]["price"]
        lines += ["", f"단가: {p['model']} 입력 {p['input']} · 출력 {p['output']} {p['per']} — {p['file']} sha16 {p['sha16']} · 확인일 {p['checkedOn']}(항목 notes 의 확인일 따름).",
                  "ⓐ·ⓑ = 규칙 후보(상한 3) + AI 탐지 상한 3(글 있는 쪽). ⓒ = 쪽 글 전체의 환경 신호 문장 수. 순증 = 쪽마다 max(0, ⓧ−ⓐ) 의 합. 토큰은 o200k_base, 아래끝 eval 요청당 평균.",
                  "ⓐ 의 AI 탐지 3 은 상한(실제 AI 는 덜 낼 수 있다) — ⓐ 는 기준선의 위끝이고, 합 ⓒ/ⓐ<1 은 환경 문장이 3 보다 적은 쪽이 많다는 뜻."]
    return "\n".join(lines) + "\n"


def _fmt(r: dict[str, Any]) -> str:
    return str(r["value"]) if r["status"] == "measured" else r["status"]
