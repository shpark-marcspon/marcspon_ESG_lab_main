"""TRX7 tx7_pick — 자리 상한(4.1-mini 0.25 / luna 0.1 / sol 0.15 / 합 0.5) · 예상 · 장부 줄 칸 · 줄이는 차례 · llm.py 비추론 분기. 네트워크 0."""

import json
import sys
import types

from trlab import tx7_pick as X


def _reset(tmp_path, monkeypatch):
    monkeypatch.setattr(X, "OUT7", tmp_path)
    X._RESERVED.update({X.MINI: 0.0, X.LUNA: 0.0, X.SOL: 0.0})


def test_reserve_over_cap_per_model_and_total(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    (tmp_path / "calls.jsonl").write_text(json.dumps({"tag": "TRX7/review/V2/gpt-4.1-mini/u1", "model": X.MINI, "costUsd": 0.2495}) + "\n"
                                          + json.dumps({"tag": "TRX7/names/N0/gpt-4.1-mini/1/s1", "model": X.MINI, "costUsd": 0.05, "dry_run": True}) + "\n")
    assert X.site_spent(X.MINI) == 0.2495 and X.total_spent() == 0.2495  # dry_run 줄은 안 셈
    assert not X.reserve(X.MINI, 0.001)  # 0.2495 + 0.001 > 0.25
    assert X.reserve(X.LUNA, 0.1) and not X.reserve(X.LUNA, 0.001)
    assert X.reserve(X.SOL, 0.15) and not X.reserve(X.SOL, 0.001)
    X.release(X.SOL, 0.15)
    X.release(X.LUNA, 0.1)
    (tmp_path / "calls.jsonl").write_text(json.dumps({"tag": "t", "model": X.LUNA, "costUsd": 0.09}) + "\n"
                                          + json.dumps({"tag": "t", "model": X.SOL, "costUsd": 0.14}) + "\n"
                                          + json.dumps({"tag": "t", "model": X.MINI, "costUsd": 0.24}) + "\n")
    assert X.reserve(X.MINI, 0.01) and not X.reserve(X.LUNA, 0.03)  # luna 0.09 + 0.03 > 0.1 → 자리로 막힘
    assert not X.reserve(X.SOL, 0.011)  # 0.14 + 0.011 > 0.15
    assert X.reserve(X.SOL, 0.01) and X.reserve(X.LUNA, 0.01)  # 자리 딱(0.15·0.10), 합 0.47 + 잡아 둔 0.03 = 0.50 — 딱은 된다
    assert not X.reserve(X.MINI, 0.0001)  # 합 0.5 넘김 — 자리(0.24+0.01+0.0001 ≤ 0.25)는 들어도 막힘
    _reset(tmp_path, monkeypatch)


def test_total_cap_blocks_even_when_site_fits(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    (tmp_path / "calls.jsonl").write_text(json.dumps({"tag": "t", "model": X.MINI, "costUsd": 0.25}) + "\n"
                                          + json.dumps({"tag": "t", "model": X.SOL, "costUsd": 0.15}) + "\n"
                                          + json.dumps({"tag": "t", "model": X.LUNA, "costUsd": 0.05}) + "\n")
    assert X.reserve(X.LUNA, 0.05) and not X.reserve(X.LUNA, 0.0001)  # 합 0.5 딱 — 더는 못 보냄
    _reset(tmp_path, monkeypatch)


def test_estimate_default_then_measured(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    inp, outp, usd, src = X.estimate(X.MINI, "i" * 100, "가" * 900, "review")
    assert src == "default/default" and inp == 400 and outp == 250
    assert abs(usd - (400 * 0.4 + 250 * 1.6) / 1e6) < 1e-12
    assert X.estimate(X.LUNA, "", "가" * 1000, "review")[1] == 600 and X.estimate(X.MINI, "", "가" * 10, "names")[1] == 80
    (tmp_path / "calls.jsonl").write_text(json.dumps({"tag": "TRX7/review/V2/gpt-4.1-mini/u1", "model": X.MINI, "chars": 2000, "inputTokens": 1000,
                                                      "outputTokens": 120, "costUsd": 0.0001}) + "\n"
                                          + json.dumps({"tag": "TRX7/review/V2/gpt-4.1-mini/u2", "model": X.MINI, "chars": 2000, "inputTokens": 1500,
                                                        "outputTokens": 300, "costUsd": 0.0001}) + "\n")
    inp2, outp2, _, src2 = X.estimate(X.MINI, "", "가" * 1000, "review")
    assert src2 == "measured/measured" and inp2 == 500 and outp2 == 300  # 첫 줄의 토큰/글자, 출력은 중앙값(위쪽)
    assert X.estimate(X.MINI, "", "가" * 1000, "names")[1] == 80 and X.tok_per_char(X.LUNA)[1] == "default"  # 갈래·모델은 안 빌림


def test_send_ledger_row_fields_and_skip(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    import semgraph.llm as llm
    seen = {}

    def fake(model, instr, text, schema, out, tag, effort="low"):
        seen["effort"] = effort
        return '{"issues": []}', {"tag": tag, "model": model, "inputTokens": 12, "outputTokens": 3, "costUsd": 0.000003}

    monkeypatch.setattr(llm, "respond", fake)
    assert X.reserve(X.MINI, 0.01) and X._RESERVED[X.MINI] == 0.01
    raw, row = X.send(X.MINI, "i", "t", {}, "TRX7/review/V0r/gpt-4.1-mini/u1", 0.01, chars=2, items=1)
    assert raw == '{"issues": []}' and X._RESERVED[X.MINI] == 0.0 and seen["effort"] == "low"
    X.log_skip(X.LUNA, "TRX7/review/V2/gpt-6-luna/u2", 0.02, "over_cap", chars=5, items=1)
    lines = [json.loads(x) for x in open(tmp_path / "calls.jsonl")]
    assert lines[0]["tag"] == "TRX7/review/V0r/gpt-4.1-mini/u1" and lines[0]["estimateUsd"] == 0.01 and lines[0]["chars"] == 2 and lines[0]["items"] == 1
    assert lines[1]["dry_run"] and lines[1]["skip"] == "over_cap" and lines[1]["costUsd"] == 0.0 and lines[1]["model"] == X.LUNA
    assert X.site_spent(X.MINI) == 0.000003 and X.site_spent(X.LUNA) == 0.0


def test_shrink_order():
    mini = [("V2", [("planted-01", 0.1), ("real-01", 0.1)]), ("V0e", [("planted-01", 0.1), ("real-01", 0.1)]), ("V0r", [("planted-01", 0.1), ("real-01", 0.1)])]
    pl, steps = X.shrink(X.MINI, mini, 0.6)
    assert steps == [] and pl == mini
    pl, steps = X.shrink(X.MINI, mini, 0.45)
    assert steps == ["V2: 실제 오역 단위 뺌", "V0e: 실제 오역 단위 뺌"] and pl[0][1] == [("planted-01", 0.1)] and pl[2][1] == mini[2][1]
    pl, steps = X.shrink(X.MINI, mini, 0.3)
    assert steps[:3] == ["V2: 실제 오역 단위 뺌", "V0e: 실제 오역 단위 뺌", "V0r: 실제 오역 단위 뺌"] and sum(len(i) for _, i in pl) == 3
    luna = [("V2", [("planted-01", 0.01), ("clean-01", 0.01), ("real-01", 0.01)])]
    pl, steps = X.shrink(X.LUNA, luna, 0.015)
    assert steps == ["V2: 심은 단위만"] and pl[0][1] == [("planted-01", 0.01)]
    sol = [("V3", [(f"u{i}", 0.01) for i in range(12)])]
    pl, steps = X.shrink(X.SOL, sol, 0.09)
    assert steps == ["V3: 12 → 8"] and len(pl[0][1]) == 8
    pl, steps = X.shrink(X.SOL, sol, 0.05)
    assert steps == ["V3: 12 → 8", "V3 뺌"] and pl[0][1] == []


def test_llm_no_reasoning_branch_only_for_mini(monkeypatch):
    """llm.respond: gpt-4.1-mini 는 reasoning 을 싣지 않고, 다른 모델은 그대로 싣는다(§6 1 ㉠)."""
    import semgraph.llm as llm
    calls = []

    class _Resp:
        output_text = "{}"
        status = "completed"
        usage = types.SimpleNamespace(input_tokens=10, output_tokens=2, input_tokens_details=None)

    class _Client:
        def __init__(self, **kw):
            self.responses = types.SimpleNamespace(create=lambda **kw: calls.append(kw) or _Resp())

    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = _Client
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    monkeypatch.setattr(llm, "_log", lambda out, row: None)
    llm.respond(X.MINI, "i", "t", {}, "/tmp", "tag")
    llm.respond(X.LUNA, "i", "t", {}, "/tmp", "tag", effort="low")
    assert "reasoning" not in calls[0] and calls[0]["model"] == X.MINI and calls[0]["text"]["format"]["type"] == "json_schema"
    assert calls[1]["reasoning"] == {"effort": "low"} and calls[1]["model"] == X.LUNA
    assert llm.PRICES[X.MINI] == (0.4, 0.1, 1.6) and X.MINI in llm.NO_REASONING and X.LUNA not in llm.NO_REASONING
