"""M-T5 쪽 번호 지도 고침 재채점 — 호출 0(발주서 EVA §2 (7)).

`trlab/pagemap.py` 의 `_nums` 는 글이 9자를 넘으면 버려, 꼬리말 「… 보고서 N」 꼴 번호를 못 읽는다(TRX8 ⑤-1).
판 셋을 **같은 프로세스에서 `pagemap._nums` 를 갈아 끼워** 다시 잰다(파일 0줄, 끝에 원래 함수로 되돌림):
- base : 원 `_nums` — 다시 뽑아 TRX8 c3 시작 A∨B(nb3 6.7 · nb4~nb6 100)가 나와야 한다(골든 ④, 다르면 멈춤).
- F1   : 9자 넘는 블록이면 글 **끝**의 1~3자리 수만(앞이 숫자·쉼표·점이면 안 셈 — 「2,030」 은 버림). 앞 조건
         `0 < n < 1000`·`abs(n − 쪽) < 60` 그대로. 9자 이하는 원 `_nums` 그대로.
- F2   : F1 + 지도의 direct 덮음이 쪽 수의 반 아래인 책은 번호를 버림(`dropped_to_path2`) — 점수 칸은 TRX8 의 N- c3 값을 읽어 옮긴다.
이 고침은 nb3 를 본 뒤의 **사후 고침**이다 — 주지표는 「고친 책 / 망친 책」(nb4~nb6 이 100 에서 내려가면 망침)과 direct 덮음 변화.

작업 자리: `out/trx8` 을 `out/eva/mt5/work/` 로 복사(`calls.jsonl` 뺌)한 뒤 import **전에** `TRX8_OUT`·`TRX2_OUT`·`TRX3_OUT`·
`TRX4_OUT` = 그 자리, `TRX2_GOLD` = `prework/gold-trx8`. `trlab.tx8_pick` 은 함수 안에서만 import 한다(시험 수집 때
setdefault 가 진짜 `out/trx8` 로 걸리지 않게).
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Iterator

BOOKS = ("nb3", "nb4", "nb5", "nb6")
BASE_EXPECT = {"nb3": 6.7, "nb4": 100.0, "nb5": 100.0, "nb6": 100.0}
_TAIL = re.compile(r"(?<![\d,.])(\d{1,3})$")


def f1_nums(orig: Callable[[str, int], list[int]]) -> Callable[[str, int], list[int]]:
    """F1 `_nums` — 9자 이하는 원 함수, 넘으면 글 끝 1~3자리 수만."""

    def nums(text: str, page: int) -> list[int]:
        t = text.strip()
        if len(t) <= 9:
            return orig(text, page)
        m = _TAIL.search(t)
        if not m:
            return []
        n = int(m.group(1))
        return [n] if 0 < n < 1000 and abs(n - page) < 60 else []

    nums.__name__ = "_nums_f1"
    return nums


def f2_drop(cover_direct: int, npages: int) -> bool:
    """F2 — direct 덮음이 쪽 수의 반 아래면 그 책 번호를 버림."""
    return cover_direct < npages / 2


@contextlib.contextmanager
def swapped(module: Any, name: str, fn: Callable) -> Iterator[None]:
    """`module.<name>` 을 잠시 갈아 끼우고 끝에 원래 것으로 되돌림(예외가 나도)."""
    orig = getattr(module, name)
    setattr(module, name, fn)
    try:
        yield
    finally:
        setattr(module, name, orig)


def prepare_work(src: Path, work: Path) -> None:
    """`src`(out/trx8) → `work` 복사(`calls.jsonl` 뺌). 이미 있으면 지우고 다시."""
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(src, work, ignore=shutil.ignore_patterns("calls.jsonl"))


def set_env(work: Path, gold8: Path) -> None:
    for k in ("TRX8_OUT", "TRX2_OUT", "TRX3_OUT", "TRX4_OUT"):
        os.environ[k] = str(work)
    os.environ["TRX2_GOLD"] = str(gold8)


def _rows_changed(a: dict[str, Any], b: dict[str, Any]) -> int:
    ra = {r["n"]: r.get("pred_start") for r in a["grade"]["rows"]}
    rb = {r["n"]: r.get("pred_start") for r in b["grade"]["rows"]}
    return sum(ra.get(k) != rb.get(k) for k in set(ra) | set(rb))


def run(work: Path, gold8: Path) -> dict[str, Any]:
    """판 셋 × 책 넷 → `{rows[{book, variant, start_ab, end_ab, direct, npages, offset, changed, status}], restored}`."""
    set_env(work, gold8)
    from trlab import order_win as W
    from trlab import pagemap as PM
    from trlab import tx8_pick as T

    orig = PM._nums
    out: dict[str, Any] = {"rows": []}
    base_res: dict[str, dict[str, Any]] = {}
    for variant in ("base", "F1", "F2"):
        fn = orig if variant == "base" else f1_nums(orig)
        with swapped(PM, "_nums", fn):
            for key in BOOKS:
                PM.main([key, T.LANG])
                pm = json.loads((work / "pagemap" / f"{key}-{T.LANG}.json").read_text(encoding="utf-8"))
                W.main([key, T.LANG])
                res = T.X.run_code(T.name1(key))
                status = "ok"
                if variant == "F2" and f2_drop(pm["cover"]["direct"], pm["npages"]):
                    status = "dropped_to_path2"
                    res = json.loads((work / "runs" / f"{T.nameN(key)}-c3.json").read_text(encoding="utf-8"))
                if variant == "base":
                    base_res[key] = res
                g = res["grade"]
                out["rows"].append({"book": key, "variant": variant, "start_ab": g["start_ab"], "end_ab": g["end_ab"],
                                    "direct": pm["cover"]["direct"], "npages": pm["npages"], "offset": pm["offset"],
                                    "changed": _rows_changed(base_res[key], res), "status": status})
        if variant == "base":
            got = {r["book"]: r["start_ab"] for r in out["rows"] if r["variant"] == "base"}
            out["base_same"] = got == BASE_EXPECT
            if not out["base_same"]:
                out["base_got"] = got
                break
    out["restored"] = PM._nums is orig
    return out


def run_mt5(out_dir: Path) -> dict[str, Any]:
    """M-T5 표 → `out_dir/mt5/summary.{json,md}` + 골든 ④ → `out_dir/golden/mt5.json`."""
    from ..common import report as R
    from ..iro.cli import ROOT

    work = out_dir / "mt5" / "work"
    prepare_work(ROOT / "esg-lab" / "out" / "trx8", work)
    res = run(work, ROOT / "prework" / "gold-trx8")
    g = out_dir / "golden" / "mt5.json"
    g.parent.mkdir(parents=True, exist_ok=True)
    g.write_text(json.dumps({"expect": BASE_EXPECT, "same": res.get("base_same"), "got": res.get("base_got"),
                             "restored": res["restored"]}, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if not res.get("base_same"):
        raise SystemExit(f"골든 ④ 다름: {res.get('base_got')} — 멈춤")
    base = {r["book"]: r for r in res["rows"] if r["variant"] == "base"}
    rows, fixed, broken = [], {}, {}
    for r in res["rows"]:
        b0 = base[r["book"]]
        rows.append({"gold": R.NO_GOLD, "책": r["book"], "판": r["variant"], "시작 A∨B": r["start_ab"], "끝 A∨B": r["end_ab"],
                     "direct/쪽": f"{r['direct']}/{r['npages']}", "offset": r["offset"], "바뀐 항목": r["changed"], "상태": r["status"]})
        if r["variant"] != "base":
            fixed.setdefault(r["variant"], []).extend([r["book"]] if r["start_ab"] > b0["start_ab"] else [])
            broken.setdefault(r["variant"], []).extend([r["book"]] if r["start_ab"] < b0["start_ab"] else [])
    summ = {v: f"고친 {len(fixed.get(v, []))}({', '.join(fixed.get(v, [])) or '—'}) / 망친 {len(broken.get(v, []))}({', '.join(broken.get(v, [])) or '—'})"
            for v in ("F1", "F2")}
    notes = [f"{v}: {s}" for v, s in summ.items()] + [
        "사후 고침(nb3 를 본 뒤) — 주지표는 고친/망친 책과 direct 덮음 변화. 수 = 길 1 c3(호출 0), 정답 TRX8.",
        "F2 의 dropped_to_path2 줄 점수 = TRX8 의 N- c3 값을 읽어 옮김.",
        "base = 원 _nums — TRX8 c3 시작 A∨B 와 같음(골든 ④). 끝에 pagemap._nums 를 원래 함수로 되돌림.",
    ]
    R.write(out_dir / "mt5", "summary", rows, R.NO_GOLD, title="M-T5 쪽 번호 지도 고침 재채점", notes=notes,
            extra={"restored": res["restored"]})
    shutil.rmtree(work)  # 73MB 사본 — 표를 쓴 뒤 지움(다시 돌리면 다시 복사)
    return {"rows": res["rows"], "summary": summ, "restored": res["restored"]}
