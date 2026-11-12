"""MA2 장부 — 자리 상한·밴드 4.5 문턱·얼림 전 실호출 막음·두 장부 합(지은 줄만, 네트워크 0)."""
import json

import pytest

from evalrig.translation import measure_ma2 as M


def _row(tag, usd, **kw):
    return {"tag": tag, "model": "gpt-6-luna", "costUsd": usd, **kw}


def test_site_of_tags():
    assert M.site_of("MA2/lab/tr/sam-35-54-toc/c0") == "lab/tr"
    assert M.site_of("MA2/t6/mini/c1") == "t6"
    assert M.site_of("TRX10/full/x") is None


def test_over_cap_skips_site_cap():
    lab = [_row("MA2/lab/fix/b1", 0.30)]
    assert M.over_cap("lab/fix", 0.04, lab, [], {}) is None
    assert M.over_cap("lab/fix", 0.06, lab, [], {}) == "over_cap"  # 0.30 + 0.06 > 0.35
    assert M.over_cap("lab/fix", 0.04, lab, [], {"lab/fix": 0.02}) == "over_cap"  # 잡아 둔 몫도 셈


def test_dry_rows_do_not_count():
    lab = [_row("MA2/lab/fix/b1", 0.30, dry_run=True, skip="dry_run")]
    assert M.site_spent("lab/fix", lab) == 0.0


def test_band_stop_at_4_5():
    v1 = [{"site": "v1", "costUsd": 4.40}]
    assert M.over_cap("lab/tr", 0.05, [], v1, {}) is None
    assert M.over_cap("lab/tr", 0.11, [], v1, {}) == "band_stop"


def test_unknown_site_refused():
    with pytest.raises(ValueError):
        M.over_cap("lab/zzz", 0.0, [], [], {})


def test_two_ledgers_summed(tmp_path):
    (tmp_path / "v1" / "sam-35-54").mkdir(parents=True)
    (tmp_path / "v1" / "sam-35-54-dry").mkdir(parents=True)
    # 1차 calls.jsonl 꼴(CallRecord.to_json 의 costUsd 칸) — 지은 줄
    (tmp_path / "v1" / "sam-35-54" / "calls.jsonl").write_text(
        "\n".join(json.dumps({"model": "gpt-4.1-mini", "costUsd": c, "inputTokens": 10, "outputTokens": 5}) for c in (0.1, 0.2)) + "\n")
    (tmp_path / "v1" / "sam-35-54-dry" / "calls.jsonl").write_text(json.dumps({"costUsd": 9.0}) + "\n")  # dry 폴더는 안 셈
    (tmp_path / "calls.jsonl").write_text(json.dumps(_row("MA2/lab/tr/c0", 0.05, inputTokens=1000, outputTokens=100)) + "\n")
    lab, v1 = M.lab_rows(tmp_path), M.v1_rows(tmp_path)
    s = M.ledger_summary(lab, v1)
    assert s["band_usd"] == pytest.approx(0.35)
    assert s["sites"]["v1"]["calls"] == 2 and s["sites"]["lab/tr"]["usd"] == pytest.approx(0.05)
    assert s["sites"]["lab/tr"]["recomputed"] == pytest.approx((1000 * 0.1 + 100 * 0.5) / 1e6)
    assert s["band_ok"]


def test_assert_frozen_blocks_before_freeze(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "OUT", tmp_path)
    with pytest.raises(SystemExit, match="first-run"):
        M.assert_frozen(object())


def test_assert_frozen_detects_change(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "OUT", tmp_path)
    monkeypatch.setattr(M, "freeze_lines", lambda P: ["a", "b"])
    (tmp_path / "first-run.txt").write_text("# 머리\na\nb\n")
    M.assert_frozen(None)
    (tmp_path / "first-run.txt").write_text("# 머리\na\nc\n")
    with pytest.raises(SystemExit, match="바뀌었다"):
        M.assert_frozen(None)


def test_default_out_is_main_checkout():
    assert ".worktrees" not in str(M.OUT) and str(M.OUT).endswith("esg-lab/out/ma2")


def test_band_stop_counts_reserved():
    v1 = [{"site": "v1", "costUsd": 4.40}]
    assert M.over_cap("lab/tr", 0.05, [], v1, {}) is None
    assert M.over_cap("lab/tr", 0.05, [], v1, {"lab/chk": 0.06}) == "band_stop"  # 4.40 + 잡아 둔 0.06 + 0.05 > 4.5


def test_head_stop_before_command(tmp_path, monkeypatch):
    import json as _j
    monkeypatch.setattr(M, "OUT", tmp_path)
    (tmp_path / "plan.json").write_text(_j.dumps({"rows": [{"자리": "t6", "어림 USD": 0.2}]}))
    M.head_stop(["t6"])
    (tmp_path / "v1" / "sam-35-54").mkdir(parents=True)
    (tmp_path / "v1" / "sam-35-54" / "calls.jsonl").write_text(_j.dumps({"costUsd": 4.4}) + "\n")
    with pytest.raises(SystemExit, match="멈춤"):
        M.head_stop(["t6"])


def test_recompute_v1_cache_read_field():
    # 1차 CallRecord 꼴: inputTokens = 캐시 안 탄 몫, cacheReadTokens 따로(지은 줄)
    v1 = [{"site": "v1", "model": "gpt-4.1-mini", "inputTokens": 900, "cacheReadTokens": 3000, "outputTokens": 100, "costUsd": 0.00082}]
    s = M.ledger_summary([], v1)
    assert s["sites"]["v1"]["recomputed"] == pytest.approx((900 * 0.4 + 3000 * 0.1 + 100 * 1.6) / 1e6)
