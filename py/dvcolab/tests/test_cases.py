"""사례: JSON 왕복, seed 결정성, 짜 둔 답과 HINTS, 지은 값."""
import ast
import json
from pathlib import Path

from dvcolab import cases
from dvcolab.sandbox import load_answers

ANSWERS = Path(__file__).resolve().parents[1] / "answers.synthetic.json"


def test_cases_round_trip_through_json():
    built = cases.build(20, 7)
    again = json.loads(json.dumps(built, ensure_ascii=False))
    assert json.dumps(again, ensure_ascii=False) == json.dumps(built, ensure_ascii=False)
    assert again["format"] == "dvcolab.cases/1"
    assert (again["seed"], again["random"]) == (7, 20)


def test_the_same_seed_gives_the_same_cases_and_another_seed_differs():
    a = json.dumps(cases.build(50, 1), ensure_ascii=False)
    assert a == json.dumps(cases.build(50, 1), ensure_ascii=False)
    assert a != json.dumps(cases.build(50, 2), ensure_ascii=False)


def test_ids_are_unique_and_hand_cases_cover_dv1a():
    built = cases.build(30, 3)
    ids = [c["id"] for c in built["calls"]]
    assert len(ids) == len(set(ids))
    hand = [c for c in built["calls"] if c["id"].startswith("hand-")]
    by_fn = {fn: sum(1 for c in hand if c["fn"] == fn) for fn in {c["fn"] for c in hand}}
    assert by_fn["_run_local_tool"] == 35
    assert by_fn["split_themes"] == 7
    assert by_fn["tool_search_items"] == 10  # 9 + 메모 없는 항목
    assert by_fn["tool_value_of_by_name"] == 11 * 6 + 1


def test_random_safe_float_cases_go_through_value_of_by_name_with_their_own_data():
    built = cases.build(200, 5)
    via = [c for c in built["calls"] if c["id"].startswith("random-") and c["fn"] == "tool_value_of_by_name"]
    assert via and all("data" in c for c in via)


def test_the_plan_answer_is_the_hints_literal():
    plan = load_answers(ANSWERS)[0]
    assert ast.literal_eval(plan) == cases.HINTS
    assert len(load_answers(ANSWERS)) == 3


def test_the_inputs_are_made_up():
    env = cases.environment()
    assert len(env) == 12
    assert all(rec["항목"].startswith("가상 항목") for rec in env)
    assert cases.COMPANY["company_name"].startswith("가상")
    assert "{괄호}" in cases.COMPANY["material_topics"][-1]
