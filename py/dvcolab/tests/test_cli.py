"""CLI 종료 코드 0·1·2 — 지은 작은 md(fake_md)로. 받은 코랩은 돌리지 않는다."""
import json

from dvcolab.cli import DEFAULT_ANSWERS, run
from dvcolab.known import OUR_COMPANY_LINES
from dvcolab.tests.fake_md import FAKE_MD


def go(tmp_path, name, engine=None, forbidden=None, md=FAKE_MD, random_n=0, seed=None):
    path = tmp_path / "가상.md"
    path.write_text(md, encoding="utf-8")
    out = tmp_path / name
    return run(path, engine, out, DEFAULT_ANSWERS, random_n, seed, None, forbidden), out


def as_engine(tmp_path, colab_json, change=None):
    doc = json.loads(colab_json.read_text(encoding="utf-8"))
    doc["side"] = "engine"
    if change:
        change(doc)
    path = tmp_path / "engine.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def test_colab_side_only_is_zero_and_writes_the_files(tmp_path):
    code, out = go(tmp_path, "a")
    assert code == 0
    for name in ("cases.json", "colab.json", "diff.md", "run.json", "colab-stdout.log"):
        assert (out / name).is_file()
    colab = json.loads((out / "colab.json").read_text(encoding="utf-8"))
    assert [p["id"] for p in colab["prompts"]] == ["plan", "review", "insight"]
    assert all(x["held"] for x in colab["expectations"])  # 약점 5 꼴: 비우지 않으면 옛 값
    run_json = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert "사례마다 _value_cache = {}" in run_json["harness_ops"]


def test_same_is_zero_one_char_is_one_and_known_is_zero(tmp_path):
    _, out = go(tmp_path, "a")
    assert go(tmp_path, "b", as_engine(tmp_path, out / "colab.json"))[0] == 0

    def one_char(doc):
        doc["calls"][0]["value"] = {"kind": "가상합!", "a": doc["calls"][0]["value"].get("a")}

    assert go(tmp_path, "c", as_engine(tmp_path, out / "colab.json", one_char))[0] == 1

    def company(doc):
        doc["prompts"][0]["user"] = doc["prompts"][0]["user"].replace("    [기업명]: 자리표시", OUR_COMPANY_LINES[0])

    code, d = go(tmp_path, "d", as_engine(tmp_path, out / "colab.json", company))
    assert code == 0
    assert "| 1 |" in (d / "diff.md").read_text(encoding="utf-8")

    def company_and_more(doc):
        company(doc)
        doc["prompts"][2]["system"] += "!"

    assert go(tmp_path, "e", as_engine(tmp_path, out / "colab.json", company_and_more))[0] == 1


def test_same_seed_gives_the_same_colab_json_bytes(tmp_path):
    _, a = go(tmp_path, "a", random_n=30, seed=4)
    _, b = go(tmp_path, "b", random_n=30, seed=4)
    assert (a / "colab.json").read_bytes() == (b / "colab.json").read_bytes()


def test_tool_failures_are_two(tmp_path):
    words = tmp_path / "words.txt"
    words.write_text("자리표시\n", encoding="utf-8")
    assert go(tmp_path, "a", forbidden=words)[0] == 2  # 출력에 금지 낱말
    assert go(tmp_path, "b", md=FAKE_MD.replace("### 셀 4 · code", "### 셀 4 · markdown"))[0] == 2  # 셀 없음
    assert go(tmp_path, "c", md=FAKE_MD.replace("print(len(data_split))", "1 / 0"))[0] == 2  # 셀 실패
    bad = tmp_path / "bad.json"
    bad.write_text('{"format": "가상"}', encoding="utf-8")
    assert go(tmp_path, "d", bad)[0] == 2  # 엔진 결과 꼴
    swallowed = FAKE_MD.replace('insight = ask("가상 인사이트", "가상", TOOLS_RESPONSES_websearch)',
                                'insight = ask("가상 인사이트", "가상", TOOLS_RESPONSES_websearch)\nmore = ask("x", "y", [])')  # fmt: skip
    assert go(tmp_path, "e", md=swallowed)[0] == 2  # 짜 둔 답 모자람(BaseException 이라 코랩의 except Exception 을 지나감)
