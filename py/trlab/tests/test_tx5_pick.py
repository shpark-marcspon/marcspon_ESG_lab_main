"""TRX5 tx5_pick — 자리 상한(luna 0.3 · sol 0.2 · 합 0.5) · 예상 · 장부 줄 칸 · 표본 재채점. 지어낸 자료, 네트워크 0."""

import json

from trlab import tx5_pick as X


def test_reserve_over_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(X, "OUT5", tmp_path)
    (tmp_path / "calls.jsonl").write_text(json.dumps({"tag": "TRX5/span/sam/0", "costUsd": 0.2995}) + "\n"
                                          + json.dumps({"tag": "TRX5/span/sam/1", "costUsd": 0.05, "dry_run": True}) + "\n")
    X._RESERVED.update(luna=0.0, sol=0.0)
    assert X.site_spent("luna") == 0.2995 and X.total_spent() == 0.2995  # dry_run 줄은 안 셈
    assert not X.reserve("luna", 0.001)  # 0.2995 + 0.001 > 0.3
    assert X.reserve("sol", 0.15)
    assert not X.reserve("sol", 0.06)  # 잡아 둔 0.15 + 0.06 > 0.2 (합 0.2995+0.15+0.06 > 0.5 도)
    X.release("sol", 0.15)
    assert X.reserve("sol", 0.2) and not X.reserve("sol", 0.001)  # 합 0.4995 — 0.001 더하면 0.5005 > 0.5
    X._RESERVED.update(luna=0.0, sol=0.0)


def test_estimate_default_then_measured(tmp_path, monkeypatch):
    monkeypatch.setattr(X, "OUT5", tmp_path)
    inp, outp, usd, src = X.estimate(X.LUNA, "가" * 1000, "i" * 40, 4)
    assert src == "default" and inp == int(1000 * 0.615) + 10 and outp == 100
    assert abs(usd - (inp * 0.1 + outp * 0.5) / 1e6) < 1e-12
    (tmp_path / "calls.jsonl").write_text(json.dumps({"tag": "TRX5/span/sam/0", "model": X.LUNA, "chars": 2000, "inputTokens": 1000,
                                                      "outputTokens": 50, "costUsd": 0.0001}) + "\n")
    inp2, outp2, _, src2 = X.estimate(X.LUNA, "가" * 1000, "", 1)
    assert src2 == "measured" and inp2 == 500 and outp2 == 50  # 출력 최소 50
    assert X.tok_per_char(X.SOL)[1] == "default"  # 다른 모델의 실측은 안 빌림


def test_send_ledger_row_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(X, "OUT5", tmp_path)
    X._RESERVED.update(luna=0.0, sol=0.0)
    import semgraph.llm as llm
    monkeypatch.setattr(llm, "respond", lambda model, instr, text, schema, out, tag, effort="low":
                        ('{"items": []}', {"tag": tag, "model": model, "inputTokens": 12, "outputTokens": 3, "costUsd": 0.000003}))
    assert X.reserve("luna", 0.01) and X._RESERVED["luna"] == 0.01
    raw, row = X.send("luna", X.LUNA, "i", "t", {}, "TRX5/span/sam/7", 0.01, chars=1, items=2)
    assert raw == '{"items": []}' and X._RESERVED["luna"] == 0.0
    lines = [json.loads(x) for x in open(tmp_path / "calls.jsonl")]
    assert len(lines) == 1 and lines[0]["tag"] == "TRX5/span/sam/7" and lines[0]["estimateUsd"] == 0.01
    assert lines[0]["chars"] == 1 and lines[0]["items"] == 2 and lines[0]["model"] == X.LUNA and lines[0]["costUsd"] == 0.000003
    X.log_skip("luna", X.LUNA, "TRX5/span/sam/8", 0.02, "over_cap", chars=5, items=1)
    lines = [json.loads(x) for x in open(tmp_path / "calls.jsonl")]
    assert lines[1]["dry_run"] and lines[1]["skip"] == "over_cap" and lines[1]["costUsd"] == 0.0 and lines[1]["estimateUsd"] == 0.02
    assert X.site_spent("luna") == 0.000003


def test_rescore_against_gold_subj():
    gold = [{"book": "sam", "unit": "p1-b1", "sha": X.sha16("It grows."), "gold_subj": "pronoun"},
            {"book": "sam", "unit": "p1-b1", "sha": X.sha16("We grow."), "gold_subj": "we"},
            {"book": "kb", "unit": "p2-b1", "sha": X.sha16("Gone."), "gold_subj": "none"}]
    rows = {"none": {("p1-b1", X.sha16("It grows.")): {"subj": "none"}, ("p1-b1", X.sha16("We grow.")): {"subj": "we"}},
            "R1": {("p1-b1", X.sha16("It grows.")): {"subj": "pronoun"}, ("p1-b1", X.sha16("We grow.")): {"subj": "we"},
                   ("p2-b1", X.sha16("Gone.")): {"subj": "none"}}}
    r = X.rescore_one(gold, rows)
    assert r["none"] == {"ok": 1, "n": 3, "missing": 1, "wrong": [{"book": "sam", "unit": "p1-b1", "sha": X.sha16("It grows."), "gold": "pronoun", "got": "none"}]}
    assert r["R1"] == {"ok": 3, "n": 3, "missing": 0, "wrong": []}
