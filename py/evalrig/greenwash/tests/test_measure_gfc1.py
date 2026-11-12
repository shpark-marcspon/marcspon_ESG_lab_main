"""GFC1 측정 셈 시험(발주서 GFC1 §3-1 T1~T11 + critic B1·B2, 지은 자료만 — 회사·보고서 글 0)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from evalrig.greenwash import measure_gfc1 as M

SRC = Path(M.__file__)


# ---- 지은 자료 짓기 ----
def item(key, hit=(), level="None", page=1, text=None):
    v = None if level is None else {"level": level, "category": None}
    return {"key": "i0", "stableKey": key, "target": {"kind": "sentence", "text": text or f"문장 {key}", "page": page,
                                                      "blockIds": [f"p{page}-b1"]},
            "status": "done" if v else "failed", "errorCode": None if v else "judge_failed", "verdict": v,
            "module": {"criteriaLines": list(hit)}}


def result(items, unplaced=()):
    return {"segments": [{"key": "s1", "status": "done", "errorCode": None, "items": list(items),
                          "unplaced": [{"text": t, "page": 1, "errorCode": "quote_not_found"} for t in unplaced]}]}


def types_rows(book, keys_types):
    return [{"book": book, "key": k, "type": t, "confidence": "high", "failed": False} for k, t in keys_types]


def build_books():
    """일반 2권(g1·g2) · 금융 2권(n1·n2) × 판정 받은 항목 10."""
    res, rows = {}, []
    for b in ("g1", "g2"):
        its, tt = [], []
        hits = [("high-1",)] * 4 + [("mid-1",)] * 3 + [()] * 3
        for i, h in enumerate(hits):
            k = f"{b}-{i}"
            its.append(item(k, h))
            tt.append((k, "F-product" if i == 9 else "G-energy"))
        res[b] = result(its)
        rows += types_rows(b, tt)
    for b in ("n1", "n2"):
        its, tt = [], []
        # F 4: specific 1 · generic 1 · none 2 / G 6: specific 3 · generic 2 · none 1
        spec = [("F-product", ("high-1", "mid-1")), ("F-product", ("mid-1", "keyword-exclusion")), ("F-product", ()),
                ("F-product", ()), ("G-ghg-target", ("keyword-ghg",)), ("G-ghg-target", ("high-2",)),
                ("G-ghg-target", ("exception-1",)), ("G-ghg-target", ("evidence-quantity",)),
                ("G-ghg-target", ("high-4",)), ("G-ghg-target", ())]
        for i, (t, h) in enumerate(spec):
            k = f"{b}-{i}"
            its.append(item(k, h))
            tt.append((k, t))
        res[b] = result(its)
        rows += types_rows(b, tt)
    groups = {"g1": "general", "g2": "general", "n1": "finance", "n2": "finance"}
    return res, rows, groups


def run_tally(res, rows, groups):
    cands = [c for b, r in res.items() for c in M.candidates_of(b, r)]
    return M.tally(cands, M.type_maps(rows, rows), groups)


# ---- T1 갈래 ----
@pytest.mark.parametrize("hit,verdict,want", [
    ((), {"level": "None"}, "none"),
    (("mid-1", "keyword-exclusion"), {"level": "Mid"}, "generic_only"),
    (("mid-1", "high-1"), {"level": "High"}, "specific"),
    (("high-1",), None, None),
])
def test_t1_coverage_class(hit, verdict, want):
    assert M.coverage_class(hit, verdict) == want


def test_t1_candidate_line_is_specific():
    assert M.coverage_class(("mid-1", "fin-2"), {"level": "Mid"}) == "specific"


# ---- T2 종류 ----
def test_t2_resolve_type():
    a = {"type": "F-label", "failed": False}
    assert M.resolve_type(a, dict(a)) == "F-label"
    assert M.resolve_type(a, {"type": "G-other", "failed": False}) == M.UNSTABLE
    assert M.resolve_type(a, {"type": None, "failed": True}) == M.UNSTABLE_FAILED
    assert M.resolve_type(a, None) == M.UNSTABLE_FAILED


# ---- T3 tally 손셈 ----
def test_t3_tally_hand():
    t = run_tally(*build_books())
    fin, gen = t["groups"]["finance"], t["groups"]["general"]
    assert (fin["fJudged"], fin["stableJudged"], fin["fSharePct"]) == (8, 20, 40.0)
    assert (gen["fJudged"], gen["fSharePct"]) == (2, 10.0)  # 일반 권 F-* = 오분류 바닥
    cov = t["coverage"]
    assert (cov["general_all"]["U"], cov["general_all"]["n"], cov["general_all"]["Upct"]) == (12, 20, 60.0)
    assert (cov["finance_F"]["U"], cov["finance_F"]["n"], cov["finance_F"]["Upct"]) == (6, 8, 75.0)
    assert (cov["finance_G"]["U"], cov["finance_G"]["Upct"]) == (6, 50.0)
    assert cov["finance_F"]["generic_only"] == 2 and cov["finance_F"]["none"] == 4
    assert t["lines"]["general"]["hit"]["high-1"] == 8 and t["lines"]["general"]["pct"]["high-1"] == 40.0
    assert t["lines"]["finance_F"]["hit"]["mid-1"] == 4 and t["lines"]["finance_F"]["pct"]["mid-1"] == 50.0
    assert t["financeBooksF"]["n1"]["Upct"] == 75.0
    assert cov["byType"]["F-product"]["finance"]["n"] == 8


# ---- T4 unplaced·판정 실패는 분모 밖 ----
def test_t4_failed_unplaced_outside():
    res, rows, groups = build_books()
    res["n1"]["segments"][0]["items"].append(item("n1-x", ("high-1",), level=None))
    res["n1"]["segments"][0]["unplaced"].append({"text": "자리 못 찾음", "page": 2, "errorCode": "quote_not_found"})
    rows += types_rows("n1", [("n1-x", "F-product"), ("u0", "F-product")])
    t = run_tally(res, rows, groups)
    b = t["books"]["n1"]
    assert (b["candidates"], b["judged"], b["failed"], b["unplaced"]) == (12, 10, 1, 1)
    assert b["failedCodes"] == {"judge_failed": 1}
    assert t["coverage"]["finance_F"]["n"] == 8  # 그대로
    assert b["candidates"] - 2 == b["coverage"]["n"]


def test_t4_verdict_with_check_failure_is_judged():
    """검사 실패(errorCode)지만 verdict 있음 = 판정 받음·분모 안(§1-5, 검증 권고 R1)."""
    it = item("n-chk", ("high-1",), level="High")
    it["status"], it["errorCode"] = "failed", "levelRule"
    (c,) = M.candidates_of("n1", result([it]))
    assert (c["status"], c["cls"]) == ("judged", "specific")


# ---- T5 floor ----
def test_t5_floor_pairs():
    def r(c, rep, hit, level="Mid"):
        return {"book": "x", "claim": c, "rep": rep, "hit": list(hit), "level": level, "failure": None}

    rows = [r("A", k, ("high-1",)) for k in (1, 2, 3)]
    rows += [r("B", 1, ("high-1",)), r("B", 2, ("high-1",)), r("B", 3, ("mid-1",))]
    rows += [r("C", 1, (), "None"), r("C", 2, ("mid-1",)), r("C", 3, ("high-1",), "High")]
    f = M.floor(rows)["all"]
    assert (f["pairs"], f["clsDiff"]) == (9, 5)
    assert f["clsDiffPct"] == round(500 / 9, 1)
    assert f["levelDiff"] == 3  # C 만 None·Mid·High


def test_t5_floor_skips_failed():
    rows = [{"book": "x", "claim": "A", "rep": 1, "hit": [], "level": "None", "failure": None},
            {"book": "x", "claim": "A", "rep": 2, "hit": [], "level": None, "failure": "judge_failed"}]
    assert M.floor(rows) == {}


# ---- T6 쪽 거름 ----
def test_t6_select_pages_ties_keep_front():
    union = list(range(1, 41))
    keep, drop = M.select_pages(union, {p: 1 for p in union}, 35)
    assert keep == list(range(1, 36)) and drop == list(range(36, 41))


def test_t6_select_pages_score_order():
    union = list(range(1, 41))
    sc = {p: (5 if p > 35 else 1) for p in union}
    keep, drop = M.select_pages(union, sc, 35)
    assert set(range(36, 41)) <= set(keep) and drop == [31, 32, 33, 34, 35]
    assert M.select_pages([3, 1, 2], {}, 35) == ([1, 2, 3], [])


# ---- T7 detectcov ----
def test_t7_detectcov():
    s_env = "가나은행은 녹색채권을 발행해 온실가스 감축 사업에 썼습니다."  # 금융 낱말 + 환경 신호
    s2 = "가나은행은 녹색여신 취급 규모를 늘렸습니다."
    s3 = "가나은행은 스튜어드십 원칙에 따라 관여 활동을 했습니다."
    s4 = "가나은행은 PCAF 방식으로 산정 범위를 넓혔습니다."
    s5 = "가나은행은 ESG 투자 원칙을 세웠습니다."  # 상한 찬 쪽
    pages = {1: " ".join([s_env, s2]), 2: " ".join([s3, s4]), 3: s5 + " 회사는 신제품을 출시했습니다."}
    cands = [{"page": 1, "text": s2}, {"page": 2, "text": "가나은행은  스튜어드십 원칙에 따라 관여 활동을 했습니다"}]
    cands += [{"page": 3, "text": f"다른 후보 {i}"} for i in range(4)]
    d = M.detectcov(pages, cands, fin=True)
    assert (d["n"], d["env"], d["got"]) == (4, 1, 2)
    assert (d["envPct"], d["gotPct"]) == (25.0, 50.0)
    assert (d["fullPageN"], d["fullPageGot"]) == (1, 0)


def test_t7_detectcov_env_baseline():
    pages = {1: "회사는 탄소중립 목표를 세웠습니다. 회사는 재생에너지 사용을 늘렸습니다. 회사는 신제품을 출시했습니다."}
    d = M.detectcov(pages, [{"page": 1, "text": "회사는 탄소중립 목표를 세웠습니다."}], fin=False)
    assert (d["n"], d["got"]) == (2, 1)


# ---- T8 ledger ----
MODELS = {"models": {"m-a": {"pricing": {"input": 1.0, "output": 2.0, "cacheRead": 0.25, "cacheWrite": 1.25,
                                         "longContext": {"aboveInputTokens": 1000, "input": 2.0, "output": 3.0,
                                                         "cacheRead": 0.5, "cacheWrite": 2.5}}}},
          "embeddingModels": {"e-a": {"pricing": {"input": 0.5}}}}


def test_t8_ledger(tmp_path):
    good = (100 * 1.0 + 200 * 0.25 + 400 * 1.25 + 50 * 2.0) / 1e6
    rows = [
        {"model": "m-a", "inputTokens": 100, "cacheReadTokens": 200, "cacheWriteTokens": 400, "outputTokens": 50,
         "costUsd": good, "resultKind": "success"},
        {"model": "e-a", "inputTokens": 1000, "costUsd": 1000 * 0.5 / 1e6, "resultKind": "success"},
        {"model": "m-a", "inputTokens": 10, "outputTokens": 0, "costUsd": 0.0, "resultKind": "provider_error"},
    ]
    f = tmp_path / "f1-x.jsonl"
    f.write_text("\n".join(__import__("json").dumps(r) for r in rows))
    out = M.ledger([str(f)], MODELS)
    assert out["warnings"] == []
    assert out["files"]["f1-x"]["kinds"] == {"success": 2, "provider_error": 1}
    assert abs(out["usdTotal"] - (good + 1000 * 0.5 / 1e6)) < 1e-9
    rows[0]["costUsd"] = good + 0.001
    f.write_text("\n".join(__import__("json").dumps(r) for r in rows))
    assert len(M.ledger([str(f)], MODELS)["warnings"]) == 1


def test_t8_recompute_cachewrite_and_long():
    r = {"model": "m-a", "inputTokens": 0, "cacheReadTokens": 0, "cacheWriteTokens": 800, "outputTokens": 0}
    assert M.recompute(r, MODELS) == pytest.approx(800 * 1.25 / 1e6)
    r = {"model": "m-a", "inputTokens": 1200, "cacheReadTokens": 0, "cacheWriteTokens": 0, "outputTokens": 10}
    assert M.recompute(r, MODELS) == pytest.approx((1200 * 2.0 + 10 * 3.0) / 1e6)
    # 문턱은 입력 세 칸 합(검증 권고 R2): cacheWrite 로만 넘는 행
    r = {"model": "m-a", "inputTokens": 10, "cacheReadTokens": 0, "cacheWriteTokens": 1000, "outputTokens": 0}
    assert M.recompute(r, MODELS) == pytest.approx((10 * 2.0 + 1000 * 2.5) / 1e6)


# ---- T9 f5 ----
def test_t9_f5():
    def r(b, k, s, arm, level, hit):
        return {"book": b, "key": k, "sample": s, "arm": arm, "level": level, "hit": list(hit), "failure": None}

    rows = []
    # 뺀 권 F-* 4: 후보 줄이 셋에 걸림, 하나는 등급 바뀜
    rows += [r("h", "a", "holdoutF", "A15", "Mid", ("mid-1",)), r("h", "a", "holdoutF", "A15K", "Mid", ("mid-1", "fin-1"))]
    rows += [r("h", "b", "holdoutF", "A15", "None", ()), r("h", "b", "holdoutF", "A15K", "High", ("fin-2",))]
    rows += [r("h", "c", "holdoutF", "A15", "Mid", ("high-1",)), r("h", "c", "holdoutF", "A15K", "Mid", ("high-1", "fin-1"))]
    rows += [r("h", "d", "holdoutF", "A15", "None", ()), r("h", "d", "holdoutF", "A15K", "None", ())]
    # 일반 2: 오히트 하나
    rows += [r("g", "e", "general", "A15", "None", ("keyword-ghg",)), r("g", "e", "general", "A15K", "None", ("keyword-ghg",))]
    rows += [r("g", "f", "general", "A15", "Mid", ("mid-1",)), r("g", "f", "general", "A15K", "Mid", ("mid-1", "fin-3"))]
    out = M.f5(rows, floor_level_pct=10.0)
    h, g = out["holdoutF"], out["general"]
    assert (h["n"], h["candHit"], h["candHitPct"]) == (4, 3, 75.0)
    assert (h["U15"], h["U15K"]) == (3, 1)
    assert h["moves"] == {"generic_only->specific": 1, "none->none": 1, "none->specific": 1, "specific->specific": 1}
    assert h["levelDiffPct"] == 25.0 and h["delta"] == 15.0
    assert (g["n"], g["candHit"], g["candHitPct"]) == (2, 1, 50.0)
    assert out["useful"] is False  # 일반 오히트 50% > 10%


# ---- T10 verdict ----
def vt(f_share=20.0, f_books=(20.0, 20.0, 20.0, 5.0), u_gen=50.0, u_f=70.0, u_books=(70.0, 70.0, 70.0, 40.0), n_f=40):
    books = {f"n{i}": {"group": "finance", "fSharePct": s} for i, s in enumerate(f_books)}
    books["g0"] = {"group": "general", "fSharePct": 1.0}
    return {"books": books, "groups": {"finance": {"fSharePct": f_share}},
            "coverage": {"general_all": {"Upct": u_gen}, "finance_F": {"Upct": u_f, "n": n_f}},
            "financeBooksF": {f"n{i}": {"Upct": u} for i, u in enumerate(u_books)}}


EYE_OK = {"finance_F": {"pct": 80.0}, "general_G": {"pct": 75.0}}


def test_t10_verdict_branches():
    assert M.verdict(vt(f_share=10.0), 5.0, EYE_OK, 0.0)["answer"].startswith("아니오 — 금융 고유 주장이 드묾")
    assert M.verdict(vt(f_books=(20.0, 20.0, 5.0, 5.0)), 5.0, EYE_OK, 0.0)["answer"].startswith("아니오 — 금융 고유")
    assert M.verdict(vt(), 5.0, EYE_OK, 0.0)["answer"].startswith("예")
    assert M.verdict(vt(n_f=19), 5.0, EYE_OK, 0.0)["answer"].startswith("못 가름 — F-*")
    assert M.verdict(vt(u_f=58.0), 10.0, EYE_OK, 0.0)["answer"].startswith("못 가름 — 차가 흔들림")
    assert M.verdict(vt(u_f=58.0), 5.0, EYE_OK, 0.0)["answer"].startswith("아니오 — 15줄이 덮음")
    assert M.verdict(vt(u_books=(70.0, 70.0, 40.0, 40.0)), 5.0, EYE_OK, 0.0)["answer"].startswith("아니오 — 15줄이 덮음")


def test_t10_eye_validity_b1():
    eye = {"finance_F": {"pct": 80.0}, "general_G": {"pct": 69.0}}
    assert M.verdict(vt(), 5.0, eye, 0.0)["answer"] == "못 가름 — 덮임 지표 타당성 부족"
    eye = {"finance_F": {"pct": 70.0}, "general_G": {"pct": 70.0}}
    assert M.verdict(vt(), 5.0, eye, 0.0)["answer"].startswith("예")


def test_t10_eye_missing_blocks():
    assert M.verdict(vt(), 5.0, None, 0.0)["answer"] == "못 가름 — 덮임 지표 타당성 부족"
    assert M.verdict(vt(), 5.0, {"finance_F": {"pct": 90.0}}, 0.0)["answer"] == "못 가름 — 덮임 지표 타당성 부족"


def test_t10_detect_gap_b2():
    assert M.verdict(vt(f_share=10.0), 5.0, EYE_OK, 20.0)["answer"] == "못 가름 — 탐지 누락"
    assert M.verdict(vt(f_share=10.0), 5.0, EYE_OK, 19.9)["answer"].startswith("아니오 — 금융 고유")


def test_t10_eye_agreement():
    labels = [{"group": "finance_F", "cls": "none", "label": "none"}, {"group": "finance_F", "cls": "generic_only", "label": "fits"},
              {"group": "general_G", "cls": "specific", "label": "fits"}]
    a = M.eye_agreement(labels)
    assert (a["finance_F"]["n"], a["finance_F"]["agree"], a["finance_F"]["pct"]) == (2, 1, 50.0)
    assert a["general_G"]["pct"] == 100.0


def test_t10_eye_sample_seeded():
    rows = [{"book": "x", "key": f"k{i}", "cls": c} for i, c in enumerate(["specific"] * 12 + ["none"] * 3)]
    a, b = M.eye_sample(rows, 7), M.eye_sample(rows, 7)
    assert a == b and len(a) == 13


# ---- T11 코드에 단가 숫자·권 열쇠·회사 이름 0 ----
def _code_only() -> str:
    import io
    import tokenize

    toks = tokenize.generate_tokens(io.StringIO(SRC.read_text(encoding="utf-8")).readline)
    return " ".join(t.string for t in toks if t.type not in (tokenize.COMMENT, tokenize.STRING))


def test_t11_no_price_literals():
    assert re.findall(r"\b\d+\.\d+\b", _code_only()) == []


def test_t11_no_book_keys_or_paths():
    src = SRC.read_text(encoding="utf-8")
    assert re.search(r"\bb[1-5]\b", src) is None  # f1~f5 는 잴 것 이름과 겹쳐 b 열쇠만 본다
    assert re.search(r"_\d{4}_kor", src) is None and ".pdf" not in src and "/Users/" not in src


def test_t11_no_company_names():
    common = subprocess.run(["git", "-C", str(SRC.parent), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True)
    names = Path(common.stdout.strip()).parent / "out/GFC1/names.txt" if common.returncode == 0 else None
    if names is None or not names.exists():
        pytest.skip("이름 목록 없음(git 밖)")
    words = [w.strip().lower() for w in names.read_text(encoding="utf-8").splitlines() if w.strip()]
    for f in (SRC, Path(__file__)):
        low = f.read_text(encoding="utf-8").lower()
        assert [w for w in words if w in low] == [], f.name
