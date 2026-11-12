"""셀 뽑기와 고르기 — 지은 작은 md."""
import pytest

from dvcolab.cells import NOT_RUN, RUN, CellFormatError, default_colab_md, pick, read_cells
from dvcolab.tests.fake_md import FAKE_MD


def test_pick_runs_the_six_cells_in_order(tmp_path):
    md = tmp_path / "가상.md"
    md.write_text(FAKE_MD, encoding="utf-8")
    cells = read_cells(md)
    assert [c.number for c in cells] == [2, 3, 4, 7, 10, 14, 18]
    assert [c.number for c in pick(cells)] == sorted(RUN) == [3, 4, 7, 10, 14, 18]
    assert all(not c.skipped for c in cells)  # 건너뛰기 규칙 0


def test_a_missing_run_cell_fails(tmp_path):
    md = tmp_path / "가상.md"
    md.write_text(FAKE_MD.replace("### 셀 14 · code", "### 셀 14 · markdown"), encoding="utf-8")
    with pytest.raises(CellFormatError, match="돌릴 셀이 없다"):
        pick(read_cells(md))


def test_an_unknown_code_cell_fails(tmp_path):
    md = tmp_path / "가상.md"
    md.write_text(FAKE_MD + "\n### 셀 40 · code\n\n```python\nx = 1\n```\n", encoding="utf-8")
    with pytest.raises(CellFormatError, match="모르는 code 셀"):
        pick(read_cells(md))


def test_run_and_not_run_do_not_overlap_and_cover_fourteen():
    assert not set(RUN) & set(NOT_RUN)
    assert len(RUN) + len(NOT_RUN) == 14  # 읽기판의 code 셀 14


def test_the_default_path_names_the_data_verify_reading_copy():
    try:
        path = default_colab_md()
    except FileNotFoundError:
        pytest.skip("received-derived/ 없는 자리")
    assert path.parent.name == "데이터 검증 코랩"
    assert path.suffix == ".md"
