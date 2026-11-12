"""보고서 정답 셋 짓개 — SG1 책 넷의 사실(`drafting.fact@1`) → `prework/gold-eva/drafting-sg1-<책>.json`.

책 자리는 `semgraph.sg1d.BOOKS` 의 차례(첫째~넷째)로 고른다 — 코드에 책 이름·경로 글자를 쓰지 않는다.
- 첫째~셋째: `<out>/sg1d/task/facts.json`({질의: {facts}}) · 질의 `<out>/queries-passage.json` ·
  핵심 단락 `<out>/gold-final.json` · 단락 글 `<out>/passages.json`(사실은 taskeval `--gold gold-final.json` 으로 뽑힘).
- 넷째: `<out>/sg1i/facts.part.jsonl`(줄 `{k, v:{facts}}`) · `<out>/sg1i/queries.json` · `<out>/sg1i/gold.json` ·
  `<out>/sg1g/passages.json`.

메타(발주서 EVA §2 (3), 넷째 책은 감독 답 — 검증 2 B1): 첫째~셋째 provenance `agent_draft_checked`(사실 = 모델이 뽑고
코드로 거름, 핵심 단락 = 에이전트 초안 + 독립 판정 + 20% 표본) · review `agent_sampled` sample 118/127(세 책 합친 15 질의) ·
넷째 provenance `agent_draft` · review `none`(SG1i 별도 에이전트 초안, 표본 검토 없음) · coverage `partial`(뽑힌 사실만) · synthetic false ·
pending 협의 엑셀·확정 원고(`MARC-ENT-607`). `meta.leaves{질의: {text(=leafText), core[단락 id]}}`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..common import gold as G
from . import GOLD_UNIT, MODULE

SET_IDS = ("sam", "cos", "kb", "b4")  # 책 차례 → setId 꼬리(넷째 책 이름은 이름 목록에 걸려 차례로 부른다)
SAMPLE = {"ok": 118, "n": 127}


def book_paths(i: int, out_root: Path) -> dict[str, Path]:
    """책 차례 i(0~3) → 파일 자리(메인 체크아웃 `esg-lab/out/<폴더>`)."""
    from semgraph.sg1d import BOOKS

    name = list(BOOKS)[i]
    d = out_root / Path(BOOKS[name][1]).name
    if i < 3:
        return {"facts": d / "sg1d" / "task" / "facts.json", "queries": d / "queries-passage.json",
                "core": d / "gold-final.json", "passages": d / "passages.json"}
    return {"facts": d / "sg1i" / "facts.part.jsonl", "queries": d / "sg1i" / "queries.json",
            "core": d / "sg1i" / "gold.json", "passages": d / "sg1g" / "passages.json"}


def read_facts(path: Path) -> dict[str, list[dict[str, Any]]]:
    if path.suffix == ".jsonl":
        out = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                out[r["k"]] = (r["v"] or {}).get("facts") or []
        return out
    return {k: (v or {}).get("facts") or [] for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def _qorder(k: str) -> tuple[int, str]:
    digits = "".join(c for c in k if c.isdigit())
    return (int(digits) if digits else 0, k)


def _review(i: int) -> dict[str, Any]:
    """첫째~셋째: 독립 판정 표본(세 책 합친 15 질의 5·6·4 — 118/127). 넷째: 별도 에이전트 초안, 표본 검토 없음."""
    if i < 3:
        return {"status": "agent_sampled", "by": "SG1 독립 판정", "at": None,
                "note": "단락 표본 정밀 — 표본 118/127 은 세 책 합친 15 질의(5·6·4), 사실 단위는 사람 확인 없음",
                "sample": dict(SAMPLE)}
    return {"status": "none", "by": None, "at": None, "note": "SG1i 별도 에이전트 초안, 표본 검토 없음", "sample": None}


def build(i: int, paths: dict[str, Path], *, at: str, rel_to: Path) -> dict[str, Any]:
    facts = read_facts(paths["facts"])
    qs = {q["id"]: q for q in json.loads(paths["queries"].read_text(encoding="utf-8"))}
    core = json.loads(paths["core"].read_text(encoding="utf-8"))
    items, leaves = [], {}
    for k in sorted(facts, key=_qorder):
        fs = facts[k]
        if not fs:
            continue
        leaves[k] = {"text": qs[k]["text"], "core": [x["id"] for x in core.get(k, []) if x.get("label") == "core"]}
        for j, f in enumerate(fs):
            items.append({"leaf": k, "idx": j, "metric": str(f.get("metric", "")), "value": str(f.get("value", "")),
                          "unit": str(f.get("unit", "")), "year": str(f.get("year", "")), "match": list(f["match"])})
    frm = [{"ref": p.relative_to(rel_to).as_posix(), "sha256": G.file_sha16(p)} for _, p in sorted(paths.items())]
    return G.new(
        set_id=f"drafting-sg1-{SET_IDS[i]}",
        module=MODULE,
        unit=GOLD_UNIT,
        coverage="partial",
        synthetic=False,
        provenance={"kind": "agent_draft_checked" if i < 3 else "agent_draft", "by": "SG1 (모델 추출 + 코드 거름)", "from": frm, "at": at},
        review=_review(i),
        pending=[{"what": "협의 엑셀·확정 원고 한 쌍", "question": "MARC-ENT-607", "slot": "consultant"}],
        items=items,
        meta={"book_index": i, "leaves": leaves, "n_leaves": len(leaves)},
    )


def passages(paths: dict[str, Path]) -> dict[str, str]:
    """단락 id → 글(골든 ③ 용)."""
    return {p["id"]: p["text"] for p in json.loads(paths["passages"].read_text(encoding="utf-8"))}
