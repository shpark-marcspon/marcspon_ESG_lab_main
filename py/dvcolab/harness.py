"""코랩 쪽 실행: 읽기판 셀을 지은 사례로 돌려 `dvcolab.result/1` 꼴의 결과를 짓는다.

차례(한 이름공간):
1. 하네스가 `data`(지은 입력)·`fname` 과 셀 2 의 import 셋(json·os·islice)을 미리 넣는다 — 셀 2(입력 붙여넣기
   자리)는 돌리지 않는다.
2. 셀 3·4·7 — 테마 거르기, 파일 왕복(작업 폴더 안), 도구·스키마·분배기·고리 정의. 셀 7 뒤 하네스가 `TOLERANCE` 전역을
   사례의 허용 오차로 둔다(도구 사례는 그 값을 인자로도 받는다).
3. 사례(calls)마다 하네스가 `_value_cache` 를 새 dict 로 갈아 끼우고(코랩의 `clear_value_cache()` 와 같은 일, 코랩 코드
   고침 아님) 코랩 함수를 부른다. `split_themes` 사례는 셀 3 을 그 입력으로 새 이름공간에서 돌린다.
4. 기대 사례(expectations): 코랩이 캐시를 비우지 않고 두 번 부르면 옛 값을 낸다(약점 5, `MARC-ISSUE-186`).
5. 셀 10·14·18 — 가짜 requests 가 보낸 system·user 를 받고 짜 둔 답을 돌려준다. 셀 14 의 `HINTS` 는 셀 10 의 답에서
   `ast.literal_eval` 로 나온다(★d).
6. 고리 사례(loops, DV1b) — 셀 7 의 `run_responses_loop` 을 사례마다 부른다(셀 10 뒤 — 고리가 쓰는 주소를 셀 10 이 정의).
하네스 단언(어긋나면 `HarnessError` — 도구 실패): 셀 10 의 답이 `plan` 에 남음, `HINTS` = 사례의 hints, `data_split` 과
`SET_MAX_STEPS` 가 지은 값에서 나온 것, 셀마다 보낸 부름이 하나.
"""

import builtins
import contextlib
import copy
import io
import json
import os
import itertools
from typing import Any

from dvcolab.cases import S1
from dvcolab.cells import CodeCell
from dvcolab.compare import FORMAT
from dvcolab.sandbox import FAKE_KEY, Sandbox, run_cell

PROMPT_CELLS = {10: "plan", 14: "review", 18: "insight"}
SETUP_CELLS = (3, 4, 7)
PLAIN = {"tool_sum_check", "tool_sum_n_check", "tool_ratio_check"}
WITH_DATA = {
    "tool_value_of_by_name",
    "tool_sum_check_by_name",
    "tool_sum_n_check_by_names",
    "tool_ratio_check_by_name",
    "tool_search_items",
}
FNAME = "synthetic-dvcolab"


class HarnessError(Exception):
    """하네스 단언 어긋남 — 지은 값이 코랩 쪽에 뜻대로 들어가지 않았다."""


def _outcome(run: Any) -> dict[str, Any]:
    try:
        value = run()
    except Exception as error:  # 예외도 견줄 값이다
        return {"outcome": "raise", "value": f"{type(error).__name__}: {error}"}
    return {"outcome": "ok", "value": json.loads(json.dumps(value, ensure_ascii=False))}  # 튜플 → 목록(엔진 쪽 JSON 과 같은 꼴)


def _split_by_cell_3(cell3: CodeCell, given: Any, log: io.TextIOBase) -> Any:
    scratch: dict[str, Any] = {"__name__": "__main__", "__builtins__": builtins, "data": given}
    run_cell(cell3, scratch, log)
    return scratch["data_split"]


def _call(ns: dict[str, Any], cell3: CodeCell, call: dict[str, Any], data: Any, tolerance: Any, log: io.TextIOBase) -> Any:
    fn: str = call["fn"]
    args = call.get("args", [])
    pos = list(args) if isinstance(args, list) else []
    kw = dict(args) if isinstance(args, dict) else {}
    if fn in PLAIN:
        return ns[fn](*pos, **kw)
    if fn in WITH_DATA:
        return ns[fn](data, *pos, **kw)
    if fn == "_run_local_tool":
        name, tool_args = pos
        return ns[fn](name, tool_args, data=data, tolerance=tolerance)
    if fn == "split_themes":
        return _split_by_cell_3(cell3, pos[0], log)
    raise HarnessError(f"모르는 fn: {fn!r}")


def _expectations(ns: dict[str, Any], base: Any) -> list[dict[str, Any]]:
    """코랩 쪽에서만 보는 기대 — 약점 5: 캐시를 비우지 않으면 데이터가 바뀌어도 옛 값."""
    ns["_value_cache"] = {}
    first = ns["tool_value_of_by_name"](copy.deepcopy(base), *S1, "2022")
    changed = copy.deepcopy(base)
    changed["환경"][0]["values"]["2022"] = 7
    stale = ns["tool_value_of_by_name"](changed, *S1, "2022")
    ns["_value_cache"] = {}
    fresh = ns["tool_value_of_by_name"](changed, *S1, "2022")
    held = (first, stale, fresh) == ({"value": 1200.0}, {"value": 1200.0}, {"value": 7.0})
    return [{"id": "weakness-5-stale-cache", "held": held, "what": "비우지 않고 두 번 → 옛 1200.0, 비운 뒤 → 7.0"}]


def _loops(ns: dict[str, Any], cases: dict[str, Any], box: Sandbox, log: io.TextIOBase) -> list[dict[str, Any]]:
    """고리 사례(DV1b): 셀 7 의 `run_responses_loop` 을 사례마다 부른다 — 캐시는 새 dict(사례마다 `_value_cache = {}`),
    `api_key` 는 가짜 글, `tools` 는 이름공간의 스키마(인사이트는 web_search 스키마), `data` 는 사례 입력의 「환경」, 모델은
    코랩 셀의 gpt-4.1. 가짜 post 가 짜 둔 답을 돌려주고 실행된 호출·결과 글을 적는다. 짜 둔 답을 다 쓰지 않으면 하네스
    단언(사례가 뜻대로 돌지 않았다)."""
    out: list[dict[str, Any]] = []
    data_split = {"환경": copy.deepcopy(cases["data"]["환경"])} if "환경" in cases["data"] else {}
    for case in cases.get("loops", []):
        ns["_value_cache"] = {}
        tools = ns["TOOLS_RESPONSES_websearch"] if case["stage"] == "insight" else ns["TOOLS_RESPONSES"]
        box.start_loop(case["script"])
        with contextlib.redirect_stdout(log):
            returned = ns["run_responses_loop"](
                "가상 system", "가상 user", api_key=FAKE_KEY, model="gpt-4.1", tools=tools,
                max_steps=case["maxSteps"], data=data_split, tolerance=cases["tolerance"],
            )  # fmt: skip
        record = box.end_loop()
        if record["unusedAnswers"]:
            raise HarnessError(f"고리 {case['id']}: 짜 둔 답 {record['unusedAnswers']}개가 남았다")
        text = returned[0] if isinstance(returned, tuple) else returned
        out.append(
            {
                "id": case["id"],
                "executed": record["executed"],
                "outputs": record["outputs"],
                "providerCalls": record["providerCalls"],
                "ending": "partial" if record["lastHadCalls"] else "done",
                "text": text,
            }
        )
    return out


def run_colab(cells: list[CodeCell], cases: dict[str, Any], box: Sandbox, log: io.TextIOBase) -> tuple[dict[str, Any], dict[str, Any]]:
    """`sandboxed` 안에서 부른다. (결과, 하네스 기록)을 돌려준다."""
    by_number = {c.number: c for c in cells}
    base = cases["data"]
    tolerance = cases["tolerance"]
    # 셀 2 가 하던 import 셋도 미리 넣는다(셀 4 가 json·islice 를 쓴다). clear_value_cache 는 셀 7 이 다시 정의한다.
    ns: dict[str, Any] = {"data": copy.deepcopy(base), "fname": FNAME, "json": json, "os": os, "islice": itertools.islice}
    ops = ["preset data·fname·json·os·islice(셀 2 안 돌림)"]
    for n in SETUP_CELLS:
        run_cell(by_number[n], ns, log)
    expected_split = {"환경": base["환경"]} if "환경" in base else {}
    if json.dumps(ns["data_split"], ensure_ascii=False) != json.dumps(expected_split, ensure_ascii=False):
        raise HarnessError("셀 3·4·7 뒤 data_split 이 지은 입력의 「환경」이 아니다")
    e_global = "e" in ns
    ns["TOLERANCE"] = tolerance
    ops.append(f"셀 7 뒤 TOLERANCE = {tolerance!r}")
    calls: list[dict[str, Any]] = []
    for call in cases["calls"]:
        ns["_value_cache"] = {}
        data = copy.deepcopy(call.get("data", base))
        with contextlib.redirect_stdout(log):
            calls.append({"id": call["id"], **_outcome(lambda: _call(ns, by_number[3], call, data, tolerance, log))})  # noqa: B023
    ops.append("사례마다 _value_cache = {}")
    with contextlib.redirect_stdout(log):
        expectations = _expectations(ns, base)
    prompts: list[dict[str, Any]] = []
    snapshots: dict[str, dict[str, Any]] = {}
    variables = {p["kind"]: p for p in cases["prompts"]}
    for n, kind in PROMPT_CELLS.items():
        before = len(box.posts)
        run_cell(by_number[n], ns, log)
        sent = box.posts[before:]
        if len(sent) != 1:
            raise HarnessError(f"셀 {n}: 보낸 부름이 {len(sent)}개(하나여야 한다)")
        snapshots[kind] = {k: ns.get(k) for k in ("MODEL_RESP", "TOLERANCE", "SET_MAX_STEPS")}
        entry = variables[kind]
        prompts.append({"id": entry["id"], "system": sent[0]["system"], "user": sent[0]["user"]})
    # 고리 사례는 셀 10·14·18 뒤에 돈다: 고리의 _post 가 쓰는 RESPONSES_URL 은 셀 7 에서는 글자 안·주석이고(R/104·R/111)
    # 셀 10 이 정의한다(R/810). 하네스가 값을 넣지 않는다.
    loops = _loops(ns, cases, box, log)
    ops.append(f"셀 10·14·18 뒤 고리 사례 {len(loops)}(사례마다 _value_cache = {{}})")
    if ns.get("plan") != box.answers[0]:
        raise HarnessError("셀 10 의 답이 plan 에 남지 않았다")
    hints = variables["review"]["vars"]["hints"]
    if ns.get("HINTS") != hints:
        raise HarnessError("셀 14 의 HINTS 가 사례의 hints 와 다르다(짜 둔 셀 10 답을 본다)")
    if snapshots["review"]["SET_MAX_STEPS"] != len(hints) * 3:
        raise HarnessError("셀 14 의 SET_MAX_STEPS 가 len(HINTS)*3 이 아니다")
    models = {s["MODEL_RESP"] for s in snapshots.values()}
    expectations.append({"id": "one-model-all-cells", "held": len(models) == 1, "what": "셀 10·14·18 의 MODEL_RESP 가 하나"})
    expectations.append(
        {
            "id": "plan-insight-tolerance",
            "held": snapshots["plan"]["TOLERANCE"] == snapshots["insight"]["TOLERANCE"],
            "what": "셀 10·18 의 TOLERANCE 가 같다(셀 14 는 앞 값을 씀)",
        }
    )
    constants = {
        "TOOLS_RESPONSES": ns["TOOLS_RESPONSES"],
        "TOOLS_RESPONSES_websearch": ns["TOOLS_RESPONSES_websearch"],
        "MODEL_RESP": snapshots["plan"]["MODEL_RESP"],
        "TOLERANCE": snapshots["plan"]["TOLERANCE"],
        "PLAN_MAX_STEPS": snapshots["plan"]["SET_MAX_STEPS"],
        "REVIEW_MAX_STEPS": snapshots["review"]["SET_MAX_STEPS"],
        "INSIGHT_MAX_STEPS": snapshots["insight"]["SET_MAX_STEPS"],
    }
    result = {
        "format": FORMAT,
        "side": "colab",
        "seed": cases.get("seed"),
        "random": cases.get("random", 0),
        "calls": calls,
        "prompts": prompts,
        "constants": json.loads(json.dumps(constants, ensure_ascii=False)),
        "expectations": expectations,
        "loops": loops,
    }
    record = {
        "harness_ops": ops,
        "e_global_before_calls": e_global,
        "posts": [{k: v for k, v in p.items() if k not in ("system", "user")} for p in box.posts],
        "fake_events": box.events,
    }
    return result, record
