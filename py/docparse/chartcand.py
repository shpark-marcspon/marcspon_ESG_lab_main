"""「이 쪽의 이 구역에 도표가 있을 것 같다」는 후보를 꼴을 보지 않고 가려낸다 (밴드 CT1, 새 산출).

`charts.py` 와 따로다. 저쪽은 막대·꺾은선·표식의 꼴을 맞춰 도표를 찾고 값에 라벨을 붙인다.
여기서는 꼴을 알아맞히지 않는다. 신호는 둘뿐이다:

- 그림 표식(mark): 벡터 그림 요소 가운데 괘선이 아닌 것. 괘선 = 가로·세로로 곧은 선과 두께 `RULE_THICK` 이하의 사각형
  (표의 줄, 도표의 눈금선·축). 글자를 담은 채운 도형은 표식이 아니라 바탕(칸 배경, 글 상자)으로 본다: 네모난 도형은
  낱말이 하나라도 들어 있으면, 그 밖의 도형은 든 낱말이 도형 상자 넓이의 `CONTAINER_TEXT_SHARE` 이상을 덮으면.
  여러 쪽의 같은 자리에 되풀이되는 표식 구역(쪽 머리의 아이콘 등)은 쪽 장식으로 보고 뺀다.
- 떠 있는 숫자(loose number): PDF 의 숫자 낱말 가운데, 문서 모델에서 「머리글이 선 표」의 칸도 「글 문단」도 아닌 자리에 놓인 것
  (머리글 없는 표·확신 낮은 표의 칸, 숫자가 절반 넘는 짧은 블록, 어느 그릇에도 들지 않은 낱말).

후보의 정의 셋:
- `marks`: 표식이 `MIN_MARKS` 개 이상 모인 구역.
- `numbers`: 떠 있는 숫자가 `MIN_LOOSE` 개 이상 모인 구역.
- `both`: `marks` 구역(여유 `BOTH_MARGIN`) 안에 떠 있는 숫자가 `BOTH_MIN_LOOSE` 개 이상 든 것.

OCR·LLM 을 쓰지 않는다. 숫자의 값은 읽지 않는다. 그림으로 박힌 도표(글자 층도 벡터 요소도 없는 것)는 닿지 않는다.
문턱값(pt)은 KB금융 2025 두 문서의 몇 쪽을 보고 정한 추정이다.
"""
from .engine import Rect
from .layout import MIN_VISIBLE_FONT_SIZE, is_numeric

SCHEMA_VERSION = "0.1"
RULE_THICK = 1.5          # 이보다 얇은 사각형과 곧은 가로·세로 선은 괘선이다
MARK_MAX_AREA_RATIO = 0.25  # 쪽 넓이의 이 비율을 넘는 요소는 쪽 배경·판이다
CONTAINER_TEXT_SHARE = 0.1  # 네모가 아닌 채운 도형: 든 낱말이 상자 넓이의 이만큼을 덮으면 글 상자다
MARK_GAP = 40.0           # 표식끼리 이보다 가까우면 한 구역
FURNITURE_PAGE_SHARE = 0.2  # 같은 자리의 표식 구역이 쪽의 이 비율 이상(그리고 3쪽 이상)에 되풀이되면 쪽 장식이다
MIN_MARKS = 3
LOOSE_GAP = 40.0          # 떠 있는 숫자끼리 이보다 가까우면 한 구역
MIN_LOOSE = 3
BOTH_MARGIN = 20.0
BOTH_MIN_LOOSE = 2
PROSE_NUMERIC_SHARE = 0.5  # 블록의 낱말 가운데 숫자가 이 비율 미만이면 글 문단이다

DEFINITIONS = ("marks", "numbers", "both")


def _is_rule_item(it):
    if it[0] == "l":
        p, q = it[1], it[2]
        return abs(p.y - q.y) <= 0.6 or abs(p.x - q.x) <= 0.6
    if it[0] in ("re", "qu"):
        r = Rect(it[1])
        r.normalize()
        return min(r.width, r.height) <= RULE_THICK
    return False


def _inside(box, x, y, pad=0.0):
    return box[0] - pad <= x <= box[2] + pad and box[1] - pad <= y <= box[3] + pad


def page_words(page):
    """[(x0, y0, x1, y1, 글)] 보이는 가로 낱말."""
    out = []
    for w in page.get_words():
        if w[4].strip() and w[3] - w[1] >= MIN_VISIBLE_FONT_SIZE:
            out.append((w[0], w[1], w[2], w[3], w[4]))
    return out


def page_marks(page, words):
    """괘선도 바탕도 아닌 벡터 그림 요소의 상자 목록."""
    pw, ph = page.rect.width, page.rect.height
    marks = []
    for d in page.get_drawings():
        stroke, fill = d.get("color") is not None, d.get("fill") is not None
        if not (stroke or fill) or not d["items"]:
            continue
        if fill and not stroke and d.get("fill_opacity", 1) == 0:
            continue
        r = Rect(d["rect"])
        r.normalize()
        box = (max(0.0, r.x0), max(0.0, r.y0), min(pw, r.x1), min(ph, r.y1))
        if box[2] <= box[0] and box[3] <= box[1]:
            continue
        if (box[2] - box[0]) * (box[3] - box[1]) > MARK_MAX_AREA_RATIO * pw * ph:
            continue
        if all(_is_rule_item(it) for it in d["items"]):
            continue
        if fill and min(box[2] - box[0], box[3] - box[1]) > RULE_THICK:
            held = [w for w in words if _inside(box, (w[0] + w[2]) / 2, (w[1] + w[3]) / 2)]
            boxy = all(it[0] in ("re", "qu") or _is_rule_item(it) for it in d["items"])
            covered = sum((w[2] - w[0]) * (w[3] - w[1]) for w in held)
            if held and (boxy or covered >= CONTAINER_TEXT_SHARE * (box[2] - box[0]) * (box[3] - box[1])):
                continue  # 글자를 담은 채운 도형: 칸 배경이나 글 상자
        marks.append(box)
    return marks


def loose_numbers(words, page_no, model):
    """숫자 낱말 가운데 머리글이 선 표의 칸도 글 문단도 아닌 자리에 놓인 것의 상자 목록."""
    headed, other_tables, prose, numeric_blocks = [], [], [], []
    for t in model["tables"]:
        if t["page"] != page_no:
            continue
        (headed if t["status"] == "ok" and t["header"]["rows"] else other_tables).append(t["bbox"])
    for b in model["blocks"]:
        if b["page"] != page_no or b["kind"] == "table":
            continue
        toks = b["text"].split()
        share = sum(1 for t in toks if is_numeric(t)) / len(toks) if toks else 0.0
        (prose if share < PROSE_NUMERIC_SHARE else numeric_blocks).append(b["bbox"])
    out = []
    for w in words:
        if not is_numeric(w[4]):
            continue
        x, y = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        if any(_inside(b, x, y, 1.0) for b in other_tables) or any(_inside(b, x, y, 1.0) for b in numeric_blocks):
            out.append(w[:4])
        elif any(_inside(b, x, y, 1.0) for b in headed) or any(_inside(b, x, y, 1.0) for b in prose):
            continue
        else:
            out.append(w[:4])
    return out


def cluster(boxes, gap):
    """상자끼리의 틈이 gap 이하면 한 묶음. [[상자 번호]]"""
    parent = list(range(len(boxes)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    # x0 차례로 훑으며, 아직 닿을 수 있는(오른쪽 끝이 지금 상자의 왼쪽 끝 - gap 이상인) 상자하고만 견준다.
    active = []
    for b in sorted(range(len(boxes)), key=lambda i: boxes[i][0]):
        active = [a for a in active if boxes[b][0] - boxes[a][2] <= gap]
        for a in active:
            if max(boxes[a][1], boxes[b][1]) - min(boxes[a][3], boxes[b][3]) <= gap:
                parent[find(a)] = find(b)
        active.append(b)
    groups = {}
    for i in range(len(boxes)):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _union(boxes):
    return [round(min(b[0] for b in boxes), 1), round(min(b[1] for b in boxes), 1),
            round(max(b[2] for b in boxes), 1), round(max(b[3] for b in boxes), 1)]


def mark_regions_of(marks):
    """표식이 모인 구역. [[x0, y0, x1, y1, 표식 수]]"""
    return [_union([marks[i] for i in g]) + [len(g)] for g in cluster(marks, MARK_GAP) if len(g) >= MIN_MARKS]


def furniture_keys(regions_by_page):
    """여러 쪽의 같은 자리에 되풀이되는 표식 구역의 열쇠(1pt 로 반올림한 상자)."""
    seen = {}
    for regions in regions_by_page:
        for key in {tuple(round(v) for v in r[:4]) for r in regions}:
            seen[key] = seen.get(key, 0) + 1
    need = max(3, FURNITURE_PAGE_SHARE * len(regions_by_page))
    return {k for k, n in seen.items() if n >= need}


def page_candidates(page, page_no, model, furniture=frozenset(), parts=None):
    """그 쪽의 후보 구역. [{"definition", "bbox", "marks", "loose_numbers"}]

    parts = (낱말, 표식) 을 이미 읽었으면 넘긴다. furniture = `furniture_keys` 가 낸, 뺄 표식 구역의 열쇠.
    """
    words, marks = parts if parts is not None else (None, None)
    if words is None:
        words = page_words(page)
        marks = page_marks(page, words)
    loose = loose_numbers(words, page_no, model)
    out = []
    mark_regions = [r for r in mark_regions_of(marks) if tuple(round(v) for v in r[:4]) not in furniture]
    for *box, n in mark_regions:
        inside = [w for w in loose if _inside(box, (w[0] + w[2]) / 2, (w[1] + w[3]) / 2, BOTH_MARGIN)]
        out.append({"definition": "marks", "bbox": box, "marks": n, "loose_numbers": len(inside)})
        if len(inside) >= BOTH_MIN_LOOSE:
            out.append({"definition": "both", "bbox": _union([box] + inside), "marks": n, "loose_numbers": len(inside)})
    for g in cluster(loose, LOOSE_GAP):
        if len(g) >= MIN_LOOSE:
            box = _union([loose[i] for i in g])
            n = sum(1 for m in marks if _inside(box, (m[0] + m[2]) / 2, (m[1] + m[3]) / 2))
            out.append({"definition": "numbers", "bbox": box, "marks": n, "loose_numbers": len(g)})
    order = {d: i for i, d in enumerate(DEFINITIONS)}
    out.sort(key=lambda c: (order[c["definition"]], c["bbox"][1], c["bbox"][0]))
    return out


def candidates_document(pdf, model):
    """문서 전체의 후보. 형식은 `schemas/chart-candidates.schema.json`."""
    cands, pages = [], []
    parts = {}
    for meta in model["pages"]:
        words = page_words(pdf[meta["page"] - 1])
        parts[meta["page"]] = (words, page_marks(pdf[meta["page"] - 1], words))
    furniture = furniture_keys([mark_regions_of(m) for _w, m in parts.values()])
    for meta in model["pages"]:
        pno = meta["page"]
        found = page_candidates(pdf[pno - 1], pno, model, furniture, parts[pno])
        for c in found:
            n = sum(1 for x in cands if x["page"] == pno and x["definition"] == c["definition"]) + 1
            cands.append({"id": f"p{pno}-{c['definition']}{n}", "page": pno, **c})
        pages.append({"page": pno, **{d: sum(1 for c in found if c["definition"] == d) for d in DEFINITIONS}})
    return {
        "schema_version": SCHEMA_VERSION,
        "source": model["source"],
        "method": {"name": "docparse.chartcand", "llm_used": False, "ocr_used": False,
                   "params": {"rule_thick": RULE_THICK, "mark_max_area_ratio": MARK_MAX_AREA_RATIO, "mark_gap": MARK_GAP,
                              "min_marks": MIN_MARKS, "container_text_share": CONTAINER_TEXT_SHARE,
                              "furniture_page_share": FURNITURE_PAGE_SHARE, "loose_gap": LOOSE_GAP, "min_loose": MIN_LOOSE,
                              "both_margin": BOTH_MARGIN, "both_min_loose": BOTH_MIN_LOOSE,
                              "prose_numeric_share": PROSE_NUMERIC_SHARE}},
        "pages": pages,
        "candidates": cands,
    }
