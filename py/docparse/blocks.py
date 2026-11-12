"""표 밖 글자 조각을 문단·제목 블록으로 묶는다."""
from statistics import median


def build_text_blocks(chunks):
    """조각을 문단으로 묶는다. [{"bbox", "text", "size", "chunks"}] (chunks = 글을 이룬 조각, 글의 차례대로)"""
    paras = []
    for c in sorted(chunks, key=lambda c: (c.y0, c.x0)):
        if not c.text:
            continue
        home = None
        for p in reversed(paras[-40:]):
            if abs(p["size"] - c.size) > 0.12 * c.size:
                continue
            x0, y0, x1, y1 = p["bbox"]
            same_line = c.y0 < p["last_y1"] - 0.5 * c.size and 0 <= c.x0 - p["last_x1"] <= 1.5 * c.size
            overlap = min(x1, c.x1) - max(x0, c.x0)
            next_line = (0 <= c.y0 - p["last_y1"] + 0.35 * c.size <= 1.0 * c.size
                         and overlap >= 0.5 * min(x1 - x0, c.x1 - c.x0) and abs(c.x0 - x0) <= 2.5 * c.size)
            if same_line or next_line:
                home = p
                break
        if home is None:
            paras.append({"bbox": list(c.bbox), "parts": [c.text], "chunks": [c], "size": c.size, "last_y1": c.y1, "last_x1": c.x1})
        else:
            b = home["bbox"]
            home["bbox"] = [min(b[0], c.x0), min(b[1], c.y0), max(b[2], c.x1), max(b[3], c.y1)]
            home["parts"].append(c.text)
            home["chunks"].append(c)
            home["last_y1"], home["last_x1"] = c.y1, c.x1
    return [{"bbox": [round(v, 1) for v in p["bbox"]], "text": " ".join(p["parts"]), "size": p["size"],
             "chunks": p["chunks"]} for p in paras]


def body_font_size(blocks):
    """글자 수로 가중한 본문 글자 크기(중앙값)."""
    sizes = [b["size"] for b in blocks for _ in range(max(1, len(b["text"]) // 10))]
    return median(sizes) if sizes else 0.0


def classify(block, body_size):
    """heading | paragraph. 본문보다 1.25배 이상 크고 120자 이하면 제목으로 본다."""
    if body_size and block["size"] >= 1.25 * body_size and len(block["text"]) <= 120:
        return "heading"
    return "paragraph"
