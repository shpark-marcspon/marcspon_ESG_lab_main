"""GE1c 그린 근거 재현 셈(발주서 GE1c §2-2 + permits 「critic 반영」 B1·M3, 호출 0). 표준 라이브러리만.
구성 블록 셈은 흠 있음(감독 처분 — 도표 조각이 짧은 숫자라 아무 데서나 맞음, out/GE1c/rules.frozen.md).

셈 정의는 `out/GE1c/rules.draft.md` §3(실호출 전에 적음)과 같다:
  옛 셈   — core 단락 글 전체(norm)의 길이 min(60, 글 길이) 연속 창이 근거 글에 있음(MB1 `measure_mb1.found` 꼴).
  새 셈   — core 단락 글의 머리 줄(첫 줄바꿈 앞 — 섹션 제목·제목) 뒤만 같은 창으로.
  블록 셈 — core 단락의 구성 블록 글(bnorm, 빈 글 뺌)마다 근거 글(bnorm)에 들면 맞음, 맞은 블록 ≥ 절반이면 찾음.
  예산 자름 — 전체 차례 기록을 앞에서부터 글자 수를 더해 처음 넘는 단락에서 멈춤(`within_budget` 꼴), 건너뛰기 판도 둠.
경로·권 열쇠·회사 이름·단가 숫자를 이 파일에 쓰지 않는다. 실재 글은 인자로만 들어온다.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

RUN_LEN = 60
METRICS = ("old", "new", "block")
SCOPES = ("all", "body", "chart")


def norm(s: str) -> str:
    """NFKC + 공백 전부 지움."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def bnorm(s: str) -> str:
    """블록 셈 맞댐 글: NFKC + 공백·「/」·「|」 지움(표 칸 잇기 「 | 」 와 도표 원문 줄 「 / 」 가 양쪽에서 같아지게)."""
    return re.sub(r"[\s/|]+", "", unicodedata.normalize("NFKC", s or ""))


def head_body(text: str) -> str:
    """머리 줄(첫 줄바꿈 앞) 뒤. 줄바꿈이 없으면 글 전체."""
    return text.partition("\n")[2] if "\n" in text else text


def found_window(passage: str, evidence: str, *, run: int = RUN_LEN, skip_head: bool = False) -> bool:
    p = norm(head_body(passage) if skip_head else passage)
    e = norm(evidence)
    n = min(run, len(p))
    return n > 0 and any(p[i : i + n] in e for i in range(len(p) - n + 1))


def found_blocks(blocks: Sequence[str], evidence: str) -> bool:
    ts = [t for t in (bnorm(b) for b in blocks) if t]
    if not ts:
        return False
    e = bnorm(evidence)
    return 2 * sum(t in e for t in ts) >= len(ts)


def is_chart(text: str) -> bool:
    """도표 단락(GE1 셈 자리 규칙)."""
    return text.startswith("도표") or "\n도표:" in text[:200]


def cut(order: Sequence[str], length: Mapping[str, int], budget: int) -> list[str]:
    """처음 넘는 단락에서 멈춤(엔진 within_budget 꼴)."""
    used, got = 0, []
    for p in order:
        used += length[p]
        if used > budget:
            break
        got.append(p)
    return got


def cut_skip(order: Sequence[str], length: Mapping[str, int], budget: int) -> list[str]:
    """넘는 단락은 건너뛰고 다음을 봄(642-5 「continue 면」 판)."""
    used, got = 0, []
    for p in order:
        if used + length[p] > budget:
            continue
        used += length[p]
        got.append(p)
    return got


@dataclass(frozen=True)
class Core:
    id: str
    text: str
    blocks: tuple[str, ...]

    @property
    def chart(self) -> bool:
        return is_chart(self.text)


def score_core(core: Core, evidence: str) -> dict[str, bool]:
    return {
        "old": found_window(core.text, evidence),
        "new": found_window(core.text, evidence, skip_head=True),
        "block": found_blocks(core.blocks, evidence),
    }


def score_claim(cores: Iterable[Core], evidence: str) -> list[dict[str, object]]:
    return [{"id": c.id, "chart": c.chart, **score_core(c, evidence)} for c in cores]


def summarize(per_claim: Mapping[str, Sequence[Mapping[str, object]]]) -> dict[str, dict[str, dict[str, object]]]:
    """주장 → core 셈 목록 → {scope: {metric: {mean, n, found, cores}}}. 평균은 그 scope core 가 있는 주장만."""
    out: dict[str, dict[str, dict[str, object]]] = {}
    for scope in SCOPES:
        out[scope] = {}
        for m in METRICS:
            vals: list[float] = []
            hit = tot = 0
            for rows in per_claim.values():
                sel = [r for r in rows if scope == "all" or (scope == "chart") == bool(r["chart"])]
                if not sel:
                    continue
                k = sum(bool(r[m]) for r in sel)
                vals.append(k / len(sel))
                hit += k
                tot += len(sel)
            out[scope][m] = {
                "mean": round(sum(vals) / len(vals), 4) if vals else None,
                "n": len(vals),
                "found": hit,
                "cores": tot,
            }
    return out
