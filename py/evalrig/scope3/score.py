"""채점기 → `evalrig.scope3.score/1`(JSON) + md. 정답 단위마다 결과 단위를 열쇠로 짝짓는다.

카테고리마다:
- `n` — 정답 있는 줄(excluded·unreviewed 밖). `excluded`·`unreviewed` 는 따로 센다.
- `hit1` — 계수 열쇠 칸(정답이 가진 칸)과 계수 값(`Decimal` 같음)이 정답과 같음. 분모 = n − 계수 없는 정답 줄(`noFactor`).
  결과 줄이 없는 정답 줄은 `missing` 으로 분모 안(놓침).
- `hit3` — C1·C2 만: 정답 대표 제목이 결과 후보 **앞 셋** 안. 그 밖 카테고리는 `{"status": "n/a"}`.
- `classification` — C1·C2 만: 그룹·NAICS 각각과 둘 다(정답에 있는 칸만). 정답 분류가 없으면 `no_gold`.
- `co2e` — 줄마다 `|e−g|/|g|`(분모는 **정답**), g=0 이면 `zero_gold`(절대 오차), 한쪽만 비면 `missing`, 둘 다 비면 `both_empty`. 요약 = 오차 0 인
  줄 수·중앙값·최대(ok 줄만).
- `missingRows`(정답엔 있고 결과엔 없는 줄)·`extraRows`(결과에만 있는 줄 — 정답 셋이 그 카테고리를 갖는 것만).

맨 위 `targets`: 사업계획서 지표 둘(「분류 85%」 = C1·C2 분류 둘 다 맞은 비율, 「top-3 80%」 = C1·C2 hit3)을 목표와
나란히. 정답이 없으면 `{"status": "no_gold", "reason": …}`(0.0 이 아니다). 셈이 끝나면 종료 0(목표 미달은 종료 코드가 아님).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from evalrig.scope3.adapters import AdapterOutput
from evalrig.scope3.unit import CATEGORIES, CLASSIFIED, SCORE_SCHEMA, GoldSet, ToolFailure, Unit, same_decimal

HIT3_WINDOW = 3
TARGETS = {"classification": Decimal("0.85"), "top3": Decimal("0.80")}


def rate(hits: int, of: int) -> dict[str, Any]:
    """비율은 분모와 같이. 분모 0 이면 rate 는 None(0.0 이 아니다)."""
    return {"hits": hits, "of": of, "rate": None if of == 0 else str((Decimal(hits) / Decimal(of)).quantize(Decimal("0.0001")))}


def factor_hit(gold: Unit, got: Unit) -> bool:
    gf, ef = gold.factor, got.factor
    if gf is None:
        raise ValueError("계수 없는 정답 줄")  # 부르는 쪽이 noFactor 로 뺀다
    if ef is None:
        return False
    for name, value in gf.key_fields.items():
        if ef.key_fields.get(name) != value:
            return False
    return same_decimal(gf.value, ef.value)


def has_factor(u: Unit) -> bool:
    return u.factor is not None and (u.factor.value is not None or any(v is not None for v in u.factor.key_fields.values()))


def hit3(gold: Unit, got: Unit | None) -> bool:
    title = gold.factor.key_fields.get("대표EF") if gold.factor else None
    if got is None or title is None:
        return False
    return title in (got.candidates or ())[:HIT3_WINDOW]


@dataclass(frozen=True)
class Co2eRow:
    kind: str  # ok·zero_gold·missing(한쪽만 빔)·both_empty(둘 다 빔 — 어긋남도 오차 0 도 아님)
    error: Decimal | None


def co2e_error(gold: str | None, got: str | None) -> Co2eRow:
    if gold is None and got is None:
        return Co2eRow("both_empty", None)
    if gold is None or got is None:
        return Co2eRow("missing", None)
    g, e = Decimal(gold), Decimal(got)
    if g == 0:
        return Co2eRow("zero_gold", abs(e))
    return Co2eRow("ok", abs(e - g) / abs(g))


def _median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _num(d: Decimal | None) -> str | None:
    return None if d is None else format(d.normalize(), "f") if d == d.to_integral() else str(d.normalize())


def _part(c: dict[str, Any] | None, part: str) -> str | None:
    """분류 칸 하나의 글. 통째 absent(rows/1)·칸 absent·None 은 None."""
    if not isinstance(c, dict) or c.get("status") == "absent":
        return None
    v = c.get(part)
    return v if isinstance(v, str) else None


def _classification(gold_units: list[tuple[Unit, Unit | None]]) -> dict[str, Any]:
    """정답에 그 칸이 없으면 `no_gold`, 정답은 있는데 결과 쪽 분류가 통째로 없으면(rows/1) `no_result`(0 이 아니다)."""
    out: dict[str, Any] = {}
    for part in ("group", "naics", "all"):
        parts = ("group", "naics") if part == "all" else (part,)
        hits = of = with_result = 0
        for g, e in gold_units:
            present = [p for p in parts if _part(g.classification, p) is not None]
            if not present:
                continue
            of += 1
            if e is not None and isinstance(e.classification, dict) and e.classification.get("status") != "absent":
                with_result += 1
            hits += int(e is not None and all(_part(e.classification, p) == _part(g.classification, p) for p in present))
        if of == 0:
            out[part] = {"status": "no_gold", "reason": f"정답에 {part} 분류가 없다"}
        elif with_result == 0:
            out[part] = {"status": "no_result", "reason": "결과 쪽 분류가 없다(rows/1 에 묶음·NAICS 칸 없음)"}
        else:
            out[part] = rate(hits, of)
    return out


def score(gold: GoldSet, result: AdapterOutput) -> dict[str, Any]:
    if gold.key_by != result.key_by:
        raise ToolFailure(f"열쇠 갈래가 다르다 — 정답 {gold.key_by} · 결과 {result.key_by}(짝짓지 않는다)")
    by_key: dict[tuple[Any, ...], Unit] = {}
    for u in result.units:
        if u.key.ident in by_key:
            raise ToolFailure(f"결과 열쇠 {u.key.ident} 가 둘 이상")
        by_key[u.key.ident] = u
    gold_keys = {u.key.ident for u in gold.units}
    categories: dict[str, Any] = {}
    rows_out: list[dict[str, Any]] = []
    for cat in CATEGORIES:
        cmeta = gold.meta["categories"][cat]
        if cmeta.get("status") != "present":
            categories[cat] = {"status": "no_gold", "reason": cmeta.get("reason")}
            continue
        units = [u for u in gold.units if u.category == cat]
        judged = [u for u in units if u.gold is None]
        excluded = sum(1 for u in units if u.gold == "excluded")
        unreviewed = sum(1 for u in units if u.gold == "unreviewed")
        pairs = [(g, by_key.get(g.key.ident)) for g in judged]
        missing = [g for g, e in pairs if e is None]
        extra = [u for u in result.units if u.category == cat and u.key.ident not in gold_keys]
        hit = miss = no_factor = 0
        co2e_rows: list[Co2eRow] = []
        for g, e in pairs:
            row: dict[str, Any] = {"category": cat, "key": g.key.to_json()}
            if not has_factor(g):
                no_factor += 1
                row["hit1"] = "noFactor"
            elif e is None:
                row["hit1"] = "missing"
            elif factor_hit(g, e):
                hit += 1
                row["hit1"] = True
            else:
                miss += 1
                row["hit1"] = False
            c = co2e_error(g.co2e, e.co2e if e else None)
            co2e_rows.append(c)
            row["co2e"] = {"kind": c.kind, "error": _num(c.error)}
            if cat in CLASSIFIED:
                row["hit3"] = hit3(g, e)
            rows_out.append(row)
        h1 = rate(hit, hit + miss + sum(1 for g, e in pairs if e is None and has_factor(g)))
        h1 |= {"miss": miss, "missing": sum(1 for g, e in pairs if e is None and has_factor(g)), "noFactor": no_factor}
        oks = [c.error for c in co2e_rows if c.kind == "ok" and c.error is not None]
        zero_err = sum(1 for c in co2e_rows if c.kind in ("ok", "zero_gold") and c.error == 0)
        cat_out: dict[str, Any] = {
            "status": "scored",
            "n": len(judged),
            "excluded": excluded,
            "unreviewed": unreviewed,
            "hit1": h1,
            "co2e": {
                "ok": len(oks),
                "zeroGold": sum(1 for c in co2e_rows if c.kind == "zero_gold"),
                "missing": sum(1 for c in co2e_rows if c.kind == "missing"),
                "bothEmpty": sum(1 for c in co2e_rows if c.kind == "both_empty"),
                "zeroError": zero_err,
                "median": _num(_median(oks)),
                "max": _num(max(oks) if oks else None),
            },
            "missingRows": len(missing),
            "extraRows": len(extra),
        }
        if cat in CLASSIFIED:
            scorable = [(g, e) for g, e in pairs if has_factor(g) and g.factor and g.factor.key_fields.get("대표EF")]
            cat_out["hit3"] = rate(sum(1 for g, e in scorable if hit3(g, e)), len(scorable)) | {"window": HIT3_WINDOW}
            cat_out["classification"] = _classification(pairs)
        else:
            cat_out["hit3"] = {"status": "n/a"}
        categories[cat] = cat_out
    failed = [s for s in result.sheets if s.status in ("failed", "not_yet_supported")]
    return {
        "schema": SCORE_SCHEMA,
        "gold": {"source": gold.meta.get("source"), "keyBy": gold.key_by, "producedBy": gold.meta.get("producedBy"),
                 "reviewed": gold.meta.get("reviewed")},  # fmt: skip
        "result": {"adapter": result.adapter, "rows": len(result.units), "sheetsFailed": len(failed),
                   "failedSheets": [{"category": s.category, "status": s.status} for s in failed], "notes": result.notes},  # fmt: skip
        "targets": _targets(gold, categories),
        "categories": categories,
        "rows": rows_out,
    }


def _targets(gold: GoldSet, categories: dict[str, Any]) -> dict[str, Any]:
    """C1·C2 를 합친 지표 둘. 셀 칸이 없으면 no_gold(까닭은 정답 메타) 또는 no_result — 0.0 으로 접지 않는다."""
    out: dict[str, Any] = {}
    for name, path in (("classification", ("classification", "all")), ("top3", ("hit3",))):
        hits = of = 0
        statuses: list[dict[str, Any]] = []
        for cat in sorted(CLASSIFIED):
            node: Any = categories.get(cat)
            for p in path:
                node = node.get(p) if isinstance(node, dict) and node.get("status") != "no_gold" else node
            if isinstance(node, dict) and "of" in node:
                hits += node["hits"]
                of += node["of"]
            elif isinstance(node, dict) and "status" in node:
                statuses.append(node)
        target = str(TARGETS[name])
        if of:
            got = rate(hits, of)
            out[name] = got | {"target": target, "met": Decimal(str(got["rate"])) >= TARGETS[name]}
            continue
        kind = "no_result" if any(st.get("status") == "no_result" for st in statuses) else "no_gold"
        reasons = sorted({str(st.get("reason")) for st in statuses if st.get("status") == kind})
        out[name] = {"status": kind, "reason": " · ".join(reasons) or "C1·C2 정답 줄 가운데 셀 칸이 없다", "target": target}
    return out


def render_md(report: dict[str, Any]) -> str:
    """숫자·카테고리 이름만(품목 글·열쇠 글 0)."""
    lines = ["# Scope 3 채점 (evalrig.scope3.score/1)", ""]
    g, r = report["gold"], report["result"]
    sha = (g.get("source") or {}).get("sha256")
    lines.append(f"- 정답: {g['producedBy']} · reviewed {g['reviewed']} · keyBy {g['keyBy']}" + (f" · sha256 {sha[:16]}" if sha else ""))
    lines.append(f"- 결과: {r['adapter']} · 줄 {r['rows']} · 1차 시트 실패 {r['sheetsFailed']}")
    for name, t in report["targets"].items():
        label = "분류 85%" if name == "classification" else "top-3 80%"
        if t.get("status") in ("no_gold", "no_result"):
            lines.append(f"- {label}: {t['status']} — {t['reason']}")
        else:
            lines.append(f"- {label}: {t['hits']}/{t['of']} ({t['rate']}) · 목표 {t['target']} · {'충족' if t['met'] else '미달'}")
    lines += ["", "| 카테고리 | n | 제외 | 미검토 | hit1 | 놓친 줄 | 계수 없음 | hit3 | 오차 0 | 중앙값 | 최대 | 결과에만 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for cat, c in report["categories"].items():
        if c.get("status") == "no_gold":
            lines.append(f"| {cat} | no_gold | | | | | | | | | | |")
            continue
        h1, h3, e = c["hit1"], c["hit3"], {k: ("—" if v is None else v) for k, v in c["co2e"].items()}
        h3s = "n/a" if h3.get("status") == "n/a" else f"{h3['hits']}/{h3['of']}"
        lines.append(
            f"| {cat} | {c['n']} | {c['excluded']} | {c['unreviewed']} | {h1['hits']}/{h1['of']} | {c['missingRows']} | {h1['noFactor']} | {h3s} | "
            f"{e['zeroError']} | {e['median']} | {e['max']} | {c['extraRows']} |"
        )
    absent = [f"- {cat}: {c['reason']}" for cat, c in report["categories"].items() if c.get("status") == "no_gold"]
    if absent:
        lines += ["", "no_gold 까닭:", *absent]
    return "\n".join(lines) + "\n"
