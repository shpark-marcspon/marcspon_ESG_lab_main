"""명령: plan | dry-run | probe | run  (README 의 「명령」).

  plan     모의 공급사로 두 판(old·luna)을 돌려 호출 수와 호출마다의 dry-run 위끝(core 셈)을 더한 표 — 실호출 0
  dry-run  assemble_only 로 세 판(old·luna-raw·luna)을 돌려 공급사 전 거름(LlmConfigError 등)과 첫 요청 몸을 적은 표
  probe    live · luna · 가장 작은 입력 한 번(마크스폰 키)
  run      live(--mock 이면 모의) · --variant 판 · 전체 입력 한 번
  compare  같은 --label 의 live 실행(모듈마다 old·luna 마지막 것)을 견준 표 — out/mig1/compare-<label>.json, 실호출 0
"""

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from miglab import env, harness, labmodels, metrics, modules, responders


def _run_dir(mode: str, module: str, variant: str, size: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = env.OUT / "runs" / f"{stamp}-{mode}-{module}-{variant}-{size}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def execute(
    module: str, *, mode: str, variant: str, size: str, pdf: Path | None, pages: str | None, label: str
) -> dict[str, Any]:
    folder = _run_dir(mode, module, variant, size)
    calls_path = env.CALLS if mode == "live" else folder / "calls.jsonl"
    lab = {"module": module, "size": size, "label": label, "run": folder.name}
    responder = responders.for_module(module) if mode == "mock" else None
    harness.restore_tool_models()
    try:
        built = harness.build(mode=mode, variant=variant, lab=lab, calls_path=calls_path, responder=responder)
        patches = list(harness.PATCHES)
        options = modules.Options(variant=variant, size=size, pdf=pdf, pages=pages)
        started = time.monotonic()
        ran = modules.run_module(module, built.client, options, built.sink.run_usd)
        seconds = round(time.monotonic() - started, 1)
        iro = None
        if module in ("mt", "mt1") and mode == "dry":
            iro_ran = modules.iro_check(built.client, options)
            iro = iro_ran.error or "통과(공급사 앞 거름 없음)"
    finally:
        harness.restore_tool_models()
    summary = {
        "module": module,
        "mode": mode,
        "variant": variant,
        "size": size,
        "seconds": seconds,
        "inputKey": dict(ran.input_key),
        "error": ran.error,
        "notes": ran.notes,
        "patches": patches,
        "routesChanged": labmodels.changed_routes(variant),
        "events": [asdict(e) for e in built.client.events],
        "eventErrors": sorted({e.error for e in built.client.events if e.error}),
        "providerCalls": len(built.recorder.bodies),
        "calls": metrics.call_stats(built.sink.records),
        "metrics": metrics.for_module(module, ran.result, ran.source) if ran.result is not None else None,
        "iroCheck": iro,
        "planUpperUsd": round(sum(e.estimate_usd or 0.0 for e in built.client.events), 6),
    }
    (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if ran.result is not None:
        (folder / "result.json").write_text(
            json.dumps(ran.result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )
    with (folder / "bodies.jsonl").open("w", encoding="utf-8") as file:
        for body in built.recorder.bodies:
            file.write(json.dumps(body, ensure_ascii=False, default=str) + "\n")
    summary["folder"] = str(folder)
    return summary


def _line(summary: dict[str, Any]) -> str:
    calls = summary["calls"]
    error = summary["error"] or "-"
    return (
        f"{summary['module']:<4} {summary['variant']:<8} {summary['mode']:<4} 공급사 {summary['providerCalls']:>3} "
        f"기록 {calls['records']:>3} usd {calls['costUsd']:.4f} 위끝합 {summary['planUpperUsd']:.4f} "
        f"{summary['seconds']:>6}s 거름 {len(summary['eventErrors'])} 실패 {error[:160]}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="miglab")
    parser.add_argument("command", choices=("plan", "dry-run", "probe", "run", "compare"))
    parser.add_argument("--module", default="all")
    parser.add_argument("--variant", default="luna", choices=labmodels.VARIANTS)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--size", choices=("full", "probe"), default=None)
    parser.add_argument("--pdf", type=Path, default=None)
    parser.add_argument("--pages", default=None)
    parser.add_argument("--label", default="")
    args = parser.parse_args(argv)
    if args.command == "compare":
        from miglab import compare

        return compare.main(args.label or "pair")
    chosen = modules.MODULES if args.module == "all" else tuple(args.module.split(","))
    for name in chosen:
        if name not in modules.MODULES:
            parser.error(f"모듈은 {modules.MODULES}: {name}")

    if args.command == "plan":
        runs = [(m, "mock", v, args.size or "full") for m in chosen for v in ("old", "luna")]
    elif args.command == "dry-run":
        runs = [(m, "dry", v, args.size or "full") for m in chosen for v in labmodels.VARIANTS]
    elif args.command == "probe":
        runs = [(m, "mock" if args.mock else "live", "luna", "probe") for m in chosen]
    else:
        runs = [(m, "mock" if args.mock else "live", args.variant, args.size or "full") for m in chosen]

    if any(mode == "live" for _, mode, _, _ in runs):
        print(f"장부(calls.jsonl live 합) 시작 ${harness.ledger_total():.4f} / 상한 ${harness.TICKET_CAP_USD}")
    for module, mode, variant, size in runs:
        summary = execute(
            module, mode=mode, variant=variant, size=size, pdf=args.pdf, pages=args.pages, label=args.label
        )
        print(_line(summary))
        for err in summary["eventErrors"]:
            print(f"     거름: {err[:220]}")
        if summary["iroCheck"]:
            print(f"     iro_155: {summary['iroCheck'][:220]}")
        print(f"     → {summary['folder']}")
    if any(mode == "live" for _, mode, _, _ in runs):
        print(f"장부 끝 ${harness.ledger_total():.4f} / 상한 ${harness.TICKET_CAP_USD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
