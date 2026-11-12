"""다른 회사 엑셀↔PDF 시험지 짓개(발주서 MB2 §2-3) — 정답을 코드가 짓는다(사람·모델 라벨 0). 표준 라이브러리만.

입력: 보고서 문서 모델(`document.json`) + 랩 `facts` 명령 출력(`facts.json`, 값 후보 목록 — 호출 0).
고르는 규칙(돌리기 전에 고정, 씨앗 `SEED`):
1. 상태 ok 인 표 가운데 머리 행에 `20(2[0-5])` 연도가 하나만 든 칸이 있는 열 → 그 열의 연도. 같은 연도가 두 열에 있으면
   그 연도는 버린다(한 (행, 연도)에 값 둘). 원천 엑셀의 연도 열이 `xpdf.HEADER` 의 셋(2023~2025)이라 그 셋만 쓴다.
   그 셋 가운데 연도 열이 둘 이상인 표만.
2. 머리 행(과 랩의 안쪽 머리 행)이 아닌 행 가운데 그 연도 열 칸이 둘 이상 `table_cell` 값 후보인 행. 값 후보는
   `percent` 아님 · `unitSuffix` 없음 · `sign "none"` 인 것만(원천 단위 칸이 표의 단위 하나라서 — 셈 갈래를 줄이려고).
3. 행 이름 = 그 행 머리 열(연도 열 뺌) 글자를 「 · 」 로 잇고(없으면 0열), 표의 첫 제목 후보가 있으면 앞에 붙임.
4. 이름이 겹치는 행은 모두 뺌. 씨앗 난수로 행을 섞어 칸 수가 `MIN_CELLS` 이상이 될 때까지 고름.
   머리 열이 아닌 열 가운데 머리 행에 연도(20xx)가 하나 든 열이 아닌 것이 있는 표(연도 하나가 열 둘에 걸친 표 등)는 뺀다.
단위 = 0열이 아닌 머리 열 가운데 머리 행 글이 비었거나 「단위」 인 열이 하나뿐이면 그 행의 그 칸(이름에서 뺌), 아니면 그 표의
`unitLines` 가운데 「단위」 가 든 첫 줄(없으면 「」). (경계 C2 에서 보고서 표를 보고 실측 전에 정함 — 보고에 적음.)
심기: `xpdf.PLAN` 일곱 + source_only 하나. 원천 이름 = 보고서 머리글 글자 그대로라 KB 판(목록 이름)보다 쉬운 조건이다.
산출(원천 행·심은 목록·정답)은 보고서 머리글 글자를 품으므로 git 밖에만 쓴다.
"""
from __future__ import annotations

import hashlib
import random
import re
from decimal import Decimal
from typing import Any

from evalrig.dataverify import gold as G
from evalrig.dataverify import xpdf

SEED = 20261111
MIN_CELLS = 44
YEARS = ("2023", "2024", "2025")
YEAR_RE = re.compile(r"20(2[0-5])")
ANY_YEAR_RE = re.compile(r"20\d\d")
SOURCE_ONLY_NAME = "원천 전용 지표(시험지에서 지음)"
AUTHOR = "runner-MB2 (MARC-TICK-560)"
EASY_RULE = "원천 이름 = 보고서 머리글 글자 그대로 — KB 판(목록 이름)보다 쉬운 조건"


def _norm(s: Any) -> str:
    return " ".join(str(s or "").split())


def year_cols(table: dict[str, Any]) -> dict[int, str]:
    found: dict[int, str] = {}
    for r in table["header"]["rows"]:
        for j, c in enumerate(table["cells"][r] if r < len(table["cells"]) else []):
            ys = {m.group(0) for m in YEAR_RE.finditer(str(c))}
            if len(ys) == 1 and j not in found:
                found[j] = ys.pop()
    by_year: dict[str, list[int]] = {}
    for j, y in found.items():
        by_year.setdefault(y, []).append(j)
    return {js[0]: y for y, js in by_year.items() if len(js) == 1 and y in YEARS}


def candidate_rows(doc: dict[str, Any], facts: dict[str, Any]) -> list[dict[str, Any]]:
    ok_fact = {}
    for f in facts["facts"]:
        if f["bucket"] != "table_cell" or f.get("percent") or f.get("unitSuffix") or f.get("sign", "none") != "none":
            continue
        w = f["where"]
        ok_fact[(w["gridId"], w["row"], w["col"])] = f
    grids = {g["id"]: g for g in facts["grids"]}
    rows = []
    for t in doc["tables"]:
        if t["status"] != "ok":
            continue
        yc = year_cols(t)
        if len(yc) < 2:
            continue
        g = grids.get(t["id"], {})
        hrows = [t["cells"][r] for r in t["header"]["rows"] if r < len(t["cells"])]
        head = lambda j: [_norm(h[j]) for h in hrows if j < len(h)]  # noqa: E731
        ncols = max(len(r) for r in t["cells"])
        name_cols = [j for j in t["header"]["cols"] if j not in yc] or [0]
        yearish = {j for j in range(ncols) if len({m.group(0) for h in head(j) for m in ANY_YEAR_RE.finditer(h)}) == 1}
        if any(j not in name_cols and j not in yearish for j in range(ncols)):
            continue  # 연도 머리가 없는 값 열(연도 하나가 열 둘에 걸침 등) — 어느 열이 그 해 값인지 모름
        skip = set(t["header"]["rows"]) | set(g.get("innerHeaders") or [])
        titles = g.get("titleCandidates") or []
        unit_cols = [j for j in name_cols if j != 0 and all(h in ("", "단위") for h in head(j))]
        unit_col = unit_cols[0] if len(unit_cols) == 1 else None
        if unit_col is not None:
            name_cols = [j for j in name_cols if j != unit_col]
        table_unit = next((u for u in (g.get("unitLines") or []) if "단위" in u), "")
        for i, row in enumerate(t["cells"]):
            if i in skip:
                continue
            got = {y: ok_fact[(t["id"], i, j)] for j, y in yc.items() if (t["id"], i, j) in ok_fact}
            if len(got) < 2:
                continue
            parts = [_norm(row[j]) for j in name_cols if j < len(row) and _norm(row[j])]
            if not parts:
                continue
            name = " · ".join(([_norm(titles[0])] if titles else []) + parts)
            unit = _norm(row[unit_col]) if unit_col is not None and unit_col < len(row) and _norm(row[unit_col]) else table_unit
            rows.append({"table": t["id"], "row": i, "name": name, "unit": unit,
                         "cells": {y: {"cell": f["id"], "raw": f["raw"], "value": f["value"], "decimals": f["decimals"]}
                                   for y, f in sorted(got.items())}})
    return rows


def pick_rows(cands: list[dict[str, Any]], *, seed: int = SEED, min_cells: int = MIN_CELLS) -> list[dict[str, Any]]:
    names: dict[str, int] = {}
    for r in cands:
        names[r["name"]] = names.get(r["name"], 0) + 1
    uniq = [r for r in cands if names[r["name"]] == 1]
    rng = random.Random(seed)
    order = list(uniq)
    rng.shuffle(order)
    out, n = [], 0
    for r in order:
        if n >= min_cells:
            break
        out.append(r)
        n += len(r["cells"])
    return out


def _num(s: str) -> int | float:
    d = Decimal(s)
    return int(d) if d == d.to_integral_value() else float(d)  # 정수 값은 int(엑셀이 7300.0 을 7300 으로 다시 읽음)


def _change_digit(s: str, rng: random.Random) -> int | float:
    pos = [k for k, ch in enumerate(s) if ch.isdigit()]
    p = rng.choice(pos)
    lead = p == pos[0] and len(pos) > 1
    choices = [d for d in "0123456789" if d != s[p] and not (lead and d == "0")]
    return _num(s[:p] + rng.choice(choices) + s[p + 1:])


def _rounding(s: str, decimals: int) -> float:
    q = Decimal(1).scaleb(-decimals)
    return float((Decimal(s) + q * Decimal("0.4")).quantize(q / 10))


def build(doc: dict[str, Any], facts: dict[str, Any], *, seed: int = SEED, min_cells: int = MIN_CELLS):
    """(source_rows, plants, gold, picked). 원천 행은 `xpdf.write_xlsx` 꼴."""
    sha12 = doc["source"]["sha256"][:12]
    picked = pick_rows(candidate_rows(doc, facts), seed=seed, min_cells=min_cells)
    if len(picked) < len(xpdf.PLAN):
        raise ValueError(f"고른 행 {len(picked)} < 심기 {len(xpdf.PLAN)}")
    ind = {f"X-{k + 1:03d}": r for k, r in enumerate(picked)}
    rng = random.Random(seed)
    order = sorted(ind)
    rng.shuffle(order)
    swap_ok = [k for k in order if len({c["value"] for c in ind[k]["cells"].values()}) >= 2]
    chosen: dict[str, str] = {}
    for kind in xpdf.PLAN:
        cand = [k for k in (swap_ok if kind == "year_swap" else order) if k not in chosen]
        chosen[cand[0]] = kind

    src = {k: {y: _num(c["value"]) for y, c in r["cells"].items()} for k, r in ind.items()}
    verdict = {(k, y): ("match", None, []) for k, r in ind.items() for y in r["cells"]}
    plants: list[dict[str, Any]] = []
    n = 0
    for k in sorted(chosen, key=order.index):
        kind = chosen[k]
        n += 1
        ev = f"X{n:02d}"
        cells = ind[k]["cells"]
        yrs = sorted(cells)
        if kind == "report_only":
            del src[k]
            for y in yrs:
                plants.append({"event": ev, "kind": kind, "indicator": k, "period": y, "cell": cells[y]["cell"],
                               "original": cells[y]["raw"], "planted": None})
                verdict[(k, y)] = ("report_only", kind, [])
            continue
        if kind == "year_swap":
            pairs = [(a, b) for i, a in enumerate(yrs) for b in yrs[i + 1:] if cells[a]["value"] != cells[b]["value"]]
            y1, y2 = rng.choice(pairs)
            src[k][y1], src[k][y2] = src[k][y2], src[k][y1]
            for y in (y1, y2):
                plants.append({"event": ev, "kind": kind, "indicator": k, "period": y, "cell": cells[y]["cell"],
                               "original": cells[y]["raw"], "planted": src[k][y]})
                verdict[(k, y)] = ("mismatch", kind, [])
            continue
        y = rng.choice(yrs)
        c = cells[y]
        if kind == "digit":
            nv, vd = _change_digit(c["value"], rng), ("mismatch", kind, [])
        elif kind == "unit_scale":
            nv, vd = _num(str(Decimal(c["value"]) * 1000)), ("mismatch", kind, [])
        elif kind == "rounding":
            nv, vd = _rounding(c["value"], int(c["decimals"] or 0)), ("match", kind, ["mismatch"])
        else:  # pragma: no cover
            raise ValueError(kind)
        src[k][y] = nv
        plants.append({"event": ev, "kind": kind, "indicator": k, "period": y, "cell": c["cell"], "original": c["raw"],
                       "planted": nv})
        verdict[(k, y)] = vd

    so = f"X-{len(ind) + 1:03d}"
    n += 1
    ev = f"X{n:02d}"
    src[so] = {y: rng.randint(100, 9999) for y in YEARS}
    for y in YEARS:
        plants.append({"event": ev, "kind": "source_only", "indicator": so, "period": y, "cell": None, "original": None,
                       "planted": src[so][y]})
        verdict[(so, y)] = ("source_only", "source_only", [])

    rows = []
    for k in sorted(src):
        r = ind.get(k)
        name = r["name"] if r else SOURCE_ONLY_NAME
        rows.append({"ID": k, "테마": "환경", "항목": name, "대분류": "", "중분류": "", "소분류": name,
                     "입력 방식": "직접 입력", "단위": r["unit"] if r else "", "values": dict(sorted(src[k].items())),
                     "핵심데이터": "", "메모": ""})
    items = []
    for (k, y) in sorted(verdict):
        v, kind, acc = verdict[(k, y)]
        r = ind.get(k)
        items.append({"indicator": k, "year": y, "verdict": v, "kind": kind, "accept": acc,
                      "cell": r["cells"][y]["cell"] if r else None})
    plants_doc = {"note": "다른 회사 엑셀↔PDF 시험지에 심은 것(원천에만). 행·지표는 씨앗 고정 난수로 골랐다.",
                  "seed": seed, "events": n, "plants": plants}
    conventions = {
        "labels": ["match", "mismatch", "source_only", "report_only"],
        "kinds": ["digit", "unit_scale", "rounding", "year_swap", "source_only", "report_only"],
        "rules": [
            "원천 = 엑셀(지은 시험지), 보고서 = 공개 PDF(그대로). 칸마다 원천 값과 보고서 글자 값을 견준 정답.",
            EASY_RULE,
            "rounding 은 원천에 보고서 표시 자릿수의 0.4 칸을 붙인 것 — match(대조군), mismatch 도 accept.",
            "year_swap 은 두 칸 모두 mismatch. report_only 는 원천에서 뺀 행의 보고서 칸 전부. source_only 는 원천에만 있는 행.",
            "1차(colab_156)에는 두 문서 대사가 없다 — 이 시험지의 1차 점수는 not_applicable.",
        ],
        "accept": "items[].accept — 돌리기 전에 정했다.",
    }
    meta = {
        "report": {"fileName": doc["source"]["file_name"], "pdfSha256": doc["source"]["sha256"]},
        "rowsPicked": len(picked), "cellsUsed": sum(len(r["cells"]) for r in picked),
        "v1Score": {"status": "not_applicable", "reason": "1차(colab_156)에 두 문서 대사 없음"},
    }
    gs = G.header(set_id=f"dataverify.xcopy-{sha12}", coverage="exhaustive", synthetic=False,
                  sources=[f"document.json sha256={doc['source']['sha256'][:16]}", f"evalrig.dataverify.xcopy seed={seed}"],
                  conventions=conventions, meta=meta)
    # 발주서의 `code_made` 는 공통 PROVENANCE_KINDS 에 없다 — 「러너가 코드·규칙으로 지음」 runner_made(common 0줄)
    gs["provenance"]["by"] = AUTHOR
    gs["items"] = items
    return rows, plants_doc, gs, picked


def doc_sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
