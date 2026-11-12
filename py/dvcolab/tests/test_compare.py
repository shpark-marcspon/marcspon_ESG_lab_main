"""견줌: 같음 0, 한 글자 바뀜 1, NaN·예외 outcome, 알려진 차이가 모르는 차이를 덮지 않음."""
import math

from dvcolab.compare import compare, hunks
from dvcolab.known import OUR_COMPANY_LINES, REVIEW_EXAMPLE, Hunk, classify


def result(calls=None, prompts=None, constants=None, expectations=None):
    return {
        "format": "dvcolab.result/1",
        "calls": calls or [],
        "prompts": prompts or [],
        "constants": constants or {},
        "expectations": expectations or [],
    }


def test_same_is_zero_and_nan_equals_nan():
    calls = [{"id": "a", "outcome": "ok", "value": {"diff": math.nan}}, {"id": "b", "outcome": "raise", "value": "NameError: 가상"}]
    assert compare(result(calls), result([dict(c) for c in calls])).unknown == 0


def test_a_changed_value_or_outcome_or_missing_call_is_unknown():
    colab = result([{"id": "a", "outcome": "ok", "value": {"v": 1.0}}, {"id": "b", "outcome": "ok", "value": 1}])
    assert compare(colab, result([{"id": "a", "outcome": "ok", "value": {"v": 1.5}}, {"id": "b", "outcome": "ok", "value": 1}])).unknown == 1
    assert compare(colab, result([{"id": "a", "outcome": "raise", "value": {"v": 1.0}}, {"id": "b", "outcome": "ok", "value": 1}])).unknown == 1
    assert compare(colab, result([{"id": "a", "outcome": "ok", "value": {"v": 1.0}}])).unknown == 1


def test_key_order_matters():
    colab = result([{"id": "a", "outcome": "ok", "value": {"x": 1, "y": 2}}])
    assert compare(colab, result([{"id": "a", "outcome": "ok", "value": {"y": 2, "x": 1}}])).unknown == 1


def test_known_company_lines_do_not_hide_a_change_elsewhere():
    colab_user = "    # 가상\n    [기업명]: 자리표시\n    [사이]\n    [DATA]"
    ours = f"    # 가상\n{OUR_COMPANY_LINES[0]}\n    [사이]\n    [DATA]"
    same = compare(result(prompts=[{"id": "plan", "system": "S", "user": colab_user}]),
                   result(prompts=[{"id": "plan", "system": "S", "user": ours}]))  # fmt: skip
    assert (len(same.known), same.unknown) == (1, 0)
    changed = compare(result(prompts=[{"id": "plan", "system": "S", "user": colab_user}]),
                      result(prompts=[{"id": "plan", "system": "S", "user": ours.replace("[DATA]", "[DATA!]")}]))  # fmt: skip
    assert (len(changed.known), changed.unknown) == (1, 1)
    # 붙은 줄이 함께 바뀌면 hunk 하나가 되어 규칙에 안 맞는다 — 모르는 차이로 남는다(덮지 않음)
    glued = compare(result(prompts=[{"id": "plan", "system": "S", "user": colab_user}]),
                    result(prompts=[{"id": "plan", "system": "S", "user": ours.replace("[사이]", "[사이!]")}]))  # fmt: skip
    assert (len(glued.known), glued.unknown) == (0, 1)


def test_a_wrong_company_value_is_not_known():
    h = Hunk("plan", "user", "replace", 2, 2, ("    [기업명]: 자리표시",), ("    [기업명]: 가상건설사",))
    assert classify(h) is None


def test_review_example_numbers_are_known_only_when_just_digits_differ():
    ours = f'    {{"item": "가상", {REVIEW_EXAMPLE}}}'
    theirs_digits = ours.replace("1200", "9876")
    assert classify(Hunk("review", "system", "replace", 1, 1, (theirs_digits,), (ours,))) is not None
    assert classify(Hunk("review", "system", "replace", 1, 1, (theirs_digits + "x",), (ours,))) is None
    assert classify(Hunk("plan", "system", "replace", 1, 1, (theirs_digits,), (ours,))) is None


def test_a_prompt_missing_on_either_side_is_unknown():
    prompt = {"id": "plan", "system": "S", "user": "U"}
    assert compare(result(prompts=[prompt]), result(prompts=[dict(prompt)])).unknown == 0
    assert compare(result(prompts=[prompt]), result()).unknown == 1
    assert compare(result(), result(prompts=[prompt])).unknown == 1


def test_constants_and_expectations():
    assert compare(result(constants={"K": [1]}), result(constants={"K": [1]})).unknown == 0
    assert compare(result(constants={"K": [1]}), result(constants={"K": [2]})).unknown == 1
    assert compare(result(expectations=[{"id": "x", "held": False}]), result()).unknown == 1


def test_hunks_point_at_lines():
    (h,) = hunks("plan", "user", "a\nb\nc", "a\nB\nc")
    assert (h.tag, h.colab_at, h.engine_at, h.colab, h.engine) == ("replace", 2, 2, ("b",), ("B",))
