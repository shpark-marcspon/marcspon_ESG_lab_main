"""고리 사례(DV1b): 가짜 post 의 고리 모드, 지은 md 로 고리 왕복·견줌(모르는 차이 1 → 종료 1), 사례 꼴. 받은 코랩은 안 돌린다."""
import json

from dvcolab import cases
from dvcolab.sandbox import RESPONSES_URL, Sandbox
from dvcolab.tests.test_cli import as_engine, go


def test_the_fake_post_answers_a_loop_and_records_the_executed_calls_and_outputs():
    box = Sandbox([])
    box.start_loop([{"calls": [["가상도구", '{"a": 1}'], ["둘째", "{}"]], "text": "가상 앞글"}, {"text": "가상 끝"}])
    first = box.post(RESPONSES_URL, json={"input": [{"role": "system", "content": "s"}]}).json()
    assert [o["type"] for o in first["output"]] == ["message", "function_call", "function_call"]
    ids = [o["call_id"] for o in first["output"][1:]]
    sent = [{"type": "function_call_output", "call_id": cid, "output": f"가상 결과 {n}"} for n, cid in enumerate(ids)]
    second = box.post(RESPONSES_URL, json={"input": sent}).json()
    assert second["output"][0]["content"][0]["text"] == "가상 끝"
    record = box.end_loop()
    assert record == {
        "executed": [["가상도구", '{"a": 1}'], ["둘째", "{}"]],
        "outputs": ["가상 결과 0", "가상 결과 1"],
        "providerCalls": 2,
        "lastHadCalls": False,
        "unusedAnswers": 0,
    }
    assert box.posts == []  # 고리 부름은 셀 10·14·18 의 짜 둔 답 몫에 세지 않는다


def test_loops_round_trip_through_the_fake_md(tmp_path):
    code, out = go(tmp_path, "a")
    assert code == 0
    colab = json.loads((out / "colab.json").read_text(encoding="utf-8"))
    by_id = {loop["id"]: loop for loop in colab["loops"]}
    assert len(by_id) == len(cases.loops()) == 11
    a = by_id["loop-a-two-calls-then-text"]
    assert (a["providerCalls"], a["ending"], len(a["executed"])) == (3, "done", 2)
    assert a["outputs"][0] == '{"name": "value_of_by_name", "nan": NaN}'  # 지은 분배기의 글(NaN 도 글자 그대로)
    assert (by_id["loop-b-steps-run-out"]["providerCalls"], by_id["loop-b-steps-run-out"]["ending"]) == (3, "partial")
    assert by_id["loop-b-steps-run-out"]["text"] == "가상 부분 리포트"
    assert (by_id["loop-j-review-zero-steps"]["providerCalls"], by_id["loop-j-review-zero-steps"]["executed"]) == (1, [])
    run_json = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert run_json["loops"] == 11


def test_a_loop_difference_is_unknown_and_exits_one(tmp_path):
    _, out = go(tmp_path, "a")
    assert go(tmp_path, "b", as_engine(tmp_path, out / "colab.json"))[0] == 0

    def one_output(doc):
        doc["loops"][0]["outputs"][0] += " "

    code, d = go(tmp_path, "c", as_engine(tmp_path, out / "colab.json", one_output))
    assert code == 1
    assert "고리 loop-a-two-calls-then-text: 칸 outputs" in (d / "diff.md").read_text(encoding="utf-8")

    def missing(doc):
        doc["loops"].pop()

    assert go(tmp_path, "d", as_engine(tmp_path, out / "colab.json", missing))[0] == 1

    def ending(doc):
        doc["loops"][1]["ending"] = "done"

    assert go(tmp_path, "e", as_engine(tmp_path, out / "colab.json", ending))[0] == 1


def test_loop_cases_are_made_so_known_differences_stay_zero():
    made = cases.loops(500, 1)
    assert len(made) == 11 + 10
    assert len({loop["id"] for loop in made}) == len(made)
    assert made == cases.loops(500, 1)
    assert made != cases.loops(500, 2)
    for loop in made:
        script = loop["script"]
        names = [name for entry in script for name, _ in entry.get("calls", [])]
        assert "web_search" not in names
        last = script[-1]
        assert "text" in last and last["text"].strip()  # 부분이어도 글 하나(코랩의 응답 JSON 통째 갈래가 안 생기게)
        if "calls" in last:  # 부분: 상한에서 끝난다
            assert len(script) == max(1, loop["maxSteps"])
        assert all("calls" in entry for entry in script[:-1])
    assert "loops" in cases.build()
