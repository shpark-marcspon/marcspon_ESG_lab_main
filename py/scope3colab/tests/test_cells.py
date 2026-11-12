"""§3-1 코드 블록 뽑기 — 지어낸 작은 md(코드 블록 셋, `!pip` 줄 하나)."""
import pytest

from scope3colab.cells import CellFormatError, extract_cells

SYNTHETIC_MD = """\
# 가상 노트북 — 읽기판 꼴(synthetic)

## 1. 셀 전문

### 셀 0 · markdown

```python
print("markdown 셀 안의 블록은 코드가 아니다")
```

### 셀 1 · code

```python
# 가상 설치
!pip install -q 가상패키지
x = 1
```

### 셀 2 · code

````python
doc = '''
```
글 속의 짧은 펜스는 블록을 닫지 않는다
```
'''
y = x + 1
````

### 셀 3 · code

```python
if True:
    base_path = '/content/drive/MyDrive/가상폴더'
z = y + 1
```

## 2. 입력 칸

```python
print("셀 전문 밖의 블록")
```
"""


def test_blocks_in_order_and_skipped_lines():
    cells = extract_cells(SYNTHETIC_MD)
    assert [c.number for c in cells] == [1, 2, 3]
    skipped = [(s.cell, s.line, s.rule) for c in cells for s in c.skipped]
    assert skipped == [(1, 2, "shell-magic"), (3, 2, "drive-base-path")]
    # 건너뛴 줄은 같은 들여쓰기의 pass — if 몸통이 깨지지 않는다
    assert "    pass  # scope3colab: 건너뜀(drive-base-path)" in cells[2].source
    assert "글 속의 짧은 펜스" in cells[1].source


def test_blocks_run_in_one_namespace():
    ns: dict = {}
    for cell in extract_cells(SYNTHETIC_MD):
        exec(compile(cell.source, f"<cell {cell.number}>", "exec"), ns)
    assert (ns["x"], ns["y"], ns["z"]) == (1, 2, 3)
    assert "base_path" not in ns


def test_unclosed_fence_fails():
    with pytest.raises(CellFormatError, match="닫히지 않았다"):
        extract_cells("### 셀 1 · code\n\n```python\nx = 1\n")


def test_out_of_order_cells_fail():
    md = "### 셀 2 · code\n```python\na = 1\n```\n### 셀 1 · code\n```python\nb = 2\n```\n"
    with pytest.raises(CellFormatError, match="차례"):
        extract_cells(md)
