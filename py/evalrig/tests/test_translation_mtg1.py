"""MTG1 순수 셈 시험(지은 자료만, 네트워크 0) — 발주서 §3-1 T1~T14."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from evalrig.translation import measure_mtg1 as M

GATES_SRC = '''
import re
def structure_tag_signature(text):
    return re.findall(
        r"^\\s*\\[(?:PAGE\\s+\\d+|BLOCK\\s+\\d+|TABLE\\s+\\d+|VISUAL\\s+\\d+|HEADER|FOOTER|PAGE_NUMBER|CAPTION|ANNOTATION|"
        r"QUALITY_CHECK|UNREADABLE|POSSIBLY_MISSING|MERGE_QUALITY_CHECK)\\]\\s*$",
        str(text or ""),
        flags=re.IGNORECASE | re.MULTILINE,
    )
'''
TAG_RE = M.compile_tag_re(M.tag_pattern_from_gates(GATES_SRC))
KO = "가"  # 지은 국문 한 글자


def _tags(*ns: int) -> str:
    return "\n".join(f"[BLOCK {n}]\nbody {n}" for n in ns)


# T1 짧음
def test_t1_short() -> None:
    assert M.is_short(3, KO * 60)
    assert not M.is_short(4, KO * 10)
    assert not M.is_short(1, KO * 61)
    assert M.is_short(1, " ".join(KO * 60)) and M.squeezed(" ".join(KO * 60)) == 60


def test_tag_pattern_from_gates_reads_concatenated_literal() -> None:
    pat = M.tag_pattern_from_gates(GATES_SRC)
    assert pat.startswith(r"^\s*\[(?:PAGE") and pat.endswith(r"\]\s*$") and "CAPTION" in pat


# T2 A0
def test_t2_a0() -> None:
    assert M.verdict_a0("BLOCK 2", "[BLOCK 2]\nHello", "ok", TAG_RE) == ("ok", "Hello")
    assert M.verdict_a0("BLOCK 2", "Hello", "ok", TAG_RE)[0] == "dropped"
    assert M.verdict_a0("BLOCK 2", "[BLOCK 3]\nHello", "ok", TAG_RE)[0] == "shifted"
    assert M.verdict_a0("BLOCK 2", "[BLOCK 2]\nA\n[BLOCK 3]\nB", "ok", TAG_RE)[0] == "shifted"
    assert M.verdict_a0("BLOCK 2", "[BLOCK 2]\n   \n", "ok", TAG_RE)[0] == "dropped"  # critic B2 — 몸 공백뿐
    assert M.verdict_a0("BLOCK 2", "[BLOCK 2]\nA", "cutoff", TAG_RE)[0] == "call_failed"  # critic B1


# T3 A1
def test_t3_a1() -> None:
    body = "  Some text\n\nsecond para "
    fixed = M.a1_fix("BLOCK 4", body, TAG_RE)
    assert fixed == "[BLOCK 4]\n" + body and fixed.endswith(body)
    assert M.verdict_a0("BLOCK 4", fixed, "ok", TAG_RE)[0] == "ok"
    tagged = "[BLOCK 4]\nx"
    assert M.a1_fix("BLOCK 4", tagged, TAG_RE) == tagged


# T4 A2
def test_t4_a2() -> None:
    exp = [f"BLOCK {i}" for i in (1, 2, 3, 4)]
    v = M.verdict_page_tags(exp, "[PAGE 9]\n" + _tags(1, 2, 4), "ok", TAG_RE)["verdict"]
    assert v == {"BLOCK 1": "ok", "BLOCK 2": "ok", "BLOCK 3": "dropped", "BLOCK 4": "ok"}
    v = M.verdict_page_tags(exp, _tags(1, 3, 2, 4), "ok", TAG_RE)["verdict"]
    assert v == {"BLOCK 1": "ok", "BLOCK 3": "shifted", "BLOCK 2": "shifted", "BLOCK 4": "shifted"}
    v = M.verdict_page_tags(exp, _tags(1, 2, 2, 3, 4), "ok", TAG_RE)["verdict"]
    assert v == {"BLOCK 1": "ok", "BLOCK 2": "shifted", "BLOCK 3": "ok", "BLOCK 4": "ok"}
    v = M.verdict_page_tags(exp, "[BLOCK 1]\na\n[BLOCK 2]\n\n[BLOCK 3]\nc\n[BLOCK 4]\nd", "ok", TAG_RE)["verdict"]
    assert v["BLOCK 2"] == "dropped"  # critic B2 — 태그만 있고 몸이 빔
    r = M.verdict_page_tags(exp, "lead\n" + _tags(1, 2, 3, 4) + "\n[BLOCK 9]\nz", "ok", TAG_RE)
    assert r["unknown"] == 1 and r["outside"] == 1 and set(r["verdict"].values()) == {"ok"}


# T5 B
def test_t5_b() -> None:
    exp = ["BLOCK 1", "BLOCK 2", "BLOCK 3", "VISUAL 1"]
    items = [{"id": "BLOCK 1", "translation": "a"}, {"id": "BLOCK 2", "translation": " "},
             {"id": "BLOCK 3", "translation": "c"}, {"id": "BLOCK 3", "translation": "c2"}, {"id": "BLOCK 7", "translation": "z"}]
    r = M.verdict_items(exp, items, "ok")
    assert r["verdict"] == {"BLOCK 1": "ok", "BLOCK 2": "dropped", "BLOCK 3": "shifted", "VISUAL 1": "dropped"}
    assert r["unknown"] == 1 and r["retry"] == ["BLOCK 2", "VISUAL 1"]
    merged = M.merge_retry(items, [{"id": "BLOCK 2", "translation": "b"}, {"id": "VISUAL 1", "translation": "v"}], r["retry"])
    assert M.verdict_items(exp, merged, "ok")["verdict"]["BLOCK 2"] == "ok"
    assert M.verdict_items(exp, None, "call_failed")["verdict"] == {t: "call_failed" for t in exp}


# T6 분모 밖
def test_t6_denominator() -> None:
    units = [{"kind": "block", "has_ko": True}, {"kind": "block", "has_ko": False}, {"kind": "table", "has_ko": True}]
    assert [M.in_denom(u) for u in units] == [True, False, False]
    rows = [{"book": "x", "page": 1, "tag": "BLOCK 1", "short": True, "verdict": "ok"},
            {"book": "x", "page": 1, "tag": "BLOCK 2", "short": True, "verdict": "call_failed"},
            {"book": "x", "page": 1, "tag": "BLOCK 3", "short": True, "verdict": "dropped"}]
    t = M.tally(rows)["short"]
    assert t["n"] == 3 and t["call_failed"] == 1 and t["D"] == pytest.approx(1 / 2)


# T7 tally 손셈: 쪽 셋 × 팔 셋
def test_t7_tally() -> None:
    def rows(spec: dict[int, list[tuple[bool, str]]]) -> list[dict]:
        return [{"book": "x", "page": p, "tag": f"BLOCK {i}", "short": s, "verdict": v}
                for p, us in spec.items() for i, (s, v) in enumerate(us, 1)]
    arms = {
        "A0": rows({1: [(True, "ok"), (True, "dropped")], 2: [(False, "ok")], 3: [(True, "ok"), (False, "shifted")]}),
        "A2": rows({1: [(True, "ok"), (True, "ok")], 2: [(False, "ok")], 3: [(True, "ok"), (False, "ok")]}),
        "B": rows({1: [(True, "shifted"), (True, "ok")], 2: [(False, "dropped")], 3: [(True, "ok"), (False, "ok")]}),
    }
    want = {"A0": (1 / 3, 1 / 2, 1 / 3), "A2": (0.0, 0.0, 1.0), "B": (1 / 3, 1 / 2, 1 / 3)}
    for arm, (ds, dl, match) in want.items():
        t = M.tally(arms[arm])
        assert t["short"]["D"] == pytest.approx(ds) and t["long"]["D"] == pytest.approx(dl)
        assert t["page_match"] == pytest.approx(match)
    t = M.tally(arms["A0"], cut_pages=[("x", 1)])
    assert t["excl_cut"]["short"]["n"] == 1


# T8 쪽 고름
def test_t8_pages() -> None:
    def u(page: int, n: int) -> list[dict]:
        return [{"page": page, "kind": "block", "n_members": 1, "ko": KO} for _ in range(n)]
    units = u(2, 5) + u(3, 9) + sum((u(p, 4) for p in range(4, 14)), [])
    toc = [{"text": "CEO Message", "start_page": 2}, {"text": "Overview", "start_page": 3}]
    sel = M.select_pages(units, toc, range(1, 15))
    assert sel["letter"] == [2]
    assert sel["top"] == [3, 4, 5, 6, 7, 8, 9, 10]  # 같은 수(4)는 앞 쪽부터
    assert sel["S"] == [2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert M.select_pages(units, toc, [p for p in range(1, 15) if p != 2])["letter"] == []  # 글자 층 없는 쪽은 뺌


PRICES = {"m-core": {"input": 2.0, "cacheRead": 0.5, "cacheWrite": 2.5, "output": 8.0},
          "m-sg": {"input": 1.0, "cacheRead": 0.25, "cacheWrite": 1.0, "output": 4.0}}


# T9 장부
def test_t9_ledger() -> None:
    core = {"apiModelId": "m-core", "inputTokens": 1000, "cacheReadTokens": 2000, "cacheWriteTokens": 0, "thinkingTokens": 10,
            "outputTokens": 100, "costUsd": (1000 * 2.0 + 2000 * 0.5 + 100 * 8.0) / 1e6, "resultKind": "success"}
    sg = {"model": "m-sg", "inputTokens": 3000, "cachedTokens": 1000, "outputTokens": 200,
          "costUsd": (2000 * 1.0 + 1000 * 0.25 + 200 * 4.0) / 1e6}
    led = M.ledger({"a": [core], "s": [sg]}, PRICES)
    assert led["warnings"] == []
    assert led["form"]["core"] == pytest.approx(core["costUsd"]) and led["form"]["semgraph"] == pytest.approx(sg["costUsd"])
    assert led["by"]["a|m-core"]["cache_read"] == 2000 and led["by"]["s|m-sg"]["cache_read"] == 1000
    bad = dict(core, costUsd=core["costUsd"] + 0.001)
    assert len(M.ledger({"a": [bad]}, PRICES)["warnings"]) == 1


# T10 풀: 블록 대 쪽
def test_t10_pool() -> None:
    from trlab import span_incons as SI
    gloss = [{"kr": "녹색위원회", "en": "Green Committee"}]
    rows = [{"page": p, "tag": f"BLOCK {i}", "ko": "녹색위원회 활동", "en": "e", "verdict": "ok", "order": i}
            for p in (1, 2) for i in (1, 2)]
    up = M.unit_pairs(rows)
    pp = M.page_pairs(up)
    assert len(up) == 4 and len(pp) == 2
    assert SI.pool_from(up, gloss, 3)["terms"] == ["녹색위원회"]  # 단위 넷 ≥ 3
    assert SI.pool_from(pp, gloss, 3)["terms"] == []  # 쪽 둘 < 3
    assert len(M.unit_pairs(rows + [dict(rows[0], tag="BLOCK 9", verdict="dropped")])) == 4


# T11 span 재사용
def test_t11_span_reuse() -> None:
    batch = {"pairs": [{"id": "1:BLOCK 1", "page": 1, "ko": "가나", "en": "x", "terms": ["가나"]}]}
    cache: dict = {}
    todo, hit = M.split_cached(batch, cache)
    assert todo is not None and hit == []
    M.remember(todo, [{"pair": "1:BLOCK 1", "term": "가나", "span": "x", "kind": "exact"}], cache)
    todo2, hit2 = M.split_cached(batch, cache)
    assert todo2 is None and hit2[0]["span"] == "x"  # 부름 0
    changed = {"pairs": [dict(batch["pairs"][0], en="y")]}
    todo3, _ = M.split_cached(changed, cache)
    assert todo3 is not None  # 글 sha 다름 → 부름


def _ta(**kw) -> dict:
    x = {"short_den": {"A0": 200, "A2": 200, "B": 200}, "cf_rate": {"A0": 0.0, "A2": 0.0, "B": 0.0},
         "eye": {"A0": 18, "A2": 18, "B": 18}, "verifier": 9,
         "bad": {"A0": 2, "A2": 20, "A2r2": 20, "B": 2, "Br2": 2},
         "bleu": {"A0": 30.0, "A2": 30.0, "B": 30.0}, "viol": {"A2": 0.1, "B": 0.1}, "rk": {"A2": 1, "B": 1},
         "usd": {"A2": 0.1, "B": 0.1}}
    for k, v in kw.items():
        x[k] = {**x[k], **v} if isinstance(v, dict) else v
    return x


# T12 T-A 갈래 다섯 + 못 가름 둘
def test_t12_verdict_ta() -> None:
    assert M.verdict_ta(_ta())["answer"] == "예"
    assert M.verdict_ta(_ta(bad={"A2": 3, "A2r2": 3}))["branch"] == 2
    assert M.verdict_ta(_ta(bad={"B": 12, "Br2": 12}))["branch"] == 3
    assert M.verdict_ta(_ta(bad={"A0": 0, "B": 4, "Br2": 4}))["branch"] == 4
    assert M.verdict_ta(_ta(usd={"B": 0.2}))["branch"] == 5
    assert M.verdict_ta(_ta(bleu={"B": 28.5}))["branch"] == 5
    assert M.verdict_ta(_ta(short_den={"B": 99}))["answer"] == "못 가름"
    assert M.verdict_ta(_ta(eye={"A2": 15}))["answer"] == "못 가름"
    assert M.verdict_ta(_ta(verifier=7))["answer"] == "못 가름"
    assert M.verdict_ta(_ta(verifier=None))["verifier_pending"] is True


def test_t12_eye_agree() -> None:
    its = [{"code": "ok", "label": "own"}, {"code": "dropped", "label": "own"}, {"code": "shifted", "label": "mixed"},
           {"code": "ratio_flag", "label": "partial"}]
    assert M.eye_agree(its) == 3


# T13 T-B
def test_t13_verdict_tb() -> None:
    assert M.verdict_tb({"a": 31, "b": 29, "c": 40}, {"a": 3, "b": 0, "c": 4}, (9, 10))["answer"] == "예"
    assert M.verdict_tb({"a": 29, "b": 29, "c": 40}, {"a": 3, "b": 0, "c": 4}, (9, 10))["answer"] == "못 가름"
    assert M.verdict_tb({"a": 31, "b": 29, "c": 40}, {"a": 3, "b": 0, "c": 1}, (9, 10))["answer"] == "아니오 — 드묾"
    assert M.denominator_side({"a": 50, "b": 50, "c": 40}, {"a": 39, "b": 40, "c": 39}) == "블록 단위가 분모를 세움"
    assert M.denominator_side({"a": 50, "b": 50, "c": 40}, {"a": 45, "b": 40, "c": 39}) == "범위가 세움"


def test_eyecheck_sample_fixed() -> None:
    rows = [{"book": "x", "page": p, "tag": f"BLOCK {i}", "short": True, "verdict": "dropped" if i == 1 else "ok"}
            for p in range(1, 11) for i in range(1, 4)]
    a, b = M.eyecheck_sample(rows), M.eyecheck_sample(rows)
    assert a == b and len(a) == 20 and sum(r["verdict"] == "dropped" for r in a) == 10


# 검증 B1 — T-A 판 실비에 T-B 장부가 섞이지 않음
def test_b1_run_usd_excludes_tb() -> None:
    by = {"B-luna-r1-sam|gpt-6-luna": {"usd": 0.03}, "B-luna-r1-kb|gpt-6-luna": {"usd": 0.01},
          "TB-luna-r1-sam|gpt-6-luna": {"usd": 0.30}, "B-luna-r2-sam|gpt-6-luna": {"usd": 0.05}}
    assert M.run_usd(by, "B-luna-r1") == pytest.approx(0.04)
    assert M.run_usd(by, "TB-luna-r1") == pytest.approx(0.30)


# 검증 B2 — 원문 없는 분모 단위는 call_failed, 분모 집합이 다르면 거부
def test_b2_not_run_and_denoms() -> None:
    us = [{"tag": "BLOCK 1", "kind": "block", "has_ko": True, "page": 3},
          {"tag": "BLOCK 2", "kind": "block", "has_ko": True, "page": 3},
          {"tag": "BLOCK 3", "kind": "block", "has_ko": False, "page": 3}]
    ver, not_run = M.complete_verdicts(us, {"BLOCK 1": "ok"})
    assert ver == {"BLOCK 1": "ok", "BLOCK 2": "call_failed"} and not_run == ["BLOCK 2"]
    exp = M.denom_keys(us, "x", [3])
    assert exp == {"x:3:BLOCK 1", "x:3:BLOCK 2"}
    rows = [{"book": "x", "page": 3, "tag": t, "in_denom": True} for t in ("BLOCK 1", "BLOCK 2")]
    M.check_denoms({"A": rows, "B": rows}, exp)
    with pytest.raises(SystemExit):
        M.check_denoms({"A": rows, "B": rows[:1]}, exp)
    pages = {"S": [1, 2], "letter": [1], "rest": [5]}
    assert M.run_pages("TB-luna-r1", pages) == [5] and M.run_pages("A0-41-r1", pages) == [1]
    assert M.run_pages("B-luna-r1", pages) == [1, 2]


# 검증 r1 — 갈래 4 의 여유 max(1, F) 경계
def test_r1_branch4_boundary() -> None:
    assert M.verdict_ta(_ta(bad={"A0": 0, "B": 1, "Br2": 1}))["answer"] == "예"
    assert M.verdict_ta(_ta(bad={"A0": 0, "B": 2, "Br2": 2}))["branch"] == 4


# J2 — 검증자 일치 비율 읽기(표본 8 = 모집단 전부) · 글자대로 답을 나란히
def test_j2_verifier_ratio() -> None:
    terms, fix = {"a": 31, "b": 45, "c": 38}, {"a": 1, "b": 3, "c": 0}
    r = M.verdict_tb(terms, fix, (7, 8))
    assert r["answer"] == "아니오 — 드묾" and r["literal"] == "못 가름"
    assert M.verdict_tb(terms, fix, (6, 8))["answer"] == "못 가름"  # 75% < 80%
    assert M.verdict_tb(terms, fix, (8, 10))["literal"] == "아니오 — 드묾"
    assert M.verifier_ok(8, 10) and not M.verifier_ok(7, 10)


# 검증 권고 N2 — 품질 단위 열쇠에 책이 든다(책 사이 같은 쪽·태그가 겹치지 않음)
def test_n2_unit_keys_have_book() -> None:
    rows = [{"book": b, "page": 4, "tag": "BLOCK 1", "ko": KO, "en": "x", "verdict": "ok"} for b in ("sam", "cos")]
    keys = [u.key for u in M._unit_objs(rows, "B-luna-r1")]
    assert len(set(keys)) == 2


# T14 코드에 단가 숫자·회사 이름·책 국문 0
ALLOWED_FLOATS = {0.0, 0.0005, 0.05, 0.35, 0.5, 0.8, 1.0, 1.5, 1e6}  # 이름 붙은 문턱·어림 계수(단가 아님)


def test_t14_no_prices_names_text() -> None:
    import ast
    src = Path(M.__file__).read_text(encoding="utf-8")
    floats = {n.value for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Constant) and isinstance(n.value, float)}
    assert floats <= ALLOWED_FLOATS, floats - ALLOWED_FLOATS
    assert "PRICES" not in src
    names = M.OUT / "names.txt"
    if names.exists():
        low = src.casefold()
        assert [n for n in names.read_text(encoding="utf-8").splitlines() if n.strip() and n.casefold() in low] == []
    assert re.findall(r"[가-힣]{12,}", src) == [], "책 국문 같은 긴 한글 덩이"


# 검증 권고 V7 — tally·verdict 명령이 분모 단언을 실제로 부른다
def test_v7_commands_call_check_denoms(tmp_path, monkeypatch) -> None:
    import argparse
    import json as _json
    (tmp_path / "pages.json").write_text(_json.dumps({"books": {b: {"S": [1], "letter": [], "rest": []} for b in M.BOOKS}}))
    monkeypatch.setattr(M, "OUT", tmp_path)
    monkeypatch.setattr(M, "units_doc", lambda b: {"units": []})
    fake = {"rows": [], "cut_pages": [], "other": {}}
    monkeypatch.setattr(M, "load_units", lambda r: fake if r in ("A0-luna-r1", "B-luna-r1") else None)

    def boom(rows_by_run, expected):
        if rows_by_run:  # T-A 판이 든 부름만(빈 편지 쪽 묶음은 세지 않음)
            raise RuntimeError("check_denoms 불림")
    monkeypatch.setattr(M, "check_denoms", boom)
    for cmd in (M.cmd_tally, M.cmd_verdict):
        with pytest.raises(RuntimeError, match="check_denoms"):
            cmd(argparse.Namespace())
