"""§3-5 견줌: 같은 표 0 차이, 칸 하나 바꾸면 차이 1줄·종료 1, 줄 빠짐도 차이. CLI 종료 코드 0·1·2.

CLI 시험은 지어낸 작은 md 를 쓴다: 셀 하나가 코랩처럼 `files.upload()` 로 템플릿을 받고, 코랩 결과 꼴(C1·C2 시트)의
엑셀을 openpyxl 로 써서 `result = {"result_file": …}` 를 남긴다. 받은 코랩은 돌리지 않는다(pandas 없이 돈다).
"""
import json
from pathlib import Path

import pytest

from scope3colab.cli import main
from scope3colab.compare import compare, render_diff_md
from scope3colab.rows import FIELDS, Row, Rows, RowsFormatError, rows_from_json

FAKE_COLAB_MD = '''\
### 셀 1 · code

```python
!pip install -q 가상패키지
from google.colab import files
uploaded = files.upload()
input_file = list(uploaded.keys())[0]
```

### 셀 2 · code

```python
import os
from openpyxl import Workbook
os.makedirs("result", exist_ok=True)
wb = Workbook()
ws = wb.active
ws.title = "C1_구매품서비스"
ws.append(["품목명", "EF적용금액(KRW)", "EF_DB", "배출계수", "배출량(tCO2e)", "EF_DB_추천사유", "추천EF1", "추천EF1_배출계수"])
ws.append(["가상품목 가", 9100000, "Synthetic Title A", 0.00091, 0.1 + 0.2, "가상 사유(db_keyword).", "Synthetic Title B", 0.00092])
ws2 = wb.create_sheet("C2_자본재")
ws2.append(["자산명", "EF적용금액(KRW)", "EF_DB", "배출계수", "배출량(tCO2e)"])
ws2.append(["가상자산 나", 92000000, "Synthetic Title C", 0.00093, 85.56])
wb.create_sheet("C10_가상가공")  # 견주지 않는 시트(C5 는 S3C-o2, C3 은 S3C-o4a 부터 견줌 — 코랩에 없는 C10)
wb.save("result/가상_result.xlsx")
result = {"result_file": "result/가상_result.xlsx"}
```
'''


def row(sheet="C1", n=1, item="가상품목 가", **fields):
    base = {name: None for name, _, _ in FIELDS}
    base.update(fields)
    return Row(sheet, n, item, base)


def rows(*rs, side="colab"):
    return Rows(side, tuple(rs), ())


def test_same_rows_zero_diffs_and_decimal_equality():
    a = rows(row(**{"대표EF": "Synthetic A", "배출량(tCO2e)": "8.281"}))
    b = rows(row(**{"대표EF": "Synthetic A", "배출량(tCO2e)": "8.2810"}), side="engine")
    assert compare(a, b) == []


def test_one_changed_cell_is_one_diff_with_zero_tolerance():
    a = rows(row(**{"배출량(tCO2e)": "8.281000000000001"}))
    b = rows(row(**{"배출량(tCO2e)": "8.281"}), side="engine")
    diffs = compare(a, b)
    assert [(d.field, d.colab, d.engine) for d in diffs] == [("배출량(tCO2e)", "8.281000000000001", "8.281")]


def test_missing_rows_on_either_side_are_diffs():
    a = rows(row(n=1), row(n=2, item="가상품목 나"))
    b = rows(row(n=1), row("C2", 1, "가상자산 다"), side="engine")
    diffs = compare(a, b)
    assert [(d.key, d.field, d.colab, d.engine) for d in diffs] == [
        (("C1", 2, "가상품목 나"), "(줄)", "있음", None),
        (("C2", 1, "가상자산 다"), "(줄)", None, "있음"),
    ]


def test_null_vs_value_is_a_diff():
    diffs = compare(rows(row(**{"추천EF2": None})), rows(row(**{"추천EF2": "Synthetic B"}), side="engine"))
    assert len(diffs) == 1


def test_engine_json_numbers_and_strings_normalize():
    data = {
        "schema": "scope3colab.rows/1",
        "side": "engine",
        "rows": [{"sheet": "C1", "row": 1, "item": "가상품목 가", "fields": {"배출량(tCO2e)": 8.281, "대표EF_배출계수": "0.00091"}}],
    }
    got = rows_from_json(data, "engine")
    assert got.rows[0].fields["배출량(tCO2e)"] == "8.281"
    assert got.rows[0].fields["대표EF_배출계수"] == "0.00091"
    assert got.rows[0].fields["대표EF"] is None


def test_diff_md_lists_each_diff():
    a = rows(row(**{"대표EF": "Synthetic A"}))
    b = rows(row(**{"대표EF": "Synthetic B"}), side="engine")
    md = render_diff_md(a, b, compare(a, b), {"가상": "값"})
    assert "차이 1줄." in md
    assert "| C1 | 1 | 가상품목 가 | 대표EF | Synthetic A | Synthetic B |" in md


# ── CLI 종료 코드 ──
@pytest.fixture
def fake_md(tmp_path) -> Path:
    path = tmp_path / "가상_읽기판.md"
    path.write_text(FAKE_COLAB_MD, encoding="utf-8")
    return path


@pytest.fixture
def no_answers(tmp_path) -> Path:
    path = tmp_path / "answers.synthetic.json"
    path.write_text(json.dumps({"synthetic": True, "answers": []}), encoding="utf-8")
    return path


def run_cli(md: Path, answers: Path, out: Path, engine: Path | None = None) -> int:
    argv = ["run", "--colab-md", str(md), "--answers", str(answers), "--out", str(out)]
    if engine is not None:
        argv += ["--engine-result", str(engine)]
    return main(argv)


def test_cli_without_engine_writes_colab_json(fake_md, no_answers, tmp_path):
    out = tmp_path / "out"
    assert run_cli(fake_md, no_answers, out) == 0
    colab = json.loads((out / "colab.json").read_text("utf-8"))
    assert [(r["sheet"], r["row"], r["item"]) for r in colab["rows"]] == [("C1", 1, "가상품목 가"), ("C2", 1, "가상자산 나")]
    assert colab["rows"][0]["fields"]["배출량(tCO2e)"] == "0.3"  # 코랩 결과 파일의 값: openpyxl 이 `%.16g` 로 쓴다(0.1 + 0.2 → 0.3)
    assert colab["not_compared"] == ["C10_가상가공"]
    meta = json.loads((out / "run.json").read_text("utf-8"))
    assert [(s["cell"], s["rule"]) for s in meta["skipped_lines"]] == [(1, "shell-magic")]
    assert "엔진 결과 없음" in (out / "diff.md").read_text("utf-8")


def test_cli_same_is_0_changed_cell_is_1(fake_md, no_answers, tmp_path):
    first = tmp_path / "first"
    assert run_cli(fake_md, no_answers, first) == 0
    engine = json.loads((first / "colab.json").read_text("utf-8"))
    engine["side"] = "engine"
    same = tmp_path / "engine-same.json"
    same.write_text(json.dumps(engine, ensure_ascii=False), "utf-8")
    assert run_cli(fake_md, no_answers, tmp_path / "same", same) == 0

    engine["rows"][0]["fields"]["배출량(tCO2e)"] = "0.30000000000000004"  # 메모리의 float 그대로면 파일 값과 다르다
    changed = tmp_path / "engine-changed.json"
    changed.write_text(json.dumps(engine, ensure_ascii=False), "utf-8")
    assert run_cli(fake_md, no_answers, tmp_path / "changed", changed) == 1
    md = (tmp_path / "changed" / "diff.md").read_text("utf-8")
    assert "차이 1줄." in md

    del engine["rows"][1]
    engine["rows"][0]["fields"]["배출량(tCO2e)"] = "0.3"
    missing = tmp_path / "engine-missing.json"
    missing.write_text(json.dumps(engine, ensure_ascii=False), "utf-8")
    assert run_cli(fake_md, no_answers, tmp_path / "missing", missing) == 1


def test_duplicate_row_key_is_format_error():
    with pytest.raises(RowsFormatError, match="둘 이상"):
        rows(row(), row(), side="engine")


def test_cli_engine_with_duplicate_row_is_2(fake_md, no_answers, tmp_path):
    first = tmp_path / "first"
    assert run_cli(fake_md, no_answers, first) == 0
    engine = json.loads((first / "colab.json").read_text("utf-8"))
    engine["rows"].append(engine["rows"][0])  # 같은 줄을 한 번 더 — 덮어서 차이 0 이 되면 안 된다
    dup = tmp_path / "engine-dup.json"
    dup.write_text(json.dumps(engine, ensure_ascii=False), "utf-8")
    assert run_cli(fake_md, no_answers, tmp_path / "dup", dup) == 2


def test_cli_cell_failure_outside_skip_list_is_2(tmp_path, no_answers):
    md = tmp_path / "가상_실패.md"
    md.write_text("### 셀 1 · code\n\n```python\nraise ValueError('가상 실패')\n```\n", encoding="utf-8")
    assert run_cli(md, no_answers, tmp_path / "out") == 2


def test_cli_unused_answers_is_2(fake_md, tmp_path):
    answers = tmp_path / "answers.synthetic.json"
    answers.write_text(json.dumps({"synthetic": True, "answers": [{"text": "남는 답"}]}), encoding="utf-8")
    assert run_cli(fake_md, answers, tmp_path / "out") == 2


def test_cli_bad_engine_json_is_2(fake_md, no_answers, tmp_path):
    bad = tmp_path / "engine.json"
    bad.write_text(json.dumps({"schema": "다른 꼴"}), encoding="utf-8")
    assert run_cli(fake_md, no_answers, tmp_path / "out", bad) == 2


def test_cli_answers_not_marked_synthetic_is_2(fake_md, tmp_path):
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"answers": []}), encoding="utf-8")
    assert run_cli(fake_md, answers, tmp_path / "out") == 2


def test_cli_relative_out_resolves_before_colab_changes_dir(fake_md, no_answers, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert run_cli(fake_md, no_answers, Path("상대/out")) == 0
    assert (tmp_path / "상대" / "out" / "colab.json").is_file()
