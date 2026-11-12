"""TRX10 tx10_pick — 자리 셋(luna-full 0.45 / mini 0.25 / luna-reask 0.1, 합 0.8)·태그 앞머리·예상(글자 × 토큰/글자 + 출력 × 블록 수)·
100 짝 선실측 셈·장부 칸(elapsed_ms·site·unit_ids·estimateUsd)·쪽 차례로 줄이기·얼림 전 실호출 막기. 네트워크 0."""

import json

import pytest

from trlab import tx10_pick as X


def _reset(tmp_path, monkeypatch):
    monkeypatch.setattr(X, "OUT10", tmp_path)
    X._RESERVED.update({s: 0.0 for s in X.SITES})


def _row(tag, usd, **kw):
    d = {"tag": tag, "model": X.LUNA if "gpt-6-luna" in tag else X.MINI, "costUsd": usd}
    d.update(kw)
    return json.dumps(d) + "\n"


def test_site_of_by_tag_prefix():
    assert X.site_of("TRX10/full/gpt-6-luna/p1-b1") == "luna-full" and X.site_of("TRX10/bundle/gpt-6-luna/3") == "luna-full"
    assert X.site_of("TRX10/reask/gpt-6-luna/p1-b1") == "luna-reask"
    assert X.site_of("TRX10/full/gpt-4.1-mini/p1-b1") == "mini" and X.site_of("TRX10/bundle/gpt-4.1-mini/1") == "mini"
    assert X.site_of("TRX7/review/V2/gpt-6-luna/x") is None and X.site_of("") is None and X.site_of("TRX10/full/gpt-6.1-sol/x") is None
    assert X.CAPS10 == {"luna-full": 0.45, "mini": 0.25, "luna-reask": 0.1} and X.TOTAL_CAP10 == 0.8 and X.THREADS == 1


def test_reserve_over_cap_per_site_and_total(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    (tmp_path / "calls.jsonl").write_text(_row("TRX10/full/gpt-6-luna/a", 0.3) + _row("TRX10/bundle/gpt-6-luna/1", 0.1495)
                                          + _row("TRX10/full/gpt-6-luna/b", 0.5, dry_run=True))
    assert abs(X.site_spent("luna-full") - 0.4495) < 1e-12 and X.site_spent("luna-reask") == 0 and abs(X.total_spent() - 0.4495) < 1e-12  # dry_run 안 셈
    assert not X.reserve("luna-full", 0.001)  # 0.4495 + 0.001 > 0.45
    assert X.reserve("luna-full", 0.0005) and not X.reserve("luna-full", 0.0001)  # 잡아 둔 몫도 센다
    X.release("luna-full", 0.0005)
    assert X.reserve("mini", 0.25) and not X.reserve("mini", 0.0001)
    assert X.reserve("luna-reask", 0.1) and not X.reserve("luna-reask", 0.0001)
    X.release("mini", 0.25)
    X.release("luna-reask", 0.1)
    (tmp_path / "calls.jsonl").write_text(_row("TRX10/full/gpt-6-luna/a", 0.45) + _row("TRX10/full/gpt-4.1-mini/a", 0.25)
                                          + _row("TRX10/reask/gpt-6-luna/a", 0.09))
    assert X.reserve("luna-reask", 0.01) and not X.reserve("luna-reask", 0.0001)  # 합 0.8 딱
    X.release("luna-reask", 0.01)
    (tmp_path / "calls.jsonl").write_text(_row("TRX10/full/gpt-6-luna/a", 0.40) + _row("TRX10/full/gpt-4.1-mini/a", 0.25)
                                          + _row("TRX10/reask/gpt-6-luna/a", 0.1))
    assert not X.reserve("luna-full", 0.0501)  # 자리(0.45)는 들어도 합 0.8 넘김
    assert X.reserve("luna-full", 0.05)
    _reset(tmp_path, monkeypatch)


def test_site_column_wins_over_tag_when_present(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    (tmp_path / "calls.jsonl").write_text(_row("TRX10/full/gpt-6-luna/a", 0.2, site="luna-full") + _row("odd/tag", 0.05, site="mini"))
    assert X.site_spent("luna-full") == 0.2 and X.site_spent("mini") == 0.05


def test_estimate_default_then_measured_and_first_n(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    inp, outp, usd, src = X.estimate(X.LUNA, 1000, 1, "full")
    assert (inp, outp, src) == (366, 161, "default(0)") and abs(usd - (366 * 0.1 + 161 * 0.5) / 1e6) < 1e-12
    inp, outp, usd, src = X.estimate(X.MINI, 1000, 4, "bundle")
    assert (inp, outp) == (286, 63 * 4) and abs(usd - (286 * 0.4 + 252 * 1.6) / 1e6) < 1e-12  # 묶음은 출력 × 블록 수
    rows = "".join(_row(f"TRX10/full/gpt-6-luna/u{i}", 0.0001, chars=1000, inputTokens=300 + (i % 3), outputTokens=100 + (i % 5), items=1)
                   for i in range(100))
    rows += _row("TRX10/full/gpt-6-luna/u100", 0.0001, chars=1000, inputTokens=9000, outputTokens=9000, items=1)  # 101번째 — 100 짝 셈엔 안 든다
    rows += _row("TRX10/bundle/gpt-6-luna/1", 0.0001, chars=1000, inputTokens=5000, outputTokens=800, items=4)
    (tmp_path / "calls.jsonl").write_text(rows)
    k, o, src, n = X.calib(X.LUNA, "full", first_n=100)
    assert src == "measured" and n == 100 and 0.300 <= k <= 0.302 and 100 <= o <= 104
    k2, o2, _, n2 = X.calib(X.LUNA, "full")
    assert n2 == 101 and k2 == k and o2 == o  # 중앙값이라 하나가 커도 안 흔들림 — first_n 이 그 100 만 쓰는 것은 n 으로 본다
    kb, ob, _, nb = X.calib(X.LUNA, "bundle")
    assert nb == 1 and kb == 5.0 and ob == 200  # 갈래별, 블록당 출력 = outputTokens / items
    assert X.calib(X.MINI, "full")[2] == "default" and X.calib(X.LUNA, "reask")[2] == "default"  # 모델·갈래는 안 빌림


def test_send_ledger_row_fields_and_skip(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    import semgraph.llm as llm
    seen = {}

    def fake(model, instr, text, schema, out, tag, effort="low"):
        seen["effort"] = effort
        return '{"issues": []}', {"tag": tag, "model": model, "inputTokens": 12, "outputTokens": 3, "costUsd": 0.000003, "seconds": 2.345}

    monkeypatch.setattr(llm, "respond", fake)
    assert X.reserve("luna-full", 0.01) and X._RESERVED["luna-full"] == 0.01
    raw, row = X.send(X.LUNA, "i", "t", {}, "TRX10/full/gpt-6-luna/p1-b1", 0.01, ["p1-b1"], chars=2, items=1)
    assert raw == '{"issues": []}' and X._RESERVED["luna-full"] == 0.0 and seen["effort"] == "low"
    X.log_skip(X.MINI, "TRX10/full/gpt-4.1-mini/p1-b2", 0.02, "over_cap", ["p1-b2"], chars=5, items=1)
    lines = [json.loads(x) for x in open(tmp_path / "calls.jsonl")]
    assert lines[0]["elapsed_ms"] == 2345 and lines[0]["site"] == "luna-full" and lines[0]["unit_ids"] == ["p1-b1"]
    assert lines[0]["estimateUsd"] == 0.01 and lines[0]["chars"] == 2 and lines[0]["items"] == 1
    assert lines[1]["dry_run"] and lines[1]["skip"] == "over_cap" and lines[1]["costUsd"] == 0.0 and lines[1]["site"] == "mini" and lines[1]["unit_ids"] == ["p1-b2"]
    assert X.site_spent("luna-full") == 0.000003 and X.site_spent("mini") == 0.0


def test_shrink_pages_from_last_page():
    units = [{"id": f"p{p}-b{b}", "page": p} for p in (1, 2, 3, 4) for b in (1, 2)]
    kept, cut = X.shrink_pages(units, lambda u: 0.1, 0.8)
    assert cut == [] and len(kept) == 8
    kept, cut = X.shrink_pages(units, lambda u: 0.1, 0.45)
    assert cut == [3, 4] and [u["page"] for u in kept] == [1, 1, 2, 2]
    kept, cut = X.shrink_pages(units, lambda u: 0.1, 0.0)
    assert kept == [] and cut == [1, 2, 3, 4]


def test_assert_frozen_blocks_calls_before_first_run(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError):
        X.assert_frozen()
    (tmp_path / "first-run.txt").write_text("# x\n")
    X.assert_frozen()


def test_call_dry_run_logs_only_and_over_cap_writes_status(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    row = {"id": "p1-b1"}
    st, raw, lrow = X._call(X.LUNA, "i" * 10, "t" * 90, {}, "TRX10/full/gpt-6-luna/p1-b1", ["p1-b1"], "full", 1, True, tmp_path / "o.json", row)
    assert st == "dry_run" and raw is None and row["chars"] == 100 and row["est"]["src"] == "default(0)" and not (tmp_path / "o.json").exists()
    assert json.loads(open(tmp_path / "calls.jsonl").readline())["skip"] == "dry_run"
    (tmp_path / "first-run.txt").write_text("# x\n")
    (tmp_path / "calls.jsonl").write_text(_row("TRX10/full/gpt-6-luna/a", 0.45))
    st, raw, lrow = X._call(X.LUNA, "i", "t", {}, "TRX10/full/gpt-6-luna/p1-b1", ["p1-b1"], "full", 1, False, tmp_path / "o.json", dict(row))
    assert st == "over_cap" and json.load(open(tmp_path / "o.json"))["status"] == "over_cap"
    st, _, _ = X._call(X.LUNA, "i", "t", {}, "TRX10/full/gpt-6-luna/p1-b1", ["p1-b1"], "full", 1, False, tmp_path / "o.json", dict(row))
    assert st == "exists"
    lines = [json.loads(x) for x in open(tmp_path / "calls.jsonl")]
    assert [x.get("skip") for x in lines[1:]] == ["over_cap"]
