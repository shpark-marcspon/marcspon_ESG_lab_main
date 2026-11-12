"""번호표의 대상을 꼴을 보지 않고 고르고, 쪽의 글을 값 없이 좌표와 함께 낸다 (밴드 VT1, 새 산출).

`charts.py`(막대·꺾은선의 꼴을 맞춰 도표를 찾는다)에 기대지 않는다. 쓰는 것은 셋뿐이다:
PDF 의 낱말과 좌표, 문서 모델(표의 머리글이 섰는가, 글 블록의 크기), 도표 후보 구역(`chartcand.py` 의 `marks`).

1. 숫자 낱말의 목록 (`number_words`): 쪽의 숫자 낱말 전부에 차례로 열쇠(`p<쪽>-w<순번>`)를 주고, 놓인 자리의 갈래를 적는다.
   번호표의 기준과 무관한 목록이다 (정답을 다는 쪽도 이 열쇠를 쓴다).
2. 번호표의 대상 (`select_targets`): 그 목록에서
   - 표식 구역(`marks`)이 하나라도 있는 쪽의 낱말이고,
   - 연도 꼴(`layout.is_year`)이 아니고,
   - 머리글이 선 표의 칸에 놓이지 않았고,
   - 문단(낱말이 `PARAGRAPH_MIN_WORDS` 개 이상이고 숫자 낱말이 절반 미만인 글 블록)에 놓이지 않은 것.
   도표인지, 어떤 도표인지는 보지 않는다. 그래서 도표가 아닌 숫자(큰 글씨 요약 숫자, 머리글 없는 표의 칸)에도 번호표가 붙는다.
   그것을 가르는 일은 번호표를 본 쪽(모델)의 몫이다.
3. 쪽의 글 (`page_lines`): PDF 의 낱말을 줄 토막으로 묶어 자리(쪽 너비·높이를 1000 으로 본 좌표)와 함께 낸다.
   번호표가 덮은 낱말은 `〔번호〕` 로, 그 밖의 숫자는 연도 꼴이 아니면 `▒` 로 바꾼다. 값의 글자는 어디에도 싣지 않는다.

OCR·LLM 을 쓰지 않는다. 문턱값은 추정이다 (KB금융 2025 두 문서만 보고 정했다).
"""
import re
import unicodedata

from .chartcand import PROSE_NUMERIC_SHARE, candidates_document
from .layout import MIN_VISIBLE_FONT_SIZE, is_numeric, is_year
from .places import link_page

PARAGRAPH_MIN_WORDS = 8   # 글 블록의 낱말이 이 수 이상이고 숫자가 절반 미만이면 문단이다 (그보다 짧으면 도표의 이름표일 수 있다)
TAG_OVERLAP = 0.5         # 낱말 상자의 이 비율 이상이 번호표 상자와 겹치면 그 낱말은 번호표가 덮은 것이다
SEGMENT_GAP_EM = 0.8      # 한 줄에서 낱말 사이의 틈이 줄 높이의 이 배수를 넘으면 토막을 가른다 (축 글자는 따로따로 자리를 갖게)
GRID = 1000               # 좌표의 눈금: 쪽의 왼쪽 위 (0, 0), 오른쪽 아래 (GRID, GRID)
ROW_TOLERANCE = 4         # 세로 자리가 이 눈금 안이면 같은 줄로 보고 왼쪽부터 늘어놓는다
MASK = "▒"
_DIGITS = re.compile(r"\d[\d,.]*\d|\d")

PLACE_KINDS = ("headed_table", "loose_table", "paragraph", "label_block", "none")


def _center(box):
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def _inside(box, x, y, pad=0.0):
    return box[0] - pad <= x <= box[2] + pad and box[1] - pad <= y <= box[3] + pad


def _same_box(a, b):
    return all(abs(p - q) <= 0.6 for p, q in zip(a, b))


def number_words(page, page_no, model):
    """쪽의 숫자 낱말 전부. [{"key", "page", "bbox", "text", "links", "place", "year"}]

    links: 문서 모델에서 그 낱말이 놓인 그릇과 글자 자리 [{"container", "start", "end"}] (값 후보의 ID 로 가는 길).
    겹친 표가 같은 낱말을 두 그릇에 실었으면 둘 다 적는다. 문서 모델에 이어지지 않은 낱말은 빈 목록이다.
    place: 놓인 자리의 갈래 (`PLACE_KINDS`). 그릇이 여럿이면 문서 순서의 첫째로 가른다.
    """
    linked, _ = link_page(page, page_no, model["blocks"], model["tables"])
    tables = {t["id"]: t for t in model["tables"] if t["page"] == page_no}
    blocks = {b["id"]: b for b in model["blocks"] if b["page"] == page_no and b["kind"] != "table"}

    def place_of(container):
        if container is None:
            return "none"
        if ":" in container:
            t = tables[container.split(":")[0]]
            return "headed_table" if t["status"] == "ok" and t["header"]["rows"] else "loose_table"
        words = blocks[container]["text"].split()
        share = sum(1 for w in words if is_numeric(w)) / len(words) if words else 0.0
        return "paragraph" if len(words) >= PARAGRAPH_MIN_WORDS and share < PROSE_NUMERIC_SHARE else "label_block"

    out = []
    raw = sorted((w for w in page.get_words() if w[4].strip() and w[3] - w[1] >= MIN_VISIBLE_FONT_SIZE and is_numeric(w[4])),
                 key=lambda w: (round(w[1], 1), round(w[0], 1)))
    for n, w in enumerate(raw, 1):
        box = [round(v, 1) for v in w[:4]]
        hits = [p for p in linked if p["text"] == w[4] and _same_box(p["bbox"], box)]
        out.append({"key": f"p{page_no}-w{n}", "page": page_no, "bbox": box, "text": w[4],
                    "links": [{"container": p["container"], "start": p["start"], "end": p["end"]} for p in hits],
                    "place": place_of(hits[0]["container"] if hits else None), "year": is_year(w[4])})
    return out


def select_targets(words, mark_pages):
    """번호표의 대상. words: `number_words` 의 낱말들, mark_pages: 표식 구역이 있는 쪽 번호의 모음.

    돌려주는 번호표 [{"page", "n", "bbox", "id", "links"}] 에는 낱말의 글자를 싣지 않는다. 번호는 쪽마다 1 부터.
    """
    tags, next_n = [], {}
    for w in words:
        if w["page"] not in mark_pages or w["year"] or w["place"] in ("headed_table", "paragraph"):
            continue
        n = next_n.get(w["page"], 0) + 1
        next_n[w["page"]] = n
        tags.append({"page": w["page"], "n": n, "bbox": w["bbox"], "id": w["key"], "links": w["links"]})
    return tags


def mask_word(text):
    """번호표가 덮지 않은 낱말에서 숫자를 가린다. 연도 꼴은 이름이라 그대로 둔다."""
    if is_year(text):
        return text
    return _DIGITS.sub(MASK, unicodedata.normalize("NFKC", text))


def _covering_tag(box, tags):
    area = max(1e-9, (box[2] - box[0]) * (box[3] - box[1]))
    for t in tags:
        b = t["bbox"]
        w = min(box[2], b[2]) - max(box[0], b[0])
        h = min(box[3], b[3]) - max(box[1], b[1])
        if w > 0 and h > 0 and w * h / area >= TAG_OVERLAP:
            return t
    return None


def page_lines(page, tags, mask=True):
    """쪽의 글을 줄 토막으로. [{"x0", "x1", "y", "text"}] 좌표는 0..GRID.

    tags: 그 쪽의 번호표 [{"n", "bbox"}]. 번호표가 덮은 낱말은 `〔n〕` 하나로 바뀐다 (같은 번호표 아래 낱말이 여럿이어도 한 번).
    mask=False 는 테스트가 「가림을 끄면 값이 샌다」를 보일 때만 쓴다.
    """
    pw, ph = page.rect.width, page.rect.height
    lines = {}
    for w in page.get_words():
        if not w[4].strip() or w[3] - w[1] < MIN_VISIBLE_FONT_SIZE:
            continue
        lines.setdefault((w[5], w[6]), []).append(w)
    segments = []
    for members in lines.values():
        members.sort(key=lambda w: w[0])
        current = []
        for w in members:
            if current and w[0] - current[-1][2] > SEGMENT_GAP_EM * (w[3] - w[1]):
                segments.append(current)
                current = []
            current.append(w)
        segments.append(current)
    out = []
    for seg in segments:
        parts, done = [], set()
        for w in seg:
            tag = _covering_tag(w[:4], tags)
            if tag is not None:
                if tag["n"] not in done:
                    done.add(tag["n"])
                    parts.append(f"〔{tag['n']}〕")
            else:
                parts.append(mask_word(w[4]) if mask else w[4])
        if not parts:
            continue
        y = (min(w[1] for w in seg) + max(w[3] for w in seg)) / 2
        out.append({"x0": round(seg[0][0] / pw * GRID), "x1": round(seg[-1][2] / pw * GRID), "y": round(y / ph * GRID), "text": " ".join(parts)})
    out.sort(key=lambda s: (s["y"], s["x0"]))
    rows, ordered = [], []
    for s in out:
        if rows and s["y"] - rows[-1][0]["y"] <= ROW_TOLERANCE:
            rows[-1].append(s)
        else:
            rows.append([s])
    for row in rows:
        ordered.extend(sorted(row, key=lambda s: s["x0"]))
    return ordered


def visible_numbers(page, tags):
    """번호표가 덮지 않아 쪽 그림에 그대로 보이는 숫자 낱말의 수: {"year": 연도 꼴, "other": 그 밖}."""
    counts = {"year": 0, "other": 0}
    for w in page.get_words():
        if not w[4].strip() or w[3] - w[1] < MIN_VISIBLE_FONT_SIZE or not is_numeric(w[4]):
            continue
        if _covering_tag(w[:4], tags) is None:
            counts["year" if is_year(w[4]) else "other"] += 1
    return counts


def mark_pages_of(pdf, model):
    """표식 구역(`marks`)이 하나라도 있는 쪽 번호."""
    return {p["page"] for p in candidates_document(pdf, model)["pages"] if p["marks"]}


def targets_document(pdf, model):
    """문서 하나의 (숫자 낱말 목록, 번호표 목록, 표식 구역이 있는 쪽)."""
    marks = mark_pages_of(pdf, model)
    words = []
    for meta in model["pages"]:
        words.extend(number_words(pdf[meta["page"] - 1], meta["page"], model))
    return words, select_targets(words, marks), sorted(marks)


def lines_document(pdf, tags, pages=None):
    """번호표가 있는 쪽(또는 pages 로 준 쪽)마다 줄 토막과, 번호표 없이 보이는 숫자의 수."""
    by_page = {}
    for t in tags:
        by_page.setdefault(t["page"], []).append(t)
    out = {}
    for page_no in sorted(pages if pages is not None else by_page):
        page = pdf[page_no - 1]
        mine = by_page.get(page_no, [])
        out[str(page_no)] = {"lines": page_lines(page, mine), "visible_numbers": visible_numbers(page, mine), "tags": len(mine)}
    return out
