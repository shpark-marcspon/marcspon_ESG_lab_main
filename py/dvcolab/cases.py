"""견줄 사례(전부 지은 것). 꼴 `dvcolab.cases/1` 의 정본이 이 머리다 — esg-app `scripts/dataverify_compare_dump.py` 가
읽기만 한다.

```
{"format": "dvcolab.cases/1", "seed": <int|null>, "random": <int>,
 "data": {<테마>: [<항목>…]},           # 지은 입력(「환경」 열둘 + 다른 테마 하나)
 "tolerance": 1.1,                      # 분배기 사례의 실행 허용 오차
 "calls": [{"id", "fn", "args": [...] | {...}, "data"?: {...}}],
 "prompts": [{"id", "kind": "plan" | "review" | "insight", "vars": {...}}],
 "loops": [{"id", "stage": "plan" | "review" | "insight", "maxSteps": <int>,       # 선택 칸(DV1b) — 없으면 고리 대조 없음
            "script": [{"calls": [[<이름>, <인자 글>], …], "text"?: <글>}       # 함수 호출 답(글이 있으면 그 앞 message)
                     | {"text": <글>, "citations"?: [{"url", "title"}], "hosted"?: <int>}]}]}   # 글 답(끝남)
```

- `loops`(DV1b): 고리 하나 = 짜 둔 Responses 답의 차례. 코랩 쪽은 셀 7 뒤 이름공간의 `run_responses_loop` 을 가짜
  `requests.post` 로 돌리고(`data` = 사례 입력의 「환경」, `tolerance`), 엔진 쪽은 esg-app 흐름의 `run_loop` 을 모의 공급사로
  돈다. 견줌 = 실행된 (이름, 인자 글) 차례 · function_call_output 글 · 공급사 호출 수 · 끝남/부분 · 마지막 글.
  부분으로 끝나는 고리의 마지막 답에는 글을 꼭 준다(코랩은 message 가 없으면 응답 JSON 통째를 리포트로 쓴다 — 그 차이는
  사례를 그렇게 지어 생기지 않게 한다). 도구 이름 `web_search` 는 넣지 않는다(코랩은 건너뛰고 엔진은 분배기로 보낸다 —
  esg-app DV1b 보고 「다른 자리」, 실제로는 닿지 않는 갈래).

- `fn`: `tool_sum_check`·`tool_sum_n_check`·`tool_ratio_check`(인자만), `tool_value_of_by_name`·`tool_sum_check_by_name`·
  `tool_sum_n_check_by_names`·`tool_ratio_check_by_name`·`tool_search_items`(앞에 data), `_run_local_tool`
  (args = [이름, 인자], data·tolerance 는 사례의 것), `split_themes`(args = [입력 dict] — 코랩 쪽은 셀 3 을 그 입력으로 돌린다).
- `data` 칸이 있으면 그 사례만 그 입력을 쓴다(`_safe_float` 무작위 사례가 `tool_value_of_by_name` 을 거치는 자리).
- 「환경」 항목 열둘은 esg-app `esgapi/dataverify/tests/engines/colab_156/support.py` 와 같은 값이고, 둘 다 손으로 적는다
  (esg-lab 은 esg-app 을 import 하지 않는다). 손 사례는 DV1a 시험의 사례 전부(도구 여덟·분배기 35·search 9·테마 7).
- `--random N --seed S`: hypothesis 전략과 같은 값 모둠에서 `random.Random(S)` 로 N 개. 같은 seed 면 같은 사례.
"""

import json
import random
from typing import Any

FORMAT = "dvcolab.cases/1"
TOLERANCE = 1.1

S1 = ["온실가스 배출량", "직접 배출량", "Scope 1", "tCO2eq"]
S1_TON = ["온실가스 배출량", "직접 배출량", "Scope 1", "ton"]
S2 = ["온실가스 배출량", "간접 배출량", "Scope 2", "tCO2eq"]
TOTAL = ["온실가스 배출량", "총 배출량", "Scope 1+2", "tCO2eq"]
ENERGY = ["에너지", "총 에너지", "총 에너지 사용량", "TJ"]
RENEW = ["에너지", "재생에너지", "재생에너지 사용량", "TJ"]
RATIO = ["에너지", "재생에너지", "재생에너지 비율", "%"]
WASTE = ["폐기물", "총 폐기물", "폐기물 발생량", "ton"]
RECYCLED = ["폐기물", "처리 방법", "재활용량", "ton"]
LANDFILL = ["폐기물", "처리 방법", "매립량", "ton"]
WATER = ["용수", "취수", "총 취수량", "m3"]
YEARS = ["2022", "2023", "2024"]
QUADS = [S1, S1_TON, S2, TOTAL, ENERGY, RENEW, RATIO, WASTE, RECYCLED, LANDFILL, WATER]


def item(n: int, quad: list[str], values: dict[str, Any], key: str = "", memo: str = "", theme: str = "환경") -> dict[str, Any]:
    대분류, 중분류, 소분류, 단위 = quad
    return {
        "ID": f"E-{n:03d}",
        "테마": theme,
        "항목": f"가상 항목 {n}",
        "대분류": 대분류,
        "중분류": 중분류,
        "소분류": 소분류,
        "입력 방식": "직접 입력",
        "단위": 단위,
        "values": values,
        "핵심데이터": key,
        "메모": memo,
    }


def environment() -> list[dict[str, Any]]:
    """지은 「환경」 항목 열둘 — esg-app support.environment() 와 같은 값(손으로 따로 적음)."""
    return [
        item(1, S1, {"2022": 1200, "2023": "1,250", "2024": 1300.5}, key="Y"),
        item(2, S2, {"2022": 300, "2023": " 12 ", "2024": "N/A"}),
        item(3, TOTAL, {"2022": 1500, "2023": 1262, "2024": 1605}, key="Y", memo="가상 메모"),
        item(4, S1_TON, {"2022": 9, "2023": 8, "2024": 7}),
        item(5, S1, {"2022": 99999, "2023": 99999, "2024": 99999}),
        item(6, ENERGY, {"2022": 100, "2023": 0, "2024": "100"}, key="Y"),
        item(7, RENEW, {"2022": 25, "2023": 5, "2024": ""}),
        item(8, RATIO, {"2022": 0.25, "2023": 1.0, "2024": 25}),
        item(9, WASTE, {"2022": None, "2023": "abc", "2024": 50}, key="Y"),
        item(10, RECYCLED, {"2022": 10, "2023": 20, "2024": "nan"}),
        item(11, LANDFILL, {"2022": 5, "2023": "inf", "2024": 25}),
        item(12, WATER, {"2022": "1,234", "2023": "1 234", "2024": "12,34"}),
    ]


SOCIAL = [item(1, ["가상 사회", "가상", "가상", "명"], {"2022": 1}, theme="사회")]


def data() -> dict[str, Any]:
    """입력: 「환경」 열둘과 다른 테마 하나(셀 3 이 걸러 낸다)."""
    return {"환경": environment(), "사회": SOCIAL}


# 프롬프트 변수 한 벌(지은 것). 중대토픽에 `{괄호}`, HINTS 둘(하나에 `{x}`).
COMPANY = {
    "company_name": "가상건설",
    "sasb_industry": "Engineering & Construction Services",
    "material_topics": ["기후변화 대응", "안전보건", "가상 토픽 {괄호}"],
    "reporting_year": "2025",
}
HINTS = [
    "총 배출량(Scope 1+2) = 직접 배출량(Scope 1) + 간접 배출량(Scope 2)",
    "재생에너지 비율 = 재생에너지 / 총 에너지 {x}",
]


def _hand_calls() -> list[dict[str, Any]]:
    made: list[dict[str, Any]] = []

    def add(fn: str, args: Any, **extra: Any) -> None:
        made.append({"id": f"hand-{len(made) + 1:03d}-{fn}", "fn": fn, "args": args, **extra})

    # value_of_by_name: 열한 네 칸 × 해 여섯
    for quad in QUADS:
        for year in [*YEARS, "2025", 2022, None]:
            add("tool_value_of_by_name", [*quad, year])
    add("tool_value_of_by_name", ["없는", "가상", "항목", "t", "2022"])
    for args in [(1200, 300, 1500, 1.1), (1200, 300, 1505, 1.1), ("1200", 300, 1500, 1.1), (1200.5, 300, 1500, 1.1),
                 (0.1, 0.2, 0.3, 0), (1, 2, 4, 1.0)]:  # fmt: skip
        add("tool_sum_check", list(args))
    for bad in ["N/A", None, "", "1,234", [1]]:
        add("tool_sum_check", [bad, 1, 2, 1.1])
        add("tool_sum_check", [1, 2, bad, 1.1])
    for args in [([1, 2], 3, 1.1), ([1, "N/A"], 3, 1.1), (None, 3, 1.1), ([], 0, 0.0), ([1.5, "2"], 3.5, 0), (["1,2"], 1, 1)]:
        add("tool_sum_n_check", list(args))
    for args in [(25, 100, 0.25, 1.1), (25, 100, 25, 1.1), (100, 100, 1.0, 1.1), (1, 100, 1.0, 1.1), (5, 0, 1, 1.1),
                 (5, 1e-16, 1, 1.1), ("x", 1, 1, 1.1), (1, 3, 33.3333, 0.001), (0, 5, 0, 0), (2, 100, -0.5, 1.1)]:  # fmt: skip
        add("tool_ratio_check", list(args))
    for years in [["2022"], ["2023"], [], [2022], ["2022", "2024"]]:
        add("tool_sum_check_by_name", [TOTAL, S1, S2, years, 1.1])
    add("tool_sum_n_check_by_names", [[RECYCLED, LANDFILL], WASTE, YEARS, 1.1])
    add("tool_sum_n_check_by_names", [[S1, S2], TOTAL, ["2022", "2023"], 1.1])
    add("tool_ratio_check_by_name", [RENEW, ENERGY, RATIO, YEARS, 1.1])
    for kwargs in [{}, {"대분류": "온실가스 배출량"}, {"중분류": "처리 방법"}, {"소분류_contains": "Scope"},
                   {"대분류": "폐기물", "소분류_contains": "량"}, {"limit": 3, "offset": 2}, {"limit": 0}, {"offset": 50},
                   {"대분류": ""}]:  # fmt: skip
        add("tool_search_items", kwargs)
    memo_less = data()
    del memo_less["환경"][0]["메모"]
    del memo_less["환경"][0]["핵심데이터"]
    add("tool_search_items", {}, data=memo_less)
    # 분배기 35(DV1a test_dispatch 의 사례)
    for name, args in [
        ("value_of_by_name", {"대분류": S1[0], "중분류": S1[1], "소분류": S1[2], "단위": S1[3], "year": "2022"}),
        ("value_of_by_name", {"대분류": S1[0], "year": "2022"}),
        ("value_of_by_name", {}),
        ("value_of_by_name", {"대분류": ["목록"], "중분류": "가", "소분류": "나", "단위": "다", "year": "2022"}),
        ("sum_check", {"a": 1200, "b": 300, "c": 1500, "tolerance": 1.1}),
        ("sum_check", {"a": 1200, "b": 300, "c": 1500}),
        ("sum_check", {"a": 1200, "b": 300, "c": 1500, "tolerance": "1.1"}),
        ("sum_check", {"a": "N/A", "b": 300, "c": 1500}),
        ("sum_check", {}),
        ("sum_n_check", {"addends": [1, 2], "target": 3}),
        ("sum_n_check", {"addends": [1, "N/A"], "target": 3}),
        ("sum_n_check", {"target": 3}),
        ("sum_n_check", {"addends": [1, 2], "target": 3, "tolerance": None}),
        ("ratio_check", {"d": 25, "e": 100, "f": 0.25}),
        ("ratio_check", {"d": 25, "e": 0, "f": 0.25}),
        ("ratio_check", {"d": 25, "e": 100}),
        ("sum_check_by_name", {"total": TOTAL, "a": S1, "b": S2, "years": ["2022", "2023"]}),
        ("sum_check_by_name", {"total": TOTAL, "a": S1, "b": S2, "years": ["2022", "2024"]}),
        ("sum_check_by_name", {"a": S1, "b": S2, "years": ["2022"]}),
        ("sum_check_by_name", {"total": TOTAL[:3], "a": S1, "b": S2, "years": ["2022"]}),
        ("sum_check_by_name", {"total": None, "a": S1, "b": S2, "years": ["2022"]}),
        ("sum_check_by_name", {"total": TOTAL, "a": S1, "b": S2, "years": [["2022"]]}),
        ("sum_n_check_by_names", {"addends": [RECYCLED, LANDFILL], "target": WASTE, "years": ["2022", "2024"]}),
        ("sum_n_check_by_names", {"addends": [S1, S2], "target": TOTAL, "years": ["2022"], "tolerance": 0}),
        ("sum_n_check_by_names", {"addends": 5, "target": TOTAL, "years": ["2022"]}),
        ("ratio_check_by_name", {"numerator": RENEW, "denominator": ENERGY, "expected": RATIO, "years": YEARS}),
        ("ratio_check_by_name", {"numerator": RENEW, "denominator": ENERGY, "years": ["2022"]}),
        ("search_items", {}),
        ("search_items", {"대분류": "폐기물"}),
        ("search_items", {"소분류_contains": "Scope", "limit": 1, "offset": 1}),
        ("search_items", {"limit": "5"}),
        ("search_items", {"소분류_contains": 3}),
        ("value_of", {"대분류": "가"}),
        ("", {}),
        ("sum_check", None),
    ]:
        add("_run_local_tool", [name, args])
    # 테마 거르기 7
    env = environment()
    for themes in [{"환경": env}, {"환경": env, "사회": SOCIAL}, {"사회": SOCIAL, "환경": []}, {"사회": SOCIAL},
                   {"지배구조": [], "사회": SOCIAL}, {}, {" 환경": env}]:  # fmt: skip
        add("split_themes", [themes])
    return made


# ── 무작위: hypothesis 전략(esg-app test_tools S2)과 같은 값 모둠 ──
_ODD = ["N/A", "", "1,234", " 12 ", "abc", "nan", "inf", "-0", "1e3", "0.5", None]
_EDGES = [0, 0.0, -0.0, 1, 1.0, 0.5, 0.999999, 1.000001, -1e-16, 1e-16]
_ALPHABET = "0123456789.,- eE+N/A가\t"


def _number(r: random.Random) -> Any:
    kind = r.randrange(4)
    if kind == 0:
        return r.randint(-(10**9), 10**9)
    if kind == 1:
        return r.uniform(-1e6, 1e6)
    if kind == 2:
        return r.choice([-1.0, 1.0]) * 10.0 ** r.uniform(-20, 300)
    return round(r.uniform(-1000, 1000), r.randrange(0, 4))


def _value(r: random.Random) -> Any:
    kind = r.randrange(4)
    if kind == 0:
        return _number(r)
    if kind == 1:
        return r.choice(_ODD)
    if kind == 2:
        return r.choice(_EDGES)
    return "".join(r.choice(_ALPHABET) for _ in range(r.randrange(7)))


def _tolerance(r: random.Random) -> Any:
    return r.choice([0, 1.1, 1e-6, 1, r.uniform(0, 1e9), r.random()])


def _random_calls(n: int, seed: int) -> list[dict[str, Any]]:
    r = random.Random(seed)
    made: list[dict[str, Any]] = []
    for i in range(n):
        kind = r.randrange(4)
        cid = f"random-{seed}-{i + 1:04d}"
        if kind == 0:
            args = [_value(r), _value(r), _value(r), _tolerance(r)]
            if r.random() < 0.3:  # 경계: 허용 오차 = |(a+b)-c|
                a, b, c = (_number(r) for _ in range(3))
                args = [a, b, c, abs((a + b) - c)]
            made.append({"id": cid, "fn": "tool_sum_check", "args": args})
        elif kind == 1:
            shape = r.randrange(3)
            addends: Any = [_value(r) for _ in range(r.randrange(6))] if shape == 0 else (None if shape == 1 else _value(r))
            made.append({"id": cid, "fn": "tool_sum_n_check", "args": [addends, _value(r), _tolerance(r)]})
        elif kind == 2:
            made.append({"id": cid, "fn": "tool_ratio_check", "args": [_value(r), _value(r), _value(r), _tolerance(r)]})
        else:  # _safe_float 를 value_of_by_name 으로(발주서 §7-3)
            value = _value(r) if r.random() < 0.8 else [r.randint(0, 9) for _ in range(r.randrange(3))]
            one = {"환경": [item(1, ["가상", "가상", "가상", "t"], {"2022": value})]}
            made.append({"id": cid, "fn": "tool_value_of_by_name", "args": ["가상", "가상", "가상", "t", "2022"], "data": one})
    return made


# ── 고리 사례(DV1b) ──

def _args(**kwargs: Any) -> str:
    return json.dumps(kwargs, ensure_ascii=False)


S1_2022 = _args(대분류=S1[0], 중분류=S1[1], 소분류=S1[2], 단위=S1[3], year=2022)
SUM_BY_NAME = _args(total=TOTAL, a=S1, b=S2, years=["2022"])


def _hand_loops() -> list[dict[str, Any]]:
    """손 사례 ⓐ~ⓚ(전부 지은 것)."""

    def loop(key: str, stage: str, max_steps: int, script: list[dict[str, Any]]) -> dict[str, Any]:
        return {"id": f"loop-{key}", "stage": stage, "maxSteps": max_steps, "script": script}

    return [
        # ⓐ 함수 호출 둘(걸음 둘) → 글
        loop("a-two-calls-then-text", "plan", 12, [
            {"calls": [["value_of_by_name", S1_2022]]},
            {"calls": [["sum_check_by_name", SUM_BY_NAME]]},
            {"text": "  가상 계산 Plan ['가상 산식']\n"},
        ]),
        # ⓑ 걸음이 다함(부분) — 셋째 답이 아직 부른다
        loop("b-steps-run-out", "review", 3, [
            {"calls": [["value_of_by_name", S1_2022]]},
            {"calls": [["search_items", _args(소분류_contains="Scope 2")]]},
            {"calls": [["value_of_by_name", S1_2022]], "text": " 가상 부분 리포트 "},
        ]),
        # ⓒ max_steps 1 에 함수 호출 — 첫 답이 곧 부분
        loop("c-one-step-call", "plan", 1, [{"calls": [["value_of_by_name", S1_2022]], "text": "가상 첫 답"}]),
        # ⓓ 깨진 인자 JSON → {}
        loop("d-broken-arguments", "review", 6, [{"calls": [["value_of_by_name", '{"대분류": ']]}, {"text": "가상 d"}]),
        # ⓔ 모르는 이름
        loop("e-unknown-name", "review", 6, [{"calls": [["가상도구", "{}"]]}, {"text": "가상 e"}]),
        # ⓕ 약점 1: 비수치 sum_check → runtime error 글
        loop("f-weakness-1", "review", 6, [{"calls": [["sum_check", _args(a="x", b=1, c=2)]]}, {"text": "가상 f"}]),
        # ⓖ search_items 인자 없음 → limit 200
        loop("g-search-no-args", "review", 6, [{"calls": [["search_items", "{}"]]}, {"text": "가상 g"}]),
        # ⓗ 이름형 검사, 해 둘
        loop("h-by-name-two-years", "review", 6, [
            {"calls": [["sum_check_by_name", _args(total=TOTAL, a=S1, b=S2, years=["2022", "2023"])]]},
            {"text": "가상 h"},
        ]),
        # ⓘ 한 답에 호출 셋(차례)
        loop("i-three-calls-in-one-answer", "review", 6, [
            {"calls": [
                ["ratio_check_by_name", _args(numerator=RENEW, denominator=ENERGY, expected=RATIO, years=YEARS)],
                ["value_of_by_name", S1_2022],
                ["sum_n_check", _args(addends=[1, 2], target=3)],
            ]},
            {"text": "가상 i"},
        ]),
        # ⓙ 검토 max_steps 0(HINTS 0개) — 코랩은 한 번 부른다, 엔진은 걸음 1
        loop("j-review-zero-steps", "review", 0, [{"calls": [["value_of_by_name", S1_2022]], "text": "가상 j"}]),
        # ⓚ 인사이트 — 글 + 출처(코랩은 출처를 리포트에 싣지 않는다 — 글만 견준다)
        loop("k-insight-text", "insight", 12, [
            {"text": "가상 인사이트 k", "citations": [{"url": "https://example.com/synthetic", "title": "가상"}], "hosted": 1},
        ]),
    ]


TOOL_NAMES = ["value_of_by_name", "sum_check", "sum_n_check", "ratio_check", "sum_check_by_name",
              "sum_n_check_by_names", "ratio_check_by_name", "search_items", "가상도구"]  # fmt: skip
_BROKEN = ["{", "", "[1]", "null", "3", '"가상"', '{"a": NaN, "b": 1, "c": 2}', '{"대분류": Infinity}']


def _random_arguments(r: random.Random, name: str) -> str:
    kind = r.randrange(10)
    if kind < 2:
        return r.choice(_BROKEN)
    if kind < 5:
        keys = ["a", "b", "c", "d", "e", "f", "addends", "target", "tolerance", "limit", "offset", "대분류", "year", "years"]
        return _args(**{r.choice(keys): _value(r) for _ in range(r.randrange(5))})
    quad = r.choice(QUADS)
    if name == "value_of_by_name":
        return _args(대분류=quad[0], 중분류=quad[1], 소분류=quad[2], 단위=quad[3], year=r.choice([*YEARS, 2022, None]))
    if name == "search_items":
        return _args(**r.choice([{}, {"대분류": quad[0]}, {"소분류_contains": "Scope"}, {"limit": r.randrange(5), "offset": 1}]))
    if name.endswith("_by_name"):
        return _args(total=r.choice(QUADS), a=quad, b=r.choice(QUADS), numerator=quad, denominator=r.choice(QUADS),
                     expected=r.choice(QUADS), years=r.sample(YEARS, r.randrange(1, 4)))  # fmt: skip
    if name == "sum_n_check_by_names":
        return _args(addends=r.sample(QUADS, 2), target=quad, years=["2022"])
    return _args(a=_value(r), b=_value(r), c=_value(r), d=_value(r), e=_value(r), f=_value(r), addends=[_value(r)],
                 target=_value(r), tolerance=_tolerance(r))  # fmt: skip


def _random_loops(n: int, seed: int) -> list[dict[str, Any]]:
    """고리 n 개(seed 마다 같은 사례). 걸음 상한 1~4, 답마다 호출 1~3, 끝은 글 답(끝남) 또는 상한에서 호출+글(부분)."""
    r = random.Random(f"loops-{seed}")
    made: list[dict[str, Any]] = []
    for i in range(n):
        max_steps = r.randint(1, 4)
        steps = r.randint(1, max_steps)

        def calls() -> list[list[str]]:
            out: list[list[str]] = []
            for _ in range(r.randint(1, 3)):
                name = r.choice(TOOL_NAMES)
                out.append([name, _random_arguments(r, name)])
            return out

        script: list[dict[str, Any]] = [{"calls": calls()} for _ in range(steps - 1)]
        if steps == max_steps and r.random() < 0.5:
            script.append({"calls": calls(), "text": f"가상 부분 {i + 1}"})
        else:
            script.append({"text": f" 가상 리포트 {i + 1}\n"})
        made.append({"id": f"loop-random-{seed}-{i + 1:03d}", "stage": r.choice(["plan", "review"]), "maxSteps": max_steps,
                     "script": script})  # fmt: skip
    return made


def loops(random_n: int = 0, seed: int | None = None) -> list[dict[str, Any]]:
    made = _hand_loops()
    if random_n and seed is not None:
        made += _random_loops(max(1, random_n // 50), seed)
    return made


def prompts() -> list[dict[str, Any]]:
    return [
        {"id": "plan", "kind": "plan", "vars": dict(COMPANY)},
        {"id": "review", "kind": "review", "vars": {"hints": list(HINTS)}},
        {"id": "insight", "kind": "insight", "vars": dict(COMPANY)},
    ]


def build(random_n: int = 0, seed: int | None = None) -> dict[str, Any]:
    calls = _hand_calls()
    if random_n:
        if seed is None:
            raise ValueError("--random 은 --seed 와 함께 준다")
        calls += _random_calls(random_n, seed)
    return {
        "format": FORMAT,
        "seed": seed,
        "random": random_n,
        "data": data(),
        "tolerance": TOLERANCE,
        "calls": calls,
        "prompts": prompts(),
        "loops": loops(random_n, seed),
    }
