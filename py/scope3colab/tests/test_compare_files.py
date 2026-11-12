"""S3L3 `compare`: 코랩을 돌리지 않고 주어진 코랩 결과 파일(과 채운 템플릿)을 코랩 쪽으로 견준다.

같음(0)·칸 차이(1, `diff.json` 에 두 쪽 매핑 칸)·꼴 어긋남(줄 빠짐·결과 파일 열·회색 칸, 1)·도구 실패(2), 그리고 `run.json`
에 입력 파일 이름이 없다는 단언. 파일은 전부 지은 것(이름 `가상`·수 첫 자리 9)이다 — 받은 자료를 읽는 시험은 두지 않는다.
"""

import json
from pathlib import Path

from openpyxl import Workbook

from scope3colab.cli import main
from scope3colab.rows import rows_from_result_xlsx

WASTE_HEADER = ["국가", "사업장", "폐기물 종류", "처리량", "배출계수(kgCO2eq/kg)", "처리방법_정규화", "화석기원 CO2e(tCO2e)",
                "생물기원 CO2e(tCO2e)", "바이오매스 구분", "배출량(tCO2e)", "단위환산비고"]  # fmt: skip
COMMUTE_HEADER = ["국가", "사업장", "이동수단", "평균통근거리(km)", "직원수", "근무일수", "거리왕복계수", "정규화이동수단",
                  "배출계수", "배출량(tCO2e)", "계산식"]  # fmt: skip


def write_result(path: Path, extra_column: bool = False) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "C5_사업장폐기물"
    ws.append(WASTE_HEADER)
    ws.append(["가상국", "가상사업장", "가상폐지", 900, 0.09, "소각", 0.009, 0.0009, "가상 구분", 0.009, None])
    ws.append(["가상국", "가상사업장", "가상폐목", 9.5, 0.095, "매립", 0.0009, 0, "해당없음", 0.0009, "가상 환산"])
    c7 = wb.create_sheet("C7_통근")
    c7.append(COMMUTE_HEADER + (["가상열"] if extra_column else []))
    c7.append(["가상국", "가상사업장", "가상버스", 9.1, 9, 99, 2, "bus", 0.09, 0.9, "가상 식"])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def write_template(path: Path, ef: float | None = None, em: float | None = None) -> Path:
    """회색 칸(배출계수·배출량 머리 아래) 하나짜리 템플릿. 값을 주면 채운 판."""
    wb = Workbook()
    ws = wb.active
    ws.title = "C7_임직원통근"
    ws.append(["국가", "교통수단", "비고", "배출계수(tCO2e/km)", "배출량(tCO2e)"])
    ws.append(["가상국", "가상버스", "가상 비고", ef, em])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def engine_json(result: Path, path: Path, edit=None) -> Path:
    data = rows_from_result_xlsx(result).to_json()
    data["side"] = "engine"
    if edit is not None:
        edit(data)
    path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    return path


def run_compare(tmp_path: Path, *extra: str, engine: Path, result: Path) -> tuple[int, Path]:
    out = tmp_path / "out"
    code = main(["compare", "--colab-result-file", str(result), "--engine-result", str(engine), "--out", str(out), *extra])
    return code, out


def test_same_is_0_and_run_json_has_no_file_names(tmp_path):
    result = write_result(tmp_path / "가상회사_결과.xlsx")
    engine = engine_json(result, tmp_path / "가상엔진.json")
    code, out = run_compare(tmp_path, engine=engine, result=result)
    assert code == 0
    assert json.loads((out / "diff.json").read_text("utf-8")) == {"schema": "scope3colab.diff/1", "diffs": []}
    run = (out / "run.json").read_text("utf-8")
    meta = json.loads(run)
    assert meta["exit"] == 0 and meta["colab_rows"] == meta["engine_rows"] == 3
    assert set(meta["inputs"]["코랩 결과 파일"]) == {"sha256", "bytes"}
    for name in ("가상회사", "가상엔진", str(tmp_path), ".xlsx", ".json"):
        assert name not in run
        assert name not in (out / "diff.md").read_text("utf-8")


def test_changed_cell_is_1_and_diff_json_carries_both_mappings(tmp_path):
    result = write_result(tmp_path / "가상_결과.xlsx")

    def edit(data):
        row = next(r for r in data["rows"] if r["sheet"] == "C7")
        row["fields"]["정규화이동수단"] = "subway"
        row["fields"]["배출량(tCO2e)"] = "0.99"

    code, out = run_compare(tmp_path, engine=engine_json(result, tmp_path / "e.json", edit), result=result)
    assert code == 1
    diffs = json.loads((out / "diff.json").read_text("utf-8"))["diffs"]
    assert [(d["sheet"], d["row"], d["field"], d["colab"], d["engine"]) for d in diffs] == [
        ("C7", 1, "정규화이동수단", "bus", "subway"),
        ("C7", 1, "배출량(tCO2e)", "0.9", "0.99"),
    ]
    assert diffs[0]["mapping"] == {
        "colab": {"정규화이동수단": "bus", "평균통근거리(km)": "9.1", "거리왕복계수": "2"},
        "engine": {"정규화이동수단": "subway", "평균통근거리(km)": "9.1", "거리왕복계수": "2"},
    }
    assert json.loads((out / "run.json").read_text("utf-8"))["diffs"] == 2


def test_shape_mismatch_rows_columns_and_gray_cells_are_1(tmp_path):
    result = write_result(tmp_path / "가상_결과.xlsx")

    def drop_last_waste_row(data):
        data["rows"] = [r for r in data["rows"] if not (r["sheet"] == "C5" and r["row"] == 2)]

    engine = engine_json(result, tmp_path / "e.json", drop_last_waste_row)
    engine_file = write_result(tmp_path / "e_result.xlsx", extra_column=True)
    template = write_template(tmp_path / "t.xlsx")
    colab_filled = write_template(tmp_path / "cf.xlsx", 0.00009, 0.9)
    engine_filled = write_template(tmp_path / "ef.xlsx", 0.00009, 0.99)
    code, out = run_compare(
        tmp_path, "--engine-result-file", str(engine_file), "--colab-filled-file", str(colab_filled),
        "--template", str(template), "--engine-filled-file", str(engine_filled), engine=engine, result=result,
    )  # fmt: skip
    assert code == 1
    diffs = json.loads((out / "diff.json").read_text("utf-8"))["diffs"]
    assert [(d["sheet"], d["row"], d["field"], d["colab"], d["engine"]) for d in diffs] == [("C5", 2, "(줄)", "있음", None)]
    assert diffs[0]["mapping"] == {"colab": {"처리방법_정규화": "매립", "바이오매스 구분": "해당없음"}, "engine": None}
    output = json.loads((out / "outputs.json").read_text("utf-8"))["diffs"]
    assert [(d["file"], d["where"], d["what"]) for d in output] == [
        ("result", "C7_통근", "(열)"),
        ("filled", "C7_임직원통근!E2", "회색 칸"),
    ]
    assert json.loads((out / "run.json").read_text("utf-8"))["output_diffs"] == 2


def test_tool_failures_are_2(tmp_path):
    result = write_result(tmp_path / "가상_결과.xlsx")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "가상/0", "rows": []}), "utf-8")
    assert run_compare(tmp_path, engine=bad, result=result)[0] == 2
    good = engine_json(result, tmp_path / "e.json")
    assert run_compare(tmp_path, "--engine-filled-file", str(result), engine=good, result=result)[0] == 2
    assert run_compare(tmp_path, "--template", str(result), engine=good, result=result)[0] == 2
