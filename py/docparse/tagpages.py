"""숫자 자리를 번호표로 덮은 쪽 그림을 만든다 (비전 모델 입력용).

번호표의 자리(bbox)와 번호는 호출하는 쪽이 준다. 여기서는 글자를 읽지 않는다.
원본 PDF 는 고치지 않는다: 메모리에서 연 쪽 위에 흰 사각형과 번호를 그려 PNG 로만 쓴다.
"""
from pathlib import Path

import pymupdf

TAG_FONT = 6.5      # 번호 글자 크기 (pt). 도표 속 값 글자와 같은 크기
TAG_PAD = 0.8       # 덮는 사각형을 글자 상자보다 이만큼 크게
TAG_MIN_WIDTH = 4.2  # 번호 한 글자당 최소 너비 (pt)


def tag_rect(bbox, n):
    """번호표의 사각형: 글자 상자를 다 덮고, 번호가 들어갈 만큼 넓다."""
    x0, y0, x1, y1 = bbox
    need = TAG_MIN_WIDTH * (len(str(n)) + 1)
    if x1 - x0 < need:
        mid = (x0 + x1) / 2
        x0, x1 = mid - need / 2, mid + need / 2
    return [x0 - TAG_PAD, y0 - TAG_PAD, x1 + TAG_PAD, y1 + TAG_PAD]


def tag_page(page, tags):
    """메모리에서 연 쪽 하나에서 번호표 자리의 글자를 지우고 그 위에 번호표를 그린다. tags: [{"n", "bbox"}]."""
    for t in tags:
        page.add_redact_annot(pymupdf.Rect(t["bbox"]))
    # 글자만 지운다 (그림 요소는 남긴다). 덮기만 하면 텍스트 계층에 값이 남으므로 지운 뒤 그린다.
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    for t in tags:
        r = pymupdf.Rect(tag_rect(t["bbox"], t["n"]))
        page.draw_rect(r, color=(0.8, 0, 0), fill=(1, 1, 1), width=0.4)
        label = str(t["n"])
        width = pymupdf.get_text_length(label, fontname="helv", fontsize=TAG_FONT)
        page.insert_text((r.x0 + (r.width - width) / 2, r.y1 - (r.height - TAG_FONT * 0.72) / 2), label, fontname="helv", fontsize=TAG_FONT, color=(0.8, 0, 0))


def render_tagged(pdf_path, tags, out_dir, dpi=110):
    """tags: [{"page", "n", "bbox"}]. 쪽마다 p0000.png 를 쓰고 경로 목록을 돌려준다."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_page = {}
    for t in tags:
        by_page.setdefault(t["page"], []).append(t)
    files = []
    with pymupdf.open(pdf_path) as doc:
        for page_no in sorted(by_page):
            page = doc[page_no - 1]
            tag_page(page, by_page[page_no])
            path = out_dir / f"p{page_no:04d}.png"
            page.get_pixmap(dpi=dpi).save(path)
            files.append(path)
    return files
