"""`python -m evalrig.iro <gold|convert|score>` — py/ 에서 `uv run --frozen --group scope3colab`.

- gold    : `prework/gold-eva/iro-empty.json` 짓고 얼림(+ `freeze.txt` 줄)
- convert : 자리 여섯(IR0·IRL·IRL2·IRD1~3) → `out/eva/iro/units/<자리>.json` — IR0(코랩 기준 실행, 원본과 같은 취급)은
            파일로 쓰지 않는다(글을 옮기지 않음 — 쓸 때마다 메모리에서 다시 변환, `units_for`)
- score   : 단위 + 정답 → `out/eva/iro/summary.{json,md}`(정답 없는 코드 지표 + 정답 칸 no_denominator)
- urlcheck: M-I2 URL 검사(오프라인) → `out/eva/mi2/summary.{json,md}`
산출 `EVA_OUT`(기본 esg-lab/out/eva) · 정답 `EVA_GOLD`(기본 prework/gold-eva).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from ..common import gold as G
from ..common import report as R
from ..common.units import Unit
from . import from_v1 as V1
from .gold import EMPTY_ID, build_empty
from .score import code_metrics, score

_HERE = Path(__file__).resolve()
ROOT = _HERE.parents[4]
if ROOT.name.endswith(".worktrees"):
    ROOT = ROOT.parent
OUT = Path(os.environ.get("EVA_OUT", ROOT / "esg-lab" / "out" / "eva"))
GOLD = Path(os.environ.get("EVA_GOLD", ROOT / "prework" / "gold-eva"))
APPW = ROOT / "esg-app.worktrees"
COLAB = ROOT / "received-derived" / "2026-10-23" / "IRO 코랩 기준 실행"
GOLD_AT = "2026-11-09"
# 배포본(IRD)·코랩(IR0)은 결과에 범위 칸이 없다 — 처음 값(IRD input.json research 줄)·코랩 셀 17 판으로 둔다
DEFAULT_RANGE = "2024-01-01~오늘까지"
SITES = ("IR0", "IRL", "IRL2", "IRD1", "IRD2", "IRD3")


def site_units(site: str) -> list[Unit]:
    if site == "IR0":
        resp, topics = V1.load_colab(COLAB)
        return V1.convert_colab(resp, run="IR0", topics=topics, scope={"whitelist": "cell17", "dateRange": DEFAULT_RANGE, "sector": None})
    if site in ("IRL", "IRL2"):
        d = json.loads((APPW / f"{site}.out" / "live" / "result.json").read_text(encoding="utf-8"))
        return V1.convert_result(d, run=site)
    if site in ("IRD1", "IRD2", "IRD3"):
        txt = V1.read_raw_json_xlsx(APPW / f"{site}.out" / "xlsx" / "materiality-websearch.xlsx")
        return V1.convert_raw_json(txt, run=site, scope={"whitelist": "cell73", "dateRange": DEFAULT_RANGE, "sector": "non_financial"})
    raise ValueError(site)


def dump_units(path: Path, units: list[Unit]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([u.to_dict() for u in units], ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def load_units(path: Path) -> list[Unit]:
    return [Unit.from_dict(d) for d in json.loads(path.read_text(encoding="utf-8"))]


def freeze_line(path: Path) -> None:
    """얼리고 `freeze.txt` 의 그 파일 줄을 갈음(이름 차례)."""
    s = G.freeze(path)
    ft = GOLD / "freeze.txt"
    rows = {}
    if ft.exists():
        for line in ft.read_text(encoding="utf-8").splitlines():
            if line.strip():
                n, h = line.split()
                rows[n] = h
    rows[path.name] = s
    ft.write_text("".join(f"{n} {h}\n" for n, h in sorted(rows.items())), encoding="utf-8")


def cmd_gold(_: Any) -> None:
    GOLD.mkdir(parents=True, exist_ok=True)
    p = GOLD / f"{EMPTY_ID}.json"
    p.write_text(G.dumps(build_empty(GOLD_AT)), encoding="utf-8")
    freeze_line(p)
    print("gold", p.name, G.load(p)["frozen"]["sha16"])


# 원본과 같은 취급인 자리(코랩 기준 실행 = received-derived) — 단위를 파일로 쓰지 않고 쓸 때마다 메모리에서 다시 변환한다
MEMORY_ONLY = ("IR0",)


def units_for(site: str) -> list[Unit]:
    """자리의 단위 — 원본 취급 자리는 메모리 변환, 그 밖은 `convert` 가 쓴 파일."""
    if site in MEMORY_ONLY:
        return site_units(site)
    return load_units(OUT / "iro" / "units" / f"{site}.json")


def cmd_convert(_: Any) -> None:
    for s in SITES:
        us = site_units(s)
        if s not in MEMORY_ONLY:
            dump_units(OUT / "iro" / "units" / f"{s}.json", us)
        print("convert", s, len(us), "failed", sum(u.status == "failed" for u in us), "(메모리만)" if s in MEMORY_ONLY else "")


def cmd_score(_: Any) -> None:
    gs = G.load(GOLD / f"{EMPTY_ID}.json")
    rows = []
    for s in SITES:
        us = units_for(s)
        sc = score(us, gs)
        for b, m in code_metrics(us).items():
            rows.append({"gold": gs["setId"], "자리": s, "갈래": b, "단위": m["units"], "실패": m["failed"], "빈": m["missing"],
                         "항목": m["items"], "URL": m["urls"], "searched_rate": m["searched_rate"],
                         "날짜 ok/missing/unreadable": "/".join(str(m["date_status"].get(k, 0)) for k in ("ok", "missing", "unreadable")),
                         "재현%": sc["gold"]["recall"], "정밀%": sc["gold"]["precision"]})
    R.write(OUT / "iro", "summary", rows, [gs], title="EVA IRO — 1차 산출 단위 셈(정답 0)",
            notes=["정답 0(iro-empty) — 재현·정밀은 분모 없음. 컨설턴트 수정본(MARC-ENT-607) 뒤 채움.",
                   "searched_rate 분모 = 검색 호출 유무를 아는 단위(코랩 응답만 앎, 앱·배포본 결과는 모름).",
                   f"IR0·IRD 의 기간은 결과에 칸이 없어 처음 값 {DEFAULT_RANGE} 로 둔다(추정)."])
    print("score", len(rows))


def cmd_urlcheck(_: Any) -> None:
    from .urlcheck import run_mi2

    for line in run_mi2(OUT)["top"]:
        print("M-I2", line)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evalrig.iro")
    ap.add_argument("cmd", choices=["gold", "convert", "score", "urlcheck"])
    a = ap.parse_args(argv)
    {"gold": cmd_gold, "convert": cmd_convert, "score": cmd_score, "urlcheck": cmd_urlcheck}[a.cmd](a)
    return 0
