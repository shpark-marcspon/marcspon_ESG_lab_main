"""회귀 테스트: 정답 표 15개에 대한 일치율이 실측 하한을 지킨다.

하한은 `data/gold/floors.json` (이 밴드가 2026-10-01 에 실측한 값). 원본 PDF 는 저장소 밖에 있다.
- auto 전략: 양식별 하한 + 표별 하한
- text 전략(괘선을 쓰지 않음): 표별 하한. 삼성전자 표 5개는 세로선이 없는 표의 기준 사례다.
"""
import json

import pytest

from docparse.config import DEFAULT_GOLD_DIR
from docparse.score import aggregate, load_gold, score_gold

from .conftest import require_reports

pytestmark = pytest.mark.usefixtures("engine")

FLOORS = json.loads((DEFAULT_GOLD_DIR / "floors.json").read_text(encoding="utf-8"))
GOLD_IDS = [g["id"] for g in load_gold(DEFAULT_GOLD_DIR)]
SAMSUNG_IDS = [g["id"] for g in load_gold(DEFAULT_GOLD_DIR) if g["report"] == "samsung"]
OTHER_IDS = [i for i in GOLD_IDS if i not in SAMSUNG_IDS]
METRICS = FLOORS["metrics"]


_SCORES = {}


@pytest.fixture
def scores(engine):
    """엔진마다 한 번만 잰다."""
    require_reports()
    if engine not in _SCORES:
        _SCORES[engine] = {s: score_gold(DEFAULT_GOLD_DIR, s) for s in ("auto", "text")}
    return _SCORES[engine]


def _check(result, floor, label):
    below = {m: (result[m], floor[m]) for m in METRICS if result[m] < floor[m] - 1e-9}
    assert not below, f"{label}: 일치율이 하한 아래다 (실측, 하한) {below}"


def test_gold_set_is_complete():
    assert len(GOLD_IDS) == 15 and len(SAMSUNG_IDS) == 5
    assert set(FLOORS["auto"]["by_table"]) == set(GOLD_IDS) == set(FLOORS["text"]["by_table"])


@pytest.mark.parametrize("form", sorted(FLOORS["auto"]["by_form"]))
def test_auto_strategy_keeps_floor_per_form(scores, form):
    _check(aggregate(scores["auto"], "form")[form], FLOORS["auto"]["by_form"][form], f"auto/{form}")


@pytest.mark.parametrize("table_id", GOLD_IDS)
def test_auto_strategy_keeps_floor_per_table(scores, table_id):
    r = next(x for x in scores["auto"] if x["id"] == table_id)
    assert r["n_tables"] >= 1, f"{table_id}: 정답 영역에서 표를 찾지 못했다"
    _check(r, FLOORS["auto"]["by_table"][table_id], f"auto/{table_id}")


@pytest.mark.parametrize("table_id", SAMSUNG_IDS)
def test_text_strategy_keeps_floor_on_samsung_tables(scores, table_id):
    """글자 정렬 기반 전략만으로 삼성전자 표를 읽는다. 이 전략을 무력화하면 빨개진다."""
    r = next(x for x in scores["text"] if x["id"] == table_id)
    assert r["n_tables"] >= 1 and r["methods"] == ["text"], f"{table_id}: 글자 정렬 기반 표가 없다"
    _check(r, FLOORS["text"]["by_table"][table_id], f"text/{table_id}")


@pytest.mark.parametrize("table_id", OTHER_IDS)
def test_text_strategy_keeps_floor_on_other_tables(scores, table_id):
    r = next(x for x in scores["text"] if x["id"] == table_id)
    _check(r, FLOORS["text"]["by_table"][table_id], f"text/{table_id}")


def test_extracted_numbers_exist_verbatim_in_text_layer(scores):
    """추출된 숫자 셀은 그 쪽 텍스트 계층에 같은 문자열로 있어야 한다 (값을 고치거나 만들지 않는다)."""
    import re

    from docparse.compare import is_num, norm
    from docparse.config import report_path
    from docparse.engine import open_pdf
    from docparse.tables import extract_tables

    checked = 0
    for g in load_gold(DEFAULT_GOLD_DIR):
        with open_pdf(report_path(g["report"])) as doc:
            page = doc[g["page"] - 1]
            words = {w[4].strip() for w in page.get_words()}
            for t in extract_tables(page, "auto"):
                for row in t.grid:
                    for cell in row:
                        if cell and is_num(norm(cell)) and " " not in cell:
                            # 낱말과 완전히 같거나, 낱말이 「값 + 각주 표시」인 경우만 허용한다
                            assert cell in words or any(
                                w.startswith(cell) and re.fullmatch(r"(\d{1,2}\))+|\*+", w[len(cell):]) for w in words
                            ), (g["id"], cell)
                            checked += 1
    assert checked > 1000
