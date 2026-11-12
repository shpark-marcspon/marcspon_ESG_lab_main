"""표 추출 전략의 조합: 괘선 기반 → 실패 시 글자 정렬 기반. 표마다 쓰인 전략을 기록한다."""
from .layout import page_chunks, page_rules
from .table_model import METHOD_LINES, METHOD_NONE, METHOD_TEXT, STATUS_FAILED, Table
from .tables_lines import find_lines_tables
from .tables_text import find_text_tables

STRATEGIES = ("auto", METHOD_LINES, METHOD_TEXT)


def _inside(c, box, pad=1.0):
    return box[0] - pad <= c.xc <= box[2] + pad and box[1] - pad <= c.yc <= box[3] + pad


def _has_value_rows(chunks):
    from .tables_text import cluster_rows
    rows = cluster_rows(chunks)
    return sum(1 for r in rows if len(r) >= 2 and any(c.value_like for c in r)) >= 2


def extract_tables(page, strategy="auto", chunks=None):
    """쪽 하나에서 표를 뽑는다. 위→아래, 왼쪽→오른쪽 순으로 돌려준다.

    strategy: auto(괘선 기반 → 남은 영역에 글자 정렬 기반) | lines | text
    격자를 세우지 못한 표 후보는 빈 표가 아니라 status=failed, method=none 인 항목으로 남긴다.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"알 수 없는 전략: {strategy}")
    if chunks is None:
        chunks, _ = page_chunks(page)
    tables, failed_boxes = [], []
    rest = list(chunks)
    if strategy in ("auto", METHOD_LINES):
        hs, vs = page_rules(page)
        tables, rejected = find_lines_tables(hs, vs, chunks)
        rest = [c for c in chunks if not any(_inside(c, t.bbox) for t in tables)]
        # 괘선은 있는데 격자가 서지 않았고 그 안에 값 줄이 있으면 실패한 표 후보다.
        # auto 에서는 글자 정렬 기반이 그 자리를 표로 세우지 못했을 때만 failed 로 남는다(아래 겹침 검사).
        failed_boxes = [b for b in rejected if _has_value_rows([c for c in rest if _inside(c, b)])]
    if strategy in ("auto", METHOD_TEXT):
        text_tables, text_failed = find_text_tables(rest)
        tables.extend(text_tables)
        failed_boxes.extend(text_failed)
    for b in failed_boxes:
        if any(_overlap(b, t.bbox) > 0.5 * _area(b) for t in tables):
            continue
        tables.append(Table(bbox=[round(v, 1) for v in b], grid=[], method=METHOD_NONE, status=STATUS_FAILED,
                            notes=["grid_not_built"], header={"rows": [], "cols": [], "basis": "empty"}))
    tables.sort(key=lambda t: (round(t.bbox[1] / 20), t.bbox[0]))
    return tables


def _area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _overlap(a, b):
    return _area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])
