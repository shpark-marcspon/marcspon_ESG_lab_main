"""TRX9 ②·③ 자리·장부·상한·native 배율·요청 꼴 시험 — 네트워크 0, 가짜 응답, 장부는 임시 폴더."""

import json
import types

import pytest

from raslab import ras2_check as C
from raslab import ras2_pick as P
from raslab import run as R


@pytest.fixture
def out(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "OUT9", tmp_path)
    monkeypatch.setitem(P._RESERVED, "luna", 0.0)
    monkeypatch.setitem(P._RESERVED, "sol", 0.0)
    return tmp_path


def _rows(out, *rows):
    with open(out / "calls.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_reserve_site_total_and_sub_caps(out):
    _rows(out, {"tag": "TRX9/b2sol/x", "model": "gpt-6.1-sol", "usd": 0.20}, {"tag": "TRX9/b2luna/x", "model": "gpt-6-luna", "usd": 0.05})
    assert P.reserve("sol", "b2", 0.04)  # 0.20 + 0.04 ≤ 0.25
    assert not P.reserve("sol", "b2", 0.02)  # 잡아 둔 0.04 까지 더하면 0.26 > 0.25 (판 상한)
    P.release("sol", 0.04)
    assert not P.reserve("luna", "b2", 0.06)  # 0.05 + 0.06 > 0.1 (자리 상한)
    _rows(out, {"tag": "TRX9/b3sol/x", "model": "gpt-6.1-sol", "usd": 0.45}, {"tag": "TRX9/b2luna/x", "model": "gpt-6-luna", "usd": 0.09})
    assert not P.reserve("sol", "b3", 0.03)  # 자리 0.5 안이어도 합 0.6 넘음(0.54 + 0.03 … 판 0.15 도 넘음)
    assert not P.reserve("sol", "b3", 0.01)  # 판 b3/sol 0.15 넘음
    _rows(out, {"tag": "TRX9/b3sol/x", "model": "gpt-6.1-sol", "usd": 0.10, "dry_run": True})
    assert P.reserve("sol", "b3", 0.14)  # dry_run 줄은 누적에 안 든다


def test_send_over_cap_logs_and_does_not_call(out, monkeypatch):
    _rows(out, {"tag": "TRX9/b2sol/x", "model": "gpt-6.1-sol", "usd": 0.249})
    called = []

    class Client:
        pass

    def boom(client, req):
        called.append(req)
        raise AssertionError("보내면 안 된다")

    monkeypatch.setattr(P, "respond", boom)
    res, rec = P.send(Client(), "sol", "b2", "gpt-6.1-sol", b"png", (300, 300), "TRX9/b2sol/k_p1_r0", "k", 1, 0)
    assert res is None and rec["status"] == "over_cap" and rec["usd"] == 0.0 and not called
    rows = P.calls()
    assert rows[-1]["tag"] == "TRX9/b2sol/k_p1_r0" and rows[-1]["site"] == "sol" and "estimateUsd" in rows[-1]


def test_send_request_shape_and_ledger_row(out, monkeypatch):
    seen = {}

    def fake(client, req):
        seen.update(req)
        u = types.SimpleNamespace(input_tokens=700, output_tokens=500, input_tokens_details=types.SimpleNamespace(cached_tokens=0), output_tokens_details=types.SimpleNamespace(reasoning_tokens=10))
        return types.SimpleNamespace(usage=u, status="completed", output_text=json.dumps({"items": ["a"], "tables": []}))

    monkeypatch.setattr(P, "respond", fake)
    res, rec = P.send(object(), "luna", "b2", "gpt-6-luna", b"png", (300, 300), "TRX9/b2lunah/k_p1_r0", "k", 1, 0, hint=True)
    assert res == {"items": ["a"], "tables": []}
    assert seen["model"] == "gpt-6-luna" and seen["instructions"].endswith(C.DECIMAL_HINT) and seen["instructions"].startswith(R.INSTR)
    img = seen["input"][0]["content"]
    assert len(img) == 1 and img[0]["type"] == "input_image" and img[0]["detail"] == "high" and img[0]["image_url"].startswith("data:image/png;base64,")
    assert seen["text"]["format"]["schema"] == R.SCHEMA and seen["text"]["format"]["strict"] is True
    row = P.calls()[-1]
    assert row["status"] == "completed" and row["input_tokens"] == 700 and row["output_tokens"] == 500
    assert row["usd"] == round((700 * 0.1 + 500 * 0.5) / 1e6, 6) and row["hint"] is True and row["estimateUsd"] > 0
    assert (out / "calls" / "TRX9_b2lunah_k_p1_r0.json").is_file()
    assert P._RESERVED["luna"] == 0.0  # 보낸 뒤 잡아 둔 몫 풀림


def test_estimate_uses_ras1_out_tokens_or_600():
    usd, est_in = P.estimate("gpt-6.1-sol", (750, 750))
    assert est_in == 450 + 750
    assert usd == pytest.approx((1200 * 2.0 + 600 * 10.0) / 1e6)
    usd2, _ = P.estimate("gpt-6.1-sol", (750, 750), out_tokens=1000)
    assert usd2 > usd


def test_native_scale_bounds():
    bbox = [0, 0, 100, 100]  # 100pt × 100pt
    assert P.native_scale([90.0], bbox) == (2.5, 180.0)  # 원본 dpi < 180 → 180
    assert P.native_scale([345.0], bbox) == (345.0 / 72, 345.0)
    assert P.native_scale([900.0], bbox) == (600.0 / 72, 600.0)  # > 600 → 600
    assert P.native_scale([], bbox) == (2.5, 180.0)
    big = [0, 0, 1000, 1000]  # 600dpi 면 8333² px > 4Mpx → 2000² px
    scale, dpi = P.native_scale([600.0], big)
    assert abs(1000 * scale * 1000 * scale - 4_000_000) < 1 and dpi < 600


def test_tag_and_models():
    assert P.tag("b2luna", "issi", 45, 0) == "TRX9/b2luna/issi_p45_r0"
    assert P.MODELS == {"luna": "gpt-6-luna", "sol": "gpt-6.1-sol"} and P.CAPS9 == {"luna": 0.1, "sol": 0.5} and P.TOTAL_CAP9 == 0.6
