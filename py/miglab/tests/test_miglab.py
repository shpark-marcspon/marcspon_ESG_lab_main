"""miglab 시험 — 실호출·네트워크·키 0. 요청 몸은 assemble_only(dry) 로 실제 OpenAI 어댑터가 지은 것(§7-2)."""

import json
from pathlib import Path
from typing import Any

import pytest

from miglab import env, harness, labmodels, modules

ALLOWED: frozenset[str] = frozenset({"model", "temperature", "max_output_tokens"})
"""두 판의 요청 몸에서 달라도 되는 칸(§7-2)."""
LUNA_RESERVE = 2000
"""esg-app models.json luna 항목 reasoningOutputReserveTokens — 시험이 파일에서 다시 읽어 단언한다."""


@pytest.fixture(autouse=True)
def lab_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(env, "OUT", tmp_path)
    monkeypatch.setattr(env, "CALLS", tmp_path / "calls.jsonl")
    yield tmp_path
    harness.restore_tool_models()


# ── 랩 models 파일 ──


def test_luna_raw_changes_routes_only() -> None:
    old, raw = labmodels.lab_models("old"), labmodels.lab_models("luna-raw")
    assert {k: v for k, v in raw.items() if k != "routes"} == {k: v for k, v in old.items() if k != "routes"}
    changed = {r for r in raw["routes"] if raw["routes"][r] != old["routes"][r]}
    assert changed == set(labmodels.OLD_ROUTES)
    assert all(raw["routes"][r] == [labmodels.LUNA] for r in changed)


def test_luna_adds_only_web_search_price_to_the_luna_entry() -> None:
    raw, lab = labmodels.lab_models("luna-raw"), labmodels.lab_models("luna")
    assert lab["routes"] == raw["routes"]
    for key in raw["models"]:
        if key != labmodels.LUNA:
            assert lab["models"][key] == raw["models"][key]
    before, after = raw["models"][labmodels.LUNA], lab["models"][labmodels.LUNA]
    assert set(after) - set(before) == {"hostedToolPricing"}
    assert after["hostedToolPricing"] == {"webSearchPerCall": 0.01}
    assert {k: v for k, v in after.items() if k not in ("hostedToolPricing", "notes")} == {
        k: v for k, v in before.items() if k != "notes"
    }
    assert after["notes"][:-1] == before["notes"]
    # gpt-4.1·gpt-4o 의 같은 가격표 줄 값과 같다(§7 참고)
    assert lab["models"]["gpt-4.1"]["hostedToolPricing"] == lab["models"]["gpt-4o"]["hostedToolPricing"]
    assert lab["models"]["gpt-4.1"]["hostedToolPricing"] == after["hostedToolPricing"]


# ── 같은 입력 · 다른 모델 이름만(요청 몸) ──


def _bodies(module: str, variant: str, tmp: Path) -> tuple[list[dict[str, Any]], harness.Built]:
    harness.restore_tool_models()
    built = harness.build(
        mode="dry", variant=variant, lab={"module": module}, calls_path=tmp / f"{module}-{variant}.jsonl"
    )
    modules.run_module(module, built.client, modules.Options(variant=variant), built.sink.run_usd)
    harness.restore_tool_models()
    return built.recorder.bodies, built


@pytest.mark.parametrize("module", ["tr", "dv", "mt", "s3", "dr"])
def test_two_variants_send_the_same_body_but_model_temperature_and_output_cap(module: str, lab_out: Path) -> None:
    old, _ = _bodies(module, "old", lab_out)
    luna, _ = _bodies(module, "luna", lab_out)
    assert old, "옛 판이 요청을 하나도 짓지 않았다"
    assert len(old) == len(luna)
    config = json.loads(env.ESGAPP_MODELS.read_text(encoding="utf-8"))
    reserve = config["models"][labmodels.LUNA]["reasoningOutputReserveTokens"]
    assert reserve == LUNA_RESERVE
    luna_cap = config["models"][labmodels.LUNA]["maxOutputTokens"]
    for before, after in zip(old, luna, strict=True):
        differing = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        assert differing <= ALLOWED, differing
        assert after["model"] == labmodels.LUNA
        assert before["model"] in ("gpt-4.1", "gpt-4.1-mini", "gpt-4o")
        assert "temperature" not in after
        assert after["max_output_tokens"] == min(luna_cap, before["max_output_tokens"] + reserve)


def test_luna_raw_is_refused_before_the_provider_where_the_code_sends_temperature(lab_out: Path) -> None:
    bodies, built = _bodies("dr", "luna-raw", lab_out)
    assert bodies == []
    assert built.sink.records == []


def test_the_tool_patch_does_not_leak_past_one_run(lab_out: Path) -> None:
    _bodies("dv", "luna", lab_out)
    assert labmodels.LUNA not in harness.client_mod.TOOL_MODELS
    bodies, _ = _bodies("dv", "luna-raw", lab_out)
    assert bodies == []


# ── 비용 셈 ──


def test_ledger_counts_live_lines_only(lab_out: Path) -> None:
    rows = [
        {"lab": {"mode": "live"}, "costUsd": 0.25},
        {"lab": {"mode": "live"}, "costUsd": None},
        {"lab": {"mode": "mock"}, "costUsd": 9.0},
        {"lab": {"mode": "live"}, "costUsd": 0.125},
    ]
    env.CALLS.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert harness.ledger_total() == 0.375


def test_sink_spent_adds_the_ledger_so_the_cap_spans_runs(lab_out: Path) -> None:
    from esgapi.core.llm.types import Scope

    sink = harness.LabSink(lab_out / "c.jsonl", {"module": "x"}, prior_usd=4.9)
    assert sink.spent(Scope()) == 4.9
    assert sink.run_usd() == 0.0


def test_luna_cost_is_the_core_formula_with_the_lab_price() -> None:
    """luna 단가(입력 0.1·캐시 읽기 0.01·출력 0.5 USD/MTok, 272k 아래) — 손셈: 비캐시 입력 10,000 + 캐시 읽기 2,000 + 출력 3,000
    (core Usage.input_tokens 는 캐시를 뺀 몫 — providers/openai._to_usage)."""
    from esgapi.core.llm.config import load_config
    from esgapi.core.llm.cost import compute_cost_usd
    from esgapi.core.llm.types import Usage

    path = labmodels.write_lab_models("luna")
    config = load_config((path,))
    luna = next(m for m in config.models.values() if m.api_model_id == labmodels.LUNA)
    usage = Usage(input_tokens=10_000, output_tokens=3_000, cache_read_tokens=2_000, cache_write_tokens=0)
    expected = (10_000 * 0.1 + 2_000 * 0.01 + 3_000 * 0.5) / 1e6
    assert compute_cost_usd(usage, luna.pricing, 0, None) == pytest.approx(expected, abs=1e-9)
    assert compute_cost_usd(usage, luna.pricing, 2, luna.hosted_tool_pricing) == pytest.approx(expected + 0.02, abs=1e-9)
