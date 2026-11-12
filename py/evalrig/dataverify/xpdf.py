"""데이터 시험지 ② 엑셀 원천 ↔ PDF 대사 — KB 2025(공개 보고서) 문서 a 의 지표 칸(발주서 EVB §2-7).

원천은 git 에 이미 있는 `data/reconcile/gold/kb-2025.gold.json` 의 문서 a `kind:"indicator"` 칸이다(`raw` 를 수로).
연도(period)가 없는 칸은 쓰지 않고 수만 보고한다(`kb-2025.pairs.json` 에도 짝이 없음 — 경계 0 실측).
엑셀은 git 밖(`out/evb/xpdf/`)에만 짓는다. git 에는 이 생성기와 심은 목록·정답만 싣는다(감독 답 Q1).

심기(seed 고정 난수로 지표를 고름 — 손으로 고르지 않음, 한 지표에 심기 하나):
digit(한 자리) ×2 · unit_scale(1,000배) ×2 · rounding(반올림 경계 — 원천에 소수가 붙고 보고서 글자로는 같음) ×1 ·
year_swap(두 해 값 바꿔치기) ×1 · source_only(원천에만 있는 행 — 값은 지은 것) ×1 · report_only(원천에서 뺀 행) ×1.
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any

from evalrig.dataverify import gold as G

SEED = 20261110
DOC = "a"
SOURCE_ONLY_YEARS = ("2023", "2024", "2025")
HEADER = ["ID", "테마", "항목", "대분류", "중분류", "소분류", "입력 방식", "단위", "2023", "2024", "2025", "핵심데이터", "메모"]
PLAN = ["report_only", "year_swap", "digit", "digit", "unit_scale", "unit_scale", "rounding"]  # + source_only(목록 밖 지표)


def parse_raw(raw: str) -> int | float:
    s = raw.replace(",", "").strip()
    return float(s) if "." in s else int(s)


def indicator_cells(kb_gold: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """(연도 있는 문서 a 지표 칸, 연도 없는 칸 id). 칸 = {indicator, period, raw, unit, cell}."""
    cells, no_year = [], []
    for tb in kb_gold["tables"]:
        if tb["doc"] != DOC:
            continue
        for c in tb["cells"]:
            if c["kind"] != "indicator":
                continue
            cid = f"{tb['tableId']}:{c['cell']}"
            if c["period"] is None:
                no_year.append(cid)
                continue
            cells.append({"indicator": c["indicator"], "period": c["period"], "raw": c["raw"], "unit": c["unit"], "cell": cid})
    return cells, no_year


def _change_digit(v: int, rng: random.Random) -> int:
    s = str(v)
    pos = rng.randrange(len(s))
    choices = [d for d in "0123456789" if d != s[pos] and not (pos == 0 and d == "0")]
    return int(s[:pos] + rng.choice(choices) + s[pos + 1:])


def build(kb_gold: dict[str, Any], catalog: dict[str, Any], *, seed: int = SEED, kb_gold_sha16: str = "", catalog_sha16: str = ""):
    """(source_rows, plants, gold). source_rows 는 엑셀 행(git 밖), 나머지 둘은 git."""
    cells, no_year = indicator_cells(kb_gold)
    labels = {i["key"]: i for i in catalog["indicators"]}
    by_ind: dict[str, list[dict[str, Any]]] = {}
    for c in cells:
        by_ind.setdefault(c["indicator"], []).append(c)
    for k in by_ind:
        units = {c["unit"] for c in by_ind[k]}
        if len(units) != 1:
            raise ValueError(f"{k}: 단위가 둘 이상 {units}")
    rng = random.Random(seed)
    order = sorted(by_ind)
    rng.shuffle(order)
    swap_ok = [k for k in order if len(by_ind[k]) >= 2]
    chosen: dict[str, str] = {}
    pool = list(order)
    for kind in PLAN:
        cand = [k for k in (swap_ok if kind == "year_swap" else pool) if k not in chosen]
        chosen[cand[0]] = kind
        pool.remove(cand[0])

    values = {k: {c["period"]: parse_raw(c["raw"]) for c in v} for k, v in by_ind.items()}
    src_vals = {k: dict(v) for k, v in values.items()}
    plants: list[dict[str, Any]] = []
    verdict: dict[tuple[str, str], tuple[str, str | None, list[str]]] = {
        (c["indicator"], c["period"]): ("match", None, []) for c in cells
    }
    cell_of = {(c["indicator"], c["period"]): c for c in cells}
    n = 0
    for k in sorted(chosen, key=order.index):
        kind = chosen[k]
        n += 1
        ev = f"X{n:02d}"
        yrs = sorted(values[k])
        if kind == "report_only":
            del src_vals[k]
            for y in yrs:
                c = cell_of[(k, y)]
                plants.append({"event": ev, "kind": kind, "indicator": k, "period": y, "cell": c["cell"], "original": c["raw"], "planted": None})
                verdict[(k, y)] = ("report_only", kind, [])
            continue
        if kind == "year_swap":
            y1, y2 = sorted(rng.sample(yrs, 2))
            if values[k][y1] == values[k][y2]:  # pragma: no cover — KB 칸에는 없음
                raise ValueError(f"{k}: 바꿔도 같은 값")
            src_vals[k][y1], src_vals[k][y2] = values[k][y2], values[k][y1]
            for y in (y1, y2):
                c = cell_of[(k, y)]
                plants.append({"event": ev, "kind": kind, "indicator": k, "period": y, "cell": c["cell"], "original": c["raw"], "planted": src_vals[k][y]})
                verdict[(k, y)] = ("mismatch", kind, [])
            continue
        y = rng.choice(yrs)
        v = values[k][y]
        if kind == "digit":
            nv = _change_digit(int(v), rng)
            vd = ("mismatch", kind, [])
        elif kind == "unit_scale":
            nv = v * 1000
            vd = ("mismatch", kind, [])
        elif kind == "rounding":
            nv = round(v + 0.4, 1)  # 보고서 글자(정수)로 반올림하면 원래 값 — 허용(match), mismatch 도 accept
            vd = ("match", kind, ["mismatch"])
        else:  # pragma: no cover
            raise ValueError(kind)
        src_vals[k][y] = nv
        c = cell_of[(k, y)]
        plants.append({"event": ev, "kind": kind, "indicator": k, "period": y, "cell": c["cell"], "original": c["raw"], "planted": nv})
        verdict[(k, y)] = vd

    # source_only: 목록에 있고 문서 a 에 없는 온실가스 지표 하나, 값은 지은 것
    extra = sorted(i["key"] for i in catalog["indicators"] if i["key"] not in by_ind and i.get("unitFamily") == "mass_co2e")
    so = rng.choice(extra)
    n += 1
    ev = f"X{n:02d}"
    src_vals[so] = {y: rng.randint(100, 9999) for y in SOURCE_ONLY_YEARS}
    for y in SOURCE_ONLY_YEARS:
        plants.append({"event": ev, "kind": "source_only", "indicator": so, "period": y, "cell": None, "original": None, "planted": src_vals[so][y]})
        verdict[(so, y)] = ("source_only", "source_only", [])
    units = {k: v[0]["unit"] for k, v in by_ind.items()}
    units[so] = "tCO2eq"

    rows = []
    for k in sorted(src_vals):
        lab = labels.get(k, {}).get("label", k)
        rows.append({
            "ID": k, "테마": "환경" if k.startswith("ghg.") else "경영 일반", "항목": lab,
            "대분류": "온실가스 배출량" if k.startswith("ghg.") else "재무", "중분류": k, "소분류": lab,
            "입력 방식": "직접 입력", "단위": units[k], "values": {y: src_vals[k][y] for y in sorted(src_vals[k])},
            "핵심데이터": "", "메모": "",
        })

    plants_doc = {
        "note": "엑셀 원천 ↔ PDF 대사 시험지에 심은 것. 원천(엑셀, git 밖)에만 심는다. 지표는 seed 고정 난수로 골랐다(손으로 고르지 않음). "
                "original 은 kb-2025.gold.json 의 그 칸 raw 글자 그대로다.",
        "status": "러너 작성, 전문가 검토 전 (밴드 EVB, 2026-11-09)",
        "seed": seed,
        "sources": {"kbGold": f"data/reconcile/gold/kb-2025.gold.json sha16={kb_gold_sha16}",
                    "catalog": f"data/reconcile/indicators.synthetic.json sha16={catalog_sha16}"},
        "events": n,
        "excludedNoYear": no_year,
        "plants": plants,
    }
    items = []
    for (k, y) in sorted(verdict):
        v, kind, acc = verdict[(k, y)]
        c = cell_of.get((k, y))
        items.append({"indicator": k, "year": y, "verdict": v, "kind": kind, "accept": acc, "cell": c["cell"] if c else None})
    conventions = {
        "labels": ["match", "mismatch", "source_only", "report_only"],
        "kinds": ["digit", "unit_scale", "rounding", "year_swap", "source_only", "report_only"],
        "rules": [
            "원천 = 엑셀(지은 시험지), 보고서 = 공개 PDF(그대로). 칸마다 원천 값과 보고서 글자 값을 견준 정답.",
            "rounding 은 원천에 소수 0.4 를 붙인 것 — 보고서 글자로 반올림하면 같다: match(대조군), mismatch 도 accept.",
            "year_swap 은 두 칸 모두 mismatch. report_only 는 원천에서 행을 뺀 지표의 보고서 칸 전부. source_only 는 원천에만 있는 행의 해마다.",
            f"문서 a 지표 칸 중 연도 없는 {len(no_year)} 칸은 정답 밖(meta.excludedNoYear).",
            "1차(colab_156)에는 두 문서 대사가 없다 — 이 시험지의 1차 점수는 not_applicable.",
        ],
        "accept": "items[].accept — 돌리기 전에 정했다.",
    }
    meta = {
        "report": {"fileName": kb_gold["documents"][DOC]["fileName"], "pdfSha256": kb_gold["documents"][DOC]["pdfSha256"]},
        "plants": "kb-2025-xpdf.plants.json",
        "cellsUsed": len(cells),
        "excludedNoYear": no_year,
        "v1Score": {"status": "not_applicable", "reason": "1차(colab_156)에 두 문서 대사 없음"},
    }
    gs = G.header(
        set_id="dataverify.kb-2025-xpdf",
        coverage="exhaustive",
        synthetic=False,
        sources=[f"data/reconcile/gold/kb-2025.gold.json sha16={kb_gold_sha16}",
                 f"evalrig.dataverify.xpdf seed={seed}"],
        conventions=conventions,
        meta=meta,
    )
    gs["items"] = items
    return rows, plants_doc, gs


def pdf_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_xlsx(rows: list[dict[str, Any]], path: str | Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "data"
    ws.append(HEADER)
    for r in rows:
        ws.append([r[h] if h in r else r["values"].get(h) for h in HEADER])
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))


def read_xlsx(path: str | Path) -> list[dict[str, Any]]:
    """시트 값을 다시 읽어 원천 행 꼴로(결정성은 바이트가 아니라 이 값으로 잰다 — zip 시각)."""
    from openpyxl import load_workbook

    ws = load_workbook(str(path), read_only=True).active
    it = ws.iter_rows(values_only=True)
    head = list(next(it))
    out = []
    for row in it:
        d = dict(zip(head, row))
        vals = {y: d.pop(y) for y in ("2023", "2024", "2025") if d.get(y) is not None}
        for y in ("2023", "2024", "2025"):
            d.pop(y, None)
        d = {h: ("" if d.get(h) is None else d[h]) for h in HEADER if h not in ("2023", "2024", "2025")}
        d["values"] = vals
        out.append(d)
    return out


def canonical_rows(rows: list[dict[str, Any]]) -> str:
    keys = [h for h in HEADER if h not in ("2023", "2024", "2025")] + ["values"]
    return json.dumps([{k: r[k] for k in keys} for r in rows], ensure_ascii=False, sort_keys=True)
