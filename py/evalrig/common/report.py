"""보고 표 — `write(out_dir, name, rows, gold_sets)` → `<name>.json` + `<name>.md`.

규칙(발주서 EVA §2 (1)):
- 표 머리에 정답 셋마다 `setId · sha16 · provenance · review · coverage · synthetic · pending` 한 줄을
  **늘** 찍는다. 정답 셋 메타가 빠지면 ValueError. 정답이 없는 코드 지표 표는 `NO_GOLD` 를 넘겨
  「정답 셋 없음」 줄을 찍는다(빈 목록·None 은 ValueError — 빠뜨림과 「없음」을 가른다).
- 모든 줄의 첫 칸 `gold` = 그 줄이 쓴 정답 셋 setId(또는 `NO_GOLD`) — md 에서는 「정답」 칸에
  `setId(provenance/review)` 로 찍는다.
- 정답 셋은 `gold.check` 를 다시 거친다 — `frozen.sha16` 이 내용과 다르면 ValueError.
- % 칸(이름이 `%` 로 끝나거나 `rate·recall·precision·f1·비율·재현·정밀` 이 든 칸)은 `match.ratio` 꼴만
  (분모를 같이, F1 은 `den: None` 이라 분모 없이 찍힘). 그 밖의 float 도 받되 md 에는 소수 셋째 자리로.
- coverage=partial 셋의 정밀 칸을 막는 것은 부르는 쪽(`gold.guard_precision` → `match.unmeasurable`) 몫이다.
- 실행 시각·evalrig 코드 sha16 은 json 에만 — md 는 다시 뽑아도 바이트가 같다(`cmp`).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from . import gold as G
from .match import is_ratio

SCHEMA_ID = "evalrig.report@1"
NO_GOLD = "NO_GOLD"

_PKG = Path(__file__).resolve().parents[1]


def code_sha16(root: Path = _PKG) -> str:
    """evalrig 코드(시험 뺀 *.py)의 sha256 앞 16자 — 상대 경로 차례로 경로·바이트를 이어 셈."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        if rel.startswith("tests/") or "__pycache__" in rel:
            continue
        h.update(rel.encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()[:16]


_PCT_WORDS = ("rate", "recall", "precision", "f1")
_PCT_KO = ("비율", "재현", "정밀")


def _is_pct_col(c: str) -> bool:
    lc = c.lower()
    return c.endswith("%") or any(w in lc for w in _PCT_WORDS) or any(w in c for w in _PCT_KO)


def _gold_headers(gold_sets: Any) -> list[dict[str, Any]] | str:
    if gold_sets == NO_GOLD:
        return NO_GOLD
    if not gold_sets:
        raise ValueError("gold_sets 가 비었음 — 정답이 없으면 NO_GOLD 를 넘긴다")
    out = []
    for gs in gold_sets:
        if not isinstance(gs, dict):
            raise ValueError("gold_sets 는 정답 셋 dict 의 목록")
        try:
            G.check(gs, require_unit=False)  # 꼴 + frozen.sha16 ↔ 내용 대조(얼린 뒤 고침 적발)
            h = G.header(gs)
        except G.GoldError as e:
            raise ValueError(f"정답 셋 {gs.get('setId')!r} 메타 틀림: {e}") from None
        except KeyError as e:
            raise ValueError(f"정답 셋 메타 빠짐: {e}") from None
        if not h["setId"] or not h["sha16"]:
            raise ValueError(f"정답 셋 {h.get('setId')!r}: setId·frozen.sha16 필수(얼린 셋만 보고)")
        if h["provenance"] not in G.PROVENANCE_KINDS or h["review"] not in G.REVIEW_STATUSES:
            raise ValueError(f"정답 셋 {h['setId']}: provenance·review 갈래 틀림")
        out.append(h)
    ids = [h["setId"] for h in out]
    if len(set(ids)) != len(ids):
        raise ValueError("gold_sets 의 setId 중복")
    return out


def fmt(v: Any) -> str:
    """md 칸 글."""
    if is_ratio(v):
        if v.get("status") == "no_denominator":
            return f"— ({v['num']}/0)"
        if v.get("status") == "unmeasurable":
            return f"셀 수 없음({v.get('why', '')})"
        pct = f"{v['value'] * 100:.1f}%"
        return pct if v.get("den") is None else f"{pct} ({_n(v['num'])}/{_n(v['den'])})"
    if isinstance(v, bool):
        return "예" if v else "아니오"
    if isinstance(v, float):
        return f"{v:.3f}"
    if v is None:
        return "—"
    if isinstance(v, (list, tuple)):
        return ", ".join(fmt(x) for x in v)
    if isinstance(v, dict):
        return _esc(json.dumps(v, ensure_ascii=False, sort_keys=True))
    return _esc(str(v))


def _esc(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def _n(x: Any) -> str:
    return f"{x:g}" if isinstance(x, float) else str(x)


def render_md(
    name: str,
    rows: Sequence[dict[str, Any]],
    gold_sets: Any,
    *,
    columns: Sequence[str] | None = None,
    title: str | None = None,
    notes: Sequence[str] = (),
) -> str:
    """md 글(시각·코드 sha 없음 — 결정적)."""
    heads = _gold_headers(gold_sets)
    known = {NO_GOLD} if heads == NO_GOLD else {h["setId"] for h in heads}
    if heads != NO_GOLD:
        known.add(NO_GOLD)  # 한 표 안에 정답 없는 줄(코드 지표)이 섞여도 됨
    label = {NO_GOLD: "정답 없음"}
    if heads != NO_GOLD:
        for h in heads:
            label[h["setId"]] = f"{h['setId']}({h['provenance']}/{h['review']})"
    cols: list[str] = []
    if columns is not None:
        cols = [c for c in columns if c != "gold"]
    else:
        for r in rows:
            for c in r:
                if c != "gold" and c not in cols:
                    cols.append(c)
    for i, r in enumerate(rows):
        g = r.get("gold")
        if g not in known:
            raise ValueError(f"rows[{i}].gold {g!r} 가 gold_sets 에 없음")
        for c in cols:
            v = r.get(c)
            if _is_pct_col(c) and v is not None and not is_ratio(v):
                raise ValueError(f"rows[{i}].{c}: % 칸은 ratio 꼴(분모 같이)")
    lines = [f"# {title or name}", ""]
    lines.append("## 정답 셋")
    lines.append("")
    if heads == NO_GOLD:
        lines.append("- 정답 셋 없음 — 코드 지표만")
    else:
        for h in heads:
            pend = ", ".join(h["pending"]) or "없음"
            lines.append(
                f"- `{h['setId']}` · sha16 `{h['sha16']}` · provenance `{h['provenance']}` · review `{h['review']}`"
                f" · coverage `{h['coverage']}` · synthetic `{str(h['synthetic']).lower()}` · 항목 {h['n']} · pending {pend}"
            )
    lines += ["", "## 표", ""]
    hdr = ["정답", *(_esc(c) for c in cols)]
    lines.append("| " + " | ".join(hdr) + " |")
    lines.append("|" + "---|" * len(hdr))
    for r in rows:
        lines.append("| " + " | ".join([label[r["gold"]], *(fmt(r.get(c)) for c in cols)]) + " |")
    if notes:
        lines += ["", "## 메모", ""]
        lines += [f"- {n}" for n in notes]
    return "\n".join(lines) + "\n"


def write(
    out_dir: str | Path,
    name: str,
    rows: Sequence[dict[str, Any]],
    gold_sets: Any,
    *,
    columns: Sequence[str] | None = None,
    title: str | None = None,
    notes: Sequence[str] = (),
    extra: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    """`<out_dir>/<name>.json`·`.md` 를 쓰고 두 경로를 돌려준다."""
    md = render_md(name, rows, gold_sets, columns=columns, title=title, notes=notes)
    heads = _gold_headers(gold_sets)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jp, mp = out / f"{name}.json", out / f"{name}.md"
    doc = {
        "schema": SCHEMA_ID,
        "name": name,
        "title": title or name,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "evalrigSha16": code_sha16(),
        "gold": heads,
        "columns": list(columns) if columns is not None else None,
        "rows": list(rows),
        "notes": list(notes),
    }
    if extra:
        doc["extra"] = extra
    jp.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
    mp.write_text(md, encoding="utf-8")
    return jp, mp
