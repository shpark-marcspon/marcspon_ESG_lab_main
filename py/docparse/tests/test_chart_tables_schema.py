"""도표를 표로 세운 파생 문서(ts/reconcile 의 chart-tables, 밴드 CT1)가 문서 모델 스키마를 그대로 통과하는가.

본보기 파일은 ts/reconcile 의 테스트가 만든 것과 글자까지 같다 (그쪽 테스트가 지킨다). synthetic.
"""
import json

from docparse.config import REPO_ROOT
from docparse.document import validate

FIXTURES = REPO_ROOT / "ts" / "reconcile" / "test" / "fixtures"


def test_derived_document_with_chart_tables_passes_the_same_schema():
    source = json.loads((FIXTURES / "chart-doc.synthetic.json").read_text(encoding="utf-8"))
    derived = json.loads((FIXTURES / "chart-doc.derived.synthetic.json").read_text(encoding="utf-8"))["document"]
    assert validate(source) == [] and validate(derived) == []
    assert len(derived["tables"]) == len(source["tables"]) + 3
    new = derived["tables"][len(source["tables"]):]
    assert all(t["method"] == "text" and t["status"] == "ok" and t["notes"][0].startswith("chart_table:") for t in new)
    assert all(t["n_rows"] == len(t["cells"]) and t["n_cols"] == len(t["cells"][0]) for t in new)
    # 새 표마다 표 블록이 하나씩 있다
    assert sorted(b["table_id"] for b in derived["blocks"] if b["kind"] == "table") == sorted(t["id"] for t in derived["tables"])
