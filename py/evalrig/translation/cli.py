"""`python -m evalrig.translation <gold|convert|score|golden>` — py/ 에서 `uv run --frozen --group scope3colab`.

- gold    : 공식판 짝 · 용어집 가리킴 셋 · TRX7 가리킴 → `prework/gold-eva/translation-*.json` 짓고 얼림
- convert : TRL2·TRL3 × 변형 A·B·A+autofix·B+autofix(쪽 단위, page_offset 39) · trpdf res 셋(블록 단위)
            → `out/eva/translation/units/<자리>.json`
- score   : 자리별 용어 위반(엄격·관대)·남은 한국어·빠진 쪽 → `out/eva/translation/summary.{json,md}`
- golden  : 골든 ① 용어 위반 = `glossary_check.violations` (공식판·engine-4.1-mini) · ② TRX7 V2 검수 다시 셈 = score 파일
            → `out/eva/golden/translation.json`
- bleu    : M-T4 BLEU 바닥선 → `out/eva/mt4/summary.{json,md}`
- mt5     : M-T5 쪽 번호 지도 고침 재채점(+ 골든 ④) → `out/eva/mt5/summary.{json,md}` · `out/eva/golden/mt5.json`
- eva     : 묶음 표 — 골든 ①~④ + M-I2·M-T4·M-T5 머리 줄 → `out/eva/summary.{json,md}`(앞 명령들의 산출을 읽음)
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from ..common import gold as G
from ..common import report as R
from ..iro.cli import APPW, GOLD, OUT, ROOT, dump_units, freeze_line, load_units
from . import gold as TG
from .from_v1 import convert_pairs, convert_result
from .score import missing, remaining_korean, review_rows, term_violations

GOLD_AT = "2026-11-09"
TRL = ("TRL2", "TRL3")
VARIANTS = ("A", "B", "A+autofix", "B+autofix")
PAGE_OFFSET = 39  # TRL 둘 다 국문 PDF 쪽 40~45(발주서 EVA ★7)
TRPDF_MODELS = ("engine-4.1-mini", "gpt-6-luna", "gpt-6.1-sol")
OUT7 = Path(os.environ.get("TRX7_OUT", ROOT / "esg-lab" / "out" / "trx7"))
GOLD7 = Path(os.environ.get("TRX7_GOLD", ROOT / "prework" / "gold-trx7"))


def site_name(run: str, variant: str) -> str:
    return f"{run}-{variant}"


def ko_pages(key: str = "sam", regions: tuple[str, ...] = ("body",)) -> dict[int, str]:
    """국문 쪽 글 = 그 쪽 본문 블록 글(차례대로). 머리말·꼬리말(region footer 등)은 뺀다 — 쪽마다 되풀이돼 용어 위반을 부풀린다."""
    from trlab.common import load

    pages: dict[int, list] = {}
    for b in load(key, "ko"):
        if b.region in regions:
            pages.setdefault(b.page, []).append(b)
    return {p: "\n".join(x.text for x in sorted(bs, key=lambda b: b.order)) for p, bs in pages.items()}


def all_sites() -> list[str]:
    return [site_name(r, v) for r in TRL for v in VARIANTS] + [f"trpdf-{m}" for m in TRPDF_MODELS]


def cmd_gold(_: Any) -> None:
    GOLD.mkdir(parents=True, exist_ok=True)
    sets = [TG.build_official(at=GOLD_AT, root=ROOT)]
    sets += [TG.build_glossary_ref(k, at=GOLD_AT, root=ROOT) for k in TG.GLOSS_BOOKS]
    sets.append(TG.build_review_ref(at=GOLD_AT, root=ROOT, gold7=GOLD7))
    for gs in sets:
        p = GOLD / f"{gs['setId']}.json"
        p.write_text(G.dumps(gs), encoding="utf-8")
        freeze_line(p)
        print("gold", p.name, len(gs["items"]), G.load(p)["frozen"]["sha16"])


def cmd_convert(_: Any) -> None:
    from trlab.glossary_check import engine_pairs

    kp = ko_pages()
    for r in TRL:
        d = json.loads((APPW / f"{r}.out" / "live" / "result.json").read_text(encoding="utf-8"))
        pn = d["document"]["pageNumbers"]
        if r == "TRL2" and pn != [PAGE_OFFSET + i for i in range(1, len(pn) + 1)]:
            raise SystemExit(f"{r}: pageNumbers {pn} ≠ 40~45 — 멈춤")
        for v in VARIANTS:
            us = convert_result(d, run=r, variant=v, page_offset=PAGE_OFFSET, ko_pages=kp)
            dump_units(OUT / "translation" / "units" / f"{site_name(r, v)}.json", us)
            print("convert", site_name(r, v), len(us), [u.status for u in us].count("ok"), "ok")
    for m in TRPDF_MODELS:
        us = convert_pairs(engine_pairs(m), run=f"trpdf-{m}")
        dump_units(OUT / "translation" / "units" / f"trpdf-{m}.json", us)
        print("convert", f"trpdf-{m}", len(us))


def cmd_score(_: Any) -> None:
    from trlab.glossary_check import load_gloss

    off = G.load(GOLD / f"{TG.OFFICIAL_ID}.json")
    gl = G.load(GOLD / "translation-glossary-sam.json")
    TG.check_ref(gl, ROOT)
    gloss = load_gloss("sam")["items"]
    gold_pages = sorted({it["page"] for it in off["items"]})
    rows = []
    for s in all_sites():
        us = load_units(OUT / "translation" / "units" / f"{s}.json")
        tv = term_violations(us, gloss)
        rk = remaining_korean(us)
        if s.startswith("TRL"):
            pages = [int(u.group) for u in us]
            miss = missing(us, [f"page:{p}" for p in gold_pages if min(pages) <= p <= max(pages)])["rate"]
        else:
            miss = None
        rows.append({"gold": gl["setId"], "자리": s, "단위": len(us), "용어 든 단위": tv["units_with_term"],
                     "위반 엄격 rate": tv["strict"], "위반 관대 rate": tv["lenient"], "남은 한국어 rate": rk["rate"],
                     "빠진 쪽 rate": miss})
    R.write(OUT / "translation", "summary", rows, [gl, off], title="EVA 번역 — 1차 산출 용어 위반·남은 한국어",
            notes=["TRL 은 쪽 단위(국문 쪽 40~45, 국문 글 = 본문 블록만 — 꼬리말 뺌), trpdf 는 블록 단위(쪽 11·30·79).",
                   "빠진 쪽 = 공식판 짝이 있는 그 범위 국문 쪽 중 후보가 없거나 빈 쪽.",
                   "용어 위반은 trlab glossary_check.violations 그대로(엄격 = 표기 그대로 없음, 관대 = 꼴 바꿔도 없음)."])
    print("score", len(rows))


def _same_hits(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> bool:
    k = lambda h: (h["pair"], h["kr"], h["en"], h["lenient_hit"])  # noqa: E731
    return sorted(map(k, a)) == sorted(map(k, b))


def cmd_golden(_: Any) -> None:
    from trlab.glossary_check import engine_pairs, load_gloss, official_pairs, violations

    gloss = load_gloss("sam")["items"]
    res: dict[str, Any] = {"g1": {}, "g2": {}}
    for name, pairs in (("official-sam", official_pairs("sam")[0]), ("engine-4.1-mini", engine_pairs("engine-4.1-mini"))):
        direct = violations(pairs, gloss)[0]
        ours = term_violations(convert_pairs(pairs, run=f"golden-{name}"), gloss)["hits"]
        res["g1"][name] = {"pairs": len(pairs), "direct": len(direct), "evalrig": len(ours), "same": _same_hits(direct, ours)}
    units = {u["id"]: u for u in json.loads((GOLD7 / "units.json").read_text(encoding="utf-8"))["units"]}
    gb = {k: load_gloss(k)["items"] for k in ("sam", "cos", "kb")}
    for folder in sorted((OUT7 / "review").glob("V2-*")):
        recs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json")) if not p.name.startswith("_")]
        ours = review_rows(recs, units, gb, "V2")
        ref = json.loads((OUT7 / "score" / f"{folder.name}.json").read_text(encoding="utf-8"))
        res["g2"][folder.name] = {"rows": len(ours), "ref_rows": ref["n"], "same": ours == ref["rows"]}
    res["same"] = all(v["same"] for part in ("g1", "g2") for v in res[part].values()) and bool(res["g2"])
    p = OUT / "golden" / "translation.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("golden ①②", json.dumps(res, ensure_ascii=False))
    if not res["same"]:
        raise SystemExit(1)


def cmd_bleu(_: Any) -> None:
    from .bleu import run_mt4

    print("M-T4 BLEU 범위", run_mt4(OUT)["range"])


def cmd_mt5(_: Any) -> None:
    from .mt5 import run_mt5

    r = run_mt5(OUT)
    print("M-T5", r["summary"], "되돌림", r["restored"])


def _load(p: Path) -> dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))


def cmd_eva(_: Any) -> None:
    """묶음 표 — 골든 넷과 측정 셋의 머리 줄. 정답 셋 메타는 쓰인 셋 전부를 머리에 찍는다."""
    from .. import drafting, iro  # noqa: F401 — 정답 항목 꼴 등록(register_unit)
    names = sorted(p.name for p in GOLD.glob("*.json"))
    gsets = [G.load(GOLD / n) for n in names]
    gd, gt, gm = (_load(OUT / "golden" / f) for f in ("drafting.json", "translation.json", "mt5.json"))
    mi2 = _load(OUT / "mi2" / "summary.json")["extra"]["headline"]
    mt4 = [r for r in _load(OUT / "mt4" / "summary.json")["rows"] if not r["자리"].startswith("바닥")]
    mt5 = _load(OUT / "mt5" / "summary.json")
    rows = [
        {"gold": "translation-sam-official", "항목": "골든 ① 용어 위반(공식판 · engine-4.1-mini)", "같음": gt["g1"]["official-sam"]["same"] and gt["g1"]["engine-4.1-mini"]["same"],
         "값": f"{gt['g1']['official-sam']['evalrig']}·{gt['g1']['engine-4.1-mini']['evalrig']} 걸림"},
        {"gold": "translation-review-trx7", "항목": "골든 ② TRX7 V2 검수 다시 셈", "같음": all(v["same"] for v in gt["g2"].values()),
         "값": " · ".join(f"{k} {v['rows']}줄" for k, v in sorted(gt["g2"].items()))},
        {"gold": "drafting-sg1-sam", "항목": "골든 ③ 보고서 사실 판정(책 넷)", "같음": gd["same"], "값": f"{gd['n']} 판정 · 다름 {gd['diff']}"},
        {"gold": R.NO_GOLD, "항목": "골든 ④ M-T5 base = TRX8 c3", "같음": gm["same"] and gm["restored"],
         "값": " · ".join(f"{k} {v:g}" for k, v in sorted(gm["expect"].items()))},
    ]
    for s in ("IR0", "IRL2", "IRD3"):
        h = mi2[s]
        rows.append({"gold": R.NO_GOLD, "항목": f"M-I2 {s} 규제 URL", "같음": None,
                     "값": f"루트만 {h['root_only']['num']}/{h['n']} · 화이트리스트 밖 {h['outside_whitelist']['num']}/{h['n']} · 기간 밖 {h['out_of_range']['num']}/{h['n']}"})
    b = [r["BLEU"] for r in mt4]
    rows.append({"gold": "translation-sam-official", "항목": "M-T4 BLEU 범위(자리 11)", "같음": None, "값": f"{min(b):.1f}~{max(b):.1f}"})
    for n in mt5["notes"][:2]:
        rows.append({"gold": R.NO_GOLD, "항목": f"M-T5 {n.split(':')[0]}", "같음": None, "값": n.split(":", 1)[1].strip()})
    R.write(OUT, "summary", rows, gsets, title="EVA 묶음 — 골든 ①~④ · 측정 M-I2 · M-T4 · M-T5",
            notes=["자세한 표: iro·drafting·translation·mi2·mt4·mt5 의 summary.md.", "AI 호출 0 · 밖 HTTP 0."])
    print("eva", len(rows), "골든 넷 같음", all(r["같음"] for r in rows[:4]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evalrig.translation")
    ap.add_argument("cmd", choices=["gold", "convert", "score", "golden", "bleu", "mt5", "eva"])
    a = ap.parse_args(argv)
    {"gold": cmd_gold, "convert": cmd_convert, "score": cmd_score, "golden": cmd_golden,
     "bleu": cmd_bleu, "mt5": cmd_mt5, "eva": cmd_eva}[a.cmd](a)
    return 0
