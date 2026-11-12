"""지은 한글 글 네 쪽짜리 표본 PDF 를 PyMuPDF 로 짓는다. `python greenwash/tests/build_sample.py <out 아래의 경로>`.

PDF 는 저장소에 넣지 않는다. 글꼴은 PyMuPDF 에 든 CJK 글꼴을 PDF 에 심어서 쓴다(그래야 `pypdf` 가 글자 그대로 읽는다).
"""
import sys
import textwrap
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from greenwash.tests.sample_text import PAGES  # noqa: E402

# 쪽마다 (글자 크기, 줄 간격, 한 줄의 글자 수, 줄을 이어 붙여 흘릴지). 쪽 4 는 글이 길어 작게, 이어 붙여 쓴다.
LAYOUT = [(10, 15, 40, False), (10, 15, 40, False), (10, 15, 40, False), (7, 9.5, 58, True)]


def build(path) -> None:
    doc = pymupdf.open()
    font = pymupdf.Font("cjk")
    for lines, (size, leading, width, flow) in zip(PAGES, LAYOUT):
        page = doc.new_page()
        writer = pymupdf.TextWriter(page.rect)
        y = 72.0
        for para in ([" ".join(lines)] if flow else lines):
            for row in textwrap.wrap(para, width=width, break_long_words=False) or [""]:
                writer.append((72, y), row, font=font, fontsize=size)
                y += leading
            y += leading * 0.6
        if y > page.rect.height - 40:
            raise ValueError(f"쪽을 넘쳤다: y={y}")
        writer.write_text(page)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("쓰는 법: build_sample.py <지을 PDF 경로(out/ 아래)>")
    build(sys.argv[1])
    print(f"표본 PDF(synthetic) → {sys.argv[1]}")
