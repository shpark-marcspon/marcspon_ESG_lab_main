"""번역 1차 산출 → 채점 단위 `translation.block@1`.

- (a) `translation.result@1`(앱, docx·PDF 길) — `convert_result`: 변형(라벨 A·B, `+autofix` 판 따로) 하나 = 구획 하나.
  `translatedText` 를 `[PAGE n]` 머리로 쪽을 가르고(같은 번호 머리가 잇달아 오면 한 쪽), 결과 쪽 차례 i(1부터) →
  국문 PDF 쪽 = `page_offset + i` → `key = "page:<쪽>"`, `group = "<쪽>"`, `granularity = page`. `[PAGE]`·`[BLOCK k]`
  머리 줄은 걷는다(`[BLOCK k]` ↔ 국문 블록 id 짝은 못 닫음 — 쪽 단위로 잼).
- (b) trpdf res(`glossary_check.engine_pairs` 꼴 `{id, page, ko, en}`) — `convert_pairs`: `key = id`, `group = 쪽`,
  `granularity = block`. 공식판 짝(`official_pairs`)도 같은 꼴이라 같은 변환기로 단위가 된다(골든 ①).
"""
from __future__ import annotations

import re
from typing import Any

from ..common.units import Origin, Unit, converter
from . import MODULE, UNIT

SCHEMA = "translation.result@1"
_PAGE = re.compile(r"^[ \t]*\[PAGE (\d+)\][ \t]*$", re.M)
_HEAD = re.compile(r"^[ \t]*\[(?:PAGE|BLOCK) \d+\][ \t]*\n?", re.M)


def split_pages(text: str) -> list[tuple[int, str]]:
    """`[PAGE n]` 머리로 가름 → [(머리 번호, 쪽 글)] 차례. 같은 번호가 잇달아 오면 한 쪽으로 합친다. 첫 머리 앞 글은 버린다."""
    marks = list(_PAGE.finditer(text))
    out: list[tuple[int, str]] = []
    for j, m in enumerate(marks):
        n = int(m.group(1))
        body = text[m.end(): marks[j + 1].start() if j + 1 < len(marks) else len(text)]
        if out and out[-1][0] == n:
            out[-1] = (n, out[-1][1] + body)
        else:
            out.append((n, body))
    return out


def strip_heads(text: str) -> str:
    """`[PAGE n]`·`[BLOCK k]` 머리 줄을 걷고 줄 끝 공백·빈 줄을 정리."""
    t = _HEAD.sub("", text)
    lines = [ln.rstrip() for ln in t.splitlines()]
    return "\n".join(ln for ln in lines if ln.strip()).strip()


def _variant(result: dict[str, Any], label: str) -> dict[str, Any]:
    for v in result.get("variants") or []:
        if v.get("label") == label:
            return v
    raise ValueError(f"변형 {label!r} 없음")


@converter("translation.result-json@1")
def convert_result(result: dict[str, Any], *, run: str, variant: str, page_offset: int,
                   ko_pages: dict[int, str] | None = None, **_: Any) -> list[Unit]:
    """변형 하나(`"A"`·`"B"`·`"A+autofix"` …) → 쪽 단위. `ko_pages{국문 쪽: 국문 글}` 이 있으면 `payload.ko` 에 넣는다."""
    sv = result.get("schemaVersion")
    if sv != SCHEMA:
        raise ValueError(f"schemaVersion {sv!r} ≠ {SCHEMA}")
    label, _, fix = variant.partition("+")
    v = _variant(result, label)
    origin = Origin("v1", sv, run, convert_result.name)
    n_doc = len((result.get("document") or {}).get("pageNumbers") or [])
    # 남은 한국어 표시는 쪽 번호(pageNum = `[PAGE n]` 머리 번호)로 짝짓는다 — autofix.pages 에는 고친 쪽만 든다
    if fix:
        af = v.get("autofix") or {}
        text, ok = af.get("translatedText") or "", af.get("status") == "done"
        rk = {p.get("pageNum"): p.get("remainingKoreanAfter") for p in af.get("pages") or []}
    else:
        text, ok = v.get("translatedText") or "", True
        rk = {f.get("pageNum"): f.get("remainingKorean") for f in v.get("fragments") or []}
    pages = split_pages(text)
    if n_doc and len(pages) != n_doc:
        raise ValueError(f"{run} {variant}: [PAGE] 쪽 {len(pages)} ≠ document.pageNumbers {n_doc}")
    out = []
    for i, (head, body) in enumerate(pages, start=1):
        pg = page_offset + i
        out.append(Unit(
            module=MODULE, unit=UNIT, key=f"page:{pg}", group=str(pg),
            payload={"ko": (ko_pages or {}).get(pg), "en": strip_heads(body), "granularity": "page", "variant": variant,
                     "result_page": head, "remainingKorean": rk.get(head)},
            status=("ok" if strip_heads(body) else "missing") if ok else "failed",
            origin=origin,
        ))
    return out


@converter("translation.pairs@1")
def convert_pairs(pairs: list[dict[str, Any]], *, run: str, schema: str = "trlab.engine_pairs@1", **_: Any) -> list[Unit]:
    """`{id, page, ko, en}` 목록 → 블록 단위."""
    origin = Origin("v1", schema, run, convert_pairs.name)
    return [Unit(module=MODULE, unit=UNIT, key=str(p["id"]), group=str(p["page"]),
                 payload={"ko": p["ko"], "en": p["en"], "granularity": "block", "variant": run, "remainingKorean": None},
                 status="ok" if (p.get("en") or "").strip() else "missing", origin=origin)
            for p in pairs]
