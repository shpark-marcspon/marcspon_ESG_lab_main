"""번역 정답 셋 짓개·읽개.

- `translation-sam-official` — 공식 영문판 짝 전량(`glossary_cands.pair_texts("sam")`). 항목에는 짝 id·쪽·블록 id·글 sha16 만
  두고 글은 읽을 때 다시 붙인다(`official_texts` — sha 가 다르면 ValueError, TRX7 꼴). 짝 맞춤은 코드(TRX1 align).
- `translation-glossary-<책>` — `gold-trx1/glossary/<책>.json` 을 가리키는 머리(+ 그 sha16·항목 수).
- `translation-review-trx7` — `gold-trx7/units.json`·`real.json` 을 가리키는 머리(+ sha16·갈래별 수).
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from ..common import gold as G
from . import GLOSS_REF_UNIT, MODULE, PAIR_UNIT, REVIEW_REF_UNIT

OFFICIAL_ID = "translation-sam-official"
REVIEW_ID = "translation-review-trx7"
GLOSS_BOOKS = ("sam", "cos", "kb")
_NO_REVIEW = {"status": "none", "by": None, "at": None, "note": None, "sample": None}


def _rel(p: Path, root: Path) -> str:
    return p.resolve().relative_to(root.resolve()).as_posix()


def build_official(*, at: str, root: Path, key: str = "sam") -> dict[str, Any]:
    from trlab.align import OUT as TRX1_OUT
    from trlab.glossary_cands import pair_texts
    from trlab.review_probe import sha16

    items = []
    for p, kt, et, _, _ in pair_texts(key):
        items.append({"id": "+".join(p["ko"]), "page": int(p["page"]), "ko": list(p["ko"]), "en": list(p["en"]),
                      "ko_sha16": sha16(kt), "en_sha16": sha16(et), "ko_len": len(kt)})
    af = Path(TRX1_OUT) / "align" / f"{key}.json"
    return G.new(
        set_id=OFFICIAL_ID if key == "sam" else f"translation-{key}-official",
        module=MODULE, unit=PAIR_UNIT, coverage="exhaustive", synthetic=False,
        provenance={"kind": "official_publication", "by": "공식 영문판(사람 번역) · 짝 맞춤 TRX1 align",
                    "from": [{"ref": _rel(af, root), "sha256": G.file_sha16(af)}], "at": at},
        review={"status": "agent_sampled", "by": "TRX1", "at": None,
                "note": "짝 맞춤은 코드(TRX1 align, 표본 50 라벨) — 번역 글은 공식판 그대로", "sample": None},
        items=items,
        meta={"book": key, "n_pairs": len(items)},
    )


def official_texts(gs: dict[str, Any]) -> list[dict[str, Any]]:
    """정답 짝 → `{id, page, ko, en, ko_ids, en_ids}`(글을 다시 붙임). sha16 이 정답과 다르면 ValueError."""
    from trlab.glossary_cands import pair_texts
    from trlab.review_probe import sha16

    live = {"+".join(p["ko"]): (p, kt, et) for p, kt, et, _, _ in pair_texts(gs["meta"]["book"])}
    out = []
    for it in gs["items"]:
        p, kt, et = live[it["id"]]
        if sha16(kt) != it["ko_sha16"] or sha16(et) != it["en_sha16"]:
            raise ValueError(f"{it['id']}: 공식 짝 글 sha 가 정답과 다르다")
        out.append({"id": it["id"], "page": it["page"], "ko": kt, "en": et, "ko_ids": it["ko"], "en_ids": it["en"]})
    return out


def build_glossary_ref(key: str, *, at: str, root: Path) -> dict[str, Any]:
    from trlab.common import GOLD as TRX1_GOLD

    f = Path(TRX1_GOLD) / "glossary" / f"{key}.json"
    n = len(json.loads(f.read_text(encoding="utf-8"))["items"])
    return G.new(
        set_id=f"translation-glossary-{key}", module=MODULE, unit=GLOSS_REF_UNIT, coverage="partial", synthetic=False,
        provenance={"kind": "runner_made", "by": "TRX1 용어집(공식판 짝에서 코드 후보 + 러너 고름)",
                    "from": [{"ref": _rel(f, root), "sha256": G.file_sha16(f)}], "at": at},
        review=dict(_NO_REVIEW), items=[],
        meta={"file": _rel(f, root), "sha16": G.file_sha16(f), "n_items": n},
    )


def build_review_ref(*, at: str, root: Path, gold7: Path) -> dict[str, Any]:
    uf, rf = gold7 / "units.json", gold7 / "real.json"
    units = json.loads(uf.read_text(encoding="utf-8"))["units"]
    counts = Counter(f"{u['split']}:{'clean' if u['cat'] == 'clean' else ('real' if u['cat'] == 'real' else 'planted')}" for u in units)
    return G.new(
        set_id=REVIEW_ID, module=MODULE, unit=REVIEW_REF_UNIT, coverage="partial", synthetic=False,
        provenance={"kind": "runner_made", "by": "TRX7 (심은 단위 = 러너가 심음, 실제 단위 = 엔진 산출에서 고름)",
                    "from": [{"ref": _rel(uf, root), "sha256": G.file_sha16(uf)}, {"ref": _rel(rf, root), "sha256": G.file_sha16(rf)}],
                    "at": at},
        review=dict(_NO_REVIEW), items=[],
        meta={"units": _rel(uf, root), "units_sha16": G.file_sha16(uf), "real": _rel(rf, root), "real_sha16": G.file_sha16(rf),
              "n_units": len(units), "counts": dict(sorted(counts.items()))},
    )


def check_ref(gs: dict[str, Any], root: Path) -> None:
    """가리킴 셋의 원 파일 sha16 이 그대로인지(바뀌었으면 ValueError)."""
    for f in gs["provenance"]["from"]:
        if isinstance(f, dict) and f.get("sha256") and G.file_sha16(root / f["ref"]) != f["sha256"]:
            raise ValueError(f"{gs['setId']}: {f['ref']} sha16 이 바뀜")
