"""보고서 1차 산출 `drafting.bullets@3` → 채점 단위 `drafting.leaf@1`.

잎 하나 = 단위 하나: `key` = 잎 `key`, `group` = 잎 `key`,
`payload = {text(bullet 글을 줄로 이음), bullets[{text, unsupported, n_sources}], numbers[≥3자 숫자 글자, 쉼표·공백 뺌],
titlePath, pages, outcome_status, warnings{code: 수}}`. 잎 `outcome.status` 가 done 이 아니면 `status = failed`.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from ..common.units import Origin, Source, Unit, converter
from . import MODULE, UNIT

SCHEMA = "drafting.bullets@3"
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def num(s: str) -> str:
    """쉼표·공백 뺌(SG1 `taskeval._num` 과 같은 셈)."""
    return re.sub(r"[,\s]", "", s)


def numbers(text: str) -> list[str]:
    """글의 숫자 글자(쉼표 묶음 포함) → 쉼표 뺀 꼴, 3자 이상만, 나온 차례(중복 유지)."""
    return [n for n in (num(m.group(0)) for m in _NUM_RE.finditer(text)) if len(n) >= 3]


@converter("drafting.bullets3@1")
def convert_bullets(result: dict[str, Any], *, run: str, **_: Any) -> list[Unit]:
    sv = result.get("schemaVersion")
    if sv != SCHEMA:
        raise ValueError(f"schemaVersion {sv!r} ≠ {SCHEMA}")
    origin = Origin("v1", sv, run, convert_bullets.name)
    warn: dict[str, Counter] = {}
    for w in result.get("warnings") or []:
        if isinstance(w, dict) and w.get("key"):
            warn.setdefault(w["key"], Counter())[str(w.get("code"))] += 1
    out = []
    for leaf in result.get("leaves") or []:
        oc = leaf.get("outcome") or {}
        bs = [b for b in (oc.get("bullets") or []) if isinstance(b, dict)]
        text = "\n".join(str(b.get("text") or "") for b in bs)
        srcs = []
        for b in bs:
            for s in b.get("sources") or []:
                if isinstance(s, dict):
                    srcs.append(Source("doc", f"{s.get('documentId')}:{s.get('blockId')}"))
        out.append(Unit(
            module=MODULE, unit=UNIT, key=str(leaf["key"]), group=str(leaf["key"]),
            payload={
                "text": text,
                "bullets": [{"text": str(b.get("text") or ""), "unsupported": bool(b.get("unsupported")),
                             "n_sources": len(b.get("sources") or [])} for b in bs],
                "numbers": numbers(text),
                "titlePath": leaf.get("titlePath"),
                "pages": leaf.get("pages"),
                "outcome_status": oc.get("status"),
                "warnings": dict(sorted(warn.get(leaf["key"], Counter()).items())),
            },
            sources=tuple(srcs),
            status="ok" if oc.get("status") == "done" else "failed",
            origin=origin,
        ))
    return out
